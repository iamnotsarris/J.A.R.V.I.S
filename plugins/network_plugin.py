"""
network_info.py — estado da rede e IPs deste computador.

Renomear de network_plugin.py para network_info.py — o sufixo "_plugin"
é redundante dentro da pasta plugins/ (o loader não exige) e o padrão
do projeto é snake_case curto: appidentity, undo, confirm, etc.

Correções sobre o original:
  + PLUGIN dict (sem ele o loader rejeita)
  + run(parameters) em vez de execute() (o loader chama run())
  + retorno via core.outcome (string, não dict)
  + player= pra logar no activity feed
  + tier="core" + brief pra sobreviver a budget apertado
  + múltiplos endpoints de conectividade (não só google.com)
  + múltiplos endpoints de IP público com fallback
  + get_local_ip() reporta erro em vez de 127.0.0.1 silencioso
  + checks em paralelo (pior caso 5s, não 10s)
"""

from __future__ import annotations

import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

from core.outcome import done, failed, unverified

PLUGIN = {
    "name": "network_info",
    "description": (
        "Reporta o estado da rede deste computador: se há internet, o IP "
        "local (LAN) e o IP público. Use quando o usuário perguntar 'tem "
        "internet?', 'qual meu IP?', 'qual meu IP público?', 'estou "
        "conectado?', 'por que a internet caiu?'. NÃO use para "
        "diagnóstico de velocidade (é outra ferramenta) nem para "
        "configurar Wi-Fi (use computer_settings)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "check_connectivity": {
                "type": "BOOLEAN",
                "description": "Verificar se há internet (padrão true).",
            },
            "include_public_ip": {
                "type": "BOOLEAN",
                "description": "Incluir IP público (padrão true).",
            },
            "include_local_ip": {
                "type": "BOOLEAN",
                "description": "Incluir IP local (padrão true).",
            },
        },
        "required": [],
    },
    "behavior": "BLOCKING",
    "tier": "core",
    "brief": "Rede e IPs.",
}


# ── Endpoints neutros ──────────────────────────────────────────────────────
# google.com é bloqueado em CN/IR e devolve falso-negativo com internet OK.
# Vários endpoints, cada um tentado em sequência até um responder.
_CONNECTIVITY_URLS = (
    "https://www.google.com",
    "https://www.cloudflare.com",
    "https://1.1.1.1",
)

_PUBLIC_IP_URLS = (
    "https://api.ipify.org?format=json",
    "https://ipapi.co/ip/",
    "https://ifconfig.me/ip",
)


def _check_internet(timeout: int = 5) -> bool:
    """True se QUALQUER endpoint responder 2xx/3xx. `timeout` é o budget
    TOTAL entre os endpoints, não por tentativa — senão 3 URLs * 5s = 15s."""
    try:
        import requests
    except ImportError:
        return False
    deadline = time.monotonic() + timeout
    for url in _CONNECTIVITY_URLS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            r = requests.head(url, timeout=remaining, allow_redirects=True)
            if 200 <= r.status_code < 400:
                return True
        except Exception:
            continue
    return False


def _get_local_ip() -> tuple[bool, str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP — connect() só roteia, não envia pacote.
        s.connect(("8.8.8.8", 1))
        return True, s.getsockname()[0]
    except OSError as e:
        return False, f"sem rota de saída: {e}"
    finally:
        s.close()


def _get_public_ip(timeout: int = 5) -> tuple[bool, str]:
    try:
        import requests
    except ImportError:
        return False, "requests não instalado"
    deadline = time.monotonic() + timeout
    for url in _PUBLIC_IP_URLS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            r = requests.get(url, timeout=remaining)
            r.raise_for_status()
            text = r.text.strip()
            if text.startswith("{"):
                ip = json.loads(text).get("ip", "")
            else:
                ip = text
            if ip:
                return True, ip
        except Exception:
            continue
    return False, "todos os serviços de IP falharam"


def run(parameters: dict, player=None, session_memory=None) -> str:
    want_conn = parameters.get("check_connectivity", True)
    want_public = parameters.get("include_public_ip", True)
    want_local = parameters.get("include_local_ip", True)

    out: dict = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_conn = pool.submit(_check_internet) if want_conn else None
        f_local = pool.submit(_get_local_ip) if want_local else None
        f_public = pool.submit(_get_public_ip) if want_public else None

        if f_conn:
            try:
                connected = bool(f_conn.result(timeout=10))
                out["connected"] = connected
                if not connected:
                    errors.append("sem conectividade")
            except Exception as e:
                errors.append(f"conectividade: {type(e).__name__}: {e}")

        if f_local:
            try:
                ok, detail = f_local.result(timeout=10)
                if ok:
                    out["local_ip"] = detail
                else:
                    errors.append(f"IP local: {detail}")
            except Exception as e:
                errors.append(f"IP local: {type(e).__name__}: {e}")

        if f_public:
            try:
                ok, detail = f_public.result(timeout=10)
                if ok:
                    out["public_ip"] = detail
                else:
                    errors.append(f"IP público: {detail}")
            except Exception as e:
                errors.append(f"IP público: {type(e).__name__}: {e}")

    if player:
        try:
            status = "online" if out.get("connected") else "offline"
            player.write_log(
                f"JARVIS: rede — {status}, "
                f"local={out.get('local_ip', '?')}, "
                f"público={out.get('public_ip', '?')}"
            )
        except Exception:
            pass

    if out and not errors:
        return done("network_info", **out)

    if out:
        return unverified(
            "network_info",
            f"parcial: {'; '.join(errors)}",
            **out,
        )

    return failed("network_info", "; ".join(errors) or "nada foi medido")

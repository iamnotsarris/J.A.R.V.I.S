"""
pc_location.py — onde ESTE computador está.

Diferente de localização de celular: um desktop não se move, então "tempo
real" só significa algo em laptop. As três fontes, em ordem de prioridade:

  MANUAL    config/pc_location.json, que você edita à mão. Melhor para
            desktop parado — preciso, confiável, offline, sem permissão.
            Você diz "estou na Rua X, número Y", ele guarda.

  WINDOWS   Windows.Devices.Geolocation via winrt. Precisa de "Serviços
            de Localização" ativos em Configurações → Privacidade →
            Localização. Usa triangulação de Wi-Fi — precisão típica de
            20-100 m em área urbana, pior no campo. É o mesmo mecanismo
            que o Maps do Windows usa, então se o Maps te localiza, isto
            também localiza.

  IP        ipapi.co. ~25 km. Funciona em qualquer SO, sem permissão,
            mas mente se você usa VPN (diz o país do servidor) e erra
            na cidade quando o provedor usa NAT grande.

MACOS E LINUX NÃO TÊM NATIVO AQUI
    CoreLocation no macOS exige um app bundle assinado com
    NSLocationUsageDescription — impossível de um script Python sem
    empacotar como .app, o que sai completamente do escopo de um plugin.
    GeoClue2 no Linux exige o daemon rodando e configurado para permitir
    apps fora de sandbox; em muitas distros o padrão é negar.
    Em ambos: manual ou IP, e o plugin reporta qual foi usado.

O RESULTADO SEMPRE DIZ A FONTE E A PRECISÃO
    O modelo lê source="ip" accuracy_km=25 e diz "aproximadamente em São
    Paulo". Lê source="manual" e diz "na Rua Augusta, 1500". O mesmo
    fato, frases diferentes — e o usuário não é enganado por uma
    precisão que o dado não tem.
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.outcome import done, failed, started

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

_CONFIG = _BASE / "config" / "pc_location.json"

_FRESH = {"manual": 86_400, "windows": 30, "ip": 300}  # segundos
_POLL = {"manual": 3_600, "windows": 15, "ip": 300}

_WATCH_DEFAULT_MIN = 30
_WATCH_MAX_MIN = 240
_WATCH_DEFAULT_THRESHOLD_M = 200
_WATCH_COOLDOWN_MIN = 3


PLUGIN = {
    "name": "pc_location",
    "description": (
        "Retorna a localização DESTE computador: cidade, coordenadas, "
        "precisão e fonte (manual, windows ou ip). Use quando o usuário "
        "perguntar 'onde eu estou?', 'em que cidade estou?', 'que horas "
        "são aqui?', 'que tempo faz aqui?', 'quanto falta pra chegar em "
        "X?' (com near_lat/near_lon). action='watch' acompanha mudanças "
        "(útil em laptop, inútil em desktop). action='set_manual' fixa "
        "a localização no config. NÃO use para localização de celular "
        "nem para rastrear outra pessoa."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'current', 'watch', 'stop', 'set_manual' ou 'clear_manual'.",
            },
            "near_lat": {
                "type": "NUMBER",
                "description": "Latitude de referência (opcional).",
            },
            "near_lon": {
                "type": "NUMBER",
                "description": "Longitude de referência (opcional).",
            },
            "near_name": {
                "type": "STRING",
                "description": "Nome do ponto de referência (opcional).",
            },
            "manual_lat": {
                "type": "NUMBER",
                "description": "Latitude (para set_manual).",
            },
            "manual_lon": {
                "type": "NUMBER",
                "description": "Longitude (para set_manual).",
            },
            "manual_name": {
                "type": "STRING",
                "description": "Rótulo do local (para set_manual).",
            },
            "duration_minutes": {
                "type": "INTEGER",
                "description": f"Duração do watch em min (padrão {_WATCH_DEFAULT_MIN}, máx {_WATCH_MAX_MIN}).",
            },
            "move_threshold_meters": {
                "type": "INTEGER",
                "description": f"Distância mínima para avisar (padrão {_WATCH_DEFAULT_THRESHOLD_M}).",
            },
        },
        "required": ["action"],
    },
    "behavior": "BLOCKING",
    "tier": "extra",
    "brief": "Localização do PC.",
}


# ─── Cache em memória ────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_last_known: Optional[dict] = None


def _store(loc: dict) -> None:
    global _last_known
    with _cache_lock:
        _last_known = dict(loc)


def _cached(fresh_s: float) -> Optional[dict]:
    with _cache_lock:
        if _last_known is None:
            return None
        if time.time() - _last_known.get("at", 0) > fresh_s:
            return None
        return dict(_last_known)


# ─── Config manual ───────────────────────────────────────────────────────────


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_config(data: dict) -> None:
    _CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ─── Fontes ──────────────────────────────────────────────────────────────────


def _source_manual() -> tuple[bool, "dict | str"]:
    cfg = _load_config()
    if not cfg.get("manual"):
        return False, "manual não configurado"
    try:
        lat = float(cfg["lat"])
        lon = float(cfg["lon"])
    except (KeyError, TypeError, ValueError):
        return False, "config manual sem lat/lon válidos"
    return True, {
        "lat": lat,
        "lon": lon,
        "city": str(cfg.get("city") or "")[:80],
        "region": str(cfg.get("region") or "")[:80],
        "country": str(cfg.get("country") or "")[:80],
        "timezone": str(cfg.get("timezone") or "")[:64],
        "name": str(cfg.get("name") or "")[:80],
        "accuracy_km": 0.0,
        "source": "manual",
        "at": time.time(),
    }


def _source_windows() -> tuple[bool, "dict | str"]:
    try:
        from winrt.windows.devices.geolocation import Geolocator
    except ImportError:
        return False, (
            "winrt não instalado — pip install winrt-runtime "
            "winrt-Windows.Devices.Geolocation winrt-Windows.Foundation"
        )
    except Exception as e:
        return False, f"winrt indisponível: {type(e).__name__}: {e}"

    async def _get():
        locator = Geolocator()
        # request_access_async devolve ALLOWED / DENIED / UNSPECIFIED
        try:
            access = await locator.request_access_async()
        except Exception as e:
            raise RuntimeError(f"request_access falhou: {e}")
        if "ALLOWED" not in str(access).upper():
            raise PermissionError(
                "Serviços de Localização bloqueados. Habilite em "
                "Configurações → Privacidade e segurança → Localização."
            )
        pos = await locator.get_geoposition_async()
        c = pos.coordinate
        p = c.point.position
        return {
            "lat": p.latitude,
            "lon": p.longitude,
            "accuracy_m": c.accuracy,
        }

    try:
        # asyncio.run() falha se já houver loop rodando na thread corrente.
        # Plugins rodam em executor threads (sem loop), então é seguro.
        result = asyncio.run(_get())
    except PermissionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"WinRT: {type(e).__name__}: {e}"

    return True, {
        "lat": result["lat"],
        "lon": result["lon"],
        "city": "",
        "region": "",
        "country": "",
        "timezone": "",
        "name": "",
        "accuracy_km": float(result["accuracy_m"]) / 1000.0,
        "source": "windows",
        "at": time.time(),
    }


def _source_ip() -> tuple[bool, "dict | str"]:
    try:
        import requests

        r = requests.get(
            "https://ipapi.co/json/",
            timeout=8,
            headers={"User-Agent": "JARVIS-pc_location/1.0"},
        )
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return False, f"consulta de IP falhou: {type(e).__name__}: {e}"

    try:
        return True, {
            "lat": float(d["latitude"]),
            "lon": float(d["longitude"]),
            "city": str(d.get("city") or "")[:80],
            "region": str(d.get("region") or "")[:80],
            "country": str(d.get("country_name") or d.get("country") or "")[:80],
            "timezone": str(d.get("timezone") or "")[:64],
            "name": "",
            "accuracy_km": 25.0,
            "source": "ip",
            "at": time.time(),
        }
    except (KeyError, TypeError, ValueError):
        return False, "resposta do serviço de IP em formato inesperado"


def _fetch() -> tuple[bool, "dict | str"]:
    """Melhor fonte disponível: manual > windows > ip."""
    errors: list[str] = []
    for fn in (_source_manual, _source_windows, _source_ip):
        ok, data = fn()
        if ok:
            _store(data)
            return True, data
        errors.append(data)
    return False, "; ".join(errors)


# ─── Util ───────────────────────────────────────────────────────────────────


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _fmt(loc: dict) -> dict:
    out = {
        "lat": round(loc["lat"], 5),
        "lon": round(loc["lon"], 5),
        "city": loc.get("city") or None,
        "region": loc.get("region") or None,
        "country": loc.get("country") or None,
        "timezone": loc.get("timezone") or None,
        "name": loc.get("name") or None,
        "accuracy_km": round(float(loc.get("accuracy_km") or 0), 2) or None,
        "source": loc.get("source"),
    }
    return {k: v for k, v in out.items() if v is not None}


# ─── Watch ──────────────────────────────────────────────────────────────────

_watch_lock = threading.Lock()
_watch_thread: Optional[threading.Thread] = None
_watch_stop: Optional[threading.Event] = None


def _safe_say(player, message: str) -> None:
    if player is None:
        return
    try:
        if hasattr(player, "request_say"):
            player.request_say(message)
            return
    except Exception:
        pass
    try:
        player.write_log(f"JARVIS: {message}")
    except Exception:
        pass


def _watch_loop(
    stop: threading.Event, player, threshold_m: float, cooldown_s: float, max_s: float
) -> None:
    last_pos: Optional[dict] = None
    last_notify_at = 0.0
    t0 = time.monotonic()

    while not stop.is_set():
        if time.monotonic() - t0 > max_s:
            _safe_say(player, "Acompanhamento de localização encerrado.")
            break

        ok, data = _fetch()
        if ok:
            if last_pos is None:
                last_pos = data
                last_notify_at = time.monotonic()
            else:
                dist = _haversine_m(
                    last_pos["lat"], last_pos["lon"], data["lat"], data["lon"]
                )
                since = time.monotonic() - last_notify_at
                if dist >= threshold_m and since >= cooldown_s:
                    label = (
                        data.get("city") or f"({data['lat']:.4f}, {data['lon']:.4f})"
                    )
                    _safe_say(player, f"Este computador se moveu. Agora em {label}.")
                    last_pos = data
                    last_notify_at = time.monotonic()

        # Polling depende da fonte que respondeu por último.
        with _cache_lock:
            src = (_last_known or {}).get("source", "ip")
        wait = _POLL.get(src, 60)
        if stop.wait(wait):
            break


# ─── run ────────────────────────────────────────────────────────────────────


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _watch_thread, _watch_stop

    action = str(parameters.get("action", "")).strip().lower()
    if not action:
        return failed(
            "pc_location", "sem ação — current, watch, stop, set_manual, clear_manual?"
        )

    if action == "current":
        # Manual é estável — cache 24h. Windows/IP têm TTL curto.
        cached = None
        for src in ("manual", "windows", "ip"):
            with _cache_lock:
                if _last_known and _last_known.get("source") == src:
                    cached = _cached(_FRESH.get(src, 60))
                    if cached:
                        break
        if cached is None:
            ok, data = _fetch()
            if not ok:
                return failed("pc_location", data)
            cached = data

        facts = _fmt(cached)

        near_lat, near_lon = parameters.get("near_lat"), parameters.get("near_lon")
        if near_lat is not None and near_lon is not None:
            try:
                d = _haversine_m(
                    cached["lat"], cached["lon"], float(near_lat), float(near_lon)
                )
                facts["distance_m"] = round(d)
                facts["distance_km"] = round(d / 1000.0, 2)
                if parameters.get("near_name"):
                    facts["near"] = str(parameters["near_name"])[:80]
            except (TypeError, ValueError):
                pass

        if player:
            try:
                label = (
                    facts.get("name")
                    or facts.get("city")
                    or f"{facts['lat']:.3f},{facts['lon']:.3f}"
                )
                player.write_log(f"JARVIS: PC em {label} (fonte={facts['source']})")
            except Exception:
                pass

        return done("pc_location", **facts)

    if action == "set_manual":
        try:
            lat = float(parameters["manual_lat"])
            lon = float(parameters["manual_lon"])
        except (KeyError, TypeError, ValueError):
            return failed(
                "pc_location",
                "set_manual precisa de manual_lat e manual_lon numéricos.",
            )

        cfg = _load_config()
        cfg.update(
            {
                "manual": True,
                "lat": lat,
                "lon": lon,
                "name": str(parameters.get("manual_name") or "")[:80],
                "city": str(parameters.get("manual_name") or "")[:80],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        try:
            _save_config(cfg)
        except Exception as e:
            return failed("pc_location", f"não consegui salvar config: {e}")

        return done(
            "pc_location",
            action="set_manual",
            lat=lat,
            lon=lon,
            name=cfg["name"] or None,
            note="config salvo; localização manual passa a ser a fonte primária",
        )

    if action == "clear_manual":
        cfg = _load_config()
        if not cfg.get("manual"):
            return done(
                "pc_location",
                action="clear_manual",
                note="não havia manual configurado",
            )
        cfg["manual"] = False
        try:
            _save_config(cfg)
        except Exception as e:
            return failed("pc_location", f"não consegui salvar config: {e}")
        return done(
            "pc_location",
            action="clear_manual",
            note="manual desativado; volta a usar windows/ip",
        )

    if action == "watch":
        with _watch_lock:
            if _watch_thread is not None and _watch_thread.is_alive():
                return failed(
                    "pc_location",
                    "já existe um acompanhamento ativo — use action='stop' primeiro",
                )

            try:
                duration_min = int(
                    parameters.get("duration_minutes", _WATCH_DEFAULT_MIN)
                )
            except (TypeError, ValueError):
                duration_min = _WATCH_DEFAULT_MIN
            duration_min = max(1, min(duration_min, _WATCH_MAX_MIN))

            try:
                threshold_m = float(
                    parameters.get("move_threshold_meters", _WATCH_DEFAULT_THRESHOLD_M)
                )
            except (TypeError, ValueError):
                threshold_m = float(_WATCH_DEFAULT_THRESHOLD_M)

            ok, baseline = _fetch()
            if not ok:
                return failed("pc_location", baseline)

            if baseline.get("source") == "manual":
                return failed(
                    "pc_location",
                    "watch não faz sentido com localização manual — "
                    "ela nunca muda. Use clear_manual ou confie no "
                    "Windows/IP primeiro.",
                )

            _watch_stop = threading.Event()
            _watch_thread = threading.Thread(
                target=_watch_loop,
                args=(
                    _watch_stop,
                    player,
                    threshold_m,
                    _WATCH_COOLDOWN_MIN * 60,
                    duration_min * 60,
                ),
                daemon=True,
                name="pc-location-watch",
            )
            _watch_thread.start()

        return started(
            "pc_location",
            duration_min=duration_min,
            threshold_m=int(threshold_m),
            source=baseline.get("source"),
            accuracy_km=round(float(baseline.get("accuracy_km") or 0), 2),
            note="avisos chegam quando o PC mudar de posição",
        )

    if action == "stop":
        with _watch_lock:
            t, s = _watch_thread, _watch_stop
            _watch_thread, _watch_stop = None, None
        if t is None or not t.is_alive():
            return done("pc_location", action="stop", note="nada para parar")
        if s is not None:
            s.set()
        t.join(timeout=3.0)
        return done("pc_location", action="stop", note="acompanhamento encerrado")

    return failed("pc_location", f"ação desconhecida: {action}")

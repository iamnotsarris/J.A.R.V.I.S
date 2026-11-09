"""
jarvis_bridge.py — Ponte de comunicação JARVIS <-> Celular via WSS (Tudo em 1).
"""

from __future__ import annotations

import asyncio
import datetime
import json
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from core.outcome import done, failed, started

try:
    from aiohttp import web

    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).resolve().parent.parent

_CONFIG_DIR = _BASE / "config"
_CERT_FILE = _CONFIG_DIR / "jarvis_bridge_cert.pem"
_KEY_FILE = _CONFIG_DIR / "jarvis_bridge_key.pem"
_PORT = 8765

_server_thread: Optional[threading.Thread] = None
_server_loop: Optional[asyncio.AbstractEventLoop] = None
_server_runner: Optional[web.AppRunner] = None
_connected_clients: set = set()
_last_phone_status: dict = {}

PLUGIN = {
    "name": "jarvis_bridge",
    "description": (
        "Comunicação em tempo real com o celular via WSS. Use action='start' "
        "para ligar a ponte; 'status' para ver a bateria do celular; "
        "'notify' para enviar notificação; 'stop' para desligar."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'start', 'stop', 'status' ou 'notify'.",
            },
            "message": {
                "type": "STRING",
                "description": "Texto da notificação (para action='notify').",
            },
        },
        "required": ["action"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
    "tier": "extra",
}

# ─── HTML Embutido ───────────────────────────────────────────────────────────
_HTML_PAGE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>J.A.R.V.I.S.</title>
    <style>
        body { background: #050505; color: #00e5ff; font-family: monospace; text-align: center; padding: 20px; }
        .card { border: 1px solid #00e5ff; padding: 20px; border-radius: 10px; margin-bottom: 20px; background: rgba(0, 229, 255, 0.05); }
        button { background: transparent; border: 1px solid #00e5ff; color: #00e5ff; padding: 15px; font-size: 18px; margin: 10px; border-radius: 5px; cursor: pointer; width: 80%; }
        button:active { background: #00e5ff; color: #000; }
        #status { font-weight: bold; margin-bottom: 10px; color: #fff; }
    </style>
</head>
<body>
    <h2>J.A.R.V.I.S. Link</h2>
    <div class="card">
        <div id="status">Desconectado</div>
        <div>Bateria: <span id="battery">--</span></div>
    </div>
    <button onclick="sendCommand('pause_media')">⏯️ Play/Pause</button>
    <button onclick="sendCommand('next_track')">⏭️ Próxima Faixa</button>

    <script>
        let ws = null;
        
        function log(msg) {
            document.getElementById('status').innerText = msg;
        }

        function connect() {
            log("Conectando...");
            ws = new WebSocket(`wss://${window.location.host}/ws`);

            ws.onopen = () => {
                log("Conectado!");
                updateBattery();
                if (Notification.permission !== "granted") Notification.requestPermission();
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === "notification") {
                    if (Notification.permission === "granted") {
                        new Notification("JARVIS", { body: data.message });
                    } else {
                        alert("JARVIS: " + data.message);
                    }
                }
            };

            ws.onclose = () => log("Desconectado");
            ws.onerror = () => log("Erro (Aceite o certificado no navegador)");
        }

        function sendCommand(action) {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "command", action: action }));
            } else {
                alert("Não conectado");
            }
        }

        function updateBattery() {
            if ('getBattery' in navigator) {
                navigator.getBattery().then(battery => {
                    const update = () => {
                        const pct = Math.round(battery.level * 100);
                        document.getElementById('battery').innerText = pct + "%";
                        if (ws && ws.readyState === WebSocket.OPEN) {
                            ws.send(JSON.stringify({ type: "status", battery: pct, charging: battery.charging }));
                        }
                    };
                    update();
                    battery.addEventListener('levelchange', update);
                    battery.addEventListener('chargingchange', update);
                });
            }
        }

        // Inicia a conexão assim que a página carregar
        connect();
    </script>
</body>
</html>
"""


# ─── Lógica de Certificado ───────────────────────────────────────────────────
def _ensure_certificate():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if _CERT_FILE.exists() and _KEY_FILE.exists():
        return True
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with open(_KEY_FILE, "wb") as f:
            f.write(
                key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
        subject = issuer = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS Bridge")]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            )
            .sign(key, hashes.SHA256())
        )
        with open(_CERT_FILE, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        return True
    except Exception as e:
        print(f"[JarvisBridge] Erro certificado: {e}")
        return False


# ─── Servidor ────────────────────────────────────────────────────────────────
def _get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


async def _ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    _connected_clients.add(ws)
    print(f"[JarvisBridge] Celular conectado: {request.remote}")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    if data.get("type") == "status":
                        global _last_phone_status
                        _last_phone_status = data
                    elif data.get("type") == "command":
                        cmd = data.get("action")
                        if cmd == "pause_media":
                            import pyautogui

                            pyautogui.press("playpause")
                        elif cmd == "next_track":
                            import pyautogui

                            pyautogui.press("nexttrack")
                except Exception:
                    pass
    finally:
        _connected_clients.remove(ws)
        print(f"[JarvisBridge] Celular desconectado: {request.remote}")
    return ws


async def _http_handler(request):
    return web.Response(text=_HTML_PAGE, content_type="text/html")


async def _run_server():
    global _server_runner
    app = web.Application()
    app.router.add_get("/", _http_handler)
    app.router.add_get("/ws", _ws_handler)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=str(_CERT_FILE), keyfile=str(_KEY_FILE))

    _server_runner = web.AppRunner(app)
    await _server_runner.setup()
    site = web.TCPSite(_server_runner, "0.0.0.0", _PORT, ssl_context=ssl_context)
    await site.start()
    print(f"[JarvisBridge] Rodando em https://{_get_local_ip()}:{_PORT}")

    # Fica rodando para sempre até o processo ser morto
    while True:
        await asyncio.sleep(3600)


def _start_server_thread():
    global _server_loop, _server_thread
    if _server_thread and _server_thread.is_alive():
        return True

    def _target():
        global _server_loop
        _server_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_server_loop)
        _server_loop.run_until_complete(_run_server())

    _server_thread = threading.Thread(target=_target, daemon=True)
    _server_thread.start()
    time.sleep(1.5)
    return _server_thread.is_alive()


def _stop_server_thread():
    global _server_loop, _server_runner, _server_thread
    if _server_loop and _server_runner:
        asyncio.run_coroutine_threadsafe(_server_runner.cleanup(), _server_loop)
    _server_thread = None


# ─── Ações ───────────────────────────────────────────────────────────────────
def run(parameters: dict, player=None, session_memory=None) -> str:
    if not _AIOHTTP:
        return failed("jarvis_bridge", "Instale aiohttp: pip install aiohttp")

    action = str(parameters.get("action", "")).strip().lower()

    if action == "start":
        if not _ensure_certificate():
            return failed("jarvis_bridge", "Erro SSL.")
        if _start_server_thread():
            url = f"https://{_get_local_ip()}:{_PORT}"
            if player:
                try:
                    player.write_log(f"JARVIS: Acesse {url} no celular.")
                except:
                    pass
            return started("jarvis_bridge", url=url, note="Abra no Chrome do celular.")
        return failed("jarvis_bridge", "Falha ao iniciar servidor.")

    elif action == "stop":
        _stop_server_thread()
        return done("jarvis_bridge", action="stop")

    elif action == "status":
        if not _connected_clients:
            return done("jarvis_bridge", connected=False)
        return done(
            "jarvis_bridge",
            connected=True,
            battery=_last_phone_status.get("battery", "?"),
        )

    elif action == "notify":
        msg = str(parameters.get("message", "")).strip()
        if not msg:
            return failed("jarvis_bridge", "Mensagem vazia.")
        if not _connected_clients:
            return failed("jarvis_bridge", "Celular não conectado.")

        async def _send():
            payload = json.dumps({"type": "notification", "message": msg})
            for ws in list(_connected_clients):
                try:
                    await ws.send_str(payload)
                except:
                    pass

        if _server_loop:
            asyncio.run_coroutine_threadsafe(_send(), _server_loop)
        return done("jarvis_bridge", action="notify", status="enviado")

    return failed("jarvis_bridge", "Ação desconhecida.")

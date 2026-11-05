"""
network_scanner.py — Mapeia os dispositivos conectados na rede local.

COMO FUNCIONA
    1. Descobre o IP local e a máscara de sub-rede.
    2. Faz um "ping sweep" paralelo (ICMP) em todos os IPs da rede (limitado
       a 254 hosts para não travar nem sobrecarregar a rede).
    3. Lê a tabela ARP do sistema operacional para obter o MAC address dos
       dispositivos que responderam.
    4. Tenta resolver o hostname (nome do dispositivo) de cada IP.
    5. Retorna tudo formatado como JSON.

POR QUE NÃO USAR NMAP OU SCAPY
    O Nmap é excelente, mas exige instalação de um binário externo (que o
    antivírus do Windows adora bloquear). O Scapy precisa de privilégios
    de root/administrador para enviar pacotes ARP crus. A abordagem de
    ping + tabela ARP do SO funciona nativamente em qualquer plataforma,
    sem privilégios especiais e sem dependências extras além do que o
    JARVIS já usa.

SEGURANÇA
    O script não envia pacotes maliciosos nem faz port scanning. Apenas
    usa o comando `ping` nativo do SO com timeout curtíssimo (0.3s).
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import platform
import re
import socket
import subprocess
from typing import Any

from core.outcome import done, failed
from core.lazyimport import installed

_OS = platform.system()

PLUGIN = {
    "name": "network_scanner",
    "description": (
        "Escaneia a rede local (Wi-Fi ou cabo) e retorna a lista de "
        "dispositivos conectados, incluindo IP, MAC address e hostname. "
        "Use quando o usuário perguntar 'quais dispositivos estão na minha "
        "rede?', 'quem está conectado no Wi-Fi?', 'qual o IP da impressora?', "
        "'lista os IPs da rede'. NÃO use para escanear a internet ou testar "
        "velocidade de conexão."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "subnet": {
                "type": "STRING",
                "description": (
                    "Opcional. Forçar o escaneamento de uma sub-rede específica "
                    "(ex: '192.168.1.0/24'). Se omitido, descobre automaticamente."
                ),
            }
        },
        "required": [],
    },
    "behavior": "BLOCKING",
    "tier": "extra",
    "brief": "Lista IPs e dispositivos da rede local.",
}


def _get_local_ip_and_subnet() -> tuple[str, str]:
    """Descobre o IP local e assume uma máscara /24 se não souber a exata."""
    try:
        # Truque do UDP: não envia pacote real, apenas roteia para descobrir a interface
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()

        # Pega a rede /24 padrão (ex: 192.168.1.0/24)
        ip_obj = ipaddress.IPv4Address(local_ip)
        network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
        return local_ip, str(network)
    except Exception as e:
        print(f"[network_scanner] Falha ao descobrir rede: {e}")
        return "127.0.0.1", ""


def _ping_host(ip: str) -> bool:
    """Faz um ping rápido e silencioso em um IP. Retorna True se respondeu."""
    try:
        if _OS == "Windows":
            # -n 1 (1 ping), -w 300 (timeout 300ms)
            cmd = ["ping", "-n", "1", "-w", "300", ip]
        else:
            # -c 1 (1 ping), -W 1 (timeout 1s)
            cmd = ["ping", "-c", "1", "-W", "1", ip]

        # Oculta a janela preta no Windows
        startupinfo = None
        if _OS == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.5,
            startupinfo=startupinfo,
        )
        return res.returncode == 0
    except Exception:
        return False


def _get_arp_table() -> dict[str, str]:
    """Lê a tabela ARP do SO e retorna um dicionário {IP: MAC}."""
    arp_dict = {}
    try:
        if _OS == "Windows":
            cmd = ["arp", "-a"]
        elif _OS == "Darwin":  # macOS
            cmd = ["arp", "-an"]
        else:  # Linux
            cmd = ["ip", "neigh"]
            import shutil

            if not shutil.which("ip"):
                cmd = ["arp", "-an"]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)

        mac_regex = re.compile(
            r"([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})"
        )
        ip_regex = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")

        for line in res.stdout.splitlines():
            mac_match = mac_regex.search(line)
            ip_match = ip_regex.search(line)

            if mac_match and ip_match:
                ip = ip_match.group(1)
                mac = mac_match.group(1).upper()
                # Ignora MACs de broadcast/multicast para não poluir a lista
                if mac.startswith("FF:FF:FF") or mac.startswith("01:00:5E"):
                    continue
                arp_dict[ip] = mac
    except Exception as e:
        print(f"[network_scanner] Falha ao ler tabela ARP: {e}")
    return arp_dict


def _resolve_hostname(ip: str) -> str:
    """Tenta resolver o nome do dispositivo (hostname) via DNS reverso."""
    try:
        # Usa um timeout via socket para não demorar horas em IPs que não têm DNS
        socket.setdefaulttimeout(0.5)
        hostname, _, _ = socket.gethostbyaddr(ip)
        return hostname.split(".")[0]
    except Exception:
        return "Desconhecido"


def run(parameters: dict, player=None, session_memory=None) -> str:
    subnet_arg = str(parameters.get("subnet", "")).strip()

    local_ip, auto_subnet = _get_local_ip_and_subnet()
    target_subnet = subnet_arg or auto_subnet

    if not target_subnet:
        return failed("network_scanner", "Não consegui detectar a rede local.")

    try:
        network = ipaddress.IPv4Network(target_subnet, strict=False)
        if network.num_addresses > 256:
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
    except ValueError as e:
        return failed("network_scanner", f"Sub-rede inválida: {target_subnet}")

    if player:
        try:
            player.write_log(f"JARVIS: Escaneando rede {network}…")
        except Exception:
            pass

    hosts = list(network.hosts())
    # Remove o próprio IP da lista para não confundir
    hosts = [str(ip) for ip in hosts if str(ip) != local_ip]

    active_ips = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        # Mapeia os pings. Quem responder True, entra na lista.
        futures = {executor.submit(_ping_host, ip): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                active_ips.append(futures[future])

    # 2. Pega a tabela ARP para encontrar os MAC addresses
    arp_table = _get_arp_table()

    # 3. Monta o JSON final
    devices = []

    # Adiciona o próprio host na lista
    my_mac = arp_table.get(local_ip, "Próprio Host")
    devices.append(
        {
            "ip": local_ip,
            "mac": my_mac,
            "hostname": "JARVIS (Este PC)",
            "status": "Você",
        }
    )

    for ip in active_ips:
        mac = arp_table.get(ip, "Desconhecido")
        hostname = _resolve_hostname(ip)
        devices.append({"ip": ip, "mac": mac, "hostname": hostname})

    # Ordena a lista pelo último octeto do IP para ficar organizado
    devices.sort(key=lambda x: int(x["ip"].split(".")[-1]))

    # 4. Prepara o retorno
    payload = {"network": str(network), "total_found": len(devices), "devices": devices}

    json_str = json.dumps(payload, indent=2, ensure_ascii=False)

    if player:
        try:
            player.write_log(f"JARVIS: Encontrei {len(devices)} dispositivos ativos.")
        except Exception:
            pass

    # O JARVIS lê o JSON diretamente no resultado da ferramenta
    return done("network_scanner", data=json_str)

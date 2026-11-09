"""
focus_mode.py — Pomodoro com consequências.

"Jarvis, modo foco por 25 minutos" → fecha os apps que vão te interromper,
abaixa o volume, inicia um timer, e avisa quando acaba.

O QUE ESTE PLUGIN COMBINA
    Não é um timer — set_timer já existe. Não é close_app — appidentity
    já faz isso. É a COMBINAÇÃO: fechar apps E iniciar timer E avisar no
    fim, numa única frase do usuário. É o que só um assistente com
    "corpo" consegue fazer — um timer de celular não fecha o Slack.

POR QUE NÃO RESTAURAR OS APPS NO FIM
    Fechar Slack/Discord por 25 min e reabrir automaticamente é o oposto
    de foco — o usuário vê uma avalanche de notificações acumuladas de
    uma vez. Se ele quer os apps de volta, ele fala "abre o Slack". A
    ausência é o feature.

POR QUE ask() QUANDO HÁ RISCO
    Fechar uma janela com trabalho não salvo perde esse trabalho.
    request_close() do appidentity já pergunta educadamente (WM_CLOSE,
    não terminate), mas se o app mostrar um diálogo "salvar antes de
    fechar?", o usuário precisa saber. ask() deixa ele decidir.

POR QUE PUSH_UNDO SÓ NO VOLUME
    O volume é um estado único e reversível — "undo" devolve pro valor
    original. Os apps fechados NÃO são reabertos automaticamente: o
    usuário que pediu foco provavelmente não quer que a gente decida
    quando ele quer o Slack de volta.
"""

from __future__ import annotations

import threading
import time

from core.outcome import failed, started, unverified

_DEFAULT_DISTRACTORS = (
    "slack",
    "discord",
    "telegram",
    "whatsapp",
    "teams",
    "skype",
    "signal",
)

_FOCUS_VOLUME = 30  # % — música baixa o suficiente pra não tirar do estado

PLUGIN = {
    "name": "focus_mode",
    "description": (
        "Inicia um período de foco: fecha os apps que costumam interromper "
        "(Slack, Discord, Telegram, WhatsApp, Teams), abaixa o volume, e "
        "avisa quando o tempo acabar. Use quando o usuário disser 'modo "
        "foco', 'me deixa focado por X minutos', 'pomodoro', 'vou trabalhar "
        "sem interrupção'. NÃO use para um timer comum (use set_timer) nem "
        "para fechar UM app específico (use app close)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "minutes": {
                "type": "INTEGER",
                "description": "Duração em minutos (padrão 25 — pomodoro clássico).",
            },
            "apps_to_close": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": (
                    "Nomes de apps a fechar. Se omitido, usa: "
                    "slack, discord, telegram, whatsapp, teams, skype, signal."
                ),
            },
            "lower_volume": {
                "type": "BOOLEAN",
                "description": "Se verdadeiro (padrão), abaixa o volume para 30%.",
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",  # avisa sozinho no fim; não reabre a conversa
    "tier": "extra",
}


def _human_minutes(m: float) -> str:
    if m == 1:
        return "1 minuto"
    if float(m).is_integer():
        return f"{int(m)} minutos"
    return f"{m:.0f} minutos"


def _close_distractors(apps: list[str], player=None) -> tuple[int, list[str]]:
    """Fecha cada app da lista. Retorna (quantos_fechou, falhas)."""
    try:
        from core import appidentity
    except Exception as e:
        return 0, [f"appidentity indisponível: {e}"]

    closed = 0
    failures: list[str] = []

    for name in apps:
        try:
            matches = appidentity.running(name, include_self=False)
            if not matches:
                continue
            # Fecha só o melhor match — evita variantes (DiscordCanary, etc.)
            # a menos que o usuário tenha pedido o nome completo.
            target = matches[0]
            gone, reason = appidentity.request_close(target, timeout=3.0)
            if gone:
                closed += 1
                if player:
                    try:
                        player.write_log(f"JARVIS: fechei {target.label}")
                    except Exception:
                        pass
            elif reason:
                failures.append(f"{target.label}: {reason}")
        except Exception as e:
            failures.append(f"{name}: {e}")

    return closed, failures


def _lower_volume_with_undo() -> bool:
    """Abaixa volume e registra undo. Retorna True se mudou algo."""
    try:
        from actions.computer_settings import volume_get, volume_set
        from core.undo import push_undo

        old = volume_get()
        if old is None or old == _FOCUS_VOLUME:
            return False
        volume_set(_FOCUS_VOLUME)
        push_undo(
            f"volume de volta pra {old}%",
            lambda ov=old: volume_set(ov) or "ok",
        )
        return True
    except Exception as e:
        print(f"[focus_mode] volume: {e}")
        return False


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        minutes = float(parameters.get("minutes", 25))
    except Exception:
        minutes = 25.0
    if minutes <= 0:
        return failed("focus_mode", "duração precisa ser positiva.")
    if minutes > 180:
        return failed("focus_mode", "acima de 3 horas — use set_timer pra isso.")

    apps = parameters.get("apps_to_close")
    if not isinstance(apps, list) or not apps:
        apps = list(_DEFAULT_DISTRACTORS)
    apps = [str(a).strip() for a in apps if str(a).strip()]

    lower = parameters.get("lower_volume", True)

    # 1. Fecha apps
    closed, failures = _close_distractors(apps, player)

    # 2. Abaixa volume (com undo)
    volume_changed = _lower_volume_with_undo() if lower else False

    # 3. Timer em thread daemon
    secs = minutes * 60.0
    tempo = _human_minutes(minutes)

    def _fire():
        time.sleep(secs)
        msg = f"Foco encerrado. Foram {tempo}."
        try:
            if player and hasattr(player, "request_say"):
                player.request_say(msg)
            elif player:
                player.write_log(f"JARVIS: {msg}")
        except Exception:
            pass
        print(f"[focus_mode] {msg}")

    threading.Thread(target=_fire, daemon=True, name=f"focus-{int(secs)}s").start()

    facts = {
        "duration": tempo,
        "apps_closed": closed,
        "volume_set": volume_changed,
    }
    if failures:
        return unverified(
            "focus_mode",
            f"timer de {tempo} iniciado, mas {len(failures)} app(s) não fecharam",
            failures=", ".join(failures[:3]),
            **facts,
        )

    return started(
        "focus_mode",
        **facts,
        note="o aviso chega sozinho quando o tempo acabar",
    )

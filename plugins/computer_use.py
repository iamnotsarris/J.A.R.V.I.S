"""
computer_use.py — controle do desktop guiado por visão.

Um agente curto: tira screenshot → o modelo escolhe UM passo → executa →
repete, até cumprir o objetivo ou estourar o limite.

POR QUE É UM LOOP E NÃO UMA AÇÃO
    "Abre o navegador e pesquisa X" não é uma chamada pyautogui — é uma
    sequência que depende do que apareceu na tela entre cada passo. O
    modelo precisa VER o resultado do clique anterior para decidir o
    próximo. É o padrão do screen_agent, embutido aqui como plugin
    delimitado para uma tarefa.

POR QUE VERBOS FIXOS
    Aceitar código ou strings de shell seria execução arbitrária exposta
    ao LLM, que lê web. Injeção de prompt viraria RCE. Aqui o modelo
    escolhe entre verbos nomeados; o pior que consegue é o pior clique
    possível, não o pior comando possível.

O RISCO, SEM ENFEITE
    Controle de mouse+teclado + LLM que lê web = injeção vira ação.
    Mitigações que cabem neste arquivo:
      - verbos fixos (sem código, sem shell)
      - limite de passos e timeout
      - toda ação vai pro log, e o modelo vê o histórico
      - "ask" antes de ações destrutivas é instruído no prompt
    O que NÃO cabe aqui: sandbox de verdade. Se você tem dados
    sensíveis nessa máquina, considere rodar isso numa sessão/conta
    separada, ou mantenha o JARVIS supervisionado.
"""

from __future__ import annotations

import io
import time

from core import gemini
from core.lazyimport import LazyModule, installed
from core.outcome import ask, done, failed, unverified

_PYAUTOGUI = installed("pyautogui")
pyautogui = LazyModule(
    "pyautogui",
    lambda m: (setattr(m, "FAILSAFE", True), setattr(m, "PAUSE", 0.05)),
)
# Failsafe: jogar o mouse no canto superior esquerdo aborta o pyautogui.
# É o botão de pânico que o usuário tem. Não remova.

MAX_STEPS_DEFAULT = 15
MAX_STEPS_HARD = 30
TIMEOUT_S = 120.0

PLUGIN = {
    "name": "computer_use",
    "description": (
        "Executa um objetivo no desktop controlando mouse e teclado, guiado "
        "por captura de tela. Use para tarefas que exigem vários passos em "
        "apps sem API: 'abre o navegador e pesquisa X', 'preenche o "
        "formulário com os dados do arquivo Y', 'navega até a página Z e "
        "clica no botão de download'. NÃO use para uma única ação isolada "
        "(use computer_control) nem para abrir/fechar app (use app open/close)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "goal": {
                "type": "STRING",
                "description": "O objetivo em linguagem natural, específico e verificável.",
            },
            "max_steps": {
                "type": "INTEGER",
                "description": f"Limite de passos (padrão {MAX_STEPS_DEFAULT}, máximo {MAX_STEPS_HARD}).",
            },
        },
        "required": ["goal"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "WHEN_IDLE",
    "tier": "extra",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Captura e prompt
# ─────────────────────────────────────────────────────────────────────────────


def _screenshot() -> tuple[bytes, tuple[int, int]]:
    """PNG da tela inteira + (largura, altura). Levanta se mss falhar."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        raw = sct.grab(sct.monitors[0])  # monitor 0 = todos, mosaicados
        img = Image.frombytes("RGB", raw.size, raw.rgb)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), img.size


def _to_part(png: bytes):
    """Part do SDK com inline_data — o mesmo formato que core.gemini espera."""
    from google.genai import types as gtypes

    return gtypes.Part.from_bytes(data=png, mime_type="image/png")


def _build_prompt(goal: str, history: list[dict], size: tuple[int, int]) -> str:
    w, h = size
    hist = (
        "\n".join(
            f"  {i+1}. {s['action']} {s.get('args', {})} → "
            f"{'ok' if s['ok'] else 'FALHOU: ' + s['detail']}"
            for i, s in enumerate(history)
        )
        or "  (nenhum passo ainda)"
    )
    return f"""Você está controlando o mouse e o teclado de um computador.
Olhe a captura de tela ({w}x{h} px) e decida O PRÓXIMO passo único.

OBJETIVO: {goal}

PASSOS JÁ EXECUTADOS:
{hist}

Responda SOMENTE com JSON, um destes:
  {{"action": "click", "x": <int>, "y": <int>, "button": "left"|"right", "reason": "..."}}
  {{"action": "move",  "x": <int>, "y": <int>, "reason": "..."}}
  {{"action": "type",  "text": "...", "reason": "..."}}
  {{"action": "key",   "combo": "ctrl+s", "reason": "..."}}
  {{"action": "scroll","direction": "up"|"down", "amount": <int>, "reason": "..."}}
  {{"action": "done",  "summary": "o que foi feito"}}
  {{"action": "ask",   "question": "o que você precisa saber"}}

REGRAS:
- Coordenadas em pixels da TELA INTEIRA, não da janela. O canto superior
  esquerdo é (0, 0).
- UM passo por resposta. Na próxima rodada você vê o resultado.
- Se o próximo clique puder destruir algo (Apagar, Excluir, Formatar,
  Fechar sem salvar, Enviar mensagem, Confirmar compra), use "ask" antes.
- Nunca digite senhas, chaves de API, ou execute comandos de shell
  destrutivos (rm -rf, format, del /f /s, diskpart). Se o objetivo exigir,
  use "ask".
- Se estiver travado há 2 passos no mesmo ponto, use "ask".
- Não invente: se não vê o que precisa, diga o que vê com "ask".
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Execução de UM passo
# ─────────────────────────────────────────────────────────────────────────────


def _execute(action: str, d: dict) -> tuple[bool, str]:
    if not _PYAUTOGUI:
        return False, "pyautogui não está instalado"
    try:
        if action == "click":
            pyautogui.click(
                x=int(d["x"]), y=int(d["y"]), button=d.get("button", "left")
            )
        elif action == "move":
            pyautogui.moveTo(x=int(d["x"]), y=int(d["y"]))
        elif action == "type":
            text = str(d.get("text", ""))
            if not text:
                return False, "texto vazio"
            pyautogui.write(text, interval=0.02)
        elif action == "key":
            combo = str(d.get("combo", "")).strip()
            keys = [k.strip() for k in combo.split("+") if k.strip()]
            if not keys:
                return False, "combo vazio"
            pyautogui.hotkey(*keys)
        elif action == "scroll":
            amount = int(d.get("amount", 3))
            amount = max(1, min(50, abs(amount)))
            if d.get("direction") == "up":
                amount = -amount
            pyautogui.scroll(amount)
        else:
            return False, f"ação desconhecida: {action}"
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
#  O loop
# ─────────────────────────────────────────────────────────────────────────────


def run(parameters: dict, player=None, session_memory=None) -> str:
    goal = str(parameters.get("goal", "")).strip()
    if not goal:
        return failed("computer_use", "sem objetivo — diga o que fazer.")

    steps = int(parameters.get("max_steps", MAX_STEPS_DEFAULT))
    steps = max(1, min(steps, MAX_STEPS_HARD))

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(f"JARVIS: {msg}")
            except Exception:
                pass

    history: list[dict] = []
    t0 = time.monotonic()

    for i in range(steps):
        if time.monotonic() - t0 > TIMEOUT_S:
            return unverified(
                "computer_use",
                f"timeout de {int(TIMEOUT_S)}s após {i} passos",
                goal=goal,
                steps_taken=i,
            )

        try:
            png, size = _screenshot()
        except Exception as e:
            return failed("computer_use", f"não consegui capturar a tela: {e}")

        decision = gemini.as_json(
            [_to_part(png), _build_prompt(goal, history, size)],
            tier=gemini.SMART,
            timeout_ms=60_000,
        )

        if not isinstance(decision, dict) or "action" not in decision:
            return unverified(
                "computer_use",
                "o modelo não devolveu uma ação válida",
                goal=goal,
                steps_taken=i,
            )

        action = str(decision.get("action", "")).lower()

        if action == "done":
            summary = str(decision.get("summary", "")).strip()
            _log(f"objetivo cumprido em {i+1} passo(s).")
            return done("computer_use", goal=goal, steps=i + 1, summary=summary or None)

        if action == "ask":
            question = (
                str(decision.get("question", "")).strip()
                or "preciso de mais informação"
            )
            return ask("computer_use", question, goal=goal, steps_taken=i)

        ok, detail = _execute(action, decision)
        args_preview = {
            k: v for k, v in decision.items() if k not in ("action", "reason")
        }
        history.append(
            {"action": action, "args": args_preview, "ok": ok, "detail": detail}
        )
        _log(
            f"computer_use: {action} {args_preview} "
            f"{'ok' if ok else 'FALHOU: ' + detail}"
        )

    return unverified(
        "computer_use",
        f"limite de {steps} passos atingido sem cumprir o objetivo",
        goal=goal,
        steps_taken=steps,
    )

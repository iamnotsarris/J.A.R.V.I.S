"""
blender_modeler.py — Cria modelos 3D no Blender por comando de voz.

COMO FUNCIONA
    O usuário descreve o que quer (ex: "uma nave espacial com propulsores
    azuis"). O plugin envia esse pedido para o `core.gemini`, que escreve
    um script Python nativo usando a API `bpy` do Blender.
    O script é salvo num arquivo temporário e executado pelo Blender em
    modo headless (background). Ao final, o arquivo .blend gerado é
    aberto na tela.

POR QUE NÃO AUTOMATIZAR A UI
    Usar pyautogui no Blender seria frágil (depende da versão, do idioma
    da interface, da resolução da tela). Via `bpy`, temos controle
    absoluto, e o script gerado é salvo para você poder reaproveitar.

SEGURANÇA
    O script gerado roda dentro do Blender. Como ele não é executado com
    privilégios de SO, o pior que pode acontecer é o Blender travar.
    Ainda assim, instruímos o Gemini a gerar apenas chamadas de modelagem
    geométrica (bpy.ops.mesh, bpy.data.meshes, etc), sem acesso a rede
    ou sistema de arquivos.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from core import gemini
from core.outcome import failed, started, unverified

_OS = platform.system()

PLUGIN = {
    "name": "blender_modeler",
    "description": (
        "Cria objetos e modelos 3D no Blender descrevendo-os por voz. "
        "Use quando o usuário disser 'cria no blender uma...', 'modela uma...', "
        "'faz um objeto 3D de...'. O JARVIS gera o script, executa no Blender "
        "e abre o arquivo final. NÃO use para animar, renderizar vídeos longos "
        "ou editar texturas complexas (apenas modelagem e materiais básicos)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {
                "type": "STRING",
                "description": (
                    "A descrição detalhada do modelo 3D. Quanto mais "
                    "específico (formas, cores, quantidade), melhor o resultado. "
                    "Ex: 'um robô futurista feito de cubos e cilindros, com "
                    "olhos vermelhos'."
                ),
            },
        },
        "required": ["description"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
    "tier": "extra",
    "brief": "Modela objetos no Blender.",
}

# ─────────────────────────────────────────────────────────────────────────────
#  Descoberta do Executável do Blender
# ─────────────────────────────────────────────────────────────────────────────


def _find_blender() -> str | None:
    """Tenta achar o executável do Blender no PATH ou nas pastas padrão."""
    import shutil

    # 1. Tenta no PATH do sistema
    for cmd in ("blender", "Blender"):
        if shutil.which(cmd):
            return shutil.which(cmd)

    # 2. Tenta caminhos padrão por SO
    if _OS == "Windows":
        base = Path("C:/Program Files/Blender Foundation")
        if base.exists():
            # Pega a versão mais recente instalada
            versions = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
            for v in versions:
                exe = v / "blender.exe"
                if exe.exists():
                    return str(exe)

    elif _OS == "Darwin":  # macOS
        exe = Path("/Applications/Blender.app/Contents/MacOS/Blender")
        if exe.exists():
            return str(exe)

    elif _OS == "Linux":
        for path in ("/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender"):
            if Path(path).exists():
                return path

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Geração do Script Python (bpy)
# ─────────────────────────────────────────────────────────────────────────────


def _generate_blender_script(description: str, output_path: str) -> str:
    """Pede ao Gemini para escrever o script bpy."""

    prompt = f"""Você é um especialista na API Python do Blender (bpy). 
Escreva um script Python que monte a seguinte cena/objeto 3D:

DESCRIÇÃO: {description}

REGRAS ESTRITAS:
1. O script será executado no Blender 3.x ou 4.x.
2. Limpe a cena inicial completamente (delete todos os objetos padrão: Cube, Light, Camera).
3. Construa o objeto usando `bpy.ops.mesh.primitive_*` (cubos, cilindros, esferas) ou `bmesh`. NÃO use importação de arquivos externos.
4. Aplique materiais (Principled BSDF) com as cores solicitadas na descrição. Defina propriedades de Metalic e Roughness para ficar bonito.
5. Agrupe os objetos criados (se houver mais de um).
6. No final do script, adicione uma câmera e uma luz (Sun ou Area Light) para iluminar o objeto.
7. Salve o arquivo .blend no caminho EXATO fornecido: `bpy.ops.wm.save_as_mainfile(filepath=r"{output_path}")`.

NÃO escreva explicações, NÃO use blocos de código markdown (```python). Retorne APENAS o código Python puro que o Blender possa executar diretamente."""

    response = gemini.text(prompt, tier=gemini.SMART, timeout_ms=60_000)

    # Limpeza caso o modelo retorne as crases do markdown
    if response.startswith("```"):
        lines = response.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines[-1].startswith("```"):
            lines = lines[:-1]
        response = "\n".join(lines)

    return response.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  run
# ─────────────────────────────────────────────────────────────────────────────


def run(parameters: dict, player=None, session_memory=None) -> str:
    description = str(parameters.get("description", "")).strip()
    if not description:
        return failed("blender_modeler", "Sem descrição do que modelar.")

    blender_exe = _find_blender()
    if not blender_exe:
        return failed(
            "blender_modeler",
            "Blender não encontrado. Instale o Blender ou adicione ao PATH do sistema.",
        )

    # Define onde o arquivo final será salvo
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).resolve().parent.parent

    output_dir = base_dir / "output" / "blender"
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_name = (
        "".join(c for c in description[:20] if c.isalnum() or c == " ")
        .strip()
        .replace(" ", "_")
    )
    if not safe_name:
        safe_name = "modelo_jarvis"
    blend_file = str(output_dir / f"{safe_name}_{int(time.time())}.blend")

    # 1. Gera o script via Gemini
    if player:
        try:
            player.write_log(f"JARVIS: Escrevendo o script 3D para '{description}'...")
        except Exception:
            pass

    try:
        script_code = _generate_blender_script(description, blend_file)
    except Exception as e:
        return failed("blender_modeler", f"Gemini falhou ao gerar o script: {e}")

    # 2. Salva o script num arquivo temporário
    temp_fd, temp_path = tempfile.mkstemp(suffix=".py", text=True)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(script_code)
    except Exception as e:
        return failed("blender_modeler", f"Falha ao salvar script temp: {e}")

    # 3. Executa o Blender em background
    if player:
        try:
            player.write_log(
                "JARVIS: Iniciando o Blender em background para criar o modelo..."
            )
        except Exception:
            pass

    try:
        # --background: roda sem interface
        # --python: executa o script
        result = subprocess.run(
            [blender_exe, "--background", "--python", temp_path],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            # Loga o erro do Blender para debug
            print(f"[blender_modeler] Erro no Blender:\n{result.stderr}")
            return unverified(
                "blender_modeler",
                "O Blender encontrou um erro ao executar o script de modelagem.",
                detail=result.stderr[-200:],  # Pega o finalzinho do erro
            )

    except subprocess.TimeoutExpired:
        return failed(
            "blender_modeler", "O Blender demorou mais de 2 minutos e foi abortado."
        )
    except Exception as e:
        return failed("blender_modeler", f"Falha ao executar o Blender: {e}")
    finally:
        # Limpa o arquivo temporário
        try:
            os.remove(temp_path)
        except Exception:
            pass

    if not os.path.exists(blend_file):
        return failed(
            "blender_modeler",
            "O Blender terminou, mas o arquivo .blend não foi encontrado. O script pode ter falhado silenciosamente.",
        )

    # 4. Abre o arquivo final no Blender com interface gráfica
    if player:
        try:
            player.write_log("JARVIS: Modelo criado! Abrindo o Blender...")
        except Exception:
            pass

    try:
        if _OS == "Windows":
            os.startfile(blend_file)
        elif _OS == "Darwin":
            subprocess.Popen(["open", blend_file])
        else:
            subprocess.Popen(["xdg-open", blend_file])
    except Exception as e:
        print(f"[blender_modeler] Falha ao abrir a UI do Blender: {e}")

    return started(
        "blender_modeler",
        model=description[:50],
        note="O Blender foi aberto com o seu modelo",
    )

"""
JARVIS HUD Avatar — Estilo Homem de Ferro (Mark II).

Releitura da UI do JARVIS dos filmes do Tony Stark. Mantém a interface
pública IDÊNTICA à versão anterior (step, paint, glance, SPAN, shaded),
então nenhum outro arquivo do projeto precisa mudar.

Linguagem visual nova:
  * Paleta puxada para ciano (#00E5FF) e branco puro.
  * Arc Reactor central com anel de contenção e cruz em N/E/S/W.
  * Anéis SEGMENTADOS em vez de arcos contínuos — o look de máquina.
  * Grid hexagonal sutil ao fundo ("mesa de projeção").
  * Anéis de pulso expansivos a cada ~2.2s (sonar).
  * Labels reais: nome no topo, timestamp embaixo, estado à direita.
  * Scanlines em dupla camada (uma desce, outra sobe).

O que NÃO muda:
  * Assinatura de step(), paint(), glance().
  * Nós 3D e data streams (mesma física, mesma densidade).
  * A dependência de PyQt6 e os imports existentes.
"""

from __future__ import annotations

import math
import random
import time
from datetime import datetime

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient, QFont

# ── Paleta Homem de Ferro ───────────────────────────────────────────────────
IRON_CYAN = QColor(0, 229, 255)
IRON_BLUE = QColor(0, 150, 220)
IRON_WHITE = QColor(255, 255, 255)
IRON_DEEP = QColor(0, 60, 100)


def _c(col: QColor, a: float) -> QColor:
    q = QColor(col)
    q.setAlpha(int(max(0.0, min(255.0, a))))
    return q


def _tint(col: QColor) -> QColor:
    """Puxa uma cor em direção ao ciano do Homem de Ferro, preservando-a se
    já for azulada. O app pode passar qualquer primary/accent — o resultado
    sempre tem o 'sabor' do JARVIS dos filmes."""
    if col.blue() > 100 and col.blue() > col.red() * 0.8:
        return col
    return QColor(
        int(col.red() * 0.25 + IRON_CYAN.red() * 0.75),
        int(col.green() * 0.25 + IRON_CYAN.green() * 0.75),
        int(col.blue() * 0.25 + IRON_CYAN.blue() * 0.75),
    )


class HoloAvatar:
    shaded = True
    SPAN = 2.0

    # Nome exibido no HUD. Pode ser trocado depois:
    #   avatar = HoloAvatar(); avatar.assistant_name = "FRIDAY"
    assistant_name = "J.A.R.V.I.S."

    # Comportamento dos pulsos
    _PULSE_INTERVAL = 2.2  # segundos entre pulsos
    _PULSE_SPEED = 0.60  # progresso/segundo (0→1)

    def __init__(self) -> None:
        self._t = 0.0
        self._energy = 0.0
        self._glow = 0.0
        self._rotation = 0.0
        self._radar_angle = 0.0
        self._scan_y = -1.0
        self._state = "IDLE"

        # Estado Homem de Ferro
        self._arc_phase = 0.0
        self._pulse_rings: list[dict] = []
        self._last_pulse = 0.0
        self._label_update_at = 0.0
        self._label_time_str = ""
        self._label_state_str = "IDLE"

        # Nós 3D
        self._nodes_3d = [
            {
                "theta": random.uniform(0, math.pi * 2),
                "phi": random.uniform(0, math.pi),
                "size": random.uniform(1.5, 4.0),
                "speed": random.uniform(0.2, 0.6),
            }
            for _ in range(32)
        ]

        # Data streams (hex)
        hex_chars = "0123456789ABCDEF"
        self._data_streams = []
        for _ in range(6):
            text = "".join(random.choice(hex_chars) for _ in range(8))
            self._data_streams.append(
                {
                    "text": f"0x{text}",
                    "x": random.uniform(-1.5, 1.5),
                    "y": random.uniform(-1.5, 1.5),
                    "speed": random.uniform(0.1, 0.3),
                }
            )

    # ── Atualização ─────────────────────────────────────────────────────────

    def step(
        self,
        dt,
        amp,
        speaking=False,
        muted=False,
        state="",
        v_open=None,
        v_wide=0.0,
        v_level=None,
        v_seq=None,
        v_hop=0.02,
    ):
        dt = max(0.001, min(0.10, float(dt)))
        self._t += dt
        self._state = state.upper() or "IDLE"

        # Energia (áudio + fala)
        target = max(amp, v_open or 0.0) if speaking and not muted else 0.0
        rate = 0.35 if target > self._energy else 0.08
        self._energy += (target - self._energy) * rate
        self._glow += ((1.0 if speaking else 0.0) - self._glow) * 0.05

        # Velocidades — mais mecânicas em THINKING
        speed_mult = 2.2 if self._state in ("THINKING", "PROCESSING") else 1.0
        self._rotation += dt * (0.8 + 1.2 * self._energy) * speed_mult
        self._radar_angle += dt * 90 * speed_mult
        self._arc_phase += dt * (0.4 + 1.8 * self._energy) * speed_mult

        # Scanline
        self._scan_y += dt * (1.5 if self._state == "THINKING" else 0.6)
        if self._scan_y > 1.2:
            self._scan_y = -1.2

        # Pulsos expansivos
        self._last_pulse += dt
        if self._last_pulse >= self._PULSE_INTERVAL:
            self._last_pulse = 0.0
            self._pulse_rings.append({"progress": 0.0})
        for pulse in self._pulse_rings[:]:
            pulse["progress"] += dt * self._PULSE_SPEED
            if pulse["progress"] >= 1.0:
                self._pulse_rings.remove(pulse)

        # Labels (atualizadas 1x/segundo — caro criar string toda frame)
        now = time.monotonic()
        if now - self._label_update_at >= 1.0:
            self._label_update_at = now
            self._label_time_str = datetime.now().strftime("%H:%M:%S")
            self._label_state_str = self._state

        # Nós 3D
        for node in self._nodes_3d:
            node["theta"] += dt * node["speed"] * speed_mult

        # Data streams
        for s in self._data_streams:
            s["y"] -= dt * s["speed"] * speed_mult
            if s["y"] < -1.5:
                s["y"] = 1.5
                s["x"] = random.uniform(-1.5, 1.5)

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        pass

    # ── Desenho ─────────────────────────────────────────────────────────────

    def paint(self, p, cx, cy, r, primary, accent, bg=None):
        if bg is None:
            bg = QColor(0, 0, 0)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Tintura Homem de Ferro
        primary = _tint(primary)
        accent = _tint(accent)

        energy = self._energy
        rot_deg = math.degrees(self._rotation)
        arc_deg = math.degrees(self._arc_phase)

        # 1. Aura de fundo
        self._draw_aura(p, cx, cy, r, primary, energy)

        # 2. Grid hexagonal
        self._draw_hex_grid(p, cx, cy, r, primary)

        # 3. Pulsos (sonar)
        self._draw_pulse_rings(p, cx, cy, r, accent)

        # 4. Radial spokes
        self._draw_radial_spokes(
            p, cx, cy, r * 0.55, r * 1.25, primary, count=6, rotation=rot_deg * 0.3
        )

        # 5. Anel tracejado externo
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(primary, 180), 2.0, Qt.PenStyle.DashLine))
        p.save()
        p.translate(cx, cy)
        p.rotate(-rot_deg * 0.4)
        p.drawEllipse(QRectF(-r * 1.4, -r * 1.4, r * 2.8, r * 2.8))
        p.restore()

        # 6. Anel de ticks
        p.save()
        p.translate(cx, cy)
        for i in range(0, 360, 10):
            p.save()
            p.rotate(i)
            tick_len = r * 0.10 if i % 30 == 0 else r * 0.05
            width = 1.8 if i % 30 == 0 else 1.0
            alpha = 160 if i % 30 == 0 else 110
            p.setPen(QPen(_c(primary, alpha), width))
            p.drawLine(QPointF(0, -r * 1.15), QPointF(0, -r * 1.15 - tick_len))
            p.restore()
        p.restore()

        # 7. Anel segmentado externo (8 segmentos, rotação rápida)
        self._segmented_ring(
            p,
            cx,
            cy,
            r * 0.95,
            accent,
            segments=8,
            gap_deg=5,
            width=2.2,
            rotation=rot_deg,
            energy=energy,
        )

        # 8. Anel de contenção interno (4 segmentos, contra-rotação)
        self._segmented_ring(
            p,
            cx,
            cy,
            r * 0.70,
            primary,
            segments=4,
            gap_deg=14,
            width=2.5,
            rotation=-rot_deg * 1.3,
            energy=energy,
        )

        # 9. Corner brackets
        self._draw_corner_brackets(p, cx, cy, r, accent)

        # 10. Nós 3D
        self._draw_nodes_3d(p, cx, cy, r, primary, accent)

        # 11. Arc Reactor
        self._draw_arc_reactor(p, cx, cy, r, primary, accent, energy, arc_deg)

        # 12. Data streams
        self._draw_data_streams(p, cx, cy, r, primary, energy)

        # 13. Scanlines
        self._draw_scanlines(p, cx, cy, r, accent, energy)

        # 14. Labels
        self._draw_labels(p, cx, cy, r, primary, accent, energy)

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _draw_aura(self, p, cx, cy, r, primary, energy):
        aura_r = r * 2.0
        grad = QRadialGradient(cx, cy, aura_r)
        grad.setColorAt(0.0, _c(primary, 30 + 80 * energy))
        grad.setColorAt(0.4, _c(primary, 8 + 25 * energy))
        grad.setColorAt(1.0, _c(primary, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - aura_r, cy - aura_r, aura_r * 2, aura_r * 2))

    def _hexagon(self, p, cx, cy, size):
        pts = []
        for i in range(6):
            angle = math.radians(60 * i - 30)
            pts.append(
                QPointF(cx + size * math.cos(angle), cy + size * math.sin(angle))
            )
        for i in range(6):
            p.drawLine(pts[i], pts[(i + 1) % 6])

    def _draw_hex_grid(self, p, cx, cy, r, color):
        size = r * 0.11
        w = size * math.sqrt(3)
        h = size * 1.5
        p.setBrush(Qt.BrushStyle.NoBrush)
        for row in range(-3, 4):
            for col in range(-3, 4):
                hx = cx + col * w + (row % 2) * (w / 2)
                hy = cy + row * h
                dist = math.hypot(hx - cx, hy - cy)
                if dist > r * 1.35:
                    continue
                opacity = max(15, 60 * (1 - dist / (r * 1.35)))
                p.setPen(QPen(_c(color, opacity), 1.0))
                self._hexagon(p, hx, hy, size * 0.85)

    def _draw_pulse_rings(self, p, cx, cy, r, accent):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pulse in self._pulse_rings:
            prog = pulse["progress"]
            opacity = max(0.0, 180 * (1 - prog))
            radius = r * 0.25 + prog * r * 1.25
            width = 2.5 * (1 - prog) + 0.5
            p.setPen(QPen(_c(accent, opacity), width))
            p.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    def _segmented_ring(
        self, p, cx, cy, radius, color, segments, gap_deg, width, rotation, energy=0.0
    ):
        p.save()
        p.translate(cx, cy)
        p.rotate(rotation)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc_span = (360.0 - segments * gap_deg) / segments
        p.setPen(QPen(_c(color, 180 + 75 * energy), width))
        for i in range(segments):
            start = i * (arc_span + gap_deg) + gap_deg / 2
            p.drawArc(
                QRectF(-radius, -radius, radius * 2, radius * 2),
                int(start * 16),
                int(arc_span * 16),
            )
        p.restore()

    def _draw_radial_spokes(self, p, cx, cy, r_inner, r_outer, color, count, rotation):
        p.save()
        p.translate(cx, cy)
        p.rotate(rotation)
        p.setPen(QPen(_c(color, 80), 1.2))
        for i in range(count):
            p.save()
            p.rotate((360.0 / count) * i)
            p.drawLine(QPointF(0, -r_inner), QPointF(0, -r_outer))
            p.restore()
        p.restore()

    def _draw_corner_brackets(self, p, cx, cy, r, accent):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(accent, 180), 2.2))
        size = r * 1.8
        length = r * 0.28
        for dx, dy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
            sx = cx + dx * size
            sy = cy + dy * size
            p.drawLine(QPointF(sx, sy), QPointF(sx - dx * length, sy))
            p.drawLine(QPointF(sx, sy), QPointF(sx, sy - dy * length))

    def _draw_nodes_3d(self, p, cx, cy, r, primary, accent):
        p.setBrush(Qt.BrushStyle.NoBrush)
        for node in self._nodes_3d:
            x3d = math.sin(node["theta"]) * math.cos(node["phi"])
            y3d = math.sin(node["theta"]) * math.sin(node["phi"])
            z3d = math.cos(node["theta"])
            scale = (z3d + 1.5) / 2.5
            nx = cx + x3d * r * 1.25
            ny = cy + y3d * r * 1.25

            for other in self._nodes_3d:
                if node is other:
                    continue
                ox3d = math.sin(other["theta"]) * math.cos(other["phi"])
                oy3d = math.sin(other["theta"]) * math.sin(other["phi"])
                if math.hypot(x3d - ox3d, y3d - oy3d) < 0.38:
                    ox = cx + ox3d * r * 1.25
                    oy = cy + oy3d * r * 1.25
                    p.setPen(QPen(_c(primary, 40 * scale), 1.0))
                    p.drawLine(QPointF(nx, ny), QPointF(ox, oy))

            p.setPen(QPen(_c(accent, 255 * scale), 1.5))
            p.setBrush(QBrush(_c(accent, 200 * scale)))
            p.drawEllipse(QPointF(nx, ny), node["size"] * scale, node["size"] * scale)

    def _draw_arc_reactor(self, p, cx, cy, r, primary, accent, energy, arc_deg):
        core_r = r * 0.30 * (1.0 + 0.18 * energy)

        # Glow: branco-quente no centro → ciano → transparente
        grad = QRadialGradient(cx, cy, core_r)
        grad.setColorAt(0.00, _c(IRON_WHITE, 255))
        grad.setColorAt(0.20, _c(accent, 255))
        grad.setColorAt(0.55, _c(primary, 180 + 75 * energy))
        grad.setColorAt(0.85, _c(primary, 50))
        grad.setColorAt(1.00, _c(primary, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        # Anel de contenção com 4 gaps (N/E/S/W)
        p.save()
        p.translate(cx, cy)
        p.rotate(arc_deg)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(accent, 240), 2.0))
        inner_r = core_r * 0.85
        for i in range(4):
            start = i * 90 + 12
            p.drawArc(
                QRectF(-inner_r, -inner_r, inner_r * 2, inner_r * 2),
                int(start * 16),
                int(66 * 16),
            )
        for i in range(4):
            p.save()
            p.rotate(i * 90)
            p.setPen(QPen(_c(IRON_WHITE, 200), 1.5))
            p.drawLine(QPointF(0, -core_r * 0.55), QPointF(0, -core_r * 0.92))
            p.restore()
        p.restore()

        # Anel externo do reactor (contra-rotação)
        p.save()
        p.translate(cx, cy)
        p.rotate(-arc_deg * 1.5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_c(primary, 160), 1.2))
        outer_r = core_r * 1.15
        p.drawArc(
            QRectF(-outer_r, -outer_r, outer_r * 2, outer_r * 2), 20 * 16, 130 * 16
        )
        p.drawArc(
            QRectF(-outer_r, -outer_r, outer_r * 2, outer_r * 2), 200 * 16, 130 * 16
        )
        p.restore()

    def _draw_data_streams(self, p, cx, cy, r, primary, energy):
        p.setFont(QFont("Courier", int(max(7, r * 0.055)), QFont.Weight.Bold))
        p.setPen(QPen(_c(primary, 170 + 75 * energy), 1.0))
        for s in self._data_streams:
            p.drawText(
                QPointF(cx + s["x"] * r * 1.55, cy + s["y"] * r * 1.55), s["text"]
            )

    def _draw_scanlines(self, p, cx, cy, r, accent, energy):
        # Principal — desce
        y1 = cy + self._scan_y * r * 1.2
        p.setPen(QPen(_c(accent, 100 + 100 * energy), 1.5))
        p.drawLine(QPointF(cx - r * 1.2, y1), QPointF(cx + r * 1.2, y1))
        p.setPen(QPen(_c(accent, 30), 4.0))
        p.drawLine(QPointF(cx - r * 1.2, y1), QPointF(cx + r * 1.2, y1))
        # Secundária — sobe
        y2 = cy - self._scan_y * r * 1.0
        p.setPen(QPen(_c(accent, 40 + 40 * energy), 1.0))
        p.drawLine(QPointF(cx - r * 1.15, y2), QPointF(cx + r * 1.15, y2))

    def _draw_labels(self, p, cx, cy, r, primary, accent, energy):
        # Nome no topo
        p.setFont(QFont("Courier", int(max(8, r * 0.09)), QFont.Weight.Bold))
        p.setPen(QPen(_c(accent, 200 + 55 * energy), 1.0))
        name = self.assistant_name
        nw = p.fontMetrics().horizontalAdvance(name)
        p.drawText(QPointF(cx - nw / 2, cy - r * 1.5), name)

        # Timestamp embaixo
        p.setFont(QFont("Courier", int(max(7, r * 0.06)), QFont.Weight.Normal))
        p.setPen(QPen(_c(primary, 150 + 50 * energy), 1.0))
        t = self._label_time_str or "--:--:--"
        tw = p.fontMetrics().horizontalAdvance(t)
        p.drawText(QPointF(cx - tw / 2, cy + r * 1.55), t)

        # Estado à direita
        state = self._label_state_str or "IDLE"
        p.setPen(QPen(_c(accent, 140 + 60 * energy), 1.0))
        p.drawText(QPointF(cx + r * 1.30, cy + r * 0.05), state)

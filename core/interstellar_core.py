"""core/interstellar_core.py

Núcleo interestelar do HUD: um buraco negro com disco de acreção inclinado,
anel de fótons duplo, jatos relativísticos, esfera ergosférica, filamentos de
energia, fluxo de partículas em queda espiral e uma camada discreta de
geometria sagrada que dá à entidade o caráter de consciência computacional.

Tudo desenhado com QPainter — sem GPU, sem dependências novas. A classe
mantém apenas o seu próprio estado de animação (tempo, partículas, ondas)
e expõe `step()` / `paint()`, do mesmo jeito que o HoloAvatar.
"""

from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)


class InterstellarCore:
    _N_PARTICLES = 150
    _N_FILAMENTS = 16
    _N_STARS = 80

    def __init__(self) -> None:
        self._t = 0.0
        self._absorb = 0.0
        self._last_state = None
        self._waves: list[float] = []  # timestamps de ondas de energia activas
        self._flares: list[float] = []  # flares de voz

        rng = random.Random(0x5EED)

        self._particles = [
            {
                "r": rng.uniform(0.40, 1.02),
                "a": rng.uniform(0.0, math.tau),
                "v": rng.uniform(0.30, 1.0),
                "s": rng.uniform(0.5, 1.9),
                "tail": rng.random() < 0.55,  # ~metade das partículas com cauda
            }
            for _ in range(self._N_PARTICLES)
        ]

        self._filaments = [
            {
                "a": rng.uniform(0.0, math.tau),
                "len": rng.uniform(0.55, 0.95),
                "w": rng.uniform(0.6, 1.6),
                "ph": rng.uniform(0.0, math.tau),
            }
            for _ in range(self._N_FILAMENTS)
        ]

        self._stars = [
            {
                "x": rng.uniform(-1.05, 1.05),
                "y": rng.uniform(-1.05, 1.05),
                "b": rng.uniform(0.2, 1.0),
                "ph": rng.uniform(0.0, math.tau),
            }
            for _ in range(self._N_STARS)
        ]

    # ── animação ──────────────────────────────────────────────────────────
    def step(
        self, dt: float, amp: float, speaking: bool, muted: bool, state: str
    ) -> None:
        dt = min(0.10, max(0.0, float(dt)))

        rate = 1.0
        if state in ("THINKING", "PROCESSING"):
            rate = 2.2
        elif speaking:
            rate = 1.5
        elif state == "LISTENING":
            rate = 1.2
        if muted:
            rate *= 0.5

        self._t += dt * rate

        # Pulso de absorção ao entrar em estado ativo
        if state != self._last_state:
            if state in ("THINKING", "PROCESSING") or speaking:
                self._absorb = min(1.0, self._absorb + 0.85)
            self._last_state = state
        self._absorb *= max(0.85, 1.0 - dt * 1.9)

        # Ondas de energia (expansão concêntrica) quando activo
        active = state in ("THINKING", "PROCESSING") or speaking
        if active and not muted and random.random() < dt * 2.0:
            self._waves.append(self._t)
        if self._waves:
            self._waves = [w for w in self._waves if self._t - w < 1.8]

        # Flares de voz (beams simétricos)
        if speaking and not muted and amp > 0.05 and random.random() < dt * 6.0:
            self._flares.append(self._t)
        if self._flares:
            self._flares = [f for f in self._flares if self._t - f < 0.6]

        # Partículas — queda espiral kepleriana
        pulse = (1.0 + 0.7 * amp) if speaking else 1.0
        for p in self._particles:
            p["r"] -= dt * 0.05 * (0.35 + p["v"]) * rate * pulse
            if p["r"] < 0.14:
                p["r"] = 1.02 + random.uniform(0.0, 0.10)
                p["a"] = random.uniform(0.0, math.tau)
            p["a"] += dt * 0.32 * p["v"] * rate / (0.32 + p["r"])

    # ── pintura ───────────────────────────────────────────────────────────
    def paint(
        self,
        p: QPainter,
        cx: float,
        cy: float,
        r: float,
        main: QColor,
        acc: QColor,
        bg: QColor,
        amp: float,
        speaking: bool,
        muted: bool,
        state: str,
        name: str = "",
        W: float = 0.0,
        H: float = 0.0,
    ) -> None:
        t = self._t
        live = (speaking or amp > 0.05) and not muted
        rate_mult = 1.0
        if state in ("THINKING", "PROCESSING"):
            rate_mult = 1.8
        elif speaking:
            rate_mult = 1.45
        elif state == "LISTENING":
            rate_mult = 1.2
        if muted:
            rate_mult *= 0.55

        # Paleta
        if muted:
            hot = main.lighter(160)
            warm = main
            cool = main.darker(140)
            outer = main.darker(120)
            boost = 0.55
        else:
            hot = QColor("#eafcff")
            warm = main
            cool = QColor("#9b5cff")
            outer = (
                acc
                if (speaking or state in ("THINKING", "PROCESSING", "LISTENING"))
                else cool
            )
            boost = 1.0

        def blend(col: QColor, a: float) -> QColor:
            k = max(0.0, min(1.0, a * boost))
            return QColor(
                int(bg.red() + (col.red() - bg.red()) * k),
                int(bg.green() + (col.green() - bg.green()) * k),
                int(bg.blue() + (col.blue() - bg.blue()) * k),
            )

        lift = 1.0 + 0.55 * amp + (0.20 if speaking else 0.0)

        r_out = r * 1.00
        r_in = r * 0.38
        aspect = 0.30
        r_hor = r * 0.34

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(Qt.PenStyle.NoPen)

        # ── 1. Estrelas distantes (com lente gravitacional subtil) ────────
        star_col = QColor("#d8f8ff") if not muted else warm
        for s in self._stars:
            sx0, sy0 = s["x"], s["y"]
            dist = math.hypot(sx0, sy0)
            if dist < 0.55:
                # Empurra radialmente — anel de Einstein.
                factor = 1.0 + 0.35 * (0.55 - dist) / 0.55
                sx0 *= factor
                sy0 *= factor
            tw = 0.5 + 0.5 * math.sin(t * 1.6 + s["ph"])
            a = 0.10 + 0.45 * s["b"] * tw
            p.setPen(QPen(blend(star_col, a), 1))
            p.drawPoint(QPointF(cx + sx0 * r * 1.05, cy + sy0 * r * 1.05))
        p.setPen(Qt.PenStyle.NoPen)

        # ── 2. Data streams — pacotes radiais de informação ───────────────
        for i in range(6):
            ang = i * (math.tau / 6) + t * 0.4 * rate_mult
            ca, sa = math.cos(ang), math.sin(ang)
            for seg in range(3):
                phase = (t * 0.8 + i * 0.31 + seg * 0.37) % 1.0
                r0 = r * (0.52 + phase * 0.60)
                r1 = r * (0.52 + phase * 0.60 + 0.05)
                if r1 > r * 1.10:
                    continue
                a = 0.55 * (1.0 - phase) * 0.6
                p.setPen(QPen(blend(acc if live else warm, a), max(1.0, r * 0.006)))
                p.drawLine(
                    QPointF(cx + ca * r0, cy + sa * r0),
                    QPointF(cx + ca * r1, cy + sa * r1),
                )
        p.setPen(Qt.PenStyle.NoPen)

        # ── 3. Coroa exterior — corpo de luz ambiente ────────────────────
        for gr, a0 in ((r * 1.18, 0.11), (r * 0.80, 0.22), (r * 0.48, 0.30)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(warm, min(0.95, a0 * lift)))
            g.setColorAt(0.40, blend(warm, min(0.95, a0 * lift * 0.55)))
            g.setColorAt(0.75, blend(outer, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(outer, 0.0))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 4. Lóbulos de coroa (elipses rotacionadas — orgânico) ────────
        for elong, scale_y, ang, a0, tint in (
            (1.55, 0.85, t * 4.0 * rate_mult, 0.16, warm),
            (1.25, 0.75, -t * 3.0 * rate_mult, 0.13, acc if not muted else warm),
        ):
            p.save()
            p.translate(cx, cy)
            p.rotate(math.degrees(ang) % 360.0)
            p.scale(elong, scale_y)
            g = QRadialGradient(0, 0, r * 0.85)
            g.setColorAt(0.0, blend(tint, a0 * lift))
            g.setColorAt(0.5, blend(tint, a0 * lift * 0.45))
            g.setColorAt(1.0, blend(tint, 0.0))
            p.setBrush(QBrush(g))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(0, 0), r * 0.85, r * 0.85)
            p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 5. Jatos relativísticos (pólos) ───────────────────────────────
        for direction in (-1.0, 1.0):
            jet_len = r * (1.35 + 0.10 * (amp if speaking else 0.0))
            g = QLinearGradient(
                cx, cy + direction * r_hor * 1.05, cx, cy + direction * jet_len
            )
            g.setColorAt(0.00, blend(hot, 0.0))
            g.setColorAt(0.10, blend(hot, 0.28 * lift))
            g.setColorAt(0.45, blend(warm, 0.16 * lift))
            g.setColorAt(1.00, blend(outer, 0.0))
            w0, w1 = r * 0.025, r * 0.075
            path = QPainterPath()
            path.moveTo(cx - w0, cy + direction * r_hor * 1.05)
            path.lineTo(cx + w0, cy + direction * r_hor * 1.05)
            path.lineTo(cx + w1, cy + direction * jet_len)
            path.lineTo(cx - w1, cy + direction * jet_len)
            path.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── Disco de acreção: definições ─────────────────────────────────
        def _disk_gradient() -> QRadialGradient:
            g = QRadialGradient(0.0, 0.0, r_out)
            inner = max(0.01, r_in / r_out)
            g.setColorAt(0.00, QColor(0, 0, 0, 0))
            g.setColorAt(inner * 0.95, QColor(0, 0, 0, 0))
            g.setColorAt(inner, blend(hot, 0.92 * lift))
            g.setColorAt(inner + 0.06, blend(warm, 0.72 * lift))
            g.setColorAt(0.65, blend(warm, 0.46 * lift))
            g.setColorAt(0.85, blend(cool, 0.26 * lift))
            g.setColorAt(0.97, blend(cool, 0.08 * lift))
            g.setColorAt(1.00, blend(cool, 0.0))
            return g

        def _draw_disk_body() -> None:
            # Base
            p.setBrush(QBrush(_disk_gradient()))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(0, 0), r_out, r_out)

            # Doppler — lado que se aproxima fica mais brilhante
            g_doppler = QLinearGradient(-r_out, 0, r_out, 0)
            g_doppler.setColorAt(0.00, QColor(0, 0, 0, 0))
            g_doppler.setColorAt(0.55, QColor(0, 0, 0, 0))
            g_doppler.setColorAt(1.00, blend(hot, 0.32 * lift))
            p.setBrush(QBrush(g_doppler))
            p.drawEllipse(QPointF(0, 0), r_out, r_out)

            # Espirais de turbulência (ondas de densidade)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for k in range(2):
                path = QPainterPath()
                steps = 20
                ang0 = t * 0.6 * rate_mult + k * math.pi
                for i in range(steps + 1):
                    frac = i / steps
                    rad = r_in * 1.05 + (r_out * 0.95 - r_in * 1.05) * frac
                    ang = ang0 + frac * math.pi * 1.4
                    x = math.cos(ang) * rad
                    y = math.sin(ang) * rad
                    if i == 0:
                        path.moveTo(x, y)
                    else:
                        path.lineTo(x, y)
                p.setPen(QPen(blend(hot, 0.35 * lift), max(1.0, r * 0.010)))
                p.drawPath(path)

            # Grumos brilhantes orbitando
            n_clumps = 4
            for k in range(n_clumps):
                ang = t * 0.5 * rate_mult + k * (math.tau / n_clumps)
                rad = r * (0.55 + 0.15 * math.sin(t * 1.2 + k * 2.0))
                x = math.cos(ang) * rad
                y = math.sin(ang) * rad
                p.setBrush(QBrush(blend(hot, 0.55 * lift)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(x, y), r * 0.018, r * 0.018)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 6. Metade posterior do disco ─────────────────────────────────
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, aspect)
        _draw_disk_body()
        p.restore()

        # ── 7. Esfera ergosférica (frame dragging) ───────────────────────
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, 0.85)
        erg_r = r_hor * 1.42
        g_erg = QRadialGradient(0, 0, erg_r)
        g_erg.setColorAt(0.00, QColor(0, 0, 0, 0))
        g_erg.setColorAt(0.70, QColor(0, 0, 0, 0))
        g_erg.setColorAt(0.92, blend(warm, 0.16 * lift))
        g_erg.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.setBrush(QBrush(g_erg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(0, 0), erg_r, erg_r)
        p.restore()

        # ── 8. Horizonte de eventos ──────────────────────────────────────
        g_hor = QRadialGradient(cx, cy, r_hor)
        g_hor.setColorAt(0.00, QColor("#000000"))
        g_hor.setColorAt(0.82, QColor("#000205"))
        g_hor.setColorAt(0.95, blend(QColor("#000000"), 0.40))
        g_hor.setColorAt(1.00, QColor("#000000"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g_hor))
        p.drawEllipse(QRectF(cx - r_hor, cy - r_hor, r_hor * 2, r_hor * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 9. Coroa interna — halo brilhante colado ao horizonte ────────
        gr_inner = r_hor * 1.32
        g_inner = QRadialGradient(cx, cy, gr_inner)
        inner_frac = r_hor / gr_inner
        g_inner.setColorAt(0.00, QColor(0, 0, 0, 0))
        g_inner.setColorAt(inner_frac * 0.92, QColor(0, 0, 0, 0))
        g_inner.setColorAt(inner_frac, blend(hot, 0.55 * lift))
        g_inner.setColorAt(inner_frac + 0.08, blend(hot, 0.22 * lift))
        g_inner.setColorAt(1.00, QColor(0, 0, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g_inner))
        p.drawEllipse(QRectF(cx - gr_inner, cy - gr_inner, gr_inner * 2, gr_inner * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 10. Anel de fótons primário ──────────────────────────────────
        pr = r_hor * 1.08
        p.setPen(QPen(blend(hot, 0.90 * lift), max(1.4, r * 0.014)))
        p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # ── 11. Anel de fótons secundário (mais fino e mais externo) ─────
        pr2 = r_hor * 1.18
        p.setPen(QPen(blend(warm, 0.45 * lift), max(1.0, r * 0.007)))
        p.drawEllipse(QRectF(cx - pr2, cy - pr2, pr2 * 2, pr2 * 2))

        # ── 12. Arco lenteado (lado distante do disco, dobrado sobre) ────
        arc_r = r_hor * 1.28
        p.setPen(QPen(blend(hot, 0.78 * lift), max(1.6, r * 0.018)))
        p.drawArc(
            QRectF(cx - arc_r, cy - arc_r, arc_r * 2, arc_r * 2),
            int(20 * 16),
            int(140 * 16),
        )
        arc_r2 = r_hor * 1.33
        p.setPen(QPen(blend(hot, 0.38 * lift), max(1.0, r * 0.008)))
        p.drawArc(
            QRectF(cx - arc_r2, cy - arc_r2, arc_r2 * 2, arc_r2 * 2),
            int(35 * 16),
            int(110 * 16),
        )

        # ── 13. Metade frontal do disco (passa à frente do horizonte) ────
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, aspect)
        p.setClipRect(QRectF(-r * 1.25, 0.0, r * 2.5, r * 1.25 / aspect))
        _draw_disk_body()
        p.restore()

        # ── 14. Hotspot Doppler (acento extra no lado que aproxima) ──────
        p.save()
        p.translate(cx, cy)
        p.scale(1.0, aspect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(blend(hot, 0.65 * lift), max(1.4, r * 0.014)))
        p.drawArc(
            QRectF(-r_out * 1.01, -r_out * 1.01, r_out * 2.02, r_out * 2.02),
            int(-55 * 16),
            int(70 * 16),
        )
        p.restore()

        # ── 15. Redemoinhos de acreção fora do disco ─────────────────────
        for k in range(3):
            sw_r = r * (1.06 + k * 0.05)
            ang0 = t * (12 + k * 6) * rate_mult * (1 if k % 2 == 0 else -1)
            p.setPen(
                QPen(
                    blend(acc if k == 0 and live else warm, (0.34 - k * 0.08) * lift),
                    max(1.0, r * (0.008 - k * 0.0015)),
                )
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(
                QRectF(cx - sw_r, cy - sw_r, sw_r * 2, sw_r * 2),
                int((ang0 % 360) * 16),
                int(45 * 16),
            )

        # ── 16. Ondas de energia (anéis concêntricos em expansão) ────────
        for w_start in self._waves:
            age = self._t - w_start
            frac = age / 1.8
            if frac >= 1.0:
                continue
            rad = r_hor + (r * 1.10 - r_hor) * frac
            a = (1.0 - frac) * 0.55
            p.setPen(QPen(blend(hot, a * lift), max(1.0, r * 0.006)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # ── 17. Anéis geométricos girando (identidade de IA) ─────────────
        for k, (rr, span, count, dirn, col, a, wid) in enumerate(
            (
                (0.96, 90, 2, +1, acc, 0.60, max(1.2, r * 0.010)),
                (0.86, 60, 3, -1, warm, 0.34, max(1.0, r * 0.008)),
                (0.72, 120, 1, +1, acc, 0.40, max(1.2, r * 0.010)),
            )
        ):
            rad = r * rr
            p.setPen(QPen(blend(col, a * lift), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate_mult * (14 + k * 8) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16), int(span * 16))

        # ── 18. Geometria sagrada — hexágonos concêntricos ───────────────
        p.setBrush(Qt.BrushStyle.NoBrush)
        for hex_r, alpha, rot_speed in (
            (0.55, 0.10, 8),
            (0.72, 0.08, -6),
            (0.88, 0.06, 4),
        ):
            hex_rad = r * hex_r
            rot = t * rot_speed * rate_mult
            path = QPainterPath()
            for i in range(6):
                ang = rot + i * math.pi / 3
                x = cx + math.cos(ang) * hex_rad
                y = cy + math.sin(ang) * hex_rad
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
            p.setPen(QPen(blend(acc, alpha * lift), 1))
            p.drawPath(path)
            for i in range(6):
                ang = rot + i * math.pi / 3
                x = cx + math.cos(ang) * hex_rad
                y = cy + math.sin(ang) * hex_rad
                p.setBrush(QBrush(blend(acc, alpha * 1.8 * lift)))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(x, y), 1.4, 1.4)
            p.setBrush(Qt.BrushStyle.NoBrush)

        # ── 19. Filamentos de energia ────────────────────────────────────
        for f in self._filaments:
            base_ang = f["a"] + t * 0.25 * rate_mult
            flick = 0.5 + 0.5 * math.sin(t * 3.0 + f["ph"])
            reach = r * (0.42 + f["len"] * 0.55 * (0.85 + 0.25 * flick))
            if live:
                reach *= 1.0 + 0.15 * amp * flick
            inner_r = r_hor * 1.10
            path = QPainterPath()
            x0 = cx + math.cos(base_ang) * inner_r
            y0 = cy + math.sin(base_ang) * inner_r
            path.moveTo(x0, y0)
            perp = base_ang + math.pi / 2
            for step in range(1, 5):
                k = step / 4.0
                wob = math.sin(t * 4.0 + f["ph"] + step * 1.3) * r * 0.02
                dist = inner_r + (reach - inner_r) * k
                x = cx + math.cos(base_ang) * dist + math.cos(perp) * wob
                y = cy + math.sin(base_ang) * dist + math.sin(perp) * wob
                path.lineTo(x, y)
            alpha = 0.20 + 0.55 * f["w"] * (0.6 + 0.4 * flick)
            if live:
                alpha *= 1.0 + 0.5 * amp
            p.setPen(
                QPen(
                    blend(acc if live else warm, min(0.9, alpha)),
                    max(1.0, f["w"] * r * 0.006),
                )
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        # ── 20. Partículas com cauda ─────────────────────────────────────
        for pt in self._particles:
            rr = pt["r"] * r
            a_rad = pt["a"]
            px = cx + math.cos(a_rad) * rr
            py = cy + math.sin(a_rad) * rr * 0.90
            near = max(0.0, 1.0 - (pt["r"] - 0.14) / 0.90)
            alpha = 0.15 + 0.80 * near * near
            size = pt["s"] * (0.7 + near * 1.4) * max(1.0, r * 0.004)
            col = hot if near > 0.6 else warm

            # Cauda (oposta à direcção do movimento angular)
            if pt["tail"] and near > 0.35:
                da = 0.14 + pt["v"] * 0.06
                dr = 0.04 + pt["v"] * 0.02
                ta = a_rad - da
                tr = rr + dr * r
                tx = cx + math.cos(ta) * tr
                ty = cy + math.sin(ta) * tr * 0.90
                p.setPen(QPen(blend(col, alpha * 0.55 * lift), max(0.8, size * 0.55)))
                p.drawLine(QPointF(px, py), QPointF(tx, ty))

            # Corpo
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(blend(col, alpha * lift)))
            p.drawEllipse(QPointF(px, py), size, size)

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(Qt.PenStyle.NoPen)

        # ── 21. Pulso de absorção (reacção a comando) ────────────────────
        if self._absorb > 0.02:
            frac = 1.0 - self._absorb
            rr = r * (0.36 + frac * 0.55)
            p.setPen(QPen(blend(hot, self._absorb * 0.85), max(1.5, r * 0.012)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))
            rr2 = rr * 0.82
            p.setPen(QPen(blend(hot, self._absorb * 0.45), max(1.0, r * 0.008)))
            p.drawEllipse(QRectF(cx - rr2, cy - rr2, rr2 * 2, rr2 * 2))

        # ── 22. Flares de voz ────────────────────────────────────────────
        if self._flares:
            for f_start in self._flares:
                age = self._t - f_start
                if age >= 0.6:
                    continue
                frac = age / 0.6
                a = (1.0 - frac) * amp * 0.7
                n = 8
                for i in range(n):
                    ang = i * (math.tau / n) + t * 0.6
                    r0 = r * (0.70 + frac * 0.15)
                    r1 = r * (0.78 + frac * 0.35)
                    p.setPen(QPen(blend(hot, a), max(1.0, r * 0.008)))
                    p.drawLine(
                        QPointF(cx + math.cos(ang) * r0, cy + math.sin(ang) * r0),
                        QPointF(cx + math.cos(ang) * r1, cy + math.sin(ang) * r1),
                    )

        # ── 23. Ondulações concêntricas (voz) ────────────────────────────
        if speaking and not muted and amp > 0.04:
            for k in range(2):
                phase = (t * 1.6 + k * 0.5) % 1.0
                rad = r * (0.55 + phase * 0.85)
                a = (1.0 - phase) * amp * 0.45
                p.setPen(QPen(blend(acc, a), max(1.0, r * 0.006)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # ── 24. Moldura (marcas de canto e mira) ─────────────────────────
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(warm, 0.42), 1.2))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QPointF(x, y), QPointF(x - sx * arm, y))
                p.drawLine(QPointF(x, y), QPointF(x, y - sy * arm))

            p.setPen(QPen(blend(warm, 0.14), 1))
            gap = r * 0.90
            if W > 40:
                p.drawLine(QPointF(cx - W / 2, cy), QPointF(cx - gap, cy))
                p.drawLine(QPointF(cx + gap, cy), QPointF(cx + W / 2, cy))
            if H > 40:
                p.drawLine(QPointF(cx, cy - H / 2), QPointF(cx, cy - gap))
                p.drawLine(QPointF(cx, cy + gap), QPointF(cx, cy + H / 2))

        # ── 25. Indicador de estado rotativo em torno do nome ────────────
        ring_r = r_hor * 0.88
        arc_start = (t * 55 * rate_mult) % 360.0
        p.setPen(QPen(blend(acc, 0.55), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(
            QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2),
            int(arc_start * 16),
            int(50 * 16),
        )

        # ── 26. Nome no centro do horizonte ──────────────────────────────
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(
                8, int(min(r * 0.095, (r_hor * 1.7) / max(1, len(name)) * 1.6 - space))
            )
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            txt_col = QColor("#ffffff") if not muted else main.lighter(150)
            a = 0.68 + 0.32 * min(1.0, amp * 2)

            # Halo escuro por baixo (garante contraste no horizonte preto)
            p.setPen(QPen(blend(QColor("#000000"), 0.9), 2))
            p.drawText(
                QRectF(cx - r + 1, cy - fsz + 1, r * 2, fsz * 2),
                Qt.AlignmentFlag.AlignCenter,
                name,
            )

            # Texto com pulsação da voz
            p.setPen(QPen(blend(txt_col, a), 1))
            p.drawText(
                QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                Qt.AlignmentFlag.AlignCenter,
                name,
            )

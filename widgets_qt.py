"""
widgets_qt.py – Custom QPainter-based widgets for Sound2Text PyQt6 UI.
No customtkinter / tkinter imports.
"""
from __future__ import annotations

import time
from collections import deque

from PyQt6.QtCore import QTimer, Qt, QRect, QPoint, QPointF, pyqtSignal
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QFont, QLinearGradient, QPen,
)
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy, QFrame

from i18n import _LANG

# ── Seven-segment character map (bit mask: a=64 b=32 c=16 d=8 e=4 f=2 g=1) ────
_SEG7 = {
    "0": 0b1111110, "1": 0b0110000, "2": 0b1101101,
    "3": 0b1111001, "4": 0b0110011, "5": 0b1011011,
    "6": 0b1011111, "7": 0b1110000, "8": 0b1111111,
    "9": 0b1111011,
}


def _dim_color(hex_color: str, factor: int = 9) -> QColor:
    """Return a very dim QColor version of hex_color (for ghost segments)."""
    r = max(int(hex_color[1:3], 16) // factor, 8)
    g = max(int(hex_color[3:5], 16) // factor, 6)
    b = max(int(hex_color[5:7], 16) // factor, 6)
    return QColor(r, g, b)


# ── VUBarMeterWidget ──────────────────────────────────────────────────────────

class VUBarMeterWidget(QWidget):
    """
    Design-B style VU meter: two columns of vertical bars stacked bottom-to-top.

    All 10 segments per column are always visible with fixed colors:
    - Green #3ddc84 (segments 0-5)
    - Yellow #ffb454 (segments 6-7)
    - Red #ff5a52 (segments 8-9)

    Overall opacity (brightness) varies with mic level: 0.18 (silent) → 1.0 (loud).
    Segment 6 (first yellow) blinks when active.

    Emits clicked() when the widget is clicked (PTT stop trigger).
    """
    clicked = pyqtSignal()
    FPS = 33
    SEGMENTS = 10  # segments per column

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(74)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._level = 0.0
        self._smoothed = 0.0
        self._blink_phase = 0  # for segment 6 blink animation

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.FPS)

    def set_level(self, v: float) -> None:
        """Set mic level in range [0.0, 1.0]."""
        self._level = max(0.0, min(float(v), 1.0))

    def _tick(self) -> None:
        """Smooth the level, update blink phase, and refresh display."""
        k = 0.55 if self._level > self._smoothed else 0.20
        self._smoothed += (self._level - self._smoothed) * k
        self._blink_phase = (self._blink_phase + 1) % 60  # ~1s blink cycle at 33 FPS
        self.update()

    def paintEvent(self, _event) -> None:
        """Draw two columns of vertical bars with dynamic opacity based on level."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()

        # Bar dimensions
        seg_height = 7
        seg_gap = 3
        col_gap = 12
        label_width = 24

        # Padding
        h_pad = 8
        v_pad = 8

        # Available width for two columns
        avail_width = w - h_pad * 2 - label_width
        col_width = (avail_width - col_gap) // 2
        bar_width = max(4, col_width - 4)

        # Starting positions
        col1_x = h_pad + (col_width - bar_width) // 2
        col2_x = col1_x + col_width + col_gap
        label_x = w - label_width + 2

        # Compute overall opacity based on level: 0.18 (silent) → 1.0 (loud)
        opacity = 0.18 + self._smoothed * (1.0 - 0.18)
        overall_alpha = int(255 * opacity)

        # Determine if segment 6 should blink (when volume > 0)
        blink_on = (self._blink_phase < 30) if self._smoothed > 0.05 else True

        # Draw two columns with fixed colors
        for col in range(2):
            col_x = col1_x if col == 0 else col2_x

            for seg in range(self.SEGMENTS):
                y = h - v_pad - (seg + 1) * (seg_height + seg_gap)

                # Fixed color mapping
                if seg < 6:  # Green zone
                    color = QColor("#3ddc84")
                elif seg < 8:  # Yellow zone
                    color = QColor("#ffb454")
                else:  # Red zone
                    color = QColor("#ff5a52")

                # Apply overall brightness + special blink for segment 6
                if seg == 6 and not blink_on:
                    # Blink out: darker for this segment
                    color.setAlpha(int(overall_alpha * 0.3))
                else:
                    color.setAlpha(overall_alpha)

                painter.fillRect(col_x, y, bar_width, seg_height, color)

        # Draw "MIC LEVEL" label vertically on the right
        painter.save()
        painter.translate(label_x, h // 2)
        painter.rotate(-90)
        painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        label_color = QColor("#5b6675")
        label_color.setAlpha(overall_alpha)
        painter.setPen(label_color)
        # Center the rotated label on the panel's vertical axis regardless of font.
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance("MIC LEVEL")
        painter.drawText(-tw // 2, fm.ascent() // 2, "MIC LEVEL")
        painter.restore()

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ── VUMeterWidget ─────────────────────────────────────────────────────────────

class VUMeterWidget(QWidget):
    """
    Pill-shaped, scrolling **waveform-envelope** mic meter.

    Each frame the current (smoothed) level is pushed into a ring buffer; the
    buffer is drawn as a symmetric filled envelope that scrolls right→left, the
    newest sample at the right edge (like a podcast / recorder app). The fill
    tint reacts to the live level (cyan → green → amber → red) and the leading
    edge carries a soft glow. Emits clicked() — used as the PTT-stop trigger.
    """
    clicked = pyqtSignal()
    FPS     = 33
    HISTORY = 400   # ring-buffer length (≥ widest pixel width we expect)

    # Level → base RGB ramp (quiet cyan → loud red). Boundaries are normalized
    # level, not bar position, so the whole envelope shifts hue with volume.
    _RAMP = [
        (0.0,  ( 41, 182, 246)),   # cyan
        (0.45, ( 80, 210, 190)),   # teal
        (0.65, (102, 187, 106)),   # green
        (0.82, (255, 193,  60)),   # amber
        (1.01, (248,  81,  73)),   # red
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._level    = 0.0
        self._smoothed = 0.0
        self._peak     = 0.0
        self._active   = False
        self._hist: deque[float] = deque([0.0] * self.HISTORY, maxlen=self.HISTORY)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.FPS)

    def set_level(self, v: float) -> None:
        self._level = max(0.0, min(float(v), 1.0))

    def show(self) -> None:  # type: ignore[override]
        self._active = True
        self.setVisible(True)

    def hide(self) -> None:  # type: ignore[override]
        self._active = False
        self._level = 0.0
        self.setVisible(False)

    def _tick(self) -> None:
        target = self._level if self._active else 0.0
        # Asymmetric smoothing: snap up fast, fall slowly — reads like a real meter.
        k = 0.55 if target > self._smoothed else 0.20
        self._smoothed += (target - self._smoothed) * k
        self._hist.append(self._smoothed)
        # Peak indicator decays gently toward the current level.
        self._peak = max(self._smoothed, self._peak - 0.012)
        self.update()

    @classmethod
    def _ramp_color(cls, v: float) -> tuple[int, int, int]:
        """Interpolate the RGB ramp at level v ∈ [0, 1]."""
        v = max(0.0, min(v, 1.0))
        ramp = cls._RAMP
        for i in range(1, len(ramp)):
            b0, c0 = ramp[i - 1]
            b1, c1 = ramp[i]
            if v < b1:
                t = 0.0 if b1 == b0 else (v - b0) / (b1 - b0)
                return tuple(int(c0[j] + (c1[j] - c0[j]) * t) for j in range(3))
        return ramp[-1][1]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        cy = h / 2.0
        corner = 7.0   # squarer corners to match the rectangular VU panel

        # ── Rounded-rect clip ─────────────────────────────────────
        clip = QPainterPath()
        clip.addRoundedRect(0.0, 0.0, float(w), float(h), corner, corner)
        p.setClipPath(clip)

        # ── Background — subtle vertical depth ────────────────────
        bg = QLinearGradient(0.0, 0.0, 0.0, float(h))
        bg.setColorAt(0.0, QColor("#0b0f15"))
        bg.setColorAt(0.5, QColor("#0d1320"))
        bg.setColorAt(1.0, QColor("#0b0f15"))
        p.fillRect(0, 0, w, h, bg)

        # ── Sample window: newest sample at the right edge ────────
        pad   = max(6, int(h * 0.18))
        avail = max(2, w - 2 * pad)
        cols  = min(avail, len(self._hist))
        hist  = list(self._hist)[-cols:]
        amp   = h * 0.40
        floor = 0.035   # so silence still draws a faint baseline thread

        def x_at(i: int) -> float:
            return pad + (i / max(1, cols - 1)) * avail

        def lvl_at(i: int) -> float:
            return max(floor, hist[i])

        # Envelope tint follows the live (newest) level.
        rc, gc, bc = self._ramp_color(self._smoothed)

        # ── Filled symmetric envelope ─────────────────────────────
        path = QPainterPath()
        path.moveTo(x_at(0), cy - lvl_at(0) * amp)
        for i in range(1, cols):
            path.lineTo(x_at(i), cy - lvl_at(i) * amp)
        for i in range(cols - 1, -1, -1):
            path.lineTo(x_at(i), cy + lvl_at(i) * amp)
        path.closeSubpath()

        fill = QLinearGradient(0.0, cy - amp, 0.0, cy + amp)
        fill.setColorAt(0.0,  QColor(rc, gc, bc, 60))
        fill.setColorAt(0.45, QColor(min(rc + 40, 255), min(gc + 40, 255), min(bc + 40, 255), 210))
        fill.setColorAt(0.5,  QColor(235, 248, 255, 235))   # bright spine
        fill.setColorAt(0.55, QColor(min(rc + 40, 255), min(gc + 40, 255), min(bc + 40, 255), 210))
        fill.setColorAt(1.0,  QColor(rc, gc, bc, 60))
        p.fillPath(path, fill)

        # ── Top contour stroke for a crisp edge ───────────────────
        edge = QPainterPath()
        edge.moveTo(x_at(0), cy - lvl_at(0) * amp)
        for i in range(1, cols):
            edge.lineTo(x_at(i), cy - lvl_at(i) * amp)
        pen = QPen(QColor(min(rc + 60, 255), min(gc + 60, 255), min(bc + 60, 255), 200))
        pen.setWidthF(1.4)
        p.setPen(pen)
        p.drawPath(edge)
        # Mirror the contour on the bottom.
        edge_b = QPainterPath()
        edge_b.moveTo(x_at(0), cy + lvl_at(0) * amp)
        for i in range(1, cols):
            edge_b.lineTo(x_at(i), cy + lvl_at(i) * amp)
        p.drawPath(edge_b)

        # ── Leading-edge glow (newest sample, right side) ─────────
        lead_x = x_at(cols - 1)
        lead_v = lvl_at(cols - 1)
        glow = QColor(min(rc + 70, 255), min(gc + 70, 255), min(bc + 70, 255))
        for r, a in ((6.0, 28), (3.5, 70), (1.8, 200)):
            glow.setAlpha(a)
            p.setBrush(glow)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(lead_x, cy - lead_v * amp), r, r)
            p.drawEllipse(QPointF(lead_x, cy + lead_v * amp), r, r)

        # ── Peak-hold ticks ───────────────────────────────────────
        if self._peak > floor:
            pc = self._ramp_color(self._peak)
            pen = QPen(QColor(pc[0], pc[1], pc[2], 150))
            pen.setWidthF(1.0)
            p.setPen(pen)
            py = self._peak * amp
            p.drawLine(QPointF(pad, cy - py), QPointF(pad + avail, cy - py))
            p.drawLine(QPointF(pad, cy + py), QPointF(pad + avail, cy + py))

        p.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ── SevenSegClock ─────────────────────────────────────────────────────────────

class SevenSegClock(QWidget):
    """
    MM:SS display drawn via QPainter as authentic 7-segment LED digits.
    Dark background (#0d1117), configurable digit color.
    Ghost (inactive) segments shown very dim.
    Public: set_secs(float)
    Size: ~180×70 px
    """

    DW = 32   # digit width
    DH = 52   # digit height
    DT = 5    # segment thickness
    CW = 12   # colon width
    PX = 10   # horizontal padding
    PY = 8    # vertical padding
    GS = 2    # inter-segment gap

    def __init__(self, parent=None, on_color: str = "#29b6f6"):
        super().__init__(parent)
        # Five digit slots: three for minutes (so the colon never shifts once a
        # session passes 100 min) + two for seconds, plus the colon.
        cw = self.PX * 2 + 5 * self.DW + self.CW + 6 * self.GS + 12
        ch = self.DH + self.PY * 2
        self.setFixedSize(cw, ch)
        self._on_color  = QColor(on_color)
        self._off_color = _dim_color(on_color, 10)
        self._secs      = 0.0
        self._last_int  = -1

        # Dark background
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#0d1117"))
        self.setPalette(p)

    def set_secs(self, secs: float) -> None:
        if int(secs) != self._last_int:
            self._secs     = secs
            self._last_int = int(secs)
            self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#0d1117"))

        s       = int(self._secs)
        mm, ss  = s // 60, s % 60
        # Minutes occupy three slots; below 100 min the leading slot is a blank
        # (space → not drawn), so it reads " 02:05" rather than "002:05" yet the
        # colon position stays fixed when minutes grow to three digits.
        mm_str  = f"{mm:03d}" if mm >= 100 else f" {mm:02d}"
        chars   = f"{mm_str}{ss:02d}"
        x, y    = self.PX, self.PY

        for i, ch in enumerate(chars):
            self._draw_digit(painter, x, y, ch)
            x += self.DW + self.GS
            if i == 2:
                self._draw_colon(painter, x, y)
                x += self.CW + self.GS

        painter.end()

    def _draw_digit(self, painter: QPainter, ox: int, oy: int, ch: str) -> None:
        if ch == " ":
            return   # blank slot — draw nothing (not even ghost segments)
        mask = _SEG7.get(ch, 0)
        W, H, T, G = self.DW, self.DH, self.DT, self.GS

        def seg(bit: int, x1: int, y1: int, x2: int, y2: int) -> None:
            c = self._on_color if (mask & bit) else self._off_color
            painter.fillRect(ox + x1, oy + y1, x2 - x1, y2 - y1, c)

        seg(64, T+G,        0,          W-T-G, T)           # top
        seg(32, W-T,        T+G,        W,     H//2-G)      # top-right
        seg(16, W-T,        H//2+G,     W,     H-T-G)       # bottom-right
        seg(8,  T+G,        H-T,        W-T-G, H)           # bottom
        seg(4,  0,          H//2+G,     T,     H-T-G)       # bottom-left
        seg(2,  0,          T+G,        T,     H//2-G)      # top-left
        seg(1,  T+G,        H//2-T//2,  W-T-G, H//2+T//2)  # middle

    def _draw_colon(self, painter: QPainter, ox: int, oy: int) -> None:
        cx = ox + self.CW // 2
        r  = 3
        painter.setBrush(self._on_color)
        painter.setPen(Qt.PenStyle.NoPen)
        for yc in (oy + self.DH // 3, oy + 2 * self.DH // 3):
            painter.drawEllipse(QPoint(cx, yc), r, r)


# ── DashboardWidget ───────────────────────────────────────────────────────────

class DashboardWidget(QWidget):
    """
    Three SevenSegClock timers side by side:
      elapsed (cyan #29b6f6) / audio (green #66bb6a) / trans (amber #ffa726)
    Dark card background #0d1117.
    Public: start(), stop(), reset(), add_audio(secs), add_trans(secs)
    Elapsed clock driven internally by QTimer every second.
    """

    _TIMERS = [
        # key        zh label     ja label       en label       LED color
        ("elapsed", "录音经过",  "録音経過",    "Elapsed",      "#29b6f6"),
        ("audio",   "音频收录",  "音声収録",    "Audio Rec",    "#66bb6a"),
        ("trans",   "文字转换",  "文字起こし",  "Transcribed",  "#ffa726"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)

        self._clocks:        dict[str, SevenSegClock] = {}
        self._labels:        dict[str, QLabel]        = {}
        self._audio_secs   = 0.0
        self._trans_secs   = 0.0
        self._start_time   = 0.0
        self._frozen_elapsed = 0.0
        self._active       = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(16)

        lang = _LANG
        for key, lz, lj, le, color in self._TIMERS:
            cap = lz if lang == "zh" else (lj if lang == "ja" else le)

            # Card widget with dark background and rounded corners
            card = QWidget()
            card.setObjectName("dashCard")
            card.setStyleSheet(
                "#dashCard { background-color: #0D1117; border-radius: 12px; }"
            )

            vbox = QVBoxLayout(card)
            vbox.setContentsMargins(16, 12, 16, 14)
            vbox.setSpacing(6)

            # Label row: colored dot + caption text (above clock)
            lbl_row = QWidget()
            lbl_row.setStyleSheet("background: transparent;")
            lbl_hbox = QHBoxLayout(lbl_row)
            lbl_hbox.setContentsMargins(0, 0, 0, 0)
            lbl_hbox.setSpacing(4)

            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font-size: 10px; background: transparent;")
            lbl_hbox.addWidget(dot)

            lbl = QLabel(cap)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("color: #8A8A9A; font-size: 11px; background: transparent;")
            lbl_hbox.addWidget(lbl)
            lbl_hbox.addStretch()
            self._labels[key] = lbl

            vbox.addWidget(lbl_row)

            # Seven-segment clock
            clk = SevenSegClock(card, on_color=color)
            vbox.addWidget(clk, 0, Qt.AlignmentFlag.AlignHCenter)
            self._clocks[key] = clk

            outer.addWidget(card)

        # Internal timer for elapsed clock
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._start_time     = time.time()
        self._audio_secs     = 0.0
        self._trans_secs     = 0.0
        self._frozen_elapsed = 0.0
        self._active         = True

    def stop(self) -> None:
        if self._active and self._start_time:
            self._frozen_elapsed = time.time() - self._start_time
        self._active = False

    def reset(self) -> None:
        self._start_time     = 0.0
        self._audio_secs     = 0.0
        self._trans_secs     = 0.0
        self._frozen_elapsed = 0.0
        self._active         = False
        for clk in self._clocks.values():
            clk.set_secs(0.0)

    def add_audio(self, secs: float) -> None:
        self._audio_secs += secs

    def add_trans(self, secs: float) -> None:
        self._trans_secs += secs

    # ── Internal tick ─────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._active and self._start_time:
            elapsed = time.time() - self._start_time
        else:
            elapsed = self._frozen_elapsed

        vals = {
            "elapsed": elapsed,
            "audio":   self._audio_secs,
            "trans":   self._trans_secs,
        }
        for key, clk in self._clocks.items():
            try:
                clk.set_secs(vals[key])
            except Exception:
                pass

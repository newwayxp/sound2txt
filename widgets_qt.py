"""
widgets_qt.py – Custom QPainter-based widgets for Sound2Text PyQt6 UI.
No customtkinter / tkinter imports.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QTimer, Qt, QRect, QPoint, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QFont, QLinearGradient
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSizePolicy

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


# ── VUMeterWidget ─────────────────────────────────────────────────────────────

class VUMeterWidget(QWidget):
    """
    Pill-shaped horizontal VU meter.
    Blocks light up left→right: white → blue → yellow → red zones.
    Each lit block has a multi-layer glow: outer haze + inner bloom + bright core.
    Emits clicked() — used as PTT-stop trigger.
    """
    clicked = pyqtSignal()
    FPS    = 33
    N_BARS = 14

    # Zone boundaries (normalized bar position) and base RGB colors
    _ZONES = [
        (0.28, (210, 235, 255)),   # white/ice
        (0.55, ( 30, 120, 255)),   # blue
        (0.78, (255, 195,  20)),   # yellow
        (1.01, (255,  45,  30)),   # red
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setMinimumWidth(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._level    = 0.0
        self._smoothed = 0.0
        self._active   = False

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
        self.setVisible(False)

    def _tick(self) -> None:
        target = self._level if self._active else 0.0
        self._smoothed += (target - self._smoothed) * 0.35
        self.update()

    @staticmethod
    def _zone_color(frac: float) -> tuple[int, int, int]:
        for boundary, rgb in VUMeterWidget._ZONES:
            if frac < boundary:
                return rgb
        return VUMeterWidget._ZONES[-1][1]

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        radius = h / 2.0

        # ── Pill clip ─────────────────────────────────────────────
        clip = QPainterPath()
        clip.addRoundedRect(0.0, 0.0, float(w), float(h), radius, radius)
        p.setClipPath(clip)

        # ── Background ────────────────────────────────────────────
        p.fillRect(0, 0, w, h, QColor("#0d1117"))

        # ── Bars ──────────────────────────────────────────────────
        pad   = int(radius)
        avail = max(1, w - 2 * pad)
        step  = avail / self.N_BARS
        bar_w = max(3, int(step * 0.52))
        bar_h = int(h * 0.55)
        bar_y = (h - bar_h) // 2
        lv    = self._smoothed

        for i in range(self.N_BARS):
            frac  = (i + 0.5) / self.N_BARS
            bar_x = int(pad + i * step + (step - bar_w) / 2)
            lit   = frac < lv

            if lit:
                rc, gc, bc = self._zone_color(frac)

                # Layer 1 — wide outer haze (full bar height, very transparent)
                haze = max(bar_w, 4)
                p.fillRect(bar_x - haze, 0,
                           bar_w + haze * 2, h,
                           QColor(rc, gc, bc, 20))

                # Layer 2 — inner bloom just around the bar
                p.fillRect(bar_x - 2, bar_y - 2,
                           bar_w + 4, bar_h + 4,
                           QColor(rc, gc, bc, 65))

                # Layer 3 — core bar: vertical gradient (dim edge → bright center → dim edge)
                core = QLinearGradient(0.0, float(bar_y),
                                       0.0, float(bar_y + bar_h))
                core.setColorAt(0.0,  QColor(rc, gc, bc, 140))
                core.setColorAt(0.35, QColor(min(rc + 55, 255),
                                             min(gc + 55, 255),
                                             min(bc + 55, 255), 230))
                core.setColorAt(0.5,  QColor(255, 255, 255, 210))   # bright spine
                core.setColorAt(0.65, QColor(min(rc + 55, 255),
                                             min(gc + 55, 255),
                                             min(bc + 55, 255), 230))
                core.setColorAt(1.0,  QColor(rc, gc, bc, 140))
                p.fillRect(bar_x, bar_y, bar_w, bar_h, core)

            else:
                # Unlit — barely visible dark block
                p.fillRect(bar_x, bar_y, bar_w, bar_h, QColor(255, 255, 255, 16))

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
        cw = self.PX * 2 + 4 * self.DW + self.CW + 5 * self.GS + 12
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
        chars   = f"{mm:02d}{ss:02d}"
        x, y    = self.PX, self.PY

        for i, ch in enumerate(chars):
            self._draw_digit(painter, x, y, ch)
            x += self.DW + self.GS
            if i == 1:
                self._draw_colon(painter, x, y)
                x += self.CW + self.GS

        painter.end()

    def _draw_digit(self, painter: QPainter, ox: int, oy: int, ch: str) -> None:
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

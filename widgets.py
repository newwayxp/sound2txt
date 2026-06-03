"""
widgets.py - Pure CTk widget classes for Sound2Text.
No business logic.  Import-safe from any module.
"""
import time
import math
import random
import tkinter as tk
import customtkinter as ctk

from i18n import _LANG, t

# ── Seven-segment character map  (bit mask: a=64 b=32 c=16 d=8 e=4 f=2 g=1) ──
_SEG7 = {
    "0": 0b1111110, "1": 0b0110000, "2": 0b1101101,
    "3": 0b1111001, "4": 0b0110011, "5": 0b1011011,
    "6": 0b1011111, "7": 0b1110000, "8": 0b1111111,
    "9": 0b1111011,
}


def _dim(hex_color: str, factor: int = 9) -> str:
    """Return a very dim version of hex_color (for ghost segments)."""
    r = max(int(hex_color[1:3], 16) // factor, 8)
    g = max(int(hex_color[3:5], 16) // factor, 6)
    b = max(int(hex_color[5:7], 16) // factor, 6)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── OnAirWidget  (horizontal VU-meter, LED segments) ─────────────────────────

class OnAirWidget(ctk.CTkFrame):
    """
    Compact horizontal VU-meter: N colored LED segments.
    Interface: show() / hide() / set_level(0.0-1.0)
    """
    N   = 12   # segments
    SW  = 9    # segment width
    SH  = 22   # segment height
    GAP = 3    # gap between segments
    FPS = 40   # ms/frame

    def __init__(self, master, **kwargs):
        w = self.N * (self.SW + self.GAP) - self.GAP + 8
        super().__init__(master, fg_color="transparent", **kwargs)
        self._cv = tk.Canvas(self, width=w, height=self.SH + 8,
                             bg="#1a1a2a", highlightthickness=1,
                             highlightbackground="#333344")
        self._cv.pack()
        self._level    = 0.0
        self._smoothed = 0.0
        self._active   = False
        self._anim()

    def set_level(self, v: float):
        self._level = max(0.0, min(v, 1.0))

    def show(self):
        self._active = True
        self.grid(row=0, column=4, padx=(6, 2), pady=10, sticky="w")

    def hide(self):
        self._active = False
        self.grid_remove()

    def _anim(self):
        self._smoothed += ((self._level if self._active else 0.0)
                           - self._smoothed) * 0.35
        lit = int(self._smoothed * self.N)
        cv  = self._cv
        try:
            cv.delete("all")
        except Exception:
            return
        for i in range(self.N):
            x    = 4 + i * (self.SW + self.GAP)
            frac = i / (self.N - 1)
            if i < lit:
                c = "#00e676" if frac < 0.60 else ("#ffea00" if frac < 0.80 else "#ff1744")
            else:
                c = "#0a2a18" if frac < 0.60 else ("#2a2a06" if frac < 0.80 else "#2a0a10")
            cv.create_rectangle(x, 4, x + self.SW, 4 + self.SH, fill=c, outline="")
        self.after(self.FPS, self._anim)


# ── SevenSegClock  (MM:SS display with 7-segment LED look) ───────────────────

class SevenSegClock(ctk.CTkFrame):
    """
    MM:SS display drawn on a Canvas as authentic 7-segment LED digits.
    Active segments glow; ghost segments are very dim (same hue).
    """
    DW = 32   # digit width
    DH = 52   # digit height
    DT = 5    # segment thickness
    CW = 12   # colon width
    PX = 10   # horizontal padding
    PY = 8    # vertical padding
    GS = 2    # inter-segment gap

    def __init__(self, master, on_color: str = "#29b6f6", **kwargs):
        cw = self.PX * 2 + 4 * self.DW + self.CW + 5 * self.GS
        ch = self.DH + self.PY * 2
        super().__init__(master, fg_color="#0d1117", corner_radius=10, **kwargs)
        self._cv  = tk.Canvas(self, width=cw, height=ch,
                              bg="#0d1117", highlightthickness=0)
        self._cv.pack(padx=6, pady=6)
        self._on   = on_color
        self._off  = _dim(on_color, 10)
        self._secs = 0.0
        self._frozen_secs = 0.0
        self._active      = False
        self._start_time  = 0.0
        self._redraw()

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        self._start_time = time.time()
        self._active     = True
        self._tick()

    def stop(self):
        if self._active:
            self._frozen_secs = time.time() - self._start_time
        self._active = False

    def reset(self):
        self._active      = False
        self._start_time  = 0.0
        self._frozen_secs = 0.0
        self._secs        = 0.0
        self._redraw()

    def set_secs(self, secs: float):
        """Directly set the display value (for external control)."""
        if int(secs) != int(self._secs):
            self._secs = secs
            self._redraw()

    def _tick(self):
        if not self._active:
            return
        self._secs = time.time() - self._start_time
        self._redraw()
        self.after(1000, self._tick)

    # ── Drawing ───────────────────────────────────────────────────────────────
    def _redraw(self):
        cv = self._cv
        cv.delete("all")
        s  = int(self._secs)
        mm, ss = s // 60, s % 60
        chars = f"{mm:02d}{ss:02d}"
        x, y  = self.PX, self.PY
        for i, ch in enumerate(chars):
            self._digit(x, y, ch)
            x += self.DW + self.GS
            if i == 1:
                self._colon(x, y)
                x += self.CW + self.GS

    def _digit(self, ox: int, oy: int, ch: str):
        mask = _SEG7.get(ch, 0)
        cv   = self._cv
        W, H, T, G = self.DW, self.DH, self.DT, self.GS
        def seg(bit, x1, y1, x2, y2):
            c = self._on if (mask & bit) else self._off
            cv.create_rectangle(ox+x1, oy+y1, ox+x2, oy+y2, fill=c, outline="")
        seg(64, T+G,   0,          W-T-G, T)
        seg(32, W-T,   T+G,        W,     H//2-G)
        seg(16, W-T,   H//2+G,     W,     H-T-G)
        seg(8,  T+G,   H-T,        W-T-G, H)
        seg(4,  0,     H//2+G,     T,     H-T-G)
        seg(2,  0,     T+G,        T,     H//2-G)
        seg(1,  T+G,   H//2-T//2,  W-T-G, H//2+T//2)

    def _colon(self, ox: int, oy: int):
        cv = self._cv
        cx = ox + self.CW // 2
        r  = 3
        for yc in (oy + self.DH // 3, oy + 2 * self.DH // 3):
            cv.create_oval(cx-r, yc-r, cx+r, yc+r, fill=self._on, outline="")


# ── DashboardWidget  (3 LED clocks side by side) ─────────────────────────────

class DashboardWidget(ctk.CTkFrame):
    """
    Three SevenSegClock timers: elapsed recording, audio captured, transcribed.
    """
    _TIMERS = [
        # key        zh label     ja label       en label       LED color
        ("elapsed", "录音经过",  "録音経過",    "Elapsed",      "#29b6f6"),
        ("audio",   "音频收录",  "音声収録",    "Audio Rec",    "#66bb6a"),
        ("trans",   "文字转换",  "文字起こし",  "Transcribed",  "#ffa726"),
    ]

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._clocks:       dict[str, SevenSegClock] = {}
        self._audio_secs  = 0.0
        self._trans_secs  = 0.0
        self._start_time  = 0.0
        self._frozen_elapsed = 0.0
        self._active      = False

        lang = _LANG
        for col, (key, lz, lj, le, color) in enumerate(self._TIMERS):
            self.grid_columnconfigure(col, weight=1)
            cap = lz if lang == "zh" else (lj if lang == "ja" else le)

            card = ctk.CTkFrame(self, fg_color="#0d1117", corner_radius=14)
            card.grid(row=0, column=col, padx=12, pady=12, sticky="nsew")
            card.grid_columnconfigure(0, weight=1)

            clk = SevenSegClock(card, on_color=color)
            clk.grid(row=0, column=0, pady=(14, 4))
            self._clocks[key] = clk

            ctk.CTkLabel(card, text=cap, font=ctk.CTkFont(size=11),
                         text_color="#4a4a6a").grid(row=1, column=0, pady=(0, 12))

        self._tick()

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        self._start_time     = time.time()
        self._audio_secs     = 0.0
        self._trans_secs     = 0.0
        self._frozen_elapsed = 0.0
        self._active         = True

    def stop(self):
        if self._active and self._start_time:
            self._frozen_elapsed = time.time() - self._start_time
        self._active = False

    def reset(self):
        self._start_time     = 0.0
        self._audio_secs     = 0.0
        self._trans_secs     = 0.0
        self._frozen_elapsed = 0.0
        for clk in self._clocks.values():
            clk.set_secs(0.0)

    def add_audio(self, secs: float): self._audio_secs += secs
    def add_trans(self, secs: float): self._trans_secs += secs

    def _tick(self):
        e = (time.time() - self._start_time) if (self._active and self._start_time) \
            else self._frozen_elapsed
        vals = {"elapsed": e, "audio": self._audio_secs, "trans": self._trans_secs}
        for key, clk in self._clocks.items():
            try:
                clk.set_secs(vals[key])
            except Exception:
                pass
        self.after(1000, self._tick)

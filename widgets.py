"""
widgets.py – Pure CTk widget classes for Sound2Text.

Contains:
  - OnAirWidget   : animated "ON AIR" indicator with audio level ring
  - SevenSegClock : seven-segment-style elapsed-time display
  - DashboardWidget: rec/trans time counters panel
  - _dim() helper : darken a hex colour
"""
import time
import tkinter as tk
import customtkinter as ctk

from i18n import _LANG  # noqa: F401  (imported for potential locale-aware widgets)

# ── Seven-segment character map ────────────────────────────────────────────────
# Each char maps to a tuple of segment IDs (a–g) that are ON.
# Segment layout:
#   aaa
#  f   b
#  f   b
#   ggg
#  e   c
#  e   c
#   ddd
_SEG7: dict[str, tuple[str, ...]] = {
    "0": ("a", "b", "c", "d", "e", "f"),
    "1": ("b", "c"),
    "2": ("a", "b", "d", "e", "g"),
    "3": ("a", "b", "c", "d", "g"),
    "4": ("b", "c", "f", "g"),
    "5": ("a", "c", "d", "f", "g"),
    "6": ("a", "c", "d", "e", "f", "g"),
    "7": ("a", "b", "c"),
    "8": ("a", "b", "c", "d", "e", "f", "g"),
    "9": ("a", "b", "c", "d", "f", "g"),
    ":": ("dp1", "dp2"),
    " ": (),
}


def _dim(hex_color: str, factor: float = 0.18) -> str:
    """Return *hex_color* darkened to *factor* brightness (0–1)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r * factor)
    g = int(g * factor)
    b = int(b * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── OnAirWidget ────────────────────────────────────────────────────────────────

class OnAirWidget(ctk.CTkFrame):
    """
    Animated "ON AIR" indicator.

    Shows a pulsing red ring whose thickness reflects the current audio level
    (0.0–1.0).  Call ``show()`` / ``hide()`` and ``set_level(v)`` to control.
    """

    _RING_MAX  = 8    # max ring width in pixels
    _BLINK_MS  = 600  # blink interval in ms

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        self._visible  = False
        self._level    = 0.0
        self._blink_on = True

        self._canvas = tk.Canvas(
            self, width=90, height=90,
            bg=self._get_bg(), highlightthickness=0,
        )
        self._canvas.pack(padx=4, pady=4)

        self._ring_id  = None
        self._dot_id   = None
        self._text_id  = None
        self._paint()

    def _get_bg(self) -> str:
        # Match the CTkFrame background colour
        mode = ctk.get_appearance_mode()
        return "#2b2b2b" if mode == "Dark" else "#ebebeb"

    def _paint(self):
        self._canvas.delete("all")
        bg  = self._get_bg()
        self._canvas.configure(bg=bg)
        cx, cy, r = 45, 45, 32

        if self._visible:
            # Ring – width proportional to level
            ring_w = max(2, int(self._RING_MAX * self._level))
            ring_color = "#ff3333" if self._blink_on else "#660000"
            self._canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline=ring_color, width=ring_w,
            )
            # Centre dot
            dot_r = 18
            dot_color = "#ff2020" if self._blink_on else "#440000"
            self._canvas.create_oval(
                cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
                fill=dot_color, outline="",
            )
            # "ON AIR" label
            self._canvas.create_text(
                cx, cy, text="ON AIR",
                fill="white" if self._blink_on else "#888888",
                font=("Arial", 9, "bold"),
            )
        else:
            # Idle grey ring
            self._canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="#444444", width=2,
            )
            dot_r = 18
            self._canvas.create_oval(
                cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r,
                fill="#333333", outline="",
            )
            self._canvas.create_text(
                cx, cy, text="OFF",
                fill="#666666",
                font=("Arial", 9, "bold"),
            )

    def _blink(self):
        if not self._visible:
            return
        self._blink_on = not self._blink_on
        self._paint()
        self.after(self._BLINK_MS, self._blink)

    def show(self):
        if not self._visible:
            self._visible  = True
            self._blink_on = True
            self._paint()
            self.after(self._BLINK_MS, self._blink)

    def hide(self):
        self._visible = False
        self._paint()

    def set_level(self, level: float):
        """Update audio level (0.0–1.0) used for ring thickness."""
        self._level = max(0.0, min(1.0, level))
        if self._visible:
            self._paint()


# ── SevenSegClock ──────────────────────────────────────────────────────────────

class SevenSegClock(ctk.CTkFrame):
    """
    Seven-segment style elapsed-time display (HH:MM:SS).

    Call ``start()`` / ``stop()`` / ``reset()`` to control.
    """

    _SEG_W  = 4   # segment bar width
    _DIGIT_W = 22
    _DIGIT_H = 36
    _GAP     = 3
    _COLOR_ON  = "#00ff88"
    _COLOR_OFF_FACTOR = 0.12

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        self._running    = False
        self._start_time: float | None = None
        self._elapsed    = 0.0  # accumulated seconds before last pause

        # 8 digits: HH:MM:SS  (indices 0-1=H, 2=colon, 3-4=M, 5=colon, 6-7=S)
        self._chars = "00:00:00"

        bg = self._resolve_bg()
        total_w = 8 * (self._DIGIT_W + self._GAP) + 2 * (4 + self._GAP)
        self._canvas = tk.Canvas(
            self, width=total_w, height=self._DIGIT_H + 8,
            bg=bg, highlightthickness=0,
        )
        self._canvas.pack(padx=4, pady=2)
        self._render()

    def _resolve_bg(self) -> str:
        mode = ctk.get_appearance_mode()
        return "#1e1e1e" if mode == "Dark" else "#f0f0f0"

    def _seg_coords(self, x: int, y: int, seg: str):
        """Return canvas polygon coords for segment *seg* at top-left (x, y)."""
        w, h, sw = self._DIGIT_W, self._DIGIT_H, self._SEG_W
        half = h // 2
        coords = {
            "a": (x+sw,   y,          x+w-sw, y,          x+w-sw//2, y+sw//2,   x+sw//2, y+sw//2),
            "b": (x+w,    y+sw,       x+w,    y+half-sw,  x+w-sw//2, y+half,    x+w-sw//2, y+sw),
            "c": (x+w,    y+half+sw,  x+w,    y+h-sw,     x+w-sw//2, y+h-sw//2, x+w-sw//2, y+half+sw),
            "d": (x+sw,   y+h,        x+w-sw, y+h,        x+w-sw//2, y+h-sw//2, x+sw//2,   y+h-sw//2),
            "e": (x,      y+half+sw,  x,      y+h-sw,     x+sw//2,   y+h-sw//2, x+sw//2,   y+half+sw),
            "f": (x,      y+sw,       x,      y+half-sw,  x+sw//2,   y+half,    x+sw//2,   y+sw),
            "g": (x+sw,   y+half,     x+w-sw, y+half,     x+w-sw//2, y+half+sw//2, x+sw//2, y+half+sw//2),
        }
        return coords.get(seg)

    def _render(self):
        self._canvas.delete("all")
        bg = self._resolve_bg()
        self._canvas.configure(bg=bg)
        x = 2
        y = 4
        color_off = _dim(self._COLOR_ON, self._COLOR_OFF_FACTOR)

        for ch in self._chars:
            if ch == ":":
                # Draw two dots
                cx = x + 2
                for dot_y in (y + self._DIGIT_H // 3, y + 2 * self._DIGIT_H // 3):
                    self._canvas.create_oval(cx, dot_y - 2, cx + 4, dot_y + 2,
                                             fill=self._COLOR_ON, outline="")
                x += 8 + self._GAP
                continue

            segs_on = set(_SEG7.get(ch, ()))
            all_segs = ("a", "b", "c", "d", "e", "f", "g")
            for seg in all_segs:
                coords = self._seg_coords(x, y, seg)
                if coords is None:
                    continue
                color = self._COLOR_ON if seg in segs_on else color_off
                self._canvas.create_polygon(*coords, fill=color, outline="")
            x += self._DIGIT_W + self._GAP

    def _format(self, total_secs: float) -> str:
        t = int(total_secs)
        h  = t // 3600
        m  = (t % 3600) // 60
        s  = t % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _tick(self):
        if not self._running:
            return
        elapsed = self._elapsed + (time.monotonic() - self._start_time)
        new_chars = self._format(elapsed)
        if new_chars != self._chars:
            self._chars = new_chars
            self._render()
        self.after(500, self._tick)

    def start(self):
        if not self._running:
            self._running    = True
            self._start_time = time.monotonic()
            self._tick()

    def stop(self):
        if self._running:
            self._elapsed += time.monotonic() - self._start_time
            self._running   = False

    def reset(self):
        self._running   = False
        self._elapsed   = 0.0
        self._start_time = None
        self._chars     = "00:00:00"
        self._render()


# ── DashboardWidget ────────────────────────────────────────────────────────────

class DashboardWidget(ctk.CTkFrame):
    """
    Dashboard showing recording and transcription elapsed times.

    Public API:
      start()         – start both clocks
      stop()          – stop both clocks
      reset()         – reset both clocks to 00:00:00
      add_audio(secs) – add *secs* to the audio-time counter (future use)
      add_trans(secs) – add *secs* to the trans-time counter (future use)
    """

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)

        self._audio_secs = 0.0
        self._trans_secs = 0.0

        from i18n import t
        # Row 0: Rec clock
        ctk.CTkLabel(self, text=t("rec_time"),
                     font=ctk.CTkFont(size=11), anchor="w").grid(
            row=0, column=0, padx=(8, 4), pady=(6, 0), sticky="w")
        self._rec_clock = SevenSegClock(self)
        self._rec_clock.grid(row=1, column=0, padx=8, pady=(0, 4))

        # Row 2: Trans clock
        ctk.CTkLabel(self, text=t("trans_time"),
                     font=ctk.CTkFont(size=11), anchor="w").grid(
            row=2, column=0, padx=(8, 4), pady=(4, 0), sticky="w")
        self._tr_clock = SevenSegClock(self)
        self._tr_clock.grid(row=3, column=0, padx=8, pady=(0, 6))

    def start(self):
        self._rec_clock.start()
        self._tr_clock.start()

    def stop(self):
        self._rec_clock.stop()
        self._tr_clock.stop()

    def reset(self):
        self._rec_clock.reset()
        self._tr_clock.reset()
        self._audio_secs = 0.0
        self._trans_secs = 0.0

    def add_audio(self, secs: float):
        self._audio_secs += secs

    def add_trans(self, secs: float):
        self._trans_secs += secs

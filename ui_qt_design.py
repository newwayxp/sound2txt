"""
ui_qt.py – PyQt6 View layer for Sound2Text.

Implements ViewProtocol (defined in presenter.py) using PyQt6.
No customtkinter / tkinter imports.
"""
from __future__ import annotations

import os
import html
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    QMetaObject, QSize, Qt, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QRadioButton, QSlider, QSizePolicy,
    QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from appconfig import BASE, AppConfig
from i18n import _LANG, t
from widgets_qt import VUMeterWidget, VUBarMeterWidget
from log_util import LogConfig, FileLogger
from font_checker import ensure_fonts_installed

if TYPE_CHECKING:
    from presenter import Presenter

# ── Global stylesheet ─────────────────────────────────────────────────────────

#
# Refined dark theme. Palette (GitHub-dark inspired, cohesive with the
# custom-painted dark widgets in widgets_qt.py which use #0D1117):
#   WINDOW  #13161C   app canvas (slightly lifted so sunken panels read recessed)
#   SURFACE #1B1F27   bars, tab panes, headers
#   SUNKEN  #0D1117   text views / log / inputs — matches the painted widgets
#   BORDER  #2A2F3A   hairline dividers and control outlines
#   HOVER   #242A35   hover/elevated fill
#   TEXT    #E6EDF3   primary text
#   SUB     #9AA3B2   secondary text
#   MUTE    #6B7480   muted / hint text
#   ACCENT  #4AA8FF   single sky-blue accent (focus, selected tab, links)
#   SEL_BG  #16324A   selection background
#   GREEN   #2EA043 (start)   RED #DA3633 (stop)
#
_STYLESHEET = """
/* ── Base ── */
QWidget {
    font-family: __FONT_FAMILY__;
    font-size: 12px;
    color: #E6EDF3;
}

/* ── Main window / central widget ── */
QMainWindow, QWidget#centralWidget, QWidget#innerContent {
    background-color: #13161C;
}

/* ── Control bar — sits above the canvas, hairline bottom border ── */
QFrame#controlBar {
    background-color: #1B1F27;
    border: none;
    border-bottom: 1px solid #2A2F3A;
}

/* ── Start/Stop toggle button (colors set inline per state) ── */
QPushButton#btnToggle {
    color: white;
    border: none;
    border-radius: 17px;
    padding: 6px 20px;
    font-size: 13px;
    font-weight: bold;
    min-width: 100px;
    min-height: 34px;
}

/* ── Save button — accent, soft vertical gradient for depth ── */
QPushButton#btnSave {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2F71CE, stop:1 #2256A8);
    color: #FFFFFF;
    border: none;
    border-radius: 8px;
    padding: 6px 18px;
    font-weight: bold;
    min-width: 160px;
    min-height: 36px;
}
QPushButton#btnSave:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D84E2, stop:1 #2A63BC);
}
QPushButton#btnSave:pressed {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #1F559E, stop:1 #1A4986);
}

/* ── Browse folder button — small, subtle ── */
QPushButton#btnBrowse {
    background-color: #21262D;
    color: #C9D1D9;
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 26px;
}
QPushButton#btnBrowse:hover { background-color: #2A313B; border-color: #3A4250; }

/* ── Preset buttons ── */
QPushButton#btnPreset {
    background-color: #21262D;
    color: #C9D1D9;
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    padding: 4px 10px;
    min-height: 26px;
}
QPushButton#btnPreset:hover { background-color: #2A313B; border-color: #3A4250; }

/* ── Segmented pill toggles (device / model selectors, design-B) ── */
QPushButton#pillToggle {
    background-color: #0C1016;
    color: #C9CCD2;
    border: 1px solid #2A323E;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    min-height: 18px;
}
QPushButton#pillToggle:hover { border-color: #3A4250; color: #E6EDF3; }
QPushButton#pillToggle:checked {
    background-color: #2B6FB8;
    border: 1px solid #3B9EFF;
    color: #FFFFFF;
}
QPushButton#pillToggle:checked:hover { background-color: #3D84E2; }

/* ── Generic push buttons (e.g. Clear log) ── */
QPushButton {
    background-color: #21262D;
    color: #C9D1D9;
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    padding: 4px 10px;
}
QPushButton:hover { background-color: #2A313B; border-color: #3A4250; }
QPushButton:pressed { background-color: #1A1E25; }

/* ── Tab widget — flat, accent underline for selected ── */
QTabWidget {
    background-color: #0E1218;
}
QTabWidget::pane {
    border: none;
    border-top: 1px solid #232B36;
    background-color: #0E1218;
    margin: 0px;
    padding: 0px;
}
QTabBar {
    background-color: #0F141B;
    border-bottom: 1px solid #232B36;
}
QTabBar::tab {
    color: #7E8A99;
    padding: 0px 14px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
    font-size: 13px;
    font-weight: 500;
    height: 38px;
    line-height: 38px;
}
QTabBar::tab:selected {
    color: #9FD0FF;
    font-weight: 600;
    border-bottom: 2px solid #3B9EFF;
}
QTabBar::tab:hover:!selected {
    color: #AEB6C0;
    border-bottom: 2px solid #2B6FB8;
}
QTabBar::tab:disabled { color: #3A4250; }

/* ── Line edit ── */
QLineEdit {
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 28px;
    background-color: #0D1117;
    color: #E6EDF3;
    selection-background-color: #16324A;
    selection-color: #E6EDF3;
}
QLineEdit:focus { border-color: #4AA8FF; }
QLineEdit:disabled { color: #6B7480; background-color: #161A21; }

/* ── Combo box ── */
QComboBox {
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    padding: 3px 8px;
    min-height: 28px;
    background-color: #0D1117;
    color: #E6EDF3;
}
QComboBox:hover { border-color: #3A4250; }
QComboBox:focus { border-color: #4AA8FF; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}
QComboBox QAbstractItemView {
    background-color: #161B22;
    color: #E6EDF3;
    selection-background-color: #16324A;
    selection-color: #E6EDF3;
    border: 1px solid #2A2F3A;
    outline: none;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #2A2F3A;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #4AA8FF;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #4AA8FF;
    border-radius: 2px;
}

/* ── Text views (transcript / corrected / minutes) ── */
QTextEdit, QTextBrowser {
    background-color: #0D1117;
    color: #E6EDF3;
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    selection-background-color: #16324A;
    selection-color: #E6EDF3;
}

/* ── Text edit (log) ── */
QTextEdit#logBox {
    border: 1px solid #2A2F3A;
    border-radius: 5px;
    background-color: #0D1117;
    color: #C9D1D9;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    line-height: 1.7;
}

/* ── Vertical separator lines ── */
QFrame[frameShape="5"] {
    color: #2A2F3A;
}

/* ── Labels ── */
QLabel { color: #C9D1D9; background: transparent; }

/* ── Checkboxes and radio buttons ── */
QCheckBox, QRadioButton { color: #C9D1D9; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px; height: 16px;
    border: 1px solid #3A4250;
    background-color: #0D1117;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: #4AA8FF; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #4AA8FF;
    border-color: #4AA8FF;
}

/* ── Scrollbars — thin, unobtrusive ── */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #2E3540;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #3C4550; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #2E3540;
    border-radius: 5px;
    min-width: 28px;
}
QScrollBar::handle:horizontal:hover { background: #3C4550; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ── Message boxes ── */
QMessageBox { background-color: #1B1F27; }
QMessageBox QLabel { color: #E6EDF3; }

/* ── Tooltips ── */
QToolTip {
    background-color: #21262D;
    color: #E6EDF3;
    border: 1px solid #2A2F3A;
    padding: 4px 6px;
}
"""


# ── Separator helper ──────────────────────────────────────────────────────────

def _vsep(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ── App (View) ────────────────────────────────────────────────────────────────

class CompactDashboardWidget(QWidget):
    """Compact left-rail dashboard for the Claude design-B layout."""

    _LABELS = {
        "elapsed": {"zh": "录音经过", "ja": "録音経過", "en": "Elapsed"},
        "audio": {"zh": "音频收录", "ja": "音声収録", "en": "Audio Rec"},
        "trans": {"zh": "已转写", "ja": "文字起こし", "en": "Transcribed"},
    }
    _COLORS = {"elapsed": "#2FD6E0", "audio": "#3DDC84", "trans": "#FFB454"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value_labels: dict[str, QLabel] = {}
        self._audio_secs = 0.0
        self._trans_secs = 0.0
        self._start_time = 0.0
        self._frozen_elapsed = 0.0
        self._active = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)

        for key in ("elapsed", "audio", "trans"):
            block = QWidget(self)
            block.setStyleSheet("background: transparent;")
            box = QVBoxLayout(block)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(5)

            label = QLabel(self._LABELS[key].get(_LANG, self._LABELS[key]["en"]), block)
            label.setStyleSheet(
                "color: #7E8A99; font-size: 9px; font-weight: 600; "
                "letter-spacing: 1.5px; background: transparent; margin-bottom: 5px;"
            )
            box.addWidget(label)

            value = QLabel("00:00:00", block)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            # Share Tech Mono 22px with letter-spacing 2px (absolute). The family +
            # size MUST be set via QSS here: the global `QWidget { font-family;
            # font-size }` rule overrides setFont(), so a bare setFont() would be
            # ignored (rendered as 12px Noto Sans JP). letter-spacing has no QSS
            # equivalent, so it stays on the QFont (QSS merges over it).
            font = QFont("Share Tech Mono", 22)
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2)
            value.setFont(font)
            value.setStyleSheet(
                f"color: {self._COLORS[key]}; background: transparent; line-height: 1;"
                " font-family: 'Share Tech Mono'; font-size: 22px;"
            )
            box.addWidget(value)
            self._value_labels[key] = value
            layout.addWidget(block)

        layout.setContentsMargins(0, 2, 0, 0)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()
        self._tick()

    @staticmethod
    def _fmt(secs: float) -> str:
        s = max(0, int(secs))
        hh, rem = divmod(s, 3600)
        mm, ss = divmod(rem, 60)
        return f"{hh:02d}:{mm:02d}:{ss:02d}"

    def start(self) -> None:
        self._start_time = time.time()
        self._audio_secs = 0.0
        self._trans_secs = 0.0
        self._frozen_elapsed = 0.0
        self._active = True
        self._tick()

    def stop(self) -> None:
        if self._active and self._start_time:
            self._frozen_elapsed = time.time() - self._start_time
        self._active = False
        self._tick()

    def reset(self) -> None:
        self._start_time = 0.0
        self._audio_secs = 0.0
        self._trans_secs = 0.0
        self._frozen_elapsed = 0.0
        self._active = False
        self._tick()

    def add_audio(self, secs: float) -> None:
        self._audio_secs += secs
        self._tick()

    def add_trans(self, secs: float) -> None:
        self._trans_secs += secs
        self._tick()

    def _tick(self) -> None:
        elapsed = time.time() - self._start_time if self._active and self._start_time else self._frozen_elapsed
        vals = {"elapsed": elapsed, "audio": self._audio_secs, "trans": self._trans_secs}
        for key, secs in vals.items():
            self._value_labels[key].setText(self._fmt(secs))


class App(QMainWindow):
    """
    PyQt6 View implementing ViewProtocol.
    All business logic is delegated to the Presenter.
    """
    # Thread-safe signals — safe to emit from any thread
    _log_signal  = pyqtSignal(str)      # log message
    _call_signal = pyqtSignal(object)   # schedule(fn) — run fn on main thread

    def __init__(self, presenter: "Presenter"):
        super().__init__()

        self._presenter = presenter
        self._btn_cuda: QPushButton | None = None
        self._allow_close = False   # set True by destroy() once shutdown is done

        # Window basics
        self.setWindowTitle(t("title"))
        self.resize(1000, 680)
        self.setMinimumSize(860, 560)

        # Initialize file logger for UI-side logs
        try:
            log_cfg = LogConfig(presenter._config)
            self._file_log = FileLogger(log_cfg.log_file)
            self._file_log.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [UI ] [INFO ] UI window created")
        except Exception:
            self._file_log = None

        # Wire view before building (so presenter can call us during initialize)
        presenter.set_view(self)

        self._build()
        self._apply_initial_ui_state()

        # Connect signals (thread-safe cross-thread communication)
        self._log_signal.connect(self._log_box.append)
        self._call_signal.connect(lambda fn: fn())

        # Deferred presenter startup: show the window first, run the slow CUDA
        # detection in the background, and keep Start disabled until it finishes.
        QTimer.singleShot(0, self._start_async_init)

    # ── Async startup ─────────────────────────────────────────────────────────

    def _start_async_init(self) -> None:
        """Window is now visible. Disable Start, then run startup work (and, only
        when device=auto, the slow CUDA probe) in a background thread so the UI
        stays responsive."""
        self.set_start_enabled(False)
        self.put_log("[UI] Initializing…")

        def _worker():
            try:
                self._presenter.warm_up()   # heavy, view-free, result cached
            except Exception as e:
                self.put_log(f"[ERROR] Startup detection failed: {e}")
            # Finish on the Qt main thread — initialize() touches view widgets.
            self.schedule(self._finish_async_init)

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_async_init(self) -> None:
        """Runs on the Qt main thread after warm_up() completes."""
        self._presenter.initialize()
        self.set_start_enabled(True)
        self.put_log("[UI] Ready")

    # ── ViewProtocol ──────────────────────────────────────────────────────────

    def put_log(self, msg: str) -> None:
        """Thread-safe: emit signal which is connected to log box on main thread."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # UI ステート系は常にファイルに書き込む
        if hasattr(self, "_file_log") and self._file_log:
            self._file_log.write(f"[{ts}] [UI ] [INFO ] {msg}")
        self._log_signal.emit(f"[{ts}] {msg}")

    def schedule(self, fn) -> None:
        """Thread-safe: emit signal so fn runs on the Qt main thread."""
        self._call_signal.emit(fn)

    def _status_text(self, key: str) -> str:
        labels = {
            "ready": {
                "zh": "准备就绪",
                "ja": "準備完了",
                "en": "Ready",
            },
            "recording": {
                "zh": "录音中",
                "ja": "録音中",
                "en": "Recording",
            },
            "stopping": {
                "zh": "停止处理中 - 正在保存文字和音频",
                "ja": "停止処理中 - 文字起こしと音声を保存中",
                "en": "Stopping - finalizing transcript and audio",
            },
        }
        return labels[key].get(_LANG, labels[key]["en"])

    def _mic_state_text(self, active: bool) -> str:
        if active:
            return "ON AIR"
        return "MIC OFF"

    def _mic_level_text(self) -> str:
        return "MIC\nLEVEL"

    def set_start_enabled(self, v: bool) -> None:
        if v:
            self._btn_toggle_recording = False
            self._btn_toggle.setText(t("start"))
            if hasattr(self, "_top_status"):
                self._top_status.setText(self._status_text("ready"))
        self._apply_toggle_style(enabled=v)
        self.put_log(f"[UI-STATE] Start: {'enabled' if v else 'disabled'}")

    def set_stop_enabled(self, v: bool) -> None:
        if v:
            # Switch button to Stop mode and enable
            self._btn_toggle_recording = True
            self._btn_toggle.setText(t("stop"))
            if hasattr(self, "_top_status"):
                self._top_status.setText(self._status_text("recording"))
            self._apply_toggle_style(enabled=True)
        elif self._btn_toggle_recording:
            # Disable only when currently in Stop mode;
            # if already back in Start mode (set_start_enabled ran first), ignore.
            if hasattr(self, "_top_status"):
                self._top_status.setText(self._status_text("stopping"))
            self._apply_toggle_style(enabled=False)
        self.put_log(f"[UI-STATE] Stop: {'enabled' if v else 'disabled'}")

    def show_onair(self) -> None:
        """Mic recording active — LED turns red."""
        if hasattr(self, "_onair_panel"):
            self._onair_panel.setVisible(True)
            self._onair_panel.setStyleSheet(
                "QFrame#onairPanel { background-color: #1A1216; border: 1px solid #3A2326; border-radius: 7px; padding: 10px 12px; }"
            )
        if hasattr(self, "_vumeter_bar"):
            self._vumeter_bar.setVisible(True)
            self._vu_meter.show()
        if hasattr(self, "_onair_label"):
            self._onair_label.setText(self._mic_state_text(True))
            self._onair_label.setStyleSheet(
                "color: #FF7A72; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; background: transparent;"
            )
        self._onair_dot.setStyleSheet(
            "background-color: #FF4848;"
            "border-radius: 50%;"
            "border: none;"
        )
        self.put_log("[UI-STATE] ON AIR: recording (red)")

    def hide_onair(self) -> None:
        """Mic recording stopped — LED turns blue, VU resets to 0."""
        if hasattr(self, "_onair_panel"):
            self._onair_panel.setVisible(True)
            self._onair_panel.setStyleSheet(
                "QFrame#onairPanel { background-color: #0F1722; border: 1px solid #1E2A3A; border-radius: 7px; padding: 10px 12px; }"
            )
        if hasattr(self, "_vumeter_bar"):
            self._vumeter_bar.setVisible(True)
        if hasattr(self, "_onair_label"):
            self._onair_label.setText(self._mic_state_text(False))
            self._onair_label.setStyleSheet(
                "color: #5CB0FF; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; background: transparent;"
            )
        self._onair_dot.setStyleSheet(
            "background-color: #3B9EFF;"
            "border-radius: 50%;"
            "border: none;"
        )
        self._vu_meter.set_level(0.0)   # discard any in-flight level update
        self._vu_meter.show()
        self.put_log("[UI-STATE] ON AIR: idle (blue)")

    def set_onair_level(self, level: float) -> None:
        self._vu_meter.set_level(level)

    def dashboard_start(self) -> None:
        self._dashboard.start()

    def dashboard_stop(self) -> None:
        self._dashboard.stop()

    def dashboard_reset(self) -> None:
        self._dashboard.reset()

    def dashboard_add_audio(self, secs: float) -> None:
        self._dashboard.add_audio(secs)

    def dashboard_add_trans(self, secs: float) -> None:
        self._dashboard.add_trans(secs)

    def set_window_title(self, title: str) -> None:
        self.setWindowTitle(title)

    def get_window_title(self) -> str:
        return self.windowTitle()

    def destroy(self) -> None:
        # Called once the presenter's graceful shutdown has finished.
        self._allow_close = True
        QApplication.quit()

    def lock_to_cpu(self) -> None:
        # Reflect a CPU device in the UI (startup config or a runtime CUDA
        # fallback). Per the free-settings policy the GPU radios are NOT disabled
        # — the user may re-select CUDA at any time, which re-validates it.
        if hasattr(self, "_rb_device_cpu"):
            self._rb_device_cpu.setChecked(True)

    def unlock_gpu_buttons(self) -> None:
        # No-op: device radios are never disabled (free-settings policy). Kept
        # for ViewProtocol compatibility.
        pass

    def set_cuda_btn_text(self, text: str) -> None:
        if self._btn_cuda is not None:
            self._btn_cuda.setText(text)

    def set_cuda_btn_state(self, enabled: bool) -> None:
        # Free-settings policy: the CUDA radio stays selectable regardless of the
        # probe result. Selecting it triggers an on-demand validation instead.
        if self._btn_cuda is not None:
            self._btn_cuda.setEnabled(True)

    # ── Busy overlay ───────────────────────────────────────────────────────────

    def _ensure_busy_overlay(self) -> QWidget:
        ov = getattr(self, "_busy_overlay", None)
        if ov is None:
            ov = QWidget(self)
            ov.setObjectName("busyOverlay")
            ov.setStyleSheet("#busyOverlay { background-color: rgba(0,0,0,0.45); }")
            lbl = QLabel("", ov)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "color: white; font-size: 16px; font-weight: 600; background: transparent;"
            )
            self._busy_label = lbl
            self._busy_overlay = ov
            ov.hide()
        return ov

    def show_busy(self, text: str) -> None:
        ov = self._ensure_busy_overlay()
        ov.setGeometry(self.rect())
        self._busy_label.setGeometry(ov.rect())
        self._busy_label.setText(text)
        ov.raise_()
        ov.show()
        QApplication.processEvents()

    def hide_busy(self) -> None:
        ov = getattr(self, "_busy_overlay", None)
        if ov is not None:
            ov.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        ov = getattr(self, "_busy_overlay", None)
        if ov is not None and ov.isVisible():
            ov.setGeometry(self.rect())
            self._busy_label.setGeometry(ov.rect())

    # ── CUDA on-demand validation ──────────────────────────────────────────────

    def _on_cuda_radio_clicked(self) -> None:
        """User picked CUDA. Validate the GPU on demand behind a busy mask; if no
        usable GPU is found, warn and revert the selection to CPU."""
        self.show_busy(t("cuda_checking"))
        self._busy_shown_at = time.monotonic()

        def _probe():
            try:
                ok = bool(self._presenter.cuda_available)
            except Exception:
                ok = False
            self.schedule(lambda: self._finish_cuda_probe(ok))

        threading.Thread(target=_probe, daemon=True).start()

    def _finish_cuda_probe(self, ok: bool) -> None:
        # The CUDA probe result is cached after the first call, so a repeat check
        # returns almost instantly — the busy mask would flash invisibly. Keep it
        # on screen for a minimum so the user actually sees the feedback.
        _MIN_MS = 350
        shown = (time.monotonic() - getattr(self, "_busy_shown_at", 0.0)) * 1000
        remaining = int(_MIN_MS - shown)
        if remaining > 0:
            QTimer.singleShot(remaining, lambda: self._after_cuda_probe(ok))
        else:
            self._after_cuda_probe(ok)

    def _after_cuda_probe(self, ok: bool) -> None:
        self.hide_busy()
        if ok:
            self.put_log("[UI] CUDA available — device set to CUDA")
            return
        # No usable GPU: revert to CPU and inform the user.
        if hasattr(self, "_rb_device_cpu"):
            self._rb_device_cpu.setChecked(True)
        self.put_log(f"[UI] {t('cuda_no_gpu')}")
        QMessageBox.warning(self, t("title"), t("cuda_no_gpu"))

    # Status labels are no-ops (like ui.py)
    def set_rec_status(self, text_key: str, color: str) -> None:
        pass

    def set_tr_status(self, text_key: str, color: str) -> None:
        pass

    def set_sum_status(self, text_key: str, color: str) -> None:
        pass

    @staticmethod
    def _format_transcript_html(text: str) -> str:
        rows: list[str] = []
        ts_re = re.compile(r"^\[?(\d{1,2}:\d{2}:\d{2})\]?\s*(.*)$")
        speaker_re = re.compile(r"^([\w\u3040-\u30ff\u3400-\u9fff]{1,12})\s+(.+)$")
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                rows.append("<div class='blank'>&nbsp;</div>")
                continue
            if line.startswith("==="):
                rows.append(f"<div class='meta'>{html.escape(line)}</div>")
                continue
            match = ts_re.match(line)
            if not match:
                rows.append(f"<div class='textline'>{html.escape(line)}</div>")
                continue
            stamp, body = match.groups()
            speaker = ""
            body = body.strip()
            sm = speaker_re.match(body)
            if sm:
                speaker, body = sm.groups()
            speaker_html = f"<span class='speaker'>{html.escape(speaker)}</span>" if speaker else ""
            rows.append(
                "<div class='line'>"
                f"<span class='stamp'>{html.escape(stamp)}</span>"
                f"{speaker_html}"
                f"<span class='body'>{html.escape(body)}</span>"
                "</div>"
            )
        return (
            "<html><head><style>"
            # CJK glyphs are not in JetBrains Mono; Qt ignores the CSS fallback
            # order and substitutes a heavy face, which reads as bold. Lead the
            # body with a regular-weight CJK family so 漢字/中文 render normally;
            # the timestamp keeps the monospace look via .stamp.
            "body{margin:0;background:#0e1218;color:#dde3ea;"
            "font-family:'Noto Sans JP','Microsoft YaHei','Meiryo UI','Segoe UI',sans-serif;"
            "font-size:13px;line-height:1.95;font-weight:normal;}"
            ".line{white-space:pre-wrap;font-weight:normal;}"
            ".stamp{color:#5cb0ff;display:inline-block;min-width:76px;font-weight:normal;"
            "font-family:'JetBrains Mono','Consolas',monospace;}"
            ".speaker{color:#7fb8e6;display:inline-block;min-width:54px;margin-right:8px;font-weight:normal;}"
            ".body{color:#dde3ea;font-weight:normal;}"
            ".meta{color:#5b6675;font-weight:normal;}"
            ".textline{color:#dde3ea;white-space:pre-wrap;font-weight:normal;}"
            ".blank{height:10px;}"
            "</style></head><body>"
            + "".join(rows)
            + "</body></html>"
        )

    def show_transcript(self, path: str) -> None:
        """Display transcript file content in the Transcript tab."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            self._transcript_view.setHtml(self._format_transcript_html(text))
            from PyQt6.QtGui import QTextCursor
            self._transcript_view.moveCursor(QTextCursor.MoveOperation.End)
        except Exception as e:
            self._transcript_view.setPlainText(f"[Error reading transcript: {e}]")

    def show_corrected(self, path: str) -> None:
        """Display corrected transcript file content in the Corrected tab."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            self._corrected_view.setHtml(self._format_transcript_html(text))
            from PyQt6.QtGui import QTextCursor
            self._corrected_view.moveCursor(QTextCursor.MoveOperation.End)
        except Exception as e:
            self._corrected_view.setPlainText(f"[Error reading corrected text: {e}]")

    def show_minutes(self, path: str) -> None:
        """Display meeting minutes file content as rendered Markdown."""
        try:
            with open(path, encoding="utf-8-sig") as f:
                text = f.read()
            from PyQt6.QtGui import QTextCursor
            try:
                self._minutes_view.setMarkdown(text)
            except Exception:
                self._minutes_view.setPlainText(text)
            self._minutes_view.moveCursor(QTextCursor.MoveOperation.Start)
        except Exception as e:
            self._minutes_view.setPlainText(f"[Error reading minutes: {e}]")

    def clear_results(self) -> None:
        """Clear transcript, corrected and minutes tabs when a new session starts."""
        self._transcript_view.clear()
        self._corrected_view.clear()
        self._minutes_view.clear()

    # ── Initial UI state from config ──────────────────────────────────────────

    def _apply_initial_ui_state(self) -> None:
        cfg = self._presenter._config

        # Device radio
        device = cfg.get("recording", "device", fallback="auto")
        if device == "cuda" and hasattr(self, "_rb_device_cuda"):
            self._rb_device_cuda.setChecked(True)
        elif device == "cpu" and hasattr(self, "_rb_device_cpu"):
            self._rb_device_cpu.setChecked(True)
        elif hasattr(self, "_rb_device_auto"):
            self._rb_device_auto.setChecked(True)

        # Model selection
        model = cfg.get("recording", "model_size", fallback="small").strip()
        if hasattr(self, "_model_btns"):
            self._select_model_pill(model)

        # Quick lang combo
        if hasattr(self, "_quick_lang_combo"):
            cur_lang = cfg.get("recording", "language", fallback="auto")
            idx = {"auto": 0, "zh": 1, "ja": 2, "en": 3}.get(cur_lang, 0)
            self._quick_lang_combo.setCurrentIndex(idx)

        if hasattr(self, "_lang_buttons"):
            cur_lang = cfg.get("recording", "language", fallback="auto")
            self._refresh_lang_segments(cur_lang)

        # Mode buttons

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Row 0: control bar (full-width, no padding)
        control_bar = self._build_design_control_bar(central)
        layout.addWidget(control_bar, 0)

        body = QWidget()
        body.setObjectName("bodyArea")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        body_layout.addWidget(self._build_left_rail(body), 0)

        main = QWidget(body)
        main.setObjectName("mainWorkArea")
        main.setStyleSheet("QWidget#mainWorkArea { background-color: #0E1218; }")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        settings_tabs = self._build_settings_tabs(main)
        main_layout.addWidget(settings_tabs, 1)

        log_area = self._build_log_area(main)
        log_area.setFixedHeight(96)
        main_layout.addWidget(log_area, 0)

        body_layout.addWidget(main, 1)
        layout.addWidget(body, 1)

    # ── Control bar ───────────────────────────────────────────────────────────

    def _build_design_control_bar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName("controlBar")
        bar.setFixedHeight(66)

        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(18, 0, 18, 0)
        hbox.setSpacing(20)

        self._btn_toggle_recording = False
        self._btn_toggle = QPushButton(t("start"), bar)
        self._btn_toggle.setObjectName("btnToggle")
        self._btn_toggle.clicked.connect(self._on_toggle)
        self._apply_toggle_style(enabled=True)
        hbox.addWidget(self._btn_toggle)

        hbox.addWidget(_vsep(bar))

        self._top_status = QLabel(self._status_text("ready"), bar)
        self._top_status.setStyleSheet(
            "color: #7E8A99; font-size: 12.5px; font-weight: 500; line-height: 1.5; background: transparent;"
        )
        self._top_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hbox.addWidget(self._top_status, 1)

        hbox.addWidget(_vsep(bar))

        self._lang_buttons: dict[str, QPushButton] = {}
        lang_wrap = QFrame(bar)
        lang_wrap.setObjectName("langSegments")
        lang_wrap.setStyleSheet(
            "QFrame#langSegments { background-color: #0E1218; border: 1px solid #232B36; border-radius: 7px; }"
        )
        lang_box = QHBoxLayout(lang_wrap)
        lang_box.setContentsMargins(4, 4, 4, 4)
        lang_box.setSpacing(4)
        for code, label in (
            ("auto", "Auto"),
            ("zh", t("lang_zh")),
            ("ja", t("lang_ja")),
            ("en", "English"),
        ):
            btn = QPushButton(label, lang_wrap)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, c=code: self._set_quick_lang(c))
            self._lang_buttons[code] = btn
            lang_box.addWidget(btn)
        hbox.addWidget(lang_wrap, 0)
        cur_lang = self._presenter._config.get("recording", "language", fallback="auto")
        self._refresh_lang_segments(cur_lang)

        return bar

    def _build_control_bar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName("controlBar")
        bar.setFixedHeight(66)

        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(16, 10, 16, 10)
        hbox.setSpacing(8)

        # Single Start/Stop toggle button
        self._btn_toggle_recording = False   # False = "Start" mode
        self._btn_toggle = QPushButton(t("start"), bar)
        self._btn_toggle.setObjectName("btnToggle")
        self._btn_toggle.clicked.connect(self._on_toggle)
        self._apply_toggle_style(enabled=True)
        hbox.addWidget(self._btn_toggle)

        hbox.addWidget(_vsep(bar))

        # ON AIR + VU meter — wide rectangular panel that fills the bar between
        # the Start button and the language selector. Hidden until recording.
        self._vumeter_bar = QFrame(bar)
        self._vumeter_bar.setObjectName("vuContainer")
        self._vumeter_bar.setFixedHeight(46)
        self._vumeter_bar.setMinimumWidth(200)
        # Reserve the panel's (expanding) space even while it is hidden before
        # recording, so the bar layout stays put — the language selector keeps its
        # place on the right and this slot acts as the placeholder.
        _vu_sp = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _vu_sp.setRetainSizeWhenHidden(True)
        self._vumeter_bar.setSizePolicy(_vu_sp)
        self._vumeter_bar.setStyleSheet(
            "QFrame#vuContainer {"
            "  background-color: #0d1b2a;"
            "  border: 1.5px solid #2c3e50;"
            "  border-radius: 10px;"
            "}"
        )
        self._vumeter_bar.setVisible(False)
        vum_hbox = QHBoxLayout(self._vumeter_bar)
        vum_hbox.setContentsMargins(14, 0, 14, 0)
        vum_hbox.setSpacing(10)

        # ON AIR LED — circular, blue=idle / red=recording
        self._onair_dot = QLabel(self._vumeter_bar)
        self._onair_dot.setFixedSize(22, 22)
        self._onair_dot.setStyleSheet(
            "background-color: #1565C0;"
            "border-radius: 11px;"
            "border: 2px solid rgba(255,255,255,0.25);"
        )
        vum_hbox.addWidget(self._onair_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        # VU meter — click to toggle mic; fills the rest of the panel
        self._vu_meter = VUMeterWidget()
        self._vu_meter.clicked.connect(self._on_vumeter_click)
        vum_hbox.addWidget(self._vu_meter, 1)

        # stretch=1 → the VU panel fills all slack between Start and the language
        # selector (replaces the former fixed-width box + expanding spacer).
        hbox.addWidget(self._vumeter_bar, 1)

        hbox.addWidget(_vsep(bar))

        # Language quick selector
        lang_label = QLabel(t("lang_label"), bar)
        lang_label.setStyleSheet("color: #8B949E; font-size: 11px; background: transparent;")
        hbox.addWidget(lang_label)

        self._quick_lang_combo = QComboBox(bar)
        self._quick_lang_combo.addItems([
            t("lang_auto"), t("lang_zh"), t("lang_ja"), t("lang_en"),
        ])
        self._quick_lang_combo.setMinimumWidth(160)
        cur_lang = self._presenter._config.get("recording", "language", fallback="auto")
        self._quick_lang_combo.setCurrentIndex(
            {"auto": 0, "zh": 1, "ja": 2, "en": 3}.get(cur_lang, 0)
        )
        self._quick_lang_combo.currentIndexChanged.connect(self._on_quick_lang_change)
        hbox.addWidget(self._quick_lang_combo)
        hbox.addSpacing(12)   # right margin so combo doesn't sit at window edge

        return bar

    # ── Settings tabs ─────────────────────────────────────────────────────────

    def _build_left_rail(self, parent: QWidget) -> QFrame:
        rail = QFrame(parent)
        rail.setObjectName("leftRail")
        rail.setFixedWidth(212)
        rail.setStyleSheet(
            "QFrame#leftRail { background-color: #0F141B; border-right: 1px solid #232B36; }"
        )
        vbox = QVBoxLayout(rail)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(15)

        self._onair_panel = QFrame(rail)
        self._onair_panel.setObjectName("onairPanel")
        onair_layout = QHBoxLayout(self._onair_panel)
        onair_layout.setContentsMargins(10, 0, 12, 0)
        onair_layout.setSpacing(0)

        self._onair_label = QLabel(self._mic_state_text(False), self._onair_panel)
        self._onair_label.setStyleSheet(
            "color: #7E8A99; font-size: 11px; font-weight: 700; letter-spacing: 1px; background: transparent;"
        )
        onair_layout.addWidget(self._onair_label)
        onair_layout.addStretch()

        self._onair_dot = QLabel(self._onair_panel)
        self._onair_dot.setFixedSize(11, 11)
        onair_layout.addWidget(self._onair_dot)
        vbox.addWidget(self._onair_panel)

        self._vumeter_bar = QFrame(rail)
        self._vumeter_bar.setObjectName("railVuContainer")
        self._vumeter_bar.setStyleSheet(
            "QFrame#railVuContainer { background-color: #0E1218; border: 1px solid #232B36; border-radius: 7px; }"
        )
        vum_hbox = QHBoxLayout(self._vumeter_bar)
        vum_hbox.setContentsMargins(12, 12, 12, 12)
        vum_hbox.setSpacing(0)

        self._vu_meter = VUBarMeterWidget(self._vumeter_bar)
        self._vu_meter.clicked.connect(self._on_vumeter_click)
        vum_hbox.addWidget(self._vu_meter, 1)

        vbox.addWidget(self._vumeter_bar)

        self._dashboard = CompactDashboardWidget(rail)
        vbox.addWidget(self._dashboard)
        vbox.addStretch()

        self._ptt_visible = True
        self.hide_onair()
        return rail

    def _build_settings_tabs(self, parent: QWidget) -> QTabWidget:
        tabs = QTabWidget(parent)
        tabs.setDocumentMode(True)
        tabs.tabBar().setExpanding(False)
        tabs.addTab(self._build_tab_transcript(tabs), t("tab_transcript"))
        tabs.addTab(self._build_tab_corrected(tabs),  t("tab_corrected"))
        tabs.addTab(self._build_tab_minutes(tabs),    t("tab_minutes"))
        tabs.addTab(self._build_tab_paths(tabs),      t("tab_paths"))
        tabs.addTab(self._build_tab_rec(tabs),        t("tab_rec"))
        tabs.addTab(self._build_tab_api(tabs),        t("tab_api"))
        tabs.addTab(self._build_tab_network(tabs),    t("tab_network"))
        return tabs

    # ── Paths tab ─────────────────────────────────────────────────────────────

    def _build_tab_paths(self, parent: QWidget) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(20, 18, 20, 18)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)
        # Two settings per row: cols 0-2 = label/input/browse, cols 3-5 = same again
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(4, 1)

        cfg = self._presenter._config
        # (row_type, icon, label_key, section, config_key, default_path)
        # row_type: "dir" = directory picker, "file" = file picker
        rows_def = [
            ("dir",  "🔊", "audio_dir",    "paths",   "audio_dir",      r"C:\Users\Public\Sound2Text\audio"),
            ("dir",  "📝", "tr_dir",       "paths",   "transcript_dir", r"C:\Users\Public\Sound2Text\transcript"),
            ("dir",  "✏️", "corr_dir",     "summary", "corrected_dir",  r"C:\Users\Public\Sound2Text\corrected"),
            ("dir",  "📋", "sum_dir",      "summary", "summary_dir",    r"C:\Users\Public\Sound2Text\memo"),
            ("dir",  "🌐", "final_dir",    "summary", "final_corrected_dir", r"C:\Users\Public\Sound2Text\corrected"),
            ("dir",  "🏷️", "term_dir",     "summary", "term_cache_dir", r"C:\Users\Public\Sound2Text\term_cache"),
            ("file", "📖", "vocab_file",   "paths",   "vocab_file",     ""),
            ("file", "📖", "glossary_file","paths",   "glossary_file",  ""),
        ]
        entries: dict = {}
        for idx, (rtype, icon, lbl_key, sec, key, default) in enumerate(rows_def):
            row   = idx
            cbase = 0
            # Icon + label in one widget, left-aligned, fixed width
            label_w = QWidget()
            label_h = QHBoxLayout(label_w)
            label_h.setContentsMargins(0, 0, 0, 0)
            label_h.setSpacing(6)

            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(20)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            icon_lbl.setStyleSheet("font-size: 15px;")
            icon_lbl.hide()

            text_lbl = QLabel(t(lbl_key))
            text_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            label_h.addWidget(icon_lbl)
            label_h.addWidget(text_lbl)
            label_h.addStretch()
            label_w.setFixedWidth(130)
            grid.addWidget(label_w, row, cbase + 0)

            # Path input
            le = QLineEdit(cfg.get(sec, key, fallback=default))
            grid.addWidget(le, row, cbase + 1, 1, 4)
            entries[(sec, key)] = le

            # Browse button
            btn = QPushButton("📂")
            btn.setObjectName("btnBrowse")
            btn.setText({"zh": "浏览", "ja": "参照", "en": "Browse"}.get(_LANG, "Browse"))
            btn.setFixedWidth(72)
            if rtype == "file":
                btn.clicked.connect(lambda _, v=le: self._browse_file(v))
            else:
                btn.clicked.connect(lambda _, v=le: self._browse_dir(v))
            grid.addWidget(btn, row, cbase + 5)

        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _save():
            updates = {k: v.text() for k, v in entries.items()}
            self._presenter.save_config(updates)

        save_btn.clicked.connect(_save)
        save_row = len(rows_def)
        grid.addWidget(save_btn, save_row, 0, 1, 6, Qt.AlignmentFlag.AlignRight)

        return w

    def _browse_dir(self, line_edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "", line_edit.text() or "C:\\"
        )
        if path:
            line_edit.setText(path.replace("/", "\\"))

    def _browse_file(self, line_edit: QLineEdit) -> None:
        import os as _os
        start = _os.path.dirname(line_edit.text()) if line_edit.text() else "C:\\"
        path, _ = QFileDialog.getOpenFileName(
            self, "", start, "Text files (*.txt);;All files (*)"
        )
        if path:
            line_edit.setText(path.replace("/", "\\"))

    # ── Recording tab ─────────────────────────────────────────────────────────

    def _build_tab_rec(self, parent: QWidget) -> QWidget:
        w   = QWidget()
        cfg = self._presenter._config

        # Design-B layout: stacked groups (title → controls → hint), 20px between
        # groups, left-aligned — no right-aligned label column. Everything lives
        # in a max-560px column so the controls don't stretch full width.
        outer = QVBoxLayout(w)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(0)

        col = QWidget()
        col.setMaximumWidth(560)
        groups = QVBoxLayout(col)
        groups.setContentsMargins(0, 0, 0, 0)
        groups.setSpacing(20)
        outer.addWidget(col)
        outer.addStretch(1)   # keep groups compact at the top

        def _title(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet("color: #9FD0FF; font-size: 13px; font-weight: 600; background: transparent;")
            return lbl

        def _hint(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #6B7686; font-size: 11px; font-weight: normal; background: transparent;")
            return lbl

        # ── Device group ──────────────────────────────────────────────────────
        dev_group = QVBoxLayout()
        dev_group.setContentsMargins(0, 0, 0, 0)
        dev_group.setSpacing(8)
        dev_group.addWidget(_title({"zh": "设备选择", "ja": "デバイス選択", "en": "Device selection"}.get(_LANG, "Device selection")))

        dev_row = QHBoxLayout()
        dev_row.setContentsMargins(0, 0, 0, 0)
        dev_row.setSpacing(8)

        self._device_bg = QButtonGroup(w)
        self._device_bg.setExclusive(True)
        cur_device = cfg.get("recording", "device", fallback="auto")
        self._rb_device_auto = self._make_pill(t("device_auto"))
        self._rb_device_cuda = self._make_pill("CUDA (GPU)")
        self._rb_device_cpu  = self._make_pill("CPU")
        self._btn_cuda = self._rb_device_cuda
        for rb in (self._rb_device_auto, self._rb_device_cuda, self._rb_device_cpu):
            self._device_bg.addButton(rb)
            dev_row.addWidget(rb)
        dev_row.addStretch()
        dev_group.addLayout(dev_row)
        dev_group.addWidget(_hint({
            "zh": "选择 CUDA 时按需校验 GPU，失败时自动回退到 CPU。",
            "ja": "CUDA 選択時はオンデマンドで検証し、失敗した場合は自動で CPU に戻します。",
            "en": "CUDA is validated on demand; falls back to CPU on failure.",
        }.get(_LANG, "CUDA is validated on demand; falls back to CPU on failure.")))
        groups.addLayout(dev_group)

        # Free-settings policy: the radios are never disabled. Picking CUDA
        # validates the GPU on demand (busy overlay + revert on failure).
        self._rb_device_cuda.clicked.connect(self._on_cuda_radio_clicked)
        if cur_device == "cuda":
            self._rb_device_cuda.setChecked(True)
        elif cur_device == "cpu":
            self._rb_device_cpu.setChecked(True)
        else:
            self._rb_device_auto.setChecked(True)

        # ── Model group ───────────────────────────────────────────────────────
        model_group = QVBoxLayout()
        model_group.setContentsMargins(0, 0, 0, 0)
        model_group.setSpacing(8)
        model_group.addWidget(_title({"zh": "模型选择", "ja": "モデル選択", "en": "Model selection"}.get(_LANG, "Model selection")))

        # Model selection — pick from the available models only (no free text):
        # the standard faster-whisper sizes plus any local CT2 model directory
        # found under models/. The choice takes effect on the next recording.
        # Rendered as a wrapping grid of pill toggles to match design-B.
        self._model_bg = QButtonGroup(w)
        self._model_bg.setExclusive(True)
        self._model_btns: dict[str, QPushButton] = {}
        self._model_grid = QGridLayout()
        self._model_grid.setContentsMargins(0, 0, 0, 0)
        self._model_grid.setHorizontalSpacing(8)
        self._model_grid.setVerticalSpacing(8)
        self._model_grid.setColumnStretch(self._MODEL_PILL_COLS, 1)  # trailing slack → pills stay left

        models = self._available_models()
        cur_model = cfg.get("recording", "model_size", fallback="small").strip()
        # Keep the currently-configured model selectable even if it is no longer
        # present (e.g. a deleted local dir), so the UI reflects the real setting.
        if cur_model and cur_model not in models:
            models.append(cur_model)
        for name in models:
            self._add_model_pill(name)
        self._select_model_pill(cur_model)
        model_group.addLayout(self._model_grid)
        model_group.addWidget(_hint({
            "zh": "仅可选择本地已存在的模型。",
            "ja": "ローカルに存在するモデルのみ選択できます。",
            "en": "Only locally available models can be selected.",
        }.get(_LANG, "Only locally available models can be selected.")))
        groups.addLayout(model_group)

        # ── Save (left-aligned, per design) ──────────────────────────────────
        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _get_device():
            if self._rb_device_cuda.isChecked():
                return "cuda"
            if self._rb_device_cpu.isChecked():
                return "cpu"
            return "auto"

        def _get_model():
            btn = self._model_bg.checkedButton()
            return btn.text().strip() if btn else "small"

        def _save():
            device = _get_device()
            model  = _get_model()
            # No forced override here: a CUDA selection was already validated when
            # the radio was clicked (see _on_cuda_radio_clicked), so we trust the
            # current radio state and save it verbatim.
            updates = {
                ("recording", "device"):     device,
                ("recording", "model_size"): model,
            }
            self._presenter.save_config(updates)
            self._presenter.apply_startup_defaults(log=True)

        save_btn.clicked.connect(_save)
        save_row = QHBoxLayout()
        save_row.setContentsMargins(0, 0, 0, 0)
        save_row.addWidget(save_btn)
        save_row.addStretch()
        groups.addLayout(save_row)

        return w

    # ── Pill-toggle helpers (device / model selectors) ─────────────────────────
    _MODEL_PILL_COLS = 5   # pills per row before wrapping in the model grid

    @staticmethod
    def _make_pill(text: str) -> QPushButton:
        """A checkable segmented pill button (design-B selector style)."""
        b = QPushButton(text)
        b.setObjectName("pillToggle")
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _add_model_pill(self, name: str) -> None:
        """Create + register a model pill, laid out in a wrapping grid."""
        if name in self._model_btns:
            return
        b = self._make_pill(name)
        self._model_bg.addButton(b)
        self._model_btns[name] = b
        idx = len(self._model_btns) - 1
        cols = self._MODEL_PILL_COLS
        self._model_grid.addWidget(
            b, idx // cols, idx % cols, Qt.AlignmentFlag.AlignLeft
        )

    def _select_model_pill(self, name: str) -> None:
        """Check the pill for `name`, creating it first if it is missing."""
        if name and name not in self._model_btns:
            self._add_model_pill(name)
        b = self._model_btns.get(name)
        if b is not None:
            b.setChecked(True)

    @staticmethod
    def _available_models() -> list[str]:
        """Models the user may select: the standard faster-whisper sizes plus
        any local CT2 model directory (one containing model.bin) under models/."""
        models = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
        models_dir = os.path.join(BASE, "models")
        try:
            for name in sorted(os.listdir(models_dir)):
                path = os.path.join(models_dir, name)
                if (os.path.isdir(path)
                        and os.path.exists(os.path.join(path, "model.bin"))
                        and name not in models):
                    models.append(name)
        except OSError:
            pass
        return models

    # ── Summary / API tab ─────────────────────────────────────────────────────

    def _build_tab_api(self, parent: QWidget) -> QWidget:
        outer = QWidget()
        vbox  = QVBoxLayout(outer)
        vbox.setContentsMargins(20, 18, 20, 18)
        vbox.setSpacing(12)

        cfg          = self._presenter._config
        current_mode = cfg.get("summary", "mode", fallback="openai")

        inner_tabs = QTabWidget(outer)
        inner_tabs.setDocumentMode(True)
        inner_tabs.tabBar().setExpanding(False)
        vbox.addWidget(inner_tabs, 1)

        # ── OpenAI tab ────────────────────────────────────────────────────────
        oa_widget = QWidget()
        oa_grid   = QGridLayout(oa_widget)
        oa_grid.setContentsMargins(0, 14, 0, 10)
        oa_grid.setVerticalSpacing(10)
        oa_grid.setHorizontalSpacing(10)
        oa_grid.setColumnStretch(1, 1)

        self._api_base  = QLineEdit(cfg.get("summary", "api_base",  fallback="https://api.groq.com/openai/v1"))
        self._api_key   = QLineEdit(cfg.get("summary", "api_key",   fallback=""))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_model = QLineEdit(cfg.get("summary", "model",     fallback="llama-3.3-70b-versatile"))

        for i, (lbl_key, le) in enumerate([
            ("api_base",    self._api_base),
            ("api_key",     self._api_key),
            ("cloud_model", self._api_model),
        ]):
            oa_grid.addWidget(QLabel(t(lbl_key)), i, 0, Qt.AlignmentFlag.AlignRight)
            oa_grid.addWidget(le, i, 1)

        oa_grid.addWidget(QLabel(t("presets")), 3, 0, Qt.AlignmentFlag.AlignRight)
        preset_frame = QWidget()
        preset_hbox  = QHBoxLayout(preset_frame)
        preset_hbox.setContentsMargins(0, 0, 0, 0)

        for name, base, model in [
            ("Groq",     "https://api.groq.com/openai/v1",                    "llama-3.3-70b-versatile"),
            ("Cerebras", "https://api.cerebras.ai/v1",                        "llama-3.3-70b"),
            ("DeepSeek", "https://api.deepseek.com/v1",                       "deepseek-chat"),
            ("Aliyun",   "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo"),
        ]:
            btn = QPushButton(name)
            btn.setObjectName("btnPreset")
            btn.clicked.connect(
                lambda _, b=base, m=model: (
                    self._api_base.setText(b),
                    self._api_model.setText(m),
                )
            )
            preset_hbox.addWidget(btn)

        preset_hbox.addStretch()
        oa_grid.addWidget(preset_frame, 3, 1)

        inner_tabs.addTab(oa_widget, "OpenAI")

        # ── Ollama tab ────────────────────────────────────────────────────────
        ol_widget = QWidget()
        ol_grid   = QGridLayout(ol_widget)
        ol_grid.setContentsMargins(0, 14, 0, 10)
        ol_grid.setVerticalSpacing(10)
        ol_grid.setHorizontalSpacing(10)
        ol_grid.setColumnStretch(1, 1)

        self._ollama_url   = QLineEdit(cfg.get("summary", "ollama_url",   fallback="http://localhost:11434"))
        self._ollama_model = QLineEdit(cfg.get("summary", "ollama_model", fallback="qwen2.5:7b"))

        for i, (lbl_key, le) in enumerate([
            ("ollama_url",   self._ollama_url),
            ("ollama_model", self._ollama_model),
        ]):
            ol_grid.addWidget(QLabel(t(lbl_key)), i, 0, Qt.AlignmentFlag.AlignRight)
            ol_grid.addWidget(le, i, 1)

        inner_tabs.addTab(ol_widget, "Ollama")

        # Set active inner tab based on config
        inner_tabs.setCurrentIndex(1 if current_mode == "ollama" else 0)
        self._inner_api_tabs = inner_tabs

        self._online_refine = QCheckBox(t("online_refine"))
        self._online_refine.setChecked(
            cfg.getboolean("summary", "enable_online_refine", fallback=False)
        )
        self._online_refine.setToolTip(t("online_refine_hint"))
        # Persist immediately on toggle so the flag can never be lost by saving a
        # different settings tab (the checkbox lives on this tab only).
        self._online_refine.toggled.connect(
            lambda checked: self._presenter.save_config(
                {("summary", "enable_online_refine"): "true" if checked else "false"})
        )
        vbox.addWidget(self._online_refine)

        # Save button
        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _save():
            mode = "ollama" if inner_tabs.currentIndex() == 1 else "openai"
            updates = {
                ("summary", "mode"):         mode,
                ("summary", "api_base"):     self._api_base.text(),
                ("summary", "api_key"):      self._api_key.text(),
                ("summary", "model"):        self._api_model.text(),
                ("summary", "ollama_url"):   self._ollama_url.text(),
                ("summary", "ollama_model"): self._ollama_model.text(),
                ("summary", "enable_online_refine"):
                    "true" if self._online_refine.isChecked() else "false",
            }
            self._presenter.save_config(updates)
            if mode == "ollama":
                self._presenter.ensure_ollama_running()

        save_btn.clicked.connect(_save)
        vbox.addWidget(save_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        return outer

    # ── Network tab ───────────────────────────────────────────────────────────

    def _build_tab_network(self, parent: QWidget) -> QWidget:
        w   = QWidget()
        cfg = self._presenter._config
        grid = QGridLayout(w)
        grid.setContentsMargins(20, 18, 20, 18)
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(1, 1)

        self._https_proxy = QLineEdit(cfg.get("network", "https_proxy", fallback=""))
        self._http_proxy  = QLineEdit(cfg.get("network", "http_proxy",  fallback=""))

        grid.addWidget(QLabel(t("https_proxy")), 0, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._https_proxy, 0, 1)
        grid.addWidget(QLabel(t("http_proxy")),  1, 0, Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self._http_proxy, 1, 1)

        hint = QLabel(t("proxy_hint"))
        hint.setStyleSheet("color: gray; font-size: 11px;")
        grid.addWidget(hint, 2, 0, 1, 2)

        grid.addWidget(QLabel(t("ssl_verify")), 3, 0, Qt.AlignmentFlag.AlignRight)
        ssl_widget = QWidget()
        ssl_hbox   = QHBoxLayout(ssl_widget)
        ssl_hbox.setContentsMargins(0, 0, 0, 0)

        ssl_group = QButtonGroup(w)
        self._rb_ssl_on  = QRadioButton(t("ssl_on"))
        self._rb_ssl_off = QRadioButton(t("ssl_off"))
        ssl_group.addButton(self._rb_ssl_on)
        ssl_group.addButton(self._rb_ssl_off)
        ssl_hbox.addWidget(self._rb_ssl_on)
        ssl_hbox.addWidget(self._rb_ssl_off)
        ssl_hbox.addStretch()

        ssl_val = cfg.get("network", "ssl_verify", fallback="true")
        if ssl_val.lower() == "false":
            self._rb_ssl_off.setChecked(True)
        else:
            self._rb_ssl_on.setChecked(True)

        grid.addWidget(ssl_widget, 3, 1)

        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _save():
            ssl = "false" if self._rb_ssl_off.isChecked() else "true"
            updates = {
                ("network", "https_proxy"): self._https_proxy.text(),
                ("network", "http_proxy"):  self._http_proxy.text(),
                ("network", "ssl_verify"):  ssl,
            }
            self._presenter.save_config(updates)

        save_btn.clicked.connect(_save)
        grid.addWidget(save_btn, 4, 0, 1, 2, Qt.AlignmentFlag.AlignHCenter)

        return w

    # ── Transcript tab ────────────────────────────────────────────────────────

    def _build_tab_transcript(self, parent: QWidget) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(4)

        self._transcript_view = QTextEdit()
        self._transcript_view.setReadOnly(True)
        self._transcript_view.setPlaceholderText(t("transcript_hint"))
        self._transcript_view.setStyleSheet(
            "QTextEdit { font-family: 'JetBrains Mono', 'Noto Sans JP', 'Meiryo UI', 'Microsoft YaHei';"
            "  font-size: 13px; font-weight: normal; line-height: 1.95; padding: 10px; }"
        )
        vbox.addWidget(self._transcript_view)
        return w

    # ── Corrected tab ─────────────────────────────────────────────────────────

    def _build_tab_corrected(self, parent: QWidget) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(4)

        self._corrected_view = QTextEdit()
        self._corrected_view.setReadOnly(True)
        self._corrected_view.setPlaceholderText(t("corrected_hint"))
        self._corrected_view.setStyleSheet(
            "QTextEdit { font-family: 'JetBrains Mono', 'Noto Sans JP', 'Meiryo UI', 'Microsoft YaHei';"
            "  font-size: 13px; font-weight: normal; line-height: 1.95; padding: 10px; }"
        )
        vbox.addWidget(self._corrected_view)
        return w

    # ── Minutes tab ───────────────────────────────────────────────────────────

    def _build_tab_minutes(self, parent: QWidget) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(4)

        self._minutes_view = QTextBrowser()
        self._minutes_view.setReadOnly(True)
        self._minutes_view.setOpenExternalLinks(True)
        self._minutes_view.setPlaceholderText(t("minutes_hint"))
        self._minutes_view.document().setDefaultStyleSheet(
            """
            body {
                font-family: 'Noto Sans JP', 'Segoe UI', 'Meiryo UI', 'Microsoft YaHei';
                font-size: 13.5px;
                line-height: 1.9;
                color: #DDE3EA;
            }
            h1 {
                font-size: 20px; color: #EEF2F6;
                margin-top: 14px; margin-bottom: 12px;
                border-bottom: 1px solid #232B36; padding-bottom: 10px;
            }
            h2 {
                font-size: 15px; color: #9FD0FF;
                margin-top: 22px; margin-bottom: 10px;
                border-bottom: none; padding-bottom: 0;
            }
            h3 { font-size: 14px; color: #9FD0FF; margin-top: 16px; margin-bottom: 8px; }
            ul, ol { margin-left: 18px; }
            li { margin-top: 3px; margin-bottom: 3px; }
            p { margin-top: 6px; margin-bottom: 6px; }
            a { color: #4AA8FF; }
            code { background-color: #161B22; color: #E6EDF3; padding: 1px 4px; border-radius: 4px; }
            strong { color: #E6EDF3; }
            blockquote { color: #9AA3B2; border-left: 3px solid #2A2F3A; padding-left: 10px; }
            """
        )
        self._minutes_view.setStyleSheet(
            "QTextBrowser {"
            "  font-family: 'Noto Sans JP', 'Segoe UI', 'Meiryo UI', 'Microsoft YaHei';"
            "  font-size: 13.5px;"
            "  line-height: 1.9;"
            "  padding: 22px 28px;"
            "}"
        )
        vbox.addWidget(self._minutes_view)
        return w

    # ── Log area ──────────────────────────────────────────────────────────────

    def _build_log_area(self, parent: QWidget) -> QWidget:
        log_widget = QWidget(parent)
        log_vbox = QVBoxLayout(log_widget)
        log_vbox.setContentsMargins(0, 0, 0, 0)
        log_vbox.setSpacing(0)

        # Header: "Log" title + "Clear Log" button (height: 28px)
        hdr = QWidget()
        hdr.setFixedHeight(28)
        hdr_hbox = QHBoxLayout(hdr)
        hdr_hbox.setContentsMargins(14, 0, 14, 0)
        hdr_hbox.setSpacing(0)
        hdr.setStyleSheet("border-bottom: 1px solid #1a212b; background-color: #0f141b;")

        title_lbl = QLabel(t("log_title"))
        title_lbl.setStyleSheet("font-weight: 600; font-size: 12px; color: #9fd0ff; background: transparent;")
        hdr_hbox.addWidget(title_lbl)
        hdr_hbox.addStretch()

        clear_btn = QPushButton(t("clear_log"))
        clear_btn.setFixedSize(75, 22)
        clear_btn.setStyleSheet(
            "QPushButton { border: 1px solid #2a323e; border-radius: 4px; padding: 4px 9px; "
            "font-size: 11px; font-weight: 500; color: #7e8a99; background: transparent; }"
        )
        clear_btn.clicked.connect(self._clear_log)
        hdr_hbox.addWidget(clear_btn)
        log_vbox.addWidget(hdr)

        # Log content area
        self._log_box = QTextEdit()
        self._log_box.setObjectName("logBox")
        self._log_box.setReadOnly(True)
        self._log_box.setFont(QFont("JetBrains Mono", 11))
        self._log_box.setStyleSheet(
            "QTextEdit#logBox { background-color: #0f141b; border: none; color: #8a94a3; "
            "font-family: 'JetBrains Mono', monospace; font-size: 11px; line-height: 1.7; "
            "padding: 6px 14px; }"
        )
        log_vbox.addWidget(self._log_box)

        return log_widget

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _apply_toggle_style(self, enabled: bool) -> None:
        """Apply green (start) or red (stop) style to the toggle button."""
        rec = self._btn_toggle_recording
        if rec:   # Stop — red
            top,  bot   = "#F85149", "#D32F2C"
            top_h, bot_h = "#FF6A60", "#E5403B"
            bg_d         = "#4A2422"
        else:     # Start — green
            top,  bot   = "#34B14E", "#218A3A"
            top_h, bot_h = "#46C763", "#2A9D45"
            bg_d         = "#1F3D2A"

        def _grad(a: str, b: str) -> str:
            return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                    f"stop:0 {a}, stop:1 {b})")

        self._btn_toggle.setStyleSheet(
            f"QPushButton#btnToggle           {{ background-color: {_grad(top, bot)};   }}"
            f"QPushButton#btnToggle:hover     {{ background-color: {_grad(top_h, bot_h)}; }}"
            f"QPushButton#btnToggle:disabled  {{ background-color: {bg_d}; color: #6B7480; }}"
        )
        self._btn_toggle.setEnabled(enabled)

    def _on_toggle(self) -> None:
        def _run():
            try:
                if self._btn_toggle_recording:
                    self._presenter.stop()
                else:
                    self._presenter.start()
            except Exception as e:
                import traceback
                self.put_log(f"[ERROR] {e}\n{traceback.format_exc()}")
        threading.Thread(target=_run, daemon=True).start()

    def _on_vumeter_click(self) -> None:
        """Toggle mic mixing on/off via On Air button."""
        target = getattr(self._presenter, "toggle_mic_preview", self._presenter.toggle_mic)
        threading.Thread(target=target, daemon=True).start()

    def show_ptt_button(self) -> None:
        """Recording session started; show the mic-off control as the click target."""
        self._ptt_visible = True
        if hasattr(self, "_onair_panel"):
            self._onair_panel.setVisible(True)
        if hasattr(self, "_vumeter_bar"):
            self._vumeter_bar.setVisible(True)
            self._vu_meter.show()
        if hasattr(self, "_top_status"):
            self._top_status.setText(self._status_text("recording"))

    def hide_ptt_button(self) -> None:
        """Hide VU bar when recording stops; stop meter animation."""
        self._ptt_visible = True
        if hasattr(self, "_vumeter_bar"):
            self._vu_meter.set_level(0.0)
            self._vu_meter.show()
            self._vumeter_bar.setVisible(True)
        if hasattr(self, "_onair_panel"):
            self._onair_panel.setVisible(True)
            self.hide_onair()
        if hasattr(self, "_top_status"):
            self._top_status.setText(self._status_text("ready"))

    def _on_quick_lang_change(self, index: int) -> None:
        code = ["auto", "zh", "ja", "en"][index]
        self._presenter.save_config({("recording", "language"): code})

    def _set_quick_lang(self, code: str) -> None:
        self._refresh_lang_segments(code)
        self._presenter.save_config({("recording", "language"): code})

    def _refresh_lang_segments(self, code: str) -> None:
        buttons = getattr(self, "_lang_buttons", {})
        if not buttons:
            return
        for lang, btn in buttons.items():
            active = (lang == code)
            btn.setStyleSheet(
                "QPushButton {"
                f" color: {'#9FD0FF' if active else '#7E8A99'};"
                f" background-color: {'#16324D' if active else 'transparent'};"
                f" border: 1px solid {'#2B6FB8' if active else 'transparent'};"
                " border-radius: 4px;"
                " padding: 7px 10px;"
                " font-size: 12px;"
                f" font-weight: {'600' if active else '500'};"
                "}"
                "QPushButton:hover { background-color: #1A212B; border-color: #2A323E; }"
            )

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _clear_log(self) -> None:
        self._log_box.clear()

    # ── Window close ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()   # graceful shutdown finished → really close
            return
        event.ignore()       # keep window open; presenter finishes background work first
        self._presenter.on_close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from appconfig import AppConfig
    from presenter import Presenter

    app_qt = QApplication(sys.argv)
    _font_family = (
        '"SF Pro Text", "Helvetica Neue", "Hiragino Sans", "PingFang SC"'
        if sys.platform == "darwin" else
        '"Noto Sans JP", "Segoe UI", "Meiryo UI", "Microsoft YaHei"'
    )
    app_qt.setStyleSheet(_STYLESHEET.replace("__FONT_FAMILY__", _font_family))

    config    = AppConfig()
    presenter = Presenter(config)
    window    = App(presenter)
    window.show()
    sys.exit(app_qt.exec())

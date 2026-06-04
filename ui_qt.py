"""
ui_qt.py – PyQt6 View layer for Sound2Text.

Implements ViewProtocol (defined in presenter.py) using PyQt6.
No customtkinter / tkinter imports.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    QMetaObject, QSize, Qt, QTimer, pyqtSignal, pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QRadioButton, QSlider, QSizePolicy,
    QSpacerItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from appconfig import AppConfig
from i18n import _LANG, t
from widgets_qt import DashboardWidget, VUMeterWidget

if TYPE_CHECKING:
    from presenter import Presenter

# ── Global stylesheet ─────────────────────────────────────────────────────────

_STYLESHEET = """
/* ── Base ── */
QWidget {
    font-family: "Segoe UI", "Yu Gothic UI", "PingFang SC", sans-serif;
    font-size: 13px;
}

/* ── Main window / central widget — pure white background ── */
QMainWindow, QWidget#centralWidget {
    background-color: #FFFFFF;
}

/* ── Control bar — blends with window, subtle bottom border ── */
QFrame#controlBar {
    background-color: #FFFFFF;
    border: none;
    border-bottom: 1px solid #E0E0E0;
}

/* ── Start button — pill shaped, bright green ── */
QPushButton#btnStart {
    background-color: #28a745;
    color: white;
    border: none;
    border-radius: 20px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-width: 110px;
    min-height: 40px;
}
QPushButton#btnStart:hover    { background-color: #218838; }
QPushButton#btnStart:disabled { background-color: #7cb990; color: #e0e0e0; }

/* ── Stop button — pill shaped, bright red ── */
QPushButton#btnStop {
    background-color: #dc3545;
    color: white;
    border: none;
    border-radius: 20px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-width: 110px;
    min-height: 40px;
}
QPushButton#btnStop:hover    { background-color: #c82333; }
QPushButton#btnStop:disabled { background-color: #e88a93; color: #e0e0e0; }

/* ── Save button — blue, centered ── */
QPushButton#btnSave {
    background-color: #1976D2;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 6px 18px;
    font-weight: bold;
    min-width: 160px;
    min-height: 36px;
}
QPushButton#btnSave:hover { background-color: #1565C0; }


/* ── Mode segmented control — left button ── */
QPushButton#modeBtnLeft {
    background-color: #FFFFFF;
    color: #424242;
    border: 1px solid #BDBDBD;
    border-right: none;
    border-top-left-radius: 18px;
    border-bottom-left-radius: 18px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 6px 16px;
    min-height: 34px;
    font-size: 13px;
}
QPushButton#modeBtnLeft:checked {
    background-color: #1565C0;
    color: white;
    border-color: #1565C0;
}
QPushButton#modeBtnLeft:hover:!checked {
    background-color: #F5F5F5;
}

/* ── Mode segmented control — right button ── */
QPushButton#modeBtnRight {
    background-color: #FFFFFF;
    color: #424242;
    border: 1px solid #BDBDBD;
    border-top-left-radius: 0px;
    border-bottom-left-radius: 0px;
    border-top-right-radius: 18px;
    border-bottom-right-radius: 18px;
    padding: 6px 16px;
    min-height: 34px;
    font-size: 13px;
}
QPushButton#modeBtnRight:checked {
    background-color: #1565C0;
    color: white;
    border-color: #1565C0;
}
QPushButton#modeBtnRight:hover:!checked {
    background-color: #F5F5F5;
}

/* ── Browse folder button — small, subtle ── */
QPushButton#btnBrowse {
    background-color: #F0F0F0;
    color: #424242;
    border: 1px solid #D0D0D0;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 26px;
}
QPushButton#btnBrowse:hover { background-color: #E0E0E0; }

/* ── Preset buttons ── */
QPushButton#btnPreset {
    background-color: #F0F0F0;
    color: #424242;
    border: 1px solid #D0D0D0;
    border-radius: 5px;
    padding: 4px 10px;
    min-height: 26px;
}
QPushButton#btnPreset:hover { background-color: #E0E0E0; }

/* ── Tab widget — flat style, blue underline for selected ── */
QTabWidget::pane {
    border: none;
    border-top: 1px solid #E0E0E0;
    background-color: #FFFFFF;
}
QTabBar::tab {
    color: #616161;
    padding: 8px 16px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    background: transparent;
}
QTabBar::tab:selected {
    color: #1565C0;
    font-weight: bold;
    border-bottom: 2px solid #1565C0;
}
QTabBar::tab:hover:!selected {
    color: #1976D2;
    border-bottom: 2px solid #BBDEFB;
}

/* ── Line edit ── */
QLineEdit {
    border: 1px solid #BDBDBD;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 28px;
    background-color: #FFFFFF;
    color: #212121;
}
QLineEdit:focus { border-color: #1976D2; }

/* ── Combo box ── */
QComboBox {
    border: 1px solid #BDBDBD;
    border-radius: 5px;
    padding: 3px 8px;
    min-height: 28px;
    background-color: #FFFFFF;
    color: #212121;
}
QComboBox:focus { border-color: #1976D2; }
QComboBox::drop-down {
    border: none;
    width: 20px;
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: #E0E0E0;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #1976D2;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #1976D2;
    border-radius: 2px;
}

/* ── Text edit (log) ── */
QTextEdit#logBox {
    border: 1px solid #E0E0E0;
    border-radius: 5px;
    background-color: #FAFAFA;
    color: #212121;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
}

/* ── Vertical separator lines ── */
QFrame[frameShape="5"] {
    color: #E0E0E0;
}

/* ── Labels — clean dark text ── */
QLabel {
    color: #424242;
}

/* ── Checkboxes and radio buttons ── */
QCheckBox, QRadioButton {
    color: #424242;
}
"""


# ── Separator helper ──────────────────────────────────────────────────────────

def _vsep(parent: QWidget) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# ── App (View) ────────────────────────────────────────────────────────────────

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
        self._gpu_locked_buttons: list[QWidget] = []
        self._btn_cuda: QRadioButton | None = None

        # Window basics
        self.setWindowTitle(t("title"))
        self.resize(1000, 680)
        self.setMinimumSize(860, 560)

        # Wire view before building (so presenter can call us during initialize)
        presenter.set_view(self)

        self._build()
        self._apply_initial_ui_state()

        # Connect signals (thread-safe cross-thread communication)
        self._log_signal.connect(self._log_box.append)
        self._call_signal.connect(lambda fn: fn())

        # Deferred presenter startup
        QTimer.singleShot(100, presenter.initialize)

    # ── ViewProtocol ──────────────────────────────────────────────────────────

    def put_log(self, msg: str) -> None:
        """Thread-safe: emit signal which is connected to log box on main thread."""
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_signal.emit(f"[{ts}] {msg}")

    def schedule(self, fn) -> None:
        """Thread-safe: emit signal so fn runs on the Qt main thread."""
        self._call_signal.emit(fn)

    def set_start_enabled(self, v: bool) -> None:
        self._btn_start.setEnabled(v)
        self.put_log(f"[UI-STATE] Start button: {'enabled' if v else 'disabled'}")

    def set_stop_enabled(self, v: bool) -> None:
        self._btn_stop.setEnabled(v)
        self.put_log(f"[UI-STATE] Stop button: {'enabled' if v else 'disabled'}")

    def show_onair(self) -> None:
        """Mic recording active — dot turns red."""
        self._onair_dot.setStyleSheet("color: #E53935; font-size: 15px;")
        self.put_log("[UI-STATE] ON AIR: recording (red)")

    def hide_onair(self) -> None:
        """Mic recording stopped — dot turns blue."""
        self._onair_dot.setStyleSheet("color: #1565C0; font-size: 15px;")
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
        self.close()

    def lock_to_cpu_tiny(self) -> None:
        if hasattr(self, "_rb_device_cpu"):
            self._rb_device_cpu.setChecked(True)
        if hasattr(self, "_rb_model_tiny"):
            self._rb_model_tiny.setChecked(True)
        for w in self._gpu_locked_buttons:
            w.setEnabled(False)

    def unlock_gpu_buttons(self) -> None:
        for w in self._gpu_locked_buttons:
            w.setEnabled(True)

    def set_cuda_btn_text(self, text: str) -> None:
        if self._btn_cuda is not None:
            self._btn_cuda.setText(text)

    def set_cuda_btn_state(self, enabled: bool) -> None:
        if self._btn_cuda is not None:
            self._btn_cuda.setEnabled(enabled)

    # Status labels are no-ops (like ui.py)
    def set_rec_status(self, text_key: str, color: str) -> None:
        pass

    def set_tr_status(self, text_key: str, color: str) -> None:
        pass

    def set_sum_status(self, text_key: str, color: str) -> None:
        pass

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

        # Model radio
        model = cfg.get("recording", "model_size", fallback="small")
        rb_model = getattr(self, f"_rb_model_{model.replace('-', '_')}", None)
        if rb_model:
            rb_model.setChecked(True)

        # Language radio (recording tab)
        lang = cfg.get("recording", "language", fallback="auto")
        rb_lang = getattr(self, f"_rb_lang_{lang}", None)
        if rb_lang:
            rb_lang.setChecked(True)

        # Mic checkbox
        mic = cfg.getboolean("recording", "enable_mic", fallback=True)
        if hasattr(self, "_mic_check"):
            self._mic_check.setChecked(mic)

        # Chunk duration slider
        try:
            rec_sec = int(cfg.get("recording", "record_sec", fallback="30"))
            if hasattr(self, "_slider_rec_sec"):
                self._slider_rec_sec.setValue(rec_sec)
        except ValueError:
            pass

        # Quick lang combo
        if hasattr(self, "_quick_lang_combo"):
            cur_lang = cfg.get("recording", "language", fallback="auto")
            idx = {"auto": 0, "zh": 1, "ja": 2, "en": 3}.get(cur_lang, 0)
            self._quick_lang_combo.setCurrentIndex(idx)

        # Mode buttons
        rec_mode = cfg.get("recording", "mode", fallback="meeting")
        if rec_mode == "local_mic" and hasattr(self, "_btn_mode_local_mic"):
            self._btn_mode_local_mic.setChecked(True)
            self._btn_mode_meeting.setChecked(False)
        elif hasattr(self, "_btn_mode_meeting"):
            self._btn_mode_meeting.setChecked(True)
            self._btn_mode_local_mic.setChecked(False)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Row 0: control bar (full-width, no padding)
        control_bar = self._build_control_bar(central)
        layout.addWidget(control_bar, 0)

        # Inner content area with padding
        inner = QWidget()
        inner.setObjectName("innerContent")
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(12, 8, 12, 12)
        inner_layout.setSpacing(6)

        # Row 1: settings tabs (stretch=1)
        settings_tabs = self._build_settings_tabs(inner)
        inner_layout.addWidget(settings_tabs, 1)

        # Row 2: log area (stretch=1)
        log_area = self._build_log_area(inner)
        inner_layout.addWidget(log_area, 1)

        layout.addWidget(inner, 1)

    # ── Control bar ───────────────────────────────────────────────────────────

    def _build_control_bar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName("controlBar")
        bar.setFixedHeight(66)

        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(16, 10, 16, 10)
        hbox.setSpacing(8)

        # Start button
        self._btn_start = QPushButton(t("start"), bar)
        self._btn_start.setObjectName("btnStart")
        self._btn_start.clicked.connect(self._on_start)
        hbox.addWidget(self._btn_start)

        # Stop button
        self._btn_stop = QPushButton(t("stop"), bar)
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop)
        hbox.addWidget(self._btn_stop)

        hbox.addWidget(_vsep(bar))

        # Mode toggle buttons — segmented control style
        mode_val = self._presenter._config.get("recording", "mode", fallback="meeting")
        self._btn_mode_meeting   = QPushButton(t("mode_meeting"), bar)
        self._btn_mode_local_mic = QPushButton(t("mode_local_mic"), bar)

        self._btn_mode_meeting.setObjectName("modeBtnLeft")
        self._btn_mode_local_mic.setObjectName("modeBtnRight")

        for btn in (self._btn_mode_meeting, self._btn_mode_local_mic):
            btn.setCheckable(True)
        self._btn_mode_meeting.setChecked(mode_val != "local_mic")
        self._btn_mode_local_mic.setChecked(mode_val == "local_mic")

        mode_group = QButtonGroup(bar)
        mode_group.setExclusive(True)
        mode_group.addButton(self._btn_mode_meeting)
        mode_group.addButton(self._btn_mode_local_mic)
        mode_group.buttonClicked.connect(self._on_mode_change)

        hbox.addWidget(self._btn_mode_meeting)
        hbox.addWidget(self._btn_mode_local_mic)

        # ON AIR dot + VU meter container (hidden until recording starts)
        # Click VU meter → toggle mic recording in both meeting and local_mic modes
        self._vumeter_bar = QWidget(bar)
        self._vumeter_bar.setFixedHeight(40)
        self._vumeter_bar.setFixedWidth(158)
        self._vumeter_bar.setVisible(False)
        vum_hbox = QHBoxLayout(self._vumeter_bar)
        vum_hbox.setContentsMargins(2, 0, 2, 0)
        vum_hbox.setSpacing(6)

        # ON AIR dot: blue = idle, red = mic recording
        self._onair_dot = QLabel("●")
        self._onair_dot.setFixedSize(16, 40)
        self._onair_dot.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._onair_dot.setStyleSheet("color: #1565C0; font-size: 15px;")
        vum_hbox.addWidget(self._onair_dot)

        # VU meter — click to toggle mic
        self._vu_meter = VUMeterWidget()
        self._vu_meter.clicked.connect(self._on_vumeter_click)
        vum_hbox.addWidget(self._vu_meter)

        hbox.addWidget(self._vumeter_bar)

        # Expanding spacer
        hbox.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        hbox.addWidget(_vsep(bar))

        # Language quick selector
        lang_label = QLabel(t("lang_label"), bar)
        lang_label.setStyleSheet("color: #757575; font-size: 11px;")
        hbox.addWidget(lang_label)

        self._quick_lang_combo = QComboBox(bar)
        self._quick_lang_combo.addItems([
            t("lang_auto"), t("lang_zh"), t("lang_ja"), t("lang_en"),
        ])
        self._quick_lang_combo.setFixedWidth(110)
        cur_lang = self._presenter._config.get("recording", "language", fallback="auto")
        self._quick_lang_combo.setCurrentIndex(
            {"auto": 0, "zh": 1, "ja": 2, "en": 3}.get(cur_lang, 0)
        )
        self._quick_lang_combo.currentIndexChanged.connect(self._on_quick_lang_change)
        hbox.addWidget(self._quick_lang_combo)

        return bar

    # ── Settings tabs ─────────────────────────────────────────────────────────

    def _build_settings_tabs(self, parent: QWidget) -> QTabWidget:
        tabs = QTabWidget(parent)
        tabs.addTab(self._build_tab_paths(tabs),   t("tab_paths"))
        tabs.addTab(self._build_tab_rec(tabs),     t("tab_rec"))
        tabs.addTab(self._build_tab_api(tabs),     t("tab_api"))
        tabs.addTab(self._build_tab_network(tabs), t("tab_network"))
        return tabs

    # ── Paths tab ─────────────────────────────────────────────────────────────

    def _build_tab_paths(self, parent: QWidget) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setVerticalSpacing(8)
        grid.setHorizontalSpacing(10)
        grid.setColumnStretch(1, 1)   # col 0=label, col 1=input, col 2=browse

        cfg = self._presenter._config
        # (icon, label_key, section, config_key, default_path)
        rows_def = [
            ("🔊", "audio_dir",  "paths",   "audio_dir",      r"C:\Users\Public\Sound2Text\audio"),
            ("🎙", "mic_dir",    "paths",   "mic_dir",         r"C:\Users\Public\Sound2Text\mic"),
            ("📝", "tr_dir",     "paths",   "transcript_dir",  r"C:\Users\Public\Sound2Text\transcript"),
            ("✏️", "corr_dir",   "summary", "corrected_dir",   r"C:\Users\Public\Sound2Text\corrected"),
            ("📋", "sum_dir",    "summary", "summary_dir",     r"C:\Users\Public\Sound2Text\memo"),
        ]
        entries: dict = {}
        for row, (icon, lbl_key, sec, key, default) in enumerate(rows_def):
            # Icon + label in one widget, left-aligned, fixed width
            label_w = QWidget()
            label_h = QHBoxLayout(label_w)
            label_h.setContentsMargins(0, 0, 0, 0)
            label_h.setSpacing(6)

            icon_lbl = QLabel(icon)
            icon_lbl.setFixedWidth(20)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            icon_lbl.setStyleSheet("font-size: 15px;")

            text_lbl = QLabel(t(lbl_key))
            text_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            label_h.addWidget(icon_lbl)
            label_h.addWidget(text_lbl)
            label_h.addStretch()
            label_w.setFixedWidth(170)
            grid.addWidget(label_w, row, 0)

            # Path input
            le = QLineEdit(cfg.get(sec, key, fallback=default))
            grid.addWidget(le, row, 1)
            entries[(sec, key)] = le

            # Browse button
            btn = QPushButton("📂")
            btn.setObjectName("btnBrowse")
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda _, v=le: self._browse_dir(v))
            grid.addWidget(btn, row, 2)

        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _save():
            updates = {k: v.text() for k, v in entries.items()}
            self._presenter.save_config(updates)

        save_btn.clicked.connect(_save)
        grid.addWidget(save_btn, len(rows_def), 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)

        return w

    def _browse_dir(self, line_edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "", line_edit.text() or "C:\\"
        )
        if path:
            line_edit.setText(path.replace("/", "\\"))

    # ── Recording tab ─────────────────────────────────────────────────────────

    def _build_tab_rec(self, parent: QWidget) -> QWidget:
        w   = QWidget()
        cfg = self._presenter._config
        grid = QGridLayout(w)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setColumnStretch(1, 1)
        row = 0

        # Mic enable checkbox
        grid.addWidget(QLabel(t("mic_enable")), row, 0, Qt.AlignmentFlag.AlignRight)
        self._mic_check = QCheckBox(t("mic_enable_hint"))
        self._mic_check.setChecked(cfg.getboolean("recording", "enable_mic", fallback=True))
        grid.addWidget(self._mic_check, row, 1)
        row += 1

        # Device radio buttons
        grid.addWidget(QLabel(t("device_label")), row, 0, Qt.AlignmentFlag.AlignRight)
        dev_widget = QWidget()
        dev_hbox   = QHBoxLayout(dev_widget)
        dev_hbox.setContentsMargins(0, 0, 0, 0)

        dev_group = QButtonGroup(w)
        cur_device = cfg.get("recording", "device", fallback="auto")

        self._rb_device_auto = QRadioButton(t("device_auto"))
        self._rb_device_cuda = QRadioButton("CUDA (GPU)")
        self._rb_device_cpu  = QRadioButton("CPU")
        self._btn_cuda = self._rb_device_cuda

        for rb in (self._rb_device_auto, self._rb_device_cuda, self._rb_device_cpu):
            dev_group.addButton(rb)
            dev_hbox.addWidget(rb)
        dev_hbox.addStretch()
        grid.addWidget(dev_widget, row, 1)

        # Lock GPU buttons (auto and cuda) until CUDA is confirmed
        self._gpu_locked_buttons.extend([self._rb_device_auto, self._rb_device_cuda])

        # Set initial device radio
        if cur_device == "cuda":
            self._rb_device_cuda.setChecked(True)
        elif cur_device == "cpu":
            self._rb_device_cpu.setChecked(True)
        else:
            self._rb_device_auto.setChecked(True)
        row += 1

        # Model radio buttons
        grid.addWidget(QLabel(t("model_label")), row, 0, Qt.AlignmentFlag.AlignRight)
        model_widget = QWidget()
        model_hbox   = QHBoxLayout(model_widget)
        model_hbox.setContentsMargins(0, 0, 0, 0)

        model_group = QButtonGroup(w)
        cur_model   = cfg.get("recording", "model_size", fallback="small")

        model_rbs = {}
        for val in ("tiny", "small", "medium", "large-v3"):
            rb = QRadioButton(val)
            model_group.addButton(rb)
            model_hbox.addWidget(rb)
            safe_key = val.replace("-", "_")
            setattr(self, f"_rb_model_{safe_key}", rb)
            model_rbs[val] = rb
            if val != "tiny":
                self._gpu_locked_buttons.append(rb)

        model_hbox.addStretch()
        grid.addWidget(model_widget, row, 1)

        rb_cur_model = model_rbs.get(cur_model, model_rbs["small"])
        rb_cur_model.setChecked(True)
        row += 1

        # Language radio buttons
        grid.addWidget(QLabel(t("lang_label")), row, 0, Qt.AlignmentFlag.AlignRight)
        lang_widget = QWidget()
        lang_hbox   = QHBoxLayout(lang_widget)
        lang_hbox.setContentsMargins(0, 0, 0, 0)

        lang_group = QButtonGroup(w)
        cur_lang   = cfg.get("recording", "language", fallback="auto")

        lang_rbs = {}
        for val, lk in [("auto", "lang_auto"), ("zh", "lang_zh"),
                        ("ja", "lang_ja"),     ("en", "lang_en")]:
            rb = QRadioButton(t(lk))
            lang_group.addButton(rb)
            lang_hbox.addWidget(rb)
            setattr(self, f"_rb_lang_{val}", rb)
            lang_rbs[val] = rb

        lang_hbox.addStretch()
        grid.addWidget(lang_widget, row, 1)

        rb_cur_lang = lang_rbs.get(cur_lang, lang_rbs["auto"])
        rb_cur_lang.setChecked(True)
        row += 1

        # Chunk duration slider
        grid.addWidget(QLabel(t("rec_sec")), row, 0, Qt.AlignmentFlag.AlignRight)
        slider_widget = QWidget()
        slider_hbox   = QHBoxLayout(slider_widget)
        slider_hbox.setContentsMargins(0, 0, 0, 0)

        self._slider_rec_sec = QSlider(Qt.Orientation.Horizontal)
        self._slider_rec_sec.setRange(10, 120)
        try:
            self._slider_rec_sec.setValue(
                int(cfg.get("recording", "record_sec", fallback="30"))
            )
        except ValueError:
            self._slider_rec_sec.setValue(30)

        self._slider_lbl = QLabel(f"{self._slider_rec_sec.value()} s")
        self._slider_lbl.setFixedWidth(46)
        self._slider_rec_sec.valueChanged.connect(
            lambda v: self._slider_lbl.setText(f"{v} s")
        )
        slider_hbox.addWidget(self._slider_rec_sec)
        slider_hbox.addWidget(self._slider_lbl)
        grid.addWidget(slider_widget, row, 1)
        row += 1

        # Save button
        save_btn = QPushButton(t("save"))
        save_btn.setObjectName("btnSave")

        def _get_device():
            if self._rb_device_cuda.isChecked():
                return "cuda"
            if self._rb_device_cpu.isChecked():
                return "cpu"
            return "auto"

        def _get_model():
            for val, rb in model_rbs.items():
                if rb.isChecked():
                    return val
            return "small"

        def _get_lang():
            for val, rb in lang_rbs.items():
                if rb.isChecked():
                    return val
            return "auto"

        def _save():
            device = _get_device()
            model  = _get_model()
            if not self._presenter.cuda_available:
                device = "cpu"
                model  = "tiny"
                self._rb_device_cpu.setChecked(True)
                self._rb_model_tiny.setChecked(True)
            updates = {
                ("recording", "record_sec"):  str(self._slider_rec_sec.value()),
                ("recording", "language"):    _get_lang(),
                ("recording", "device"):      device,
                ("recording", "model_size"):  model,
                ("recording", "enable_mic"):  str(self._mic_check.isChecked()),
            }
            self._presenter.save_config(updates)
            self._presenter.apply_startup_defaults(log=True)

        save_btn.clicked.connect(_save)
        grid.addWidget(save_btn, row, 0, 1, 3, Qt.AlignmentFlag.AlignHCenter)

        return w

    # ── Summary / API tab ─────────────────────────────────────────────────────

    def _build_tab_api(self, parent: QWidget) -> QWidget:
        outer = QWidget()
        vbox  = QVBoxLayout(outer)
        vbox.setContentsMargins(8, 8, 8, 8)

        cfg          = self._presenter._config
        current_mode = cfg.get("summary", "mode", fallback="openai")

        inner_tabs = QTabWidget(outer)
        vbox.addWidget(inner_tabs, 1)

        # ── OpenAI tab ────────────────────────────────────────────────────────
        oa_widget = QWidget()
        oa_grid   = QGridLayout(oa_widget)
        oa_grid.setContentsMargins(12, 12, 12, 12)
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
        ol_grid.setContentsMargins(12, 12, 12, 12)
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
        grid.setContentsMargins(12, 12, 12, 12)
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

    # ── Log area ──────────────────────────────────────────────────────────────

    def _build_log_area(self, parent: QWidget) -> QTabWidget:
        log_tabs = QTabWidget(parent)

        tab_dash = {"zh": "⏱  进度", "ja": "⏱  進捗", "en": "⏱  Progress"}.get(_LANG, "⏱  Progress")
        tab_log  = {"zh": "📋  日志", "ja": "📋  ログ", "en": "📋  Log"}.get(_LANG, "📋  Log")

        # ── Dashboard tab ─────────────────────────────────────────────────────
        dash_widget = QWidget()
        dash_layout = QHBoxLayout(dash_widget)
        dash_layout.setContentsMargins(8, 8, 8, 8)
        self._dashboard = DashboardWidget(dash_widget)
        dash_layout.addWidget(self._dashboard, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

        log_tabs.addTab(dash_widget, tab_dash)

        # ── Log tab ───────────────────────────────────────────────────────────
        log_widget = QWidget()
        log_vbox   = QVBoxLayout(log_widget)
        log_vbox.setContentsMargins(6, 6, 6, 6)
        log_vbox.setSpacing(4)

        # Header row with clear button
        hdr = QWidget()
        hdr_hbox = QHBoxLayout(hdr)
        hdr_hbox.setContentsMargins(0, 0, 0, 0)
        title_lbl = QLabel(t("log_title"))
        title_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        hdr_hbox.addWidget(title_lbl)
        hdr_hbox.addStretch()
        clear_btn = QPushButton(t("clear_log"))
        clear_btn.setFixedSize(60, 26)
        clear_btn.clicked.connect(self._clear_log)
        hdr_hbox.addWidget(clear_btn)
        log_vbox.addWidget(hdr)

        self._log_box = QTextEdit()
        self._log_box.setObjectName("logBox")
        self._log_box.setReadOnly(True)
        self._log_box.setFont(QFont("Consolas", 11))
        log_vbox.addWidget(self._log_box)

        log_tabs.addTab(log_widget, tab_log)
        log_tabs.setCurrentIndex(0)

        return log_tabs

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        def _run():
            try:
                self._presenter.start()
            except Exception as e:
                import traceback
                self.put_log(f"[ERROR] Start failed: {e}")
                self.put_log(traceback.format_exc())
        threading.Thread(target=_run, daemon=True).start()

    def _on_stop(self) -> None:
        def _run():
            try:
                self._presenter.stop()
            except Exception as e:
                self.put_log(f"[ERROR] Stop failed: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _on_vumeter_click(self) -> None:
        """Toggle mic recording on/off (works in both meeting and local_mic modes)."""
        threading.Thread(target=self._presenter.toggle_mic, daemon=True).start()

    def show_ptt_button(self) -> None:
        """Show VU meter bar when recording starts (both modes)."""
        if hasattr(self, "_vumeter_bar"):
            self._vumeter_bar.setVisible(True)

    def hide_ptt_button(self) -> None:
        """Hide VU meter bar when recording stops."""
        if hasattr(self, "_vumeter_bar"):
            self._vumeter_bar.setVisible(False)
            self._vu_meter.set_level(0.0)

    def _on_mode_change(self, btn: QPushButton) -> None:
        code = "local_mic" if btn is self._btn_mode_local_mic else "meeting"
        self._presenter.save_config({("recording", "mode"): code})

    def _on_quick_lang_change(self, index: int) -> None:
        code = ["auto", "zh", "ja", "en"][index]
        self._presenter.save_config({("recording", "language"): code})
        # Sync the recording-tab language radio
        rb = getattr(self, f"_rb_lang_{code}", None)
        if rb:
            rb.setChecked(True)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _clear_log(self) -> None:
        self._log_box.clear()

    # ── Window close ─────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        event.ignore()  # prevent immediate close; let presenter decide
        self._presenter.on_close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    from appconfig import AppConfig
    from presenter import Presenter

    app_qt = QApplication(sys.argv)
    app_qt.setStyleSheet(_STYLESHEET)

    config    = AppConfig()
    presenter = Presenter(config)
    window    = App(presenter)
    window.show()
    sys.exit(app_qt.exec())

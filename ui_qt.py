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
    Q_ARG, QMetaObject, QSize, Qt, QTimer, pyqtSlot,
)
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QRadioButton, QSlider, QSizePolicy,
    QSpacerItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from appconfig import AppConfig, _CUDA_AVAILABLE
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

/* ── Main window / frames ── */
QMainWindow, QWidget#centralWidget {
    background-color: palette(window);
}

/* ── Control bar ── */
QFrame#controlBar {
    background-color: palette(base);
    border-radius: 8px;
}

/* ── Start button ── */
QPushButton#btnStart {
    background-color: #28a745;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-width: 110px;
    min-height: 40px;
}
QPushButton#btnStart:hover    { background-color: #1e7e34; }
QPushButton#btnStart:disabled { background-color: #5a9a6e; color: #cccccc; }

/* ── Stop button ── */
QPushButton#btnStop {
    background-color: #dc3545;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 20px;
    font-size: 14px;
    font-weight: bold;
    min-width: 110px;
    min-height: 40px;
}
QPushButton#btnStop:hover    { background-color: #bd2130; }
QPushButton#btnStop:disabled { background-color: #9a5060; color: #cccccc; }

/* ── Save button ── */
QPushButton#btnSave {
    background-color: #1976d2;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 6px 18px;
    font-weight: bold;
    min-width: 140px;
    min-height: 32px;
}
QPushButton#btnSave:hover { background-color: #1565c0; }

/* ── Mode toggle buttons ── */
QPushButton#modeBtn {
    background-color: palette(button);
    color: palette(button-text);
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 5px 12px;
    min-height: 30px;
}
QPushButton#modeBtn:checked {
    background-color: #1976d2;
    color: white;
    border-color: #1565c0;
}
QPushButton#modeBtn:hover:!checked { background-color: palette(midlight); }

/* ── Browse folder button ── */
QPushButton#btnBrowse {
    background-color: palette(mid);
    border: none;
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 26px;
}
QPushButton#btnBrowse:hover { background-color: palette(dark); }

/* ── Preset buttons ── */
QPushButton#btnPreset {
    background-color: palette(mid);
    border: none;
    border-radius: 5px;
    padding: 4px 10px;
    min-height: 26px;
}
QPushButton#btnPreset:hover { background-color: palette(dark); }

/* ── Tab widget ── */
QTabWidget::pane {
    border: 1px solid palette(mid);
    border-radius: 6px;
}
QTabBar::tab {
    color: palette(text);
    padding: 6px 14px;
    margin-right: 2px;
    border: none;
    background: transparent;
}
QTabBar::tab:selected {
    color: #1976d2;
    font-weight: bold;
    border-bottom: 2px solid #1976d2;
}
QTabBar::tab:hover:!selected { color: #1565c0; }

/* ── Line edit / combo box ── */
QLineEdit {
    border: 1px solid palette(mid);
    border-radius: 5px;
    padding: 4px 8px;
    min-height: 28px;
    background-color: palette(base);
}
QLineEdit:focus { border-color: #1976d2; }

QComboBox {
    border: 1px solid palette(mid);
    border-radius: 5px;
    padding: 3px 8px;
    min-height: 28px;
    background-color: palette(base);
}

/* ── Slider ── */
QSlider::groove:horizontal {
    height: 4px;
    background: palette(mid);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #1976d2;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #1976d2;
    border-radius: 2px;
}

/* ── Text edit (log) ── */
QTextEdit#logBox {
    border: 1px solid palette(mid);
    border-radius: 5px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
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

    def __init__(self, presenter: "Presenter"):
        super().__init__()

        self._presenter = presenter
        self._gpu_locked_buttons: list[QWidget] = []
        self._btn_cuda: QRadioButton | None = None

        # Window basics
        self.setWindowTitle(t("title"))
        self.resize(900, 700)
        self.setMinimumSize(700, 560)

        # Wire view before building (so presenter can call us during initialize)
        presenter.set_view(self)

        self._build()
        self._apply_initial_ui_state()

        # Log poll timer
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(100)
        self._log_timer.timeout.connect(lambda: None)  # no-op; logs via invokeMethod
        self._log_timer.start()

        # Deferred presenter startup
        QTimer.singleShot(100, presenter.initialize)

    # ── ViewProtocol ──────────────────────────────────────────────────────────

    def put_log(self, msg: str) -> None:
        """Thread-safe: queues log append via QueuedConnection."""
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {msg}"
        QMetaObject.invokeMethod(
            self, "_append_log",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, line),
        )

    @pyqtSlot(str)
    def _append_log(self, line: str) -> None:
        self._log_box.append(line)

    def schedule(self, fn) -> None:
        QTimer.singleShot(0, fn)

    def set_start_enabled(self, v: bool) -> None:
        self._btn_start.setEnabled(v)

    def set_stop_enabled(self, v: bool) -> None:
        self._btn_stop.setEnabled(v)

    def show_onair(self) -> None:
        self._vu_meter.show()

    def hide_onair(self) -> None:
        self._vu_meter.hide()

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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # Row 0: control bar
        control_bar = self._build_control_bar(central)
        layout.addWidget(control_bar, 0)

        # Row 1: settings tabs (stretch=1)
        settings_tabs = self._build_settings_tabs(central)
        layout.addWidget(settings_tabs, 1)

        # Row 2: log area (stretch=1)
        log_area = self._build_log_area(central)
        layout.addWidget(log_area, 1)

    # ── Control bar ───────────────────────────────────────────────────────────

    def _build_control_bar(self, parent: QWidget) -> QFrame:
        bar = QFrame(parent)
        bar.setObjectName("controlBar")
        bar.setFixedHeight(70)

        hbox = QHBoxLayout(bar)
        hbox.setContentsMargins(12, 8, 12, 8)
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

        # Mode toggle buttons
        mode_val = self._presenter._config.get("recording", "mode", fallback="meeting")
        self._btn_mode_meeting   = QPushButton(t("mode_meeting"), bar)
        self._btn_mode_local_mic = QPushButton(t("mode_local_mic"), bar)
        for btn in (self._btn_mode_meeting, self._btn_mode_local_mic):
            btn.setObjectName("modeBtn")
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

        # VU meter (hidden by default)
        self._vu_meter = VUMeterWidget(bar)
        hbox.addWidget(self._vu_meter)

        # Expanding spacer
        hbox.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        hbox.addWidget(_vsep(bar))

        # Language quick selector
        lang_label = QLabel(t("lang_label"), bar)
        lang_label.setStyleSheet("color: gray; font-size: 11px;")
        hbox.addWidget(lang_label)

        self._quick_lang_combo = QComboBox(bar)
        self._quick_lang_combo.addItems([
            t("lang_auto"), t("lang_zh"), t("lang_ja"), t("lang_en"),
        ])
        self._quick_lang_combo.setFixedWidth(130)
        cur_lang = self._presenter._config.get("recording", "language", fallback="auto")
        self._quick_lang_combo.setCurrentIndex(
            {"auto": 0, "zh": 1, "ja": 2, "en": 3}.get(cur_lang, 0)
        )
        self._quick_lang_combo.currentIndexChanged.connect(self._on_quick_lang_change)
        hbox.addWidget(self._quick_lang_combo)

        hbox.addWidget(_vsep(bar))

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
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setColumnStretch(1, 1)

        cfg = self._presenter._config
        rows_def = [
            ("audio_dir",  "paths",   "audio_dir",      r"C:\Users\Public\Sound2Text\audio"),
            ("mic_dir",    "paths",   "mic_dir",          r"C:\Users\Public\Sound2Text\mic"),
            ("tr_dir",     "paths",   "transcript_dir",   r"C:\Users\Public\Sound2Text\transcript"),
            ("corr_dir",   "summary", "corrected_dir",    r"C:\Users\Public\Sound2Text\corrected"),
            ("sum_dir",    "summary", "summary_dir",      r"C:\Users\Public\Sound2Text\memo"),
        ]
        entries: dict = {}
        for row, (lbl_key, sec, key, default) in enumerate(rows_def):
            grid.addWidget(QLabel(t(lbl_key)), row, 0, Qt.AlignmentFlag.AlignRight)
            le = QLineEdit(cfg.get(sec, key, fallback=default))
            grid.addWidget(le, row, 1)
            entries[(sec, key)] = le

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
            if not _CUDA_AVAILABLE:
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
        threading.Thread(target=self._presenter.start, daemon=True).start()

    def _on_stop(self) -> None:
        threading.Thread(target=self._presenter.stop, daemon=True).start()

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

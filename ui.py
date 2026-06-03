"""
Sound2Text UI — Control panel for recording, transcription and meeting summary.
Startup: python ui.py

Architecture: pure View — no business logic, no subprocess calls.
All actions are delegated to the Presenter.
"""
import queue
import threading
from datetime import datetime

import customtkinter as ctk

from appconfig import AppConfig, _CUDA_AVAILABLE
from i18n import t
from presenter import Presenter
from widgets import DashboardWidget, OnAirWidget

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


# ── App (View) ────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    """
    Pure View.  Owns all CTk widget construction and layout.
    Delegates every user action to self._presenter.
    Implements ViewProtocol so the Presenter can update the UI from threads.
    """

    def __init__(self, presenter: Presenter):
        super().__init__()

        self._presenter = presenter
        self._log_q: queue.Queue = queue.Queue()

        # View-owned state (button/widget references)
        self._gpu_locked_buttons: list = []
        self._btn_cuda = None           # set in _build_tab_rec

        self.title(t("title"))
        self.geometry("900x740")
        self.minsize(720, 580)

        # Wire the presenter to this view
        presenter.set_view(self)

        self._build()
        self._apply_initial_ui_state()
        self._poll_log()

        # Let the presenter do startup checks after the event loop starts
        self.after(100, presenter.initialize)

    # ── ViewProtocol implementation ───────────────────────────────────────────

    def put_log(self, msg: str) -> None:
        """Thread-safe: write to queue; _poll_log drains it on main thread."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_q.put(f"[{ts}] {msg}")

    def schedule(self, fn) -> None:
        """Run *fn* on the UI main thread (safe to call from any thread)."""
        self.after(0, fn)

    def set_start_enabled(self, v: bool) -> None:
        self._btn_start.configure(state="normal" if v else "disabled")

    def set_stop_enabled(self, v: bool) -> None:
        self._btn_stop.configure(state="normal" if v else "disabled")

    def show_onair(self) -> None:
        self._onair.show()

    def hide_onair(self) -> None:
        self._onair.hide()

    def set_onair_level(self, level: float) -> None:
        self._onair.set_level(level)

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
        self.title(title)

    def get_window_title(self) -> str:
        return self.title()

    def lock_to_cpu_tiny(self) -> None:
        """Disable GPU/large-model buttons and fix device+model vars."""
        if hasattr(self, "_device_var"):
            self._device_var.set("cpu")
        if hasattr(self, "_model_var"):
            self._model_var.set("tiny")
        for btn in self._gpu_locked_buttons:
            btn.configure(state="disabled")

    def unlock_gpu_buttons(self) -> None:
        for btn in self._gpu_locked_buttons:
            btn.configure(state="normal")

    def set_cuda_btn_text(self, text: str) -> None:
        if self._btn_cuda is not None:
            self._btn_cuda.configure(text=text)

    def set_cuda_btn_state(self, enabled: bool) -> None:
        if self._btn_cuda is not None:
            self._btn_cuda.configure(state="normal" if enabled else "disabled")

    def set_rec_status(self, text_key: str, color: str) -> None:
        self._lbl_rec.configure(text=t(text_key), text_color=color)

    def set_tr_status(self, text_key: str, color: str) -> None:
        self._lbl_tr.configure(text=t(text_key), text_color=color)

    def set_sum_status(self, text_key: str, color: str) -> None:
        self._lbl_sum.configure(text=t(text_key), text_color=color)

    # ── Apply initial UI state from config ────────────────────────────────────

    def _apply_initial_ui_state(self) -> None:
        """Read config and set all widget vars to their stored values."""
        cfg = self._presenter._config
        if hasattr(self, "_device_var"):
            self._device_var.set(cfg.get("recording", "device", fallback="auto"))
        if hasattr(self, "_model_var"):
            self._model_var.set(cfg.get("recording", "model_size", fallback="small"))
        if hasattr(self, "_lang_var"):
            self._lang_var.set(cfg.get("recording", "language", fallback="auto"))
        if hasattr(self, "_rec_sec"):
            try:
                self._rec_sec.set(int(cfg.get("recording", "record_sec", fallback="30")))
            except ValueError:
                pass
        if hasattr(self, "_mode_var"):
            self._mode_var.set(cfg.get("summary", "mode", fallback="openai"))
        if hasattr(self, "_ssl_var"):
            self._ssl_var.set(cfg.get("network", "ssl_verify", fallback="true"))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_control_bar()
        self._build_settings()
        self._build_log()

    def _build_control_bar(self) -> None:
        bar = ctk.CTkFrame(self, height=80, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        bar.grid_columnconfigure(4, weight=1)

        # Start button
        self._btn_start = ctk.CTkButton(
            bar, text=t("start"), width=130, height=46,
            fg_color="#1a7a1a", hover_color="#155e15",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._on_start,
        )
        self._btn_start.grid(row=0, column=0, padx=(12, 6), pady=12)

        # Stop button
        self._btn_stop = ctk.CTkButton(
            bar, text=t("stop"), width=130, height=46,
            fg_color="#8b1a1a", hover_color="#6e1414",
            font=ctk.CTkFont(size=15, weight="bold"),
            state="disabled",
            command=self._on_stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=6, pady=12)

        # Separator
        ctk.CTkFrame(bar, width=2, height=50, fg_color="gray50").grid(
            row=0, column=2, padx=14
        )

        # Status labels
        status = ctk.CTkFrame(bar, fg_color="transparent")
        status.grid(row=0, column=3, sticky="w")

        for col, lbl_key, attr in [
            (0, "rec_label", "_lbl_rec"),
            (2, "tr_label",  "_lbl_tr"),
            (4, "sum_label", "_lbl_sum"),
        ]:
            ctk.CTkLabel(
                status, text=t(lbl_key), font=ctk.CTkFont(size=12)
            ).grid(row=0, column=col, padx=(0, 4))
            lbl = ctk.CTkLabel(
                status, text=t("idle"), text_color="gray60",
                font=ctk.CTkFont(size=12)
            )
            lbl.grid(row=0, column=col + 1, padx=(0, 18))
            setattr(self, attr, lbl)

        # OnAir widget
        self._onair = OnAirWidget(bar)
        self._onair.grid(row=0, column=4, padx=(0, 6), pady=4, sticky="e")

        # Dashboard
        self._dashboard = DashboardWidget(bar)
        self._dashboard.grid(row=0, column=5, padx=(0, 12), pady=4, sticky="e")

    def _build_settings(self) -> None:
        tabs = ctk.CTkTabview(self, anchor="nw")
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self._build_tab_paths(tabs.add(t("tab_paths")))
        self._build_tab_rec(tabs.add(t("tab_rec")))
        self._build_tab_api(tabs.add(t("tab_api")))
        self._build_tab_network(tabs.add(t("tab_network")))

    def _build_log(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr, text=t("log_title"),
            font=ctk.CTkFont(size=13, weight="bold"), anchor="w"
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            hdr, text=t("clear_log"), width=60, height=24,
            fg_color="gray40", hover_color="gray30",
            command=self._clear_log,
        ).grid(row=0, column=1)

        self._log_box = ctk.CTkTextbox(
            frame, font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 6))

    # ── Settings tabs ─────────────────────────────────────────────────────────

    def _entry_row(self, parent, row: int, label_key: str,
                   section: str, key: str, default: str = "", show=None):
        cfg = self._presenter._config
        ctk.CTkLabel(parent, text=t(label_key), anchor="w", width=170).grid(
            row=row, column=0, sticky="w", padx=(12, 4), pady=7
        )
        var = ctk.StringVar(value=cfg.get(section, key, fallback=default))
        kw  = {"show": show} if show else {}
        ctk.CTkEntry(parent, textvariable=var, width=420, **kw).grid(
            row=row, column=1, sticky="ew", padx=(0, 12), pady=7
        )
        return var

    def _save_btn(self, parent, row: int, entries: dict) -> None:
        def _save():
            updates = {k: (str(v.get()) if isinstance(v, ctk.IntVar) else v.get())
                       for k, v in entries.items()}
            self._presenter.save_config(updates)
        ctk.CTkButton(parent, text=t("save"), width=160, command=_save).grid(
            row=row, column=0, columnspan=2, pady=14
        )

    def _build_tab_paths(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        e = {}
        e[("paths",   "audio_dir")]      = self._entry_row(tab, 0, "audio_dir",  "paths",   "audio_dir",      r"C:\Users\Public\Sound2Text\audio")
        e[("paths",   "transcript_dir")] = self._entry_row(tab, 1, "tr_dir",     "paths",   "transcript_dir", r"C:\Users\Public\Sound2Text\transcript")
        e[("summary", "corrected_dir")]  = self._entry_row(tab, 2, "corr_dir",   "summary", "corrected_dir",  r"C:\Users\Public\Sound2Text\corrected")
        e[("summary", "summary_dir")]    = self._entry_row(tab, 3, "sum_dir",    "summary", "summary_dir",    r"C:\Users\Public\Sound2Text\memo")
        self._save_btn(tab, 4, e)

    def _build_tab_rec(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        cfg = self._presenter._config

        # Device
        ctk.CTkLabel(tab, text=t("device_label"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._device_var = ctk.StringVar(
            value=cfg.get("recording", "device", fallback="auto")
        )
        dev_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dev_frame.grid(row=0, column=1, columnspan=2, sticky="w", pady=7)

        btn_auto = ctk.CTkRadioButton(
            dev_frame, text=t("device_auto"),
            variable=self._device_var, value="auto"
        )
        btn_auto.pack(side="left", padx=(0, 14))

        self._btn_cuda = ctk.CTkRadioButton(
            dev_frame, text="CUDA (GPU)",
            variable=self._device_var, value="cuda"
        )
        self._btn_cuda.pack(side="left", padx=(0, 14))

        btn_cpu = ctk.CTkRadioButton(
            dev_frame, text="CPU",
            variable=self._device_var, value="cpu"
        )
        btn_cpu.pack(side="left")
        self._gpu_locked_buttons.extend([btn_auto, self._btn_cuda])

        # Model
        ctk.CTkLabel(tab, text=t("model_label"), anchor="w", width=170).grid(
            row=1, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._model_var = ctk.StringVar(
            value=cfg.get("recording", "model_size", fallback="small")
        )
        model_frame = ctk.CTkFrame(tab, fg_color="transparent")
        model_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=7)
        for val, desc in [
            ("tiny",     "tiny (高速)"),
            ("small",    "small (推奨)"),
            ("medium",   "medium (高精度)"),
            ("large-v3", "large-v3 (最高精度)"),
        ]:
            btn = ctk.CTkRadioButton(
                model_frame, text=desc, variable=self._model_var, value=val
            )
            btn.pack(side="left", padx=(0, 10))
            if val != "tiny":
                self._gpu_locked_buttons.append(btn)

        # Language
        ctk.CTkLabel(tab, text=t("lang_label"), anchor="w", width=170).grid(
            row=2, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._lang_var = ctk.StringVar(
            value=cfg.get("recording", "language", fallback="auto")
        )
        lang_frame = ctk.CTkFrame(tab, fg_color="transparent")
        lang_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=7)
        for val, lk in [
            ("auto", "lang_auto"), ("zh", "lang_zh"),
            ("ja", "lang_ja"), ("en", "lang_en"),
        ]:
            ctk.CTkRadioButton(
                lang_frame, text=t(lk), variable=self._lang_var, value=val
            ).pack(side="left", padx=(0, 14))

        # Chunk duration
        ctk.CTkLabel(tab, text=t("rec_sec"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._rec_sec = ctk.IntVar(
            value=int(cfg.get("recording", "record_sec", fallback="30"))
        )
        lbl = ctk.CTkLabel(tab, text=f"{self._rec_sec.get()} s", width=50)
        ctk.CTkSlider(
            tab, from_=10, to=120, number_of_steps=11, variable=self._rec_sec,
            command=lambda v: lbl.configure(text=f"{int(v)} s"),
        ).grid(row=3, column=1, sticky="ew", padx=(0, 60), pady=7)
        lbl.grid(row=3, column=2, padx=4)

        def _save():
            updates = {
                ("recording", "record_sec"): str(self._rec_sec.get()),
                ("recording", "language"):   self._lang_var.get(),
                ("recording", "device"):     self._device_var.get(),
                ("recording", "model_size"): self._model_var.get(),
            }
            # Enforce CPU-only if no CUDA
            if not _CUDA_AVAILABLE:
                updates[("recording", "device")]     = "cpu"
                updates[("recording", "model_size")] = "tiny"
                self._device_var.set("cpu")
                self._model_var.set("tiny")
            self._presenter.save_config(updates)
            self._presenter.apply_startup_defaults(log=True)

        ctk.CTkButton(tab, text=t("save"), width=160, command=_save).grid(
            row=4, column=0, columnspan=3, pady=14
        )

    def _build_tab_api(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        cfg = self._presenter._config
        e = {}

        ctk.CTkLabel(tab, text=t("mode_label"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._mode_var = ctk.StringVar(
            value=cfg.get("summary", "mode", fallback="openai")
        )
        mf = ctk.CTkFrame(tab, fg_color="transparent")
        mf.grid(row=0, column=1, sticky="w", pady=7)
        ctk.CTkRadioButton(
            mf, text=t("mode_openai"), variable=self._mode_var, value="openai"
        ).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(
            mf, text=t("mode_ollama"), variable=self._mode_var, value="ollama"
        ).pack(side="left")
        e[("summary", "mode")] = self._mode_var

        e[("summary", "api_base")]     = self._entry_row(tab, 1, "api_base",     "summary", "api_base",     "https://api.groq.com/openai/v1")
        e[("summary", "api_key")]      = self._entry_row(tab, 2, "api_key",      "summary", "api_key",      "", show="*")
        e[("summary", "model")]        = self._entry_row(tab, 3, "cloud_model",  "summary", "model",        "llama-3.3-70b-versatile")
        e[("summary", "ollama_model")] = self._entry_row(tab, 4, "ollama_model", "summary", "ollama_model", "qwen2.5:7b")

        ctk.CTkLabel(tab, text=t("presets"), anchor="w", width=170).grid(
            row=5, column=0, sticky="w", padx=(12, 4), pady=(12, 4)
        )
        pf = ctk.CTkFrame(tab, fg_color="transparent")
        pf.grid(row=5, column=1, sticky="w", pady=(12, 4))
        for name, base, model in [
            ("Groq",     "https://api.groq.com/openai/v1",                    "llama-3.3-70b-versatile"),
            ("DeepSeek", "https://api.deepseek.com/v1",                       "deepseek-chat"),
            ("Aliyun",   "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo"),
        ]:
            def _apply(b=base, m=model):
                e[("summary", "api_base")].set(b)
                e[("summary", "model")].set(m)
                self._mode_var.set("openai")
            ctk.CTkButton(
                pf, text=name, width=90, height=28,
                fg_color="gray40", hover_color="gray30",
                command=_apply,
            ).pack(side="left", padx=4)

        self._save_btn(tab, 6, e)

    def _build_tab_network(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        cfg = self._presenter._config
        e = {}

        e[("network", "https_proxy")] = self._entry_row(tab, 0, "https_proxy", "network", "https_proxy", "")
        e[("network", "http_proxy")]  = self._entry_row(tab, 1, "http_proxy",  "network", "http_proxy",  "")

        ctk.CTkLabel(
            tab, text=t("proxy_hint"), text_color="gray60",
            font=ctk.CTkFont(size=11), anchor="w"
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=(12, 4), pady=(0, 8))

        ctk.CTkLabel(tab, text=t("ssl_verify"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7
        )
        self._ssl_var = ctk.StringVar(
            value=cfg.get("network", "ssl_verify", fallback="true")
        )
        ssl_frame = ctk.CTkFrame(tab, fg_color="transparent")
        ssl_frame.grid(row=3, column=1, sticky="w", pady=7)
        ctk.CTkRadioButton(
            ssl_frame, text=t("ssl_on"),
            variable=self._ssl_var, value="true"
        ).pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(
            ssl_frame, text=t("ssl_off"),
            variable=self._ssl_var, value="false"
        ).pack(side="left")
        e[("network", "ssl_verify")] = self._ssl_var

        self._save_btn(tab, 4, e)

    # ── Button callbacks ──────────────────────────────────────────────────────

    def _on_start(self) -> None:
        threading.Thread(target=self._presenter.start, daemon=True).start()

    def _on_stop(self) -> None:
        threading.Thread(target=self._presenter.stop, daemon=True).start()

    # ── Log ───────────────────────────────────────────────────────────────────

    def _poll_log(self) -> None:
        while not self._log_q.empty():
            msg = self._log_q.get_nowait()
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(100, self._poll_log)

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    config    = AppConfig()
    presenter = Presenter(config)
    app       = App(presenter)

    _close_count = [0]

    def _on_close():
        _close_count[0] += 1
        if _close_count[0] >= 2:
            presenter.force_quit()
        else:
            presenter.on_close()

    app.protocol("WM_DELETE_WINDOW", _on_close)
    app.mainloop()

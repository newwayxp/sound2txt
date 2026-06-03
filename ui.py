"""
Sound2Text UI — Pure View layer.
No business logic, no subprocess calls.
All actions are delegated to the Presenter.
"""
import queue
import threading
from datetime import datetime

import customtkinter as ctk

from appconfig import AppConfig, _CUDA_AVAILABLE
from i18n import _LANG, t
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

        # View-owned widget state
        self._gpu_locked_buttons: list = []
        self._btn_cuda = None

        self.title(t("title"))
        self.geometry("860x680")
        self.minsize(700, 560)

        presenter.set_view(self)

        self._build()
        self._apply_initial_ui_state()
        self._poll_log()

        # Let the presenter run startup checks after the event loop starts
        self.after(100, presenter.initialize)

    # ── ViewProtocol ──────────────────────────────────────────────────────────

    def put_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_q.put(f"[{ts}] {msg}")

    def schedule(self, fn) -> None:
        self.after(0, fn)

    def set_start_enabled(self, v: bool) -> None:
        self._btn_start.configure(state="normal" if v else "disabled")

    def set_stop_enabled(self, v: bool) -> None:
        self._btn_stop.configure(state="normal" if v else "disabled")

    def show_onair(self) -> None:      self._onair.show()
    def hide_onair(self) -> None:      self._onair.hide()
    def set_onair_level(self, l: float): self._onair.set_level(l)

    def dashboard_start(self)             -> None:  self._dashboard.start()
    def dashboard_stop(self)              -> None:  self._dashboard.stop()
    def dashboard_reset(self)             -> None:  self._dashboard.reset()
    def dashboard_add_audio(self, s: float): self._dashboard.add_audio(s)
    def dashboard_add_trans(self, s: float): self._dashboard.add_trans(s)

    def set_window_title(self, title: str) -> None: self.title(title)
    def get_window_title(self)             -> str:  return self.title()
    def destroy(self)                      -> None:  super().destroy()

    def lock_to_cpu_tiny(self) -> None:
        if hasattr(self, "_device_var"): self._device_var.set("cpu")
        if hasattr(self, "_model_var"):  self._model_var.set("tiny")
        for btn in self._gpu_locked_buttons:
            btn.configure(state="disabled")

    def unlock_gpu_buttons(self) -> None:
        for btn in self._gpu_locked_buttons:
            btn.configure(state="normal")

    def set_cuda_btn_text(self, text: str) -> None:
        if self._btn_cuda: self._btn_cuda.configure(text=text)

    def set_cuda_btn_state(self, enabled: bool) -> None:
        if self._btn_cuda:
            self._btn_cuda.configure(state="normal" if enabled else "disabled")

    # Status labels are hidden – kept as dummy targets so presenter can still call
    def set_rec_status(self, text_key: str, color: str) -> None: pass
    def set_tr_status(self,  text_key: str, color: str) -> None: pass
    def set_sum_status(self, text_key: str, color: str) -> None: pass

    # ── Initial UI state from config ──────────────────────────────────────────

    def _apply_initial_ui_state(self) -> None:
        cfg = self._presenter._config
        if hasattr(self, "_device_var"):
            self._device_var.set(cfg.get("recording", "device",     fallback="auto"))
        if hasattr(self, "_model_var"):
            self._model_var.set(cfg.get("recording", "model_size",  fallback="small"))
        if hasattr(self, "_lang_var"):
            self._lang_var.set(cfg.get("recording", "language",     fallback="auto"))
        if hasattr(self, "_mic_var"):
            self._mic_var.set(cfg.getboolean("recording", "enable_mic", fallback=True))
        if hasattr(self, "_rec_sec"):
            try:
                self._rec_sec.set(int(cfg.get("recording", "record_sec", fallback="30")))
            except ValueError:
                pass
        if hasattr(self, "_quick_lang_var"):
            lang = cfg.get("recording", "language", fallback="auto")
            lv   = {"auto": t("lang_auto"), "zh": t("lang_zh"),
                    "ja": t("lang_ja"),     "en": t("lang_en")}
            self._quick_lang_var.set(lv.get(lang, t("lang_auto")))
        if hasattr(self, "_mode_seg"):
            mode_val = cfg.get("recording", "mode", fallback="meeting")
            self._mode_seg.set(t(f"mode_{mode_val}"))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_bar()
        self._build_settings()
        self._build_log()

    # ── Control bar ───────────────────────────────────────────────────────────

    def _build_bar(self) -> None:
        bar = ctk.CTkFrame(self, height=70, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        bar.grid_columnconfigure(7, weight=1)

        # Start
        self._btn_start = ctk.CTkButton(
            bar, text=t("start"), width=120, height=44,
            fg_color="#28a745", hover_color="#1e7e34", text_color="white",
            font=ctk.CTkFont(size=15, weight="bold"), corner_radius=8,
            command=self._on_start,
        )
        self._btn_start.grid(row=0, column=0, padx=(12, 6), pady=12)

        # Stop
        self._btn_stop = ctk.CTkButton(
            bar, text=t("stop"), width=120, height=44,
            fg_color="#dc3545", hover_color="#bd2130", text_color="white",
            font=ctk.CTkFont(size=15, weight="bold"), corner_radius=8,
            state="disabled", command=self._on_stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=6, pady=12)

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(
            row=0, column=2, padx=10)

        # Mode selector
        mode_val = self._presenter._config.get("recording", "mode", fallback="meeting")
        self._mode_seg = ctk.CTkSegmentedButton(
            bar,
            values=[t("mode_meeting"), t("mode_local_mic")],
            command=self._on_mode_change,
            width=210, height=34,
            font=ctk.CTkFont(size=12),
        )
        self._mode_seg.set(t(f"mode_{mode_val}"))
        self._mode_seg.grid(row=0, column=3, padx=(0, 6), pady=12)

        # OnAir VU meter (hidden by default, shown when mic is active)
        self._onair = OnAirWidget(bar)

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(
            row=0, column=6, padx=6)

        # Language quick selector
        lang_frame = ctk.CTkFrame(bar, fg_color="transparent")
        lang_frame.grid(row=0, column=5, padx=(0, 10), pady=12)
        ctk.CTkLabel(lang_frame, text=t("lang_label"),
                     font=ctk.CTkFont(size=11), text_color="gray70").grid(
            row=0, column=0, padx=(0, 6))
        lang_values = {
            "auto": t("lang_auto"), "zh": t("lang_zh"),
            "ja":   t("lang_ja"),   "en": t("lang_en"),
        }
        cur_lang = self._presenter._config.get("recording", "language", fallback="auto")
        self._quick_lang_var = ctk.StringVar(
            value=lang_values.get(cur_lang, t("lang_auto")))
        self._lang_menu = ctk.CTkOptionMenu(
            lang_frame,
            values=list(lang_values.values()),
            variable=self._quick_lang_var,
            width=140, height=34,
            command=self._on_quick_lang_change,
        )
        self._lang_menu.grid(row=0, column=1)
        self._lang_code_map = {v: k for k, v in lang_values.items()}

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(
            row=0, column=6, padx=10)

        # Status label refs (hidden — dashboard covers this info)
        _hidden = ctk.CTkFrame(bar, fg_color="transparent")
        for attr in ("_lbl_rec", "_lbl_tr", "_lbl_sum"):
            setattr(self, attr, ctk.CTkLabel(_hidden, text=""))

    # ── Settings tabs ─────────────────────────────────────────────────────────

    def _build_settings(self) -> None:
        _tab_style = dict(
            segmented_button_fg_color=("gray85", "gray22"),
            segmented_button_selected_color=("#1976d2", "#1976d2"),
            segmented_button_selected_hover_color=("#1565c0", "#1565c0"),
            segmented_button_unselected_hover_color=("gray78", "gray32"),
            text_color=("gray10", "gray90"),
        )
        tabs = ctk.CTkTabview(self, anchor="nw", **_tab_style)
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self._build_tab_paths(tabs.add(t("tab_paths")))
        self._build_tab_rec(tabs.add(t("tab_rec")))
        self._build_tab_api(tabs.add(t("tab_api")))
        self._build_tab_network(tabs.add(t("tab_network")))

    def _entry_row(self, parent, row: int, label_key: str,
                   section: str, key: str, default: str = "", show=None):
        cfg = self._presenter._config
        ctk.CTkLabel(parent, text=t(label_key), anchor="w", width=170).grid(
            row=row, column=0, sticky="w", padx=(12, 4), pady=7)
        var = ctk.StringVar(value=cfg.get(section, key, fallback=default))
        kw  = {"show": show} if show else {}
        ctk.CTkEntry(parent, textvariable=var, width=380, **kw).grid(
            row=row, column=1, sticky="ew", padx=(0, 4), pady=7)
        return var

    def _save_btn(self, parent, row: int, entries: dict) -> None:
        def _save():
            updates = {k: (str(v.get()) if isinstance(v, ctk.IntVar) else v.get())
                       for k, v in entries.items()}
            self._presenter.save_config(updates)
        ctk.CTkButton(parent, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=row, column=0, columnspan=3, pady=14)

    def _build_tab_paths(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        e = {}
        rows = [
            (0, "audio_dir",  "paths",   "audio_dir",      r"C:\Users\Public\Sound2Text\audio"),
            (1, "mic_dir",    "paths",   "mic_dir",         r"C:\Users\Public\Sound2Text\mic"),
            (2, "tr_dir",     "paths",   "transcript_dir",  r"C:\Users\Public\Sound2Text\transcript"),
            (3, "corr_dir",   "summary", "corrected_dir",   r"C:\Users\Public\Sound2Text\corrected"),
            (4, "sum_dir",    "summary", "summary_dir",     r"C:\Users\Public\Sound2Text\memo"),
        ]
        for row, lbl_key, sec, key, default in rows:
            var = self._entry_row(tab, row, lbl_key, sec, key, default)
            e[(sec, key)] = var
            def _pick(v=var):
                import tkinter.filedialog as fd
                path = fd.askdirectory(initialdir=v.get() or "C:\\")
                if path:
                    v.set(path.replace("/", "\\"))
            ctk.CTkButton(tab, text="📂", width=36, height=28,
                          fg_color="gray40", hover_color="gray30",
                          command=_pick).grid(row=row, column=2, padx=(4, 8), pady=7)
        self._save_btn(tab, 5, e)

    def _build_tab_rec(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        cfg = self._presenter._config

        # Mic enable
        ctk.CTkLabel(tab, text=t("mic_enable"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7)
        self._mic_var = ctk.BooleanVar(
            value=cfg.getboolean("recording", "enable_mic", fallback=True))
        mic_frame = ctk.CTkFrame(tab, fg_color="transparent")
        mic_frame.grid(row=0, column=1, sticky="w", pady=7)
        ctk.CTkSwitch(mic_frame, text=t("mic_enable_hint"),
                      variable=self._mic_var,
                      onvalue=True, offvalue=False).pack(side="left")

        # Device
        ctk.CTkLabel(tab, text=t("device_label"), anchor="w", width=170).grid(
            row=1, column=0, sticky="w", padx=(12, 4), pady=7)
        self._device_var = ctk.StringVar(
            value=cfg.get("recording", "device", fallback="auto"))
        dev_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dev_frame.grid(row=1, column=1, sticky="w", pady=7)
        btn_auto = ctk.CTkRadioButton(dev_frame, text=t("device_auto"),
                                      variable=self._device_var, value="auto")
        btn_auto.pack(side="left", padx=(0, 14))
        self._btn_cuda = ctk.CTkRadioButton(dev_frame, text="CUDA (GPU)",
                                            variable=self._device_var, value="cuda")
        self._btn_cuda.pack(side="left", padx=(0, 14))
        ctk.CTkRadioButton(dev_frame, text="CPU",
                           variable=self._device_var, value="cpu").pack(side="left")
        self._gpu_locked_buttons.extend([btn_auto, self._btn_cuda])

        # Model
        ctk.CTkLabel(tab, text=t("model_label"), anchor="w", width=170).grid(
            row=2, column=0, sticky="w", padx=(12, 4), pady=7)
        self._model_var = ctk.StringVar(
            value=cfg.get("recording", "model_size", fallback="small"))
        model_frame = ctk.CTkFrame(tab, fg_color="transparent")
        model_frame.grid(row=2, column=1, sticky="w", pady=7)
        for val, desc in [("tiny",     "tiny"),
                          ("small",    "small"),
                          ("medium",   "medium"),
                          ("large-v3", "large-v3")]:
            btn = ctk.CTkRadioButton(model_frame, text=desc,
                                     variable=self._model_var, value=val)
            btn.pack(side="left", padx=(0, 10))
            if val != "tiny":
                self._gpu_locked_buttons.append(btn)

        # Language
        ctk.CTkLabel(tab, text=t("lang_label"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7)
        self._lang_var = ctk.StringVar(
            value=cfg.get("recording", "language", fallback="auto"))
        lang_frame = ctk.CTkFrame(tab, fg_color="transparent")
        lang_frame.grid(row=3, column=1, sticky="w", pady=7)
        for val, lk in [("auto","lang_auto"),("zh","lang_zh"),
                        ("ja","lang_ja"),("en","lang_en")]:
            ctk.CTkRadioButton(lang_frame, text=t(lk),
                               variable=self._lang_var, value=val
                               ).pack(side="left", padx=(0, 14))

        # Chunk duration
        ctk.CTkLabel(tab, text=t("rec_sec"), anchor="w", width=170).grid(
            row=4, column=0, sticky="w", padx=(12, 4), pady=7)
        self._rec_sec = ctk.IntVar(
            value=int(cfg.get("recording", "record_sec", fallback="30")))
        lbl = ctk.CTkLabel(tab, text=f"{self._rec_sec.get()} s", width=50)
        ctk.CTkSlider(tab, from_=10, to=120, number_of_steps=11,
                      variable=self._rec_sec,
                      command=lambda v: lbl.configure(text=f"{int(v)} s")
                      ).grid(row=4, column=1, sticky="ew", padx=(0, 60), pady=7)
        lbl.grid(row=4, column=2, padx=4)

        def _save():
            updates = {
                ("recording", "record_sec"):  str(self._rec_sec.get()),
                ("recording", "language"):    self._lang_var.get(),
                ("recording", "device"):      self._device_var.get(),
                ("recording", "model_size"):  self._model_var.get(),
                ("recording", "enable_mic"):  str(self._mic_var.get()),
            }
            if not _CUDA_AVAILABLE:
                updates[("recording", "device")]    = "cpu"
                updates[("recording", "model_size")] = "tiny"
                self._device_var.set("cpu")
                self._model_var.set("tiny")
            self._presenter.save_config(updates)
            self._presenter.apply_startup_defaults(log=True)

        ctk.CTkButton(tab, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=5, column=0, columnspan=3, pady=14)

    def _build_tab_api(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        cfg = self._presenter._config

        current_mode     = cfg.get("summary", "mode", fallback="openai")
        tab_label_openai = t("mode_openai")
        tab_label_ollama = t("mode_ollama")

        _tab_style = dict(
            segmented_button_fg_color=("gray85", "gray22"),
            segmented_button_selected_color=("#1976d2", "#1976d2"),
            segmented_button_selected_hover_color=("#1565c0", "#1565c0"),
            segmented_button_unselected_hover_color=("gray78", "gray32"),
            text_color=("gray10", "gray90"),
        )
        inner = ctk.CTkTabview(tab, **_tab_style)
        inner.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        inner.add(tab_label_openai)
        inner.add(tab_label_ollama)

        # OpenAI tab
        oa = inner.tab(tab_label_openai)
        oa.grid_columnconfigure(1, weight=1)
        e = {}
        e[("summary", "api_base")] = self._entry_row(oa, 0, "api_base",    "summary", "api_base",  "https://api.groq.com/openai/v1")
        e[("summary", "api_key")]  = self._entry_row(oa, 1, "api_key",     "summary", "api_key",   "", show="*")
        e[("summary", "model")]    = self._entry_row(oa, 2, "cloud_model", "summary", "model",     "llama-3.3-70b-versatile")

        ctk.CTkLabel(oa, text=t("presets"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=(12, 4))
        pf = ctk.CTkFrame(oa, fg_color="transparent")
        pf.grid(row=3, column=1, sticky="w", pady=(12, 4))
        for name, base, model in [
            ("Groq",     "https://api.groq.com/openai/v1",                    "llama-3.3-70b-versatile"),
            ("DeepSeek", "https://api.deepseek.com/v1",                       "deepseek-chat"),
            ("Aliyun",   "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo"),
        ]:
            def _apply(b=base, m=model):
                e[("summary","api_base")].set(b)
                e[("summary","model")].set(m)
                inner.set(tab_label_openai)
            ctk.CTkButton(pf, text=name, width=90, height=28,
                          fg_color="gray40", hover_color="gray30",
                          command=_apply).pack(side="left", padx=4)

        # Ollama tab
        ol = inner.tab(tab_label_ollama)
        ol.grid_columnconfigure(1, weight=1)
        e[("summary", "ollama_url")]   = self._entry_row(ol, 0, "ollama_url",   "summary", "ollama_url",   "http://localhost:11434")
        e[("summary", "ollama_model")] = self._entry_row(ol, 1, "ollama_model", "summary", "ollama_model", "qwen2.5:7b")

        inner.set(tab_label_ollama if current_mode == "ollama" else tab_label_openai)

        def _save():
            mode = "ollama" if inner.get() == tab_label_ollama else "openai"
            updates = {("summary", "mode"): mode}
            updates.update({k: v.get() for k, v in e.items()})
            self._presenter.save_config(updates)
            if mode == "ollama":
                self._presenter.ensure_ollama_running()
        ctk.CTkButton(tab, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=1, column=0, pady=14)

    def _build_tab_network(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        cfg = self._presenter._config
        e = {}
        e[("network", "https_proxy")] = self._entry_row(tab, 0, "https_proxy", "network", "https_proxy", "")
        e[("network", "http_proxy")]  = self._entry_row(tab, 1, "http_proxy",  "network", "http_proxy",  "")
        ctk.CTkLabel(tab, text=t("proxy_hint"), text_color="gray60",
                     font=ctk.CTkFont(size=11), anchor="w").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(12, 4), pady=(0, 8))
        ctk.CTkLabel(tab, text=t("ssl_verify"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7)
        self._ssl_var = ctk.StringVar(
            value=cfg.get("network", "ssl_verify", fallback="true"))
        ssl_frame = ctk.CTkFrame(tab, fg_color="transparent")
        ssl_frame.grid(row=3, column=1, sticky="w", pady=7)
        ctk.CTkRadioButton(ssl_frame, text=t("ssl_on"),
                           variable=self._ssl_var, value="true").pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(ssl_frame, text=t("ssl_off"),
                           variable=self._ssl_var, value="false").pack(side="left")
        e[("network", "ssl_verify")] = self._ssl_var
        self._save_btn(tab, 4, e)

    # ── Log area (tabview: Progress + Log) ────────────────────────────────────

    def _build_log(self) -> None:
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        tab_dash = {"zh": "⏱  进度", "ja": "⏱  進捗", "en": "⏱  Progress"}.get(_LANG, "⏱  Progress")
        tab_log  = {"zh": "📋  日志", "ja": "📋  ログ", "en": "📋  Log"     }.get(_LANG, "📋  Log")

        _tab_style = dict(
            segmented_button_fg_color=("gray85", "gray22"),
            segmented_button_selected_color=("#1976d2", "#1976d2"),
            segmented_button_selected_hover_color=("#1565c0", "#1565c0"),
            segmented_button_unselected_hover_color=("gray78", "gray32"),
            text_color=("gray10", "gray90"),
        )
        inner = ctk.CTkTabview(frame, anchor="nw", **_tab_style)
        inner.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        inner.add(tab_dash)
        inner.add(tab_log)

        # Progress tab — 3 LED timers
        dt = inner.tab(tab_dash)
        dt.grid_columnconfigure(0, weight=1)
        dt.grid_rowconfigure(0, weight=1)
        self._dashboard = DashboardWidget(dt)
        self._dashboard.grid(row=0, column=0, pady=12)

        # Log tab
        lt = inner.tab(tab_log)
        lt.grid_columnconfigure(0, weight=1)
        lt.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(lt, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text=t("log_title"),
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w").grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text=t("clear_log"), width=60, height=24,
                      fg_color="gray40", hover_color="gray30",
                      command=self._clear_log).grid(row=0, column=1)

        self._log_box = ctk.CTkTextbox(
            lt, font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word")
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))

        inner.set(tab_dash)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_start(self) -> None:
        threading.Thread(target=self._presenter.start, daemon=True).start()

    def _on_stop(self) -> None:
        threading.Thread(target=self._presenter.stop, daemon=True).start()

    def _on_mode_change(self, selected_label: str) -> None:
        code = "local_mic" if selected_label == t("mode_local_mic") else "meeting"
        self._presenter.save_config({("recording", "mode"): code})

    def _on_quick_lang_change(self, selected_label: str) -> None:
        code = self._lang_code_map.get(selected_label, "auto")
        self._presenter.save_config({("recording", "language"): code})
        if hasattr(self, "_lang_var"):
            self._lang_var.set(code)

    # ── Log helpers ───────────────────────────────────────────────────────────

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

"""
Sound2Text UI — Control panel for recording, transcription and meeting summary.
Startup: python ui.py
"""
import locale
import os
import sys
import queue
import threading
import subprocess
import configparser
import customtkinter as ctk

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

BASE        = os.path.dirname(os.path.abspath(__file__))
CFG_FILE    = os.path.join(BASE, "config.ini")
STOP_SIGNAL = os.path.join(BASE, ".stop_signal")
STATE_FILE  = os.path.join(BASE, ".last_transcript")

# ── i18n ─────────────────────────────────────────────────────────────────────

def _ui_lang() -> str:
    lang = (locale.getdefaultlocale()[0] or "en").lower()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith("ja"):
        return "ja"
    return "en"

_LANG = _ui_lang()

_T: dict[str, dict[str, str]] = {
    "title":         {"zh": "Sound2Text 控制台",          "ja": "Sound2Text コントロール",       "en": "Sound2Text Control Panel"},
    "start":         {"zh": "▶  开始",                    "ja": "▶  開始",                       "en": "▶  Start"},
    "stop":          {"zh": "■  停止",                    "ja": "■  停止",                       "en": "■  Stop"},
    "rec_label":     {"zh": "录音",                       "ja": "録音",                           "en": "Rec"},
    "tr_label":      {"zh": "转写",                       "ja": "転写",                           "en": "Trans"},
    "sum_label":     {"zh": "纪要",                       "ja": "纪要",                           "en": "Summary"},
    "idle":          {"zh": "● 停止",                     "ja": "● 停止",                        "en": "● Idle"},
    "running":       {"zh": "● 运行中",                   "ja": "● 実行中",                       "en": "● Running"},
    "stopped":       {"zh": "● 停止",                     "ja": "● 停止",                        "en": "● Stopped"},
    "generating":    {"zh": "● 生成中",                   "ja": "● 生成中",                       "en": "● Generating"},
    "done":          {"zh": "● 完成",                     "ja": "● 完了",                        "en": "● Done"},
    "skipped":       {"zh": "● 跳过",                     "ja": "● スキップ",                     "en": "● Skipped"},
    "standby":       {"zh": "● 待机",                     "ja": "● 待機",                        "en": "● Standby"},
    "tab_paths":     {"zh": "📁  路径",                   "ja": "📁  パス",                       "en": "📁  Paths"},
    "tab_rec":       {"zh": "🎙  录音",                   "ja": "🎙  録音",                       "en": "🎙  Recording"},
    "tab_api":       {"zh": "⚙  纪要/API",               "ja": "⚙  纪要/API",                   "en": "⚙  Summary/API"},
    "save":          {"zh": "💾  保存设置",               "ja": "💾  設定を保存",                 "en": "💾  Save Settings"},
    "saved":         {"zh": "[UI] 设置已保存",            "ja": "[UI] 設定を保存しました",        "en": "[UI] Settings saved"},
    "log_title":     {"zh": "运行日志",                   "ja": "実行ログ",                       "en": "Log"},
    "clear_log":     {"zh": "清空",                       "ja": "クリア",                        "en": "Clear"},
    "starting":      {"zh": "[UI] 启动成功",              "ja": "[UI] 起動しました",              "en": "[UI] Started"},
    "stopping":      {"zh": "[UI] 正在停止...",           "ja": "[UI] 停止中...",                 "en": "[UI] Stopping..."},
    "sum_start":     {"zh": "[UI] 生成会议纪要...",       "ja": "[UI] 会议纪要を生成中...",       "en": "[UI] Generating summary..."},
    "all_done":      {"zh": "[UI] 全部完成",              "ja": "[UI] すべて完了",               "en": "[UI] All done"},
    "audio_dir":     {"zh": "音频保存目录",               "ja": "音声保存先",                     "en": "Audio directory"},
    "tr_dir":        {"zh": "转写文件目录",               "ja": "転写ファイル保存先",             "en": "Transcript directory"},
    "corr_dir":      {"zh": "纠错文件目录",               "ja": "纠错ファイル保存先",             "en": "Corrected text directory"},
    "sum_dir":       {"zh": "纪要保存目录",               "ja": "纪要保存先",                     "en": "Summary directory"},
    "rec_sec":       {"zh": "每段录音时长（秒）",         "ja": "1チャンク録音秒数",              "en": "Chunk duration (sec)"},
    "mode_label":    {"zh": "后端模式",                   "ja": "バックエンド",                   "en": "Backend"},
    "mode_openai":   {"zh": "OpenAI 兼容 API（Groq / DeepSeek 等）", "ja": "OpenAI 互換 API（Groq / DeepSeek 等）", "en": "OpenAI-compatible API (Groq / DeepSeek etc.)"},
    "mode_ollama":   {"zh": "Ollama（本地）",             "ja": "Ollama（ローカル）",             "en": "Ollama (local)"},
    "presets":       {"zh": "快速预设",                   "ja": "プリセット",                     "en": "Presets"},
    "api_base":      {"zh": "API Base URL",               "ja": "API Base URL",                   "en": "API Base URL"},
    "api_key":       {"zh": "API Key",                    "ja": "API Key",                        "en": "API Key"},
    "cloud_model":   {"zh": "Cloud Model",                "ja": "クラウドモデル",                 "en": "Cloud Model"},
    "ollama_model":  {"zh": "Ollama Model",               "ja": "Ollama モデル",                  "en": "Ollama Model"},
    "lang_label":    {"zh": "转写语言",                   "ja": "転写言語",                          "en": "Transcription language"},
    "lang_auto":     {"zh": "自动检测",                   "ja": "自動検出",                          "en": "Auto detect"},
    "lang_zh":       {"zh": "中文 (简体)",               "ja": "中国語（簡体字）",                   "en": "Chinese (Simplified)"},
    "lang_ja":       {"zh": "日语",                      "ja": "日本語",                             "en": "Japanese"},
    "lang_en":       {"zh": "英语",                      "ja": "英語",                               "en": "English"},
}

def t(key: str) -> str:
    return _T.get(key, {}).get(_LANG, _T.get(key, {}).get("en", key))


# ── App ───────────────────────────────────────────────────────────────────────

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(t("title"))
        self.geometry("860x680")
        self.minsize(700, 560)

        self._cfg      = configparser.ConfigParser()
        self._cfg.read(CFG_FILE, encoding="utf-8")
        self._log_q    = queue.Queue()
        self._running  = False
        self._rec_proc = None
        self._tr_proc  = None

        # 子プロセスに UTF-8 出力を強制する（環境変数 + -X utf8 フラグ）
        self._env = os.environ.copy()
        self._env["PYTHONUTF8"] = "1"

        self._build()
        self._poll_log()

    # ── UI 構築 ───────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_control_bar()
        self._build_settings()
        self._build_log()

    def _build_control_bar(self):
        bar = ctk.CTkFrame(self, height=70, corner_radius=8)
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        bar.grid_columnconfigure(3, weight=1)

        self._btn_start = ctk.CTkButton(
            bar, text=t("start"), width=130, height=46,
            fg_color="#1a7a1a", hover_color="#155e15",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self._start,
        )
        self._btn_start.grid(row=0, column=0, padx=(12, 6), pady=12)

        self._btn_stop = ctk.CTkButton(
            bar, text=t("stop"), width=130, height=46,
            fg_color="#8b1a1a", hover_color="#6e1414",
            font=ctk.CTkFont(size=15, weight="bold"),
            state="disabled",
            command=self._stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=6, pady=12)

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(row=0, column=2, padx=14)

        status = ctk.CTkFrame(bar, fg_color="transparent")
        status.grid(row=0, column=3, sticky="w")

        for col, lbl_key, attr in [
            (0, "rec_label", "_lbl_rec"),
            (2, "tr_label",  "_lbl_tr"),
            (4, "sum_label", "_lbl_sum"),
        ]:
            ctk.CTkLabel(status, text=t(lbl_key), font=ctk.CTkFont(size=12)).grid(row=0, column=col, padx=(0, 4))
            lbl = ctk.CTkLabel(status, text=t("idle"), text_color="gray60", font=ctk.CTkFont(size=12))
            lbl.grid(row=0, column=col + 1, padx=(0, 18))
            setattr(self, attr, lbl)

    def _build_settings(self):
        tabs = ctk.CTkTabview(self, anchor="nw")
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self._build_tab_paths(tabs.add(t("tab_paths")))
        self._build_tab_rec(tabs.add(t("tab_rec")))
        self._build_tab_api(tabs.add(t("tab_api")))

    def _build_log(self):
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(frame, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text=t("log_title"), font=ctk.CTkFont(size=13, weight="bold"), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text=t("clear_log"), width=60, height=24,
                      fg_color="gray40", hover_color="gray30",
                      command=self._clear_log).grid(row=0, column=1)

        self._log_box = ctk.CTkTextbox(
            frame, font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=6, pady=(2, 6))

    # ── 設定タブ ──────────────────────────────────────────────────────────────

    def _entry_row(self, parent, row, label_key, section, key, default="", show=None):
        ctk.CTkLabel(parent, text=t(label_key), anchor="w", width=170).grid(
            row=row, column=0, sticky="w", padx=(12, 4), pady=7)
        var = ctk.StringVar(value=self._cfg.get(section, key, fallback=default))
        kw  = {"show": show} if show else {}
        ctk.CTkEntry(parent, textvariable=var, width=420, **kw).grid(
            row=row, column=1, sticky="ew", padx=(0, 12), pady=7)
        return var

    def _save_btn(self, parent, row, entries):
        def _save():
            for (sec, key), var in entries.items():
                if not self._cfg.has_section(sec):
                    self._cfg.add_section(sec)
                val = str(var.get()) if isinstance(var, ctk.IntVar) else var.get()
                self._cfg.set(sec, key, val)
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)
            self._put_log(t("saved"))
        ctk.CTkButton(parent, text=t("save"), width=160, command=_save).grid(
            row=row, column=0, columnspan=2, pady=14)

    def _build_tab_paths(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        e = {}
        e[("paths",   "audio_dir")]      = self._entry_row(tab, 0, "audio_dir",  "paths",   "audio_dir",      r"C:\code\data\audio")
        e[("paths",   "transcript_dir")] = self._entry_row(tab, 1, "tr_dir",     "paths",   "transcript_dir", r"C:\code\data\transcript")
        e[("summary", "corrected_dir")]  = self._entry_row(tab, 2, "corr_dir",   "summary", "corrected_dir",  r"C:\code\data\corrected")
        e[("summary", "summary_dir")]    = self._entry_row(tab, 3, "sum_dir",    "summary", "summary_dir",    r"C:\code\data\memo")
        self._save_btn(tab, 4, e)

    def _build_tab_rec(self, tab):
        tab.grid_columnconfigure(1, weight=1)

        # 言語設定
        ctk.CTkLabel(tab, text=t("lang_label"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7)
        self._lang_var = ctk.StringVar(
            value=self._cfg.get("recording", "language", fallback="auto"))
        lang_frame = ctk.CTkFrame(tab, fg_color="transparent")
        lang_frame.grid(row=0, column=1, columnspan=2, sticky="w", pady=7)
        for val, label_key in [("auto", "lang_auto"), ("zh", "lang_zh"),
                                ("ja", "lang_ja"), ("en", "lang_en")]:
            ctk.CTkRadioButton(lang_frame, text=t(label_key),
                               variable=self._lang_var, value=val).pack(side="left", padx=(0, 14))

        # 録音チャンク秒数
        ctk.CTkLabel(tab, text=t("rec_sec"), anchor="w", width=170).grid(
            row=1, column=0, sticky="w", padx=(12, 4), pady=7)
        self._rec_sec = ctk.IntVar(value=int(self._cfg.get("recording", "record_sec", fallback="30")))
        lbl = ctk.CTkLabel(tab, text=f"{self._rec_sec.get()} s", width=50)
        ctk.CTkSlider(
            tab, from_=10, to=120, number_of_steps=11, variable=self._rec_sec,
            command=lambda v: lbl.configure(text=f"{int(v)} s"),
        ).grid(row=1, column=1, sticky="ew", padx=(0, 60), pady=7)
        lbl.grid(row=1, column=2, padx=4)

        def _save():
            if not self._cfg.has_section("recording"):
                self._cfg.add_section("recording")
            self._cfg.set("recording", "record_sec", str(self._rec_sec.get()))
            self._cfg.set("recording", "language",   self._lang_var.get())
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)
            self._put_log(t("saved"))
        ctk.CTkButton(tab, text=t("save"), width=160, command=_save).grid(
            row=2, column=0, columnspan=3, pady=14)

    def _build_tab_api(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        e = {}

        ctk.CTkLabel(tab, text=t("mode_label"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7)
        self._mode_var = ctk.StringVar(value=self._cfg.get("summary", "mode", fallback="openai"))
        mf = ctk.CTkFrame(tab, fg_color="transparent")
        mf.grid(row=0, column=1, sticky="w", pady=7)
        ctk.CTkRadioButton(mf, text=t("mode_openai"), variable=self._mode_var, value="openai").pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(mf, text=t("mode_ollama"), variable=self._mode_var, value="ollama").pack(side="left")
        e[("summary", "mode")] = self._mode_var

        e[("summary", "api_base")]     = self._entry_row(tab, 1, "api_base",     "summary", "api_base",     "https://api.groq.com/openai/v1")
        e[("summary", "api_key")]      = self._entry_row(tab, 2, "api_key",      "summary", "api_key",      "", show="*")
        e[("summary", "model")]        = self._entry_row(tab, 3, "cloud_model",  "summary", "model",        "llama-3.3-70b-versatile")
        e[("summary", "ollama_model")] = self._entry_row(tab, 4, "ollama_model", "summary", "ollama_model", "qwen2.5:7b")

        ctk.CTkLabel(tab, text=t("presets"), anchor="w", width=170).grid(
            row=5, column=0, sticky="w", padx=(12, 4), pady=(12, 4))
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
            ctk.CTkButton(pf, text=name, width=90, height=28,
                          fg_color="gray40", hover_color="gray30",
                          command=_apply).pack(side="left", padx=4)

        self._save_btn(tab, 6, e)

    # ── プロセス制御 ──────────────────────────────────────────────────────────

    def _reload_config(self):
        """config.ini を読み直し、UI の設定ウィジェットを最新状態に更新する。"""
        self._cfg.read(CFG_FILE, encoding="utf-8")
        # 言語ラジオボタンを更新（前回の自動検出結果が書き戻されていた場合に反映）
        lang = self._cfg.get("recording", "language", fallback="auto")
        if hasattr(self, "_lang_var"):
            self._lang_var.set(lang)

    def _start(self):
        if self._running:
            return
        if os.path.exists(STOP_SIGNAL):
            os.remove(STOP_SIGNAL)

        # 開始前に config を再読み込み（前回の自動検出結果を反映）
        self._reload_config()

        self._running = True
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._lbl_rec.configure(text=t("running"), text_color="#44dd44")
        self._lbl_tr.configure(text=t("running"),  text_color="#44dd44")
        self._lbl_sum.configure(text=t("standby"), text_color="gray60")

        self._rec_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "recorder.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        self._tr_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "transcriber.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )

        threading.Thread(target=self._pipe, args=(self._rec_proc, "[Rec]"),  daemon=True).start()
        threading.Thread(target=self._pipe, args=(self._tr_proc,  "[Tr]"),   daemon=True).start()
        threading.Thread(target=self._after_trans,                            daemon=True).start()

        self._put_log(t("starting"))

    def _stop(self):
        if not self._running:
            return
        self._btn_stop.configure(state="disabled")
        self._put_log(t("stopping"))

        if self._rec_proc and self._rec_proc.poll() is None:
            self._rec_proc.terminate()
        self._lbl_rec.configure(text=t("stopped"), text_color="gray60")

        with open(STOP_SIGNAL, "w") as f:
            f.write("stop")

    def _after_trans(self):
        if self._tr_proc:
            self._tr_proc.wait()
        self._lbl_tr.configure(text=t("stopped"), text_color="gray60")

        if os.path.exists(STATE_FILE):
            self._lbl_sum.configure(text=t("generating"), text_color="#ddaa00")
            self._put_log(t("sum_start"))
            sum_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(sum_proc, "[Sum]"), daemon=True).start()
            sum_proc.wait()
            self._lbl_sum.configure(text=t("done"), text_color="#44dd44")
        else:
            self._lbl_sum.configure(text=t("skipped"), text_color="gray60")

        self._running = False
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._put_log(t("all_done"))

    def _pipe(self, proc, prefix):
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                self._put_log(f"{prefix} {text}")

    # ── ログ ─────────────────────────────────────────────────────────────────

    def _put_log(self, msg: str):
        self._log_q.put(msg)

    def _poll_log(self):
        while not self._log_q.empty():
            msg = self._log_q.get_nowait()
            self._log_box.configure(state="normal")
            self._log_box.insert("end", msg + "\n")
            self._log_box.see("end")
            self._log_box.configure(state="disabled")
        self.after(150, self._poll_log)

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── 終了 ─────────────────────────────────────────────────────────────────

    def on_close(self):
        if self._running:
            if self._rec_proc and self._rec_proc.poll() is None:
                self._rec_proc.terminate()
            if self._tr_proc and self._tr_proc.poll() is None:
                self._tr_proc.terminate()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()

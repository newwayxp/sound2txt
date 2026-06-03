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
import time
import shutil
import customtkinter as ctk
from datetime import datetime

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

BASE        = os.path.dirname(os.path.abspath(__file__))
CFG_FILE    = os.path.join(BASE, "config.ini")
STOP_SIGNAL  = os.path.join(BASE, ".stop_signal")
STATE_FILE   = os.path.join(BASE, ".last_transcript")
LANG_FILE    = os.path.join(BASE, ".last_language")
START_FILE   = os.path.join(BASE, ".recording_start")

# ── CUDA detection (run once at import time) ──────────────────────────────────

def _detect_cuda():
    has_gpu = False
    try:
        import ctranslate2
        has_gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_gpu = shutil.which("nvidia-smi") is not None
    if not has_gpu:
        return False, False
    import ctypes
    dll_names = ["cublas64_13.dll", "cublas64_12.dll", "cublas64_11.dll", "cublas.dll"]
    search_dirs = [""]
    cuda_root = os.environ.get("CUDA_PATH", "")
    if cuda_root:
        search_dirs += [os.path.join(cuda_root, "bin"), os.path.join(cuda_root, "bin", "x64")]
    toolkit_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.isdir(toolkit_base):
        for ver in sorted(os.listdir(toolkit_base), reverse=True):
            search_dirs += [
                os.path.join(toolkit_base, ver, "bin"),
                os.path.join(toolkit_base, ver, "bin", "x64"),
            ]
    for search_dir in search_dirs:
        for dll in dll_names:
            path = os.path.join(search_dir, dll) if search_dir else dll
            try:
                ctypes.CDLL(path)
                return True, True
            except OSError:
                pass
    return True, False  # GPU found but cuBLAS missing

_CUDA_AVAILABLE, _CUDA_LIBS_OK = _detect_cuda()

# ── Ollama global state ────────────────────────────────────────────────────────
import threading as _threading
_OLLAMA_LOCK  = _threading.Lock()
_OLLAMA_STATE = {"status": "stopped"}  # "stopped" | "starting" | "running"

# ── i18n ─────────────────────────────────────────────────────────────────────

def _ui_lang() -> str:
    try:
        # Python 3.11+: use getlocale() which doesn't show deprecation warning
        lang = (locale.getlocale()[0] or "").lower()
    except Exception:
        lang = ""
    if not lang:
        try:
            import ctypes
            # Windows: get system UI language from registry
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            lang = "zh" if lang_id in (0x0804, 0x0C04, 0x1004) else \
                   "ja" if lang_id == 0x0411 else "en"
            return lang
        except Exception:
            return "en"
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
    "failed":        {"zh": "● 失败",                     "ja": "● 失敗",                        "en": "● Failed"},
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
    "mic_dir":       {"zh": "麦克风录音目录",             "ja": "マイク録音保存先",               "en": "Mic audio directory"},
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
    "ollama_url":    {"zh": "Ollama 服务器地址",            "ja": "Ollama サーバー URL",            "en": "Ollama Server URL"},
    "ollama_model":  {"zh": "Ollama Model",               "ja": "Ollama モデル",                  "en": "Ollama Model"},
    "device_label":  {"zh": "运算设备",                   "ja": "実行デバイス",                      "en": "Device"},
    "device_auto":   {"zh": "自动（有GPU则用GPU）",        "ja": "自動（GPU 優先）",                  "en": "Auto (GPU if available)"},
    "gpu_unavailable":{"zh": "未检测到 CUDA GPU，已固定为 CPU + tiny", "ja": "CUDA GPU が見つからないため CPU + tiny に固定しました", "en": "CUDA GPU not detected; fixed to CPU + tiny"},
    "model_label":   {"zh": "Whisper 模型",               "ja": "Whisper モデル",                    "en": "Whisper model"},
    "mic_enable":    {"zh": "麦克风录音",                 "ja": "マイク録音",                        "en": "Microphone recording"},
    "mic_enable_hint":{"zh": "同时录制本地麦克风（自己的发言）", "ja": "自分の発言もマイクで録音する", "en": "Also record local mic (your own voice)"},
    "lang_label":    {"zh": "转写语言",                   "ja": "転写言語",                          "en": "Transcription language"},
    "lang_auto":     {"zh": "自动检测",                   "ja": "自動検出",                          "en": "Auto detect"},
    "lang_zh":       {"zh": "中文 (简体)",               "ja": "中国語（簡体字）",                   "en": "Chinese (Simplified)"},
    "lang_ja":       {"zh": "日语",                      "ja": "日本語",                             "en": "Japanese"},
    "lang_en":       {"zh": "英语",                      "ja": "英語",                               "en": "English"},
    "tab_network":   {"zh": "🌐  代理",                  "ja": "🌐  ネットワーク",                   "en": "🌐  Network"},
    "https_proxy":   {"zh": "HTTPS 代理",                "ja": "HTTPS プロキシ",                     "en": "HTTPS Proxy"},
    "http_proxy":    {"zh": "HTTP 代理",                 "ja": "HTTP プロキシ",                      "en": "HTTP Proxy"},
    "ssl_verify":    {"zh": "SSL 证书验证",              "ja": "SSL 証明書の検証",                   "en": "SSL certificate verify"},
    "ssl_on":        {"zh": "启用（默认）",              "ja": "有効（デフォルト）",                 "en": "Enabled (default)"},
    "ssl_off":       {"zh": "禁用（企业代理自签名证书）","ja": "無効（社内プロキシ自己署名証明書）", "en": "Disabled (self-signed corp proxy)"},
    "proxy_hint":    {"zh": "留空则不使用代理",          "ja": "空欄の場合はプロキシなし",           "en": "Leave blank to disable proxy"},
    "voice_note":      {"zh": "🎤",                        "ja": "🎤",                                "en": "🎤"},
    "mode_meeting":    {"zh": "会议模式",                 "ja": "会議モード",                        "en": "Meeting"},
    "mode_local_mic":  {"zh": "本地Mic",                  "ja": "ローカルMic",                       "en": "Local Mic"},
}

def t(key: str) -> str:
    return _T.get(key, {}).get(_LANG, _T.get(key, {}).get("en", key))


# ── 7-segment patterns  (a=64 b=32 c=16 d=8 e=4 f=2 g=1) ─────────────────────
_SEG7 = {
    '0': 0b1111110, '1': 0b0110000, '2': 0b1101101,
    '3': 0b1111001, '4': 0b0110011, '5': 0b1011011,
    '6': 0b1011111, '7': 0b1110000, '8': 0b1111111,
    '9': 0b1111011,
}

def _dim(hex_color: str, factor: int = 9) -> str:
    """Return a very dim version of hex_color for ghost segments."""
    r = max(int(hex_color[1:3], 16) // factor, 10)
    g = max(int(hex_color[3:5], 16) // factor, 8)
    b = max(int(hex_color[5:7], 16) // factor, 8)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── ON AIR / VU-Meter Widget ──────────────────────────────────────────────────

class OnAirWidget(ctk.CTkFrame):
    """
    Compact horizontal VU-meter: colored LED segments that react to mic level.
    Design: green/yellow/red gradient segments, dark background.
    """
    N    = 12     # number of segments
    SW   = 9      # segment width
    SH   = 22     # segment height
    GAP  = 3      # gap between segments
    FPS  = 40     # ms per frame (~25 fps)

    def __init__(self, master, **kwargs):
        import tkinter as tk
        w = self.N * (self.SW + self.GAP) - self.GAP + 8
        super().__init__(master, fg_color="transparent", **kwargs)
        self._cv = tk.Canvas(self, width=w, height=self.SH + 8,
                             bg="#1a1a2a", highlightthickness=1,
                             highlightbackground="#333344")
        self._cv.pack(padx=0, pady=0)
        self._level    = 0.0
        self._smoothed = 0.0
        self._active   = False
        self._anim()

    def set_level(self, v: float):  self._level = max(0.0, min(v, 1.0))

    def show(self):
        self._active = True
        self.grid(row=0, column=4, padx=(6, 2), pady=10, sticky="w")

    def hide(self):
        self._active = False
        self.grid_remove()

    def _anim(self):
        self._smoothed += ((self._level if self._active else 0.0) - self._smoothed) * 0.35
        lit = int(self._smoothed * self.N)
        cv  = self._cv
        try:
            cv.delete("all")
        except Exception:
            return
        for i in range(self.N):
            x = 4 + i * (self.SW + self.GAP)
            frac = i / (self.N - 1)
            if i < lit:
                if   frac < 0.60: c = "#00e676"
                elif frac < 0.80: c = "#ffea00"
                else:             c = "#ff1744"
            else:
                if   frac < 0.60: c = "#0a2a18"
                elif frac < 0.80: c = "#2a2a06"
                else:             c = "#2a0a10"
            cv.create_rectangle(x, 4, x + self.SW, 4 + self.SH, fill=c, outline="")
        self.after(self.FPS, self._anim)


class SevenSegClock(ctk.CTkFrame):
    """
    MM:SS display as authentic 7-segment LED digits.
    Active segments glow; ghost segments show as very dim lines.
    """
    DW = 32   # digit width
    DH = 52   # digit height
    DT = 5    # segment thickness
    CW = 12   # colon width
    PX = 10   # horizontal canvas padding
    PY = 8    # vertical canvas padding
    GS = 2    # inter-segment gap

    def __init__(self, master, on_color: str = "#29b6f6", **kwargs):
        import tkinter as tk
        cw = self.PX * 2 + 4 * self.DW + self.CW + 5 * self.GS
        ch = self.DH + self.PY * 2
        super().__init__(master, fg_color="#0d1117", corner_radius=10, **kwargs)
        self._cv  = tk.Canvas(self, width=cw, height=ch,
                              bg="#0d1117", highlightthickness=0)
        self._cv.pack(padx=6, pady=6)
        self._on  = on_color
        self._off = _dim(on_color, 10)
        self._val = 0.0
        self._redraw()

    def set_secs(self, secs: float):
        if int(secs) != int(self._val):
            self._val = secs
            self._redraw()

    def _redraw(self):
        cv = self._cv
        cv.delete("all")
        s  = int(self._val)
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


class DashboardWidget(ctk.CTkFrame):
    """Three 7-segment LED timers: elapsed, audio captured, text transcribed."""
    _TIMERS = [
        ("elapsed", "录音经过", "録音経過",   "Elapsed",     "#29b6f6"),
        ("audio",   "音频收录", "音声収録",   "Audio Rec",   "#66bb6a"),
        ("trans",   "文字转换", "文字起こし", "Transcribed", "#ffa726"),
    ]

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._start_time = 0.0
        self._audio_secs = 0.0
        self._trans_secs     = 0.0
        self._active         = False
        self._frozen_elapsed = 0.0
        self._clocks         = {}
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

    def start(self):
        self._start_time    = time.time()
        self._audio_secs    = 0.0
        self._trans_secs    = 0.0
        self._frozen_elapsed = 0.0
        self._active        = True

    def stop(self):
        # Freeze display at current values; do NOT zero out until reset() is called
        if self._active and self._start_time:
            self._frozen_elapsed = time.time() - self._start_time
        self._active = False

    def reset(self):
        """Zero all timers — called after all post-processing is complete."""
        self._start_time     = 0.0
        self._audio_secs     = 0.0
        self._trans_secs     = 0.0
        self._frozen_elapsed = 0.0

    def add_audio(self, secs: float):  self._audio_secs += secs
    def add_trans(self, secs: float):  self._trans_secs += secs

    def _tick(self):
        if self._active and self._start_time:
            e = time.time() - self._start_time   # counting up
        else:
            e = self._frozen_elapsed             # frozen at stop value
        for key, clk in self._clocks.items():
            v = e if key == "elapsed" else (
                self._audio_secs if key == "audio" else self._trans_secs)
            try:
                clk.set_secs(v)
            except Exception:
                pass
        self.after(1000, self._tick)


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
        self._running       = False
        self._rec_proc      = None
        self._mic_proc      = None
        self._tr_proc       = None
        self._stopping      = False
        self._meter_active  = False
        self._ollama_proc   = None   # subprocess started by us (None = started externally)
        self._tr_current_file  = ""   # last audio file reported by transcriber
        self._tr_last_activity = 0.0  # monotonic time of last [Tr] file message

        # 子プロセスに UTF-8 出力と非バッファリングを強制
        self._env = os.environ.copy()
        self._env["PYTHONUTF8"]       = "1"
        self._env["PYTHONUNBUFFERED"] = "1"
        # Ensure CUDA 13 runtime DLLs are findable by CTranslate2 (looks for cublas64_12/13)
        _cuda_root = self._env.get("CUDA_PATH", "")
        if _cuda_root:
            _cuda_bin = os.path.join(_cuda_root, "bin", "x64")
            if os.path.isdir(_cuda_bin):
                self._env["PATH"] = _cuda_bin + os.pathsep + self._env.get("PATH", "")

        self._auto_select_backend()   # update config before UI is built
        self._build()
        self._apply_cpu_only_defaults(log=True)
        self._poll_log()
        self._log_startup_info()
        self._ensure_ollama_running()
        self._prestart_transcriber()  # pre-load Whisper model in background

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
        bar.grid_columnconfigure(7, weight=1)

        self._btn_start = ctk.CTkButton(
            bar, text=t("start"), width=120, height=44,
            fg_color="#28a745", hover_color="#1e7e34",
            text_color="white",
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=8,
            command=self._start,
        )
        self._btn_start.grid(row=0, column=0, padx=(12, 6), pady=12)

        self._btn_stop = ctk.CTkButton(
            bar, text=t("stop"), width=120, height=44,
            fg_color="#dc3545", hover_color="#bd2130",
            text_color="white",
            font=ctk.CTkFont(size=15, weight="bold"),
            corner_radius=8,
            state="disabled",
            command=self._stop,
        )
        self._btn_stop.grid(row=0, column=1, padx=6, pady=12)

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(row=0, column=2, padx=10)

        # ── モード切替 ──
        mode_val = self._cfg.get("recording", "mode", fallback="meeting")
        self._mode_var = ctk.StringVar(value=t(f"mode_{mode_val}"))
        self._mode_seg = ctk.CTkSegmentedButton(
            bar,
            values=[t("mode_meeting"), t("mode_local_mic")],
            variable=self._mode_var,
            command=self._on_mode_change,
            width=210, height=34,
            font=ctk.CTkFont(size=12),
        )
        self._mode_seg.grid(row=0, column=3, padx=(0, 6), pady=12)

        # ON AIR widget (hidden by default, shown only in local_mic recording)
        self._onair = OnAirWidget(bar)
        # not gridded yet - shown via show()/hide()

        ctk.CTkFrame(bar, width=2, height=40, fg_color="gray50").grid(row=0, column=6, padx=6)

        # ── 言語クイックセレクター ──
        lang_frame = ctk.CTkFrame(bar, fg_color="transparent")
        lang_frame.grid(row=0, column=5, padx=(0, 10), pady=12)

        ctk.CTkLabel(lang_frame, text=t("lang_label"),
                     font=ctk.CTkFont(size=11), text_color="gray70").grid(row=0, column=0, padx=(0, 6))

        lang_values = {
            "auto": t("lang_auto"),
            "zh":   t("lang_zh"),
            "ja":   t("lang_ja"),
            "en":   t("lang_en"),
        }
        current_lang = self._cfg.get("recording", "language", fallback="auto")
        self._quick_lang_var = ctk.StringVar(value=lang_values.get(current_lang, t("lang_auto")))

        self._lang_menu = ctk.CTkOptionMenu(
            lang_frame,
            values=list(lang_values.values()),
            variable=self._quick_lang_var,
            width=140, height=34,
            command=self._on_quick_lang_change,
        )
        self._lang_menu.grid(row=0, column=1)
        self._lang_code_map = {v: k for k, v in lang_values.items()}

        # Status labels (hidden — dashboard timers show this info instead)
        _hidden = ctk.CTkFrame(bar, fg_color="transparent")
        for _, attr in [("rec_label", "_lbl_rec"), ("tr_label", "_lbl_tr"), ("sum_label", "_lbl_sum")]:
            setattr(self, attr, ctk.CTkLabel(_hidden, text=""))

    def _build_settings(self):
        tabs = ctk.CTkTabview(self, anchor="nw",
            segmented_button_fg_color=("gray85", "gray22"),
            segmented_button_selected_color=("#1976d2", "#1976d2"),
            segmented_button_selected_hover_color=("#1565c0", "#1565c0"),
            segmented_button_unselected_hover_color=("gray78", "gray32"),
            text_color=("gray10", "gray90"),
        )
        tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        self._build_tab_paths(tabs.add(t("tab_paths")))
        self._build_tab_rec(tabs.add(t("tab_rec")))
        self._build_tab_api(tabs.add(t("tab_api")))
        self._build_tab_network(tabs.add(t("tab_network")))

    def _build_log(self):
        frame = ctk.CTkFrame(self, corner_radius=8)
        frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 12))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        # ── inner tab view ────────────────────────────────────────────────────
        tab_dash_lbl = {"zh": "⏱  进度",  "ja": "⏱  進捗",  "en": "⏱  Progress"}.get(_LANG, "⏱  Progress")
        tab_log_lbl  = {"zh": "📋  日志",  "ja": "📋  ログ",  "en": "📋  Log"     }.get(_LANG, "📋  Log")

        inner = ctk.CTkTabview(frame, anchor="nw",
            segmented_button_fg_color=("gray85", "gray22"),
            segmented_button_selected_color=("#1976d2", "#1976d2"),
            segmented_button_selected_hover_color=("#1565c0", "#1565c0"),
            segmented_button_unselected_hover_color=("gray78", "gray32"),
            text_color=("gray10", "gray90"),
        )
        inner.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        inner.add(tab_dash_lbl)
        inner.add(tab_log_lbl)

        # ── Dashboard tab ─────────────────────────────────────────────────────
        dash_tab = inner.tab(tab_dash_lbl)
        dash_tab.grid_columnconfigure(0, weight=1)
        dash_tab.grid_rowconfigure(0, weight=1)
        self._dashboard = DashboardWidget(dash_tab)
        self._dashboard.grid(row=0, column=0, pady=20)

        # ── Log tab ───────────────────────────────────────────────────────────
        log_tab = inner.tab(tab_log_lbl)
        log_tab.grid_columnconfigure(0, weight=1)
        log_tab.grid_rowconfigure(1, weight=1)

        hdr = ctk.CTkFrame(log_tab, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text=t("log_title"),
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(hdr, text=t("clear_log"), width=60, height=24,
                      fg_color="gray40", hover_color="gray30",
                      command=self._clear_log).grid(row=0, column=1)

        self._log_box = ctk.CTkTextbox(
            log_tab, font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled", wrap="word",
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))

        # Default: show Dashboard tab
        inner.set(tab_dash_lbl)

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
        ctk.CTkButton(parent, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=row, column=0, columnspan=2, pady=14)

    def _build_tab_paths(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        e = {}
        rows = [
            (0, "audio_dir",  "paths",   "audio_dir",      r"C:\code\data\audio"),
            (1, "mic_dir",    "paths",   "mic_dir",        r"C:\code\data\mic"),
            (2, "tr_dir",     "paths",   "transcript_dir", r"C:\code\data\transcript"),
            (3, "corr_dir",   "summary", "corrected_dir",  r"C:\code\data\corrected"),
            (4, "sum_dir",    "summary", "summary_dir",    r"C:\code\data\memo"),
        ]
        for row, lbl_key, sec, key, default in rows:
            var = self._entry_row(tab, row, lbl_key, sec, key, default)
            e[(sec, key)] = var
            # Folder picker button
            def _pick(v=var):
                import tkinter.filedialog as fd
                path = fd.askdirectory(title=t(lbl_key),
                                       initialdir=v.get() or "C:\\")
                if path:
                    v.set(path.replace("/", "\\"))
            ctk.CTkButton(tab, text="📂", width=36, height=28,
                          fg_color="gray40", hover_color="gray30",
                          command=_pick).grid(row=row, column=2, padx=(4, 8), pady=7)
        self._save_btn(tab, 5, e)

    def _build_tab_rec(self, tab):
        tab.grid_columnconfigure(1, weight=1)

        # Mic enable toggle
        ctk.CTkLabel(tab, text=t("mic_enable"), anchor="w", width=170).grid(
            row=0, column=0, sticky="w", padx=(12, 4), pady=7)
        self._mic_var = ctk.BooleanVar(
            value=self._cfg.getboolean("recording", "enable_mic", fallback=True))
        mic_frame = ctk.CTkFrame(tab, fg_color="transparent")
        mic_frame.grid(row=0, column=1, sticky="w", pady=7)
        ctk.CTkSwitch(mic_frame, text=t("mic_enable_hint"),
                      variable=self._mic_var, onvalue=True, offvalue=False).pack(side="left")

        # デバイス設定
        ctk.CTkLabel(tab, text=t("device_label"), anchor="w", width=170).grid(
            row=1, column=0, sticky="w", padx=(12, 4), pady=7)
        self._device_var = ctk.StringVar(
            value=self._cfg.get("recording", "device", fallback="auto"))
        dev_frame = ctk.CTkFrame(tab, fg_color="transparent")
        dev_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=7)
        self._gpu_locked_buttons = []
        btn_auto = ctk.CTkRadioButton(dev_frame, text=t("device_auto"),
                                      variable=self._device_var, value="auto")
        btn_auto.pack(side="left", padx=(0, 14))
        self._btn_cuda = ctk.CTkRadioButton(dev_frame, text="CUDA (GPU)",
                                            variable=self._device_var, value="cuda")
        self._btn_cuda.pack(side="left", padx=(0, 14))
        btn_cpu = ctk.CTkRadioButton(dev_frame, text="CPU",
                                     variable=self._device_var, value="cpu")
        btn_cpu.pack(side="left")
        self._gpu_locked_buttons.extend([btn_auto, self._btn_cuda])

        # モデルサイズ
        ctk.CTkLabel(tab, text=t("model_label"), anchor="w", width=170).grid(
            row=1, column=0, sticky="w", padx=(12, 4), pady=7)
        self._model_var = ctk.StringVar(
            value=self._cfg.get("recording", "model_size", fallback="small"))
        model_frame = ctk.CTkFrame(tab, fg_color="transparent")
        model_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=7)
        for val, desc in [("tiny", "tiny (高速)"), ("small", "small (推奨)"),
                           ("medium", "medium (高精度)"), ("large-v3", "large-v3 (最高精度)")]:
            btn = ctk.CTkRadioButton(model_frame, text=desc,
                                     variable=self._model_var, value=val)
            btn.pack(side="left", padx=(0, 10))
            if val != "tiny":
                self._gpu_locked_buttons.append(btn)

        # 言語設定
        ctk.CTkLabel(tab, text=t("lang_label"), anchor="w", width=170).grid(
            row=2, column=0, sticky="w", padx=(12, 4), pady=7)
        self._lang_var = ctk.StringVar(
            value=self._cfg.get("recording", "language", fallback="auto"))
        lang_frame = ctk.CTkFrame(tab, fg_color="transparent")
        lang_frame.grid(row=3, column=1, columnspan=2, sticky="w", pady=7)
        for val, label_key in [("auto", "lang_auto"), ("zh", "lang_zh"),
                                ("ja", "lang_ja"), ("en", "lang_en")]:
            ctk.CTkRadioButton(lang_frame, text=t(label_key),
                               variable=self._lang_var, value=val).pack(side="left", padx=(0, 14))

        # 録音チャンク秒数
        ctk.CTkLabel(tab, text=t("rec_sec"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7)
        self._rec_sec = ctk.IntVar(value=int(self._cfg.get("recording", "record_sec", fallback="30")))
        lbl = ctk.CTkLabel(tab, text=f"{self._rec_sec.get()} s", width=50)
        ctk.CTkSlider(
            tab, from_=10, to=120, number_of_steps=11, variable=self._rec_sec,
            command=lambda v: lbl.configure(text=f"{int(v)} s"),
        ).grid(row=3, column=1, sticky="ew", padx=(0, 60), pady=7)
        lbl.grid(row=3, column=2, padx=4)

        def _save():
            if not self._cfg.has_section("recording"):
                self._cfg.add_section("recording")
            self._cfg.set("recording", "record_sec",  str(self._rec_sec.get()))
            self._cfg.set("recording", "language",    self._lang_var.get())
            self._cfg.set("recording", "device",      self._device_var.get())
            self._cfg.set("recording", "model_size",  self._model_var.get())
            self._cfg.set("recording", "enable_mic",  str(self._mic_var.get()).lower())
            if not self._cuda_available():
                self._cfg.set("recording", "device", "cpu")
                self._cfg.set("recording", "model_size", "tiny")
                self._device_var.set("cpu")
                self._model_var.set("tiny")
                self._put_log(f"[UI] {t('gpu_unavailable')}")
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)
            self._put_log(t("saved"))
        ctk.CTkButton(tab, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=4, column=0, columnspan=3, pady=14)

    def _build_tab_api(self, tab):
        tab.grid_columnconfigure(0, weight=1)

        current_mode = self._cfg.get("summary", "mode", fallback="openai")
        tab_label_openai = t("mode_openai")
        tab_label_ollama = t("mode_ollama")

        inner = ctk.CTkTabview(tab)
        inner.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 0))
        inner.add(tab_label_openai)
        inner.add(tab_label_ollama)

        # ── OpenAI tab ────────────────────────────────────────────────────────
        oa = inner.tab(tab_label_openai)
        oa.grid_columnconfigure(1, weight=1)
        e = {}
        e[("summary", "api_base")] = self._entry_row(oa, 0, "api_base",    "summary", "api_base", "https://api.groq.com/openai/v1")
        e[("summary", "api_key")]  = self._entry_row(oa, 1, "api_key",     "summary", "api_key",  "", show="*")
        e[("summary", "model")]    = self._entry_row(oa, 2, "cloud_model", "summary", "model",    "llama-3.3-70b-versatile")

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
                e[("summary", "api_base")].set(b)
                e[("summary", "model")].set(m)
                inner.set(tab_label_openai)
            ctk.CTkButton(pf, text=name, width=90, height=28,
                          fg_color="gray40", hover_color="gray30",
                          command=_apply).pack(side="left", padx=4)

        # ── Ollama tab ────────────────────────────────────────────────────────
        ol = inner.tab(tab_label_ollama)
        ol.grid_columnconfigure(1, weight=1)
        e[("summary", "ollama_url")]   = self._entry_row(ol, 0, "ollama_url",   "summary", "ollama_url",   "http://localhost:11434")
        e[("summary", "ollama_model")] = self._entry_row(ol, 1, "ollama_model", "summary", "ollama_model", "qwen2.5:7b")

        # Start on the tab matching the saved mode
        inner.set(tab_label_ollama if current_mode == "ollama" else tab_label_openai)

        # ── Save — writes mode based on active tab ────────────────────────────
        def _save():
            mode = "ollama" if inner.get() == tab_label_ollama else "openai"
            if not self._cfg.has_section("summary"):
                self._cfg.add_section("summary")
            self._cfg.set("summary", "mode", mode)
            for (sec, key), var in e.items():
                if not self._cfg.has_section(sec):
                    self._cfg.add_section(sec)
                self._cfg.set(sec, key, var.get())
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)
            self._put_log(t("saved"))
            if mode == "ollama":
                self._ensure_ollama_running()

        ctk.CTkButton(tab, text=t("save"), width=160, command=_save,
                      fg_color="#1976d2", hover_color="#1565c0",
                      text_color="white", corner_radius=8).grid(
            row=1, column=0, pady=14)

    def _build_tab_network(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        e = {}

        e[("network", "https_proxy")] = self._entry_row(tab, 0, "https_proxy", "network", "https_proxy", "")
        e[("network", "http_proxy")]  = self._entry_row(tab, 1, "http_proxy",  "network", "http_proxy",  "")

        ctk.CTkLabel(tab, text=t("proxy_hint"), text_color="gray60",
                     font=ctk.CTkFont(size=11), anchor="w").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=(12, 4), pady=(0, 8))

        ctk.CTkLabel(tab, text=t("ssl_verify"), anchor="w", width=170).grid(
            row=3, column=0, sticky="w", padx=(12, 4), pady=7)
        self._ssl_var = ctk.StringVar(
            value=self._cfg.get("network", "ssl_verify", fallback="true"))
        ssl_frame = ctk.CTkFrame(tab, fg_color="transparent")
        ssl_frame.grid(row=3, column=1, sticky="w", pady=7)
        ctk.CTkRadioButton(ssl_frame, text=t("ssl_on"),
                           variable=self._ssl_var, value="true").pack(side="left", padx=(0, 20))
        ctk.CTkRadioButton(ssl_frame, text=t("ssl_off"),
                           variable=self._ssl_var, value="false").pack(side="left")
        e[("network", "ssl_verify")] = self._ssl_var

        self._save_btn(tab, 4, e)

    # ── プロセス制御 ──────────────────────────────────────────────────────────

    def _reload_config(self):
        """config.ini を読み直し、UI の設定ウィジェットを最新状態に更新する。"""
        self._cfg.read(CFG_FILE, encoding="utf-8")
        lang = self._cfg.get("recording", "language", fallback="auto")
        # 設定タブのラジオボタンを更新
        if hasattr(self, "_lang_var"):
            self._lang_var.set(lang)
        # コントロールバーのクイックセレクターを更新
        if hasattr(self, "_quick_lang_var") and hasattr(self, "_lang_code_map"):
            label = {v: k for k, v in self._lang_code_map.items()}.get(lang)
            if label:
                self._quick_lang_var.set(label)
        # モードセレクターを更新
        if hasattr(self, "_mode_var"):
            mode = self._cfg.get("recording", "mode", fallback="meeting")
            self._mode_var.set(t(f"mode_{mode}"))

    def _start_meter(self):
        """Start lightweight mic sampling for the ON AIR level meter."""
        self._meter_active = True
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _prestart_transcriber(self):
        """Launch transcriber.py at UI startup so the Whisper model is loaded before recording begins."""
        if self._tr_proc and self._tr_proc.poll() is None:
            return  # already running
        self._put_log("[UI] Whisper モデルをバックグラウンドでロード中...")
        self._tr_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "transcriber.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        threading.Thread(target=self._pipe, args=(self._tr_proc, "[Tr]"), daemon=True).start()

    def _stop_meter(self):
        self._meter_active = False

    def _ensure_ollama_running(self):
        """If Ollama mode is configured and server is not running, start ollama serve."""
        if self._cfg.get("summary", "mode", fallback="openai") != "ollama":
            return
        threading.Thread(target=self._ollama_start_worker, daemon=True).start()

    def _ollama_start_worker(self):
        import requests, subprocess, time
        with _OLLAMA_LOCK:
            if _OLLAMA_STATE["status"] in ("starting", "running"):
                return
            _OLLAMA_STATE["status"] = "starting"

        ollama_url = self._cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
        try:
            requests.get(ollama_url, timeout=2)
            with _OLLAMA_LOCK:
                _OLLAMA_STATE["status"] = "running"
            return  # already running externally
        except Exception:
            pass

        self._put_log("[Ollama] 正在启动 Ollama 服务...")
        try:
            self._ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self._put_log("[Ollama] ⚠ 未找到 ollama 命令，请确认已安装")
            with _OLLAMA_LOCK:
                _OLLAMA_STATE["status"] = "stopped"
            return

        for _ in range(20):
            time.sleep(0.5)
            try:
                requests.get(ollama_url, timeout=1)
                self._put_log("[Ollama] ✓ Ollama 服务已就绪")
                with _OLLAMA_LOCK:
                    _OLLAMA_STATE["status"] = "running"
                return
            except Exception:
                pass

        self._put_log("[Ollama] ⚠ Ollama 启动超时，请手动运行: ollama serve")
        with _OLLAMA_LOCK:
            _OLLAMA_STATE["status"] = "stopped"

    def _stop_ollama(self):
        """Terminate the ollama serve process if we started it."""
        if self._ollama_proc and self._ollama_proc.poll() is None:
            self._ollama_proc.terminate()
            self._ollama_proc = None
        with _OLLAMA_LOCK:
            _OLLAMA_STATE["status"] = "stopped"

    def _meter_loop(self):
        """Reads mic input and feeds level to OnAirWidget at ~30fps."""
        stream = None
        pa     = None
        try:
            import numpy as np
            import pyaudiowpatch as pyaudio
            pa = pyaudio.PyAudio()
            try:
                info = pa.get_default_input_device_info()
                idx  = int(info["index"])
                sr   = int(info["defaultSampleRate"])
            except Exception:
                return
            chunk  = 512
            stream = pa.open(
                format=pyaudio.paInt16, channels=1, rate=sr,
                input=True, frames_per_buffer=chunk,
                input_device_index=idx,
            )
            while self._meter_active:
                data  = stream.read(chunk, exception_on_overflow=False)
                arr   = np.frombuffer(data, dtype=np.int16)
                rms   = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                level = min(rms / 4000.0, 1.0)
                if hasattr(self, "_onair"):
                    self._onair.set_level(level)
        except Exception:
            pass
        finally:
            # Always release hardware, even on crash
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa:
                try:
                    pa.terminate()
                except Exception:
                    pass
            # If we crashed while still supposed to be active, auto-hide the widget
            if self._meter_active:
                self._meter_active = False
                if hasattr(self, "_onair"):
                    try:
                        self.after(0, self._onair.hide)
                    except Exception:
                        pass

    def _on_mode_change(self, selected_label: str):
        """Mode toggle: meeting <-> local_mic. Saves to config immediately."""
        code = "local_mic" if selected_label == t("mode_local_mic") else "meeting"
        if not self._cfg.has_section("recording"):
            self._cfg.add_section("recording")
        self._cfg.set("recording", "mode", code)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            self._cfg.write(f)
        self._put_log(f"[UI] Mode: {code}")

    def _open_voice_note(self):
        """Deprecated: mode is now set via the mode selector in the control bar."""
        pass

    def _on_quick_lang_change(self, selected_label: str):
        """コントロールバーの言語セレクターが変更されたとき config.ini を即時保存する。"""
        code = self._lang_code_map.get(selected_label, "auto")
        if not self._cfg.has_section("recording"):
            self._cfg.add_section("recording")
        self._cfg.set("recording", "language", code)
        with open(CFG_FILE, "w", encoding="utf-8") as f:
            self._cfg.write(f)
        # 設定タブのラジオボタンも同期
        if hasattr(self, "_lang_var"):
            self._lang_var.set(code)
        self._put_log(f"[UI] Language set to: {code}")

    def _cuda_libs_ok(self) -> bool:
        return _CUDA_LIBS_OK

    def _cuda_available(self) -> bool:
        return _CUDA_AVAILABLE

    def _force_cpu_tiny_config(self) -> bool:
        if not self._cfg.has_section("recording"):
            self._cfg.add_section("recording")
        changed = False
        if self._cfg.get("recording", "device", fallback="auto") != "cpu":
            self._cfg.set("recording", "device", "cpu")
            changed = True
        if self._cfg.get("recording", "model_size", fallback="small") != "tiny":
            self._cfg.set("recording", "model_size", "tiny")
            changed = True
        if changed:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)
        return changed

    def _auto_select_backend(self):
        """If Ollama is running at startup, prefer it and sync the model name."""
        import requests
        ollama_url = self._cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
        try:
            requests.get(ollama_url, timeout=2)
        except Exception:
            return  # Ollama not running — leave config as-is

        # Ollama is reachable: mark global state so _ensure_ollama_running skips re-launch
        with _OLLAMA_LOCK:
            _OLLAMA_STATE["status"] = "running"

        changed = False
        if not self._cfg.has_section("summary"):
            self._cfg.add_section("summary")

        if self._cfg.get("summary", "mode", fallback="openai") != "ollama":
            self._cfg.set("summary", "mode", "ollama")
            changed = True

        # Sync model name to an actually-installed model
        try:
            resp   = requests.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=2)
            models = [m["name"] for m in resp.json().get("models", [])]
            if models:
                current = self._cfg.get("summary", "ollama_model", fallback="")
                if current not in models:
                    self._cfg.set("summary", "ollama_model", models[0])
                    changed = True
        except Exception:
            pass

        if changed:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)

    def _log_startup_info(self):
        """Print a startup summary to the log panel."""
        self._put_log("─" * 48)
        self._put_log("[起動] Sound2Text 初期化完了")

        # GPU / CUDA
        if _CUDA_AVAILABLE and _CUDA_LIBS_OK:
            self._put_log("[起動] GPU: RTX 検出済み — CUDA 使用可能 ✓")
        elif _CUDA_AVAILABLE:
            self._put_log("[起動] GPU: 検出済みですが cuBLAS が見つかりません ⚠")
        else:
            self._put_log("[起動] GPU: なし — CPU モードで動作")

        # Whisper model & device
        model  = self._cfg.get("recording", "model_size", fallback="small")
        device = self._cfg.get("recording", "device",     fallback="auto")
        self._put_log(f"[起動] Whisper: モデル={model}  デバイス={device}")

        # Recording mode
        rec_mode = self._cfg.get("recording", "mode", fallback="meeting")
        mic_on   = self._cfg.getboolean("recording", "enable_mic", fallback=True)
        self._put_log(f"[起動] 録音モード: {rec_mode}  マイク: {'ON' if mic_on else 'OFF'}")

        # Summary backend
        sum_mode = self._cfg.get("summary", "mode", fallback="openai")
        if sum_mode == "ollama":
            ollama_url   = self._cfg.get("summary", "ollama_url",   fallback="http://localhost:11434")
            ollama_model = self._cfg.get("summary", "ollama_model", fallback="qwen2.5:7b")
            self._put_log(f"[起動] 要約: Ollama ({ollama_model})  {ollama_url}")
        else:
            api_base = self._cfg.get("summary", "api_base", fallback="")
            model_id = self._cfg.get("summary", "model",    fallback="")
            self._put_log(f"[起動] 要約: OpenAI 互換 API  {model_id}")

        self._put_log("─" * 48)

    def _apply_cpu_only_defaults(self, log=False):
        """Check CUDA availability and warn if GPU cannot be used."""
        has_gpu    = self._cuda_available()
        has_libs   = self._cuda_libs_ok() if has_gpu else True
        cfg_device = self._cfg.get("recording", "device", fallback="auto")

        def _lock_to_cpu_tiny():
            """Force CPU+tiny in config and disable all non-tiny model/GPU buttons."""
            self._force_cpu_tiny_config()
            if hasattr(self, "_device_var"):
                self._device_var.set("cpu")
            if hasattr(self, "_model_var"):
                self._model_var.set("tiny")
            if hasattr(self, "_btn_cuda"):
                self._btn_cuda.configure(state="disabled")
            for btn in getattr(self, "_gpu_locked_buttons", []):
                btn.configure(state="disabled")

        if not has_gpu:
            # No GPU at all - silently lock to CPU + tiny
            _lock_to_cpu_tiny()
            if log:
                self._put_log(f"[UI] {t('gpu_unavailable')}")
            return

        if has_gpu and not has_libs:
            # GPU detected but cuBLAS missing → warn and lock to CPU + tiny
            _lock_to_cpu_tiny()
            if hasattr(self, "_btn_cuda"):
                self._btn_cuda.configure(state="disabled",
                                          text="CUDA (GPU) — install CUDA Toolkit to enable")

            self._put_log("[UI] ⚠ NVIDIA GPU detected but CUDA runtime libraries are missing.")
            self._put_log("[UI]   GPU option and large models have been disabled.")
            self._put_log("[UI]   Install CUDA Toolkit to enable GPU + larger models:")
            self._put_log("[UI]   > winget install Nvidia.CUDA")
            self._put_log("[UI]   https://developer.nvidia.com/cuda-downloads")
            return

        # GPU + cuBLAS OK — restore/upgrade settings that were conservatively capped
        cfg_model = self._cfg.get("recording", "model_size", fallback="small")
        changed   = False

        if not self._cfg.has_section("recording"):
            self._cfg.add_section("recording")

        if cfg_device == "cpu":
            self._cfg.set("recording", "device", "auto")
            if hasattr(self, "_device_var"):
                self._device_var.set("auto")
            changed = True
            if log:
                self._put_log("[UI] CUDA GPU detected — device reset to Auto (GPU).")
        elif log:
            self._put_log("[UI] CUDA GPU and cuBLAS detected. GPU acceleration available.")

        # Upgrade model to large-v3 if still at a conservative default (tiny/small)
        if cfg_model in ("tiny", "small"):
            self._cfg.set("recording", "model_size", "large-v3")
            if hasattr(self, "_model_var"):
                self._model_var.set("large-v3")
            changed = True
            if log:
                self._put_log("[UI] GPU available — Whisper model upgraded to large-v3 (best accuracy).")

        if changed:
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                self._cfg.write(f)

    def _ui(self, fn, *args, **kwargs):
        self.after(0, lambda: fn(*args, **kwargs))

    def _set_status(self, label, text_key, color):
        self._ui(label.configure, text=t(text_key), text_color=color)

    def _set_controls_idle(self):
        self._running = False
        self._stopping = False
        self._btn_start.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        if hasattr(self, "_dashboard"):
            self._dashboard.reset()

    def _read_last_transcript(self) -> str:
        if not os.path.exists(STATE_FILE):
            return ""
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()

    def _latest_file(self, directory: str, pattern: str) -> str:
        if not directory or not os.path.isdir(directory):
            return ""
        import glob
        files = glob.glob(os.path.join(directory, pattern))
        return max(files, key=os.path.getmtime) if files else ""

    def _wait_process(self, proc, name: str, timeout_sec=None, interval_sec=5) -> bool:
        if not proc:
            return True
        start = time.monotonic()
        next_log = interval_sec   # first check after one interval
        _recording_msg_shown = False
        _last_seen_activity  = self._tr_last_activity

        while proc.poll() is None:
            elapsed = int(time.monotonic() - start)

            if name == "transcriber.py":
                now_activity = self._tr_last_activity
                if now_activity != _last_seen_activity:
                    # New file started processing — reset so we'll show "录音中" again after quiet
                    _last_seen_activity  = now_activity
                    _recording_msg_shown = False
                    next_log = elapsed + interval_sec

                if elapsed >= next_log:
                    quiet = (time.monotonic() - self._tr_last_activity) > 3.0
                    if self._running and quiet and not _recording_msg_shown:
                        self._put_log("[UI] 収録中... 次のチャンクを待っています")
                        _recording_msg_shown = True
                        next_log = elapsed + interval_sec
                    elif not self._running:
                        if self._tr_current_file:
                            self._put_log(f"[UI] 転写中: {self._tr_current_file}  elapsed={elapsed}s")
                        next_log = elapsed + interval_sec
            else:
                if elapsed >= next_log:
                    self._put_log(f"[UI] Waiting for {name} to finish... elapsed={elapsed}s")
                    next_log = elapsed + interval_sec
            if timeout_sec is not None and elapsed >= timeout_sec:
                self._put_log(f"[UI] {name} did not finish within {timeout_sec}s. Terminating.")
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._put_log(f"[UI] {name} still running. Killing process.")
                    proc.kill()
                    proc.wait()
                return False
            time.sleep(0.5)
        self._put_log(f"[UI] {name} exited with code {proc.returncode}")
        return True

    def _start(self):
        if self._running:
            return
        if os.path.exists(STOP_SIGNAL):
            os.remove(STOP_SIGNAL)
        for state_file in (STATE_FILE, LANG_FILE):
            if os.path.exists(state_file):
                os.remove(state_file)

        # Write recording start time — transcriber skips files older than this
        with open(START_FILE, "w") as _sf:
            _sf.write(str(time.time()))
        self._put_log(f"[UI] Recording started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        # 開始前に config を再読み込み（前回の自動検出結果を反映）
        self._reload_config()
        self._apply_cpu_only_defaults(log=True)

        self._running = True
        self._stopping = False
        self._btn_start.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._lbl_rec.configure(text=t("running"), text_color="#44dd44")
        self._lbl_tr.configure(text=t("running"),  text_color="#44dd44")
        self._lbl_sum.configure(text=t("standby"), text_color="gray60")

        audio_dir = self._cfg.get("paths", "audio_dir", fallback="")
        transcript_dir = self._cfg.get("paths", "transcript_dir", fallback="")
        corrected_dir = self._cfg.get("summary", "corrected_dir", fallback="")
        summary_dir = self._cfg.get("summary", "summary_dir", fallback="")
        mode = self._cfg.get("summary", "mode", fallback="openai")
        language = self._cfg.get("recording", "language", fallback="auto")
        record_sec = self._cfg.get("recording", "record_sec", fallback="30")

        self._put_log("[UI] Start requested")
        self._put_log(f"[UI] Config loaded: mode={mode}, language={language}, record_sec={record_sec}s")
        self._put_log(f"[UI] Audio directory: {audio_dir}")
        self._put_log(f"[UI] Transcript directory: {transcript_dir}")
        self._put_log(f"[UI] Corrected text directory: {corrected_dir}")
        self._put_log(f"[UI] Summary directory: {summary_dir}")

        rec_mode = self._cfg.get("recording", "mode", fallback="meeting").strip().lower()

        # Launch loopback recorder only in meeting mode
        self._rec_proc = None
        if rec_mode == "meeting":
            self._put_log("[UI] Launching recorder.py (loopback)")
            self._rec_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "recorder.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(self._rec_proc, "[Rec]"), daemon=True).start()
            self._put_log(f"[UI] recorder.py started: pid={self._rec_proc.pid}")
        else:
            self._put_log("[UI] Local Mic mode: loopback recorder skipped")

        # Launch mic recorder if enabled; show OnAir meter whenever mic is active
        self._mic_proc = None
        if self._cfg.getboolean("recording", "enable_mic", fallback=True):
            self._put_log("[UI] Launching mic_recorder.py")
            self._mic_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "mic_recorder.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(self._mic_proc, "[Mic]"), daemon=True).start()
            self._onair.show()
            self._start_meter()
        if hasattr(self, "_dashboard"):
            self._dashboard.start()

        if self._tr_proc and self._tr_proc.poll() is None:
            self._put_log(f"[UI] transcriber.py already running (pre-loaded): pid={self._tr_proc.pid}")
        else:
            self._put_log("[UI] Launching transcriber.py")
            self._tr_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "transcriber.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            self._put_log(f"[UI] transcriber.py started: pid={self._tr_proc.pid}")
            threading.Thread(target=self._pipe, args=(self._tr_proc, "[Tr]"), daemon=True).start()

        if self._rec_proc:
            threading.Thread(target=self._pipe, args=(self._rec_proc, "[Rec]"), daemon=True).start()
        threading.Thread(target=self._after_trans,                         daemon=True).start()

        self._put_log(t("starting"))

    def _stop(self):
        if not self._running:
            return
        if self._stopping:
            self._put_log("[UI] Stop is already in progress")
            return
        self._stopping = True
        self._btn_stop.configure(state="disabled")
        self._put_log(t("stopping"))

        # Hide ON AIR widget and stop meter
        self._stop_meter()
        self._onair.hide()
        if hasattr(self, "_dashboard"):
            self._dashboard.stop()

        self._put_log("[UI] Creating stop signal for transcriber.py")
        with open(STOP_SIGNAL, "w", encoding="utf-8") as f:
            f.write("stop")

        if self._rec_proc and self._rec_proc.poll() is None:
            self._put_log("[UI] Stopping recorder.py")
            self._rec_proc.terminate()
        else:
            self._put_log("[UI] recorder.py is already stopped")
        self._lbl_rec.configure(text=t("stopped"), text_color="gray60")

        if self._mic_proc and self._mic_proc.poll() is None:
            self._put_log("[UI] Stopping mic_recorder.py")
            self._mic_proc.terminate()
        self._put_log("[UI] Waiting for transcriber.py to drain remaining audio files")

    def _check_summary_backend(self) -> tuple[bool, str]:
        """Return (ok, reason). Called before launching summarizer to catch obvious failures early."""
        import requests as _req
        mode = self._cfg.get("summary", "mode", fallback="openai")

        if mode == "ollama":
            ollama_url = self._cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
            try:
                _req.get(ollama_url, timeout=3)
                return True, ""
            except Exception:
                return False, (
                    f"Ollama サーバーに接続できません ({ollama_url})。"
                    " 'ollama serve' を実行してください。"
                )

        if mode == "openai":
            api_key  = self._cfg.get("summary", "api_key",  fallback="")
            api_base = self._cfg.get("summary", "api_base", fallback="")
            if not api_key or api_key == "your_api_key_here":
                return False, "API Key が設定されていません。設定タブで API Key を入力してください。"
            # Light connectivity check — just TCP connect, no actual API call
            try:
                import urllib.parse
                host = urllib.parse.urlparse(api_base).netloc or api_base
                _req.get(f"https://{host}", timeout=4)
                return True, ""
            except Exception:
                return False, (
                    f"API サーバーに接続できません ({api_base})。"
                    " ネットワーク接続またはプロキシ設定を確認してください。"
                )

        return True, ""

    def _after_trans(self):
        self._wait_process(self._tr_proc, "transcriber.py", timeout_sec=900)
        self._set_status(self._lbl_tr, "stopped", "gray60")

        transcript_path = self._read_last_transcript()
        if transcript_path:
            self._put_log(f"[UI] Transcript file: {transcript_path}")
        else:
            self._put_log("[UI] Transcript file was not created")

        if transcript_path and os.path.exists(transcript_path):
            # ── pre-check: verify AI backend is reachable before launching ──
            ok, reason = self._check_summary_backend()
            if not ok:
                self._put_log(f"[UI] ⚠ {reason}")
                self._put_log("[UI] 会議纪要の生成をスキップしました。設定を確認してください。")
                self._set_status(self._lbl_sum, "failed", "#dd4444")
            else:
                self._set_status(self._lbl_sum, "generating", "#ddaa00")
                self._put_log(t("sum_start"))
                self._put_log("[UI] Launching summarizer.py")
                sum_proc = subprocess.Popen(
                    [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py")],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", env=self._env,
                )
                self._put_log(f"[UI] summarizer.py started: pid={sum_proc.pid}")
                threading.Thread(target=self._pipe, args=(sum_proc, "[Sum]"), daemon=True).start()
                self._wait_process(sum_proc, "summarizer.py", timeout_sec=900)

                if sum_proc.returncode == 0:
                    corrected = self._latest_file(
                        self._cfg.get("summary", "corrected_dir", fallback=""), "corrected_*.txt")
                    summary   = self._latest_file(
                        self._cfg.get("summary", "summary_dir",   fallback=""), "summary_*.md")
                    if corrected:
                        self._put_log(f"[UI] Corrected text file: {corrected}")
                    if summary:
                        self._put_log(f"[UI] Summary file: {summary}")
                    self._set_status(self._lbl_sum, "done", "#44dd44")
                else:
                    self._put_log("[UI] ⚠ 会議纪要の生成に失敗しました。[Sum] ログを確認してください。")
                    self._set_status(self._lbl_sum, "failed", "#dd4444")
        else:
            self._put_log("[UI] Summary skipped because transcript file is missing")
            self._set_status(self._lbl_sum, "skipped", "gray60")

        self._ui(self._set_controls_idle)
        self._put_log(t("all_done"))

    @staticmethod
    def _wav_secs(path: str) -> float:
        """Return duration in seconds of a WAV file, or 0.0 on error."""
        import wave as _wave
        try:
            with _wave.open(path, "rb") as wf:
                return wf.getnframes() / wf.getframerate()
        except Exception:
            return 0.0

    def _pipe(self, proc, prefix):
        import re
        _tr_file_re  = re.compile(r"\d+/\d+:\s+(\S+\.wav)")
        _rec_save_re = re.compile(r"保存|Saved")
        _fname_re    = re.compile(r"((?:audio|mic)_\S+\.wav)")
        _tr_done_re  = re.compile(r"done \((loopback|mic)\):\s+(\S+\.wav)")
        _rec_sec     = self._cfg.getint("recording", "record_sec", fallback=30)
        audio_dir    = self._cfg.get("paths", "audio_dir", fallback="")
        mic_dir      = self._cfg.get("paths", "mic_dir",   fallback="")

        for line in proc.stdout:
            text = line.rstrip()
            if not text:
                continue

            if prefix == "[Tr]":
                m = _tr_file_re.search(text)
                if m:
                    self._tr_current_file  = m.group(1)
                    self._tr_last_activity = time.monotonic()
                dm = _tr_done_re.search(text)
                if dm and hasattr(self, "_dashboard"):
                    source, fname = dm.group(1), dm.group(2)
                    done_base = mic_dir if source == "mic" else audio_dir
                    secs = self._wav_secs(os.path.join(done_base, "done", fname))
                    if secs <= 0:
                        secs = float(_rec_sec)   # fallback to config value
                    self._dashboard.add_trans(secs)

            elif prefix in ("[Rec]", "[Mic]"):
                if _rec_save_re.search(text) and hasattr(self, "_dashboard"):
                    fm = _fname_re.search(text)
                    if fm:
                        fname = fm.group(1)
                        base  = mic_dir if fname.startswith("mic_") else audio_dir
                        secs  = self._wav_secs(os.path.join(base, fname))
                        if secs <= 0:
                            secs = float(_rec_sec)
                        self._dashboard.add_audio(secs)

            self._put_log(f"{prefix} {text}")

    # ── ログ ─────────────────────────────────────────────────────────────────

    def _put_log(self, msg: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._log_q.put(f"[{ts}] {msg}")

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
            # Recording in progress: trigger Stop and wait for all post-processing
            self._put_log("[UI] Window close requested. Finishing current processing...")
            self._put_log("[UI] Please wait. (Press X again to force quit)")
            self.title(self.title() + "  [Ending...]")

            if not self._stopping:
                self._stop()   # send stop signal, terminate recorder/mic

            # Start monitoring loop - close only after all processing is done
            self._force_close = False
            self._close_pending = True
            self._check_and_close()
        else:
            self._stop_ollama()
            self.destroy()

    def _check_and_close(self):
        """Poll until processing finishes, then destroy. Handles force-close on 2nd X press."""
        if self._force_close or not self._running:
            self._put_log("[UI] Processing complete. Closing.")
            self.after(300, self.destroy)
            return
        self.after(400, self._check_and_close)

    def _force_quit(self):
        """Second close attempt → force quit immediately."""
        self._force_close = True
        self._stop_meter()
        self._stop_ollama()
        if hasattr(self, "_onair"):
            self._onair.hide()
        if self._tr_proc and self._tr_proc.poll() is None:
            self._tr_proc.kill()
        self.destroy()


if __name__ == "__main__":
    app = App()
    _close_count = [0]

    def _on_close_with_force():
        _close_count[0] += 1
        if _close_count[0] >= 2:
            app._force_quit()
        else:
            app.on_close()

    app.protocol("WM_DELETE_WINDOW", _on_close_with_force)
    app.mainloop()

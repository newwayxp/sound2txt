"""
i18n.py – UI translation strings and language detection for Sound2Text.
"""
import locale
import os
import sys


def _detect_os_lang() -> str:
    """Return the raw OS locale/language string, platform-aware."""
    if sys.platform == "win32":
        try:
            return (locale.getdefaultlocale()[0] or "en").lower()
        except Exception:
            return "en"

    if sys.platform == "darwin":
        # 1. macOS system preference (most reliable for GUI apps) — check first
        try:
            import subprocess
            result = subprocess.run(
                ["defaults", "read", "NSGlobalDomain", "AppleLanguages"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.splitlines():
                lang = line.strip().strip('",').split("-")[0].lower()
                if len(lang) >= 2 and lang.isalpha():
                    return lang
        except Exception:
            pass
        # 2. Environment variables (only if system preference not available)
        for key in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
            val = os.environ.get(key, "")
            if val and val not in ("C", "POSIX", "C.UTF-8"):
                return val.lower()
        # 3. locale.getlocale() as final fallback (not deprecated)
        try:
            return (locale.getlocale()[0] or "en").lower()
        except Exception:
            return "en"

    # Linux / other
    for key in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(key, "")
        if val and val not in ("C", "POSIX", "C.UTF-8"):
            return val.lower()
    try:
        return (locale.getlocale()[0] or "en").lower()
    except Exception:
        return "en"


def _ui_lang() -> str:
    lang = _detect_os_lang()
    if lang.startswith("zh"):
        return "zh"
    if lang.startswith("ja"):
        return "ja"
    return "en"


_LANG: str = _ui_lang()

_T: dict[str, dict[str, str]] = {
    "title":         {"zh": "Sound2Text 控制台",          "ja": "Sound2Text コントロール",       "en": "Sound2Text Control Panel"},
    "start":         {"zh": "▶  开始",                    "ja": "▶  開始",                       "en": "▶  Start"},
    "stop":          {"zh": "■  停止",                    "ja": "■  停止",                       "en": "■  Stop"},
    "rec_label":     {"zh": "录音",                       "ja": "録音",                           "en": "Rec"},
    "tr_label":      {"zh": "转写",                       "ja": "転写",                           "en": "Trans"},
    "sum_label":     {"zh": "纪要",                       "ja": "議事録",                         "en": "Minutes"},
    "idle":          {"zh": "● 停止",                     "ja": "● 停止",                        "en": "● Idle"},
    "running":       {"zh": "● 运行中",                   "ja": "● 実行中",                       "en": "● Running"},
    "stopped":       {"zh": "● 停止",                     "ja": "● 停止",                        "en": "● Stopped"},
    "generating":    {"zh": "● 生成中",                   "ja": "● 生成中",                       "en": "● Generating"},
    "done":          {"zh": "● 完成",                     "ja": "● 完了",                        "en": "● Done"},
    "skipped":       {"zh": "● 跳过",                     "ja": "● スキップ",                     "en": "● Skipped"},
    "standby":       {"zh": "● 待机",                     "ja": "● 待機",                        "en": "● Standby"},
    "tab_paths":     {"zh": "📁  路径",                   "ja": "📁  保存先",                     "en": "📁  Paths"},
    "tab_rec":       {"zh": "🎙  录音",                   "ja": "🎙  録音",                       "en": "🎙  Recording"},
    "tab_api":       {"zh": "⚙  AI 设置",                "ja": "⚙  AI 設定",                    "en": "⚙  AI Settings"},
    "save":          {"zh": "💾  保存设置",               "ja": "💾  設定を保存",                 "en": "💾  Save Settings"},
    "saved":         {"zh": "[UI] 设置已保存",            "ja": "[UI] 設定を保存しました",        "en": "[UI] Settings saved"},
    "log_title":     {"zh": "运行日志",                   "ja": "実行ログ",                       "en": "Log"},
    "clear_log":     {"zh": "清空",                       "ja": "クリア",                        "en": "Clear"},
    "starting":      {"zh": "[UI] 启动成功",              "ja": "[UI] 起動しました",              "en": "[UI] Started"},
    "stopping":      {"zh": "[UI] 正在停止...",           "ja": "[UI] 停止中...",                 "en": "[UI] Stopping..."},
    "sum_start":     {"zh": "[UI] 生成会议纪要...",       "ja": "[UI] 議事録を生成中...",         "en": "[UI] Generating minutes..."},
    "all_done":      {"zh": "[UI] 全部完成",              "ja": "[UI] すべて完了",               "en": "[UI] All done"},
    "audio_dir":     {"zh": "音频保存目录",               "ja": "音声保存先",                     "en": "Audio directory"},
    "tr_dir":        {"zh": "转写文件目录",               "ja": "文字起こし保存先",               "en": "Transcript directory"},
    "corr_dir":      {"zh": "校正文件目录",               "ja": "校正テキスト保存先",             "en": "Corrected text directory"},
    "sum_dir":       {"zh": "纪要保存目录",               "ja": "議事録保存先",                   "en": "Minutes directory"},
    "rec_sec":       {"zh": "每段录音时长（秒）",         "ja": "1チャンク録音秒数",              "en": "Chunk duration (sec)"},
    "mode_label":    {"zh": "后端模式",                   "ja": "バックエンド",                   "en": "Backend"},
    "mode_openai":   {"zh": "OpenAI 兼容 API（Groq / DeepSeek 等）", "ja": "OpenAI 互換 API（Groq / DeepSeek 等）", "en": "OpenAI-compatible API (Groq / DeepSeek etc.)"},
    "mode_ollama":   {"zh": "Ollama（本地）",             "ja": "Ollama（ローカル）",             "en": "Ollama (local)"},
    "presets":       {"zh": "快速预设",                   "ja": "プリセット",                     "en": "Presets"},
    "api_base":      {"zh": "API Base URL",               "ja": "API Base URL",                   "en": "API Base URL"},
    "api_key":       {"zh": "API Key",                    "ja": "API Key",                        "en": "API Key"},
    "cloud_model":   {"zh": "Cloud Model",                "ja": "クラウドモデル",                 "en": "Cloud Model"},
    "ollama_model":  {"zh": "Ollama Model",               "ja": "Ollama モデル",                  "en": "Ollama Model"},
    "device_label":  {"zh": "运算设备",                   "ja": "実行デバイス",                   "en": "Device"},
    "device_auto":   {"zh": "自动（有GPU则用GPU）",        "ja": "自動（GPU 優先）",               "en": "Auto (GPU if available)"},
    "gpu_unavailable":{"zh": "未检测到 CUDA GPU，已固定为 CPU + tiny", "ja": "CUDA GPU が見つからないため CPU + tiny に固定しました", "en": "CUDA GPU not detected; fixed to CPU + tiny"},
    "model_label":   {"zh": "Whisper 模型",               "ja": "Whisper モデル",                 "en": "Whisper model"},
    "lang_label":    {"zh": "转写语言",                   "ja": "転写言語",                       "en": "Transcription language"},
    "lang_auto":     {"zh": "自动检测",                   "ja": "自動検出",                       "en": "Auto detect"},
    "lang_zh":       {"zh": "中文 (简体)",               "ja": "中国語（簡体字）",               "en": "Chinese (Simplified)"},
    "lang_ja":       {"zh": "日语",                      "ja": "日本語",                         "en": "Japanese"},
    "lang_en":       {"zh": "英语",                      "ja": "英語",                           "en": "English"},
    "tab_transcript":{"zh": "📝  转写结果",              "ja": "📝  文字起こし",                 "en": "📝  Transcript"},
    "tab_minutes":   {"zh": "📋  会议纪要",              "ja": "📋  議事録",                     "en": "📋  Minutes"},
    "tab_network":   {"zh": "🌐  代理",                  "ja": "🌐  ネットワーク",               "en": "🌐  Network"},
    "https_proxy":   {"zh": "HTTPS 代理",                "ja": "HTTPS プロキシ",                 "en": "HTTPS Proxy"},
    "http_proxy":    {"zh": "HTTP 代理",                 "ja": "HTTP プロキシ",                  "en": "HTTP Proxy"},
    "ssl_verify":    {"zh": "SSL 证书验证",              "ja": "SSL 証明書の検証",               "en": "SSL certificate verify"},
    "ssl_on":        {"zh": "启用（默认）",              "ja": "有効（デフォルト）",             "en": "Enabled (default)"},
    "ssl_off":       {"zh": "禁用（企业代理自签名证书）","ja": "無効（社内プロキシ自己署名証明書）", "en": "Disabled (self-signed corp proxy)"},
    "proxy_hint":    {"zh": "留空则不使用代理",          "ja": "空欄の場合はプロキシなし",       "en": "Leave blank to disable proxy"},
    # Subtitle
    "subtitle_btn":  {"zh": "字幕",                      "ja": "字幕",                           "en": "Subtitle"},
    "subtitle_on":   {"zh": "● 字幕显示中",              "ja": "● 字幕表示中",                   "en": "● Subtitles ON"},
    "subtitle_off":  {"zh": "● 字幕关闭",                "ja": "● 字幕オフ",                     "en": "● Subtitles OFF"},
    "sub_src_lang":  {"zh": "识别语言",                  "ja": "認識言語",                       "en": "Source language"},
    "sub_dst_lang":  {"zh": "显示语言（翻译）",          "ja": "表示言語（翻訳）",               "en": "Display language (translate)"},
    "sub_dst_same":  {"zh": "与识别语言相同",            "ja": "認識言語のまま",                 "en": "Same as source"},
    "on_air":        {"zh": "ON AIR",                    "ja": "ON AIR",                         "en": "ON AIR"},
    "rec_time":      {"zh": "录音时间",                  "ja": "録音時間",                       "en": "Rec Time"},
    "trans_time":    {"zh": "转写时间",                  "ja": "転写時間",                       "en": "Trans Time"},
    # Recording tab
    "mic_enable":    {"zh": "麦克风录音",                "ja": "マイク録音",                     "en": "Microphone recording"},
    "mic_enable_hint":{"zh": "同时录制本地麦克风（自己的发言）","ja": "自分の発言もマイクで録音する","en": "Also record local mic (your own voice)"},
    # Recording mode buttons
    "mode_meeting":  {"zh": "会议模式",                  "ja": "会議モード",                     "en": "Meeting Mode"},
    "mode_local_mic":{"zh": "本地麦克风",               "ja": "マイクのみ",                     "en": "Mic Only"},
    # Ollama URL
    "ollama_url":    {"zh": "Ollama 服务器地址",         "ja": "Ollama サーバー URL",            "en": "Ollama Server URL"},
    # Failed status
    "failed":        {"zh": "● 失败",                    "ja": "● 失敗",                        "en": "● Failed"},
    "ptt_speak":          {"zh": "发言",                  "ja": "発言",                           "en": "Speak"},
    "ptt_speaking":       {"zh": "发言中",               "ja": "発言中",                         "en": "Speaking"},
    "transcript_hint":    {"zh": "开始录音后，转写结果将实时显示在这里。",
                           "ja": "録音を開始すると、文字起こし結果がリアルタイムで表示されます。",
                           "en": "Transcribed text will appear here in real time after starting."},
    "minutes_hint":       {"zh": "停止后，会议纪要将自动生成并显示在这里。",
                           "ja": "停止後、議事録が自動生成されてここに表示されます。",
                           "en": "Meeting minutes will appear here automatically after stopping."},
}


def t(key: str) -> str:
    """Return the UI string for *key* in the current UI language."""
    return _T.get(key, {}).get(_LANG, _T.get(key, {}).get("en", key))

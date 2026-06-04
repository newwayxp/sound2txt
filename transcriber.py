"""
文字起こしプロセス: AUDIO_DIR を監視し、新しい WAV ファイルを順番に文字起こしする
処理済みファイルは done/ サブディレクトリに移動して管理する

停止時の処理:
  1. 録音プロセスの書き込み完了を少し待つ
  2. audio_dir に残っている全ファイルを変換してから終了
  3. トランスクリプトパスを .last_transcript に書いて終了
     （会议纪要の生成は start.py が summarizer.py を呼んで行う）
"""
import os
import configparser

# ── CUDA DLL compatibility (must run before any ctranslate2/faster-whisper import) ──
def _setup_cuda_dlls():
    """
    1. Add all CUDA toolkit bin dirs to the DLL search path.
    2. If ctranslate2 needs cublas64_12.dll but only cublas64_13.dll exists,
       create a compatibility copy inside ctranslate2's own package directory.
       This is fully dynamic — no hardcoded paths.
    """
    import ctypes, shutil

    # ── Step 1: collect CUDA bin directories ──────────────────────────────────
    cuda_dirs = []
    cuda_root = os.environ.get("CUDA_PATH", "")
    if cuda_root:
        cuda_dirs += [os.path.join(cuda_root, "bin"), os.path.join(cuda_root, "bin", "x64")]
    toolkit_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.isdir(toolkit_base):
        for ver in sorted(os.listdir(toolkit_base), reverse=True):
            cuda_dirs += [
                os.path.join(toolkit_base, ver, "bin"),
                os.path.join(toolkit_base, ver, "bin", "x64"),
            ]

    # ── Step 2: register dirs so dependency DLLs are found ───────────────────
    if hasattr(os, "add_dll_directory"):
        for d in cuda_dirs:
            if os.path.isdir(d):
                try:
                    os.add_dll_directory(d)
                except Exception:
                    pass

    # ── Step 3: create cublas alias if needed ─────────────────────────────────
    # ctranslate2 compiled against CUDA 11/12 looks for cublas64_11/12.dll.
    # If the installed CUDA is 13+, create a named copy inside ctranslate2's
    # package directory so LoadLibrary can find it.
    needed = ["cublas64_12.dll", "cublas64_11.dll"]
    if any(_dll_loadable(n) for n in needed):
        return   # already satisfied — nothing to do

    # Find the actual cublas DLL (try highest version first)
    src_cublas = _find_dll_in_dirs(
        ["cublas64_13.dll", "cublas64_12.dll", "cublas64_11.dll"], cuda_dirs)
    src_cublaslt = _find_dll_in_dirs(
        ["cublasLt64_13.dll", "cublasLt64_12.dll", "cublasLt64_11.dll"], cuda_dirs)
    if not src_cublas:
        return  # no CUDA installed — nothing to do

    try:
        import ctranslate2 as _ct2
        ct2_dir = os.path.dirname(_ct2.__file__)
    except Exception:
        return

    _copy_dll_alias(src_cublas,   ct2_dir, "cublas64_12.dll")
    if src_cublaslt:
        _copy_dll_alias(src_cublaslt, ct2_dir, "cublasLt64_12.dll")


def _dll_loadable(name: str) -> bool:
    import ctypes
    try:
        ctypes.CDLL(name)
        return True
    except OSError:
        return False

def _find_dll_in_dirs(names, dirs):
    for name in names:
        for d in dirs:
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None

def _copy_dll_alias(src: str, dst_dir: str, alias: str):
    import shutil
    dst = os.path.join(dst_dir, alias)
    if os.path.exists(dst):
        return
    try:
        shutil.copy2(src, dst)
        print(f"[Transcriber] Created CUDA compat alias: {alias} -> {os.path.basename(src)}")
    except Exception as e:
        print(f"[Transcriber] Could not create {alias}: {e}")

_setup_cuda_dlls()

# ── ネットワーク設定（faster-whisper の import より先に実行） ──
_pre_cfg = configparser.ConfigParser()
_pre_cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")
if _pre_cfg.has_section("network"):
    _proxy = _pre_cfg.get("network", "https_proxy", fallback="")
    if _proxy:
        os.environ.setdefault("HTTPS_PROXY", _proxy)
        os.environ.setdefault("HTTP_PROXY",  _pre_cfg.get("network", "http_proxy", fallback=_proxy))
    if not _pre_cfg.getboolean("network", "ssl_verify", fallback=True):
        os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")
        os.environ.setdefault("CURL_CA_BUNDLE", "")
        os.environ.setdefault("REQUESTS_CA_BUNDLE", "")

os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "error")

import glob
import re
import time
import warnings
from faster_whisper import WhisperModel
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

POLL_SEC        = 1.0
EVAL_CHUNKS     = 1   # detect language after the first audio chunk (~record_sec seconds)
MIN_CONFIDENCE  = 0.5
SUPPORTED_LANGS = {"zh", "ja", "en"}
LANG_ALIAS      = {"yue": "zh", "zh-TW": "zh", "zh-HK": "zh"}

# OS のロケールからデフォルト言語を決定
def _detect_os_language() -> str:
    loc = ""
    try:
        # Windows: レジストリから取得（最も確実）
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International")
        loc = winreg.QueryValueEx(key, "LocaleName")[0].lower()  # 例: "ja-jp"
    except Exception:
        pass
    if not loc:
        try:
            import locale
            locale.setlocale(locale.LC_ALL, "")
            loc = (locale.getlocale()[0] or "").lower()
        except Exception:
            pass
    if loc.startswith("zh"):
        return "zh"
    if loc.startswith("ja"):
        return "ja"
    if loc.startswith("ko"):
        return "ko"
    return "en"

_OS_DEFAULT_LANG = _detect_os_language()

# UI からの停止シグナルファイル（ui.py が作成する）
STOP_SIGNAL  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stop_signal")
START_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".recording_start")
DRAIN_WAIT_SEC  = 3

STATE_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_transcript")
LANG_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_language")
START_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".recording_start")

HALLUCINATION_PHRASES = [
    # Japanese
    "ご視聴ありがとうございました", "チャンネル登録", "字幕",
    "ご視聴ありがとうございます", "高評価", "コメント欄",
    # English
    "thank you for watching", "please subscribe", "like and subscribe",
    # Chinese (common Whisper hallucinations in silent/noise segments)
    "请不吝点赞", "订阅", "转发", "打赏支持", "明镜与点点",
    "感谢观看", "关注我的频道", "点击订阅", "欢迎关注",
    "字幕由", "本视频",
]

# Speaker label for local mic input (by detected language)
_SELF_LABEL = {"zh": "[本人]", "ja": "[自分]", "en": "[Me]"}


def _parse_file_start_time(wav_path: str):
    """
    Parse recording start time from filename.
    Supports: audio_YYYYMMDD_HHMMSS_ffffff.wav
              mic_YYYYMMDD_HHMMSS_ffffff.wav
    Returns datetime or None.
    """
    name = os.path.basename(wav_path)
    m = re.match(r'^(?:audio|mic)_(\d{8})_(\d{6})_', name)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        except ValueError:
            pass
    return None

VOCAB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vocabulary.txt")

# 言語ごとのトランスクリプトヘッダー / フッター
_TRANSCRIPT_HEADER = {
    "zh": "=== 转写开始 {ts} ===",
    "ja": "=== 文字起こし開始 {ts} ===",
    "en": "=== Transcription started {ts} ===",
}
_TRANSCRIPT_FOOTER = {
    "zh": "=== 结束 {ts} ===",
    "ja": "=== 終了 {ts} ===",
    "en": "=== Ended {ts} ===",
}

def _header(lang: str | None, ts: str) -> str:
    return _TRANSCRIPT_HEADER.get(lang or "en", _TRANSCRIPT_HEADER["en"]).format(ts=ts)

def _footer(lang: str | None, ts: str) -> str:
    return _TRANSCRIPT_FOOTER.get(lang or "en", _TRANSCRIPT_FOOTER["en"]).format(ts=ts)

# 言語ごとの base initial_prompt
# zh は簡体字に誘導し、用語リストを後ろに追加する
_LANG_PROMPT_BASE: dict[str, str] = {
    "zh": "以下是普通话录音的简体中文转写内容，可能包含专有名词：",
    "ja": "以下は日本語の録音です。固有名詞・専門用語が含まれる場合があります：",
    "en": "The following is an audio transcript. It may include proper nouns and technical terms:",
}


def load_vocabulary() -> list[str]:
    """vocabulary.txt から有効な用語を読み込む。"""
    if not os.path.exists(VOCAB_FILE):
        return []
    terms = []
    with open(VOCAB_FILE, encoding="utf-8") as f:
        for line in f:
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return terms


def build_initial_prompt(lang: str | None, vocab: list[str]) -> str | None:
    """
    Whisper の initial_prompt を組み立てる。
    - lang が None（自動検出フェーズ）でも vocab があれば渡す
    - 用語リストをプロンプトに埋め込んでデコーダーを誘導する
    """
    base = _LANG_PROMPT_BASE.get(lang or "", "")
    if vocab:
        terms_str = "、".join(vocab) if lang == "ja" else ", ".join(vocab)
        return f"{base}{terms_str}。" if base else terms_str
    return base if base else None


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _write_recording_config(cfg, cfg_file, device, model_size):
    try:
        if not cfg.has_section("recording"):
            cfg.add_section("recording")
        changed = False
        if cfg.get("recording", "device", fallback="auto") != device:
            cfg.set("recording", "device", device)
            changed = True
        if cfg.get("recording", "model_size", fallback="small") != model_size:
            cfg.set("recording", "model_size", model_size)
            changed = True
        if changed:
            with open(cfg_file, "w", encoding="utf-8") as f:
                cfg.write(f)
            print(f"[Transcriber] config.ini updated: device={device}, model_size={model_size}")
    except Exception as e:
        print(f"[Transcriber] config.ini update skipped: {e}")


def main():
    _cfg = configparser.ConfigParser()
    _cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")
    audio_dir      = _cfg.get("paths", "audio_dir")
    mic_dir        = _cfg.get("paths", "mic_dir",    fallback=r"C:\Users\Public\Sound2Text\mic")
    transcript_dir = _cfg.get("paths", "transcript_dir")
    enable_mic     = _cfg.getboolean("recording", "enable_mic", fallback=True)
    rec_mode       = _cfg.get("recording", "mode", fallback="meeting").strip().lower()
    record_sec     = _cfg.getint("recording", "record_sec", fallback=30)
    # local_mic mode: mic only, no loopback scanning
    use_loopback   = (rec_mode != "local_mic")
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(transcript_dir, exist_ok=True)
    done_dir = os.path.join(audio_dir, "done")
    os.makedirs(done_dir, exist_ok=True)
    if enable_mic and mic_dir:
        os.makedirs(mic_dir, exist_ok=True)
        os.makedirs(os.path.join(mic_dir, "done"), exist_ok=True)
    else:
        mic_dir = ""  # disabled

    print(f"[Transcriber] mode={rec_mode}  loopback={'on' if use_loopback else 'off'}  mic={'on' if mic_dir else 'off'}")

    cfg_file   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")

    # config.ini の language 設定を読む
    # "auto" → 自動検出、"zh"/"ja"/"en" → 固定して即時開始
    cfg_lang = _cfg.get("recording", "language", fallback="auto").strip().lower()
    language  = cfg_lang if cfg_lang in SUPPORTED_LANGS else None

    # デバイス・モデルサイズ設定
    cfg_device     = _cfg.get("recording", "device",     fallback="auto").strip().lower()
    model_size     = _cfg.get("recording", "model_size", fallback="small").strip()

    if cfg_device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    else:
        device = cfg_device

    # CUDA ライブラリ（cuBLAS）の実在チェック
    if device == "cuda":
        import ctypes
        cublas_ok = False
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
                    cublas_ok = True
                    break
                except OSError:
                    pass
            if cublas_ok:
                break
        if not cublas_ok:
            print("[Transcriber] WARNING: CUDA GPU detected but cuBLAS library not found.")
            print("[Transcriber] Install CUDA Toolkit: https://developer.nvidia.com/cuda-downloads")
            print("[Transcriber] Falling back to CPU.")
            device = "cpu"

    compute_type = "float16" if device == "cuda" else "int8"

    print(f"[Transcriber] loading faster-whisper {model_size} (device={device}, compute={compute_type})")
    whisper = WhisperModel(model_size, device=device, compute_type=compute_type)
    print(f"[Transcriber] model loaded on {device}")
    print(f"[Transcriber] ready (output file will be created on first audio)")
    if language:
        print(f"[Transcriber] 言語固定: {language}（config.ini）")
    else:
        print(f"[Transcriber] 言語自動検出（{EVAL_CHUNKS}チャンク待機）")
    print("[Transcriber] 新規音声ファイルを待機中... (Ctrl+C で停止)\n")

    pending     = []
    lang_scores = {}
    seen        = set()
    self_label  = ""  # resolved after language detection

    vocab = load_vocabulary()
    if vocab:
        print(f"[Transcriber] 用語リスト: {len(vocab)} 件読み込み")

    def _transcribe(wav_path, lang):
        """For language detection: returns (full_text, info)."""
        prompt = build_initial_prompt(lang, vocab)
        try:
            segments, info = whisper.transcribe(
                wav_path, language=lang,
                initial_prompt=prompt,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text = " ".join(s.text.strip() for s in segments)
        except RuntimeError as e:
            print(f"[Transcriber] transcribe error: {e}")
            from types import SimpleNamespace
            return "", SimpleNamespace(language="en", language_probability=0.0)
        if any(p in text for p in HALLUCINATION_PHRASES):
            text = ""
        return text, info

    def _transcribe_lines(wav_path, lang, self_label=""):
        """
        Transcribe and return formatted lines with accurate per-segment timestamps.
        self_label: speaker label string (e.g. '[Me]') for mic input, empty for loopback.
        Timestamps are computed as: file_start_time + segment.start offset.
        """
        nonlocal whisper, device, compute_type
        prompt     = build_initial_prompt(lang, vocab)
        file_start = _parse_file_start_time(wav_path)
        try:
            segments, _ = whisper.transcribe(
                wav_path, language=lang,
                initial_prompt=prompt,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            seg_list = list(segments)  # force evaluation here to catch GPU errors
        except RuntimeError as e:
            err_msg = str(e)
            if "cublas" in err_msg.lower() or "cuda" in err_msg.lower():
                # GPU 推論失敗 → CPU に永続フォールバック（以降は再試行しない）
                print(f"[Transcriber] GPU inference error: {e}")
                print("[Transcriber] Reloading model on CPU (permanent fallback)...")
                device       = "cpu"
                compute_type = "int8"
                whisper      = WhisperModel(model_size, device="cpu", compute_type="int8")
                print("[Transcriber] Reloaded on CPU. Retrying...")
                segments, _ = whisper.transcribe(
                    wav_path, language=lang, initial_prompt=prompt,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                )
                seg_list = list(segments)
            else:
                print(f"[Transcriber] transcribe error: {e}")
                return []
        # Return (absolute_datetime, formatted_line) pairs so caller can sort
        segs_out = []
        for seg in seg_list:
            text = seg.text.strip()
            if not text or any(p in text for p in HALLUCINATION_PHRASES):
                continue
            if file_start:
                actual = file_start + timedelta(seconds=seg.start)
                ts = actual.strftime("%H:%M:%S")
            else:
                actual = None
                ts = datetime.now().strftime("%H:%M:%S")
            label = f" {self_label}" if self_label else ""
            segs_out.append((actual, f"[{ts}]{label} {text}"))
        return segs_out

    # ── Segment buffer: collects (datetime, line) pairs across all sources ──────
    # Segments are flushed in absolute-time order so loopback and mic interleave.
    _seg_buf: list[tuple] = []   # (datetime | None, str)

    def _buf_flush_up_to(cutoff_dt, out_file):
        """Write all buffered segments with dt < cutoff_dt, sorted by time."""
        ready    = [(d, l) for d, l in _seg_buf if d is None or d < cutoff_dt]
        deferred = [(d, l) for d, l in _seg_buf if d is not None and d >= cutoff_dt]
        ready.sort(key=lambda x: x[0] if x[0] else datetime.min)
        for _, line in ready:
            out_file.write(line + "\n")
        if ready:
            out_file.flush()
        _seg_buf.clear()
        _seg_buf.extend(deferred)

    def _buf_flush_all(out_file):
        """Write all remaining buffered segments in time order."""
        _seg_buf.sort(key=lambda x: x[0] if x[0] else datetime.min)
        for _, line in _seg_buf:
            out_file.write(line + "\n")
        if _seg_buf:
            out_file.flush()
        _seg_buf.clear()

    def _write_and_move(wav_path, segs_or_text, out, source="loopback"):
        """Buffer segment lines (with timestamps) and move file to done/."""
        if isinstance(segs_or_text, str):
            # Plain string from detection cache — no datetime, write via buf
            if segs_or_text:
                _seg_buf.append((None, segs_or_text))
        else:
            for dt, line in segs_or_text:
                _seg_buf.append((dt, line))

        dest_dir = os.path.join(mic_dir, "done") if source == "mic" else done_dir
        os.rename(wav_path, os.path.join(dest_dir, os.path.basename(wav_path)))
        print(f"[Transcriber] done ({source}): {os.path.basename(wav_path)}")

    def _process_with_autodetect(wav_path):
        text, info = _transcribe(wav_path, lang=None)
        raw_lang   = LANG_ALIAS.get(info.language, info.language)
        prob       = info.language_probability

        # 無音チャンク: テキストが空 → 待機継続（EVAL_CHUNKS にカウントしない）
        if not text.strip():
            print(f"[Transcriber] 無音チャンク → 会議開始を待機中...")
            os.rename(wav_path, os.path.join(done_dir, os.path.basename(wav_path)))
            return

        valid = raw_lang in SUPPORTED_LANGS and prob >= MIN_CONFIDENCE
        print(
            f"[Transcriber] チャンク {len(pending)+1}: "
            f"検出={info.language} ({prob:.0%})"
            + (f" → 票: {raw_lang}" if valid else " → 低信頼・スキップ")
        )
        if valid:
            lang_scores[raw_lang] = lang_scores.get(raw_lang, 0) + prob
        pending.append((wav_path, text))

    def _decide_language():
        if lang_scores:
            lang   = max(lang_scores, key=lambda k: lang_scores[k])
            detail = " | ".join(f"{l}:{s:.2f}" for l, s in sorted(lang_scores.items(), key=lambda x: -x[1]))
            print(f"[Transcriber] 言語確定: {lang}  (スコア: {detail})\n")
        else:
            # 発話はあるが信頼度不足 → OS デフォルト言語を使用
            lang = _OS_DEFAULT_LANG
            print(f"[Transcriber] 信頼度不足 → OS デフォルト言語: {lang}\n")
        return lang

    def _flush_all(out):
        nonlocal language, self_label

        print(f"[Transcriber] 録音ファイルの書き込み完了を {DRAIN_WAIT_SEC}秒待機...")
        time.sleep(DRAIN_WAIT_SEC)

        # Collect late loopback + mic files
        late_loopback = [(f, "loopback") for f in sorted(glob.glob(os.path.join(audio_dir, "audio_*.wav")))
                         if f not in seen and os.path.getmtime(f) >= _start_cutoff] if use_loopback else []
        late_mic      = [(f, "mic")      for f in sorted(glob.glob(os.path.join(mic_dir, "mic_*.wav")))
                         if f not in seen and os.path.getmtime(f) >= _start_cutoff] if mic_dir else []
        late_files    = sorted(late_loopback + late_mic, key=lambda x: os.path.basename(x[0]).split("_", 1)[-1])

        total = len(late_files) + len(pending)
        if total == 0:
            print("[Transcriber] No remaining files.")
            return

        print(f"[Transcriber] Processing {total} remaining files...")

        for i, (wav_path, source) in enumerate(late_files, 1):
            seen.add(wav_path)
            print(f"[Transcriber] {i}/{total}: {os.path.basename(wav_path)} ({source})")
            if language is not None:
                lbl   = self_label if source == "mic" else ""
                lines = _transcribe_lines(wav_path, language, lbl)
                _write_and_move(wav_path, lines, out, source)
            else:
                if source == "loopback":
                    _process_with_autodetect(wav_path)

        if pending:
            if language is None:
                language   = _decide_language()
                self_label = _SELF_LABEL.get(language, "[Me]")
            offset = len(late_files)
            print(f"[Transcriber] Flushing {len(pending)} buffered chunks ({language})...")
            for j, item in enumerate(pending, 1):
                wav_path = item[0]
                source   = item[2] if len(item) > 2 else "loopback"
                print(f"[Transcriber] {offset+j}/{total}: {os.path.basename(wav_path)} ({source})")
                lbl   = self_label if source == "mic" else ""
                lines = _transcribe_lines(wav_path, language, lbl)
                _write_and_move(wav_path, lines, out, source)
            pending.clear()

        print(f"[Transcriber] All {total} files processed.")
        # Flush all buffered segments in absolute-time order
        _buf_flush_all(out)

    # Output file is created lazily when the first audio chunk arrives,
    # so the timestamp reflects actual recording start (not UI startup).
    class _LazyOut:
        def __init__(self):
            self._f   = None
            self.path = None

        def _open(self):
            if self._f is None:
                ts         = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.path  = os.path.join(transcript_dir, f"transcript_{ts}.txt")
                self._f    = open(self.path, "w", encoding="utf-8-sig")
                print(f"[Transcriber] ready -> {self.path}")
            return self._f

        def write(self, s):   return self._open().write(s)
        def flush(self):
            if self._f: self._f.flush()
        def seek(self, p):    return self._open().seek(p)
        def close(self):
            if self._f: self._f.close()

    out = _LazyOut()
    start_ts       = None   # set on first open
    header_written = False
    self_label     = ""

    def _ensure_header():
        nonlocal start_ts, header_written
        if not header_written and language is not None:
            if start_ts is None:
                start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            out.write(_header(language, start_ts) + "\n\n")
            out.flush()
            header_written = True

    # Read recording start cutoff — files older than this are skipped
    _start_cutoff = 0.0
    try:
        with open(START_FILE) as _sf:
            _start_cutoff = float(_sf.read().strip())
    except Exception:
        pass

    try:
        while True:
            # Re-read start cutoff each cycle (written by UI when recording starts)
            try:
                with open(START_FILE) as _sf:
                    _start_cutoff = float(_sf.read().strip())
            except Exception:
                pass

            loopback_new = []
            if use_loopback:
                loopback_new = [
                    (f, "loopback") for f in sorted(glob.glob(os.path.join(audio_dir, "audio_*.wav")))
                    if f not in seen and os.path.getmtime(f) >= _start_cutoff
                ]
            mic_new = []
            if mic_dir:
                mic_new = [
                    (f, "mic") for f in sorted(glob.glob(os.path.join(mic_dir, "mic_*.wav")))
                    if f not in seen and os.path.getmtime(f) >= _start_cutoff
                ]
            all_new = sorted(loopback_new + mic_new, key=lambda x: os.path.basename(x[0]).split("_", 1)[-1])

            for wav_path, source in all_new:
                seen.add(wav_path)
                if start_ts is None:
                    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                fname = os.path.basename(wav_path)
                print(f"[Transcriber] 認識開始: {fname} ({source})", flush=True)

                if language is None:
                    if source == "loopback":
                        _process_with_autodetect(wav_path)
                        if len(pending) >= EVAL_CHUNKS:
                            language   = _decide_language()
                            self_label = _SELF_LABEL.get(language, "[Me]")
                            print(f"[Transcriber] 言語確定: {language}  (以降この言語で認識)", flush=True)
                            _ensure_header()
                            for item in pending:
                                wp  = item[0]; tx = item[1]
                                src = item[2] if len(item) > 2 else "loopback"
                                print(f"[Transcriber] 認識開始: {os.path.basename(wp)} ({src}/pending)", flush=True)
                                lbl   = self_label if src == "mic" else ""
                                lines = _transcribe_lines(wp, language, lbl) if language in _LANG_PROMPT_BASE else ([(None, tx)] if tx else [])
                                _write_and_move(wp, lines, out, src)
                            pending.clear()
                    else:
                        pending.append((wav_path, "", source))
                else:
                    _ensure_header()
                    lbl   = self_label if source == "mic" else ""
                    lines = _transcribe_lines(wav_path, language, lbl)
                    _write_and_move(wav_path, lines, out, source)

            if os.path.exists(STOP_SIGNAL):
                os.remove(STOP_SIGNAL)
                raise KeyboardInterrupt

            # Flush segments older than record_sec+5s — by then all overlapping
            # files (loopback + mic) for that time window have been transcribed,
            # so we can write them in absolute-time order.
            if all_new and out.path:
                cutoff = datetime.now() - timedelta(seconds=record_sec + 5)
                _buf_flush_up_to(cutoff, out)

            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        print("\n[Transcriber] 停止します...")
        if out.path:  # only flush/write if we ever opened a file
            _flush_all(out)
            # Flush any segments still in buffer (sorted by absolute time)
            _buf_flush_all(out)
            if not header_written:
                if start_ts is None:
                    start_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                out.seek(0)
                out.write(_header(language, start_ts) + "\n\n")
            out.write("\n" + _footer(language, datetime.now().strftime("%Y-%m-%d %H:%M:%S")) + "\n")
    finally:
        out.close()

    # トランスクリプトパスと検出言語をステートファイルに書く
    if out.path:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(out.path)
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            f.write(language if language else "")

    # 自動検出だった場合、次回のために config.ini を更新する
    if cfg_lang not in SUPPORTED_LANGS and language:
        try:
            _cfg.set("recording", "language", language)
            with open(cfg_file, "w", encoding="utf-8") as f:
                _cfg.write(f)
            print(f"[Transcriber] config.ini を更新しました: language = {language}")
        except Exception as e:
            print(f"[Transcriber] config.ini 更新失敗（無視）: {e}")

    if out.path:
        print(f"[Transcriber] 保存しました: {out.path}")


if __name__ == "__main__":
    main()

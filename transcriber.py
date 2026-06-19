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
        sys_info(f"CUDA compat alias: {alias} -> {os.path.basename(src)}")
    except Exception as e:
        sys_error(f"Could not create CUDA alias {alias}: {e}")

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
from log_util import tr_debug, tr_info, tr_warn, tr_error, sys_info, sys_error

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

# シグナルファイル（presenter / recorder が書き込み、transcriber が読み取る）
_BASE = os.path.dirname(os.path.abspath(__file__))
STOP_SIGNAL    = os.path.join(_BASE, ".stop_signal")
START_FILE     = os.path.join(_BASE, ".recording_start")
STATE_FILE     = os.path.join(_BASE, ".last_transcript")
LANG_FILE      = os.path.join(_BASE, ".last_language")
DRAIN_WAIT_SEC = 3

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

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_VOCAB_DEFAULT = os.path.join(_BASE_DIR, "vocabulary.txt")

def _resolve_vocab_file(cfg: configparser.ConfigParser) -> str:
    """config.ini の vocab_file パスを取得し、初回は program dir からコピーする。"""
    path = cfg.get("paths", "vocab_file", fallback="").strip()
    if not path:
        return _VOCAB_DEFAULT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) and os.path.exists(_VOCAB_DEFAULT):
        import shutil
        shutil.copy2(_VOCAB_DEFAULT, path)
        sys_info(f"vocabulary.txt をコピー: {path}")
    return path

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


def load_vocabulary(vocab_file: str = "") -> list[str]:
    """vocabulary.txt から有効な用語を読み込む。"""
    path = vocab_file or _VOCAB_DEFAULT
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as f:
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
            sys_info(f"config.ini updated: device={device}, model_size={model_size}")
    except Exception as e:
        sys_warn(f"config.ini update skipped: {e}")


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

    sys_info(f"mode={rec_mode}  loopback={'on' if use_loopback else 'off'}  mic={'on' if mic_dir else 'off'}")

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

    # Speed knobs (config-overridable). On CPU a greedy search (beam_size=1) is
    # ~2–3× faster than the default beam_size=5 with only a small accuracy cost,
    # which is the single biggest win for medium-on-CPU. cpu_threads=0 lets
    # CTranslate2 auto-pick (sensible on hybrid P/E-core laptop CPUs).
    beam_size   = _cfg.getint("recording", "beam_size",   fallback=(5 if device == "cuda" else 1))
    cpu_threads = _cfg.getint("recording", "cpu_threads", fallback=0)

    tr_info(f"loading faster-whisper {model_size} (device={device}, compute={compute_type}, "
            f"beam={beam_size}, cpu_threads={cpu_threads or 'auto'})")
    whisper = WhisperModel(model_size, device=device, compute_type=compute_type,
                           cpu_threads=cpu_threads)
    tr_info(f"model loaded on {device}")

    # Warm up the model: the first real transcribe pays CTranslate2's lazy
    # init (kernel/graph setup, weight prefetch) which can add several seconds.
    # Run one throwaway pass on a short silent buffer so the first actual chunk
    # transcribes at full speed → less time to first text.
    try:
        import numpy as _np
        _silence = _np.zeros(16000, dtype=_np.float32)  # 1s @ 16kHz
        _segs, _ = whisper.transcribe(_silence, beam_size=beam_size,
                                      condition_on_previous_text=False)
        list(_segs)  # force evaluation
        tr_info("model warmup done")
    except Exception as e:
        tr_debug(f"model warmup skipped: {e}")
    tr_info("ready (output file will be created on first audio)")
    if language:
        tr_info(f"言語固定: {language}（config.ini）")
    else:
        tr_info(f"言語自動検出（{EVAL_CHUNKS}チャンク待機）")
    print("[Transcriber] 新規音声ファイルを待機中... (Ctrl+C で停止)\n")

    pending     = []
    lang_scores = {}
    seen        = set()
    self_label  = ""  # resolved after language detection

    vocab_file = _resolve_vocab_file(_cfg)
    vocab = load_vocabulary(vocab_file)
    if vocab:
        tr_info(f"用語リスト: {len(vocab)} 件 ({vocab_file})")

    def _transcribe(wav_path, lang):
        """For language detection: returns (full_text, info)."""
        prompt = build_initial_prompt(lang, vocab)
        try:
            segments, info = whisper.transcribe(
                wav_path, language=lang,
                initial_prompt=prompt,
                beam_size=beam_size,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            text = " ".join(s.text.strip() for s in segments)
        except RuntimeError as e:
            tr_error(f"transcribe error: {e}")
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
                beam_size=beam_size,
                condition_on_previous_text=False,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            seg_list = list(segments)  # force evaluation here to catch GPU errors
        except RuntimeError as e:
            err_msg = str(e)
            if "cublas" in err_msg.lower() or "cuda" in err_msg.lower():
                # GPU 推論失敗 → CPU に永続フォールバック（以降は再試行しない）
                tr_error(f"GPU inference error: {e}")
                print("[Transcriber] Reloading model on CPU (permanent fallback)...")
                device       = "cpu"
                compute_type = "int8"
                whisper      = WhisperModel(model_size, device="cpu", compute_type="int8")
                print("[Transcriber] Reloaded on CPU. Retrying...")
                segments, _ = whisper.transcribe(
                    wav_path, language=lang, initial_prompt=prompt,
                    beam_size=beam_size, condition_on_previous_text=False,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
                )
                seg_list = list(segments)
            else:
                tr_error(f"transcribe error: {e}")
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
        tr_debug(f"done→done/ ({source}): {os.path.basename(wav_path)}")

    def _process_with_autodetect(wav_path):
        text, info = _transcribe(wav_path, lang=None)
        raw_lang   = LANG_ALIAS.get(info.language, info.language)
        prob       = info.language_probability

        # 無音チャンク: テキストが空 → 待機継続（EVAL_CHUNKS にカウントしない）
        if not text.strip():
            tr_debug("無音チャンク → 会議開始を待機中...")
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
            tr_info(f"言語確定: {lang}  (スコア: {detail})")
        else:
            # 発話はあるが信頼度不足 → OS デフォルト言語を使用
            lang = _OS_DEFAULT_LANG
            tr_warn(f"信頼度不足 → OS デフォルト言語: {lang}")
        return lang

    def _flush_all(out):
        nonlocal language, self_label

        tr_info(f"停止: 録音ファイル書き込み完了を {DRAIN_WAIT_SEC}秒待機...")
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

        tr_info(f"停止後処理: 残り {total} 件を変換してから終了...")

        for i, (wav_path, source) in enumerate(late_files, 1):
            seen.add(wav_path)
            tr_info(f"flush {i}/{total}: {os.path.basename(wav_path)} ({source})")
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
            tr_info(f"pending {len(pending)} チャンクを {language} で変換中...")
            for j, item in enumerate(pending, 1):
                wav_path = item[0]
                source   = item[2] if len(item) > 2 else "loopback"
                tr_info(f"flush {offset+j}/{total}: {os.path.basename(wav_path)} ({source})")
                lbl   = self_label if source == "mic" else ""
                lines = _transcribe_lines(wav_path, language, lbl)
                _write_and_move(wav_path, lines, out, source)
            pending.clear()

        tr_info(f"全 {total} 件の変換完了")
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
                tr_info(f"transcript file created: {self.path}")
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
                tr_info(f"認識開始: {fname} ({source})")

                if language is None:
                    if source == "loopback":
                        _process_with_autodetect(wav_path)
                        if len(pending) >= EVAL_CHUNKS:
                            language   = _decide_language()
                            self_label = _SELF_LABEL.get(language, "[Me]")
                            tr_info(f"言語確定: {language}  (以降この言語で認識)")
                            _ensure_header()
                            for item in pending:
                                wp  = item[0]; tx = item[1]
                                src = item[2] if len(item) > 2 else "loopback"
                                tr_debug(f"pending再変換: {os.path.basename(wp)} ({src})")
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
            sys_info(f"config.ini 更新: language = {language}")
        except Exception as e:
            sys_warn(f"config.ini 更新失敗（無視）: {e}")

    if out.path:
        tr_info(f"保存しました: {out.path}")


def main_single(wav_path: str, output_file: str, language: str | None, source: str = "loopback") -> None:
    """
    単一ファイルモード: 1ファイルを転写して output_file に追記し done/ に移動して終了。
    presenter が 1ファイルごとに本プロセスを起動する設計に対応。
    """
    _cfg = configparser.ConfigParser()
    _cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")

    audio_dir      = _cfg.get("paths", "audio_dir")
    mic_dir        = _cfg.get("paths", "mic_dir", fallback="")
    model_size     = _cfg.get("recording", "model_size", fallback="small").strip()
    cfg_device     = _cfg.get("recording", "device", fallback="auto").strip().lower()

    if cfg_device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    else:
        device = cfg_device

    compute_type = "float16" if device == "cuda" else "int8"

    vocab_file = _resolve_vocab_file(_cfg)
    vocab      = load_vocabulary(vocab_file)

    tr_info(f"[single] loading {model_size} ({device})")
    whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
    tr_info(f"[single] model ready, processing: {os.path.basename(wav_path)}")

    # 言語が未確定の場合は自動検出
    if not language:
        prompt = build_initial_prompt(None, vocab)
        try:
            segs, info = whisper_model.transcribe(
                wav_path, language=None, initial_prompt=prompt,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
            )
            list(segs)  # consume to get info
            raw = LANG_ALIAS.get(info.language, info.language)
            if raw in SUPPORTED_LANGS and info.language_probability >= MIN_CONFIDENCE:
                language = raw
                tr_info(f"[single] 言語検出: {language} ({info.language_probability:.0%})")
                with open(LANG_FILE, "w", encoding="utf-8") as f:
                    f.write(language)
            else:
                language = _OS_DEFAULT_LANG
                tr_warn(f"[single] 低信頼 → OS言語: {language}")
        except Exception as e:
            language = _OS_DEFAULT_LANG
            tr_error(f"[single] 言語検出失敗: {e}")

    # 転写
    prompt     = build_initial_prompt(language, vocab)
    file_start = _parse_file_start_time(wav_path)
    self_label = _SELF_LABEL.get(language, "") if source == "mic" else ""
    try:
        segs, _ = whisper_model.transcribe(
            wav_path, language=language, initial_prompt=prompt,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        )
        seg_list = list(segs)
    except Exception as e:
        tr_error(f"[single] transcribe error: {e}")
        seg_list = []

    lines = []
    for seg in seg_list:
        text = seg.text.strip()
        if not text or any(p in text for p in HALLUCINATION_PHRASES):
            continue
        if file_start:
            ts = (file_start + timedelta(seconds=seg.start)).strftime("%H:%M:%S")
        else:
            ts = datetime.now().strftime("%H:%M:%S")
        label = f" {self_label}" if self_label else ""
        lines.append(f"[{ts}]{label} {text}")

    # 転写結果を追記
    if lines:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "a", encoding="utf-8-sig") as f:
            for line in lines:
                f.write(line + "\n")
        tr_info(f"[single] {len(lines)}行追記: {os.path.basename(output_file)}")
    else:
        tr_debug(f"[single] 無音またはフィルタで除外: {os.path.basename(wav_path)}")

    # done/ に移動
    done_dir = os.path.join(mic_dir if source == "mic" else audio_dir, "done")
    os.makedirs(done_dir, exist_ok=True)
    try:
        os.rename(wav_path, os.path.join(done_dir, os.path.basename(wav_path)))
        tr_debug(f"[single] done→done/ ({source}): {os.path.basename(wav_path)}")
    except Exception as e:
        tr_error(f"[single] done移動失敗: {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",   help="単一ファイルモード: 転写する WAV ファイルパス")
    parser.add_argument("--output", help="転写結果の出力ファイルパス（追記モード）")
    parser.add_argument("--lang",   help="言語コード (zh/ja/en)。省略時は自動検出")
    parser.add_argument("--source", default="loopback", help="loopback or mic")
    args = parser.parse_args()

    if args.file:
        if not args.output:
            print("ERROR: --file には --output も必要です", flush=True)
            sys.exit(1)
        main_single(args.file, args.output, args.lang or None, args.source)
    else:
        main()

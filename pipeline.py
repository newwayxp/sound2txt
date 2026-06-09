"""
Audio pipeline: capture → VAD (silence detection) → async Whisper transcription

Flow:
  1. Main loop captures audio, feeds to VAD
  2. When VAD detects end-of-speech (silence gap), the segment is queued
  3. Background thread transcribes segments in order and appends to transcript
  4. Session audio is saved continuously as WAV/MP3 (separate from transcription)

Signal files:
  .pipeline_session   : save audio + write transcript (written by start btn)
  .pipeline_stop      : stop the pipeline process
  .pipeline_session_done : written when session audio + transcripts are complete
"""
import os
import sys
import time
import wave
import tempfile
import queue
import collections
import configparser
import threading
import warnings
import numpy as np

warnings.filterwarnings("ignore")

_BASE = os.path.dirname(os.path.abspath(__file__))

# ── network / proxy setup ─────────────────────────────────────────────────────
_pre = configparser.ConfigParser()
_pre.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")
if _pre.has_section("network"):
    _px = _pre.get("network", "https_proxy", fallback="")
    if _px:
        os.environ.setdefault("HTTPS_PROXY", _px)
        os.environ.setdefault("HTTP_PROXY",  _px)
    if not _pre.getboolean("network", "ssl_verify", fallback=True):
        os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")
        os.environ.setdefault("CURL_CA_BUNDLE", "")
        os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "error")

from log_util import tr_info, tr_debug, tr_warn, tr_error, sys_info, sys_error
from glossary import resolve_glossary_file, load_glossary, apply_glossary

# ── signal files ──────────────────────────────────────────────────────────────
SIGNAL_SESSION   = os.path.join(_BASE, ".pipeline_session")
SIGNAL_STOP      = os.path.join(_BASE, ".pipeline_stop")
SIGNAL_SESS_DONE = os.path.join(_BASE, ".pipeline_session_done")
MIC_ONAIR        = os.path.join(_BASE, ".mic_onair")
LANG_FILE          = os.path.join(_BASE, ".last_language")
STATE_FILE         = os.path.join(_BASE, ".last_transcript")
CORRECTED_STATE    = os.path.join(_BASE, ".last_corrected")

SAMPLE_RATE = 16000
CHUNK_SIZE  = 1024

HALLUCINATION = [
    # YouTube / streaming hallucinations
    "ご視聴ありがとうございました", "チャンネル登録",
    "thank you for watching", "please subscribe",
    "请不吝点赞", "订阅", "感谢观看", "字幕由",
    # Initial prompt leakage
    "日本語の会議録音", "普通话录音的简体中文",
    "Meeting transcript",
]


# ── VAD (silence-based speech segment detection) ─────────────────────────────
class AccumulatingVAD:
    """
    Detects end-of-speech by observing silence after speech.

    Sends a segment when:
      A. silence_sec of silence follows at least min_accum_sec of speech
      B. total accumulated duration exceeds max_sec (force flush)
    """
    def __init__(self, threshold: float = 0.5,
                 silence_sec: float = 2.0,
                 min_accum_sec: float = 1.0,
                 max_sec: float = 30.0):
        self._threshold    = threshold
        self._turn_silence = silence_sec
        self._min_sec      = min_accum_sec
        self._max_sec      = max_sec

        self._accum: list[np.ndarray] = []
        self._accum_dur   = 0.0
        self._speech_dur  = 0.0
        self._silence_dur = 0.0
        self._speaking    = False

        self._model = None
        try:
            from faster_whisper.vad import SileroVADModel
            self._model = SileroVADModel()
            sys_info(f"VAD: Silero (silence={silence_sec}s min={min_accum_sec}s max={max_sec}s)")
        except Exception:
            sys_info(f"VAD: amplitude (silence={silence_sec}s min={min_accum_sec}s max={max_sec}s)")

    def _is_speech(self, chunk: np.ndarray) -> bool:
        if self._model:
            try:
                prob = self._model(chunk.astype(np.float32) / 32768.0, SAMPLE_RATE)
                return float(prob) >= self._threshold
            except Exception:
                pass
        return int(np.abs(chunk).mean()) >= 300

    def feed(self, chunk: np.ndarray) -> bytes | None:
        is_speech = self._is_speech(chunk)
        chunk_dur = len(chunk) / SAMPLE_RATE

        self._accum.append(chunk)
        self._accum_dur += chunk_dur

        if is_speech:
            if not self._speaking:
                tr_debug(f"VAD speech   accum={self._accum_dur:.1f}s")
            self._speaking     = True
            self._speech_dur  += chunk_dur
            self._silence_dur  = 0.0
        else:
            if self._speaking:
                self._silence_dur += chunk_dur
                if self._silence_dur >= self._turn_silence:
                    if self._speech_dur >= self._min_sec:
                        tr_info(f"VAD turn-end: speech={self._speech_dur:.1f}s "
                                f"in {self._accum_dur:.1f}s window → sending to transcribe")
                        return self._flush("turn")
                    else:
                        # Short noise - reset speech tracking but keep accumulated buffer
                        # so force-flush can still trigger at max_sec
                        tr_debug(f"VAD noise-skip speech={self._speech_dur:.1f}s "
                                 f"accum={self._accum_dur:.1f}s kept")
                        self._speaking    = False
                        self._speech_dur  = 0.0
                        self._silence_dur = 0.0

        if self._accum_dur >= self._max_sec:
            tr_info(f"VAD force-flush: {self._accum_dur:.1f}s accumulated "
                    f"(speech={self._speech_dur:.1f}s) → sending to transcribe")
            return self._flush("force")

        return None

    def _reset(self):
        self._accum.clear()
        self._accum_dur  = 0.0
        self._speech_dur = 0.0
        self._silence_dur = 0.0
        self._speaking   = False

    def _flush(self, reason: str = "") -> bytes:
        seg = np.concatenate(self._accum).tobytes()
        dur = len(seg) / 2 / SAMPLE_RATE
        tr_debug(f"VAD flushed [{reason}] speech={self._speech_dur:.1f}s total={dur:.1f}s")
        self._reset()
        return seg

    def force_flush(self) -> bytes | None:
        if self._accum and self._speech_dur >= self._min_sec:
            return self._flush("stop")
        self._reset()
        return None


def _is_echo_segment(mic_pcm: bytes,
                     buf_snapshot: list,
                     threshold: float) -> bool:
    """
    Returns True if mic segment is likely an echo of recent system audio.
    Uses normalised cross-correlation between mic PCM and buffered system audio.
    Both signals are int16 at SAMPLE_RATE (16 kHz).
    """
    if not buf_snapshot:
        return False
    from scipy.signal import correlate as _correlate
    mic_arr = np.frombuffer(mic_pcm, dtype=np.int16).astype(np.float32)
    ref_arr = np.concatenate(buf_snapshot).astype(np.float32)
    if len(mic_arr) < 32 or len(ref_arr) < len(mic_arr):
        return False
    ref_rms = float(np.sqrt(np.mean(ref_arr ** 2)))
    if ref_rms < 200.0:  # system audio essentially silent → no echo possible
        return False
    mic_n = mic_arr / (float(np.abs(mic_arr).max()) + 1e-7)
    ref_n = ref_arr / (float(np.abs(ref_arr).max()) + 1e-7)
    corr  = _correlate(ref_n, mic_n, mode="valid")
    max_c = float(np.abs(corr).max()) / len(mic_n)
    tr_debug(f"AEC corr={max_c:.3f} thresh={threshold}")
    return max_c > threshold


def _mic_label(lang: str | None) -> str:
    """Return source prefix for mic-origin transcription lines."""
    if lang == "ja":
        return "【自分】"
    if lang == "zh":
        return "【自己】"
    return "[Me] "


# ── per-segment LLM correction ───────────────────────────────────────────────
def _correct_segment(text: str, lang: str | None,
                     cfg: configparser.ConfigParser) -> str:
    """Call LLM to correct one transcribed segment. Falls back to original."""
    if not cfg.getboolean("summary", "enable_correction", fallback=True):
        return text
    mode = cfg.get("summary", "mode", fallback="openai").strip().lower()

    if mode == "ollama":
        base_url = cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
        model    = cfg.get("summary", "ollama_model", fallback="qwen2.5:7b").strip()
        url      = base_url.rstrip("/") + "/v1/chat/completions"
        api_key  = "ollama"
    else:
        base_url = cfg.get("summary", "api_base", fallback="").strip()
        api_key  = cfg.get("summary", "api_key",  fallback="").strip()
        model    = cfg.get("summary", "model",    fallback="").strip()
        url      = base_url.rstrip("/") + "/chat/completions" if base_url else ""

    if not url or not api_key or not model:
        return text

    _correction_prompts = {
        "ja": f"会議音声の自動転写テキストの誤認識・句読点を修正してください。説明不要。修正後のテキストだけを出力。\n\n{text}",
        "zh": f"请修正以下会议语音自动转录文字中的识别错误和标点符号，只输出修正后的文字，不要解释。\n\n{text}",
        "en": f"Fix ASR recognition errors and punctuation in this meeting transcript. Output only the corrected text.\n\n{text}",
    }
    prompt = _correction_prompts.get(lang or "", _correction_prompts["en"])

    verify  = cfg.getboolean("network", "ssl_verify", fallback=True)
    px      = cfg.get("network", "https_proxy", fallback="")
    proxies = {"https": px, "http": px} if px else None

    import requests
    try:
        r = requests.post(
            url,
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": max(200, len(text) * 3),
                  "temperature": 0.2},
            headers={"Authorization": f"Bearer {api_key}"},
            proxies=proxies, verify=verify, timeout=20,
        )
        r.raise_for_status()
        result = r.json()["choices"][0]["message"]["content"].strip()
        # Strip common LLM preamble patterns (e.g. "以下が修正後のテキストです：\n")
        import re as _re
        result = _re.sub(
            r"^(以下[がはの].{0,20}[：:]\s*|修正後[のは].{0,15}[：:]\s*|"
            r"Here is.{0,30}:\s*|Corrected.{0,20}:\s*)", "",
            result, flags=_re.IGNORECASE
        ).strip()
        return result if result else text
    except Exception as e:
        tr_debug(f"correction API error: {e}")
        return text


# ── model-specific transcription parameters ───────────────────────────────────
def _make_transcribe_kwargs(model_path: str) -> dict:
    """Return transcribe() kwargs appropriate for the given model."""
    import json as _json

    kwargs: dict = dict(
        beam_size                  = 5,
        temperature                = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        vad_filter                 = True,
        vad_parameters             = {"min_silence_duration_ms": 400, "threshold": 0.4},
        condition_on_previous_text = False,
        word_timestamps            = False,
        no_speech_threshold        = 0.7,
        log_prob_threshold         = -1.0,
    )

    if not os.path.isdir(model_path):
        return kwargs

    preproc = os.path.join(model_path, "preprocessor_config.json")
    if not os.path.exists(preproc):
        return kwargs

    try:
        cfg = _json.load(open(preproc, encoding="utf-8"))
    except Exception:
        return kwargs

    if cfg.get("feature_size", 80) == 128:
        # Whisper Large-v3 based (large-v3)
        kwargs["vad_parameters"] = {"min_silence_duration_ms": 500, "threshold": 0.35}

    return kwargs


# ── main pipeline ─────────────────────────────────────────────────────────────
def run():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")

    # ── Pre-load CUDA DLLs from nvidia pip packages BEFORE importing ctranslate2
    if sys.platform == "win32":
        try:
            import ctypes as _ct, site as _site
            _site_dirs = _site.getsitepackages()
            try:
                _site_dirs = _site_dirs + [_site.getusersitepackages()]
            except Exception:
                pass
            for _site_dir in _site_dirs:
                for _sub, _dll in [
                    ("nvidia/cuda_runtime/bin", "cudart64_12.dll"),
                    ("nvidia/cublas/bin",       "cublas64_12.dll"),
                    ("nvidia/cublas/bin",       "cublasLt64_12.dll"),
                ]:
                    _dll_path = os.path.join(_site_dir, _sub.replace("/", os.sep), _dll)
                    if os.path.exists(_dll_path):
                        try:
                            _ct.CDLL(_dll_path)
                            sys_info(f"CUDA pre-loaded: {_dll}")
                        except OSError as _e:
                            sys_info(f"CUDA pre-load failed ({_dll}): {_e}")
        except Exception:
            pass

    # ── Import faster-whisper ─────────────────────────────────────────────────
    try:
        from faster_whisper import WhisperModel
    except (OSError, ImportError) as e:
        sys_error(f"faster-whisper load failed: {e}")
        setup_hint = "setup.bat" if sys.platform == "win32" else "setup_mac.sh"
        sys_error(f"Run {setup_hint} to repair the installation")
        return

    # ── Device detection ──────────────────────────────────────────────────────
    device_cfg = cfg.get("recording", "device", fallback="auto").strip().lower()
    if device_cfg == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    else:
        device = device_cfg
    compute_type = "float16" if device == "cuda" else "int8"

    model_size = cfg.get("recording", "model_size", fallback="small").strip()

    # ── Session language ──────────────────────────────────────────────────────
    cfg_lang = cfg.get("recording", "language", fallback="auto").strip().lower()
    session_lang: str | None = cfg_lang if cfg_lang in {"zh", "ja", "en"} else None
    if not session_lang and os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, encoding="utf-8") as f:
                l = f.read().strip()
            if l in {"zh", "ja", "en"}:
                session_lang = l
                tr_info(f"Using previous language: {session_lang}")
        except Exception:
            pass

    # ── Language → model mapping ──────────────────────────────────────────────
    _LANG_MODELS = {
        "ja": cfg.get("models", "ja", fallback=model_size),
        "zh": cfg.get("models", "zh", fallback=model_size),
        "en": cfg.get("models", "en", fallback=model_size),
    }
    _models_dir = os.path.join(_BASE, "models")

    def _resolve_model(lang: str | None) -> str:
        candidate = _LANG_MODELS.get(lang or "", model_size)
        local_path = os.path.join(_models_dir, candidate)
        if os.path.isdir(local_path) and os.path.exists(os.path.join(local_path, "model.bin")):
            sys_info(f"Using language model: {candidate} (lang={lang})")
            return local_path
        if candidate != model_size:
            sys_info(f"Language model not found ({candidate}), using {model_size}")
        return model_size

    # ── Load model ────────────────────────────────────────────────────────────
    model_path = _resolve_model(session_lang)
    sys_info(f"pipeline: model={model_path} device={device}")
    try:
        whisper = WhisperModel(model_path, device=device, compute_type=compute_type)
    except Exception as e:
        if device == "cuda":
            sys_info(f"CUDA unavailable ({e}), falling back to CPU")
            device = "cpu"
            compute_type = "int8"
            whisper = WhisperModel(model_path, device="cpu", compute_type="int8")
        else:
            sys_error(f"Failed to load model: {e}")
            return
    sys_info(f"model ready (device={device})")
    _current_model_lang = session_lang
    _transcribe_kwargs = _make_transcribe_kwargs(model_path)
    sys_info(f"transcribe: beam={_transcribe_kwargs['beam_size']} "
             f"vad={_transcribe_kwargs['vad_parameters']}")

    # ── Vocabulary ────────────────────────────────────────────────────────────
    vocab_file = os.path.expanduser(cfg.get("paths", "vocab_file", fallback="").strip())
    vocab: list[str] = []
    if vocab_file and os.path.exists(vocab_file):
        with open(vocab_file, encoding="utf-8") as f:
            vocab = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    # Whisper's initial_prompt is capped (~224 tokens); feeding the whole vocab
    # would overflow and dilute the bias. Use only the top-N highest-priority
    # terms (vocabulary.txt is ordered project-critical first). The full list
    # still feeds the LLM correction step.
    _ip_max = cfg.getint("recording", "initial_prompt_max_terms", fallback=64)

    def _initial_prompt(lang: str | None) -> str | None:
        # No base sentence — avoids Whisper hallucinating the prompt text.
        # language= parameter already tells Whisper which language to use.
        if vocab:
            sep = "、" if lang == "ja" else ", "
            return sep.join(vocab[:_ip_max])
        return None

    # ── Glossary (deterministic 誤→正 replacement; works even when LLM is down) ──
    _glossary = load_glossary(resolve_glossary_file(cfg))
    if _glossary:
        sys_info(f"glossary: {len(_glossary)} correction rule(s) loaded")

    # ── VAD ───────────────────────────────────────────────────────────────────
    silence_s = cfg.getfloat("subtitle", "silence_sec",   fallback=2.0)
    min_s     = cfg.getfloat("subtitle", "min_accum_sec", fallback=1.0)
    max_s     = cfg.getfloat("subtitle", "max_sec",       fallback=30.0)
    vad = AccumulatingVAD(silence_sec=silence_s, min_accum_sec=min_s, max_sec=max_s)

    # ── Mic state (shared across _open_session / _mic_run / _close_session) ──
    _mic_state: dict = {
        "segments":     [],   # list[tuple[float, bytes]] — (offset_sec, pcm_int16_bytes)
        "cur_buf":      [],   # list[np.ndarray] — current on-air audio accumulator
        "onair_offset": None, # float | None — session-relative offset in seconds when On Air started
    }
    _conv_threads: list[threading.Thread] = []  # non-daemon MP3 conversion threads to join
    # System audio sample counter — single-writer (main thread), read by mic thread via GIL.
    # Counts frames at original sample_rate since session start → used for precise mic offset.
    _sys_frames = [0]

    # ── AEC: system audio reference buffer (main thread writes, mic thread reads) ──
    _aec_buf:  collections.deque = collections.deque()  # list of np.ndarray chunks at SAMPLE_RATE
    _aec_lock: threading.Lock    = threading.Lock()
    _AEC_BUF_SEC  = 2.0
    _AEC_THRESH   = cfg.getfloat("recording", "aec_threshold", fallback=0.55)

    # ── Session state ─────────────────────────────────────────────────────────
    transcript_file:  str | None = None
    corrected_file:   str | None = None
    raw_file_path:    str | None = None
    raw_fh = None
    session_ts: str | None = None

    _default_base  = os.path.join(os.path.expanduser("~"), "Documents", "Sound2Text")
    transcript_dir = os.path.expanduser(cfg.get("paths", "transcript_dir",
                             fallback=os.path.join(_default_base, "transcript")))
    corrected_dir  = os.path.expanduser(cfg.get("summary", "corrected_dir",
                             fallback=os.path.join(_default_base, "corrected")))
    audio_dir = os.path.expanduser(cfg.get("paths", "audio_dir",
                        fallback=os.path.join(_default_base, "audio")))

    def _open_session():
        nonlocal transcript_file, corrected_file, raw_file_path, raw_fh, session_ts
        from datetime import datetime
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(transcript_dir, exist_ok=True)
        os.makedirs(corrected_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)
        transcript_file = os.path.join(transcript_dir, f"transcript_{session_ts}.txt")
        corrected_file  = os.path.join(corrected_dir,  f"corrected_{session_ts}.txt")
        raw_file_path   = os.path.join(audio_dir, f".tmp_audio_{session_ts}.raw")
        raw_fh = open(raw_file_path, "wb")
        with open(transcript_file, "w", encoding="utf-8-sig") as f:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"=== Session started {ts_str} ===\n\n")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(transcript_file)
        # Initialize corrected file and signal
        ts_str2 = ts_str  # already defined above via datetime
        with open(corrected_file, "w", encoding="utf-8-sig") as f:
            f.write(f"=== Corrected transcript started {ts_str2} ===\n\n")
        with open(CORRECTED_STATE, "w", encoding="utf-8") as f:
            f.write(corrected_file)
        _sys_frames[0] = 0          # reset sample counter at session start
        _mic_state["segments"].clear()
        _mic_state["cur_buf"].clear()
        _mic_state["onair_offset"] = None
        sys_info(f"Session started: {session_ts}")
        sys_info(f"Audio streaming to disk: {raw_file_path}")
        sys_info(f"Transcript: {transcript_file}")
        sys_info(f"Corrected:  {corrected_file}")

    def _close_session(channels: int, sample_size: int, sample_rate: int):
        nonlocal raw_fh, raw_file_path, transcript_file, corrected_file, session_ts
        if raw_fh:
            raw_fh.close()
            raw_fh = None
        if raw_file_path and os.path.exists(raw_file_path):
            wav_path = os.path.join(os.path.dirname(raw_file_path),
                                    f"audio_{session_ts}.wav")
            _snap_ts = session_ts  # capture for closure
            try:
                # RAW → WAV: fast, done synchronously to free disk space
                with open(raw_file_path, "rb") as f:
                    pcm = f.read()
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(sample_size)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm)
                os.remove(raw_file_path)
                dur = len(pcm) / (sample_rate * channels * sample_size)
                tr_info(f"WAV saved: {os.path.basename(wav_path)} ({dur:.1f}s) — "
                        f"raw={len(pcm)//1024}KB on disk")

                # WAV → MP3: slow for long files, run in background thread
                audio_fmt   = cfg.get("recording", "audio_format",  fallback="mp3").strip().lower()
                mp3_quality = cfg.get("recording", "mp3_quality",   fallback="2").strip()

                def _convert_mp3(wav_p: str, quality: str, mic_segs: list):
                    import subprocess as _sp
                    mp3_p = wav_p.replace(".wav", ".mp3")
                    tr_info(f"MP3 conversion started: {os.path.basename(wav_p)}"
                            + (f" (+{len(mic_segs)} mic segments)" if mic_segs else ""))

                    inputs    = ["-i", wav_p]
                    flt_parts = []
                    tmp_wavs  = []

                    for idx, (offset_sec, pcm) in enumerate(mic_segs):
                        try:
                            tmp_w = wav_p.replace(".wav", f"_mic{idx}.wav")
                            with wave.open(tmp_w, "wb") as _wf:
                                _wf.setnchannels(1)
                                _wf.setsampwidth(2)
                                _wf.setframerate(SAMPLE_RATE)
                                _wf.writeframes(pcm)
                            tmp_wavs.append(tmp_w)
                            inputs += ["-i", tmp_w]
                            d_ms = round(offset_sec * 1000)  # ms-precise
                            flt_parts.append(f"[{idx+1}]adelay={d_ms}[d{idx}]")
                        except Exception as _we:
                            tr_warn(f"Mic segment {idx} write error: {_we}")

                    if flt_parts:
                        n   = 1 + len(flt_parts)
                        ins = "[0]" + "".join(f"[d{i}]" for i in range(len(flt_parts)))
                        flt = (";".join(flt_parts) +
                               f";{ins}amix=inputs={n}:duration=first:"
                               f"dropout_transition=0:normalize=0")
                        cmd = ["ffmpeg"] + inputs + [
                            "-filter_complex", flt,
                            "-codec:a", "libmp3lame", "-qscale:a", quality, mp3_p, "-y",
                        ]
                    else:
                        cmd = ["ffmpeg", "-i", wav_p,
                               "-codec:a", "libmp3lame", "-qscale:a", quality, mp3_p, "-y"]

                    result = _sp.run(cmd, capture_output=True, text=True)

                    for tmp in tmp_wavs:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass

                    if result.returncode == 0:
                        mp3_size = os.path.getsize(mp3_p) // 1024
                        wav_size = os.path.getsize(wav_p) // 1024
                        os.remove(wav_p)
                        tr_info(f"MP3 saved: {os.path.basename(mp3_p)} "
                                f"({mp3_size}KB, {int(100-mp3_size/wav_size*100)}% smaller)")
                    else:
                        tr_warn(f"MP3 conversion failed, WAV kept: {result.stderr[-200:]}")

                if audio_fmt == "mp3":
                    mic_segs_snap = list(_mic_state["segments"])
                    tr_info(f"MP3 queued: {len(mic_segs_snap)} mic segment(s) to mix")
                    t = threading.Thread(target=_convert_mp3,
                                         args=(wav_path, mp3_quality, mic_segs_snap),
                                         daemon=False)
                    _conv_threads.append(t)
                    t.start()
                    tr_info("MP3 conversion running in background")

            except Exception as e:
                tr_error(f"Audio conversion failed: {e}")

        from datetime import datetime
        _end_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if transcript_file and os.path.exists(transcript_file):
            with open(transcript_file, "a", encoding="utf-8-sig") as f:
                f.write(f"\n=== Session ended {_end_ts} ===\n")
        if corrected_file and os.path.exists(corrected_file):
            with open(corrected_file, "a", encoding="utf-8-sig") as f:
                f.write(f"\n=== Corrected transcript ended {_end_ts} ===\n")

        if session_lang:
            with open(LANG_FILE, "w", encoding="utf-8") as f:
                f.write(session_lang)

        raw_file_path   = None
        transcript_file = None
        corrected_file  = None
        session_ts      = None

        with open(SIGNAL_SESS_DONE, "w") as f:
            f.write("done")
        sys_info("Session complete signal written")

    # ── Async transcription thread ────────────────────────────────────────────
    _seg_queue: queue.Queue = queue.Queue()

    _lang_detect_attempts = [0]   # segments tried while session_lang is still None

    def _transcribe_loop():
        """Background thread: transcribes audio segments in order."""
        nonlocal session_lang, whisper, _current_model_lang, device, compute_type, _transcribe_kwargs, corrected_file

        while True:
            item = _seg_queue.get()
            if item is None:                  # sentinel — stop
                _seg_queue.task_done()
                break

            audio_bytes, _seg_source = item
            _src_label = _mic_label(session_lang) if _seg_source == "mic" else ""
            seg_dur = len(audio_bytes) / (SAMPLE_RATE * 2)
            tr_info(f"Transcribing {seg_dur:.1f}s {_seg_source} audio (queue depth={_seg_queue.qsize()})")

            # Write temp WAV
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_f:
                tmp = tmp_f.name
            t0 = time.monotonic()
            try:
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(SAMPLE_RATE)
                    wf.writeframes(audio_bytes)

                prompt = _initial_prompt(session_lang)
                segs, info = whisper.transcribe(
                    tmp,
                    language       = session_lang,
                    initial_prompt = prompt,
                    **_transcribe_kwargs,
                )
                seg_list = list(segs)

            except Exception as e:
                if device == "cuda" and ("cublas" in str(e).lower() or "cuda" in str(e).lower()):
                    tr_info(f"CUDA error, switching to CPU: {e}")
                    device = "cpu"
                    compute_type = "int8"
                    whisper = WhisperModel(model_path, device="cpu", compute_type="int8")
                    try:
                        segs, info = whisper.transcribe(
                            tmp, language=session_lang, **_transcribe_kwargs)
                        seg_list = list(segs)
                    except Exception as e2:
                        tr_error(f"transcribe error (CPU fallback): {e2}")
                        _seg_queue.task_done()
                        continue
                else:
                    tr_error(f"transcribe error: {e}")
                    _seg_queue.task_done()
                    continue
            finally:
                try:
                    os.remove(tmp)
                except Exception:
                    pass

            elapsed = time.monotonic() - t0
            tr_info(f"Transcribed in {elapsed:.1f}s: lang={info.language} "
                    f"({info.language_probability:.0%}) segments={len(seg_list)}")

            # Language detection: lock in when confident, fall back after retries
            if not session_lang:
                try:
                    from transcriber import LANG_ALIAS
                    lang = LANG_ALIAS.get(info.language, info.language)
                    _lang_detect_attempts[0] += 1

                    # CJK languages are acoustically distinct — accept at lower threshold
                    threshold = 0.3 if lang in {"zh", "ja"} else 0.5
                    locked = lang in {"zh", "ja", "en"} and info.language_probability >= threshold

                    # After 3 uncertain segments, fall back to UI language
                    if not locked and _lang_detect_attempts[0] >= 3:
                        from i18n import _LANG as _ui_lang
                        if _ui_lang in {"zh", "ja", "en"}:
                            lang = _ui_lang
                        elif lang not in {"zh", "ja", "en"}:
                            lang = "en"
                        tr_info(f"Language uncertain after {_lang_detect_attempts[0]} attempts "
                                f"— using {'UI language' if _ui_lang in {'zh','ja','en'} else 'fallback'}: {lang}")
                        locked = True

                    if locked:
                        session_lang = lang
                        tr_info(f"Language locked: {session_lang} ({info.language_probability:.0%})")
                        with open(LANG_FILE, "w", encoding="utf-8") as f:
                            f.write(session_lang)
                        if _current_model_lang != session_lang:
                            new_path = _resolve_model(session_lang)
                            if new_path != model_path:
                                tr_info(f"Switching to {session_lang} model...")
                                whisper = WhisperModel(new_path, device=device, compute_type=compute_type)
                                _transcribe_kwargs = _make_transcribe_kwargs(new_path)
                                _current_model_lang = session_lang
                                tr_info("Model switched")
                except Exception:
                    pass

            # Filter hallucinations and low-confidence segments
            lines = []
            for i, s in enumerate(seg_list):
                text = s.text.strip()
                nsp  = getattr(s, "no_speech_prob", 0.0)
                lp   = getattr(s, "avg_logprob", 0.0)
                if not text:
                    tr_debug(f"  seg[{i}] EMPTY (nsp={nsp:.2f} lp={lp:.2f})")
                    continue
                if any(h in text for h in HALLUCINATION):
                    tr_debug(f"  seg[{i}] HALLUCINATION: {text[:40]}")
                    continue
                if nsp > 0.7:
                    tr_debug(f"  seg[{i}] LOW_CONF no_speech={nsp:.2f}: {text[:40]}")
                    continue
                tr_debug(f"  seg[{i}] OK nsp={nsp:.2f} lp={lp:.2f}: {text[:60]}")
                lines.append(text)

            original = " ".join(lines).strip()
            if not original:
                tr_debug("WHISPER_EMPTY (all segments filtered)")
                _seg_queue.task_done()
                continue

            tr_info(f"[pipeline] {'[mic] ' if _seg_source == 'mic' else ''}original: {original}")
            _append_transcript(_src_label + original)

            # Per-segment correction: call LLM immediately after transcription
            corrected_text = _correct_segment(original, session_lang, cfg)
            if corrected_text != original:
                tr_info(f"[pipeline] corrected: {corrected_text}")
            else:
                corrected_text = original  # no correction or API unavailable

            # Deterministic glossary fix (applies even if the LLM step was skipped)
            corrected_text = apply_glossary(corrected_text, _glossary)

            # Append to corrected file and update signal for UI polling
            if corrected_file:
                _append_corrected(_src_label + corrected_text, corrected_file)

            _seg_queue.task_done()

    _worker = threading.Thread(target=_transcribe_loop, daemon=True, name="transcribe")
    _worker.start()

    # ── Audio device setup ────────────────────────────────────────────────────
    if sys.platform == "win32":
        import pyaudiowpatch as pyaudio
    else:
        import pyaudio

    pa = pyaudio.PyAudio()

    from device_utils import select_active_device
    device_index, dev_info = select_active_device(pa)
    channels    = dev_info["maxInputChannels"]
    sample_rate = int(dev_info["defaultSampleRate"])
    sample_size = pa.get_sample_size(pyaudio.paInt16)
    sys_info(f"audio device: {dev_info['name']}  rate={sample_rate}")
    stream = pa.open(
        format=pyaudio.paInt16, channels=channels,
        rate=sample_rate, frames_per_buffer=CHUNK_SIZE,
        input=True, input_device_index=device_index,
    )

    need_resample = (sample_rate != SAMPLE_RATE)
    if need_resample:
        try:
            import scipy.signal as _ss
            sys_info(f"Resampling: {sample_rate}→{SAMPLE_RATE}")
        except ImportError:
            tr_warn("scipy not found, resampling disabled")
            need_resample = False

    def _to_mono16k(raw: bytes) -> np.ndarray:
        arr = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            arr = arr[::channels]
        if need_resample:
            import scipy.signal as _ss
            arr = _ss.resample_poly(arr, SAMPLE_RATE, sample_rate).astype(np.int16)
        return arr

    # ── Mic pipeline (independent VAD → shared transcription queue) ──────────
    # Mic audio is NOT mixed into system audio; each source is transcribed
    # separately and appended to the same corrected file by timestamp.
    # Mixing happens at text level. On Air button (MIC_ONAIR signal) controls
    # whether mic feeds into the VAD.
    enable_mic    = cfg.getboolean("recording", "enable_mic", fallback=True)
    _mic_vad      = AccumulatingVAD(silence_sec=silence_s, min_accum_sec=min_s, max_sec=max_s)
    _mic_vad_lock = threading.Lock()
    _mic_stop     = threading.Event()
    _mic_thread:  threading.Thread | None = None

    if enable_mic:
        _mic_idx, _mic_info = None, None
        try:
            _d = pa.get_default_input_device_info()
            if _d.get("maxInputChannels", 0) > 0 and not _d.get("isLoopbackDevice", False):
                _mic_idx, _mic_info = int(_d["index"]), _d
        except Exception:
            pass
        if _mic_idx is None:
            for _i in range(pa.get_device_count()):
                _d = pa.get_device_info_by_index(_i)
                if _d.get("maxInputChannels", 0) > 0 and not _d.get("isLoopbackDevice", False):
                    _mic_idx, _mic_info = _i, _d
                    break

        if _mic_idx is not None:
            _mic_rate  = int(_mic_info["defaultSampleRate"])
            _mic_ch    = min(int(_mic_info["maxInputChannels"]), 2)
            _mic_gain  = cfg.getfloat("recording", "mic_gain", fallback=1.0)
            _mic_do_rs = (_mic_rate != SAMPLE_RATE)
            sys_info(f"Mic pipeline: {_mic_info['name']}  rate={_mic_rate}  ch={_mic_ch}")

            def _mic_run():
                if sys.platform == "win32":
                    import pyaudiowpatch as _paw
                else:
                    import pyaudio as _paw
                _pa2 = _paw.PyAudio()
                _prev_onair = False
                try:
                    _st = _pa2.open(
                        format=_paw.paInt16, channels=_mic_ch,
                        rate=_mic_rate, frames_per_buffer=CHUNK_SIZE,
                        input=True, input_device_index=_mic_idx,
                    )
                    while not _mic_stop.is_set():
                        try:
                            _raw = _st.read(CHUNK_SIZE, exception_on_overflow=False)
                            _arr = np.frombuffer(_raw, dtype=np.int16)
                            if _mic_ch > 1:
                                _arr = _arr.reshape(-1, _mic_ch).mean(axis=1).astype(np.int16)
                            if _mic_do_rs:
                                import scipy.signal as _ss
                                _arr = _ss.resample_poly(_arr, SAMPLE_RATE, _mic_rate).astype(np.int16)
                            if _mic_gain != 1.0:
                                _arr = np.clip(_arr.astype(np.float32) * _mic_gain,
                                               -32768, 32767).astype(np.int16)

                            _onair     = os.path.exists(MIC_ONAIR)
                            _in_sess   = os.path.exists(SIGNAL_SESSION)

                            if _onair and not _prev_onair:
                                # On Air just activated.
                                # Offset = system audio frames written so far / sample_rate.
                                # Subtract one mic-chunk worth of frames to compensate for
                                # the one-chunk detection lag (On Air was pressed before
                                # this chunk was read, so actual start is ~1 chunk earlier).
                                _frames_now = _sys_frames[0]
                                _lag_frames = round(CHUNK_SIZE / SAMPLE_RATE * sample_rate)
                                _offset_frames = max(0, _frames_now - _lag_frames)
                                _mic_state["onair_offset"] = _offset_frames / sample_rate
                                _mic_state["cur_buf"].clear()
                                with _mic_vad_lock:
                                    _mic_vad._reset()
                            elif not _onair and _prev_onair:
                                # On Air just deactivated — save recorded audio + flush VAD
                                if _mic_state["cur_buf"] and _in_sess:
                                    _pcm = np.concatenate(_mic_state["cur_buf"]).tobytes()
                                    _mic_state["segments"].append(
                                        (_mic_state["onair_offset"] or 0.0, _pcm)
                                    )
                                _mic_state["cur_buf"].clear()
                                with _mic_vad_lock:
                                    _fseg = _mic_vad.force_flush()
                                if _fseg and _in_sess:
                                    with _aec_lock:
                                        _snap = [c for _, c in _aec_buf]
                                    if _is_echo_segment(_fseg, _snap, _AEC_THRESH):
                                        tr_info("AEC: echo segment suppressed (on-air off flush)")
                                    else:
                                        _seg_queue.put((_fseg, "mic"))
                            _prev_onair = _onair

                            if _onair and _in_sess:
                                _mic_state["cur_buf"].append(_arr)  # record for MP3 mixing
                                with _mic_vad_lock:
                                    _mseg = _mic_vad.feed(_arr)
                                if _mseg:
                                    with _aec_lock:
                                        _snap = [c for _, c in _aec_buf]
                                    if _is_echo_segment(_mseg, _snap, _AEC_THRESH):
                                        tr_info("AEC: echo segment suppressed")
                                    else:
                                        _seg_queue.put((_mseg, "mic"))
                        except Exception as _e:
                            tr_warn(f"Mic read error: {_e}")
                            time.sleep(0.05)
                    _st.stop_stream()
                    _st.close()
                finally:
                    _pa2.terminate()
                    sys_info("Mic pipeline stopped")

            _mic_thread = threading.Thread(target=_mic_run, daemon=True, name="mic")
            _mic_thread.start()
        else:
            sys_info("Mic pipeline: no input device found, disabled")
            enable_mic = False

    # ── Main loop ─────────────────────────────────────────────────────────────
    sys_info("pipeline loop start")
    session_was_active = False

    try:
        while not os.path.exists(SIGNAL_STOP):
            session_active = os.path.exists(SIGNAL_SESSION)

            if not session_active and not session_was_active:
                time.sleep(0.2)
                continue

            # Session start
            if session_active and not session_was_active:
                # Re-read config.ini (language / model_size / device may have been
                # changed in the settings UI since the pipeline started) so the new
                # settings take effect on this recording without an app restart.
                _sess_cfg = configparser.ConfigParser()
                _sess_cfg.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")
                _sess_lang_raw = _sess_cfg.get("recording", "language", fallback="auto").strip().lower()
                _sess_lang = _sess_lang_raw if _sess_lang_raw in {"zh", "ja", "en"} else None

                if not _sess_lang and os.path.exists(LANG_FILE):
                    try:
                        with open(LANG_FILE, encoding="utf-8") as _lf:
                            _l = _lf.read().strip()
                        if _l in {"zh", "ja", "en"}:
                            _sess_lang = _l
                    except Exception:
                        pass

                # ── Re-read model size / device (user-configurable, no limit) ──
                _new_device_cfg = _sess_cfg.get("recording", "device", fallback="auto").strip().lower()
                if _new_device_cfg == "auto":
                    try:
                        import ctranslate2
                        _new_device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                    except Exception:
                        _new_device = "cpu"
                else:
                    _new_device = _new_device_cfg
                _new_model_size = _sess_cfg.get("recording", "model_size", fallback="small").strip()

                _settings_changed = (_new_model_size != model_size) or (_new_device != device)
                if _settings_changed:
                    sys_info(f"Model/device changed: {model_size}/{device} → "
                             f"{_new_model_size}/{_new_device}")
                    model_size   = _new_model_size
                    device       = _new_device
                    compute_type = "float16" if device == "cuda" else "int8"
                    # Rebuild per-language model map so the new base model_size is
                    # used as the fallback for languages without a dedicated model.
                    _LANG_MODELS = {
                        "ja": _sess_cfg.get("models", "ja", fallback=model_size),
                        "zh": _sess_cfg.get("models", "zh", fallback=model_size),
                        "en": _sess_cfg.get("models", "en", fallback=model_size),
                    }

                # Reload the model if the language, model size, or device changed.
                if _sess_lang != session_lang or _settings_changed:
                    if _sess_lang != session_lang:
                        sys_info(f"Language: {session_lang or 'auto'} → {_sess_lang or 'auto'}")
                    session_lang = _sess_lang
                    _new_mp = _resolve_model(session_lang)
                    if _new_mp != model_path or _settings_changed:
                        try:
                            sys_info(f"Reloading model: {_new_mp} (device={device})")
                            whisper = WhisperModel(_new_mp, device=device, compute_type=compute_type)
                            model_path = _new_mp
                            _transcribe_kwargs = _make_transcribe_kwargs(model_path)
                            _current_model_lang = session_lang
                        except Exception as _me:
                            sys_error(f"Model reload failed: {_me}")

                _open_session()
                session_was_active = True
                with open(os.path.join(_BASE, ".recording_start"), "w") as f:
                    f.write(str(time.time()))

            # Session end
            if not session_active and session_was_active:
                # Flush remaining audio from system VAD buffer
                seg = vad.force_flush()
                if seg:
                    if raw_fh:
                        raw_fh.write(seg)
                    _seg_queue.put((seg, "system"))

                # Flush remaining audio from mic VAD buffer + save on-air recording
                if enable_mic:
                    if _mic_state["cur_buf"]:
                        _pcm = np.concatenate(_mic_state["cur_buf"]).tobytes()
                        dur_s = len(_pcm) / (SAMPLE_RATE * 2)
                        _mic_state["segments"].append(
                            (_mic_state["onair_offset"] or 0.0, _pcm)
                        )
                        _mic_state["cur_buf"].clear()
                        sys_info(f"Mic recording saved at session end: {dur_s:.1f}s "
                                 f"offset={_mic_state['onair_offset']:.1f}s")
                    sys_info(f"Mic segments total: {len(_mic_state['segments'])}")
                    with _mic_vad_lock:
                        mic_seg = _mic_vad.force_flush()
                    if mic_seg:
                        _seg_queue.put((mic_seg, "mic"))

                # Wait for all pending transcriptions before closing session
                sys_info("Waiting for transcriptions to complete...")
                _seg_queue.join()

                _close_session(channels, sample_size, sample_rate)
                session_was_active = False
                continue

            raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            chunk = _to_mono16k(raw)

            # Write raw audio for session recording; count frames for precise mic offset
            if session_active and raw_fh:
                try:
                    raw_fh.write(raw)
                    _sys_frames[0] += CHUNK_SIZE  # frames at original sample_rate
                except Exception:
                    pass

            # Update AEC reference buffer
            if enable_mic:
                _now_t = time.monotonic()
                with _aec_lock:
                    _aec_buf.append((_now_t, chunk))
                    while _aec_buf and _now_t - _aec_buf[0][0] > _AEC_BUF_SEC:
                        _aec_buf.popleft()

            # Feed to VAD
            seg = vad.feed(chunk)
            if seg:
                _seg_queue.put((seg, "system"))

    except KeyboardInterrupt:
        pass
    finally:
        # Flush remaining audio from system VAD
        seg = vad.force_flush()
        if seg:
            _seg_queue.put((seg, "system"))

        # Flush remaining audio from mic VAD + save on-air recording, then stop mic thread
        if enable_mic:
            if _mic_state["cur_buf"]:
                _pcm = np.concatenate(_mic_state["cur_buf"]).tobytes()
                _mic_state["segments"].append(
                    (_mic_state["onair_offset"] or 0.0, _pcm)
                )
                _mic_state["cur_buf"].clear()
            with _mic_vad_lock:
                mic_seg = _mic_vad.force_flush()
            if mic_seg:
                _seg_queue.put((mic_seg, "mic"))
        _mic_stop.set()
        if _mic_thread is not None:
            _mic_thread.join(timeout=3)

        # Stop background thread
        _seg_queue.put(None)
        _seg_queue.join()
        _worker.join(timeout=60)

        if session_was_active:
            _close_session(channels, sample_size, sample_rate)

        # Wait for any in-progress MP3 conversions to finish
        for _ct in _conv_threads:
            if _ct.is_alive():
                tr_info("Waiting for MP3 conversion to complete...")
                _ct.join(timeout=120)

        if stream is not None:
            stream.stop_stream()
            stream.close()
        pa.terminate()
        sys_info("pipeline stopped")

    for f in (SIGNAL_STOP, MIC_ONAIR):
        try:
            os.remove(f)
        except Exception:
            pass


def _append_transcript(text: str):
    """Append transcribed text to the transcript file recorded in STATE_FILE."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            path = f.read().strip()
        if path and os.path.exists(os.path.dirname(path)):
            from datetime import datetime
            ts = datetime.now().strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8-sig") as f:
                f.write(f"[{ts}] {text}\n")
    except Exception as e:
        tr_error(f"transcript append error: {e}")


def _append_corrected(text: str, path: str):
    """Append corrected text to the corrected file and update CORRECTED_STATE."""
    try:
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        with open(path, "a", encoding="utf-8-sig") as f:
            f.write(f"[{ts}] {text}\n")
        # Touch CORRECTED_STATE so the presenter's polling thread detects the update
        with open(CORRECTED_STATE, "w", encoding="utf-8") as f:
            f.write(path)
    except Exception as e:
        tr_error(f"corrected append error: {e}")


if __name__ == "__main__":
    run()

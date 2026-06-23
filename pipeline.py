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
import re
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
SIGNAL_READY     = os.path.join(_BASE, ".pipeline_ready")
SIGNAL_LOCK      = os.path.join(_BASE, ".pipeline.lock")
MIC_ONAIR        = os.path.join(_BASE, ".mic_onair")
LANG_FILE          = os.path.join(_BASE, ".last_language")
STATE_FILE         = os.path.join(_BASE, ".last_transcript")
CORRECTED_STATE    = os.path.join(_BASE, ".last_corrected")
SUBTITLE_FILE      = os.path.join(_BASE, ".subtitle_text")

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


def _is_repetition_hallucination(text: str) -> bool:
    """Detect Whisper loop hallucinations such as repeated single terms.

    Hard/noisy audio can make Whisper emit long low-information loops
    ("プロセス、プロセス..." or "税金の税金..."). These can pass no_speech/logprob
    filters, then spend extra time in LLM correction and poison the transcript.
    Keep this conservative: only drop text with a dominant repeated token or a
    repeated short phrase covering most of the segment.
    """
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 24:
        return False

    tokens = [t for t in re.split(r"[、。,.!?！？\s]+", text) if t]
    if len(tokens) >= 8:
        counts = collections.Counter(tokens)
        token, count = counts.most_common(1)[0]
        if len(token) >= 2 and count >= 6 and count / len(tokens) >= 0.65:
            return True

    for size in range(2, min(10, len(compact) // 3) + 1):
        sliding = [compact[i:i + size] for i in range(0, len(compact) - size + 1)]
        counts = collections.Counter(sliding)
        phrase, count = counts.most_common(1)[0]
        if count >= 8 and (count * len(phrase)) / len(compact) >= 0.45:
            return True

        chunks = [compact[i:i + size] for i in range(0, len(compact) - size + 1, size)]
        if len(chunks) < 6:
            continue
        counts = collections.Counter(chunks)
        phrase, count = counts.most_common(1)[0]
        if count >= 6 and (count * len(phrase)) / len(compact) >= 0.55:
            return True
    return False


def _acquire_pipeline_lock():
    """Return an exclusive process lock handle, or None if another pipeline runs."""
    fh = open(SIGNAL_LOCK, "a+b")
    fh.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    try:
        fh.seek(0)
        fh.write(b"1")
        fh.flush()
        fh.seek(0)
    except Exception:
        pass
    return fh


def _release_pipeline_lock(fh) -> None:
    if not fh:
        return
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


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
                 max_sec: float = 30.0,
                 min_speech_sec: float = 0.5):
        self._threshold    = threshold
        self._turn_silence = silence_sec
        self._min_sec      = min_accum_sec
        self._max_sec      = max_sec
        # Minimum speech in a force-flushed window for it to be worth transcribing.
        # Windows below this are silence/music: Whisper returns nothing yet can burn
        # 100s+ on them (temperature fallback), so we drop them from transcription.
        self._min_speech   = min_speech_sec

        self._accum: list[np.ndarray] = []
        self._accum_dur   = 0.0
        self._speech_dur  = 0.0
        self._silence_dur = 0.0
        self._speaking    = False

        # Load the Silero VAD. faster-whisper >=1.x exposes it via get_vad_model()
        # (the constructor needs the bundled ONNX path, and __call__ takes
        # num_samples — NOT the sample rate). The previous SileroVADModel() call
        # with no args raised on every chunk, silently falling back to a crude
        # amplitude check that misclassifies quiet speech as silence — which, with
        # the silence-skip, dropped real speech from transcription.
        self._model = None
        self._vad_frame = 512   # Silero v6 processes audio in 512-sample frames
        # Rolling buffer: capture chunks (after 48k→16k resampling) are often
        # SMALLER than one Silero frame — e.g. a 1024-frame read at 48 kHz becomes
        # ~341 samples at 16 kHz, < 512 — so Silero could never run per-chunk and
        # we silently fell back to amplitude (→ quiet speech read as silence →
        # everything skipped). Accumulate across chunks until ≥1 frame is available.
        self._vad_buf    = np.zeros(0, dtype=np.float32)
        self._last_speech = False
        try:
            from faster_whisper.vad import get_vad_model
            self._model = get_vad_model()
            sys_info(f"VAD: Silero v6 (silence={silence_sec}s min={min_accum_sec}s "
                     f"max={max_sec}s thr={threshold})")
        except Exception as e:
            sys_info(f"VAD: amplitude fallback (Silero load failed: {e}) "
                     f"(silence={silence_sec}s min={min_accum_sec}s max={max_sec}s)")

    def has_model(self) -> bool:
        """True when the reliable Silero VAD is loaded (vs amplitude fallback)."""
        return self._model is not None

    def _is_speech(self, chunk: np.ndarray) -> bool:
        if self._model is not None:
            try:
                self._vad_buf = np.concatenate(
                    [self._vad_buf, chunk.astype(np.float32) / 32768.0])
                n = (len(self._vad_buf) // self._vad_frame) * self._vad_frame
                if n >= self._vad_frame:
                    frames = self._vad_buf[:n]
                    self._vad_buf = self._vad_buf[n:]   # keep sub-frame remainder
                    out = np.asarray(self._model(frames, num_samples=self._vad_frame))
                    self._last_speech = float(out.max()) >= self._threshold
                # Between frame boundaries, reuse the most recent decision.
                return self._last_speech
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
            # Only trust a low speech_dur enough to DROP the window when the
            # reliable Silero VAD is active. With the crude amplitude fallback,
            # quiet speech can read as 0s of speech, so skipping would lose it —
            # transcribe everything instead and let Whisper's own VAD/filters cope.
            if self._model is not None and self._speech_dur < self._min_speech:
                # Window full but essentially no speech (silence/music). Transcribing
                # it wastes large amounts of CPU for an empty result and needlessly
                # grows the backlog; the audio is still saved to the session
                # recording separately, so nothing is lost from the MP3.
                # Logged at INFO (parseable) so the dashboard "audio recorded" timer
                # still advances for this window even though it isn't transcribed.
                tr_info(f"VAD silence-skip: {self._accum_dur:.1f}s accumulated "
                        f"(speech={self._speech_dur:.1f}s < {self._min_speech:.1f}s) "
                        f"— recorded, not transcribed")
                self._reset()
                return None
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
        """Flush whatever audio remains at session stop.

        Unlike a mid-session turn flush this must NOT apply the ``min_accum_sec``
        gate: the tail here is usually a sub-``min_accum_sec`` remnant left over
        after the previous force-flush, and dropping it would silently lose the
        last thing the user said. Only skip when the reliable Silero VAD is active
        AND the remnant has essentially no speech (``speech_dur < min_speech_sec``)
        — that is pure trailing silence/noise, which Whisper would merely burn
        time hallucinating over. With the amplitude fallback ``speech_dur`` is
        unreliable, so always flush rather than risk dropping real speech."""
        if not self._accum:
            self._reset()
            return None
        if self._model is not None and self._speech_dur < self._min_speech:
            tr_debug(f"force_flush: dropping silent tail "
                     f"(speech={self._speech_dur:.1f}s < {self._min_speech:.1f}s)")
            self._reset()
            return None
        return self._flush("stop")


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
    """Call LLM to correct one transcribed segment. Falls back to original.

    Skipped entirely when post-session online refinement is enabled: that path
    does ONE combined full-correction + industry-term pass at stop, so per-segment
    LLM correction here would be redundant work and — more importantly — dozens of
    API calls per session, which is what trips the provider's rate limit (HTTP
    429). The deterministic glossary still runs in the correction worker, so live
    output stays glossary-corrected even while LLM correction is deferred."""
    if not cfg.getboolean("summary", "enable_correction", fallback=True) \
       or cfg.getboolean("summary", "enable_online_refine", fallback=False):
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

    # Strong, language-agnostic system prompt. Small local models (e.g. qwen2.5)
    # otherwise tend to "explain" or translate fragments instead of just fixing
    # them, polluting the transcript with meta-commentary.
    _system = (
        "You are an ASR transcript proofreader. You fix ONLY mis-recognized words "
        "and punctuation in the given text.\n"
        "ABSOLUTE RULES — follow every one:\n"
        "1. Output ONLY the corrected text. No explanations, notes, comments, or labels.\n"
        "2. Keep the EXACT original language(s). NEVER translate. Mixed-language text "
        "stays mixed exactly as-is.\n"
        "3. Do NOT add, remove, summarize, or rephrase content. Keep length similar.\n"
        "4. If a part is unclear, leave it unchanged. When in doubt, output it verbatim.\n"
        "5. Never describe what you changed. Never write '对应' / '比如' / '因此' / "
        "'修正後' / 'Here is' style meta text.\n"
        "出力は校正後のテキストのみ。説明・翻訳・注釈は一切禁止。"
    )
    _user = {
        "ja": f"次のテキストの誤認識と句読点のみを修正し、校正後のテキストだけを返してください:\n\n{text}",
        "zh": f"仅修正下面文字的识别错误和标点，只返回修正后的文字本身:\n\n{text}",
        "en": f"Fix only the recognition errors and punctuation below. Return only the corrected text:\n\n{text}",
    }.get(lang or "", None)
    if _user is None:
        _user = f"Fix only the recognition errors and punctuation below. Return only the corrected text:\n\n{text}"

    verify  = cfg.getboolean("network", "ssl_verify", fallback=True)
    px      = cfg.get("network", "https_proxy", fallback="")
    proxies = {"https": px, "http": px} if px else None

    import requests
    try:
        r = requests.post(
            url,
            json={"model": model,
                  "messages": [{"role": "system", "content": _system},
                               {"role": "user",   "content": _user}],
                  "max_tokens": max(120, int(len(text) * 1.8)),
                  "temperature": 0.0},
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
        if not result:
            return text
        # Pollution guard: a proper correction stays close to the input length.
        # If the model returned a much longer string, it almost certainly added
        # explanations/translations — discard it and keep the original text.
        if len(result) > len(text) * 2 + 30:
            tr_debug(f"correction discarded (output too long: "
                     f"{len(result)} vs {len(text)} chars) — keeping original")
            return text
        # Meta-commentary guard: telltale phrases that only appear when the model
        # is explaining rather than correcting.
        _meta_markers = ("对应处", "对应的", "修正后的文本", "因此，修正", "请替换",
                         "具体活动名称", "假设这是", "Note:", "Explanation:")
        if any(m in result for m in _meta_markers):
            tr_debug("correction discarded (meta-commentary detected) — keeping original")
            return text
        return result
    except Exception as e:
        tr_debug(f"correction API error: {e}")
        return text


# ── model-specific transcription parameters ───────────────────────────────────
def _make_transcribe_kwargs(model_path: str, device: str = "cpu") -> dict:
    """Return transcribe() kwargs appropriate for the given model and device.

    On CPU, beam search (beam_size=5) is several times slower than greedy
    decoding for a marginal accuracy gain, which is the difference between
    keeping up with real time and building an ever-growing backlog. Use greedy
    (beam_size=1) on CPU and reserve beam search for CUDA.

    The temperature fallback list is the other big CPU time sink: when a decode
    fails the quality thresholds (repetition / compression ratio), faster-whisper
    re-decodes the WHOLE window once per temperature. On hard/music segments even
    2 values can make a 4 s clip take 30 s+. Use a one-item list on CPU to keep
    the API shape correct while disabling CPU fallback storms; CUDA is fast
    enough to keep the full list."""
    import json as _json

    kwargs: dict = dict(
        beam_size                  = 1 if device == "cpu" else 5,
        temperature                = ([0.0] if device == "cpu"
                                      else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]),
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
    _pipeline_lock = _acquire_pipeline_lock()
    if _pipeline_lock is None:
        sys_error("Another pipeline.py instance is already running; exiting")
        return

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")

    # Startup-race guard: capture whether a recording session was already pending
    # the instant this process started, BEFORE the slow CUDA/model init below. The
    # presenter writes .pipeline_session and then spawns us, so a quick start→stop
    # can create *and remove* the session entirely while we are still loading the
    # model (large-v3 on CUDA ≈ 9 s). If that happens, the main loop would start
    # with the session already gone, never observe it, and never write
    # .pipeline_session_done — hanging the presenter's stop forever. We reconcile
    # this right after the loop starts (see "pipeline loop start").
    _session_at_startup = os.path.exists(SIGNAL_SESSION)

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
        from asr_backend import create_asr_backend, WhisperBackend
    except Exception as e:
        sys_error(f"ASR backend load failed: {e}")
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
    _HEAVY_CPU_MODELS = {"large", "large-v1", "large-v2", "large-v3"}

    def _is_heavy_cpu_model(candidate: str) -> bool:
        name = os.path.basename(candidate.rstrip("\\/")).lower()
        return name in _HEAVY_CPU_MODELS

    def _resolve_model(lang: str | None) -> str:
        candidate = _LANG_MODELS.get(lang or "", model_size)
        if device == "cpu" and candidate != model_size and _is_heavy_cpu_model(candidate):
            sys_info(f"CPU mode: skipping language model override {candidate} "
                     f"(lang={lang}); using {model_size}")
            candidate = model_size
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
    _transcribe_kwargs = _make_transcribe_kwargs(model_path, device)
    try:
        asr = create_asr_backend(cfg, model_path, device, compute_type, _transcribe_kwargs,
                                 log_info=sys_info, log_warn=tr_warn)
    except Exception as e:
        if device == "cuda":
            sys_info(f"ASR backend unavailable on CUDA ({e}), falling back to CPU")
            device = "cpu"
            compute_type = "int8"
            _transcribe_kwargs = _make_transcribe_kwargs(model_path, "cpu")
            try:
                asr = create_asr_backend(cfg, model_path, device, compute_type,
                                         _transcribe_kwargs, log_info=sys_info,
                                         log_warn=tr_warn)
            except Exception as e2:
                sys_error(f"Failed to load ASR backend: {e2}")
                return
        else:
            sys_error(f"Failed to load ASR backend: {e}")
            return
    if getattr(asr, "name", "") == "whisper":
        device = getattr(asr, "device", device)
        compute_type = getattr(asr, "compute_type", compute_type)
    sys_info(f"model ready (backend={getattr(asr, 'name', 'unknown')} device={device})")
    _current_model_lang = session_lang
    if getattr(asr, "name", "") == "whisper":
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
    silence_s    = cfg.getfloat("subtitle", "silence_sec",    fallback=2.0)
    min_s        = cfg.getfloat("subtitle", "min_accum_sec",  fallback=1.0)
    max_s        = cfg.getfloat("subtitle", "max_sec",        fallback=30.0)
    min_speech_s = cfg.getfloat("subtitle", "min_speech_sec", fallback=0.5)
    vad = AccumulatingVAD(silence_sec=silence_s, min_accum_sec=min_s, max_sec=max_s,
                          min_speech_sec=min_speech_s)

    # ── Mic state (shared across _open_session / _mic_run / _close_session) ──
    _mic_state: dict = {
        "segments":     [],   # list[tuple[float, bytes]] — (offset_sec, pcm_int16_bytes)
        "cur_buf":      [],   # list[np.ndarray] — current on-air audio accumulator
        "onair_offset": None, # float | None — session-relative offset in seconds when On Air started
    }
    _live_streams: dict[str, object | None] = {"system": None, "mic": None}
    _live_last: dict[str, str] = {"system": "", "mic": ""}
    _live_lock = threading.Lock()

    def _write_subtitle(text: str) -> None:
        try:
            with open(SUBTITLE_FILE, "w", encoding="utf-8") as f:
                f.write(text.strip())
        except Exception:
            pass

    def _reset_live_streams() -> None:
        with _live_lock:
            _live_streams["system"] = None
            _live_streams["mic"] = None
            _live_last["system"] = ""
            _live_last["mic"] = ""
        _write_subtitle("")

    def _live_accept(source: str, chunk: np.ndarray) -> None:
        if not getattr(asr, "supports_streaming", False):
            return
        try:
            pcm = chunk.astype(np.int16, copy=False).tobytes()
            with _live_lock:
                live = _live_streams.get(source)
                if live is None:
                    live = asr.new_stream(session_lang)
                    _live_streams[source] = live if live is not None else False
                if not live:
                    return
                text = live.accept(pcm).strip()
                if text and text != _live_last.get(source, ""):
                    _live_last[source] = text
                    label = _mic_label(session_lang) if source == "mic" else ""
                    _write_subtitle(label + text)
                    tr_debug(f"Nemotron partial [{source}]: {text[:80]}")
        except Exception as e:
            tr_debug(f"Nemotron live stream disabled for {source}: {e}")
            with _live_lock:
                _live_streams[source] = False

    _conv_threads: list[threading.Thread] = []  # non-daemon MP3 conversion threads to join
    # System audio sample counter — single-writer (system-capture), read by mic thread via GIL.
    # Counts frames at original sample_rate since session start → used for precise mic offset.
    _sys_frames = [0]
    _recording_active = threading.Event()

    # ── AEC: system audio reference buffer (system-vad writes, mic thread reads) ──
    _aec_buf:  collections.deque = collections.deque()  # list of np.ndarray chunks at SAMPLE_RATE
    _aec_lock: threading.Lock    = threading.Lock()
    _AEC_BUF_SEC  = 2.0
    _AEC_THRESH   = cfg.getfloat("recording", "aec_threshold", fallback=0.55)

    # ── Session state ─────────────────────────────────────────────────────────
    transcript_file:  str | None = None
    corrected_file:   str | None = None
    raw_file_path:    str | None = None
    raw_fh = None
    raw_lock = threading.Lock()
    session_ts: str | None = None
    # Per-segment audio cache: each VAD segment is written to a small WAV file in
    # this directory and only its *path* is queued, so the in-memory queue never
    # holds raw PCM. This decouples capture from (slow) transcription without RAM
    # build-up. Files are deleted as the worker consumes them; the dir is removed
    # when the session ends.
    seg_cache_dir: str | None = None
    _seg_seq      = [0]                       # monotonic segment counter
    _seg_seq_lock = threading.Lock()          # protects _seg_seq across threads

    _default_base  = os.path.join(os.path.expanduser("~"), "Documents", "Sound2Text")
    transcript_dir = os.path.expanduser(cfg.get("paths", "transcript_dir",
                             fallback=os.path.join(_default_base, "transcript")))
    corrected_dir  = os.path.expanduser(cfg.get("summary", "corrected_dir",
                             fallback=os.path.join(_default_base, "corrected")))
    audio_dir = os.path.expanduser(cfg.get("paths", "audio_dir",
                        fallback=os.path.join(_default_base, "audio")))

    def _open_session():
        nonlocal transcript_file, corrected_file, raw_file_path, raw_fh, session_ts
        nonlocal seg_cache_dir
        from datetime import datetime
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(transcript_dir, exist_ok=True)
        os.makedirs(corrected_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)
        transcript_file = os.path.join(transcript_dir, f"transcript_{session_ts}.txt")
        corrected_file  = os.path.join(corrected_dir,  f"corrected_{session_ts}.txt")
        raw_file_path   = os.path.join(audio_dir, f".tmp_audio_{session_ts}.raw")
        with raw_lock:
            raw_fh = open(raw_file_path, "wb")
        seg_cache_dir = os.path.join(audio_dir, f".seg_cache_{session_ts}")
        # Sweep orphaned caches left by a previously crashed session (single
        # pipeline process → no concurrent session can own them).
        try:
            for _d in os.listdir(audio_dir):
                if _d.startswith(".seg_cache_") and _d != os.path.basename(seg_cache_dir):
                    _stale = os.path.join(audio_dir, _d)
                    if os.path.isdir(_stale):
                        for _f in os.listdir(_stale):
                            try:
                                os.remove(os.path.join(_stale, _f))
                            except Exception:
                                pass
                        os.rmdir(_stale)
        except Exception:
            pass
        os.makedirs(seg_cache_dir, exist_ok=True)
        with _seg_seq_lock:
            _seg_seq[0] = 0
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
        _reset_live_streams()
        sys_info(f"Session started: {session_ts}")
        sys_info(f"Audio streaming to disk: {raw_file_path}")
        sys_info(f"Transcript: {transcript_file}")
        sys_info(f"Corrected:  {corrected_file}")

    def _close_session(channels: int, sample_size: int, sample_rate: int):
        nonlocal raw_fh, raw_file_path, transcript_file, corrected_file, session_ts
        nonlocal seg_cache_dir
        # Sanity check: the queue is drained before _close_session is called, so no
        # seg cache files should remain. Log any leftovers to catch a regression.
        _left = (len(os.listdir(seg_cache_dir))
                 if seg_cache_dir and os.path.isdir(seg_cache_dir) else 0)
        sys_info(f"finalize: queue drained, seg_cache files left={_left} "
                 f"(expected 0); converting raw → wav/mp3")
        with raw_lock:
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
        _reset_live_streams()

        # Remove the segment cache dir. By the time _close_session runs the queue
        # has been drained (main loop joins it first), so all seg files are gone;
        # clear any stragglers just in case, then drop the directory.
        if seg_cache_dir and os.path.isdir(seg_cache_dir):
            try:
                for _fn in os.listdir(seg_cache_dir):
                    try:
                        os.remove(os.path.join(seg_cache_dir, _fn))
                    except Exception:
                        pass
                os.rmdir(seg_cache_dir)
                tr_info("Segment cache cleaned up")
            except Exception as _ce:
                tr_warn(f"Segment cache cleanup error: {_ce}")
        seg_cache_dir = None

        raw_file_path   = None
        transcript_file = None
        corrected_file  = None
        session_ts      = None

        with open(SIGNAL_SESS_DONE, "w") as f:
            f.write("done")
        tr_info("finalize: session complete signal written — stop finished")

    # ── Async transcription thread ────────────────────────────────────────────
    # The queue carries only segment *file paths* (see _enqueue), never PCM, so it
    # can stay unbounded without memory growth: capture is never blocked and no
    # audio is dropped even when transcription falls far behind. Disk usage is
    # bounded by session length and the files are deleted as they are consumed.
    _seg_queue: queue.Queue = queue.Queue()
    # Text correction is separated from Whisper inference. The transcribe worker
    # appends raw text quickly, then queues LLM/glossary correction here so the
    # next audio segment can start Whisper immediately.
    _corr_queue: queue.Queue = queue.Queue()

    def _enqueue(seg: bytes, source: str) -> None:
        """Persist an audio segment to the on-disk cache and queue its path.

        ``emit_ts`` is the wall-clock time the segment's audio *ended* — i.e.
        when this speech finished — captured here at flush time. With dynamic VAD
        segments (often a full ``max_sec`` window when speech is continuous), the
        old segment-*start* timestamp lagged the visible text by up to one whole
        window (e.g. 8 s with ``max_sec=8``). Using the audio end time keeps the
        displayed ``[HH:MM:SS]`` aligned with when the words were actually spoken,
        and stays independent of any (much later) transcription backlog. Both
        system and mic segments are 16 kHz mono int16. Writing the segment to disk
        (instead of queuing the bytes) keeps the in-memory queue tiny."""
        emit_ts = time.time()
        cache = seg_cache_dir or tempfile.gettempdir()
        with _seg_seq_lock:
            _seg_seq[0] += 1
            idx = _seg_seq[0]
        seg_path = os.path.join(cache, f"seg_{idx:06d}_{source}.wav")
        try:
            with wave.open(seg_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(seg)
        except Exception as e:
            tr_error(f"segment cache write error: {e}")
            return
        # File is fully written + closed BEFORE it is queued, so the worker can
        # never pick up a half-written segment (the "don't process the file that
        # is still being recorded" guarantee). The log line below is emitted only
        # after both steps, making that ordering observable.
        seg_dur = len(seg) / 2 / SAMPLE_RATE
        _seg_queue.put((seg_path, source, emit_ts))
        tr_info(f"seg cached+queued: {os.path.basename(seg_path)} "
                f"({len(seg)}B, dur={seg_dur:.1f}s, "
                f"emit={time.strftime('%H:%M:%S', time.localtime(emit_ts))}) "
                f"depth={_seg_queue.qsize()}")

    _lang_detect_attempts = [0]   # segments tried while session_lang is still None

    def _correction_loop():
        """Background thread: corrects transcribed text and appends corrected output."""
        while True:
            item = _corr_queue.get()
            if item is None:
                _corr_queue.task_done()
                break

            try:
                (original, lang, corrected_path, seg_ts, src_label,
                 seg_dur, seg_source) = item

                corrected_text = _correct_segment(original, lang, cfg)
                if corrected_text != original:
                    tr_info(f"[pipeline] corrected: {corrected_text}")
                else:
                    corrected_text = original  # no correction or API unavailable

                # Deterministic glossary fix (applies even if the LLM step was skipped)
                corrected_text = apply_glossary(corrected_text, _glossary)

                if corrected_path:
                    _append_corrected(src_label + corrected_text, corrected_path, seg_ts)
                    tr_info(f"Segment finalized: {seg_dur:.1f}s {seg_source} audio "
                            f"transcribed+corrected")
            except Exception as e:
                tr_error(f"correction worker error (segment kept raw only): {e}")
            finally:
                _corr_queue.task_done()

    def _transcribe_loop():
        """Background thread: transcribes audio segments in order."""
        nonlocal session_lang, asr, _current_model_lang, device, compute_type, _transcribe_kwargs, corrected_file

        while True:
            item = _seg_queue.get()
            if item is None:                  # sentinel — stop
                _seg_queue.task_done()
                break

            # Every non-sentinel item MUST reach task_done() exactly once, or the
            # session-end _seg_queue.join() blocks forever and recording can never
            # stop (the pipeline never writes .pipeline_session_done). Wrap the whole
            # body so any unexpected error drops just this one segment and the worker
            # keeps draining the backlog instead of dying silently.
            try:
                seg_path, _seg_source, _emit_ts = item
                _src_label = _mic_label(session_lang) if _seg_source == "mic" else ""
                try:
                    with wave.open(seg_path, "rb") as _wf:
                        seg_dur = _wf.getnframes() / float(_wf.getframerate() or SAMPLE_RATE)
                except Exception:
                    seg_dur = 0.0
                tr_info(f"Transcribing {seg_dur:.1f}s {_seg_source} audio (queue depth={_seg_queue.qsize()})")

                # Transcribe the cached segment file directly (no temp re-write).
                t0 = time.monotonic()
                try:
                    prompt = _initial_prompt(session_lang)
                    asr_result = asr.transcribe(seg_path, session_lang, prompt)
                    info = asr_result.info
                    seg_list = list(asr_result.segments)

                except Exception as e:
                    if getattr(asr, "name", "") != "whisper":
                        tr_warn(f"Nemotron transcribe error, falling back to faster-whisper: {e}")
                        try:
                            _transcribe_kwargs = _make_transcribe_kwargs(model_path, device)
                            asr = WhisperBackend(model_path, device=device,
                                                 compute_type=compute_type,
                                                 transcribe_kwargs=_transcribe_kwargs)
                            asr_result = asr.transcribe(seg_path, session_lang, prompt)
                            info = asr_result.info
                            seg_list = list(asr_result.segments)
                        except Exception as e2:
                            tr_error(f"transcribe error (Whisper fallback): {e2}")
                            continue
                    elif device == "cuda" and ("cublas" in str(e).lower() or "cuda" in str(e).lower()):
                        tr_info(f"CUDA error, switching to CPU: {e}")
                        # The CPU model rebuild itself can fail; if it does, the
                        # error must NOT escape (it would kill the worker). Drop the
                        # segment and continue — the outer finally still task_done()s.
                        try:
                            device = "cpu"
                            compute_type = "int8"
                            _transcribe_kwargs = _make_transcribe_kwargs(model_path, "cpu")
                            asr = create_asr_backend(cfg, model_path, device, compute_type,
                                                     _transcribe_kwargs, log_info=sys_info,
                                                     log_warn=tr_warn)
                            asr_result = asr.transcribe(seg_path, session_lang, prompt)
                            info = asr_result.info
                            seg_list = list(asr_result.segments)
                        except Exception as e2:
                            tr_error(f"transcribe error (CPU fallback): {e2}")
                            continue
                    else:
                        tr_error(f"transcribe error: {e}")
                        continue
                finally:
                    try:
                        os.remove(seg_path)
                        tr_debug(f"seg removed: {os.path.basename(seg_path)}")
                    except Exception:
                        pass

                elapsed = time.monotonic() - t0
                throughput = seg_dur / elapsed if elapsed > 0 else 0
                # emit→finish lag: how far behind real time this result is. The line's
                # written timestamp uses emit (audio) time, NOT this finish time.
                _emit_str = time.strftime('%H:%M:%S', time.localtime(_emit_ts))
                _fin_str  = time.strftime('%H:%M:%S')
                tr_info(f"Transcribed in {elapsed:.1f}s ({throughput:.2f}x speed): "
                        f"emit={_emit_str} finished={_fin_str} lag={time.time()-_emit_ts:.0f}s "
                        f"lang={info.language} ({info.language_probability:.0%}) "
                        f"segments={len(seg_list)} queue={_seg_queue.qsize()}")

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
                                if getattr(asr, "name", "") != "whisper":
                                    _current_model_lang = session_lang
                                elif new_path != model_path:
                                    tr_info(f"Switching to {session_lang} model...")
                                    _transcribe_kwargs = _make_transcribe_kwargs(new_path, device)
                                    asr = create_asr_backend(cfg, new_path, device, compute_type,
                                                             _transcribe_kwargs,
                                                             log_info=sys_info,
                                                             log_warn=tr_warn)
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
                    if _is_repetition_hallucination(text):
                        tr_debug(f"  seg[{i}] REPETITION_HALLUCINATION: {text[:60]}")
                        continue
                    if nsp > 0.7:
                        tr_debug(f"  seg[{i}] LOW_CONF no_speech={nsp:.2f}: {text[:40]}")
                        continue
                    tr_debug(f"  seg[{i}] OK nsp={nsp:.2f} lp={lp:.2f}: {text[:60]}")
                    lines.append(text)

                original = " ".join(lines).strip()
                if not original:
                    tr_debug("WHISPER_EMPTY (all segments filtered)")
                    continue

                tr_info(f"[pipeline] {'[mic] ' if _seg_source == 'mic' else ''}original: {original}")
                # Timestamp = when the segment's audio ended (captured at enqueue
                # time as the flush wall-clock). Shared by both the raw transcript and
                # the corrected line so they match exactly and reflect real audio time,
                # not the (later) time transcription/correction finished.
                from datetime import datetime
                _seg_ts = datetime.fromtimestamp(_emit_ts).strftime("%H:%M:%S")
                _append_transcript(_src_label + original, _seg_ts)

                _corr_queue.put((original, session_lang, corrected_file, _seg_ts,
                                 _src_label, seg_dur, _seg_source))
                tr_debug(f"correction queued depth={_corr_queue.qsize()}")

            except Exception as e:
                # Last-resort guard: never let an unexpected error kill the worker
                # (that would hang the session-end queue join).
                tr_error(f"transcribe worker error (segment dropped): {e}")
            finally:
                _seg_queue.task_done()

    _corr_worker = threading.Thread(target=_correction_loop, daemon=True, name="correct")
    _corr_worker.start()
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

    def _open_system_stream():
        return pa.open(
            format=pyaudio.paInt16, channels=channels,
            rate=sample_rate, frames_per_buffer=CHUNK_SIZE,
            input=True, input_device_index=device_index,
        )

    stream = _open_system_stream()

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

    enable_mic = cfg.getboolean("recording", "enable_mic", fallback=True)

    # ── System audio capture and boundary detection threads ───────────────────
    # Capture owns device reads + session raw writes. VAD owns sentence/segment
    # boundary decisions. Transcription remains the existing _seg_queue worker.
    _system_audio_queue: queue.Queue = queue.Queue()
    _system_capture_stop = threading.Event()

    def _system_capture_loop():
        nonlocal stream
        _consec_errors = 0
        while not _system_capture_stop.is_set():
            try:
                raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                _consec_errors = 0
            except Exception as e:
                # The WASAPI loopback stream can be invalidated by the OS
                # (default-device change, sleep/resume, sample-rate switch).
                # Once that happens every read fails with "[Errno -9988] Stream
                # closed" forever, so capture silently dies and the next session
                # never records. Recover by reopening the stream after a short
                # run of consecutive failures (and don't spam the log per read).
                _consec_errors += 1
                if _consec_errors == 1:
                    tr_warn(f"System audio read error: {e}")
                if _consec_errors >= 5:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    try:
                        stream = _open_system_stream()
                        _consec_errors = 0
                        sys_info("System audio stream reopened after read errors")
                    except Exception as re:
                        tr_warn(f"System audio stream reopen failed: {re}")
                        time.sleep(1.0)
                else:
                    time.sleep(0.05)
                continue

            if not _recording_active.is_set():
                continue

            try:
                chunk = _to_mono16k(raw)
            except Exception as e:
                tr_warn(f"System audio resample error: {e}")
                continue

            with raw_lock:
                if raw_fh:
                    try:
                        raw_fh.write(raw)
                        _sys_frames[0] += CHUNK_SIZE  # frames at original sample_rate
                    except Exception:
                        pass

            _system_audio_queue.put(chunk)

        sys_info("System capture thread stopped")

    def _system_vad_loop():
        while True:
            chunk = _system_audio_queue.get()
            if chunk is None:
                _system_audio_queue.task_done()
                break

            try:
                # Update AEC reference buffer from the same audio that feeds VAD.
                if enable_mic:
                    _now_t = time.monotonic()
                    with _aec_lock:
                        _aec_buf.append((_now_t, chunk))
                        while _aec_buf and _now_t - _aec_buf[0][0] > _AEC_BUF_SEC:
                            _aec_buf.popleft()

                _live_accept("system", chunk)
                seg = vad.feed(chunk)
                if seg:
                    _enqueue(seg, "system")
            except Exception as e:
                tr_warn(f"System VAD error: {e}")
            finally:
                _system_audio_queue.task_done()

        sys_info("System VAD thread stopped")

    _system_capture_thread = threading.Thread(
        target=_system_capture_loop, daemon=True, name="system-capture")
    _system_vad_thread = threading.Thread(
        target=_system_vad_loop, daemon=True, name="system-vad")
    _system_capture_thread.start()
    _system_vad_thread.start()
    sys_info("System audio threads started: capture + vad")
    try:
        with open(SIGNAL_READY, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        sys_error(f"failed to write pipeline ready signal: {e}")

    def _drain_system_audio_queue(timeout: float = 5.0):
        # Give an in-flight stream.read() one chunk window to publish its chunk
        # before we flush VAD. Otherwise stop can race just before queue.put().
        time.sleep(max(0.05, CHUNK_SIZE / float(sample_rate) * 2))
        # Bounded drain. When nothing is playing, WASAPI loopback delivers no
        # frames, so the capture thread is parked inside stream.read() and this
        # queue stays empty — but a plain queue.join() here has no timeout, so any
        # task-accounting hiccup would hang the *entire* stop path forever (the
        # session can never finalize → .pipeline_session_done never written). Wait
        # only until the queue is actually drained or the deadline passes; the VAD
        # buffer is force-flushed right after regardless.
        deadline = time.time() + timeout
        while getattr(_system_audio_queue, "unfinished_tasks", 0) > 0 \
                and time.time() < deadline:
            time.sleep(0.02)
        _pending = getattr(_system_audio_queue, "unfinished_tasks", 0)
        if _pending > 0:
            # tr_info (not sys_info): SYS is excluded from the UI's default
            # ui_show filter, so finalize diagnostics must use a visible category.
            tr_info(f"WARN: system audio queue not drained within {timeout:.0f}s "
                    f"(qsize={_system_audio_queue.qsize()}, pending={_pending}); "
                    f"continuing finalize anyway")

    def _join_with_progress(q: "queue.Queue", name: str, every: float = 5.0) -> None:
        """Block until ``q`` is fully processed, logging depth periodically.

        Intentionally **unbounded** — a real transcription backlog after stop can
        legitimately take many minutes on weak hardware, and cutting it off would
        drop the tail of the recording. The periodic log just makes a long (or
        stuck) wait observable in the log file instead of a silent black hole, so
        the exact stalling stage is always visible."""
        last = time.time()
        while getattr(q, "unfinished_tasks", 0) > 0:
            time.sleep(0.1)
            now = time.time()
            if now - last >= every:
                last = now
                # tr_info so the stalling stage is visible in the UI (SYS is
                # filtered out of the default ui_show).
                tr_info(f"finalize: still waiting on {name} "
                        f"(qsize={q.qsize()}, pending={q.unfinished_tasks})")

    # ── Mic pipeline (independent VAD → shared transcription queue) ──────────
    # Mic audio is NOT mixed into system audio; each source is transcribed
    # separately and appended to the same corrected file by timestamp.
    # Mixing happens at text level. On Air button (MIC_ONAIR signal) controls
    # whether mic feeds into the VAD.
    _mic_vad      = AccumulatingVAD(silence_sec=silence_s, min_accum_sec=min_s, max_sec=max_s,
                                    min_speech_sec=min_speech_s)
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
                                        _enqueue(_fseg, "mic")
                            _prev_onair = _onair

                            if _onair and _in_sess:
                                _mic_state["cur_buf"].append(_arr)  # record for MP3 mixing
                                _live_accept("mic", _arr)
                                with _mic_vad_lock:
                                    _mseg = _mic_vad.feed(_arr)
                                if _mseg:
                                    with _aec_lock:
                                        _snap = [c for _, c in _aec_buf]
                                    if _is_echo_segment(_mseg, _snap, _AEC_THRESH):
                                        tr_info("AEC: echo segment suppressed")
                                    else:
                                        _enqueue(_mseg, "mic")
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

    # Reconcile the startup race (see _session_at_startup above): a session was
    # pending when we launched but is already gone now → it was started and
    # stopped during our model load, before we could observe it. Nothing was
    # captured, but the presenter is blocked waiting for the finalize signal, so
    # acknowledge completion immediately. (If the session is still active here,
    # the loop handles it normally.)
    if _session_at_startup and not os.path.exists(SIGNAL_SESSION):
        sys_info("session was started and stopped during pipeline startup "
                 "(model load) — nothing recorded; writing done signal so stop "
                 "can finish")
        try:
            with open(SIGNAL_SESS_DONE, "w") as f:
                f.write("startup-race")
        except Exception as _e:
            sys_error(f"failed to write startup-race done signal: {_e}")

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
                _new_asr_backend = _sess_cfg.get("asr", "backend",
                                                 fallback=cfg.get("asr", "backend",
                                                                  fallback="whisper")).strip().lower()
                _old_asr_backend = cfg.get("asr", "backend", fallback="whisper").strip().lower()

                _settings_changed = ((_new_model_size != model_size) or
                                     (_new_device != device) or
                                     (_new_asr_backend != _old_asr_backend))
                if _settings_changed:
                    sys_info(f"Model/device changed: {model_size}/{device} → "
                             f"{_new_model_size}/{_new_device}")
                    cfg          = _sess_cfg
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
                            _transcribe_kwargs = _make_transcribe_kwargs(_new_mp, device)
                            sys_info(f"Reloading ASR backend: {_new_mp} (device={device})")
                            asr = create_asr_backend(cfg, _new_mp, device, compute_type,
                                                     _transcribe_kwargs,
                                                     log_info=sys_info,
                                                     log_warn=tr_warn)
                            model_path = _new_mp
                            _current_model_lang = session_lang
                        except Exception as _me:
                            sys_error(f"ASR backend reload failed: {_me}")

                _open_session()
                _recording_active.set()
                session_was_active = True
                with open(os.path.join(_BASE, ".recording_start"), "w") as f:
                    f.write(str(time.time()))

            # Session end
            if not session_active and session_was_active:
                from datetime import datetime as _dt
                tr_info(f"STOP detected at {_dt.now().strftime('%H:%M:%S')} — "
                        f"draining {_seg_queue.qsize()} queued segment(s) before finalize")
                _recording_active.clear()
                _drain_system_audio_queue()
                # Flush remaining audio from system VAD buffer
                seg = vad.force_flush()
                if seg:
                    _enqueue(seg, "system")

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
                        _enqueue(mic_seg, "mic")

                # Wait for all pending transcription/correction before closing session
                sys_info("Waiting for transcriptions to complete...")
                _join_with_progress(_seg_queue, "transcription queue")
                sys_info("Waiting for corrections to complete...")
                _join_with_progress(_corr_queue, "correction queue")

                tr_info("finalize: queues drained → closing session "
                        "(converting audio, writing done signal)")
                _close_session(channels, sample_size, sample_rate)
                session_was_active = False
                continue

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        _recording_active.clear()
        _system_capture_stop.set()
        try:
            stream.stop_stream()
        except Exception:
            pass
        _system_capture_thread.join(timeout=3)
        _drain_system_audio_queue()

        # Flush remaining audio from system VAD
        seg = vad.force_flush()
        if seg:
            _enqueue(seg, "system")

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
                _enqueue(mic_seg, "mic")
        _mic_stop.set()
        if _mic_thread is not None:
            _mic_thread.join(timeout=3)

        _system_audio_queue.put(None)
        _system_audio_queue.join()
        _system_vad_thread.join(timeout=3)

        # Stop background thread
        _seg_queue.put(None)
        _seg_queue.join()
        _worker.join(timeout=60)
        _corr_queue.put(None)
        _corr_queue.join()
        _corr_worker.join(timeout=60)

        if session_was_active:
            _corr_queue.join()
            _close_session(channels, sample_size, sample_rate)

        # Wait for any in-progress MP3 conversions to finish
        for _ct in _conv_threads:
            if _ct.is_alive():
                tr_info("Waiting for MP3 conversion to complete...")
                _ct.join(timeout=120)

        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
        pa.terminate()
        sys_info("pipeline stopped")

    for f in (SIGNAL_STOP, MIC_ONAIR, SIGNAL_READY):
        try:
            os.remove(f)
        except Exception:
            pass


def _append_transcript(text: str, ts: str = ""):
    """Append transcribed text to the transcript file recorded in STATE_FILE."""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            path = f.read().strip()
        if path and os.path.exists(os.path.dirname(path)):
            from datetime import datetime
            ts = ts or datetime.now().strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8-sig") as f:
                f.write(f"[{ts}] {text}\n")
    except Exception as e:
        tr_error(f"transcript append error: {e}")


def _append_corrected(text: str, path: str, ts: str = ""):
    """Append corrected text to the corrected file and update CORRECTED_STATE.

    *ts* should be the transcription-time stamp so the corrected line keeps the
    original time rather than the (later) time the correction finished."""
    try:
        from datetime import datetime
        ts = ts or datetime.now().strftime("%H:%M:%S")
        with open(path, "a", encoding="utf-8-sig") as f:
            f.write(f"[{ts}] {text}\n")
        # Touch CORRECTED_STATE so the presenter's polling thread detects the update
        with open(CORRECTED_STATE, "w", encoding="utf-8") as f:
            f.write(path)
    except Exception as e:
        tr_error(f"corrected append error: {e}")


if __name__ == "__main__":
    run()

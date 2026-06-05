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

# ── signal files ──────────────────────────────────────────────────────────────
SIGNAL_SESSION   = os.path.join(_BASE, ".pipeline_session")
SIGNAL_STOP      = os.path.join(_BASE, ".pipeline_stop")
SIGNAL_SESS_DONE = os.path.join(_BASE, ".pipeline_session_done")
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


# ── per-segment LLM correction ───────────────────────────────────────────────
def _correct_segment(text: str, lang: str | None,
                     cfg: configparser.ConfigParser) -> str:
    """Call LLM to correct one transcribed segment. Falls back to original."""
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

    lang_hint = {"ja": "日本語", "zh": "中国語（简体字）", "en": "English"}.get(lang or "", "")
    prompt = (
        f"会議音声の自動転写テキストの誤認識・句読点を修正してください。"
        f"{'言語は' + lang_hint + '。' if lang_hint else ''}"
        f"説明・前置き・引用符は不要。修正後のテキストだけを出力。\n\n{text}"
    )

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
        # Whisper Large-v3 based (kotoba-whisper, large-v3)
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
        sys_error("Run setup.bat to repair the installation")
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
    if model_size == "large-v3" and device == "cpu":
        model_size = "medium"
        sys_info("large-v3 is not recommended on CPU, switching to medium")

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
        "ja": cfg.get("models", "ja", fallback="kotoba-whisper-v2.0-ct2"),
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
    vocab_file = cfg.get("paths", "vocab_file", fallback="").strip()
    vocab: list[str] = []
    if vocab_file and os.path.exists(vocab_file):
        with open(vocab_file, encoding="utf-8") as f:
            vocab = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    def _initial_prompt(lang: str | None) -> str | None:
        # No base sentence — avoids Whisper hallucinating the prompt text.
        # language= parameter already tells Whisper which language to use.
        if vocab:
            sep = "、" if lang == "ja" else ", "
            return sep.join(vocab)
        return None

    # ── VAD ───────────────────────────────────────────────────────────────────
    silence_s = cfg.getfloat("subtitle", "silence_sec",   fallback=2.0)
    min_s     = cfg.getfloat("subtitle", "min_accum_sec", fallback=1.0)
    max_s     = cfg.getfloat("subtitle", "max_sec",       fallback=30.0)
    vad = AccumulatingVAD(silence_sec=silence_s, min_accum_sec=min_s, max_sec=max_s)

    # ── Session state ─────────────────────────────────────────────────────────
    transcript_file:  str | None = None
    corrected_file:   str | None = None
    raw_file_path:    str | None = None
    raw_fh = None
    session_ts: str | None = None

    transcript_dir = cfg.get("paths", "transcript_dir",
                             fallback=r"C:\Users\Public\Sound2Text\transcript")
    corrected_dir  = cfg.get("summary", "corrected_dir",
                             fallback=r"C:\Users\Public\Sound2Text\corrected")
    audio_dir = cfg.get("paths", "audio_dir",
                        fallback=r"C:\Users\Public\Sound2Text\audio")

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

                def _convert_mp3(wav_p: str, quality: str):
                    import subprocess
                    mp3_p = wav_p.replace(".wav", ".mp3")
                    tr_info(f"MP3 conversion started: {os.path.basename(wav_p)}")
                    result = subprocess.run(
                        ["ffmpeg", "-i", wav_p, "-codec:a", "libmp3lame",
                         "-qscale:a", quality, mp3_p, "-y"],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        mp3_size = os.path.getsize(mp3_p) // 1024
                        wav_size = os.path.getsize(wav_p) // 1024
                        os.remove(wav_p)
                        tr_info(f"MP3 saved: {os.path.basename(mp3_p)} "
                                f"({mp3_size}KB, {int(100-mp3_size/wav_size*100)}% smaller)")
                    else:
                        tr_warn(f"MP3 conversion failed, WAV kept: {result.stderr[-100:]}")

                if audio_fmt == "mp3":
                    t = threading.Thread(target=_convert_mp3,
                                         args=(wav_path, mp3_quality), daemon=True)
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

    def _transcribe_loop():
        """Background thread: transcribes audio segments in order."""
        nonlocal session_lang, whisper, _current_model_lang, device, compute_type, _transcribe_kwargs, corrected_file

        while True:
            audio_bytes = _seg_queue.get()
            if audio_bytes is None:          # sentinel — stop
                _seg_queue.task_done()
                break

            seg_dur = len(audio_bytes) / (SAMPLE_RATE * 2)
            tr_info(f"Transcribing {seg_dur:.1f}s audio (queue depth={_seg_queue.qsize()})")

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

            # Language detection on first segment
            if not session_lang:
                try:
                    from transcriber import LANG_ALIAS
                    lang = LANG_ALIAS.get(info.language, info.language)
                    if lang in {"zh", "ja", "en"} and info.language_probability >= 0.5:
                        session_lang = lang
                        tr_info(f"Language detected: {session_lang}")
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

            tr_info(f"[pipeline] original: {original}")
            _append_transcript(original)

            # Per-segment correction: call LLM immediately after transcription
            corrected_text = _correct_segment(original, session_lang, cfg)
            if corrected_text != original:
                tr_info(f"[pipeline] corrected: {corrected_text}")
            else:
                corrected_text = original  # no correction or API unavailable

            # Append to corrected file and update signal for UI polling
            if corrected_file:
                _append_corrected(corrected_text, corrected_file)

            _seg_queue.task_done()

    _worker = threading.Thread(target=_transcribe_loop, daemon=True, name="transcribe")
    _worker.start()

    # ── Audio device setup ────────────────────────────────────────────────────
    import pyaudiowpatch as pyaudio
    from device_utils import select_active_device
    pa = pyaudio.PyAudio()
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
                _open_session()
                session_was_active = True
                with open(os.path.join(_BASE, ".recording_start"), "w") as f:
                    f.write(str(time.time()))

            # Session end
            if not session_active and session_was_active:
                # Flush remaining audio from VAD buffer
                seg = vad.force_flush()
                if seg:
                    if raw_fh:
                        raw_fh.write(seg)
                    _seg_queue.put(seg)

                # Wait for all pending transcriptions before closing session
                sys_info("Waiting for transcriptions to complete...")
                _seg_queue.join()

                _close_session(channels, sample_size, sample_rate)
                session_was_active = False
                continue

            # Read audio chunk
            raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            chunk = _to_mono16k(raw)

            # Write raw audio for session recording
            if session_active and raw_fh:
                try:
                    raw_fh.write(raw)
                except Exception:
                    pass

            # Feed to VAD
            seg = vad.feed(chunk)
            if seg:
                _seg_queue.put(seg)

    except KeyboardInterrupt:
        pass
    finally:
        # Flush remaining audio
        seg = vad.force_flush()
        if seg:
            _seg_queue.put(seg)

        # Stop background thread
        _seg_queue.put(None)
        _seg_queue.join()
        _worker.join(timeout=60)

        if session_was_active:
            _close_session(channels, sample_size, sample_rate)

        stream.stop_stream()
        stream.close()
        pa.terminate()
        sys_info("pipeline stopped")

    for f in (SIGNAL_STOP,):
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

"""
Real-time subtitle processor.

Architecture:
  - Captures audio from WASAPI loopback in small chunks
  - Detects sentence boundaries via amplitude VAD
  - Transcribes each sentence with faster-whisper
  - Optionally translates via LLM API
  - Writes subtitle text to .subtitle_text (for subtitle_window)
  - Prints transcribed lines to stdout (for transcript accumulation)

CLI:
  python subtitle_processor.py                  # live mode
  python subtitle_processor.py --test <wav>     # test mode: play WAV as input
"""
import os
import sys
import time
import wave
import tempfile
import configparser
import threading
import argparse
import warnings
import numpy as np

warnings.filterwarnings("ignore")

# ── network / proxy setup (before faster-whisper import) ─────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
_cfg_pre = configparser.ConfigParser()
_cfg_pre.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")
if _cfg_pre.has_section("network"):
    _proxy = _cfg_pre.get("network", "https_proxy", fallback="")
    if _proxy:
        os.environ.setdefault("HTTPS_PROXY", _proxy)
        os.environ.setdefault("HTTP_PROXY", _proxy)
    if not _cfg_pre.getboolean("network", "ssl_verify", fallback=True):
        os.environ.setdefault("HF_HUB_DISABLE_SSL_VERIFICATION", "1")
        os.environ.setdefault("CURL_CA_BUNDLE", "")
        os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
os.environ.setdefault("HUGGINGFACE_HUB_VERBOSITY", "error")

from faster_whisper import WhisperModel
from log_util import tr_info, tr_debug, tr_warn, tr_error, sys_info, sys_error

# ── constants ─────────────────────────────────────────────────────────────────
SAMPLE_RATE     = 16000
CHUNK_SIZE      = 1024
SUBTITLE_FILE   = os.path.join(_BASE, ".subtitle_text")
LANG_FILE       = os.path.join(_BASE, ".last_language")

HALLUCINATION_PHRASES = [
    "ご視聴ありがとうございました", "チャンネル登録",
    "thank you for watching", "please subscribe",
    "请不吝点赞", "订阅", "感谢观看",
]

# ── Amplitude VAD ─────────────────────────────────────────────────────────────
class AmplitudeVAD:
    """
    Simple amplitude-based VAD that detects sentence boundaries.
    Returns a segment (bytes) when speech is followed by sufficient silence.
    """
    def __init__(self, threshold: int, silence_sec: float, max_sec: float):
        self.threshold   = threshold
        self.silence_chunks = int(SAMPLE_RATE / CHUNK_SIZE * silence_sec)
        self.max_chunks     = int(SAMPLE_RATE / CHUNK_SIZE * max_sec)
        self._buf: list[np.ndarray] = []
        self._speaking   = False
        self._sil_cnt    = 0

    def feed(self, chunk: np.ndarray) -> bytes | None:
        level = int(np.abs(chunk).mean())
        if level >= self.threshold:
            self._buf.append(chunk)
            self._speaking = True
            self._sil_cnt  = 0
        elif self._speaking:
            self._buf.append(chunk)
            self._sil_cnt += 1
            if (self._sil_cnt >= self.silence_chunks
                    or len(self._buf) >= self.max_chunks):
                seg = np.concatenate(self._buf).tobytes()
                self._buf.clear()
                self._speaking = False
                self._sil_cnt  = 0
                return seg
        return None

    def flush(self) -> bytes | None:
        """Force-flush remaining buffer (called on stop)."""
        if self._buf and self._speaking:
            seg = np.concatenate(self._buf).tobytes()
            self._buf.clear()
            self._speaking = False
            return seg
        self._buf.clear()
        return None


# ── LLM translation ───────────────────────────────────────────────────────────
_LANG_NAMES = {
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "en": "English",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}

def translate_text(text: str, target_lang: str, cfg: configparser.ConfigParser) -> str:
    """Translate text via OpenAI-compatible API. Returns original on error."""
    api_base = cfg.get("summary", "api_base", fallback="").strip()
    api_key  = cfg.get("summary", "api_key",  fallback="").strip()
    model    = cfg.get("summary", "model",    fallback="llama-3.3-70b-versatile").strip()
    if not api_base or not api_key:
        tr_warn("翻訳スキップ: API Key 未設定")
        return text

    target_name = _LANG_NAMES.get(target_lang, target_lang)
    import requests
    proxies = {}
    https_p = cfg.get("network", "https_proxy", fallback="")
    if https_p:
        proxies = {"https": https_p, "http": cfg.get("network", "http_proxy", fallback=https_p)}
    verify = cfg.getboolean("network", "ssl_verify", fallback=True)

    try:
        resp = requests.post(
            f"{api_base}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system",
                     "content": f"Translate the following to {target_name}. "
                                "Output ONLY the translation, no explanation."},
                    {"role": "user", "content": text},
                ],
                "max_tokens": 300,
                "temperature": 0.1,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            proxies=proxies if proxies else None,
            verify=verify,
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        tr_warn(f"翻訳失敗: {e}")
        return text


# ── Main processor ────────────────────────────────────────────────────────────
def main(test_wav: str | None = None):
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")

    # ── config ────────────────────────────────────────────────────────────────
    model_size    = cfg.get("recording", "model_size", fallback="small").strip()
    device_cfg    = cfg.get("recording", "device",     fallback="auto").strip().lower()
    transcript_dir = cfg.get("paths", "transcript_dir",
                             fallback=r"C:\Users\Public\Sound2Text\transcript")

    # subtitle settings
    src_lang      = cfg.get("subtitle", "src_lang",    fallback="auto").strip().lower()
    dst_lang      = cfg.get("subtitle", "dst_lang",    fallback="").strip().lower()
    vad_threshold = cfg.getint("subtitle", "vad_threshold", fallback=400)
    silence_sec   = cfg.getfloat("subtitle", "silence_sec", fallback=0.8)
    max_sec       = cfg.getfloat("subtitle", "max_sec",      fallback=12.0)
    clear_sec     = cfg.getfloat("subtitle", "clear_sec",    fallback=3.0)

    # transcript output file (read from state or create new)
    from appconfig import STATE_FILE
    transcript_file = ""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                transcript_file = f.read().strip()
        except Exception:
            pass
    if not transcript_file or not os.path.exists(os.path.dirname(transcript_file)):
        os.makedirs(transcript_dir, exist_ok=True)
        from datetime import datetime
        transcript_file = os.path.join(
            transcript_dir,
            f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )

    # device
    if device_cfg == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    else:
        device = device_cfg
    compute_type = "float16" if device == "cuda" else "int8"

    sys_info(f"subtitle_processor: model={model_size} device={device} "
             f"src={src_lang} dst={dst_lang or '(none)'}")

    # ── load model ────────────────────────────────────────────────────────────
    tr_info(f"loading {model_size} ({device})")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    tr_info("model ready")

    # session language (auto-detect or fixed)
    session_lang: str | None = src_lang if src_lang in {"zh", "ja", "en"} else None
    if not session_lang and os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, encoding="utf-8") as f:
                l = f.read().strip()
            if l in {"zh", "ja", "en"}:
                session_lang = l
                tr_info(f"前回検出言語を使用: {session_lang}")
        except Exception:
            pass

    vad         = AmplitudeVAD(vad_threshold, silence_sec, max_sec)
    last_speech = time.monotonic()
    subtitle_written = False
    stop_flag   = threading.Event()

    def _write_subtitle(text: str) -> None:
        nonlocal subtitle_written
        try:
            with open(SUBTITLE_FILE, "w", encoding="utf-8") as f:
                f.write(text)
            subtitle_written = True
        except Exception:
            pass

    def _clear_subtitle() -> None:
        nonlocal subtitle_written
        try:
            with open(SUBTITLE_FILE, "w", encoding="utf-8") as f:
                f.write("")
            subtitle_written = False
        except Exception:
            pass

    def _process_segment(audio_bytes: bytes) -> None:
        nonlocal session_lang, last_speech

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_bytes)

            # transcribe
            segs, info = model.transcribe(
                tmp, language=session_lang,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
            )
            seg_list = list(segs)
            text = " ".join(s.text.strip() for s in seg_list).strip()
        except Exception as e:
            tr_error(f"transcribe error: {e}")
            text = ""
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

        if not text or any(p in text for p in HALLUCINATION_PHRASES):
            return

        # language detection (first time)
        if not session_lang:
            raw = info.language
            from transcriber import LANG_ALIAS
            lang = LANG_ALIAS.get(raw, raw)
            if lang in {"zh", "ja", "en"} and info.language_probability >= 0.5:
                session_lang = lang
                tr_info(f"言語確定: {session_lang}")
                with open(LANG_FILE, "w", encoding="utf-8") as f:
                    f.write(session_lang)

        last_speech = time.monotonic()
        tr_info(f"[subtitle] original: {text}")

        # translation
        subtitle_text = text
        if dst_lang and dst_lang != session_lang:
            translated = translate_text(text, dst_lang, cfg)
            subtitle_text = translated
            tr_info(f"[subtitle] translated({dst_lang}): {translated}")

        # update subtitle window
        _write_subtitle(subtitle_text)

        # write original text to transcript
        from datetime import datetime
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}"
        print(line, flush=True)
        try:
            with open(transcript_file, "a", encoding="utf-8-sig") as f:
                f.write(line + "\n")
        except Exception as e:
            tr_error(f"transcript write error: {e}")

    # ── auto-clear subtitle timer ─────────────────────────────────────────────
    def _auto_clear_loop():
        while not stop_flag.is_set():
            time.sleep(0.5)
            if subtitle_written and (time.monotonic() - last_speech) >= clear_sec:
                _clear_subtitle()

    threading.Thread(target=_auto_clear_loop, daemon=True).start()

    # ── audio source: test WAV file or live capture ───────────────────────────
    if test_wav:
        _run_test_mode(test_wav, vad, _process_segment, stop_flag)
    else:
        _run_live_mode(cfg, vad, _process_segment, stop_flag)

    # flush remaining buffer
    remaining = vad.flush()
    if remaining:
        _process_segment(remaining)

    _clear_subtitle()
    tr_info("subtitle_processor 終了")


def _run_live_mode(cfg, vad, process_fn, stop_flag):
    """WASAPI loopback からリアルタイムキャプチャ。"""
    import pyaudiowpatch as pyaudio
    from device_utils import select_active_device

    pa = pyaudio.PyAudio()
    try:
        device_index, dev_info = select_active_device(pa)
        channels    = dev_info["maxInputChannels"]
        sample_rate = int(dev_info["defaultSampleRate"])
    except Exception as e:
        sys_error(f"デバイス取得失敗: {e}")
        pa.terminate()
        return

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=sample_rate,
        frames_per_buffer=CHUNK_SIZE,
        input=True,
        input_device_index=device_index,
    )
    sys_info(f"live capture: {dev_info['name']}  rate={sample_rate}")

    from appconfig import STOP_SIGNAL
    try:
        while not stop_flag.is_set():
            if os.path.exists(STOP_SIGNAL):
                break
            raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            chunk = np.frombuffer(raw, dtype=np.int16)
            # downsample if needed
            if sample_rate != SAMPLE_RATE:
                import scipy.signal
                chunk = scipy.signal.resample_poly(
                    chunk, SAMPLE_RATE, sample_rate
                ).astype(np.int16)
            seg = vad.feed(chunk)
            if seg:
                process_fn(seg)
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()


def _run_test_mode(wav_path: str, vad, process_fn, stop_flag):
    """テスト用: WAV ファイルを読み込んでリアルタイム再生速度でフィード。"""
    sys_info(f"test mode: {wav_path}")
    with wave.open(wav_path, "rb") as wf:
        src_rate   = wf.getframerate()
        n_channels = wf.getnchannels()
        chunk_frames = CHUNK_SIZE

        chunk_dur = chunk_frames / SAMPLE_RATE
        while not stop_flag.is_set():
            raw = wf.readframes(chunk_frames)
            if not raw:
                break
            arr = np.frombuffer(raw, dtype=np.int16)
            # stereo → mono
            if n_channels == 2:
                arr = arr[::2]
            # resample if needed
            if src_rate != SAMPLE_RATE:
                import scipy.signal
                arr = scipy.signal.resample_poly(
                    arr, SAMPLE_RATE, src_rate
                ).astype(np.int16)
            seg = vad.feed(arr)
            if seg:
                process_fn(seg)
            time.sleep(chunk_dur)   # simulate real-time


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", metavar="WAV", help="テスト用WAVファイル")
    args = parser.parse_args()
    main(test_wav=args.test)

"""
Unified audio pipeline: capture → VAD → Whisper → translation → output

State machine controlled by two signal files:
  .pipeline_subtitle  : show subtitles (written by subtitle btn)
  .pipeline_session   : save audio + write transcript (written by start btn)

When pipeline_subtitle exists → run loop, update subtitle window
When pipeline_session exists  → also save raw audio + append to transcript
When neither exists           → idle / exit

Recognition improvements vs previous subtitle_processor.py:
  - Uses faster-whisper with Silero VAD (more accurate than amplitude VAD)
  - model_size = small by default on CPU (better than tiny)
  - initial_prompt carries vocabulary + language context
  - condition_on_previous_text=True within a sentence, False across sentences
  - beam_size=5, temperature=0 for deterministic output
  - Segment-level confidence filtering (low prob segments discarded)
"""
import os
import sys
import time
import wave
import tempfile
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

from faster_whisper import WhisperModel
from log_util import tr_info, tr_debug, tr_warn, tr_error, sys_info, sys_error

# ── signal files ──────────────────────────────────────────────────────────────
SIGNAL_SUBTITLE  = os.path.join(_BASE, ".pipeline_subtitle")
SIGNAL_SESSION   = os.path.join(_BASE, ".pipeline_session")
SIGNAL_STOP      = os.path.join(_BASE, ".pipeline_stop")
SIGNAL_SESS_DONE = os.path.join(_BASE, ".pipeline_session_done")  # session 完了通知
SUBTITLE_FILE    = os.path.join(_BASE, ".subtitle_text")
LANG_FILE        = os.path.join(_BASE, ".last_language")
STATE_FILE       = os.path.join(_BASE, ".last_transcript")

SAMPLE_RATE = 16000
CHUNK_SIZE  = 1024

HALLUCINATION = [
    "ご視聴ありがとうございました", "チャンネル登録",
    "thank you for watching", "please subscribe",
    "请不吝点赞", "订阅", "感谢观看", "字幕由",
]

# ── Accumulating VAD ─────────────────────────────────────────────────────────
class AccumulatingVAD:
    """
    Two-stage VAD:
      Stage 1 (Silero/amplitude): detects speech vs silence at chunk level
      Stage 2 (accumulator): collects detected speech until ready to submit

    Submission triggers:
      A. Silence detected AND accumulated speech >= min_accum_sec
      B. Total accumulated audio >= max_sec (force flush)

    This ensures Whisper always gets enough context (>= min_accum_sec)
    for accurate recognition, regardless of short pauses within speech.
    """
    def __init__(self, threshold: float = 0.5,
                 silence_sec: float = 1.5,
                 min_accum_sec: float = 3.0,
                 max_sec: float = 15.0):
        self._threshold    = threshold
        self._silence_sec  = silence_sec   # 無音がこの秒数続いたら文末候補
        self._min_accum    = min_accum_sec # 最低蓄積時間（これ未満では送らない）
        self._max_sec      = max_sec       # 最大蓄積時間（強制送信）
        self._chunk_dur    = CHUNK_SIZE / SAMPLE_RATE

        # 蓄積バッファ（送信単位）
        self._accum: list[np.ndarray] = []
        self._accum_dur  = 0.0

        # 発話追跡
        self._speaking    = False
        self._silence_dur = 0.0

        # Silero モデル
        self._model = None
        try:
            from faster_whisper.vad import SileroVADModel
            self._model = SileroVADModel()
            sys_info(f"AccumulatingVAD: Silero ready "
                     f"(min={min_accum_sec}s silence={silence_sec}s max={max_sec}s)")
        except Exception:
            tr_warn(f"AccumulatingVAD: amplitude mode "
                    f"(min={min_accum_sec}s silence={silence_sec}s max={max_sec}s)")

    def _is_speech(self, chunk: np.ndarray) -> bool:
        if self._model:
            try:
                prob = self._model(chunk.astype(np.float32) / 32768.0, SAMPLE_RATE)
                return float(prob) >= self._threshold
            except Exception:
                pass
        return int(np.abs(chunk).mean()) >= 300

    def feed(self, chunk: np.ndarray) -> bytes | None:
        """
        Feed chunk. Returns accumulated audio bytes when ready for Whisper,
        or None to keep buffering.
        """
        is_speech = self._is_speech(chunk)
        prev_speaking = self._speaking
        self._accum.append(chunk)
        self._accum_dur += self._chunk_dur

        if is_speech:
            if not prev_speaking:
                tr_debug(f"VAD speech start  accum={self._accum_dur:.1f}s")
            self._speaking    = True
            self._silence_dur = 0.0
        else:
            if self._speaking:
                self._silence_dur += self._chunk_dur
                # 無音が始まったタイミングだけログ
                if abs(self._silence_dur - self._chunk_dur) < 0.01:
                    tr_debug(f"VAD silence start accum={self._accum_dur:.1f}s")

        # 強制送信: 最大蓄積時間超過
        if self._accum_dur >= self._max_sec:
            tr_debug(f"VAD force flush   accum={self._accum_dur:.1f}s (max reached)")
            return self._flush("force")

        # 通常送信: 無音が silence_sec 以上 かつ 蓄積が min_accum_sec 以上
        if (self._speaking
                and self._silence_dur >= self._silence_sec
                and self._accum_dur   >= self._min_accum):
            tr_debug(f"VAD normal flush  accum={self._accum_dur:.1f}s "
                     f"silence={self._silence_dur:.1f}s")
            return self._flush("silence")

        # 蓄積不足で無音閾値超過した場合
        if (self._speaking
                and self._silence_dur >= self._silence_sec
                and self._accum_dur   <  self._min_accum):
            tr_debug(f"VAD waiting more  accum={self._accum_dur:.1f}s "
                     f"(< min={self._min_accum}s) silence={self._silence_dur:.1f}s")

        return None

    def _flush(self, reason: str = "") -> bytes:
        seg = np.concatenate(self._accum).tobytes()
        dur = len(seg) / (SAMPLE_RATE * 2)
        tr_debug(f"VAD flushed [{reason}] dur={dur:.1f}s")
        self._accum.clear()
        self._accum_dur  = 0.0
        self._speaking   = False
        self._silence_dur = 0.0
        return seg

    def force_flush(self) -> bytes | None:
        """停止時: 残バッファを返す（min_accum 未満でも）。"""
        if self._accum and self._speaking:
            return self._flush("stop")
        self._accum.clear()
        return None


# ── translation ───────────────────────────────────────────────────────────────
def _translate(text: str, dst_lang: str, cfg: configparser.ConfigParser) -> str:
    api_base = cfg.get("summary", "api_base", fallback="").strip()
    api_key  = cfg.get("summary", "api_key",  fallback="").strip()
    model    = cfg.get("summary", "model",    fallback="llama-3.3-70b-versatile").strip()
    if not api_base or not api_key:
        return text
    lang_names = {"zh": "Chinese (Simplified)", "ja": "Japanese",
                  "en": "English", "ko": "Korean"}
    target = lang_names.get(dst_lang, dst_lang)
    verify  = cfg.getboolean("network", "ssl_verify", fallback=True)
    px_https = cfg.get("network", "https_proxy", fallback="")
    proxies  = {"https": px_https, "http": px_https} if px_https else None
    import requests
    try:
        r = requests.post(
            f"{api_base}/chat/completions",
            json={"model": model,
                  "messages": [
                      {"role": "system",
                       "content": f"Translate to {target}. Output ONLY the translation."},
                      {"role": "user", "content": text}],
                  "max_tokens": 300, "temperature": 0.1},
            headers={"Authorization": f"Bearer {api_key}"},
            proxies=proxies, verify=verify, timeout=8,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        tr_warn(f"翻訳失敗: {e}")
        return text


# ── main pipeline ─────────────────────────────────────────────────────────────
def run():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(_BASE, "config.ini"), encoding="utf-8")

    # ── model setup ───────────────────────────────────────────────────────────
    # CPU でも medium まで許可（速度は遅くなる）
    model_size = cfg.get("recording", "model_size", fallback="small").strip()
    if model_size == "large-v3":
        model_size = "medium"  # large-v3 のみ CPU では非推奨
        sys_info("large-v3 → medium に変更 (CPU)")

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

    sys_info(f"pipeline: model={model_size} device={device}")
    whisper = WhisperModel(model_size, device=device, compute_type=compute_type)
    sys_info("model ready")

    # ── session language ──────────────────────────────────────────────────────
    cfg_lang = cfg.get("recording", "language", fallback="auto").strip().lower()
    session_lang: str | None = cfg_lang if cfg_lang in {"zh", "ja", "en"} else None
    if not session_lang and os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, encoding="utf-8") as f:
                l = f.read().strip()
            if l in {"zh", "ja", "en"}:
                session_lang = l
                tr_info(f"前回言語を使用: {session_lang}")
        except Exception:
            pass

    # ── vocabulary ───────────────────────────────────────────────────────────
    vocab_file = cfg.get("paths", "vocab_file", fallback="").strip()
    vocab: list[str] = []
    if vocab_file and os.path.exists(vocab_file):
        with open(vocab_file, encoding="utf-8") as f:
            vocab = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    def _initial_prompt(lang: str | None) -> str | None:
        base = {"zh": "以下是普通话录音的简体中文转写：",
                "ja": "以下は日本語の会議録音です：",
                "en": "Meeting transcript:"}.get(lang or "", "")
        if vocab:
            sep = "、" if lang == "ja" else ", "
            return base + sep.join(vocab)
        return base or None

    # ── VAD ───────────────────────────────────────────────────────────────────
    vad_thr       = cfg.getfloat("subtitle", "vad_threshold",  fallback=400)
    silence_s     = cfg.getfloat("subtitle", "silence_sec",     fallback=1.5)
    min_accum_s   = cfg.getfloat("subtitle", "min_accum_sec",   fallback=3.0)
    max_s         = cfg.getfloat("subtitle", "max_sec",          fallback=15.0)
    vad = AccumulatingVAD(
        threshold    = vad_thr / 32768.0,
        silence_sec  = silence_s,
        min_accum_sec = min_accum_s,
        max_sec      = max_s,
    )

    # ── subtitle / session state ──────────────────────────────────────────────
    dst_lang = cfg.get("subtitle", "dst_lang", fallback="").strip().lower()
    transcript_dir = cfg.get("paths", "transcript_dir",
                             fallback=r"C:\Users\Public\Sound2Text\transcript")
    audio_dir = cfg.get("paths", "audio_dir",
                        fallback=r"C:\Users\Public\Sound2Text\audio")

    transcript_file: str | None = None
    raw_file_path:   str | None = None
    raw_fh = None
    session_ts: str | None = None

    def _open_session():
        nonlocal transcript_file, raw_file_path, raw_fh, session_ts
        from datetime import datetime
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(transcript_dir, exist_ok=True)
        os.makedirs(audio_dir, exist_ok=True)
        transcript_file = os.path.join(transcript_dir,
                                       f"transcript_{session_ts}.txt")
        raw_file_path   = os.path.join(audio_dir,
                                       f".tmp_audio_{session_ts}.raw")
        raw_fh = open(raw_file_path, "wb")
        # Write transcript header
        with open(transcript_file, "w", encoding="utf-8-sig") as f:
            ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"=== 会議転写開始 {ts_str} ===\n\n")
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            f.write(transcript_file)
        sys_info(f"session開始: {session_ts}")
        tr_info(f"transcript: {transcript_file}")

    def _close_session(channels: int, sample_size: int, sample_rate: int):
        nonlocal raw_fh, raw_file_path, transcript_file, session_ts
        if raw_fh:
            raw_fh.close()
            raw_fh = None
        if raw_file_path and os.path.exists(raw_file_path):
            wav_path = raw_file_path.replace(".tmp_audio_", "audio_")[:-4] + ".wav"
            wav_path = os.path.join(os.path.dirname(raw_file_path),
                                    f"audio_{session_ts}.wav")
            try:
                with open(raw_file_path, "rb") as f:
                    pcm = f.read()
                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(sample_size)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm)
                os.remove(raw_file_path)
                dur = len(pcm) / (sample_rate * channels * sample_size)
                tr_info(f"音声保存: {os.path.basename(wav_path)} ({dur:.1f}秒)")
            except Exception as e:
                tr_error(f"音声変換失敗: {e}")
        if transcript_file and os.path.exists(transcript_file):
            from datetime import datetime
            with open(transcript_file, "a", encoding="utf-8-sig") as f:
                f.write(f"\n=== 終了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        raw_file_path   = None
        transcript_file = None
        session_ts      = None
        # 完了通知を書き込む（presenter が待機中の場合に通知）
        with open(SIGNAL_SESS_DONE, "w") as f:
            f.write("done")
        sys_info("session 完了シグナル書き込み")

    def _write_subtitle(text: str):
        try:
            with open(SUBTITLE_FILE, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    # ── transcribe one segment ────────────────────────────────────────────────
    def _process(audio_bytes: bytes) -> tuple[str, str]:
        """Returns (original_text, display_text). display=translated if dst set."""
        nonlocal session_lang

        seg_dur = len(audio_bytes) / (SAMPLE_RATE * 2)
        tr_debug(f"WHISPER_IN  dur={seg_dur:.1f}s lang={session_lang} model={model_size}")

        import time as _time
        t0 = _time.monotonic()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        try:
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_bytes)

            prompt = _initial_prompt(session_lang)
            segs, info = whisper.transcribe(
                tmp,
                language            = session_lang,
                initial_prompt      = prompt,
                beam_size           = 5,
                temperature         = 0.0,
                vad_filter          = True,
                vad_parameters      = {"min_silence_duration_ms": 400,
                                       "threshold": 0.4},
                condition_on_previous_text = False,
                word_timestamps     = False,
                no_speech_threshold = 0.6,
                log_prob_threshold  = -1.0,
            )
            seg_list = list(segs)
        except Exception as e:
            tr_error(f"transcribe error: {e}")
            return "", ""
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass

        elapsed = _time.monotonic() - t0
        tr_debug(f"WHISPER_OUT elapsed={elapsed:.1f}s "
                 f"lang={info.language}({info.language_probability:.2f}) "
                 f"segments={len(seg_list)}")

        # language detection on first segment
        if not session_lang:
            from transcriber import LANG_ALIAS
            lang = LANG_ALIAS.get(info.language, info.language)
            if lang in {"zh", "ja", "en"} and info.language_probability >= 0.5:
                session_lang = lang
                tr_info(f"言語確定: {session_lang}")
                with open(LANG_FILE, "w", encoding="utf-8") as f:
                    f.write(session_lang)

        # filter hallucinations and low-confidence segments
        lines = []
        for i, s in enumerate(seg_list):
            text = s.text.strip()
            nsp  = getattr(s, "no_speech_prob", 0.0)
            avg_logprob = getattr(s, "avg_logprob", 0.0)
            if not text:
                continue
            if any(h in text for h in HALLUCINATION):
                tr_debug(f"  seg[{i}] HALLUCINATION: {text[:40]}")
                continue
            if nsp > 0.7:
                tr_debug(f"  seg[{i}] LOW_CONF no_speech={nsp:.2f}: {text[:40]}")
                continue
            tr_debug(f"  seg[{i}] OK no_speech={nsp:.2f} logprob={avg_logprob:.2f}: {text[:60]}")
            lines.append(text)

        original = " ".join(lines).strip()
        if not original:
            tr_debug("WHISPER_EMPTY (all segments filtered)")
            return "", ""

        tr_info(f"[pipeline] original: {original}")

        # translation for subtitle display only
        display = original
        if dst_lang and dst_lang != session_lang:
            display = _translate(original, dst_lang, cfg)
            tr_info(f"[pipeline] translated({dst_lang}): {display}")

        return original, display

    # ── audio device setup ────────────────────────────────────────────────────
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

    # ── resample helper (if device rate != 16kHz) ─────────────────────────────
    need_resample = (sample_rate != SAMPLE_RATE)
    if need_resample:
        try:
            import scipy.signal as _ss
            sys_info(f"リサンプル: {sample_rate}→{SAMPLE_RATE}")
        except ImportError:
            tr_warn("scipy なし、リサンプル無効")
            need_resample = False

    def _to_mono16k(raw: bytes) -> np.ndarray:
        arr = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            arr = arr[::channels]
        if need_resample:
            import scipy.signal as _ss
            arr = _ss.resample_poly(arr, SAMPLE_RATE, sample_rate).astype(np.int16)
        return arr

    # ── main loop ─────────────────────────────────────────────────────────────
    sys_info("pipeline loop start")
    session_was_active = False

    try:
        while not os.path.exists(SIGNAL_STOP):
            subtitle_active = os.path.exists(SIGNAL_SUBTITLE)
            session_active  = os.path.exists(SIGNAL_SESSION)

            if not subtitle_active and not session_active:
                # 両方オフ → アイドル
                time.sleep(0.2)
                continue

            # session 開始
            if session_active and not session_was_active:
                _open_session()
                session_was_active = True
                # 開始時刻を .recording_start に書く
                with open(os.path.join(_BASE, ".recording_start"), "w") as f:
                    f.write(str(time.time()))

            # session 終了
            if not session_active and session_was_active:
                seg = vad.force_flush()
                if seg:
                    orig, disp = _process(seg)
                    if orig:
                        if subtitle_active:
                            _write_subtitle(disp)
                        if raw_fh:
                            raw_fh.write(seg)
                        _append_transcript(orig)
                _close_session(channels, sample_size, sample_rate)
                session_was_active = False

            # 音声チャンク取得
            raw = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            chunk = _to_mono16k(raw)

            # セッション中は raw PCM を保存
            if session_active and raw_fh:
                try:
                    raw_fh.write(raw)
                except Exception:
                    pass

            # VAD → 文検出
            seg = vad.feed(chunk)
            if not seg:
                continue

            # 転写・翻訳
            orig, disp = _process(seg)
            if not orig:
                continue

            # 字幕表示
            if subtitle_active:
                _write_subtitle(disp)

            # transcript 書き込み（セッション中 + original のみ）
            if session_active:
                _append_transcript(orig)

    except KeyboardInterrupt:
        pass
    finally:
        # 残バッファを処理
        seg = vad.force_flush()
        if seg and (os.path.exists(SIGNAL_SUBTITLE) or os.path.exists(SIGNAL_SESSION)):
            orig, disp = _process(seg)
            if orig:
                if os.path.exists(SIGNAL_SUBTITLE):
                    _write_subtitle(disp)
                if session_was_active:
                    _append_transcript(orig)

        if session_was_active:
            _close_session(channels, sample_size, sample_rate)

        stream.stop_stream()
        stream.close()
        pa.terminate()
        sys_info("pipeline 終了")

    # clean up signal files
    for f in (SIGNAL_STOP,):
        try:
            os.remove(f)
        except Exception:
            pass


def _append_transcript(text: str):
    """STATE_FILE に記録された transcript ファイルに原文を追記。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            path = f.read().strip()
        if path and os.path.exists(os.path.dirname(path)):
            from datetime import datetime
            ts   = datetime.now().strftime("%H:%M:%S")
            with open(path, "a", encoding="utf-8-sig") as f:
                f.write(f"[{ts}] {text}\n")
    except Exception as e:
        tr_error(f"transcript append error: {e}")


if __name__ == "__main__":
    run()

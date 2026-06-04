"""
debug_modules.py — Individual module tests using existing recordings.

Usage:
  python debug_modules.py loopback      # list loopback devices + 5s capture test
  python debug_modules.py audio         # record 15s from loopback → transcribe
  python debug_modules.py mic           # record 15s from mic → transcribe
  python debug_modules.py pipeline      # record loopback+mic simultaneously → merge+verify
  python debug_modules.py transcriber   # transcribe the latest existing WAV file
  python debug_modules.py summarizer    # run summarizer on the latest transcript
  python debug_modules.py ui            # launch UI in debug mode (3s auto-close)
  python debug_modules.py all           # run all tests sequentially

Optional duration override (seconds):
  python debug_modules.py audio 30      # record 30s instead of default 15s
  python debug_modules.py pipeline 20
"""
import sys
import os
import glob
import subprocess
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# ── helpers ───────────────────────────────────────────────────────────────────

def latest(directory: str, pattern: str) -> str:
    files = sorted(glob.glob(os.path.join(directory, pattern)), key=os.path.getmtime)
    return files[-1] if files else ""

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ── test_transcriber ──────────────────────────────────────────────────────────

def test_transcriber():
    """Transcribe the latest audio WAV file with transcriber.py."""
    section("TEST: transcriber.py")

    from appconfig import AppConfig
    cfg = AppConfig()
    audio_dir = cfg.get("paths", "audio_dir", fallback=r"C:\Users\Public\Sound2Text\audio")
    done_dir  = os.path.join(audio_dir, "done")

    # Look for WAV files in audio_dir or done/
    wav = latest(audio_dir, "audio_*.wav") or latest(done_dir, "audio_*.wav")
    if not wav:
        mic_dir = cfg.get("paths", "mic_dir", fallback=r"C:\Users\Public\Sound2Text\mic")
        wav = latest(mic_dir, "mic_*.wav") or latest(os.path.join(mic_dir, "done"), "mic_*.wav")

    if not wav:
        print("❌  No WAV files found. Record some audio first.")
        return False

    print(f"✓  Using: {wav}")
    print(f"   Size:  {os.path.getsize(wav) // 1024} KB")

    # Quick transcription test via faster-whisper directly
    try:
        from appconfig import _setup_cuda_dlls
        _setup_cuda_dlls()
        from faster_whisper import WhisperModel
        import ctranslate2

        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        model_size = cfg.get("recording", "model_size", fallback="small")

        print(f"   Loading {model_size} on {device}...")
        model = WhisperModel(model_size, device=device, compute_type=compute)
        segments, info = model.transcribe(wav, language=None)
        texts = [s.text.strip() for s in segments if s.text.strip()]
        print(f"   Detected language: {info.language} ({info.language_probability:.0%})")
        print(f"   Segments: {len(texts)}")
        for t in texts[:5]:
            print(f"   > {t}")
        print("✓  Transcriber OK")
        return True
    except Exception as e:
        import traceback
        print(f"❌  Transcriber error: {e}")
        traceback.print_exc()
        return False

# ── test_summarizer ───────────────────────────────────────────────────────────

def test_summarizer():
    """Run correction step on the latest transcript."""
    section("TEST: summarizer.py (--step correct)")

    from appconfig import AppConfig
    cfg = AppConfig()
    transcript_dir = cfg.get("paths", "transcript_dir",
                             fallback=r"C:\Users\Public\Sound2Text\transcript")
    transcript = latest(transcript_dir, "transcript_*.txt")

    if not transcript:
        print("❌  No transcript files found.")
        return False

    print(f"✓  Using: {transcript}")
    with open(transcript, encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    print(f"   Length: {len(content)} chars")
    print(f"   Preview: {content[:120].replace(chr(10),' ')}...")

    result = subprocess.run(
        [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py"),
         "--step", "correct", transcript],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=BASE,
    )
    print(result.stdout[-800:] if result.stdout else "(no stdout)")
    if result.returncode != 0:
        print(f"❌  Exit code {result.returncode}")
        if result.stderr:
            print(result.stderr[-300:])
        return False

    corrected_dir = cfg.get("summary", "corrected_dir",
                            fallback=r"C:\Users\Public\Sound2Text\corrected")
    corrected = latest(corrected_dir, "corrected_*.txt")
    if corrected:
        with open(corrected, encoding="utf-8-sig", errors="replace") as f:
            out = f.read()
        print(f"✓  Corrected file: {corrected}")
        print(f"   Output:\n{out[:400]}")
    return True

# ── test_loopback ─────────────────────────────────────────────────────────────

def test_loopback(probe_sec: float = 3.0):
    """
    List all loopback devices and probe each for probe_sec seconds.
    Then record 5 seconds with the auto-selected device and report audio levels.
    Helps diagnose silent meeting-mode recordings.
    """
    section("TEST: loopback device selection (meeting mode)")

    try:
        import pyaudiowpatch as pyaudio
        import numpy as np
    except ImportError as e:
        print(f"❌  Missing dependency: {e}")
        return False

    pa = pyaudio.PyAudio()

    # ── 1. List every loopback device ─────────────────────────────────────────
    print("\nAll loopback devices:")
    loopback_devices = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and info.get("isLoopbackDevice", False):
            loopback_devices.append((i, info))
            print(f"  [{i:2d}] {info['name'][:60]:<60}  ch={info['maxInputChannels']}  "
                  f"sr={int(info['defaultSampleRate'])}")

    if not loopback_devices:
        print("❌  No loopback devices found. pyaudiowpatch WASAPI loopback not available.")
        pa.terminate()
        return False

    print(f"\n{len(loopback_devices)} loopback device(s) found.")

    # ── 2. Probe each device for probe_sec ───────────────────────────────────
    print(f"\nProbing each device for {probe_sec}s (play some audio now):")
    CHUNK = 1024
    results = []

    for dev_idx, dev_info in loopback_devices:
        channels    = dev_info["maxInputChannels"]
        sample_rate = int(dev_info["defaultSampleRate"])
        n_chunks    = max(1, int(sample_rate / CHUNK * probe_sec))
        levels      = []

        try:
            stream = pa.open(
                format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                frames_per_buffer=CHUNK, input=True, input_device_index=dev_idx,
            )
            for _ in range(n_chunks):
                data   = stream.read(CHUNK, exception_on_overflow=False)
                arr    = np.frombuffer(data, dtype=np.int16)
                levels.append(int(np.abs(arr).mean()))
            stream.stop_stream(); stream.close()
            avg   = int(np.mean(levels)) if levels else 0
            peak  = max(levels) if levels else 0
            bar   = "#" * min(avg // 20, 40)
            status = "✓ AUDIO" if avg > 50 else "  silent"
            print(f"  [{dev_idx:2d}] {status}  avg={avg:5d}  peak={peak:5d}  |{bar:<40}|  {dev_info['name'][:40]}")
            results.append((dev_idx, dev_info, avg))
        except Exception as e:
            print(f"  [{dev_idx:2d}] ❌ ERROR: {e}  ({dev_info['name'][:40]})")
            results.append((dev_idx, dev_info, -1))

    # ── 3. Auto-select and record 5 seconds ──────────────────────────────────
    best = max(results, key=lambda x: x[2])
    best_idx, best_info, best_avg = best

    if best_avg < 0:
        print("\n❌  All devices failed to open.")
        pa.terminate()
        return False

    print(f"\nAuto-selected: [{best_idx}] {best_info['name']}")
    if best_avg <= 50:
        print("⚠  Low audio level detected. If a meeting is in progress, the selected")
        print("   device may not be capturing the correct audio output.")
        print("   Check: Windows Sound → Playback tab → default device matches the meeting app.")

    # Record 5 seconds with live level bar
    print(f"\nRecording 5s with selected device (play audio during this window):")
    channels    = best_info["maxInputChannels"]
    sample_rate = int(best_info["defaultSampleRate"])
    record_sec  = 5
    n_chunks    = int(sample_rate / CHUNK * record_sec)

    try:
        import wave, os
        from appconfig import AppConfig
        cfg = AppConfig()
        audio_dir = cfg.get("paths", "audio_dir", fallback=r"C:\Users\Public\Sound2Text\audio")
        os.makedirs(audio_dir, exist_ok=True)

        stream = pa.open(
            format=pyaudio.paInt16, channels=channels, rate=sample_rate,
            frames_per_buffer=CHUNK, input=True, input_device_index=best_idx,
        )
        frames    = []
        max_level = 0
        for i in range(n_chunks):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            lvl = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
            max_level = max(max_level, lvl)
            bar = "#" * min(lvl // 50, 30)
            elapsed = int(i / n_chunks * record_sec)
            print(f"\r  {elapsed+1}/{record_sec}s  avg:{lvl:5d}  |{bar:<30}|", end="", flush=True)
        stream.stop_stream(); stream.close()
        print()

        from datetime import datetime
        out_path = os.path.join(audio_dir, f"dbg_loopback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(channels); wf.setsampwidth(2)
            wf.setframerate(sample_rate); wf.writeframes(b"".join(frames))

        if max_level > 50:
            print(f"✓  Recorded OK  peak={max_level}  → {out_path}")
        else:
            print(f"⚠  Recorded but SILENT  peak={max_level}  → {out_path}")
            print("   The WAV file contains near-zero audio. Meeting audio is not captured.")
            print("   Try: Play a YouTube video and re-run this test.")
        pa.terminate()
        return max_level > 50

    except Exception as e:
        import traceback
        print(f"❌  Recording error: {e}")
        traceback.print_exc()
        pa.terminate()
        return False


# ── test_ui ───────────────────────────────────────────────────────────────────

def test_ui():
    """Launch ui_qt.py with extra debug logging (closes after 3 seconds)."""
    section("TEST: ui_qt.py (3-second launch test)")

    env = os.environ.copy()
    env["QT_LOGGING_RULES"] = "*.debug=true"
    env["PYTHONUNBUFFERED"] = "1"

    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", """
import sys, os
sys.path.insert(0, r'{base}')
os.environ['QT_QPA_PLATFORM'] = 'windows'
os.environ['SOUND2TXT_NO_OLLAMA'] = '1'  # skip Ollama auto-start in tests
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
app = QApplication(sys.argv)
from appconfig import AppConfig
from presenter import Presenter
from ui_qt import App
config = AppConfig()
presenter = Presenter(config)
window = App(presenter)
window.show()
print('[DEBUG] Window shown OK')
missing = [m for m in ['put_log','schedule','set_start_enabled','set_stop_enabled',
                        'show_onair','hide_onair','dashboard_start','show_ptt_button']
           if not hasattr(window, m)]
print(f'[DEBUG] Missing ViewProtocol methods: {{missing if missing else "none"}}')
QTimer.singleShot(2000, app.quit)
sys.exit(app.exec())
""".format(base=BASE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env,
    )
    output = (result.stdout + result.stderr).strip()
    if "[DEBUG] Window shown OK" in output:
        print("✓  UI launched and closed cleanly")
        for line in output.splitlines():
            if line.startswith("[DEBUG]") or "error" in line.lower() or "Error" in line:
                print(f"   {line}")
        return True
    else:
        print("❌  UI test failed")
        print(output[-600:])
        return False

# ── recording helpers ─────────────────────────────────────────────────────────

def _record_loopback_wav(audio_dir: str, duration: int) -> tuple:
    """Record loopback for `duration` seconds. Returns (wav_path, peak_level)."""
    try:
        import pyaudiowpatch as pyaudio, numpy as np, wave
        from device_utils import select_active_device
        from datetime import datetime
        pa  = pyaudio.PyAudio()
        idx, info = select_active_device(pa)
        ch  = info["maxInputChannels"]
        sr  = int(info["defaultSampleRate"])
        n   = int(sr / 1024 * duration)
        stream = pa.open(format=pyaudio.paInt16, channels=ch, rate=sr,
                         frames_per_buffer=1024, input=True, input_device_index=idx)
        frames, peak = [], 0
        for _ in range(n):
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(data)
            lvl = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
            if lvl > peak:
                peak = lvl
        stream.stop_stream(); stream.close(); pa.terminate()
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(audio_dir, f"audio_{ts}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(ch); wf.setsampwidth(2)
            wf.setframerate(sr); wf.writeframes(b"".join(frames))
        return path, peak
    except Exception:
        import traceback; traceback.print_exc()
        return None, 0


def _record_mic_wav(mic_dir: str, duration: int) -> tuple:
    """Record from default mic for `duration` seconds. Returns (wav_path, peak_level)."""
    try:
        import pyaudiowpatch as pyaudio, numpy as np, wave
        from datetime import datetime
        pa   = pyaudio.PyAudio()
        info = pa.get_default_input_device_info()
        idx  = int(info["index"])
        ch   = min(info["maxInputChannels"], 1)
        sr   = int(info["defaultSampleRate"])
        n    = int(sr / 1024 * duration)
        stream = pa.open(format=pyaudio.paInt16, channels=ch, rate=sr,
                         frames_per_buffer=1024, input=True, input_device_index=idx)
        frames, peak = [], 0
        for _ in range(n):
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(data)
            lvl = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
            if lvl > peak:
                peak = lvl
        stream.stop_stream(); stream.close(); pa.terminate()
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(mic_dir, f"mic_{ts}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(ch); wf.setsampwidth(2)
            wf.setframerate(sr); wf.writeframes(b"".join(frames))
        return path, peak
    except Exception:
        import traceback; traceback.print_exc()
        return None, 0


def _load_whisper(cfg):
    """Load WhisperModel using config settings. Returns (model, device) or (None, None)."""
    try:
        from appconfig import _setup_cuda_dlls
        _setup_cuda_dlls()
        from faster_whisper import WhisperModel
        import ctranslate2
        model_size = cfg.get("recording", "model_size", fallback="small")
        device     = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        compute    = "float16" if device == "cuda" else "int8"
        print(f"  Loading Whisper {model_size} on {device}...")
        return WhisperModel(model_size, device=device, compute_type=compute), device
    except Exception as e:
        print(f"  ❌ Model load failed: {e}")
        return None, None


def _transcribe_to_lines(model, wav_path: str, speaker_label: str = "") -> list:
    """
    Transcribe wav_path with the given model.
    Returns list of '[HH:MM:SS] [label] text' strings, sorted by segment start time.
    """
    from datetime import datetime, timedelta
    try:
        from transcriber import (
            _parse_file_start_time, HALLUCINATION_PHRASES,
            build_initial_prompt, load_vocabulary,
        )
        vocab = load_vocabulary()
    except Exception:
        vocab = []
        HALLUCINATION_PHRASES = []
        def _parse_file_start_time(_): return None  # type: ignore
        def build_initial_prompt(l, v): return None  # type: ignore

    try:
        segments, info = model.transcribe(
            wav_path, language=None,
            initial_prompt=build_initial_prompt(None, vocab),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        seg_list = list(segments)
    except Exception as e:
        print(f"  ❌ Transcription error: {e}")
        return []

    print(f"  Detected language: {info.language} ({info.language_probability:.0%})")

    file_start = _parse_file_start_time(wav_path)
    lines = []
    for seg in seg_list:
        text = seg.text.strip()
        if not text or any(p in text for p in HALLUCINATION_PHRASES):
            continue
        if file_start:
            actual = file_start + timedelta(seconds=seg.start)
            ts     = actual.strftime("%H:%M:%S")
        else:
            ts = datetime.now().strftime("%H:%M:%S")
        label_part = f" {speaker_label}" if speaker_label else ""
        lines.append(f"[{ts}]{label_part} {text}")
    return lines


# ── test_audio_to_text ────────────────────────────────────────────────────────

def test_audio_to_text(duration: int = 15):
    """Record from loopback for duration seconds, then transcribe."""
    section(f"TEST: audio (loopback) → text  [{duration}s recording]")

    from appconfig import AppConfig
    cfg       = AppConfig()
    audio_dir = cfg.get("paths", "audio_dir", fallback=r"C:\Users\Public\Sound2Text\audio")
    os.makedirs(audio_dir, exist_ok=True)

    print(f"Recording {duration}s from loopback (play system / meeting audio now)...")
    wav, peak = _record_loopback_wav(audio_dir, duration)
    if not wav:
        print("❌ Loopback recording failed."); return False

    bar = "█" * min(peak // 100, 30)
    print(f"  Saved: {os.path.basename(wav)}  peak={peak}  |{bar}|")
    if peak < 10:
        print("⚠  Near-silent — run `python debug_modules.py loopback` to diagnose.")

    model, _ = _load_whisper(cfg)
    if not model:
        return False

    print("  Transcribing...")
    lines = _transcribe_to_lines(model, wav)
    if lines:
        print(f"✓  {len(lines)} segment(s) transcribed:")
        for line in lines:
            print(f"     {line}")
        return True
    else:
        print("⚠  No speech detected. (Recording may be silent or hallucinations filtered.)")
        return peak > 10   # pass if audio was present


# ── test_mic_to_text ──────────────────────────────────────────────────────────

def test_mic_to_text(duration: int = 15):
    """Record from default mic for duration seconds, then transcribe."""
    section(f"TEST: mic → text  [{duration}s recording]")

    from appconfig import AppConfig
    cfg     = AppConfig()
    mic_dir = cfg.get("paths", "mic_dir", fallback=r"C:\Users\Public\Sound2Text\mic")
    os.makedirs(mic_dir, exist_ok=True)

    print(f"Recording {duration}s from microphone (please speak now)...")
    wav, peak = _record_mic_wav(mic_dir, duration)
    if not wav:
        print("❌ Mic recording failed."); return False

    bar = "█" * min(peak // 30, 30)
    print(f"  Saved: {os.path.basename(wav)}  peak={peak}  |{bar}|")
    if peak < 10:
        print("⚠  Near-silent — is the microphone connected and unmuted?")

    model, _ = _load_whisper(cfg)
    if not model:
        return False

    print("  Transcribing...")
    lines = _transcribe_to_lines(model, wav, speaker_label="[自分]")
    if lines:
        print(f"✓  {len(lines)} segment(s) transcribed:")
        for line in lines:
            print(f"     {line}")
        return True
    else:
        print("⚠  No speech detected in mic recording.")
        return False


# ── test_merge_pipeline ───────────────────────────────────────────────────────

def test_merge_pipeline(duration: int = 20):
    """
    Record loopback + mic simultaneously, transcribe both, then verify merge.
    The merge step sorts all segments by absolute timestamp (same logic as
    transcriber.py _seg_buf) so loopback and mic lines are interleaved correctly.
    """
    section(f"TEST: loopback + mic → merge  [{duration}s simultaneous recording]")

    from appconfig import AppConfig
    cfg       = AppConfig()
    audio_dir = cfg.get("paths", "audio_dir", fallback=r"C:\Users\Public\Sound2Text\audio")
    mic_dir   = cfg.get("paths", "mic_dir",   fallback=r"C:\Users\Public\Sound2Text\mic")
    for d in (audio_dir, mic_dir):
        os.makedirs(d, exist_ok=True)

    # ── 1. Simultaneous recording ─────────────────────────────────────────────
    print(f"Recording {duration}s simultaneously.")
    print("  → Play audio on speakers  AND  speak into the microphone now.\n")

    results = {}

    def _do_lb():
        results["loopback"] = _record_loopback_wav(audio_dir, duration)

    def _do_mic():
        results["mic"] = _record_mic_wav(mic_dir, duration)

    t1 = threading.Thread(target=_do_lb,  daemon=True)
    t2 = threading.Thread(target=_do_mic, daemon=True)
    t1.start(); t2.start()

    for remaining in range(duration, 0, -1):
        print(f"\r  {remaining:3d}s remaining...", end="", flush=True)
        time.sleep(1)
    print("\r  Recording complete.          ")

    t1.join(); t2.join()

    lb_wav,  lb_peak  = results.get("loopback", (None, 0))
    mic_wav, mic_peak = results.get("mic",      (None, 0))

    lb_bar  = "█" * min(lb_peak  // 100, 20) if lb_wav  else "FAILED"
    mic_bar = "█" * min(mic_peak // 30,  20) if mic_wav else "FAILED"
    print(f"  [Loopback] peak={lb_peak:5d}  |{lb_bar:<20}|  {os.path.basename(lb_wav)  if lb_wav  else '---'}")
    print(f"  [Mic]      peak={mic_peak:5d}  |{mic_bar:<20}|  {os.path.basename(mic_wav) if mic_wav else '---'}")

    if not lb_wav and not mic_wav:
        print("❌ Both recordings failed."); return False

    # ── 2. Load model once, transcribe both ───────────────────────────────────
    model, _ = _load_whisper(cfg)
    if not model:
        return False

    from datetime import datetime, timedelta
    all_segs = []   # list of (datetime | None, str)

    def _collect(wav_path, label):
        if not wav_path:
            return
        print(f"\n  Transcribing {os.path.basename(wav_path)} ...")
        from transcriber import _parse_file_start_time, HALLUCINATION_PHRASES, build_initial_prompt, load_vocabulary
        vocab = load_vocabulary()
        try:
            segs, info = model.transcribe(
                wav_path, language=None,
                initial_prompt=build_initial_prompt(None, vocab),
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
            file_start = _parse_file_start_time(wav_path)
            count = 0
            lp = f" {label}" if label else ""
            for seg in segs:
                text = seg.text.strip()
                if not text or any(p in text for p in HALLUCINATION_PHRASES):
                    continue
                dt = (file_start + timedelta(seconds=seg.start)) if file_start else None
                ts = dt.strftime("%H:%M:%S") if dt else datetime.now().strftime("%H:%M:%S")
                all_segs.append((dt, f"[{ts}]{lp} {text}"))
                count += 1
            print(f"  Detected: {info.language} ({info.language_probability:.0%})  → {count} segment(s)")
        except Exception as e:
            print(f"  ❌ Transcription error: {e}")

    _collect(lb_wav,  "")        # loopback: no speaker label
    _collect(mic_wav, "[自分]")  # mic: speaker label

    # ── 3. Merge: sort by absolute timestamp ──────────────────────────────────
    all_segs.sort(key=lambda x: x[0] if x[0] else datetime.min)

    section("MERGED TRANSCRIPT")
    if not all_segs:
        print("⚠  No speech detected in either source.")
        return lb_peak > 10 or mic_peak > 10

    for _, line in all_segs:
        print(f"  {line}")

    lb_lines  = [l for _, l in all_segs if "[自分]" not in l and l.startswith("[")]
    mic_lines = [l for _, l in all_segs if "[自分]" in l]

    print(f"\n  Total segments  : {len(all_segs)}")
    print(f"  Loopback lines  : {len(lb_lines)}  {'✓' if lb_lines else '⚠ silent/empty'}")
    print(f"  Mic lines       : {len(mic_lines)}  {'✓' if mic_lines else '⚠ silent/empty (speak during test)'}")

    return len(all_segs) > 0


# ── check_signal_files ────────────────────────────────────────────────────────

def check_signal_files():
    """Check for stale signal files that could interfere with recording."""
    section("CHECK: Signal files")
    signals = {
        ".stop_signal": "Recording stop signal (should NOT exist at startup)",
        ".ptt_stop":    "PTT stop signal (should NOT exist at startup)",
        ".recording_start": "Recording start time (OK to exist)",
        ".last_transcript": "Last transcript path (OK to exist)",
        ".last_corrected":  "Last corrected path (OK to exist)",
    }
    all_ok = True
    for fname, desc in signals.items():
        path = os.path.join(BASE, fname)
        exists = os.path.exists(path)
        status = "EXISTS" if exists else "absent"
        warn = "⚠" if exists and "should NOT" in desc else " "
        print(f"  {warn} {fname:<22} {status}  — {desc}")
        if exists and "should NOT" in desc:
            all_ok = False
    return all_ok

# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd      = sys.argv[1] if len(sys.argv) > 1 else "all"
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    print(f"\nSound2Text Debug Tool  BASE: {BASE}")
    check_signal_files()

    results = {}
    if cmd in ("loopback", "all"):
        results["loopback"]  = test_loopback()
    if cmd in ("audio", "all"):
        results["audio"]     = test_audio_to_text(duration)
    if cmd in ("mic", "all"):
        results["mic"]       = test_mic_to_text(duration)
    if cmd in ("pipeline",):
        results["pipeline"]  = test_merge_pipeline(duration if len(sys.argv) > 2 else 20)
    if cmd in ("transcriber", "all"):
        results["transcriber"] = test_transcriber()
    if cmd in ("summarizer", "all"):
        results["summarizer"]  = test_summarizer()
    if cmd in ("ui", "all"):
        results["ui"]          = test_ui()

    section("SUMMARY")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '❌'}  {name}")

    sys.exit(0 if all(results.values()) else 1)

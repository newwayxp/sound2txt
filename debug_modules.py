"""
debug_modules.py — Individual module tests using existing recordings.

Usage:
  python debug_modules.py loopback      # list loopback devices + 5s capture test (meeting mode)
  python debug_modules.py transcriber   # test transcriber with latest WAV file
  python debug_modules.py summarizer    # test summarizer with latest transcript
  python debug_modules.py ui            # launch UI in debug mode (extra logging)
  python debug_modules.py all           # run all tests sequentially
"""
import sys
import os
import glob
import subprocess

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
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    print(f"\nSound2Text Debug Tool  BASE: {BASE}")
    check_signal_files()

    results = {}
    if cmd in ("loopback", "all"):
        results["loopback"]    = test_loopback()
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

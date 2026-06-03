"""
Microphone recording process.
Captures local microphone input and saves to mic_dir as mic_TIMESTAMP.wav.
Uses the same stop-signal mechanism as recorder.py.
"""
import pyaudiowpatch as pyaudio
import wave
import os
import time
import configparser
import numpy as np
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

CHUNK       = 1024
MAX_RETRIES = 5
RETRY_WAIT  = 3
STOP_SIGNAL     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".stop_signal")
PTT_STOP_SIGNAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ptt_stop")


def _select_mic_device(pa: pyaudio.PyAudio):
    """
    Select the default microphone input device (not loopback).
    Returns (device_index, device_info, aec_available).
    """
    # Try system default input
    try:
        info = pa.get_default_input_device_info()
        if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice", False):
            print(f"[MicRecorder] Mic device: {info['name']}")
            return int(info["index"]), info, False
    except Exception:
        pass

    # Scan as fallback
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice", False):
            print(f"[MicRecorder] Mic device (scan): {info['name']}")
            return i, info, False

    return None, None, False


def _open_stream(pa, device_index, channels, sample_rate):
    return pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=sample_rate,
        frames_per_buffer=CHUNK,
        input=True,
        input_device_index=device_index,
    )


def main():
    import sys as _sys
    ptt_mode = "--ptt" in _sys.argv   # PTT button always records, ignoring enable_mic

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")

    if not ptt_mode and not cfg.getboolean("recording", "enable_mic", fallback=True):
        print("[MicRecorder] Mic recording disabled in config.")
        return

    mic_dir        = cfg.get("paths", "mic_dir", fallback=r"C:\Users\Public\Sound2Text\mic")
    mic_record_sec = cfg.getint("recording", "record_sec", fallback=30)
    os.makedirs(mic_dir, exist_ok=True)
    os.makedirs(os.path.join(mic_dir, "done"), exist_ok=True)

    pa = pyaudio.PyAudio()
    device_index, dev_info, aec_ok = _select_mic_device(pa)

    if device_index is None:
        print("[MicRecorder] No microphone found. Exiting.")
        pa.terminate()
        return

    channels    = min(dev_info["maxInputChannels"], 1)  # mono for Whisper
    sample_rate = int(dev_info["defaultSampleRate"])
    sample_size = pa.get_sample_size(pyaudio.paInt16)
    n_chunks    = int(sample_rate / CHUNK * mic_record_sec)

    print(f"[MicRecorder] {mic_record_sec}s per file -> {mic_dir}")
    print("Ctrl+C to stop\n")

    stream             = None
    consecutive_errors = 0

    def _read_chunk_or_stop():
        """
        Read one CHUNK from the stream.
        Returns (data, stopped) where stopped=True if stop signal was detected.
        """
        if os.path.exists(STOP_SIGNAL) or os.path.exists(PTT_STOP_SIGNAL):
            return None, True
        try:
            return stream.read(CHUNK, exception_on_overflow=False), False
        except Exception as e:
            raise e

    def _save_frames(frames):
        """Save collected frames to a WAV file."""
        if not frames:
            return
        ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        tmp_path = os.path.join(mic_dir, f".tmp_{ts}.wav")
        fin_path = os.path.join(mic_dir, f"mic_{ts}.wav")
        level    = int(np.abs(np.frombuffer(b"".join(frames), dtype=np.int16)).mean())
        try:
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(sample_size)
                wf.setframerate(sample_rate)
                wf.writeframes(b"".join(frames))
            os.rename(tmp_path, fin_path)
            print(f"[MicRecorder] Saved: {os.path.basename(fin_path)}  (level:{level})")
        except Exception as e:
            print(f"[MicRecorder] Save error: {e}")
            try: os.remove(tmp_path)
            except Exception: pass

    try:
        stream = _open_stream(pa, device_index, channels, sample_rate)

        while True:
            # Read frames one by one, checking stop signal between each
            frames             = []
            stopped_mid_chunk  = False
            consecutive_errors = 0

            for _ in range(n_chunks):
                try:
                    data, stopped = _read_chunk_or_stop()
                except Exception as e:
                    consecutive_errors += 1
                    print(f"\n[MicRecorder] Stream error ({consecutive_errors}/{MAX_RETRIES}): {e}")
                    if consecutive_errors >= MAX_RETRIES:
                        print("[MicRecorder] Restarting stream...")
                        try:
                            stream.stop_stream(); stream.close()
                        except Exception:
                            pass
                        time.sleep(RETRY_WAIT)
                        try:
                            stream = _open_stream(pa, device_index, channels, sample_rate)
                            consecutive_errors = 0
                        except Exception as re:
                            print(f"[MicRecorder] Restart failed: {re}")
                            stopped_mid_chunk = True
                    break

                if stopped:
                    stopped_mid_chunk = True
                    break
                frames.append(data)

            # Save whatever was collected (full chunk or partial)
            _save_frames(frames)

            if stopped_mid_chunk:
                break

    except KeyboardInterrupt:
        print("\n[MicRecorder] Stopped")
    finally:
        if stream is not None:
            try: stream.stop_stream(); stream.close()
            except Exception: pass
        pa.terminate()
        # Clean up PTT stop signal if it was used
        try:
            if os.path.exists(PTT_STOP_SIGNAL):
                os.remove(PTT_STOP_SIGNAL)
        except Exception:
            pass


if __name__ == "__main__":
    main()

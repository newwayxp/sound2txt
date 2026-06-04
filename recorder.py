"""
録音プロセス: 音声をキャプチャし WAV ファイルとして AUDIO_DIR に保存し続ける
- 個別チャンク読み取りエラー → 無音バイトで代替してループ継続
- ストリームレベルエラー     → 自動再起動（最大 MAX_RETRIES 回）
- WAV 保存エラー            → ログして次チャンクへ継続
"""
import pyaudiowpatch as pyaudio
import wave
import os
import time
import configparser
import numpy as np
import warnings
from datetime import datetime
from device_utils import select_active_device

warnings.filterwarnings("ignore")

CHUNK       = 1024
MAX_RETRIES = 5   # ストリーム再起動の最大試行回数
RETRY_WAIT  = 3   # 再起動前の待機秒数


def _open_stream(pa, device_index, channels, sample_rate):
    return pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=sample_rate,
        frames_per_buffer=CHUNK,
        input=True,
        input_device_index=device_index,
    )


def _read_chunk(stream) -> bytes:
    """チャンク読み取り。エラー時は無音バイトを返してループを継続させる。"""
    try:
        return stream.read(CHUNK, exception_on_overflow=False)
    except Exception:
        return bytes(CHUNK * 2)  # 16bit silence


def _restart_stream(pa, device_index, channels, sample_rate) -> pyaudio.Stream | None:
    """ストリームを段階的に再起動する。全試行失敗なら None を返す。"""
    for attempt in range(1, MAX_RETRIES + 1):
        wait = RETRY_WAIT * attempt
        print(f"[Recorder] 再起動試行 {attempt}/{MAX_RETRIES}  ({wait}秒後)...")
        time.sleep(wait)
        try:
            s = _open_stream(pa, device_index, channels, sample_rate)
            print("[Recorder] ストリーム再起動成功")
            return s
        except Exception as e:
            print(f"[Recorder] 試行 {attempt} 失敗: {e}")
    return None


START_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".recording_start")


def main():
    _cfg = configparser.ConfigParser()
    _cfg.read(os.path.join(os.path.dirname(__file__), "config.ini"), encoding="utf-8")
    audio_dir  = _cfg.get("paths", "audio_dir")
    record_sec = _cfg.getint("recording", "record_sec", fallback=30)
    os.makedirs(audio_dir, exist_ok=True)

    # 録音開始時刻を Unix timestamp で書き込む（transcriber が旧ファイルをスキップするために使用）
    session_start_ts = time.time()
    with open(START_FILE, "w", encoding="utf-8") as f:
        f.write(str(session_start_ts))
    print(f"[Recorder] セッション開始: {datetime.fromtimestamp(session_start_ts).strftime('%Y-%m-%d %H:%M:%S')}")

    pa = pyaudio.PyAudio()
    device_index, dev_info = select_active_device(pa)
    channels    = dev_info["maxInputChannels"]
    sample_rate = int(dev_info["defaultSampleRate"])
    sample_size = pa.get_sample_size(pyaudio.paInt16)
    n_chunks    = int(sample_rate / CHUNK * record_sec)

    print(f"[Recorder] デバイス: {dev_info['name']}")
    print(f"[Recorder] {record_sec}秒ごとに保存 → {audio_dir}")
    print("Ctrl+C で停止\n")

    stream            = _open_stream(pa, device_index, channels, sample_rate)
    consecutive_errors = 0
    _file_count        = 0  # how many WAV files saved so far

    try:
        while True:
            # ── チャンク収集（個別エラーは無音で代替） ────────────────
            try:
                frames = [_read_chunk(stream) for _ in range(n_chunks)]
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                print(f"\n[Recorder] ストリームエラー ({consecutive_errors}/{MAX_RETRIES}): {e}")

                if consecutive_errors >= MAX_RETRIES:
                    try:
                        stream.stop_stream()
                        stream.close()
                    except Exception:
                        pass
                    new_stream = _restart_stream(pa, device_index, channels, sample_rate)
                    if new_stream is None:
                        print("[Recorder] 復旧失敗。録音を終了します。")
                        break
                    stream = new_stream
                    consecutive_errors = 0
                continue

            # ── WAV 保存 ──────────────────────────────────────────────
            audio_np = np.frombuffer(b"".join(frames), dtype=np.int16)
            level    = int(np.abs(audio_np).mean())
            _file_count += 1
            ts       = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            tmp_path = os.path.join(audio_dir, f".tmp_{ts}.wav")
            fin_path = os.path.join(audio_dir, f"audio_{ts}.wav")

            # Warn on first 3 files if level suggests silence
            if _file_count <= 3 and level < 10:
                print(f"[Recorder] ⚠ 音量がほぼゼロ (avg:{level}) — "
                      "ループバックデバイスが正しく選択されていない可能性があります。"
                      " python debug_modules.py loopback を実行して確認してください。")

            try:
                with wave.open(tmp_path, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(sample_size)
                    wf.setframerate(sample_rate)
                    wf.writeframes(b"".join(frames))
                os.rename(tmp_path, fin_path)
                print(f"[Recorder] 保存: {os.path.basename(fin_path)}  (音量avg:{level})")
            except Exception as e:
                print(f"[Recorder] ファイル保存エラー: {e}")
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    except KeyboardInterrupt:
        print("\n[Recorder] 停止")
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        pa.terminate()


if __name__ == "__main__":
    main()

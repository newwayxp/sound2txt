"""
録音プロセス: 音声を1セッション1ファイルとして蓄積する

動作:
  - 録音中は生 PCM を一時ファイル (.tmp_audio_TIMESTAMP.raw) に追記
  - 停止・終了時に WAV ヘッダを付けて audio_TIMESTAMP.wav として保存
  - 複数の小さなファイルを生成しない
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
from log_util import rec_debug, rec_info, rec_warn, rec_error, sys_info, sys_error

warnings.filterwarnings("ignore")

CHUNK       = 1024
MAX_RETRIES = 5
RETRY_WAIT  = 3

START_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".recording_start")


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
    try:
        return stream.read(CHUNK, exception_on_overflow=False)
    except Exception:
        return bytes(CHUNK * 2)   # 無音で代替


def _restart_stream(pa, device_index, channels, sample_rate):
    for attempt in range(1, MAX_RETRIES + 1):
        wait = RETRY_WAIT * attempt
        rec_warn(f"ストリーム再起動試行 {attempt}/{MAX_RETRIES} ({wait}秒後)")
        time.sleep(wait)
        try:
            s = _open_stream(pa, device_index, channels, sample_rate)
            rec_info("ストリーム再起動成功")
            return s
        except Exception as e:
            rec_error(f"再起動試行 {attempt} 失敗: {e}")
    return None


def _finalize_wav(raw_path: str, wav_path: str, channels: int,
                  sample_size: int, sample_rate: int) -> bool:
    """生 PCM ファイルを WAV に変換して raw を削除する。"""
    try:
        with open(raw_path, "rb") as f:
            pcm_data = f.read()
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_size)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_data)
        os.remove(raw_path)
        duration = len(pcm_data) / (sample_rate * channels * sample_size)
        rec_info(f"保存完了: {os.path.basename(wav_path)}  "
                 f"({duration:.1f}秒 / {len(pcm_data) // 1024} KB)")
        return True
    except Exception as e:
        rec_error(f"WAV変換失敗: {e}  raw={raw_path}")
        return False


def main():
    _cfg = configparser.ConfigParser()
    _cfg.read(os.path.join(os.path.dirname(__file__), "config.ini"), encoding="utf-8")
    audio_dir  = _cfg.get("paths", "audio_dir")
    os.makedirs(audio_dir, exist_ok=True)

    # 録音開始時刻を書き込む（transcriber / subtitle_processor が旧ファイルをスキップするために使用）
    session_start_ts = time.time()
    with open(START_FILE, "w", encoding="utf-8") as f:
        f.write(str(session_start_ts))
    session_ts = datetime.fromtimestamp(session_start_ts).strftime("%Y%m%d_%H%M%S")
    sys_info(f"セッション開始: {datetime.fromtimestamp(session_start_ts).strftime('%Y-%m-%d %H:%M:%S')}")

    pa = pyaudio.PyAudio()
    device_index, dev_info = select_active_device(pa)
    channels    = dev_info["maxInputChannels"]
    sample_rate = int(dev_info["defaultSampleRate"])
    sample_size = pa.get_sample_size(pyaudio.paInt16)

    rec_info(f"デバイス: {dev_info['name']}  rate={sample_rate}  ch={channels}")

    # ── 一時ファイルパス ─────────────────────────────────────────────────────
    raw_path = os.path.join(audio_dir, f".tmp_audio_{session_ts}.raw")
    wav_path = os.path.join(audio_dir, f"audio_{session_ts}.wav")
    rec_info(f"録音先(一時): {raw_path}")

    stream             = _open_stream(pa, device_index, channels, sample_rate)
    consecutive_errors = 0
    total_chunks       = 0

    try:
        with open(raw_path, "wb") as raw_file:
            rec_info("録音開始 (Ctrl+C または停止シグナルで終了)")
            while True:
                try:
                    data = _read_chunk(stream)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    rec_warn(f"チャンク読み取りエラー ({consecutive_errors}/{MAX_RETRIES}): {e}")
                    if consecutive_errors >= MAX_RETRIES:
                        try:
                            stream.stop_stream(); stream.close()
                        except Exception:
                            pass
                        new_stream = _restart_stream(pa, device_index, channels, sample_rate)
                        if new_stream is None:
                            rec_error("復旧失敗。録音を終了します。")
                            break
                        stream = new_stream
                        consecutive_errors = 0
                    data = bytes(CHUNK * 2)

                raw_file.write(data)
                total_chunks += 1

                # 音量監視（最初の数チャンクで警告）
                if total_chunks <= 3:
                    level = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
                    if level < 10:
                        rec_warn(f"音量ほぼゼロ (avg:{level}) — ループバックデバイスを確認してください")

                # 定期的な進捗ログ（30秒ごと相当）
                if total_chunks % int(sample_rate / CHUNK * 30) == 0:
                    elapsed = total_chunks * CHUNK / sample_rate
                    size_kb = total_chunks * CHUNK * sample_size // 1024
                    rec_debug(f"録音中: {elapsed:.0f}秒 / {size_kb} KB")

    except KeyboardInterrupt:
        rec_info("停止シグナル受信")
    finally:
        try:
            stream.stop_stream(); stream.close()
        except Exception:
            pass
        pa.terminate()

    # ── WAV に変換して保存 ────────────────────────────────────────────────────
    if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
        rec_info("WAV ファイルに変換中...")
        _finalize_wav(raw_path, wav_path, channels, sample_size, sample_rate)
    else:
        rec_warn("録音データなし。ファイルを保存しません。")

    rec_info("録音プロセス終了")


if __name__ == "__main__":
    main()

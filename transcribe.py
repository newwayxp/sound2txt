import pyaudiowpatch as pyaudio
import wave
import tempfile
import os
import numpy as np
import warnings
from faster_whisper import WhisperModel
from datetime import datetime

warnings.filterwarnings("ignore")

CHUNK             = 1024
RATE              = 16000
RECORD_SEC        = 5
SILENCE_THRESHOLD = 800

HALLUCINATION_PHRASES = [
    "ご視聴ありがとうございました", "チャンネル登録", "字幕",
    "thank you for watching", "please subscribe",
]

OUTPUT_DIR  = r"C:\vscode\ChubbAPI"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")

# ── デバイス選択 ──────────────────────────────────────────
pa = pyaudio.PyAudio()

print("ループバックデバイス一覧:")
loopback_devices = []
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info["maxInputChannels"] > 0 and info.get("isLoopbackDevice", False):
        loopback_devices.append((i, info))
        print(f"  [{len(loopback_devices)-1}] {info['name']}")

if not loopback_devices:
    print("[ERROR] ループバックデバイスが見つかりません。")
    pa.terminate()
    exit(1)

if len(loopback_devices) == 1:
    selected = loopback_devices[0]
else:
    print()
    choice   = input("ヘッドセット/イヤホンに対応する番号を選択してください: ")
    selected = loopback_devices[int(choice)]

device_index = selected[0]
channels     = selected[1]["maxInputChannels"]
sample_rate  = int(selected[1]["defaultSampleRate"])
print(f"選択: {selected[1]['name']}\n")

# ── 音量確認 (5秒) ────────────────────────────────────────
print("[音量確認] 5秒間 Zoom の音声を流してください...")
stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                 frames_per_buffer=CHUNK, input=True, input_device_index=device_index)

max_level = 0
for _ in range(int(sample_rate / CHUNK * 5)):
    data  = stream.read(CHUNK, exception_on_overflow=False)
    level = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
    max_level = max(max_level, level)
    bar = "#" * (level // 100)
    print(f"\r音量: {level:5d} |{bar:<40}|", end="", flush=True)

stream.stop_stream(); stream.close()

if max_level < SILENCE_THRESHOLD:
    SILENCE_THRESHOLD = max(50, max_level // 2)
    print(f"\n閾値を {SILENCE_THRESHOLD} に自動調整しました。")
else:
    print(f"\n音量正常 (最大: {max_level})")

# ── モデル読み込み ────────────────────────────────────────
print("\nfaster-whisper small モデル読み込み中...")
model = WhisperModel("small", device="cpu", compute_type="int8")
print(f"準備完了 → 保存先: {OUTPUT_FILE}")
print("Ctrl+C で停止\n")

# ── 文字起こし ────────────────────────────────────────────
stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                 frames_per_buffer=CHUNK, input=True, input_device_index=device_index)

with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
    out.write(f"=== Zoom文字起こし {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
    try:
        while True:
            frames   = [stream.read(CHUNK, exception_on_overflow=False)
                        for _ in range(int(RATE / CHUNK * RECORD_SEC))]
            audio_np = np.frombuffer(b"".join(frames), dtype=np.int16)
            level    = int(np.abs(audio_np).mean())

            if level < SILENCE_THRESHOLD:
                print(f"\r待機中... (音量:{level:4d})", end="", flush=True)
                continue

            print(f"\n変換中... (音量:{level})")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            with wave.open(tmp, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                wf.setframerate(RATE)
                wf.writeframes(b"".join(frames))

            segments, info = model.transcribe(
                tmp, language="ja",
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500}
            )
            text = " ".join(s.text.strip() for s in segments)
            os.remove(tmp)

            is_hallucination = any(p in text for p in HALLUCINATION_PHRASES)
            jp_chars   = sum(1 for c in text if '　' <= c <= '鿿')
            is_foreign = len(text) > 3 and jp_chars / max(len(text), 1) < 0.4

            if text and not is_hallucination and not is_foreign:
                timestamp = datetime.now().strftime("%H:%M:%S")
                line = f"[{timestamp}] {text}"
                print(line)
                out.write(line + "\n")
                out.flush()

    except KeyboardInterrupt:
        end_line = f"\n=== 終了 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ==="
        print(end_line)
        out.write(end_line + "\n")

stream.stop_stream(); stream.close()
pa.terminate()
print(f"保存しました: {OUTPUT_FILE}")

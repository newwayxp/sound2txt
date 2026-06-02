import pyaudiowpatch as pyaudio
import wave
import numpy as np
import warnings
from datetime import datetime

warnings.filterwarnings("ignore")

RECORD_SEC  = 30  # 録音秒数
OUTPUT_FILE = rf"C:\vscode\ChubbAPI\test_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

pa = pyaudio.PyAudio()

# ── デバイス選択 ──────────────────────────────────────────
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
    choice   = input("\nヘッドセット/イヤホンの番号を選択してください: ")
    selected = loopback_devices[int(choice)]

device_index = selected[0]
channels     = selected[1]["maxInputChannels"]
sample_rate  = int(selected[1]["defaultSampleRate"])
print(f"\n選択: {selected[1]['name']}")

# ── 録音 ─────────────────────────────────────────────────
CHUNK = 1024
print(f"\n{RECORD_SEC}秒間録音開始... (Zoom の音声を流してください)\n")

stream = pa.open(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                 frames_per_buffer=CHUNK, input=True, input_device_index=device_index)

frames    = []
total     = int(sample_rate / CHUNK * RECORD_SEC)
max_level = 0

for i in range(total):
    data  = stream.read(CHUNK, exception_on_overflow=False)
    frames.append(data)
    level = int(np.abs(np.frombuffer(data, dtype=np.int16)).mean())
    max_level = max(max_level, level)
    elapsed = int(i / total * RECORD_SEC)
    bar = "#" * (level // 150)
    print(f"\r{elapsed:2d}秒 / {RECORD_SEC}秒  音量:{level:5d} |{bar:<30}|", end="", flush=True)

stream.stop_stream(); stream.close(); pa.terminate()

# ── WAV 保存 ─────────────────────────────────────────────
with wave.open(OUTPUT_FILE, "wb") as wf:
    wf.setnchannels(channels)
    wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
    wf.setframerate(sample_rate)
    wf.writeframes(b"".join(frames))

print(f"\n\n録音完了")
print(f"最大音量レベル : {max_level}")
print(f"保存先         : {OUTPUT_FILE}")
print(f"\nWindows Media Player で再生して音声内容を確認してください。")

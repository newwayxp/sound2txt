import pyaudiowpatch as pyaudio
import wave
import numpy as np
import warnings
import configparser
import os
from datetime import datetime
from device_utils import select_active_device

warnings.filterwarnings("ignore")

_cfg = configparser.ConfigParser()
_cfg.read(os.path.join(os.path.dirname(__file__), "config.ini"), encoding="utf-8")
AUDIO_DIR = _cfg.get("paths", "audio_dir")
os.makedirs(AUDIO_DIR, exist_ok=True)

RECORD_SEC  = 30
CHUNK       = 1024
OUTPUT_FILE = os.path.join(AUDIO_DIR, f"test_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")

pa = pyaudio.PyAudio()

device_index, dev_info = select_active_device(pa)
channels    = dev_info["maxInputChannels"]
sample_rate = int(dev_info["defaultSampleRate"])
sample_size = pa.get_sample_size(pyaudio.paInt16)

print(f"\n{RECORD_SEC}秒間録音開始...\n")

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

stream.stop_stream()
stream.close()

with wave.open(OUTPUT_FILE, "wb") as wf:
    wf.setnchannels(channels)
    wf.setsampwidth(sample_size)
    wf.setframerate(sample_rate)
    wf.writeframes(b"".join(frames))

pa.terminate()

print(f"\n\n録音完了")
print(f"最大音量レベル : {max_level}")
print(f"保存先         : {OUTPUT_FILE}")

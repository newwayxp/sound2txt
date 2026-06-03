import pyaudiowpatch as pyaudio
import numpy as np
import subprocess
import sys

PROBE_SEC       = 0.5   # 各デバイスを試す秒数
PROBE_TIMEOUT   = 3.0   # この秒数で応答しないデバイスはスキップ
ACTIVE_THRESHOLD = 100  # この音量avg以上なら即決定・残りのデバイスはスキップ


def _probe_level(dev_idx: int, channels: int, sample_rate: int) -> int:
    """別プロセスでデバイスをサンプリングし音量平均を返す。タイムアウト or 失敗時は 0。"""
    code = (
        "import pyaudiowpatch as pyaudio, numpy as np, sys\n"
        "pa = pyaudio.PyAudio()\n"
        "try:\n"
        f"    s = pa.open(format=pyaudio.paInt16, channels={channels},\n"
        f"                rate={sample_rate}, frames_per_buffer=1024,\n"
        f"                input=True, input_device_index={dev_idx})\n"
        f"    n = max(1, int({sample_rate}/1024*{PROBE_SEC}))\n"
        "    lvls = [int(np.abs(np.frombuffer(s.read(1024, exception_on_overflow=False), dtype=np.int16)).mean()) for _ in range(n)]\n"
        "    s.stop_stream(); s.close()\n"
        "    print(int(np.mean(lvls)))\n"
        "except Exception:\n"
        "    print(0)\n"
        "finally:\n"
        "    pa.terminate()\n"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT
        )
        return int(r.stdout.strip() or "0")
    except (subprocess.TimeoutExpired, ValueError):
        return 0


def select_active_device(pa: pyaudio.PyAudio) -> tuple[int, dict]:
    """
    全ループバックデバイスを短時間サンプリングし、
    最も音量が大きいデバイスを自動選択して返す。
    応答しないデバイスは PROBE_TIMEOUT 秒でスキップ。
    戻り値: (device_index, device_info)
    """
    loopback_devices = []
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and info.get("isLoopbackDevice", False):
            loopback_devices.append((i, info))

    if not loopback_devices:
        print("[ERROR] ループバックデバイスが見つかりません。")
        pa.terminate()
        raise SystemExit(1)

    if len(loopback_devices) == 1:
        idx, info = loopback_devices[0]
        print(f"デバイス自動選択: {info['name']}")
        return idx, info

    print("デバイスの音量を確認中...")
    best_idx, best_info, best_level = None, None, -1

    for dev_idx, dev_info in loopback_devices:
        channels    = dev_info["maxInputChannels"]
        sample_rate = int(dev_info["defaultSampleRate"])

        avg = _probe_level(dev_idx, channels, sample_rate)

        status = f"音量avg: {avg:5d}" if avg > 0 else "タイムアウト(スキップ)"
        print(f"  [{dev_idx:2d}] {dev_info['name'][:50]:<50}  {status}")

        if avg > best_level:
            best_level = avg
            best_idx   = dev_idx
            best_info  = dev_info

        if avg >= ACTIVE_THRESHOLD:
            print(f"有音デバイスを検出 → 即決定\n")
            return best_idx, best_info

    print(f"\n自動選択: {best_info['name']}  (音量avg: {best_level})\n")
    return best_idx, best_info

import pyaudiowpatch as pyaudio
import numpy as np
import subprocess
import sys

PROBE_SEC        = 0.5   # 各デバイスを試す秒数
PROBE_TIMEOUT    = 3.0   # この秒数で応答しないデバイスはスキップ
ACTIVE_THRESHOLD = 100   # この音量avg以上なら即決定・残りのデバイスはスキップ


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


def _get_default_wasapi_output_name(pa: pyaudio.PyAudio) -> str:
    """
    Return the name of the default WASAPI output device, or "" on failure.
    Used to prefer the matching loopback device over others.
    """
    try:
        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out_idx = wasapi_info.get("defaultOutputDevice", -1)
        if default_out_idx >= 0:
            info = pa.get_device_info_by_index(default_out_idx)
            return info.get("name", "")
    except Exception:
        pass
    return ""


def select_active_device(pa: pyaudio.PyAudio) -> tuple[int, dict]:
    """
    ループバックデバイスを選択して返す。

    選択戦略（優先順）:
      1. デフォルト WASAPI 出力デバイスと名前が一致するループバック
      2. 音量プローブで最も音量が大きいループバック（有音なら即決定）
      3. 最初に見つかったループバック（無音 / プローブ失敗時のフォールバック）

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
        print(f"デバイス自動選択 (唯一): {info['name']}")
        return idx, info

    # ── Strategy 1: prefer loopback matching the default output ──────────────
    default_name = _get_default_wasapi_output_name(pa)
    if default_name:
        for dev_idx, dev_info in loopback_devices:
            # Loopback names are typically "<output name> [Loopback]"
            if default_name.lower() in dev_info["name"].lower():
                print(f"デバイス自動選択 (デフォルト出力一致): {dev_info['name']}")
                return dev_idx, dev_info

    # ── Strategy 2: probe each device for audio level ────────────────────────
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
            print(f"有音デバイスを検出 → 即決定")
            return best_idx, best_info

    # ── Strategy 3: fallback to highest-level (or first) ─────────────────────
    if best_level <= 0:
        # All probes returned 0 — fall back to first device and warn
        best_idx, best_info = loopback_devices[0]
        print(f"⚠ 全デバイスが無音 — 最初のデバイスを使用: {best_info['name']}")
        print(f"  会議ソフトが別の出力デバイスを使っている可能性があります。")
        print(f"  python debug_modules.py loopback を実行して確認してください。")
    else:
        print(f"\n自動選択: {best_info['name']}  (音量avg: {best_level})\n")
    return best_idx, best_info

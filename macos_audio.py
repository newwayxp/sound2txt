"""
macos_audio.py – CoreAudio routing automation for Sound2Text on macOS.

Recording start  → activate_loopback()
  1. Detect BlackHole 2ch.
  2. Destroy any previous "Sound2Text Multi-Output" aggregate device.
  3. Create a fresh one: current speakers + BlackHole 2ch (stacked / multi-output).
  4. Switch system default output to it.

Recording stop  → restore_loopback()
  5. Switch system default output back to the original speakers.
  (The Multi-Output Device is left in place so future sessions reuse it faster.)
"""

import ctypes, struct, os, json, sys, logging

if sys.platform != "darwin":
    raise ImportError("macos_audio is macOS-only")

_log = logging.getLogger("sound2text.macos_audio")

# ── Framework handles ──────────────────────────────────────────────────────────
_CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

# Set explicit restype / argtypes to avoid 32-bit truncation on Apple Silicon
_CA.AudioObjectGetPropertyDataSize.restype  = ctypes.c_int32
_CA.AudioObjectGetPropertyData.restype      = ctypes.c_int32
_CA.AudioObjectSetPropertyData.restype      = ctypes.c_int32
_CA.AudioHardwareCreateAggregateDevice.restype  = ctypes.c_int32
_CA.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p,
                                                    ctypes.POINTER(ctypes.c_uint32)]
_CA.AudioHardwareDestroyAggregateDevice.restype  = ctypes.c_int32
_CA.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]

_CF.CFStringCreateWithCString.restype  = ctypes.c_void_p
_CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_CF.CFStringGetCString.restype         = ctypes.c_bool
_CF.CFStringGetCString.argtypes        = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_uint32, ctypes.c_uint32]
_CF.CFDictionaryCreate.restype         = ctypes.c_void_p
_CF.CFDictionaryCreate.argtypes        = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.c_long,
                                          ctypes.c_void_p, ctypes.c_void_p]
_CF.CFArrayCreate.restype              = ctypes.c_void_p
_CF.CFArrayCreate.argtypes             = [ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p),
                                          ctypes.c_long, ctypes.c_void_p]
_CF.CFNumberCreate.restype             = ctypes.c_void_p
_CF.CFNumberCreate.argtypes            = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
_CF.CFRelease.restype                  = None
_CF.CFRelease.argtypes                 = [ctypes.c_void_p]


# ── Structures / FourCC ────────────────────────────────────────────────────────
class _PA(ctypes.Structure):
    """AudioObjectPropertyAddress"""
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]

def _fcc(s: str) -> int:
    return struct.unpack(">I", s.encode("ascii"))[0]

_SYS  = 1             # kAudioObjectSystemObject
_GLOB = _fcc("glob")  # kAudioObjectPropertyScopeGlobal
_DEVS = _fcc("dev#")  # kAudioHardwarePropertyDevices
_DOUT = _fcc("dOut")  # kAudioHardwarePropertyDefaultOutputDevice
_NAME = _fcc("lnam")  # kAudioObjectPropertyName
_UID  = _fcc("uid ")  # kAudioDevicePropertyDeviceUID
_UTF8 = 0x08000100    # kCFStringEncodingUTF8
_I32T = 3             # kCFNumberSInt32Type

_MULTI_UID  = "com.sound2text.multioutput.v1"
_MULTI_NAME = "Sound2Text Multi-Output"
_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".macos_audio_state")


# ── CoreAudio low-level ────────────────────────────────────────────────────────

def _prop_size(obj: int, sel: int) -> int:
    addr = _PA(sel, _GLOB, 0)
    sz = ctypes.c_uint32(0)
    _CA.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None, ctypes.byref(sz))
    return sz.value

def _get_u32(obj: int, sel: int) -> int:
    addr = _PA(sel, _GLOB, 0)
    sz = ctypes.c_uint32(4)
    val = ctypes.c_uint32(0)
    _CA.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                   ctypes.byref(sz), ctypes.byref(val))
    return val.value

def _set_u32(obj: int, sel: int, val: int) -> None:
    addr = _PA(sel, _GLOB, 0)
    v = ctypes.c_uint32(val)
    _CA.AudioObjectSetPropertyData(obj, ctypes.byref(addr), 0, None,
                                   ctypes.c_uint32(4), ctypes.byref(v))

def _get_cfstr(obj: int, sel: int) -> str:
    addr = _PA(sel, _GLOB, 0)
    sz  = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
    ref = ctypes.c_void_p(0)
    st  = _CA.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                         ctypes.byref(sz), ctypes.byref(ref))
    if st or not ref.value:
        return ""
    buf = ctypes.create_string_buffer(512)
    _CF.CFStringGetCString(ref.value, buf, 512, _UTF8)
    _CF.CFRelease(ref.value)
    return buf.value.decode("utf-8", errors="replace")

def _list_devices() -> list[int]:
    n = _prop_size(_SYS, _DEVS) // 4
    if n == 0:
        return []
    addr = _PA(_DEVS, _GLOB, 0)
    sz   = ctypes.c_uint32(n * 4)
    buf  = (ctypes.c_uint32 * n)()
    _CA.AudioObjectGetPropertyData(_SYS, ctypes.byref(addr), 0, None, ctypes.byref(sz), buf)
    return list(buf)


# ── Public device helpers ──────────────────────────────────────────────────────

def get_default_output() -> int:
    return _get_u32(_SYS, _DOUT)

def set_default_output(dev_id: int) -> None:
    _set_u32(_SYS, _DOUT, dev_id)

def device_name(dev_id: int) -> str:
    try:
        return _get_cfstr(dev_id, _NAME)
    except Exception:
        return f"<device {dev_id}>"

def device_uid(dev_id: int) -> str:
    try:
        return _get_cfstr(dev_id, _UID)
    except Exception:
        return ""

def find_by_name(substr: str) -> int | None:
    s = substr.lower()
    for d in _list_devices():
        try:
            if s in device_name(d).lower():
                return d
        except Exception:
            pass
    return None

def find_by_uid(uid: str) -> int | None:
    for d in _list_devices():
        try:
            if device_uid(d) == uid:
                return d
        except Exception:
            pass
    return None


# ── CoreFoundation helpers ─────────────────────────────────────────────────────

def _cfstr(s: str) -> int:
    return _CF.CFStringCreateWithCString(None, s.encode("utf-8"), _UTF8)

def _cfnum(n: int) -> int:
    v = ctypes.c_int32(n)
    return _CF.CFNumberCreate(None, _I32T, ctypes.byref(v))

def _build_agg_desc(speaker_uid: str, bh_uid: str) -> tuple[int, list[int]]:
    """
    Build CFDictionaryRef describing the stacked aggregate (Multi-Output) device.
    Key names come from <CoreAudio/AudioHardware.h>:
      kAudioAggregateDeviceNameKey        = "name"
      kAudioAggregateDeviceUIDKey         = "uid"
      kAudioAggregateDeviceIsStackedKey   = "stacked"
      kAudioAggregateDeviceSubDeviceListKey = "subdevices"
      kAudioAggregateDeviceMainSubDeviceKey = "master"
      kAudioSubDeviceUIDKey               = "uid"   (same as device uid key)

    Returns (cf_dict_ref, [all owned CF refs to release after device creation]).
    Uses NULL callbacks → caller must keep `owned` alive until after
    AudioHardwareCreateAggregateDevice returns.
    """
    owned: list[int] = []

    def s(v: str) -> int:
        r = _cfstr(v); owned.append(r); return r

    def n(v: int) -> int:
        r = _cfnum(v); owned.append(r); return r

    # ── Sub-device dicts: [{"uid": speaker_uid}, {"uid": bh_uid}] ────────────
    sub_uid_key = s("uid")

    _k1 = (ctypes.c_void_p * 1)(sub_uid_key)
    _v1 = (ctypes.c_void_p * 1)(s(speaker_uid))
    d1  = _CF.CFDictionaryCreate(None, _k1, _v1, 1, None, None); owned.append(d1)

    _k2 = (ctypes.c_void_p * 1)(sub_uid_key)
    _v2 = (ctypes.c_void_p * 1)(s(bh_uid))
    d2  = _CF.CFDictionaryCreate(None, _k2, _v2, 1, None, None); owned.append(d2)

    sub_items = (ctypes.c_void_p * 2)(d1, d2)
    sub_arr   = _CF.CFArrayCreate(None, sub_items, 2, None); owned.append(sub_arr)

    # ── Top-level description dict ────────────────────────────────────────────
    keys = [s(k) for k in ("name", "uid", "stacked", "subdevices", "master")]
    vals = [
        s(_MULTI_NAME),
        s(_MULTI_UID),
        n(1),          # stacked = 1 → Multi-Output Device (not Aggregate)
        sub_arr,
        s(speaker_uid),
    ]
    k_arr = (ctypes.c_void_p * 5)(*keys)
    v_arr = (ctypes.c_void_p * 5)(*vals)
    desc  = _CF.CFDictionaryCreate(None, k_arr, v_arr, 5, None, None)
    return desc, owned


def _destroy_existing() -> None:
    dev = find_by_uid(_MULTI_UID)
    if dev:
        st = _CA.AudioHardwareDestroyAggregateDevice(dev)
        _log.info(f"Destroyed existing Multi-Output Device id={dev} st={st}")


def _create_multi_output(speaker_uid: str, bh_uid: str) -> int:
    desc, owned = _build_agg_desc(speaker_uid, bh_uid)
    dev_id = ctypes.c_uint32(0)
    st = _CA.AudioHardwareCreateAggregateDevice(desc, ctypes.byref(dev_id))
    # Release all CF objects after device creation
    _CF.CFRelease(desc)
    for obj in owned:
        if obj:
            _CF.CFRelease(obj)
    if st:
        raise OSError(f"AudioHardwareCreateAggregateDevice → OSStatus {st:#010x}")
    _log.info(f"Created Multi-Output Device id={dev_id.value}")
    return dev_id.value


# ── State file ─────────────────────────────────────────────────────────────────

def _save_orig(dev_id: int) -> None:
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"original": dev_id}, f)

def _load_orig() -> int | None:
    if not os.path.exists(_STATE_PATH):
        return None
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return int(json.load(f).get("original", 0)) or None
    except Exception:
        return None

def _clear_state() -> None:
    try:
        os.remove(_STATE_PATH)
    except FileNotFoundError:
        pass


# ── Public API ────────────────────────────────────────────────────────────────

def activate_loopback(log_fn=None) -> bool:
    """
    Build Sound2Text Multi-Output (current speakers + BlackHole) and switch
    the system default output to it.  Returns True on success.
    """
    def _info(msg: str) -> None:
        _log.info(msg)
        if log_fn:
            log_fn(msg)

    try:
        bh = find_by_name("BlackHole 2ch")
        if bh is None:
            _info("[AudioRoute] BlackHole 2ch not found — skipping auto-routing")
            return False

        cur      = get_default_output()
        cur_uid  = device_uid(cur)
        cur_name = device_name(cur)

        if cur_uid == _MULTI_UID:
            _info("[AudioRoute] Already on Sound2Text Multi-Output")
            return True

        _save_orig(cur)
        _info(f"[AudioRoute] Current output: {cur_name}")

        _destroy_existing()
        multi = _create_multi_output(cur_uid, device_uid(bh))
        set_default_output(multi)
        _info(f"[AudioRoute] Switched → {_MULTI_NAME}")
        return True

    except Exception as e:
        _log.error(f"activate_loopback: {e}")
        if log_fn:
            log_fn(f"[AudioRoute] [WARN] 自動ルーティング失敗 (録音は継続): {e}")
        return False


def restore_loopback(log_fn=None) -> bool:
    """
    Restore system default output to what it was before activate_loopback().
    Returns True if restored.
    """
    def _info(msg: str) -> None:
        _log.info(msg)
        if log_fn:
            log_fn(msg)

    try:
        orig = _load_orig()
        if orig is None:
            return False

        if orig in _list_devices():
            set_default_output(orig)
            _info(f"[AudioRoute] Restored → {device_name(orig)}")
        else:
            _info(f"[AudioRoute] Original device id={orig} not found, skipping restore")

        _clear_state()
        return True

    except Exception as e:
        _log.error(f"restore_loopback: {e}")
        _clear_state()
        return False

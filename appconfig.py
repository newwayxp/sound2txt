"""
appconfig.py – Config file I/O and CUDA detection for Sound2Text.

No CTk / tkinter imports.  Safe to import from presenter, transcriber, etc.
"""
import configparser
import os
import subprocess
import shutil

# ── Path constants ─────────────────────────────────────────────────────────────

BASE        = os.path.dirname(os.path.abspath(__file__))
CFG_FILE    = os.path.join(BASE, "config.ini")
STOP_SIGNAL = os.path.join(BASE, ".stop_signal")
STATE_FILE  = os.path.join(BASE, ".last_transcript")
LANG_FILE   = os.path.join(BASE, ".last_language")
START_FILE  = os.path.join(BASE, ".recording_start")

# ── CUDA detection ─────────────────────────────────────────────────────────────

def _setup_cuda_dlls() -> None:
    """
    Dynamically find all CUDA toolkit installations and add their DLL
    directories to the search path.  Also creates cublas64_12.dll aliases
    inside the ctranslate2 package directory when only CUDA 13+ is installed,
    so that ctranslate2 (compiled for CUDA 11/12) can find cuBLAS at runtime.
    """
    if not hasattr(os, "add_dll_directory"):
        return

    cuda_dirs: list[str] = []

    # From environment variable
    cuda_root = os.environ.get("CUDA_PATH", "")
    if cuda_root:
        cuda_dirs += [os.path.join(cuda_root, "bin"),
                      os.path.join(cuda_root, "bin", "x64")]

    # Dynamic scan of common install base
    toolkit_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.isdir(toolkit_base):
        for ver in sorted(os.listdir(toolkit_base), reverse=True):
            cuda_dirs += [os.path.join(toolkit_base, ver, "bin"),
                          os.path.join(toolkit_base, ver, "bin", "x64")]

    for d in cuda_dirs:
        if os.path.isdir(d):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass

    # Create cublas64_12 alias if only cublas64_13 is present
    _ensure_cublas_compat(cuda_dirs)


def _ensure_cublas_compat(cuda_dirs: list[str]) -> None:
    """Copy cublas64_13.dll as cublas64_12.dll into ctranslate2's package dir
    so that ctranslate2 (compiled for CUDA 11/12) can find cuBLAS."""
    import ctypes

    # Already satisfied?
    for name in ("cublas64_12.dll", "cublas64_11.dll"):
        try:
            ctypes.CDLL(name); return
        except OSError:
            pass

    # Find the actual cublas DLL
    def _find(names: list[str]) -> str:
        for n in names:
            for d in cuda_dirs:
                p = os.path.join(d, n)
                if os.path.exists(p):
                    return p
        return ""

    src_cublas  = _find(["cublas64_13.dll",  "cublas64_12.dll",  "cublas64_11.dll"])
    src_cublaslt = _find(["cublasLt64_13.dll","cublasLt64_12.dll","cublasLt64_11.dll"])
    if not src_cublas:
        return

    try:
        import ctranslate2 as _ct2
        ct2_dir = os.path.dirname(_ct2.__file__)
    except Exception:
        return

    import shutil
    for src, alias in [(src_cublas, "cublas64_12.dll"),
                       (src_cublaslt, "cublasLt64_12.dll") if src_cublaslt else (None, None)]:
        if not src or not alias:
            continue
        dst = os.path.join(ct2_dir, alias)
        if not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass


def _detect_cuda() -> tuple[bool, bool]:
    """
    Return ``(cuda_available, cuda_libs_ok)``.

    *cuda_available* is True when an NVIDIA GPU is present.
    *cuda_libs_ok* is True when ctranslate2 can actually use CUDA.
    """
    # Try ctranslate2 first – most reliable
    try:
        _setup_cuda_dlls()
        import ctranslate2  # type: ignore
        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            return True, True
    except Exception:
        pass

    # Fallback: nvidia-smi
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, False
    try:
        result = subprocess.run(
            [exe, "-L"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and "GPU" in result.stdout:
            # GPU present but ctranslate2 CUDA libs may be missing
            return True, False
    except Exception:
        pass
    return False, False


_CUDA_AVAILABLE, _CUDA_LIBS_OK = _detect_cuda()


# ── AppConfig ─────────────────────────────────────────────────────────────────

class AppConfig:
    """Thin wrapper around ConfigParser with auto-section creation on set."""

    def __init__(self):
        self._cfg = configparser.ConfigParser()
        self._cfg.read(CFG_FILE, encoding="utf-8")

    # ── read helpers ──────────────────────────────────────────────────────────

    def get(self, section: str, key: str, fallback: str = "") -> str:
        return self._cfg.get(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        return self._cfg.getboolean(section, key, fallback=fallback)

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        return self._cfg.getint(section, key, fallback=fallback)

    def has_section(self, section: str) -> bool:
        return self._cfg.has_section(section)

    # ── write helpers ─────────────────────────────────────────────────────────

    def set(self, section: str, key: str, value: str) -> None:
        """Set *key* in *section*, creating the section if needed."""
        if not self._cfg.has_section(section):
            self._cfg.add_section(section)
        self._cfg.set(section, key, value)

    def save(self) -> None:
        """Write the current state to *CFG_FILE*."""
        with open(CFG_FILE, "w", encoding="utf-8") as fh:
            self._cfg.write(fh)

    def reload(self) -> None:
        """Re-read *CFG_FILE* from disk."""
        self._cfg.read(CFG_FILE, encoding="utf-8")

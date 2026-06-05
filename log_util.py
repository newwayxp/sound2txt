"""
Shared logging utility for Sound2Text subprocesses.

Each subprocess prints structured log lines to stdout:
    ||LOG||CATEGORY||LEVEL||message

The UI captures these lines, writes ALL to the log file,
and displays only lines matching the configured filters.

Categories : REC  TR  SUM  UI  SYS
Levels     : DEBUG  INFO  WARN  ERROR
"""
import os
import sys
import configparser
from datetime import datetime

# ── Constants ────────────────────────────────────────────────
CAT_REC  = "REC"
CAT_TR   = "TR"
CAT_SUM  = "SUM"
CAT_UI   = "UI"
CAT_SYS  = "SYS"

LVL_DEBUG = "DEBUG"
LVL_INFO  = "INFO"
LVL_WARN  = "WARN"
LVL_ERROR = "ERROR"

_LEVEL_ORDER = {LVL_DEBUG: 0, LVL_INFO: 1, LVL_WARN: 2, LVL_ERROR: 3}

# Structured log line prefix (parsed by UI)
_PREFIX = "||LOG||"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def log(category: str, level: str, message: str) -> None:
    """Print a structured log line to stdout (captured by UI)."""
    line = f"{_PREFIX}{category}||{level}||{_ts()}||{message}"
    print(line, flush=True)


# ── Convenience wrappers ──────────────────────────────────────

def rec_debug(msg): log(CAT_REC, LVL_DEBUG, msg)
def rec_info(msg):  log(CAT_REC, LVL_INFO,  msg)
def rec_warn(msg):  log(CAT_REC, LVL_WARN,  msg)
def rec_error(msg): log(CAT_REC, LVL_ERROR, msg)

def tr_debug(msg):  log(CAT_TR,  LVL_DEBUG, msg)
def tr_info(msg):   log(CAT_TR,  LVL_INFO,  msg)
def tr_warn(msg):   log(CAT_TR,  LVL_WARN,  msg)
def tr_error(msg):  log(CAT_TR,  LVL_ERROR, msg)

def sum_debug(msg): log(CAT_SUM, LVL_DEBUG, msg)
def sum_info(msg):  log(CAT_SUM, LVL_INFO,  msg)
def sum_warn(msg):  log(CAT_SUM, LVL_WARN,  msg)
def sum_error(msg): log(CAT_SUM, LVL_ERROR, msg)

def sys_debug(msg): log(CAT_SYS, LVL_DEBUG, msg)
def sys_info(msg):  log(CAT_SYS, LVL_INFO,  msg)
def sys_warn(msg):  log(CAT_SYS, LVL_WARN,  msg)
def sys_error(msg): log(CAT_SYS, LVL_ERROR, msg)


# ── UI-side: parse and filter ─────────────────────────────────

def parse_log_line(raw: str):
    """
    Parse a structured log line from subprocess stdout.
    Returns (category, level, timestamp, message) or None if not structured.
    """
    if not raw.startswith(_PREFIX):
        return None
    parts = raw[len(_PREFIX):].split("||", 3)
    if len(parts) != 4:
        return None
    cat, lvl, ts, msg = parts
    return cat.strip(), lvl.strip(), ts.strip(), msg.strip()


class LogConfig:
    """Reads logging configuration from config.ini."""

    def __init__(self, cfg: configparser.ConfigParser):
        base = os.path.dirname(os.path.abspath(sys.argv[0]))

        # Log file path
        default_log = os.path.join(
            cfg.get("paths", "transcript_dir",
                    fallback=r"C:\Users\Public\Sound2Text\transcript"),
            "..", "sound2txt.log"
        )
        self.log_file = os.path.normpath(
            cfg.get("logging", "log_file", fallback=default_log).strip()
        )
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)

        # Backend log level (written to file)
        self.log_level = cfg.get("logging", "log_level", fallback="DEBUG").strip().upper()

        # UI display: which categories to show
        ui_show_raw = cfg.get("logging", "ui_show",
                               fallback="REC,TR,SUM,UI,SYS").strip().upper()
        self.ui_show = {c.strip() for c in ui_show_raw.split(",") if c.strip()}

        # UI display: minimum level to show
        self.ui_level = cfg.get("logging", "ui_level", fallback="INFO").strip().upper()

    def should_write_file(self, level: str) -> bool:
        return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(self.log_level, 0)

    def should_show_ui(self, category: str, level: str) -> bool:
        if category not in self.ui_show:
            return False
        return _LEVEL_ORDER.get(level, 0) >= _LEVEL_ORDER.get(self.ui_level, 0)


class FileLogger:
    """Writes log lines to a file with rotation (max 10 MB)."""

    MAX_BYTES = 10 * 1024 * 1024  # 10 MB

    def __init__(self, path: str):
        self._path = path
        self._f = None
        self._open()

    def _open(self):
        try:
            self._f = open(self._path, "a", encoding="utf-8", buffering=1)
        except Exception as e:
            print(f"[LOG] Failed to open log file {self._path}: {e}", file=sys.stderr)

    def write(self, line: str) -> None:
        if not self._f:
            return
        try:
            self._f.write(line + "\n")
            # Rotate if too large
            if self._f.tell() > self.MAX_BYTES:
                self._f.close()
                backup = self._path + ".1"
                if os.path.exists(backup):
                    os.remove(backup)
                os.rename(self._path, backup)
                self._open()
        except Exception:
            pass

    def write_raw(self, category: str, level: str, ts: str, message: str) -> None:
        self.write(f"[{ts}] [{category:<3}] [{level:<5}] {message}")

    def close(self):
        if self._f:
            try:
                self._f.close()
            except Exception:
                pass

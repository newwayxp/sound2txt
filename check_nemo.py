"""Check whether NVIDIA NeMo ASR is already available.

The script is intentionally read-only: it does not install packages. It checks
the current Python first, then common sibling jidori-talk venv locations so the
heavy NeMo install is not duplicated when it already exists there.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _can_import(python_exe: Path) -> tuple[bool, str]:
    code = (
        "import sys\n"
        "try:\n"
        "    import nemo.collections.asr as nemo_asr\n"
        "    print('OK ' + sys.executable)\n"
        "except Exception as e:\n"
        "    print('NO ' + sys.executable + ' :: ' + str(e))\n"
        "    raise SystemExit(1)\n"
    )
    try:
        result = subprocess.run(
            [str(python_exe), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:
        return False, f"NO {python_exe} :: {exc}"
    output = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, output


def _candidate_pythons() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [Path(sys.executable)]
    sibling = here.parent / "jidori-talk"
    for name in (".venv", "venv", "env"):
        if os.name == "nt":
            candidates.append(sibling / name / "Scripts" / "python.exe")
        else:
            candidates.append(sibling / name / "bin" / "python")
    seen: set[Path] = set()
    out: list[Path] = []
    for item in candidates:
        try:
            resolved = item.resolve()
        except Exception:
            resolved = item
        if resolved not in seen and resolved.exists():
            seen.add(resolved)
            out.append(resolved)
    return out


def main() -> int:
    found = False
    for python_exe in _candidate_pythons():
        ok, message = _can_import(python_exe)
        print(message)
        found = found or ok
    if found:
        print("NeMo ASR is available in at least one checked environment.")
        return 0
    print("NeMo ASR was not found. Install only if you want Nemotron local backend.")
    print('Suggested install: pip install Cython packaging && pip install "nemo_toolkit[asr] @ git+https://github.com/NVIDIA/NeMo.git@main"')
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

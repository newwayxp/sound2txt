#!/usr/bin/env python3
"""Download the UI fonts at install time instead of bundling them.

Sound2Text needs four TTF files in the local ``fonts/`` directory
(loaded by run_gui.py via QFontDatabase.addApplicationFont):

    - ShareTechMono-Regular.ttf   (SIL OFL 1.1)
    - JetBrainsMono-Regular.ttf   (SIL OFL 1.1)
    - JetBrainsMono-Bold.ttf      (SIL OFL 1.1)
    - JetBrainsMono-BoldItalic.ttf(SIL OFL 1.1)

Both fonts are under the SIL Open Font License 1.1, which permits
redistribution; we download them from the upstream sources so they are
not committed to / shipped inside this repository's installer.

Proxy/SSL: honours the standard HTTPS_PROXY / HTTP_PROXY environment
variables (urllib reads them automatically). Set S2T_SSL_NO_VERIFY=1 to
skip certificate verification behind a TLS-intercepting corporate proxy.
"""
from __future__ import annotations

import io
import os
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Files the application actually loads at runtime.
SHARE_TECH = "ShareTechMono-Regular.ttf"
JETBRAINS = [
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Bold.ttf",
    "JetBrainsMono-BoldItalic.ttf",
]
REQUIRED = [SHARE_TECH, *JETBRAINS]

# Upstream sources (SIL OFL 1.1).
SHARE_TECH_URL = (
    "https://github.com/google/fonts/raw/main/ofl/sharetechmono/"
    "ShareTechMono-Regular.ttf"
)
JETBRAINS_ZIP_URL = (
    "https://github.com/JetBrains/JetBrainsMono/releases/download/"
    "v2.304/JetBrainsMono-2.304.zip"
)


def _ssl_context() -> ssl.SSLContext | None:
    if os.environ.get("S2T_SSL_NO_VERIFY") or os.environ.get(
        "HF_HUB_DISABLE_SSL_VERIFICATION"
    ):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Sound2Text-installer"})
    with urllib.request.urlopen(req, context=_ssl_context(), timeout=120) as resp:
        return resp.read()


def _missing() -> list[str]:
    return [f for f in REQUIRED if not (FONTS_DIR / f).exists()]


def main() -> int:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    missing = _missing()
    if not missing:
        print("All required fonts already present in fonts/ - skipping download.")
        return 0

    print(f"Downloading {len(missing)} missing font(s) into {FONTS_DIR} ...")

    # Share Tech Mono (single file)
    if SHARE_TECH in missing:
        try:
            print(f"  - {SHARE_TECH} ...", end=" ", flush=True)
            (FONTS_DIR / SHARE_TECH).write_bytes(_fetch(SHARE_TECH_URL))
            print("OK")
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED ({exc})")

    # JetBrains Mono (zip with fonts/ttf/*.ttf inside)
    jb_missing = [f for f in JETBRAINS if f in missing]
    if jb_missing:
        try:
            print("  - JetBrains Mono archive ...", end=" ", flush=True)
            data = _fetch(JETBRAINS_ZIP_URL)
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                wanted = set(jb_missing)
                for info in zf.infolist():
                    base = os.path.basename(info.filename)
                    if base in wanted:
                        (FONTS_DIR / base).write_bytes(zf.read(info))
                        wanted.discard(base)
            print("OK")
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED ({exc})")

    still_missing = _missing()
    if still_missing:
        print(
            "WARNING: could not download: "
            + ", ".join(still_missing)
            + "\nThe app still runs; UI just falls back to a default monospace font."
        )
        # Non-fatal: fonts are cosmetic, so do not block installation.
        return 0

    print("Fonts ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

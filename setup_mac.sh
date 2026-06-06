#!/bin/bash
set -e

echo "=== Sound2Text macOS Setup ==="
echo ""

# ── Homebrew check ────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "Homebrew is required but not found."
    echo "Install it from https://brew.sh, then re-run this script."
    exit 1
fi

# ── Detect Python (prefer Homebrew arm64, fallback to system) ─────────────────
PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY="$candidate"
        break
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3 not found. Install via: brew install python@3.11"
    exit 1
fi
PIP="${PY%python3}pip3"
command -v "$PIP" &>/dev/null || PIP="pip3"
echo "Using Python: $($PY --version)"

# ── System dependencies ───────────────────────────────────────────────────────
echo ""
echo "[1/4] Installing system dependencies..."

# ffmpeg and blackhole via brew (usually succeed)
brew install ffmpeg blackhole-2ch 2>/dev/null || true

# portaudio: try brew first, fall back to source build if formula is broken
PORTAUDIO_PREFIX="$HOME/portaudio"
if brew install portaudio 2>/dev/null; then
    echo "  portaudio installed via Homebrew"
    PA_INC="$(brew --prefix portaudio)/include"
    PA_LIB="$(brew --prefix portaudio)/lib"
else
    echo "  brew portaudio failed (known macOS 15 pkgconf issue) — building from source..."
    PA_TGZ="/tmp/portaudio.tgz"
    PA_SRC="/tmp/portaudio"
    curl -fSL "http://files.portaudio.com/archives/pa_stable_v190700_20210406.tgz" -o "$PA_TGZ"
    tar xzf "$PA_TGZ" -C /tmp
    cd "$PA_SRC"
    # Remove -Werror so unused-variable warnings don't abort the build on macOS 15
    ./configure --prefix="$PORTAUDIO_PREFIX" 2>/dev/null
    sed -i '' 's/-Werror//' Makefile 2>/dev/null || true
    make -j4
    make install prefix="$PORTAUDIO_PREFIX"
    # Also copy platform-specific header pyaudio needs
    cp include/pa_mac_core.h "$PORTAUDIO_PREFIX/include/" 2>/dev/null || true
    cd - >/dev/null
    PA_INC="$PORTAUDIO_PREFIX/include"
    PA_LIB="$PORTAUDIO_PREFIX/lib"
    echo "  portaudio built from source → $PORTAUDIO_PREFIX"
fi

# ── Python packages ───────────────────────────────────────────────────────────
echo ""
echo "[2/4] Installing Python packages..."
"$PIP" install faster-whisper scipy requests

# pyaudio needs portaudio headers/libs at build time
SDK="$(xcrun --show-sdk-path 2>/dev/null || echo '')"
SYSROOT="${SDK:+-isysroot $SDK}"
CFLAGS="-I$PA_INC $SYSROOT" LDFLAGS="-L$PA_LIB" "$PIP" install pyaudio

# Fix dylib path in the .so if portaudio was installed to a non-standard prefix
if [ "$PA_LIB" != "/usr/local/lib" ]; then
    SO_FILE=$("$PY" -c "import pyaudio, os; print(os.path.join(os.path.dirname(pyaudio.__file__), '_portaudio'+'$(python3 -c "import sysconfig; print(sysconfig.get_config_var(\"EXT_SUFFIX\"))" 2>/dev/null)'))" 2>/dev/null || echo "")
    if [ -z "$SO_FILE" ]; then
        SO_FILE=$(find /opt/homebrew /Library/Frameworks -name "_portaudio*.so" 2>/dev/null | head -1)
    fi
    if [ -n "$SO_FILE" ] && [ -f "$SO_FILE" ]; then
        install_name_tool -change /usr/local/lib/libportaudio.2.dylib \
            "$PA_LIB/libportaudio.2.dylib" "$SO_FILE" 2>/dev/null || true
        echo "  Fixed portaudio dylib path in $SO_FILE"
    fi
fi

# ── Config ────────────────────────────────────────────────────────────────────
echo ""
echo "[3/4] Setting up config..."
if [ ! -f config.ini ]; then
    cp config_default.ini config.ini
    echo "  Created config.ini from template."
    echo "  Edit config.ini and set your LLM API key under [summary] api_key."
else
    echo "  config.ini already exists, skipping."
fi

# ── BlackHole audio routing ───────────────────────────────────────────────────
echo ""
echo "[4/4] Configure BlackHole for system audio capture"
echo ""
echo "  Sound2Text captures meeting audio via a virtual loopback device."
echo "  You need a Multi-Output Device so your speakers AND BlackHole"
echo "  both receive audio at the same time."
echo ""
echo "  ┌─ Steps ──────────────────────────────────────────────────────────────┐"
echo "  │  1. Audio MIDI Setup will open now.                                  │"
echo "  │  2. Click the [+] button at the bottom-left corner.                  │"
echo "  │  3. Choose 「Create Multi-Output Device」.                            │"
echo "  │  4. In the right panel, check BOTH:                                  │"
echo "  │       ☑  BlackHole 2ch                                               │"
echo "  │       ☑  Your speakers (e.g. 'Mac mini Speakers', 'Q2790R3')        │"
echo "  │  5. Right-click the new device → 「Use This Device For Sound Output」│"
echo "  │     Or: System Settings → Sound → Output → 'Multi-Output Device'    │"
echo "  └──────────────────────────────────────────────────────────────────────┘"
echo ""
echo "  Opening Audio MIDI Setup..."
open "/Applications/Utilities/Audio MIDI Setup.app"
echo ""
read -rp "  ▶ Press Enter once you have completed all 5 steps above... "
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
echo "=== Setup complete ==="
echo ""
echo "To start Sound2Text:"
echo "  $PY ui_qt.py"

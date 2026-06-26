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
for candidate in python3.11 python3.12 python3.13 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if [[ "$ver" == "3.11" || "$ver" == "3.12" || "$ver" == "3.13" ]]; then
            PY="$candidate"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "ERROR: Python 3.11/3.12/3.13 not found. Install one via: brew install python@3.12"
    exit 1
fi
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python" ]; then
    current_ver=$("$VENV_DIR/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    if [[ "$current_ver" != "3.11" && "$current_ver" != "3.12" && "$current_ver" != "3.13" ]]; then
        echo "Existing venv uses unsupported Python $current_ver; recreating $VENV_DIR..."
        rm -rf "$VENV_DIR"
    fi
fi
if [ ! -d "$VENV_DIR" ]; then
    echo "Using Python: $($PY --version)"
    echo "Creating virtual environment at $VENV_DIR..."
    "$PY" -m venv "$VENV_DIR"
fi
PY="$VENV_DIR/bin/python"
# Use the venv's pip to avoid externally-managed environment issues.
"$PY" -m pip install --upgrade pip setuptools wheel

echo "Using Python: $($PY --version)"

echo ""
echo "[1/3] Installing system dependencies..."

# ffmpeg
brew install ffmpeg 2>/dev/null || true

# BlackHole 2ch: virtual loopback driver for system audio capture
echo "  Installing BlackHole 2ch..."
BH_INSTALLED=false
if brew install --cask blackhole-2ch 2>/dev/null; then
    BH_INSTALLED=true
else
    echo "  brew cask failed — downloading BlackHole .pkg from existential.audio..."
    BH_PKG="/tmp/BlackHole2ch.pkg"
    curl -fSL "https://existential.audio/downloads/BlackHole2ch.v0.5.0.pkg" \
        -o "$BH_PKG"
    echo "  Installing BlackHole .pkg (may require your password)..."
    sudo installer -pkg "$BH_PKG" -target / && BH_INSTALLED=true || true
    echo "  BlackHole installed."
fi

# Restart CoreAudio so the new BlackHole driver is detected immediately
if $BH_INSTALLED; then
    echo "  Restarting CoreAudio to load BlackHole driver..."
    sudo kill "$(pgrep coreaudiod)" 2>/dev/null || true
    sleep 2
    echo "  CoreAudio restarted."
fi

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
echo "[2/3] Installing Python packages..."
# Install from requirements_mac.txt, but handle pyaudio specially for build flags
if ! "$PY" -m pip install -r requirements_mac.txt; then
    echo "  Standard pip install failed; retrying with --user..."
    "$PY" -m pip install --user -r requirements_mac.txt
fi

# pyaudio needs portaudio headers/libs at build time; reinstall with proper flags
SDK="$(xcrun --show-sdk-path 2>/dev/null || echo '')"
SYSROOT="${SDK:+-isysroot $SDK}"
CFLAGS="-I$PA_INC $SYSROOT" LDFLAGS="-L$PA_LIB" "$PY" -m pip install --force-reinstall pyaudio 2>/dev/null || true

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

# ── UI fonts (SIL OFL 1.1, not bundled) ───────────────────────────────────────
echo ""
echo "  Downloading UI fonts (JetBrains Mono, Share Tech Mono)..."
if [ -f download_fonts.py ]; then
    "$PY" download_fonts.py || echo "  WARNING: font download failed — app will use a default monospace font."
fi

# ── Config ────────────────────────────────────────────────────────────────────
echo ""
echo "[3/3] Setting up config..."
if [ ! -f config.ini ]; then
    cp config_default.ini config.ini
    echo "  Created config.ini from template."
    echo "  Edit config.ini and set your LLM API key under [summary] api_key."
else
    echo "  config.ini already exists, skipping."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "  Audio routing is fully automatic:"
echo "  ▶ Start recording → system output switches to BlackHole + your speakers"
echo "  ■ Stop recording  → system output restored to original"
echo ""
echo "To start Sound2Text:"
echo "  $PY ui_qt.py"

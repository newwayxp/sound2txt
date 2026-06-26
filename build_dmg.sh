#!/bin/bash
# build_dmg.sh – Build Sound2Text macOS installer DMG
# Usage: bash build_dmg.sh
# Output: dist/Sound2Text-macOS.dmg
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/.build_tmp"
DIST_DIR="$SCRIPT_DIR/dist"
DMG_NAME="Sound2Text-macOS.dmg"
INSTALL_DIR='$HOME/Library/Sound2Text'   # literal $ – expanded at runtime on user machine

SOURCE_FILES=(
    ui_qt.py presenter.py pipeline.py appconfig.py
    i18n.py log_util.py summarizer.py device_utils.py
    macos_audio.py mic_recorder.py transcriber.py
    subtitle_window.py widgets_qt.py debug_modules.py
    config_default.ini
    requirements.txt requirements_mac.txt
    setup_mac.sh
    download_fonts.py
)

echo "=== Sound2Text – macOS DMG Builder ==="
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# ── 0. Generate app icon ──────────────────────────────────────────────────────
ICON_PATH="$SCRIPT_DIR/app_icon.icns"
if [ ! -f "$ICON_PATH" ]; then
    echo "Generating app icon..."
    python3 "$SCRIPT_DIR/gen_icon.py"
fi

# ── 1. Sound2Text.app (launcher placed in /Applications after install) ────────
LAUNCH_APP="$BUILD_DIR/Sound2Text.app"
mkdir -p "$LAUNCH_APP/Contents/MacOS"

cat > "$LAUNCH_APP/Contents/MacOS/Sound2Text" <<'EOF'
#!/bin/bash
INSTALL_DIR="$HOME/Library/Sound2Text"

if [ ! -f "$INSTALL_DIR/ui_qt.py" ]; then
    osascript -e 'display dialog "Sound2Text is not installed yet.\n\nPlease run \"Sound2Text Installer.app\" first." buttons {"OK"} default button "OK" with title "Sound2Text" with icon stop'
    exit 1
fi

PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if "$c" --version &>/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    osascript -e 'display dialog "Python 3 not found.\nPlease run \"Sound2Text Installer.app\" to complete setup." buttons {"OK"} default button "OK" with title "Sound2Text" with icon stop'
    exit 1
fi

cd "$INSTALL_DIR"
exec "$PY" ui_qt.py
EOF
chmod +x "$LAUNCH_APP/Contents/MacOS/Sound2Text"
mkdir -p "$LAUNCH_APP/Contents/Resources"
[ -f "$ICON_PATH" ] && cp "$ICON_PATH" "$LAUNCH_APP/Contents/Resources/app_icon.icns"

cat > "$LAUNCH_APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key>     <string>Sound2Text</string>
  <key>CFBundleIdentifier</key>     <string>com.sound2text.app</string>
  <key>CFBundleName</key>           <string>Sound2Text</string>
  <key>CFBundleDisplayName</key>    <string>Sound2Text</string>
  <key>CFBundleIconFile</key>       <string>app_icon</string>
  <key>CFBundleVersion</key>        <string>1.5.0</string>
  <key>CFBundleShortVersionString</key><string>1.5.0</string>
  <key>CFBundlePackageType</key>    <string>APPL</string>
  <key>LSMinimumSystemVersion</key> <string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Sound2Text uses the microphone for speech recognition.</string>
</dict></plist>
EOF

# ── 2. Sound2Text Installer.app ───────────────────────────────────────────────
INST_APP="$BUILD_DIR/Sound2Text Installer.app"
INST_RES="$INST_APP/Contents/Resources"
mkdir -p "$INST_APP/Contents/MacOS" "$INST_RES/source"

# Bundle source files
MISSING=()
for f in "${SOURCE_FILES[@]}"; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$INST_RES/source/"
    else
        MISSING+=("$f")
    fi
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Warning: missing source files: ${MISSING[*]}"
fi

# Bundle the launcher .app inside installer resources
cp -r "$LAUNCH_APP" "$INST_RES/"

# Installer executable
cat > "$INST_APP/Contents/MacOS/installer" <<'INSTALLER_EOF'
#!/bin/bash
RESOURCES="$(cd "$(dirname "$0")/../Resources" && pwd)"
INSTALL_DIR="$HOME/Library/Sound2Text"
LAUNCH_APP_SRC="$RESOURCES/Sound2Text.app"

# ── Welcome ───────────────────────────────────────────────────────────────────
BTN=$(osascript 2>&1 <<'OSASCRIPT'
display dialog "Sound2Text Installer

The following will be installed automatically:
  • Homebrew  (if not present)
  • BlackHole 2ch  (virtual audio driver)
  • ffmpeg + Python packages

Your administrator password will be requested once
for the BlackHole audio driver installation.

The Terminal window will show live progress." \
  buttons {"Cancel", "Install"} default button "Install" \
  with title "Sound2Text Installer" with icon note
OSASCRIPT
)
[[ "$BTN" != *"Install"* ]] && exit 0

# ── Copy source files to install location ─────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp -r "$RESOURCES/source/"* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/setup_mac.sh"

# ── Deploy Sound2Text.app to Applications ─────────────────────────────────────
if [ -w "/Applications" ]; then
    APP_DEST="/Applications/Sound2Text.app"
else
    mkdir -p "$HOME/Applications"
    APP_DEST="$HOME/Applications/Sound2Text.app"
fi
rm -rf "$APP_DEST"
cp -r "$LAUNCH_APP_SRC" "$APP_DEST"
APP_DIR="$(dirname "$APP_DEST")"

# ── Run setup in Terminal ──────────────────────────────────────────────────────
DONE_FILE="$(mktemp -u /tmp/s2t_done_XXXXXX)"

# Write the setup commands to a temp script to avoid quoting nightmares
SETUP_SCRIPT="$(mktemp /tmp/s2t_setup_XXXXXX.sh)"
cat > "$SETUP_SCRIPT" <<SETUP
#!/bin/bash
clear
printf '\033[1m╔══════════════════════════════════════════╗\n'
printf '║         Sound2Text  Setup                 ║\n'
printf '╚══════════════════════════════════════════╝\033[0m\n'
echo ''
cd '$INSTALL_DIR'
bash setup_mac.sh
STATUS=\$?
echo ''
if [ \$STATUS -eq 0 ]; then
    printf '\033[32m✅  Setup complete! You can close this window.\033[0m\n'
    touch '$DONE_FILE'
else
    printf '\033[31m❌  Setup failed. Please check the output above.\033[0m\n'
    touch '${DONE_FILE}.err'
fi
SETUP
chmod +x "$SETUP_SCRIPT"

osascript <<APPLESCRIPT
tell application "Terminal"
    activate
    set w to do script "bash '$SETUP_SCRIPT'"
    set custom title of w to "Sound2Text Setup"
end tell
APPLESCRIPT

# ── Wait dialog – user clicks OK after Terminal shows "Setup complete" ─────────
osascript <<'OSASCRIPT'
display dialog "Sound2Text is being installed.

Watch the Terminal window. When it shows:
  ✅  Setup complete!

Click OK to continue." \
  buttons {"OK"} default button "OK" \
  with title "Sound2Text Installer" with icon note
OSASCRIPT

# ── Completion ─────────────────────────────────────────────────────────────────
if [ -f "${DONE_FILE}.err" ]; then
    osascript -e 'display dialog "Setup encountered an error.\nPlease check the Terminal window for details." buttons {"OK"} default button "OK" with title "Sound2Text Installer" with icon stop'
    exit 1
fi

OPEN_BTN=$(osascript 2>&1 <<OSASCRIPT
display dialog "Installation complete! 🎉

Sound2Text.app has been added to:
  $APP_DIR

Click \"Launch\" to start Sound2Text now." \
  buttons {"Later", "Launch"} default button "Launch" \
  with title "Sound2Text Installer" with icon note
OSASCRIPT
)

rm -f "$SETUP_SCRIPT" "$DONE_FILE" "${DONE_FILE}.err"

[[ "$OPEN_BTN" == *"Launch"* ]] && open "$APP_DEST"
INSTALLER_EOF
chmod +x "$INST_APP/Contents/MacOS/installer"
[ -f "$ICON_PATH" ] && cp "$ICON_PATH" "$INST_RES/app_icon.icns"

cat > "$INST_APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key>     <string>installer</string>
  <key>CFBundleIdentifier</key>     <string>com.sound2text.installer</string>
  <key>CFBundleName</key>           <string>Sound2Text Installer</string>
  <key>CFBundleDisplayName</key>    <string>Sound2Text Installer</string>
  <key>CFBundleIconFile</key>       <string>app_icon</string>
  <key>CFBundleVersion</key>        <string>1.5.0</string>
  <key>CFBundleShortVersionString</key><string>1.5.0</string>
  <key>CFBundlePackageType</key>    <string>APPL</string>
  <key>LSMinimumSystemVersion</key> <string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF

# ── 3. README ─────────────────────────────────────────────────────────────────
STAGING="$BUILD_DIR/dmg_staging"
mkdir -p "$STAGING"
cp -r "$INST_APP" "$STAGING/"

cat > "$STAGING/README.txt" <<'EOF'
Sound2Text – macOS
══════════════════

INSTALL
  1. Double-click "Sound2Text Installer.app"

  ⚠️  First launch: macOS may show a security warning.
      Right-click the app → "Open" → "Open" to proceed.

  2. Click "Install" in the dialog.
  3. Watch the Terminal window – setup runs automatically.
  4. When Terminal shows "✅ Setup complete!", click OK.
  5. Click "Launch" to start Sound2Text.

WHAT GETS INSTALLED
  • ~/Library/Sound2Text/   source files
  • /Applications/Sound2Text.app   launcher
  • Homebrew, BlackHole 2ch, ffmpeg, Python packages

AFTER INSTALLATION
  Double-click Sound2Text.app in Applications to launch.
  Audio routing (BlackHole ↔ speakers) is fully automatic.

REQUIREMENTS
  macOS 12 Monterey or later · Apple Silicon or Intel
EOF

# ── 4. Build DMG ──────────────────────────────────────────────────────────────
echo ""
echo "Creating DMG..."
hdiutil create \
    -volname "Sound2Text" \
    -srcfolder "$STAGING" \
    -ov -format UDZO \
    "$DIST_DIR/$DMG_NAME" \
    -quiet

rm -rf "$BUILD_DIR"

echo ""
echo "✅  Done: dist/$DMG_NAME"
echo ""
echo "Test it with:"
echo "  open dist/$DMG_NAME"

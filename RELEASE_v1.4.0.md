# Sound2Text v1.4.0 Release Notes

**Release date**: 2026-06-06
**Previous release**: v1.3.14

---

## 🍎 macOS Support (New Platform)

Sound2Text now runs natively on macOS. System audio is captured via **BlackHole 2ch** virtual audio driver, and all core features — real-time transcription, AI correction, and meeting minutes generation — work the same as on Windows.

> **Recommended hardware: Apple Silicon Mac (M1/M2/M3/M4)**
> Apple Silicon provides significantly faster Whisper inference than Intel Macs.
> Intel Macs are supported but transcription speed will be slower.

### Supported environment

| Item | Requirement |
|---|---|
| OS | macOS 12 Monterey or later |
| Chip | **Apple Silicon (M1/M2/M3/M4) — recommended** · Intel (supported) |
| Python | Homebrew Python 3.10+ |
| Virtual audio | BlackHole 2ch (installed automatically by setup_mac.sh) |

---

## 🔊 Automatic Audio Routing (macOS)

No manual Audio MIDI Setup configuration required. Audio routing is fully automatic:

- Click **▶ Start** → the app detects your current speaker output, creates a Multi-Output Device (speakers + BlackHole), and switches system output to it automatically
- Click **■ Stop** → system output is restored to the original device

The app saves your original output device before switching and always restores it cleanly, even if the session ends unexpectedly.

---

## 📦 macOS Installer DMG

A self-contained DMG installer is included for easy distribution:

```
Sound2Text-macOS.dmg
├── Sound2Text Installer.app   ← double-click to install
└── README.txt
```

**Install flow:**
1. Double-click `Sound2Text Installer.app`
2. Click **Install** in the welcome dialog
3. A Terminal window opens and runs `setup_mac.sh` automatically (installs Homebrew, BlackHole, ffmpeg, Python packages)
4. After setup completes, click **Launch**

`Sound2Text.app` is deployed to `/Applications` automatically during installation.

To build the DMG from source:

```bash
bash build_dmg.sh
# Output: dist/Sound2Text-macOS.dmg
```

---

## 🛠 macOS Setup Script (`setup_mac.sh`)

`setup_mac.sh` handles the full environment setup on a new Mac:

- Installs **Homebrew** (if not present)
- Installs **BlackHole 2ch** virtual audio driver via Homebrew Cask (with direct `.pkg` fallback)
- Installs **ffmpeg** and **portaudio**
- Restarts CoreAudio so BlackHole is recognized immediately (no reboot required)
- Creates a Python **virtualenv** and installs all dependencies from `requirements_mac.txt`
- Copies `config_default.ini` → `config.ini`

```bash
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
bash setup_mac.sh
```

---

## 🐛 Bug Fixes

| Fix | Symptom |
|---|---|
| `os.path.expanduser()` missing in `pipeline.py` and `summarizer.py` | Output files were saved to literal `~/Documents/...` path instead of the actual home directory — transcript, corrected text, and meeting minutes were not generated |
| Output dirs created at startup | First session after a fresh install failed because output directories didn't exist yet |
| Language auto-detection loop | Language setting was re-detected in a loop causing unnecessary overhead |
| Local Mic mode on macOS | Mic-only recording mode did not work correctly on macOS |
| Platform-aware UI language detection | System language preference on macOS was not being read correctly |

---

## 📁 New Files

| File | Purpose |
|---|---|
| `macos_audio.py` | CoreAudio bindings for automatic Multi-Output Device management |
| `setup_mac.sh` | One-command environment setup for macOS |
| `build_dmg.sh` | Builds the distributable DMG installer |
| `gen_icon.py` | Generates `app_icon.icns` (blue waveform icon for .app bundles) |
| `app_icon.icns` | macOS app icon (blue gradient + 7-bar waveform) |
| `requirements_mac.txt` | macOS-specific Python dependencies |

---

## ⬆️ Upgrade from v1.3.x

**Windows users**: No changes required — all fixes are backwards-compatible.

**macOS (new install)**:

```bash
bash setup_mac.sh
```

No manual `config.ini` changes are needed.

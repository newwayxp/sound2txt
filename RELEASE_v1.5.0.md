# Sound2Text v1.5.0 Release Notes

**Release date**: 2026-06-06
**Previous release**: v1.3.14

---

## 🍎 macOS Support (New Platform)

Sound2Text now runs natively on macOS. System audio is captured via **BlackHole 2ch** virtual audio driver, and all core features — real-time transcription, AI correction, and meeting minutes generation — work identically to Windows.

> **Recommended hardware: Apple Silicon Mac (M1 / M2 / M3 / M4)**
> Apple Silicon provides significantly faster Whisper inference.
> Intel Macs are supported but transcription will be slower.

### Supported environment

| Item | Requirement |
|---|---|
| OS | macOS 12 Monterey or later |
| Chip | **Apple Silicon M1/M2/M3/M4 — recommended** · Intel (supported) |
| Python | Homebrew Python 3.10+ |
| Virtual audio | BlackHole 2ch (installed automatically by `setup_mac.sh`) |

---

## 🔊 Automatic Audio Routing (macOS)

No manual Audio MIDI Setup configuration required. Audio routing is fully automatic:

- Click **▶ Start** → the app detects your current speaker output, creates a Multi-Output Device (speakers + BlackHole), and switches the system output to it automatically
- Click **■ Stop** → system output is restored to the original device

The original output device is saved before switching and always restored cleanly, even if the session ends unexpectedly.

---

## 📦 macOS Installer DMG

A self-contained DMG installer is now provided for easy distribution.

```
Sound2Text-macOS.dmg
├── Sound2Text Installer.app   ← double-click to install
└── README.txt
```

**Install flow:**
1. Double-click `Sound2Text Installer.app`
2. Click **Install** in the welcome dialog
3. A Terminal window opens and runs `setup_mac.sh` automatically
   — installs Homebrew, BlackHole 2ch, ffmpeg, Python packages
4. When Terminal shows ✅ Setup complete!, click OK
5. Click **Launch** to start Sound2Text

`Sound2Text.app` is deployed to `/Applications` automatically.

To build the DMG from source:

```bash
bash build_dmg.sh
# Output: dist/Sound2Text-macOS.dmg
```

---

## 🛠 One-Command macOS Setup (`setup_mac.sh`)

For users installing from source, a single script handles everything:

```bash
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
bash setup_mac.sh
```

What it does:
- Installs **Homebrew** (if not present)
- Installs **BlackHole 2ch** via Homebrew Cask (direct `.pkg` fallback included)
- Installs **ffmpeg** and **portaudio**
- Restarts CoreAudio so BlackHole is recognized immediately — no reboot required
- Creates a Python **virtualenv** and installs all dependencies from `requirements_mac.txt`
- Copies `config_default.ini` → `config.ini`

---

## 🐛 Bug Fixes

| Fix | Symptom |
|---|---|
| `os.path.expanduser()` missing in `pipeline.py` / `summarizer.py` | Output files saved to literal `~/Documents/…` path — transcripts and meeting minutes were not generated on disk |
| Output directories created at startup | First session on a fresh install failed because output dirs didn't exist yet |
| Language auto-detection loop | System language was re-detected repeatedly during a session |
| Local Mic mode on macOS | Mic-only recording mode did not function correctly on macOS |
| macOS system language preference | UI language fell back incorrectly instead of reading the macOS locale |

---

## 📁 New Files

| File | Purpose |
|---|---|
| `macos_audio.py` | CoreAudio ctypes bindings — automatic Multi-Output Device creation and restoration |
| `setup_mac.sh` | One-command setup script for macOS |
| `build_dmg.sh` | Builds the distributable macOS DMG installer |
| `gen_icon.py` | Generates `app_icon.icns` from code (blue gradient + 7-bar waveform) |
| `app_icon.icns` | macOS app icon used by both `.app` bundles |
| `requirements_mac.txt` | macOS-specific Python dependency list |

---

## ⬆️ Upgrade Notes

**Windows users**: No action required — all changes are backwards-compatible.

**macOS (new install)**:

```bash
bash setup_mac.sh
```

No manual `config.ini` changes needed.

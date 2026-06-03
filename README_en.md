# Sound2Text

A Windows desktop tool for real-time audio transcription, AI-powered correction, and automatic meeting minutes generation.

Captures system audio via Windows WASAPI, performs local speech recognition with faster-whisper, and generates structured meeting minutes using an LLM.

English | [日本語](README_ja.md) | [简体中文](README_zh.md)

---

## Features

- **Real-time transcription** — Auto-detects audio output device, no manual setup required
- **Multi-language support** — Automatically detects Chinese / Japanese / English; skips detection on subsequent runs
- **GPU acceleration** — Automatically uses CUDA when an NVIDIA GPU is available (~10x faster)
- **AI correction** — LLM fixes homophones, adds punctuation, and organizes paragraphs
- **Meeting minutes** — One-click generation of structured minutes in the same language as the recording
- **Custom vocabulary** — `vocabulary.txt` improves recognition accuracy for domain-specific terms
- **Corporate proxy support** — Supports HTTP/HTTPS proxies and self-signed certificates

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.8 or later |
| RAM | 4 GB or more |
| GPU | Optional, NVIDIA CUDA (auto-used if available) |

---

## Installation

### Option 1: Installer (Recommended)

Download `Sound2Text_Setup_x.x.x.exe` from [Releases](../../releases) and run it.

> Python 3.8+ must be installed beforehand. Check **Add Python to PATH** during installation.

### Option 2: Manual Installation

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
```

---

## Usage

### GUI (Recommended)

```powershell
python ui.py
```

Click **▶ Start** to begin recording. Click **■ Stop** to end the session and automatically generate meeting minutes.

### Command Line

```powershell
python start.py
```

Press `Ctrl+C` to stop.

---

## Initial Setup (API Key)

After launching, enter your API Key in the **⚙ Summary/API** settings tab:

| Service | Where to get | Cost |
|---|---|---|
| **Groq** (recommended) | https://console.groq.com | 14,400 free requests/day |
| DeepSeek | https://platform.deepseek.com | Very low pay-per-use |
| Alibaba Cloud | https://bailian.console.aliyun.com | Free tokens for new users |
| Ollama (local) | Run locally, no API Key needed | Completely free |

---

## Output Files

| File | Location |
|---|---|
| Raw transcript | `Sound2Text\transcript\transcript_*.txt` |
| Corrected text | `Sound2Text\corrected\corrected_*.txt` |
| Meeting minutes | `Sound2Text\memo\summary_*.md` |
| Audio files | `Sound2Text\audio\audio_*.wav` |

Paths can be changed in the **📁 Paths** settings tab.

---

## Custom Vocabulary

Edit `vocabulary.txt`, one term per line:

```
Anthropic
ChatGPT
Docker
John Smith
```

Terms are passed to both Whisper's `initial_prompt` and the LLM correction prompt, improving recognition accuracy for proper nouns and technical terms.

---

## Corporate Proxy Settings

Configure in the **🌐 Network** settings tab, or edit `config.ini` directly:

```ini
[network]
https_proxy = http://proxy.company.com:8080
http_proxy  = http://proxy.company.com:8080
ssl_verify  = true   # Set to false for self-signed certificates
```

---

## Whisper Model Comparison

| Model | CPU Speed | GPU Speed | Accuracy |
|---|---|---|---|
| tiny | ~0.3s / 30s audio | ~0.1s | Low |
| small | ~3s / 30s audio | ~0.3s | Medium (recommended) |
| medium | ~15s / 30s audio | ~0.8s | High |
| large-v3 | ~40s / 30s audio | ~2s | Best |

Models can be changed in the **🎙 Recording** settings tab.

---

## Architecture

```
recorder.py      — Captures audio → saves audio_*.wav
transcriber.py   — Watches audio dir → transcribes → saves transcript_*.txt
summarizer.py    — Corrects text → generates meeting minutes
ui.py            — GUI controller (orchestrates all three)
start.py         — CLI launcher (same as UI but terminal-based)
```

---

## Troubleshooting

**No loopback device found**
> Go to `Control Panel → Sound → Recording tab → Stereo Mix → Enable`

**No transcription output**
> Run `record_test.py` to verify audio is being captured.

**FP16 warning**
> Automatically resolved when using GPU (CUDA). No impact on CPU mode.

**Building the installer**
> Double-click `build_installer.bat`. It searches for Inno Setup automatically and caches the path for future runs.

---

## Notice

> **Always obtain consent from all meeting participants before recording.**
> Unauthorized recording may violate applicable laws.

---

## Dependencies

| Library | Purpose |
|---|---|
| pyaudiowpatch | Windows WASAPI loopback recording |
| faster-whisper | Speech recognition (optimized Whisper) |
| customtkinter | GUI framework |
| requests | LLM API calls |
| ffmpeg | Audio format conversion |

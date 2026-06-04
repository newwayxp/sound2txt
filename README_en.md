# Sound2Text

Real-time audio transcription + AI correction + automatic meeting minutes for Windows.

Captures system audio and microphone via Windows WASAPI, transcribes locally with faster-whisper, and generates structured meeting minutes using an LLM.

English | [日本語](README_ja.md) | [简体中文](README_zh.md)

---

## Features

- **Real-time transcription** — Captures system audio output via WASAPI loopback, no manual device setup required
- **Microphone support** — Click the VU meter to toggle mic recording; transcripts are labeled with a speaker tag
- **Two recording modes** — Meeting mode (system audio + optional mic) and Local Mic mode (mic only)
- **Multi-language** — Auto-detects Chinese / Japanese / English; remembered for next session
- **GPU acceleration** — Automatically uses CUDA when an NVIDIA GPU is available (~10× speed boost)
- **AI correction** — LLM fixes homophones, adds punctuation, and cleans up paragraphs
- **Meeting minutes** — Generates structured minutes in the detected language
- **Custom vocabulary** — `vocabulary.txt` improves recognition of proper nouns and technical terms
- **Proxy support** — HTTP/HTTPS proxy and self-signed certificate support for corporate networks

---

## Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10 / 11 (64-bit) |
| Python | 3.10 or higher |
| RAM | 4 GB+ (8 GB recommended for large-v3) |
| GPU | Optional — NVIDIA CUDA (used automatically if available) |

---

## Installation

### Option 1: Run setup.bat (Recommended)

1. Install [Python 3.10+](https://www.python.org/downloads/) — check **Add Python to PATH**
2. Double-click `setup.bat`

The script installs all Python dependencies, ffmpeg, and creates `run.bat` + a desktop shortcut.

### Option 2: Manual

```powershell
git clone https://github.com/newwayxp/sound2txt.git
cd sound2txt
pip install -r requirements.txt
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
copy config_default.ini config.ini
```

---

## Launch

### Installed via the installer (.exe)

- **Start Menu** → click `Sound2Text` to launch
- If you chose to create a desktop icon during installation, double-click it to launch

### Installed via setup.bat

- Double-click the **Sound2Text shortcut** automatically created on your desktop
- Or open the install folder and double-click `run.bat`

### Command line (developers)

From the install folder:

```powershell
python ui_qt.py
```

---

## How to Use

### 1. Set API Key (first time)

Open the **Summary/API** tab and enter your LLM API key:

| Service | URL | Cost |
|---|---|---|
| **Groq** (recommended) | https://console.groq.com | 14,400 free requests/day |
| DeepSeek | https://platform.deepseek.com | Very cheap, pay-per-use |
| Alibaba Bailian | https://bailian.console.aliyun.com | Free credits for new users |
| Ollama (local) | Run locally, no key needed | Completely free |

### 2. Select recording mode

| Mode | Description |
|---|---|
| **Meeting** | Records system audio (others' voices) + optional microphone (your voice) |
| **Local Mic** | Records microphone only (lectures, narration) |

### 3. Start recording

Click the green **▶ Start** button → it turns into a red **■ Stop** button and the VU meter area appears.

### 4. Microphone ON AIR control

| Action | Result |
|---|---|
| Click the VU meter | Start mic recording — indicator dot turns red 🔴 |
| Click again | Stop mic recording — indicator turns blue 🔵 |

In Meeting mode, system audio is always recorded. The VU meter only controls the microphone.

### 5. Stop and generate minutes

Click **■ Stop** → the app automatically runs transcription → AI correction → meeting minutes generation → button returns to green **▶ Start**.

---

## Control Bar Layout

```
[▶Start/■Stop] | [Meeting][Local Mic] | [🔵 VU meter] | ... | Language [▼]
```

- **🔵/🔴 indicator** — Blue = idle, Red = mic recording active
- **VU meter** — Click to toggle mic, shows real-time audio level

---

## Output Files

| File | Default location |
|---|---|
| Raw transcript | `C:\Users\Public\Sound2Text\transcript\transcript_*.txt` |
| Corrected text | `C:\Users\Public\Sound2Text\corrected\corrected_*.txt` |
| Meeting minutes | `C:\Users\Public\Sound2Text\memo\summary_*.md` |
| Audio (system) | `C:\Users\Public\Sound2Text\audio\audio_*.wav` |
| Audio (mic) | `C:\Users\Public\Sound2Text\mic\mic_*.wav` |

Paths can be changed in the **Paths** settings tab.

---

## Whisper Model Comparison

| Model | CPU (30s audio) | GPU (30s audio) | Accuracy | Size |
|---|---|---|---|---|
| tiny | ~0.3s | ~0.1s | Low | 75 MB |
| small | ~3s | ~0.3s | Medium (recommended) | 244 MB |
| medium | ~15s | ~0.8s | High | 769 MB |
| large-v3 | ~40s | ~2s | Best | 1.5 GB |

Models are downloaded automatically on first use to `~/.cache/huggingface/`.

---

## Custom Vocabulary

Edit `vocabulary.txt`, one term per line:

```
Anthropic
Docker Compose
John Smith
quarterly review
```

Terms are passed to both Whisper's `initial_prompt` and the LLM correction prompt.

---

## Debug Tools

```powershell
python debug_modules.py loopback    # List loopback devices + 5s audio probe
python debug_modules.py audio       # Record 15s system audio → transcribe
python debug_modules.py mic         # Record 15s microphone → transcribe
python debug_modules.py pipeline    # Record both simultaneously → verify merge
python debug_modules.py audio 30    # Custom duration (seconds)
```

---

## File Structure

```
ui_qt.py            Main GUI window (PyQt6, MVP View)
widgets_qt.py       Custom QPainter widgets (VU meter, 7-segment clock)
presenter.py        Business logic (MVP Presenter)
appconfig.py        Config I/O + CUDA detection
start.py            Headless CLI launcher
recorder.py         System audio recording process (WASAPI loopback)
mic_recorder.py     Microphone recording process
transcriber.py      Transcription process (faster-whisper)
summarizer.py       AI correction + meeting minutes generation
device_utils.py     WASAPI loopback device auto-selection
debug_modules.py    Diagnostic test tool
config.ini          User config (machine-specific, do not commit)
config_default.ini  Default config template
vocabulary.txt      Custom terminology list
requirements.txt    Python dependencies
setup.bat           One-click setup script for new machines
```

---

## Proxy Configuration

In the **Network** settings tab or directly in `config.ini`:

```ini
[network]
https_proxy = http://proxy.company.com:8080
http_proxy  = http://proxy.company.com:8080
ssl_verify  = true   # set false for self-signed certificates
```

---

## Troubleshooting

**No loopback device found**
> Control Panel → Sound → Recording tab → Stereo Mix → Enable

**VU meter shows no activity**
> Run `python debug_modules.py loopback` to diagnose device selection.
> The meeting app may be using a different audio output device — check Windows default audio output.

**Model download is slow**
> Set `HF_ENDPOINT=https://hf-mirror.com` before running (mirror for mainland China).

---

## Notice

> **Always obtain consent from all meeting participants before recording.**
> Unauthorized recording may violate applicable laws.

---

## Dependencies

| Library | Purpose |
|---|---|
| pyaudiowpatch | Windows WASAPI loopback + microphone capture |
| faster-whisper | Speech recognition (optimized OpenAI Whisper) |
| PyQt6 | GUI framework |
| requests | LLM API calls |
| numpy | Audio data processing |
| ffmpeg | Audio decoding (used internally by faster-whisper) |

# Sound2Text — Architecture Reference

> Version: v1.3.12 (2026-06-04)  
> Update this file before each session-limit approaches, by reading only the files affected by recent changes.

---

## Overview

Windows desktop app: real-time audio capture → local Whisper transcription → LLM correction + meeting notes.

**Stack:** PyQt6 · faster-whisper · pyaudiowpatch (WASAPI) · requests · numpy · ffmpeg

---

## Process Architecture

Sound2Text uses **multi-process isolation** — each stage runs as a separate subprocess so a crash in one doesn't affect the others. Inter-process communication is done via **signal files** (not pipes or sockets).

```
┌─────────────────────────────────────┐
│  ui_qt.py  (main process, PyQt6)    │
│  ┌──────────────┐  ┌─────────────┐  │
│  │  presenter.py│  │ widgets_qt  │  │
│  │  (Presenter) │  │ (VU,7-seg)  │  │
│  └──────┬───────┘  └─────────────┘  │
└─────────┼───────────────────────────┘
          │ subprocess.Popen
    ┌─────┴──────────────────────────┐
    │         Child Processes        │
    │  recorder.py    (WASAPI loop)  │
    │  mic_recorder.py (PTT mic)     │
    │  transcriber.py  (Whisper)     │
    │  summarizer.py   (LLM)         │
    └────────────────────────────────┘
```

---

## MVP Pattern

```
ui_qt.py      View  — PyQt6 display only, no business logic
presenter.py  Presenter — all business logic, process management
appconfig.py  Model/Config — ConfigParser wrapper + CUDA detection
```

**Entry points:**
- `python ui_qt.py` — GUI mode
- `python start.py` — CLI mode (no GUI)

---

## File Map

| File | Role |
|------|------|
| `ui_qt.py` | PyQt6 main window (View). Implements `ViewProtocol`. |
| `widgets_qt.py` | Custom QPainter widgets: `VUMeterWidget`, seven-segment clock |
| `presenter.py` | All business logic + subprocess lifecycle. Defines `ViewProtocol`. |
| `appconfig.py` | `AppConfig` (ConfigParser wrapper), `BASE`/`CFG_FILE`/signal path constants, CUDA detection at import time |
| `i18n.py` | Translation dict + `t()` function, `_LANG` global |
| `recorder.py` | Child process: WASAPI loopback recording → WAV chunks to `audio_dir` |
| `mic_recorder.py` | Child process: microphone PTT recording → `mic_dir` |
| `transcriber.py` | Child process: watches `audio_dir`, transcribes WAVs with faster-whisper |
| `summarizer.py` | Child process: LLM correction (step 1/2) + meeting notes (step 2/2) |
| `device_utils.py` | WASAPI device auto-selection logic |
| `debug_modules.py` | Standalone diagnostic tests (loopback / audio / mic / pipeline / transcriber / summarizer / ui) |
| `start.py` | CLI launcher (spawns recorder + transcriber + summarizer without GUI) |
| `config_default.ini` | Template for `config.ini` (user-specific, gitignored) |

---

## Signal Files (IPC)

All files live in `BASE` (program directory) and are gitignored.

| File | Written by | Read by | Content |
|------|-----------|---------|---------|
| `.recording_start` | presenter + recorder | transcriber | Unix timestamp of recording start |
| `.stop_signal` | presenter | transcriber | Stop instruction |
| `.ptt_stop` | presenter | mic_recorder | PTT stop instruction |
| `.last_transcript` | transcriber | presenter | Path to output `.txt` |
| `.last_language` | transcriber | (unused) | Detected language code |

---

## Thread Safety

The presenter runs background threads (VU meter, transcription monitoring, stop sequence). UI updates must go through signals, not direct calls.

```python
_log_signal  = pyqtSignal(str)    # put_log() — safe from any thread
_call_signal = pyqtSignal(object) # schedule(fn) — safe from any thread
```

`QTimer.singleShot(0, fn)` does **not** work from non-main threads in Qt6.  
All cross-thread UI updates must use `_call_signal.emit(fn)`.

---

## ViewProtocol

`presenter.py` defines the interface the View must implement. Key methods:

| Method | Effect |
|--------|--------|
| `set_start_enabled(True)` | Green "▶ Start" button, `_btn_toggle_recording = False` |
| `set_stop_enabled(True)` | Red "■ Stop" button, `_btn_toggle_recording = True` |
| `set_stop_enabled(False)` | Only disables when in Stop mode (no-op in Start mode) |
| `show_onair()` | ON AIR dot → red #E53935 |
| `hide_onair()` | ON AIR dot → blue #1565C0 + forces `_vu_meter.set_level(0.0)` |
| `show_ptt_button()` | Show VU bar + call `_vu_meter.show()` |
| `hide_ptt_button()` | `_vu_meter.hide()` then hide VU bar |
| `schedule(fn)` | Run `fn` on UI main thread via `_call_signal` |
| `put_log(msg)` | Append to log panel via `_log_signal` |

---

## Control Bar Layout

```
[▶/■ toggle] | [会议模式][本地Mic] | [🔵 VU box] | spacer | [Language combo]
```

- **Toggle button** (`_btn_toggle`): 34px tall, green=idle, red=recording
- **VU container** (`_vumeter_bar`): QFrame dark-navy #0d1b2a, rounded, visible only when recording
- **ON AIR dot** (`_onair_dot`): 22×22 CSS circle LED
- **VU meter** (`VUMeterWidget`): pill shape, 40px tall, blue gradient; click → `presenter.toggle_mic()`

---

## Key Flows

### Recording Start (`presenter.start()`)
1. Delete `.stop_signal` / `.ptt_stop`
2. Write `time.time()` → `.recording_start`
3. Meeting mode: spawn `recorder.py` (WASAPI loopback)
4. Both modes: `hide_onair()` + `show_ptt_button()` (VU bar appears)
5. Mic is **not** auto-started — user clicks VU meter

### Mic Toggle (`presenter.toggle_mic()`)
- If `mic_proc` alive → `stop_mic()` → `_stop_meter()` → `hide_onair()` → write `.ptt_stop` → wait 5s
- Otherwise → `start_mic()` → spawn `mic_recorder.py --ptt` → `show_onair()` → `_start_meter()`

### Recording Stop → Post-processing (`_after_trans_impl`)
1. Wait for transcriber to finish
2. Run `summarizer.py` correction (step 1/2)
3. Run `summarizer.py` summary (step 2/2)
4. Call `_set_controls_idle()` → `set_start_enabled(True)` + `dashboard_reset()`

### VU Meter Race Prevention
- `_meter_loop` re-checks `self._meter_active` after `stream.read()` before calling `schedule()`
- `hide_onair()` forces `_vu_meter.set_level(0.0)` to neutralize any in-flight level update

---

## Configuration (`config.ini`)

Key sections:

| Section | Purpose |
|---------|---------|
| `[model]` | `backend` (cuda/cpu), `size` (tiny/small/medium/large-v3) |
| `[paths]` | `audio_dir`, `transcript_dir`, `corrected_dir`, `memo_dir`, `mic_dir`, `vocab_file` |
| `[llm]` | `api_key`, `api_url`, `model` |
| `[network]` | `https_proxy`, `http_proxy`, `ssl_verify` |
| `[app]` | `language`, `mode` (meeting/mic) |

`config.ini` is gitignored; `config_default.ini` is the committed template.

---

## CUDA Compatibility

`appconfig.py` runs `_detect_cuda()` at import time → sets `_CUDA_AVAILABLE`, `_CUDA_LIBS_OK`.

Handles CUDA 13+ / ctranslate2 (compiled for CUDA 11/12) mismatch by copying  
`cublas64_13.dll` → `cublas64_12.dll` inside ctranslate2's package directory.

Child processes inherit CUDA DLL paths via `PATH` env var set in `Presenter.__init__`.

---

## Output File Locations (defaults)

| Output | Path |
|--------|------|
| Raw transcript | `C:\Users\Public\Sound2Text\transcript\transcript_*.txt` |
| Corrected text | `C:\Users\Public\Sound2Text\corrected\corrected_*.txt` |
| Meeting notes | `C:\Users\Public\Sound2Text\memo\summary_*.md` |
| System audio | `C:\Users\Public\Sound2Text\audio\audio_*.wav` |
| Mic audio | `C:\Users\Public\Sound2Text\mic\mic_*.wav` |
| Vocabulary | `C:\Users\Public\Sound2Text\corrected\vocabulary.txt` |

---

## Installer

- Script: `installer.iss` (Inno Setup), version 1.3.11
- Build: `build_installer.bat` → `dist/Sound2Text_Setup_1.3.11.exe`
- Entry: `ui_qt.py` (old `ui.py` removed)
- Python requirement: 3.10+

---

## Debug Tools

```powershell
python debug_modules.py loopback    # List WASAPI devices + 5s probe
python debug_modules.py audio [N]   # Record Ns system audio → transcribe
python debug_modules.py mic [N]     # Record Ns mic → transcribe
python debug_modules.py pipeline [N]# Both simultaneously → merge check
python debug_modules.py transcriber # Transcribe existing WAV
python debug_modules.py summarizer  # Summarize existing transcript
python debug_modules.py ui          # 3s UI launch test
```

**pipeline note:** PyAudio `Pa_Initialize` is not thread-safe — open both streams on the main thread first, then read in threads.

---

## Gitignored Runtime Files

```
config.ini          # user settings (contains API key)
run.bat             # generated by setup.bat
vocabulary.txt      # user-editable term list (program dir copy)
*.log
.recording_start / .stop_signal / .ptt_stop
.last_transcript / .last_corrected / .last_language
*.wav
```

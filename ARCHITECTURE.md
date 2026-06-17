# Sound2Text — Architecture Reference

> Version: v1.5.2 (updated 2026-06-17)
> Update this file before each session-limit approaches, by reading only the files affected by recent changes.

---

## Overview

Windows desktop app: real-time audio capture → local Whisper transcription → per-segment LLM correction + meeting notes.

**Stack:** PyQt6 · faster-whisper · pyaudiowpatch (WASAPI) · requests · numpy · ffmpeg

---

## Process Architecture

Sound2Text uses **multi-process isolation** — the UI and the heavy audio/ML work
run as separate processes so a crash in one doesn't take down the other.
Inter-process communication is done via **signal files** (not pipes or sockets).

```
┌─────────────────────────────────────┐
│  ui_qt.py  (main process, PyQt6)    │
│  ┌──────────────┐  ┌─────────────┐  │
│  │  presenter.py│  │ widgets_qt  │  │
│  │  (Presenter) │  │ (VU,7-seg)  │  │
│  └──────┬───────┘  └─────────────┘  │
└─────────┼───────────────────────────┘
          │ subprocess.Popen + signal files
   ┌──────┴───────────────────────────────────┐
   │              Child processes              │
   │  pipeline.py        long-lived recording  │
   │                     core (see below)      │
   │  summarizer.py      LLM meeting minutes    │
   │  subtitle_window.py live subtitle overlay  │
   │  transcriber.py     on-demand file → text  │
   └───────────────────────────────────────────┘
```

`pipeline.py` is **the** real-time engine. It is started once (long-lived) and
reused across recordings; a recording "session" is toggled with signal files
rather than by restarting the process.

> **Legacy:** earlier versions split recording across `recorder.py` (WASAPI
> loopback) + `mic_recorder.py` (PTT mic) + a file-watching `transcriber.py`
> loop. That path is gone — `recorder.py` is deleted, mic capture now lives
> inside `pipeline.py`, and `mic_recorder.py` remains only as a standalone tool.
> `transcriber.py` is still used for *on-demand* transcription of dropped-in
> audio files (not the live path) and exports `LANG_ALIAS`.

---

## Real-time pipeline (`pipeline.py`)

A single long-lived process containing several threads:

```
main thread        : session lifecycle via signal files; start/stop/drain/finalize
system-capture    : WASAPI loopback capture → write .tmp_audio_<ts>.raw
                     → resample 16k mono chunks → system audio queue
system-vad        : consumes system audio queue → AEC reference update
                     → AccumulatingVAD → on VAD flush → _enqueue(seg, "system")
mic thread         : microphone capture (On Air / .mic_onair) → independent VAD
                     • AEC echo suppression vs. recent system audio
                     • on flush → _enqueue(seg, "mic")
transcribe worker  : pops a segment FILE PATH from the queue → Whisper →
                     hallucination/low-conf filter → append raw transcript →
                     enqueue text correction, delete seg file
correct worker     : consumes text correction queue → per-segment LLM correction →
                     glossary fix → append corrected transcript
post-session refine: optional online industry-term refinement in summarizer.py;
                     writes final_corrected_<ts>.txt without overwriting corrected
MP3 thread(s)      : WAV → MP3 (libmp3lame) in background at session end
```

The system-audio path intentionally separates recording from boundary detection:
capture keeps reading the device and writing the full session raw file, while
`system-vad` decides sentence/segment boundaries and queues only completed
segments for transcription. Whisper transcription and LLM correction are also
separate workers: raw transcript text appears as soon as Whisper completes, and
the correction worker updates the corrected file later without blocking the next
Whisper segment. Session stop first disables capture, drains the system audio
queue, force-flushes VAD, then waits for both transcription and correction queues.
After that, `summarizer.py --step summary` may run the optional online industry
term refinement before generating minutes.

### Optional online industry-term refinement

Controlled by `[summary] enable_online_refine` (default `false`). When enabled,
the post-session summary step:

1. Reads the real-time `corrected_<ts>.txt` file.
2. Uses the configured LLM backend to infer the meeting domain and 3-8 search
   keywords.
3. Downloads terminology candidates from the white-listed Wikipedia OpenSearch
   API and caches the raw API responses under `[summary] term_cache_dir`
   (default `~/Documents/Sound2Text/term_cache`).
4. Runs a conservative full-transcript refinement using those terms.
5. Writes `final_corrected_<ts>.txt` and records it in `.last_final_corrected`.
6. Generates `summary_<ts>.md` from the final corrected file.

The original `transcript_<ts>.txt` and real-time `corrected_<ts>.txt` are never
overwritten. If the online lookup or final refinement fails, the system falls
back to the existing corrected file and still generates minutes.

### VAD = Silero v6 via `AccumulatingVAD`

`AccumulatingVAD` (system and mic each own one) decides end-of-speech and emits a
segment when either: (A) `silence_sec` of silence follows ≥ `min_accum_sec` of
speech, or (B) the window exceeds `max_sec` (force flush).

The speech/silence decision comes from **Silero v6**, loaded via faster-whisper's
`get_vad_model()` (the path-based constructor — `SileroVADModel()` with no args
raises) and called with `num_samples=512`. Two traps the current code guards
against — **do not regress these**:

- **512-sample framing.** Silero processes audio in fixed 512-sample frames. A
  capture read resampled 48k→16k is often *smaller* than one frame (~341 samples),
  so a naive per-chunk call never runs. `_is_speech` accumulates chunks in
  `_vad_buf` until ≥ 512 samples are available, runs whole frames, and keeps the
  sub-frame remainder; between frame boundaries it reuses the last decision.
- **No silent fallback masking a load failure.** If Silero fails to load, the code
  drops to a crude amplitude check (`|chunk|.mean() ≥ 300`) — which misclassifies
  *quiet speech as silence*. `has_model()` distinguishes the two: the silence-skip
  below only fires when the reliable Silero path is active, so a fallback never
  drops real speech.

### Segment queue = **disk cache of file paths** (not in-RAM PCM)

Each VAD segment is written to its own small WAV file under
`audio_dir/.seg_cache_<ts>/seg_NNNNNN_<source>.wav`, and **only the path** is put
on the queue. Rationale:

- Queuing raw PCM let the queue grow without bound when transcription fell behind
  (slow CPU / large model) → memory blow-up.
- Capping the queue (`maxsize=N`) instead blocked the capture thread → WASAPI
  buffer overflow = dropped audio + stalled `.raw` writes.
- File paths are tiny, so the queue can stay **unbounded**: capture never blocks,
  no audio is lost, and RAM stays flat regardless of backlog. Disk use is bounded
  by session length; files are deleted as the worker consumes them.

The segment file is fully written **and closed before** it is queued, so the
worker can never read a half-written file ("don't process the file still being
recorded"). The session `.raw` is likewise never touched by the worker — it is
converted to WAV/MP3 only at session end, after the queue is drained.

### Timestamps

Line prefix `[HH:MM:SS]` is the **segment end time** (when the speech finished),
captured in the capture thread at enqueue as the flush wall-clock (`time.time()`).
It is shared by both the raw transcript and the corrected line, so a 100-second
transcription lag does **not** shift the displayed time — stop time and the last
transcript timestamp stay consistent. (It was previously the segment *start*
(`now - segment_duration`), which lagged the visible text by up to one full
`max_sec` window — e.g. 8 s when speech is continuous and every segment is a
force-flush.)

### Silence skip (throughput)

At the `max_sec` force-flush, a window with `speech_dur < min_speech_sec`
(default 0.5 s, config `[subtitle] min_speech_sec`) is **dropped from
transcription** (it is silence/music: Whisper returns nothing yet can burn 100 s+
on it via temperature fallback). The audio is still in the session recording, so
nothing is lost from the MP3.

### Decoding params (`_make_transcribe_kwargs`)

`beam_size = 1` on CPU (greedy — several times faster, the difference between
keeping up with real time and an ever-growing backlog), `5` on CUDA.
`temperature` is always a **list**, never scalar `0.0`. CPU uses `[0.0]` to avoid
temperature fallback storms on hard/noisy segments; CUDA keeps
`[0.0, 0.2, …, 1.0]`.

On CPU, language-specific `[models]` overrides pointing to heavy `large*` models
are skipped unless the base `[recording] model_size` is also that heavy model.
This prevents `model_size=medium` + `[models] ja=large-v3` from silently running
large-v3 on CPU and falling far behind real time. Long repeated-loop
hallucinations are filtered before they reach the transcript or LLM correction.

---

## MVP Pattern

```
ui_qt.py      View  — PyQt6 display only, no business logic
presenter.py  Presenter — all business logic, process management
appconfig.py  Model/Config — ConfigParser wrapper + lazy CUDA detection
```

**Entry points:**
- `python ui_qt.py` — GUI mode (default)
- `python start.py [--model M] [--llm M] [--language L] [--seconds N]` — CLI mode
  (auto-start, no GUI). `--seconds N` auto-stops after N s then waits for
  processing (non-interactive testing); otherwise stop with Ctrl+C.

---

## File Map

| File | Role |
|------|------|
| `ui_qt.py` | PyQt6 main window (View). Implements `ViewProtocol`. |
| `widgets_qt.py` | Custom QPainter widgets: `VUMeterWidget`, seven-segment clock |
| `presenter.py` | All business logic + subprocess lifecycle. Defines `ViewProtocol`. |
| `pipeline.py` | **Real-time engine**: loopback + mic capture, VAD, segment disk-cache queue, Whisper, per-segment LLM correction, glossary, raw→WAV→MP3, transcript/corrected files |
| `appconfig.py` | `AppConfig` (ConfigParser wrapper), `BASE`/`CFG_FILE`/signal path constants, lazy CUDA detection |
| `glossary.py` | Deterministic 誤→正 replacement (`resolve_glossary_file`/`load_glossary`/`apply_glossary`); works even when the LLM is down |
| `i18n.py` | Translation dict + `t()` function, `_LANG` global |
| `summarizer.py` | Child process: LLM correction (legacy step) + meeting notes from the live corrected file |
| `subtitle_window.py` | Child process: live subtitle overlay window (PyQt6) |
| `transcriber.py` | On-demand transcription of existing audio files; exports `LANG_ALIAS` |
| `device_utils.py` | WASAPI device auto-selection (`select_active_device`) |
| `log_util.py` | File + UI logging (`sys_info`/`tr_info`/`tr_debug`/…) |
| `start.py` | CLI launcher (auto-start, `--model`/`--llm`/`--language`/`--seconds`) |
| `debug_modules.py` | Standalone diagnostic tests |
| `mic_recorder.py` | Legacy standalone PTT mic recorder (not in the live path) |
| `config_default.ini` | Template for `config.ini` (user-specific, gitignored) |
| `CODEX_MEMORY.md` | Compact Codex working notes; `ARCHITECTURE.md` remains the source of truth |

---

## Signal Files (IPC)

All files live in `BASE` (program directory) and are gitignored. Written/removed
by the presenter unless noted.

| File | Direction | Meaning |
|------|-----------|---------|
| `.pipeline_session` | presenter → pipeline | Session active. Created on start; **removing it ends the session** (flush → drain queue → finalize). |
| `.pipeline_stop` | presenter → pipeline | Stop the long-lived pipeline process (app exit). |
| `.pipeline_session_done` | pipeline → presenter | Session fully finalized (queue drained, audio converted). Presenter waits for this before summarizing. |
| `.pipeline.lock` | pipeline/presenter | Cross-process singleton lock; prevents GUI/CLI from running multiple `pipeline.py` instances against the same signal files. |
| `.pipeline_subtitle` | presenter → pipeline | Subtitle mode active. |
| `.mic_onair` | presenter → pipeline | On Air: feed mic into the VAD. |
| `.recording_start` | presenter/pipeline | Unix timestamp of recording start. |
| `.last_transcript` | pipeline → presenter | Path to the live raw transcript `.txt` (polled for the Transcript tab). |
| `.last_corrected` | pipeline → presenter | Path to the live corrected `.txt` (polled for the Corrected tab; also the minutes input). |
| `.last_final_corrected` | summarizer → presenter | Path to the optional post-session `final_corrected_<ts>.txt`. |
| `.last_language` | pipeline | Detected/locked language code (reused next session). |

`presenter.start()` deletes stale session signals (`.pipeline_stop`,
`.last_transcript`, `.last_language`, `.mic_onair`, `.last_corrected`) before
writing `.pipeline_session`, so a polling thread can never show last session's
content. **Add any new session signal to that cleanup list.**

If another Presenter already owns a running `pipeline.py` (for example GUI is
open and CLI is started), the new Presenter reuses that external pipeline and
does **not** write `.pipeline_stop` on exit. Only the Presenter that spawned the
pipeline should stop it.

---

## Key Flows

### Recording start (`presenter.start()`)
1. Delete stale session signal files.
2. Write `time.time()` → `.recording_start`.
3. Write `.pipeline_session`; `_ensure_pipeline_running()` spawns `pipeline.py`
   if not already alive.
4. Start the `_poll_corrected_file` thread (3 s poll of `.last_transcript` /
   `.last_corrected` → Transcript / Corrected tabs).
5. Mic is **not** auto-started — user clicks the VU meter (`.mic_onair`).

> **Live config reload:** at each session start `pipeline.py` re-reads
> `language` / `model_size` / `device` from `config.ini` and reloads the Whisper
> model if any changed — settings edits take effect on the next recording with no
> app restart.

### Recording stop (`presenter.stop()` → drain → minutes)
1. Remove `.pipeline_session`. Pipeline flushes remaining audio, then
   `_seg_queue.join()` **fully drains the transcription backlog** (no timeout),
   converts raw → WAV → MP3, cleans the seg cache, writes `.pipeline_session_done`.
2. `_wait_pipeline_and_summarize` waits for `.pipeline_session_done` **as long as
   the pipeline process is alive** (poll + generous ceiling, ~3600 s), not a short
   fixed timeout. A premature timeout used to touch the still-open `.raw`
   (WinError 32) and summarize an incomplete transcript.
3. Minutes are generated from the live corrected file (`.last_corrected`), then
   `_set_controls_idle()` re-enables Start.

### Window close (`on_close` → `_graceful_shutdown`)
- If recording, `stop()` first; wait for `_running` to clear while the pipeline is
  alive (ceiling ~3600 s); then `_stop_pipeline(timeout=1800)` (pipeline drains
  fully on exit) and stop ancillary processes; finally destroy the window.
- A **second** close press force-quits immediately.
- Timeouts are deliberately generous: with weak hardware a stop can leave ~20 min
  of buffered audio that takes 30–40 min to transcribe; cutting it off would lose
  the tail.

### Mic toggle (On Air)
- VU click → toggles `.mic_onair`. While on, the mic thread feeds its own VAD;
  segments are transcribed separately and merged into the corrected file by
  timestamp. Mic PCM is also kept for MP3 mixing (`adelay` per On-Air offset).

---

## Dashboard (live timers)

`DashboardWidget` (`widgets_qt.py`) shows three seven-segment clocks during a
session:

| Timer | Color | Source |
|-------|-------|--------|
| **Elapsed** | cyan | wall-clock since `dashboard_start()`, driven by an internal 1 s `QTimer` |
| **Audio Rec** | green | total segment audio whose WAV cache file has been fully written and queued |
| **Transcribed** | amber | total segment audio that has been transcribed, corrected/glossaried, and appended to the corrected file |

The two content timers are **not** pushed by the pipeline directly (it's a
separate process). Instead `presenter._pipe` tails the pipeline's stdout and
parses its INFO log lines with regexes, calling `dashboard_add_audio` /
`dashboard_add_trans`:

| Pipeline log line | Counter |
|-------------------|---------|
| `seg cached+queued: … dur=<N>s …` | Audio Rec (`add_audio`) |
| `Segment finalized: <N>s … transcribed+corrected` | Transcribed (`add_trans`) |

This means **Elapsed** is wall-clock time from Start, **Audio Rec** advances only
after a segment file has actually been persisted, and **Transcribed** advances
only after the text for that segment has been transcribed, corrected, and written
to the corrected transcript. Silence/music windows that are skipped before
segment caching do not advance Audio Rec. **The regexes in `presenter.py` must
stay in sync with the exact log strings in `pipeline.py`** — a format drift
silently freezes a counter at 0.

---

## Thread Safety

The presenter runs background threads (VU meter, polling, stop/shutdown). UI
updates must go through signals, not direct calls.

```python
_log_signal  = pyqtSignal(str)    # put_log() — safe from any thread
_call_signal = pyqtSignal(object) # schedule(fn) — safe from any thread
```

`QTimer.singleShot(0, fn)` does **not** work from non-main threads in Qt6.
All cross-thread UI updates must use `_call_signal.emit(fn)` (`view.schedule`).

Inside `pipeline.py`, system capture and system VAD are separate threads joined
by `_system_audio_queue`. `raw_lock` protects the session raw file while capture
writes and finalization closes/converts it. The segment counter is guarded by
`_seg_seq_lock` (system and mic threads both enqueue), and the AEC reference
buffer / mic VAD have their own locks.

---

## ViewProtocol

`presenter.py` defines the interface the View must implement. Key methods:

| Method | Effect |
|--------|--------|
| `set_start_enabled(v)` / `set_stop_enabled(v)` | Toggle the Start/Stop button state |
| `show_onair()` / `hide_onair()` | ON AIR dot red / blue (blue also zeroes the VU) |
| `show_ptt_button()` / `hide_ptt_button()` | Show / hide the VU bar |
| `show_transcript(p)` / `show_corrected(p)` / `show_minutes(p)` | Load a file into a tab |
| `clear_results()` | Clear the 3 result tabs on start |
| `dashboard_start()` / `dashboard_stop()` / `dashboard_reset()` | Start/freeze/clear the live timers |
| `dashboard_add_audio(s)` / `dashboard_add_trans(s)` | Advance the Audio Rec / Transcribed timers (called from log parsing) |
| `schedule(fn)` | Run `fn` on the UI thread via `_call_signal` |
| `put_log(msg)` | Append to the log panel via `_log_signal` |

When the presenter calls a new view method, add a stub to `ViewProtocol`.

The Transcript and Corrected tabs intentionally show plain text and keep their
live-scroll behavior. The Minutes tab renders the generated Markdown with
`QTextBrowser.setMarkdown(...)` so headings, lists, and emphasis can be checked
in the UI; it falls back to plain text if Markdown rendering is unavailable.

---

## Configuration (`config.ini`)

Gitignored; `config_default.ini` is the committed template. Sections in use:

| Section | Keys (selected) |
|---------|-----------------|
| `[recording]` | `device` (auto/cuda/cpu), `model_size`, `language`, `enable_mic`, `mic_gain`, `aec_threshold`, `audio_format`, `mp3_quality`, `initial_prompt_max_terms` |
| `[paths]` | `audio_dir`, `transcript_dir`, `vocab_file`, `glossary_file` |
| `[summary]` | `mode` (openai/ollama), `api_base`/`api_key`/`model` or `ollama_url`/`ollama_model`, `enable_correction`, `enable_online_refine`, `online_refine_terms`, `corrected_dir`, `summary_dir`, `final_corrected_dir`, `term_cache_dir` |
| `[models]` | per-language model override: `ja` / `zh` / `en` |
| `[subtitle]` | VAD tuning: `silence_sec`, `min_accum_sec`, `max_sec`, `min_speech_sec` |
| `[network]` | `https_proxy`, `http_proxy`, `ssl_verify` |
| `[logging]` | `log_file`, `log_level`, `ui_show` |

---

## Documentation Maintenance

Keep the Markdown files in sync with each functional change. Update
`ARCHITECTURE.md` when a change affects process boundaries, IPC signal files,
threading, pipeline flow, config keys, output files, or important invariants.
Update README files when user-facing setup, usage, supported platforms, defaults,
or troubleshooting steps change. Keep `CODEX_MEMORY.md` as a compact working
summary after notable architecture or workflow changes.

---

## Tuning the real-time pipeline

| Symptom | Knob |
|---------|------|
| Backlog grows / long wait after stop | lower `model_size` (medium→small/base), use CUDA, raise `[subtitle] min_speech_sec` to drop more silence |
| CPU unexpectedly uses a huge language model | check `[models]`; `large*` language overrides are skipped on CPU unless base `model_size` is also large |
| Quiet speech dropped as "silence" | lower `[subtitle] min_speech_sec` |
| Choppy / over-segmented | raise `silence_sec` / `max_sec` |
| Wrong language locked | set `[recording] language` explicitly (not `auto`) |

---

## CUDA Compatibility

`appconfig.py` exposes lazy CUDA detection via `cuda_status()`; callers can warm
the cache after startup so UI import stays fast. `pipeline.py` pre-loads the
nvidia pip CUDA DLLs (`cudart64_12` / `cublas64_12` / `cublasLt64_12`) by full
path **before** importing ctranslate2. On a CUDA error mid-run the worker falls
back to CPU (`int8`) and rebuilds decode kwargs (`beam_size=1`).

---

## Output File Locations

Defaults derive from `~/Documents/Sound2Text/<sub>`; the running config points
them at `audio_dir` / `transcript_dir` / `[summary] corrected_dir` / `summary_dir`.

| Output | File |
|--------|------|
| Raw transcript | `transcript_<ts>.txt` |
| Corrected text | `corrected_<ts>.txt` |
| Final corrected text | `final_corrected_<ts>.txt` in `[summary] final_corrected_dir` (default = `corrected_dir`; only when online refinement succeeds) |
| Downloaded term cache | `term_cache_dir/*.json` (default `~/Documents/Sound2Text/term_cache`) |
| Meeting notes | `summary_<ts>.md` |
| Session audio | `audio_<ts>.mp3` (WAV intermediate deleted) |
| Segment cache (transient) | `.seg_cache_<ts>/seg_*.wav` (deleted as consumed; dir removed at session end) |
| Raw session buffer (transient) | `.tmp_audio_<ts>.raw` (converted then deleted) |

---

## Gitignored Runtime Files

```
config.ini          # user settings (contains API key)
run.bat             # generated by setup.bat
*.log
*.wav  *.mp3  *.raw
.pipeline_session / .pipeline_stop / .pipeline_session_done / .pipeline_subtitle / .pipeline.lock
.mic_onair / .recording_start
.last_transcript / .last_corrected / .last_language
.seg_cache_*/       # transient per-session segment cache
```

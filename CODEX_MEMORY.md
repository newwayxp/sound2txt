# Codex Memory

This is a compact working note for future Codex sessions. The source of truth is
`ARCHITECTURE.md`; use this file as a quick orientation map before making code
changes.

## Project Purpose

Sound2Text is a desktop app for real-time audio capture, local Whisper
transcription, per-segment LLM correction, and meeting minutes generation.

Core flow:

1. Capture system audio and optional microphone audio.
2. Segment speech with VAD.
3. Transcribe each segment with faster-whisper.
4. Correct each segment with an LLM and glossary replacements.
5. Generate meeting notes from the corrected transcript.
6. Save transcript, corrected text, meeting notes, and session audio.

## Architecture Snapshot

- `ui_qt.py`: PyQt6 View. Keep it display-focused.
  Transcript/Corrected tabs display plain text; Minutes renders Markdown with
  a plain-text fallback.
- `presenter.py`: Presenter/business logic, subprocess lifecycle, signal-file
  IPC, UI status updates, log parsing for dashboard timers.
- `pipeline.py`: Long-lived real-time engine. Owns audio capture, Silero VAD,
  disk-backed segment queue, Whisper transcription, per-segment correction,
  glossary application, raw/WAV/MP3 finalization.
- `appconfig.py`: ConfigParser wrapper and lazy CUDA detection helpers.
- `summarizer.py`: LLM transcript correction legacy path plus meeting minutes.
- `transcriber.py`: On-demand file transcription path, not the live pipeline.
- `widgets_qt.py`: Custom VU meter and dashboard clock widgets.
- `glossary.py`: Deterministic mistake-to-correction replacements.
- `start.py`: CLI launcher useful for non-interactive tests with `--seconds`.

## IPC And Runtime Files

Signal files live in the project directory:

- `.pipeline_session`: presenter tells pipeline a recording session is active.
- `.pipeline_stop`: presenter tells long-lived pipeline to exit.
- `.pipeline_session_done`: pipeline tells presenter final drain/finalize is done.
- `.pipeline.lock`: singleton lock so GUI/CLI cannot run multiple live pipelines
  against the same signal files.
- `.mic_onair`: toggles microphone feed into the live pipeline.
- `.last_transcript`: current raw transcript path.
- `.last_corrected`: current corrected transcript path.
- `.last_language`: detected or locked session language.
- `.recording_start`: session start timestamp.

When adding a new per-session signal, add it to the stale-cleanup list in
`presenter.start()`.

Presenter tracks whether it spawned the pipeline. A CLI/GUI instance that reuses
an external locked pipeline must not write `.pipeline_stop` when it exits.

## Important Invariants

- The live recording path is `pipeline.py`; do not revive old recorder-style
  flows unless explicitly requested.
- The segment queue stores file paths, not in-memory PCM. This prevents memory
  blow-ups and avoids blocking capture when transcription falls behind.
- Segment files must be fully written and closed before queueing.
- Session raw audio is converted only after the queue is drained at session end.
- Stop waits for `.pipeline_session_done`; avoid short fixed timeouts that can
  summarize incomplete transcripts or touch still-open audio files.
- Silero VAD uses 512-sample framing. Preserve buffering of sub-frame chunks.
- If Silero fails and amplitude fallback is used, do not silence-skip quiet audio
  as if reliable VAD were available.
- CPU transcription should use `beam_size=1`; CUDA can use a larger beam.
- Whisper `temperature` should remain a list, not scalar `0.0`. CPU uses `[0.0]`
  to avoid fallback storms; CUDA can keep a longer fallback list.
- On CPU, `[models]` language overrides pointing to `large*` are skipped unless
  the base `[recording] model_size` is also large; this keeps `model_size=medium`
  from silently running `large-v3` and building a backlog.
- Repeated-loop Whisper hallucinations are filtered before transcript/LLM
  correction so hard audio does not poison output with repeated tokens.
- Dashboard counters depend on presenter regexes matching exact pipeline INFO
  log strings:
  - `seg cached+queued: ... dur=<N>s ...` advances Audio Rec after segment WAV
    cache write.
  - `Segment finalized: <N>s ... transcribed+corrected` advances Transcribed
    after corrected-file append.
- Cross-thread UI updates must go through `view.schedule(...)` / Qt signals.
  Do not call Qt widgets directly from presenter background threads.

## Current Notes

- Keep Markdown docs synchronized with functional changes. Update
  `ARCHITECTURE.md` for architecture, IPC, config, pipeline, threading, output,
  or invariant changes; update README files for user-facing setup/usage changes;
  update this memory file when a short future-session note would help.
- `config.ini` is user-specific and may contain API keys. Do not commit or
  overwrite it casually.
- Existing user changes may be present in `ARCHITECTURE.md`, `i18n.py`,
  `pipeline.py`, `presenter.py`, and `ui_qt.py`; inspect diffs before editing.

## Useful Checks

- Syntax smoke test: `python -m py_compile <changed files>`
- CLI timed session: `python start.py --seconds 5`
- Audio diagnostics:
  - `python debug_modules.py loopback`
  - `python debug_modules.py audio`
  - `python debug_modules.py mic`
  - `python debug_modules.py pipeline`

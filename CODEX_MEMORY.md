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
- `pipeline.py`: Long-lived real-time engine. System audio uses separate
  `system-capture` and `system-vad` threads before the disk-backed segment queue;
  Whisper transcription and LLM/glossary correction are separate workers, followed
  by raw/WAV/MP3 finalization.
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
- `.last_final_corrected`: optional post-session final corrected transcript path
  from online industry-term refinement.
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
- System audio recording, boundary detection, and transcription are separate
  stages: capture writes the full raw session and feeds `_system_audio_queue`,
  VAD flushes completed segments, and the transcribe worker consumes segment
  file paths.
- LLM/glossary correction is downstream of Whisper via `_corr_queue`; the raw
  transcript can update before corrected text, but stop/finalize must wait for
  both `_seg_queue` and `_corr_queue`.
- Optional `[summary] enable_online_refine=true` runs after stop in
  `summarizer.py --step summary`: infer domain, fetch Wikipedia OpenSearch term
  candidates into `corrected_dir/term_cache/`, write `final_corrected_*.txt`,
  then generate minutes from that final file. Original transcript/corrected files
  are retained.
- Segment files must be fully written and closed before queueing.
- Session raw audio is converted only after the queue is drained at session end.
- Stop waits for `.pipeline_session_done`; avoid short fixed timeouts that can
  summarize incomplete transcripts or touch still-open audio files.
- Exception: if the user stops before `pipeline.py` has actually opened the
  session (`Session started:` / fresh `.last_transcript`), no audio exists yet.
  Presenter returns to idle immediately and lets the long-lived pipeline finish
  startup in the background.
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

## Change Log — 2026-06-17 (read ARCHITECTURE.md for the authoritative details)

This batch touched online-refine config, the minutes template, and pipeline
timing. **Re-read `ARCHITECTURE.md` (Timestamps, Configuration, Output File
Locations) before working on these areas — the notes below are only a pointer.**

- **Online refine output dirs are now configurable** (`summarizer.py`,
  `config_default.ini`): `[summary] term_cache_dir` (downloaded Wikipedia terms)
  and `[summary] final_corrected_dir` (`final_corrected_*.txt`). Both resolve via
  `cfg.get + expanduser` with backward-compatible fallbacks (`corrected_dir/term_cache`
  and `corrected_dir`). Same pattern as `corrected_dir`/`summary_dir`.
- **`enable_online_refine` checkbox now persists on toggle** (`ui_qt.py`): the
  online-refine checkbox lives only on the API/minutes tab; saving a different
  settings tab used to lose the flag. It now writes immediately on `toggled`.
  Symptom of the old bug: `term_cache`/`final_corrected` dirs stay empty and the
  log prints `[Summarizer] online refine disabled`.
- **Minutes template = plain text** (`summarizer.py` `_SUMMARY_TEMPLATES` +
  `_summary_prompts`): per-language `body` skeleton, no tables, no emoji; info
  fields (date/topic/summary) are separate bold paragraphs (blank-line separated
  so QTextBrowser does not soft-merge them). `ui_qt.py` Minutes-tab CSS styles
  headings only (table/blockquote CSS removed).
- **Live timestamp = segment END time** (`pipeline.py` `_enqueue`): `emit_ts =
  time.time()` (was `time.time() - seg_dur`, the segment *start*). With continuous
  speech every segment is a full `max_sec` force-flush, so the old start-stamp
  lagged the visible text by a whole window (e.g. 8 s). End-time keeps `[HH:MM:SS]`
  aligned with when speech finished and makes the `lag=` log pure transcription lag.
- **`force_flush` no longer drops the tail** (`pipeline.py` `AccumulatingVAD`):
  session-stop flush used to require `speech_dur >= min_accum_sec` (1 s) and
  silently dropped shorter remnants. Now it flushes the remnant and only skips
  when reliable Silero VAD reports near-zero speech (`< min_speech_sec`); under the
  amplitude fallback it always flushes (speech_dur unreliable).
- **Verified completeness of the stop→drain path**: capture (`system-capture`) is
  gated by `_recording_active`; raw-write and queue-put are atomic per chunk, so
  MP3 audio and transcription input never diverge. Stop drains
  `_system_audio_queue` via `queue.join()` (chunks are fed through VAD, not
  discarded), force-flushes VAD, then joins `_seg_queue`+`_corr_queue` before
  converting raw→MP3. Only intentional gaps: silence-skip windows (in MP3, not
  transcribed) and the sub-chunk in the hardware buffer at the exact stop instant
  (deliberately not recovered — stop means stop).

## Change Log — 2026-06-17 (part 2: correction strategy & API resilience)

- **Per-segment correction is skipped when `enable_online_refine=true`**
  (`pipeline.py` `_correct_segment`): avoids dozens of API calls per session (the
  HTTP 429 trigger). Glossary still applies live in the correction worker.
- **New `summarizer.py --step online`**: ONE combined full-correction +
  industry-term pass on the RAW transcript → `final_corrected_*`, then minutes.
  Used by the presenter when online refine is on; `--step summary` (minutes from
  the live per-segment corrected file) is used when it's off. Three-level
  fallback (combined → plain correction → raw+glossary) so a session always
  yields usable text even if every LLM call fails.
- **Presenter routes by `enable_online_refine`** (`presenter.py`): online on →
  Corrected tab is NOT streamed during recording (it shows glossary-only raw);
  it is filled once at stop with `final_corrected`, and the summarize step is
  `online` on the raw transcript. Online off → unchanged (live per-segment).
- **Bounded API retry** (`summarizer.py` `_call_openai`): retries 429/5xx up to
  `_RETRY_ATTEMPTS` times, waiting ≤ `_RETRY_MAX_WAIT`s (honors a short
  Retry-After). On exhaustion it raises and callers fall back to the
  pre-correction text — never write partial/garbage downstream. 404 etc. are not
  retried.
- **Language guard hoisted to module-level `_LANG_GUARDS`** and applied to the
  combined-pass prompt too (was missing there). Needed because Chinese-centric
  local models (qwen) translate JA/EN → Chinese; guard is prepended to system AND
  appended to the user prompt.
- **LLM backend note**: app is OpenAI-compatible (`mode=openai`, swap
  `api_base`/`api_key`/`model`) or local `mode=ollama`. Groq free tier is tight
  (~100K tokens/day, 12K TPM → long meetings 429). Verify a provider's model id
  via `GET <api_base>/models` before setting `model` (a wrong id returns 404, not
  401). Cerebras free currently serves `gpt-oss-120b` / `zai-glm-4.7` (NOT
  llama-3.3-70b); `gpt-oss-120b` also avoids the qwen JA→ZH translation problem.

## Useful Checks

- Syntax smoke test: `python -m py_compile <changed files>`
- CLI timed session: `python start.py --seconds 5`
- Audio diagnostics:
  - `python debug_modules.py loopback`
  - `python debug_modules.py audio`
  - `python debug_modules.py mic`
  - `python debug_modules.py pipeline`

"""
presenter.py – All business logic for Sound2Text.

MUST NOT import customtkinter or tkinter.
Communicates with the View via ViewProtocol + schedule().
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from appconfig import (
    AppConfig,
    BASE,
    CFG_FILE,
    LANG_FILE,
    STATE_FILE,
    STOP_SIGNAL,
    _CUDA_AVAILABLE,
    _CUDA_LIBS_OK,
    _setup_cuda_dlls,
)

if TYPE_CHECKING:
    pass


# ── ViewProtocol ───────────────────────────────────────────────────────────────

class ViewProtocol:
    """
    Interface that the View must implement so the Presenter can drive it
    without importing any GUI toolkit.
    """

    def put_log(self, msg: str): ...
    def schedule(self, fn): ...                  # run fn on the UI main thread
    def set_start_enabled(self, v: bool): ...
    def set_stop_enabled(self, v: bool): ...
    def show_onair(self): ...
    def hide_onair(self): ...
    def set_onair_level(self, level: float): ...
    def dashboard_start(self): ...
    def dashboard_stop(self): ...
    def dashboard_reset(self): ...
    def dashboard_add_audio(self, secs: float): ...
    def dashboard_add_trans(self, secs: float): ...
    def set_window_title(self, title: str): ...
    def get_window_title(self) -> str: ...
    def destroy(self): ...
    def lock_to_cpu_tiny(self): ...
    def unlock_gpu_buttons(self): ...
    def set_cuda_btn_text(self, text: str): ...
    def set_cuda_btn_state(self, enabled: bool): ...
    def set_rec_status(self, text_key: str, color: str): ...
    def set_tr_status(self, text_key: str, color: str): ...
    def set_sum_status(self, text_key: str, color: str): ...


# ── Presenter ─────────────────────────────────────────────────────────────────

class Presenter:
    """
    Owns all process management and business logic.

    Usage::

        config    = AppConfig()
        presenter = Presenter(config)
        app       = App(presenter)         # App calls presenter.set_view(self)
        presenter.initialize()             # run after view is set
    """

    def __init__(self, config: AppConfig):
        self._config  = config
        self._view: ViewProtocol | None = None

        # ── subprocess handles ────────────────────────────────────────────────
        self._rec_proc  = None
        self._tr_proc   = None
        self._mic_proc  = None
        self._ollama_proc = None

        # ── state flags ───────────────────────────────────────────────────────
        self._running         = False
        self._stopping        = False
        self._meter_active    = False

        # ── transcriber tracking ─────────────────────────────────────────────
        self._tr_current_file  = ""
        self._tr_last_activity = 0.0

        # ── Ollama ────────────────────────────────────────────────────────────
        self._OLLAMA_LOCK  = threading.Lock()
        self._OLLAMA_STATE = "stopped"   # "stopped" | "starting" | "running"

        # ── subprocess env ────────────────────────────────────────────────────
        _setup_cuda_dlls()
        self._env = os.environ.copy()
        self._env["PYTHONUTF8"]       = "1"
        self._env["PYTHONUNBUFFERED"] = "1"
        # Propagate CUDA DLL dirs via PATH so child processes also find them
        cuda_paths = [
            r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin",
            r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.0\bin",
            r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8\bin",
            r"C:\Program Files\NVIDIA\CUDNN\v9.0\bin",
            r"C:\Program Files\NVIDIA\CUDNN\v8.9\bin",
            r"C:\Program Files\NVIDIA\CUDNN\v8.6\bin",
        ]
        extra = os.pathsep.join(p for p in cuda_paths if os.path.isdir(p))
        if extra:
            self._env["PATH"] = extra + os.pathsep + self._env.get("PATH", "")

    # ── view wiring ───────────────────────────────────────────────────────────

    def set_view(self, view: ViewProtocol) -> None:
        self._view = view

    # ── initialisation ────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """
        Called once after the view is set.
        Runs CUDA checks, logs startup info, and applies defaults.
        """
        self._auto_select_backend()
        self._log_startup_info()
        self._apply_cpu_only_defaults(log=False)

    # ── CUDA helpers ──────────────────────────────────────────────────────────

    def _cuda_available(self) -> bool:
        return _CUDA_AVAILABLE

    def _cuda_libs_ok(self) -> bool:
        return _CUDA_LIBS_OK

    # ── backend auto-select ───────────────────────────────────────────────────

    def _auto_select_backend(self) -> None:
        """Detect CUDA and update the CUDA radio button state in the view."""
        if self._view is None:
            return
        if self._cuda_available():
            if self._cuda_libs_ok():
                self._view.set_cuda_btn_text("CUDA (GPU)")
                self._view.set_cuda_btn_state(True)
            else:
                self._view.set_cuda_btn_text("CUDA (libs missing)")
                self._view.set_cuda_btn_state(False)
            self._view.unlock_gpu_buttons()
        else:
            self._view.set_cuda_btn_text("CUDA (GPU)")
            self._view.set_cuda_btn_state(False)

    # ── startup log ───────────────────────────────────────────────────────────

    def _log_startup_info(self) -> None:
        if self._view is None:
            return
        self._view.put_log(
            f"[UI] CUDA available={self._cuda_available()} "
            f"libs_ok={self._cuda_libs_ok()}"
        )
        mode  = self._config.get("summary", "mode", fallback="openai")
        lang  = self._config.get("recording", "language", fallback="auto")
        dev   = self._config.get("recording", "device",   fallback="auto")
        model = self._config.get("recording", "model_size", fallback="small")
        self._view.put_log(
            f"[UI] Config: mode={mode} lang={lang} device={dev} model={model}"
        )

    # ── CPU-only defaults ─────────────────────────────────────────────────────

    def _force_cpu_tiny_config(self) -> bool:
        """Force config to cpu/tiny if not already; returns True if changed."""
        changed = False
        if self._config.get("recording", "device", fallback="auto") != "cpu":
            self._config.set("recording", "device", "cpu")
            changed = True
        if self._config.get("recording", "model_size", fallback="small") != "tiny":
            self._config.set("recording", "model_size", "tiny")
            changed = True
        if changed:
            self._config.save()
        return changed

    def _apply_cpu_only_defaults(self, log: bool = False) -> None:
        if self._cuda_available():
            return
        changed = self._force_cpu_tiny_config()
        if self._view is not None:
            self._view.lock_to_cpu_tiny()
            if log or changed:
                from i18n import t
                self._view.put_log(f"[UI] {t('gpu_unavailable')}")

    def apply_startup_defaults(self, log: bool = True) -> None:
        """Called from view's _apply_initial_ui_state after reading config."""
        self._apply_cpu_only_defaults(log=log)

    # ── config helpers ────────────────────────────────────────────────────────

    def _reload_config(self) -> None:
        """Re-read config.ini and notify the view of any lang change."""
        self._config.reload()

    def save_config(self, updates: dict) -> None:
        """
        *updates* = {(section, key): value, ...}
        Write all pairs then reload config.
        """
        for (sec, key), val in updates.items():
            self._config.set(sec, key, str(val))
        self._config.save()
        self._reload_config()
        if self._view:
            from i18n import t
            self._view.put_log(t("saved"))

    def _on_mode_change(self, mode: str) -> None:
        self._config.set("summary", "mode", mode)
        self._config.save()
        if mode == "ollama":
            threading.Thread(target=self._ensure_ollama_running, daemon=True).start()

    def _on_quick_lang_change(self, lang: str) -> None:
        self._config.set("recording", "language", lang)
        self._config.save()

    # ── Ollama management ─────────────────────────────────────────────────────

    def _ensure_ollama_running(self) -> None:
        import shutil
        with self._OLLAMA_LOCK:
            if self._OLLAMA_STATE in ("starting", "running"):
                return
            self._OLLAMA_STATE = "starting"

        if self._view:
            self._view.put_log("[UI] Starting Ollama...")

        ollama_exe = shutil.which("ollama")
        if not ollama_exe:
            if self._view:
                self._view.put_log("[UI] Ollama not found in PATH – skipping auto-start")
            with self._OLLAMA_LOCK:
                self._OLLAMA_STATE = "stopped"
            return

        threading.Thread(target=self._ollama_start_worker,
                         args=(ollama_exe,), daemon=True).start()

    def _ollama_start_worker(self, ollama_exe: str) -> None:
        try:
            proc = subprocess.Popen(
                [ollama_exe, "serve"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env=self._env,
            )
            self._ollama_proc = proc
            with self._OLLAMA_LOCK:
                self._OLLAMA_STATE = "running"
            if self._view:
                self._view.put_log(f"[UI] Ollama started: pid={proc.pid}")
            for line in proc.stdout:
                line = line.rstrip()
                if line and self._view:
                    self._view.put_log(f"[Ollama] {line}")
        except Exception as exc:
            if self._view:
                self._view.put_log(f"[UI] Ollama start failed: {exc}")
        finally:
            with self._OLLAMA_LOCK:
                self._OLLAMA_STATE = "stopped"

    def _stop_ollama(self) -> None:
        if self._ollama_proc and self._ollama_proc.poll() is None:
            self._view and self._view.put_log("[UI] Stopping Ollama...")
            self._ollama_proc.terminate()
            try:
                self._ollama_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._ollama_proc.kill()
                self._ollama_proc.wait()
        self._ollama_proc = None
        with self._OLLAMA_LOCK:
            self._OLLAMA_STATE = "stopped"

    # ── transcriber prestart ──────────────────────────────────────────────────

    def _prestart_transcriber(self) -> None:
        """
        Optionally warm up the transcriber by importing the model in a
        background process.  Currently a no-op placeholder.
        """
        pass

    # ── meter ────────────────────────────────────────────────────────────────

    def _start_meter(self) -> None:
        if self._meter_active:
            return
        self._meter_active = True
        threading.Thread(target=self._meter_loop, daemon=True).start()

    def _stop_meter(self) -> None:
        self._meter_active = False
        if self._view:
            self._view.schedule(lambda: self._view.set_onair_level(0.0))

    def _meter_loop(self) -> None:
        """
        Simulate a VU meter by reading mic audio level.
        Currently produces a fake level so the ring animates.
        Replace with real audio sampling if needed.
        """
        import math
        t0 = time.monotonic()
        while self._meter_active:
            elapsed = time.monotonic() - t0
            level = (math.sin(elapsed * 3.0) + 1.0) / 2.0 * 0.8 + 0.1
            if self._view:
                v = level  # capture for lambda
                self._view.schedule(lambda l=v: self._view.set_onair_level(l))
            time.sleep(0.1)

    # ── start / stop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        if self._view is None:
            return

        # Clean up signal files
        for path in (STOP_SIGNAL, STATE_FILE, LANG_FILE):
            if os.path.exists(path):
                os.remove(path)

        # Re-read config and enforce CPU-only if needed
        self._reload_config()
        self._apply_cpu_only_defaults(log=True)

        self._running  = True
        self._stopping = False

        # Disable start, enable stop
        self._view.schedule(lambda: self._view.set_start_enabled(False))
        self._view.schedule(lambda: self._view.set_stop_enabled(True))
        self._view.schedule(lambda: self._view.set_rec_status("running", "#44dd44"))
        self._view.schedule(lambda: self._view.set_tr_status("running",  "#44dd44"))
        self._view.schedule(lambda: self._view.set_sum_status("standby", "gray60"))
        self._view.schedule(lambda: self._view.show_onair())
        self._view.schedule(lambda: self._view.dashboard_start())

        audio_dir      = self._config.get("paths", "audio_dir",     fallback="")
        transcript_dir = self._config.get("paths", "transcript_dir", fallback="")
        corrected_dir  = self._config.get("summary", "corrected_dir", fallback="")
        summary_dir    = self._config.get("summary", "summary_dir",  fallback="")
        mode           = self._config.get("summary", "mode",         fallback="openai")
        language       = self._config.get("recording", "language",   fallback="auto")
        record_sec     = self._config.get("recording", "record_sec", fallback="30")

        from i18n import t
        self._view.put_log("[UI] Start requested")
        self._view.put_log(
            f"[UI] Config loaded: mode={mode}, language={language}, "
            f"record_sec={record_sec}s"
        )
        self._view.put_log(f"[UI] Audio directory: {audio_dir}")
        self._view.put_log(f"[UI] Transcript directory: {transcript_dir}")
        self._view.put_log(f"[UI] Corrected text directory: {corrected_dir}")
        self._view.put_log(f"[UI] Summary directory: {summary_dir}")

        self._view.put_log("[UI] Launching recorder.py")
        self._rec_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "recorder.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        self._view.put_log(f"[UI] recorder.py started: pid={self._rec_proc.pid}")

        self._view.put_log("[UI] Launching transcriber.py")
        self._tr_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "transcriber.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        self._view.put_log(f"[UI] transcriber.py started: pid={self._tr_proc.pid}")

        threading.Thread(target=self._pipe, args=(self._rec_proc, "[Rec]"),  daemon=True).start()
        threading.Thread(target=self._pipe, args=(self._tr_proc,  "[Tr]"),   daemon=True).start()
        threading.Thread(target=self._after_trans,                            daemon=True).start()

        self._start_meter()
        self._view.put_log(t("starting"))

    def stop(self) -> None:
        if not self._running:
            return
        if self._stopping:
            self._view and self._view.put_log("[UI] Stop is already in progress")
            return
        self._stopping = True

        if self._view:
            self._view.schedule(lambda: self._view.set_stop_enabled(False))
            from i18n import t
            self._view.put_log(t("stopping"))
            self._view.put_log("[UI] Creating stop signal for transcriber.py")

        with open(STOP_SIGNAL, "w", encoding="utf-8") as fh:
            fh.write("stop")

        if self._rec_proc and self._rec_proc.poll() is None:
            self._view and self._view.put_log("[UI] Stopping recorder.py")
            self._rec_proc.terminate()
        else:
            self._view and self._view.put_log("[UI] recorder.py is already stopped")

        if self._view:
            self._view.schedule(lambda: self._view.set_rec_status("stopped", "gray60"))
            self._view.schedule(lambda: self._view.hide_onair())
            self._view.schedule(lambda: self._view.dashboard_stop())
        self._stop_meter()
        self._view and self._view.put_log(
            "[UI] Waiting for transcriber.py to drain remaining audio files"
        )

    # ── after-transcription pipeline ─────────────────────────────────────────

    def _after_trans(self) -> None:
        self._wait_process(self._tr_proc, "transcriber.py", timeout_sec=900)
        if self._view:
            self._view.schedule(lambda: self._view.set_tr_status("stopped", "gray60"))

        transcript_path = self._read_last_transcript()
        if transcript_path:
            self._view and self._view.put_log(f"[UI] Transcript file: {transcript_path}")
        else:
            self._view and self._view.put_log("[UI] Transcript file was not created")

        if transcript_path and os.path.exists(transcript_path):
            if self._view:
                self._view.schedule(
                    lambda: self._view.set_sum_status("generating", "#ddaa00")
                )
            from i18n import t
            self._view and self._view.put_log(t("sum_start"))
            self._view and self._view.put_log("[UI] Launching summarizer.py")

            sum_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            self._view and self._view.put_log(
                f"[UI] summarizer.py started: pid={sum_proc.pid}"
            )
            threading.Thread(target=self._pipe, args=(sum_proc, "[Sum]"), daemon=True).start()
            self._wait_process(sum_proc, "summarizer.py", timeout_sec=900)

            corrected = self._latest_file(
                self._config.get("summary", "corrected_dir", fallback=""),
                "corrected_*.txt",
            )
            summary = self._latest_file(
                self._config.get("summary", "summary_dir", fallback=""),
                "summary_*.md",
            )
            if corrected:
                self._view and self._view.put_log(f"[UI] Corrected text file: {corrected}")
            if summary:
                self._view and self._view.put_log(f"[UI] Summary file: {summary}")

            if self._view:
                self._view.schedule(lambda: self._view.set_sum_status("done", "#44dd44"))
        else:
            self._view and self._view.put_log(
                "[UI] Summary skipped because transcript file is missing"
            )
            if self._view:
                self._view.schedule(lambda: self._view.set_sum_status("skipped", "gray60"))

        if self._view:
            self._view.schedule(self._set_controls_idle)
        from i18n import t
        self._view and self._view.put_log(t("all_done"))

    def _check_summary_backend(self) -> bool:
        """Return True if the summary backend is reachable (placeholder)."""
        return True

    # ── window lifecycle ──────────────────────────────────────────────────────

    def on_close(self) -> None:
        if self._running:
            if self._rec_proc and self._rec_proc.poll() is None:
                self._rec_proc.terminate()
            if self._tr_proc and self._tr_proc.poll() is None:
                self._tr_proc.terminate()
        self._stop_ollama()
        if self._view:
            self._view.schedule(self._view.destroy)

    def force_quit(self) -> None:
        """Immediate kill – used on second close attempt."""
        self._force_quit()

    def _force_quit(self) -> None:
        for proc in (self._rec_proc, self._tr_proc, self._ollama_proc):
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
        if self._view:
            self._view.schedule(self._view.destroy)

    def _check_and_close(self) -> None:
        pass  # placeholder for future "are you sure?" logic

    def _set_controls_idle(self) -> None:
        self._running  = False
        self._stopping = False
        if self._view:
            self._view.set_start_enabled(True)
            self._view.set_stop_enabled(False)
            self._view.dashboard_reset()

    # ── process helpers ───────────────────────────────────────────────────────

    def _pipe(self, proc, prefix: str) -> None:
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                self._view and self._view.put_log(f"{prefix} {text}")

    def _wait_process(self, proc, name: str,
                      timeout_sec: int | None = None,
                      interval_sec: int = 5) -> bool:
        if not proc:
            return True
        start    = time.monotonic()
        next_log = 0
        while proc.poll() is None:
            elapsed = int(time.monotonic() - start)
            if elapsed >= next_log:
                self._view and self._view.put_log(
                    f"[UI] Waiting for {name} to finish... elapsed={elapsed}s"
                )
                next_log = elapsed + interval_sec
            if timeout_sec is not None and elapsed >= timeout_sec:
                self._view and self._view.put_log(
                    f"[UI] {name} did not finish within {timeout_sec}s. Terminating."
                )
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._view and self._view.put_log(
                        f"[UI] {name} still running. Killing process."
                    )
                    proc.kill()
                    proc.wait()
                return False
            time.sleep(0.5)
        self._view and self._view.put_log(
            f"[UI] {name} exited with code {proc.returncode}"
        )
        return True

    def _wav_secs(self, path: str) -> float:
        """Return duration in seconds for a WAV file (best-effort)."""
        try:
            import wave
            with wave.open(path, "rb") as w:
                return w.getnframes() / max(1, w.getframerate())
        except Exception:
            return 0.0

    def _read_last_transcript(self) -> str:
        if not os.path.exists(STATE_FILE):
            return ""
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()

    def _latest_file(self, directory: str, pattern: str) -> str:
        if not directory or not os.path.isdir(directory):
            return ""
        files = glob.glob(os.path.join(directory, pattern))
        return max(files, key=os.path.getmtime) if files else ""

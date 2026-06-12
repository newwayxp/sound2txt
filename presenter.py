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
import traceback
from datetime import datetime
from typing import TYPE_CHECKING

# ── File logger (always active, independent of UI) ────────────────────────────
import configparser as _cp
from log_util import LogConfig, FileLogger, parse_log_line

def _load_log_config() -> LogConfig:
    cfg = _cp.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"),
             encoding="utf-8")
    return LogConfig(cfg)

_log_cfg    = _load_log_config()
_file_log   = FileLogger(_log_cfg.log_file)

def _log(category: str, level: str, message: str) -> None:
    if _log_cfg.should_write_file(level):
        from log_util import _ts
        _file_log.write_raw(category, level, _ts(), message)

_log("SYS", "INFO", "="*60)
_log("SYS", "INFO", "Sound2Text presenter started")

from appconfig import (
    AppConfig,
    BASE,
    CFG_FILE,
    LANG_FILE,
    START_FILE,
    STATE_FILE,
    STOP_SIGNAL,
    cuda_status,
    _setup_cuda_dlls,
)

_MIC_ONAIR = os.path.join(BASE, ".mic_onair")

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
    def lock_to_cpu(self): ...
    def unlock_gpu_buttons(self): ...
    def set_cuda_btn_text(self, text: str): ...
    def set_cuda_btn_state(self, enabled: bool): ...
    def set_rec_status(self, text_key: str, color: str): ...
    def set_tr_status(self, text_key: str, color: str): ...
    def set_sum_status(self, text_key: str, color: str): ...
    def show_ptt_button(self): ...
    def hide_ptt_button(self): ...
    def clear_results(self): ...


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
        self._rec_proc    = None
        self._tr_proc     = None   # kept for _pipe / dashboard compat
        self._ollama_proc = None

        # ── on-demand transcriber (file watcher) ──────────────────────────────
        self._fw_stop      = threading.Event()   # set when stop() is called
        self._fw_thread: threading.Thread | None = None
        self._session_lang: str | None = None    # detected/fixed language for session
        self._transcript_file: str | None = None # current session transcript path

        # ── pipeline (unified: VAD + Whisper + translation) ───────────────────
        self._pipeline_proc:  subprocess.Popen | None = None
        self._sub_win_proc:   subprocess.Popen | None = None
        # backward compat alias
        self._sub_proc = None

        # ── state flags ───────────────────────────────────────────────────────
        self._running         = False
        self._stopping        = False
        self._closing         = False   # graceful shutdown in progress
        self._meter_active    = False
        self._session_id      = 0   # incremented each start(); guards stale async callbacks

        # ── transcriber tracking ─────────────────────────────────────────────
        self._tr_current_file  = ""
        self._tr_last_activity = 0.0

        # ── Ollama ────────────────────────────────────────────────────────────
        self._OLLAMA_LOCK  = threading.Lock()
        self._OLLAMA_STATE = "stopped"   # "stopped" | "starting" | "running"

        # ── subprocess env ────────────────────────────────────────────────────
        # NOTE: _setup_cuda_dlls() and CUDA detection are deferred to warm_up(),
        # which runs in a background thread after the window is shown, so the UI
        # appears immediately instead of blocking on the slow CUDA probe.
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

        self._ensure_dirs()

    # ── view wiring ───────────────────────────────────────────────────────────

    def set_view(self, view: ViewProtocol) -> None:
        self._view = view
        _log("SYS", "INFO", f"View set: {type(view).__name__}")

    def _log(self, msg: str, level: str = "info") -> None:
        """Write to both the UI log panel and the log file."""
        lvl_map = {"debug": "DEBUG", "info": "INFO", "warning": "WARN", "error": "ERROR"}
        _log("UI", lvl_map.get(level, "INFO"), msg)
        if self._view:
            self._view.put_log(msg)

    # ── initialisation ────────────────────────────────────────────────────────

    def warm_up(self) -> None:
        """Heavy, view-free startup work that is safe to run off the main thread:
        set up the CUDA DLL search path and probe for a GPU (result cached).
        Run this from a background thread so the window can appear first."""
        _setup_cuda_dlls()
        cuda_status()

    def initialize(self) -> None:
        """
        Called once after warm_up() completes, on the Qt main thread.
        Reads the cached CUDA result, logs startup info, and applies defaults.
        (Must run on the main thread — it updates view widgets.)
        """
        self._auto_select_backend()
        self._log_startup_info()
        self._apply_cpu_only_defaults(log=False)

    # ── CUDA helpers ──────────────────────────────────────────────────────────

    def _cuda_available(self) -> bool:
        return cuda_status()[0]

    def _cuda_libs_ok(self) -> bool:
        return cuda_status()[1]

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

    def _force_cpu_config(self) -> bool:
        """Force device to cpu when CUDA is unavailable; returns True if changed.
        The user's model_size choice is left untouched (no restriction)."""
        changed = False
        if self._config.get("recording", "device", fallback="auto") != "cpu":
            self._config.set("recording", "device", "cpu")
            changed = True
        if changed:
            self._config.save()
        return changed

    def _apply_cpu_only_defaults(self, log: bool = False) -> None:
        if self._cuda_available():
            return
        changed = self._force_cpu_config()
        if self._view is not None:
            self._view.lock_to_cpu()
            if log or changed:
                from i18n import t
                self._view.put_log(f"[UI] {t('gpu_unavailable')}")

    def apply_startup_defaults(self, log: bool = True) -> None:
        """Called from view's _apply_initial_ui_state after reading config."""
        self._apply_cpu_only_defaults(log=log)
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create all configured output directories if they don't exist."""
        _base = os.path.join(os.path.expanduser("~"), "Documents", "Sound2Text")
        dirs = [
            self._config.get("paths", "audio_dir",
                             fallback=os.path.join(_base, "audio")),
            self._config.get("paths", "transcript_dir",
                             fallback=os.path.join(_base, "transcript")),
            self._config.get("summary", "corrected_dir",
                             fallback=os.path.join(_base, "corrected")),
            self._config.get("summary", "summary_dir",
                             fallback=os.path.join(_base, "memo")),
        ]
        for d in dirs:
            if d:
                try:
                    os.makedirs(d, exist_ok=True)
                except Exception:
                    pass

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

    def start_mic(self) -> None:
        """Activate mic mixing in pipeline (On Air ON)."""
        if not self._running:
            return
        if os.path.exists(_MIC_ONAIR):
            return  # already active
        try:
            with open(_MIC_ONAIR, "w") as f:
                f.write("1")
        except Exception as e:
            self._view and self._view.put_log(f"[UI] Mic on-air signal error: {e}")
            return
        self._view and self._view.put_log("[UI] マイクミックス ON")
        if self._view:
            self._view.schedule(lambda: self._view.show_onair())
        self._start_meter()

    def toggle_mic(self) -> None:
        """Toggle mic mixing. Called when user clicks the VU meter."""
        if not self._running:
            return
        if os.path.exists(_MIC_ONAIR):
            self.stop_mic()
        else:
            self.start_mic()

    def stop_mic(self) -> None:
        """Deactivate mic mixing in pipeline (On Air OFF)."""
        self._stop_meter()
        if self._view:
            self._view.schedule(lambda: self._view.hide_onair())
        try:
            os.remove(_MIC_ONAIR)
        except FileNotFoundError:
            pass
        self._view and self._view.put_log("[UI] マイクミックス OFF")

    @property
    def cuda_available(self) -> bool:
        """Whether CUDA GPU is usable (view uses this instead of importing appconfig)."""
        avail, libs_ok = cuda_status()
        return avail and libs_ok

    def ensure_ollama_running(self) -> None:
        """Public: start Ollama if mode is ollama and not already running."""
        if self._config.get("summary", "mode", fallback="openai") == "ollama":
            threading.Thread(target=self._ensure_ollama_running, daemon=True).start()

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
        if os.environ.get("SOUND2TXT_NO_OLLAMA"):
            return   # skip in test/debug mode
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

    # ── subtitle control ──────────────────────────────────────────────────────

    def _finalize_recorder_raw(self) -> None:
        """recorder が terminate() で停止した後、残った .raw ファイルを WAV に変換する。"""
        import wave as _wave
        import glob as _glob

        # recorder が完全終了するまで最大 5 秒待つ
        if self._rec_proc:
            try:
                self._rec_proc.wait(timeout=5)
            except Exception:
                pass

        audio_dir = self._config.get("paths", "audio_dir", fallback="")
        if not audio_dir:
            return

        raw_files = _glob.glob(os.path.join(audio_dir, ".tmp_audio_*.raw"))
        if not raw_files:
            return

        import configparser as _cp
        _cfg = _cp.ConfigParser()
        _cfg.read(os.path.join(BASE, "config.ini"), encoding="utf-8")

        for raw_path in raw_files:
            basename = os.path.basename(raw_path)
            ts       = basename[len(".tmp_audio_"):-len(".raw")]
            wav_path = os.path.join(audio_dir, f"audio_{ts}.wav")
            try:
                with open(raw_path, "rb") as f:
                    pcm_data = f.read()
                if not pcm_data:
                    os.remove(raw_path)
                    continue
                # channel/rate は config から推測（デフォルト: stereo 44100）
                # 実際のデバイス設定は recorder が知っているが、ここでは WAV を書くだけ
                # ※ recorder.py と同じ select_active_device で取得するのが正確だが
                #    ここでは raw を保存して後で正確に変換できるようにする
                from device_utils import select_active_device
                if sys.platform == "win32":
                    import pyaudiowpatch as _pa
                else:
                    import pyaudio as _pa
                _audio = _pa.PyAudio()
                try:
                    _, dev_info = select_active_device(_audio)
                    channels    = dev_info["maxInputChannels"]
                    sample_rate = int(dev_info["defaultSampleRate"])
                    sample_size = _audio.get_sample_size(_pa.paInt16)
                except Exception:
                    channels    = 2
                    sample_rate = 44100
                    sample_size = 2
                finally:
                    _audio.terminate()

                with _wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(sample_size)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm_data)
                os.remove(raw_path)
                duration = len(pcm_data) / (sample_rate * channels * sample_size)
                _log("REC", "INFO", f"RAW→WAV変換完了: {os.path.basename(wav_path)} ({duration:.1f}秒)")
                self._view and self._view.put_log(
                    f"[REC] 音声ファイル保存: {os.path.basename(wav_path)} ({duration:.1f}秒)"
                )
            except Exception as e:
                _log("REC", "ERROR", f"RAW→WAV変換失敗: {e}  raw={raw_path}")
                self._view and self._view.put_log(f"[REC] [ERROR] 音声変換失敗: {e}")

    # ── Pipeline control (unified: audio + VAD + Whisper + translation) ────────

    _PIPELINE_SUBTITLE = os.path.join(BASE, ".pipeline_subtitle")
    _PIPELINE_SESSION  = os.path.join(BASE, ".pipeline_session")
    _PIPELINE_STOP     = os.path.join(BASE, ".pipeline_stop")

    def _ensure_pipeline_running(self) -> None:
        """pipeline.py が起動していなければ起動する。"""
        if self._pipeline_proc and self._pipeline_proc.poll() is None:
            return
        # stop シグナルをクリア
        try:
            os.remove(self._PIPELINE_STOP)
        except FileNotFoundError:
            pass
        self._pipeline_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "pipeline.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        threading.Thread(
            target=self._pipe, args=(self._pipeline_proc, "[PL]"), daemon=True
        ).start()
        _log("SYS", "INFO", f"pipeline started pid={self._pipeline_proc.pid}")
        self._view and self._view.put_log(f"[UI] pipeline 起動 pid={self._pipeline_proc.pid}")

    def start_subtitle(self, dst_lang: str = "") -> None:
        """字幕ボタン ON: pipeline を起動して subtitle シグナルを書く。"""
        self.save_config({("subtitle", "dst_lang"): dst_lang})
        # subtitle シグナルを書く
        with open(self._PIPELINE_SUBTITLE, "w") as f:
            f.write("1")
        self._ensure_pipeline_running()
        # 字幕ウィンドウを起動
        if not self._sub_win_proc or self._sub_win_proc.poll() is not None:
            self._sub_win_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "subtitle_window.py")],
                env=self._env,
            )
            _log("SYS", "INFO", f"subtitle_window started pid={self._sub_win_proc.pid}")
        self._view and self._view.put_log("[UI] 字幕開始")

    def stop_subtitle(self) -> None:
        """字幕ボタン OFF: subtitle シグナルを削除。session も OFF なら pipeline 停止。"""
        try:
            os.remove(self._PIPELINE_SUBTITLE)
        except FileNotFoundError:
            pass
        # session も非アクティブなら pipeline を停止
        if not os.path.exists(self._PIPELINE_SESSION):
            self._stop_pipeline()
        # 字幕ウィンドウを閉じる
        if self._sub_win_proc and self._sub_win_proc.poll() is None:
            self._sub_win_proc.terminate()
            self._sub_win_proc = None
        self._view and self._view.put_log("[UI] 字幕停止")

    def _stop_pipeline(self, timeout: float = 30) -> None:
        """pipeline に停止シグナルを送り、終了を待つ。
        timeout は wait の上限（プロセスは終了次第すぐ返る）。閉じる際は
        MP3 変換の完了を待てるよう大きめの値を渡す。"""
        with open(self._PIPELINE_STOP, "w") as f:
            f.write("1")
        if self._pipeline_proc and self._pipeline_proc.poll() is None:
            try:
                self._pipeline_proc.wait(timeout=timeout)
            except Exception:
                self._pipeline_proc.terminate()
        self._pipeline_proc = None
        _log("SYS", "INFO", "pipeline stopped")

    def _stop_meter(self) -> None:
        self._meter_active = False
        if self._view:
            self._view.schedule(lambda: self._view.set_onair_level(0.0))

    def _meter_loop(self) -> None:
        """Read mic input in real time and feed RMS level to the VU meter."""
        stream = None
        pa     = None
        try:
            import numpy as np
            if sys.platform == "win32":
                import pyaudiowpatch as pyaudio
            else:
                import pyaudio
            pa = pyaudio.PyAudio()
            try:
                info = pa.get_default_input_device_info()
                idx  = int(info["index"])
                sr   = int(info["defaultSampleRate"])
            except Exception:
                return
            chunk  = 512
            stream = pa.open(
                format=pyaudio.paInt16, channels=1, rate=sr,
                input=True, frames_per_buffer=chunk,
                input_device_index=idx,
            )
            while self._meter_active:
                data  = stream.read(chunk, exception_on_overflow=False)
                arr   = np.frombuffer(data, dtype=np.int16)
                rms   = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)))
                level = min(rms / 4000.0, 1.0)
                # Re-check flag: meter may have been stopped while read() blocked
                if self._meter_active and self._view:
                    v = level
                    self._view.schedule(lambda l=v: self._view.set_onair_level(l))
        except Exception:
            pass
        finally:
            if stream:
                try: stream.stop_stream(); stream.close()
                except Exception: pass
            if pa:
                try: pa.terminate()
                except Exception: pass
            # If crashed while active, auto-hide
            if self._meter_active:
                self._meter_active = False
                if self._view:
                    try: self._view.schedule(self._view.hide_onair)
                    except Exception: pass

    # ── start / stop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        if self._view is None:
            return

        # Clean up signal files from any previous session.
        # Include .pipeline_stop so a lingering stop signal (from a previous
        # stop/close or crash) can never make the reused pipeline exit on start.
        for path in (STOP_SIGNAL, STATE_FILE, LANG_FILE,
                     _MIC_ONAIR, self._PIPELINE_STOP,
                     os.path.join(BASE, ".last_corrected")):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

        # Re-read config and enforce CPU-only if needed
        self._reload_config()
        self._apply_cpu_only_defaults(log=True)

        self._running   = True
        self._stopping  = False
        self._session_id += 1   # invalidate callbacks from previous session

        # Disable start, enable stop
        self._view.schedule(lambda: self._view.set_start_enabled(False))
        self._view.schedule(lambda: self._view.set_stop_enabled(True))
        self._view.schedule(lambda: self._view.set_rec_status("running", "#44dd44"))
        self._view.schedule(lambda: self._view.set_tr_status("running",  "#44dd44"))
        self._view.schedule(lambda: self._view.set_sum_status("standby", "gray60"))
        self._view.schedule(lambda: self._view.dashboard_start())
        self._view.schedule(lambda: self._view.clear_results())
        # Both modes: show VU bar with blue dot; user clicks VU to start mic
        self._view.schedule(lambda: self._view.hide_onair())      # dot = blue
        self._view.schedule(lambda: self._view.show_ptt_button()) # show VU container

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

        # Write recording start time so transcriber skips pre-recording files
        with open(START_FILE, "w") as _sf:
            _sf.write(str(time.time()))
        self._view.put_log(f"[UI] Recording started at: {time.strftime('%Y-%m-%d %H:%M:%S')}")

        rec_mode   = self._config.get("recording", "mode",       fallback="meeting").strip().lower()
        # ── Pipeline セッション開始 ────────────────────────────────────────────
        # pipeline.py が音声取得・VAD・Whisper・翻訳・音声保存・transcript書き込みを担当
        self._rec_proc = None

        # macOS: switch system output to Multi-Output Device (speakers + BlackHole)
        if sys.platform == "darwin":
            try:
                import macos_audio as _mac_audio
                _mac_audio.activate_loopback(
                    log_fn=self._view.put_log if self._view else None
                )
            except Exception as _e:
                self._view and self._view.put_log(
                    f"[AudioRoute] 自動ルーティングをスキップ: {_e}"
                )

        # session シグナルを書く → pipeline がこれを検知してセッション開始
        with open(self._PIPELINE_SESSION, "w") as f:
            f.write("1")
        self._ensure_pipeline_running()
        # Start real-time corrected file polling for transcript tab
        threading.Thread(target=self._poll_corrected_file, daemon=True).start()
        self._view.put_log("[UI] pipeline セッション開始")
        self._view.put_log("[UI] VU メーターをクリックするとマイク録音を開始/停止します")
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

        # pipeline の session シグナルを削除 → pipeline がセッション終了処理を行い、
        # アイドル状態に戻って次のセッション開始を待つ。
        # pipeline プロセスはここでは停止しない（長寿命：次回 start() で再利用し、
        # セッション開始時に config / モデルを再読込する）。停止はアプリ終了時のみ。
        try:
            os.remove(self._PIPELINE_SESSION)
        except FileNotFoundError:
            pass

        # macOS: restore original system output immediately on stop
        if sys.platform == "darwin":
            try:
                import macos_audio as _mac_audio
                _mac_audio.restore_loopback(
                    log_fn=self._view.put_log if self._view else None
                )
            except Exception:
                pass

        # session 完了を待ってから纪要生成
        self._fw_stop.set()
        threading.Thread(target=self._wait_pipeline_and_summarize, daemon=True).start()

        # Deactivate mic mixing signal
        try:
            os.remove(_MIC_ONAIR)
        except FileNotFoundError:
            pass

        if self._view:
            self._view.schedule(lambda: self._view.set_rec_status("stopped", "gray60"))
            self._view.schedule(lambda: self._view.hide_onair())
            self._view.schedule(lambda: self._view.hide_ptt_button())
            self._view.schedule(lambda: self._view.dashboard_stop())
        self._stop_meter()
        self._view and self._view.put_log("[UI] セッション終了中... pipeline の処理完了を待っています")

    # ── on-demand file watcher ────────────────────────────────────────────────

    def _poll_corrected_file(self) -> None:
        """Poll the live transcript and corrected files during a session and
        update the Transcript / Corrected tabs separately in real time.

        The raw transcript (STATE_FILE → .last_transcript) feeds the Transcript
        tab; the corrected text (.last_corrected) feeds the Corrected tab. The
        two are kept distinct so corrections never overwrite the raw output."""
        _corr_state = os.path.join(BASE, ".last_corrected")
        _sid        = self._session_id   # session this poll thread belongs to
        # (state_pointer_file, view_method, last_path, last_mtime)
        watches = [
            [STATE_FILE,   "show_transcript", None, 0.0],
            [_corr_state,  "show_corrected",  None, 0.0],
        ]
        POLL_INTERVAL = 0.5  # check every 500ms instead of 3s for faster responsiveness
        while self._running and self._session_id == _sid:
            for w in watches:
                state_file, method = w[0], w[1]
                try:
                    if not os.path.exists(state_file):
                        continue
                    with open(state_file, encoding="utf-8") as f:
                        path = f.read().strip()
                    if not (path and os.path.exists(path)):
                        continue
                    mtime = os.path.getmtime(path)
                    # Update if path changed OR if file was modified (use smaller threshold)
                    if path != w[2] or mtime > w[3]:
                        w[2], w[3] = path, mtime
                        _p = path
                        if self._view and hasattr(self._view, method):
                            if self._session_id == _sid:  # re-check before scheduling
                                self._view.schedule(
                                    lambda p=_p, m=method: getattr(self._view, m)(p))
                except Exception as e:
                    self._log(f"Poll error on {state_file}: {e}", level="warning")
            time.sleep(POLL_INTERVAL)

    def _wait_pipeline_and_summarize(self) -> None:
        """pipeline がセッション終了処理を完了するまで待ち、纪要を生成する。"""
        _SESS_DONE = os.path.join(BASE, ".pipeline_session_done")
        # pipeline_session_done シグナルを待つ（最大 60 秒）
        deadline = time.time() + 60
        while time.time() < deadline:
            if os.path.exists(_SESS_DONE):
                try:
                    os.remove(_SESS_DONE)
                except Exception:
                    pass
                _log("SYS", "INFO", "session_done シグナル受信")
                break
            time.sleep(0.5)
        else:
            _log("SYS", "WARN", "session_done タイムアウト → フォールバック: RAW 変換を試みる")
            self._finalize_recorder_raw()  # 安全策: 残 RAW ファイルを変換

        if self._view:
            self._view.schedule(lambda: self._view.set_tr_status("stopped", "gray60"))

        # 纪要生成
        threading.Thread(target=self._run_summary_pipeline, daemon=True).start()

    def _file_watch_loop(self) -> None:
        """音声ファイルを監視し、新ファイルごとに transcriber を1回起動して処理する。"""
        try:
            self._file_watch_loop_impl()
        except Exception as e:
            _log("SYS", "ERROR", f"file_watch_loop error: {e}")
            self._view and self._view.put_log(f"[UI] [ERROR] ファイル監視エラー: {e}")
        finally:
            self._stop_meter()
            if self._view:
                self._view.schedule(lambda: self._view.hide_onair())
                self._view.schedule(self._set_controls_idle)

    def _file_watch_loop_impl(self) -> None:
        """ファイル監視メインループ：新ファイルごとに transcriber --file を起動。"""
        audio_dir  = self._config.get("paths", "audio_dir")
        rec_sec    = self._config.getint("recording", "record_sec", fallback=30)

        seen: set[str] = set()
        start_cutoff = 0.0
        try:
            with open(START_FILE) as f:
                start_cutoff = float(f.read().strip())
        except Exception:
            pass

        POLL_SEC   = 1.0
        DRAIN_WAIT = 3

        def _new_files(directory: str, pattern: str) -> list[str]:
            return sorted(
                f for f in glob.glob(os.path.join(directory, pattern))
                if f not in seen and os.path.getmtime(f) >= start_cutoff
            )

        def _process_file(wav: str, source: str) -> None:
            seen.add(wav)
            _log("TR", "INFO", f"on-demand start ({source}): {os.path.basename(wav)}")
            self._view and self._view.put_log(f"[TR] 認識開始: {os.path.basename(wav)} ({source})")

            cmd = [
                sys.executable, "-X", "utf8",
                os.path.join(BASE, "transcriber.py"),
                "--file",   wav,
                "--output", self._transcript_file,
                "--source", source,
            ]
            if self._session_lang:
                cmd += ["--lang", self._session_lang]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            # pipe stdout to UI/log (reuse existing _pipe)
            self._tr_proc = proc   # _pipe の dashboard 更新に使用
            threading.Thread(target=self._pipe, args=(proc, "[Tr]"), daemon=True).start()
            proc.wait()

            # 検出言語を更新
            if os.path.exists(LANG_FILE):
                try:
                    with open(LANG_FILE, encoding="utf-8") as f:
                        detected = f.read().strip()
                    if detected and detected != self._session_lang:
                        self._session_lang = detected
                        _log("TR", "INFO", f"言語確定: {detected}")
                except Exception:
                    pass

            # dashboard_add_trans は _pipe が stdout 解析で実施するため、ここでは不要

        # ── メインループ ──────────────────────────────────────────────────────
        # 新方式: recorder が1セッション1ファイルに蓄積
        # → 録音中は subtitle_processor がリアルタイム転写を担当
        # → 停止後に最終 WAV ファイルが生成されたら処理する
        _log("SYS", "INFO", "file_watch_loop started (single-file mode)")

        # 停止シグナルを待つ
        while not self._fw_stop.is_set():
            time.sleep(POLL_SEC)

        # 停止後: recorder が WAV ファイルを書き終えるまで待つ
        _log("SYS", "INFO", "停止シグナル受信 → recorder の書き込み完了を待機...")
        time.sleep(DRAIN_WAIT + 2)   # recorder の WAV 変換を待つ余裕

        # 最終 WAV ファイルを処理（字幕プロセスが既に転写済みの場合はスキップ）
        subtitle_active = (self._sub_proc and self._sub_proc.poll() is None)
        if not subtitle_active:
            # 字幕なしモード: 最終 WAV を転写する (mic は pipeline でミックス済み)
            final_wavs = _new_files(audio_dir, "audio_*.wav")
            for wav in final_wavs:
                _process_file(wav, "loopback")
            _log("SYS", "INFO", f"最終転写完了: loopback={len(final_wavs)}")
        else:
            _log("SYS", "INFO", "字幕プロセスが転写済み → 転写スキップ")

        # transcript が作成されたら STATE_FILE に書く
        if self._transcript_file and os.path.exists(self._transcript_file):
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                f.write(self._transcript_file)
            if self._session_lang:
                with open(LANG_FILE, "w", encoding="utf-8") as f:
                    f.write(self._session_lang)

        if self._view:
            self._view.schedule(lambda: self._view.set_tr_status("stopped", "gray60"))

        # ── 以降は纪要生成パイプラインへ（旧 _after_trans_impl の後半）──────
        # この関数を呼び出した _file_watch_loop の finally で後処理、
        # 纪要は _run_summary_pipeline を別途呼ぶ
        threading.Thread(target=self._run_summary_pipeline, daemon=True).start()

    def _after_trans_impl(self) -> None:
        pass  # 旧互換（file_watch_loop に移行済み）

    def _run_summary_pipeline(self) -> None:
        """纪要生成パイプライン（file_watch_loop 終了後に呼ばれる）。"""
        try:
            self._run_summary_pipeline_impl()
        except Exception as e:
            _log("SYS", "ERROR", f"summary pipeline error: {e}")
            self._view and self._view.put_log(f"[UI] [ERROR] 纪要生成エラー: {e}")
        finally:
            self._running  = False
            self._stopping = False
            if self._view:
                self._view.schedule(self._set_controls_idle)

    def _run_summary_pipeline_impl(self) -> None:
        _sid = self._session_id   # capture; if start() runs again this changes → stale

        def _schedule_if_current(fn):
            """Only schedule UI update when still in the same session."""
            if self._session_id == _sid and self._view:
                self._view.schedule(fn)

        transcript_path = self._read_last_transcript()
        if transcript_path:
            self._view and self._view.put_log(f"[UI] Transcript file: {transcript_path}")
            # Raw transcript → Transcript tab (never overwritten by corrections).
            if self._view and hasattr(self._view, "show_transcript"):
                _p = transcript_path
                _schedule_if_current(lambda p=_p: self._view.show_transcript(p))
            # Corrected text → its own Corrected tab.
            _corrected_state = os.path.join(BASE, ".last_corrected")
            try:
                if os.path.exists(_corrected_state):
                    with open(_corrected_state, encoding="utf-8") as _f:
                        _cp = _f.read().strip()
                    if _cp and os.path.exists(_cp) and self._view \
                            and hasattr(self._view, "show_corrected"):
                        _schedule_if_current(lambda p=_cp: self._view.show_corrected(p))
            except Exception:
                pass
        else:
            self._view and self._view.put_log("[UI] Transcript file was not created")

        if transcript_path and os.path.exists(transcript_path):
            if self._view:
                _schedule_if_current(lambda: self._view.set_sum_status("generating", "#ddaa00"))
            from i18n import t

            # Minutes are generated directly from the real-time corrected file
            # produced per-segment during the session (pipeline.py). No second,
            # full re-correction pass — that would create a duplicate corrected
            # file and make the displayed text diverge from the minutes input.
            # Fall back to the raw transcript only if no corrected file exists
            # (e.g. correction disabled or the API was unavailable).
            _corrected_state = os.path.join(BASE, ".last_corrected")
            minutes_input = transcript_path
            try:
                if os.path.exists(_corrected_state):
                    with open(_corrected_state, encoding="utf-8") as f:
                        cp = f.read().strip()
                    if cp and os.path.exists(cp):
                        minutes_input = cp
                        self._view and self._view.put_log(f"[UI] Using corrected text for minutes: {cp}")
            except Exception:
                pass

            self._view and self._view.put_log("[UI] Generating meeting minutes...")
            sum_proc2 = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py"),
                 "--step", "summary", minutes_input],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(sum_proc2, "[Sum]"), daemon=True).start()
            self._wait_process(sum_proc2, "summarizer.py (summary)", timeout_sec=600)

            summary = self._latest_file(
                self._config.get("summary", "summary_dir", fallback=""), "summary_*.md")
            if summary:
                self._view and self._view.put_log(f"[UI] Summary file: {summary}")
                if self._view and hasattr(self._view, "show_minutes"):
                    _p = summary
                    _schedule_if_current(lambda p=_p: self._view.show_minutes(p))

            _schedule_if_current(lambda: self._view.set_sum_status("done", "#44dd44"))
        else:
            self._view and self._view.put_log(
                "[UI] Summary skipped because transcript file is missing"
            )
            if self._view:
                self._view.schedule(lambda: self._view.set_sum_status("skipped", "gray60"))

        from i18n import t
        self._view and self._view.put_log(t("all_done"))

    def _check_summary_backend(self) -> bool:
        """Return True if the summary backend is reachable (placeholder)."""
        return True

    # ── window lifecycle ──────────────────────────────────────────────────────

    def on_close(self) -> None:
        # Second close press while already shutting down → force quit now.
        if self._closing:
            self._view and self._view.put_log("[UI] 強制終了します")
            self._force_quit()
            return
        self._closing = True
        # Wait for background work (final transcription, MP3 conversion, minutes)
        # to finish before actually closing, instead of killing it mid-way.
        self._view and self._view.put_log(
            "[UI] 終了処理中… バックグラウンド処理の完了を待っています"
            "（もう一度閉じると強制終了）"
        )
        if self._view:
            self._view.schedule(lambda: self._view.set_start_enabled(False))
            self._view.schedule(lambda: self._view.set_stop_enabled(False))
        threading.Thread(target=self._graceful_shutdown, daemon=True).start()

    def _graceful_shutdown(self) -> None:
        """Finish outstanding background work, then close the window.
        Runs off the Qt thread so the UI stays responsive while waiting."""
        try:
            # 1. If still recording, stop to finalize the current session
            #    (flush audio, transcribe remainder, start MP3 conversion, summary).
            if self._running and not self._stopping:
                self.stop()
            # 2. Wait for the finalize + summarize chain to complete (bounded).
            deadline = time.time() + 300
            while self._running and time.time() < deadline:
                time.sleep(0.5)
            # 3. Stop the long-lived pipeline; give its shutdown enough time to
            #    finish any in-progress MP3 conversion before the process exits.
            self._stop_pipeline(timeout=180)
            self._stop_ollama()
        except Exception as e:
            _log("SYS", "ERROR", f"graceful shutdown error: {e}")
        finally:
            if self._view:
                self._view.schedule(self._view.destroy)

    def force_quit(self) -> None:
        """Immediate kill – used on second close attempt."""
        self._force_quit()

    def _force_quit(self) -> None:
        for proc in (self._rec_proc, self._tr_proc, self._ollama_proc,
                     self._pipeline_proc):
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
        import re, wave as _wave
        _tr_file_re  = re.compile(r"\d+/\d+:\s+(\S+\.wav)")
        _rec_save_re = re.compile(r"保存|Saved")
        _fname_re    = re.compile(r"((?:audio|mic)_\S+\.wav)")
        _tr_done_re  = re.compile(r"done[^\(]*\((loopback|mic)\):\s+(\S+\.wav)")
        _rec_sec  = self._config.getint("recording", "record_sec", fallback=30)
        audio_dir = self._config.get("paths", "audio_dir", fallback="")
        # Primary source for dashboard timers (avoids double-counting).
        primary_audio_prefix = "[Rec]"
        primary_trans_source = "loopback"

        # Pipeline dashboard patterns
        _pl_vad_turn  = re.compile(r"VAD turn-end.*in ([\d.]+)s window")
        _pl_vad_force = re.compile(r"VAD force-flush: ([\d.]+)s accumulated")
        _pl_whisper_in  = re.compile(r"Transcribing ([\d.]+)s system audio")
        _pl_whisper_out = re.compile(r"Transcribed in [\d.]+s:")
        _pl_pending = [0.0]  # pending audio secs for trans counter

        for line in proc.stdout:
            text = line.rstrip()
            if not text:
                continue

            # 構造化ログを先に解析し、正規表現は msg 部分に適用する
            parsed = parse_log_line(text)
            search_text = parsed[3] if parsed else text  # msg or raw text

            if prefix == "[Tr]":
                m = _tr_file_re.search(search_text)
                if m:
                    self._tr_current_file  = m.group(1)
                    self._tr_last_activity = time.monotonic()
                dm = _tr_done_re.search(search_text)
                if dm and self._view and self._running:
                    source, fname = dm.group(1), dm.group(2)
                    if source == primary_trans_source:
                        secs = self._wav_secs(os.path.join(audio_dir, "done", fname))
                        if secs <= 0: secs = float(_rec_sec)
                        self._view.schedule(lambda s=secs: self._view.dashboard_add_trans(s))

            elif prefix == "[PL]":
                # Pipeline VAD flush → update audio counter (middle)
                m = _pl_vad_turn.search(search_text)
                if not m:
                    m = _pl_vad_force.search(search_text)
                if m and self._view and self._running:
                    secs = float(m.group(1))
                    self._view.schedule(lambda s=secs: self._view.dashboard_add_audio(s))

                # Pipeline transcription start → track segment duration
                m2 = _pl_whisper_in.search(search_text)
                if m2:
                    _pl_pending[0] = float(m2.group(1))

                # Pipeline transcription complete → update trans counter (right)
                if _pl_whisper_out.search(search_text) and _pl_pending[0] > 0:
                    secs = _pl_pending[0]
                    _pl_pending[0] = 0.0
                    if self._view and self._running:
                        self._view.schedule(lambda s=secs: self._view.dashboard_add_trans(s))

            elif prefix in ("[Rec]", "[Mic]"):
                # Count only the primary recorder so loopback + mic don't double-count
                if prefix == primary_audio_prefix and _rec_save_re.search(search_text) and self._running:
                    fm = _fname_re.search(search_text)
                    if fm and self._view:
                        fname = fm.group(1)
                        secs  = self._wav_secs(os.path.join(audio_dir, fname))
                        if secs <= 0: secs = float(_rec_sec)
                        self._view.schedule(lambda s=secs: self._view.dashboard_add_audio(s))

            # ── ログファイル書き込み + UI 表示フィルタリング ──────────
            parsed = parse_log_line(text)
            if parsed:
                cat, lvl, ts, msg = parsed
                # ファイルには全て書き込む
                if _log_cfg.should_write_file(lvl):
                    _file_log.write_raw(cat, lvl, ts, msg)
                # UI には設定に応じてフィルタリング
                if self._view and _log_cfg.should_show_ui(cat, lvl):
                    self._view.put_log(f"[{cat}] {msg}")
            else:
                # 非構造化行（旧形式）は全てファイルとUIに出力
                _file_log.write(f"{prefix} {text}")
                if self._view:
                    self._view.put_log(f"{prefix} {text}")

    def _wait_process(self, proc, name: str,
                      timeout_sec: int | None = None,
                      interval_sec: int = 5) -> bool:
        if not proc:
            return True
        start    = time.monotonic()
        next_log = interval_sec
        _recording_msg_shown = False
        _last_seen_activity  = self._tr_last_activity

        while proc.poll() is None:
            elapsed = int(time.monotonic() - start)

            if name == "transcriber.py":
                now_act = self._tr_last_activity
                if now_act != _last_seen_activity:
                    _last_seen_activity  = now_act
                    _recording_msg_shown = False
                    next_log = elapsed + interval_sec

                if elapsed >= next_log:
                    quiet = (time.monotonic() - self._tr_last_activity) > 3.0
                    if self._running and quiet and not _recording_msg_shown:
                        self._view and self._view.put_log(
                            "[UI] 収録中... 次のチャンクを待っています")
                        _recording_msg_shown = True
                        next_log = elapsed + interval_sec
                    elif not self._running:
                        if self._tr_current_file:
                            self._view and self._view.put_log(
                                f"[UI] 転写中: {self._tr_current_file}  elapsed={elapsed}s")
                        next_log = elapsed + interval_sec
            else:
                if elapsed >= next_log:
                    self._view and self._view.put_log(
                        f"[UI] Waiting for {name} to finish... elapsed={elapsed}s")
                    next_log = elapsed + interval_sec

            if timeout_sec is not None and elapsed >= timeout_sec:
                msg = f"{name} did not finish within {timeout_sec}s — force terminated"
                _log("SYS", "ERROR", msg)
                self._view and self._view.put_log(f"[UI] [ERROR] {msg}")
                proc.terminate()
                if self._rec_proc and self._rec_proc.poll() is None:
                    _log("SYS", "WARN", "Stopping recorder due to process timeout")
                    self._view and self._view.put_log("[UI] [WARN] レコーダーを停止します")
                    self._rec_proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _log("SYS", "ERROR", f"{name} still running after terminate — killing")
                    self._view and self._view.put_log(f"[UI] [ERROR] {name} kill 送信")
                    proc.kill()
                    proc.wait()
                return False
            time.sleep(0.5)

        rc = proc.returncode
        if rc == 0 or rc == -2:   # 0=正常, -2=SIGINT(Ctrl+C)
            _log("SYS", "INFO",  f"{name} exited normally (code={rc})")
        else:
            _log("SYS", "ERROR", f"{name} exited with error code={rc}")
            self._view and self._view.put_log(f"[UI] [ERROR] {name} 異常終了 code={rc}")
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

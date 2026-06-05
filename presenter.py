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
    def show_ptt_button(self): ...
    def hide_ptt_button(self): ...


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
        self._mic_proc    = None
        self._ollama_proc = None

        # ── on-demand transcriber (file watcher) ──────────────────────────────
        self._fw_stop      = threading.Event()   # set when stop() is called
        self._fw_thread: threading.Thread | None = None
        self._session_lang: str | None = None    # detected/fixed language for session
        self._transcript_file: str | None = None # current session transcript path

        # ── subtitle ──────────────────────────────────────────────────────────
        self._sub_proc: subprocess.Popen | None = None
        self._sub_win_proc: subprocess.Popen | None = None

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
        _log("SYS", "INFO", f"View set: {type(view).__name__}")

    def _log(self, msg: str, level: str = "info") -> None:
        """Write to both the UI log panel and the log file."""
        lvl_map = {"debug": "DEBUG", "info": "INFO", "warning": "WARN", "error": "ERROR"}
        _log("UI", lvl_map.get(level, "INFO"), msg)
        if self._view:
            self._view.put_log(msg)

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

    def start_mic(self) -> None:
        """Start mic_recorder.py (Push-to-Talk ON)."""
        if not self._running:
            return
        if self._mic_proc and self._mic_proc.poll() is None:
            return  # already running
        mic_dir = self._config.get("paths", "mic_dir", fallback=r"C:\Users\Public\Sound2Text\mic")
        self._view and self._view.put_log(f"[UI] 発言開始 — mic_dir: {mic_dir}")
        # Pass --ptt flag so mic_recorder.py ignores the enable_mic setting
        self._mic_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "mic_recorder.py"), "--ptt"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        self._view and self._view.put_log(f"[UI] mic_recorder.py started: pid={self._mic_proc.pid}")
        threading.Thread(target=self._pipe, args=(self._mic_proc, "[Mic]"), daemon=True).start()
        if self._view:
            self._view.schedule(lambda: self._view.show_onair())
        self._start_meter()

    def toggle_mic(self) -> None:
        """Toggle mic recording. Called when user clicks the VU meter."""
        if not self._running:
            return
        if self._mic_proc and self._mic_proc.poll() is None:
            self.stop_mic()
        else:
            self.start_mic()

    def stop_mic(self) -> None:
        """Stop mic_recorder.py (Push-to-Talk OFF) — save partial frames first."""
        self._stop_meter()
        if self._view:
            self._view.schedule(lambda: self._view.hide_onair())
        if self._mic_proc and self._mic_proc.poll() is None:
            # Write PTT stop signal so mic_recorder saves the partial chunk before exiting
            try:
                with open(os.path.join(BASE, ".ptt_stop"), "w") as f:
                    f.write("stop")
            except Exception:
                self._mic_proc.terminate()  # fallback
            # Wait up to 5 seconds for graceful exit; force-kill if needed
            try:
                self._mic_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._mic_proc.kill()
            self._view and self._view.put_log("[UI] 発言終了 — マイク録音を停止しました")
        self._mic_proc = None

    @property
    def cuda_available(self) -> bool:
        """Whether CUDA GPU is usable (view uses this instead of importing appconfig)."""
        return _CUDA_AVAILABLE and _CUDA_LIBS_OK

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
                import pyaudiowpatch as _pa
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

    def start_subtitle(self, dst_lang: str = "") -> None:
        """字幕プロセスと字幕ウィンドウを起動する。"""
        self.stop_subtitle()   # 既存があれば停止

        # config に dst_lang を書き込む
        self.save_config({("subtitle", "dst_lang"): dst_lang})

        # subtitle_processor.py を起動
        self._sub_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "subtitle_processor.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=self._env,
        )
        threading.Thread(
            target=self._pipe, args=(self._sub_proc, "[Sub]"), daemon=True
        ).start()
        _log("SYS", "INFO", f"subtitle_processor started pid={self._sub_proc.pid}")

        # subtitle_window.py を起動（別プロセス）
        self._sub_win_proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", os.path.join(BASE, "subtitle_window.py")],
            env=self._env,
        )
        _log("SYS", "INFO", f"subtitle_window started pid={self._sub_win_proc.pid}")
        self._view and self._view.put_log("[UI] 字幕ウィンドウを起動しました")

    def stop_subtitle(self) -> None:
        """字幕プロセスを停止する。"""
        for proc, name in [(self._sub_proc, "subtitle_processor"),
                           (self._sub_win_proc, "subtitle_window")]:
            if proc and proc.poll() is None:
                proc.terminate()
                _log("SYS", "INFO", f"{name} stopped")
        self._sub_proc     = None
        self._sub_win_proc = None
        # 字幕テキストをクリア
        try:
            subtitle_file = os.path.join(BASE, ".subtitle_text")
            with open(subtitle_file, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

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
            import pyaudiowpatch as pyaudio
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
                # Gate: if mic_recorder.py has exited, level must be 0
                # (hardware stream may still work even if the recorder crashed)
                mic_running = (
                    self._mic_proc is not None
                    and self._mic_proc.poll() is None
                )
                if not mic_running:
                    # Recording stopped or failed — show 0 and quit meter
                    if self._view:
                        self._view.schedule(lambda: self._view.set_onair_level(0.0))
                        self._view.schedule(lambda: self._view.hide_onair())
                    self._meter_active = False
                    break

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

        # Clean up signal files (including any stale PTT stop signal)
        for path in (STOP_SIGNAL, STATE_FILE, LANG_FILE,
                     os.path.join(BASE, ".ptt_stop")):
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
        self._view.schedule(lambda: self._view.dashboard_start())
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
        enable_mic = self._config.getboolean("recording", "enable_mic", fallback=True)

        # Loopback recorder (meeting mode only)
        self._rec_proc = None
        if rec_mode == "meeting":
            self._view.put_log("[UI] Launching recorder.py (loopback)")
            self._rec_proc = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "recorder.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(self._rec_proc, "[Rec]"), daemon=True).start()
            self._view.put_log(f"[UI] recorder.py started: pid={self._rec_proc.pid}")
        else:
            self._view.put_log("[UI] Local Mic mode: loopback recorder skipped")

        # Mic is not auto-started in either mode.
        # User clicks the VU meter to start/stop mic via toggle_mic().
        self._mic_proc = None
        self._view.put_log("[UI] VU メーターをクリックするとマイク録音を開始/停止します")

        # Transcriber: on-demand per-file mode
        # セッション用 transcript ファイルを確定
        transcript_dir = self._config.get("paths", "transcript_dir",
                                          fallback=r"C:\Users\Public\Sound2Text\transcript")
        os.makedirs(transcript_dir, exist_ok=True)
        self._transcript_file = os.path.join(
            transcript_dir,
            f"transcript_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        # 設定言語を読み込む（auto の場合は最初のファイルで検出）
        cfg_lang = self._config.get("recording", "language", fallback="auto").strip().lower()
        self._session_lang = cfg_lang if cfg_lang in {"zh", "ja", "en"} else None

        self._fw_stop.clear()
        self._fw_thread = threading.Thread(
            target=self._file_watch_loop, daemon=True
        )
        self._fw_thread.start()
        self._view.put_log(f"[UI] ファイル監視開始 → {self._transcript_file}")
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

        # ファイル監視スレッドに停止を通知（残ファイルを処理してから終了）
        self._fw_stop.set()

        if self._rec_proc and self._rec_proc.poll() is None:
            self._view and self._view.put_log("[UI] Stopping recorder.py")
            self._rec_proc.terminate()
            # terminate() は Windows では即時強制終了 → finally が動かない
            # → RAW ファイルが残る場合は presenter 側で WAV 変換する
            threading.Thread(target=self._finalize_recorder_raw, daemon=True).start()
        else:
            self._view and self._view.put_log("[UI] recorder.py is already stopped")

        if self._mic_proc and self._mic_proc.poll() is None:
            self._view and self._view.put_log("[UI] Stopping mic_recorder.py")
            self._mic_proc.terminate()

        if self._view:
            self._view.schedule(lambda: self._view.set_rec_status("stopped", "gray60"))
            self._view.schedule(lambda: self._view.hide_onair())
            self._view.schedule(lambda: self._view.hide_ptt_button())
            self._view.schedule(lambda: self._view.dashboard_stop())
        self._stop_meter()
        self._view and self._view.put_log("[UI] 残り音声ファイルを処理してから終了します")

    # ── on-demand file watcher ────────────────────────────────────────────────

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
        mic_dir    = self._config.get("paths", "mic_dir", fallback="")
        rec_mode   = self._config.get("recording", "mode", fallback="meeting").strip().lower()
        rec_sec    = self._config.getint("recording", "record_sec", fallback=30)
        use_loopback = (rec_mode != "local_mic")

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
            # 字幕なしモード: 最終 WAV を転写する
            final_wavs = _new_files(audio_dir, "audio_*.wav")
            if use_loopback:
                for wav in final_wavs:
                    _process_file(wav, "loopback")
            mic_wavs = _new_files(mic_dir, "mic_*.wav") if mic_dir and os.path.isdir(mic_dir) else []
            for wav in mic_wavs:
                _process_file(wav, "mic")
            _log("SYS", "INFO", f"最終転写完了: loopback={len(final_wavs)} mic={len(mic_wavs)}")
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

            # ── Step 1: Correction (runs first, gives user corrected text ASAP) ──
            self._view and self._view.put_log("[UI] Starting correction (Step 1/2)...")
            sum_proc1 = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py"),
                 "--step", "correct", transcript_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(sum_proc1, "[Sum]"), daemon=True).start()
            self._wait_process(sum_proc1, "summarizer.py (correct)", timeout_sec=600)

            corrected = self._latest_file(
                self._config.get("summary", "corrected_dir", fallback=""), "corrected_*.txt")
            if corrected:
                self._view and self._view.put_log(f"[UI] Corrected text: {corrected}")

            # ── Step 2: Meeting minutes ─────────────────────────────────────────
            self._view and self._view.put_log("[UI] Generating meeting minutes (Step 2/2)...")
            sum_proc2 = subprocess.Popen(
                [sys.executable, "-X", "utf8", os.path.join(BASE, "summarizer.py"),
                 "--step", "summary", transcript_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=self._env,
            )
            threading.Thread(target=self._pipe, args=(sum_proc2, "[Sum]"), daemon=True).start()
            self._wait_process(sum_proc2, "summarizer.py (summary)", timeout_sec=600)

            summary = self._latest_file(
                self._config.get("summary", "summary_dir", fallback=""), "summary_*.md")
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
        import re, wave as _wave
        _tr_file_re  = re.compile(r"\d+/\d+:\s+(\S+\.wav)")
        _rec_save_re = re.compile(r"保存|Saved")
        _fname_re    = re.compile(r"((?:audio|mic)_\S+\.wav)")
        _tr_done_re  = re.compile(r"done[^\(]*\((loopback|mic)\):\s+(\S+\.wav)")
        _rec_sec  = self._config.getint("recording", "record_sec", fallback=30)
        audio_dir = self._config.get("paths", "audio_dir", fallback="")
        mic_dir   = self._config.get("paths", "mic_dir",   fallback=r"C:\Users\Public\Sound2Text\mic")
        rec_mode  = self._config.get("recording", "mode",  fallback="meeting").strip().lower()

        # Primary source for dashboard timers (avoids double-counting).
        # In meeting mode the loopback file covers the full session duration;
        # in local_mic mode only mic files exist.
        primary_audio_prefix = "[Rec]" if rec_mode == "meeting" else "[Mic]"
        primary_trans_source = "loopback" if rec_mode == "meeting" else "mic"

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
                # Count only the primary source so loopback + mic don't double-count
                if dm and self._view and self._running:
                    source, fname = dm.group(1), dm.group(2)
                    if source == primary_trans_source:
                        done_base = mic_dir if source == "mic" else audio_dir
                        secs = self._wav_secs(os.path.join(done_base, "done", fname))
                        if secs <= 0: secs = float(_rec_sec)
                        self._view.schedule(lambda s=secs: self._view.dashboard_add_trans(s))

            elif prefix in ("[Rec]", "[Mic]"):
                # Count only the primary recorder so loopback + mic don't double-count
                if prefix == primary_audio_prefix and _rec_save_re.search(search_text) and self._running:
                    fm = _fname_re.search(search_text)
                    if fm and self._view:
                        fname = fm.group(1)
                        base  = mic_dir if fname.startswith("mic_") else audio_dir
                        secs  = self._wav_secs(os.path.join(base, fname))
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

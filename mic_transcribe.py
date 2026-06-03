"""
Quick mic transcription window.
Speak into the mic and see real-time transcription.
Launch: python mic_transcribe.py  or  from main UI
"""
import os
import sys
import queue
import threading
import configparser
import warnings
import customtkinter as ctk

warnings.filterwarnings("ignore")

BASE     = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(BASE, "config.ini")

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

_LANG_LABELS = {
    "auto": "Auto", "zh": "中文", "ja": "日本語", "en": "English"
}
_LABEL_TO_CODE = {v: k for k, v in _LANG_LABELS.items()}


class MicTranscribeWindow(ctk.CTkToplevel):
    """
    Small floating window for quick mic transcription.
    Can be used as a standalone app or opened from the main UI.
    """

    def __init__(self, master=None):
        super().__init__(master)
        self.title("🎤 Voice Note")
        self.geometry("520x420")
        self.minsize(400, 300)
        self.resizable(True, True)
        if master:
            self.transient(master)

        self._cfg = configparser.ConfigParser()
        self._cfg.read(CFG_FILE, encoding="utf-8")

        self._running   = False
        self._log_q     = queue.Queue()
        self._stream    = None
        self._pa        = None
        self._whisper   = None
        self._model_loaded = False
        self._output_file  = ""

        self._build()
        self._poll()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI 構築 ───────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── 上部コントロール ──
        top = ctk.CTkFrame(self, corner_radius=8)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.grid_columnconfigure(2, weight=1)

        self._btn = ctk.CTkButton(
            top, text="▶  Start", width=110, height=40,
            fg_color="#1a7a1a", hover_color="#155e15",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle,
        )
        self._btn.grid(row=0, column=0, padx=(10, 6), pady=10)

        # 言語セレクター
        lang_val = self._cfg.get("recording", "language", fallback="auto")
        self._lang_var = ctk.StringVar(value=_LANG_LABELS.get(lang_val, "Auto"))
        ctk.CTkOptionMenu(
            top, values=list(_LANG_LABELS.values()),
            variable=self._lang_var, width=110, height=34,
        ).grid(row=0, column=1, padx=6, pady=10)

        self._status_lbl = ctk.CTkLabel(
            top, text="Ready", text_color="gray60",
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._status_lbl.grid(row=0, column=2, padx=8, sticky="w")

        # ── テキストエリア ──
        self._textbox = ctk.CTkTextbox(
            self, font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word", state="disabled",
        )
        self._textbox.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)

        # ── 下部ボタン ──
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        bot.grid_columnconfigure(0, weight=1)

        self._file_lbl = ctk.CTkLabel(
            bot, text="", text_color="gray60",
            font=ctk.CTkFont(size=11), anchor="w",
        )
        self._file_lbl.grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            bot, text="Copy All", width=90, height=28,
            fg_color="gray40", hover_color="gray30",
            command=self._copy_all,
        ).grid(row=0, column=1, padx=4)

        ctk.CTkButton(
            bot, text="Clear", width=70, height=28,
            fg_color="gray40", hover_color="gray30",
            command=self._clear,
        ).grid(row=0, column=2)

    # ── 録音・転写制御 ────────────────────────────────────────────────────────

    def _toggle(self):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._running = True
        self._btn.configure(text="■  Stop", fg_color="#8b1a1a", hover_color="#6e1414")
        self._status_lbl.configure(text="Loading model...", text_color="#ddaa00")
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _stop(self):
        self._running = False
        self._btn.configure(text="▶  Start", fg_color="#1a7a1a", hover_color="#155e15")
        self._status_lbl.configure(text="Stopping...", text_color="gray60")

    def _run_loop(self):
        """Main recording + transcription loop (runs in background thread)."""
        import numpy as np
        import pyaudiowpatch as pyaudio
        from faster_whisper import WhisperModel

        cfg = configparser.ConfigParser()
        cfg.read(CFG_FILE, encoding="utf-8")

        transcript_dir = cfg.get("paths", "transcript_dir",
                                  fallback=r"C:\Users\Public\Sound2Text\transcript")
        os.makedirs(transcript_dir, exist_ok=True)

        # Language
        lang_label = self._lang_var.get()
        lang = _LABEL_TO_CODE.get(lang_label, "auto")
        if lang == "auto":
            lang = None  # Whisper auto-detect

        # Whisper model
        if not self._model_loaded:
            model_size = cfg.get("recording", "model_size", fallback="small")
            device     = cfg.get("recording", "device",     fallback="auto").lower()
            if device == "auto":
                try:
                    import ctranslate2, ctypes
                    if ctranslate2.get_cuda_device_count() > 0:
                        cublas_ok = any(
                            _dll_exists(d) for d in
                            ["cublas64_13.dll", "cublas64_12.dll", "cublas64_11.dll", "cublas.dll"]
                        )
                        device = "cuda" if cublas_ok else "cpu"
                    else:
                        device = "cpu"
                except Exception:
                    device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            self._log_q.put(("status", f"Loading {model_size} on {device}..."))
            self._whisper = WhisperModel(model_size, device=device, compute_type=compute)
            self._model_loaded = True

        # Output file
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._output_file = os.path.join(transcript_dir, f"voice_note_{ts}.txt")
        self._log_q.put(("file", self._output_file))

        # Mic device
        pa = pyaudio.PyAudio()
        device_index, dev_info = None, None
        try:
            info = pa.get_default_input_device_info()
            if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice", False):
                device_index = int(info["index"])
                dev_info = info
        except Exception:
            pass
        if device_index is None:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice", False):
                    device_index, dev_info = i, info
                    break

        if device_index is None:
            self._log_q.put(("status", "No microphone found!"))
            self._running = False
            pa.terminate()
            return

        channels    = min(dev_info["maxInputChannels"], 1)
        sample_rate = int(dev_info["defaultSampleRate"])
        chunk       = 1024
        rec_sec     = 5   # transcribe every 5 seconds for low latency
        n_chunks    = int(sample_rate / chunk * rec_sec)

        self._log_q.put(("status", f"Recording: {dev_info['name'][:30]}"))
        self._log_q.put(("text",   f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n"))

        stream = pa.open(
            format=pyaudio.paInt16, channels=channels, rate=sample_rate,
            frames_per_buffer=chunk, input=True, input_device_index=device_index,
        )

        import wave, tempfile
        from datetime import datetime as dt

        with open(self._output_file, "w", encoding="utf-8-sig") as out:
            out.write(f"=== Voice Note {dt.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")

            while self._running:
                try:
                    frames = [stream.read(chunk, exception_on_overflow=False)
                              for _ in range(n_chunks)]
                except Exception:
                    break

                audio_np = np.frombuffer(b"".join(frames), dtype=np.int16)
                level    = int(np.abs(audio_np).mean())

                if level < 30:   # near-silence, skip
                    continue

                # Write temp WAV and transcribe
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp = f.name
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
                    wf.setframerate(sample_rate)
                    wf.writeframes(b"".join(frames))

                try:
                    segments, info = self._whisper.transcribe(
                        tmp, language=lang,
                        vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 300},
                    )
                    for seg in segments:
                        text = seg.text.strip()
                        if text:
                            ts_str = dt.now().strftime("%H:%M:%S")
                            line   = f"[{ts_str}] {text}"
                            self._log_q.put(("text", line + "\n"))
                            out.write(line + "\n")
                            out.flush()
                finally:
                    os.remove(tmp)

        stream.stop_stream()
        stream.close()
        pa.terminate()

        self._log_q.put(("status", f"Saved: {os.path.basename(self._output_file)}"))
        self._log_q.put(("text",   f"--- Ended {dt.now().strftime('%H:%M:%S')} ---\n"))

    # ── ログポーリング ────────────────────────────────────────────────────────

    def _poll(self):
        while not self._log_q.empty():
            msg_type, payload = self._log_q.get_nowait()
            if msg_type == "text":
                self._textbox.configure(state="normal")
                self._textbox.insert("end", payload)
                self._textbox.see("end")
                self._textbox.configure(state="disabled")
            elif msg_type == "status":
                self._status_lbl.configure(text=payload)
                if not self._running:
                    self._btn.configure(text="▶  Start",
                                        fg_color="#1a7a1a", hover_color="#155e15")
            elif msg_type == "file":
                self._file_lbl.configure(text=os.path.basename(payload))
        self.after(100, self._poll)

    # ── ユーティリティ ────────────────────────────────────────────────────────

    def _copy_all(self):
        text = self._textbox.get("1.0", "end")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status_lbl.configure(text="Copied!")

    def _clear(self):
        self._textbox.configure(state="normal")
        self._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")

    def _on_close(self):
        self._running = False
        self.destroy()


def _dll_exists(name: str) -> bool:
    import ctypes
    try:
        ctypes.CDLL(name)
        return True
    except OSError:
        return False


# ── スタンドアロン起動 ────────────────────────────────────────────────────────

class _StandaloneApp(ctk.CTk):
    """Minimal host window for standalone mode."""
    def __init__(self):
        super().__init__()
        self.withdraw()  # hide host window
        self._win = MicTranscribeWindow(self)
        self._win.protocol("WM_DELETE_WINDOW", self._quit)

    def _quit(self):
        self._win.destroy()
        self.destroy()


if __name__ == "__main__":
    app = _StandaloneApp()
    app.mainloop()

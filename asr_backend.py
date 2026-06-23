"""Speech-to-text backends for Sound2Text.

Nemotron 3.5 ASR is optional because NeMo/PyTorch installations are large and
platform-sensitive. The factory prefers Nemotron when configured, then falls
back to the existing faster-whisper backend without interrupting recording.
"""
from __future__ import annotations

import glob
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np


LogFn = Callable[[str], None]


_LANG_TO_NEMOTRON = {
    "zh": "zh-CN",
    "ja": "ja-JP",
    "en": "en-US",
}

_TAG_TO_LANG = {
    "zh-cn": "zh",
    "zh": "zh",
    "ja-jp": "ja",
    "ja": "ja",
    "en-us": "en",
    "en-gb": "en",
    "en": "en",
}


@dataclass
class ASRResult:
    segments: list[Any]
    info: Any


def register_cuda_dlls() -> None:
    """Make CUDA DLLs from toolkit or pip nvidia packages visible on Windows."""
    if not hasattr(os, "add_dll_directory"):
        return

    dirs: list[str] = []
    cuda_root = os.environ.get("CUDA_PATH", "")
    if cuda_root:
        dirs += [os.path.join(cuda_root, "bin"), os.path.join(cuda_root, "bin", "x64")]

    toolkit_base = r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"
    if os.path.isdir(toolkit_base):
        for version in sorted(os.listdir(toolkit_base), reverse=True):
            dirs += [
                os.path.join(toolkit_base, version, "bin"),
                os.path.join(toolkit_base, version, "bin", "x64"),
            ]

    nvidia_base = os.path.join(sysconfig.get_paths().get("purelib", ""), "nvidia")
    dirs += glob.glob(os.path.join(nvidia_base, "*", "bin"))

    for item in dirs:
        if os.path.isdir(item):
            try:
                os.add_dll_directory(item)
            except Exception:
                pass


def _add_sibling_nemo_venv(log: LogFn | None = None) -> bool:
    """Reuse jidori-talk's NeMo venv when it matches this Python version."""
    here = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.join(os.path.dirname(here), "jidori-talk", ".venv")
    site_packages = os.path.join(sibling, "Lib", "site-packages")
    pyvenv = os.path.join(sibling, "pyvenv.cfg")
    if not os.path.isdir(site_packages):
        return False

    version_ok = True
    try:
        with open(pyvenv, encoding="utf-8") as f:
            cfg_text = f.read().lower()
        expected = f"{sys.version_info.major}.{sys.version_info.minor}"
        version_ok = f"version = {expected}" in cfg_text or f"version_info = {expected}" in cfg_text
    except Exception:
        pass
    if not version_ok:
        return False

    if site_packages not in sys.path:
        sys.path.insert(0, site_packages)
        if log:
            log(f"Reusing NeMo packages from {site_packages}")

    if hasattr(os, "add_dll_directory"):
        for pattern in (
            os.path.join(site_packages, "nvidia", "*", "bin"),
            os.path.join(site_packages, "torch", "lib"),
            os.path.join(site_packages, "torchaudio", "lib"),
        ):
            for item in glob.glob(pattern):
                if os.path.isdir(item):
                    try:
                        os.add_dll_directory(item)
                    except Exception:
                        pass
    return True


def pcm16_to_float32(pcm_bytes: bytes) -> np.ndarray:
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


class WhisperBackend:
    name = "whisper"
    supports_streaming = False

    def __init__(
        self,
        model_path: str,
        device: str,
        compute_type: str,
        transcribe_kwargs: dict,
    ) -> None:
        register_cuda_dlls()
        from faster_whisper import WhisperModel

        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.transcribe_kwargs = dict(transcribe_kwargs)
        self.model = WhisperModel(model_path, device=device, compute_type=compute_type)

    def transcribe(self, wav_path: str, language: str | None, initial_prompt: str | None) -> ASRResult:
        segs, info = self.model.transcribe(
            wav_path,
            language=language,
            initial_prompt=initial_prompt,
            **self.transcribe_kwargs,
        )
        return ASRResult(list(segs), info)


class NemotronStream:
    """Cache-aware streaming session for raw 16 kHz mono PCM chunks."""

    def __init__(self, backend: "NemotronBackend") -> None:
        self.backend = backend
        self.model = backend.model
        self._text = ""
        self._ok = True
        try:
            from nemo.collections.asr.parts.utils.streaming_utils import (
                CacheAwareStreamingAudioBuffer,
                extract_transcriptions,
            )

            self._extract = extract_transcriptions
            self.buffer = CacheAwareStreamingAudioBuffer(
                model=self.model, online_normalization=False
            )
            (
                self.cache_last_channel,
                self.cache_last_time,
                self.cache_last_channel_len,
            ) = self.model.encoder.get_initial_cache_state(batch_size=1)
            self.previous_hypotheses = None
            self.pred_out_stream = None
            self.step_num = 0
        except Exception:
            self._ok = False

    def accept(self, pcm_bytes: bytes) -> str:
        if not self._ok:
            return self._text
        try:
            import torch

            audio = pcm16_to_float32(pcm_bytes)
            self.buffer.append_audio(audio, stream_id=0)
            for chunk_audio, chunk_lengths in self.buffer:
                with torch.inference_mode():
                    (
                        self.pred_out_stream,
                        transcribed_texts,
                        self.cache_last_channel,
                        self.cache_last_time,
                        self.cache_last_channel_len,
                        self.previous_hypotheses,
                    ) = self.model.conformer_stream_step(
                        processed_signal=chunk_audio,
                        processed_signal_length=chunk_lengths,
                        cache_last_channel=self.cache_last_channel,
                        cache_last_time=self.cache_last_time,
                        cache_last_channel_len=self.cache_last_channel_len,
                        keep_all_outputs=self.buffer.is_buffer_empty(),
                        previous_hypotheses=self.previous_hypotheses,
                        previous_pred_out=self.pred_out_stream,
                        drop_extra_pre_encoded=self.step_num != 0,
                        return_transcription=True,
                    )
                self.step_num += 1
                texts = self._extract(transcribed_texts)
                if texts:
                    self._text = (texts[0] or "").strip()
        except Exception:
            self._ok = False
        return self._text


class NemotronBackend:
    name = "nemotron"
    supports_streaming = True

    def __init__(self, cfg, device: str, log: LogFn | None = None) -> None:
        register_cuda_dlls()
        try:
            import nemo.collections.asr as nemo_asr
        except ModuleNotFoundError:
            _add_sibling_nemo_venv(log)
            register_cuda_dlls()
            import nemo.collections.asr as nemo_asr

        self.cfg = cfg
        self.device = cfg.get("nemotron", "device", fallback=device).strip().lower()
        if self.device == "auto":
            self.device = device
        self.model_name = cfg.get(
            "nemotron",
            "model_name",
            fallback="nvidia/nemotron-3.5-asr-streaming-0.6b",
        ).strip()
        self.strip_lang_tags = cfg.getboolean("nemotron", "strip_lang_tags", fallback=True)
        self.log = log or (lambda _msg: None)
        self._current_target: str | None = None

        self.model = nemo_asr.models.ASRModel.from_pretrained(model_name=self.model_name)
        try:
            self.model = self.model.to(self.device)
        except Exception as exc:
            self.log(f"Nemotron could not move to {self.device}; using default device: {exc}")
        self.model.eval()

        att_context = cfg.get("nemotron", "att_context_size", fallback="[56, 3]").strip()
        try:
            import ast

            att_context_size = ast.literal_eval(att_context)
        except Exception:
            att_context_size = [56, 3]
        self.model.encoder.set_default_att_context_size(att_context_size=att_context_size)
        try:
            self.model.encoder.setup_streaming_params()
        except Exception:
            pass
        try:
            self.model.decoding.set_strip_lang_tags(self.strip_lang_tags)
        except Exception:
            pass

    def _target_lang(self, language: str | None) -> str:
        if language in _LANG_TO_NEMOTRON:
            return _LANG_TO_NEMOTRON[language]
        return self.cfg.get("nemotron", "target_lang", fallback="auto").strip() or "auto"

    def _set_prompt(self, language: str | None) -> str:
        target = self._target_lang(language)
        if target != self._current_target and hasattr(self.model, "set_inference_prompt"):
            self.model.set_inference_prompt(target)
            self._current_target = target
        return target

    def transcribe(self, wav_path: str, language: str | None, initial_prompt: str | None) -> ASRResult:
        target = self._set_prompt(language)
        try:
            result = self.model.transcribe([wav_path], target_lang=target)
        except TypeError:
            result = self.model.transcribe([wav_path])
        text = _nemo_text(result).strip()
        lang = _detect_lang_from_text(text, language, target)
        text = _strip_lang_tag(text)
        segment = SimpleNamespace(text=text, no_speech_prob=0.0, avg_logprob=0.0)
        info = SimpleNamespace(language=lang or "en", language_probability=1.0 if lang else 0.0)
        return ASRResult([segment] if text else [], info)

    def new_stream(self, language: str | None = None) -> NemotronStream | None:
        if not self.cfg.getboolean("nemotron", "partial_updates", fallback=True):
            return None
        self._set_prompt(language)
        stream = NemotronStream(self)
        return stream if stream._ok else None


def _nemo_text(result: Any) -> str:
    if not result:
        return ""
    item = result[0]
    if isinstance(item, str):
        return item
    return getattr(item, "text", str(item))


def _strip_lang_tag(text: str) -> str:
    return re.sub(r"\s*<[-A-Za-z]{2,8}(?:-[A-Za-z]{2,8})?>\s*$", "", text).strip()


def _detect_lang_from_text(text: str, requested: str | None, target: str) -> str | None:
    if requested in {"zh", "ja", "en"}:
        return requested
    m = re.search(r"<([-A-Za-z]{2,8}(?:-[A-Za-z]{2,8})?)>\s*$", text)
    if m:
        return _TAG_TO_LANG.get(m.group(1).lower())
    return _TAG_TO_LANG.get(target.lower())


def create_asr_backend(
    cfg,
    model_path: str,
    device: str,
    compute_type: str,
    transcribe_kwargs: dict,
    log_info: LogFn | None = None,
    log_warn: LogFn | None = None,
):
    backend = cfg.get("asr", "backend", fallback="whisper").strip().lower()
    fallback = cfg.get("asr", "fallback_backend", fallback="whisper").strip().lower()
    info = log_info or (lambda _msg: None)
    warn = log_warn or (lambda _msg: None)

    if backend == "nemotron":
        try:
            engine = NemotronBackend(cfg, device=device, log=info)
            info(f"ASR backend: Nemotron 3.5 ({engine.model_name}, device={engine.device})")
            return engine
        except Exception as exc:
            warn(f"Nemotron backend unavailable: {exc}")
            if fallback != "whisper":
                raise

    engine = WhisperBackend(model_path, device=device, compute_type=compute_type,
                            transcribe_kwargs=transcribe_kwargs)
    info(f"ASR backend: faster-whisper ({model_path}, device={device})")
    return engine

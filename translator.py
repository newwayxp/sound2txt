"""
translator.py – Live, per-line translation worker for the Transcript tab.

A long-running child process (spawned by presenter.py while translation mode is
ON) that watches the live raw transcript file and writes a SEPARATE translated
file — it never touches the corrected text or meeting minutes.

Flow
----
1. Read `.translate_target` (presence = ON; content = target lang code zh/ja/en).
2. Read `.last_transcript` → raw `transcript_<ts>.txt`; derive `<ts>` and write
   `translated_<ts>.txt` under `[translate] translated_dir`.
3. Translate every complete line (backlog first, then new lines live) via the
   LLM configured in `[summary]` (same OpenAI/Ollama settings as correction),
   keeping the `[HH:MM:SS]` timestamp prefix verbatim, and append to the output.
4. Point `.last_translated` at the output so the presenter can poll it.
5. Exit when `.translate_target` is removed / its lang changes / the parent kills
   us. On LLM failure a line falls back to the original text (so the tab is never
   blank) and the error is logged.

IPC mirrors the rest of the app (signal files in BASE); the LLM call mirrors
summarizer.py's `_call` but is self-contained here so the correction/minutes
code path is left completely untouched.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import sys
import time

import requests
import urllib3

_BASE = os.path.dirname(os.path.abspath(__file__))

TRANSLATE_TARGET = os.path.join(_BASE, ".translate_target")
LAST_TRANSCRIPT  = os.path.join(_BASE, ".last_transcript")
LAST_TRANSLATED  = os.path.join(_BASE, ".last_translated")
CFG_FILE         = os.path.join(_BASE, "config.ini")

_RETRY_STATUS   = {429, 500, 502, 503, 529}
_RETRY_ATTEMPTS = 3
_RETRY_MAX_WAIT = 8.0
POLL_INTERVAL   = 0.5

# Target language code → name used in the translation instruction.
_LANG_NAME = {"zh": "简体中文", "ja": "日本語", "en": "English"}

# `[HH:MM:SS] body` — the timestamp prefix is preserved; only the body is translated.
_TS_RE = re.compile(r"^(\[\d{1,3}:\d{2}:\d{2}\]\s*)?(.*)$")


def _log(msg: str) -> None:
    print(f"[Translator] {msg}", flush=True)


# ── LLM client (self-contained; mirrors summarizer.py) ─────────────────────────

def _network_kwargs(cfg: configparser.ConfigParser) -> dict:
    kwargs = {"verify": cfg.getboolean("network", "ssl_verify", fallback=True)}
    proxies = {}
    https_proxy = cfg.get("network", "https_proxy", fallback="")
    http_proxy  = cfg.get("network", "http_proxy",  fallback="")
    if https_proxy:
        proxies["https"] = https_proxy
    if http_proxy:
        proxies["http"] = http_proxy
    elif https_proxy:
        proxies["http"] = https_proxy
    if proxies:
        kwargs["proxies"] = proxies
    if not kwargs["verify"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return kwargs


def _retry_wait(resp, attempt: int) -> float:
    try:
        ra = resp.headers.get("Retry-After") if resp is not None else None
        if ra is not None:
            return min(_RETRY_MAX_WAIT, float(ra))
    except (TypeError, ValueError):
        pass
    return min(_RETRY_MAX_WAIT, 2.0 ** attempt)


def _call_openai(system: str, user: str, api_base: str, api_key: str,
                 model: str, request_kwargs: dict) -> str:
    url      = api_base.rstrip("/") + "/chat/completions"
    headers  = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    payload  = {"model": model, "messages": messages, "stream": True}
    timeout_sec = min(120, max(30, len(user) // 50 + 30))

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, stream=True,
                                 timeout=timeout_sec, **request_kwargs)
            resp.raise_for_status()
            parts = []
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = bytes(raw).decode("utf-8", errors="replace") if isinstance(raw, (bytes, memoryview)) else str(raw)
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                chunk = json.loads(data)
                text  = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if text:
                    parts.append(text)
            return "".join(parts)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in _RETRY_STATUS and attempt < _RETRY_ATTEMPTS:
                wait = _retry_wait(e.response, attempt)
                _log(f"API {status} (attempt {attempt}/{_RETRY_ATTEMPTS}) — retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < _RETRY_ATTEMPTS:
                wait = min(_RETRY_MAX_WAIT, 2.0 * attempt)
                _log(f"network error {type(e).__name__} (attempt {attempt}/{_RETRY_ATTEMPTS}) — retry in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise


def _call_ollama(system: str, user: str, model: str, base_url: str,
                 request_kwargs: dict) -> str:
    url  = base_url.rstrip("/") + "/api/generate"
    resp = requests.post(url, json={"model": model, "prompt": f"{system}\n\n{user}",
                                    "stream": True}, stream=True, timeout=120, **request_kwargs)
    resp.raise_for_status()
    parts = []
    for line in resp.iter_lines():
        if not line:
            continue
        chunk = json.loads(line)
        parts.append(chunk.get("response", ""))
        if chunk.get("done"):
            break
    return "".join(parts)


def _translate(text: str, target_name: str, cfg: configparser.ConfigParser) -> str:
    system = (
        f"You are a professional translator. Translate the user's text into "
        f"{target_name}. Output ONLY the translation — no notes, no quotes, no "
        f"romanization. Preserve numbers and proper nouns. If the text is already "
        f"in {target_name}, return it unchanged."
    )
    req = _network_kwargs(cfg)
    mode = cfg.get("summary", "mode", fallback="openai").lower()
    if mode == "ollama":
        return _call_ollama(
            system, text,
            cfg.get("summary", "ollama_model", fallback="qwen2.5:7b"),
            cfg.get("summary", "ollama_url",   fallback="http://localhost:11434"),
            req,
        ).strip()
    return _call_openai(
        system, text,
        cfg.get("summary", "api_base", fallback="https://api.groq.com/openai/v1"),
        cfg.get("summary", "api_key",  fallback=""),
        cfg.get("summary", "model",    fallback="llama-3.3-70b-versatile"),
        req,
    ).strip()


# ── helpers ────────────────────────────────────────────────────────────────────

def _read_target() -> str | None:
    """Current target lang code, or None when translation is OFF."""
    try:
        with open(TRANSLATE_TARGET, encoding="utf-8") as f:
            code = f.read().strip().lower()
        return code if code in _LANG_NAME else "zh"
    except FileNotFoundError:
        return None


def _raw_transcript_path() -> str | None:
    try:
        with open(LAST_TRANSCRIPT, encoding="utf-8") as f:
            p = f.read().strip()
        return p if p and os.path.exists(p) else None
    except FileNotFoundError:
        return None


def _derive_output_path(raw_path: str, translated_dir: str) -> str:
    name = os.path.basename(raw_path)
    if name.startswith("transcript_") and name.endswith(".txt"):
        ts = name[len("transcript_"):-len(".txt")]
    else:
        ts = os.path.splitext(name)[0]
    return os.path.join(translated_dir, f"translated_{ts}.txt")


def _complete_lines(raw_path: str) -> list[str]:
    """All newline-terminated lines (drops any trailing partial line still being
    written by the pipeline)."""
    try:
        with open(raw_path, encoding="utf-8-sig") as f:
            data = f.read()
    except OSError:
        return []
    if "\n" not in data:
        return []
    return data.split("\n")[:-1]


def main() -> int:
    target = _read_target()
    if target is None:
        _log("no .translate_target — exiting")
        return 0
    target_name = _LANG_NAME[target]

    cfg = configparser.ConfigParser()
    cfg.read(CFG_FILE, encoding="utf-8")
    translated_dir = os.path.expanduser(
        cfg.get("translate", "translated_dir", fallback="~/Documents/Sound2Text/translated")
    )
    os.makedirs(translated_dir, exist_ok=True)

    _log(f"started: target={target} ({target_name})")

    # Wait for the live transcript pointer to appear (translation may be toggled
    # on before any transcript exists yet).
    raw_path = None
    while raw_path is None:
        if _read_target() != target:        # turned off or lang changed
            _log("target removed/changed before transcript appeared — exiting")
            return 0
        raw_path = _raw_transcript_path()
        if raw_path is None:
            time.sleep(POLL_INTERVAL)

    out_path = _derive_output_path(raw_path, translated_dir)
    # Fresh output each run (presenter restarts us on a language change).
    out = open(out_path, "w", encoding="utf-8")
    with open(LAST_TRANSLATED, "w", encoding="utf-8") as f:
        f.write(out_path)
    _log(f"raw={raw_path} -> out={out_path}")

    done = 0
    try:
        while True:
            cur = _read_target()
            if cur is None or cur != target:
                _log("target removed/changed — exiting")
                break
            # The live transcript file path can rotate between sessions.
            new_raw = _raw_transcript_path()
            if new_raw and new_raw != raw_path:
                _log("transcript file rotated — exiting (presenter will restart)")
                break

            lines = _complete_lines(raw_path)
            if len(lines) > done:
                for i in range(done, len(lines)):
                    line = lines[i]
                    m = _TS_RE.match(line)
                    prefix, body = (m.group(1) or "", m.group(2)) if m else ("", line)
                    if body.strip():
                        try:
                            translated = _translate(body, target_name, cfg)
                        except Exception as e:
                            _log(f"translate failed (line {i}): {e} — keeping original")
                            translated = body
                        out.write(f"{prefix}{translated}\n")
                    else:
                        out.write(f"{line}\n")
                    out.flush()
                done = len(lines)
            time.sleep(POLL_INTERVAL)
    finally:
        try:
            out.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

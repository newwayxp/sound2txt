"""
会议纪要生成パイプライン:
  Step 1. 纠错: ASR 誤認識・句読点・改行を修正 → corrected_dir に保存
  Step 2. 整理: 纠错済みテキストから会议纪要を生成 → summary_dir に保存

出力言語は転写テキストの言語に合わせ、翻訳しない。
"""
import os
import sys
import json
import configparser
import requests
import urllib3
from datetime import datetime

STATE_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_transcript")
LANG_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_language")
CORRECTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_corrected")
_BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_VOCAB_DEFAULT = os.path.join(_BASE_DIR, "vocabulary.txt")

from glossary import resolve_glossary_file, load_glossary, apply_glossary, glossary_prompt_section


def _network_kwargs(cfg: configparser.ConfigParser) -> dict:
    kwargs = {"verify": cfg.getboolean("network", "ssl_verify", fallback=True)}

    proxies = {}
    https_proxy = cfg.get("network", "https_proxy", fallback="")
    http_proxy = cfg.get("network", "http_proxy", fallback="")
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


def _resolve_vocab_file(cfg: configparser.ConfigParser) -> str:
    """config.ini の vocab_file パスを取得し、初回は program dir からコピーする。"""
    path = cfg.get("paths", "vocab_file", fallback="").strip()
    if not path:
        return _VOCAB_DEFAULT
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) and os.path.exists(_VOCAB_DEFAULT):
        import shutil
        shutil.copy2(_VOCAB_DEFAULT, path)
        print(f"[Summarizer] vocabulary.txt を {path} にコピーしました")
    return path


def _load_vocabulary(vocab_file: str = "") -> list[str]:
    """vocabulary.txt から有効な用語を読み込む（transcriber と共有）。"""
    path = vocab_file or _VOCAB_DEFAULT
    if not os.path.exists(path):
        return []
    terms = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            term = line.strip()
            if term and not term.startswith("#"):
                terms.append(term)
    return terms

# ── 纠错プロンプト ────────────────────────────────────────────────────────────

CORRECT_SYSTEM = """\
You are a professional ASR (Automatic Speech Recognition) correction specialist
skilled in Chinese, Japanese, and English mixed conversation.
You are familiar with common Whisper mis-recognition patterns such as:
  - Homophone confusion (e.g. 在/再, 他/她, 的/地/得 in Chinese; は/わ in Japanese)
  - Proper noun mis-recognition (names, places, products)
  - Cross-language confusion in code-switched speech
  - Missing punctuation (ASR outputs no punctuation by default)
  - Hallucinated lines inserted by Whisper in silent/noisy segments

CRITICAL RULE for Chinese text:
  If the input uses Simplified Chinese characters (简体中文), your output MUST also use
  Simplified Chinese characters. NEVER convert Simplified Chinese to Traditional Chinese.
  This rule overrides all other considerations.
"""

CORRECT_PROMPT = """\
Please correct the ASR transcript below in two steps.

[Step 1 — Read and understand]
Read the entire text first to understand: the conversation context, main topics,
speaker relationships, and any proper nouns or domain-specific terms.

[Step 2 — Correct paragraph by paragraph]
With full context in mind:
1. Fix homophone errors and mis-recognized words (only when you are confident)
2. Add appropriate punctuation (periods, commas, question marks, exclamation marks)
3. Insert blank lines at topic changes or speaker turns
4. Preserve colloquial expressions, filler words, and meaningful repetitions
5. Keep the original language(s) unchanged — do NOT translate any part
6. Keep original timestamps (e.g. [22:27:15])
7. If the input is in Simplified Chinese (简体字), output MUST be in Simplified Chinese.
   NEVER convert 简体字 → 繁体字. (e.g. keep 变/换/语, never write 變/換/語)
8. REMOVE any Whisper hallucination lines — these are lines that are completely
   unrelated to the actual conversation, typically promotional or repetitive phrases
   that Whisper inserts during silent/noisy segments. Common patterns to delete:
   - Chinese: 请不吝点赞、订阅、转发、打赏支持、感谢观看、欢迎关注 etc.
   - Japanese: ご視聴ありがとうございました、チャンネル登録 etc.
   - English: "Thank you for watching", "Please subscribe" etc.

[Output]
Output only the corrected text, no explanations or annotations.

--- Original transcript ---
{transcript}
"""

# ── 纪要テンプレート（言語別） ───────────────────────────────────────────────

_SUMMARY_TEMPLATES = {
    "zh": {
        "system": "你是一名专业的会议纪要撰写专家，擅长将口语化的会议记录整理成简洁清晰的书面纪要。",
        "header": "# 会议纪要",
        "date_label": "**会议时间：**",
        "sections": ["## 主要讨论内容", "## 重要决议与结论", "## 待跟进事项"],
        "hints":    ["（列出会议中讨论的主要话题）", "（决定事项或重要结论，如无则写「无」）", "（需要后续跟进的行动项，如无则写「无」）"],
    },
    "ja": {
        "system": "あなたはプロの議事録作成専門家です。口語的な会議の文字起こしを、簡潔でわかりやすい書面の議事録にまとめる専門家です。",
        "header": "# 議事録",
        "date_label": "**日時：**",
        "sections": ["## 主な議題", "## 決定事項・結論", "## フォローアップ事項"],
        "hints":    ["（議論された主なトピックを箇条書き）", "（決定事項・重要な結論。なければ「なし」）", "（フォローアップが必要な行動項目。なければ「なし」）"],
    },
    "en": {
        "system": "You are a professional meeting minutes writer, skilled at turning informal spoken transcripts into clear, concise written records.",
        "header": "# Meeting Minutes",
        "date_label": "**Date:**",
        "sections": ["## Main Discussion Topics", "## Key Decisions and Conclusions", "## Action Items"],
        "hints":    ["(List the main topics discussed)", "(Key decisions or conclusions. Write 'None' if none.)", "(Follow-up action items. Write 'None' if none.)"],
    },
}

def _summary_prompts(language: str, date: str, transcript: str):
    """言語に合った system + user プロンプトを返す。"""
    tmpl = _SUMMARY_TEMPLATES.get(language, _SUMMARY_TEMPLATES["en"])

    system = tmpl["system"]

    sections = "\n\n".join(
        f"{sec}\n{hint}" for sec, hint in zip(tmpl["sections"], tmpl["hints"])
    )

    user = f"""\
Generate meeting minutes using the format below.
Output language MUST be the same as the transcript — do NOT translate.

{tmpl['header']}

{tmpl['date_label']} {date}

{sections}

--- Corrected transcript ---
{transcript}
"""
    return system, user


# ── バックエンド呼び出し ─────────────────────────────────────────────────────

def _call_openai(
    system: str,
    user: str,
    api_base: str,
    api_key: str,
    model: str,
    request_kwargs: dict,
) -> str:
    url      = api_base.rstrip("/") + "/chat/completions"
    headers  = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    payload  = {"model": model, "messages": messages, "stream": True}

    # Adaptive timeout: longer for long user prompts (estimate 1 token ≈ 4 chars)
    estimated_tokens = len(user) // 4 + 500
    timeout_sec = min(600, max(120, estimated_tokens // 100))  # 1s per 100 tokens, capped at 10min
    print(f"[Summarizer] API call: {estimated_tokens} tokens, timeout={timeout_sec}s")

    resp = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=timeout_sec,
        **request_kwargs,
    )
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


def _call_ollama(system: str, user: str, model: str, base_url: str = "http://localhost:11434") -> str:
    prompt = f"{system}\n\n{user}"
    url    = base_url.rstrip("/") + "/api/generate"
    resp   = requests.post(
        url,
        json={"model": model, "prompt": prompt, "stream": True},
        stream=True, timeout=300,
    )
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


def _call(system: str, user: str, cfg: configparser.ConfigParser) -> str:
    mode = cfg.get("summary", "mode", fallback="ollama").lower()
    if mode == "ollama":
        return _call_ollama(
            system, user,
            cfg.get("summary", "ollama_model", fallback="qwen2.5:7b"),
            cfg.get("summary", "ollama_url",   fallback="http://localhost:11434"),
        )
    return _call_openai(
        system, user,
        cfg.get("summary", "api_base",  fallback="https://api.groq.com/openai/v1"),
        cfg.get("summary", "api_key",   fallback=""),
        cfg.get("summary", "model",     fallback="llama-3.3-70b-versatile"),
        _network_kwargs(cfg),
    )


# ── Step 1: 纠错 ─────────────────────────────────────────────────────────────

def correct_transcript(raw: str, corrected_dir: str, ts: str,
                        cfg: configparser.ConfigParser, language: str = "") -> str:
    import time
    t_start = time.time()
    raw_len = len(raw)
    raw_lines = len(raw.splitlines())
    print(f"[Summarizer] Step1: correcting transcript ({raw_len} chars, {raw_lines} lines)...")

    # Build a language-specific ABSOLUTE instruction prepended in the target language
    # so local models (Ollama/qwen) respect it even when the rest of the prompt is English.
    # Language guard — written in Chinese AND target language so qwen (a Chinese-centric
    # model) reliably respects it. Also added to the END of the user prompt for extra weight.
    _lang_guards = {
        "zh": (
            "【绝对要求】输出语言：简体中文。\n"
            "RULE: Output ONLY in Simplified Chinese. NEVER translate to other languages."
        ),
        "ja": (
            "【絶対禁止】日本語のテキストを中国語や英語に翻訳してはなりません。\n"
            "【絶対条件】出力は必ず日本語のみ。中国語への翻訳は厳禁。\n"
            "RULE: Output ONLY in Japanese. Do NOT translate to Chinese or any other language."
        ),
        "en": (
            "ABSOLUTE RULE: Output ONLY in English. Do NOT translate to Chinese or Japanese.\n"
            "【禁止】中国語・日本語への翻訳禁止。英語のみで出力すること。"
        ),
    }
    _lang_guard = _lang_guards.get(language, "")

    system = ((_lang_guard + "\n\n") if _lang_guard else "") + CORRECT_SYSTEM

    vocab_file = _resolve_vocab_file(cfg)
    vocab = _load_vocabulary(vocab_file)
    vocab_section = ""
    if vocab:
        print(f"[Summarizer] vocabulary: {len(vocab)} terms loaded ({vocab_file})")
        vocab_section = (
            "\n\nKnown proper nouns / technical terms in this recording"
            " (treat these as correct spellings, do not alter them):\n"
            + ", ".join(vocab)
        )

    # Deterministic 誤→正 glossary: inject as explicit rules AND apply after the LLM
    glossary = load_glossary(resolve_glossary_file(cfg))
    glossary_section = glossary_prompt_section(glossary)

    prompt = CORRECT_PROMPT.format(transcript=raw) + vocab_section + glossary_section
    # Repeat the language guard at the END of the user prompt — models weight recent instructions highly
    if _lang_guard:
        prompt += f"\n\n[REMINDER] {_lang_guard}"

    t_api_start = time.time()
    print(f"[Summarizer] calling correction API...")
    corrected = _call(system, prompt, cfg).strip()
    api_elapsed = time.time() - t_api_start
    print(f"[Summarizer] API response: {api_elapsed:.1f}s")

    # Enforce the glossary deterministically (covers any terms the LLM missed,
    # and still works when the API is unavailable).
    corrected = apply_glossary(corrected, glossary)

    os.makedirs(corrected_dir, exist_ok=True)
    path = os.path.join(corrected_dir, f"corrected_{ts}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(corrected)
    # Record the exact path so the summary step can use it without guessing
    with open(CORRECTED_FILE, "w", encoding="utf-8") as f:
        f.write(path)

    elapsed = time.time() - t_start
    print(f"[Summarizer] correction done -> {path} ({elapsed:.1f}s total)")
    return corrected


# ── Step 2: 纪要生成 ──────────────────────────────────────────────────────────

def make_summary(corrected: str, language: str, summary_dir: str, ts: str, cfg: configparser.ConfigParser) -> None:
    import time
    t_start = time.time()
    corrected_len = len(corrected)
    print(f"[Summarizer] Step2: generating summary (lang={language}, {corrected_len} chars)...")
    date   = datetime.now().strftime("%Y-%m-%d %H:%M")
    system, user = _summary_prompts(language, date, corrected)

    t_api_start = time.time()
    print(f"[Summarizer] calling summary API...")
    summary = _call(system, user, cfg).strip()
    api_elapsed = time.time() - t_api_start
    print(f"[Summarizer] API response: {api_elapsed:.1f}s")

    os.makedirs(summary_dir, exist_ok=True)
    path = os.path.join(summary_dir, f"summary_{ts}.md")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(summary)

    elapsed = time.time() - t_start
    print(f"[Summarizer] summary done -> {path} ({elapsed:.1f}s total)")


# ── メイン ───────────────────────────────────────────────────────────────────

def run(transcript_path: str, cfg: configparser.ConfigParser, language: str = "") -> bool:
    corrected_dir = os.path.expanduser(cfg.get("summary", "corrected_dir", fallback=r"C:\code\data\corrected"))
    summary_dir   = os.path.expanduser(cfg.get("summary", "summary_dir",   fallback=r"C:\code\data\memo"))
    mode          = cfg.get("summary", "mode",          fallback="ollama").lower()
    api_key       = cfg.get("summary", "api_key",       fallback="")

    print(f"[Summarizer] mode={mode}  lang={language or 'auto'}  transcript={transcript_path}")

    if mode == "openai" and (not api_key or api_key == "your_api_key_here"):
        print("[Summarizer] api_key not set in config.ini")
        print("  Groq     : https://console.groq.com")
        print("  DeepSeek : https://platform.deepseek.com")
        return False

    if mode == "ollama":
        ollama_url = cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
        try:
            requests.get(ollama_url, timeout=2)
        except Exception:
            print(f"[Summarizer] Ollama not reachable at {ollama_url}. Start with: ollama serve")
            return False

    if not os.path.exists(transcript_path):
        print(f"[Summarizer] file not found: {transcript_path}")
        return False

    with open(transcript_path, "r", encoding="utf-8-sig") as f:
        raw = f.read().strip()

    if not raw or raw.count("\n") < 2:
        print("[Summarizer] transcript too short, skipping")
        return False

    # 言語が未指定なら OS 言語 / 録音設定から推定
    if not language:
        import configparser as _cp2
        _c2 = _cp2.ConfigParser()
        _c2.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"),
                 encoding="utf-8")
        language = _c2.get("recording", "language", fallback="auto").strip().lower()
        if language not in {"zh", "ja", "en"}:
            # auto → OS 言語で判定
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International")
                loc = winreg.QueryValueEx(key, "LocaleName")[0].lower()
                language = "zh" if loc.startswith("zh") else "ja" if loc.startswith("ja") else "en"
            except Exception:
                language = "ja"  # 日本語環境が多いためデフォルト
        print(f"[Summarizer] 言語推定: {language}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        corrected = correct_transcript(raw, corrected_dir, ts, cfg)
        make_summary(corrected, language, summary_dir, ts, cfg)
    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] [Summarizer] API error: {e}")
        return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] [Summarizer] network error: {e}")
        return False
    except Exception as e:
        print(f"[ERROR] [Summarizer] error: {e}")
        return False

    return True


def run_step(step: str, transcript_path: str, cfg: configparser.ConfigParser,
             language: str = "") -> bool:
    """
    Run a single pipeline step.
    step = "correct"  → correction only (Step 1)
    step = "summary"  → meeting minutes only, uses latest corrected file (Step 2)
    step = "all"      → both steps (default, backward compatible)
    """
    corrected_dir = os.path.expanduser(cfg.get("summary", "corrected_dir", fallback=r"C:\code\data\corrected"))
    summary_dir   = os.path.expanduser(cfg.get("summary", "summary_dir",   fallback=r"C:\code\data\memo"))
    mode          = cfg.get("summary", "mode",          fallback="ollama").lower()
    api_key       = cfg.get("summary", "api_key",       fallback="")

    print(f"[Summarizer] step={step} mode={mode} lang={language or 'auto'} transcript={transcript_path}")

    if mode == "openai" and (not api_key or api_key == "your_api_key_here"):
        print("[Summarizer] api_key not set in config.ini"); return False

    if mode == "ollama":
        ollama_url = cfg.get("summary", "ollama_url", fallback="http://localhost:11434")
        try:
            requests.get(ollama_url, timeout=2)
        except Exception:
            print(f"[Summarizer] Ollama not reachable at {ollama_url}. Start with: ollama serve")
            return False

    if not os.path.exists(transcript_path):
        print(f"[Summarizer] file not found: {transcript_path}"); return False

    with open(transcript_path, "r", encoding="utf-8-sig") as f:
        raw = f.read().strip()

    if not raw or raw.count("\n") < 2:
        print("[Summarizer] transcript too short, skipping"); return False

    if not language:
        language = "en"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        if step in ("correct", "all"):
            corrected = correct_transcript(raw, corrected_dir, ts, cfg, language)
        else:
            # For summary-only step, load the corrected file recorded by the correct step
            corrected_path = ""
            if os.path.exists(CORRECTED_FILE):
                with open(CORRECTED_FILE, "r", encoding="utf-8") as f:
                    corrected_path = f.read().strip()
            if corrected_path and os.path.exists(corrected_path):
                with open(corrected_path, "r", encoding="utf-8-sig") as f:
                    corrected = f.read()
                print(f"[Summarizer] using corrected file: {corrected_path}")
            else:
                # Fallback: search latest corrected file (should not normally reach here)
                import glob as _glob
                files = sorted(_glob.glob(os.path.join(corrected_dir, "corrected_*.txt")),
                               key=os.path.getmtime)
                corrected = open(files[-1], encoding="utf-8-sig").read() if files else raw
                print(f"[Summarizer] warning: .last_corrected missing, using latest file")

        if step in ("summary", "all"):
            make_summary(corrected, language, summary_dir, ts, cfg)

    except requests.exceptions.HTTPError as e:
        print(f"[ERROR] [Summarizer] API error: {e}"); return False
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] [Summarizer] network error: {e}"); return False
    except Exception as e:
        print(f"[ERROR] [Summarizer] error: {e}"); return False

    return True


def main():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini"), encoding="utf-8")

    # Parse --step argument
    args  = sys.argv[1:]
    step  = "all"
    if "--step" in args:
        idx  = args.index("--step")
        step = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if args:
        transcript_path = args[0]
        language = args[1] if len(args) >= 2 else ""
        # Always try LANG_FILE if language not explicitly given
        if not language and os.path.exists(LANG_FILE):
            with open(LANG_FILE, "r", encoding="utf-8") as f:
                language = f.read().strip()
    elif os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            transcript_path = f.read().strip()
        language = ""
        if os.path.exists(LANG_FILE):
            with open(LANG_FILE, "r", encoding="utf-8") as f:
                language = f.read().strip()
        print(f"[Summarizer] reading from state: {transcript_path}  lang={language}")
    else:
        print("[Summarizer] usage: python summarizer.py [--step correct|summary|all] <transcript.txt> [lang]")
        sys.exit(1)

    sys.exit(0 if run_step(step, transcript_path, cfg, language) else 1)


if __name__ == "__main__":
    main()

"""
会议纪要生成パイプライン:
  Step 1. 纠错: ASR 誤認識・句読点・改行を修正 → corrected_dir に保存
  Step 2. 整理: 纠错済みテキストから会议纪要を生成 → summary_dir に保存

出力言語は転写テキストの言語に合わせ、翻訳しない。
"""
import os
import sys
import json
import time
import configparser
import requests
import urllib3
from datetime import datetime

# Bounded retry for transient API errors (HTTP 429 / 5xx). Kept deliberately
# SHORT: if the limit/outage does not clear in a few seconds it usually won't
# clear soon, so we give up rather than block the user — callers then fall back
# to the pre-correction text and never write partial/garbage output downstream.
_RETRY_STATUS   = {429, 500, 502, 503, 529}
_RETRY_ATTEMPTS = 3      # total attempts (1 initial + 2 retries)
_RETRY_MAX_WAIT = 8.0    # cap per-wait seconds; ignore longer Retry-After hints

STATE_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_transcript")
LANG_FILE      = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_language")
CORRECTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_corrected")
FINAL_CORRECTED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_final_corrected")
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

# ── 言語ガード（翻訳禁止）─────────────────────────────────────────────────────
# Chinese-centric local models (qwen) tend to translate JA/EN transcripts into
# Chinese despite English instructions. These guards are written in Chinese AND
# the target language and are placed at BOTH the start of the system prompt and
# the END of the user prompt (models weight the most recent instruction highly).
_LANG_GUARDS = {
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


def _lang_guard(language: str) -> str:
    return _LANG_GUARDS.get(language, "")


# ── 纪要テンプレート（言語別） ───────────────────────────────────────────────

_SUMMARY_TEMPLATES = {
    "zh": {
        "system": "你是一名专业的会议纪要撰写专家，擅长将口语化的会议记录整理成结构清晰、专业美观的书面纪要。",
        "body": """\
# 会议纪要

**日期：** {date}

**主题：** （用一句话概括本次会议主题）

**摘要：** （用 1–2 句话概括会议的核心内容与主要结论）

## 主要议题
（按话题分条，每条格式为「**话题**：要点说明」；要点较多时用子项展开）

## 决定事项
（编号列出明确的决定或结论；如无则写「无」）

## 行动项
（逐条列出，格式为「事项 — 负责人 — 期限」；如无则写「无」）

## 下一步
（后续安排或下次会议计划；如无则写「无」）""",
    },
    "ja": {
        "system": "あなたはプロの議事録作成専門家です。口語的な会議の文字起こしを、構成が明確で見やすいプロフェッショナルな議事録にまとめます。",
        "body": """\
# 議事録

**日時：** {date}

**テーマ：** （会議の主題を一言で）

**要約：** （会議の核心と主な結論を1〜2文で）

## 主な議題
（トピックごとに「**トピック**：要点」の形式で箇条書き。要点が多い場合は小項目で展開）

## 決定事項
（明確な決定・結論を番号付きで。なければ「なし」）

## アクションアイテム
（1行ずつ「項目 — 担当者 — 期限」の形式で記入。なければ「なし」）

## 次のステップ
（今後の予定・次回会議。なければ「なし」）""",
    },
    "en": {
        "system": "You are a professional meeting minutes writer, skilled at turning informal spoken transcripts into clear, well-structured, professional written records.",
        "body": """\
# Meeting Minutes

**Date:** {date}

**Topic:** (Summarize the meeting topic in one line)

**Summary:** (1–2 sentences capturing the core content and key conclusions)

## Main Discussion Topics
(Bullet each topic as "**Topic**: key points"; expand with sub-items when needed)

## Key Decisions
(Numbered list of explicit decisions or conclusions. Write "None" if none.)

## Action Items
(One per line as "Task — Owner — Due". Write "None" if none.)

## Next Steps
(Follow-up plans or next meeting. Write "None" if none.)""",
    },
}

def _summary_prompts(language: str, date: str, transcript: str):
    """言語に合った system + user プロンプトを返す。"""
    tmpl = _SUMMARY_TEMPLATES.get(language, _SUMMARY_TEMPLATES["en"])
    body = tmpl["body"].replace("{date}", date)

    user = f"""\
Fill in the meeting-minutes template below from the transcript.

Rules:
- Output language MUST be the same as the transcript — do NOT translate.
- Keep the exact Markdown structure: the headings and the bold field labels.
- Replace each parenthesized instruction （…） with real content, and remove the
  guidance text and the parentheses themselves.
- Use plain text and simple bullet/numbered lists only — no tables.
- Be concise and factual; never invent content not supported by the transcript.
- Output only the finished Markdown, with no extra commentary or code fences.

--- Template ---
{body}

--- Corrected transcript ---
{transcript}
"""
    return tmpl["system"], user


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

    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
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
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in _RETRY_STATUS and attempt < _RETRY_ATTEMPTS:
                wait = _retry_wait(e.response, attempt)
                print(f"[Summarizer] API {status} (attempt {attempt}/{_RETRY_ATTEMPTS}) "
                      f"— retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise  # non-retryable, or out of attempts → caller falls back to pre-correction text
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < _RETRY_ATTEMPTS:
                wait = min(_RETRY_MAX_WAIT, 2.0 * attempt)
                print(f"[Summarizer] API network error ({type(e).__name__}) "
                      f"(attempt {attempt}/{_RETRY_ATTEMPTS}) — retrying in {wait:.0f}s")
                time.sleep(wait)
                continue
            raise


def _retry_wait(resp, attempt: int) -> float:
    """Seconds to wait before the next retry: honor a short Retry-After header,
    else exponential backoff, both capped at _RETRY_MAX_WAIT so a long limit
    window never blocks the user."""
    try:
        ra = resp.headers.get("Retry-After") if resp is not None else None
        if ra is not None:
            return min(_RETRY_MAX_WAIT, float(ra))
    except (TypeError, ValueError):
        pass
    return min(_RETRY_MAX_WAIT, 2.0 ** attempt)


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

def _json_from_text(text: str) -> dict:
    """Extract a small JSON object from an LLM response."""
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    return {}


def _infer_industry_context(text: str, language: str,
                            cfg: configparser.ConfigParser) -> tuple[str, list[str]]:
    sample = text[:8000]
    system = (
        "You classify meeting transcripts for terminology correction. "
        "Return compact JSON only."
    )
    user = f"""\
Analyze the transcript and infer its industry/domain and search keywords for terminology lookup.
Return JSON only, with this schema:
{{"domain":"short industry/domain name","keywords":["keyword1","keyword2"]}}

Rules:
- Use the same language as the transcript where possible.
- Keep 3 to 8 specific keywords.
- Prefer proper nouns, technical terms, products, laws, markets, places, and organizations.

language={language or "auto"}

--- Transcript sample ---
{sample}
"""
    try:
        data = _json_from_text(_call(system, user, cfg).strip())
    except Exception as e:
        print(f"[Summarizer] online refine: domain inference failed: {e}")
        return "", []

    domain = str(data.get("domain", "")).strip()
    raw_keywords = data.get("keywords", [])
    keywords = []
    if isinstance(raw_keywords, list):
        for k in raw_keywords:
            s = str(k).strip()
            if s and s not in keywords:
                keywords.append(s)
            if len(keywords) >= 8:
                break
    print(f"[Summarizer] online refine: domain={domain or '-'} keywords={keywords}")
    return domain, keywords


def _wiki_lang(language: str) -> str:
    return language if language in {"ja", "zh", "en"} else "en"


def _download_industry_terms(keywords: list[str], language: str,
                             cache_dir: str, cfg: configparser.ConfigParser) -> list[str]:
    """Download terminology candidates from Wikipedia OpenSearch and cache them."""
    os.makedirs(cache_dir, exist_ok=True)
    terms: list[str] = []
    wiki = _wiki_lang(language)
    limit = cfg.getint("summary", "online_refine_terms", fallback=80)
    request_kwargs = _network_kwargs(cfg)

    for keyword in keywords:
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in keyword)[:80] or "keyword"
        cache_path = os.path.join(cache_dir, f"{wiki}_{safe_name}.json")
        data = None
        if os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = None
        if data is None:
            url = f"https://{wiki}.wikipedia.org/w/api.php"
            params = {
                "action": "opensearch",
                "namespace": "0",
                "search": keyword,
                "limit": "10",
                "format": "json",
            }
            try:
                headers = {"User-Agent": "Sound2Text/1.0 (industry term refinement)"}
                resp = requests.get(url, params=params, headers=headers,
                                    timeout=15, **request_kwargs)
                resp.raise_for_status()
                data = resp.json()
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[Summarizer] online refine: term download failed for {keyword}: {e}")
                continue

        titles = data[1] if isinstance(data, list) and len(data) > 1 else []
        for title in titles:
            term = str(title).strip()
            if term and term not in terms:
                terms.append(term)
            if len(terms) >= limit:
                break
        if len(terms) >= limit:
            break

    print(f"[Summarizer] online refine: downloaded/cached {len(terms)} term(s)")
    return terms


def online_refine_transcript(corrected: str, corrected_dir: str, ts: str,
                             cfg: configparser.ConfigParser, language: str = "",
                             source_path: str = "") -> tuple[str, str]:
    """Post-session final correction using inferred industry terms.

    The real-time corrected_*.txt remains untouched; a successful pass writes a
    separate final_corrected_*.txt and stores its path in .last_final_corrected.
    """
    if not cfg.getboolean("summary", "enable_online_refine", fallback=False):
        print("[Summarizer] online refine disabled")
        return corrected, source_path
    if not corrected.strip():
        return corrected, source_path

    import time
    t_start = time.time()
    print("[Summarizer] online refine: started")

    domain, keywords = _infer_industry_context(corrected, language, cfg)
    if not keywords and domain:
        keywords = [domain]
    if not keywords:
        print("[Summarizer] online refine: no keywords, skipped")
        return corrected, source_path

    cache_dir = os.path.expanduser(
        cfg.get("summary", "term_cache_dir",
                fallback=os.path.join(corrected_dir, "term_cache")))
    terms = _download_industry_terms(keywords, language, cache_dir, cfg)
    if not terms:
        print("[Summarizer] online refine: no downloaded terms, skipped")
        return corrected, source_path

    term_text = "\n".join(f"- {t}" for t in terms)
    system = (
        "You are a senior ASR transcript editor. Use industry terminology "
        "conservatively. Output only the final corrected transcript."
    )
    user = f"""\
Refine the corrected transcript using the downloaded industry/domain terminology.

Rules:
1. Preserve all timestamps exactly.
2. Preserve the transcript language; do not translate.
3. Only fix terms when context strongly supports the change.
4. Do not rewrite style unnecessarily.
5. Do not remove content except obvious ASR hallucinations.
6. Output only the final corrected transcript.

Inferred domain: {domain or "-"}

Downloaded terminology candidates:
{term_text}

--- Corrected transcript ---
{corrected}
"""
    try:
        final_text = _call(system, user, cfg).strip()
    except Exception as e:
        print(f"[Summarizer] online refine: final correction failed: {e}")
        return corrected, source_path

    if len(final_text) < max(20, len(corrected) * 0.4):
        print("[Summarizer] online refine: output too short, keeping corrected file")
        return corrected, source_path

    final_dir = os.path.expanduser(
        cfg.get("summary", "final_corrected_dir", fallback=corrected_dir))
    os.makedirs(final_dir, exist_ok=True)
    path = os.path.join(final_dir, f"final_corrected_{ts}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(final_text)
    with open(FINAL_CORRECTED_FILE, "w", encoding="utf-8") as f:
        f.write(path)

    elapsed = time.time() - t_start
    print(f"[Summarizer] online refine done -> {path} ({elapsed:.1f}s total)")
    return final_text, path


def _combined_correct_prompts(language: str, raw: str, terms: list[str]):
    """system + user for a SINGLE pass that both corrects ASR errors and applies
    the downloaded industry terminology. Mirrors the per-step correction rules."""
    _guard = _lang_guard(language)
    system = ((_guard + "\n\n") if _guard else "") + CORRECT_SYSTEM
    term_block = ""
    if terms:
        term_list = "\n".join(f"  - {t}" for t in terms)
        term_block = ("\n9. Prefer this downloaded domain/industry terminology when the "
                      "audio plausibly refers to it (use it conservatively, only when the "
                      "context clearly supports the term):\n" + term_list + "\n")
    user = f"""\
Correct the ASR transcript below and apply domain terminology in a single pass.

[Step 1 — Read and understand]
Read the whole text first: context, topics, proper nouns, and domain terms.

[Step 2 — Correct]
1. Fix homophone errors and mis-recognized words (only when you are confident)
2. Add appropriate punctuation
3. Insert blank lines at topic changes or speaker turns
4. Preserve colloquial expressions, filler words, and meaningful repetitions
5. Keep the original language(s) unchanged — do NOT translate any part
6. Keep original timestamps (e.g. [22:27:15])
7. If the input is Simplified Chinese (简体字), output MUST stay Simplified Chinese
8. REMOVE Whisper hallucination lines (unrelated promotional/repetitive phrases){term_block}
[Output]
Output only the corrected text, no explanations or annotations.

--- Original transcript ---
{raw}
"""
    if _guard:
        user += f"\n\n[REMINDER] {_guard}"
    return system, user


def online_full_correct(raw: str, corrected_dir: str, ts: str,
                        cfg: configparser.ConfigParser,
                        language: str = "") -> tuple[str, str]:
    """One-pass full correction + industry-term refinement on the RAW transcript.

    Used when per-segment real-time correction is skipped (online refine enabled):
    infer the domain, download terminology, then run a SINGLE LLM call that both
    corrects ASR errors and applies the domain terms, writing final_corrected_*.
    Falls back to a plain full correction (then glossary-only raw) on failure so a
    session always yields usable text + minutes."""
    if not raw.strip():
        return raw, ""

    import time
    t_start = time.time()
    print("[Summarizer] online full-correct: started")

    # 1) domain + terms (best effort; correction still runs without them)
    terms: list[str] = []
    try:
        domain, keywords = _infer_industry_context(raw, language, cfg)
        if not keywords and domain:
            keywords = [domain]
        if keywords:
            cache_dir = os.path.expanduser(
                cfg.get("summary", "term_cache_dir",
                        fallback=os.path.join(corrected_dir, "term_cache")))
            terms = _download_industry_terms(keywords, language, cache_dir, cfg)
    except Exception as e:
        print(f"[Summarizer] online full-correct: term lookup failed ({e}); correcting without terms")

    # 2) single combined correction + term pass
    system, user = _combined_correct_prompts(language, raw, terms)
    try:
        final_text = _call(system, user, cfg).strip()
    except Exception as e:
        print(f"[Summarizer] online full-correct: combined pass failed ({e}); falling back")
        final_text = ""

    # 3) fallbacks: plain full correction, then raw
    if len(final_text) < max(20, len(raw) * 0.4):
        try:
            final_text = correct_transcript(raw, corrected_dir, ts, cfg, language)
            print("[Summarizer] online full-correct: used plain correction fallback")
        except Exception as e:
            print(f"[Summarizer] online full-correct: plain correction failed ({e}); using raw")
            final_text = raw

    # deterministic glossary always applied (offline, even if LLM was skipped)
    try:
        from glossary import resolve_glossary_file, load_glossary, apply_glossary
        final_text = apply_glossary(final_text, load_glossary(resolve_glossary_file(cfg)))
    except Exception:
        pass

    final_dir = os.path.expanduser(
        cfg.get("summary", "final_corrected_dir", fallback=corrected_dir))
    os.makedirs(final_dir, exist_ok=True)
    path = os.path.join(final_dir, f"final_corrected_{ts}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(final_text)
    with open(FINAL_CORRECTED_FILE, "w", encoding="utf-8") as f:
        f.write(path)

    print(f"[Summarizer] online full-correct done -> {path} "
          f"({time.time()-t_start:.1f}s, {len(terms)} terms)")
    return final_text, path


def correct_transcript(raw: str, corrected_dir: str, ts: str,
                        cfg: configparser.ConfigParser, language: str = "") -> str:
    import time
    t_start = time.time()
    raw_len = len(raw)
    raw_lines = len(raw.splitlines())
    print(f"[Summarizer] Step1: correcting transcript ({raw_len} chars, {raw_lines} lines)...")

    # Language guard (see _LANG_GUARDS): prepended to system AND appended to the
    # user prompt so qwen reliably keeps the original language.
    _guard = _lang_guard(language)

    system = ((_guard + "\n\n") if _guard else "") + CORRECT_SYSTEM

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
    if _guard:
        prompt += f"\n\n[REMINDER] {_guard}"

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
        corrected_path = ""
        if os.path.exists(CORRECTED_FILE):
            with open(CORRECTED_FILE, "r", encoding="utf-8") as f:
                corrected_path = f.read().strip()
        corrected, final_path = online_refine_transcript(
            corrected, corrected_dir, ts, cfg, language, corrected_path)
        if final_path and final_path != corrected_path:
            print(f"[Summarizer] using final corrected file for summary: {final_path}")
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
    step = "online"   → ONE combined full-correction + industry-term pass on the
                        RAW transcript, then minutes. Used when per-segment
                        correction was skipped (online refine enabled).
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
        if step == "online":
            # Combined full-correction + industry-term pass on the RAW transcript,
            # then minutes. Used when per-segment correction was skipped during the
            # session (online refine enabled), so this is the only correction pass.
            final_text, final_path = online_full_correct(raw, corrected_dir, ts, cfg, language)
            if final_path:
                print(f"[Summarizer] using final corrected file for summary: {final_path}")
            make_summary(final_text, language, summary_dir, ts, cfg)
            return True

        if step in ("correct", "all"):
            corrected = correct_transcript(raw, corrected_dir, ts, cfg, language)
            corrected_path = ""
            if os.path.exists(CORRECTED_FILE):
                with open(CORRECTED_FILE, "r", encoding="utf-8") as f:
                    corrected_path = f.read().strip()
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
            corrected, final_path = online_refine_transcript(
                corrected, corrected_dir, ts, cfg, language, corrected_path)
            if final_path and final_path != corrected_path:
                print(f"[Summarizer] using final corrected file for summary: {final_path}")
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
        print("[Summarizer] usage: python summarizer.py [--step correct|summary|all|online] <transcript.txt> [lang]")
        sys.exit(1)

    sys.exit(0 if run_step(step, transcript_path, cfg, language) else 1)


if __name__ == "__main__":
    main()

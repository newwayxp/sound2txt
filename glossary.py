"""Deterministic ASR term-correction glossary (誤 => 正).

Shared by pipeline.py (per-segment, real-time) and summarizer.py (full transcript).

File format — one rule per line:

    誤認識の表記 => 正しい表記

Plain lines (no "=>") and lines starting with "#" are ignored. Deterministic
replacement runs with NO LLM call, so it works offline and even when the
correction API is rate-limited (HTTP 429). Add a line whenever the model keeps
mis-recognizing a fixed term — it takes effect on the next recording.
"""
import os
import configparser

_BASE = os.path.dirname(os.path.abspath(__file__))
GLOSSARY_DEFAULT = os.path.join(_BASE, "glossary.txt")


def resolve_glossary_file(cfg: configparser.ConfigParser) -> str:
    """config.ini の glossary_file パスを返す。未設定ならプログラム同梱版。
    初回はテンプレートをユーザーパスへコピーする（vocabulary.txt と同じ挙動）。"""
    path = cfg.get("paths", "glossary_file", fallback="").strip()
    path = os.path.expanduser(path)
    if not path:
        return GLOSSARY_DEFAULT
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path) and os.path.exists(GLOSSARY_DEFAULT):
            import shutil
            shutil.copy2(GLOSSARY_DEFAULT, path)
    except Exception:
        pass
    return path


def load_glossary(path: str = "") -> list[tuple[str, str]]:
    """Parse "誤 => 正" rules. Longer left-hand sides first so the most specific
    rule wins when patterns overlap."""
    path = path or GLOSSARY_DEFAULT
    if not os.path.exists(path):
        return []
    pairs: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=>" not in s:
                continue
            wrong, right = s.split("=>", 1)
            wrong, right = wrong.strip(), right.strip()
            if wrong and right and wrong != right:
                pairs.append((wrong, right))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def apply_glossary(text: str, pairs: list[tuple[str, str]]) -> str:
    """Deterministically replace every known mis-recognition with its correct form."""
    if not text or not pairs:
        return text
    for wrong, right in pairs:
        if wrong in text:
            text = text.replace(wrong, right)
    return text



def append_glossary_rules(path: str, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Append new ``wrong => right`` rules, skipping duplicates."""
    path = path or GLOSSARY_DEFAULT
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    existing = set(load_glossary(path))
    written: list[tuple[str, str]] = []
    for wrong, right in pairs:
        wrong = str(wrong).strip()
        right = str(right).strip()
        pair = (wrong, right)
        if not wrong or not right or wrong == right or pair in existing:
            continue
        existing.add(pair)
        written.append(pair)
    if not written:
        return []
    needs_newline = os.path.exists(path) and os.path.getsize(path) > 0
    with open(path, "a", encoding="utf-8") as f:
        if needs_newline:
            f.write("\n")
        for wrong, right in written:
            f.write(f"{wrong} => {right}\n")
    return written


def glossary_prompt_section(pairs: list[tuple[str, str]]) -> str:
    """A prompt snippet that asks the LLM to also apply the term corrections."""
    if not pairs:
        return ""
    rules = "\n".join(f"  {w} → {r}" for w, r in pairs)
    return ("\n\nApply these exact term corrections (the left side is a known ASR "
            "mis-recognition; replace it with the right side):\n" + rules)

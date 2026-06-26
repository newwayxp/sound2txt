"""History session discovery, replay timing, and correction persistence."""
from __future__ import annotations

import difflib
import glob
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TS_RE = re.compile(r"(\d{8}_\d{6})")
STAMP_RE = re.compile(r"^\s*\[(\d{2}):(\d{2}):(\d{2})\]\s*(.*)$")
TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+|[\u30A0-\u30FF\u30FC]+|[\u4E00-\u9FFF]|[\u3040-\u309F]|[^\s]", re.UNICODE)
SUMMARY_TOPIC_RE = re.compile(
    r"^\s*(?:[-*]\s*)?\*\*(?:\u4e3b\u9898|\u30c6\u30fc\u30de|Topic)\s*[:\uff1a]\*\*\s*(.+?)\s*$",
    re.IGNORECASE,
)
_SUDACHI_TOKENIZER = None
_SUDACHI_MODE = None
_SUDACHI_LOAD_FAILED = False


@dataclass(frozen=True)
class HistorySession:
    ts: str
    display_time: str
    title: str
    summary_path: str
    corrected_path: str
    audio_path: str
    can_replay: bool
    can_edit: bool


@dataclass(frozen=True)
class TranscriptSegment:
    stamp: str
    start_sec: float
    end_sec: float
    text: str
    line: str


def _cfg_path(cfg, section: str, key: str, fallback: str = "") -> str:
    try:
        value = cfg.get(section, key, fallback=fallback)
    except TypeError:
        value = cfg.get(section, key) if cfg.has_option(section, key) else fallback
    return os.path.abspath(os.path.expanduser(value)) if value else ""


def history_dirs(cfg) -> dict[str, str]:
    base = os.path.join(os.path.expanduser("~"), "Documents", "Sound2Text")
    return {
        "audio": _cfg_path(cfg, "paths", "audio_dir", os.path.join(base, "audio")),
        "transcript": _cfg_path(cfg, "paths", "transcript_dir", os.path.join(base, "transcript")),
        "corrected": _cfg_path(cfg, "summary", "corrected_dir", os.path.join(base, "corrected")),
        "summary": _cfg_path(cfg, "summary", "summary_dir", os.path.join(base, "memo")),
        "final_corrected": _cfg_path(cfg, "summary", "final_corrected_dir", os.path.join(base, "corrected")),
    }


def list_sessions(cfg) -> list[HistorySession]:
    dirs = history_dirs(cfg)
    ts_values = _corrected_session_ts_values(dirs)
    sessions = [_make_session(ts, dirs) for ts in ts_values]
    sessions = [session for session in sessions if session.audio_path and session.corrected_path]
    return sorted(sessions, key=lambda s: s.ts, reverse=True)


def load_session(cfg, ts: str) -> tuple[HistorySession, list[TranscriptSegment], str]:
    dirs = history_dirs(cfg)
    session = _make_session(ts, dirs)
    if not session.corrected_path:
        raise FileNotFoundError(f"corrected transcript not found for {ts}")
    text = Path(session.corrected_path).read_text(encoding="utf-8-sig")
    duration = audio_duration_seconds(session.audio_path)
    return session, parse_transcript_segments(ts, text, duration), text


def parse_transcript_segments(ts: str, text: str, audio_duration: float = 0.0) -> list[TranscriptSegment]:
    session_base = _seconds_from_ts(ts)
    parsed: list[tuple[str, float, str, str]] = []
    for raw_line in text.splitlines():
        match = STAMP_RE.match(raw_line)
        if not match:
            continue
        h, m, s, body = match.groups()
        line_sec = int(h) * 3600 + int(m) * 60 + int(s)
        start = line_sec - session_base
        if start < 0:
            start += 24 * 3600
        parsed.append((f"{h}:{m}:{s}", float(start), body, raw_line))

    segments: list[TranscriptSegment] = []
    for index, (stamp, line_time, body, line) in enumerate(parsed):
        # Live transcript timestamps are segment end times. The text spoken on
        # this line occupies the interval from the previous line's timestamp to
        # this line's timestamp. Treating line_time as the start seeks to the
        # next utterance, which feels one sentence late in replay.
        start = parsed[index - 1][1] if index > 0 else 0.0
        end = line_time
        if end <= start:
            if index + 1 < len(parsed):
                start = line_time
                end = parsed[index + 1][1]
            else:
                end = audio_duration if audio_duration > start else start + 5.0
        if end <= start:
            end = start + 1.0
        segments.append(TranscriptSegment(stamp, start, end, body, line))
    return segments


def save_corrected_text(path: str, new_text: str) -> None:
    if not path:
        raise ValueError("missing corrected transcript path")
    normalized = _normalize_saved_text(new_text)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    fd, tmp = tempfile.mkstemp(prefix=".history_save_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="\n") as f:
            f.write(normalized)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def extract_learning_candidates(old_text: str, new_text: str) -> tuple[list[tuple[str, str]], list[str]]:
    old_lines = _timestamped_lines(old_text)
    new_lines = _timestamped_lines(new_text)
    pairs: list[tuple[str, str]] = []
    terms: list[str] = []
    for old_body, new_body in _paired_bodies_for_learning(old_lines, new_lines):
        if new_body == old_body:
            continue
        for wrong, right in _diff_pairs(old_body, new_body):
            if _valid_pair(wrong, right):
                pairs.append((wrong, right))
                for term in _proper_terms(right):
                    terms.append(term)
    return _dedupe_pairs(pairs), _dedupe_terms(terms)


def audio_duration_seconds(path: str) -> float:
    if not path or not os.path.exists(path):
        return 0.0
    try:
        import wave

        if path.lower().endswith(".wav"):
            with wave.open(path, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                return float(frames) / float(rate) if rate else 0.0
    except Exception:
        pass
    return 0.0


def _make_session(ts: str, dirs: dict[str, str]) -> HistorySession:
    final_corrected_path = _final_corrected_for_session(dirs, ts)
    final_ts = _extract_ts(final_corrected_path) if final_corrected_path else ""
    summary_path = _first_existing(
        os.path.join(dirs["summary"], f"summary_{final_ts}.md") if final_ts else "",
        os.path.join(dirs["summary"], f"summary_{ts}.md"),
    )
    corrected_path = _first_existing(
        final_corrected_path,
        os.path.join(dirs["corrected"], f"corrected_{ts}.txt"),
    )
    audio_path = _find_audio(dirs["audio"], ts)
    title = _title_for_session(summary_path, corrected_path)
    return HistorySession(
        ts=ts,
        display_time=_format_ts(ts),
        title=title or ts,
        summary_path=summary_path,
        corrected_path=corrected_path,
        audio_path=audio_path,
        can_replay=bool(audio_path and corrected_path),
        can_edit=bool(corrected_path),
    )


def _corrected_session_ts_values(dirs: dict[str, str]) -> list[str]:
    corrected_dir = dirs.get("corrected", "")
    if not corrected_dir:
        return []
    ts_values: set[str] = set()
    for path in glob.glob(os.path.join(corrected_dir, "corrected_*.txt")):
        ts = _extract_ts(path)
        if ts:
            ts_values.add(ts)
    return sorted(ts_values)


def _final_corrected_for_session(dirs: dict[str, str], ts: str) -> str:
    final_dir = dirs.get("final_corrected", "")
    if not final_dir:
        return ""
    session_ts = _corrected_session_ts_values(dirs)
    try:
        index = session_ts.index(ts)
    except ValueError:
        index = -1
    next_ts = session_ts[index + 1] if index >= 0 and index + 1 < len(session_ts) else ""
    candidates: list[tuple[str, str]] = []
    for path in glob.glob(os.path.join(final_dir, "final_corrected_*.txt")):
        final_ts = _extract_ts(path)
        if final_ts and final_ts >= ts and (not next_ts or final_ts < next_ts):
            candidates.append((final_ts, path))
    return max(candidates, default=("", ""))[1]


def _find_audio(audio_dir: str, ts: str) -> str:
    if not audio_dir:
        return ""
    matches = sorted(glob.glob(os.path.join(audio_dir, f"audio_{ts}.*")))
    return matches[0] if matches else ""


def _first_existing(*paths: str) -> str:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return ""


def _extract_ts(path: str) -> str:
    match = TS_RE.search(os.path.basename(path))
    return match.group(1) if match else ""


def _format_ts(ts: str) -> str:
    try:
        return datetime.strptime(ts, "%Y%m%d_%H%M%S").strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def _seconds_from_ts(ts: str) -> int:
    dt = datetime.strptime(ts, "%Y%m%d_%H%M%S")
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def _title_for_session(summary_path: str, corrected_path: str) -> str:
    if summary_path:
        try:
            lines = Path(summary_path).read_text(encoding="utf-8-sig").splitlines()
            for line in lines:
                match = SUMMARY_TOPIC_RE.match(line)
                if match:
                    return _clean_title(match.group(1))
            for line in lines:
                s = line.strip()
                if s.startswith("#"):
                    return _clean_title(s.lstrip("#").strip())
        except Exception:
            pass
    if corrected_path:
        try:
            text = Path(corrected_path).read_text(encoding="utf-8-sig")
            for line in text.splitlines():
                match = STAMP_RE.match(line)
                body = (match.group(4) if match else line).strip()
                if body:
                    return _clean_title(body[:48])
        except Exception:
            pass
    return ""


def _clean_title(text: str) -> str:
    return text.strip().strip("()\uFF08\uFF09").strip()[:80]


def _normalize_saved_text(text: str) -> str:
    raw_lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while raw_lines and not raw_lines[-1]:
        raw_lines.pop()

    lines: list[str] = []
    for line in raw_lines:
        if not line.strip():
            continue
        match = STAMP_RE.match(line)
        if match:
            lines.append(f"[{match.group(1)}:{match.group(2)}:{match.group(3)}] {match.group(4).strip()}".rstrip())
            continue
        if not lines:
            lines.append(line.strip())
            continue
        lines[-1] = f"{lines[-1].rstrip()} {line.strip()}"
    return "\n".join(lines) + ("\n" if lines else "")


def _timestamped_lines(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = STAMP_RE.match(line)
        if match:
            result.append((f"{match.group(1)}:{match.group(2)}:{match.group(3)}", match.group(4).strip()))
    return result


def _paired_bodies_for_learning(
    old_lines: list[tuple[str, str]],
    new_lines: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    if len(old_lines) == len(new_lines) and all(old[0] == new[0] for old, new in zip(old_lines, new_lines)):
        return [(old[1], new[1]) for old, new in zip(old_lines, new_lines)]

    paired: list[tuple[str, str]] = []
    used_old: set[int] = set()
    for new_index, (new_stamp, new_body) in enumerate(new_lines):
        if new_index < len(old_lines) and old_lines[new_index][0] == new_stamp:
            used_old.add(new_index)
            paired.append((old_lines[new_index][1], new_body))
            continue
        for old_index, (old_stamp, old_body) in enumerate(old_lines):
            if old_index not in used_old and old_stamp == new_stamp:
                used_old.add(old_index)
                paired.append((old_body, new_body))
                break
    return paired


def _diff_pairs(old: str, new: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    old_tokens = _tokenize_for_diff(old)
    new_tokens = _tokenize_for_diff(new)
    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            pair = _replace_pair_from_new_unit(old, new, old_tokens, new_tokens, i1, i2, j1, j2)
            if pair is not None:
                pairs.append(pair)
    return pairs


def _replace_pair_from_new_unit(
    old: str,
    new: str,
    old_tokens: list[str],
    new_tokens: list[str],
    i1: int,
    i2: int,
    j1: int,
    j2: int,
) -> tuple[str, str] | None:
    right = "".join(new_tokens[j1:j2]).strip()
    if not right:
        return None

    spans = _token_spans(new, new_tokens)
    if j1 >= len(spans) or j2 - 1 >= len(spans):
        return None
    new_start = spans[j1][0]
    new_end = spans[j2 - 1][1]
    right_unit = _expand_to_new_word_unit(new, new_start, new_end)
    if not right_unit:
        return None

    old_raw = "".join(old_tokens[i1:i2]).strip()
    right_raw = right_unit.strip()
    if _is_insert_delete_like(old_raw, right_raw):
        return None

    # Use the corrected/new word unit as the anchor; trim shared context from
    # the old changed area only when Sudachi/fallback made the old side wider.
    wrong_unit, right_unit = _align_old_to_new_unit(old_raw, right_raw)
    if not wrong_unit or not right_unit or wrong_unit == right_unit:
        return None
    if _is_insert_delete_like(wrong_unit, right_unit):
        return None
    return wrong_unit, right_unit


def _tokenize_for_diff(text: str) -> list[str]:
    if _has_japanese(text):
        sudachi_tokens = _sudachi_tokenize(text)
        if sudachi_tokens:
            return sudachi_tokens
    return TOKEN_RE.findall(text)


def _token_spans(text: str, tokens: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for token in tokens:
        idx = text.find(token, pos)
        if idx < 0:
            idx = pos
        end = idx + len(token)
        spans.append((idx, end))
        pos = end
    return spans


def _expand_to_new_word_unit(text: str, start: int, end: int) -> str:
    if start < 0 or end <= start:
        return ""
    if any(_is_cjk_char(ch) for ch in text[start:end]):
        # The corrected side defines the learning unit. If Sudachi already gave
        # us a multi-character token, keep it. If fallback gave one CJK
        # character, add a little left context without absorbing the following noun.
        if end - start > 1:
            return text[start:end].strip()
        left = start
        while left > 0 and _is_cjk_char(text[left - 1]) and end - left < 2:
            left -= 1
        return text[left:end].strip()
    return text[start:end].strip()


def _align_old_to_new_unit(old_raw: str, right_unit: str) -> tuple[str, str]:
    if _is_cjk_like_token(old_raw) and _is_cjk_like_token(right_unit):
        return _compact_cjk_pair(old_raw, right_unit)
    return old_raw.strip(), right_unit.strip()


def _is_insert_delete_like(wrong: str, right: str) -> bool:
    if not wrong or not right or wrong == right:
        return True
    # If one side simply contains the other, the user probably inserted/deleted
    # characters rather than correcting a stable ASR misrecognition.
    return wrong in right or right in wrong


def _is_cjk_char(ch: str) -> bool:
    return bool(re.fullmatch(r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF\u30FC]", ch))


def _has_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]", text))


def _sudachi_tokenize(text: str) -> list[str]:
    global _SUDACHI_TOKENIZER, _SUDACHI_MODE, _SUDACHI_LOAD_FAILED
    if _SUDACHI_LOAD_FAILED:
        return []
    try:
        if _SUDACHI_TOKENIZER is None:
            from sudachipy import Dictionary, SplitMode

            _SUDACHI_TOKENIZER = Dictionary().create()
            _SUDACHI_MODE = SplitMode.B
        return [
            morpheme.surface()
            for morpheme in _SUDACHI_TOKENIZER.tokenize(text, _SUDACHI_MODE)
            if morpheme.surface().strip()
        ]
    except Exception:
        _SUDACHI_LOAD_FAILED = True
        return []


def _compact_cjk_pair(wrong: str, right: str) -> tuple[str, str]:
    if not wrong or not right or not (_is_cjk_like_token(wrong) and _is_cjk_like_token(right)):
        return wrong, right

    prefix = 0
    max_prefix = min(len(wrong), len(right))
    while prefix < max_prefix and wrong[prefix] == right[prefix]:
        prefix += 1

    suffix = 0
    max_suffix = min(len(wrong), len(right)) - prefix
    while suffix < max_suffix and wrong[len(wrong) - suffix - 1] == right[len(right) - suffix - 1]:
        suffix += 1

    if prefix == 0 and suffix == 0:
        return wrong, right
    if prefix >= len(wrong) or prefix >= len(right):
        return wrong, right

    left_take = min(prefix, 2)
    right_take = 0 if left_take else min(suffix, 2)
    old_start = prefix - left_take
    new_start = prefix - left_take
    old_end = len(wrong) - suffix + right_take
    new_end = len(right) - suffix + right_take
    compact_wrong = wrong[old_start:old_end]
    compact_right = right[new_start:new_end]
    if compact_wrong and compact_right and compact_wrong != compact_right:
        return compact_wrong, compact_right
    return wrong, right


def _is_cjk_like_token(token: str) -> bool:
    return bool(re.fullmatch(r"[\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFFー]+", token))


def _valid_pair(wrong: str, right: str) -> bool:
    if not wrong or not right or wrong == right:
        return False
    if len(wrong) > 20 or len(right) > 20:
        return False
    if _is_punct_or_space(wrong) or _is_punct_or_space(right):
        return False
    return True


def _is_punct_or_space(text: str) -> bool:
    return all(ch.isspace() or unicodedata.category(ch).startswith("P") for ch in text)


def _proper_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9][A-Za-z0-9._-]{1,}\b", text):
        terms.append(match.group(0))
    for match in re.finditer(r"[\u30A0-\u30FF\u30FC]{3,}", text):
        terms.append(match.group(0))
    return terms


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str]] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            result.append(term)
    return result

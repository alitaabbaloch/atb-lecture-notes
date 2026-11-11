"""Roman Urdu step: every Urdu-script transcript line is rewritten in Latin letters by the notes AI.

Command line: python addons/notes/make_roman.py <transcript.json> [...]  converts an existing lesson in place.
The old transcript is kept as <name>.urdu.md next to it (never overwritten).
"""
import json
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import llm

ROMAN_SYSTEM = """You rewrite Urdu-script transcript lines in Roman Urdu: Urdu written in Latin letters, the way people in Pakistan type it.
Rules:
- Keep every word, in the same order. Do not translate Urdu words into English.
- Do not correct, improve, shorten or add anything. Repeated words stay repeated.
- English words and names that were written in Urdu script are written in normal English spelling.
- English words already in Latin letters stay exactly as they are.
- If a word is garbled, write it as it sounds. Do not guess a meaning.
- Keep digits as digits.
Reply with JSON only: a list of strings, one per input line, same count, same order."""

MAX_LINES = 40
MAX_CHARS = 3000
MIN_RATIO, MAX_RATIO = 0.7, 1.45
THREADS = 4
STAGE = "converting to Roman Urdu"

_ARABIC_RANGE = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*(.*?)\s*```$", re.S)


def has_arabic(text: str) -> bool:
    """True when the text holds an Arabic-script letter (digits and punctuation of that script do not count)."""
    return any(unicodedata.category(c).startswith("L") for c in _ARABIC_RANGE.findall(text or ""))


def _words(lines) -> int:
    return sum(len(x.split()) for x in lines)


def _batches(indexes, lines) -> list[list[int]]:
    """Consecutive indexes in groups of at most MAX_LINES lines and MAX_CHARS characters (one long line goes alone)."""
    out, cur, size = [], [], 0
    for i in indexes:
        n = len(lines[i])
        if cur and (len(cur) >= MAX_LINES or size + n > MAX_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _parse(answer, batch: list[str]) -> list[str] | None:
    """The accepted list of Roman lines, or None when the answer breaks any rule."""
    text = str(answer).strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, list) or len(data) != len(batch):
        return None
    if not all(isinstance(x, str) and x.strip() for x in data):
        return None
    data = [x.strip() for x in data]
    if any(has_arabic(x) for x in data):
        return None
    ratio = _words(data) / max(1, _words(batch))
    return data if MIN_RATIO <= ratio <= MAX_RATIO else None


def _default_chat(system, user, *, purpose, max_tokens):
    return llm.chat(system, user, purpose=purpose, max_tokens=max_tokens)


def _convert_batch(batch: list[str], chat) -> list[str] | None:
    for _ in range(2):  # one retry
        try:
            answer = chat(ROMAN_SYSTEM, json.dumps(batch, ensure_ascii=False), purpose="roman", max_tokens=8000)
        except Exception:  # noqa: BLE001  a provider error counts as a failed attempt
            continue
        parsed = _parse(answer, batch)
        if parsed is not None:
            return parsed
    return None


def romanize_lines(lines, chat=None) -> tuple[list[str], list[int]]:
    """(roman_lines, failed_indexes). Lines without Arabic letters are never sent. Failed lines stay as they were."""
    chat = chat or _default_chat
    lines = [str(x) for x in lines]
    out = list(lines)
    failed: list[int] = []
    groups = _batches([i for i, x in enumerate(lines) if has_arabic(x)], lines)
    if not groups:
        return out, failed
    with ThreadPoolExecutor(max_workers=min(THREADS, len(groups))) as pool:
        answers = list(pool.map(lambda g: _convert_batch([lines[i] for i in g], chat), groups))
    for group, answer in zip(groups, answers):
        if answer is None:
            failed.extend(group)
        else:
            for i, text in zip(group, answer):
                out[i] = text
    return out, sorted(failed)


def romanize_result(result: dict, chat=None) -> dict:
    """A copy of the result with Roman text and the Urdu kept in each segment's "urdu" key."""
    segments = result.get("segments") or []
    if not segments:
        result = dict(result)
        result["script"] = "urdu"
        return result
    sources = [str(s["urdu"] if "urdu" in s else s.get("text", "")).strip() for s in segments]
    roman, failed = romanize_lines(sources, chat)
    bad = set(failed)
    new_segments = []
    for i, (seg, src, line) in enumerate(zip(segments, sources, roman)):
        seg = dict(seg)
        if i in bad:
            seg["text"] = src
            seg.pop("urdu", None)
        else:
            seg["urdu"], seg["text"] = src, line
        new_segments.append(seg)
    convertible = sum(1 for x in sources if has_arabic(x))
    out = dict(result)
    out["segments"] = new_segments
    out["text"] = " ".join(s["text"].strip() for s in new_segments if s["text"].strip())
    out["script"] = "urdu" if failed and len(failed) >= convertible else "mixed" if failed else "roman"
    out["quality"] = {**(result.get("quality") or {}), "roman_failed_lines": len(failed)}
    return out


# ---- command line ------------------------------------------------------------------------------------------

def _frontmatter(md: Path) -> dict:
    try:
        head = md.read_text(encoding="utf-8").split("\n---", 1)[0]
    except OSError:
        return {}
    out = {}
    for line in head.splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            out[key.strip()] = value.strip()
    return out


def _seconds(text: str) -> float:
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return 0.0


def convert_file(json_path: Path, chat=None) -> tuple[Path, int]:
    """Convert one lesson in place. Returns (md path, failed lines)."""
    import writer
    json_path = Path(json_path)
    md = json_path.with_suffix(".md")
    if not re.match(r"\d+-", md.name):
        raise ValueError("the transcript file name must start with a number, like 01-name.json")
    result = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("not a transcript file")
    front = _frontmatter(md)
    new = romanize_result(result, chat)
    backup = md.with_name(md.stem + ".urdu.md")
    if md.exists() and not backup.exists():
        backup.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    meta = {"url": front.get("url"), "recorded": front.get("recorded"), "duration": _seconds(front.get("duration", "")),
            "model": front.get("model"), "language": front.get("language") or result.get("language"),
            "audio": front.get("audio"), "speed": front.get("speed"), "engine": front.get("engine")}
    writer.write_transcript(md.parent, front.get("course", ""), front.get("lesson", md.stem), new, meta,
                            existing_md=md.name)
    return md, new["quality"]["roman_failed_lines"]


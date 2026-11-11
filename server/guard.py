"""Pure checks on Whisper output: repeat loops, wrong scripts, captions. No audio, no model.

A segment is a dict with at least start, end, text (and usually avg_logprob and temperature).
Segment ids in a Window are positions in the list that was passed in.
"""
import re
import unicodedata
from collections import Counter
from typing import NamedTuple

MIN_LOOP_WORDS = 8
RUN_LENGTH = 5
TRIPLE_COUNT = 4
UNIQUE_RATIO = 0.35
ZERO_LENGTH = 0.1
ZERO_CLUSTER = 3
ZERO_SPAN = 10.0
WINDOW_JOIN = 30.0
MIN_LOGPROB = -1.0
MAX_TEMPERATURE = 0.4
GAP_SECONDS = 8.0     # a stretch this long inside a block with no text line is suspect
THIN_SECONDS = 8.0    # a line this long...
THIN_SHARE = 0.35     # ...with fewer words per second than this share of the lesson's median line rate
THIN_MIN_LINES = 20   # no thin-line rule for very short transcripts

CAPTIONS = {"موسیقی", "music", "applause", "تالیاں", "♪", "nan"}
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_EDGE = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_BRACKETS = re.compile(r"[\[\]\(\)\{\}<>*]")
_CHAR_LOOP = re.compile(r"([^\W\d_]{1,4})\1{7,}", re.UNICODE | re.IGNORECASE)
_PUNCT = re.compile(r"[.,!?:;\-_\"'…،؟۔]")


class Window(NamedTuple):
    start: float
    end: float
    reasons: list
    segment_ids: list


def _words(text: str) -> list[str]:
    words = (_EDGE.sub("", w).lower() for w in str(text or "").split())
    return [w for w in words if w]


def is_loop_text(text: str) -> bool:
    words = _words(text)
    if len(words) < MIN_LOOP_WORDS:
        return False
    run = best = 1
    for a, b in zip(words, words[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    if best >= RUN_LENGTH:
        return True
    triples = Counter(zip(words, words[1:], words[2:]))
    if triples and max(triples.values()) >= TRIPLE_COUNT:
        return True
    return len(set(words)) / len(words) < UNIQUE_RATIO


def has_char_loop(text: str) -> bool:
    """A 1 to 4 letter unit repeated 8 or more times in a row inside one token (cyncyncyn...)."""
    return bool(_CHAR_LOOP.search(str(text or "")))


def _allowed_letter(ch: str) -> bool:
    o = ord(ch)
    if any(a <= o <= b for a, b in _ARABIC_RANGES):
        return True
    return unicodedata.name(ch, "").startswith("LATIN")


def has_foreign_script(text: str) -> bool:
    return any(ch.isalpha() and not _allowed_letter(ch) for ch in str(text or ""))


def is_caption(text: str) -> bool:
    rest = _PUNCT.sub("", _BRACKETS.sub("", str(text or ""))).strip().lower()
    rest = re.sub(r"\s+", " ", rest)
    if not rest:
        return False
    return rest in CAPTIONS or set(rest.replace(" ", "")) == {"♪"}


def drop_non_speech(segments):
    """Remove caption segments. Consecutive ones become one non_speech issue. Returns (kept, issues)."""
    kept, issues, run = [], [], []

    def close():
        if run:
            issues.append({"kind": "non_speech", "start": run[0]["start"], "end": run[-1]["end"], "detail": "caption"})
            run.clear()

    for seg in segments:
        if is_caption(seg.get("text", "")):
            run.append(seg)
        else:
            close()
            kept.append(seg)
    close()
    return kept, issues


def _flags(segments) -> dict[int, set]:
    """Index of every flagged segment -> the reasons."""
    flags: dict[int, set] = {}

    def add(i, reason):
        flags.setdefault(i, set()).add(reason)

    same = 1
    zeros = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text", "")).strip()
        if is_loop_text(text) or has_char_loop(text):
            add(i, "loop")
        if has_foreign_script(text):
            add(i, "foreign_script")
        if i and text and text == str(segments[i - 1].get("text", "")).strip():
            same += 1
        else:
            same = 1
        if same >= 3:
            add(i, "repeat")
        if seg["end"] - seg["start"] < ZERO_LENGTH:
            zeros.append(i)
    for a, i in enumerate(zeros):  # three or more zero-length segments inside any 10 s stretch
        group = [j for j in zeros[a:] if segments[j]["start"] - segments[i]["start"] <= ZERO_SPAN]
        if len(group) >= ZERO_CLUSTER:
            for j in group:
                add(j, "zero_length")
    return flags


def find_bad_windows(segments) -> list[Window]:
    flags = _flags(segments)
    windows: list[Window] = []
    for i in sorted(flags):
        seg = segments[i]
        if windows and seg["start"] - windows[-1].end < WINDOW_JOIN:
            w = windows[-1]
            windows[-1] = Window(w.start, max(w.end, seg["end"]), sorted(set(w.reasons) | flags[i]),
                                 w.segment_ids + [i])
        else:
            windows.append(Window(seg["start"], seg["end"], sorted(flags[i]), [i]))
    return windows


def acceptable(segments) -> bool:
    if _flags(segments):
        return False
    if any((s.get("temperature") or 0) > MAX_TEMPERATURE for s in segments):
        return False
    weighted = [(max(s["end"] - s["start"], 0.0), s["avg_logprob"]) for s in segments
                if s.get("avg_logprob") is not None]
    total = sum(w for w, _ in weighted)
    if weighted:
        mean = sum(w * lp for w, lp in weighted) / total if total > 0 else sum(lp for _, lp in weighted) / len(weighted)
        if mean < MIN_LOGPROB:
            return False
    return True


def find_gaps(segments, blocks, issues=(), min_seconds=GAP_SECONDS) -> list[tuple[float, float]]:
    """Stretches inside a block that no text line and no known issue covers."""
    covered = [(s["start"], s["end"]) for s in segments if _words(s.get("text", ""))]
    covered += [(x["start"], x["end"]) for x in issues if x.get("kind") in ("non_speech", "unclear")]
    covered.sort()
    gaps = []
    for a, b in blocks:
        cursor = a
        for start, end in covered:
            if end <= cursor:
                continue
            if start >= b:
                break
            if start - cursor >= min_seconds:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if b - cursor >= min_seconds:
            gaps.append((cursor, b))
    return gaps


def find_thin(segments, min_seconds=THIN_SECONDS, share=THIN_SHARE) -> list[int]:
    """Positions of lines that last long but hold few words."""
    rates = sorted(len(_words(s.get("text", ""))) / (s["end"] - s["start"]) for s in segments
                   if _words(s.get("text", "")) and s["end"] - s["start"] >= 1.0)
    if len(rates) < THIN_MIN_LINES:
        return []
    mid = len(rates) // 2
    median = rates[mid] if len(rates) % 2 else (rates[mid - 1] + rates[mid]) / 2
    return [i for i, s in enumerate(segments)
            if _words(s.get("text", "")) and s["end"] - s["start"] >= min_seconds
            and len(_words(s["text"])) / (s["end"] - s["start"]) < share * median]

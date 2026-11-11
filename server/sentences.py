"""Cut long speech-model segments into sentence lines using word times. Pure: no audio, no model.

The speech model returns one segment per 30 s of audio. With word times on, every segment carries a
`words` list. cut() turns those segments into sentence lines so the loop guard, repair, writer and
notes keep working on line-sized pieces.
"""
import re

MIN_WORDS = 4          # a shorter sentence joins a neighbour
MAX_WORDS = 40         # a longer sentence is cut at a pause
MAX_WORD_SECONDS = 1.5 # the aligner can stretch a last word into silence: cap it
MIN_SECONDS = 0.1      # no line shorter than this
ENDS = ".?!"
UNKNOWN = "(?)"       # what the model's `nan` becomes inside a line
_TRAIL = "\"')]"
_PUNCT_RUN = re.compile(r"([!?.,])\1{2,}")
_EPS = 1e-9
_META = ("avg_logprob", "compression_ratio", "no_speech_prob")


def _is_empty(text: str) -> bool:
    """True when the text is `nan` or holds no letter or digit at all."""
    kept = _alnum(text)
    return kept == "" or _is_nan(kept)


def _alnum(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _is_nan(kept: str) -> bool:
    """`nan` written one or more times: nan, nannan."""
    return len(kept) % 3 == 0 and kept == "nan" * (len(kept) // 3)


def _clean_words(parent: dict) -> list[tuple[str, float, float]]:
    out = []
    for w in parent.get("words") or []:
        text = _PUNCT_RUN.sub(r"\1", str(w.get("word", "")).strip())
        kept = _alnum(text)
        if not kept:
            continue
        if _is_nan(kept):
            text = UNKNOWN + (text[-1] if text[-1] in ".?!" else "")
        start = float(w["start"])
        out.append((text, start, min(float(w["end"]), start + MAX_WORD_SECONDS)))
    return out


def _split_sentences(words: list) -> list[list]:
    sentences, current = [], []
    for word in words:
        current.append(word)
        if word[0] != UNKNOWN and word[0].rstrip(_TRAIL).endswith(tuple(ENDS)):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def _join_short(sentences: list[list]) -> list[list]:
    out, carry = [], []
    for i, sentence in enumerate(sentences):
        sentence = carry + sentence
        carry = []
        if len(sentence) < MIN_WORDS and i < len(sentences) - 1:
            carry = sentence
        else:
            out.append(sentence)
    if len(out) > 1 and len(out[-1]) < MIN_WORDS:
        last = out.pop()
        out[-1] = out[-1] + last
    return out


def _cut_long(sentence: list) -> list[list]:
    parts = []
    while len(sentence) > MAX_WORDS:
        positions = range(MAX_WORDS // 2, MAX_WORDS + 1)  # 1-based position of the word that starts the next part
        best = max(positions, key=lambda p: (sentence[p - 1][1] - sentence[p - 2][2], p))
        parts.append(sentence[:best - 1])
        sentence = sentence[best - 1:]
    parts.append(sentence)
    return parts


def _fix_times(lines: list[list], parent_start: float, parent_end: float) -> list[list]:
    """Each line is [start, end, text]. Order them, keep them inside the parent, none under MIN_SECONDS."""
    cursor = parent_start
    for line in lines:
        line[0] = min(max(line[0], cursor), parent_end)
        line[1] = max(min(line[1], parent_end), line[0])
        cursor = line[1]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line[1] - line[0] >= MIN_SECONDS - _EPS:
            i += 1
            continue
        limit = lines[i + 1][0] if i + 1 < len(lines) else parent_end
        if line[0] + MIN_SECONDS <= limit + _EPS:
            line[1] = line[0] + MIN_SECONDS
            i += 1
        elif i + 1 < len(lines):
            lines[i + 1] = [line[0], lines[i + 1][1], line[2] + " " + lines[i + 1][2]]
            del lines[i]
        elif i > 0:
            lines[i - 1] = [lines[i - 1][0], line[1], lines[i - 1][2] + " " + line[2]]
            del lines[i]
        else:
            i += 1
    return lines


def _cut_parent(parent: dict) -> list[list]:
    """[start, end, text] lines for one parent that is not empty."""
    whole = [[float(parent["start"]), float(parent["end"]), str(parent.get("text") or "").strip()]]
    words = _clean_words(parent)
    if not words:
        return whole
    sentences = []
    for sentence in _join_short(_split_sentences(words)):
        sentences.extend(_cut_long(sentence))
    sentences = [s for s in sentences if not all(w[0].rstrip(ENDS) == UNKNOWN for w in s)]
    lines = [[s[0][1], s[-1][2], " ".join(w[0] for w in s)] for s in sentences]
    return _fix_times(lines, float(parent["start"]), float(parent["end"]))


def cut(segments: list[dict]) -> list[dict]:
    """Sentence lines from speech-model segments that carry word times."""
    out = []
    for parent in segments:
        text = str(parent.get("text") or "")
        if _is_empty(text):
            continue
        for start, end, line_text in _cut_parent(parent):
            line = {"id": len(out), "start": start, "end": end, "text": line_text}
            line.update({k: parent.get(k) for k in _META})
            line["temperature"] = parent.get("temperature") or 0.0
            out.append(line)
    return out

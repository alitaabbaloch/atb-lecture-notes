"""Turn a lesson transcript (notes/<course>/<NN>-<lesson>.md and its .json) into traceable study notes.

Steps: cut the lesson into sections of about ten minutes of speech (five minutes of recording when it was played
at 2x); one model call per section returns JSON bullets
that carry a timestamp; code checks every note against the transcript; a second call (another
provider when there is one) verifies the bullets; code builds the Markdown file. The model never writes the file.

Providers and keys: see llm.py. ATB_NOTES=fake runs the whole flow with a built-in fake and no network call.

Usage:  python addons/notes/make_notes.py <path-to-md> [more files...]
Writes  <NN>-<lesson>-notes.md and <NN>-<lesson>-notes.json next to each transcript.
"""
import json
import logging
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import glossary as glossary_mod
import llm
import writer

log = logging.getLogger("atb")

SECTION_TARGET = 600.0      # seconds of speech per section, at natural speed
SINGLE_SECTION_MAX = 900.0  # a shorter lesson is one section
CUT_WINDOW = 120.0          # how far from the ideal cut a better cut point may be
LONG_GAP = 3.0
TS_TOLERANCE = 20.0         # a bullet must sit this close to a real line
SUPPORT_WINDOW = 60.0       # numbers and terms are looked for this close to the bullet
KINDS = ("point", "step", "action", "term", "tip")
UNCLEAR_MAX = 5
TURN_SLACK = 3.0            # a line can hold the end of one speaker and the start of the next
SECTION_MAX_TOKENS = 16000

SECTION_SYSTEM = """You turn one part of a lecture transcript into study notes a student will revise from.

About the transcript: it is machine-made from an Urdu lecture with English technical terms mixed in, sometimes spelled phonetically in Urdu script. It has no speaker labels. Some lessons are question-and-answer sessions where students speak too. Some lines are garbled.

Reply with JSON only, no other text, in exactly this shape and key order:
{"turns": [{"t": "mm:ss", "who": "teacher|student"}],
 "summary": "two sentences about what this part teaches",
 "notes": [{"t": "mm:ss", "kind": "point|step|action|term|tip", "text": "..."}],
 "qa": [{"t": "mm:ss", "question": "...", "answer_t": "mm:ss", "answer": "..."}],
 "unclear": [{"t": "mm:ss", "until": "mm:ss", "why": "..."}]}

First fill "turns" (who is speaking):
- Read the whole part and list every change of speaker: one entry per turn, not one per line. "t" is the timestamp of the first line of the turn, copied exactly from the transcript. "who" is teacher or student. The first entry is the first line of this part.
- A line can hold the end of one speaker's words and the start of the next speaker's. Start the new turn at that line.
- Students greet, say "sir", give their name or city, ask, describe what they did, repeat what they understood, propose, and thank. The teacher answers, explains, corrects, and calls the next student by name.
- A part where only the teacher speaks has one entry.
- When you cannot tell who speaks, mark the turn as student.

What goes in "notes" (the teacher's teaching content only):
- Only from teacher turns: what a student should remember or do. Rules, criteria, numbers, tools and how they are used, steps, warnings, definitions, examples that carry a lesson.
- Aim for 5 to 12 notes for ten minutes of teaching. Fewer is fine. Never pad. If this part teaches nothing, return an empty list.
- Join lines that make one point into one note. "t" is the timestamp of the first line the note rests on, copied exactly from the transcript.
- kind: point (an idea or rule), step (one step of a procedure, in order), action (something the student is told to do), term (a tool or term and what it is), tip (a tip or a warning).
- Leave out greetings, thanks, sound checks, names being called, jokes, encouragement and class admin.

What goes in "qa" (question-and-answer exchanges):
- One entry for each real question a student asks about the subject being taught: the question in one sentence, and the teacher's answer in one to three sentences. Skip personal stories, prayers, thanks, introductions and meeting arrangements.
- Write the question itself ("Should we ...?"), not a report of it ("The student asks whether ...").
- "t" is the timestamp of the question, inside a student turn. "answer_t" is the first line of the teacher's reply, inside a teacher turn after the question.
- The answer holds only what the teacher says in that reply. Never put the student's own words in the answer.
- When a student describes what they did, repeats the lesson or proposes something, and the teacher only agrees, write the description as the question ("Is it right to ...?") and the agreement plus anything the teacher adds as the answer ("Yes. Only make keyword clusters.").
- Start an answer with Yes or No only when the teacher says so.
- When the teacher puts the matter off ("for now just do X", "that comes later"), the answer says exactly that.
- When the teacher is not sure or says he does not know, the answer says so.
- If the teacher does not answer, or the answer cannot be understood, set "answer" and "answer_t" to "".
- A question the teacher asks himself is not an entry.
- A student's statement, guess or proposal is never the teacher's teaching. Put it in "qa" as the question, never in "notes".

What goes in "unclear":
- Only passages that seem to carry teaching content but cannot be understood. At most 5. Join neighbouring lines into one span from "t" to "until".
- Do not list every garbled line. Ignore garbled small talk.
- Lines marked [unclear until ...] are already known gaps: do not repeat them.

Rules for all text:
- Clear English. English technical terms and tool names in Latin letters, spelled correctly when you are sure which term is meant. The glossary terms are the likeliest candidates.
- A term spelled phonetically that you cannot identify with confidence must not be replaced by a different term. Write it as it sounds in Latin letters followed by (?), for example "bai code (?)".
- Keep qualifiers exactly: low stays low, high stays high.
- Say only what the transcript says. No outside knowledge. Never invent a URL, a name, a number or a tool. Do not add what a speaker implies.
- Keep numbers exactly. A number spoken in words may be written in digits.
- If you are not sure what was meant, leave it out or put it in "unclear". Do not guess."""

VERIFY_SYSTEM = """You check study notes against the transcript lines they came from. The transcript has no speaker labels; students usually address the teacher as "sir" and ask questions.
For every item decide:
- "supported": the transcript lines near its timestamp say this.
- "unsupported": the transcript does not say this (invented, guessed, or not found near that time). Also when the item names a tool, a technique or a qualifier such as low or high that differs from the word in the transcript lines.
- "wrong_speaker": for a note: a student says it, but the note presents it as the teacher's teaching. For a question-and-answer item: the answer repeats what the student said or proposed, and the teacher's reply does not say it.
For a question-and-answer item, judge the answer: "supported" only if the teacher's reply in the transcript says it.
Reply with JSON only: [{"id": 0, "verdict": "supported"}], one entry per id."""

ROMAN_ABOUT = """About the transcript: it is machine-made from an Urdu lecture and written in Roman letters, with Hindi-style spelling. English technical terms are mixed in and may be misspelled. A line that starts with (second hearing) was filled in from another speech model and is in Urdu script. It has no speaker labels. Some lessons are question-and-answer sessions where students speak too. Some lines are garbled."""

SECOND_SECTION = """A second hearing: after the lines you also get <second_hearing>. It is the same audio transcribed by another speech model, with its own timestamps. Use it to work out what a garbled word, name, number or sentence in the lines really is. When the two hearings agree, trust them. When one is garbled and the other is clear, use the clear one. When they disagree on a name, a number or a qualifier and the context does not settle it, do not pick one: leave it out or put it in "unclear". Every timestamp in your answer comes from the lines, never from the second hearing."""

SECOND_VERIFY = """You also get <second_hearing>: the same audio transcribed by another speech model. An item is "supported" when either hearing says it and the other does not clearly contradict it."""

SUMMARY_SYSTEM = """Write a summary of 3 to 6 sentences of what this lesson teaches and why it matters, in clear English.
Use only the section summaries and key points given. Plain sentences only: no headings, no bullets, no preamble.
Do not add qualifiers or details that are not in the section summaries or key points."""

ABOUT_RE = re.compile(r"^About the transcript:.*$", re.M)


def section_system(roman: bool, second: bool) -> str:
    """The section prompt for this kind of transcript."""
    text = ABOUT_RE.sub(lambda _: ROMAN_ABOUT, SECTION_SYSTEM, count=1) if roman else SECTION_SYSTEM
    return text + "\n\n" + SECOND_SECTION if second else text


def verify_system(second: bool) -> str:
    if not second:
        return VERIFY_SYSTEM
    return VERIFY_SYSTEM.replace("Reply with JSON only", SECOND_VERIFY + "\nReply with JSON only", 1)


RETRY_NOTE = "\n\nYour last answer was not valid JSON in the required shape. Return valid JSON only, with no other text."


@dataclass
class Line:
    start: float
    end: float
    text: str = ""
    unclear: bool = False
    block: int | None = None

    @property
    def speech(self) -> float:
        return max(0.0, min(self.end - self.start, 30.0))


# ---- reading the transcript ------------------------------------------------------------------------------

LINE_RE = re.compile(r"^\*\*\[(\d+(?::\d\d){1,2})\]\*\*\s*(.*)$")
UNCLEAR_RE = re.compile(r"^\[unclear until (\d+(?::\d\d){1,2})\]$")


def parse_ts(value) -> float | None:
    parts = str(value).strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return float(seconds)


def transcript_body(md: Path) -> tuple[str, str]:
    """Title and the lines between '## Transcript' and '## Plain text' (the plain text repeats the transcript)."""
    text = md.read_text(encoding="utf-8")
    m = re.search(r"^lesson:\s*(.*)$", text, re.M)
    title = m.group(1).strip() if m else md.stem
    start = re.search(r"^## Transcript\s*$", text, re.M)
    body = text[start.end():] if start else text
    end = re.search(r"^## Plain text\s*$", body, re.M)
    if end:
        body = body[:end.start()]
    return title, body.strip()


def _has_arabic(text: str) -> bool:
    return any(c.isalpha() and "ARABIC" in unicodedata.name(c, "") for c in text)


def is_roman(lines: list[Line]) -> bool:
    """True when more than half of the lines that hold a letter have no Arabic-script letter."""
    texts = [l.text for l in lines if not l.unclear and any(c.isalpha() for c in l.text)]
    return bool(texts) and sum(not _has_arabic(t) for t in texts) * 2 > len(texts)


def load_second(md: Path, gloss: glossary_mod.Glossary | None = None) -> list[tuple[float, str]]:
    """(start seconds, text) of the second hearing saved next to the transcript, or [] when there is none."""
    try:
        data = json.loads(md.with_name(md.stem + ".second.json").read_text(encoding="utf-8"))
        found = []
        for s in data["segments"]:
            text = str(s.get("text", "")).strip()
            if text:
                found.append((float(s["start"]), gloss.apply(text)[0] if gloss is not None else text))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return sorted(found, key=lambda x: x[0])


def playback_speed(data: dict) -> float:
    """How fast the lesson was played while it was recorded, from the transcript's .json. 1.0 when unknown."""
    try:
        speed = float(data.get("speed") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return speed if 0.25 <= speed <= 4.0 else 1.0


def load_lesson(md: Path, gloss: glossary_mod.Glossary | None = None) -> dict:
    """{"title", "lines": [Line], "duration", "speed", "roman", "second"}. The .json is optional; the old shape (no "v") counts as clean."""
    title, body = transcript_body(md)
    try:
        data = json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    segments = [s for s in data.get("segments", []) if isinstance(s, dict) and str(s.get("text", "")).strip()]
    ends: dict[int, list] = {}
    for s in segments:
        ends.setdefault(int(s.get("start", 0)), []).append(s)
    lines: list[Line] = []
    for raw in body.splitlines():
        m = LINE_RE.match(raw.strip())
        if not m:
            continue
        start, text = parse_ts(m.group(1)), m.group(2).strip()
        if start is None or not text:
            continue
        u = UNCLEAR_RE.match(text)
        if u:
            lines.append(Line(start, parse_ts(u.group(1)) or start, "", True))
            continue
        pending = ends.get(int(start))
        seg = pending.pop(0) if pending else None
        if seg and str(seg.get("urdu", "")).strip():  # the transcript is Roman: notes read the more faithful Urdu
            text = str(seg["urdu"]).strip()
        end = float(seg["end"]) if seg and seg.get("end") is not None else start
        if gloss is not None:
            text = gloss.apply(text)[0]
        lines.append(Line(start, max(end, start), text))
    if data.get("v") == 2:  # an unclear issue the transcript did not carry (hand-made files)
        have = {int(line.start) for line in lines if line.unclear}
        for issue in data.get("issues", []):
            if isinstance(issue, dict) and issue.get("kind") == "unclear" and int(issue.get("start", 0)) not in have:
                lines.append(Line(float(issue["start"]), float(issue.get("end", issue["start"])), "", True))
    lines.sort(key=lambda line: (line.start, not line.unclear))
    for i, line in enumerate(lines):  # a line without a known end lasts until the next one, at most 10 s
        if not line.unclear and line.end <= line.start:
            nxt = lines[i + 1].start if i + 1 < len(lines) else line.start + 5
            line.end = min(max(nxt, line.start), line.start + 10)
    blocks = [b for b in data.get("blocks", []) if isinstance(b, dict) and "start" in b and "end" in b]
    for line in lines:
        for b in blocks:
            if b["start"] - 1 <= line.start <= b["end"] + 1:
                line.block = b.get("i")
                break
    duration = data.get("duration") or (lines[-1].end if lines else 0)
    return {"title": title, "lines": lines, "duration": duration, "speed": playback_speed(data),
            "roman": is_roman(lines), "second": load_second(md, gloss)}


# ---- sections --------------------------------------------------------------------------------------------

def make_sections(lines: list[Line], target: float = SECTION_TARGET, single_max: float = SINGLE_SECTION_MAX,
                  speed: float = 1.0) -> list:
    """About ten minutes of speech per section, cut at a block boundary or the longest pause nearby.

    `speed` is how fast the lesson was played while it was recorded. At 2x, ten minutes of speech take five minutes
    of recording, so every length is divided by it.
    """
    target, single_max, window, long_gap = target / speed, single_max / speed, CUT_WINDOW / speed, LONG_GAP / speed
    total = sum(line.speech for line in lines)
    if total < single_max or len(lines) < 2:
        return [lines] if lines else []
    n = max(2, round(total / target))
    before, run = [], 0.0
    for line in lines:
        before.append(run)
        run += line.speech
    cuts: list[int] = []
    for k in range(1, n):
        goal, low = total * k / n, (cuts[-1] if cuts else 0) + 1
        if low >= len(lines):
            break
        near = [i for i in range(low, len(lines)) if abs(before[i] - goal) <= window]
        if not near:
            cuts.append(min(range(low, len(lines)), key=lambda i: abs(before[i] - goal)))
            continue
        gap = lambda i: lines[i].start - lines[i - 1].end  # noqa: E731
        edge = [i for i in near if (lines[i].block is not None and lines[i - 1].block is not None
                                    and lines[i].block != lines[i - 1].block) or gap(i) >= long_gap]
        if edge:
            cuts.append(min(edge, key=lambda i: abs(before[i] - goal)))
        else:
            cuts.append(max(near, key=lambda i: (gap(i), -abs(before[i] - goal))))
    bounds = [0, *cuts, len(lines)]
    return [lines[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]


# ---- model calls -----------------------------------------------------------------------------------------

def parse_json(text: str):
    """A fenced or bare JSON object or array. Raises ValueError."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    spans = sorted((text.find(a), a, b) for a, b in (("{", "}"), ("[", "]")) if a in text)
    for start, a, b in spans:
        end = text.rfind(b)
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                continue
    raise ValueError("not valid JSON")


def ask_json(system: str, user: str, *, purpose: str, validate, max_tokens: int):
    """One call, parsed and validated; one retry asking for valid JSON only. Raises ValueError after that."""
    for attempt in (0, 1):
        text = llm.chat(system, user + (RETRY_NOTE if attempt else ""), max_tokens=max_tokens, purpose=purpose)
        try:
            return validate(parse_json(text))
        except ValueError:
            log.warning("notes: %s answer was not valid JSON (attempt %s)", purpose, attempt + 1)
    raise ValueError("the model did not return valid JSON")


def _section_shape(data):
    if not isinstance(data, dict):
        raise ValueError("wrong shape")
    for key in ("turns", "notes", "qa", "unclear"):
        if not isinstance(data.get(key, []), list):
            raise ValueError("wrong shape")
    return data


def _verdict_shape(data):
    if isinstance(data, dict):
        data = data.get("verdicts", data.get("results"))
    if not isinstance(data, list):
        raise ValueError("wrong shape")
    return data


def render_lines(lines: list[Line]) -> str:
    return "\n".join(f"[{writer.fmt_ts(l.start)}] " + (f"[unclear until {writer.fmt_ts(l.end)}]" if l.unclear else l.text)
                     for l in lines)


def hearing_block(second, start: float, end: float) -> str:
    """The second hearing for one stretch of the lesson, as a block for the prompt ('' when there is nothing)."""
    rows = [f"[{writer.fmt_ts(t)}] {text}" for t, text in second if start - 20 <= t <= end + 5]
    return "<second_hearing>\n" + "\n".join(rows) + "\n</second_hearing>" if rows else ""


def section_prompt(title: str, index: int, count: int, lines: list[Line], terms: list[str], previous: str,
                   hearing: str = "") -> str:
    return (f"Lesson title: {title}\n"
            f"Section {index + 1} of {count}, from {writer.fmt_ts(lines[0].start)} to {writer.fmt_ts(lines[-1].end)}.\n"
            f"Glossary terms (spell them exactly like this): {', '.join(terms) if terms else 'none'}\n"
            f"Summary of the previous section: {previous or 'none, this is the first section'}\n\n"
            f"<lines>\n{render_lines(lines)}\n</lines>" + (f"\n\n{hearing}" if hearing else ""))


# ---- checks in code --------------------------------------------------------------------------------------

DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]{2,}")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER_RE.findall(text.translate(DIGITS))}


def one_line(value) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _real_stamp(value, real: list[Line]) -> float | None:
    sec = parse_ts(value)
    if sec is None or not any(abs(l.start - sec) <= TS_TOLERANCE for l in real):
        return None
    return sec


def parse_turns(data: dict, lines: list[Line]) -> list[tuple[float, str]]:
    """The speaker turns the model listed: (seconds, "teacher" or "student"), sorted; the later of two equal times wins."""
    real = [l for l in lines if not l.unclear]
    found: dict[float, str] = {}
    for item in data.get("turns") or []:
        if not isinstance(item, dict):
            continue
        who = str(item.get("who", "")).strip().lower()
        sec = _real_stamp(item.get("t"), real)
        if who in ("teacher", "student") and sec is not None:
            found.pop(sec, None)
            found[sec] = who
    return sorted(found.items())


def who_at(turns: list[tuple[float, str]], sec: float) -> str | None:
    """Who speaks at this time; None when there are no turns."""
    if not turns:
        return None
    who = turns[0][1]
    for start, name in turns:
        if start <= sec + 0.5:
            who = name
    return who


def teacher_near(turns: list[tuple[float, str]], sec: float) -> bool:
    """True when the teacher speaks at this time, or a teacher turn starts within TURN_SLACK seconds after it."""
    if not turns or who_at(turns, sec) == "teacher":
        return True
    return any(who == "teacher" and sec < start <= sec + TURN_SLACK for start, who in turns)


def check_section(data: dict, lines: list[Line], gloss: glossary_mod.Glossary | None) -> dict:
    """Drop what does not sit on a real line. Numbers and terms the transcript lacks are recorded for the JSON only."""
    real = [l for l in lines if not l.unclear]
    known = [(l.start, l.end) for l in lines if l.unclear]
    notes, qa, unclear, rejected, wrong_speaker = [], [], [], [], []
    turns = parse_turns(data, lines)

    def near_text(sec):
        text = " ".join(l.text for l in real if abs(l.start - sec) <= SUPPORT_WINDOW)
        return gloss.apply(text)[0] if gloss is not None else text

    for item in data.get("notes") or []:
        sec = _real_stamp(item.get("t"), real) if isinstance(item, dict) else None
        text = one_line(item.get("text")) if isinstance(item, dict) else ""
        if sec is None or not text:
            rejected.append({"reason": "no real line at this timestamp", "item": item})
            continue
        near = near_text(sec)
        near_words = {w.lower() for w in LATIN_RE.findall(near)}
        kept = {"t": writer.fmt_ts(sec), "sec": sec, "kind": item.get("kind") if item.get("kind") in KINDS else "point",
                "text": text, "verdict": "unchecked",
                "unsupported_numbers": sorted(_numbers(text) - _numbers(near)),
                "unmatched_terms": sorted({w for w in LATIN_RE.findall(text) if w.lower() not in near_words})[:20]}
        if who_at(turns, sec) == "student" and not teacher_near(turns, sec):
            wrong_speaker.append({**kept, "verdict": "wrong_speaker"})
        else:
            notes.append(kept)
    trusted = len(wrong_speaker) <= len(notes)
    if not trusted:  # the turns would empty the section: do not trust them for notes
        notes = sorted(notes + [{**n, "verdict": "unchecked"} for n in wrong_speaker], key=lambda n: n["sec"])
        wrong_speaker = []
    for item in data.get("qa") or []:
        sec = _real_stamp(item.get("t"), real) if isinstance(item, dict) else None
        question = one_line(item.get("question")) if isinstance(item, dict) else ""
        if sec is None or not question:
            rejected.append({"reason": "no real line at this timestamp", "item": item})
            continue
        if who_at(turns, sec) == "teacher":
            rejected.append({"reason": "the question is in a teacher turn", "item": item})
            continue
        answer = one_line(item.get("answer"))
        answer_sec = _real_stamp(item.get("answer_t"), real) if answer else None
        if answer and answer_sec is None and one_line(item.get("answer_t")):
            rejected.append({"reason": "answer timestamp is not a real line", "item": item})
        if answer and answer_sec is not None and (answer_sec < sec or (who_at(turns, answer_sec) == "student" and not teacher_near(turns, answer_sec))):
            rejected.append({"reason": "the answer is not in a teacher turn after the question", "item": item})
            answer, answer_sec = "", None
        qa.append({"t": writer.fmt_ts(sec), "sec": sec, "question": question, "answer": answer,
                   "answer_t": writer.fmt_ts(answer_sec) if answer_sec is not None else "", "verdict": "unchecked"})
    for item in data.get("unclear") or []:
        sec = _real_stamp(item.get("t"), real) if isinstance(item, dict) else None
        why = one_line(item.get("why")) if isinstance(item, dict) else ""
        if sec is None or not why:
            rejected.append({"reason": "no real line at this timestamp", "item": item})
            continue
        until = parse_ts(item.get("until", ""))
        until = until if until is not None and until >= sec else sec
        if any(a <= until and sec < b for a, b in known):
            rejected.append({"reason": "overlaps a known unclear span", "item": item})
            continue
        if len(unclear) >= UNCLEAR_MAX:
            rejected.append({"reason": "more than 5 unclear spans", "item": item})
            continue
        unclear.append({"t": writer.fmt_ts(sec), "sec": sec, "until": writer.fmt_ts(until), "why": why})
    return {"notes": notes, "qa": qa, "unclear": unclear, "rejected": rejected, "wrong_speaker": wrong_speaker,
            "turns_trusted": trusted, "turns": [{"t": writer.fmt_ts(sec), "sec": sec, "who": who} for sec, who in turns]}


def verify(title: str, lines: list[Line], notes: list, qa: list, hearing: str = "") -> bool:
    """Second opinion on every note and exchange. Applies the verdicts. False when the call failed."""
    items = [{"id": i, "type": "note", "t": n["t"], "text": n["text"]} for i, n in enumerate(notes)]
    items += [{"id": len(notes) + i, "type": "qa", "t": q["t"], "question": q["question"], "answer": q["answer"]}
              for i, q in enumerate(qa)]
    if not items:
        return True
    user = (f"Lesson title: {title}\n\n<lines>\n{render_lines(lines)}\n</lines>\n\n"
            f"<items>\n{json.dumps(items, ensure_ascii=False)}\n</items>" + (f"\n\n{hearing}" if hearing else ""))
    try:
        verdicts = ask_json(verify_system(bool(hearing)), user, purpose="verify", validate=_verdict_shape, max_tokens=4000)
    except llm.NoProviderError:
        raise
    except Exception as e:  # noqa: BLE001  verification is a bonus: everything stays, marked unverified
        log.warning("notes: verification failed (%s)", e)
        return False
    for v in verdicts:
        try:
            i = int(v["id"])
            verdict = v.get("verdict")
            if i < 0:
                continue
            target = notes[i] if i < len(notes) else qa[i - len(notes)]
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            continue
        if verdict in ("supported", "unsupported", "wrong_speaker"):
            target["verdict"] = verdict
    for x in notes + qa:
        if x["verdict"] == "unchecked":
            x["verdict"] = "supported"
    return True


def process_section(index: int, count: int, lines: list[Line], title: str, terms: list[str], previous: str,
                    gloss: glossary_mod.Glossary | None, roman: bool = False, second=()) -> dict:
    hearing = hearing_block(second, lines[0].start, lines[-1].end)
    out = {"index": index, "start": lines[0].start, "end": lines[-1].end, "ok": False, "summary": "",
           "turns": [], "turns_trusted": True, "notes": [], "qa": [], "unclear": [], "unconfirmed": [], "wrong_speaker": [], "rejected": [],
           "verified": False, "error": None}
    try:
        data = ask_json(section_system(roman, bool(hearing)),
                        section_prompt(title, index, count, lines, terms, previous, hearing),
                        purpose="notes", validate=_section_shape, max_tokens=SECTION_MAX_TOKENS)
    except llm.NoProviderError:
        raise
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
        log.warning("notes: section %s of %s failed: %s", index + 1, count, e)
        return out
    out["summary"] = one_line(data.get("summary"))
    checked = check_section(data, lines, gloss)
    notes, qa = checked["notes"], checked["qa"]
    out["turns"] = checked["turns"]
    out["turns_trusted"] = checked["turns_trusted"]
    out["wrong_speaker"].extend(checked["wrong_speaker"])
    out["verified"] = verify(title, lines, notes, qa, hearing)
    for n in list(notes):
        if n["verdict"] == "unsupported":
            out["unconfirmed"].append({"t": n["t"], "sec": n["sec"], "text": n["text"]})
        elif n["verdict"] == "wrong_speaker":
            out["wrong_speaker"].append(n)
        else:
            continue
        notes.remove(n)
    for q in qa:
        if q["verdict"] in ("unsupported", "wrong_speaker"):
            q["answer"], q["answer_t"] = "", ""
    out.update(notes=notes, qa=qa, unclear=checked["unclear"], rejected=checked["rejected"], ok=True)
    return out


# ---- the file --------------------------------------------------------------------------------------------


def final_summary(title: str, sections: list) -> str:
    done = [s for s in sections if s["ok"]]
    points = [n["text"] for s in done for n in s["notes"] if n["kind"] == "point"]
    fallback = " ".join(s["summary"] for s in done if s["summary"]) or "No summary could be generated."
    user = (f"Lesson title: {title}\n\nSection summaries:\n"
            + "\n".join(f"{i + 1}. {s['summary']}" for i, s in enumerate(done))
            + "\n\nKey points:\n" + "\n".join(f"- {p}" for p in points[:80]))
    try:
        text = llm.chat(SUMMARY_SYSTEM, user, max_tokens=2000, purpose="summary")
    except llm.NoProviderError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("notes: final summary failed (%s)", e)
        return fallback
    return one_line(text) or fallback


def build_markdown(title: str, source: str, summary: str, sections: list, spans: list) -> str:
    notes = sorted((n for s in sections for n in s["notes"]), key=lambda n: n["sec"])
    qa = sorted((q for s in sections for q in s["qa"]), key=lambda q: q["sec"])

    def of(kind):
        return [n for n in notes if n["kind"] == kind]

    def item(n, prefix="- ") -> str:
        return f"{prefix}{n['text']} [{n['t']}]"

    def block(heading, rows):
        return [f"## {heading}", "", *rows, ""]

    out = [f"# {one_line(title)} - Notes", "", f"Source transcript: {source}", ""]
    out += block("Summary", [summary])
    out += block("Key points", [item(n) for n in of("point")] or ["- No teaching points were found in this lesson."])
    if of("step"):
        out += block("Step-by-step process", [item(n, f"{i}. ") for i, n in enumerate(of("step"), 1)])
    if of("action"):
        out += block("Action items", [item(n, "- [ ] ") for n in of("action")])
    if of("term"):
        out += block("Terms and tools", [item(n) for n in of("term")])
    if of("tip"):
        out += block("Instructor's tips and warnings", [item(n) for n in of("tip")])
    if qa:
        rows = []
        for q in qa:
            rows.append(f"- **Q [{q['t']}]** {q['question']}")
            rows.append(f"  **A [{q['answer_t']}]** {q['answer']}" if q["answer"] and q["answer_t"]
                        else f"  **A** {q['answer']}" if q["answer"] else "  **A** No clear answer in the recording.")
        out += block("Questions from students", rows)
    rows = [f"- [{u['t']} to {u['until']}] the recording could not be transcribed" for u in sorted(spans, key=lambda u: u["sec"])]
    model = [u for s in sections for u in s["unclear"]]
    model += [{"sec": s["start"], "t": writer.fmt_ts(s["start"]), "until": writer.fmt_ts(s["end"]),
               "why": "notes could not be generated for this part"} for s in sections if not s["ok"]]
    rows += [f"- [{u['t']} to {u['until']}] {u['why']}" for u in sorted(model, key=lambda u: u["sec"])]
    rows += [f"- Unconfirmed: {u['text']} [{u['t']}]"
             for u in sorted((u for s in sections for u in s["unconfirmed"]), key=lambda u: u["sec"])]
    if rows:
        out += block("Unclear in the recording", rows)
    return "\n".join(out).rstrip() + "\n"



def summarize(md: Path) -> Path:
    md = Path(md)
    gloss = glossary_mod.load(md.parent / "glossary.txt")
    lesson = load_lesson(md, gloss)
    title, lines = lesson["title"], lesson["lines"]
    spans = [{"sec": l.start, "t": writer.fmt_ts(l.start), "until": writer.fmt_ts(l.end)} for l in lines if l.unclear]
    speech = [l for l in lines if not l.unclear]
    sections: list[dict] = []
    if speech:
        parts = make_sections(lines, speed=lesson["speed"])
        previous = ""
        for i, part in enumerate(parts):
            if not any(not l.unclear for l in part):
                continue
            result = process_section(i, len(parts), part, title, gloss.terms, previous, gloss,
                                     lesson["roman"], lesson["second"])
            sections.append(result)
            previous = result["summary"] or previous
        failed = [s for s in sections if not s["ok"]]
        if len(failed) * 2 > len(sections):
            raise RuntimeError(f"notes failed for {len(failed)} of {len(sections)} sections: {failed[-1]['error']}")
        summary = final_summary(title, sections)
    else:
        summary = "No speech could be read in this recording."
    out = md.with_name(md.stem + "-notes.md")
    out.write_text(build_markdown(title, md.name, summary, sections, spans), encoding="utf-8")
    record = {"v": 1, "lesson": title, "source": md.name, "summary": summary, "unclear_spans": spans,
              "script": "roman" if lesson["roman"] else "urdu", "second_hearing": bool(lesson["second"]),
              "sections": sections}
    out.with_suffix(".json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


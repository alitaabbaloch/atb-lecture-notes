"""The "for AI" file: a prompt, glossary terms and the transcript(s), for pasting into any chat AI."""
import math
import os
import re
import unicodedata
from pathlib import Path

import writer

PART_SECONDS = 900.0   # about 15 minutes of lesson per part

HOW_TO = ("How to use: copy everything below the line into a chat with any AI (Claude, ChatGPT, Gemini or another). "
          "If the chat says the text is too long, paste one part at a time. Each part starts with \"=== PART\".")

SCRIPT_ROMAN = "It is written in Roman letters with Hindi-style spelling."
SCRIPT_URDU = "It is written in Urdu script, with English terms sometimes spelled phonetically in Urdu letters."

SECOND_BULLET = ('- SECOND HEARING is the same audio heard by a second speech model. Where the two agree, trust them. Where one is garbled and the other is clear, use the clear one. Where they disagree on a name, a number or a word like low or high, and the context does not settle it, do not pick one: list it under "Check in the video".')

PROMPT = """You are given the transcript of one lecture. Write study notes a student will revise from.

About the transcript
- A speech model made it from an Urdu lecture with English technical terms. {script} Some words are wrong and some lines are garbled.
- It has no speaker labels. In question-and-answer sessions students speak too. Students greet, say "sir", ask, describe what they did, and thank. The teacher answers, explains, and calls the next student by name.
- A line "[unclear until mm:ss]" is a stretch that could not be transcribed.
{second}
Write the notes in English, in this order, with these headings:
## Summary
## Key points
## Step-by-step process
## Action items
## Terms and tools
## Tips and warnings
## Questions from students
## Check in the video

Rules
- Summary: 3 to 6 sentences. Leave out a heading that has nothing under it.
- Every bullet ends with the timestamp of the line it rests on, like [12:30].
- Notes hold only what the teacher teaches. A student's statement, guess or proposal is never the teacher's teaching. Put it under "Questions from students" as the question.
- An answer holds only what the teacher replies. Start it with Yes or No only when the teacher says so. If the teacher puts the matter off, say that. If the teacher does not answer, or the reply cannot be understood, write "No clear answer".
- Skip greetings, thanks, prayers, sound checks, personal stories and class admin.
- Say only what the transcript says. No outside knowledge. Never invent a name, a number, a link or a tool.
- Keep numbers exactly. Keep words like low and high exactly.
- Spell English terms correctly when you are sure which term is meant. The glossary lists the likely ones. If you cannot tell which term a garbled word is, write it as it sounds followed by (?).
- Anything you are not sure about goes under "Check in the video" with its timestamp, not into the notes.
- If the lecture arrives in several parts, write the notes for each part as it arrives, then wait for the next part."""


def has_arabic(text: str) -> bool:
    return any("ARABIC" in unicodedata.name(ch, "") for ch in text if ch.isalpha())


def script_sentence(result: dict) -> str:
    lines = [s["text"] for s in result.get("segments", []) if any(ch.isalpha() for ch in s.get("text") or "")]
    arabic = sum(1 for t in lines if has_arabic(t))
    return SCRIPT_ROMAN if (len(lines) - arabic) * 2 > len(lines) or not lines else SCRIPT_URDU


def prompt_text(result: dict, second: dict | None) -> str:
    return (PROMPT.replace("{script}", script_sentence(result))
            .replace("{second}", (SECOND_BULLET + "\n") if second else ""))


def one_line(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def text_entries(result: dict, issues: bool) -> list[tuple[float, int, str]]:
    """(start, order, line) for every segment with text; unclear issues sort before a text line at the same start."""
    out = []
    for seg in result.get("segments", []):
        text = one_line(seg.get("text"))
        if text:
            mark = "(second hearing) " if seg.get("source") == "second" else ""
            out.append((seg["start"], 1, f"[{writer.fmt_ts(seg['start'])}] {mark}{text}"))
    if issues:
        for i in result.get("issues", []):
            if i.get("kind") == "unclear":
                out.append((i["start"], 0, f"[{writer.fmt_ts(i['start'])}] [unclear until {writer.fmt_ts(i['end'])}]"))
    return sorted(out, key=lambda e: (e[0], e[1]))


def duration_of(result: dict) -> float:
    if result.get("duration"):
        return float(result["duration"])
    segments = result.get("segments", [])
    return float(segments[-1]["end"]) if segments else 0.0


def boundaries(entries: list, duration: float, n: int) -> list[float]:
    """Start times of parts 2..n: the first transcript line at or after each multiple of duration / n."""
    starts = [e[0] for e in entries if e[1] == 1]
    out = []
    for k in range(1, n):
        mark = k * duration / n
        out.append(next((s for s in starts if s >= mark), mark))
    return out


def in_range(entries: list, low: float, high: float) -> list[str]:
    return [line for start, _, line in entries if low <= start < high]


def part_text(index: int, total: int, low: float, high: float, shown_end: float,
              main: list, second: list) -> str:
    head = f"=== PART {index + 1} of {total}: {writer.fmt_ts(0 if index == 0 else low)} to {writer.fmt_ts(shown_end)} ==="
    block = [head, "", "TRANSCRIPT"] + in_range(main, low, high)
    lines = in_range(second, low, high)
    if lines:
        block += ["", "SECOND HEARING"] + lines
    return "\n".join(block)


def build(title: str, result: dict, second: dict | None = None, terms=()) -> str:
    """The whole text of the file."""
    duration = duration_of(result)
    n = max(1, math.ceil(duration / PART_SECONDS))
    main = text_entries(result, issues=True)
    other = text_entries(second, issues=False) if second else []
    cuts = [float("-inf")] + boundaries(main, duration, n)
    highs = cuts[1:] + [float("inf")]
    ends = cuts[1:] + [duration]
    parts = [part_text(i, n, cuts[i], highs[i], ends[i], main, other) for i in range(n)]
    terms = [one_line(t) for t in terms if one_line(t)]
    top = [f"# {one_line(title)}: transcript for an AI chat", "", HOW_TO, "", "---", "", prompt_text(result, second), ""]
    if terms:
        top += ["Glossary (spell these terms exactly like this): " + ", ".join(terms), ""]
    return "\n".join(top) + "\n" + "\n\n".join(parts) + "\n"


def title_of(md_path: Path) -> str:
    if md_path.exists():
        for line in md_path.read_text(encoding="utf-8").splitlines()[:40]:
            if line.startswith("lesson:") and one_line(line[7:]):
                return one_line(line[7:])
    return re.sub(r"^\d+-", "", md_path.stem)


def write(md_path, result: dict, second: dict | None = None, terms=()) -> Path:
    """Write <NN>-<lesson>-for-ai.md next to the transcript md_path and return its path."""
    md_path = Path(md_path)
    out = md_path.with_name(md_path.stem + "-for-ai.md")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(build(title_of(md_path), result, second, terms), encoding="utf-8")
    os.replace(tmp, out)
    return out

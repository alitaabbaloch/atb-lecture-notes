"""Transcript files: notes/<course-slug>/<NN>-<lesson-slug>.md and .json."""
import json
import os
import re
from pathlib import Path


def slug(text: str, limit: int = 80) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:limit].strip("-") or "untitled"


def fmt_ts(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_total(seconds: float) -> str:
    """mm:ss with minutes that may pass 59."""
    seconds = int(round(seconds or 0))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def next_index(course_dir: Path) -> int:
    nums = []
    for p in course_dir.glob("*.md"):
        m = re.match(r"(\d+)-", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def one_line(value) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()


def _mark(seg: dict) -> str:
    return "(second hearing) " if seg.get("source") == "second" else ""


def write_atomic(path: Path, text: str) -> None:
    """Write through a temporary file in the same folder, so a reader never sees a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def dump(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=1, default=str)


def write_second(md_path: Path, second: dict) -> Path:
    """The full second-hearing result, next to the transcript: <NN>-<lesson>.second.json."""
    out = Path(md_path).with_suffix(".second.json")
    write_atomic(out, dump(second))
    return out


def write_transcript(data_dir: Path, course: str, lesson: str, result: dict, meta: dict,
                     existing_md: str | None = None) -> tuple[str, str, int]:
    """Write the transcript and the raw engine result. Returns (md path, json path, NN), paths relative to data_dir.

    meta keys: url, recorded, duration, model, language, audio, speed, engine.
    If existing_md is given (a re-run of the same lesson) the same file names are reused.
    """
    if existing_md and (data_dir / existing_md).parent.exists():
        md_path = data_dir / existing_md
        number = int(re.match(r"(\d+)-", md_path.name).group(1))
    else:
        course_dir = data_dir / "notes" / slug(course)
        course_dir.mkdir(parents=True, exist_ok=True)
        number = next_index(course_dir)
        md_path = course_dir / f"{number:02d}-{slug(lesson)}.md"
    json_path = md_path.with_suffix(".json")
    write_atomic(json_path, dump(result))

    segments = result.get("segments", [])
    duration = meta.get("duration") or (segments[-1]["end"] if segments else 0)
    lines = [
        "---",
        f"course: {one_line(course)}",
        f"lesson: {one_line(lesson)}",
        f"url: {one_line(meta.get('url'))}",
        f"recorded: {one_line(meta.get('recorded'))}",
        f"duration: {fmt_ts(duration)}",
        f"model: {one_line(meta.get('model'))}",
        f"language: {one_line(meta.get('language'))}",
    ]
    if result.get("script"):  # roman | urdu | mixed, set by the Roman Urdu step
        lines.append(f"script: {one_line(result['script'])}")
    lines += [
        f"audio: {one_line(meta.get('audio'))}",
        f"speed: {one_line(meta.get('speed'))}",
        f"engine: {one_line(meta.get('engine'))}",
        "pipeline: 2",
    ]
    if "speech_seconds" in result:  # a v2 result
        lines.append(f"speech: {fmt_ts(result['speech_seconds'])}")
        unclear = sum(max(0.0, i["end"] - i["start"]) for i in result.get("issues", []) if i.get("kind") == "unclear")
        lines.append(f"unclear: {fmt_total(unclear)}")
        if result.get("tempo") and result["tempo"] != 1.0:
            lines.append(f"tempo: {result['tempo']}")
    lines += [
        "---",
        "",
        f"# {one_line(lesson)}",
        "",
        "## Transcript",
        "",
    ]
    entries = [(seg["start"], 1, f"**[{fmt_ts(seg['start'])}]** {_mark(seg)}{seg['text'].strip()}")
               for seg in segments if seg["text"].strip()]
    entries += [(i["start"], 0, f"**[{fmt_ts(i['start'])}]** [unclear until {fmt_ts(i['end'])}]")
                for i in result.get("issues", []) if i.get("kind") == "unclear"]
    for _, _, line in sorted(entries, key=lambda e: (e[0], e[1])):
        lines.append(line)
        lines.append("")
    main = [seg["text"].strip() for seg in segments if seg["text"].strip() and seg.get("source") != "second"]
    plain = " ".join(main) or (result.get("text", "").strip() if not segments else "")
    lines += ["## Plain text", "", plain, ""]
    write_atomic(md_path, "\n".join(lines))
    return str(md_path.relative_to(data_dir)), str(json_path.relative_to(data_dir)), number

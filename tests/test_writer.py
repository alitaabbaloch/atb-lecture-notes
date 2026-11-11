import json

import summarize
import writer


def test_slug():
    assert writer.slug("Hello, World! Lesson #2") == "Hello-World-Lesson-2"
    assert writer.slug("  ") == "untitled"
    assert writer.slug("a" * 200) == "a" * 80
    assert writer.slug("درس نمبر ایک") == "درس-نمبر-ایک"


def test_fmt_ts():
    assert writer.fmt_ts(65) == "01:05"
    assert writer.fmt_ts(3725) == "01:02:05"


def test_next_index(tmp_path):
    assert writer.next_index(tmp_path) == 1
    (tmp_path / "01-a.md").write_text("x")
    (tmp_path / "07-b-notes.md").write_text("x")
    (tmp_path / "notes.md").write_text("x")
    assert writer.next_index(tmp_path) == 8


RESULT = {"text": "alpha beta", "language": "en", "segments": [
    {"start": 0, "end": 5, "text": " UNIQUE-LINE-ONE"}, {"start": 65, "end": 70, "text": " second"}]}
META = {"url": "http://example.invalid/x", "recorded": "2024-01-01 10:00", "duration": 70.0, "model": "m",
        "language": "en", "audio": "recordings/c/a.webm", "speed": 1.5, "engine": "fake"}


def test_frontmatter_order_and_body(tmp_path):
    md, js, n = writer.write_transcript(tmp_path, "My Course", "Lesson\nOne", RESULT, META)
    assert (md, js, n) == ("notes/My-Course/01-Lesson-One.md", "notes/My-Course/01-Lesson-One.json", 1)
    lines = (tmp_path / md).read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    keys = [l.split(":")[0] for l in lines[1:end]]
    assert keys == ["course", "lesson", "url", "recorded", "duration", "model", "language", "audio", "speed",
                    "engine", "pipeline"]
    assert "lesson: Lesson One" in lines and "pipeline: 2" in lines and "duration: 01:10" in lines
    body = "\n".join(lines[end:])
    assert body.index("# Lesson One") < body.index("## Transcript") < body.index("## Plain text")
    assert "**[00:00]** UNIQUE-LINE-ONE" in body and "**[01:05]** second" in body
    assert json.loads((tmp_path / js).read_text())["language"] == "en"
    md2, _, n2 = writer.write_transcript(tmp_path, "My Course", "Other", RESULT, META)
    assert n2 == 2 and md2.startswith("notes/My-Course/02-")


def test_rerun_reuses_the_same_files(tmp_path):
    md, _, n = writer.write_transcript(tmp_path, "C", "L", RESULT, META)
    again = writer.write_transcript(tmp_path, "C", "L", RESULT, META, existing_md=md)
    assert again[0] == md and again[2] == n
    assert len(list((tmp_path / "notes" / "C").glob("*.md"))) == 1


def test_notes_prompt_contains_transcript_once(tmp_path):
    """Regression: the old code sent the transcript and then the plain text copy of it."""
    result = {"text": "UNIQUE-LINE-ONE second", "language": "en", "segments": RESULT["segments"]}
    md, _, _ = writer.write_transcript(tmp_path, "C", "L", result, META)
    lesson = summarize.load_lesson(tmp_path / md)
    prompt = summarize.section_prompt(lesson["title"], 0, 1, lesson["lines"], [], "")
    first = RESULT["segments"][0]["text"]
    assert prompt.count(first) == 1
    assert "## Plain text" not in prompt and "## Transcript" not in prompt and "pipeline: 2" not in prompt
    assert prompt.count("<lines>") == 1


def test_fake_notes_make_no_network_call(tmp_path, monkeypatch):
    monkeypatch.setenv("ATB_NOTES", "fake")
    md, _, _ = writer.write_transcript(tmp_path, "C", "L", RESULT, META)
    out = summarize.summarize(tmp_path / md)
    text = out.read_text()
    assert out.name == "01-L-notes.md" and "## Summary" in text and "fake notes engine" in text


def test_missing_key_raises_clear_error(monkeypatch):
    import llm
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "NOTES_BASE_URL", "ATB_NOTES"):
        monkeypatch.delenv(name, raising=False)
    llm.reset()
    try:
        llm.chat("s", "u")
    except RuntimeError as e:
        assert "no notes AI key" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

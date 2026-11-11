import for_ai

SEGS = [{"start": 2, "end": 5, "text": "pehla jumla"}, {"start": 5, "end": 9, "text": "doosra jumla"}]
RESULT = {"segments": SEGS, "issues": [], "duration": 120}
SECOND = {"segments": [{"start": 2, "end": 6, "text": "second view line"}]}
BULLET = "- SECOND HEARING is the same audio"
ROMAN = "It is written in Roman letters with Hindi-style spelling."
URDU = "It is written in Urdu script"


def test_plain_roman_file():
    text = for_ai.build("Lesson One", RESULT)
    assert text.startswith("# Lesson One: transcript for an AI chat\n")
    assert "How to use: copy everything below the line" in text
    assert ROMAN in text and URDU not in text
    assert "Glossary" not in text and "SECOND HEARING" not in text
    assert text.count("=== PART 1 of 1: 00:00 to 02:00 ===") == 1 and text.count("=== PART") == 2  # how-to names it too
    assert "TRANSCRIPT\n[00:02] pehla jumla\n[00:05] doosra jumla\n" in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_urdu_script_sentence():
    result = {"segments": [{"start": 0, "end": 3, "text": "یہ پہلا جملہ ہے"}, {"start": 3, "end": 6, "text": "دوسرا جملہ"}]}
    text = for_ai.build("T", result)
    assert URDU in text and ROMAN not in text


def test_second_hearing_present_and_absent():
    with_second = for_ai.build("T", RESULT, SECOND)
    assert BULLET in with_second
    assert "\nSECOND HEARING\n[00:02] second view line\n" in with_second
    without = for_ai.build("T", RESULT)
    assert BULLET not in without and "\nSECOND HEARING\n" not in without
    assert "{" not in without and "}" not in without and "{" not in with_second and "}" not in with_second


def test_glossary_line_in_order():
    text = for_ai.build("T", RESULT, terms=["Zeta tool", "alpha term", "Mid"])
    assert "Glossary (spell these terms exactly like this): Zeta tool, alpha term, Mid\n" in text


def test_unclear_issue_placement():
    result = {"segments": [{"start": 70, "end": 80, "text": "after gap"}, {"start": 2, "end": 5, "text": "before"}],
              "issues": [{"kind": "unclear", "start": 70, "end": 84}, {"kind": "loop", "start": 90, "end": 99}],
              "duration": 120}
    text = for_ai.build("T", result)
    assert "[00:02] before\n[01:10] [unclear until 01:24]\n[01:10] after gap\n" in text
    assert "[01:30]" not in text


def test_second_source_segment_is_marked():
    result = {"segments": [{"start": 24, "end": 30, "text": "filled in", "source": "second"}], "duration": 60}
    assert "[00:24] (second hearing) filled in\n" in for_ai.build("T", result)


def test_forty_minutes_three_parts():
    segs = [{"start": t, "end": t + 5, "text": f"line {t}"} for t in range(0, 2400, 20)]
    second = {"segments": [{"start": t, "end": t + 5, "text": f"other {t}"} for t in (10, 810, 830, 1700)]}
    text = for_ai.build("T", {"segments": segs, "duration": 2400}, second)
    for k in (1, 2, 3):
        assert text.count(f"=== PART {k} of 3:") == 1
    assert "=== PART 1 of 3: 00:00 to 13:20 ===" in text
    assert "=== PART 2 of 3: 13:20 to 26:40 ===" in text
    assert "=== PART 3 of 3: 26:40 to 40:00 ===" in text
    for seg in segs:
        assert text.count(f"] line {seg['start']}\n") == 1
    parts = text.split("\n=== PART ")[1:]
    assert len(parts) == 3
    assert "other 10\n" in parts[0] and "other 810\n" not in parts[0]
    assert "other 810\n" in parts[1] and "other 830\n" in parts[1] and "other 1700\n" not in parts[1]
    assert "other 1700\n" in parts[2] and "other 10\n" not in parts[2]


def test_whitespace_collapsed():
    result = {"segments": [{"start": 1, "end": 4, "text": "  one\ntwo   three\t four "}], "duration": 10}
    assert "[00:01] one two three four\n" in for_ai.build("T", result)


def test_write_names_and_titles(tmp_path):
    md = tmp_path / "03-some-lesson.md"
    md.write_text("---\ncourse: C\nlesson: Real Title\n---\n\n# Real Title\n", encoding="utf-8")
    out = for_ai.write(md, RESULT)
    assert out == tmp_path / "03-some-lesson-for-ai.md"
    assert out.read_text(encoding="utf-8").startswith("# Real Title: transcript for an AI chat\n")
    bare = tmp_path / "04-no-front-matter.md"
    out2 = for_ai.write(bare, RESULT)
    assert out2.name == "04-no-front-matter-for-ai.md"
    assert out2.read_text(encoding="utf-8").startswith("# no-front-matter: transcript for an AI chat\n")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "03-some-lesson-for-ai.md", "03-some-lesson.md", "04-no-front-matter-for-ai.md"]


def test_empty_transcript():
    text = for_ai.build("T", {"segments": [], "issues": []})
    assert text.count("=== PART 1 of 1: 00:00 to 00:00 ===") == 1
    assert text.endswith("TRANSCRIPT\n") and "[00:" not in text.split("=== PART 1")[1]

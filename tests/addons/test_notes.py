import json
import subprocess
import sys
from pathlib import Path

import pytest

import llm
import summarize
import writer
from conftest import wait_job

ROOT = Path(__file__).resolve().parent.parent.parent
META = {"url": "u", "recorded": "r", "duration": 0, "model": "m", "language": "ur", "audio": "a", "speed": 1, "engine": "fake"}


def make_lesson(tmp_path, texts, *, step=20.0, issues=None, v2=True, course="C", lesson="Lesson", blocks=None):
    """A transcript .md and .json written by the real writer from invented lines."""
    segments = [{"id": i, "start": i * step, "end": i * step + step - 2, "text": t} for i, t in enumerate(texts)]
    result = {"text": " ".join(texts), "segments": segments, "language": "ur"}
    if v2:
        result.update({"v": 2, "speech_seconds": len(texts) * step, "issues": issues or [], "blocks": blocks or [],
                       "duration": len(texts) * step})
    md, js, _ = writer.write_transcript(tmp_path, course, lesson, result, META)
    return tmp_path / md


def texts(n, prefix="Line"):
    return [f"{prefix} {i} of the invented lecture about topic {i}" for i in range(n)]


@pytest.fixture(autouse=True)
def fake_notes(monkeypatch):
    monkeypatch.setenv("ATB_NOTES", "fake")
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    llm.reset()


class Script:
    """Replaces llm.chat: records every call and answers per purpose with what the test provides."""

    def __init__(self, monkeypatch, section=None, verify=None, summary="Scripted summary."):
        self.calls, self.section, self.verify, self.summary = [], section, verify, summary
        self.systems = []
        monkeypatch.setattr(llm, "chat", self.chat)

    def chat(self, system, user, *, max_tokens=8000, purpose="notes"):
        self.calls.append((purpose, user))
        self.systems.append((purpose, system))
        if purpose == "notes":
            answer = self.section(user) if callable(self.section) else self.section
        elif purpose == "verify":
            answer = self.verify(user) if callable(self.verify) else (self.verify or "[]")
        else:
            answer = self.summary if purpose == "summary" else "[]"
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, str) else json.dumps(answer)

    def of(self, purpose):
        return [u for p, u in self.calls if p == purpose]

    def systems_of(self, purpose):
        return [x for p, x in self.systems if p == purpose]


def section_json(notes=(), qa=(), unclear=(), summary="Two sentences. Here.", turns=None):
    data = {"summary": summary, "notes": list(notes), "qa": list(qa), "unclear": list(unclear)}
    if turns is not None:
        data["turns"] = turns
    return data


def turn(t, who):
    return {"t": t, "who": who}


def note(t, text, kind="point"):
    return {"t": t, "kind": kind, "text": text}


def qa_item(t, question, answer_t="", answer=""):
    return {"t": t, "question": question, "answer_t": answer_t, "answer": answer}


def lines_of(texts_, step=20.0):
    return [summarize.Line(i * step, i * step + step - 2, t) for i, t in enumerate(texts_)]


# ---- sections ----------------------------------------------------------------------------------------------

def test_short_lesson_is_one_section():
    assert len(summarize.make_sections(lines_of(texts(40)))) == 1  # 40 lines of 18 s = 12 minutes


def test_long_lesson_is_cut_into_about_ten_minute_sections():
    lines = lines_of(texts(120))  # 36 minutes of speech
    sections = summarize.make_sections(lines)
    assert len(sections) == 4 and [l for s in sections for l in s] == lines
    for s in sections:
        assert 420 <= sum(l.speech for l in s) <= 780


def test_sections_are_cut_at_the_longest_pause_near_the_goal():
    lines = lines_of(texts(120))
    for l in lines[58:]:  # a 12 s pause before line 58, near the middle of a two-section split
        l.start += 12
        l.end += 12
    sections = summarize.make_sections(lines, target=1100.0)
    assert len(sections) == 2 and sections[1][0] is lines[58]


def test_sections_prefer_block_boundaries():
    lines = lines_of(texts(120))
    for l in lines:
        l.block = 0 if l.start < 1160 else 1
    sections = summarize.make_sections(lines, target=1100.0)
    assert sections[1][0].start == 1160 and sections[0][-1].block == 0 and sections[1][0].block == 1


def test_a_recording_played_at_double_speed_gets_sections_half_as_long():
    lines = lines_of(texts(120))  # 36 minutes of recording hold 72 minutes of speech at 2x
    sections = summarize.make_sections(lines, speed=2.0)
    assert len(sections) == 7 and [l for s in sections for l in s] == lines
    for s in sections:
        assert 210 <= sum(l.speech for l in s) <= 390


def test_a_short_fast_recording_is_cut_where_a_normal_one_is_not():
    lines = lines_of(texts(40))  # 12 minutes of recording
    assert len(summarize.make_sections(lines)) == 1
    assert len(summarize.make_sections(lines, speed=2.0)) == 2  # 24 minutes of speech


def test_the_playback_speed_comes_from_the_transcript_record():
    assert summarize.playback_speed({"speed": 2.0}) == 2.0
    assert summarize.playback_speed({"speed": "1.5"}) == 1.5
    for odd in ({}, {"speed": None}, {"speed": 0}, {"speed": "fast"}, {"speed": [2]}, {"speed": 9}, {"speed": -2}):
        assert summarize.playback_speed(odd) == 1.0


def test_a_fast_recording_gets_more_sections_in_the_notes(tmp_path):
    md = make_lesson(tmp_path, texts(60))  # 20 minutes of recording
    assert summarize.load_lesson(md)["speed"] == 1.0
    assert len(json.loads(summarize.summarize(md).with_suffix(".json").read_text(encoding="utf-8"))["sections"]) == 2
    record = md.with_suffix(".json")
    data = json.loads(record.read_text(encoding="utf-8"))
    data["speed"] = 2.0  # the pipeline writes the speed the lesson was played at
    record.write_text(json.dumps(data), encoding="utf-8")
    assert summarize.load_lesson(md)["speed"] == 2.0
    assert len(json.loads(summarize.summarize(md).with_suffix(".json").read_text(encoding="utf-8"))["sections"]) == 4


# ---- the file ----------------------------------------------------------------------------------------------

def read(out):
    return out.read_text(encoding="utf-8")


def saved(out):
    return json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))


def test_fake_notes_keep_timestamps_and_layout(tmp_path):
    out = summarize.summarize(make_lesson(tmp_path, texts(9)))
    text = read(out)
    assert text.startswith("# Lesson - Notes\n\nSource transcript: 01-Lesson.md\n\n## Summary\n")
    assert text.index("## Summary") < text.index("## Key points")
    for heading in ("Step-by-step process", "Action items", "Terms and tools", "Instructor's tips and warnings",
                    "Questions from students", "Unclear in the recording"):
        assert f"## {heading}" not in text  # empty sections are left out
    for stamp in ("[00:00]", "[01:00]", "[02:00]"):  # the fake cites lines 0, 3, 6
        assert stamp in text
    assert saved(out)["sections"][0]["verified"] is True and len(saved(out)["sections"][0]["notes"]) == 3


def test_bad_timestamps_are_dropped_and_logged(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json([note("00:20", "Real note"), note("09:59", "Floating note"),
                                              note("nonsense", "Bad stamp")]))
    out = summarize.summarize(md)
    text = read(out)
    assert "Real note [00:20]" in text and "Floating note" not in text and "Bad stamp" not in text
    assert [r["item"]["text"] for r in saved(out)["sections"][0]["rejected"]] == ["Floating note", "Bad stamp"]


def test_a_note_within_twenty_seconds_of_a_line_is_kept(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))  # lines at 0, 20, 40
    Script(monkeypatch, section=section_json([note("00:55", "Near the last line"), note("01:01", "Too far")]))
    text = read(summarize.summarize(md))
    assert "Near the last line [00:55]" in text and "Too far" not in text


def test_exchange_renders_as_q_and_a_lines(tmp_path):
    t = texts(9)
    t[3] = "the student asks what a keyword is"
    t[4] = "a keyword is what people type"
    text = read(summarize.summarize(make_lesson(tmp_path, t)))
    students = text.split("## Questions from students")[1]
    assert "- **Q [01:00]** the student asks what a keyword is\n  **A [01:20]** a keyword is what people type" in students
    assert "the student asks" not in text.split("## Questions from students")[0]


def test_exchange_without_a_next_line_has_no_answer(tmp_path):
    t = texts(3)
    t[2] = "the student asks last"
    text = read(summarize.summarize(make_lesson(tmp_path, t)))
    assert "- **Q [00:40]** the student asks last\n  **A** No clear answer in the recording." in text


def test_invented_answer_becomes_no_clear_answer(tmp_path):
    t = texts(9)
    t[3] = "the student asks something"
    t[4] = "INVENTED reply"
    out = summarize.summarize(make_lesson(tmp_path, t))
    text = read(out)
    assert "**Q [01:00]** the student asks something\n  **A** No clear answer in the recording." in text
    assert "INVENTED" not in text
    assert saved(out)["sections"][0]["qa"][0]["answer"] == ""


def test_invented_note_lands_under_unclear_as_unconfirmed(tmp_path):
    t = texts(9)
    t[3] = "Line 3 INVENTED claim"
    text = read(summarize.summarize(make_lesson(tmp_path, t)))
    head, unclear = text.split("## Unclear in the recording")
    assert "- Unconfirmed: Line 3 INVENTED claim [01:00]" in unclear and "INVENTED" not in head


def test_wrongspeaker_note_is_removed_and_kept_in_the_json(tmp_path):
    t = texts(9)
    t[3] = "Line 3 WRONGSPEAKER guess"
    out = summarize.summarize(make_lesson(tmp_path, t))
    assert "WRONGSPEAKER" not in read(out)
    assert [n["text"] for n in saved(out)["sections"][0]["wrong_speaker"]] == ["Line 3 WRONGSPEAKER guess"]


# ---- speaker turns -----------------------------------------------------------------------------------------

TURNS = [turn("00:00", "teacher"), turn("00:40", "student"), turn("01:20", "teacher")]  # lines every 20 s


def test_a_note_in_a_student_turn_is_removed_and_kept_under_wrong_speaker(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json([note("00:20", "Teacher point"), note("00:40", "Student proposal")], turns=TURNS))
    out = summarize.summarize(md)
    text = read(out)
    assert "Teacher point [00:20]" in text and "Student proposal" not in text
    wrong = saved(out)["sections"][0]["wrong_speaker"]
    assert [(n["text"], n["verdict"]) for n in wrong] == [("Student proposal", "wrong_speaker")]


def test_turns_are_not_trusted_when_they_would_remove_most_notes(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    script = Script(monkeypatch, section=section_json(
        [note("00:20", "Teacher point"), note("00:40", "Labelled student one"), note("00:50", "Labelled student two")], turns=TURNS))
    out = summarize.summarize(md)
    text = read(out)
    assert all(x in text for x in ("Teacher point", "Labelled student one", "Labelled student two"))
    section = saved(out)["sections"][0]
    assert section["turns_trusted"] is False and section["wrong_speaker"] == []
    items = script.of("verify")[0]
    assert all(x in items for x in ("Teacher point", "Labelled student one", "Labelled student two"))


def test_a_tie_between_removed_and_kept_notes_still_removes(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json([note("00:20", "Teacher point"), note("00:40", "Student proposal")], turns=TURNS))
    section = saved(summarize.summarize(md))["sections"][0]
    assert section["turns_trusted"] is True
    assert [n["text"] for n in section["wrong_speaker"]] == ["Student proposal"]


def test_a_note_removed_for_its_speaker_is_not_sent_to_verify(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    script = Script(monkeypatch, section=section_json([note("00:20", "Teacher point"), note("00:40", "Student proposal")], turns=TURNS))
    summarize.summarize(md)
    items = script.of("verify")[0]
    assert "Teacher point" in items and "Student proposal" not in items


def test_an_answer_in_a_student_turn_becomes_no_clear_answer(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json(
        qa=[qa_item("00:40", "What is a niche?", "01:00", "A niche is a topic.")],
        turns=[turn("00:00", "teacher"), turn("00:40", "student")]))
    out = summarize.summarize(md)
    assert "**Q [00:40]** What is a niche?\n  **A** No clear answer in the recording." in read(out)
    rejected = saved(out)["sections"][0]["rejected"]
    assert [r["reason"] for r in rejected] == ["the answer is not in a teacher turn after the question"]


def test_an_answer_before_its_question_becomes_no_clear_answer(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json(
        qa=[qa_item("01:00", "What is a niche?", "00:20", "A niche is a topic.")],
        turns=[turn("00:00", "teacher"), turn("00:40", "student"), turn("01:20", "teacher")]))
    out = summarize.summarize(md)
    assert "**Q [01:00]** What is a niche?\n  **A** No clear answer in the recording." in read(out)
    assert "A niche is a topic." not in read(out)


def test_a_question_in_a_teacher_turn_is_dropped(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json(
        qa=[qa_item("00:20", "Teacher asks himself?", "00:40", "He answers."),
            qa_item("00:40", "Student asks?", "01:20", "Teacher replies.")], turns=TURNS))
    out = summarize.summarize(md)
    text = read(out)
    assert "Teacher asks himself?" not in text and "**Q [00:40]** Student asks?\n  **A [01:20]** Teacher replies." in text
    rejected = saved(out)["sections"][0]["rejected"]
    assert [r["reason"] for r in rejected] == ["the question is in a teacher turn"]


def test_a_reply_without_turns_behaves_as_before(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json([note("00:20", "Plain note")], qa=[qa_item("00:40", "Plain question?", "01:00", "Plain answer.")]))
    out = summarize.summarize(md)
    text = read(out)
    assert "Plain note [00:20]" in text and "**Q [00:40]** Plain question?\n  **A [01:00]** Plain answer." in text
    assert saved(out)["sections"][0]["turns"] == [] and saved(out)["sections"][0]["wrong_speaker"] == []


def test_bad_turn_entries_are_ignored_and_a_non_list_is_a_wrong_shape(tmp_path, monkeypatch):
    lines = lines_of(texts(6))
    data = {"turns": [turn("00:00", " Teacher "), turn("nonsense", "student"), turn("09:59", "student"),
                      turn("00:40", "principal"), "text", turn("00:20", "STUDENT")]}
    assert summarize.parse_turns(data, lines) == [(0.0, "teacher"), (20.0, "student")]
    assert summarize.parse_turns({}, lines) == [] and summarize.parse_turns({"turns": [turn("x", "student")]}, lines) == []
    md = make_lesson(tmp_path, texts(3))
    answers = iter([json.dumps({**section_json(), "turns": "x"}), json.dumps(section_json([note("00:00", "Second try")]))])
    script = Script(monkeypatch, section=lambda user: next(answers))
    assert "Second try [00:00]" in read(summarize.summarize(md))
    assert len(script.of("notes")) == 2


def test_the_section_record_carries_the_turns(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    Script(monkeypatch, section=section_json(turns=[turn("01:20", "teacher"), turn("00:00", "student")]))
    assert saved(summarize.summarize(md))["sections"][0]["turns"] == [
        {"t": "00:00", "sec": 0.0, "who": "student"}, {"t": "01:20", "sec": 80.0, "who": "teacher"}]


def test_teacher_near_allows_a_few_seconds_of_slack():
    turns = [(0.0, "student"), (22.0, "teacher")]
    assert summarize.teacher_near(turns, 30.0)       # inside a teacher turn
    assert summarize.teacher_near(turns, 20.0)       # student turn, teacher starts 2 s later
    assert not summarize.teacher_near(turns, 18.0)   # teacher starts 4 s later
    assert summarize.teacher_near([], 5.0)           # no turns


def _slack_lines():
    return [summarize.Line(float(i * 2), i * 2 + 1.5, f"Invented line {i}") for i in range(30)]  # a line every 2 s


def test_a_student_turn_note_just_before_a_teacher_turn_is_kept():
    lines = _slack_lines()
    turns = [turn("00:00", "teacher"), turn("00:10", "student"), turn("00:30", "teacher")]
    data = {"turns": turns, "notes": [note("00:28", "Close to the teacher"), note("00:26", "Too far from the teacher")]}
    checked = summarize.check_section(data, lines, None)
    assert [n["text"] for n in checked["notes"]] == ["Close to the teacher"]
    assert [n["text"] for n in checked["wrong_speaker"]] == ["Too far from the teacher"]


def test_an_answer_in_a_student_turn_just_before_a_teacher_turn_is_kept():
    lines = _slack_lines()
    turns = [turn("00:00", "teacher"), turn("00:10", "student"), turn("00:30", "teacher")]
    data = {"turns": turns, "qa": [qa_item("00:12", "Why?", "00:28", "Because."), qa_item("00:12", "Why again?", "00:24", "Because.")]}
    qa = summarize.check_section(data, lines, None)["qa"]
    assert [(q["answer"], q["answer_t"]) for q in qa] == [("Because.", "00:28"), ("", "")]


def test_who_at_finds_the_speaker_for_a_time():
    turns = [(20.0, "student"), (60.0, "teacher")]
    assert summarize.who_at([], 30.0) is None
    assert summarize.who_at(turns, 5.0) == "student"    # before the first turn
    assert summarize.who_at(turns, 60.0) == "teacher"   # exactly at a turn
    assert summarize.who_at(turns, 59.6) == "teacher"   # within the half-second margin
    assert summarize.who_at(turns, 40.0) == "student"   # between turns
    assert summarize.who_at(turns, 500.0) == "teacher"  # after the last turn


def test_more_than_five_unclear_entries_are_cut_to_five(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(10))
    Script(monkeypatch, section=section_json(unclear=[
        {"t": f"{i * 20 // 60:02d}:{i * 20 % 60:02d}", "until": "03:00", "why": f"garbled {i}"} for i in range(8)]))
    out = summarize.summarize(md)
    text = read(out)
    assert text.count("garbled") == 5 and "garbled 4" in text and "garbled 5" not in text
    assert "[00:00 to 03:00] garbled 0" in text
    assert len(saved(out)["sections"][0]["rejected"]) == 3


def test_unclear_entry_on_a_known_gap_or_unknown_time_is_dropped(tmp_path, monkeypatch):
    issues = [{"kind": "unclear", "start": 40.0, "end": 100.0, "detail": "loop"}]
    md = make_lesson(tmp_path, texts(6), issues=issues)
    Script(monkeypatch, section=section_json(unclear=[
        {"t": "00:40", "until": "01:00", "why": "dup of known gap"}, {"t": "09:00", "until": "09:10", "why": "nowhere"},
        {"t": "01:40", "until": "01:50", "why": "fresh"}]))
    text = read(summarize.summarize(md))
    assert "dup of known gap" not in text and "nowhere" not in text and "[01:40 to 01:50] fresh" in text


def test_transcript_unclear_issue_appears_in_the_unclear_section(tmp_path):
    issues = [{"kind": "unclear", "start": 40.0, "end": 100.0, "detail": "loop"}]
    md = make_lesson(tmp_path, texts(3), issues=issues)
    assert "[unclear until 01:40]" in read(md)
    text = read(summarize.summarize(md))
    assert "- [00:40 to 01:40] the recording could not be transcribed" in text.split("## Unclear in the recording")[1]


def test_no_check_number_text_anywhere(tmp_path, monkeypatch):
    t = texts(3)
    t[1] = "the limit is 300 searches"
    md = make_lesson(tmp_path, t)
    Script(monkeypatch, section=section_json([note("00:20", "The limit is 300 searches"),
                                              note("00:20", "The limit is 500 searches")]))
    out = summarize.summarize(md)
    assert "check number" not in read(out) and "(check" not in read(out)
    notes = saved(out)["sections"][0]["notes"]
    assert notes[0]["unsupported_numbers"] == [] and notes[1]["unsupported_numbers"] == ["500"]


def test_nothing_to_teach_gives_the_no_teaching_points_line(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))
    Script(monkeypatch, section=section_json())
    text = read(summarize.summarize(md))
    assert "## Key points\n\n- No teaching points were found in this lesson." in text


def test_old_shape_json_still_works(tmp_path):
    md = make_lesson(tmp_path, texts(9), v2=False)
    assert "v" not in json.loads(md.with_suffix(".json").read_text(encoding="utf-8"))
    text = read(summarize.summarize(md))
    assert "## Key points" in text and "[00:00]" in text


def test_notes_work_without_a_json_file(tmp_path):
    md = make_lesson(tmp_path, texts(9))
    md.with_suffix(".json").unlink()
    assert "[00:00]" in read(summarize.summarize(md))


def test_steps_actions_terms_and_tips_get_their_own_sections(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(8))
    Script(monkeypatch, section=section_json([note("00:00", "Open the tool", "step"), note("00:20", "Pick a niche", "step"),
                                              note("00:40", "Try it yourself", "action"),
                                              note("01:00", "Ahrefs is a tool", "term"), note("01:20", "Never skip it", "tip"),
                                              note("01:40", "A rule", "weird-kind")]))
    text = read(summarize.summarize(md))
    assert "1. Open the tool [00:00]\n2. Pick a niche [00:20]" in text and "- [ ] Try it yourself [00:40]" in text
    assert "## Terms and tools\n\n- Ahrefs is a tool [01:00]" in text
    assert "## Instructor's tips and warnings\n\n- Never skip it [01:20]" in text
    assert "## Key points\n\n- A rule [01:40]" in text


def test_the_glossary_normalises_lines_before_the_model_sees_them(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, ["use the کیورڈ tool", "second", "third"])
    (tmp_path / "notes" / "C" / "glossary.txt").write_text("keyword = کیورڈ\nSemrush\n", encoding="utf-8")
    script = Script(monkeypatch, section=section_json())
    summarize.summarize(md)
    prompt = script.of("notes")[0]
    assert "use the keyword tool" in prompt and "Glossary terms (spell them exactly like this): keyword, Semrush" in prompt


# ---- calls -------------------------------------------------------------------------------------------------

def test_each_line_is_sent_once_in_the_section_prompts(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(120))  # four sections
    script = Script(monkeypatch, section=section_json())
    summarize.summarize(md)
    prompts = script.of("notes")
    assert len(prompts) == 4
    for i in range(120):
        assert sum(f"{p}".count(f"Line {i} of the invented") for p in prompts) == 1
    assert all("## Plain text" not in p and p.count("<lines>") == 1 for p in prompts)
    assert "none, this is the first section" in prompts[0] and "Two sentences. Here." in prompts[1]
    assert [p for p, _ in script.calls].count("verify") == 0  # no bullets, nothing to verify
    assert len(script.of("summary")) == 1


def test_verify_and_summary_calls(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    script = Script(monkeypatch, section=section_json([note("00:00", "A point")]))
    summarize.summarize(md)
    assert [p for p, _ in script.calls] == ["notes", "verify", "summary"]
    assert "<items>" in script.of("verify")[0] and "A point" in script.of("summary")[0]


def test_fenced_json_is_accepted(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))
    Script(monkeypatch, section="Here you go:\n```json\n" + json.dumps(section_json([note("00:00", "Fenced")])) + "\n```")
    assert "Fenced [00:00]" in summarize.summarize(md).read_text(encoding="utf-8")


def test_invalid_json_is_retried_once(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))
    answers = iter(["not json at all", json.dumps(section_json([note("00:00", "Second try")]))])
    script = Script(monkeypatch, section=lambda user: next(answers))
    assert "Second try [00:00]" in summarize.summarize(md).read_text(encoding="utf-8")
    assert "Return valid JSON only" in script.of("notes")[1] and "Return valid JSON only" not in script.of("notes")[0]


def test_a_failed_section_is_marked_and_the_rest_continue(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(120))
    count = {"n": 0}

    def section(user):
        count["n"] += 1
        return "garbage" if count["n"] in (2, 3) else section_json([note("00:00", "ok")])

    Script(monkeypatch, section=section)
    # attempts: section 1 ok (1), section 2 fails twice (2, 3), sections 3 and 4 ok
    out = summarize.summarize(md)
    text = out.read_text(encoding="utf-8")
    assert "notes could not be generated for this part" in text
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert [s["ok"] for s in saved["sections"]] == [True, False, True, True]


def test_more_than_half_failed_sections_raise(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(120))
    Script(monkeypatch, section="garbage")
    with pytest.raises(RuntimeError, match="4 of 4 sections"):
        summarize.summarize(md)
    assert not md.with_name(md.stem + "-notes.md").exists()


def test_a_provider_error_in_one_section_is_a_failed_section(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(120))
    answers = iter([RuntimeError("rate limited"), section_json(), section_json(), section_json()])
    Script(monkeypatch, section=lambda user: next(answers))
    saved = json.loads(summarize.summarize(md).with_suffix(".json").read_text(encoding="utf-8"))
    assert [s["ok"] for s in saved["sections"]] == [False, True, True, True]


def test_no_provider_raises_clearly(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))
    monkeypatch.delenv("ATB_NOTES")
    with pytest.raises(RuntimeError, match="no notes AI key"):
        summarize.summarize(md)


def test_a_failed_verification_keeps_the_bullets(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(3))
    Script(monkeypatch, section=section_json([note("00:00", "Kept")]), verify="garbage")
    out = summarize.summarize(md)
    assert "Kept [00:00]" in out.read_text(encoding="utf-8")
    assert json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))["sections"][0]["verified"] is False


def test_empty_transcript_makes_no_model_call(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, [])
    script = Script(monkeypatch, section=section_json())
    assert "No speech could be read" in summarize.summarize(md).read_text(encoding="utf-8")
    assert script.calls == []


# ---- command line and pipeline -----------------------------------------------------------------------------

def test_cli_writes_notes(tmp_path):
    md = make_lesson(tmp_path / "data", texts(9))
    env = {"ATB_NOTES": "fake", "ATB_APP_DIR": str(tmp_path / "app"), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(ROOT / "addons" / "notes" / "make_notes.py"), str(md)], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "01-Lesson.md -> 01-Lesson-notes.md"
    assert (md.with_name("01-Lesson-notes.md")).exists() and (md.with_name("01-Lesson-notes.json")).exists()


def test_notes_failure_does_not_fail_the_lesson(env, monkeypatch, webm20):
    monkeypatch.setenv("AUTO_NOTES", "1")
    from fastapi.testclient import TestClient
    import server

    def boom(*a, **k):
        raise RuntimeError("the notes AI exploded")

    monkeypatch.setattr(llm, "chat", boom)
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "C", "lesson": "L"})
        job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["notes"] is None and "exploded" in job["notes_error"] and job["output"]


def test_pipeline_applies_the_course_glossary_and_copies_the_starter(env, monkeypatch, webm20):
    monkeypatch.setenv("AUTO_NOTES", "1")
    from fastapi.testclient import TestClient
    import server
    course_dir = env["data"] / "notes" / "Glossary-Course"
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "Glossary-Course", "lesson": "L", "speed": "1"})
        job = wait_job(c, r.json()["job_id"])
    starter = (ROOT / "glossaries" / "seo-starter.txt").read_text(encoding="utf-8")
    assert (course_dir / "glossary.txt").read_text(encoding="utf-8") == starter
    assert job["notes"] and (env["data"] / job["notes"]).exists()


def test_pipeline_glossary_fixes_text_and_keeps_raw_text(env, webm20):
    from fastapi.testclient import TestClient
    import server
    course_dir = env["data"] / "notes" / "Gl"
    course_dir.mkdir(parents=True)
    (course_dir / "glossary.txt").write_text("thing = Line\n", encoding="utf-8")
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "Gl", "lesson": "L", "speed": "1"})
        job = wait_job(c, r.json()["job_id"])
    md = (env["data"] / job["output"]).read_text(encoding="utf-8")
    assert "thing 0 covers" in md and "Line 0 covers" not in md
    saved = json.loads((env["data"] / job["output"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["segments"][0]["raw_text"].strip().startswith("Line 0") and saved["corrections"][0]["to"] == "thing"


def test_glossary_can_be_switched_off(env, monkeypatch, webm20):
    monkeypatch.setenv("ATB_GLOSSARY", "0")
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "Off", "lesson": "L"})
        wait_job(c, r.json()["job_id"])
    assert not (env["data"] / "notes" / "Off" / "glossary.txt").exists()


# ---- roman transcripts and the second hearing --------------------------------------------------------------

def write_second(md, rows):
    """A <stem>.second.json next to the transcript; rows are (start seconds, text)."""
    segments = [{"start": t, "end": t + 5, "text": text} for t, text in rows]
    md.with_name(md.stem + ".second.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")


URDU_LINES = ["یہ ایک فرضی لیکچر ہے", "دوسری لائن یہاں ہے", "تیسری لائن بھی ہے"]


def test_builders_without_roman_or_second_return_the_old_prompts():
    assert summarize.section_system(False, False) == summarize.SECTION_SYSTEM
    assert summarize.verify_system(False) == summarize.VERIFY_SYSTEM


def test_roman_section_prompt_swaps_only_the_about_paragraph():
    text = summarize.section_system(True, False)
    assert summarize.ROMAN_ABOUT in text and "sometimes spelled phonetically in Urdu script" not in text
    assert "Reply with JSON only, no other text, in exactly this shape and key order:" in text
    for heading in ("First fill \"turns\"", "What goes in \"notes\"", "What goes in \"qa\"", "What goes in \"unclear\"",
                    "Rules for all text:"):
        assert heading in text
    assert text.split("\n\nAbout the transcript:")[0] == summarize.SECTION_SYSTEM.split("\n\nAbout the transcript:")[0]
    assert text.split("\n\nReply with JSON only")[1] == summarize.SECTION_SYSTEM.split("\n\nReply with JSON only")[1]


def test_second_hearing_is_added_to_the_section_and_verify_prompts():
    assert summarize.section_system(False, True) == summarize.SECTION_SYSTEM + "\n\n" + summarize.SECOND_SECTION
    assert summarize.section_system(True, True).endswith(summarize.SECOND_SECTION)
    lines = summarize.verify_system(True).splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("Reply with JSON only"))
    assert lines[i - 1] == summarize.SECOND_VERIFY


def test_urdu_lesson_without_a_second_file_sends_the_old_prompts(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, URDU_LINES)
    script = Script(monkeypatch, section=section_json([note("00:00", "A point")]))
    out = summarize.summarize(md)
    assert script.systems_of("notes") == [summarize.SECTION_SYSTEM]
    assert script.systems_of("verify") == [summarize.VERIFY_SYSTEM]
    assert "<second_hearing>" not in script.of("notes")[0] + script.of("verify")[0]
    assert saved(out)["script"] == "urdu" and saved(out)["second_hearing"] is False


def test_roman_lesson_uses_the_roman_prompt(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    script = Script(monkeypatch, section=section_json())
    out = summarize.summarize(md)
    assert summarize.ROMAN_ABOUT in script.systems_of("notes")[0]
    assert saved(out)["script"] == "roman" and saved(out)["second_hearing"] is False


def test_script_is_decided_by_more_than_half_of_the_lines():
    def lines(*texts_):
        return [summarize.Line(i, i + 1, t) for i, t in enumerate(texts_)]
    assert summarize.is_roman(lines("one", "two", "یہ ہے")) is True
    assert summarize.is_roman(lines("one", "یہ ہے")) is False  # exactly half is not more than half
    assert summarize.is_roman(lines("123", "یہ ہے", "کیا")) is False  # a line without a letter is not counted
    assert summarize.is_roman([]) is False
    assert summarize.is_roman([summarize.Line(0, 5, "", True)]) is False


def test_second_hearing_reaches_the_section_and_verify_calls(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    write_second(md, [(2, "pehli second line"), (65, "doosri second line")])
    script = Script(monkeypatch, section=section_json([note("00:00", "A point")]))
    out = summarize.summarize(md)
    block = "<second_hearing>\n[00:02] pehli second line\n[01:05] doosri second line\n</second_hearing>"
    assert script.of("notes")[0].endswith("\n\n" + block)
    assert script.systems_of("notes")[0].endswith(summarize.SECOND_SECTION)
    assert script.of("verify")[0].endswith("\n\n" + block)
    assert summarize.SECOND_VERIFY in script.systems_of("verify")[0]
    assert saved(out)["second_hearing"] is True


def test_each_section_gets_only_its_own_second_hearing(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(120))  # four sections of about ten minutes
    write_second(md, [(100, "early second line"), (1250, "late second line")])
    script = Script(monkeypatch, section=section_json())
    out = summarize.summarize(md)
    prompts, systems = script.of("notes"), script.systems_of("notes")
    spans = [(s["start"], s["end"]) for s in saved(out)["sections"]]
    assert len(prompts) == 4
    for (start, end), prompt, system in zip(spans, prompts, systems):
        for t, word in ((100, "early second line"), (1250, "late second line")):
            assert (word in prompt) == (start - 20 <= t <= end + 5)
        has = "<second_hearing>" in prompt
        assert has == any(start - 20 <= t <= end + 5 for t in (100, 1250))
        assert system.endswith(summarize.SECOND_SECTION) == has
        assert (system == summarize.section_system(True, has))
    assert "<second_hearing>" not in prompts[1] and systems[1] == summarize.section_system(True, False)


def test_hearing_block_keeps_lines_from_twenty_seconds_before_to_five_after():
    second = [(79, "a"), (80, "b"), (205, "c"), (206, "d")]
    assert summarize.hearing_block(second, 100, 200) == "<second_hearing>\n[01:20] b\n[03:25] c\n</second_hearing>"
    assert summarize.hearing_block(second, 1000, 1100) == "" and summarize.hearing_block([], 0, 100) == ""


def test_load_second_gives_nothing_for_a_missing_or_bad_file(tmp_path):
    md = make_lesson(tmp_path, texts(3))
    assert summarize.load_second(md) == []
    second = md.with_name(md.stem + ".second.json")
    second.write_text("not json at all", encoding="utf-8")
    assert summarize.load_second(md) == []
    second.write_text(json.dumps([{"start": 0, "text": "x"}]), encoding="utf-8")
    assert summarize.load_second(md) == []
    second.write_text(json.dumps({"segments": "x"}), encoding="utf-8")
    assert summarize.load_second(md) == []


def test_load_second_skips_empty_text_and_sorts_by_start(tmp_path):
    md = make_lesson(tmp_path, texts(3))
    write_second(md, [(30, "later"), (10, "  "), (5, "sooner"), (20, "")])
    assert summarize.load_second(md) == [(5.0, "sooner"), (30.0, "later")]


def test_the_glossary_reaches_the_second_hearing(tmp_path, monkeypatch):
    md = make_lesson(tmp_path, texts(6))
    (tmp_path / "notes" / "C" / "glossary.txt").write_text("keyword = كيورڈ\nSemrush\n", encoding="utf-8")
    write_second(md, [(10, "use the كيورڈ tool")])
    assert summarize.load_second(md, glossary_mod_load(md)) == [(10.0, "use the keyword tool")]
    script = Script(monkeypatch, section=section_json())
    summarize.summarize(md)
    assert "[00:10] use the keyword tool" in script.of("notes")[0]


def glossary_mod_load(md):
    return summarize.glossary_mod.load(md.parent / "glossary.txt")

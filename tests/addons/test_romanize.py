import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import llm
import romanize
import summarize
import writer

ROOT = Path(__file__).resolve().parent.parent.parent
META = {"url": "u", "recorded": "r", "duration": 0, "model": "m", "language": "ur", "audio": "a", "speed": 1,
        "engine": "fake", "course": "C"}
U = "یہ کا"  # two common Urdu function words


def urdu(n):
    return f"{U} line{n}"


def echo(system, user, *, purpose, max_tokens):
    """A scripted model: replaces every Urdu-script word with the word urdu."""
    return llm._fake_roman(user)


class Spy:
    def __init__(self, answer=echo):
        self.calls, self.answer, self.lock = [], answer, threading.Lock()

    def __call__(self, system, user, *, purpose, max_tokens):
        with self.lock:
            self.calls.append((system, json.loads(user), purpose, max_tokens))
        return self.answer(system, user, purpose=purpose, max_tokens=max_tokens)


# ---- romanize_lines ------------------------------------------------------------------------------------------

def test_the_system_text_and_call_arguments():
    spy = Spy()
    romanize.romanize_lines([urdu(0)], chat=spy)
    system, batch, purpose, max_tokens = spy.calls[0]
    assert system == romanize.ROMAN_SYSTEM and purpose == "roman" and max_tokens == 8000 and batch == [urdu(0)]
    assert romanize.ROMAN_SYSTEM.startswith("You rewrite Urdu-script transcript lines in Roman Urdu")
    assert romanize.ROMAN_SYSTEM.endswith("same count, same order.")


def test_latin_only_lines_are_not_sent_and_stay_unchanged():
    spy = Spy()
    out, failed = romanize.romanize_lines(["plain english 12", urdu(1), "", "more latin"], chat=spy)
    assert out == ["plain english 12", "urdu urdu line1", "", "more latin"] and failed == []
    assert [b for _, b, _, _ in spy.calls] == [[urdu(1)]]
    spy = Spy()
    assert romanize.romanize_lines(["only latin"], chat=spy) == (["only latin"], []) and spy.calls == []


def test_batches_hold_at_most_forty_lines():
    spy = Spy()
    lines = [urdu(i) for i in range(85)]
    out, failed = romanize.romanize_lines(lines, chat=spy)
    assert sorted(len(b) for _, b, _, _ in spy.calls) == [5, 40, 40] and failed == []
    assert out == [f"urdu urdu line{i}" for i in range(85)]


def test_batches_hold_at_most_three_thousand_characters():
    spy = Spy()
    lines = [U + " " + "x" * 990 for _ in range(7)]  # 996 characters each
    romanize.romanize_lines(lines, chat=spy)
    assert sorted(len(b) for _, b, _, _ in spy.calls) == [1, 3, 3]
    spy = Spy()
    romanize.romanize_lines([U + " " + "y" * 5000, urdu(1)], chat=spy)  # one long line goes alone
    assert [len(b) for _, b, _, _ in spy.calls] == [1, 1]


def test_order_is_kept_with_threads():
    def slow_first(system, user, *, purpose, max_tokens):
        batch = json.loads(user)
        if batch[0].endswith("line0"):
            time.sleep(0.3)  # the first batch finishes last
        return llm._fake_roman(user)

    lines = [f"{U} line{i}" if i % 3 else f"latin {i}" for i in range(130)]
    out, failed = romanize.romanize_lines(lines, chat=slow_first)
    assert failed == []
    assert out == [f"urdu urdu line{i}" if i % 3 else f"latin {i}" for i in range(130)]


def test_a_fenced_json_answer_is_accepted():
    fenced = lambda s, u, **k: "```json\n" + llm._fake_roman(u) + "\n```"  # noqa: E731
    assert romanize.romanize_lines([urdu(0)], chat=fenced) == (["urdu urdu line0"], [])


@pytest.mark.parametrize("answer", [
    "not json at all",
    '{"a": 1}',
    '["one item", "too many"]',
    '[""]',
    '[3]',
    '["\\u06cc\\u06c1 still urdu"]',
    '["a"]',  # far fewer words than the input
    '["a b c d e f g h i j k l m n o p"]',  # far more words
])
def test_each_rejection_rule(answer):
    spy = Spy(lambda s, u, **k: answer)
    out, failed = romanize.romanize_lines([urdu(0)], chat=spy)
    assert out == [urdu(0)] and failed == [0] and len(spy.calls) == 2  # tried, retried once, kept the Urdu


def test_word_count_limits_are_inclusive_at_the_edges():
    ten = " ".join([U.split()[0]] * 10)
    for answer, ok in ((" ".join(["w"] * 7), True), (" ".join(["w"] * 6), False),
                       (" ".join(["w"] * 14), True), (" ".join(["w"] * 15), False)):
        out, failed = romanize.romanize_lines([ten], chat=lambda s, u, a=answer, **k: json.dumps([a]))
        assert (failed == []) is ok


def test_a_retry_that_works_is_used():
    answers = iter(["garbage", None])

    def chat(system, user, *, purpose, max_tokens):
        a = next(answers)
        return a if a else llm._fake_roman(user)

    assert romanize.romanize_lines([urdu(0)], chat=chat) == (["urdu urdu line0"], [])


def test_a_provider_error_counts_as_a_failed_attempt():
    def boom(*a, **k):
        raise RuntimeError("down")

    assert romanize.romanize_lines([urdu(0)], chat=boom) == ([urdu(0)], [0])


def test_only_the_failing_batch_keeps_its_urdu():
    def chat(system, user, *, purpose, max_tokens):
        batch = json.loads(user)
        return "nope" if batch[0].endswith("line40") else llm._fake_roman(user)

    lines = [urdu(i) for i in range(80)]
    out, failed = romanize.romanize_lines(lines, chat=chat)
    assert failed == list(range(40, 80))
    assert out[:40] == [f"urdu urdu line{i}" for i in range(40)] and out[40:] == lines[40:]


# ---- romanize_result -----------------------------------------------------------------------------------------

def result_of(lines):
    segs = [{"id": i, "start": i * 4.0, "end": i * 4.0 + 3, "text": " " + t} for i, t in enumerate(lines)]
    return {"text": " ".join(lines), "segments": segs, "language": "ur", "v": 2,
            "quality": {"words": 9, "flags": []}}


def test_romanize_result_keeps_the_urdu_and_rebuilds_the_text():
    res = romanize.romanize_result(result_of([urdu(0), "plain", urdu(2)]), chat=echo)
    assert [s["text"] for s in res["segments"]] == ["urdu urdu line0", "plain", "urdu urdu line2"]
    assert [s["urdu"] for s in res["segments"]] == [urdu(0), "plain", urdu(2)]
    assert res["text"] == "urdu urdu line0 plain urdu urdu line2"
    assert res["script"] == "roman" and res["quality"] == {"words": 9, "flags": [], "roman_failed_lines": 0}


def test_some_failed_lines_make_the_result_mixed():
    def chat(system, user, *, purpose, max_tokens):
        return "nope" if "line40" in user else llm._fake_roman(user)

    res = romanize.romanize_result(result_of([urdu(i) for i in range(41)]), chat=chat)
    assert res["script"] == "mixed" and res["quality"]["roman_failed_lines"] == 1
    assert res["segments"][40]["text"] == urdu(40) and "urdu" not in res["segments"][40]
    assert res["segments"][0]["urdu"] == urdu(0)


def test_a_result_with_no_segments_is_unchanged_apart_from_script():
    res = romanize.romanize_result({"text": "", "segments": [], "language": "ur"}, chat=echo)
    assert res == {"text": "", "segments": [], "language": "ur", "script": "urdu"}


def test_converting_twice_keeps_the_original_urdu():
    once = romanize.romanize_result(result_of([urdu(0)]), chat=echo)
    twice = romanize.romanize_result(once, chat=echo)
    assert twice["segments"][0]["urdu"] == urdu(0) and twice["segments"][0]["text"] == "urdu urdu line0"


# ---- writer and notes ----------------------------------------------------------------------------------------

def test_writer_uses_text_and_adds_script_after_language(tmp_path):
    res = romanize.romanize_result(result_of([urdu(0), urdu(1)]), chat=echo)
    md, js, _ = writer.write_transcript(tmp_path, "C", "L", res, META)
    text = (tmp_path / md).read_text(encoding="utf-8")
    head = text.split("\n---", 1)[0].splitlines()
    keys = [x.split(":")[0] for x in head]
    assert keys[keys.index("language") + 1] == "script" and "script: roman" in head
    assert "**[00:00]** urdu urdu line0" in text and text.endswith("## Plain text\n\nurdu urdu line0 urdu urdu line1\n")
    assert json.loads((tmp_path / js).read_text(encoding="utf-8"))["segments"][0]["urdu"] == urdu(0)


def test_notes_are_built_from_the_urdu_lines_when_present(tmp_path):
    res = romanize.romanize_result(result_of([urdu(0), "plain", urdu(2)]), chat=echo)
    md, _, _ = writer.write_transcript(tmp_path, "C", "L", res, META)
    lesson = summarize.load_lesson(tmp_path / md)
    assert [x.text for x in lesson["lines"]] == [urdu(0), "plain", urdu(2)]
    assert [x.start for x in lesson["lines"]] == [0.0, 4.0, 8.0]
    assert U not in (tmp_path / md).read_text(encoding="utf-8")  # the transcript file itself is Roman


# ---- command line --------------------------------------------------------------------------------------------

def run_cli(tmp_path, *paths):
    env = {"ATB_NOTES": "fake", "ATB_APP_DIR": str(tmp_path / "app"), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(ROOT / "addons" / "notes" / "make_roman.py"), *map(str, paths)], cwd=tmp_path,
                          env=env, capture_output=True, text=True)


def old_shape_lesson(tmp_path):
    lines = [urdu(0), "plain", urdu(2)]
    res = {"text": " ".join(lines), "language": "ur",
           "segments": [{"id": i, "start": i * 4.0, "end": i * 4.0 + 3, "text": " " + t} for i, t in enumerate(lines)]}
    md, js, _ = writer.write_transcript(tmp_path, "C", "L", res, {**META, "duration": 75})
    return tmp_path / md, tmp_path / js


def test_cli_converts_an_old_shape_lesson_and_keeps_a_backup(tmp_path):
    md, js = old_shape_lesson(tmp_path / "data")
    before = md.read_text(encoding="utf-8")
    r = run_cli(tmp_path, js)
    assert r.returncode == 0, r.stderr
    after = md.read_text(encoding="utf-8")
    assert "script: roman" in after and "**[00:00]** urdu urdu line0" in after and "duration: 01:15" in after
    assert "course: C" in after and "lesson: L" in after
    assert md.with_name("01-L.urdu.md").read_text(encoding="utf-8") == before
    saved = json.loads(js.read_text(encoding="utf-8"))
    assert saved["segments"][0]["urdu"] == urdu(0) and saved["script"] == "roman" and "v" not in saved


def test_cli_never_overwrites_an_existing_backup(tmp_path):
    md, js = old_shape_lesson(tmp_path / "data")
    backup = md.with_name("01-L.urdu.md")
    backup.write_text("keep me", encoding="utf-8")
    assert run_cli(tmp_path, js).returncode == 0
    assert backup.read_text(encoding="utf-8") == "keep me"
    again = run_cli(tmp_path, js)  # a second run is harmless and keeps the true Urdu
    assert again.returncode == 0
    assert json.loads(js.read_text(encoding="utf-8"))["segments"][0]["urdu"] == urdu(0)

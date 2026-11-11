import json
import subprocess

import numpy as np
import pytest

import audio
import engines
import transcriber
from transcriber import Options

SR = 16000


@pytest.fixture(autouse=True)
def quiet_fake(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_SECONDS", "0")
    for name in ("ATB_FAKE_LOOP_AT", "ATB_FAKE_CAPTION_AT"):
        monkeypatch.delenv(name, raising=False)


def tone(seconds, level=0.12):
    t = np.arange(int(seconds * SR)) / SR
    return (level * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


def bursts(*parts):
    """('n', s) noise, ('s', s) silence."""
    rng = np.random.default_rng(3)
    return np.concatenate([np.zeros(int(s * SR), np.float32) if k == "s"
                           else (rng.standard_normal(int(s * SR)) * 0.1).astype(np.float32) for k, s in parts])


class Counting(engines.FakeEngine):
    def __init__(self):
        self.calls = []

    def transcribe_array(self, samples, language, **decode):
        self.calls.append((len(samples) / SR, decode))
        return super().transcribe_array(samples, language, **decode)


class Stubborn(Counting):
    """Loops at 12 s on the first pass and in the first segment of every repair attempt."""

    def transcribe_array(self, samples, language, **decode):
        out = super().transcribe_array(samples, language, **decode)
        repair = "no_speech_threshold" in decode
        for s in out["segments"]:
            if (repair and s["id"] == 0) or (not repair and s["start"] <= 12.0 < s["end"]):
                s["text"] = " " + "word " * 30
        return out


def texts(result):
    return [s["text"] for s in result["segments"]]


def test_plain_run_and_result_shape():
    engine = Counting()
    result = transcriber.transcribe_samples(tone(20), engine, Options(speed=1.0))
    assert result["v"] == 2 and result["tempo"] == 1.0 and result["speed"] == 1.0
    assert len(result["segments"]) == 5 and result["issues"] == [] and result["corrections"] == []
    assert [s["id"] for s in result["segments"]] == list(range(5))
    assert set(result["segments"][0]) == {"id", "start", "end", "text", "avg_logprob", "compression_ratio",
                                          "no_speech_prob", "temperature", "block", "repaired"}
    assert result["duration"] == 20.0 and result["blocks"] == [{"i": 0, "start": 0.0, "end": 20.0}]
    assert result["speech_seconds"] == 20.0
    assert result["settings"] == {"engine": "fake", "model": "fake", "tempo_restore": True, "repair": True}
    assert result["quality"]["words"] > 0 and result["quality"]["flags"] == []
    assert len(engine.calls) == 1 and "no_speech_threshold" not in engine.calls[0][1]  # first pass: today's settings
    assert result["text"] == " ".join(s["text"].strip() for s in result["segments"])


@pytest.fixture(scope="module")
def sine60(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "tone60.webm"
    subprocess.run([audio.ffmpeg_exe(), "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=60", "-c:a", "libopus", str(path)], check=True)
    return path


def test_times_are_recording_seconds_when_slowed(sine60):
    engine = Counting()
    result = transcriber.transcribe_lesson(sine60, engine, Options(speed=2.0))
    assert abs(engine.calls[0][0] - 120.0) < 1.0  # whisper heard the audio at half speed
    assert abs(result["segments"][-1]["end"] - 60.0) < 1.0  # but the result is in recording seconds
    assert abs(result["duration"] - 60.0) < 1.0 and result["tempo"] == 0.5
    assert abs(result["segments"][1]["start"] - 2.0) < 0.01  # whisper 4 s is recording 2 s
    result = transcriber.transcribe_lesson(sine60, Counting(), Options(speed=2.0, tempo_restore=False))
    assert result["tempo"] == 1.0 and abs(result["segments"][-1]["end"] - 60.0) < 1.0


def test_transcribe_samples_slows_raw_samples_and_accepts_slowed_ones():
    engine = Counting()
    transcriber.transcribe_samples(tone(20), engine, Options(speed=2.0))
    assert abs(engine.calls[0][0] - 40.0) < 1.0
    engine = Counting()
    result = transcriber.transcribe_samples(tone(40), engine, Options(speed=2.0), speed_already_applied=True)
    assert abs(engine.calls[0][0] - 40.0) < 0.01 and abs(result["segments"][-1]["end"] - 20.0) < 0.01


def test_speeds_outside_one_atempo_filter_are_chained():
    assert audio.atempo_filter(0.5) == "atempo=0.500000"
    assert audio.atempo_filter(0.25).count("atempo") == 2


def test_loop_is_repaired(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "12.0")
    engine = Counting()
    result = transcriber.transcribe_samples(tone(60), engine, Options())
    assert not any("word word" in t for t in texts(result))
    assert [i["kind"] for i in result["issues"]] == ["repaired"]
    assert result["quality"]["repaired_windows"] == 1
    assert any(s["repaired"] for s in result["segments"])
    assert len(engine.calls) == 2
    repair_decode = engine.calls[1][1]
    assert repair_decode["no_speech_threshold"] is None and repair_decode["temperature"] == (0.0, 0.2, 0.4)
    assert abs(engine.calls[1][0] - (4 + 2 + 2)) < 4.1  # 2 s before, window, 2 s after (widened to whole segments)
    covered = [(s["start"], s["end"]) for s in result["segments"]]
    assert covered[0][0] == 0 and covered[-1][1] == 60.0
    assert all(a[1] <= b[0] + 0.01 for a, b in zip(covered, covered[1:]))  # nothing overlaps, nothing reordered


def test_unrepairable_loop_becomes_unclear_and_neighbours_survive():
    engine = Stubborn()
    result = transcriber.transcribe_samples(tone(60), engine, Options())
    kinds = [i["kind"] for i in result["issues"]]
    assert kinds == ["unclear"] and "loop" in result["issues"][0]["detail"]
    assert result["issues"][0]["start"] == 12.0 and result["issues"][0]["end"] == 16.0
    assert len(engine.calls) == 3  # first pass, then the two repair attempts
    got = texts(result)
    assert not any("word word" in t for t in got)
    assert any(t.startswith(" Line 2 ") for t in got) and any(t.startswith(" Line 4 ") for t in got)
    assert len(got) == 14  # 15 first-pass segments minus the flagged one
    assert result["quality"]["unclear_seconds"] == 4.0 and result["quality"]["repaired_windows"] == 0


def test_repair_switch_off_marks_unclear_without_extra_calls(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "12.0")
    engine = Counting()
    result = transcriber.transcribe_samples(tone(60), engine, Options(repair=False))
    assert len(engine.calls) == 1 and [i["kind"] for i in result["issues"]] == ["unclear"]
    assert len(result["segments"]) == 14


def test_repair_never_throws_away_unflagged_speech():
    class Thin(Counting):
        def transcribe_array(self, samples, language, **decode):
            out = super().transcribe_array(samples, language, **decode)
            if "no_speech_threshold" in decode:  # a repair attempt that finds almost nothing
                out["segments"] = out["segments"][:1]
                out["segments"][0]["text"] = " ok"
            elif out["segments"][3]["start"] == 12.0:
                out["segments"][3]["text"] = " " + "word " * 30
            return out

    result = transcriber.transcribe_samples(tone(60), Thin(), Options())
    assert [i["kind"] for i in result["issues"]] == ["unclear"]
    assert len(result["segments"]) == 14


def test_caption_becomes_non_speech(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_CAPTION_AT", "40.0")
    result = transcriber.transcribe_samples(tone(60), Counting(), Options())
    assert [(i["kind"], i["start"], i["end"]) for i in result["issues"]] == [("non_speech", 40.0, 44.0)]
    assert not any("موسیقی" in t for t in texts(result))
    assert len(result["segments"]) == 14 and result["quality"]["non_speech_seconds"] == 4.0


def test_empty_audio():
    for samples in (np.zeros(0, np.float32), np.zeros(SR * 5, np.float32)):
        engine = Counting()
        result = transcriber.transcribe_samples(samples, engine, Options())
        assert result["segments"] == [] and result["text"] == "" and result["blocks"] == []
        assert result["quality"]["words"] == 0 and result["quality"]["flags"] == [] and engine.calls == []


def test_progress_is_block_seconds_done_over_total():
    x = bursts(("n", 10), ("s", 4), ("n", 20), ("s", 4), ("n", 10))
    seen = []
    transcriber.transcribe_samples  # same flow as the lesson flow, which reports progress
    result = transcriber._run(x, Counting(), Options(), 1.0, None, seen.append, 0)
    assert len(result["blocks"]) == 3
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)
    assert max(seen[:-1]) <= 0.9 + 1e-9 and seen.count(1.0) == 1  # first pass ends at 0.9, 1.0 only at the very end
    assert any(abs(p - 0.9 * 0.25) < 0.05 for p in seen)  # 10 s of about 40 block seconds


def test_repair_progress_runs_from_0_9_to_1(monkeypatch):
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "12.0")
    seen = []
    transcriber._run(tone(60), Counting(), Options(), 1.0, None, seen.append, 0)
    assert 0.9 < seen[-2] < 1.0
    assert seen[-1] == 1.0 and seen.count(1.0) == 1


class Dies(Counting):
    def __init__(self, die_on):
        super().__init__()
        self.die_on = die_on

    def transcribe_array(self, samples, language, **decode):
        if len(self.calls) + 1 == self.die_on:
            raise RuntimeError("interrupted")
        return super().transcribe_array(samples, language, **decode)


def test_checkpoints_are_reused_after_an_interrupted_run(tmp_path, sine60):
    x = bursts(("n", 10), ("s", 4), ("n", 10), ("s", 4), ("n", 10))
    work = tmp_path / "work"
    first = Dies(die_on=3)
    with pytest.raises(RuntimeError):
        transcriber._run(x, first, Options(), 1.0, work, None, 123)
    assert len(first.calls) == 2
    assert sorted(p.name for p in work.glob("block-*.json")) == ["block-000.json", "block-001.json"]
    assert not list(work.glob("*.tmp"))
    second = Counting()
    result = transcriber._run(x, second, Options(), 1.0, work, None, 123)
    assert len(second.calls) == 1 and len(result["blocks"]) == 3  # only the missing block is decoded
    assert result["segments"][0]["start"] < 1.0
    third = Counting()
    transcriber._run(x, third, Options(), 1.0, work, None, 123)
    assert third.calls == []
    # a different audio size (a different file) or setting makes the checkpoints stale
    fourth = Counting()
    transcriber._run(x, fourth, Options(), 1.0, work, None, 999)
    assert len(fourth.calls) == 3
    saved = json.loads((work / "block-000.json").read_text())
    assert saved["hash"] and saved["segments"]


def test_speed_flag_and_wpm():
    result = transcriber.transcribe_samples(tone(20), Counting(), Options(speed=1.0))
    assert 60 <= result["quality"]["wpm"] <= 230
    class Wordy(Counting):
        def transcribe_array(self, samples, language, **decode):
            out = super().transcribe_array(samples, language, **decode)
            for s in out["segments"]:
                s["text"] = " " + " ".join(f"w{i}" for i in range(60))
            return out
    result = transcriber.transcribe_samples(tone(20), Wordy(), Options(speed=1.0))
    assert "speed_looks_wrong" in result["quality"]["flags"]


class Hole(Counting):
    """The first pass leaves out the lines that start inside [a, b). Repair attempts: `repair(out)` or untouched."""

    def __init__(self, a=20.0, b=36.0, repair=None):
        super().__init__()
        self.a, self.b, self.repair = a, b, repair

    def transcribe_array(self, samples, language, **decode):
        out = super().transcribe_array(samples, language, **decode)
        if "no_speech_threshold" in decode:
            if self.repair:
                self.repair(out)
        else:
            out["segments"] = [s for s in out["segments"] if not self.a <= s["start"] < self.b]
        return out


def no_text(result, kind):
    return [i for i in result["issues"] if i["kind"] == kind and i["detail"] == "no_text"]


def test_gap_is_filled():
    result = transcriber.transcribe_samples(bursts(("n", 60)), Hole(), Options())
    inside = [s for s in result["segments"] if 20.0 <= s["start"] < 36.0]
    assert inside and all(s["repaired"] for s in inside)
    assert len(no_text(result, "repaired")) == 1 and len(result["issues"]) == 1
    assert result["quality"]["repaired_windows"] == 1 and result["quality"]["unclear_seconds"] == 0
    covered = [(s["start"], s["end"]) for s in result["segments"]]
    assert all(a[1] <= b[0] + 0.01 for a, b in zip(covered, covered[1:]))


def test_pause_is_left_alone():
    rng = np.random.default_rng(5)
    x = bursts(("n", 60))
    x[20 * SR: 36 * SR] = (rng.standard_normal(16 * SR) * 0.003).astype(np.float32)
    engine = Hole()
    result = transcriber.transcribe_samples(x, engine, Options())
    assert len(engine.calls) == 1 and result["issues"] == []


def test_speech_that_stays_unreadable_is_marked_unclear():
    def loops(out):
        for s in out["segments"]:
            s["text"] = " " + "word " * 30
    engine = Hole(repair=loops)
    result = transcriber.transcribe_samples(bursts(("n", 60)), engine, Options())
    assert [(i["kind"], i["detail"], i["start"], i["end"]) for i in result["issues"]] == [("unclear", "no_text", 20.0, 36.0)]
    assert result["quality"]["unclear_seconds"] > 0 and result["quality"]["repaired_windows"] == 0
    assert len(engine.calls) == 3
    assert len(result["segments"]) == 11 and not any("word word" in t for t in texts(result))


def test_a_decode_that_finds_nothing_leaves_no_mark():
    engine = Hole(repair=lambda out: out.update(segments=[]))
    result = transcriber.transcribe_samples(bursts(("n", 60)), engine, Options())
    assert len(engine.calls) == 3 and result["issues"] == []


def _thin_engine(repair):
    class Thin(Counting):
        def transcribe_array(self, samples, language, **decode):
            out = super().transcribe_array(samples, language, **decode)
            if "no_speech_threshold" in decode:
                repair(out)
            else:
                keep = [s for s in out["segments"] if not 20.0 <= s["start"] < 36.0]
                keep.append({**out["segments"][5], "start": 20.0, "end": 36.0, "text": " one two three"})
                out["segments"] = sorted(keep, key=lambda s: s["start"])
            return out
    return Thin()


def test_thin_line_is_replaced():
    result = transcriber.transcribe_samples(bursts(("n", 120)), _thin_engine(lambda out: None), Options())
    assert not any(t == " one two three" for t in texts(result))
    inside = [s for s in result["segments"] if 20.0 <= s["start"] < 36.0]
    assert len(inside) >= 4 and all(s["repaired"] for s in inside)
    assert len(no_text(result, "repaired")) == 1 and result["quality"]["repaired_windows"] == 1


def test_thin_line_that_cannot_be_improved_stays_without_an_issue():
    def same(out):
        out["segments"] = [{**out["segments"][0], "text": " one two three"}]
    result = transcriber.transcribe_samples(bursts(("n", 120)), _thin_engine(same), Options())
    assert any(t == " one two three" for t in texts(result))
    assert result["issues"] == [] and result["quality"]["repaired_windows"] == 0


def test_repair_off_does_not_look_for_missing_text():
    engine = Hole()
    result = transcriber.transcribe_samples(bursts(("n", 60)), engine, Options(repair=False))
    assert len(engine.calls) == 1 and no_text(result, "repaired") == [] and no_text(result, "unclear") == []


def test_long_gap_is_measured_in_recording_time():
    def blocks(silence, speed):
        x = bursts(("n", 10), ("s", silence), ("n", 10))
        return len(transcriber.transcribe_samples(x, Counting(), Options(speed=speed),
                                                  speed_already_applied=True)["blocks"])
    assert blocks(4, 2.0) == 1   # 4 s slowed = 2 s of recording: not a break
    assert blocks(7, 2.0) == 2   # 7 s slowed = 3.5 s of recording: a break
    assert blocks(4, 1.0) == 2

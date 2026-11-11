"""The second hearing: hearing.fill (pure), the writer marks, claim order, and the pipeline job."""
import copy
import json
import threading
import time

import engines
import hearing
import writer
from conftest import wait_for, wait_job
from store import Store


# ---- hearing.fill ---------------------------------------------------------------------------------------------

def seg(start, end, text, **extra):
    return {"id": 0, "start": start, "end": end, "text": text, "block": 0, "repaired": False, **extra}


def primary_with(segments, issues=(), blocks=((0.0, 40.0),), tempo=1.0):
    return {"v": 2, "text": " ".join(s["text"] for s in segments), "segments": list(segments),
            "issues": list(issues), "blocks": [{"i": i, "start": a, "end": b} for i, (a, b) in enumerate(blocks)],
            "tempo": tempo, "quality": {"words": 0, "unclear_seconds": 0.0, "flags": []}}


def second_with(*lines):
    return {"segments": [{"start": a, "end": b, "text": t} for a, b, t in lines]}


def unclear(a, b):
    return {"kind": "unclear", "start": a, "end": b, "detail": "loop"}


def test_unclear_stretch_is_filled_from_the_second_hearing():
    primary = primary_with([seg(0, 10, "alpha beta"), seg(20, 30, "gamma")], [unclear(10, 20)])
    second = second_with((11, 14, "second one"), (14, 19, "second two"), (25, 28, "outside"))
    before_p, before_s = copy.deepcopy(primary), copy.deepcopy(second)
    new, filled = hearing.fill(primary, second)
    assert filled == 1
    assert primary == before_p and second == before_s
    assert [s["text"] for s in new["segments"]] == ["alpha beta", "second one", "second two", "gamma"]
    assert [s["id"] for s in new["segments"]] == [0, 1, 2, 3]
    added = new["segments"][1]
    assert set(added) - {"id"} == {"start", "end", "text", "block", "repaired", "source"}
    assert added["source"] == "second" and added["repaired"] is True and added["block"] == 0
    assert new["issues"] == [{"kind": "filled", "start": 10, "end": 20, "detail": "second_hearing"}]
    assert new["quality"]["unclear_seconds"] == 0 and new["quality"]["second_filled_seconds"] == 10
    assert new["quality"]["words"] == 7 and new["text"] == "alpha beta second one second two gamma"


def test_unclear_stretch_without_second_lines_stays_unclear():
    primary = primary_with([seg(0, 10, "alpha"), seg(20, 40, "beta")], [unclear(10, 20)])
    new, filled = hearing.fill(primary, second_with((30, 33, "far away")))
    assert filled == 0 and new["issues"] == [unclear(10, 20)]
    assert new["quality"]["unclear_seconds"] == 10 and new["quality"]["second_filled_seconds"] == 0
    assert len(new["segments"]) == 2


def test_a_long_gap_without_text_is_filled_and_a_short_one_is_left():
    long_gap = primary_with([seg(0, 10, "alpha"), seg(20, 30, "beta")])
    new, filled = hearing.fill(long_gap, second_with((12, 18, "heard it")))
    assert filled == 1 and [s["text"] for s in new["segments"]] == ["alpha", "heard it", "beta"]
    assert new["issues"] == [{"kind": "filled", "start": 10.0, "end": 20.0, "detail": "second_hearing"}]
    short_gap = primary_with([seg(0, 10, "alpha"), seg(15, 30, "beta")])
    new, filled = hearing.fill(short_gap, second_with((11, 14, "heard it")))
    assert filled == 0 and new["issues"] == [] and len(new["segments"]) == 2


def test_the_gap_threshold_follows_the_tempo():
    def run(tempo):
        p = primary_with([seg(0, 10, "alpha"), seg(15, 30, "beta")], tempo=tempo)  # a 5 s hole in recording time
        return hearing.fill(p, second_with((11, 14, "heard it")))[1]
    assert run(0.5) == 1
    assert run(1.0) == 0


def test_overlapping_holes_become_one():
    primary = primary_with([seg(0, 8, "alpha"), seg(30, 40, "beta")], [unclear(10, 20), unclear(15, 25)])
    new, filled = hearing.fill(primary, second_with((12, 14, "one"), (21, 23, "two")))
    assert filled == 1
    assert new["issues"] == [{"kind": "filled", "start": 10, "end": 25, "detail": "second_hearing"}]
    assert [s["text"] for s in new["segments"]] == ["alpha", "one", "two", "beta"]
    assert new["quality"]["second_filled_seconds"] == 15


def test_a_primary_without_blocks_only_uses_its_unclear_issues():
    gap_only = primary_with([seg(0, 10, "alpha"), seg(25, 30, "beta")])
    del gap_only["blocks"]
    assert hearing.fill(gap_only, second_with((12, 20, "heard it")))[1] == 0
    both = primary_with([seg(0, 10, "alpha"), seg(25, 30, "beta")], [unclear(10, 15)])
    del both["blocks"]
    new, filled = hearing.fill(both, second_with((11, 14, "heard it"), (17, 24, "gap line")))
    assert filled == 1 and [s["text"] for s in new["segments"]] == ["alpha", "heard it", "beta"]


def test_second_hearing_times_are_clipped_into_the_hole():
    primary = primary_with([seg(0, 8, "alpha"), seg(20, 40, "beta")], [unclear(10, 20)])
    second = second_with((8, 13, "front"), (16, 21, "back"), (17, 23, "mid on the edge"))
    new, _ = hearing.fill(primary, second)
    got = [(s["start"], s["end"], s["text"]) for s in new["segments"] if s.get("source") == "second"]
    assert got == [(10, 13, "front"), (16, 20, "back")]


# ---- writer ---------------------------------------------------------------------------------------------------

META = {"url": "u", "recorded": "r", "duration": 30.0, "model": "m", "language": "ur", "audio": "a.webm",
        "speed": 1, "engine": "fake"}


def test_a_second_hearing_line_is_marked_and_kept_out_of_the_plain_text(tmp_path):
    result = {"text": "alpha gamma", "segments": [seg(0, 10, "alpha"), seg(10, 20, "beta", source="second"),
                                                   seg(20, 30, "gamma")]}
    md, _, _ = writer.write_transcript(tmp_path, "C", "L", result, META)
    text = (tmp_path / md).read_text(encoding="utf-8")
    assert "**[00:10]** (second hearing) beta" in text
    assert text.split("## Plain text")[1].strip() == "alpha gamma"


def test_write_second_and_no_temporary_files(tmp_path):
    result = {"text": "alpha", "segments": [seg(0, 10, "alpha")]}
    md, js, _ = writer.write_transcript(tmp_path, "C", "Lesson", result, META)
    out = writer.write_second(tmp_path / md, {"segments": [seg(0, 5, "beta")], "language": "ur"})
    assert out == tmp_path / "notes" / "C" / "01-Lesson.second.json"
    assert json.loads(out.read_text(encoding="utf-8"))["segments"][0]["text"] == "beta"
    assert sorted(p.name for p in out.parent.iterdir()) == ["01-Lesson.json", "01-Lesson.md", "01-Lesson.second.json"]


# ---- store ----------------------------------------------------------------------------------------------------

def test_claim_job_with_kinds_in_order(tmp_path):
    store = Store(tmp_path / "x.db")
    second = store.create_job(1, "second")
    time.sleep(0.01)
    first = store.create_job(2, "transcribe")
    notes = store.create_job(3, "notes")
    assert store.claim_job(("transcribe", "second"))["id"] == first
    assert store.claim_job(("transcribe", "second"))["id"] == second
    assert store.claim_job(("transcribe", "second")) is None
    assert store.claim_job("notes")["id"] == notes


def test_claim_job_with_only_a_second_job(tmp_path):
    store = Store(tmp_path / "x.db")
    second = store.create_job(1, "second")
    assert store.claim_job(("transcribe", "second"))["id"] == second


# ---- pipeline -------------------------------------------------------------------------------------------------

class Roman(engines.FakeEngine):
    script = "roman"


class Roles:
    """Stands in for engines.Engines: one first engine, one second engine (or a second() that raises)."""

    def __init__(self, first, second=None, error=None):
        self._first, self._second, self._error = first, second, error

    def primary(self, language):
        return self._first

    def second(self):
        if self._error:
            raise self._error
        return self._second


class Second(engines.FakeEngine):
    """Hears its own words. Waits for `gate` when given, records its calls, or raises `fail`."""
    name = "fake-second"
    model = "fake-second"

    def __init__(self, gate=None, log=None, fail=None):
        self.gate, self.log, self.fail = gate, log if log is not None else [], fail

    def transcribe_array(self, samples, language, **decode):
        self.log.append("second")
        if self.gate:
            assert self.gate.wait(20), "the test never opened the gate"
        if self.fail:
            raise self.fail
        out = super().transcribe_array(samples, language, **decode)
        for s in out["segments"]:
            s["text"] = f" Second hearing line {s['id']} about the topic."
        return out


class First(Roman):
    def __init__(self, log=None, gate=None):
        self.log, self.gate = log if log is not None else [], gate

    def transcribe_array(self, samples, language, **decode):
        self.log.append("first")
        if self.gate:
            assert self.gate.wait(20), "the test never opened the gate"
        return super().transcribe_array(samples, language, **decode)


def start(env, monkeypatch, roles, second_on=True, **extra):
    from fastapi.testclient import TestClient
    import server
    if second_on:
        monkeypatch.setenv("ATB_SECOND_HEARING", "1")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    app = server.create_app()
    app.state.pipe.engines = roles
    return TestClient(app, base_url=f"http://127.0.0.1:{env['port']}"), app


def upload(c, webm, lesson="Lesson"):
    r = c.post("/transcribe", files={"audio": ("l.webm", webm.read_bytes(), "audio/webm")},
               data={"course": "Demo", "lesson": lesson, "speed": "1"})
    assert r.status_code == 200
    return r.json()["job_id"]


def second_state(c, job_id):
    return c.get(f"/jobs/{job_id}").json()["second"]


def second_done(c, job_id):
    return wait_for(lambda: second_state(c, job_id), lambda s: s is not None and s["state"] == "done")


def test_switch_off_gives_no_second_job_but_a_for_ai_file(env, monkeypatch, webm20):
    client, app = start(env, monkeypatch, Roles(Roman(), Second()), second_on=False)
    with client as c:
        job = wait_job(c, upload(c, webm20))
        assert job["second"] is None
    md = env["data"] / job["output"]
    assert not md.with_suffix(".second.json").exists()
    text = md.with_name(md.stem + "-for-ai.md").read_text(encoding="utf-8")
    assert "[00:00] Line 0 covers topic" in text and "SECOND HEARING" not in text


def test_the_transcript_is_readable_while_the_second_pass_runs(env, monkeypatch, webm20):
    gate = threading.Event()
    client, app = start(env, monkeypatch, Roles(Roman(), Second(gate=gate)))
    with client as c:
        job_id = upload(c, webm20)
        job = wait_job(c, job_id)
        assert job["second"]["state"] in ("queued", "running") and job["status"] == "done"
        md = env["data"] / job["output"]
        assert "## Transcript" in md.read_text(encoding="utf-8") and job["progress"] == 1.0
        assert not md.with_suffix(".second.json").exists()
        gate.set()
        state = second_done(c, job_id)
        assert state["error"] is None
    assert md.with_suffix(".second.json").exists()
    assert "SECOND HEARING" in md.with_name(md.stem + "-for-ai.md").read_text(encoding="utf-8")


def test_a_first_engine_without_roman_script_gets_no_second_job(env, monkeypatch, webm20):
    client, app = start(env, monkeypatch, Roles(engines.FakeEngine(), Second()))
    with client as c:
        job = wait_job(c, upload(c, webm20))
        assert job["second"] is None
    assert not (env["data"] / job["output"]).with_suffix(".second.json").exists()


def test_an_unclear_stretch_is_filled_after_the_second_pass(env, monkeypatch, webm20):
    client, app = start(env, monkeypatch, Roles(Roman(), Second()), ATB_REPAIR="0", ATB_FAKE_LOOP_AT="12.0")
    with client as c:
        job_id = upload(c, webm20)
        job = wait_job(c, job_id)
        md = env["data"] / job["output"]
        second_done(c, job_id)
        job = c.get(f"/jobs/{job_id}").json()
    text = md.read_text(encoding="utf-8")
    assert "**[00:12]** (second hearing) Second hearing line 3 about the topic." in text
    assert "unclear: 00:00" in text.split("\n---", 1)[0] and "[unclear until" not in text
    assert job["quality"]["unclear_seconds"] == 0 and job["quality"]["second_filled_seconds"] == 4


def test_a_failing_second_pass_leaves_the_transcript_alone(env, monkeypatch, webm20):
    gate = threading.Event()
    second = Second(gate=gate, fail=ValueError("second model broke"))
    client, app = start(env, monkeypatch, Roles(Roman(), second))
    with client as c:
        job_id = upload(c, webm20)
        job = wait_job(c, job_id)
        md = env["data"] / job["output"]
        before = (md.read_bytes(), md.with_suffix(".json").read_bytes())
        gate.set()
        state = second_done(c, job_id)
        job = c.get(f"/jobs/{job_id}").json()
    assert "second model broke" in state["error"] and job["status"] == "done" and job["error"] is None
    assert (md.read_bytes(), md.with_suffix(".json").read_bytes()) == before
    assert not md.with_suffix(".second.json").exists()


def test_a_missing_second_model_is_recorded_not_raised(env, monkeypatch, webm20):
    error = RuntimeError("the second hearing needs the Whisper large-v3 model. Run install/get-second-model.command once.")
    client, app = start(env, monkeypatch, Roles(Roman(), error=error))
    with client as c:
        job_id = upload(c, webm20)
        wait_job(c, job_id)
        state = second_done(c, job_id)
        job = c.get(f"/jobs/{job_id}").json()
    assert state["error"] == str(error) and job["status"] == "done"
    assert app.state.store.get_job(job_id)["state"] == "done"


def test_notes_wait_for_the_second_pass(auto_notes, env, monkeypatch, webm20):
    gate = threading.Event()
    client, app = start(env, monkeypatch, Roles(Roman(), Second(gate=gate)))
    with client as c:
        job_id = upload(c, webm20)
        job = wait_job(c, job_id)
        assert app.state.store.lesson_job(job["lesson_id"], "notes") is None and job["notes"] is None
        gate.set()
        second_done(c, job_id)
        job = wait_for(lambda: c.get(f"/jobs/{job_id}").json(), lambda j: j["notes"])
    md = env["data"] / job["output"]
    assert (env["data"] / job["notes"]).stat().st_mtime_ns >= md.with_suffix(".second.json").stat().st_mtime_ns


def test_notes_follow_the_first_pass_when_the_switch_is_off(auto_notes, env, monkeypatch, webm20):
    client, app = start(env, monkeypatch, Roles(Roman(), Second()), second_on=False)
    with client as c:
        job_id = upload(c, webm20)
        job = wait_for(lambda: c.get(f"/jobs/{job_id}").json(), lambda j: j["notes"])
    assert job["second"] is None


def test_every_first_pass_runs_before_any_second_pass(env, monkeypatch, webm20):
    log, release = [], threading.Event()
    client, app = start(env, monkeypatch, Roles(First(log, gate=release), Second(log=log)))
    with client as c:
        a = upload(c, webm20, "Lesson A")
        b = upload(c, webm20, "Lesson B")
        release.set()
        second_done(c, a)
        second_done(c, b)
    assert log == ["first", "first", "second", "second"]


def test_a_second_job_left_running_is_picked_up_again(env, monkeypatch, webm20):
    log = []
    client, app = start(env, monkeypatch, Roles(Roman(), Second(log=log)), ATB_REPAIR="0", ATB_FAKE_LOOP_AT="12.0")
    with client as c:
        job_id = upload(c, webm20)
        job = wait_job(c, job_id)
        second_done(c, job_id)
        md = env["data"] / job["output"]
        after_first_run = md.read_bytes()
        store = app.state.store
        old = store.lesson_job(job["lesson_id"], "second")
        store.update_job(old["id"], state="running", finished_at=None)  # a crash in the middle
        assert store.requeue_running() == (1, 0)
        assert store.get_job(old["id"])["state"] == "queued"
        app.state.pipe.wake_transcribe.set()
        wait_for(lambda: store.get_job(old["id"]), lambda j: j["state"] == "done" and j["finished_at"])
    assert log == ["second", "second"]
    assert md.read_bytes() == after_first_run and "(second hearing)" in after_first_run.decode("utf-8")


def test_find_transcript_ignores_for_ai_and_urdu_files(client):
    pipe = client.app.state.pipe
    d = client.env["data"] / "notes" / "c"
    d.mkdir(parents=True)
    head = "---\ncourse: c\naudio: recordings/c/a.webm\npipeline: 2\n---\n# a\n"
    (d / "01-a-for-ai.md").write_text(head)
    (d / "01-a.urdu.md").write_text(head)
    assert pipe.find_transcript("c", "recordings/c/a.webm") is None
    (d / "01-a.md").write_text(head)
    assert pipe.find_transcript("c", "recordings/c/a.webm") == "notes/c/01-a.md"

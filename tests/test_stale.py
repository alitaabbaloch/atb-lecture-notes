import sqlite3
import time

from fastapi.testclient import TestClient

import server
from conftest import new_session, wait_for


def put(client, sid, offset, data, seq=0):
    return client.put(f"/sessions/{sid}/chunks/{seq}", content=data, headers={"X-Offset": str(offset)})


def db(env):
    c = sqlite3.connect(env["app"] / "library.db")
    c.row_factory = sqlite3.Row
    return c


def session_row(env, sid):
    with db(env) as c:
        return dict(c.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone())


def test_new_session_and_chunks_set_stale_after(client):
    sid = new_session(client)["sid"]
    created = session_row(client.env, sid)["stale_after"]
    assert abs(created - (time.time() + 300)) < 10
    time.sleep(0.05)
    put(client, sid, 0, b"x")
    assert session_row(client.env, sid)["stale_after"] > created


def test_startup_gives_grace_and_finishes_nothing(env):
    def boot():
        return TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}")

    with boot() as c1:
        c1.env = env
        sid = new_session(c1)["sid"]
        put(c1, sid, 0, b"a" * 10)
    old = time.time() - 86400
    with db(env) as c:  # as if the server had been down for a day
        c.execute("UPDATE sessions SET last_chunk_at=?, stale_after=?", (old, old + 300))
    with boot() as c2:
        row = session_row(env, sid)
        assert row["state"] == "recording" and row["recovered"] == 0
        assert row["stale_after"] > time.time() + 43200 - 60
        assert c2.get(f"/sessions/{sid}").json()["state"] == "recording"
        r = put(c2, sid, 10, b"b" * 5, seq=1)
        assert r.status_code == 200 and r.json() == {"received_bytes": 15}


def test_sweeper_finishes_only_after_stale_after(client, webm20):
    sid = new_session(client)["sid"]
    put(client, sid, 0, webm20.read_bytes())
    pipe = client.app.state.pipe
    deadline = session_row(client.env, sid)["stale_after"]
    assert pipe.sweep_stale(now=deadline - 1) == 0
    assert client.get(f"/sessions/{sid}").json()["state"] == "recording"
    assert pipe.sweep_stale(now=deadline + 1) == 1
    row = session_row(client.env, sid)
    assert row["state"] == "finished" and row["recovered"] == 1
    job = client.get("/jobs").json()[0]
    assert job["recovered"] is True and job["sid"] == sid
    wait_for(lambda: client.get(f"/jobs/{job['id']}").json(), lambda j: j["status"] == "done")


def test_old_database_gets_the_new_columns(tmp_path):
    from store import Store
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE sessions(sid TEXT PRIMARY KEY, client_id TEXT UNIQUE, lesson_id INTEGER, state TEXT,"
                  " part_path TEXT, received_bytes INTEGER DEFAULT 0, last_chunk_at REAL,"
                  " recovered INTEGER DEFAULT 0, created_at REAL)")
        c.execute("INSERT INTO sessions(sid, state) VALUES('s1', 'recording')")
    s = Store(path)
    cols = {r["name"] for r in s.conn().execute("PRAGMA table_info(sessions)")}
    assert {"stale_after", "finish_reason"} <= cols
    assert s.get_session("s1")["stale_after"] is None
    Store(path)  # second open must not fail


def test_view_sid_stalled_and_recovered_reason(client, webm20):
    sid = new_session(client)["sid"]
    view = client.get(f"/jobs/{sid}").json()
    assert view["status"] == "recording" and view["sid"] == sid and view["stalled"] is False
    with db(client.env) as c:
        c.execute("UPDATE sessions SET last_chunk_at=?", (time.time() - 120,))
    assert client.get(f"/jobs/{sid}").json()["stalled"] is True
    put(client, sid, 0, webm20.read_bytes())
    assert client.get(f"/jobs/{sid}").json()["stalled"] is False
    out = client.post(f"/sessions/{sid}/finish", json={"reason": "recovered"}).json()
    job = wait_for(lambda: client.get(f"/jobs/{out['job_id']}").json(), lambda j: j["status"] == "done")
    assert job["recovered"] is True and job["stalled"] is False and job["sid"] == sid
    assert session_row(client.env, sid)["finish_reason"] == "recovered"
    assert session_row(client.env, sid)["recovered"] == 0  # the reason alone drives the view


def test_normal_finish_is_not_recovered(client, webm20):
    sid = new_session(client)["sid"]
    put(client, sid, 0, webm20.read_bytes())
    out = client.post(f"/sessions/{sid}/finish", json={"reason": "stopped"}).json()
    job = wait_for(lambda: client.get(f"/jobs/{out['job_id']}").json(), lambda j: j["status"] == "done")
    assert job["recovered"] is False


def test_rerun_after_lost_db_update_reuses_the_transcript(auto_notes, client, webm20):
    """A crash between writing the transcript and the database update must not make a second numbered file."""
    sid = new_session(client)["sid"]
    put(client, sid, 0, webm20.read_bytes())
    out = client.post(f"/sessions/{sid}/finish", json={"reason": "stopped"}).json()
    first = wait_for(lambda: client.get(f"/jobs/{out['job_id']}").json(), lambda j: j["status"] == "done")
    notes_dir = client.env["data"] / "notes" / "Demo-Course"
    before = sorted(p.name for p in notes_dir.glob("*.md"))
    assert len(before) == 3  # transcript, notes and the for-AI file
    with db(client.env) as c:  # forget the transcript, requeue the job
        c.execute("UPDATE lessons SET md_path=NULL, notes_path=NULL, number=NULL")
        c.execute("UPDATE jobs SET state='queued', finished_at=NULL WHERE kind='transcribe'")
    client.app.state.pipe.wake_transcribe.set()
    wait_for(lambda: client.get(f"/jobs/{out['job_id']}").json(),
             lambda j: j["status"] == "done" and j["output"] == first["output"])
    wait_for(lambda: client.get(f"/jobs/{out['job_id']}").json(), lambda j: j["notes"] == first["notes"])
    assert sorted(p.name for p in notes_dir.glob("*.md")) == before


def test_find_transcript_ignores_other_audio_and_notes_files(client, tmp_path):
    pipe = client.app.state.pipe
    d = client.env["data"] / "notes" / "c"
    d.mkdir(parents=True)
    (d / "01-a.md").write_text("---\ncourse: c\naudio: recordings/c/a.webm\npipeline: 2\n---\n# a\n")
    (d / "02-b.md").write_text("---\ncourse: c\naudio: recordings/c/b.webm\npipeline: 2\n---\n# b\n")
    (d / "02-b-notes.md").write_text("audio: recordings/c/z.webm\n")
    assert pipe.find_transcript("c", "recordings/c/b.webm") == "notes/c/02-b.md"
    assert pipe.find_transcript("c", "recordings/c/z.webm") is None
    assert pipe.find_transcript("nope", "recordings/c/a.webm") is None

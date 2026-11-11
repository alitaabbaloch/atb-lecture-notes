import threading

from store import MAX_ATTEMPTS, Store


def make(tmp_path):
    return Store(tmp_path / "library.db")


def lesson(s):
    course = s.course_for("C", "c")
    return s.create_lesson(course["id"], "L", "l", "", 1.0, "ur", "2024-01-01 10:00")


def test_schema_created(tmp_path):
    s = make(tmp_path)
    names = {r[0] for r in s.conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"courses", "lessons", "sessions", "jobs", "settings"} <= names
    assert s.conn().execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_claim_is_atomic(tmp_path):
    s = make(tmp_path)
    lid = lesson(s)
    s.create_job(lid, "transcribe")
    results = []

    def grab():
        results.append(s.claim_job("transcribe"))

    threads = [threading.Thread(target=grab) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    claimed = [r for r in results if r is not None]
    assert len(claimed) == 1
    assert claimed[0]["state"] == "running"
    assert s.claim_job("transcribe") is None


def test_claim_takes_oldest_and_only_its_kind(tmp_path):
    s = make(tmp_path)
    lid = lesson(s)
    first = s.create_job(lid, "transcribe")
    s.create_job(lid, "transcribe")
    s.create_job(lid, "notes")
    assert s.claim_job("transcribe")["id"] == first
    assert s.claim_job("notes")["kind"] == "notes"


def test_requeue_running(tmp_path):
    s = make(tmp_path)
    lid = lesson(s)
    job = s.create_job(lid, "transcribe")
    s.claim_job("transcribe")
    assert s.requeue_running() == (1, 0)
    row = s.get_job(job)
    assert row["state"] == "queued" and row["attempts"] == 1


def test_attempts_limit(tmp_path):
    s = make(tmp_path)
    lid = lesson(s)
    job = s.create_job(lid, "transcribe")
    for _ in range(MAX_ATTEMPTS - 1):
        s.claim_job("transcribe")
        assert s.requeue_running() == (1, 0)
    s.claim_job("transcribe")
    assert s.requeue_running() == (0, 1)
    row = s.get_job(job)
    assert row["state"] == "error" and row["attempts"] == MAX_ATTEMPTS
    assert s.claim_job("transcribe") is None

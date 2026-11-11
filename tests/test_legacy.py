from conftest import wait_job


def test_legacy_transcribe_gives_a_done_job(auto_notes, client, webm20):
    with open(webm20, "rb") as f:
        r = client.post("/transcribe", files={"audio": ("lesson.webm", f, "audio/webm")},
                        data={"course": "Old Course", "lesson": "Old Lesson", "url": "http://example.invalid/old"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = wait_job(client, job_id)
    expected = {"id", "lesson_id", "course", "lesson", "url", "created", "status", "stage", "progress",
                "progress_estimated", "size_mb", "started", "finished", "seconds", "output", "notes", "preview",
                "error", "notes_error", "second", "recovered", "sid", "stalled", "quality"}
    assert set(job) == expected
    assert job["course"] == "Old Course" and job["lesson"] == "Old Lesson" and job["url"] == "http://example.invalid/old"
    assert job["size_mb"] > 0 and job["seconds"] is not None and job["error"] is None and job["notes_error"] is None
    assert job["output"].startswith("notes/Old-Course/01-Old-Lesson") and job["preview"]
    assert job["notes"].endswith("-notes.md") and job["progress"] == 1.0 and job["progress_estimated"] is False
    assert job["quality"]["words"] > 0
    data = client.env["data"]
    assert (data / job["output"]).exists() and (data / job["notes"]).exists()
    assert len(list((data / "recordings" / "Old-Course").glob("*.webm"))) == 1
    assert client.get("/jobs").json()[0]["id"] == job_id


def test_legacy_empty_upload_is_rejected(client):
    r = client.post("/transcribe", files={"audio": ("lesson.webm", b"", "audio/webm")})
    assert r.status_code == 400


def test_legacy_log(client):
    r = client.post("/log", json={"src": "offscreen", "stage": "uploaded", "detail": "job 1\nsecond line"})
    assert r.json() == {"ok": True}
    text = (client.env["app"] / "logs" / "extension.log").read_text()
    assert "[offscreen] uploaded job 1 second line" in text and len(text.splitlines()) == 1


def test_missing_key_does_not_fail_the_lesson(env, monkeypatch, webm20):
    """Real notes path without DEEPSEEK_API_KEY: lesson is done, the reason is in notes_error."""
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("AUTO_NOTES", "1")
    monkeypatch.delenv("ATB_NOTES")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        r = c.post("/transcribe", files={"audio": ("l.webm", webm20.read_bytes(), "audio/webm")},
                   data={"course": "C", "lesson": "L"})
        job = wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["notes"] is None and "no notes AI key" in job["notes_error"]
    assert job["output"]

import json

from conftest import wait_for, wait_job


def upload(client, webm, speed="1.5"):
    r = client.post("/transcribe", files={"audio": ("lesson.webm", webm.read_bytes(), "audio/webm")},
                    data={"course": "Demo", "lesson": "Lesson", "speed": speed})
    assert r.status_code == 200
    return r.json()["job_id"]


def test_real_progress_reaches_the_job_view(env, monkeypatch, webm20):
    monkeypatch.setenv("ATB_FAKE_SECONDS", "1.5")
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        job_id = upload(c, webm20)
        seen = []

        def poll():
            job = c.get(f"/jobs/{job_id}").json()
            seen.append((job["status"], job["progress"], job["progress_estimated"]))
            return job

        wait_for(poll, lambda j: j["status"] == "transcribing" and j["progress"] is not None, timeout=20)
        job = wait_job(c, job_id)
    running = [s for s in seen if s[0] == "transcribing" and s[1] is not None and s[2] is False]
    assert running and all(0.0 <= p <= 1.0 for _, p, _ in running)
    assert job["progress"] == 1.0 and job["progress_estimated"] is False


def test_quality_unclear_line_frontmatter_and_work_folder(env, monkeypatch, webm20):
    monkeypatch.setenv("ATB_REPAIR", "0")
    monkeypatch.setenv("ATB_FAKE_LOOP_AT", "12.0")
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        job = wait_job(c, upload(c, webm20, speed="1.5"))
        assert c.get("/jobs").json()[0]["quality"] == job["quality"]
    assert job["quality"]["unclear_seconds"] > 0 and job["quality"]["words"] > 0
    text = (env["data"] / job["output"]).read_text(encoding="utf-8")
    head = text.split("\n---", 1)[0].splitlines()
    keys = [l.split(":")[0] for l in head]
    assert keys[-4:] == ["pipeline", "speech", "unclear", "tempo"]
    assert "tempo: 0.6667" in head
    assert "[unclear until 00:10]" in text and "**[00:08]**" in text  # whisper 12 s * 0.6667 = recording 8 s
    saved = json.loads((env["data"] / job["output"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["v"] == 2 and saved["issues"][0]["kind"] == "unclear"
    work = env["app"] / "work"
    assert not work.exists() or not any(work.iterdir())


def test_a_normal_lesson_at_speed_one_has_no_tempo_and_no_unclear_lines(client, webm20):
    job = wait_job(client, upload(client, webm20, speed="1"))
    text = (client.env["data"] / job["output"]).read_text(encoding="utf-8")
    assert "tempo:" not in text and "[unclear" not in text and job["quality"]["unclear_seconds"] == 0
    assert text.count("**[") == 5  # five 4 s lines for 20 s of audio


def test_config_switches(env, monkeypatch):
    import config
    s = config.load()
    assert s.tempo_restore is True and s.repair is True
    monkeypatch.setenv("ATB_TEMPO_RESTORE", "0")
    monkeypatch.setenv("ATB_REPAIR", "0")
    s = config.load()
    assert s.tempo_restore is False and s.repair is False
    monkeypatch.setenv("ATB_REPAIR", "1")
    assert config.load().repair is True

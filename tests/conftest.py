import socket
import subprocess
import time

import pytest

import audio

TEST_ENV = ("ATB_DEV", "ATB_FAKE_SECONDS", "DEEPSEEK_API_KEY", "WHISPER_LANGUAGE", "WHISPER_MODEL", "AUTO_NOTES",
            "NOTES_MODEL", "ATB_FAKE_LOOP_AT", "ATB_FAKE_CAPTION_AT", "ATB_REPAIR", "ATB_TEMPO_RESTORE",
            "ANTHROPIC_API_KEY", "NOTES_BASE_URL", "NOTES_API_KEY", "ATB_GLOSSARY", "ATB_NOTES_PROVIDER", "ATB_SCRIPT", "ATB_FAKE_ROMAN", "ATB_FAKE_URDU",
            "ATB_TRANSCRIPT", "ATB_SECOND_HEARING", "ATB_ROMAN_MODEL")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Throwaway app and data folders, a free port (never 8765), fake engine and fake notes."""
    for name in TEST_ENV:
        monkeypatch.delenv(name, raising=False)
    port = free_port()
    monkeypatch.setenv("ATB_APP_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("ATB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ATB_PORT", str(port))
    monkeypatch.setenv("ATB_ENGINE", "fake")
    monkeypatch.setenv("ATB_NOTES", "fake")
    monkeypatch.setenv("ATB_FAKE_SECONDS", "0.1")
    return {"app": tmp_path / "app", "data": tmp_path / "data", "port": port}


@pytest.fixture
def auto_notes(env, monkeypatch):
    """Opt in to automatic notes. List it before `client` so the app reads it."""
    monkeypatch.setenv("AUTO_NOTES", "1")


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        c.env = env
        yield c


@pytest.fixture(scope="session")
def webm20(tmp_path_factory):
    """Twenty seconds of a 440 Hz tone as Opus in WebM, made with the bundled ffmpeg."""
    path = tmp_path_factory.mktemp("media") / "tone.webm"
    subprocess.run([audio.ffmpeg_exe(), "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=20", "-c:a", "libopus", str(path)], check=True)
    return path


def wait_for(fetch, done, timeout=30.0):
    """Poll fetch() until done(value) is true; fail with the last value on timeout."""
    end = time.time() + timeout
    value = None
    while time.time() < end:
        value = fetch()
        if done(value):
            return value
        time.sleep(0.2)
    raise AssertionError(f"timed out, last value: {value}")


def wait_job(client, job_id, status="done", timeout=30.0):
    return wait_for(lambda: client.get(f"/jobs/{job_id}").json(), lambda j: j.get("status") == status, timeout)


def new_session(client, client_id="c1", **fields):
    body = {"client_session_id": client_id, "course": "Demo Course", "lesson": "Lesson One",
            "url": "http://example.invalid/1", "speed": 1.5, "started_at": 1700000000000, **fields}
    r = client.post("/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()

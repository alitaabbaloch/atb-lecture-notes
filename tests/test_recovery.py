"""Real subprocess tests: the server is killed with SIGKILL and must pick up where it stopped."""
import json
import math
import os
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pytest

import audio
from conftest import free_port, wait_for

ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "tools" / "replay_chunks.py"


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return json.loads(r.read())


class Server:
    def __init__(self, tmp_path, fake_seconds="0.2", auto_notes=True):
        self.port = free_port()
        self.app, self.data = tmp_path / "app", tmp_path / "data"
        self.log = tmp_path / "server.out"
        self.fake_seconds = fake_seconds
        self.auto_notes = auto_notes
        self.proc = None

    def start(self):
        env = {**os.environ, "ATB_APP_DIR": str(self.app), "ATB_DATA_DIR": str(self.data), "ATB_PORT": str(self.port),
               "ATB_ENGINE": "fake", "ATB_NOTES": "fake", "ATB_FAKE_SECONDS": self.fake_seconds,
               "AUTO_NOTES": "1" if self.auto_notes else "0"}
        env.pop("ATB_DEV", None)
        env.pop("DEEPSEEK_API_KEY", None)
        with open(self.log, "ab") as out:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(self.port),
                 "--app-dir", str(ROOT / "server")], cwd=ROOT, env=env, stdout=out, stderr=out)
        wait_for(self.health, lambda h: h is not None, timeout=20)

    def health(self):
        try:
            return get(self.port, "/health")
        except OSError:
            return None

    def kill(self):
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(timeout=10)

    def replay(self, source, *args):
        r = subprocess.run([sys.executable, str(REPLAY), "--port", str(self.port), "--file", str(source),
                            "--course", "Demo Course", "--lesson", "Lesson One", *args],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        return dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line), r.stdout

    def job(self, job_id):
        return get(self.port, f"/jobs/{job_id}")


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    yield s
    if s.proc and s.proc.poll() is None:
        s.proc.kill()
        s.proc.wait()


def test_crash_mid_upload_then_resume(server, webm20):
    source_size = webm20.stat().st_size
    chunk = 30000
    total_chunks = math.ceil(source_size / chunk)
    half = total_chunks // 2
    assert half >= 1

    server.start()
    first, out = server.replay(webm20, "--chunk-bytes", str(chunk), "--stop-after", str(half))
    assert "not finished" in out
    client_id = first["client_session_id"]
    assert get(server.port, f"/sessions/{first['sid']}")["received_bytes"] == half * chunk
    assert server.health()["queue"]["recording"] == 1

    server.kill()
    server.start()
    assert server.health()["queue"]["recording"] == 1  # not stale yet, so it is still waiting for the client

    second, out = server.replay(webm20, "--chunk-bytes", str(chunk), "--resume", client_id)
    assert second["sid"] == first["sid"]
    assert f"resuming at byte {half * chunk}" in out
    job = wait_for(lambda: server.job(second["job_id"]), lambda j: j["status"] in ("done", "error"), timeout=60)
    assert job["status"] == "done", job

    data = server.data
    recordings = list((data / "recordings" / "Demo-Course").glob("*.webm"))
    assert len(recordings) == 1
    final = recordings[0]
    assert not list((data / "recordings").rglob("*.part"))
    duration = audio.duration_seconds(final)
    assert duration is not None and abs(duration - 20) < 0.5
    # Every uploaded byte arrived: the server held exactly the source bytes before it remuxed them.
    session = get(server.port, f"/sessions/{first['sid']}")
    assert session["state"] == "finished" and session["received_bytes"] == source_size
    # The remux rewrites the container header, so the file is a few bytes different from the source (not byte
    # identical). The audio itself must be: same decoded samples.
    assert abs(final.stat().st_size - source_size) <= 64, (final.stat().st_size, source_size)
    assert np.array_equal(audio.decode_16k_mono(final), audio.decode_16k_mono(webm20))
    assert (data / job["output"]).exists() and (data / job["notes"]).exists()
    assert "## Transcript" in (data / job["output"]).read_text()


def test_crash_while_transcribing_requeues_the_job(tmp_path, webm20):
    server = Server(tmp_path, fake_seconds="3")
    try:
        server.start()
        first, _ = server.replay(webm20)
        job_id = first["job_id"]
        wait_for(lambda: server.job(job_id), lambda j: j["status"] == "transcribing", timeout=20)
        server.kill()

        server.start()
        job = wait_for(lambda: server.job(job_id), lambda j: j["status"] in ("done", "error"), timeout=60)
        assert job["status"] == "done", job
        assert job["output"] and (server.data / job["output"]).exists()
        assert (server.data / job["notes"]).exists()
        assert len(list((server.data / "notes").rglob("*.md"))) == 3  # transcript + notes + for-AI file, no duplicates
    finally:
        if server.proc and server.proc.poll() is None:
            server.proc.kill()
            server.proc.wait()


def test_shutdown_endpoint_really_exits(server):
    server.start()
    token = (server.app / "admin.token").read_text().strip()
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/admin/shutdown", method="POST",
                                 headers={"X-Admin-Token": token})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read()) == {"ok": True, "stopping": True}
    assert server.proc.wait(timeout=15) == 0
    assert not (server.app / "server.json").exists()

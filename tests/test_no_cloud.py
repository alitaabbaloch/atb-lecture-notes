"""The core never calls a cloud AI: no import of one, no package needed, notes only when switched on."""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import wait_job

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"anthropic", "openai", "llm", "llm_anthropic", "llm_openai", "summarize", "romanize"}

BLOCKED_RUN = """
import json, sys
sys.modules["anthropic"] = None
sys.modules["openai"] = None
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from fastapi.testclient import TestClient
import server
audio = Path(sys.argv[2]).read_bytes()
with TestClient(server.create_app(), base_url="http://127.0.0.1:" + sys.argv[3]) as c:
    r = c.post("/transcribe", files={"audio": ("l.webm", audio, "audio/webm")}, data={"course": "C", "lesson": "L"})
    job_id = r.json()["job_id"]
    import time
    end = time.time() + 30
    job = {}
    while time.time() < end:
        job = c.get("/jobs/" + job_id).json()
        if job.get("status") in ("done", "error"):
            break
        time.sleep(0.2)
real = [n for n in ("llm", "summarize", "romanize", "anthropic", "openai")
        if type(sys.modules.get(n)).__name__ == "module"]
print(json.dumps({"status": job.get("status"), "output": job.get("output"), "notes": job.get("notes"),
                  "notes_error": job.get("notes_error"), "real": real}))
"""


def imported_names(tree) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def upload(client, webm, lesson="L"):
    r = client.post("/transcribe", files={"audio": ("l.webm", webm.read_bytes(), "audio/webm")},
                    data={"course": "C", "lesson": lesson})
    return wait_job(client, r.json()["job_id"])


def test_no_server_module_imports_a_cloud_ai_or_the_notes_code():
    for path in sorted((ROOT / "server").glob("*.py")):
        bad = imported_names(ast.parse(path.read_text(encoding="utf-8"))) & FORBIDDEN
        assert not bad, f"{path.name} imports {sorted(bad)}"


def test_a_lesson_finishes_with_the_cloud_packages_blocked(env, webm20):
    run_env = {**os.environ, "ATB_APP_DIR": str(env["app"]), "ATB_DATA_DIR": str(env["data"]),
               "ATB_PORT": str(env["port"]), "ATB_ENGINE": "fake", "ATB_FAKE_SECONDS": "0.1"}
    for name in ("AUTO_NOTES", "ATB_NOTES", "ATB_DEV"):
        run_env.pop(name, None)
    r = subprocess.run([sys.executable, "-c", BLOCKED_RUN, str(ROOT / "server"), str(webm20), str(env["port"])],
                       cwd=ROOT, env=run_env, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == "done" and out["output"] and (env["data"] / out["output"]).exists()
    assert out["notes"] is None and not list(env["data"].rglob("*-notes.*"))
    assert out["real"] == []


def test_notes_are_off_by_default(client, webm20):
    job = upload(client, webm20)
    assert job["status"] == "done" and job["notes"] is None and job["notes_error"] is None
    assert client.app.state.pipe.store.lesson_job(job["lesson_id"], "notes") is None
    assert not list(client.env["data"].rglob("*-notes.*"))


def test_a_missing_add_on_does_not_fail_the_lesson(env, monkeypatch, webm20):
    from fastapi.testclient import TestClient
    import pipeline
    import server
    monkeypatch.setenv("AUTO_NOTES", "1")
    monkeypatch.setattr(pipeline.Pipeline, "notes_addon", lambda self: None)
    with TestClient(server.create_app(), base_url=f"http://127.0.0.1:{env['port']}") as c:
        job = upload(c, webm20)
    assert job["status"] == "done" and job["output"] and job["notes"] is None
    assert job["notes_error"] == "the notes add-on is not installed"


def test_the_add_on_writes_notes_when_switched_on(auto_notes, client, webm20):
    job = upload(client, webm20)
    assert job["status"] == "done" and job["notes"] and job["notes_error"] is None
    assert (client.env["data"] / job["notes"]).exists()


def test_old_config_keys_are_ignored(env):
    import config
    env["app"].mkdir(parents=True)
    (env["app"] / "config.json").write_text(json.dumps({"script": "roman", "glossary_suggestions": True}),
                                            encoding="utf-8")
    cfg = config.load()
    assert cfg.auto_notes is False and not hasattr(cfg, "script") and not hasattr(cfg, "glossary_suggestions")

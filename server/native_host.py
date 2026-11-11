"""Chrome native messaging host for ATB Lecture Notes.

Reads one message from stdin, answers once on stdout, exits.
Commands: status, start, stop, open_library. Standard library only.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

HOST_VERSION = "1.0.0"
APP_ID = "atb-lecture-notes"
MAX_MESSAGE = 64 * 1024
COMMANDS = ("status", "start", "stop", "open_library")
REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- paths


def app_dir() -> Path:
    override = os.environ.get("ATB_APP_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ATB Lecture Notes"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "ATB Lecture Notes"
    return Path.home() / ".config" / "atb-lecture-notes"


def get_port() -> int:
    env = os.environ.get("ATB_PORT")
    if env and env.isdigit():
        return int(env)
    try:
        cfg = json.loads((app_dir() / "config.json").read_text(encoding="utf-8"))
        value = int(cfg.get("port"))
        if 0 < value < 65536:
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return 8765


def venv_python() -> Path:
    if sys.platform.startswith("win"):
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def log_line(text: str) -> None:
    try:
        logs = app_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(logs / "host.log", "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {text}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- framing


def read_message(stream, max_len: int = MAX_MESSAGE):
    """Read one framed message. Returns the decoded object.

    Raises EOFError when there is no message, ValueError when it is too large
    or is not valid JSON.
    """
    header = stream.read(4)
    if not header or len(header) < 4:
        raise EOFError("no message")
    (length,) = struct.unpack("@I", header)
    if length > max_len:
        raise ValueError("message too large")
    body = stream.read(length)
    if len(body) < length:
        raise ValueError("message truncated")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid message")


def encode_message(obj) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack("@I", len(body)) + body


def write_message(stream, obj) -> None:
    stream.write(encode_message(obj))
    stream.flush()


# ---------------------------------------------------------------- server calls


def probe(port: int, timeout: float = 2.0):
    """Return ("down", None), ("ours", health_dict) or ("foreign", None)."""
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read(65536).decode("utf-8"))
    except urllib.error.HTTPError:
        return "foreign", None
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return "down", None
    except (ValueError, UnicodeDecodeError):
        return "foreign", None
    if isinstance(data, dict) and data.get("app") == APP_ID:
        return "ours", data
    return "foreign", None


def foreign_reply(port: int) -> dict:
    return {"ok": False, "error": f"another program is using port {port}", "foreign": True}


def cmd_status(port: int) -> dict:
    state, data = probe(port)
    if state == "ours":
        return {"ok": True, "running": True, "version": data.get("version"), "busy": bool(data.get("busy"))}
    if state == "foreign":
        return foreign_reply(port)
    return {"ok": True, "running": False, "version": None, "busy": False}


def spawn_server(port: int):
    py = venv_python()
    cmd = [
        str(py), "-m", "uvicorn", "server:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--app-dir", str(REPO_ROOT / "server"),
    ]
    logs = app_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / "server.log", "ab")
    env = dict(os.environ)
    env["ATB_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": out, "stderr": out,
              "cwd": str(REPO_ROOT), "env": env}
    try:
        if sys.platform.startswith("win"):
            base = (getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
            breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
            try:
                return subprocess.Popen(cmd, creationflags=base | breakaway, **kwargs)
            except OSError:
                return subprocess.Popen(cmd, creationflags=base, **kwargs)
        return subprocess.Popen(cmd, start_new_session=True, **kwargs)
    finally:
        out.close()


def cmd_start(port: int, wait_seconds: float = 20.0) -> dict:
    state, _ = probe(port)
    if state == "ours":
        return {"ok": True, "running": True, "started": False}
    if state == "foreign":
        return foreign_reply(port)
    if not venv_python().exists():
        return {"ok": False, "error": "not installed: run the installer"}
    try:
        proc = spawn_server(port)
    except OSError as exc:
        return {"ok": False, "error": f"could not start the server: {exc.strerror or exc}"}
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        state, _ = probe(port, timeout=1.0)
        if state == "ours":
            return {"ok": True, "running": True, "started": True}
        if state == "foreign":
            return foreign_reply(port)
        if proc.poll() is not None:
            return {"ok": False, "error": "the server stopped right after starting, see logs/server.log"}
        time.sleep(0.4)
    return {"ok": False, "error": "the server did not answer in time, see logs/server.log"}


def cmd_stop(port: int) -> dict:
    state, _ = probe(port)
    if state == "down":
        return {"ok": True, "stopped": False, "running": False}
    if state == "foreign":
        return foreign_reply(port)
    try:
        token = (app_dir() / "admin.token").read_text(encoding="utf-8").strip()
    except OSError:
        return {"ok": False, "error": "admin token not found"}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/shutdown",
        data=b"",
        method="POST",
        headers={"X-Admin-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            return {"ok": False, "busy": True}
        return {"ok": False, "error": f"the server refused to stop (HTTP {exc.code})"}
    except (urllib.error.URLError, OSError):
        return {"ok": False, "error": "could not reach the server"}
    return {"ok": True, "stopped": True}


def cmd_open_library(port: int) -> dict:
    try:
        webbrowser.open(f"http://127.0.0.1:{port}/library")
    except Exception as exc:  # webbrowser can raise many things
        return {"ok": False, "error": f"could not open the browser: {exc}"}
    return {"ok": True}


def handle(msg) -> dict:
    cmd = msg.get("cmd") if isinstance(msg, dict) else None
    if cmd not in COMMANDS:
        return {"ok": False, "error": "unknown command"}
    port = get_port()
    if cmd == "status":
        return cmd_status(port)
    if cmd == "start":
        return cmd_start(port)
    if cmd == "stop":
        return cmd_stop(port)
    return cmd_open_library(port)


def run(stdin, stdout) -> dict:
    """Read one message, write one reply, return the reply."""
    cmd = None
    try:
        msg = read_message(stdin)
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        reply = handle(msg)
    except EOFError:
        return {}
    except ValueError as exc:
        reply = {"ok": False, "error": str(exc)}
    except Exception as exc:  # never leave Chrome without an answer
        reply = {"ok": False, "error": f"host error: {type(exc).__name__}"}
    reply["host_version"] = HOST_VERSION
    log_line(f"cmd={cmd!r} reply={json.dumps(reply, separators=(',', ':'))}")
    write_message(stdout, reply)
    return reply


def main() -> int:
    if sys.platform.startswith("win"):
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    run(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Tests for server/native_host.py. Uses temp dirs and free ports, never 8765."""
import io
import json
import socket
import struct
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import native_host  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    """Tiny HTTP server. `app` is what /health reports."""

    def __init__(self, app="atb-lecture-notes", shutdown_status=200):
        self.app = app
        self.shutdown_status = shutdown_status
        self.tokens = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {"ok": True, "app": outer.app, "version": "1.0.0", "busy": False})
                else:
                    self._send(404, {})

            def do_POST(self):
                if self.path.startswith("/admin/shutdown"):
                    outer.tokens.append(self.headers.get("X-Admin-Token"))
                    code = outer.shutdown_status
                    self._send(code, {"ok": code == 200} if code == 200 else {"busy": True})
                else:
                    self._send(404, {})

        self.port = free_port()
        self.httpd = HTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ATB_APP_DIR", str(tmp_path))
    monkeypatch.delenv("ATB_PORT", raising=False)
    return tmp_path


def roundtrip(msg_bytes):
    out = io.BytesIO()
    reply = native_host.run(io.BytesIO(msg_bytes), out)
    out.seek(0)
    return reply, native_host.read_message(out)


def test_framing_round_trip():
    obj = {"cmd": "status", "text": "café"}
    buf = io.BytesIO(native_host.encode_message(obj))
    assert native_host.read_message(buf) == obj


def test_read_message_empty_stream():
    with pytest.raises(EOFError):
        native_host.read_message(io.BytesIO(b""))


def test_unknown_command(env):
    reply, framed = roundtrip(native_host.encode_message({"cmd": "format_disk"}))
    assert framed == reply
    assert reply["ok"] is False
    assert reply["error"] == "unknown command"
    assert reply["host_version"] == "1.0.0"


def test_oversized_message(env):
    # Only the length header claims 1 MB. The host must refuse without reading it.
    data = struct.pack("@I", 1024 * 1024) + b"x" * 10
    reply, framed = roundtrip(data)
    assert reply["ok"] is False
    assert "too large" in reply["error"]
    assert framed["host_version"] == "1.0.0"


def test_status_ours_and_stopped(env, monkeypatch):
    with FakeServer() as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "status"}))
        assert reply["ok"] is True and reply["running"] is True
        assert reply["version"] == "1.0.0" and reply["busy"] is False
    monkeypatch.setenv("ATB_PORT", str(free_port()))
    reply, _ = roundtrip(native_host.encode_message({"cmd": "status"}))
    assert reply["ok"] is True and reply["running"] is False


def test_status_foreign(env, monkeypatch):
    with FakeServer(app="something-else") as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "status"}))
        assert reply["ok"] is False and reply["foreign"] is True


def test_stop_sends_token(env, monkeypatch):
    (env / "admin.token").write_text("secret-token\n")
    with FakeServer() as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "stop"}))
        assert reply == {"ok": True, "stopped": True, "host_version": "1.0.0"}
        assert srv.tokens == ["secret-token"]


def test_stop_busy(env, monkeypatch):
    (env / "admin.token").write_text("t")
    with FakeServer(shutdown_status=409) as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "stop"}))
        assert reply["ok"] is False and reply["busy"] is True


def test_start_foreign_port(env, monkeypatch):
    with FakeServer(app="not-us") as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "start"}))
        assert reply["ok"] is False
        assert reply["foreign"] is True
        assert reply["error"] == f"another program is using port {srv.port}"


def test_start_already_running(env, monkeypatch):
    with FakeServer() as srv:
        monkeypatch.setenv("ATB_PORT", str(srv.port))
        reply, _ = roundtrip(native_host.encode_message({"cmd": "start"}))
        assert reply["ok"] is True and reply["running"] is True and reply["started"] is False


def test_start_not_installed(env, monkeypatch):
    monkeypatch.setenv("ATB_PORT", str(free_port()))
    monkeypatch.setattr(native_host, "venv_python", lambda: env / "missing" / "python")
    reply, _ = roundtrip(native_host.encode_message({"cmd": "start"}))
    assert reply["ok"] is False
    assert reply["error"] == "not installed: run the installer"


def test_port_from_config(env):
    (env / "config.json").write_text(json.dumps({"port": 9123}))
    assert native_host.get_port() == 9123


def test_request_is_logged(env):
    roundtrip(native_host.encode_message({"cmd": "nope"}))
    log = (env / "logs" / "host.log").read_text()
    assert "unknown command" in log


def test_spawn_server_passes_env(env, monkeypatch):
    seen = {}

    class FakeProc:
        pass

    def fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(native_host.subprocess, "Popen", fake_popen)
    native_host.spawn_server(45678)
    assert seen["env"]["ATB_PORT"] == "45678"
    assert seen["env"]["PYTHONUNBUFFERED"] == "1"
    assert "--port" in seen["cmd"] and "45678" in seen["cmd"]

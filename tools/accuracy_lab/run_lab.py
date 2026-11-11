#!/usr/bin/env python3
"""Serve a lab folder to your browser and store the answers.

Usage: uv run python tools/accuracy_lab/run_lab.py --lab <folder> [--port 8770]

Only 127.0.0.1 is used. key.json is never served. Answers go to answers.jsonl (every answer, in order) and
answers.json (the latest answer per clip).
"""
import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

PAGE_FILES = {"index.html": "text/html; charset=utf-8", "lab.js": "text/javascript; charset=utf-8",
              "lab.css": "text/css; charset=utf-8", "lab.json": "application/json; charset=utf-8",
              "answers.json": "application/json; charset=utf-8"}
CLIP_RE = re.compile(r"^clips/(clip-\d+)\.wav$")
RESERVED_PORTS = (8765, 8766)
MAX_BODY = 10_000


class Lab:
    """The folder, the list of clips and the answers (guarded by a lock)."""

    def __init__(self, folder: Path):
        self.folder = Path(folder).resolve()
        data = json.loads((self.folder / "lab.json").read_text(encoding="utf-8"))
        self.clips = {c["id"]: {k["key"] for k in c["candidates"]} for c in data["clips"]}
        self.lock = threading.Lock()
        self.answers: dict[str, dict] = {}
        log = self.folder / "answers.jsonl"
        if log.exists():  # carry on where an earlier run stopped
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    self._keep(json.loads(line))
                except ValueError:
                    continue
        self._write_json()

    def _keep(self, row: dict) -> None:
        old = self.answers.get(row["clip"])
        if old is None or row["at"] >= old["at"]:
            self.answers[row["clip"]] = row

    def _write_json(self) -> None:
        tmp = self.folder / "answers.json.tmp"
        tmp.write_text(json.dumps({"answers": self.answers}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.folder / "answers.json")

    def validate(self, body) -> dict | None:
        if not isinstance(body, dict):
            return None
        clip, choice = body.get("clip"), body.get("choice")
        if clip not in self.clips or not (choice in ("same", "none") or choice in self.clips[clip]):
            return None
        at = body.get("at")
        at = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else time.time() * 1000
        return {"clip": clip, "choice": choice, "mistakes": body.get("mistakes") is True, "at": at}

    def store(self, row: dict) -> None:
        with self.lock:
            with open(self.folder / "answers.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            self._keep(row)
            self._write_json()

    def file_for(self, rel: str):
        """Map a URL path to (file, content type) or None. Only the page files and the clips are served."""
        if rel in ("", "/"):
            rel = "index.html"
        rel = rel.lstrip("/")
        if rel in PAGE_FILES:
            return self.folder / rel, PAGE_FILES[rel]
        if CLIP_RE.match(rel):
            return self.folder / rel, "audio/wav"
        return None


def make_server(folder, port: int = 8770) -> ThreadingHTTPServer:
    if port in RESERVED_PORTS:
        raise SystemExit(f"Port {port} belongs to the main server. Pick another.")
    lab = Lab(folder)

    class Handler(BaseHTTPRequestHandler):
        server_version = "accuracy-lab"

        def log_message(self, *args):
            pass

        def _port(self) -> int:
            return self.server.server_address[1]

        def _allowed(self) -> bool:
            p = self._port()
            if self.headers.get("Host", "") not in (f"127.0.0.1:{p}", f"localhost:{p}"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in (f"http://127.0.0.1:{p}", f"http://localhost:{p}")

        def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None, head=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _get(self, head: bool):
            if not self._allowed():
                return self._send(403, b"forbidden", head=head)
            raw = urlsplit(self.path).path
            rel = unquote(raw)
            target = lab.file_for(rel) if ".." not in rel and "\\" not in rel else None
            if not target or not target[0].is_file():
                return self._send(404, b"not found", head=head)
            path, ctype = target
            size = path.stat().st_size
            rng = self.headers.get("Range")
            start, end, code = 0, size - 1, 200
            if rng:
                m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
                if not m or (m.group(1) == "" and m.group(2) == ""):
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"}, head=head)
                if m.group(1) == "":  # last N bytes
                    start, end = max(0, size - int(m.group(2))), size - 1
                else:
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                if start >= size or start > end:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"}, head=head)
                code = 206
            with open(path, "rb") as f:
                f.seek(start)
                body = f.read(end - start + 1)
            extra = {"Accept-Ranges": "bytes"}
            if code == 206:
                extra["Content-Range"] = f"bytes {start}-{end}/{size}"
            self._send(code, body, ctype, extra, head)

        def do_GET(self):
            self._get(False)

        def do_HEAD(self):
            self._get(True)

        def do_POST(self):
            if not self._allowed():
                return self._send(403, b"forbidden")
            if urlsplit(self.path).path != "/answer":
                return self._send(404, b"not found")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._send(415, b"json only")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, b"length required")
            if length < 0 or length > MAX_BODY:
                return self._send(413, b"too large")
            try:
                row = lab.validate(json.loads(self.rfile.read(length).decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                row = None
            if row is None:
                return self._send(400, b"bad answer")
            lab.store(row)
            self._send(200, b'{"ok":true}', "application/json")

        def do_OPTIONS(self):  # no cross-origin use at all
            self._send(403, b"forbidden")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lab", required=True)
    p.add_argument("--port", type=int, default=8770)
    a = p.parse_args(argv)
    if not (Path(a.lab) / "lab.json").exists():
        raise SystemExit(f"No lab.json in {a.lab}. Run make_lab.py first.")
    server = make_server(a.lab, a.port)
    print(f"Open http://127.0.0.1:{server.server_address[1]}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

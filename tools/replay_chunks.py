#!/usr/bin/env python3
"""Plays the part of the extension: uploads a recording to the local server in chunks.

Examples:
  replay_chunks.py --port 8766 --file lesson.webm --course Demo --lesson "Lesson 1"
  replay_chunks.py --port 8766 --file lesson.webm --stop-after 3          # simulate a crash
  replay_chunks.py --port 8766 --file lesson.webm --resume <client_session_id>
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid


def call(port: int, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--course", default="Demo course")
    p.add_argument("--lesson", default="Demo lesson")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed recorded in the lesson")
    p.add_argument("--chunk-bytes", type=int, default=30000)
    p.add_argument("--stop-after", type=int, metavar="N", help="exit after N chunks without finishing")
    p.add_argument("--resume", metavar="CLIENT_SESSION_ID", help="continue a session from the bytes the server has")
    p.add_argument("--no-finish", action="store_true")
    a = p.parse_args()

    data = open(a.file, "rb").read()
    client_id = a.resume or uuid.uuid4().hex
    code, session = call(a.port, "POST", "/sessions", json.dumps({
        "client_session_id": client_id, "course": a.course, "lesson": a.lesson, "url": "http://example.invalid/lesson",
        "speed": a.speed, "started_at": int(time.time() * 1000)}).encode(), {"Content-Type": "application/json"})
    if code != 200:
        print(f"could not create session: {code} {session}", file=sys.stderr)
        return 1
    sid, offset = session["sid"], session["received_bytes"]
    print(f"client_session_id={client_id}")
    print(f"sid={sid}")
    if a.resume:
        print(f"resuming at byte {offset} of {len(data)}")

    sent = 0
    while offset < len(data):
        if a.stop_after is not None and sent >= a.stop_after:
            print(f"stopped after {sent} chunks at byte {offset} (not finished)")
            return 0
        chunk = data[offset:offset + a.chunk_bytes]
        code, out = call(a.port, "PUT", f"/sessions/{sid}/chunks/{sent}", chunk,
                         {"X-Offset": str(offset), "Content-Type": "application/octet-stream"})
        if code == 409 and "received_bytes" in out:
            offset = out["received_bytes"]  # the server is behind or ahead; continue from what it has
            continue
        if code != 200:
            print(f"chunk failed: {code} {out}", file=sys.stderr)
            return 1
        offset = out["received_bytes"]
        sent += 1
    print(f"uploaded {offset} bytes in {sent} chunks")

    if a.no_finish or (a.stop_after is not None and sent >= a.stop_after):
        return 0
    code, out = call(a.port, "POST", f"/sessions/{sid}/finish",
                     json.dumps({"reason": "replay", "total_bytes": len(data)}).encode(),
                     {"Content-Type": "application/json"})
    if code != 200:
        print(f"finish failed: {code} {out}", file=sys.stderr)
        return 1
    print(f"job_id={out['job_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""ATB Lecture Notes server (FastAPI, 127.0.0.1 only). Run: uvicorn server:app --app-dir server"""
import hmac
import json
import logging
import os
import secrets
import signal
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import config
import engines
from pipeline import Pipeline
from store import Store

MAX_CHUNK_BYTES = 8 * 1024 * 1024
CORS_METHODS = "GET, POST, PUT, PATCH, OPTIONS"
CORS_HEADERS = "Content-Type, X-Offset, X-Admin-Token"

LIBRARY_HTML = """<!doctype html><meta charset="utf-8"><title>ATB Lecture Notes</title>
<style>body{font:14px ui-monospace,Menlo,monospace;background:#0B1014;color:#D7EEEE;margin:24px}
td,th{padding:4px 14px 4px 0;text-align:left}h1{font-size:16px}</style>
<h1>ATB Lecture Notes</h1><table id="t"><tr><th>course</th><th>lesson</th><th>status</th><th>output</th></tr></table>
<script>fetch('/jobs?limit=100').then(r=>r.json()).then(js=>{const t=document.getElementById('t');
for(const j of js){const tr=t.insertRow();for(const v of [j.course,j.lesson,j.status,j.output||'']){tr.insertCell().textContent=v}}})</script>"""


async def body_json(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def exit_process() -> None:
    """Ask uvicorn to shut down gracefully (it handles SIGINT)."""
    if sys.platform == "win32":
        threading.Timer(0.5, lambda: os._exit(0)).start()
    else:
        os.kill(os.getpid(), signal.SIGINT)


def create_app() -> FastAPI:
    cfg = config.load()
    store = Store(cfg.db_path)
    roles = engines.Engines(cfg)
    pipe = Pipeline(cfg, store, roles)
    nonce = secrets.token_hex(16)
    token_path = cfg.app_dir / "admin.token"
    if not token_path.exists():
        write_private(token_path, secrets.token_urlsafe(32))
    admin_token = token_path.read_text(encoding="utf-8").strip()
    server_json = cfg.app_dir / "server.json"
    hosts = {f"127.0.0.1:{cfg.port}", f"localhost:{cfg.port}"}
    origins = {f"http://127.0.0.1:{cfg.port}", f"http://localhost:{cfg.port}",
               *(f"chrome-extension://{i}" for i in cfg.allowed_extension_ids)}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not logging.getLogger().handlers:
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        pipe.start()
        write_private(server_json, json.dumps({"pid": os.getpid(), "port": cfg.port, "nonce": nonce,
                                               "started": time.time()}))
        try:
            yield
        finally:
            pipe.stop()
            try:
                if json.loads(server_json.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    server_json.unlink()
            except (OSError, ValueError):
                pass

    app = FastAPI(title=config.APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.store, app.state.pipe = cfg, store, pipe
    app.state.request_exit = exit_process
    log_lock = threading.Lock()

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return JSONResponse({"error": "origin not allowed"}, status_code=403)
        headers = {}
        if origin is not None:
            headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
            if request.method == "OPTIONS" and "access-control-request-method" in request.headers:
                headers.update({"Access-Control-Allow-Methods": CORS_METHODS,
                                "Access-Control-Allow-Headers": CORS_HEADERS, "Access-Control-Max-Age": "600"})
                if "access-control-request-private-network" in request.headers:
                    headers["Access-Control-Allow-Private-Network"] = "true"
                return Response(status_code=204, headers=headers)
        response = await call_next(request)
        response.headers.update(headers)
        return response

    @app.get("/health")
    def health():
        counts = store.counts()
        engine = roles.primary(cfg.language)
        return {"ok": True, "app": config.APP_ID, "version": config.VERSION, "api": config.API, "nonce": nonce,
                "engine": engine.name, "model": getattr(engine, "label", None) or engine.model, "language": cfg.language,
                "transcript": cfg.transcript, "second_hearing": cfg.second_hearing, "script": engine.script,
                "data_dir": str(cfg.data_dir), "notes_dir": str(cfg.data_dir / "notes"),
                "busy": counts["recording"] + counts["running"] > 0, "queue": counts}

    @app.post("/log")
    async def log_event(request: Request):
        body = await body_json(request)
        detail = " ".join(str(body.get("detail", "")).split())[:2000]
        line = (f"{time.strftime('%H:%M:%S')} [{str(body.get('src', '?'))[:40]}] "
                f"{str(body.get('stage', ''))[:80]} {detail}").rstrip()
        with log_lock, (cfg.logs_dir / "extension.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return {"ok": True}

    @app.get("/courses")
    def courses():
        return store.courses()

    @app.post("/sessions")
    async def create_session(request: Request):
        b = await body_json(request)
        client_id = b.get("client_session_id")
        if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 128:
            return JSONResponse({"error": "client_session_id is required"}, status_code=400)
        info = await run_in_threadpool(
            pipe.open_session, client_id, b.get("course"), b.get("lesson"), b.get("url", ""), b.get("speed", 1),
            b.get("language"), b.get("started_at"))
        return {"sid": info["sid"], "received_bytes": info["received_bytes"], "state": info["state"]}

    @app.get("/sessions/{sid}")
    def get_session(sid: str):
        session = store.get_session(sid)
        if session is None:
            return JSONResponse({"error": "unknown session"}, status_code=404)
        return pipe.session_info(session)

    @app.patch("/sessions/{sid}")
    async def patch_session(sid: str, request: Request):
        info = await run_in_threadpool(pipe.update_session_meta, sid, await body_json(request))
        if info is None:
            return JSONResponse({"error": "unknown session"}, status_code=404)
        return JSONResponse(info, status_code=409 if "sid" not in info else 200)

    @app.put("/sessions/{sid}/chunks/{seq}")
    async def put_chunk(sid: str, seq: int, request: Request):
        try:
            offset = int(request.headers.get("x-offset", ""))
            if offset < 0:
                raise ValueError
        except ValueError:
            return JSONResponse({"error": "X-Offset header is required"}, status_code=400)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_CHUNK_BYTES:
            return JSONResponse({"error": "chunk too large"}, status_code=413)
        body = bytearray()
        async for piece in request.stream():
            body += piece
            if len(body) > MAX_CHUNK_BYTES:
                return JSONResponse({"error": "chunk too large"}, status_code=413)
        code, out = await run_in_threadpool(pipe.append_chunk, sid, offset, bytes(body))
        return JSONResponse(out, status_code=code)

    @app.post("/sessions/{sid}/finish")
    async def finish(sid: str, request: Request):
        b = await body_json(request)
        total = b.get("total_bytes")
        reason = b.get("reason")
        out = await run_in_threadpool(pipe.finish_session, sid, False, total if isinstance(total, int) else None,
                                      reason if isinstance(reason, str) else None)
        if out is None:
            return JSONResponse({"error": "unknown session"}, status_code=404)
        return out

    @app.get("/jobs")
    def jobs(limit: int = 30):
        return pipe.list_views(max(1, min(limit, 200)))

    @app.get("/jobs/{job_id}")
    def job(job_id: str):
        return pipe.find_view(job_id) or {"status": "unknown"}

    @app.post("/jobs/{job_id}/retry")
    def retry(job_id: str):
        code, out = pipe.retry(job_id)
        return JSONResponse(out, status_code=code)

    @app.post("/transcribe")
    async def transcribe(audio: UploadFile = File(...), course: str = Form("Course"), lesson: str = Form("Lesson"),
                         url: str = Form(""), speed: float = Form(1.0)):
        """Old endpoint: the whole file in one request. Becomes a finished session."""
        suffix = Path(audio.filename or "").suffix.lower()
        ext = suffix if 2 <= len(suffix) <= 6 and suffix[1:].isalnum() else ".webm"
        info = await run_in_threadpool(pipe.open_session, "upload-" + uuid.uuid4().hex, course, lesson, url, speed,
                                       None, None, ext)
        part = pipe.abs(pipe.store.get_session(info["sid"])["part_path"])

        def save():
            with open(part, "wb") as f:
                while chunk := audio.file.read(1 << 20):
                    f.write(chunk)

        await run_in_threadpool(save)
        out = await run_in_threadpool(pipe.finish_session, info["sid"])
        if out["empty"]:
            return JSONResponse({"error": "empty audio"}, status_code=400)
        return {"job_id": out["job_id"]}

    @app.post("/admin/shutdown")
    def shutdown(request: Request, background: BackgroundTasks, force: int = 0):
        given = request.headers.get("x-admin-token", "")
        if not given or not hmac.compare_digest(given, admin_token):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        counts = store.counts()
        if not force and counts["recording"] + counts["running"] > 0:
            return JSONResponse({"busy": True}, status_code=409)
        background.add_task(app.state.request_exit)
        return {"ok": True, "stopping": True}

    @app.get("/library", response_class=HTMLResponse)
    def library():
        return LIBRARY_HTML

    if cfg.dev:
        root = config.REPO_ROOT
        ext_dir = root / "extension"

        @app.get("/dev/recorder-core.js")
        def recorder_core():
            path = ext_dir / "recorder-core.js"
            if not path.exists():
                return JSONResponse({"error": "not found"}, status_code=404)
            return Response(path.read_bytes(), media_type="text/javascript")

        app.mount("/dev/ext", StaticFiles(directory=ext_dir, check_dir=False), name="dev-ext")
        app.mount("/dev", StaticFiles(directory=root / "tools" / "testpage", html=True, check_dir=False), name="dev")

    return app


def __getattr__(name: str):
    """`uvicorn server:app` builds the app on first use, so importing this module has no side effects."""
    if name == "app":
        globals()["app"] = create_app()
        return globals()["app"]
    raise AttributeError(name)

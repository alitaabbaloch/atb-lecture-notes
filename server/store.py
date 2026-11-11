"""SQLite library: courses, lessons, sessions, jobs, settings. One connection per thread, WAL mode."""
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_ATTEMPTS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY, name TEXT, slug TEXT UNIQUE, created_at REAL);
CREATE TABLE IF NOT EXISTS lessons(id INTEGER PRIMARY KEY, course_id INTEGER, number INTEGER, title TEXT, slug TEXT,
  url TEXT, source TEXT DEFAULT 'v2', audio_path TEXT, md_path TEXT, json_path TEXT, notes_path TEXT,
  duration REAL, speed REAL DEFAULT 1, language TEXT, recorded_at TEXT, status TEXT, quality TEXT,
  created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS sessions(sid TEXT PRIMARY KEY, client_id TEXT UNIQUE, lesson_id INTEGER, state TEXT,
  part_path TEXT, received_bytes INTEGER DEFAULT 0, last_chunk_at REAL, recovered INTEGER DEFAULT 0, created_at REAL,
  stale_after REAL, finish_reason TEXT);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, lesson_id INTEGER, kind TEXT, state TEXT, stage TEXT,
  progress REAL, attempts INTEGER DEFAULT 0, error TEXT, created_at REAL, started_at REAL, finished_at REAL,
  meta TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS jobs_kind_state ON jobs(kind, state, created_at);
CREATE INDEX IF NOT EXISTS jobs_lesson ON jobs(lesson_id);
CREATE INDEX IF NOT EXISTS sessions_lesson ON sessions(lesson_id);
"""

LESSON_COLUMNS = {"course_id", "number", "title", "slug", "url", "audio_path", "md_path", "json_path",
                  "notes_path", "duration", "speed", "language", "recorded_at", "status", "quality"}
SESSION_COLUMNS = {"state", "part_path", "received_bytes", "last_chunk_at", "recovered", "stale_after",
                   "finish_reason"}
JOB_COLUMNS = {"state", "stage", "progress", "attempts", "error", "started_at", "finished_at", "meta"}


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self.conn().executescript(SCHEMA)
        columns = {r["name"] for r in self.conn().execute("PRAGMA table_info(sessions)")}
        for name, kind in (("stale_after", "REAL"), ("finish_reason", "TEXT")):  # databases from before these existed
            if name not in columns:
                self.conn().execute(f"ALTER TABLE sessions ADD COLUMN {name} {kind}")

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=5, isolation_level=None)  # autocommit; tx() opens transactions
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=5000")
            c.execute("PRAGMA journal_mode=WAL")
            self._local.c = c
        return c

    @contextmanager
    def tx(self):
        c = self.conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")

    def _update(self, table: str, key: str, value, allowed: set, fields: dict, c=None) -> None:
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unknown columns: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        (c or self.conn()).execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [*fields.values(), value])

    # courses
    def course_for(self, name: str, slug: str) -> sqlite3.Row:
        with self.tx() as c:
            row = c.execute("SELECT * FROM courses WHERE slug=?", (slug,)).fetchone()
            if row is None:
                cur = c.execute("INSERT INTO courses(name, slug, created_at) VALUES(?,?,?)", (name, slug, time.time()))
                row = c.execute("SELECT * FROM courses WHERE id=?", (cur.lastrowid,)).fetchone()
            return row

    def get_course(self, course_id: int):
        return self.conn().execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()

    def courses(self) -> list[dict]:
        rows = self.conn().execute(
            "SELECT c.id, c.name, c.slug, (SELECT COUNT(*) FROM lessons l WHERE l.course_id=c.id AND l.status!='empty')"
            " AS lessons FROM courses c ORDER BY c.name").fetchall()
        return [dict(r) for r in rows]

    # lessons
    def create_lesson(self, course_id: int, title: str, slug: str, url: str, speed: float, language: str,
                      recorded_at: str, source: str = "v2") -> int:
        now = time.time()
        cur = self.conn().execute(
            "INSERT INTO lessons(course_id, title, slug, url, source, speed, language, recorded_at, status,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (course_id, title, slug, url, source, speed, language, recorded_at, "recording", now, now))
        return cur.lastrowid

    def get_lesson(self, lesson_id: int):
        return self.conn().execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()

    def update_lesson(self, lesson_id: int, c=None, **fields) -> None:
        fields["updated_at"] = time.time()
        self._update("lessons", "id", lesson_id, LESSON_COLUMNS | {"updated_at"}, fields, c)

    def set_quality(self, lesson_id: int, quality: dict | None) -> None:
        self.update_lesson(lesson_id, quality=json.dumps(quality) if quality is not None else None)

    def recent_lessons(self, limit: int) -> list[sqlite3.Row]:
        return self.conn().execute(
            "SELECT * FROM lessons WHERE status!='empty' ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)).fetchall()

    # sessions
    def create_session(self, sid: str, client_id: str, lesson_id: int, part_path: str, stale_after: float | None = None,
                       state: str = "recording"):
        self.conn().execute(
            "INSERT INTO sessions(sid, client_id, lesson_id, state, part_path, received_bytes, created_at, stale_after)"
            " VALUES(?,?,?,?,?,0,?,?)", (sid, client_id, lesson_id, state, part_path, time.time(), stale_after))

    def get_session(self, sid: str):
        return self.conn().execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()

    def session_by_client(self, client_id: str):
        return self.conn().execute("SELECT * FROM sessions WHERE client_id=?", (client_id,)).fetchone()

    def session_for_lesson(self, lesson_id: int):
        return self.conn().execute("SELECT * FROM sessions WHERE lesson_id=?", (lesson_id,)).fetchone()

    def update_session(self, sid: str, c=None, **fields) -> None:
        self._update("sessions", "sid", sid, SESSION_COLUMNS, fields, c)

    def grant_grace(self, until: float) -> int:
        """Give every session still recording a deadline, so audio the browser still holds is not thrown away."""
        cur = self.conn().execute("UPDATE sessions SET stale_after=? WHERE state='recording'", (until,))
        return cur.rowcount

    def recording_sessions(self) -> list[sqlite3.Row]:
        return self.conn().execute("SELECT * FROM sessions WHERE state='recording'").fetchall()

    # jobs
    def create_job(self, lesson_id: int, kind: str, meta: dict | None = None, c=None) -> str:
        job_id = new_id()
        (c or self.conn()).execute(
            "INSERT INTO jobs(id, lesson_id, kind, state, attempts, created_at, meta) VALUES(?,?,?,?,0,?,?)",
            (job_id, lesson_id, kind, "queued", time.time(), json.dumps(meta or {})))
        return job_id

    def get_job(self, job_id: str):
        return self.conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def lesson_job(self, lesson_id: int, kind: str):
        return self.conn().execute(
            "SELECT * FROM jobs WHERE lesson_id=? AND kind=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (lesson_id, kind)).fetchone()

    def update_job(self, job_id: str, **fields) -> None:
        self._update("jobs", "id", job_id, JOB_COLUMNS, fields)

    def merge_meta(self, job_id: str, extra: dict) -> None:
        with self.tx() as c:
            row = c.execute("SELECT meta FROM jobs WHERE id=?", (job_id,)).fetchone()
            meta = json.loads(row["meta"] or "{}") if row else {}
            meta.update(extra)
            c.execute("UPDATE jobs SET meta=? WHERE id=?", (json.dumps(meta), job_id))

    def claim_job(self, kind):
        """Atomically turn the oldest queued job into a running one. `kind` is one kind or a tuple: with a tuple,
        every queued job of the first kind comes before any job of the next."""
        kinds = (kind,) if isinstance(kind, str) else tuple(kind)
        with self.tx() as c:
            for k in kinds:
                row = c.execute("SELECT * FROM jobs WHERE kind=? AND state='queued' ORDER BY created_at, rowid LIMIT 1",
                                (k,)).fetchone()
                if row is not None:
                    c.execute("UPDATE jobs SET state='running', started_at=?, finished_at=NULL, error=NULL WHERE id=?",
                              (time.time(), row["id"]))
                    return c.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            return None

    def requeue_running(self) -> tuple[int, int]:
        """After a crash: running jobs go back to queued; after MAX_ATTEMPTS they become errors."""
        requeued = failed = 0
        with self.tx() as c:
            for row in c.execute("SELECT id, attempts FROM jobs WHERE state='running'").fetchall():
                attempts = row["attempts"] + 1
                if attempts >= MAX_ATTEMPTS:
                    c.execute("UPDATE jobs SET state='error', attempts=?, finished_at=?, error=? WHERE id=?",
                              (attempts, time.time(), "interrupted too many times", row["id"]))
                    failed += 1
                else:
                    c.execute("UPDATE jobs SET state='queued', attempts=?, stage=NULL, progress=NULL WHERE id=?",
                              (attempts, row["id"]))
                    requeued += 1
        return requeued, failed

    def counts(self) -> dict:
        c = self.conn()
        one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "recording": one("SELECT COUNT(*) FROM sessions WHERE state='recording'"),
            "queued": one("SELECT COUNT(*) FROM jobs WHERE state='queued'"),
            "running": one("SELECT COUNT(*) FROM jobs WHERE state='running'"),
        }

    # settings
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn().execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=?",
                            (key, value, value))

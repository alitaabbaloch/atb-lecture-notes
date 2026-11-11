"""Sessions, finishing, job views, and the two worker threads (transcription with the second hearing, notes)."""
import importlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import audio
import engines as engine_roles
import for_ai
import glossary
import hearing
import transcriber
import writer
from store import Store, new_id

log = logging.getLogger("atb")

PROGRESS_CAP = 0.97
STALLED_SECONDS = 60


def clean(text, limit: int, default: str) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()[:limit]
    return text or default


def format_recorded(value) -> str:
    """The extension may send epoch milliseconds, epoch seconds or an ISO string. Local time, minute precision."""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value).strftime("%Y-%m-%d %H:%M")
        if isinstance(value, str) and value:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (dt.astimezone() if dt.tzinfo else dt).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OverflowError, OSError):
        pass
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class Pipeline:
    def __init__(self, cfg, store: Store, engines):
        if not hasattr(engines, "primary"):  # an old caller passed one engine object
            engines = engine_roles.Single(engines)
        self.cfg, self.store, self.engines = cfg, store, engines
        self.stop_event = threading.Event()
        self.wake_transcribe = threading.Event()
        self.wake_notes = threading.Event()
        self.threads: list[threading.Thread] = []
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def abs(self, rel: str) -> Path:
        return self.cfg.data_dir / rel

    def lock(self, sid: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(sid, threading.Lock())

    # ---- sessions -------------------------------------------------------------------------------------------

    def open_session(self, client_id: str, course: str, lesson: str, url: str = "", speed=1, language=None,
                     started_at=None, ext: str = ".webm") -> dict:
        existing = self.store.session_by_client(client_id)
        if existing:
            return self.session_info(existing)
        course = clean(course, 120, "Course")
        lesson = clean(lesson, 200, "Lesson")
        row = self.store.course_for(course, writer.slug(course))
        try:
            speed = min(4.0, max(0.25, float(speed)))
        except (TypeError, ValueError):
            speed = 1.0
        lang = self.cfg.language if language is None else clean(language, 16, "")
        sid = new_id() + new_id()
        part = f"recordings/{row['slug']}/{sid}{ext}.part"
        lesson_id = self.store.create_lesson(row["id"], lesson, writer.slug(lesson), clean(url, 500, ""), speed, lang,
                                             format_recorded(started_at))
        try:
            self.store.create_session(sid, client_id, lesson_id, part, time.time() + self.cfg.stale_session_seconds)
        except Exception:  # lost a race with a concurrent create for the same client id
            existing = self.store.session_by_client(client_id)
            if existing is None:
                raise
            return self.session_info(existing)
        self.abs(part).parent.mkdir(parents=True, exist_ok=True)
        return self.session_info(self.store.get_session(sid))

    def part_size(self, session) -> int:
        try:
            return self.abs(session["part_path"]).stat().st_size
        except (OSError, TypeError):
            return 0

    def session_info(self, session) -> dict:
        size = self.part_size(session) if session["state"] == "recording" else session["received_bytes"]
        return {"sid": session["sid"], "state": session["state"], "received_bytes": size,
                "lesson_id": session["lesson_id"]}

    def update_session_meta(self, sid: str, fields: dict):
        """PATCH: course, lesson, speed. Returns the session info, or None when unknown."""
        with self.lock(sid):
            session = self.store.get_session(sid)
            if session is None:
                return None
            if session["state"] != "recording":
                return {"state": session["state"]}
            changes = {}
            if "course" in fields:
                name = clean(fields["course"], 120, "Course")
                changes["course_id"] = self.store.course_for(name, writer.slug(name))["id"]
            if "lesson" in fields:
                title = clean(fields["lesson"], 200, "Lesson")
                changes.update(title=title, slug=writer.slug(title))
            if "speed" in fields:
                try:
                    changes["speed"] = min(4.0, max(0.25, float(fields["speed"])))
                except (TypeError, ValueError):
                    pass
            self.store.update_lesson(session["lesson_id"], **changes)
            return self.session_info(session)

    def append_chunk(self, sid: str, offset: int, body: bytes) -> tuple[int, dict]:
        """The .part file size is the truth. Returns (http status, json body)."""
        with self.lock(sid):
            session = self.store.get_session(sid)
            if session is None:
                return 404, {"error": "unknown session"}
            if session["state"] != "recording":
                return 409, {"state": session["state"]}
            path = self.abs(session["part_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            size = path.stat().st_size if path.exists() else 0
            if offset > size:
                return 409, {"received_bytes": size}
            rewound = offset < size
            if rewound:
                os.truncate(path, offset)
            with open(path, "ab") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            received = offset + len(body)
            now = time.time()
            self.store.update_session(sid, received_bytes=received, last_chunk_at=now,
                                      stale_after=now + self.cfg.stale_session_seconds)
            out = {"received_bytes": received}
            if rewound:
                out["rewound"] = True
            return 200, out

    def finish_session(self, sid: str, recovered: bool = False, total_bytes=None, reason: str | None = None):
        """Remux, mark finished, queue the transcription. Idempotent. Returns None for an unknown session."""
        with self.lock(sid):
            session = self.store.get_session(sid)
            if session is None:
                return None
            lesson_id = session["lesson_id"]
            if session["state"] != "recording":
                job = self.store.lesson_job(lesson_id, "transcribe")
                return {"job_id": job["id"] if job else None, "lesson_id": lesson_id,
                        "empty": self.store.get_lesson(lesson_id)["status"] == "empty"}
            part = self.abs(session["part_path"])
            size = part.stat().st_size if part.exists() else 0
            flags = {"recovered": 1} if recovered else {}
            if reason:
                flags["finish_reason"] = clean(reason, 40, "")
            if size == 0:
                with self.store.tx() as c:
                    self.store.update_session(sid, c, state="finished", received_bytes=0, **flags)
                    self.store.update_lesson(lesson_id, c, status="empty")
                part.unlink(missing_ok=True)
                return {"job_id": None, "lesson_id": lesson_id, "empty": True}
            if total_bytes is not None and total_bytes != size:
                log.warning("session %s: client says %s bytes, server has %s", sid, total_bytes, size)

            lesson = self.store.get_lesson(lesson_id)
            course = self.store.get_course(lesson["course_id"])
            ext = Path(part.name[:-len(".part")]).suffix or ".webm"
            stamp = datetime.fromtimestamp(lesson["created_at"]).strftime("%Y%m%d-%H%M%S")  # stable across retries
            dst = self.abs(f"recordings/{course['slug']}/{stamp}-{lesson['slug']}{ext}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            remuxed = ext == ".webm" and audio.remux_webm(part, dst)
            if not remuxed:
                os.replace(part, dst)  # keep the raw bytes rather than lose the lesson
            with self.store.tx() as c:
                self.store.update_session(sid, c, state="finished", received_bytes=size, **flags)
                self.store.update_lesson(lesson_id, c, status="queued", audio_path=str(dst.relative_to(self.cfg.data_dir)))
                job_id = self.store.create_job(lesson_id, "transcribe", c=c)
            if remuxed:
                part.unlink(missing_ok=True)
            self.wake_transcribe.set()
            return {"job_id": job_id, "lesson_id": lesson_id, "empty": False}

    def sweep_stale(self, now: float | None = None) -> int:
        """Finish sessions whose deadline (stale_after) has passed. Returns how many."""
        now = now or time.time()
        count = 0
        for s in self.store.recording_sessions():
            deadline = s["stale_after"] or (s["last_chunk_at"] or s["created_at"]) + self.cfg.stale_session_seconds
            if now > deadline:
                log.warning("finishing stale session %s", s["sid"])
                self.finish_session(s["sid"], recovered=True)
                count += 1
        return count

    # ---- job views ------------------------------------------------------------------------------------------

    def _speed_key(self, engine) -> str:
        return f"speed_factor:{engine.name}:{engine.model}"

    def speed_factor(self, engine) -> float:
        default = engine_roles.DEFAULT_SPEED.get(engine.name, 3.0)
        try:
            return float(self.store.get_setting(self._speed_key(engine), str(default)))
        except ValueError:
            return default

    def _record_speed(self, engine, audio_seconds: float, elapsed: float) -> None:
        if audio_seconds <= 0 or elapsed <= 0:
            return
        key = self._speed_key(engine)
        n = int(self.store.get_setting(key + ":n", "0"))
        new = (self.speed_factor(engine) * n + audio_seconds / elapsed) / (n + 1) if n else audio_seconds / elapsed
        self.store.set_setting(key, repr(new))
        self.store.set_setting(key + ":n", str(n + 1))

    def _lesson_engine(self, lesson):
        language = lesson["language"] if lesson["language"] is not None else self.cfg.language
        return self.engines.primary(language)

    def job_view(self, lesson) -> dict:
        s = self.store
        session = s.session_for_lesson(lesson["id"])
        tjob = s.lesson_job(lesson["id"], "transcribe")
        njob = s.lesson_job(lesson["id"], "notes")
        sjob = s.lesson_job(lesson["id"], "second")
        course = s.get_course(lesson["course_id"])
        now = time.time()
        status, stage, progress, estimated = lesson["status"], None, None, False
        if tjob is None:
            if status == "recording":
                stage = "recording"
        elif tjob["state"] == "queued":
            status, stage = "queued", "waiting"
        elif tjob["state"] == "running":
            status, stage = "transcribing", "transcribing"
            if tjob["progress"] is not None:  # real: seconds of speech blocks done over all block seconds
                progress = tjob["progress"]
            else:  # nothing reported yet: estimate from the measured speed of this engine and model
                estimated = True
                expected = (lesson["duration"] or 0) / self.speed_factor(self._lesson_engine(lesson))
                if expected > 0 and tjob["started_at"]:
                    progress = min(PROGRESS_CAP, max(0.0, (now - tjob["started_at"]) / expected))
        elif tjob["state"] == "error":
            status, stage = "error", "failed"
        elif njob is not None and njob["state"] in ("queued", "running"):
            status, stage, estimated, progress = "summarizing", "notes", True, PROGRESS_CAP
        else:
            status, stage, progress = "done", "done", 1.0
        started = tjob["started_at"] if tjob else None
        ends = [j["finished_at"] for j in (tjob, njob) if j is not None and j["finished_at"]]
        finished = max(ends) if status == "done" and ends else None
        if status == "recording" and session is not None:
            size = self.part_size(session)
        else:
            try:
                size = self.abs(lesson["audio_path"]).stat().st_size if lesson["audio_path"] else 0
            except OSError:
                size = 0
        meta = json.loads(tjob["meta"] or "{}") if tjob else {}
        return {
            "id": tjob["id"] if tjob else (session["sid"] if session else str(lesson["id"])),
            "lesson_id": lesson["id"], "course": course["name"] if course else "", "lesson": lesson["title"],
            "url": lesson["url"] or "", "created": lesson["recorded_at"], "status": status, "stage": stage,
            "progress": progress, "progress_estimated": estimated, "size_mb": round(size / 1e6, 2),
            "started": started, "finished": finished,
            "seconds": round(finished - started, 1) if finished and started else None,
            "output": lesson["md_path"], "notes": lesson["notes_path"], "preview": meta.get("preview"),
            "error": tjob["error"] if tjob and tjob["state"] == "error" else None,
            "notes_error": njob["error"] if njob else None,
            "second": None if sjob is None else {
                "state": "done" if sjob["state"] == "error" else sjob["state"],  # a second pass never counts as an error
                "progress": sjob["progress"], "error": sjob["error"]},
            "recovered": bool(session and (session["recovered"] or session["finish_reason"] == "recovered")),
            "sid": session["sid"] if session else None,
            "quality": json.loads(lesson["quality"]) if lesson["quality"] else None,
            "stalled": bool(status == "recording" and session is not None
                            and now - (session["last_chunk_at"] or session["created_at"]) > STALLED_SECONDS),
        }

    def find_view(self, ident: str):
        job = self.store.get_job(ident)
        if job is not None and job["kind"] == "transcribe":
            return self.job_view(self.store.get_lesson(job["lesson_id"]))
        session = self.store.get_session(ident)
        if session is not None:
            return self.job_view(self.store.get_lesson(session["lesson_id"]))
        return None

    def list_views(self, limit: int) -> list[dict]:
        return [self.job_view(l) for l in self.store.recent_lessons(limit)]

    def retry(self, ident: str):
        """Returns (code, body). Only transcription errors can be retried."""
        view = self.find_view(ident)
        if view is None:
            return 404, {"status": "unknown"}
        if view["status"] != "error":
            return 409, {"status": view["status"]}
        self.store.update_job(view["id"], state="queued", attempts=0, error=None, stage=None, progress=None,
                              finished_at=None)
        self.wake_transcribe.set()
        return 200, self.find_view(view["id"])

    # ---- workers --------------------------------------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        for target in (self._transcribe_loop, self._notes_loop, self._sweep_loop):
            t = threading.Thread(target=target, daemon=True, name=target.__name__)
            t.start()
            self.threads.append(t)

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_transcribe.set()
        self.wake_notes.set()
        for t in self.threads:
            t.join(timeout=2)

    def recover(self) -> None:
        requeued, failed = self.store.requeue_running()
        waiting = self.store.grant_grace(time.time() + self.cfg.startup_grace_seconds)
        if requeued or failed or waiting:
            log.info("recovery: %s jobs requeued, %s failed, %s open sessions given a grace period",
                     requeued, failed, waiting)

    def _sweep_loop(self) -> None:
        while not self.stop_event.wait(60):
            try:
                self.sweep_stale()
            except Exception:  # noqa: BLE001
                log.exception("stale session sweep failed")

    def _loop(self, kind, wake: threading.Event, run) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.store.claim_job(kind)
            except Exception:  # noqa: BLE001
                log.exception("could not claim a %s job", kind)
                job = None
            if job is None:
                wake.wait(2.0)
                wake.clear()
                continue
            try:
                run(job)
            except Exception as e:  # noqa: BLE001
                log.exception("%s job %s failed", kind, job["id"])
                self.store.update_job(job["id"], state="error", error=f"{type(e).__name__}: {e}"[:500],
                                      finished_at=time.time())

    def _transcribe_loop(self) -> None:
        self._loop(("transcribe", "second"), self.wake_transcribe, self._run_transcription_job)

    def _notes_loop(self) -> None:
        self._loop("notes", self.wake_notes, self._run_notes)

    def _run_transcription_job(self, job) -> None:
        (self._run_second if job["kind"] == "second" else self._run_transcribe)(job)

    def _run_transcribe(self, job) -> None:
        lesson = self.store.get_lesson(job["lesson_id"])
        course = self.store.get_course(lesson["course_id"])
        audio_file = self.abs(lesson["audio_path"])
        t0 = time.time()
        seconds = audio.duration_seconds(audio_file)
        if seconds:
            self.store.update_lesson(lesson["id"], duration=seconds)
        self.store.update_job(job["id"], stage="transcribing")
        language = lesson["language"] if lesson["language"] is not None else self.cfg.language
        speed = lesson["speed"] or 1.0
        gloss = self.course_glossary(course)
        engine = self.engines.primary(language)
        opts = transcriber.Options(language=language or None, speed=speed, tempo_restore=self.cfg.tempo_restore,
                                   repair=self.cfg.repair,
                                   initial_prompt=gloss.prompt() if gloss and self.cfg.glossary_prompt else None)
        work = self.cfg.app_dir / "work" / str(lesson["id"])
        result = transcriber.transcribe_lesson(audio_file, engine, opts, work_dir=work,
                                               on_progress=lambda p: self.store.update_job(job["id"], progress=p))
        if gloss:
            try:
                result = glossary.apply_to_result(result, gloss)
            except Exception as e:  # noqa: BLE001  a glossary problem must not lose the transcript
                log.warning("glossary failed for lesson %s: %s", lesson["id"], e)
        if engine.script:
            result["script"] = engine.script
        meta = {"url": lesson["url"], "recorded": lesson["recorded_at"], "duration": seconds,
                "model": getattr(engine, "label", None) or engine.model, "language": result.get("language") or language,
                "audio": lesson["audio_path"], "speed": lesson["speed"], "engine": engine.name}
        md, js, number = writer.write_transcript(
            self.cfg.data_dir, course["name"], lesson["title"], result, meta,
            existing_md=lesson["md_path"] or self.find_transcript(course["slug"], lesson["audio_path"]))
        self.store.update_lesson(lesson["id"], md_path=md, json_path=js, number=number,
                                 language=result.get("language") or language)
        self.store.set_quality(lesson["id"], result.get("quality"))
        self._write_for_ai(md, result, None, gloss)
        shutil.rmtree(work, ignore_errors=True)
        self.store.merge_meta(job["id"], {"preview": result.get("text", "").strip()[:400]})
        elapsed = time.time() - t0
        if seconds:
            self._record_speed(engine, seconds, elapsed)
        wants_second = self.cfg.second_hearing and getattr(engine, "script", None) == "roman"
        with self.store.tx() as c:
            if wants_second:
                pending = self.store.lesson_job(lesson["id"], "second")
                if pending is None or pending["state"] not in ("queued", "running"):
                    self.store.create_job(lesson["id"], "second", c=c)
            elif self.cfg.auto_notes:
                self.store.create_job(lesson["id"], "notes", c=c)
            self.store.update_lesson(lesson["id"], c, status="done")
            c.execute("UPDATE jobs SET state='done', stage='done', progress=1, finished_at=? WHERE id=?",
                      (time.time(), job["id"]))
        if wants_second:
            self.wake_transcribe.set()
        elif self.cfg.auto_notes:
            self.wake_notes.set()

    def _write_for_ai(self, md: str, result: dict, second, gloss) -> None:
        """The "for AI" file next to the transcript. A failure is logged and ignored."""
        try:
            for_ai.write(self.abs(md), result, second, gloss.terms if gloss else ())
        except Exception as e:  # noqa: BLE001
            log.warning("could not write the for-AI file for %s: %s", md, e)

    def _run_second(self, job) -> None:
        """The second hearing never fails the lesson: the reason is recorded and the job is marked done."""
        lesson = self.store.get_lesson(job["lesson_id"])
        error = None
        try:
            self._second_pass(job, lesson)
        except Exception as e:  # noqa: BLE001
            log.warning("second hearing failed for lesson %s: %s", lesson["id"], e)
            error = str(e)[:300] or type(e).__name__
        self.store.update_job(job["id"], state="done", stage="done", finished_at=time.time(), error=error,
                              **({"progress": 1.0} if error is None else {}))
        if self.cfg.auto_notes and lesson["md_path"]:
            self.store.create_job(lesson["id"], "notes")
            self.wake_notes.set()

    def _second_pass(self, job, lesson) -> None:
        course = self.store.get_course(lesson["course_id"])
        if not lesson["json_path"] or not self.abs(lesson["json_path"]).exists():
            raise RuntimeError("the first transcript is missing")
        t0 = time.time()
        engine = self.engines.second()
        language = lesson["language"] if lesson["language"] is not None else self.cfg.language
        gloss = self.course_glossary(course)
        opts = transcriber.Options(language=language or None, speed=lesson["speed"] or 1.0,
                                   tempo_restore=self.cfg.tempo_restore, repair=self.cfg.repair,
                                   initial_prompt=gloss.prompt() if gloss and self.cfg.glossary_prompt else None)
        self.store.update_job(job["id"], stage="transcribing")
        work = self.cfg.app_dir / "work" / f"{lesson['id']}-second"
        second = transcriber.transcribe_lesson(self.abs(lesson["audio_path"]), engine, opts, work_dir=work,
                                               on_progress=lambda p: self.store.update_job(job["id"], progress=p))
        if gloss:
            try:
                second = glossary.apply_to_result(second, gloss)
            except Exception as e:  # noqa: BLE001
                log.warning("glossary failed for the second hearing of lesson %s: %s", lesson["id"], e)
        md = self.abs(lesson["md_path"])
        primary = json.loads(self.abs(lesson["json_path"]).read_text(encoding="utf-8"))
        primary, filled = hearing.fill(primary, second)
        writer.write_second(md, second)
        if filled:
            settings = primary.get("settings") or {}
            meta = {"url": lesson["url"], "recorded": lesson["recorded_at"], "duration": lesson["duration"],
                    "model": settings.get("model"), "language": primary.get("language") or language,
                    "audio": lesson["audio_path"], "speed": lesson["speed"], "engine": settings.get("engine")}
            writer.write_transcript(self.cfg.data_dir, course["name"], lesson["title"], primary, meta,
                                    existing_md=lesson["md_path"])
            self.store.set_quality(lesson["id"], primary["quality"])
        self._write_for_ai(lesson["md_path"], primary, second, gloss)
        shutil.rmtree(work, ignore_errors=True)
        if lesson["duration"]:
            self._record_speed(engine, lesson["duration"], time.time() - t0)

    def course_glossary(self, course) -> "glossary.Glossary | None":
        """The course glossary (copied from the starter on first use), or None when the glossary is off."""
        if not self.cfg.glossary:
            return None
        try:
            path = glossary.ensure_course_glossary(self.cfg.data_dir, writer.slug(course["name"]),
                                                   self.cfg.starter_glossary)
            return glossary.load(path)
        except Exception as e:  # noqa: BLE001
            log.warning("could not load the course glossary: %s", e)
            return None

    def find_transcript(self, course_slug: str, audio_path: str) -> str | None:
        """A transcript already written for this audio file (a crash happened before the database was updated)."""
        for md in sorted((self.cfg.data_dir / "notes" / course_slug).glob("*.md")):
            if md.name.endswith(("-notes.md", "-for-ai.md", ".urdu.md")):
                continue
            try:
                head = md.read_text(encoding="utf-8").split("\n---", 1)[0]
            except OSError:
                continue
            if f"\naudio: {audio_path}\n" in head + "\n":
                return str(md.relative_to(self.cfg.data_dir))
        return None

    def notes_addon(self):
        """The optional notes add-on module, or None when it is not installed. Imported by name so the core holds no cloud AI import."""
        folder = str(Path(__file__).resolve().parent.parent / "addons" / "notes")
        if folder not in sys.path:
            sys.path.append(folder)
        try:
            llm = importlib.import_module("llm")
            summarize = importlib.import_module("summarize")
        except ImportError:
            return None
        llm.set_provider(self.cfg.notes_provider)
        return summarize

    def _run_notes(self, job) -> None:
        """A notes failure never fails the lesson: the reason is recorded and the job is marked done."""
        lesson = self.store.get_lesson(job["lesson_id"])
        summarize = self.notes_addon()
        if summarize is None:
            self.store.update_job(job["id"], state="done", stage="done", finished_at=time.time(),
                                  error="the notes add-on is not installed")
            return
        try:
            out = summarize.summarize(self.abs(lesson["md_path"]))
            self.store.update_lesson(lesson["id"], notes_path=str(out.relative_to(self.cfg.data_dir)))
            self.store.update_job(job["id"], state="done", stage="done", finished_at=time.time())
        except Exception as e:  # noqa: BLE001
            log.warning("notes failed for lesson %s: %s", lesson["id"], e)
            self.store.update_job(job["id"], state="done", stage="done", finished_at=time.time(),
                                  error=str(e)[:300])

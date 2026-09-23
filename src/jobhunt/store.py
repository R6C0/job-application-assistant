"""SQLite persistence.

One file, no ORM, no migration framework. The schema is small enough that
`CREATE TABLE IF NOT EXISTS` plus an explicit version row is less machinery than
a migration tool and easier to reason about at 2am.

Writes are idempotent on `Job.id`, which is what makes the pipeline safe to run
on a timer: a rerun that sees the same posting updates the row and does not
produce a second notification.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from .models import (
    Application,
    CompanyBrief,
    Job,
    Letter,
    Match,
    Reason,
    Stage,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    dedupe_key   TEXT NOT NULL,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    company      TEXT NOT NULL,
    url          TEXT NOT NULL,
    payload      TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs (dedupe_key);

CREATE TABLE IF NOT EXISTS applications (
    job_id       TEXT PRIMARY KEY REFERENCES jobs (id),
    stage        TEXT NOT NULL,
    score        REAL,
    match_json   TEXT,
    brief_json   TEXT,
    letter_json  TEXT,
    notes        TEXT NOT NULL DEFAULT '',
    notified_at  TEXT,
    submitted_at TEXT,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_apps_stage ON applications (stage);

CREATE TABLE IF NOT EXISTS notifications (
    job_id  TEXT NOT NULL,
    sent_on TEXT NOT NULL,
    PRIMARY KEY (job_id, sent_on)
);
"""


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: str | Path = "jobhunt.db") -> None:
        self.path = Path(path)
        # check_same_thread=False because FastAPI runs sync endpoints in a
        # thread pool, so the review interface touches this connection from a
        # different thread than the one that opened it. The lock below is what
        # makes that safe: sqlite3 allows cross-thread use but does not
        # serialise it for you.
        self._conn = sqlite3.connect(self.path, detect_types=0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------
    def upsert_job(self, job: Job) -> bool:
        """Store a job. Returns True if this is the first time we have seen it."""
        payload = json.dumps(
            {
                **{k: v for k, v in asdict(job).items() if not isinstance(v, datetime)},
                "posted_at": _dt(job.posted_at),
                "fetched_at": _dt(job.fetched_at),
            }
        )
        now = datetime.now().isoformat()

        with self._tx() as conn:
            existing = conn.execute("SELECT id FROM jobs WHERE id = ?", (job.id,)).fetchone()
            if existing:
                conn.execute(
                    "UPDATE jobs SET payload = ?, last_seen = ? WHERE id = ?",
                    (payload, now, job.id),
                )
                return False

            conn.execute(
                """INSERT INTO jobs
                   (id, dedupe_key, source, external_id, title, company, url,
                    payload, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job.id,
                    job.dedupe_key,
                    job.source,
                    job.external_id,
                    job.title,
                    job.company,
                    job.url,
                    payload,
                    now,
                    now,
                ),
            )
            return True

    def seen_dedupe_key(self, key: str, excluding_job_id: str) -> bool:
        """Has the same role already arrived from another source?"""
        row = self._query_one(
            "SELECT 1 FROM jobs WHERE dedupe_key = ? AND id != ? LIMIT 1",
            (key, excluding_job_id),
        )
        return row is not None

    def get_job(self, job_id: str) -> Job | None:
        row = self._query_one("SELECT payload FROM jobs WHERE id = ?", (job_id,))
        if not row:
            return None
        data = json.loads(row["payload"])
        data["posted_at"] = _parse_dt(data.get("posted_at"))
        data["fetched_at"] = _parse_dt(data.get("fetched_at")) or datetime.now()
        return Job(**data)

    # ------------------------------------------------------------------
    # Applications
    # ------------------------------------------------------------------
    def save_application(self, app: Application) -> None:
        match_json = (
            json.dumps(
                {
                    "score": app.match.score,
                    "reasons": [asdict(r) for r in app.match.reasons],
                    "matched_skills": app.match.matched_skills,
                    "missing_skills": app.match.missing_skills,
                    "blockers": app.match.blockers,
                }
            )
            if app.match
            else None
        )
        brief_json = json.dumps(asdict(app.brief)) if app.brief else None
        letter_json = (
            json.dumps(
                {
                    "body": app.letter.body,
                    "evidence_used": app.letter.evidence_used,
                    "generator": app.letter.generator,
                    "edited_by_human": app.letter.edited_by_human,
                    "created_at": _dt(app.letter.created_at),
                }
            )
            if app.letter
            else None
        )

        with self._tx() as conn:
            conn.execute(
                """INSERT INTO applications
                   (job_id, stage, score, match_json, brief_json, letter_json,
                    notes, notified_at, submitted_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (job_id) DO UPDATE SET
                     stage = excluded.stage,
                     score = excluded.score,
                     match_json = excluded.match_json,
                     brief_json = excluded.brief_json,
                     letter_json = excluded.letter_json,
                     notes = excluded.notes,
                     notified_at = excluded.notified_at,
                     submitted_at = excluded.submitted_at,
                     updated_at = excluded.updated_at""",
                (
                    app.job.id,
                    app.stage.value,
                    app.match.score if app.match else None,
                    match_json,
                    brief_json,
                    letter_json,
                    app.notes,
                    _dt(app.notified_at),
                    _dt(app.submitted_at),
                    datetime.now().isoformat(),
                ),
            )

    def get_application(self, job_id: str) -> Application | None:
        row = self._query_one("SELECT * FROM applications WHERE job_id = ?", (job_id,))
        job = self.get_job(job_id)
        if not row or not job:
            return None
        return self._hydrate(row, job)

    def list_applications(
        self, stage: Stage | None = None, limit: int = 100
    ) -> list[Application]:
        if stage:
            rows = self._query(
                """SELECT a.* FROM applications a
                   WHERE a.stage = ?
                   ORDER BY a.score DESC NULLS LAST, a.updated_at DESC
                   LIMIT ?""",
                (stage.value, limit),
            )
        else:
            rows = self._query(
                """SELECT a.* FROM applications a
                   ORDER BY a.updated_at DESC LIMIT ?""",
                (limit,),
            )

        out: list[Application] = []
        for row in rows:
            job = self.get_job(row["job_id"])
            if job:
                out.append(self._hydrate(row, job))
        return out

    def _hydrate(self, row: sqlite3.Row, job: Job) -> Application:
        match = None
        if row["match_json"]:
            data = json.loads(row["match_json"])
            match = Match(
                score=data["score"],
                reasons=[Reason(**r) for r in data["reasons"]],
                matched_skills=data["matched_skills"],
                missing_skills=data["missing_skills"],
                blockers=data.get("blockers", []),
            )

        brief = CompanyBrief(**json.loads(row["brief_json"])) if row["brief_json"] else None

        letter = None
        if row["letter_json"]:
            data = json.loads(row["letter_json"])
            letter = Letter(
                body=data["body"],
                evidence_used=data["evidence_used"],
                generator=data["generator"],
                edited_by_human=data["edited_by_human"],
                created_at=_parse_dt(data["created_at"]) or datetime.now(),
            )

        return Application(
            job=job,
            stage=Stage(row["stage"]),
            match=match,
            brief=brief,
            letter=letter,
            notified_at=_parse_dt(row["notified_at"]),
            submitted_at=_parse_dt(row["submitted_at"]),
            notes=row["notes"] or "",
        )

    # ------------------------------------------------------------------
    # Notification budget
    # ------------------------------------------------------------------
    def record_notification(self, job_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notifications (job_id, sent_on) VALUES (?, ?)",
                (job_id, date.today().isoformat()),
            )

    def notifications_today(self) -> int:
        row = self._query_one(
            "SELECT COUNT(*) AS n FROM notifications WHERE sent_on = ?",
            (date.today().isoformat(),),
        )
        return int(row["n"])

    def already_notified(self, job_id: str) -> bool:
        row = self._query_one(
            "SELECT 1 FROM notifications WHERE job_id = ? LIMIT 1", (job_id,)
        )
        return row is not None

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------
    def counts_by_stage(self) -> dict[str, int]:
        rows = self._query("SELECT stage, COUNT(*) AS n FROM applications GROUP BY stage")
        return {row["stage"]: int(row["n"]) for row in rows}

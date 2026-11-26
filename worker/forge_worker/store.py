"""SQLite-backed job store. States: queued -> running -> done | failed | cancelled."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

JobState = Literal["queued", "running", "done", "failed", "cancelled"]
FINAL_STATES: frozenset[str] = frozenset({"done", "failed", "cancelled"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    params TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued','running','done','failed','cancelled')),
    stages TEXT NOT NULL,
    completed_stages TEXT NOT NULL DEFAULT '[]',
    stage TEXT,
    stage_progress REAL NOT NULL DEFAULT 0 CHECK (stage_progress BETWEEN 0 AND 1),
    progress REAL NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 1),
    message TEXT NOT NULL DEFAULT '',
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    updated_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs(state, created_at);
CREATE TABLE IF NOT EXISTS job_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_logs_job ON job_logs(job_id, id);
"""


@dataclass
class Job:
    id: str
    kind: str
    name: str
    params: dict[str, Any]
    state: JobState
    stages: list[str]
    completed_stages: list[str] = field(default_factory=list)
    stage: str | None = None
    stage_progress: float = 0.0
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    cancel_requested: bool = False
    created_at: float = 0.0
    started_at: float | None = None
    updated_at: float = 0.0
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        params=json.loads(row["params"]),
        state=row["state"],
        stages=json.loads(row["stages"]),
        completed_stages=json.loads(row["completed_stages"]),
        stage=row["stage"],
        stage_progress=row["stage_progress"],
        progress=row["progress"],
        message=row["message"],
        error=row["error"],
        cancel_requested=bool(row["cancel_requested"]),
        created_at=row["created_at"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


class JobStore:
    """All access goes through one lock; SQLite in WAL mode handles the rest."""

    def __init__(self, db_path: Path) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(self, kind: str, name: str, params: dict[str, Any], stages: list[str]) -> Job:
        now = time.time()
        job_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, kind, name, params, state, stages, created_at, updated_at, message)"
                " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, 'Waiting in queue')",
                (job_id, kind, name, json.dumps(params, sort_keys=True), json.dumps(stages), now, now),
            )
            self._conn.commit()
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def next_queued(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return _row_to_job(row) if row else None

    _UPDATABLE = frozenset(
        {
            "state",
            "completed_stages",
            "stage",
            "stage_progress",
            "progress",
            "message",
            "error",
            "cancel_requested",
            "started_at",
            "finished_at",
        }
    )

    def update(self, job_id: str, **fields: Any) -> Job:
        unknown = set(fields) - self._UPDATABLE
        if unknown:
            raise ValueError(f"cannot update fields: {sorted(unknown)}")
        if "completed_stages" in fields:
            fields["completed_stages"] = json.dumps(fields["completed_stages"])
        if "cancel_requested" in fields:
            fields["cancel_requested"] = int(bool(fields["cancel_requested"]))
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
            self._conn.commit()
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def log(self, job_id: str, message: str, level: str = "info") -> dict[str, Any]:
        ts = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO job_logs (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
                (job_id, ts, level, message),
            )
            self._conn.commit()
        return {"id": cur.lastrowid, "job_id": job_id, "ts": ts, "level": level, "message": message}

    def logs(self, job_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, job_id, ts, level, message FROM job_logs WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def requeue_interrupted(self) -> list[str]:
        """Jobs left 'running' by a crash or app close go back to the queue (resume)."""
        with self._lock:
            rows = self._conn.execute("SELECT id FROM jobs WHERE state = 'running'").fetchall()
            ids = [r["id"] for r in rows]
            self._conn.execute(
                "UPDATE jobs SET state = 'queued', message = 'Resuming after restart', updated_at = ?"
                " WHERE state = 'running'",
                (time.time(),),
            )
            self._conn.commit()
        return ids

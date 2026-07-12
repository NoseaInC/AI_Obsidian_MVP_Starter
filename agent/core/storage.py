from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
  payload_json TEXT NOT NULL, result_json TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_sets (
  change_set_id TEXT PRIMARY KEY, prepared_id TEXT, state TEXT NOT NULL,
  bundle_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY, role TEXT NOT NULL, path TEXT NOT NULL,
  status TEXT NOT NULL, review_state TEXT, content_hash TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
  entity_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mastery_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
  old_mastery INTEGER, new_mastery INTEGER NOT NULL, confirmed INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes (
  quiz_id TEXT PRIMARY KEY, artifact_id TEXT, state TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, artifact_id TEXT,
  due_date TEXT, score REAL NOT NULL, state TEXT NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id TEXT PRIMARY KEY, state TEXT NOT NULL, journal_path TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def close(self) -> None:
        with self.lock: self.connection.close()

    def create_job(self, kind: str, payload: dict[str, Any]) -> str:
        job_id, now = uuid.uuid4().hex, _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO jobs VALUES (?, ?, 'queued', ?, NULL, NULL, ?, ?)",
                (job_id, kind, json.dumps(payload, ensure_ascii=False), now, now),
            )
            self.connection.commit(); return job_id

    def update_job(self, job_id: str, state: str, *, result: Any = None, error: str | None = None) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE jobs SET state=?, result_json=?, error=?, updated_at=? WHERE job_id=?",
                (state, json.dumps(result, ensure_ascii=False) if result is not None else None, error, _now(), job_id),
            )
            self.connection.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.connection.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def next_queued(self) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
            return dict(row) if row else None

    def recover_interrupted(self) -> int:
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE jobs SET state='queued', error='recovered after interrupted runtime', updated_at=? WHERE state='running'",
                (_now(),),
            )
            self.connection.commit(); return cursor.rowcount

    def audit(self, event: str, entity_id: str, details: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO audit(event, entity_id, details_json, created_at) VALUES (?, ?, ?, ?)",
                (event, entity_id, json.dumps(details, ensure_ascii=False), _now()),
            )
            self.connection.commit()

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .contracts import AgentRunSnapshot, InlineConfirmation


SCHEMA = """
CREATE TABLE IF NOT EXISTS assistant_framework_runs (
  run_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  model TEXT NOT NULL,
  request_json TEXT NOT NULL,
  status TEXT NOT NULL,
  message_history_json TEXT NOT NULL DEFAULT '{}',
  deferred_requests_json TEXT,
  confirmation_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_assistant_framework_runs_conversation
  ON assistant_framework_runs(conversation_id, updated_at);

CREATE TABLE IF NOT EXISTS assistant_framework_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence),
  FOREIGN KEY(run_id) REFERENCES assistant_framework_runs(run_id)
    ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assistant_framework_events_run
  ON assistant_framework_events(run_id, sequence);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RuntimePersistence:
    """Persist runtime state while keeping prompts/history in Local-Only files."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.private_root = (Path(store.path).parent / "assistant-runtime").resolve()
        self.private_root.mkdir(parents=True, exist_ok=True)
        with self.store.lock:
            self.store.connection.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in self.store.connection.execute(
                    "PRAGMA table_info(assistant_framework_runs)"
                ).fetchall()
            }
            for name, declaration in (
                ("checkpoint_id", "TEXT NOT NULL DEFAULT ''"),
                ("last_event_sequence", "INTEGER NOT NULL DEFAULT 0"),
                ("parent_run_id", "TEXT"),
                ("forked_from_sequence", "INTEGER"),
            ):
                if name not in columns:
                    self.store.connection.execute(
                        f"ALTER TABLE assistant_framework_runs ADD COLUMN {name} {declaration}"
                    )
            # A process can exit after claiming a confirmation but before the
            # deferred PydanticAI run resumes. The durable deferred payload is
            # sufficient to retry safely after restart.
            self.store.connection.execute(
                "UPDATE assistant_framework_runs SET status='waiting_confirmation' "
                "WHERE status='resuming' AND deferred_requests_json IS NOT NULL"
            )
            self.store.connection.commit()

    def claim_confirmation(self, run_id: str) -> bool:
        """Atomically grant one resumer ownership of a pending confirmation."""
        with self.store.lock:
            cursor = self.store.connection.execute(
                "UPDATE assistant_framework_runs SET status='resuming', updated_at=? "
                "WHERE run_id=? AND status='waiting_confirmation'",
                (_now(), run_id),
            )
            self.store.connection.commit()
        return cursor.rowcount == 1

    def _write_private(self, run_id: str, name: str, value: str) -> str:
        if not run_id.startswith("run-") or not run_id.removeprefix("run-").isalnum():
            raise ValueError("assistant_run_id_invalid")
        run_root = (self.private_root / run_id).resolve()
        if not run_root.is_relative_to(self.private_root) or run_root.is_symlink():
            raise PermissionError("assistant_runtime_path_escape")
        run_root.mkdir(parents=True, exist_ok=True)
        target = (run_root / f"{name}.json").resolve()
        if not target.is_relative_to(run_root) or target.is_symlink():
            raise PermissionError("assistant_runtime_path_escape")
        temporary = run_root / f".{name}.{uuid.uuid4().hex}.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return json.dumps(
            {
                "private_path": str(target.relative_to(Path(self.store.path).parent)),
                "sha256": digest,
                "chars": len(value),
            },
            ensure_ascii=False,
        )

    def _read_private(self, reference_json: str | None, fallback: str) -> str:
        if not reference_json:
            return fallback
        try:
            reference = json.loads(reference_json)
        except json.JSONDecodeError:
            # Compatibility with an interrupted pre-migration development run.
            return reference_json
        if not isinstance(reference, dict) or "private_path" not in reference:
            return reference_json
        relative = str(reference.get("private_path") or "")
        root = self.private_root.resolve()
        path = (Path(self.store.path).parent / relative).resolve()
        if (
            not relative
            or not path.is_relative_to(root)
            or not path.is_file()
            or path.is_symlink()
        ):
            raise RuntimeError("assistant_runtime_private_state_unavailable")
        value = path.read_text(encoding="utf-8")
        expected_hash = str(reference.get("sha256") or "")
        expected_chars = int(reference.get("chars", -1))
        if (
            len(value) != expected_chars
            or hashlib.sha256(value.encode("utf-8")).hexdigest() != expected_hash
        ):
            raise RuntimeError("assistant_runtime_private_state_integrity_failed")
        return value

    def create_run(
        self,
        run_id: str,
        conversation_id: str,
        profile_id: str,
        model: str,
        request: dict[str, Any],
        parent_run_id: str | None = None,
        forked_from_sequence: int | None = None,
    ) -> None:
        now = _now()
        request_ref = self._write_private(
            run_id,
            "request",
            json.dumps(request, ensure_ascii=False),
        )
        empty_history_ref = self._write_private(run_id, "history", "[]")
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO assistant_framework_runs(
                     run_id, conversation_id, profile_id, model,
                     request_json, status, message_history_json,
                     created_at, updated_at, checkpoint_id,
                     parent_run_id, forked_from_sequence
                   ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    conversation_id,
                    profile_id,
                    model,
                    request_ref,
                    empty_history_ref,
                    now,
                    now,
                    f"checkpoint-{uuid.uuid4().hex}",
                    parent_run_id,
                    forked_from_sequence,
                ),
            )
            self.store.connection.commit()

    def update_checkpoint(self, run_id: str, checkpoint_id: str) -> None:
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE assistant_framework_runs SET checkpoint_id=?, updated_at=? WHERE run_id=?",
                (checkpoint_id, _now(), run_id),
            )
            self.store.connection.commit()

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence "
                "FROM assistant_framework_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row["sequence"] or 0) + 1
            value = {
                "schemaVersion": 3,
                "seq": sequence,
                "type": event_type,
                "runId": run_id,
                **payload,
            }
            self.store.connection.execute(
                """INSERT INTO assistant_framework_events(
                     id, run_id, sequence, event_type,
                     payload_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"afe-{uuid.uuid4().hex}",
                    run_id,
                    sequence,
                    event_type,
                    json.dumps(value, ensure_ascii=False),
                    now,
                ),
            )
            self.store.connection.execute(
                "UPDATE assistant_framework_runs SET updated_at=?, last_event_sequence=? WHERE run_id=?",
                (now, sequence, run_id),
            )
            self.store.connection.commit()
        return value

    def save_history(self, run_id: str, history_json: bytes | str) -> None:
        value = history_json.decode("utf-8") if isinstance(history_json, bytes) else history_json
        history_ref = self._write_private(run_id, "history", value)
        with self.store.lock:
            self.store.connection.execute(
                """UPDATE assistant_framework_runs
                   SET message_history_json=?, updated_at=?
                   WHERE run_id=?""",
                (history_ref, _now(), run_id),
            )
            self.store.connection.commit()

    def save_pending(
        self,
        run_id: str,
        history_json: bytes | str,
        deferred_json: bytes | str,
        confirmation: InlineConfirmation,
    ) -> None:
        history = history_json.decode("utf-8") if isinstance(history_json, bytes) else history_json
        deferred = deferred_json.decode("utf-8") if isinstance(deferred_json, bytes) else deferred_json
        history_ref = self._write_private(run_id, "history", history)
        deferred_ref = self._write_private(run_id, "deferred", deferred)
        with self.store.lock:
            self.store.connection.execute(
                """UPDATE assistant_framework_runs
                   SET status='waiting_confirmation',
                       message_history_json=?,
                       deferred_requests_json=?,
                       confirmation_json=?,
                       updated_at=?
                   WHERE run_id=?""",
                (
                    history_ref,
                    deferred_ref,
                    confirmation.model_dump_json(),
                    _now(),
                    run_id,
                ),
            )
            self.store.connection.commit()

    def finish(self, run_id: str, status: str = "completed") -> None:
        now = _now()
        with self.store.lock:
            self.store.connection.execute(
                """UPDATE assistant_framework_runs
                   SET status=?, deferred_requests_json=NULL,
                       confirmation_json=NULL, updated_at=?,
                       completed_at=?
                   WHERE run_id=?""",
                (status, now, now, run_id),
            )
            self.store.connection.commit()

    def load(self, run_id: str) -> AgentRunSnapshot:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM assistant_framework_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if not row:
            raise ValueError("assistant_run_not_found")
        confirmation = (
            InlineConfirmation.model_validate_json(row["confirmation_json"])
            if row["confirmation_json"]
            else None
        )
        request_text = self._read_private(row["request_json"], "{}")
        return AgentRunSnapshot(
            run_id=row["run_id"],
            conversation_id=row["conversation_id"],
            profile_id=row["profile_id"],
            model=row["model"],
            request=json.loads(request_text),
            status=row["status"],
            message_history_json=self._read_private(
                row["message_history_json"],
                "[]",
            ),
            deferred_requests_json=(
                self._read_private(row["deferred_requests_json"], "")
                if row["deferred_requests_json"]
                else None
            ),
            confirmation=confirmation,
            checkpoint_id=str(row["checkpoint_id"] or ""),
            last_event_sequence=int(row["last_event_sequence"] or 0),
            parent_run_id=row["parent_run_id"],
            forked_from_sequence=row["forked_from_sequence"],
        )

    def latest_history(self, conversation_id: str) -> str:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT message_history_json
                   FROM assistant_framework_runs
                   WHERE conversation_id=?
                     AND status IN ('completed', 'waiting_confirmation')
                   ORDER BY updated_at DESC
                   LIMIT 1""",
                (conversation_id,),
            ).fetchone()
        return self._read_private(row["message_history_json"], "[]") if row else "[]"

    def events_after(
        self,
        run_id: str,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        with self.store.lock:
            rows = self.store.connection.execute(
                """SELECT payload_json
                   FROM assistant_framework_events
                   WHERE run_id=? AND sequence>?
                   ORDER BY sequence
                   LIMIT 2000""",
                (run_id, max(0, int(after_sequence))),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

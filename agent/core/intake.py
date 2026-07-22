from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from agent.tools.source_fetch import fetch_user_url, validate_public_url
from agent.core.redaction import redact_secret_text


ALLOWED_KINDS = {"pdf", "text", "url", "local_path", "folder", "vault_note", "conversation"}
ALLOWED_MIME = {
    "application/pdf", "text/plain", "text/markdown", "text/uri-list",
    "application/json", "application/octet-stream",
}
MAX_ATTACHMENT_BYTES = 80 * 1024 * 1024
FOLDER_CONFIRM_THRESHOLD = 20
SAFE_NAME = re.compile(r"[^\w\-. ()\u4e00-\u9fff]+", re.UNICODE)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temp.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _safe_display_name(value: str, fallback: str) -> str:
    name = Path(value).name.strip()[:160]
    name = SAFE_NAME.sub("-", name).strip(" .-")
    return name or fallback


class IntakeService:
    """Private attachment, conversation and versioned Artifact boundary.

    Raw messages and uploaded bodies live under 90-Local-Only. SQLite stores
    metadata, integrity hashes and structured Artifact payloads only.
    """

    def __init__(self, vault: Path, store: Any) -> None:
        self.vault = vault.resolve()
        self.store = store
        self.root = self.vault / "90-Local-Only/Agent"
        self.attachments_root = self.root / "Attachments"
        self.messages_root = self.root / "Conversations/messages"

    def create_conversation(self, title: str = "新会话") -> dict[str, Any]:
        conversation_id, now = f"conv-{uuid.uuid4().hex}", _now()
        title = str(title).strip()[:100] or "新会话"
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO conversations(
                     id, title, active_artifact_id, active_task_thread_id, created_at, updated_at,
                     active_topic, personalization_enabled, retention_policy
                   ) VALUES (?, ?, NULL, NULL, ?, ?, NULL, 1, 'full')""",
                (conversation_id, title, now, now),
            )
            self.store.connection.commit()
        return self.get_conversation(conversation_id, include_messages=False)

    def ensure_conversation(self, conversation_id: str | None, title: str = "新会话") -> str:
        if conversation_id:
            with self.store.lock:
                row = self.store.connection.execute("SELECT id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError("conversation_not_found")
            return str(row["id"])
        return str(self.create_conversation(title)["id"])

    def ensure_runtime_conversation(self, conversation_id: str, title: str = "新会话") -> str:
        """Create a missing Pi-owned conversation with its stable identity.

        Normal product callers must still use ``ensure_conversation`` so a
        stale or forged id is rejected. Pi creates identities before the
        first persisted message (and when forking), so this narrowly scoped
        bridge is the only place allowed to insert an externally supplied id.
        """
        conversation_id = str(conversation_id or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", conversation_id):
            raise ValueError("invalid_conversation_id")
        title = str(title).strip()[:100] or "新会话"
        now = _now()
        with self.store.lock:
            self.store.connection.execute(
                """INSERT OR IGNORE INTO conversations(
                     id, title, active_artifact_id, active_task_thread_id, created_at, updated_at,
                     active_topic, personalization_enabled, retention_policy
                   ) VALUES (?, ?, NULL, NULL, ?, ?, NULL, 1, 'full')""",
                (conversation_id, title, now, now),
            )
            self.store.connection.commit()
        return conversation_id

    def append_message(
        self, conversation_id: str, role: str, content: str, message_type: str = "text",
        task_thread_id: str | None = None, artifact_group_id: str | None = None,
        message_id: str | None = None, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("invalid_message_role")
        if not content or len(content) > 250_000:
            raise ValueError("invalid_message_content")
        content = redact_secret_text(content)
        self.ensure_conversation(conversation_id)
        message_id = str(message_id or f"msg-{uuid.uuid4().hex}")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", message_id):
            raise ValueError("invalid_message_id")
        with self.store.lock:
            existing = self.store.connection.execute(
                "SELECT * FROM conversation_messages WHERE id=? AND conversation_id=?",
                (message_id, conversation_id),
            ).fetchone()
        if existing:
            saved = self._read_message(existing)
            if saved["role"] != role or saved["content"] != content:
                raise RuntimeError("conversation_message_id_conflict")
            return saved
        now = _now()
        relative = Path("Conversations/messages") / f"{message_id}.json"
        path = self.root / relative
        payload: dict[str, Any] = {
            "id": message_id,
            "role": role,
            "content": content,
            "created_at": now,
        }
        if metadata:
            payload["metadata"] = metadata
        _atomic_bytes(path, (_json(payload) + "\n").encode())
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO conversation_messages(
                     id, conversation_id, role, content_reference, message_type,
                     task_thread_id, artifact_group_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (message_id, conversation_id, role, str(relative), message_type, task_thread_id, artifact_group_id, now),
            )
            self.store.connection.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
            self.store.connection.commit()
        return {
            "id": message_id,
            "role": role,
            "messageType": message_type,
            "content": content,
            "createdAt": now,
            "metadata": metadata,
        }

    def update_message_metadata(
        self, conversation_id: str, message_id: str, metadata: dict[str, Any],
    ) -> dict[str, Any]:
        self.ensure_conversation(conversation_id)
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", message_id):
            raise ValueError("invalid_message_id")
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM conversation_messages WHERE id=? AND conversation_id=?",
                (message_id, conversation_id),
            ).fetchone()
        if not row:
            raise ValueError("conversation_message_not_found")
        relative = Path(str(row["content_reference"]))
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.messages_root.resolve()) or not path.is_file() or path.is_symlink():
            raise RuntimeError("conversation_message_content_unavailable")
        payload = _decode(path.read_text(encoding="utf-8", errors="replace"), {})
        payload["metadata"] = metadata
        _atomic_bytes(path, (_json(payload) + "\n").encode())
        return self._read_message(row)

    def _read_message(self, row: Any) -> dict[str, Any]:
        relative = Path(str(row["content_reference"]))
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.messages_root.resolve()) or not path.is_file() or path.is_symlink():
            content = "[本地消息内容不可用]"
            metadata: dict[str, Any] | None = None
        else:
            payload = _decode(path.read_text(encoding="utf-8", errors="replace"), {})
            content = str(payload.get("content", ""))
            metadata = payload.get("metadata")
        return {
            "id": row["id"], "role": row["role"], "messageType": row["message_type"],
            "content": content, "taskThreadId": row["task_thread_id"],
            "artifactGroupId": row["artifact_group_id"], "createdAt": row["created_at"],
            "metadata": metadata,
        }

    def list_conversations(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        limit, offset = max(1, min(100, int(limit))), max(0, int(offset))
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT c.*, COUNT(m.id) message_count FROM conversations c LEFT JOIN conversation_messages m ON m.conversation_id=c.id GROUP BY c.id ORDER BY c.updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        items = [{
            "id": row["id"], "title": row["title"], "activeArtifactId": row["active_artifact_id"],
            "activeTaskThreadId": row["active_task_thread_id"],
            "activeTopic": row["active_topic"], "personalizationEnabled": bool(row["personalization_enabled"]),
            "retentionPolicy": row["retention_policy"],
            "messageCount": row["message_count"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        } for row in rows]
        return {"items": items, "limit": limit, "offset": offset, "hasMore": len(items) == limit}

    def get_conversation(self, conversation_id: str, include_messages: bool = True) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not row:
                raise ValueError("conversation_not_found")
            messages = self.store.connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY rowid", (conversation_id,)
            ).fetchall() if include_messages else []
            title = row["title"]
            if title == "新会话" and messages:
                first_user_message = next((item for item in messages if item["role"] == "user"), None)
                if first_user_message:
                    first_line = str(self._read_message(first_user_message)["content"] or "").strip().splitlines()[0]
                    title = first_line[:32].strip(" ，。！？,.!?") or title
                    self.store.connection.execute(
                        "UPDATE conversations SET title=? WHERE id=?",
                        (title, conversation_id),
                    )
                    self.store.connection.commit()
        return {
            "id": row["id"], "title": title, "activeArtifactId": row["active_artifact_id"],
            "activeTaskThreadId": row["active_task_thread_id"],
            "activeTopic": row["active_topic"], "personalizationEnabled": bool(row["personalization_enabled"]),
            "retentionPolicy": row["retention_policy"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "messages": [self._read_message(item) for item in messages],
        }

    def recent_messages(self, conversation_id: str, limit: int = 8) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 100))
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY rowid DESC LIMIT ?",
                (conversation_id, bounded),
            ).fetchall()
        return [self._read_message(row) for row in reversed(rows)]

    def get_message(self, conversation_id: str, message_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM conversation_messages WHERE conversation_id=? AND id=?",
                (conversation_id, message_id),
            ).fetchone()
        if not row:
            raise ValueError("conversation_message_not_found")
        return self._read_message(row)

    def recent_messages_through(self, conversation_id: str, message_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return bounded history ending at an existing message for regeneration."""
        with self.store.lock:
            target = self.store.connection.execute(
                "SELECT rowid FROM conversation_messages WHERE conversation_id=? AND id=?",
                (conversation_id, message_id),
            ).fetchone()
            if not target:
                raise ValueError("conversation_message_not_found")
            rows = self.store.connection.execute(
                """SELECT * FROM conversation_messages
                   WHERE conversation_id=? AND rowid<=?
                   ORDER BY rowid DESC LIMIT ?""",
                (conversation_id, int(target["rowid"]), max(1, min(100, int(limit)))),
            ).fetchall()
        return [self._read_message(row) for row in reversed(rows)]

    def update_conversation_preferences(
        self, conversation_id: str, *, personalization_enabled: bool | None = None,
        retention_policy: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_conversation(conversation_id)
        if retention_policy is not None and retention_policy not in {"full", "summary_only", "session"}:
            raise ValueError("invalid_retention_policy")
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            self.store.connection.execute(
                """UPDATE conversations SET personalization_enabled=?, retention_policy=?, updated_at=?
                   WHERE id=?""",
                (int(personalization_enabled if personalization_enabled is not None else bool(row["personalization_enabled"])),
                 retention_policy or row["retention_policy"], _now(), conversation_id),
            )
            self.store.connection.commit()
        return self.get_conversation(conversation_id, include_messages=False)

    def export_conversation(self, conversation_id: str, extras: dict[str, Any] | None = None) -> dict[str, Any]:
        """Export one private conversation without moving it into the synced Vault."""
        conversation = self.get_conversation(conversation_id)
        now = datetime.now().astimezone()
        relative = Path("Conversations/exports") / f"{conversation_id}-{now.strftime('%Y%m%d-%H%M%S')}.json"
        payload = {
            "schemaVersion": 1,
            "exportedAt": now.isoformat(timespec="seconds"),
            "conversation": conversation,
            **(extras or {}),
        }
        encoded = (_json(payload) + "\n").encode("utf-8")
        _atomic_bytes(self.root / relative, encoded)
        return {"conversationId": conversation_id, "reference": str(relative), "sizeBytes": len(encoded)}

    def retain_summary_only(self, conversation_id: str) -> dict[str, Any]:
        """Delete raw message bodies after a summary exists; derived artifacts remain."""
        self.ensure_conversation(conversation_id)
        if not self.store.latest_conversation_summary(conversation_id):
            raise ValueError("conversation_summary_required")
        with self.store.lock:
            rows = self.store.connection.execute(
                "SELECT content_reference FROM conversation_messages WHERE conversation_id=?", (conversation_id,),
            ).fetchall()
            self.store.connection.execute("DELETE FROM conversation_messages WHERE conversation_id=?", (conversation_id,))
            self.store.connection.execute(
                """UPDATE conversations SET retention_policy='summary_only',
                   active_task_thread_id=NULL, updated_at=? WHERE id=?""",
                (_now(), conversation_id),
            )
            self.store.connection.commit()
        removed = self._unlink_private_references([str(row["content_reference"]) for row in rows])
        return {"conversationId": conversation_id, "retentionPolicy": "summary_only", "removedMessages": len(rows), "removedFiles": removed}

    def delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Delete a conversation and its local-only descendants after caller confirmation."""
        self.ensure_conversation(conversation_id)
        with self.store.lock:
            message_rows = self.store.connection.execute(
                "SELECT content_reference FROM conversation_messages WHERE conversation_id=?", (conversation_id,),
            ).fetchall()
            attachment_rows = self.store.connection.execute(
                "SELECT storage_reference FROM attachments WHERE conversation_id=?", (conversation_id,),
            ).fetchall()
            artifact_ids = [str(row["id"]) for row in self.store.connection.execute(
                "SELECT id FROM agent_artifacts WHERE conversation_id=?", (conversation_id,),
            ).fetchall()]
            task_ids = [str(row["id"]) for row in self.store.connection.execute(
                "SELECT id FROM assistant_task_threads WHERE conversation_id=?", (conversation_id,),
            ).fetchall()]
            for artifact_id in artifact_ids:
                self.store.connection.execute("DELETE FROM artifact_versions WHERE artifact_id=?", (artifact_id,))
            for task_id in task_ids:
                self.store.connection.execute("DELETE FROM assistant_task_steps WHERE task_thread_id=?", (task_id,))
            for table in (
                "artifact_groups", "assistant_task_threads", "intake_items", "agent_artifacts",
                "attachments", "conversation_messages", "conversation_summaries",
                "conversation_knowledge_signals",
            ):
                self.store.connection.execute(f"DELETE FROM {table} WHERE conversation_id=?", (conversation_id,))
            self.store.connection.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
            self.store.connection.commit()
        references = [str(row["content_reference"]) for row in message_rows]
        references.extend(str(row["storage_reference"]) for row in attachment_rows)
        removed = self._unlink_private_references(references)
        return {"conversationId": conversation_id, "deleted": True, "removedFiles": removed}

    def clear_conversations(self, scope: str) -> dict[str, Any]:
        if scope not in {"recent-7-days", "all"}:
            raise ValueError("invalid_conversation_clear_scope")
        with self.store.lock:
            if scope == "all":
                rows = self.store.connection.execute("SELECT id FROM conversations ORDER BY created_at").fetchall()
            else:
                cutoff = (datetime.now().astimezone() - timedelta(days=7)).isoformat(timespec="seconds")
                rows = self.store.connection.execute(
                    "SELECT id FROM conversations WHERE created_at>=? ORDER BY created_at", (cutoff,),
                ).fetchall()
        deleted = [self.delete_conversation(str(row["id"])) for row in rows]
        return {"scope": scope, "deletedCount": len(deleted), "removedFiles": sum(int(item["removedFiles"]) for item in deleted)}

    def _unlink_private_references(self, references: list[str]) -> int:
        removed = 0
        root = self.root.resolve()
        for reference in references:
            if not reference or reference.startswith("external:"):
                continue
            path = (self.root / reference).resolve()
            if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
                continue
            path.unlink()
            removed += 1
            parent = path.parent
            while parent != root and parent.is_relative_to(root):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
        return removed

    @staticmethod
    def _task_step_labels() -> list[tuple[str, str]]:
        return [
            ("existing-knowledge", "检查已有知识"),
            ("trusted-sources", "检索可信来源"),
            ("level-fit", "生成适合当前水平的内容"),
            ("organize", "组织内容与练习"),
            ("editable-result", "生成可继续修改的成果"),
        ]

    def create_task_thread(self, conversation_id: str, user_message_id: str, title: str = "处理当前任务") -> dict[str, Any]:
        self.ensure_conversation(conversation_id)
        task_id, now = f"task-thread-{uuid.uuid4().hex}", _now()
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO assistant_task_threads(
                     id, conversation_id, user_message_id, title, intent, status,
                     progress, artifact_group_id, error_code, recoverable, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'unknown', 'planning', 5, NULL, NULL, 0, ?, ?)""",
                (task_id, conversation_id, user_message_id, str(title).strip()[:160] or "处理当前任务", now, now),
            )
            for ordinal, (label, user_label) in enumerate(self._task_step_labels(), 1):
                self.store.connection.execute(
                    """INSERT INTO assistant_task_steps(
                         id, task_thread_id, ordinal, label, user_facing_label, status,
                         detail, started_at, completed_at, error_code
                       ) VALUES (?, ?, ?, ?, ?, ?, '', ?, NULL, NULL)""",
                    (f"task-step-{uuid.uuid4().hex}", task_id, ordinal, label, user_label,
                     "running" if ordinal == 1 else "pending", now if ordinal == 1 else None),
                )
            self.store.connection.execute(
                "UPDATE conversations SET active_task_thread_id=?, updated_at=? WHERE id=?",
                (task_id, now, conversation_id),
            )
            self.store.connection.execute(
                "UPDATE conversation_messages SET task_thread_id=? WHERE id=? AND conversation_id=?",
                (task_id, user_message_id, conversation_id),
            )
            self.store.connection.commit()
        return self.get_task_thread(task_id)

    def update_task_thread(
        self, task_id: str, *, title: str | None = None, intent: str | None = None,
        status: str | None = None, progress: int | None = None,
        artifact_group_id: str | None = None, error_code: str | None = None,
        recoverable: bool | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM assistant_task_threads WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("task_thread_not_found")
            next_status = status or str(row["status"])
            next_progress = max(0, min(100, int(progress if progress is not None else row["progress"])))
            self.store.connection.execute(
                """UPDATE assistant_task_threads SET title=?, intent=?, status=?, progress=?,
                   artifact_group_id=?, error_code=?, recoverable=?, updated_at=? WHERE id=?""",
                (str(title or row["title"])[:160], intent or row["intent"], next_status, next_progress,
                 artifact_group_id if artifact_group_id is not None else row["artifact_group_id"],
                 error_code, int(recoverable if recoverable is not None else row["recoverable"]), now, task_id),
            )
            current = 5 if next_status in {"completed", "waiting_user", "partially_completed"} else max(1, min(5, (next_progress + 19) // 20))
            for step in self.store.connection.execute(
                "SELECT id, ordinal FROM assistant_task_steps WHERE task_thread_id=? ORDER BY ordinal", (task_id,)
            ).fetchall():
                ordinal = int(step["ordinal"])
                step_status = "completed" if ordinal < current or next_status in {"completed", "waiting_user"} else (
                    "failed" if next_status == "failed" and ordinal == current else
                    "running" if ordinal == current and next_status not in {"failed", "cancelled"} else
                    "skipped" if next_status == "cancelled" and ordinal >= current else "pending"
                )
                self.store.connection.execute(
                    """UPDATE assistant_task_steps SET status=?, started_at=COALESCE(started_at, ?),
                       completed_at=?, error_code=? WHERE id=?""",
                    (step_status, now if step_status in {"running", "completed", "failed"} else None,
                     now if step_status in {"completed", "failed", "skipped"} else None,
                     error_code if step_status == "failed" else None, step["id"]),
                )
            self.store.connection.commit()
        return self.get_task_thread(task_id)

    def get_task_thread(self, task_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM assistant_task_threads WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("task_thread_not_found")
            steps = self.store.connection.execute(
                "SELECT * FROM assistant_task_steps WHERE task_thread_id=? ORDER BY ordinal", (task_id,)
            ).fetchall()
        return {
            "schemaVersion": 1, "id": row["id"], "conversationId": row["conversation_id"],
            "userMessageId": row["user_message_id"], "title": row["title"], "intent": row["intent"],
            "status": row["status"], "progress": row["progress"],
            "artifactGroupId": row["artifact_group_id"], "errorCode": row["error_code"],
            "recoverable": bool(row["recoverable"]),
            "steps": [{"id": item["id"], "label": item["label"], "userFacingLabel": item["user_facing_label"],
                       "status": item["status"], "detail": item["detail"]} for item in steps],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def latest_task_thread(self, conversation_id: str) -> dict[str, Any] | None:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT id FROM assistant_task_threads WHERE conversation_id=? ORDER BY updated_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return self.get_task_thread(str(row["id"])) if row else None

    def set_active_artifact(self, conversation_id: str, artifact_id: str) -> None:
        with self.store.lock:
            if not self.store.connection.execute("SELECT 1 FROM agent_artifacts WHERE id=? AND conversation_id=?", (artifact_id, conversation_id)).fetchone():
                raise ValueError("artifact_conversation_mismatch")
            self.store.connection.execute("UPDATE conversations SET active_artifact_id=?, updated_at=? WHERE id=?", (artifact_id, _now(), conversation_id))
            self.store.connection.commit()

    def _public_attachment(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "conversationId": row["conversation_id"], "kind": row["kind"],
            "displayName": row["display_name"], "mimeType": row["mime_type"],
            "sizeBytes": row["size_bytes"], "sha256": row["sha256"],
            "sourceType": row["source_type"], "status": row["status"], "createdAt": row["created_at"],
        }

    def store_binary_attachment(self, conversation_id: str, display_name: str, mime_type: str, body: bytes, kind: str = "") -> dict[str, Any]:
        conversation_id = self.ensure_conversation(conversation_id)
        if not body or len(body) > MAX_ATTACHMENT_BYTES:
            raise ValueError("attachment_size_invalid")
        mime_type = str(mime_type).split(";", 1)[0].strip().lower() or "application/octet-stream"
        if mime_type not in ALLOWED_MIME:
            raise ValueError("attachment_mime_not_allowed")
        inferred = "pdf" if mime_type == "application/pdf" else "conversation" if display_name.lower().endswith(".json") else "text"
        kind = kind or inferred
        if kind not in ALLOWED_KINDS:
            raise ValueError("attachment_kind_not_allowed")
        if kind == "pdf" and not body.startswith(b"%PDF"):
            raise ValueError("invalid_pdf_signature")
        digest = hashlib.sha256(body).hexdigest()
        with self.store.lock:
            duplicate = self.store.connection.execute(
                "SELECT * FROM attachments WHERE conversation_id=? AND sha256=? ORDER BY created_at LIMIT 1",
                (conversation_id, digest),
            ).fetchone()
        if duplicate:
            return {**self._public_attachment(duplicate), "duplicate": True}
        attachment_id, now = f"att-{uuid.uuid4().hex}", _now()
        safe_name = _safe_display_name(display_name, f"attachment-{attachment_id[-8:]}")
        relative = Path("Attachments") / attachment_id / safe_name
        _atomic_bytes(self.root / relative, body)
        with self.store.lock:
            self.store.connection.execute(
                "INSERT INTO attachments VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', 'ready', ?)",
                (attachment_id, conversation_id, kind, safe_name, mime_type, len(body), digest, str(relative), now),
            )
            self.store.connection.commit()
            row = self.store.connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return {**self._public_attachment(row), "duplicate": False}

    def reference_local_path(self, conversation_id: str, raw_path: str, *, explicit_user_selection: bool = False, confirmed_large_folder: bool = False) -> dict[str, Any]:
        conversation_id = self.ensure_conversation(conversation_id)
        raw = Path(raw_path).expanduser()
        if raw.is_symlink():
            raise ValueError("attachment_unavailable")
        path = raw.resolve()
        if not path.exists():
            raise ValueError("attachment_unavailable")
        if not path.is_relative_to(self.vault) and not explicit_user_selection:
            raise ValueError("attachment_path_not_authorized")
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink()]
            if len(files) > FOLDER_CONFIRM_THRESHOLD and not confirmed_large_folder:
                return {
                    "requiresConfirmation": True, "code": "folder_threshold_exceeded", "fileCount": len(files),
                    "typeCounts": self._type_counts(files), "estimatedModelCalls": max(1, (len(files) + 9) // 10),
                    "options": ["先生成文件清单", "处理 PDF", "处理最近 20 个文件", "取消"],
                }
            manifest = [{"name": item.name, "suffix": item.suffix.lower(), "size": item.stat().st_size} for item in files[:1000]]
            body = (_json({"root": str(path), "files": manifest}) + "\n").encode()
            kind, mime = "folder", "application/json"
            size = sum(item["size"] for item in manifest)
        else:
            body = b""
            kind = "pdf" if path.suffix.lower() == ".pdf" else "local_path"
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if mime not in ALLOWED_MIME:
                raise ValueError("attachment_mime_not_allowed")
            size = path.stat().st_size
            if size > MAX_ATTACHMENT_BYTES:
                raise ValueError("attachment_size_invalid")
        digest = hashlib.sha256(body).hexdigest() if path.is_dir() else self._hash_file(path)
        attachment_id, now = f"att-{uuid.uuid4().hex}", _now()
        if path.is_dir():
            relative = Path("Attachments") / attachment_id / "folder-manifest.json"
            _atomic_bytes(self.root / relative, body)
            storage_reference = str(relative)
        else:
            storage_reference = f"external:{path}"
        with self.store.lock:
            duplicate = self.store.connection.execute(
                "SELECT * FROM attachments WHERE conversation_id=? AND sha256=?", (conversation_id, digest)
            ).fetchone()
            if duplicate:
                return {**self._public_attachment(duplicate), "duplicate": True}
            self.store.connection.execute(
                "INSERT INTO attachments VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'user-selected', 'ready', ?)",
                (attachment_id, conversation_id, kind, _safe_display_name(path.name, kind), mime, size, digest, storage_reference, now),
            )
            self.store.connection.commit()
            row = self.store.connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return {**self._public_attachment(row), "duplicate": False}

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _type_counts(files: list[Path]) -> dict[str, int]:
        result: dict[str, int] = {}
        for path in files:
            key = path.suffix.lower() or "无扩展名"
            result[key] = result.get(key, 0) + 1
        return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))

    def store_url_attachment(self, conversation_id: str, url: str, *, allow_network: bool, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
        conversation_id = self.ensure_conversation(conversation_id)
        safe_url = validate_public_url(url)
        if not allow_network:
            raise ValueError("network_permission_required")
        result = fetch_user_url({"url": safe_url}, **({"opener": opener} if opener else {}))
        return self.store_url_content(
            conversation_id, str(result["url"]), str(result["text"]), str(result["content_type"]),
        )

    def store_url_content(self, conversation_id: str, url: str, text: str, content_type: str = "text/plain") -> dict[str, Any]:
        conversation_id = self.ensure_conversation(conversation_id)
        safe_url = validate_public_url(url)
        body = str(text).encode()
        attachment = self.store_binary_attachment(conversation_id, "网页资料.txt", "text/plain", body, "url")
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE attachments SET source_type='user-url', storage_reference=? WHERE id=?",
                (f"url:{safe_url}|{self._storage_reference(str(attachment['id']))}", attachment["id"]),
            )
            self.store.connection.commit()
        return {**attachment, "url": safe_url, "contentType": content_type}

    def _storage_reference(self, attachment_id: str) -> str:
        with self.store.lock:
            row = self.store.connection.execute("SELECT storage_reference FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise ValueError("attachment_not_found")
        return str(row["storage_reference"])

    def get_attachment(self, attachment_id: str, *, include_private_reference: bool = False) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if not row:
            raise ValueError("attachment_not_found")
        result = self._public_attachment(row)
        if include_private_reference:
            result["storageReference"] = row["storage_reference"]
        return result

    def resolve_attachment_path(self, attachment_id: str) -> Path:
        reference = str(self.get_attachment(attachment_id, include_private_reference=True)["storageReference"])
        if reference.startswith("external:"):
            path = Path(reference.removeprefix("external:")).resolve()
        else:
            relative = reference.split("|", 1)[-1] if reference.startswith("url:") else reference
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.attachments_root.resolve()):
                raise ValueError("attachment_reference_invalid")
        if not path.exists() or path.is_symlink():
            raise ValueError("attachment_unavailable")
        return path

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        attachment = self.get_attachment(attachment_id, include_private_reference=True)
        with self.store.lock:
            in_use = self.store.connection.execute(
                "SELECT 1 FROM intake_items WHERE attachment_ids_json LIKE ? AND status NOT IN ('failed','cancelled') LIMIT 1",
                (f"%{attachment_id}%",),
            ).fetchone()
            if in_use:
                raise ValueError("attachment_in_use")
            self.store.connection.execute("DELETE FROM attachments WHERE id=?", (attachment_id,))
            self.store.connection.commit()
        reference = str(attachment["storageReference"])
        if not reference.startswith(("external:", "url:")):
            path = (self.root / reference).resolve()
            if path.is_relative_to(self.attachments_root.resolve()) and path.is_file() and not path.is_symlink():
                path.unlink()
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
        return {"id": attachment_id, "deleted": True, "originalFileDeleted": False}

    @staticmethod
    def _artifact_public(row: Any, *, detail: bool = False) -> dict[str, Any]:
        result = {
            "id": row["id"], "type": row["artifact_type"], "title": row["title"], "status": row["status"],
            "conversationId": row["conversation_id"], "sourceRunId": row["source_run_id"],
            "artifactGroupId": row["artifact_group_id"],
            "version": row["version"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }
        payload = _decode(row["payload_json"], {})
        if detail:
            result["payload"] = payload
        else:
            result["summary"] = {
                key: payload.get(key) for key in ("kind", "summary", "riskLevel", "sourceType", "estimatedMinutes", "progress") if key in payload
            }
        return result

    def create_artifact(
        self, artifact_type: str, title: str, status: str, conversation_id: str,
        source_run_id: str | None, payload: dict[str, Any], artifact_group_id: str | None = None,
    ) -> dict[str, Any]:
        conversation_id = self.ensure_conversation(conversation_id)
        normalized_title = " ".join(str(title).strip().casefold().split())
        artifact_id, now = f"artifact-{uuid.uuid4().hex}", _now()
        safe_payload = self._sanitize_payload(payload)
        with self.store.lock:
            if source_run_id:
                rows = self.store.connection.execute(
                    "SELECT * FROM agent_artifacts WHERE conversation_id=? AND source_run_id=? AND artifact_type=?",
                    (conversation_id, source_run_id, artifact_type),
                ).fetchall()
                duplicate = next((row for row in rows if " ".join(str(row["title"]).strip().casefold().split()) == normalized_title), None)
                if duplicate:
                    return self._artifact_public(duplicate, detail=True)
            self.store.connection.execute(
                """INSERT INTO agent_artifacts(
                     id, artifact_type, title, status, conversation_id, source_run_id,
                     artifact_group_id, version, payload_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (artifact_id, artifact_type, str(title).strip()[:160] or "未命名成果", status,
                 conversation_id, source_run_id, artifact_group_id, _json(safe_payload), now, now),
            )
            self.store.connection.execute(
                "INSERT INTO artifact_versions VALUES (?, ?, 1, NULL, '', ?, ?)",
                (f"artifact-version-{uuid.uuid4().hex}", artifact_id, _json(safe_payload), now),
            )
            self.store.connection.execute("UPDATE conversations SET active_artifact_id=?, updated_at=? WHERE id=?", (artifact_id, now, conversation_id))
            self.store.connection.commit()
            row = self.store.connection.execute("SELECT * FROM agent_artifacts WHERE id=?", (artifact_id,)).fetchone()
        return self._artifact_public(row, detail=True)

    def create_artifact_group(
        self, conversation_id: str, task_thread_id: str, title: str,
        artifacts: list[dict[str, Any]], group_type: str = "assistant-result",
    ) -> dict[str, Any] | None:
        if not artifacts:
            return None
        primary_priority = ["write_result", "update_suggestion", "organization_plan", "learning_pack", "material", "research_bundle", "capture_proposal", "learning_plan", "knowledge_gap", "change_set", "quiz"]
        primary = next((item for artifact_type in primary_priority for item in artifacts if item["type"] == artifact_type), artifacts[0])
        child_ids = [str(item["id"]) for item in artifacts if item["id"] != primary["id"]]
        group_id, now = f"artifact-group-{uuid.uuid4().hex}", _now()
        with self.store.lock:
            existing = self.store.connection.execute(
                "SELECT id FROM artifact_groups WHERE task_thread_id=? ORDER BY updated_at DESC LIMIT 1", (task_thread_id,)
            ).fetchone()
            if existing:
                return self.get_artifact_group(str(existing["id"]))
            self.store.connection.execute(
                """INSERT INTO artifact_groups(
                     id, conversation_id, task_thread_id, title, group_type, status,
                     primary_artifact_id, child_artifact_ids_json, version, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (group_id, conversation_id, task_thread_id, str(title).strip()[:160] or primary["title"],
                 group_type, "ready", primary["id"], _json(child_ids),
                 max(int(item.get("version", 1)) for item in artifacts), now, now),
            )
            artifact_ids = [str(item["id"]) for item in artifacts]
            self.store.connection.executemany(
                "UPDATE agent_artifacts SET artifact_group_id=? WHERE id=?", [(group_id, artifact_id) for artifact_id in artifact_ids]
            )
            self.store.connection.execute(
                "UPDATE assistant_task_threads SET artifact_group_id=?, updated_at=? WHERE id=?",
                (group_id, now, task_thread_id),
            )
            self.store.connection.execute(
                "UPDATE conversation_messages SET artifact_group_id=? WHERE task_thread_id=?",
                (group_id, task_thread_id),
            )
            self.store.connection.commit()
        return self.get_artifact_group(group_id)

    def get_artifact_group(self, group_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM artifact_groups WHERE id=?", (group_id,)).fetchone()
            if not row:
                raise ValueError("artifact_group_not_found")
            artifacts = self.store.connection.execute(
                "SELECT * FROM agent_artifacts WHERE artifact_group_id=? ORDER BY created_at", (group_id,)
            ).fetchall()
        return {
            "schemaVersion": 1, "id": row["id"], "conversationId": row["conversation_id"],
            "taskThreadId": row["task_thread_id"], "title": row["title"], "type": row["group_type"],
            "status": row["status"], "primaryArtifactId": row["primary_artifact_id"],
            "childArtifactIds": _decode(row["child_artifact_ids_json"], []), "version": row["version"],
            "artifacts": [self._artifact_public(item, detail=True) for item in artifacts],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def latest_artifact_group(self, conversation_id: str) -> dict[str, Any] | None:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT id FROM artifact_groups WHERE conversation_id=? ORDER BY updated_at DESC LIMIT 1", (conversation_id,)
            ).fetchone()
        return self.get_artifact_group(str(row["id"])) if row else None

    @classmethod
    def _sanitize_payload(cls, value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for child_key, child in value.items():
                lowered = str(child_key).casefold()
                if lowered in {"original_text", "selected_text", "private_request_path", "payload_path"}:
                    continue
                if lowered in {"content", "text"} and isinstance(child, str):
                    result[f"{child_key}_chars"] = len(child)
                    result[f"{child_key}_sha256"] = hashlib.sha256(child.encode()).hexdigest()
                else:
                    result[child_key] = cls._sanitize_payload(child, str(child_key))
            return result
        if isinstance(value, list):
            return [cls._sanitize_payload(item, key) for item in value[:100]]
        if isinstance(value, str):
            return value[:5000]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:1000]

    def list_artifacts(self, artifact_type: str = "", status: str = "", conversation_id: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        clauses, values = [], []
        if artifact_type:
            clauses.append("artifact_type=?"); values.append(artifact_type)
        if status:
            clauses.append("status=?"); values.append(status)
        if conversation_id:
            clauses.append("conversation_id=?"); values.append(conversation_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        limit, offset = max(1, min(200, int(limit))), max(0, int(offset))
        with self.store.lock:
            rows = self.store.connection.execute(
                f"SELECT * FROM agent_artifacts{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*values, limit, offset)
            ).fetchall()
        return {"items": [self._artifact_public(row) for row in rows], "limit": limit, "offset": offset, "hasMore": len(rows) == limit}

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM agent_artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise ValueError("artifact_not_found")
            versions = self.store.connection.execute(
                "SELECT version, parent_version, revision_instruction, created_at FROM artifact_versions WHERE artifact_id=? ORDER BY version DESC",
                (artifact_id,),
            ).fetchall()
        result = self._artifact_public(row, detail=True)
        result["versions"] = [dict(item) for item in versions]
        return result

    def revise_artifact(self, artifact_id: str, instruction: str, patch: dict[str, Any] | None = None, expected_version: int | None = None) -> dict[str, Any]:
        instruction = str(instruction).strip()
        if not instruction or len(instruction) > 10_000:
            raise ValueError("revision_instruction_required")
        with self.store.lock:
            row = self.store.connection.execute("SELECT * FROM agent_artifacts WHERE id=?", (artifact_id,)).fetchone()
            if not row:
                raise ValueError("artifact_not_found")
            current_version = int(row["version"])
            if expected_version is not None and int(expected_version) != current_version:
                raise ValueError("stale_artifact_revision")
            payload = _decode(row["payload_json"], {})
            payload = self._merge(payload, self._sanitize_payload(patch or {}))
            payload["lastRevisionInstruction"] = instruction
            if "不要拆" in instruction or "只保留一篇" in instruction:
                payload["splitStrategy"] = "single-main-note"
            if "移到周末" in instruction:
                payload["scheduleTarget"] = "weekend"
            if "猜测" in instruction or "不是事实" in instruction:
                payload["verificationStatus"] = "needs-verification"
            next_version, now = current_version + 1, _now()
            title = str((patch or {}).get("title") or row["title"])[:160]
            self.store.connection.execute(
                "UPDATE agent_artifacts SET title=?, status='draft', version=?, payload_json=?, updated_at=? WHERE id=?",
                (title, next_version, _json(payload), now, artifact_id),
            )
            self.store.connection.execute(
                "INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"artifact-version-{uuid.uuid4().hex}", artifact_id, next_version, current_version, instruction, _json(payload), now),
            )
            self.store.connection.execute("UPDATE conversations SET active_artifact_id=?, updated_at=? WHERE id=?", (artifact_id, now, row["conversation_id"]))
            self.store.connection.commit()
        return self.get_artifact(artifact_id)

    @classmethod
    def _merge(cls, current: Any, patch: Any) -> Any:
        if isinstance(current, dict) and isinstance(patch, dict):
            merged = dict(current)
            for key, value in patch.items():
                merged[key] = cls._merge(merged.get(key), value)
            return merged
        return patch

    def artifact_action(self, artifact_id: str, action: str) -> dict[str, Any]:
        states = {"accept": "accepted", "reject": "rejected", "complete": "completed", "later": "awaiting_confirmation", "reopen": "draft"}
        if action not in states:
            raise ValueError("artifact_action_not_supported")
        artifact = self.get_artifact(artifact_id)
        if artifact["status"] == "accepted" and action not in {"reopen"}:
            return artifact
        now = _now()
        with self.store.lock:
            self.store.connection.execute("UPDATE agent_artifacts SET status=?, updated_at=? WHERE id=?", (states[action], now, artifact_id))
            self.store.connection.commit()
        return self.get_artifact(artifact_id)

    def create_intake_item(self, conversation_id: str, message_id: str, attachments: list[str], references: list[dict[str, Any]]) -> str:
        intake_id, now = f"intake-{uuid.uuid4().hex}", _now()
        with self.store.lock:
            self.store.connection.execute(
                "INSERT INTO intake_items VALUES (?, ?, ?, NULL, 'queued', 0, ?, ?, NULL, ?, ?)",
                (intake_id, conversation_id, message_id, _json(attachments), _json(references), now, now),
            )
            self.store.connection.commit()
        return intake_id

    def update_intake_item(self, intake_id: str, status: str, progress: int, run_id: str | None = None, error_code: str | None = None) -> None:
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE intake_items SET status=?, progress=?, run_id=COALESCE(?,run_id), error_code=?, updated_at=? WHERE id=?",
                (status, max(0, min(100, int(progress))), run_id, error_code, _now(), intake_id),
            )
            self.store.connection.commit()

    def list_materials(self, status: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        where, values = (" WHERE i.status=?", [status]) if status else ("", [])
        with self.store.lock:
            rows = self.store.connection.execute(
                f"SELECT i.*, c.title conversation_title FROM intake_items i JOIN conversations c ON c.id=i.conversation_id{where} ORDER BY i.updated_at DESC LIMIT ? OFFSET ?",
                (*values, max(1, min(200, limit)), max(0, offset)),
            ).fetchall()
            items = []
            for row in rows:
                ids = _decode(row["attachment_ids_json"], [])
                attachments = [self._public_attachment(item) for attachment_id in ids if (item := self.store.connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone())]
                artifact_count = self.store.connection.execute("SELECT COUNT(*) FROM agent_artifacts WHERE conversation_id=?", (row["conversation_id"],)).fetchone()[0]
                items.append({
                    "id": row["id"], "conversationId": row["conversation_id"], "title": attachments[0]["displayName"] if attachments else row["conversation_title"],
                    "status": row["status"], "progress": row["progress"], "runId": row["run_id"], "errorCode": row["error_code"],
                    "attachments": attachments, "references": _decode(row["reference_json"], []), "artifactCount": artifact_count,
                    "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                })
        return {"items": items, "limit": limit, "offset": offset, "hasMore": len(items) == limit}

    def get_material(self, intake_id: str) -> dict[str, Any]:
        items = self.list_materials(limit=200)["items"]
        item = next((row for row in items if row["id"] == intake_id), None)
        if not item:
            raise ValueError("material_not_found")
        conversation = self.get_conversation(item["conversationId"])
        item["instruction"] = next((message["content"] for message in conversation["messages"] if message["role"] == "user"), "")
        item["artifacts"] = self.list_artifacts(conversation_id=item["conversationId"])["items"]
        if item.get("runId"):
            try:
                item["run"] = self.store.get_brain_run(str(item["runId"]), include_details=True)
            except RuntimeError:
                item["run"] = None
        return item

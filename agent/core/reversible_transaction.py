from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from difflib import unified_diff
from pathlib import Path
from typing import Any

from agent.tools.vault_access import safe_note


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _diff(relative: str, before: bytes, after: bytes) -> tuple[str, int, int]:
    lines = list(
        unified_diff(
            before.decode("utf-8", errors="replace").splitlines(keepends=True),
            after.decode("utf-8", errors="replace").splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    deleted = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return "".join(lines), added, deleted


class ReversibleTransactionService:
    """Multi-file snapshot/apply/verify/undo boundary for Markdown changes."""

    def __init__(self, vault: Path, store: Any, authorizations: Any) -> None:
        self.vault = vault.resolve()
        self.store = store
        self.authorizations = authorizations
        self.snapshot_root = self.vault / "90-Local-Only/Agent/Snapshots"
        self.diff_root = self.vault / "90-Local-Only/Agent/Action-Diffs"
        self._writer_lock = threading.RLock()

    def apply_change_set(self, change_sets: Any, payload: dict[str, Any]) -> dict[str, Any]:
        change_set_id = str(payload.get("change_set_id") or "")
        authorization_id = str(payload.get("task_authorization_id") or "")
        if not change_set_id or not authorization_id:
            raise ValueError("change_set_and_task_authorization_required")
        record, bundle = change_sets.prepared(change_set_id)
        if record["state"] == "applied":
            existing = self.store.find_agent_action_for_change_set(change_set_id)
            if not existing:
                raise RuntimeError("applied_change_set_action_missing")
            return self._public_result(existing, idempotent=True)
        if record["state"] not in {"planned", "proposed"}:
            raise RuntimeError("change_set_not_planned")
        writes = list(bundle.get("writes") or [])
        self.authorizations.validate(
            authorization_id,
            str(bundle.get("run_id") or ""),
            writes,
        )
        change_sets.validate({"change_set_id": change_set_id})

        transaction_id = f"pi-tx-{uuid.uuid4().hex}"
        details = {
            "transactionId": transaction_id,
            "changeSetId": change_set_id,
            "taskAuthorizationId": authorization_id,
            "runId": str(bundle.get("run_id") or ""),
            "turnId": str(payload.get("turn_id") or ""),
            "sourceMessageId": str(payload.get("source_message_id") or ""),
            "targetExisted": {},
            "diffReference": "",
        }
        action_id = self.store.create_agent_action(
            "apply_vault_change",
            f"{len(writes)} markdown files",
            "low",
            details,
            status="applying",
        )
        change_sets.update_state(change_set_id, "applying", transaction_id)
        files: list[dict[str, Any]] = []
        before_by_path: dict[str, bytes] = {}
        after_by_path: dict[str, bytes] = {}
        with self._writer_lock:
            try:
                for item in writes:
                    relative = str(item["path"])
                    target = safe_note(self.vault, relative)
                    existed = target.is_file()
                    before = target.read_bytes() if existed else b""
                    before_hash = _hash(before) if existed else "missing"
                    expected = str(bundle["base_hashes"][relative])
                    if before_hash != expected:
                        raise RuntimeError("change_set_base_changed")
                    after = (str(item["content"]).rstrip() + "\n").encode("utf-8")
                    before_by_path[relative] = before
                    after_by_path[relative] = after
                    details["targetExisted"][relative] = existed
                    snapshot_path = self.snapshot_root / action_id / relative
                    _atomic_write(snapshot_path, before)
                    self.store.record_file_snapshot(
                        action_id,
                        relative,
                        _hash(before),
                        str(snapshot_path.relative_to(self.vault / "90-Local-Only/Agent")),
                    )

                for relative, after in after_by_path.items():
                    target = safe_note(self.vault, relative)
                    current = target.read_bytes() if target.is_file() else b""
                    current_hash = _hash(current) if target.is_file() else "missing"
                    if current_hash != str(bundle["base_hashes"][relative]):
                        raise RuntimeError("change_set_base_changed")
                    _atomic_write(target, after)

                diffs = []
                for item in writes:
                    relative = str(item["path"])
                    target = safe_note(self.vault, relative)
                    after = after_by_path[relative]
                    after_hash = _hash(after)
                    if not target.is_file() or _hash(target.read_bytes()) != after_hash:
                        raise RuntimeError("vault_write_verification_failed")
                    before = before_by_path[relative]
                    patch, added, deleted = _diff(relative, before, after)
                    before_hash = _hash(before) if details["targetExisted"][relative] else "missing"
                    self.store.record_file_change(
                        action_id,
                        relative,
                        before_hash,
                        after_hash,
                        f"added={added}; deleted={deleted}",
                    )
                    file_result = {
                        "path": relative,
                        "action": "update" if details["targetExisted"][relative] else "create",
                        "beforeHash": before_hash,
                        "afterHash": after_hash,
                        "added": added,
                        "deleted": deleted,
                    }
                    files.append(file_result)
                    diffs.append({**file_result, "diff": patch})
                diff_path = self.diff_root / f"{action_id}.json"
                diff_body = (json.dumps({"actionId": action_id, "files": diffs}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                _atomic_write(diff_path, diff_body)
                details["diffReference"] = str(diff_path.relative_to(self.vault / "90-Local-Only/Agent"))
                details["diffHash"] = _hash(diff_body)
                self.store.complete_agent_action(
                    action_id,
                    "applied",
                    {
                        **details,
                        "files": files,
                        "verified": True,
                        "undoAvailable": True,
                    },
                )
                change_sets.update_state(change_set_id, "applied", transaction_id)
            except Exception:
                rollback_failed = False
                for relative, before in reversed(list(before_by_path.items())):
                    try:
                        target = safe_note(self.vault, relative)
                        if details["targetExisted"].get(relative):
                            _atomic_write(target, before)
                        else:
                            target.unlink(missing_ok=True)
                    except Exception:
                        rollback_failed = True
                state = "failed" if rollback_failed else "rolled_back"
                self.store.complete_agent_action(action_id, state, {**details, "rollbackFailed": rollback_failed})
                change_sets.update_state(change_set_id, state, transaction_id)
                raise
        return {
            "actionId": action_id,
            "transactionId": transaction_id,
            "state": "applied",
            "files": files,
            "verification": {"verified": True},
            "undoAvailable": True,
            "idempotent": False,
        }

    def get(self, action_id: str) -> dict[str, Any]:
        return self._public_result(self.store.get_agent_action(action_id))

    def diff(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_agent_action(action_id)
        reference = str(action["details"].get("diffReference") or "")
        target = (self.vault / "90-Local-Only/Agent" / reference).resolve()
        if not reference or not target.is_relative_to(self.diff_root.resolve()) or not target.is_file() or target.is_symlink():
            raise RuntimeError("action_diff_unavailable")
        body = target.read_bytes()
        if _hash(body) != str(action["details"].get("diffHash") or ""):
            raise RuntimeError("action_diff_tampered")
        payload = json.loads(body.decode("utf-8"))
        if payload.get("actionId") != action_id:
            raise RuntimeError("action_diff_tampered")
        return payload

    def undo(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_agent_action(action_id)
        if action["status"] != "applied" or not action["snapshots"] or not action["changes"]:
            raise ValueError("agent_action_not_undoable")
        snapshots = {item["relative_path"]: item for item in action["snapshots"]}
        changes = {item["relative_path"]: item for item in action["changes"]}
        existed = dict(action["details"].get("targetExisted") or {})
        before_by_path: dict[str, bytes] = {}
        after_by_path: dict[str, bytes] = {}
        with self._writer_lock:
            for relative, change in changes.items():
                target = safe_note(self.vault, relative)
                current = target.read_bytes() if target.is_file() else b""
                if ("missing" if not target.is_file() else _hash(current)) != str(change["after_hash"]):
                    raise RuntimeError("vault_target_changed_since_action")
                snapshot = snapshots.get(relative)
                if not snapshot:
                    raise RuntimeError("snapshot_unavailable")
                snapshot_path = (self.vault / "90-Local-Only/Agent" / str(snapshot["snapshot_reference"])).resolve()
                if not snapshot_path.is_relative_to(self.snapshot_root.resolve()) or not snapshot_path.is_file() or snapshot_path.is_symlink():
                    raise RuntimeError("snapshot_unavailable")
                before = snapshot_path.read_bytes()
                if _hash(before) != str(snapshot["content_hash"]):
                    raise RuntimeError("snapshot_integrity_failed")
                before_by_path[relative] = before
                after_by_path[relative] = current
            try:
                for relative, before in before_by_path.items():
                    target = safe_note(self.vault, relative)
                    if existed.get(relative):
                        _atomic_write(target, before)
                    else:
                        target.unlink(missing_ok=True)
                for relative, before in before_by_path.items():
                    target = safe_note(self.vault, relative)
                    restored = target.read_bytes() if target.is_file() else b""
                    expected = _hash(before) if existed.get(relative) else _hash(b"")
                    if _hash(restored) != expected:
                        raise RuntimeError("undo_verification_failed")
            except Exception:
                for relative, after in after_by_path.items():
                    _atomic_write(safe_note(self.vault, relative), after)
                self.store.complete_agent_action(action_id, "undo_conflicted")
                raise
        undo_id = self.store.create_agent_action(
            "undo_agent_action",
            action_id,
            "low",
            {"undoOf": action_id, "files": sorted(changes)},
            status="applied",
        )
        self.store.complete_agent_action(action_id, "undone", {"undoActionId": undo_id})
        return {"actionId": action_id, "undoActionId": undo_id, "state": "undone", "files": sorted(changes)}

    @staticmethod
    def _public_result(action: dict[str, Any], idempotent: bool = False) -> dict[str, Any]:
        details = action.get("details") or {}
        return {
            "actionId": action["id"],
            "transactionId": details.get("transactionId", ""),
            "state": action["status"],
            "files": details.get("files", []),
            "verification": {"verified": details.get("verified") is True},
            "undoAvailable": action["status"] == "applied" and details.get("undoAvailable") is True,
            "idempotent": idempotent,
        }

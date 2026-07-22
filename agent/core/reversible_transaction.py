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
from agent.core.task_authorization import _safe_vault_path


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


def _exclusive_write(path: Path, body: bytes) -> None:
    """Create a file without ever replacing a concurrent human write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _state_hash(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError("vault_target_changed_since_action")
    if not path.exists():
        return "missing"
    if not path.is_file():
        raise RuntimeError("vault_target_changed_since_action")
    return _hash(path.read_bytes())


def _restore_staged_file(stage: Path, target: Path) -> bool:
    """Restore a staged inode only when nobody recreated the target."""
    if not stage.is_file() or stage.is_symlink() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(stage, target)
    except FileExistsError:
        return False
    stage.unlink()
    return True


def _stage_expected_file(path: Path, expected_hash: str, stage: Path) -> bytes:
    """Atomically take a path, then verify the exact bytes that were taken.

    Moving before hashing closes the check/unlink race with Obsidian. If the
    bytes do not match, they are restored without replacing a concurrently
    recreated target. A conflicting staged copy is intentionally retained in
    the local-only snapshot area for recovery rather than losing user data.
    """
    stage.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists() or stage.is_symlink():
        raise RuntimeError("undo_stage_collision")
    try:
        os.rename(path, stage)
    except FileNotFoundError as error:
        raise RuntimeError("vault_target_changed_since_action") from error
    body = stage.read_bytes()
    if _hash(body) != expected_hash:
        _restore_staged_file(stage, path)
        raise RuntimeError("vault_target_changed_since_action")
    return body


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
        staged_before: dict[str, Path] = {}
        applied_paths: set[str] = set()
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
                    expected = str(bundle["base_hashes"][relative])
                    current_hash = _state_hash(target)
                    if current_hash != expected:
                        raise RuntimeError("change_set_base_changed")
                    if expected != "missing":
                        stage = self.snapshot_root / action_id / "apply-stage" / relative
                        try:
                            _stage_expected_file(target, expected, stage)
                        except RuntimeError as error:
                            raise RuntimeError("change_set_base_changed") from error
                        staged_before[relative] = stage
                    try:
                        _exclusive_write(target, after)
                    except FileExistsError as error:
                        raise RuntimeError("change_set_base_changed") from error
                    applied_paths.add(relative)

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
                for stage in staged_before.values():
                    try:
                        stage.unlink(missing_ok=True)
                    except OSError:
                        # The verified snapshot remains the recovery source; a
                        # stale local-only staging inode is safe to clean later.
                        pass
            except Exception:
                rollback_failed = False
                taken_after: set[str] = set()
                for relative in reversed(list(applied_paths)):
                    try:
                        target = safe_note(self.vault, relative)
                        discard = self.snapshot_root / action_id / "apply-rollback" / relative
                        _stage_expected_file(target, _hash(after_by_path[relative]), discard)
                        taken_after.add(relative)
                        discard.unlink()
                    except Exception:
                        rollback_failed = True
                for relative, stage in staged_before.items():
                    # Restore the pre-action inode only after the exact Agent
                    # output was atomically removed (or if the write never
                    # reached the target and it is still absent). Never replace
                    # a concurrent Obsidian/user file.
                    if relative in applied_paths and relative not in taken_after:
                        continue
                    try:
                        if not _restore_staged_file(stage, safe_note(self.vault, relative)):
                            rollback_failed = True
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

    def organize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Atomically create governed directories and move Markdown notes.

        This is intentionally narrower than a filesystem API: all paths stay
        inside mutable Vault roots, only Markdown files can move, and the
        entire operation is snapshotted and reversible.
        """
        authorization_id = str(payload.get("task_authorization_id") or "")
        run_id = str(payload.get("run_id") or "")
        if not authorization_id or not run_id:
            raise ValueError("organization_authorization_required")
        validated = self.authorizations.validate_organization(
            authorization_id,
            run_id,
            {
                "directories": list(payload.get("directories") or []),
                "moves": list(payload.get("moves") or []),
                "remove_empty_source_dirs": payload.get("remove_empty_source_dirs") is True,
            },
        )
        organization = validated["organization"]
        transaction_id = f"pi-tx-{uuid.uuid4().hex}"
        details: dict[str, Any] = {
            "transactionId": transaction_id,
            "taskAuthorizationId": authorization_id,
            "runId": run_id,
            "turnId": str(payload.get("turn_id") or ""),
            "sourceMessageId": str(payload.get("source_message_id") or ""),
            "title": str(payload.get("title") or "整理 Vault 笔记")[:200],
            "operationType": "vault_organization",
            "requestedDirectories": list(organization["directories"]),
            "removeEmptySourceDirectories": organization["remove_empty_source_dirs"],
            "moves": [],
            "createdDirectories": [],
            "removedSourceDirectories": [],
            "diffReference": "",
        }
        action_id = self.store.create_agent_action(
            "organize_vault_notes",
            f"{len(organization['moves'])} markdown moves",
            "medium",
            details,
            status="applying",
        )
        before_by_source: dict[str, bytes] = {}
        created_target_hashes: dict[str, str] = {}
        created_directories: list[str] = []
        files: list[dict[str, Any]] = []
        with self._writer_lock:
            try:
                # Revalidate after acquiring the writer lock so collisions,
                # protected state and symlinks cannot be stale at apply time.
                organization = self.authorizations.validate_organization(
                    authorization_id,
                    run_id,
                    organization,
                )["organization"]
                for item in organization["moves"]:
                    source_relative = item["source_path"]
                    source = safe_note(self.vault, source_relative)
                    before = source.read_bytes()
                    self.authorizations.ensure_organization_source_content(
                        source_relative, before,
                    )
                    before_by_source[source_relative] = before
                    snapshot_path = self.snapshot_root / action_id / source_relative
                    _atomic_write(snapshot_path, before)
                    self.store.record_file_snapshot(
                        action_id,
                        source_relative,
                        _hash(before),
                        str(snapshot_path.relative_to(self.vault / "90-Local-Only/Agent")),
                    )

                for relative in organization["directories"]:
                    target = _safe_vault_path(self.vault, relative, directory=True)
                    self._ensure_directory(target, created_directories)
                for item in organization["moves"]:
                    destination = safe_note(self.vault, item["target_path"])
                    self._ensure_directory(destination.parent, created_directories)

                for item in organization["moves"]:
                    destination_relative = item["target_path"]
                    destination = safe_note(self.vault, destination_relative)
                    body = before_by_source[item["source_path"]]
                    try:
                        # The preflight collision check is not a commit
                        # boundary: Obsidian can create the same note between
                        # validation and this write.  O_EXCL guarantees an
                        # Agent move never replaces that concurrent human
                        # note.
                        _exclusive_write(destination, body)
                    except FileExistsError as error:
                        raise FileExistsError("vault_move_target_collision") from error
                    created_target_hashes[destination_relative] = _hash(body)

                for item in organization["moves"]:
                    source_relative = item["source_path"]
                    destination_relative = item["target_path"]
                    source = safe_note(self.vault, source_relative)
                    destination = safe_note(self.vault, destination_relative)
                    expected = _hash(before_by_source[source_relative])
                    if not destination.is_file() or _hash(destination.read_bytes()) != expected:
                        raise RuntimeError("vault_move_verification_failed")
                    if not source.is_file() or _hash(source.read_bytes()) != expected:
                        raise RuntimeError("vault_move_source_changed")

                for item in organization["moves"]:
                    source = safe_note(self.vault, item["source_path"])
                    self._unlink_unchanged_source(
                        source,
                        _hash(before_by_source[item["source_path"]]),
                        self.snapshot_root
                        / action_id
                        / "move-stage"
                        / item["source_path"],
                    )
                if organization["remove_empty_source_dirs"]:
                    details["removedSourceDirectories"] = self._remove_empty_source_directories(
                        [item["source_path"] for item in organization["moves"]]
                    )

                for item in organization["moves"]:
                    source_relative = item["source_path"]
                    destination_relative = item["target_path"]
                    destination = safe_note(self.vault, destination_relative)
                    content_hash = _hash(before_by_source[source_relative])
                    if safe_note(self.vault, source_relative).exists():
                        raise RuntimeError("vault_move_source_removal_failed")
                    if not destination.is_file() or _hash(destination.read_bytes()) != content_hash:
                        raise RuntimeError("vault_move_verification_failed")
                    self.store.record_file_change(
                        action_id, source_relative, content_hash, "missing",
                        f"move_to={destination_relative}",
                    )
                    self.store.record_file_change(
                        action_id, destination_relative, "missing", content_hash,
                        f"move_from={source_relative}",
                    )
                    move_result = {
                        "path": destination_relative,
                        "action": "move",
                        "from": source_relative,
                        "to": destination_relative,
                        "beforeHash": content_hash,
                        "afterHash": content_hash,
                    }
                    files.append(move_result)
                    details["moves"].append({
                        "source_path": source_relative,
                        "target_path": destination_relative,
                        "contentHash": content_hash,
                    })

                details["createdDirectories"] = sorted(set(created_directories))
                diff_path = self.diff_root / f"{action_id}.json"
                diff_body = (
                    json.dumps(
                        {
                            "actionId": action_id,
                            "operation": "vault_organization",
                            "directories": details["createdDirectories"],
                            "removedSourceDirectories": details["removedSourceDirectories"],
                            "files": files,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ).encode("utf-8")
                _atomic_write(diff_path, diff_body)
                details["diffReference"] = str(
                    diff_path.relative_to(self.vault / "90-Local-Only/Agent")
                )
                details["diffHash"] = _hash(diff_body)
                self.store.complete_agent_action(
                    action_id,
                    "applied",
                    {**details, "files": files, "verified": True, "undoAvailable": True},
                )
            except Exception:
                rollback_failed = not self._rollback_organization(
                    before_by_source,
                    created_target_hashes,
                    created_directories,
                    self.snapshot_root / action_id / "organization-rollback",
                )
                self.store.complete_agent_action(
                    action_id,
                    "failed" if rollback_failed else "rolled_back",
                    {**details, "rollbackFailed": rollback_failed},
                )
                raise
        return {
            "actionId": action_id,
            "transactionId": transaction_id,
            "state": "applied",
            "files": files,
            "directories": details["createdDirectories"],
            "verification": {"verified": True},
            "undoAvailable": True,
            "idempotent": False,
        }

    @staticmethod
    def _unlink_unchanged_source(
        source: Path,
        expected_hash: str,
        stage: Path,
    ) -> None:
        """Atomically take and verify a source instead of check-then-unlink."""
        try:
            _stage_expected_file(source, expected_hash, stage)
        except RuntimeError as error:
            raise RuntimeError("vault_move_source_changed") from error
        stage.unlink()

    def _ensure_directory(self, target: Path, created: list[str]) -> None:
        missing: list[Path] = []
        current = target
        while current != self.vault and not current.exists():
            missing.append(current)
            current = current.parent
        if current != self.vault and (current.is_symlink() or not current.is_dir()):
            raise ValueError("vault_directory_parent_invalid")
        target.mkdir(parents=True, exist_ok=True)
        for path in reversed(missing):
            relative = path.relative_to(self.vault).as_posix()
            if relative not in created:
                created.append(relative)

    def _rollback_organization(
        self,
        before_by_source: dict[str, bytes],
        created_target_hashes: dict[str, str],
        created_directories: list[str],
        rollback_stage_root: Path | None = None,
    ) -> bool:
        ok = True
        for relative, before in before_by_source.items():
            try:
                target = safe_note(self.vault, relative)
                if target.exists():
                    # An identical source is already restored.  A different
                    # source belongs to Obsidian/the user and must win.
                    if not target.is_file() or _hash(target.read_bytes()) != _hash(before):
                        ok = False
                    continue
                try:
                    _exclusive_write(target, before)
                except FileExistsError:
                    # A human note appeared after the missing check.
                    ok = False
            except Exception:
                ok = False
        for relative, expected in reversed(list(created_target_hashes.items())):
            try:
                target = safe_note(self.vault, relative)
                if not target.exists():
                    continue
                stage_root = rollback_stage_root or (
                    self.snapshot_root / "organization-rollback-orphans" / uuid.uuid4().hex
                )
                stage = stage_root / relative
                try:
                    # Rename first and verify the inode we actually took.  If
                    # Obsidian edits/recreates the target concurrently, the
                    # helper restores or retains both copies without deleting
                    # the human content.
                    _stage_expected_file(target, expected, stage)
                except RuntimeError:
                    ok = False
                    continue
                stage.unlink()
            except Exception:
                ok = False
        self._remove_created_directories(created_directories)
        return ok

    def _remove_empty_source_directories(self, source_paths: list[str]) -> list[str]:
        removed: list[str] = []
        candidates: set[Path] = set()
        for relative in source_paths:
            current = safe_note(self.vault, relative).parent
            while current != self.vault:
                path = current.relative_to(self.vault)
                if len(path.parts) == 1:
                    break
                candidates.add(current)
                current = current.parent
        for target in sorted(candidates, key=lambda path: len(path.parts), reverse=True):
            try:
                target.rmdir()
                removed.append(target.relative_to(self.vault).as_posix())
            except (FileNotFoundError, OSError):
                continue
        return removed

    def _remove_created_directories(self, directories: list[str]) -> None:
        for relative in sorted(set(directories), key=lambda value: value.count("/"), reverse=True):
            try:
                target = _safe_vault_path(self.vault, relative, directory=True)
                target.rmdir()
            except (FileNotFoundError, OSError):
                pass

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

    def undo(
        self,
        action_id: str,
        *,
        authorization_id: str = "",
        run_id: str = "",
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        action = self.store.get_agent_action(action_id)
        self._validate_undo_owner(
            action,
            authorization_id=authorization_id,
            run_id=run_id,
            user_confirmed=user_confirmed,
        )
        if action.get("details", {}).get("operationType") == "vault_organization":
            return self._undo_organization(action)
        if action["status"] != "applied" or not action["snapshots"] or not action["changes"]:
            raise ValueError("agent_action_not_undoable")
        snapshots = {item["relative_path"]: item for item in action["snapshots"]}
        changes = {item["relative_path"]: item for item in action["changes"]}
        existed = dict(action["details"].get("targetExisted") or {})
        before_by_path: dict[str, bytes] = {}
        staged_after: dict[str, Path] = {}
        restored_paths: set[str] = set()
        with self._writer_lock:
            for relative, change in changes.items():
                target = safe_note(self.vault, relative)
                if _state_hash(target) != str(change["after_hash"]):
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
            try:
                # Atomically move every expected post-action file into the
                # local-only staging area before restoring anything. Hashing
                # the moved inode closes the external check/unlink race.
                for relative, change in changes.items():
                    target = safe_note(self.vault, relative)
                    stage = self.snapshot_root / action_id / "undo-stage" / relative
                    _stage_expected_file(target, str(change["after_hash"]), stage)
                    staged_after[relative] = stage
                for relative, before in before_by_path.items():
                    target = safe_note(self.vault, relative)
                    if existed.get(relative):
                        _exclusive_write(target, before)
                        restored_paths.add(relative)
                for relative, before in before_by_path.items():
                    target = safe_note(self.vault, relative)
                    expected = _hash(before) if existed.get(relative) else "missing"
                    if _state_hash(target) != expected:
                        raise RuntimeError("undo_verification_failed")
            except Exception:
                rollback_failed = False
                # Remove only bytes created by this undo. Never replace a
                # target that Obsidian or the user changed concurrently.
                for relative in restored_paths:
                    target = safe_note(self.vault, relative)
                    try:
                        if _state_hash(target) != _hash(before_by_path[relative]):
                            rollback_failed = True
                            continue
                        discard = self.snapshot_root / action_id / "undo-rollback" / relative
                        _stage_expected_file(target, _hash(before_by_path[relative]), discard)
                        discard.unlink()
                    except Exception:
                        rollback_failed = True
                for relative, stage in staged_after.items():
                    if not _restore_staged_file(stage, safe_note(self.vault, relative)):
                        rollback_failed = True
                self.store.complete_agent_action(
                    action_id,
                    "undo_conflicted",
                    {"undoRollbackFailed": rollback_failed},
                )
                raise
            for stage in staged_after.values():
                stage.unlink(missing_ok=True)
        undo_id = self.store.create_agent_action(
            "undo_agent_action",
            action_id,
            "low",
            {"undoOf": action_id, "files": sorted(changes)},
            status="applied",
        )
        self.store.complete_agent_action(action_id, "undone", {"undoActionId": undo_id})
        return {"actionId": action_id, "undoActionId": undo_id, "state": "undone", "files": sorted(changes)}

    def _undo_organization(self, action: dict[str, Any]) -> dict[str, Any]:
        if action["status"] != "applied":
            raise ValueError("agent_action_not_undoable")
        action_id = str(action["id"])
        details = dict(action.get("details") or {})
        moves = list(details.get("moves") or [])
        created_directories = list(details.get("createdDirectories") or [])
        snapshots = {item["relative_path"]: item for item in action.get("snapshots") or []}
        destination_paths = {
            str(item.get("target_path") or item.get("destination_path") or "")
            for item in moves
        }
        before_by_source: dict[str, bytes] = {}
        staged_destinations: dict[str, Path] = {}
        restored_sources: set[str] = set()
        with self._writer_lock:
            for item in moves:
                source_relative = str(item.get("source_path") or "")
                destination_relative = str(
                    item.get("target_path") or item.get("destination_path") or ""
                )
                expected = str(item.get("contentHash") or "")
                source = safe_note(self.vault, source_relative)
                destination = safe_note(self.vault, destination_relative)
                if source.exists():
                    raise RuntimeError("vault_move_source_recreated_since_action")
                if _state_hash(destination) != expected:
                    raise RuntimeError("vault_target_changed_since_action")
                snapshot = snapshots.get(source_relative)
                if not snapshot:
                    raise RuntimeError("snapshot_unavailable")
                snapshot_path = (
                    self.vault / "90-Local-Only/Agent" / str(snapshot["snapshot_reference"])
                ).resolve()
                if (
                    not snapshot_path.is_relative_to(self.snapshot_root.resolve())
                    or not snapshot_path.is_file()
                    or snapshot_path.is_symlink()
                ):
                    raise RuntimeError("snapshot_unavailable")
                before = snapshot_path.read_bytes()
                if _hash(before) != str(snapshot["content_hash"]) or _hash(before) != expected:
                    raise RuntimeError("snapshot_integrity_failed")
                before_by_source[source_relative] = before

            for relative in created_directories:
                directory = _safe_vault_path(self.vault, relative, directory=True)
                if not directory.exists():
                    continue
                for child in directory.rglob("*"):
                    child_relative = child.relative_to(self.vault).as_posix()
                    if child.is_symlink():
                        raise RuntimeError("vault_directory_changed_since_action")
                    if child.is_file() and child_relative not in destination_paths:
                        raise RuntimeError("vault_directory_changed_since_action")

            try:
                # Take all destinations atomically and validate the exact
                # inodes taken before recreating any source path.
                for item in moves:
                    destination_relative = str(
                        item.get("target_path") or item.get("destination_path") or ""
                    )
                    stage = (
                        self.snapshot_root / action_id / "undo-stage" / destination_relative
                    )
                    _stage_expected_file(
                        safe_note(self.vault, destination_relative),
                        str(item.get("contentHash") or ""),
                        stage,
                    )
                    staged_destinations[destination_relative] = stage
                for relative, before in before_by_source.items():
                    _exclusive_write(safe_note(self.vault, relative), before)
                    restored_sources.add(relative)
                for relative, before in before_by_source.items():
                    restored = safe_note(self.vault, relative)
                    if not restored.is_file() or _hash(restored.read_bytes()) != _hash(before):
                        raise RuntimeError("undo_verification_failed")
                self._remove_created_directories(created_directories)
            except Exception:
                rollback_failed = False
                for relative in restored_sources:
                    target = safe_note(self.vault, relative)
                    try:
                        expected = _hash(before_by_source[relative])
                        if _state_hash(target) != expected:
                            rollback_failed = True
                            continue
                        discard = self.snapshot_root / action_id / "undo-rollback" / relative
                        _stage_expected_file(target, expected, discard)
                        discard.unlink()
                    except Exception:
                        rollback_failed = True
                for relative, stage in staged_destinations.items():
                    if not _restore_staged_file(stage, safe_note(self.vault, relative)):
                        rollback_failed = True
                self.store.complete_agent_action(
                    action_id,
                    "undo_conflicted",
                    {"undoRollbackFailed": rollback_failed},
                )
                raise
            for stage in staged_destinations.values():
                stage.unlink(missing_ok=True)
        undo_id = self.store.create_agent_action(
            "undo_agent_action",
            action_id,
            "medium",
            {"undoOf": action_id, "operationType": "vault_organization"},
            status="applied",
        )
        self.store.complete_agent_action(action_id, "undone", {"undoActionId": undo_id})
        return {
            "actionId": action_id,
            "undoActionId": undo_id,
            "state": "undone",
            "files": sorted(before_by_source),
        }

    def _validate_undo_owner(
        self,
        action: dict[str, Any],
        *,
        authorization_id: str,
        run_id: str,
        user_confirmed: bool,
    ) -> None:
        """Keep model-initiated undo inside the action's exact Run lineage.

        The authenticated UI has a separate explicit user-confirmed route;
        model tools must prove both the active authorization and originating
        Run so an observed action id is never a cross-Run capability.
        """
        if user_confirmed:
            return
        if not authorization_id or not run_id:
            raise PermissionError("undo_action_authorization_required")
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization.get("status") != "active":
            raise PermissionError("task_authorization_expired")
        if str(authorization.get("runId") or "") != run_id:
            raise PermissionError("task_authorization_run_mismatch")
        details = dict(action.get("details") or {})
        if (
            str(details.get("taskAuthorizationId") or "") != authorization_id
            or str(details.get("runId") or "") != run_id
        ):
            raise PermissionError("undo_action_run_mismatch")

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

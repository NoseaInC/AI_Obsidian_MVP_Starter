from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any


AUTONOMY_MODES = {"cautious", "balanced", "high"}
DENIED_ROOTS = {"Private", "Personal", "Secrets"}
MANAGED_ROOTS = {
    "01-Inbox/Agent-Managed",
    "30-Learning/Agent-Managed",
    "90-Local-Only/Agent-Managed",
}
LOW_RISK_DRAFT_ROOTS = {
    "01-Inbox", "10-Sources", "20-Knowledge/Concepts", "20-Knowledge/Topics",
}


def _hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temp.open("wb") as handle:
        handle.write(body); handle.flush(); os.fsync(handle.fileno())
    os.replace(temp, path)


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end >= 0 else ""


def render_managed_block(text: str, block: str, content: str) -> str:
    if not re.fullmatch(r"[a-z0-9-]{1,40}", block):
        raise ValueError("invalid_managed_block")
    start = f"<!-- agent:managed:{block}:start -->"
    end = f"<!-- agent:managed:{block}:end -->"
    replacement = f"{start}\n{content.rstrip()}\n{end}"
    if start in text or end in text:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        if not pattern.search(text):
            raise ValueError("managed_block_corrupt")
        return pattern.sub(replacement, text, count=1)
    separator = "" if not text else "\n\n" if not text.endswith("\n\n") else ""
    return text + separator + replacement + "\n"


class VaultAutonomyService:
    """Bounded auto-write boundary with snapshot, audit and conflict-safe undo."""

    def __init__(self, vault: Path, store: Any) -> None:
        self.vault = vault.resolve(); self.store = store
        self.snapshot_root = self.vault / "90-Local-Only/Agent/Snapshots"
        if self.store.get_setting("autonomy_mode") is None:
            self.store.set_setting("autonomy_mode", "high")
            self.store.set_setting("autonomy_permission_summary_acknowledged", False)

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.store.get_setting("autonomy_mode", "high"),
            "permissionSummaryAcknowledged": bool(self.store.get_setting("autonomy_permission_summary_acknowledged", False)),
            "summary": {
                "can": ["读取授权 Vault", "自动调整今日", "维护 Agent 管理区域", "对普通笔记执行低风险受控修改"],
                "willNot": ["静默删除文件", "覆盖 protected/core", "访问 Vault 外未授权路径", "访问本地网络"],
            },
        }

    def set_mode(self, mode: str, *, acknowledge_summary: bool = False) -> dict[str, Any]:
        if mode not in AUTONOMY_MODES:
            raise ValueError("invalid_autonomy_mode")
        self.store.set_setting("autonomy_mode", mode)
        if acknowledge_summary:
            self.store.set_setting("autonomy_permission_summary_acknowledged", True)
        return self.status()

    def _target(self, relative_path: str, *, allow_missing: bool = False) -> tuple[Path, str]:
        relative = Path(str(relative_path))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("invalid_vault_path")
        if relative.parts[0] in DENIED_ROOTS:
            raise PermissionError("agent_access_denied")
        target = (self.vault / relative).resolve()
        if not target.is_relative_to(self.vault):
            raise ValueError("vault_path_escape")
        if target.exists() and target.is_symlink():
            raise ValueError("symlink_target_not_allowed")
        if not allow_missing and not target.is_file():
            raise FileNotFoundError(relative_path)
        return target, relative.as_posix()

    def classify(self, relative_path: str, *, allow_missing: bool = False) -> dict[str, Any]:
        target, relative = self._target(relative_path, allow_missing=allow_missing)
        managed = any(relative == root or relative.startswith(root + "/") for root in MANAGED_ROOTS)
        if not target.exists():
            return {"path": relative, "scope": "agent-managed" if managed else "ordinary", "protected": not managed, "exists": False}
        body = target.read_text(encoding="utf-8", errors="replace") if target.suffix.casefold() == ".md" else ""
        frontmatter = _frontmatter(body).casefold()
        status_match = re.search(r"(?m)^\s*status\s*:\s*['\"]?([^\n'\"]+)", frontmatter)
        status = status_match.group(1).strip() if status_match else ""
        protected = (
            status in {"reviewed", "core"}
            or bool(re.search(r"(?m)^\s*agent_access\s*:\s*['\"]?denied", frontmatter))
            or bool(re.search(r"(?m)^\s*agent_protected\s*:\s*(?:true|yes|1)", frontmatter))
            or relative.startswith("10-Sources/")
        )
        return {"path": relative, "scope": "protected" if protected else "agent-managed" if managed else "ordinary", "protected": protected, "exists": True, "status": status}

    def apply_low_risk(self, request: dict[str, Any]) -> dict[str, Any]:
        relative = str(request.get("path") or "")
        operation = str(request.get("operation") or "update_managed_block")
        content = str(request.get("content") or "")
        # A structured source/method draft can contain derivations or a bounded
        # pasted excerpt; keep a hard ceiling without forcing it into fragments.
        if len(content) > 120_000:
            raise ValueError("low_risk_content_too_large")
        allow_missing = operation in {"create_agent_managed_note", "create_draft_note"}
        classification = self.classify(relative, allow_missing=allow_missing)
        mode = str(self.store.get_setting("autonomy_mode", "high"))
        if classification["protected"] and not (operation == "create_draft_note" and not classification["exists"]):
            return {"applied": False, "requiresConfirmation": True, "reason": "protected_or_core", "classification": classification}
        if mode == "cautious":
            return {"applied": False, "requiresConfirmation": True, "reason": "cautious_mode", "classification": classification}
        if operation == "create_agent_managed_note" and classification["scope"] != "agent-managed":
            return {"applied": False, "requiresConfirmation": True, "reason": "new_note_outside_managed_scope", "classification": classification}
        if operation == "create_draft_note":
            allowed_root = next((root for root in LOW_RISK_DRAFT_ROOTS if relative == root or relative.startswith(root + "/")), "")
            meta = _frontmatter(content).casefold()
            safe_draft = bool(re.search(r"(?m)^\s*status\s*:\s*(?:ai-draft|draft|inbox)\s*$", meta)) and bool(
                re.search(r"(?m)^\s*agent_managed\s*:\s*(?:true|yes|1)\s*$", meta)
            )
            if mode != "high" or not allowed_root or not safe_draft:
                return {"applied": False, "requiresConfirmation": True, "reason": "draft_creation_requires_confirmation", "classification": classification}
        if operation not in {"update_managed_block", "create_agent_managed_note", "create_draft_note"}:
            raise ValueError("unsupported_low_risk_operation")

        target, relative = self._target(relative, allow_missing=allow_missing)
        existed = target.exists()
        before = target.read_bytes() if existed else b""
        before_hash = _hash(before)
        expected = str(request.get("expected_hash") or before_hash)
        if expected != before_hash:
            raise RuntimeError("vault_target_changed")
        if operation == "update_managed_block":
            if not existed or target.suffix.casefold() != ".md":
                raise ValueError("managed_block_requires_markdown")
            after_text = render_managed_block(before.decode("utf-8"), str(request.get("block") or "learning-brain"), content)
        else:
            if existed:
                raise FileExistsError(relative)
            if target.suffix.casefold() != ".md":
                raise ValueError("agent_managed_note_must_be_markdown")
            after_text = content.rstrip() + "\n"
        after = after_text.encode("utf-8")
        after_hash = _hash(after)
        details = {
            "scope": classification["scope"], "reason": str(request.get("reason") or "低风险 Agent 维护"),
            "sourceConversationId": str(request.get("source_conversation_id") or ""),
            "sourceArtifactId": str(request.get("source_artifact_id") or ""),
            "targetExisted": existed, "operation": operation,
        }
        action_id = self.store.create_agent_action(operation, relative, "low", details)
        snapshot_path = self.snapshot_root / action_id / relative
        _atomic_write(snapshot_path, before)
        snapshot_reference = str(snapshot_path.relative_to(self.vault / "90-Local-Only/Agent"))
        self.store.record_file_snapshot(action_id, relative, before_hash, snapshot_reference)
        try:
            current = target.read_bytes() if target.exists() else b""
            if _hash(current) != before_hash:
                raise RuntimeError("vault_target_changed")
            _atomic_write(target, after)
            if _hash(target.read_bytes()) != after_hash:
                raise RuntimeError("vault_write_verification_failed")
            added = max(0, after_text.count("\n") - before.decode("utf-8", errors="replace").count("\n"))
            self.store.record_file_change(action_id, relative, before_hash, after_hash, f"operation={operation}; added_lines={added}; bytes={len(before)}->{len(after)}")
            self.store.complete_agent_action(action_id, "applied", {"beforeHash": before_hash, "afterHash": after_hash})
        except Exception:
            if existed:
                _atomic_write(target, before)
            elif target.exists():
                target.unlink()
            self.store.complete_agent_action(action_id, "rolled_back")
            raise
        return {"applied": True, "requiresConfirmation": False, "actionId": action_id,
                "path": relative, "beforeHash": before_hash, "afterHash": after_hash,
                "undoAvailable": True, "classification": classification}

    def undo(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_agent_action(action_id)
        if action["status"] != "applied" or not action["snapshots"] or not action["changes"]:
            raise ValueError("agent_action_not_undoable")
        snapshot, change = action["snapshots"][0], action["changes"][0]
        target, relative = self._target(str(snapshot["relative_path"]), allow_missing=True)
        current = target.read_bytes() if target.exists() else b""
        if _hash(current) != str(change["after_hash"]):
            raise RuntimeError("vault_target_changed_since_action")
        snapshot_path = (self.vault / "90-Local-Only/Agent" / str(snapshot["snapshot_reference"])).resolve()
        if not snapshot_path.is_relative_to(self.snapshot_root.resolve()) or not snapshot_path.is_file() or snapshot_path.is_symlink():
            raise RuntimeError("snapshot_unavailable")
        before = snapshot_path.read_bytes()
        if _hash(before) != str(snapshot["content_hash"]):
            raise RuntimeError("snapshot_integrity_failed")
        undo_id = self.store.create_agent_action("undo", relative, "low", {"undoOf": action_id, "reason": "用户撤销 Agent 修改"})
        if not action["details"].get("targetExisted") and before == b"":
            target.unlink(missing_ok=True)
        else:
            _atomic_write(target, before)
        restored_hash = _hash(target.read_bytes() if target.exists() else b"")
        self.store.record_file_change(undo_id, relative, str(change["after_hash"]), restored_hash, f"undo={action_id}")
        self.store.complete_agent_action(undo_id, "applied", {"restoredHash": restored_hash})
        self.store.complete_agent_action(action_id, "undone", {"undoActionId": undo_id})
        return {"actionId": action_id, "undoActionId": undo_id, "state": "undone", "path": relative}

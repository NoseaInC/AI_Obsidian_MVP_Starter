from __future__ import annotations

import hashlib
import json
import re
import sys
import uuid
from difflib import unified_diff
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ingest_pdf

from .vault_access import safe_note


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _protected(path: Path) -> bool:
    if not path.is_file():
        return False
    meta = ingest_pdf.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return str(meta.get("status", "")) in ingest_pdf.READ_ONLY_STATUSES


_BARE_WRITE_COMMANDS = {"写入", "保存", "确认写入", "执行写入", "按此写入", "按这个写入"}


def _bare_write_command_artifact(relative: str, content: str) -> bool:
    """Reject the old failure mode where a control command became note content.

    The target title alone is not enough: the guard requires the proposed
    document's ``原始内容`` section to contain only the same terse command.
    This keeps ordinary notes that merely discuss saving/writing valid.
    """
    stem = re.sub(r"[\s。.!！]+", "", Path(relative).stem)
    if stem not in _BARE_WRITE_COMMANDS:
        return False
    original = re.search(r"(?ims)^##\s*原始内容\s*$\s*(.*?)(?=^##\s|\Z)", content)
    if not original:
        return False
    command = re.sub(r"[\s。.!！]+", "", original.group(1))
    return command in _BARE_WRITE_COMMANDS


class ChangeSetTools:
    def __init__(self, vault: Path, store: Any) -> None:
        self.vault, self.store = vault.resolve(), store
        self.root = self.vault / "90-Local-Only/Agent/Change-Sets"

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        run_id = str(payload.get("run_id", ""))
        writes = list(payload.get("writes", []))
        if not run_id or not writes or len(writes) > 10:
            raise ValueError("change_set_requires_1_to_10_writes")
        normalized: list[dict[str, Any]] = []
        hashes: dict[str, str] = {}
        for raw in writes:
            relative, content = str(raw.get("path", "")), str(raw.get("content", ""))
            path = safe_note(self.vault, relative)
            if not content.strip():
                raise ValueError("empty_change_set_content")
            if _bare_write_command_artifact(relative, content):
                raise ValueError("ambiguous_write_command_content")
            exists = path.exists()
            if exists and _protected(path):
                raise ValueError("reviewed_core_read_only")
            action = "update" if exists else "create"
            hashes[relative] = _hash(path.read_text(encoding="utf-8")) if exists else "missing"
            normalized.append({"path": relative, "content": content, "action": action, "category": str(raw.get("category", "brain-proposal"))})
        change_set_id = f"brain-cs-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        payload_path = self.root / f"{change_set_id}.json"
        bundle = {"id": change_set_id, "run_id": run_id, "title": str(payload.get("title", "Agent 提案")), "writes": normalized, "base_hashes": hashes}
        ingest_pdf.atomic_write(payload_path, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
        metadata = [{"path": item["path"], "action": item["action"], "category": item["category"], "content_sha256": _hash(item["content"]), "payload_path": str(payload_path.relative_to(self.vault))} for item in normalized]
        preview = f"{bundle['title']} · {len(normalized)} 个候选写入"
        if self.store.is_assistant_runtime_run(run_id):
            self.store.create_assistant_change_set(change_set_id, run_id, bundle["title"], metadata, preview, hashes)
        else:
            self.store.create_brain_change_set(change_set_id, run_id, bundle["title"], metadata, preview, hashes)
        public_metadata = [{key: value for key, value in item.items() if key != "payload_path"} for item in metadata]
        return {"id": change_set_id, "state": "proposed", "title": bundle["title"], "writes": public_metadata, "preview": preview, "requires_confirmation": True}

    def _bundle(self, change_set_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        record = self.get_record(change_set_id)
        paths = {str(item.get("payload_path", "")) for item in record["writes"]}
        if len(paths) != 1:
            raise RuntimeError("change_set_payload_missing")
        payload_path = (self.vault / paths.pop()).resolve()
        if not payload_path.is_relative_to(self.root.resolve()) or not payload_path.is_file() or payload_path.is_symlink():
            raise RuntimeError("change_set_payload_invalid")
        bundle = json.loads(payload_path.read_text(encoding="utf-8"))
        if bundle.get("id") != change_set_id or bundle.get("run_id") != record.get("run_id"):
            raise RuntimeError("change_set_payload_tampered")
        metadata = {str(item.get("path")): item for item in record["writes"]}
        if len(metadata) != len(bundle.get("writes", [])):
            raise RuntimeError("change_set_payload_tampered")
        for item in bundle.get("writes", []):
            saved = metadata.get(str(item.get("path", "")))
            if not saved or saved.get("action") != item.get("action") or saved.get("category") != item.get("category") or saved.get("content_sha256") != _hash(str(item.get("content", ""))):
                raise RuntimeError("change_set_payload_tampered")
        if record.get("base_hashes") != bundle.get("base_hashes"):
            raise RuntimeError("change_set_payload_tampered")
        return record, bundle

    def get_record(self, change_set_id: str) -> dict[str, Any]:
        """Resolve a proposal from its owning runtime without exposing payload text."""
        try:
            return self.store.get_assistant_change_set(change_set_id)
        except RuntimeError:
            return self.store.get_brain_change_set(change_set_id)

    def public_record(self, change_set_id: str) -> dict[str, Any]:
        record = self.get_record(change_set_id)
        return {
            **record,
            "writes": [
                {key: value for key, value in item.items() if key != "payload_path"}
                for item in record.get("writes", [])
            ],
        }

    def diff(self, change_set_id: str) -> dict[str, Any]:
        """Build an authenticated preview without persisting note text in SQLite."""
        record, bundle = self._bundle(change_set_id)
        files: list[dict[str, Any]] = []
        for item in bundle["writes"]:
            relative = str(item["path"])
            target = safe_note(self.vault, relative)
            before = target.read_text(encoding="utf-8") if target.exists() else ""
            after = str(item["content"])
            lines = list(unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
            ))
            files.append({
                "path": relative,
                "action": item["action"],
                "added": sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
                "deleted": sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
                "diff": "".join(lines),
            })
        return {"id": change_set_id, "state": record["state"], "files": files}

    def validate(self, payload: dict[str, Any]) -> dict[str, Any]:
        change_set_id = str(payload.get("change_set_id", ""))
        record, bundle = self._bundle(change_set_id)
        if record["state"] == "applied":
            return {"id": change_set_id, "valid": True, "idempotent": True, "state": "applied"}
        if record["state"] != "proposed":
            raise RuntimeError("change_set_not_proposed")
        for item in bundle["writes"]:
            path = safe_note(self.vault, item["path"])
            if _bare_write_command_artifact(str(item["path"]), str(item["content"])):
                raise RuntimeError("ambiguous_write_command_content")
            current = _hash(path.read_text(encoding="utf-8")) if path.exists() else "missing"
            if current != bundle["base_hashes"][item["path"]]:
                raise RuntimeError("change_set_base_changed")
            if path.exists() and _protected(path):
                raise RuntimeError("reviewed_core_read_only")
        return {"id": change_set_id, "valid": True, "idempotent": False, "state": record["state"]}

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        change_set_id = str(payload.get("change_set_id", ""))
        if payload.get("confirmed") is not True:
            raise PermissionError("explicit_confirmation_required")
        valid = self.validate({"change_set_id": change_set_id})
        if valid["idempotent"]:
            return {"id": change_set_id, "state": "applied", "idempotent": True}
        _, bundle = self._bundle(change_set_id)
        plan = ingest_pdf.WritePlan(source_id=change_set_id, vault=self.vault, writes=[
            ingest_pdf.PlannedWrite(safe_note(self.vault, item["path"]), item["content"], item["action"], item["category"])
            for item in bundle["writes"]
        ])
        transaction_id = f"brain-{uuid.uuid4().hex}"
        journal = ingest_pdf.execute_plan(plan, transaction_id=transaction_id)
        if self.store.is_assistant_runtime_run(str(bundle.get("run_id") or "")):
            self.store.update_assistant_change_set(change_set_id, "applied", transaction_id)
        else:
            self.store.update_brain_change_set(change_set_id, "applied", transaction_id)
        return {"id": change_set_id, "state": "applied", "idempotent": False, "transaction_id": transaction_id, "journal": str(journal.relative_to(self.vault))}

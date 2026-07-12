#!/usr/bin/env python3
"""Transactional review workflow for generated Markdown artifacts."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import ingest_pdf as ingest


REVIEWABLE_ROLES = {"paper-draft", "topic", "concept", "update-suggestion"}


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def update_frontmatter(text: str, updates: dict[str, Any | None]) -> str:
    fm, body = ingest._split_frontmatter_text(text)
    entries = ingest._frontmatter_entries(fm)
    retained: list[str] = []
    seen: set[str] = set()
    for key, lines in entries:
        if key in updates:
            seen.add(key)
            value = updates[key]
            if value is not None:
                retained.extend(ingest._frontmatter([(key, value)]).splitlines(keepends=True)[1:-1])
        else:
            retained.extend(lines)
    for key, value in updates.items():
        if key not in seen and value is not None:
            retained.extend(ingest._frontmatter([(key, value)]).splitlines(keepends=True)[1:-1])
    return "---\n" + "".join(retained) + "---\n" + body


def scan_artifacts(vault: Path) -> list[dict[str, Any]]:
    roots = [vault / "90-Local-Only/AI-Drafts", vault / "20-Knowledge/Topics", vault / "20-Knowledge/Concepts"]
    found: dict[str, dict[str, Any]] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = ingest.parse_frontmatter(text)
            role = str(meta.get("artifact_role", ""))
            artifact_id = str(meta.get("artifact_id", ""))
            if role not in REVIEWABLE_ROLES or not artifact_id:
                continue
            if artifact_id in found:
                raise RuntimeError(f"重复 artifact_id：{artifact_id}")
            found[artifact_id] = {
                "artifact_id": artifact_id, "artifact_role": role, "path": path,
                "status": str(meta.get("status", "")),
                "review_state": str(meta.get("review_state", "pending")),
                "generated_from": str(meta.get("generated_from", "")),
                "source_notes": meta.get("source_notes", []),
            }
    return sorted(found.values(), key=lambda item: (item["review_state"], item["artifact_role"], item["artifact_id"]))


def get_artifact(vault: Path, artifact_id: str) -> dict[str, Any]:
    matches = [item for item in scan_artifacts(vault) if item["artifact_id"] == artifact_id]
    if not matches:
        raise RuntimeError(f"Artifact 不存在：{artifact_id}")
    return matches[0]


def review_packet(vault: Path, artifact_id: str) -> str:
    item = get_artifact(vault, artifact_id)
    path: Path = item["path"]
    text = path.read_text(encoding="utf-8")
    pages = sorted(set(re.findall(r"第\s*(\d+)\s*页", text)), key=int)
    sections = sorted(set(re.findall(r"对话片段\s*(\d+)", text)), key=int)
    return "\n".join([
        f"# Review: {artifact_id}", "", f"- Role: `{item['artifact_role']}`",
        f"- State: `{item['review_state']}` / `{item['status']}`", f"- Path: `{path}`",
        f"- Source: `{item['generated_from']}`", f"- Evidence pages: {', '.join(pages) or 'not parsed'}",
        f"- Conversation sections: {', '.join(sections) or 'not parsed'}",
        "", "## Content", "", text,
    ])


def artifact_diff(vault: Path, artifact_id: str) -> str:
    item = get_artifact(vault, artifact_id)
    path: Path = item["path"]
    proposed = path.read_text(encoding="utf-8").splitlines(keepends=True)
    before: list[str] = []
    before_name = "/dev/null"
    if item["artifact_role"] == "update-suggestion":
        meta = ingest.parse_frontmatter("".join(proposed))
        link = str(meta.get("target_note", ""))
        match = re.fullmatch(r"\[\[([^\]]+)\]\]", link)
        if match:
            candidates = list((vault / "20-Knowledge").rglob(f"{match.group(1)}.md"))
            if len(candidates) == 1:
                before_name = str(candidates[0])
                before = candidates[0].read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(difflib.unified_diff(
        before, proposed, fromfile=before_name, tofile=str(path), n=3,
    ))


def transition(vault: Path, artifact_id: str, action: str, reason: str = "", *, _locked: bool = False) -> Path:
    vault = vault.resolve()
    if not _locked:
        with ingest.vault_write_lock(vault):
            return transition(vault, artifact_id, action, reason, _locked=True)
    item = get_artifact(vault, artifact_id)
    path: Path = item["path"]
    before = path.read_text(encoding="utf-8")
    meta = ingest.parse_frontmatter(before)
    status, state = str(meta.get("status", "")), str(meta.get("review_state", "pending"))
    now = datetime.now().astimezone()
    if action in {"approve", "approve-edited"}:
        if status != "ai-draft" or state not in {"pending", "reopened", ""}:
            raise RuntimeError("只有 pending ai-draft 可以接受。")
        updates = {
            "status": "reviewed", "review_state": "accepted",
            "reviewed_at": now.isoformat(timespec="seconds"),
            "review_mode": "edited" if action == "approve-edited" else "as-generated",
            "rejection_reason": None,
        }
    elif action == "reject":
        if status != "ai-draft" or not reason.strip():
            raise RuntimeError("拒绝 pending ai-draft 时必须提供原因。")
        updates = {
            "status": "rejected", "review_state": "rejected",
            "rejected_at": now.isoformat(timespec="seconds"), "rejection_reason": reason.strip(),
        }
    elif action == "reopen":
        if status != "rejected" or state != "rejected":
            raise RuntimeError("仅 rejected artifact 可重新打开；reviewed/core 不降级。")
        updates = {
            "status": "ai-draft", "review_state": "reopened", "reopened_at": now.isoformat(timespec="seconds"),
            "rejection_reason": None,
        }
    else:
        raise RuntimeError(f"未知审核动作：{action}")
    after = update_frontmatter(before, updates)
    stamp = now.strftime("%Y%m%dT%H%M%S%f%z")
    safe_id = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()[:12]
    audit = vault / "90-Local-Only/Review-Audit" / f"{stamp}-{safe_id}.json"
    audit_content = _json({
        "artifact_id": artifact_id, "artifact_role": item["artifact_role"], "action": action,
        "reason": reason, "path": str(path.relative_to(vault)), "before_sha256": _hash(before),
        "after_sha256": _hash(after), "at": now.isoformat(timespec="seconds"),
    })
    plan = ingest.WritePlan(source_id=f"review:{artifact_id}", vault=vault, writes=[
        ingest.PlannedWrite(path, after, "update", "review-transition"),
        ingest.PlannedWrite(audit, audit_content, "create", "review-audit"),
    ])
    ingest.execute_plan(plan, transaction_id=f"review-{stamp}-{safe_id}", acquire_lock=False)
    return audit


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Review generated artifacts")
    root.add_argument("--vault", required=True)
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("show", "diff", "approve", "approve-edited", "reopen"):
        command = sub.add_parser(name); command.add_argument("artifact_id")
    reject = sub.add_parser("reject"); reject.add_argument("artifact_id"); reject.add_argument("--reason", required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    vault = Path(args.vault).expanduser().resolve()
    if args.command == "list":
        serializable = [{**item, "path": str(item["path"])} for item in scan_artifacts(vault)]
        print(_json({"artifacts": serializable}), end="")
    elif args.command == "show": print(review_packet(vault, args.artifact_id))
    elif args.command == "diff": print(artifact_diff(vault, args.artifact_id), end="")
    elif args.command == "reject": print(transition(vault, args.artifact_id, "reject", args.reason))
    else: print(transition(vault, args.artifact_id, args.command))


if __name__ == "__main__":
    try: main()
    except RuntimeError as exc: raise SystemExit(f"失败：{exc}") from None

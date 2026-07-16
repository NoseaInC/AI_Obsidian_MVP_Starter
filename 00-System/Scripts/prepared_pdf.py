#!/usr/bin/env python3
"""Immutable Prepare → Inspect → Apply Prepared workflow for PDF ingestion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import ingest_pdf as ingest


PREPARED_REL = Path("90-Local-Only/Prepared-Bundles")
APPLIED_REL = Path("90-Local-Only/Prepared-State/applied")
REJECTED_REL = Path("90-Local-Only/Prepared-State/rejected")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON 顶层必须是对象：{path}")
    return data


def _within(vault: Path, relative: str) -> Path:
    candidate = (vault / relative).resolve()
    if not candidate.is_relative_to(vault.resolve()):
        raise RuntimeError(f"路径越出 Vault：{relative}")
    return candidate


def _role(category: str) -> str:
    return {
        "source": "source-index", "paper-draft": "paper-draft", "topic": "topic",
        "concept": "concept", "suggestion": "update-suggestion",
    }.get(category, category)


def _managed_fields(category: str) -> set[str]:
    return ingest.SOURCE_MANAGED_FIELDS if category == "source" else ingest.ARTIFACT_MANAGED_FIELDS


def controlled_fingerprint(text: str, category: str) -> str:
    fm, body = ingest._split_frontmatter_text(text)
    meta = ingest.parse_frontmatter(text)
    controlled_meta = {key: meta.get(key) for key in sorted(_managed_fields(category)) if key in meta}
    role = _role(category)
    start, end = ingest.managed_markers(role)
    start_at, end_at = body.find(start), body.find(end)
    if start_at < 0 and role == "source-index":
        start, end = ingest.LEGACY_MANAGED_START, ingest.LEGACY_MANAGED_END
        start_at, end_at = body.find(start), body.find(end)
    if start_at >= 0 and end_at >= start_at:
        managed = body[start_at:end_at + len(end)]
    else:
        heading = "## 人工批注" if category == "source" else "## 人工审核区"
        managed = body.split(heading, 1)[0]
    return _sha_text(_json({"frontmatter": controlled_meta, "managed": managed}))


def target_snapshot(path: Path, category: str) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    text = path.read_text(encoding="utf-8", errors="strict")
    meta = ingest.parse_frontmatter(text)
    return {
        "exists": True,
        "sha256": _sha_text(text),
        "controlled_sha256": controlled_fingerprint(text, category),
        "status": str(meta.get("status", "")),
        "artifact_id": str(meta.get("artifact_id", "")),
    }


def _preview(plan: ingest.WritePlan, prepared_id: str, result: dict[str, Any] | None = None) -> str:
    lines = [f"# Prepared Bundle {prepared_id}", "", f"source_id: `{plan.source_id}`", "", "## Change Set", ""]
    for item in plan.writes:
        lines.append(f"- **{item.action}** `{item.category}` — `{item.path}`")
    if plan.skipped:
        lines.extend(["", "## Reused / skipped", *[f"- {item}" for item in plan.skipped]])
    if plan.warnings:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in plan.warnings]])
    if result and isinstance(result.get("evidence"), list):
        lines.extend(["", "## Evidence pages"])
        for item in result["evidence"]:
            lines.append(f"- {item.get('claim', '')} — pages {', '.join(map(str, item.get('pages', [])))} — {item.get('kind', '')}")
    lines.extend(["", "This bundle has not changed knowledge files. Inspect before apply-prepared.", ""])
    return "\n".join(lines)


def prepare_bundle(
    args: argparse.Namespace, *, client: ingest.ModelClient | None = None,
    pages: list[str] | None = None, now: datetime | None = None,
    progress: Callable[[str, int], None] | None = None,
) -> Path:
    report = progress or (lambda _stage, _percent: None)
    vault = Path(args.vault).expanduser().resolve()
    pdf = Path(args.pdf).expanduser().resolve()
    if not vault.is_dir() or not pdf.is_file():
        raise RuntimeError("Vault 或 PDF 不存在。")
    report("reading_file", 10)
    pages = pages if pages is not None else ingest.extract_pages(pdf)
    report("extracting_pages", 25)
    digest = ingest.file_sha256(pdf)
    inventory = ingest.scan_vault(vault)
    report("scanning_vault", 35)
    if client is None:
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY。")
        client = ingest.DeepSeekClient(key, args.base_url)

    prompt = ingest.result_contract(args.domain_focus, args.kind, ingest.inventory_for_prompt(inventory))
    prompt += "\n\n带 PAGE 标记的完整本地提取文本：\n" + "\n".join(pages)
    report("calling_model", 50)
    raw = client.create_json(model=args.model, system=ingest.SYNTHESIS_SYSTEM, user=prompt)
    result = ingest.parse_and_validate_json(raw, len(pages))
    report("validating_result", 70)
    now = now or datetime.now().astimezone()
    plan = ingest.build_plan(
        vault, pdf_path=pdf, digest=digest, result=result, model=args.model,
        kind=args.kind, domain_focus=args.domain_focus, max_concepts=args.max_concepts,
        inventory=inventory, now=now,
    )
    report("generating_change_set", 85)

    prepared_id = f"{now.strftime('%Y%m%dT%H%M%S%z')}-{digest[:12]}-{uuid.uuid4().hex[:8]}"
    root = vault / PREPARED_REL
    root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{prepared_id}.", dir=root))
    final = root / prepared_id
    request = {
        "prepared_id": prepared_id, "created_at": now.isoformat(timespec="seconds"),
        "pdf_path": str(pdf), "pdf_sha256": digest, "page_count": len(pages),
        "kind": args.kind, "domain_focus": args.domain_focus, "model": args.model,
        "max_concepts": args.max_concepts, "source_id": plan.source_id,
    }
    changes: list[dict[str, Any]] = []
    artifacts = temp / "artifacts"
    artifacts.mkdir()
    for index, item in enumerate(plan.writes):
        relative = str(item.path.resolve().relative_to(vault))
        artifact_file = f"artifacts/{index:04d}.md"
        (temp / artifact_file).write_text(item.content, encoding="utf-8", newline="\n")
        changes.append({
            "target": relative, "action": item.action, "category": item.category,
            "artifact_file": artifact_file, "content_sha256": _sha_text(item.content),
            "precondition": target_snapshot(item.path, item.category),
        })
    payloads = {
        "request.json": _json(request),
        "normalized-result.json": _json(result),
        "evidence.json": _json({"evidence": result["evidence"], "inferences": result["inferences"]}),
        "vault-inventory.json": _json(ingest.inventory_for_prompt(inventory)),
        "change-set.json": _json({"source_id": plan.source_id, "writes": changes, "skipped": plan.skipped, "warnings": plan.warnings}),
        "preview.md": _preview(plan, prepared_id, result),
        "extracted-text.txt": "\n".join(pages),
        "model-responses.json": _json({"response": raw}),
    }
    for name, content in payloads.items():
        (temp / name).write_text(content, encoding="utf-8", newline="\n")
    file_hashes = {
        str(path.relative_to(temp)): ingest.file_sha256(path)
        for path in sorted(temp.rglob("*")) if path.is_file()
    }
    bundle_hash = _sha_text(_json(file_hashes))
    (temp / "manifest.json").write_text(_json({
        "schema_version": 1, "prepared_id": prepared_id, "bundle_hash": bundle_hash,
        "file_hashes": file_hashes, "status": "prepared",
    }), encoding="utf-8", newline="\n")
    os.replace(temp, final)
    report("prepared", 95)
    return final


def verify_bundle(vault: Path, prepared_id: str) -> tuple[Path, dict[str, Any]]:
    bundle = _within(vault, str(PREPARED_REL / prepared_id))
    if not bundle.is_dir():
        raise RuntimeError(f"Prepared Bundle 不存在：{prepared_id}")
    manifest = _read_json(bundle / "manifest.json")
    if manifest.get("prepared_id") != prepared_id:
        raise RuntimeError("Prepared ID 不匹配。")
    hashes = manifest.get("file_hashes")
    if not isinstance(hashes, dict):
        raise RuntimeError("Bundle manifest 缺少 file_hashes。")
    for relative, expected in hashes.items():
        path = _within(bundle, str(relative))
        if not path.is_file() or ingest.file_sha256(path) != expected:
            raise RuntimeError(f"Prepared Bundle 已被篡改：{relative}")
    if _sha_text(_json(hashes)) != manifest.get("bundle_hash"):
        raise RuntimeError("Prepared Bundle hash 无效。")
    return bundle, manifest


def inspect_bundle(vault: Path, prepared_id: str) -> str:
    bundle, _ = verify_bundle(vault.resolve(), prepared_id)
    return (bundle / "preview.md").read_text(encoding="utf-8")


def apply_prepared(vault: Path, prepared_id: str) -> Path:
    vault = vault.resolve()
    with ingest.vault_write_lock(vault):
        return _apply_prepared_locked(vault, prepared_id)


def _apply_prepared_locked(vault: Path, prepared_id: str) -> Path:
    vault = vault.resolve()
    bundle, manifest = verify_bundle(vault, prepared_id)
    rejected = vault / REJECTED_REL / f"{prepared_id}.json"
    if rejected.exists():
        raise RuntimeError("Prepared Bundle 已拒绝，不能 apply。")
    applied = vault / APPLIED_REL / f"{prepared_id}.json"
    if applied.exists():
        record = _read_json(applied)
        if record.get("bundle_hash") != manifest["bundle_hash"]:
            raise RuntimeError("已应用记录与 bundle hash 冲突。")
        return applied

    request = _read_json(bundle / "request.json")
    input_path = Path(str(request.get("pdf_path") or request.get("input_path", "")))
    input_hash = str(request.get("pdf_sha256") or request.get("input_sha256", ""))
    if not input_path.is_file() or ingest.file_sha256(input_path) != input_hash:
        raise RuntimeError("原始输入不存在或 hash 已改变。")
    result = _read_json(bundle / "normalized-result.json")
    if request.get("result_schema", "pdf-v1") == "conversation-v1":
        from prepared_conversation import validate_conversation_result
        validate_conversation_result(result, int(request["section_count"]))
    else:
        ingest.validate_model_result(result, int(request["page_count"]))
    change_set = _read_json(bundle / "change-set.json")
    plan = ingest.WritePlan(source_id=str(change_set["source_id"]), vault=vault)
    for change in change_set.get("writes", []):
        target = _within(vault, str(change["target"]))
        category = str(change["category"])
        expected = change["precondition"]
        current = target_snapshot(target, category)
        if bool(current.get("exists")) != bool(expected.get("exists")):
            raise RuntimeError(f"目标存在性已改变：{target}")
        if current.get("exists"):
            if current.get("status") in ingest.READ_ONLY_STATUSES:
                raise RuntimeError(f"目标已成为只读状态：{target}")
            if current.get("controlled_sha256") != expected.get("controlled_sha256"):
                raise RuntimeError(f"目标受管理内容已改变：{target}")
        artifact_path = _within(bundle, str(change["artifact_file"]))
        proposed = artifact_path.read_text(encoding="utf-8")
        if _sha_text(proposed) != change["content_sha256"]:
            raise RuntimeError(f"Change Set 内容 hash 无效：{target}")
        content = proposed
        if target.exists():
            content = ingest.merge_managed_artifact(
                target.read_text(encoding="utf-8"), proposed, block=_role(category),
                managed_fields=_managed_fields(category),
                human_heading="## 人工批注" if category == "source" else "## 人工审核区",
            )
        plan.writes.append(ingest.PlannedWrite(
            target, content, "update" if target.exists() else "create", category,
        ))
    applied.parent.mkdir(parents=True, exist_ok=True)
    record = _json({
        "prepared_id": prepared_id, "bundle_hash": manifest["bundle_hash"],
        "applied_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    plan.writes.append(ingest.PlannedWrite(applied, record, "create", "apply-record"))
    ingest.execute_plan(plan, transaction_id=f"apply-{prepared_id}", acquire_lock=False)
    return applied


def reject_prepared(vault: Path, prepared_id: str, reason: str) -> Path:
    _, manifest = verify_bundle(vault.resolve(), prepared_id)
    target = vault.resolve() / REJECTED_REL / f"{prepared_id}.json"
    if target.exists():
        return target
    ingest.atomic_write(target, _json({
        "prepared_id": prepared_id, "bundle_hash": manifest["bundle_hash"],
        "reason": reason, "rejected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }))
    return target


def list_prepared(vault: Path) -> list[dict[str, str]]:
    vault = vault.resolve()
    root = vault / PREPARED_REL
    rows: list[dict[str, str]] = []
    if not root.exists():
        return rows
    for bundle in sorted((path for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))):
        try:
            _, manifest = verify_bundle(vault, bundle.name)
            state = "applied" if (vault / APPLIED_REL / f"{bundle.name}.json").exists() else (
                "rejected" if (vault / REJECTED_REL / f"{bundle.name}.json").exists() else "prepared"
            )
            rows.append({"prepared_id": bundle.name, "state": state, "bundle_hash": str(manifest["bundle_hash"])})
        except RuntimeError:
            rows.append({"prepared_id": bundle.name, "state": "invalid", "bundle_hash": ""})
    return rows


def clean_prepared(vault: Path, prepared_id: str) -> None:
    vault = vault.resolve()
    bundle, _ = verify_bundle(vault, prepared_id)
    if not (vault / APPLIED_REL / f"{prepared_id}.json").exists() and not (
        vault / REJECTED_REL / f"{prepared_id}.json"
    ).exists():
        raise RuntimeError("只能清理已 applied 或 rejected 的 Prepared Bundle。")
    shutil.rmtree(bundle)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prepared PDF ingestion")
    sub = root.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--pdf", required=True); prepare.add_argument("--vault", required=True)
    prepare.add_argument("--kind", choices=["paper", "textbook"], required=True)
    prepare.add_argument("--domain-focus", default=""); prepare.add_argument("--model", default=ingest.DEFAULT_MODEL)
    prepare.add_argument("--base-url", default=ingest.DEFAULT_BASE_URL); prepare.add_argument("--max-concepts", type=int, default=3)
    for name in ("inspect", "reject-prepared", "apply-prepared", "clean-prepared"):
        cmd = sub.add_parser(name); cmd.add_argument("--vault", required=True); cmd.add_argument("prepared_id")
        if name == "reject-prepared": cmd.add_argument("--reason", required=True)
    listed = sub.add_parser("list-prepared"); listed.add_argument("--vault", required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    vault = Path(args.vault)
    if args.command == "prepare":
        print(prepare_bundle(args))
    elif args.command == "inspect":
        print(inspect_bundle(vault, args.prepared_id))
    elif args.command == "list-prepared":
        print(_json({"bundles": list_prepared(vault)}), end="")
    elif args.command == "reject-prepared":
        print(reject_prepared(vault, args.prepared_id, args.reason))
    elif args.command == "apply-prepared":
        print(apply_prepared(vault, args.prepared_id))
    elif args.command == "clean-prepared":
        clean_prepared(vault, args.prepared_id)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, ingest.ValidationError) as exc:
        raise SystemExit(f"失败：{exc}") from None

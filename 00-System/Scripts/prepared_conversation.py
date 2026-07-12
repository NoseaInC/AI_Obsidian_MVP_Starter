#!/usr/bin/env python3
"""Prepare high-value AI conversations without directly writing knowledge targets."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import ingest_pdf as ingest
import prepared_pdf as prepared


SYSTEM = """你是严谨的知识整理器。AI 助手回答不是已验证事实。只输出 JSON；所有 assistant 结论必须标记 needs-verification。优先可复用主线知识。"""


def validate_conversation_result(data: Any, section_count: int) -> dict[str, Any]:
    if not isinstance(data, dict): raise ingest.ValidationError("conversation result 必须是对象")
    for key in ("title", "goal", "unresolved"):
        ingest._require_text(data, key, "conversation")
    points = data.get("key_points")
    if not isinstance(points, list) or not points: raise ingest.ValidationError("key_points 必须是非空数组")
    for index, point in enumerate(points):
        if not isinstance(point, dict): raise ingest.ValidationError("key_point 必须是对象")
        ingest._require_text(point, "claim", f"key_points[{index}]")
        speaker = ingest._require_string(point, "speaker", f"key_points[{index}]")
        verification = ingest._require_string(point, "verification", f"key_points[{index}]")
        if speaker not in {"user", "assistant"}: raise ingest.ValidationError("speaker 无效")
        if speaker == "assistant" and verification != "needs-verification": raise ingest.ValidationError("AI 回答必须标记 needs-verification")
        point["evidence_sections"] = ingest._validate_pages(point.get("evidence_sections"), section_count, f"key_points[{index}].evidence_sections")
    topic = data.get("topic")
    if not isinstance(topic, dict): raise ingest.ValidationError("topic 必须是对象")
    ingest._require_string(topic, "title", "topic"); ingest._require_text(topic, "synthesis", "topic")
    topic["evidence_sections"] = ingest._validate_pages(topic.get("evidence_sections"), section_count, "topic.evidence_sections")
    concepts = data.get("concepts")
    if not isinstance(concepts, list) or len(concepts) > 3: raise ingest.ValidationError("concepts 必须为 0–3 项")
    for index, concept in enumerate(concepts):
        if not isinstance(concept, dict): raise ingest.ValidationError("concept 必须是对象")
        ingest._require_string(concept, "title", f"concepts[{index}]")
        for key in ("definition", "conditions", "why_reusable"): ingest._require_text(concept, key, f"concepts[{index}]")
        if type(concept.get("reusable")) is not bool: raise ingest.ValidationError("reusable 必须是布尔值")
        concept["evidence_sections"] = ingest._validate_pages(concept.get("evidence_sections"), section_count, f"concepts[{index}].evidence_sections")
    return data


def _sections(text: str, size: int = 6000) -> list[str]:
    return [f"--- SECTION {index} ---\n{text[start:start + size]}" for index, start in enumerate(range(0, len(text), size), 1)]


def _existing_by_id(folder: Path, artifact_id: str) -> Path | None:
    if not folder.exists(): return None
    matches = [path for path in folder.glob("*.md") if str(ingest.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")).get("artifact_id", "")) == artifact_id]
    if len(matches) > 1: raise RuntimeError(f"重复 artifact_id：{artifact_id}")
    return matches[0] if matches else None


def _sections_text(values: list[int]) -> str: return "、".join(f"对话片段 {value}" for value in values)


def _render_source(result: dict[str, Any], source_id: str, original: Path, raw_archive: Path, draft: str, derived: list[str], platform: str, now: datetime) -> str:
    start, end = ingest.managed_markers("source-index")
    fields = [("type", "source"), ("source_type", "ai-conversation"), ("status", "processed"), ("source_id", source_id), ("generated_from", source_id), ("generated_by", ingest.GENERATED_BY), ("artifact_role", "source-index"), ("artifact_id", f"{source_id}:source-index"), ("original_location", str(original)), ("archive_file", str(raw_archive)), ("platform", platform), ("processed_at", now.isoformat(timespec="seconds")), ("ai_draft", f"[[{draft}]]"), ("topic_drafts", [f"[[{name}]]" for name in derived])]
    return ingest._frontmatter(fields) + f"\n{start}\n# {result['title']}\n\n## 对话目标\n{result['goal']}\n\n## 本地原始归档\n- `{raw_archive}`\n- AI 草稿：[[{draft}]]\n\n## 可信度\nAI 助手回答仅作为待验证来源，不视为正式事实。\n{end}\n\n## 人工批注\n"


def _render_draft(result: dict[str, Any], source_id: str, source_link: str, now: datetime) -> str:
    start, end = ingest.managed_markers("paper-draft")
    points = "\n".join(f"- {item['claim']}（{item['speaker']}；{item['verification']}；{_sections_text(item['evidence_sections'])}）" for item in result["key_points"])
    fields = [("type", "ai-draft"), ("draft_kind", "ai-conversation-distillation"), ("status", "ai-draft"), ("review_state", "pending"), ("generated_from", source_id), ("generated_by", ingest.GENERATED_BY), ("artifact_role", "paper-draft"), ("artifact_id", f"{source_id}:paper-draft"), ("created", now.date().isoformat()), ("source_notes", [f"[[{source_link}]]"]), ("ai_generated", True)]
    return ingest._frontmatter(fields) + f"\n{start}\n# AI 对话提炼：{result['title']}\n\n## 对话目标\n{result['goal']}\n\n## 核心结论与可信度\n{points}\n\n## 尚未解决\n{result['unresolved']}\n\n## 来源回链\n- [[{source_link}]]\n{end}\n\n## 人工审核区\n"


def _render_topic(topic: dict[str, Any], source_id: str, source_link: str, now: datetime) -> str:
    start, end = ingest.managed_markers("topic"); artifact_id = f"{source_id}:topic"
    fields = [("type", "topic"), ("status", "ai-draft"), ("review_state", "pending"), ("generated_from", source_id), ("generated_by", ingest.GENERATED_BY), ("artifact_role", "topic"), ("artifact_id", artifact_id), ("mastery", 0), ("created", now.date().isoformat()), ("source_notes", [f"[[{source_link}]]"])]
    return ingest._frontmatter(fields) + f"\n{start}\n# {topic['title']}\n\n{topic['synthesis']}\n\n## 来源\n- [[{source_link}]]（{_sections_text(topic['evidence_sections'])}；AI 回答待验证）\n{end}\n\n## 人工审核区\n"


def _render_concept(concept: dict[str, Any], source_id: str, source_link: str, index: int, now: datetime) -> str:
    start, end = ingest.managed_markers("concept"); artifact_id = f"{source_id}:concept:{index}"
    fields = [("type", "concept"), ("status", "ai-draft"), ("review_state", "pending"), ("generated_from", source_id), ("generated_by", ingest.GENERATED_BY), ("artifact_role", "concept"), ("artifact_id", artifact_id), ("mastery", 0), ("created", now.date().isoformat()), ("source_notes", [f"[[{source_link}]]"])]
    return ingest._frontmatter(fields) + f"\n{start}\n# {concept['title']}\n\n## 定义\n{concept['definition']}\n\n## 条件与边界\n{concept['conditions']}\n\n## 复用价值\n{concept['why_reusable']}\n\n## 来源\n- [[{source_link}]]（{_sections_text(concept['evidence_sections'])}；AI 回答待验证）\n{end}\n\n## 人工审核区\n"


def _render_suggestion(title: str, target: str, content: str, sections: list[int], source_id: str, source_link: str, artifact_id: str, now: datetime) -> str:
    start, end = ingest.managed_markers("update-suggestion")
    fields = [("type", "update-suggestion"), ("status", "ai-draft"), ("review_state", "pending"), ("generated_from", source_id), ("generated_by", ingest.GENERATED_BY), ("artifact_role", "update-suggestion"), ("artifact_id", artifact_id), ("target_note", f"[[{target}]]"), ("created", now.date().isoformat()), ("source_notes", [f"[[{source_link}]]"])]
    return ingest._frontmatter(fields) + f"\n{start}\n# 更新建议：{title}\n\n## 目标笔记\n- [[{target}]]\n\n## 拟补充内容\n{content}\n\n## 来源\n- [[{source_link}]]（{_sections_text(sections)}；AI 回答待验证）\n{end}\n\n## 人工审核区\n"


def prepare_conversation(args: argparse.Namespace, *, client: ingest.ModelClient | None = None, now: datetime | None = None) -> Path:
    vault, original = Path(args.vault).resolve(), Path(args.input).resolve()
    if not vault.is_dir() or not original.is_file(): raise RuntimeError("Vault 或对话输入不存在")
    text = original.read_text(encoding="utf-8", errors="strict")
    if not text.strip(): raise RuntimeError("对话为空")
    digest, source_id, now = ingest.file_sha256(original), f"conversation-{ingest.file_sha256(original)}", now or datetime.now().astimezone()
    sections = _sections(text)
    if client is None:
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key: raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY")
        client = ingest.DeepSeekClient(key, args.base_url)
    contract = {"title": "...", "goal": "...", "unresolved": "...", "key_points": [{"claim": "...", "speaker": "user|assistant", "verification": "user-provided|needs-verification", "evidence_sections": [1]}], "topic": {"title": "上位主线主题", "synthesis": "...", "evidence_sections": [1]}, "concepts": [{"title": "...", "definition": "...", "conditions": "...", "why_reusable": "...", "reusable": True, "evidence_sections": [1]}]}
    raw = client.create_json(model=args.model, system=SYSTEM, user=f"平台：{args.platform}\n严格契约：{json.dumps(contract, ensure_ascii=False)}\n\n" + "\n".join(sections))
    result = validate_conversation_result(json.loads(raw), len(sections))
    prepared_id = f"{now.strftime('%Y%m%dT%H%M%S%z')}-{digest[:12]}-{uuid.uuid4().hex[:8]}"
    root = vault / prepared.PREPARED_REL; root.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{prepared_id}.", dir=root)); final = root / prepared_id
    raw_archive = final / "raw-conversation.txt"
    safe, short = ingest.sanitize_filename(result["title"]), digest[:12]
    source_folder, draft_folder = vault / "10-Sources/AI-Conversations", vault / "90-Local-Only/AI-Drafts"
    source_path = _existing_by_id(source_folder, f"{source_id}:source-index") or source_folder / f"{safe}-{short}.md"
    draft_path = _existing_by_id(draft_folder, f"{source_id}:paper-draft") or draft_folder / f"{safe}-AI草稿-{short}.md"
    if source_path.exists() and not _existing_by_id(source_folder, f"{source_id}:source-index"):
        source_path = source_folder / f"{safe}-{short}-structured.md"
    if draft_path.exists() and not _existing_by_id(draft_folder, f"{source_id}:paper-draft"):
        draft_path = draft_folder / f"{safe}-AI草稿-{short}-structured.md"
    topic = result["topic"]; topic_folder = vault / "20-Knowledge/Topics"
    topic_path = _existing_by_id(topic_folder, f"{source_id}:topic") or topic_folder / f"{ingest.sanitize_filename(topic['title'])}.md"
    proposed: list[tuple[Path, str, str]] = []
    topic_content = _render_topic(topic, source_id, source_path.stem, now)
    topic_policy = ingest._existing_policy(topic_path, source_id)
    if topic_policy not in {"protected", "conflict", "rejected"}:
        proposed.append((topic_path, topic_content, "topic"))
    elif topic_policy == "protected":
        suggestion = draft_folder / "Update-Suggestions" / f"{ingest.sanitize_filename(topic_path.stem)}-更新建议-{short}.md"
        proposed.append((suggestion, _render_suggestion(topic["title"], topic_path.stem, topic["synthesis"], topic["evidence_sections"], source_id, source_path.stem, f"{source_id}:update-suggestion:topic", now), "suggestion"))
    concept_paths: list[Path] = []
    for index, concept in enumerate(item for item in result["concepts"] if item["reusable"]):
        folder = vault / "20-Knowledge/Concepts"; path = _existing_by_id(folder, f"{source_id}:concept:{index}") or folder / f"{ingest.sanitize_filename(concept['title'])}.md"
        concept_policy = ingest._existing_policy(path, source_id)
        if concept_policy == "protected":
            suggestion = draft_folder / "Update-Suggestions" / f"{ingest.sanitize_filename(path.stem)}-更新建议-{short}.md"
            proposed.append((suggestion, _render_suggestion(concept["title"], path.stem, concept["definition"], concept["evidence_sections"], source_id, source_path.stem, f"{source_id}:update-suggestion:concept:{index}", now), "suggestion")); concept_paths.append(path); continue
        if concept_policy in {"conflict", "rejected"}: continue
        proposed.append((path, _render_concept(concept, source_id, source_path.stem, index, now), "concept")); concept_paths.append(path)
    source_content = _render_source(result, source_id, original, raw_archive, draft_path.stem, [topic_path.stem, *[path.stem for path in concept_paths]], args.platform, now)
    draft_content = _render_draft(result, source_id, source_path.stem, now)
    prefix: list[tuple[Path, str, str]] = [(source_path, source_content, "source")]
    draft_policy = ingest._existing_policy(draft_path, source_id)
    if draft_policy not in {"protected", "rejected"}:
        prefix.append((draft_path, draft_content, "paper-draft"))
    elif draft_policy == "protected":
        suggestion = draft_folder / "Update-Suggestions" / f"{safe}-整理更新建议-{short}.md"
        prefix.append((suggestion, _render_suggestion(result["title"], draft_path.stem, result["goal"], [1], source_id, source_path.stem, f"{source_id}:update-suggestion:paper-draft", now), "suggestion"))
    proposed[:0] = prefix
    changes = []; artifacts = temp / "artifacts"; artifacts.mkdir()
    for index, (target, content, category) in enumerate(proposed):
        policy = ingest._existing_policy(target, source_id)
        if target.exists() and policy == "update": content = ingest.merge_source_index(target.read_text(encoding="utf-8"), content) if category == "source" else ingest.merge_managed_artifact(target.read_text(encoding="utf-8"), content, block=prepared._role(category), managed_fields=prepared._managed_fields(category))
        artifact_file = f"artifacts/{index:04d}.md"; (temp / artifact_file).write_text(content, encoding="utf-8")
        changes.append({"target": str(target.relative_to(vault)), "action": "update" if target.exists() else "create", "category": category, "artifact_file": artifact_file, "content_sha256": prepared._sha_text(content), "precondition": prepared.target_snapshot(target, category)})
    request = {"prepared_id": prepared_id, "created_at": now.isoformat(timespec="seconds"), "input_kind": "ai-conversation", "input_path": str(original), "input_sha256": digest, "section_count": len(sections), "result_schema": "conversation-v1", "model": args.model, "source_id": source_id}
    payloads = {"request.json": prepared._json(request), "normalized-result.json": prepared._json(result), "evidence.json": prepared._json({"key_points": result["key_points"]}), "vault-inventory.json": prepared._json(ingest.inventory_for_prompt(ingest.scan_vault(vault))), "change-set.json": prepared._json({"source_id": source_id, "writes": changes, "skipped": [], "warnings": ["AI assistant claims require verification"]}), "preview.md": f"# Prepared Conversation {prepared_id}\n\n" + "\n".join(f"- {item['action']} {item['category']} `{item['target']}`" for item in changes) + "\n", "raw-conversation.txt": text, "model-responses.json": prepared._json({"response": raw})}
    for name, content in payloads.items(): (temp / name).write_text(content, encoding="utf-8")
    hashes = {str(path.relative_to(temp)): ingest.file_sha256(path) for path in sorted(temp.rglob("*")) if path.is_file()}
    (temp / "manifest.json").write_text(prepared._json({"schema_version": 1, "prepared_id": prepared_id, "bundle_hash": prepared._sha_text(prepared._json(hashes)), "file_hashes": hashes, "status": "prepared"}), encoding="utf-8")
    os.replace(temp, final); return final


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", required=True); parser.add_argument("--vault", required=True); parser.add_argument("--platform", default="ChatGPT"); parser.add_argument("--model", default=ingest.DEFAULT_MODEL); parser.add_argument("--base-url", default=ingest.DEFAULT_BASE_URL); return parser


if __name__ == "__main__":
    try: print(prepare_conversation(parser().parse_args()))
    except (RuntimeError, ValueError, json.JSONDecodeError, ingest.ValidationError) as exc: raise SystemExit(f"失败：{exc}") from None

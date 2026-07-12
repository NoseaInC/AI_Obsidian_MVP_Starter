#!/usr/bin/env python3
"""Local-first, review-first PDF ingestion for the Obsidian MVP Vault.

The PDF is parsed locally. Only extracted text containing explicit PAGE markers
is sent to the configured OpenAI-compatible API. Model output is JSON; Markdown
is rendered and written locally only after strict validation succeeds.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
READ_ONLY_STATUSES = {"reviewed", "core"}
GENERATED_BY = "ingest_pdf"
MANAGED_START = "<!-- ingest-pdf:managed:start -->"
MANAGED_END = "<!-- ingest-pdf:managed:end -->"
INVENTORY_FOLDERS = (
    "20-Knowledge/Concepts",
    "20-Knowledge/Topics",
    "20-Knowledge/MOCs",
)


class ValidationError(ValueError):
    """The model result does not match the local contract."""


@dataclass(frozen=True)
class InventoryItem:
    path: Path
    title: str
    note_type: str = ""
    status: str = ""
    domain: str = ""
    aliases: tuple[str, ...] = ()
    source_notes: tuple[str, ...] = ()
    generated_from: str = ""
    generated_by: str = ""
    artifact_role: str = ""
    artifact_id: str = ""


@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    content: str
    action: str
    category: str


@dataclass
class WritePlan:
    source_id: str
    vault: Path | None = None
    writes: list[PlannedWrite] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ModelClient(Protocol):
    def create_json(self, *, model: str, system: str, user: str) -> str: ...


class DeepSeekClient:
    """Small adapter that never logs or persists the API key."""

    def __init__(self, api_key: str, base_url: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def create_json(self, *, model: str, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            stream=False,
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("模型返回了空内容。")
        return content.strip()


def sanitize_filename(value: str, max_len: int = 120) -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "未命名资料")[:max_len].rstrip(" .")


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: Iterable[str], indent: int = 2) -> str:
    values = list(values)
    if not values:
        return "[]"
    pad = " " * indent
    return "\n" + "\n".join(f"{pad}- {yaml_quote(v)}" for v in values)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pages(pdf_path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError("PDF 已加密，无法用空密码读取。") from exc
    pages: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            raise RuntimeError(f"第 {number} 页文字提取失败。") from exc
        pages.append(f"--- PAGE {number} ---\n{text}\n")
    if not any(re.sub(r"--- PAGE \d+ ---", "", page).strip() for page in pages):
        raise RuntimeError("没有提取到文字；这可能是扫描 PDF，需要先做 OCR。")
    return pages


def chunk_pages(pages: Iterable[str], max_chars: int = 80_000) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars 必须大于 0。")
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for page in pages:
        if current and size + len(page) > max_chars:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(page)
        size += len(page)
    if current:
        chunks.append("\n".join(current))
    return chunks


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        try:
            return json.loads(value) if value.startswith('"') else value[1:-1]
        except json.JSONDecodeError:
            return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else value
        except json.JSONDecodeError:
            return [x.strip().strip('"\'') for x in value[1:-1].split(",") if x.strip()]
    return value


def parse_frontmatter(text: str) -> dict[str, Any]:
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    result: dict[str, Any] = {}
    current_key: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", line)
        if match:
            current_key = match.group(1)
            result[current_key] = _scalar(match.group(2) or "")
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and current_key:
            if not isinstance(result[current_key], list):
                result[current_key] = []
            result[current_key].append(_scalar(item.group(1)))
    return result


def scan_vault(vault: Path) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    for relative in INVENTORY_FOLDERS:
        folder = vault / relative
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            aliases = meta.get("aliases", [])
            sources = meta.get("source_notes", [])
            if not isinstance(aliases, list):
                aliases = [str(aliases)] if aliases else []
            if not isinstance(sources, list):
                sources = [str(sources)] if sources else []
            items.append(
                InventoryItem(
                    path=path,
                    title=path.stem,
                    note_type=str(meta.get("type", "")),
                    status=str(meta.get("status", "")),
                    domain=str(meta.get("domain", "")),
                    aliases=tuple(map(str, aliases)),
                    source_notes=tuple(map(str, sources)),
                    generated_from=str(meta.get("generated_from", "")),
                    generated_by=str(meta.get("generated_by", "")),
                    artifact_role=str(meta.get("artifact_role", "")),
                    artifact_id=str(meta.get("artifact_id", "")),
                )
            )
    return items


def find_generated_artifacts(vault: Path, source_id: str, role: str) -> list[Path]:
    """Find generated Markdown by identity, including pre-metadata legacy output."""
    roots = {
        "source-index": ("10-Sources/Papers", "10-Sources/Textbooks"),
        "paper-draft": ("90-Local-Only/AI-Drafts",),
        "topic": ("20-Knowledge/Topics",),
        "concept": ("20-Knowledge/Concepts",),
        "update-suggestion": ("90-Local-Only/AI-Drafts/Update-Suggestions",),
    }.get(role, ())
    matches: list[Path] = []
    for relative in roots:
        folder = vault / relative
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = parse_frontmatter(text)
            generated_from = str(meta.get("generated_from", ""))
            full_digest = source_id.removeprefix("pdf-")
            short = full_digest[:12]
            legacy_source = (
                role == "source-index"
                and str(meta.get("type", "")) == "source"
                and str(meta.get("source_id", "")) == f"pdf-{short}"
                and (full_digest in text or str(meta.get("sha256", "")) == full_digest)
            )
            legacy_draft = (
                role == "paper-draft"
                and str(meta.get("type", "")) == "ai-draft"
                and path.stem.endswith(f"AI草稿-{short}")
            )
            if generated_from != source_id and not legacy_source and not legacy_draft:
                continue
            actual_role = str(meta.get("artifact_role", ""))
            legacy_role = ""
            if not actual_role:
                if role == "source-index" and str(meta.get("type", "")) == "source":
                    legacy_role = role
                elif role == "paper-draft" and str(meta.get("type", "")) == "ai-draft":
                    legacy_role = role
                elif role in {"topic", "concept"} and str(meta.get("type", "")) == role:
                    legacy_role = role
                elif role == "update-suggestion" and str(meta.get("type", "")) == role:
                    legacy_role = role
            if actual_role == role or legacy_role == role:
                matches.append(path)
    return matches


def _unique_generated_artifact(vault: Path, source_id: str, role: str) -> Path | None:
    matches = find_generated_artifacts(vault, source_id, role)
    if len(matches) > 1 and role in {"source-index", "paper-draft", "topic"}:
        names = ", ".join(str(path) for path in matches)
        raise RuntimeError(f"同一来源存在多个 {role} 生成物，拒绝猜测：{names}")
    return matches[0] if matches else None


def inventory_for_prompt(items: list[InventoryItem]) -> list[dict[str, Any]]:
    return [
        {
            "title": item.title,
            "type": item.note_type,
            "status": item.status,
            "domain": item.domain,
            "aliases": list(item.aliases),
            "source_notes": list(item.source_notes),
            "generated_from": item.generated_from,
        }
        for item in items
    ]


def _require_string(obj: dict[str, Any], key: str, context: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context}.{key} 必须是非空字符串。")
    return value.strip()


def _require_text(obj: dict[str, Any], key: str, context: str) -> str:
    """Accept narrative text as a string or a non-empty string list, then normalize."""
    value = obj.get(key)
    if isinstance(value, str) and value.strip():
        obj[key] = value.strip()
        return obj[key]
    if (
        isinstance(value, list) and value
        and all(isinstance(item, str) and item.strip() for item in value)
    ):
        obj[key] = "\n".join(f"- {item.strip()}" for item in value)
        return obj[key]
    raise ValidationError(f"{context}.{key} 必须是非空字符串或非空字符串数组。")


def _require_string_list(obj: dict[str, Any], key: str, context: str) -> list[str]:
    value = obj.get(key)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise ValidationError(f"{context}.{key} 必须是字符串数组。")
    return [x.strip() for x in value if x.strip()]


def _validate_pages(pages: Any, page_count: int, context: str, allow_empty: bool = False) -> list[int]:
    if not isinstance(pages, list) or any(type(p) is not int for p in pages):
        raise ValidationError(f"{context} 必须是页码整数数组。")
    if not pages and not allow_empty:
        raise ValidationError(f"{context} 不得为空。")
    if any(page < 1 or page > page_count for page in pages):
        raise ValidationError(f"{context} 包含超出 PDF 1–{page_count} 页范围的页码。")
    return sorted(set(pages))


def validate_model_result(data: Any, page_count: int) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValidationError("模型结果必须是 JSON 对象。")
    _require_string(data, "title", "result")
    required_text = (
        "one_sentence_summary", "detailed_summary", "research_question",
        "methods_and_assumptions", "contributions", "main_results", "limitations",
        "concept_relations", "existing_knowledge_links", "followup_questions",
    )
    for key in required_text:
        _require_text(data, key, "result")

    evidence = data.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValidationError("result.evidence 必须是非空数组。")
    for i, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValidationError(f"evidence[{i}] 必须是对象。")
        _require_string(item, "claim", f"evidence[{i}]")
        _require_string(item, "excerpt", f"evidence[{i}]")
        kind = _require_string(item, "kind", f"evidence[{i}]")
        if kind not in {"paper", "author", "inference", "uncertain"}:
            raise ValidationError(f"evidence[{i}].kind 无效。")
        item["pages"] = _validate_pages(item.get("pages"), page_count, f"evidence[{i}].pages")

    inferences = data.get("inferences")
    if not isinstance(inferences, list):
        raise ValidationError("result.inferences 必须是数组。")
    for i, item in enumerate(inferences):
        if not isinstance(item, dict):
            raise ValidationError(f"inferences[{i}] 必须是对象。")
        _require_string(item, "statement", f"inferences[{i}]")
        item["evidence_pages"] = _validate_pages(
            item.get("evidence_pages"), page_count, f"inferences[{i}].evidence_pages", allow_empty=True
        )

    topic = data.get("topic")
    if not isinstance(topic, dict):
        raise ValidationError("result.topic 必须是对象。")
    _require_string(topic, "title", "topic")
    for key in (
        "problem", "problem_setting", "prerequisites", "assumptions",
        "method_routes", "paper_position", "concept_relations", "confusions", "next_steps",
    ):
        _require_text(topic, key, "topic")
    topic["evidence_pages"] = _validate_pages(
        topic.get("evidence_pages"), page_count, "topic.evidence_pages"
    )

    concepts = data.get("concepts")
    if not isinstance(concepts, list):
        raise ValidationError("result.concepts 必须是数组。")
    for i, concept in enumerate(concepts):
        context = f"concepts[{i}]"
        if not isinstance(concept, dict):
            raise ValidationError(f"{context} 必须是对象。")
        _require_string(concept, "title", context)
        for key in (
            "definition", "conditions", "intuition", "formula", "confusions",
            "example", "common_errors", "selection_reason",
        ):
            _require_text(concept, key, context)
        questions = _require_string_list(concept, "review_questions", context)
        if len(questions) != 3:
            raise ValidationError(f"{context}.review_questions 必须恰好有 3 题。")
        for score in ("mainline_score", "reuse_score", "prerequisite_score", "scope_score"):
            value = concept.get(score)
            if type(value) is not int or value < 0 or value > 5:
                raise ValidationError(f"{context}.{score} 必须是 0–5 的整数。")
        if type(concept.get("paper_specific")) is not bool:
            raise ValidationError(f"{context}.paper_specific 必须是布尔值。")
        concept["evidence_pages"] = _validate_pages(
            concept.get("evidence_pages"), page_count, f"{context}.evidence_pages"
        )

    suggestions = data.get("update_suggestions", [])
    if not isinstance(suggestions, list):
        raise ValidationError("result.update_suggestions 必须是数组。")
    for i, suggestion in enumerate(suggestions):
        context = f"update_suggestions[{i}]"
        if not isinstance(suggestion, dict):
            raise ValidationError(f"{context} 必须是对象。")
        for key in ("target_title", "proposed_content", "relation", "evidence_strength", "action"):
            _require_string(suggestion, key, context)
        suggestion["evidence_pages"] = _validate_pages(
            suggestion.get("evidence_pages"), page_count, f"{context}.evidence_pages"
        )
    return data


def concept_score(concept: dict[str, Any]) -> int:
    score = (
        3 * int(concept["mainline_score"])
        + 2 * int(concept["reuse_score"])
        + 2 * int(concept["prerequisite_score"])
        + int(concept["scope_score"])
    )
    if concept["paper_specific"]:
        score -= 12
    return score


def rank_concepts(concepts: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if maximum < 0:
        raise ValueError("max_concepts 不得小于 0。")
    eligible = [
        item for item in concepts
        if concept_score(item) >= 20 and not item["paper_specific"]
    ]
    return sorted(
        eligible,
        key=lambda item: (-concept_score(item), item["paper_specific"], item["title"].casefold()),
    )[:maximum]


def parse_and_validate_json(raw: str, page_count: int) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"模型结果不是有效 JSON：{exc.msg}") from exc
    return validate_model_result(data, page_count)


EVIDENCE_SYSTEM = """你是严谨的学术证据提取器。只根据给定文本工作。页码只能来自 --- PAGE N --- 标记。只输出 JSON 对象，不输出 Markdown。"""
EVIDENCE_PROMPT = """从这个 PDF 文本分块提取证据。返回 {{"evidence":[{{"claim":"...","pages":[1],"excerpt":"简短证据摘要","kind":"paper|author|inference|uncertain"}}]}}。不要生成分块中不存在的页码。\n\n{chunk}"""
SYNTHESIS_SYSTEM = """你是严谨的学术知识架构师。只能基于带页码的证据综合，优先学习主线主题和可跨资料复用的概念。只输出符合契约的 JSON 对象。"""


def result_contract(domain_focus: str, kind: str, inventory: list[dict[str, Any]]) -> str:
    schema = {
        "title": "资料标题", "one_sentence_summary": "一句话摘要", "detailed_summary": "详细摘要",
        "research_question": "研究问题", "methods_and_assumptions": "方法与关键假设",
        "contributions": "核心贡献", "main_results": "主要结果", "limitations": "局限性",
        "concept_relations": "核心概念关系", "existing_knowledge_links": "与已有知识关联",
        "followup_questions": "后续研究问题",
        "evidence": [{"claim": "结论", "pages": [1], "excerpt": "证据摘要", "kind": "paper"}],
        "inferences": [{"statement": "AI 推断", "evidence_pages": [1]}],
        "topic": {
            "title": "一篇主线主题", "problem": "...", "problem_setting": "...", "prerequisites": "...",
            "assumptions": "...", "method_routes": "...", "paper_position": "...",
            "concept_relations": "...", "confusions": "...", "next_steps": "...", "evidence_pages": [1],
        },
        "concepts": [{
            "title": "可复用概念", "definition": "...", "conditions": "...", "intuition": "...",
            "formula": "...", "confusions": "...", "example": "...", "common_errors": "...",
            "review_questions": ["题1", "题2", "题3"], "evidence_pages": [1],
            "mainline_score": 0, "reuse_score": 0, "prerequisite_score": 0, "scope_score": 0,
            "paper_specific": False, "selection_reason": "...",
        }],
        "update_suggestions": [{
            "target_title": "已有笔记", "proposed_content": "...", "evidence_pages": [1],
            "relation": "补充|修正|冲突|示例", "evidence_strength": "强|中|弱", "action": "...",
        }],
    }
    return (
        f"资料类型：{kind}\n领域主线：{domain_focus or '未指定'}\n"
        "主题必须优先学习主线，而不是论文专属模块。概念候选可多给，但要诚实评分；"
        "如果 inventory 已有能容纳本文的更宽主线主题，topic.title 必须复用其准确标题，"
        "论文方法只写入 paper_position，并通过 update_suggestions 建议补充，不能另造较窄的重复主题。"
        "paper_specific 对论文特有组件必须为 true。每项重要结论尽量给 evidence_pages。\n"
        f"Vault inventory：{json.dumps(inventory, ensure_ascii=False)}\n"
        f"严格输出以下键和类型：{json.dumps(schema, ensure_ascii=False)}"
    )


def call_model_with_repair(
    client: ModelClient,
    *,
    model: str,
    prompt: str,
    page_count: int,
) -> tuple[dict[str, Any], list[str]]:
    raws: list[str] = []
    raw = client.create_json(model=model, system=SYNTHESIS_SYSTEM, user=prompt)
    raws.append(raw)
    try:
        return parse_and_validate_json(raw, page_count), raws
    except ValidationError as first_error:
        repair = (
            "上一次输出未通过本地校验。修复为完整 JSON；不要解释，不要新增页码。\n"
            f"校验错误：{first_error}\n原输出：\n{raw}\n\n原契约：\n{prompt}"
        )
        repaired = client.create_json(model=model, system=SYNTHESIS_SYSTEM, user=repair)
        raws.append(repaired)
        return parse_and_validate_json(repaired, page_count), raws


def analyze_document(
    client: ModelClient,
    *,
    model: str,
    chunks: list[str],
    page_count: int,
    domain_focus: str,
    kind: str,
    inventory: list[InventoryItem],
) -> tuple[dict[str, Any], dict[str, Any]]:
    chunk_evidence: list[Any] = []
    raw_chunks: list[str] = []
    for chunk in chunks:
        raw = client.create_json(
            model=model, system=EVIDENCE_SYSTEM, user=EVIDENCE_PROMPT.format(chunk=chunk)
        )
        raw_chunks.append(raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError("分块证据不是有效 JSON。") from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("evidence"), list):
            raise ValidationError("分块证据缺少 evidence 数组。")
        for i, evidence in enumerate(parsed["evidence"]):
            if not isinstance(evidence, dict):
                raise ValidationError("分块 evidence 必须是对象。")
            _validate_pages(evidence.get("pages"), page_count, f"chunk.evidence[{i}].pages")
        chunk_evidence.extend(parsed["evidence"])
    prompt = result_contract(domain_focus, kind, inventory_for_prompt(inventory))
    prompt += "\n\n带页码分块证据：\n" + json.dumps(chunk_evidence, ensure_ascii=False)
    result, raw_results = call_model_with_repair(
        client, model=model, prompt=prompt, page_count=page_count
    )
    return result, {"chunk_responses": raw_chunks, "result_responses": raw_results}


def _pages(pages: list[int]) -> str:
    return "、".join(f"第 {p} 页" for p in pages)


def _frontmatter(fields: list[tuple[str, Any]]) -> str:
    lines = ["---"]
    for key, value in fields:
        if isinstance(value, list):
            lines.append(f"{key}:" + yaml_list([str(v) for v in value]))
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {yaml_quote(str(value))}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def render_source(
    result: dict[str, Any], *, source_id: str, digest: str, pdf_path: Path,
    kind: str, model: str, now: str, draft_link: str, topic_links: list[str], concept_links: list[str]
) -> str:
    fields = [
        ("type", "source"), ("source_type", kind), ("status", "processed"),
        ("source_id", source_id), ("generated_from", source_id),
        ("generated_by", GENERATED_BY), ("artifact_role", "source-index"),
        ("artifact_id", f"{source_id}:source-index"),
        ("original_location", pdf_path.resolve().as_uri()), ("local_file", str(pdf_path.resolve())),
        ("sha256", digest), ("processed_by", model), ("processed_at", now),
        ("ai_draft", f"[[{draft_link}]]"),
        ("topic_drafts", [f"[[{x}]]" for x in topic_links]),
        ("concept_drafts", [f"[[{x}]]" for x in concept_links]),
        ("tags", [f"source/{kind}"]),
    ]
    links = "\n".join(f"- [[{x}]]" for x in topic_links + concept_links) or "- 无"
    return _frontmatter(fields) + f"""
{MANAGED_START}
# {result['title']}

## 原始资料

- [打开本地 PDF]({pdf_path.resolve().as_uri()})
- SHA-256：`{digest}`
- 论文整理草稿：[[{draft_link}]]

## 派生知识草稿

{links}

## 隐私说明

PDF 文件本身未上传；AI 只处理了脚本在本地逐页提取并标记页码的文本。

{MANAGED_END}

## 人工批注
"""


SOURCE_MANAGED_FIELDS = {
    "type", "source_type", "status", "source_id", "generated_from", "generated_by",
    "artifact_role", "artifact_id", "original_location", "local_file", "sha256",
    "processed_by", "processed_at", "ai_draft", "topic_drafts", "concept_drafts",
    "related_concepts", "tags",
}


def _split_frontmatter_text(text: str) -> tuple[list[str], str]:
    lines = text.lstrip("\ufeff").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return [], text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[1:index], "".join(lines[index + 1:])
    return [], text


def _frontmatter_entries(lines: list[str]) -> list[tuple[str, list[str]]]:
    entries: list[tuple[str, list[str]]] = []
    current_key = ""
    current: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Za-z0-9_-]+):", line)
        if match:
            if current:
                entries.append((current_key, current))
            current_key, current = match.group(1), [line]
        else:
            current.append(line)
    if current:
        entries.append((current_key, current))
    return entries


def merge_source_index(existing: str, generated: str) -> str:
    """Refresh only source-owned YAML and managed Markdown; retain human text."""
    old_fm, old_body = _split_frontmatter_text(existing)
    new_fm, new_body = _split_frontmatter_text(generated)
    preserved = [
        lines for key, lines in _frontmatter_entries(old_fm)
        if key and key not in SOURCE_MANAGED_FIELDS
    ]
    merged_fm = [line for _, lines in _frontmatter_entries(new_fm) for line in lines]
    merged_fm.extend(line for lines in preserved for line in lines)

    start = old_body.find(MANAGED_START)
    end = old_body.find(MANAGED_END)
    new_start = new_body.index(MANAGED_START)
    new_end = new_body.index(MANAGED_END) + len(MANAGED_END)
    managed = new_body[new_start:new_end]
    if start >= 0 and end >= start:
        body = old_body[:start] + managed + old_body[end + len(MANAGED_END):]
    else:
        human = ""
        marker = "## 人工批注"
        if marker in old_body:
            human = old_body.split(marker, 1)[1].lstrip("\r\n")
        body = managed + "\n\n## 人工批注\n" + human
    return "---\n" + "".join(merged_fm) + "---\n" + body


def render_paper_draft(
    result: dict[str, Any], *, source_id: str, source_link: str, model: str,
    today: str, kind: str = "paper"
) -> str:
    evidence_rows = "\n".join(
        f"| {e['claim'].replace('|', '｜')} | {_pages(e['pages'])} | {e['excerpt'].replace('|', '｜')} | {e['kind']} |"
        for e in result["evidence"]
    )
    inferences = "\n".join(
        f"- **AI 推断**：{item['statement']}（证据页：{_pages(item['evidence_pages']) or '无，需验证'}）"
        for item in result["inferences"]
    ) or "- 无；不得把本节内容视为论文原结论。"
    fields = [
        ("type", "ai-draft"),
        ("draft_kind", "paper-summary" if kind == "paper" else "textbook-study"),
        ("status", "ai-draft"),
        ("generated_from", source_id), ("created", today), ("updated", today),
        ("generated_by", GENERATED_BY), ("artifact_role", "paper-draft"),
        ("artifact_id", f"{source_id}:paper-draft"),
        ("processed_by", model), ("source_notes", [f"[[{source_link}]]"]),
        ("ai_generated", True), ("reviewed", False), ("tags", ["ai/draft"]),
    ]
    return _frontmatter(fields) + f"""
# 论文整理：{result['title']}

## 一句话摘要

{result['one_sentence_summary']}

## 详细摘要

{result['detailed_summary']}

## 研究问题

{result['research_question']}

## 方法与关键假设

{result['methods_and_assumptions']}

## 核心贡献

{result['contributions']}

## 主要结果

{result['main_results']}

## 局限性

{result['limitations']}

## 核心概念关系

{result['concept_relations']}

## 与已有知识的关联

{result['existing_knowledge_links']}

## 后续研究问题

{result['followup_questions']}

## 带页码的证据表

| 结论/观点 | 页码 | 依据摘要 | 类型 |
|---|---:|---|---|
{evidence_rows}

## AI 推断（不是论文原结论）

{inferences}

## 来源回链

- [[{source_link}]]

## 审核选项

- [ ] 接受并拆分/合并为知识笔记
- [ ] 修改后接受
- [ ] 保留为来源摘要
- [ ] 拒绝

## 人工审核区
"""


def render_topic(topic: dict[str, Any], *, source_id: str, source_link: str, domain: str, today: str) -> str:
    fields = [
        ("type", "topic"), ("status", "ai-draft"), ("domain", domain), ("mastery", 0),
        ("generated_from", source_id), ("created", today), ("updated", today),
        ("generated_by", GENERATED_BY), ("artifact_role", "topic"),
        ("artifact_id", f"{source_id}:topic"),
        ("source_notes", [f"[[{source_link}]]"]), ("ai_generated", True),
        ("reviewed", False), ("tags", ["knowledge/topic", "ai/draft"]),
    ]
    return _frontmatter(fields) + f"""
# {topic['title']}

## 主题要解决的问题
{topic['problem']}

## 基本问题设定
{topic['problem_setting']}

## 前置知识
{topic['prerequisites']}

## 核心假设
{topic['assumptions']}

## 主要方法路线
{topic['method_routes']}

## 当前论文在路线中的位置
{topic['paper_position']}

## 概念关系
{topic['concept_relations']}

## 易混淆点
{topic['confusions']}

## 下一步学习路线
{topic['next_steps']}

## 来源回链
- [[{source_link}]]（{_pages(topic['evidence_pages'])}）

## 人工审核区
"""


def render_concept(
    concept: dict[str, Any], *, source_id: str, source_link: str, domain: str,
    today: str, artifact_id: str
) -> str:
    fields = [
        ("type", "concept"), ("status", "ai-draft"), ("domain", domain), ("mastery", 0),
        ("generated_from", source_id), ("created", today), ("updated", today),
        ("generated_by", GENERATED_BY), ("artifact_role", "concept"),
        ("artifact_id", artifact_id),
        ("source_notes", [f"[[{source_link}]]"]), ("ai_generated", True),
        ("reviewed", False), ("tags", ["knowledge/concept", "ai/draft"]),
    ]
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(concept["review_questions"], 1))
    return _frontmatter(fields) + f"""
# {concept['title']}

## 严谨定义
{concept['definition']}

## 适用条件与假设
{concept['conditions']}

## 直觉
{concept['intuition']}

## 公式或关键推导
{concept['formula']}

## 易混淆概念
{concept['confusions']}

## 具体例子
{concept['example']}

## 常见错误
{concept['common_errors']}

## 复习题
{questions}

## 来源与页码
- [[{source_link}]]（{_pages(concept['evidence_pages'])}）

## 候选评分
- 主线：{concept['mainline_score']}/5；复用：{concept['reuse_score']}/5；前置：{concept['prerequisite_score']}/5；范围：{concept['scope_score']}/5
- 论文专属：{'是' if concept['paper_specific'] else '否'}
- 选择理由：{concept['selection_reason']}

## 人工审核区
"""


def render_suggestion(
    *, title: str, target: str, content: str, pages: list[int], relation: str,
    strength: str, action: str, source_id: str, source_link: str, today: str,
    artifact_id: str
) -> str:
    fields = [
        ("type", "update-suggestion"), ("draft_kind", "knowledge-update"),
        ("status", "ai-draft"), ("generated_from", source_id), ("target_note", f"[[{target}]]"),
        ("generated_by", GENERATED_BY), ("artifact_role", "update-suggestion"),
        ("artifact_id", artifact_id),
        ("created", today), ("source_notes", [f"[[{source_link}]]"]),
        ("ai_generated", True), ("reviewed", False), ("tags", ["ai/draft", "ai/update-suggestion"]),
    ]
    return _frontmatter(fields) + f"""
# 更新建议：{title}

## 目标笔记
- [[{target}]]

## 新来源
- [[{source_link}]]

## 拟新增内容
{content}

## 来源页码
{_pages(pages)}

## 建议类型
{relation}

## 证据强度
{strength}

## 建议操作
{action}

## 人工审核区
- [ ] 接受
- [ ] 修改后接受
- [ ] 拒绝
"""


def _normalized_title(value: str) -> str:
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def find_similar(title: str, inventory: list[InventoryItem], note_type: str) -> InventoryItem | None:
    target = _normalized_title(title)
    best: tuple[float, InventoryItem] | None = None
    for item in inventory:
        if item.note_type != note_type:
            continue
        names = [item.title, *item.aliases]
        score = max((difflib.SequenceMatcher(None, target, _normalized_title(name)).ratio() for name in names), default=0)
        if score >= 0.88 and (best is None or score > best[0]):
            best = (score, item)
    return best[1] if best else None


def _existing_policy(path: Path, source_id: str) -> str:
    if not path.exists():
        return "create"
    meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    if str(meta.get("status", "")) in READ_ONLY_STATUSES:
        return "protected"
    if str(meta.get("status", "")) == "ai-draft" and str(meta.get("generated_from", "")) == source_id:
        return "update"
    if (
        str(meta.get("status", "")) == "ai-draft"
        and path.stem.endswith(source_id.removeprefix("pdf-")[:12])
    ):
        return "update"
    return "conflict"


def build_plan(
    vault: Path, *, pdf_path: Path, digest: str, result: dict[str, Any], model: str,
    kind: str, domain_focus: str, max_concepts: int, inventory: list[InventoryItem],
    now: datetime | None = None
) -> WritePlan:
    now = now or datetime.now().astimezone()
    today, timestamp = now.date().isoformat(), now.isoformat(timespec="seconds")
    source_id = f"pdf-{digest}"
    short = digest[:12]
    safe_title = sanitize_filename(result["title"])
    source_folder = vault / "10-Sources" / ("Papers" if kind == "paper" else "Textbooks")
    draft_folder = vault / "90-Local-Only" / "AI-Drafts"
    suggestion_folder = draft_folder / "Update-Suggestions"
    source_path = _unique_generated_artifact(vault, source_id, "source-index") or (
        source_folder / f"{safe_title}-{short}.md"
    )
    draft_path = _unique_generated_artifact(vault, source_id, "paper-draft") or (
        draft_folder / f"{safe_title}-AI草稿-{short}.md"
    )
    plan = WritePlan(source_id=source_id, vault=vault)

    # Deterministic migration path for files created by the legacy Markdown-only
    # processor. They lack generated_from and therefore may never be overwritten.
    for label, candidate in (("来源索引", source_path), ("整理草稿", draft_path)):
        if candidate.exists():
            meta = parse_frontmatter(candidate.read_text(encoding="utf-8", errors="replace"))
            legacy_short = source_id.removeprefix("pdf-")[:12]
            same_source_draft = (
                str(meta.get("status", "")) == "ai-draft"
                and (
                    str(meta.get("generated_from", "")) == source_id
                    or candidate.stem.endswith(legacy_short)
                )
            )
            same_source_index = (
                str(meta.get("type", "")) == "source"
                and str(meta.get("status", "")) == "processed"
                and (
                    str(meta.get("generated_from", "")) == source_id
                    or str(meta.get("source_id", "")) == f"pdf-{legacy_short}"
                )
            )
            if not (same_source_draft or same_source_index):
                replacement = candidate.with_name(f"{candidate.stem}-structured.md")
                plan.warnings.append(f"旧版或异源{label}不可覆盖，改用 {replacement.name}。")
                if candidate == source_path:
                    source_path = replacement
                else:
                    draft_path = replacement
    source_link, draft_link = source_path.stem, draft_path.stem

    topic = result["topic"]
    selected_concepts = rank_concepts(result["concepts"], max_concepts)
    existing_topic_path = _unique_generated_artifact(vault, source_id, "topic")
    existing_concept_paths = find_generated_artifacts(vault, source_id, "concept")
    suggested_topic_targets: list[InventoryItem] = []
    for suggestion in result.get("update_suggestions", []):
        target = find_similar(suggestion["target_title"], inventory, "topic")
        if target and target not in suggested_topic_targets:
            suggested_topic_targets.append(target)
    concepts_by_id: dict[str, Path] = {}
    legacy_concepts: list[Path] = []
    for existing in existing_concept_paths:
        meta = parse_frontmatter(existing.read_text(encoding="utf-8", errors="replace"))
        artifact_id = str(meta.get("artifact_id", ""))
        if artifact_id:
            concepts_by_id[artifact_id] = existing
        else:
            legacy_concepts.append(existing)
    knowledge_writes: list[PlannedWrite] = []
    topic_links: list[str] = []
    concept_links: list[str] = []

    def add_knowledge(note: dict[str, Any], note_type: str, slot: int = 0) -> None:
        if note_type == "topic" and suggested_topic_targets:
            target = suggested_topic_targets[0]
            topic_links.append(target.title)
            plan.skipped.append(f"复用现有主题关系并生成更新建议：{target.path}")
            return
        title = sanitize_filename(note["title"])
        folder = vault / "20-Knowledge" / ("Topics" if note_type == "topic" else "Concepts")
        artifact_id = f"{source_id}:{note_type}" if note_type == "topic" else f"{source_id}:concept:{slot}"
        stable_path = existing_topic_path if note_type == "topic" else concepts_by_id.get(artifact_id)
        if stable_path is None and note_type == "concept" and slot < len(legacy_concepts):
            stable_path = legacy_concepts[slot]
        path = stable_path or (folder / f"{title}.md")
        similar = find_similar(note["title"], inventory, note_type)
        if stable_path is not None:
            meta = parse_frontmatter(stable_path.read_text(encoding="utf-8", errors="replace"))
            similar = InventoryItem(
                path=stable_path, title=stable_path.stem, note_type=note_type,
                status=str(meta.get("status", "")), generated_from=str(meta.get("generated_from", "")),
            )
        if similar and similar.status in READ_ONLY_STATUSES:
            suggestion_title = sanitize_filename(f"{note['title']}-更新建议-{short}")
            suggestion_path = suggestion_folder / f"{suggestion_title}.md"
            body = note["problem"] if note_type == "topic" else note["definition"]
            pages = note["evidence_pages"]
            content = render_suggestion(
                title=note["title"], target=similar.title, content=body, pages=pages,
                relation="补充", strength="中", action="人工比较后选择性合并；不得自动修改目标笔记。",
                source_id=source_id, source_link=source_link, today=today,
                artifact_id=f"{source_id}:update-suggestion:{note_type}:{slot}",
            )
            knowledge_writes.append(PlannedWrite(suggestion_path, content, _existing_policy(suggestion_path, source_id), "suggestion"))
            plan.suggestions.append(str(suggestion_path))
            return
        policy = _existing_policy(path, source_id)
        if policy in {"protected", "conflict"}:
            conflict_path = folder / f"{title}-{short}.md"
            policy = _existing_policy(conflict_path, source_id)
            plan.warnings.append(f"{path} 不可覆盖，使用不冲突文件名 {conflict_path.name}。")
            path = conflict_path
        content = (
            render_topic(note, source_id=source_id, source_link=source_link, domain=domain_focus, today=today)
            if note_type == "topic"
            else render_concept(
                note, source_id=source_id, source_link=source_link, domain=domain_focus,
                today=today, artifact_id=artifact_id,
            )
        )
        knowledge_writes.append(PlannedWrite(path, content, policy, note_type))
        (topic_links if note_type == "topic" else concept_links).append(path.stem)

    add_knowledge(topic, "topic")
    for index, concept in enumerate(selected_concepts):
        add_knowledge(concept, "concept", index)

    for suggestion in result.get("update_suggestions", []):
        target = find_similar(suggestion["target_title"], inventory, "concept") or find_similar(
            suggestion["target_title"], inventory, "topic"
        )
        if not target:
            plan.warnings.append(f"模型建议的目标笔记不存在：{suggestion['target_title']}")
            continue
        suggestion_path = suggestion_folder / f"{sanitize_filename(target.title)}-更新建议-{short}.md"
        content = render_suggestion(
            title=target.title, target=target.title, content=suggestion["proposed_content"],
            pages=suggestion["evidence_pages"], relation=suggestion["relation"],
            strength=suggestion["evidence_strength"], action=suggestion["action"],
            source_id=source_id, source_link=source_link, today=today,
            artifact_id=f"{source_id}:update-suggestion:{_normalized_title(target.title)}",
        )
        knowledge_writes.append(PlannedWrite(suggestion_path, content, _existing_policy(suggestion_path, source_id), "suggestion"))
        plan.suggestions.append(str(suggestion_path))

    source_content = render_source(
        result, source_id=source_id, digest=digest, pdf_path=pdf_path, kind=kind,
        model=model, now=timestamp, draft_link=draft_link, topic_links=topic_links,
        concept_links=concept_links,
    )
    draft_content = render_paper_draft(
        result, source_id=source_id, source_link=source_link, model=model, today=today, kind=kind
    )
    for path, content, category in (
        (source_path, source_content, "source"), (draft_path, draft_content, "paper-draft")
    ):
        policy = _existing_policy(path, source_id)
        if category == "source" and path.exists():
            meta = parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if (
                str(meta.get("type", "")) == "source"
                and str(meta.get("status", "")) == "processed"
                and (
                    str(meta.get("generated_from", "")) == source_id
                    or str(meta.get("source_id", "")) == f"pdf-{source_id.removeprefix('pdf-')[:12]}"
                )
            ):
                content = merge_source_index(path.read_text(encoding="utf-8"), content)
                policy = "update"
        if policy in {"protected", "conflict"}:
            raise RuntimeError(f"稳定生成文件存在非本来源或正式内容，拒绝覆盖：{path}")
        plan.writes.append(PlannedWrite(path, content, policy, category))
    for item in knowledge_writes:
        if item.action in {"protected", "conflict"}:
            plan.skipped.append(str(item.path))
        else:
            plan.writes.append(item)
    return plan


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _write_journal(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def execute_plan(
    plan: WritePlan, *, replace_func: Callable[[str | Path, str | Path], None] = os.replace,
    transaction_id: str | None = None,
) -> Path:
    """Commit the complete write set, rolling back every changed target on failure."""
    if not plan.vault:
        raise RuntimeError("写入计划缺少 Vault 根目录。")
    vault = plan.vault.resolve()
    seen: set[Path] = set()
    for item in plan.writes:
        target = item.path.resolve()
        if not target.is_relative_to(vault):
            raise RuntimeError(f"事务目标越出 Vault：{target}")
        if target in seen:
            raise RuntimeError(f"事务中存在重复目标：{target}")
        if item.action not in {"create", "update"}:
            raise RuntimeError(f"事务包含不可写动作 {item.action}：{target}")
        if not isinstance(item.content, str):
            raise RuntimeError(f"事务内容不是文本：{target}")
        seen.add(target)

    transaction_id = transaction_id or (
        datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z") + "-" + uuid.uuid4().hex[:10]
    )
    transaction_dir = vault / "90-Local-Only" / "Processing-Cache" / "transactions" / transaction_id
    staged_dir = transaction_dir / "staged"
    backup_dir = transaction_dir / "backups"
    staged_dir.mkdir(parents=True, exist_ok=False)
    backup_dir.mkdir(parents=True, exist_ok=True)
    journal_path = transaction_dir / "journal.json"
    journal: dict[str, Any] = {
        "transaction_id": transaction_id,
        "source_id": plan.source_id,
        "status": "staging",
        "targets": [str(item.path.resolve()) for item in plan.writes],
        "staged_files": [],
        "backups": {},
        "committed_targets": [],
        "errors": [],
    }
    _write_journal(journal_path, journal)

    staged: list[Path] = []
    committed: list[tuple[Path, Path | None]] = []
    try:
        for index, item in enumerate(plan.writes):
            staged_path = staged_dir / f"{index:04d}.staged"
            atomic_write(staged_path, item.content)
            staged.append(staged_path)
            journal["staged_files"].append(str(staged_path))
        journal["status"] = "staged"
        _write_journal(journal_path, journal)

        journal["status"] = "committing"
        _write_journal(journal_path, journal)
        for index, (item, staged_path) in enumerate(zip(plan.writes, staged)):
            target = item.path.resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                backup = backup_dir / f"{index:04d}.backup"
                shutil.copy2(target, backup)
                journal["backups"][str(target)] = str(backup)
                _write_journal(journal_path, journal)
            replace_func(staged_path, target)
            committed.append((target, backup))
            journal["committed_targets"].append(str(target))
            _write_journal(journal_path, journal)

        journal["status"] = "completed"
        _write_journal(journal_path, journal)
        return journal_path
    except BaseException as exc:
        journal["errors"].append(f"{type(exc).__name__}: {exc}")
        rollback_errors: list[str] = []
        for target, backup in reversed(committed):
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            except BaseException as rollback_exc:
                rollback_errors.append(f"{target}: {type(rollback_exc).__name__}: {rollback_exc}")
        journal["errors"].extend(rollback_errors)
        journal["status"] = "failed" if rollback_errors else "rolled_back"
        _write_journal(journal_path, journal)
        raise RuntimeError(
            f"事务 {transaction_id} 提交失败，状态 {journal['status']}；详见 {journal_path}"
        ) from exc


def manifest_data(
    *, plan: WritePlan, pdf_path: Path, digest: str, model: str, timestamp: str,
    cache_path: Path, errors: list[str] | None = None
) -> dict[str, Any]:
    return {
        "source_id": plan.source_id, "pdf_path": str(pdf_path.resolve()), "pdf_hash": digest,
        "model": model, "processed_at": timestamp,
        "created_files": [str(x.path) for x in plan.writes if x.action == "create"],
        "updated_files": [str(x.path) for x in plan.writes if x.action == "update"],
        "skipped_files": plan.skipped, "update_suggestions": plan.suggestions,
        "warnings": plan.warnings, "errors": errors or [], "model_cache": str(cache_path),
    }


def print_plan(plan: WritePlan, *, dry_run: bool) -> None:
    print("DRY-RUN 写入计划" if dry_run else "执行写入计划")
    print(f"source_id: {plan.source_id}")
    for item in plan.writes:
        print(f"  {item.action:>6}  [{item.category}] {item.path}")
    for item in plan.skipped:
        print(f"    skip  {item}")
    for warning in plan.warnings:
        print(f" warning  {warning}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="本地提取 PDF 文本，经 DeepSeek 生成严格 JSON，并安全写入待审核知识草稿。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  python 00-System/Scripts/ingest_pdf.py \\
    --pdf "/path/to/paper.pdf" \\
    --vault "/Users/suyk/Obsidian/AI_Obsidian_MVP_Starter" \\
    --kind paper --domain-focus "因果推断" \\
    --model deepseek-v4-pro --max-concepts 3 --apply

先预览（默认行为）：把 --apply 改为 --dry-run。PDF 本身永远不会上传。
""",
    )
    parser.add_argument("--pdf", required=True, help="本地 PDF 路径")
    parser.add_argument("--vault", required=True, help="Obsidian Vault 根目录")
    parser.add_argument("--kind", choices=["paper", "textbook"], required=True)
    parser.add_argument("--domain-focus", default="", help="优先学习的领域主线")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-concepts", type=int, default=3)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="验证后执行原子写入")
    mode.add_argument("--dry-run", action="store_true", help="只打印计划，不写入任何文件（默认）")
    parser.add_argument("--force-regenerate", action="store_true", help="重新调用模型；仍不覆盖 reviewed/core")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--max-chars", type=int, default=80_000, help=argparse.SUPPRESS)
    return parser


def run_ingestion(
    args: argparse.Namespace, *, client: ModelClient | None = None,
    pages: list[str] | None = None, now: datetime | None = None
) -> WritePlan:
    pdf_path = Path(args.pdf).expanduser().resolve()
    vault = Path(args.vault).expanduser().resolve()
    if not pdf_path.is_file():
        raise RuntimeError(f"PDF 不存在：{pdf_path}")
    if not vault.is_dir():
        raise RuntimeError(f"Vault 不存在：{vault}")
    if args.max_concepts < 0:
        raise RuntimeError("--max-concepts 不得小于 0。")
    pages = pages if pages is not None else extract_pages(pdf_path)
    digest = file_sha256(pdf_path)
    chunks = chunk_pages(pages, args.max_chars)
    inventory = scan_vault(vault)
    if client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("缺少环境变量 DEEPSEEK_API_KEY。")
        client = DeepSeekClient(api_key, args.base_url)
    result, raw_cache = analyze_document(
        client, model=args.model, chunks=chunks, page_count=len(pages),
        domain_focus=args.domain_focus, kind=args.kind, inventory=inventory,
    )
    plan = build_plan(
        vault, pdf_path=pdf_path, digest=digest, result=result, model=args.model,
        kind=args.kind, domain_focus=args.domain_focus, max_concepts=args.max_concepts,
        inventory=inventory, now=now,
    )
    print_plan(plan, dry_run=not args.apply)
    if not args.apply:
        return plan

    now = now or datetime.now().astimezone()
    stamp = now.strftime("%Y%m%dT%H%M%S%z")
    cache_folder = vault / "90-Local-Only" / "Processing-Cache"
    model_cache = cache_folder / "model-results" / f"{digest[:12]}-{stamp}.json"
    extracted_cache = vault / "90-Local-Only" / "Extracted-Text" / f"{digest[:12]}.txt"
    manifest_path = cache_folder / "manifests" / f"{digest[:12]}-{stamp}.json"
    plan.writes.extend([
        PlannedWrite(extracted_cache, "\n".join(pages), "update" if extracted_cache.exists() else "create", "local-cache"),
        PlannedWrite(model_cache, json.dumps({"validated_result": result, **raw_cache}, ensure_ascii=False, indent=2) + "\n", "create", "local-cache"),
    ])
    manifest = manifest_data(
        plan=plan, pdf_path=pdf_path, digest=digest, model=args.model,
        timestamp=now.isoformat(timespec="seconds"), cache_path=model_cache,
    )
    plan.writes.append(PlannedWrite(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "create", "manifest"))
    execute_plan(plan)
    print(f"完成：创建 {len(manifest['created_files'])}，更新 {len(manifest['updated_files'])}，跳过 {len(plan.skipped)}。")
    print(f"Manifest：{manifest_path}")
    return plan


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_ingestion(args)
    except (RuntimeError, ValidationError, ValueError) as exc:
        raise SystemExit(f"摄入失败：{exc}") from None


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .helpers import classify_text, frontmatter, inbox_folder, note_result, safe_title, unique_path


def run(tools: Any, vault: Path, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    original = str(payload.get("text", ""))
    if not original.strip():
        raise ValueError("capture_text_required")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    assistant_intent = metadata.get("assistant_intent") if isinstance(metadata.get("assistant_intent"), dict) else {}
    inherited = assistant_intent.get("inheritedProposal") is True
    requested_path = str(assistant_intent.get("requestedDestination") or "").strip().replace("\\", "/")
    kind = str(payload.get("capture_type") or classify_text(original))
    if kind not in {"idea", "note", "question", "quote", "decision", "task", "learning_reflection", "source_excerpt"}:
        kind = "note"
    title = safe_title(str(payload.get("title") or (Path(requested_path).stem if requested_path else original)), "未命名记录")
    folder = inbox_folder(vault, kind)
    path = requested_path if inherited and requested_path else unique_path(vault, folder, title, original)
    related = tools.call("search_vault", {"query": title, "limit": 5}, run_id=run_id, step_id=step_id)["items"]
    related = [item for item in related if item["path"] != path]
    links = "\n".join(f"- [[{item['title']}]]" for item in related[:5]) or "- 暂无"
    questions = [line.strip() for line in original.splitlines() if "?" in line or "？" in line]
    if inherited:
        note_type = "concept" if "/Concepts/" in path else "topic" if "/Topics/" in path else "note"
        conversation_id = str(metadata.get("conversation_id") or "")
        source_message_id = str(assistant_intent.get("sourceMessageId") or "")
        content = (
            "---\n"
            f"type: {note_type}\n"
            "status: ai-draft\nreview_state: pending\nagent_managed: true\n"
            f"source_refs: [\"conversation:{conversation_id}\", \"message:{source_message_id}\"]\n"
            "---\n\n"
            + (original if re.match(r"(?m)^#\s+", original) else f"# {title}\n\n{original}")
            + "\n\n## 来源与审核状态\n\n"
            f"- 当前对话：`{conversation_id}`\n- 上一轮提案：`{source_message_id}`\n"
            "- 本文为待审核 AI 草稿；尚未写入正式知识。\n"
        )
        kind = "knowledge-draft"
        target = vault / path
        if target.is_file():
            existing = target.read_text(encoding="utf-8", errors="replace")
            status = re.search(r"(?im)^status\s*:\s*['\"]?([^\n'\"]+)", existing)
            if status and status.group(1).strip().casefold() in {"reviewed", "core"}:
                suggestion_id = hashlib.sha256((path + content).encode()).hexdigest()[:12]
                original_path = path
                path = f"90-Local-Only/Agent-Managed/Update-Suggestions/{Path(original_path).stem}-{suggestion_id}.md"
                content = (
                    "---\ntype: update-suggestion\nstatus: ai-draft\nreview_state: pending\n"
                    f"target: \"{original_path}\"\n---\n\n# 更新建议：{Path(original_path).stem}\n\n"
                    "## 建议内容\n\n" + content
                    + "\n\n## 安全说明\n\n目标笔记为 reviewed/core；原文不会被自动覆盖。\n"
                )
                kind = "update-suggestion"
    else:
        content = (
            frontmatter(kind, ["agent-capture"])
            + f"\n# {title}\n\n## 原始内容\n\n{original}\n\n"
            + "## Agent 整理\n\n"
            + f"- 类型：{kind}\n- 核心内容：{safe_title(original, '待整理')}\n\n"
            + f"## 相关知识\n\n{links}\n\n"
            + "## 待验证问题\n\n"
            + ("\n".join(f"- {item}" for item in questions) if questions else "- 暂无")
            + "\n\n## 后续操作\n\n- [ ] 审核整理内容\n- [ ] 决定是否沉淀为正式知识\n"
        )
    proposed = note_result(title, path, content, kind)
    change_set = tools.call("create_change_set", {"run_id": run_id, "title": f"保存：{title}", "writes": [proposed]}, run_id=run_id, step_id=step_id)
    return {
        "kind": "capture", "original_text": original, "classification": kind,
        "suggested_title": title, "suggested_path": path, "related_notes": related,
        "duplicates": [item for item in related if item["title"].casefold() == title.casefold()],
        "unverified_questions": questions, "proposed_notes": [proposed], "change_set": change_set,
    }

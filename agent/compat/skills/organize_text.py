from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.compat.skills.helpers import frontmatter, inbox_folder, note_result, safe_title, unique_path


def _lines(text: str) -> list[str]:
    return [re.sub(r"^[-*\d.、)\s]+", "", line).strip() for line in text.splitlines() if line.strip()]


def run(tools: Any, vault: Path, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    original = str(payload.get("text", ""))
    if not original.strip():
        raise ValueError("organize_text_required")
    title = safe_title(str(payload.get("title") or original), "待整理内容")
    folder = inbox_folder(vault, "note")
    path = unique_path(vault, folder, title, original)
    lines = _lines(original)
    questions = [line for line in lines if "?" in line or "？" in line]
    decisions = [line for line in lines if any(key in line for key in ("决定", "采用", "必须", "结论"))]
    tasks = [line for line in lines if any(key in line for key in ("TODO", "待办", "下一步", "需要"))]
    conclusions = [line for line in lines if line not in questions and line not in tasks][:8]
    related = tools.call("search_vault", {"query": title, "limit": 5}, run_id=run_id, step_id=step_id)["items"]
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) or "- 暂无"
    content = (
        frontmatter("organized-note", ["agent-organized"])
        + f"\n# {title}\n\n## 原始内容\n\n{original}\n\n"
        + f"## 核心结论\n\n{bullets(conclusions)}\n\n"
        + f"## 决策与观点\n\n{bullets(decisions)}\n\n"
        + f"## 问题与待验证内容\n\n{bullets(questions)}\n\n"
        + f"## 学习任务\n\n{bullets(tasks)}\n\n"
        + "## 相关知识\n\n" + ("\n".join(f"- [[{item['title']}]]" for item in related[:5]) or "- 暂无") + "\n"
    )
    proposed = note_result(title, path, content, "organized-note")
    change_set = tools.call(
        "create_change_set",
        {"run_id": run_id, "title": f"整理：{title}", "writes": [proposed]},
        run_id=run_id,
        step_id=step_id,
        allowed_permissions=("read_only", "proposal"),
    )
    return {
        "kind": "organized-text", "original_text": original, "proposed_notes": [proposed],
        "concept_draft_count": 0, "questions": questions, "learning_tasks": tasks,
        "related_notes": related, "warnings": [], "change_set": change_set,
    }

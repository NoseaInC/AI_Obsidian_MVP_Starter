from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

def run(tools: Any, store: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    question = str(payload.get("text", "")).strip()
    if not question:
        raise ValueError("research_question_required")
    search = tools.call("search_academic_sources", {"query": question, "limit": 12}, run_id=run_id, step_id=step_id)
    sources = list(search["sources"])
    if not sources:
        return {
            "kind": "research-recovery", "status": "partially_completed",
            "title": f"继续研究：{question[:60]}",
            "message": "本地资料中没有找到足够依据，当前对话和已有结果已经保留。",
            "fallback_options": [
                {"id": "trusted-research", "label": "搜索可信网页和论文"},
                {"id": "limited-guide", "label": "先生成来源有限的概念导读"},
                {"id": "add-material", "label": "添加资料"},
            ],
            "providers": search.get("providers", []), "retrieved_at": search.get("retrieved_at"),
            "requires_user_choice": True, "error_code": "research_no_results",
        }
    budget = int(payload.get("time_budget_minutes") or 60)
    selected: list[dict[str, Any]] = []
    spent = 0
    for source in sources:
        minutes = max(1, int(source.get("estimated_minutes", 10)))
        if spent + minutes > budget:
            continue
        selected.append(source); spent += minutes
    if not selected:
        return {
            "kind": "research-recovery", "status": "partially_completed",
            "title": f"调整研究范围：{question[:60]}",
            "message": "找到的资料超出当前时间预算。可以缩小范围，或增加可用时间后继续。",
            "fallback_options": [
                {"id": "edit-task", "label": "缩小研究范围"},
                {"id": "retry", "label": "增加时间后重试"},
            ],
            "providers": search.get("providers", []), "retrieved_at": search.get("retrieved_at"),
            "requires_user_choice": True, "error_code": "research_budget_too_small",
        }
    existing = [item for item in context.get("relevant_notes", [])]
    bundle_id = f"research-{hashlib.sha256((question + run_id).encode()).hexdigest()[:16]}"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    bundle = {
        "id": bundle_id, "run_id": run_id, "title": f"研究：{question[:60]}", "question": question,
        "status": "proposed", "user_goal": context.get("user_profile", {}).get("goal", ""),
        "existing_knowledge": [{"title": item.get("title"), "path": item.get("path")} for item in existing],
        "knowledge_gaps": ["需要结合所选资料进一步验证定义、假设和适用边界"],
        "reading_order": [source["id"] for source in selected], "estimated_minutes": spent,
        "difficulty": "medium", "unverified_questions": ["不同来源的假设和评价口径是否一致？"],
        "next_steps": ["阅读首个来源", "记录证据页码", "审核后再生成正式知识"],
        "providers": search.get("providers", []), "retrieved_at": search["retrieved_at"],
        "created_at": now, "source_count": len(selected),
    }
    store.save_research_bundle(bundle, selected)
    return {"kind": "research-bundle", "bundle": bundle, "sources": selected, "requires_confirmation_to_save": True}

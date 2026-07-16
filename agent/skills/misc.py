from __future__ import annotations

import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def find_related(tools: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    if payload.get("active_note"):
        return {"kind": "related-notes", **tools.call("get_related_notes", {"path": payload["active_note"]}, run_id=run_id, step_id=step_id)}
    return {"kind": "related-notes", **tools.call("search_vault", {"query": payload.get("text", ""), "limit": 10}, run_id=run_id, step_id=step_id)}


def create_study_plan(tools: Any, store: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    state = tools.call("get_learning_state", {}, run_id=run_id, step_id=step_id)["items"]
    candidates = store.list_curriculum_candidates("active")
    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(state[:7]):
        tasks.append({"id": f"task-{uuid.uuid4().hex[:12]}", "title": item["title"], "date": (date.today() + timedelta(days=index % 7)).isoformat(), "minutes": 10 if item.get("mastery", 0) > 1 else 20, "route": "mainline", "state": "proposed", "source": item.get("path", "")})
    for item in candidates[:3]:
        tasks.append({"id": f"task-{uuid.uuid4().hex[:12]}", "title": item["title"], "date": (date.today() + timedelta(days=2)).isoformat(), "minutes": item["estimated_minutes"], "route": item["route"], "state": "proposed", "source": "curriculum"})
    proposal = {"id": f"plan-{uuid.uuid4().hex}", "run_id": run_id, "title": "学习计划提案", "state": "proposed", "tasks": tasks}
    tools.call("create_plan_proposal", proposal, run_id=run_id, step_id=step_id)
    return {"kind": "plan-proposal", "proposal": proposal, "requires_confirmation": True}


def import_material(payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    return {"kind": "delegation", "target": "existing-ingestion-pipeline", "message": "资料导入继续使用 Prepare → Inspect → Apply Prepared；Brain 不绕过现有事务和审核。", "requires_confirmation": True}


def save_to_obsidian(tools: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    writes = payload.get("writes")
    if not isinstance(writes, list) or not writes:
        return {"kind": "save-proposal", "state": "needs-content", "requires_confirmation": True}
    change_set = tools.call("create_change_set", {"run_id": run_id, "title": str(payload.get("title", "保存到 Obsidian")), "writes": writes}, run_id=run_id, step_id=step_id)
    return {"kind": "save-proposal", "change_set": change_set, "requires_confirmation": True}

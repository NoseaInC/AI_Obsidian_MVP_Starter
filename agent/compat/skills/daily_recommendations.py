from __future__ import annotations

from typing import Any

from agent.core import recommendations


def run(vault: Any, store: Any, prepared_loader: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    items = recommendations.build(vault, store, prepared_loader())
    budget = int(payload.get("time_budget_minutes") or (25 if __import__('datetime').date.today().weekday() < 5 else 210))
    selected: list[dict[str, Any]] = []
    used = 0
    due = [item for item in items if item.get("kind") == "review"]
    remaining = [item for item in items if item.get("kind") != "review"]
    for item in due[:2] + remaining:
        minutes = int(item.get("estimatedMinutes", 10))
        if selected and used + minutes > budget:
            continue
        selected.append(item); used += minutes
        if used >= budget or len(selected) >= 5:
            break
    return {"kind": "daily-recommendations", "items": selected, "estimated_minutes": used, "budget_minutes": budget, "ranking": "deterministic", "explainable": True}

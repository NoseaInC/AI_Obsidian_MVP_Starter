from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any


def run(tools: Any, vault: Path, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str) -> dict[str, Any]:
    all_notes = tools.call("search_vault", {"query": "", "limit": 50}, run_id=run_id, step_id=step_id)["items"]
    by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in all_notes:
        by_title[item["title"].casefold()].append(item)
    duplicates = [group for group in by_title.values() if len(group) > 1]
    stale_inbox = []
    for item in all_notes:
        if item["path"].startswith(("00-Inbox/", "01-Inbox/")):
            stale_inbox.append(item)
    suggestions = []
    if duplicates:
        suggestions.append({"type": "duplicate-title", "count": len(duplicates), "action": "人工比较后决定是否合并"})
    if stale_inbox:
        suggestions.append({"type": "inbox", "count": len(stale_inbox), "action": "逐条审核并归档"})
    return {"kind": "vault-audit", "read_only": True, "duplicates": duplicates, "inbox_items": stale_inbox[:20], "suggestions": suggestions, "automatic_changes": 0}

"""Memory retrieval: V1 reuses exact/metadata/FTS-BM25 matching. No vector DB."""
from __future__ import annotations

from typing import Any

from agent.core.memory import policy as P


def search_ranked(
    service: Any,
    query: str,
    *,
    memory_types: list[str] | None = None,
    scope_type: str = "user",
    scope_id: str | None = None,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Ranked retrieval: score by key match > value match > recency."""
    items = service.search(
        query, memory_types=memory_types, scope_type=scope_type, scope_id=scope_id, limit=limit * 3
    )
    q = query.strip().lower()
    scored = []
    for item in items:
        key = str(item.get("memory_key", "")).lower()
        value_text = str(item.get("value", "")).lower()
        score = 0.0
        if q and q in key:
            score += 2.0
        if q and q in value_text:
            score += 1.0
        # recency bonus: newer items rank higher
        updated = str(item.get("updated_at", ""))
        if updated:
            try:
                date_part = updated[:10]
                import datetime
                days_old = (datetime.date.today() - datetime.date.fromisoformat(date_part)).days
                score += max(0.0, 1.0 - days_old / 30.0) * 0.5
            except Exception:
                pass
        scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]

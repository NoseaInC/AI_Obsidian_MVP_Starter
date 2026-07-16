from __future__ import annotations

from typing import Any


class MemoryManager:
    """Stores references and summaries only; knowledge prose remains Markdown."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def user_preferences(self) -> dict[str, Any]:
        return {
            "goal": "数据科学秋招与 Agent 方向", "mainline_ratio": 70, "branch_ratio": 30,
            "weekday_minutes": [20, 30], "weekend_minutes": [180, 240],
            "learning_order": ["定义", "推导", "问题", "例子", "复述", "代码"],
        }

    def run_summary(self, run_id: str) -> dict[str, Any]:
        return self.store.get_brain_run(run_id, include_details=True)

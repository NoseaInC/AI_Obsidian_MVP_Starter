from __future__ import annotations

from typing import Any


class Reflection:
    def summarize(self, plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "step_count": len(plan.get("steps", [])),
            "completed_steps": len(results),
            "warnings": [warning for result in results for warning in result.get("warnings", [])][:20],
            "proposed_action_count": sum(len(result.get("proposed_actions", [])) for result in results),
        }

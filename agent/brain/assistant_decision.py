from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


PlannerAction = Literal["tool", "respond", "clarify"]


@dataclass(frozen=True)
class PlannerDecision:
    action: PlannerAction
    tool_name: str = ""
    arguments: dict[str, Any] | None = None
    purpose: str = ""
    clarification: str = ""

    @classmethod
    def from_value(
        cls,
        value: dict[str, Any],
        *,
        allowed_tools: tuple[str, ...] | list[str],
    ) -> "PlannerDecision":
        if not isinstance(value, dict):
            raise ValueError("planner_decision_object_required")

        action = str(value.get("action") or "").strip()
        if action not in {"tool", "respond", "clarify"}:
            raise ValueError("planner_decision_action_invalid")

        if action == "tool":
            tool_name = str(value.get("tool_name") or "").strip()
            if tool_name not in set(allowed_tools):
                raise ValueError("planner_decision_tool_not_allowed")
            arguments = value.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("planner_decision_arguments_object_required")
            purpose = str(value.get("purpose") or "").strip()
            if not purpose:
                raise ValueError("planner_decision_purpose_required")
            return cls(
                action="tool",
                tool_name=tool_name,
                arguments=arguments,
                purpose=purpose[:300],
            )

        if action == "clarify":
            clarification = str(value.get("clarification") or "").strip()
            if not clarification:
                raise ValueError("planner_decision_clarification_required")
            return cls(action="clarify", clarification=clarification[:1000])

        return cls(action="respond")

    @staticmethod
    def json_schema(allowed_tools: tuple[str, ...] | list[str]) -> dict[str, Any]:
        names = list(dict.fromkeys(str(name) for name in allowed_tools))
        return {
            "name": "assistant_planner_decision",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["tool", "respond", "clarify"],
                    },
                    "tool_name": {
                        "type": "string",
                        "enum": ["", *names],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "purpose": {
                        "type": "string",
                        "maxLength": 300,
                    },
                    "clarification": {
                        "type": "string",
                        "maxLength": 1000,
                    },
                },
                "required": [
                    "action",
                    "tool_name",
                    "arguments",
                    "purpose",
                    "clarification",
                ],
                "additionalProperties": False,
            },
        }

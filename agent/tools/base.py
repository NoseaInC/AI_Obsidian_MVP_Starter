from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


ToolPermission = Literal[
    "read_only",
    "proposal",
    "approval_required",
    "forbidden",
]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    uses_network: bool = False
    mutates_state: bool = False
    timeout_seconds: int = 20
    permission_level: ToolPermission = "read_only"
    idempotent: bool = True
    cancellable: bool = False
    max_result_bytes: int = 16_000

    def model_spec(self) -> dict[str, Any]:
        """Return the provider-neutral OpenAI function-tool contract."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

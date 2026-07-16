from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tools: tuple[str, ...]
    reads_vault: bool = False
    uses_network: bool = False
    creates_change_set: bool = False
    requires_confirmation: bool = False
    timeout: int = 30
    retry_policy: str = "none"


SkillHandler = Callable[[dict[str, Any], dict[str, Any], str, str], dict[str, Any]]

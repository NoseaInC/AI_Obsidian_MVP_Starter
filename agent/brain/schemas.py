from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class BrainStatus(StrEnum):
    CREATED = "created"
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    RUNNING = "running"
    VERIFYING = "verifying"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


INTENTS = {
    "ask_question", "learn_topic", "research_topic", "capture_text", "organize_text",
    "organize_vault", "import_material", "summarize_material", "create_note", "update_note",
    "find_related_notes", "create_study_plan", "generate_recommendations", "generate_quiz",
    "evaluate_explanation", "organize_material", "save_to_obsidian", "analyze_knowledge_gap",
    "generate_daily_plan", "compare_materials", "evaluate_understanding",
    "continue_artifact_revision",
}


@dataclass(frozen=True)
class BrainRequest:
    text: str
    mode: str = "auto"
    source: str = "assistant"
    active_note: str = ""
    selected_text: str = ""
    time_budget_minutes: int | None = None
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    idempotency_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, idempotency_key: str = "") -> "BrainRequest":
        text = str(value.get("text", ""))
        if not text.strip() or len(text) > 250_000:
            raise ValueError("text must contain 1–250000 characters")
        budget = value.get("time_budget_minutes")
        if budget is not None and (not isinstance(budget, int) or not 1 <= budget <= 480):
            raise ValueError("time_budget_minutes must be 1–480")
        return cls(
            text=text, mode=str(value.get("mode", "auto")), source=str(value.get("source", "assistant")),
            active_note=str(value.get("active_note", "")), selected_text=str(value.get("selected_text", "")),
            time_budget_minutes=budget, request_id=str(value.get("request_id") or uuid.uuid4().hex),
            correlation_id=str(value.get("correlation_id") or uuid.uuid4().hex), idempotency_key=idempotency_key,
            metadata=dict(value.get("metadata", {})),
        )

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["text"] = self.text
        return result


@dataclass(frozen=True)
class IntentResult:
    primary_intent: str
    secondary_intents: tuple[str, ...] = ()
    confidence: float = 1.0
    basis: str = "deterministic"

    def __post_init__(self) -> None:
        if self.primary_intent not in INTENTS:
            raise ValueError("Unsupported primary intent")
        if any(item not in INTENTS for item in self.secondary_intents):
            raise ValueError("Unsupported secondary intent")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Intent confidence must be in [0,1]")

    def to_dict(self) -> dict[str, Any]:
        return {"primary_intent": self.primary_intent, "secondary_intents": list(self.secondary_intents), "confidence": self.confidence, "basis": self.basis}


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    skill: str
    purpose: str
    requires_network: bool = False
    can_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrainPlan:
    goal: str
    steps: tuple[PlanStep, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"goal": self.goal, "steps": [item.to_dict() for item in self.steps]}


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str
    risk_level: str
    protected_resources: tuple[str, ...] = ()
    required_ui: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "protected_resources": list(self.protected_resources)}

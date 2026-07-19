from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProposedWrite(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=100_000)
    category: str = Field(default="assistant-agent", max_length=80)


class ChangeProposalResult(BaseModel):
    proposal_id: str
    title: str
    preview: str
    writes: list[dict[str, Any]]
    requires_commit: bool = True
    next_action: Literal["commit_vault_change", "preview_only"] = (
        "commit_vault_change"
    )
    model_instruction: str = (
        "If the user asked to make the change, call commit_vault_change "
        "with proposal_id now. Do not ask for confirmation in plain text; "
        "the Runtime will render the required inline confirmation."
    )
    auto_applied: bool = False
    transaction_id: str | None = None


class WriteCommitResult(BaseModel):
    proposal_id: str
    state: Literal["applied", "denied", "pending"]
    transaction_id: str | None = None
    journal: str | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    message: str


class InlineConfirmation(BaseModel):
    run_id: str
    kind: Literal["write", "question"] = "write"
    proposal_id: str = ""
    title: str
    summary: str
    risk_level: Literal["low", "medium", "high"] = "medium"
    writes: list[dict[str, Any]] = Field(default_factory=list)
    tool_name: str = "commit_vault_change"
    question: str = ""
    options: list[str] = Field(default_factory=list)
    reason: str = ""
    scope_candidates: list[str] = Field(default_factory=list)
    actions: list[str] = Field(
        default_factory=lambda: ["confirm", "reject"]
    )


class AgentRunSnapshot(BaseModel):
    run_id: str
    conversation_id: str
    profile_id: str
    model: str
    request: dict[str, Any]
    status: str
    message_history_json: str = "[]"
    deferred_requests_json: str | None = None
    confirmation: InlineConfirmation | None = None
    checkpoint_id: str = ""
    last_event_sequence: int = 0
    parent_run_id: str | None = None
    forked_from_sequence: int | None = None

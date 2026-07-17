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
    auto_applied: bool = False
    transaction_id: str | None = None


class WriteCommitResult(BaseModel):
    proposal_id: str
    state: Literal["applied", "denied", "pending"]
    transaction_id: str | None = None
    journal: str | None = None
    message: str


class InlineConfirmation(BaseModel):
    run_id: str
    proposal_id: str
    title: str
    summary: str
    risk_level: Literal["medium", "high"] = "medium"
    writes: list[dict[str, Any]] = Field(default_factory=list)
    tool_name: str = "commit_vault_change"
    actions: list[Literal["confirm", "reject"]] = Field(
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

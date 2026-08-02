"""Workspace Policy: directory semantics and writing rules for the Agent.

Rules live in 00-System/AI/*.md (user-editable). This package loads them and
validates WriteIntents before any knowledge write enters the transaction path.
"""
from agent.core.workspace_policy.loader import WorkspacePolicyLoader
from agent.core.workspace_policy.models import (
    NOTE_TYPE_DIRECTORY,
    REVIEW_UNIT_ALLOWED,
    NoteType,
    Purpose,
    WriteIntent,
)
from agent.core.workspace_policy.validator import (
    WriteIntentValidator,
    validate_intent,
)
from agent.core.workspace_policy.resolver import build_intent, profile_summary_text

__all__ = [
    "WorkspacePolicyLoader",
    "WriteIntentValidator",
    "validate_intent",
    "build_intent",
    "profile_summary_text",
    "WriteIntent",
    "NoteType",
    "Purpose",
    "NOTE_TYPE_DIRECTORY",
    "REVIEW_UNIT_ALLOWED",
]

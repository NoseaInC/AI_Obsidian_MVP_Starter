"""WriteIntent resolver: from natural-language intent to a concrete WriteIntent.

V1: helper to build a WriteIntent from purpose/noteType/title, resolving the
target path from the fixed directory mapping.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.workspace_policy.models import NOTE_TYPE_DIRECTORY, WriteIntent


def build_intent(
    *,
    purpose: str,
    note_type: str,
    title: str,
    action: str = "create",
    reason: str = "",
    links: dict[str, Any] | None = None,
    review_unit: bool | None = None,
    status: str = "ai-draft",
    existing_target: str | None = None,
    vault: Path | None = None,
    course_subdir: str | None = None,
) -> WriteIntent:
    """构建 WriteIntent 并解析目标路径。"""
    directory = NOTE_TYPE_DIRECTORY.get(note_type)
    if directory is None:
        raise ValueError(f"unknown_note_type:{note_type}")

    if review_unit is None:
        from agent.core.workspace_policy.models import REVIEW_UNIT_ALLOWED
        review_unit = bool(REVIEW_UNIT_ALLOWED.get(note_type, False))

    target = directory
    if course_subdir and note_type in ("course", "course-chapter"):
        target = f"{directory}/{course_subdir}"
    if note_type in ("project", "project-note"):
        target = f"{directory}/{title}"

    path = f"{target}/{title}.md"
    return WriteIntent(
        purpose=purpose,
        noteType=note_type,
        action=action,
        title=title,
        targetPath=path,
        reason=reason,
        links=links or {},
        existingTarget=existing_target,
        status=status,
        reviewUnit=review_unit,
    )


def profile_summary_text(vault: Path, max_chars: int = 1600) -> str:
    from agent.core.workspace_policy.loader import WorkspacePolicyLoader
    return WorkspacePolicyLoader(vault).profile_summary(max_chars)

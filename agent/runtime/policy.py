from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


DEFAULT_AUTO_WRITE_ROOTS = (
    "10-Inbox",
    "20-Knowledge/Drafts",
    "30-Sources/AI-Drafts",
    "90-Local-Only/Agent/Drafts",
)


@dataclass(frozen=True)
class WriteDecision:
    auto_apply: bool
    risk_level: str
    reason: str


def assess_proposal(
    record: dict[str, Any],
    *,
    write_requested: bool,
    autonomy_mode: str,
    configured_roots: list[str] | None = None,
) -> WriteDecision:
    """Decide whether the runtime can auto-apply without a UI click.

    The model does not participate in this decision.
    """
    if not write_requested:
        return WriteDecision(False, "high", "用户没有明确要求写入")

    writes = list(record.get("writes") or [])
    if not writes:
        return WriteDecision(False, "high", "提案没有有效写入")

    if len(writes) > 3:
        return WriteDecision(False, "high", "一次修改超过 3 个文件")

    roots = tuple(configured_roots or DEFAULT_AUTO_WRITE_ROOTS)
    normalized_roots = tuple(
        str(PurePosixPath(root)).rstrip("/") for root in roots
    )

    for write in writes:
        if str(write.get("action") or "") != "create":
            return WriteDecision(
                False,
                "medium",
                "更新已有笔记需要在对话中确认一次",
            )
        path = str(PurePosixPath(str(write.get("path") or "")))
        if not any(
            path == root or path.startswith(f"{root}/")
            for root in normalized_roots
        ):
            return WriteDecision(
                False,
                "medium",
                "新笔记不在允许自动写入的 Draft/Inbox 目录",
            )

    if autonomy_mode not in {"balanced", "high"}:
        return WriteDecision(
            False,
            "medium",
            "当前自治模式要求确认",
        )

    return WriteDecision(
        True,
        "low",
        "明确的新建 Draft，且位于允许的自动写入目录",
    )

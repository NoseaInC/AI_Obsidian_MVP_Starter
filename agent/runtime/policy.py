from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal


DEFAULT_WRITABLE_ROOTS = (
    "01-Inbox",
    "10-Inbox",
    "20-Knowledge",
    "30-Learning",
    "40-Projects",
    "90-Local-Only/AI-Drafts",
    "90-Local-Only/Agent/Drafts",
)

DEFAULT_SCOPEABLE_CREATE_ROOTS = (
    "01-Inbox",
    "10-Inbox",
    "20-Knowledge/Drafts",
    "90-Local-Only/AI-Drafts",
    "90-Local-Only/Agent/Drafts",
)


@dataclass(frozen=True)
class ToolPermissionDecision:
    decision: Literal["allow", "ask", "deny"]
    risk_level: Literal["low", "medium", "high"]
    reason: str
    scope_candidates: tuple[str, ...] = ()


def _normalized_root(value: str) -> str:
    raw = value.strip().replace("\\", "/").rstrip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ValueError("invalid_permission_root")
    return str(path)


def _normalized_note_path(value: str) -> str:
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.casefold() != ".md"
    ):
        raise ValueError("invalid_note_path")
    return str(path)


def _matching_root(path: str, roots: tuple[str, ...]) -> str | None:
    matches = [root for root in roots if path == root or path.startswith(f"{root}/")]
    return max(matches, key=len) if matches else None


class ToolPermissionGate:
    """Deterministic final authority for model-requested Vault commits.

    The model may choose when to call ``commit_vault_change`` but cannot choose
    this gate's result. Deny rules are evaluated before scoped session grants.
    """

    def __init__(
        self,
        configured_roots: list[str] | None = None,
        scopeable_create_roots: list[str] | None = None,
    ) -> None:
        values = configured_roots or list(DEFAULT_WRITABLE_ROOTS)
        self.writable_roots = tuple(dict.fromkeys(_normalized_root(item) for item in values))
        scope_values = scopeable_create_roots or list(DEFAULT_SCOPEABLE_CREATE_ROOTS)
        self.scopeable_create_roots = tuple(
            dict.fromkeys(_normalized_root(item) for item in scope_values)
        )

    def assess_commit(
        self,
        record: dict[str, Any],
        *,
        session_allow_create_roots: list[str] | None = None,
    ) -> ToolPermissionDecision:
        writes = list(record.get("writes") or [])
        if not writes:
            return ToolPermissionDecision("deny", "high", "提案没有有效写入")

        normalized: list[tuple[dict[str, Any], str, str]] = []
        for write in writes:
            try:
                path = _normalized_note_path(str(write.get("path") or ""))
            except ValueError:
                return ToolPermissionDecision("deny", "high", "目标路径无效或发生越界")
            root = _matching_root(path, self.writable_roots)
            if root is None:
                return ToolPermissionDecision("deny", "high", "目标不在受控可写目录")
            action = str(write.get("action") or "")
            if action not in {"create", "update"}:
                return ToolPermissionDecision("deny", "high", "未知写入动作")
            normalized.append((write, path, root))

        # Updates never inherit a directory-wide session grant. They always
        # require a fresh, concrete confirmation and base-hash validation.
        if any(str(item.get("action")) == "update" for item, _, _ in normalized):
            return ToolPermissionDecision(
                "ask",
                "medium",
                "更新已有笔记需要在当前对话中确认",
            )

        allowed: set[str] = set()
        for value in session_allow_create_roots or []:
            try:
                root = _normalized_root(value)
            except ValueError:
                continue
            if (
                root in self.scopeable_create_roots
                and _matching_root(root, self.writable_roots) is not None
            ):
                allowed.add(root)

        matched_scopes = [
            _matching_root(path, self.scopeable_create_roots)
            for _, path, _ in normalized
        ]
        roots = tuple(dict.fromkeys(
            root for root in matched_scopes if root is not None
        ))
        all_scopeable = all(root is not None for root in matched_scopes)
        if all_scopeable and roots and all(root in allowed for root in roots):
            return ToolPermissionDecision(
                "allow",
                "low",
                "本会话已有该目录的新建文件授权",
                roots,
            )

        return ToolPermissionDecision(
            "ask",
            "medium",
            "提交修改默认需要在当前对话中确认",
            roots,
        )

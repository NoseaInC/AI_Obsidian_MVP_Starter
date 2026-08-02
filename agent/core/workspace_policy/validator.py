"""WriteIntent validator: type/path match, review_unit rules, duplicate detection,
reviewed/core protection, link existence, task authorization scope."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "00-System" / "Scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest_pdf
from agent.core.workspace_policy.models import (
    NOTE_TYPE_DIRECTORY,
    REVIEW_UNIT_ALLOWED,
    Action,
    NoteType,
    Purpose,
    WriteIntent,
)

READ_ONLY_STATUSES = getattr(ingest_pdf, "READ_ONLY_STATUSES", {"reviewed", "core"})


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"  # error | warning

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass
class ValidationResult:
    ok: bool
    intent: WriteIntent | None = None
    issues: list[ValidationIssue] = field(default_factory=list)
    suggestedAction: str | None = None
    suggestedTarget: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "suggestedAction": self.suggestedAction,
            "suggestedTarget": self.suggestedTarget,
        }


class WriteIntentValidator:
    def __init__(self, vault: Path) -> None:
        self.vault = Path(vault)

    def validate(self, intent: WriteIntent, *, authorization_paths: set[str] | None = None) -> ValidationResult:
        issues: list[ValidationIssue] = []
        result = ValidationResult(ok=True, intent=intent)

        # 1. 类型与目录匹配
        directory = NOTE_TYPE_DIRECTORY.get(intent.noteType)
        if directory is None:
            issues.append(ValidationIssue("unknown_note_type", f"未知笔记类型：{intent.noteType}"))
        else:
            target = Path(intent.targetPath)
            if not str(target).startswith(directory):
                issues.append(
                    ValidationIssue(
                        "note_type_directory_mismatch",
                        f"类型 {intent.noteType} 应位于 {directory}，但目标是 {intent.targetPath}",
                    )
                )

        # 2. reviewUnit 与类型匹配
        if REVIEW_UNIT_ALLOWED.get(intent.noteType) is False and intent.reviewUnit:
            issues.append(
                ValidationIssue(
                    "review_unit_not_allowed",
                    f"类型 {intent.noteType} 不允许 review_unit=true",
                )
            )
        if REVIEW_UNIT_ALLOWED.get(intent.noteType) is True and not intent.reviewUnit:
            issues.append(
                ValidationIssue(
                    "review_unit_missing",
                    f"类型 {intent.noteType} 应声明 review_unit=true 才能进入复习",
                    severity="warning",
                )
            )

        # 3. 目标是否为 reviewed/core
        target_path = self.vault / intent.targetPath
        if target_path.is_file():
            meta = ingest_pdf.parse_frontmatter(
                target_path.read_text(encoding="utf-8", errors="replace")
            )
            status = str(meta.get("status", ""))
            if status in READ_ONLY_STATUSES:
                issues.append(
                    ValidationIssue(
                        "target_protected",
                        f"目标是受保护笔记（{status}），不能直接写入",
                    )
                )
                result.suggestedAction = "update_proposal"

        # 4. 链接目标是否存在或标记待创建
        for key, links in (intent.links or {}).items():
            if isinstance(links, str):
                links = [links]
            for link in links or []:
                if not isinstance(link, str) or not link:
                    continue
                resolved = self._resolve_link(link)
                if resolved is None:
                    issues.append(
                        ValidationIssue(
                            "link_target_missing",
                            f"链接 {link} 目标不存在，需创建或移除",
                            severity="warning",
                        )
                    )

        # 5. 任务授权范围
        if authorization_paths:
            target_str = intent.targetPath.replace("\\", "/").lstrip("/")
            if not any(target_str.startswith(p.replace("\\", "/").rstrip("/") + "/") or target_str == p.rstrip("/") for p in authorization_paths):
                issues.append(
                    ValidationIssue(
                        "target_outside_authorization",
                        f"目标 {intent.targetPath} 不在 Task Authorization 范围内",
                    )
                )

        # 6. 已有相同/近似笔记 → 建议 update
        existing = self._find_existing(intent)
        if existing:
            if intent.action == "create":
                issues.append(
                    ValidationIssue(
                        "duplicate_target",
                        f"已存在相同知识主体：{existing}，建议 update 而非 create",
                    )
                )
                result.suggestedAction = "update"
                result.suggestedTarget = existing

        result.ok = not any(i.severity == "error" for i in issues)
        result.issues = issues
        return result

    def _resolve_link(self, link: str) -> Path | None:
        """按 Obsidian Wikilink 规则解析：文件名匹配。"""
        name = link.split("|")[0].strip()
        for md in self.vault.rglob("*.md"):
            if md.stem == name or md.name == name:
                return md
        return None

    def _find_existing(self, intent: WriteIntent) -> str | None:
        """在同一目录下查找同标题或语义相近的笔记。"""
        directory = NOTE_TYPE_DIRECTORY.get(intent.noteType)
        if not directory:
            return None
        root = self.vault / directory
        if not root.is_dir():
            return None
        title = intent.title.strip()
        for md in root.rglob("*.md"):
            if md.stem == title:
                return str(md.relative_to(self.vault))
        # 近似匹配：标题包含
        for md in root.rglob("*.md"):
            if title and (title in md.stem or md.stem in title):
                return str(md.relative_to(self.vault))
        return None


def validate_intent(vault: Path, intent_dict: dict[str, Any], *, authorization_paths: set[str] | None = None) -> dict[str, Any]:
    intent = WriteIntent.from_dict(intent_dict)
    result = WriteIntentValidator(vault).validate(intent, authorization_paths=authorization_paths)
    return {
        "ok": result.ok,
        "intent": intent.to_dict(),
        "issues": [i.to_dict() for i in result.issues],
        "suggestedAction": result.suggestedAction,
        "suggestedTarget": result.suggestedTarget,
    }

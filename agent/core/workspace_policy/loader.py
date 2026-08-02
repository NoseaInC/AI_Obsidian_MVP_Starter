"""Workspace policy loader: reads the four policy Markdown files under 00-System/AI/."""
from __future__ import annotations

from pathlib import Path
from typing import Any

POLICY_FILES = {
    "constitution": "VAULT_CONSTITUTION.md",
    "note_types": "NOTE_TYPES.md",
    "writing": "WRITING_POLICY.md",
    "linking": "LINKING_POLICY.md",
}


class WorkspacePolicyLoader:
    def __init__(self, vault: Path) -> None:
        self.ai_dir = Path(vault) / "00-System" / "AI"

    def _read(self, name: str) -> str:
        path = self.ai_dir / POLICY_FILES[name]
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def get(self, name: str) -> str:
        if name not in POLICY_FILES:
            raise ValueError(f"unknown_policy:{name}")
        return self._read(name)

    def profile_summary(self, max_chars: int = 1600) -> str:
        """常驻 Workspace Profile：约 800 tokens 的规则摘要，每轮注入。"""
        lines = [
            "工作空间规则摘要（Vault Workspace Policy）：",
            "1. 目录职责：10-Sources 资料出处；20-Knowledge 正式知识（Courses/Topics/Concepts）；30-Learning 学习记录；40-Projects 项目内容；90-Local-Only 私有中间物。",
            "2. 笔记类型决定目录：source→10-Sources；course/course-chapter→20-Knowledge/Courses；topic→Topics；concept→Concepts；project/project-note→40-Projects；learning-log→30-Learning。",
            "3. 先检索再新建：已有同一知识主体优先更新，不默认创建重复笔记；只是增加来源时更新 sources 字段。",
            "4. 课程章节（course-chapter）必须 review_unit=false，不进入复习；Topic/Concept 才允许 review_unit=true。",
            "5. 项目专属内容写入 40-Projects，不自动成为通用知识；只有用户明确要求沉淀方法时才建通用 Topic。",
            "6. reviewed/core 笔记默认只读；发现受保护目标时只能拒绝或生成更新提案，不得直接改写。",
            "7. 所有普通知识写入必须先产生结构化 WriteIntent，经 Policy 校验后再进入 Write Plan。",
        ]
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

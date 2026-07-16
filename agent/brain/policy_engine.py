from __future__ import annotations

from pathlib import Path
from typing import Any

from .schemas import PolicyDecision


READ_ONLY = {"find_related_notes", "research_topic", "curriculum_planner", "daily_recommendations", "tutor_topic", "organize_vault"}
LOW_RISK = {"capture_text", "organize_text", "create_study_plan", "save_to_obsidian"}
FORBIDDEN_TOOLS = {"shell", "subprocess", "write_file", "read_file", "arbitrary_url_fetch", "arbitrary_sql"}


class PolicyEngine:
    def decide(self, skill: str, payload: dict[str, Any], definition: Any) -> PolicyDecision:
        allowed_tools = set(getattr(definition, "allowed_tools", ()))
        blocked = sorted(allowed_tools & FORBIDDEN_TOOLS)
        if blocked:
            return PolicyDecision(False, False, f"Skill 请求了禁止工具：{', '.join(blocked)}", "blocked", (), "error")
        target_status = str(payload.get("target_status", ""))
        target_path = str(payload.get("target_path", ""))
        protected = target_status in {"reviewed", "core"} or target_path.startswith("20-Knowledge/") and bool(payload.get("modify_existing"))
        if protected:
            return PolicyDecision(True, True, "正式知识只能生成更新建议", "high", (target_path,), "diff-confirm")
        if skill in READ_ONLY and not getattr(definition, "creates_change_set", False):
            return PolicyDecision(True, False, "只读操作允许自动执行", "read-only")
        if skill in LOW_RISK or getattr(definition, "creates_change_set", False):
            return PolicyDecision(True, True, "候选写入必须先生成 Change Set", "low", (), "change-set")
        return PolicyDecision(True, bool(getattr(definition, "requires_confirmation", False)), "注册能力通过策略检查", "read-only")

    @staticmethod
    def safe_relative_path(vault: Path, relative: str) -> Path:
        candidate = (vault.resolve() / relative).resolve()
        if not candidate.is_relative_to(vault.resolve()) or candidate.is_symlink():
            raise ValueError("invalid_path")
        return candidate

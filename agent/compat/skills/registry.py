from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.compat.skills.base import SkillDefinition, SkillHandler
from agent.compat.skills import capture_text, curriculum_planner, daily_recommendations, misc, organize_text, organize_vault, research_topic, tutor_topic


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, tuple[SkillDefinition, SkillHandler]] = {}

    def register(self, definition: SkillDefinition, handler: SkillHandler) -> None:
        if definition.name in self._skills:
            raise ValueError(f"duplicate_skill:{definition.name}")
        self._skills[definition.name] = (definition, handler)

    def has(self, name: str) -> bool:
        return name in self._skills

    def definition(self, name: str) -> SkillDefinition:
        if name not in self._skills:
            raise ValueError(f"unregistered_skill:{name}")
        return self._skills[name][0]

    def definitions(self) -> list[dict[str, Any]]:
        return [definition.__dict__ for definition, _ in self._skills.values()]

    def execute(self, name: str, payload: dict[str, Any], context: dict[str, Any], *, run_id: str, step_id: str) -> dict[str, Any]:
        if name not in self._skills:
            raise ValueError(f"unregistered_skill:{name}")
        return self._skills[name][1](payload, context, run_id, step_id)


def build_skill_registry(vault: Path, store: Any, tools: Any, prepared_loader: Any = lambda: [], model_gateway: Any = None) -> SkillRegistry:
    vault = vault.resolve()
    registry = SkillRegistry()

    def add(name: str, description: str, allowed_tools: tuple[str, ...], handler: SkillHandler, *, reads: bool = False, network: bool = False, change_set: bool = False, confirm: bool = False, retry: str = "none") -> None:
        registry.register(SkillDefinition(name, description, {"type": "object"}, {"type": "object"}, allowed_tools, reads, network, change_set, confirm, 30, retry), handler)

    add("capture_text", "保留原文并生成 Inbox 笔记提案", ("search_vault", "create_change_set"), lambda p, c, r, s: capture_text.run(tools, vault, p, c, r, s), reads=True, change_set=True, confirm=True)
    add("organize_text", "将长文本整理为一个主笔记候选", ("search_vault", "create_change_set"), lambda p, c, r, s: organize_text.run(tools, vault, p, c, r, s), reads=True, change_set=True, confirm=True)
    add("research_topic", "组合本地与显式配置来源生成 Research Bundle", ("search_academic_sources",), lambda p, c, r, s: research_topic.run(tools, store, p, c, r, s), reads=True, network=True, retry="transient")
    add("curriculum_planner", "根据 reviewed/core 与薄弱点生成候选知识池", ("get_learning_state",), lambda p, c, r, s: curriculum_planner.run(tools, store, p, c, r, s, model_gateway), reads=True)
    add("daily_recommendations", "在时间预算内混合到期复习与下一步学习", ("get_learning_state", "get_due_reviews"), lambda p, c, r, s: daily_recommendations.run(vault, store, prepared_loader, p, c, r, s), reads=True)
    add("tutor_topic", "按定义、推导、问题、例子、复述、代码组织学习", (), lambda p, c, r, s: tutor_topic.run(p, c, r, s, model_gateway), reads=True)
    add("organize_vault", "只读扫描重复、Inbox 与知识组织问题", ("search_vault",), lambda p, c, r, s: organize_vault.run(tools, vault, p, c, r, s), reads=True)
    add("find_related_notes", "查找当前笔记的相关链接或主题匹配", ("search_vault", "get_related_notes"), lambda p, c, r, s: misc.find_related(tools, p, c, r, s), reads=True)
    add("create_study_plan", "创建 proposed 学习计划，不直接确认", ("get_learning_state", "create_plan_proposal"), lambda p, c, r, s: misc.create_study_plan(tools, store, p, c, r, s), reads=True, change_set=True, confirm=True)
    add("import_material", "把资料交给现有 Prepare/Inspect/Apply 管线", (), misc.import_material, confirm=True)
    add("save_to_obsidian", "只创建受控 Change Set", ("create_change_set", "validate_change_set"), lambda p, c, r, s: misc.save_to_obsidian(tools, p, c, r, s), change_set=True, confirm=True)
    return registry

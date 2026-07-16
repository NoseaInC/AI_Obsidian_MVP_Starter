from __future__ import annotations

import uuid
from typing import Any, Protocol

from .errors import BrainError
from .schemas import BrainPlan, BrainRequest, IntentResult, PlanStep


class SkillLookup(Protocol):
    def has(self, name: str) -> bool: ...
    def definition(self, name: str) -> Any: ...


INTENT_SKILLS: dict[str, tuple[str, ...]] = {
    "capture_text": ("capture_text",), "create_note": ("capture_text",),
    "organize_text": ("organize_text",), "summarize_material": ("organize_text",),
    "organize_material": ("import_material",), "compare_materials": ("import_material",),
    "research_topic": ("research_topic",), "find_related_notes": ("find_related_notes",),
    "create_study_plan": ("curriculum_planner", "create_study_plan"),
    "analyze_knowledge_gap": ("curriculum_planner",),
    "generate_recommendations": ("daily_recommendations",), "generate_daily_plan": ("daily_recommendations",),
    "generate_quiz": ("tutor_topic",), "evaluate_explanation": ("tutor_topic",), "evaluate_understanding": ("tutor_topic",),
    "learn_topic": ("tutor_topic",), "ask_question": ("tutor_topic",),
    "organize_vault": ("organize_vault",),
    "import_material": ("import_material",), "update_note": ("save_to_obsidian",), "save_to_obsidian": ("save_to_obsidian",),
    "continue_artifact_revision": ("organize_text",),
}


class Planner:
    def __init__(self, registry: SkillLookup) -> None:
        self.registry = registry

    def plan(self, request: BrainRequest, intent: IntentResult) -> BrainPlan:
        names = list(INTENT_SKILLS.get(intent.primary_intent, ("tutor_topic",)))
        for secondary in intent.secondary_intents:
            for name in INTENT_SKILLS.get(secondary, ()):
                if name not in names:
                    names.append(name)
        steps: list[PlanStep] = []
        for name in names:
            if not self.registry.has(name):
                raise BrainError("brain_skill_not_allowed", f"未注册能力：{name}", False, "更新 Skill Registry")
            definition = self.registry.definition(name)
            steps.append(PlanStep(
                step_id=f"step-{uuid.uuid4().hex[:10]}", skill=name,
                purpose=str(getattr(definition, "description", name)),
                requires_network=bool(getattr(definition, "uses_network", False)),
                can_write=bool(getattr(definition, "creates_change_set", False)),
            ))
        return BrainPlan(request.text[:120], tuple(steps))

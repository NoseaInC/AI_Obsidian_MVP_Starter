from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .errors import BrainError
from .schemas import INTENTS, BrainRequest, IntentResult


MODE_INTENT = {
    "qa": "ask_question", "question": "ask_question", "research": "research_topic",
    "capture": "capture_text", "organize": "organize_text", "plan": "create_study_plan",
    "evaluate": "evaluate_explanation", "tutor": "learn_topic", "material": "organize_material",
}

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("organize_material", re.compile(r"(?:整理|分析|总结).*(?:PDF|论文|教材|材料|文件)|(?:PDF|论文|教材).*(?:整理|分析|总结)", re.I)),
    ("research_topic", re.compile(r"找.*资料|研究(?:一下)?|检索|research|文献", re.I)),
    ("organize_vault", re.compile(r"整理.*(?:知识库|vault|笔记库)|重复笔记|孤立笔记", re.I)),
    ("create_study_plan", re.compile(r"(?:安排|制定|生成).*(?:计划|路线)|周末学习|学习路线", re.I)),
    ("generate_daily_plan", re.compile(r"今天.*(?:分钟|安排|计划)|今日安排", re.I)),
    ("generate_recommendations", re.compile(r"今天学什么|今日推荐|推荐.*学习", re.I)),
    ("generate_quiz", re.compile(r"短测|小测|quiz|出题", re.I)),
    ("evaluate_understanding", re.compile(r"检查.*(?:复述|理解|回答)|评估.*解释", re.I)),
    ("analyze_knowledge_gap", re.compile(r"知识缺口|缺少什么|薄弱点|补全知识", re.I)),
    ("compare_materials", re.compile(r"比较.*(?:资料|论文|材料)|对比.*(?:资料|论文|材料)", re.I)),
    ("organize_text", re.compile(r"整理.*(?:内容|文字|文本|记录|对话)|结构化", re.I)),
    ("capture_text", re.compile(r"保存|存入|记下来|记录.*(?:灵感|想法|笔记)|capture", re.I)),
    ("find_related_notes", re.compile(r"相关笔记|关联知识|找.*笔记", re.I)),
    ("learn_topic", re.compile(r"解释|教我|学习|推导|怎么理解", re.I)),
]


class IntentRouter:
    def __init__(self, classifier: Callable[[BrainRequest], Any] | None = None) -> None:
        self.classifier = classifier

    def route(self, request: BrainRequest) -> IntentResult:
        explicit = MODE_INTENT.get(request.mode)
        if explicit:
            return IntentResult(explicit, basis="explicit-mode")
        matches = [intent for intent, pattern in RULES if pattern.search(request.text)]
        if matches:
            primary = matches[0]
            secondary = tuple(dict.fromkeys(matches[1:4]))
            if primary == "organize_material":
                requested = list(secondary)
                if any(token in request.text for token in ("学习", "安排", "计划")) and "create_study_plan" not in requested:
                    requested.append("create_study_plan")
                if any(token in request.text for token in ("关联", "缺口", "已有知识")) and "analyze_knowledge_gap" not in requested:
                    requested.append("analyze_knowledge_gap")
                secondary = tuple(requested[:3])
            if primary == "research_topic" and "周末" in request.text and "create_study_plan" not in secondary:
                secondary = (*secondary, "create_study_plan")
            return IntentResult(primary, secondary, .92, "deterministic")
        if not self.classifier:
            return IntentResult("ask_question", confidence=.55, basis="fallback")
        for repaired in (False, True):
            try:
                raw = self.classifier(request)
                if isinstance(raw, str):
                    raw = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
                primary = str(raw["primary_intent"])
                secondary = tuple(str(item) for item in raw.get("secondary_intents", []))
                if primary not in INTENTS or any(item not in INTENTS for item in secondary):
                    raise ValueError("unregistered intent")
                return IntentResult(primary, secondary, float(raw.get("confidence", .7)), "model-repaired" if repaired else "model")
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                if not repaired:
                    continue
        return IntentResult("ask_question", confidence=.4, basis="invalid-model-fallback")

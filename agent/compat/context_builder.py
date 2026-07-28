from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent.core import learning

from agent.errors import BrainError
from agent.core.compat_schemas import BrainRequest, IntentResult


SECRET_PATTERN = re.compile(r"(?:authorization|bearer|api[_-]?key|token|secret|password)", re.I)
WORD_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _terms(value: str) -> set[str]:
    return {item.casefold() for item in WORD_PATTERN.findall(value) if len(item) > 1}


class ContextBuilder:
    def __init__(self, vault: Path, token_budget: int = 6000, note_limit: int = 8, excerpt_chars: int = 1600) -> None:
        self.vault = vault.resolve()
        self.token_budget = token_budget
        self.note_limit = note_limit
        self.excerpt_chars = excerpt_chars

    def _safe_note(self, relative: str) -> Path | None:
        if not relative:
            return None
        path = (self.vault / relative).resolve()
        if not path.is_relative_to(self.vault) or not path.is_file() or path.is_symlink() or path.suffix.lower() != ".md":
            return None
        return path

    def build(self, request: BrainRequest, intent: IntentResult) -> dict[str, Any]:
        query_text = request.text + " " + request.selected_text
        query_terms = _terms(query_text)
        active = self._safe_note(request.active_note)
        reviewed_items = learning.scan_reviewed(self.vault)
        candidates: list[tuple[float, learning.KnowledgeItem]] = []
        for item in reviewed_items:
            title_terms = _terms(item.title + " " + item.domain + " " + " ".join(item.weak_points))
            overlap = len(query_terms & title_terms)
            title_match = item.title.casefold() in query_text.casefold()
            score = overlap * 10 + (20 if title_match else 0) + item.importance + (4 - item.mastery) + (2 if item.status == "core" else 1)
            if overlap or title_match or intent.primary_intent in {"generate_recommendations", "create_study_plan", "organize_vault"}:
                candidates.append((score, item))
        candidates.sort(key=lambda row: (-row[0], row[1].title))
        notes: list[dict[str, Any]] = []
        seen: set[Path] = set()
        for _, item in candidates[: self.note_limit]:
            path = item.path.resolve()
            if path in seen:
                continue
            seen.add(path)
            excerpt = path.read_text(encoding="utf-8", errors="replace")[: self.excerpt_chars]
            notes.append({"title": item.title, "path": str(path.relative_to(self.vault)), "status": item.status, "domain": item.domain, "mastery": item.mastery, "excerpt": excerpt})
        active_note: dict[str, Any] = {}
        if active:
            active_note = {"title": active.stem, "path": str(active.relative_to(self.vault)), "excerpt": active.read_text(encoding="utf-8", errors="replace")[: self.excerpt_chars]}
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        supplied_profile = metadata.get("user_profile") if isinstance(metadata.get("user_profile"), dict) else {}
        source_evidence = metadata.get("source_evidence") if isinstance(metadata.get("source_evidence"), list) else []
        context = {
            "user_profile": supplied_profile or {"mainline_ratio": 70, "branch_ratio": 30, "weekday_minutes": [20, 30], "weekend_minutes": [180, 240], "preference": ["定义", "推导", "问题", "例子", "复述", "代码"]},
            "time_budget": {"minutes": request.time_budget_minutes},
            "active_note": active_note,
            "selected_text": request.selected_text[: self.excerpt_chars],
            "knowledge_state": {"reviewed_count": len(reviewed_items)},
            "relevant_notes": notes,
            "source_evidence": source_evidence[:12],
            "constraints": {"no_direct_write": True, "reviewed_core_read_only": True, "original_text_preserved": True},
            "allowed_actions": [],
            "context_manifest": [{"title": item["title"], "type": "reviewed-note"} for item in notes],
        }
        if active_note:
            context["context_manifest"].insert(0, {"title": active_note["title"], "type": "active-note"})
        self._exclude_secrets(context)
        if self._estimate_tokens(context) > self.token_budget:
            while context["relevant_notes"] and self._estimate_tokens(context) > self.token_budget:
                context["relevant_notes"].pop()
                context["context_manifest"].pop()
        if self._estimate_tokens(context) > self.token_budget:
            context["selected_text"] = context["selected_text"][:400]
        if self._estimate_tokens(context) > self.token_budget:
            raise BrainError("brain_context_too_large", "必要上下文仍超过预算", False, "缩短选中文本或缩小任务范围")
        return context

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        return max(1, len(str(value)) // 4)

    @classmethod
    def _exclude_secrets(cls, value: Any) -> None:
        if isinstance(value, dict):
            for key in list(value):
                if SECRET_PATTERN.search(str(key)):
                    value.pop(key, None)
                else:
                    cls._exclude_secrets(value[key])
        elif isinstance(value, list):
            for item in value:
                cls._exclude_secrets(item)

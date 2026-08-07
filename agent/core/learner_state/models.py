"""Learner State data models.

Learner State is a *derived, rebuildable projection* — never a new source of
truth. Raw authorities remain: memory_items, learning_events, quiz/mastery
tables, recommendation feedback. This module only defines the projection shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GoalState:
    memory_id: str
    title: str
    status: str  # active / paused / completed / abandoned
    alignment_weight: float = 1.0  # explicit active goal = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"memoryId": self.memory_id, "title": self.title, "status": self.status, "alignmentWeight": self.alignment_weight}


@dataclass
class KnowledgeState:
    topic: str
    state: str  # knowledge_gap / mastered / learning / neutral
    gap_score: float  # 0..1, 1 = clear gap
    confidence: float
    evidence_count: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic, "state": self.state, "gapScore": round(self.gap_score, 3),
            "confidence": round(self.confidence, 3), "evidenceCount": self.evidence_count,
            "reasons": self.reasons,
        }


@dataclass
class DomainState:
    domain: str
    interest: float  # 0..1, shrunk
    behavior_fit: float  # 0..1, shrunk
    confidence: float
    evidence_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain, "interest": round(self.interest, 3),
            "behaviorFit": round(self.behavior_fit, 3), "confidence": round(self.confidence, 3),
            "evidenceCount": self.evidence_count,
        }


@dataclass
class PreferenceState:
    memory_id: str
    key: str
    summary: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"memoryId": self.memory_id, "key": self.key, "summary": self.summary, "confidence": self.confidence}


@dataclass
class BehaviorState:
    event_count: int
    covered_days: int
    maturity: float  # 0..1
    completion_rate: float  # 0..1
    hint_dependency_rate: float  # 0..1
    preferred_duration_min: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eventCount": self.event_count, "coveredDays": self.covered_days,
            "maturity": round(self.maturity, 3), "completionRate": round(self.completion_rate, 3),
            "hintDependencyRate": round(self.hint_dependency_rate, 3),
            "preferredDurationMin": self.preferred_duration_min,
        }


@dataclass
class EvidenceSummary:
    learning_events: int = 0
    quiz_results: int = 0
    mastery_records: int = 0
    feedback_count: int = 0
    memory_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "learningEvents": self.learning_events, "quizResults": self.quiz_results,
            "masteryRecords": self.mastery_records, "feedbackCount": self.feedback_count,
            "memoryCount": self.memory_count,
        }


@dataclass
class LearnerState:
    generated_at: str
    active_goals: list[GoalState] = field(default_factory=list)
    knowledge_states: list[KnowledgeState] = field(default_factory=list)
    domain_states: list[DomainState] = field(default_factory=list)
    preferences: list[PreferenceState] = field(default_factory=list)
    behavior: BehaviorState | None = None
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generatedAt": self.generated_at,
            "activeGoals": [g.to_dict() for g in self.active_goals],
            "knowledgeStates": [k.to_dict() for k in self.knowledge_states],
            "domainStates": [d.to_dict() for d in self.domain_states],
            "preferences": [p.to_dict() for p in self.preferences],
            "behavior": self.behavior.to_dict() if self.behavior else None,
            "evidenceSummary": self.evidence_summary.to_dict(),
        }

    def signals_for_ranking(self, topic: str | None, domain: str | None) -> dict[str, float]:
        """Four unified signals consumed by the Today Ranking:
        goal_alignment / knowledge_gap / behavior_fit / interest — all 0..1."""
        return {
            "goal_alignment": _goal_alignment_for(self.active_goals, topic, domain),
            "knowledge_gap": _gap_for(self.knowledge_states, topic, domain),
            "behavior_fit": _behavior_fit_for(self.domain_states, domain),
            "interest": _interest_for(self.domain_states, domain),
        }


def _goal_alignment_for(goals: list[GoalState], topic: str | None, domain: str | None) -> float:
    if not goals:
        return 0.0
    topic_l = (topic or "").casefold()
    domain_l = (domain or "").casefold()
    best = 0.0
    for goal in goals:
        title_l = goal.title.casefold()
        w = goal.alignment_weight
        if topic_l and _overlap(topic_l, title_l, 4):
            best = max(best, 1.0 * w)  # topic direct match
        elif domain_l and _overlap(domain_l, title_l, 3):
            best = max(best, 0.6 * w)  # domain coarse match
    return round(min(1.0, best), 3)


def _overlap(a: str, b: str, min_len: int) -> bool:
    """True if a and b share a run of ≥ min_len characters (Chinese-friendly)."""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # slide window over the shorter string
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    for size in range(min_len, min(len(short), len(long)) + 1):
        for start in range(0, len(short) - size + 1):
            if short[start:start + size] in long:
                return True
    return False


def _gap_for(states: list[KnowledgeState], topic: str | None, domain: str | None) -> float:
    if not topic:
        return 0.0
    topic_l = topic.casefold()
    best = 0.0
    for state in states:
        if state.topic.casefold() == topic_l:
            best = max(best, state.gap_score)
    return round(min(1.0, best), 3)


def _behavior_fit_for(domains: list[DomainState], domain: str | None) -> float:
    if not domain:
        return 0.0
    domain_l = domain.casefold()
    for state in domains:
        if state.domain.casefold() == domain_l:
            return round(min(1.0, state.behavior_fit), 3)
    return 0.0


def _interest_for(domains: list[DomainState], domain: str | None) -> float:
    if not domain:
        return 0.0
    domain_l = domain.casefold()
    for state in domains:
        if state.domain.casefold() == domain_l:
            return round(min(1.0, state.interest), 3)
    return 0.0

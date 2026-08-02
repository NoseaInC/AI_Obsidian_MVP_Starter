"""Memory policy: type whitelists, status transitions, promotion rules."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

MEMORY_TYPES = {"goal", "preference", "knowledge_state", "project_decision"}

SCOPE_TYPES = {"vault", "project", "conversation", "user"}

SOURCE_TYPES = {
    "explicit_user",
    "verified",
    "repeated_observed",
    "single_observed",
    "model_candidate",
}

EVIDENCE_LEVELS = {"claimed", "observed", "verified"}

STATUSES = {"active", "candidate", "superseded", "deleted"}

GOAL_STATES = {"active", "paused", "completed", "abandoned"}

# 冲突优先级：值越大优先级越高
CONFLICT_PRIORITY = {
    "explicit_user": 5,
    "verified": 4,
    "repeated_observed": 3,
    "single_observed": 2,
    "model_candidate": 1,
}

# 隐式候选晋升条件
PROMOTION_MIN_EVIDENCE = 3
PROMOTION_MIN_DISTINCT_DAYS = 2
PROMOTION_MIN_CONFIDENCE = 0.75


def validate_memory_type(memory_type: str) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"unsupported_memory_type:{memory_type}")
    return memory_type


def validate_scope(scope_type: str) -> str:
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"unsupported_scope_type:{scope_type}")
    return scope_type


def validate_source_type(source_type: str) -> str:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unsupported_source_type:{source_type}")
    return source_type


def validate_status(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"unsupported_status:{status}")
    return status


def validate_evidence_level(level: str) -> str:
    if level not in EVIDENCE_LEVELS:
        raise ValueError(f"unsupported_evidence_level:{level}")
    return level


def validate_confidence(confidence: float) -> float:
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("confidence_out_of_range")
    return float(confidence)


def can_promote_candidate(evidence_count: int, distinct_days: int, confidence: float) -> bool:
    """隐式候选晋升条件：至少 3 条证据、来自 2 个不同日期、置信度 >= 0.75。"""
    return (
        evidence_count >= PROMOTION_MIN_EVIDENCE
        and distinct_days >= PROMOTION_MIN_DISTINCT_DAYS
        and confidence >= PROMOTION_MIN_CONFIDENCE
    )


def resolve_conflict(new_source_type: str, existing_source_type: str) -> bool:
    """新记忆是否应该取代现有记忆（不原地覆盖，走 supersede）。"""
    return CONFLICT_PRIORITY.get(new_source_type, 0) > CONFLICT_PRIORITY.get(existing_source_type, 0)


def normalize_expiry(expires_at: Any) -> str | None:
    if not expires_at:
        return None
    if isinstance(expires_at, datetime):
        return expires_at.isoformat(timespec="seconds")
    return str(expires_at)


def default_expiry(days: int | None = None) -> str | None:
    if days is None:
        return None
    return (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")

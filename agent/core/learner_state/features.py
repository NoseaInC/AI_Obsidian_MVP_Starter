"""Feature computation for Learner State.

All inferred personalization features are *shrunk* before use:

    effective = neutral + (raw - neutral) * confidence * maturity

maturity is a function of evidence_count and covered_days. Explicit user
preferences bypass shrinkage (confidence=1, maturity=1).
"""
from __future__ import annotations

from datetime import date
from typing import Any

NEUTRAL = 0.5


def maturity(evidence_count: int, covered_days: int) -> float:
    """0..1 — low for a few events in one day, higher for sustained behavior."""
    count_factor = min(1.0, evidence_count / 40.0)
    day_factor = min(1.0, covered_days / 14.0)
    # 两个维度取几何平均，避免单维爆表
    return round((count_factor * day_factor) ** 0.5, 3)


def shrink(raw: float, confidence: float, mat: float) -> float:
    """Shrink a raw 0..1 feature toward neutral using confidence and maturity."""
    raw = max(0.0, min(1.0, float(raw)))
    confidence = max(0.0, min(1.0, float(confidence)))
    mat = max(0.0, min(1.0, float(mat)))
    return round(NEUTRAL + (raw - NEUTRAL) * confidence * mat, 3)


def explicit_confidence() -> tuple[float, float]:
    """Explicit preferences: confidence=1, maturity=1 (no shrinkage)."""
    return 1.0, 1.0


# ── Knowledge gap ──────────────────────────────────────────

def knowledge_gap(
    *,
    mastery: int | None,
    quiz_correctness: list[float],
    hint_used: int,
    abandoned: int,
    memory_state: str | None,  # "gap" | "claimed_mastery" | None
    prerequisite_gap: bool,
    recency_days: int = 90,
) -> dict[str, Any]:
    """0..1 gap score from deterministic rules.

    1.0 = clear knowledge gap; 0.0 = no evident gap.
    Returns {gapScore, confidence, evidenceCount, reasons}.
    """
    reasons: list[str] = []
    evidence = 0
    score_parts: list[float] = []

    if mastery is not None:
        evidence += 1
        if mastery <= 1:
            score_parts.append(0.75)
            reasons.append(f"mastery={mastery}")
        elif mastery >= 3:
            score_parts.append(0.15)
            reasons.append(f"mastery={mastery}")

    if quiz_correctness:
        recent = quiz_correctness[:5]
        evidence += len(recent)
        accuracy = sum(recent) / len(recent)
        if accuracy < 0.6:
            score_parts.append(0.85)
            reasons.append(f"recent_quiz_accuracy={accuracy:.2f}")
        elif accuracy >= 0.85:
            score_parts.append(0.15)
            reasons.append(f"recent_quiz_accuracy={accuracy:.2f}")
        else:
            score_parts.append(0.45)
            reasons.append(f"recent_quiz_accuracy={accuracy:.2f}")

    if hint_used:
        evidence += hint_used
        score_parts.append(min(0.6, 0.3 + 0.1 * hint_used))
        reasons.append(f"hint_used={hint_used}")

    if abandoned:
        evidence += abandoned
        score_parts.append(0.55)
        reasons.append(f"abandoned={abandoned}")

    if memory_state == "gap":
        evidence += 1
        score_parts.append(0.8)
        reasons.append("memory knowledge_state=observed_gap")
    elif memory_state == "claimed_mastery":
        evidence += 1
        score_parts.append(0.35)
        reasons.append("memory knowledge_state=claimed(not verified)")

    if prerequisite_gap:
        evidence += 1
        score_parts.append(0.6)
        reasons.append("prerequisite_gap")

    if not score_parts:
        return {"gapScore": 0.0, "confidence": 0.0, "evidenceCount": 0, "reasons": []}

    raw = sum(score_parts) / len(score_parts)
    # 时间衰减：久远证据置信度低
    recency_factor = max(0.3, 1.0 - recency_days / 180.0)
    confidence = min(0.95, 0.4 + evidence * 0.08) * recency_factor
    return {
        "gapScore": round(raw, 3),
        "confidence": round(confidence, 3),
        "evidenceCount": evidence,
        "reasons": reasons,
    }


# ── Goal alignment ─────────────────────────────────────────

def goal_alignment(goals: list[dict[str, Any]], topic: str | None, domain: str | None) -> dict[str, Any]:
    """0..1. Explicit active goals > inferred; topic direct > domain coarse."""
    if not goals:
        return {"alignment": 0.0, "confidence": 0.0, "matchedGoal": None, "reasons": []}
    topic_l = (topic or "").casefold()
    domain_l = (domain or "").casefold()
    best_score = 0.0
    best_goal = None
    best_kind = ""
    for goal in goals:
        title_l = str(goal.get("title", "")).casefold()
        status = str(goal.get("status", ""))
        kind = "explicit" if str(goal.get("sourceType", "")) == "explicit_user" else "inferred"
        weight = 1.0 if status == "active" else 0.4  # paused/completed 权重低
        if topic_l and (topic_l in title_l or title_l in topic_l):
            score = 1.0 * weight
            match_kind = "topic"
        elif domain_l and (domain_l in title_l or title_l in domain_l):
            score = 0.6 * weight
            match_kind = "domain"
        else:
            continue
        if score > best_score:
            best_score, best_goal, best_kind = score, goal, match_kind
    if best_goal is None:
        return {"alignment": 0.0, "confidence": 0.0, "matchedGoal": None, "reasons": []}
    confidence = 1.0 if best_kind == "explicit" else 0.6
    return {
        "alignment": round(min(1.0, best_score), 3),
        "confidence": confidence,
        "matchedGoal": str(best_goal.get("title", "")),
        "reasons": [f"matched_goal={best_kind}:{best_goal.get('title', '')}"],
    }


# ── Behavior fit ───────────────────────────────────────────

def behavior_fit(
    *,
    completed_count: int,
    started_count: int,
    abandoned_count: int,
    avg_duration_min: float | None,
    preferred_duration_min: int | None,
    target_duration_min: int | None,
) -> dict[str, Any]:
    """0..1 — does the candidate match real study behavior."""
    total = started_count + abandoned_count
    if total == 0:
        return {"fit": 0.0, "confidence": 0.0, "reasons": []}
    reasons: list[str] = []
    parts: list[float] = []

    completion = completed_count / total if total else 0.0
    parts.append(0.3 + 0.6 * completion)
    reasons.append(f"completion_rate={completion:.2f}")

    if abandoned_count and started_count:
        abandon_rate = abandoned_count / max(1, started_count)
        if abandon_rate > 0.5:
            parts.append(0.2)
            reasons.append(f"high_abandon_rate={abandon_rate:.2f}")

    if preferred_duration_min and target_duration_min:
        ratio = target_duration_min / preferred_duration_min
        if 0.5 <= ratio <= 1.5:
            parts.append(0.9)
            reasons.append(f"duration_fit={target_duration_min}min vs preferred {preferred_duration_min}min")
        elif ratio > 2.5:
            parts.append(0.25)
            reasons.append(f"too_long={target_duration_min}min vs preferred {preferred_duration_min}min")
        else:
            parts.append(0.6)

    confidence = min(0.9, 0.35 + total * 0.06)
    return {
        "fit": round(sum(parts) / len(parts), 3),
        "confidence": round(confidence, 3),
        "reasons": reasons,
    }


# ── Interest ───────────────────────────────────────────────

def interest(
    *,
    explicit_interest: bool,
    repeated_active: int,
    favorites: int,
    clicks: int,
    exposures: int,
    negative: int,  # not_interested / dismissed / off_route / repeated snooze
) -> dict[str, Any]:
    """0..1. Explicit > repeated active > favorite > clicked > single exposure.
    Negative feedback lowers interest. A single click cannot dominate."""
    parts: list[tuple[float, float]] = []  # (value, weight)
    reasons: list[str] = []

    if explicit_interest:
        parts.append((1.0, 1.0))
        reasons.append("explicit_interest")
    if repeated_active >= 2:
        parts.append((0.9, 0.8))
        reasons.append(f"repeated_active={repeated_active}")
    if favorites:
        parts.append((0.85, 0.6))
        reasons.append(f"favorites={favorites}")
    if clicks:
        parts.append((0.6, min(0.5, 0.2 + clicks * 0.1)))
        reasons.append(f"clicks={clicks}")
    if exposures and not (clicks or favorites or repeated_active):
        parts.append((0.5, 0.15))  # single exposure alone → near neutral
        reasons.append("exposure_only")

    if not parts:
        return {"interest": 0.0, "confidence": 0.0, "reasons": []}

    raw = sum(v * w for v, w in parts) / sum(w for _, w in parts)
    # 负反馈衰减
    if negative:
        raw -= min(0.5, negative * 0.15)
        reasons.append(f"negative={negative}")
        raw = max(0.0, raw)

    evidence = len(parts) + negative
    confidence = min(0.9, 0.4 + evidence * 0.08)
    return {"interest": round(raw, 3), "confidence": round(confidence, 3), "reasons": reasons}


# ── Aggregators ────────────────────────────────────────────

def aggregate_events(
    events: list[dict[str, Any]],
    now: date,
) -> dict[str, Any]:
    """Aggregate learning_events into behavior counters keyed by domain/topic."""
    domains: dict[str, dict[str, Any]] = {}
    days: set[str] = set()
    durations: list[float] = []
    for event in events:
        created = str(event.get("created_at") or "")
        if created:
            days.add(created[:10])
        domain = str(event.get("domain") or "未知")
        bucket = domains.setdefault(domain, {
            "completed": 0, "started": 0, "abandoned": 0, "hint": 0, "clicks": 0,
            "exposures": 0, "favorites": 0, "negative": 0, "active": 0,
            "quiz_correct": [], "quiz_count": 0, "durations": [],
        })
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                import json
                payload = json.loads(payload)
            except Exception:
                payload = {}
        bucket["started"] += 1 if event_type in {"study_session_started", "recommendation_started"} else 0
        bucket["completed"] += 1 if event_type in {"study_session_completed", "recommendation_completed"} else 0
        bucket["abandoned"] += 1 if event_type == "study_session_abandoned" else 0
        bucket["hint"] += 1 if event_type == "hint_opened" else 0
        bucket["clicks"] += 1 if event_type == "recommendation_clicked" else 0
        bucket["exposures"] += 1 if event_type == "recommendation_exposed" else 0
        bucket["negative"] += 1 if event_type in {"recommendation_feedback", "recommendation_dismissed"} else 0
        if event_type in {"study_session_started", "recommendation_started"}:
            bucket["active"] += 1
        if event_type == "quiz_completed":
            bucket["quiz_count"] += 1
            try:
                bucket["quiz_correct"].append(float(payload.get("correctness", 0) or 0))
            except (TypeError, ValueError):
                pass
        duration = event.get("duration_ms")
        if duration:
            try:
                durations.append(float(duration) / 60000.0)
            except (TypeError, ValueError):
                pass
    return {
        "domains": domains,
        "covered_days": len(days),
        "event_count": len(events),
        "avg_duration_min": round(sum(durations) / len(durations), 1) if durations else None,
    }

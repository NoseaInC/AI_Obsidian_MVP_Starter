"""LearnerStateBuilder: derive the Unified Learner State from raw evidence.

Authorities (never modified here):
  - memory_items            (long-term semantic memory)
  - learning_events         (behavior)
  - quiz/mastery records    (objective learning evidence)
  - recommendation feedback (interest signals)

LearnerState is a rebuildable projection only.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agent.core.learner_state import features as F
from agent.core.learner_state.models import (
    BehaviorState,
    DomainState,
    EvidenceSummary,
    GoalState,
    KnowledgeState,
    LearnerState,
    PreferenceState,
)


class LearnerStateBuilder:
    def __init__(self, store: Any, memory: Any) -> None:
        self.store = store
        self.memory = memory

    def build(self, *, now: date | None = None) -> LearnerState:
        now = now or date.today()
        memory_items = self._active_memory_items()
        events = self._learning_events()
        quiz_results = self._quiz_results()
        mastery_records = self._mastery_records()
        feedback = self._feedback()

        aggregated = F.aggregate_events(events, now)

        # ── Goals ─────────────────────────────────────────
        goals = [
            GoalState(
                memory_id=item["id"],
                title=item["memory_key"],
                status=str(item.get("value", {}).get("status") or "active"),
                alignment_weight=1.0 if str(item.get("source_type", "")) == "explicit_user" else 0.7,
            )
            for item in memory_items
            if item["memory_type"] == "goal" and item["status"] == "active"
        ]

        # ── Preferences ───────────────────────────────────
        preferences = [
            PreferenceState(
                memory_id=item["id"],
                key=item["memory_key"],
                summary=str(item.get("value", {}).get("summary") or ""),
                confidence=float(item.get("confidence", 0)),
            )
            for item in memory_items
            if item["memory_type"] == "preference" and item["status"] == "active"
        ]

        # ── Behavior ──────────────────────────────────────
        mat = F.maturity(aggregated["event_count"], aggregated["covered_days"])
        behavior = BehaviorState(
            event_count=aggregated["event_count"],
            covered_days=aggregated["covered_days"],
            maturity=mat,
            completion_rate=self._completion_rate(aggregated),
            hint_dependency_rate=self._hint_dependency_rate(aggregated),
            preferred_duration_min=self._preferred_duration(aggregated),
        )

        # ── Knowledge states ──────────────────────────────
        knowledge_states = self._knowledge_states(
            memory_items, quiz_results, mastery_records, aggregated
        )

        # ── Domain states (interest + behavior fit) ───────
        domain_states = self._domain_states(memory_items, aggregated)

        evidence_summary = EvidenceSummary(
            learning_events=len(events),
            quiz_results=len(quiz_results),
            mastery_records=len(mastery_records),
            feedback_count=len(feedback),
            memory_count=len(memory_items),
        )

        return LearnerState(
            generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            active_goals=goals,
            knowledge_states=knowledge_states,
            domain_states=domain_states,
            preferences=preferences,
            behavior=behavior,
            evidence_summary=evidence_summary,
        )

    # ── helpers ───────────────────────────────────────────

    def _active_memory_items(self) -> list[dict[str, Any]]:
        return self.memory.list_active(limit=200)

    def _learning_events(self) -> list[dict[str, Any]]:
        try:
            return self.store.list_learning_events(limit=5000)
        except Exception:
            return []

    def _quiz_results(self) -> list[dict[str, Any]]:
        """Quiz correctness lives in learning_events payloads (quizzes table is schema-only)."""
        rows = self.store.connection.execute(
            "SELECT payload_json, created_at FROM learning_events WHERE event_type='quiz_completed' ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        results = []
        for row in rows:
            payload = row["payload_json"]
            if isinstance(payload, str):
                try:
                    import json
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            try:
                results.append({
                    "correctness": float(payload.get("correctness", 0) or 0),
                    "createdAt": str(row["created_at"]),
                })
            except (TypeError, ValueError):
                continue
        return results

    def _mastery_records(self) -> list[dict[str, Any]]:
        try:
            rows = self.store.connection.execute(
                "SELECT artifact_id, new_mastery, created_at FROM mastery_history WHERE confirmed=1 ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _feedback(self) -> list[dict[str, Any]]:
        try:
            rows = self.store.connection.execute(
                "SELECT action, details_json, created_at FROM recommendation_feedback ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def _completion_rate(self, aggregated: dict[str, Any]) -> float:
        totals = {"completed": 0, "started": 0}
        for bucket in aggregated["domains"].values():
            totals["completed"] += bucket["completed"]
            totals["started"] += bucket["started"]
        if not totals["started"]:
            return 0.0
        return round(totals["completed"] / totals["started"], 3)

    def _hint_dependency_rate(self, aggregated: dict[str, Any]) -> float:
        totals = {"hint": 0, "started": 0}
        for bucket in aggregated["domains"].values():
            totals["hint"] += bucket["hint"]
            totals["started"] += bucket["started"]
        if not totals["started"]:
            return 0.0
        return round(min(1.0, totals["hint"] / totals["started"]), 3)

    def _preferred_duration(self, aggregated: dict[str, Any]) -> int | None:
        durations = [
            d for bucket in aggregated["domains"].values()
            for d in bucket.get("durations", [])
        ]
        if not durations:
            return None
        return int(round(sum(durations) / len(durations)))

    def _knowledge_states(
        self,
        memory_items: list[dict[str, Any]],
        quiz_results: list[dict[str, Any]],
        mastery_records: list[dict[str, Any]],
        aggregated: dict[str, Any],
    ) -> list[KnowledgeState]:
        """Merge mastery/quiz evidence with memory knowledge_state per topic."""
        topics: dict[str, dict[str, Any]] = {}

        # mastery records per artifact id
        for record in mastery_records:
            key = str(record["artifact_id"])
            bucket = topics.setdefault(key, {"mastery": [], "quiz": [], "hint": 0, "abandoned": 0, "memory": None})
            bucket["mastery"].append(int(record.get("new_mastery", 0) or 0))

        # memory knowledge_state items
        for item in memory_items:
            if item["memory_type"] != "knowledge_state":
                continue
            key = item["memory_key"]
            bucket = topics.setdefault(key, {"mastery": [], "quiz": [], "hint": 0, "abandoned": 0, "memory": None})
            bucket["memory"] = str(item.get("value", {}).get("level") or item.get("evidence_level") or "observed")

        # topic-level quiz/hint/abandon from learning events (subjectId ≈ topic title in practice)
        quiz_by_topic: dict[str, list[float]] = {}
        hint_by_topic: dict[str, int] = {}
        abandon_by_topic: dict[str, int] = {}
        rows = self.store.connection.execute(
            "SELECT event_type, topic, payload_json FROM learning_events WHERE event_type IN ('quiz_completed','hint_opened','study_session_abandoned') LIMIT 2000"
        ).fetchall()
        for row in rows:
            topic = str(row["topic"] or "")
            if not topic:
                continue
            event_type = str(row["event_type"])
            if event_type == "quiz_completed":
                payload = row["payload_json"]
                if isinstance(payload, str):
                    try:
                        import json
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                try:
                    quiz_by_topic.setdefault(topic, []).append(float(payload.get("correctness", 0) or 0))
                except (TypeError, ValueError):
                    pass
            elif event_type == "hint_opened":
                hint_by_topic[topic] = hint_by_topic.get(topic, 0) + 1
            elif event_type == "study_session_abandoned":
                abandon_by_topic[topic] = abandon_by_topic.get(topic, 0) + 1

        for topic, correctness in quiz_by_topic.items():
            bucket = topics.setdefault(topic, {"mastery": [], "quiz": [], "hint": 0, "abandoned": 0, "memory": None})
            bucket["quiz"].extend(correctness)
        for topic, count in hint_by_topic.items():
            topics.setdefault(topic, {"mastery": [], "quiz": [], "hint": 0, "abandoned": 0, "memory": None})["hint"] = count
        for topic, count in abandon_by_topic.items():
            topics.setdefault(topic, {"mastery": [], "quiz": [], "hint": 0, "abandoned": 0, "memory": None})["abandoned"] = count

        states: list[KnowledgeState] = []
        for topic, bucket in topics.items():
            mastery = max(bucket["mastery"]) if bucket["mastery"] else None
            memory_state = bucket["memory"]
            gap_res = F.knowledge_gap(
                mastery=mastery,
                quiz_correctness=bucket["quiz"],
                hint_used=bucket["hint"],
                abandoned=bucket["abandoned"],
                memory_state=memory_state,
                prerequisite_gap=False,
            )
            if memory_state == "verified" or (mastery is not None and mastery >= 3):
                state = "mastered"
            elif gap_res["gapScore"] >= 0.5:
                state = "knowledge_gap"
            elif memory_state in {"claimed", "observed"}:
                state = "learning"
            else:
                state = "neutral"
            states.append(KnowledgeState(
                topic=topic, state=state,
                gap_score=gap_res["gapScore"],
                confidence=gap_res["confidence"],
                evidence_count=gap_res["evidenceCount"],
                reasons=gap_res["reasons"],
            ))
        return states

    def _domain_states(
        self,
        memory_items: list[dict[str, Any]],
        aggregated: dict[str, Any],
    ) -> list[DomainState]:
        # memory interest hints (favorite / explicit domain preference)
        explicit_domains: set[str] = set()
        for item in memory_items:
            if item["memory_type"] == "preference":
                explicit_domains.add(item["memory_key"])

        domains: list[DomainState] = []
        for domain, bucket in aggregated["domains"].items():
            interest_res = F.interest(
                explicit_interest=domain in explicit_domains,
                repeated_active=bucket["active"],
                favorites=bucket["favorites"],
                clicks=bucket["clicks"],
                exposures=bucket["exposures"],
                negative=bucket["negative"],
            )
            fit_res = F.behavior_fit(
                completed_count=bucket["completed"],
                started_count=bucket["started"],
                abandoned_count=bucket["abandoned"],
                avg_duration_min=None,
                preferred_duration_min=None,
                target_duration_min=None,
            )
            mat = F.maturity(bucket["started"] + bucket["completed"], aggregated["covered_days"])
            # 负反馈是显式信号（用户明确说不感兴趣）→ 不收缩，直接生效
            if bucket["negative"]:
                interest_shrunk = round(max(0.0, interest_res["interest"]), 3)
            else:
                interest_shrunk = F.shrink(interest_res["interest"], interest_res["confidence"], mat)
            fit_shrunk = F.shrink(fit_res["fit"], fit_res["confidence"], mat)
            domains.append(DomainState(
                domain=domain,
                interest=interest_shrunk,
                behavior_fit=fit_shrunk,
                confidence=min(interest_res["confidence"], fit_res["confidence"]),
                evidence_count=bucket["started"] + bucket["completed"] + bucket["negative"],
            ))
        return domains

    def pi_learner_context(self, *, max_items: int = 8, max_tokens: int = 800) -> dict[str, Any]:
        """Compact learner context for Pi — bounded, pre-computed state."""
        state = self.build()
        context = {
            "activeGoals": [g.title for g in state.active_goals[:3]],
            "topKnowledgeGaps": [
                {"topic": k.topic, "gapScore": k.gap_score}
                for k in sorted(state.knowledge_states, key=lambda s: -s.gap_score)[:3]
                if k.state == "knowledge_gap"
            ],
            "explicitPreferences": [p.summary or p.key for p in state.preferences[:3]],
            "recentBehaviorPattern": state.behavior.to_dict() if state.behavior else {},
        }
        # bound by token estimate
        budget = max_tokens
        result: dict[str, Any] = {k: [] for k in context}
        for key, items in context.items():
            if budget <= 0:
                break
            result[key] = items
            budget -= max(20, len(str(items)) // 2)
        return result

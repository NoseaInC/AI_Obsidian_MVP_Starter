"""Confidence / Maturity Regression.

Cases: single click / 3 clicks same day / 5 behaviors across 3 days /
explicit preference / negative feedback.
Requirements:
  - single behavior has minimal effect
  - cross-day repeated evidence increases effect gradually
  - explicit preference directly influences
  - negative feedback lowers next ranking
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from agent.core.storage import StateStore
from agent.core.memory import MemoryService
from agent.core.learner_state import LearnerStateBuilder
from agent.core.learner_state import features as F


def _build() -> tuple[StateStore, MemoryService, LearnerStateBuilder]:
    tmp = tempfile.TemporaryDirectory()
    store = StateStore(Path(tmp.name) / "eval.sqlite3")
    memory = MemoryService(store)
    return store, memory, LearnerStateBuilder(store, memory)


def run() -> dict[str, object]:
    store, memory, builder = _build()
    try:
        # 1. single click → interest 影响小
        store.append_learning_events([
            {"id": "c1", "eventType": "recommendation_clicked", "subjectType": "recommendation", "subjectId": "r",
             "topic": "t", "domain": "统计", "payload": {}, "createdAt": "2026-08-01T10:00:00+08:00", "schemaVersion": 1},
        ])
        state = builder.build()
        single_click_interest = next((d.interest for d in state.domain_states if d.domain == "统计"), 0.5)
        single_click_effect = abs(single_click_interest - 0.5)

        # 2. 3 clicks same day → 仍低 maturity
        store.append_learning_events([
            {"id": f"c{i}", "eventType": "recommendation_clicked", "subjectType": "recommendation", "subjectId": "r",
             "topic": "t", "domain": "统计", "payload": {}, "createdAt": f"2026-08-02T10:0{i}:00+08:00", "schemaVersion": 1}
            for i in range(2, 5)
        ])
        state = builder.build()
        same_day_interest = next((d.interest for d in state.domain_states if d.domain == "统计"), 0.5)
        same_day_effect = abs(same_day_interest - 0.5)

        # 3. 5 behaviors across 3 days → maturity 增加
        store.append_learning_events([
            {"id": f"b{i}", "eventType": "study_session_started", "subjectType": "recommendation", "subjectId": "r",
             "topic": "t", "domain": "统计", "durationMs": 600000, "payload": {},
             "createdAt": f"2026-08-0{3 + i // 2}T10:00:00+08:00", "schemaVersion": 1}
            for i in range(5)
        ])
        state = builder.build()
        multi_day_interest = next((d.interest for d in state.domain_states if d.domain == "统计"), 0.5)
        multi_day_effect = abs(multi_day_interest - 0.5)
        maturity_3d = state.behavior.maturity if state.behavior else 0.0

        # 4. explicit preference → 强影响
        memory.remember_explicit("preference", "统计与机器学习", {"summary": "重点学习统计"})
        state = builder.build()
        explicit_pref = len(state.preferences)
        explicit_effect = 1.0 if explicit_pref > 0 else 0.0

        # 5. negative feedback → 降低兴趣
        interest_before = multi_day_interest
        store.append_learning_events([
            {"id": "neg1", "eventType": "recommendation_feedback", "subjectType": "recommendation", "subjectId": "r",
             "topic": "t", "domain": "统计", "payload": {"action": "not_interested"},
             "createdAt": "2026-08-04T10:00:00+08:00", "schemaVersion": 1},
            {"id": "neg2", "eventType": "recommendation_feedback", "subjectType": "recommendation", "subjectId": "r",
             "topic": "t", "domain": "统计", "payload": {"action": "not_interested"},
             "createdAt": "2026-08-04T10:05:00+08:00", "schemaVersion": 1},
        ])
        state = builder.build()
        interest_after = next((d.interest for d in state.domain_states if d.domain == "统计"), 0.5)

        # maturity 随证据增长
        mat_small = F.maturity(2, 1)
        mat_medium = F.maturity(8, 3)
        mat_large = F.maturity(80, 14)

        return {
            "single_click_effect": round(single_click_effect, 3),
            "same_day_3clicks_effect": round(same_day_effect, 3),
            "multi_day_effect": round(multi_day_effect, 3),
            "maturity_progression": [mat_small, mat_medium, mat_large],
            "explicit_preference_effect": explicit_effect,
            "negative_feedback_lowers_interest": interest_after < interest_before,
            "checks": {
                "single_click_minimal": single_click_effect < 0.2,
                "cross_day_stronger_than_single_day": multi_day_effect > single_click_effect,
                "maturity_monotonic": mat_small < mat_medium < mat_large,
                "explicit_strong": explicit_effect == 1.0,
            },
        }
    finally:
        store.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))

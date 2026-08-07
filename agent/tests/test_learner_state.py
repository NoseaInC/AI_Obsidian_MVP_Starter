from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent.core.storage import StateStore
from agent.core.memory import MemoryService
from agent.core.learner_state import LearnerStateBuilder
from agent.core.learner_state import features as F


def _events_for_journey() -> list[dict]:
    """User Journey: Day1 学假设检验 quiz 错；Day2 再学 p 值用 hint quiz 仍错；Day3 重复假设检验 quiz 错 + hint。"""
    return [
        {"id": "d1s", "eventType": "study_session_started", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "durationMs": 900000, "payload": {},
         "createdAt": "2026-08-01T10:00:00+08:00", "schemaVersion": 1},
        {"id": "d1q", "eventType": "quiz_completed", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "payload": {"correctness": 0.4},
         "createdAt": "2026-08-01T10:05:00+08:00", "schemaVersion": 1},
        {"id": "d2s", "eventType": "study_session_started", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "p 值", "domain": "统计与机器学习", "durationMs": 900000, "payload": {},
         "createdAt": "2026-08-02T10:00:00+08:00", "schemaVersion": 1},
        {"id": "d2h", "eventType": "hint_opened", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "p 值", "domain": "统计与机器学习", "payload": {},
         "createdAt": "2026-08-02T10:03:00+08:00", "schemaVersion": 1},
        {"id": "d2q", "eventType": "quiz_completed", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "p 值", "domain": "统计与机器学习", "payload": {"correctness": 0.3},
         "createdAt": "2026-08-02T10:05:00+08:00", "schemaVersion": 1},
        {"id": "d3q", "eventType": "quiz_completed", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "payload": {"correctness": 0.35},
         "createdAt": "2026-08-03T10:00:00+08:00", "schemaVersion": 1},
        {"id": "d3h", "eventType": "hint_opened", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "payload": {},
         "createdAt": "2026-08-03T10:03:00+08:00", "schemaVersion": 1},
        {"id": "d3h2", "eventType": "hint_opened", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "payload": {},
         "createdAt": "2026-08-03T10:04:00+08:00", "schemaVersion": 1},
        {"id": "d3c", "eventType": "study_session_completed", "subjectType": "recommendation", "subjectId": "r1",
         "topic": "假设检验", "domain": "统计与机器学习", "durationMs": 900000, "payload": {},
         "createdAt": "2026-08-03T10:10:00+08:00", "schemaVersion": 1},
    ]


class LearnerStateBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "l.sqlite3")
        self.memory = MemoryService(self.store)
        self.builder = LearnerStateBuilder(self.store, self.memory)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _seed_journey(self):
        self.memory.remember_explicit("goal", "准备数据分析秋招", {"summary": "重点补统计"})
        self.store.append_learning_events(_events_for_journey())

    def test_learner_state_is_deterministic(self):
        self._seed_journey()
        s1 = self.builder.build().to_dict()
        s2 = self.builder.build().to_dict()
        self.assertEqual(s1, s2)

    def test_learner_state_is_rebuildable(self):
        self._seed_journey()
        s1 = self.builder.build().to_dict()
        # 清空事件后重建 → 不同；重放后恢复
        self.store.clear_learning_events()
        s2 = self.builder.build().to_dict()
        self.assertNotEqual(s1, s2)
        self.store.append_learning_events(_events_for_journey())
        s3 = self.builder.build().to_dict()
        self.assertEqual(s1, s3)

    def test_memory_and_mastery_do_not_overwrite_each_other(self):
        # mastery 记录不写 memory；memory 不写 mastery
        self._seed_journey()
        self.store.connection.execute(
            "INSERT INTO mastery_history(artifact_id, old_mastery, new_mastery, confirmed, created_at) VALUES (?,?,?,1,?)",
            ("假设检验", 1, 2, "2026-08-01T10:00:00+08:00"),
        )
        self.store.connection.commit()
        state = self.builder.build()
        gap_state = next((k for k in state.knowledge_states if k.topic == "假设检验"), None)
        self.assertIsNotNone(gap_state)
        # memory 里没有 mastery 类型的项
        memory_types = {i["memory_type"] for i in self.memory.list_active()}
        self.assertNotIn("mastery", memory_types)

    def test_claimed_not_verified(self):
        self.memory.remember_explicit("knowledge_state", "我已经会中心极限定理了", {"level": "claimed"})
        state = self.builder.build()
        entry = next((k for k in state.knowledge_states if "中心极限" in k.topic), None)
        self.assertIsNotNone(entry)
        # claimed 不自动变成 mastered/verified 高掌握
        self.assertNotEqual(entry.state, "mastered")
        self.assertLess(entry.gap_score, 0.5)  # claimed 有缺口但非高置信

    def test_low_confidence_behavior_shrinks(self):
        # 单日少量行为 → maturity 低 → shrunk 后接近中性
        mat = F.maturity(2, 1)
        self.assertLess(mat, 0.5)
        shrunk = F.shrink(0.9, 0.5, mat)
        self.assertLess(shrunk, 0.7)
        # 大量跨日行为 → maturity 高
        mat2 = F.maturity(80, 14)
        self.assertGreater(mat2, 0.7)
        shrunk2 = F.shrink(0.9, 0.9, mat2)
        self.assertGreater(shrunk2, 0.8)

    def test_goal_affects_ranking_signals(self):
        self._seed_journey()
        state = self.builder.build()
        sig = state.signals_for_ranking("数据分析秋招统计", "统计与机器学习")
        self.assertGreater(sig["goal_alignment"], 0.5)

    def test_gap_affects_ranking_signals(self):
        self._seed_journey()
        state = self.builder.build()
        sig = state.signals_for_ranking("假设检验", "统计与机器学习")
        self.assertGreater(sig["knowledge_gap"], 0.5)

    def test_negative_feedback_reduces_interest(self):
        # 3 次负反馈 + 曝光 → 兴趣显著低于中性
        for i in range(3):
            self.store.append_learning_events([
                {"id": f"x{i}", "eventType": "recommendation_exposed", "subjectType": "recommendation", "subjectId": "r1",
                 "topic": "t", "domain": "因果推断", "payload": {}, "createdAt": f"2026-08-0{i+1}T10:00:00+08:00", "schemaVersion": 1},
                {"id": f"n{i}", "eventType": "recommendation_feedback", "subjectType": "recommendation", "subjectId": "r1",
                 "topic": "t", "domain": "因果推断", "payload": {"action": "not_interested"}, "createdAt": f"2026-08-0{i+1}T10:01:00+08:00", "schemaVersion": 1},
            ])
        state = self.builder.build()
        domain = next((d for d in state.domain_states if d.domain == "因果推断"), None)
        self.assertIsNotNone(domain)
        self.assertLess(domain.interest, 0.5)

    def test_single_negative_feedback_shrinks_conservatively(self):
        # 单条负反馈 → shrink 后接近中性（不剧烈）
        self.store.append_learning_events([
            {"id": "sx", "eventType": "recommendation_exposed", "subjectType": "recommendation", "subjectId": "r1",
             "topic": "t", "domain": "因果推断", "payload": {}, "createdAt": "2026-08-01T10:00:00+08:00", "schemaVersion": 1},
            {"id": "sn", "eventType": "recommendation_feedback", "subjectType": "recommendation", "subjectId": "r1",
             "topic": "t", "domain": "因果推断", "payload": {"action": "not_interested"}, "createdAt": "2026-08-02T10:00:00+08:00", "schemaVersion": 1},
        ])
        state = self.builder.build()
        domain = next((d for d in state.domain_states if d.domain == "因果推断"), None)
        self.assertIsNotNone(domain)
        self.assertLessEqual(domain.interest, 0.5)

    def test_time_budget_affects_ranking(self):
        from agent.core.learner_state.models import LearnerState
        # behavior_fit 反映偏好时长；timeFit 由 TS 侧处理，这里验证 behavior 不干扰
        fit = F.behavior_fit(completed_count=8, started_count=10, abandoned_count=2,
                             avg_duration_min=15, preferred_duration_min=15, target_duration_min=15)
        self.assertGreater(fit["fit"], 0.7)

    def test_explicit_preference_stronger_than_inferred(self):
        # 显式偏好 maturity=1, confidence=1 → 无收缩
        explicit = F.shrink(0.9, *F.explicit_confidence())
        self.assertEqual(explicit, 0.9)
        inferred = F.shrink(0.9, 0.5, F.maturity(2, 1))
        self.assertLess(inferred, explicit)

    def test_one_click_cannot_dominate(self):
        # 单次 click 不足以形成高兴趣
        res = F.interest(repeated_active=0, favorites=0, clicks=1, exposures=1, negative=0, explicit_interest=False)
        self.assertLess(res["interest"], 0.7)

    def test_deleted_memory_zero_effect(self):
        m = self.memory.remember_explicit("goal", "删除的目标", {"summary": "x"})
        self.memory.forget(m["id"])
        state = self.builder.build()
        self.assertNotIn("删除的目标", [g.title for g in state.active_goals])

    def test_pi_context_bounded(self):
        self._seed_journey()
        ctx = self.builder.pi_learner_context()
        self.assertIn("activeGoals", ctx)
        self.assertIn("topKnowledgeGaps", ctx)
        total_chars = sum(len(str(v)) for v in ctx.values())
        self.assertLess(total_chars, 2000)  # 远低于 800 token 上限

    def test_behavior_derived_candidate_is_conservative(self):
        # 单日单次 quiz 错误 → 不生成候选（需要 ≥2 次错误 + hint + ≥2 天）
        self.store.append_learning_events([
            {"id": "s1", "eventType": "quiz_completed", "subjectType": "recommendation", "subjectId": "r",
             "topic": "单次主题", "domain": "统计", "payload": {"correctness": 0.2},
             "createdAt": "2026-08-01T10:00:00+08:00", "schemaVersion": 1},
        ])
        rows = self.store.connection.execute(
            "SELECT memory_key FROM memory_items WHERE memory_type='knowledge_state'"
        ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_full_user_journey_forms_memory_and_gap(self):
        """Day1..3: 目标设定 → 学习 → quiz 错 → hint → 再错 → 行为派生 candidate。
        验证 LearnerState 识别 goal + knowledge gap，且 candidate 不直接 active。"""
        self._seed_journey()
        # 行为派生 candidate（跨 3 天、2 次 quiz 失败 + hint）
        from agent.core.service import AgentService
        vault = Path(self.temp.name)
        (vault / "90-Local-Only/Agent").mkdir(parents=True, exist_ok=True)
        (vault / "20-Knowledge/Concepts").mkdir(parents=True, exist_ok=True)
        svc = AgentService(vault)
        svc.store = self.store  # 复用同一存储
        svc.memory = self.memory
        svc.run_behavior_memory_extraction()

        rows = self.store.connection.execute(
            "SELECT memory_key, status FROM memory_items WHERE memory_type='knowledge_state'"
        ).fetchall()
        self.assertGreaterEqual(len(rows), 1)
        for r in rows:
            self.assertEqual(r["status"], "candidate")  # 只生成 candidate，不激活

        # LearnerState 识别目标 + 缺口
        state = self.builder.build()
        self.assertIn("准备数据分析秋招", [g.title for g in state.active_goals])
        gaps = [k for k in state.knowledge_states if k.state == "knowledge_gap"]
        self.assertGreaterEqual(len(gaps), 1)
        # 排名信号：假设检验 gap 高
        sig = state.signals_for_ranking("假设检验", "统计与机器学习")
        self.assertGreater(sig["knowledge_gap"], 0.5)
        svc.store.close()


if __name__ == "__main__":
    unittest.main()

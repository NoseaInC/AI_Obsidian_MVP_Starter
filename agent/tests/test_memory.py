from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.storage import StateStore
from agent.core.memory import MemoryService, extract_candidates


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "memory.sqlite3")
        self.svc = MemoryService(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_explicit_memory_is_active_and_searchable_across_sessions(self):
        m = self.svc.remember_explicit(
            "preference", "技术问题先讲核心思想",
            {"summary": "先核心思想再数学推导"},
            evidence_id="msg-1",
        )
        self.assertEqual(m["status"], "active")
        self.assertEqual(m["confidence"], 1.0)
        self.assertEqual(m["source_type"], "explicit_user")
        # 新 service 实例（模拟新会话）仍能召回
        svc2 = MemoryService(self.store)
        hits = svc2.search("核心思想", memory_types=["preference"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["memory_key"], "技术问题先讲核心思想")

    def test_single_observation_does_not_activate_long_term_preference(self):
        c = self.svc.create_candidate("preference", "喜欢简洁回答", {"summary": "x"},
                                      evidence=[("message", "e1")])
        self.assertEqual(c["status"], "candidate")
        with self.assertRaises(ValueError):
            self.svc.activate_candidate(c["id"])

    def test_candidate_promotes_only_when_rules_met(self):
        c = self.svc.create_candidate(
            "goal", "持续学习统计", {"summary": "x"},
            evidence=[("message", "a1"), ("message", "a2"), ("learning_event", "a3")],
        )
        # 3 条证据散布到 3 个不同日期
        dates = ["2026-07-30T00:00:00+08:00", "2026-07-31T00:00:00+08:00", "2026-08-01T00:00:00+08:00"]
        rows = self.store.connection.execute(
            "SELECT id FROM memory_evidence WHERE memory_id=?", (c["id"],)
        ).fetchall()
        for i, row in enumerate(rows):
            self.store.connection.execute(
                "UPDATE memory_evidence SET created_at=? WHERE id=?", (dates[i], row["id"])
            )
        self.store.connection.commit()
        activated = self.svc.activate_candidate(c["id"])
        self.assertEqual(activated["status"], "active")
        self.assertEqual(activated["source_type"], "repeated_observed")

    def test_conflict_supersedes_not_overwrites(self):
        first = self.svc.remember_explicit("preference", "回答要详细", {"summary": "v1"})
        second = self.svc.remember_explicit("preference", "回答要详细", {"summary": "v2 更新"})
        self.assertEqual(self.svc.get(first["id"])["status"], "superseded")
        self.assertEqual(second["status"], "active")
        self.assertEqual(second["supersedes_id"], first["id"])
        # 只有一条 active
        active = [i for i in self.svc.list_active("preference") if i["status"] == "active"]
        self.assertEqual(len(active), 1)

    def test_forget_removes_from_search_and_context(self):
        m = self.svc.remember_explicit("preference", "偏好A", {"summary": "x"})
        self.svc.forget(m["id"])
        self.assertEqual(len(self.svc.search("偏好A")), 0)
        ctx = self.svc.build_pi_context("偏好A")
        total = sum(len(v) for v in ctx.values())
        self.assertEqual(total, 0)

    def test_claimed_never_auto_verified(self):
        c = self.svc.create_candidate(
            "knowledge_state", "我掌握了贝叶斯", {"level": "claimed"},
            evidence=[("message", "k1"), ("quiz", "k2"), ("learning_event", "k3")],
        )
        self.assertEqual(c["evidence_level"], "claimed")
        self.assertEqual(c["status"], "candidate")
        # claimed 不自动提升 verified；只有满足晋升规则才 active（仍是 observed）
        dates = ["2026-07-30T00:00:00+08:00", "2026-07-31T00:00:00+08:00", "2026-08-01T00:00:00+08:00"]
        rows = self.store.connection.execute(
            "SELECT id FROM memory_evidence WHERE memory_id=?", (c["id"],)
        ).fetchall()
        for i, row in enumerate(rows):
            self.store.connection.execute(
                "UPDATE memory_evidence SET created_at=? WHERE id=?", (dates[i], row["id"])
            )
        self.store.connection.commit()
        activated = self.svc.activate_candidate(c["id"])
        self.assertEqual(activated["evidence_level"], "observed")
        self.assertNotEqual(activated["evidence_level"], "verified")

    def test_pi_context_bounded(self):
        for i in range(20):
            self.svc.remember_explicit("goal", f"目标{i}", {"summary": f"goal {i}"})
        ctx = self.svc.build_pi_context("目标", max_items=8, max_tokens=800)
        total = sum(len(v) for v in ctx.values())
        self.assertLessEqual(total, 8)

    def test_deleted_memory_never_enters_pi_context(self):
        m = self.svc.remember_explicit("project_decision", "统一使用 Pi", {"summary": "x"})
        ctx = self.svc.build_pi_context("Pi")
        self.assertEqual(len(ctx["projectDecisions"]), 1)
        self.svc.forget(m["id"])
        ctx2 = self.svc.build_pi_context("Pi")
        self.assertEqual(len(ctx2["projectDecisions"]), 0)


class MemoryExtractorTests(unittest.TestCase):
    def test_extracts_explicit_goal(self):
        candidates = extract_candidates("我的长期目标是掌握统计推断")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["memory_type"], "goal")
        self.assertTrue(candidates[0]["explicit"])

    def test_extracts_explicit_preference(self):
        candidates = extract_candidates("记住，技术问题先讲核心思想，再讲数学推导")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["memory_type"], "preference")
        self.assertIn("核心思想", candidates[0]["memory_key"])

    def test_non_explicit_message_yields_no_candidates(self):
        self.assertEqual(extract_candidates("今天天气不错"), [])


if __name__ == "__main__":
    unittest.main()

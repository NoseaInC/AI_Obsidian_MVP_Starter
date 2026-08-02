from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from datetime import datetime

from agent.core import learning
from agent.core import recommendations
from agent.core.storage import StateStore
from agent.core.service import AgentService


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True); (self.vault / "20-Knowledge/Topics").mkdir(parents=True)
        (self.vault / "30-Learning/Weekly").mkdir(parents=True)

    def tearDown(self): self.temp.cleanup()

    def note(self, name: str, *, status="reviewed", domain="统计与机器学习", mastery=1, importance=3, next_review="2026-07-12") -> Path:
        path = self.vault / "20-Knowledge/Concepts" / f"{name}.md"
        path.write_text(f'''---
type: concept
status: "{status}"
domain: "{domain}"
mastery: {mastery}
importance: {importance}
next_review: "{next_review}"
artifact_id: "manual:{name}"
---
# {name}
人工正文。
''', encoding="utf-8"); return path

    def test_reviewed_only_due_sorting_and_future(self):
        self.note("overdue", next_review="2026-07-10", mastery=2)
        self.note("today", next_review="2026-07-12", mastery=1)
        self.note("future", next_review="2026-07-14")
        self.note("draft", status="ai-draft")
        items = learning.scan_reviewed(self.vault)
        self.assertEqual([item.title for item in learning.due_reviews(items, date(2026, 7, 12))], ["overdue", "today"])
        self.assertEqual([item.title for item in learning.future_reviews(items, date(2026, 7, 12))], ["future"])
        self.assertNotIn("draft", [item.title for item in items])

    def test_weighting_is_70_30_when_supply_exists(self):
        for index in range(8): self.note(f"main-{index}")
        for index in range(5): self.note(f"side-{index}", domain="LLM 与 Agent")
        chosen = learning.weighted_learning(learning.scan_reviewed(self.vault), 10)
        self.assertEqual(sum(learning.branch(item) == "mainline" for item in chosen), 7)
        self.assertEqual(sum(learning.branch(item) == "llm-agent" for item in chosen), 3)

    def test_intervals_mastery_suggestion_and_confirmed_write(self):
        path = self.note("ATE", mastery=2)
        self.assertEqual(learning.suggest_mastery(2, .95), 3)
        self.assertEqual(learning.suggest_mastery(3, .9, critical_error=True), 2)
        store = StateStore(self.vault / "90-Local-Only/state.sqlite3")
        learning.confirm_mastery(self.vault, store, path, 3, ["识别与估计"], date(2026, 7, 12))
        text = path.read_text(encoding="utf-8")
        self.assertIn('next_review: "2026-07-20"', text)
        self.assertIn("人工正文。", text)
        count = store.connection.execute("SELECT COUNT(*) FROM mastery_history WHERE confirmed=1").fetchone()[0]
        self.assertEqual(count, 1); store.close()

    def test_unfinished_reschedule_deduplicates(self):
        tasks = [{"artifact_id": "a", "state": "unfinished", "due_date": "2026-07-10"}, {"artifact_id": "a", "state": "queued", "due_date": "2026-07-11"}]
        result = learning.reschedule_unfinished(tasks, date(2026, 7, 12))
        self.assertEqual(len(result), 1); self.assertEqual(result[0]["due_date"], "2026-07-12")

    def test_weekly_plan_requires_confirmation(self):
        for index in range(3): self.note(f"k-{index}")
        path = learning.create_weekly_plan(self.vault, date(2026, 7, 13))
        self.assertIn('status: "proposed"', path.read_text(encoding="utf-8"))
        learning.confirm_weekly_plan(self.vault, path)
        self.assertIn('status: "confirmed"', path.read_text(encoding="utf-8"))

    def test_plan_task_patch_is_bounded_and_persisted(self):
        service = AgentService(self.vault)
        service.store.save_plan_proposal({
            "id": "plan-bounded", "title": "可调整计划", "state": "proposed",
            "tasks": [{"id": "task-one", "title": "复习倾向得分", "minutes": 15, "state": "proposed"}],
        })
        updated = service.patch_plan_task("task-one", {"date": "2026-07-15", "minutes": 20, "state": "queued", "secret": "ignored"})
        self.assertEqual(updated["minutes"], 20)
        self.assertEqual(updated["date"], "2026-07-15")
        self.assertNotIn("secret", updated)
        current = service.current_plan()["proposals"][0]["tasks"][0]
        self.assertEqual(current["state"], "queued")
        with self.assertRaisesRegex(ValueError, "plan_task_patch_required"):
            service.patch_plan_task("task-one", {"secret": "ignored"})
        service.store.close()

    def test_quiz_shape(self):
        self.note("propensity")
        item = learning.scan_reviewed(self.vault)[0]
        self.assertEqual(len(learning.quiz(item, weekend=False)), 3)
        self.assertIn("迁移应用", learning.quiz(item, weekend=True)[2])

    def test_deterministic_recommendations_are_reviewed_only_and_explain_score(self):
        self.note("到期主线", next_review="2026-07-10", mastery=1, importance=5)
        self.note("支线", domain="LLM 与 Agent", next_review="2026-07-12", mastery=2)
        self.note("草稿", status="ai-draft")
        store = StateStore(self.vault / "90-Local-Only/recommendations.sqlite3")
        result = recommendations.build(self.vault, store, [], date(2026, 7, 12))
        self.assertEqual([item["title"] for item in result], ["到期主线", "支线"])
        self.assertGreater(result[0]["score"], result[1]["score"])
        self.assertEqual(result[0]["kind"], "review")
        self.assertTrue(result[0]["reasonDetails"])
        self.assertNotIn("草稿", [item["title"] for item in result])
        store.close()

    def test_feedback_changes_recommendations_without_markdown_write(self):
        today = date.today()
        path = self.note("反馈测试", next_review=today.isoformat())
        before = path.read_text(encoding="utf-8")
        store = StateStore(self.vault / "90-Local-Only/feedback.sqlite3")
        item = recommendations.build(self.vault, store, [], today)[0]
        recommendations.record_action(store, item["id"], "later")
        self.assertEqual(recommendations.build(self.vault, store, [], today), [])
        self.assertTrue(store.undo_recommendation_feedback(item["id"]))
        self.assertEqual(len(recommendations.build(self.vault, store, [], today)), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        store.close()

    def test_too_hard_prioritizes_prerequisites_and_weekend_is_persisted(self):
        self.note("困难知识", next_review="2026-07-12", mastery=2)
        store = StateStore(self.vault / "90-Local-Only/hard.sqlite3")
        first = recommendations.build(self.vault, store, [], date(2026, 7, 12))[0]
        recommendations.record_action(store, first["id"], "too_hard")
        recommendations.record_action(store, first["id"], "weekend")
        second = recommendations.build(self.vault, store, [], date(2026, 7, 12))[0]
        self.assertGreater(second["score"], first["score"])
        self.assertIn("weekend", [row["action"] for row in store.recommendation_feedback()])
        store.close()

    def test_dashboard_and_study_completion_require_mastery_confirmation(self):
        path = self.note("学习会话", next_review=date.today().isoformat(), mastery=1)
        before = path.read_text(encoding="utf-8")
        service = AgentService(self.vault)
        dashboard = service.dashboard()
        self.assertEqual(dashboard["summary"]["review_count"], 1)
        recommendation = dashboard["recommendations"][0]
        session = service.start_study_session(recommendation["id"])
        completed = service.complete_study_session(session["session_id"], .8, "能够解释定义")
        self.assertTrue(completed["requires_confirmation"])
        self.assertEqual(completed["suggested_mastery"], 2)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        service.store.close()

    def test_study_workspace_resumes_progress_and_completion_is_idempotent(self):
        self.note("可恢复课程", next_review=date.today().isoformat(), mastery=1)
        service = AgentService(self.vault)
        recommendation = service.dashboard()["recommendations"][0]
        started = service.start_study_session(recommendation["id"])
        self.assertEqual(len(started["lesson"]["sections"]), 5)
        paused = service.update_study_session(started["session_id"], "pause", {
            "progress": {"sectionIndex": 2, "completedSectionIds": ["why", "definition"], "quizAnswers": {"checkpoint": 1}},
        })
        self.assertEqual(paused["state"], "paused")
        resumed = service.start_study_session(recommendation["id"])
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["session_id"], started["session_id"])
        self.assertEqual(resumed["progress"]["sectionIndex"], 2)
        first = service.complete_study_session(started["session_id"], 1, "能够说明边界")
        second = service.complete_study_session(started["session_id"], 1, "重复提交")
        self.assertEqual(first["state"], "completed")
        self.assertEqual(second["state"], "completed")
        feedback = [row for row in service.store.recommendation_feedback() if row["recommendation_id"] == recommendation["id"]]
        self.assertEqual(sum(row["action"] == "completed" for row in feedback), 1)
        reopened = service.update_study_session(started["session_id"], "undo_complete", {"progress": {}})
        self.assertEqual(reopened["state"], "active")
        self.assertEqual(service.get_recommendation(recommendation["id"])["title"], "可恢复课程")
        service.store.close()

    def test_completed_daily_task_stays_visible_and_updates_time_summary(self):
        self.note("今日保留", next_review=date.today().isoformat(), mastery=1)
        service = AgentService(self.vault)
        initial = service.daily_dashboard()
        task = initial["todayPlan"]["items"][0]
        session = service.start_study_session(task["recommendationId"])
        service.complete_study_session(session["session_id"], 1, "完成")
        refreshed = service.daily_dashboard()
        completed = next(item for item in refreshed["todayPlan"]["items"] if item["recommendationId"] == task["recommendationId"])
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["recommendation"]["title"], "今日保留")
        self.assertEqual(refreshed["summary"]["completed_minutes"], completed["minutes"])
        service.store.close()

    def test_curriculum_candidate_is_explained_and_moves_to_proposed_plan_without_note_write(self):
        service = AgentService(self.vault)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        service.store.upsert_curriculum_candidate({
            "candidate_id": "candidate-bridge", "title": "桥梁概念", "canonical_title": "桥梁概念",
            "kind": "bridge", "domain": "统计与机器学习", "route": "mainline",
            "prerequisites": ["基础概念"], "related_topics": ["目标主题"], "why_now": "补齐当前薄弱点",
            "learning_outcomes": ["能够解释"], "estimated_minutes": 12, "difficulty": "medium",
            "scores": {"mainline": .9, "gap": .9, "reuse": .8}, "confidence": .8,
            "basis": {"type": "model_curriculum", "sourceBasis": [{"type": "local_knowledge", "title": "基础概念"}], "behaviorBasis": []},
            "status": "active", "model_profile_id": "fake-model-profile", "generated_at": now,
            "verification": {"schemaVersion": 1, "grade": "B", "score": .78, "claims": [], "verifiedAt": now}, "schema_version": 1,
        })
        before = list(self.vault.rglob("*.md"))
        item = service.get_recommendation("candidate-bridge")
        self.assertTrue(item["candidate"]); self.assertIn("尚未成为正式笔记", item["reasonDetails"])
        self.assertNotIn(item["id"], [entry["recommendationId"] for entry in service.build_today(force=True)["items"]])
        service.recommendation_action(item["id"], "tomorrow", {})
        self.assertEqual(service.list_curriculum_candidates("planned")["items"][0]["status"], "planned")
        self.assertEqual(service.current_plan()["tomorrow"][0]["title"], "桥梁概念")
        self.assertEqual(before, list(self.vault.rglob("*.md")))
        service.store.close()

    def test_learning_events_are_idempotent_private_and_build_cold_start_profile(self):
        service = AgentService(self.vault)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        event = {
            "id": "learning-event-1", "eventType": "recommendation_exposed",
            "subjectType": "recommendation", "subjectId": "rec-1", "topic": "倾向得分",
            "domain": "因果推断", "recommendationId": "rec-1", "payload": {"category": "review"},
            "createdAt": now, "schemaVersion": 1,
        }
        first = service.record_learning_events({"events": [event], "schemaVersion": 1})
        duplicate = service.record_learning_events({"events": [event], "schemaVersion": 1})
        self.assertEqual(first["inserted"], 1); self.assertEqual(duplicate["duplicates"], 1)
        profile = service.learner_profile(rebuild=True)
        self.assertEqual(profile["eventCount"], 1)
        self.assertLessEqual(profile["behaviorWeight"], .05)
        self.assertTrue(any(item["key"] == "recommendation_click_rate" for item in profile["features"]))
        with self.assertRaisesRegex(ValueError, "private_or_unsupported"):
            service.record_learning_events({"events": [{**event, "id": "learning-event-2", "payload": {"fullNote": "private"}}], "schemaVersion": 1})
        service.store.close()

    def test_daily_dashboard_exposes_runtime_boundary_without_model_call(self):
        self.note("本地复习", next_review=date.today().isoformat())
        service = AgentService(self.vault)
        dashboard = service.daily_dashboard()
        self.assertEqual(dashboard["schemaVersion"], 1)
        self.assertEqual(dashboard["runtime"]["ranking"], "typescript-domain-v1")
        self.assertEqual(dashboard["runtime"]["persistence"], "python-sqlite-adapter-v1")
        self.assertEqual(dashboard["recommendations"][0]["title"], "本地复习")
        service.store.close()

    def test_conversation_signals_generate_three_explainable_learning_directions(self):
        service = AgentService(self.vault)
        result = service.explicit.submit_intake({"message": "我还是不理解倾向得分为什么可以用于匹配，想继续深入学习"}, "direction-signal")
        directions = result["intelligence"]["directions"]
        self.assertGreaterEqual(len(directions), 3)
        self.assertEqual({item["horizon"] for item in directions}, {"near", "route", "exploration"})
        self.assertTrue(all(item["why"] for item in directions))
        self.assertTrue(all(item["confidenceLabel"] in {"高置信", "来源有限"} for item in directions))
        self.assertTrue(any(item.get("direction") for item in service.list_recommendations()))
        service.store.close()

    def test_agent_adjusts_today_to_time_budget_and_undo_is_version_checked(self):
        for index in range(4):
            self.note(f"今日候选-{index}", next_review=date.today().isoformat())
        service = AgentService(self.vault)
        initial = service.build_today(30, force=True)
        self.assertLessEqual(initial["totalMinutes"], 30)
        adjusted = service.adjust_today({"type": "set_budget", "available_minutes": 15, "reason": "今天只有 15 分钟"})
        self.assertLessEqual(adjusted["afterMinutes"], 15)
        self.assertTrue(adjusted["undoAvailable"])
        restored = service.undo_today_adjustment(adjusted["actionId"])
        self.assertEqual(restored["state"], "undone")
        self.assertEqual(restored["plan"]["budgetMinutes"], 30)
        with self.assertRaisesRegex(ValueError, "not_undoable"):
            service.undo_today_adjustment(adjusted["actionId"])
        service.store.close()

    def test_daily_dashboard_exposes_latest_undoable_adjustment(self):
        for index in range(3):
            self.note(f"调整候选-{index}", next_review=date.today().isoformat())
        service = AgentService(self.vault)
        service.build_today(25, force=True)
        adjusted = service.adjust_today({"type": "set_budget", "available_minutes": 15})
        dashboard = service.daily_dashboard()
        self.assertEqual(dashboard["recentAdjustment"]["actionId"], adjusted["actionId"])
        service.undo_today_adjustment(adjusted["actionId"])
        self.assertIsNone(service.daily_dashboard()["recentAdjustment"])
        service.store.close()

    def test_assistant_time_and_no_formula_constraint_rebuilds_today_and_undo_restores_constraint(self):
        self.note("矩阵公式推导", next_review=date.today().isoformat())
        self.note("回归直觉", next_review=date.today().isoformat())
        service = AgentService(self.vault)
        result = service.explicit.submit_intake({"message": "今天只有 15 分钟，不想看公式。"}, "today-no-formula")
        self.assertIsNotNone(result["dailyAdjustment"])
        plan = result["dailyAdjustment"]["plan"]
        self.assertLessEqual(plan["totalMinutes"], 15)
        self.assertFalse(any("公式" in item["recommendation"].get("title", "") for item in plan["items"] if not item["fixed"]))
        self.assertTrue(service.daily_dashboard()["todayConstraints"]["noFormula"])
        service.undo_today_adjustment(result["dailyAdjustment"]["actionId"])
        self.assertEqual(service.store.get_setting("today_constraints", {}), {})
        service.store.close()

    def test_fixed_today_task_cannot_be_removed_by_agent(self):
        self.note("固定复习", next_review=date.today().isoformat())
        service = AgentService(self.vault)
        plan = service.build_today(20, force=True)
        fixed = [{**item, "fixed": True} for item in plan["items"]]
        plan = service.store.replace_daily_plan(plan["date"], plan["budgetMinutes"], fixed, expected_version=plan["version"])
        with self.assertRaisesRegex(ValueError, "fixed_task_cannot_be_removed"):
            service.adjust_today({"type": "remove", "target_id": plan["items"][0]["recommendationId"]})
        service.store.close()

    def test_course_chapter_review_unit_false_excluded_from_review(self):
        # 课程章节：review_unit=false，即使 status=reviewed 也不进复习
        path = self.vault / "20-Knowledge/Concepts" / "课程章节笔记.md"
        path.write_text('''---
type: course-chapter
status: reviewed
review_unit: false
domain: 统计与机器学习
mastery: 1
importance: 4
next_review: "2026-07-12"
---
# 课程章节笔记
长章节正文，不作为复习单元。
''', encoding="utf-8")
        # 正常概念：review_unit=true + reviewed → 可复习
        self.note("可复习概念", next_review="2026-07-12")
        items = learning.scan_reviewed(self.vault)
        titles = [item.title for item in items]
        self.assertNotIn("课程章节笔记", titles)
        self.assertIn("可复习概念", titles)

    def test_concept_review_unit_true_reviewed_enters_review(self):
        self.note("概念A", next_review="2026-07-12")
        items = learning.scan_reviewed(self.vault)
        due = learning.due_reviews(items, date(2026, 7, 12))
        self.assertIn("概念A", [item.title for item in due])

    def test_topic_review_unit_true_core_enters_review(self):
        path = self.vault / "20-Knowledge/Topics" / "主线主题.md"
        path.write_text('''---
type: topic
status: core
review_unit: true
domain: 统计与机器学习
mastery: 0
importance: 4
next_review: "2026-07-12"
---
# 主线主题
''', encoding="utf-8")
        items = learning.scan_reviewed(self.vault)
        self.assertIn("主线主题", [item.title for item in items])

    def test_ai_draft_never_enters_formal_review(self):
        self.note("草稿概念", status="ai-draft", next_review="2026-07-12")
        items = learning.scan_reviewed(self.vault)
        self.assertNotIn("草稿概念", [item.title for item in items])


if __name__ == "__main__": unittest.main()

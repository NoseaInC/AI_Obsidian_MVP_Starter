from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent.core import learning
from agent.core.storage import StateStore


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

    def test_quiz_shape(self):
        self.note("propensity")
        item = learning.scan_reviewed(self.vault)[0]
        self.assertEqual(len(learning.quiz(item, weekend=False)), 3)
        self.assertIn("迁移应用", learning.quiz(item, weekend=True)[2])


if __name__ == "__main__": unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain import BrainOrchestrator, BrainRequest
from agent.core.storage import StateStore
from agent.skills import build_skill_registry
from agent.tools import build_tool_registry
from agent.tools.source_search import ArxivResearchProvider, CrossrefResearchProvider, StaticResearchProvider, search_sources


class BrainSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"
        for folder in ("01-Inbox/Ideas", "01-Inbox/Notes", "20-Knowledge/Topics", "20-Knowledge/Concepts", "10-Sources/Papers"):
            (self.vault / folder).mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/state.sqlite3")

    def tearDown(self): self.store.close(); self.temp.cleanup()

    def brain(self, providers=None):
        tools = build_tool_registry(self.vault, self.store, research_providers=providers)
        return BrainOrchestrator(self.vault, self.store, build_skill_registry(self.vault, self.store, tools))

    def test_capture_preserves_original_and_applies_only_after_confirmation(self):
        original = "灵感第一行\n第二行必须原样保留。"
        run = self.brain().submit(BrainRequest(text=original, mode="capture"))
        result = run["result"]["results"][0]
        self.assertEqual(result["original_text"], original)
        self.assertIn(original, result["proposed_notes"][0]["content"])
        change_set = result["change_set"]
        from agent.tools.change_set import ChangeSetTools
        manager = ChangeSetTools(self.vault, self.store)
        with self.assertRaises(PermissionError): manager.apply({"change_set_id": change_set["id"], "confirmed": False})
        applied = manager.apply({"change_set_id": change_set["id"], "confirmed": True})
        target = self.vault / result["suggested_path"]
        self.assertTrue(target.is_file()); self.assertIn(original, target.read_text(encoding="utf-8"))
        self.assertTrue(manager.apply({"change_set_id": change_set["id"], "confirmed": True})["idempotent"])

    def test_capture_detects_duplicate_and_normalizes_title(self):
        (self.vault / "01-Inbox/Notes/非法 标题.md").write_text("已有", encoding="utf-8")
        run = self.brain().submit(BrainRequest(text="非法/标题", mode="capture"))
        result = run["result"]["results"][0]
        self.assertNotIn("/", result["suggested_title"])
        self.assertTrue(result["suggested_path"].endswith(".md"))

    def test_protected_note_and_path_traversal_are_rejected(self):
        protected = self.vault / "20-Knowledge/Concepts/formal.md"
        protected.write_text("---\nstatus: reviewed\n---\n# formal\n", encoding="utf-8")
        tools = build_tool_registry(self.vault, self.store)
        run_id = self.store.create_job("quiz", {})  # not a brain FK; create a real brain run below
        brain_run = self.brain().submit(BrainRequest(text="只读解释"))
        with self.assertRaises(ValueError):
            tools.call("create_change_set", {"run_id": brain_run["id"], "writes": [{"path": "20-Knowledge/Concepts/formal.md", "content": "overwrite"}]}, run_id=brain_run["id"])
        with self.assertRaises(ValueError):
            tools.call("create_change_set", {"run_id": brain_run["id"], "writes": [{"path": "../escape.md", "content": "x"}]}, run_id=brain_run["id"])

    def test_bare_write_command_can_never_become_a_note(self):
        tools = build_tool_registry(self.vault, self.store)
        brain_run = self.brain().submit(BrainRequest(text="只读解释"))
        mistaken_note = (
            "---\ntype: note\nstatus: inbox\n---\n\n# 写入\n\n"
            "## 原始内容\n\n写入\n\n## Agent 整理\n\n- 类型：note\n"
        )
        with self.assertRaisesRegex(ValueError, "ambiguous_write_command_content"):
            tools.call(
                "create_change_set",
                {"run_id": brain_run["id"], "writes": [{"path": "01-Inbox/写入.md", "content": mistaken_note}]},
                run_id=brain_run["id"],
            )

    def test_research_deduplicates_and_labels_static_sources(self):
        item = {"id": "s1", "source_type": "academic_api", "title": "Double Machine Learning", "canonical_url": "https://example.org/paper", "authors": "A", "published_at": "2024", "relevance": .9, "quality": .9, "difficulty": "medium", "estimated_minutes": 20, "reason": "直接回答问题", "metadata": {}}
        providers = [StaticResearchProvider([item, dict(item)])]
        run = self.brain(providers).submit(BrainRequest(text="研究 Double Machine Learning", mode="research", time_budget_minutes=30))
        result = run["result"]["results"][0]
        self.assertEqual(run["status"], "completed"); self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["source_type"], "academic_api")
        self.assertEqual(self.store.get_research_bundle(result["bundle"]["id"])["source_count"], 1)

    def test_external_research_adapters_are_named_and_unavailable_by_default(self):
        result = search_sources([ArxivResearchProvider(), CrossrefResearchProvider()], {"query": "causal inference", "limit": 5})
        self.assertEqual(result["sources"], [])
        self.assertEqual({row["provider"] for row in result["providers"]}, {"arxiv", "crossref"})
        self.assertTrue(all(row["status"] == "unavailable" for row in result["providers"]))

    def test_curriculum_uses_reviewed_weak_points_and_deduplicates(self):
        path = self.vault / "20-Knowledge/Concepts/倾向得分.md"
        path.write_text("---\nstatus: reviewed\ndomain: statistics\nmastery: 1\nimportance: 5\nweak_points: [重叠性, 重叠性]\n---\n# 倾向得分\n", encoding="utf-8")
        run = self.brain().submit(BrainRequest(text="生成学习计划", mode="plan"))
        candidates = self.store.list_curriculum_candidates("active")
        self.assertEqual([item["title"] for item in candidates], ["重叠性"])
        self.assertEqual(candidates[0]["kind"], "bridge")
        self.assertTrue(any(result["kind"] == "plan-proposal" for result in run["result"]["results"]))
        proposal = self.store.list_plan_proposals()[0]
        self.assertEqual(proposal["state"], "proposed")
        self.assertEqual(self.store.update_plan_proposal_state(proposal["id"], "confirmed")["state"], "confirmed")

    def test_model_curriculum_candidate_is_machine_verified_before_daily_admission(self):
        path = self.vault / "20-Knowledge/Concepts/倾向得分.md"
        path.write_text("---\nstatus: reviewed\ndomain: 因果推断\nmastery: 2\nimportance: 5\nweak_points: []\n---\n# 倾向得分\n", encoding="utf-8")

        class FakeCurriculumGateway:
            def generate_curriculum_candidates(self, state):
                return {"_profile_id": "fake-deepseek", "_model": "fake-model", "candidates": [{
                    "title": "可识别性与可估计性的区别", "kind": "comparison", "domain": "因果推断", "route": "mainline",
                    "prerequisites": ["倾向得分"], "related_topics": ["倾向得分"],
                    "why_now": ["连接因果目标与估计方法"],
                    "learning_outcomes": ["区分识别与估计", "说明识别假设的作用"],
                    "estimated_minutes": 8, "difficulty": "medium", "mainline_score": .95, "gap_score": .9, "confidence": .8,
                }]}

        tools = build_tool_registry(self.vault, self.store)
        registry = build_skill_registry(self.vault, self.store, tools, model_gateway=FakeCurriculumGateway())
        run = BrainOrchestrator(self.vault, self.store, registry).submit(BrainRequest(text="生成学习计划", mode="plan"))
        candidates = self.store.list_curriculum_candidates("active")
        self.assertEqual(run["status"], "awaiting_confirmation")
        self.assertEqual(candidates[0]["title"], "可识别性与可估计性的区别")
        self.assertEqual(candidates[0]["verification"]["grade"], "B")
        self.assertEqual(candidates[0]["model_profile_id"], "fake-deepseek")
        self.assertTrue(candidates[0]["basis"]["sourceBasis"])


if __name__ == "__main__": unittest.main()

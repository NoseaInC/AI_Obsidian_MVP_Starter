from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.brain import BrainOrchestrator, BrainRequest
from agent.brain.context_builder import ContextBuilder
from agent.brain.intent_router import IntentRouter
from agent.brain.planner import Planner
from agent.brain.schemas import IntentResult
from agent.core.storage import StateStore
from agent.skills import build_skill_registry
from agent.tools import build_tool_registry


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"
        for folder in ("01-Inbox/Ideas", "01-Inbox/Notes", "20-Knowledge/Topics", "20-Knowledge/Concepts", "10-Sources/Papers"):
            (self.vault / folder).mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/state.sqlite3")
        self.tools = build_tool_registry(self.vault, self.store)
        self.skills = build_skill_registry(self.vault, self.store, self.tools)
        self.brain = BrainOrchestrator(self.vault, self.store, self.skills)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def reviewed(self, title: str, body: str = "定义与条件", mastery: int = 1):
        path = self.vault / "20-Knowledge/Concepts" / f"{title}.md"
        path.write_text(f"---\ntype: concept\nstatus: reviewed\ndomain: statistics\nmastery: {mastery}\nimportance: 5\nweak_points: [适用边界]\n---\n# {title}\n\n{body}\n", encoding="utf-8")
        return path

    def test_deterministic_and_model_intent_routing_with_invalid_fallback(self):
        self.assertEqual(IntentRouter().route(BrainRequest(text="帮我研究双重机器学习资料")).primary_intent, "research_topic")
        model = IntentRouter(lambda request: {"primary_intent": "capture_text", "confidence": .8})
        self.assertEqual(model.route(BrainRequest(text="ambiguous")).basis, "model")
        invalid = IntentRouter(lambda request: "not-json")
        result = invalid.route(BrainRequest(text="ambiguous"))
        self.assertEqual(result.primary_intent, "ask_question"); self.assertEqual(result.basis, "invalid-model-fallback")

    def test_capture_lifecycle_idempotency_cancel_retry_and_private_payload(self):
        request = BrainRequest(text="记录灵感：用因果图解释混杂", mode="capture", idempotency_key="same")
        first = self.brain.submit(request); second = self.brain.submit(request)
        self.assertEqual(first["id"], second["id"]); self.assertEqual(first["status"], "awaiting_confirmation")
        self.assertEqual([row["status"] for row in first["steps"]], ["completed"])
        self.assertEqual(len(first["tool_events"]), 2); self.assertEqual(len(first["proposed_actions"]), 1)
        raw = self.store.connection.execute("SELECT request_json FROM brain_runs WHERE id=?", (first["id"],)).fetchone()[0]
        self.assertNotIn("用因果图解释混杂", raw)
        self.assertEqual(self.brain.cancel(first["id"])["status"], "cancelled")
        retry = self.brain.retry(first["id"])
        self.assertEqual(retry["retry_of"], first["id"]); self.assertEqual(retry["status"], "awaiting_confirmation")

    def test_context_is_ranked_bounded_and_active_note_is_explicit(self):
        self.reviewed("倾向得分", "倾向得分用于因果推断")
        self.reviewed("无关主题", "完全不同")
        active = self.vault / "01-Inbox/Notes/current.md"; active.write_text("当前笔记", encoding="utf-8")
        builder = ContextBuilder(self.vault, token_budget=2000, note_limit=1, excerpt_chars=300)
        context = builder.build(BrainRequest(text="解释倾向得分", active_note="01-Inbox/Notes/current.md", selected_text="选中内容"), IntentResult("learn_topic"))
        self.assertEqual(context["active_note"]["path"], "01-Inbox/Notes/current.md")
        self.assertEqual(context["selected_text"], "选中内容")
        self.assertEqual(len(context["relevant_notes"]), 1)
        self.assertEqual(context["relevant_notes"][0]["title"], "倾向得分")
        self.assertNotIn("无关主题", json.dumps(context, ensure_ascii=False))

    def test_planner_rejects_unregistered_skill(self):
        class Missing:
            def has(self, name): return False
            def definition(self, name): raise AssertionError
        with self.assertRaisesRegex(Exception, "未注册能力"):
            Planner(Missing()).plan(BrainRequest(text="x"), IntentResult("capture_text"))

    def test_research_without_sources_returns_recoverable_partial_result(self):
        run = self.brain.submit(BrainRequest(text="研究一个完全不存在的主题", mode="research"))
        self.assertEqual(run["status"], "completed")
        recovery = run["result"]["results"][0]
        self.assertEqual(recovery["kind"], "research-recovery")
        self.assertEqual(recovery["status"], "partially_completed")
        self.assertEqual(recovery["error_code"], "research_no_results")
        self.assertEqual(len(recovery["fallback_options"]), 3)


if __name__ == "__main__": unittest.main()

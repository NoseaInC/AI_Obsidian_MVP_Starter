from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from agent.brain.run_coordinator import AssistantRunCoordinator
from agent.brain.schemas import BrainRequest, IntentResult
from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.tools.base import ToolDefinition


class FakeStore:
    def __init__(self) -> None:
        self.cancelled = False
        self.events = []
        self.checkpoints = []
        self.tool_events = []

    def create_brain_run(self, run_id, request): pass
    def update_brain_run(self, run_id, status, current_step=None): pass
    def set_brain_intent(self, run_id, intent): pass
    def set_brain_model(self, run_id, profile_id): pass
    def set_brain_plan(self, run_id, plan): pass
    def start_brain_step(self, *args): pass
    def complete_brain_step(self, step_id): pass
    def finish_brain_run(self, *args): pass
    def fail_brain_step(self, *args): pass
    def fail_brain_run(self, *args): pass
    def audit(self, *args): pass
    def brain_cancel_requested(self, run_id): return self.cancelled
    def save_agent_run_checkpoint(self, run_id, phase, state, pending_approval_id=None):
        self.checkpoints.append((phase, state, pending_approval_id))
    def record_tool_event(self, event_id, run_id, step_id, tool, status, input_summary, output_summary="", error_code=None):
        self.tool_events.append((tool, status))


class FakeRouter:
    def route(self, request):
        return IntentResult("ask_question")


class FakeTools:
    def __init__(self):
        self.calls = []
        self.defs = {
            "search_vault": ToolDefinition(
                "search_vault",
                "search",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                {"type": "object", "additionalProperties": True},
            ),
        }

    def has(self, name): return name in self.defs
    def definition(self, name): return self.defs[name]
    def model_specs(self, names, allowed_permissions=("read_only",)):
        return [self.defs[name].model_spec() for name in names if name in self.defs]
    def call(self, name, payload, **kwargs):
        self.calls.append((name, payload))
        return {"items": [{"title": "Delta Method", "path": "20-Knowledge/Delta.md"}]}


class FakeProvider:
    def __init__(self):
        self.round = 0

    def structured_output(self, model, messages, schema, **options):
        self.round += 1
        if self.round == 1:
            content = {
                "action": "tool",
                "tool_name": "search_vault",
                "arguments": {"query": "Delta Method", "limit": 5},
                "purpose": "检索现有笔记",
                "clarification": "",
            }
        else:
            content = {
                "action": "respond",
                "tool_name": "",
                "arguments": {},
                "purpose": "",
                "clarification": "",
            }
        return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    def stream_chat(self, model, messages, **options):
        yield {"type": "delta", "content": "已找到相关笔记。"}
        yield {"type": "finish", "finishReason": "stop"}


class RuntimeV3Test(unittest.TestCase):
    def test_observation_driven_replanning(self):
        with TemporaryDirectory() as temp:
            store = FakeStore()
            tools = FakeTools()
            coordinator = AssistantRunCoordinator(Path(temp), store, tools, FakeRouter())
            request = BrainRequest(text="这个方法和 Vault 中哪些笔记有关？")
            session = coordinator.begin(
                "run-test",
                request,
                focus={"activeMethod": {"displayName": "Delta Method"}},
                intent_hint=IntentResult("ask_question"),
            )
            events = list(
                coordinator.run(
                    session,
                    FakeProvider(),
                    model="fake",
                    recent=[],
                    native_tool_calling=False,
                )
            )
            self.assertEqual(tools.calls[0][0], "search_vault")
            self.assertEqual(session.answer, "已找到相关笔记。")
            self.assertTrue(any(item["type"] == "observation.recorded" for item in events))
            self.assertGreaterEqual(session.planner_round, 2)


class StructuredWriteProvider:
    def __init__(self, note_path: str, original: str) -> None:
        self.note_path = note_path
        self.original = original
        self.round = 0
        self.schemas: list[dict[str, Any]] = []
        self.messages: list[list[dict[str, Any]]] = []

    def structured_output(self, model, messages, schema, **options):
        self.round += 1
        self.schemas.append(schema)
        self.messages.append(list(messages))
        decisions = [
            {
                "action": "tool",
                "tool_name": "read_note_excerpt",
                "arguments": {"path": self.note_path, "max_chars": 4000},
                "purpose": "读取当前笔记正文",
                "clarification": "",
            },
            {
                "action": "tool",
                "tool_name": "search_vault",
                "arguments": {"query": "Delta Method", "limit": 5},
                "purpose": "搜索相关知识",
                "clarification": "",
            },
            {
                "action": "tool",
                "tool_name": "create_change_set",
                "arguments": {
                    "run_id": "model-cannot-choose-run-id",
                    "title": "补充 Delta Method",
                    "writes": [
                        {
                            "path": self.note_path,
                            "content": self.original + "\n## 补充\n\n影响函数与 Delta Method 相关。\n",
                            "category": "assistant-proposal",
                        }
                    ],
                },
                "purpose": "创建可审核的修改提案",
                "clarification": "",
            },
        ]
        value = decisions[min(self.round - 1, len(decisions) - 1)]
        return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}

    def stream_chat(self, model, messages, **options):
        yield {"type": "delta", "content": "不应在审批前进入最终回答。"}


class AssistantRuntimeV3IntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        self.note_path = "20-Knowledge/Topics/Delta Method.md"
        self.note = self.vault / self.note_path
        self.note.parent.mkdir(parents=True)
        self.original = "# Delta Method\n"
        self.note.write_text(self.original, encoding="utf-8")
        self.service = AgentService(self.vault, key_store=FakeKeyStore())
        self.profile = self.service.save_model_profile({
            "displayName": "离线结构化规划模型",
            "providerType": "deepseek",
            "baseUrl": "https://api.deepseek.com/v1",
            "apiKey": "fake-runtime-v3-only",
            "defaultModel": "deepseek-chat",
            "settings": {"customHeaders": {}, "toolCalling": False},
        })
        self.conversation = self.service.create_conversation({"title": "Runtime V3"})

    def tearDown(self) -> None:
        self.service.store.close()
        self.temp.cleanup()

    def _run_to_proposal(self):
        provider = StructuredWriteProvider(self.note_path, self.original)
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": self.conversation["id"],
                "message": "读取当前笔记，搜索 Delta Method 相关知识，再把结论补充到当前笔记。",
                "active_note": {"path": self.note_path, "selection": ""},
                "profile_id": self.profile["id"],
            }))
        return provider, events

    def test_real_observations_create_persisted_proposal_without_vault_write(self):
        provider, events = self._run_to_proposal()
        event_types = [item["type"] for item in events]
        expected = [
            "run.started",
            "context.resolved",
            "plan.created",
            "plan.decision",
            "tool.requested",
            "tool.completed",
            "observation.recorded",
            "proposal.created",
            "approval.required",
            "run.awaiting_approval",
        ]
        cursor = 0
        for event_type in event_types:
            if cursor < len(expected) and event_type == expected[cursor]:
                cursor += 1
        self.assertEqual(cursor, len(expected))
        self.assertEqual(self.note.read_text(encoding="utf-8"), self.original)
        self.assertEqual(events[-1]["type"], "run.awaiting_approval")
        self.assertTrue(all(item["schemaVersion"] == 2 for item in events))
        run_id = events[-1]["runId"]
        self.assertEqual(self.service.store.get_brain_run(run_id)["status"], "awaiting_confirmation")
        checkpoint = self.service.store.get_agent_run_checkpoint(run_id)
        self.assertEqual(checkpoint["phase"], "awaiting_approval")
        self.assertEqual(checkpoint["pendingApprovalId"], events[-1]["proposalId"])
        private_body = "影响函数与 Delta Method 相关。"
        self.assertNotIn(
            private_body,
            json.dumps(checkpoint["state"], ensure_ascii=False),
        )
        tool_rows = self.service.store.connection.execute(
            "SELECT input_summary FROM tool_events WHERE run_id=?",
            (run_id,),
        ).fetchall()
        self.assertNotIn(
            private_body,
            "\n".join(str(row["input_summary"]) for row in tool_rows),
        )
        plan_event = next(item for item in events if item["type"] == "plan.created")
        self.assertEqual(plan_event["goal"]["type"], "private-text")
        persisted = self.service.assistant_run_events(run_id)
        self.assertEqual(persisted["events"], events)
        self.assertEqual(
            self.service.assistant_run_events(run_id, after_sequence=events[-2]["seq"])["events"],
            [events[-1]],
        )
        allowed = provider.schemas[-1]["schema"]["properties"]["tool_name"]["enum"]
        self.assertIn("", allowed)
        self.assertIn("create_change_set", allowed)
        self.assertNotIn("apply_confirmed_change_set", allowed)

    def test_confirmed_resume_applies_once_and_completes_run(self):
        _, events = self._run_to_proposal()
        run_id = events[-1]["runId"]
        result = self.service.resume_assistant_run(run_id, True)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertIn("影响函数与 Delta Method 相关", self.note.read_text(encoding="utf-8"))
        replay = self.service.resume_assistant_run(run_id, True)
        self.assertTrue(replay["idempotent"])
        lifecycle = [item["type"] for item in self.service.assistant_run_events(run_id)["events"]]
        self.assertEqual(lifecycle.count("change.applied"), 1)
        self.assertEqual(lifecycle[-1], "run.completed")

    def test_reject_keeps_vault_unchanged_and_is_idempotent(self):
        _, events = self._run_to_proposal()
        run_id = events[-1]["runId"]
        proposal_id = events[-1]["proposalId"]
        result = self.service.reject_assistant_run(run_id, "证据不足")
        self.assertEqual(result["run"]["status"], "cancelled")
        self.assertEqual(self.note.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.service.store.get_brain_change_set(proposal_id)["state"], "rejected")
        replay = self.service.reject_assistant_run(run_id, "重复拒绝")
        self.assertTrue(replay["idempotent"])


if __name__ == "__main__":
    unittest.main()

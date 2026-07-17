from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.core.storage import StateStore
from agent.brain import BrainRequest
from agent.tools.vault_access import read_note_excerpt


class _ToolCallingProvider:
    provider_type = "openai-compatible"

    def __init__(self, tool_name: str = "search_vault") -> None:
        self.tool_name = tool_name
        self.planner_calls = 0
        self.final_messages: list[dict] = []
        self.planner_messages: list[dict] = []

    def chat(self, model, messages, **options):
        self.planner_messages = list(messages)
        self.planner_calls += 1
        if self.planner_calls == 1:
            arguments = {"query": "倾向得分", "limit": 5} if self.tool_name == "search_vault" else {"change_set_id": "forbidden", "confirmed": True}
            return {"choices": [{"message": {"content": "", "tool_calls": [{
                "id": "call-model-1", "type": "function",
                "function": {"name": self.tool_name, "arguments": json.dumps(arguments, ensure_ascii=False)},
            }]}}]}
        return {"choices": [{"message": {"content": "READY"}}]}

    def stream_chat(self, model, messages, **options):
        self.final_messages = list(messages)
        yield {"type": "delta", "content": "基于本地工具结果的回答"}
        yield {"type": "finish", "finishReason": "stop"}

    def structured_output(self, model, messages, schema, **options):
        self.planner_messages = list(messages)
        context = json.loads(str(messages[-1]["content"]))
        request = str(context.get("request") or "")
        observations = list(context.get("observations") or [])
        completed = [item.get("tool") for item in observations if item.get("status") == "completed"]
        active_note = str(context.get("active_note") or "")

        tool_name = ""
        arguments = {}
        if "读取一下现在我的 Obsidian" in request and "get_vault_overview" not in completed:
            tool_name, arguments = "get_vault_overview", {"recent_limit": 8}
        elif active_note and "只根据这篇实际笔记" in request:
            sequence = ["read_note_metadata", "read_note_excerpt", "get_related_notes"]
            tool_name = next((name for name in sequence if name not in completed), "")
            if tool_name:
                arguments = {"path": active_note}
                if tool_name == "read_note_excerpt": arguments["max_chars"] = 4000
        elif "实际搜索 Vault" in request:
            if "search_vault" not in completed:
                tool_name, arguments = "search_vault", {"query": "因果推断", "limit": 5}
            elif "read_note_excerpt" not in completed:
                search = next(item for item in observations if item.get("tool") == "search_vault")
                items = ((search.get("result") or {}).get("items") or [])
                if items:
                    tool_name = "read_note_excerpt"
                    arguments = {"path": items[0]["path"], "max_chars": 4000}

        value = {
            "action": "tool" if tool_name else "respond",
            "tool_name": tool_name,
            "arguments": arguments,
            "purpose": f"执行 {tool_name}" if tool_name else "",
            "clarification": "",
        }
        return {"choices": [{"message": {"content": json.dumps(value, ensure_ascii=False)}}]}


class AgentRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True)
        (self.vault / "20-Knowledge/Concepts/倾向得分.md").write_text(
            "---\ntype: concept\nstatus: reviewed\ndomain: statistics\nmastery: 2\nimportance: 5\n---\n# 倾向得分\n\n用于因果推断。\n",
            encoding="utf-8",
        )
        self.keys = FakeKeyStore()
        self.service = AgentService(self.vault, key_store=self.keys)
        self.profile = self.service.save_model_profile({
            "displayName": "离线工具模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-only",
            "defaultModel": "fake-tools", "settings": {"customHeaders": {}, "toolCalling": True},
        })
        self.service.set_model_routing({"assistant_chat": {"profileId": self.profile["id"]}})

    def tearDown(self):
        self.service.store.close()
        self.temp.cleanup()

    def test_assistant_stream_runs_registered_tool_loop_and_persists_audit(self):
        provider = _ToolCallingProvider()
        conversation = self.service.create_conversation({"title": "真实工具链"})
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": conversation["id"], "message": "解释倾向得分并关联已有知识",
                "profile_id": self.profile["id"],
            }))
        types = [item["type"] for item in events]
        self.assertIn("plan.created", types)
        self.assertIn("tool.requested", types)
        self.assertIn("tool.started", types)
        self.assertIn("tool.completed", types)
        self.assertEqual(events[-1]["type"], "run.completed")
        run = self.service.get_brain_run(events[-1]["brainRunId"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual([item["tool"] for item in run["tool_events"]], ["search_vault"])
        self.assertIn("tool_observations", provider.final_messages[0]["content"])
        self.assertIn("倾向得分", provider.final_messages[0]["content"])

    def test_model_cannot_invent_or_call_mutating_tool(self):
        provider = _ToolCallingProvider("apply_confirmed_change_set")
        conversation = self.service.create_conversation({"title": "权限测试"})
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": conversation["id"], "message": "解释倾向得分",
                "profile_id": self.profile["id"],
            }))
        blocked = [item for item in events if item["type"] == "tool.completed" and item.get("tool") == "apply_confirmed_change_set"]
        self.assertEqual(blocked[0]["status"], "failed")
        run = self.service.get_brain_run(events[-1]["brainRunId"])
        self.assertEqual(run["tool_events"], [])
        self.assertFalse((self.vault / "90-Local-Only/Agent/Change-Sets/forbidden.json").exists())

    def test_tool_contracts_are_specific_and_strict(self):
        specs = self.service.tools.model_specs(("search_vault", "apply_confirmed_change_set"))
        self.assertEqual([item["function"]["name"] for item in specs], ["search_vault"])
        schema = specs[0]["function"]["parameters"]
        self.assertEqual(schema["required"], ["query"])
        self.assertFalse(schema["additionalProperties"])
        with self.assertRaisesRegex(ValueError, "missing:query"):
            self.service.tools.call("search_vault", {"limit": 5}, run_id="not-created")

    def test_vault_overview_reads_real_safe_roots_and_excludes_project_runtime_files(self):
        (self.vault / "AGENT_INTERNAL_SPEC.md").write_text("# 不能进入模型上下文\n", encoding="utf-8")
        private = self.vault / "90-Local-Only/secret.md"
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text("private runtime text", encoding="utf-8")
        profile = self.service.save_model_profile({
            "displayName": "无工具调用模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-overview",
            "defaultModel": "fake-overview", "settings": {"customHeaders": {}, "toolCalling": False},
        })
        provider = _ToolCallingProvider()
        conversation = self.service.create_conversation({"title": "知识库概览"})
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": conversation["id"], "message": "读取一下现在我的 Obsidian",
                "profile_id": profile["id"],
            }))
        run = self.service.get_brain_run(events[-1]["brainRunId"])
        self.assertEqual([item["tool"] for item in run["tool_events"]], ["get_vault_overview"])
        final_context = provider.final_messages[0]["content"]
        self.assertIn('"totalNotes":1', final_context)
        self.assertIn("倾向得分", final_context)
        self.assertNotIn("AGENT_INTERNAL_SPEC", final_context)
        self.assertNotIn("private runtime text", final_context)
        with self.assertRaisesRegex(ValueError, "invalid_note_path"):
            read_note_excerpt(self.vault, {"path": "90-Local-Only/secret.md"})

    def test_natural_language_search_reads_top_matching_note_excerpt(self):
        profile = self.service.save_model_profile({
            "displayName": "确定性检索模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-search",
            "defaultModel": "fake-search", "settings": {"customHeaders": {}, "toolCalling": False},
        })
        provider = _ToolCallingProvider()
        conversation = self.service.create_conversation({"title": "自然语言检索"})
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": conversation["id"],
                "message": "我有哪些因果推断知识？请实际搜索 Vault，并读取最相关的正式笔记片段。",
                "profile_id": profile["id"],
            }))
        run = self.service.get_brain_run(events[-1]["brainRunId"])
        self.assertEqual([item["tool"] for item in run["tool_events"]], ["search_vault", "read_note_excerpt"])
        self.assertIn("用于因果推断", provider.final_messages[0]["content"])

    def test_explicit_active_note_reads_only_that_note_without_unrequested_vault_search(self):
        profile = self.service.save_model_profile({
            "displayName": "确定性当前笔记模型", "providerType": "openai-compatible",
            "baseUrl": "http://localhost:9000/v1", "apiKey": "fake-active-note",
            "defaultModel": "fake-active-note", "settings": {"customHeaders": {}, "toolCalling": False},
        })
        provider = _ToolCallingProvider()
        conversation = self.service.create_conversation({"title": "当前笔记"})
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.assistant_stream({
                "conversation_id": conversation["id"],
                "message": "请只根据这篇实际笔记解释充分性，不要搜索其他笔记，不要修改内容。",
                "active_note": {"path": "20-Knowledge/Concepts/倾向得分.md", "selection": ""},
                "profile_id": profile["id"],
            }))
        run = self.service.get_brain_run(events[-1]["brainRunId"])
        self.assertEqual(
            [item["tool"] for item in run["tool_events"]],
            ["read_note_metadata", "read_note_excerpt", "get_related_notes"],
        )
        self.assertIn("用于因果推断", provider.final_messages[0]["content"])

    def test_secret_shaped_user_text_never_reaches_planner_or_final_model_context(self):
        provider = _ToolCallingProvider()
        conversation = self.service.create_conversation({"title": "密钥脱敏"})
        secret = "sk-abcdefghijk12345"
        with patch.object(self.service.models, "provider", return_value=provider):
            list(self.service.assistant_stream({
                "conversation_id": conversation["id"],
                "message": f"解释这段配置为何失败：Bearer abcdefghijk 和 {secret}",
                "profile_id": self.profile["id"],
            }))
        sent = json.dumps(provider.planner_messages + provider.final_messages, ensure_ascii=False)
        self.assertNotIn(secret, sent)
        self.assertNotIn("Bearer abcdefghijk", sent)
        self.assertIn("[REDACTED]", sent)
        persisted = json.dumps(self.service.get_conversation(conversation["id"]), ensure_ascii=False)
        self.assertNotIn(secret, persisted)
        self.assertNotIn("Bearer abcdefghijk", persisted)

    def test_runtime_restart_marks_interrupted_agent_run_recoverable_and_honest(self):
        root = self.vault / "90-Local-Only/Agent/recovery-test.sqlite3"
        store = StateStore(root)
        request = BrainRequest(text="解释倾向得分")
        store.create_brain_run("run-interrupted", request)
        store.update_brain_run("run-interrupted", "running")
        store.start_brain_step("run-interrupted", "step-interrupted", 1, "assistant_agent", "测试恢复")
        store.close()
        recovered = StateStore(root)
        self.assertEqual(recovered.recover_interrupted(), 0)
        run = recovered.get_brain_run("run-interrupted", include_details=True)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "brain_runtime_interrupted")
        self.assertEqual(run["steps"][0]["status"], "failed")
        recovered.close()


if __name__ == "__main__":
    unittest.main()

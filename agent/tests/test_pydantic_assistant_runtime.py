from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from agent.core.models import FakeKeyStore
from agent.core.service import AgentService


def _tool_history(messages) -> tuple[list[str], str]:
    names: list[str] = []
    proposal = ""
    for message in messages:
        for part in getattr(message, "parts", []):
            name = str(getattr(part, "tool_name", "") or "")
            content = getattr(part, "content", None)
            if name:
                names.append(name)
            if isinstance(content, BaseModel):
                content = content.model_dump()
            if isinstance(content, dict):
                proposal = str(content.get("proposal_id") or content.get("proposalId") or proposal)
    return names, proposal


def _write_model(path: str, before: str, after: str) -> FunctionModel:
    async def stream(messages, _info):
        names, proposal = _tool_history(messages)
        if "get_current_note" not in names:
            yield {0: DeltaToolCall(name="get_current_note", json_args="{}", tool_call_id="read-1")}
            return
        if "propose_vault_change" not in names:
            arguments = {
                "title": "补充当前笔记",
                "writes": [{"path": path, "content": after, "category": "assistant-agent"}],
            }
            yield {0: DeltaToolCall(name="propose_vault_change", json_args=json.dumps(arguments, ensure_ascii=False), tool_call_id="proposal-1")}
            return
        if "commit_vault_change" not in names:
            yield {0: DeltaToolCall(name="commit_vault_change", json_args=json.dumps({"proposal_id": proposal}), tool_call_id="commit-1")}
            return
        yield "修改已按确认完成。"

    return FunctionModel(stream_function=stream)


class PydanticAssistantRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "20-Knowledge").mkdir(parents=True)
        (self.vault / "10-Inbox").mkdir(parents=True)
        self.keys = FakeKeyStore()
        self.service = AgentService(self.vault, key_store=self.keys)
        self.profile = self.service.save_model_profile({
            "displayName": "离线 PydanticAI",
            "providerType": "openai-compatible",
            "baseUrl": "http://127.0.0.1:9000/v1",
            "apiKey": "offline-test-key",
            "defaultModel": "test-model",
            "availableModels": ["test-model"],
            "settings": {},
        })
        self.service.set_model_routing({"assistant_chat": {"profileId": self.profile["id"]}})

    def tearDown(self) -> None:
        self.service.store.close()
        self.temp.cleanup()

    def _stream(self, message: str, *, active_note: str = "", model=None):
        self.service.assistant_runtime.model_factory = lambda *_: model or TestModel(call_tools=[], custom_output_text="离线回答")
        body = {"message": message, "profile_id": self.profile["id"]}
        if active_note:
            body["active_note"] = {"path": active_note}
        return list(self.service.assistant_stream(body))

    def _pending_update(self):
        path = "20-Knowledge/Test.md"
        before = "# Test\n\n原文。\n"
        after = before + "\n## Agent 补充\n\n新增内容。\n"
        target = self.vault / path
        target.write_text(before, encoding="utf-8")
        events = self._stream("把刚才内容补进当前笔记。", active_note=path, model=_write_model(path, before, after))
        confirmation = next(item for item in events if item["type"] == "inline.confirmation.required")
        return target, before, after, events, confirmation

    def test_01_text_stream_is_ordered_and_persisted(self):
        events = self._stream("解释 Delta Method")
        types = [item["type"] for item in events]
        self.assertLess(types.index("message.started"), types.index("message.delta"))
        self.assertLess(types.index("message.delta"), types.index("message.completed"))
        self.assertIn("usage.updated", types)
        self.assertEqual([item["seq"] for item in events], list(range(1, len(events) + 1)))

    def test_02_tool_observation_drives_a_second_model_round(self):
        calls: list[list[str]] = []
        async def stream(messages, _info):
            names, _ = _tool_history(messages); calls.append(names)
            if "search_vault" not in names:
                yield {0: DeltaToolCall(name="search_vault", json_args=json.dumps({"query": "Delta Method"}), tool_call_id="search-1")}; return
            yield "已根据真实搜索结果回答。"
        events = self._stream("查找 Delta Method", model=FunctionModel(stream_function=stream))
        self.assertGreaterEqual(len(calls), 2)
        self.assertIn("search_vault", calls[-1])
        self.assertIn("tool.completed", [item["type"] for item in events])

    def test_03_current_note_tool_reads_only_explicit_note(self):
        path = "20-Knowledge/Test.md"; (self.vault / path).write_text("# Test\n\n真实正文", encoding="utf-8")
        async def stream(messages, _info):
            names, _ = _tool_history(messages)
            if "get_current_note" not in names:
                yield {0: DeltaToolCall(name="get_current_note", json_args="{}", tool_call_id="read-1")}; return
            yield "已读取当前笔记。"
        events = self._stream("读取当前笔记", active_note=path, model=FunctionModel(stream_function=stream))
        tool = next(item for item in events if item["type"] == "tool.completed")
        self.assertIn("Test", tool["summary"])

    def test_04_proposal_does_not_write_before_confirmation(self):
        target, before, _, events, _ = self._pending_update()
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertIn("write.diff", [item["type"] for item in events])

    def test_05_inline_confirmation_has_public_paths_without_payload_reference(self):
        _, _, _, _, event = self._pending_update()
        serialized = json.dumps(event, ensure_ascii=False)
        self.assertIn("20-Knowledge/Test.md", serialized)
        self.assertNotIn("payload_path", serialized)
        self.assertEqual(event["confirmation"]["tool_name"], "commit_vault_change")

    def test_06_confirm_resumes_same_run_and_applies_once(self):
        target, _, after, _, event = self._pending_update()
        resumed = list(self.service.confirm_assistant_run(event["runId"], True))
        self.assertTrue(all(item["runId"] == event["runId"] for item in resumed))
        self.assertEqual(target.read_text(encoding="utf-8"), after)
        replay = list(self.service.confirm_assistant_run(event["runId"], True))
        self.assertEqual(target.read_text(encoding="utf-8"), after)
        self.assertEqual(replay[-1]["type"], "run.completed")

    def test_07_reject_keeps_vault_unchanged(self):
        target, before, _, _, event = self._pending_update()
        resumed = list(self.service.confirm_assistant_run(event["runId"], False))
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertEqual(resumed[-1]["type"], "run.completed")

    def test_08_write_tool_is_blocked_without_explicit_write_intent(self):
        async def stream(_messages, _info):
            args = {"title": "越权", "writes": [{"path": "10-Inbox/x.md", "content": "x"}]}
            yield {0: DeltaToolCall(name="propose_vault_change", json_args=json.dumps(args), tool_call_id="bad-1")}
        events = self._stream("解释概念", model=FunctionModel(stream_function=stream))
        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertFalse((self.vault / "10-Inbox/x.md").exists())

    def test_09_reviewed_note_can_never_be_proposed_for_overwrite(self):
        path = "20-Knowledge/Protected.md"; before = "---\nstatus: reviewed\n---\n# Protected\n"
        (self.vault / path).write_text(before, encoding="utf-8")
        events = self._stream("补充到当前笔记", active_note=path, model=_write_model(path, before, before + "改写"))
        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertEqual((self.vault / path).read_text(encoding="utf-8"), before)

    def test_10_base_hash_change_rejects_confirmed_apply(self):
        target, _, _, _, event = self._pending_update()
        target.write_text("# 用户后来修改\n", encoding="utf-8")
        resumed = list(self.service.confirm_assistant_run(event["runId"], True))
        self.assertEqual(resumed[-1]["type"], "run.failed")
        self.assertEqual(target.read_text(encoding="utf-8"), "# 用户后来修改\n")

    def test_11_reconnect_returns_durable_ordered_events(self):
        events = self._stream("普通问题")
        run_id = events[0]["runId"]
        replay = self.service.assistant_run_events(run_id, 2)["items"]
        self.assertTrue(replay)
        self.assertTrue(all(item["seq"] > 2 for item in replay))

    def test_12_cancel_is_backend_state_not_frontend_only(self):
        _, _, _, _, event = self._pending_update()
        result = self.service.cancel_assistant_run(event["runId"])
        self.assertEqual(result["status"], "cancelling")
        self.assertEqual(self.service.assistant_runtime.persistence.load(event["runId"]).status, "cancelled")

    def test_13_secrets_are_redacted_before_private_request_persistence(self):
        secret = "sk-live-THIS-MUST-NOT-PERSIST"
        events = self._stream(f"解释配置失败：Bearer abcdefghijklmnop 和 {secret}")
        run_id = events[0]["runId"]
        root = self.vault / "90-Local-Only/Agent/assistant-runtime" / run_id
        self.assertNotIn(secret, (root / "request.json").read_text(encoding="utf-8"))
        self.assertNotIn(secret, json.dumps(events, ensure_ascii=False))

    def test_14_diff_is_generated_on_demand_and_not_stored_in_sqlite(self):
        _, _, _, _, event = self._pending_update()
        proposal = event["confirmation"]["proposal_id"]
        diff = self.service.diff_change_set(proposal)
        self.assertIn("+新增内容。", diff["files"][0]["diff"])
        database = (self.vault / "90-Local-Only/Agent/agent.sqlite3").read_bytes()
        self.assertNotIn("新增内容。".encode(), database)

    def test_15_runtime_history_is_private_file_not_sqlite_body(self):
        marker = "PRIVATE-HISTORY-MARKER-9483"
        self._stream(marker)
        database = (self.vault / "90-Local-Only/Agent/agent.sqlite3").read_bytes()
        self.assertNotIn(marker.encode(), database)

    def test_16_compact_creates_a_durable_checkpoint_event(self):
        events = self._stream("需要压缩的上下文")
        result = self.service.compact_assistant_run(events[0]["runId"])
        self.assertTrue(result["checkpointId"].startswith("checkpoint-"))
        self.assertEqual(result["event"]["type"], "context.compacted")

    def test_17_fork_records_parent_and_sequence(self):
        events = self._stream("建立可分支会话")
        source_id = events[0]["runId"]
        result = self.service.fork_assistant_run(source_id, 2)
        snapshot = self.service.assistant_runtime.persistence.load(result["runId"])
        self.assertEqual(snapshot.parent_run_id, source_id)
        self.assertEqual(snapshot.forked_from_sequence, 2)

    def test_18_natural_current_note_write_phrasing_requires_confirmation(self):
        path = "20-Knowledge/Drafts/Natural phrase.md"
        before = "# Natural phrase\n\n原文。\n"
        after = before + "\nDelta Method 的核心前提是可微性。\n"
        target = self.vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(before, encoding="utf-8")
        events = self._stream(
            "请把一句“Delta Method 的核心前提是可微性”补充进当前笔记。只创建修改提案，不要绕过确认。",
            active_note=path,
            model=_write_model(path, before, after),
        )
        self.assertIn("inline.confirmation.required", [item["type"] for item in events])
        self.assertEqual(target.read_text(encoding="utf-8"), before)

    def test_19_tool_event_summary_never_persists_note_excerpt_in_sqlite(self):
        marker = "PRIVATE-NOTE-EXCERPT-MUST-STAY-OUTSIDE-SQLITE"
        path = "20-Knowledge/Private excerpt.md"
        target = self.vault / path
        target.write_text(f"# Private\n\n{marker}\n", encoding="utf-8")

        async def stream(messages, _info):
            names, _ = _tool_history(messages)
            if "get_current_note" not in names:
                yield {0: DeltaToolCall(name="get_current_note", json_args="{}", tool_call_id="private-read")}
                return
            yield "已基于当前笔记回答。"

        self._stream(
            "读取当前笔记",
            active_note=path,
            model=FunctionModel(stream_function=stream),
        )
        database = (self.vault / "90-Local-Only/Agent/agent.sqlite3").read_bytes()
        self.assertNotIn(marker.encode(), database)


if __name__ == "__main__":
    unittest.main()

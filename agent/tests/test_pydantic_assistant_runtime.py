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


def _tool_results(messages, tool_name: str) -> list[dict]:
    results: list[dict] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            if str(getattr(part, "tool_name", "") or "") != tool_name:
                continue
            content = getattr(part, "content", None)
            if isinstance(content, BaseModel):
                content = content.model_dump()
            if isinstance(content, dict):
                results.append(content)
    return results


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

    def test_08_commit_defaults_to_inline_harness_request_without_intent_flags(self):
        path = "10-Inbox/x.md"
        events = self._stream(
            "好，继续。",
            model=_write_model(path, "", "# X\n\n由 Harness 管理。\n"),
        )
        confirmation = next(item for item in events if item["type"] == "inline.confirmation.required")
        self.assertEqual(confirmation["confirmation"]["risk_level"], "medium")
        self.assertIn("默认需要", confirmation["confirmation"]["summary"])
        self.assertFalse((self.vault / "10-Inbox/x.md").exists())
        resumed = list(self.service.confirm_assistant_run(confirmation["runId"], True))
        self.assertEqual(resumed[-1]["type"], "run.completed")
        self.assertTrue((self.vault / "10-Inbox/x.md").is_file())

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

    def test_20_harness_collects_local_sources_and_writes_verified_topic_note(self):
        concept = self.vault / "20-Knowledge/倾向得分.md"
        method = self.vault / "20-Knowledge/匹配方法.md"
        concept.write_text("# 倾向得分\n\n在给定协变量时接受处理的条件概率。\n", encoding="utf-8")
        method.write_text("# 匹配方法\n\n比较倾向得分相近的处理组与对照组。\n", encoding="utf-8")
        target_path = "10-Inbox/倾向得分匹配-主题整理.md"
        target_content = (
            "# 倾向得分匹配\n\n"
            "## 核心思路\n\n通过倾向得分构造可比的处理组与对照组。\n\n"
            "## 本地来源\n\n- [[倾向得分]]\n- [[匹配方法]]\n"
        )

        phase = 0
        proposal = ""

        async def stream(messages, _info):
            nonlocal phase, proposal
            names, observed_proposal = _tool_history(messages)
            proposal = observed_proposal or proposal
            if phase == 0:
                phase = 1
                yield {0: DeltaToolCall(name="search_vault", json_args=json.dumps({"query": "倾向得分 匹配"}, ensure_ascii=False), tool_call_id="topic-search")}
                return
            if phase == 1:
                phase = 2
                yield {0: DeltaToolCall(name="read_vault_note", json_args=json.dumps({"path": "20-Knowledge/倾向得分.md"}, ensure_ascii=False), tool_call_id="topic-read-1")}
                return
            if phase == 2:
                phase = 3
                yield {0: DeltaToolCall(name="read_vault_note", json_args=json.dumps({"path": "20-Knowledge/匹配方法.md"}, ensure_ascii=False), tool_call_id="topic-read-2")}
                return
            if phase == 3:
                phase = 4
                args = {"title": "整理倾向得分匹配主题", "writes": [{"path": target_path, "content": target_content, "category": "assistant-agent"}]}
                yield {0: DeltaToolCall(name="propose_vault_change", json_args=json.dumps(args, ensure_ascii=False), tool_call_id="topic-proposal")}
                return
            if phase == 4:
                phase = 5
                yield {0: DeltaToolCall(name="commit_vault_change", json_args=json.dumps({"proposal_id": proposal}), tool_call_id="topic-commit")}
                return
            yield "已搜集两篇本地资料，完成整理，并由 Harness 校验写入结果。"

        events = self._stream(
            "以倾向得分匹配为主题，搜索本地资料，整理成一篇可追溯笔记并保存到 10-Inbox/倾向得分匹配-主题整理.md。",
            model=FunctionModel(stream_function=stream),
        )
        confirmation = next(item for item in events if item["type"] == "inline.confirmation.required")
        self.assertFalse((self.vault / target_path).exists())
        resumed = list(self.service.confirm_assistant_run(confirmation["runId"], True))
        self.assertEqual(resumed[-1]["type"], "run.completed")
        self.assertEqual((self.vault / target_path).read_text(encoding="utf-8"), target_content)
        all_events = events + resumed
        tools = [item.get("tool") for item in all_events if item["type"] == "tool.completed"]
        self.assertEqual(tools[:5], ["search_vault", "read_vault_note", "read_vault_note", "propose_vault_change", "commit_vault_change"])
        commit = next(item for item in all_events if item.get("tool") == "commit_vault_change" and item["type"] == "tool.completed")
        self.assertTrue(commit["result"]["verification"]["verified"])

    def test_21_all_safe_tools_are_stable_across_turns(self):
        observed: list[tuple[str, ...]] = []

        async def stream(_messages, info):
            observed.append(tuple(sorted(tool.name for tool in info.function_tools)))
            yield "完成。"

        model = FunctionModel(stream_function=stream)
        self._stream("普通解释", model=model)
        self._stream("请整理一个主题", model=model)
        self.assertEqual(observed[0], observed[1])
        self.assertTrue({
            "get_current_note", "get_current_selection", "get_conversation_focus",
            "get_recent_conversation_messages", "search_vault", "list_vault_folder",
            "read_vault_note", "find_related_notes", "get_attachment_metadata",
            "read_pdf_pages", "search_pdf", "search_public_web", "fetch_public_url",
            "ask_user", "propose_vault_change", "commit_vault_change",
        }.issubset(set(observed[0])))

    def test_22_runtime_context_contains_no_classified_intent_or_write_markers(self):
        events = self._stream("把内容整理一下")
        snapshot = self.service.assistant_runtime.persistence.load(events[0]["runId"])
        encoded = json.dumps(snapshot.request, ensure_ascii=False)
        self.assertIn("<runtime-context>", snapshot.request["prompt"])
        self.assertNotIn("write_requested", encoded)
        self.assertNotIn("commit_required", encoded)
        self.assertNotIn('"intent"', encoded)

    def test_23_folder_listing_is_real_paginated_vault_observation(self):
        folder = self.vault / "20-Knowledge/Folder"
        folder.mkdir(parents=True)
        (folder / "A.md").write_text("# A", encoding="utf-8")
        (folder / "B.md").write_text("# B", encoding="utf-8")

        async def stream(messages, _info):
            names, _ = _tool_history(messages)
            if "list_vault_folder" not in names:
                yield {0: DeltaToolCall(
                    name="list_vault_folder",
                    json_args=json.dumps({"path": "20-Knowledge/Folder", "limit": 1}),
                    tool_call_id="folder-list",
                )}
                return
            yield "已根据真实目录清单回答。"

        events = self._stream("列出这个文件夹", model=FunctionModel(stream_function=stream))
        event = next(item for item in events if item.get("tool") == "list_vault_folder" and item["type"] == "tool.completed")
        self.assertIn("1 篇笔记", event["summary"])

    def test_24_ask_user_resumes_same_run_with_answer_observation(self):
        saw_answer = False

        async def stream(messages, _info):
            nonlocal saw_answer
            names, _ = _tool_history(messages)
            if "ask_user" not in names:
                yield {0: DeltaToolCall(
                    name="ask_user",
                    json_args=json.dumps({
                        "question": "保存到哪一个目录？",
                        "options": ["10-Inbox", "20-Knowledge/Drafts"],
                        "reason": "两个目录都安全，但用途不同",
                    }, ensure_ascii=False),
                    tool_call_id="ask-1",
                )}
                return
            for message in messages:
                for part in getattr(message, "parts", []):
                    if getattr(part, "tool_name", "") == "ask_user" and "10-Inbox" in str(getattr(part, "content", "")):
                        saw_answer = True
            yield "已按你的选择继续。"

        events = self._stream("帮我整理，但目录由我选择", model=FunctionModel(stream_function=stream))
        pending = next(item for item in events if item["type"] == "inline.confirmation.required")
        self.assertEqual(pending["confirmation"]["kind"], "question")
        self.assertEqual(pending["confirmation"]["options"], ["10-Inbox", "20-Knowledge/Drafts"])
        resumed = list(self.service.confirm_assistant_run(pending["runId"], True, answer="10-Inbox"))
        self.assertTrue(saw_answer)
        self.assertEqual(resumed[-1]["type"], "run.completed")

    def test_25_scoped_session_grant_auto_allows_only_future_creates_in_root(self):
        first_path = "10-Inbox/one.md"
        first = self._stream("创建第一篇", model=_write_model(first_path, "", "# One\n"))
        pending = next(item for item in first if item["type"] == "inline.confirmation.required")
        conversation_id = pending["conversationId"]
        self.assertIn("10-Inbox", pending["confirmation"]["scope_candidates"])
        list(self.service.confirm_assistant_run(pending["runId"], True, scope="10-Inbox"))

        second_path = "10-Inbox/two.md"
        phase = 0
        proposal = ""

        async def second_stream(messages, _info):
            nonlocal phase, proposal
            _, observed = _tool_history(messages)
            proposal = observed or proposal
            if phase == 0:
                phase = 1
                yield {0: DeltaToolCall(
                    name="propose_vault_change",
                    json_args=json.dumps({
                        "title": "第二篇",
                        "writes": [{"path": second_path, "content": "# Two\n", "category": "assistant-agent"}],
                    }, ensure_ascii=False),
                    tool_call_id="second-proposal",
                )}
                return
            if phase == 1:
                phase = 2
                yield {0: DeltaToolCall(
                    name="commit_vault_change",
                    json_args=json.dumps({"proposal_id": proposal}),
                    tool_call_id="second-commit",
                )}
                return
            yield "第二篇已完成。"

        self.service.assistant_runtime.model_factory = lambda *_: FunctionModel(stream_function=second_stream)
        events = list(self.service.assistant_stream({
            "message": "创建第二篇",
            "profile_id": self.profile["id"],
            "conversation_id": conversation_id,
        }))
        self.assertNotIn("inline.confirmation.required", [item["type"] for item in events])
        self.assertTrue((self.vault / second_path).is_file())

    def test_26_absolute_path_is_denied_before_any_write(self):
        outside = Path(self.temp.name) / "outside.md"
        events = self._stream(
            "写到绝对路径",
            model=_write_model(str(outside), "", "# Escape\n"),
        )
        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertFalse(outside.exists())

    def test_27_invalid_tool_arguments_are_returned_to_model_for_recovery(self):
        phase = 0

        async def stream(_messages, _info):
            nonlocal phase
            if phase == 0:
                phase = 1
                yield {0: DeltaToolCall(
                    name="search_vault",
                    json_args=json.dumps({"limit": 5}),
                    tool_call_id="bad-search",
                )}
                return
            if phase == 1:
                phase = 2
                yield {0: DeltaToolCall(
                    name="search_vault",
                    json_args=json.dumps({"query": "Delta Method", "limit": 5}),
                    tool_call_id="good-search",
                )}
                return
            yield "已修正工具参数并完成搜索。"

        events = self._stream("搜索 Delta Method", model=FunctionModel(stream_function=stream))
        tool_events = [item for item in events if item["type"] == "tool.completed"]
        self.assertTrue(any(item["status"] == "failed" for item in tool_events))
        self.assertTrue(any(item["status"] == "completed" for item in tool_events))
        self.assertEqual(events[-1]["type"], "run.completed")

    def test_28_proposal_only_turn_does_not_require_keyword_classifier(self):
        target = "10-Inbox/preview-only.md"

        async def stream(messages, _info):
            names, _ = _tool_history(messages)
            if "propose_vault_change" not in names:
                yield {0: DeltaToolCall(
                    name="propose_vault_change",
                    json_args=json.dumps({
                        "title": "只预览",
                        "writes": [{"path": target, "content": "# Preview\n", "category": "assistant-agent"}],
                    }, ensure_ascii=False),
                    tool_call_id="preview-proposal",
                )}
                return
            yield "修改方案已生成，尚未提交。"

        events = self._stream("先给我看看方案", model=FunctionModel(stream_function=stream))
        self.assertIn("write.diff", [item["type"] for item in events])
        self.assertNotIn("inline.confirmation.required", [item["type"] for item in events])
        self.assertFalse((self.vault / target).exists())

    def test_29_vault_root_alias_lists_only_model_visible_roots(self):
        (self.vault / "01-Inbox").mkdir()
        (self.vault / "30-Learning").mkdir()
        (self.vault / "30-Learning/Plan.md").write_text(
            "# Plan\n",
            encoding="utf-8",
        )
        (self.vault / "agent").mkdir()
        (self.vault / "agent/private.md").write_text(
            "# Runtime internals\n",
            encoding="utf-8",
        )
        captured: dict = {}

        async def stream(messages, _info):
            nonlocal captured
            results = _tool_results(messages, "list_vault_folder")
            if not results:
                yield {0: DeltaToolCall(
                    name="list_vault_folder",
                    json_args=json.dumps({"path": "/"}),
                    tool_call_id="root-list",
                )}
                return
            captured = results[-1]
            yield "已列出模型可见的 Vault 顶层目录。"

        events = self._stream(
            "列出 Obsidian Vault 根目录",
            model=FunctionModel(stream_function=stream),
        )
        paths = [item["path"] for item in captured["items"]]
        self.assertEqual(captured["path"], ".")
        self.assertEqual(captured["scope"], "model-visible-vault-root")
        self.assertIn("01-Inbox", paths)
        self.assertIn("20-Knowledge", paths)
        self.assertIn("30-Learning", paths)
        self.assertNotIn("agent", paths)
        self.assertNotIn("90-Local-Only", paths)
        self.assertTrue(all(item["kind"] == "folder" for item in captured["items"]))
        root_event = next(
            item for item in events
            if item.get("tool") == "list_vault_folder"
            and item["type"] == "tool.completed"
        )
        self.assertEqual(root_event["status"], "completed")
        self.assertIn("个目录", root_event["summary"])
        self.assertEqual(events[-1]["type"], "run.completed")

    def test_30_missing_folder_is_observation_and_same_run_replans(self):
        captured_error: dict = {}
        captured_root: dict = {}

        async def stream(messages, _info):
            nonlocal captured_error, captured_root
            results = _tool_results(messages, "list_vault_folder")
            if not results:
                yield {0: DeltaToolCall(
                    name="list_vault_folder",
                    json_args=json.dumps({"path": "20-Knowledge/Missing"}),
                    tool_call_id="missing-folder",
                )}
                return
            if len(results) == 1:
                captured_error = results[0]
                yield {0: DeltaToolCall(
                    name="list_vault_folder",
                    json_args=json.dumps({"path": "."}),
                    tool_call_id="fallback-root",
                )}
                return
            captured_root = results[-1]
            yield "原目录不存在，已安全回退到 Vault 顶层目录。"

        events = self._stream(
            "检查目录；若不存在就查看 Vault 根目录",
            model=FunctionModel(stream_function=stream),
        )
        self.assertFalse(captured_error["ok"])
        self.assertEqual(
            captured_error["error"]["code"],
            "folder_not_found",
        )
        self.assertTrue(captured_error["error"]["recoverable"])
        self.assertEqual(captured_root["path"], ".")
        tool_events = [
            item for item in events
            if item.get("tool") == "list_vault_folder"
            and item["type"] == "tool.completed"
        ]
        self.assertEqual(
            [item["status"] for item in tool_events],
            ["failed", "completed"],
        )
        self.assertEqual(
            tool_events[0]["result"]["error"]["code"],
            "folder_not_found",
        )
        self.assertNotIn("run.failed", [item["type"] for item in events])
        self.assertEqual(events[-1]["type"], "run.completed")

    def test_31_unexpected_tool_value_error_still_fails_fast(self):
        original_call = self.service.tools.call

        def broken_call(*args, **kwargs):
            raise ValueError("tool_output_object_required")

        self.service.tools.call = broken_call

        async def stream(_messages, _info):
            yield {0: DeltaToolCall(
                name="search_vault",
                json_args=json.dumps({"query": "Delta Method"}),
                tool_call_id="broken-tool",
            )}

        try:
            events = self._stream(
                "搜索 Delta Method",
                model=FunctionModel(stream_function=stream),
            )
        finally:
            self.service.tools.call = original_call

        self.assertEqual(events[-1]["type"], "run.failed")
        self.assertEqual(events[-1]["code"], "ValueError")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent.core.service import AgentService
from agent.core.storage import StateStore


class PiSessionTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, store=self.store)
        self.authorization = {
            "id": "auth-tree-1",
            "sessionId": "session-tree-1",
            "runId": "run-tree-1",
            "turnId": "turn-tree-1",
            "sourceMessageId": "message-tree-1",
            "objective": "读取当前知识并回答",
            "resourceScope": {"currentNote": True, "explicitVaultPaths": [], "createRoots": ["01-Inbox"], "workspaceId": "", "projectPaths": []},
            "operationScope": ["read"],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        }
        self.service.register_task_authorization({"conversationId": "conversation-tree", "model": "fake", "taskAuthorization": self.authorization})

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_append_only_tree_pairs_tools_and_replays_events(self) -> None:
        events = [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "tool_use", "id": "call-1", "name": "search_vault", "input": {"query": "因果"}, "status": "running"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "tool_result", "id": "call-1", "name": "search_vault", "result": {"items": [{"path": "20-Knowledge/x.md"}]}, "summary": "ok", "status": "completed"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 3, "type": "text", "content": "找到一条笔记"},
        ]
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        restored = self.service.pi_run_events("run-tree-1", 1)
        self.assertEqual([item["sequence"] for item in restored["items"]], [2, 3])
        session = self.service.pi_session("session-tree-1")["session"]
        types = [item["entry_type"] for item in session["entries"]]
        self.assertEqual(types, ["task_authorization", "tool_call", "tool_result", "message"])
        self.assertEqual(session["entries"][-1]["id"], session["state"]["currentLeafId"])
        tool_result = next(item for item in session["entries"] if item["entry_type"] == "tool_result")
        self.assertNotIn("items", tool_result["payload"])
        self.assertEqual(tool_result["payload"]["callId"], "call-1")

    def test_steering_follow_up_and_cancel_are_durable(self) -> None:
        steering = self.service.control_pi_run("run-tree-1", {"type": "steering", "text": "只看统计推断"})
        follow_up = self.service.control_pi_run("run-tree-1", {"type": "follow_up", "text": "再给三道题"})
        self.assertEqual(steering["control"]["type"], "steering")
        self.assertEqual(follow_up["control"]["type"], "follow_up")
        result = self.service.cancel_pi_run("run-tree-1")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.store.get_pi_run("run-tree-1")["status"], "cancelled")
        entries = self.service.pi_session("session-tree-1")["session"]["entries"]
        self.assertEqual([item["entry_type"] for item in entries][-2:], ["steering", "follow_up"])

    def test_terminal_run_persists_idempotent_conversation_history_for_restart(self) -> None:
        events = [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "text", "content": "第一段。"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "text", "content": "第二段。"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 3, "type": "done", "status": "completed"},
        ]
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})
        self.service.append_pi_events({"runId": "run-tree-1", "events": events})

        payload = self.service.pi_session("session-tree-1")
        history = payload["history"]
        self.assertEqual(payload["schemaVersion"], 2)
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[0]["id"], "message-tree-1")
        self.assertEqual(history[0]["content"], "读取当前知识并回答")
        self.assertEqual(history[1]["id"], "pi-assistant-run-tree-1")
        self.assertEqual(history[1]["content"], "第一段。第二段。")

        metadata = {
            "piTrace": {
                "schemaVersion": 1,
                "runId": "run-tree-1",
                "status": "completed",
                "steps": [{"id": "tool:call-1", "status": "completed"}],
                "toolCalls": [{"id": "call-1", "tool": "search_vault", "status": "completed"}],
                "reasoningBlocks": [{"id": "provider-reasoning-0", "content": "本地推理"}],
            }
        }
        updated = self.service.update_message_metadata(
            "conversation-tree", "pi-assistant-run-tree-1", metadata,
        )
        self.assertEqual(updated["metadata"], metadata)
        restored = self.service.intake.get_conversation("conversation-tree")["messages"][-1]
        self.assertEqual(restored["content"], "第一段。第二段。")
        self.assertEqual(restored["metadata"], metadata)

    def test_message_metadata_is_conversation_scoped_and_bounded(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 1, "type": "text", "content": "回答"},
            {"runId": "run-tree-1", "conversationId": "conversation-tree", "sequence": 2, "type": "done", "status": "completed"},
        ]})
        other = self.service.create_conversation({"title": "其他会话"})
        with self.assertRaisesRegex(ValueError, "conversation_message_not_found"):
            self.service.update_message_metadata(other["id"], "pi-assistant-run-tree-1", {"piTrace": {}})
        with self.assertRaisesRegex(ValueError, "message_metadata_too_large"):
            self.service.update_message_metadata(
                "conversation-tree", "pi-assistant-run-tree-1", {"payload": "x" * 1_000_001},
            )


    def test_partial_run_includes_assistant_content_in_tree(self):
        # Reuse the session/run created in setUp (no explicit session creation needed).
        self.service.append_pi_events(
            {"runId": "run-tree-1", "events": [
                {"type": "text", "sequence": 1, "content": "部分产出内容"},
            ]}
        )
        session = self.service.pi_session("session-tree-1")["session"]
        entries = session["entries"]
        message_entry = next(
            (item for item in entries if item["entry_type"] == "message" and "部分产出内容" in str(item["payload"].get("content", ""))),
            None,
        )
        self.assertIsNotNone(message_entry, "partial run assistant content must appear in the session tree")
        self.assertEqual(str(session["state"]["currentLeafId"]), str(message_entry["id"]))

    def test_projection_restores_complete_tool_pair_and_aggregates_text(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "先搜索。"},
            {"sequence": 2, "type": "tool_use", "id": "call-1", "name": "search_vault", "input": {"query": "因果"}, "status": "running"},
            {"sequence": 3, "type": "tool_result", "id": "call-1", "name": "search_vault", "result": {"result": {"next_cursor": "c2"}}, "summary": "找到一页", "status": "completed"},
            {"sequence": 4, "type": "text", "content": "第一段。"},
            {"sequence": 5, "type": "text", "content": "第二段。"},
            {"sequence": 6, "type": "done", "status": "completed"},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        self.assertEqual(projected["sessionId"], "session-tree-1")
        self.assertEqual(projected["leafId"], projected["entries"][-1]["id"])
        self.assertEqual([item["role"] for item in projected["messages"]], [
            "user", "assistant", "toolResult", "assistant",
        ])
        call = projected["messages"][1]["content"][-1]
        result = projected["messages"][2]
        self.assertEqual(call, {"type": "toolCall", "id": "call-1", "name": "search_vault", "arguments": {"query": "因果"}})
        self.assertEqual(result["toolCallId"], call["id"])
        self.assertEqual(result["toolName"], call["name"])
        self.assertEqual(projected["messages"][-1]["content"], [{"type": "text", "text": "第一段。第二段。"}])
        bounded = self.service.pi_session_projection("session-tree-1", upto_sequence=3)
        self.assertEqual(bounded["leafId"], "pi-entry-run-tree-1-000000000003")
        self.assertEqual([item["role"] for item in bounded["messages"]], ["user", "assistant", "toolResult"])

    def test_projection_restores_blocked_failed_and_completed_action_references(self) -> None:
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "tool_use", "id": "blocked-1", "name": "write_note", "input": {"path": "01-Inbox/x.md"}},
            {"sequence": 2, "type": "tool_result", "id": "blocked-1", "name": "write_note", "result": {"result": {"code": "user_denied_permission"}}, "summary": "denied", "status": "blocked"},
            {"sequence": 3, "type": "tool_use", "id": "failed-1", "name": "search_vault", "input": {"query": "x"}},
            {"sequence": 4, "type": "tool_result", "id": "failed-1", "name": "search_vault", "result": {"result": {"code": "search_failed"}}, "summary": "failed", "status": "failed"},
            {"sequence": 5, "type": "tool_use", "id": "action-call", "name": "apply_vault_change", "input": {"change_set_id": "cs-1"}},
            {"sequence": 6, "type": "tool_result", "id": "action-call", "name": "apply_vault_change", "result": {"result": {"actionId": "action-done"}}, "summary": "applied", "status": "completed"},
            {"sequence": 7, "type": "action_result", "actionId": "action-done", "state": "completed"},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        results = [item for item in projected["messages"] if item["role"] == "toolResult"]
        self.assertEqual([item["details"]["status"] for item in results], ["blocked", "failed", "completed"])
        self.assertFalse(results[0]["isError"])
        self.assertTrue(results[1]["isError"])
        self.assertEqual(results[2]["details"]["actionId"], "action-done")
        self.assertEqual(projected["activeActions"], [{"id": "action-done", "status": "referenced"}])
        action_message = next(item for item in projected["messages"] if item.get("customType") == "persistedActionResult")
        self.assertEqual(action_message["details"]["state"], "completed")

    def test_projection_uses_parent_lineage_and_excludes_sibling_branch(self) -> None:
        auth_id = f"pi-entry-auth-{self.authorization['id']}"
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "text", "content": "当前分支"},
        ]})
        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO pi_session_entries(
                     id, parent_id, timestamp, entry_type, session_id, run_id,
                     turn_id, sequence, payload_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "pi-entry-sibling", auth_id, "2026-07-23T10:00:00+08:00", "message",
                    "session-tree-1", "run-tree-1", "turn-tree-1", 99,
                    json.dumps({"sequence": 99, "type": "text", "content": "兄弟分支"}, ensure_ascii=False),
                ),
            )
            self.store.connection.commit()
        current = self.service.pi_session_projection("session-tree-1")
        sibling = self.service.pi_session_projection("session-tree-1", leaf_id="pi-entry-sibling")
        self.assertNotIn("兄弟分支", json.dumps(current, ensure_ascii=False))
        self.assertNotIn("当前分支", json.dumps(sibling, ensure_ascii=False))
        self.assertIn("兄弟分支", json.dumps(sibling["messages"], ensure_ascii=False))

    def test_projection_includes_persisted_branch_metadata(self) -> None:
        forked = {
            **self.authorization,
            "id": "auth-tree-fork",
            "runId": "run-tree-fork",
            "turnId": "turn-tree-fork",
            "sourceMessageId": "message-tree-fork",
            "objective": "分支继续",
            "parentRunId": "run-tree-1",
            "forkedFromSequence": 1,
        }
        self.service.register_task_authorization({
            "conversationId": "conversation-tree",
            "model": "fake",
            "taskAuthorization": forked,
        })
        projected = self.service.pi_session_projection("session-tree-1", run_id="run-tree-fork")
        branch = projected["messages"][0]
        self.assertEqual(projected["branchId"], "run-tree-fork")
        self.assertEqual(branch["role"], "branchSummary")
        self.assertEqual(branch["fromId"], "run-tree-1")
        self.assertIn("event boundary 1", branch["summary"])

    def test_projection_restores_controls_and_interrupts_orphaned_call(self) -> None:
        self.service.control_pi_run("run-tree-1", {"type": "steering", "text": "只看主线"})
        self.service.control_pi_run("run-tree-1", {"type": "follow_up", "text": "完成后出题"})
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "tool_use", "id": "orphan-1", "name": "search_vault", "input": {"query": "x"}},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        controls = [item["customType"] for item in projected["messages"] if item["role"] == "custom"]
        self.assertEqual(controls[:2], ["steering", "follow_up"])
        interrupted = next(item for item in projected["messages"] if item["role"] == "toolResult")
        self.assertEqual(interrupted["toolCallId"], "orphan-1")
        self.assertEqual(interrupted["toolName"], "search_vault")
        self.assertEqual(interrupted["details"]["code"], "tool_call_interrupted_by_restart")

    def test_projection_excludes_reasoning_and_redacts_api_keys(self) -> None:
        secret = "sk-verysecret123456"
        self.service.append_pi_events({"runId": "run-tree-1", "events": [
            {"sequence": 1, "type": "reasoning", "content": f"private reasoning {secret}"},
            {"sequence": 2, "type": "tool_use", "id": "call-secret", "name": "search_vault", "input": {"apiKey": secret, "accessToken": "opaque-value", "query": "safe"}},
        ]})
        projected = self.service.pi_session_projection("session-tree-1")
        encoded = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("private reasoning", encoded)
        assistant = next(item for item in projected["messages"] if item["role"] == "assistant")
        self.assertEqual(assistant["content"][0]["arguments"]["apiKey"], "[redacted]")
        self.assertEqual(assistant["content"][0]["arguments"]["accessToken"], "[redacted]")


if __name__ == "__main__":
    unittest.main()

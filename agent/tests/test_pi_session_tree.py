from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()

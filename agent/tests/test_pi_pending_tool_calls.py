from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.service import AgentService
from agent.core.storage import StateStore


class PiPendingToolCallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, store=self.store)
        self.authorization = {
            "id": "auth-pending-1",
            "sessionId": "session-pending-1",
            "runId": "run-pending-1",
            "turnId": "turn-pending-1",
            "sourceMessageId": "message-pending-1",
            "objective": "写入前需要授权",
            "resourceScope": {"currentNote": True, "explicitVaultPaths": [], "createRoots": ["01-Inbox"], "workspaceId": "", "projectPaths": []},
            "operationScope": ["read", "write"],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        }
        self.service.register_task_authorization({"conversationId": "conversation-pending", "model": "fake", "taskAuthorization": self.authorization})

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _body(self, **overrides) -> dict:
        body = {
            "toolCallId": "call-1",
            "toolName": "create_note",
            "turnId": "turn-pending-1",
            "sessionId": "session-pending-1",
            "taskAuthorizationId": "auth-pending-1",
            "arguments": {"path": "01-Inbox/新知识.md", "content": "正文"},
            "permissionRequest": {"toolCallId": "call-1", "capability": "vault_write", "message": "需要写入"},
            "state": "pending",
        }
        body.update(overrides)
        return body

    def test_save_then_recover_pending_by_run_and_session(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        by_run = self.service.get_pending_tool_calls(run_id="run-pending-1")["items"]
        self.assertEqual(len(by_run), 1)
        record = by_run[0]
        self.assertEqual(record["toolCallId"], "call-1")
        self.assertEqual(record["toolName"], "create_note")
        self.assertEqual(record["arguments"]["path"], "01-Inbox/新知识.md")
        self.assertEqual(record["taskAuthorizationId"], "auth-pending-1")
        self.assertEqual(record["state"], "pending")
        # A fresh runtime after restart only knows the session id.
        by_session = self.service.get_pending_tool_calls(session_id="session-pending-1")["items"]
        self.assertEqual([item["toolCallId"] for item in by_session], ["call-1"])

    def test_idempotent_save_preserves_created_at_and_updates_state(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        created = self.service.get_pending_tool_calls(run_id="run-pending-1")["items"][0]["createdAt"]
        self.service.save_pending_tool_call("run-pending-1", self._body(state="interrupted"))
        items = self.service.get_pending_tool_calls(run_id="run-pending-1")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["createdAt"], created)
        self.assertEqual(items[0]["state"], "interrupted")

    def test_resolve_marks_terminal_and_drops_from_active(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        self.service.resolve_pending_tool_call("run-pending-1", "call-1", "allowed")
        active = self.service.get_pending_tool_calls(run_id="run-pending-1")["items"]
        self.assertEqual(active, [])
        everything = self.service.get_pending_tool_calls(run_id="run-pending-1", active_only=False)["items"]
        self.assertEqual(len(everything), 1)
        self.assertEqual(everything[0]["state"], "allowed")
        self.assertIsNotNone(everything[0]["resolvedAt"])

    def test_denied_call_is_not_replayed_as_active(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        self.service.resolve_pending_tool_call("run-pending-1", "call-1", "denied")
        self.assertEqual(self.service.get_pending_tool_calls(session_id="session-pending-1")["items"], [])

    def test_invalid_state_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save_pending_tool_call("run-pending-1", self._body(state="bogus"))
        self.service.save_pending_tool_call("run-pending-1", self._body())
        with self.assertRaises(ValueError):
            self.service.resolve_pending_tool_call("run-pending-1", "call-1", "bogus")

    def test_missing_required_fields_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save_pending_tool_call("run-pending-1", self._body(toolCallId=""))
        with self.assertRaises(ValueError):
            self.service.save_pending_tool_call("run-pending-1", self._body(toolName=""))

    def test_unknown_run_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save_pending_tool_call("run-missing", self._body())


if __name__ == "__main__":
    unittest.main()

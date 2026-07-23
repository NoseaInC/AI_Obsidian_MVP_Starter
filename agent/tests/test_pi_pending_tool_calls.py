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
            "toolName": "plan_vault_change",
            "turnId": "turn-pending-1",
            "sessionId": "session-pending-1",
            "taskAuthorizationId": "auth-pending-1",
            "arguments": {
                "title": "新知识",
                "writes": [{"path": "01-Inbox/新知识.md", "content": "正文"}],
            },
            "permissionRequest": {
                "type": "vault_writes",
                "writes": [{"path": "01-Inbox/新知识.md"}],
                "message": "需要写入",
            },
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
        self.assertEqual(record["toolName"], "plan_vault_change")
        self.assertEqual(record["arguments"]["writes"][0]["path"], "01-Inbox/新知识.md")
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

    def test_recovery_validation_returns_original_identity_without_new_run(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        result = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertTrue(result["recoverable"])
        self.assertEqual(result["record"]["runId"], "run-pending-1")
        self.assertEqual(result["record"]["turnId"], "turn-pending-1")
        self.assertEqual(result["taskAuthorization"]["id"], "auth-pending-1")
        self.assertEqual(result["record"]["recoveryGuard"]["version"], 1)

    def test_expired_or_non_reversible_authorization_cannot_recover(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE task_authorizations SET status='expired' WHERE id='auth-pending-1'"
            )
            self.store.connection.commit()
        expired = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertFalse(expired["recoverable"])
        self.assertEqual(expired["code"], "task_authorization_expired")
        self.assertEqual(
            self.service.get_pending_tool_calls(session_id="session-pending-1")["items"],
            [],
        )

        # A separately reset record still fails if reversibility changed.
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE task_authorizations SET status='active', reversible_only=0 WHERE id='auth-pending-1'"
            )
            self.store.connection.execute(
                "UPDATE pi_pending_tool_calls SET state='pending', resolved_at=NULL WHERE run_id='run-pending-1'"
            )
            self.store.connection.commit()
        unsafe = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertFalse(unsafe["recoverable"])
        self.assertEqual(unsafe["code"], "task_authorization_not_reversible")

    def test_stale_vault_hash_and_target_collision_fail_closed(self) -> None:
        target = self.vault / "01-Inbox/新知识.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nstatus: ai-draft\nagent_access: allowed\n---\n\n第一版\n", encoding="utf-8")
        self.service.save_pending_tool_call("run-pending-1", self._body())
        target.write_text("---\nstatus: ai-draft\nagent_access: allowed\n---\n\n用户改过\n", encoding="utf-8")
        stale = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertFalse(stale["recoverable"])
        self.assertEqual(stale["code"], "pi_pending_recovery_state_changed")

    def test_removed_tool_contract_and_workspace_are_not_recoverable(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        original_contracts = self.service.tool_contracts
        self.service.tool_contracts = lambda: {"schemaVersion": 1, "items": []}
        missing_contract = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertFalse(missing_contract["recoverable"])
        self.assertEqual(missing_contract["code"], "pi_pending_tool_contract_missing")
        self.service.tool_contracts = original_contracts

        with self.store.lock:
            self.store.connection.execute("DELETE FROM pi_pending_tool_calls")
            self.store.connection.commit()
        workspace_id = "workspace-pending"
        worktree = self.service.developer_workspace.root / workspace_id / "worktree"
        worktree.mkdir(parents=True)
        self.store.set_setting(f"developer_workspace:{workspace_id}", {
            "id": workspace_id,
            "runId": "run-pending-1",
            "path": str(worktree),
            "project": str(self.vault),
            "status": "active",
        })
        workspace_body = self._body(
            toolCallId="workspace-call",
            toolName="write_workspace_file",
            arguments={"workspace_id": workspace_id, "path": "README.md", "content": "x"},
            permissionRequest={
                "type": "developer_workspace",
                "capability": {"type": "developer_workspace", "workspaceId": workspace_id},
                "message": "需要开发工作区权限",
            },
        )
        self.service.save_pending_tool_call("run-pending-1", workspace_body)
        worktree.rmdir()
        missing_workspace = self.service.validate_pending_tool_call_recovery(
            "run-pending-1", "workspace-call",
        )
        self.assertFalse(missing_workspace["recoverable"])
        self.assertEqual(missing_workspace["code"], "developer_workspace_unavailable")

    def test_organization_protection_or_collision_change_blocks_recovery(self) -> None:
        source = self.vault / "01-Inbox/source.md"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("---\nstatus: ai-draft\nagent_access: allowed\n---\n\n正文\n", encoding="utf-8")
        organization = {
            "directories": ["20-Knowledge/Drafts/整理"],
            "moves": [{
                "source_path": "01-Inbox/source.md",
                "target_path": "20-Knowledge/Drafts/整理/source.md",
            }],
            "remove_empty_source_dirs": False,
        }
        body = self._body(
            toolCallId="organize-call",
            toolName="organize_vault_notes",
            arguments={"title": "整理", **organization},
            permissionRequest={
                "type": "vault_organization",
                "organization": organization,
                "message": "需要整理权限",
            },
        )
        self.service.save_pending_tool_call("run-pending-1", body)
        destination = self.vault / "20-Knowledge/Drafts/整理/source.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("用户创建的目标", encoding="utf-8")
        collision = self.service.validate_pending_tool_call_recovery(
            "run-pending-1", "organize-call",
        )
        self.assertFalse(collision["recoverable"])
        self.assertEqual(collision["code"], "vault_move_target_collision")

    def test_persisted_tool_result_completes_pending_without_replay(self) -> None:
        self.service.save_pending_tool_call("run-pending-1", self._body())
        self.service.append_pi_events({"runId": "run-pending-1", "events": [{
            "sequence": 1,
            "type": "tool_result",
            "id": "call-1",
            "name": "plan_vault_change",
            "result": {"result": {"actionId": "action-1"}},
            "status": "completed",
        }]})
        result = self.service.validate_pending_tool_call_recovery("run-pending-1", "call-1")
        self.assertFalse(result["recoverable"])
        self.assertEqual(result["code"], "pi_pending_tool_call_already_completed")
        record = self.service.get_pending_tool_calls(
            run_id="run-pending-1", active_only=False,
        )["items"][0]
        self.assertEqual(record["state"], "completed")

    def test_projection_keeps_active_pending_call_without_synthetic_interruption(self) -> None:
        body = self._body()
        self.service.append_pi_events({"runId": "run-pending-1", "events": [{
            "sequence": 1,
            "type": "tool_use",
            "id": "call-1",
            "name": "plan_vault_change",
            "input": body["arguments"],
            "status": "running",
        }]})
        self.service.save_pending_tool_call("run-pending-1", body)
        projected = self.service.pi_session_projection("session-pending-1")
        matching = [
            message for message in projected["messages"]
            if message.get("role") == "toolResult" and message.get("toolCallId") == "call-1"
        ]
        self.assertEqual(matching, [])
        call = next(
            block
            for message in projected["messages"] if message.get("role") == "assistant"
            for block in message["content"] if block.get("type") == "toolCall"
        )
        self.assertEqual(call["id"], projected["pending"]["toolCallId"])


if __name__ == "__main__":
    unittest.main()

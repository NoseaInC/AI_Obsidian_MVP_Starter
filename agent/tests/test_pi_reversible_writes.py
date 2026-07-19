from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core.reversible_transaction import ReversibleTransactionService
from agent.core.storage import StateStore
from agent.core.task_authorization import TaskAuthorizationService
from agent.core.vault_autonomy import VaultAutonomyService
from agent.tools.change_set import ChangeSetTools


class PiReversibleWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name)
        for relative in ("01-Inbox", "20-Knowledge/Drafts", "20-Knowledge/Concepts", "90-Local-Only/Agent"):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        autonomy = VaultAutonomyService(self.vault, self.store)
        self.authorizations = TaskAuthorizationService(self.vault, self.store, autonomy.classify)
        self.change_sets = ChangeSetTools(self.vault, self.store)
        self.transactions = ReversibleTransactionService(self.vault, self.store, self.authorizations)
        self.authorization_id = "authorization-test"
        self.authorizations.create({
            "id": self.authorization_id,
            "sessionId": "session-test",
            "runId": "run-test",
            "turnId": "turn-test",
            "sourceMessageId": "message-test",
            "objective": "整理当前用户给出的知识",
            "resourceScope": {
                "currentNote": False,
                "explicitVaultPaths": [],
                "createRoots": [],
                "workspaceId": "",
                "projectPaths": [],
            },
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def plan(self, writes: list[dict[str, str]]) -> dict:
        result = self.change_sets.create({
            "run_id": "run-test",
            "task_authorization_id": self.authorization_id,
            "title": "测试写入",
            "writes": writes,
        })
        _, bundle = self.change_sets.prepared(result["id"])
        self.authorizations.plan(self.authorization_id, list(bundle["writes"]))
        return result

    def apply(self, change_set_id: str) -> dict:
        return self.transactions.apply_change_set(self.change_sets, {
            "change_set_id": change_set_id,
            "task_authorization_id": self.authorization_id,
            "turn_id": "turn-test",
            "source_message_id": "message-test",
        })

    def test_current_task_creates_verifies_diffs_and_undoes_without_confirmation(self) -> None:
        planned = self.plan([{"path": "01-Inbox/Delta.md", "content": "# Delta\n\n结论"}])
        self.assertEqual(planned["state"], "planned")
        self.assertFalse(planned["requires_confirmation"])
        result = self.apply(planned["id"])
        self.assertEqual(result["state"], "applied")
        self.assertTrue(result["verification"]["verified"])
        self.assertTrue(result["undoAvailable"])
        diff = self.transactions.diff(result["actionId"])
        self.assertIn("+# Delta", diff["files"][0]["diff"])
        self.transactions.undo(result["actionId"])
        self.assertFalse((self.vault / "01-Inbox/Delta.md").exists())

    def test_authorized_existing_note_update_restores_exact_snapshot(self) -> None:
        target = self.vault / "01-Inbox/Existing.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Existing.md", "content": "after"}])
        action = self.apply(planned["id"])
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        self.transactions.undo(action["actionId"])
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_repeated_apply_is_idempotent(self) -> None:
        planned = self.plan([{"path": "01-Inbox/Once.md", "content": "once"}])
        first = self.apply(planned["id"])
        second = self.apply(planned["id"])
        self.assertEqual(second["actionId"], first["actionId"])
        self.assertTrue(second["idempotent"])

    def test_reviewed_core_is_never_redirected_or_confirmed(self) -> None:
        target = self.vault / "20-Knowledge/Concepts/Core.md"
        target.write_text("---\nstatus: core\n---\n\n# Core\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "reviewed_core_read_only"):
            self.plan([{"path": "20-Knowledge/Concepts/Core.md", "content": "overwrite"}])
        self.assertIn("# Core", target.read_text(encoding="utf-8"))

    def test_create_outside_explicit_safe_roots_is_denied(self) -> None:
        with self.assertRaisesRegex(PermissionError, "task_create_root_requires_scope_expansion"):
            self.plan([{"path": "20-Knowledge/Concepts/New.md", "content": "new"}])

    def test_second_plan_cannot_silently_expand_established_turn_scope(self) -> None:
        first = self.plan([{"path": "01-Inbox/First.md", "content": "first"}])
        self.assertEqual(first["state"], "planned")
        with self.assertRaisesRegex(PermissionError, "task_scope_expansion_requires_new_user_turn"):
            self.plan([{"path": "20-Knowledge/Drafts/Second.md", "content": "second"}])
        self.assertFalse((self.vault / "20-Knowledge/Drafts/Second.md").exists())

    def test_path_escape_and_symlink_escape_are_denied(self) -> None:
        with self.assertRaises(ValueError):
            self.plan([{"path": "../escape.md", "content": "no"}])
        outside = self.vault.parent / "outside-pi-write-test"
        outside.mkdir(exist_ok=True)
        link = self.vault / "01-Inbox/link"
        link.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaises(ValueError):
                self.plan([{"path": "01-Inbox/link/escape.md", "content": "no"}])
        finally:
            link.unlink(missing_ok=True)

    def test_stale_base_hash_refuses_apply(self) -> None:
        target = self.vault / "01-Inbox/Stale.md"
        target.write_text("base\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Stale.md", "content": "planned"}])
        target.write_text("human edit\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "change_set_base_changed"):
            self.apply(planned["id"])
        self.assertEqual(target.read_text(encoding="utf-8"), "human edit\n")

    def test_multifile_failure_rolls_back_every_target(self) -> None:
        first = self.vault / "01-Inbox/A.md"
        second = self.vault / "01-Inbox/B.md"
        first.write_text("A0\n", encoding="utf-8")
        second.write_text("B0\n", encoding="utf-8")
        planned = self.plan([
            {"path": "01-Inbox/A.md", "content": "A1"},
            {"path": "01-Inbox/B.md", "content": "B1"},
        ])
        from agent.core import reversible_transaction as module
        original = module._atomic_write

        def fail_second_target(path: Path, body: bytes) -> None:
            if path.name == "B.md" and "Snapshots" not in path.parts:
                raise OSError("injected failure")
            original(path, body)

        with patch.object(module, "_atomic_write", side_effect=fail_second_target):
            with self.assertRaisesRegex(OSError, "injected failure"):
                self.apply(planned["id"])
        self.assertEqual(first.read_text(encoding="utf-8"), "A0\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B0\n")

    def test_undo_conflict_preserves_human_edit(self) -> None:
        target = self.vault / "01-Inbox/Conflict.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Conflict.md", "content": "agent"}])
        action = self.apply(planned["id"])
        target.write_text("human after agent\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "vault_target_changed_since_action"):
            self.transactions.undo(action["actionId"])
        self.assertEqual(target.read_text(encoding="utf-8"), "human after agent\n")

    def test_tampered_snapshot_refuses_undo(self) -> None:
        target = self.vault / "01-Inbox/Snapshot.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Snapshot.md", "content": "after"}])
        action = self.apply(planned["id"])
        saved = self.store.get_agent_action(action["actionId"])
        reference = saved["snapshots"][0]["snapshot_reference"]
        (self.vault / "90-Local-Only/Agent" / reference).write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "snapshot_integrity_failed"):
            self.transactions.undo(action["actionId"])
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_tampered_diff_is_rejected(self) -> None:
        planned = self.plan([{"path": "01-Inbox/Diff.md", "content": "after"}])
        action = self.apply(planned["id"])
        saved = self.store.get_agent_action(action["actionId"])
        reference = saved["details"]["diffReference"]
        (self.vault / "90-Local-Only/Agent" / reference).write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "action_diff_tampered"):
            self.transactions.diff(action["actionId"])

    def test_run_completion_expires_authorization(self) -> None:
        self.store.complete_pi_run("run-test", "completed")
        with self.assertRaisesRegex(PermissionError, "task_authorization_expired"):
            self.authorizations.plan(
                self.authorization_id,
                [{"path": "01-Inbox/Late.md", "content": "late"}],
            )


if __name__ == "__main__":
    unittest.main()

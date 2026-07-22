from __future__ import annotations

import hashlib
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
        self.authorizations.grant_scope(
            self.authorization_id,
            "run-test",
            [{"path": item["path"]} for item in writes],
            "once",
        )
        self.authorizations.plan(self.authorization_id, list(bundle["writes"]))
        return result

    def organize(self, organization: dict, *, grant: bool = True) -> dict:
        if grant:
            self.authorizations.grant_organization(
                self.authorization_id, "run-test", organization, "once",
            )
        return self.transactions.organize({
            "task_authorization_id": self.authorization_id,
            "run_id": "run-test",
            "turn_id": "turn-test",
            "source_message_id": "message-test",
            **organization,
        })

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
        self.transactions.undo(result["actionId"], user_confirmed=True)
        self.assertFalse((self.vault / "01-Inbox/Delta.md").exists())

    def test_authorized_existing_note_update_restores_exact_snapshot(self) -> None:
        target = self.vault / "01-Inbox/Existing.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Existing.md", "content": "after"}])
        action = self.apply(planned["id"])
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        self.transactions.undo(action["actionId"], user_confirmed=True)
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

    def test_reviewed_core_frontmatter_variants_are_protected(self) -> None:
        variants = {
            "inline-comment": "---\nstatus: reviewed # confirmed by user\n---\n\n# Reviewed\n",
            "quoted": "---\nstatus: 'core' # confirmed by user\n---\n\n# Core\n",
            "bom": "\ufeff---\nstatus: \"reviewed\"\n---\n\n# Reviewed\n",
        }
        for name, body in variants.items():
            with self.subTest(name=name):
                source = self.vault / f"01-Inbox/{name}.md"
                source.write_text(body, encoding="utf-8")
                with self.assertRaisesRegex(PermissionError, "reviewed_core_read_only"):
                    self.authorizations.grant_organization(
                        self.authorization_id,
                        "run-test",
                        {"moves": [{
                            "source_path": f"01-Inbox/{name}.md",
                            "target_path": f"20-Knowledge/Drafts/{name}.md",
                        }]},
                    )
                self.assertEqual(source.read_text(encoding="utf-8"), body)

    def test_invalid_utf8_markdown_is_fail_closed_for_updates(self) -> None:
        target = self.vault / "20-Knowledge/Concepts/Invalid-Encoding.md"
        original = b"\xff\xfeuntrusted markdown bytes\n"
        target.write_bytes(original)
        with self.assertRaisesRegex(PermissionError, "reviewed_core_read_only"):
            self.authorizations.grant_scope(
                self.authorization_id,
                "run-test",
                [{"path": "20-Knowledge/Concepts/Invalid-Encoding.md"}],
                "once",
            )
        self.assertEqual(target.read_bytes(), original)

    def test_organization_rechecks_exact_source_bytes_after_classification(self) -> None:
        source = self.vault / "01-Inbox/Becomes-Reviewed.md"
        source.write_text("---\nstatus: ai-draft\n---\n\n# Draft\n", encoding="utf-8")
        organization = {
            "moves": [{
                "source_path": "01-Inbox/Becomes-Reviewed.md",
                "target_path": "20-Knowledge/Drafts/Becomes-Reviewed.md",
            }],
        }
        self.authorizations.grant_organization(
            self.authorization_id, "run-test", organization, "once",
        )
        original_validate = self.authorizations.validate_organization
        validation_count = 0

        def become_reviewed_after_validation(*args, **kwargs):
            nonlocal validation_count
            result = original_validate(*args, **kwargs)
            validation_count += 1
            if validation_count == 2:
                source.write_text(
                    "---\nstatus: reviewed # user confirmed\n---\n\n# Reviewed\n",
                    encoding="utf-8",
                )
            return result

        with patch.object(
            self.authorizations,
            "validate_organization",
            side_effect=become_reviewed_after_validation,
        ):
            with self.assertRaisesRegex(PermissionError, "reviewed_core_read_only"):
                self.organize(organization, grant=False)
        self.assertTrue(source.is_file())
        self.assertFalse((self.vault / "20-Knowledge/Drafts/Becomes-Reviewed.md").exists())

    def test_organization_preserves_edit_made_after_destination_verification(self) -> None:
        source = self.vault / "01-Inbox/Human-Race.md"
        source.write_text("original\n", encoding="utf-8")
        organization = {
            "moves": [{
                "source_path": "01-Inbox/Human-Race.md",
                "target_path": "20-Knowledge/Drafts/Human-Race.md",
            }],
        }
        self.authorizations.grant_organization(
            self.authorization_id, "run-test", organization, "once",
        )
        original_unlink = self.transactions._unlink_unchanged_source

        def edit_before_final_compare(
            path: Path,
            expected_hash: str,
            stage: Path,
        ) -> None:
            path.write_text("human edit after verification\n", encoding="utf-8")
            original_unlink(path, expected_hash, stage)

        with patch.object(
            self.transactions,
            "_unlink_unchanged_source",
            side_effect=edit_before_final_compare,
        ):
            with self.assertRaisesRegex(RuntimeError, "vault_move_source_changed"):
                self.organize(organization, grant=False)
        self.assertEqual(source.read_text(encoding="utf-8"), "human edit after verification\n")
        self.assertFalse((self.vault / "20-Knowledge/Drafts/Human-Race.md").exists())

    def test_organization_never_replaces_concurrent_destination(self) -> None:
        source = self.vault / "01-Inbox/Concurrent-Destination.md"
        source.write_text("agent source\n", encoding="utf-8")
        destination = self.vault / "20-Knowledge/Drafts/Concurrent-Destination.md"
        organization = {
            "moves": [{
                "source_path": "01-Inbox/Concurrent-Destination.md",
                "target_path": "20-Knowledge/Drafts/Concurrent-Destination.md",
            }],
        }
        self.authorizations.grant_organization(
            self.authorization_id, "run-test", organization, "once",
        )
        from agent.core import reversible_transaction as module
        original = module._exclusive_write

        def human_wins_race(path: Path, body: bytes) -> None:
            if path.name == "Concurrent-Destination.md":
                path.write_text("human concurrent note\n", encoding="utf-8")
            original(path, body)

        with patch.object(module, "_exclusive_write", side_effect=human_wins_race):
            with self.assertRaisesRegex(FileExistsError, "vault_move_target_collision"):
                self.organize(organization, grant=False)
        self.assertEqual(source.read_text(encoding="utf-8"), "agent source\n")
        self.assertEqual(destination.read_text(encoding="utf-8"), "human concurrent note\n")

    def test_organization_rollback_never_replaces_concurrent_source(self) -> None:
        source = self.vault / "01-Inbox/Rollback-Source.md"
        from agent.core import reversible_transaction as module
        original = module._exclusive_write

        def human_recreates_source(path: Path, body: bytes) -> None:
            if path.name == "Rollback-Source.md":
                path.write_text("human recreated source\n", encoding="utf-8")
            original(path, body)

        with patch.object(module, "_exclusive_write", side_effect=human_recreates_source):
            restored = self.transactions._rollback_organization(
                {"01-Inbox/Rollback-Source.md": b"agent original\n"},
                {},
                [],
                self.vault / "90-Local-Only/Agent/Snapshots/test-source-race",
            )
        self.assertFalse(restored)
        self.assertEqual(source.read_text(encoding="utf-8"), "human recreated source\n")

    def test_organization_rollback_never_deletes_concurrent_destination_edit(self) -> None:
        destination = self.vault / "20-Knowledge/Drafts/Rollback-Target.md"
        destination.write_text("agent target\n", encoding="utf-8")
        expected = hashlib.sha256(b"agent target\n").hexdigest()
        from agent.core import reversible_transaction as module
        original = module._stage_expected_file

        def human_edits_before_atomic_take(path: Path, expected_hash: str, stage: Path) -> bytes:
            if path.name == "Rollback-Target.md":
                path.write_text("human destination edit\n", encoding="utf-8")
            return original(path, expected_hash, stage)

        with patch.object(module, "_stage_expected_file", side_effect=human_edits_before_atomic_take):
            restored = self.transactions._rollback_organization(
                {},
                {"20-Knowledge/Drafts/Rollback-Target.md": expected},
                [],
                self.vault / "90-Local-Only/Agent/Snapshots/test-target-race",
            )
        self.assertFalse(restored)
        self.assertEqual(destination.read_text(encoding="utf-8"), "human destination edit\n")

    def test_request_mode_requires_explicit_scope_before_first_write(self) -> None:
        writes = [{"path": "20-Knowledge/Concepts/New.md", "content": "new"}]
        result = self.change_sets.create({
            "run_id": "run-test",
            "task_authorization_id": self.authorization_id,
            "title": "请求权限",
            "writes": writes,
        })
        _, bundle = self.change_sets.prepared(result["id"])
        with self.assertRaisesRegex(PermissionError, "task_create_scope_required"):
            self.authorizations.plan(self.authorization_id, list(bundle["writes"]))
        self.authorizations.grant_scope(
            self.authorization_id, "run-test", [{"path": writes[0]["path"]}], "once",
        )
        self.authorizations.plan(self.authorization_id, list(bundle["writes"]))

    def test_same_run_can_expand_an_exact_second_scope_after_user_grant(self) -> None:
        first = self.plan([{"path": "01-Inbox/First.md", "content": "first"}])
        self.assertEqual(first["state"], "planned")
        writes = [{"path": "20-Knowledge/Drafts/Second.md", "content": "second"}]
        second = self.change_sets.create({
            "run_id": "run-test",
            "task_authorization_id": self.authorization_id,
            "title": "第二次精确授权",
            "writes": writes,
        })
        _, bundle = self.change_sets.prepared(second["id"])
        with self.assertRaisesRegex(PermissionError, "task_create_scope_required"):
            self.authorizations.plan(self.authorization_id, list(bundle["writes"]))
        self.assertFalse((self.vault / "20-Knowledge/Drafts/Second.md").exists())
        self.authorizations.grant_scope(
            self.authorization_id, "run-test", [{"path": writes[0]["path"]}], "once",
        )
        self.authorizations.plan(self.authorization_id, list(bundle["writes"]))

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
        original = module._exclusive_write

        def fail_second_target(path: Path, body: bytes) -> None:
            if path.name == "B.md":
                raise OSError("injected failure")
            original(path, body)

        with patch.object(module, "_exclusive_write", side_effect=fail_second_target):
            with self.assertRaisesRegex(OSError, "injected failure"):
                self.apply(planned["id"])
        self.assertEqual(first.read_text(encoding="utf-8"), "A0\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B0\n")

    def test_apply_rollback_preserves_concurrent_human_edit(self) -> None:
        first = self.vault / "01-Inbox/Concurrent-A.md"
        second = self.vault / "01-Inbox/Concurrent-B.md"
        first.write_text("A0\n", encoding="utf-8")
        second.write_text("B0\n", encoding="utf-8")
        planned = self.plan([
            {"path": "01-Inbox/Concurrent-A.md", "content": "A1"},
            {"path": "01-Inbox/Concurrent-B.md", "content": "B1"},
        ])
        from agent.core import reversible_transaction as module
        original = module._exclusive_write

        def edit_first_then_fail_second(path: Path, body: bytes) -> None:
            if path.name == "Concurrent-B.md":
                first.write_text("human edit during apply\n", encoding="utf-8")
                raise OSError("injected failure after human edit")
            original(path, body)

        with patch.object(
            module,
            "_exclusive_write",
            side_effect=edit_first_then_fail_second,
        ):
            with self.assertRaisesRegex(OSError, "injected failure after human edit"):
                self.apply(planned["id"])
        self.assertEqual(first.read_text(encoding="utf-8"), "human edit during apply\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B0\n")

    def test_organization_failure_restores_sources_and_removes_partial_targets(self) -> None:
        first = self.vault / "01-Inbox/Move-A.md"
        second = self.vault / "01-Inbox/Move-B.md"
        first.write_text("A\n", encoding="utf-8")
        second.write_text("B\n", encoding="utf-8")
        organization = {
            "moves": [
                {
                    "source_path": "01-Inbox/Move-A.md",
                    "target_path": "20-Knowledge/Concepts/Nested/Move-A.md",
                },
                {
                    "source_path": "01-Inbox/Move-B.md",
                    "target_path": "20-Knowledge/Concepts/Nested/Move-B.md",
                },
            ],
            "remove_empty_source_dirs": False,
        }
        self.authorizations.grant_organization(
            self.authorization_id, "run-test", organization, "once",
        )
        from agent.core import reversible_transaction as module
        original = module._exclusive_write
        failed = False

        def fail_second_destination(path: Path, body: bytes) -> None:
            nonlocal failed
            if not failed and path.as_posix().endswith("Concepts/Nested/Move-B.md"):
                failed = True
                raise OSError("injected organization failure")
            original(path, body)

        with patch.object(module, "_exclusive_write", side_effect=fail_second_destination):
            with self.assertRaisesRegex(OSError, "injected organization failure"):
                self.organize(organization, grant=False)
        self.assertEqual(first.read_text(encoding="utf-8"), "A\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B\n")
        self.assertFalse((self.vault / "20-Knowledge/Concepts/Nested").exists())

    def test_organization_collision_and_path_escape_are_preflight_only(self) -> None:
        source = self.vault / "01-Inbox/Source.md"
        source.write_text("source\n", encoding="utf-8")
        collision = self.vault / "20-Knowledge/Concepts/Collision.md"
        collision.write_text("human\n", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "vault_move_target_collision"):
            self.authorizations.grant_organization(
                self.authorization_id,
                "run-test",
                {"moves": [{
                    "source_path": "01-Inbox/Source.md",
                    "target_path": "20-Knowledge/Concepts/Collision.md",
                }]},
            )
        self.assertEqual(source.read_text(encoding="utf-8"), "source\n")
        self.assertEqual(collision.read_text(encoding="utf-8"), "human\n")
        with self.assertRaises(ValueError):
            self.authorizations.grant_organization(
                self.authorization_id,
                "run-test",
                {"moves": [{
                    "source_path": "01-Inbox/Source.md",
                    "target_path": "../escape.md",
                }]},
            )

    def test_organization_symlink_escape_is_denied(self) -> None:
        source = self.vault / "01-Inbox/Symlink-Source.md"
        source.write_text("safe\n", encoding="utf-8")
        outside = self.vault.parent / "outside-pi-organization-test"
        outside.mkdir(exist_ok=True)
        link = self.vault / "20-Knowledge/Concepts/link"
        link.symlink_to(outside, target_is_directory=True)
        try:
            with self.assertRaisesRegex(ValueError, "symlink_path_not_allowed"):
                self.authorizations.grant_organization(
                    self.authorization_id,
                    "run-test",
                    {"moves": [{
                        "source_path": "01-Inbox/Symlink-Source.md",
                        "target_path": "20-Knowledge/Concepts/link/escape.md",
                    }]},
                )
            self.assertFalse((outside / "escape.md").exists())
            self.assertEqual(source.read_text(encoding="utf-8"), "safe\n")
        finally:
            link.unlink(missing_ok=True)

    def test_undo_conflict_preserves_human_edit(self) -> None:
        target = self.vault / "01-Inbox/Conflict.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Conflict.md", "content": "agent"}])
        action = self.apply(planned["id"])
        target.write_text("human after agent\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "vault_target_changed_since_action"):
            self.transactions.undo(action["actionId"], user_confirmed=True)
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
            self.transactions.undo(action["actionId"], user_confirmed=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_model_undo_is_bound_to_the_originating_authorization_and_run(self) -> None:
        planned = self.plan([{"path": "01-Inbox/Owned.md", "content": "owned"}])
        action = self.apply(planned["id"])
        self.authorizations.create({
            "id": "authorization-other",
            "sessionId": "session-other",
            "runId": "run-other",
            "turnId": "turn-other",
            "sourceMessageId": "message-other",
            "objective": "unrelated run",
            "resourceScope": {},
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })
        with self.assertRaisesRegex(PermissionError, "undo_action_run_mismatch"):
            self.transactions.undo(
                action["actionId"],
                authorization_id="authorization-other",
                run_id="run-other",
            )
        self.assertTrue((self.vault / "01-Inbox/Owned.md").exists())
        result = self.transactions.undo(
            action["actionId"],
            authorization_id=self.authorization_id,
            run_id="run-test",
        )
        self.assertEqual(result["state"], "undone")

    def test_undo_atomic_take_preserves_edit_after_preflight(self) -> None:
        target = self.vault / "01-Inbox/Race.md"
        target.write_text("before\n", encoding="utf-8")
        planned = self.plan([{"path": "01-Inbox/Race.md", "content": "agent"}])
        action = self.apply(planned["id"])
        from agent.core import reversible_transaction as module
        original = module._stage_expected_file
        injected = False

        def edit_before_atomic_take(path: Path, expected_hash: str, stage: Path) -> bytes:
            nonlocal injected
            if not injected and path.as_posix().endswith("/01-Inbox/Race.md"):
                injected = True
                target.write_text("human concurrent edit\n", encoding="utf-8")
            return original(path, expected_hash, stage)

        with patch.object(module, "_stage_expected_file", side_effect=edit_before_atomic_take):
            with self.assertRaisesRegex(RuntimeError, "vault_target_changed_since_action"):
                self.transactions.undo(action["actionId"], user_confirmed=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "human concurrent edit\n")

    def test_undo_rollback_never_overwrites_concurrent_human_edit(self) -> None:
        first = self.vault / "01-Inbox/Undo-A.md"
        second = self.vault / "01-Inbox/Undo-B.md"
        first.write_text("A0\n", encoding="utf-8")
        second.write_text("B0\n", encoding="utf-8")
        action = self.apply(self.plan([
            {"path": "01-Inbox/Undo-A.md", "content": "A1"},
            {"path": "01-Inbox/Undo-B.md", "content": "B1"},
        ])["id"])
        from agent.core import reversible_transaction as module
        original = module._exclusive_write
        restored_first = False

        def fail_after_human_edit(path: Path, body: bytes) -> None:
            nonlocal restored_first
            if path.as_posix().endswith("/01-Inbox/Undo-A.md"):
                original(path, body)
                restored_first = True
                return
            if path.as_posix().endswith("/01-Inbox/Undo-B.md") and restored_first:
                first.write_text("human during rollback\n", encoding="utf-8")
                raise OSError("injected undo failure")
            original(path, body)

        with patch.object(module, "_exclusive_write", side_effect=fail_after_human_edit):
            with self.assertRaisesRegex(OSError, "injected undo failure"):
                self.transactions.undo(action["actionId"], user_confirmed=True)
        self.assertEqual(first.read_text(encoding="utf-8"), "human during rollback\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "B1\n")

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

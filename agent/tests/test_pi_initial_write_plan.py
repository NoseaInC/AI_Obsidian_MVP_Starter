from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent.core.service import AgentService
from agent.tests.test_realistic_agent_vault_workflow import _ScriptedResearchAgent


class PiInitialWritePlanBindingTests(unittest.TestCase):
    """The first safe Markdown write Plan freezes the Task Authorization.

    Before the first Plan, every write proposal is unbound and would surface a
    permission card. The first safe Plan auto-binds (no card); later additions
    of paths/operations in the same Run still require a real permission card.
    No keyword, regex, or Intent Router is involved.
    """

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name) / "Vault"
        for relative in (
            "01-Inbox/Research",
            "20-Knowledge/Drafts",
            "20-Knowledge/Topics",
            "20-Knowledge/Concepts",
        ):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        (self.vault / "20-Knowledge/Concepts/A.md").write_text(
            "---\nstatus: ai-draft\n---\n\n# A\n", encoding="utf-8",
        )
        self.service = AgentService(self.vault)
        self.run_id = "run-bind"
        self.authorization_id = "authorization-bind"
        self.service.register_task_authorization({
            "conversationId": "conversation-bind",
            "taskAuthorization": {
                "id": self.authorization_id,
                "sessionId": "session-bind",
                "runId": self.run_id,
                "turnId": "turn-bind",
                "sourceMessageId": "message-bind",
                "objective": "测试首次写入计划冻结",
                "resourceScope": {
                    "currentNote": True,
                    "explicitVaultPaths": ["20-Knowledge/Concepts/A.md"],
                    "createRoots": [],
                    "workspaceIds": [],
                    "projectPaths": [],
                },
                "operationScope": [],
                "reversibleOnly": True,
                "networkPolicy": "deny",
                "externalSideEffects": False,
                "expiresAtRunEnd": True,
            },
        })
        self.agent = _ScriptedResearchAgent(
            self.service, self.authorization_id, self.run_id,
        )

    def tearDown(self) -> None:
        self.service.store.close()
        self.temporary.cleanup()

    def _scope(self) -> dict[str, Any]:
        return self.service.store.get_task_authorization(self.authorization_id)["resourceScope"]

    def _operations(self) -> list[str]:
        return self.service.store.get_task_authorization(self.authorization_id)["operationScope"]

    # --- Scenario 1: first Plan updates the current note -> auto-bind, no card.
    def test_first_update_of_current_note_auto_binds_without_card(self) -> None:
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "更新 A", "writes": [{"path": "20-Knowledge/Concepts/A.md", "content": "# A\n\n更新\n"}]},
            call_id="call-update-a",
        )
        self.assertTrue(planned["ok"], planned)
        self.assertEqual(self.agent.permission_requests, [])
        self.assertEqual(self._scope()["writeScopeState"], "bound")
        self.assertEqual(self._scope()["initialWriteToolCallId"], "call-update-a")
        self.assertIsNotNone(self._scope().get("initialWriteBoundAt"))
        self.assertIn("update_markdown", self._operations())

    # --- Scenario 2: first Plan creates inside DEFAULT_CREATE_ROOTS -> auto-bind.
    def test_first_create_in_draft_root_auto_binds(self) -> None:
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "新建草稿", "writes": [{"path": "20-Knowledge/Drafts/新笔记.md", "content": "# 新笔记\n"}]},
            call_id="call-create-draft",
        )
        self.assertTrue(planned["ok"], planned)
        self.assertEqual(self.agent.permission_requests, [])
        self.assertEqual(self._scope()["writeScopeState"], "bound")
        self.assertIn("create_markdown", self._operations())

    # --- Scenario 3: create outside DEFAULT_CREATE_ROOTS -> card (no silent bind).
    def test_create_outside_draft_roots_requires_card(self) -> None:
        blocked = self.agent.call(
            "plan_vault_change",
            {"title": "写到 Topics", "writes": [{"path": "20-Knowledge/Topics/越权.md", "content": "# 越权\n"}]},
            call_id="call-create-topic",
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "task_create_scope_required")
        self.assertEqual(blocked["error"]["permissionRequest"]["type"], "vault_writes")
        self.assertEqual(self._scope()["writeScopeState"], "unbound")
        self.agent.grant(blocked, mode="once")
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "写到 Topics", "writes": [{"path": "20-Knowledge/Topics/越权.md", "content": "# 越权\n"}]},
            call_id="call-create-topic",
        )
        self.assertTrue(planned["ok"], planned)

    # --- Scenario 4: update of an unspecified existing note -> card.
    def test_update_unspecified_existing_note_requires_card(self) -> None:
        (self.vault / "20-Knowledge/Concepts/B.md").write_text("# B\n", encoding="utf-8")
        blocked = self.agent.call(
            "plan_vault_change",
            {"title": "改 B", "writes": [{"path": "20-Knowledge/Concepts/B.md", "content": "# B\n\n改\n"}]},
            call_id="call-update-b",
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "task_update_scope_required")
        self.assertEqual(blocked["error"]["permissionRequest"]["type"], "vault_writes")
        self.assertEqual(self._scope()["writeScopeState"], "unbound")
        self.agent.grant(blocked, mode="once")
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "改 B", "writes": [{"path": "20-Knowledge/Concepts/B.md", "content": "# B\n\n改\n"}]},
            call_id="call-update-b",
        )
        self.assertTrue(planned["ok"], planned)

    # --- Scenario 5: later create in draft root, different call -> expansion card.
    def test_later_create_in_draft_root_after_bind_requires_card(self) -> None:
        self.agent.call(
            "plan_vault_change",
            {"title": "更新 A", "writes": [{"path": "20-Knowledge/Concepts/A.md", "content": "# A\n\nx\n"}]},
            call_id="call-first",
        )
        self.assertEqual(self._scope()["writeScopeState"], "bound")
        later = self.agent.call(
            "plan_vault_change",
            {"title": "新草稿", "writes": [{"path": "20-Knowledge/Drafts/晚到.md", "content": "# 晚到\n"}]},
            call_id="call-later",
        )
        self.assertFalse(later["ok"])
        self.assertEqual(later["error"]["code"], "task_create_scope_required")
        self.assertEqual(later["error"]["permissionRequest"]["type"], "vault_writes")
        self.agent.grant(later, mode="once")
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "新草稿", "writes": [{"path": "20-Knowledge/Drafts/晚到.md", "content": "# 晚到\n"}]},
            call_id="call-later",
        )
        self.assertTrue(planned["ok"], planned)

    # --- Scenario 6: later update of a new note after bind -> expansion card.
    def test_later_update_after_bind_requires_card(self) -> None:
        self.agent.call(
            "plan_vault_change",
            {"title": "更新 A", "writes": [{"path": "20-Knowledge/Concepts/A.md", "content": "# A\n\nx\n"}]},
            call_id="call-first",
        )
        (self.vault / "20-Knowledge/Concepts/C.md").write_text("# C\n", encoding="utf-8")
        later = self.agent.call(
            "plan_vault_change",
            {"title": "改 C", "writes": [{"path": "20-Knowledge/Concepts/C.md", "content": "# C\n\ny\n"}]},
            call_id="call-later",
        )
        self.assertFalse(later["ok"])
        self.assertEqual(later["error"]["code"], "task_update_scope_required")
        self.agent.grant(later, mode="once")
        planned = self.agent.call(
            "plan_vault_change",
            {"title": "改 C", "writes": [{"path": "20-Knowledge/Concepts/C.md", "content": "# C\n\ny\n"}]},
            call_id="call-later",
        )
        self.assertTrue(planned["ok"], planned)

    # --- Scenario 7: reviewed/core protected note in first Plan -> hard deny, no card.
    def test_protected_reviewed_note_first_plan_hard_denied(self) -> None:
        (self.vault / "20-Knowledge/Concepts/C.md").write_text(
            "---\nstatus: reviewed\n---\n\n# C\n", encoding="utf-8",
        )
        blocked = self.agent.call(
            "plan_vault_change",
            {"title": "改 C", "writes": [{"path": "20-Knowledge/Concepts/C.md", "content": "覆盖"}]},
            call_id="call-update-c",
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "reviewed_core_read_only")
        self.assertNotIn("permissionRequest", blocked["error"])
        self.assertEqual(self._scope()["writeScopeState"], "unbound")
        self.assertEqual(
            (self.vault / "20-Knowledge/Concepts/C.md").read_text(encoding="utf-8"),
            "---\nstatus: reviewed\n---\n\n# C\n",
        )

    # --- Scenario 8: symlink target in first Plan -> hard deny, no card.
    def test_symlink_target_first_plan_hard_denied(self) -> None:
        outside = self.temporary.name / Path("outside.md")
        outside.write_text("# outside\n", encoding="utf-8")
        link = self.vault / "20-Knowledge/Concepts/link.md"
        os.symlink(outside, link)
        blocked = self.agent.call(
            "plan_vault_change",
            {"title": "改 link", "writes": [{"path": "20-Knowledge/Concepts/link.md", "content": "覆盖"}]},
            call_id="call-update-link",
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "invalid_note_path")
        self.assertNotIn("permissionRequest", blocked["error"])
        self.assertEqual(self._scope()["writeScopeState"], "unbound")

    # --- Scenario 9: path escape in first Plan -> hard deny, no card.
    def test_path_escape_first_plan_hard_denied(self) -> None:
        blocked = self.agent.call(
            "plan_vault_change",
            {"title": "逃逸", "writes": [{"path": "20-Knowledge/Concepts/../../../../escape.md", "content": "x"}]},
            call_id="call-escape",
        )
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "invalid_note_path")
        self.assertNotIn("permissionRequest", blocked["error"])
        self.assertEqual(self._scope()["writeScopeState"], "unbound")

    # --- plan_vault_copy: first copy auto-binds every batch under one call id.
    def test_plan_vault_copy_auto_binds_all_batches(self) -> None:
        sources = [
            "01-Inbox/Research/MPNN-概览.md",
            "01-Inbox/Research/GNN-读书摘录.md",
        ]
        for source in sources:
            (self.vault / source).write_text(f"# {source}\n", encoding="utf-8")
        result = self.agent.call(
            "plan_vault_copy",
            {"title": "复制资料", "destination_root": "20-Knowledge/Drafts", "source_paths": sources},
            call_id="call-copy",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.agent.permission_requests, [])
        self.assertEqual(self._scope()["writeScopeState"], "bound")
        self.assertIn("create_markdown", self._operations())
        for source in sources:
            target = f"20-Knowledge/Drafts/{Path(source).name}"
            self.assertIn(target, self._scope()["explicitVaultPaths"])


class PiInitialWritePlanUnitTests(unittest.TestCase):
    """Direct checks of the 14 binding rules on ``bind_initial_markdown_plan``."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.vault = Path(self.temporary.name) / "Vault"
        (self.vault / "01-Inbox").mkdir(parents=True, exist_ok=True)
        (self.vault / "20-Knowledge/Drafts").mkdir(parents=True, exist_ok=True)
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True, exist_ok=True)
        (self.vault / "20-Knowledge/Concepts/A.md").write_text("---\nstatus: ai-draft\n---\n\n# A\n", encoding="utf-8")
        self.service = AgentService(self.vault)
        self.run_id = "run-unit"
        self.authorization_id = "authorization-unit"
        self.service.register_task_authorization({
            "conversationId": "conversation-unit",
            "taskAuthorization": {
                "id": self.authorization_id,
                "sessionId": "session-unit",
                "runId": self.run_id,
                "turnId": "turn-unit",
                "sourceMessageId": "message-unit",
                "objective": "unit",
                "resourceScope": {
                    "currentNote": False,
                    "explicitVaultPaths": [],
                    "createRoots": [],
                    "workspaceIds": [],
                    "projectPaths": [],
                },
                "operationScope": [],
                "reversibleOnly": True,
                "networkPolicy": "deny",
                "externalSideEffects": False,
                "expiresAtRunEnd": True,
            },
        })

    def tearDown(self) -> None:
        self.service.store.close()
        self.temporary.cleanup()

    def _bind(self, call_id: str, writes: list[dict[str, Any]], run_id: str | None = None) -> dict[str, Any]:
        return self.service.task_authorizations.bind_initial_markdown_plan(
            self.authorization_id, run_id or self.run_id, call_id, writes,
        )

    def test_bind_rejects_run_mismatch(self) -> None:
        # The active/run-match rule rejects a binding request from another run.
        with self.assertRaises(PermissionError):
            self._bind("call-x", [{"path": "20-Knowledge/Drafts/n.md", "content": "x"}], run_id="other-run")

    def test_bind_rejects_too_few_or_too_many_writes(self) -> None:
        with self.assertRaises(ValueError):
            self._bind("call-x", [])
        with self.assertRaises(ValueError):
            self._bind("call-x", [{"path": f"20-Knowledge/Drafts/n{i}.md", "content": "x"} for i in range(11)])

    def test_bind_create_only_in_default_roots(self) -> None:
        with self.assertRaises(PermissionError):
            self._bind("call-x", [{"path": "20-Knowledge/Topics/x.md", "content": "x"}])
        result = self._bind("call-x", [{"path": "20-Knowledge/Drafts/x.md", "content": "x"}])
        self.assertTrue(result["bound"])
        self.assertEqual(
            self.service.store.get_task_authorization(self.authorization_id)["resourceScope"]["writeScopeState"],
            "bound",
        )

    def test_bind_update_requires_explicit_path(self) -> None:
        with self.assertRaises(PermissionError):
            self._bind("call-x", [{"path": "20-Knowledge/Concepts/A.md", "content": "x"}])
        # Pre-scope A.md, then the update binds.
        self.service.task_authorizations.grant_scope(
            self.authorization_id, self.run_id, [{"path": "20-Knowledge/Concepts/A.md"}],
        )
        result = self._bind("call-x", [{"path": "20-Knowledge/Concepts/A.md", "content": "x"}])
        self.assertTrue(result["bound"])

    def test_bind_rejects_protected_note(self) -> None:
        (self.vault / "20-Knowledge/Concepts/C.md").write_text("---\nstatus: reviewed\n---\n\n# C\n", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self._bind("call-x", [{"path": "20-Knowledge/Concepts/C.md", "content": "x"}])

    def test_bind_different_tool_call_id_after_bound_is_rejected(self) -> None:
        self._bind("call-first", [{"path": "20-Knowledge/Drafts/x.md", "content": "x"}])
        with self.assertRaises(PermissionError):
            self._bind("call-second", [{"path": "20-Knowledge/Drafts/y.md", "content": "x"}])

    def test_bind_idempotent_on_same_tool_call_id(self) -> None:
        first = self._bind("call-first", [{"path": "20-Knowledge/Drafts/x.md", "content": "x"}])
        self.assertTrue(first["bound"])
        # Same call id re-binding (e.g. batched copy) extends rather than errors.
        second = self._bind("call-first", [{"path": "20-Knowledge/Drafts/y.md", "content": "x"}])
        self.assertFalse(second["bound"])
        scope = self.service.store.get_task_authorization(self.authorization_id)["resourceScope"]
        self.assertIn("20-Knowledge/Drafts/x.md", scope["explicitVaultPaths"])
        self.assertIn("20-Knowledge/Drafts/y.md", scope["explicitVaultPaths"])


if __name__ == "__main__":
    unittest.main()

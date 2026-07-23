from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.service import AgentService
from agent.core.storage import StateStore


def _authorization(run_id: str, session_id: str) -> dict:
    return {
        "id": f"auth-{run_id}",
        "sessionId": session_id,
        "runId": run_id,
        "turnId": f"turn-{run_id}",
        "sourceMessageId": f"message-{run_id}",
        "objective": "压缩后需要恢复上下文",
        "resourceScope": {
            "currentNote": True,
            "explicitVaultPaths": [],
            "createRoots": ["01-Inbox"],
            "workspaceId": "",
            "projectPaths": [],
        },
        "operationScope": ["read", "write"],
        "reversibleOnly": True,
        "networkPolicy": "deny",
        "externalSideEffects": False,
        "expiresAtRunEnd": True,
    }


class PiCompactionCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, store=self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def _register(self, run_id: str, session_id: str) -> None:
        self.service.register_task_authorization(
            {"conversationId": session_id, "model": "fake", "taskAuthorization": _authorization(run_id, session_id)}
        )

    @staticmethod
    def _entry(run_id: str, goal: str, **overrides) -> dict:
        boundary = f"pi-entry-auth-auth-{run_id}"
        entry = {
            "cutEntryId": boundary,
            "keptFromEntryId": boundary,
            "summaryVersion": 1,
            "tokensBefore": 5000,
            "tokensAfter": 800,
            "createdAt": "2026-07-23T10:00:00+08:00",
            "structuredState": {
                "goal": goal,
                "activeNote": {"path": "20-Knowledge/Notes/x.md", "name": "x"},
                "attachments": ["a.pdf"],
                "sourcesRead": ["src-1"],
                "completedActions": ["act-1"],
                "pendingActions": ["act-2"],
                "failedTools": ["bad-tool"],
                "activeWorkspace": {"workspaceId": "ws-1", "projectPaths": ["p1"]},
                "branchId": "run-a",
                "currentLeafId": boundary,
            },
        }
        entry.update(overrides)
        return entry

    def test_save_then_recover_structured_state(self) -> None:
        self._register("run-compact-1", "session-compact-1")
        self.service.save_compaction_checkpoint(
            "run-compact-1",
            {
                "sessionId": "session-compact-1",
                "branchId": "run-compact-1",
                "entry": self._entry("run-compact-1", "整理阅读笔记"),
                "summary": "结构化压缩摘要",
            },
        )
        got = self.service.get_compaction_checkpoint("run-compact-1")["entry"]
        self.assertIsNotNone(got)
        self.assertEqual(got["tokensBefore"], 5000)
        self.assertEqual(got["tokensAfter"], 800)
        self.assertEqual(got["branchId"], "run-compact-1")
        self.assertEqual(got["summary"], "结构化压缩摘要")
        self.assertTrue(got["checkpointEntryId"].startswith("pi-entry-checkpoint-"))
        state = got["structuredState"]
        self.assertEqual(state["goal"], "整理阅读笔记")
        self.assertEqual(state["activeNote"]["path"], "20-Knowledge/Notes/x.md")
        self.assertEqual(state["attachments"], ["a.pdf"])
        self.assertEqual(state["completedActions"], ["act-1"])
        self.assertEqual(state["activeWorkspace"]["workspaceId"], "ws-1")

    def test_sibling_branch_sources_do_not_enter_selected_lineage(self) -> None:
        self._register("run-main", "session-shared")
        main_auth_entry = "pi-entry-auth-auth-run-main"
        sibling_auth = _authorization("run-sibling", "session-shared")
        sibling_auth.update({
            "parentRunId": "run-main",
            "forkedFromEntryId": main_auth_entry,
            "forkedFromSequence": 0,
        })
        self.service.register_task_authorization({
            "conversationId": "session-shared",
            "model": "fake",
            "taskAuthorization": sibling_auth,
        })
        self.service.append_pi_events({"runId": "run-sibling", "events": [
            {"sequence": 10, "type": "tool_use", "id": "sibling-call", "name": "search_vault", "input": {"query": "sibling-only"}},
            {"sequence": 20, "type": "tool_result", "id": "sibling-call", "name": "search_vault", "result": {"items": []}, "summary": "SIBLING-ONLY-SOURCE", "status": "completed"},
        ]})
        self.service.append_pi_events({"runId": "run-main", "events": [
            {"sequence": 10, "type": "tool_use", "id": "main-call", "name": "search_vault", "input": {"query": "main-only"}},
            {"sequence": 20, "type": "tool_result", "id": "main-call", "name": "search_vault", "result": {"items": []}, "summary": "MAIN-ONLY-SOURCE", "status": "completed"},
        ]})

        projection = self.service.pi_session_projection("session-shared", run_id="run-main")
        encoded = str(projection["compactionState"])
        self.assertIn("MAIN-ONLY-SOURCE", encoded)
        self.assertNotIn("SIBLING-ONLY-SOURCE", encoded)
        self.assertEqual(projection["branchId"], "run-main")

    def test_repeated_save_updates_in_place(self) -> None:
        self._register("run-c", "session-c")
        self.service.save_compaction_checkpoint(
            "run-c", {"sessionId": "session-c", "branchId": "run-c", "entry": self._entry("run-c", "v1"), "summary": "v1"}
        )
        self.service.save_compaction_checkpoint(
            "run-c", {"sessionId": "session-c", "branchId": "run-c", "entry": self._entry("run-c", "v2", tokensBefore=9000), "summary": "v2"}
        )
        rows = self.service.get_compaction_checkpoint("run-c")["entry"]
        self.assertEqual(rows["structuredState"]["goal"], "v2")
        self.assertEqual(rows["tokensBefore"], 9000)

    def test_missing_entry_is_rejected(self) -> None:
        self._register("run-d", "session-d")
        with self.assertRaises(ValueError):
            self.service.save_compaction_checkpoint(
                "run-d", {"sessionId": "session-d", "branchId": "run-d", "entry": None}
            )

    def test_unknown_run_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.save_compaction_checkpoint(
                "run-missing",
                {"sessionId": "session-missing", "branchId": "run-missing", "entry": self._entry("run-missing", "x"), "summary": "x"},
            )

    def test_absent_checkpoint_returns_null(self) -> None:
        self._register("run-e", "session-e")
        self.assertIsNone(self.service.get_compaction_checkpoint("run-e")["entry"])

    def test_projection_reuses_checkpoint_then_only_kept_entries(self) -> None:
        self._register("run-projection", "session-projection")
        self.service.append_pi_events({"runId": "run-projection", "events": [
            {"sequence": 10, "type": "text", "content": "必须被检查点替代的旧回答"},
            {"sequence": 20, "type": "tool_use", "id": "kept-call", "name": "search_vault", "input": {"query": "主线"}},
            {"sequence": 30, "type": "tool_result", "id": "kept-call", "name": "search_vault", "result": {"items": []}, "summary": "保留事实", "status": "completed"},
            {"sequence": 40, "type": "text", "content": "必须保留的近期回答"},
        ]})
        entry = self._entry("run-projection", "恢复目标")
        entry.update({
            "cutEntryId": "pi-entry-run-projection-000000000010",
            "keptFromEntryId": "pi-entry-run-projection-000000000020",
        })
        self.service.save_compaction_checkpoint("run-projection", {
            "sessionId": "session-projection", "branchId": "run-projection",
            "entry": entry, "summary": "已恢复目标、约束和旧事实",
        })

        projection = self.service.pi_session_projection("session-projection", run_id="run-projection")
        encoded = str(projection["messages"])
        self.assertEqual(projection["messages"][0]["role"], "compactionSummary")
        self.assertNotIn("必须被检查点替代的旧回答", encoded)
        self.assertIn("kept-call", encoded)
        self.assertIn("必须保留的近期回答", encoded)
        self.assertEqual(
            projection["compaction"]["checkpointEntryId"],
            projection["messages"][0]["metadata"]["entryId"],
        )

    def test_checkpoint_redacts_secret_fields_and_large_output(self) -> None:
        self._register("run-private", "session-private")
        entry = self._entry("run-private", "隐私测试")
        entry["structuredState"]["taskAuthorization"] = {
            "apiKey": "sk-secret-value-123456",
            "bashOutput": "x" * 20_000,
        }
        self.service.save_compaction_checkpoint("run-private", {
            "sessionId": "session-private", "branchId": "run-private",
            "entry": entry, "summary": "safe summary sk-secret-value-123456",
        })
        encoded = str(self.service.get_compaction_checkpoint("run-private")["entry"])
        self.assertNotIn("sk-secret-value-123456", encoded)
        self.assertIn("[redacted]", encoded)
        self.assertIn("[truncated sha256=", encoded)

    def test_projection_builds_complete_compaction_sources_from_structured_stores(self) -> None:
        self._register("run-sources", "session-sources")
        authorization = self.store.get_task_authorization("auth-run-sources")
        scope = dict(authorization["resourceScope"])
        scope.update({
            "workspaceId": "workspace-1",
            "workspaceIds": ["workspace-1"],
            "projectPaths": ["isolated/project"],
            "writeScopeState": "bound",
        })
        self.store.update_task_authorization_scope(
            "auth-run-sources", scope, ["read_workspace_file"],
        )
        self.store.save_conversation_focus("session-sources", {
            "activeTopic": {"title": "语义检索"},
            "activeVaultNotePath": "20-Knowledge/Drafts/current.md",
            "activeSelectionReference": "selection:current",
            "confidence": .9,
        })
        attachment = self.service.create_attachment(
            {"conversation_id": "session-sources", "display_name": "paper.pdf", "kind": "pdf"},
            b"%PDF-1.4\nfixture", "application/pdf",
        )
        action_id = self.store.create_agent_action(
            "apply_vault_change", "20-Knowledge/Drafts/result.md", "low", {},
        )
        self.store.record_file_snapshot(action_id, "20-Knowledge/Drafts/result.md", "before", "local:snapshot")
        self.store.record_file_change(action_id, "20-Knowledge/Drafts/result.md", "before", "after", "+result")
        self.store.complete_agent_action(action_id, "completed")
        self.service.append_pi_events({"runId": "run-sources", "events": [
            {"sequence": 10, "type": "tool_use", "id": "source-call", "name": "search_vault", "input": {"query": "语义检索"}},
            {"sequence": 20, "type": "tool_result", "id": "source-call", "name": "search_vault", "result": {"result": {"actionId": action_id}}, "summary": "读取主线来源", "status": "completed"},
            {"sequence": 30, "type": "tool_use", "id": "failed-call", "name": "read_note_excerpt", "input": {"path": "missing.md"}},
            {"sequence": 40, "type": "tool_result", "id": "failed-call", "name": "read_note_excerpt", "result": {"code": "note_missing"}, "summary": "不存在", "status": "failed"},
        ]})

        projection = self.service.pi_session_projection("session-sources", run_id="run-sources")
        state = projection["compactionState"]
        self.assertEqual(state["currentLeafId"], projection["leafId"])
        self.assertEqual(state["conversationFocus"]["activeTopic"]["title"], "语义检索")
        self.assertEqual(state["activeNote"]["path"], "20-Knowledge/Drafts/current.md")
        self.assertEqual(state["activeSelectionReference"], "selection:current")
        self.assertEqual(state["attachments"][0]["id"], attachment["id"])
        self.assertEqual(state["sourcesRead"][0]["tool"], "search_vault")
        self.assertEqual(state["failedTools"][0]["code"], "note_missing")
        self.assertEqual(state["completedActions"][0]["id"], action_id)
        self.assertTrue(state["undoState"]["actions"][0]["available"])
        self.assertEqual(state["activeWorkspace"]["workspaceId"], "workspace-1")
        self.assertEqual(state["taskBranch"]["branchId"], "run-sources")
        self.assertEqual(state["taskAuthorization"]["operationScope"], ["read_workspace_file"])


if __name__ == "__main__":
    unittest.main()

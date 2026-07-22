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
    def _entry(goal: str, **overrides) -> dict:
        entry = {
            "cutEntryId": "entry-3",
            "keptFromEntryId": "entry-3",
            "summaryVersion": 1,
            "tokensBefore": 5000,
            "tokensAfter": 800,
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
                "entry": self._entry("整理阅读笔记"),
            },
        )
        got = self.service.get_compaction_checkpoint("run-compact-1")["entry"]
        self.assertIsNotNone(got)
        self.assertEqual(got["tokensBefore"], 5000)
        self.assertEqual(got["tokensAfter"], 800)
        self.assertEqual(got["branchId"], "run-compact-1")
        state = got["structuredState"]
        self.assertEqual(state["goal"], "整理阅读笔记")
        self.assertEqual(state["activeNote"]["path"], "20-Knowledge/Notes/x.md")
        self.assertEqual(state["attachments"], ["a.pdf"])
        self.assertEqual(state["completedActions"], ["act-1"])
        self.assertEqual(state["activeWorkspace"]["workspaceId"], "ws-1")

    def test_sibling_branch_isolation(self) -> None:
        self._register("run-a", "session-a")
        self._register("run-b", "session-b")
        self.service.save_compaction_checkpoint(
            "run-a", {"sessionId": "session-a", "branchId": "run-a", "entry": self._entry("A 分支")}
        )
        self.service.save_compaction_checkpoint(
            "run-b", {"sessionId": "session-b", "branchId": "run-b", "entry": self._entry("B 分支")}
        )
        got_a = self.service.get_compaction_checkpoint("run-a")["entry"]
        got_b = self.service.get_compaction_checkpoint("run-b")["entry"]
        self.assertEqual(got_a["structuredState"]["goal"], "A 分支")
        self.assertEqual(got_b["structuredState"]["goal"], "B 分支")
        self.assertEqual(got_a["branchId"], "run-a")
        self.assertEqual(got_b["branchId"], "run-b")

    def test_repeated_save_updates_in_place(self) -> None:
        self._register("run-c", "session-c")
        self.service.save_compaction_checkpoint(
            "run-c", {"sessionId": "session-c", "branchId": "run-c", "entry": self._entry("v1")}
        )
        self.service.save_compaction_checkpoint(
            "run-c", {"sessionId": "session-c", "branchId": "run-c", "entry": self._entry("v2", tokensBefore=9000)}
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
                {"sessionId": "session-missing", "branchId": "run-missing", "entry": self._entry("x")},
            )

    def test_absent_checkpoint_returns_null(self) -> None:
        self._register("run-e", "session-e")
        self.assertIsNone(self.service.get_compaction_checkpoint("run-e")["entry"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from agent.runtime.policy import ToolPermissionGate


class RuntimePolicyTest(unittest.TestCase):
    def test_new_draft_defaults_to_inline_confirmation(self):
        result = ToolPermissionGate().assess_commit({
            "writes": [{"path": "10-Inbox/Delta Method.md", "action": "create"}],
        })
        self.assertEqual(result.decision, "ask")
        self.assertEqual(result.scope_candidates, ("10-Inbox",))

    def test_existing_note_needs_inline_confirmation(self):
        result = ToolPermissionGate().assess_commit({
            "writes": [{"path": "20-Knowledge/Delta Method.md", "action": "update"}],
        })
        self.assertEqual(result.decision, "ask")
        self.assertEqual(result.risk_level, "medium")

    def test_scoped_session_grant_allows_only_create_in_that_root(self):
        gate = ToolPermissionGate()
        allowed = gate.assess_commit(
            {"writes": [{"path": "10-Inbox/Test.md", "action": "create"}]},
            session_allow_create_roots=["10-Inbox"],
        )
        update = gate.assess_commit(
            {"writes": [{"path": "10-Inbox/Test.md", "action": "update"}]},
            session_allow_create_roots=["10-Inbox"],
        )
        self.assertEqual(allowed.decision, "allow")
        self.assertEqual(update.decision, "ask")

    def test_scoped_child_root_grant_allows_future_create(self):
        result = ToolPermissionGate().assess_commit(
            {
                "writes": [
                    {
                        "path": "20-Knowledge/Drafts/影响函数.md",
                        "action": "create",
                    }
                ]
            },
            session_allow_create_roots=["20-Knowledge/Drafts"],
        )
        self.assertEqual(result.decision, "allow")

    def test_deny_precedes_scoped_session_allow(self):
        result = ToolPermissionGate().assess_commit(
            {"writes": [{"path": "../../escape.md", "action": "create"}]},
            session_allow_create_roots=["10-Inbox"],
        )
        self.assertEqual(result.decision, "deny")


if __name__ == "__main__":
    unittest.main()

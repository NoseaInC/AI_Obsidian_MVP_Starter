from __future__ import annotations

import unittest

from agent.runtime.policy import assess_proposal


class RuntimePolicyTest(unittest.TestCase):
    def test_new_draft_can_auto_apply(self):
        result = assess_proposal(
            {
                "writes": [
                    {
                        "path": "10-Inbox/Delta Method.md",
                        "action": "create",
                    }
                ]
            },
            write_requested=True,
            autonomy_mode="balanced",
        )
        self.assertTrue(result.auto_apply)

    def test_existing_note_needs_inline_confirmation(self):
        result = assess_proposal(
            {
                "writes": [
                    {
                        "path": "20-Knowledge/Delta Method.md",
                        "action": "update",
                    }
                ]
            },
            write_requested=True,
            autonomy_mode="high",
        )
        self.assertFalse(result.auto_apply)
        self.assertEqual(result.risk_level, "medium")

    def test_no_explicit_write_intent_never_applies(self):
        result = assess_proposal(
            {
                "writes": [
                    {
                        "path": "10-Inbox/Test.md",
                        "action": "create",
                    }
                ]
            },
            write_requested=False,
            autonomy_mode="high",
        )
        self.assertFalse(result.auto_apply)


if __name__ == "__main__":
    unittest.main()

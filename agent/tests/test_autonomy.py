from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.service import AgentService


class VaultAutonomyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.service = AgentService(self.vault)

    def tearDown(self) -> None:
        self.service.store.close(); self.temp.cleanup()

    def test_high_mode_low_risk_managed_block_is_snapshotted_and_undoable(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/普通草稿.md"
        original = "---\nstatus: draft\n---\n\n# 普通草稿\n\n人工正文，不得覆盖。\n"
        note.write_text(original, encoding="utf-8")
        result = self.service.apply_autonomous_vault_change({
            "path": "20-Knowledge/Concepts/普通草稿.md", "operation": "update_managed_block",
            "block": "learning-brain", "content": "- 补充来源：[[Dragonnet]]",
            "reason": "补充可追溯关系",
        })
        self.assertTrue(result["applied"]); self.assertTrue(result["undoAvailable"])
        changed = note.read_text(encoding="utf-8")
        self.assertIn("人工正文，不得覆盖。", changed)
        self.assertIn("agent:managed:learning-brain:start", changed)
        action = self.service.store.get_agent_action(result["actionId"])
        self.assertEqual(action["status"], "applied")
        self.assertTrue(action["snapshots"]); self.assertTrue(action["changes"])
        undo = self.service.undo_autonomous_vault_change(result["actionId"])
        self.assertEqual(undo["state"], "undone")
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_undo_refuses_to_overwrite_a_later_user_edit(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/冲突.md"
        note.write_text("# 冲突\n", encoding="utf-8")
        result = self.service.apply_autonomous_vault_change({
            "path": "20-Knowledge/Concepts/冲突.md", "operation": "update_managed_block", "content": "Agent 内容",
        })
        note.write_text(note.read_text(encoding="utf-8") + "\n用户后续编辑\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed_since_action"):
            self.service.undo_autonomous_vault_change(result["actionId"])
        self.assertIn("用户后续编辑", note.read_text(encoding="utf-8"))

    def test_reviewed_note_is_never_overwritten_and_becomes_update_suggestion_change_set(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/正式知识.md"
        original = "---\nstatus: reviewed\n---\n\n# 正式知识\n\n确认正文。\n"
        note.write_text(original, encoding="utf-8")
        result = self.service.apply_autonomous_vault_change({
            "path": "20-Knowledge/Concepts/正式知识.md", "operation": "update_managed_block",
            "content": "建议增加一个外部来源。",
        })
        self.assertFalse(result["applied"]); self.assertTrue(result["requiresConfirmation"])
        self.assertEqual(result["reason"], "protected_or_core")
        self.assertEqual(result["changeSet"]["state"], "proposed")
        self.assertIn("Update-Suggestions", result["proposalPath"])
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_cautious_mode_only_proposes_and_denied_folder_is_unreadable(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/谨慎.md"; note.write_text("# 谨慎\n", encoding="utf-8")
        self.service.set_autonomy_mode({"mode": "cautious", "acknowledge_summary": True})
        result = self.service.apply_autonomous_vault_change({
            "path": "20-Knowledge/Concepts/谨慎.md", "operation": "update_managed_block", "content": "提案内容",
        })
        self.assertFalse(result["applied"]); self.assertEqual(result["reason"], "cautious_mode")
        self.assertEqual(note.read_text(encoding="utf-8"), "# 谨慎\n")
        with self.assertRaisesRegex(PermissionError, "agent_access_denied"):
            self.service.autonomy.classify("Private/secret.md", allow_missing=True)

    def test_diagnostics_reports_conversation_predictions_web_and_undo_boundaries(self) -> None:
        self.service.submit_intake({"message": "解释倾向得分的直觉"}, "diagnostics-conversation")
        note = self.vault / "20-Knowledge/Concepts/诊断.md"; note.write_text("# 诊断\n", encoding="utf-8")
        action = self.service.apply_autonomous_vault_change({
            "path": "20-Knowledge/Concepts/诊断.md", "operation": "update_managed_block", "content": "受控内容",
        })
        diagnostics = self.service.brain_diagnostics()
        self.assertGreaterEqual(diagnostics["indexes"]["conversations"], 1)
        self.assertGreaterEqual(diagnostics["indexes"]["conversation_summaries"], 1)
        self.assertGreaterEqual(diagnostics["indexes"]["knowledge_signals"], 1)
        self.assertGreaterEqual(diagnostics["indexes"]["agent_actions"], 1)
        self.assertGreaterEqual(diagnostics["indexes"]["snapshots"], 1)
        self.assertGreaterEqual(diagnostics["indexes"]["undo_available"], 1)
        self.assertIn("typescript_boundary", diagnostics["runtime"])
        self.assertIn("python_boundary", diagnostics["runtime"])
        detail = self.service.store.get_agent_action(action["actionId"])
        self.assertTrue(detail["changes"]); self.assertTrue(detail["snapshots"])


if __name__ == "__main__":
    unittest.main()

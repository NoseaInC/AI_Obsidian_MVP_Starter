from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.core.storage import StateStore


class StructuredContextMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        for relative in ("01-Inbox", "20-Knowledge/Concepts", "20-Knowledge/Topics", "90-Local-Only/Agent"):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, self.store, key_store=FakeKeyStore())

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_plain_prose_never_routes_a_write(self) -> None:
        result = self.service.submit_intake({"message": "把这个整理并保存到 Obsidian。", "mode": "auto"}, "plain-prose")
        self.assertEqual(result["assistantIntent"]["name"], "answer_question")
        self.assertFalse(result["assistantIntent"]["writeRequested"])
        self.assertFalse(result["organization"])
        self.assertEqual(list((self.vault / "20-Knowledge").rglob("*.md")), [])

    def test_explicit_organize_mode_previews_without_writing(self) -> None:
        result = self.service.submit_intake({
            "message": "课堂材料：一致性与渐近分布。" * 12,
            "mode": "organize",
        }, "structured-preview")
        self.assertEqual(result["assistantIntent"]["name"], "organize_preview")
        self.assertFalse(result["assistantIntent"]["writeRequested"])
        self.assertEqual(result["organization"]["status"], "preview")
        self.assertTrue(result["organization"]["plan"]["actions"])
        self.assertEqual(list((self.vault / "20-Knowledge").rglob("*.md")), [])

    def test_explicit_save_mode_is_reversible(self) -> None:
        result = self.service.submit_intake({
            "message": "Delta Method 的定义、条件和来源。",
            "mode": "save",
        }, "structured-save")
        self.assertTrue(result["assistantIntent"]["writeRequested"])
        self.assertEqual(result["organization"]["status"], "applied")
        changed = next(item for item in result["organization"]["results"] if item.get("path"))
        target = self.vault / changed["path"]
        self.assertTrue(target.exists())
        self.assertIn("status: ai-draft", target.read_text(encoding="utf-8"))
        undone = self.service.undo_autonomous_vault_change(changed["actionId"])
        self.assertEqual(undone["state"], "undone")
        self.assertFalse(target.exists())

    def test_reviewed_note_is_not_overwritten_by_structured_save(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/最大似然估计.md"
        original = "---\nstatus: reviewed\n---\n\n# 最大似然估计\n\n人工确认内容。\n"
        note.write_text(original, encoding="utf-8")
        result = self.service.submit_intake({
            "message": "补充来源与适用边界。",
            "mode": "save",
            "requested_destination": "20-Knowledge/Concepts/最大似然估计.md",
            "requested_output": "existing_note_update",
            "active_note": {"path": "20-Knowledge/Concepts/最大似然估计.md", "selection": "人工确认内容。"},
        }, "structured-reviewed")
        self.assertEqual(note.read_text(encoding="utf-8"), original)
        self.assertEqual(result["organization"]["status"], "awaiting_confirmation")
        self.assertTrue(any(item["type"] == "update_suggestion" for item in result["artifacts"]))

    def test_input_bundle_is_bounded_to_eight_recent_messages(self) -> None:
        conversation = self.service.create_conversation({"title": "bounded"})
        for index in range(12):
            self.service.intake.append_message(conversation["id"], "user", f"turn-{index}")
        last = self.service.intake.append_message(conversation["id"], "user", "current")
        bundle = self.service.context_material.input.build(conversation["id"], last, [], {})
        self.assertEqual(len(bundle["recentMessages"]), 8)


if __name__ == "__main__":
    unittest.main()

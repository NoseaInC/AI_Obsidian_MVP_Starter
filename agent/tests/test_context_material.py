from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.core.storage import SCHEMA_VERSION, StateStore


class ContextMaterialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        (self.vault / "01-Inbox").mkdir(parents=True)
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True)
        (self.vault / "20-Knowledge/Topics").mkdir(parents=True)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, self.store, key_store=FakeKeyStore())

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_delta_method_followup_resolves_pronoun_writes_one_note_and_undoes(self) -> None:
        first = self.service.submit_intake({"message": "介绍一下 Delta Method。"}, "delta-first")
        self.assertEqual(first["focus"]["activeMethod"]["canonicalName"], "Delta Method")
        self.assertFalse(first["organization"])

        second = self.service.submit_intake({
            "conversation_id": first["conversation"]["id"],
            "message": "把这个方法带上推导整理到 Obsidian 中。",
        }, "delta-second")
        self.assertEqual(second["assistantIntent"]["name"], "create_method_note")
        self.assertEqual(second["focus"]["resolution"]["resolvedReference"]["canonicalName"], "Delta Method")
        self.assertFalse(second["focus"]["resolution"]["requiresConfirmation"])
        self.assertEqual(second["organization"]["status"], "applied")
        self.assertLessEqual(len(second["organization"]["plan"]["actions"]), 3)

        result = second["organization"]["results"][0]
        note = self.vault / result["path"]
        self.assertEqual(result["path"], "20-Knowledge/Concepts/Delta Method.md")
        text = note.read_text(encoding="utf-8")
        self.assertIn("status: ai-draft", text)
        self.assertIn("agent_managed: true", text)
        self.assertIn(r"\sqrt{n}", text)
        self.assertIn("Slutsky", text)
        self.assertFalse(any(item["type"] == "learning_pack" for item in second["artifacts"]))
        self.assertTrue(result["undoAvailable"])
        action = self.store.get_agent_action(result["actionId"])
        self.assertTrue(action["snapshots"])
        self.assertTrue(action["changes"])
        undo = self.service.undo_autonomous_vault_change(result["actionId"])
        self.assertEqual(undo["state"], "undone")
        self.assertFalse(note.exists())

    def test_unresolved_pronoun_requests_minimal_confirmation(self) -> None:
        result = self.service.submit_intake({"message": "把这个方法整理一下。"}, "unresolved-pronoun")
        self.assertTrue(result["focus"]["resolution"]["requiresConfirmation"])
        self.assertIsNone(result["focus"]["resolution"]["resolvedReference"])
        self.assertEqual(result["assistantIntent"]["name"], "organize_preview")

    def test_explicit_mixed_material_preview_creates_plan_without_writing(self) -> None:
        message = (
            "课堂笔记：统计推断需要区分估计量的一致性与渐近分布。\n"
            "结合 https://www.nist.gov/itl 帮我整理成预览，但不要保存。\n"
            + "这段内容只用于本地整理与来源关联。" * 8
        )
        result = self.service.submit_intake({"message": message}, "mixed-preview-no-write")

        self.assertEqual(result["assistantIntent"]["name"], "organize_preview")
        self.assertFalse(result["assistantIntent"]["writeRequested"])
        self.assertEqual(result["organization"]["status"], "preview")
        self.assertTrue(result["organization"]["plan"]["actions"])
        self.assertTrue(any(item["type"] == "organization_plan" for item in result["artifacts"]))
        self.assertFalse(any(item["type"] == "write_result" for item in result["artifacts"]))
        self.assertEqual(result["materialBundle"]["understanding"]["sourceMetadata"]["urls"], ["https://www.nist.gov/itl"])
        self.assertEqual(list((self.vault / "20-Knowledge").rglob("*.md")), [])

    def test_current_note_low_risk_update_preserves_human_text_and_is_undoable(self) -> None:
        note = self.vault / "20-Knowledge/Concepts/已有草稿.md"
        original = "---\nstatus: ai-draft\n---\n\n# 已有草稿\n\n人工内容。\n"
        note.write_text(original, encoding="utf-8")
        first = self.service.submit_intake({"message": "解释 Delta Method 的推导直觉。"}, "current-note-context")
        result = self.service.submit_intake({
            "conversation_id": first["conversation"]["id"],
            "message": "把推导加入当前笔记",
            "active_note": {"path": "20-Knowledge/Concepts/已有草稿.md", "selection": "人工内容。"},
        }, "current-note-update")
        self.assertEqual(result["assistantIntent"]["name"], "update_current_note")
        self.assertEqual(result["organization"]["status"], "applied")
        self.assertFalse(result["organization"]["plan"]["requiresConfirmation"])
        changed = note.read_text(encoding="utf-8")
        self.assertIn("人工内容。", changed)
        self.assertIn("agent:managed:material-organization:start", changed)
        action_id = result["organization"]["results"][0]["actionId"]
        self.service.undo_autonomous_vault_change(action_id)
        self.assertEqual(note.read_text(encoding="utf-8"), original)

    def test_reviewed_alias_is_protected_and_does_not_create_duplicate(self) -> None:
        reviewed = self.vault / "20-Knowledge/Concepts/德尔塔方法.md"
        original = "---\nstatus: reviewed\naliases: [Delta Method, Delta 方法]\n---\n\n# 德尔塔方法\n\n确认内容。\n"
        reviewed.write_text(original, encoding="utf-8")
        first = self.service.submit_intake({"message": "介绍一下 Delta Method。"}, "delta-reviewed-first")
        result = self.service.submit_intake({
            "conversation_id": first["conversation"]["id"],
            "message": "把这个方法带上推导整理到 Obsidian 中。",
        }, "delta-reviewed-second")
        self.assertEqual(result["organization"]["status"], "awaiting_confirmation")
        self.assertEqual(result["organization"]["plan"]["actions"][0]["action"], "skip")
        suggestion = next(item for item in result["artifacts"] if item["type"] == "update_suggestion")
        self.assertTrue(suggestion["payload"]["changeSetId"])
        self.assertEqual(reviewed.read_text(encoding="utf-8"), original)
        self.assertFalse((self.vault / "20-Knowledge/Concepts/Delta Method.md").exists())

    def test_pdf_and_pasted_text_are_traceable_without_sqlite_body_copy(self) -> None:
        conversation = self.service.create_conversation({"title": "多材料"})
        pdf = self.service.create_attachment(
            {"conversation_id": conversation["id"], "display_name": "Delta 讲义.pdf", "kind": "pdf"},
            b"%PDF-1.4\n% offline fixture\n", "application/pdf",
        )
        marker = "PRIVATE-PASTED-BODY-MUST-STAY-LOCAL"
        result = self.service.submit_intake({
            "conversation_id": conversation["id"],
            "message": f"整理这篇论文和下面文本：\n{marker}\n" + ("解释文本。" * 30),
            "attachments": [{"attachment_id": pdf["id"]}],
        }, "mixed-material")
        self.assertEqual(result["materialBundle"]["understanding"]["kind"], "mixed_bundle")
        self.assertIn(pdf["id"], result["materialBundle"]["attachmentIds"])
        self.assertNotIn(marker, "\n".join(self.store.connection.iterdump()))

    def test_current_pdf_survives_to_next_turn_and_becomes_one_source_note(self) -> None:
        conversation = self.service.create_conversation({"title": "当前 PDF"})
        pdf = self.service.create_attachment(
            {"conversation_id": conversation["id"], "display_name": "Delta 讲义.pdf", "kind": "pdf"},
            b"%PDF-1.4\n% offline fixture\n", "application/pdf",
        )
        first = self.service.submit_intake({
            "conversation_id": conversation["id"], "message": "先看看这篇论文。",
            "attachments": [{"attachment_id": pdf["id"]}],
        }, "pdf-focus-first")
        second = self.service.submit_intake({
            "conversation_id": conversation["id"], "message": "把这篇整理到 Obsidian 中。",
        }, "pdf-focus-second")
        self.assertEqual(second["focus"]["resolution"]["resolvedReference"]["type"], "paper")
        self.assertEqual(second["assistantIntent"]["name"], "create_source_note")
        self.assertEqual(second["materialBundle"]["understanding"]["kind"], "pdf")
        self.assertEqual(second["organization"]["status"], "applied")
        result = second["organization"]["results"][0]
        self.assertEqual(result["path"], "10-Sources/Papers/Delta 讲义.md")
        self.assertTrue((self.vault / result["path"]).is_file())
        self.assertEqual(len([job for job in self.store.list_jobs() if job["kind"] == "prepare-pdf"]), 1)

    def test_deleting_attachment_clears_stale_conversation_focus(self) -> None:
        conversation = self.service.create_conversation({"title": "附件焦点清理"})
        attachment = self.service.create_attachment(
            {
                "conversation_id": conversation["id"],
                "display_name": "临时上下文.txt",
                "kind": "text",
            },
            b"temporary local context",
            "text/plain",
        )
        self.store.save_conversation_focus(conversation["id"], {
            "activeAttachmentIds": [attachment["id"]],
            "activeMaterial": {
                "type": "text",
                "displayName": "临时上下文.txt",
                "sourceAttachmentIds": [attachment["id"]],
            },
            "confidence": 1,
            "schemaVersion": 1,
        })

        self.service.delete_attachment(attachment["id"])

        focus = self.store.get_conversation_focus(conversation["id"])
        self.assertEqual(focus["activeAttachmentIds"], [])
        self.assertIsNone(focus["activeMaterial"])

    def test_pronoun_corpus_keeps_the_active_method(self) -> None:
        first = self.service.submit_intake({"message": "介绍一下 Delta Method。"}, "pronoun-corpus-first")
        conversation_id = first["conversation"]["id"]
        phrases = [
            "继续讲这个方法。", "那个方法有什么假设？", "上述方法适合什么场景？", "刚才的方法再举个例子。",
            "把这个方法和 Bootstrap 比较。", "继续推导。", "前面说的局限是什么？", "刚才那个怎么估计方差？",
            "当前这个知识点容易错在哪里？", "第二个条件为什么需要？", "这个概念和渐近正态有什么关系？",
        ]
        for index, phrase in enumerate(phrases):
            result = self.service.submit_intake({"conversation_id": conversation_id, "message": phrase}, f"pronoun-corpus-{index}")
            resolved = result["focus"]["resolution"]["resolvedReference"]
            self.assertIsNotNone(resolved, phrase)
            self.assertEqual(resolved["canonicalName"], "Delta Method", phrase)

    def test_schema_and_turn_scoped_units_are_repeatable(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 7)
        first = self.service.submit_intake({"message": "倾向得分是什么？"}, "focus-repeat-1")
        second = self.service.submit_intake({
            "conversation_id": first["conversation"]["id"], "message": "继续解释这个概念。",
        }, "focus-repeat-2")
        self.assertNotEqual(first["materialBundle"]["id"], second["materialBundle"]["id"])
        ids = [row[0] for row in self.store.connection.execute("SELECT id FROM knowledge_units").fetchall()]
        self.assertEqual(len(ids), len(set(ids)))
        tables = {row[0] for row in self.store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertTrue({"conversation_focus", "material_bundles", "knowledge_units", "organization_plans", "organization_actions"}.issubset(tables))

    def test_input_bundle_reads_only_the_last_eight_of_a_long_conversation(self) -> None:
        first = self.service.submit_intake({"message": "介绍一下 Delta Method。"}, "long-context-first")
        conversation_id = first["conversation"]["id"]
        last = None
        for index in range(100):
            last = self.service.intake.append_message(conversation_id, "user" if index % 2 == 0 else "assistant", f"历史消息 {index}")
        started = time.perf_counter()
        bundle = self.service.context_material.input.build(conversation_id, last, [], {})
        elapsed = time.perf_counter() - started
        self.assertEqual(len(bundle["recentMessages"]), 8)
        self.assertLess(elapsed, 1.0)

    def test_short_write_confirmation_inherits_prior_target_and_creates_one_change_set(self) -> None:
        conversation = self.service.create_conversation({"title": "统计推断整理"})
        self.service.intake.append_message(conversation["id"], "user", "把统计推断整理成一篇主题笔记")
        proposal = (
            "# 统计推断\n\n统计推断需要区分识别与估计，并说明一致性、无混杂性和重叠性。\n\n"
            "## ✍️ 拟写入计划\n\n目标路径\n\n`20-Knowledge/Topics/统计推断.md`\n"
        )
        source = self.service.intake.append_message(conversation["id"], "assistant", proposal)

        result = self.service.submit_intake({"conversation_id": conversation["id"], "message": "写入"}, "inherit-write")

        self.assertTrue(result["assistantIntent"]["inheritedProposal"])
        self.assertEqual(result["assistantIntent"]["sourceMessageId"], source["id"])
        change = next(item for item in result["artifacts"] if item["type"] == "change_set")
        record = self.store.get_brain_change_set(change["payload"]["changeSetId"])
        self.assertEqual([item["path"] for item in record["writes"]], ["20-Knowledge/Topics/统计推断.md"])
        self.assertFalse((self.vault / "20-Knowledge/Topics/统计推断.md").exists())
        self.assertFalse((self.vault / "01-Inbox/写入.md").exists())
        answer = result["conversation"]["messages"][-1]["content"]
        self.assertIn("尚未写入", answer)
        self.assertIn("20-Knowledge/Topics/统计推断.md", answer)

        status = self.service.submit_intake({"conversation_id": conversation["id"], "message": "你写入到哪里了？"}, "write-status")
        status_answer = status["conversation"]["messages"][-1]["content"]
        self.assertIn("等待确认", status_answer)
        self.assertIn("20-Knowledge/Topics/统计推断.md", status_answer)

    def test_short_write_without_prior_proposal_requests_target_instead_of_saving_command(self) -> None:
        result = self.service.submit_intake({"message": "写入"}, "write-needs-target")
        self.assertEqual(result["artifacts"], [])
        self.assertTrue(result["assistantIntent"]["needsTargetClarification"])
        self.assertIn("不会把“写入”两个字当成笔记正文", result["conversation"]["messages"][-1]["content"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM brain_change_sets").fetchone()[0], 0)

    def test_inherited_write_to_reviewed_target_creates_update_suggestion_only(self) -> None:
        reviewed = self.vault / "20-Knowledge/Topics/正式主题.md"
        original = "---\nstatus: reviewed\n---\n\n# 正式主题\n\n人工确认内容。\n"
        reviewed.write_text(original, encoding="utf-8")
        conversation = self.service.create_conversation({"title": "受保护提案"})
        self.service.intake.append_message(conversation["id"], "assistant", "# 正式主题\n\n建议补充。\n\n## 拟写入计划\n\n目标路径：`20-Knowledge/Topics/正式主题.md`")
        result = self.service.submit_intake({"conversation_id": conversation["id"], "message": "确认写入"}, "reviewed-inherited")
        change = next(item for item in result["artifacts"] if item["type"] == "change_set")
        record = self.store.get_brain_change_set(change["payload"]["changeSetId"])
        self.assertTrue(record["writes"][0]["path"].startswith("90-Local-Only/Agent-Managed/Update-Suggestions/"))
        self.assertEqual(reviewed.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()

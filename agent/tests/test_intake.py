from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent.api.server import serve
from agent.core.intake import IntakeService
from agent.core.models import FakeKeyStore
from agent.core.service import AgentService
from agent.core.storage import StateStore
from agent.core.web_research import WebResearchService
from agent.tools.source_fetch import validate_public_url


class IntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "Vault"
        (self.vault / "01-Inbox").mkdir(parents=True)
        (self.vault / "90-Local-Only/Agent").mkdir(parents=True)
        self.store = StateStore(self.vault / "90-Local-Only/Agent/test.sqlite3")
        self.service = AgentService(self.vault, self.store, key_store=FakeKeyStore())

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_binary_pdf_attachment_is_private_deduplicated_and_indexed(self) -> None:
        conversation = self.service.create_conversation({"title": "PDF"})
        body = b"%PDF-1.4\n% offline fixture\n"
        first = self.service.create_attachment({"conversation_id": conversation["id"], "display_name": "paper.pdf", "kind": "pdf"}, body, "application/pdf")
        second = self.service.create_attachment({"conversation_id": conversation["id"], "display_name": "copy.pdf", "kind": "pdf"}, body, "application/pdf")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertTrue(second["duplicate"])
        self.assertNotIn("storageReference", first)
        self.assertTrue(self.service.intake.resolve_attachment_path(first["id"]).is_relative_to((self.vault / "90-Local-Only/Agent/Attachments").resolve()))

    def test_invalid_mime_signature_and_size_are_rejected(self) -> None:
        conversation = self.service.create_conversation({})
        with self.assertRaisesRegex(ValueError, "invalid_pdf_signature"):
            self.service.create_attachment({"conversation_id": conversation["id"], "display_name": "bad.pdf", "kind": "pdf"}, b"not pdf", "application/pdf")
        with self.assertRaisesRegex(ValueError, "attachment_mime_not_allowed"):
            self.service.create_attachment({"conversation_id": conversation["id"], "display_name": "app.exe"}, b"MZ", "application/x-msdownload")

    def test_local_path_requires_explicit_selection_and_symlink_is_rejected(self) -> None:
        conversation = self.service.create_conversation({})
        outside = self.root / "outside.md"
        outside.write_text("private", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not_authorized"):
            self.service.create_attachment({"conversation_id": conversation["id"], "path": str(outside)})
        attachment = self.service.create_attachment({"conversation_id": conversation["id"], "path": str(outside), "explicit_user_selection": True})
        self.assertEqual(attachment["kind"], "local_path")
        link = self.root / "link.md"
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.service.create_attachment({"conversation_id": conversation["id"], "path": str(link), "explicit_user_selection": True})

    def test_folder_threshold_returns_minimal_clarification(self) -> None:
        conversation = self.service.create_conversation({})
        folder = self.root / "batch"
        folder.mkdir()
        for index in range(21):
            (folder / f"{index}.md").write_text(str(index), encoding="utf-8")
        result = self.service.create_attachment({"conversation_id": conversation["id"], "path": str(folder), "explicit_user_selection": True})
        self.assertTrue(result["requiresConfirmation"])
        self.assertEqual(result["fileCount"], 21)
        self.assertEqual(len(result["options"]), 4)
        confirmed = self.service.create_attachment({"conversation_id": conversation["id"], "path": str(folder), "explicit_user_selection": True, "confirmed_large_folder": True})
        self.assertEqual(confirmed["kind"], "folder")

    def test_raw_conversation_text_lives_in_private_file_not_sqlite(self) -> None:
        marker = "PRIVATE-ORIGINAL-DO-NOT-PERSIST-IN-SQLITE"
        result = self.service.submit_intake({"message": f"请保存这个灵感：{marker}"}, "intake-private")
        dump = "\n".join(self.store.connection.iterdump())
        self.assertNotIn(marker, dump)
        conversation = result["conversation"]
        self.assertIn(marker, conversation["messages"][0]["content"])
        request_path = self.vault / "90-Local-Only/Agent/brain-requests"
        self.assertTrue(any(request_path.glob("*.json")))

    def test_text_attachment_creates_traceable_material_artifact_without_copying_body_to_sqlite(self) -> None:
        marker = "PRIVATE-CLASS-NOTES-BODY"
        conversation = self.service.create_conversation({"title": "课堂笔记"})
        attachment = self.service.create_attachment(
            {"conversation_id": conversation["id"], "display_name": "课堂笔记.md", "kind": "text"},
            f"# 课堂笔记\n\n{marker}\n".encode(), "text/markdown",
        )
        result = self.service.submit_intake({
            "conversation_id": conversation["id"], "message": "整理这份课堂笔记",
            "attachments": [{"attachment_id": attachment["id"]}],
        }, "text-material-flow")
        material = next(item for item in result["artifacts"] if item["type"] == "material")
        self.assertEqual(material["payload"]["sourceType"], "用户文本")
        self.assertEqual(material["payload"]["attachmentId"], attachment["id"])
        self.assertEqual(material["payload"]["originalContent"], "保留在 90-Local-Only 的受控附件引用中")
        self.assertNotIn(marker, "\n".join(self.store.connection.iterdump()))

    def test_public_url_is_fetched_once_and_becomes_material_plus_research_bundle(self) -> None:
        calls = []
        def fake_fetch(payload):
            calls.append(payload["url"])
            return {"url": payload["url"], "content_type": "text/html", "bytes": 180,
                    "text": "<title>公开课程</title><article>这是一段公开课程正文，用于解释回归假设、识别边界以及进一步阅读方向。</article>"}
        self.service.web = WebResearchService(self.vault, self.store, fetcher=fake_fetch)
        conversation = self.service.create_conversation({"title": "网页资料"})
        attachment = self.service.create_attachment({
            "conversation_id": conversation["id"], "url": "https://1.1.1.1/article", "allow_network": True,
        })
        result = self.service.submit_intake({
            "conversation_id": conversation["id"], "message": "研究这个公开链接并整理关键内容",
            "attachments": [{"attachment_id": attachment["id"]}],
        }, "url-material-flow")
        self.assertEqual(calls, ["https://1.1.1.1/article"])
        self.assertTrue({"material", "research_bundle"}.issubset({item["type"] for item in result["artifacts"]}))
        research = next(item for item in result["artifacts"] if item["type"] == "research_bundle")
        self.assertEqual(research["payload"]["sourceType"], "公开网页")
        self.assertEqual(len(self.store.list_web_sources()), 1)
        save_conversation = self.service.create_conversation({"title": "保存网页"})
        save_attachment = self.service.create_attachment({
            "conversation_id": save_conversation["id"], "url": "https://1.1.1.1/article", "allow_network": True,
        })
        saved = self.service.submit_intake({
            "conversation_id": save_conversation["id"], "message": "研究这个网页并保存到 Obsidian",
            "attachments": [{"attachment_id": save_attachment["id"]}],
            "mode": "save",
        }, "url-save-flow")
        web_change = next(item for item in saved["artifacts"] if item["type"] == "change_set")
        self.assertIn("网页研究包", web_change["payload"]["summary"])
        self.assertEqual(calls, ["https://1.1.1.1/article"])

    def test_structured_save_produces_a_reversible_write_result(self) -> None:
        result = self.service.submit_intake({"message": "用知识缺口驱动学习任务", "mode": "save"}, "intake-artifacts")
        types = {item["type"] for item in result["artifacts"]}
        self.assertIn("write_result", types)
        self.assertEqual(result["organization"]["status"], "applied")
        changed = next(item for item in result["organization"]["results"] if item.get("path"))
        self.assertTrue(changed["undoAvailable"])
        self.assertTrue((self.vault / changed["path"]).exists())

    def test_followup_revises_active_artifact_in_same_conversation(self) -> None:
        first = self.service.submit_intake({"message": "Agent 应该先生成提案", "mode": "save"}, "intake-first")
        active = first["conversation"]["activeArtifactId"]
        second = self.service.submit_intake({"conversation_id": first["conversation"]["id"], "message": "不要拆成三篇，只保留一篇主笔记。", "artifact_revision": True}, "intake-second")
        self.assertEqual(second["artifacts"][0]["id"], active)
        self.assertEqual(second["artifacts"][0]["version"], 2)
        self.assertEqual(second["run"]["primary_intent"], "continue_artifact_revision")

    def test_intake_idempotency_does_not_duplicate_artifacts(self) -> None:
        body = {"message": "同一个请求只执行一次", "mode": "save"}
        first = self.service.submit_intake(body, "same-intake-key")
        second = self.service.submit_intake(body, "same-intake-key")
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["run"]["id"], second["run"]["id"])
        self.assertEqual({item["id"] for item in first["artifacts"]}, {item["id"] for item in second["artifacts"]})
        self.assertEqual(first["materialBundle"]["id"], second["materialBundle"]["id"])
        self.assertIn("assistantIntent", second)
        self.assertIn("focus", second)

    def test_assistant_task_thread_groups_one_learning_pack_and_one_quiz(self) -> None:
        result = self.service.submit_intake(
            {"message": "把 PSM 整理成学习包，先讲直觉，暂时不要公式。", "mode": "tutor", "requested_output": "learning_pack"},
            "assistant-learning-pack",
        )
        self.assertEqual(result["task_thread"]["status"], "completed")
        self.assertEqual(len(result["task_thread"]["steps"]), 5)
        self.assertTrue(all(step["status"] == "completed" for step in result["task_thread"]["steps"]))
        self.assertIsNotNone(result["artifact_group"])
        learning_packs = [item for item in result["artifacts"] if item["type"] == "learning_pack"]
        self.assertEqual(len(learning_packs), 1)
        self.assertEqual(learning_packs[0]["title"], "PSM入门学习包")
        self.assertFalse(any(item["type"] == "quiz" for item in result["artifacts"]))
        self.assertTrue(learning_packs[0]["payload"]["noFormula"])
        self.assertEqual(len(learning_packs[0]["payload"]["quizPreview"]), 1)
        conversation = self.service.get_conversation(result["conversation"]["id"])
        self.assertEqual(conversation["title"], "把 PSM 整理成学习包，先讲直觉，暂时不要公式。")
        self.assertEqual(conversation["latestTaskThread"]["id"], result["task_thread"]["id"])
        self.assertEqual(conversation["latestArtifactGroup"]["primaryArtifactId"], learning_packs[0]["id"])

    def test_ordinary_question_answers_and_tracks_without_artifact_or_task_thread(self) -> None:
        result = self.service.submit_intake(
            {"message": "PSM 和普通回归调整有什么区别？", "mode": "tutor"},
            "assistant-answer-only-result",
        )
        self.assertEqual(result["outcome"]["kind"], "answer_and_track")
        self.assertEqual(result["artifacts"], [])
        self.assertIsNone(result["task_thread"])
        self.assertIsNone(result["artifact_group"])
        self.assertTrue(result["intelligence"]["tracked"])
        conversation = self.service.get_conversation(result["conversation"]["id"])
        self.assertEqual(conversation["messages"][-1]["messageType"], "answer")
        self.assertTrue(conversation["summary"])
        self.assertTrue(conversation["knowledgeSignals"])

    def test_personalization_can_disable_signal_and_summary_tracking(self) -> None:
        conversation = self.service.create_conversation({"title": "不保留个性化"})
        self.service.update_conversation_preferences(
            conversation["id"], {"personalization_enabled": False, "retention_policy": "session"},
        )
        result = self.service.submit_intake(
            {"conversation_id": conversation["id"], "message": "解释一下倾向得分"},
            "assistant-no-personalization",
        )
        self.assertEqual(result["outcome"]["kind"], "answer_only")
        self.assertFalse(result["intelligence"]["tracked"])
        self.assertEqual(self.store.list_conversation_signals(conversation["id"]), [])
        self.assertIsNone(self.store.latest_conversation_summary(conversation["id"]))

    def test_conversation_export_summary_only_and_delete_are_explicit_local_controls(self) -> None:
        marker = "PRIVATE-CONVERSATION-EXPORT-MARKER"
        result = self.service.submit_intake({"message": f"请解释倾向得分：{marker}"}, "conversation-controls")
        conversation_id = result["conversation"]["id"]
        exported = self.service.export_conversation(conversation_id)
        export_path = self.vault / "90-Local-Only/Agent" / exported["reference"]
        self.assertTrue(export_path.is_file())
        self.assertIn(marker, export_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "explicit_confirmation_required"):
            self.service.retain_conversation_summary(conversation_id, False)
        retained = self.service.retain_conversation_summary(conversation_id, True)
        self.assertGreaterEqual(retained["removedMessages"], 2)
        conversation = self.service.get_conversation(conversation_id)
        self.assertEqual(conversation["retentionPolicy"], "summary_only")
        self.assertEqual(conversation["messages"], [])
        self.assertIsNotNone(conversation["summary"])
        with self.assertRaisesRegex(ValueError, "explicit_confirmation_required"):
            self.service.delete_conversation(conversation_id, False)
        deleted = self.service.delete_conversation(conversation_id, True)
        self.assertTrue(deleted["deleted"])
        with self.assertRaisesRegex(ValueError, "conversation_not_found"):
            self.service.get_conversation(conversation_id)

    def test_clear_conversations_supports_recent_seven_days_and_all_with_confirmation(self) -> None:
        first = self.service.create_conversation({"title": "one"})
        second = self.service.create_conversation({"title": "two"})
        with self.assertRaisesRegex(ValueError, "explicit_confirmation_required"):
            self.service.clear_conversations("all", False)
        recent = self.service.clear_conversations("recent-7-days", True)
        self.assertEqual(recent["deletedCount"], 2)
        self.assertEqual(self.service.list_conversations()["items"], [])
        self.assertTrue(first["id"]); self.assertTrue(second["id"])

    def test_repeated_signal_is_deduplicated_and_recurrence_increases(self) -> None:
        conversation = self.service.create_conversation({"title": "信号去重"})
        self.service.submit_intake(
            {"conversation_id": conversation["id"], "message": "我不理解倾向得分为什么能用于匹配"},
            "assistant-signal-first",
        )
        self.service.submit_intake(
            {"conversation_id": conversation["id"], "message": "倾向得分为什么这么定义？我还是不理解"},
            "assistant-signal-second",
        )
        signals = self.store.list_conversation_signals(conversation["id"])
        topic = next(item for item in signals if item["signalType"] == "topic" and item["topic"] == "倾向得分")
        self.assertEqual(topic["recurrence"], 2)

    def test_explicit_today_time_command_adjusts_plan_without_artifact(self) -> None:
        result = self.service.submit_intake({"message": "今天只有 15 分钟，请调整今日安排。"}, "assistant-adjust-today")
        self.assertEqual(result["artifacts"], [])
        self.assertIsNotNone(result["dailyAdjustment"])
        self.assertLessEqual(result["dailyAdjustment"]["afterMinutes"], 15)
        conversation = self.service.get_conversation(result["conversation"]["id"])
        self.assertEqual(conversation["messages"][-1]["messageType"], "action-result")
        self.assertIn("今日安排已调整", conversation["messages"][-1]["content"])

    def test_new_conversation_is_named_from_first_user_request(self) -> None:
        conversation = self.service.create_conversation({"title": "新会话"})
        result = self.service.submit_intake(
            {"conversation_id": conversation["id"], "message": "帮我制定 PSM 的三天学习安排"},
            "assistant-conversation-title",
        )
        self.assertEqual(result["conversation"]["title"], "帮我制定 PSM 的三天学习安排")

    def test_artifact_creation_is_deduplicated_and_today_integration_is_idempotent(self) -> None:
        conversation = self.service.create_conversation({"title": "dedupe"})
        first = self.service.intake.create_artifact(
            "learning_pack", "PSM 入门", "draft", conversation["id"], "run-same",
            {"estimatedMinutes": 12, "quizPreview": ["复述 PSM"]},
        )
        second = self.service.intake.create_artifact(
            "learning_pack", "  psm   入门 ", "draft", conversation["id"], "run-same",
            {"estimatedMinutes": 12, "quizPreview": ["复述 PSM"]},
        )
        self.assertEqual(first["id"], second["id"])
        added = self.service.add_artifact_to_today(first["id"])
        duplicate = self.service.add_artifact_to_today(first["id"])
        self.assertFalse(added["duplicate"]); self.assertTrue(duplicate["duplicate"])
        self.assertTrue(self.service.assistant_context(first["id"], conversation["id"])["inToday"])
        self.assertEqual(sum(item["id"] == added["recommendation"]["id"] for item in self.service.list_recommendations()), 1)
        self.assertTrue(self.service.undo_artifact_today(first["id"])["removed"])
        self.assertFalse(self.service.assistant_context(first["id"], conversation["id"])["inToday"])
        self.assertFalse(any(item["id"] == added["recommendation"]["id"] for item in self.service.list_recommendations()))

    def test_pdf_intake_creates_material_artifact_and_visible_material_row(self) -> None:
        conversation = self.service.create_conversation({"title": "Dragonnet"})
        attachment = self.service.create_attachment({"conversation_id": conversation["id"], "display_name": "Dragonnet.pdf", "kind": "pdf"}, b"%PDF-1.4\nfixture", "application/pdf")
        result = self.service.submit_intake({"conversation_id": conversation["id"], "message": "帮我整理这个 PDF，并安排后续学习", "attachments": [{"attachment_id": attachment["id"]}]}, "pdf-flow")
        self.assertIn("material", {item["type"] for item in result["artifacts"]})
        materials = self.service.list_materials()["items"]
        self.assertEqual(materials[0]["title"], "Dragonnet.pdf")
        self.assertGreaterEqual(materials[0]["artifactCount"], 1)
        self.assertTrue(any(job["kind"] == "prepare-pdf" for job in self.service.list_jobs()))

    def test_ssrf_guards_reject_local_private_and_file_urls(self) -> None:
        for url in ("http://localhost/a", "file:///tmp/a", "http://127.0.0.1/a", "http://10.0.0.2/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_url(url, resolver=lambda _: ["127.0.0.1"] if "10." not in url else ["10.0.0.2"])

    def test_authenticated_intake_and_artifact_api(self) -> None:
        try:
            server = serve(self.vault, port=0, session_token="session", key_store=FakeKeyStore())
        except PermissionError:
            self.skipTest("当前沙箱禁止绑定 localhost；API 契约由无套接字测试继续覆盖")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/api/v1"
        try:
            request = urllib.request.Request(
                f"{base}/intake/submit", method="POST", data=json.dumps({"message": "API Intake", "mode": "save"}).encode(),
                headers={"Authorization": "Bearer session", "Content-Type": "application/json", "Idempotency-Key": "api-intake"},
            )
            with urllib.request.urlopen(request) as response:
                payload = json.load(response)
            self.assertTrue(payload["artifacts"])
            conversation_id = payload["conversation"]["id"]
            bundle_id = payload["materialBundle"]["id"]
            for path, key in ((f"/conversation-focus/{conversation_id}", "focus"), (f"/material-bundles/{bundle_id}", "bundle")):
                with urllib.request.urlopen(urllib.request.Request(base + path, headers={"Authorization": "Bearer session"})) as response:
                    context_payload = json.load(response)
                self.assertIn(key, context_payload)
            with urllib.request.urlopen(urllib.request.Request(f"{base}/artifacts", headers={"Authorization": "Bearer session"})) as response:
                artifacts = json.load(response)
            self.assertTrue(artifacts["items"])
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/artifacts")
            self.assertEqual(caught.exception.code, 401)
        finally:
            server.shutdown(); server.server_close(); server.RequestHandlerClass.service.store.close(); thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

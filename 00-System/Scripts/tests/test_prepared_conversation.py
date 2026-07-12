from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(SCRIPTS))
import ingest_pdf as ingest
import prepared_conversation as conversation
import prepared_pdf


RESULT = {
    "title": "关于因果推断的讨论", "goal": "理解识别与估计。", "unresolved": "需要核对教材。",
    "key_points": [
        {"claim": "用户希望区分识别和估计。", "speaker": "user", "verification": "user-provided", "evidence_sections": [1]},
        {"claim": "助手解释了重叠性。", "speaker": "assistant", "verification": "needs-verification", "evidence_sections": [1]},
    ],
    "topic": {"title": "观测数据中的因果推断", "synthesis": "围绕识别假设组织估计方法。", "evidence_sections": [1]},
    "concepts": [{"title": "重叠性", "definition": "各处理均有正概率。", "conditions": "目标总体支持集。", "why_reusable": "是多种估计器前提。", "reusable": True, "evidence_sections": [1]}],
}


class Client:
    def __init__(self): self.calls = 0
    def create_json(self, **kwargs): self.calls += 1; return json.dumps(RESULT, ensure_ascii=False)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name); self.vault = self.root / "Vault"; self.vault.mkdir()
        for rel in ("10-Sources/AI-Conversations", "20-Knowledge/Topics", "20-Knowledge/Concepts", "20-Knowledge/MOCs", "90-Local-Only/AI-Drafts"):
            (self.vault / rel).mkdir(parents=True, exist_ok=True)
        self.input = self.root / "chat.md"; self.input.write_text("User: 什么是重叠性？\nAssistant: ...", encoding="utf-8")
        self.args = argparse.Namespace(input=str(self.input), vault=str(self.vault), platform="ChatGPT", model="fake", base_url="invalid")

    def tearDown(self): self.temp.cleanup()

    def test_prepare_archives_full_conversation_once_without_formal_write(self):
        client = Client(); bundle = conversation.prepare_conversation(self.args, client=client, now=datetime(2026, 7, 12, tzinfo=timezone.utc))
        self.assertEqual(client.calls, 1)
        self.assertEqual((bundle / "raw-conversation.txt").read_text(encoding="utf-8"), self.input.read_text(encoding="utf-8"))
        self.assertEqual(list((self.vault / "10-Sources/AI-Conversations").glob("*.md")), [])
        self.assertIn("needs-verification", (bundle / "normalized-result.json").read_text(encoding="utf-8"))

    def test_apply_uses_shared_prepared_transaction_without_model(self):
        bundle = conversation.prepare_conversation(self.args, client=Client(), now=datetime(2026, 7, 12, tzinfo=timezone.utc))
        with mock.patch.object(ingest, "DeepSeekClient", side_effect=AssertionError("network forbidden")):
            prepared_pdf.apply_prepared(self.vault, bundle.name)
        source = next((self.vault / "10-Sources/AI-Conversations").glob("*.md"))
        self.assertIn("待验证来源", source.read_text(encoding="utf-8"))
        concept = next((self.vault / "20-Knowledge/Concepts").glob("*.md"))
        self.assertIn("对话片段 1", concept.read_text(encoding="utf-8"))

    def test_assistant_claim_cannot_be_marked_verified(self):
        invalid = json.loads(json.dumps(RESULT)); invalid["key_points"][1]["verification"] = "verified"
        with self.assertRaises(ingest.ValidationError): conversation.validate_conversation_result(invalid, 1)

    def test_reviewed_same_name_gets_update_suggestion(self):
        target = self.vault / "20-Knowledge/Topics/观测数据中的因果推断.md"
        target.write_text('---\ntype: topic\nstatus: "reviewed"\n---\n# 正式知识\n', encoding="utf-8")
        bundle = conversation.prepare_conversation(self.args, client=Client(), now=datetime(2026, 7, 12, tzinfo=timezone.utc))
        changes = json.loads((bundle / "change-set.json").read_text(encoding="utf-8"))["writes"]
        self.assertFalse(any(item["target"] == str(target.relative_to(self.vault)) for item in changes))
        self.assertTrue(any(item["category"] == "suggestion" for item in changes))


if __name__ == "__main__": unittest.main()

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import ingest_pdf as ingest
import prepared_pdf as prepared
from test_ingest_pdf import FakeClient, valid_result


class PreparedPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "Vault"
        for relative in (
            "10-Sources/Papers", "20-Knowledge/Topics", "20-Knowledge/Concepts",
            "20-Knowledge/MOCs", "90-Local-Only/AI-Drafts",
        ):
            (self.vault / relative).mkdir(parents=True, exist_ok=True)
        self.pdf = self.root / "paper.pdf"
        self.pdf.write_bytes(b"stable-pdf")
        self.now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            pdf=str(self.pdf), vault=str(self.vault), kind="paper", domain_focus="因果推断",
            model="fake", base_url="https://invalid.example", max_concepts=3,
        )

    def prepare(self, result=None, now=None) -> tuple[Path, FakeClient]:
        client = FakeClient(result=result or valid_result())
        bundle = prepared.prepare_bundle(
            self.args(), client=client, pages=["--- PAGE 1 ---\ntext\n"], now=now or self.now,
        )
        return bundle, client

    def test_prepare_calls_model_once_and_writes_no_knowledge_targets(self):
        bundle, client = self.prepare()
        self.assertEqual(client.calls, 1)
        required = {
            "request.json", "normalized-result.json", "evidence.json", "vault-inventory.json",
            "change-set.json", "preview.md", "manifest.json",
        }
        self.assertTrue(required.issubset({path.name for path in bundle.iterdir()}))
        self.assertEqual(list((self.vault / "10-Sources/Papers").glob("*.md")), [])
        self.assertEqual(list((self.vault / "20-Knowledge/Topics").glob("*.md")), [])

    def test_inspect_is_read_only(self):
        bundle, _ = self.prepare()
        before = {str(path.relative_to(self.vault)): ingest.file_sha256(path) for path in self.vault.rglob("*") if path.is_file()}
        preview = prepared.inspect_bundle(self.vault, bundle.name)
        after = {str(path.relative_to(self.vault)): ingest.file_sha256(path) for path in self.vault.rglob("*") if path.is_file()}
        self.assertIn("Change Set", preview)
        self.assertEqual(before, after)

    def test_bundle_tampering_is_rejected(self):
        bundle, _ = self.prepare()
        (bundle / "preview.md").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "篡改"):
            prepared.apply_prepared(self.vault, bundle.name)

    def test_pdf_hash_change_is_rejected(self):
        bundle, _ = self.prepare()
        self.pdf.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "原始输入"):
            prepared.apply_prepared(self.vault, bundle.name)

    def test_target_change_is_rejected(self):
        bundle, _ = self.prepare()
        changes = json.loads((bundle / "change-set.json").read_text(encoding="utf-8"))["writes"]
        target = self.vault / changes[0]["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "存在性"):
            prepared.apply_prepared(self.vault, bundle.name)

    def test_apply_does_not_create_model_client_and_is_idempotent(self):
        bundle, _ = self.prepare()
        with mock.patch.object(ingest, "DeepSeekClient", side_effect=AssertionError("network forbidden")):
            first = prepared.apply_prepared(self.vault, bundle.name)
            second = prepared.apply_prepared(self.vault, bundle.name)
        self.assertEqual(first, second)
        self.assertTrue(first.exists())
        self.assertEqual(len(prepared.list_prepared(self.vault)), 1)
        self.assertEqual(prepared.list_prepared(self.vault)[0]["state"], "applied")

    def test_human_area_change_is_allowed_and_preserved(self):
        first, _ = self.prepare()
        prepared.apply_prepared(self.vault, first.name)
        second, _ = self.prepare(now=self.now.replace(minute=1))
        change_set = json.loads((second / "change-set.json").read_text(encoding="utf-8"))["writes"]
        source_change = next(item for item in change_set if item["category"] == "source")
        source = self.vault / source_change["target"]
        source.write_text(source.read_text(encoding="utf-8") + "\n人工新增内容。\n", encoding="utf-8")
        prepared.apply_prepared(self.vault, second.name)
        self.assertIn("人工新增内容。", source.read_text(encoding="utf-8"))

    def test_reviewed_status_change_is_rejected(self):
        first, _ = self.prepare()
        prepared.apply_prepared(self.vault, first.name)
        second, _ = self.prepare(now=self.now.replace(minute=2))
        change_set = json.loads((second / "change-set.json").read_text(encoding="utf-8"))["writes"]
        topic_change = next(item for item in change_set if item["category"] == "topic")
        topic = self.vault / topic_change["target"]
        topic.write_text(topic.read_text(encoding="utf-8").replace('status: "ai-draft"', 'status: "reviewed"'), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "只读"):
            prepared.apply_prepared(self.vault, second.name)

    def test_reject_blocks_apply(self):
        bundle, _ = self.prepare()
        prepared.reject_prepared(self.vault, bundle.name, "low quality")
        with self.assertRaisesRegex(RuntimeError, "已拒绝"):
            prepared.apply_prepared(self.vault, bundle.name)


if __name__ == "__main__":
    unittest.main()

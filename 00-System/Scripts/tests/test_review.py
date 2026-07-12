from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import ingest_pdf as ingest
import review
from test_ingest_pdf import valid_result


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        for rel in ("20-Knowledge/Concepts", "20-Knowledge/Topics", "20-Knowledge/MOCs", "90-Local-Only/AI-Drafts"):
            (self.vault / rel).mkdir(parents=True, exist_ok=True)
        concept = valid_result()["concepts"][0]
        self.source_id = "pdf-" + "a" * 64
        self.artifact_id = f"{self.source_id}:concept:0"
        self.path = self.vault / "20-Knowledge/Concepts/倾向得分.md"
        self.path.write_text(ingest.render_concept(
            concept, source_id=self.source_id, source_link="source", domain="因果推断",
            today="2026-07-12", artifact_id=self.artifact_id,
        ) + "\n人工备注。\n", encoding="utf-8")

    def tearDown(self): self.temp.cleanup()

    def test_list_show_and_diff_include_traceability(self):
        items = review.scan_artifacts(self.vault)
        self.assertEqual(items[0]["artifact_id"], self.artifact_id)
        packet = review.review_packet(self.vault, self.artifact_id)
        self.assertIn(self.source_id, packet)
        self.assertIn("Evidence pages: 1", packet)
        self.assertIn(str(self.path), review.artifact_diff(self.vault, self.artifact_id))

    def test_approve_is_transactional_and_preserves_body(self):
        audit = review.transition(self.vault, self.artifact_id, "approve")
        text = self.path.read_text(encoding="utf-8")
        meta = ingest.parse_frontmatter(text)
        self.assertEqual((meta["status"], meta["review_state"]), ("reviewed", "accepted"))
        self.assertIn("人工备注。", text)
        self.assertTrue(audit.exists())
        transactions = list((self.vault / "90-Local-Only/Processing-Cache/transactions").glob("review-*"))
        self.assertEqual(len(transactions), 1)

    def test_approve_edited_records_mode(self):
        review.transition(self.vault, self.artifact_id, "approve-edited")
        self.assertEqual(ingest.parse_frontmatter(self.path.read_text(encoding="utf-8"))["review_mode"], "edited")

    def test_reject_requires_reason_and_reopen_does_not_downgrade_reviewed(self):
        with self.assertRaises(RuntimeError): review.transition(self.vault, self.artifact_id, "reject")
        review.transition(self.vault, self.artifact_id, "reject", "too narrow")
        meta = ingest.parse_frontmatter(self.path.read_text(encoding="utf-8"))
        self.assertEqual((meta["status"], meta["review_state"]), ("rejected", "rejected"))
        review.transition(self.vault, self.artifact_id, "reopen")
        self.assertEqual(ingest.parse_frontmatter(self.path.read_text(encoding="utf-8"))["status"], "ai-draft")
        review.transition(self.vault, self.artifact_id, "approve")
        with self.assertRaises(RuntimeError): review.transition(self.vault, self.artifact_id, "reopen")

    def test_rejected_artifact_is_not_regenerated(self):
        review.transition(self.vault, self.artifact_id, "reject", "not reusable")
        pdf = Path(self.temp.name) / "paper.pdf"; pdf.write_bytes(b"x")
        result = valid_result()
        plan = ingest.build_plan(
            self.vault, pdf_path=pdf, digest="a" * 64, result=result, model="fake",
            kind="paper", domain_focus="因果推断", max_concepts=3,
            inventory=ingest.scan_vault(self.vault), now=datetime(2026, 7, 12, tzinfo=timezone.utc),
        )
        self.assertFalse(any(item.category == "concept" and item.path == self.path for item in plan.writes))
        self.assertTrue(any("已拒绝" in item for item in plan.skipped))


if __name__ == "__main__": unittest.main()

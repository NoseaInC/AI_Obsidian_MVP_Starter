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


def valid_result(page: int = 1) -> dict:
    return {
        "title": "测试论文",
        "one_sentence_summary": "一句话。",
        "detailed_summary": "详细摘要。",
        "research_question": "研究什么？",
        "methods_and_assumptions": "方法与假设。",
        "contributions": "贡献。",
        "main_results": "结果。",
        "limitations": "局限。",
        "concept_relations": "关系。",
        "existing_knowledge_links": "关联。",
        "followup_questions": "后续问题。",
        "evidence": [{"claim": "结论", "pages": [page], "excerpt": "证据", "kind": "paper"}],
        "inferences": [{"statement": "可能推广", "evidence_pages": [page]}],
        "topic": {
            "title": "因果效应估计", "problem": "估计处理效应。", "problem_setting": "观测数据。",
            "prerequisites": "概率论。", "assumptions": "可忽略性。", "method_routes": "匹配与加权。",
            "paper_position": "机器学习路线。", "concept_relations": "倾向得分连接处理与协变量。",
            "confusions": "相关不等于因果。", "next_steps": "学习识别。", "evidence_pages": [page],
        },
        "concepts": [
            {
                "title": "倾向得分", "definition": "给定协变量的处理概率。", "conditions": "重叠性。",
                "intuition": "压缩协变量。", "formula": "e(x)=P(T=1|X=x)", "confusions": "结果模型。",
                "example": "治疗选择。", "common_errors": "忽略重叠。",
                "review_questions": ["定义？", "假设？", "用途？"], "evidence_pages": [page],
                "mainline_score": 5, "reuse_score": 5, "prerequisite_score": 4, "scope_score": 4,
                "paper_specific": False, "selection_reason": "跨论文复用。",
            },
            {
                "title": "论文专属头部", "definition": "局部模块。", "conditions": "本论文。",
                "intuition": "辅助训练。", "formula": "L=L1+L2", "confusions": "通用正则。",
                "example": "本论文网络。", "common_errors": "当成通用方法。",
                "review_questions": ["是什么？", "为何用？", "边界？"], "evidence_pages": [page],
                "mainline_score": 4, "reuse_score": 3, "prerequisite_score": 2, "scope_score": 2,
                "paper_specific": True, "selection_reason": "论文局部组件。",
            },
        ],
        "update_suggestions": [],
    }


class FakeClient:
    def __init__(self, result: dict | None = None, final_raws: list[str] | None = None):
        self.result = result or valid_result()
        self.final_raws = list(final_raws or [])
        self.calls = 0

    def create_json(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        if "证据提取器" in system:
            return json.dumps({"evidence": [{"claim": "结论", "pages": [1], "excerpt": "证据", "kind": "paper"}]}, ensure_ascii=False)
        if self.final_raws:
            return self.final_raws.pop(0)
        return json.dumps(self.result, ensure_ascii=False)


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / "Vault 中文"
        for rel in (
            "20-Knowledge/Concepts", "20-Knowledge/Topics", "20-Knowledge/MOCs",
            "10-Sources/Papers", "90-Local-Only/AI-Drafts",
        ):
            (self.vault / rel).mkdir(parents=True, exist_ok=True)
        self.pdf = self.root / "有 空格.pdf"
        self.pdf.write_bytes(b"fake-pdf-for-hash")
        self.now = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def args(self, apply: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            pdf=str(self.pdf), vault=str(self.vault), kind="paper", domain_focus="因果推断",
            model="fake-model", base_url="https://invalid.example", max_concepts=3,
            apply=apply, dry_run=not apply, force_regenerate=False, verbose=False, max_chars=80_000,
        )

    def write_note(self, rel: str, frontmatter: str) -> Path:
        path = self.vault / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{frontmatter}\n---\n# {path.stem}\n", encoding="utf-8")
        return path

    def plan(self, inventory=None):
        digest = ingest.file_sha256(self.pdf)
        return ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=digest, result=valid_result(), model="fake-model",
            kind="paper", domain_focus="因果推断", max_concepts=3,
            inventory=inventory if inventory is not None else ingest.scan_vault(self.vault), now=self.now,
        )

    def test_filename_sanitizing(self):
        self.assertEqual(ingest.sanitize_filename(' A/B:*? "题"  '), "A-B- -题-")
        self.assertEqual(ingest.sanitize_filename("..."), "未命名资料")

    def test_frontmatter_scanning(self):
        self.write_note(
            "20-Knowledge/Concepts/倾向得分.md",
            'type: concept\nstatus: reviewed\ndomain: 因果推断\naliases:\n  - "propensity score"\nsource_notes: []\ngenerated_from: "manual"',
        )
        item = ingest.scan_vault(self.vault)[0]
        self.assertEqual((item.title, item.status, item.aliases), ("倾向得分", "reviewed", ("propensity score",)))

    def test_reviewed_or_core_is_never_overwritten(self):
        target = self.write_note("20-Knowledge/Topics/因果效应估计.md", "type: topic\nstatus: core")
        before = target.read_text(encoding="utf-8")
        plan = self.plan()
        ingest.execute_plan(plan)
        self.assertEqual(target.read_text(encoding="utf-8"), before)
        self.assertTrue(any(x.category == "suggestion" for x in plan.writes))

    def test_same_source_ai_draft_is_idempotently_updated(self):
        digest = ingest.file_sha256(self.pdf)
        self.write_note(
            "20-Knowledge/Topics/因果效应估计.md",
            f'type: topic\nstatus: ai-draft\ngenerated_from: "pdf-{digest}"',
        )
        plan = self.plan()
        item = next(x for x in plan.writes if x.category == "topic")
        self.assertEqual(item.action, "update")

    def test_different_source_same_name_is_not_overwritten(self):
        original = self.write_note(
            "20-Knowledge/Topics/因果效应估计.md",
            'type: topic\nstatus: ai-draft\ngenerated_from: "pdf-other"',
        )
        before = original.read_text(encoding="utf-8")
        plan = self.plan()
        topic = next(x for x in plan.writes if x.category == "topic")
        self.assertNotEqual(topic.path, original)
        ingest.execute_plan(plan)
        self.assertEqual(original.read_text(encoding="utf-8"), before)

    def test_concept_candidate_sorting(self):
        concepts = valid_result()["concepts"]
        ranked = ingest.rank_concepts(concepts, 3)
        self.assertEqual([x["title"] for x in ranked], ["倾向得分"])

    def test_paper_specific_is_penalized(self):
        generic, specific = valid_result()["concepts"]
        specific.update({"mainline_score": 5, "reuse_score": 5, "prerequisite_score": 4, "scope_score": 4})
        self.assertLess(ingest.concept_score(specific), ingest.concept_score(generic))

    def test_even_high_scoring_paper_specific_concept_is_not_promoted(self):
        specific = valid_result()["concepts"][1]
        specific.update({
            "mainline_score": 5, "reuse_score": 5,
            "prerequisite_score": 5, "scope_score": 5,
        })
        self.assertEqual(ingest.concept_score(specific), 28)
        self.assertEqual(ingest.rank_concepts([specific], 3), [])

    def test_out_of_range_page_is_rejected(self):
        data = valid_result(page=2)
        with self.assertRaises(ingest.ValidationError):
            ingest.validate_model_result(data, page_count=1)

    def test_narrative_string_lists_are_normalized_locally(self):
        data = valid_result()
        data["main_results"] = ["结果一。", "结果二。"]
        validated = ingest.validate_model_result(data, page_count=1)
        self.assertEqual(validated["main_results"], "- 结果一。\n- 结果二。")

    def test_invalid_json_after_repair_writes_nothing(self):
        client = FakeClient(final_raws=["not-json", "still-not-json"])
        with self.assertRaises(ingest.ValidationError):
            ingest.run_ingestion(self.args(apply=True), client=client, pages=["--- PAGE 1 ---\ntext"], now=self.now)
        self.assertEqual(list((self.vault / "10-Sources/Papers").glob("*.md")), [])
        self.assertFalse((self.vault / "90-Local-Only/Processing-Cache").exists())

    def test_dry_run_produces_no_files(self):
        ingest.run_ingestion(self.args(apply=False), client=FakeClient(), pages=["--- PAGE 1 ---\ntext"], now=self.now)
        self.assertEqual(list(self.vault.rglob("*.md")), [])
        self.assertEqual(list(self.vault.rglob("*.json")), [])

    def test_fake_client_path_does_not_read_real_key_or_network_client(self):
        with mock.patch.object(ingest.os, "getenv", side_effect=AssertionError("must not read key")), \
             mock.patch.object(ingest, "DeepSeekClient", side_effect=AssertionError("must not build network client")):
            ingest.run_ingestion(
                self.args(apply=False), client=FakeClient(),
                pages=["--- PAGE 1 ---\ntext"], now=self.now,
            )

    def test_manifest_is_generated(self):
        ingest.run_ingestion(self.args(apply=True), client=FakeClient(), pages=["--- PAGE 1 ---\ntext"], now=self.now)
        manifests = list((self.vault / "90-Local-Only/Processing-Cache/manifests").glob("*.json"))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdf_hash"], ingest.file_sha256(self.pdf))
        self.assertTrue(manifest["model_cache"].endswith(".json"))

    def test_markdown_backlinks_are_correct(self):
        plan = self.plan()
        source = next(x for x in plan.writes if x.category == "source")
        draft = next(x for x in plan.writes if x.category == "paper-draft")
        topic = next(x for x in plan.writes if x.category == "topic")
        self.assertIn(f"[[{draft.path.stem}]]", source.content)
        self.assertIn(f"[[{source.path.stem}]]", draft.content)
        self.assertIn(f"[[{source.path.stem}]]", topic.content)

    def test_transaction_rolls_back_first_new_file_when_second_commit_fails(self):
        first = self.vault / "20-Knowledge/Topics/一.md"
        second = self.vault / "20-Knowledge/Concepts/二.md"
        plan = ingest.WritePlan(
            source_id="pdf-test", vault=self.vault,
            writes=[
                ingest.PlannedWrite(first, "first\n", "create", "topic"),
                ingest.PlannedWrite(second, "second\n", "create", "concept"),
            ],
        )

        def fail_second(src, dst):
            if Path(dst) == second.resolve():
                raise OSError("injected second rename failure")
            ingest.os.replace(src, dst)

        with self.assertRaises(RuntimeError):
            ingest.execute_plan(plan, replace_func=fail_second, transaction_id="tx-new-failure")
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        journal = json.loads((
            self.vault / "90-Local-Only/Processing-Cache/transactions/tx-new-failure/journal.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "rolled_back")
        self.assertEqual(journal["committed_targets"], [str(first.resolve())])

    def test_transaction_restores_replaced_file_after_later_failure(self):
        first = self.vault / "20-Knowledge/Topics/已有.md"
        second = self.vault / "20-Knowledge/Concepts/新建.md"
        first.write_text("human old content\n", encoding="utf-8")
        plan = ingest.WritePlan(
            source_id="pdf-test", vault=self.vault,
            writes=[
                ingest.PlannedWrite(first, "generated replacement\n", "update", "topic"),
                ingest.PlannedWrite(second, "new content\n", "create", "concept"),
            ],
        )

        def fail_second(src, dst):
            if Path(dst) == second.resolve():
                raise OSError("injected second rename failure")
            ingest.os.replace(src, dst)

        with self.assertRaises(RuntimeError):
            ingest.execute_plan(plan, replace_func=fail_second, transaction_id="tx-restore-failure")
        self.assertEqual(first.read_text(encoding="utf-8"), "human old content\n")
        self.assertFalse(second.exists())

    def test_successful_transaction_journal_is_completed(self):
        target = self.vault / "20-Knowledge/Topics/完成.md"
        plan = ingest.WritePlan(
            source_id="pdf-test", vault=self.vault,
            writes=[ingest.PlannedWrite(target, "done\n", "create", "topic")],
        )
        journal_path = ingest.execute_plan(plan, transaction_id="tx-complete")
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "completed")
        self.assertEqual(target.read_text(encoding="utf-8"), "done\n")

    def test_title_drift_and_force_regenerate_reuse_generated_paths(self):
        first_result = valid_result()
        first = ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=ingest.file_sha256(self.pdf),
            result=first_result, model="fake-model", kind="paper", domain_focus="因果推断",
            max_concepts=3, inventory=ingest.scan_vault(self.vault), now=self.now,
        )
        ingest.execute_plan(first, transaction_id="tx-title-a")
        first_paths = {item.category: item.path for item in first.writes}

        second_result = valid_result()
        second_result["title"] = "完全不同的模型标题"
        second_result["topic"]["title"] = "标题也漂移的主题"
        second_result["concepts"][0]["title"] = "标题也漂移的概念"
        second = ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=ingest.file_sha256(self.pdf),
            result=second_result, model="fake-model", kind="paper", domain_focus="因果推断",
            max_concepts=3, inventory=ingest.scan_vault(self.vault), now=self.now,
        )
        second_paths = {item.category: item.path for item in second.writes}
        for category in ("source", "paper-draft", "topic", "concept"):
            self.assertEqual(second_paths[category], first_paths[category])
        self.assertEqual(len(ingest.find_generated_artifacts(self.vault, first.source_id, "source-index")), 1)

    def test_legacy_short_hash_source_and_draft_are_migrated_in_place(self):
        digest = ingest.file_sha256(self.pdf)
        short = digest[:12]
        source = self.vault / f"10-Sources/Papers/dragonnet-{short}.md"
        draft = self.vault / f"90-Local-Only/AI-Drafts/dragonnet-AI草稿-{short}.md"
        source.write_text(
            f'---\ntype: source\nstatus: processed\nsource_id: "pdf-{short}"\n---\n'
            f'# legacy\nSHA-256：`{digest}`\n\n## 人工批注\n保留我。\n', encoding="utf-8",
        )
        draft.write_text(
            '---\ntype: ai-draft\nstatus: ai-draft\n---\n# legacy draft\n', encoding="utf-8",
        )
        result = valid_result()
        result["title"] = "A title unrelated to the old filename"
        plan = ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=digest, result=result, model="fake-model",
            kind="paper", domain_focus="因果推断", max_concepts=3,
            inventory=ingest.scan_vault(self.vault), now=self.now,
        )
        self.assertEqual(next(x.path for x in plan.writes if x.category == "source"), source)
        self.assertEqual(next(x.path for x in plan.writes if x.category == "paper-draft"), draft)
        ingest.execute_plan(plan, transaction_id="tx-legacy")
        self.assertIn("保留我。", source.read_text(encoding="utf-8"))
        self.assertEqual(ingest.parse_frontmatter(source.read_text(encoding="utf-8"))["generated_from"], f"pdf-{digest}")

    def test_source_managed_block_refreshes_links_and_preserves_human_content(self):
        first = self.plan()
        ingest.execute_plan(first, transaction_id="tx-managed-a")
        source_path = next(item.path for item in first.writes if item.category == "source")
        text = source_path.read_text(encoding="utf-8")
        text = text.replace("---\n", "---\ncustom_owner: \"human\"\n", 1)
        text += "\n这是一条人工批注。\n"
        source_path.write_text(text, encoding="utf-8")

        changed = valid_result()
        changed["concepts"] = []
        second = ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=ingest.file_sha256(self.pdf),
            result=changed, model="fake-model", kind="paper", domain_focus="因果推断",
            max_concepts=3, inventory=ingest.scan_vault(self.vault), now=self.now,
        )
        source_write = next(item for item in second.writes if item.category == "source")
        old_concept = next(item.path.stem for item in first.writes if item.category == "concept")
        self.assertNotIn(f"[[{old_concept}]]", source_write.content)
        self.assertIn('custom_owner: "human"', source_write.content)
        self.assertIn("这是一条人工批注。", source_write.content)
        ingest.execute_plan(second, transaction_id="tx-managed-b")
        refreshed = source_path.read_text(encoding="utf-8")
        self.assertIn("这是一条人工批注。", refreshed)
        self.assertEqual(refreshed.count(ingest.MANAGED_START), 1)
        self.assertEqual(refreshed.count(ingest.MANAGED_END), 1)

    def test_existing_mainline_topic_is_reused_when_model_proposes_update(self):
        existing = self.write_note(
            "20-Knowledge/Topics/观测数据中的平均处理效应估计.md",
            "type: topic\nstatus: ai-draft\ndomain: 因果推断",
        )
        result = valid_result()
        result["topic"]["title"] = "使用神经网络估计平均处理效应"
        result["update_suggestions"] = [{
            "target_title": existing.stem, "proposed_content": "补充神经网络路线。",
            "evidence_pages": [1], "relation": "补充", "evidence_strength": "中",
            "action": "人工比较后合并。",
        }]
        plan = ingest.build_plan(
            self.vault, pdf_path=self.pdf, digest=ingest.file_sha256(self.pdf),
            result=result, model="fake-model", kind="paper", domain_focus="因果推断",
            max_concepts=3, inventory=ingest.scan_vault(self.vault), now=self.now,
        )
        self.assertFalse(any(item.category == "topic" for item in plan.writes))
        self.assertTrue(any(item.category == "suggestion" for item in plan.writes))
        source = next(item for item in plan.writes if item.category == "source")
        self.assertIn(f"[[{existing.stem}]]", source.content)


if __name__ == "__main__":
    unittest.main()

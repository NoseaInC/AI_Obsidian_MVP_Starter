from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.workspace_policy import (
    build_intent,
    validate_intent,
    WorkspacePolicyLoader,
)


class WorkspacePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        # 建立真实 vault 结构
        for d in [
            "10-Sources", "20-Knowledge/Courses", "20-Knowledge/Topics",
            "20-Knowledge/Concepts", "30-Learning", "40-Projects", "00-System/AI",
        ]:
            (self.vault / d).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_course_chapter_writes_to_courses(self):
        intent = build_intent(
            purpose="course_learning", note_type="course-chapter",
            title="第一章", course_subdir="概率论与数理统计",
        )
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("20-Knowledge/Courses", intent.targetPath)

    def test_topic_writes_to_topics(self):
        intent = build_intent(purpose="knowledge_consolidation", note_type="topic", title="工具变量方法")
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("20-Knowledge/Topics", intent.targetPath)

    def test_concept_writes_to_concepts(self):
        intent = build_intent(purpose="knowledge_consolidation", note_type="concept", title="样本均值")
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("20-Knowledge/Concepts", intent.targetPath)

    def test_project_note_writes_to_projects(self):
        intent = build_intent(purpose="project_progress", note_type="project-note", title="记忆模块进度")
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("40-Projects", intent.targetPath)

    def test_learning_log_writes_to_learning(self):
        intent = build_intent(purpose="learning_log", note_type="learning-log", title="今日学习记录")
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])
        self.assertIn("30-Learning", intent.targetPath)

    def test_note_type_directory_mismatch_rejected(self):
        intent = build_intent(purpose="knowledge_consolidation", note_type="concept", title="X")
        intent.targetPath = "20-Knowledge/Topics/X.md"  # 故意放错目录
        result = validate_intent(self.vault, intent.to_dict())
        self.assertFalse(result["ok"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("note_type_directory_mismatch", codes)

    def test_duplicate_suggests_update(self):
        self._write("20-Knowledge/Concepts/已有概念.md", "---\ntype: concept\nstatus: ai-draft\n---\n# 已有概念\n")
        intent = build_intent(purpose="knowledge_consolidation", note_type="concept", title="已有概念")
        result = validate_intent(self.vault, intent.to_dict())
        # 同标题 → 建议 update
        self.assertEqual(result["suggestedAction"], "update")
        self.assertEqual(result["suggestedTarget"], "20-Knowledge/Concepts/已有概念.md")

    def test_reviewed_core_target_rejected(self):
        self._write(
            "20-Knowledge/MOCs/测试MOC.md",
            "---\ntype: moc\nstatus: core\n---\n# 测试MOC\n",
        )
        intent = build_intent(purpose="knowledge_consolidation", note_type="topic", title="X")
        intent.targetPath = "20-Knowledge/MOCs/测试MOC.md"
        result = validate_intent(self.vault, intent.to_dict())
        self.assertFalse(result["ok"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("target_protected", codes)

    def test_missing_link_target_warns(self):
        intent = build_intent(
            purpose="knowledge_consolidation", note_type="concept", title="新概念",
            links={"related": ["不存在的笔记"]},
        )
        result = validate_intent(self.vault, intent.to_dict())
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("link_target_missing", codes)

    def test_authorization_scope_enforced(self):
        intent = build_intent(purpose="knowledge_consolidation", note_type="concept", title="范围外概念")
        result = validate_intent(
            self.vault, intent.to_dict(),
            authorization_paths={"20-Knowledge/Topics"},  # 授权只覆盖 Topics
        )
        codes = [i["code"] for i in result["issues"]]
        self.assertIn("target_outside_authorization", codes)

    def test_project_content_does_not_auto_promote(self):
        # project-note 类型不会自动进入通用知识目录
        intent = build_intent(purpose="project_progress", note_type="project-note", title="知序记忆模块")
        self.assertIn("40-Projects", intent.targetPath)
        result = validate_intent(self.vault, intent.to_dict())
        self.assertTrue(result["ok"], result["issues"])

    def test_policy_loader_reads_files(self):
        loader = WorkspacePolicyLoader(self.vault)
        self.assertEqual(loader.get("writing"), "")  # 空 vault 无 policy 文件
        profile = loader.profile_summary()
        self.assertIn("WriteIntent", profile)


class WriteIntentShapeTests(unittest.TestCase):
    def test_missing_field_rejected(self):
        import tempfile
        from pathlib import Path
        from agent.core.workspace_policy.validator import WriteIntent
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                WriteIntent.from_dict({"purpose": "x"})  # 缺 noteType


if __name__ == "__main__":
    unittest.main()

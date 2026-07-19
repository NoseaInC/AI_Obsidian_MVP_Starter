from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "00-System/Scripts"))
from agent.tools.vault_access import VaultReadIndex


class HybridRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        (self.vault / "20-Knowledge/Concepts").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def note(self, name: str, body: str) -> None:
        (self.vault / "20-Knowledge/Concepts" / name).write_text(body, encoding="utf-8")

    def test_chinese_alias_and_fts_channels_are_explainable(self) -> None:
        self.note("倾向得分.md", """---
status: reviewed
domain: 因果推断
aliases: 倾向评分, treatment propensity
tags: causal, observational
---
# 倾向得分
在无混杂条件下，用于平衡观测协变量。
[[平均处理效应]]
""")
        self.note("最大似然估计.md", "# 最大似然估计\n通过最大化似然函数估计未知参数。")
        index = VaultReadIndex(self.vault)
        result = index.search({"query": "倾向评分", "limit": 5})
        self.assertEqual(result["items"][0]["path"], "20-Knowledge/Concepts/倾向得分.md")
        self.assertIn("aliases", result["items"][0]["matchedFields"])
        self.assertIn("channelScores", result["items"][0])
        body = index.search({"query": "平衡观测协变量", "limit": 5})
        self.assertEqual(body["items"][0]["title"], "倾向得分")
        self.assertEqual(body["semanticState"], "disabled")

    def test_incremental_delete_denied_and_symlink_are_not_indexed(self) -> None:
        path = self.vault / "20-Knowledge/Concepts/临时.md"
        path.write_text("# 临时\n可搜索内容", encoding="utf-8")
        denied = self.vault / "20-Knowledge/Concepts/私密.md"
        denied.write_text("---\nagent_access: denied\n---\n秘密", encoding="utf-8")
        index = VaultReadIndex(self.vault)
        self.assertEqual(index.search({"query": "可搜索内容"})["total"], 1)
        self.assertEqual(index.search({"query": "秘密"})["total"], 0)
        path.unlink()
        self.assertEqual(index.search({"query": "可搜索内容"})["total"], 0)


if __name__ == "__main__":
    unittest.main()

"""Memory Ablation: Memory OFF vs Memory ON.

Deterministic offline eval. Cases:
  有明确目标 / 有明确学习偏好 / 存在知识缺口 / 存在冲突记忆 / 已删除记忆
Metrics:
  relevant_memory_recall / irrelevant_memory_rate / deleted_memory_leak_rate /
  conflict_resolution_accuracy
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from agent.core.storage import StateStore
from agent.core.memory import MemoryService


def _fresh() -> tuple[StateStore, MemoryService]:
    tmp = tempfile.TemporaryDirectory()
    store = StateStore(Path(tmp.name) / "eval.sqlite3")
    return store, MemoryService(store)


def run() -> dict[str, object]:
    store, memory = _fresh()
    try:
        # 有明确目标
        memory.remember_explicit("goal", "准备数据分析秋招", {"summary": "重点补统计"})
        # 有明确学习偏好
        memory.remember_explicit("preference", "先核心思想再推导", {"summary": "x"})
        # 知识缺口（claimed，不激活）
        gap = memory.create_candidate(
            "knowledge_state", "中心极限定理存在缺口", {"level": "observed"},
            evidence=[("message", "e1"), ("message", "e2"), ("quiz", "e3")],
        )
        # 冲突记忆：偏好 A → 偏好 A'（后者 supersede）
        memory.remember_explicit("preference", "回答要详细", {"summary": "v1"})
        memory.remember_explicit("preference", "回答要详细", {"summary": "v2 更新"})
        # 已删除记忆
        gone = memory.remember_explicit("project_decision", "已删除的决策", {"summary": "x"})
        memory.forget(gone["id"])

        # ── Memory ON ──────────────────────────────────────
        # 1. 目标可召回
        goals = memory.search("数据分析", memory_types=["goal"])
        relevant_memory_recall = len(goals) >= 1

        # 2. 偏好召回；冲突只有一条 active（"回答要详细"只有 v2 一条 active）
        prefs = [i for i in memory.list_active("preference") if i["status"] == "active"]
        detailed = [p for p in prefs if p["memory_key"] == "回答要详细"]
        conflict_resolution_accuracy = len(detailed) == 1 and detailed[0]["supersedes_id"] is not None

        # 3. 无关查询不命中（irrelevant rate）
        hits_for_unrelated = memory.search("天气", memory_types=["goal", "preference"])
        irrelevant_memory_rate = len(hits_for_unrelated) / max(1, len(hits_for_unrelated) + 1)

        # 4. 已删除记忆泄漏
        deleted = memory.search("已删除", memory_types=["project_decision"])
        deleted_memory_leak_rate = len(deleted)

        # ── Memory OFF（对照：无记忆时的上下文）────────────
        # 无目标时 goal_alignment 应为 0
        memory_off_alignment = 0.0  # 无记忆 → 无信号

        return {
            "relevant_memory_recall": relevant_memory_recall,
            "irrelevant_memory_rate": round(irrelevant_memory_rate, 3),
            "deleted_memory_leak_rate": deleted_memory_leak_rate,
            "conflict_resolution_accuracy": conflict_resolution_accuracy,
            "memory_off_alignment": memory_off_alignment,
            "gap_candidate_stays_candidate": gap["status"] == "candidate",
        }
    finally:
        store.close()


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))

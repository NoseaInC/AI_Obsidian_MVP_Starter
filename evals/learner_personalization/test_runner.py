"""Deterministic offline evals for learner personalization.

Run all:  python -m evals.learner_personalization.test_runner
"""
from __future__ import annotations

import json

from evals.learner_personalization import test_memory_ablation
from evals.learner_personalization import test_ranking_ablation
from evals.learner_personalization import test_confidence_maturity


def main() -> None:
    results = {
        "memory_ablation": test_memory_ablation.run(),
        "ranking_ablation": test_ranking_ablation.run(),
        "confidence_maturity": test_confidence_maturity.run(),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))

    # 汇总通过/失败
    checks: list[tuple[str, bool]] = []
    ma = results["memory_ablation"]
    checks.append(("memory: relevant recall", ma["relevant_memory_recall"]))
    checks.append(("memory: no deleted leak", ma["deleted_memory_leak_rate"] == 0))
    checks.append(("memory: conflict resolved", ma["conflict_resolution_accuracy"]))
    checks.append(("memory: candidate stays candidate", ma["gap_candidate_stays_candidate"]))

    ra = results["ranking_ablation"]
    if "error" not in ra:
        checks.append(("ranking: top-k hit", ra["top_k_hit"]))
        checks.append(("ranking: pairwise", ra["pairwise_ordering_accuracy"]))
        checks.append(("ranking: ndcg@k", ra["ndcg_at_k"]))
        checks.append(("ranking: time budget", ra["time_budget_exceeded"]))

    cm = results["confidence_maturity"]
    for key, value in cm["checks"].items():
        checks.append((f"confidence: {key}", value))

    print("\n=== SUMMARY ===")
    passed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        passed += 1 if ok else 0
    print(f"\n{passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()

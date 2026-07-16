from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any


def _canonical(value: str) -> str:
    return "".join(char.casefold() for char in value if char.isalnum())


def _bounded_score(value: Any, default: float) -> float:
    try: return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError): return default


def run(tools: Any, store: Any, payload: dict[str, Any], context: dict[str, Any], run_id: str, step_id: str, model_gateway: Any = None) -> dict[str, Any]:
    state = tools.call("get_learning_state", {}, run_id=run_id, step_id=step_id)["items"]
    existing = {_canonical(str(item.get("title", ""))) for item in state}
    existing.update(_canonical(item["title"]) for item in store.list_curriculum_candidates("active"))
    candidates: list[dict[str, Any]] = []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    model_result = model_gateway.generate_curriculum_candidates(state) if model_gateway else None
    title_map = {_canonical(str(item.get("title", ""))): str(item.get("title", "")) for item in state}
    for raw in (model_result or {}).get("candidates", [])[:3]:
        title = str(raw.get("title", "")).strip()[:120]
        canonical = _canonical(title)
        if not title or not canonical or canonical in existing:
            continue
        prerequisites = [str(value).strip()[:120] for value in raw.get("prerequisites", []) if str(value).strip()][:4]
        related = [str(value).strip()[:120] for value in raw.get("related_topics", []) if str(value).strip()][:5]
        evidence_titles: list[str] = []
        for value in prerequisites + related:
            matched = title_map.get(_canonical(value))
            if matched and matched not in evidence_titles: evidence_titles.append(matched)
        outcomes = [str(value).strip()[:200] for value in raw.get("learning_outcomes", []) if str(value).strip()][:5]
        confidence = _bounded_score(raw.get("confidence"), .5)
        grade = "A" if len(evidence_titles) >= 2 and confidence >= .88 else "B" if evidence_titles and confidence >= .65 else "C"
        verification_score = round(min(confidence, .9 if grade == "A" else .82 if grade == "B" else .55), 3)
        verification = {
            "schemaVersion": 1, "grade": grade, "score": verification_score,
            "claims": [{
                "claimId": f"claim-{index + 1}", "text": outcome, "importance": "core" if index == 0 else "supporting",
                "status": "consistent" if grade == "A" else "partial" if grade == "B" else "unsupported",
                "confidence": verification_score,
                "evidence": [{"type": "local_vault", "title": evidence} for evidence in evidence_titles[:3]],
                "missingConditions": [] if grade == "A" else ["学习时需结合来源核对定义条件"],
            } for index, outcome in enumerate(outcomes)],
            "conflicts": [], "verifiedAt": now,
            "notice": "来源有限，建议作为概念导读" if grade == "B" else "来源不足，已从每日新知识排除" if grade == "C" else "核心教学目标已由本地知识关系支持",
        }
        candidate = {
            "candidate_id": f"candidate-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}",
            "title": title, "canonical_title": canonical,
            "kind": str(raw.get("kind", "bridge")), "domain": str(raw.get("domain", "未分类"))[:80],
            "route": str(raw.get("route", "mainline")), "prerequisites": prerequisites,
            "related_topics": related, "why_now": "；".join(str(value).strip() for value in raw.get("why_now", []) if str(value).strip())[:500],
            "learning_outcomes": outcomes, "estimated_minutes": max(5, min(30, int(raw.get("estimated_minutes", 10)))),
            "difficulty": str(raw.get("difficulty", "medium")),
            "scores": {"mainline": _bounded_score(raw.get("mainline_score"), .7), "gap": _bounded_score(raw.get("gap_score"), .7), "reuse": .75, "behavior": .5},
            "confidence": confidence,
            "basis": {"type": "model_curriculum", "sourceBasis": [{"type": "local_knowledge", "title": value} for value in evidence_titles], "behaviorBasis": [], "model": (model_result or {}).get("_model")},
            "generated_by": "model-curriculum-v1", "generated_at": now,
            "status": "active" if grade in {"A", "B"} else "withheld",
            "model_profile_id": (model_result or {}).get("_profile_id"), "verification": verification, "schema_version": 1,
        }
        store.upsert_curriculum_candidate(candidate)
        candidates.append(candidate); existing.add(canonical)
    if candidates:
        return {"kind": "curriculum", "candidates": candidates, "generated": len(candidates), "model_used": True, "model": (model_result or {}).get("_model")}

    # Safe no-model fallback: it may support the normal learning route but is
    # deliberately not labeled as "每日新知识" in the TypeScript domain layer.
    for item in sorted(state, key=lambda row: (int(row.get("mastery", 0)), -int(row.get("importance", 3)))):
        for weak in item.get("weak_points", []) or []:
            title = str(weak).strip()
            canonical = _canonical(title)
            if not title or canonical in existing:
                continue
            candidate = {
                "candidate_id": f"candidate-{hashlib.sha256(canonical.encode()).hexdigest()[:16]}",
                "title": title, "canonical_title": canonical, "kind": "bridge",
                "domain": str(item.get("domain", "未分类")), "route": "mainline",
                "prerequisites": [item["title"]], "related_topics": [item["title"]],
                "why_now": f"它是“{item['title']}”当前记录的薄弱点。",
                "learning_outcomes": [f"能定义并辨析 {title}", f"能说明它与 {item['title']} 的关系"],
                "estimated_minutes": 12, "difficulty": "medium",
                "scores": {"mainline": 0.8, "gap": 0.9, "reuse": 0.7}, "confidence": .82,
                "basis": {"type": "reviewed_weak_point", "path": item.get("path", "")},
                "generated_by": "deterministic-curriculum-v1", "generated_at": now,
                "status": "active", "model_profile_id": None,
                "verification": {"schemaVersion": 1, "grade": "C", "score": .5, "claims": [], "verifiedAt": now, "notice": "未经过模型候选生成，不能进入每日新知识"},
                "schema_version": 1,
            }
            store.upsert_curriculum_candidate(candidate); candidates.append(candidate); existing.add(canonical)
            if len(candidates) >= 8:
                break
        if len(candidates) >= 8:
            break
    return {"kind": "curriculum", "candidates": candidates, "generated": len(candidates), "model_used": False}

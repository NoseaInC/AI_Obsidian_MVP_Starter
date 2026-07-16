from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def canonical_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())


def _known_titles(vault: Path, store: Any) -> set[str]:
    known: set[str] = set()
    for path in vault.rglob("*.md"):
        if any(part in {".obsidian", ".git"} for part in path.parts):
            continue
        known.add(canonical_title(path.stem))
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:5000]
        except OSError:
            continue
        for value in re.findall(r"(?mi)^\s*(?:title|alias|aliases)\s*:\s*['\"]?([^\n'\"]+)", head):
            for item in re.split(r"[,，\[\]]", value):
                if item.strip():
                    known.add(canonical_title(item))
    with store.lock:
        for row in store.connection.execute("SELECT title FROM agent_artifacts"):
            known.add(canonical_title(row["title"]))
        for row in store.connection.execute("SELECT title FROM lesson_versions"):
            known.add(canonical_title(row["title"]))
    return {value for value in known if value}


def _direction_id(horizon: str, title: str) -> str:
    return "direction-" + hashlib.sha256(f"{horizon}:{canonical_title(title)}".encode()).hexdigest()[:20]


def predict_directions(vault: Path, store: Any, limit: int = 5) -> list[dict[str, Any]]:
    """Produce explainable candidates from durable local evidence.

    This is intentionally deterministic. A model may later enrich a lesson, but
    it is not the authority for direction ranking or novelty classification.
    """

    signals = store.list_conversation_signals(limit=200)
    known = _known_titles(vault, store)
    topic_rows: dict[str, dict[str, Any]] = {}
    for signal in signals:
        topic = str(signal.get("topic") or "").strip()
        if not topic or topic == "当前主题":
            continue
        bucket = topic_rows.setdefault(topic, {"recurrence": 0, "confidence": 0.0, "types": set(), "messages": []})
        bucket["recurrence"] += int(signal.get("recurrence", 1))
        bucket["confidence"] = max(bucket["confidence"], float(signal.get("confidence", 0)))
        bucket["types"].add(str(signal.get("signalType") or "topic"))
        bucket["messages"].extend(signal.get("messageIds", []))
    ranked = sorted(topic_rows.items(), key=lambda item: (-item[1]["recurrence"], -item[1]["confidence"], item[0]))
    if not ranked:
        return []

    candidates: list[dict[str, Any]] = []
    templates = [
        ("near", "{topic}的适用边界与常见误区", "bridge", "mainline", 12, "medium"),
        ("route", "从{topic}到相邻方法的比较", "route", "mainline", 18, "medium"),
        ("exploration", "{topic}的可迁移应用", "exploration", "branch", 15, "medium"),
    ]
    candidate_count = max(3, min(5, len(ranked) + 2))
    for index in range(candidate_count):
        topic, evidence = ranked[min(index, len(ranked) - 1)]
        horizon, template, category, route, minutes, difficulty = templates[index % len(templates)]
        title = template.format(topic=topic)
        canonical = canonical_title(title)
        types = evidence["types"]
        reasons = [f"最近对话中“{topic}”出现 {evidence['recurrence']} 次"]
        if "confusion" in types:
            reasons.append("用户明确表达过困惑，需要先补边界与辨析")
        if "missing_prerequisite" in types:
            reasons.append("对话中出现前置知识缺口")
        if "interest" in types:
            reasons.append("用户明确表达过继续学习意图")
        priority = "高优先级" if evidence["recurrence"] >= 3 or "confusion" in types else "中优先级"
        confidence_label = "高置信" if evidence["recurrence"] >= 2 else "来源有限"
        is_new = canonical not in known
        novelty = "Vault、AI 草稿和历史 Lesson 中未发现同名等价条目" if is_new else "已有相关内容，应作为深化而非每日新知识"
        candidates.append({
            "id": _direction_id(horizon, title), "horizon": horizon, "title": title,
            "category": category, "why": reasons, "connections": [topic],
            "noveltyBasis": novelty, "isNewKnowledge": is_new,
            "prerequisites": [topic] if category != "bridge" else [],
            "estimatedMinutes": minutes, "difficulty": difficulty, "route": route,
            "sourceQuality": "本地对话证据", "priority": priority,
            "confidenceLabel": confidence_label,
            "confidence": min(.95, .5 + .08 * evidence["recurrence"] + (.12 if "confusion" in types else 0)),
            "evidence": list(dict.fromkeys(evidence["messages"]))[:10],
            "actions": ["tomorrow", "weekend", "not_now"],
        })
    return candidates[: max(3, min(5, limit))]

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))
import ingest_pdf

from agent.core import learning
from agent.core.storage import StateStore

ALLOWED_ACTIONS = {"later", "tomorrow", "weekend", "favorite", "not_interested", "mastered", "too_easy", "too_hard", "off_route", "completed", "exited"}
WIKI_LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]")


def _id(kind: str, value: str) -> str:
    return f"rec-{hashlib.sha256(f'{kind}:{value}'.encode()).hexdigest()[:16]}"


def _references(path: Path, text: str, limit: int = 5) -> list[dict[str, str]]:
    seen: set[str] = set(); result: list[dict[str, str]] = []
    for title in WIKI_LINK.findall(text):
        title = title.strip()
        if not title or title == path.stem or title in seen: continue
        seen.add(title); result.append({"title": title, "path": ""})
        if len(result) >= limit: break
    return result


def _feedback_map(store: StateStore) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in store.recommendation_feedback():
        item = dict(row); item["details"] = json.loads(item.pop("details_json") or "{}")
        result.setdefault(str(row["recommendation_id"]), []).append(item)
    return result


def _hidden_today(events: list[dict[str, Any]], today: date) -> bool:
    for event in events:
        action = event["action"]; event_date = str(event["created_at"])[:10]
        if action in {"later", "tomorrow", "weekend"} and event_date == today.isoformat(): return True
        if action in {"not_interested", "off_route"}:
            try:
                if date.fromisoformat(event_date) >= today - timedelta(days=7): return True
            except ValueError: pass
    return False


def _knowledge_recommendation(item: learning.KnowledgeItem, today: date, events: list[dict[str, Any]]) -> dict[str, Any]:
    overdue = max(0, (today - item.next_review).days) if item.next_review and item.next_review <= today else 0
    kind = "review" if item.next_review and item.next_review <= today else "learn"
    due_score = min(30.0, 18.0 + overdue * 3) if kind == "review" else 0.0
    route_score = 25.0 if learning.branch(item) == "mainline" else 15.0
    prerequisite_score = 20.0 if item.mastery <= 1 else 10.0 if item.mastery == 2 else 4.0
    mastery_score = (4 - item.mastery) / 4 * 15 + min(4, len(item.weak_points) * 2)
    interest_score = min(10.0, item.importance * 2)
    if any(event["action"] == "favorite" for event in events): interest_score += 5
    if any(event["action"] == "too_hard" for event in events): prerequisite_score += 5
    score = round(min(100, due_score + route_score + prerequisite_score + mastery_score + interest_score), 2)
    text = item.path.read_text(encoding="utf-8", errors="replace")
    meta = ingest_pdf.parse_frontmatter(text)
    prereq_values = meta.get("prerequisites", [])
    if not isinstance(prereq_values, list): prereq_values = [prereq_values] if prereq_values else []
    prerequisites = [{"title": str(value).strip("[]"), "path": ""} for value in prereq_values if str(value).strip()]
    related = _references(item.path, text)
    weak = list(item.weak_points)
    micro_titles = (weak + [ref["title"] for ref in prerequisites + related])[:5]
    if any(event["action"] == "too_hard" for event in events) and not micro_titles:
        micro_titles = [f"{item.title}的基础定义"]
    details = [
        f"当前路线相关性：{'统计/机器学习主线' if learning.branch(item) == 'mainline' else 'LLM/Agent 支线'}",
        f"掌握度 {item.mastery}/4，重要度 {item.importance}/5",
    ]
    if overdue: details.insert(0, f"已到期 {overdue} 天")
    if weak: details.append(f"薄弱点：{'、'.join(weak[:3])}")
    reason = f"{'已到复习时间' if kind == 'review' else '适合作为当前路线的下一步'}，建议用短时段巩固定义与适用条件。"
    return {
        "id": _id(kind, str(item.path)), "title": item.title, "kind": kind,
        "estimatedMinutes": 5 if kind == "review" else 12, "score": score,
        "reason": reason, "reasonDetails": details, "prerequisites": prerequisites,
        "relatedNotes": related, "microConcepts": [{"id": _id("micro", title), "title": title, "explanation": f"它与“{item.title}”的理解和应用直接相关。", "estimatedMinutes": 4, "candidate": not any(ref["title"] == title for ref in related)} for title in micro_titles],
        "quizPreview": {"question": learning.quiz(item)[0], "answerHint": "先给出定义，再说明成立条件。"},
        "sourcePath": str(item.path), "domain": item.domain or "未分类",
        "route": "mainline" if learning.branch(item) == "mainline" else "branch",
        "dueState": "overdue" if overdue else "due" if kind == "review" else "upcoming",
        "mastery": item.mastery, "actions": ["later", "tomorrow", "weekend", "favorite", "not_interested", "start"],
        "favorite": any(event["action"] == "favorite" for event in events),
    }


def _source_recommendation(vault: Path, bundle: dict[str, Any]) -> dict[str, Any]:
    prepared_id = str(bundle["prepared_id"])
    request_path = vault / "90-Local-Only/Prepared-Bundles" / prepared_id / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8")) if request_path.is_file() else {}
    title = Path(str(request.get("pdf_path", "待确认资料"))).stem or "待确认资料"
    return {
        "id": _id("source", prepared_id), "title": title, "kind": "source", "estimatedMinutes": 10,
        "score": 52.0, "reason": "资料已完成本地提取和结构化分析，等待你检查 Change Set。",
        "reasonDetails": ["尚未写入正式知识", "Apply 不会再次调用模型"], "prerequisites": [], "relatedNotes": [],
        "microConcepts": [], "sourcePath": "", "domain": str(request.get("domain_focus") or "待整理资料"),
        "route": "mainline", "actions": ["later", "review_change_set"], "preparedId": prepared_id,
    }


def build(vault: Path, store: StateStore, prepared: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today(); feedback = _feedback_map(store); result: list[dict[str, Any]] = []
    suppressed_domains = {
        str(event.get("details", {}).get("domain"))
        for events in feedback.values() for event in events
        if event["action"] in {"not_interested", "off_route"} and event.get("details", {}).get("domain")
    }
    for item in learning.scan_reviewed(vault):
        kind = "review" if item.next_review and item.next_review <= today else "learn"
        rec_id = _id(kind, str(item.path)); events = feedback.get(rec_id, [])
        if _hidden_today(events, today): continue
        rec = _knowledge_recommendation(item, today, events)
        if rec["domain"] in suppressed_domains: rec["score"] = max(0, rec["score"] - 15); rec["reasonDetails"].append("同领域近期收到负反馈，已降低权重")
        result.append(rec)
    for bundle in prepared:
        if bundle.get("state") != "prepared": continue
        rec = _source_recommendation(vault, bundle)
        if not _hidden_today(feedback.get(rec["id"], []), today): result.append(rec)
    return sorted(result, key=lambda item: (-float(item["score"]), item["estimatedMinutes"], item["title"]))


def record_action(store: StateStore, recommendation_id: str, action: str, details: dict[str, Any] | None = None) -> None:
    if action not in ALLOWED_ACTIONS: raise RuntimeError(f"Unsupported recommendation action: {action}")
    store.add_recommendation_feedback(recommendation_id, action, details or {})

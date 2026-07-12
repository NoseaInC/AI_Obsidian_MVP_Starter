from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import sys
ROOT = Path(__file__).resolve().parents[2]; SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))
import ingest_pdf
import review
from agent.core.storage import StateStore


INTERVALS = {0: 1, 1: 2, 2: 4, 3: 8, 4: 21}


@dataclass(frozen=True)
class KnowledgeItem:
    path: Path; title: str; status: str; domain: str; mastery: int; importance: int
    last_review: date | None; next_review: date | None; weak_points: tuple[str, ...]


def _date(value: Any) -> date | None:
    if not value: return None
    try: return date.fromisoformat(str(value))
    except ValueError: return None


def scan_reviewed(vault: Path) -> list[KnowledgeItem]:
    items: list[KnowledgeItem] = []
    for folder in (vault / "20-Knowledge/Topics", vault / "20-Knowledge/Concepts"):
        if not folder.exists(): continue
        for path in folder.glob("*.md"):
            meta = ingest_pdf.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if str(meta.get("status", "")) not in ingest_pdf.READ_ONLY_STATUSES: continue
            weak = meta.get("weak_points", []); weak = weak if isinstance(weak, list) else ([str(weak)] if weak else [])
            try: mastery = int(meta.get("mastery", 0)); importance = int(meta.get("importance", 3))
            except (TypeError, ValueError): mastery, importance = 0, 3
            items.append(KnowledgeItem(path, path.stem, str(meta["status"]), str(meta.get("domain", "")), max(0, min(4, mastery)), max(1, min(5, importance)), _date(meta.get("last_review")), _date(meta.get("next_review")), tuple(map(str, weak))))
    return items


def branch(item: KnowledgeItem) -> str:
    value = item.domain.casefold()
    return "llm-agent" if "llm" in value or "agent" in value or "大模型" in value else "mainline"


def due_reviews(items: list[KnowledgeItem], today: date, limit: int = 3) -> list[KnowledgeItem]:
    due = [item for item in items if item.next_review is not None and item.next_review <= today]
    return sorted(due, key=lambda item: (-(today - item.next_review).days, item.mastery, -item.importance, item.title))[:limit]


def future_reviews(items: list[KnowledgeItem], today: date, days: int = 2) -> list[KnowledgeItem]:
    end = today + timedelta(days=days)
    return sorted([item for item in items if item.next_review and today < item.next_review <= end], key=lambda item: (item.next_review, -item.importance, item.title))


def weighted_learning(items: list[KnowledgeItem], count: int = 10) -> list[KnowledgeItem]:
    candidates = sorted(items, key=lambda item: (item.mastery, -item.importance, item.last_review or date.min, item.title))
    main = [item for item in candidates if branch(item) == "mainline"]
    side = [item for item in candidates if branch(item) == "llm-agent"]
    main_quota = round(count * 0.7); selected = main[:main_quota] + side[:count - main_quota]
    if len(selected) < count:
        selected_ids = {item.path for item in selected}; selected.extend(item for item in candidates if item.path not in selected_ids)
    return selected[:count]


def daily_plan(vault: Path, today: date | None = None) -> dict[str, Any]:
    today = today or date.today(); items = scan_reviewed(vault)
    due = due_reviews(items, today, 3)
    new = weighted_learning([item for item in items if item not in due and item.mastery <= 1], 1)
    return {
        "date": today.isoformat(), "review": [serialize(item) for item in due],
        "new_learning": [serialize(item) for item in new],
        "next_two_days": [serialize(item) for item in future_reviews(items, today)],
    }


def serialize(item: KnowledgeItem) -> dict[str, Any]:
    return {"title": item.title, "path": str(item.path), "domain": item.domain, "mastery": item.mastery, "importance": item.importance, "next_review": item.next_review.isoformat() if item.next_review else None, "weak_points": list(item.weak_points)}


def quiz(item: KnowledgeItem, weekend: bool = False) -> list[str]:
    questions = [f"请严格定义“{item.title}”，并说明成立条件。", f"给出“{item.title}”最常见的混淆点，并解释区别。"]
    if weekend: questions.append(f"构造一个需要迁移应用“{item.title}”的例子，并完整讲解。")
    else: questions.append(f"用一个具体例子说明“{item.title}”如何使用。")
    return questions


def suggest_mastery(current: int, correctness: float, critical_error: bool = False) -> int:
    if not 0 <= correctness <= 1: raise ValueError("correctness must be in [0, 1]")
    if critical_error: return max(0, current - 1)
    target = 4 if correctness >= .9 else 3 if correctness >= .75 else 2 if correctness >= .55 else 1 if correctness >= .3 else 0
    return max(0, min(4, min(current + 1, target)))


def confirm_mastery(vault: Path, store: StateStore, path: Path, mastery: int, weak_points: list[str], today: date | None = None, *, _locked: bool = False) -> Path:
    if mastery not in INTERVALS: raise RuntimeError("mastery 必须为 0–4")
    vault, path, today = vault.resolve(), path.resolve(), today or date.today()
    if not _locked:
        with ingest_pdf.vault_write_lock(vault):
            return confirm_mastery(vault, store, path, mastery, weak_points, today, _locked=True)
    if not path.is_relative_to(vault / "20-Knowledge"): raise RuntimeError("只能更新知识目录中的学习状态")
    before = path.read_text(encoding="utf-8"); meta = ingest_pdf.parse_frontmatter(before)
    if str(meta.get("status", "")) not in ingest_pdf.READ_ONLY_STATUSES: raise RuntimeError("只有 reviewed/core 可进入学习调度")
    old = int(meta.get("mastery", 0) or 0); next_review = today + timedelta(days=INTERVALS[mastery])
    after = review.update_frontmatter(before, {"mastery": mastery, "last_review": today.isoformat(), "next_review": next_review.isoformat(), "weak_points": weak_points})
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    audit = vault / "90-Local-Only/Learning-Audit" / f"{stamp}.json"
    plan = ingest_pdf.WritePlan(source_id=f"learning:{path.stem}", vault=vault, writes=[
        ingest_pdf.PlannedWrite(path, after, "update", "learning-state"),
        ingest_pdf.PlannedWrite(audit, __import__("json").dumps({"path": str(path.relative_to(vault)), "old_mastery": old, "new_mastery": mastery, "confirmed": True, "next_review": next_review.isoformat()}, ensure_ascii=False, indent=2) + "\n", "create", "learning-audit"),
    ])
    ingest_pdf.execute_plan(plan, transaction_id=f"learning-{stamp}", acquire_lock=False)
    with store.lock:
        store.connection.execute("INSERT INTO mastery_history(artifact_id, old_mastery, new_mastery, confirmed, created_at) VALUES (?, ?, ?, 1, ?)", (str(meta.get("artifact_id", path.stem)), old, mastery, datetime.now().astimezone().isoformat(timespec="seconds")))
        store.connection.commit()
    return audit


def reschedule_unfinished(tasks: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for task in tasks:
        item = dict(task); key = str(item.get("artifact_id") or item.get("title"))
        if item.get("state") in {"unfinished", "queued"} and _date(item.get("due_date")) and _date(item.get("due_date")) < today:
            item["due_date"] = today.isoformat(); item["state"] = "rescheduled"
        result[key] = item
    return list(result.values())


def create_weekly_plan(vault: Path, week_start: date) -> Path:
    vault = vault.resolve(); selected = weighted_learning(scan_reviewed(vault), 10)
    path = vault / "30-Learning/Weekly" / f"{week_start.isoformat()}-学习计划.md"
    if path.exists(): raise RuntimeError("本周计划已存在")
    rows = "\n".join(f"- [ ] [[{item.title}]] · {item.domain} · mastery {item.mastery}" for item in selected) or "- 暂无 reviewed/core 知识"
    content = ingest_pdf._frontmatter([
        ("type", "weekly-plan"), ("status", "proposed"), ("week_start", week_start.isoformat()),
        ("generated_by", "obsidian-learning-agent"), ("mainline_ratio", 70), ("side_ratio", 30),
    ]) + f"\n# {week_start.isoformat()} 学习计划\n\n{rows}\n\n## 人工调整区\n"
    plan = ingest_pdf.WritePlan(source_id=f"weekly:{week_start}", vault=vault, writes=[ingest_pdf.PlannedWrite(path, content, "create", "weekly-plan")])
    ingest_pdf.execute_plan(plan, transaction_id=f"weekly-{week_start}"); return path


def confirm_weekly_plan(vault: Path, path: Path) -> None:
    vault, path = vault.resolve(), path.resolve()
    if not path.is_relative_to(vault / "30-Learning/Weekly"): raise RuntimeError("计划路径无效")
    before = path.read_text(encoding="utf-8"); meta = ingest_pdf.parse_frontmatter(before)
    if str(meta.get("status", "")) != "proposed": raise RuntimeError("只有 proposed 计划可以确认")
    after = review.update_frontmatter(before, {"status": "confirmed", "confirmed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    plan = ingest_pdf.WritePlan(source_id=f"weekly:{path.stem}", vault=vault, writes=[ingest_pdf.PlannedWrite(path, after, "update", "weekly-confirm")])
    ingest_pdf.execute_plan(plan, transaction_id=f"weekly-confirm-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}")

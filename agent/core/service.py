from __future__ import annotations

import json
import sys
import argparse
import hashlib
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

import prepared_pdf
import review
from agent.core import learning
from agent.core.storage import StateStore


class AgentService:
    """Restricted business API; deliberately has no generic write-file method."""

    def __init__(self, vault: Path, store: StateStore | None = None) -> None:
        self.vault = vault.resolve()
        self.store = store or StateStore(self.vault / "90-Local-Only/Agent/agent.sqlite3")
        self.store.recover_interrupted()
        self.log_path = self.vault / "90-Local-Only/Agent/logs/events.jsonl"
        self.sync_indexes()

    def log(self, event: str, details: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, "details": details}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.store.audit(event, str(details.get("job_id") or details.get("artifact_id") or details.get("prepared_id") or ""), details)

    def health(self) -> dict[str, Any]:
        self.sync_indexes()
        return {
            "ok": True,
            "status": "ok",
            "service": "obsidian-learning-agent",
            "protocol_version": 1,
            "vault": str(self.vault),
            "jobs": len(self.store.list_jobs()),
        }

    def list_jobs(self) -> list[dict[str, Any]]: return self.store.list_jobs()
    def list_prepared(self) -> list[dict[str, str]]:
        rows = prepared_pdf.list_prepared(self.vault); now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            for item in rows:
                self.store.connection.execute("INSERT INTO change_sets(change_set_id, prepared_id, state, bundle_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(change_set_id) DO UPDATE SET state=excluded.state, bundle_hash=excluded.bundle_hash, updated_at=excluded.updated_at", (item["prepared_id"], item["prepared_id"], item["state"], item["bundle_hash"], now, now))
            self.store.connection.commit()
        return rows
    def inspect_prepared(self, prepared_id: str) -> str: return prepared_pdf.inspect_bundle(self.vault, prepared_id)
    def apply_prepared(self, prepared_id: str) -> str:
        result = str(prepared_pdf.apply_prepared(self.vault, prepared_id)); self.log("prepared.applied", {"prepared_id": prepared_id}); return result
    def list_reviews(self) -> list[dict[str, Any]]:
        self.sync_artifacts(); return [{**item, "path": str(item["path"])} for item in review.scan_artifacts(self.vault)]
    def show_review(self, artifact_id: str) -> str: return review.review_packet(self.vault, artifact_id)
    def diff_review(self, artifact_id: str) -> str: return review.artifact_diff(self.vault, artifact_id)
    def transition_review(self, artifact_id: str, action: str, reason: str = "") -> str:
        result = str(review.transition(self.vault, artifact_id, action, reason)); self.log("review.transition", {"artifact_id": artifact_id, "action": action}); return result
    def today_learning(self) -> dict[str, Any]: return learning.daily_plan(self.vault)
    def suggest_mastery(self, current: int, correctness: float, critical_error: bool = False) -> int:
        return learning.suggest_mastery(current, correctness, critical_error)
    def confirm_mastery(self, path: str, mastery: int, weak_points: list[str]) -> str:
        target = (self.vault / path).resolve()
        result = str(learning.confirm_mastery(self.vault, self.store, target, mastery, weak_points)); self.log("learning.mastery-confirmed", {"path": path, "mastery": mastery}); return result

    def enqueue(self, kind: str, payload: dict[str, Any]) -> str:
        allowed = {"prepare-pdf", "expand-idea", "learning-plan", "quiz"}
        if kind not in allowed: raise RuntimeError(f"Unsupported job kind: {kind}")
        job_id = self.store.create_job(kind, payload); self.log("job.queued", {"job_id": job_id, "kind": kind}); return job_id

    def run_job(self, job_id: str, handlers: dict[str, Callable[[dict[str, Any]], Any]]) -> None:
        job = self.store.get_job(job_id)
        if not job or job["state"] != "queued": raise RuntimeError("Job is not queued")
        payload = json.loads(job["payload_json"]); kind = str(job["kind"])
        if kind not in handlers: raise RuntimeError(f"No handler for {kind}")
        self.store.update_job(job_id, "running")
        try:
            result = handlers[kind](payload)
            self.store.update_job(job_id, "completed", result=result)
        except Exception as exc:
            self.store.update_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}")
            raise

    def default_handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        def prepare_pdf_job(payload: dict[str, Any]) -> dict[str, str]:
            args = argparse.Namespace(
                pdf=str(payload["pdf"]), vault=str(self.vault), kind=str(payload.get("kind", "paper")),
                domain_focus=str(payload.get("domain_focus", "")), model=str(payload.get("model", "deepseek-v4-pro")),
                base_url=str(payload.get("base_url", "https://api.deepseek.com")), max_concepts=int(payload.get("max_concepts", 3)),
            )
            return {"bundle": str(prepared_pdf.prepare_bundle(args))}
        def learning_plan_job(payload: dict[str, Any]) -> dict[str, str]:
            today = date.today(); start = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
            return {"path": str(learning.create_weekly_plan(self.vault, start))}
        def quiz_job(payload: dict[str, Any]) -> dict[str, Any]:
            plan = learning.daily_plan(self.vault); candidates = plan["review"] or plan["new_learning"]
            if not candidates: return {"questions": [], "reason": "no reviewed/core candidate"}
            path = Path(candidates[0]["path"]); item = next(item for item in learning.scan_reviewed(self.vault) if item.path == path)
            return {"artifact": item.title, "questions": learning.quiz(item, weekend=date.today().weekday() >= 5)}
        def expand_idea_job(payload: dict[str, Any]) -> dict[str, Any]:
            relative = Path(str(payload["path"])); path = (self.vault / relative).resolve()
            if not path.is_relative_to(self.vault / "01-Inbox/Ideas") or not path.is_file(): raise RuntimeError("idea path is outside 01-Inbox/Ideas")
            return {"path": str(relative), "state": "awaiting-model-prepared-expansion", "message": "Idea retained; no unrestricted write was performed."}
        return {"prepare-pdf": prepare_pdf_job, "learning-plan": learning_plan_job, "quiz": quiz_job, "expand-idea": expand_idea_job}

    def process_next(self, handlers: dict[str, Callable[[dict[str, Any]], Any]] | None = None) -> str | None:
        job = self.store.next_queued()
        if not job: return None
        try: self.run_job(str(job["job_id"]), handlers or self.default_handlers())
        except Exception as exc: self.log("job.failed", {"job_id": job["job_id"], "error": f"{type(exc).__name__}: {exc}"})
        return str(job["job_id"])

    def sync_artifacts(self) -> None:
        items = review.scan_artifacts(self.vault); now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            self.store.connection.execute("DELETE FROM artifacts")
            for item in items:
                path = Path(item["path"]); digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.store.connection.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)", (item["artifact_id"], item["artifact_role"], str(path.relative_to(self.vault)), item["status"], item["review_state"], digest, now))
            self.store.connection.commit()

    def sync_transactions(self) -> None:
        root = self.vault / "90-Local-Only/Processing-Cache/transactions"; now = datetime.now().astimezone().isoformat(timespec="seconds")
        if not root.exists(): return
        with self.store.lock:
            for journal in root.glob("*/journal.json"):
                try: data = json.loads(journal.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError): continue
                transaction_id = str(data.get("transaction_id", journal.parent.name)); state = str(data.get("status", "unknown"))
                self.store.connection.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?) ON CONFLICT(transaction_id) DO UPDATE SET state=excluded.state, journal_path=excluded.journal_path, updated_at=excluded.updated_at", (transaction_id, state, str(journal.relative_to(self.vault)), now, now))
            self.store.connection.commit()

    def sync_indexes(self) -> None:
        self.sync_artifacts(); self.list_prepared(); self.sync_transactions()

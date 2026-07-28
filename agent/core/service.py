from __future__ import annotations

import json
import sys
import argparse
import hashlib
import os
import re
import uuid
from datetime import date, timedelta
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "00-System/Scripts"
if str(SCRIPTS) not in sys.path: sys.path.insert(0, str(SCRIPTS))

import prepared_pdf
import ingest_pdf
import review
from agent.core import learning
from agent.core import recommendations
from agent.core import learning_directions
from agent.core.models import KeyStore, ModelProfileService
from agent.core.pi_model_proxy import PiModelProxy
from agent.core.task_authorization import TaskAuthorizationService
from agent.core.reversible_transaction import ReversibleTransactionService
from agent.core.developer_workspace import DeveloperWorkspace
from agent.core.redaction import redact
from agent.core.storage import StateStore
from agent.core.intake import IntakeService
from agent.core.web_research import WebResearchService
from agent.core.vault_autonomy import VaultAutonomyService, render_managed_block
from agent.core import study_workspace
from agent.errors import BrainError
from agent.tools import build_tool_registry
from agent.tools.change_set import ChangeSetTools
from agent.core.explicit_workflow_service import ExplicitWorkflowService
from agent.core.dashboard import DashboardAggregator, TZ, _today, _date_str, _now_iso
from agent.materials import PreparedPdfService
from agent.tools.vault_access import read_model_visible_note, safe_read_note


class AgentService:
    """Restricted business API; deliberately has no generic write-file method."""

    def __init__(self, vault: Path, store: StateStore | None = None, runtime_id: str = "", key_store: KeyStore | None = None) -> None:
        self.vault = vault.resolve()
        self.runtime_id = runtime_id
        self.store = store or StateStore(self.vault / "90-Local-Only/Agent/agent.sqlite3")
        self.intake = IntakeService(self.vault, self.store)
        self.models = ModelProfileService(self.store, key_store)
        self.model_proxy = PiModelProxy(self.models, self.store)
        self.web = WebResearchService(self.vault, self.store)
        self.autonomy = VaultAutonomyService(self.vault, self.store)
        self.tools = build_tool_registry(
            self.vault,
            self.store,
            intake=self.intake,
            allow_network=False,
        )
        self.brain_change_sets = ChangeSetTools(self.vault, self.store)
        self.explicit = ExplicitWorkflowService(self)
        self.task_authorizations = TaskAuthorizationService(
            self.vault, self.store, self.autonomy.classify,
        )
        self.reversible_transactions = ReversibleTransactionService(
            self.vault, self.store, self.task_authorizations,
        )
        self.developer_workspace = DeveloperWorkspace(self.vault, self.store)
        self.dashboard_agg = DashboardAggregator(self.vault, self.store)
        self.materials = PreparedPdfService(self.vault)
        self.store.recover_interrupted()
        self.log_path = self.vault / "90-Local-Only/Agent/logs/events.jsonl"
        self.sync_indexes()

    def log(self, event: str, details: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        safe_details = redact(details)
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, "details": safe_details}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.store.audit(event, str(details.get("job_id") or details.get("artifact_id") or details.get("prepared_id") or details.get("run_id") or ""), safe_details)

    def health(self) -> dict[str, Any]:
        self.sync_indexes()
        return {
            "ok": True,
            "status": "ok",
            "service": "obsidian-learning-agent",
            "protocol_version": 1,
            "vault": str(self.vault),
            "jobs": len(self.store.list_jobs()),
            "pid": os.getpid(),
            "runtime_id": self.runtime_id,
            "workflow_version": self.explicit.workflows.version,
            "assistant_runtime_version": "pi-agent-runtime/1",
            "model_proxy_version": self.model_proxy.version,
            "schema_version": self.store.schema_version(),
        }

    def submit_workflow(self, body: dict[str, Any], idempotency_key: str = "", *, mode: str | None = None) -> dict[str, Any]:
        # Explicit (legacy Brain) workflow path. See ExplicitWorkflowService.
        return self.explicit.submit_workflow(body, idempotency_key, mode=mode)

    def create_conversation(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.create_conversation(str(body.get("title") or "新会话"))

    def list_conversations(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_conversations(limit, offset)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        conversation = self.intake.get_conversation(conversation_id)
        return {
            **conversation,
            "latestTaskThread": self.intake.latest_task_thread(conversation_id),
            "latestArtifactGroup": self.intake.latest_artifact_group(conversation_id),
            "summary": self.store.latest_conversation_summary(conversation_id),
            "knowledgeSignals": self.store.list_conversation_signals(conversation_id, 50),
            "focus": self.store.get_conversation_focus(conversation_id),
        }

    def update_conversation_preferences(self, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.update_conversation_preferences(
            conversation_id,
            personalization_enabled=body.get("personalization_enabled") if "personalization_enabled" in body else None,
            retention_policy=str(body["retention_policy"]) if body.get("retention_policy") is not None else None,
        )

    def rename_conversation(self, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.rename_conversation(conversation_id, str(body.get("title") or ""))

    def pin_conversation(self, conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.pin_conversation(conversation_id, bool(body.get("pinned", True)))

    def search_conversations(self, query: str, limit: int = 30) -> dict[str, Any]:
        return self.intake.search_conversations(query, limit)

    # ── Dashboard ──────────────────────────────────────────────

    def dashboard_snapshot(self) -> dict[str, Any]:
        """Single coherent snapshot. DB writes are transactionally scoped;
        heavy filesystem scans run outside the lock to avoid blocking other operations."""
        today = _today()
        yesterday = today - timedelta(days=1)

        # Phase 1: DB reads + re-aggregate today and yesterday (lock)
        with self.store.lock:
            self.dashboard_agg.fill_recent_gaps(7)
            # Re-aggregate today + yesterday so late-arriving events are captured
            self.dashboard_agg.aggregate_daily_metrics(today)
            self.dashboard_agg.aggregate_daily_metrics(yesterday)
            self.dashboard_agg.collect_storage_snapshot(today)

            conn = self.store.connection
            row = conn.execute(
                "SELECT * FROM dashboard_daily_metrics WHERE metric_date=?", (_date_str(today),)
            ).fetchone()
            storage_row = conn.execute(
                "SELECT * FROM dashboard_storage_snapshots WHERE snapshot_date=?", (_date_str(today),)
            ).fetchone()
            seven_start = _date_str(today - timedelta(days=6))
            rows_7d = conn.execute(
                "SELECT SUM(learning_duration_ms) ld, SUM(agent_completed_count) ac, "
                "SUM(knowledge_created_count) kc FROM dashboard_daily_metrics "
                "WHERE metric_date >= ?", (seven_start,)
            ).fetchone()
            trend_rows = conn.execute(
                "SELECT * FROM dashboard_daily_metrics WHERE metric_date >= ? ORDER BY metric_date",
                (seven_start,),
            ).fetchall()
            health = self._dashboard_health(conn)
            materials = self._dashboard_materials(conn)

        # Phase 2: heavy filesystem scan outside lock
        knowledge_count = self._count_knowledge_assets()

        def _r(d, k, fallback=0):
            if d is None: return fallback
            try: return int(d[k])
            except: return fallback

        external_bytes = (
            _r(storage_row, "wal_bytes") + _r(storage_row, "conversation_bytes") +
            _r(storage_row, "attachment_bytes") + _r(storage_row, "research_cache_bytes") +
            _r(storage_row, "log_bytes") + _r(storage_row, "temporary_bytes")
        )
        db_bytes = _r(storage_row, "database_bytes")

        return {
            "generatedAt": _now_iso(),
            "overview": {
                "storageTotalBytes": db_bytes + external_bytes,
                "learningDurationMs7d": rows_7d["ld"] or 0 if rows_7d else 0,
                "knowledgeAssetCount": knowledge_count,
                "agentCompletedRunCount7d": rows_7d["ac"] or 0 if rows_7d else 0,
                "storageReclaimableBytes": _r(storage_row, "reclaimable_bytes"),
                "knowledgeCreatedCount7d": rows_7d["kc"] or 0 if rows_7d else 0,
            },
            "storage": {
                "totalBytes": db_bytes + external_bytes,
                "breakdown": {
                    "databaseBytes": db_bytes,
                    "walBytes": _r(storage_row, "wal_bytes"),
                    "conversationBytes": _r(storage_row, "conversation_bytes"),
                    "attachmentBytes": _r(storage_row, "attachment_bytes"),
                    "researchCacheBytes": _r(storage_row, "research_cache_bytes"),
                    "logBytes": _r(storage_row, "log_bytes"),
                    "temporaryBytes": _r(storage_row, "temporary_bytes"),
                },
                "databaseComposition": {
                    "reasoningEstimatedBytes": _r(storage_row, "reasoning_bytes"),
                },
                "reclaimableBytes": _r(storage_row, "reclaimable_bytes"),
            },
            "trends": {
                "days": 7,
                "points": [{
                    "date": r["metric_date"],
                    "learningDurationMs": r["learning_duration_ms"],
                    "completedTasks": r["completed_learning_tasks"],
                    "knowledgeCreated": r["knowledge_created_count"],
                    "agentRuns": r["agent_run_count"],
                    "agentCompleted": r["agent_completed_count"],
                    "agentFailed": r["agent_failed_count"],
                    "toolCalls": r["tool_call_count"],
                    "inputTokens": r["input_tokens"],
                    "outputTokens": r["output_tokens"],
                    "reasoningTokens": r["reasoning_tokens"],
                } for r in trend_rows],
            },
            "health": health,
            "materials": materials,
        }

    def dashboard_refresh(self) -> dict[str, Any]:
        return self.dashboard_snapshot()

    # ── Legacy individual endpoints (delegated to snapshot) ───

    def dashboard_overview(self) -> dict[str, Any]:
        return self.dashboard_snapshot()

    def dashboard_trends(self, days: int = 7) -> dict[str, Any]:
        return self.dashboard_snapshot()

    def dashboard_storage(self) -> dict[str, Any]:
        return self.dashboard_snapshot()

    def dashboard_data_health(self) -> dict[str, Any]:
        return self.dashboard_snapshot()

    def dashboard_materials_summary(self) -> dict[str, Any]:
        return self.dashboard_snapshot()

    # ── Helpers ────────────────────────────────────────────────

    def _dashboard_health(self, conn) -> dict[str, Any]:
        db_path = self.vault / "90-Local-Only/Agent/agent.sqlite3"
        wal_path = Path(str(db_path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
        db_bytes = db_path.stat().st_size if db_path.is_file() else 0

        # Check for WAL warning (>500MB)
        wal_warning = wal_bytes > 500 * 1024 * 1024

        # Quick integrity check
        sqlite_ok = True
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()
            sqlite_ok = result and str(result[0]) == "ok"
        except Exception:
            sqlite_ok = False

        return {
            "databaseBytes": db_bytes,
            "walBytes": wal_bytes,
            "walWarning": wal_warning,
            "sqliteOk": sqlite_ok,
        }

    def _dashboard_materials(self, conn) -> dict[str, Any]:
        status_counts = {"completed": 0, "processing": 0, "failed": 0, "awaiting_confirmation": 0, "ready": 0}
        rows = conn.execute("SELECT status, COUNT(*) cnt FROM intake_items GROUP BY status").fetchall()
        for row in rows:
            s = str(row["status"])
            if s in status_counts: status_counts[s] = row["cnt"]
        total = sum(status_counts.values())
        recent = conn.execute(
            "SELECT c.title, i.status, i.updated_at FROM intake_items i JOIN conversations c ON c.id=i.conversation_id ORDER BY i.updated_at DESC LIMIT 5"
        ).fetchall()
        attachment_count = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
        return {
            "totalCount": total,
            "processingCount": status_counts["processing"],
            "failedCount": status_counts["failed"],
            "completedCount": status_counts["completed"],
            "pendingCount": status_counts["ready"] + status_counts["awaiting_confirmation"],
            "attachmentCount": attachment_count,
            "recentItems": [{"title": r["title"], "status": r["status"], "updatedAt": r["updated_at"]} for r in recent],
        }

    def _count_knowledge_assets(self) -> int:
        root = self.vault / "20-Knowledge"
        if not root.is_dir():
            return 0
        count = 0
        for md in root.rglob("*.md"):
            if md.is_symlink():
                continue
            count += 1
        return count

    def export_conversation(self, conversation_id: str) -> dict[str, Any]:
        result = self.intake.export_conversation(conversation_id, {
            "summary": self.store.latest_conversation_summary(conversation_id),
            "knowledgeSignals": self.store.list_conversation_signals(conversation_id, 100),
            "artifacts": self.intake.list_artifacts(conversation_id=conversation_id, limit=200).get("items", []),
        })
        self.log("conversation.exported", {"conversation_id": conversation_id, "size_bytes": result["sizeBytes"]})
        return result

    def retain_conversation_summary(self, conversation_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.retain_summary_only(conversation_id)
        self.log("conversation.summary-only", {"conversation_id": conversation_id, "removed_messages": result["removedMessages"]})
        return result

    def delete_conversation(self, conversation_id: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.delete_conversation(conversation_id)
        self.log("conversation.deleted", {"conversation_id": conversation_id, "removed_files": result["removedFiles"]})
        return result

    def clear_conversations(self, scope: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("explicit_confirmation_required")
        result = self.intake.clear_conversations(scope)
        self.log("conversations.cleared", {"scope": scope, "deleted_count": result["deletedCount"]})
        return result

    def create_attachment(self, body: dict[str, Any], raw: bytes | None = None, content_type: str = "") -> dict[str, Any]:
        conversation_id = self.intake.ensure_conversation(str(body.get("conversation_id") or "") or None, str(body.get("conversation_title") or "新会话"))
        if raw is not None:
            return self.intake.store_binary_attachment(
                conversation_id, str(body.get("display_name") or "attachment"),
                content_type or str(body.get("mime_type") or "application/octet-stream"), raw,
                str(body.get("kind") or ""),
            )
        if body.get("path"):
            return self.intake.reference_local_path(
                conversation_id, str(body["path"]),
                explicit_user_selection=body.get("explicit_user_selection") is True,
                confirmed_large_folder=body.get("confirmed_large_folder") is True,
            )
        if body.get("url"):
            if body.get("allow_network") is not True:
                raise ValueError("network_permission_required")
            source = self.fetch_public_web(str(body["url"]))
            return self.intake.store_url_content(
                conversation_id, source["canonicalUrl"], self.web.cached_text(source), "text/plain",
            )
        raise ValueError("attachment_body_required")

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        return self.intake.get_attachment(attachment_id)

    def delete_attachment(self, attachment_id: str) -> dict[str, Any]:
        attachment = self.intake.get_attachment(attachment_id)
        result = self.intake.delete_attachment(attachment_id)
        conversation_id = str(attachment.get("conversationId") or "")
        focus = self.store.get_conversation_focus(conversation_id) if conversation_id else None
        if focus and attachment_id in list(focus.get("activeAttachmentIds") or []):
            focus["activeAttachmentIds"] = [item for item in focus.get("activeAttachmentIds", []) if item != attachment_id]
            active_material = focus.get("activeMaterial") or {}
            if attachment_id in list(active_material.get("sourceAttachmentIds") or []):
                focus["activeMaterial"] = None
            self.store.save_conversation_focus(conversation_id, focus)
        self.log("attachment.deleted", {"attachment_id": attachment_id})
        return result

    def _today_candidates(self) -> list[dict[str, Any]]:
        items = self.list_recommendations()
        # The daily plan is the execution layer. Direction predictions and
        # unverified curriculum candidates stay in their own pools until they
        # pass the explicit admission gate.
        items = [item for item in items if not item.get("direction") and (
            not item.get("candidate") or study_workspace.admit_candidate(item).placement == "daily-plan"
        ) and int(item.get("splitPart") or 1) == 1]
        constraints = self.store.get_setting("today_constraints", {})
        if constraints.get("date") == date.today().isoformat() and constraints.get("noFormula"):
            items = [item for item in items if not any(
                token in f"{item.get('title', '')} {item.get('reason', '')}" for token in ("公式", "推导")
            )]
        return items

    def refresh_learning_directions(self) -> list[dict[str, Any]]:
        candidates = learning_directions.predict_directions(self.vault, self.store, 5)
        with self.store.lock:
            self.store.connection.execute("UPDATE direction_predictions SET state='stale' WHERE state='active'")
            self.store.connection.commit()
        stored = []
        for candidate in candidates:
            stored.append(self.store.save_direction_prediction({
                "id": candidate["id"], "horizon": candidate["horizon"], "title": candidate["title"],
                "rationale": candidate["why"], "evidence": candidate["evidence"],
                "confidence": candidate["confidence"], "state": "active",
            }))
        self.log("learning-directions.refreshed", {"count": len(stored)})
        return self.learning_directions()

    def learning_directions(self) -> list[dict[str, Any]]:
        metadata = {item["id"]: item for item in learning_directions.predict_directions(self.vault, self.store, 5)}
        persisted = self.store.list_direction_predictions("active", 20)
        return [{**item, **metadata.get(item["id"], {})} for item in persisted]

    def build_today(self, budget_minutes: int | None = None, *, force: bool = False) -> dict[str, Any]:
        plan_date = date.today().isoformat()
        current = self.store.get_daily_plan(plan_date)
        if current and not force and budget_minutes is None:
            eligible = {str(item["id"]): item for item in self._today_candidates()}
            if all(item.get("state") == "completed" or str(item["recommendationId"]) in eligible for item in current.get("items", [])):
                return self._hydrate_daily_plan(current, eligible)
        budget = max(5, min(360, int(budget_minutes or (current or {}).get("budgetMinutes") or 25)))
        recommendations_all = self._today_candidates()
        recommendation_by_id = {str(item["id"]): item for item in recommendations_all}
        fixed_items = [dict(item) for item in (current or {}).get("items", []) if item.get("fixed") and str(item.get("recommendationId")) in recommendation_by_id]
        chosen: list[dict[str, Any]] = fixed_items
        used = {str(item["recommendationId"]) for item in chosen}
        consumed = sum(int(item["minutes"]) for item in chosen)
        budget = max(budget, min(360, consumed))
        for recommendation in recommendations_all:
            rec_id = str(recommendation["id"])
            if rec_id in used or consumed >= budget:
                continue
            estimate = max(5, int(recommendation.get("estimatedMinutes", 10)))
            remaining = budget - consumed
            if estimate > remaining and remaining < 5:
                continue
            minutes = min(estimate, remaining)
            chosen.append({"recommendationId": rec_id, "minutes": minutes, "state": "planned", "fixed": False, "source": "deterministic-ranking"})
            used.add(rec_id); consumed += minutes
        plan = self.store.replace_daily_plan(
            plan_date, budget, chosen, expected_version=int((current or {}).get("version", 0)),
        )
        return self._hydrate_daily_plan(plan, recommendation_by_id)

    def _hydrate_daily_plan(self, plan: dict[str, Any], recommendation_by_id: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        lookup = recommendation_by_id or {str(item["id"]): item for item in self.list_recommendations()}
        for planned in plan.get("items", []):
            recommendation_id = str(planned["recommendationId"])
            if recommendation_id in lookup: continue
            with self.store.lock:
                row = self.store.connection.execute(
                    "SELECT details_json FROM study_sessions WHERE recommendation_id=? ORDER BY updated_at DESC LIMIT 1", (recommendation_id,),
                ).fetchone()
            if row:
                stored = json.loads(row["details_json"] or "{}").get("recommendation")
                if isinstance(stored, dict): lookup[recommendation_id] = stored
        items = [{**item, "recommendation": lookup.get(str(item["recommendationId"]), {
            "id": item["recommendationId"], "title": "推荐内容不可用", "kind": "learn",
        })} for item in plan.get("items", [])]
        return {**plan, "items": items, "totalMinutes": sum(int(item["minutes"]) for item in items)}

    def adjust_today(self, change: dict[str, Any]) -> dict[str, Any]:
        plan_date = date.today().isoformat()
        before = self.store.get_daily_plan(plan_date)
        if before is None:
            self.build_today()
            before = self.store.get_daily_plan(plan_date)
        assert before is not None
        adjustment_type = str(change.get("type") or "set_budget")
        target_id = str(change.get("target_id") or "")
        items = [dict(item) for item in before["items"]]
        budget = int(before["budgetMinutes"])
        if adjustment_type == "set_budget":
            budget = max(5, min(360, int(change.get("available_minutes") or budget)))
            fixed = [item for item in items if item.get("fixed")]
            fixed_minutes = sum(int(item["minutes"]) for item in fixed)
            if fixed_minutes > budget:
                raise ValueError("fixed_tasks_exceed_budget")
            recs = self._today_candidates()
            items = fixed; used = {str(item["recommendationId"]) for item in items}; consumed = fixed_minutes
            for rec in recs:
                if str(rec["id"]) in used or consumed >= budget:
                    continue
                estimate = max(5, int(rec.get("estimatedMinutes", 10))); remaining = budget - consumed
                if remaining < 5:
                    break
                items.append({"recommendationId": str(rec["id"]), "minutes": min(estimate, remaining), "state": "planned", "fixed": False, "source": "agent-adjustment"})
                used.add(str(rec["id"])); consumed += min(estimate, remaining)
        elif adjustment_type in {"move_tomorrow", "move_weekend", "remove"}:
            target = next((item for item in items if item["recommendationId"] == target_id), None)
            if not target:
                raise ValueError("daily_item_not_found")
            if target.get("fixed"):
                raise ValueError("fixed_task_cannot_be_removed")
            items = [item for item in items if item["recommendationId"] != target_id]
            if adjustment_type != "remove":
                recommendations.record_action(self.store, target_id, "tomorrow" if adjustment_type == "move_tomorrow" else "weekend", {"agent_adjusted": True})
        elif adjustment_type == "shorten":
            target = next((item for item in items if item["recommendationId"] == target_id), None)
            if not target:
                raise ValueError("daily_item_not_found")
            target["minutes"] = max(5, min(int(target["minutes"]), int(change.get("minutes") or 5)))
        elif adjustment_type == "reorder":
            order = [str(value) for value in change.get("order", [])]
            position = {value: index for index, value in enumerate(order)}
            items.sort(key=lambda item: position.get(str(item["recommendationId"]), len(position) + int(item.get("position", 0))))
        else:
            raise ValueError("unsupported_daily_adjustment")
        normalized = [{key: value for key, value in item.items() if key != "recommendation"} for item in items]
        after = self.store.replace_daily_plan(plan_date, budget, normalized, expected_version=int(before["version"]))
        prior_constraints = change.get("_previous_constraints") if "_previous_constraints" in change else self.store.get_setting("today_constraints", {})
        before_snapshot = {**before, "constraints": dict(prior_constraints or {})}
        after_snapshot = {**after, "constraints": self.store.get_setting("today_constraints", {})}
        action = self.store.save_daily_adjustment(
            adjustment_type, target_id, before_snapshot, after_snapshot, str(change.get("reason") or "Agent 调整今日安排"),
        )
        self.log("daily-plan.adjusted", {"action_id": action["actionId"], "type": adjustment_type, "before_minutes": sum(int(item["minutes"]) for item in before["items"]), "after_minutes": sum(int(item["minutes"]) for item in after["items"])})
        return {**action, "plan": self._hydrate_daily_plan(after),
                "beforeMinutes": sum(int(item["minutes"]) for item in before["items"]),
                "afterMinutes": sum(int(item["minutes"]) for item in after["items"]), "undoAvailable": True}

    def undo_today_adjustment(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_daily_adjustment(action_id)
        if action["state"] != "applied":
            raise ValueError("daily_adjustment_not_undoable")
        before, after = action["before"], action["after"]
        current = self.store.get_daily_plan(str(after["date"]))
        if not current or int(current["version"]) != int(after["version"]):
            raise RuntimeError("daily_plan_changed_since_adjustment")
        restored = self.store.replace_daily_plan(
            str(before["date"]), int(before["budgetMinutes"]), list(before["items"]),
            expected_version=int(current["version"]),
        )
        self.store.set_setting("today_constraints", dict(before.get("constraints") or {}))
        self.store.mark_daily_adjustment_undone(action_id)
        self.log("daily-plan.adjustment-undone", {"action_id": action_id})
        return {"actionId": action_id, "state": "undone", "plan": self._hydrate_daily_plan(restored)}

    def list_agent_artifacts(self, artifact_type: str = "", status: str = "", conversation_id: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_artifacts(artifact_type, status, conversation_id, limit, offset)

    def get_agent_artifact(self, artifact_id: str) -> dict[str, Any]:
        return self.intake.get_artifact(artifact_id)

    def revise_agent_artifact(self, artifact_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.intake.revise_artifact(artifact_id, str(body.get("instruction") or ""), dict(body.get("patch", {})), body.get("expected_version"))

    def agent_artifact_action(self, artifact_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self.intake.artifact_action(artifact_id, str(body.get("action") or ""))
        self.log("artifact.action", {"artifact_id": artifact_id, "action": body.get("action")})
        return result

    def list_materials(self, status: str = "", limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.intake.list_materials(status, limit, offset)

    def get_material(self, intake_id: str) -> dict[str, Any]:
        return self.intake.get_material(intake_id)

    def list_brain_runs(self, *, limit: int = 50, offset: int = 0, status: str = "") -> dict[str, Any]:
        items = self.store.list_brain_runs(limit, offset, status)
        return {"items": items, "limit": max(1, min(100, limit)), "offset": max(0, offset), "has_more": len(items) == max(1, min(100, limit))}

    def get_brain_run(self, run_id: str) -> dict[str, Any]:
        return self.store.get_brain_run(run_id, include_details=True)

    def cancel_brain_run(self, run_id: str) -> dict[str, Any]:
        self.store.request_brain_cancel(run_id)
        self.log("workflow.cancel-requested", {"run_id": run_id})
        return self.store.get_brain_run(run_id, include_details=True)

    def retry_brain_run(self, run_id: str) -> dict[str, Any]:
        result = self.explicit.workflows.retry(run_id); self.log("workflow.retried", {"run_id": result["id"], "prior_run_id": run_id}); return result

    def brain_events(self, run_id: str, after_sequence: int = 0) -> dict[str, Any]:
        run = self.get_brain_run(run_id)
        agent_events = self.store.list_agent_run_events(run_id, after_sequence)
        return {
            "schemaVersion": 2,
            "run_id": run_id,
            "status": run["status"],
            "steps": run["steps"],
            "tool_events": run["tool_events"],
            "proposed_actions": run["proposed_actions"],
            "events": [item["payload"] for item in agent_events],
            "checkpoint": self.store.get_agent_run_checkpoint(run_id),
        }

    def assistant_run_events(
        self,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        run = self.store.get_brain_run(run_id)
        saved = self.store.list_agent_run_events(
            run_id,
            max(0, int(after_sequence)),
            limit,
        )
        return {
            "schemaVersion": 2,
            "runId": run_id,
            "status": run["status"],
            "events": [item["payload"] for item in saved],
            "checkpoint": self.store.get_agent_run_checkpoint(run_id),
        }

    def brain_capabilities(self) -> dict[str, Any]:
        return {"workflow_version": self.explicit.workflows.version, "skills": self.explicit.skills.definitions(), "tools": self.tools.definitions(), "pi_is_only_agent_loop": True, "reviewed_core_read_only": True}

    def brain_health(self) -> dict[str, Any]:
        profiles = self.list_model_profiles()
        routes = self.model_routing()
        return {
            "ok": True, "workflow_version": self.explicit.workflows.version, "schema_version": self.store.schema_version(),
            "model_configured": any(item["configured"] and item["enabled"] for item in profiles),
            "model_routes": {key: bool(value.get("profileId")) for key, value in routes.items()},
            "fallback_available": True,
        }

    def brain_diagnostics(self) -> dict[str, Any]:
        profiles = self.list_model_profiles()
        from urllib.parse import urlparse
        with self.store.lock:
            counts = {
                "conversations": int(self.store.connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]),
                "conversation_summaries": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0]),
                "knowledge_signals": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_knowledge_signals WHERE status='active'").fetchone()[0]),
                "learning_events": int(self.store.connection.execute("SELECT COUNT(*) FROM learning_events").fetchone()[0]),
                "direction_predictions": int(self.store.connection.execute("SELECT COUNT(*) FROM direction_predictions WHERE state='active'").fetchone()[0]),
                "today_adjustments": int(self.store.connection.execute("SELECT COUNT(*) FROM daily_plan_adjustments").fetchone()[0]),
                "web_sources": int(self.store.connection.execute("SELECT COUNT(*) FROM web_sources").fetchone()[0]),
                "agent_actions": int(self.store.connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0]),
                "snapshots": int(self.store.connection.execute("SELECT COUNT(*) FROM file_snapshots").fetchone()[0]),
                "undo_available": int(self.store.connection.execute("SELECT COUNT(*) FROM agent_actions WHERE status='applied' AND action_type!='undo'").fetchone()[0]),
                "conversation_focus": int(self.store.connection.execute("SELECT COUNT(*) FROM conversation_focus").fetchone()[0]),
                "material_bundles": int(self.store.connection.execute("SELECT COUNT(*) FROM material_bundles").fetchone()[0]),
                "organization_plans": int(self.store.connection.execute("SELECT COUNT(*) FROM organization_plans").fetchone()[0]),
            }
            latest_web = self.store.connection.execute("SELECT title, fetched_at FROM web_sources ORDER BY fetched_at DESC LIMIT 1").fetchone()
            latest_error = self.store.connection.execute(
                "SELECT error_code, error_message, updated_at FROM brain_runs WHERE status='failed' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        jobs = self.store.list_jobs()
        routes = self.model_routing()
        return {
            "service": {"status": "online", "api_version": "v1", "workflow_version": self.explicit.workflows.version, "schema_version": self.store.schema_version(), "runtime_id": self.runtime_id},
            "models": [{"id": item["id"], "name": item["displayName"], "provider": item["providerType"], "base_url_host": urlparse(item["baseUrl"]).hostname or "", "configured": item["configured"], "model": item["defaultModel"]} for item in profiles],
            "brain_runs": self.store.list_brain_runs(20, 0),
            "indexes": {"reviewed": len(learning.scan_reviewed(self.vault)), "curriculum_candidates": len(self.store.list_curriculum_candidates("active")), "research_bundles": len(self.store.list_research_bundles(100, 0)), "pending_reviews": sum(item.get("review_state") == "pending" for item in self.list_reviews()), **counts},
            "runtime": {
                "current_model_route": (routes.get("assistant_chat") or routes.get("assistant") or {}).get("profileId") or "local-deterministic",
                "key_configured": any(item["configured"] and item["enabled"] for item in profiles),
                "web_provider": "public-http" if counts["web_sources"] else "not-used",
                "latest_web_fetch": dict(latest_web) if latest_web else None,
                "pdf_jobs": sum(item.get("kind") == "prepare-pdf" for item in jobs),
                "typescript_boundary": "Obsidian UI, events and native Markdown rendering",
                "python_boundary": "policy, persistence, retrieval, transactions and model adapters",
            },
            "recent_error": redact(dict(latest_error)) if latest_error else None,
        }

    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self.brain_change_sets.public_record(change_set_id)

    def diff_change_set(self, change_set_id: str) -> dict[str, Any]:
        return self.brain_change_sets.diff(change_set_id)

    def _append_assistant_lifecycle_event(
        self,
        run_id: str,
        event_type: str,
        **payload: Any,
    ) -> dict[str, Any]:
        existing = self.store.list_agent_run_events(run_id, 0, 2000)
        for item in existing:
            value = item.get("payload") or {}
            if item.get("type") == event_type and (
                not payload.get("proposalId")
                or value.get("proposalId") == payload.get("proposalId")
            ):
                return value
        request = self.store.load_brain_request(run_id)
        metadata = request.get("metadata") or {}
        return self.store.append_agent_run_event_next(
            run_id,
            event_type,
            {
                "conversationId": str(metadata.get("conversation_id") or ""),
                **payload,
            },
        )

    def list_research_bundles(self, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        items = self.store.list_research_bundles(limit, offset)
        return {"items": items, "limit": limit, "offset": offset, "has_more": len(items) == limit}

    def get_research_bundle(self, bundle_id: str) -> dict[str, Any]:
        return self.store.get_research_bundle(bundle_id)

    def search_public_web(self, query: str, limit: int = 8) -> dict[str, Any]:
        result = self.web.search(query, limit)
        self.log("web.search-completed", {"query_length": len(query), "result_count": len(result["results"])})
        return result

    def search_academic_web(self, query: str, limit: int = 8) -> dict[str, Any]:
        result = self.web.search_academic(query, limit)
        self.log("web.academic-search-completed", {
            "query_length": len(query),
            "result_count": len(result["results"]),
            "provider_failure_count": len(result.get("providerFailures") or []),
        })
        return result

    def fetch_public_web(self, url: str) -> dict[str, Any]:
        source = self.web.fetch_for_model(url)
        self.log("web.source-fetched", {"source_id": source["id"], "domain": source["domain"], "prompt_injection_detected": source["promptInjectionDetected"]})
        return source

    def web_capabilities(self) -> dict[str, Any]:
        return self.web.capabilities()

    def research_public_web(self, query: str, urls: list[str] | None = None, limit: int = 5) -> dict[str, Any]:
        result = self.web.research(query, urls, limit)
        self.log("web.research-completed", {"bundle_id": result["bundle"]["id"], "source_count": len(result["sources"]), "failure_count": len(result["failures"])})
        return result

    def autonomy_status(self) -> dict[str, Any]:
        return self.autonomy.status()

    def set_autonomy_mode(self, body: dict[str, Any]) -> dict[str, Any]:
        result = self.autonomy.set_mode(str(body.get("mode") or ""), acknowledge_summary=body.get("acknowledge_summary") is True)
        self.log("autonomy.mode-updated", {"mode": result["mode"], "summary_acknowledged": result["permissionSummaryAcknowledged"]})
        return result

    def apply_autonomous_vault_change(self, body: dict[str, Any]) -> dict[str, Any]:
        # Explicit (legacy Brain) protected-write path. See ExplicitWorkflowService.
        return self.explicit.apply_autonomous_vault_change(body)

    def undo_autonomous_vault_change(self, action_id: str) -> dict[str, Any]:
        result = self.autonomy.undo(action_id)
        self.log("vault.change-undone", {"action_id": action_id, "undo_action_id": result["undoActionId"], "path": result["path"]})
        return result

    def save_research_bundle(self, bundle_id: str) -> dict[str, Any]:
        # Explicit (legacy Brain) research-bundle save path. See ExplicitWorkflowService.
        return self.explicit.save_research_bundle(bundle_id)

    def add_research_to_plan(self, bundle_id: str) -> dict[str, Any]:
        bundle = self.store.get_research_bundle(bundle_id)
        tasks = [{"id": f"research-task-{uuid.uuid4().hex[:12]}", "title": source["title"], "minutes": source["estimated_minutes"], "state": "proposed", "source": source.get("canonical_url") or source.get("metadata", {}).get("path", "")} for source in bundle.get("sources", [])]
        proposal = {"id": f"plan-{uuid.uuid4().hex}", "title": f"阅读计划：{bundle['title']}", "state": "proposed", "tasks": tasks}
        self.store.save_plan_proposal(proposal); self.log("research.added-to-plan", {"bundle_id": bundle_id, "proposal_id": proposal["id"]}); return proposal

    def confirm_plan_proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.store.update_plan_proposal_state(proposal_id, "confirmed")
        self.log("plan-proposal.confirmed", {"proposal_id": proposal_id, "task_count": len(proposal["tasks"])})
        return proposal

    def list_curriculum_candidates(self, status: str = "active") -> dict[str, Any]:
        return {"items": self.store.list_curriculum_candidates(status), "status": status}

    def refresh_curriculum(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.submit_workflow({**body, "text": str(body.get("text") or "根据当前知识状态生成学习计划"), "mode": "plan", "source": "curriculum"})

    def curriculum_candidate_action(self, candidate_id: str, action: str, cooldown_until: str | None = None) -> dict[str, Any]:
        self.store.curriculum_action(candidate_id, action, cooldown_until); self.log("curriculum.action", {"candidate_id": candidate_id, "action": action}); return {"candidate_id": candidate_id, "action": action}

    @staticmethod
    def _public_job(row: dict[str, Any]) -> dict[str, Any]:
        job = dict(row)
        job["payload"] = json.loads(job.pop("payload_json"))
        raw_result = job.pop("result_json")
        job["result"] = json.loads(raw_result) if raw_result else None
        job["cancel_requested"] = bool(job["cancel_requested"])
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        return [self._public_job(row) for row in self.store.list_jobs()]

    def get_job(self, job_id: str) -> dict[str, Any]:
        row = self.store.get_job(job_id)
        if not row: raise RuntimeError(f"Unknown job: {job_id}")
        job = self._public_job(row)
        job["timeline"] = self.store.list_job_events(job_id)
        return job

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self._public_job(self.store.request_cancel(job_id)); self.log("job.cancel-requested", {"job_id": job_id}); return job

    def retry_job(self, job_id: str) -> dict[str, Any]:
        prior = self.store.get_job(job_id)
        if not prior: raise RuntimeError(f"Unknown job: {job_id}")
        if prior["state"] not in {"failed", "cancelled"}: raise RuntimeError(f"Job cannot be retried from state {prior['state']}")
        new_id = self.enqueue(str(prior["kind"]), json.loads(prior["payload_json"]))
        self.log("job.retried", {"job_id": new_id, "prior_job_id": job_id})
        return self.get_job(new_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        self.store.delete_job_record(job_id); self.log("job.record-deleted", {"job_id": job_id}); return {"job_id": job_id, "deleted": True, "knowledge_files_deleted": False}
    def list_prepared(self) -> list[dict[str, str]]:
        rows = prepared_pdf.list_prepared(self.vault); now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            for item in rows:
                self.store.connection.execute("INSERT INTO change_sets(change_set_id, prepared_id, state, bundle_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(change_set_id) DO UPDATE SET state=excluded.state, bundle_hash=excluded.bundle_hash, updated_at=excluded.updated_at", (item["prepared_id"], item["prepared_id"], item["state"], item["bundle_hash"], now, now))
            self.store.connection.commit()
        return rows
    def inspect_prepared(self, prepared_id: str) -> str: return prepared_pdf.inspect_bundle(self.vault, prepared_id)
    def reject_prepared(self, prepared_id: str, reason: str = "") -> dict[str, Any]:
        result = self.materials.reject(prepared_id, reason)
        linked = next((row for row in self.store.list_jobs() if row.get("prepared_id") == prepared_id and row["state"] == "awaiting_confirmation"), None)
        if linked: self.store.update_job(str(linked["job_id"]), "rejected", stage="rejected", progress=100)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            self.store.connection.execute("UPDATE change_sets SET state='rejected', updated_at=? WHERE prepared_id=?", (now, prepared_id))
            self.store.connection.commit()
        self.log("prepared.rejected", {"prepared_id": prepared_id, "reason": reason})
        return result
    def apply_prepared(self, prepared_id: str) -> str:
        linked = next((row for row in self.store.list_jobs() if row.get("prepared_id") == prepared_id and row["state"] == "awaiting_confirmation"), None)
        if linked: self.store.update_job(str(linked["job_id"]), "applying", stage="applying", progress=100)
        try:
            result = str(prepared_pdf.apply_prepared(self.vault, prepared_id))
        except Exception:
            if linked: self.store.update_job(str(linked["job_id"]), "awaiting_confirmation", stage="awaiting_confirmation", progress=100)
            raise
        if linked: self.store.update_job(str(linked["job_id"]), "completed", result={"apply_result": result, "prepared_id": prepared_id}, stage="completed", progress=100, prepared_id=prepared_id)
        self.log("prepared.applied", {"prepared_id": prepared_id}); return result
    def list_reviews(self) -> list[dict[str, Any]]:
        self.sync_artifacts(); return [{**item, "path": str(item["path"])} for item in review.scan_artifacts(self.vault)]
    def show_review(self, artifact_id: str) -> str: return review.review_packet(self.vault, artifact_id)
    def diff_review(self, artifact_id: str) -> str: return review.artifact_diff(self.vault, artifact_id)
    def transition_review(self, artifact_id: str, action: str, reason: str = "") -> str:
        result = str(review.transition(self.vault, artifact_id, action, reason)); self.log("review.transition", {"artifact_id": artifact_id, "action": action}); return result
    def today_learning(self) -> dict[str, Any]: return learning.daily_plan(self.vault)

    LEARNING_EVENT_TYPES = {
        "recommendation_exposed", "recommendation_clicked", "recommendation_started", "recommendation_completed", "recommendation_snoozed",
        "recommendation_added_to_plan", "recommendation_feedback", "recommendation_dismissed",
        "study_session_started", "study_session_paused", "study_session_resumed",
        "study_session_completed", "study_session_abandoned", "quiz_started",
        "quiz_answered", "quiz_completed", "hint_opened", "related_note_opened",
        "source_opened", "lesson_error_reported", "plan_task_completed", "plan_task_postponed", "assistant_topic_revisited",
    }
    LEARNING_PAYLOAD_KEYS = {"action", "category", "route", "correctness", "hintUsed", "reason", "errorCode", "sourceType", "verificationGrade"}

    def record_learning_events(self, body: dict[str, Any]) -> dict[str, Any]:
        if int(body.get("schemaVersion", 1)) != 1:
            raise ValueError("learning_event_schema_mismatch")
        raw_events = body.get("events", [])
        if not isinstance(raw_events, list) or not raw_events or len(raw_events) > 100:
            raise ValueError("learning_events_batch_must_contain_1_to_100_items")
        events: list[dict[str, Any]] = []
        for raw in raw_events:
            if not isinstance(raw, dict):
                raise ValueError("learning_event_must_be_an_object")
            event_id = str(raw.get("id", ""))
            event_type = str(raw.get("eventType", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", event_id):
                raise ValueError("learning_event_id_invalid")
            if event_type not in self.LEARNING_EVENT_TYPES:
                raise ValueError("learning_event_type_invalid")
            if int(raw.get("schemaVersion", 0)) != 1:
                raise ValueError("learning_event_schema_mismatch")
            payload = raw.get("payload", {})
            if not isinstance(payload, dict) or not set(payload).issubset(self.LEARNING_PAYLOAD_KEYS):
                raise ValueError("learning_event_payload_contains_private_or_unsupported_fields")
            if len(json.dumps(payload, ensure_ascii=False)) > 2048:
                raise ValueError("learning_event_payload_too_large")
            created_at = str(raw.get("createdAt", ""))
            try: datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as error: raise ValueError("learning_event_created_at_invalid") from error
            duration = raw.get("durationMs")
            if duration is not None and (not isinstance(duration, int) or duration < 0 or duration > 86_400_000):
                raise ValueError("learning_event_duration_invalid")
            events.append({
                "id": event_id, "eventType": event_type,
                "subjectType": str(raw.get("subjectType", ""))[:64], "subjectId": str(raw.get("subjectId", ""))[:256],
                "topic": str(raw.get("topic", ""))[:160] or None, "domain": str(raw.get("domain", ""))[:120] or None,
                "recommendationId": str(raw.get("recommendationId", ""))[:256] or None,
                "sessionId": str(raw.get("sessionId", ""))[:256] or None,
                "durationMs": duration, "payload": payload, "createdAt": created_at, "schemaVersion": 1,
            })
        inserted, duplicates = self.store.append_learning_events(events)
        self.log("learning.events-recorded", {"inserted": inserted, "duplicates": duplicates})
        return {"ok": True, "inserted": inserted, "duplicates": duplicates, "schemaVersion": 1}

    def learner_profile(self, *, rebuild: bool = False) -> dict[str, Any]:
        events = self.store.list_learning_events(5000)
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        dates = sorted({str(item["created_at"])[:10] for item in events if item.get("created_at")})
        count, days = len(events), len(dates)
        weight = min(.05, count / 400) if count < 20 else min(.10, .05 + (count - 20) / 1600) if count <= 100 or days < 7 else .15
        by_type: dict[str, int] = {}
        for item in events: by_type[str(item["event_type"])] = by_type.get(str(item["event_type"]), 0) + 1
        exposed = max(1, by_type.get("recommendation_exposed", 0))
        started = max(1, by_type.get("study_session_started", 0))
        feature_rows = [
            ("recommendation_click_rate", "global", by_type.get("recommendation_clicked", 0) / exposed, exposed),
            ("recommendation_completion_rate", "global", by_type.get("study_session_completed", 0) / started, started),
            ("hint_dependency_rate", "global", by_type.get("hint_opened", 0) / started, started),
        ]
        for domain in sorted({str(item.get("domain") or "") for item in events if item.get("domain")}):
            relevant = [item for item in events if item.get("domain") == domain]
            starts = sum(item["event_type"] == "study_session_started" for item in relevant)
            completed = sum(item["event_type"] == "study_session_completed" for item in relevant)
            feature_rows.append(("topic_completion_rate", domain, completed / max(1, starts), max(1, starts)))
        window_start = dates[0] if dates else now[:10]
        features = [{
            "key": key, "scope": scope, "value": round(value, 4),
            "confidence": round(min(.95, evidence / 100), 3), "evidenceCount": evidence,
            "windowStart": window_start, "windowEnd": now[:10], "updatedAt": now, "source": "inferred",
        } for key, scope, value, evidence in feature_rows]
        if rebuild or events: self.store.replace_inferred_features(features)
        return {"schemaVersion": 1, "eventCount": count, "coveredDays": days, "behaviorWeight": round(weight, 4), "features": self.store.list_learner_features(), "lastRebuiltAt": now}

    def clear_learning_data(self, scope: str) -> dict[str, Any]:
        if scope not in {"recent-7-days", "all"}: raise ValueError("learning_data_scope_invalid")
        since = (date.today() - timedelta(days=7)).isoformat() if scope == "recent-7-days" else ""
        deleted = self.store.clear_learning_events(since)
        profile = self.learner_profile(rebuild=True)
        self.log("learning.data-cleared", {"scope": scope, "deleted": deleted})
        return {"ok": True, "scope": scope, "deleted": deleted, "profile": profile}

    def daily_dashboard(self) -> dict[str, Any]:
        dashboard = self.dashboard()
        today_plan = self.build_today()
        planned_items = list(today_plan.get("items") or [])
        dashboard["summary"] = {**dashboard["summary"],
            "suggested_minutes": sum(int(item.get("minutes") or 0) for item in planned_items if item.get("state") != "skipped"),
            "completed_minutes": sum(int(item.get("minutes") or 0) for item in planned_items if item.get("state") == "completed"),
            "review_count": sum((item.get("recommendation") or {}).get("kind") == "review" for item in planned_items),
            "learn_count": sum((item.get("recommendation") or {}).get("kind") in {"learn", "explore"} for item in planned_items),
        }
        recent_adjustment = self.store.latest_daily_adjustment()
        profiles = self.models.list()
        routes = self.models.routing()
        model_route = next((routes.get(name) for name in ("curriculum_planner", "daily_knowledge_generator") if routes.get(name, {}).get("profileId")), {})
        profile = next((item for item in profiles if item["id"] == model_route.get("profileId")), None)
        return {
            **dashboard, "schemaVersion": 1, "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "todayPlan": today_plan, "directions": self.learning_directions(),
            "todayConstraints": self.store.get_setting("today_constraints", {}),
            "recentAdjustment": ({
                "actionId": recent_adjustment["actionId"], "type": recent_adjustment["type"],
                "beforeMinutes": sum(int(item["minutes"]) for item in recent_adjustment["before"]["items"]),
                "afterMinutes": sum(int(item["minutes"]) for item in recent_adjustment["after"]["items"]),
                "undoAvailable": True,
            } if recent_adjustment else None),
            "learnerProfile": self.learner_profile(),
            "runtime": {"ranking": "typescript-domain-v1", "persistence": "python-sqlite-adapter-v1", "modelConfigured": bool(profile and profile.get("configured")), "provider": profile.get("displayName") if profile else None},
        }
    def list_recommendations(self) -> list[dict[str, Any]]:
        items = recommendations.build(self.vault, self.store, self.list_prepared())
        generated_ids = {str(item["id"]) for item in items}
        for persisted in self.store.list_persisted_recommendations("active"):
            if str(persisted["id"]) not in generated_ids:
                items.append(persisted)
        for candidate in self.store.list_curriculum_candidates("active"):
            verification = candidate.get("verification", {})
            grade = str(verification.get("grade", "C"))
            # Deterministic fallback candidates remain useful for the learning
            # route but may not masquerade as model-generated daily knowledge.
            model_generated = bool(candidate.get("model_profile_id"))
            scores = candidate.get("scores", {})
            score = round(100 * (.35 * float(scores.get("gap", 0)) + .3 * float(scores.get("mainline", 0)) + .2 * float(scores.get("reuse", 0)) + .15 * float(candidate.get("confidence", 0))), 2)
            recommendation = {
                "id": candidate["candidate_id"], "title": candidate["title"], "kind": "explore",
                "estimatedMinutes": candidate["estimated_minutes"], "score": score,
                "reason": candidate["why_now"], "reasonDetails": [
                    "尚未成为正式笔记", f"候选类型：{candidate['kind']}",
                    f"生成依据：{candidate.get('generated_by', 'deterministic-curriculum-v1')}",
                ],
                "prerequisites": [{"title": title, "path": ""} for title in candidate.get("prerequisites", [])],
                "relatedNotes": [{"title": title, "path": ""} for title in candidate.get("related_topics", [])],
                "microConcepts": [], "sourcePath": "", "domain": candidate["domain"],
                "route": candidate["route"], "dueState": "candidate", "mastery": 0,
                "actions": ["later", "tomorrow", "weekend", "favorite", "not_interested", "start"],
                "candidate": model_generated, "candidateKind": candidate["kind"], "generatedAt": candidate["generated_at"],
                "confidence": float(candidate.get("confidence", 0)), "verificationGrade": grade,
                "verificationScore": float(verification.get("score", 0)), "verification": verification,
                "sourceBasis": list(candidate.get("basis", {}).get("sourceBasis", [])),
                "behaviorBasis": list(candidate.get("basis", {}).get("behaviorBasis", [])),
                "learningOutcomes": list(candidate.get("learning_outcomes", [])),
                "gapScore": float(scores.get("gap", 0)) * 100,
                "behaviorScore": float(scores.get("behavior", .5)) * 100,
                "difficultyScore": {"easy": 1, "medium": 2, "hard": 3}.get(str(candidate.get("difficulty")), 2),
            }
            items.extend(study_workspace.split_candidate(recommendation))
        existing_ids = {str(item["id"]) for item in items}
        for direction in self.learning_directions():
            if str(direction["id"]) in existing_ids:
                continue
            priority_score = {"高优先级": 76.0, "中优先级": 64.0, "探索性": 52.0}.get(str(direction.get("priority")), 58.0)
            items.append({
                "id": direction["id"], "title": direction["title"], "kind": "explore",
                "estimatedMinutes": int(direction.get("estimatedMinutes", 15)), "score": priority_score,
                "reason": "；".join(direction.get("why", [])), "reasonDetails": list(direction.get("why", [])),
                "prerequisites": [{"title": title, "path": ""} for title in direction.get("prerequisites", [])],
                "relatedNotes": [{"title": title, "path": ""} for title in direction.get("connections", [])],
                "microConcepts": [], "sourcePath": "", "domain": str((direction.get("connections") or ["学习方向"])[0]),
                "route": direction.get("route", "mainline"), "dueState": "direction", "mastery": 0,
                "actions": ["tomorrow", "weekend", "not_interested", "start"],
                "direction": True, "horizon": direction.get("horizon"), "priority": direction.get("priority"),
                "confidenceLabel": direction.get("confidenceLabel"), "noveltyBasis": direction.get("noveltyBasis"),
                "isNewKnowledge": bool(direction.get("isNewKnowledge")), "sourceQuality": direction.get("sourceQuality"),
                "conversationScore": min(100, float(direction.get("confidence", 0)) * 100),
            })
        return sorted(items, key=lambda item: (-float(item.get("score", 0)), int(item.get("estimatedMinutes", 10)), str(item.get("title", ""))))

    def add_artifact_to_today(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.intake.get_artifact(artifact_id)
        recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
        existing = self.store.get_persisted_recommendation(recommendation_id)
        if existing and existing.get("state") == "active":
            return {"recommendation": existing, "duplicate": True, "undo_available": True}
        payload = artifact.get("payload") or {}
        item = {
            "id": recommendation_id, "title": artifact["title"], "kind": "learn",
            "estimatedMinutes": max(5, min(180, int(payload.get("estimatedMinutes") or 12))),
            "score": 78.0, "state": "active", "artifactId": artifact_id,
            "reason": "由助手生成并由你加入今日，适合继续完成当前学习目标。",
            "reasonDetails": ["来自当前助手任务", "尚未自动写入正式知识"],
            "prerequisites": [{"title": str(title), "path": ""} for title in payload.get("prerequisites", [])[:5]],
            "relatedNotes": [{"title": str(item.get("title") or "相关笔记"), "path": str(item.get("path") or "")} for item in payload.get("sources", [])[:5]],
            "microConcepts": [], "quizPreview": {"question": str((payload.get("quizPreview") or ["请复述核心定义与适用条件。"])[:1][0]), "answerHint": "先讲直觉，再补充条件。"},
            "sourcePath": str((payload.get("sources") or [{}])[0].get("path") or ""),
            "domain": str(payload.get("domain") or "当前学习主线"), "route": "mainline",
            "dueState": "assistant", "mastery": 0,
            "actions": ["later", "tomorrow", "weekend", "favorite", "not_interested", "start"],
        }
        stored = self.store.upsert_recommendation(item)
        self.log("assistant.today-added", {"artifact_id": artifact_id, "recommendation_id": recommendation_id})
        return {"recommendation": stored, "duplicate": False, "undo_available": True}

    def undo_artifact_today(self, artifact_id: str) -> dict[str, Any]:
        recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
        removed = self.store.set_persisted_recommendation_state(recommendation_id, "removed")
        self.log("assistant.today-undone", {"artifact_id": artifact_id, "recommendation_id": recommendation_id})
        return {"recommendation_id": recommendation_id, "removed": removed, "undo_available": False}

    def assistant_context(self, artifact_id: str = "", conversation_id: str = "") -> dict[str, Any]:
        artifact = self.intake.get_artifact(artifact_id) if artifact_id else None
        resolved_conversation = conversation_id or str((artifact or {}).get("conversationId") or "")
        group = self.intake.latest_artifact_group(resolved_conversation) if resolved_conversation else None
        task = self.intake.latest_task_thread(resolved_conversation) if resolved_conversation else None
        payload = (artifact or {}).get("payload") or {}
        sources = list(payload.get("sources") or payload.get("evidence") or [])[:5]
        materials = self.intake.list_artifacts("material", conversation_id=resolved_conversation, limit=5)["items"] if resolved_conversation else []
        today_recommendation = None
        if artifact_id:
            recommendation_id = f"rec-assistant-{hashlib.sha256(artifact_id.encode()).hexdigest()[:16]}"
            today_recommendation = self.store.get_persisted_recommendation(recommendation_id)
        focus = self.store.get_conversation_focus(resolved_conversation) if resolved_conversation else None
        latest_bundle = None; latest_plan = None
        if resolved_conversation:
            with self.store.lock:
                bundle_row = self.store.connection.execute(
                    "SELECT id FROM material_bundles WHERE conversation_id=? ORDER BY updated_at DESC LIMIT 1", (resolved_conversation,),
                ).fetchone()
                plan_row = self.store.connection.execute(
                    """SELECT p.id FROM organization_plans p JOIN material_bundles b ON b.id=p.bundle_id
                       WHERE b.conversation_id=? ORDER BY p.updated_at DESC LIMIT 1""", (resolved_conversation,),
                ).fetchone()
            latest_bundle = self.store.get_material_bundle(str(bundle_row["id"])) if bundle_row else None
            latest_plan = self.store.get_organization_plan(str(plan_row["id"])) if plan_row else None
        return {
            "task": task, "artifactGroup": group,
            "focus": focus, "currentUnderstanding": (latest_bundle or {}).get("understanding"),
            "organizationPlan": latest_plan,
            "inToday": bool(today_recommendation and today_recommendation.get("state") == "active"),
            "relatedNotes": [{"title": str(item.get("title") or "相关笔记"), "path": str(item.get("path") or "")} for item in sources],
            "recentMaterials": [{"id": item["id"], "title": item["title"], "status": item["status"]} for item in materials],
            "recommendedActions": [
                {"id": "today", "label": "加入今日", "target": "today"},
                {"id": "path", "label": "生成学习路径", "target": "plan"},
                {"id": "follow-up", "label": "继续追问", "target": "assistant"},
            ],
        }

    def conversation_focus(self, conversation_id: str) -> dict[str, Any]:
        focus = self.store.get_conversation_focus(conversation_id)
        if not focus: raise ValueError("conversation_focus_not_found")
        return focus

    def material_bundle(self, bundle_id: str) -> dict[str, Any]:
        return self.store.get_material_bundle(bundle_id)

    def organization_plan(self, plan_id: str) -> dict[str, Any]:
        return self.store.get_organization_plan(plan_id)

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        item = next((item for item in self.list_recommendations() if item["id"] == recommendation_id), None)
        if not item: raise RuntimeError(f"Recommendation not found: {recommendation_id}")
        return item

    def recommendation_action(self, recommendation_id: str, action: str, details: dict[str, Any]) -> dict[str, Any]:
        if action == "undo":
            if not self.store.undo_recommendation_feedback(recommendation_id): raise RuntimeError("No recommendation feedback to undo")
            self.log("recommendation.undo", {"recommendation_id": recommendation_id})
            return {"recommendation_id": recommendation_id, "action": action, "undo_available": False}
        rec = self.get_recommendation(recommendation_id)
        candidate_root = str(rec.get("candidateRootId") or recommendation_id)
        if candidate_root.startswith("candidate-") and action in {"later", "tomorrow", "weekend", "favorite", "not_interested"}:
            cooldown = (date.today() + timedelta(days=7)).isoformat() if action in {"later", "not_interested"} else None
            self.store.curriculum_action(candidate_root, action, cooldown)
        details = {**details, "domain": rec.get("domain", ""), "title": rec.get("title", "")}
        recommendations.record_action(self.store, recommendation_id, action, details)
        self.log("recommendation.action", {"recommendation_id": recommendation_id, "action": action})
        return {"recommendation_id": recommendation_id, "action": action, "undo_available": True}

    def dashboard(self) -> dict[str, Any]:
        recs = self.list_recommendations(); jobs = self.list_jobs(); prepared = self.list_prepared(); reviews = self.list_reviews()
        active = [job for job in jobs if job["state"] in {"queued", "running", "prepared", "awaiting_confirmation", "applying"}]
        failed = [job for job in jobs if job["state"] == "failed"]
        return {
            "date": date.today().isoformat(), "recommendations": recs,
            "summary": {
                "suggested_minutes": sum(int(item["estimatedMinutes"]) for item in recs[:3]),
                "completed_minutes": 0,
                "review_count": sum(item["kind"] == "review" for item in recs),
                "learn_count": sum(item["kind"] in {"learn", "explore"} for item in recs),
                "prepared_count": sum(item.get("state") == "prepared" for item in prepared),
                "review_count_pending": sum(item.get("review_state") == "pending" for item in reviews),
                "failed_count": len(failed), "active_job_count": len(active),
            },
            "active_jobs": active[:3], "failed_jobs": failed[:3],
        }

    def current_plan(self) -> dict[str, Any]:
        feedback = self.store.recommendation_feedback(); weekend: list[dict[str, Any]] = []; tomorrow: list[dict[str, Any]] = []
        for row in feedback:
            if row["action"] not in {"weekend", "tomorrow"}: continue
            details = json.loads(row["details_json"] or "{}")
            target = weekend if row["action"] == "weekend" else tomorrow
            if not any(item["recommendation_id"] == row["recommendation_id"] for item in target): target.append({"recommendation_id": row["recommendation_id"], "title": details.get("title", "学习任务"), "domain": details.get("domain", "")})
        weekly: list[dict[str, Any]] = []
        root = self.vault / "30-Learning/Weekly"
        if root.exists():
            for path in sorted(root.glob("*.md"), reverse=True)[:4]:
                meta = ingest_pdf.parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                weekly.append({"title": path.stem, "path": str(path.relative_to(self.vault)), "status": str(meta.get("status", ""))})
        return {"tomorrow": tomorrow, "weekend": weekend, "weekly": weekly, "proposals": self.store.list_plan_proposals(), "mainline_ratio": 70, "branch_ratio": 30}

    def patch_plan_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        allowed = {"date", "minutes", "state", "route", "position"}
        patch = {key: value for key, value in body.items() if key in allowed}
        if not patch:
            raise ValueError("plan_task_patch_required")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.store.lock:
            rows = self.store.connection.execute("SELECT id, tasks_json FROM plan_proposals").fetchall()
            found = None
            for row in rows:
                tasks = json.loads(row["tasks_json"])
                for task in tasks:
                    if str(task.get("id")) == task_id:
                        task.update(patch); found = dict(task)
                        break
                if found:
                    if "position" in patch:
                        tasks.sort(key=lambda item: (int(item.get("position", 10_000)), str(item.get("date", ""))))
                    self.store.connection.execute("UPDATE plan_proposals SET tasks_json=?, updated_at=? WHERE id=?", (json.dumps(tasks, ensure_ascii=False), now, row["id"]))
                    self.store.connection.commit(); break
        if not found:
            raise ValueError("plan_task_not_found")
        self.log("plan-task.updated", {"task_id": task_id, "fields": sorted(patch)})
        return found

    def list_model_profiles(self) -> list[dict[str, Any]]: return self.models.list()
    def save_model_profile(self, data: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        profile = self.models.save(data, profile_id); self.log("model-profile.saved", {"profile_id": profile["id"], "configured": profile["configured"]}); return profile
    def delete_model_profile(self, profile_id: str) -> dict[str, Any]:
        self.models.delete(profile_id); self.log("model-profile.deleted", {"profile_id": profile_id}); return {"id": profile_id, "deleted": True}
    def test_model_profile(self, profile_id: str) -> dict[str, Any]: return self.models.test(profile_id)
    def list_profile_models(self, profile_id: str) -> list[str]: return self.models.models(profile_id)
    def model_routing(self) -> dict[str, dict[str, str | None]]: return self.models.routing()
    def set_model_routing(self, routes: dict[str, dict[str, Any]]) -> dict[str, dict[str, str | None]]: return self.models.set_routing(routes)

    def model_capabilities(self, profile_id: str = "") -> dict[str, Any]:
        return self.model_proxy.capabilities(profile_id)

    def probe_model_capabilities(self, profile_id: str) -> dict[str, Any]:
        return self.model_proxy.probe(profile_id)

    def stream_model_proxy(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        return self.model_proxy.stream(body)

    def tool_contracts(self) -> dict[str, Any]:
        # Pi receives a stable, provider-neutral tool surface. Legacy proposal
        # tools stay available to the legacy runtime but are not exposed here.
        permissions = {"read_only"}
        items = [
            item
            for item in self.tools.definitions()
            if str(item.get("permission_level") or "") in permissions
        ]
        markdown_write = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "minLength": 1, "maxLength": 500},
                "content": {"type": "string", "minLength": 1, "maxLength": 100000},
                "category": {"type": "string", "maxLength": 80},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
        common = {
            "output_schema": {"type": "object", "additionalProperties": True},
            "uses_network": False,
            "mutates_state": True,
            "timeout_seconds": 30,
            "permission_level": "proposal",
            "cancellable": False,
            "max_result_bytes": 32000,
        }
        items.extend([
            {
                "name": "get_current_note",
                "description": "读取当前 Turn 明确提供的活动笔记；path 必须来自 zhixu_turn_context.currentNote。",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 32000,
            },
            {
                "name": "read_vault_note",
                "description": "分页读取搜索结果中的安全 Markdown 笔记正文；长笔记按 next_offset 继续读取，修改或完整复制前必须读到 truncated=false。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "minLength": 1, "maxLength": 500},
                        "offset": {"type": "integer", "minimum": 0, "maximum": 10_000_000},
                        "max_chars": {"type": "integer", "minimum": 100, "maximum": 50_000},
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 200000,
            },
            {
                "name": "find_related_notes",
                "description": "读取指定笔记的出链、反向链接和图谱相关笔记。",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 500}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": False,
                "mutates_state": False,
                "timeout_seconds": 20,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": False,
                "max_result_bytes": 32000,
            },
            {
                "name": "search_public_web",
                "description": "在当前 Turn 已授权联网时搜索公开网页；结果只是待验证外部资料。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 500},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": True,
                "mutates_state": False,
                "timeout_seconds": 45,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": True,
                "max_result_bytes": 32000,
            },
            {
                "name": "fetch_public_url",
                "description": "在当前 Turn 已授权联网时安全抓取一个公开 URL；正文以不可信证据边界返回。",
                "input_schema": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "minLength": 8, "maxLength": 2000}},
                    "required": ["url"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object", "additionalProperties": True},
                "uses_network": True,
                "mutates_state": False,
                "timeout_seconds": 45,
                "permission_level": "read_only",
                "idempotent": True,
                "cancellable": True,
                "max_result_bytes": 48000,
            },
            {
                **common,
                "name": "plan_vault_change",
                "description": "为当前任务生成内部 Markdown 写入计划和事后 Diff；不会等待人工审批。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "writes": {"type": "array", "minItems": 1, "maxItems": 10, "items": markdown_write},
                    },
                    "required": ["title", "writes"],
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "plan_vault_copy",
                "description": "把已有安全 Markdown 笔记完整复制到 01-Inbox 或 20-Knowledge/Drafts 下的新目录，并生成一个或多个内部 Change Set。正文由 Harness 本地读取，禁止模型先读取再把全文塞进工具参数。返回 change_sets 后逐个调用 apply_vault_change。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "source_paths": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "destination_root": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "required": ["title", "source_paths", "destination_root"],
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "organize_vault_notes",
                "description": (
                    "在当前 Task Authorization 内批量创建安全 Vault 子目录并移动普通 Markdown 笔记；"
                    "自动创建目标父目录，整批事务化、失败完整回滚且可撤销。"
                    "reviewed/core、受保护文件和 Vault 外路径永远禁止移动；"
                    "10-Sources 仅允许在明确授权后整理非正式 source-index 草稿。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 200},
                        "directories": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {"type": "string", "minLength": 1, "maxLength": 500},
                        },
                        "moves": {
                            "type": "array",
                            "maxItems": 50,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "source_path": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "target_path": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "destination_path": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 500,
                                        "description": "已弃用别名；新调用使用 target_path",
                                    },
                                },
                                "required": ["source_path"],
                                "anyOf": [
                                    {"required": ["target_path"]},
                                    {"required": ["destination_path"]},
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "remove_empty_source_dirs": {"type": "boolean", "default": False},
                    },
                    "additionalProperties": False,
                },
                "idempotent": False,
            },
            {
                **common,
                "name": "apply_vault_change",
                "description": "在当前 Task Authorization 内快照、原子应用并校验内部 Change Set；返回可撤销 Action。",
                "input_schema": {
                    "type": "object",
                    "properties": {"change_set_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    "required": ["change_set_id"],
                    "additionalProperties": False,
                },
                "idempotent": True,
            },
            {
                **common,
                "name": "undo_agent_action",
                "description": "冲突安全地撤销一个由当前 Harness 完成的可逆 Vault Action。",
                "input_schema": {
                    "type": "object",
                    "properties": {"action_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    "required": ["action_id"],
                    "additionalProperties": False,
                },
                "idempotent": True,
            },
        ])
        developer_common = {
            "output_schema": {"type": "object", "additionalProperties": True},
            "uses_network": False,
            "mutates_state": True,
            "timeout_seconds": 310,
            "permission_level": "proposal",
            "idempotent": False,
            "cancellable": True,
            "max_result_bytes": 64000,
        }
        workspace_id = {"type": "string", "minLength": 1, "maxLength": 128}
        run_payload = {
            "type": "object",
            "properties": {
                "workspace_id": workspace_id,
                "cwd": {"type": "string", "maxLength": 500, "default": "."},
                "timeoutMs": {"type": "integer", "minimum": 100, "maximum": 300000},
                "networkPolicy": {"type": "string", "enum": ["deny"]},
                "expectedOutputs": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 500}},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        }
        items.extend([
            {**developer_common, "name": "create_git_worktree", "description": "为当前开发任务创建隔离的 Git worktree 和任务分支。", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "read_workspace_file", "description": "读取当前受控开发 worktree 内的 UTF-8 文件。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "name": "write_workspace_file", "description": "在当前受控 worktree 内原子写入文件，可带 base hash 防止覆盖并发修改。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}, "content": {"type": "string", "maxLength": 1000000}, "base_hash": {"type": "string", "maxLength": 128}}, "required": ["workspace_id", "path", "content"], "additionalProperties": False}},
            {**developer_common, "name": "run_command", "description": "用结构化 argv 在 macOS sandbox 内运行白名单开发命令；默认断网且不继承密钥。", "input_schema": {**run_payload, "properties": {**run_payload["properties"], "executable": {"type": "string", "enum": ["git", "npm", "node", "python", "python3", "pytest", "rg", "ls", "find", "make"]}, "args": {"type": "array", "maxItems": 100, "items": {"type": "string", "maxLength": 4000}}}, "required": ["workspace_id", "executable", "args"]}},
            {**developer_common, "name": "run_bash", "description": "仅在当前 worktree 内运行受 macOS sandbox 和永久拒绝列表约束的 zsh 脚本。优先使用 run_command。", "input_schema": {**run_payload, "properties": {**run_payload["properties"], "script": {"type": "string", "minLength": 1, "maxLength": 40000}, "riskExplanation": {"type": "string", "maxLength": 2000}}, "required": ["workspace_id", "script", "riskExplanation"]}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "git_status", "description": "查看受控任务分支状态。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "git_diff", "description": "查看受控任务分支未提交 Diff。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "git_commit", "description": "在受控任务分支创建本地提交，不推送网络。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "message": {"type": "string", "minLength": 1, "maxLength": 200}}, "required": ["workspace_id", "message"], "additionalProperties": False}},
            {**developer_common, "name": "merge_task_branch", "description": "仅在主项目和任务分支都干净时，将已提交任务分支合并到当前项目；失败自动 abort。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "activate_runtime_upgrade", "description": "验证已合并任务并请求 Obsidian 进程管理器执行固定检查、构建、安装、Runtime 重启和健康检查；失败时自动调用 rollback_task_branch。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "name": "rollback_task_branch", "description": "删除当前任务 worktree 和任务分支，撤销未合并的开发任务。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id}, "required": ["workspace_id"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "validate_skill_draft", "description": "校验受控 worktree 中的 Skill Draft，不启用、不读取密钥。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": True, "name": "validate_mcp_server", "description": "校验受控 worktree 中的 MCP manifest 与隔离权限。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
            {**developer_common, "mutates_state": False, "idempotent": False, "name": "probe_mcp_server", "description": "在断网、最小环境的 sandbox 中临时启动 MCP 并执行 initialize 与 tools/list。", "input_schema": {"type": "object", "properties": {"workspace_id": workspace_id, "path": {"type": "string", "minLength": 1, "maxLength": 500}}, "required": ["workspace_id", "path"], "additionalProperties": False}},
        ])
        return {"schemaVersion": 1, "items": items}

    def register_task_authorization(self, body: dict[str, Any]) -> dict[str, Any]:
        authorization = dict(body.get("taskAuthorization") or {})
        authorization["conversationId"] = str(
            body.get("conversationId") or authorization.get("sessionId") or ""
        )
        authorization["profileId"] = str(body.get("profileId") or "")
        authorization["model"] = str(body.get("model") or "")
        authorization["providerAdapterVersion"] = str(
            body.get("providerAdapterVersion") or self.model_proxy.version
        )
        created = self.task_authorizations.create(authorization)
        conversation_id = str(authorization.get("conversationId") or "")
        objective = str(authorization.get("objective") or "").strip()
        source_message_id = str(authorization.get("sourceMessageId") or "")
        if conversation_id and objective and source_message_id:
            self.intake.ensure_runtime_conversation(conversation_id, objective)
            self.intake.append_message(
                conversation_id,
                "user",
                objective,
                "pi-runtime",
                message_id=source_message_id,
            )
        return {"taskAuthorization": created}

    def expand_task_authorization(
        self,
        authorization_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply one typed, explicit grant to the same active Run."""
        run_id = str(body.get("runId") or "")
        mode = str(body.get("mode") or "once")
        writes = body.get("writes")
        organization = body.get("organization")
        capability = body.get("capability")
        if isinstance(capability, dict):
            if not isinstance(organization, dict) and isinstance(capability.get("organization"), dict):
                organization = capability.get("organization")
            if not isinstance(writes, list) and isinstance(capability.get("writes"), list):
                writes = capability.get("writes")
        if not run_id:
            raise ValueError("task_permission_request_invalid")
        capability_type = (
            str(capability.get("type") or "") if isinstance(capability, dict) else ""
        )
        # Typed capabilities must be dispatched before the generic writes list:
        # Pi intentionally sends writes=[] for non-Vault permission cards.
        if capability_type == "developer_workspace":
            workspace_id = str(capability.get("workspaceId") or "").strip()
            tool_name = str(capability.get("toolName") or "").strip()
            if not workspace_id or not tool_name:
                raise ValueError("developer_workspace_permission_invalid")
            # Never trust a client-supplied project path. The persisted record
            # proves Run ownership and that this is an isolated worktree.
            workspace = self.developer_workspace.get(workspace_id, run_id)
            self.task_authorizations.register_workspace(
                authorization_id,
                run_id,
                workspace_id,
                str(workspace["project"]),
            )
            result = self.task_authorizations.grant_workspace_operation(
                authorization_id,
                run_id,
                workspace_id,
                tool_name,
                mode,
            )
            result["capability"] = {
                "type": "developer_workspace",
                "workspaceId": workspace_id,
                "toolName": tool_name,
            }
        elif capability_type == "network":
            # Network scope is represented by networkPolicy on the Turn and is
            # established before model execution. Never misroute it to an empty
            # Vault write grant.
            raise ValueError("network_permission_requires_explicit_turn_policy")
        elif isinstance(organization, dict):
            result = self.task_authorizations.grant_organization(
                authorization_id, run_id, organization, mode,
            )
        elif isinstance(writes, list) and len(writes) <= 100:
            result = self.task_authorizations.grant_scope(
                authorization_id,
                run_id,
                [dict(item) for item in writes if isinstance(item, dict)],
                mode,
            )
        else:
            raise ValueError("task_permission_request_invalid")
        return {"taskAuthorization": result["authorization"], **result}

    @staticmethod
    def _runtime_permission_request(
        code: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        if code in {
            "task_scope_expansion_requires_new_user_turn",
            "task_create_root_requires_scope_expansion",
            "task_create_scope_required",
            "task_update_scope_required",
        }:
            if tool_name == "plan_vault_copy":
                destination_root = str(arguments.get("destination_root") or "").strip().strip("/")
                requested_writes = [
                    {"path": f"{destination_root}/{Path(str(source)).name}"}
                    for source in list(arguments.get("source_paths") or [])
                    if str(source).strip()
                ]
            else:
                requested_writes = [
                    {"path": str(item.get("path") or "")}
                    for item in list(arguments.get("writes") or [])
                    if isinstance(item, dict) and item.get("path")
                ]
            return {
                "type": "vault_writes",
                "toolName": tool_name,
                "summary": f"授权本轮写入 {len(requested_writes)} 个明确 Markdown 目标",
                "writes": requested_writes,
            }
        if code == "task_organization_scope_required" and tool_name == "organize_vault_notes":
            moves = [
                {
                    "source_path": str(item.get("source_path") or ""),
                    "target_path": str(
                        item.get("target_path") or item.get("destination_path") or ""
                    ),
                }
                for item in list(arguments.get("moves") or [])
                if isinstance(item, dict)
            ]
            summary = "; ".join(
                f"{item['source_path']} → {item['target_path']}" for item in moves
            )
            return {
                "type": "vault_organization",
                "toolName": "organize_vault_notes",
                "summary": summary or "创建受控 Vault 子目录",
                "organization": {
                    "directories": [str(item) for item in list(arguments.get("directories") or [])],
                    "moves": moves,
                    "remove_empty_source_dirs": arguments.get("remove_empty_source_dirs") is True,
                },
            }
        if code in {
            "developer_workspace_not_authorized",
            "developer_operation_not_authorized",
        }:
            return {
                "type": "developer_workspace",
                "toolName": tool_name,
                "workspaceId": str(arguments.get("workspace_id") or ""),
                "summary": f"授权当前 Run 在隔离 worktree 执行 {tool_name}",
            }
        return None

    def _auto_bind_and_plan(
        self,
        authorization_id: str,
        run_id: str,
        tool_call_id: str,
        writes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Plan writes, freezing the first safe plan when scope is unbound.

        Returns the planned dict. Re-raises PermissionError when the scope still
        cannot be satisfied, so a later expansion surfaces a permission card
        instead of being silently granted.
        """
        try:
            planned = self.task_authorizations.plan(authorization_id, writes)
        except PermissionError as scope_error:
            if str(scope_error) in {
                "task_create_scope_required",
                "task_update_scope_required",
            }:
                try:
                    self.task_authorizations.bind_initial_markdown_plan(
                        authorization_id, run_id, tool_call_id, writes,
                    )
                except PermissionError:
                    # Auto-bind refused (unsafe, already bound by another call,
                    # or protected). Keep the original scope error.
                    pass
                else:
                    return self.task_authorizations.plan(authorization_id, writes)
            raise
        # The plan is already permitted, but this is still the first safe write
        # plan: freeze the scope so later additions require a real permission card.
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["resourceScope"].get("writeScopeState") != "bound":
            try:
                self.task_authorizations.bind_initial_markdown_plan(
                    authorization_id, run_id, tool_call_id, writes,
                )
            except PermissionError:
                pass
        return planned

    def append_pi_events(self, body: dict[str, Any]) -> dict[str, Any]:
        run_id = str(body.get("runId") or "")
        events = body.get("events")
        if not run_id or not isinstance(events, list) or len(events) > 1000:
            raise ValueError("pi_events_invalid")
        last = self.store.append_pi_agent_events(run_id, events)
        terminal = next(
            (item for item in reversed(events) if item.get("type") in {"done", "error"}),
            None,
        )
        if terminal:
            status = (
                "completed"
                if terminal.get("type") == "done" and terminal.get("status") != "cancelled"
                else "cancelled" if terminal.get("status") == "cancelled" else "failed"
            )
            self.store.complete_pi_run(run_id, status, str(terminal.get("code") or ""))
            run = self.store.get_pi_run(run_id)
            text = "".join(
                str(item.get("content") or "")
                for item in self.store.list_pi_agent_events(run_id, 0, 5000)
                if item.get("type") == "text"
            ).strip()
            if text:
                session = self.store.get_pi_session(str(run["session_id"]))
                conversation_id = str(session.get("conversation_id") or run["session_id"])
                self.intake.append_message(
                    conversation_id,
                    "assistant",
                    text,
                    "pi-runtime",
                    message_id=f"pi-assistant-{run_id}",
                )
        return {"runId": run_id, "lastEventSequence": last}

    def pi_run_events(self, run_id: str, after_sequence: int = 0) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        return {
            "run": run,
            "items": self.store.list_pi_agent_events(run_id, after_sequence),
            "schemaVersion": 1,
        }

    def pi_session(self, session_id: str) -> dict[str, Any]:
        try:
            session: dict[str, Any] | None = self.store.get_pi_session(session_id)
            conversation_id = str(session.get("conversation_id") or session_id)
        except ValueError:
            session = None
            conversation_id = session_id
        try:
            history = self.intake.get_conversation(conversation_id).get("messages", [])
        except ValueError:
            history = []
        return {
            "session": session,
            "history": [
                {
                    "id": str(item.get("id") or ""),
                    "role": str(item.get("role") or ""),
                    "content": str(item.get("content") or ""),
                    "createdAt": str(item.get("createdAt") or ""),
                }
                for item in history
                if item.get("role") in {"user", "assistant"} and item.get("content")
            ],
            "schemaVersion": 2,
        }

    def pi_session_projection(
        self,
        session_id: str,
        *,
        leaf_id: str | None = None,
        run_id: str | None = None,
        upto_sequence: int | None = None,
    ) -> dict[str, Any]:
        return self.store.project_pi_session_context(
            session_id,
            leaf_id=leaf_id or None,
            upto_run_id=run_id or None,
            upto_sequence=upto_sequence,
        )

    def pi_fork_projection(
        self,
        run_id: str,
        *,
        sequence: int | None = None,
        mode: str = "fork",
    ) -> dict[str, Any]:
        return self.store.project_pi_fork_context(run_id, sequence, mode)

    _PENDING_TOOL_CALL_STATES = {
        "pending",
        "allowed",
        "denied",
        "cancelled",
        "interrupted",
        "completed",
    }

    def save_pending_tool_call(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        if not run:
            raise ValueError("pi_run_not_found")
        tool_call_id = str(body.get("toolCallId") or "").strip()
        tool_name = str(body.get("toolName") or "").strip()
        turn_id = str(body.get("turnId") or run.get("turn_id") or "").strip()
        session_id = str(body.get("sessionId") or run.get("session_id") or "").strip()
        task_authorization_id = str(body.get("taskAuthorizationId") or "").strip()
        state = str(body.get("state") or "pending").strip() or "pending"
        if not tool_call_id or not tool_name or not session_id:
            raise ValueError("pi_pending_tool_call_invalid")
        if state not in self._PENDING_TOOL_CALL_STATES:
            raise ValueError("pi_pending_tool_call_state_invalid")
        arguments = body.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        permission_request = body.get("permissionRequest")
        if not isinstance(permission_request, dict):
            permission_request = {}
        if session_id != str(run.get("session_id") or "") or turn_id != str(run.get("turn_id") or ""):
            raise ValueError("pi_pending_tool_call_identity_mismatch")
        authorization = self.store.get_task_authorization(task_authorization_id)
        if (
            authorization["status"] != "active"
            or authorization["runId"] != run_id
            or authorization["sessionId"] != session_id
            or authorization["turnId"] != turn_id
            or not authorization["reversibleOnly"]
            or authorization["externalSideEffects"]
        ):
            raise PermissionError("pi_pending_tool_call_authorization_invalid")
        contracts = {item["name"]: item for item in self.tool_contracts()["items"]}
        if tool_name not in contracts:
            raise ValueError("pi_pending_tool_contract_missing")
        vault_guard = self.task_authorizations.capture_pending_recovery_guard(
            tool_name, arguments, permission_request,
        )
        workspace_guard: dict[str, Any] | None = None
        workspace_id = str(arguments.get("workspace_id") or "").strip()
        if workspace_id:
            workspace_guard = self.developer_workspace.pending_recovery_guard(
                workspace_id,
                run_id,
                str(arguments.get("path") or ""),
                str(arguments.get("base_hash") or ""),
            )
        recovery_guard = {
            "version": 1,
            "toolName": tool_name,
            "vault": vault_guard,
            "workspace": workspace_guard,
        }
        self.store.save_pending_tool_call(
            run_id=run_id,
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            permission_request=permission_request,
            recovery_guard=recovery_guard,
            task_authorization_id=task_authorization_id,
            state=state,
        )
        return {"ok": True, "runId": run_id, "toolCallId": tool_call_id, "state": state}

    def validate_pending_tool_call_recovery(
        self,
        run_id: str,
        tool_call_id: str,
    ) -> dict[str, Any]:
        """Fail closed unless a paused call is still safe under its original Run."""
        records = self.store.get_pending_tool_calls(run_id=run_id, active_only=False)
        record = next((item for item in records if item["toolCallId"] == tool_call_id), None)
        if record is None:
            raise ValueError("pi_pending_tool_call_not_found")

        def invalid(code: str) -> dict[str, Any]:
            if record["state"] in {"pending", "interrupted"}:
                self.store.resolve_pending_tool_call(run_id, tool_call_id, "interrupted")
            return {
                "recoverable": False,
                "resolved": True,
                "code": str(code or "pi_pending_recovery_invalid")[:200],
                "runId": run_id,
                "toolCallId": tool_call_id,
            }

        try:
            if record["state"] not in {"pending", "interrupted"}:
                return invalid("pi_pending_tool_call_already_resolved")
            if self.store.has_pi_tool_result(run_id, tool_call_id, record["toolName"]):
                self.store.resolve_pending_tool_call(run_id, tool_call_id, "completed")
                return {
                    "recoverable": False,
                    "resolved": True,
                    "code": "pi_pending_tool_call_already_completed",
                    "runId": run_id,
                    "toolCallId": tool_call_id,
                }
            run = self.store.get_pi_run(run_id)
            if str(run.get("status") or "") != "running":
                return invalid("pi_pending_run_not_active")
            if (
                record["sessionId"] != str(run.get("session_id") or "")
                or record["turnId"] != str(run.get("turn_id") or "")
            ):
                return invalid("pi_pending_run_identity_mismatch")
            authorization = self.store.get_task_authorization(record["taskAuthorizationId"])
            if authorization["status"] != "active":
                return invalid("task_authorization_expired")
            if (
                authorization["runId"] != run_id
                or authorization["sessionId"] != record["sessionId"]
                or authorization["turnId"] != record["turnId"]
            ):
                return invalid("task_authorization_run_mismatch")
            if not authorization["reversibleOnly"] or authorization["externalSideEffects"]:
                return invalid("task_authorization_not_reversible")
            contracts = {item["name"]: item for item in self.tool_contracts()["items"]}
            contract = contracts.get(record["toolName"])
            if not contract:
                return invalid("pi_pending_tool_contract_missing")
            if contract.get("permission_level") != "proposal" or contract.get("uses_network") is True:
                return invalid("pi_pending_operation_not_reversible")
            guard = record.get("recoveryGuard")
            if not isinstance(guard, dict) or guard.get("version") != 1:
                return invalid("pi_pending_recovery_guard_missing")
            current_vault = self.task_authorizations.capture_pending_recovery_guard(
                record["toolName"], record["arguments"], record["permissionRequest"],
            )
            current_workspace: dict[str, Any] | None = None
            workspace_id = str(record["arguments"].get("workspace_id") or "").strip()
            if workspace_id:
                current_workspace = self.developer_workspace.pending_recovery_guard(
                    workspace_id,
                    run_id,
                    str(record["arguments"].get("path") or ""),
                    str(record["arguments"].get("base_hash") or ""),
                )
            current_guard = {
                "version": 1,
                "toolName": record["toolName"],
                "vault": current_vault,
                "workspace": current_workspace,
            }
            if current_guard != guard:
                return invalid("pi_pending_recovery_state_changed")
            if record["toolName"] == "apply_vault_change":
                self.brain_change_sets.validate({
                    "change_set_id": str(record["arguments"].get("change_set_id") or ""),
                })
            elif record["toolName"] == "undo_agent_action":
                action = self.store.get_agent_action(
                    str(record["arguments"].get("action_id") or "")
                )
                if str(action.get("status") or "") != "completed":
                    return invalid("agent_action_not_undoable")
            return {
                "recoverable": True,
                "resolved": False,
                "record": record,
                "taskAuthorization": authorization,
                "lastEventSequence": int(run.get("last_event_sequence") or 0),
                "schemaVersion": 1,
            }
        except (ValueError, RuntimeError, PermissionError, FileNotFoundError, FileExistsError) as exc:
            return invalid(str(exc) or exc.__class__.__name__)

    def get_pending_tool_calls(
        self,
        run_id: str | None = None,
        session_id: str | None = None,
        active_only: bool = True,
    ) -> dict[str, Any]:
        if run_id is not None:
            run = self.store.get_pi_run(run_id)
            if not run:
                raise ValueError("pi_run_not_found")
        items = self.store.get_pending_tool_calls(
            run_id=run_id,
            session_id=session_id,
            active_only=active_only,
        )
        return {"items": items}

    def resolve_pending_tool_call(self, run_id: str, tool_call_id: str, state: str) -> dict[str, Any]:
        state = str(state or "").strip()
        if state not in self._PENDING_TOOL_CALL_STATES:
            raise ValueError("pi_pending_tool_call_state_invalid")
        self.store.resolve_pending_tool_call(run_id, tool_call_id, state)
        return {"ok": True, "runId": run_id, "toolCallId": tool_call_id, "state": state}

    def save_compaction_checkpoint(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        if not run:
            raise ValueError("pi_run_not_found")
        entry = body.get("entry")
        if not isinstance(entry, dict):
            raise ValueError("pi_compaction_entry_invalid")
        session_id = str(body.get("sessionId") or run.get("session_id") or "").strip()
        branch_id = str(body.get("branchId") or run.get("run_id") or "").strip()
        if not session_id:
            raise ValueError("pi_compaction_session_invalid")
        summary = str(body.get("summary") or "")
        if not summary:
            raise ValueError("pi_compaction_summary_invalid")
        checkpoint = self.store.save_compaction_checkpoint(
            run_id=run_id,
            session_id=session_id,
            branch_id=branch_id,
            entry=entry,
            summary=summary,
        )
        return {"ok": True, "runId": run_id, "checkpoint": checkpoint}

    def get_compaction_checkpoint(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        if not run:
            raise ValueError("pi_run_not_found")
        record = self.store.get_compaction_checkpoint(run_id)
        return {"entry": record}

    def update_message_metadata(
        self, conversation_id: str, message_id: str, metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return self.intake.update_message_metadata(conversation_id, message_id, metadata)

    def control_pi_run(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        control_type = str(body.get("type") or "")
        text = str(body.get("text") or "").strip()
        if control_type not in {"steering", "follow_up"} or not text or len(text) > 20_000:
            raise ValueError("pi_control_invalid")
        run = self.store.get_pi_run(run_id)
        if str(run.get("status") or "") != "running":
            raise RuntimeError("pi_run_not_running")
        entry = self.store.record_pi_control(run_id, control_type, {"text": text})
        return {"control": entry}

    def cancel_pi_run(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_pi_run(run_id)
        if str(run.get("status") or "") not in {"completed", "failed", "cancelled"}:
            self.store.complete_pi_run(run_id, "cancelled", "user_cancelled")
        return {"runId": run_id, "status": "cancelled"}

    def get_agent_action(self, action_id: str) -> dict[str, Any]:
        return {"action": self.reversible_transactions.get(action_id)}

    def get_agent_action_diff(self, action_id: str) -> dict[str, Any]:
        return self.reversible_transactions.diff(action_id)

    def undo_agent_action(self, action_id: str) -> dict[str, Any]:
        # This method is reached only from the authenticated, explicit UI undo
        # endpoint. Model tool calls use the strict Run-bound branch below.
        return {
            "action": self.reversible_transactions.undo(
                action_id,
                user_confirmed=True,
            )
        }

    def call_runtime_tool(self, body: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(body.get("toolName") or "")
        arguments = body.get("arguments")
        if not tool_name or not isinstance(arguments, dict):
            raise ValueError("runtime_tool_request_invalid")
        run_id = str(body.get("runId") or "")
        turn_id = str(body.get("turnId") or "")
        call_id = str(body.get("toolCallId") or "")
        if not run_id or not turn_id or not call_id:
            raise ValueError("runtime_tool_identity_required")
        stable_writes = {
            "plan_vault_change", "plan_vault_copy", "organize_vault_notes",
            "apply_vault_change", "undo_agent_action",
        }
        stable_reads = {
            "get_current_note", "read_vault_note", "find_related_notes",
            "search_public_web", "fetch_public_url",
        }
        developer_tools = {
            "create_git_worktree", "read_workspace_file", "write_workspace_file",
            "run_command", "run_bash", "git_status", "git_diff", "git_commit",
            "merge_task_branch", "activate_runtime_upgrade", "rollback_task_branch", "validate_skill_draft", "validate_mcp_server",
            "probe_mcp_server",
        }
        definition = None if tool_name in stable_writes | stable_reads | developer_tools else self.tools.definition(tool_name)
        uses_network = tool_name in {"search_public_web", "fetch_public_url"} or bool(definition and definition.uses_network)
        if uses_network and body.get("networkAuthorized") is not True:
            return {
                "ok": False,
                "error": {
                    "code": "network_authorization_required",
                    "message": "当前任务未授权联网",
                    "recoverable": True,
                    "retryable": False,
                },
                "isError": True,
            }
        try:
            authorization_id = str(body.get("taskAuthorizationId") or "")
            if tool_name == "plan_vault_change":
                result = self.brain_change_sets.create({
                    "run_id": run_id,
                    "task_authorization_id": authorization_id,
                    "title": str(arguments.get("title") or "Agent 写入计划"),
                    "writes": list(arguments.get("writes") or []),
                })
                _, bundle = self.brain_change_sets.prepared(str(result["id"]))
                try:
                    planned = self._auto_bind_and_plan(
                        authorization_id,
                        run_id,
                        call_id,
                        list(bundle["writes"]),
                    )
                except Exception:
                    self.brain_change_sets.update_state(str(result["id"]), "failed")
                    raise
                result = {
                    **result,
                    "authorization": planned["authorization"],
                    "diff": self.brain_change_sets.diff(str(result["id"])),
                }
            elif tool_name == "plan_vault_copy":
                destination_root = str(arguments.get("destination_root") or "").strip().strip("/")
                if not (
                    destination_root == "01-Inbox"
                    or destination_root.startswith("01-Inbox/")
                    or destination_root == "20-Knowledge/Drafts"
                    or destination_root.startswith("20-Knowledge/Drafts/")
                ):
                    raise PermissionError("vault_copy_destination_requires_draft_root")
                writes: list[dict[str, Any]] = []
                seen_targets: set[str] = set()
                for raw_source in list(arguments.get("source_paths") or []):
                    source = safe_read_note(self.vault, str(raw_source))
                    if not source.is_file():
                        raise FileNotFoundError("copy_source_not_found")
                    source_text = read_model_visible_note(source)
                    target = f"{destination_root}/{source.name}"
                    if target in seen_targets:
                        raise ValueError("copy_target_collision")
                    seen_targets.add(target)
                    writes.append({
                        "path": target,
                        "content": source_text,
                        "category": "vault-copy",
                    })
                change_sets: list[dict[str, Any]] = []
                title = str(arguments.get("title") or "复制 Vault 笔记")
                for batch_index in range(0, len(writes), 10):
                    batch = writes[batch_index:batch_index + 10]
                    planned = self.brain_change_sets.create({
                        "run_id": run_id,
                        "task_authorization_id": authorization_id,
                        "title": title if len(writes) <= 10 else f"{title}（{batch_index // 10 + 1}）",
                        "writes": batch,
                    })
                    _, bundle = self.brain_change_sets.prepared(str(planned["id"]))
                    try:
                        authorization = self._auto_bind_and_plan(
                            authorization_id,
                            run_id,
                            call_id,
                            list(bundle["writes"]),
                        )
                    except Exception:
                        self.brain_change_sets.update_state(str(planned["id"]), "failed")
                        raise
                    # Keep the full authenticated Diff in the Change Set store.
                    # Returning every copied body/Diff to the model would defeat
                    # this server-side batch tool and can exhaust the transport.
                    change_sets.append({
                        "id": planned["id"],
                        "state": planned["state"],
                        "title": planned["title"],
                        "files": [item["path"] for item in planned["writes"]],
                        "fileCount": len(planned["writes"]),
                    })
                result = {
                    "sourceCount": len(writes),
                    "destinationRoot": destination_root,
                    "change_sets": change_sets,
                    "next": "逐个调用 apply_vault_change，并传入每个 change_set 的 id",
                }
            elif tool_name == "apply_vault_change":
                result = self.reversible_transactions.apply_change_set(
                    self.brain_change_sets,
                    {
                        "change_set_id": str(arguments.get("change_set_id") or ""),
                        "task_authorization_id": authorization_id,
                        "turn_id": turn_id,
                        "source_message_id": str(body.get("sourceMessageId") or ""),
                    },
                )
            elif tool_name == "organize_vault_notes":
                result = self.reversible_transactions.organize({
                    "task_authorization_id": authorization_id,
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "source_message_id": str(body.get("sourceMessageId") or ""),
                    "title": str(arguments.get("title") or "整理 Vault 笔记"),
                    "directories": list(arguments.get("directories") or []),
                    "moves": list(arguments.get("moves") or []),
                    "remove_empty_source_dirs": arguments.get("remove_empty_source_dirs") is True,
                })
            elif tool_name == "undo_agent_action":
                result = self.reversible_transactions.undo(
                    str(arguments.get("action_id") or ""),
                    authorization_id=authorization_id,
                    run_id=run_id,
                )
            elif tool_name in {"get_current_note", "read_vault_note"}:
                result = self.tools.call(
                    "read_note_excerpt",
                    {
                        "path": str(arguments.get("path") or ""),
                        "offset": max(0, int(arguments.get("offset") or 0)),
                        "max_chars": max(100, min(50_000, int(arguments.get("max_chars") or 12_000))),
                    },
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    record_event=False,
                )
            elif tool_name == "find_related_notes":
                result = self.tools.call(
                    "get_related_notes",
                    {"path": str(arguments.get("path") or "")},
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    record_event=False,
                )
            elif tool_name == "search_public_web":
                result = self.web.search(
                    str(arguments.get("query") or ""),
                    max(1, min(10, int(arguments.get("limit") or 8))),
                )
            elif tool_name == "fetch_public_url":
                result = self.web.fetch_for_model(str(arguments.get("url") or ""))
            elif tool_name == "create_git_worktree":
                result = self.developer_workspace.create(run_id)
                self.task_authorizations.register_workspace(
                    authorization_id, run_id, str(result["id"]), str(result["project"]),
                )
            elif tool_name in developer_tools:
                workspace_id_value = str(arguments.get("workspace_id") or "")
                self.task_authorizations.validate_workspace(
                    authorization_id, run_id, workspace_id_value, tool_name,
                )
                if tool_name == "read_workspace_file":
                    result = self.developer_workspace.read(workspace_id_value, run_id, str(arguments.get("path") or ""))
                elif tool_name == "write_workspace_file":
                    result = self.developer_workspace.write(workspace_id_value, run_id, str(arguments.get("path") or ""), str(arguments.get("content") or ""), str(arguments.get("base_hash") or ""))
                elif tool_name == "run_command":
                    result = self.developer_workspace.run_command(workspace_id_value, run_id, arguments)
                elif tool_name == "run_bash":
                    result = self.developer_workspace.run_bash(workspace_id_value, run_id, arguments)
                elif tool_name == "git_status":
                    result = self.developer_workspace.git_status(workspace_id_value, run_id)
                elif tool_name == "git_diff":
                    result = self.developer_workspace.git_diff(workspace_id_value, run_id)
                elif tool_name == "git_commit":
                    result = self.developer_workspace.git_commit(workspace_id_value, run_id, str(arguments.get("message") or ""))
                elif tool_name == "merge_task_branch":
                    result = self.developer_workspace.merge(workspace_id_value, run_id)
                elif tool_name == "activate_runtime_upgrade":
                    result = self.developer_workspace.activation_request(workspace_id_value, run_id)
                elif tool_name == "rollback_task_branch":
                    result = self.developer_workspace.rollback(workspace_id_value, run_id)
                elif tool_name == "validate_skill_draft":
                    result = self.developer_workspace.validate_skill(workspace_id_value, run_id, str(arguments.get("path") or ""))
                elif tool_name == "validate_mcp_server":
                    result = self.developer_workspace.validate_mcp(workspace_id_value, run_id, str(arguments.get("path") or ""))
                else:
                    result = self.developer_workspace.probe_mcp(workspace_id_value, run_id, str(arguments.get("path") or ""))
            else:
                result = self.tools.call(
                    tool_name,
                    arguments,
                    run_id=run_id,
                    step_id=f"{turn_id}:{call_id}",
                    allowed_permissions=("read_only",),
                    # Pi lifecycle events are persisted in the assistant runtime
                    # event store. The legacy tool_events table is FK-bound to
                    # brain_runs and must not receive Pi run identifiers.
                    record_event=False,
                )
            return {
                "ok": True,
                "content": result,
                "isError": False,
                "recoverable": False,
                "action": result if tool_name in {
                    "organize_vault_notes", "apply_vault_change", "undo_agent_action",
                } else None,
            }
        except (ValueError, FileNotFoundError, FileExistsError, PermissionError, RuntimeError) as exc:
            code = str(exc)[:160] or type(exc).__name__
            permission_request = self._runtime_permission_request(code, tool_name, arguments)
            return {
                "ok": False,
                "error": {
                    "code": code,
                    "message": "工具未能完成；请根据该 Observation 调整步骤",
                    "recoverable": permission_request is not None or not isinstance(exc, PermissionError),
                    "retryable": isinstance(exc, (FileNotFoundError, RuntimeError)),
                    **({"permissionRequest": permission_request} if permission_request else {}),
                },
                "isError": True,
            }

    def chat(self, body: dict[str, Any], stream: bool = False) -> dict[str, Any]:
        routes = self.model_routing(); route = routes.get("assistant_chat") or routes.get("assistant", {})
        profile_id = str(body.get("profile_id") or route.get("profileId") or "")
        if not profile_id: raise RuntimeError("No assistant model profile is configured")
        profile = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        if not profile or not profile["enabled"]: raise RuntimeError("Assistant model profile is unavailable")
        messages = body.get("messages", [])
        if not isinstance(messages, list) or not messages or len(messages) > 100: raise ValueError("messages must contain 1–100 entries")
        clean: list[dict[str, str]] = []
        for item in messages:
            role, content = str(item.get("role", "")), str(item.get("content", ""))
            if role not in {"system", "user", "assistant"} or not content or len(content) > 100_000: raise ValueError("Invalid chat message")
            clean.append({"role": role, "content": content})
        settings = profile["settings"]; model = str(body.get("model") or route.get("modelOverride") or profile["defaultModel"])
        provider = self.models.provider(profile_id)
        if stream:
            chunks: list[str] = []
            for event in provider.stream_chat(model, clean, temperature=settings.get("temperature", .3), max_tokens=settings.get("maxTokens", 2000)):
                if event.get("type") == "delta": chunks.append(str(event.get("content") or ""))
            result = {"choices": [{"message": {"content": "".join(chunks)}}]}
        else:
            result = provider.chat(model, clean, temperature=settings.get("temperature", .3), max_tokens=settings.get("maxTokens", 2000))
        content = str((((result.get("choices") or [{}])[0].get("message") or {}).get("content")) or "")
        conversation_id = str(body.get("conversation_id") or uuid.uuid4().hex)
        if len(conversation_id) > 128 or not conversation_id.replace("-", "").isalnum(): raise ValueError("Invalid conversation id")
        root = self.vault / "90-Local-Only/Agent/Conversations"; root.mkdir(parents=True, exist_ok=True)
        record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "profile_id": profile_id, "model": model, "messages": clean, "assistant": content}
        with (root / f"{conversation_id}.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.log("chat.completed", {"conversation_id": conversation_id, "profile_id": profile_id, "model": model, "message_count": len(clean)})
        return {"conversation_id": conversation_id, "model": model, "message": {"role": "assistant", "content": content}, "streaming": False, "stream_requested": stream}

    def _assistant_grounding_text(self, attachments: list[dict[str, Any]], active_note: dict[str, Any]) -> str:
        sections: list[str] = []
        note_path = str(active_note.get("path") or "")
        if note_path:
            path = (self.vault / note_path).resolve()
            if path.is_relative_to(self.vault) and path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".md":
                selection = str(active_note.get("selection") or "").strip()
                text = selection or path.read_text(encoding="utf-8", errors="replace")
                sections.append(f"[当前笔记 {note_path}]\n{text[:8_000]}")
        for attachment in attachments[:10]:
            try:
                path = self.intake.resolve_attachment_path(str(attachment["id"]))
                if attachment.get("kind") == "pdf":
                    pages = ingest_pdf.extract_pages(path)
                    text = "\n".join(pages)[:16_000]
                elif path.is_file() and path.stat().st_size <= 5 * 1024 * 1024:
                    text = path.read_text(encoding="utf-8", errors="replace")[:12_000]
                else:
                    text = ""
                if text:
                    sections.append(f"[附件 {attachment.get('displayName')}]\n{text}")
            except (OSError, UnicodeError, RuntimeError, ValueError):
                continue
        return "\n\n".join(sections)[:24_000]

    def start_study_session(self, recommendation_id: str) -> dict[str, Any]:
        rec = self.get_recommendation(recommendation_id)
        if rec["kind"] == "source": raise RuntimeError("Source recommendations must be reviewed before study")
        existing = self.store.resumable_study_session(recommendation_id)
        if existing:
            self.log("study.resumed", {"recommendation_id": recommendation_id, "session_id": existing["session_id"]})
            return {**study_workspace.public_session(existing), "recommendation": rec, "resumed": True}
        lesson = study_workspace.lesson_blueprint(rec)
        session_id = self.store.create_study_session(recommendation_id, {
            "title": rec["title"], "estimated_minutes": rec["estimatedMinutes"], "lesson": lesson, "recommendation": rec,
            "progress": {"sectionIndex": 0, "completedSectionIds": [], "quizAnswers": {}, "notes": ""},
        })
        self.store.set_daily_recommendation_state(date.today().isoformat(), recommendation_id, "in_progress")
        self.log("study.started", {"recommendation_id": recommendation_id, "session_id": session_id})
        return {**study_workspace.public_session(self.store.get_study_session(session_id)), "recommendation": rec, "resumed": False}

    def get_study_session(self, session_id: str) -> dict[str, Any]:
        return study_workspace.public_session(self.store.get_study_session(session_id))

    def update_study_session(self, session_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        states = {"pause": "paused", "resume": "active", "open_quiz": "quiz", "leave_quiz": "active", "progress": "active", "abandon": "abandoned", "retry": "active", "undo_complete": "active"}
        if action not in states: raise ValueError("unsupported_study_action")
        current = self.store.get_study_session(session_id)
        if action == "undo_complete" and current["state"] != "completed": raise ValueError("study_completion_not_undoable")
        if action != "undo_complete" and current["state"] == "completed": raise ValueError("completed_study_session_is_read_only")
        patch = {"progress": dict(body.get("progress") or {})}
        if body.get("error"): patch["last_error"] = str(body["error"])
        updated = self.store.update_study_session(session_id, states[action], patch)
        if action == "undo_complete":
            self.store.undo_recommendation_feedback(str(updated["recommendation_id"]))
            self.store.undo_recommendation_feedback(str(updated["recommendation_id"]))
        daily_state = {"pause": "paused", "resume": "in_progress", "open_quiz": "in_progress", "leave_quiz": "in_progress", "progress": "in_progress", "abandon": "skipped", "retry": "in_progress", "undo_complete": "in_progress"}[action]
        self.store.set_daily_recommendation_state(date.today().isoformat(), str(updated["recommendation_id"]), daily_state)
        self.log(f"study.{action.replace('_', '-')}", {"session_id": session_id, "recommendation_id": updated["recommendation_id"]})
        return study_workspace.public_session(updated)

    def complete_study_session(self, session_id: str, correctness: float, notes: str = "") -> dict[str, Any]:
        row = self.store.get_study_session(session_id)
        if row["state"] == "completed":
            details = row.get("details") or {}
            return {"session_id": session_id, "state": "completed", "suggested_mastery": int(details.get("suggested_mastery", 0)), "requires_confirmation": True, "path": details.get("path"), "next_review": details.get("next_review"), "review_scheduled": bool(details.get("review_scheduled", True)), "idempotent": True}
        rec = self.get_recommendation(str(row["recommendation_id"]))
        suggested = self.suggest_mastery(int(rec.get("mastery", 0)), correctness)
        next_review = (date.today() + timedelta(days=1 if correctness < .8 else 2)).isoformat()
        completed = self.store.complete_study_session(session_id, {"correctness": correctness, "notes": notes, "suggested_mastery": suggested, "path": rec.get("sourcePath"), "next_review": next_review, "review_scheduled": True})
        recommendations.record_action(self.store, str(row["recommendation_id"]), "completed", {"session_id": session_id})
        recommendations.record_action(self.store, str(row["recommendation_id"]), "tomorrow", {"title": rec.get("title", "学习内容"), "domain": rec.get("domain", ""), "scheduled_review": True, "next_review": next_review})
        self.store.set_daily_recommendation_state(date.today().isoformat(), str(row["recommendation_id"]), "completed")
        self.log("study.completed", {"recommendation_id": row["recommendation_id"], "session_id": session_id})
        return {"session_id": session_id, "state": completed["state"], "suggested_mastery": suggested, "requires_confirmation": True, "path": rec.get("sourcePath"), "next_review": next_review, "review_scheduled": True}
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
        self.store.update_job(job_id, "running", stage="running", progress=5)
        try:
            if kind == "prepare-pdf":
                payload["_progress"] = lambda stage, progress: self.store.update_job(job_id, "running", stage=stage, progress=progress)
            result = handlers[kind](payload)
            latest = self.store.get_job(job_id)
            if latest and latest["cancel_requested"]:
                self.store.update_job(job_id, "cancelled", result=result, stage="cancelled", progress=int(latest["progress"]))
                return
            if kind == "prepare-pdf":
                prepared_id = str(result.get("prepared_id") or Path(str(result["bundle"])).name)
                self.store.update_job(job_id, "prepared", result=result, stage="prepared", progress=95, prepared_id=prepared_id)
                self.store.update_job(job_id, "awaiting_confirmation", result=result, stage="awaiting_confirmation", progress=100, prepared_id=prepared_id)
            else:
                self.store.update_job(job_id, "completed", result=result, stage="completed", progress=100)
        except Exception as exc:
            self.store.update_job(job_id, "failed", error=f"{type(exc).__name__}: {exc}", stage="failed")
            raise

    def default_handlers(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        def prepare_pdf_job(payload: dict[str, Any]) -> dict[str, str]:
            args = argparse.Namespace(
                pdf=str(payload["pdf"]), vault=str(self.vault), kind=str(payload.get("kind", "paper")),
                domain_focus=str(payload.get("domain_focus", "")), model=str(payload.get("model", "deepseek-v4-pro")),
                base_url=str(payload.get("base_url", "https://api.deepseek.com")), max_concepts=int(payload.get("max_concepts", 3)),
            )
            bundle = Path(prepared_pdf.prepare_bundle(args, progress=payload.get("_progress")))
            return {"bundle": str(bundle), "prepared_id": bundle.name}
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

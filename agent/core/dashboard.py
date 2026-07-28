"""
Dashboard metrics aggregator.

Metric contracts (MVP):
  storage_total_bytes         — filesystem scan of 90-Local-Only + SQLite
  learning_duration_ms_7d     — sum of study session duration, past 7 days incl. today
  knowledge_asset_count       — count of reviewed/core Markdown under 20-Knowledge
  agent_completed_run_count_7d — completed pi_agent_runs, past 7 days

Trends:
  daily_learning_duration     — learning_duration_ms per day
  daily_knowledge_created     — new knowledge notes per day
  daily_agent_runs_by_type    — agent runs per day, by run outcome

Storage:
  storage_breakdown           — categorized bytes (db / wal / conversations / reasoning / attachments / cache / logs / temp)
  data_health                 — WAL size, orphan files, index status

Source tables:
  - learning_events (duration_ms, event_type, created_at)
  - 20-Knowledge/*.md (frontmatter: reviewed / core / draft status)
  - pi_agent_runs (status, created_at)
  - pi_agent_events (event_type, created_at)
  - file_system (90-Local-Only, agent.sqlite3)

Timezone: user local (Asia/Shanghai default).  Missing data → zero.
Today is always live-queried from source tables; historical days use pre-aggregated rows.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


TZ = timezone(timedelta(hours=8))  # Asia/Shanghai default


def _today() -> date:
    return datetime.now(TZ).date()


def _now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _date_str(d: date) -> str:
    return d.isoformat()


def _scan_dir_bytes(root: Path) -> int:
    total = 0
    try:
        for entry in root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


class DashboardAggregator:
    """Idempotent daily aggregation + storage snapshot."""

    def __init__(self, vault: Path, store: Any) -> None:
        self.vault = Path(vault)
        self.store = store
        self._knowledge_root = self.vault / "20-Knowledge"

    # ── daily business metrics ────────────────────────────────

    def aggregate_daily_metrics(self, metric_date: date) -> dict[str, Any]:
        conn = self.store.connection
        ds = _date_str(metric_date)
        day_start = f"{ds}T00:00:00"
        day_end = f"{ds}T23:59:59"

        learning_duration_ms = conn.execute(
            "SELECT COALESCE(SUM(duration_ms), 0) FROM learning_events "
            "WHERE created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        completed_learning_tasks = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM learning_events "
            "WHERE event_type='study_session_completed' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]

        knowledge_created_count = self._count_knowledge_notes_created(ds)

        agent_run_count = conn.execute(
            "SELECT COUNT(*) FROM pi_agent_runs WHERE created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        agent_completed_count = conn.execute(
            "SELECT COUNT(*) FROM pi_agent_runs WHERE status='completed' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        agent_failed_count = conn.execute(
            "SELECT COUNT(*) FROM pi_agent_runs WHERE status='failed' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        tool_call_count = conn.execute(
            "SELECT COUNT(*) FROM pi_agent_events WHERE event_type IN ('tool_use', 'tool_call_start') AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]

        input_tokens = conn.execute(
            "SELECT COALESCE(SUM(json_extract(payload_json, '$.usage.inputTokens')), 0) FROM pi_agent_events "
            "WHERE event_type='usage' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        output_tokens = conn.execute(
            "SELECT COALESCE(SUM(json_extract(payload_json, '$.usage.outputTokens')), 0) FROM pi_agent_events "
            "WHERE event_type='usage' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]
        reasoning_tokens = conn.execute(
            "SELECT COALESCE(SUM(json_extract(payload_json, '$.usage.reasoningTokens')), 0) FROM pi_agent_events "
            "WHERE event_type='usage' AND created_at >= ? AND created_at <= ?",
            (day_start, day_end),
        ).fetchone()[0]

        row = dict(
            metric_date=ds,
            learning_duration_ms=learning_duration_ms,
            completed_learning_tasks=completed_learning_tasks,
            knowledge_created_count=knowledge_created_count,
            agent_run_count=agent_run_count,
            agent_completed_count=agent_completed_count,
            agent_failed_count=agent_failed_count,
            tool_call_count=tool_call_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            computed_at=_now_iso(),
            schema_version=1,
        )

        conn.execute(
            """INSERT INTO dashboard_daily_metrics
               (metric_date, learning_duration_ms, completed_learning_tasks, knowledge_created_count,
                agent_run_count, agent_completed_count, agent_failed_count, tool_call_count,
                input_tokens, output_tokens, reasoning_tokens, computed_at, schema_version)
               VALUES (:metric_date, :learning_duration_ms, :completed_learning_tasks, :knowledge_created_count,
                       :agent_run_count, :agent_completed_count, :agent_failed_count, :tool_call_count,
                       :input_tokens, :output_tokens, :reasoning_tokens, :computed_at, :schema_version)
               ON CONFLICT(metric_date) DO UPDATE SET
                learning_duration_ms=excluded.learning_duration_ms,
                completed_learning_tasks=excluded.completed_learning_tasks,
                knowledge_created_count=excluded.knowledge_created_count,
                agent_run_count=excluded.agent_run_count,
                agent_completed_count=excluded.agent_completed_count,
                agent_failed_count=excluded.agent_failed_count,
                tool_call_count=excluded.tool_call_count,
                input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens,
                reasoning_tokens=excluded.reasoning_tokens,
                computed_at=excluded.computed_at,
                schema_version=excluded.schema_version""",
            row,
        )
        conn.commit()
        return row

    # ── storage snapshot ──────────────────────────────────────

    def collect_storage_snapshot(self, snapshot_date: date | None = None) -> dict[str, Any]:
        sd = snapshot_date or _today()
        ds = _date_str(sd)
        local_only = self.vault / "90-Local-Only"
        agent_dir = local_only / "Agent"
        db_path = agent_dir / "agent.sqlite3"

        database_bytes = db_path.stat().st_size if db_path.is_file() else 0
        wal_path = Path(str(db_path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
        conversation_bytes = _scan_dir_bytes(agent_dir / "Conversations")
        attachment_bytes = _scan_dir_bytes(agent_dir / "Attachments")
        research_cache_bytes = _scan_dir_bytes(agent_dir / "WebCache")
        log_path = agent_dir / "logs" / "events.jsonl"
        log_bytes = log_path.stat().st_size if log_path.is_file() else 0

        # Reasoning: scan event payloads for reasoning content size estimate
        reasoning_bytes = 0
        try:
            row = conn.execute(
                "SELECT SUM(LENGTH(payload_json)) FROM pi_agent_events WHERE event_type LIKE 'reasoning%'"
            ).fetchone()
            if row and row[0]:
                reasoning_bytes = int(row[0])
        except Exception:
            pass

        temporary_bytes = _scan_dir_bytes(local_only / "Temp") if (local_only / "Temp").is_dir() else 0

        reclaimable_bytes = _estimate_reclaimable_bytes(local_only)

        row = dict(
            snapshot_date=ds,
            database_bytes=database_bytes,
            wal_bytes=wal_bytes,
            conversation_bytes=conversation_bytes,
            reasoning_bytes=reasoning_bytes,
            attachment_bytes=attachment_bytes,
            research_cache_bytes=research_cache_bytes,
            log_bytes=log_bytes,
            temporary_bytes=temporary_bytes,
            reclaimable_bytes=reclaimable_bytes,
            computed_at=_now_iso(),
            schema_version=1,
        )

        conn = self.store.connection
        conn.execute(
            """INSERT INTO dashboard_storage_snapshots
               (snapshot_date, database_bytes, wal_bytes, conversation_bytes, reasoning_bytes,
                attachment_bytes, research_cache_bytes, log_bytes, temporary_bytes,
                reclaimable_bytes, computed_at, schema_version)
               VALUES (:snapshot_date, :database_bytes, :wal_bytes, :conversation_bytes, :reasoning_bytes,
                       :attachment_bytes, :research_cache_bytes, :log_bytes, :temporary_bytes,
                       :reclaimable_bytes, :computed_at, :schema_version)
               ON CONFLICT(snapshot_date) DO UPDATE SET
                database_bytes=excluded.database_bytes,
                wal_bytes=excluded.wal_bytes,
                conversation_bytes=excluded.conversation_bytes,
                reasoning_bytes=excluded.reasoning_bytes,
                attachment_bytes=excluded.attachment_bytes,
                research_cache_bytes=excluded.research_cache_bytes,
                log_bytes=excluded.log_bytes,
                temporary_bytes=excluded.temporary_bytes,
                reclaimable_bytes=excluded.reclaimable_bytes,
                computed_at=excluded.computed_at,
                schema_version=excluded.schema_version""",
            row,
        )
        conn.commit()
        return row

    # ── gap-filling ───────────────────────────────────────────

    def fill_recent_gaps(self, days: int = 7) -> list[dict[str, Any]]:
        today = _today()
        results: list[dict[str, Any]] = []
        for offset in range(days):
            d = today - timedelta(days=offset)
            ds = _date_str(d)
            existing = self.store.connection.execute(
                "SELECT metric_date FROM dashboard_daily_metrics WHERE metric_date=?", (ds,)
            ).fetchone()
            if not existing:
                results.append(self.aggregate_daily_metrics(d))
        return results

    # ── helpers ───────────────────────────────────────────────

    def _count_knowledge_notes_created(self, date_str: str) -> int:
        root = self._knowledge_root
        if not root.is_dir():
            return 0
        count = 0
        day_prefix = date_str
        for md in root.rglob("*.md"):
            if md.is_symlink():
                continue
            try:
                stat = md.stat()
                created_date = datetime.fromtimestamp(stat.st_ctime, TZ).date().isoformat()
                if created_date == day_prefix:
                    count += 1
            except OSError:
                pass
        return count


def _estimate_reclaimable_bytes(local_only: Path) -> int:
    agent_dir = local_only / "Agent"
    total = 0
    web_cache = agent_dir / "WebCache"
    if web_cache.is_dir():
        total += _scan_dir_bytes(web_cache)
    # Old reasoning files from earlier builds
    old_reasoning = local_only / "Reasoning"
    if old_reasoning.is_dir():
        total += _scan_dir_bytes(old_reasoning)
    logs_dir = agent_dir / "logs"
    if logs_dir.is_dir():
        total += _scan_dir_bytes(logs_dir)
    temp = local_only / "Temp"
    if temp.is_dir():
        total += _scan_dir_bytes(temp)
    return total

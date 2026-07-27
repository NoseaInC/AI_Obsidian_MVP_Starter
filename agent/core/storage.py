from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.redaction import SENSITIVE_KEY, redact_secret_text


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
  payload_json TEXT NOT NULL, result_json TEXT, error TEXT,
  current_stage TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
  prepared_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
  state TEXT NOT NULL, stage TEXT NOT NULL, progress INTEGER NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS change_sets (
  change_set_id TEXT PRIMARY KEY, prepared_id TEXT, state TEXT NOT NULL,
  bundle_hash TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY, role TEXT NOT NULL, path TEXT NOT NULL,
  status TEXT NOT NULL, review_state TEXT, content_hash TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
  entity_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mastery_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id TEXT NOT NULL,
  old_mastery INTEGER, new_mastery INTEGER NOT NULL, confirmed INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quizzes (
  quiz_id TEXT PRIMARY KEY, artifact_id TEXT, state TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
  recommendation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, artifact_id TEXT,
  due_date TEXT, score REAL NOT NULL, state TEXT NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendation_feedback (
  feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
  recommendation_id TEXT NOT NULL, action TEXT NOT NULL,
  details_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_sessions (
  session_id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL,
  state TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_at TEXT, details_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_profiles (
  profile_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
  provider_type TEXT NOT NULL, base_url TEXT NOT NULL,
  api_key_reference TEXT NOT NULL, default_model TEXT NOT NULL,
  available_models_json TEXT NOT NULL, settings_json TEXT NOT NULL,
  enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_routing (
  task TEXT PRIMARY KEY, profile_id TEXT, model_override TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(profile_id) REFERENCES model_profiles(profile_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id TEXT PRIMARY KEY, state TEXT NOT NULL, journal_path TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS brain_runs (
  id TEXT PRIMARY KEY, request_id TEXT NOT NULL, correlation_id TEXT NOT NULL,
  idempotency_key TEXT, primary_intent TEXT, secondary_intents_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL, status TEXT NOT NULL, current_step TEXT,
  model_profile_id TEXT, request_json TEXT NOT NULL, plan_json TEXT,
  result_json TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
  retry_of TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_at TEXT, error_code TEXT, error_message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_brain_runs_idempotency
  ON brain_runs(idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE TABLE IF NOT EXISTS brain_steps (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  skill TEXT NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL,
  started_at TEXT, completed_at TEXT, error_code TEXT,
  FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tool_events (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT,
  tool TEXT NOT NULL, status TEXT NOT NULL, input_summary TEXT NOT NULL,
  output_summary TEXT NOT NULL, started_at TEXT NOT NULL,
  completed_at TEXT, error_code TEXT,
  FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS proposed_actions (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_type TEXT NOT NULL,
  title TEXT NOT NULL, summary TEXT NOT NULL, risk_level TEXT NOT NULL,
  status TEXT NOT NULL, change_set_id TEXT, created_at TEXT NOT NULL,
  decided_at TEXT, FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS brain_change_sets (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL,
  title TEXT NOT NULL, writes_json TEXT NOT NULL, preview TEXT NOT NULL,
  base_hashes_json TEXT NOT NULL, transaction_id TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, applied_at TEXT,
  FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assistant_change_sets (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL, state TEXT NOT NULL,
  title TEXT NOT NULL, writes_json TEXT NOT NULL, preview TEXT NOT NULL,
  base_hashes_json TEXT NOT NULL, transaction_id TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, applied_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_assistant_change_sets_run
  ON assistant_change_sets(run_id, created_at);
CREATE TABLE IF NOT EXISTS research_bundles (
  id TEXT PRIMARY KEY, run_id TEXT, title TEXT NOT NULL, question TEXT NOT NULL,
  status TEXT NOT NULL, source_count INTEGER NOT NULL, estimated_minutes INTEGER NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_sources (
  id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, source_type TEXT NOT NULL,
  title TEXT NOT NULL, canonical_url TEXT NOT NULL, authors TEXT NOT NULL,
  published_at TEXT, relevance REAL NOT NULL, quality REAL NOT NULL,
  difficulty TEXT NOT NULL, estimated_minutes INTEGER NOT NULL,
  reason TEXT NOT NULL, metadata_json TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES research_bundles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS curriculum_candidates (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, canonical_title TEXT NOT NULL,
  kind TEXT NOT NULL, domain TEXT NOT NULL, route TEXT NOT NULL,
  prerequisites_json TEXT NOT NULL, related_topics_json TEXT NOT NULL,
  why_now TEXT NOT NULL, learning_outcomes_json TEXT NOT NULL,
  estimated_minutes INTEGER NOT NULL, difficulty TEXT NOT NULL,
  scores_json TEXT NOT NULL, confidence REAL NOT NULL, basis_json TEXT NOT NULL,
  status TEXT NOT NULL, model_profile_id TEXT, generated_at TEXT NOT NULL,
  cooldown_until TEXT,
  verification_json TEXT NOT NULL DEFAULT '{}',
  schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS plan_proposals (
  id TEXT PRIMARY KEY, run_id TEXT, title TEXT NOT NULL, state TEXT NOT NULL,
  tasks_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, active_artifact_id TEXT,
  active_task_thread_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
  content_reference TEXT NOT NULL, message_type TEXT NOT NULL,
  task_thread_id TEXT, artifact_group_id TEXT, created_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, kind TEXT NOT NULL,
  display_name TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL, storage_reference TEXT NOT NULL, source_type TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_attachments_sha ON attachments(sha256);
CREATE TABLE IF NOT EXISTS agent_artifacts (
  id TEXT PRIMARY KEY, artifact_type TEXT NOT NULL, title TEXT NOT NULL,
  status TEXT NOT NULL, conversation_id TEXT NOT NULL, source_run_id TEXT,
  artifact_group_id TEXT, version INTEGER NOT NULL, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_conversation ON agent_artifacts(conversation_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS artifact_versions (
  id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, version INTEGER NOT NULL,
  parent_version INTEGER, revision_instruction TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(artifact_id, version),
  FOREIGN KEY(artifact_id) REFERENCES agent_artifacts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS intake_items (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_message_id TEXT NOT NULL,
  run_id TEXT, status TEXT NOT NULL, progress INTEGER NOT NULL,
  attachment_ids_json TEXT NOT NULL, reference_json TEXT NOT NULL,
  error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_intake_items_status ON intake_items(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS learning_events (
  id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
  subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
  topic TEXT, domain TEXT, recommendation_id TEXT, session_id TEXT,
  duration_ms INTEGER, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, schema_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_learning_events_created ON learning_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_events_recommendation ON learning_events(recommendation_id, created_at DESC);
CREATE TABLE IF NOT EXISTS learner_features (
  feature_key TEXT NOT NULL, scope TEXT NOT NULL,
  value_json TEXT NOT NULL, confidence REAL NOT NULL,
  evidence_count INTEGER NOT NULL, window_start TEXT NOT NULL,
  window_end TEXT NOT NULL, source TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(feature_key, scope, source)
);
CREATE TABLE IF NOT EXISTS lesson_versions (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, version INTEGER NOT NULL,
  title TEXT NOT NULL, payload_json TEXT NOT NULL,
  verification_json TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(candidate_id, version)
);
CREATE TABLE IF NOT EXISTS assistant_task_threads (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, user_message_id TEXT NOT NULL,
  title TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL,
  progress INTEGER NOT NULL, artifact_group_id TEXT, error_code TEXT,
  recoverable INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_assistant_task_threads_conversation
  ON assistant_task_threads(conversation_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS assistant_task_steps (
  id TEXT PRIMARY KEY, task_thread_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
  label TEXT NOT NULL, user_facing_label TEXT NOT NULL, status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '', started_at TEXT, completed_at TEXT, error_code TEXT,
  UNIQUE(task_thread_id, ordinal),
  FOREIGN KEY(task_thread_id) REFERENCES assistant_task_threads(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS artifact_groups (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, task_thread_id TEXT NOT NULL,
  title TEXT NOT NULL, group_type TEXT NOT NULL, status TEXT NOT NULL,
  primary_artifact_id TEXT, child_artifact_ids_json TEXT NOT NULL,
  version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
  FOREIGN KEY(task_thread_id) REFERENCES assistant_task_threads(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS conversation_summaries (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, version INTEGER NOT NULL,
  summary TEXT NOT NULL, source_message_ids_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(conversation_id, version),
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS conversation_knowledge_signals (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
  signal_type TEXT NOT NULL, topic TEXT NOT NULL, detail TEXT NOT NULL,
  confidence REAL NOT NULL, recurrence INTEGER NOT NULL,
  message_ids_json TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(conversation_id, fingerprint),
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_conversation_signals_topic
  ON conversation_knowledge_signals(topic, updated_at DESC);
CREATE TABLE IF NOT EXISTS direction_predictions (
  id TEXT PRIMARY KEY, horizon TEXT NOT NULL, title TEXT NOT NULL,
  rationale_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
  confidence REAL NOT NULL, state TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_plan_adjustments (
  id TEXT PRIMARY KEY, adjustment_type TEXT NOT NULL, target_id TEXT,
  before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
  state TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS daily_plans (
  plan_date TEXT PRIMARY KEY, budget_minutes INTEGER NOT NULL,
  state TEXT NOT NULL, version INTEGER NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_recommendations (
  id TEXT PRIMARY KEY, plan_date TEXT NOT NULL, recommendation_id TEXT NOT NULL,
  position INTEGER NOT NULL, minutes INTEGER NOT NULL, state TEXT NOT NULL,
  fixed INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(plan_date, recommendation_id),
  FOREIGN KEY(plan_date) REFERENCES daily_plans(plan_date) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recommendation_exposures (
  id TEXT PRIMARY KEY, recommendation_id TEXT NOT NULL,
  surface TEXT NOT NULL, position INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_sources (
  id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  source_type TEXT NOT NULL, content_hash TEXT NOT NULL, fetched_at TEXT NOT NULL,
  quality REAL NOT NULL, metadata_json TEXT NOT NULL, cache_reference TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_actions (
  id TEXT PRIMARY KEY, action_type TEXT NOT NULL, target TEXT NOT NULL,
  risk_level TEXT NOT NULL, status TEXT NOT NULL, details_json TEXT NOT NULL,
  created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS file_snapshots (
  id TEXT PRIMARY KEY, action_id TEXT NOT NULL, relative_path TEXT NOT NULL,
  content_hash TEXT NOT NULL, snapshot_reference TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(action_id) REFERENCES agent_actions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS file_change_log (
  id TEXT PRIMARY KEY, action_id TEXT NOT NULL, relative_path TEXT NOT NULL,
  before_hash TEXT, after_hash TEXT, diff_summary TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(action_id) REFERENCES agent_actions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS agent_settings (
  key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversation_focus (
  conversation_id TEXT PRIMARY KEY,
  active_topic_json TEXT, active_concept_json TEXT, active_method_json TEXT,
  active_material_json TEXT, active_attachment_ids_json TEXT NOT NULL DEFAULT '[]',
  active_artifact_id TEXT, active_task_thread_id TEXT, active_recommendation_id TEXT,
  active_note_path TEXT, active_selection_reference TEXT,
  last_confirmed_intent TEXT, last_write_target TEXT,
  confidence REAL NOT NULL DEFAULT 0, evidence_json TEXT NOT NULL DEFAULT '[]',
  schema_version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS material_bundles (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, title TEXT NOT NULL,
  material_kinds_json TEXT NOT NULL, attachment_ids_json TEXT NOT NULL,
  source_ids_json TEXT NOT NULL, understanding_json TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS knowledge_units (
  id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, unit_type TEXT NOT NULL,
  title TEXT NOT NULL, content_reference TEXT NOT NULL,
  provenance_json TEXT NOT NULL, source_reference_json TEXT NOT NULL,
  verification_status TEXT NOT NULL, confidence REAL NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES material_bundles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS organization_plans (
  id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, intent TEXT NOT NULL,
  primary_action TEXT NOT NULL, plan_json TEXT NOT NULL, confidence REAL NOT NULL,
  status TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES material_bundles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS organization_actions (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, action_type TEXT NOT NULL,
  target_path TEXT NOT NULL, note_type TEXT NOT NULL, merge_strategy TEXT NOT NULL,
  status TEXT NOT NULL, result_json TEXT NOT NULL,
  created_at TEXT NOT NULL, completed_at TEXT,
  FOREIGN KEY(plan_id) REFERENCES organization_plans(id) ON DELETE CASCADE
);
"""

AGENT_RUNTIME_V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_run_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  UNIQUE(run_id, sequence),
  FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_run_sequence
  ON agent_run_events(run_id, sequence);

CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
  run_id TEXT PRIMARY KEY,
  checkpoint_version INTEGER NOT NULL,
  phase TEXT NOT NULL,
  state_json TEXT NOT NULL,
  pending_approval_id TEXT,
  last_event_sequence INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY(run_id) REFERENCES brain_runs(id) ON DELETE CASCADE
);
"""

PI_RUNTIME_SCHEMA = """
CREATE TABLE IF NOT EXISTS pi_agent_sessions (
  session_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  status TEXT NOT NULL,
  selected_model TEXT,
  state_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pi_agent_runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  source_message_id TEXT NOT NULL,
  status TEXT NOT NULL,
  profile_id TEXT,
  selected_model TEXT,
  provider_adapter_version TEXT,
  parent_run_id TEXT,
  forked_from_sequence INTEGER,
  forked_from_entry_id TEXT,
  current_leaf_id TEXT,
  last_event_sequence INTEGER NOT NULL DEFAULT 0,
  checkpoint_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  error_code TEXT,
  FOREIGN KEY(session_id) REFERENCES pi_agent_sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pi_agent_runs_session
  ON pi_agent_runs(session_id, created_at);
CREATE TABLE IF NOT EXISTS pi_agent_events (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(run_id, sequence),
  FOREIGN KEY(run_id) REFERENCES pi_agent_runs(run_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS task_authorizations (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  source_message_id TEXT NOT NULL,
  objective TEXT NOT NULL,
  resource_scope_json TEXT NOT NULL,
  operation_scope_json TEXT NOT NULL,
  reversible_only INTEGER NOT NULL,
  network_policy TEXT NOT NULL,
  external_side_effects INTEGER NOT NULL,
  expires_at_run_end INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES pi_agent_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_task_authorizations_run
  ON task_authorizations(run_id, status);
CREATE TABLE IF NOT EXISTS pi_session_entries (
  id TEXT PRIMARY KEY,
  parent_id TEXT,
  timestamp TEXT NOT NULL,
  entry_type TEXT NOT NULL,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  sequence INTEGER NOT NULL DEFAULT 0,
  payload_json TEXT NOT NULL,
  FOREIGN KEY(session_id) REFERENCES pi_agent_sessions(session_id) ON DELETE CASCADE,
  FOREIGN KEY(run_id) REFERENCES pi_agent_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pi_session_entries_session_time
  ON pi_session_entries(session_id, timestamp, id);
CREATE TABLE IF NOT EXISTS pi_pending_tool_calls (
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  tool_call_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  permission_request_json TEXT NOT NULL,
  recovery_guard_json TEXT NOT NULL DEFAULT '{}',
  task_authorization_id TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  PRIMARY KEY(run_id, tool_call_id),
  FOREIGN KEY(run_id) REFERENCES pi_agent_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pi_pending_tool_calls_run
  ON pi_pending_tool_calls(run_id, state);
CREATE INDEX IF NOT EXISTS idx_pi_pending_tool_calls_session
  ON pi_pending_tool_calls(session_id, state);
CREATE TABLE IF NOT EXISTS pi_compaction_checkpoints (
  run_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  branch_id TEXT NOT NULL,
  cut_entry_id TEXT NOT NULL,
  kept_from_entry_id TEXT NOT NULL,
  summary_version INTEGER NOT NULL,
  tokens_before INTEGER NOT NULL,
  tokens_after INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  checkpoint_entry_id TEXT,
  summary TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id),
  FOREIGN KEY(run_id) REFERENCES pi_agent_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pi_compaction_checkpoints_branch
  ON pi_compaction_checkpoints(branch_id, created_at);
"""



SCHEMA_VERSION = 16


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


_PROJECTION_SECRET_KEYS = {
    "apikey", "api_key", "authorization", "bearer", "credential",
    "credentials", "environment", "env", "password", "secret", "token",
}


def _projection_safe(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact persisted context before it can reach a model request."""
    if depth > 8:
        return "[nested value omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cleaned = redact_secret_text(value)
        encoded = cleaned.encode("utf-8", errors="replace")
        if len(encoded) <= 4096:
            return cleaned
        digest = hashlib.sha256(encoded).hexdigest()
        return f"{encoded[:4096].decode('utf-8', errors='ignore')}\n[truncated sha256={digest}]"
    if isinstance(value, list):
        return [_projection_safe(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if normalized in {
                "tokensbefore", "tokens_before", "tokensafter", "tokens_after",
                "summaryversion", "summary_version",
            } and isinstance(item, (int, float)):
                result[key] = item
            elif normalized in {"taskauthorization", "task_authorization"}:
                # This exact field is a deliberately bounded authorization
                # summary. Its nested secret-shaped keys are still redacted.
                result[key] = _projection_safe(item, depth=depth + 1)
            elif (
                normalized in _PROJECTION_SECRET_KEYS
                or compact in _PROJECTION_SECRET_KEYS
                or SENSITIVE_KEY.search(key)
            ):
                result[key] = "[redacted]"
            else:
                result[key] = _projection_safe(item, depth=depth + 1)
        return result
    return str(value)[:1000]


_MODEL_OBSERVATION_MAX_BYTES = 12 * 1024
_COMMAND_OUTPUT_KEYS = {
    "stdout", "stderr", "output", "log", "logs", "diff", "patch", "testoutput",
}
_REASONING_EVENT_TYPES = {
    "reasoning", "reasoning_status", "thinking", "thinking_start",
    "thinking_delta", "thinking_end",
}
_LOCAL_REASONING_EVENT_MAX_BYTES = 64 * 1024


def _model_observation_safe(
    value: Any,
    *,
    tool_name: str,
    max_string_bytes: int,
    max_items: int,
    depth: int = 0,
    field_name: str = "",
) -> Any:
    """Keep useful Tool semantics while enforcing a small, secret-free envelope."""
    if depth > 6:
        return "[nested value omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        cleaned = redact_secret_text(value)
        normalized_field = field_name.lower().replace("-", "").replace("_", "")
        command_like = tool_name in {"run_command", "run_bash", "git_diff", "git_status"}
        limit = min(max_string_bytes, 2048) if (
            command_like or normalized_field in _COMMAND_OUTPUT_KEYS
        ) else max_string_bytes
        encoded = cleaned.encode("utf-8", errors="replace")
        if len(encoded) <= limit:
            return cleaned
        digest = hashlib.sha256(encoded).hexdigest()
        prefix = encoded[:limit].decode("utf-8", errors="ignore")
        return f"{prefix}\n[truncated sha256={digest}]"
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        safe = [
            _model_observation_safe(
                item,
                tool_name=tool_name,
                max_string_bytes=max_string_bytes,
                max_items=max_items,
                depth=depth + 1,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            safe.append({"truncatedItems": len(items) - max_items})
        return safe
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        pairs = list(value.items())
        for raw_key, item in pairs[:max_items]:
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if (
                normalized in _PROJECTION_SECRET_KEYS
                or compact in _PROJECTION_SECRET_KEYS
                or SENSITIVE_KEY.search(key)
            ):
                result[key] = "[redacted]"
            else:
                result[key] = _model_observation_safe(
                    item,
                    tool_name=tool_name,
                    max_string_bytes=max_string_bytes,
                    max_items=max_items,
                    depth=depth + 1,
                    field_name=key,
                )
        if len(pairs) > max_items:
            result["_truncatedFields"] = len(pairs) - max_items
        return result
    return _model_observation_safe(
        str(value),
        tool_name=tool_name,
        max_string_bytes=max_string_bytes,
        max_items=max_items,
        depth=depth,
        field_name=field_name,
    )


def _bounded_model_observation(tool_name: str, value: Any) -> dict[str, Any]:
    """Return a model-usable Tool Result that never exceeds the durable budget."""
    raw_value = value if isinstance(value, dict) else {"value": value}
    for max_string_bytes, max_items in (
        (4096, 24),
        (2048, 16),
        (1024, 8),
        (512, 4),
    ):
        safe = _model_observation_safe(
            raw_value,
            tool_name=tool_name,
            max_string_bytes=max_string_bytes,
            max_items=max_items,
        )
        assert isinstance(safe, dict)
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) <= _MODEL_OBSERVATION_MAX_BYTES:
            return safe

    # The final preview is derived from an already-redacted representation.
    compact = _model_observation_safe(
        raw_value,
        tool_name=tool_name,
        max_string_bytes=256,
        max_items=2,
    )
    preview = json.dumps(compact, ensure_ascii=False, sort_keys=True)
    return {
        "truncated": True,
        "excerpt": preview.encode("utf-8")[:8000].decode("utf-8", errors="ignore"),
    }


def _durable_reasoning_event(event: dict[str, Any]) -> dict[str, Any]:
    """Keep authentic local reasoning while bounding accidental oversized deltas."""
    event_type = str(event.get("type") or "")
    if event_type not in _REASONING_EVENT_TYPES and not event_type.startswith("reasoning."):
        return dict(event)
    raw_phase = str(event.get("phase") or "")
    if event_type == "thinking_delta" or event_type.endswith(".delta") or raw_phase == "delta":
        phase = "delta"
    elif event_type == "thinking_end" or event_type.endswith(".completed") or raw_phase == "completed":
        phase = "completed"
    else:
        phase = "started"
    raw_text = redact_secret_text(str(
        event.get("content")
        or event.get("delta")
        or event.get("thinking")
        or ""
    ))
    encoded = raw_text.encode("utf-8", errors="replace")
    if len(encoded) > _LOCAL_REASONING_EVENT_MAX_BYTES:
        digest = hashlib.sha256(encoded).hexdigest()
        raw_text = (
            encoded[:_LOCAL_REASONING_EVENT_MAX_BYTES].decode("utf-8", errors="ignore")
            + f"\n[truncated sha256={digest}]"
        )
    explicit_tokens = event.get("tokenCount")
    try:
        token_count = max(0, int(explicit_tokens or 0))
    except (TypeError, ValueError):
        token_count = 0
    if not token_count and raw_text:
        token_count = max(1, (len(raw_text) + 3) // 4)
    return {
        **({"schemaVersion": event["schemaVersion"]} if "schemaVersion" in event else {}),
        **({"runId": event["runId"]} if "runId" in event else {}),
        **({"conversationId": event["conversationId"]} if "conversationId" in event else {}),
        **({"sequence": event["sequence"]} if "sequence" in event else {}),
        **({"seq": event["seq"]} if "seq" in event else {}),
        "type": "reasoning",
        "blockId": str(event.get("blockId") or "provider-reasoning"),
        "provider": str(event.get("provider") or "provider"),
        "phase": phase,
        "content": raw_text,
        "tokenCount": token_count,
    }


def _durable_pi_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize every privacy-sensitive payload before SQLite/reconnect."""
    normalized = _durable_reasoning_event(event)
    event_type = str(normalized.get("type") or "")
    if event_type == "tool_use":
        tool_name = str(normalized.get("name") or "")
        return {
            **normalized,
            **({
                "input": _bounded_model_observation(tool_name, normalized["input"]),
            } if isinstance(normalized.get("input"), dict) else {}),
            **({
                "arguments": _bounded_model_observation(tool_name, normalized["arguments"]),
            } if isinstance(normalized.get("arguments"), dict) else {}),
        }
    if event_type == "notice" and isinstance(normalized.get("data"), dict):
        data = normalized["data"]
        tool_name = str(data.get("tool") or normalized.get("name") or "")
        return {**normalized, "data": _bounded_model_observation(tool_name, data)}
    if event_type != "tool_result":
        return normalized
    raw_result = normalized.get("result") if isinstance(normalized.get("result"), dict) else {}
    nested = raw_result.get("result") if isinstance(raw_result.get("result"), dict) else raw_result
    observation_hash = str(normalized.get("observationHash") or hashlib.sha256(
        json.dumps(raw_result, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest())
    safe_result = _bounded_model_observation(
        str(normalized.get("name") or ""),
        nested,
    )
    safe_envelope = {
        key: _projection_safe(raw_result[key])
        for key in ("tool", "permissionLevel", "idempotent", "blocked", "status")
        if key in raw_result
    }
    return {
        **normalized,
        "result": {**safe_envelope, "result": safe_result},
        "observationHash": observation_hash,
    }


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_jobs()
        self._migrate_daily_intelligence()
        self._migrate_assistant_chat_first()
        self._migrate_learning_brain()
        self._migrate_agent_runtime_v3()
        self._migrate_pi_runtime()
        self._migrate_dashboard()
        self._record_schema_version()
        self.connection.commit()

    def _migrate_agent_runtime_v3(self) -> None:
        self.connection.executescript(AGENT_RUNTIME_V3_SCHEMA)

    def _migrate_pi_runtime(self) -> None:
        # The short-lived experimental table pi_pending_permissions was never
        # part of a released schema; drop it so the structured
        # pi_pending_tool_calls table becomes the single source of truth.
        self.connection.execute("DROP TABLE IF EXISTS pi_pending_permissions")
        self.connection.executescript(PI_RUNTIME_SCHEMA)
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(pi_session_entries)")
        }
        if "sequence" not in columns:
            self.connection.execute(
                "ALTER TABLE pi_session_entries ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0"
            )
            rows = self.connection.execute(
                "SELECT id, payload_json FROM pi_session_entries"
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                    sequence = int(payload.get("sequence") or payload.get("seq") or 0)
                except (TypeError, ValueError, json.JSONDecodeError):
                    sequence = 0
                self.connection.execute(
                    "UPDATE pi_session_entries SET sequence=? WHERE id=?",
                    (max(0, sequence), row["id"]),
                )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pi_session_entries_run_sequence "
            "ON pi_session_entries(session_id, run_id, sequence)"
        )
        pending_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(pi_pending_tool_calls)")
        }
        if "recovery_guard_json" not in pending_columns:
            self.connection.execute(
                "ALTER TABLE pi_pending_tool_calls "
                "ADD COLUMN recovery_guard_json TEXT NOT NULL DEFAULT '{}'"
            )
        run_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(pi_agent_runs)")
        }
        if "forked_from_entry_id" not in run_columns:
            self.connection.execute(
                "ALTER TABLE pi_agent_runs ADD COLUMN forked_from_entry_id TEXT"
            )
        if "current_leaf_id" not in run_columns:
            self.connection.execute(
                "ALTER TABLE pi_agent_runs ADD COLUMN current_leaf_id TEXT"
            )
        for name in ("profile_id", "selected_model", "provider_adapter_version"):
            if name not in run_columns:
                self.connection.execute(
                    f"ALTER TABLE pi_agent_runs ADD COLUMN {name} TEXT"
                )
        runs = self.connection.execute(
            "SELECT run_id FROM pi_agent_runs WHERE current_leaf_id IS NULL"
        ).fetchall()
        for run in runs:
            leaf = self.connection.execute(
                "SELECT id FROM pi_session_entries WHERE run_id=? ORDER BY timestamp DESC, id DESC LIMIT 1",
                (run["run_id"],),
            ).fetchone()
            if leaf:
                self.connection.execute(
                    "UPDATE pi_agent_runs SET current_leaf_id=? WHERE run_id=?",
                    (leaf["id"], run["run_id"]),
                )
        checkpoint_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(pi_compaction_checkpoints)")
        }
        if "checkpoint_entry_id" not in checkpoint_columns:
            self.connection.execute(
                "ALTER TABLE pi_compaction_checkpoints ADD COLUMN checkpoint_entry_id TEXT"
            )
        if "summary" not in checkpoint_columns:
            self.connection.execute(
                "ALTER TABLE pi_compaction_checkpoints ADD COLUMN summary TEXT NOT NULL DEFAULT ''"
            )
        # Normalize older reasoning variants while preserving any local prose
        # that has not already been removed by a status-only build.
        event_rows = self.connection.execute(
            "SELECT id, run_id, sequence, payload_json FROM pi_agent_events"
        ).fetchall()
        migrated_tool_events: dict[tuple[str, int], dict[str, Any]] = {}
        for row in event_rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            normalized = _durable_pi_event(payload)
            if str(normalized.get("type") or "") == "tool_result":
                migrated_tool_events[(str(row["run_id"]), int(row["sequence"]))] = normalized
            if normalized != payload:
                self.connection.execute(
                    "UPDATE pi_agent_events SET event_type=?, payload_json=? WHERE id=?",
                    (
                        str(normalized.get("type") or "notice"),
                        json.dumps(normalized, ensure_ascii=False),
                        row["id"],
                    ),
                )
        entry_rows = self.connection.execute(
            "SELECT id, run_id, sequence, entry_type, payload_json FROM pi_session_entries"
        ).fetchall()
        for row in entry_rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            normalized = _durable_reasoning_event(payload)
            if str(row["entry_type"]) == "tool_result" and not isinstance(
                normalized.get("modelObservation"), dict,
            ):
                source_event = migrated_tool_events.get((
                    str(row["run_id"]), int(row["sequence"] or 0),
                ))
                if source_event:
                    restored = self._pi_session_payload(source_event)
                    normalized = {
                        **normalized,
                        "modelObservation": restored.get("modelObservation", {}),
                        "observationHash": restored.get(
                            "observationHash",
                            normalized.get("observationHash", ""),
                        ),
                    }
            if normalized != payload:
                self.connection.execute(
                    "UPDATE pi_session_entries SET payload_json=? WHERE id=?",
                    (json.dumps(normalized, ensure_ascii=False), row["id"]),
                )

    def _migrate_dashboard(self) -> None:
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS dashboard_daily_metrics (
                metric_date TEXT PRIMARY KEY,
                learning_duration_ms INTEGER NOT NULL DEFAULT 0,
                completed_learning_tasks INTEGER NOT NULL DEFAULT 0,
                knowledge_created_count INTEGER NOT NULL DEFAULT 0,
                agent_run_count INTEGER NOT NULL DEFAULT 0,
                agent_completed_count INTEGER NOT NULL DEFAULT 0,
                agent_failed_count INTEGER NOT NULL DEFAULT 0,
                tool_call_count INTEGER NOT NULL DEFAULT 0,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS dashboard_storage_snapshots (
                snapshot_date TEXT PRIMARY KEY,
                database_bytes INTEGER NOT NULL DEFAULT 0,
                wal_bytes INTEGER NOT NULL DEFAULT 0,
                conversation_bytes INTEGER NOT NULL DEFAULT 0,
                reasoning_bytes INTEGER NOT NULL DEFAULT 0,
                attachment_bytes INTEGER NOT NULL DEFAULT 0,
                research_cache_bytes INTEGER NOT NULL DEFAULT 0,
                log_bytes INTEGER NOT NULL DEFAULT 0,
                temporary_bytes INTEGER NOT NULL DEFAULT 0,
                reclaimable_bytes INTEGER NOT NULL DEFAULT 0,
                computed_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL DEFAULT 1
            )"""
        )
        self.connection.commit()

    def create_pi_task_authorization(self, authorization: dict[str, Any]) -> dict[str, Any]:
        required = (
            "id", "sessionId", "runId", "turnId", "sourceMessageId",
            "objective", "resourceScope", "operationScope",
        )
        if any(key not in authorization for key in required):
            raise ValueError("task_authorization_invalid")
        now = _now()
        session_id = str(authorization["sessionId"])
        run_id = str(authorization["runId"])
        with self.lock:
            self.connection.execute(
                """INSERT OR IGNORE INTO pi_agent_sessions(
                     session_id, conversation_id, status, selected_model,
                     state_json, created_at, updated_at
                   ) VALUES (?, ?, 'running', ?, '{}', ?, ?)""",
                (
                    session_id,
                    str(authorization.get("conversationId") or session_id),
                    str(authorization.get("model") or ""),
                    now,
                    now,
                ),
            )
            self.connection.execute(
                """INSERT OR IGNORE INTO pi_agent_runs(
                     run_id, session_id, turn_id, source_message_id, status,
                     profile_id, selected_model, provider_adapter_version,
                     parent_run_id, forked_from_sequence, forked_from_entry_id,
                     current_leaf_id, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    session_id,
                    str(authorization["turnId"]),
                    str(authorization["sourceMessageId"]),
                    str(authorization.get("profileId") or ""),
                    str(authorization.get("model") or ""),
                    str(authorization.get("providerAdapterVersion") or ""),
                    authorization.get("parentRunId"),
                    authorization.get("forkedFromSequence"),
                    authorization.get("forkedFromEntryId"),
                    authorization.get("forkedFromEntryId"),
                    now,
                    now,
                ),
            )
            parent_run_id = str(authorization.get("parentRunId") or "")
            if not authorization.get("forkedFromEntryId") and parent_run_id:
                self.connection.execute(
                    """UPDATE pi_agent_runs
                       SET current_leaf_id=(SELECT current_leaf_id FROM pi_agent_runs WHERE run_id=?)
                       WHERE run_id=? AND current_leaf_id IS NULL""",
                    (parent_run_id, run_id),
                )
            if not authorization.get("forkedFromEntryId") and not parent_run_id:
                session_state_row = self.connection.execute(
                    "SELECT state_json FROM pi_agent_sessions WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                session_state = json.loads(session_state_row["state_json"] or "{}") if session_state_row else {}
                inherited_leaf = str(session_state.get("currentLeafId") or "") or None
                self.connection.execute(
                    "UPDATE pi_agent_runs SET current_leaf_id=? "
                    "WHERE run_id=? AND current_leaf_id IS NULL",
                    (inherited_leaf, run_id),
                )
            existing = self.connection.execute(
                "SELECT * FROM task_authorizations WHERE id=?",
                (str(authorization["id"]),),
            ).fetchone()
            if existing:
                decoded = self._decode_task_authorization(existing)
                comparable = {
                    key: decoded[key]
                    for key in (
                        "id", "sessionId", "runId", "turnId", "sourceMessageId",
                        "objective", "resourceScope", "operationScope",
                        "reversibleOnly", "networkPolicy", "externalSideEffects",
                        "expiresAtRunEnd",
                    )
                }
                incoming = {
                    "id": str(authorization["id"]),
                    "sessionId": session_id,
                    "runId": run_id,
                    "turnId": str(authorization["turnId"]),
                    "sourceMessageId": str(authorization["sourceMessageId"]),
                    "objective": str(authorization["objective"]),
                    "resourceScope": dict(authorization["resourceScope"]),
                    "operationScope": list(authorization["operationScope"]),
                    "reversibleOnly": authorization.get("reversibleOnly") is True,
                    "networkPolicy": str(authorization.get("networkPolicy") or "deny"),
                    "externalSideEffects": authorization.get("externalSideEffects") is True,
                    "expiresAtRunEnd": authorization.get("expiresAtRunEnd") is not False,
                }
                if comparable != incoming:
                    raise RuntimeError("task_authorization_id_conflict")
                return decoded
            self.connection.execute(
                """INSERT INTO task_authorizations(
                     id, session_id, run_id, turn_id, source_message_id,
                     objective, resource_scope_json, operation_scope_json,
                     reversible_only, network_policy, external_side_effects,
                     expires_at_run_end, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    str(authorization["id"]),
                    session_id,
                    run_id,
                    str(authorization["turnId"]),
                    str(authorization["sourceMessageId"]),
                    str(authorization["objective"])[:20_000],
                    json.dumps(dict(authorization["resourceScope"]), ensure_ascii=False),
                    json.dumps(list(authorization["operationScope"]), ensure_ascii=False),
                    int(authorization.get("reversibleOnly") is True),
                    str(authorization.get("networkPolicy") or "deny"),
                    int(authorization.get("externalSideEffects") is True),
                    int(authorization.get("expiresAtRunEnd") is not False),
                    now,
                    now,
                ),
            )
            run = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            assert run is not None
            self._append_pi_session_entry_locked(
                run,
                f"pi-entry-auth-{authorization['id']}",
                "task_authorization",
                {
                    "authorizationId": str(authorization["id"]),
                    "objective": str(authorization["objective"])[:20_000],
                    "resourceScope": dict(authorization["resourceScope"]),
                    "operationScope": list(authorization["operationScope"]),
                    "networkPolicy": str(authorization.get("networkPolicy") or "deny"),
                    "reversibleOnly": authorization.get("reversibleOnly") is True,
                },
                now,
            )
            self.connection.commit()
        return self.get_task_authorization(str(authorization["id"]))

    @staticmethod
    def _decode_task_authorization(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            "id": item["id"],
            "sessionId": item["session_id"],
            "runId": item["run_id"],
            "turnId": item["turn_id"],
            "sourceMessageId": item["source_message_id"],
            "objective": item["objective"],
            "resourceScope": json.loads(item["resource_scope_json"]),
            "operationScope": json.loads(item["operation_scope_json"]),
            "reversibleOnly": bool(item["reversible_only"]),
            "networkPolicy": item["network_policy"],
            "externalSideEffects": bool(item["external_side_effects"]),
            "expiresAtRunEnd": bool(item["expires_at_run_end"]),
            "status": item["status"],
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def get_task_authorization(self, authorization_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM task_authorizations WHERE id=?",
                (authorization_id,),
            ).fetchone()
        if not row:
            raise ValueError("task_authorization_not_found")
        return self._decode_task_authorization(row)

    def update_task_authorization_scope(
        self,
        authorization_id: str,
        resource_scope: dict[str, Any],
        operation_scope: list[str],
    ) -> dict[str, Any]:
        now = _now()
        with self.lock:
            cursor = self.connection.execute(
                """UPDATE task_authorizations
                   SET resource_scope_json=?, operation_scope_json=?, updated_at=?
                   WHERE id=? AND status='active'""",
                (
                    json.dumps(resource_scope, ensure_ascii=False),
                    json.dumps(sorted(set(operation_scope)), ensure_ascii=False),
                    now,
                    authorization_id,
                ),
            )
            if not cursor.rowcount:
                raise RuntimeError("task_authorization_not_active")
            self.connection.commit()
        return self.get_task_authorization(authorization_id)

    def complete_pi_run(self, run_id: str, status: str, error_code: str = "") -> None:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("pi_run_status_invalid")
        now = _now()
        with self.lock:
            self.connection.execute(
                """UPDATE pi_agent_runs
                   SET status=?, completed_at=?, updated_at=?, error_code=?
                   WHERE run_id=?""",
                (status, now, now, error_code or None, run_id),
            )
            self.connection.execute(
                """UPDATE task_authorizations SET status='expired', updated_at=?
                   WHERE run_id=? AND expires_at_run_end=1""",
                (now, run_id),
            )
            self.connection.execute(
                """UPDATE pi_agent_sessions SET status=?, updated_at=?
                   WHERE session_id=(SELECT session_id FROM pi_agent_runs WHERE run_id=?)""",
                (status, now, run_id),
            )
            self.connection.commit()

    @staticmethod
    def _pi_entry_type(event_type: str) -> str:
        return {
            "text": "message",
            "tool_use": "tool_call",
            "tool_result": "tool_result",
            "action_result": "action",
            "context_compacted": "compaction",
            "confirmation_required": "confirmation",
            "question_required": "confirmation",
            "scope_expansion_required": "permission_decision",
        }.get(event_type, "custom")

    @staticmethod
    def _pi_session_payload(event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "notice")
        if event_type == "tool_result":
            result = event.get("result") if isinstance(event.get("result"), dict) else {}
            nested = result.get("result") if isinstance(result.get("result"), dict) else {}
            model_observation = _bounded_model_observation(
                str(event.get("name") or ""),
                nested if nested else result,
            )
            action_id = str(nested.get("actionId") or "")
            continuation = nested.get("continuation")
            code = str(nested.get("code") or result.get("code") or event.get("code") or "")
            observation_reference = str(
                nested.get("observationReference")
                or nested.get("observation_reference")
                or result.get("observationReference")
                or ""
            )
            return {
                "sequence": int(event.get("sequence") or 0),
                "callId": str(event.get("id") or ""),
                "tool": str(event.get("name") or ""),
                "status": str(event.get("status") or ""),
                "summary": str(event.get("summary") or "")[:1000],
                "observationHash": str(event.get("observationHash") or hashlib.sha256(
                    json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
                ).hexdigest()),
                "modelObservation": model_observation,
                **({"actionId": action_id} if action_id else {}),
                **({"code": code} if code else {}),
                **({"observationReference": observation_reference} if observation_reference else {}),
                **({"continuation": continuation} if isinstance(continuation, dict) else {}),
            }
        if event_type == "action_result":
            return {
                "sequence": int(event.get("sequence") or 0),
                "actionId": str(event.get("actionId") or ""),
                "state": str(event.get("state") or ""),
            }
        payload = dict(event)
        payload.pop("result", None)
        return payload

    def _append_pi_session_entry_locked(
        self,
        run: sqlite3.Row,
        entry_id: str,
        entry_type: str,
        payload: dict[str, Any],
        now: str,
    ) -> None:
        session = self.connection.execute(
            "SELECT state_json FROM pi_agent_sessions WHERE session_id=?",
            (run["session_id"],),
        ).fetchone()
        state = json.loads(session["state_json"] or "{}") if session else {}
        current_run = self.connection.execute(
            "SELECT current_leaf_id FROM pi_agent_runs WHERE run_id=?",
            (run["run_id"],),
        ).fetchone()
        parent_id = str(current_run["current_leaf_id"] or "") if current_run else ""
        parent_id = parent_id or None
        inserted = self.connection.execute(
            """INSERT OR IGNORE INTO pi_session_entries(
                 id, parent_id, timestamp, entry_type, session_id, run_id,
                 turn_id, sequence, payload_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                parent_id,
                now,
                entry_type,
                run["session_id"],
                run["run_id"],
                run["turn_id"],
                max(0, int(payload.get("sequence") or payload.get("seq") or 0)),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        if inserted.rowcount:
            state["currentLeafId"] = entry_id
            self.connection.execute(
                "UPDATE pi_agent_runs SET current_leaf_id=?, updated_at=? WHERE run_id=?",
                (entry_id, now, run["run_id"]),
            )
            self.connection.execute(
                "UPDATE pi_agent_sessions SET state_json=?, updated_at=? WHERE session_id=?",
                (json.dumps(state, ensure_ascii=False), now, run["session_id"]),
            )

    def append_pi_agent_events(self, run_id: str, events: list[dict[str, Any]]) -> int:
        with self.lock:
            run = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run:
                raise ValueError("pi_run_not_found")
            last = 0
            for raw_event in events:
                event = _durable_pi_event(raw_event)
                event_now = datetime.now().astimezone().isoformat(timespec="microseconds")
                sequence = int(event.get("sequence") or event.get("seq") or 0)
                if sequence < 1:
                    raise ValueError("pi_event_sequence_invalid")
                last = max(last, sequence)
                payload = json.dumps(event, ensure_ascii=False)
                existing = self.connection.execute(
                    "SELECT payload_json FROM pi_agent_events WHERE run_id=? AND sequence=?",
                    (run_id, sequence),
                ).fetchone()
                if existing:
                    if existing["payload_json"] != payload:
                        raise RuntimeError("pi_event_sequence_conflict")
                    continue
                self.connection.execute(
                    "INSERT INTO pi_agent_events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"pi-event-{uuid.uuid4().hex}",
                        run_id,
                        sequence,
                        str(event.get("type") or "notice"),
                        payload,
                        event_now,
                    ),
                )
                self._append_pi_session_entry_locked(
                    run,
                    f"pi-entry-{run_id}-{sequence:012d}",
                    self._pi_entry_type(str(event.get("type") or "notice")),
                    self._pi_session_payload(event),
                    event_now,
                )
            if last:
                now = _now()
                self.connection.execute(
                    "UPDATE pi_agent_runs SET last_event_sequence=?, updated_at=? WHERE run_id=?",
                    (last, now, run_id),
                )
            self.connection.commit()
        return last

    def record_pi_control(self, run_id: str, control_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if control_type not in {"steering", "follow_up", "model_change", "active_tools_change", "branch_summary"}:
            raise ValueError("pi_control_type_invalid")
        now = datetime.now().astimezone().isoformat(timespec="microseconds")
        entry_id = f"pi-entry-{uuid.uuid4().hex}"
        with self.lock:
            run = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if not run:
                raise ValueError("pi_run_not_found")
            self._append_pi_session_entry_locked(
                run,
                entry_id,
                control_type,
                {key: value for key, value in payload.items() if key not in {"apiKey", "secret"}},
                now,
            )
            self.connection.commit()
        return {"id": entry_id, "type": control_type, "runId": run_id}

    def get_pi_run(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (run_id,),
            ).fetchone()
        if not row:
            raise ValueError("pi_run_not_found")
        return dict(row)

    def list_pi_agent_events(self, run_id: str, after_sequence: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT payload_json FROM pi_agent_events
                   WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?""",
                (run_id, max(0, int(after_sequence)), max(1, min(5000, int(limit)))),
            ).fetchall()
        return [
            _durable_pi_event(json.loads(row["payload_json"]))
            for row in rows
        ]

    def has_pi_tool_result(self, run_id: str, tool_call_id: str, tool_name: str) -> bool:
        """Check the exact durable Tool Result boundary without JSON SQL extensions."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT payload_json FROM pi_agent_events WHERE run_id=? AND event_type='tool_result'",
                (run_id,),
            ).fetchall()
        for row in rows:
            try:
                event = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            if (
                str(event.get("id") or event.get("toolCallId") or "") == tool_call_id
                and str(event.get("name") or event.get("toolName") or "") == tool_name
            ):
                return True
        return False

    def get_pi_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM pi_agent_sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if not row:
                raise ValueError("pi_session_not_found")
            entries = self.connection.execute(
                "SELECT * FROM pi_session_entries WHERE session_id=? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
        item = dict(row)
        item["state"] = json.loads(item.pop("state_json") or "{}")
        item["entries"] = [
            {**dict(entry), "payload": json.loads(entry["payload_json"])}
            for entry in entries
        ]
        for entry in item["entries"]:
            entry.pop("payload_json", None)
        return item

    @staticmethod
    def _project_pi_messages(
        entries: list[dict[str, Any]],
        branch_id: str,
        branch_run: dict[str, Any] | None,
        active_pending_calls: set[tuple[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build a privacy-bounded Pi transcript without orphaned tool results."""
        messages: list[dict[str, Any]] = []
        assistant_content: list[dict[str, Any]] = []
        assistant_timestamp = ""
        assistant_run_id = ""
        assistant_turn_id = ""
        assistant_entry_start_id = ""
        assistant_entry_end_id = ""
        pending: dict[str, dict[str, Any]] = {}
        pending_order: list[str] = []
        active_pending_calls = active_pending_calls or set()

        if branch_run and branch_run.get("parent_run_id"):
            messages.append({
                "role": "branchSummary",
                "summary": (
                    f"Restored branch {branch_id} from parent run "
                    f"{branch_run['parent_run_id']} at persisted event boundary "
                    f"{branch_run.get('forked_from_sequence') or 0}."
                ),
                "fromId": str(branch_run["parent_run_id"]),
                "timestamp": str(branch_run.get("created_at") or _now()),
                "metadata": {"branchId": branch_id},
            })

        def flush_assistant() -> None:
            nonlocal assistant_content, assistant_timestamp, assistant_run_id, assistant_turn_id
            nonlocal assistant_entry_start_id, assistant_entry_end_id
            if not assistant_content:
                return
            messages.append({
                "role": "assistant",
                "content": assistant_content,
                "timestamp": assistant_timestamp or _now(),
                "stopReason": (
                    "toolUse" if any(item.get("type") == "toolCall" for item in assistant_content)
                    else "stop"
                ),
                "metadata": {
                    "branchId": branch_id,
                    "runId": assistant_run_id,
                    "turnId": assistant_turn_id,
                    "entryStartId": assistant_entry_start_id,
                    "entryEndId": assistant_entry_end_id,
                },
            })
            assistant_content = []
            assistant_timestamp = ""
            assistant_run_id = ""
            assistant_turn_id = ""
            assistant_entry_start_id = ""
            assistant_entry_end_id = ""

        def append_tool_result(
            call_id: str,
            tool_name: str,
            payload: dict[str, Any],
            timestamp: str,
            entry_id: str,
            *,
            interrupted: bool = False,
        ) -> None:
            status = "interrupted" if interrupted else str(payload.get("status") or "completed")
            observation = {
                "ok": status == "completed",
                "status": status,
                "summary": str(payload.get("summary") or "")[:1000],
                "observationReference": str(
                    payload.get("observationReference")
                    or f"pi-observation:{payload.get('observationHash') or call_id}"
                ),
                "observationHash": str(payload.get("observationHash") or ""),
                **({"actionId": str(payload["actionId"])} if payload.get("actionId") else {}),
                **({"continuation": _projection_safe(payload["continuation"])} if isinstance(payload.get("continuation"), dict) else {}),
                **({
                    "modelObservation": _projection_safe(payload["modelObservation"]),
                } if isinstance(payload.get("modelObservation"), dict) else {}),
            }
            code = str(payload.get("code") or "")
            if interrupted:
                code = "tool_call_interrupted_by_restart"
                observation["summary"] = "工具调用在结果持久化前中断，恢复时不会自动重复执行。"
            elif not code and status in {"failed", "blocked", "interrupted"}:
                code = f"tool_result_{status}"
            if code:
                observation["code"] = code
            messages.append({
                "role": "toolResult",
                "toolCallId": call_id,
                "toolName": tool_name,
                "content": [{
                    "type": "text",
                    "text": json.dumps(observation, ensure_ascii=False, sort_keys=True),
                }],
                "details": observation,
                "isError": status in {"failed", "interrupted"},
                "timestamp": timestamp or _now(),
                "metadata": {
                    "branchId": branch_id,
                    "entryId": entry_id,
                    "entryStartId": entry_id,
                    "entryEndId": entry_id,
                },
            })

        def interrupt_pending() -> None:
            flush_assistant()
            for call_id in list(pending_order):
                call = pending.pop(call_id, None)
                if not call:
                    continue
                if (call_id, str(call["tool"])) in active_pending_calls:
                    # This is not an orphan: R04 will resume it from the
                    # persisted pending record under the original Run.
                    continue
                append_tool_result(
                    call_id,
                    str(call["tool"]),
                    {},
                    str(call["timestamp"]),
                    str(call.get("entryId") or ""),
                    interrupted=True,
                )
            pending_order.clear()

        for entry in entries:
            entry_type = str(entry.get("entryType") or "")
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            timestamp = str(entry.get("timestamp") or _now())
            run_id = str(entry.get("runId") or "")
            turn_id = str(entry.get("turnId") or "")
            entry_id = str(entry.get("id") or "")

            if entry_type == "task_authorization":
                interrupt_pending()
                objective = str(payload.get("objective") or "").strip()
                if objective:
                    messages.append({
                        "role": "user",
                        "content": objective,
                        "timestamp": timestamp,
                        "metadata": {
                            "branchId": branch_id, "runId": run_id, "turnId": turn_id,
                            "entryId": entry_id, "entryStartId": entry_id, "entryEndId": entry_id,
                        },
                    })
                continue

            if entry_type == "message":
                text = str(payload.get("content") or "")
                if not text:
                    continue
                if pending:
                    interrupt_pending()
                if not assistant_content:
                    assistant_timestamp = timestamp
                    assistant_run_id = run_id
                    assistant_turn_id = turn_id
                    assistant_entry_start_id = entry_id
                assistant_entry_end_id = entry_id
                if assistant_content and assistant_content[-1].get("type") == "text":
                    assistant_content[-1]["text"] = str(assistant_content[-1].get("text") or "") + text
                else:
                    assistant_content.append({"type": "text", "text": text})
                continue

            if entry_type == "tool_call":
                call_id = str(payload.get("callId") or payload.get("id") or "")
                tool_name = str(payload.get("tool") or payload.get("name") or "")
                if not call_id or not tool_name:
                    continue
                if not assistant_content:
                    assistant_timestamp = timestamp
                    assistant_run_id = run_id
                    assistant_turn_id = turn_id
                    assistant_entry_start_id = entry_id
                assistant_entry_end_id = entry_id
                arguments = payload.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = payload.get("input") if isinstance(payload.get("input"), dict) else {}
                assistant_content.append({
                    "type": "toolCall",
                    "id": call_id,
                    "name": tool_name,
                    "arguments": _projection_safe(arguments),
                })
                pending[call_id] = {"tool": tool_name, "timestamp": timestamp, "entryId": entry_id}
                pending_order.append(call_id)
                continue

            if entry_type == "tool_result":
                call_id = str(payload.get("callId") or payload.get("id") or "")
                call = pending.get(call_id)
                tool_name = str(payload.get("tool") or payload.get("name") or "")
                if not call or not call_id or tool_name != str(call.get("tool") or ""):
                    # A result without the exact persisted call is unsafe context.
                    continue
                flush_assistant()
                append_tool_result(call_id, tool_name, payload, timestamp, entry_id)
                pending.pop(call_id, None)
                pending_order = [item for item in pending_order if item != call_id]
                continue

            if entry_type in {"steering", "follow_up"}:
                interrupt_pending()
                text = str(payload.get("text") or "").strip()
                if text:
                    messages.append({
                        "role": "custom",
                        "customType": entry_type,
                        "content": text,
                        "display": True,
                        "details": {"runId": run_id, "turnId": turn_id, "branchId": branch_id},
                        "timestamp": timestamp,
                        "metadata": {"entryId": entry_id, "entryStartId": entry_id, "entryEndId": entry_id},
                    })
                continue

            if entry_type == "action":
                interrupt_pending()
                action_ref = {
                    "actionId": str(payload.get("actionId") or ""),
                    "state": str(payload.get("state") or ""),
                }
                if action_ref["actionId"]:
                    messages.append({
                        "role": "custom",
                        "customType": "persistedActionResult",
                        "content": json.dumps(action_ref, ensure_ascii=False, sort_keys=True),
                        "display": False,
                        "details": action_ref,
                        "timestamp": timestamp,
                        "metadata": {"entryId": entry_id, "entryStartId": entry_id, "entryEndId": entry_id},
                    })
                continue

            if entry_type == "compaction" and payload.get("summary"):
                interrupt_pending()
                messages.append({
                    "role": "compactionSummary",
                    "summary": str(payload.get("summary") or "")[:20_000],
                    "tokensBefore": int(payload.get("tokensBefore") or 0),
                    "timestamp": timestamp,
                    "metadata": {"entryId": entry_id, "entryStartId": entry_id, "entryEndId": entry_id},
                })

        interrupt_pending()
        return messages

    def project_pi_session_context(
        self,
        session_id: str,
        leaf_id: str | None = None,
        upto_run_id: str | None = None,
        upto_sequence: int | None = None,
    ) -> dict[str, Any]:
        """Project one parent-linked lineage; sibling branches never enter context."""
        with self.lock:
            session_row = self.connection.execute(
                "SELECT * FROM pi_agent_sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if not session_row:
                raise ValueError("pi_session_not_found")
            rows = self.connection.execute(
                "SELECT * FROM pi_session_entries WHERE session_id=? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
            session_state = json.loads(session_row["state_json"] or "{}")
            by_id = {str(row["id"]): row for row in rows}
            order = {str(row["id"]): index for index, row in enumerate(rows)}

            selected_leaf = str(leaf_id or "")
            if selected_leaf and selected_leaf not in by_id:
                raise ValueError("pi_session_leaf_not_found")
            target_run_id = str(upto_run_id or "")
            state_leaf = str(session_state.get("currentLeafId") or "")
            if not target_run_id and upto_sequence is not None and state_leaf in by_id:
                target_run_id = str(by_id[state_leaf]["run_id"])
            if not selected_leaf and target_run_id:
                target_run = self.connection.execute(
                    "SELECT current_leaf_id FROM pi_agent_runs WHERE run_id=?",
                    (target_run_id,),
                ).fetchone()
                run_leaf = str(target_run["current_leaf_id"] or "") if target_run else ""
                if upto_sequence is None and run_leaf in by_id:
                    selected_leaf = run_leaf
                else:
                    candidates = [
                        row for row in rows
                        if str(row["run_id"]) == target_run_id
                        and (
                            upto_sequence is None
                            or int(row["sequence"] or 0) == 0
                            or int(row["sequence"] or 0) <= max(0, int(upto_sequence))
                        )
                    ]
                    if candidates:
                        selected_leaf = str(max(candidates, key=lambda row: order[str(row["id"])])["id"])
            if not selected_leaf:
                selected_leaf = state_leaf
            if not selected_leaf and rows:
                selected_leaf = str(rows[-1]["id"])

            lineage_rows: list[sqlite3.Row] = []
            visited: set[str] = set()
            cursor = selected_leaf
            while cursor:
                if cursor in visited:
                    raise RuntimeError("pi_session_lineage_cycle")
                visited.add(cursor)
                row = by_id.get(cursor)
                if row is None:
                    raise RuntimeError("pi_session_lineage_broken")
                lineage_rows.append(row)
                cursor = str(row["parent_id"] or "")
            lineage_rows.reverse()

            branch_id = str(target_run_id or (lineage_rows[-1]["run_id"] if lineage_rows else ""))
            branch_row = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (branch_id,),
            ).fetchone() if branch_id else None

            projected_entries: list[dict[str, Any]] = []
            for row in lineage_rows:
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if str(payload.get("type") or "") in {
                    "reasoning", "reasoning_status", "thinking", "thinking_start",
                    "thinking_delta", "thinking_end",
                }:
                    # Provider reasoning is local UI/recovery data, never model context.
                    continue
                projected_entries.append({
                    "id": str(row["id"]),
                    "parentId": str(row["parent_id"] or "") or None,
                    "timestamp": str(row["timestamp"]),
                    "entryType": str(row["entry_type"]),
                    "sessionId": str(row["session_id"]),
                    "runId": str(row["run_id"]),
                    "turnId": str(row["turn_id"]),
                    "sequence": int(row["sequence"] or payload.get("sequence") or 0),
                    "payload": _projection_safe(payload),
                })

            conversation_id = str(session_row["conversation_id"] or session_id)
            attachment_rows = self.connection.execute(
                """SELECT id, kind, display_name, mime_type, size_bytes, sha256, status, created_at
                   FROM attachments WHERE conversation_id=? ORDER BY created_at, id LIMIT 100""",
                (conversation_id,),
            ).fetchall()
            run_ids = list(dict.fromkeys(str(row["run_id"]) for row in lineage_rows))
            compaction_row = None
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                compaction_candidates = self.connection.execute(
                    f"SELECT * FROM pi_compaction_checkpoints WHERE run_id IN ({placeholders}) "
                    "ORDER BY created_at DESC",
                    tuple(run_ids),
                ).fetchall()
                lineage_ids = {str(row["id"]) for row in lineage_rows}
                compaction_row = next((
                    row for row in compaction_candidates
                    if str(row["checkpoint_entry_id"] or "") in lineage_ids
                    and str(row["kept_from_entry_id"] or "") in lineage_ids
                ), None)
            authorization_row = self.connection.execute(
                "SELECT * FROM task_authorizations WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (branch_id,),
            ).fetchone() if branch_id else None

        focus = _projection_safe(self.get_conversation_focus(conversation_id) or {})
        lineage_calls = {
            (
                str(entry["payload"].get("callId") or entry["payload"].get("id") or ""),
                str(entry["payload"].get("tool") or entry["payload"].get("name") or ""),
            )
            for entry in projected_entries
            if entry["entryType"] == "tool_call" and isinstance(entry.get("payload"), dict)
        }
        lineage_run_ids = {str(row["run_id"]) for row in lineage_rows}
        pending_items = [
            item
            for item in self.get_pending_tool_calls(session_id=session_id, active_only=True)
            if item.get("runId") in lineage_run_ids
            and (str(item.get("toolCallId") or ""), str(item.get("toolName") or "")) in lineage_calls
        ]
        pending = _projection_safe(pending_items[0]) if pending_items else None
        active_pending_calls = {
            (str(item.get("toolCallId") or ""), str(item.get("toolName") or ""))
            for item in pending_items
        }
        action_ids = list(dict.fromkeys(
            str(entry["payload"].get("actionId") or "")
            for entry in projected_entries
            if entry["entryType"] in {"tool_result", "action"}
            and isinstance(entry.get("payload"), dict)
            and entry["payload"].get("actionId")
        ))
        active_actions: list[dict[str, Any]] = []
        for action_id in action_ids:
            try:
                action = self.get_agent_action(action_id)
            except ValueError:
                active_actions.append({"id": action_id, "status": "referenced"})
            else:
                active_actions.append({
                    "id": action_id,
                    "actionType": str(action.get("action_type") or ""),
                    "target": str(action.get("target") or "")[:1000],
                    "status": str(action.get("status") or ""),
                    "completedAt": action.get("completed_at"),
                    "undoState": {
                        "available": str(action.get("status") or "") == "completed",
                        "snapshotCount": len(action.get("snapshots") or []),
                        "changeCount": len(action.get("changes") or []),
                    },
                })

        compaction = None
        if compaction_row:
            compaction = {
                "runId": compaction_row["run_id"],
                "sessionId": compaction_row["session_id"],
                "branchId": compaction_row["branch_id"],
                "cutEntryId": compaction_row["cut_entry_id"],
                "keptFromEntryId": compaction_row["kept_from_entry_id"],
                "summaryVersion": compaction_row["summary_version"],
                "tokensBefore": compaction_row["tokens_before"],
                "tokensAfter": compaction_row["tokens_after"],
                "structuredState": _projection_safe(json.loads(compaction_row["state_json"] or "{}")),
                "checkpointEntryId": compaction_row["checkpoint_entry_id"],
                "summary": str(compaction_row["summary"] or "")[:20_000],
                "createdAt": compaction_row["created_at"],
            }
        message_entries = projected_entries
        if compaction:
            checkpoint_id = str(compaction.get("checkpointEntryId") or "")
            kept_id = str(compaction.get("keptFromEntryId") or "")
            indexes = {str(item["id"]): index for index, item in enumerate(projected_entries)}
            checkpoint_index = indexes.get(checkpoint_id)
            kept_index = indexes.get(kept_id)
            if checkpoint_index is not None and kept_index is not None and kept_index < checkpoint_index:
                # A checkpoint is logically the prefix for the recent messages
                # it retained, even though persistence necessarily happened
                # after those entries were already durable.
                message_entries = [
                    projected_entries[checkpoint_index],
                    *projected_entries[kept_index:checkpoint_index],
                    *projected_entries[checkpoint_index + 1:],
                ]
        authorization = self._decode_task_authorization(authorization_row) if authorization_row else None
        resource_scope = dict((authorization or {}).get("resourceScope") or {})
        explicit_paths = list(resource_scope.get("explicitVaultPaths") or [])
        active_note_path = str(focus.get("activeVaultNotePath") or "")
        if not active_note_path and resource_scope.get("currentNote") is True and explicit_paths:
            active_note_path = str(explicit_paths[0])
        source_tools = {
            "read_note_excerpt", "search_vault", "read_attachment_excerpt",
            "search_public_web", "search_academic_sources", "fetch_public_url",
            "read_workspace_file",
        }
        sources_read: list[dict[str, Any]] = []
        failed_tools: list[dict[str, Any]] = []
        for item in projected_entries:
            if item["entryType"] != "tool_result":
                continue
            payload = item["payload"] if isinstance(item.get("payload"), dict) else {}
            tool_name = str(payload.get("tool") or payload.get("name") or "")
            status = str(payload.get("status") or "completed")
            if tool_name in source_tools and status == "completed":
                sources_read.append({
                    "tool": tool_name,
                    "observationReference": str(payload.get("observationReference") or ""),
                    "observationHash": str(payload.get("observationHash") or ""),
                    "summary": str(payload.get("summary") or "")[:500],
                })
            if status in {"failed", "blocked", "interrupted"}:
                failed_tools.append({
                    "tool": tool_name,
                    "status": status,
                    "code": str(payload.get("code") or "")[:200],
                    "summary": str(payload.get("summary") or "")[:500],
                })
        completed_actions = [
            item for item in active_actions if item.get("status") in {"completed", "applied"}
        ]
        pending_actions = [
            item for item in active_actions if item.get("status") not in {"completed", "applied", "undone", "failed"}
        ]
        if pending:
            pending_actions.append({
                "toolCallId": str(pending.get("toolCallId") or ""),
                "toolName": str(pending.get("toolName") or ""),
                "status": str(pending.get("state") or "pending"),
            })
        task_authorization_summary = None
        if authorization:
            task_authorization_summary = {
                "id": str(authorization.get("id") or ""),
                "runId": str(authorization.get("runId") or ""),
                "networkPolicy": str(authorization.get("networkPolicy") or "deny"),
                "operationScope": list(authorization.get("operationScope") or []),
                "resourceScope": {
                    "currentNote": resource_scope.get("currentNote") is True,
                    "explicitVaultPaths": explicit_paths[:50],
                    "createRoots": list(resource_scope.get("createRoots") or [])[:20],
                    "workspaceId": str(resource_scope.get("workspaceId") or ""),
                    "workspaceIds": list(resource_scope.get("workspaceIds") or [])[:20],
                    "projectPaths": list(resource_scope.get("projectPaths") or [])[:20],
                    "writeScopeState": str(resource_scope.get("writeScopeState") or "unbound"),
                },
            }
        branch_run = dict(branch_row) if branch_row else None
        compaction_state = {
            "goal": str((authorization or {}).get("objective") or "")[:20_000] or None,
            "explicitConstraints": [],
            "conversationFocus": focus or None,
            "activeNote": {"path": active_note_path} if active_note_path else None,
            "activeSelectionReference": str(focus.get("activeSelectionReference") or "") or None,
            "attachments": [
                {
                    "id": row["id"], "kind": row["kind"],
                    "displayName": row["display_name"], "sha256": row["sha256"],
                    "status": row["status"],
                }
                for row in attachment_rows
            ],
            "sourcesRead": sources_read[:100],
            "completedActions": completed_actions,
            "pendingActions": pending_actions,
            "failedTools": failed_tools[:100],
            "activeWorkspace": {
                "workspaceId": str(resource_scope.get("workspaceId") or ""),
                "workspaceIds": list(resource_scope.get("workspaceIds") or [])[:20],
                "projectPaths": list(resource_scope.get("projectPaths") or [])[:20],
            },
            "taskBranch": {
                "branchId": branch_id,
                "parentRunId": str((branch_run or {}).get("parent_run_id") or "") or None,
                "forkedFromSequence": (branch_run or {}).get("forked_from_sequence"),
                "forkedFromEntryId": str((branch_run or {}).get("forked_from_entry_id") or "") or None,
            },
            "taskAuthorization": task_authorization_summary,
            "currentLeafId": selected_leaf or None,
            "branchId": branch_id,
            "actionIds": action_ids,
            "undoState": {
                "actions": [
                    {"id": item.get("id"), **dict(item.get("undoState") or {})}
                    for item in active_actions
                ],
            },
        }
        return {
            "sessionId": session_id,
            "leafId": selected_leaf or None,
            "branchId": branch_id,
            "entries": projected_entries,
            "messages": self._project_pi_messages(
                message_entries, branch_id, branch_run, active_pending_calls,
            ),
            "focus": focus,
            "attachments": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "displayName": row["display_name"],
                    "mimeType": row["mime_type"],
                    "sizeBytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "status": row["status"],
                    "createdAt": row["created_at"],
                }
                for row in attachment_rows
            ],
            "activeActions": active_actions,
            "pending": pending,
            "compaction": compaction,
            "compactionState": _projection_safe(compaction_state),
            "schemaVersion": 1,
        }

    def project_pi_fork_context(
        self,
        run_id: str,
        sequence: int | None = None,
        mode: str = "fork",
    ) -> dict[str, Any]:
        """Resolve an event boundary to one complete persisted entry lineage."""
        if mode not in {"fork", "regenerate"}:
            raise ValueError("pi_fork_mode_invalid")
        run = self.get_pi_run(run_id)
        session_id = str(run["session_id"])
        with self.lock:
            session_row = self.connection.execute(
                "SELECT conversation_id, selected_model FROM pi_agent_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            authorization_row = self.connection.execute(
                "SELECT resource_scope_json FROM task_authorizations WHERE run_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        resource_scope = json.loads(authorization_row["resource_scope_json"] or "{}") \
            if authorization_row else {}
        explicit_paths = resource_scope.get("explicitVaultPaths")
        source_active_note = (
            str(explicit_paths[0])
            if resource_scope.get("currentNote") is True
            and isinstance(explicit_paths, list)
            and explicit_paths
            else ""
        )
        leaf_id = str(run.get("current_leaf_id") or "")
        full = self.project_pi_session_context(
            session_id,
            leaf_id=leaf_id or None,
            upto_run_id=run_id,
        )
        entries = list(full["entries"])
        if not entries:
            raise ValueError("pi_fork_source_empty")

        if sequence is None:
            boundary = len(entries) - 1
        else:
            requested = max(0, int(sequence))
            candidates = [
                index
                for index, entry in enumerate(entries)
                if entry["runId"] == run_id and int(entry.get("sequence") or 0) <= requested
            ]
            if not candidates:
                raise ValueError("pi_fork_event_sequence_not_found")
            boundary = candidates[-1]

        if mode == "regenerate":
            # Drop the selected assistant answer as one delta block, while
            # keeping the preceding user message and any completed Tool Pair.
            answer_index = boundary
            while answer_index >= 0 and entries[answer_index]["entryType"] != "message":
                answer_index -= 1
            if answer_index < 0:
                raise ValueError("pi_regenerate_answer_not_found")
            answer_run = entries[answer_index]["runId"]
            answer_turn = entries[answer_index]["turnId"]
            first_delta = answer_index
            while (
                first_delta > 0
                and entries[first_delta - 1]["entryType"] == "message"
                and entries[first_delta - 1]["runId"] == answer_run
                and entries[first_delta - 1]["turnId"] == answer_turn
            ):
                first_delta -= 1
            boundary = first_delta - 1

        # A fork may never end with an unresolved Tool Call. Move the boundary
        # to immediately before the earliest still-open call.
        open_calls: dict[tuple[str, str], int] = {}
        for index, entry in enumerate(entries[: boundary + 1]):
            payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
            call_id = str(payload.get("callId") or payload.get("id") or "")
            tool_name = str(payload.get("tool") or payload.get("name") or "")
            pair = (call_id, tool_name)
            if entry["entryType"] == "tool_call" and all(pair):
                open_calls[pair] = index
            elif entry["entryType"] == "tool_result" and pair in open_calls:
                open_calls.pop(pair, None)
        if open_calls:
            boundary = min(open_calls.values()) - 1
        if boundary < 0:
            raise ValueError("pi_fork_boundary_before_user_message")

        resolved = entries[boundary]
        projection = self.project_pi_session_context(
            session_id,
            leaf_id=str(resolved["id"]),
        )
        completed_action_ids = sorted({
            str(entry["payload"].get("actionId") or "")
            for entry in projection["entries"]
            if isinstance(entry.get("payload"), dict)
            and entry["payload"].get("actionId")
            and (
                str(entry["payload"].get("status") or "") in {"completed", "applied"}
                or str(entry["payload"].get("state") or "") in {"completed", "applied"}
            )
        })
        projection["completedActionIds"] = completed_action_ids
        return {
            "sourceRunId": run_id,
            "sessionId": session_id,
            "mode": mode,
            "requestedSequence": sequence,
            "resolvedForkEntryId": str(resolved["id"]),
            "resolvedForkSequence": int(resolved.get("sequence") or 0),
            "completedActionIds": completed_action_ids,
            "sourceContext": {
                "conversationId": str(session_row["conversation_id"] or session_id)
                if session_row else session_id,
                "profileId": str(run.get("profile_id") or ""),
                "selectedModel": str(
                    run.get("selected_model")
                    or (session_row["selected_model"] if session_row else "")
                    or ""
                ),
                "providerAdapterVersion": str(run.get("provider_adapter_version") or ""),
                "activeNote": {"path": source_active_note} if source_active_note else None,
            },
            "projection": projection,
            "schemaVersion": 1,
        }

    _PENDING_ACTIVE_STATES = ("pending", "interrupted")

    def save_pending_tool_call(
        self,
        run_id: str,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        permission_request: dict[str, Any],
        recovery_guard: dict[str, Any],
        task_authorization_id: str,
        state: str = "pending",
    ) -> None:
        now = _now()
        with self.lock:
            existing = self.connection.execute(
                "SELECT created_at FROM pi_pending_tool_calls WHERE run_id=? AND tool_call_id=?",
                (run_id, tool_call_id),
            ).fetchone()
            self.connection.execute(
                """INSERT INTO pi_pending_tool_calls(
                     run_id, session_id, turn_id, tool_call_id, tool_name,
                     arguments_json, permission_request_json, recovery_guard_json,
                     task_authorization_id,
                     state, created_at, updated_at, resolved_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                   ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                     session_id=excluded.session_id,
                     turn_id=excluded.turn_id,
                     tool_name=excluded.tool_name,
                     arguments_json=excluded.arguments_json,
                     permission_request_json=excluded.permission_request_json,
                     recovery_guard_json=excluded.recovery_guard_json,
                     task_authorization_id=excluded.task_authorization_id,
                     state=excluded.state,
                     updated_at=excluded.updated_at,
                     resolved_at=NULL""",
                (
                    run_id,
                    session_id,
                    turn_id,
                    tool_call_id,
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False),
                    json.dumps(permission_request, ensure_ascii=False),
                    json.dumps(recovery_guard, ensure_ascii=False, sort_keys=True),
                    task_authorization_id,
                    state,
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
            self.connection.commit()

    def get_pending_tool_calls(
        self,
        run_id: str | None = None,
        session_id: str | None = None,
        states: list[str] | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        if session_id is not None:
            clauses.append("session_id=?")
            params.append(session_id)
        effective_states = states
        if effective_states is None and active_only:
            effective_states = list(self._PENDING_ACTIVE_STATES)
        if effective_states:
            placeholders = ",".join("?" for _ in effective_states)
            clauses.append(f"state IN ({placeholders})")
            params.extend(effective_states)
        if active_only:
            clauses.append("resolved_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.connection.execute(
                f"SELECT * FROM pi_pending_tool_calls{where} ORDER BY created_at, tool_call_id",
                tuple(params),
            ).fetchall()
        return [
            {
                "runId": row["run_id"],
                "sessionId": row["session_id"],
                "turnId": row["turn_id"],
                "toolCallId": row["tool_call_id"],
                "toolName": row["tool_name"],
                "arguments": json.loads(row["arguments_json"]),
                "permissionRequest": json.loads(row["permission_request_json"]),
                "recoveryGuard": json.loads(row["recovery_guard_json"] or "{}"),
                "taskAuthorizationId": row["task_authorization_id"],
                "state": row["state"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "resolvedAt": row["resolved_at"],
            }
            for row in rows
        ]

    def resolve_pending_tool_call(
        self,
        run_id: str,
        tool_call_id: str,
        state: str,
    ) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                """UPDATE pi_pending_tool_calls
                   SET state=?, updated_at=?, resolved_at=?
                   WHERE run_id=? AND tool_call_id=?""",
                (state, now, now, run_id, tool_call_id),
            )
            self.connection.commit()

    def save_compaction_checkpoint(
        self,
        run_id: str,
        session_id: str,
        branch_id: str,
        entry: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        now = _now()
        structured_state = _projection_safe(entry.get("structuredState") or {})
        encoded_state = json.dumps(structured_state, ensure_ascii=False)
        if len(encoded_state.encode("utf-8")) > 256_000:
            raise ValueError("pi_compaction_state_too_large")
        safe_summary = str(_projection_safe(summary))[:20_000]
        cut_entry_id = str(entry.get("cutEntryId") or "")
        kept_from_entry_id = str(entry.get("keptFromEntryId") or "")
        if not cut_entry_id or not kept_from_entry_id or not safe_summary:
            raise ValueError("pi_compaction_boundary_invalid")
        checkpoint_entry_id = f"pi-entry-checkpoint-{uuid.uuid4().hex}"
        with self.lock:
            run = self.connection.execute(
                "SELECT * FROM pi_agent_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if not run or str(run["session_id"]) != session_id or branch_id != run_id:
                raise ValueError("pi_compaction_lineage_mismatch")
            cursor = str(run["current_leaf_id"] or "")
            lineage: set[str] = set()
            while cursor:
                if cursor in lineage:
                    raise RuntimeError("pi_session_lineage_cycle")
                lineage.add(cursor)
                row = self.connection.execute(
                    "SELECT parent_id FROM pi_session_entries WHERE id=? AND session_id=?",
                    (cursor, session_id),
                ).fetchone()
                if not row:
                    raise RuntimeError("pi_session_lineage_broken")
                cursor = str(row["parent_id"] or "")
            if cut_entry_id not in lineage or kept_from_entry_id not in lineage:
                raise ValueError("pi_compaction_boundary_not_in_lineage")
            try:
                self._append_pi_session_entry_locked(
                    run,
                    checkpoint_entry_id,
                    "compaction",
                    {
                        "summary": safe_summary,
                        "cutEntryId": cut_entry_id,
                        "keptFromEntryId": kept_from_entry_id,
                        "summaryVersion": int(entry.get("summaryVersion") or 1),
                        "tokensBefore": int(entry.get("tokensBefore") or 0),
                        "tokensAfter": int(entry.get("tokensAfter") or 0),
                        "structuredState": structured_state,
                        "createdAt": now,
                    },
                    now,
                )
                self.connection.execute(
                    """INSERT INTO pi_compaction_checkpoints(
                         run_id, session_id, branch_id, cut_entry_id, kept_from_entry_id,
                         summary_version, tokens_before, tokens_after, state_json,
                         checkpoint_entry_id, summary, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(run_id) DO UPDATE SET
                         session_id=excluded.session_id,
                         branch_id=excluded.branch_id,
                         cut_entry_id=excluded.cut_entry_id,
                         kept_from_entry_id=excluded.kept_from_entry_id,
                         summary_version=excluded.summary_version,
                         tokens_before=excluded.tokens_before,
                         tokens_after=excluded.tokens_after,
                         state_json=excluded.state_json,
                         checkpoint_entry_id=excluded.checkpoint_entry_id,
                         summary=excluded.summary,
                         created_at=excluded.created_at""",
                    (
                        run_id, session_id, branch_id, cut_entry_id, kept_from_entry_id,
                        int(entry.get("summaryVersion") or 1),
                        int(entry.get("tokensBefore") or 0),
                        int(entry.get("tokensAfter") or 0), encoded_state,
                        checkpoint_entry_id, safe_summary, now,
                    ),
                )
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
        saved = self.get_compaction_checkpoint(run_id)
        assert saved is not None
        return saved

    def get_compaction_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM pi_compaction_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "runId": row["run_id"],
            "sessionId": row["session_id"],
            "branchId": row["branch_id"],
            "cutEntryId": row["cut_entry_id"],
            "keptFromEntryId": row["kept_from_entry_id"],
            "summaryVersion": row["summary_version"],
            "tokensBefore": row["tokens_before"],
            "tokensAfter": row["tokens_after"],
            "structuredState": json.loads(row["state_json"]),
            "checkpointEntryId": row["checkpoint_entry_id"],
            "summary": row["summary"],
            "createdAt": row["created_at"],
        }


    def append_agent_run_event(
        self,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if sequence < 1:
            raise ValueError("agent_event_sequence_invalid")
        event_id = f"agent-event-{uuid.uuid4().hex}"
        now = _now()
        with self.lock:
            existing = self.connection.execute(
                "SELECT payload_json FROM agent_run_events "
                "WHERE run_id=? AND sequence=?",
                (run_id, sequence),
            ).fetchone()
            if existing:
                saved = json.loads(existing["payload_json"])
                if saved != payload:
                    raise RuntimeError("agent_event_sequence_conflict")
                return saved
            self.connection.execute(
                """INSERT INTO agent_run_events(
                     id, run_id, sequence, event_type, payload_json,
                     created_at, schema_version
                   ) VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (
                    event_id,
                    run_id,
                    sequence,
                    event_type,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            self.connection.execute(
                """UPDATE agent_run_checkpoints
                   SET last_event_sequence=?, updated_at=?
                   WHERE run_id=?""",
                (sequence, now, run_id),
            )
            self.connection.commit()
        return payload


    def list_agent_run_events(
        self,
        run_id: str,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """SELECT sequence, event_type, payload_json, created_at
                   FROM agent_run_events
                   WHERE run_id=? AND sequence>?
                   ORDER BY sequence
                   LIMIT ?""",
                (
                    run_id,
                    max(0, int(after_sequence)),
                    max(1, min(2000, int(limit))),
                ),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def append_agent_run_event_next(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS last_sequence "
                "FROM agent_run_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            sequence = int(row["last_sequence"]) + 1
            value = {
                **payload,
                "schemaVersion": 2,
                "seq": sequence,
                "type": event_type,
                "runId": run_id,
            }
            return self.append_agent_run_event(
                run_id,
                sequence,
                event_type,
                value,
            )


    def save_agent_run_checkpoint(
        self,
        run_id: str,
        phase: str,
        state: dict[str, Any],
        *,
        pending_approval_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self.lock:
            row = self.connection.execute(
                "SELECT checkpoint_version, created_at, last_event_sequence "
                "FROM agent_run_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
            version = int(row["checkpoint_version"]) + 1 if row else 1
            created_at = str(row["created_at"]) if row else now
            last_sequence = int(row["last_event_sequence"]) if row else 0
            self.connection.execute(
                """INSERT INTO agent_run_checkpoints(
                     run_id, checkpoint_version, phase, state_json,
                     pending_approval_id, last_event_sequence,
                     created_at, updated_at, schema_version
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                   ON CONFLICT(run_id) DO UPDATE SET
                     checkpoint_version=excluded.checkpoint_version,
                     phase=excluded.phase,
                     state_json=excluded.state_json,
                     pending_approval_id=excluded.pending_approval_id,
                     updated_at=excluded.updated_at""",
                (
                    run_id,
                    version,
                    str(phase)[:80],
                    json.dumps(state, ensure_ascii=False),
                    pending_approval_id,
                    last_sequence,
                    created_at,
                    now,
                ),
            )
            self.connection.commit()
        return self.get_agent_run_checkpoint(run_id) or {}


    def get_agent_run_checkpoint(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "runId": row["run_id"],
            "checkpointVersion": row["checkpoint_version"],
            "phase": row["phase"],
            "state": json.loads(row["state_json"] or "{}"),
            "pendingApprovalId": row["pending_approval_id"],
            "lastEventSequence": row["last_event_sequence"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "schemaVersion": row["schema_version"],
        }

    def _migrate_jobs(self) -> None:
        """Add runtime columns without invalidating an existing local state DB."""
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(jobs)")}
        additions = {
            "current_stage": "TEXT NOT NULL DEFAULT 'queued'",
            "progress": "INTEGER NOT NULL DEFAULT 0",
            "prepared_id": "TEXT",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")

    def _migrate_daily_intelligence(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(curriculum_candidates)")}
        additions = {
            "verification_json": "TEXT NOT NULL DEFAULT '{}'",
            "schema_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(f"ALTER TABLE curriculum_candidates ADD COLUMN {name} {definition}")

    def _migrate_assistant_chat_first(self) -> None:
        """Extend existing local state without replacing conversations or artifacts."""
        additions = {
            "conversations": {"active_task_thread_id": "TEXT"},
            "conversation_messages": {"task_thread_id": "TEXT", "artifact_group_id": "TEXT"},
            "agent_artifacts": {"artifact_group_id": "TEXT"},
        }
        for table, columns_to_add in additions.items():
            columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns_to_add.items():
                if name not in columns:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _migrate_learning_brain(self) -> None:
        additions = {
            "conversations": {
                "active_topic": "TEXT",
                "personalization_enabled": "INTEGER NOT NULL DEFAULT 1",
                "retention_policy": "TEXT NOT NULL DEFAULT 'full'",
                "is_pinned": "INTEGER NOT NULL DEFAULT 0",
            },
            "conversation_messages": {
                "model_profile_id": "TEXT",
                "attachment_references_json": "TEXT NOT NULL DEFAULT '[]'",
            },
        }
        for table, columns_to_add in additions.items():
            columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}
            for name, definition in columns_to_add.items():
                if name not in columns:
                    self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def _record_schema_version(self) -> None:
        now = _now()
        self.connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value, updated_at) VALUES ('schema_version', ?, ?)",
            (str(SCHEMA_VERSION), now),
        )
        self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self.lock: self.connection.close()

    def save_conversation_focus(self, conversation_id: str, focus: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        entities = {name: focus.get(name) for name in ("activeTopic", "activeConcept", "activeMethod", "activeMaterial")}
        with self.lock:
            self.connection.execute(
                """INSERT INTO conversation_focus(
                     conversation_id, active_topic_json, active_concept_json, active_method_json,
                     active_material_json, active_attachment_ids_json, active_artifact_id,
                     active_task_thread_id, active_recommendation_id, active_note_path,
                     active_selection_reference, last_confirmed_intent, last_write_target,
                     confidence, evidence_json, schema_version, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                     active_topic_json=excluded.active_topic_json,
                     active_concept_json=excluded.active_concept_json,
                     active_method_json=excluded.active_method_json,
                     active_material_json=excluded.active_material_json,
                     active_attachment_ids_json=excluded.active_attachment_ids_json,
                     active_artifact_id=excluded.active_artifact_id,
                     active_task_thread_id=excluded.active_task_thread_id,
                     active_recommendation_id=excluded.active_recommendation_id,
                     active_note_path=excluded.active_note_path,
                     active_selection_reference=excluded.active_selection_reference,
                     last_confirmed_intent=excluded.last_confirmed_intent,
                     last_write_target=excluded.last_write_target,
                     confidence=excluded.confidence, evidence_json=excluded.evidence_json,
                     schema_version=excluded.schema_version, updated_at=excluded.updated_at""",
                (
                    conversation_id,
                    *(json.dumps(entities[name], ensure_ascii=False) if entities[name] else None for name in entities),
                    json.dumps(list(focus.get("activeAttachmentIds") or [])[:50], ensure_ascii=False),
                    focus.get("activeArtifactId"), focus.get("activeTaskThreadId"), focus.get("activeRecommendationId"),
                    focus.get("activeVaultNotePath"), focus.get("activeSelectionReference"),
                    focus.get("lastConfirmedIntent"), focus.get("lastWriteTarget"),
                    max(0, min(1, float(focus.get("confidence") or 0))),
                    json.dumps(list(focus.get("evidence") or [])[:50], ensure_ascii=False),
                    int(focus.get("schemaVersion") or 1), now,
                ),
            )
            self.connection.commit()
        return self.get_conversation_focus(conversation_id) or {}

    def get_conversation_focus(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM conversation_focus WHERE conversation_id=?", (conversation_id,)).fetchone()
        if not row:
            return None
        return {
            "conversationId": row["conversation_id"],
            "activeTopic": json.loads(row["active_topic_json"]) if row["active_topic_json"] else None,
            "activeConcept": json.loads(row["active_concept_json"]) if row["active_concept_json"] else None,
            "activeMethod": json.loads(row["active_method_json"]) if row["active_method_json"] else None,
            "activeMaterial": json.loads(row["active_material_json"]) if row["active_material_json"] else None,
            "activeAttachmentIds": json.loads(row["active_attachment_ids_json"] or "[]"),
            "activeArtifactId": row["active_artifact_id"], "activeTaskThreadId": row["active_task_thread_id"],
            "activeRecommendationId": row["active_recommendation_id"], "activeVaultNotePath": row["active_note_path"],
            "activeSelectionReference": row["active_selection_reference"],
            "lastConfirmedIntent": row["last_confirmed_intent"], "lastWriteTarget": row["last_write_target"],
            "confidence": row["confidence"], "evidence": json.loads(row["evidence_json"] or "[]"),
            "schemaVersion": row["schema_version"], "updatedAt": row["updated_at"],
        }

    def save_material_bundle(self, bundle: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
        now = _now(); bundle_id = str(bundle["id"])
        with self.lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO material_bundles(
                     id, conversation_id, title, material_kinds_json, attachment_ids_json,
                     source_ids_json, understanding_json, schema_version, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM material_bundles WHERE id=?), ?), ?)""",
                (bundle_id, bundle["conversationId"], str(bundle.get("title") or "当前材料")[:160],
                 json.dumps(bundle.get("materialKinds", []), ensure_ascii=False),
                 json.dumps(bundle.get("attachmentIds", []), ensure_ascii=False),
                 json.dumps(bundle.get("sourceIds", []), ensure_ascii=False),
                 json.dumps(bundle.get("understanding", {}), ensure_ascii=False),
                 int(bundle.get("schemaVersion") or 1), bundle_id, now, now),
            )
            self.connection.execute("DELETE FROM knowledge_units WHERE bundle_id=?", (bundle_id,))
            for unit in units[:100]:
                self.connection.execute(
                    "INSERT INTO knowledge_units VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (unit["id"], bundle_id, unit["unitType"], str(unit.get("title") or "知识单元")[:160],
                     str(unit.get("contentReference") or "")[:1000], json.dumps(unit.get("provenance", {}), ensure_ascii=False),
                     json.dumps(unit.get("sourceReference", {}), ensure_ascii=False),
                     str(unit.get("verificationStatus") or "needs-verification"),
                     max(0, min(1, float(unit.get("confidence") or 0)))),
                )
            self.connection.commit()
        return self.get_material_bundle(bundle_id)

    def get_material_bundle(self, bundle_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM material_bundles WHERE id=?", (bundle_id,)).fetchone()
            if not row: raise ValueError("material_bundle_not_found")
            units = self.connection.execute("SELECT * FROM knowledge_units WHERE bundle_id=? ORDER BY rowid", (bundle_id,)).fetchall()
        return {
            "id": row["id"], "conversationId": row["conversation_id"], "title": row["title"],
            "materialKinds": json.loads(row["material_kinds_json"]), "attachmentIds": json.loads(row["attachment_ids_json"]),
            "sourceIds": json.loads(row["source_ids_json"]), "understanding": json.loads(row["understanding_json"]),
            "knowledgeUnits": [{"id": unit["id"], "unitType": unit["unit_type"], "title": unit["title"],
                                "contentReference": unit["content_reference"], "provenance": json.loads(unit["provenance_json"]),
                                "sourceReference": json.loads(unit["source_reference_json"]),
                                "verificationStatus": unit["verification_status"], "confidence": unit["confidence"]} for unit in units],
            "schemaVersion": row["schema_version"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    def save_organization_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        now = _now(); plan_id = str(plan["planId"])
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO organization_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM organization_plans WHERE id=?), ?), ?)",
                (plan_id, plan["sourceBundleId"], plan["intent"], plan["primaryAction"],
                 json.dumps(plan, ensure_ascii=False), float(plan.get("confidence") or 0), str(plan.get("status") or "planned"),
                 int(plan.get("schemaVersion") or 1), plan_id, now, now),
            )
            self.connection.execute("DELETE FROM organization_actions WHERE plan_id=?", (plan_id,))
            for action in list(plan.get("actions") or [])[:10]:
                self.connection.execute(
                    "INSERT INTO organization_actions VALUES (?, ?, ?, ?, ?, ?, 'planned', '{}', ?, NULL)",
                    (str(action.get("id") or f"org-action-{uuid.uuid4().hex}"), plan_id, action["action"], action["targetPath"],
                     action["noteType"], action["mergeStrategy"], now),
                )
            self.connection.commit()
        return self.get_organization_plan(plan_id)

    def get_organization_plan(self, plan_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM organization_plans WHERE id=?", (plan_id,)).fetchone()
            if not row: raise ValueError("organization_plan_not_found")
        return json.loads(row["plan_json"])

    def complete_organization_plan(self, plan_id: str, status: str, action_results: list[dict[str, Any]]) -> dict[str, Any]:
        now = _now()
        with self.lock:
            row = self.connection.execute("SELECT plan_json FROM organization_plans WHERE id=?", (plan_id,)).fetchone()
            if not row: raise ValueError("organization_plan_not_found")
            plan = json.loads(row["plan_json"]); plan["status"] = status; plan["executionResults"] = action_results
            self.connection.execute("UPDATE organization_plans SET status=?, plan_json=?, updated_at=? WHERE id=?", (status, json.dumps(plan, ensure_ascii=False), now, plan_id))
            for result in action_results:
                self.connection.execute(
                    "UPDATE organization_actions SET status=?, result_json=?, completed_at=? WHERE id=? AND plan_id=?",
                    (str(result.get("status") or status), json.dumps(result, ensure_ascii=False), now, result.get("organizationActionId") or result.get("actionId"), plan_id),
                )
            self.connection.commit()
        return self.get_organization_plan(plan_id)

    def latest_conversation_summary(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM conversation_summaries WHERE conversation_id=? ORDER BY version DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["sourceMessageIds"] = json.loads(item.pop("source_message_ids_json") or "[]")
        return item

    def save_conversation_summary(self, conversation_id: str, summary: str, source_message_ids: list[str]) -> dict[str, Any]:
        summary = str(summary).strip()[:2000]
        if not summary:
            raise ValueError("conversation_summary_required")
        summary_id, now = f"conv-summary-{uuid.uuid4().hex}", _now()
        with self.lock:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) version FROM conversation_summaries WHERE conversation_id=?",
                (conversation_id,),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            self.connection.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?)",
                (summary_id, conversation_id, version, summary,
                 json.dumps([str(value) for value in source_message_ids[:50]], ensure_ascii=False), now),
            )
            self.connection.commit()
        return {"id": summary_id, "conversation_id": conversation_id, "version": version,
                "summary": summary, "sourceMessageIds": source_message_ids[:50], "created_at": now}

    def upsert_conversation_signal(self, conversation_id: str, signal: dict[str, Any], message_id: str) -> dict[str, Any]:
        fingerprint = str(signal.get("fingerprint") or "")[:64]
        if not fingerprint:
            raise ValueError("signal_fingerprint_required")
        now = _now()
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM conversation_knowledge_signals WHERE conversation_id=? AND fingerprint=?",
                (conversation_id, fingerprint),
            ).fetchone()
            if row:
                message_ids = json.loads(row["message_ids_json"] or "[]")
                if message_id not in message_ids:
                    message_ids.append(message_id)
                recurrence = int(row["recurrence"]) + (0 if message_id in json.loads(row["message_ids_json"] or "[]") else 1)
                confidence = min(.99, max(float(row["confidence"]), float(signal.get("confidence", 0))) + .03)
                self.connection.execute(
                    """UPDATE conversation_knowledge_signals SET recurrence=?, confidence=?,
                       message_ids_json=?, updated_at=? WHERE id=?""",
                    (recurrence, confidence, json.dumps(message_ids[-50:], ensure_ascii=False), now, row["id"]),
                )
                signal_id = str(row["id"])
            else:
                signal_id = f"signal-{uuid.uuid4().hex}"
                self.connection.execute(
                    """INSERT INTO conversation_knowledge_signals(
                         id, conversation_id, fingerprint, signal_type, topic, detail,
                         confidence, recurrence, message_ids_json, status, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'active', ?, ?)""",
                    (signal_id, conversation_id, fingerprint, str(signal.get("signalType") or "topic")[:40],
                     str(signal.get("topic") or "当前主题")[:160], str(signal.get("detail") or "")[:500],
                     max(0, min(1, float(signal.get("confidence", 0)))),
                     json.dumps([message_id], ensure_ascii=False), now, now),
                )
            self.connection.commit()
            stored = self.connection.execute("SELECT * FROM conversation_knowledge_signals WHERE id=?", (signal_id,)).fetchone()
        item = dict(stored)
        item["messageIds"] = json.loads(item.pop("message_ids_json") or "[]")
        item["signalType"] = item.pop("signal_type")
        return item

    def list_conversation_signals(self, conversation_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(500, int(limit)))
        with self.lock:
            if conversation_id:
                rows = self.connection.execute(
                    """SELECT * FROM conversation_knowledge_signals
                       WHERE conversation_id=? AND status='active'
                       ORDER BY recurrence DESC, confidence DESC, updated_at DESC LIMIT ?""",
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """SELECT * FROM conversation_knowledge_signals WHERE status='active'
                       ORDER BY recurrence DESC, confidence DESC, updated_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["messageIds"] = json.loads(item.pop("message_ids_json") or "[]")
            item["signalType"] = item.pop("signal_type")
            items.append(item)
        return items

    def save_direction_prediction(self, prediction: dict[str, Any]) -> dict[str, Any]:
        prediction_id, now = str(prediction.get("id") or f"direction-{uuid.uuid4().hex}"), _now()
        with self.lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO direction_predictions(
                     id, horizon, title, rationale_json, evidence_json, confidence,
                     state, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM direction_predictions WHERE id=?), ?), ?)""",
                (prediction_id, str(prediction.get("horizon") or "near"), str(prediction.get("title") or "学习方向")[:160],
                 json.dumps(prediction.get("rationale", []), ensure_ascii=False),
                 json.dumps(prediction.get("evidence", []), ensure_ascii=False),
                 max(0, min(1, float(prediction.get("confidence", 0)))),
                 str(prediction.get("state") or "active"), prediction_id, now, now),
            )
            self.connection.commit()
        return {**prediction, "id": prediction_id, "createdAt": now, "updatedAt": now}

    def list_direction_predictions(self, state: str = "active", limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM direction_predictions WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (state, max(1, min(100, int(limit)))),
            ).fetchall()
        return [{"id": row["id"], "horizon": row["horizon"], "title": row["title"],
                 "rationale": json.loads(row["rationale_json"]), "evidence": json.loads(row["evidence_json"]),
                 "confidence": row["confidence"], "state": row["state"],
                 "createdAt": row["created_at"], "updatedAt": row["updated_at"]} for row in rows]

    def replace_daily_plan(
        self, plan_date: str, budget_minutes: int, items: list[dict[str, Any]],
        *, state: str = "active", expected_version: int | None = None,
    ) -> dict[str, Any]:
        now = _now()
        budget_minutes = max(5, min(24 * 60, int(budget_minutes)))
        with self.lock:
            current = self.connection.execute("SELECT * FROM daily_plans WHERE plan_date=?", (plan_date,)).fetchone()
            current_version = int(current["version"]) if current else 0
            if expected_version is not None and current_version != int(expected_version):
                raise RuntimeError("daily_plan_version_conflict")
            version = current_version + 1
            self.connection.execute(
                """INSERT INTO daily_plans(plan_date, budget_minutes, state, version, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(plan_date) DO UPDATE SET budget_minutes=excluded.budget_minutes,
                     state=excluded.state, version=excluded.version, updated_at=excluded.updated_at""",
                (plan_date, budget_minutes, state, version, current["created_at"] if current else now, now),
            )
            self.connection.execute("DELETE FROM daily_recommendations WHERE plan_date=?", (plan_date,))
            for position, item in enumerate(items):
                recommendation_id = str(item.get("recommendationId") or item.get("id") or "")
                if not recommendation_id:
                    continue
                row_id = str(item.get("rowId") or f"daily-{uuid.uuid4().hex}")
                self.connection.execute(
                    """INSERT INTO daily_recommendations(
                         id, plan_date, recommendation_id, position, minutes, state,
                         fixed, source, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_id, plan_date, recommendation_id, position,
                     max(1, min(360, int(item.get("minutes", 10)))), str(item.get("state") or "planned"),
                     int(bool(item.get("fixed"))), str(item.get("source") or "deterministic"), now, now),
                )
            self.connection.commit()
        return self.get_daily_plan(plan_date) or {}

    def get_daily_plan(self, plan_date: str) -> dict[str, Any] | None:
        with self.lock:
            plan = self.connection.execute("SELECT * FROM daily_plans WHERE plan_date=?", (plan_date,)).fetchone()
            if not plan:
                return None
            rows = self.connection.execute(
                "SELECT * FROM daily_recommendations WHERE plan_date=? ORDER BY position", (plan_date,),
            ).fetchall()
        return {
            "date": plan["plan_date"], "budgetMinutes": plan["budget_minutes"],
            "state": plan["state"], "version": plan["version"],
            "items": [{"rowId": row["id"], "recommendationId": row["recommendation_id"],
                       "position": row["position"], "minutes": row["minutes"],
                       "state": row["state"], "fixed": bool(row["fixed"]), "source": row["source"]}
                      for row in rows],
            "createdAt": plan["created_at"], "updatedAt": plan["updated_at"],
        }

    def set_daily_recommendation_state(self, plan_date: str, recommendation_id: str, state: str) -> bool:
        if state not in {"planned", "in_progress", "paused", "completed", "skipped"}:
            raise ValueError("invalid_daily_recommendation_state")
        now = _now()
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE daily_recommendations SET state=?, updated_at=? WHERE plan_date=? AND recommendation_id=?",
                (state, now, plan_date, recommendation_id),
            )
            if cursor.rowcount:
                self.connection.execute("UPDATE daily_plans SET version=version+1, updated_at=? WHERE plan_date=?", (now, plan_date))
            self.connection.commit()
        return bool(cursor.rowcount)

    def save_daily_adjustment(
        self, adjustment_type: str, target_id: str, before: dict[str, Any],
        after: dict[str, Any], reason: str,
    ) -> dict[str, Any]:
        action_id, now = f"daily-action-{uuid.uuid4().hex}", _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO daily_plan_adjustments VALUES (?, ?, ?, ?, ?, ?, 'applied', ?, NULL)",
                (action_id, adjustment_type, target_id or None,
                 json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False),
                 str(reason)[:500], now),
            )
            self.connection.commit()
        return {"actionId": action_id, "type": adjustment_type, "targetId": target_id,
                "before": before, "after": after, "reason": reason, "state": "applied", "createdAt": now}

    def get_daily_adjustment(self, action_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM daily_plan_adjustments WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise ValueError("daily_adjustment_not_found")
        return {"actionId": row["id"], "type": row["adjustment_type"], "targetId": row["target_id"],
                "before": json.loads(row["before_json"]), "after": json.loads(row["after_json"]),
                "reason": row["reason"], "state": row["state"], "createdAt": row["created_at"],
                "undoneAt": row["undone_at"]}

    def latest_daily_adjustment(self, state: str = "applied") -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT id FROM daily_plan_adjustments WHERE state=? ORDER BY created_at DESC LIMIT 1",
                (state,),
            ).fetchone()
        return self.get_daily_adjustment(str(row["id"])) if row else None

    def mark_daily_adjustment_undone(self, action_id: str) -> None:
        with self.lock:
            row = self.connection.execute("SELECT state FROM daily_plan_adjustments WHERE id=?", (action_id,)).fetchone()
            if not row or row["state"] != "applied":
                raise ValueError("daily_adjustment_not_undoable")
            self.connection.execute(
                "UPDATE daily_plan_adjustments SET state='undone', undone_at=? WHERE id=?", (_now(), action_id),
            )
            self.connection.commit()

    def upsert_web_source(self, source: dict[str, Any]) -> None:
        metadata = {
            "author": source.get("author", ""), "publishedAt": source.get("publishedAt", ""),
            "domain": source.get("domain", ""), "relevanceScore": source.get("relevanceScore", 0),
            "supportedClaims": source.get("supportedClaims", []),
            "promptInjectionDetected": bool(source.get("promptInjectionDetected")),
            "promptInjectionMarkers": source.get("promptInjectionMarkers", []), "bytes": source.get("bytes", 0),
        }
        with self.lock:
            self.connection.execute(
                """INSERT INTO web_sources(
                     id, canonical_url, title, source_type, content_hash, fetched_at,
                     quality, metadata_json, cache_reference
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(canonical_url) DO UPDATE SET title=excluded.title,
                     source_type=excluded.source_type, content_hash=excluded.content_hash,
                     fetched_at=excluded.fetched_at, quality=excluded.quality,
                     metadata_json=excluded.metadata_json, cache_reference=excluded.cache_reference""",
                (str(source["id"]), str(source["canonicalUrl"]), str(source.get("title") or "网页资料")[:300],
                 str(source.get("sourceType") or "public_web"), str(source["contentHash"]),
                 str(source.get("fetchedAt") or _now()), float(source.get("qualityScore", 0)),
                 json.dumps(metadata, ensure_ascii=False), str(source.get("cacheReference") or "")),
            )
            self.connection.commit()

    def list_web_sources(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM web_sources ORDER BY fetched_at DESC LIMIT ?", (max(1, min(500, int(limit))),),
            ).fetchall()
        items = []
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            items.append({"id": row["id"], "canonicalUrl": row["canonical_url"], "title": row["title"],
                          "sourceType": row["source_type"], "contentHash": row["content_hash"],
                          "fetchedAt": row["fetched_at"], "qualityScore": row["quality"],
                          "cacheReference": row["cache_reference"], **metadata})
        return items

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.lock:
            row = self.connection.execute("SELECT value_json FROM agent_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else default

    def set_setting(self, key: str, value: Any) -> Any:
        with self.lock:
            self.connection.execute(
                """INSERT INTO agent_settings(key, value_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), _now()),
            )
            self.connection.commit()
        return value

    def create_agent_action(
        self, action_type: str, target: str, risk_level: str, details: dict[str, Any],
        status: str = "running",
    ) -> str:
        action_id, now = f"agent-action-{uuid.uuid4().hex}", _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO agent_actions VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (action_id, action_type, target, risk_level, status,
                 json.dumps(details, ensure_ascii=False), now),
            )
            self.connection.commit()
        return action_id

    def record_file_snapshot(self, action_id: str, relative_path: str, content_hash: str, snapshot_reference: str) -> str:
        snapshot_id = f"snapshot-{uuid.uuid4().hex}"
        with self.lock:
            self.connection.execute(
                "INSERT INTO file_snapshots VALUES (?, ?, ?, ?, ?, ?)",
                (snapshot_id, action_id, relative_path, content_hash, snapshot_reference, _now()),
            )
            self.connection.commit()
        return snapshot_id

    def record_file_change(self, action_id: str, relative_path: str, before_hash: str | None, after_hash: str | None, diff_summary: str) -> str:
        change_id = f"file-change-{uuid.uuid4().hex}"
        with self.lock:
            self.connection.execute(
                "INSERT INTO file_change_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                (change_id, action_id, relative_path, before_hash, after_hash, str(diff_summary)[:2000], _now()),
            )
            self.connection.commit()
        return change_id

    def complete_agent_action(self, action_id: str, status: str, details_patch: dict[str, Any] | None = None) -> None:
        with self.lock:
            row = self.connection.execute("SELECT details_json FROM agent_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ValueError("agent_action_not_found")
            details = json.loads(row["details_json"] or "{}")
            details.update(details_patch or {})
            self.connection.execute(
                "UPDATE agent_actions SET status=?, details_json=?, completed_at=? WHERE id=?",
                (status, json.dumps(details, ensure_ascii=False), _now(), action_id),
            )
            self.connection.commit()

    def get_agent_action(self, action_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM agent_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ValueError("agent_action_not_found")
            snapshots = self.connection.execute("SELECT * FROM file_snapshots WHERE action_id=?", (action_id,)).fetchall()
            changes = self.connection.execute("SELECT * FROM file_change_log WHERE action_id=?", (action_id,)).fetchall()
        item = dict(row); item["details"] = json.loads(item.pop("details_json") or "{}")
        item["snapshots"] = [dict(value) for value in snapshots]; item["changes"] = [dict(value) for value in changes]
        return item

    def find_agent_action_for_change_set(self, change_set_id: str) -> dict[str, Any] | None:
        """Return the newest exact action match without trusting a LIKE-only hit."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT id FROM agent_actions WHERE action_type='apply_vault_change' "
                "AND details_json LIKE ? ORDER BY created_at DESC LIMIT 50",
                (f'%"changeSetId": "{change_set_id}"%',),
            ).fetchall()
        for row in rows:
            action = self.get_agent_action(str(row["id"]))
            if str(action.get("details", {}).get("changeSetId") or "") == change_set_id:
                return action
        return None

    def list_agent_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM agent_actions ORDER BY created_at DESC LIMIT ?", (max(1, min(500, int(limit))),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["details"] = json.loads(item.pop("details_json") or "{}"); result.append(item)
        return result

    def create_job(self, kind: str, payload: dict[str, Any]) -> str:
        job_id, now = uuid.uuid4().hex, _now()
        with self.lock:
            self.connection.execute(
                """INSERT INTO jobs(
                     job_id, kind, state, payload_json, result_json, error,
                     current_stage, progress, prepared_id, cancel_requested,
                     created_at, updated_at
                   ) VALUES (?, ?, 'queued', ?, NULL, NULL, 'queued', 0, NULL, 0, ?, ?)""",
                (job_id, kind, json.dumps(payload, ensure_ascii=False), now, now),
            )
            self._append_job_event(job_id, "queued", "queued", 0, {})
            self.connection.commit(); return job_id

    def _append_job_event(self, job_id: str, state: str, stage: str, progress: int, details: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO job_events(job_id, state, stage, progress, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, state, stage, progress, json.dumps(details, ensure_ascii=False), _now()),
        )

    def update_job(
        self, job_id: str, state: str, *, result: Any = None, error: str | None = None,
        stage: str | None = None, progress: int | None = None, prepared_id: str | None = None,
    ) -> None:
        with self.lock:
            current = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not current: raise RuntimeError(f"Unknown job: {job_id}")
            next_stage = stage or state
            next_progress = int(current["progress"] if progress is None else max(0, min(100, progress)))
            next_result = current["result_json"] if result is None else json.dumps(result, ensure_ascii=False)
            next_prepared = prepared_id if prepared_id is not None else current["prepared_id"]
            self.connection.execute(
                """UPDATE jobs SET state=?, result_json=?, error=?, current_stage=?,
                   progress=?, prepared_id=?, updated_at=? WHERE job_id=?""",
                (state, next_result, error, next_stage, next_progress, next_prepared, _now(), job_id),
            )
            self._append_job_event(job_id, state, next_stage, next_progress, {"error": error} if error else {})
            self.connection.commit()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.connection.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def list_job_events(self, job_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT * FROM job_events WHERE job_id=? ORDER BY event_id", (job_id,)
            )]

    def next_queued(self) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
            return dict(row) if row else None

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown job: {job_id}")
            if row["state"] == "queued":
                self.connection.execute("UPDATE jobs SET state='cancelled', current_stage='cancelled', cancel_requested=1, updated_at=? WHERE job_id=?", (_now(), job_id))
                self._append_job_event(job_id, "cancelled", "cancelled", int(row["progress"]), {})
            elif row["state"] == "running":
                self.connection.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE job_id=?", (_now(), job_id))
                self._append_job_event(job_id, "running", str(row["current_stage"]), int(row["progress"]), {"cancel_requested": True})
            else: raise RuntimeError(f"Job cannot be cancelled from state {row['state']}")
            self.connection.commit()
            return dict(self.connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())

    def delete_job_record(self, job_id: str) -> None:
        with self.lock:
            row = self.connection.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown job: {job_id}")
            if row["state"] not in {"failed", "cancelled"}: raise RuntimeError("Only failed or cancelled job records may be deleted")
            self.connection.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
            self.connection.commit()

    def recover_interrupted(self) -> int:
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE jobs SET state='queued', current_stage='queued', error='recovered after interrupted runtime', updated_at=? WHERE state='running'",
                (_now(),),
            )
            recovered_jobs = cursor.rowcount
            now = _now()
            transient = ("understanding", "planning", "awaiting_authorization", "running", "verifying")
            placeholders = ",".join("?" for _ in transient)
            self.connection.execute(
                f"UPDATE brain_steps SET status='failed', completed_at=?, error_code='brain_runtime_interrupted' "
                f"WHERE status='running' AND run_id IN (SELECT id FROM brain_runs WHERE status IN ({placeholders}))",
                (now, *transient),
            )
            self.connection.execute(
                f"UPDATE brain_runs SET status='failed', error_code='brain_runtime_interrupted', "
                f"error_message='本地 Runtime 上次意外中断；可以安全重试', completed_at=?, updated_at=? "
                f"WHERE status IN ({placeholders})",
                (now, now, *transient),
            )
            # A Pi run lives partly in the Obsidian renderer process.  Once the
            # local Runtime has restarted, a row still marked running cannot
            # have a live model stream behind it and must not remain a ghost
            # task forever.
            self.connection.execute(
                "UPDATE pi_agent_runs SET status='failed', error_code='pi_runtime_interrupted', "
                "completed_at=?, updated_at=? WHERE status='running'",
                (now, now),
            )
            self.connection.execute(
                "UPDATE task_authorizations SET status='expired', updated_at=? "
                "WHERE status='active' AND run_id IN "
                "(SELECT run_id FROM pi_agent_runs WHERE error_code='pi_runtime_interrupted')",
                (now,),
            )
            self.connection.execute(
                "UPDATE pi_agent_sessions SET status='failed', updated_at=? "
                "WHERE session_id IN "
                "(SELECT session_id FROM pi_agent_runs WHERE error_code='pi_runtime_interrupted')",
                (now,),
            )
            self.connection.commit(); return recovered_jobs

    def audit(self, event: str, entity_id: str, details: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO audit(event, entity_id, details_json, created_at) VALUES (?, ?, ?, ?)",
                (event, entity_id, json.dumps(details, ensure_ascii=False), _now()),
            )
            self.connection.commit()

    def upsert_recommendation(self, recommendation: dict[str, Any]) -> dict[str, Any]:
        recommendation_id = str(recommendation["id"])
        now = _now()
        with self.lock:
            self.connection.execute(
                """INSERT INTO recommendations(
                     recommendation_id, kind, artifact_id, due_date, score, state,
                     details_json, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(recommendation_id) DO UPDATE SET
                     kind=excluded.kind, artifact_id=excluded.artifact_id,
                     due_date=excluded.due_date, score=excluded.score,
                     state=excluded.state, details_json=excluded.details_json""",
                (recommendation_id, str(recommendation.get("kind") or "learn"), recommendation.get("artifactId"),
                 recommendation.get("dueDate"), float(recommendation.get("score", 75)),
                 str(recommendation.get("state") or "active"),
                 json.dumps(recommendation, ensure_ascii=False), now),
            )
            self.connection.commit()
        return self.get_persisted_recommendation(recommendation_id) or recommendation

    def get_persisted_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM recommendations WHERE recommendation_id=?", (recommendation_id,)
            ).fetchone()
        if not row:
            return None
        details = json.loads(row["details_json"] or "{}")
        return {**details, "id": row["recommendation_id"], "kind": row["kind"],
                "artifactId": row["artifact_id"], "score": row["score"], "state": row["state"],
                "createdAt": row["created_at"]}

    def list_persisted_recommendations(self, state: str = "active") -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT recommendation_id FROM recommendations WHERE state=? ORDER BY created_at DESC", (state,)
            ).fetchall()
        return [item for row in rows if (item := self.get_persisted_recommendation(str(row["recommendation_id"]))) is not None]

    def set_persisted_recommendation_state(self, recommendation_id: str, state: str) -> bool:
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE recommendations SET state=? WHERE recommendation_id=?", (state, recommendation_id)
            )
            self.connection.commit()
            return bool(cursor.rowcount)

    def add_recommendation_feedback(self, recommendation_id: str, action: str, details: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO recommendation_feedback(recommendation_id, action, details_json, created_at) VALUES (?, ?, ?, ?)",
                (recommendation_id, action, json.dumps(details, ensure_ascii=False), _now()),
            )
            self.connection.commit()

    def recommendation_feedback(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT * FROM recommendation_feedback ORDER BY feedback_id DESC"
            )]

    def undo_recommendation_feedback(self, recommendation_id: str) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT feedback_id FROM recommendation_feedback WHERE recommendation_id=? ORDER BY feedback_id DESC LIMIT 1",
                (recommendation_id,),
            ).fetchone()
            if not row: return False
            self.connection.execute("DELETE FROM recommendation_feedback WHERE feedback_id=?", (row["feedback_id"],))
            self.connection.commit(); return True

    @staticmethod
    def _study_session_row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json") or "{}")
        return item

    def get_study_session(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM study_sessions WHERE session_id=?", (session_id,)).fetchone()
        if not row: raise RuntimeError(f"Unknown study session: {session_id}")
        return self._study_session_row(row)

    def resumable_study_session(self, recommendation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM study_sessions WHERE recommendation_id=? AND state IN ('active','paused','quiz','error') ORDER BY updated_at DESC LIMIT 1",
                (recommendation_id,),
            ).fetchone()
        return self._study_session_row(row) if row else None

    def create_study_session(self, recommendation_id: str, details: dict[str, Any]) -> str:
        session_id, now = uuid.uuid4().hex, _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO study_sessions VALUES (?, ?, 'active', ?, ?, NULL, ?)",
                (session_id, recommendation_id, now, now, json.dumps(details, ensure_ascii=False)),
            )
            self.connection.commit()
        return session_id

    def update_study_session(self, session_id: str, state: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"active", "paused", "quiz", "completing", "completed", "abandoned", "error"}
        if state not in allowed: raise ValueError("invalid_study_session_state")
        with self.lock:
            row = self.connection.execute("SELECT * FROM study_sessions WHERE session_id=?", (session_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown study session: {session_id}")
            details = json.loads(row["details_json"] or "{}")
            progress = dict(details.get("progress") or {})
            progress.update(dict(patch.get("progress") or {}))
            details.update({key: value for key, value in patch.items() if key != "progress"})
            details["progress"] = progress
            now = _now()
            self.connection.execute(
                "UPDATE study_sessions SET state=?, updated_at=?, completed_at=?, details_json=? WHERE session_id=?",
                (state, now, now if state == "completed" else (None if row["state"] == "completed" else row["completed_at"]), json.dumps(details, ensure_ascii=False), session_id),
            )
            self.connection.commit()
            updated = self.connection.execute("SELECT * FROM study_sessions WHERE session_id=?", (session_id,)).fetchone()
        return self._study_session_row(updated)

    def complete_study_session(self, session_id: str, details: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM study_sessions WHERE session_id=?", (session_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown study session: {session_id}")
            if row["state"] == "completed": return self._study_session_row(row)
            if row["state"] not in {"active", "paused", "quiz", "completing"}: raise RuntimeError("Study session cannot be completed")
            now = _now()
            current = json.loads(row["details_json"] or "{}")
            current.update(details)
            self.connection.execute(
                "UPDATE study_sessions SET state='completed', updated_at=?, completed_at=?, details_json=? WHERE session_id=?",
                (now, now, json.dumps(current, ensure_ascii=False), session_id),
            )
            self.connection.commit()
            return self._study_session_row(self.connection.execute("SELECT * FROM study_sessions WHERE session_id=?", (session_id,)).fetchone())

    def upsert_model_profile(self, profile: dict[str, Any]) -> None:
        now = _now()
        with self.lock:
            existing = self.connection.execute("SELECT created_at FROM model_profiles WHERE profile_id=?", (profile["id"],)).fetchone()
            self.connection.execute(
                """INSERT OR REPLACE INTO model_profiles(
                     profile_id, display_name, provider_type, base_url, api_key_reference,
                     default_model, available_models_json, settings_json, enabled, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (profile["id"], profile["displayName"], profile["providerType"], profile["baseUrl"],
                 profile["apiKeyReference"], profile["defaultModel"], json.dumps(profile.get("availableModels", []), ensure_ascii=False),
                 json.dumps(profile.get("settings", {}), ensure_ascii=False), int(bool(profile.get("enabled", True))),
                 existing["created_at"] if existing else now, now),
            )
            self.connection.commit()

    def list_model_profiles(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM model_profiles ORDER BY display_name COLLATE NOCASE").fetchall()
        return [{
            "id": row["profile_id"], "displayName": row["display_name"], "providerType": row["provider_type"],
            "baseUrl": row["base_url"], "apiKeyReference": row["api_key_reference"], "defaultModel": row["default_model"],
            "availableModels": json.loads(row["available_models_json"]), "settings": json.loads(row["settings_json"]),
            "enabled": bool(row["enabled"]), "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        } for row in rows]

    def delete_model_profile(self, profile_id: str) -> None:
        with self.lock:
            if not self.connection.execute("SELECT 1 FROM model_profiles WHERE profile_id=?", (profile_id,)).fetchone():
                raise RuntimeError("Model profile not found")
            self.connection.execute("DELETE FROM model_profiles WHERE profile_id=?", (profile_id,))
            self.connection.commit()

    def model_routing(self) -> dict[str, dict[str, str | None]]:
        with self.lock:
            rows = self.connection.execute("SELECT task, profile_id, model_override FROM model_routing ORDER BY task").fetchall()
        return {row["task"]: {"profileId": row["profile_id"], "modelOverride": row["model_override"]} for row in rows}

    def set_model_routing(self, routes: dict[str, dict[str, Any]]) -> None:
        now = _now()
        with self.lock:
            for task, route in routes.items():
                self.connection.execute(
                    "INSERT OR REPLACE INTO model_routing(task, profile_id, model_override, updated_at) VALUES (?, ?, ?, ?)",
                    (task, route.get("profileId") or None, route.get("modelOverride") or None, now),
                )
            self.connection.commit()

    def schema_version(self) -> int:
        with self.lock:
            return int(self.connection.execute("PRAGMA user_version").fetchone()[0])

    def create_brain_run(self, run_id: str, request: Any, retry_of: str | None = None) -> None:
        now = _now()
        private_root = self.path.parent / "brain-requests"
        private_root.mkdir(parents=True, exist_ok=True)
        private_path = private_root / f"{run_id}.json"
        temp_path = private_root / f".{run_id}.{uuid.uuid4().hex}.tmp"
        payload = request.public()
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_path, private_path)
        public_request = dict(payload)
        for key in ("text", "selected_text"):
            text = str(public_request.get(key, ""))
            public_request[key] = {"chars": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest()[:16]}
        public_request["private_request_path"] = str(private_path.relative_to(self.path.parent))
        with self.lock:
            self.connection.execute(
                """INSERT INTO brain_runs(
                     id, request_id, correlation_id, idempotency_key, source, status,
                     request_json, retry_of, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)""",
                (run_id, request.request_id, request.correlation_id, request.idempotency_key or None,
                 request.source, json.dumps(public_request, ensure_ascii=False), retry_of, now, now),
            )
            self.connection.commit()

    def load_brain_request(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT request_json FROM brain_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError(f"Unknown brain run: {run_id}")
        public = json.loads(row["request_json"])
        relative = str(public.get("private_request_path", ""))
        path = (self.path.parent / relative).resolve()
        root = (self.path.parent / "brain-requests").resolve()
        if not relative or not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise RuntimeError("Brain request payload is unavailable")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("Brain request payload is invalid")
        for key in ("text", "selected_text"):
            text = str(value.get(key, "")); expected = public.get(key, {})
            if len(text) != int(expected.get("chars", -1)) or hashlib.sha256(text.encode()).hexdigest()[:16] != str(expected.get("sha256", "")):
                raise RuntimeError("Brain request payload integrity check failed")
        return value

    def find_brain_run_by_idempotency(self, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute("SELECT id FROM brain_runs WHERE idempotency_key=?", (key,)).fetchone()
            return str(row["id"]) if row else None

    def update_brain_run(self, run_id: str, status: str, current_step: str | None = None) -> None:
        with self.lock:
            cursor = self.connection.execute(
                "UPDATE brain_runs SET status=?, current_step=?, updated_at=? WHERE id=?",
                (status, current_step, _now(), run_id),
            )
            if not cursor.rowcount: raise RuntimeError(f"Unknown brain run: {run_id}")
            self.connection.commit()

    def set_brain_intent(self, run_id: str, intent: Any) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE brain_runs SET primary_intent=?, secondary_intents_json=?, updated_at=? WHERE id=?",
                (intent.primary_intent, json.dumps(list(intent.secondary_intents), ensure_ascii=False), _now(), run_id),
            )
            self.connection.commit()

    def set_brain_model(self, run_id: str, profile_id: str) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE brain_runs SET model_profile_id=?, updated_at=? WHERE id=?",
                (profile_id, _now(), run_id),
            )
            self.connection.commit()

    def set_brain_plan(self, run_id: str, plan: Any) -> None:
        value = plan.to_dict()
        goal = str(value.get("goal", ""))
        value["goal"] = {"chars": len(goal), "sha256": hashlib.sha256(goal.encode()).hexdigest()[:16]}
        with self.lock:
            self.connection.execute(
                "UPDATE brain_runs SET plan_json=?, updated_at=? WHERE id=?",
                (json.dumps(value, ensure_ascii=False), _now(), run_id),
            )
            self.connection.commit()

    def finish_brain_run(self, run_id: str, status: str, result: dict[str, Any]) -> None:
        now = _now()
        private_root = self.path.parent / "brain-results"
        private_root.mkdir(parents=True, exist_ok=True)
        private_path = private_root / f"{run_id}.json"
        encoded = json.dumps(result, ensure_ascii=False).encode()
        temp_path = private_root / f".{run_id}.{uuid.uuid4().hex}.tmp"
        with temp_path.open("wb") as handle:
            handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_path, private_path)
        public_result = {
            "private_result_path": str(private_path.relative_to(self.path.parent)),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "result_count": len(result.get("results", [])),
            "reflection": result.get("reflection", {}),
        }
        with self.lock:
            self.connection.execute(
                "UPDATE brain_runs SET status=?, result_json=?, completed_at=?, updated_at=? WHERE id=?",
                (status, json.dumps(public_result, ensure_ascii=False), now, now, run_id),
            )
            self.connection.commit()

    def fail_brain_run(self, run_id: str, status: str, error: Any) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                "UPDATE brain_runs SET status=?, error_code=?, error_message=?, completed_at=?, updated_at=? WHERE id=?",
                (status, str(error.code), str(error.human_message), now, now, run_id),
            )
            self.connection.commit()

    def request_brain_cancel(self, run_id: str) -> None:
        now = _now()
        with self.lock:
            row = self.connection.execute("SELECT status FROM brain_runs WHERE id=?", (run_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown brain run: {run_id}")
            if row["status"] in {"completed", "failed", "cancelled"}: raise RuntimeError("Brain run is already terminal")
            if row["status"] == "awaiting_confirmation":
                self.connection.execute("UPDATE brain_runs SET status='cancelled', cancel_requested=1, completed_at=?, updated_at=? WHERE id=?", (now, now, run_id))
                self.connection.execute("UPDATE proposed_actions SET status='cancelled', decided_at=? WHERE run_id=? AND status='pending'", (now, run_id))
                self.connection.execute("UPDATE brain_change_sets SET state='cancelled', updated_at=? WHERE run_id=? AND state='proposed'", (now, run_id))
            else:
                self.connection.execute("UPDATE brain_runs SET cancel_requested=1, updated_at=? WHERE id=?", (now, run_id))
            self.connection.commit()

    def brain_cancel_requested(self, run_id: str) -> bool:
        with self.lock:
            row = self.connection.execute("SELECT cancel_requested FROM brain_runs WHERE id=?", (run_id,)).fetchone()
            return bool(row and row["cancel_requested"])

    def start_brain_step(self, run_id: str, step_id: str, ordinal: int, skill: str, purpose: str) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO brain_steps VALUES (?, ?, ?, ?, ?, 'running', ?, NULL, NULL)",
                (step_id, run_id, ordinal, skill, purpose, now),
            )
            self.connection.execute("UPDATE brain_runs SET current_step=?, updated_at=? WHERE id=?", (step_id, now, run_id))
            self.connection.commit()

    def complete_brain_step(self, step_id: str) -> None:
        with self.lock:
            self.connection.execute("UPDATE brain_steps SET status='completed', completed_at=? WHERE id=?", (_now(), step_id))
            self.connection.commit()

    def fail_brain_step(self, step_id: str, error_code: str) -> None:
        with self.lock:
            self.connection.execute("UPDATE brain_steps SET status='failed', completed_at=?, error_code=? WHERE id=?", (_now(), error_code, step_id))
            self.connection.commit()

    @staticmethod
    def _decode_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        for source, target, fallback in [
            ("secondary_intents_json", "secondary_intents", []), ("request_json", "request", {}),
            ("plan_json", "plan", None), ("result_json", "result", None),
        ]:
            raw = item.pop(source, None)
            item[target] = json.loads(raw) if raw else fallback
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item

    def get_brain_run(self, run_id: str, include_details: bool = False) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM brain_runs WHERE id=?", (run_id,)).fetchone()
            if not row: raise RuntimeError(f"Unknown brain run: {run_id}")
            item = self._decode_run(row)
            if include_details:
                result = item.get("result") or {}
                relative = str(result.get("private_result_path", "")) if isinstance(result, dict) else ""
                if relative:
                    path = (self.path.parent / relative).resolve()
                    root = (self.path.parent / "brain-results").resolve()
                    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
                        raise RuntimeError("Brain result payload is unavailable")
                    encoded = path.read_bytes()
                    if hashlib.sha256(encoded).hexdigest() != str(result.get("sha256", "")):
                        raise RuntimeError("Brain result payload integrity check failed")
                    private_result = json.loads(encoded)
                    if not isinstance(private_result, dict):
                        raise RuntimeError("Brain result payload is invalid")
                    item["result"] = private_result
                item["steps"] = [dict(value) for value in self.connection.execute("SELECT * FROM brain_steps WHERE run_id=? ORDER BY ordinal", (run_id,))]
                item["tool_events"] = [dict(value) for value in self.connection.execute("SELECT * FROM tool_events WHERE run_id=? ORDER BY started_at", (run_id,))]
                item["proposed_actions"] = [dict(value) for value in self.connection.execute("SELECT * FROM proposed_actions WHERE run_id=? ORDER BY created_at", (run_id,))]
            return item

    def list_brain_runs(self, limit: int = 50, offset: int = 0, status: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit))); offset = max(0, int(offset))
        with self.lock:
            if status:
                rows = self.connection.execute("SELECT * FROM brain_runs WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (status, limit, offset)).fetchall()
            else:
                rows = self.connection.execute("SELECT * FROM brain_runs ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            return [self._decode_run(row) for row in rows]

    def record_tool_event(self, event_id: str, run_id: str, step_id: str, tool: str, status: str, input_summary: str, output_summary: str = "", error_code: str | None = None) -> None:
        now = _now()
        with self.lock:
            existing = self.connection.execute("SELECT 1 FROM tool_events WHERE id=?", (event_id,)).fetchone()
            if existing:
                self.connection.execute("UPDATE tool_events SET status=?, output_summary=?, completed_at=?, error_code=? WHERE id=?", (status, output_summary, now, error_code, event_id))
            else:
                self.connection.execute("INSERT INTO tool_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (event_id, run_id, step_id, tool, status, input_summary, output_summary, now, now if status != "running" else None, error_code))
            self.connection.commit()

    def create_brain_change_set(self, change_set_id: str, run_id: str, title: str, writes: list[dict[str, Any]], preview: str, base_hashes: dict[str, str]) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO brain_change_sets VALUES (?, ?, 'proposed', ?, ?, ?, ?, NULL, ?, ?, NULL)",
                (change_set_id, run_id, title, json.dumps(writes, ensure_ascii=False), preview, json.dumps(base_hashes), now, now),
            )
            action_id = f"action-{uuid.uuid4().hex}"
            self.connection.execute(
                "INSERT INTO proposed_actions VALUES (?, ?, 'change_set', ?, ?, 'low', 'pending', ?, ?, NULL)",
                (action_id, run_id, title, f"{len(writes)} 个候选写入", change_set_id, now),
            )
            self.connection.commit()

    def create_assistant_change_set(self, change_set_id: str, run_id: str, title: str, writes: list[dict[str, Any]], preview: str, base_hashes: dict[str, str]) -> None:
        """Persist an internal write proposal without coupling it to legacy brain runs."""
        now = _now()
        with self.lock:
            self.connection.execute(
                "INSERT INTO assistant_change_sets VALUES (?, ?, 'proposed', ?, ?, ?, ?, NULL, ?, ?, NULL)",
                (change_set_id, run_id, title, json.dumps(writes, ensure_ascii=False), preview, json.dumps(base_hashes), now, now),
            )
            self.connection.commit()

    def is_assistant_runtime_run(self, run_id: str) -> bool:
        with self.lock:
            try:
                return self.connection.execute(
                    "SELECT 1 FROM assistant_framework_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone() is not None
            except sqlite3.OperationalError:
                return False

    def get_assistant_change_set(self, change_set_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM assistant_change_sets WHERE id=?", (change_set_id,)).fetchone()
            if not row:
                raise RuntimeError("Change Set not found")
            item = dict(row)
            item["writes"] = json.loads(item.pop("writes_json"))
            item["base_hashes"] = json.loads(item.pop("base_hashes_json"))
            return item

    def update_assistant_change_set(self, change_set_id: str, state: str, transaction_id: str | None = None) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                "UPDATE assistant_change_sets SET state=?, transaction_id=?, updated_at=?, applied_at=? WHERE id=?",
                (state, transaction_id, now, now if state == "applied" else None, change_set_id),
            )
            self.connection.commit()

    def get_brain_change_set(self, change_set_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT * FROM brain_change_sets WHERE id=?", (change_set_id,)).fetchone()
            if not row: raise RuntimeError("Change Set not found")
            item = dict(row); item["writes"] = json.loads(item.pop("writes_json")); item["base_hashes"] = json.loads(item.pop("base_hashes_json")); return item

    def update_brain_change_set(self, change_set_id: str, state: str, transaction_id: str | None = None) -> None:
        now = _now()
        with self.lock:
            self.connection.execute("UPDATE brain_change_sets SET state=?, transaction_id=?, updated_at=?, applied_at=? WHERE id=?", (state, transaction_id, now, now if state == "applied" else None, change_set_id))
            self.connection.execute("UPDATE proposed_actions SET status=?, decided_at=? WHERE change_set_id=?", ("accepted" if state == "applied" else state, now, change_set_id))
            self.connection.commit()

    def save_research_bundle(self, bundle: dict[str, Any], sources: list[dict[str, Any]]) -> None:
        now = _now()
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO research_bundles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (bundle["id"], bundle.get("run_id"), bundle["title"], bundle["question"], bundle["status"], len(sources), bundle["estimated_minutes"], json.dumps(bundle, ensure_ascii=False), bundle.get("created_at", now), now),
            )
            self.connection.execute("DELETE FROM research_sources WHERE bundle_id=?", (bundle["id"],))
            for source in sources:
                source_id = str(source["id"])
                row_id = "research-source-" + hashlib.sha256(f"{bundle['id']}|{source_id}".encode()).hexdigest()[:24]
                metadata = {**source.get("metadata", {}), "sourceId": source_id}
                self.connection.execute(
                    "INSERT INTO research_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (row_id, bundle["id"], source["source_type"], source["title"], source.get("canonical_url", ""), source.get("authors", ""), source.get("published_at"), float(source.get("relevance", 0)), float(source.get("quality", 0)), source.get("difficulty", "medium"), int(source.get("estimated_minutes", 10)), source.get("reason", ""), json.dumps(metadata, ensure_ascii=False)),
                )
            self.connection.commit()

    def list_research_bundles(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT payload_json FROM research_bundles ORDER BY created_at DESC LIMIT ? OFFSET ?", (max(1, min(100, limit)), max(0, offset))).fetchall()
            return [json.loads(row["payload_json"]) for row in rows]

    def get_research_bundle(self, bundle_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT payload_json FROM research_bundles WHERE id=?", (bundle_id,)).fetchone()
            if not row: raise RuntimeError("Research Bundle not found")
            bundle = json.loads(row["payload_json"])
            sources = []
            for source in self.connection.execute("SELECT * FROM research_sources WHERE bundle_id=? ORDER BY relevance DESC, quality DESC", (bundle_id,)):
                item = dict(source); item["metadata"] = json.loads(item.pop("metadata_json"))
                item["id"] = item["metadata"].get("sourceId", item["id"]); sources.append(item)
            bundle["sources"] = sources
            return bundle

    def upsert_curriculum_candidate(self, candidate: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                """INSERT OR REPLACE INTO curriculum_candidates(
                   id, title, canonical_title, kind, domain, route,
                   prerequisites_json, related_topics_json, why_now,
                   learning_outcomes_json, estimated_minutes, difficulty,
                   scores_json, confidence, basis_json, status,
                   model_profile_id, generated_at, cooldown_until,
                   verification_json, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (candidate["candidate_id"], candidate["title"], candidate["canonical_title"], candidate["kind"], candidate["domain"], candidate["route"], json.dumps(candidate.get("prerequisites", []), ensure_ascii=False), json.dumps(candidate.get("related_topics", []), ensure_ascii=False), candidate["why_now"], json.dumps(candidate.get("learning_outcomes", []), ensure_ascii=False), int(candidate["estimated_minutes"]), candidate["difficulty"], json.dumps(candidate.get("scores", {}), ensure_ascii=False), float(candidate["confidence"]), json.dumps(candidate.get("basis", {}), ensure_ascii=False), candidate["status"], candidate.get("model_profile_id"), candidate["generated_at"], candidate.get("cooldown_until"), json.dumps(candidate.get("verification", {}), ensure_ascii=False), int(candidate.get("schema_version", 1))),
            )
            self.connection.commit()

    def list_curriculum_candidates(self, status: str = "active") -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM curriculum_candidates WHERE status=? ORDER BY generated_at DESC", (status,)).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["candidate_id"] = item.pop("id")
            for key in ("prerequisites", "related_topics", "learning_outcomes", "scores", "basis", "verification"):
                item[key] = json.loads(item.pop(f"{key}_json"))
            result.append(item)
        return result

    def append_learning_events(self, events: list[dict[str, Any]]) -> tuple[int, int]:
        inserted = 0
        with self.lock:
            for event in events:
                cursor = self.connection.execute(
                    """INSERT OR IGNORE INTO learning_events(
                       id, event_type, subject_type, subject_id, topic, domain,
                       recommendation_id, session_id, duration_ms, payload_json,
                       created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event["id"], event["eventType"], event["subjectType"], event["subjectId"], event.get("topic"), event.get("domain"), event.get("recommendationId"), event.get("sessionId"), event.get("durationMs"), json.dumps(event.get("payload", {}), ensure_ascii=False), event["createdAt"], int(event.get("schemaVersion", 1))),
                )
                inserted += cursor.rowcount
            self.connection.commit()
        return inserted, len(events) - inserted

    def list_learning_events(self, limit: int = 1000, since: str = "") -> list[dict[str, Any]]:
        limit = max(1, min(5000, limit))
        with self.lock:
            if since:
                rows = self.connection.execute("SELECT * FROM learning_events WHERE created_at>=? ORDER BY created_at DESC LIMIT ?", (since, limit)).fetchall()
            else:
                rows = self.connection.execute("SELECT * FROM learning_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            result.append(item)
        return result

    def clear_learning_events(self, since: str = "") -> int:
        with self.lock:
            cursor = self.connection.execute("DELETE FROM learning_events WHERE created_at>=?", (since,)) if since else self.connection.execute("DELETE FROM learning_events")
            self.connection.commit()
            return cursor.rowcount

    def replace_inferred_features(self, features: list[dict[str, Any]]) -> None:
        with self.lock:
            self.connection.execute("DELETE FROM learner_features WHERE source='inferred'")
            for feature in features:
                self.connection.execute(
                    """INSERT OR REPLACE INTO learner_features(
                       feature_key, scope, value_json, confidence, evidence_count,
                       window_start, window_end, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (feature["key"], feature["scope"], json.dumps(feature.get("value"), ensure_ascii=False), float(feature["confidence"]), int(feature["evidenceCount"]), feature["windowStart"], feature["windowEnd"], feature["source"], feature["updatedAt"]),
                )
            self.connection.commit()

    def list_learner_features(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT * FROM learner_features ORDER BY source, feature_key, scope").fetchall()
        return [{
            "key": row["feature_key"], "scope": row["scope"], "value": json.loads(row["value_json"]),
            "confidence": row["confidence"], "evidenceCount": row["evidence_count"],
            "windowStart": row["window_start"], "windowEnd": row["window_end"],
            "source": row["source"], "updatedAt": row["updated_at"],
        } for row in rows]

    def curriculum_action(self, candidate_id: str, action: str, cooldown_until: str | None = None) -> None:
        states = {"favorite": "active", "not_interested": "dismissed", "later": "cooldown", "tomorrow": "planned", "weekend": "planned"}
        if action not in states: raise ValueError("Unsupported curriculum action")
        with self.lock:
            cursor = self.connection.execute("UPDATE curriculum_candidates SET status=?, cooldown_until=? WHERE id=?", (states[action], cooldown_until, candidate_id))
            if not cursor.rowcount: raise RuntimeError("Curriculum candidate not found")
            self.connection.commit()

    def save_plan_proposal(self, proposal: dict[str, Any]) -> None:
        now = _now()
        with self.lock:
            self.connection.execute("INSERT OR REPLACE INTO plan_proposals VALUES (?, ?, ?, ?, ?, ?, ?)", (proposal["id"], proposal.get("run_id"), proposal["title"], proposal.get("state", "proposed"), json.dumps(proposal.get("tasks", []), ensure_ascii=False), proposal.get("created_at", now), now))
            self.connection.commit()

    def list_plan_proposals(self) -> list[dict[str, Any]]:
        with self.lock:
            result = []
            for row in self.connection.execute("SELECT * FROM plan_proposals ORDER BY created_at DESC"):
                item = dict(row); item["tasks"] = json.loads(item.pop("tasks_json")); result.append(item)
            return result

    def update_plan_proposal_state(self, proposal_id: str, state: str) -> dict[str, Any]:
        if state not in {"proposed", "confirmed", "rejected"}:
            raise ValueError("Unsupported plan proposal state")
        with self.lock:
            cursor = self.connection.execute("UPDATE plan_proposals SET state=?, updated_at=? WHERE id=?", (state, _now(), proposal_id))
            if not cursor.rowcount: raise RuntimeError("Plan proposal not found")
            self.connection.commit()
        return next(item for item in self.list_plan_proposals() if item["id"] == proposal_id)

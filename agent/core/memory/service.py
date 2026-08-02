"""MemoryService: long-term user memory backed by memory_items / memory_evidence.

Long-term memory is the authority for user goals, preferences, knowledge state
and project decisions. It is NOT a conversation archive; evidence only stores
references (message_id / learning_event_id / quiz_id / feedback_id / action_id).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from agent.core.memory import policy as P


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"mem-{uuid.uuid4().hex[:16]}"


class MemoryService:
    def __init__(self, store: Any) -> None:
        self.store = store

    # ── CRUD ──────────────────────────────────────────────

    def remember_explicit(
        self,
        memory_type: str,
        memory_key: str,
        value: dict[str, Any],
        *,
        scope_type: str = "user",
        scope_id: str | None = None,
        evidence_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """显式记忆：用户明确表达，立即激活，confidence=1.0，source=explicit_user。"""
        P.validate_memory_type(memory_type)
        P.validate_scope(scope_type)
        memory_key = memory_key.strip()
        if not memory_key or len(memory_key) > 200:
            raise ValueError("memory_key_invalid")
        now = _now()
        item_id = _new_id()

        # 冲突处理：同类型同 scope 同 key 的现有 active 记忆 → supersede
        existing = self._find_active(memory_type, scope_type, scope_id, memory_key)
        supersedes_id = None
        with self.store.lock:
            if existing:
                supersedes_id = existing["id"]
                self.store.connection.execute(
                    "UPDATE memory_items SET status='superseded', updated_at=? WHERE id=?",
                    (now, existing["id"]),
                )
            self.store.connection.execute(
                """INSERT INTO memory_items
                   (id, memory_type, scope_type, scope_id, memory_key, value_json,
                    source_type, evidence_level, confidence, status,
                    created_at, updated_at, expires_at, supersedes_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'explicit_user', 'verified', 1.0, 'active', ?, ?, ?, ?)""",
                (item_id, memory_type, scope_type, scope_id, memory_key,
                 _json(value), now, now, expires_at, supersedes_id),
            )
            if evidence_id:
                self.store.connection.execute(
                    """INSERT OR IGNORE INTO memory_evidence
                       (id, memory_id, evidence_type, evidence_id, weight, created_at)
                       VALUES (?, ?, 'message', ?, 1.0, ?)""",
                    (_new_id(), item_id, evidence_id, now),
                )
            self.store.connection.commit()
        return self.get(item_id)

    def create_candidate(
        self,
        memory_type: str,
        memory_key: str,
        value: dict[str, Any],
        *,
        scope_type: str = "user",
        scope_id: str | None = None,
        evidence: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """隐式候选：模型只生成候选，Python Policy 决定是否晋升。"""
        P.validate_memory_type(memory_type)
        P.validate_scope(scope_type)
        memory_key = memory_key.strip()
        if not memory_key or len(memory_key) > 200:
            raise ValueError("memory_key_invalid")
        now = _now()
        item_id = _new_id()

        # 已有 active 同 key 记忆 → 直接返回现有（不重复）
        existing = self._find_active(memory_type, scope_type, scope_id, memory_key)
        if existing:
            return self.get(existing["id"])

        # 候选置信度随证据增长：base 0.5 + 0.1/条，封顶 0.9
        evidence = evidence or []
        confidence = min(0.9, 0.5 + 0.1 * len(evidence))

        with self.store.lock:
            self.store.connection.execute(
                """INSERT INTO memory_items
                   (id, memory_type, scope_type, scope_id, memory_key, value_json,
                    source_type, evidence_level, confidence, status,
                    created_at, updated_at, expires_at, supersedes_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'model_candidate', 'claimed', ?, 'candidate', ?, ?, NULL, NULL)""",
                (item_id, memory_type, scope_type, scope_id, memory_key,
                 _json(value), confidence, now, now),
            )
            if evidence:
                for etype, eid in evidence:
                    self.store.connection.execute(
                        """INSERT OR IGNORE INTO memory_evidence
                           (id, memory_id, evidence_type, evidence_id, weight, created_at)
                           VALUES (?, ?, ?, ?, 1.0, ?)""",
                        (_new_id(), item_id, etype, eid, now),
                    )
            self.store.connection.commit()
        return self.get(item_id)

    def activate_candidate(self, memory_id: str) -> dict[str, Any]:
        """晋升候选：满足证据数/日期/置信度条件才允许激活。"""
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                raise ValueError("memory_not_found")
            if row["status"] != "candidate":
                raise ValueError("memory_not_candidate")
            evidence = self.store.connection.execute(
                "SELECT evidence_type, evidence_id, weight, created_at FROM memory_evidence WHERE memory_id=?",
                (memory_id,),
            ).fetchall()
        evidence_count = len(evidence)
        distinct_days = len({str(e["created_at"])[:10] for e in evidence})
        confidence = float(row["confidence"])

        if not P.can_promote_candidate(evidence_count, distinct_days, confidence):
            raise ValueError(
                f"candidate_promotion_requirements_not_met:"
                f"evidence={evidence_count} days={distinct_days} confidence={confidence}"
            )
        now = _now()
        with self.store.lock:
            self.store.connection.execute(
                "UPDATE memory_items SET status='active', updated_at=?, source_type='repeated_observed', evidence_level='observed' WHERE id=?",
                (now, memory_id),
            )
            self.store.connection.commit()
        return self.get(memory_id)

    def search(
        self,
        query: str = "",
        *,
        memory_types: list[str] | None = None,
        scope_type: str = "user",
        scope_id: str | None = None,
        limit: int = 6,
    ) -> list[dict[str, Any]]:
        """V1 检索：active、未过期、未 superseded、未 deleted；词面匹配 memory_key。"""
        limit = max(1, min(20, int(limit)))
        clauses = ["status='active'", "(expires_at IS NULL OR expires_at > ?)"]
        values: list[Any] = [_now()]
        if memory_types:
            clauses.append(f"memory_type IN ({','.join('?' for _ in memory_types)})")
            values.extend(memory_types)
        if scope_type:
            clauses.append("scope_type=?")
            values.append(scope_type)
            if scope_id:
                clauses.append("scope_id=?")
                values.append(scope_id)
        if query.strip():
            clauses.append("(memory_key LIKE ? OR value_json LIKE ?)")
            like = f"%{query.strip()}%"
            values.extend([like, like])
        sql = (
            "SELECT * FROM memory_items WHERE " + " AND ".join(clauses)
            + " ORDER BY updated_at DESC LIMIT ?"
        )
        values.append(limit)
        with self.store.lock:
            rows = self.store.connection.execute(sql, values).fetchall()
        return [_row_dict(row) for row in rows]

    def list_active(self, memory_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses = ["status='active'", "(expires_at IS NULL OR expires_at > ?)"]
        values: list[Any] = [_now()]
        if memory_type:
            clauses.append("memory_type=?")
            values.append(memory_type)
        sql = "SELECT * FROM memory_items WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        with self.store.lock:
            rows = self.store.connection.execute(sql, values).fetchall()
        return [_row_dict(row) for row in rows]

    def forget(self, memory_id: str) -> dict[str, Any]:
        """删除：只软删除（status='deleted'），保留可追溯记录。"""
        now = _now()
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                raise ValueError("memory_not_found")
            self.store.connection.execute(
                "UPDATE memory_items SET status='deleted', updated_at=? WHERE id=?",
                (now, memory_id),
            )
            self.store.connection.commit()
        return self.get(memory_id)

    def supersede(self, memory_id: str, new_value: dict[str, Any]) -> dict[str, Any]:
        """用新值取代旧记忆：旧记录 superseded，新记录 supersedes_id 指向旧记录。"""
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                raise ValueError("memory_not_found")
        return self.remember_explicit(
            str(row["memory_type"]), str(row["memory_key"]), new_value,
            scope_type=str(row["scope_type"]), scope_id=row["scope_id"],
            evidence_id=None,
        )

    def get(self, memory_id: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.connection.execute(
                "SELECT * FROM memory_items WHERE id=?", (memory_id,)
            ).fetchone()
            if not row:
                raise ValueError("memory_not_found")
            evidence = self.store.connection.execute(
                "SELECT evidence_type, evidence_id, weight, created_at FROM memory_evidence WHERE memory_id=?",
                (memory_id,),
            ).fetchall()
        result = _row_dict(row)
        result["evidence"] = [dict(e) for e in evidence]
        return result

    # ── Pi Context ─────────────────────────────────────────

    def build_pi_context(
        self,
        request_text: str,
        *,
        memory_types: list[str] | None = None,
        scope_type: str = "user",
        scope_id: str | None = None,
        max_items: int = 8,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        """每轮 Pi Turn 前的记忆上下文。只返回 active 且相关的记忆，限制条数与 token。"""
        types = memory_types or ["goal", "preference", "knowledge_state", "project_decision"]
        items = self.search(
            request_text, memory_types=types, scope_type=scope_type, scope_id=scope_id, limit=max_items
        )
        budget = max_tokens
        result: dict[str, Any] = {
            "activeGoals": [],
            "explicitPreferences": [],
            "relevantKnowledgeStates": [],
            "projectDecisions": [],
        }
        for item in items:
            if budget <= 0:
                break
            entry = {
                "id": item["id"],
                "key": item["memory_key"],
                "value": item["value"],
            }
            group = {
                "goal": "activeGoals",
                "preference": "explicitPreferences",
                "knowledge_state": "relevantKnowledgeStates",
                "project_decision": "projectDecisions",
            }[item["memory_type"]]
            result[group].append(entry)
            budget -= _estimate_tokens(entry)
        return result

    # ── helpers ────────────────────────────────────────────

    def _find_active(
        self, memory_type: str, scope_type: str, scope_id: str | None, memory_key: str
    ) -> dict[str, Any] | None:
        with self.store.lock:
            row = self.store.connection.execute(
                """SELECT * FROM memory_items
                   WHERE memory_type=? AND scope_type=? AND scope_id IS ? AND memory_key=? AND status='active'
                   ORDER BY updated_at DESC LIMIT 1""",
                (memory_type, scope_type, scope_id, memory_key),
            ).fetchone()
        return dict(row) if row else None


def _json(value: dict[str, Any]) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


def _row_dict(row: Any) -> dict[str, Any]:
    import json
    result = dict(row)
    try:
        result["value"] = json.loads(result.get("value_json") or "{}")
    except json.JSONDecodeError:
        result["value"] = {}
    result.pop("value_json", None)
    return result


def _estimate_tokens(entry: dict[str, Any]) -> int:
    text = f"{entry.get('key', '')} {entry.get('value', '')}"
    return max(20, len(str(text)) // 2)

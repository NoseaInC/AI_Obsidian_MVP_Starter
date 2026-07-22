#!/usr/bin/env python3
"""Opt-in real-model smoke test for the governed Assistant Harness.

The test copies only non-secret provider metadata, resolves the API key through
the existing Keychain reference, and operates entirely inside a temporary
Vault. It is intentionally excluded from the offline test suite.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.core.service import AgentService


TARGET = "10-Inbox/Harness-倾向得分匹配-真实模型测试.md"


def _configured_profile(source_vault: Path) -> dict[str, Any]:
    database = source_vault / "90-Local-Only/Agent/agent.sqlite3"
    if not database.is_file():
        raise RuntimeError("source_agent_database_missing")
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT p.*
            FROM model_profiles p
            JOIN model_routing r ON r.profile_id = p.profile_id
            WHERE r.task = 'assistant_chat' AND p.enabled = 1
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("assistant_model_profile_missing")
    return {
        "displayName": f"{row['display_name']} · Harness smoke",
        "providerType": row["provider_type"],
        "baseUrl": row["base_url"],
        "apiKeyReference": row["api_key_reference"],
        "defaultModel": row["default_model"],
        "availableModels": json.loads(row["available_models_json"] or "[]"),
        "enabled": True,
        "settings": json.loads(row["settings_json"] or "{}"),
    }


def _seed_vault(vault: Path) -> None:
    notes = {
        "20-Knowledge/Topics/因果推断.md": """---
status: reviewed
domain: statistics-ml
---
# 因果推断

从观测数据估计处理效应通常依赖一致性、无混杂性和重叠性。
倾向得分方法通过处理分配机制帮助构造可比人群，但不能修复未观测混杂。
""",
        "20-Knowledge/Concepts/倾向得分.md": """---
status: core
domain: statistics-ml
---
# 倾向得分

倾向得分定义为 $e(X)=P(T=1\\mid X)$。在强可忽略性与共同支持条件下，
它是处理分配的平衡得分。匹配后仍需检查协变量平衡，不能只看模型拟合度。
""",
    }
    for relative, content in notes.items():
        path = vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run(source_vault: Path) -> dict[str, Any]:
    profile_data = _configured_profile(source_vault)
    with tempfile.TemporaryDirectory(prefix="zhixu-harness-smoke-") as temp:
        vault = Path(temp) / "Vault"
        vault.mkdir()
        _seed_vault(vault)
        service = AgentService(vault)
        try:
            profile = service.save_model_profile(profile_data)
            if not profile.get("configured"):
                raise RuntimeError("keychain_reference_not_configured")
            service.set_model_routing({
                "assistant_chat": {
                    "profileId": profile["id"],
                    "modelOverride": profile["defaultModel"],
                }
            })
            prompt = (
                "以倾向得分匹配为主题完成一次真实知识整理。必须先搜索 Vault，读取"
                "20-Knowledge 中至少两篇相关本地笔记，再综合成一篇包含定义、识别假设、"
                "实施步骤、诊断方法、局限与 Obsidian 来源回链的笔记。不要只在聊天里回答；"
                f"请把最终笔记保存到 {TARGET}，并完成 Harness 校验。"
            )
            events = list(service.assistant_stream({"message": prompt}))
            terminal = [
                item.get("type")
                for item in events
                if item.get("type") in {"run.completed", "run.failed", "run.waiting_confirmation"}
            ]
            tools = [
                str(item.get("tool") or "")
                for item in events
                if item.get("type") == "tool.completed"
            ]
            target = vault / TARGET
            if terminal[-1:] != ["run.completed"]:
                failure = next((item for item in reversed(events) if item.get("type") == "run.failed"), {})
                raise RuntimeError(f"real_run_not_completed:{failure.get('code', terminal[-1:] or ['missing'])}")
            required = {"search_vault", "read_vault_note", "propose_vault_change", "commit_vault_change"}
            missing = sorted(required.difference(tools))
            if missing:
                raise RuntimeError(f"required_tools_missing:{','.join(missing)}")
            if tools.count("read_vault_note") < 2:
                raise RuntimeError("insufficient_local_source_reads")
            if not target.is_file():
                raise RuntimeError("target_note_not_written")
            content = target.read_text(encoding="utf-8")
            checks = {
                "substantial": len(content) >= 300,
                "topic": "倾向得分匹配" in content,
                "assumptions": any(term in content for term in ("无混杂", "可忽略", "重叠", "共同支持")),
                "limitations": any(term in content for term in ("局限", "限制", "未观测混杂")),
                "source_links": "[[" in content and ("倾向得分" in content or "因果推断" in content),
            }
            if not all(checks.values()):
                raise RuntimeError("written_note_quality_gate_failed:" + json.dumps(checks, ensure_ascii=False))
            commit = next(
                item for item in reversed(events)
                if item.get("type") == "tool.completed" and item.get("tool") == "commit_vault_change"
            )
            verification = (commit.get("result") or {}).get("verification") or {}
            if verification.get("verified") is not True:
                raise RuntimeError("post_apply_verification_missing")
            return {
                "ok": True,
                "model": profile["defaultModel"],
                "target": TARGET,
                "toolSequence": tools,
                "qualityChecks": checks,
                "verified": True,
                "temporaryVault": True,
            }
        finally:
            service.store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-vault",
        type=Path,
        default=ROOT,
        help="Vault containing the configured assistant model profile and Keychain reference.",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.source_vault.resolve()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

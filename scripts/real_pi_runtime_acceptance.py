#!/usr/bin/env python3
"""Launch the production Pi runtime against real DeepSeek in a temporary Vault.

This opt-in test copies only non-secret provider metadata and a Keychain
reference.  The API key is resolved by the existing backend and is never read,
printed, persisted in the temporary repository, or passed to Node.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.api.server import serve


def _configured_profile(source_vault: Path) -> dict[str, Any]:
    database = source_vault / "90-Local-Only/Agent/agent.sqlite3"
    if not database.is_file():
        raise RuntimeError("source_agent_database_missing")
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT p.* FROM model_profiles p
            JOIN model_routing r ON r.profile_id = p.profile_id
            WHERE r.task = 'assistant_chat' AND p.enabled = 1
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("assistant_model_profile_missing")
    settings = json.loads(row["settings_json"] or "{}")
    return {
        "displayName": f"{row['display_name']} · Pi acceptance",
        "providerType": row["provider_type"],
        "baseUrl": row["base_url"],
        "apiKeyReference": row["api_key_reference"],
        "defaultModel": row["default_model"],
        "availableModels": json.loads(row["available_models_json"] or "[]"),
        "enabled": True,
        "settings": settings,
    }


def _write(vault: Path, relative: str, content: str) -> None:
    target = vault / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _seed(vault: Path) -> None:
    _write(vault, "20-Knowledge/Drafts/当前论证.md", """---
status: ai-draft
domain: statistics-ml
---
# 观测数据的识别论证

识别平均处理效应依赖三条假设：一致性、条件无混杂性和重叠性。
实施倾向得分方法后，必须检查协变量平衡与共同支持区间。
""")
    _write(vault, "20-Knowledge/Concepts/倾向得分.md", """---
status: reviewed
domain: statistics-ml
---
# 倾向得分

倾向得分 $e(X)=P(T=1\\mid X)$ 是处理分配的平衡得分。
估计后需检查标准化均差和重叠性；它不能消除未观测混杂。
""")
    _write(vault, "20-Knowledge/Topics/观测研究.md", """---
status: core
domain: statistics-ml
---
# 观测研究

从观测数据识别因果效应通常要求一致性、无混杂性与正值性。
[[倾向得分]] 可用于设计阶段的平衡与共同支持诊断。
""")
    _write(vault, "package.json", json.dumps({
        "name": "zhixu-temporary-acceptance",
        "version": "1.0.0",
        "scripts": {
            "test": "node -e \\\"const p=require('./package.json'); if(p.version!=='1.0.1') process.exit(1)\\\"",
            "build": "node -e \\\"require('fs').writeFileSync('build.ok','ok')\\\"",
        },
    }, ensure_ascii=False, indent=2) + "\n")
    (vault / ".gitignore").write_text("90-Local-Only/\nbuild.ok\n", encoding="utf-8")
    subprocess.run(["/usr/bin/git", "init", "-q"], cwd=vault, check=True)
    subprocess.run(["/usr/bin/git", "add", "--", "."], cwd=vault, check=True)
    subprocess.run([
        "/usr/bin/git", "-c", "user.name=Zhixu Acceptance",
        "-c", "user.email=acceptance@localhost", "commit", "-qm", "seed",
    ], cwd=vault, check=True)


def run(source_vault: Path) -> dict[str, Any]:
    profile_data = _configured_profile(source_vault)
    with tempfile.TemporaryDirectory(prefix="zhixu-real-pi-") as temporary:
        vault = Path(temporary) / "Vault"
        vault.mkdir()
        _seed(vault)
        token = uuid.uuid4().hex + uuid.uuid4().hex
        server = serve(vault, port=0, session_token=token, runtime_id="real-pi-acceptance")
        service = server.RequestHandlerClass.service
        profile = service.save_model_profile(profile_data)
        if not profile.get("configured"):
            service.store.close()
            server.server_close()
            raise RuntimeError("keychain_reference_not_configured")
        service.set_model_routing({
            "assistant_chat": {"profileId": profile["id"], "modelOverride": profile["defaultModel"]}
        })
        service.intake.ensure_runtime_conversation("acceptance-b-focus", "倾向得分诊断")
        service.store.save_conversation_focus("acceptance-b-focus", {
            "activeTopic": {"title": "观测数据中的处理效应估计"},
            "activeMethod": {"title": "倾向得分"},
            "activeNotePath": "20-Knowledge/Drafts/当前论证.md",
            "lastConfirmedIntent": "理解倾向得分与识别假设的关系",
            "confidence": 0.95,
            "evidence": ["user-confirmed"],
        })
        thread = threading.Thread(target=server.serve_forever, name="pi-acceptance-api", daemon=True)
        thread.start()
        try:
            environment = {
                **os.environ,
                "ZHIXU_ACCEPTANCE_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "ZHIXU_ACCEPTANCE_SESSION_TOKEN": token,
                "ZHIXU_ACCEPTANCE_PROFILE_ID": str(profile["id"]),
                "ZHIXU_ACCEPTANCE_MODEL": str(profile["defaultModel"]),
                "ZHIXU_ACCEPTANCE_PLUGIN_ROOT": str(ROOT / "obsidian-agent-plugin"),
                "ZHIXU_ACCEPTANCE_VAULT_ROOT": str(vault),
            }
            completed = subprocess.run(
                ["node", str(ROOT / "scripts/real_pi_runtime_acceptance.mjs")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=1_800,
            )
            if completed.returncode != 0:
                # stderr is model/key free; tokens are never included in errors.
                raise RuntimeError(f"real_pi_acceptance_failed:{completed.stderr[-4000:]}")
            payload = json.loads(completed.stdout)
            if set(payload.get("cases") or {}) != set("ABCDEFGH"):
                raise RuntimeError("real_pi_acceptance_incomplete")
            return payload
        finally:
            server.shutdown()
            thread.join(timeout=10)
            server.server_close()
            service.store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-vault", type=Path, default=ROOT)
    args = parser.parse_args()
    result = run(args.source_vault.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

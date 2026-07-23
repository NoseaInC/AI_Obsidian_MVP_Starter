#!/usr/bin/env python3
"""Run the R08 A-S acceptance in temporary, secret-free environments.

This is a production-code, deterministic-provider acceptance. It deliberately
does not call a real model, resolve a Keychain reference, read the user's Vault,
or launch/restart the user's Obsidian process.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "obsidian-agent-plugin"
SENSITIVE_ENV = re.compile(
    r"(?:API[_-]?KEY|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|SECRET|CREDENTIAL|KEYCHAIN)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    label: str
    cwd: Path
    command: tuple[str, ...]


def _node_case(case_id: str, label: str, file_name: str, pattern: str) -> AcceptanceCase:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node_not_found")
    return AcceptanceCase(
        case_id,
        label,
        PLUGIN,
        (node, "--test", "--test-name-pattern", pattern, f"tests/{file_name}"),
    )


def _python_case(case_id: str, label: str, test_name: str) -> AcceptanceCase:
    return AcceptanceCase(case_id, label, ROOT, (sys.executable, "-m", "unittest", test_name))


def _cases() -> list[AcceptanceCase]:
    return [
        _node_case("A", "ordinary Q&A", "pi-repair-acceptance.test.mjs", r"repair acceptance A:"),
        _node_case("B", "current-note Q&A", "pi-repair-acceptance.test.mjs", r"repair acceptance B:"),
        _node_case("C", "cross-Vault Q&A", "pi-repair-acceptance.test.mjs", r"repair acceptance C:"),
        _python_case(
            "D",
            "direct reversible write",
            "agent.tests.test_pi_reversible_writes.PiReversibleWriteTests."
            "test_current_task_creates_verifies_diffs_and_undoes_without_confirmation",
        ),
        _python_case(
            "E",
            "conflict-safe Undo",
            "agent.tests.test_pi_reversible_writes.PiReversibleWriteTests."
            "test_authorized_existing_note_update_restores_exact_snapshot",
        ),
        _node_case(
            "F",
            "inline permission card",
            "pi-permission-e2e.test.mjs",
            r"permission card pauses and resumes",
        ),
        _node_case(
            "G",
            "reload while waiting",
            "pi-permission-recovery.test.mjs",
            r"survives two consecutive restarts",
        ),
        _node_case(
            "H",
            "recover and approve",
            "pi-permission-recovery.test.mjs",
            r"persists the pending call and rebuilds",
        ),
        _node_case(
            "I",
            "recover and deny",
            "pi-permission-recovery.test.mjs",
            r"deny on the recovered card",
        ),
        _node_case(
            "J",
            "Entry-boundary fork",
            "pi-fork.test.mjs",
            r"fork projects the chosen branch",
        ),
        _node_case(
            "K",
            "regenerate",
            "pi-fork.test.mjs",
            r"regenerate uses the persisted pre-answer boundary",
        ),
        _node_case(
            "L",
            "Tool-safe compaction",
            "pi-compaction.test.mjs",
            r"compaction never splits a tool call",
        ),
        _node_case(
            "M",
            "compaction restart recovery",
            "pi-compaction.test.mjs",
            r"runtime compaction persists a recoverable checkpoint",
        ),
        _node_case(
            "N",
            "learning assistant Q&A",
            "pi-assistant-turn.test.mjs",
            r"shared Pi turn preserves local learning context",
        ),
        _node_case(
            "O",
            "study-note Action Result",
            "pi-repair-acceptance.test.mjs",
            r"repair acceptance O:",
        ),
        _python_case(
            "P",
            "Developer Workspace",
            "agent.tests.test_developer_workspace.DeveloperWorkspaceTests."
            "test_creates_task_branch_and_atomic_project_write_without_file_confirmation",
        ),
        _python_case(
            "Q",
            "controlled Bash",
            "agent.tests.test_developer_workspace.DeveloperWorkspaceTests."
            "test_controlled_bash_runs_inside_workspace_with_network_denied",
        ),
        _python_case(
            "R",
            "Skill Draft validation",
            "agent.tests.test_developer_workspace.DeveloperWorkspaceTests."
            "test_skill_and_mcp_validation_reject_secret_access",
        ),
        _python_case(
            "S",
            "MCP validate/probe",
            "agent.tests.test_developer_workspace.DeveloperWorkspaceTests."
            "test_mcp_temporary_probe_uses_minimal_environment_and_lists_tools",
        ),
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="zhixu-pi-r08-") as temporary:
        acceptance_root = Path(temporary)
        temporary_base = acceptance_root / "fixtures"
        temporary_base.mkdir()
        environment = {
            name: value
            for name, value in os.environ.items()
            if not SENSITIVE_ENV.search(name)
        }
        environment["TMPDIR"] = str(temporary_base)
        environment["ZHIXU_ACCEPTANCE_MODE"] = "offline-deterministic"
        results: dict[str, dict[str, object]] = {}
        for item in _cases():
            started = time.monotonic()
            completed = subprocess.run(
                item.command,
                cwd=item.cwd,
                env=environment,
                text=True,
                capture_output=True,
                timeout=240,
            )
            duration = round(time.monotonic() - started, 3)
            output = (completed.stdout + completed.stderr).strip()
            results[item.case_id] = {
                "label": item.label,
                "ok": completed.returncode == 0,
                "durationSeconds": duration,
                "command": list(item.command),
                "outputTail": output[-1200:],
            }

        passed = sum(1 for item in results.values() if item["ok"])
        report = {
            "schemaVersion": 1,
            "mode": "headless-production-code/deterministic-provider",
            "temporaryVaultSqliteGitOnly": True,
            "realModelCalled": False,
            "keychainOrSecretRead": False,
            "userObsidianTouched": False,
            "passed": passed,
            "total": len(results),
            "cases": results,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

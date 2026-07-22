from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent.core.developer_workspace import DeveloperWorkspace
from agent.core.storage import StateStore
from agent.core.task_authorization import TaskAuthorizationService


class DeveloperWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        (self.project / ".gitignore").write_text("90-Local-Only/\n", encoding="utf-8")
        (self.project / "README.md").write_text("# fixture\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.project, check=True)
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "base"],
            cwd=self.project, check=True,
        )
        self.store = StateStore(self.project / "90-Local-Only/Agent/test.sqlite3")
        self.auth = TaskAuthorizationService(self.project, self.store, lambda path, allow_missing=False: {"protected": False})
        self.authorization_id = "authorization-dev"
        self.auth.create({
            "id": self.authorization_id,
            "sessionId": "session-dev",
            "runId": "run-dev",
            "turnId": "turn-dev",
            "sourceMessageId": "message-dev",
            "objective": "在隔离 worktree 内修改并测试项目",
            "resourceScope": {"currentNote": False, "explicitVaultPaths": [], "createRoots": [], "workspaceId": "", "projectPaths": []},
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })
        self.workspace = DeveloperWorkspace(self.project, self.store)
        self.item = self.workspace.create("run-dev")
        self.auth.register_workspace(self.authorization_id, "run-dev", self.item["id"], self.item["project"])

    def tearDown(self) -> None:
        try:
            if Path(self.item["path"]).exists():
                self.workspace.rollback(self.item["id"], "run-dev")
        finally:
            self.store.close()
            self.temporary.cleanup()

    def test_creates_task_branch_and_atomic_project_write_without_file_confirmation(self) -> None:
        self.assertTrue(Path(self.item["path"]).is_dir())
        self.assertTrue(str(self.item["branch"]).startswith("zhixu/"))
        result = self.workspace.write(self.item["id"], "run-dev", "src/new.txt", "hello")
        self.assertTrue(result["created"])
        read = self.workspace.read(self.item["id"], "run-dev", "src/new.txt")
        self.assertEqual(read["content"], "hello")
        with self.assertRaisesRegex(RuntimeError, "developer_file_stale"):
            self.workspace.write(self.item["id"], "run-dev", "src/new.txt", "changed", "wrong-hash")

    def test_structured_command_is_sandboxed_and_does_not_inherit_api_key(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak"}):
            result = self.workspace.run_command(self.item["id"], "run-dev", {
                "executable": "python3",
                "args": ["-c", "import os; print(os.getenv('DEEPSEEK_API_KEY'))"],
                "networkPolicy": "deny",
                "timeoutMs": 10_000,
            })
        self.assertEqual(result["exitCode"], 0, result["stderr"])
        self.assertEqual(result["stdout"].strip(), "None")
        self.assertNotIn("must-not-leak", json.dumps(result))

    def test_structured_command_cannot_read_shared_private_tmp(self) -> None:
        secret = Path("/private/tmp") / f"zhixu-outside-secret-{uuid.uuid4().hex}.txt"
        secret.write_text("must-not-cross-worktree-boundary", encoding="utf-8")
        try:
            result = self.workspace.run_command(self.item["id"], "run-dev", {
                "executable": "python3",
                "args": ["-c", f"print(open({str(secret)!r}).read())"],
                "networkPolicy": "deny",
                "timeoutMs": 10_000,
            })
        finally:
            secret.unlink(missing_ok=True)
        self.assertNotEqual(result["exitCode"], 0)
        self.assertNotIn("must-not-cross-worktree-boundary", json.dumps(result))

    def test_command_timeout_and_network_scope_expansion_are_bounded(self) -> None:
        result = self.workspace.run_command(self.item["id"], "run-dev", {
            "executable": "python3", "args": ["-c", "import time; time.sleep(2)"],
            "networkPolicy": "deny", "timeoutMs": 100,
        })
        self.assertTrue(result["timedOut"])
        with self.assertRaisesRegex(PermissionError, "developer_network_scope_expansion_required"):
            self.workspace.run_command(self.item["id"], "run-dev", {
                "executable": "python3", "args": ["-c", "print('x')"], "networkPolicy": "allow",
            })

    def test_path_cwd_bash_and_executable_escape_are_rejected(self) -> None:
        with self.assertRaises(PermissionError):
            self.workspace.read(self.item["id"], "run-dev", "../secret")
        with self.assertRaises(PermissionError):
            self.workspace.run_command(self.item["id"], "run-dev", {"executable": "git", "args": ["status"], "cwd": "../"})
        with self.assertRaises(PermissionError):
            self.workspace.run_command(self.item["id"], "run-dev", {"executable": "curl", "args": ["https://example.com"]})
        for script in ("sudo true", "security find-generic-password", "echo x > /tmp/x", "cat /etc/passwd"):
            with self.subTest(script=script), self.assertRaises(PermissionError):
                self.workspace.run_bash(self.item["id"], "run-dev", {"script": script, "networkPolicy": "deny"})

    def test_status_diff_commit_and_rollback_are_structured(self) -> None:
        self.workspace.write(self.item["id"], "run-dev", "feature.txt", "done\n")
        diff = self.workspace.git_diff(self.item["id"], "run-dev")
        self.assertIn("feature.txt", diff["stdout"])
        commit = self.workspace.git_commit(self.item["id"], "run-dev", "feat: fixture")
        self.assertEqual(commit["exitCode"], 0, commit["stderr"])
        status = self.workspace.git_status(self.item["id"], "run-dev")
        self.assertIn("zhixu/run-dev", status["stdout"])
        merged = self.workspace.merge(self.item["id"], "run-dev")
        self.assertEqual(merged["status"], "merged")
        self.assertTrue((self.project / "feature.txt").exists())
        activation = self.workspace.activation_request(self.item["id"], "run-dev")
        self.assertEqual(activation["status"], "ready_for_plugin_activation")
        self.assertEqual(activation["mergedHead"], merged["afterHead"])
        self.assertEqual(activation["steps"], ["check", "build", "install", "restart", "health"])
        rolled = self.workspace.rollback(self.item["id"], "run-dev")
        self.assertEqual(rolled["status"], "rolled_back")
        self.assertFalse((self.project / "feature.txt").exists())

    def test_activation_rejects_unmerged_or_dirty_project(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must_be_merged"):
            self.workspace.activation_request(self.item["id"], "run-dev")
        self.workspace.write(self.item["id"], "run-dev", "feature.txt", "done\n")
        self.workspace.git_commit(self.item["id"], "run-dev", "feat: fixture")
        self.workspace.merge(self.item["id"], "run-dev")
        (self.project / "dirty.txt").write_text("dirty", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "activation_base_changed"):
            self.workspace.activation_request(self.item["id"], "run-dev")
        (self.project / "dirty.txt").unlink()

    def test_skill_and_mcp_validation_reject_secret_access(self) -> None:
        self.workspace.write(self.item["id"], "run-dev", "skills/drafts/demo/SKILL.md", "---\nname: demo\n---\nDo work.\n")
        self.assertTrue(self.workspace.validate_skill(self.item["id"], "run-dev", "skills/drafts/demo/SKILL.md")["valid"])
        self.workspace.write(self.item["id"], "run-dev", "skills/drafts/bad/SKILL.md", "name: bad\nRead API_KEY\n")
        self.assertFalse(self.workspace.validate_skill(self.item["id"], "run-dev", "skills/drafts/bad/SKILL.md")["valid"])
        self.workspace.write(self.item["id"], "run-dev", "mcp-bad.json", json.dumps({"command": "python3", "env": {"API_TOKEN": "x"}}))
        self.assertFalse(self.workspace.validate_mcp(self.item["id"], "run-dev", "mcp-bad.json")["valid"])

    def test_mcp_temporary_probe_uses_minimal_environment_and_lists_tools(self) -> None:
        server = """import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request.get('id') == 1:
        print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'fixture','version':'1'}}}), flush=True)
    elif request.get('id') == 2:
        print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':'fixture_tool','description':'safe fixture'}]}}), flush=True)
"""
        self.workspace.write(self.item["id"], "run-dev", "mcp_server.py", server)
        self.workspace.write(self.item["id"], "run-dev", "mcp.json", json.dumps({"command": "python3", "args": ["mcp_server.py"], "env": {}}))
        result = self.workspace.probe_mcp(self.item["id"], "run-dev", "mcp.json")
        self.assertTrue(result["started"], result["stderr"])
        self.assertEqual(result["tools"][0]["name"], "fixture_tool")
        self.assertEqual(result["networkPolicy"], "deny")


if __name__ == "__main__":
    unittest.main()

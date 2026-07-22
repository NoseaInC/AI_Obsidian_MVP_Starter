from __future__ import annotations

import hashlib
import difflib
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

from agent.core.redaction import redact_secret_text


ALLOWED_EXECUTABLES = {"git", "npm", "node", "python", "python3", "pytest", "rg", "ls", "find", "make"}
FIXED_EXECUTABLES = {
    "git": "/Library/Developer/CommandLineTools/usr/bin/git",
    "python": "/Library/Developer/CommandLineTools/usr/bin/python3",
    "python3": "/Library/Developer/CommandLineTools/usr/bin/python3",
    "ls": "/bin/ls",
    "find": "/usr/bin/find",
    "make": "/usr/bin/make",
}
DENIED_COMMANDS = {"sudo", "su", "security", "ssh", "scp", "sftp", "osascript", "launchctl", "diskutil", "shutdown", "reboot", "chown"}
SENSITIVE_ENV = re.compile(r"(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|SSH_AUTH_SOCK|AWS_|AZURE_|GOOGLE_)", re.I)
SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DeveloperWorkspace:
    """Task-scoped Git worktree and macOS sandboxed command plane."""

    def __init__(self, project: Path, store: Any) -> None:
        self.project = project.resolve()
        self.store = store
        self.root = self.project / "90-Local-Only/Agent/DeveloperWorkspaces"
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str) -> dict[str, Any]:
        if not (self.project / ".git").exists():
            raise RuntimeError("developer_project_not_git_repository")
        safe = SAFE_ID.sub("-", run_id)[:48].strip("-") or uuid.uuid4().hex[:12]
        workspace_id = f"workspace-{safe}"
        target = self.root / workspace_id / "worktree"
        branch = f"zhixu/{safe}"
        existing = self.store.get_setting(f"developer_workspace:{workspace_id}", None)
        if isinstance(existing, dict) and target.is_dir():
            return existing
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["/usr/bin/git", "worktree", "add", "-b", branch, str(target), "HEAD"],
            cwd=self.project, text=True, capture_output=True, timeout=60, env=self._env(target),
        )
        if result.returncode != 0:
            # Idempotent recovery if the branch survived an interrupted create.
            result = subprocess.run(
                ["/usr/bin/git", "worktree", "add", str(target), branch],
                cwd=self.project, text=True, capture_output=True, timeout=60, env=self._env(target),
            )
        if result.returncode != 0:
            raise RuntimeError(f"developer_worktree_create_failed:{redact_secret_text(result.stderr)[:500]}")
        item = {"id": workspace_id, "runId": run_id, "path": str(target), "branch": branch, "project": str(self.project), "status": "active"}
        self.store.set_setting(f"developer_workspace:{workspace_id}", item)
        return item

    def get(self, workspace_id: str, run_id: str = "") -> dict[str, Any]:
        item = self.store.get_setting(f"developer_workspace:{workspace_id}", None)
        if not isinstance(item, dict): raise ValueError("developer_workspace_not_found")
        if run_id and str(item.get("runId") or "") != run_id: raise PermissionError("developer_workspace_run_mismatch")
        root = Path(str(item.get("path") or "")).resolve()
        if not root.is_dir() or not self._within(root, self.root): raise RuntimeError("developer_workspace_unavailable")
        return item

    def read(self, workspace_id: str, run_id: str, relative: str, max_chars: int = 50_000) -> dict[str, Any]:
        root = Path(self.get(workspace_id, run_id)["path"])
        target = self._target(root, relative)
        data = target.read_bytes()
        return {"path": target.relative_to(root).as_posix(), "content": data.decode("utf-8", errors="replace")[:max_chars], "sha256": _hash(data), "size": len(data)}

    def write(self, workspace_id: str, run_id: str, relative: str, content: str, base_hash: str = "") -> dict[str, Any]:
        root = Path(self.get(workspace_id, run_id)["path"])
        target = self._target(root, relative, allow_missing=True)
        existed = target.exists()
        before = target.read_bytes() if existed else b""
        if base_hash and _hash(before) != base_hash: raise RuntimeError("developer_file_stale")
        encoded = content.encode("utf-8")
        if len(encoded) > 1_000_000: raise ValueError("developer_file_too_large")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temp.write_bytes(encoded); os.replace(temp, target)
        return {"path": target.relative_to(root).as_posix(), "beforeHash": _hash(before), "afterHash": _hash(encoded), "created": not existed, "bytes": len(encoded)}

    def run_command(self, workspace_id: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        executable = str(payload.get("executable") or "")
        if executable not in ALLOWED_EXECUTABLES: raise PermissionError("developer_executable_denied")
        arguments = [str(item) for item in list(payload.get("args") or [])]
        if len(arguments) > 100 or any("\x00" in item or len(item) > 4000 for item in arguments): raise ValueError("developer_arguments_invalid")
        root = Path(self.get(workspace_id, run_id)["path"])
        cwd = self._cwd(root, str(payload.get("cwd") or "."))
        command = FIXED_EXECUTABLES.get(executable) or shutil.which(executable, path=os.environ.get("PATH", ""))
        if not command: raise FileNotFoundError("developer_executable_not_found")
        return self._run(root, cwd, [command, *arguments], payload)

    def run_bash(self, workspace_id: str, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        script = str(payload.get("script") or "")
        if not script or len(script) > 40_000: raise ValueError("developer_script_invalid")
        lowered = script.casefold()
        if any(re.search(rf"(^|[;&|\s]){re.escape(command)}(?:\s|$)", lowered) for command in DENIED_COMMANDS):
            raise PermissionError("developer_bash_command_denied")
        if re.search(r"(?:^|\s)(?:/Users|/Volumes|/private|/etc)/", script) or re.search(r"(?:^|[^<])>>?", script):
            raise PermissionError("developer_bash_path_or_redirection_denied")
        root = Path(self.get(workspace_id, run_id)["path"])
        cwd = self._cwd(root, str(payload.get("cwd") or "."))
        return self._run(root, cwd, ["/bin/zsh", "-f", "-c", script], payload)

    def git_status(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        return self.run_command(workspace_id, run_id, {"executable": "git", "args": ["status", "--short", "--branch"], "timeoutMs": 20_000, "networkPolicy": "deny"})

    def git_diff(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        result = self.run_command(workspace_id, run_id, {"executable": "git", "args": ["diff", "--", "."], "timeoutMs": 20_000, "networkPolicy": "deny"})
        untracked = self.run_command(workspace_id, run_id, {"executable": "git", "args": ["ls-files", "--others", "--exclude-standard", "-z"], "timeoutMs": 20_000, "networkPolicy": "deny"})
        root = Path(self.get(workspace_id, run_id)["path"])
        additions = []
        for relative in str(untracked.get("stdout") or "").split("\x00"):
            if not relative:
                continue
            try:
                target = self._target(root, relative)
                if target.stat().st_size > 1_000_000:
                    additions.append(f"Binary or large untracked file: {relative}\n")
                    continue
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                additions.extend(difflib.unified_diff([], lines, fromfile="/dev/null", tofile=f"b/{relative}"))
            except (OSError, PermissionError):
                additions.append(f"Unreadable untracked file: {relative}\n")
        result["stdout"] = (str(result.get("stdout") or "") + "".join(additions))[:64_000]
        return result

    def git_commit(self, workspace_id: str, run_id: str, message: str) -> dict[str, Any]:
        if not message.strip() or len(message) > 200: raise ValueError("git_commit_message_invalid")
        self.run_command(workspace_id, run_id, {"executable": "git", "args": ["add", "--", "."], "timeoutMs": 20_000, "networkPolicy": "deny"})
        return self.run_command(workspace_id, run_id, {
            "executable": "git",
            "args": [
                "-c", "user.name=Zhixu Agent",
                "-c", "user.email=zhixu-agent@localhost",
                "commit", "-m", message.strip(),
            ],
            "timeoutMs": 60_000,
            "networkPolicy": "deny",
        })

    def merge(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        item = self.get(workspace_id, run_id)
        workspace_status = self.git_status(workspace_id, run_id)
        dirty = [line for line in str(workspace_status.get("stdout") or "").splitlines()[1:] if line.strip()]
        if dirty:
            raise RuntimeError("developer_workspace_must_be_committed_before_merge")
        main_status = subprocess.run(
            ["/usr/bin/git", "status", "--porcelain"], cwd=self.project,
            text=True, capture_output=True, timeout=30, env=self._env(self.project),
        )
        if main_status.returncode != 0 or main_status.stdout.strip():
            raise RuntimeError("developer_project_must_be_clean_before_merge")
        before = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.project,
            text=True, capture_output=True, timeout=15, env=self._env(self.project), check=True,
        ).stdout.strip()
        merged = subprocess.run(
            ["/usr/bin/git", "-c", "user.name=Zhixu Agent", "-c", "user.email=zhixu-agent@localhost", "merge", "--no-ff", "--no-edit", str(item["branch"])],
            cwd=self.project, text=True, capture_output=True, timeout=120,
            env=self._env(self.project),
        )
        if merged.returncode != 0:
            subprocess.run(["/usr/bin/git", "merge", "--abort"], cwd=self.project, text=True, capture_output=True, timeout=30, env=self._env(self.project))
            raise RuntimeError(f"developer_merge_failed:{redact_secret_text(merged.stderr)[:500]}")
        after = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.project,
            text=True, capture_output=True, timeout=15, env=self._env(self.project), check=True,
        ).stdout.strip()
        item = {**item, "status": "merged", "beforeHead": before, "afterHead": after}
        self.store.set_setting(f"developer_workspace:{workspace_id}", item)
        return {"id": workspace_id, "branch": item["branch"], "status": "merged", "beforeHead": before, "afterHead": after}

    def activation_request(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        """Validate a merged task before the Obsidian-owned deploy/restart step.

        The backend never kills or replaces itself.  It emits a bounded request
        that the plugin process manager fulfills with fixed repository scripts,
        then reports build/install/restart/health results back to the Pi loop.
        """
        item = self.get(workspace_id, run_id)
        if item.get("status") != "merged":
            raise RuntimeError("developer_workspace_must_be_merged_before_activation")
        current = subprocess.run(
            ["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.project,
            text=True, capture_output=True, timeout=15, env=self._env(self.project), check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["/usr/bin/git", "status", "--porcelain"], cwd=self.project,
            text=True, capture_output=True, timeout=30, env=self._env(self.project), check=True,
        ).stdout.strip()
        if current != str(item.get("afterHead") or "") or status:
            raise RuntimeError("developer_activation_base_changed")
        return {
            "id": workspace_id,
            "project": str(self.project),
            "mergedHead": current,
            "status": "ready_for_plugin_activation",
            "steps": ["check", "build", "install", "restart", "health"],
            "rollbackTool": "rollback_task_branch",
        }

    def rollback(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        item = self.get(workspace_id, run_id)
        target = Path(str(item["path"])).resolve()
        branch = str(item["branch"])
        if item.get("status") == "merged":
            current = subprocess.run(["/usr/bin/git", "rev-parse", "HEAD"], cwd=self.project, text=True, capture_output=True, timeout=15, env=self._env(self.project), check=True).stdout.strip()
            status = subprocess.run(["/usr/bin/git", "status", "--porcelain"], cwd=self.project, text=True, capture_output=True, timeout=30, env=self._env(self.project), check=True).stdout.strip()
            if current != str(item.get("afterHead") or "") or status:
                raise RuntimeError("developer_merged_rollback_conflict")
            reverted = subprocess.run(
                ["/usr/bin/git", "-c", "user.name=Zhixu Agent", "-c", "user.email=zhixu-agent@localhost", "revert", "-m", "1", "--no-edit", current],
                cwd=self.project, text=True, capture_output=True, timeout=120, env=self._env(self.project),
            )
            if reverted.returncode != 0:
                subprocess.run(["/usr/bin/git", "revert", "--abort"], cwd=self.project, text=True, capture_output=True, timeout=30, env=self._env(self.project))
                raise RuntimeError(f"developer_merge_revert_failed:{redact_secret_text(reverted.stderr)[:500]}")
        removed = subprocess.run(
            ["/usr/bin/git", "worktree", "remove", "--force", str(target)],
            cwd=self.project, text=True, capture_output=True, timeout=60,
            env=self._env(self.project),
        )
        if removed.returncode != 0:
            raise RuntimeError(f"developer_worktree_remove_failed:{redact_secret_text(removed.stderr)[:500]}")
        deleted = subprocess.run(
            ["/usr/bin/git", "branch", "-D", branch],
            cwd=self.project, text=True, capture_output=True, timeout=30,
            env=self._env(self.project),
        )
        if deleted.returncode != 0:
            raise RuntimeError(f"developer_branch_remove_failed:{redact_secret_text(deleted.stderr)[:500]}")
        item = {**item, "status": "rolled_back"}
        self.store.set_setting(f"developer_workspace:{workspace_id}", item)
        return {"id": workspace_id, "branch": branch, "status": "rolled_back"}

    def validate_skill(self, workspace_id: str, run_id: str, relative: str) -> dict[str, Any]:
        data = self.read(workspace_id, run_id, relative, 200_000)
        text = str(data["content"])
        errors = []
        if "name:" not in text: errors.append("manifest_name_required")
        if re.search(r"(?:API_KEY|PASSWORD|security\s+find-generic-password)", text, re.I): errors.append("secret_access_forbidden")
        if re.search(r"while\s+true|for\s*\(;;\)", text, re.I): errors.append("unbounded_loop_forbidden")
        return {"valid": not errors, "errors": errors, "path": data["path"], "sha256": data["sha256"]}

    def validate_mcp(self, workspace_id: str, run_id: str, relative: str) -> dict[str, Any]:
        data = self.read(workspace_id, run_id, relative, 200_000)
        try: manifest = json.loads(str(data["content"]))
        except json.JSONDecodeError: return {"valid": False, "errors": ["mcp_manifest_json_invalid"]}
        command = str(manifest.get("command") or "")
        errors = [] if command in ALLOWED_EXECUTABLES else ["mcp_command_denied"]
        env_names = sorted(str(key) for key in dict(manifest.get("env") or {}))
        if any(SENSITIVE_ENV.search(name) for name in env_names): errors.append("mcp_secret_env_denied")
        return {"valid": not errors, "errors": errors, "command": command, "args": list(manifest.get("args") or []), "envNames": env_names, "networkPolicy": "deny", "readablePaths": ["workspace"], "writablePaths": ["workspace"]}

    def probe_mcp(self, workspace_id: str, run_id: str, relative: str) -> dict[str, Any]:
        validation = self.validate_mcp(workspace_id, run_id, relative)
        if not validation["valid"]:
            return {**validation, "started": False, "tools": []}
        root = Path(self.get(workspace_id, run_id)["path"])
        executable = FIXED_EXECUTABLES.get(str(validation["command"])) or shutil.which(str(validation["command"]), path=os.environ.get("PATH", ""))
        if not executable:
            raise FileNotFoundError("mcp_executable_not_found")
        args = [str(item) for item in validation["args"]]
        if len(args) > 100 or any("\x00" in item or len(item) > 4000 for item in args):
            raise ValueError("mcp_arguments_invalid")
        command = ["/usr/bin/sandbox-exec", "-p", self._sandbox_profile(root), executable, *args]
        process = subprocess.Popen(
            command, cwd=root, env=self._env(root), text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "zhixu-probe", "version": "1"}}}) + "\n")
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
            process.stdin.flush()
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill(); stdout, stderr = process.communicate()
            raise TimeoutError("mcp_probe_timeout")
        rows = []
        for line in stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") == 2:
                rows = list(dict(payload.get("result") or {}).get("tools") or [])
        tools = [
            {"name": str(item.get("name") or "")[:200], "description": str(item.get("description") or "")[:1000]}
            for item in rows[:100] if isinstance(item, dict) and item.get("name")
        ]
        return {
            **validation,
            "started": process.returncode == 0,
            "exitCode": process.returncode,
            "tools": tools,
            "stderr": redact_secret_text(stderr)[:2000],
        }

    def _run(self, root: Path, cwd: Path, command: list[str], payload: dict[str, Any]) -> dict[str, Any]:
        network = str(payload.get("networkPolicy") or "deny")
        if network != "deny": raise PermissionError("developer_network_scope_expansion_required")
        timeout = max(100, min(300_000, int(payload.get("timeoutMs") or 30_000))) / 1000
        profile = self._sandbox_profile(root)
        argv = ["/usr/bin/sandbox-exec", "-p", profile, *command]
        try:
            completed = subprocess.run(argv, cwd=cwd, env=self._env(root), text=True, capture_output=True, timeout=timeout)
            expected = [str(item) for item in list(payload.get("expectedOutputs") or [])[:50]]
            output_checks = []
            for relative in expected:
                try:
                    output_checks.append({"path": relative, "exists": self._target(root, relative).is_file()})
                except (FileNotFoundError, PermissionError):
                    output_checks.append({"path": relative, "exists": False})
            return {"exitCode": completed.returncode, "stdout": redact_secret_text(completed.stdout)[:64_000], "stderr": redact_secret_text(completed.stderr)[:32_000], "timedOut": False, "cwd": cwd.relative_to(root).as_posix() or ".", "networkPolicy": "deny", "expectedOutputs": output_checks}
        except subprocess.TimeoutExpired as error:
            return {"exitCode": None, "stdout": redact_secret_text(str(error.stdout or ""))[:64_000], "stderr": "command_timeout", "timedOut": True, "cwd": cwd.relative_to(root).as_posix() or ".", "networkPolicy": "deny"}

    @staticmethod
    def _sandbox_profile(root: Path) -> str:
        escaped = str(root).replace('"', '\\"')
        runtime = str(root.parent).replace('"', '\\"')
        gitdir = ""
        git_read_rules = ""
        git_write_rules = ""
        pointer = root / ".git"
        if pointer.is_file():
            first = pointer.read_text(encoding="utf-8", errors="replace").strip()
            if first.startswith("gitdir: "):
                candidate = Path(first.removeprefix("gitdir: ")).resolve()
                gitdir = str(candidate).replace('"', '\\"')
                common = candidate.parent.parent
                try:
                    head = (candidate / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    head = ""
                branch_ref = head.removeprefix("ref: ") if head.startswith("ref: refs/heads/zhixu/") else ""
                common_escaped = str(common).replace('"', '\\"')
                git_read_rules = f' (subpath "{common_escaped}")'
                git_write_rules = f' (subpath "{gitdir}") (subpath "{common_escaped}/objects")'
                if branch_ref:
                    branch = str(common / branch_ref).replace('"', '\\"')
                    branch_log = str(common / "logs" / branch_ref).replace('"', '\\"')
                    git_write_rules += f' (literal "{branch}") (literal "{branch}.lock") (literal "{branch_log}") (literal "{branch_log}.lock")'
        ancestors = {Path("/"), *root.parents, *Path(runtime).parents}
        if gitdir:
            ancestors.update(Path(gitdir).parents)
        literal_rules = " ".join(
            f'(literal "{str(path).replace(chr(34), chr(92) + chr(34))}")'
            for path in sorted(ancestors, key=lambda item: len(str(item)))
        )
        return f'''(version 1)\n(deny default)\n(allow process*)\n(allow sysctl-read)\n(allow mach-lookup)\n(allow file-read* {literal_rules} (literal "/var") (subpath "/var/select") (subpath "{escaped}") (subpath "{runtime}/home") (subpath "{runtime}/tmp"){git_read_rules} (subpath "/System") (subpath "/usr") (subpath "/bin") (subpath "/opt") (subpath "/Library") (literal "/dev/null") (literal "/dev/urandom"))\n(allow file-write* (subpath "{escaped}") (subpath "{runtime}/home") (subpath "{runtime}/tmp"){git_write_rules} (literal "/dev/null"))\n(deny network*)'''

    @staticmethod
    def _env(root: Path) -> dict[str, str]:
        home = root.parent / "home"; temp = root.parent / "tmp"
        home.mkdir(parents=True, exist_ok=True); temp.mkdir(parents=True, exist_ok=True)
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "TMPDIR": str(temp), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "CI": "1", "NO_COLOR": "1", "GIT_CONFIG_NOSYSTEM": "1"}

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try: path.resolve().relative_to(root.resolve()); return True
        except ValueError: return False

    def _cwd(self, root: Path, relative: str) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts: raise PermissionError("developer_cwd_escape")
        target = (root / relative).resolve()
        if not self._within(target, root) or not target.is_dir() or ".git" in Path(relative).parts: raise PermissionError("developer_cwd_escape")
        return target

    def _target(self, root: Path, relative: str, allow_missing: bool = False) -> Path:
        raw = Path(relative)
        if raw.is_absolute() or ".." in raw.parts or ".git" in raw.parts: raise PermissionError("developer_path_escape")
        target = (root / raw).resolve()
        if not self._within(target, root) or (target.exists() and target.is_symlink()): raise PermissionError("developer_path_escape")
        if not allow_missing and not target.is_file(): raise FileNotFoundError("developer_file_not_found")
        return target

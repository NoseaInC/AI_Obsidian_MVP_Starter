from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.tools.vault_access import SAFE_ROOTS, safe_note


WRITE_OPERATIONS = {"create_markdown", "update_markdown"}
DEVELOPER_OPERATIONS = {
    "developer_workspace", "read_project_file", "write_project_file",
    "run_command", "run_bash", "git_commit", "merge_task_branch",
    "activate_runtime_upgrade", "rollback_task_branch", "validate_skill", "validate_mcp",
}
DEFAULT_CREATE_ROOTS = ("01-Inbox", "20-Knowledge/Drafts")


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _safe_write_target(vault: Path, relative: str) -> Path:
    target = safe_note(vault, relative)
    resolved_vault = vault.resolve()
    path = target.relative_to(resolved_vault).as_posix()
    if not any(_within(path, root) for root in SAFE_ROOTS):
        raise PermissionError("vault_write_root_denied")
    return target


class TaskAuthorizationService:
    """Deterministic run-scoped boundary; it never classifies natural language."""

    def __init__(self, vault: Path, store: Any, classify_path) -> None:
        self.vault = vault.resolve()
        self.store = store
        self.classify_path = classify_path

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        authorization = dict(payload.get("taskAuthorization") or payload)
        authorization["objective"] = str(authorization.get("objective") or "")[:20_000]
        if authorization.get("reversibleOnly") is not True:
            raise PermissionError("task_authorization_must_be_reversible")
        if authorization.get("externalSideEffects") is True:
            raise PermissionError("external_side_effects_not_allowed")
        resource = dict(authorization.get("resourceScope") or {})
        explicit = []
        for raw in list(resource.get("explicitVaultPaths") or []):
            path = _safe_write_target(self.vault, str(raw)).relative_to(self.vault).as_posix()
            if path not in explicit:
                explicit.append(path)
        resource["explicitVaultPaths"] = explicit
        resource["createRoots"] = [
            root for root in list(resource.get("createRoots") or [])
            if root in DEFAULT_CREATE_ROOTS
        ]
        authorization["resourceScope"] = resource
        authorization["operationScope"] = [
            operation for operation in list(authorization.get("operationScope") or [])
            if operation in WRITE_OPERATIONS | DEVELOPER_OPERATIONS
        ]
        return self.store.create_pi_task_authorization(authorization)

    def authorize_workspace(
        self,
        authorization_id: str,
        run_id: str,
        workspace_id: str,
        project_path: str,
    ) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        if authorization["runId"] != run_id:
            raise PermissionError("task_authorization_run_mismatch")
        resource = dict(authorization["resourceScope"])
        workspaces = set(str(value) for value in resource.get("workspaceIds") or [])
        projects = set(str(value) for value in resource.get("projectPaths") or [])
        workspaces.add(workspace_id)
        projects.add(project_path)
        resource["workspaceIds"] = sorted(workspaces)
        resource["projectPaths"] = sorted(projects)
        operations = sorted(set(authorization["operationScope"]) | DEVELOPER_OPERATIONS)
        return self.store.update_task_authorization_scope(authorization_id, resource, operations)

    def validate_workspace(
        self,
        authorization_id: str,
        run_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        if authorization["runId"] != run_id:
            raise PermissionError("task_authorization_run_mismatch")
        if workspace_id not in set(authorization["resourceScope"].get("workspaceIds") or []):
            raise PermissionError("developer_workspace_not_authorized")
        return authorization

    def plan(self, authorization_id: str, writes: list[dict[str, Any]]) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        resource = dict(authorization["resourceScope"])
        explicit = list(resource.get("explicitVaultPaths") or [])
        create_roots = list(resource.get("createRoots") or [])
        operations = list(authorization["operationScope"])
        scope_established = bool(operations)
        planned: list[dict[str, str]] = []
        for item in writes:
            relative = str(item.get("path") or "")
            target = _safe_write_target(self.vault, relative)
            normalized = target.relative_to(self.vault).as_posix()
            classification = self.classify_path(normalized, allow_missing=True)
            if target.exists() and classification.get("protected"):
                raise PermissionError("reviewed_core_read_only")
            operation = "update_markdown" if target.exists() else "create_markdown"
            if operation == "create_markdown":
                root = next((candidate for candidate in DEFAULT_CREATE_ROOTS if _within(normalized, candidate)), "")
                if not root:
                    raise PermissionError("task_create_root_requires_scope_expansion")
                if root not in create_roots:
                    if scope_established:
                        raise PermissionError("task_scope_expansion_requires_new_user_turn")
                    create_roots.append(root)
            elif normalized not in explicit:
                if scope_established:
                    raise PermissionError("task_scope_expansion_requires_new_user_turn")
                explicit.append(normalized)
            if operation not in operations:
                if scope_established:
                    raise PermissionError("task_scope_expansion_requires_new_user_turn")
                operations.append(operation)
            planned.append({"path": normalized, "operation": operation})
        resource["explicitVaultPaths"] = sorted(set(explicit))
        resource["createRoots"] = sorted(set(create_roots))
        updated = self.store.update_task_authorization_scope(
            authorization_id,
            resource,
            operations,
        )
        return {"authorization": updated, "planned": planned}

    def validate(
        self,
        authorization_id: str,
        run_id: str,
        writes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        if authorization["runId"] != run_id:
            raise PermissionError("task_authorization_run_mismatch")
        if not authorization["reversibleOnly"] or authorization["externalSideEffects"]:
            raise PermissionError("task_authorization_not_reversible")
        resource = authorization["resourceScope"]
        explicit = set(resource.get("explicitVaultPaths") or [])
        create_roots = set(resource.get("createRoots") or [])
        operations = set(authorization["operationScope"])
        validated = []
        for item in writes:
            target = _safe_write_target(self.vault, str(item.get("path") or ""))
            relative = target.relative_to(self.vault).as_posix()
            operation = "update_markdown" if target.exists() else "create_markdown"
            if operation not in operations:
                raise PermissionError("task_operation_not_authorized")
            if operation == "update_markdown" and relative not in explicit:
                raise PermissionError("task_path_not_authorized")
            if operation == "create_markdown" and not any(_within(relative, root) for root in create_roots):
                raise PermissionError("task_create_root_not_authorized")
            classification = self.classify_path(relative, allow_missing=True)
            if target.exists() and classification.get("protected"):
                raise PermissionError("reviewed_core_read_only")
            validated.append({"path": relative, "operation": operation})
        return {"authorization": authorization, "validated": validated}

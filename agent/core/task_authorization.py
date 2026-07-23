from __future__ import annotations

import hashlib
from pathlib import Path
from time import time
from typing import Any

from agent.core.frontmatter_policy import policy_metadata_from_bytes
from agent.tools.vault_access import SAFE_ROOTS, safe_note


WRITE_OPERATIONS = {
    "create_markdown",
    "update_markdown",
    "create_directory",
    "move_markdown",
}
DEVELOPER_TOOL_OPERATIONS = {
    "read_workspace_file": "read_workspace_file",
    "write_workspace_file": "write_workspace_file",
    "run_command": "run_command",
    "run_bash": "run_bash",
    "git_status": "git_status",
    "git_diff": "git_diff",
    "git_commit": "git_commit",
    "merge_task_branch": "merge_task_branch",
    "activate_runtime_upgrade": "activate_runtime_upgrade",
    "rollback_task_branch": "rollback_task_branch",
    "validate_skill_draft": "validate_skill_draft",
    "validate_mcp_server": "validate_mcp_server",
    "probe_mcp_server": "probe_mcp_server",
}
DEVELOPER_OPERATIONS = set(DEVELOPER_TOOL_OPERATIONS.values())
DEFAULT_CREATE_ROOTS = ("01-Inbox", "20-Knowledge/Drafts")
MUTABLE_ROOTS = tuple(SAFE_ROOTS)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _safe_write_target(vault: Path, relative: str) -> Path:
    target = safe_note(vault, relative)
    resolved_vault = vault.resolve()
    path = target.relative_to(resolved_vault).as_posix()
    if not any(_within(path, root) for root in MUTABLE_ROOTS):
        raise PermissionError("vault_write_root_denied")
    return target


def _safe_vault_path(vault: Path, relative: str, *, directory: bool = False) -> Path:
    """Resolve one governed Vault path without granting generic filesystem access."""
    raw = str(relative or "").strip()
    if not raw or "\\" in raw:
        raise ValueError("invalid_vault_path")
    candidate = Path(raw)
    parts = raw.split("/")
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid_vault_path")
    lexical = vault.resolve().joinpath(*parts)
    for current in (lexical, *lexical.parents):
        if current == vault.resolve():
            break
        if current.is_symlink():
            raise ValueError("symlink_path_not_allowed")
    target = lexical.resolve()
    try:
        normalized = target.relative_to(vault.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("invalid_vault_path") from error
    if not any(_within(normalized, root) for root in MUTABLE_ROOTS):
        raise PermissionError("vault_write_root_denied")
    if directory:
        if target.exists() and not target.is_dir():
            raise FileExistsError("vault_directory_collision")
    elif target.suffix.casefold() != ".md" or (target.exists() and not target.is_file()):
        raise ValueError("invalid_note_path")
    return target


def _move_key(source: str, destination: str) -> str:
    return f"{source}\n{destination}"


def _organization_body_protected(
    body: bytes,
    relative: str,
    classification: dict[str, Any],
) -> bool:
    """Keep protected notes immutable, using the exact bytes being moved."""
    metadata, valid = policy_metadata_from_bytes(body)
    if not valid:
        return True
    if metadata.get("status") in {"reviewed", "core"}:
        return True
    if metadata.get("agent_access") == "denied":
        return True
    if metadata.get("agent_protected") in {"true", "yes", "1"}:
        return True
    if relative.startswith("10-Sources/"):
        return metadata.get("type") != "source-index"
    return bool(classification.get("protected"))


def _organization_source_protected(
    target: Path,
    relative: str,
    classification: dict[str, Any],
) -> bool:
    """Keep reviewed/core immutable while allowing draft source indexes to move."""
    if not target.is_file():
        return bool(classification.get("protected"))
    return _organization_body_protected(target.read_bytes(), relative, classification)


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
        allow_all_run = resource.get("allowAllRunCapabilities") is True
        allow_all_safe_markdown = (
            resource.get("allowAllSafeMarkdown") is True or allow_all_run
        )
        allow_all_organization = (
            resource.get("allowAllSafeVaultOrganization") is True or allow_all_run
        )
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
        resource["explicitVaultDirectories"] = []
        resource["explicitVaultMoves"] = []
        # Developer workspaces are server-created, persisted resources.  Never
        # accept workspace identifiers or operation grants from the client when
        # the Turn envelope is registered.
        resource["workspaceIds"] = []
        resource["projectPaths"] = []
        resource["workspaceOperationScopes"] = {}
        resource["allowAllSafeMarkdown"] = allow_all_safe_markdown
        resource["allowAllSafeVaultOrganization"] = allow_all_organization
        # Every Plan starts unbound; the first safe write plan freezes the scope
        # deterministically without a confirmation card. No keyword/Intent Router.
        resource["writeScopeState"] = "unbound"
        resource["initialWriteToolCallId"] = None
        resource["initialWriteBoundAt"] = None
        authorization["resourceScope"] = resource
        operation_scope = [
            operation for operation in list(authorization.get("operationScope") or [])
            if operation in WRITE_OPERATIONS
        ]
        if allow_all_run:
            operation_scope.extend(sorted(WRITE_OPERATIONS))
        authorization["operationScope"] = sorted(set(operation_scope))
        return self.store.create_pi_task_authorization(authorization)

    def grant_scope(
        self,
        authorization_id: str,
        run_id: str,
        writes: list[dict[str, Any]],
        mode: str = "once",
    ) -> dict[str, Any]:
        """Grant exact Markdown targets to the same active Run."""
        authorization = self._active_for_run(authorization_id, run_id)
        if mode not in {"once", "all"}:
            raise ValueError("task_permission_mode_invalid")
        resource = dict(authorization["resourceScope"])
        explicit = set(str(value) for value in resource.get("explicitVaultPaths") or [])
        operations = set(str(value) for value in authorization["operationScope"])
        granted: list[dict[str, str]] = []
        for item in writes:
            target = _safe_write_target(self.vault, str(item.get("path") or ""))
            relative = target.relative_to(self.vault).as_posix()
            classification = self.classify_path(relative, allow_missing=True)
            if target.exists() and classification.get("protected"):
                raise PermissionError("reviewed_core_read_only")
            operation = "update_markdown" if target.exists() else "create_markdown"
            explicit.add(relative)
            operations.add(operation)
            granted.append({"path": relative, "operation": operation})
        if mode == "all":
            resource["allowAllSafeMarkdown"] = True
            operations.update({"create_markdown", "update_markdown"})
        resource["explicitVaultPaths"] = sorted(explicit)
        updated = self.store.update_task_authorization_scope(
            authorization_id, resource, sorted(operations),
        )
        return {"authorization": updated, "granted": granted, "mode": mode}

    def grant_organization(
        self,
        authorization_id: str,
        run_id: str,
        organization: dict[str, Any],
        mode: str = "once",
    ) -> dict[str, Any]:
        """Grant exact directory and move pairs for one active Run."""
        authorization = self._active_for_run(authorization_id, run_id)
        if mode not in {"once", "all"}:
            raise ValueError("task_permission_mode_invalid")
        normalized = self._normalize_organization(organization)
        resource = dict(authorization["resourceScope"])
        directories = set(str(value) for value in resource.get("explicitVaultDirectories") or [])
        moves = set(str(value) for value in resource.get("explicitVaultMoves") or [])
        cleanup_moves = set(
            str(value) for value in resource.get("explicitEmptySourceDirectoryMoves") or []
        )
        operations = set(str(value) for value in authorization["operationScope"])
        directories.update(normalized["directories"])
        moves.update(
            _move_key(item["source_path"], item["target_path"])
            for item in normalized["moves"]
        )
        if normalized["remove_empty_source_dirs"]:
            cleanup_moves.update(
                _move_key(item["source_path"], item["target_path"])
                for item in normalized["moves"]
            )
        if normalized["directories"]:
            operations.add("create_directory")
        if normalized["moves"]:
            operations.add("move_markdown")
        if mode == "all":
            resource["allowAllSafeVaultOrganization"] = True
            operations.update({"create_directory", "move_markdown"})
        resource["explicitVaultDirectories"] = sorted(directories)
        resource["explicitVaultMoves"] = sorted(moves)
        resource["explicitEmptySourceDirectoryMoves"] = sorted(cleanup_moves)
        updated = self.store.update_task_authorization_scope(
            authorization_id, resource, sorted(operations),
        )
        return {"authorization": updated, "organization": normalized, "mode": mode}

    def _active_for_run(self, authorization_id: str, run_id: str) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        if authorization["runId"] != run_id:
            raise PermissionError("task_authorization_run_mismatch")
        if not authorization["reversibleOnly"] or authorization["externalSideEffects"]:
            raise PermissionError("task_authorization_not_reversible")
        return authorization

    def ensure_organization_source_content(self, relative: str, body: bytes) -> None:
        """Recheck protection against the exact source bytes at the commit boundary."""
        classification = self.classify_path(relative, allow_missing=False)
        if _organization_body_protected(body, relative, classification):
            raise PermissionError("reviewed_core_read_only")

    def _normalize_organization(self, organization: dict[str, Any]) -> dict[str, Any]:
        raw_directories = organization.get("directories") or []
        raw_moves = organization.get("moves") or []
        remove_empty_source_dirs = (
            organization.get("remove_empty_source_dirs") is True
            or organization.get("removeEmptySourceDirs") is True
        )
        if (
            not isinstance(raw_directories, list)
            or not isinstance(raw_moves, list)
            or len(raw_directories) > 50
            or len(raw_moves) > 50
            or not (raw_directories or raw_moves)
        ):
            raise ValueError("vault_organization_requires_1_to_50_operations")
        directories: list[str] = []
        seen_directories: set[str] = set()
        for raw in raw_directories:
            target = _safe_vault_path(self.vault, str(raw), directory=True)
            relative = target.relative_to(self.vault).as_posix()
            if relative not in seen_directories:
                seen_directories.add(relative)
                directories.append(relative)
        moves: list[dict[str, str]] = []
        sources: set[str] = set()
        destinations: set[str] = set()
        for raw in raw_moves:
            if not isinstance(raw, dict):
                raise ValueError("vault_move_invalid")
            source = _safe_vault_path(
                self.vault, str(raw.get("source_path") or raw.get("from") or ""),
            )
            destination = _safe_vault_path(
                self.vault,
                str(
                    raw.get("target_path")
                    or raw.get("destination_path")
                    or raw.get("to")
                    or ""
                ),
            )
            source_relative = source.relative_to(self.vault).as_posix()
            destination_relative = destination.relative_to(self.vault).as_posix()
            if source_relative == destination_relative:
                raise ValueError("vault_move_same_path")
            if source_relative in sources or destination_relative in destinations:
                raise ValueError("vault_move_duplicate_path")
            if not source.is_file():
                raise FileNotFoundError("vault_move_source_not_found")
            classification = self.classify_path(source_relative, allow_missing=False)
            if _organization_source_protected(source, source_relative, classification):
                raise PermissionError("reviewed_core_read_only")
            if destination.exists():
                destination_classification = self.classify_path(
                    destination_relative, allow_missing=False,
                )
                if _organization_source_protected(
                    destination, destination_relative, destination_classification,
                ):
                    raise PermissionError("reviewed_core_read_only")
                raise FileExistsError("vault_move_target_collision")
            sources.add(source_relative)
            destinations.add(destination_relative)
            moves.append({
                "source_path": source_relative,
                "target_path": destination_relative,
            })
        if sources & destinations:
            raise ValueError("vault_move_chains_not_supported")
        return {
            "directories": directories,
            "moves": moves,
            "remove_empty_source_dirs": remove_empty_source_dirs,
        }

    def validate_organization(
        self,
        authorization_id: str,
        run_id: str,
        organization: dict[str, Any],
    ) -> dict[str, Any]:
        authorization = self._active_for_run(authorization_id, run_id)
        normalized = self._normalize_organization(organization)
        resource = authorization["resourceScope"]
        operations = set(authorization["operationScope"])
        allow_all = resource.get("allowAllSafeVaultOrganization") is True
        allowed_directories = set(resource.get("explicitVaultDirectories") or [])
        allowed_moves = set(resource.get("explicitVaultMoves") or [])
        allowed_cleanup_moves = set(
            resource.get("explicitEmptySourceDirectoryMoves") or []
        )
        if normalized["directories"] and "create_directory" not in operations:
            raise PermissionError("task_organization_scope_required")
        if normalized["moves"] and "move_markdown" not in operations:
            raise PermissionError("task_organization_scope_required")
        if not allow_all:
            if any(path not in allowed_directories for path in normalized["directories"]):
                raise PermissionError("task_organization_scope_required")
            if any(
                _move_key(item["source_path"], item["target_path"]) not in allowed_moves
                for item in normalized["moves"]
            ):
                raise PermissionError("task_organization_scope_required")
            if normalized["remove_empty_source_dirs"] and any(
                _move_key(item["source_path"], item["target_path"])
                not in allowed_cleanup_moves
                for item in normalized["moves"]
            ):
                raise PermissionError("task_organization_scope_required")
        return {"authorization": authorization, "organization": normalized}

    def capture_pending_recovery_guard(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        permission_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture bounded Vault state needed to safely resume a paused call.

        The guard contains paths, existence/protection flags and content hashes,
        never note bodies. Recomputing it after restart detects edits, newly
        protected notes and target collisions before an old permission card can
        be reused.
        """
        files: dict[tuple[str, str], dict[str, Any]] = {}
        directories: dict[str, dict[str, Any]] = {}

        def remember_file(relative: str, role: str) -> None:
            target = _safe_write_target(self.vault, relative)
            normalized = target.relative_to(self.vault).as_posix()
            exists = target.exists()
            classification = self.classify_path(normalized, allow_missing=not exists)
            body = target.read_bytes() if target.is_file() else b""
            files[(role, normalized)] = {
                "role": role,
                "path": normalized,
                "exists": exists,
                "isFile": target.is_file(),
                "sha256": hashlib.sha256(body).hexdigest() if target.is_file() else "missing",
                "protected": bool(classification.get("protected")) if exists else False,
            }

        def remember_directory(relative: str) -> None:
            target = _safe_vault_path(self.vault, relative, directory=True)
            normalized = target.relative_to(self.vault).as_posix()
            directories[normalized] = {
                "path": normalized,
                "exists": target.exists(),
                "isDirectory": target.is_dir(),
            }

        if tool_name == "organize_vault_notes":
            raw = permission_request.get("organization")
            organization = raw if isinstance(raw, dict) else arguments
            normalized = self._normalize_organization(organization)
            for relative in normalized["directories"]:
                remember_directory(relative)
            for move in normalized["moves"]:
                remember_file(move["source_path"], "source")
                remember_file(move["target_path"], "target")
        elif tool_name == "plan_vault_change":
            for item in list(arguments.get("writes") or []):
                if isinstance(item, dict):
                    remember_file(str(item.get("path") or ""), "target")
        elif tool_name == "plan_vault_copy":
            destination_root = str(arguments.get("destination_root") or "").strip().strip("/")
            for raw_source in list(arguments.get("source_paths") or []):
                source = str(raw_source or "")
                remember_file(source, "source")
                remember_file(f"{destination_root}/{Path(source).name}", "target")
        else:
            for item in list(permission_request.get("writes") or []):
                if not isinstance(item, dict):
                    continue
                if item.get("source_path"):
                    remember_file(str(item["source_path"]), "source")
                target = item.get("target_path") or item.get("path")
                if target:
                    remember_file(str(target), "target")
        return {
            "version": 1,
            "files": [files[key] for key in sorted(files)],
            "directories": [directories[key] for key in sorted(directories)],
        }

    def register_workspace(
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
        scopes = dict(resource.get("workspaceOperationScopes") or {})
        scopes.setdefault(workspace_id, [])
        resource["workspaceOperationScopes"] = scopes
        return self.store.update_task_authorization_scope(
            authorization_id,
            resource,
            list(authorization["operationScope"]),
        )

    def grant_workspace_operation(
        self,
        authorization_id: str,
        run_id: str,
        workspace_id: str,
        tool_name: str,
        mode: str = "once",
    ) -> dict[str, Any]:
        """Grant one exact developer operation for one persisted workspace."""
        authorization = self._active_for_run(authorization_id, run_id)
        if mode not in {"once", "all"}:
            raise ValueError("task_permission_mode_invalid")
        operation = DEVELOPER_TOOL_OPERATIONS.get(tool_name)
        if not operation:
            raise ValueError("developer_operation_invalid")
        resource = dict(authorization["resourceScope"])
        if workspace_id not in set(resource.get("workspaceIds") or []):
            raise PermissionError("developer_workspace_not_authorized")
        workspace_scopes = {
            str(key): [str(item) for item in list(value or [])]
            for key, value in dict(resource.get("workspaceOperationScopes") or {}).items()
            if isinstance(value, list)
        }
        allowed = set(workspace_scopes.get(workspace_id) or [])
        allowed.update(DEVELOPER_OPERATIONS if mode == "all" else {operation})
        workspace_scopes[workspace_id] = sorted(allowed)
        resource["workspaceOperationScopes"] = workspace_scopes
        operations = set(str(value) for value in authorization["operationScope"])
        operations.update(allowed)
        updated = self.store.update_task_authorization_scope(
            authorization_id,
            resource,
            sorted(operations),
        )
        return {
            "authorization": updated,
            "workspaceId": workspace_id,
            "operation": operation,
            "mode": mode,
        }

    def validate_workspace(
        self,
        authorization_id: str,
        run_id: str,
        workspace_id: str,
        tool_name: str,
    ) -> dict[str, Any]:
        authorization = self._active_for_run(authorization_id, run_id)
        if workspace_id not in set(authorization["resourceScope"].get("workspaceIds") or []):
            raise PermissionError("developer_workspace_not_authorized")
        operation = DEVELOPER_TOOL_OPERATIONS.get(tool_name)
        if not operation:
            raise ValueError("developer_operation_invalid")
        workspace_scopes = dict(
            authorization["resourceScope"].get("workspaceOperationScopes") or {}
        )
        allowed = set(str(value) for value in list(workspace_scopes.get(workspace_id) or []))
        if operation not in allowed:
            raise PermissionError("developer_operation_not_authorized")
        return authorization

    def plan(self, authorization_id: str, writes: list[dict[str, Any]]) -> dict[str, Any]:
        authorization = self.store.get_task_authorization(authorization_id)
        if authorization["status"] != "active":
            raise PermissionError("task_authorization_expired")
        resource = dict(authorization["resourceScope"])
        explicit = set(resource.get("explicitVaultPaths") or [])
        create_roots = set(resource.get("createRoots") or [])
        operations = set(authorization["operationScope"])
        allow_all_safe_markdown = resource.get("allowAllSafeMarkdown") is True
        planned: list[dict[str, str]] = []
        for item in writes:
            relative = str(item.get("path") or "")
            target = _safe_write_target(self.vault, relative)
            normalized = target.relative_to(self.vault).as_posix()
            classification = self.classify_path(normalized, allow_missing=True)
            if target.exists() and classification.get("protected"):
                raise PermissionError("reviewed_core_read_only")
            operation = "update_markdown" if target.exists() else "create_markdown"
            if operation not in operations:
                raise PermissionError(
                    "task_create_scope_required"
                    if operation == "create_markdown"
                    else "task_update_scope_required"
                )
            if not allow_all_safe_markdown and normalized not in explicit:
                if not (
                    operation == "create_markdown"
                    and any(_within(normalized, root) for root in create_roots)
                ):
                    raise PermissionError(
                        "task_create_scope_required"
                        if operation == "create_markdown"
                        else "task_update_scope_required"
                    )
            planned.append({"path": normalized, "operation": operation})
        return {"authorization": authorization, "planned": planned}

    def bind_initial_markdown_plan(
        self,
        authorization_id: str,
        run_id: str,
        tool_call_id: str,
        writes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Freeze the first reversible Markdown write plan without a card.

        The model never grants scope by prose; the first *safe* plan call does.
        Rules enforced atomically under the StateStore lock:
          * the authorization is active and matches this run;
          * it is reversible-only with no external side effects;
          * ``writeScopeState`` is ``"unbound"`` (or already bound by this exact
            ``tool_call_id`` for the same logical plan, e.g. batched copy);
          * a *different* bound ``tool_call_id`` is rejected — that is a real
            scope expansion and must surface a permission card;
          * 1–10 writes, each a safe governed Vault path;
          * update requires the path to be an explicit scoped path;
          * create is allowed only inside ``DEFAULT_CREATE_ROOTS``;
          * reviewed/core/protected targets are never auto-bound;
          * idempotent on the same ``tool_call_id``.
        """
        writes = list(writes or [])
        if not (1 <= len(writes) <= 10):
            raise ValueError("initial_write_plan_requires_1_to_10_writes")
        with self.store.lock:
            authorization = self._active_for_run(authorization_id, run_id)
            resource = dict(authorization["resourceScope"])
            already_bound = resource.get("writeScopeState") == "bound"
            if already_bound and resource.get("initialWriteToolCallId") != tool_call_id:
                # A later plan in the same Run wants more scope: that is an
                # expansion, not the first freeze, so refuse auto-binding.
                raise PermissionError("task_initial_write_scope_already_bound")
            explicit = set(str(value) for value in resource.get("explicitVaultPaths") or [])
            operations = set(str(value) for value in authorization["operationScope"])
            granted: list[dict[str, str]] = []
            for item in writes:
                relative = str(item.get("path") or "")
                target = _safe_write_target(self.vault, relative)
                normalized = target.relative_to(self.vault).as_posix()
                classification = self.classify_path(normalized, allow_missing=True)
                if target.exists() and classification.get("protected"):
                    raise PermissionError("reviewed_core_read_only")
                operation = "update_markdown" if target.exists() else "create_markdown"
                if operation == "update_markdown":
                    if normalized not in explicit:
                        raise PermissionError("task_update_scope_required")
                else:
                    if not any(_within(normalized, root) for root in DEFAULT_CREATE_ROOTS):
                        raise PermissionError("task_create_scope_required")
                explicit.add(normalized)
                operations.add(operation)
                granted.append({"path": normalized, "operation": operation})
            resource = dict(resource)
            resource["explicitVaultPaths"] = sorted(explicit)
            resource["writeScopeState"] = "bound"
            resource["initialWriteToolCallId"] = tool_call_id
            resource["initialWriteBoundAt"] = time()
            updated = self.store.update_task_authorization_scope(
                authorization_id, resource, sorted(operations),
            )
        return {
            "authorization": updated,
            "bound": not already_bound,
            "granted": granted,
        }

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
        allow_all_safe_markdown = resource.get("allowAllSafeMarkdown") is True
        validated = []
        for item in writes:
            target = _safe_write_target(self.vault, str(item.get("path") or ""))
            relative = target.relative_to(self.vault).as_posix()
            operation = "update_markdown" if target.exists() else "create_markdown"
            if operation not in operations:
                raise PermissionError(
                    "task_create_scope_required"
                    if operation == "create_markdown"
                    else "task_update_scope_required"
                )
            if operation == "update_markdown" and not allow_all_safe_markdown and relative not in explicit:
                raise PermissionError("task_update_scope_required")
            if (
                operation == "create_markdown"
                and not allow_all_safe_markdown
                and relative not in explicit
                and not any(_within(relative, root) for root in create_roots)
            ):
                raise PermissionError("task_create_scope_required")
            classification = self.classify_path(relative, allow_missing=True)
            if target.exists() and classification.get("protected"):
                raise PermissionError("reviewed_core_read_only")
            validated.append({"path": relative, "operation": operation})
        return {"authorization": authorization, "validated": validated}

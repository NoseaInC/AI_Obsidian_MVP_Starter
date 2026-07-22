from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.core.models import FakeKeyStore
from agent.core.service import AgentService


class _AgentProvider:
    def __init__(self) -> None:
        self.request = None

    def stream_agent(self, model, messages, *, tools=None, **options):
        self.request = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "options": options,
        }
        yield {"type": "thinking_delta", "delta": "transport-only"}
        yield {"type": "text_delta", "delta": "先检查 Vault。"}
        yield {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call-1",
            "name": "list_vault_folder",
            "arguments_delta": '{"path":"/"}',
        }
        yield {
            "type": "finish",
            "finish_reason": "tool_calls",
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }


class _RejectedProvider:
    def stream_agent(self, model, messages, *, tools=None, **options):
        error = RuntimeError("provider payload must not reach the UI")
        error.code = 400
        raise error
        yield  # pragma: no cover - keep this a generator


class _TruncatedToolProvider:
    def stream_agent(self, model, messages, *, tools=None, **options):
        yield {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call-truncated",
            "name": "plan_vault_change",
            "arguments_delta": '{"title":"incomplete",',
        }
        yield {"type": "finish", "finish_reason": "length", "usage": {"completion_tokens": 32}}


class _MalformedToolProvider:
    def stream_agent(self, model, messages, *, tools=None, **options):
        yield {
            "type": "tool_call_delta",
            "index": 0,
            "id": "call-malformed",
            "name": "plan_vault_change",
            "arguments_delta": '{"title":}',
        }
        yield {"type": "finish", "finish_reason": "tool_calls"}


class PiModelProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name) / "Vault"
        self.vault.mkdir()
        self.keys = FakeKeyStore()
        self.service = AgentService(self.vault, key_store=self.keys)
        self.profile = self.service.save_model_profile(
            {
                "displayName": "Pi 离线测试",
                "providerType": "openai-compatible",
                "baseUrl": "http://127.0.0.1:9000/v1",
                "apiKey": "pi-secret-never-return",
                "defaultModel": "fake-tool-model",
                "availableModels": ["fake-tool-model"],
                "settings": {
                    "customHeaders": {},
                    "nativeToolCalling": True,
                    "streamedToolCalls": True,
                    "parallelToolCalls": False,
                },
            }
        )

    def tearDown(self) -> None:
        self.service.store.close()
        self.temp.cleanup()

    def test_one_request_proxy_preserves_ordered_text_tool_and_usage_events(self) -> None:
        provider = _AgentProvider()
        body = {
            "profileId": self.profile["id"],
            "model": "fake-tool-model",
            "context": {
                "systemPrompt": "Use tools for Vault facts.",
                "messages": [{"role": "user", "content": "列出目录"}],
                "tools": [
                    {
                        "name": "list_vault_folder",
                        "description": "List folder",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                    }
                ],
            },
            "options": {"maxTokens": 1000},
        }
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.stream_model_proxy(body))
        event_types = [item["type"] for item in events]
        self.assertEqual(
            event_types,
            [
                "start",
                "thinking_start",
                "thinking_delta",
                "text_start",
                "text_delta",
                "tool_call_start",
                "tool_call_delta",
                "text_end",
                "thinking_end",
                "tool_call_end",
                "usage",
                "done",
            ],
        )
        self.assertEqual(events[-1]["finishReason"], "toolUse")
        self.assertEqual(events[-3]["arguments"], '{"path":"/"}')
        self.assertEqual(provider.request["messages"][0]["role"], "system")
        self.assertEqual(provider.request["tools"][0]["function"]["name"], "list_vault_folder")
        self.assertNotIn("pi-secret-never-return", json.dumps(events))

    def test_transport_thinking_is_not_sent_back_as_normal_assistant_content(self) -> None:
        provider = _AgentProvider()
        body = {
            "profileId": self.profile["id"],
            "context": {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "private-provider-block"},
                            {"type": "text", "text": "visible answer"},
                        ],
                    },
                    {"role": "user", "content": "continue"},
                ],
                "tools": [],
            },
        }
        with patch.object(self.service.models, "provider", return_value=provider):
            list(self.service.stream_model_proxy(body))
        encoded = json.dumps(provider.request["messages"])
        self.assertIn("visible answer", encoded)
        self.assertNotIn("private-provider-block", encoded)

    def test_provider_neutral_placeholder_falls_back_to_profile_model(self) -> None:
        provider = _AgentProvider()
        body = {
            "profileId": self.profile["id"],
            "model": "configured-assistant-model",
            "context": {"messages": [{"role": "user", "content": "continue"}], "tools": []},
        }
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.stream_model_proxy(body))
        self.assertEqual(provider.request["model"], "fake-tool-model")
        self.assertEqual(events[0]["model"], "fake-tool-model")

    def test_single_configured_profile_is_used_when_auto_route_is_empty(self) -> None:
        provider = _AgentProvider()
        body = {
            "context": {"messages": [{"role": "user", "content": "continue"}], "tools": []},
        }
        with patch.object(self.service.models, "provider", return_value=provider):
            events = list(self.service.stream_model_proxy(body))
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[0]["model"], "fake-tool-model")
        self.assertEqual(events[-1]["type"], "done")

    def test_startup_validation_failure_is_a_terminal_model_protocol_error(self) -> None:
        events = list(self.service.stream_model_proxy({
            "profileId": self.profile["id"],
            "context": {"messages": "not-a-list", "tools": []},
        }))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "error")
        self.assertEqual(events[0]["code"], "model_messages_invalid")

    def test_provider_rejection_is_a_terminal_model_protocol_error(self) -> None:
        body = {
            "profileId": self.profile["id"],
            "model": "missing-model",
            "context": {"messages": [{"role": "user", "content": "continue"}], "tools": []},
        }
        with patch.object(self.service.models, "provider", return_value=_RejectedProvider()):
            events = list(self.service.stream_model_proxy(body))
        self.assertEqual([item["type"] for item in events], ["start", "error"])
        self.assertEqual(events[-1]["code"], "model_provider_rejected")
        self.assertNotIn("payload", json.dumps(events, ensure_ascii=False))

    def test_profile_output_limit_is_not_silently_clamped_to_32k(self) -> None:
        profile = self.service.save_model_profile({
            "displayName": "Large output profile",
            "providerType": "openai-compatible",
            "baseUrl": "http://127.0.0.1:9000/v1",
            "apiKey": "large-output-secret",
            "defaultModel": "large-output-model",
            "availableModels": ["large-output-model"],
            "settings": {"maxTokens": 96_000, "contextWindow": 500_000, "customHeaders": {}},
        })
        provider = _AgentProvider()
        with patch.object(self.service.models, "provider", return_value=provider):
            list(self.service.stream_model_proxy({
                "profileId": profile["id"],
                "context": {"messages": [{"role": "user", "content": "continue"}], "tools": []},
                "options": {"maxTokens": 96_000},
            }))
        self.assertEqual(provider.request["options"]["max_tokens"], 96_000)

    def test_truncated_tool_json_returns_specific_protocol_error(self) -> None:
        body = {
            "profileId": self.profile["id"],
            "context": {"messages": [{"role": "user", "content": "copy notes"}], "tools": []},
        }
        with patch.object(self.service.models, "provider", return_value=_TruncatedToolProvider()):
            events = list(self.service.stream_model_proxy(body))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "model_tool_arguments_truncated")
        self.assertNotIn("tool_call_end", [event["type"] for event in events])

    def test_malformed_tool_json_returns_specific_protocol_error(self) -> None:
        body = {
            "profileId": self.profile["id"],
            "context": {"messages": [{"role": "user", "content": "copy notes"}], "tools": []},
        }
        with patch.object(self.service.models, "provider", return_value=_MalformedToolProvider()):
            events = list(self.service.stream_model_proxy(body))
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "model_tool_arguments_invalid")

    def test_runtime_exposes_only_read_and_proposal_tools_to_pi(self) -> None:
        contracts = self.service.tool_contracts()["items"]
        levels = {item["permission_level"] for item in contracts}
        names = {item["name"] for item in contracts}
        self.assertLessEqual(levels, {"read_only", "proposal"})
        self.assertIn("list_vault_folder", names)
        self.assertIn("get_current_note", names)
        self.assertIn("read_vault_note", names)
        self.assertIn("find_related_notes", names)
        self.assertIn("search_public_web", names)
        self.assertIn("fetch_public_url", names)
        self.assertIn("plan_vault_change", names)
        self.assertIn("plan_vault_copy", names)
        self.assertIn("apply_vault_change", names)
        self.assertIn("undo_agent_action", names)
        self.assertIn("activate_runtime_upgrade", names)
        self.assertNotIn("create_change_set", names)
        self.assertNotIn("apply_confirmed_change_set", names)
        self.assertNotIn("validate_change_set", names)

    def test_runtime_tool_call_uses_real_safe_folder_tool(self) -> None:
        (self.vault / "20-Knowledge").mkdir()
        response = self.service.call_runtime_tool(
            {
                "runId": "run-1",
                "turnId": "turn-1",
                "toolCallId": "call-1",
                "toolName": "list_vault_folder",
                "arguments": {"path": "/"},
                "networkAuthorized": False,
            }
        )
        self.assertTrue(response["ok"])
        self.assertIn("20-Knowledge", json.dumps(response, ensure_ascii=False))

    def test_stable_current_note_alias_reads_real_vault_content(self) -> None:
        target = self.vault / "20-Knowledge/Current.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# 当前笔记\n\n可验证正文。\n", encoding="utf-8")
        response = self.service.call_runtime_tool(
            {
                "runId": "run-current",
                "turnId": "turn-current",
                "toolCallId": "call-current",
                "toolName": "get_current_note",
                "arguments": {"path": "20-Knowledge/Current.md"},
                "networkAuthorized": False,
            }
        )
        self.assertTrue(response["ok"])
        self.assertIn("可验证正文", json.dumps(response, ensure_ascii=False))

    def test_long_note_reads_are_paged_without_an_8000_character_ceiling(self) -> None:
        target = self.vault / "20-Knowledge/Long.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        body = "# Long\n\n" + "一二三四五六七八九十" * 6000
        target.write_text(body, encoding="utf-8")
        first = self.service.call_runtime_tool({
            "runId": "run-long", "turnId": "turn-long", "toolCallId": "call-long-1",
            "toolName": "read_vault_note",
            "arguments": {"path": "20-Knowledge/Long.md", "max_chars": 50_000},
            "networkAuthorized": False,
        })
        self.assertTrue(first["ok"])
        self.assertEqual(len(first["content"]["content"]), 50_000)
        self.assertTrue(first["content"]["truncated"])
        second = self.service.call_runtime_tool({
            "runId": "run-long", "turnId": "turn-long", "toolCallId": "call-long-2",
            "toolName": "read_vault_note",
            "arguments": {
                "path": "20-Knowledge/Long.md",
                "offset": first["content"]["next_offset"],
                "max_chars": 50_000,
            },
            "networkAuthorized": False,
        })
        self.assertEqual(first["content"]["content"] + second["content"]["content"], body)
        self.assertFalse(second["content"]["truncated"])

    def test_same_run_can_grant_one_persisted_developer_workspace_and_retry(self) -> None:
        run_id = "run-developer-grant"
        authorization_id = "authorization-developer-grant"
        workspace_id = "workspace-developer-grant"
        worktree = self.service.developer_workspace.root / workspace_id / "worktree"
        worktree.mkdir(parents=True)
        (worktree / "README.md").write_text("# isolated fixture\n", encoding="utf-8")
        self.service.store.set_setting(f"developer_workspace:{workspace_id}", {
            "id": workspace_id,
            "runId": run_id,
            "path": str(worktree),
            "branch": "zhixu/developer-grant",
            "project": str(self.vault),
            "status": "active",
        })
        self.service.task_authorizations.create({
            "id": authorization_id,
            "sessionId": "session-developer-grant",
            "runId": run_id,
            "turnId": "turn-developer-grant",
            "sourceMessageId": "message-developer-grant",
            "objective": "在受控开发 worktree 中读取项目文件",
            "resourceScope": {
                "currentNote": False,
                "explicitVaultPaths": [],
                "createRoots": [],
                "workspaceIds": [],
                "projectPaths": [],
            },
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })
        request = {
            "runId": run_id,
            "turnId": "turn-developer-grant",
            "toolCallId": "call-read-workspace",
            "toolName": "read_workspace_file",
            "taskAuthorizationId": authorization_id,
            "arguments": {"workspace_id": workspace_id, "path": "README.md"},
            "networkAuthorized": False,
        }
        blocked = self.service.call_runtime_tool(request)
        self.assertFalse(blocked["ok"], blocked)
        self.assertEqual(blocked["error"]["code"], "developer_workspace_not_authorized")

        expanded = self.service.expand_task_authorization(authorization_id, {
            "runId": run_id,
            "mode": "once",
            "capability": {
                "type": "developer_workspace",
                "workspaceId": workspace_id,
                "toolName": "read_workspace_file",
                # Must be ignored in favour of the persisted workspace record.
                "projectPath": "/tmp/untrusted-project",
            },
        })
        self.assertIn(workspace_id, expanded["taskAuthorization"]["resourceScope"]["workspaceIds"])
        self.assertNotIn("/tmp/untrusted-project", expanded["taskAuthorization"]["resourceScope"]["projectPaths"])
        self.assertEqual(
            expanded["taskAuthorization"]["resourceScope"]["workspaceOperationScopes"][workspace_id],
            ["read_workspace_file"],
        )

        retried = self.service.call_runtime_tool(request)
        self.assertTrue(retried["ok"], retried)
        self.assertEqual(retried["content"]["content"], "# isolated fixture\n")

        write_blocked = self.service.call_runtime_tool({
            **request,
            "toolCallId": "call-write-workspace-not-granted",
            "toolName": "write_workspace_file",
            "arguments": {
                "workspace_id": workspace_id,
                "path": "README.md",
                "content": "must not be written",
            },
        })
        self.assertFalse(write_blocked["ok"], write_blocked)
        self.assertEqual(write_blocked["error"]["code"], "developer_operation_not_authorized")

    def test_created_workspace_requires_exact_read_write_and_merge_grants_in_same_run(self) -> None:
        subprocess.run(["git", "init", "-q"], cwd=self.vault, check=True)
        (self.vault / ".gitignore").write_text("90-Local-Only/\n", encoding="utf-8")
        (self.vault / "README.md").write_text("# governed project\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=self.vault, check=True)
        subprocess.run(
            [
                "git", "-c", "user.name=Test", "-c", "user.email=test@localhost",
                "commit", "-qm", "base",
            ],
            cwd=self.vault,
            check=True,
        )
        run_id = "run-exact-developer-operations"
        authorization_id = "authorization-exact-developer-operations"
        self.service.task_authorizations.create({
            "id": authorization_id,
            "sessionId": "session-exact-developer-operations",
            "runId": run_id,
            "turnId": "turn-exact-developer-operations",
            "sourceMessageId": "message-exact-developer-operations",
            "objective": "在隔离 worktree 中读取、修改并合并一个文件",
            "resourceScope": {
                "currentNote": False,
                "explicitVaultPaths": [],
                "createRoots": [],
                "workspaceIds": ["client-forged-workspace"],
                "projectPaths": ["/tmp/client-forged-project"],
            },
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })
        created = self.service.call_runtime_tool({
            "runId": run_id,
            "turnId": "turn-exact-developer-operations",
            "toolCallId": "call-create-worktree",
            "toolName": "create_git_worktree",
            "taskAuthorizationId": authorization_id,
            "arguments": {},
            "networkAuthorized": False,
        })
        self.assertTrue(created["ok"], created)
        workspace_id = str(created["content"]["id"])
        registered = self.service.store.get_task_authorization(authorization_id)
        self.assertEqual(registered["resourceScope"]["workspaceIds"], [workspace_id])
        self.assertEqual(
            registered["resourceScope"]["workspaceOperationScopes"],
            {workspace_id: []},
        )
        self.assertFalse(set(registered["operationScope"]) & {
            "read_workspace_file", "write_workspace_file", "merge_task_branch",
        })

        def request(tool_name: str, call_id: str, arguments: dict[str, object]) -> dict[str, object]:
            return {
                "runId": run_id,
                "turnId": "turn-exact-developer-operations",
                "toolCallId": call_id,
                "toolName": tool_name,
                "taskAuthorizationId": authorization_id,
                "arguments": arguments,
                "networkAuthorized": False,
            }

        def grant_and_retry(payload: dict[str, object]) -> dict[str, object]:
            blocked = self.service.call_runtime_tool(payload)
            self.assertFalse(blocked["ok"], blocked)
            self.assertEqual(blocked["error"]["code"], "developer_operation_not_authorized")
            permission = blocked["error"]["permissionRequest"]
            self.assertEqual(permission["workspaceId"], workspace_id)
            self.assertEqual(permission["toolName"], payload["toolName"])
            self.service.expand_task_authorization(authorization_id, {
                "runId": run_id,
                "mode": "once",
                # Pi always includes this empty list. The typed developer
                # capability must take precedence over it.
                "writes": [],
                "capability": permission,
            })
            return self.service.call_runtime_tool(payload)

        read_call = request(
            "read_workspace_file",
            "call-read-exact",
            {"workspace_id": workspace_id, "path": "README.md"},
        )
        read = grant_and_retry(read_call)
        self.assertTrue(read["ok"], read)
        self.assertEqual(read["content"]["content"], "# governed project\n")

        write_call = request(
            "write_workspace_file",
            "call-write-exact",
            {
                "workspace_id": workspace_id,
                "path": "feature.txt",
                "content": "governed change\n",
            },
        )
        write = grant_and_retry(write_call)
        self.assertTrue(write["ok"], write)
        self.assertTrue(write["content"]["created"])

        commit_call = request(
            "git_commit",
            "call-commit-exact",
            {"workspace_id": workspace_id, "message": "feat: governed change"},
        )
        committed = grant_and_retry(commit_call)
        self.assertTrue(committed["ok"], committed)
        self.assertEqual(committed["content"]["exitCode"], 0)

        merge_call = request(
            "merge_task_branch",
            "call-merge-exact",
            {"workspace_id": workspace_id},
        )
        merged = grant_and_retry(merge_call)
        self.assertTrue(merged["ok"], merged)
        self.assertEqual(merged["content"]["status"], "merged")
        self.assertEqual((self.vault / "feature.txt").read_text(encoding="utf-8"), "governed change\n")

        final_scope = self.service.store.get_task_authorization(authorization_id)
        self.assertEqual(
            set(final_scope["resourceScope"]["workspaceOperationScopes"][workspace_id]),
            {
                "read_workspace_file",
                "write_workspace_file",
                "git_commit",
                "merge_task_branch",
            },
        )

    def test_harness_batch_copy_preserves_long_notes_and_applies_all_batches(self) -> None:
        source_root = self.vault / "20-Knowledge/Concepts"
        source_root.mkdir(parents=True, exist_ok=True)
        expected: dict[str, str] = {}
        paths: list[str] = []
        for index in range(12):
            text = f"# Concept {index}\n\n" + (f"完整正文-{index}-" * 1500) + "\n"
            path = source_root / f"Concept-{index}.md"
            path.write_text(text, encoding="utf-8")
            paths.append(path.relative_to(self.vault).as_posix())
            expected[path.name] = text
        authorization_id = "authorization-copy"
        self.service.task_authorizations.create({
            "id": authorization_id,
            "sessionId": "session-copy",
            "runId": "run-copy",
            "turnId": "turn-copy",
            "sourceMessageId": "message-copy",
            "objective": "完整复制概念笔记到草稿目录",
            "resourceScope": {"currentNote": False, "explicitVaultPaths": [], "createRoots": []},
            "operationScope": [],
            "reversibleOnly": True,
            "networkPolicy": "deny",
            "externalSideEffects": False,
            "expiresAtRunEnd": True,
        })
        # The first safe copy plan freezes the scope automatically: no card.
        planned = self.service.call_runtime_tool({
            "runId": "run-copy", "turnId": "turn-copy", "toolCallId": "call-copy-plan",
            "toolName": "plan_vault_copy", "taskAuthorizationId": authorization_id,
            "arguments": {
                "title": "概念笔记副本",
                "source_paths": paths,
                "destination_root": "20-Knowledge/Drafts/Concept-Copies",
            },
            "networkAuthorized": False,
        })
        self.assertTrue(planned["ok"], planned)
        self.assertNotIn("permissionRequest", planned.get("error", {}))
        scope = self.service.store.get_task_authorization(authorization_id)["resourceScope"]
        self.assertEqual(scope["writeScopeState"], "bound")
        self.assertEqual(scope["initialWriteToolCallId"], "call-copy-plan")
        change_sets = planned["content"]["change_sets"]
        self.assertEqual([item["fileCount"] for item in change_sets], [10, 2])
        self.assertNotIn("完整正文", json.dumps(planned, ensure_ascii=False))
        for index, change_set in enumerate(change_sets):
            applied = self.service.call_runtime_tool({
                "runId": "run-copy", "turnId": "turn-copy", "toolCallId": f"call-copy-apply-{index}",
                "toolName": "apply_vault_change", "taskAuthorizationId": authorization_id,
                "sourceMessageId": "message-copy",
                "arguments": {"change_set_id": change_set["id"]},
                "networkAuthorized": False,
            })
            self.assertTrue(applied["ok"], applied)
        for name, text in expected.items():
            copied = self.vault / "20-Knowledge/Drafts/Concept-Copies" / name
            self.assertEqual(copied.read_text(encoding="utf-8"), text)
        # A later copy to a *different* destination in the same Run is an
        # expansion and still requires a permission card.
        later = self.service.call_runtime_tool({
            "runId": "run-copy", "turnId": "turn-copy", "toolCallId": "call-copy-plan-2",
            "toolName": "plan_vault_copy", "taskAuthorizationId": authorization_id,
            "arguments": {
                "title": "另一批副本",
                "source_paths": paths,
                "destination_root": "20-Knowledge/Drafts/Other-Copies",
            },
            "networkAuthorized": False,
        })
        self.assertFalse(later["ok"], later)
        request = later["error"]["permissionRequest"]
        self.assertEqual(request["type"], "vault_writes")
        self.service.expand_task_authorization(authorization_id, {
            "runId": "run-copy",
            "mode": "once",
            "writes": request["writes"],
        })
        retried = self.service.call_runtime_tool({
            "runId": "run-copy", "turnId": "turn-copy", "toolCallId": "call-copy-plan-2",
            "toolName": "plan_vault_copy", "taskAuthorizationId": authorization_id,
            "arguments": {
                "title": "另一批副本",
                "source_paths": paths,
                "destination_root": "20-Knowledge/Drafts/Other-Copies",
            },
            "networkAuthorized": False,
        })
        self.assertTrue(retried["ok"], retried)

    def test_web_tools_exist_but_require_turn_scoped_network_authorization(self) -> None:
        denied = self.service.call_runtime_tool(
            {
                "runId": "run-web",
                "turnId": "turn-web",
                "toolCallId": "call-web-denied",
                "toolName": "search_public_web",
                "arguments": {"query": "Pi agent runtime", "limit": 3},
                "networkAuthorized": False,
            }
        )
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], "network_authorization_required")
        with patch.object(self.service.web, "search", return_value={"results": [{"title": "Pi"}]}) as search:
            allowed = self.service.call_runtime_tool(
                {
                    "runId": "run-web",
                    "turnId": "turn-web",
                    "toolCallId": "call-web-allowed",
                    "toolName": "search_public_web",
                    "arguments": {"query": "Pi agent runtime", "limit": 3},
                    "networkAuthorized": True,
                }
            )
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["content"]["results"][0]["title"], "Pi")
        search.assert_called_once_with("Pi agent runtime", 3)


if __name__ == "__main__":
    unittest.main()

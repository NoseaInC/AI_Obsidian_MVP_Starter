from __future__ import annotations

import json
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

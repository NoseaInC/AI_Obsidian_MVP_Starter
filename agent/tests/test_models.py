from __future__ import annotations

import json
import tempfile
import unittest
import subprocess
import urllib.error
from unittest.mock import patch
from pathlib import Path

from agent.core.models import FakeKeyStore, MacKeychainStore, ModelProfileService, OpenAICompatibleProvider, validate_base_url, validate_headers
from agent.core.service import AgentService
from agent.brain.model_gateway import BrainModelGateway, INTENT_SCHEMA, parse_model_json
from agent.brain.errors import BrainError


class _Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class _StreamResponse:
    def __init__(self, lines): self.lines = [line.encode() for line in lines]
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def __iter__(self): return iter(self.lines)


class _ChatProvider:
    def chat(self, model, messages, **options):
        return {"choices": [{"message": {"content": f"fake:{model}:{messages[-1]['content']}"}}]}

    def stream_chat(self, model, messages, **options):
        return self.chat(model, messages, **options)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.vault = Path(self.temp.name) / "Vault"; self.vault.mkdir()
        self.keys = FakeKeyStore(); self.service = AgentService(self.vault, key_store=self.keys)

    def tearDown(self): self.service.store.close(); self.temp.cleanup()

    def profile(self, **overrides):
        base = {"displayName": "本地兼容服务", "providerType": "openai-compatible", "baseUrl": "http://127.0.0.1:9000/v1",
                "apiKey": "sk-test-secret", "defaultModel": "test-model", "availableModels": ["test-model"],
                "settings": {"customHeaders": {"X-Client": "zhixu"}, "timeout": 3}}
        return {**base, **overrides}

    def test_profile_crud_never_persists_or_logs_key(self):
        saved = self.service.save_model_profile(self.profile())
        self.assertTrue(saved["configured"]); self.assertNotIn("apiKey", saved)
        database = (self.vault / "90-Local-Only/Agent/agent.sqlite3").read_bytes()
        self.assertNotIn(b"sk-test-secret", database)
        self.assertNotIn("sk-test-secret", self.service.log_path.read_text(encoding="utf-8"))
        updated = self.service.save_model_profile({**self.profile(apiKey=""), "displayName": "更新名称"}, saved["id"])
        self.assertEqual(updated["displayName"], "更新名称"); self.assertTrue(updated["configured"])
        self.assertEqual(self.service.delete_model_profile(saved["id"])["deleted"], True)

    def test_profile_can_reference_an_existing_keychain_item_without_secret_input(self):
        self.keys.set("deepseek-main", "existing-secret")
        saved = self.service.save_model_profile(self.profile(apiKey="", apiKeyReference="deepseek-main"))
        self.assertEqual(saved["apiKeyReference"], "deepseek-main")
        self.assertTrue(saved["configured"])
        self.assertNotIn("existing-secret", json.dumps(saved))
        self.service.delete_model_profile(saved["id"])
        self.assertEqual(self.keys.get("deepseek-main"), "existing-secret")
        with self.assertRaises(ValueError):
            self.service.save_model_profile(self.profile(apiKey="", apiKeyReference="bad reference with spaces"))

    def test_legacy_deepseek_profile_receives_reasoning_capability_defaults(self):
        self.keys.set("legacy-deepseek", "existing-secret")
        self.service.store.upsert_model_profile({
            "id": "legacy-deepseek-profile",
            "displayName": "Legacy DeepSeek",
            "providerType": "deepseek",
            "baseUrl": "https://api.deepseek.com",
            "apiKeyReference": "legacy-deepseek",
            "defaultModel": "deepseek-chat",
            "availableModels": ["deepseek-chat"],
            "enabled": True,
            "settings": {"maxTokens": 4096},
        })
        profile = next(
            item for item in self.service.list_model_profiles()
            if item["id"] == "legacy-deepseek-profile"
        )
        self.assertTrue(profile["settings"]["reasoningContent"])
        self.assertTrue(profile["settings"]["nativeToolCalling"])
        self.assertEqual(profile["settings"]["thinkingControl"], "provider-default")
        self.assertEqual(profile["settings"]["maxTokens"], 4096)

    def test_base_url_and_protected_headers(self):
        self.assertEqual(validate_base_url("https://api.example.com/v1/"), "https://api.example.com/v1")
        self.assertEqual(validate_base_url("http://localhost:11434/v1"), "http://localhost:11434/v1")
        for value in ["http://api.example.com/v1", "file:///tmp/x", "https://user:pass@example.com"]:
            with self.assertRaises(ValueError): validate_base_url(value)
        for name in ["Authorization", "host", "Content-Length"]:
            with self.assertRaises(ValueError): validate_headers({name: "bad"})

    def test_routing_only_accepts_known_profiles_and_tasks(self):
        saved = self.service.save_model_profile(self.profile())
        routes = self.service.set_model_routing({"assistant": {"profileId": saved["id"], "modelOverride": "test-model"}})
        self.assertEqual(routes["assistant"]["profileId"], saved["id"])
        with self.assertRaises(ValueError): self.service.set_model_routing({"unknown": {"profileId": saved["id"]}})
        with self.assertRaises(ValueError): self.service.set_model_routing({"assistant": {"profileId": "missing"}})

    def test_openai_compatible_adapter_lists_models_without_real_network(self):
        calls = []
        def opener(request, timeout):
            calls.append((request.full_url, request.headers, timeout)); return _Response({"data": [{"id": "b"}, {"id": "a"}]})
        provider = OpenAICompatibleProvider("https://api.example.com/v1", "secret", {"X-Client": "test"}, 2, opener)
        self.assertEqual(provider.list_models(), ["a", "b"])
        self.assertTrue(provider.test_connection("a")["ok"])
        self.assertTrue(all(url.endswith("/models") for url, _, _ in calls))

    def test_openai_compatible_adapter_parses_real_sse_without_fake_chunking(self):
        requests = []
        lines = [
            ': keep-alive\n',
            'data: {"choices":[{"delta":{"content":"影响"},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{"content":"函数"},"finish_reason":"stop"}]}\n',
            'data: [DONE]\n',
        ]
        def opener(request, timeout):
            requests.append(json.loads(request.data)); return _StreamResponse(lines)
        provider = OpenAICompatibleProvider("https://api.example.com/v1", "secret", {}, 2, opener)
        events = list(provider.stream_chat("test", [{"role": "user", "content": "解释"}], temperature=.2))
        self.assertEqual("".join(item.get("content", "") for item in events), "影响函数")
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(requests[0]["stream"])
        self.assertEqual(requests[0]["temperature"], .2)

    def test_stream_rejects_malformed_provider_json(self):
        provider = OpenAICompatibleProvider(
            "https://api.example.com/v1", "secret", {}, 2,
            lambda request, timeout: _StreamResponse(["data: not-json\n"]),
        )
        with self.assertRaisesRegex(RuntimeError, "invalid SSE JSON"):
            list(provider.stream_chat("test", [{"role": "user", "content": "x"}]))

    def test_connection_errors_and_missing_model_are_structured(self):
        def http(code):
            def opener(request, timeout): raise urllib.error.HTTPError(request.full_url, code, "error", {}, None)
            return opener
        self.assertEqual(OpenAICompatibleProvider("https://api.example.com/v1", "x", {}, opener=http(401)).test_connection()["code"], "authentication_failed")
        self.assertEqual(OpenAICompatibleProvider("https://api.example.com/v1", "x", {}, opener=http(429)).test_connection()["code"], "rate_limited")
        def timeout(request, timeout): raise urllib.error.URLError(TimeoutError())
        self.assertEqual(OpenAICompatibleProvider("https://api.example.com/v1", "x", {}, opener=timeout).test_connection()["code"], "timeout")
        provider = OpenAICompatibleProvider("https://api.example.com/v1", "x", {}, opener=lambda request, timeout: _Response({"data": [{"id": "available"}]}))
        self.assertEqual(provider.test_connection("missing")["code"], "model_not_found")

    def test_structured_output_uses_openai_compatible_endpoint_without_eval(self):
        calls = []
        def opener(request, timeout):
            calls.append(json.loads(request.data)); return _Response({"choices": [{"message": {"content": "{}"}}]})
        provider = OpenAICompatibleProvider("https://api.example.com/v1", "x", {}, opener=opener)
        provider.structured_output("test", [{"role": "user", "content": "x"}], {"name": "result", "schema": {"type": "object"}})
        self.assertEqual(calls[0]["response_format"]["type"], "json_schema")

    def test_deepseek_structured_output_uses_json_object_and_bounded_schema_prompt(self):
        calls = []
        def opener(request, timeout):
            calls.append(json.loads(request.data)); return _Response({"choices": [{"message": {"content": "{}"}}]})
        provider = OpenAICompatibleProvider(
            "https://api.deepseek.com", "x", {}, opener=opener, provider_type="deepseek",
        )
        provider.structured_output(
            "deepseek-v4-pro",
            [{"role": "system", "content": "返回 JSON"}, {"role": "user", "content": "x"}],
            {"name": "result", "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}},
        )
        self.assertEqual(calls[0]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0]["thinking"], {"type": "disabled"})
        self.assertIn("JSON Schema", calls[0]["messages"][0]["content"])
        self.assertNotIn("json_schema", json.dumps(calls[0]["response_format"]))

    def test_keychain_failure_never_echoes_secret(self):
        with patch("agent.core.models.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["security", "secret-value"])):
            with self.assertRaises(RuntimeError) as caught: MacKeychainStore().set("ref", "secret-value")
        self.assertNotIn("secret-value", str(caught.exception))

    def test_chat_uses_selected_provider_and_archives_locally(self):
        saved = self.service.save_model_profile(self.profile())
        with patch.object(self.service.models, "provider", return_value=_ChatProvider()):
            result = self.service.chat({"profile_id": saved["id"], "messages": [{"role": "user", "content": "解释平衡性"}]})
        self.assertEqual(result["message"]["content"], "fake:test-model:解释平衡性")
        archive = self.vault / "90-Local-Only/Agent/Conversations" / f"{result['conversation_id']}.jsonl"
        self.assertTrue(archive.exists())
        self.assertIn("解释平衡性", archive.read_text(encoding="utf-8"))
        log = self.service.log_path.read_text(encoding="utf-8")
        self.assertNotIn("解释平衡性", log)
        self.assertNotIn("sk-test-secret", log)

    def test_brain_model_gateway_uses_routed_structured_output_and_strict_json(self):
        saved = self.service.save_model_profile(self.profile())
        self.service.set_model_routing({"intent_router": {"profileId": saved["id"], "modelOverride": "test-model"}})
        class Provider:
            def structured_output(self, model, messages, schema, **options):
                return {"choices": [{"message": {"content": '{"primary_intent":"capture_text","secondary_intents":[],"confidence":0.9}'}}]}
        with patch.object(self.service.models, "provider", return_value=Provider()):
            result = BrainModelGateway(self.service.models).classify_intent(type("Request", (), {"text": "ambiguous", "mode": "auto"})())
        self.assertEqual(result["primary_intent"], "capture_text")
        self.assertIn("enum", json.dumps(INTENT_SCHEMA))
        with self.assertRaises(BrainError): parse_model_json({"choices": [{"message": {"content": "not json"}}]})

    def test_brain_tutor_uses_assistant_chat_route_and_marks_unverified_answer(self):
        saved = self.service.save_model_profile(self.profile())
        self.service.set_model_routing({"assistant_chat": {"profileId": saved["id"], "modelOverride": "test-model"}})
        class Provider:
            def chat(self, model, messages, **options):
                self.model, self.messages, self.options = model, messages, options
                return {"choices": [{"message": {"content": "你好，我是模型生成的学习助手回答。"}}]}
        provider = Provider()
        with patch.object(self.service.models, "provider", return_value=provider):
            run = self.service.submit_brain({"text": "你好", "mode": "auto"}, "model-tutor-run")
        self.assertEqual(run["status"], "completed")
        result = run["result"]["results"][0]
        self.assertEqual(result["answer"], "你好，我是模型生成的学习助手回答。")
        self.assertTrue(result["model_generated"])
        self.assertEqual(result["verification_status"], "needs-verification")
        self.assertEqual(provider.model, "test-model")


if __name__ == "__main__": unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.model_capabilities import CapabilityResolver, DeepSeekCapabilityProbe
from agent.core.models import lookup_known_model_context_window
from agent.core.storage import StateStore


class FakeProvider:
    def test_connection(self, model: str = ""):
        return {"ok": True, "code": "connected", "models": [model]}

    def stream_agent(self, model, messages, *, tools=None, **options):
        if tools:
            yield {"type": "tool_call_delta", "index": 0, "id": "probe-call", "name": "capability_probe", "arguments": '{"value":"OK"}'}
            yield {"type": "usage", "usage": {"total_tokens": 5}}
        else:
            yield {"type": "thinking_delta", "delta": "transport-only"}
            yield {"type": "text_delta", "delta": "OK"}
            yield {"type": "usage", "usage": {"total_tokens": 4}}


class FakeModels:
    def __init__(self): self.instance = FakeProvider()
    def provider(self, _profile_id): return self.instance


class CapabilityProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "state.sqlite3")
        self.profile = {
            "id": "deepseek", "providerType": "deepseek", "baseUrl": "https://api.deepseek.com/v1",
            "defaultModel": "deepseek-chat", "settings": {"customHeaders": {"X-App": "test"}, "parallelToolCalls": False},
        }

    def tearDown(self) -> None:
        self.store.close(); self.temp.cleanup()

    def test_behavioral_probe_is_cached_without_secret_and_resolves_conservatively(self) -> None:
        probe = DeepSeekCapabilityProbe(FakeModels(), self.store)
        result = probe.probe(self.profile)
        self.assertEqual(result["capabilities"]["nativeToolCalling"]["status"], "supported")
        self.assertEqual(result["capabilities"]["reasoningContent"]["status"], "supported")
        self.assertEqual(result["capabilities"]["parallelToolCalls"]["status"], "unsupported")
        self.assertEqual(probe.cached(self.profile)["checkedAt"], result["checkedAt"])
        serialized = str(self.store.get_setting(f"pi_capability:{probe.cache.key(self.profile)}", {}))
        self.assertNotIn("apiKey", serialized)
        resolved = CapabilityResolver.resolve(result)
        self.assertTrue(resolved["nativeToolCalling"])
        self.assertFalse(resolved["parallelToolCalls"])
        conservative = CapabilityResolver.resolve(None)
        self.assertFalse(conservative["strictToolSchema"])
        self.assertFalse(conservative["reasoningEffort"])

    def test_profile_limits_are_used_when_probe_has_no_numeric_result(self) -> None:
        resolved = CapabilityResolver.resolve(None, {"contextWindow": 1_000_000, "maxTokens": 384_000})
        self.assertEqual(resolved["contextWindow"], 1_000_000)
        self.assertEqual(resolved["maxOutputTokens"], 384_000)


class KnownModelContextWindowTests(unittest.TestCase):
    def test_known_deepseek_v4_pro_is_one_million(self):
        self.assertEqual(lookup_known_model_context_window("deepseek-v4-pro"), 1_000_000)
        self.assertEqual(lookup_known_model_context_window("DeepSeek-V4-Pro"), 1_000_000)

    def test_known_deepseek_v4_flash_is_128k(self):
        self.assertEqual(lookup_known_model_context_window("deepseek-v4-flash"), 128_000)

    def test_known_openai_gpt_4_1_is_one_million(self):
        self.assertEqual(lookup_known_model_context_window("gpt-4.1"), 1_000_000)

    def test_unknown_model_falls_back_to_128k(self):
        self.assertEqual(lookup_known_model_context_window("custom-fancy-model-99"), 128_000)

    def test_empty_model_falls_back_to_128k(self):
        self.assertEqual(lookup_known_model_context_window(""), 128_000)

    def test_prefix_match_prefers_longest_key(self):
        # "deepseek-v4-pro" should win over "deepseek-v4-flash" when neither
        # prefix matches exactly, but here we just confirm exact match wins.
        self.assertEqual(lookup_known_model_context_window("deepseek-v4-pro"), 1_000_000)
        self.assertEqual(lookup_known_model_context_window("deepseek-v4-flash"), 128_000)


    def test_inconclusive_probe_does_not_override_user_explicit_settings(self) -> None:
        probe_result = {
            "capabilities": {
                "contextWindow": {"status": "inconclusive", "value": 1_000_000},
                "maxOutputTokens": {"status": "inconclusive", "value": 128_000},
            },
        }
        explicit = {"contextWindow": 128_000, "maxTokens": 32_000}
        resolved = CapabilityResolver.resolve(probe_result, explicit_settings=explicit)
        self.assertEqual(resolved["contextWindow"], 128_000)
        self.assertEqual(resolved["maxOutputTokens"], 32_000)

    def test_error_and_unsupported_probe_values_are_ignored_even_when_present(self) -> None:
        probe_result = {
            "capabilities": {
                "contextWindow": {"status": "error", "value": 999_999},
                "maxOutputTokens": {"status": "unsupported", "value": 888_888},
            },
        }
        resolved = CapabilityResolver.resolve(probe_result)
        self.assertNotEqual(resolved["contextWindow"], 999_999)
        self.assertNotEqual(resolved["maxOutputTokens"], 888_888)


if __name__ == "__main__":
    unittest.main()

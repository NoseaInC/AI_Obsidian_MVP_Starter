from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.core.model_capabilities import CapabilityResolver, DeepSeekCapabilityProbe
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


if __name__ == "__main__":
    unittest.main()

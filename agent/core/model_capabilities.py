from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Any

from agent.core.models import normalized_model_settings, lookup_known_model_context_window


ADAPTER_VERSION = "openai-compatible-pi-v2"
CAPABILITY_NAMES = (
    "basicStreaming", "nativeToolCalling", "streamedToolCalls", "parallelToolCalls",
    "strictToolSchema", "reasoningContent", "thinkingControl", "reasoningEffort",
    "toolChoiceAuto", "toolChoiceRequired", "usageReporting", "supportsJsonSchema",
    "preservesToolCallId", "supportsCancellation", "contextWindow", "maxOutputTokens",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CapabilityStore:
    def __init__(self, store: Any, ttl_days: int = 7) -> None:
        self.store = store
        self.ttl = timedelta(days=ttl_days)

    @staticmethod
    def key(profile: dict[str, Any]) -> str:
        safe_headers = sorted(str(key).casefold() for key in (profile.get("settings") or {}).get("customHeaders", {}))
        payload = {
            "profileId": profile.get("id"), "model": profile.get("defaultModel"),
            "baseUrl": str(profile.get("baseUrl") or "").rstrip("/").casefold(),
            "headerNames": safe_headers, "adapter": ADAPTER_VERSION,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def get(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        value = self.store.get_setting(f"pi_capability:{self.key(profile)}", None)
        if not isinstance(value, dict):
            return None
        try:
            checked = datetime.fromisoformat(str(value["checkedAt"]))
        except (KeyError, ValueError):
            return None
        if datetime.now().astimezone() - checked > self.ttl:
            return None
        return value

    def put(self, profile: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
        self.store.set_setting(f"pi_capability:{self.key(profile)}", value)
        return value


class DeepSeekCapabilityProbe:
    """Minimal non-sensitive behavioral probe for OpenAI-compatible models."""

    def __init__(self, models: Any, store: Any) -> None:
        self.models = models
        self.cache = CapabilityStore(store)

    @staticmethod
    def _state(status: str, *, code: str = "", value: Any = None, latency: int = 0) -> dict[str, Any]:
        result = {"status": status, "checkedAt": _now(), "latencyMs": latency}
        if code: result["errorCode"] = code
        if value is not None: result["value"] = value
        return result

    def cached(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        return self.cache.get(profile)

    def probe(self, profile: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        model = str(profile.get("defaultModel") or "")
        settings = normalized_model_settings(profile)
        capabilities = {name: self._state("inconclusive") for name in CAPABILITY_NAMES}
        provider = self.models.provider(str(profile["id"]))
        connection = provider.test_connection(model)
        if not connection.get("ok"):
            code = str(connection.get("code") or "connection_failed")
            capabilities = {name: self._state("error", code=code) for name in CAPABILITY_NAMES}
            return self.cache.put(profile, {"profileId": profile["id"], "model": model, "checkedAt": _now(), "adapterVersion": ADAPTER_VERSION, "capabilities": capabilities, "connection": connection})

        text_seen = usage_seen = reasoning_seen = False
        try:
            for event in provider.stream_agent(
                model,
                [{"role": "user", "content": "Reply with exactly: OK"}],
                tools=[], temperature=0, max_tokens=8,
            ):
                kind = str(event.get("type") or "")
                text_seen = text_seen or kind == "text_delta"
                usage_seen = usage_seen or kind == "usage"
                reasoning_seen = reasoning_seen or kind == "thinking_delta"
            capabilities["basicStreaming"] = self._state("supported" if text_seen else "inconclusive")
            capabilities["usageReporting"] = self._state("supported" if usage_seen else "unsupported")
            capabilities["reasoningContent"] = self._state("supported" if reasoning_seen else "unsupported")
        except Exception as error:
            capabilities["basicStreaming"] = self._state("error", code=type(error).__name__)

        call_ids: list[str] = []
        streamed_fragments = 0
        try:
            tools = [{"type": "function", "function": {"name": "capability_probe", "description": "Return probe value", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}, "strict": True}}]
            for event in provider.stream_agent(
                model,
                [{"role": "user", "content": "Call capability_probe once with value OK."}],
                tools=tools, temperature=0, max_tokens=32, tool_choice="required",
            ):
                if str(event.get("type") or "") == "tool_call_delta":
                    streamed_fragments += 1
                    if event.get("id"): call_ids.append(str(event["id"]))
            supported = bool(call_ids or streamed_fragments)
            for name in ("nativeToolCalling", "toolChoiceRequired", "strictToolSchema", "supportsJsonSchema"):
                capabilities[name] = self._state("supported" if supported else "unsupported")
            capabilities["streamedToolCalls"] = self._state("supported" if streamed_fragments else "unsupported")
            capabilities["preservesToolCallId"] = self._state("supported" if call_ids else "unsupported")
            capabilities["toolChoiceAuto"] = self._state("supported" if supported else "inconclusive")
        except Exception as error:
            code = type(error).__name__
            for name in ("nativeToolCalling", "streamedToolCalls", "toolChoiceRequired", "strictToolSchema", "supportsJsonSchema", "preservesToolCallId"):
                capabilities[name] = self._state("error", code=code)

        capabilities["parallelToolCalls"] = self._state("supported" if settings.get("parallelToolCalls") and capabilities["nativeToolCalling"]["status"] == "supported" else "unsupported")
        capabilities["reasoningEffort"] = self._state("inconclusive")
        capabilities["thinkingControl"] = self._state("inconclusive")
        capabilities["supportsCancellation"] = self._state("inconclusive")
        capabilities["contextWindow"] = self._state("inconclusive", value=int(settings.get("contextWindow") or lookup_known_model_context_window(model)))
        capabilities["maxOutputTokens"] = self._state("inconclusive", value=int(settings.get("maxTokens") or 3_000))
        latency = int((time.monotonic() - started) * 1000)
        for value in capabilities.values(): value["latencyMs"] = latency
        result = {"profileId": profile["id"], "model": model, "checkedAt": _now(), "adapterVersion": ADAPTER_VERSION, "capabilities": capabilities, "connection": connection}
        return self.cache.put(profile, result)


class CapabilityResolver:
    @staticmethod
    def resolve(
        probe: dict[str, Any] | None,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = (probe or {}).get("capabilities") or {}
        configured = settings or {}
        supported = lambda name: (values.get(name) or {}).get("status") == "supported"
        def numeric(name: str, fallback: int, configured_name: str | None = None) -> int:
            return int(
                (values.get(name) or {}).get("value")
                or configured.get(configured_name or name)
                or fallback
            )
        return {
            "basicStreaming": supported("basicStreaming"),
            "nativeToolCalling": supported("nativeToolCalling"),
            "streamedToolCalls": supported("streamedToolCalls"),
            "parallelToolCalls": supported("parallelToolCalls"),
            "strictToolSchema": supported("strictToolSchema"),
            "reasoningContent": supported("reasoningContent"),
            "reasoningEffort": supported("reasoningEffort"),
            "toolChoiceRequired": supported("toolChoiceRequired"),
            "usageReporting": supported("usageReporting"),
            "contextWindow": numeric("contextWindow", lookup_known_model_context_window(str((probe or {}).get("model") or ""))),
            "maxOutputTokens": numeric("maxOutputTokens", 3_000, "maxTokens"),
        }

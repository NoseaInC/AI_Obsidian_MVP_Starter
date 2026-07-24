from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Iterator, Protocol
from urllib.parse import urlparse


PROVIDER_TYPES = {"deepseek", "openai", "openai-compatible", "custom"}
PROTECTED_HEADERS = {"authorization", "host", "content-length"}
ROUTING_TASKS = {
    # Pi is the only model/tool loop. The other routes are explicit,
    # single-purpose workflows and never classify free-form user prose.
    "agent_runtime", "material_analysis",
    "research_synthesis", "capture_organize", "curriculum_planner", "daily_knowledge_generator",
    "claim_extractor", "claim_verifier", "lesson_generator",
    "tutor", "quiz", "evaluation", "pdf_prepare", "assistant_chat",
    # Product task aliases retained for existing settings.
    "assistant", "prepare", "review", "weekly-plan", "fast",
}
KEY_REFERENCE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


# Vendor-published context windows for known OpenAI-compatible models.
# Used as a fallback so users do not have to manually configure the limit
# for popular chat models. Matching is case-insensitive and prefers the
# longest key that matches as a prefix (so "deepseek-v4-pro" beats
# "deepseek-v4").
KNOWN_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # DeepSeek family. DeepSeek-V4 Pro ships with a 1M-token window.
    "deepseek-v4-pro": 1_000_000,
    "deepseek-v4-flash": 128_000,
    "deepseek-v4-lite": 64_000,
    "deepseek-v3.2-exp": 128_000,
    "deepseek-v3.2": 128_000,
    "deepseek-v3.1": 128_000,
    "deepseek-v3": 64_000,
    "deepseek-coder-v2": 128_000,
    "deepseek-coder": 128_000,
    # OpenAI family.
    "gpt-4.1": 1_000_000,
    "gpt-4.1-mini": 1_000_000,
    "gpt-4.1-nano": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o1": 200_000,
    "o1-mini": 128_000,
    # Anthropic via OpenAI-compatible proxies.
    "claude-3-7-sonnet": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    # Google Gemini.
    "gemini-2.5-pro": 1_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-1.5-pro": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
}
# Generic fallback when no vendor-specific match is found.
DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW = 128_000


def lookup_known_model_context_window(model_name: str) -> int:
    """Return the vendor-published window for ``model_name`` if known.

    Matching is case-insensitive. The longest key that matches as a prefix
    wins, so vendor versioning can introduce newer entries without breaking
    older lookups.
    """
    if not model_name:
        return DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW
    normalized = model_name.strip().lower()
    best_key = ""
    best_value = DEFAULT_UNKNOWN_MODEL_CONTEXT_WINDOW
    for key, value in KNOWN_MODEL_CONTEXT_WINDOWS.items():
        if normalized.startswith(key) and len(key) > len(best_key):
            best_key = key
            best_value = value
    return best_value


def normalized_model_settings(profile: dict[str, Any]) -> dict[str, Any]:
    """Return capability defaults without rewriting an existing profile.

    Early profiles predate the reasoning/tool capability fields.  Treating a
    missing field as an explicit ``False`` silently disables features after an
    upgrade, so reads use provider-aware defaults while preserving every
    explicit user choice.
    """
    provider_type = str(profile.get("providerType") or "openai-compatible")
    default_model_name = str(profile.get("defaultModel") or "")
    settings = dict(profile.get("settings") or {})
    native_tool_calling = bool(
        settings.get("nativeToolCalling", settings.get("toolCalling", True))
    )
    defaults: dict[str, Any] = {
        "temperature": 0.3,
        "maxTokens": 3000,
        "contextWindow": lookup_known_model_context_window(default_model_name),
        "timeout": 30,
        "streaming": True,
        "jsonSchema": True,
        "toolCalling": native_tool_calling,
        "nativeToolCalling": native_tool_calling,
        "streamedToolCalls": native_tool_calling,
        "reasoningContent": provider_type == "deepseek",
        "thinkingControl": "provider-default",
        "parallelToolCalls": False,
        "reasoningEffort": "",
        "organizationId": "",
        "customHeaders": {},
    }
    return {**defaults, **settings}


class KeyStore(Protocol):
    def set(self, reference: str, secret: str) -> None: ...
    def get(self, reference: str) -> str | None: ...
    def delete(self, reference: str) -> None: ...
    def configured(self, reference: str) -> bool: ...


class FakeKeyStore:
    def __init__(self) -> None: self.values: dict[str, str] = {}
    def set(self, reference: str, secret: str) -> None: self.values[reference] = secret
    def get(self, reference: str) -> str | None: return self.values.get(reference)
    def delete(self, reference: str) -> None: self.values.pop(reference, None)
    def configured(self, reference: str) -> bool: return bool(self.values.get(reference))


class MacKeychainStore:
    """Fixed-argv Keychain adapter. No shell and no secret-bearing logs."""
    service = "com.zhixu.obsidian.model-provider"

    def set(self, reference: str, secret: str) -> None:
        if not secret: raise ValueError("API key must not be empty")
        try:
            subprocess.run(
                ["/usr/bin/security", "add-generic-password", "-U", "-a", reference, "-s", self.service, "-w", secret],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            # CalledProcessError stringifies argv, which contains the secret.
            raise RuntimeError("Unable to save API key in macOS Keychain") from None

    def get(self, reference: str) -> str | None:
        env_name = reference.removeprefix("env:") if reference.startswith("env:") else ""
        if env_name: return os.environ.get(env_name)
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", reference, "-s", self.service, "-w"],
            check=False, capture_output=True, text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def delete(self, reference: str) -> None:
        if reference.startswith("env:"): return
        subprocess.run(
            ["/usr/bin/security", "delete-generic-password", "-a", reference, "-s", self.service],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def configured(self, reference: str) -> bool:
        if reference.startswith("env:"):
            return bool(os.environ.get(reference.removeprefix("env:")))
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", reference, "-s", self.service],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL must be an absolute service URL without credentials, query or fragment")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Base URL must use HTTPS; HTTP is allowed only for localhost")
    return value


def validate_headers(headers: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).strip()
        if not name or name.lower() in PROTECTED_HEADERS: raise ValueError(f"Protected or invalid header: {name}")
        if any(char in name + str(raw_value) for char in "\r\n"): raise ValueError("Header names and values cannot contain newlines")
        result[name] = str(raw_value)
    return result


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    api_key: str
    headers: dict[str, str]
    timeout: float = 30.0
    opener: Any = urllib.request.urlopen
    provider_type: str = "openai-compatible"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", method=method, data=data,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.headers},
        )
        with self.opener(request, timeout=self.timeout) as response: return json.loads(response.read())

    def list_models(self) -> list[str]:
        payload = self._request("GET", "/models")
        return sorted(str(item["id"]) for item in payload.get("data", []) if item.get("id"))

    def test_connection(self, model: str = "") -> dict[str, Any]:
        try:
            models = self.list_models()
            if model and models and model not in models:
                return {"ok": False, "code": "model_not_found", "message": "连接成功，但未找到所选模型", "models": models}
            return {"ok": True, "code": "connected", "message": "连接成功", "models": models}
        except urllib.error.HTTPError as exc:
            code = "authentication_failed" if exc.code in {401, 403} else "rate_limited" if exc.code == 429 else "provider_error"
            return {"ok": False, "code": code, "message": {"authentication_failed": "认证失败", "rate_limited": "请求受到速率限制"}.get(code, f"服务返回 HTTP {exc.code}")}
        except (TimeoutError, urllib.error.URLError) as exc:
            timeout = isinstance(getattr(exc, "reason", None), TimeoutError)
            return {"ok": False, "code": "timeout" if timeout else "unreachable", "message": "连接超时" if timeout else "无法连接服务地址"}
        except (ValueError, json.JSONDecodeError, KeyError):
            return {"ok": False, "code": "incompatible", "message": "服务响应不兼容 OpenAI API"}

    def chat(self, model: str, messages: list[dict[str, str]], **options: Any) -> dict[str, Any]:
        return self._request("POST", "/chat/completions", {"model": model, "messages": messages, **options})

    def stream_chat(self, model: str, messages: list[dict[str, str]], **options: Any) -> Iterator[dict[str, Any]]:
        """Yield normalized events from a real OpenAI-compatible SSE stream.

        The caller owns the iterator lifetime. Closing it also closes the HTTP
        response, which lets an aborted Obsidian request stop upstream work.
        No completed response is sliced into fake chunks here.
        """
        payload = {"model": model, "messages": messages, **options, "stream": True}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions", method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.headers},
        )
        with self.opener(request, timeout=self.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                data = line.removeprefix("data:").strip() if line.startswith("data:") else line
                if data == "[DONE]":
                    yield {"type": "done"}
                    return
                try:
                    item = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Model stream returned invalid SSE JSON") from exc
                choice = (item.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"type": "delta", "content": content}
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    yield {"type": "finish", "finishReason": str(finish_reason), "usage": item.get("usage") or {}}

    def stream_agent(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        **options: Any,
    ) -> Iterator[dict[str, Any]]:
        """Stream the provider protocol needed by the TypeScript Pi runtime.

        This deliberately stays below the Agent Loop: it performs one model
        request, preserves streamed tool-call argument fragments, and never
        executes a tool.  API keys remain inside this Python process.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **options,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **self.headers,
            },
        )
        with self.opener(request, timeout=self.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                data = line.removeprefix("data:").strip() if line.startswith("data:") else line
                if data == "[DONE]":
                    yield {"type": "done"}
                    return
                try:
                    item = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Model stream returned invalid SSE JSON") from exc
                usage = item.get("usage")
                if isinstance(usage, dict):
                    yield {"type": "usage", "usage": usage}
                choice = (item.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield {"type": "text_delta", "delta": content}
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    # Forwarded only for provider protocol continuity. The Pi
                    # adapter discards it from user-visible and persisted text.
                    yield {"type": "thinking_delta", "delta": reasoning}
                for raw_call in delta.get("tool_calls") or []:
                    function = raw_call.get("function") or {}
                    yield {
                        "type": "tool_call_delta",
                        "index": int(raw_call.get("index", 0)),
                        "id": str(raw_call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments_delta": str(function.get("arguments") or ""),
                    }
                finish_reason = choice.get("finish_reason")
                if finish_reason:
                    yield {
                        "type": "finish",
                        "finish_reason": str(finish_reason),
                        "usage": usage or {},
                    }

    def structured_output(self, model: str, messages: list[dict[str, str]], schema: dict[str, Any], **options: Any) -> dict[str, Any]:
        if self.provider_type == "deepseek":
            # DeepSeek's OpenAI-compatible Chat Completion API currently
            # accepts json_object, not OpenAI's json_schema response type.
            # Preserve the same local validation contract by placing the
            # requested schema in the bounded prompt and validating the
            # returned object in BrainModelGateway.
            schema_text = json.dumps(schema.get("schema", schema), ensure_ascii=False, separators=(",", ":"))
            instruction = f"只输出 JSON 对象，不要使用 Markdown。输出必须符合以下 JSON Schema：{schema_text}"
            adapted = [dict(item) for item in messages]
            if adapted and adapted[0].get("role") == "system":
                adapted[0]["content"] = f"{adapted[0].get('content', '')}\n{instruction}"
            else:
                adapted.insert(0, {"role": "system", "content": instruction})
            return self.chat(
                model,
                adapted,
                response_format={"type": "json_object"},
                thinking={"type": "disabled"},
                **options,
            )
        return self.chat(model, messages, response_format={"type": "json_schema", "json_schema": schema}, **options)


class ModelProfileService:
    def __init__(self, store: Any, key_store: KeyStore | None = None) -> None:
        self.store, self.key_store = store, key_store or MacKeychainStore()

    def _public(self, profile: dict[str, Any]) -> dict[str, Any]:
        configured = self.key_store.configured(profile["apiKeyReference"])
        return {
            **profile,
            "settings": normalized_model_settings(profile),
            "configured": configured,
            "keyHint": "••••••••" if configured else "未配置",
        }

    def list(self) -> list[dict[str, Any]]: return [self._public(item) for item in self.store.list_model_profiles()]

    def save(self, data: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
        provider_type = str(data.get("providerType", "openai-compatible"))
        if provider_type not in PROVIDER_TYPES: raise ValueError("Unsupported provider type")
        profile_id = profile_id or uuid.uuid4().hex
        existing = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        requested_reference = str(data.get("apiKeyReference", "")).strip()
        reference = requested_reference or (existing["apiKeyReference"] if existing else f"zhixu:{profile_id}")
        if not KEY_REFERENCE.fullmatch(reference):
            raise ValueError("API Key Reference may contain only letters, numbers, dot, underscore, colon and hyphen")
        settings = dict(data.get("settings", {})); settings["customHeaders"] = validate_headers(dict(settings.get("customHeaders", {})))
        native_tool_calling = bool(
            settings.get("nativeToolCalling", settings.get("toolCalling", True))
        )
        profile = {
            "id": profile_id, "displayName": str(data.get("displayName", "未命名配置")).strip() or "未命名配置",
            "providerType": provider_type, "baseUrl": validate_base_url(str(data.get("baseUrl", ""))),
            "apiKeyReference": reference, "defaultModel": str(data.get("defaultModel", "")).strip(),
            "availableModels": [str(item) for item in data.get("availableModels", [])], "enabled": bool(data.get("enabled", True)),
            "settings": {"temperature": float(settings.get("temperature", .3)), "maxTokens": int(settings.get("maxTokens", 2000)),
                         "contextWindow": int(settings.get("contextWindow", lookup_known_model_context_window(str(data.get("defaultModel", ""))))),
                         "timeout": float(settings.get("timeout", 30)), "streaming": bool(settings.get("streaming", True)),
                         "jsonSchema": bool(settings.get("jsonSchema", True)), "toolCalling": native_tool_calling,
                         "nativeToolCalling": native_tool_calling,
                         "streamedToolCalls": bool(settings.get("streamedToolCalls", native_tool_calling)),
                         "reasoningContent": bool(settings.get("reasoningContent", provider_type == "deepseek")),
                         "thinkingControl": str(settings.get("thinkingControl", "provider-default")),
                         "parallelToolCalls": bool(settings.get("parallelToolCalls", False)),
                         "reasoningEffort": str(settings.get("reasoningEffort", "")),
                         "organizationId": str(settings.get("organizationId", "")), "customHeaders": settings["customHeaders"]},
        }
        api_key = str(data.get("apiKey", ""))
        if api_key: self.key_store.set(reference, api_key)
        self.store.upsert_model_profile(profile)
        return self._public(next(item for item in self.store.list_model_profiles() if item["id"] == profile_id))

    def delete(self, profile_id: str) -> None:
        profile = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        if not profile: raise RuntimeError("Model profile not found")
        # A Profile may point at a pre-existing or shared Keychain item such as
        # `deepseek-main`. Removing UI configuration must never destroy that
        # external secret implicitly.
        self.store.delete_model_profile(profile_id)

    def provider(self, profile_id: str) -> OpenAICompatibleProvider:
        profile = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        if not profile: raise RuntimeError("Model profile not found")
        key = self.key_store.get(profile["apiKeyReference"])
        if not key: raise RuntimeError("API key is not configured")
        settings = profile["settings"]
        return OpenAICompatibleProvider(
            profile["baseUrl"], key, settings.get("customHeaders", {}),
            float(settings.get("timeout", 30)), provider_type=profile["providerType"],
        )

    def test(self, profile_id: str) -> dict[str, Any]:
        profile = next((item for item in self.store.list_model_profiles() if item["id"] == profile_id), None)
        return self.provider(profile_id).test_connection(profile["defaultModel"] if profile else "")

    def models(self, profile_id: str) -> list[str]: return self.provider(profile_id).list_models()

    def routing(self) -> dict[str, dict[str, str | None]]: return self.store.model_routing()

    def set_routing(self, routes: dict[str, dict[str, Any]]) -> dict[str, dict[str, str | None]]:
        if not set(routes).issubset(ROUTING_TASKS): raise ValueError("Unsupported model routing task")
        known = {item["id"] for item in self.store.list_model_profiles()}
        for route in routes.values():
            if route.get("profileId") and route["profileId"] not in known: raise ValueError("Unknown model profile in routing")
        self.store.set_model_routing(routes); return self.routing()

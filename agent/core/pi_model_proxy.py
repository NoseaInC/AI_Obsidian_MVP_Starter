from __future__ import annotations

import json
from typing import Any, Iterator

from agent.core.models import ModelProfileService, normalized_model_settings
from agent.core.model_capabilities import CapabilityResolver, DeepSeekCapabilityProbe


MODEL_STREAM_SCHEMA = 1
MAX_MESSAGES = 200
MAX_TEXT_CHARS = 160_000
MAX_TOOLS = 96


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in value
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _provider_messages(raw_messages: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_messages, list) or len(raw_messages) > MAX_MESSAGES:
        raise ValueError("model_messages_invalid")
    messages: list[dict[str, Any]] = []
    pending_tool_calls: dict[str, str] = {}
    for raw in raw_messages:
        if not isinstance(raw, dict):
            raise ValueError("model_message_invalid")
        role = str(raw.get("role") or "")
        if role == "user":
            content = _text_content(raw.get("content"))
            if len(content) > MAX_TEXT_CHARS:
                raise ValueError("model_message_too_large")
            messages.append({"role": "user", "content": content})
            continue
        if role == "assistant":
            content_blocks = raw.get("content") or []
            text = _text_content(content_blocks)
            tool_calls: list[dict[str, Any]] = []
            for block in content_blocks if isinstance(content_blocks, list) else []:
                if not isinstance(block, dict) or block.get("type") != "toolCall":
                    continue
                call_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                if not call_id or not name:
                    continue
                arguments = block.get("arguments")
                tool_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments if isinstance(arguments, dict) else {},
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                )
                pending_tool_calls[call_id] = name
            message: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue
        if role == "toolResult":
            call_id = str(raw.get("toolCallId") or "")
            if not call_id:
                raise ValueError("tool_result_id_required")
            content = _text_content(raw.get("content"))
            if len(content) > MAX_TEXT_CHARS:
                raise ValueError("tool_result_too_large")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": str(raw.get("toolName") or pending_tool_calls.get(call_id) or "tool"),
                    "content": content,
                }
            )
            continue
        raise ValueError("unsupported_model_message_role")
    return messages


def _provider_tools(raw_tools: Any) -> list[dict[str, Any]]:
    if raw_tools in (None, []):
        return []
    if not isinstance(raw_tools, list) or len(raw_tools) > MAX_TOOLS:
        raise ValueError("model_tools_invalid")
    tools: list[dict[str, Any]] = []
    for raw in raw_tools:
        if not isinstance(raw, dict):
            raise ValueError("model_tool_invalid")
        name = str(raw.get("name") or "")
        description = str(raw.get("description") or "")
        parameters = raw.get("parameters")
        if not name or not isinstance(parameters, dict):
            raise ValueError("model_tool_contract_invalid")
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description[:4_000],
                    "parameters": parameters,
                },
            }
        )
    return tools


class PiModelProxy:
    """One-request secure model adapter for the TypeScript Pi Agent Loop."""

    version = "pi-model-proxy-v1"

    def __init__(self, models: ModelProfileService, store: Any) -> None:
        self.models = models
        self.store = store
        self.capability_probe = DeepSeekCapabilityProbe(models, store)

    def _profile(self, profile_id: str) -> dict[str, Any]:
        profile = next(
            (item for item in self.store.list_model_profiles() if item["id"] == profile_id),
            None,
        )
        if not profile or not profile.get("enabled", True):
            raise RuntimeError("model_profile_unavailable")
        return profile

    def capabilities(self, profile_id: str = "") -> dict[str, Any]:
        profiles = self.models.list()
        selected = next((item for item in profiles if item["id"] == profile_id), None)
        result = {
            "schemaVersion": MODEL_STREAM_SCHEMA,
            "runtime": self.version,
            "profiles": [
                {
                    "id": item["id"],
                    "provider": item["providerType"],
                    "model": item["defaultModel"],
                    "configured": item["configured"],
                    "capabilities": CapabilityResolver.resolve(
                        self.capability_probe.cached(item),
                        normalized_model_settings(item),
                    ),
                    "probe": self.capability_probe.cached(item),
                }
                for item in profiles
            ],
            "selected": selected["id"] if selected else "",
        }
        return result

    def probe(self, profile_id: str) -> dict[str, Any]:
        profile = self._profile(profile_id)
        return self.capability_probe.probe(profile)

    @staticmethod
    def _provider_events(
        provider: Any,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """Keep provider failures inside the model-stream protocol.

        A model request is already an HTTP 200 NDJSON response by the time the
        upstream provider is opened.  Letting an exception escape here used to
        make the generic server writer emit a legacy ``run.failed`` event that
        the Pi transport could not understand, leaving the UI waiting forever.
        """
        try:
            yield from provider.stream_agent(model, messages, tools=tools, **options)
        except Exception as exc:
            status = getattr(exc, "code", None)
            code = "model_provider_rejected" if isinstance(status, int) else "model_provider_unavailable"
            message = (
                f"模型请求被供应商拒绝（HTTP {status}）"
                if isinstance(status, int)
                else "模型流连接失败；请检查模型配置或网络后重试"
            )
            yield {"type": "proxy_error", "code": code, "message": message}

    def stream(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Keep every startup failure inside the versioned model protocol.

        The HTTP response is already an NDJSON stream when this generator is
        first advanced.  A validation/profile failure before the first
        ``start`` event must therefore become a normal terminal ``error``
        event; emitting the server's legacy ``run.failed`` shape leaves the
        TypeScript transport without a terminal model event.
        """
        try:
            yield from self._stream(body)
        except Exception as exc:
            code = str(exc) or type(exc).__name__
            messages = {
                "assistant_model_profile_required": "尚未选择助手模型；请先选择一个已配置的模型",
                "model_profile_unavailable": "所选模型配置不可用；请重新选择模型",
                "assistant_model_required": "模型配置缺少模型名称",
                "model_messages_invalid": "当前会话历史过长；系统需要先压缩上下文",
                "model_message_too_large": "当前会话中有一条消息过长；请压缩上下文后重试",
                "tool_result_too_large": "工具结果过长；请缩小读取范围后重试",
            }
            yield {
                "schemaVersion": MODEL_STREAM_SCHEMA,
                "type": "error",
                "code": code if code in messages else "model_stream_invalid",
                "message": messages.get(code, "模型请求未能启动；请检查模型配置或上下文后重试"),
            }

    def _stream(self, body: dict[str, Any]) -> Iterator[dict[str, Any]]:
        profile_id = str(body.get("profileId") or body.get("profile_id") or "")
        if not profile_id:
            routes = self.models.routing()
            route = routes.get("assistant_chat") or routes.get("assistant") or {}
            profile_id = str(route.get("profileId") or "")
        if not profile_id:
            enabled = [item for item in self.models.list() if item.get("enabled", True) and item.get("configured")]
            if len(enabled) == 1:
                profile_id = str(enabled[0].get("id") or "")
        if not profile_id:
            raise RuntimeError("assistant_model_profile_required")
        profile = self._profile(profile_id)
        context = body.get("context")
        if not isinstance(context, dict):
            raise ValueError("model_context_required")
        system_prompt = str(context.get("systemPrompt") or "")
        if len(system_prompt) > MAX_TEXT_CHARS:
            raise ValueError("system_prompt_too_large")
        messages = _provider_messages(context.get("messages"))
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})
        tools = _provider_tools(context.get("tools"))
        settings = normalized_model_settings(profile)
        cached_probe = self.capability_probe.cached(profile)
        resolved = CapabilityResolver.resolve(cached_probe, settings)
        requested = body.get("options") if isinstance(body.get("options"), dict) else {}
        options: dict[str, Any] = {
            "temperature": float(requested.get("temperature", settings.get("temperature", 0.3))),
        }
        # The previous 32K clamp could cut a streamed function-call JSON in
        # half, producing "Unexpected end of JSON input" after long write
        # plans.  The model profile/capability probe is now the single source
        # of truth. A non-positive configured value means provider default.
        configured_output = int(settings.get("maxTokens") or 0)
        resolved_output = int(resolved.get("maxOutputTokens") or configured_output or 0)
        requested_output = int(requested.get("maxTokens") or configured_output or resolved_output or 0)
        output_limits = [value for value in (requested_output, configured_output, resolved_output) if value > 0]
        if output_limits:
            options["max_tokens"] = min(output_limits)
        if tools:
            options["tool_choice"] = "auto"
            options["parallel_tool_calls"] = bool(resolved.get("parallelToolCalls", False))
        reasoning = str(requested.get("reasoning") or "")
        if reasoning and resolved.get("reasoningEffort"):
            options["reasoning_effort"] = reasoning
        provider = self.models.provider(profile_id)
        requested_model = str(body.get("model") or "").strip()
        # ``configured-assistant-model`` is the provider-neutral placeholder
        # used while Pi is being constructed.  It must never cross the secure
        # proxy boundary as a real provider model id.
        if requested_model in {"configured-assistant-model", "unknown"}:
            requested_model = ""
        model = requested_model or str(profile.get("defaultModel") or "").strip()
        if not model:
            raise RuntimeError("assistant_model_required")

        yield {"schemaVersion": MODEL_STREAM_SCHEMA, "type": "start", "model": model}
        text_started = False
        thinking_started = False
        calls: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        for event in self._provider_events(provider, model, messages, tools, options):
            kind = str(event.get("type") or "")
            if kind == "text_delta":
                if not text_started:
                    text_started = True
                    yield {"schemaVersion": MODEL_STREAM_SCHEMA, "type": "text_start"}
                yield {
                    "schemaVersion": MODEL_STREAM_SCHEMA,
                    "type": "text_delta",
                    "delta": str(event.get("delta") or ""),
                }
            elif kind == "thinking_delta":
                if not thinking_started:
                    thinking_started = True
                    yield {"schemaVersion": MODEL_STREAM_SCHEMA, "type": "thinking_start"}
                # This is a transport block only. The plugin does not render or
                # persist it as normal assistant content.
                yield {
                    "schemaVersion": MODEL_STREAM_SCHEMA,
                    "type": "thinking_delta",
                    "delta": str(event.get("delta") or ""),
                }
            elif kind == "tool_call_delta":
                index = int(event.get("index", 0))
                state = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                call_id = str(event.get("id") or "")
                name = str(event.get("name") or "")
                if call_id:
                    state["id"] = call_id
                if name:
                    state["name"] = name
                if not state.get("started") and state["id"] and state["name"]:
                    state["started"] = "1"
                    yield {
                        "schemaVersion": MODEL_STREAM_SCHEMA,
                        "type": "tool_call_start",
                        "index": index,
                        "id": state["id"],
                        "name": state["name"],
                    }
                delta = str(event.get("arguments_delta") or "")
                state["arguments"] += delta
                if delta:
                    yield {
                        "schemaVersion": MODEL_STREAM_SCHEMA,
                        "type": "tool_call_delta",
                        "index": index,
                        "delta": delta,
                    }
            elif kind == "usage":
                usage = dict(event.get("usage") or {})
            elif kind == "finish":
                finish_reason = str(event.get("finish_reason") or "stop")
                usage = dict(event.get("usage") or usage)
            elif kind == "proxy_error":
                yield {
                    "schemaVersion": MODEL_STREAM_SCHEMA,
                    "type": "error",
                    "code": str(event.get("code") or "model_provider_unavailable"),
                    "message": str(event.get("message") or "模型流连接失败"),
                }
                return

        if text_started:
            yield {"schemaVersion": MODEL_STREAM_SCHEMA, "type": "text_end"}
        if thinking_started:
            yield {"schemaVersion": MODEL_STREAM_SCHEMA, "type": "thinking_end"}
        # Never pass a half-serialized function call to the plugin. Providers
        # commonly end with finish_reason=length when the configured output
        # budget is exhausted. JSON.parse would otherwise surface only the
        # misleading "Unexpected end of JSON input" and the entire run would
        # be reported as pi_runtime_failed.
        if calls and finish_reason == "length":
            yield {
                "schemaVersion": MODEL_STREAM_SCHEMA,
                "type": "error",
                "code": "model_tool_arguments_truncated",
                "message": "模型输出达到当前配置上限，工具参数未完整返回；请缩小单次工具调用或使用批量 Vault 工具",
            }
            return
        for index in sorted(calls):
            state = calls[index]
            if not state.get("started"):
                raise RuntimeError("incomplete_streamed_tool_call")
            try:
                arguments = json.loads(state["arguments"] or "{}")
            except json.JSONDecodeError:
                yield {
                    "schemaVersion": MODEL_STREAM_SCHEMA,
                    "type": "error",
                    "code": "model_tool_arguments_invalid",
                    "message": "供应商返回了不完整的工具参数；请重试或改用批量 Vault 工具",
                }
                return
            if not isinstance(arguments, dict):
                yield {
                    "schemaVersion": MODEL_STREAM_SCHEMA,
                    "type": "error",
                    "code": "model_tool_arguments_invalid",
                    "message": "供应商返回的工具参数不是 JSON 对象",
                }
                return
            yield {
                "schemaVersion": MODEL_STREAM_SCHEMA,
                "type": "tool_call_end",
                "index": index,
                "id": state["id"],
                "name": state["name"],
                "arguments": state["arguments"],
            }
        yield {
            "schemaVersion": MODEL_STREAM_SCHEMA,
            "type": "usage",
            "usage": usage,
        }
        yield {
            "schemaVersion": MODEL_STREAM_SCHEMA,
            "type": "done",
            "finishReason": "toolUse" if finish_reason in {"tool_calls", "function_call"} else "length" if finish_reason == "length" else "stop",
        }

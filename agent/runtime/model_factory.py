from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider


def build_pydantic_model(
    model_profile_service,
    profile_id: str,
    model_name: str,
):
    """Reuse the project's existing Keychain-backed profile resolution."""
    provider = model_profile_service.provider(profile_id)
    profile_record = next(
        item for item in model_profile_service.store.list_model_profiles()
        if item["id"] == profile_id
    )
    settings = dict(profile_record.get("settings") or {})
    client = AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        default_headers=provider.headers or None,
        timeout=provider.timeout,
        max_retries=2,
    )
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(openai_client=client),
        profile=OpenAIModelProfile(
            openai_chat_thinking_field=(
                "reasoning_content"
                if settings.get("reasoningContent") is True
                else None
            ),
            # Hidden reasoning may be used by the provider for subsequent tool
            # turns, but is never emitted as UI text or persisted to Markdown.
            openai_chat_send_back_thinking_parts="field"
            if settings.get("reasoningContent") is True
            else False,
            openai_supports_strict_tool_definition=bool(
                settings.get("jsonSchema", True)
            ),
        ),
        settings={
            "temperature": float(settings.get("temperature", 0.3)),
            "max_tokens": int(settings.get("maxTokens", 3000)),
            "parallel_tool_calls": bool(
                settings.get("parallelToolCalls", False)
            ),
            **(
                {"openai_reasoning_effort": settings["reasoningEffort"]}
                if settings.get("reasoningEffort") in {
                    "none", "minimal", "low", "medium", "high", "xhigh"
                }
                else {}
            ),
        },
    )

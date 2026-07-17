from __future__ import annotations

from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def build_pydantic_model(
    model_profile_service,
    profile_id: str,
    model_name: str,
):
    """Reuse the project's existing Keychain-backed profile resolution."""
    provider = model_profile_service.provider(profile_id)
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
    )

from __future__ import annotations

from ..config import Settings
from .base import CompletionRequest, LLMProvider, Message, ProviderError

__all__ = ["CompletionRequest", "LLMProvider", "Message", "ProviderError", "build_provider"]


def build_provider(settings: Settings) -> LLMProvider:
    """Create the provider selected by LLM_PROVIDER.

    Imports are local so that, for example, mock mode never imports the
    Anthropic SDK.
    """
    if settings.llm_provider == "mock":
        from .mock import MockProvider

        return MockProvider(settings.mock_responses_dir)
    if settings.llm_provider == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(
            api_key=settings.anthropic_api_key,
            model=settings.claude_model,
            timeout_seconds=min(settings.llm_timeout_seconds, 300.0),
        )
    from .ollama import OllamaProvider

    return OllamaProvider(
        base_url=settings.ollama_base_url,
        text_model=settings.ollama_text_model,
        vision_model=settings.ollama_vision_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

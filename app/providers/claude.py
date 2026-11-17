"""Claude provider, via the official Anthropic SDK.

Not verified against the live API (no key on the build machine). The unit test
drives it through the SDK with a stubbed HTTP transport.
"""

from __future__ import annotations

import base64
from typing import Any

import anthropic

from .base import CompletionRequest, LLMProvider, ProviderError


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 120.0,
        max_tokens: int = 4096,
        http_client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("LLM_PROVIDER=claude requires ANTHROPIC_API_KEY to be set")
        self._model = model
        self._max_tokens = max_tokens
        # The SDK retries 408/429/5xx and connection errors with exponential backoff.
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=2, http_client=http_client
        )

    def model_for(self, mode: str) -> str:
        return self._model  # one multimodal model handles both modes

    def complete(self, request: CompletionRequest) -> str:
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                continue
            blocks: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    },
                }
                for image in message.images
            ]
            blocks.append({"type": "text", "text": message.content})
            messages.append({"role": message.role, "content": blocks})
        try:
            response = self._client.messages.create(
                model=self._model, max_tokens=self._max_tokens, system=system, messages=messages
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"Claude API error: {exc}") from exc
        return "".join(block.text for block in response.content if block.type == "text")

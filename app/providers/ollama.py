"""Ollama provider (default). Talks to the local /api/chat endpoint."""

from __future__ import annotations

import base64
import logging
import time

import httpx

from .base import CompletionRequest, LLMProvider, ProviderError

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        text_model: str,
        vision_model: str,
        timeout_seconds: float = 900.0,
        max_transport_retries: int = 2,
        backoff_seconds: float = 2.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._text_model = text_model
        self._vision_model = vision_model
        self._max_transport_retries = max_transport_retries
        self._backoff_seconds = backoff_seconds
        # Connecting is fast or broken; generating on a CPU can take minutes.
        timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout)

    def model_for(self, mode: str) -> str:
        return self._vision_model if mode == "vision" else self._text_model

    def complete(self, request: CompletionRequest) -> str:
        payload = {
            "model": self.model_for(request.mode),
            "stream": False,
            "format": "json",  # constrain output to syntactically valid JSON
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    **(
                        {"images": [base64.b64encode(i).decode("ascii") for i in m.images]}
                        if m.images
                        else {}
                    ),
                }
                for m in request.messages
            ],
        }
        last_error: Exception | None = None
        for attempt in range(self._max_transport_retries + 1):
            if attempt:
                time.sleep(self._backoff_seconds * 2 ** (attempt - 1))
            try:
                response = self._client.post("/api/chat", json=payload)
            except httpx.TimeoutException as exc:
                # Not retried: a second multi-minute wait rarely helps and blocks the caller.
                raise ProviderError(f"Ollama timed out: {exc!r}") from exc
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("ollama transport error (try %d): %r", attempt + 1, exc)
                continue
            if response.status_code >= 500:
                last_error = ProviderError(f"Ollama HTTP {response.status_code}")
                logger.warning("ollama HTTP %d (try %d)", response.status_code, attempt + 1)
                continue
            if response.status_code != 200:
                raise ProviderError(
                    f"Ollama HTTP {response.status_code}: {response.text[:300]}"
                )
            try:
                return response.json()["message"]["content"]
            except (ValueError, KeyError, TypeError) as exc:
                raise ProviderError("Unexpected response shape from Ollama") from exc
        raise ProviderError(f"Ollama unreachable after retries: {last_error!r}")

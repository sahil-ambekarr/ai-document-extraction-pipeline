"""Provider-neutral request types. Each provider translates these to its own API."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    """The LLM backend could not produce a response (network, auth, missing recording...)."""


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[bytes] = field(default_factory=list)  # PNG bytes, user messages only


@dataclass
class CompletionRequest:
    messages: list[Message]
    mode: str  # "text" | "vision"
    doc_hash: str  # sha256 of the uploaded file; the mock provider keys on it
    attempt: int  # 1-based attempt number within the validation retry loop


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def model_for(self, mode: str) -> str:
        """Model identifier used for the given extraction mode."""

    @abstractmethod
    def complete(self, request: CompletionRequest) -> str:
        """Return the raw model output (expected to be a JSON object as text)."""

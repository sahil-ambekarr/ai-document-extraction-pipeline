"""Mock provider: replays stored responses keyed by the document's sha256.

Each file `<sha256>.json` holds a list of responses, one per attempt, so a
recording can include a deliberately invalid first answer followed by a valid
one. That lets tests and CI exercise the retry loop with no model at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import CompletionRequest, LLMProvider, ProviderError


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, responses_dir: Path) -> None:
        self._dir = Path(responses_dir)

    def model_for(self, mode: str) -> str:
        return "recorded"

    def complete(self, request: CompletionRequest) -> str:
        path = self._dir / f"{request.doc_hash}.json"
        if not path.is_file():
            raise ProviderError(
                "Mock provider has no recorded response for this document "
                f"(sha256 {request.doc_hash[:12]}...). Use a file from data/samples/ "
                "or switch LLM_PROVIDER to ollama or claude."
            )
        responses: list[str] = json.loads(path.read_text(encoding="utf-8"))["responses"]
        if not responses:
            raise ProviderError(f"Recording {path.name} contains no responses")
        # Past the end of the recording, keep replaying the last answer.
        return responses[min(request.attempt, len(responses)) - 1]

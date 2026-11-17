"""Mock provider: replays stored responses keyed by the document's sha256.

A file `<sha256>.json` holds the raw model outputs, one per attempt, so a
recording can contain an invalid first answer followed by a corrected one.
That lets tests and CI exercise the retry loop with no model at all.

    {"source": "...", "by_mode": {"text": ["{...}", "{...}"], "vision": ["{...}"]}}

`scripts/eval.py --record` writes these files from real model runs. A plain
`"responses": [...]` list is also accepted and used for any mode.
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
        recording = json.loads(path.read_text(encoding="utf-8"))
        responses = recording.get("by_mode", {}).get(request.mode) or recording.get("responses")
        if not responses:
            raise ProviderError(
                f"Recording {path.name} has no responses for {request.mode} mode"
            )
        # Past the end of the recording, keep replaying the last answer.
        return responses[min(request.attempt, len(responses)) - 1]

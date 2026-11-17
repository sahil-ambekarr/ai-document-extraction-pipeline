"""The extraction loop: prompt -> model -> parse -> validate -> (feedback -> retry)."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError

from . import prompts
from .providers import CompletionRequest, LLMProvider, Message
from .rendering import RenderedDocument
from .schemas import Invoice, PurchaseOrder, format_validation_errors, validate_document

logger = logging.getLogger(__name__)

DOC_TYPES = ("invoice", "purchase_order")
# Cap on how much of a rejected answer is echoed back, to protect the context window.
MAX_ECHO_CHARS = 6000


class ExtractionInputError(ValueError):
    """The request cannot be run as asked (e.g. text mode on a scanned image)."""


@dataclass
class AttemptRecord:
    attempt: int
    valid: bool
    errors: list[str]
    latency_ms: int
    raw_output: str


@dataclass
class ExtractionResult:
    valid: bool
    provider: str
    model: str
    mode: str
    data: dict[str, Any] | None  # validated document, or the last parseable JSON if invalid
    errors: list[str]  # errors of the final attempt; empty when valid
    attempts: list[AttemptRecord] = field(default_factory=list)
    latency_ms: int = 0

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def retry_count(self) -> int:
        return max(0, len(self.attempts) - 1)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["attempt_count"] = self.attempt_count
        payload["retry_count"] = self.retry_count
        return payload


def resolve_mode(requested: str, rendered: RenderedDocument) -> str:
    """Map auto/text/vision onto what this document can actually support."""
    if requested == "auto":
        return "text" if rendered.has_text_layer else "vision"
    if requested == "text" and not rendered.has_text_layer:
        raise ExtractionInputError(
            "Text mode needs a PDF with a text layer; this file has none. Use vision mode."
        )
    if requested not in ("text", "vision"):
        raise ExtractionInputError(f"Unknown mode {requested!r}")
    return requested


def parse_json_object(raw: str) -> dict[str, Any]:
    """Pull a JSON object out of model output, tolerating code fences and stray prose."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("output does not contain a JSON object")
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"output is not valid JSON ({exc.msg} at char {exc.pos})") from exc
    if not isinstance(parsed, dict):
        raise ValueError("output JSON must be an object")
    return parsed


def _initial_messages(rendered: RenderedDocument, mode: str, doc_type: str | None) -> list[Message]:
    if mode == "text":
        user = Message("user", prompts.TEXT_USER_TEMPLATE.format(text=rendered.text))
    else:
        user = Message("user", prompts.VISION_USER_PROMPT, images=list(rendered.pages))
    if doc_type:
        user.content += prompts.TYPE_HINT_TEMPLATE.format(doc_type=doc_type)
    return [Message("system", prompts.SYSTEM_PROMPT), user]


def _validate_output(
    raw: str, doc_type: str | None
) -> tuple[Invoice | PurchaseOrder | None, dict[str, Any] | None, list[str]]:
    """Return (validated document, parsed payload, errors)."""
    try:
        payload = parse_json_object(raw)
    except ValueError as exc:
        return None, None, [f"document: {exc}"]
    if doc_type:
        payload["document_type"] = doc_type  # the caller knows better than the model
    try:
        return validate_document(payload), payload, []
    except ValidationError as exc:
        return None, payload, format_validation_errors(exc)


def run_extraction(
    rendered: RenderedDocument,
    provider: LLMProvider,
    mode: str = "auto",
    max_attempts: int = 3,
    doc_type: str | None = None,
) -> ExtractionResult:
    """Run the validate-and-retry loop. Provider failures propagate as ProviderError."""
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise ExtractionInputError(f"doc_type must be one of {DOC_TYPES}")
    mode = resolve_mode(mode, rendered)
    messages = _initial_messages(rendered, mode, doc_type)
    result = ExtractionResult(
        valid=False, provider=provider.name, model=provider.model_for(mode), mode=mode,
        data=None, errors=[],
    )
    started = time.perf_counter()

    for attempt in range(1, max(1, max_attempts) + 1):
        attempt_started = time.perf_counter()
        raw = provider.complete(
            CompletionRequest(messages=messages, mode=mode, doc_hash=rendered.sha256,
                              attempt=attempt)
        )
        latency_ms = int((time.perf_counter() - attempt_started) * 1000)
        document, payload, errors = _validate_output(raw, doc_type)
        result.attempts.append(
            AttemptRecord(attempt=attempt, valid=document is not None, errors=errors,
                          latency_ms=latency_ms, raw_output=raw)
        )
        logger.info(
            "extraction attempt",
            extra={"doc": rendered.sha256[:12], "attempt": attempt, "mode": mode,
                   "valid": document is not None, "error_count": len(errors),
                   "latency_ms": latency_ms},
        )
        if document is not None:
            result.valid = True
            result.data = document.model_dump(mode="json")
            result.errors = []
            break
        result.errors = errors
        if payload is not None:
            result.data = payload
        # Show the model its own answer plus the exact validation errors.
        messages.append(Message("assistant", raw[:MAX_ECHO_CHARS]))
        messages.append(Message("user", prompts.build_retry_message(errors)))

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result

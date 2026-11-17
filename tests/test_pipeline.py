"""The validate-and-retry loop, driven by a scripted fake provider."""

from __future__ import annotations

import json

import pytest

from app.pipeline import ExtractionInputError, parse_json_object, resolve_mode, run_extraction
from app.providers import CompletionRequest, LLMProvider, ProviderError
from app.rendering import render_document
from tests.conftest import SAMPLES


class ScriptedProvider(LLMProvider):
    """Returns the given outputs in order and keeps every request for inspection."""

    name = "fake"

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[CompletionRequest] = []

    def model_for(self, mode: str) -> str:
        return f"fake-{mode}"

    def complete(self, request: CompletionRequest) -> str:
        # Copy the list: the pipeline appends to it after this call returns.
        self.requests.append(
            CompletionRequest(list(request.messages), request.mode, request.doc_hash,
                              request.attempt)
        )
        return self.outputs[len(self.requests) - 1]


class FailingProvider(ScriptedProvider):
    def complete(self, request: CompletionRequest) -> str:
        raise ProviderError("backend down")


@pytest.fixture(scope="module")
def pdf():
    return render_document((SAMPLES / "inv-001-classic.pdf").read_bytes(), max_side=600)


@pytest.fixture(scope="module")
def scan():
    return render_document((SAMPLES / "inv-009-classic-scan.png").read_bytes(), max_side=600)


def test_invalid_then_valid_retries_once_and_feeds_errors_back(pdf, invoice_label,
                                                                broken_invoice):
    provider = ScriptedProvider([json.dumps(broken_invoice), json.dumps(invoice_label)])
    result = run_extraction(pdf, provider, mode="text", max_attempts=3)

    assert result.valid
    assert result.retry_count == 1 and result.attempt_count == 2
    assert result.data["total"] == invoice_label["total"]
    assert result.attempts[0].valid is False and result.attempts[0].errors
    # The second request must include the rejected answer and the exact errors.
    second = provider.requests[1].messages
    assert second[-2].role == "assistant" and json.loads(second[-2].content) == broken_invoice
    assert second[-1].role == "user"
    assert "subtotal + tax" in second[-1].content
    assert provider.requests[1].attempt == 2


def test_unparseable_output_counts_as_a_failed_attempt(pdf, invoice_label):
    provider = ScriptedProvider(["Sure! Here is the data.", json.dumps(invoice_label)])
    result = run_extraction(pdf, provider, mode="text", max_attempts=3)
    assert result.valid and result.retry_count == 1
    assert "JSON object" in result.attempts[0].errors[0]


def test_gives_up_after_max_attempts_and_keeps_last_payload(pdf, broken_invoice):
    provider = ScriptedProvider([json.dumps(broken_invoice)] * 5)
    result = run_extraction(pdf, provider, mode="text", max_attempts=3)
    assert not result.valid
    assert result.attempt_count == 3 and len(provider.requests) == 3
    assert result.errors and "but total is" in result.errors[0]
    assert result.data == broken_invoice  # last parseable answer is kept for review


def test_valid_first_answer_makes_one_call(pdf, invoice_label):
    provider = ScriptedProvider([json.dumps(invoice_label)])
    result = run_extraction(pdf, provider, mode="auto", max_attempts=3)
    assert result.valid and result.retry_count == 0 and len(provider.requests) == 1
    assert result.mode == "text" and result.model == "fake-text"


def test_text_request_carries_text_layer_and_vision_carries_images(pdf, invoice_label):
    text_provider = ScriptedProvider([json.dumps(invoice_label)])
    run_extraction(pdf, text_provider, mode="text")
    user = text_provider.requests[0].messages[1]
    assert invoice_label["number"] in user.content and not user.images

    vision_provider = ScriptedProvider([json.dumps(invoice_label)])
    run_extraction(pdf, vision_provider, mode="vision")
    user = vision_provider.requests[0].messages[1]
    assert user.images and user.images[0].startswith(b"\x89PNG")


def test_doc_type_hint_overrides_model_answer(pdf, invoice_label):
    answer = dict(invoice_label, document_type="purchase_order")
    provider = ScriptedProvider([json.dumps(answer)])
    result = run_extraction(pdf, provider, mode="text", doc_type="invoice")
    assert result.valid and result.data["document_type"] == "invoice"
    assert 'known to be of type "invoice"' in provider.requests[0].messages[1].content


def test_provider_errors_propagate(pdf):
    with pytest.raises(ProviderError):
        run_extraction(pdf, FailingProvider([]), mode="text")


def test_mode_resolution(pdf, scan):
    assert resolve_mode("auto", pdf) == "text"
    assert resolve_mode("auto", scan) == "vision"
    assert resolve_mode("vision", pdf) == "vision"
    with pytest.raises(ExtractionInputError):
        resolve_mode("text", scan)


def test_bad_doc_type_is_rejected(pdf):
    with pytest.raises(ExtractionInputError):
        run_extraction(pdf, ScriptedProvider([]), doc_type="receipt")


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope that helps'],
)
def test_parse_json_object_tolerates_wrapping(raw):
    assert parse_json_object(raw) == {"a": 1}


@pytest.mark.parametrize("raw", ["", "no json here", '{"a": }', "[1, 2]"])
def test_parse_json_object_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_json_object(raw)

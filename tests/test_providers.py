"""Provider adapters with stubbed HTTP layers. No network, no model, no API key."""

from __future__ import annotations

import base64
import json

import httpx
import httpx2
import pytest

from app.providers import CompletionRequest, Message, ProviderError
from app.providers.claude import ClaudeProvider
from app.providers.mock import MockProvider
from app.providers.ollama import OllamaProvider

PNG = b"\x89PNG\r\n\x1a\nfake"


def _request(mode="vision", attempt=1, doc_hash="abc"):
    return CompletionRequest(
        messages=[Message("system", "be precise"), Message("user", "extract", images=[PNG])],
        mode=mode, doc_hash=doc_hash, attempt=attempt,
    )


# ------------------------------------------------------------------ Ollama


def _ollama(handler, **kwargs):
    client = httpx.Client(base_url="http://ollama", transport=httpx.MockTransport(handler))
    return OllamaProvider("http://ollama", "llama3.2", "qwen2.5vl:3b", client=client,
                          backoff_seconds=0, **kwargs)


def test_ollama_sends_json_format_model_and_images():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}})

    assert _ollama(handler).complete(_request()) == '{"ok": true}'
    assert seen["model"] == "qwen2.5vl:3b" and seen["format"] == "json"
    assert seen["options"]["temperature"] == 0
    assert seen["messages"][1]["images"] == [base64.b64encode(PNG).decode()]
    assert "images" not in seen["messages"][0]


def test_ollama_uses_text_model_in_text_mode():
    models = []

    def handler(request):
        models.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"message": {"content": "{}"}})

    _ollama(handler).complete(_request(mode="text"))
    assert models == ["llama3.2"]


def test_ollama_retries_server_errors_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"message": {"content": "{}"}})

    assert _ollama(handler).complete(_request()) == "{}"
    assert len(calls) == 3


def test_ollama_gives_up_after_transport_retries():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError, match="unreachable"):
        _ollama(handler, max_transport_retries=1).complete(_request())


def test_ollama_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404, json={"error": "model not found"})

    with pytest.raises(ProviderError, match="404"):
        _ollama(handler).complete(_request())
    assert len(calls) == 1


def test_ollama_timeout_becomes_provider_error():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError, match="timed out"):
        _ollama(handler).complete(_request())


# ------------------------------------------------------------------ Claude


def _claude(handler):
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return ClaudeProvider(api_key="test-key-not-real", model="claude-haiku-5-5",
                          http_client=client)


def test_claude_requires_api_key():
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        ClaudeProvider(api_key="", model="claude-haiku-5-5")


def test_claude_builds_vision_request_and_reads_text():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": '{"number": "1"}'}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    assert _claude(handler).complete(_request()) == '{"number": "1"}'
    assert seen["model"] == "claude-haiku-5-5" and seen["system"] == "be precise"
    blocks = seen["messages"][0]["content"]
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["data"] == base64.b64encode(PNG).decode()
    assert blocks[-1] == {"type": "text", "text": "extract"}


def test_claude_api_errors_become_provider_errors():
    def handler(request):
        return httpx2.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "bad"}})

    with pytest.raises(ProviderError, match="Claude API error"):
        _claude(handler).complete(_request())


# ------------------------------------------------------------------ Mock


def test_mock_replays_per_mode_and_repeats_last(tmp_path):
    (tmp_path / "abc.json").write_text(json.dumps(
        {"by_mode": {"text": ["first", "second"], "vision": ["v"]}}))
    mock = MockProvider(tmp_path)
    assert mock.complete(_request(mode="text", attempt=1)) == "first"
    assert mock.complete(_request(mode="text", attempt=2)) == "second"
    assert mock.complete(_request(mode="text", attempt=3)) == "second"
    assert mock.complete(_request(mode="vision")) == "v"


def test_mock_errors_when_recording_is_missing(tmp_path):
    with pytest.raises(ProviderError, match="no recorded response"):
        MockProvider(tmp_path).complete(_request(doc_hash="nope"))
    (tmp_path / "abc.json").write_text(json.dumps({"by_mode": {"text": ["x"]}}))
    with pytest.raises(ProviderError, match="vision"):
        MockProvider(tmp_path).complete(_request(mode="vision"))

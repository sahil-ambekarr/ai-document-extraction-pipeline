"""HTTP API in mock mode. The mock recordings are written per test, so these
tests do not depend on which real-model recordings are committed."""

from __future__ import annotations

import hashlib
import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import create_app
from tests.conftest import SAMPLES, make_settings

PDF = SAMPLES / "inv-001-classic.pdf"
SCAN = SAMPLES / "inv-009-classic-scan.png"


def _record(settings, path, outputs, mode="text"):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = settings.mock_responses_dir / f"{digest}.json"
    recording = json.loads(target.read_text()) if target.exists() else {"by_mode": {}}
    recording["by_mode"][mode] = [json.dumps(o) if isinstance(o, dict) else o for o in outputs]
    target.write_text(json.dumps(recording))


@pytest.fixture
def setup(tmp_path):
    settings = make_settings(tmp_path)
    client = TestClient(create_app(settings))
    return client, settings


def _upload(client, path, **form):
    with path.open("rb") as handle:
        return client.post("/extract", files={"file": (path.name, handle)}, data=form)


def test_health_reports_mock_provider(setup):
    client, _ = setup
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["provider"] == "mock"
    assert body["database"] == "sqlite" and body["max_attempts"] == 3


def test_extract_with_retry_then_browse_and_export(setup, invoice_label, broken_invoice):
    client, settings = setup
    _record(settings, PDF, [broken_invoice, invoice_label])

    response = _upload(client, PDF)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["valid"] and doc["retry_count"] == 1 and doc["mode"] == "text"
    assert doc["attempts"][0]["valid"] is False and doc["attempts"][0]["errors"]
    assert doc["data"]["number"] == invoice_label["number"]
    assert doc["page_urls"] == [f"/documents/{doc['id']}/pages/1"]

    page = client.get(doc["page_urls"][0])
    assert page.status_code == 200 and page.content.startswith(b"\x89PNG")

    listing = client.get("/documents").json()
    assert [d["id"] for d in listing] == [doc["id"]]
    assert listing[0]["total"] == invoice_label["total"]

    detail = client.get(f"/documents/{doc['id']}").json()
    assert detail["data"] == doc["data"] and detail["page_urls"] == doc["page_urls"]

    export = client.get("/export.xlsx")
    assert export.status_code == 200
    assert "spreadsheetml" in export.headers["content-type"]
    workbook = load_workbook(io.BytesIO(export.content))
    assert workbook["Documents"].max_row == 2
    assert client.get(f"/export.xlsx?document_id={doc['id']}").status_code == 200


def test_invalid_after_all_attempts_is_stored_as_invalid(setup, broken_invoice):
    client, settings = setup
    _record(settings, PDF, [broken_invoice])
    doc = _upload(client, PDF).json()
    assert not doc["valid"] and doc["attempt_count"] == 3
    assert any("but total is" in e for e in doc["errors"])
    assert client.get("/documents").json()[0]["number"] is None


def test_mode_and_type_can_be_set_per_request(setup, invoice_label):
    client, settings = setup
    _record(settings, PDF, [invoice_label], mode="vision")
    doc = _upload(client, PDF, mode="vision", doc_type="invoice").json()
    assert doc["mode"] == "vision" and doc["valid"]


def test_scanned_image_uses_vision_in_auto_mode(setup, invoice_label):
    client, settings = setup
    _record(settings, SCAN, [invoice_label], mode="vision")
    doc = _upload(client, SCAN).json()
    assert doc["mode"] == "vision" and doc["kind"] == "image"


def test_text_mode_on_scan_is_422(setup):
    client, _ = setup
    response = _upload(client, SCAN, mode="text")
    assert response.status_code == 422 and "text layer" in response.json()["detail"]


def test_unknown_mode_is_422(setup):
    client, _ = setup
    assert _upload(client, PDF, mode="telepathy").status_code == 422


def test_unsupported_file_is_415(setup):
    client, _ = setup
    response = client.post("/extract", files={"file": ("x.pdf", b"not really a pdf")})
    assert response.status_code == 415


def test_oversized_file_is_413(tmp_path):
    settings = make_settings(tmp_path, max_upload_mb=1)
    client = TestClient(create_app(settings))
    big = b"%PDF-" + b"0" * (1024 * 1024 + 10)
    assert client.post("/extract", files={"file": ("big.pdf", big)}).status_code == 413


def test_missing_recording_is_502(setup):
    client, _ = setup
    response = _upload(client, PDF)
    assert response.status_code == 502 and "no recorded response" in response.json()["detail"]
    assert client.get("/documents").json() == []


def test_not_found_paths(setup):
    client, _ = setup
    assert client.get("/documents/999").status_code == 404
    assert client.get("/documents/999/pages/1").status_code == 404
    assert client.get("/export.xlsx?document_id=999").status_code == 404


def test_ui_and_openapi_are_served(setup):
    client, _ = setup
    assert "Document Extraction" in client.get("/").text
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/extract", "/documents", "/documents/{document_id}", "/export.xlsx", "/health"):
        assert path in paths

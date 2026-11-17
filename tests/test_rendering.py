from __future__ import annotations

import io

import pytest
from PIL import Image

from app.rendering import UnsupportedDocumentError, render_document, sniff_kind
from tests.conftest import SAMPLES


def test_pdf_renders_pages_and_text_layer():
    rendered = render_document((SAMPLES / "inv-001-classic.pdf").read_bytes(), max_side=500)
    assert rendered.kind == "pdf" and rendered.page_count == 1 and len(rendered.pages) == 1
    assert rendered.has_text_layer and "INVOICE" in rendered.text
    image = Image.open(io.BytesIO(rendered.pages[0]))
    assert max(image.size) == 500


def test_multipage_pdf_respects_max_pages():
    data = (SAMPLES / "inv-005-banded-multipage.pdf").read_bytes()
    full = render_document(data, max_pages=4, max_side=300)
    assert full.page_count == 2 and len(full.pages) == 2 and not full.truncated
    assert "--- page 2 ---" in full.text
    capped = render_document(data, max_pages=1, max_side=300)
    assert len(capped.pages) == 1 and capped.truncated


def test_png_scan_has_no_text_layer():
    rendered = render_document((SAMPLES / "po-017-banded-scan.png").read_bytes(), max_side=400)
    assert rendered.kind == "image" and rendered.page_count == 1
    assert not rendered.has_text_layer


def test_hash_is_stable():
    data = (SAMPLES / "inv-001-classic.pdf").read_bytes()
    assert render_document(data).sha256 == render_document(data).sha256


@pytest.mark.parametrize("data", [b"", b"hello world", b"GIF89a....", b"%PDF-1.4 truncated"])
def test_bad_uploads_raise_a_clear_error(data):
    with pytest.raises(UnsupportedDocumentError):
        render_document(data)


def test_sniff_ignores_extension_and_uses_magic_bytes():
    assert sniff_kind(b"%PDF-1.7 ...") == "pdf"
    assert sniff_kind(b"\xff\xd8\xff\xe0 jpeg") == "image"

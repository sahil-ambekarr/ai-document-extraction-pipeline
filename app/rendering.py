"""Turn an uploaded PDF or image into page images plus (for PDFs) the text layer."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import pypdfium2 as pdfium
from PIL import Image

# Below this many characters a "text layer" is treated as absent (scanned PDF).
MIN_TEXT_LAYER_CHARS = 40
PDF_RENDER_SCALE = 2.0  # 144 DPI before downscaling to max_side


class UnsupportedDocumentError(ValueError):
    """The upload is not a PDF, PNG or JPEG, or cannot be decoded."""


@dataclass
class RenderedDocument:
    sha256: str
    kind: str  # "pdf" or "image"
    page_count: int  # pages in the source file
    pages: list[bytes] = field(default_factory=list)  # PNG bytes, at most max_pages
    text: str = ""  # PDF text layer, empty for images and scanned PDFs

    @property
    def has_text_layer(self) -> bool:
        return len(self.text.strip()) >= MIN_TEXT_LAYER_CHARS

    @property
    def truncated(self) -> bool:
        return self.page_count > len(self.pages)


def sniff_kind(data: bytes) -> str:
    """Identify the file by magic bytes; the filename and content type are not trusted."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        return "image"
    raise UnsupportedDocumentError("Only PDF, PNG and JPEG files are supported")


def _to_png(image: Image.Image, max_side: int) -> bytes:
    image = image.convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_document(data: bytes, max_pages: int = 4, max_side: int = 1280) -> RenderedDocument:
    if not data:
        raise UnsupportedDocumentError("The uploaded file is empty")
    kind = sniff_kind(data)
    sha256 = hashlib.sha256(data).hexdigest()

    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as image:
                page = _to_png(image, max_side)
        except Exception as exc:  # Pillow raises many types for corrupt files
            raise UnsupportedDocumentError(f"Could not decode image: {exc}") from exc
        return RenderedDocument(sha256=sha256, kind=kind, page_count=1, pages=[page])

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise UnsupportedDocumentError(f"Could not open PDF: {exc}") from exc
    try:
        page_count = len(pdf)
        if page_count == 0:
            raise UnsupportedDocumentError("The PDF has no pages")
        pages: list[bytes] = []
        texts: list[str] = []
        for index in range(min(page_count, max_pages)):
            page = pdf[index]
            pages.append(_to_png(page.render(scale=PDF_RENDER_SCALE).to_pil(), max_side))
            textpage = page.get_textpage()
            page_text = textpage.get_text_range().replace("\r\n", "\n").strip()
            textpage.close()
            page.close()
            if page_text:
                texts.append(f"--- page {index + 1} ---\n{page_text}")
    finally:
        pdf.close()
    return RenderedDocument(
        sha256=sha256, kind=kind, page_count=page_count, pages=pages, text="\n\n".join(texts)
    )

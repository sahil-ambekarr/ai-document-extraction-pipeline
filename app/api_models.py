"""Response models for the HTTP API. They also drive the OpenAPI docs at /docs."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel

from .storage import DocumentRecord


class AttemptOut(BaseModel):
    attempt: int
    valid: bool
    errors: list[str]
    latency_ms: int


class DocumentSummary(BaseModel):
    id: int
    created_at: dt.datetime
    filename: str
    kind: str
    page_count: int
    provider: str
    model: str
    mode: str
    valid: bool
    attempt_count: int
    retry_count: int
    latency_ms: int
    document_type: str | None
    number: str | None
    vendor_name: str | None
    total: float | None
    currency: str | None


class DocumentDetail(DocumentSummary):
    sha256: str
    data: dict[str, Any] | None
    errors: list[str]
    attempts: list[AttemptOut]
    page_urls: list[str]


class HealthOut(BaseModel):
    status: str
    provider: str
    extraction_mode: str
    max_attempts: int
    models: dict[str, str]
    database: str
    provider_reachable: bool | None
    detail: str | None = None


def _summary_fields(record: DocumentRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "filename": record.filename,
        "kind": record.kind,
        "page_count": record.page_count,
        "provider": record.provider,
        "model": record.model,
        "mode": record.mode,
        "valid": record.valid,
        "attempt_count": record.attempt_count,
        "retry_count": max(0, record.attempt_count - 1),
        "latency_ms": record.latency_ms,
        "document_type": record.document_type,
        "number": record.number,
        "vendor_name": record.vendor_name,
        "total": record.total,
        "currency": record.currency,
    }


def to_summary(record: DocumentRecord) -> DocumentSummary:
    return DocumentSummary(**_summary_fields(record))


def to_detail(record: DocumentRecord, rendered_pages: int) -> DocumentDetail:
    return DocumentDetail(
        **_summary_fields(record),
        sha256=record.sha256,
        data=record.data,
        errors=record.errors,
        attempts=[AttemptOut(**a) for a in record.attempts],
        page_urls=[f"/documents/{record.id}/pages/{n}" for n in range(1, rendered_pages + 1)],
    )

"""SQL persistence with SQLAlchemy. SQLite by default; any SQLAlchemy URL works in principle."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, select
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
)

from .pipeline import ExtractionResult
from .rendering import RenderedDocument


class Base(DeclarativeBase):
    pass


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Header columns are filled only for documents that passed validation, so
    # SQL consumers can trust them. The raw last answer is always in `data`.
    document_type: Mapped[str | None] = mapped_column(String(32))
    number: Mapped[str | None] = mapped_column(String(128))
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    buyer_name: Mapped[str | None] = mapped_column(String(255))
    issue_date: Mapped[dt.date | None] = mapped_column(Date)
    secondary_date: Mapped[dt.date | None] = mapped_column(Date)  # due or delivery date
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[float | None] = mapped_column(Float)
    tax: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)

    data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    errors: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)

    line_items: Mapped[list[LineItemRecord]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="LineItemRecord.line_no"
    )


class LineItemRecord(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    document: Mapped[DocumentRecord] = relationship(back_populates="line_items")


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        path = database_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # FastAPI runs sync endpoints in a thread pool, so the connection crosses threads.
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine


def _date(value: Any) -> dt.date | None:
    return dt.date.fromisoformat(value) if isinstance(value, str) else None


def save_extraction(
    session: Session, filename: str, rendered: RenderedDocument, result: ExtractionResult
) -> DocumentRecord:
    record = DocumentRecord(
        created_at=dt.datetime.now(dt.UTC).replace(tzinfo=None),
        filename=filename[:255],
        sha256=rendered.sha256,
        kind=rendered.kind,
        page_count=rendered.page_count,
        provider=result.provider,
        model=result.model,
        mode=result.mode,
        valid=result.valid,
        attempt_count=result.attempt_count,
        latency_ms=result.latency_ms,
        data=result.data,
        errors=result.errors,
        attempts=[
            {"attempt": a.attempt, "valid": a.valid, "errors": a.errors,
             "latency_ms": a.latency_ms}
            for a in result.attempts
        ],
    )
    if result.valid and result.data:
        data = result.data
        record.document_type = data["document_type"]
        record.number = data["number"][:128]
        record.vendor_name = data["vendor_name"][:255]
        record.buyer_name = data["buyer_name"][:255]
        record.issue_date = _date(data["issue_date"])
        record.secondary_date = _date(data.get("due_date") or data.get("delivery_date"))
        record.currency = data["currency"]
        record.subtotal = data["subtotal"]
        record.tax = data["tax"]
        record.total = data["total"]
        record.line_items = [
            LineItemRecord(
                line_no=index,
                description=item["description"][:500],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                amount=item["amount"],
            )
            for index, item in enumerate(data["line_items"], start=1)
        ]
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def list_documents(session: Session, limit: int = 50, offset: int = 0) -> list[DocumentRecord]:
    statement = (
        select(DocumentRecord).order_by(DocumentRecord.id.desc()).limit(limit).offset(offset)
    )
    return list(session.scalars(statement))


def get_document(session: Session, document_id: int) -> DocumentRecord | None:
    return session.get(DocumentRecord, document_id)


def documents_for_export(session: Session, document_id: int | None = None) -> list[DocumentRecord]:
    statement = (
        select(DocumentRecord)
        .options(selectinload(DocumentRecord.line_items))
        .order_by(DocumentRecord.id)
    )
    if document_id is not None:
        statement = statement.where(DocumentRecord.id == document_id)
    return list(session.scalars(statement))

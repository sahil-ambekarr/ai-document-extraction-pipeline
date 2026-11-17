"""SQL storage and Excel export, using a temporary SQLite database."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.exporters import DOCUMENT_COLUMNS, LINE_ITEM_COLUMNS, build_workbook
from app.pipeline import AttemptRecord, ExtractionResult
from app.rendering import render_document
from app.storage import (
    LineItemRecord,
    documents_for_export,
    list_documents,
    make_engine,
    save_extraction,
)
from tests.conftest import SAMPLES


@pytest.fixture
def session(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'x.db').as_posix()}")
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture(scope="module")
def rendered():
    return render_document((SAMPLES / "inv-001-classic.pdf").read_bytes(), max_side=300)


def _result(valid, data, errors=()):
    attempts = [AttemptRecord(1, False, ["x: bad"], 10, "{}")] if not valid else []
    attempts.append(AttemptRecord(len(attempts) + 1, valid, list(errors), 20, "{}"))
    return ExtractionResult(valid=valid, provider="mock", model="recorded", mode="text",
                            data=data, errors=list(errors), attempts=attempts, latency_ms=30)


def test_valid_extraction_is_stored_with_line_items(session, rendered, invoice_label):
    record = save_extraction(session, "a.pdf", rendered, _result(True, invoice_label))
    assert record.id and record.valid and record.number == invoice_label["number"]
    assert record.issue_date.isoformat() == invoice_label["issue_date"]
    assert record.secondary_date.isoformat() == invoice_label["due_date"]
    assert [i.amount for i in record.line_items] == [
        i["amount"] for i in invoice_label["line_items"]
    ]


def test_invalid_extraction_keeps_raw_data_but_no_trusted_columns(session, rendered,
                                                                  broken_invoice):
    record = save_extraction(session, "b.pdf", rendered,
                             _result(False, broken_invoice, ["total: wrong"]))
    assert not record.valid and record.data == broken_invoice
    assert record.total is None and record.number is None
    assert session.scalar(select(func.count()).select_from(LineItemRecord)) == 0
    assert record.attempt_count == 2


def test_workbook_has_documents_and_line_items(session, rendered, invoice_label, broken_invoice):
    save_extraction(session, "a.pdf", rendered, _result(True, invoice_label))
    save_extraction(session, "b.pdf", rendered, _result(False, broken_invoice, ["total: wrong"]))
    workbook = load_workbook(io.BytesIO(build_workbook(documents_for_export(session))))

    assert workbook.sheetnames == ["Documents", "Line items"]
    docs = list(workbook["Documents"].values)
    assert list(docs[0]) == DOCUMENT_COLUMNS and len(docs) == 3
    row = dict(zip(DOCUMENT_COLUMNS, docs[1], strict=True))
    assert row["status"] == "valid" and row["total"] == invoice_label["total"]
    assert row["line_item_count"] == len(invoice_label["line_items"])
    bad = dict(zip(DOCUMENT_COLUMNS, docs[2], strict=True))
    assert bad["status"] == "invalid" and bad["validation_errors"] == "total: wrong"

    items = list(workbook["Line items"].values)
    assert list(items[0]) == LINE_ITEM_COLUMNS
    assert len(items) == 1 + len(invoice_label["line_items"])
    assert workbook["Documents"]["K2"].number_format == "#,##0.00"


def test_export_can_be_filtered_to_one_document(session, rendered, invoice_label, po_label):
    first = save_extraction(session, "a.pdf", rendered, _result(True, invoice_label))
    save_extraction(session, "c.pdf", rendered, _result(True, po_label))
    only = documents_for_export(session, first.id)
    assert [d.id for d in only] == [first.id]
    assert [d.filename for d in list_documents(session)] == ["c.pdf", "a.pdf"]


def test_empty_export_still_has_headers():
    workbook = load_workbook(io.BytesIO(build_workbook([])))
    assert list(next(workbook["Documents"].values)) == DOCUMENT_COLUMNS

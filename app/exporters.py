"""Excel export: one sheet of document headers, one sheet of line items."""

from __future__ import annotations

import io
from collections.abc import Iterable, Sequence

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .storage import DocumentRecord

DOCUMENT_COLUMNS = [
    "id", "filename", "status", "document_type", "number", "vendor_name", "buyer_name",
    "issue_date", "due_or_delivery_date", "currency", "subtotal", "tax", "total",
    "line_item_count", "provider", "model", "mode", "attempts", "latency_ms",
    "validation_errors", "extracted_at",
]
LINE_ITEM_COLUMNS = [
    "document_id", "document_number", "line_no", "description", "quantity", "unit_price",
    "amount",
]
MONEY_FORMAT = "#,##0.00"


def _write_sheet(
    sheet: Worksheet, columns: Sequence[str], rows: Iterable[Sequence[object]],
    money_columns: Sequence[str],
) -> None:
    sheet.append(list(columns))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", start_color="1F3A5F")
        cell.alignment = Alignment(vertical="center")
    for row in rows:
        sheet.append(list(row))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, name in enumerate(columns, start=1):
        letter = get_column_letter(index)
        longest = max(len(str(c.value)) if c.value is not None else 0 for c in sheet[letter])
        sheet.column_dimensions[letter].width = min(max(10, longest + 2), 60)
        if name in money_columns:
            for cell in sheet[letter][1:]:
                cell.number_format = MONEY_FORMAT


def build_workbook(documents: Sequence[DocumentRecord]) -> bytes:
    workbook = Workbook()
    documents_sheet = workbook.active
    documents_sheet.title = "Documents"
    _write_sheet(
        documents_sheet,
        DOCUMENT_COLUMNS,
        (
            [
                d.id, d.filename, "valid" if d.valid else "invalid", d.document_type, d.number,
                d.vendor_name, d.buyer_name, d.issue_date, d.secondary_date, d.currency,
                d.subtotal, d.tax, d.total, len(d.line_items), d.provider, d.model, d.mode,
                d.attempt_count, d.latency_ms, "; ".join(d.errors), d.created_at,
            ]
            for d in documents
        ),
        money_columns=("subtotal", "tax", "total"),
    )
    _write_sheet(
        workbook.create_sheet("Line items"),
        LINE_ITEM_COLUMNS,
        (
            [d.id, d.number, item.line_no, item.description, item.quantity, item.unit_price,
             item.amount]
            for d in documents
            for item in d.line_items
        ),
        money_columns=("unit_price", "amount"),
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()

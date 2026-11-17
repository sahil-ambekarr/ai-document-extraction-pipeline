from __future__ import annotations

import copy
import datetime as dt
import json

import pytest
from pydantic import ValidationError

from app.schemas import Invoice, PurchaseOrder, format_validation_errors, validate_document
from tests.conftest import LABELS


def _errors(payload: dict) -> list[str]:
    with pytest.raises(ValidationError) as info:
        validate_document(payload)
    return format_validation_errors(info.value)


def test_every_ground_truth_label_is_schema_valid():
    paths = sorted(LABELS.glob("*.json"))
    assert len(paths) == 18
    for path in paths:
        label = json.loads(path.read_text(encoding="utf-8"))
        validate_document(label)  # raises on failure


def test_discriminator_picks_the_right_model(invoice_label, po_label):
    assert isinstance(validate_document(invoice_label), Invoice)
    assert isinstance(validate_document(po_label), PurchaseOrder)


def test_wrong_total_is_reported(broken_invoice):
    errors = _errors(broken_invoice)
    assert len(errors) == 1
    assert "subtotal + tax" in errors[0] and "but total is" in errors[0]


def test_line_items_must_sum_to_subtotal(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["subtotal"] = round(payload["subtotal"] + 5, 2)
    payload["total"] = round(payload["subtotal"] + payload["tax"], 2)
    errors = _errors(payload)
    assert any("line item amounts sum to" in e for e in errors)


def test_line_amount_must_equal_quantity_times_price(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["line_items"][0]["amount"] += 3
    errors = _errors(payload)
    assert any(e.startswith("line_items.0:") and "quantity x unit_price" in e for e in errors)


def test_small_rounding_drift_is_tolerated(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["total"] = round(payload["total"] + 0.03, 2)
    validate_document(payload)


def test_numbers_printed_with_symbols_are_parsed(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["total"] = f"${payload['total']:,.2f}"
    payload["line_items"][0]["unit_price"] = str(payload["line_items"][0]["unit_price"])
    doc = validate_document(payload)
    assert doc.total == invoice_label["total"]


def test_due_date_before_issue_date_is_rejected(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["due_date"] = "2001-01-01"
    errors = _errors(payload)
    assert any("due_date" in e and "before issue_date" in e for e in errors)


def test_delivery_date_before_order_date_is_rejected(po_label):
    payload = copy.deepcopy(po_label)
    payload["delivery_date"] = "2001-01-01"
    assert any("delivery_date" in e for e in _errors(payload))


@pytest.mark.parametrize("value", ["1999-12-31", (dt.date.today() + dt.timedelta(days=800))])
def test_implausible_issue_dates_are_rejected(invoice_label, value):
    payload = copy.deepcopy(invoice_label)
    payload["issue_date"] = str(value)
    payload["due_date"] = None
    assert any(e.startswith("issue_date") for e in _errors(payload))


def test_currency_is_normalised_and_checked(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["currency"] = " usd "
    assert validate_document(payload).currency == "USD"
    payload["currency"] = "$"
    assert any(e.startswith("currency") for e in _errors(payload))


def test_missing_fields_and_empty_items_are_listed(invoice_label):
    payload = copy.deepcopy(invoice_label)
    del payload["vendor_name"]
    payload["line_items"] = []
    errors = _errors(payload)
    assert any(e.startswith("vendor_name") for e in errors)
    assert any(e.startswith("line_items") for e in errors)


def test_unknown_document_type_gives_one_clear_error(invoice_label):
    payload = copy.deepcopy(invoice_label)
    payload["document_type"] = "receipt"
    errors = _errors(payload)
    assert len(errors) == 1
    assert "document_type" in errors[0] or "tag" in errors[0].lower()

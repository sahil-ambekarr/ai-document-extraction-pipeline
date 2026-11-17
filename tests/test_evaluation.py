from __future__ import annotations

import copy

import pytest

from app.evaluation import (
    HEADER_FIELDS,
    aggregate,
    amounts_match,
    dates_match,
    normalise_text,
    score_document,
    score_line_items,
)


def test_perfect_prediction_scores_one(invoice_label):
    score = score_document(copy.deepcopy(invoice_label), invoice_label)
    assert score.field_accuracy == 1.0 and score.line_items.f1 == 1.0 and score.all_correct


def test_missing_prediction_scores_zero(invoice_label):
    score = score_document(None, invoice_label)
    assert score.line_items.f1 == 0.0 and not score.all_correct
    # due_date is set in the label, so "missing" is wrong for every field.
    assert score.field_accuracy == 0.0


def test_text_normalisation_ignores_case_spacing_and_punctuation():
    assert normalise_text("  Bluefinch Office Supply Co. ") == "bluefinch office supply co"
    assert normalise_text("INV-2026-0001") == normalise_text("inv 2026 0001")
    assert normalise_text(None) == ""


def test_amount_tolerance_and_string_amounts():
    assert amounts_match(100.004, 100.0)
    assert amounts_match("$1,234.50", 1234.5)
    assert not amounts_match(100.02, 100.0)
    assert not amounts_match(None, 1.0)
    assert amounts_match(None, None)


def test_dates_compare_after_parsing():
    assert dates_match("2026-03-04", "2026-03-04")
    assert dates_match("2026-03-04T00:00:00", "2026-03-04")
    assert not dates_match("04/03/2026", "2026-03-04")
    assert dates_match(None, None) and not dates_match("2026-01-01", None)


def test_one_wrong_header_field(invoice_label):
    predicted = copy.deepcopy(invoice_label)
    predicted["vendor_name"] = "Someone Else"
    score = score_document(predicted, invoice_label)
    assert score.fields["vendor_name"] is False
    assert score.field_accuracy == pytest.approx((len(HEADER_FIELDS) - 1) / len(HEADER_FIELDS))
    assert not score.all_correct


def test_line_item_f1_counts_missing_extra_and_wrong_rows():
    gold = [
        {"description": "Oak desk", "quantity": 1, "unit_price": 300.0, "amount": 300.0},
        {"description": "Task chair", "quantity": 2, "unit_price": 100.0, "amount": 200.0},
    ]
    assert score_line_items(gold, gold).f1 == 1.0
    # One right, one with a wrong amount, one invented row.
    predicted = [
        gold[0],
        dict(gold[1], amount=210.0),
        {"description": "Lamp", "quantity": 1, "unit_price": 5, "amount": 5},
    ]
    score = score_line_items(predicted, gold)
    assert (score.matched, score.predicted, score.expected) == (1, 3, 2)
    assert score.precision == pytest.approx(1 / 3) and score.recall == pytest.approx(0.5)
    assert score.f1 == pytest.approx(0.4)


def test_line_items_match_one_to_one():
    row = {"description": "Pens", "quantity": 1, "unit_price": 2.0, "amount": 2.0}
    score = score_line_items([row], [row, row])
    assert score.matched == 1 and score.recall == 0.5


def test_line_items_tolerate_small_description_differences():
    gold = [{"description": "Ballpoint pens, blue, pack of 50", "quantity": 8,
             "unit_price": 10.87, "amount": 86.96}]
    predicted = [dict(gold[0], description="Ballpoint pens blue pack of 50")]
    assert score_line_items(predicted, gold).f1 == 1.0
    assert score_line_items("not a list", gold).f1 == 0.0


def test_aggregate_micro_and_macro(invoice_label, po_label):
    perfect = score_document(copy.deepcopy(invoice_label), invoice_label)
    empty = score_document(None, po_label)
    summary = aggregate([perfect, empty])
    assert summary["documents"] == 2
    assert summary["all_fields_correct_rate"] == 0.5
    assert summary["line_item_f1_macro"] == pytest.approx(0.5)
    n_gold = len(invoice_label["line_items"]) + len(po_label["line_items"])
    assert summary["line_item_recall"] == pytest.approx(len(invoice_label["line_items"]) / n_gold)
    assert summary["per_field_accuracy"]["total"] == 0.5
    assert aggregate([]) == {}

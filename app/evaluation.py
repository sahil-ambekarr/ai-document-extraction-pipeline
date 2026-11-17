"""Scoring an extraction against a ground-truth label.

Kept in the app package (not in scripts/) so the metric functions are unit
tested like any other code.

- Header text fields: exact match after normalisation (case, whitespace,
  punctuation). Dates compare as ISO strings after parsing.
- Amounts: absolute tolerance of AMOUNT_TOLERANCE currency units.
- Line items: greedy one-to-one matching, then precision / recall / F1.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

AMOUNT_TOLERANCE = 0.01
DESCRIPTION_SIMILARITY = 0.85

TEXT_FIELDS = ("document_type", "vendor_name", "buyer_name", "number", "currency")
DATE_FIELDS = ("issue_date", "secondary_date")  # secondary = due date or delivery date
AMOUNT_FIELDS = ("subtotal", "tax", "total")
HEADER_FIELDS = TEXT_FIELDS + DATE_FIELDS + AMOUNT_FIELDS


def normalise_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _to_date(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip()[:10]).isoformat()
        except ValueError:
            return None
    return None


def amounts_match(predicted: Any, expected: Any, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    if expected is None:
        return predicted is None
    value = _to_float(predicted)
    return value is not None and abs(value - float(expected)) <= tolerance


def text_matches(predicted: Any, expected: Any) -> bool:
    return normalise_text(predicted) == normalise_text(expected)


def dates_match(predicted: Any, expected: Any) -> bool:
    if expected is None:
        return predicted in (None, "")
    return _to_date(predicted) == _to_date(expected)


def _secondary_date(document: dict[str, Any]) -> Any:
    return document.get("due_date") or document.get("delivery_date")


def score_header(predicted: dict[str, Any], expected: dict[str, Any]) -> dict[str, bool]:
    scores: dict[str, bool] = {}
    for name in TEXT_FIELDS:
        scores[name] = text_matches(predicted.get(name), expected.get(name))
    scores["issue_date"] = dates_match(predicted.get("issue_date"), expected.get("issue_date"))
    scores["secondary_date"] = dates_match(_secondary_date(predicted), _secondary_date(expected))
    for name in AMOUNT_FIELDS:
        scores[name] = amounts_match(predicted.get(name), expected.get(name))
    return scores


def line_items_match(predicted: dict[str, Any], expected: dict[str, Any]) -> bool:
    similar = SequenceMatcher(
        None, normalise_text(predicted.get("description")), normalise_text(expected["description"])
    ).ratio() >= DESCRIPTION_SIMILARITY
    return (
        similar
        and amounts_match(predicted.get("quantity"), expected["quantity"])
        and amounts_match(predicted.get("unit_price"), expected["unit_price"])
        and amounts_match(predicted.get("amount"), expected["amount"])
    )


@dataclass
class LineItemScore:
    matched: int
    predicted: int
    expected: int

    @property
    def precision(self) -> float:
        return self.matched / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0


def score_line_items(predicted: Any, expected: list[dict[str, Any]]) -> LineItemScore:
    items = [i for i in predicted if isinstance(i, dict)] if isinstance(predicted, list) else []
    unused = list(range(len(items)))
    matched = 0
    for gold in expected:
        for position, index in enumerate(unused):
            if line_items_match(items[index], gold):
                matched += 1
                del unused[position]
                break
    return LineItemScore(matched=matched, predicted=len(items), expected=len(expected))


@dataclass
class DocumentScore:
    fields: dict[str, bool]
    line_items: LineItemScore
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def field_accuracy(self) -> float:
        return sum(self.fields.values()) / len(self.fields)

    @property
    def all_correct(self) -> bool:
        return all(self.fields.values()) and self.line_items.f1 == 1.0


def score_document(predicted: dict[str, Any] | None, expected: dict[str, Any]) -> DocumentScore:
    """Score one extraction. A missing prediction scores zero everywhere."""
    predicted = predicted or {}
    return DocumentScore(
        fields=score_header(predicted, expected),
        line_items=score_line_items(predicted.get("line_items"), expected["line_items"]),
    )


def aggregate(scores: list[DocumentScore]) -> dict[str, Any]:
    """Field-level accuracy across documents, plus line-item micro and macro F1."""
    if not scores:
        return {}
    n = len(scores)
    per_field = {name: sum(s.fields[name] for s in scores) / n for name in HEADER_FIELDS}
    matched = sum(s.line_items.matched for s in scores)
    predicted = sum(s.line_items.predicted for s in scores)
    expected = sum(s.line_items.expected for s in scores)
    micro = LineItemScore(matched, predicted, expected)
    return {
        "documents": n,
        "field_accuracy": sum(per_field.values()) / len(per_field),
        "per_field_accuracy": per_field,
        "line_item_precision": micro.precision,
        "line_item_recall": micro.recall,
        "line_item_f1_micro": micro.f1,
        "line_item_f1_macro": sum(s.line_items.f1 for s in scores) / n,
        "all_fields_correct_rate": sum(s.all_correct for s in scores) / n,
    }

"""Pydantic schemas for the two supported document types.

Field-level rules (types, required fields, currency format) are declared on the
fields. Cross-field rules (arithmetic and date sanity) live in model validators
so that one validation pass reports every problem at once - the retry loop
feeds that full list back to the model.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

# Absolute tolerance, in currency units, for arithmetic checks. Documents round
# each line to 2 decimals, so a few cents of drift across many lines is normal.
AMOUNT_TOLERANCE = 0.05
EARLIEST_DATE = dt.date(2000, 1, 1)
# A document dated more than this far ahead is almost certainly a misread year.
MAX_FUTURE_DAYS = 366


def _parse_number(value: object) -> object:
    """Accept numbers printed the way documents print them: "$1,234.50", "1 234.50"."""
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        if cleaned and re.fullmatch(r"-?\d*\.?\d+", cleaned):
            return float(cleaned)
    return value


Number = Annotated[float, BeforeValidator(_parse_number)]


def _close(a: float, b: float, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    description: str = Field(min_length=1)
    quantity: Number = Field(gt=0)
    unit_price: Number = Field(ge=0)
    amount: Number = Field(ge=0)

    @model_validator(mode="after")
    def _amount_matches_quantity_times_price(self) -> LineItem:
        expected = self.quantity * self.unit_price
        if not _close(expected, self.amount):
            raise ValueError(
                f"amount {self.amount:.2f} does not equal quantity x unit_price "
                f"({self.quantity:g} x {self.unit_price:.2f} = {expected:.2f})"
            )
        return self


class _CommercialDocument(BaseModel):
    """Fields and rules shared by invoices and purchase orders."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    vendor_name: str = Field(min_length=1, description="Company that sells / issues the invoice")
    buyer_name: str = Field(min_length=1, description="Company that buys / is billed")
    number: str = Field(min_length=1, description="Invoice number or PO number as printed")
    issue_date: dt.date = Field(description="Invoice date or order date, ISO YYYY-MM-DD")
    currency: str = Field(description="ISO 4217 code, e.g. USD, EUR, GBP, INR")
    line_items: list[LineItem] = Field(min_length=1)
    subtotal: Number = Field(ge=0)
    tax: Number = Field(ge=0)
    total: Number = Field(ge=0)

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("currency")
    @classmethod
    def _currency_is_iso_code(cls, value: str) -> str:
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a 3-letter ISO 4217 code such as USD")
        return value

    @field_validator("issue_date")
    @classmethod
    def _issue_date_is_plausible(cls, value: dt.date) -> dt.date:
        latest = dt.date.today() + dt.timedelta(days=MAX_FUTURE_DAYS)
        if not EARLIEST_DATE <= value <= latest:
            raise ValueError(f"issue_date {value} is outside the plausible range")
        return value

    @model_validator(mode="after")
    def _totals_add_up(self) -> _CommercialDocument:
        problems: list[str] = []
        line_sum = round(sum(item.amount for item in self.line_items), 2)
        if not _close(line_sum, self.subtotal):
            problems.append(
                f"line item amounts sum to {line_sum:.2f} but subtotal is {self.subtotal:.2f}"
            )
        if not _close(self.subtotal + self.tax, self.total):
            problems.append(
                f"subtotal + tax = {self.subtotal + self.tax:.2f} but total is {self.total:.2f}"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _check_not_before_issue(self, field_name: str, value: dt.date | None) -> None:
        if value is not None and value < self.issue_date:
            raise ValueError(f"{field_name} {value} is before issue_date {self.issue_date}")


class Invoice(_CommercialDocument):
    document_type: Literal["invoice"] = "invoice"
    due_date: dt.date | None = Field(default=None, description="Payment due date, if printed")

    @model_validator(mode="after")
    def _due_date_not_before_issue(self) -> Invoice:
        self._check_not_before_issue("due_date", self.due_date)
        return self


class PurchaseOrder(_CommercialDocument):
    document_type: Literal["purchase_order"] = "purchase_order"
    delivery_date: dt.date | None = Field(
        default=None, description="Requested delivery date, if printed"
    )

    @model_validator(mode="after")
    def _delivery_date_not_before_issue(self) -> PurchaseOrder:
        self._check_not_before_issue("delivery_date", self.delivery_date)
        return self


# `document_type` picks the schema, so a wrong type gives one clear error
# instead of the errors of both branches.
ExtractedDocument = Annotated[Invoice | PurchaseOrder, Field(discriminator="document_type")]
_ADAPTER: TypeAdapter[Invoice | PurchaseOrder] = TypeAdapter(ExtractedDocument)


def validate_document(payload: object) -> Invoice | PurchaseOrder:
    """Validate a parsed JSON payload. Raises pydantic.ValidationError."""
    return _ADAPTER.validate_python(payload)


def format_validation_errors(error: ValidationError) -> list[str]:
    """Turn a ValidationError into short, model-readable lines.

    The path is kept (e.g. `line_items.2.amount`) because the model needs to
    know which value to fix.
    """
    lines: list[str] = []
    for item in error.errors():
        # Drop the union tag ("invoice"/"purchase_order") pydantic prepends.
        path = [str(p) for p in item["loc"] if p not in ("invoice", "purchase_order")]
        location = ".".join(path) or "document"
        message = item["msg"].removeprefix("Value error, ")
        lines.append(f"{location}: {message}")
    return lines

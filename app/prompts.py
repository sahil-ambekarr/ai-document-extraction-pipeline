"""Prompt text. Kept in one module so prompt changes are easy to review and diff."""

from __future__ import annotations

SYSTEM_PROMPT = """You extract structured data from business documents (invoices and \
purchase orders). Reply with ONE JSON object and nothing else.

JSON shape:
{
  "document_type": "invoice" or "purchase_order",
  "vendor_name": string,      // the seller / supplier / company that issued an invoice
  "buyer_name": string,       // the customer: "Bill To", "Buyer", "Ordered By", "Customer"
  "number": string,           // invoice number or PO number exactly as printed
  "issue_date": "YYYY-MM-DD", // invoice date or order date
  "due_date": "YYYY-MM-DD" or null,      // invoices only: payment due date
  "delivery_date": "YYYY-MM-DD" or null, // purchase orders only: requested delivery date
  "currency": string,         // 3-letter ISO code: USD, EUR, GBP, INR ...
  "line_items": [
    {"description": string, "quantity": number, "unit_price": number, "amount": number}
  ],
  "subtotal": number,         // sum of line amounts, before tax
  "tax": number,              // tax / VAT / GST amount in currency units, not the percentage
  "total": number             // subtotal + tax
}

Rules:
- Copy values from the document. Do not invent, round or recompute them.
- Convert every date to YYYY-MM-DD.
- Numbers are plain JSON numbers: no currency symbols, no thousands separators.
- Include every line item, in document order, across all pages.
- For a purchase order the vendor is the supplier the order is sent to, and the buyer is \
the company placing the order.
- Each line: amount = quantity x unit_price. Overall: subtotal + tax = total."""

TEXT_USER_TEMPLATE = """Extract the JSON from this document text (PDF text layer, \
reading order may be imperfect).

<document>
{text}
</document>"""

VISION_USER_PROMPT = (
    "Extract the JSON from the attached document page image(s). "
    "Pages are given in order; the line item table may continue across pages."
)

TYPE_HINT_TEMPLATE = '\n\nThe document is known to be of type "{doc_type}".'

RETRY_TEMPLATE = """Your previous JSON was rejected by validation:
{errors}

Look at the document again and fix these problems. Reply with the complete corrected \
JSON object only."""


def build_retry_message(errors: list[str]) -> str:
    return RETRY_TEMPLATE.format(errors="\n".join(f"- {line}" for line in errors))

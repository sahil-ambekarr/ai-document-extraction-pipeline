"""Generate synthetic, labelled invoices and purchase orders.

Every company name, address and number below is invented. The output is
reproducible: the same seed gives byte-identical PDFs (reportlab's invariant
mode removes timestamps and random IDs) and identical labels.

    python scripts/generate_samples.py            # writes data/samples + data/labels
    python scripts/generate_samples.py --seed 7 --out-dir /tmp/other

Layouts: "classic" (ruled table), "banded" (coloured header, striped rows) and
"compact" (Courier, receipt-like, European number format for EUR). A few
documents carry enough line items to run onto a second page, and a few are
rendered to PNG and degraded (rotation, noise, blur) to imitate scans.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.schemas import validate_document  # noqa: E402

PAGE_W, PAGE_H = A4
MARGIN = 48

VENDORS = [
    ("Bluefinch Office Supply Co.", "14 Lantern Row, Port Ellery, ZX 40211"),
    ("Quarry Lane Fabrication Ltd", "Unit 9, Quarry Lane Works, Hollowmere HM3 2QL"),
    ("Nimbus Paper & Ink GmbH", "Wolkenstrasse 88, 99417 Neuburg-am-See"),
    ("Otterbrook Lab Goods", "220 Otterbrook Way, Fernvale, ZY 50873"),
    ("Copperleaf Catering Services", "3 Kettle Yard, Copperleaf Market, CL9 4KY"),
    ("Saffron Circuit Components Pvt Ltd", "Plot 17, Example Industrial Estate, Sector 0"),
    ("Driftwood Furniture Makers", "71 Tidewater Road, Saltmarsh Bay, ZW 31002"),
    ("Pinecone Cleaning Collective", "5 Needle Street, Evergreen Hollow, ZV 22019"),
]
BUYERS = [
    ("Example Robotics Ltd", "1 Placeholder Park, Demo City, DC1 0AA"),
    ("Fictional Harbour Clinic", "400 Seawall Avenue, Mockport, ZT 11873"),
    ("Sample Valley School District", "88 Learning Lane, Sample Valley, ZS 60412"),
    ("Imaginary Rail Museum Trust", "Old Engine Shed, Notreal Junction, NJ7 7RJ"),
    ("Testbed Analytics GmbH", "Musterweg 12, 10999 Beispielstadt"),
    ("Acme-Free Bakery Co-op", "29 Crumb Court, Loafton, ZR 40455"),
]
CATALOG = [
    ("A4 copier paper, 80gsm, box of 5 reams", 18.0, 42.0),
    ("Ballpoint pens, blue, pack of 50", 6.5, 14.0),
    ("Stainless steel bracket, 120 mm", 2.1, 7.8),
    ("M8 hex bolts, zinc plated, bag of 100", 9.0, 21.0),
    ("Nitrile gloves, size M, box of 100", 7.2, 15.5),
    ("Glass beakers 250 ml, set of 6", 18.0, 36.0),
    ("Pipette tips 200 ul, rack of 96", 4.4, 11.9),
    ("Lunch buffet per person", 9.0, 24.0),
    ("Coffee and tea service per person", 2.5, 6.0),
    ("Oak desk, 140 x 70 cm", 210.0, 480.0),
    ("Task chair with lumbar support", 95.0, 260.0),
    ("Bookshelf, 5 tiers, walnut finish", 120.0, 310.0),
    ("Deep cleaning, per hour", 18.0, 42.0),
    ("Window cleaning, per floor", 45.0, 120.0),
    ("Ceramic capacitor kit, 300 pcs", 12.0, 28.0),
    ("Microcontroller dev board", 11.0, 34.0),
    ("USB-C cable 1 m", 3.2, 9.5),
    ("Laser toner cartridge, black", 38.0, 96.0),
    ("Whiteboard markers, assorted, pack of 12", 7.0, 16.0),
    ("Safety goggles, anti-fog", 4.5, 12.0),
    ("Aluminium sheet 1 mm, 1 x 2 m", 34.0, 88.0),
    ("Welding wire spool 0.8 mm, 5 kg", 26.0, 61.0),
    ("Shipping cartons, 40 x 30 x 30 cm, bundle of 25", 19.0, 45.0),
    ("On-site installation, per hour", 35.0, 95.0),
]
CURRENCIES = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "INR "}
TAX_RATES = {"USD": [0.0, 0.0725, 0.0825], "EUR": [0.19, 0.2], "GBP": [0.2], "INR": [0.18, 0.12]}
DATE_STYLES = ["iso", "long", "us", "dotted"]


@dataclass
class DocSpec:
    stem: str
    doc_type: str  # invoice | purchase_order
    layout: str  # classic | banded | compact
    n_items: int
    scan: bool = False


# 18 documents: 10 invoices, 8 purchase orders; 4 rendered as scans. Three are long (30+ lines)
# and named "multipage", but only inv-005 and po-015 span two pages: the classic layout
# fits inv-007's 30 lines on one page. Names are kept so labels and recordings stay stable.
SPECS = [
    DocSpec("inv-001-classic", "invoice", "classic", 3),
    DocSpec("inv-002-banded", "invoice", "banded", 5),
    DocSpec("inv-003-compact", "invoice", "compact", 4),
    DocSpec("inv-004-classic", "invoice", "classic", 6),
    DocSpec("inv-005-banded-multipage", "invoice", "banded", 32),
    DocSpec("inv-006-compact", "invoice", "compact", 2),
    DocSpec("inv-007-classic-multipage", "invoice", "classic", 30),
    DocSpec("inv-008-banded", "invoice", "banded", 4),
    DocSpec("inv-009-classic-scan", "invoice", "classic", 4, scan=True),
    DocSpec("inv-010-compact-scan", "invoice", "compact", 3, scan=True),
    DocSpec("po-011-classic", "purchase_order", "classic", 4),
    DocSpec("po-012-banded", "purchase_order", "banded", 3),
    DocSpec("po-013-compact", "purchase_order", "compact", 5),
    DocSpec("po-014-classic", "purchase_order", "classic", 2),
    DocSpec("po-015-banded-multipage", "purchase_order", "banded", 34),
    DocSpec("po-016-compact", "purchase_order", "compact", 6),
    DocSpec("po-017-banded-scan", "purchase_order", "banded", 3, scan=True),
    DocSpec("po-018-classic-scan", "purchase_order", "classic", 5, scan=True),
]


# ---------------------------------------------------------------- content


def format_date(value: dt.date, style: str) -> str:
    if style == "long":
        return f"{value.day} {value.strftime('%B %Y')}"
    if style == "us":
        return value.strftime("%m/%d/%Y")
    if style == "dotted":
        return value.strftime("%d.%m.%Y")
    return value.isoformat()


def format_money(value: float, currency: str, european: bool = False) -> str:
    text = f"{value:,.2f}"
    if european:
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{CURRENCIES[currency]}{text}"


def format_qty(value: float) -> str:
    return f"{value:g}"


def make_label(spec: DocSpec, rng: random.Random, index: int) -> dict:
    vendor, vendor_addr = rng.choice(VENDORS)
    buyer, buyer_addr = rng.choice(BUYERS)
    currency = rng.choice(list(CURRENCIES))
    issue = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randint(0, 300))
    second = issue + dt.timedelta(days=rng.choice([14, 21, 30, 45]))
    items = []
    for _ in range(spec.n_items):
        description, low, high = rng.choice(CATALOG)
        quantity = rng.choice([1, 2, 3, 4, 5, 6, 8, 10, 12, 20, 25])
        if "per hour" in description:
            quantity = rng.choice([1.5, 2.5, 4, 6, 7.5])
        unit_price = round(rng.uniform(low, high), 2)
        items.append(
            {"description": description, "quantity": quantity, "unit_price": unit_price,
             "amount": round(quantity * unit_price, 2)}
        )
    subtotal = round(sum(i["amount"] for i in items), 2)
    tax = round(subtotal * rng.choice(TAX_RATES[currency]), 2)
    prefix = "INV" if spec.doc_type == "invoice" else "PO"
    number = rng.choice(
        [f"{prefix}-2026-{index:04d}", f"{prefix}{rng.randint(10000, 99999)}",
         f"{rng.randint(100, 999)}/{prefix}/26"]
    )
    label = {
        "document_type": spec.doc_type,
        "vendor_name": vendor,
        "buyer_name": buyer,
        "number": number,
        "issue_date": issue.isoformat(),
        "due_date": second.isoformat() if spec.doc_type == "invoice" else None,
        "delivery_date": second.isoformat() if spec.doc_type == "purchase_order" else None,
        "currency": currency,
        "line_items": items,
        "subtotal": subtotal,
        "tax": tax,
        "total": round(subtotal + tax, 2),
    }
    validate_document(label)  # every label must satisfy the schema it is graded against
    extras = {
        "vendor_addr": vendor_addr,
        "buyer_addr": buyer_addr,
        "date_style": rng.choice(DATE_STYLES),
        "tax_rate": 0 if subtotal == 0 else round(tax / subtotal * 100, 2),
        "terms": rng.choice(["Net 30", "Net 14", "Due on receipt", "Net 45"]),
    }
    return {"label": label, "extras": extras}


# ---------------------------------------------------------------- drawing


class Writer:
    """Thin wrapper over a reportlab canvas that tracks the cursor and pages."""

    def __init__(self, canvas: Canvas, layout: str, footer: str) -> None:
        self.c = canvas
        self.layout = layout
        self.footer = footer
        self.page = 1
        self.y = PAGE_H - MARGIN
        self.font = "Courier" if layout == "compact" else "Helvetica"
        self.bold = "Courier-Bold" if layout == "compact" else "Helvetica-Bold"

    def text(self, x: float, y: float, value: str, size: float = 10, bold: bool = False,
             align: str = "left", color: colors.Color = colors.black) -> None:
        self.c.setFillColor(color)
        self.c.setFont(self.bold if bold else self.font, size)
        if align == "right":
            self.c.drawRightString(x, y, value)
        elif align == "center":
            self.c.drawCentredString(x, y, value)
        else:
            self.c.drawString(x, y, value)
        self.c.setFillColor(colors.black)

    def finish_page(self) -> None:
        self.text(MARGIN, 24, self.footer, size=7, color=colors.grey)
        self.text(PAGE_W - MARGIN, 24, f"Page {self.page}", size=7, align="right",
                  color=colors.grey)

    def new_page(self) -> None:
        self.finish_page()
        self.c.showPage()
        self.page += 1
        self.y = PAGE_H - MARGIN


def draw_document(spec: DocSpec, label: dict, extras: dict) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=A4, invariant=1)
    canvas.setTitle(f"Synthetic {spec.doc_type.replace('_', ' ')} {label['number']}")
    canvas.setAuthor("generate_samples.py")
    w = Writer(canvas, spec.layout, "SAMPLE DOCUMENT - fictional data generated for testing")
    currency = label["currency"]
    european = spec.layout == "compact" and currency == "EUR"
    money = lambda v: format_money(v, currency, european)  # noqa: E731
    is_invoice = spec.doc_type == "invoice"
    title = "INVOICE" if is_invoice else "PURCHASE ORDER"
    date_style = extras["date_style"]
    issue = format_date(dt.date.fromisoformat(label["issue_date"]), date_style)
    second_raw = label["due_date"] or label["delivery_date"]
    second = format_date(dt.date.fromisoformat(second_raw), date_style)
    meta = [
        ("Invoice No." if is_invoice else "PO Number", label["number"]),
        ("Invoice Date" if is_invoice else "Order Date", issue),
        ("Due Date" if is_invoice else "Deliver By", second),
        ("Currency", currency),
    ]
    party_from = ("From" if is_invoice else "Supplier", label["vendor_name"], extras["vendor_addr"])
    party_to = ("Bill To" if is_invoice else "Ordered By", label["buyer_name"],
                extras["buyer_addr"])

    # Header
    if spec.layout == "banded":
        canvas.setFillColor(colors.HexColor("#2b4c7e"))
        canvas.rect(0, PAGE_H - 110, PAGE_W, 110, stroke=0, fill=1)
        issuer = label["vendor_name"] if is_invoice else label["buyer_name"]
        w.text(MARGIN, PAGE_H - 55, issuer, size=18, bold=True, color=colors.white)
        w.text(PAGE_W - MARGIN, PAGE_H - 55, title, size=22, bold=True, align="right",
               color=colors.white)
        w.y = PAGE_H - 140
        for col, (heading, name, addr) in zip((MARGIN, PAGE_W / 2), (party_from, party_to),
                                              strict=True):
            w.text(col, w.y, heading.upper(), size=8, bold=True, color=colors.grey)
            w.text(col, w.y - 14, name, size=11, bold=True)
            w.text(col, w.y - 28, addr, size=9)
        w.y -= 56
        for i, (key, value) in enumerate(meta):
            x = MARGIN + i * (PAGE_W - 2 * MARGIN) / 4
            w.text(x, w.y, key, size=8, color=colors.grey)
            w.text(x, w.y - 13, value, size=10, bold=True)
        w.y -= 36
    elif spec.layout == "compact":
        w.text(PAGE_W / 2, w.y, title, size=16, bold=True, align="center")
        w.y -= 26
        for heading, name, addr in (party_from, party_to):
            w.text(MARGIN, w.y, f"{heading + ':':<12}{name}", size=10, bold=True)
            w.text(MARGIN, w.y - 12, f"{'':<12}{addr}", size=9)
            w.y -= 30
        refs = {"Invoice No.": "Ref:", "PO Number": "Ref:", "Invoice Date": "Dated:",
                "Order Date": "Dated:", "Due Date": "Pay by:", "Deliver By": "Deliver:",
                "Currency": "Curr.:"}
        for key, value in meta:
            w.text(MARGIN, w.y, f"{refs[key]:<12}{value}", size=10)
            w.y -= 13
        w.y -= 10
    else:  # classic
        w.text(MARGIN, w.y, party_from[1], size=15, bold=True)
        w.text(MARGIN, w.y - 15, party_from[2], size=9)
        w.text(PAGE_W - MARGIN, w.y, title, size=20, bold=True, align="right")
        w.y -= 50
        w.text(MARGIN, w.y, party_to[0], size=9, bold=True, color=colors.grey)
        w.text(MARGIN, w.y - 14, party_to[1], size=11, bold=True)
        w.text(MARGIN, w.y - 27, party_to[2], size=9)
        for i, (key, value) in enumerate(meta):
            yy = w.y - i * 14
            w.text(PAGE_W - 200, yy, key + ":", size=9, color=colors.grey)
            w.text(PAGE_W - MARGIN, yy, value, size=10, align="right")
        w.y -= 70

    # Line item table
    cols = [MARGIN, MARGIN + 30, PAGE_W - MARGIN - 190, PAGE_W - MARGIN - 95, PAGE_W - MARGIN]
    headings = ["#", "Description", "Qty", "Unit price", "Amount"]
    row_h = 16 if spec.layout != "compact" else 14

    def table_header() -> None:
        if spec.layout == "banded":
            canvas.setFillColor(colors.HexColor("#dfe7f3"))
            canvas.rect(MARGIN - 4, w.y - 5, PAGE_W - 2 * MARGIN + 8, row_h, stroke=0, fill=1)
        w.text(cols[0], w.y, headings[0], size=9, bold=True)
        w.text(cols[1], w.y, headings[1], size=9, bold=True)
        for x, heading in zip(cols[2:], headings[2:], strict=True):
            w.text(x, w.y, heading, size=9, bold=True, align="right")
        canvas.setStrokeColor(colors.black if spec.layout == "classic" else colors.grey)
        canvas.line(MARGIN - 4, w.y - 6, PAGE_W - MARGIN + 4, w.y - 6)
        w.y -= row_h + 2

    table_header()
    for n, item in enumerate(label["line_items"], start=1):
        if w.y < 150:
            w.text(PAGE_W - MARGIN, w.y - 4, "continued on next page", size=8, align="right",
                   color=colors.grey)
            w.new_page()
            w.text(MARGIN, w.y, f"{title} {label['number']} (continued)", size=10, bold=True)
            w.y -= 28
            table_header()
        if spec.layout == "banded" and n % 2 == 0:
            canvas.setFillColor(colors.HexColor("#f3f6fa"))
            canvas.rect(MARGIN - 4, w.y - 5, PAGE_W - 2 * MARGIN + 8, row_h, stroke=0, fill=1)
        w.text(cols[0], w.y, str(n), size=9)
        w.text(cols[1], w.y, item["description"], size=9)
        w.text(cols[2], w.y, format_qty(item["quantity"]), size=9, align="right")
        w.text(cols[3], w.y, money(item["unit_price"]), size=9, align="right")
        w.text(cols[4], w.y, money(item["amount"]), size=9, align="right")
        if spec.layout == "classic":
            canvas.setStrokeColor(colors.lightgrey)
            canvas.line(MARGIN - 4, w.y - 5, PAGE_W - MARGIN + 4, w.y - 5)
        w.y -= row_h

    # Totals
    if w.y < 130:
        w.new_page()
    w.y -= 12
    tax_name = {"INR": "GST", "EUR": "VAT", "GBP": "VAT"}.get(currency, "Sales tax")
    totals = [
        ("Subtotal", label["subtotal"]),
        (f"{tax_name} ({extras['tax_rate']:g}%)", label["tax"]),
        ("Total due" if is_invoice else "Order total", label["total"]),
    ]
    for i, (key, value) in enumerate(totals):
        last = i == len(totals) - 1
        w.text(PAGE_W - MARGIN - 110, w.y, key, size=10, bold=last, align="right")
        w.text(PAGE_W - MARGIN, w.y, money(value), size=10, bold=last, align="right")
        w.y -= 16
    w.y -= 14
    note = (f"Payment terms: {extras['terms']}. Please quote the invoice number."
            if is_invoice else "Please confirm this order and the delivery date by email.")
    w.text(MARGIN, w.y, note, size=8, color=colors.grey)
    w.finish_page()
    canvas.save()
    return buffer.getvalue()


def to_scan(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """Render page 1 and degrade it to look like a phone or flatbed scan."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        image = pdf[0].render(scale=1.6).to_pil().convert("L")
    finally:
        pdf.close()
    angle = rng.choice([-1.6, -1.1, 0.8, 1.4])
    image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=235)
    # Noise comes from the seeded rng (not Image.effect_noise) so scans stay reproducible.
    noise = Image.frombytes("L", image.size, rng.randbytes(image.size[0] * image.size[1]))
    image = Image.blend(image, noise, rng.choice([0.08, 0.1, 0.12]))
    image = image.filter(ImageFilter.GaussianBlur(0.6))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def generate(seed: int, out_dir: Path) -> list[Path]:
    rng = random.Random(seed)
    samples_dir, labels_dir = out_dir / "samples", out_dir / "labels"
    samples_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, spec in enumerate(SPECS, start=1):
        made = make_label(spec, rng, index)
        pdf_bytes = draw_document(spec, made["label"], made["extras"])
        if spec.scan:
            target = samples_dir / f"{spec.stem}.png"
            target.write_bytes(to_scan(pdf_bytes, rng))
        else:
            target = samples_dir / f"{spec.stem}.pdf"
            target.write_bytes(pdf_bytes)
        label_path = labels_dir / f"{spec.stem}.json"
        label_path.write_text(
            json.dumps({"file": target.name, **made["label"]}, indent=2) + "\n",
            encoding="utf-8", newline="\n",
        )
        written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "data")
    args = parser.parse_args()
    for path in generate(args.seed, args.out_dir):
        print(path.relative_to(args.out_dir.parent) if args.out_dir == REPO_ROOT / "data"
              else path)


if __name__ == "__main__":
    main()

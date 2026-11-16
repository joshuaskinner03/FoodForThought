from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import fmt

FONTS = Path(__file__).parent / "fonts"
REGULAR, BOLD = "DejaVuSans", "DejaVuSans-Bold"
INK = colors.HexColor("#1d2a24")
MUTED = colors.HexColor("#5d6b64")
LINE = colors.HexColor("#c9c5b8")
PAGE_WIDTH = A4[0] - 40 * mm - 12

BODY = ParagraphStyle("body", fontName=REGULAR, fontSize=10, leading=14, textColor=INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=12, textColor=MUTED)
RIGHT = ParagraphStyle("right", parent=BODY, alignment=TA_RIGHT)
SMALL_RIGHT = ParagraphStyle("small_right", parent=SMALL, alignment=TA_RIGHT)
STRONG = ParagraphStyle("strong", parent=BODY, fontName=BOLD)
STRONG_RIGHT = ParagraphStyle("strong_right", parent=STRONG, alignment=TA_RIGHT)
NAME = ParagraphStyle("name", parent=RIGHT, fontName=BOLD, fontSize=13, leading=17)
TITLE = ParagraphStyle("title", parent=BODY, fontName=BOLD, fontSize=20, leading=24)
LABEL = ParagraphStyle("label", parent=SMALL, fontName=BOLD)


def _register_fonts() -> None:
    if REGULAR not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(REGULAR, str(FONTS / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD, str(FONTS / "DejaVuSans-Bold.ttf")))
        registerFontFamily(REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD)


def _text(value: str | None) -> str:
    return escape(value or "").replace("\n", "<br/>")


def _letterhead(practice: dict, logo: bytes | None) -> Table:
    lines = [Paragraph(_text(practice["display_name"]), NAME)]
    if practice["practice_name"] and practice["practitioner_name"]:
        who = ", ".join(part for part in (practice["practitioner_name"], practice["credentials"]) if part)
        lines.append(Paragraph(_text(who), RIGHT))
    elif practice["credentials"]:
        lines.append(Paragraph(_text(practice["credentials"]), RIGHT))
    contact = [practice["practice_address"], practice["practice_phone"], practice["practice_email"], practice["practice_website"]]
    lines += [Paragraph(_text(part), SMALL_RIGHT) for part in contact if part]
    mark = ""
    if logo:
        reader = ImageReader(io.BytesIO(logo))
        width, height = reader.getSize()
        scale = min(50 * mm / width, 28 * mm / height)
        mark = Image(io.BytesIO(logo), width=width * scale, height=height * scale)
    table = Table([[mark, lines]], colWidths=[PAGE_WIDTH * 0.4, PAGE_WIDTH * 0.6])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, -1), 0.6, LINE),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def _build(story: list, title: str, author: str) -> bytes:
    out = io.BytesIO()
    document = SimpleDocTemplate(
        out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=title, author=author,
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(REGULAR, 8)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def _plain_table(rows: list, widths: list) -> Table:
    table = Table(rows, colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    table.hAlign = "LEFT"
    return table


def invoice_pdf(practice: dict, logo: bytes | None, invoice: dict, receipt: bool = False) -> bytes:
    _register_fonts()
    totals = invoice["totals"]
    kind = "Receipt" if receipt else "Invoice"
    story = [_letterhead(practice, logo), Spacer(1, 10 * mm), Paragraph(kind, TITLE), Spacer(1, 5 * mm)]

    bill_to = [Paragraph("Bill to", LABEL), Paragraph(_text(invoice["bill_to_name"]), STRONG)]
    if invoice["bill_to_address"]:
        bill_to.append(Paragraph(_text(invoice["bill_to_address"]), BODY))
    facts = [("Invoice number", invoice["number"] or "Draft"), ("Invoice date", fmt.nice_date(invoice["issue_date"]))]
    if invoice["due_date"] and not receipt:
        facts.append(("Payment due", fmt.nice_date(invoice["due_date"])))
    if invoice["vat_bp"] and practice["vat_number"]:
        facts.append(("VAT number", practice["vat_number"]))
    fact_table = _plain_table(
        [[Paragraph(label, SMALL), Paragraph(_text(value), RIGHT)] for label, value in facts],
        [PAGE_WIDTH * 0.2, PAGE_WIDTH * 0.25],
    )
    story += [_plain_table([[bill_to, fact_table]], [PAGE_WIDTH * 0.55, PAGE_WIDTH * 0.45]), Spacer(1, 8 * mm)]

    rows = [[Paragraph(head, LABEL if i == 0 else SMALL_RIGHT) for i, head in enumerate(("Description", "Quantity", "Unit price", "Amount"))]]
    for line in invoice["lines"]:
        rows.append(
            [
                Paragraph(_text(line["description"]), BODY),
                Paragraph(fmt.plain(line["quantity"]), RIGHT),
                Paragraph(fmt.money(line["unit_pence"]), RIGHT),
                Paragraph(fmt.money(line["total_pence"]), RIGHT),
            ]
        )
    lines_table = Table(rows, colWidths=[PAGE_WIDTH * 0.52, PAGE_WIDTH * 0.14, PAGE_WIDTH * 0.17, PAGE_WIDTH * 0.17], repeatRows=1)
    lines_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
            ]
        )
    )
    lines_table.hAlign = "LEFT"
    story += [lines_table, Spacer(1, 4 * mm)]

    summary = []
    if invoice["vat_bp"]:
        summary.append(("Net", fmt.money(totals["net"]), BODY, RIGHT))
        summary.append((f"VAT at {fmt.plain(invoice['vat_bp'] / 100)}%", fmt.money(totals["vat"]), BODY, RIGHT))
    summary.append(("Total", fmt.money(totals["total"]), STRONG, STRONG_RIGHT))
    if totals["paid"] or receipt:
        summary.append(("Paid", fmt.money(totals["paid"]), BODY, RIGHT))
        label = "Balance due" if totals["balance"] > 0 else "Paid in full" if totals["balance"] == 0 else "Overpaid"
        summary.append((label, fmt.money(abs(totals["balance"])), STRONG, STRONG_RIGHT))
    summary_table = _plain_table(
        [["", Paragraph(label, left), Paragraph(value, right)] for label, value, left, right in summary],
        [PAGE_WIDTH * 0.55, PAGE_WIDTH * 0.25, PAGE_WIDTH * 0.2],
    )
    story += [summary_table, Spacer(1, 8 * mm)]

    if receipt and invoice["payments"]:
        story.append(Paragraph("Payments received", LABEL))
        for payment in invoice["payments"]:
            detail = " · ".join(part for part in (fmt.nice_date(payment["paid_on"]), payment["method"], payment["note"]) if part)
            story.append(_plain_table([[Paragraph(_text(detail), BODY), Paragraph(fmt.money(payment["amount_pence"]), RIGHT)]], [PAGE_WIDTH * 0.8, PAGE_WIDTH * 0.2]))
        story.append(Spacer(1, 6 * mm))
    if not receipt and practice["payment_details"] and totals["balance"] > 0:
        story += [Paragraph("How to pay", LABEL), Paragraph(_text(practice["payment_details"]), BODY), Spacer(1, 6 * mm)]
    if invoice["notes"]:
        story += [Paragraph(_text(invoice["notes"]), BODY), Spacer(1, 6 * mm)]
    if practice["invoice_footer"]:
        story.append(Paragraph(_text(practice["invoice_footer"]), SMALL))
    return _build(story, f"{kind} {invoice['number'] or ''}".strip(), practice["display_name"])


def letter_pdf(practice: dict, logo: bytes | None, letter: dict) -> bytes:
    _register_fonts()
    story = [_letterhead(practice, logo), Spacer(1, 10 * mm), Paragraph(fmt.nice_date(letter["letter_date"]), BODY), Spacer(1, 6 * mm)]
    recipient = "\n".join(part for part in (letter["recipient_name"], letter["recipient_address"]) if part)
    if recipient:
        story += [Paragraph(_text(recipient), BODY), Spacer(1, 8 * mm)]
    if letter["subject"]:
        story += [Paragraph(_text("Re: " + letter["subject"]), STRONG), Spacer(1, 5 * mm)]
    for paragraph in letter["body"].split("\n\n"):
        if paragraph.strip("\n"):
            story += [Paragraph(_text(paragraph.strip("\n")), BODY), Spacer(1, 4 * mm)]
    return _build(story, letter["subject"] or "Letter", practice["display_name"])

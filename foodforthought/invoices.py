from __future__ import annotations

import csv
import io

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import FormData

from . import clients, fmt, pdf, practice
from .audit import now_iso
from .db import Database
from .web import Redirect, Web, form_data, text

MAX_LINES = 40
MAX_PENCE = 100_000_000
PAYMENT_METHODS = ("Bank transfer", "Cash", "Card", "Cheque")
SERVICES = ("Initial consultation", "Follow-up consultation", "Review", "Meal plan")
FILTERS = (("open", "Unpaid"), ("draft", "Drafts"), ("all", "All"))


def line_total(quantity: str, unit_pence: int) -> int:
    return int((Decimal(quantity) * unit_pence).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def totals(lines: list, vat_bp: int, payments: list) -> dict:
    net = sum(line["total_pence"] for line in lines)
    vat = int((Decimal(net) * vat_bp / 10000).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    paid = sum(payment["amount_pence"] for payment in payments)
    return {"net": net, "vat": vat, "total": net + vat, "paid": paid, "balance": net + vat - paid}


def state(invoice: dict, today: date | None = None) -> tuple[str, str]:
    if invoice["status"] == "draft":
        return "draft", "Draft"
    if invoice["status"] == "void":
        return "void", "Void"
    figures = invoice["totals"]
    if figures["balance"] <= 0:
        return "paid", "Paid"
    due = fmt.parse_date(invoice["due_date"])
    if due and due < (today or date.today()):
        return "overdue", "Overdue"
    return ("part", "Part paid") if figures["paid"] else ("unpaid", "Unpaid")


def load(db: Database, where: str = "", params: tuple = ()) -> list[dict]:
    invoices = [dict(row) for row in db.query(f"SELECT * FROM invoice {where} ORDER BY issue_date DESC, id DESC", params)]
    by_id = {invoice["id"]: invoice for invoice in invoices}
    for invoice in invoices:
        invoice["lines"], invoice["payments"] = [], []
    if not invoices:
        return invoices
    marks = ", ".join("?" for _ in by_id)
    for row in db.query(f"SELECT * FROM invoice_line WHERE invoice_id IN ({marks}) ORDER BY position, id", tuple(by_id)):
        by_id[row["invoice_id"]]["lines"].append(dict(row, total_pence=line_total(row["quantity"], row["unit_pence"])))
    for row in db.query(f"SELECT * FROM payment WHERE invoice_id IN ({marks}) ORDER BY paid_on, id", tuple(by_id)):
        by_id[row["invoice_id"]]["payments"].append(dict(row))
    for invoice in invoices:
        invoice["totals"] = totals(invoice["lines"], invoice["vat_bp"], invoice["payments"])
        invoice["state"], invoice["state_label"] = state(invoice)
    return invoices


def get(db: Database, invoice_id: int) -> dict:
    found = load(db, "WHERE id = ?", (invoice_id,))
    if not found:
        raise HTTPException(404, "That invoice does not exist.")
    return found[0]


def tax_year_start(today: date) -> int:
    return today.year if today >= date(today.year, 4, 6) else today.year - 1


def tax_year_periods(start_year: int) -> list[tuple[str, date, date]]:
    periods = [("6 to 30 April", date(start_year, 4, 6), date(start_year, 4, 30))]
    for offset in range(1, 12):
        month = (3 + offset) % 12 + 1
        year = start_year + (1 if month < 4 else 0)
        first = date(year, month, 1)
        last = (date(year + (month == 12), month % 12 + 1, 1)) - timedelta(days=1)
        periods.append((f"{first:%B %Y}", first, last))
    periods.append(("1 to 5 April", date(start_year + 1, 4, 1), date(start_year + 1, 4, 5)))
    return periods


RETENTION_YEARS = 5


def keep_until(invoice: dict) -> date:
    dates = [fmt.parse_date(invoice["issue_date"]), *(fmt.parse_date(p["paid_on"]) for p in invoice["payments"])]
    latest = max(d for d in dates if d)
    return date(tax_year_start(latest) + 2 + RETENTION_YEARS, 1, 31)


def kept_invoices(db: Database, today: date | None = None) -> list[dict]:
    found = load(db, "WHERE erased_at IS NOT NULL")
    for invoice in found:
        invoice["keep_until"] = keep_until(invoice)
        invoice["can_delete"] = invoice["keep_until"] < (today or date.today())
    return sorted(found, key=lambda i: (i["keep_until"], i["issue_date"]))


def deletable_count(db: Database, today: date | None = None) -> int:
    return sum(1 for invoice in kept_invoices(db, today) if invoice["can_delete"])


def csv_text(rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    for row in rows:
        writer.writerow(["'" + c if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in row])
    return out.getvalue()


def pounds(pence: int) -> str:
    return f"{pence / 100:.2f}"


def summary(invoices: list[dict], start_year: int) -> dict:
    rows = []
    for label, first, last in tax_year_periods(start_year):
        row = {"label": label, "invoiced": 0, "vat": 0, "received": 0}
        for invoice in invoices:
            if invoice["status"] != "issued":
                continue
            if first.isoformat() <= invoice["issue_date"] <= last.isoformat():
                row["invoiced"] += invoice["totals"]["total"]
                row["vat"] += invoice["totals"]["vat"]
            row["received"] += sum(
                p["amount_pence"] for p in invoice["payments"] if first.isoformat() <= p["paid_on"] <= last.isoformat()
            )
        rows.append(row)
    return {
        "rows": rows,
        "invoiced": sum(r["invoiced"] for r in rows),
        "vat": sum(r["vat"] for r in rows),
        "received": sum(r["received"] for r in rows),
        "outstanding": sum(max(i["totals"]["balance"], 0) for i in invoices if i["status"] == "issued"),
    }


def read_lines(form: FormData) -> tuple[list[dict], list[str]]:
    count = text(form, "count")
    lines, errors = [], []
    for i in range(min(int(count) if count.isdigit() else 0, MAX_LINES)):
        description = text(form, f"l{i}_description", 500)
        raw_quantity = text(form, f"l{i}_quantity") or "1"
        raw_unit = text(form, f"l{i}_unit")
        quantity = fmt.parse_decimal(raw_quantity)
        unit = fmt.parse_money(raw_unit) if raw_unit else 0
        line = {"description": description, "quantity": raw_quantity, "unit_text": raw_unit, "unit_pence": unit or 0}
        if quantity is None or not 0 < quantity <= 10000:
            errors.append(f"Line {i + 1}: quantity must be a number above zero.")
        else:
            line["quantity"] = fmt.plain(quantity)
        if unit is None or not 0 <= unit <= MAX_PENCE:
            errors.append(f"Line {i + 1}: price must be an amount in pounds, such as 65 or 65.00.")
        lines.append(line)
    return lines, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def draft_page(request: Request, invoice: dict, error: str | None = None):
        return web.page(
            request,
            "invoice_draft.html",
            status_code=400 if error else 200,
            error=error,
            invoice=invoice,
            services=SERVICES,
            practice=practice.load(web.db),
        )

    @r.get("/invoices")
    def index(request: Request, show: str = "open"):
        show = show if show in dict(FILTERS) else "open"
        invoices = load(web.db)
        if show == "open":
            shown = [i for i in invoices if i["state"] in ("unpaid", "part", "overdue")]
        elif show == "draft":
            shown = [i for i in invoices if i["state"] == "draft"]
        else:
            shown = invoices
        return web.page(
            request,
            "invoices.html",
            invoices=shown,
            show=show,
            filters=FILTERS,
            clients=clients.active(web.db),
            outstanding=sum(max(i["totals"]["balance"], 0) for i in invoices if i["status"] == "issued"),
            kept_count=web.db.one("SELECT count(*) FROM invoice WHERE erased_at IS NOT NULL")[0],
        )

    @r.get("/invoices/summary")
    def income_summary(request: Request, year: str = ""):
        current = tax_year_start(date.today())
        start_year = int(year) if year.isdigit() and 2000 <= int(year) <= 2100 else current
        return web.page(
            request,
            "invoice_summary.html",
            summary=summary(load(web.db), start_year),
            start_year=start_year,
            years=list(range(current, current - 7, -1)),
            vat_registered=bool(practice.load(web.db)["vat_registered"]),
        )

    def year_bounds(year: str) -> tuple[int, str, str]:
        start_year = int(year) if year.isdigit() and 2000 <= int(year) <= 2100 else tax_year_start(date.today())
        return start_year, date(start_year, 4, 6).isoformat(), date(start_year + 1, 4, 5).isoformat()

    def csv_response(rows: list[list], filename: str, start_year: int) -> Response:
        web.log("income_exported", f"{filename} for tax year {start_year}")
        return Response(
            csv_text(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}-{start_year}-{start_year + 1 - 2000:02d}.csv"'},
        )

    @r.get("/invoices/summary/invoices.csv")
    def invoices_csv(year: str = ""):
        start_year, first, last = year_bounds(year)
        rows = [["Number", "Issue date", "Billed to", "Description", "Net", "VAT", "Total", "Status", "Paid so far"]]
        for invoice in sorted(load(web.db, "WHERE status != 'draft' AND issue_date BETWEEN ? AND ?", (first, last)), key=lambda i: (i["issue_date"], i["number"] or "")):
            figures = invoice["totals"]
            rows.append([
                invoice["number"], invoice["issue_date"], invoice["bill_to_name"], "; ".join(line["description"] for line in invoice["lines"]),
                pounds(figures["net"]), pounds(figures["vat"]), pounds(figures["total"]), invoice["state_label"], pounds(figures["paid"]),
            ])
        return csv_response(rows, "invoices", start_year)

    @r.get("/invoices/summary/payments.csv")
    def payments_csv(year: str = ""):
        start_year, first, last = year_bounds(year)
        rows = [["Date received", "Invoice", "Billed to", "Amount", "Method", "Note"]]
        found = web.db.query(
            "SELECT p.*, i.number, i.bill_to_name FROM payment p JOIN invoice i ON i.id = p.invoice_id "
            "WHERE p.paid_on BETWEEN ? AND ? ORDER BY p.paid_on, p.id",
            (first, last),
        )
        for row in found:
            rows.append([row["paid_on"], row["number"], row["bill_to_name"], pounds(row["amount_pence"]), row["method"], row["note"]])
        return csv_response(rows, "payments", start_year)

    @r.get("/invoices/kept")
    def kept(request: Request):
        return web.page(request, "invoices_kept.html", kept=kept_invoices(web.db), years=RETENTION_YEARS, section="invoices")

    @r.post("/invoices/kept/delete")
    def delete_kept(form: FormData = Depends(form_data)):
        chosen = {int(v) for v in form.getlist("invoice") if str(v).isdigit()}
        gone = [i for i in kept_invoices(web.db) if i["can_delete"] and (not chosen or i["id"] in chosen)]
        with web.db.transaction():
            for invoice in gone:
                web.db.execute("DELETE FROM invoice WHERE id = ?", (invoice["id"],))
        if gone:
            web.log("kept_invoices_deleted", f"{len(gone)} invoices past the retention date: " + ", ".join(i["number"] or "" for i in gone))
        raise Redirect(f"/invoices/kept?msg={'kept-deleted' if gone else 'saved'}")

    @r.post("/invoices/new")
    def create(form: FormData = Depends(form_data)):
        settings = practice.load(web.db)
        raw_client = text(form, "client_id")
        client = web.db.one("SELECT * FROM client WHERE id = ?", (int(raw_client),)) if raw_client.isdigit() else None
        today = date.today()
        now = now_iso()
        with web.db.transaction():
            invoice_id = web.db.insert(
                "INSERT INTO invoice(client_id, bill_to_name, bill_to_address, issue_date, due_date, vat_bp, "
                "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    client["id"] if client else None,
                    clients.full_name(client) if client else "",
                    client["address"] if client else "",
                    today.isoformat(),
                    (today + timedelta(days=int(settings["payment_terms_days"]))).isoformat(),
                    practice.vat_bp(settings),
                    now,
                    now,
                ),
            )
            web.db.execute("INSERT INTO invoice_line(invoice_id, position) VALUES(?, 0)", (invoice_id,))
            web.log("invoice_drafted", f"invoice draft {invoice_id}", client["id"] if client else None)
        raise Redirect(f"/invoices/{invoice_id}")

    @r.get("/invoices/{invoice_id}")
    def show_invoice(request: Request, invoice_id: int):
        invoice = get(web.db, invoice_id)
        if invoice["status"] == "draft":
            for line in invoice["lines"]:
                line["unit_text"] = f"{line['unit_pence'] / 100:.2f}" if line["unit_pence"] else ""
            return draft_page(request, invoice)
        return web.page(
            request,
            "invoice.html",
            invoice=invoice,
            methods=PAYMENT_METHODS,
            balance_text=f"{max(invoice['totals']['balance'], 0) / 100:.2f}",
        )

    @r.post("/invoices/{invoice_id}")
    def save_draft(request: Request, invoice_id: int, form: FormData = Depends(form_data)):
        invoice = get(web.db, invoice_id)
        if invoice["status"] != "draft":
            raise HTTPException(409, "This invoice has been issued and can no longer be edited.")
        action = text(form, "action") or "save"
        lines, errors = read_lines(form)
        verb, _, raw_index = action.partition(":")
        if verb == "add" and len(lines) < MAX_LINES:
            lines.append({"description": "", "quantity": "1", "unit_text": "", "unit_pence": 0})
        elif verb == "remove" and raw_index.isdigit() and int(raw_index) < len(lines):
            del lines[int(raw_index)]
        invoice.update(
            bill_to_name=text(form, "bill_to_name", 200),
            bill_to_address=text(form, "bill_to_address", 1000),
            issue_date=fmt.iso(text(form, "issue_date")),
            due_date=fmt.iso(text(form, "due_date")),
            notes=text(form, "notes", 2000),
            lines=lines,
        )
        if fmt.parse_date(invoice["issue_date"]) is None:
            errors.append("Give a valid invoice date.")
        if invoice["due_date"] and fmt.parse_date(invoice["due_date"]) is None:
            errors.append("Give a valid due date, or leave it empty.")
        settings = practice.load(web.db)
        if verb == "issue" and not errors:
            if not settings["display_name"]:
                errors.append("Add your practice details in Settings before issuing an invoice.")
            if not invoice["bill_to_name"]:
                errors.append("Say who the invoice is for.")
            if not any(line["description"] and line["unit_pence"] for line in lines):
                errors.append("Add at least one line with a description and a price.")
            if any(not line["description"] for line in lines):
                errors.append("Give every line a description, or remove the empty lines.")
        if errors:
            return draft_page(request, invoice, " ".join(errors))
        with web.db.transaction():
            web.db.execute(
                "UPDATE invoice SET bill_to_name = ?, bill_to_address = ?, issue_date = ?, due_date = ?, notes = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    invoice["bill_to_name"],
                    invoice["bill_to_address"],
                    invoice["issue_date"],
                    invoice["due_date"],
                    invoice["notes"],
                    now_iso(),
                    invoice_id,
                ),
            )
            web.db.execute("DELETE FROM invoice_line WHERE invoice_id = ?", (invoice_id,))
            for position, line in enumerate(lines):
                web.db.execute(
                    "INSERT INTO invoice_line(invoice_id, position, description, quantity, unit_pence) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (invoice_id, position, line["description"], line["quantity"], line["unit_pence"]),
                )
            if verb == "issue":
                number = int(web.db.get_setting("invoice_next", "1"))
                while web.db.one("SELECT 1 FROM invoice WHERE number = ?", (f"{settings['invoice_prefix']}{number:04d}",)):
                    number += 1
                web.db.execute(
                    "UPDATE invoice SET number = ?, status = 'issued' WHERE id = ?",
                    (f"{settings['invoice_prefix']}{number:04d}", invoice_id),
                )
                web.db.set_setting("invoice_next", str(number + 1))
                web.log("invoice_issued", f"invoice {settings['invoice_prefix']}{number:04d}", invoice["client_id"])
        if verb == "issue":
            raise Redirect(f"/invoices/{invoice_id}?msg=issued")
        raise Redirect(f"/invoices/{invoice_id}" + ("?msg=saved" if verb == "save" else "#lines"))

    @r.post("/invoices/{invoice_id}/delete")
    def delete_draft(invoice_id: int):
        invoice = get(web.db, invoice_id)
        if invoice["status"] != "draft":
            raise HTTPException(409, "An issued invoice cannot be deleted. Void it instead.")
        web.db.execute("DELETE FROM invoice WHERE id = ?", (invoice_id,))
        web.log("invoice_draft_deleted", f"invoice draft {invoice_id}", invoice["client_id"])
        raise Redirect("/invoices?show=draft&msg=deleted")

    @r.post("/invoices/{invoice_id}/void")
    def void(invoice_id: int):
        invoice = get(web.db, invoice_id)
        if invoice["status"] != "issued":
            raise HTTPException(409, "Only an issued invoice can be voided.")
        if invoice["payments"]:
            raise Redirect(f"/invoices/{invoice_id}?err=has-payments")
        web.db.execute("UPDATE invoice SET status = 'void', updated_at = ? WHERE id = ?", (now_iso(), invoice_id))
        web.log("invoice_voided", f"invoice {invoice['number']}", invoice["client_id"])
        raise Redirect(f"/invoices/{invoice_id}?msg=voided")

    @r.post("/invoices/{invoice_id}/payments")
    def add_payment(invoice_id: int, form: FormData = Depends(form_data)):
        invoice = get(web.db, invoice_id)
        if invoice["status"] != "issued":
            raise HTTPException(409, "Payments can only be recorded on an issued invoice.")
        paid_on = fmt.parse_date(text(form, "paid_on"))
        amount = fmt.parse_money(text(form, "amount"))
        if paid_on is None or paid_on > date.today() or amount is None or not 0 < amount <= MAX_PENCE:
            raise Redirect(f"/invoices/{invoice_id}?err=payment")
        payment_id = web.db.insert(
            "INSERT INTO payment(invoice_id, paid_on, amount_pence, method, note, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (invoice_id, paid_on.isoformat(), amount, text(form, "method", 100), text(form, "note", 500), now_iso()),
        )
        web.log("payment_recorded", f"payment {payment_id} on invoice {invoice['number']}", invoice["client_id"])
        raise Redirect(f"/invoices/{invoice_id}?msg=paid")

    @r.post("/invoices/{invoice_id}/payments/{payment_id}/delete")
    def delete_payment(invoice_id: int, payment_id: int):
        invoice = get(web.db, invoice_id)
        web.db.execute("DELETE FROM payment WHERE id = ? AND invoice_id = ?", (payment_id, invoice_id))
        web.log("payment_deleted", f"payment {payment_id} on invoice {invoice['number']}", invoice["client_id"])
        raise Redirect(f"/invoices/{invoice_id}?msg=deleted")

    def pdf_response(invoice_id: int, receipt: bool) -> Response:
        invoice = get(web.db, invoice_id)
        if invoice["status"] == "draft":
            raise HTTPException(409, "Issue the invoice before downloading it.")
        if receipt and not invoice["payments"]:
            raise HTTPException(409, "There are no payments on this invoice yet.")
        data = pdf.invoice_pdf(practice.load(web.db), practice.logo_bytes(web), invoice, receipt=receipt)
        kind = "receipt" if receipt else "invoice"
        web.log(f"{kind}_pdf_downloaded", f"invoice {invoice['number']}", invoice["client_id"])
        filename = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in f"{kind}-{invoice['number']}")
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
        )

    @r.get("/invoices/{invoice_id}/invoice.pdf")
    def invoice_file(invoice_id: int):
        return pdf_response(invoice_id, receipt=False)

    @r.get("/invoices/{invoice_id}/receipt.pdf")
    def receipt_file(invoice_id: int):
        return pdf_response(invoice_id, receipt=True)

    @r.get("/clients/{client_id}/invoices")
    def client_tab(request: Request, client_id: int):
        return web.page(
            request,
            "client_invoices.html",
            invoices=load(web.db, "WHERE client_id = ?", (client_id,)),
            **clients.context(web, client_id, "invoices"),
        )

    return r

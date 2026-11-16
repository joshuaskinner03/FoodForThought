import io
from datetime import date, timedelta

import pypdf
import pytest
from PIL import Image

from foodforthought import fmt
from foodforthought.invoices import line_total, summary, tax_year_periods, tax_year_start, totals

from .conftest import core_of
from .test_clients import add_client, jane  # noqa: F401

PRACTICE = {
    "practice_name": "Greenleaf Nutrition",
    "practitioner_name": "Sam Carter",
    "credentials": "Registered Nutritionist",
    "practice_address": "1 High Street\nLeeds",
    "payment_details": "Sort code 00-00-00, account 00000000",
    "invoice_prefix": "INV-",
    "invoice_next": "1",
    "payment_terms_days": "14",
    "vat_rate_percent": "20",
}


def db(client):
    return core_of(client).db


def pdf_text(data: bytes) -> str:
    assert data.startswith(b"%PDF")
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)


@pytest.fixture
def practice(unlocked):
    assert unlocked.post("/settings/practice", data=PRACTICE).status_code == 303
    return unlocked


def draft(client, client_id=None) -> int:
    done = client.post("/invoices/new", data={"client_id": str(client_id or "")})
    return int(done.headers["location"].rsplit("/", 1)[1])


def issue(client, invoice_id, lines=(("Initial consultation", "1", "65"),), **fields):
    data = {"count": str(len(lines)), "issue_date": "10/11/2026", "due_date": "24/11/2026", "action": "issue", **fields}
    for i, (description, quantity, unit) in enumerate(lines):
        data.update({f"l{i}_description": description, f"l{i}_quantity": quantity, f"l{i}_unit": unit})
    return client.post(f"/invoices/{invoice_id}", data=data)


def test_money_helpers():
    assert fmt.money(123456) == "£1,234.56" and fmt.money(5) == "£0.05" and fmt.money(-250) == "-£2.50"
    assert fmt.parse_money("65") == 6500 and fmt.parse_money("£1,065.50") == 106550 and fmt.parse_money("0.005") == 1
    assert fmt.parse_money("abc") is None and fmt.parse_money("") is None and fmt.parse_money("NaN") is None
    assert line_total("1.5", 6500) == 9750 and line_total("3", 3333) == 9999 and line_total("0.333", 100) == 33


def test_totals_and_vat_rounding():
    lines = [{"total_pence": 6500}, {"total_pence": 3333}]
    assert totals(lines, 0, []) == {"net": 9833, "vat": 0, "total": 9833, "paid": 0, "balance": 9833}
    with_vat = totals(lines, 2000, [{"amount_pence": 5000}])
    assert with_vat == {"net": 9833, "vat": 1967, "total": 11800, "paid": 5000, "balance": 6800}


def test_tax_year_periods():
    assert tax_year_start(date(2026, 4, 5)) == 2025 and tax_year_start(date(2026, 4, 6)) == 2026
    periods = tax_year_periods(2026)
    assert len(periods) == 13
    assert periods[0][1:] == (date(2026, 4, 6), date(2026, 4, 30))
    assert periods[-1][1:] == (date(2027, 4, 1), date(2027, 4, 5))
    assert periods[9][1:] == (date(2027, 1, 1), date(2027, 1, 31)) and periods[11][2] == date(2027, 3, 31)
    for (_, _, last), (_, first, _) in zip(periods, periods[1:]):
        assert first == last + timedelta(days=1)


def test_draft_is_prefilled_from_client_and_settings(practice, jane):
    practice.post(f"/clients/{jane}/edit", data={"first_name": "Jane", "last_name": "Smith", "address": "2 Mill Lane\nYork"})
    invoice_id = draft(practice, jane)
    row = db(practice).one("SELECT * FROM invoice WHERE id = ?", (invoice_id,))
    assert row["bill_to_name"] == "Jane Smith" and row["bill_to_address"] == "2 Mill Lane\nYork"
    assert row["status"] == "draft" and row["number"] is None and row["vat_bp"] == 0
    assert row["due_date"] == (date.today() + timedelta(days=14)).isoformat()
    assert "Draft invoice" in practice.get(f"/invoices/{invoice_id}").text
    assert practice.get(f"/invoices/{invoice_id}/invoice.pdf").status_code == 409


def test_draft_lines_add_remove_save(practice, jane):
    invoice_id = draft(practice, jane)
    base = {"count": "1", "issue_date": "10/11/2026", "l0_description": "Follow-up", "l0_quantity": "2", "l0_unit": "45.50"}
    practice.post(f"/invoices/{invoice_id}", data={**base, "action": "add"})
    assert db(practice).one("SELECT count(*) FROM invoice_line")[0] == 2
    two = {**base, "count": "2", "l1_description": "Meal plan", "l1_quantity": "1", "l1_unit": "30"}
    practice.post(f"/invoices/{invoice_id}", data={**two, "action": "save"})
    page = practice.get(f"/invoices/{invoice_id}").text
    assert "£121.00" in page and 'value="45.50"' in page
    practice.post(f"/invoices/{invoice_id}", data={**two, "action": "remove:0"})
    lines = db(practice).query("SELECT * FROM invoice_line")
    assert len(lines) == 1 and lines[0]["description"] == "Meal plan"
    bad = practice.post(f"/invoices/{invoice_id}", data={**base, "l0_unit": "lots", "action": "save"})
    assert bad.status_code == 400 and 'value="lots"' in bad.text
    assert db(practice).query("SELECT * FROM invoice_line")[0]["description"] == "Meal plan"


def test_issue_checks_and_numbering(unlocked, jane):
    invoice_id = draft(unlocked, jane)
    no_practice = issue(unlocked, invoice_id)
    assert no_practice.status_code == 400 and "practice details" in no_practice.text
    unlocked.post("/settings/practice", data=PRACTICE)
    assert issue(unlocked, invoice_id, lines=()).status_code == 400
    assert issue(unlocked, invoice_id, lines=(("", "1", "65"),)).status_code == 400
    assert issue(unlocked, invoice_id, bill_to_name="").status_code == 400
    assert db(unlocked).one("SELECT status FROM invoice")["status"] == "draft"

    assert issue(unlocked, invoice_id, bill_to_name="Jane Smith").status_code == 303
    second = draft(unlocked, jane)
    issue(unlocked, second, bill_to_name="Jane Smith")
    numbers = [row["number"] for row in db(unlocked).query("SELECT number FROM invoice ORDER BY id")]
    assert numbers == ["INV-0001", "INV-0002"] and db(unlocked).get_setting("invoice_next") == "3"
    assert issue(unlocked, invoice_id, bill_to_name="Changed").status_code == 409
    assert unlocked.post(f"/invoices/{invoice_id}/delete").status_code == 409
    assert db(unlocked).one("SELECT bill_to_name FROM invoice WHERE id = ?", (invoice_id,))["bill_to_name"] == "Jane Smith"


def test_deleted_drafts_leave_no_gap_in_numbers(practice, jane):
    first = draft(practice, jane)
    practice.post(f"/invoices/{first}/delete")
    second = draft(practice, jane)
    issue(practice, second, bill_to_name="Jane Smith")
    assert db(practice).one("SELECT number FROM invoice")["number"] == "INV-0001"


def test_invoice_pdf_payment_and_receipt(practice, jane):
    invoice_id = draft(practice, jane)
    issue(practice, invoice_id, lines=(("Initial consultation", "1", "65"), ("Follow-up & review", "2", "45")), bill_to_name="Zoë Łukasz")
    text = pdf_text(practice.get(f"/invoices/{invoice_id}/invoice.pdf").content)
    for expected in ("Invoice", "INV-0001", "Greenleaf Nutrition", "Sam Carter", "Zoë Łukasz", "Follow-up & review", "£155.00", "10 Nov 2026", "24 Nov 2026", "Sort code 00-00-00"):
        assert expected in text, expected
    assert "VAT" not in text
    assert practice.get(f"/invoices/{invoice_id}/receipt.pdf").status_code == 409

    url = f"/invoices/{invoice_id}/payments"
    for data in ({"paid_on": "bad", "amount": "10"}, {"paid_on": "11/11/2026", "amount": "0"}, {"paid_on": "01/01/2999", "amount": "10"}):
        assert practice.post(url, data=data).headers["location"].endswith("err=payment")
    practice.post(url, data={"paid_on": "11/11/2026", "amount": "100", "method": "Bank transfer"})
    assert "Part paid" in practice.get(f"/invoices/{invoice_id}").text or "Overdue" in practice.get(f"/invoices/{invoice_id}").text
    practice.post(url, data={"paid_on": "12/11/2026", "amount": "55.00", "method": "Cash"})
    page = practice.get(f"/invoices/{invoice_id}").text
    assert "Paid" in page and "Record a payment" not in page
    receipt = pdf_text(practice.get(f"/invoices/{invoice_id}/receipt.pdf").content)
    for expected in ("Receipt", "INV-0001", "Paid in full", "Bank transfer", "£100.00", "£55.00", "12 Nov 2026"):
        assert expected in receipt, expected
    assert "Sort code" not in receipt

    assert practice.post(f"/invoices/{invoice_id}/void").headers["location"].endswith("err=has-payments")
    payment_id = db(practice).one("SELECT id FROM payment")["id"]
    practice.post(f"/invoices/{invoice_id}/payments/{payment_id}/delete")
    assert db(practice).one("SELECT count(*) FROM payment")[0] == 1


def test_vat_invoice(unlocked, jane):
    missing_number = unlocked.post("/settings/practice", data={**PRACTICE, "vat_registered": "on"})
    assert missing_number.status_code == 400
    unlocked.post("/settings/practice", data={**PRACTICE, "vat_registered": "on", "vat_number": "GB123456789"})
    invoice_id = draft(unlocked, jane)
    issue(unlocked, invoice_id, lines=(("Consultation", "1", "98.33"),), bill_to_name="Jane Smith")
    text = pdf_text(unlocked.get(f"/invoices/{invoice_id}/invoice.pdf").content)
    for expected in ("GB123456789", "VAT at 20%", "£98.33", "£19.67", "£118.00"):
        assert expected in text, expected
    unlocked.post("/settings/practice", data=PRACTICE)
    assert "£118.00" in unlocked.get(f"/invoices/{invoice_id}").text


def test_void_and_lists(practice, jane):
    paid, open_one, voided, drafted = (draft(practice, jane) for _ in range(4))
    for invoice_id in (paid, open_one, voided):
        issue(practice, invoice_id, bill_to_name="Jane Smith")
    practice.post(f"/invoices/{paid}/payments", data={"paid_on": "11/11/2026", "amount": "65"})
    assert practice.post(f"/invoices/{voided}/void").status_code == 303
    assert practice.post(f"/invoices/{voided}/payments", data={"paid_on": "11/11/2026", "amount": "65"}).status_code == 409
    unpaid = practice.get("/invoices").text
    assert "INV-0002" in unpaid and "INV-0001" not in unpaid and "INV-0003" not in unpaid
    assert "Outstanding: <strong>£65.00</strong>" in unpaid
    assert "Draft" in practice.get("/invoices?show=draft").text
    everything = practice.get("/invoices?show=all").text
    assert all(number in everything for number in ("INV-0001", "INV-0002", "INV-0003"))
    tab = practice.get(f"/clients/{jane}/invoices").text
    assert "INV-0001" in tab and "Void" in tab
    assert practice.get("/invoices/9999").status_code == 404


def test_income_summary():
    def invoice(issue_date, total, payments=(), status="issued", vat=0):
        return {
            "status": status,
            "issue_date": issue_date,
            "totals": {"total": total, "vat": vat, "balance": total - sum(a for _, a in payments)},
            "payments": [{"paid_on": d, "amount_pence": a} for d, a in payments],
        }

    invoices = [
        invoice("2026-04-05", 1000, [("2026-04-06", 1000)]),
        invoice("2026-04-06", 2000, [("2026-05-01", 500)]),
        invoice("2027-04-05", 4000),
        invoice("2027-04-06", 8000),
        invoice("2026-06-10", 16000, status="void"),
        invoice("2026-06-11", 32000, status="draft"),
    ]
    result = summary(invoices, 2026)
    assert result["invoiced"] == 6000 and result["received"] == 1500
    assert result["rows"][0] == {"label": "6 to 30 April", "invoiced": 2000, "vat": 0, "received": 1000}
    assert result["rows"][1]["received"] == 500 and result["rows"][-1]["invoiced"] == 4000
    assert result["outstanding"] == 1500 + 4000 + 8000


def test_income_summary_page(practice, jane):
    invoice_id = draft(practice, jane)
    issue(practice, invoice_id, bill_to_name="Jane Smith")
    practice.post(f"/invoices/{invoice_id}/payments", data={"paid_on": "11/11/2026", "amount": "40"})
    page = practice.get("/invoices/summary?year=2026").text
    assert "6 April 2026 to 5 April 2027" in page and "November 2026" in page
    assert "£65.00" in page and "£40.00" in page and "£25.00" in page and "Of which VAT" not in page
    assert practice.get("/invoices/summary?year=banana").status_code == 200


def test_logo_upload_is_cleaned_and_used(practice, jane):
    image = Image.new("RGBA", (1600, 400), (30, 110, 80, 128))
    raw = io.BytesIO()
    image.save(raw, "PNG")
    assert practice.post("/settings/practice/logo", files={"file": ("logo.png", raw.getvalue(), "image/png")}).headers["location"].endswith("msg=logo-saved")
    served = practice.get("/settings/practice/logo")
    assert served.headers["content-type"] == "image/png"
    cleaned = Image.open(io.BytesIO(served.content))
    assert cleaned.mode == "RGB" and max(cleaned.size) == 800
    invoice_id = draft(practice, jane)
    issue(practice, invoice_id, bill_to_name="Jane Smith")
    reader = pypdf.PdfReader(io.BytesIO(practice.get(f"/invoices/{invoice_id}/invoice.pdf").content))
    assert len(reader.pages[0].images) == 1
    for bad in (b"not an image", b"<svg xmlns='http://www.w3.org/2000/svg'/>", b""):
        assert practice.post("/settings/practice/logo", files={"file": ("x.png", bad, "image/png")}).headers["location"].endswith("err=logo")
    practice.post("/settings/practice/logo/delete")
    assert practice.get("/settings/practice/logo").status_code == 404


def test_practice_settings_validation(unlocked):
    for change in ({"payment_terms_days": "soon"}, {"invoice_next": "0"}, {"vat_rate_percent": "lots"}):
        assert unlocked.post("/settings/practice", data={**PRACTICE, **change}).status_code == 400
    assert db(unlocked).get_setting("practice_name") is None

import io
import json
import zipfile
from datetime import date

from foodforthought.letters import fill

from .conftest import PASSWORD, core_of
from .test_clients import add_client, jane  # noqa: F401
from .test_invoices import PRACTICE, draft, issue, pdf_text


def db(client):
    return core_of(client).db


def test_fill_replaces_known_placeholders_only():
    values = {"client name": "Jane Smith", "today": "16 Nov 2026"}
    text = "Re [Client name] on [TODAY]. [Summarise here.] [ client name ] [[x]]"
    assert fill(text, values) == "Re Jane Smith on 16 Nov 2026. [Summarise here.] Jane Smith [[x]]"


def test_gp_letter_template_is_seeded_and_editable(unlocked):
    assert "Letter to GP" in unlocked.get("/library").text
    template_id = db(unlocked).one("SELECT id FROM letter_template")["id"]
    assert "[Client name]" in unlocked.get(f"/library/letter-templates/{template_id}").text
    assert unlocked.post("/library/letter-templates/new", data={"name": ""}).status_code == 400
    unlocked.post("/library/letter-templates/new", data={"name": "Discharge", "recipient": "client", "subject": "Finishing up", "body": "Dear [Client first name],"})
    unlocked.post(f"/library/letter-templates/{template_id}/archive")
    assert db(unlocked).one("SELECT archived FROM letter_template WHERE id = ?", (template_id,))["archived"] == 1


def test_letter_from_template_to_pdf(unlocked, jane):
    unlocked.post("/settings/practice", data=PRACTICE)
    unlocked.post(f"/clients/{jane}/edit", data={"first_name": "Jane", "last_name": "Smith", "date_of_birth": "17/05/1990", "gp_name": "Dr Patel", "gp_address": "The Surgery\nLeeds"})
    template_id = db(unlocked).one("SELECT id FROM letter_template")["id"]
    assert "Letter to GP" in unlocked.get(f"/clients/{jane}/letters").text
    page = unlocked.get(f"/clients/{jane}/letters/new?template={template_id}").text
    assert "Dear Dr Patel," in page and "Jane Smith, date of birth 17 May 1990" in page
    assert "Sam Carter" in page and "Greenleaf Nutrition" in page and "[Summarise" in page
    assert 'value="Dr Patel"' in page and "The Surgery" in page
    assert "No consent for sharing information with the GP" in page
    unlocked.post(f"/clients/{jane}/consents", data={"kind": "Sharing information with GP"})
    assert "No consent for sharing" not in unlocked.get(f"/clients/{jane}/letters/new?template={template_id}").text

    url = f"/clients/{jane}/letters"
    assert unlocked.post(f"{url}/new", data={"letter_date": "16/11/2026", "body": ""}).status_code == 400
    unlocked.post(
        f"{url}/new",
        data={"letter_date": "16/11/2026", "recipient_name": "Dr Patel", "recipient_address": "The Surgery\nLeeds", "subject": "Jane Smith", "body": "Dear Dr Patel,\r\n\r\nFirst paragraph <ok> & fine.\r\n\r\nYours sincerely,\r\nSam Carter"},
    )
    letter_id = db(unlocked).one("SELECT id FROM letter")["id"]
    response = unlocked.get(f"{url}/{letter_id}/letter.pdf")
    assert response.headers["content-type"] == "application/pdf"
    text = pdf_text(response.content)
    for expected in ("Greenleaf Nutrition", "16 Nov 2026", "Dr Patel", "The Surgery", "Re: Jane Smith", "First paragraph <ok> & fine.", "Yours sincerely,"):
        assert expected in text, expected
    unlocked.post(f"{url}/{letter_id}/edit", data={"letter_date": "17/11/2026", "body": "Changed"})
    assert db(unlocked).one("SELECT body, letter_date FROM letter")["letter_date"] == "2026-11-17"
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    assert unlocked.get(f"/clients/{other}/letters/{letter_id}/letter.pdf").status_code == 404
    unlocked.post(f"{url}/{letter_id}/delete")
    assert db(unlocked).one("SELECT count(*) FROM letter")[0] == 0


def fill_record(client, client_id):
    client.post("/settings/practice", data=PRACTICE)
    client.post(f"/clients/{client_id}/consents", data={"kind": "Contact by email"})
    client.post(f"/clients/{client_id}/attachments", files={"file": ("diary scan.txt", b"breakfast: porridge", "text/plain")})
    client.post("/diary/new", data={"client_id": str(client_id), "title": "Review", "date": "10/11/2026", "start_time": "09:00", "duration_minutes": "30"})
    client.post(f"/clients/{client_id}/notes/new", data={"note_date": "10/11/2026", "title": "Review", "body": "Sleeping better."})
    form_id = db(client).one("SELECT id FROM form_definition")["id"]
    client.post(f"/clients/{client_id}/forms/new?form={form_id}", data={"completed_on": "10/11/2026", "a_q2": "More energy", "a_q9": ["Vegetarian"]})
    client.post(f"/clients/{client_id}/measurements/new", data={"measured_on": "10/11/2026", "weight_kg": "70", "height_cm": "170"})
    client.post(f"/clients/{client_id}/letters/new", data={"letter_date": "10/11/2026", "recipient_name": "Dr Patel", "body": "Dear Dr Patel"})
    issued = draft(client, client_id)
    issue(client, issued, bill_to_name="Jane Smith")
    client.post(f"/invoices/{issued}/payments", data={"paid_on": "11/11/2026", "amount": "65"})
    return issued, draft(client, client_id)


def test_export_contains_the_whole_record(unlocked, jane):
    fill_record(unlocked, jane)
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    unlocked.post(f"/clients/{other}/notes/new", data={"note_date": "10/11/2026", "body": "Someone else's private note"})
    page = unlocked.get(f"/clients/{jane}/data").text
    assert "not encrypted" in page and "notes: 1" in page and "files: 1" in page

    response = unlocked.post(f"/clients/{jane}/export")
    assert response.headers["content-type"] == "application/zip"
    assert f"Jane-Smith-record-{date.today().isoformat()}.zip" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert {"record.html", "record.json"} <= set(names)
    attached = [name for name in names if name.startswith("files/")]
    assert len(attached) == 1 and archive.read(attached[0]) == b"breakfast: porridge"

    record = json.loads(archive.read("record.json"))
    assert record["client"]["first_name"] == "Jane" and record["client"]["allergies"] == "Peanuts"
    assert [len(record[key]) for key in ("consents", "appointments", "notes", "forms", "measurements", "letters", "files")] == [1] * 7
    assert sorted(i["totals"]["total"] for i in record["invoices"]) == [0, 6500]
    assert {"question": "What would you like help with?", "answer": "More energy"} in record["forms"][0]["answers"]
    assert record["measurements"][0]["bmi"] == 24.2
    assert "file_id" not in json.dumps(record) and "Someone else" not in json.dumps(record)
    assert any(item["action"] == "note_created" for item in record["activity"])

    html = archive.read("record.html").decode()
    for expected in ("Record for Jane Smith", "Peanuts", "Sleeping better.", "More energy", "Vegetarian", "INV-0001", "£65.00", "diary scan.txt", "Dear Dr Patel", "24.2"):
        assert expected in html, expected
    assert "Someone else" not in html and "Khan" not in html
    assert db(unlocked).one("SELECT count(*) FROM audit_log WHERE action = 'client_exported' AND client_id = ?", (jane,))[0] == 1


def test_erase_needs_the_name_typed(unlocked, jane):
    for typed in ("", "Jane", "DELETE", "Omar Khan"):
        done = unlocked.post(f"/clients/{jane}/erase", data={"confirm": typed})
        assert done.headers["location"].endswith("err=erase-name")
    assert db(unlocked).one("SELECT count(*) FROM client")[0] == 1


def test_erase_removes_everything_but_issued_invoices(unlocked, jane, cfg):
    issued, drafted = fill_record(unlocked, jane)
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    unlocked.post(f"/clients/{other}/notes/new", data={"note_date": "10/11/2026", "body": "Keep me"})
    assert len(list(cfg.attachments_dir.glob("*.bin"))) == 1

    done = unlocked.post(f"/clients/{jane}/erase", data={"confirm": "  jane   SMITH "})
    assert done.headers["location"] == "/clients?msg=erased"
    database = db(unlocked)
    for table in ("consent", "attachment", "appointment", "form_response", "measurement", "letter"):
        assert database.one(f"SELECT count(*) FROM {table}")[0] == 0, table
    assert [row["body"] for row in database.query("SELECT body FROM note")] == ["Keep me"]
    assert [row["first_name"] for row in database.query("SELECT first_name FROM client")] == ["Omar"]
    assert not list(cfg.attachments_dir.glob("*.bin"))

    invoices = database.query("SELECT * FROM invoice")
    assert len(invoices) == 1 and invoices[0]["id"] == issued and invoices[0]["client_id"] is None
    assert invoices[0]["bill_to_name"] == "Jane Smith" and invoices[0]["number"] == "INV-0001"
    assert database.one("SELECT count(*) FROM payment")[0] == 1 and database.one("SELECT count(*) FROM invoice_line")[0] == 1
    assert "£65.00" in pdf_text(unlocked.get(f"/invoices/{issued}/invoice.pdf").content)
    assert "£65.00" in unlocked.get("/invoices/summary?year=2026").text

    assert unlocked.get(f"/clients/{jane}").status_code == 404
    log = database.query("SELECT action, detail FROM audit_log WHERE client_id = ?", (jane,))
    assert log[-1]["action"] == "client_erased" and "1 issued invoices kept" in log[-1]["detail"]
    assert all("Jane" not in row["detail"] and "Smith" not in row["detail"] for row in log)
    audit_page = unlocked.get("/audit").text
    assert f"Client {jane} (erased)" in audit_page and "Jane Smith" not in audit_page
    assert "Jane" not in unlocked.get("/clients?archived=1").text and "Jane" not in unlocked.get("/clients").text


def test_erased_data_is_gone_from_a_new_backup(unlocked, jane):
    from .test_backup import restore

    fill_record(unlocked, jane)
    unlocked.post(f"/clients/{jane}/erase", data={"confirm": "Jane Smith"})
    data = unlocked.post("/backup").content
    assert restore(unlocked, data).status_code == 303
    unlocked.post("/unlock", data={"password": PASSWORD})
    assert db(unlocked).one("SELECT count(*) FROM client")[0] == 0
    assert db(unlocked).one("SELECT count(*) FROM note")[0] == 0

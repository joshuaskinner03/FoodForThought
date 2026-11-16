import pytest

from .conftest import PASSWORD, core_of

JANE = {
    "first_name": "Jane",
    "last_name": "Smith",
    "date_of_birth": "1990-05-17",
    "sex": "female",
    "email": "jane@example.test",
    "allergies": "Peanuts",
    "goals": "More energy",
}


def add_client(client, **overrides) -> int:
    response = client.post("/clients/new", data={**JANE, **overrides})
    assert response.status_code == 303, response.text
    return int(response.headers["location"].split("/")[2].split("?")[0])


@pytest.fixture
def jane(unlocked):
    return add_client(unlocked)


def actions(client):
    return [row["action"] for row in core_of(client).db.query("SELECT action FROM audit_log ORDER BY id")]


def test_create_and_view_client(unlocked, jane):
    page = unlocked.get(f"/clients/{jane}")
    assert page.status_code == 200
    assert "Jane Smith" in page.text and "Peanuts" in page.text and "More energy" in page.text
    assert "17 May 1990" in page.text
    assert "client_created" in actions(unlocked)


def test_client_needs_a_name_and_valid_birth_date(unlocked):
    assert unlocked.post("/clients/new", data={"first_name": "Jane"}).status_code == 400
    future = unlocked.post("/clients/new", data={**JANE, "date_of_birth": "2999-01-01"})
    assert future.status_code == 400 and "Jane" in future.text
    assert core_of(unlocked).db.one("SELECT count(*) FROM client")[0] == 0


def test_edit_client_logs_changed_fields_not_values(unlocked, jane):
    assert unlocked.post(f"/clients/{jane}/edit", data={**JANE, "phone": "07700 900123"}).status_code == 303
    assert "07700 900123" in unlocked.get(f"/clients/{jane}").text
    row = core_of(unlocked).db.one("SELECT * FROM audit_log WHERE action = 'client_updated'")
    assert row["detail"] == "phone" and row["client_id"] == jane


def test_search_and_archive(unlocked, jane):
    other = add_client(unlocked, first_name="Omar", last_name="Khan", email="omar@example.test")
    assert "Smith" in unlocked.get("/clients?q=jan").text
    assert "Khan" not in unlocked.get("/clients?q=jan").text
    assert "Khan" in unlocked.get("/clients?q=omar@").text
    assert "Smith" not in unlocked.get("/clients?q=%25").text
    unlocked.post(f"/clients/{other}/archive")
    assert "Khan" not in unlocked.get("/clients").text
    assert "Khan" in unlocked.get("/clients?archived=1").text
    unlocked.post(f"/clients/{other}/archive", data={"restore": "1"})
    assert "Khan" in unlocked.get("/clients").text


def test_live_search_returns_only_the_rows(unlocked, jane):
    partial = unlocked.get("/clients?q=smi", headers={"HX-Request": "true"})
    assert "Smith" in partial.text and "<html" not in partial.text


def test_text_is_escaped(unlocked):
    client_id = add_client(unlocked, first_name="<script>alert(1)</script>", goals="<b>bold</b>")
    for path in (f"/clients/{client_id}", "/clients"):
        text = unlocked.get(path).text
        assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text


def test_missing_client_is_a_friendly_404(unlocked):
    for path in ("/clients/999", "/clients/999/notes", "/clients/abc", "/clients/999/documents"):
        response = unlocked.get(path)
        assert response.status_code == 404 and "Back to Home" in response.text


def test_client_pages_need_unlocking(unlocked, jane):
    unlocked.post("/lock")
    for path in ("/clients", f"/clients/{jane}", f"/clients/{jane}/documents", "/diary", "/library"):
        assert unlocked.get(path).headers["location"] == "/unlock"
    assert unlocked.post("/clients/new", data=JANE).headers["location"] == "/unlock"
    htmx = unlocked.get("/clients?q=a", headers={"HX-Request": "true"})
    assert htmx.status_code == 204 and htmx.headers["hx-redirect"] == "/unlock"


def test_consent_record_withdraw_delete(unlocked, jane):
    url = f"/clients/{jane}/consents"
    assert unlocked.post(url, data={"kind": ""}).headers["location"].endswith("err=consent")
    unlocked.post(url, data={"kind": "Contact by email", "given_on": "2026-11-01", "method": "Verbal"})
    page = unlocked.get(f"/clients/{jane}/documents").text
    assert "Contact by email" in page and "1 Nov 2026" in page and "In place" in page
    consent_id = core_of(unlocked).db.one("SELECT id FROM consent")["id"]
    unlocked.post(f"{url}/{consent_id}/withdraw")
    assert "Withdrawn" in unlocked.get(f"/clients/{jane}/documents").text
    unlocked.post(f"{url}/{consent_id}/delete")
    assert core_of(unlocked).db.one("SELECT count(*) FROM consent")[0] == 0


def test_attachment_upload_download_delete(unlocked, jane, cfg):
    url = f"/clients/{jane}/attachments"
    scan = b"\x89PNG fake scan of a food diary"
    done = unlocked.post(url, data={"description": "Diary week 1"}, files={"file": ("../../diary scan.png", scan, "image/png")})
    assert done.headers["location"].endswith("msg=uploaded")
    row = core_of(unlocked).db.one("SELECT * FROM attachment")
    assert row["filename"] == "diary scan.png" and row["size"] == len(scan)
    stored = (cfg.attachments_dir / f"{row['file_id']}.bin").read_bytes()
    assert b"food diary" not in stored

    download = unlocked.get(f"{url}/{row['id']}")
    assert download.content == scan
    assert download.headers["content-disposition"].startswith("attachment;")
    assert download.headers["content-type"] == "application/octet-stream"
    view = unlocked.get(f"{url}/{row['id']}?view=1")
    assert view.headers["content-type"] == "image/png" and view.headers["content-disposition"].startswith("inline;")
    assert view.headers["x-content-type-options"] == "nosniff"

    assert unlocked.get(f"/clients/999/attachments/{row['id']}").status_code == 404
    unlocked.post(f"{url}/{row['id']}/delete")
    assert not list(cfg.attachments_dir.glob("*.bin"))
    assert unlocked.get(f"{url}/{row['id']}").status_code == 404


def test_html_attachment_is_never_shown_inline(unlocked, jane):
    url = f"/clients/{jane}/attachments"
    unlocked.post(url, files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")})
    attachment_id = core_of(unlocked).db.one("SELECT id FROM attachment")["id"]
    response = unlocked.get(f"{url}/{attachment_id}?view=1")
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_upload_rejects_empty_and_oversized(unlocked, jane, monkeypatch):
    from foodforthought import clients

    url = f"/clients/{jane}/attachments"
    assert unlocked.post(url, data={"description": "x"}).headers["location"].endswith("err=no-file")
    assert unlocked.post(url, files={"file": ("a.txt", b"", "text/plain")}).headers["location"].endswith("err=no-file")
    monkeypatch.setattr(clients, "MAX_UPLOAD", 10)
    assert unlocked.post(url, files={"file": ("a.txt", b"x" * 11, "text/plain")}).headers["location"].endswith("err=too-large")
    assert core_of(unlocked).db.one("SELECT count(*) FROM attachment")[0] == 0


def test_client_data_survives_backup_and_restore(unlocked, jane):
    from .test_backup import restore

    unlocked.post(f"/clients/{jane}/attachments", files={"file": ("a.txt", b"hello", "text/plain")})
    data = unlocked.post("/backup").content
    assert b"Smith" not in data and b"Peanuts" not in data
    add_client(unlocked, first_name="Later", last_name="Addition")
    assert restore(unlocked, data).status_code == 303
    unlocked.post("/unlock", data={"password": PASSWORD})
    listing = unlocked.get("/clients").text
    assert "Smith" in listing and "Addition" not in listing
    attachment_id = core_of(unlocked).db.one("SELECT id FROM attachment")["id"]
    assert unlocked.get(f"/clients/{jane}/attachments/{attachment_id}").content == b"hello"

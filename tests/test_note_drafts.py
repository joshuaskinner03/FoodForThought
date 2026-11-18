import io
import json
import zipfile

from .conftest import PASSWORD, core_of
from .test_clients import add_client


def drafts(client):
    return core_of(client).db.query("SELECT * FROM note_draft")


def test_unsaved_note_survives_the_lock(unlocked):
    jane = add_client(unlocked)
    form = {"note_date": "18/11/2026", "title": "First session", "body": "Discussed breakfast.", "appointment_id": "", "note_id": ""}
    assert unlocked.post(f"/clients/{jane}/notes/draft", data=form).status_code == 204
    form["body"] = "Discussed breakfast and lunch."
    unlocked.post(f"/clients/{jane}/notes/draft", data=form)
    assert len(drafts(unlocked)) == 1

    unlocked.post("/lock")
    unlocked.post("/unlock", data={"password": PASSWORD})
    home = unlocked.get("/").text
    assert "Unsaved note for Jane Smith" in home and f"/clients/{jane}/notes/new" in home
    assert "Carry on writing" in unlocked.get(f"/clients/{jane}/notes").text
    page = unlocked.get(f"/clients/{jane}/notes/new").text
    assert "This is your unsaved note" in page and "Discussed breakfast and lunch." in page and 'value="First session"' in page
    assert 'data-autosave="/clients/' in page

    saved = unlocked.post(f"/clients/{jane}/notes/new", data={"note_date": "18/11/2026", "title": "First session", "body": "Discussed breakfast and lunch."})
    assert saved.status_code == 303 and drafts(unlocked) == []
    assert "Unsaved note" not in unlocked.get("/").text


def test_drafts_of_existing_notes(unlocked):
    jane = add_client(unlocked)
    unlocked.post(f"/clients/{jane}/notes/new", data={"note_date": "18/11/2026", "title": "Plan", "body": "Original."})
    note_id = core_of(unlocked).db.one("SELECT id FROM note")["id"]
    unlocked.post(f"/clients/{jane}/notes/draft", data={"note_date": "18/11/2026", "title": "Plan", "body": "Edited.", "note_id": str(note_id)})
    assert "This is your unsaved note" not in unlocked.get(f"/clients/{jane}/notes/new").text
    page = unlocked.get(f"/clients/{jane}/notes/{note_id}/edit").text
    assert "Edited." in page and "This is your unsaved note" in page
    assert "Unsaved changes to a note" in unlocked.get(f"/clients/{jane}/notes").text
    done = unlocked.post(f"/clients/{jane}/notes/draft/discard", data={"note_id": str(note_id)})
    assert done.headers["location"] == f"/clients/{jane}/notes/{note_id}/edit" and drafts(unlocked) == []
    assert "Original." in unlocked.get(f"/clients/{jane}/notes/{note_id}/edit").text

    unlocked.post(f"/clients/{jane}/notes/draft", data={"title": "x", "body": "y", "note_id": ""})
    unlocked.post(f"/clients/{jane}/notes/draft", data={"title": "", "body": "", "note_id": ""})
    assert drafts(unlocked) == []
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    assert unlocked.post(f"/clients/{other}/notes/draft", data={"body": "z", "note_id": str(note_id)}).status_code == 404


def test_drafts_are_exported_and_erased(unlocked):
    jane = add_client(unlocked)
    unlocked.post(f"/clients/{jane}/notes/draft", data={"note_date": "18/11/2026", "title": "", "body": "Half written", "note_id": ""})
    archive = zipfile.ZipFile(io.BytesIO(unlocked.post(f"/clients/{jane}/export").content))
    assert json.loads(archive.read("record.json"))["note_drafts"][0]["body"] == "Half written"
    assert "Unsaved note drafts" in archive.read("record.html").decode()
    unlocked.post(f"/clients/{jane}/erase", data={"confirm": "Jane Smith"})
    assert drafts(unlocked) == []

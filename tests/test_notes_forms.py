import json

from starlette.datastructures import FormData

from foodforthought.forms import apply_action, parse_fields, read_answers

from .conftest import core_of
from .test_clients import add_client, jane  # noqa: F401


def db(client):
    return core_of(client).db


def test_default_templates_and_form_are_seeded_once(unlocked):
    library = unlocked.get("/library").text
    assert "Initial consultation" in library and "Follow-up" in library and "New client intake" in library
    unlocked.post("/lock")
    unlocked.post("/unlock", data={"password": "correct horse battery"})
    assert db(unlocked).one("SELECT count(*) FROM note_template")[0] == 2
    assert db(unlocked).one("SELECT count(*) FROM form_definition")[0] == 1


def test_note_template_create_edit_hide(unlocked, jane):
    assert unlocked.post("/library/note-templates/new", data={"name": "", "body": "x"}).status_code == 400
    unlocked.post("/library/note-templates/new", data={"name": "Review", "body": "Weight:\r\n\r\nPlan:"})
    row = db(unlocked).one("SELECT * FROM note_template WHERE name = 'Review'")
    assert row["body"] == "Weight:\n\nPlan:"
    unlocked.post(f"/library/note-templates/{row['id']}", data={"name": "Review session", "body": "Plan:"})
    assert "Review session" in unlocked.get(f"/clients/{jane}/notes/new").text
    unlocked.post(f"/library/note-templates/{row['id']}/archive")
    assert "Review session" not in unlocked.get(f"/clients/{jane}/notes/new").text
    assert "Review session" in unlocked.get("/library").text


def test_new_note_from_template_and_appointment(unlocked, jane):
    template_id = db(unlocked).one("SELECT id FROM note_template WHERE name = 'Follow-up'")["id"]
    unlocked.post("/diary/new", data={"client_id": str(jane), "title": "Review", "date": "2026-11-10", "start_time": "09:00", "duration_minutes": "30"})
    appointment_id = db(unlocked).one("SELECT id FROM appointment")["id"]
    page = unlocked.get(f"/clients/{jane}/notes/new?template={template_id}&appointment={appointment_id}").text
    assert "Progress since last session:" in page and 'value="10/11/2026"' in page
    unlocked.post(
        f"/clients/{jane}/notes/new",
        data={"note_date": "2026-11-10", "title": "Review", "body": "Going well.\r\nMore fibre.", "appointment_id": str(appointment_id)},
    )
    note = db(unlocked).one("SELECT * FROM note")
    assert note["appointment_id"] == appointment_id and note["body"] == "Going well.\nMore fibre."
    listing = unlocked.get(f"/clients/{jane}/notes").text
    assert "Tuesday 10 November 2026: Review" in listing and "More fibre." in listing
    assert "10 Nov 2026: Review" in unlocked.get(f"/clients/{jane}").text


def test_note_cannot_link_to_another_clients_appointment(unlocked, jane):
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    unlocked.post("/diary/new", data={"client_id": str(other), "date": "2026-11-10", "start_time": "09:00", "duration_minutes": "30"})
    appointment_id = db(unlocked).one("SELECT id FROM appointment")["id"]
    unlocked.post(f"/clients/{jane}/notes/new", data={"note_date": "2026-11-10", "body": "x", "appointment_id": str(appointment_id)})
    assert db(unlocked).one("SELECT appointment_id FROM note")["appointment_id"] is None


def test_note_validation_edit_delete_and_audit(unlocked, jane):
    url = f"/clients/{jane}/notes"
    assert unlocked.post(f"{url}/new", data={"note_date": "2026-11-10", "body": "  "}).status_code == 400
    assert unlocked.post(f"{url}/new", data={"note_date": "bad", "body": "x"}).status_code == 400
    unlocked.post(f"{url}/new", data={"note_date": "2026-11-10", "body": "Private clinical detail"})
    note_id = db(unlocked).one("SELECT id FROM note")["id"]
    unlocked.post(f"{url}/{note_id}/edit", data={"note_date": "2026-11-11", "body": "Amended"})
    assert db(unlocked).one("SELECT body FROM note")["body"] == "Amended"
    other = add_client(unlocked, first_name="Omar", last_name="Khan")
    assert unlocked.get(f"/clients/{other}/notes/{note_id}/edit").status_code == 404
    assert unlocked.post(f"/clients/{other}/notes/{note_id}/delete").status_code == 404
    unlocked.post(f"{url}/{note_id}/delete")
    assert db(unlocked).one("SELECT count(*) FROM note")[0] == 0
    log = db(unlocked).query("SELECT action, detail, client_id FROM audit_log WHERE action LIKE 'note_%'")
    assert [row["action"] for row in log] == ["note_created", "note_updated", "note_deleted"]
    assert all(row["client_id"] == jane and "clinical" not in row["detail"] for row in log)


def test_field_parsing_and_actions():
    posted = FormData(
        [
            ("count", "3"),
            ("f0_id", "abc"), ("f0_type", "choice"), ("f0_label", "Pick"), ("f0_options", "One\r\n\r\n Two "), ("f0_required", "on"),
            ("f1_id", "../x"), ("f1_type", "bogus"), ("f1_label", ""),
            ("f2_id", "h1"), ("f2_type", "heading"), ("f2_label", "Part two"), ("f2_required", "on"),
        ]
    )
    fields = parse_fields(posted)
    assert fields[0] == {"id": "abc", "type": "choice", "label": "Pick", "options": ["One", "Two"], "required": True}
    assert fields[1]["type"] == "text" and fields[1]["label"] == "Untitled question" and fields[1]["id"].isalnum()
    assert fields[2]["required"] is False
    ids = [f["id"] for f in fields]
    assert [f["id"] for f in apply_action(fields, "down:0")] == [ids[1], ids[0], ids[2]]
    assert [f["id"] for f in apply_action(fields, "up:2")] == [ids[0], ids[2], ids[1]]
    assert [f["id"] for f in apply_action(fields, "up:0")] == ids
    assert [f["id"] for f in apply_action(fields, "remove:1")] == [ids[0], ids[2]]
    assert [f["id"] for f in apply_action(fields, "remove:9")] == ids
    added = apply_action(fields, "add", "multi")
    assert len(added) == 4 and added[-1]["type"] == "multi"
    assert apply_action(fields, "save") == fields


def test_answer_reading():
    fields = [
        {"id": "a", "type": "text", "label": "Name", "options": [], "required": True},
        {"id": "b", "type": "choice", "label": "One", "options": ["X", "Y"], "required": False},
        {"id": "c", "type": "multi", "label": "Any", "options": ["X", "Y", "Z"], "required": False},
        {"id": "d", "type": "yesno", "label": "Yes?", "options": [], "required": False},
        {"id": "e", "type": "date", "label": "When", "options": [], "required": False},
        {"id": "h", "type": "heading", "label": "Section", "options": [], "required": False},
    ]
    posted = FormData([("a_a", ""), ("a_b", "Q"), ("a_c", "X"), ("a_c", "Z"), ("a_c", "Q"), ("a_d", "Maybe"), ("a_e", "tomorrow")])
    answers, missing = read_answers(fields, posted)
    assert answers == {"a": "", "b": "", "c": ["X", "Z"], "d": "", "e": ""} and missing == ["Name"]


def build_form(client) -> int:
    done = client.post("/library/forms/new", data={"name": "Check-in", "kind": "assessment"})
    form_id = int(done.headers["location"].rsplit("/", 1)[1])
    url = f"/library/forms/{form_id}"
    client.post(url, data={"count": "0", "name": "Check-in", "action": "add", "new_type": "text"})
    client.post(url, data={"count": "1", "f0_id": "q1", "f0_type": "text", "f0_label": "How are you?", "f0_required": "on", "action": "add", "new_type": "multi"})
    fields = json.loads(db(client).one("SELECT fields FROM form_definition WHERE id = ?", (form_id,))["fields"])
    client.post(
        url,
        data={
            "count": "2", "name": "Weekly check-in", "kind": "assessment", "description": "Fill in before each session.",
            "f0_id": "q1", "f0_type": "text", "f0_label": "How are you?", "f0_required": "on",
            "f1_id": fields[1]["id"], "f1_type": "multi", "f1_label": "Symptoms", "f1_options": "Bloating\nTiredness\nNone",
            "action": "save",
        },
    )
    return form_id


def test_form_builder_and_preview(unlocked):
    assert unlocked.post("/library/forms/new", data={"name": ""}).status_code == 400
    form_id = build_form(unlocked)
    row = db(unlocked).one("SELECT * FROM form_definition WHERE id = ?", (form_id,))
    fields = json.loads(row["fields"])
    assert row["name"] == "Weekly check-in" and [f["label"] for f in fields] == ["How are you?", "Symptoms"]
    assert fields[0]["required"] and fields[1]["options"] == ["Bloating", "Tiredness", "None"]
    builder = unlocked.get(f"/library/forms/{form_id}").text
    assert "Bloating\nTiredness\nNone" in builder
    preview = unlocked.get(f"/library/forms/{form_id}/preview").text
    assert "Weekly check-in" in preview and "Tiredness" in preview and "Fill in before each session." in preview


def test_fill_view_edit_delete_response(unlocked, jane):
    form_id = build_form(unlocked)
    multi_id = json.loads(db(unlocked).one("SELECT fields FROM form_definition WHERE id = ?", (form_id,))["fields"])[1]["id"]
    url = f"/clients/{jane}/forms"
    assert "Weekly check-in" in unlocked.get(url).text
    assert "How are you?" in unlocked.get(f"{url}/new?form={form_id}").text
    missing = unlocked.post(f"{url}/new?form={form_id}", data={"completed_on": "2026-11-12", f"a_{multi_id}": "Bloating"})
    assert missing.status_code == 400 and "Still needed: How are you?" in missing.text and "checked" in missing.text
    done = unlocked.post(
        f"{url}/new?form={form_id}",
        data={"completed_on": "2026-11-12", "a_q1": "Better <3", f"a_{multi_id}": ["Bloating", "Tiredness"]},
    )
    response_url = done.headers["location"].split("?")[0]
    view = unlocked.get(response_url).text
    assert "Better &lt;3" in view and "Bloating, Tiredness" in view and "12 Nov 2026" in view

    unlocked.post(f"/library/forms/{form_id}", data={"count": "1", "f0_id": "q9", "f0_type": "text", "f0_label": "Replaced", "action": "save"})
    view = unlocked.get(response_url).text
    assert "How are you?" in view and "Replaced" not in view

    unlocked.post(f"{response_url}/edit", data={"completed_on": "2026-11-13", "a_q1": "Much better"})
    view = unlocked.get(response_url).text
    assert "Much better" in view and "13 Nov 2026" in view and "Not answered" in view
    unlocked.post(f"{response_url}/delete")
    assert unlocked.get(response_url).status_code == 404


def test_hidden_form_is_not_offered_but_old_answers_stay(unlocked, jane):
    form_id = db(unlocked).one("SELECT id FROM form_definition")["id"]
    unlocked.post(f"/clients/{jane}/forms/new?form={form_id}", data={"completed_on": "2026-11-12"})
    unlocked.post(f"/library/forms/{form_id}/archive")
    page = unlocked.get(f"/clients/{jane}/forms").text
    assert "There are no forms yet" in page and "New client intake" in page
    assert unlocked.get(f"/clients/{jane}/forms/new?form=9999").status_code == 404

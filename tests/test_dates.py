from datetime import date

from foodforthought import fmt

from .conftest import core_of
from .test_clients import JANE, add_client, jane  # noqa: F401


def test_parse_date_accepts_day_first_and_iso():
    expected = date(2026, 11, 3)
    for text in ("03/11/2026", "3/11/2026", "3.11.2026", "03-11-2026", "2026-11-03", " 03/11/2026 "):
        assert fmt.parse_date(text) == expected, text
    for text in ("", None, "11/2026", "31/02/2026", "13/13/2026", "3/11/26", "tomorrow"):
        assert fmt.parse_date(text) is None, text
    assert fmt.uk_date("2026-11-03") == "03/11/2026"
    assert fmt.uk_date("nonsense") == "nonsense"
    assert fmt.iso("03/11/2026") == "2026-11-03" and fmt.iso("nonsense") == "nonsense"


def test_day_month_is_never_read_as_month_day(unlocked):
    client_id = add_client(unlocked, date_of_birth="05/12/1990")
    assert core_of(unlocked).db.one("SELECT date_of_birth FROM client")["date_of_birth"] == "1990-12-05"
    assert "5 Dec 1990" in unlocked.get(f"/clients/{client_id}").text
    assert 'value="05/12/1990"' in unlocked.get(f"/clients/{client_id}/edit").text


def test_no_native_date_box_carries_a_name(unlocked, jane):
    pages = ("/clients/new", f"/clients/{jane}/documents", "/diary", "/diary/new", f"/clients/{jane}/notes/new")
    for path in pages:
        html = unlocked.get(path).text
        assert 'placeholder="dd/mm/yyyy"' in html, path
        for chunk in html.split("<input")[1:]:
            tag = chunk.split(">")[0]
            if 'type="date"' in tag:
                assert "name=" not in tag and 'class="date-picker"' in tag, path


def test_uk_dates_flow_through_diary_notes_and_consent(unlocked, jane):
    unlocked.post("/diary/new", data={"client_id": str(jane), "date": "07/12/2026", "start_time": "10:00", "duration_minutes": "30"})
    assert core_of(unlocked).db.one("SELECT date FROM appointment")["date"] == "2026-12-07"
    assert "Jane Smith" in unlocked.get("/diary?week=2026-12-07").text
    unlocked.post(f"/clients/{jane}/notes/new", data={"note_date": "08/12/2026", "body": "x"})
    assert core_of(unlocked).db.one("SELECT note_date FROM note")["note_date"] == "2026-12-08"
    unlocked.post(f"/clients/{jane}/consents", data={"kind": "Email", "given_on": "09/12/2026"})
    assert core_of(unlocked).db.one("SELECT given_on FROM consent")["given_on"] == "2026-12-09"
    ics = unlocked.get("/diary/export.ics?start=01/12/2026&end=31/12/2026").text
    assert "DTSTART:20261207T100000" in ics
    bad = unlocked.post("/diary/new", data={"title": "x", "date": "31/02/2026", "start_time": "10:00", "duration_minutes": "30"})
    assert bad.status_code == 400 and 'value="31/02/2026"' in bad.text

from datetime import date, datetime, timedelta, timezone

from foodforthought.diary import _fold, occurrences, to_ics

from .conftest import core_of
from .test_clients import add_client, jane  # noqa: F401

BASE = {"title": "Follow-up", "date": "2026-11-18", "start_time": "10:00", "duration_minutes": "45"}


def appointments(client):
    return core_of(client).db.query("SELECT * FROM appointment ORDER BY date, start_time")


def test_occurrences():
    start = date(2026, 1, 31)
    assert occurrences(start, "", 5) == [start]
    assert occurrences(start, "weekly", 3) == [start, date(2026, 2, 7), date(2026, 2, 14)]
    assert occurrences(start, "fortnightly", 2)[1] == date(2026, 2, 14)
    assert occurrences(start, "four_weekly", 2)[1] == date(2026, 2, 28)
    assert occurrences(start, "monthly", 4) == [start, date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert occurrences(date(2026, 11, 30), "monthly", 4)[-1] == date(2027, 2, 28)
    assert len(occurrences(start, "weekly", 500)) == 52
    assert occurrences(start, "nonsense", 5) == [start]


def test_create_single_appointment(unlocked, jane):
    done = unlocked.post("/diary/new", data={**BASE, "client_id": str(jane), "location": "Clinic"})
    assert done.status_code == 303 and "msg=saved" in done.headers["location"]
    week = unlocked.get("/diary?week=2026-11-18").text
    assert "Jane Smith" in week and "10:00 to 10:45" in week and "16 Nov to 22 Nov 2026" in week
    assert "Jane Smith" not in unlocked.get("/diary?week=2026-11-25").text
    assert "Wednesday 18 November 2026" in unlocked.get(f"/clients/{jane}/appointments").text


def test_validation(unlocked):
    bad = [
        {**BASE, "title": ""},
        {**BASE, "date": "nope"},
        {**BASE, "start_time": "25:00"},
        {**BASE, "duration_minutes": "0"},
        {**BASE, "start_time": "23:30", "duration_minutes": "60"},
        {**BASE, "repeat": "weekly", "count": "1"},
        {**BASE, "repeat": "weekly", "count": "500"},
    ]
    for data in bad:
        assert unlocked.post("/diary/new", data=data).status_code == 400, data
    assert appointments(unlocked) == []


def test_recurring_series(unlocked, jane):
    done = unlocked.post("/diary/new", data={**BASE, "client_id": str(jane), "repeat": "fortnightly", "count": "4"})
    assert "msg=series" in done.headers["location"]
    rows = appointments(unlocked)
    assert [r["date"] for r in rows] == ["2026-11-18", "2026-12-02", "2026-12-16", "2026-12-30"]
    assert len({r["series_id"] for r in rows}) == 1 and rows[0]["series_id"] is not None

    unlocked.post(f"/diary/{rows[1]['id']}", data={**BASE, "client_id": str(jane), "date": "2026-12-03", "start_time": "14:00"})
    rows = appointments(unlocked)
    assert [r["date"] for r in rows] == ["2026-11-18", "2026-12-03", "2026-12-16", "2026-12-30"]
    assert rows[0]["start_time"] == "10:00" and rows[1]["start_time"] == "14:00"

    unlocked.post(f"/diary/{rows[2]['id']}/delete", data={"scope": "following"})
    assert [r["date"] for r in appointments(unlocked)] == ["2026-11-18", "2026-12-03"]
    unlocked.post(f"/diary/{rows[0]['id']}/delete", data={"scope": "one"})
    assert [r["date"] for r in appointments(unlocked)] == ["2026-12-03"]


def test_clash_warning(unlocked):
    unlocked.post("/diary/new", data=BASE)
    overlapping = unlocked.post("/diary/new", data={**BASE, "start_time": "10:30"})
    assert "msg=clash" in overlapping.headers["location"]
    adjacent = unlocked.post("/diary/new", data={**BASE, "start_time": "11:15"})
    assert "msg=saved" in adjacent.headers["location"]
    first = appointments(unlocked)[0]["id"]
    unlocked.post(f"/diary/{first}/status", data={"status": "cancelled"})
    second = appointments(unlocked)[1]["id"]
    resaved = unlocked.post(f"/diary/{second}", data={**BASE, "start_time": "10:30"})
    assert "msg=saved" in resaved.headers["location"]


def test_status_change_and_safe_redirect(unlocked):
    unlocked.post("/diary/new", data=BASE)
    appointment_id = appointments(unlocked)[0]["id"]
    done = unlocked.post(f"/diary/{appointment_id}/status", data={"status": "dna", "next": "//evil.example/"})
    assert done.headers["location"].startswith("/diary?week=")
    assert appointments(unlocked)[0]["status"] == "dna"
    assert unlocked.post(f"/diary/{appointment_id}/status", data={"status": "bogus"}).status_code == 400
    assert unlocked.get("/diary/9999").status_code == 404


def test_ics_export(unlocked, jane):
    unlocked.post("/diary/new", data={**BASE, "client_id": str(jane), "location": "Room 2; upstairs, left"})
    unlocked.post("/diary/new", data={**BASE, "title": "Admin", "date": "2026-11-19"})
    unlocked.post("/diary/new", data={**BASE, "title": "Gone", "date": "2026-11-20", "status": "cancelled"})
    unlocked.post("/diary/new", data={**BASE, "title": "Outside range", "date": "2027-06-01"})

    response = unlocked.get("/diary/export.ics?start=2026-11-01&end=2026-11-30")
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"]
    text = response.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 2
    assert "SUMMARY:Follow-up: JS\r\n" in text and "Jane" not in text and "Smith" not in text
    assert "DTSTART:20261118T100000\r\n" in text and "DTEND:20261118T104500\r\n" in text
    assert "LOCATION:Room 2\\; upstairs\\, left\r\n" in text
    assert "Gone" not in text and "Outside range" not in text

    full = unlocked.get("/diary/export.ics?start=2026-11-01&end=2026-11-30&names=full").text
    assert "SUMMARY:Follow-up: Jane Smith\r\n" in full
    logged = core_of(unlocked).db.query("SELECT detail FROM audit_log WHERE action = 'diary_exported'")
    assert len(logged) == 2 and "names: full" in logged[1]["detail"]


def test_ics_lines_fold_at_75_octets():
    line = "SUMMARY:" + "é" * 100
    folded = _fold(line)
    parts = folded.split("\r\n")
    assert all(len(part.encode()) <= 75 for part in parts)
    assert all(part.startswith(" ") for part in parts[1:])
    assert "".join([parts[0]] + [part[1:] for part in parts[1:]]) == line
    item = {
        "id": 1, "status": "booked", "date": "2026-11-18", "start_time": "09:00", "duration_minutes": 30,
        "client_id": None, "client_name": "", "title": "Line one\nLine two", "location": "",
    }
    text = to_ics([item], stamp=datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc))
    assert "SUMMARY:Line one\\nLine two\r\n" in text and "DTSTAMP:20261116T120000Z\r\n" in text


def test_home_shows_todays_appointments(unlocked, jane):
    today = date.today()
    unlocked.post("/diary/new", data={**BASE, "client_id": str(jane), "date": today.isoformat()})
    unlocked.post("/diary/new", data={**BASE, "title": "Tomorrow only", "date": (today + timedelta(days=1)).isoformat()})
    home = unlocked.get("/").text
    assert "Jane Smith" in home and "Tomorrow only" not in home


def test_archived_client_stays_on_their_appointment(unlocked, jane):
    unlocked.post("/diary/new", data={**BASE, "client_id": str(jane)})
    unlocked.post(f"/clients/{jane}/archive")
    appointment_id = appointments(unlocked)[0]["id"]
    page = unlocked.get(f"/diary/{appointment_id}").text
    assert "Jane Smith (archived)" in page

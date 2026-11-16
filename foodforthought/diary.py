from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import FormData

from . import clients, fmt
from .audit import now_iso
from .db import Database
from .web import Redirect, Web, form_data, text

STATUSES = (
    ("booked", "Booked"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
    ("dna", "Did not attend"),
)
REPEATS = (
    ("", "Does not repeat"),
    ("weekly", "Every week"),
    ("fortnightly", "Every 2 weeks"),
    ("four_weekly", "Every 4 weeks"),
    ("monthly", "Every month"),
)
STEP_DAYS = {"weekly": 7, "fortnightly": 14, "four_weekly": 28}
MAX_OCCURRENCES = 52
TITLES = ("Initial consultation", "Follow-up", "Review", "Phone call")
SELECT = (
    "SELECT a.*, c.first_name, c.last_name FROM appointment a "
    "LEFT JOIN client c ON c.id = a.client_id "
)


def occurrences(start: date, repeat: str, count: int) -> list[date]:
    if repeat not in dict(REPEATS) or not repeat:
        return [start]
    count = max(1, min(count, MAX_OCCURRENCES))
    if repeat == "monthly":
        dates = []
        for i in range(count):
            index = start.month - 1 + i
            year, month = start.year + index // 12, index % 12 + 1
            dates.append(date(year, month, min(start.day, calendar.monthrange(year, month)[1])))
        return dates
    return [start + timedelta(days=STEP_DAYS[repeat] * i) for i in range(count)]


def _decorate(row) -> dict:
    item = dict(row)
    item["end_time"] = fmt.end_time(row["start_time"], row["duration_minutes"])
    item["client_name"] = f"{row['first_name']} {row['last_name']}" if row["client_id"] else ""
    item["status_label"] = dict(STATUSES).get(row["status"], row["status"])
    return item


def between(db: Database, start: date, end: date) -> list[dict]:
    rows = db.query(
        SELECT + "WHERE a.date BETWEEN ? AND ? ORDER BY a.date, a.start_time, a.id",
        (start.isoformat(), end.isoformat()),
    )
    return [_decorate(row) for row in rows]


def for_client(db: Database, client_id: int) -> list[dict]:
    rows = db.query(SELECT + "WHERE a.client_id = ? ORDER BY a.date, a.start_time, a.id", (client_id,))
    return [_decorate(row) for row in rows]


def get(db: Database, appointment_id: int) -> dict:
    row = db.one(SELECT + "WHERE a.id = ?", (appointment_id,))
    if row is None:
        raise HTTPException(404, "That appointment does not exist.")
    return _decorate(row)


def clashes(db: Database, day: str, start_time: str, minutes: int, ignore_id: int | None = None) -> bool:
    end = fmt.end_time(start_time, minutes)
    for row in db.query(
        "SELECT id, start_time, duration_minutes FROM appointment WHERE date = ? AND status != 'cancelled'",
        (day,),
    ):
        if row["id"] == ignore_id:
            continue
        if row["start_time"] < end and start_time < fmt.end_time(row["start_time"], row["duration_minutes"]):
            return True
    return False


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    parts, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > (75 if not parts else 74):
            parts.append(current)
            current, size = "", 0
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def to_ics(appointments: list[dict], names: str = "initials", stamp: datetime | None = None) -> str:
    stamp = stamp or datetime.now(timezone.utc)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FoodForThought//Diary//EN", "CALSCALE:GREGORIAN"]
    for item in appointments:
        if item["status"] == "cancelled":
            continue
        start = datetime.combine(date.fromisoformat(item["date"]), fmt.parse_time(item["start_time"]))
        end = start + timedelta(minutes=item["duration_minutes"])
        who = ""
        if item["client_id"]:
            who = item["client_name"] if names == "full" else clients.initials(item)
        summary = ": ".join(part for part in (item["title"] or "Appointment", who) if part)
        lines += [
            "BEGIN:VEVENT",
            f"UID:appointment-{item['id']}@foodforthought.local",
            f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{start:%Y%m%dT%H%M%S}",
            f"DTEND:{end:%Y%m%dT%H%M%S}",
            f"SUMMARY:{_ics_text(summary)}",
        ]
        if item["location"]:
            lines.append(f"LOCATION:{_ics_text(item['location'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def read_form(form: FormData, db: Database) -> tuple[dict, list[str]]:
    values = {
        "client_id": None,
        "title": text(form, "title", 200),
        "date": text(form, "date"),
        "start_time": text(form, "start_time"),
        "duration_minutes": 60,
        "location": text(form, "location", 200),
        "status": text(form, "status") or "booked",
        "notes": text(form, "notes", 5000),
        "repeat": text(form, "repeat"),
        "count": 1,
    }
    errors = []
    raw_client = text(form, "client_id")
    if raw_client.isdigit() and db.one("SELECT 1 FROM client WHERE id = ?", (int(raw_client),)):
        values["client_id"] = int(raw_client)
    if not values["client_id"] and not values["title"]:
        errors.append("Choose a client or give the appointment a title.")
    if fmt.parse_date(values["date"]) is None:
        errors.append("Give a valid date.")
    start = fmt.parse_time(values["start_time"])
    if start is None:
        errors.append("Give a valid start time.")
    else:
        values["start_time"] = f"{start:%H:%M}"
    duration = text(form, "duration_minutes")
    if duration.isdigit() and 5 <= int(duration) <= 720:
        values["duration_minutes"] = int(duration)
    else:
        errors.append("Length must be between 5 and 720 minutes.")
    if start and not errors and fmt.end_time(values["start_time"], values["duration_minutes"]) <= values["start_time"]:
        errors.append("The appointment must finish on the day it starts.")
    if values["status"] not in dict(STATUSES):
        values["status"] = "booked"
    if values["repeat"] not in dict(REPEATS):
        values["repeat"] = ""
    count = text(form, "count")
    if values["repeat"]:
        if count.isdigit() and 2 <= int(count) <= MAX_OCCURRENCES:
            values["count"] = int(count)
        else:
            errors.append(f"Number of appointments must be between 2 and {MAX_OCCURRENCES}.")
    return values, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def form_page(request: Request, values: dict, appointment: dict | None, error: str | None = None):
        return web.page(
            request,
            "appointment_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            appointment=appointment,
            clients=clients.active(web.db),
            statuses=STATUSES,
            repeats=REPEATS,
            titles=TITLES,
            max_occurrences=MAX_OCCURRENCES,
        )

    @r.get("/diary")
    def week(request: Request, week: str = ""):
        today = date.today()
        anchor = fmt.parse_date(week) or today
        monday = anchor - timedelta(days=anchor.weekday())
        sunday = monday + timedelta(days=6)
        by_day: dict[str, list] = {}
        for item in between(web.db, monday, sunday):
            by_day.setdefault(item["date"], []).append(item)
        days = []
        for offset in range(7):
            day = monday + timedelta(days=offset)
            days.append(
                {
                    "date": day.isoformat(),
                    "weekday": f"{day:%a}",
                    "label": f"{day.day} {day:%b}",
                    "is_today": day == today,
                    "appointments": by_day.get(day.isoformat(), []),
                }
            )
        return web.page(
            request,
            "diary.html",
            days=days,
            week_label=f"{monday.day} {monday:%b} to {sunday.day} {sunday:%b %Y}",
            previous=(monday - timedelta(days=7)).isoformat(),
            following=(monday + timedelta(days=7)).isoformat(),
            export_end=(today + timedelta(days=90)).isoformat(),
        )

    @r.get("/diary/export.ics")
    def export(start: str = "", end: str = "", names: str = "initials"):
        first = fmt.parse_date(start) or date.today()
        last = fmt.parse_date(end) or first + timedelta(days=90)
        body = to_ics(between(web.db, first, last), "full" if names == "full" else "initials")
        web.log("diary_exported", f"{first.isoformat()} to {last.isoformat()}, names: {names}")
        return Response(
            body,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="foodforthought-diary.ics"'},
        )

    @r.get("/diary/new")
    def new_form(request: Request, client: str = "", day: str = ""):
        values = {
            "client_id": int(client) if client.isdigit() else None,
            "date": day if fmt.parse_date(day) else date.today().isoformat(),
            "start_time": "09:00",
            "duration_minutes": 60,
            "status": "booked",
            "count": 6,
        }
        return form_page(request, values, None)

    @r.post("/diary/new")
    def create(request: Request, form: FormData = Depends(form_data)):
        values, errors = read_form(form, web.db)
        if errors:
            return form_page(request, values, None, " ".join(errors))
        dates = occurrences(fmt.parse_date(values["date"]), values["repeat"], values["count"])
        now = now_iso()
        clash = False
        with web.db.transaction():
            series_id = None
            if len(dates) > 1:
                series_id = web.db.insert(
                    "INSERT INTO appointment_series(repeat, created_at) VALUES(?, ?)", (values["repeat"], now)
                )
            for day in dates:
                clash = clash or clashes(web.db, day.isoformat(), values["start_time"], values["duration_minutes"])
                appointment_id = web.db.insert(
                    "INSERT INTO appointment(client_id, series_id, title, date, start_time, duration_minutes, "
                    "location, status, notes, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        values["client_id"],
                        series_id,
                        values["title"],
                        day.isoformat(),
                        values["start_time"],
                        values["duration_minutes"],
                        values["location"],
                        values["status"],
                        values["notes"],
                        now,
                        now,
                    ),
                )
                web.log("appointment_created", f"appointment {appointment_id}", values["client_id"])
        message = ("series" if len(dates) > 1 else "saved") if not clash else (
            "series-clash" if len(dates) > 1 else "clash"
        )
        raise Redirect(f"/diary?week={values['date']}&msg={message}")

    @r.get("/diary/{appointment_id}")
    def edit_form(request: Request, appointment_id: int):
        appointment = get(web.db, appointment_id)
        return form_page(request, appointment, appointment)

    @r.post("/diary/{appointment_id}")
    def update(request: Request, appointment_id: int, form: FormData = Depends(form_data)):
        appointment = get(web.db, appointment_id)
        values, errors = read_form(form, web.db)
        if errors:
            return form_page(request, values, appointment, " ".join(errors))
        web.db.execute(
            "UPDATE appointment SET client_id = ?, title = ?, date = ?, start_time = ?, duration_minutes = ?, "
            "location = ?, status = ?, notes = ?, updated_at = ? WHERE id = ?",
            (
                values["client_id"],
                values["title"],
                values["date"],
                values["start_time"],
                values["duration_minutes"],
                values["location"],
                values["status"],
                values["notes"],
                now_iso(),
                appointment_id,
            ),
        )
        web.log("appointment_updated", f"appointment {appointment_id}", values["client_id"])
        clash = values["status"] != "cancelled" and clashes(
            web.db, values["date"], values["start_time"], values["duration_minutes"], appointment_id
        )
        raise Redirect(f"/diary?week={values['date']}&msg={'clash' if clash else 'saved'}")

    @r.post("/diary/{appointment_id}/status")
    def set_status(appointment_id: int, form: FormData = Depends(form_data)):
        appointment = get(web.db, appointment_id)
        status = text(form, "status")
        if status not in dict(STATUSES):
            raise HTTPException(400, "Unknown status.")
        web.db.execute(
            "UPDATE appointment SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), appointment_id)
        )
        web.log("appointment_updated", f"appointment {appointment_id}: {status}", appointment["client_id"])
        back = text(form, "next")
        if not back.startswith("/") or back.startswith("//"):
            back = f"/diary?week={appointment['date']}&msg=saved"
        raise Redirect(back)

    @r.post("/diary/{appointment_id}/delete")
    def delete(appointment_id: int, form: FormData = Depends(form_data)):
        appointment = get(web.db, appointment_id)
        if text(form, "scope") == "following" and appointment["series_id"]:
            rows = web.db.query(
                "SELECT id FROM appointment WHERE series_id = ? AND date >= ?",
                (appointment["series_id"], appointment["date"]),
            )
        else:
            rows = [appointment]
        with web.db.transaction():
            for row in rows:
                web.db.execute("DELETE FROM appointment WHERE id = ?", (row["id"],))
                web.log("appointment_deleted", f"appointment {row['id']}", appointment["client_id"])
        raise Redirect(f"/diary?week={appointment['date']}&msg=deleted")

    @r.get("/clients/{client_id}/appointments")
    def client_tab(request: Request, client_id: int):
        today = date.today().isoformat()
        items = for_client(web.db, client_id)
        return web.page(
            request,
            "client_appointments.html",
            upcoming=[item for item in items if item["date"] >= today],
            past=[item for item in reversed(items) if item["date"] < today],
            **clients.context(web, client_id, "appointments"),
        )

    return r

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


def parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text or "")
    except ValueError:
        return None


def parse_time(text: str | None) -> time | None:
    try:
        parsed = time.fromisoformat(text or "")
    except ValueError:
        return None
    return parsed.replace(second=0, microsecond=0, tzinfo=None)


def nice_date(value: str | None) -> str:
    d = parse_date(value)
    return f"{d.day} {d:%b %Y}" if d else ""


def long_date(value: str | None) -> str:
    d = parse_date(value)
    return f"{d:%A} {d.day} {d:%B %Y}" if d else ""


def local_stamp(value: str | None) -> str:
    try:
        utc = datetime.strptime(value or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return value or ""
    local = utc.astimezone()
    return f"{local.day} {local:%b %Y, %H:%M}"


def end_time(start: str, minutes: int) -> str:
    parsed = parse_time(start)
    if not parsed:
        return ""
    return f"{datetime.combine(date.min, parsed) + timedelta(minutes=minutes):%H:%M}"


def age(date_of_birth: str | None, today: date | None = None) -> int | None:
    born = parse_date(date_of_birth)
    if not born:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"

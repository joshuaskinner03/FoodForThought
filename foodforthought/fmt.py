from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


_DAY_FIRST = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")


def parse_date(text: str | None) -> date | None:
    text = (text or "").strip()
    day_first = _DAY_FIRST.match(text)
    try:
        if day_first:
            return date(int(day_first[3]), int(day_first[2]), int(day_first[1]))
        return date.fromisoformat(text)
    except ValueError:
        return None


def iso(text: str | None) -> str:
    parsed = parse_date(text)
    return parsed.isoformat() if parsed else (text or "").strip()


def uk_date(value: str | None) -> str:
    parsed = parse_date(value)
    return f"{parsed:%d/%m/%Y}" if parsed else (value or "")


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


def money(pence: int | None) -> str:
    pence = pence or 0
    sign = "-" if pence < 0 else ""
    return f"{sign}£{abs(pence) // 100:,}.{abs(pence) % 100:02d}"


def parse_money(text: str | None) -> int | None:
    cleaned = (text or "").replace("£", "").replace(",", "").strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_decimal(text: str | None) -> Decimal | None:
    try:
        value = Decimal((text or "").replace(",", ".").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def number(value, places: int = 1) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{places}f}"


def plain(value) -> str:
    if value is None or value == "":
        return ""
    try:
        return f"{Decimal(str(value)).normalize():f}"
    except InvalidOperation:
        return str(value)

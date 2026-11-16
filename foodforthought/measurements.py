from __future__ import annotations

import json
import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from . import clients, fmt
from .audit import now_iso
from .web import Redirect, Web, form_data, text

SITES = (
    ("triceps", "Triceps"),
    ("biceps", "Biceps"),
    ("subscapular", "Subscapular"),
    ("suprailiac", "Suprailiac"),
    ("chest", "Chest"),
    ("midaxillary", "Midaxillary"),
    ("abdomen", "Abdomen"),
    ("thigh", "Thigh"),
)
SEVEN_SITES = ("chest", "midaxillary", "triceps", "subscapular", "abdomen", "suprailiac", "thigh")
METHODS = {
    "durnin_womersley": {
        "label": "Durnin and Womersley (1974), 4 sites",
        "sites": {sex: ("biceps", "triceps", "subscapular", "suprailiac") for sex in ("female", "male")},
    },
    "jackson_pollock_3": {
        "label": "Jackson and Pollock, 3 sites",
        "sites": {"female": ("triceps", "suprailiac", "thigh"), "male": ("chest", "abdomen", "thigh")},
    },
    "jackson_pollock_7": {
        "label": "Jackson and Pollock, 7 sites",
        "sites": {"female": SEVEN_SITES, "male": SEVEN_SITES},
    },
}
MIN_AGE = 16

# Durnin & Womersley 1974, Br J Nutr 32:77-97, table 5: density = c - m * log10(sum of 4 skinfolds).
DURNIN_WOMERSLEY = {
    "male": ((20, 1.1620, 0.0630), (30, 1.1631, 0.0632), (40, 1.1422, 0.0544), (50, 1.1620, 0.0700), (999, 1.1715, 0.0779)),
    "female": ((20, 1.1549, 0.0678), (30, 1.1599, 0.0717), (40, 1.1423, 0.0632), (50, 1.1333, 0.0612), (999, 1.1339, 0.0645)),
}
# Jackson & Pollock 1978 (men), Jackson, Pollock & Ward 1980 (women):
# density = a - b * sum + c * sum^2 - d * age.
JACKSON_POLLOCK = {
    ("jackson_pollock_3", "male"): (1.10938, 0.0008267, 0.0000016, 0.0002574),
    ("jackson_pollock_3", "female"): (1.0994921, 0.0009929, 0.0000023, 0.0001392),
    ("jackson_pollock_7", "male"): (1.112, 0.00043499, 0.00000055, 0.00028826),
    ("jackson_pollock_7", "female"): (1.097, 0.00046971, 0.00000056, 0.00012828),
}
RANGES = {"weight_kg": (2, 400), "height_cm": (30, 250), "waist_cm": (20, 250)}
LABELS = {"weight_kg": "Weight", "height_cm": "Height", "waist_cm": "Waist"}
SERIES = (
    ("weight_kg", "Weight", "kg", 1),
    ("bmi", "BMI", "", 1),
    ("waist_cm", "Waist", "cm", 1),
    ("body_fat_percent", "Body fat", "%", 1),
)


def siri(density: float) -> float:
    return (4.95 / density - 4.5) * 100


def body_density(method: str, sex: str, age: int, skinfolds: dict[str, float]) -> float:
    total = sum(skinfolds[site] for site in METHODS[method]["sites"][sex])
    if method == "durnin_womersley":
        for upper, c, m in DURNIN_WOMERSLEY[sex]:
            if age < upper:
                return c - m * math.log10(total)
    a, b, c, d = JACKSON_POLLOCK[(method, sex)]
    return a - b * total + c * total * total - d * age


def body_fat(method: str, sex: str, age: int | None, skinfolds: dict[str, float]) -> tuple[float | None, str | None]:
    if method not in METHODS:
        return None, None
    if sex not in ("female", "male"):
        return None, "Body fat needs the client's sex. Add it under Edit details."
    if age is None:
        return None, "Body fat needs the client's date of birth. Add it under Edit details."
    if age < MIN_AGE:
        return None, f"These equations were developed on people aged {MIN_AGE} and over."
    missing = [dict(SITES)[site] for site in METHODS[method]["sites"][sex] if site not in skinfolds]
    if missing:
        return None, "This equation also needs: " + ", ".join(missing) + "."
    return round(siri(body_density(method, sex, age, skinfolds)), 1), None


def bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    if not weight_kg or not height_cm:
        return None
    return round(weight_kg / (height_cm / 100) ** 2, 1)


def with_bmi(rows: list) -> list[dict]:
    items, height = [], None
    for row in sorted(rows, key=lambda r: (r["measured_on"], r["id"])):
        height = row["height_cm"] or height
        item = dict(row, skinfolds=json.loads(row["skinfolds"]))
        item["bmi"] = bmi(row["weight_kg"], height)
        item["height_used"] = height
        items.append(item)
    return items


def _ticks(low: float, high: float, target: int = 4) -> list[float]:
    if high - low < 1e-9:
        low, high = low - 1, high + 1
    raw = (high - low) / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    first = math.floor(low / step) * step
    ticks = [first]
    while ticks[-1] < high - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def chart(points: list[tuple[date, float]], title: str, unit: str, places: int = 1) -> dict | None:
    if len(points) < 2:
        return None
    width, height, left, right, top, bottom = 420, 190, 40, 62, 12, 28
    ticks = _ticks(min(v for _, v in points), max(v for _, v in points))
    low, high = ticks[0], ticks[-1]
    first, last = points[0][0].toordinal(), points[-1][0].toordinal()
    span = max(last - first, 1)
    decimals = 0 if all(float(t).is_integer() for t in ticks) else 1

    def x(day: date) -> float:
        return round(left + (day.toordinal() - first) / span * (width - left - right), 1)

    def y(value: float) -> float:
        return round(top + (high - value) / (high - low) * (height - top - bottom), 1)

    suffix = f" {unit}" if unit and unit != "%" else unit
    marks = [
        {"x": x(day), "y": y(value), "tip": f"{fmt.nice_date(day.isoformat())}: {value:.{places}f}{suffix}"}
        for day, value in points
    ]
    return {
        "title": title,
        "unit": unit,
        "width": width,
        "height": height,
        "left": left,
        "plot_right": width - right,
        "baseline": height - bottom,
        "path": " ".join(f"{'M' if i == 0 else 'L'}{m['x']} {m['y']}" for i, m in enumerate(marks)),
        "marks": marks,
        "ticks": [{"y": y(t), "label": f"{t:.{decimals}f}"} for t in ticks],
        "start": fmt.nice_date(points[0][0].isoformat()),
        "end": fmt.nice_date(points[-1][0].isoformat()),
        "last_label": f"{points[-1][1]:.{places}f}{suffix}",
        "summary": f"{title} from {marks[0]['tip']} to {marks[-1]['tip']}",
    }


def charts(items: list[dict]) -> list[dict]:
    built = []
    for key, title, unit, places in SERIES:
        points = [(date.fromisoformat(i["measured_on"]), float(i[key])) for i in items if i.get(key) is not None]
        made = chart(points, title, unit, places)
        if made:
            built.append(made)
    return built


def read_form(form: FormData) -> tuple[dict, list[str]]:
    values: dict = {
        "measured_on": fmt.iso(text(form, "measured_on")),
        "body_fat_method": text(form, "body_fat_method"),
        "notes": text(form, "notes", 2000),
        "skinfolds": {},
    }
    errors = []
    if fmt.parse_date(values["measured_on"]) is None:
        errors.append("Give a valid date.")
    elif fmt.parse_date(values["measured_on"]) > date.today():
        errors.append("The date cannot be in the future.")
    for name, (low, high) in RANGES.items():
        raw = text(form, name)
        values[name] = raw
        if not raw:
            values[name] = None
            continue
        parsed = fmt.parse_decimal(raw)
        if parsed is None or not low <= parsed <= high:
            errors.append(f"{LABELS[name]} must be between {low} and {high}.")
        else:
            values[name] = float(parsed)
    for site, label in SITES:
        raw = text(form, f"sf_{site}")
        if not raw:
            continue
        parsed = fmt.parse_decimal(raw)
        if parsed is None or not 1 <= parsed <= 100:
            errors.append(f"{label} skinfold must be between 1 and 100 mm.")
            values["skinfolds"][site] = raw
        else:
            values["skinfolds"][site] = float(parsed)
    if values["body_fat_method"] not in METHODS:
        values["body_fat_method"] = ""
    measured = any(values[name] is not None for name in RANGES) or values["skinfolds"]
    if not errors and not measured:
        errors.append("Enter at least one measurement.")
    return values, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def rows(client_id: int) -> list[dict]:
        return with_bmi(web.db.query("SELECT * FROM measurement WHERE client_id = ?", (client_id,)))

    def row(client_id: int, measurement_id: int):
        found = web.db.one("SELECT * FROM measurement WHERE id = ? AND client_id = ?", (measurement_id, client_id))
        if found is None:
            raise HTTPException(404, "That measurement does not exist.")
        return found

    def form_page(request: Request, client_id: int, values: dict, existing, error: str | None = None):
        context = clients.context(web, client_id, "measurements")
        sex = context["client"]["sex"]
        return web.page(
            request,
            "measurement_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            existing=existing,
            sites=SITES,
            methods=[
                (key, spec["label"], ", ".join(dict(SITES)[s] for s in spec["sites"].get(sex, ())))
                for key, spec in METHODS.items()
            ],
            **context,
        )

    @r.get("/clients/{client_id}/measurements")
    def client_tab(request: Request, client_id: int):
        items = rows(client_id)
        return web.page(
            request,
            "client_measurements.html",
            measurements=list(reversed(items)),
            charts=charts(items),
            method_labels={key: spec["label"] for key, spec in METHODS.items()},
            **clients.context(web, client_id, "measurements"),
        )

    @r.get("/clients/{client_id}/measurements/new")
    def new_form(request: Request, client_id: int):
        latest = web.db.one(
            "SELECT body_fat_method FROM measurement WHERE client_id = ? AND body_fat_method != '' "
            "ORDER BY measured_on DESC, id DESC LIMIT 1",
            (client_id,),
        )
        values = {
            "measured_on": date.today().isoformat(),
            "skinfolds": {},
            "body_fat_method": latest["body_fat_method"] if latest else "",
        }
        return form_page(request, client_id, values, None)

    def save(request: Request, client_id: int, form: FormData, existing):
        client = clients.get(web.db, client_id)
        values, errors = read_form(form)
        percent = None
        if not errors and values["body_fat_method"]:
            age = fmt.age(client["date_of_birth"], fmt.parse_date(values["measured_on"]))
            percent, problem = body_fat(values["body_fat_method"], client["sex"], age, values["skinfolds"])
            if problem:
                errors.append(problem)
        if errors:
            return form_page(request, client_id, values, existing, " ".join(errors))
        params = (
            values["measured_on"],
            values["weight_kg"],
            values["height_cm"],
            values["waist_cm"],
            json.dumps(values["skinfolds"]),
            values["body_fat_method"],
            percent,
            values["notes"],
        )
        now = now_iso()
        if existing:
            web.db.execute(
                "UPDATE measurement SET measured_on = ?, weight_kg = ?, height_cm = ?, waist_cm = ?, skinfolds = ?, "
                "body_fat_method = ?, body_fat_percent = ?, notes = ?, updated_at = ? WHERE id = ?",
                (*params, now, existing["id"]),
            )
            web.log("measurement_updated", f"measurement {existing['id']}", client_id)
        else:
            measurement_id = web.db.insert(
                "INSERT INTO measurement(measured_on, weight_kg, height_cm, waist_cm, skinfolds, body_fat_method, "
                "body_fat_percent, notes, client_id, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*params, client_id, now, now),
            )
            web.log("measurement_added", f"measurement {measurement_id}", client_id)
        raise Redirect(f"/clients/{client_id}/measurements?msg=saved")

    @r.post("/clients/{client_id}/measurements/new")
    def create(request: Request, client_id: int, form: FormData = Depends(form_data)):
        return save(request, client_id, form, None)

    @r.get("/clients/{client_id}/measurements/{measurement_id}/edit")
    def edit_form(request: Request, client_id: int, measurement_id: int):
        existing = row(client_id, measurement_id)
        return form_page(request, client_id, dict(existing, skinfolds=json.loads(existing["skinfolds"])), existing)

    @r.post("/clients/{client_id}/measurements/{measurement_id}/edit")
    def update(request: Request, client_id: int, measurement_id: int, form: FormData = Depends(form_data)):
        return save(request, client_id, form, row(client_id, measurement_id))

    @r.post("/clients/{client_id}/measurements/{measurement_id}/delete")
    def delete(client_id: int, measurement_id: int):
        row(client_id, measurement_id)
        web.db.execute("DELETE FROM measurement WHERE id = ?", (measurement_id,))
        web.log("measurement_deleted", f"measurement {measurement_id}", client_id)
        raise Redirect(f"/clients/{client_id}/measurements?msg=deleted")

    return r

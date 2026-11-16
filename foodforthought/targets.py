from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from . import clients, fmt
from .audit import now_iso
from .nutrients import KCAL_PER_GRAM
from .web import Redirect, Web, form_data, text

EQUATIONS = (("henry", "Henry (Oxford, 2005)"), ("mifflin", "Mifflin-St Jeor (1990)"))
ACTIVITY = (
    ("1.2", "1.2: sedentary, little or no exercise"),
    ("1.375", "1.375: lightly active"),
    ("1.55", "1.55: moderately active"),
    ("1.725", "1.725: very active"),
    ("1.9", "1.9: extremely active"),
    ("custom", "Another value"),
)
PROTEIN_BASES = (("per_kg", "grams per kg of body weight"), ("percent", "per cent of energy"))
MIN_AGE = 18

# Henry CJK. Public Health Nutr 2005;8(7A):1133-52. kcal/day = a * weight(kg) + b * height(cm) + c.
HENRY = {
    "male": ((30, 14.4, 3.13, 113.0), (60, 11.4, 5.41, -137.0), (999, 11.4, 5.41, -256.0)),
    "female": ((30, 10.4, 6.15, -282.0), (60, 8.18, 5.02, -11.6), (999, 8.52, 4.21, 10.7)),
}


def henry(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    for upper, a, b, c in HENRY[sex]:
        if age < upper:
            return a * weight_kg + b * height_cm + c
    raise ValueError(age)


def mifflin(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    return 10 * weight_kg + 6.25 * height_cm - 5 * age + (5 if sex == "male" else -161)


def bmr(equation: str, sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    return (henry if equation == "henry" else mifflin)(sex, weight_kg, height_cm, age)


def macros(energy_kcal: float, weight_kg: float, protein_basis: str, protein_value: float, fat_percent: float) -> dict:
    if protein_basis == "per_kg":
        protein_g = protein_value * weight_kg
    else:
        protein_g = energy_kcal * protein_value / 100 / KCAL_PER_GRAM["protein"]
    fat_g = energy_kcal * fat_percent / 100 / KCAL_PER_GRAM["fat"]
    carbohydrate_kcal = energy_kcal - protein_g * KCAL_PER_GRAM["protein"] - fat_g * KCAL_PER_GRAM["fat"]
    return {
        "protein_g": protein_g,
        "fat_g": fat_g,
        "carbohydrate_g": carbohydrate_kcal / KCAL_PER_GRAM["carbohydrate"],
        "protein_percent": protein_g * KCAL_PER_GRAM["protein"] / energy_kcal * 100,
        "fat_percent": fat_percent,
        "carbohydrate_percent": carbohydrate_kcal / energy_kcal * 100,
    }


def describe(row) -> dict:
    item = dict(row)
    item["tee_kcal"] = row["bmr_kcal"] * row["pal"]
    item.update(macros(row["energy_kcal"], row["weight_kg"], row["protein_basis"], row["protein_value"], row["fat_percent"]))
    item["equation_label"] = dict(EQUATIONS).get(row["equation"], row["equation"])
    return item


def _number(form: FormData, name: str, label: str, low: float, high: float, errors: list, values: dict) -> float | None:
    raw = text(form, name)
    values[name] = raw
    parsed = fmt.parse_decimal(raw)
    if parsed is None or not low <= parsed <= high:
        errors.append(f"{label} must be between {low:g} and {high:g}.")
        return None
    values[name] = float(parsed)
    return float(parsed)


def read_form(form: FormData) -> tuple[dict, list[str]]:
    values: dict = {
        "set_on": fmt.iso(text(form, "set_on")),
        "sex": text(form, "sex"),
        "equation": text(form, "equation"),
        "activity": text(form, "activity"),
        "protein_basis": text(form, "protein_basis"),
        "notes": text(form, "notes", 1000),
    }
    errors: list[str] = []
    if fmt.parse_date(values["set_on"]) is None:
        errors.append("Give a valid date.")
    if values["sex"] not in ("female", "male"):
        errors.append("Choose female or male. The equations need it.")
    if values["equation"] not in dict(EQUATIONS):
        values["equation"] = "henry"
    if values["protein_basis"] not in dict(PROTEIN_BASES):
        values["protein_basis"] = "per_kg"
    age = _number(form, "age", "Age", MIN_AGE, 120, errors, values)
    _number(form, "weight_kg", "Weight", 30, 400, errors, values)
    _number(form, "height_cm", "Height", 120, 250, errors, values)
    if values["activity"] in dict(ACTIVITY) and values["activity"] != "custom":
        values["pal"] = float(values["activity"])
        values["custom_pal"] = text(form, "custom_pal")
    else:
        values["activity"] = "custom"
        pal = _number(form, "custom_pal", "The activity factor", 1.0, 2.5, errors, values)
        values["pal"] = pal
    raw_adjustment = text(form, "adjustment_kcal") or "0"
    adjustment = fmt.parse_decimal(raw_adjustment.replace("+", "").replace("−", "-"))
    values["adjustment_kcal"] = raw_adjustment
    if adjustment is None or not -2000 <= adjustment <= 2000:
        errors.append("The adjustment must be between -2000 and 2000 kcal.")
    else:
        values["adjustment_kcal"] = float(adjustment)
    high = 4 if values["protein_basis"] == "per_kg" else 50
    low = 0.3 if values["protein_basis"] == "per_kg" else 5
    _number(form, "protein_value", "Protein", low, high, errors, values)
    _number(form, "fat_percent", "Fat", 10, 60, errors, values)
    if age is not None:
        values["age"] = int(age)
    return values, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def form_page(request: Request, client_id: int, values: dict, error: str | None = None, source: str = ""):
        return web.page(
            request,
            "target_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            equations=EQUATIONS,
            activity=ACTIVITY,
            protein_bases=PROTEIN_BASES,
            source=source,
            **clients.context(web, client_id, "targets"),
        )

    @r.get("/clients/{client_id}/targets")
    def client_tab(request: Request, client_id: int):
        rows = web.db.query("SELECT * FROM target WHERE client_id = ? ORDER BY set_on DESC, id DESC", (client_id,))
        items = [describe(row) for row in rows]
        return web.page(
            request,
            "client_targets.html",
            current=items[0] if items else None,
            earlier=items[1:],
            **clients.context(web, client_id, "targets"),
        )

    @r.get("/clients/{client_id}/targets/new")
    def new_form(request: Request, client_id: int):
        client = clients.get(web.db, client_id)
        today = date.today()
        weight = web.db.one(
            "SELECT weight_kg, measured_on FROM measurement WHERE client_id = ? AND weight_kg IS NOT NULL "
            "ORDER BY measured_on DESC, id DESC LIMIT 1",
            (client_id,),
        )
        height = web.db.one(
            "SELECT height_cm FROM measurement WHERE client_id = ? AND height_cm IS NOT NULL "
            "ORDER BY measured_on DESC, id DESC LIMIT 1",
            (client_id,),
        )
        previous = web.db.one("SELECT * FROM target WHERE client_id = ? ORDER BY set_on DESC, id DESC LIMIT 1", (client_id,))
        values = {
            "set_on": today.isoformat(),
            "sex": client["sex"],
            "age": fmt.age(client["date_of_birth"], today) or "",
            "weight_kg": weight["weight_kg"] if weight else "",
            "height_cm": height["height_cm"] if height else "",
            "equation": previous["equation"] if previous else "henry",
            "activity": "1.375",
            "custom_pal": "",
            "adjustment_kcal": previous["adjustment_kcal"] if previous else 0,
            "protein_basis": previous["protein_basis"] if previous else "per_kg",
            "protein_value": previous["protein_value"] if previous else 0.75,
            "fat_percent": previous["fat_percent"] if previous else 35,
        }
        if previous:
            preset = f"{previous['pal']:g}"
            values["activity"] = preset if preset in dict(ACTIVITY) else "custom"
            values["custom_pal"] = "" if preset in dict(ACTIVITY) else preset
        source = f"Weight is from the measurement on {fmt.nice_date(weight['measured_on'])}." if weight else ""
        return form_page(request, client_id, values, source=source)

    @r.post("/clients/{client_id}/targets/new")
    def create(request: Request, client_id: int, form: FormData = Depends(form_data)):
        clients.get(web.db, client_id)
        values, errors = read_form(form)
        if not errors:
            base = bmr(values["equation"], values["sex"], values["weight_kg"], values["height_cm"], values["age"])
            energy = base * values["pal"] + values["adjustment_kcal"]
            if energy < 800:
                errors.append("That gives an energy target below 800 kcal a day. Check the figures.")
            else:
                split = macros(energy, values["weight_kg"], values["protein_basis"], values["protein_value"], values["fat_percent"])
                if split["carbohydrate_g"] < 0:
                    errors.append("Protein and fat together come to more than the energy target. Lower one of them.")
        if errors:
            return form_page(request, client_id, values, " ".join(errors))
        target_id = web.db.insert(
            "INSERT INTO target(client_id, set_on, sex, age, weight_kg, height_cm, equation, bmr_kcal, pal, "
            "adjustment_kcal, energy_kcal, protein_basis, protein_value, fat_percent, protein_g, fat_g, "
            "carbohydrate_g, notes, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                values["set_on"],
                values["sex"],
                values["age"],
                values["weight_kg"],
                values["height_cm"],
                values["equation"],
                base,
                values["pal"],
                values["adjustment_kcal"],
                energy,
                values["protein_basis"],
                values["protein_value"],
                values["fat_percent"],
                split["protein_g"],
                split["fat_g"],
                split["carbohydrate_g"],
                values["notes"],
                now_iso(),
            ),
        )
        web.log("targets_set", f"targets {target_id}", client_id)
        raise Redirect(f"/clients/{client_id}/targets?msg=saved")

    @r.post("/clients/{client_id}/targets/{target_id}/delete")
    def delete(client_id: int, target_id: int):
        if web.db.one("SELECT 1 FROM target WHERE id = ? AND client_id = ?", (target_id, client_id)) is None:
            raise HTTPException(404, "Those targets do not exist.")
        web.db.execute("DELETE FROM target WHERE id = ?", (target_id,))
        web.log("targets_deleted", f"targets {target_id}", client_id)
        raise Redirect(f"/clients/{client_id}/targets?msg=deleted")

    return r

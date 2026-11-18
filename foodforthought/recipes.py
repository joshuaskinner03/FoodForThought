from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from . import fmt, foods
from .audit import now_iso
from .db import Database
from .nutrients import NUTRIENTS
from .web import Redirect, Web, form_data, text

MAX_GRAMS = 5000
SUMMARY = ("energy_kcal", "protein", "fat", "saturates", "carbohydrate", "sugars", "fibre")


class AmountProblem(Exception):
    pass


def number(value: str) -> float | None:
    try:
        found = float((value or "").strip().replace(",", "."))
    except ValueError:
        return None
    return found if found > 0 else None


def read_amount(db: Database, food_id: int, amount: str, unit: str) -> tuple[float, float | None, str]:
    value = number(amount)
    if value is None:
        raise AmountProblem("Enter an amount above 0.")
    if unit.startswith("portion:"):
        portion = db.one("SELECT * FROM portion WHERE id = ? AND food_id = ?", (int(unit.split(":", 1)[1] or 0), food_id))
        if not portion:
            raise AmountProblem("Choose a unit.")
        grams, quantity, unit = value * portion["grams"], value, portion["name"]
    else:
        grams, quantity, unit = value, None, "g"
    if grams > MAX_GRAMS:
        raise AmountProblem(f"That is more than {MAX_GRAMS} g. Check the amount.")
    return round(grams, 1), quantity, unit


def amount_text(grams: float, quantity: float | None, unit: str) -> str:
    if quantity and unit and unit != "g":
        name = unit[2:] if unit.startswith("1 ") else unit
        return f"{fmt.plain(quantity)} × {name} ({grams:.0f} g)"
    return f"{grams:.0f} g" if grams >= 10 else f"{fmt.plain(round(grams, 1))} g"


def decode(nutrients) -> dict:
    return json.loads(nutrients) if isinstance(nutrients, str) else nutrients


def totals(rows: list[dict]) -> dict:
    result = {}
    for nutrient in NUTRIENTS:
        value, gap = 0.0, False
        for row in rows:
            known = decode(row["nutrients"]).get(nutrient.key)
            if known is None:
                gap = gap or row["grams"] > 0
            else:
                value += known * row["grams"] / 100
        result[nutrient.key] = {"value": value, "gap": gap}
    return result


def scale(values: dict, factor: float) -> dict:
    return {key: {"value": entry["value"] * factor, "gap": entry["gap"]} for key, entry in values.items()}


def get(db: Database, recipe_id: int) -> dict | None:
    row = db.one("SELECT * FROM recipe WHERE id = ?", (recipe_id,))
    return dict(row) if row else None


def items(db: Database, recipe_id: int) -> list[dict]:
    rows = db.query(
        "SELECT i.*, f.name AS food_name, f.nutrients, f.food_group FROM recipe_item i JOIN food f ON f.id = i.food_id "
        "WHERE i.recipe_id = ? ORDER BY i.position, i.id",
        (recipe_id,),
    )
    return [dict(row, amount=amount_text(row["grams"], row["quantity"], row["unit"])) for row in rows]


def summary(db: Database, recipe: dict, found: list[dict] | None = None) -> dict:
    found = items(db, recipe["id"]) if found is None else found
    raw = sum(item["grams"] for item in found)
    made = recipe["cooked_grams"] or raw
    whole = totals(found)
    servings = recipe["servings"] or 1
    return {
        "items": found,
        "raw_grams": raw,
        "made_grams": made,
        "serving_grams": made / servings if servings else 0,
        "per_serving": scale(whole, 1 / servings),
        "whole": whole,
    }


def salt(values: dict) -> dict:
    sodium = values.get("sodium", {"value": 0, "gap": False})
    return {"value": sodium["value"] * 2.5 / 1000, "gap": sodium["gap"]}


def search(db: Database, query: str, limit: int = 8) -> list[dict]:
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    rows = db.query("SELECT * FROM recipe WHERE archived = 0 ORDER BY name")
    found = []
    for row in rows:
        haystack = f"{row['name']} {row['tags']}".lower()
        if all(word in haystack for word in words):
            found.append(dict(row, kcal=summary(db, dict(row))["per_serving"]["energy_kcal"]["value"]))
    return found[:limit]


def used_in(db: Database, recipe_id: int) -> int:
    return db.one("SELECT count(*) FROM meal_plan_item WHERE recipe_id = ?", (recipe_id,))[0]


def read_details(form: FormData) -> tuple[dict, list[str]]:
    values = {
        "name": text(form, "name"),
        "servings": text(form, "servings"),
        "cooked_grams": text(form, "cooked_grams"),
        "tags": ", ".join(t.strip() for t in text(form, "tags").split(",") if t.strip()),
        "method": text(form, "method"),
        "notes": text(form, "notes"),
    }
    errors = []
    if not values["name"]:
        errors.append("Enter a name for the recipe.")
    servings = number(values["servings"])
    if servings is None or servings > 100:
        errors.append("Enter how many servings it makes.")
    cooked = number(values["cooked_grams"]) if values["cooked_grams"] else None
    if values["cooked_grams"] and (cooked is None or cooked > 50000):
        errors.append("Enter the cooked weight in grams, or leave it blank.")
    values.update(servings=servings, cooked_grams=cooked)
    return values, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def db() -> Database:
        return web.db

    def chosen_food(ref: str):
        kind, _, ident = ref.partition(":")
        return foods.get(db(), int(ident)) if kind == "food" and ident.isdigit() else None

    def recipe_row(recipe_id: int) -> dict:
        found = get(db(), recipe_id)
        if not found:
            raise Redirect("/recipes?err=missing")
        return found

    def panel(request: Request, recipe: dict, status_code: int = 200, error: str = "", chosen=None, **extra):
        found = summary(db(), recipe)
        context = {
            "recipe": recipe,
            "summary": found,
            "salt": salt(found["per_serving"]),
            "nutrients": NUTRIENTS,
            "summary_keys": SUMMARY,
            "chosen": chosen,
            "portions": db().query("SELECT * FROM portion WHERE food_id = ? ORDER BY grams", (chosen["id"],)) if chosen else [],
            "error": error,
            **extra,
        }
        return context

    @r.get("/recipes")
    def index(request: Request, q: str = "", archived: int = 0):
        rows = db().query("SELECT * FROM recipe WHERE archived = ? ORDER BY name COLLATE NOCASE", (1 if archived else 0,))
        words = [w for w in q.lower().split() if w]
        listed = []
        for row in rows:
            if words and not all(w in f"{row['name']} {row['tags']}".lower() for w in words):
                continue
            found = summary(db(), dict(row))
            listed.append(dict(row, per_serving=found["per_serving"], count=len(found["items"])))
        return web.page(request, "recipes.html", recipes=listed, q=q, archived=archived, section="recipes")

    @r.post("/recipes/new")
    def create(form: FormData = Depends(form_data)):
        name = text(form, "name") or "New recipe"
        servings = number(text(form, "servings")) or 1
        now = now_iso()
        recipe_id = db().insert(
            "INSERT INTO recipe(name, servings, created_at, updated_at) VALUES(?, ?, ?, ?)", (name, min(servings, 100), now, now)
        )
        web.log("recipe_created", f"recipe {recipe_id}")
        raise Redirect(f"/recipes/{recipe_id}")

    @r.get("/recipes/{recipe_id}")
    def show(request: Request, recipe_id: int, ref: str = ""):
        recipe = recipe_row(recipe_id)
        chosen = chosen_food(ref)
        return web.page(
            request, "recipe.html", section="recipes", values=recipe, errors=[], used=used_in(db(), recipe_id),
            **panel(request, recipe, chosen=chosen),
        )

    @r.post("/recipes/{recipe_id}")
    def save(request: Request, recipe_id: int, form: FormData = Depends(form_data)):
        recipe = recipe_row(recipe_id)
        values, errors = read_details(form)
        if errors:
            return web.page(
                request, "recipe.html", status_code=400, section="recipes", values=values, errors=errors,
                used=used_in(db(), recipe_id), **panel(request, recipe),
            )
        db().execute(
            "UPDATE recipe SET name = ?, servings = ?, cooked_grams = ?, tags = ?, method = ?, notes = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["servings"], values["cooked_grams"], values["tags"], values["method"], values["notes"], now_iso(), recipe_id),
        )
        raise Redirect(f"/recipes/{recipe_id}?msg=saved")

    @r.get("/recipes/{recipe_id}/food-choices")
    def choices(request: Request, recipe_id: int, q: str = ""):
        recipe = recipe_row(recipe_id)
        return web.page(
            request, "_choices.html", q=q, choices=[dict(f, kind="food") for f in foods.search(db(), q, limit=15)],
            base=f"/recipes/{recipe['id']}", form_url=f"/recipes/{recipe['id']}/item-form", include="", keep={},
        )

    @r.get("/recipes/{recipe_id}/item-form")
    def item_form(request: Request, recipe_id: int, ref: str = ""):
        recipe = recipe_row(recipe_id)
        chosen = chosen_food(ref)
        return web.page(request, "_recipe_item_form.html", **panel(request, recipe, chosen=chosen))

    @r.post("/recipes/{recipe_id}/items")
    def add_item(request: Request, recipe_id: int, form: FormData = Depends(form_data)):
        recipe = recipe_row(recipe_id)
        food_id = int(text(form, "food_id") or 0)
        chosen = foods.get(db(), food_id) if food_id else None
        htmx = request.headers.get("HX-Request") == "true"
        if not chosen:
            error = "Choose a food first."
        else:
            try:
                grams, quantity, unit = read_amount(db(), food_id, text(form, "amount"), text(form, "unit"))
                error = ""
            except AmountProblem as exc:
                error = str(exc)
        if error:
            if not htmx:
                raise Redirect(f"/recipes/{recipe_id}?ref=food:{food_id}&err=entry")
            return web.page(request, "_recipe_items.html", status_code=400, **panel(request, recipe, error=error, chosen=chosen))
        position = db().one("SELECT coalesce(max(position), 0) + 1 FROM recipe_item WHERE recipe_id = ?", (recipe_id,))[0]
        db().execute(
            "INSERT INTO recipe_item(recipe_id, position, food_id, grams, quantity, unit, label) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (recipe_id, position, food_id, grams, quantity, unit, text(form, "label")),
        )
        db().execute("UPDATE recipe SET updated_at = ? WHERE id = ?", (now_iso(), recipe_id))
        if not htmx:
            raise Redirect(f"/recipes/{recipe_id}#ingredients")
        return web.page(request, "_recipe_items.html", **panel(request, recipe))

    @r.post("/recipes/{recipe_id}/items/{item_id}/delete")
    def delete_item(request: Request, recipe_id: int, item_id: int):
        recipe = recipe_row(recipe_id)
        db().execute("DELETE FROM recipe_item WHERE id = ? AND recipe_id = ?", (item_id, recipe_id))
        if request.headers.get("HX-Request") != "true":
            raise Redirect(f"/recipes/{recipe_id}#ingredients")
        return web.page(request, "_recipe_items.html", **panel(request, recipe))

    @r.post("/recipes/{recipe_id}/duplicate")
    def duplicate(recipe_id: int):
        recipe = recipe_row(recipe_id)
        now = now_iso()
        copy_id = db().insert(
            "INSERT INTO recipe(name, servings, cooked_grams, tags, method, notes, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (f"{recipe['name']} (copy)", recipe["servings"], recipe["cooked_grams"], recipe["tags"], recipe["method"], recipe["notes"], now, now),
        )
        db().execute(
            "INSERT INTO recipe_item(recipe_id, position, food_id, grams, quantity, unit, label) "
            "SELECT ?, position, food_id, grams, quantity, unit, label FROM recipe_item WHERE recipe_id = ?",
            (copy_id, recipe_id),
        )
        raise Redirect(f"/recipes/{copy_id}?msg=saved")

    @r.post("/recipes/{recipe_id}/archive")
    def archive(recipe_id: int):
        recipe = recipe_row(recipe_id)
        db().execute("UPDATE recipe SET archived = ?, updated_at = ? WHERE id = ?", (0 if recipe["archived"] else 1, now_iso(), recipe_id))
        raise Redirect(f"/recipes/{recipe_id}?msg=saved")

    @r.post("/recipes/{recipe_id}/delete")
    def delete(recipe_id: int):
        recipe_row(recipe_id)
        if used_in(db(), recipe_id):
            raise Redirect(f"/recipes/{recipe_id}?err=recipe-used")
        db().execute("DELETE FROM recipe WHERE id = ?", (recipe_id,))
        web.log("recipe_deleted", f"recipe {recipe_id}")
        raise Redirect("/recipes?msg=deleted")

    return r

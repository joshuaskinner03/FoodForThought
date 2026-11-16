from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from starlette.datastructures import FormData

from . import cofid, fmt
from .audit import now_iso
from .db import Database
from .nutrients import BY_KEY, GROUPS, NUTRIENTS, food_group_name
from .web import Redirect, Web, form_data, text

MAX_WORKBOOK = 40 * 1024 * 1024
SEARCH_LIMIT = 40
DOWNLOAD_PAGE = "https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid"
SELECT = "SELECT f.*, s.key AS source_key, s.name AS source_name FROM food f JOIN food_source s ON s.id = f.source_id "


def decode(row) -> dict:
    food = dict(row, nutrients=json.loads(row["nutrients"]), flags=json.loads(row["flags"]))
    food["group_name"] = food_group_name(row["food_group"])
    food["custom"] = row["source_key"] == "custom"
    return food


def get(db: Database, food_id: int) -> dict:
    row = db.one(SELECT + "WHERE f.id = ?", (food_id,))
    if row is None:
        raise HTTPException(404, "That food does not exist.")
    return decode(row)


def _rank(name: str, words: list[str]) -> tuple:
    lowered = name.lower()
    name_words = lowered.replace(",", " ").split()
    starts = lowered.startswith(words[0]) if words else False
    word_starts = sum(1 for word in words if any(part.startswith(word) for part in name_words))
    return (not starts, -word_starts, len(lowered), lowered)


def search(db: Database, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    words = [word for word in query.lower().replace(",", " ").split() if word][:6]
    if not words:
        return []
    clauses, params = [], []
    for word in words:
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("(f.name LIKE ? ESCAPE '\\' OR f.code LIKE ? ESCAPE '\\')")
        params += [f"%{escaped}%", f"{escaped}%"]
    rows = db.query(SELECT + "WHERE f.archived = 0 AND " + " AND ".join(clauses) + " LIMIT 400", tuple(params))
    ranked = sorted(rows, key=lambda row: (row["source_key"] != "custom", *_rank(row["name"], words)))
    return [decode(row) for row in ranked[:limit]]


def sources(db: Database) -> dict:
    found = {}
    for row in db.query(
        "SELECT s.*, (SELECT count(*) FROM food f WHERE f.source_id = s.id AND f.archived = 0) AS foods "
        "FROM food_source s"
    ):
        found[row["key"]] = dict(row, report=json.loads(row["report"]))
    return found


def store_cofid(db: Database, parsed: list, report: dict, file_name: str, digest: str) -> dict:
    now = now_iso()
    with db.transaction():
        source = db.one("SELECT id FROM food_source WHERE key = 'cofid'")
        source_id = source["id"] if source else db.insert(
            "INSERT INTO food_source(key, name) VALUES('cofid', 'CoFID 2021')"
        )
        existing = {row["code"]: row["id"] for row in db.query("SELECT id, code FROM food WHERE source_id = ?", (source_id,))}
        added = updated = 0
        for food in parsed:
            values = (food.name, food.description, food.group, json.dumps(food.nutrients), json.dumps(food.flags), now)
            if food.code in existing:
                db.execute(
                    "UPDATE food SET name = ?, description = ?, food_group = ?, nutrients = ?, flags = ?, "
                    "updated_at = ?, archived = 0 WHERE id = ?",
                    (*values, existing.pop(food.code)),
                )
                updated += 1
            else:
                db.execute(
                    "INSERT INTO food(name, description, food_group, nutrients, flags, updated_at, source_id, code, "
                    "created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*values, source_id, food.code, now),
                )
                added += 1
        for food_id in existing.values():
            db.execute("UPDATE food SET archived = 1, updated_at = ? WHERE id = ?", (now, food_id))
        report = dict(report, added=added, updated=updated, withdrawn=len(existing))
        db.execute(
            "UPDATE food_source SET attribution = ?, file_name = ?, file_sha256 = ?, imported_at = ?, report = ? "
            "WHERE id = ?",
            (cofid.ATTRIBUTION, file_name, digest, now, json.dumps(report), source_id),
        )
    return report


def read_custom(form: FormData) -> tuple[dict, list[str]]:
    values = {"name": text(form, "name", 200), "description": text(form, "description", 500), "nutrients": {}}
    errors = []
    if not values["name"]:
        errors.append("Give the food a name.")
    for nutrient in NUTRIENTS:
        raw = text(form, f"n_{nutrient.key}")
        if not raw:
            continue
        parsed = fmt.parse_decimal(raw)
        if parsed is None or not 0 <= parsed <= 100000:
            errors.append(f"{nutrient.label} must be a number, or left empty if not known.")
            values["nutrients"][nutrient.key] = raw
        else:
            values["nutrients"][nutrient.key] = float(parsed)
    if not errors and values["nutrients"].get("energy_kcal") is None:
        errors.append("Give at least the energy in kcal per 100 g.")
    return values, errors


def scaled(food: dict, grams: float) -> list[dict]:
    groups = []
    for group in GROUPS:
        rows = []
        for nutrient in NUTRIENTS:
            if nutrient.group != group or nutrient.key not in food["nutrients"]:
                continue
            value = food["nutrients"][nutrient.key]
            flag = food["flags"].get(nutrient.key, "")
            if value is None:
                shown = "not known"
            elif flag == "trace":
                shown = "trace"
            else:
                shown = f"{value * grams / 100:.{nutrient.places}f}"
            rows.append({"label": nutrient.label, "unit": nutrient.unit, "shown": shown, "estimated": flag == "estimated"})
        if rows:
            groups.append({"name": group, "rows": rows})
    return groups


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def custom_page(request: Request, values: dict, food, error: str | None = None):
        return web.page(
            request,
            "food_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            food=food,
            groups=[(group, [n for n in NUTRIENTS if n.group == group]) for group in GROUPS],
        )

    @r.get("/foods")
    def index(request: Request, q: str = ""):
        partial = bool(request.headers.get("hx-request"))
        return web.page(
            request,
            "_food_rows.html" if partial else "foods.html",
            foods=search(web.db, q),
            q=q,
            sources=sources(web.db),
            my_foods=[
                decode(row)
                for row in web.db.query(SELECT + "WHERE s.key = 'custom' ORDER BY f.archived, f.name COLLATE NOCASE")
            ],
        )

    @r.get("/foods/import")
    def import_form(request: Request):
        return web.page(request, "food_import.html", source=sources(web.db).get("cofid"), download_page=DOWNLOAD_PAGE)

    @r.post("/foods/import")
    def run_import(request: Request, file: UploadFile | None = File(None)):
        data = file.file.read(MAX_WORKBOOK + 1) if file is not None else b""
        problem = None
        if not data:
            problem = "Choose the CoFID Excel file first."
        elif len(data) > MAX_WORKBOOK:
            problem = "That file is larger than 40 MB, so it is not the CoFID workbook."
        else:
            try:
                parsed, report = cofid.parse(data)
            except cofid.ImportProblem as exc:
                problem = str(exc)
        if problem:
            return web.page(
                request,
                "food_import.html",
                status_code=400,
                error=problem + " Nothing was changed.",
                source=sources(web.db).get("cofid"),
                download_page=DOWNLOAD_PAGE,
            )
        report = store_cofid(web.db, parsed, report, (file.filename or "")[:200], hashlib.sha256(data).hexdigest())
        web.log("food_database_imported", f"{report['foods']} foods, {report['added']} new")
        raise Redirect("/foods/import?msg=imported")

    @r.get("/foods/new")
    def new_food(request: Request):
        return custom_page(request, {"nutrients": {}}, None)

    @r.post("/foods/new")
    def create_food(request: Request, form: FormData = Depends(form_data)):
        values, errors = read_custom(form)
        if errors:
            return custom_page(request, values, None, " ".join(errors))
        now = now_iso()
        source_id = web.db.one("SELECT id FROM food_source WHERE key = 'custom'")["id"]
        food_id = web.db.insert(
            "INSERT INTO food(source_id, name, description, nutrients, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)",
            (source_id, values["name"], values["description"], json.dumps(values["nutrients"]), now, now),
        )
        web.log("food_created", f"food {food_id}")
        raise Redirect(f"/foods/{food_id}?msg=saved")

    @r.get("/foods/{food_id}")
    def show(request: Request, food_id: int, grams: str = ""):
        food = get(web.db, food_id)
        amount = fmt.parse_decimal(grams)
        amount = float(amount) if amount is not None and 0 < amount <= 5000 else 100.0
        return web.page(
            request,
            "food.html",
            food=food,
            grams=amount,
            groups=scaled(food, amount),
            portions=web.db.query("SELECT * FROM portion WHERE food_id = ? ORDER BY grams", (food_id,)),
        )

    def custom_food(food_id: int) -> dict:
        food = get(web.db, food_id)
        if not food["custom"]:
            raise HTTPException(409, "Foods from the national database cannot be edited. Add your own food instead.")
        return food

    @r.get("/foods/{food_id}/edit")
    def edit_food(request: Request, food_id: int):
        food = custom_food(food_id)
        return custom_page(request, food, food)

    @r.post("/foods/{food_id}/edit")
    def update_food(request: Request, food_id: int, form: FormData = Depends(form_data)):
        food = custom_food(food_id)
        values, errors = read_custom(form)
        if errors:
            return custom_page(request, values, food, " ".join(errors))
        web.db.execute(
            "UPDATE food SET name = ?, description = ?, nutrients = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["description"], json.dumps(values["nutrients"]), now_iso(), food_id),
        )
        web.log("food_updated", f"food {food_id}")
        raise Redirect(f"/foods/{food_id}?msg=saved")

    @r.post("/foods/{food_id}/archive")
    def archive_food(food_id: int, form: FormData = Depends(form_data)):
        custom_food(food_id)
        archived = 0 if text(form, "restore") else 1
        web.db.execute("UPDATE food SET archived = ?, updated_at = ? WHERE id = ?", (archived, now_iso(), food_id))
        web.log("food_hidden" if archived else "food_restored", f"food {food_id}")
        raise Redirect(f"/foods/{food_id}?msg=saved")

    @r.post("/foods/{food_id}/portions")
    def add_portion(food_id: int, form: FormData = Depends(form_data)):
        get(web.db, food_id)
        name = text(form, "name", 100)
        grams = fmt.parse_decimal(text(form, "grams"))
        if not name or grams is None or not 0 < grams <= 5000:
            raise Redirect(f"/foods/{food_id}?err=portion")
        web.db.execute("INSERT INTO portion(food_id, name, grams) VALUES(?, ?, ?)", (food_id, name, float(grams)))
        raise Redirect(f"/foods/{food_id}?msg=saved")

    @r.post("/foods/{food_id}/portions/{portion_id}/delete")
    def delete_portion(food_id: int, portion_id: int):
        web.db.execute("DELETE FROM portion WHERE id = ? AND food_id = ?", (portion_id, food_id))
        raise Redirect(f"/foods/{food_id}?msg=deleted")

    return r


__all__ = ["BY_KEY", "router", "search", "get", "sources", "store_cofid"]

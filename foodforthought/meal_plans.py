from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import FormData

from . import analysis, clients, fmt, foods, pdf, practice, recipes, reference
from .audit import now_iso
from .db import Database
from .nutrients import food_group_name
from .recipes import AmountProblem, amount_text, number, read_amount
from .web import Redirect, Web, form_data, text

MEALS = ("Breakfast", "Morning snack", "Lunch", "Afternoon snack", "Evening meal", "Evening snack")
MAX_DAYS = 14
DAY_TOTALS = ("energy_kcal", "protein", "fat", "carbohydrate", "fibre")


def servings_text(servings: float) -> str:
    return f"{fmt.plain(servings)} serving{'' if servings == 1 else 's'}"


def day_labels(plan: dict) -> list[dict]:
    start = fmt.parse_date(plan["start_date"]) if plan["start_date"] else None
    labels = []
    for n in range(1, plan["days"] + 1):
        when = (start + timedelta(days=n - 1)).isoformat() if start else None
        labels.append({"n": n, "label": f"Day {n}", "date": when, "long": f"Day {n}, {fmt.long_date(when)}" if when else f"Day {n}"})
    return labels


def plan_items(db: Database, plan_id: int) -> list[dict]:
    rows = db.query(
        "SELECT i.*, r.name AS recipe_name, r.servings AS recipe_servings, f.name AS food_name, f.nutrients "
        "FROM meal_plan_item i LEFT JOIN recipe r ON r.id = i.recipe_id LEFT JOIN food f ON f.id = i.food_id "
        "WHERE i.plan_id = ? ORDER BY i.day, i.position, i.id",
        (plan_id,),
    )
    cache: dict[int, dict] = {}
    found = []
    for row in rows:
        item = dict(row)
        if item["recipe_id"]:
            if item["recipe_id"] not in cache:
                cache[item["recipe_id"]] = recipes.summary(db, recipes.get(db, item["recipe_id"]))
            info = cache[item["recipe_id"]]
            factor = item["servings"] / (item["recipe_servings"] or 1)
            item.update(
                kind="recipe",
                name=item["label"] or item["recipe_name"],
                amount=servings_text(item["servings"]),
                parts=[{"food_id": i["food_id"], "food_name": i["food_name"], "food_group": i["food_group"], "grams": i["grams"] * factor, "nutrients": i["nutrients"]} for i in info["items"]],
            )
        else:
            item.update(
                kind="food",
                name=item["label"] or item["food_name"],
                amount=amount_text(item["grams"], item["quantity"], item["unit"]),
                parts=[{"food_id": item["food_id"], "food_name": item["food_name"], "food_group": "", "grams": item["grams"], "nutrients": item["nutrients"]}],
            )
        values = recipes.totals(item["parts"])
        item["kcal"] = values["energy_kcal"]["value"]
        item["protein"] = values["protein"]["value"]
        found.append(item)
    return found


def entries(found: list[dict]) -> list[dict]:
    return [{"day": f"{item['day']:02d}", "grams": part["grams"], "nutrients": part["nutrients"], "name": part["food_name"]} for item in found for part in item["parts"]]


def by_day(plan: dict, found: list[dict]) -> list[dict]:
    days = []
    for label in day_labels(plan):
        day_items = [i for i in found if i["day"] == label["n"]]
        values = recipes.totals([p for i in day_items for p in i["parts"]])
        meals = [{"name": meal, "items": [i for i in day_items if i["meal"] == meal]} for meal in MEALS]
        days.append(dict(label, meals=meals, count=len(day_items), totals={k: values[k] for k in DAY_TOTALS}))
    return days


def shopping(db: Database, found: list[dict]) -> list[dict]:
    totals: dict[int, dict] = {}
    for item in found:
        for part in item["parts"]:
            row = totals.setdefault(part["food_id"], {"food_id": part["food_id"], "name": part["food_name"], "grams": 0.0})
            row["grams"] += part["grams"]
    if not totals:
        return []
    ids = list(totals)
    marks = ",".join("?" * len(ids))
    groups = {row["id"]: row["food_group"] for row in db.query(f"SELECT id, food_group FROM food WHERE id IN ({marks})", ids)}
    portions: dict[int, dict] = {}
    for row in db.query(f"SELECT * FROM portion WHERE food_id IN ({marks}) ORDER BY grams", ids):
        portions.setdefault(row["food_id"], row)
    grouped: dict[str, list] = {}
    for food_id, row in totals.items():
        grams = row["grams"]
        shown = f"{round(grams / 5) * 5:.0f} g" if grams >= 50 else f"{grams:.0f} g"
        portion = portions.get(food_id)
        if portion and portion["grams"] > 0 and grams / portion["grams"] >= 0.5:
            count = round(grams / portion["grams"] * 2) / 2
            name = portion["name"][2:] if portion["name"].startswith("1 ") else portion["name"]
            shown += f" (about {fmt.plain(count)} × {name})"
        group = food_group_name(groups.get(food_id, "")) or "Other"
        grouped.setdefault(group, []).append({"name": row["name"], "grams": grams, "shown": shown})
    return [{"name": name, "rows": sorted(rows, key=lambda r: r["name"].lower())} for name, rows in sorted(grouped.items(), key=lambda g: (g[0] == "Other", g[0]))]


def used_recipes(db: Database, found: list[dict]) -> list[dict]:
    seen = []
    for item in found:
        if item["recipe_id"] and item["recipe_id"] not in [r["id"] for r in seen]:
            recipe = recipes.get(db, item["recipe_id"])
            seen.append(dict(recipe, summary=recipes.summary(db, recipe)))
    return seen


def copy_plan(db: Database, plan: dict, client_id: int | None, title: str) -> int:
    now = now_iso()
    new_id = db.insert(
        "INSERT INTO meal_plan(client_id, title, days, start_date, intro, show_figures, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (client_id, title, plan["days"], plan["start_date"] if client_id else None, plan["intro"], plan["show_figures"], now, now),
    )
    db.execute(
        "INSERT INTO meal_plan_item(plan_id, day, meal, position, recipe_id, food_id, servings, grams, quantity, unit, label) "
        "SELECT ?, day, meal, position, recipe_id, food_id, servings, grams, quantity, unit, label FROM meal_plan_item WHERE plan_id = ?",
        (new_id, plan["id"]),
    )
    return new_id


def read_settings(form: FormData) -> tuple[dict, list[str]]:
    values = {
        "title": text(form, "title"),
        "days": text(form, "days"),
        "start_date": text(form, "start_date"),
        "intro": text(form, "intro"),
        "show_figures": 1 if form.get("show_figures") else 0,
    }
    errors = []
    if not values["title"]:
        errors.append("Enter a title.")
    days = int(values["days"]) if values["days"].isdigit() else 0
    if not 1 <= days <= MAX_DAYS:
        errors.append(f"A plan covers 1 to {MAX_DAYS} days.")
    start = fmt.parse_date(values["start_date"]) if values["start_date"] else None
    if values["start_date"] and not start:
        errors.append("Enter the start date as dd/mm/yyyy, or leave it blank.")
    values.update(days=days, start_date=start.isoformat() if start else None)
    return values, errors


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def db() -> Database:
        return web.db

    def plan_row(plan_id: int) -> dict:
        row = db().one("SELECT * FROM meal_plan WHERE id = ?", (plan_id,))
        if not row:
            raise HTTPException(404, "That meal plan does not exist.")
        return dict(row)

    def chosen(ref: str):
        kind, _, ident = ref.partition(":")
        if not ident.isdigit():
            return None
        if kind == "recipe":
            recipe = recipes.get(db(), int(ident))
            return dict(recipe, kind="recipe", per_serving=recipes.summary(db(), recipe)["per_serving"]) if recipe else None
        if kind == "food":
            return dict(foods.get(db(), int(ident)), kind="food")
        return None

    def target_for(plan: dict):
        if not plan["client_id"]:
            return None
        return db().one("SELECT * FROM target WHERE client_id = ? ORDER BY set_on DESC, id DESC LIMIT 1", (plan["client_id"],))

    def shell(plan: dict) -> dict:
        if plan["client_id"]:
            return {"layout": "client_base.html", **clients.context(web, plan["client_id"], "meal-plans")}
        return {"layout": "template_base.html", "section": "recipes"}

    def panel(plan: dict, day: int = 1, meal: str = "Breakfast", pick=None, error: str = "") -> dict:
        found = plan_items(db(), plan["id"])
        return {
            "plan": plan,
            "days": by_day(plan, found),
            "labels": day_labels(plan),
            "meals": MEALS,
            "day": day if 1 <= day <= plan["days"] else 1,
            "meal": meal if meal in MEALS else MEALS[0],
            "chosen": pick,
            "portions": db().query("SELECT * FROM portion WHERE food_id = ? ORDER BY grams", (pick["id"],)) if pick and pick["kind"] == "food" else [],
            "target": target_for(plan),
            "error": error,
        }

    def plan_url(plan: dict) -> str:
        return f"/meal-plans/{plan['id']}"

    def back_url(plan: dict) -> str:
        return f"/clients/{plan['client_id']}/meal-plans" if plan["client_id"] else "/plan-templates"

    def templates() -> list:
        return db().query("SELECT * FROM meal_plan WHERE client_id IS NULL ORDER BY title COLLATE NOCASE")

    @r.get("/clients/{client_id}/meal-plans")
    def client_plans(request: Request, client_id: int, err: str = ""):
        plans = db().query(
            "SELECT p.*, (SELECT count(*) FROM meal_plan_item i WHERE i.plan_id = p.id) AS items FROM meal_plan p "
            "WHERE p.client_id = ? ORDER BY p.created_at DESC",
            (client_id,),
        )
        return web.page(
            request, "client_meal_plans.html", plans=plans, templates=templates(), max_days=MAX_DAYS,
            **clients.context(web, client_id, "meal-plans"),
        )

    @r.post("/clients/{client_id}/meal-plans/new")
    def new_client_plan(client_id: int, form: FormData = Depends(form_data)):
        clients.get(db(), client_id)
        template_id = text(form, "template")
        if template_id.isdigit():
            template = plan_row(int(template_id))
            plan_id = copy_plan(db(), template, client_id, text(form, "title") or template["title"])
            start = fmt.parse_date(text(form, "start_date")) if text(form, "start_date") else None
            db().execute("UPDATE meal_plan SET start_date = ? WHERE id = ?", (start.isoformat() if start else None, plan_id))
        else:
            values, errors = read_settings(form)
            if errors:
                raise Redirect(f"/clients/{client_id}/meal-plans?err=plan-days")
            plan_id = create(client_id, values)
        web.log("meal_plan_created", f"plan {plan_id}", client_id)
        raise Redirect(f"/meal-plans/{plan_id}")

    def create(client_id: int | None, values: dict) -> int:
        now = now_iso()
        return db().insert(
            "INSERT INTO meal_plan(client_id, title, days, start_date, intro, show_figures, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (client_id, values["title"], values["days"], values["start_date"], values["intro"], values["show_figures"], now, now),
        )

    @r.get("/plan-templates")
    def template_list(request: Request):
        rows = db().query(
            "SELECT p.*, (SELECT count(*) FROM meal_plan_item i WHERE i.plan_id = p.id) AS items FROM meal_plan p "
            "WHERE p.client_id IS NULL ORDER BY p.title COLLATE NOCASE"
        )
        return web.page(request, "plan_templates.html", plans=rows, max_days=MAX_DAYS, section="recipes")

    @r.post("/plan-templates/new")
    def new_template(form: FormData = Depends(form_data)):
        values, errors = read_settings(form)
        if errors:
            raise Redirect("/plan-templates?err=plan-days")
        values["start_date"] = None
        raise Redirect(f"/meal-plans/{create(None, values)}")

    @r.get("/meal-plans/{plan_id}")
    def show(request: Request, plan_id: int, day: int = 1, meal: str = "Breakfast", ref: str = ""):
        plan = plan_row(plan_id)
        return web.page(request, "meal_plan.html", **shell(plan), **panel(plan, day, meal, chosen(ref)))

    @r.get("/meal-plans/{plan_id}/choices")
    def plan_choices(request: Request, plan_id: int, q: str = "", day: str = "1", meal: str = ""):
        plan = plan_row(plan_id)
        found = [dict(rc, kind="recipe") for rc in recipes.search(db(), q)] + [dict(f, kind="food") for f in foods.search(db(), q, limit=12)]
        return web.page(
            request, "_choices.html", q=q, choices=found, base=plan_url(plan), form_url=f"{plan_url(plan)}/item-form",
            include="#entry-day, #entry-meal", keep={"day": day, "meal": meal},
        )

    @r.get("/meal-plans/{plan_id}/item-form")
    def item_form(request: Request, plan_id: int, ref: str = "", day: int = 1, meal: str = "Breakfast"):
        plan = plan_row(plan_id)
        return web.page(request, "_plan_form.html", **panel(plan, day, meal, chosen(ref)))

    @r.post("/meal-plans/{plan_id}/items")
    def add_item(request: Request, plan_id: int, form: FormData = Depends(form_data)):
        plan = plan_row(plan_id)
        htmx = request.headers.get("HX-Request") == "true"
        day = int(text(form, "day")) if text(form, "day").isdigit() else 0
        meal = text(form, "meal")
        pick = chosen(text(form, "ref"))
        error = ""
        if not pick:
            error = "Choose a recipe or food first."
        elif not 1 <= day <= plan["days"] or meal not in MEALS:
            error = "Choose a day and a meal."
        elif pick["kind"] == "recipe":
            servings = number(text(form, "servings") or "1")
            if servings is None or servings > 20:
                error = "Enter the number of servings."
        else:
            try:
                grams, quantity, unit = read_amount(db(), pick["id"], text(form, "amount"), text(form, "unit"))
            except AmountProblem as exc:
                error = str(exc)
        if error:
            if not htmx:
                raise Redirect(f"{plan_url(plan)}?err=entry")
            return web.page(request, "_plan_panel.html", status_code=400, **panel(plan, day, meal, pick, error))
        position = db().one("SELECT coalesce(max(position), 0) + 1 FROM meal_plan_item WHERE plan_id = ? AND day = ?", (plan_id, day))[0]
        if pick["kind"] == "recipe":
            db().execute(
                "INSERT INTO meal_plan_item(plan_id, day, meal, position, recipe_id, servings, label) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (plan_id, day, meal, position, pick["id"], servings, text(form, "label")),
            )
        else:
            db().execute(
                "INSERT INTO meal_plan_item(plan_id, day, meal, position, food_id, grams, quantity, unit, label) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (plan_id, day, meal, position, pick["id"], grams, quantity, unit, text(form, "label")),
            )
        db().execute("UPDATE meal_plan SET updated_at = ? WHERE id = ?", (now_iso(), plan_id))
        if not htmx:
            raise Redirect(f"{plan_url(plan)}?day={day}&meal={meal}")
        return web.page(request, "_plan_panel.html", **panel(plan, day, meal))

    @r.post("/meal-plans/{plan_id}/items/{item_id}/delete")
    def delete_item(request: Request, plan_id: int, item_id: int):
        plan = plan_row(plan_id)
        row = db().one("SELECT day, meal FROM meal_plan_item WHERE id = ? AND plan_id = ?", (item_id, plan_id))
        db().execute("DELETE FROM meal_plan_item WHERE id = ? AND plan_id = ?", (item_id, plan_id))
        day, meal = (row["day"], row["meal"]) if row else (1, MEALS[0])
        if request.headers.get("HX-Request") != "true":
            raise Redirect(f"{plan_url(plan)}?day={day}&meal={meal}")
        return web.page(request, "_plan_panel.html", **panel(plan, day, meal))

    @r.post("/meal-plans/{plan_id}/copy-day")
    def copy_day(plan_id: int, form: FormData = Depends(form_data)):
        plan = plan_row(plan_id)
        source, target = text(form, "from"), text(form, "to")
        if not (source.isdigit() and target.isdigit()) or source == target or not all(1 <= int(d) <= plan["days"] for d in (source, target)):
            raise Redirect(f"{plan_url(plan)}?err=copy-day")
        if form.get("replace"):
            db().execute("DELETE FROM meal_plan_item WHERE plan_id = ? AND day = ?", (plan_id, int(target)))
        offset = db().one("SELECT coalesce(max(position), 0) FROM meal_plan_item WHERE plan_id = ? AND day = ?", (plan_id, int(target)))[0]
        db().execute(
            "INSERT INTO meal_plan_item(plan_id, day, meal, position, recipe_id, food_id, servings, grams, quantity, unit, label) "
            "SELECT plan_id, ?, meal, position + ?, recipe_id, food_id, servings, grams, quantity, unit, label FROM meal_plan_item WHERE plan_id = ? AND day = ?",
            (int(target), offset, plan_id, int(source)),
        )
        raise Redirect(f"{plan_url(plan)}?day={target}&msg=day-copied#day-{target}")

    @r.get("/meal-plans/{plan_id}/settings")
    def settings(request: Request, plan_id: int):
        plan = plan_row(plan_id)
        values = dict(plan, start_date=plan["start_date"] or "")
        return web.page(request, "meal_plan_settings.html", values=values, errors=[], max_days=MAX_DAYS, **shell(plan), plan=plan)

    @r.post("/meal-plans/{plan_id}/settings")
    def save_settings(request: Request, plan_id: int, form: FormData = Depends(form_data)):
        plan = plan_row(plan_id)
        values, errors = read_settings(form)
        if errors:
            shown = dict(values, start_date=text(form, "start_date"), days=text(form, "days"))
            return web.page(request, "meal_plan_settings.html", status_code=400, values=shown, errors=errors, max_days=MAX_DAYS, **shell(plan), plan=plan)
        if not plan["client_id"]:
            values["start_date"] = None
        db().execute(
            "UPDATE meal_plan SET title = ?, days = ?, start_date = ?, intro = ?, show_figures = ?, updated_at = ? WHERE id = ?",
            (values["title"], values["days"], values["start_date"], values["intro"], values["show_figures"], now_iso(), plan_id),
        )
        db().execute("DELETE FROM meal_plan_item WHERE plan_id = ? AND day > ?", (plan_id, values["days"]))
        raise Redirect(f"{plan_url(plan)}?msg=saved")

    @r.post("/meal-plans/{plan_id}/delete")
    def delete(plan_id: int):
        plan = plan_row(plan_id)
        db().execute("DELETE FROM meal_plan WHERE id = ?", (plan_id,))
        web.log("meal_plan_deleted", f"plan {plan_id}", plan["client_id"])
        raise Redirect(f"{back_url(plan)}?msg=deleted")

    @r.post("/meal-plans/{plan_id}/save-template")
    def save_template(plan_id: int):
        plan = plan_row(plan_id)
        new_id = copy_plan(db(), plan, None, plan["title"])
        raise Redirect(f"/meal-plans/{new_id}?msg=template-saved")

    @r.get("/meal-plans/{plan_id}/nutrition")
    def nutrition(request: Request, plan_id: int):
        plan = plan_row(plan_id)
        found = plan_items(db(), plan_id)
        group = weight = None
        age = None
        if plan["client_id"]:
            client = clients.get(db(), plan["client_id"])
            age = analysis.age_on(client["date_of_birth"], plan["start_date"] or date.today().isoformat())
            group = reference.group_for(client["sex"], age, "")
            row = db().one(
                "SELECT weight_kg FROM measurement WHERE client_id = ? AND weight_kg IS NOT NULL ORDER BY measured_on DESC, id DESC LIMIT 1",
                (plan["client_id"],),
            )
            weight = row["weight_kg"] if row else None
        target = target_for(plan)
        result = analysis.analyse(entries(found), [f"{n:02d}" for n in range(1, plan["days"] + 1)], group, weight, target)
        return web.page(
            request, "meal_plan_nutrition.html", result=result, target=target, plan=plan, plan_age=age,
            days=by_day(plan, found), states=analysis.STATES, **shell(plan),
        )

    @r.get("/meal-plans/{plan_id}/shopping")
    def shopping_list(request: Request, plan_id: int):
        plan = plan_row(plan_id)
        return web.page(request, "meal_plan_shopping.html", plan=plan, groups=shopping(db(), plan_items(db(), plan_id)), **shell(plan))

    def build_pdf(plan: dict, form) -> bytes:
        found = plan_items(db(), plan["id"])
        client_name = clients.full_name(clients.get(db(), plan["client_id"])) if plan["client_id"] else ""
        return pdf.meal_plan_pdf(
            practice.load(db()),
            practice.logo_bytes(web),
            plan,
            client_name,
            by_day(plan, found),
            used_recipes(db(), found) if form.get("recipes") else [],
            shopping(db(), found) if form.get("shopping") else [],
            bool(form.get("figures")),
        )

    @r.get("/meal-plans/{plan_id}/pdf")
    def download_pdf(request: Request, plan_id: int):
        plan = plan_row(plan_id)
        data = build_pdf(plan, request.query_params)
        web.log("meal_plan_pdf", f"plan {plan_id}", plan["client_id"])
        filename = f"meal-plan-{plan_id}.pdf"
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @r.post("/meal-plans/{plan_id}/pdf/save")
    def save_pdf(plan_id: int, form: FormData = Depends(form_data)):
        plan = plan_row(plan_id)
        if not plan["client_id"]:
            raise Redirect(plan_url(plan))
        data = build_pdf(plan, form)
        file_id = web.core.attachments.put(data)
        db().execute(
            "INSERT INTO attachment(client_id, file_id, filename, size, description, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (plan["client_id"], file_id, f"Meal plan - {clients._safe_filename(plan['title'])}.pdf", len(data), f"Meal plan: {plan['title']}", now_iso()),
        )
        web.log("meal_plan_pdf_saved", f"plan {plan_id}", plan["client_id"])
        raise Redirect(f"{plan_url(plan)}?msg=pdf-saved")

    return r

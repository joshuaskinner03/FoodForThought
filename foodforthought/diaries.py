from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from starlette.datastructures import FormData

from . import analysis, clients, diary_template, fmt, foods, matching, reference
from .audit import now_iso
from .diary_import import MEALS, DiaryImportProblem, IMPORTERS, detect
from .web import Redirect, Web, form_data, text

MAX_DAYS = 14
CLOSE_MATCH = 60
MAX_DIARY_FILE = 5 * 1024 * 1024
SOURCES = {"manual": "Typed in", "template": "Diary template", "cronometer": "Cronometer"}
METRIC_UNITS = {"", "g", "gram", "grams", "kg", "ml", "l", "litre", "liter", "oz", "lb"}


def entry_label(entry) -> str:
    if entry["unit"] in ("", "g") and entry["grams"] is not None:
        return f"{entry['grams']:g} g"
    if entry["quantity"] and entry["grams"] is not None:
        return f"{entry['quantity']:g} × {entry['unit']} ({entry['grams']:g} g)"
    return entry["source_amount"]


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])
    db = lambda: web.db  # noqa: E731

    def diary_row(diary_id: int) -> dict:
        row = db().one("SELECT * FROM food_diary WHERE id = ?", (diary_id,))
        if row is None:
            raise HTTPException(404, "That food diary does not exist.")
        return dict(row)

    def entry_row(diary_id: int, entry_id: int) -> dict:
        row = db().one("SELECT * FROM diary_entry WHERE id = ? AND diary_id = ?", (entry_id, diary_id))
        if row is None:
            raise HTTPException(404, "That diary entry does not exist.")
        return dict(row)

    def declared_days(diary: dict) -> list[str]:
        start = fmt.parse_date(diary["start_date"]) or date.today()
        return [(start + timedelta(days=offset)).isoformat() for offset in range(diary["days"])]

    def day_options(diary: dict) -> list[dict]:
        used = {row["day"] for row in db().query("SELECT DISTINCT day FROM diary_entry WHERE diary_id = ?", (diary["id"],))}
        days = sorted(set(declared_days(diary)) | used)
        return [
            {"value": day, "label": f"Day {index}, {fmt.parse_date(day):%a} {fmt.nice_date(day)}"}
            for index, day in enumerate(days, 1)
        ]

    def counts(diary_id: int) -> dict:
        found = {"pending": 0, "confirmed": 0, "skipped": 0}
        for row in db().query("SELECT status, count(*) AS n FROM diary_entry WHERE diary_id = ? GROUP BY status", (diary_id,)):
            found[row["status"]] = row["n"]
        return found

    def entries_by_day(diary: dict) -> list[dict]:
        rows = db().query(
            "SELECT e.*, f.name AS food_name, f.nutrients FROM diary_entry e LEFT JOIN food f ON f.id = e.food_id "
            "WHERE e.diary_id = ? ORDER BY e.day, e.position, e.id",
            (diary["id"],),
        )
        options = {option["value"]: option["label"] for option in day_options(diary)}
        meal_order = {meal: index for index, meal in enumerate(MEALS)}
        grouped: dict[str, dict] = {}
        for row in rows:
            item = dict(row)
            nutrients = json.loads(row["nutrients"]) if row["nutrients"] else {}
            item["kcal"] = (nutrients.get("energy_kcal") or 0) * row["grams"] / 100 if row["grams"] is not None else None
            item["label"] = entry_label(row)
            day = grouped.setdefault(row["day"], {"day": row["day"], "label": options.get(row["day"], row["day"]), "meals": {}, "kcal": 0.0})
            day["meals"].setdefault(row["meal"] or "Other", []).append(item)
            if row["status"] == "confirmed" and item["kcal"]:
                day["kcal"] += item["kcal"]
        for day in grouped.values():
            day["meals"] = sorted(day["meals"].items(), key=lambda pair: meal_order.get(pair[0], len(MEALS)))
        return [grouped[key] for key in sorted(grouped)]

    def photos(diary: dict) -> tuple[list[dict], list[dict]]:
        own, other = [], []
        for row in db().query("SELECT * FROM attachment WHERE client_id = ? ORDER BY id", (diary["client_id"],)):
            suffix = Path(row["filename"]).suffix.lower()
            item = dict(row, image=suffix in clients.IMAGE_TYPES, pdf=suffix == ".pdf")
            if item["image"] or item["pdf"]:
                (own if row["diary_id"] == diary["id"] else other).append(item)
        return own, other[::-1]

    def entry_form_context(diary: dict, food_id: str, day: str, meal: str) -> dict:
        chosen = None
        portions = []
        if food_id.isdigit():
            chosen = foods.get(db(), int(food_id))
            portions = db().query("SELECT * FROM portion WHERE food_id = ? ORDER BY grams", (chosen["id"],))
        days = day_options(diary)
        return {
            "diary": diary,
            "chosen": chosen,
            "portions": portions,
            "days": days,
            "meals": MEALS,
            "day": day if day in {d["value"] for d in days} else (days[0]["value"] if days else ""),
            "meal": meal if meal in MEALS else "Breakfast",
        }

    def panel(request: Request, diary: dict, day: str = "", meal: str = "", food_id: str = "", error: str | None = None, status_code: int = 200):
        return web.page(
            request,
            "_entry_panel.html",
            status_code=status_code,
            error=error,
            day_groups=entries_by_day(diary),
            counts=counts(diary["id"]),
            **entry_form_context(diary, food_id, day, meal),
        )

    def is_htmx(request: Request) -> bool:
        return bool(request.headers.get("hx-request"))

    @r.get("/clients/{client_id}/diaries")
    def client_tab(request: Request, client_id: int):
        return tab_page(request, client_id)

    def tab_page(request: Request, client_id: int, error: str | None = None, status_code: int = 200):
        rows = db().query("SELECT * FROM food_diary WHERE client_id = ? ORDER BY start_date DESC, id DESC", (client_id,))
        diaries = []
        for row in rows:
            item = dict(row, counts=counts(row["id"]), source_label=SOURCES.get(row["source"], row["source"]))
            item["end_date"] = declared_days(item)[-1]
            diaries.append(item)
        today = date.today()
        return web.page(
            request,
            "client_diaries.html",
            status_code=status_code,
            error=error,
            diaries=diaries,
            importers=IMPORTERS,
            next_monday=(today + timedelta(days=(7 - today.weekday()) % 7 or 7)).isoformat(),
            **clients.context(web, client_id, "diaries"),
        )

    def read_days(form: FormData) -> tuple[str, int, list[str]]:
        errors = []
        start = fmt.parse_date(text(form, "start_date"))
        raw_days = text(form, "days")
        if start is None:
            errors.append("Give a valid first day.")
        if not raw_days.isdigit() or not 1 <= int(raw_days) <= MAX_DAYS:
            errors.append(f"The number of days must be between 1 and {MAX_DAYS}.")
        return (start.isoformat() if start else ""), (int(raw_days) if raw_days.isdigit() else 0), errors

    @r.post("/clients/{client_id}/diaries/new")
    def create(client_id: int, form: FormData = Depends(form_data)):
        clients.get(db(), client_id)
        start, days, errors = read_days(form)
        if errors:
            raise Redirect(f"/clients/{client_id}/diaries?err=diary-days")
        now = now_iso()
        diary_id = db().insert(
            "INSERT INTO food_diary(client_id, title, source, start_date, days, created_at, updated_at) "
            "VALUES(?, ?, 'manual', ?, ?, ?, ?)",
            (client_id, text(form, "title", 200) or "Food diary", start, days, now, now),
        )
        web.log("diary_created", f"diary {diary_id}", client_id)
        raise Redirect(f"/diaries/{diary_id}")

    @r.get("/clients/{client_id}/diaries/template")
    def template(client_id: int, start: str = "", days: str = "3"):
        clients.get(db(), client_id)
        first = fmt.parse_date(start) or date.today()
        count = int(days) if days.isdigit() and 1 <= int(days) <= MAX_DAYS else 3
        data = diary_template.build(first, count)
        web.log("diary_template_downloaded", f"{count} days from {first.isoformat()}", client_id)
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="food-diary.xlsx"'},
        )

    @r.post("/clients/{client_id}/diaries/import")
    def import_diary(request: Request, client_id: int, file: UploadFile | None = File(None), title: str = Form("")):
        clients.get(db(), client_id)
        data = file.file.read(MAX_DIARY_FILE + 1) if file is not None else b""
        filename = (file.filename or "") if file is not None else ""
        try:
            if not data:
                raise DiaryImportProblem("Choose the file first.")
            if len(data) > MAX_DIARY_FILE:
                raise DiaryImportProblem("That file is larger than 5 MB, so it is not a food diary.")
            importer = detect(filename, data)
            raw = importer.parse(filename, data)
            if not raw:
                raise DiaryImportProblem("No foods were found in that file. Has the diary been filled in?")
            days = sorted({entry.day for entry in raw})
            if (days[-1] - days[0]).days >= MAX_DAYS * 2:
                raise DiaryImportProblem(
                    f"That file covers {days[0]:%d/%m/%Y} to {days[-1]:%d/%m/%Y}. Export a shorter period, "
                    f"up to {MAX_DAYS} days."
                )
        except DiaryImportProblem as exc:
            return tab_page(request, client_id, f"{exc} Nothing was imported.", 400)
        index = matching.FoodIndex(db())
        now = now_iso()
        with db().transaction():
            diary_id = db().insert(
                "INSERT INTO food_diary(client_id, title, source, start_date, days, created_at, updated_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (client_id, title.strip()[:200] or f"{importer.label} diary", importer.key, days[0].isoformat(), (days[-1] - days[0]).days + 1, now, now),
            )
            for position, entry in enumerate(raw):
                db().execute(
                    "INSERT INTO diary_entry(diary_id, day, meal, position, quantity, unit, source_text, source_amount, "
                    "source_detail, app_kcal, status, suggestions, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        diary_id,
                        entry.day.isoformat(),
                        entry.meal,
                        position,
                        entry.quantity,
                        entry.unit,
                        entry.text,
                        entry.amount_text,
                        entry.detail,
                        entry.app_kcal,
                        matching.dumps(index.suggest(entry.text)),
                        now,
                    ),
                )
            web.log("diary_imported", f"diary {diary_id}: {len(raw)} entries from {importer.label}", client_id)
        raise Redirect(f"/diaries/{diary_id}/review?msg=diary-imported")

    @r.get("/diaries/{diary_id}")
    def show(request: Request, diary_id: int, day: str = "", meal: str = "", food: str = "", photo: str = ""):
        diary = dict(diary_row(diary_id), source_label="")
        diary["source_label"] = SOURCES.get(diary["source"], diary["source"])
        own, other = photos(diary)
        pages = [p for p in own if p["image"]]
        other_images = [p for p in other if p["image"]]
        chosen_photo = next((p for p in pages + other_images if str(p["id"]) == photo), pages[0] if pages else None)
        return web.page(
            request,
            "food_diary.html",
            pages=pages,
            other_images=other_images,
            pdfs=[p for p in own + other if p["pdf"]],
            photo=chosen_photo,
            day_groups=entries_by_day(diary),
            counts=counts(diary_id),
            **entry_form_context(diary, food, day, meal),
            **clients.context(web, diary["client_id"], "diaries"),
        )

    @r.get("/diaries/{diary_id}/food-choices")
    def food_choices(request: Request, diary_id: int, q: str = "", day: str = "", meal: str = ""):
        diary = diary_row(diary_id)
        return web.page(request, "_food_choices.html", diary=diary, choices=foods.search(db(), q, limit=15), q=q, day=day, meal=meal)

    @r.get("/diaries/{diary_id}/entry-form")
    def entry_form(request: Request, diary_id: int, food: str = "", day: str = "", meal: str = ""):
        diary = diary_row(diary_id)
        return web.page(request, "_entry_form.html", **entry_form_context(diary, food, day, meal))

    @r.post("/diaries/{diary_id}/entries")
    def add_entry(request: Request, diary_id: int, form: FormData = Depends(form_data)):
        diary = diary_row(diary_id)
        day, meal, food_id = text(form, "day"), text(form, "meal"), text(form, "food_id")
        problem = None
        amount = fmt.parse_decimal(text(form, "amount"))
        unit = text(form, "unit") or "g"
        if not food_id.isdigit() or db().one("SELECT 1 FROM food WHERE id = ?", (int(food_id),)) is None:
            problem = "Choose a food first."
        elif fmt.parse_date(day) is None:
            problem = "Choose a day."
        elif amount is None or not 0 < amount <= 5000:
            problem = "Give an amount above zero."
        grams, quantity, unit_label = None, None, "g"
        if not problem:
            if unit.startswith("portion:") and unit[8:].isdigit():
                portion = db().one("SELECT * FROM portion WHERE id = ? AND food_id = ?", (int(unit[8:]), int(food_id)))
                if portion is None:
                    problem = "That portion no longer exists."
                else:
                    quantity, unit_label = float(amount), portion["name"]
                    grams = round(float(amount) * portion["grams"], 1)
            else:
                grams = float(amount)
            if grams is not None and grams > 5000:
                problem = "That comes to more than 5 kg. Check the amount."
        if problem:
            if is_htmx(request):
                return panel(request, diary, day, meal, food_id, error=problem, status_code=400)
            raise Redirect(f"/diaries/{diary_id}?day={day}&meal={meal}&food={food_id}&err=entry")
        position = db().one("SELECT coalesce(max(position), -1) + 1 FROM diary_entry WHERE diary_id = ?", (diary_id,))[0]
        db().execute(
            "INSERT INTO diary_entry(diary_id, day, meal, position, food_id, grams, quantity, unit, status, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)",
            (diary_id, day, meal if meal in MEALS else "Other", position, int(food_id), grams, quantity, unit_label, now_iso()),
        )
        db().execute("UPDATE food_diary SET updated_at = ? WHERE id = ?", (now_iso(), diary_id))
        if is_htmx(request):
            return panel(request, diary, day, meal)
        raise Redirect(f"/diaries/{diary_id}?day={day}&meal={meal}")

    @r.post("/diaries/{diary_id}/entries/{entry_id}/delete")
    def delete_entry(request: Request, diary_id: int, entry_id: int):
        diary = diary_row(diary_id)
        entry = entry_row(diary_id, entry_id)
        db().execute("DELETE FROM diary_entry WHERE id = ?", (entry_id,))
        if is_htmx(request):
            return panel(request, diary, entry["day"], entry["meal"])
        raise Redirect(f"/diaries/{diary_id}?day={entry['day']}&meal={entry['meal']}")

    @r.get("/diaries/{diary_id}/entries/{entry_id}/edit")
    def edit_entry_form(request: Request, diary_id: int, entry_id: int):
        diary = diary_row(diary_id)
        entry = entry_row(diary_id, entry_id)
        food = foods.get(db(), entry["food_id"]) if entry["food_id"] else None
        return web.page(
            request,
            "diary_entry_edit.html",
            diary=diary,
            entry=entry,
            food=food,
            days=day_options(diary),
            meals=MEALS,
            **clients.context(web, diary["client_id"], "diaries"),
        )

    @r.post("/diaries/{diary_id}/entries/{entry_id}/edit")
    def edit_entry(diary_id: int, entry_id: int, form: FormData = Depends(form_data)):
        diary_row(diary_id)
        entry = entry_row(diary_id, entry_id)
        day, meal = text(form, "day"), text(form, "meal")
        grams = fmt.parse_decimal(text(form, "grams"))
        if fmt.parse_date(day) is None or grams is None or not 0 < grams <= 5000:
            raise Redirect(f"/diaries/{diary_id}/entries/{entry_id}/edit?err=entry")
        quantity = entry["quantity"]
        if entry["unit"] not in ("", "g") and entry["grams"] and quantity:
            quantity = round(quantity * float(grams) / entry["grams"], 3)
        db().execute(
            "UPDATE diary_entry SET day = ?, meal = ?, grams = ?, quantity = ? WHERE id = ?",
            (day, meal if meal in MEALS else entry["meal"], float(grams), quantity, entry_id),
        )
        raise Redirect(f"/diaries/{diary_id}?day={day}&meal={meal}&msg=saved")

    @r.post("/diaries/{diary_id}/photo")
    def upload_photo(diary_id: int, file: UploadFile | None = File(None)):
        diary = diary_row(diary_id)
        data = file.file.read(clients.MAX_UPLOAD + 1) if file is not None else b""
        if not data:
            raise Redirect(f"/diaries/{diary_id}?err=no-file")
        if len(data) > clients.MAX_UPLOAD:
            raise Redirect(f"/diaries/{diary_id}?err=too-large")
        file_id = web.core.attachments.put(data)
        attachment_id = db().insert(
            "INSERT INTO attachment(client_id, diary_id, file_id, filename, size, description, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (diary["client_id"], diary_id, file_id, clients._safe_filename(file.filename), len(data), f"Food diary: {diary['title']}", now_iso()),
        )
        web.log("attachment_added", f"attachment {attachment_id}", diary["client_id"])
        raise Redirect(f"/diaries/{diary_id}?photo={attachment_id}&msg=uploaded")

    @r.get("/diaries/{diary_id}/settings")
    def settings_form(request: Request, diary_id: int):
        diary = diary_row(diary_id)
        return web.page(
            request,
            "diary_settings.html",
            diary=diary,
            life_stages=reference.LIFE_STAGES,
            max_days=MAX_DAYS,
            **clients.context(web, diary["client_id"], "diaries"),
        )

    @r.post("/diaries/{diary_id}/settings")
    def save_settings(request: Request, diary_id: int, form: FormData = Depends(form_data)):
        diary = diary_row(diary_id)
        start, days, errors = read_days(form)
        stage = text(form, "life_stage")
        if errors:
            return web.page(
                request,
                "diary_settings.html",
                status_code=400,
                error=" ".join(errors),
                diary=dict(diary, title=text(form, "title", 200), notes=text(form, "notes", 2000)),
                life_stages=reference.LIFE_STAGES,
                max_days=MAX_DAYS,
                **clients.context(web, diary["client_id"], "diaries"),
            )
        db().execute(
            "UPDATE food_diary SET title = ?, start_date = ?, days = ?, life_stage = ?, notes = ?, updated_at = ? WHERE id = ?",
            (
                text(form, "title", 200) or diary["title"],
                start,
                days,
                stage if stage in dict(reference.LIFE_STAGES) else "",
                text(form, "notes", 2000),
                now_iso(),
                diary_id,
            ),
        )
        web.log("diary_updated", f"diary {diary_id}", diary["client_id"])
        raise Redirect(f"/diaries/{diary_id}?msg=saved")

    @r.post("/diaries/{diary_id}/delete")
    def delete(diary_id: int):
        diary = diary_row(diary_id)
        db().execute("DELETE FROM food_diary WHERE id = ?", (diary_id,))
        web.log("diary_deleted", f"diary {diary_id}", diary["client_id"])
        raise Redirect(f"/clients/{diary['client_id']}/diaries?msg=deleted")

    def review_row(entry: dict, food_override: str = "", query: str = "") -> dict:
        suggestions = json.loads(entry["suggestions"])
        if query.strip():
            searched = foods.search(db(), query, limit=8)
            suggestions = [{"food_id": f["id"], "score": None} for f in searched]
        ids = [s["food_id"] for s in suggestions]
        if entry["food_id"] and entry["food_id"] not in ids:
            suggestions.insert(0, {"food_id": entry["food_id"], "score": None})
            ids.insert(0, entry["food_id"])
        names = {}
        if ids:
            marks = ", ".join("?" for _ in ids)
            names = {row["id"]: row["name"] for row in db().query(f"SELECT id, name FROM food WHERE id IN ({marks})", tuple(ids))}
        suggestions = [dict(s, name=names[s["food_id"]]) for s in suggestions if s["food_id"] in names]
        likely = suggestions[0] if suggestions and (suggestions[0].get("remembered") or (suggestions[0]["score"] or 100) >= CLOSE_MATCH) else None
        selected = int(food_override) if food_override.isdigit() and int(food_override) in names else (
            entry["food_id"] or (likely["food_id"] if likely else None)
        )
        if food_override.isdigit() and int(food_override) not in names and db().one("SELECT 1 FROM food WHERE id = ?", (int(food_override),)):
            selected = int(food_override)
            food = foods.get(db(), selected)
            suggestions.insert(0, {"food_id": selected, "score": None, "name": food["name"]})
        food = foods.get(db(), selected) if selected else None
        portions = db().query("SELECT * FROM portion WHERE food_id = ?", (selected,)) if selected else []
        estimate = matching.weight(entry["quantity"], entry["unit"], food, portions, entry["app_kcal"])
        if entry["status"] == "confirmed" and entry["grams"] is not None and not food_override:
            estimate = matching.Weight(entry["grams"], "confirmed")
        unit_word = matching.normal_unit(entry["unit"])
        offer_portion = bool(
            entry["quantity"]
            and unit_word
            and entry["unit"].lower().split()[0] not in METRIC_UNITS
            and estimate.how in ("unknown", "energy")
        )
        return {
            "entry": entry,
            "suggestions": suggestions,
            "selected": selected,
            "weak": bool(suggestions) and not selected,
            "food": food,
            "estimate": estimate,
            "offer_portion": offer_portion,
            "portion_name": f"1 {unit_word}" if unit_word else "",
            "query": query,
            "kcal": (food["nutrients"].get("energy_kcal") or 0) * entry["grams"] / 100 if food and entry["grams"] else None,
        }

    def row_response(request: Request, diary: dict, entry_id: int, food_override: str = "", query: str = "", error: str | None = None, status_code: int = 200):
        entry = entry_row(diary["id"], entry_id)
        return web.page(
            request,
            "_review_row.html",
            status_code=status_code,
            diary=diary,
            row=review_row(entry, food_override, query),
            row_error=error,
            day_label=fmt.nice_date(entry["day"]),
        )

    @r.get("/diaries/{diary_id}/review")
    def review(request: Request, diary_id: int, show: str = "pending"):
        diary = diary_row(diary_id)
        where = "" if show == "all" else "AND status = 'pending' "
        entries = [dict(row) for row in db().query(f"SELECT * FROM diary_entry WHERE diary_id = ? {where}ORDER BY day, position, id", (diary_id,))]
        rows = [review_row(entry) for entry in entries]
        ready = sum(1 for row in rows if row["entry"]["status"] == "pending" and row["suggestions"] and row["suggestions"][0].get("remembered") and row["estimate"].grams)
        return web.page(
            request,
            "diary_review.html",
            diary=dict(diary, source_label=SOURCES.get(diary["source"], diary["source"])),
            rows=rows,
            counts=counts(diary_id),
            show=show,
            ready=ready,
            **clients.context(web, diary["client_id"], "diaries"),
        )

    @r.get("/diaries/{diary_id}/entries/{entry_id}/row")
    def row(request: Request, diary_id: int, entry_id: int, food: str = "", q: str = ""):
        return row_response(request, diary_row(diary_id), entry_id, food, q)

    def finished_review(diary: dict) -> None:
        if counts(diary["id"])["pending"] == 0:
            web.log("diary_review_completed", f"diary {diary['id']}", diary["client_id"])

    @r.post("/diaries/{diary_id}/entries/{entry_id}/confirm")
    def confirm(request: Request, diary_id: int, entry_id: int, form: FormData = Depends(form_data)):
        diary = diary_row(diary_id)
        entry = entry_row(diary_id, entry_id)
        food_id = text(form, "food_id")
        grams = fmt.parse_decimal(text(form, "grams"))
        problem = None
        if not food_id.isdigit() or db().one("SELECT 1 FROM food WHERE id = ?", (int(food_id),)) is None:
            problem = "Choose a food."
        elif grams is None or not 0 < grams <= 5000:
            problem = "Enter the weight in grams."
        if problem:
            if is_htmx(request):
                return row_response(request, diary, entry_id, food_id, error=problem, status_code=400)
            raise Redirect(f"/diaries/{diary_id}/review?err=entry#entry-{entry_id}")
        now = now_iso()
        with db().transaction():
            db().execute(
                "UPDATE diary_entry SET food_id = ?, grams = ?, status = 'confirmed' WHERE id = ?",
                (int(food_id), float(grams), entry_id),
            )
            matching.remember(db(), entry["source_text"], int(food_id), now)
            name = text(form, "portion_name", 100)
            if "save_portion" in form and name and entry["quantity"]:
                exists = db().one("SELECT 1 FROM portion WHERE food_id = ? AND lower(name) = lower(?)", (int(food_id), name))
                if not exists:
                    db().execute(
                        "INSERT INTO portion(food_id, name, grams) VALUES(?, ?, ?)",
                        (int(food_id), name, round(float(grams) / entry["quantity"], 1)),
                    )
        finished_review(diary)
        if is_htmx(request):
            return row_response(request, diary, entry_id)
        raise Redirect(f"/diaries/{diary_id}/review#entry-{entry_id}")

    @r.post("/diaries/{diary_id}/entries/{entry_id}/skip")
    def skip(request: Request, diary_id: int, entry_id: int):
        diary = diary_row(diary_id)
        entry_row(diary_id, entry_id)
        db().execute("UPDATE diary_entry SET status = 'skipped' WHERE id = ?", (entry_id,))
        finished_review(diary)
        if is_htmx(request):
            return row_response(request, diary, entry_id)
        raise Redirect(f"/diaries/{diary_id}/review#entry-{entry_id}")

    @r.post("/diaries/{diary_id}/entries/{entry_id}/reopen")
    def reopen(request: Request, diary_id: int, entry_id: int):
        diary = diary_row(diary_id)
        entry = entry_row(diary_id, entry_id)
        if not entry["source_text"]:
            raise HTTPException(409, "Entries typed in directly are changed with Edit.")
        db().execute("UPDATE diary_entry SET status = 'pending' WHERE id = ?", (entry_id,))
        if is_htmx(request):
            return row_response(request, diary, entry_id)
        raise Redirect(f"/diaries/{diary_id}/review#entry-{entry_id}")

    @r.post("/diaries/{diary_id}/review/confirm-remembered")
    def confirm_remembered(diary_id: int):
        diary = diary_row(diary_id)
        entries = [dict(row) for row in db().query("SELECT * FROM diary_entry WHERE diary_id = ? AND status = 'pending'", (diary_id,))]
        done = 0
        now = now_iso()
        with db().transaction():
            for entry in entries:
                row = review_row(entry)
                if row["suggestions"] and row["suggestions"][0].get("remembered") and row["estimate"].grams:
                    db().execute(
                        "UPDATE diary_entry SET food_id = ?, grams = ?, status = 'confirmed' WHERE id = ?",
                        (row["selected"], row["estimate"].grams, entry["id"]),
                    )
                    matching.remember(db(), entry["source_text"], row["selected"], now)
                    done += 1
        finished_review(diary)
        raise Redirect(f"/diaries/{diary_id}/review?confirmed={done}")

    @r.get("/diaries/{diary_id}/analysis")
    def analyse(request: Request, diary_id: int):
        diary = diary_row(diary_id)
        client = clients.get(db(), diary["client_id"])
        tally = counts(diary_id)
        context = clients.context(web, diary["client_id"], "diaries")
        base = {"diary": dict(diary, source_label=SOURCES.get(diary["source"], diary["source"])), "counts": tally, **context}
        if tally["pending"]:
            return web.page(request, "diary_analysis.html", result=None, **base)
        days = declared_days(diary)
        last_day = max([days[-1], *[row["day"] for row in db().query("SELECT DISTINCT day FROM diary_entry WHERE diary_id = ?", (diary_id,))]])
        entries = db().query(
            "SELECT e.day, e.grams, f.nutrients FROM diary_entry e JOIN food f ON f.id = e.food_id "
            "WHERE e.diary_id = ? AND e.status = 'confirmed' AND e.grams IS NOT NULL",
            (diary_id,),
        )
        age = analysis.age_on(client["date_of_birth"], diary["start_date"])
        group = reference.group_for(client["sex"], age, diary["life_stage"])
        weight = db().one(
            "SELECT weight_kg FROM measurement WHERE client_id = ? AND weight_kg IS NOT NULL AND measured_on <= ? "
            "ORDER BY measured_on DESC, id DESC LIMIT 1",
            (diary["client_id"], last_day),
        )
        target = db().one(
            "SELECT * FROM target WHERE client_id = ? AND set_on <= ? ORDER BY set_on DESC, id DESC LIMIT 1",
            (diary["client_id"], last_day),
        ) or db().one("SELECT * FROM target WHERE client_id = ? ORDER BY set_on DESC, id DESC LIMIT 1", (diary["client_id"],))
        result = analysis.analyse(entries, days, group, weight["weight_kg"] if weight else None, target)
        web.log("diary_analysis_viewed", f"diary {diary_id}", diary["client_id"])
        return web.page(
            request,
            "diary_analysis.html",
            result=result,
            target=target,
            diary_age=age,
            sex=client["sex"],
            states=analysis.STATES,
            **base,
        )

    return r

from __future__ import annotations

import json
import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from . import clients, fmt
from .audit import now_iso
from .web import Redirect, Web, form_data, text

FIELD_TYPES = (
    ("text", "Short answer"),
    ("textarea", "Long answer"),
    ("number", "Number"),
    ("date", "Date"),
    ("yesno", "Yes or no"),
    ("choice", "Choose one"),
    ("multi", "Choose any"),
    ("heading", "Section heading"),
)
KINDS = (("intake", "Intake"), ("assessment", "Assessment"), ("other", "Other"))
MAX_FIELDS = 100


def parse_fields(form: FormData) -> list[dict]:
    count = text(form, "count")
    fields = []
    for i in range(min(int(count) if count.isdigit() else 0, MAX_FIELDS)):
        kind = text(form, f"f{i}_type")
        field_id = text(form, f"f{i}_id", 20)
        fields.append(
            {
                "id": field_id if field_id.isalnum() else secrets.token_hex(4),
                "type": kind if kind in dict(FIELD_TYPES) else "text",
                "label": text(form, f"f{i}_label", 300) or "Untitled question",
                "options": [line.strip() for line in text(form, f"f{i}_options", 5000).splitlines() if line.strip()],
                "required": f"f{i}_required" in form and kind != "heading",
            }
        )
    return fields


def apply_action(fields: list[dict], action: str, new_type: str = "text") -> list[dict]:
    verb, _, raw = action.partition(":")
    index = int(raw) if raw.isdigit() else -1
    fields = list(fields)
    if verb == "add" and len(fields) < MAX_FIELDS:
        kind = new_type if new_type in dict(FIELD_TYPES) else "text"
        label = "New section" if kind == "heading" else "New question"
        fields.append({"id": secrets.token_hex(4), "type": kind, "label": label, "options": [], "required": False})
    elif verb == "remove" and 0 <= index < len(fields):
        del fields[index]
    elif verb == "up" and 0 < index < len(fields):
        fields[index - 1], fields[index] = fields[index], fields[index - 1]
    elif verb == "down" and 0 <= index < len(fields) - 1:
        fields[index + 1], fields[index] = fields[index], fields[index + 1]
    return fields


def read_answers(fields: list[dict], form: FormData) -> tuple[dict, list[str]]:
    answers, missing = {}, []
    for field in fields:
        if field["type"] == "heading":
            continue
        name = f"a_{field['id']}"
        if field["type"] == "multi":
            value = [v for v in form.getlist(name) if isinstance(v, str) and v in field["options"]]
        else:
            value = text(form, name, 20000)
            if field["type"] == "choice" and value not in field["options"]:
                value = ""
            if field["type"] == "yesno" and value not in ("Yes", "No"):
                value = ""
            if field["type"] == "date" and value and fmt.parse_date(value) is None:
                value = ""
        answers[field["id"]] = value
        if field.get("required") and not value:
            missing.append(field["label"])
    return answers, missing


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def definition(form_id: int) -> dict:
        row = web.db.one("SELECT * FROM form_definition WHERE id = ?", (form_id,))
        if row is None:
            raise HTTPException(404, "That form does not exist.")
        return dict(row, fields=json.loads(row["fields"]))

    def response(client_id: int, response_id: int) -> dict:
        row = web.db.one("SELECT * FROM form_response WHERE id = ? AND client_id = ?", (response_id, client_id))
        if row is None:
            raise HTTPException(404, "That completed form does not exist.")
        return dict(row, fields=json.loads(row["fields"]), answers=json.loads(row["answers"]))

    @r.get("/library/forms/new")
    def new_form(request: Request):
        return web.page(request, "form_new.html", values={"kind": "intake"}, kinds=KINDS)

    @r.post("/library/forms/new")
    def create_form(request: Request, form: FormData = Depends(form_data)):
        name = text(form, "name", 200)
        kind = text(form, "kind")
        if not name:
            return web.page(
                request,
                "form_new.html",
                status_code=400,
                error="Give the form a name.",
                values={"name": name, "kind": kind},
                kinds=KINDS,
            )
        now = now_iso()
        form_id = web.db.insert(
            "INSERT INTO form_definition(name, kind, created_at, updated_at) VALUES(?, ?, ?, ?)",
            (name, kind if kind in dict(KINDS) else "other", now, now),
        )
        web.log("form_created", f"form {form_id}")
        raise Redirect(f"/library/forms/{form_id}")

    @r.get("/library/forms/{form_id}")
    def builder(request: Request, form_id: int):
        return web.page(
            request, "form_builder.html", form=definition(form_id), field_types=FIELD_TYPES, kinds=KINDS
        )

    @r.post("/library/forms/{form_id}")
    def save_builder(request: Request, form_id: int, form: FormData = Depends(form_data)):
        current = definition(form_id)
        action = text(form, "action") or "save"
        fields = apply_action(parse_fields(form), action, text(form, "new_type"))
        name = text(form, "name", 200) or current["name"]
        kind = text(form, "kind")
        web.db.execute(
            "UPDATE form_definition SET name = ?, kind = ?, description = ?, fields = ?, updated_at = ? WHERE id = ?",
            (
                name,
                kind if kind in dict(KINDS) else current["kind"],
                text(form, "description", 1000),
                json.dumps(fields),
                now_iso(),
                form_id,
            ),
        )
        if action == "save":
            web.log("form_updated", f"form {form_id}")
            raise Redirect("/library?msg=saved")
        anchor = "#add" if action.startswith("add") else "#fields"
        raise Redirect(f"/library/forms/{form_id}{anchor}")

    @r.get("/library/forms/{form_id}/preview")
    def preview(request: Request, form_id: int):
        return web.page(request, "form_preview.html", form=definition(form_id))

    @r.post("/library/forms/{form_id}/archive")
    def archive_form(form_id: int, form: FormData = Depends(form_data)):
        definition(form_id)
        archived = 0 if text(form, "restore") else 1
        web.db.execute(
            "UPDATE form_definition SET archived = ?, updated_at = ? WHERE id = ?", (archived, now_iso(), form_id)
        )
        raise Redirect("/library?msg=saved")

    @r.get("/clients/{client_id}/forms")
    def client_tab(request: Request, client_id: int):
        return web.page(
            request,
            "client_forms.html",
            responses=web.db.query(
                "SELECT id, form_name, completed_on, updated_at FROM form_response WHERE client_id = ? "
                "ORDER BY completed_on DESC, id DESC",
                (client_id,),
            ),
            available=web.db.query(
                "SELECT id, name, kind FROM form_definition WHERE archived = 0 ORDER BY name COLLATE NOCASE"
            ),
            **clients.context(web, client_id, "forms"),
        )

    def fill_page(request: Request, client_id: int, form_name: str, fields, answers, completed_on, existing, error=None):
        return web.page(
            request,
            "response_form.html",
            status_code=400 if error else 200,
            error=error,
            form_name=form_name,
            fields=fields,
            answers=answers,
            completed_on=completed_on,
            existing=existing,
            **clients.context(web, client_id, "forms"),
        )

    @r.get("/clients/{client_id}/forms/new")
    def fill(request: Request, client_id: int, form: int):
        chosen = definition(form)
        return fill_page(request, client_id, chosen["name"], chosen["fields"], {}, date.today().isoformat(), None)

    @r.post("/clients/{client_id}/forms/new")
    def save_fill(request: Request, client_id: int, form: int, posted: FormData = Depends(form_data)):
        clients.get(web.db, client_id)
        chosen = definition(form)
        answers, missing = read_answers(chosen["fields"], posted)
        completed = fmt.parse_date(text(posted, "completed_on")) or date.today()
        if missing:
            return fill_page(
                request,
                client_id,
                chosen["name"],
                chosen["fields"],
                answers,
                completed.isoformat(),
                None,
                "Still needed: " + ", ".join(missing),
            )
        now = now_iso()
        response_id = web.db.insert(
            "INSERT INTO form_response(client_id, form_id, form_name, fields, answers, completed_on, created_at, "
            "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                chosen["id"],
                chosen["name"],
                json.dumps(chosen["fields"]),
                json.dumps(answers),
                completed.isoformat(),
                now,
                now,
            ),
        )
        web.log("form_completed", f"completed form {response_id}", client_id)
        raise Redirect(f"/clients/{client_id}/forms/{response_id}?msg=saved")

    @r.get("/clients/{client_id}/forms/{response_id}")
    def view(request: Request, client_id: int, response_id: int):
        return web.page(
            request,
            "response_view.html",
            response=response(client_id, response_id),
            **clients.context(web, client_id, "forms"),
        )

    @r.get("/clients/{client_id}/forms/{response_id}/edit")
    def edit(request: Request, client_id: int, response_id: int):
        existing = response(client_id, response_id)
        return fill_page(
            request,
            client_id,
            existing["form_name"],
            existing["fields"],
            existing["answers"],
            existing["completed_on"],
            existing,
        )

    @r.post("/clients/{client_id}/forms/{response_id}/edit")
    def save_edit(request: Request, client_id: int, response_id: int, posted: FormData = Depends(form_data)):
        existing = response(client_id, response_id)
        answers, missing = read_answers(existing["fields"], posted)
        completed = fmt.parse_date(text(posted, "completed_on")) or date.today()
        if missing:
            return fill_page(
                request,
                client_id,
                existing["form_name"],
                existing["fields"],
                answers,
                completed.isoformat(),
                existing,
                "Still needed: " + ", ".join(missing),
            )
        web.db.execute(
            "UPDATE form_response SET answers = ?, completed_on = ?, updated_at = ? WHERE id = ?",
            (json.dumps(answers), completed.isoformat(), now_iso(), response_id),
        )
        web.log("form_response_updated", f"completed form {response_id}", client_id)
        raise Redirect(f"/clients/{client_id}/forms/{response_id}?msg=saved")

    @r.post("/clients/{client_id}/forms/{response_id}/delete")
    def delete(client_id: int, response_id: int):
        response(client_id, response_id)
        web.db.execute("DELETE FROM form_response WHERE id = ?", (response_id,))
        web.log("form_response_deleted", f"completed form {response_id}", client_id)
        raise Redirect(f"/clients/{client_id}/forms?msg=deleted")

    return r

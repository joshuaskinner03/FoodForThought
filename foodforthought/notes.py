from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import FormData

from . import clients, fmt
from .audit import now_iso
from .web import Redirect, Web, form_data, text


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def templates(include_archived: bool = False) -> list:
        where = "" if include_archived else "WHERE archived = 0 "
        return web.db.query(f"SELECT * FROM note_template {where}ORDER BY archived, name COLLATE NOCASE")

    def template_row(template_id: int):
        row = web.db.one("SELECT * FROM note_template WHERE id = ?", (template_id,))
        if row is None:
            raise HTTPException(404, "That template does not exist.")
        return row

    def note_row(client_id: int, note_id: int):
        row = web.db.one("SELECT * FROM note WHERE id = ? AND client_id = ?", (note_id, client_id))
        if row is None:
            raise HTTPException(404, "That note does not exist.")
        return row

    @r.get("/library")
    def library(request: Request):
        return web.page(
            request,
            "library.html",
            note_templates=templates(include_archived=True),
            forms=web.db.query("SELECT * FROM form_definition ORDER BY archived, name COLLATE NOCASE"),
        )

    @r.get("/library/note-templates/new")
    def new_template(request: Request):
        return web.page(request, "note_template_form.html", values={}, template=None)

    @r.post("/library/note-templates/new")
    def create_template(request: Request, form: FormData = Depends(form_data)):
        name = text(form, "name", 200)
        body = text(form, "body", 200000)
        if not name:
            return web.page(
                request,
                "note_template_form.html",
                status_code=400,
                error="Give the template a name.",
                values={"name": name, "body": body},
                template=None,
            )
        now = now_iso()
        template_id = web.db.insert(
            "INSERT INTO note_template(name, body, created_at, updated_at) VALUES(?, ?, ?, ?)", (name, body, now, now)
        )
        web.log("note_template_created", f"template {template_id}")
        raise Redirect("/library?msg=saved")

    @r.get("/library/note-templates/{template_id}")
    def edit_template(request: Request, template_id: int):
        template = template_row(template_id)
        return web.page(request, "note_template_form.html", values=dict(template), template=template)

    @r.post("/library/note-templates/{template_id}")
    def update_template(request: Request, template_id: int, form: FormData = Depends(form_data)):
        template = template_row(template_id)
        name = text(form, "name", 200)
        body = text(form, "body", 200000)
        if not name:
            return web.page(
                request,
                "note_template_form.html",
                status_code=400,
                error="Give the template a name.",
                values={"name": name, "body": body},
                template=template,
            )
        web.db.execute(
            "UPDATE note_template SET name = ?, body = ?, updated_at = ? WHERE id = ?",
            (name, body, now_iso(), template_id),
        )
        web.log("note_template_updated", f"template {template_id}")
        raise Redirect("/library?msg=saved")

    @r.post("/library/note-templates/{template_id}/archive")
    def archive_template(template_id: int, form: FormData = Depends(form_data)):
        template_row(template_id)
        archived = 0 if text(form, "restore") else 1
        web.db.execute(
            "UPDATE note_template SET archived = ?, updated_at = ? WHERE id = ?", (archived, now_iso(), template_id)
        )
        raise Redirect("/library?msg=saved")

    @r.get("/clients/{client_id}/notes")
    def client_tab(request: Request, client_id: int):
        return web.page(
            request,
            "client_notes.html",
            notes=web.db.query(
                "SELECT * FROM note WHERE client_id = ? ORDER BY note_date DESC, id DESC", (client_id,)
            ),
            **clients.context(web, client_id, "notes"),
        )

    def note_page(request: Request, client_id: int, values: dict, note, error: str | None = None):
        return web.page(
            request,
            "note_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            note=note,
            note_templates=templates(),
            **clients.context(web, client_id, "notes"),
        )

    def read_note(form: FormData) -> tuple[dict, str | None]:
        values = {
            "note_date": text(form, "note_date"),
            "title": text(form, "title", 200),
            "body": text(form, "body", 200000),
            "appointment_id": text(form, "appointment_id"),
        }
        if fmt.parse_date(values["note_date"]) is None:
            return values, "Give a valid date."
        if not values["body"]:
            return values, "The note is empty."
        return values, None

    def appointment_id_for(client_id: int, raw: str) -> int | None:
        if not raw.isdigit():
            return None
        row = web.db.one("SELECT id FROM appointment WHERE id = ? AND client_id = ?", (int(raw), client_id))
        return row["id"] if row else None

    @r.get("/clients/{client_id}/notes/new")
    def new_note(request: Request, client_id: int, template: str = "", appointment: str = ""):
        values = {"note_date": date.today().isoformat(), "title": "", "body": "", "appointment_id": ""}
        if template.isdigit():
            chosen = web.db.one("SELECT * FROM note_template WHERE id = ?", (int(template),))
            if chosen:
                values.update(title=chosen["name"], body=chosen["body"])
        linked = appointment_id_for(client_id, appointment)
        if linked:
            row = web.db.one("SELECT * FROM appointment WHERE id = ?", (linked,))
            values.update(note_date=row["date"], appointment_id=str(linked))
            values["title"] = values["title"] or row["title"]
        return note_page(request, client_id, values, None)

    @r.post("/clients/{client_id}/notes/new")
    def create_note(request: Request, client_id: int, form: FormData = Depends(form_data)):
        clients.get(web.db, client_id)
        values, error = read_note(form)
        if error:
            return note_page(request, client_id, values, None, error)
        now = now_iso()
        note_id = web.db.insert(
            "INSERT INTO note(client_id, appointment_id, note_date, title, body, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                appointment_id_for(client_id, values["appointment_id"]),
                values["note_date"],
                values["title"],
                values["body"],
                now,
                now,
            ),
        )
        web.log("note_created", f"note {note_id}", client_id)
        raise Redirect(f"/clients/{client_id}/notes?msg=saved#note-{note_id}")

    @r.get("/clients/{client_id}/notes/{note_id}/edit")
    def edit_note(request: Request, client_id: int, note_id: int):
        note = note_row(client_id, note_id)
        values = dict(note, appointment_id=str(note["appointment_id"] or ""))
        return note_page(request, client_id, values, note)

    @r.post("/clients/{client_id}/notes/{note_id}/edit")
    def update_note(request: Request, client_id: int, note_id: int, form: FormData = Depends(form_data)):
        note = note_row(client_id, note_id)
        values, error = read_note(form)
        if error:
            return note_page(request, client_id, values, note, error)
        web.db.execute(
            "UPDATE note SET note_date = ?, title = ?, body = ?, updated_at = ? WHERE id = ?",
            (values["note_date"], values["title"], values["body"], now_iso(), note_id),
        )
        web.log("note_updated", f"note {note_id}", client_id)
        raise Redirect(f"/clients/{client_id}/notes?msg=saved#note-{note_id}")

    @r.post("/clients/{client_id}/notes/{note_id}/delete")
    def delete_note(client_id: int, note_id: int):
        note_row(client_id, note_id)
        web.db.execute("DELETE FROM note WHERE id = ?", (note_id,))
        web.log("note_deleted", f"note {note_id}", client_id)
        raise Redirect(f"/clients/{client_id}/notes?msg=deleted")

    return r

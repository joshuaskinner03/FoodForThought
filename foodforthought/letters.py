from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import FormData

from . import clients, fmt, pdf, practice
from .audit import now_iso
from .web import Redirect, Web, form_data, text

RECIPIENTS = (("gp", "The client's GP"), ("client", "The client"), ("other", "Someone else"))
PLACEHOLDERS = (
    "[Client name]",
    "[Client first name]",
    "[Date of birth]",
    "[Client address]",
    "[GP name]",
    "[GP address]",
    "[Today]",
    "[My name]",
    "[Practice name]",
)
_PLACEHOLDER = re.compile(r"\[([^\[\]\n]{1,40})\]")


def placeholder_values(client, settings: dict, today: date) -> dict[str, str]:
    return {
        "client name": clients.full_name(client),
        "client first name": client["preferred_name"] or client["first_name"],
        "date of birth": fmt.nice_date(client["date_of_birth"]),
        "client address": client["address"],
        "gp name": client["gp_name"] or "Doctor",
        "gp address": client["gp_address"],
        "today": fmt.nice_date(today.isoformat()),
        "my name": settings["practitioner_name"],
        "practice name": settings["practice_name"],
    }


def fill(template: str, values: dict[str, str]) -> str:
    def swap(match: re.Match) -> str:
        return values.get(match[1].strip().lower(), match[0])

    return _PLACEHOLDER.sub(swap, template)


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def template_row(template_id: int):
        row = web.db.one("SELECT * FROM letter_template WHERE id = ?", (template_id,))
        if row is None:
            raise HTTPException(404, "That letter template does not exist.")
        return row

    def letter_row(client_id: int, letter_id: int):
        row = web.db.one("SELECT * FROM letter WHERE id = ? AND client_id = ?", (letter_id, client_id))
        if row is None:
            raise HTTPException(404, "That letter does not exist.")
        return row

    def template_page(request: Request, values: dict, template, error: str | None = None):
        return web.page(
            request,
            "letter_template_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            template=template,
            recipients=RECIPIENTS,
            placeholders=PLACEHOLDERS,
        )

    def read_template(form: FormData) -> dict:
        recipient = text(form, "recipient")
        return {
            "name": text(form, "name", 200),
            "recipient": recipient if recipient in dict(RECIPIENTS) else "other",
            "subject": text(form, "subject", 300),
            "body": text(form, "body", 100000),
        }

    @r.get("/library/letter-templates/new")
    def new_template(request: Request):
        return template_page(request, {"recipient": "gp"}, None)

    @r.post("/library/letter-templates/new")
    def create_template(request: Request, form: FormData = Depends(form_data)):
        values = read_template(form)
        if not values["name"]:
            return template_page(request, values, None, "Give the template a name.")
        now = now_iso()
        template_id = web.db.insert(
            "INSERT INTO letter_template(name, recipient, subject, body, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (values["name"], values["recipient"], values["subject"], values["body"], now, now),
        )
        web.log("letter_template_created", f"letter template {template_id}")
        raise Redirect("/library?msg=saved")

    @r.get("/library/letter-templates/{template_id}")
    def edit_template(request: Request, template_id: int):
        template = template_row(template_id)
        return template_page(request, dict(template), template)

    @r.post("/library/letter-templates/{template_id}")
    def update_template(request: Request, template_id: int, form: FormData = Depends(form_data)):
        template = template_row(template_id)
        values = read_template(form)
        if not values["name"]:
            return template_page(request, values, template, "Give the template a name.")
        web.db.execute(
            "UPDATE letter_template SET name = ?, recipient = ?, subject = ?, body = ?, updated_at = ? WHERE id = ?",
            (values["name"], values["recipient"], values["subject"], values["body"], now_iso(), template_id),
        )
        web.log("letter_template_updated", f"letter template {template_id}")
        raise Redirect("/library?msg=saved")

    @r.post("/library/letter-templates/{template_id}/archive")
    def archive_template(template_id: int, form: FormData = Depends(form_data)):
        template_row(template_id)
        archived = 0 if text(form, "restore") else 1
        web.db.execute(
            "UPDATE letter_template SET archived = ?, updated_at = ? WHERE id = ?", (archived, now_iso(), template_id)
        )
        raise Redirect("/library?msg=saved")

    @r.get("/clients/{client_id}/letters")
    def client_tab(request: Request, client_id: int):
        return web.page(
            request,
            "client_letters.html",
            letters=web.db.query(
                "SELECT * FROM letter WHERE client_id = ? ORDER BY letter_date DESC, id DESC", (client_id,)
            ),
            letter_templates=web.db.query(
                "SELECT id, name FROM letter_template WHERE archived = 0 ORDER BY name COLLATE NOCASE"
            ),
            **clients.context(web, client_id, "letters"),
        )

    def letter_page(request: Request, client_id: int, values: dict, letter, error: str | None = None, **extra):
        return web.page(
            request,
            "letter_form.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            letter=letter,
            **extra,
            **clients.context(web, client_id, "letters"),
        )

    def read_letter(form: FormData) -> tuple[dict, str | None]:
        values = {
            "letter_date": fmt.iso(text(form, "letter_date")),
            "recipient_name": text(form, "recipient_name", 200),
            "recipient_address": text(form, "recipient_address", 1000),
            "subject": text(form, "subject", 300),
            "body": text(form, "body", 100000),
        }
        if fmt.parse_date(values["letter_date"]) is None:
            return values, "Give a valid date."
        if not values["body"]:
            return values, "The letter is empty."
        return values, None

    @r.get("/clients/{client_id}/letters/new")
    def new_letter(request: Request, client_id: int, template: str = ""):
        client = clients.get(web.db, client_id)
        today = date.today()
        values = {"letter_date": today.isoformat(), "recipient_name": "", "recipient_address": "", "subject": "", "body": ""}
        gp_consent_missing = False
        chosen = web.db.one("SELECT * FROM letter_template WHERE id = ?", (int(template),)) if template.isdigit() else None
        if chosen:
            swaps = placeholder_values(client, practice.load(web.db), today)
            values.update(subject=fill(chosen["subject"], swaps), body=fill(chosen["body"], swaps))
            if chosen["recipient"] == "gp":
                values.update(recipient_name=client["gp_name"], recipient_address=client["gp_address"])
                gp_consent_missing = not web.db.one(
                    "SELECT 1 FROM consent WHERE client_id = ? AND withdrawn_on = '' AND kind LIKE '%GP%'", (client_id,)
                )
            elif chosen["recipient"] == "client":
                values.update(recipient_name=clients.full_name(client), recipient_address=client["address"])
        return letter_page(request, client_id, values, None, gp_consent_missing=gp_consent_missing)

    @r.post("/clients/{client_id}/letters/new")
    def create_letter(request: Request, client_id: int, form: FormData = Depends(form_data)):
        clients.get(web.db, client_id)
        values, error = read_letter(form)
        if error:
            return letter_page(request, client_id, values, None, error)
        now = now_iso()
        letter_id = web.db.insert(
            "INSERT INTO letter(client_id, letter_date, recipient_name, recipient_address, subject, body, "
            "created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                client_id,
                values["letter_date"],
                values["recipient_name"],
                values["recipient_address"],
                values["subject"],
                values["body"],
                now,
                now,
            ),
        )
        web.log("letter_created", f"letter {letter_id}", client_id)
        raise Redirect(f"/clients/{client_id}/letters?msg=saved")

    @r.get("/clients/{client_id}/letters/{letter_id}/edit")
    def edit_letter(request: Request, client_id: int, letter_id: int):
        letter = letter_row(client_id, letter_id)
        return letter_page(request, client_id, dict(letter), letter)

    @r.post("/clients/{client_id}/letters/{letter_id}/edit")
    def update_letter(request: Request, client_id: int, letter_id: int, form: FormData = Depends(form_data)):
        letter = letter_row(client_id, letter_id)
        values, error = read_letter(form)
        if error:
            return letter_page(request, client_id, values, letter, error)
        web.db.execute(
            "UPDATE letter SET letter_date = ?, recipient_name = ?, recipient_address = ?, subject = ?, body = ?, "
            "updated_at = ? WHERE id = ?",
            (
                values["letter_date"],
                values["recipient_name"],
                values["recipient_address"],
                values["subject"],
                values["body"],
                now_iso(),
                letter_id,
            ),
        )
        web.log("letter_updated", f"letter {letter_id}", client_id)
        raise Redirect(f"/clients/{client_id}/letters?msg=saved")

    @r.get("/clients/{client_id}/letters/{letter_id}/letter.pdf")
    def letter_file(client_id: int, letter_id: int):
        letter = letter_row(client_id, letter_id)
        data = pdf.letter_pdf(practice.load(web.db), practice.logo_bytes(web), dict(letter))
        web.log("letter_pdf_downloaded", f"letter {letter_id}", client_id)
        return Response(
            data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="letter-{letter["letter_date"]}-{letter_id}.pdf"'},
        )

    @r.post("/clients/{client_id}/letters/{letter_id}/delete")
    def delete_letter(client_id: int, letter_id: int):
        letter_row(client_id, letter_id)
        web.db.execute("DELETE FROM letter WHERE id = ?", (letter_id,))
        web.log("letter_deleted", f"letter {letter_id}", client_id)
        raise Redirect(f"/clients/{client_id}/letters?msg=deleted")

    return r

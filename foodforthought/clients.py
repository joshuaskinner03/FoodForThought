from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from starlette.datastructures import FormData

from . import fmt
from .audit import now_iso
from .db import Database
from .files import AttachmentError
from .web import Redirect, Web, form_data, text

FIELDS = (
    "first_name",
    "last_name",
    "preferred_name",
    "date_of_birth",
    "sex",
    "email",
    "phone",
    "address",
    "gp_name",
    "gp_address",
    "medical_history",
    "allergies",
    "medications",
    "goals",
)
SEX_OPTIONS = (("", "Not recorded"), ("female", "Female"), ("male", "Male"))
CONSENT_KINDS = (
    "Privacy notice and record keeping",
    "Contact by email",
    "Contact by phone or text",
    "Sharing information with GP",
)
CONSENT_METHODS = ("Signed form", "Verbal", "Email")
MAX_UPLOAD = 25 * 1024 * 1024
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def get(db: Database, client_id: int):
    row = db.one("SELECT * FROM client WHERE id = ?", (client_id,))
    if row is None:
        raise HTTPException(404, "That client does not exist.")
    return row


def full_name(row) -> str:
    return f"{row['first_name']} {row['last_name']}".strip()


def initials(row) -> str:
    return "".join(part[0].upper() for part in (row["first_name"], row["last_name"]) if part)


def search(db: Database, q: str = "", archived: bool = False) -> list:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    return db.query(
        "SELECT * FROM client WHERE archived = ? AND ("
        "first_name || ' ' || last_name LIKE ? ESCAPE '\\' "
        "OR preferred_name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\') "
        "ORDER BY last_name COLLATE NOCASE, first_name COLLATE NOCASE",
        (int(archived), like, like, like),
    )


def active(db: Database) -> list:
    return search(db)


def read_form(form: FormData) -> tuple[dict, list[str]]:
    values = {name: text(form, name) for name in FIELDS}
    errors = []
    if not values["first_name"] or not values["last_name"]:
        errors.append("First name and last name are needed.")
    if values["date_of_birth"]:
        born = fmt.parse_date(values["date_of_birth"])
        if born is None or born > date.today() or born.year < 1900:
            errors.append("Date of birth is not a valid date.")
    if values["sex"] not in dict(SEX_OPTIONS):
        values["sex"] = ""
    return values, errors


def context(web: Web, client_id: int, tab: str) -> dict:
    client = get(web.db, client_id)
    return {
        "client": client,
        "name": full_name(client),
        "age": fmt.age(client["date_of_birth"]),
        "tab": tab,
    }


def _safe_filename(name: str | None) -> str:
    cleaned = "".join(ch for ch in Path(name or "").name if ch.isprintable()).strip()
    return cleaned[:200] or "file"


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    @r.get("/clients")
    def index(request: Request, q: str = "", archived: int = 0):
        rows = search(web.db, q.strip(), bool(archived))
        partial = bool(request.headers.get("hx-request"))
        return web.page(
            request,
            "_client_rows.html" if partial else "clients.html",
            clients=[dict(row, age=fmt.age(row["date_of_birth"])) for row in rows],
            q=q,
            archived=archived,
        )

    @r.get("/clients/new")
    def new_form(request: Request):
        return web.page(request, "client_form.html", values={}, sex_options=SEX_OPTIONS, client=None)

    @r.post("/clients/new")
    def create(request: Request, form: FormData = Depends(form_data)):
        values, errors = read_form(form)
        if errors:
            return web.page(
                request,
                "client_form.html",
                status_code=400,
                error=" ".join(errors),
                values=values,
                sex_options=SEX_OPTIONS,
                client=None,
            )
        now = now_iso()
        columns = ", ".join(FIELDS)
        marks = ", ".join("?" for _ in FIELDS)
        client_id = web.db.insert(
            f"INSERT INTO client({columns}, created_at, updated_at) VALUES({marks}, ?, ?)",
            (*(values[name] for name in FIELDS), now, now),
        )
        web.log("client_created", client_id=client_id)
        raise Redirect(f"/clients/{client_id}")

    @r.get("/clients/{client_id}")
    def overview(request: Request, client_id: int):
        upcoming = web.db.one(
            "SELECT * FROM appointment WHERE client_id = ? AND date >= ? AND status = 'booked' "
            "ORDER BY date, start_time LIMIT 1",
            (client_id, date.today().isoformat()),
        )
        latest_note = web.db.one(
            "SELECT * FROM note WHERE client_id = ? ORDER BY note_date DESC, id DESC LIMIT 1", (client_id,)
        )
        return web.page(
            request,
            "client_overview.html",
            upcoming=upcoming,
            latest_note=latest_note,
            sex_label=dict(SEX_OPTIONS),
            **context(web, client_id, "overview"),
        )

    @r.get("/clients/{client_id}/edit")
    def edit_form(request: Request, client_id: int):
        client = get(web.db, client_id)
        return web.page(request, "client_form.html", values=dict(client), sex_options=SEX_OPTIONS, client=client)

    @r.post("/clients/{client_id}/edit")
    def update(request: Request, client_id: int, form: FormData = Depends(form_data)):
        client = get(web.db, client_id)
        values, errors = read_form(form)
        if errors:
            return web.page(
                request,
                "client_form.html",
                status_code=400,
                error=" ".join(errors),
                values=values,
                sex_options=SEX_OPTIONS,
                client=client,
            )
        changed = [name for name in FIELDS if values[name] != client[name]]
        assignments = ", ".join(f"{name} = ?" for name in FIELDS)
        web.db.execute(
            f"UPDATE client SET {assignments}, updated_at = ? WHERE id = ?",
            (*(values[name] for name in FIELDS), now_iso(), client_id),
        )
        if changed:
            web.log("client_updated", ", ".join(changed), client_id)
        raise Redirect(f"/clients/{client_id}?msg=saved")

    @r.post("/clients/{client_id}/archive")
    def archive(client_id: int, form: FormData = Depends(form_data)):
        get(web.db, client_id)
        archived = 0 if text(form, "restore") else 1
        web.db.execute("UPDATE client SET archived = ?, updated_at = ? WHERE id = ?", (archived, now_iso(), client_id))
        web.log("client_archived" if archived else "client_unarchived", client_id=client_id)
        raise Redirect(f"/clients/{client_id}?msg={'archived' if archived else 'unarchived'}")

    @r.get("/clients/{client_id}/documents")
    def documents(request: Request, client_id: int):
        return web.page(
            request,
            "client_documents.html",
            consents=web.db.query(
                "SELECT * FROM consent WHERE client_id = ? ORDER BY given_on DESC, id DESC", (client_id,)
            ),
            attachments=[
                dict(row, viewable=Path(row["filename"]).suffix.lower() in IMAGE_TYPES)
                for row in web.db.query(
                    "SELECT * FROM attachment WHERE client_id = ? ORDER BY id DESC", (client_id,)
                )
            ],
            consent_kinds=CONSENT_KINDS,
            consent_methods=CONSENT_METHODS,
            **context(web, client_id, "documents"),
        )

    @r.post("/clients/{client_id}/consents")
    def add_consent(client_id: int, form: FormData = Depends(form_data)):
        get(web.db, client_id)
        kind = text(form, "kind", 200)
        if not kind:
            raise Redirect(f"/clients/{client_id}/documents?err=consent")
        given = fmt.parse_date(text(form, "given_on")) or date.today()
        consent_id = web.db.insert(
            "INSERT INTO consent(client_id, kind, given_on, method, note, created_at) VALUES(?, ?, ?, ?, ?, ?)",
            (client_id, kind, given.isoformat(), text(form, "method", 100), text(form, "note", 2000), now_iso()),
        )
        web.log("consent_recorded", f"consent {consent_id}", client_id)
        raise Redirect(f"/clients/{client_id}/documents?msg=saved")

    @r.post("/clients/{client_id}/consents/{consent_id}/withdraw")
    def withdraw_consent(client_id: int, consent_id: int):
        web.db.execute(
            "UPDATE consent SET withdrawn_on = ? WHERE id = ? AND client_id = ? AND withdrawn_on = ''",
            (date.today().isoformat(), consent_id, client_id),
        )
        web.log("consent_withdrawn", f"consent {consent_id}", client_id)
        raise Redirect(f"/clients/{client_id}/documents?msg=saved")

    @r.post("/clients/{client_id}/consents/{consent_id}/delete")
    def delete_consent(client_id: int, consent_id: int):
        web.db.execute("DELETE FROM consent WHERE id = ? AND client_id = ?", (consent_id, client_id))
        web.log("consent_deleted", f"consent {consent_id}", client_id)
        raise Redirect(f"/clients/{client_id}/documents?msg=deleted")

    @r.post("/clients/{client_id}/attachments")
    def upload(client_id: int, file: UploadFile | None = File(None), description: str = Form("")):
        get(web.db, client_id)
        data = file.file.read(MAX_UPLOAD + 1) if file is not None else b""
        if not data:
            raise Redirect(f"/clients/{client_id}/documents?err=no-file")
        if len(data) > MAX_UPLOAD:
            raise Redirect(f"/clients/{client_id}/documents?err=too-large")
        file_id = web.core.attachments.put(data)
        attachment_id = web.db.insert(
            "INSERT INTO attachment(client_id, file_id, filename, size, description, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (client_id, file_id, _safe_filename(file.filename), len(data), description.strip()[:500], now_iso()),
        )
        web.log("attachment_added", f"attachment {attachment_id}", client_id)
        raise Redirect(f"/clients/{client_id}/documents?msg=uploaded")

    def attachment_row(client_id: int, attachment_id: int):
        row = web.db.one("SELECT * FROM attachment WHERE id = ? AND client_id = ?", (attachment_id, client_id))
        if row is None:
            raise HTTPException(404, "That file does not exist.")
        return row

    @r.get("/clients/{client_id}/attachments/{attachment_id}")
    def download(client_id: int, attachment_id: int, view: int = 0):
        row = attachment_row(client_id, attachment_id)
        try:
            data = web.core.attachments.get(row["file_id"])
        except AttachmentError as exc:
            raise HTTPException(404, "That file is missing or damaged.") from exc
        image_type = IMAGE_TYPES.get(Path(row["filename"]).suffix.lower())
        inline = bool(view and image_type)
        headers = {
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; "
            f"filename*=UTF-8''{quote(row['filename'])}"
        }
        web.log("attachment_opened", f"attachment {attachment_id}", client_id)
        return Response(data, media_type=image_type if inline else "application/octet-stream", headers=headers)

    @r.post("/clients/{client_id}/attachments/{attachment_id}/delete")
    def delete_attachment(client_id: int, attachment_id: int):
        row = attachment_row(client_id, attachment_id)
        web.db.execute("DELETE FROM attachment WHERE id = ?", (attachment_id,))
        web.core.attachments.delete(row["file_id"])
        web.log("attachment_deleted", f"attachment {attachment_id}", client_id)
        raise Redirect(f"/clients/{client_id}/documents?msg=deleted")

    return r

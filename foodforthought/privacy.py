from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import FormData

from . import clients, invoices, measurements, practice
from .audit import now_iso
from .files import AttachmentError
from .web import Redirect, Web, form_data, text

CLIENT_TABLES = (
    ("consents", "SELECT * FROM consent WHERE client_id = ? ORDER BY given_on, id"),
    ("appointments", "SELECT * FROM appointment WHERE client_id = ? ORDER BY date, start_time, id"),
    ("notes", "SELECT * FROM note WHERE client_id = ? ORDER BY note_date, id"),
    ("letters", "SELECT * FROM letter WHERE client_id = ? ORDER BY letter_date, id"),
    ("targets", "SELECT * FROM target WHERE client_id = ? ORDER BY set_on, id"),
    ("activity", "SELECT ts, action, detail FROM audit_log WHERE client_id = ? ORDER BY id"),
)


def _archive_name(attachment) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_. " else "_" for ch in attachment["filename"]).strip(". ") or "file"
    return f"files/{attachment['id']}-{safe}"


def collect(web: Web, client_id: int) -> dict:
    db = web.db
    client = clients.get(db, client_id)
    record = {
        "exported_at": now_iso(),
        "exported_by": practice.load(db)["display_name"],
        "client": {key: client[key] for key in client.keys() if key != "id"},
    }
    for name, sql in CLIENT_TABLES:
        record[name] = [dict(row) for row in db.query(sql, (client_id,))]
    record["measurements"] = [
        {key: value for key, value in item.items() if key not in ("id", "client_id", "height_used")}
        for item in measurements.with_bmi(db.query("SELECT * FROM measurement WHERE client_id = ?", (client_id,)))
    ]
    record["forms"] = []
    for row in db.query("SELECT * FROM form_response WHERE client_id = ? ORDER BY completed_on, id", (client_id,)):
        answers = json.loads(row["answers"])
        record["forms"].append(
            {
                "form": row["form_name"],
                "completed_on": row["completed_on"],
                "answers": [
                    {"question": field["label"], "answer": answers.get(field["id"], "")}
                    for field in json.loads(row["fields"])
                    if field["type"] != "heading"
                ],
            }
        )
    record["invoices"] = [
        {
            "number": invoice["number"],
            "status": invoice["state_label"],
            "issue_date": invoice["issue_date"],
            "due_date": invoice["due_date"],
            "bill_to_name": invoice["bill_to_name"],
            "bill_to_address": invoice["bill_to_address"],
            "lines": [
                {"description": l["description"], "quantity": l["quantity"], "unit_pence": l["unit_pence"], "total_pence": l["total_pence"]}
                for l in invoice["lines"]
            ],
            "payments": [
                {"paid_on": p["paid_on"], "amount_pence": p["amount_pence"], "method": p["method"]}
                for p in invoice["payments"]
            ],
            "totals": invoice["totals"],
        }
        for invoice in invoices.load(db, "WHERE client_id = ?", (client_id,))
    ]
    record["files"] = [
        {
            "file": _archive_name(row),
            "original_name": row["filename"],
            "description": row["description"],
            "added": row["created_at"],
            "file_id": row["file_id"],
        }
        for row in db.query("SELECT * FROM attachment WHERE client_id = ? ORDER BY id", (client_id,))
    ]
    return record


def build_zip(web: Web, client_id: int) -> bytes:
    record = collect(web, client_id)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        missing = []
        for item in record["files"]:
            file_id = item.pop("file_id")
            try:
                archive.writestr(item["file"], web.core.attachments.get(file_id))
            except AttachmentError:
                missing.append(item["original_name"])
                item["file"] = ""
        record["files_missing"] = missing
        html = web.templates.get_template("export_record.html").render(
            record=record, name=clients.full_name(record["client"]), sex_label=dict(clients.SEX_OPTIONS)
        )
        archive.writestr("record.html", html)
        archive.writestr("record.json", json.dumps(record, indent=2, ensure_ascii=False))
    return out.getvalue()


def erase(web: Web, client_id: int) -> dict:
    db = web.db
    clients.get(db, client_id)
    file_ids = [row["file_id"] for row in db.query("SELECT file_id FROM attachment WHERE client_id = ?", (client_id,))]
    with db.transaction():
        db.execute("DELETE FROM invoice WHERE client_id = ? AND status = 'draft'", (client_id,))
        kept = db.one("SELECT count(*) FROM invoice WHERE client_id = ?", (client_id,))[0]
        db.execute("DELETE FROM client WHERE id = ?", (client_id,))
        db.execute(
            "UPDATE audit_log SET detail = '' WHERE client_id = ? AND action = 'client_updated'", (client_id,)
        )
        web.log("client_erased", f"{kept} issued invoices kept as financial records", client_id)
    for file_id in file_ids:
        web.core.attachments.delete(file_id)
    return {"invoices_kept": kept, "files_deleted": len(file_ids)}


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    @r.get("/clients/{client_id}/data")
    def data_page(request: Request, client_id: int):
        counts = {
            name: web.db.one(f"SELECT count(*) FROM {table} WHERE client_id = ?", (client_id,))[0]
            for name, table in (
                ("appointments", "appointment"),
                ("notes", "note"),
                ("completed forms", "form_response"),
                ("measurements", "measurement"),
                ("letters", "letter"),
                ("sets of targets", "target"),
                ("consent records", "consent"),
                ("files", "attachment"),
            )
        }
        kept = web.db.one(
            "SELECT count(*) FROM invoice WHERE client_id = ? AND status != 'draft'", (client_id,)
        )[0]
        return web.page(
            request, "client_data.html", counts=counts, invoices_kept=kept, **clients.context(web, client_id, "overview")
        )

    @r.post("/clients/{client_id}/export")
    def export(client_id: int):
        client = clients.get(web.db, client_id)
        data = build_zip(web, client_id)
        web.log("client_exported", client_id=client_id)
        name = "".join(ch if ch.isascii() and ch.isalnum() else "-" for ch in clients.full_name(client)).strip("-") or "client"
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}-record-{date.today().isoformat()}.zip"'},
        )

    @r.post("/clients/{client_id}/erase")
    def erase_client(client_id: int, form: FormData = Depends(form_data)):
        client = clients.get(web.db, client_id)
        typed = " ".join(text(form, "confirm").split()).casefold()
        if typed != " ".join(clients.full_name(client).split()).casefold():
            raise Redirect(f"/clients/{client_id}/data?err=erase-name")
        erase(web, client_id)
        raise Redirect("/clients?msg=erased")

    return r

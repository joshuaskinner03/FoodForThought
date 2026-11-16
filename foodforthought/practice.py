from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from PIL import Image
from starlette.datastructures import FormData

from .db import Database
from .files import AttachmentError
from .web import Redirect, Web, form_data, text

TEXT_FIELDS = (
    "practice_name",
    "practitioner_name",
    "credentials",
    "practice_address",
    "practice_phone",
    "practice_email",
    "practice_website",
    "payment_details",
    "invoice_footer",
    "invoice_prefix",
    "vat_number",
)
DEFAULTS = {
    "invoice_prefix": "INV-",
    "invoice_next": "1",
    "payment_terms_days": "14",
    "vat_registered": "",
    "vat_rate_percent": "20",
    "logo_file_id": "",
}
MAX_LOGO = 5 * 1024 * 1024
LOGO_MAX_SIDE = 800


def load(db: Database) -> dict:
    stored = {row["key"]: row["value"] for row in db.query("SELECT key, value FROM settings")}
    values = {name: stored.get(name, DEFAULTS.get(name, "")) for name in (*TEXT_FIELDS, *DEFAULTS)}
    values["display_name"] = values["practice_name"] or values["practitioner_name"]
    return values


def vat_bp(values: dict) -> int:
    if not values["vat_registered"]:
        return 0
    try:
        return round(float(values["vat_rate_percent"]) * 100)
    except ValueError:
        return 0


def clean_logo(data: bytes) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                return None
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, "white")
            flat.paste(image, mask=image.split()[3])
            flat.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))
            out = io.BytesIO()
            flat.save(out, "PNG")
            return out.getvalue()
    except Exception:
        return None


def logo_bytes(web: Web) -> bytes | None:
    file_id = web.db.get_setting("logo_file_id", "")
    if not file_id:
        return None
    try:
        return web.core.attachments.get(file_id)
    except AttachmentError:
        return None


def router(web: Web) -> APIRouter:
    r = APIRouter(dependencies=[Depends(web.require_unlocked)])

    def page(request: Request, values: dict, error: str | None = None):
        return web.page(
            request,
            "practice.html",
            status_code=400 if error else 200,
            error=error,
            values=values,
            has_logo=bool(web.db.get_setting("logo_file_id", "")),
        )

    @r.get("/settings/practice")
    def practice_form(request: Request):
        return page(request, load(web.db))

    @r.post("/settings/practice")
    def save(request: Request, form: FormData = Depends(form_data)):
        values = load(web.db)
        values.update({name: text(form, name, 2000) for name in TEXT_FIELDS})
        values["vat_registered"] = "1" if "vat_registered" in form else ""
        values["vat_rate_percent"] = text(form, "vat_rate_percent")
        values["payment_terms_days"] = text(form, "payment_terms_days")
        values["invoice_next"] = text(form, "invoice_next")
        errors = []
        if not values["payment_terms_days"].isdigit() or int(values["payment_terms_days"]) > 365:
            errors.append("Payment terms must be between 0 and 365 days.")
        if not values["invoice_next"].isdigit() or int(values["invoice_next"]) < 1:
            errors.append("The next invoice number must be 1 or more.")
        try:
            rate_ok = 0 <= float(values["vat_rate_percent"]) <= 100
        except ValueError:
            rate_ok = False
        if not rate_ok:
            errors.append("The VAT rate must be between 0 and 100.")
        if values["vat_registered"] and not values["vat_number"]:
            errors.append("Give your VAT number, or untick VAT registered.")
        if errors:
            return page(request, values, " ".join(errors))
        with web.db.transaction():
            for name in (*TEXT_FIELDS, "vat_registered", "vat_rate_percent", "payment_terms_days", "invoice_next"):
                web.db.set_setting(name, values[name])
            web.log("practice_details_updated")
        raise Redirect("/settings/practice?msg=saved")

    @r.post("/settings/practice/logo")
    def upload_logo(file: UploadFile | None = File(None)):
        data = file.file.read(MAX_LOGO + 1) if file is not None else b""
        cleaned = clean_logo(data) if 0 < len(data) <= MAX_LOGO else None
        if cleaned is None:
            raise Redirect("/settings/practice?err=logo")
        previous = web.db.get_setting("logo_file_id", "")
        web.db.set_setting("logo_file_id", web.core.attachments.put(cleaned))
        if previous:
            web.core.attachments.delete(previous)
        web.log("practice_logo_updated")
        raise Redirect("/settings/practice?msg=logo-saved")

    @r.post("/settings/practice/logo/delete")
    def delete_logo():
        previous = web.db.get_setting("logo_file_id", "")
        if previous:
            web.db.set_setting("logo_file_id", "")
            web.core.attachments.delete(previous)
            web.log("practice_logo_removed")
        raise Redirect("/settings/practice?msg=deleted")

    @r.get("/settings/practice/logo")
    def logo():
        data = logo_bytes(web)
        if data is None:
            raise HTTPException(404, "No logo has been added.")
        return Response(data, media_type="image/png")

    return r

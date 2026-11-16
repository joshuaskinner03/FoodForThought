from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from . import __version__, audit, fmt
from .config import MIN_PASSWORD_LENGTH, Config
from .core import Core

PKG = Path(__file__).parent
COOKIE = "fft_session"

MESSAGES = {
    "locked": "Locked.",
    "auto": "Locked after a period of inactivity.",
    "restored": "Backup restored. Unlock with the password that backup was made with.",
    "password": "Password changed.",
    "saved": "Saved.",
    "deleted": "Deleted.",
    "safety-deleted": "Safety copy deleted.",
    "archived": "Client archived. Their records are kept.",
    "unarchived": "Client restored to the active list.",
    "uploaded": "File added.",
    "clash": "Saved. Note: this overlaps another appointment that day.",
    "series": "Saved. The repeat appointments were added to the diary.",
    "issued": "Invoice issued. It can no longer be edited.",
    "voided": "Invoice voided.",
    "paid": "Payment recorded.",
    "erased": "The client's record has been erased.",
    "logo-saved": "Logo saved.",
    "series-clash": "Saved the repeat appointments. Note: at least one overlaps another appointment.",
}

ERRORS = {
    "no-file": "Choose a file first.",
    "too-large": "That file is too large. The limit is 25 MB.",
    "consent": "Say what the consent is for.",
    "logo": "That file is not a PNG or JPEG image, or it is larger than 5 MB.",
    "practice": "Add your practice details in Settings before issuing an invoice.",
    "has-payments": "Delete the payments on this invoice before voiding it.",
    "payment": "Give a valid date and an amount above zero.",
    "erase-name": "The name typed did not match, so nothing was erased.",
}


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


async def form_data(request: Request) -> FormData:
    return await request.form()


def text(form: FormData, name: str, limit: int = 50000) -> str:
    value = form.get(name, "")
    return value.replace("\r\n", "\n").strip()[:limit] if isinstance(value, str) else ""


class Web:
    def __init__(self, cfg: Config, core: Core):
        self.cfg = cfg
        self.core = core
        self.templates = Jinja2Templates(directory=PKG / "templates")
        self.templates.env.filters.update(
            nice_date=fmt.nice_date,
            uk_date=fmt.uk_date,
            money=fmt.money,
            number=fmt.number,
            plain=fmt.plain,
            long_date=fmt.long_date,
            local_stamp=fmt.local_stamp,
            file_size=fmt.file_size,
        )

    @property
    def db(self):
        return self.core.db

    def authed(self, request: Request) -> bool:
        return self.core.valid(request.cookies.get(COOKIE))

    def require_unlocked(self, request: Request) -> None:
        if not self.core.initialised:
            raise Redirect("/setup")
        if not self.authed(request):
            raise Redirect("/unlock")
        self.core.touch()

    def log(self, action: str, detail: str = "", client_id: int | None = None) -> None:
        audit.record(self.db, action, detail, client_id=client_id)

    def page(self, request: Request, template_name: str, status_code: int = 200, **context):
        params = request.query_params
        context.setdefault("error", ERRORS.get(params.get("err", "")))
        context.update(
            authed=self.authed(request),
            version=__version__,
            message=MESSAGES.get(params.get("msg", "")),
            min_password=MIN_PASSWORD_LENGTH,
            section=request.url.path.strip("/").split("/")[0],
            today=date.today().isoformat(),
        )
        return self.templates.TemplateResponse(request, template_name, context, status_code=status_code)

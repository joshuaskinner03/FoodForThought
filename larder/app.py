from __future__ import annotations

import asyncio
import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from . import __version__, audit, backup
from .config import MIN_PASSWORD_LENGTH, Config, default_data_dir
from .core import Core, Throttled
from .vault import WrongPassword

PKG = Path(__file__).parent
COOKIE = "larder_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
MESSAGES = {
    "locked": "Locked.",
    "auto": "Locked after a period of inactivity.",
    "restored": "Backup restored. Unlock with the password that backup was made with.",
    "password": "Password changed.",
    "saved": "Settings saved.",
    "safety-deleted": "Safety copy deleted.",
}


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


def _password_problem(new: str, confirm: str) -> str | None:
    if len(new) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if new != confirm:
        return "The two passwords do not match."
    return None


def create_app(cfg: Config | None = None, clock: Callable[[], float] = time.monotonic) -> FastAPI:
    cfg = cfg or Config(default_data_dir())
    core = Core(cfg, clock)
    shutil.rmtree(cfg.tmp_dir, ignore_errors=True)
    cfg.tmp_dir.mkdir(exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async def watchdog():
            while True:
                await asyncio.sleep(5)
                core.expire_if_idle()

        task = asyncio.create_task(watchdog())
        try:
            yield
        finally:
            task.cancel()
            core.lock("shutdown")

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.core = core
    app.mount("/static", StaticFiles(directory=PKG / "static"), name="static")
    templates = Jinja2Templates(directory=PKG / "templates")

    hosts = {f"localhost:{cfg.port}", f"127.0.0.1:{cfg.port}"}
    origins = {f"http://{host}" for host in hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return PlainTextResponse("Bad host", status_code=400)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in origins:
                return PlainTextResponse("Cross-site request blocked", status_code=403)
        core.expire_if_idle()
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(Redirect)
    async def on_redirect(_: Request, exc: Redirect):
        return RedirectResponse(exc.url, status_code=303)

    def authed(request: Request) -> bool:
        return core.valid(request.cookies.get(COOKIE))

    def require_unlocked(request: Request) -> None:
        if not core.initialised:
            raise Redirect("/setup")
        if not authed(request):
            raise Redirect("/unlock")
        core.touch()

    def page(request: Request, name: str, status_code: int = 200, **context):
        context.update(
            authed=authed(request),
            version=__version__,
            message=MESSAGES.get(request.query_params.get("msg", "")),
            min_password=MIN_PASSWORD_LENGTH,
        )
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def signed_in(token: str, url: str = "/") -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/")
    def home(request: Request, _: None = Depends(require_unlocked)):
        cipher = core.db.query("PRAGMA cipher_version")[0][0]
        return page(
            request,
            "home.html",
            cipher=cipher,
            data_dir=str(cfg.data_dir),
            last_backup=core.db.get_setting("last_backup"),
            auto_lock=core.auto_lock_minutes,
        )

    @app.get("/setup")
    def setup_form(request: Request):
        if core.initialised:
            raise Redirect("/unlock")
        return page(request, "setup.html")

    @app.post("/setup")
    def setup(request: Request, password: str = Form(""), confirm: str = Form("")):
        if core.initialised:
            raise Redirect("/unlock")
        problem = _password_problem(password, confirm)
        if problem:
            return page(request, "setup.html", status_code=400, error=problem)
        return signed_in(core.setup(password))

    @app.get("/unlock")
    def unlock_form(request: Request):
        if not core.initialised:
            raise Redirect("/setup")
        if authed(request):
            raise Redirect("/")
        return page(request, "unlock.html")

    @app.post("/unlock")
    def unlock(request: Request, password: str = Form("")):
        if not core.initialised:
            raise Redirect("/setup")
        try:
            return signed_in(core.unlock(password))
        except WrongPassword:
            return page(request, "unlock.html", status_code=401, error="Wrong password.")
        except Throttled as exc:
            return page(
                request,
                "unlock.html",
                status_code=429,
                error=f"Too many wrong attempts. Try again in {exc.seconds} seconds.",
            )

    @app.post("/lock")
    def lock(request: Request):
        if authed(request):
            core.lock("manual")
        response = RedirectResponse("/unlock?msg=locked", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    def settings_page(request: Request, error: str | None = None):
        copy = backup.safety_copy(cfg)
        return page(
            request,
            "settings.html",
            status_code=400 if error else 200,
            error=error,
            auto_lock=core.auto_lock_minutes,
            last_backup=core.db.get_setting("last_backup"),
            safety_copy=copy.name if copy else None,
        )

    @app.get("/settings")
    def settings(request: Request, _: None = Depends(require_unlocked)):
        return settings_page(request)

    @app.post("/settings/auto-lock")
    def set_auto_lock(request: Request, minutes: str = Form(""), _: None = Depends(require_unlocked)):
        if not minutes.isdigit() or not 1 <= int(minutes) <= 120:
            return settings_page(request, "Auto-lock must be between 1 and 120 minutes.")
        core.set_auto_lock(int(minutes))
        raise Redirect("/settings?msg=saved")

    @app.post("/settings/password")
    def change_password(
        request: Request,
        current: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
        _: None = Depends(require_unlocked),
    ):
        problem = _password_problem(password, confirm)
        if not problem:
            try:
                core.change_password(current, password)
            except WrongPassword:
                problem = "Current password is wrong."
        if problem:
            return settings_page(request, problem)
        raise Redirect("/settings?msg=password")

    @app.post("/settings/safety-copy/delete")
    def delete_safety_copy(_: None = Depends(require_unlocked)):
        backup.delete_safety_copies(cfg)
        audit.record(core.db, "safety_copy_deleted")
        raise Redirect("/settings?msg=safety-deleted")

    @app.post("/backup")
    def make_backup(_: None = Depends(require_unlocked)):
        name = backup.backup_filename()
        tmp = cfg.tmp_dir / f"{secrets.token_hex(8)}{backup.BACKUP_SUFFIX}"
        previous = core.db.get_setting("last_backup")
        core.db.set_setting("last_backup", audit.now_iso())
        audit.record(core.db, "backup_created", name)
        try:
            backup.create_backup(cfg, core.db, core.keys, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            if previous is None:
                core.db.execute("DELETE FROM settings WHERE key = 'last_backup'")
            else:
                core.db.set_setting("last_backup", previous)
            audit.record(core.db, "backup_failed", name)
            raise
        return FileResponse(
            tmp,
            filename=name,
            media_type="application/octet-stream",
            background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)),
        )

    @app.get("/restore")
    def restore_form(request: Request):
        return page(request, "restore.html", initialised=core.initialised)

    @app.post("/restore")
    def restore(request: Request, file: UploadFile = File(...), password: str = Form("")):
        handle, tmp = tempfile.mkstemp(dir=cfg.tmp_dir)
        try:
            with open(handle, "wb") as out:
                shutil.copyfileobj(file.file, out)
            try:
                stage = backup.unpack_and_verify(cfg, Path(tmp), password)
            except backup.BackupError as exc:
                return page(
                    request, "restore.html", status_code=400, error=str(exc), initialised=core.initialised
                )
        finally:
            Path(tmp).unlink(missing_ok=True)
        with core.mutex:
            core.lock("restore")
            kept = backup.install_restored(cfg, stage)
        detail = f"previous data kept in {kept.name}" if kept else ""
        audit.record_pending(cfg.pending_audit_path, "restore", detail)
        response = RedirectResponse("/unlock?msg=restored", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/audit")
    def audit_log(request: Request, _: None = Depends(require_unlocked)):
        return page(request, "audit.html", entries=audit.recent(core.db))

    @app.get("/api/status")
    def status(request: Request):
        ok = authed(request)
        return {
            "initialised": core.initialised,
            "unlocked": ok,
            "seconds_left": core.seconds_left() if ok else 0,
        }

    @app.post("/api/touch")
    def touch(request: Request):
        if not authed(request):
            return JSONResponse({"unlocked": False}, status_code=401)
        core.touch()
        return {"unlocked": True, "seconds_left": core.seconds_left()}

    return app

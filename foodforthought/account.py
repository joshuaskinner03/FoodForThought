from __future__ import annotations

import secrets
import shutil
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

from . import audit, backup, diary, launcher, updates
from .config import MIN_PASSWORD_LENGTH
from .core import Throttled
from .db import SchemaTooNew
from .vault import WrongPassword
from .web import COOKIE, Redirect, Web


def _password_problem(new: str, confirm: str) -> str | None:
    if len(new) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if new != confirm:
        return "The two passwords do not match."
    return None


def router(web: Web) -> APIRouter:
    r = APIRouter()
    cfg, core = web.cfg, web.core
    unlocked = Depends(web.require_unlocked)

    def signed_in(token: str, url: str = "/") -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
        return response

    def signed_out(url: str) -> RedirectResponse:
        response = RedirectResponse(url, status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @r.get("/")
    def home(request: Request, _: None = unlocked):
        today = date.today()
        return web.page(
            request,
            "home.html",
            appointments=diary.between(web.db, today, today),
            client_count=web.db.one("SELECT count(*) FROM client WHERE archived = 0")[0],
            cipher=web.db.query("PRAGMA cipher_version")[0][0],
            data_dir=str(cfg.data_dir),
            last_backup=web.db.get_setting("last_backup"),
            auto_lock=core.auto_lock_minutes,
            drafts=web.db.query(
                "SELECT d.*, c.first_name, c.last_name FROM note_draft d JOIN client c ON c.id = d.client_id ORDER BY d.updated_at DESC"
            ),
        )

    @r.get("/setup")
    def setup_form(request: Request):
        if core.initialised:
            raise Redirect("/unlock")
        return web.page(request, "setup.html")

    @r.post("/setup")
    def setup(request: Request, password: str = Form(""), confirm: str = Form("")):
        if core.initialised:
            raise Redirect("/unlock")
        problem = _password_problem(password, confirm)
        if problem:
            return web.page(request, "setup.html", status_code=400, error=problem)
        return signed_in(core.setup(password))

    @r.get("/unlock")
    def unlock_form(request: Request):
        if not core.initialised:
            raise Redirect("/setup")
        if web.authed(request):
            raise Redirect("/")
        return web.page(request, "unlock.html")

    @r.post("/unlock")
    def unlock(request: Request, password: str = Form("")):
        if not core.initialised:
            raise Redirect("/setup")
        try:
            return signed_in(core.unlock(password))
        except WrongPassword:
            return web.page(request, "unlock.html", status_code=401, error="Wrong password.")
        except Throttled as exc:
            return web.page(
                request,
                "unlock.html",
                status_code=429,
                error=f"Too many wrong attempts. Try again in {exc.seconds} seconds.",
            )
        except SchemaTooNew:
            return web.page(
                request,
                "unlock.html",
                status_code=409,
                error="These records were last opened by a newer version of FoodForThought. Update the app.",
            )

    @r.post("/lock")
    def lock(request: Request):
        if web.authed(request):
            core.lock("manual")
        return signed_out("/unlock?msg=locked")

    def settings_page(request: Request, error: str | None = None):
        copy = backup.safety_copy(cfg)
        return web.page(
            request,
            "settings.html",
            status_code=400 if error else 200,
            error=error,
            auto_lock=core.auto_lock_minutes,
            last_backup=web.db.get_setting("last_backup"),
            safety_copy=copy.name if copy else None,
            about=updates.current(),
            launcher_supported=launcher.supported(),
            launcher_installed=launcher.installed(),
        )

    @r.get("/settings")
    def settings(request: Request, _: None = unlocked):
        return settings_page(request)

    @r.post("/settings/auto-lock")
    def set_auto_lock(request: Request, minutes: str = Form(""), _: None = unlocked):
        if not minutes.isdigit() or not 1 <= int(minutes) <= 120:
            return settings_page(request, "Auto-lock must be between 1 and 120 minutes.")
        core.set_auto_lock(int(minutes))
        raise Redirect("/settings?msg=saved")

    @r.post("/settings/password")
    def change_password(
        request: Request,
        current: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
        _: None = unlocked,
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

    @r.post("/settings/safety-copy/delete")
    def delete_safety_copy(_: None = unlocked):
        backup.delete_safety_copies(cfg)
        web.log("safety_copy_deleted")
        raise Redirect("/settings?msg=safety-deleted")

    @r.post("/backup")
    def make_backup(_: None = unlocked):
        name = backup.backup_filename()
        tmp = cfg.tmp_dir / f"{secrets.token_hex(8)}{backup.BACKUP_SUFFIX}"
        previous = web.db.get_setting("last_backup")
        web.db.set_setting("last_backup", audit.now_iso())
        web.log("backup_created", name)
        try:
            backup.create_backup(cfg, web.db, core.keys, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            if previous is None:
                web.db.execute("DELETE FROM settings WHERE key = 'last_backup'")
            else:
                web.db.set_setting("last_backup", previous)
            web.log("backup_failed", name)
            raise
        return FileResponse(
            tmp,
            filename=name,
            media_type="application/octet-stream",
            background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)),
        )

    @r.get("/restore")
    def restore_form(request: Request):
        return web.page(request, "restore.html", initialised=core.initialised)

    @r.post("/restore")
    def restore(request: Request, file: UploadFile = File(...), password: str = Form("")):
        handle, tmp = tempfile.mkstemp(dir=cfg.tmp_dir)
        try:
            with open(handle, "wb") as out:
                shutil.copyfileobj(file.file, out)
            try:
                stage = backup.unpack_and_verify(cfg, Path(tmp), password)
            except backup.BackupError as exc:
                return web.page(
                    request, "restore.html", status_code=400, error=str(exc), initialised=core.initialised
                )
        finally:
            Path(tmp).unlink(missing_ok=True)
        with core.mutex:
            core.lock("restore")
            kept = backup.install_restored(cfg, stage)
        detail = f"previous data kept in {kept.name}" if kept else ""
        audit.record_pending(cfg.pending_audit_path, "restore", detail)
        return signed_out("/unlock?msg=restored")

    @r.get("/audit")
    def audit_log(request: Request, _: None = unlocked):
        return web.page(request, "audit.html", entries=audit.recent(web.db, 500))

    @r.get("/api/status")
    def status(request: Request):
        ok = web.authed(request)
        return {
            "initialised": core.initialised,
            "unlocked": ok,
            "seconds_left": core.seconds_left() if ok else 0,
        }

    @r.post("/api/touch")
    def touch(request: Request):
        if not web.authed(request):
            return JSONResponse({"unlocked": False}, status_code=401)
        core.touch()
        return {"unlocked": True, "seconds_left": core.seconds_left()}

    return r

from __future__ import annotations

import asyncio
import shutil
import time
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from . import account, clients, diaries, diary, foods, forms, invoices, letters, meal_plans, measurements, notes, practice, privacy, pwa, recipes, targets, updates
from .config import Config, default_data_dir
from .core import Core
from .web import COOKIE, PKG, Redirect, Web

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


def create_app(cfg: Config | None = None, clock: Callable[[], float] = time.monotonic) -> FastAPI:
    cfg = cfg or Config(default_data_dir())
    core = Core(cfg, clock)
    shutil.rmtree(cfg.tmp_dir, ignore_errors=True)
    cfg.tmp_dir.mkdir(exist_ok=True)
    web = Web(cfg, core)

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

    hosts = {f"localhost:{cfg.port}", f"127.0.0.1:{cfg.port}"}
    origins = {f"http://{host}" for host in hosts}

    def to_lock_screen(request: Request, url: str) -> Response:
        if request.headers.get("hx-request"):
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url, status_code=303)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return PlainTextResponse("Bad host", status_code=400)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in origins:
                return PlainTextResponse("Cross-site request blocked", status_code=403)
        core.expire_if_idle()
        was_authed = web.authed(request)
        try:
            response = await call_next(request)
        except Exception:
            if not was_authed or core.unlocked:
                raise
            response = to_lock_screen(request, "/unlock?msg=auto")
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(Redirect)
    async def on_redirect(request: Request, exc: Redirect):
        if exc.url in ("/unlock", "/setup"):
            return to_lock_screen(request, exc.url)
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(HTTPException)
    async def on_http_error(request: Request, exc: HTTPException):
        detail = exc.detail if exc.status_code != 404 or exc.detail != "Not Found" else "That page does not exist."
        return web.page(request, "error.html", status_code=exc.status_code, detail=detail)

    @app.exception_handler(RequestValidationError)
    async def on_bad_request(request: Request, _: RequestValidationError):
        return web.page(request, "error.html", status_code=404, detail="That page does not exist.")

    for feature in (pwa, account, practice, clients, diary, notes, forms, measurements, targets, diaries, meal_plans, invoices, letters, privacy, foods, recipes, updates):
        app.include_router(feature.router(web))

    return app


__all__ = ["create_app", "COOKIE"]

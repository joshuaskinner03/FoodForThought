from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .web import PKG, Web

MANIFEST = {
    "id": "/",
    "name": "FoodForThought",
    "short_name": "FoodForThought",
    "description": "Client records and nutrition analysis, kept on this device",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "background_color": "#f6f4ee",
    "theme_color": "#2f6b4f",
    "icons": [
        {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
    "launch_handler": {"client_mode": ["focus-existing", "auto"]},
}


def router(web: Web) -> APIRouter:
    r = APIRouter()
    worker = (PKG / "static" / "sw.js").read_text(encoding="utf-8")

    @r.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse(MANIFEST, media_type="application/manifest+json")

    @r.get("/sw.js")
    def service_worker():
        return Response(worker, media_type="text/javascript")

    @r.get("/offline")
    def offline(request: Request):
        return web.templates.TemplateResponse(request, "offline.html", {})

    return r

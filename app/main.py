"""Application FastAPI — point d'entrée."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.database import init_db
from app.routers import api, pages, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("consultation")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Base de données initialisée (%s)", settings.DATABASE_URL.split("://")[0])
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(ws.router)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api"):
        return JSONResponse(status_code=404, content={"detail": "Introuvable."})
    from app.templating import templates

    return templates.TemplateResponse(
        request, "not_found.html", {"code": "", "app_name": settings.APP_NAME}, status_code=404
    )

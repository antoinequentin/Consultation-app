"""Application FastAPI — point d'entrée."""
from __future__ import annotations

import logging
import os
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
    if not os.getenv("ADMIN_SECRET"):
        logger.warning(
            "ADMIN_SECRET non défini : secret généré pour cette session -> %s "
            "(à fixer en variable d'environnement en production, ce secret changera "
            "à chaque redémarrage sinon). Accès : /superadmin?secret=%s",
            settings.ADMIN_SECRET, settings.ADMIN_SECRET,
        )
    yield


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")

# Images uploadées (§2/§7) : servies depuis settings.DATA_DIR/uploads, PAS
# depuis app/static/uploads — ce chemin doit rester sous le volume
# persistant Kubernetes monté sur /app/data (cf. storage.py pour le détail
# de ce choix). StaticFiles exige que le répertoire existe déjà au moment
# du montage, d'où la création explicite ici plutôt que de compter sur
# l'initialisation paresseuse de LocalStorageBackend (qui n'a lieu qu'au
# premier upload réel).
_uploads_dir = settings.DATA_DIR / "uploads"
_uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(_uploads_dir)), name="uploads")

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

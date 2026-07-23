"""Routes HTML (rendu serveur via Jinja2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import crud
from app.config import settings
from app.database import get_db
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(request, "landing.html", {"app_name": settings.APP_NAME})


@router.get("/w/{code}", response_class=HTMLResponse)
def participant_page(request: Request, code: str, db: Session = Depends(get_db)):
    webinar = crud.get_webinar_by_code(db, code)
    if webinar is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"code": code.upper(), "app_name": settings.APP_NAME}, status_code=404
        )
    return templates.TemplateResponse(
        request, "participant.html", {"webinar": webinar, "app_name": settings.APP_NAME}
    )


@router.get("/w/{code}/host", response_class=HTMLResponse)
def host_page(request: Request, code: str, db: Session = Depends(get_db)):
    webinar = crud.get_webinar_by_code(db, code)
    if webinar is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"code": code.upper(), "app_name": settings.APP_NAME}, status_code=404
        )
    return templates.TemplateResponse(
        request, "host.html", {"webinar": webinar, "app_name": settings.APP_NAME}
    )


@router.get("/w/{code}/projector", response_class=HTMLResponse)
def projector_page(request: Request, code: str, db: Session = Depends(get_db)):
    webinar = crud.get_webinar_by_code(db, code)
    if webinar is None:
        return templates.TemplateResponse(
            request, "not_found.html", {"code": code.upper(), "app_name": settings.APP_NAME}, status_code=404
        )
    return templates.TemplateResponse(
        request, "projector.html", {"webinar": webinar, "app_name": settings.APP_NAME}
    )

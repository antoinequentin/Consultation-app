"""Endpoints REST (tout ce qui n'est pas la diffusion temps réel WebSocket)."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, models, schemas, security, utils
from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api")


def _public_urls(request: Request, code: str) -> dict:
    base = settings.PUBLIC_BASE_URL.rstrip("/") if settings.PUBLIC_BASE_URL else str(request.base_url).rstrip("/")
    return {
        "participant_url": f"{base}/w/{code}",
        "host_url": f"{base}/w/{code}/host",
        "projector_url": f"{base}/w/{code}/projector",
    }


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.post("/webinars", response_model=schemas.WebinarCreateResponse)
def create_webinar(payload: schemas.WebinarCreate, request: Request, db: Session = Depends(get_db)):
    webinar = crud.create_webinar(
        db,
        title=payload.title,
        password_hash=security.hash_password(payload.password),
        moderation_enabled=payload.moderation_enabled,
        allow_project_proposals=payload.allow_project_proposals,
    )

    if payload.seed_project_title and payload.seed_project_title.strip():
        crud.create_project(
            db,
            webinar_id=webinar.id,
            title=payload.seed_project_title,
            description=payload.seed_project_description or "",
            context=payload.seed_project_context or "",
            proposed_by=None,
            proposed_by_name="Animateur",
            status="proposed",
        )

    urls = _public_urls(request, webinar.code)
    return schemas.WebinarCreateResponse(
        code=webinar.code,
        title=webinar.title,
        host_token=security.create_host_token(webinar.code),
        **urls,
    )


def _get_webinar_or_404(db: Session, code: str):
    webinar = crud.get_webinar_by_code(db, code)
    if webinar is None:
        raise HTTPException(status_code=404, detail="Webinaire introuvable.")
    return webinar


@router.get("/webinars/{code}")
def webinar_info(code: str, db: Session = Depends(get_db)):
    webinar = _get_webinar_or_404(db, code)
    return {"code": webinar.code, "title": webinar.title, "phase": webinar.phase}


@router.post("/webinars/{code}/host/login", response_model=schemas.HostLoginResponse)
def host_login(code: str, payload: schemas.HostLogin, db: Session = Depends(get_db)):
    webinar = _get_webinar_or_404(db, code)
    if not security.verify_password(payload.password, webinar.admin_password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe incorrect.")
    return schemas.HostLoginResponse(token=security.create_host_token(webinar.code))


def _require_host(db: Session, code: str, token: str) -> None:
    webinar = _get_webinar_or_404(db, code)
    if not token or not security.verify_host_token(token, webinar.code):
        raise HTTPException(status_code=401, detail="Authentification animateur requise.")


@router.get("/webinars/{code}/qrcode.png")
def qrcode_png(code: str, request: Request, db: Session = Depends(get_db)):
    import qrcode

    webinar = _get_webinar_or_404(db, code)
    url = _public_urls(request, webinar.code)["participant_url"]
    img = qrcode.make(url, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@router.get("/webinars/{code}/export.zip")
def export_zip(code: str, token: str = Query(...), db: Session = Depends(get_db)):
    webinar = _get_webinar_or_404(db, code)
    _require_host(db, code, token)

    projects = crud.list_projects(db, webinar.id)
    axes_by_project = {p.id: crud.list_axes(db, p.id) for p in projects}
    propositions_by_axis: dict[int, list] = {}
    cotations_by_axis: dict[int, list] = {}
    proposition_votes_by_axis: dict[int, list] = {}
    for axes in axes_by_project.values():
        for axis in axes:
            props: list = []
            for t in ("positifs", "negatifs", "ameliorations"):
                props.extend(crud.list_propositions(db, axis_id=axis.id, type_=t, statuses=None))
            propositions_by_axis[axis.id] = props
            cotations_by_axis[axis.id] = crud.list_cotations(db, axis.id)
            proposition_votes_by_axis[axis.id] = crud.list_proposition_votes_for_axis(db, axis.id)

    project_votes = list(db.scalars(select(models.ProjectVote).where(models.ProjectVote.webinar_id == webinar.id)))

    data = utils.build_export_zip(
        webinar, projects, axes_by_project, propositions_by_axis, cotations_by_axis, project_votes,
        proposition_votes_by_axis=proposition_votes_by_axis,
    )
    filename = f"export-{webinar.code}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

"""Endpoints REST (tout ce qui n'est pas la diffusion temps réel WebSocket)."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud, models, schemas, security, storage, utils
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


def _check_admin_secret(secret: str | None) -> None:
    if not secret or secret != settings.ADMIN_SECRET:
        raise HTTPException(status_code=404)


@router.post("/superadmin/webinars/{webinar_id}/delete")
def superadmin_delete_webinar(webinar_id: int, secret: str | None = None, db: Session = Depends(get_db)):
    """Suppression complète d'un webinaire (nettoyage de base en
    développement/démo) — voir pages.py pour la vue qui expose ce bouton.
    Cascade déjà en place au niveau du schéma (PRAGMA foreign_keys=ON,
    cf. database.py) : projets, participants, axes, propositions, votes,
    cotations et historique des minuteurs de ce webinaire disparaissent
    avec lui."""
    _check_admin_secret(secret)
    webinar = db.get(models.Webinar, webinar_id)
    if webinar is None:
        raise HTTPException(status_code=404, detail="Webinaire introuvable.")
    crud.delete_webinar(db, webinar)
    return {"status": "ok"}


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


@router.post("/webinars/{code}/participants/erase")
def erase_participant_data(code: str, payload: schemas.ParticipantErasurePayload, db: Session = Depends(get_db)):
    """Droit à l'effacement (RGPD, §7). Authentification volontairement
    légère : connaître son propre participant_id (uuid généré côté client,
    stocké seulement dans le navigateur du participant) suffit à agir sur
    ses propres données — cohérent avec le reste de l'app, qui ne demande
    aucun compte ni mot de passe participant. Cette route ne révèle jamais
    si un participant_id existe ou non pour ne pas servir d'oracle : elle
    répond succès dans tous les cas.
    """
    webinar = _get_webinar_or_404(db, code)
    participant = db.get(models.Participant, payload.participant_id)
    if participant is None or participant.webinar_id != webinar.id:
        # Ne rien révéler sur l'existence du participant_id : réponse
        # identique à un effacement réussi.
        return {"status": "ok"}

    if payload.mode == "erase":
        crud.erase_participant(db, participant)
    else:
        crud.anonymize_participant(db, participant)
    return {"status": "ok", "mode": payload.mode}


@router.get("/webinars/{code}/projects/duplicable")
def list_duplicable_projects(code: str, token: str = Query(...), db: Session = Depends(get_db)):
    """Mode "projet type" (§7) : liste les projets d'UN AUTRE webinaire déjà
    animé, pour permettre à l'animateur de les reproposer dans le webinaire
    courant sans ressaisie. `code`/`token` ici désignent le webinaire
    SOURCE (celui dont on veut récupérer les projets) — l'animateur doit
    en connaître le code et le mot de passe, ce qui suffit à prouver qu'il
    en est bien l'animateur (pas de notion de compte animateur persistant
    dans cette application)."""
    webinar = _get_webinar_or_404(db, code)
    _require_host(db, code, token)
    projects = crud.list_projects(db, webinar.id)
    return {
        "webinar": {"code": webinar.code, "title": webinar.title},
        "projects": [
            {
                "id": p.id, "title": p.title, "description": p.description,
                "type_projet": p.type_projet, "territoire": p.territoire,
                "image_url": p.image_url, "status": p.status,
            }
            for p in projects
        ],
    }


@router.post("/webinars/{code}/projects/duplicate")
def duplicate_project(
    code: str, payload: schemas.ProjectDuplicatePayload, token: str = Query(...), db: Session = Depends(get_db)
):
    """Duplique un projet d'un webinaire source vers CE webinaire (`code`,
    le webinaire cible/courant). Le token vérifié est celui du webinaire
    cible : dupliquer vers un webinaire ne nécessite d'être authentifié que
    sur celui-ci, la lecture du projet source ayant déjà été validée par
    `list_duplicable_projects` (qui, elle, vérifie le token du webinaire
    source)."""
    target = _get_webinar_or_404(db, code)
    _require_host(db, code, token)

    source_project = crud.get_project(db, payload.source_project_id)
    if source_project is None:
        raise HTTPException(status_code=404, detail="Projet source introuvable.")

    new_project = crud.duplicate_project(db, source=source_project, target_webinar_id=target.id)
    return {
        "id": new_project.id, "title": new_project.title, "duplicated_from_id": new_project.duplicated_from_id,
    }


@router.get("/webinars/{code}/export.pdf")
def export_pdf(code: str, token: str = Query(...), db: Session = Depends(get_db)):
    """Rapport PDF de restitution (§7) : synthèse lisible du projet consulté
    (fiche projet + impacts positifs/négatifs approuvés, cotation,
    améliorations, par axe), en complément de l'export CSV/ZIP déjà
    existant qui, lui, sert plutôt à l'analyse des données brutes."""
    webinar = _get_webinar_or_404(db, code)
    _require_host(db, code, token)

    project = crud.get_project(db, webinar.current_project_id) if webinar.current_project_id else None
    axes_data: list[dict] = []
    if project is not None:
        axes = crud.list_axes(db, project.id)
        for axis in axes:
            positifs = crud.list_propositions(db, axis_id=axis.id, type_="positifs", statuses=None)
            negatifs = crud.list_propositions(db, axis_id=axis.id, type_="negatifs", statuses=None)
            ameliorations = crud.list_propositions(db, axis_id=axis.id, type_="ameliorations", statuses=None)
            cotation_counts = crud.get_cotation_counts(db, axis.id)
            axes_data.append({
                "axis": axis,
                "positifs": positifs,
                "negatifs": negatifs,
                "ameliorations": ameliorations,
                "cotation_counts": cotation_counts,
                "cotation_total": sum(cotation_counts.values()),
            })

    data = utils.build_pdf_report(webinar, project, axes_data)
    filename = f"restitution-{webinar.code}.pdf"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/webinars/{code}/upload-image")
async def upload_image(code: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload réel d'image de projet (§2 / §7), en complément du champ
    "coller un lien URL" déjà existant : un participant ou un animateur
    peut désormais importer une photo depuis son ordinateur. Accessible à
    quiconque connaît le code du webinaire — pas de token requis — cohérent
    avec le reste des actions participant (proposer un projet, voter...)
    qui ne demandent pas de compte.

    Le stockage réel est délégué à `app.storage` (backend "local" par
    défaut, fonctionnel immédiatement ; voir ce module pour brancher un
    stockage objet S3/MinIO en production multi-instances)."""
    _get_webinar_or_404(db, code)  # 404 propre si le code est invalide, avant de lire le fichier

    content = await file.read()
    try:
        storage.validate_image(content=content, content_type=file.content_type or "")
    except storage.StorageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    backend = storage.get_storage_backend()
    url = backend.save(
        content=content, content_type=file.content_type, original_filename=file.filename or "image"
    )
    return {"image_url": url}


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
    step_timing_logs = crud.list_step_timing_logs(db, webinar.id)

    data = utils.build_export_zip(
        webinar, projects, axes_by_project, propositions_by_axis, cotations_by_axis, project_votes,
        proposition_votes_by_axis=proposition_votes_by_axis,
        step_timing_logs=step_timing_logs,
    )
    filename = f"export-{webinar.code}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

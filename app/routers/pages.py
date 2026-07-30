"""Routes HTML (rendu serveur via Jinja2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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


# --------------------------------------------------------------------------
# Pages légales (DSFR) : mentions légales, accessibilité, données
# personnelles. Contenu volontairement générique/à compléter — ce ne sont
# pas des informations que Claude peut inventer de façon fiable (raison
# sociale de l'éditeur, directeur de publication, coordonnées de
# l'hébergeur réel, résultat d'un audit RGAA...), donc chaque page indique
# clairement les blancs à remplir plutôt que d'improviser des données qui
# auraient l'air réelles sans l'être.
# --------------------------------------------------------------------------

@router.get("/mentions-legales", response_class=HTMLResponse)
def mentions_legales(request: Request):
    return templates.TemplateResponse(
        request, "legal_page.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "Mentions légales",
            "sections": [
                ("Éditeur du site", "[Nom de la structure éditrice, adresse, à compléter]"),
                ("Directeur de la publication", "[Nom et fonction, à compléter]"),
                ("Hébergement", "[Nom et adresse de l'hébergeur, à compléter]"),
                ("Contact", "[Adresse email ou formulaire de contact, à compléter]"),
            ],
        },
    )


@router.get("/accessibilite", response_class=HTMLResponse)
def accessibilite(request: Request):
    return templates.TemplateResponse(
        request, "legal_page.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "Accessibilité : non conforme",
            "sections": [
                (
                    "État de conformité",
                    "Ce site n'a pas encore fait l'objet d'un audit d'accessibilité RGAA. "
                    "En l'absence d'audit, il ne peut être déclaré ni conforme ni "
                    "partiellement conforme : cette page sera mise à jour dès qu'un audit "
                    "aura été réalisé.",
                ),
                (
                    "Établir cette page correctement",
                    "Une déclaration d'accessibilité conforme au RGAA nécessite un audit "
                    "réalisé selon la méthodologie officielle, un schéma pluriannuel, et un "
                    "plan d'actions annuel. Voir https://accessibilite.numerique.gouv.fr/ "
                    "pour la méthodologie et le générateur de déclaration officiel.",
                ),
                (
                    "Signaler un défaut d'accessibilité",
                    "[Adresse email ou formulaire de contact pour signaler un problème "
                    "d'accessibilité, à compléter]",
                ),
            ],
        },
    )


@router.get("/donnees-personnelles", response_class=HTMLResponse)
def donnees_personnelles(request: Request):
    return templates.TemplateResponse(
        request, "legal_page.html",
        {
            "app_name": settings.APP_NAME,
            "page_title": "Données personnelles",
            "sections": [
                (
                    "Données collectées",
                    "Un identifiant technique généré aléatoirement (aucune information "
                    "permettant de vous identifier) et, si vous le renseignez, un nom "
                    "d'affichage facultatif. Aucune adresse email ni compte n'est requis "
                    "pour participer.",
                ),
                (
                    "Vos droits",
                    "Vous pouvez à tout moment anonymiser ou effacer entièrement vos "
                    "contributions depuis le bouton \"Gérer mes données\" présent au bas de "
                    "l'écran participant, sans avoir à contacter qui que ce soit.",
                ),
                (
                    "Contact",
                    "[Adresse email du délégué à la protection des données ou contact "
                    "RGPD de la structure éditrice, à compléter]",
                ),
            ],
        },
    )


# --------------------------------------------------------------------------
# Vue super-admin (nettoyage de base, développement/démo)
# --------------------------------------------------------------------------
# Volontairement non liée depuis aucune page publique ("vue cachée") et
# protégée par un secret partagé (ADMIN_SECRET, cf. config.py), pas par un
# compte utilisateur — cohérent avec le reste de l'app qui n'a pas cette
# notion. Ne pas exposer /superadmin sur un déploiement public sans avoir
# fixé ADMIN_SECRET explicitement (sinon un secret différent est régénéré
# à chaque redémarrage, ce qui est déjà une protection en soi, mais reste
# moins robuste qu'un secret fixé et gardé confidentiel).

def _check_admin_secret(secret: str | None) -> None:
    if not secret or secret != settings.ADMIN_SECRET:
        raise HTTPException(status_code=404)  # 404, pas 401/403 : ne révèle
        # même pas l'existence de la page à qui n'a pas le secret.


@router.get("/superadmin", response_class=HTMLResponse)
def superadmin_page(request: Request, secret: str | None = None, db: Session = Depends(get_db)):
    _check_admin_secret(secret)
    webinars = crud.list_all_webinars(db)
    return templates.TemplateResponse(
        request, "superadmin.html",
        {
            "app_name": settings.APP_NAME,
            "secret": secret,
            "webinars": [
                {
                    "id": w.id, "code": w.code, "title": w.title, "phase": w.phase,
                    "created_at": w.created_at, "nb_participants": len(w.participants),
                    "nb_projects": len(w.projects),
                }
                for w in webinars
            ],
        },
    )

"""
Cœur métier de l'application : machine à états du webinaire et construction
des "snapshots" d'état envoyés aux clients via WebSocket.

Déroulé d'un webinaire (nouveau, vs l'app d'origine qui n'avait qu'un seul
projet fixe) :

    LOBBY
      -> (host) start_project_submission
    PROJECT_SUBMISSION   (les participants proposent des projets)
      -> (host) close_submission_open_vote
    PROJECT_VOTE          (les participants votent pour LE projet à étudier)
      -> (host) select_project(id)
    CONSULTATION           (déroulé identique à l'app d'origine, par axe :
                             POSITIFS -> NEGATIFS -> VOTE -> AMELIORATIONS,
                             puis éventuellement l'axe suivant du projet)
      -> (host) end_consultation
    ENDED
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import crud, models, utils
from app.models import ConsultationStep, PropositionStatus, WebinarPhase

MAX_PROJECTS_PER_PARTICIPANT = 3

# Minuteur par étape (§5.1) — associe chaque étape de consultation au champ
# de durée correspondant sur `Webinar`. Une durée à None signifie "pas de
# minuteur pour cette étape" (comportement par défaut, opt-in par l'animateur).
STEP_DURATION_FIELD = {
    ConsultationStep.POSITIFS: "step_duration_positifs",
    ConsultationStep.NEGATIFS: "step_duration_negatifs",
    ConsultationStep.VOTE: "step_duration_vote",
    ConsultationStep.AMELIORATIONS: "step_duration_ameliorations",
}


def _step_duration_seconds(webinar: models.Webinar, step: int) -> int | None:
    try:
        field = STEP_DURATION_FIELD.get(ConsultationStep(step))
    except ValueError:
        return None
    return getattr(webinar, field) if field else None


def _iso_utc(value: dt.datetime | None) -> str | None:
    """Sérialise un datetime pour le JSON envoyé au client, en garantissant
    TOUJOURS un suffixe de fuseau explicite (`+00:00`), même si `value` est
    naive (ce qui arrive systématiquement après relecture depuis SQLite,
    qui ne persiste pas le tzinfo — cf. `_touch_step_timer` plus haut pour
    le même problème côté comparaison serveur).

    Sans ce garde-fou, `datetime.isoformat()` sur un datetime naive ne
    produit AUCUN suffixe de fuseau (ex: "2026-07-29T09:26:43"), que
    `new Date(...)` côté navigateur interprète alors comme une heure
    LOCALE plutôt qu'UTC. Pour un participant à Paris (UTC+2 en été),
    ça décale le calcul du minuteur de 2h vers le passé : le compte à
    rebours affiche aussitôt 0:00, comme si l'étape avait déjà débordé
    dès son lancement — c'est le bug "le minuteur ne défile pas",
    puisqu'il est en réalité déjà à zéro dès le premier rendu."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def _touch_step_timer(webinar: models.Webinar, db: Session | None = None, *, log_previous: bool = True) -> None:
    """Réinitialise le point de départ du minuteur : à appeler à chaque
    changement d'étape, d'axe, ou de sélection de projet (tout ce qui rend
    le compte à rebours précédent obsolète).

    Si `db` est fourni et `log_previous` est vrai, journalise d'abord le
    temps réellement passé sur l'étape qui se termine (§7.5) — ignoré si
    aucune étape de consultation n'était en cours (`step == NONE`) ou si
    `step_started_at` n'était pas encore posé (tout premier changement)."""
    now = models.utcnow()
    if (
        log_previous
        and db is not None
        and webinar.step_started_at is not None
        and webinar.current_step != ConsultationStep.NONE.value
    ):
        planned = _step_duration_seconds(webinar, webinar.current_step)
        started = webinar.step_started_at
        # SQLite ne persiste pas le tzinfo : après relecture, `started` peut
        # redevenir naive même si `utcnow()` produit un datetime aware.
        # On aligne les deux en aware-UTC avant de soustraire.
        if started.tzinfo is None:
            started = started.replace(tzinfo=dt.timezone.utc)
        db.add(models.StepTimingLog(
            webinar_id=webinar.id,
            project_id=webinar.current_project_id,
            axis_index=webinar.current_axis_index,
            step=webinar.current_step,
            started_at=webinar.step_started_at,
            ended_at=now,
            duration_seconds=max(0, int((now - started).total_seconds())),
            planned_duration_seconds=planned,
        ))
    webinar.step_started_at = now


class StateError(Exception):
    """Erreur métier "normale" (mauvaise étape, doublon, etc.) -> renvoyée
    au seul client fautif sous forme de message d'erreur, sans rien casser."""


# --------------------------------------------------------------------------
# Construction de l'état diffusé aux clients
# --------------------------------------------------------------------------

def _project_public(p: models.Project, *, votes: int | None, participant_id: str | None) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "context": p.context,
        "image_url": p.image_url,
        "map_url": p.map_url,
        "porteur": p.porteur,
        "budget": p.budget,
        "territoire": p.territoire,
        "stade": p.stade,
        "proposed_by_name": p.proposed_by_name,
        "status": p.status,
        "votes": votes,
        "is_mine": bool(participant_id) and p.proposed_by == participant_id,
    }


def _proposition_raw(pr: models.Proposition) -> dict:
    """Représentation plate d'une proposition (uniquement des colonnes déjà
    chargées par la requête, jamais de relation) : sûre à conserver et
    réutiliser même après la fermeture de la session SQLAlchemy qui l'a
    produite — nécessaire pour personnaliser la visibilité par participant
    sans réémettre une requête par participant (voir `personalize_propositions`
    et son usage dans websocket_manager.broadcast_state_now)."""
    return {
        "id": pr.id,
        "texte": pr.texte,
        "status": pr.status,
        "participant_id": pr.participant_id,
        "nb_accord": pr.nb_accord,
        "nb_desaccord": pr.nb_desaccord,
        "nb_passer": pr.nb_passer,
        "total_votes": pr.total_votes,
        "consensus_pct": pr.consensus_pct,
    }


def _proposition_public_from_raw(rp: dict, *, participant_id: str | None) -> dict:
    return {
        "id": rp["id"],
        "texte": rp["texte"],
        "status": rp["status"],
        "nb_accord": rp["nb_accord"],
        "nb_desaccord": rp["nb_desaccord"],
        "nb_passer": rp["nb_passer"],
        "total_votes": rp["total_votes"],
        "consensus_pct": rp["consensus_pct"],
        "is_mine": bool(participant_id) and rp["participant_id"] == participant_id,
        # NB : "participant_id" (l'auteur réel) n'est volontairement PAS
        # inclus dans la sortie publique — seul "is_mine" (calculé du point
        # de vue du destinataire) est exposé, pour ne jamais révéler
        # l'identité de l'auteur d'une proposition aux autres participants.
    }


def personalize_propositions(raw_props: list[dict], *, is_host: bool, participant_id: str | None) -> list[dict]:
    """Filtre + personnalise une liste de propositions déjà récupérées
    (sous forme de dicts, via `_proposition_raw`) pour UN destinataire
    donné, sans requête SQL supplémentaire. Isolé de `build_state` pour
    permettre à `websocket_manager.broadcast_state_now` de ne récupérer les
    propositions qu'UNE seule fois par diffusion puis de les personnaliser
    à moindre coût pour chaque participant connecté (au lieu de rejouer
    `build_state` intégralement pour chacun)."""
    if is_host:
        visible = raw_props
    else:
        visible = [
            rp for rp in raw_props
            if rp["status"] == PropositionStatus.APPROVED.value
            or (participant_id and rp["participant_id"] == participant_id)
        ]
    return [_proposition_public_from_raw(rp, participant_id=participant_id) for rp in visible]


def _raw_propositions_for(db: Session, axis: models.Axis, step: int) -> list[dict] | None:
    prop_type = models.STEP_TO_PROPOSITION_TYPE.get(ConsultationStep(step)) if step in (1, 2, 4) else None
    if prop_type is None:
        return None
    all_props = crud.list_propositions(db, axis_id=axis.id, type_=prop_type.value, statuses=None)
    return [_proposition_raw(p) for p in all_props]


def get_current_raw_propositions(db: Session, webinar: models.Webinar) -> list[dict] | None:
    """Récupère les propositions BRUTES (non filtrées, non personnalisées)
    de l'étape en cours, ou None si l'étape actuelle n'implique pas de
    propositions (vote de cotation, ou hors phase de consultation). Point
    d'entrée utilisé par `websocket_manager.broadcast_state_now`, qui ne
    dispose pas déjà d'un axe résolu (contrairement à `build_state`)."""
    if webinar.phase != WebinarPhase.CONSULTATION.value:
        return None
    axis = crud.get_current_axis(db, webinar)
    if axis is None:
        return None
    return _raw_propositions_for(db, axis, webinar.current_step)


def build_state(db: Session, webinar: models.Webinar, *, participant_id: str | None, is_host: bool) -> dict:
    state: dict = {
        "webinar": {
            "code": webinar.code,
            "title": webinar.title,
            "phase": webinar.phase,
            "phase_label": utils.phase_name(webinar.phase),
            "moderation_enabled": webinar.moderation_enabled,
            "allow_project_proposals": webinar.allow_project_proposals,
            # NB : ce champ était déjà lu côté participant.js sans jamais
            # être envoyé par le serveur (le "?? 5" côté client masquait
            # silencieusement l'absence, avec la même valeur par défaut :
            # aucun impact visible jusqu'ici, mais corrigé pour rester
            # correct si cette valeur devient un jour configurable).
            "max_propositions_per_participant": webinar.max_propositions_per_participant,
        },
        "is_host": is_host,
        # Participants ayant rejoint depuis le début du webinaire (cumulé),
        # à distinguer de "participant_count" (connexions WebSocket
        # actuellement ouvertes, ajouté par le gestionnaire WebSocket) —
        # équivalent de la statistique "Participants" du panneau animateur
        # d'origine (nombre de participant_id distincts).
        "total_participants_joined": crud.count_participants(db, webinar.id),
    }

    if webinar.phase in (WebinarPhase.PROJECT_SUBMISSION.value, WebinarPhase.PROJECT_VOTE.value):
        projects = crud.list_projects(db, webinar.id)
        vote_counts = crud.count_project_votes(db, webinar.id)
        show_votes = webinar.phase == WebinarPhase.PROJECT_VOTE.value
        state["project_phase"] = {
            "projects": [
                _project_public(p, votes=vote_counts.get(p.id, 0) if show_votes else None, participant_id=participant_id)
                for p in projects
            ],
            "total_votes": sum(vote_counts.values()),
            "max_projects_per_participant": MAX_PROJECTS_PER_PARTICIPANT,
        }

    if webinar.phase in (WebinarPhase.CONSULTATION.value, WebinarPhase.ENDED.value) and webinar.current_project_id:
        project = crud.get_project(db, webinar.current_project_id)
        axes = crud.list_axes(db, webinar.current_project_id)
        axis = None
        if axes:
            idx = max(0, min(webinar.current_axis_index, len(axes) - 1))
            axis = axes[idx]

        consultation: dict = {
            "project": {
                "id": project.id, "title": project.title, "description": project.description,
                "context": project.context, "image_url": project.image_url, "map_url": project.map_url,
                "porteur": project.porteur, "budget": project.budget,
                "territoire": project.territoire, "stade": project.stade,
                "proposed_by_name": project.proposed_by_name,
                # Conflit d'intérêt (§7) : signale au participant que le
                # projet actuellement en consultation est celui qu'il a
                # lui-même proposé — jamais calculé pour l'animateur/écran
                # de projection (participant_id est None dans ces cas), qui
                # n'ont pas à connaître l'identité des participants.
                "is_mine": bool(participant_id) and project.proposed_by == participant_id,
            } if project else None,
            "axis_index": webinar.current_axis_index,
            "axis_count": len(axes),
            "axis": {
                "id": axis.id, "texte": axis.texte, "categorie": axis.categorie,
                "color": utils.category_color(axis.categorie),
            } if axis else None,
            "step": webinar.current_step,
            "step_label": utils.step_name(webinar.current_step),
            # Minuteur (§5.1) : le client calcule lui-même le compte à
            # rebours à partir de ces deux valeurs (step_started_at +
            # step_duration_seconds), plutôt que de recevoir un "temps
            # restant" déjà calculé qui se périmerait entre deux diffusions.
            "step_started_at": _iso_utc(webinar.step_started_at),
            "step_duration_seconds": _step_duration_seconds(webinar, webinar.current_step),
            "step_durations": {
                "positifs": webinar.step_duration_positifs,
                "negatifs": webinar.step_duration_negatifs,
                "vote": webinar.step_duration_vote,
                "ameliorations": webinar.step_duration_ameliorations,
            },
        }

        if axis is not None:
            step = webinar.current_step
            prop_type = models.STEP_TO_PROPOSITION_TYPE.get(ConsultationStep(step)) if step in (1, 2, 4) else None
            if prop_type is not None:
                raw_props = _raw_propositions_for(db, axis, step) or []
                consultation["propositions"] = personalize_propositions(raw_props, is_host=is_host, participant_id=participant_id)
                consultation["proposition_type"] = prop_type.value

            if step == ConsultationStep.VOTE.value:
                counts = crud.get_cotation_counts(db, axis.id)
                total = sum(counts.values())
                consultation["cotation"] = {
                    "counts": counts,
                    "total": total,
                    "percentages": {k: (round(100 * v / total, 1) if total else 0.0) for k, v in counts.items()},
                }

        state["consultation"] = consultation

    return state


def build_you(db: Session, webinar: models.Webinar, *, participant_id: str | None) -> dict:
    """Informations propres à UN participant donné (jamais mises en cache /
    jamais partagées entre connexions)."""
    you: dict = {}
    if not participant_id:
        return you

    if webinar.phase in (WebinarPhase.PROJECT_SUBMISSION.value, WebinarPhase.PROJECT_VOTE.value):
        you["my_project_vote"] = crud.get_participant_project_vote(db, webinar_id=webinar.id, participant_id=participant_id)
        you["my_projects_count"] = crud.count_projects_by_participant(db, webinar.id, participant_id)

    if webinar.phase == WebinarPhase.CONSULTATION.value and webinar.current_project_id:
        axis = crud.get_current_axis(db, webinar)
        if axis is not None:
            step = webinar.current_step
            if step in (1, 2, 4):
                prop_type = models.STEP_TO_PROPOSITION_TYPE[ConsultationStep(step)]
                you["my_vote_map"] = crud.get_participant_proposition_votes(db, axis_id=axis.id, participant_id=participant_id)
                you["my_propositions_count"] = crud.count_propositions_by_participant(
                    db, axis_id=axis.id, participant_id=participant_id, type_=prop_type.value
                )
            if step == ConsultationStep.VOTE.value:
                you["my_cotation"] = crud.get_participant_cotation(db, axis_id=axis.id, participant_id=participant_id)
    return you


# --------------------------------------------------------------------------
# Actions PARTICIPANT
# --------------------------------------------------------------------------

def submit_project(db: Session, webinar: models.Webinar, participant_id: str, display_name: str | None, *, title: str, description: str, context: str, image_url: str | None, map_url: str | None = None, porteur: str | None = None, budget: str | None = None, territoire: str | None = None, stade: str | None = None) -> models.Project:
    if webinar.phase != WebinarPhase.PROJECT_SUBMISSION.value:
        raise StateError("La proposition de projets n'est pas (ou plus) ouverte.")
    if not webinar.allow_project_proposals:
        raise StateError("L'animateur a désactivé la proposition de nouveaux projets.")
    if crud.count_projects_by_participant(db, webinar.id, participant_id) >= MAX_PROJECTS_PER_PARTICIPANT:
        raise StateError(f"Vous avez atteint la limite de {MAX_PROJECTS_PER_PARTICIPANT} projets proposés.")
    return crud.create_project(
        db, webinar_id=webinar.id, title=title, description=description, context=context,
        image_url=image_url, map_url=map_url, porteur=porteur, budget=budget, territoire=territoire, stade=stade,
        proposed_by=participant_id, proposed_by_name=display_name,
    )


def vote_project(db: Session, webinar: models.Webinar, participant_id: str, project_id: int) -> None:
    if webinar.phase != WebinarPhase.PROJECT_VOTE.value:
        raise StateError("Le vote pour le projet n'est pas (ou plus) ouvert.")
    project = crud.get_project(db, project_id)
    if not project or project.webinar_id != webinar.id:
        raise StateError("Projet introuvable.")
    crud.cast_project_vote(db, webinar_id=webinar.id, project_id=project_id, participant_id=participant_id)


def submit_proposition(db: Session, webinar: models.Webinar, participant_id: str, *, prop_type: str, texte: str) -> models.Proposition:
    if webinar.phase != WebinarPhase.CONSULTATION.value:
        raise StateError("La consultation n'est pas en cours.")
    axis = crud.get_current_axis(db, webinar)
    if axis is None:
        raise StateError("Aucun axe actif.")
    expected = models.STEP_TO_PROPOSITION_TYPE.get(ConsultationStep(webinar.current_step))
    if expected is None or expected.value != prop_type:
        raise StateError("Ce n'est pas le moment de proposer ce type de contribution.")
    if crud.count_propositions_by_participant(db, axis_id=axis.id, participant_id=participant_id, type_=prop_type) >= webinar.max_propositions_per_participant:
        raise StateError(f"Vous avez atteint la limite de {webinar.max_propositions_per_participant} contributions pour cette étape.")
    status = PropositionStatus.PENDING.value if webinar.moderation_enabled else PropositionStatus.APPROVED.value
    return crud.create_proposition(db, axis_id=axis.id, participant_id=participant_id, type_=prop_type, texte=texte, status=status)


def vote_proposition(db: Session, webinar: models.Webinar, participant_id: str, *, proposition_id: int, vote: str) -> models.Proposition:
    if webinar.phase != WebinarPhase.CONSULTATION.value:
        raise StateError("La consultation n'est pas en cours.")
    if vote not in (v.value for v in models.VoteValue):
        raise StateError("Vote invalide.")
    proposition = crud.get_proposition(db, proposition_id)
    axis = crud.get_current_axis(db, webinar)
    if proposition is None or axis is None or proposition.axis_id != axis.id:
        raise StateError("Cette contribution n'est plus active.")
    expected = models.STEP_TO_PROPOSITION_TYPE.get(ConsultationStep(webinar.current_step))
    if expected is None or proposition.type != expected.value:
        raise StateError("Ce n'est pas le moment de voter sur ce type de contribution.")
    if proposition.status != PropositionStatus.APPROVED.value:
        raise StateError("Cette contribution est en attente de modération.")
    return crud.cast_proposition_vote(db, proposition=proposition, participant_id=participant_id, vote=vote)


def submit_cotation(db: Session, webinar: models.Webinar, participant_id: str, *, reponse: str) -> models.CotationResponse:
    if webinar.phase != WebinarPhase.CONSULTATION.value or webinar.current_step != ConsultationStep.VOTE.value:
        raise StateError("Le vote de cotation n'est pas ouvert actuellement.")
    if reponse not in (v.value for v in models.CotationValue):
        raise StateError("Réponse invalide.")
    axis = crud.get_current_axis(db, webinar)
    if axis is None:
        raise StateError("Aucun axe actif.")
    return crud.cast_cotation(db, axis_id=axis.id, participant_id=participant_id, reponse=reponse)


# --------------------------------------------------------------------------
# Actions ANIMATEUR
# --------------------------------------------------------------------------

@dataclass
class HostActionResult:
    message: str


def get_leading_project(db: Session, webinar: models.Webinar) -> models.Project | None:
    projects = crud.list_projects(db, webinar.id)
    if not projects:
        return None
    counts = crud.count_project_votes(db, webinar.id)
    # Tri par nb de votes décroissant ; à égalité, le projet proposé en
    # premier l'emporte (ordre de soumission).
    return max(projects, key=lambda p: (counts.get(p.id, 0), -p.created_at.timestamp()))


def apply_host_action(db: Session, webinar: models.Webinar, action: str, payload: dict) -> HostActionResult:
    handler = _HOST_ACTIONS.get(action)
    if handler is None:
        raise StateError(f"Action animateur inconnue : {action}")
    return handler(db, webinar, payload)


def _h_start_project_submission(db, webinar, payload):
    if webinar.phase != WebinarPhase.LOBBY.value:
        raise StateError("Le webinaire a déjà démarré.")
    webinar.phase = WebinarPhase.PROJECT_SUBMISSION.value
    if webinar.started_at is None:
        from app.models import utcnow
        webinar.started_at = utcnow()
    db.commit()
    return HostActionResult("Phase de proposition de projets ouverte.")


def _h_close_submission_open_vote(db, webinar, payload):
    if webinar.phase != WebinarPhase.PROJECT_SUBMISSION.value:
        raise StateError("La phase de proposition n'est pas en cours.")
    if not crud.list_projects(db, webinar.id):
        raise StateError("Aucun projet n'a été proposé.")
    webinar.phase = WebinarPhase.PROJECT_VOTE.value
    db.commit()
    return HostActionResult("Vote pour le projet ouvert.")


def _h_reopen_submission(db, webinar, payload):
    if webinar.phase != WebinarPhase.PROJECT_VOTE.value:
        raise StateError("Action impossible dans la phase actuelle.")
    webinar.phase = WebinarPhase.PROJECT_SUBMISSION.value
    db.commit()
    return HostActionResult("Retour à la phase de proposition de projets.")


def _h_select_project(db, webinar, payload):
    project_id = payload.get("project_id")
    if webinar.phase not in (WebinarPhase.PROJECT_VOTE.value, WebinarPhase.PROJECT_SUBMISSION.value):
        raise StateError("Action impossible dans la phase actuelle.")
    project = crud.get_project(db, project_id) if project_id else None
    if not project or project.webinar_id != webinar.id:
        raise StateError("Projet introuvable.")

    for p in crud.list_projects(db, webinar.id):
        p.status = models.ProjectStatus.SELECTED.value if p.id == project.id else models.ProjectStatus.ARCHIVED.value

    if not crud.list_axes(db, project.id):
        # Fidèle à l'application d'origine : les 6 dimensions officielles de
        # la Boussole de la Transition Écologique sont proposées par défaut
        # pour tout projet retenu (l'animateur peut ensuite en ajouter
        # d'autres, propres à ce projet, via "Ajouter un axe").
        for categorie, texte in utils.BTE_DEFAULT_AXES:
            crud.add_axis(db, project_id=project.id, texte=texte, categorie=categorie)

    _touch_step_timer(webinar, db)  # current_step est encore NONE ici : pas de log
    webinar.current_project_id = project.id
    webinar.current_axis_index = 0
    webinar.current_step = ConsultationStep.POSITIFS.value
    webinar.phase = WebinarPhase.CONSULTATION.value
    db.commit()
    return HostActionResult(f"Projet retenu : {project.title}. Consultation démarrée.")


def _h_set_step(db, webinar, payload):
    step = payload.get("step")
    if webinar.phase != WebinarPhase.CONSULTATION.value:
        raise StateError("La consultation n'est pas en cours.")
    if step not in (1, 2, 3, 4):
        raise StateError("Étape invalide.")
    _touch_step_timer(webinar, db)
    webinar.current_step = step
    db.commit()
    return HostActionResult(f"Étape : {utils.step_name(step)}")


def _h_change_axis(db, webinar, payload, delta: int):
    if webinar.phase != WebinarPhase.CONSULTATION.value or not webinar.current_project_id:
        raise StateError("La consultation n'est pas en cours.")
    axes = crud.list_axes(db, webinar.current_project_id)
    if not axes:
        raise StateError("Aucun axe défini pour ce projet.")
    new_index = webinar.current_axis_index + delta
    if new_index < 0 or new_index >= len(axes):
        raise StateError("Pas d'axe supplémentaire dans cette direction.")
    _touch_step_timer(webinar, db)
    webinar.current_axis_index = new_index
    webinar.current_step = ConsultationStep.POSITIFS.value
    db.commit()
    return HostActionResult(f"Axe {new_index + 1}/{len(axes)}")


def _h_add_axis(db, webinar, payload):
    if not webinar.current_project_id:
        raise StateError("Sélectionnez d'abord un projet.")
    texte = (payload.get("texte") or "").strip()
    if len(texte) < 3:
        raise StateError("Le texte de l'axe est trop court.")
    crud.add_axis(db, project_id=webinar.current_project_id, texte=texte, categorie=payload.get("categorie"))
    db.commit()
    return HostActionResult("Axe ajouté.")


def _h_end_consultation(db, webinar, payload):
    if webinar.phase != WebinarPhase.CONSULTATION.value:
        raise StateError("La consultation n'est pas en cours.")
    from app.models import utcnow
    webinar.phase = WebinarPhase.ENDED.value
    webinar.ended_at = utcnow()
    db.commit()
    return HostActionResult("Webinaire terminé. Résultats finaux disponibles.")


def _h_restart_webinar(db, webinar, payload):
    webinar.phase = WebinarPhase.LOBBY.value
    webinar.current_project_id = None
    webinar.current_axis_index = 0
    webinar.current_step = ConsultationStep.NONE.value
    webinar.started_at = None
    webinar.ended_at = None
    db.commit()
    return HostActionResult("Webinaire réinitialisé (les données collectées sont conservées).")


def _h_delete_project(db, webinar, payload):
    project = crud.get_project(db, payload.get("project_id"))
    if not project or project.webinar_id != webinar.id:
        raise StateError("Projet introuvable.")
    if webinar.current_project_id == project.id:
        raise StateError("Impossible de supprimer le projet actuellement sélectionné.")
    crud.delete_project(db, project)
    return HostActionResult("Projet supprimé.")


def _h_reset_cotation(db, webinar, payload):
    axis = crud.get_current_axis(db, webinar)
    if axis is None:
        raise StateError("Aucun axe actif.")
    crud.reset_cotation(db, axis.id)
    return HostActionResult("Votes de cotation réinitialisés.")


def _h_reset_all_propositions(db, webinar, payload):
    axis = crud.get_current_axis(db, webinar)
    if axis is None:
        raise StateError("Aucun axe actif.")
    prop_type = payload.get("prop_type")
    crud.reset_all_propositions(db, axis_id=axis.id, type_=prop_type)
    return HostActionResult("Contributions réinitialisées.")


def _h_delete_proposition(db, webinar, payload):
    proposition = crud.get_proposition(db, payload.get("proposition_id"))
    if proposition is None:
        raise StateError("Contribution introuvable.")
    crud.delete_proposition(db, proposition)
    return HostActionResult("Contribution supprimée.")


def _h_moderate_proposition_votes(db, webinar, payload):
    proposition = crud.get_proposition(db, payload.get("proposition_id"))
    if proposition is None:
        raise StateError("Contribution introuvable.")
    crud.reset_proposition_votes(db, proposition)
    return HostActionResult("Votes de la contribution réinitialisés.")


def _h_set_proposition_status(db, webinar, payload, status: str):
    proposition = crud.get_proposition(db, payload.get("proposition_id"))
    if proposition is None:
        raise StateError("Contribution introuvable.")
    crud.set_proposition_status(db, proposition, status)
    return HostActionResult("Statut de la contribution mis à jour.")


def _h_set_moderation(db, webinar, payload):
    webinar.moderation_enabled = bool(payload.get("enabled"))
    db.commit()
    state = "activée" if webinar.moderation_enabled else "désactivée"
    return HostActionResult(f"Modération {state}.")


def _h_set_allow_project_proposals(db, webinar, payload):
    webinar.allow_project_proposals = bool(payload.get("enabled"))
    db.commit()
    state = "autorisée" if webinar.allow_project_proposals else "fermée"
    return HostActionResult(f"Proposition de projets {state}.")


# Durée min/max raisonnable pour un minuteur d'étape (10s à 2h) — garde-fou
# contre une saisie erronée plutôt qu'une vraie contrainte métier.
_MIN_STEP_DURATION = 10
_MAX_STEP_DURATION = 7200


def _parse_duration(raw) -> int | None:
    """None/'' → pas de minuteur pour cette étape. Sinon, entier de
    secondes validé dans une plage raisonnable."""
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise StateError("Durée invalide : doit être un nombre de secondes.")
    if value < _MIN_STEP_DURATION or value > _MAX_STEP_DURATION:
        raise StateError(f"Durée invalide : entre {_MIN_STEP_DURATION} et {_MAX_STEP_DURATION} secondes.")
    return value


def _h_set_step_durations(db, webinar, payload):
    """Configure les 4 durées de minuteur indépendamment (§5.1) — un champ
    absent du payload laisse la valeur actuelle inchangée, une valeur
    explicitement null/vide désactive le minuteur pour cette étape."""
    fields = {
        "positifs": "step_duration_positifs",
        "negatifs": "step_duration_negatifs",
        "vote": "step_duration_vote",
        "ameliorations": "step_duration_ameliorations",
    }
    for key, attr in fields.items():
        if key in payload:
            setattr(webinar, attr, _parse_duration(payload.get(key)))
    # Le minuteur en cours (s'il y en a un) repart avec la nouvelle durée,
    # pour que le changement soit immédiatement visible plutôt que d'attendre
    # le prochain changement d'étape. On ne journalise PAS ici (log_previous=
    # False) : l'étape elle-même ne change pas, seul son décompte redémarre —
    # journaliser créerait une entrée d'historique artificielle pour une
    # étape qui n'est pas terminée.
    _touch_step_timer(webinar, db, log_previous=False)
    db.commit()
    return HostActionResult("Durées des minuteurs mises à jour.")


_HOST_ACTIONS = {
    "start_project_submission": _h_start_project_submission,
    "close_submission_open_vote": _h_close_submission_open_vote,
    "reopen_submission": _h_reopen_submission,
    "select_project": _h_select_project,
    "set_step": _h_set_step,
    "next_axis": lambda db, w, p: _h_change_axis(db, w, p, 1),
    "prev_axis": lambda db, w, p: _h_change_axis(db, w, p, -1),
    "add_axis": _h_add_axis,
    "end_consultation": _h_end_consultation,
    "restart_webinar": _h_restart_webinar,
    "delete_project": _h_delete_project,
    "reset_cotation": _h_reset_cotation,
    "reset_all_propositions": _h_reset_all_propositions,
    "delete_proposition": _h_delete_proposition,
    "moderate_proposition": _h_moderate_proposition_votes,
    "approve_proposition": lambda db, w, p: _h_set_proposition_status(db, w, p, PropositionStatus.APPROVED.value),
    "reject_proposition": lambda db, w, p: _h_set_proposition_status(db, w, p, PropositionStatus.REJECTED.value),
    "set_moderation": _h_set_moderation,
    "set_allow_project_proposals": _h_set_allow_project_proposals,
    "set_step_durations": _h_set_step_durations,
}

"""
Couche CRUD : toutes les opérations de lecture/écriture en base.

Convention : chaque fonction prend une `Session` SQLAlchemy déjà ouverte et
ne fait PAS le `commit()` elle-même sauf indication contraire — c'est
`state_machine.py` / les routers qui orchestrent et committent, pour garder
les transactions cohérentes lors d'opérations composites (ex: sélectionner
un projet = changer la phase + créer un axe par défaut en une transaction).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models, utils


# --------------------------------------------------------------------------
# Webinars
# --------------------------------------------------------------------------

def create_webinar(
    db: Session,
    *,
    title: str,
    password_hash: str,
    moderation_enabled: bool = False,
    allow_project_proposals: bool = True,
) -> models.Webinar:
    for _ in range(20):
        code = utils.generate_code()
        if not get_webinar_by_code(db, code):
            break
    else:  # pragma: no cover - extrêmement improbable
        raise RuntimeError("Impossible de générer un code de webinaire unique")

    webinar = models.Webinar(
        code=code,
        title=title.strip() or "Webinaire de consultation",
        admin_password_hash=password_hash,
        moderation_enabled=moderation_enabled,
        allow_project_proposals=allow_project_proposals,
        phase=models.WebinarPhase.LOBBY.value,
    )
    db.add(webinar)
    db.commit()
    db.refresh(webinar)
    return webinar


def get_webinar_by_code(db: Session, code: str) -> models.Webinar | None:
    code = (code or "").strip().upper()
    return db.scalar(select(models.Webinar).where(models.Webinar.code == code))


def get_webinar(db: Session, webinar_id: int) -> models.Webinar | None:
    return db.get(models.Webinar, webinar_id)


def touch_webinar(db: Session, webinar: models.Webinar) -> None:
    db.add(webinar)
    db.commit()
    db.refresh(webinar)


# --------------------------------------------------------------------------
# Participants
# --------------------------------------------------------------------------

def get_or_create_participant(
    db: Session, *, webinar_id: int, participant_id: str, display_name: str | None = None
) -> models.Participant:
    participant = db.get(models.Participant, participant_id)
    if participant is None:
        participant = models.Participant(
            id=participant_id, webinar_id=webinar_id, display_name=(display_name or None)
        )
        db.add(participant)
        db.commit()
        db.refresh(participant)
    elif display_name and display_name != participant.display_name:
        participant.display_name = display_name
        db.commit()
    return participant


def count_participants(db: Session, webinar_id: int) -> int:
    return db.scalar(
        select(func.count(models.Participant.id)).where(models.Participant.webinar_id == webinar_id)
    ) or 0


# --------------------------------------------------------------------------
# Droit à l'effacement (RGPD, §7)
# --------------------------------------------------------------------------
#
# Deux comportements distincts, proposés au participant lui-même (pas
# seulement à l'animateur) :
#
# - anonymize_participant : supprime toute donnée directement identifiante
#   (display_name) mais conserve le contenu déjà partagé avec le groupe
#   (impacts positifs/négatifs/améliorations proposés, cotations, votes),
#   en le détachant du participant. C'est le comportement recommandé par
#   défaut : le contenu collectif produit pendant l'atelier a une valeur
#   pour la restitution, alors que rien dedans n'identifie la personne
#   une fois le lien avec son participant_id supprimé.
# - erase_participant : effacement total, y compris le contenu produit
#   (cascade DB existante sur participant_id). Option plus radicale, pour
#   un participant qui souhaite qu'aucune trace de sa participation ne
#   subsiste, y compris ses contributions textuelles.
#
# Dans les deux cas, les projets proposés par le participant (Project.
# proposed_by) ne sont PAS supprimés : la colonne est en ondelete="SET
# NULL", le projet reste visible pour le groupe (il a pu être sélectionné
# et faire l'objet de toute une consultation), seul le lien vers son
# auteur disparaît.

ANONYMOUS_DISPLAY_NAME = "Participant anonymisé"


def anonymize_participant(db: Session, participant: models.Participant) -> None:
    """Retire les données directement identifiantes du participant tout en
    conservant ses contributions déjà partagées avec le groupe."""
    participant.display_name = None
    # Les projets qu'il a proposés perdent leur nom d'affichage identifiant
    # (le lien proposed_by lui-même partira tout seul via SET NULL une fois
    # le participant supprimé de la table).
    projects = db.scalars(
        select(models.Project).where(models.Project.proposed_by == participant.id)
    )
    for p in projects:
        p.proposed_by_name = ANONYMOUS_DISPLAY_NAME
    # On supprime la ligne Participant elle-même : c'est elle qui porte le
    # display_name et le lien d'identité. Les Proposition/PropositionVote/
    # CotationResponse/ProjectVote qui référencent participant_id seraient
    # en CASCADE — pour les CONSERVER malgré tout (contrairement à
    # erase_participant), on les détache d'abord vers un participant
    # "fantôme" dédié à ce webinaire, créé au besoin.
    ghost = _get_or_create_ghost_participant(db, webinar_id=participant.webinar_id)
    db.query(models.Proposition).filter(
        models.Proposition.participant_id == participant.id
    ).update({"participant_id": ghost.id})
    db.query(models.PropositionVote).filter(
        models.PropositionVote.participant_id == participant.id
    ).update({"participant_id": ghost.id})
    db.query(models.CotationResponse).filter(
        models.CotationResponse.participant_id == participant.id
    ).update({"participant_id": ghost.id})
    db.query(models.ProjectVote).filter(
        models.ProjectVote.participant_id == participant.id
    ).update({"participant_id": ghost.id})
    db.delete(participant)
    db.commit()


_GHOST_ID_SUFFIX = "-anonyme"


def _get_or_create_ghost_participant(db: Session, *, webinar_id: int) -> models.Participant:
    """Participant fantôme unique par webinaire, destinataire de toutes les
    contributions anonymisées de ce webinaire (évite de garder une ligne
    Participant par personne anonymisée, ce qui recréerait un identifiant
    traçable individuellement)."""
    ghost_id = f"webinar-{webinar_id}{_GHOST_ID_SUFFIX}"
    ghost = db.get(models.Participant, ghost_id)
    if ghost is None:
        ghost = models.Participant(
            id=ghost_id, webinar_id=webinar_id, display_name=ANONYMOUS_DISPLAY_NAME
        )
        db.add(ghost)
        db.flush()
    return ghost


def erase_participant(db: Session, participant: models.Participant) -> None:
    """Effacement total : supprime le participant et, via les contraintes
    CASCADE existantes, l'ensemble de ses contributions, votes et
    cotations. Les projets qu'il a proposés restent (SET NULL sur
    proposed_by), simplement détachés de son identité."""
    projects = db.scalars(
        select(models.Project).where(models.Project.proposed_by == participant.id)
    )
    for p in projects:
        p.proposed_by_name = ANONYMOUS_DISPLAY_NAME
    db.delete(participant)
    db.commit()


# --------------------------------------------------------------------------
# Projects
# --------------------------------------------------------------------------

def create_project(
    db: Session,
    *,
    webinar_id: int,
    title: str,
    description: str = "",
    context: str = "",
    image_url: str | None = None,
    map_url: str | None = None,
    porteur: str | None = None,
    budget: str | None = None,
    territoire: str | None = None,
    stade: str | None = None,
    type_projet: str | None = None,
    population: str | None = None,
    contrainte: str | None = None,
    enjeux: str | None = None,
    url_boussole: str | None = None,
    is_seed: bool = False,
    duplicated_from_id: int | None = None,
    proposed_by: str | None = None,
    proposed_by_name: str | None = None,
    status: str = models.ProjectStatus.PROPOSED.value,
) -> models.Project:
    project = models.Project(
        webinar_id=webinar_id,
        title=title.strip(),
        description=(description or "").strip(),
        context=(context or "").strip(),
        image_url=image_url or None,
        map_url=map_url or None,
        porteur=(porteur or "").strip() or None,
        budget=(budget or "").strip() or None,
        territoire=(territoire or "").strip() or None,
        stade=(stade or "").strip() or None,
        type_projet=(type_projet or "").strip() or None,
        population=(population or "").strip() or None,
        contrainte=(contrainte or "").strip() or None,
        enjeux=(enjeux or "").strip() or None,
        url_boussole=(url_boussole or "").strip() or None,
        is_seed=is_seed,
        duplicated_from_id=duplicated_from_id,
        proposed_by=proposed_by,
        proposed_by_name=proposed_by_name,
        status=status,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def duplicate_project(
    db: Session, *, source: models.Project, target_webinar_id: int
) -> models.Project:
    """Mode "projet type" (§7) : duplique un projet existant (typiquement un
    projet de seed déjà utilisé, ou tout projet d'un webinaire précédent)
    vers un nouveau webinaire, en réutilisant toutes ses métadonnées.

    Le projet dupliqué démarre toujours au statut PROPOSED, sans lien vers
    un auteur (`proposed_by`) : la duplication est une action animateur,
    pas une proposition d'un participant en particulier. `duplicated_from_id`
    garde la traçabilité vers le projet d'origine.
    """
    return create_project(
        db,
        webinar_id=target_webinar_id,
        title=source.title,
        description=source.description,
        context=source.context,
        image_url=source.image_url,
        map_url=source.map_url,
        porteur=source.porteur,
        budget=source.budget,
        territoire=source.territoire,
        stade=source.stade,
        type_projet=source.type_projet,
        population=source.population,
        contrainte=source.contrainte,
        enjeux=source.enjeux,
        url_boussole=source.url_boussole,
        is_seed=False,
        duplicated_from_id=source.id,
        proposed_by=None,
        proposed_by_name="Animateur (projet type)",
        status=models.ProjectStatus.PROPOSED.value,
    )


def list_projects(db: Session, webinar_id: int) -> list[models.Project]:
    return list(
        db.scalars(
            select(models.Project)
            .where(models.Project.webinar_id == webinar_id)
            .order_by(models.Project.created_at)
        )
    )


def get_project(db: Session, project_id: int) -> models.Project | None:
    return db.get(models.Project, project_id)


def delete_project(db: Session, project: models.Project) -> None:
    db.delete(project)
    db.commit()


def count_projects_by_participant(db: Session, webinar_id: int, participant_id: str) -> int:
    return db.scalar(
        select(func.count(models.Project.id)).where(
            models.Project.webinar_id == webinar_id,
            models.Project.proposed_by == participant_id,
        )
    ) or 0


# --------------------------------------------------------------------------
# Project votes (vote pour choisir LE projet à étudier en webinaire)
# --------------------------------------------------------------------------

def cast_project_vote(db: Session, *, webinar_id: int, project_id: int, participant_id: str) -> models.ProjectVote:
    existing = db.scalar(
        select(models.ProjectVote).where(
            models.ProjectVote.webinar_id == webinar_id,
            models.ProjectVote.participant_id == participant_id,
        )
    )
    if existing:
        existing.project_id = project_id
        db.commit()
        db.refresh(existing)
        return existing

    vote = models.ProjectVote(webinar_id=webinar_id, project_id=project_id, participant_id=participant_id)
    db.add(vote)
    db.commit()
    db.refresh(vote)
    return vote


def get_participant_project_vote(db: Session, *, webinar_id: int, participant_id: str) -> int | None:
    vote = db.scalar(
        select(models.ProjectVote).where(
            models.ProjectVote.webinar_id == webinar_id,
            models.ProjectVote.participant_id == participant_id,
        )
    )
    return vote.project_id if vote else None


def count_project_votes(db: Session, webinar_id: int) -> dict[int, int]:
    rows = db.execute(
        select(models.ProjectVote.project_id, func.count(models.ProjectVote.id))
        .where(models.ProjectVote.webinar_id == webinar_id)
        .group_by(models.ProjectVote.project_id)
    ).all()
    return dict(rows)


# --------------------------------------------------------------------------
# Axes
# --------------------------------------------------------------------------

def add_axis(db: Session, *, project_id: int, texte: str, categorie: str | None = None, ordre: int | None = None) -> models.Axis:
    if ordre is None:
        max_ordre = db.scalar(
            select(func.max(models.Axis.ordre)).where(models.Axis.project_id == project_id)
        )
        ordre = (max_ordre or 0) + 1
    axis = models.Axis(project_id=project_id, texte=texte.strip(), categorie=(categorie or None), ordre=ordre)
    db.add(axis)
    db.commit()
    db.refresh(axis)
    return axis


def list_axes(db: Session, project_id: int) -> list[models.Axis]:
    return list(
        db.scalars(select(models.Axis).where(models.Axis.project_id == project_id).order_by(models.Axis.ordre))
    )


def get_axis(db: Session, axis_id: int) -> models.Axis | None:
    return db.get(models.Axis, axis_id)


def get_current_axis(db: Session, webinar: models.Webinar) -> models.Axis | None:
    if not webinar.current_project_id:
        return None
    axes = list_axes(db, webinar.current_project_id)
    if not axes:
        return None
    idx = max(0, min(webinar.current_axis_index, len(axes) - 1))
    return axes[idx]


# --------------------------------------------------------------------------
# Propositions
# --------------------------------------------------------------------------

def create_proposition(
    db: Session,
    *,
    axis_id: int,
    participant_id: str,
    type_: str,
    texte: str,
    status: str = models.PropositionStatus.APPROVED.value,
) -> models.Proposition:
    prop = models.Proposition(
        axis_id=axis_id, participant_id=participant_id, type=type_, texte=texte.strip(), status=status
    )
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop


def list_propositions(
    db: Session, *, axis_id: int, type_: str, statuses: tuple[str, ...] | None = None
) -> list[models.Proposition]:
    stmt = select(models.Proposition).where(
        models.Proposition.axis_id == axis_id, models.Proposition.type == type_
    )
    if statuses:
        stmt = stmt.where(models.Proposition.status.in_(statuses))
    # Fidèle à l'application d'origine : les propositions les plus
    # plébiscitées remontent en tête (arrange(desc(accord)) dans
    # consultation_utils.R). Tie-break secondaire par created_at pour un
    # ordre stable et déterministe entre propositions à égalité de votes.
    stmt = stmt.order_by(models.Proposition.nb_accord.desc(), models.Proposition.created_at.asc())
    return list(db.scalars(stmt))


def get_proposition(db: Session, proposition_id: int) -> models.Proposition | None:
    return db.get(models.Proposition, proposition_id)


def count_propositions_by_participant(db: Session, *, axis_id: int, participant_id: str, type_: str) -> int:
    return db.scalar(
        select(func.count(models.Proposition.id)).where(
            models.Proposition.axis_id == axis_id,
            models.Proposition.participant_id == participant_id,
            models.Proposition.type == type_,
        )
    ) or 0


def delete_proposition(db: Session, proposition: models.Proposition) -> None:
    db.delete(proposition)
    db.commit()


def set_proposition_status(db: Session, proposition: models.Proposition, status: str) -> None:
    proposition.status = status
    db.commit()


def reset_proposition_votes(db: Session, proposition: models.Proposition) -> None:
    db.query(models.PropositionVote).filter(
        models.PropositionVote.proposition_id == proposition.id
    ).delete()
    proposition.nb_accord = 0
    proposition.nb_desaccord = 0
    proposition.nb_passer = 0
    db.commit()


def reset_all_propositions(db: Session, *, axis_id: int, type_: str | None = None) -> None:
    stmt = db.query(models.Proposition).filter(models.Proposition.axis_id == axis_id)
    if type_:
        stmt = stmt.filter(models.Proposition.type == type_)
    for prop in stmt.all():
        db.delete(prop)
    db.commit()


# --------------------------------------------------------------------------
# Proposition votes (accord / désaccord / passer)
# --------------------------------------------------------------------------

_COUNTER_FIELD = {
    models.VoteValue.ACCORD.value: "nb_accord",
    models.VoteValue.DESACCORD.value: "nb_desaccord",
    models.VoteValue.PASSER.value: "nb_passer",
}


def cast_proposition_vote(
    db: Session, *, proposition: models.Proposition, participant_id: str, vote: str
) -> models.Proposition:
    existing = db.scalar(
        select(models.PropositionVote).where(
            models.PropositionVote.proposition_id == proposition.id,
            models.PropositionVote.participant_id == participant_id,
        )
    )
    if existing and existing.vote == vote:
        return proposition  # déjà voté pareil, rien à faire

    if existing:
        old_field = _COUNTER_FIELD[existing.vote]
        setattr(proposition, old_field, max(0, getattr(proposition, old_field) - 1))
        existing.vote = vote
    else:
        existing = models.PropositionVote(
            proposition_id=proposition.id, participant_id=participant_id, vote=vote
        )
        db.add(existing)

    new_field = _COUNTER_FIELD[vote]
    setattr(proposition, new_field, getattr(proposition, new_field) + 1)

    db.commit()
    db.refresh(proposition)
    return proposition


def get_participant_proposition_votes(db: Session, *, axis_id: int, participant_id: str) -> dict[int, str]:
    rows = db.execute(
        select(models.PropositionVote.proposition_id, models.PropositionVote.vote)
        .join(models.Proposition, models.Proposition.id == models.PropositionVote.proposition_id)
        .where(models.Proposition.axis_id == axis_id, models.PropositionVote.participant_id == participant_id)
    ).all()
    return dict(rows)


# --------------------------------------------------------------------------
# Cotation (FAVORABLE / NEUTRE / DEFAVORABLE)
# --------------------------------------------------------------------------

def cast_cotation(db: Session, *, axis_id: int, participant_id: str, reponse: str) -> models.CotationResponse:
    existing = db.scalar(
        select(models.CotationResponse).where(
            models.CotationResponse.axis_id == axis_id,
            models.CotationResponse.participant_id == participant_id,
        )
    )
    if existing:
        existing.reponse = reponse
        db.commit()
        db.refresh(existing)
        return existing

    cotation = models.CotationResponse(axis_id=axis_id, participant_id=participant_id, reponse=reponse)
    db.add(cotation)
    db.commit()
    db.refresh(cotation)
    return cotation


def get_participant_cotation(db: Session, *, axis_id: int, participant_id: str) -> str | None:
    cotation = db.scalar(
        select(models.CotationResponse).where(
            models.CotationResponse.axis_id == axis_id,
            models.CotationResponse.participant_id == participant_id,
        )
    )
    return cotation.reponse if cotation else None


def get_cotation_counts(db: Session, axis_id: int) -> dict[str, int]:
    rows = db.execute(
        select(models.CotationResponse.reponse, func.count(models.CotationResponse.id))
        .where(models.CotationResponse.axis_id == axis_id)
        .group_by(models.CotationResponse.reponse)
    ).all()
    counts = {v.value: 0 for v in models.CotationValue}
    for reponse, count in rows:
        counts[reponse] = count
    return counts


def reset_cotation(db: Session, axis_id: int) -> None:
    db.query(models.CotationResponse).filter(models.CotationResponse.axis_id == axis_id).delete()
    db.commit()


def list_cotations(db: Session, axis_id: int) -> list[models.CotationResponse]:
    return list(db.scalars(select(models.CotationResponse).where(models.CotationResponse.axis_id == axis_id)))


def list_proposition_votes_for_axis(db: Session, axis_id: int) -> list[models.PropositionVote]:
    """Votes individuels (accord/désaccord/passer) de toutes les
    propositions d'un axe — niveau de détail équivalent au votes.csv de
    l'application d'origine (une ligne par vote, pas seulement les
    compteurs agrégés), utile pour l'export et une analyse ultérieure."""
    return list(
        db.scalars(
            select(models.PropositionVote)
            .join(models.Proposition, models.Proposition.id == models.PropositionVote.proposition_id)
            .where(models.Proposition.axis_id == axis_id)
        )
    )


def list_step_timing_logs(db: Session, webinar_id: int) -> list[models.StepTimingLog]:
    """Historique des temps réels passés par étape de consultation (§7.5),
    triés chronologiquement — utile pour calibrer les prochains ateliers
    (ex: telle étape prend systématiquement plus de temps que prévu)."""
    return list(
        db.scalars(
            select(models.StepTimingLog)
            .where(models.StepTimingLog.webinar_id == webinar_id)
            .order_by(models.StepTimingLog.started_at.asc())
        )
    )


# --------------------------------------------------------------------------
# Vue super-admin (protégée par ADMIN_SECRET, cf. routers/api.py)
# --------------------------------------------------------------------------

def list_all_webinars(db: Session) -> list[models.Webinar]:
    """Tous les webinaires, triés du plus récent au plus ancien — utilisé
    uniquement par la vue super-admin pour nettoyer la base en
    développement/démo, jamais exposé aux animateurs/participants."""
    return list(db.scalars(select(models.Webinar).order_by(models.Webinar.created_at.desc())))


def delete_webinar(db: Session, webinar: models.Webinar) -> None:
    """Supprime un webinaire et tout ce qui en dépend, via les contraintes
    CASCADE déjà en place sur webinar_id (projects, participants, et
    transitivement axes/propositions/votes/cotations qui dépendent de
    projects/participants)."""
    db.delete(webinar)
    db.commit()

"""
Modèles de données.

Vocabulaire (et correspondance avec l'application Shiny d'origine) :
- Webinar      : une session de consultation (n'existait pas avant : l'app
                 d'origine gérait un seul état global pour un seul événement).
- Project      : un "projet" soumis à consultation. Nouveau concept central
                 demandé : plusieurs projets possibles, proposés par les
                 participants puis départagés par un vote.
- Axis         : un axe/thème de discussion au sein d'un projet. C'est
                 l'équivalent direct des "questions" de l'app d'origine
                 (texte + catégorie), simplement rattaché à un projet plutôt
                 qu'au webinaire entier. Un projet a au moins un axe.
- Proposition  : un impact positif/négatif ou une amélioration proposée par
                 un participant sur un axe (= les "propositions" d'origine).
- PropositionVote : un vote accord/désaccord/passer sur une proposition.
- CotationResponse : une réponse FAVORABLE/NEUTRE/DEFAVORABLE à la cotation
                 d'un axe (= les "responses" d'origine).
- Participant  : un participant identifié par un UUID anonyme côté navigateur.
"""
from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class WebinarPhase(str, enum.Enum):
    LOBBY = "lobby"
    PROJECT_SUBMISSION = "project_submission"
    PROJECT_VOTE = "project_vote"
    CONSULTATION = "consultation"
    ENDED = "ended"


class ConsultationStep(int, enum.Enum):
    NONE = 0
    POSITIFS = 1
    NEGATIFS = 2
    VOTE = 3
    AMELIORATIONS = 4


class PropositionType(str, enum.Enum):
    POSITIFS = "positifs"
    NEGATIFS = "negatifs"
    AMELIORATIONS = "ameliorations"


STEP_TO_PROPOSITION_TYPE = {
    ConsultationStep.POSITIFS: PropositionType.POSITIFS,
    ConsultationStep.NEGATIFS: PropositionType.NEGATIFS,
    ConsultationStep.AMELIORATIONS: PropositionType.AMELIORATIONS,
}


class PropositionStatus(str, enum.Enum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class VoteValue(str, enum.Enum):
    ACCORD = "accord"
    DESACCORD = "desaccord"
    PASSER = "passer"


class CotationValue(str, enum.Enum):
    FAVORABLE = "FAVORABLE"
    NEUTRE = "NEUTRE"
    DEFAVORABLE = "DEFAVORABLE"


class ProjectStatus(str, enum.Enum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    ARCHIVED = "archived"


class Webinar(Base):
    __tablename__ = "webinars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    admin_password_hash: Mapped[str] = mapped_column(String(255))

    phase: Mapped[str] = mapped_column(String(32), default=WebinarPhase.LOBBY.value)
    current_project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", use_alter=True, name="fk_webinar_current_project"),
        nullable=True,
    )
    current_axis_index: Mapped[int] = mapped_column(Integer, default=0)
    current_step: Mapped[int] = mapped_column(Integer, default=ConsultationStep.NONE.value)

    # Minuteur par étape (§5.1 du cahier des charges) — horodatage du
    # dernier changement d'étape/axe/projet, utilisé par les clients (host,
    # participant, projecteur) pour calculer un compte à rebours cohérent
    # entre tous (step_started_at + step_duration_seconds(step)). Recalculé
    # côté client plutôt que diffusé "en direct" seconde par seconde : pas
    # de charge WebSocket supplémentaire, juste un point de départ fiable.
    step_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Durée configurable indépendamment pour chaque étape de consultation
    # (NULL = pas de minuteur pour cette étape, décision validée : pas de
    # durée unique commune à toutes les étapes). Exprimée en secondes.
    step_duration_positifs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_duration_negatifs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_duration_vote: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_duration_ameliorations: Mapped[int | None] = mapped_column(Integer, nullable=True)

    moderation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_project_proposals: Mapped[bool] = mapped_column(Boolean, default=True)
    max_propositions_per_participant: Mapped[int] = mapped_column(Integer, default=5)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="webinar", foreign_keys="Project.webinar_id",
        cascade="all, delete-orphan", order_by="Project.created_at",
    )
    participants: Mapped[list["Participant"]] = relationship(
        "Participant", back_populates="webinar", cascade="all, delete-orphan"
    )
    project_votes: Mapped[list["ProjectVote"]] = relationship(
        "ProjectVote", back_populates="webinar", cascade="all, delete-orphan"
    )

    current_project: Mapped["Project | None"] = relationship(
        "Project", foreign_keys=[current_project_id], post_update=True,
    )


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("webinar_id", "id", name="uq_project_webinar"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webinar_id: Mapped[int] = mapped_column(ForeignKey("webinars.id", ondelete="CASCADE"))

    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[str] = mapped_column(Text, default="")  # "état des lieux" (texte long, optionnel)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Lien cartes.gouv.fr (centré par le participant/animateur sur le
    # projet), affiché en encart intégré (<iframe>) dans les vues projet
    # (§2.2c du cahier des charges).
    map_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Métadonnées clés du projet (§2.2a / §3 du cahier des charges) —
    # optionnelles : remplies soit par le script de seed CSV (non traité
    # dans ce chantier), soit manuellement par le participant/animateur au
    # moment de la soumission. Affichées dans le volet projet permanent
    # (§3) pendant la consultation.
    porteur: Mapped[str | None] = mapped_column(String(255), nullable=True)
    budget: Mapped[str | None] = mapped_column(String(120), nullable=True)
    territoire: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stade: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Champs additionnels du CSV de seed (§2.2a / §7.3 du cahier des charges).
    type_projet: Mapped[str | None] = mapped_column(String(120), nullable=True)
    population: Mapped[str | None] = mapped_column(String(120), nullable=True)
    contrainte: Mapped[str | None] = mapped_column(Text, nullable=True)
    enjeux: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url_boussole: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Marque un projet issu du fond de projets de secours importé par CSV
    # (§8) : permet de le distinguer des projets proposés par les
    # participants, sans introduire de notion de "bibliothèque globale"
    # hors webinaire (chantier de modélisation plus lourd, non traité ici).
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Mode "projet type" (§7) : permet à l'animateur de dupliquer un projet
    # existant (typiquement un projet de seed déjà utilisé dans un webinaire
    # précédent, ou tout autre projet dont il a gardé le code) vers le
    # webinaire courant, sans ressaisir toutes les métadonnées. Référence
    # auto-jointe sur la même table ; ondelete="SET NULL" pour ne jamais
    # bloquer la suppression du projet d'origine.
    duplicated_from_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )

    proposed_by: Mapped[str | None] = mapped_column(
        ForeignKey("participants.id", ondelete="SET NULL"), nullable=True
    )
    proposed_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.PROPOSED.value)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    webinar: Mapped["Webinar"] = relationship(
        "Webinar", back_populates="projects", foreign_keys=[webinar_id]
    )
    axes: Mapped[list["Axis"]] = relationship(
        "Axis", back_populates="project", cascade="all, delete-orphan", order_by="Axis.ordre"
    )
    votes: Mapped[list["ProjectVote"]] = relationship(
        "ProjectVote", back_populates="project", cascade="all, delete-orphan"
    )


class ProjectVote(Base):
    __tablename__ = "project_votes"
    __table_args__ = (UniqueConstraint("webinar_id", "participant_id", name="uq_one_vote_per_participant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webinar_id: Mapped[int] = mapped_column(ForeignKey("webinars.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    webinar: Mapped["Webinar"] = relationship("Webinar", back_populates="project_votes")
    project: Mapped["Project"] = relationship("Project", back_populates="votes")


class Axis(Base):
    """Axe de discussion au sein d'un projet (équivalent des "questions" d'origine)."""

    __tablename__ = "axes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    texte: Mapped[str] = mapped_column(Text)
    categorie: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ordre: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped["Project"] = relationship("Project", back_populates="axes")
    propositions: Mapped[list["Proposition"]] = relationship(
        "Proposition", back_populates="axis", cascade="all, delete-orphan"
    )
    cotations: Mapped[list["CotationResponse"]] = relationship(
        "CotationResponse", back_populates="axis", cascade="all, delete-orphan"
    )


class Proposition(Base):
    __tablename__ = "propositions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    axis_id: Mapped[int] = mapped_column(ForeignKey("axes.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))  # PropositionType
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    texte: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=PropositionStatus.APPROVED.value)

    nb_accord: Mapped[int] = mapped_column(Integer, default=0)
    nb_desaccord: Mapped[int] = mapped_column(Integer, default=0)
    nb_passer: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    axis: Mapped["Axis"] = relationship("Axis", back_populates="propositions")
    prop_votes: Mapped[list["PropositionVote"]] = relationship(
        "PropositionVote", back_populates="proposition", cascade="all, delete-orphan"
    )

    @property
    def total_votes(self) -> int:
        return self.nb_accord + self.nb_desaccord + self.nb_passer

    @property
    def consensus_pct(self) -> float:
        """% d'accord parmi TOUS les votes exprimés, "passer" inclus.

        Reprend exactement `calculate_consensus_score()` de l'application
        d'origine (accord / (accord+desaccord+passer) * 100) : un "passer"
        dilue donc le score au lieu d'être neutre. On reprend fidèlement ce
        calcul historique plutôt que d'en choisir un autre en apparence
        plus "logique", pour ne pas changer silencieusement les chiffres
        affichés aux utilisateurs par rapport à l'application d'origine.
        """
        total = self.total_votes
        if total == 0:
            return 0.0
        return round(100 * self.nb_accord / total, 1)


class PropositionVote(Base):
    __tablename__ = "proposition_votes"
    __table_args__ = (UniqueConstraint("proposition_id", "participant_id", name="uq_vote_per_participant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    proposition_id: Mapped[int] = mapped_column(ForeignKey("propositions.id", ondelete="CASCADE"))
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    vote: Mapped[str] = mapped_column(String(16))  # VoteValue
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    proposition: Mapped["Proposition"] = relationship("Proposition", back_populates="prop_votes")


class CotationResponse(Base):
    __tablename__ = "cotation_responses"
    __table_args__ = (UniqueConstraint("axis_id", "participant_id", name="uq_cotation_per_participant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    axis_id: Mapped[int] = mapped_column(ForeignKey("axes.id", ondelete="CASCADE"))
    participant_id: Mapped[str] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    reponse: Mapped[str] = mapped_column(String(16))  # CotationValue
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    axis: Mapped["Axis"] = relationship("Axis", back_populates="cotations")


class StepTimingLog(Base):
    """Historique du temps réel passé sur chaque étape de consultation
    (§7.5 du cahier des charges) — utile a posteriori pour calibrer les
    prochains ateliers. Une ligne par étape effectivement quittée (pas de
    ligne pour l'étape en cours, qui n'a pas encore de durée connue)."""

    __tablename__ = "step_timing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    webinar_id: Mapped[int] = mapped_column(ForeignKey("webinars.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    axis_index: Mapped[int] = mapped_column(Integer)
    step: Mapped[int] = mapped_column(Integer)  # ConsultationStep
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    planned_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    webinar: Mapped["Webinar"] = relationship("Webinar")


class Participant(Base):
    __tablename__ = "participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 généré côté client
    webinar_id: Mapped[int] = mapped_column(ForeignKey("webinars.id", ondelete="CASCADE"))
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    joined_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    webinar: Mapped["Webinar"] = relationship("Webinar", back_populates="participants")

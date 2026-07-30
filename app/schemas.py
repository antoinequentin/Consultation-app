"""Schémas Pydantic (validation des entrées API REST + messages WebSocket)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# REST : création / authentification d'un webinaire
# --------------------------------------------------------------------------

class WebinarCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    # L'application d'origine recommandait (en documentation) un mot de
    # passe d'au moins 12 caractères, sans le vérifier dans le code. Chaque
    # webinaire ayant désormais son propre mot de passe, on impose ici un
    # plancher raisonnable ; la page de création recommande 12+ caractères.
    password: str = Field(min_length=8, max_length=128)
    moderation_enabled: bool = False
    allow_project_proposals: bool = True
    # Permet de créer directement un premier projet "graine" (optionnel),
    # pour démarrer rapidement sans passer par la phase de proposition si
    # l'animateur a déjà un projet prêt.
    seed_project_title: str | None = Field(default=None, max_length=255)
    seed_project_description: str | None = None
    seed_project_context: str | None = None


class WebinarCreateResponse(BaseModel):
    code: str
    title: str
    host_token: str
    participant_url: str
    host_url: str
    projector_url: str


class HostLogin(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class HostLoginResponse(BaseModel):
    token: str


# --------------------------------------------------------------------------
# WebSocket : enveloppe générique
# --------------------------------------------------------------------------

class WSEnvelope(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Payloads validés pour les actions les plus sensibles (texte libre)
# --------------------------------------------------------------------------

class ProjectSubmitPayload(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=4000)
    image_url: str | None = Field(default=None, max_length=1024)
    map_url: str | None = Field(default=None, max_length=1024)
    porteur: str | None = Field(default=None, max_length=255)
    budget: str | None = Field(default=None, max_length=120)
    territoire: str | None = Field(default=None, max_length=255)
    stade: str | None = Field(default=None, max_length=120)

    @field_validator("title", "description", "context")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("porteur", "budget", "territoire", "stade")
    @classmethod
    def _strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("image_url", "map_url")
    @classmethod
    def _strip_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Le lien doit commencer par http:// ou https://")
        return v


class PropositionSubmitPayload(BaseModel):
    prop_type: str = Field(pattern="^(positifs|negatifs|ameliorations)$")
    # L'application d'origine exige nchar(trimws(texte)) >= 10 : on reprend
    # exactement ce seuil (une contribution à 2 caractères n'apporte rien
    # au débat et n'était pas permise par le système d'origine).
    texte: str = Field(min_length=10, max_length=500)

    @field_validator("texte")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Votre proposition doit contenir au moins 10 caractères.")
        return v


class JoinPayload(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator("display_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class ParticipantErasurePayload(BaseModel):
    """Droit à l'effacement RGPD (§7) : le participant fournit son propre
    participant_id (uuid généré côté client, jamais transmis par un tiers)
    et choisit le mode d'effacement souhaité."""

    participant_id: str = Field(min_length=1, max_length=36)
    mode: str = Field(default="anonymize")  # "anonymize" | "erase"

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("anonymize", "erase"):
            raise ValueError("mode doit être 'anonymize' ou 'erase'.")
        return v


class ProjectDuplicatePayload(BaseModel):
    """Mode "projet type" (§7) : identifie le projet à dupliquer depuis un
    webinaire source vers le webinaire cible (celui de l'URL)."""

    source_project_id: int

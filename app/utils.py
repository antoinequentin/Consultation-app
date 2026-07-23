"""Fonctions utilitaires partagées."""
from __future__ import annotations

import csv
import io
import secrets
import zipfile

from app.models import ConsultationStep, WebinarPhase

# On exclut les caractères ambigus (0/O, 1/I/L) pour que le code reste
# facile à lire/dicter/recopier à l'oral pendant un webinaire.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_code(length: int = 5) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


# ----------------------------------------------------------------------------
# Référentiel "Boussole de la Transition Écologique" (BTE)
# ----------------------------------------------------------------------------
# Ces 6 catégories et leurs couleurs proviennent telles quelles de
# `questions_list` et `get_category_color()` dans le fichier R d'origine
# (myshinyapp/R/data.R). Elles servent de jeu d'axes par défaut pour tout
# nouveau projet retenu (l'animateur peut toujours en ajouter, en retirer,
# ou en reformuler le texte) plutôt que d'être codées en dur pour un
# unique projet fixe comme dans l'application d'origine.
BTE_DEFAULT_AXES: list[tuple[str, str]] = [
    ("ADAPTATION", "Comment le projet contribue-t-il à s'adapter au climat actuel et futur ?"),
    ("ATTÉNUATION", "Comment le projet contribue-t-il à réduire les émissions de gaz à effet de serre ?"),
    ("RESSOURCE EN EAU", "Comment le projet contribue-t-il à la gestion durable des ressources en eau ?"),
    ("BIODIVERSITÉ", "Comment le projet contribue-t-il à la protection et à la restauration de la biodiversité et des écosystèmes ?"),
    ("POLLUTION", "Comment le projet contribue-t-il à la prévention et à la réduction des pollutions ?"),
    ("ÉCONOMIE CIRCULAIRE", "Comment le projet contribue-t-il à la transition vers une économie circulaire, à la prévention des déchets ou au recyclage ?"),
]

BTE_CATEGORY_COLORS: dict[str, str] = {
    "ADAPTATION": "#ff9a00",
    "ATTÉNUATION": "#669a9a",
    "RESSOURCE EN EAU": "#0066cd",
    "BIODIVERSITÉ": "#009a00",
    "POLLUTION": "#9a6600",
    "ÉCONOMIE CIRCULAIRE": "#009a66",
}
_DEFAULT_CATEGORY_COLOR = "#000091"  # Bleu France, fallback (identique à l'original)


def category_color(categorie: str | None) -> str:
    if not categorie:
        return _DEFAULT_CATEGORY_COLOR
    return BTE_CATEGORY_COLORS.get(categorie.strip().upper(), _DEFAULT_CATEGORY_COLOR)


STEP_NAMES = {
    ConsultationStep.NONE.value: "—",
    ConsultationStep.POSITIFS.value: "Impacts positifs",
    ConsultationStep.NEGATIFS.value: "Impacts négatifs",
    ConsultationStep.VOTE.value: "Vote (cotation)",
    ConsultationStep.AMELIORATIONS.value: "Pistes d'amélioration",
}

PHASE_NAMES = {
    WebinarPhase.LOBBY.value: "Accueil",
    WebinarPhase.PROJECT_SUBMISSION.value: "Proposition de projets",
    WebinarPhase.PROJECT_VOTE.value: "Vote du projet",
    WebinarPhase.CONSULTATION.value: "Consultation",
    WebinarPhase.ENDED.value: "Terminé",
}


def step_name(step: int) -> str:
    return STEP_NAMES.get(step, "—")


def phase_name(phase: str) -> str:
    return PHASE_NAMES.get(phase, phase)


def build_export_zip(
    webinar, projects, axes_by_project, propositions_by_axis, cotations_by_axis, project_votes,
    proposition_votes_by_axis: dict | None = None,
) -> bytes:
    """Construit une archive ZIP contenant un export CSV par table, pour le
    bouton 'Exporter les données' de la console animateur (équivalent de
    l'export CSV/zip de l'application Shiny d'origine, étendu aux projets).

    `proposition_votes_by_axis` (optionnel) permet d'inclure un
    votes_propositions.csv détaillé (un vote individuel par ligne), au même
    niveau de granularité que le votes.csv de l'application d'origine —
    par opposition à propositions.csv qui ne donne que les compteurs
    agrégés par proposition.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # projects.csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "titre", "description", "statut", "propose_par", "nb_votes", "cree_le"])
        for p in projects:
            nb_votes = sum(1 for v in project_votes if v.project_id == p.id)
            writer.writerow([p.id, p.title, p.description, p.status, p.proposed_by_name or "", nb_votes, p.created_at])
        zf.writestr("projets.csv", out.getvalue())

        # axes.csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "projet_id", "texte", "categorie"])
        for axes in axes_by_project.values():
            for a in axes:
                writer.writerow([a.id, a.project_id, a.texte, a.categorie or ""])
        zf.writestr("axes.csv", out.getvalue())

        # propositions.csv (vue agrégée : compteurs par proposition)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "axe_id", "type", "texte", "statut", "accord", "desaccord", "passer", "consensus_pct", "cree_le"])
        for props in propositions_by_axis.values():
            for pr in props:
                writer.writerow([pr.id, pr.axis_id, pr.type, pr.texte, pr.status, pr.nb_accord, pr.nb_desaccord, pr.nb_passer, pr.consensus_pct, pr.created_at])
        zf.writestr("propositions.csv", out.getvalue())

        # votes_propositions.csv (vue détaillée : un vote individuel par
        # ligne — équivalent du votes.csv de l'application d'origine).
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "proposition_id", "participant_id", "vote", "cree_le"])
        if proposition_votes_by_axis:
            for votes in proposition_votes_by_axis.values():
                for v in votes:
                    writer.writerow([v.id, v.proposition_id, v.participant_id, v.vote, v.created_at])
        zf.writestr("votes_propositions.csv", out.getvalue())

        # cotations.csv (une ligne par réponse, avec participant_id —
        # équivalent du responses.csv de l'application d'origine)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["id", "axe_id", "participant_id", "reponse", "cree_le"])
        for cotations in cotations_by_axis.values():
            for c in cotations:
                writer.writerow([c.id, c.axis_id, c.participant_id, c.reponse, c.created_at])
        zf.writestr("cotations.csv", out.getvalue())

        # resume.csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["webinaire", webinar.title])
        writer.writerow(["code", webinar.code])
        writer.writerow(["phase", webinar.phase])
        writer.writerow(["cree_le", webinar.created_at])
        zf.writestr("resume.csv", out.getvalue())

    return buffer.getvalue()

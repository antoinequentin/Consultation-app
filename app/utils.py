"""Fonctions utilitaires partagées."""
from __future__ import annotations

import csv
import io
import secrets
import zipfile

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models import ConsultationStep, PropositionStatus, PropositionType, WebinarPhase

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
    "ADAPTATION": "#FFCA00",
    "ATTÉNUATION": "#6E445A",
    "RESSOURCE EN EAU": "#465F9D",
    "BIODIVERSITÉ": "#68A532",
    "POLLUTION": "#C08C65",
    "ÉCONOMIE CIRCULAIRE": "#d5706f",
    # Non fourni par défaut sur un projet (les 6 dimensions officielles de la
    # Boussole de la Transition Écologique ci-dessus restent les seules
    # ajoutées automatiquement), mais disponible comme catégorie d'axe si
    # l'animateur en ajoute un via "Ajouter un axe" (§1.3 du cahier des
    # charges — 7e couleur de la palette DSFR).
    "GUIDE TRANSVERSE": "#8585f6",
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
    step_timing_logs: list | None = None,
) -> bytes:
    """Construit une archive ZIP contenant un export CSV par table, pour le
    bouton 'Exporter les données' de la console animateur (équivalent de
    l'export CSV/zip de l'application Shiny d'origine, étendu aux projets).

    `proposition_votes_by_axis` (optionnel) permet d'inclure un
    votes_propositions.csv détaillé (un vote individuel par ligne), au même
    niveau de granularité que le votes.csv de l'application d'origine —
    par opposition à propositions.csv qui ne donne que les compteurs
    agrégés par proposition.

    `step_timing_logs` (optionnel, §7.5) permet d'inclure un
    minuteurs.csv listant le temps réel passé sur chaque étape de
    consultation quittée, utile pour calibrer les prochains ateliers.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # projects.csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "id", "titre", "description", "statut", "propose_par", "nb_votes", "image_url", "map_url",
            "porteur", "budget", "territoire", "stade", "cree_le",
        ])
        for p in projects:
            nb_votes = sum(1 for v in project_votes if v.project_id == p.id)
            writer.writerow([
                p.id, p.title, p.description, p.status, p.proposed_by_name or "", nb_votes,
                p.image_url or "", p.map_url or "",
                p.porteur or "", p.budget or "", p.territoire or "", p.stade or "", p.created_at,
            ])
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

        # minuteurs.csv (§7.5 : historique du temps réel passé par étape)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "id", "projet_id", "axe_index", "etape", "debut", "fin",
            "duree_secondes", "duree_prevue_secondes",
        ])
        for log in (step_timing_logs or []):
            writer.writerow([
                log.id, log.project_id or "", log.axis_index, step_name(log.step),
                log.started_at, log.ended_at, log.duration_seconds,
                log.planned_duration_seconds if log.planned_duration_seconds is not None else "",
            ])
        zf.writestr("minuteurs.csv", out.getvalue())

        # resume.csv
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["webinaire", webinar.title])
        writer.writerow(["code", webinar.code])
        writer.writerow(["phase", webinar.phase])
        writer.writerow(["cree_le", webinar.created_at])
        zf.writestr("resume.csv", out.getvalue())

    return buffer.getvalue()


# ----------------------------------------------------------------------------
# Export PDF (§7)
# ----------------------------------------------------------------------------
# Rapport de restitution du projet consulté : une synthèse lisible et
# imprimable en complément de l'export CSV/ZIP déjà existant (destiné, lui,
# à l'analyse des données brutes). Construit avec reportlab/Platypus, sans
# dépendance externe autre que celle déjà utilisée pour le reste de l'app.

_PDF_BRASS = colors.HexColor("#9C6B30")
_PDF_INK = colors.HexColor("#1B1B1B")
_PDF_SOFT = colors.HexColor("#5B5B5B")
_PDF_GREEN = colors.HexColor("#18753C")
_PDF_RED = colors.HexColor("#CE0033")


def _pdf_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="BoussoleTitle", fontSize=22, leading=26, textColor=_PDF_INK,
        spaceAfter=4, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="BoussoleSubtitle", fontSize=12, leading=16, textColor=_PDF_SOFT,
        spaceAfter=16,
    ))
    styles.add(ParagraphStyle(
        name="BoussoleH2", fontSize=15, leading=19, textColor=_PDF_BRASS,
        spaceBefore=18, spaceAfter=8, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="BoussoleH3", fontSize=12, leading=15, textColor=_PDF_INK,
        spaceBefore=10, spaceAfter=6, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="BoussoleBody", fontSize=10, leading=14, textColor=_PDF_INK,
    ))
    styles.add(ParagraphStyle(
        name="BoussoleMeta", fontSize=9, leading=13, textColor=_PDF_SOFT,
    ))
    styles.add(ParagraphStyle(
        name="BoussoleItem", fontSize=9.5, leading=13, textColor=_PDF_INK,
        leftIndent=10, spaceAfter=4,
    ))
    return styles


def _pdf_proposition_flowables(styles, propositions, *, empty_label: str):
    """Liste à puces des propositions APPROUVÉES uniquement (cohérent avec
    ce qu'un participant voit réellement à l'écran) — avec leur score de
    consensus, pour donner un aperçu du niveau d'adhésion sans avoir à
    ouvrir l'export CSV détaillé."""
    approved = [p for p in propositions if p.status == PropositionStatus.APPROVED.value]
    if not approved:
        return [Paragraph(f"<i>{empty_label}</i>", styles["BoussoleMeta"])]
    flowables = []
    for p in approved:
        consensus = f"{round(p.consensus_pct)}% d'accord ({p.total_votes} vote(s))" if p.total_votes else "pas encore de vote"
        flowables.append(Paragraph(f"• {p.texte} <font color='#5B5B5B' size=8>— {consensus}</font>", styles["BoussoleItem"]))
    return flowables


def build_pdf_report(
    webinar, project, axes_data: list[dict],
) -> bytes:
    """Construit le rapport PDF de restitution (§7).

    `axes_data` : liste ordonnée de dicts, un par axe du projet :
        {
            "axis": models.Axis,
            "positifs": list[models.Proposition],
            "negatifs": list[models.Proposition],
            "ameliorations": list[models.Proposition],
            "cotation_counts": dict[str, int],   # ex. {"FAVORABLE": 5, ...}
            "cotation_total": int,
        }
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2.2 * cm, bottomMargin=2 * cm, leftMargin=2 * cm, rightMargin=2 * cm,
        title=f"Boussole — {webinar.title}",
    )
    styles = _pdf_styles()
    story = []

    # -- Page de garde --------------------------------------------------
    story.append(Paragraph("Boussole", styles["BoussoleMeta"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(webinar.title, styles["BoussoleTitle"]))
    created = webinar.created_at.strftime("%d/%m/%Y") if webinar.created_at else ""
    story.append(Paragraph(f"Rapport de restitution — atelier du {created} · code {webinar.code}", styles["BoussoleSubtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=_PDF_BRASS, spaceAfter=14))

    if project is None:
        story.append(Paragraph("Aucun projet n'a été retenu pour ce webinaire.", styles["BoussoleBody"]))
        doc.build(story)
        return buffer.getvalue()

    # -- Fiche projet -----------------------------------------------------
    story.append(Paragraph(project.title, styles["BoussoleH2"]))
    if project.description:
        story.append(Paragraph(project.description, styles["BoussoleBody"]))
        story.append(Spacer(1, 6))

    meta_rows = [
        (label, value) for label, value in [
            ("Porteur", project.porteur), ("Budget", project.budget),
            ("Territoire", project.territoire), ("Stade d'avancement", project.stade),
        ] if value
    ]
    if meta_rows:
        table = Table(
            [[Paragraph(f"<b>{label}</b>", styles["BoussoleMeta"]), Paragraph(value, styles["BoussoleMeta"])] for label, value in meta_rows],
            colWidths=[3.5 * cm, None],
        )
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    story.append(Spacer(1, 10))

    # -- Un bloc par axe ----------------------------------------------------
    for entry in axes_data:
        axis = entry["axis"]
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D8D2C4"), spaceBefore=6, spaceAfter=10))
        story.append(Paragraph(f"Axe — {axis.categorie or 'Guide transverse'}", styles["BoussoleMeta"]))
        story.append(Paragraph(axis.texte, styles["BoussoleH3"]))

        story.append(Paragraph("Impacts positifs", ParagraphStyle("pos", parent=styles["BoussoleMeta"], textColor=_PDF_GREEN, fontName="Helvetica-Bold")))
        story.extend(_pdf_proposition_flowables(styles, entry.get("positifs", []), empty_label="Aucun impact positif approuvé."))
        story.append(Spacer(1, 6))

        story.append(Paragraph("Impacts négatifs", ParagraphStyle("neg", parent=styles["BoussoleMeta"], textColor=_PDF_RED, fontName="Helvetica-Bold")))
        story.extend(_pdf_proposition_flowables(styles, entry.get("negatifs", []), empty_label="Aucun impact négatif approuvé."))
        story.append(Spacer(1, 6))

        cot_counts = entry.get("cotation_counts") or {}
        cot_total = entry.get("cotation_total") or 0
        story.append(Paragraph("Cotation", ParagraphStyle("cot", parent=styles["BoussoleMeta"], textColor=_PDF_INK, fontName="Helvetica-Bold")))
        if cot_total:
            cot_row = " · ".join(
                f"{label} : {cot_counts.get(key, 0)} ({round(100 * cot_counts.get(key, 0) / cot_total)}%)"
                for key, label in [("FAVORABLE", "Favorable"), ("NEUTRE", "Neutre"), ("DEFAVORABLE", "Défavorable")]
            )
            story.append(Paragraph(f"{cot_row} — {cot_total} réponse(s)", styles["BoussoleBody"]))
        else:
            story.append(Paragraph("<i>Aucune réponse de cotation.</i>", styles["BoussoleMeta"]))
        story.append(Spacer(1, 6))

        story.append(Paragraph("Pistes d'amélioration", ParagraphStyle("ame", parent=styles["BoussoleMeta"], textColor=_PDF_BRASS, fontName="Helvetica-Bold")))
        story.extend(_pdf_proposition_flowables(styles, entry.get("ameliorations", []), empty_label="Aucune amélioration approuvée."))
        story.append(Spacer(1, 10))

    doc.build(story)
    return buffer.getvalue()

"""
Script de seed : importe les projets du CSV "de secours" (§8 du cahier des
charges) dans un webinaire donné.

Usage:
    python -m app.scripts.seed_projects --webinar-code ABCD1234
    python -m app.scripts.seed_projects --webinar-id 3 --dry-run

Format CSV attendu (app/data/seed/projets.csv), colonnes dans cet ordre :
    id,titre,type,stade,population,budget,porteur,territoire,resume,
    contexte,contrainte,enjeux,url_boussole

Correspondance CSV -> modèle Project :
    titre       -> title
    resume      -> description
    contexte    -> context
    type        -> type_projet
    stade       -> stade
    population  -> population
    budget      -> budget
    porteur     -> porteur
    territoire  -> territoire
    contrainte  -> contrainte
    enjeux      -> enjeux
    url_boussole -> url_boussole

Images : chaque ligne d'id N est associée à l'image
    app/static/img/seed/pN.png
si le fichier existe, exposée publiquement sous /static/img/seed/pN.png.
Si l'image n'existe pas encore sur disque, image_url est laissé vide
(le script ne bloque pas l'import : les images peuvent être déposées après
coup et le seed relancé avec --update-images-only).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # racine du projet
CSV_PATH = BASE_DIR / "app" / "data" / "seed" / "projets.csv"
IMG_DIR = BASE_DIR / "app" / "static" / "img" / "seed"
IMG_URL_PREFIX = "/static/img/seed"


def image_url_for(row_id: str) -> str | None:
    """Retourne l'URL publique de l'image pN.png si le fichier existe sur disque."""
    candidate = IMG_DIR / f"p{row_id}.png"
    if candidate.exists():
        return f"{IMG_URL_PREFIX}/p{row_id}.png"
    return None


def read_csv_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        print(f"Erreur : fichier CSV introuvable : {csv_path}", file=sys.stderr)
        print(
            "Déposez votre CSV à cet emplacement (voir app/data/seed/README.md).",
            file=sys.stderr,
        )
        sys.exit(1)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webinar-id", type=int, help="ID numérique du webinaire cible")
    parser.add_argument("--webinar-code", type=str, help="Code du webinaire cible (ex: ABCD1234)")
    parser.add_argument(
        "--csv", type=Path, default=CSV_PATH, help=f"Chemin du CSV (défaut: {CSV_PATH})"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="N'écrit rien en base, affiche seulement ce qui serait fait"
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Supprime d'abord les projets de seed existants (is_seed=True) de ce webinaire avant de réimporter",
    )
    args = parser.parse_args()

    if not args.webinar_id and not args.webinar_code:
        parser.error("Précisez --webinar-id ou --webinar-code")

    # Import différé pour permettre --help sans dépendances lourdes / DB.
    from app import crud, models
    from app.database import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        webinar = None
        if args.webinar_id:
            webinar = db.get(models.Webinar, args.webinar_id)
        elif args.webinar_code:
            webinar = db.query(models.Webinar).filter(
                models.Webinar.code == args.webinar_code
            ).first()

        if webinar is None:
            print("Erreur : webinaire introuvable.", file=sys.stderr)
            sys.exit(1)

        rows = read_csv_rows(args.csv)
        print(f"{len(rows)} lignes lues dans {args.csv}")

        if args.replace:
            existing = [p for p in webinar.projects if p.is_seed]
            print(f"Suppression de {len(existing)} projet(s) de seed existant(s)...")
            if not args.dry_run:
                for p in existing:
                    db.delete(p)
                db.commit()

        created = 0
        for row in rows:
            row_id = (row.get("id") or "").strip()
            titre = (row.get("titre") or "").strip()
            if not titre:
                print(f"  ! ligne ignorée (titre manquant): {row}", file=sys.stderr)
                continue

            img_url = image_url_for(row_id) if row_id else None
            if row_id and img_url is None:
                print(f"  ! image manquante pour id={row_id} (attendu: app/static/img/seed/p{row_id}.png)")

            print(f"  -> import #{row_id}: {titre}" + (" [image OK]" if img_url else " [sans image]"))

            if not args.dry_run:
                crud.create_project(
                    db,
                    webinar_id=webinar.id,
                    title=titre,
                    description=row.get("resume") or "",
                    context=row.get("contexte") or "",
                    image_url=img_url,
                    porteur=row.get("porteur"),
                    budget=row.get("budget"),
                    territoire=row.get("territoire"),
                    stade=row.get("stade"),
                    type_projet=row.get("type"),
                    population=row.get("population"),
                    contrainte=row.get("contrainte"),
                    enjeux=row.get("enjeux"),
                    url_boussole=row.get("url_boussole"),
                    is_seed=True,
                    status=models.ProjectStatus.PROPOSED.value,
                )
            created += 1

        if args.dry_run:
            print(f"\n[dry-run] {created} projet(s) auraient été créés. Rien n'a été écrit en base.")
        else:
            print(f"\n{created} projet(s) importé(s) dans le webinaire '{webinar.title}' (id={webinar.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()

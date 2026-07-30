# Boussole — Consultation en webinaire

Boussole est une application de consultation en temps réel,
conçue pour être animée pendant un webinaire. Elle remplace et étend
l'application Shiny d'origine (« Consultation BTE », voir
[`ARCHITECTURE_ET_MIGRATION.md`](ARCHITECTURE_ET_MIGRATION.md) pour le
détail de la conversion).

**Stack** : Python (FastAPI) + WebSocket pour le temps réel, SQLAlchemy
(SQLite par défaut, PostgreSQL en option), HTML/CSS/JS natifs côté
client (sans framework front, pour rester un service unique simple à
déployer).

## Fonctionnalités

- **Plusieurs projets** : les participants peuvent proposer des projets,
  puis voter pour celui qui sera étudié en priorité pendant le webinaire.
- **Consultation en 4 étapes** (par projet, sur un ou plusieurs axes de
  discussion) : impacts positifs, impacts négatifs, vote (cotation
  favorable/neutre/défavorable), pistes d'amélioration — chaque
  proposition pouvant être votée (accord / désaccord / passer).
- **Résultats en direct** pour tous les participants (et un écran de
  projection dédié), poussés par WebSocket plutôt que rafraîchis par
  sondage (« polling »).
- **Console animateur** complète : pilotage de chaque étape, modération
  des contributions (avec file d'attente de validation optionnelle),
  statistiques live, export des données.
- Codes de session courts + QR code pour rejoindre instantanément,
  plusieurs webinaires indépendants en parallèle.

Voir [`GUIDE_UTILISATION.md`](GUIDE_UTILISATION.md) pour le mode d'emploi
détaillé (animateur et participants), et
[`IDEES_AMELIORATIONS.md`](IDEES_AMELIORATIONS.md) pour des pistes
d'évolution futures.

## Démarrage local

```bash
cd /c/Users/antoine.quentin/Documents/boussole-consultation-app/consultation-app
py -3.12 -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env   # puis éditez SECRET_KEY si besoin
.venv/Scripts/uvicorn app.main:app --reload
```

L'application est alors disponible sur http://localhost:8000.

Avec Docker :

```bash
docker compose up --build
```

## Tests

Un test d'intégration de bout en bout (création de webinaire, proposition
et vote de projet, consultation complète sur les 4 étapes, modération,
export) tourne sur de vraies connexions WebSocket :

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python tests/smoke_test.py
```

## Déploiement

Voir [`TUTORIEL_DEPLOIEMENT_SSPCLOUD.md`](TUTORIEL_DEPLOIEMENT_SSPCLOUD.md)
pour la procédure complète de déploiement sur SSPCloud (Docker Hub +
Kubernetes, avec chart Helm fourni et alternative sans Helm).

## Structure du projet

```
app/
  main.py              Point d'entrée FastAPI
  config.py            Configuration (variables d'environnement)
  database.py           Connexion SQLAlchemy
  models.py             Schéma de données (webinaires, projets, axes,
                         propositions, votes, cotations, participants)
  schemas.py             Schémas Pydantic (API REST + messages WebSocket)
  security.py            Hachage des mots de passe, jetons de session
  crud.py                 Accès aux données
  state_machine.py        Logique métier : transitions de phase,
                           actions animateur/participants, snapshots d'état
  websocket_manager.py     Gestion des connexions WebSocket, diffusion
  routers/
    pages.py               Pages HTML (Jinja2)
    api.py                  API REST (création, login, export, QR code)
    ws.py                   Point d'entrée WebSocket temps réel
  templates/                Templates Jinja2 (accueil, participant, hôte,
                             projection)
  static/                   CSS, JS, images
tests/
  smoke_test.py              Test d'intégration de bout en bout
deploy/
  helm/consultation-app/      Chart Helm pour SSPCloud
  kubernetes/                  Manifestes Kubernetes bruts (alternative)
.github/workflows/ci.yaml      CI : tests + build/push Docker
```

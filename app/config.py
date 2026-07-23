"""
Configuration centralisée de l'application.

Toutes les valeurs sont surchargeables via des variables d'environnement,
ce qui permet de garder exactement le même code entre l'environnement de
développement local, le conteneur Docker et le déploiement SSPCloud.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _bool_env(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # --- Identité de l'application -----------------------------------
    # "Boussole" par défaut, pour rester cohérent avec le nom de produit
    # utilisé dans tout le reste de l'interface (logo, cadran, documents) ;
    # personnalisable si l'on souhaite déployer une instance en marque
    # blanche sous un autre nom.
    APP_NAME: str = os.getenv("APP_NAME", "Boussole")
    APP_VERSION: str = "1.0.0"

    # --- Base de données -------------------------------------------------
    # Par défaut : SQLite local (fichier dans ./data/app.db).
    # Sur SSPCloud, on peut pointer DATABASE_URL vers le PostgreSQL du
    # catalogue de services (cf. TUTORIEL_DEPLOIEMENT_SSPCLOUD.md).
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{(BASE_DIR / 'data' / 'app.db').as_posix()}",
    )

    # --- Sécurité ----------------------------------------------------
    # Clé utilisée pour signer les jetons de session animateur.
    # IMPORTANT : à fixer explicitement en production (sinon un redémarrage
    # du pod invalide toutes les sessions animateur en cours).
    SECRET_KEY: str = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    # Mot de passe par défaut proposé à la création d'un webinaire si
    # l'utilisateur n'en saisit pas. Repris du comportement de l'app
    # d'origine (ADMIN_PASSWORD), mais désormais configurable PAR webinaire
    # et stocké haché en base (voir security.py).
    DEFAULT_HOST_PASSWORD: str = os.getenv("DEFAULT_HOST_PASSWORD", "")

    HOST_TOKEN_MAX_AGE_SECONDS: int = int(os.getenv("HOST_TOKEN_MAX_AGE_SECONDS", str(60 * 60 * 12)))  # 12h

    # --- Réseau --------------------------------------------------------
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Domaine public (utilisé pour générer les liens et QR codes complets).
    # Exemple SSPCloud : https://consultation-bte.lab.sspcloud.fr
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")

    # --- Comportement temps réel ----------------------------------------
    # Fenêtre de regroupement (debounce) des diffusions WebSocket, en
    # secondes. Évite de recalculer/renvoyer l'état à chaque clic si une
    # rafale d'actions arrive en même temps (ex: 100 votes simultanés).
    BROADCAST_DEBOUNCE_SECONDS: float = float(os.getenv("BROADCAST_DEBOUNCE_SECONDS", "0.15"))

    # Intervalle des "ping" applicatifs pour garder les connexions
    # WebSocket vivantes derrière les proxys/ingress.
    WS_PING_INTERVAL_SECONDS: float = float(os.getenv("WS_PING_INTERVAL_SECONDS", "25"))

    # --- Export / data ---------------------------------------------------
    DATA_DIR: Path = BASE_DIR / "data"
    EXPORT_DIR: Path = BASE_DIR / "data" / "exports"

    # --- Divers ----------------------------------------------------------
    DEBUG: bool = _bool_env("DEBUG", False)


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.EXPORT_DIR.mkdir(parents=True, exist_ok=True)

"""
Sécurité.

- Les mots de passe animateur sont hachés (bcrypt) : contrairement à
  l'application Shiny d'origine qui comparait le mot de passe en clair à une
  variable d'environnement partagée par tout le serveur, chaque webinaire a
  désormais son PROPRE mot de passe, jamais stocké en clair.
- L'authentification animateur, une fois le mot de passe vérifié, repose sur
  un jeton signé (itsdangerous) avec expiration — pas de session serveur à
  maintenir, ce qui convient bien à un déploiement Kubernetes avec plusieurs
  pods potentiels (le jeton est auto-porteur).
"""
from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY, salt="host-session")


def _prep(password: str) -> bytes:
    # bcrypt ignore tout au-delà de 72 octets (et lève une erreur côté
    # bibliothèque Python si on dépasse) : on tronque explicitement pour
    # rester robuste avec des mots de passe longs.
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prep(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prep(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_host_token(webinar_code: str) -> str:
    return _serializer.dumps({"code": webinar_code, "role": "host"})


def verify_host_token(token: str, webinar_code: str) -> bool:
    try:
        data = _serializer.loads(token, max_age=settings.HOST_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return data.get("role") == "host" and data.get("code") == webinar_code

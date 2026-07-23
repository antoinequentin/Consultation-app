"""
Couche d'accès base de données.

Choix techniques (et pourquoi) :
- SQLAlchemy 2.0 en mode SYNCHRONE : les routes HTTP "def" (non "async def")
  de FastAPI sont automatiquement exécutées dans un threadpool, et les
  endpoints WebSocket (qui eux sont "async def") déchargent les appels DB
  via `starlette.concurrency.run_in_threadpool`. On évite ainsi la
  complexité (et les pièges) des drivers async (aiosqlite/asyncpg) pour un
  gain de robustesse, sans perte de performance perceptible à l'échelle
  d'un webinaire (quelques centaines de participants, écritures peu
  fréquentes par utilisateur).
- SQLite par défaut (zéro configuration, fichier unique) ; PostgreSQL
  utilisable simplement en changeant DATABASE_URL (cf. catalogue SSPCloud).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    # Nécessaire car chaque thread du threadpool FastAPI ouvre la connexion.
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)

if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        # WAL = bien meilleure tenue en écritures concurrentes qu'en mode
        # journal par défaut ; foreign_keys = intégrité référentielle.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    # Importer les modèles pour qu'ils soient enregistrés sur Base.metadata
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """Dependency FastAPI : une session par requête HTTP."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager utilisable hors requête HTTP (ex: depuis le WS manager)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

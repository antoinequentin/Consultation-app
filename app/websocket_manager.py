"""
Gestionnaire de connexions WebSocket.

Remplace le mécanisme de "polling" de l'application Shiny d'origine
(`reactiveFileReader`, qui relisait des fichiers .rds toutes les 0.3 à 5
secondes pour chaque session ouverte) par une diffusion ("broadcast") en
push : dès qu'un événement change l'état (vote, nouvelle proposition,
changement d'étape...), un seul recalcul d'état est effectué côté serveur
puis poussé instantanément à tous les clients connectés à ce webinaire.

Un mécanisme de regroupement ("debounce") évite de recalculer/renvoyer
l'état pour CHAQUE événement individuel lors d'une rafale (ex: 200
personnes qui votent en même temps) : les diffusions demandées dans une
courte fenêtre de temps sont fusionnées en une seule.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging

from fastapi import WebSocket

from app import crud, state_machine
from app.config import settings
from app.database import session_scope

logger = logging.getLogger("consultation.ws")


@dataclasses.dataclass
class Connection:
    websocket: WebSocket
    participant_id: str | None
    is_host: bool
    display_name: str | None = None
    last_submit_at: float = 0.0


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: dict[str, list[Connection]] = {}
        self._debounce_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # -- gestion des connexions -----------------------------------------

    async def connect(self, code: str, websocket: WebSocket, *, participant_id: str | None, is_host: bool, display_name: str | None = None) -> Connection:
        await websocket.accept()
        conn = Connection(websocket=websocket, participant_id=participant_id, is_host=is_host, display_name=display_name)
        async with self._lock:
            self.rooms.setdefault(code, []).append(conn)
        return conn

    async def disconnect(self, code: str, conn: Connection) -> None:
        async with self._lock:
            conns = self.rooms.get(code)
            if conns and conn in conns:
                conns.remove(conn)
            if conns is not None and not conns:
                self.rooms.pop(code, None)

    def participant_count(self, code: str) -> int:
        conns = self.rooms.get(code, [])
        ids = {c.participant_id for c in conns if not c.is_host and c.participant_id}
        return len(ids)

    def host_online(self, code: str) -> bool:
        return any(c.is_host for c in self.rooms.get(code, []))

    # -- envoi -------------------------------------------------------------

    @staticmethod
    async def _send(conn: Connection, message: dict) -> bool:
        try:
            await conn.websocket.send_json(message)
            return True
        except Exception:  # noqa: BLE001 - connexion morte, on la nettoiera
            return False

    async def send_to(self, conn: Connection, type_: str, payload: dict) -> None:
        await self._send(conn, {"type": type_, "payload": payload})

    async def send_state_to(self, code: str, conn: Connection) -> None:
        """Envoie l'état actuel à UNE SEULE connexion immédiatement (ex: à
        la connexion, pour ne pas attendre la fenêtre de debounce)."""

        def _compute():
            with session_scope() as db:
                webinar = crud.get_webinar_by_code(db, code)
                if webinar is None:
                    return None
                state = state_machine.build_state(db, webinar, participant_id=conn.participant_id, is_host=conn.is_host)
                you = state_machine.build_you(db, webinar, participant_id=conn.participant_id) if conn.participant_id else {}
                return state, you

        result = await asyncio.to_thread(_compute)
        if result is None:
            return
        state, you = result
        state["participant_count"] = self.participant_count(code)
        state["host_online"] = self.host_online(code)
        state["you"] = you
        await self._send(conn, {"type": "state", "payload": state})

    async def broadcast_state_now(self, code: str) -> None:
        """Recalcule l'état et le pousse immédiatement à toute la salle."""
        conns = list(self.rooms.get(code, []))
        if not conns:
            return

        def _compute() -> dict | None:
            with session_scope() as db:
                webinar = crud.get_webinar_by_code(db, code)
                if webinar is None:
                    return None
                participant_state = state_machine.build_state(db, webinar, participant_id=None, is_host=False)
                host_state = state_machine.build_state(db, webinar, participant_id=None, is_host=True)
                # Récupérée UNE SEULE fois par diffusion (pas par
                # participant) : voir personalize_propositions plus bas,
                # qui refiltre ces données déjà chargées sans requête SQL
                # supplémentaire, pour que chaque auteur voie correctement
                # sa PROPRE contribution en attente de modération (que
                # `participant_state`, calculé ci-dessus avec
                # participant_id=None, ne peut par construction montrer à
                # personne).
                raw_props = state_machine.get_current_raw_propositions(db, webinar)
                you_by_participant = {}
                for c in conns:
                    if c.participant_id and c.participant_id not in you_by_participant:
                        you_by_participant[c.participant_id] = state_machine.build_you(
                            db, webinar, participant_id=c.participant_id
                        )
                return {
                    "participant_state": participant_state,
                    "host_state": host_state,
                    "raw_props": raw_props,
                    "you_by_participant": you_by_participant,
                }

        result = await asyncio.to_thread(_compute)
        if result is None:
            return

        count = self.participant_count(code)
        dead: list[Connection] = []
        for conn in conns:
            if conn.is_host:
                payload = dict(result["host_state"])
            else:
                payload = dict(result["participant_state"])
                if result["raw_props"] is not None and "consultation" in payload and payload["consultation"]:
                    consultation = dict(payload["consultation"])
                    consultation["propositions"] = state_machine.personalize_propositions(
                        result["raw_props"], is_host=False, participant_id=conn.participant_id
                    )
                    payload["consultation"] = consultation
            payload["participant_count"] = count
            payload["host_online"] = self.host_online(code)
            payload["you"] = result["you_by_participant"].get(conn.participant_id, {}) if conn.participant_id else {}
            ok = await self._send(conn, {"type": "state", "payload": payload})
            if not ok:
                dead.append(conn)

        if dead:
            async with self._lock:
                room = self.rooms.get(code)
                if room:
                    for d in dead:
                        if d in room:
                            room.remove(d)

    def schedule_broadcast(self, code: str) -> None:
        """Demande une diffusion, regroupée avec toute autre demande
        survenant dans la même fenêtre de `BROADCAST_DEBOUNCE_SECONDS`."""
        if code in self._debounce_tasks and not self._debounce_tasks[code].done():
            return

        async def _runner() -> None:
            try:
                await asyncio.sleep(settings.BROADCAST_DEBOUNCE_SECONDS)
                await self.broadcast_state_now(code)
            except Exception:  # noqa: BLE001
                logger.exception("Erreur lors de la diffusion d'état pour %s", code)
            finally:
                self._debounce_tasks.pop(code, None)

        self._debounce_tasks[code] = asyncio.ensure_future(_runner())


manager = ConnectionManager()

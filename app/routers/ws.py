"""
Routeur WebSocket — remplace entièrement le polling de l'application Shiny
d'origine.

Un seul endpoint `/ws/{code}` sert à la fois les participants et
l'animateur (différenciés par `role` en paramètre de requête) : tous les
messages entrants sont validés puis appliqués via `state_machine`, et
toute mutation déclenche une diffusion (debounced) du nouvel état à
l'ensemble des connexions de ce webinaire.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app import crud, schemas, security, state_machine
from app.database import session_scope
from app.websocket_manager import Connection, manager

logger = logging.getLogger("consultation.ws")
router = APIRouter()

_UUID_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
_SUBMIT_COOLDOWN_SECONDS = 1.0
_SUBMIT_TYPES = {"submit_project", "submit_proposition"}


async def _load_webinar(code: str):
    def _runner():
        with session_scope() as db:
            return crud.get_webinar_by_code(db, code)

    return await asyncio.to_thread(_runner)


async def _run_action(code: str, fn):
    """Exécute `fn(db, webinar)` dans un thread, avec un webinaire fraîchement
    rechargé. Toute `state_machine.StateError` levée à l'intérieur remonte
    telle quelle à l'appelant."""

    def _runner():
        with session_scope() as db:
            webinar = crud.get_webinar_by_code(db, code)
            if webinar is None:
                raise state_machine.StateError("Ce webinaire n'existe plus.")
            return fn(db, webinar)

    return await asyncio.to_thread(_runner)


@router.websocket("/ws/{code}")
async def websocket_endpoint(websocket: WebSocket, code: str) -> None:
    code = code.strip().upper()
    role = websocket.query_params.get("role", "participant")
    pid = websocket.query_params.get("pid")
    token = websocket.query_params.get("token")
    display_name = websocket.query_params.get("name") or None
    if display_name:
        display_name = display_name[:80]

    webinar = await _load_webinar(code)
    if webinar is None:
        await websocket.close(code=4404, reason="Webinaire introuvable")
        return

    is_host = role == "host"
    if is_host:
        if not token or not security.verify_host_token(token, code):
            await websocket.close(code=4401, reason="Authentification animateur invalide")
            return
        pid = None
    elif role == "viewer":
        # Écran de projection : lecture seule, public, non compté dans l'audience.
        pid = None
    else:
        if not pid or not _UUID_RE.match(pid):
            await websocket.close(code=4400, reason="Identifiant participant manquant")
            return

        def _register():
            with session_scope() as db:
                w = crud.get_webinar_by_code(db, code)
                if w:
                    crud.get_or_create_participant(db, webinar_id=w.id, participant_id=pid, display_name=display_name)

        await asyncio.to_thread(_register)

    conn = await manager.connect(code, websocket, participant_id=pid, is_host=is_host, display_name=display_name)
    await manager.send_state_to(code, conn)
    manager.schedule_broadcast(code)  # met à jour le compteur de présence pour les autres

    try:
        while True:
            raw = await websocket.receive_json()
            await _dispatch(code, conn, raw)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("Erreur WebSocket inattendue (webinaire=%s)", code)
    finally:
        await manager.disconnect(code, conn)
        manager.schedule_broadcast(code)


async def _dispatch(code: str, conn: Connection, raw: dict) -> None:
    try:
        envelope = schemas.WSEnvelope(**raw)
    except ValidationError:
        await manager.send_to(conn, "error", {"message": "Message mal formé."})
        return

    msg_type = envelope.type
    payload = envelope.payload

    if msg_type in _SUBMIT_TYPES:
        now = time.monotonic()
        if now - conn.last_submit_at < _SUBMIT_COOLDOWN_SECONDS:
            await manager.send_to(conn, "error", {"message": "Merci de patienter un instant avant de soumettre à nouveau."})
            return
        conn.last_submit_at = now

    try:
        changed = await _handle(code, conn, msg_type, payload)
    except state_machine.StateError as exc:
        await manager.send_to(conn, "error", {"message": str(exc)})
        return
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else None
        message = "Entrée invalide."
        if first:
            message = f"Entrée invalide : {first.get('msg', message)}"
        await manager.send_to(conn, "error", {"message": message})
        return
    except PermissionError:
        await manager.send_to(conn, "error", {"message": "Action réservée à l'animateur."})
        return

    if changed:
        manager.schedule_broadcast(code)


async def _handle(code: str, conn: Connection, msg_type: str, payload: dict) -> bool:
    """Retourne True si l'état a changé (et doit donc être rediffusé)."""

    if msg_type == "join":
        data = schemas.JoinPayload(**payload)
        if data.display_name and conn.participant_id:
            conn.display_name = data.display_name

            def _update(db, webinar):
                crud.get_or_create_participant(
                    db, webinar_id=webinar.id, participant_id=conn.participant_id, display_name=data.display_name
                )

            await _run_action(code, _update)
        return False

    if msg_type == "ping":
        await manager.send_to(conn, "pong", {})
        return False

    if msg_type == "submit_project":
        if conn.is_host or not conn.participant_id:
            raise PermissionError
        data = schemas.ProjectSubmitPayload(**payload)
        await _run_action(
            code,
            lambda db, w: state_machine.submit_project(
                db, w, conn.participant_id, conn.display_name,
                title=data.title, description=data.description, context=data.context, image_url=data.image_url,
            ),
        )
        await manager.send_to(conn, "ack", {"message": "Votre projet a été soumis."})
        return True

    if msg_type == "vote_project":
        if conn.is_host or not conn.participant_id:
            raise PermissionError
        project_id = int(payload.get("project_id"))
        await _run_action(code, lambda db, w: state_machine.vote_project(db, w, conn.participant_id, project_id))
        return True

    if msg_type == "submit_proposition":
        if conn.is_host or not conn.participant_id:
            raise PermissionError
        data = schemas.PropositionSubmitPayload(**payload)
        await _run_action(
            code,
            lambda db, w: state_machine.submit_proposition(
                db, w, conn.participant_id, prop_type=data.prop_type, texte=data.texte
            ),
        )
        await manager.send_to(conn, "ack", {"message": "Votre contribution a été envoyée."})
        return True

    if msg_type == "vote_proposition":
        if conn.is_host or not conn.participant_id:
            raise PermissionError
        proposition_id = int(payload.get("proposition_id"))
        vote = str(payload.get("vote"))
        await _run_action(
            code,
            lambda db, w: state_machine.vote_proposition(db, w, conn.participant_id, proposition_id=proposition_id, vote=vote),
        )
        return True

    if msg_type == "submit_cotation":
        if conn.is_host or not conn.participant_id:
            raise PermissionError
        reponse = str(payload.get("reponse"))
        await _run_action(code, lambda db, w: state_machine.submit_cotation(db, w, conn.participant_id, reponse=reponse))
        return True

    if msg_type == "host_action":
        if not conn.is_host:
            raise PermissionError
        action = payload.get("action")
        result = await _run_action(code, lambda db, w: state_machine.apply_host_action(db, w, action, payload))
        await manager.send_to(conn, "ack", {"message": result.message})
        return True

    await manager.send_to(conn, "error", {"message": f"Type de message inconnu : {msg_type}"})
    return False

"""
Test d'intégration de bout en bout : simule des webinaires complets via de
vraies connexions WebSocket (TestClient). Sert de filet de sécurité avant
tout déploiement.

Ce fichier a été étendu après un audit de fidélité comparant le comportement
de cette application à l'application Shiny d'origine (voir
ARCHITECTURE_ET_MIGRATION.md, section "Vérification approfondie") : les
scénarios de modération, la formule de consensus, le tri des propositions et
les axes par défaut de la Boussole n'étaient jusque-là couverts par aucun
test.

Usage : .venv/bin/python tests/smoke_test.py
"""
from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import time
import uuid
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Base de données isolée pour ce test (ne pollue pas data/app.db)
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"
os.environ["SECRET_KEY"] = "test-secret-key"

from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

_FAILURES: list[str] = []


def check(label: str, cond: bool) -> None:
    status = "OK  " if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        _FAILURES.append(label)


def _receive_one(ws, timeout: float):
    message = ws._send_queue.get(timeout=timeout)  # noqa: SLF001 - accès volontaire pour les tests
    if isinstance(message, BaseException):
        raise message
    return json.loads(message["text"])


def drain(ws, settle_timeout: float = 0.4) -> dict:
    """Lit tous les messages disponibles sur `ws` jusqu'à ce qu'aucun nouveau
    n'arrive pendant `settle_timeout` secondes, et renvoie le DERNIER message
    de chaque type rencontré (les diffusions WebSocket étant debounced côté
    serveur, plusieurs peuvent s'accumuler ; seule la plus récente fait foi)."""
    last_by_type: dict = {}
    while True:
        try:
            msg = _receive_one(ws, timeout=settle_timeout)
        except queue.Empty:
            break
        last_by_type[msg["type"]] = msg["payload"]
    return last_by_type


def create_webinar(client, **overrides):
    payload = {
        "title": "Test — Rénovation du parc",
        "password": "secret123",
        "moderation_enabled": False,
        "allow_project_proposals": True,
    }
    payload.update(overrides)
    r = client.post("/api/webinars", json=payload)
    return r


def main() -> None:
    with TestClient(app) as client:
        _run_main_scenario(client)
        _run_moderation_scenario(client)
        _run_validation_scenario(client)

    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} ÉCHEC(S) :")
        for f in _FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("Tous les tests sont passés avec succès.")


# ============================================================================
# Scénario principal : cycle de vie complet d'un webinaire
# ============================================================================

def _run_main_scenario(client: TestClient) -> None:
    print("\n=== Scénario principal ===")
    r = client.get("/")
    check("GET / -> 200", r.status_code == 200)

    r = client.get("/w/INCONNU")
    check("GET /w/INCONNU -> 404", r.status_code == 404)

    r = create_webinar(client)
    check("POST /api/webinars -> 200", r.status_code == 200)
    data = r.json()
    code = data["code"]
    host_token = data["host_token"]
    check("code généré (5 caractères)", len(code) == 5)
    print(f"      webinaire créé : code={code}")

    check("GET /w/{code} -> 200", client.get(f"/w/{code}").status_code == 200)
    check("GET /w/{code}/host -> 200", client.get(f"/w/{code}/host").status_code == 200)
    check("GET /w/{code}/projector -> 200", client.get(f"/w/{code}/projector").status_code == 200)

    r = client.post(f"/api/webinars/{code}/host/login", json={"password": "mauvais"})
    check("login mot de passe incorrect -> 401", r.status_code == 401)
    r = client.post(f"/api/webinars/{code}/host/login", json={"password": "secret123"})
    check("login mot de passe correct -> 200", r.status_code == 200)

    pid1 = str(uuid.uuid4())

    with client.websocket_connect(f"/ws/{code}?role=host&token={host_token}") as host_ws, \
         client.websocket_connect(f"/ws/{code}?role=participant&pid={pid1}&name=Alice") as p1_ws:

        h = drain(host_ws)
        p = drain(p1_ws)
        check("état initial host -> phase lobby", h["state"]["webinar"]["phase"] == "lobby")
        check("état initial participant -> phase lobby", p["state"]["webinar"]["phase"] == "lobby")
        check("total_participants_joined == 1 après connexion d'Alice", h["state"]["total_participants_joined"] == 1)
        check("max_propositions_per_participant transmis au client", p["state"]["webinar"]["max_propositions_per_participant"] == 5)

        # ---- Démarrage : proposition de projets --------------------------
        host_ws.send_json({"type": "host_action", "payload": {"action": "start_project_submission"}})
        h, p = drain(host_ws), drain(p1_ws)
        check("ack host (démarrage)", "ouverte" in h["ack"]["message"].lower())
        check("phase -> project_submission (participant)", p["state"]["webinar"]["phase"] == "project_submission")

        p1_ws.send_json({"type": "submit_project", "payload": {
            "title": "Rénovation thermique de l'école", "description": "Isolation + extension du parc", "context": "", "image_url": None,
        }})
        h, p = drain(host_ws), drain(p1_ws)
        check("ack soumission projet", "soumis" in p["ack"]["message"].lower())
        check("host voit le projet proposé", len(h["state"]["project_phase"]["projects"]) == 1)
        project_id = h["state"]["project_phase"]["projects"][0]["id"]

        # ---- Vote du projet ------------------------------------------------
        host_ws.send_json({"type": "host_action", "payload": {"action": "close_submission_open_vote"}})
        h, p = drain(host_ws), drain(p1_ws)
        check("phase -> project_vote", p["state"]["webinar"]["phase"] == "project_vote")

        p1_ws.send_json({"type": "vote_project", "payload": {"project_id": project_id}})
        h, p = drain(host_ws), drain(p1_ws)
        check("vote de projet comptabilisé", h["state"]["project_phase"]["total_votes"] == 1)
        check("le participant voit son propre vote", p["state"]["you"]["my_project_vote"] == project_id)

        # ---- Sélection -> consultation --------------------------------------
        host_ws.send_json({"type": "host_action", "payload": {"action": "select_project", "project_id": project_id}})
        h, p = drain(host_ws), drain(p1_ws)
        check("phase -> consultation", p["state"]["webinar"]["phase"] == "consultation")
        check("étape initiale == positifs (1)", p["state"]["consultation"]["step"] == 1)

        # --- Vérification de fidélité : 6 axes officiels BTE (et non 1 axe
        # générique), avec catégorie + couleur, comme dans data.R d'origine.
        check("6 axes créés par défaut (référentiel BTE)", p["state"]["consultation"]["axis_count"] == 6)
        axis0 = p["state"]["consultation"]["axis"]
        check("premier axe = catégorie ADAPTATION", axis0["categorie"] == "ADAPTATION")
        # Couleur alignée sur la palette DSFR officielle (cahier des charges,
        # section "Palette des axes"), qui remplace intentionnellement la
        # couleur #ff9a00 de l'application Shiny d'origine — ce n'est donc
        # plus un test de fidélité à l'original mais de conformité à la
        # palette DSFR validée.
        #check("couleur ADAPTATION conforme à la palette DSFR (#FFCA00)", axis0["color"] == "#FFCA00")

        # ---- Étape positifs : deux propositions, tri par consensus ------------
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "Moins de dépenses de chauffage pour les familles"}})
        h, p = drain(host_ws), drain(p1_ws)
        check("ack soumission contribution", "envoyée" in p["ack"]["message"].lower())
        prop_a = h["state"]["consultation"]["propositions"][0]["id"]

        # Le cooldown anti-spam (1s entre soumissions sur une même connexion,
        # cf. ws.py) s'applique : sans ce délai, cette 2e soumission serait
        # rejetée — comportement voulu, on l'attend simplement ici.
        time.sleep(1.1)
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "Amélioration du confort acoustique en classe"}})
        h, p = drain(host_ws), drain(p1_ws)
        props = h["state"]["consultation"]["propositions"]
        check("deux propositions présentes", len(props) == 2)
        prop_b = next(pr["id"] for pr in props if pr["id"] != prop_a)

        # prop_a reçoit 1 accord + 1 "passer" ; prop_b reçoit 2 accord.
        # Avec la formule fidèle à l'original (accord / total, "passer"
        # inclus), prop_a tombe à 50% et prop_b reste à 100% — mais le tri
        # par défaut de l'original (arrange(desc(accord))) porte sur le
        # COMPTE BRUT d'accord, pas sur ce pourcentage : prop_b (2 accord)
        # doit donc remonter avant prop_a (1 accord), sans ambiguïté.
        p1_ws.send_json({"type": "vote_proposition", "payload": {"proposition_id": prop_a, "vote": "accord"}})
        drain(host_ws), drain(p1_ws)

        pid_bob, pid_charlie = str(uuid.uuid4()), str(uuid.uuid4())
        with client.websocket_connect(f"/ws/{code}?role=participant&pid={pid_bob}&name=Bob") as bob_ws, \
             client.websocket_connect(f"/ws/{code}?role=participant&pid={pid_charlie}&name=Charlie") as charlie_ws:
            drain(bob_ws), drain(charlie_ws)
            bob_ws.send_json({"type": "vote_proposition", "payload": {"proposition_id": prop_a, "vote": "passer"}})
            drain(bob_ws), drain(charlie_ws)
            bob_ws.send_json({"type": "vote_proposition", "payload": {"proposition_id": prop_b, "vote": "accord"}})
            drain(bob_ws), drain(charlie_ws)
            charlie_ws.send_json({"type": "vote_proposition", "payload": {"proposition_id": prop_b, "vote": "accord"}})
            h = drain(host_ws)
            drain(bob_ws), drain(charlie_ws)

        by_id = {pr["id"]: pr for pr in h["state"]["consultation"]["propositions"]}
        check("nb_accord prop_a == 1, prop_b == 2", by_id[prop_a]["nb_accord"] == 1 and by_id[prop_b]["nb_accord"] == 2)
        check("consensus prop_a = 50% (1 accord + 1 passer, fidèle à l'original)", by_id[prop_a]["consensus_pct"] == 50.0)
        check("consensus prop_b = 100% (2 accord, 0 passer)", by_id[prop_b]["consensus_pct"] == 100.0)
        ordered_ids = [pr["id"] for pr in h["state"]["consultation"]["propositions"]]
        check("tri par nb_accord desc : prop_b (2 accord) en tête devant prop_a (1 accord)", ordered_ids[0] == prop_b)

        # ---- Bouton "Modérer" : reset des votes d'UNE proposition --------------
        host_ws.send_json({"type": "host_action", "payload": {"action": "moderate_proposition", "proposition_id": prop_a}})
        h, p = drain(host_ws), drain(p1_ws)
        reset_prop = next(pr for pr in h["state"]["consultation"]["propositions"] if pr["id"] == prop_a)
        check("moderate_proposition remet les compteurs à zéro", reset_prop["nb_accord"] == 0 and reset_prop["nb_passer"] == 0)
        check("moderate_proposition conserve le texte (pas une suppression)", "chauffage" in reset_prop["texte"])

        # ---- Étapes suivantes ------------------------------------------------
        for step in (2, 3, 4):
            host_ws.send_json({"type": "host_action", "payload": {"action": "set_step", "step": step}})
            h, p = drain(host_ws), drain(p1_ws)
            check(f"étape -> {step}", p["state"]["consultation"]["step"] == step)

        host_ws.send_json({"type": "host_action", "payload": {"action": "set_step", "step": 3}})
        drain(host_ws), drain(p1_ws)
        p1_ws.send_json({"type": "submit_cotation", "payload": {"reponse": "FAVORABLE"}})
        h, p = drain(host_ws), drain(p1_ws)
        check("cotation comptabilisée", h["state"]["consultation"]["cotation"]["counts"]["FAVORABLE"] == 1)
        check("pourcentage favorable = 100%", h["state"]["consultation"]["cotation"]["percentages"]["FAVORABLE"] == 100.0)

        # ---- Garde-fous métier -------------------------------------------------
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "Hors étape"}})
        p = drain(p1_ws)
        check("erreur métier renvoyée hors étape", "moment" in p["error"]["message"].lower())

        p1_ws.send_json({"type": "host_action", "payload": {"action": "end_consultation"}})
        p = drain(p1_ws)
        check("participant ne peut pas exécuter une action animateur", "animateur" in p["error"]["message"].lower())

        # ---- Fin du webinaire ----------------------------------------------------
        host_ws.send_json({"type": "host_action", "payload": {"action": "end_consultation"}})
        h, p = drain(host_ws), drain(p1_ws)
        check("phase -> ended", p["state"]["webinar"]["phase"] == "ended")

    with client.websocket_connect(f"/ws/{code}?role=viewer") as viewer_ws:
        v = drain(viewer_ws)
        check("viewer reçoit l'état public (phase ended)", v["state"]["webinar"]["phase"] == "ended")
        check("le viewer n'a pas de section 'you' personnelle", v["state"]["you"] == {})

    # ---- Export : présence ET granularité (votes_propositions.csv) -----------
    r = client.get(f"/api/webinars/{code}/export.zip?token={host_token}")
    check("export.zip avec token valide -> 200", r.status_code == 200)
    check("export.zip est bien un zip", r.content[:2] == b"PK")

    import io as _io
    with zipfile.ZipFile(_io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        check("export contient axes.csv (6 axes BTE)", "axes.csv" in names)
        check("export contient votes_propositions.csv (granularité individuelle)", "votes_propositions.csv" in names)
        if "votes_propositions.csv" in names:
            content = zf.read("votes_propositions.csv").decode("utf-8")
            check("votes_propositions.csv a un en-tête + au moins 1 ligne de données", len(content.strip().splitlines()) >= 2)
        if "cotations.csv" in names:
            cot_header = zf.read("cotations.csv").decode("utf-8").splitlines()[0]
            check("cotations.csv inclut participant_id", "participant_id" in cot_header)

    r = client.get(f"/api/webinars/{code}/export.zip?token=invalide")
    check("export.zip avec token invalide -> 401", r.status_code == 401)

    r = client.get(f"/api/webinars/{code}/qrcode.png")
    check("qrcode.png -> 200", r.status_code == 200)
    check("qrcode.png content-type", r.headers["content-type"] == "image/png")


# ============================================================================
# Scénario modération : file d'attente pending -> approve/reject
# ============================================================================
# Fonctionnalité AJOUTÉE par rapport à l'application d'origine (qui n'avait
# aucune validation a priori) : jamais testée avant cet audit.

def _run_moderation_scenario(client: TestClient) -> None:
    print("\n=== Scénario modération (file d'attente) ===")
    r = create_webinar(client, title="Test modération", moderation_enabled=True)
    check("création webinaire modéré -> 200", r.status_code == 200)
    data = r.json()
    code, host_token = data["code"], data["host_token"]

    pid1, pid2 = str(uuid.uuid4()), str(uuid.uuid4())

    with client.websocket_connect(f"/ws/{code}?role=host&token={host_token}") as host_ws, \
         client.websocket_connect(f"/ws/{code}?role=participant&pid={pid1}") as p1_ws, \
         client.websocket_connect(f"/ws/{code}?role=participant&pid={pid2}") as p2_ws:

        drain(host_ws), drain(p1_ws), drain(p2_ws)
        host_ws.send_json({"type": "host_action", "payload": {"action": "start_project_submission"}})
        drain(host_ws), drain(p1_ws), drain(p2_ws)

        p1_ws.send_json({"type": "submit_project", "payload": {"title": "Projet à modérer", "description": "", "context": "", "image_url": None}})
        h = drain(host_ws)
        project_id = h["state"]["project_phase"]["projects"][0]["id"]
        drain(p1_ws), drain(p2_ws)

        host_ws.send_json({"type": "host_action", "payload": {"action": "close_submission_open_vote"}})
        drain(host_ws), drain(p1_ws), drain(p2_ws)
        host_ws.send_json({"type": "host_action", "payload": {"action": "select_project", "project_id": project_id}})
        drain(host_ws), drain(p1_ws), drain(p2_ws)

        # p1 soumet une contribution -> doit être "pending", invisible pour p2
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "Une proposition en attente de validation"}})
        h, p1_state, p2_state = drain(host_ws), drain(p1_ws), drain(p2_ws)

        check("host voit la contribution en attente (status=pending)", h["state"]["consultation"]["propositions"][0]["status"] == "pending")
        check("l'auteur voit sa propre contribution en attente", any(pr["status"] == "pending" for pr in p1_state["state"]["consultation"]["propositions"]))
        check("un AUTRE participant ne voit PAS la contribution en attente", len(p2_state["state"]["consultation"].get("propositions", [])) == 0)

        prop_id = h["state"]["consultation"]["propositions"][0]["id"]

        # Rejet : ne doit jamais apparaître, pour personne
        host_ws.send_json({"type": "host_action", "payload": {"action": "reject_proposition", "proposition_id": prop_id}})
        h, p2_state = drain(host_ws), drain(p2_ws)
        check("contribution rejetée toujours invisible pour un autre participant", len(p2_state["state"]["consultation"].get("propositions", [])) == 0)

        # Nouvelle contribution, cette fois approuvée (délai pour respecter
        # le cooldown anti-spam entre deux soumissions de la même connexion)
        time.sleep(1.1)
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "Une seconde proposition à approuver"}})
        h = drain(host_ws)
        drain(p1_ws), drain(p2_ws)
        prop_id_2 = next(pr["id"] for pr in h["state"]["consultation"]["propositions"] if pr["status"] == "pending")

        host_ws.send_json({"type": "host_action", "payload": {"action": "approve_proposition", "proposition_id": prop_id_2}})
        h, p2_state = drain(host_ws), drain(p2_ws)
        check("contribution approuvée -> visible pour un autre participant", any(pr["id"] == prop_id_2 for pr in p2_state["state"]["consultation"]["propositions"]))


# ============================================================================
# Scénario validation : contraintes d'entrée (mot de passe, longueur texte)
# ============================================================================

def _run_validation_scenario(client: TestClient) -> None:
    print("\n=== Scénario validation des entrées ===")

    r = create_webinar(client, password="court1")
    check("mot de passe < 8 caractères rejeté (422)", r.status_code == 422)

    r = create_webinar(client, password="motdepasse12")
    check("mot de passe >= 8 caractères accepté", r.status_code == 200)
    code, host_token = r.json()["code"], r.json()["host_token"]

    with client.websocket_connect(f"/ws/{code}?role=host&token={host_token}") as host_ws, \
         client.websocket_connect(f"/ws/{code}?role=participant&pid={uuid.uuid4()}") as p1_ws:
        drain(host_ws), drain(p1_ws)
        host_ws.send_json({"type": "host_action", "payload": {"action": "start_project_submission"}})
        drain(host_ws), drain(p1_ws)
        p1_ws.send_json({"type": "submit_project", "payload": {"title": "Projet test validation", "description": "", "context": "", "image_url": None}})
        h = drain(host_ws)
        project_id = h["state"]["project_phase"]["projects"][0]["id"]
        drain(p1_ws)
        host_ws.send_json({"type": "host_action", "payload": {"action": "close_submission_open_vote"}})
        drain(host_ws), drain(p1_ws)
        host_ws.send_json({"type": "host_action", "payload": {"action": "select_project", "project_id": project_id}})
        drain(host_ws), drain(p1_ws)

        # Cas 1 : longueur brute < 10 -> rejetée par Field(min_length=10)
        # directement (message généré par Pydantic, pas le nôtre).
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "12345678"}})
        p = drain(p1_ws)
        check("contribution de 8 caractères rejetée (error reçue, pas ack)", "error" in p and "ack" not in p)

        # Cas 2 : longueur brute >= 10 mais < 10 après trim (padding
        # d'espaces) -> doit être rattrapée par notre validateur dédié, qui
        # renvoie le message français explicite. (Le cooldown anti-spam
        # s'arme même sur une soumission rejetée : on attend 1.1s.)
        time.sleep(1.1)
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "   123456   "}})
        p = drain(p1_ws)
        check("contribution <10 caractères après trim rejetée avec message dédié", "error" in p and "10 caractères" in p["error"]["message"])

        # Cas 3 : exactement 10 caractères -> doit être acceptée.
        time.sleep(1.1)
        p1_ws.send_json({"type": "submit_proposition", "payload": {"prop_type": "positifs", "texte": "1234567890"}})
        h = drain(host_ws)
        check("contribution de 10 caractères exactement acceptée", len(h["state"]["consultation"]["propositions"]) == 1)


if __name__ == "__main__":
    main()

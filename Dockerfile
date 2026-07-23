# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Étape 1 — construction des dépendances Python dans un environnement isolé
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Étape 2 — image finale, minimale
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# libpq5 : psycopg2-binary embarque normalement déjà sa propre copie de
# libpq dans la wheel, donc cette installation est en principe redondante
# — elle est conservée par prudence (filet de sécurité à faible coût) en
# cas d'usage de PostgreSQL (cf. TUTORIEL_DEPLOIEMENT_SSPCLOUD.md). Pillow
# (généré par qrcode[pil]) n'a besoin d'aucune bibliothèque système
# supplémentaire pour produire du PNG : ses wheels sont autosuffisantes.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app

# Utilisateur non-root (bonne pratique, et requis par de nombreuses
# politiques de sécurité Kubernetes, dont celles de SSPCloud).
RUN useradd --create-home --uid 1000 appuser && \
    mkdir -p /app/data/exports && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/healthz', timeout=3)" || exit 1

# --proxy-headers / --forwarded-allow-ips : nécessaire derrière l'ingress
# Kubernetes de SSPCloud pour que l'application connaisse le bon schéma
# (https) et la bonne adresse d'origine.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]

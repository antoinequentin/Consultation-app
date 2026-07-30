"""
Stockage des images uploadées (§2 / §7 du cahier des charges).

Ce module isole TOUTE la logique de stockage derrière une interface
(`StorageBackend`) afin que le reste de l'application (routers, crud) ne
connaisse jamais le détail de "où" une image est physiquement rangée. Ça
permet de démarrer avec un backend local fonctionnel dès maintenant, puis
de brancher un vrai stockage objet (S3, MinIO, Scaleway...) plus tard en
n'écrivant qu'une nouvelle classe ici — sans toucher aux routers, à crud,
ni au front.

Pourquoi un backend "local" par défaut plutôt que S3 directement :
cet environnement de développement n'a pas d'accès réseau vers un service
de stockage objet externe (pas d'identifiants, pas de connectivité sortante
vers un endpoint S3/MinIO). Le upload RÉEL de fichier (par opposition à
coller une URL d'image externe, ce que l'app permettait jusqu'ici) est
néanmoins livré et pleinement fonctionnel : les fichiers sont acceptés,
validés, stockés sur le disque du serveur, et servis publiquement via
/static/uploads/... — ce qui couvre l'usage réel (un participant/animateur
importe une photo depuis son ordinateur) même si ça ne passe pas par un
bucket S3.

Pour brancher S3/MinIO en production, voir `S3StorageBackend` plus bas :
la classe est délibérément laissée en squelette commenté, avec la liste
exacte des informations à fournir (endpoint, bucket, credentials) et les
3 méthodes à implémenter, plutôt que d'écrire un code qui ne pourrait pas
être testé faute d'un vrai bucket accessible depuis cet environnement.
"""
from __future__ import annotations

import mimetypes
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Protocol

from app.config import settings

# Formats acceptés pour une image de projet. On limite volontairement aux
# formats web usuels (pas de HEIC/TIFF non plus : ils ne s'affichent pas
# nativement dans un <img> de navigateur, ce qui casserait l'aperçu).
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 Mo : large pour une photo de projet,
# raisonnable pour ne pas saturer le disque du serveur en cas d'usage intensif.


class StorageError(Exception):
    """Erreur de validation ou d'écriture, à renvoyer telle quelle au
    client (message déjà rédigé pour être affiché sans reformulation)."""


def _slugify(name: str, *, max_length: int = 40) -> str:
    """Nettoie un nom de fichier fourni par l'utilisateur pour en faire un
    segment d'URL sûr (utilisé uniquement à titre cosmétique dans le nom de
    fichier final ; l'unicité repose sur un uuid4, jamais sur ce slug)."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^a-zA-Z0-9\-_]+", "-", name).strip("-").lower()
    return name[:max_length] or "image"


class StorageBackend(Protocol):
    """Interface minimale que tout backend de stockage doit fournir.

    Trois méthodes seulement, volontairement : c'est tout ce dont
    l'application a besoin pour le cas d'usage "un participant/animateur
    importe une image de projet". Pas de listing, pas de suppression en
    masse, pas de métadonnées custom — on n'ajoute cette surface que si un
    besoin réel apparaît.
    """

    def save(self, *, content: bytes, content_type: str, original_filename: str) -> str:
        """Enregistre le contenu et renvoie l'URL PUBLIQUE (utilisable
        telle quelle dans un <img src="...">) de l'image stockée."""
        ...

    def delete(self, url: str) -> None:
        """Supprime l'image correspondant à une URL précédemment renvoyée
        par `save`. Ne lève pas d'erreur si le fichier n'existe déjà plus
        (suppression idempotente : appeler deux fois de suite ne doit pas
        planter, utile par exemple lors d'un remplacement d'image où l'on
        essaie de nettoyer l'ancienne sans certitude qu'elle existe encore)."""
        ...

    def owns_url(self, url: str) -> bool:
        """Indique si cette URL a été produite par CE backend (permet de
        ne tenter de supprimer que les images gérées par l'app, jamais une
        URL externe qu'un participant aurait collée avant l'introduction de
        l'upload réel — l'app continue d'accepter les deux formes)."""
        ...


class LocalStorageBackend:
    """Stocke les fichiers sur le disque du serveur, sous
    `data/uploads/` (et non `app/static/uploads/` : voir plus bas pourquoi),
    servis publiquement sous `/uploads/...` via un montage StaticFiles dédié
    déclaré dans `main.py`.

    Ce chemin est délibérément EN DEHORS de `app/static/`, pour rester sous
    le volume persistant `/app/data` déjà monté par le déploiement
    Kubernetes/Helm (cf. deploy/kubernetes/deployment.yaml,
    deploy/helm/consultation-app/templates/pvc.yaml) — un chemin sous
    `app/static/` fait partie de l'image Docker elle-même et serait donc
    réinitialisé à chaque redéploiement du pod, y compris en mono-instance
    (ce n'était pas seulement un problème multi-instances, comme documenté
    par erreur dans une version antérieure de ce module).

    Limite résiduelle, désormais uniquement multi-instances : sur un
    déploiement à plusieurs répliques SANS volume partagé entre elles, une
    image uploadée sur l'instance A ne serait pas visible si la requête
    suivante atterrit sur l'instance B — c'est précisément le problème
    qu'un vrai stockage objet (S3/MinIO) résout, d'où l'interface commune
    préparée dans ce module.
    """

    def __init__(self, upload_dir: Path, url_prefix: str = "/uploads"):
        self.upload_dir = upload_dir
        self.url_prefix = url_prefix.rstrip("/")
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def save(self, *, content: bytes, content_type: str, original_filename: str) -> str:
        ext = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
        if ext is None:
            # Filet de sécurité : ne devrait pas arriver si l'appelant a
            # déjà validé via `validate_image`, mais on ne fait jamais
            # confiance à un seul point de contrôle pour une écriture disque.
            guessed = mimetypes.guess_extension(content_type) or ""
            ext = guessed if guessed in ALLOWED_IMAGE_CONTENT_TYPES.values() else ".bin"
        slug = _slugify(Path(original_filename).stem)
        filename = f"{slug}-{uuid.uuid4().hex[:12]}{ext}"
        path = self.upload_dir / filename
        path.write_bytes(content)
        return f"{self.url_prefix}/{filename}"

    def delete(self, url: str) -> None:
        if not self.owns_url(url):
            return
        filename = url.rsplit("/", 1)[-1]
        path = self.upload_dir / filename
        # Idempotent : on ignore l'absence du fichier plutôt que de lever,
        # cf. contrat documenté sur StorageBackend.delete ci-dessus.
        path.unlink(missing_ok=True)

    def owns_url(self, url: str) -> bool:
        return url.startswith(f"{self.url_prefix}/")


# ----------------------------------------------------------------------------
# Point d'extension : stockage objet externe (S3, MinIO, Scaleway Object
# Storage, etc.), pour un déploiement multi-instances sans volume partagé.
#
# Non implémenté ici : cet environnement de développement n'a pas de
# connectivité réseau sortante vers un endpoint S3/MinIO ni d'identifiants
# à utiliser, donc tout code écrit "à l'aveugle" ne pourrait pas être testé
# et risquerait de contenir des erreurs non détectées (mauvais nom de
# paramètre boto3, mauvaise politique de bucket, etc.) — moins utile qu'un
# squelette clair à compléter par quelqu'un qui a accès au bucket réel.
#
# Pour l'implémenter, il faut typiquement :
#   1. Un bucket S3/MinIO existant, avec une politique autorisant la
#      lecture publique des objets sous un préfixe donné (ex: uploads/).
#   2. Des identifiants (access key / secret key) ou un rôle IAM, fournis
#      via variables d'environnement (jamais en dur dans le code) :
#      S3_ENDPOINT_URL, S3_BUCKET_NAME, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
#      S3_PUBLIC_BASE_URL (l'URL publique de base si différente de
#      S3_ENDPOINT_URL, ex: derrière un CDN).
#   3. La dépendance `boto3` (compatible S3 et MinIO, qui expose une API
#      S3), ajoutée à requirements.txt.
#
# class S3StorageBackend:
#     def __init__(self):
#         import boto3
#         self._client = boto3.client(
#             "s3",
#             endpoint_url=os.environ["S3_ENDPOINT_URL"],
#             aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
#             aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
#         )
#         self._bucket = os.environ["S3_BUCKET_NAME"]
#         self._public_base = os.environ.get("S3_PUBLIC_BASE_URL", os.environ["S3_ENDPOINT_URL"])
#
#     def save(self, *, content: bytes, content_type: str, original_filename: str) -> str:
#         ext = ALLOWED_IMAGE_CONTENT_TYPES.get(content_type, ".bin")
#         key = f"uploads/{uuid.uuid4().hex}{ext}"
#         self._client.put_object(
#             Bucket=self._bucket, Key=key, Body=content,
#             ContentType=content_type, ACL="public-read",
#         )
#         return f"{self._public_base.rstrip('/')}/{self._bucket}/{key}"
#
#     def delete(self, url: str) -> None:
#         if not self.owns_url(url):
#             return
#         key = url.split(f"/{self._bucket}/", 1)[-1]
#         self._client.delete_object(Bucket=self._bucket, Key=key)
#
#     def owns_url(self, url: str) -> bool:
#         return f"/{self._bucket}/" in url and url.startswith(self._public_base)
#
# Puis, dans get_storage_backend() ci-dessous, ajouter :
#     if settings.STORAGE_BACKEND == "s3":
#         return S3StorageBackend()


def validate_image(*, content: bytes, content_type: str) -> None:
    """Lève `StorageError` (message déjà utilisateur-facing) si l'image ne
    respecte pas les contraintes de taille/format. À appeler AVANT tout
    appel à `save()`, dans le routeur, pour renvoyer une erreur 400 claire
    plutôt que de laisser une écriture disque échouer plus tard."""
    if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_CONTENT_TYPES))
        raise StorageError(f"Format d'image non pris en charge ({content_type}). Formats acceptés : {allowed}.")
    if len(content) == 0:
        raise StorageError("Le fichier envoyé est vide.")
    if len(content) > MAX_IMAGE_BYTES:
        raise StorageError(f"Image trop volumineuse ({len(content) // 1024} Ko). Taille maximale : {MAX_IMAGE_BYTES // 1024} Ko.")


_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """Point d'entrée unique utilisé par le reste de l'application.
    Sélectionne le backend selon `settings.STORAGE_BACKEND` (variable
    d'environnement `STORAGE_BACKEND`, défaut "local"). Mémorisé après le
    premier appel : pas besoin de reconstruire le backend à chaque requête."""
    global _backend
    if _backend is None:
        backend_name = getattr(settings, "STORAGE_BACKEND", "local")
        if backend_name == "local":
            _backend = LocalStorageBackend(settings.DATA_DIR / "uploads")
        else:
            # "s3" (ou toute autre valeur) demandé mais non implémenté dans
            # cet environnement : on échoue explicitement au démarrage de
            # la première requête d'upload plutôt que de retomber
            # silencieusement sur le stockage local, ce qui masquerait une
            # mauvaise configuration en production.
            raise RuntimeError(
                f"STORAGE_BACKEND={backend_name!r} n'est pas implémenté. "
                "Voir le squelette S3StorageBackend documenté dans app/storage.py."
            )
    return _backend

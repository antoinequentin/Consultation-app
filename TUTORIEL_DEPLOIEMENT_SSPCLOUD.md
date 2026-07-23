# Déployer Boussole sur SSPCloud

Ce tutoriel adapte [le tutoriel officiel InseeFrLab de déploiement d'une
application Shiny](https://github.com/InseeFrLab/sspcloud-tutorials/blob/main/deployment/shiny-app.md)
à une application **FastAPI + WebSocket**. Le tutoriel d'origine précise
lui-même que sa méthode se généralise facilement à d'autres frameworks
Python (Flask, Streamlit...) : c'est exactement ce qu'on fait ici.

Différence principale avec le tutoriel Shiny : il n'existe pas de chart Helm
« générique Python » maintenu par InseeFrLab comme il en existe un pour
Shiny (`shiny`, qui encapsule shiny-server). On écrit donc notre propre
chart minimal (fourni dans `deploy/helm/consultation-app/`), qui suit
exactement les mêmes principes (Deployment + Service + Ingress, secrets
pour les informations sensibles). Une alternative encore plus simple, sans
Helm, est aussi fournie dans `deploy/kubernetes/` — c'est le même genre de
manifestes « bruts » qu'utilise par exemple la formation MLOps d'InseeFrLab
pour déployer une API FastAPI sur SSPCloud.

## Sommaire

1. [Prérequis](#1-prérequis)
2. [Vue d'ensemble](#2-vue-densemble)
3. [Construire et publier l'image Docker](#3-construire-et-publier-limage-docker)
4. [Ouvrir un terminal avec accès Kubernetes sur SSPCloud](#4-ouvrir-un-terminal-avec-accès-kubernetes-sur-sspcloud)
5. [Créer le Secret Kubernetes](#5-créer-le-secret-kubernetes)
6. [Option : PostgreSQL au lieu de SQLite](#6-option--postgresql-au-lieu-de-sqlite)
7. [Déployer avec Helm (méthode recommandée)](#7-déployer-avec-helm-méthode-recommandée)
8. [Alternative : déployer avec kubectl (sans Helm)](#8-alternative--déployer-avec-kubectl-sans-helm)
9. [Vérifier le déploiement et accéder à l'application](#9-vérifier-le-déploiement-et-accéder-à-lapplication)
10. [Déboguer un déploiement](#10-déboguer-un-déploiement)
11. [Mettre à jour l'application](#11-mettre-à-jour-lapplication)
12. [Passer à l'échelle (plusieurs réplicas)](#12-passer-à-léchelle-plusieurs-réplicas)
13. [Déploiement continu avec ArgoCD (pour aller plus loin)](#13-déploiement-continu-avec-argocd-pour-aller-plus-loin)

---

## 1. Prérequis

- Un compte sur [SSPCloud](https://datalab.sspcloud.fr) (adresse professionnelle).
- Un compte [Docker Hub](https://hub.docker.com) (gratuit).
- Un dépôt Git (GitHub par exemple) contenant le code de ce projet.
- Avoir testé l'application en local au moins une fois (voir `README.md`,
  section "Démarrage local") pour s'assurer que le code de départ fonctionne.

## 2. Vue d'ensemble

Le déploiement se déroule en trois grandes phases, identiques dans
l'esprit au tutoriel Shiny :

1. **Construire une image Docker** de l'application et la publier sur
   Docker Hub (automatisé par GitHub Actions, `.github/workflows/ci.yaml`).
2. **Préparer la configuration Kubernetes** : un Secret pour les valeurs
   sensibles (clé de signature, éventuellement les identifiants de la base
   de données), puis le chart Helm (ou les manifestes bruts) qui décrivent
   comment faire tourner cette image sur le cluster.
3. **Déployer** depuis un terminal disposant des droits Kubernetes sur
   SSPCloud, en lançant un service VSCode (ou tout autre service avec
   terminal) avec le rôle Kubernetes "admin" du namespace.

Contrairement à l'application Shiny d'origine (qui stockait son état dans
des fichiers `.rds` sur le disque du pod), Boussole stocke ses données dans
une vraie base (SQLite par défaut, PostgreSQL en option) et communique en
temps réel par WebSocket. Deux conséquences pour le déploiement :

- Si vous restez en SQLite, le fichier de base de données doit survivre aux
  redémarrages du pod : on monte un volume persistant (`PersistentVolumeClaim`)
  sur `/app/data`. C'est déjà configuré dans le chart fourni.
- L'Ingress doit autoriser les connexions WebSocket de longue durée (le
  timeout par défaut de nginx est de 60 secondes, ce qui couperait les
  connexions des participants pendant un webinaire). Le chart fourni inclut
  déjà les annotations nécessaires (`proxy-read-timeout`, etc.).
- Le conteneur tourne en utilisateur non-root (uid 1000, bonne pratique
  de sécurité). Pour qu'il puisse malgré tout écrire sur le volume
  persistant monté, le chart et les manifestes fournis déclarent
  `securityContext.fsGroup: 1000` au niveau du pod — sans quoi un volume
  fraîchement provisionné appartiendrait par défaut à `root` sur la
  plupart des fournisseurs de stockage Kubernetes, empêchant l'écriture
  de la base au premier démarrage.

## 3. Construire et publier l'image Docker

### Option A — Automatique (recommandé)

Le workflow `.github/workflows/ci.yaml` construit et publie l'image à
chaque push sur `main`. Il vous suffit de :

1. Créer un [token d'accès Docker Hub](https://hub.docker.com/settings/security).
2. Dans votre dépôt GitHub : **Settings → Secrets and variables → Actions**,
   ajouter deux secrets :
   - `DOCKERHUB_USERNAME` : votre identifiant Docker Hub.
   - `DOCKERHUB_TOKEN` : le token créé à l'étape précédente.
3. Pousser votre code sur `main`. Le workflow exécute d'abord les tests
   d'intégration (`tests/smoke_test.py`), puis construit et publie l'image
   sous `VOTRE_PSEUDO_DOCKERHUB/consultation-bte-app:latest`.

### Option B — Manuelle

```bash
docker build -t VOTRE_PSEUDO_DOCKERHUB/consultation-bte-app:latest .
docker login
docker push VOTRE_PSEUDO_DOCKERHUB/consultation-bte-app:latest
```

## 4. Ouvrir un terminal avec accès Kubernetes sur SSPCloud

1. Connectez-vous sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr).
2. Lancez un service **VSCode** (ou RStudio/Jupyter, peu importe — on a
   juste besoin d'un terminal) depuis le catalogue de services.
3. **Important** : dans l'onglet **Kubernetes** des paramètres de
   lancement du service, sélectionnez le rôle **`admin`** pour le
   namespace. Sans ce rôle, vous n'aurez pas les droits nécessaires pour
   créer les ressources Kubernetes du déploiement.
4. Une fois le service démarré, ouvrez un terminal à l'intérieur (menu
   *Terminal → New Terminal* dans VSCode).
5. Clonez votre dépôt :

   ```bash
   git clone https://github.com/VOTRE_COMPTE/consultation-bte-app.git
   cd consultation-bte-app
   ```

## 5. Créer le Secret Kubernetes

Avant de déployer, créez le Secret qui contiendra la clé de signature des
sessions animateur (ne la mettez jamais en clair dans `values.yaml` ou dans
un fichier commité) :

```bash
kubectl create secret generic boussole-secrets \
  --from-literal=SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

Vérifiez qu'il a bien été créé :

```bash
kubectl get secret boussole-secrets
```

## 6. Option : PostgreSQL au lieu de SQLite

Par défaut, Boussole utilise SQLite (un simple fichier, monté sur un
volume persistant) : c'est suffisant pour un usage courant (un webinaire à
la fois, quelques centaines de participants). Si vous prévoyez plusieurs
réplicas de l'application ou une charge plus importante, provisionnez un
PostgreSQL :

1. Sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr), ouvrez le
   catalogue de services et lancez le service **PostgreSQL**.
2. Une fois démarré, le service affiche les informations de connexion
   (hôte, port, utilisateur, mot de passe, nom de la base).
3. Ajoutez `DATABASE_URL` à votre Secret :

   ```bash
   kubectl create secret generic boussole-secrets \
     --from-literal=SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
     --from-literal=DATABASE_URL="postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME"
   ```

4. Dans `deploy/helm/consultation-app/values.yaml`, mettez
   `persistence.enabled: false` (plus besoin du volume SQLite).

> **Alternative encore plus rapide, sans kubectl/Helm** : le catalogue de
> services SSPCloud (interface Onyxia) propose une entrée **"Custom Docker
> Image"** qui permet de renseigner l'image, le port, les ressources, la
> persistance et les variables d'environnement directement depuis un
> formulaire web, sans écrire aucun manifeste. C'est une bonne option pour
> un premier essai rapide ou une démonstration ponctuelle ; les sections
> suivantes (Helm / kubectl) restent la méthode recommandée pour un
> déploiement durable, reproductible et versionné avec le code.

## 7. Déployer avec Helm (méthode recommandée)

1. Adaptez `deploy/helm/consultation-app/values.yaml` :
   - `image.repository` : `VOTRE_PSEUDO_DOCKERHUB/consultation-bte-app`
   - `ingress.host` : un sous-domaine de la forme `*.lab.sspcloud.fr`
     (ex. `consultation-bte.lab.sspcloud.fr` — choisissez un nom assez
     spécifique pour éviter les collisions avec d'autres utilisateurs).
   - `env.PUBLIC_BASE_URL` : `https://` + le même nom de domaine.

2. Installez le chart :

   ```bash
   helm install boussole deploy/helm/consultation-app
   ```

3. Pour les mises à jour ultérieures (après modification de `values.yaml`
   ou nouvelle version de l'image) :

   ```bash
   helm upgrade boussole deploy/helm/consultation-app
   ```

## 8. Alternative : déployer avec kubectl (sans Helm)

Pour un premier essai rapide, ou si vous préférez des manifestes simples à
lire et modifier directement (à la manière des formations InseeFrLab) :

1. Éditez `deploy/kubernetes/deployment.yaml` (nom de l'image) et
   `deploy/kubernetes/ingress.yaml` (nom de domaine).
2. Appliquez les trois manifestes :

   ```bash
   kubectl apply -f deploy/kubernetes/
   ```
3. Pour mettre à jour après modification : relancez la même commande
   (`kubectl apply` est idempotent).

## 9. Vérifier le déploiement et accéder à l'application

```bash
kubectl get pods
kubectl get ingress
```

L'URL de votre application est `https://<le host choisi>`. Donnez-lui
quelques dizaines de secondes après le tout premier déploiement (temps que
le pod démarre et que le certificat/DNS se propage).

Trois points d'entrée à connaître une fois l'application en ligne :

- `https://votre-domaine/` : page d'accueil (créer/rejoindre un webinaire).
- `https://votre-domaine/w/{code}/host` : console animateur.
- `https://votre-domaine/w/{code}/projector` : écran de projection,
  à afficher sur un vidéoprojecteur pendant le webinaire.

## 10. Déboguer un déploiement

Comme dans le tutoriel Shiny d'origine, deux grandes familles de problèmes :

**a) L'erreur vient de l'application elle-même** (le pod démarre puis
plante, ou ne passe jamais "Ready") :

```bash
kubectl get pods                      # repérer le nom du pod en erreur
kubectl describe pod <nom_du_pod>      # évènements (image introuvable, OOM...)
kubectl logs <nom_du_pod>              # logs applicatifs (tracebacks Python)
kubectl exec -it <nom_du_pod> -- bash  # inspecter le conteneur de l'intérieur
```

Corrigez le code, poussez sur `main` (l'image se reconstruit
automatiquement), puis :

```bash
helm upgrade boussole deploy/helm/consultation-app
# ou, sans Helm :
kubectl rollout restart deployment/boussole
```

**b) L'erreur vient de la configuration du déploiement** (mauvais nom de
secret, mauvais tag d'image, indentation incorrecte dans `values.yaml`...) :
pas besoin de reconstruire l'image, corrigez le chart/les manifestes puis
réappliquez (`helm upgrade` ou `kubectl apply -f deploy/kubernetes/`).

**c) Spécifique à cette application — la page se charge mais rien ne se
met à jour en direct** : vérifiez que l'Ingress porte bien les annotations
`proxy-read-timeout`/`proxy-send-timeout` (sans elles, nginx coupe les
connexions WebSocket au bout de 60 secondes) :

```bash
kubectl get ingress boussole -o yaml | grep proxy-
```

## 11. Mettre à jour l'application

1. Modifiez le code, committez, poussez sur `main` → l'image Docker se
   reconstruit automatiquement (tag `latest`).
2. Redéployez :

   ```bash
   helm upgrade boussole deploy/helm/consultation-app
   # ou
   kubectl rollout restart deployment/boussole
   ```

Pour un suivi plus rigoureux en production, préférez des tags de version
explicites (`v1.1.0`) plutôt que `latest`, et mettez à jour
`image.tag` dans `values.yaml` à chaque nouvelle version.

## 12. Passer à l'échelle (plusieurs réplicas)

Pour un très grand webinaire (au-delà de quelques centaines de
participants), vous pouvez augmenter `replicaCount` dans `values.yaml`.
**Condition impérative** : utilisez PostgreSQL (section 6) et désactivez
`persistence` — un fichier SQLite local ne peut pas être partagé en
écriture cohérente entre plusieurs pods. Les connexions WebSocket, elles,
n'ont pas besoin d'affinité de session particulière : chaque pod
recalcule l'état à partir de la base de données partagée à chaque
diffusion.

## 13. Déploiement continu avec ArgoCD (pour aller plus loin)

SSPCloud propose un service **ArgoCD** dans son catalogue, qui permet de
synchroniser automatiquement le cluster avec le contenu du dossier
`deploy/kubernetes/` (ou `deploy/helm/`) de votre dépôt Git à chaque
commit, sans avoir à relancer `helm upgrade`/`kubectl apply` manuellement.
Le principe (détaillé dans la formation MLOps d'InseeFrLab) :

1. Lancez le service **ArgoCD** depuis le catalogue SSPCloud.
2. Créez une application ArgoCD pointant vers votre dépôt Git et le
   chemin `deploy/kubernetes` (ou `deploy/helm/consultation-app`).
3. Chaque `git push` sur ce dossier déclenche automatiquement un nouveau
   déploiement.

Cette étape est optionnelle : le déploiement manuel décrit ci-dessus
fonctionne très bien pour commencer.

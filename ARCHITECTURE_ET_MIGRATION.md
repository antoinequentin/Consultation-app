# Architecture et notes de migration (Shiny → FastAPI/WebSocket)

## Note sur la reprise du code d'origine

*(Mise à jour après un audit de vérification approfondi — voir la section
"Vérification approfondie" en fin de document. Une première tentative
d'extraction du dépôt Git avait laissé croire, à tort, que l'historique
fourni était partiel : en réalité, `git show HEAD:...` sur chaque fichier
montre que **l'intégralité du code source final était bien présente**
dans l'archive, `ui.R`, `server.R` (1725 lignes), `data.R` et `main.R`
inclus. Cette correction n'a pas remis en cause la logique métier déjà
implémentée — construite sur un commit intermédiaire quasi identique —
mais elle a permis, lors de l'audit, de comparer le comportement de cette
réécriture au code source RÉEL et complet plutôt qu'à une version
intermédiaire, ce qui a révélé plusieurs écarts fins corrigés depuis.)*

La combinaison du code source complet (`server.R`, `ui.R`, `data.R`,
`main.R`, `consultation_utils.R`) et de la documentation du projet
(`GUIDE_COMPLET_V3.md`, `RESUME_MODIFICATIONS.md`, `DEPLOYMENT_SSPCLOUD.md`)
a permis de reconstituer fidèlement l'ensemble des fonctionnalités, du
modèle de données et de la logique métier de l'application d'origine. La
présente réécriture s'appuie donc sur le code source réel pour tout ce qui
concerne la logique métier (système de propositions/votes à la Polis,
cotation, référentiel de la Boussole de la Transition Écologique,
modération, état d'avancement) — et non sur une simple lecture de la
documentation.

## Vue d'ensemble de l'application d'origine

Une application Shiny mono-projet, structurée en package R
(`myshinyapp`), pour animer une consultation citoyenne sur **un seul
projet fixe** (l'exemple fourni portait sur une rénovation thermique et
l'extension d'un parc), déroulée en 4 étapes séquentielles (impacts
positifs → impacts négatifs → vote de cotation → améliorations), avec un
panneau animateur protégé par mot de passe. L'état était stocké dans des
fichiers `.rds` sur le disque du pod, et les clients étaient synchronisés
par **sondage** (`reactiveFileReader`, toutes les 1 à 5 secondes selon le
type de donnée) plutôt que par poussée d'évènements.

## Ce qui a changé

| Aspect | Application d'origine (Shiny) | Boussole (FastAPI) |
|---|---|---|
| Temps réel | Sondage périodique (polling), 1 à 5 s de latence | Diffusion WebSocket en push, quasi instantanée |
| Projets | Un seul projet fixe, codé en dur | Plusieurs projets, proposés par les participants et départagés par un vote |
| Stockage | Fichiers `.rds` sur le disque du pod | Base relationnelle (SQLite par défaut, PostgreSQL en option), avec contraintes d'unicité pour empêcher les votes en double |
| Authentification animateur | Mot de passe unique, partagé par variable d'environnement pour tout le serveur | Mot de passe propre à chaque webinaire, haché (bcrypt), jeton de session signé et expirant |
| Sessions | Une seule consultation à la fois sur le serveur | Plusieurs webinaires indépendants en parallèle, chacun avec son code court |
| Vue publique des résultats | Résultats visibles uniquement dans le flux normal de la consultation | Écran de projection dédié, plein écran, pensé pour un vidéoprojecteur |
| Modération | Suppression/réinitialisation a posteriori | Idem, plus une file de validation a priori optionnelle (activable par webinaire) |
| Déploiement | Conteneur unique R/Shiny | Conteneur unique Python (mêmes principes de déploiement SSPCloud) |

## Pourquoi WebSocket plutôt que du polling plus rapide ?

Le dernier commit de l'application d'origine portait justement sur une
réduction de la fréquence de sondage pour absorber la charge à partir de
~100 utilisateurs simultanés (`Perf: Reduce refresh rate for 100+ users`)
— un compromis qui dégrade la réactivité précisément quand l'audience est
la plus grande. Le passage à une diffusion WebSocket en push supprime ce
compromis : chaque client ne reçoit un message que lorsque l'état change
réellement, et les diffusions rapprochées dans le temps sont regroupées
côté serveur (fenêtre de 150 ms, voir `app/websocket_manager.py`) pour
absorber les rafales (ex. cent votes simultanés) sans recalculer l'état à
chaque évènement individuel.

## Modèle de données

`Webinar` (une session) → `Project` (un ou plusieurs, proposés ou
pré-définis) → `Axis` (un ou plusieurs axes de discussion par projet,
équivalent direct des « questions » de l'application d'origine) →
`Proposition` (impact positif/négatif/amélioration) → `PropositionVote`
(accord/désaccord/passer) ; et séparément `CotationResponse`
(favorable/neutre/défavorable) et `ProjectVote` (vote de sélection du
projet). Le détail des champs est commenté dans `app/models.py`.

## Machine à états

Le webinaire suit une machine à états à deux niveaux (voir
`app/state_machine.py`) :

```
LOBBY → PROJECT_SUBMISSION → PROJECT_VOTE → CONSULTATION → ENDED
                                                 │
                                    (par axe) POSITIFS → NEGATIFS → VOTE → AMELIORATIONS
```

Chaque transition est déclenchée par une action animateur (`host_action`)
et validée côté serveur (impossible, par exemple, de voter une cotation
en dehors de l'étape correspondante) — la même validation protège aussi
contre un client qui enverrait des messages WebSocket fabriqués à la
main.

## Pourquoi pas React/Vue côté client ?

Le client (participant, animateur, projection) est en HTML/CSS/JS natifs,
rendu côté serveur (Jinja2) puis mis à jour dynamiquement par les messages
WebSocket reçus. Ce choix garde l'application déployable comme **un seul
service Python**, sans étape de build front séparée, ce qui correspond à
la demande initiale (« FastAPI + WebSocket ») et simplifie le
déploiement SSPCloud (une seule image Docker). Pour un projet appelé à
grandir significativement côté interface, un passage à un framework front
(React, Vue) resterait tout à fait possible sans toucher à l'API/WebSocket
sous-jacente, qui est déjà découplée du rendu.

## Vérification approfondie

Après la livraison initiale, une relecture comparative ligne à ligne du
code source complet de l'application d'origine (voir la correction
ci-dessus) a été menée, ainsi qu'un audit statique et fonctionnel complet
de cette réécriture. Cette section liste les écarts trouvés et la façon
dont ils ont été traités, par souci de transparence.

### Écarts de fidélité corrigés

Ces points s'écartaient du comportement de l'application d'origine sans
justification volontaire ; ils ont été alignés sur le code source réel :

- **Longueur minimale des contributions** : l'original exige
  `nchar(trimws(texte)) >= 10` ; cette réécriture n'imposait que 2
  caractères. Corrigé à 10, avec un message dédié si le texte ne fait
  plus 10 caractères après suppression des espaces superflus.
- **Formule de consensus** : l'original calcule
  `accord / (accord + désaccord + passer) * 100` (un "passer" dilue donc
  le score) ; cette réécriture excluait "passer" du dénominateur, ce qui
  changeait silencieusement les pourcentages affichés. Corrigé pour
  reprendre exactement la formule d'origine.
- **Tri des propositions** : l'original trie systématiquement par nombre
  d'accord décroissant (`arrange(desc(accord))`) ; cette réécriture
  triait par ordre chronologique. Corrigé.
- **Référentiel de la Boussole de la Transition Écologique** : l'original
  définit 6 dimensions officielles fixes (ADAPTATION, ATTÉNUATION,
  RESSOURCE EN EAU, BIODIVERSITÉ, POLLUTION, ÉCONOMIE CIRCULAIRE), chacune
  avec une couleur propre, utilisées comme grille de lecture systématique
  du projet étudié. La première version de cette réécriture les avait
  remplacées par un unique axe générique. Corrigé : ces 6 axes (texte et
  couleur d'origine) sont désormais proposés par défaut pour tout projet
  retenu ; l'animateur peut toujours en ajouter d'autres.
- **Bouton "Modérer" une proposition** : l'original permet de remettre à
  zéro les votes d'une proposition précise (texte conservé). Cette action
  existait déjà côté serveur dans cette réécriture mais n'était reliée à
  aucun bouton dans la console animateur — elle était donc inutilisable.
  Corrigé : le bouton "🔄 Modérer" est désormais présent à côté de
  "Supprimer" dans le panneau de consultation.
- **Granularité de l'export** : l'original exporte un `votes.csv` avec
  une ligne par vote individuel (participant, proposition, valeur). Cette
  réécriture n'exportait que des compteurs agrégés par proposition.
  Corrigé : un `votes_propositions.csv` détaillé a été ajouté, et
  `cotations.csv` inclut désormais l'identifiant du participant.
- **Champ silencieusement manquant** : `max_propositions_per_participant`
  était lu par le script du participant mais jamais transmis par le
  serveur (un repli codé en dur masquait l'absence). Corrigé.
- **Nom de produit incohérent** : le titre d'onglet du navigateur utilisait
  par défaut "Consultation Citoyenne" alors que toute l'identité visuelle
  (logo, palette, cadran) est bâtie autour du nom "Boussole". Corrigé.

### Bug fonctionnel corrigé (indépendant de la fidélité à l'original)

En écrivant un scénario de test pour la file de modération, un bug plus
profond est apparu : le mécanisme de diffusion WebSocket calculait un état
"participant" **unique, partagé par tout le monde**, avec un identifiant de
participant nul. Un auteur ne pouvait donc jamais voir sa propre
contribution en attente de modération une fois la diffusion suivante
arrivée (quelques centaines de millisecondes après l'envoi) — elle
semblait disparaître jusqu'à validation par l'animateur. Corrigé en
personnalisant, pour chaque connexion, la visibilité des propositions à
partir d'une liste déjà récupérée une seule fois par diffusion (donc sans
requête base de données supplémentaire par participant, un point important
pour la tenue en charge avec de nombreux participants connectés).

### Améliorations de sécurité et d'infrastructure

- **Mot de passe animateur** : seuil relevé de 4 à 8 caractères ; la page
  de création recommande désormais 12 caractères ou plus (l'original
  documentait cette même recommandation sans la vérifier dans le code).
- **`securityContext.fsGroup`** : absent des manifestes Kubernetes /
  Helm alors que le conteneur tourne en utilisateur non-root (uid 1000).
  Sans cela, un volume persistant fraîchement créé (pour la base SQLite)
  est généralement monté avec des droits `root:root` par un grand nombre
  de fournisseurs de stockage Kubernetes, ce qui aurait empêché
  l'application d'écrire sa base de données au tout premier démarrage.
  Ajouté au chart Helm et au manifeste Kubernetes brut.

### Différences assumées, documentées mais volontairement conservées

Ces écarts par rapport à l'original sont des choix de conception
délibérés (déjà en grande partie documentés plus haut dans ce fichier),
listés ici explicitement pour qu'ils ne soient pas confondus avec des
oublis :

- **Vote de cotation modifiable** : l'original fige le choix
  favorable/neutre/défavorable dès la première réponse (écran "Merci"
  définitif). Cette réécriture permet de changer d'avis à tout moment.
  Argument pour ce choix : corriger un clic accidentel ; argument contre :
  un vote plus facilement influençable par les résultats déjà affichés.
  Facilement réversible si la fidélité stricte est préférée.
- **File de modération a priori** (statut "en attente" avant publication)
  : fonctionnalité entièrement nouvelle, l'original n'ayant que des
  outils de modération a posteriori (suppression, remise à zéro des
  votes — ceux-ci ont été conservés à l'identique).
- **Quota de contributions par participant et délai anti-spam (1 s)
  entre deux envois** : l'original n'imposait aucune limite de ce type.
  Ajouts jugés raisonnables pour un outil ouvert à un large public, sans
  équivalent dans le code source d'origine.
- **Trois URLs séparées** (participant / animateur / projection) au lieu
  d'une page unique à onglets : changement architectural assumé, cohérent
  avec la demande d'un véritable poste de pilotage dédié et d'un écran de
  projection, qui n'existaient pas dans l'original.

### Limites de cette vérification

Le bac à sable utilisé pour cet audit n'a accès qu'à une liste restreinte
de domaines réseau (dépôts de paquets pypi/npm/apt, GitHub) ; Docker Hub
n'en fait pas partie. Il n'a donc pas été possible de réellement construire
l'image Docker (`docker build` / `podman build` échouent dès le
téléchargement de l'image de base `python:3.12-slim`). Le `Dockerfile` a
en revanche été relu ligne à ligne, passé au travers d'un linter
(`dockerfilelint`), et son unique avertissement a été identifié comme un
faux positif connu (syntaxe `HEALTHCHECK` multi-lignes), vérifié
manuellement en reproduisant la logique du shell attendue. La résolution
des chemins de fichiers (templates, statiques, base de données) a été
vérifiée par un raisonnement explicite sur `WORKDIR`/`COPY`, cohérent avec
le comportement déjà observé lors des tests en conditions réelles menés en
dehors de Docker. De même, le chart Helm n'a pas pu être passé dans
`helm template` (binaire non installable dans cet environnement) ; sa
validité a été établie par vérification manuelle de l'indentation produite
par chaque usage de `nindent`, combinée à la validation JSON Schema
implicite du manifeste Kubernetes brut équivalent (`deploy/kubernetes/`),
qui lui est directement exécutable et a été validé comme document YAML.
Il est recommandé de faire un `helm template`/`helm lint` de contrôle,
ainsi qu'un premier déploiement sur un cluster de test, avant une mise en
production.


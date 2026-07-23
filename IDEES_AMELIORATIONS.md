# Idées et pistes d'amélioration

Au-delà de ce qui a été implémenté (multi-projets, vote de sélection,
résultats en direct, console animateur, modération), voici des pistes
classées par effort, pour la suite.

## Rapides à ajouter

- **Minuteur par étape** : un compte à rebours configurable par
  l'animateur, visible de tous (participant + projection), pour rythmer
  le webinaire sans avoir à surveiller l'horloge.
- **Réactions éphémères** (👍/❤️/😮 flottant à l'écran) sur l'écran de
  projection, pour donner un retour d'ambiance en direct sans passer par
  un vote formel — beaucoup d'outils de webinaire grand public l'utilisent
  pour maintenir l'engagement.
- **Épingler une contribution** : l'animateur met en avant une
  proposition pendant qu'il en discute à l'oral ; elle s'affiche en grand
  sur l'écran de projection.
- **Dupliquer un webinaire** : réutiliser le ou les projets/axes d'un
  webinaire précédent comme point de départ d'un nouveau (utile pour une
  série de réunions sur le même sujet, par exemple une tournée
  territoriale).

## Moyen terme

- **Upload d'image réel** pour les projets (actuellement un simple champ
  URL) : en s'appuyant sur le stockage S3/MinIO du catalogue SSPCloud,
  comme le fait déjà l'application Shiny d'origine pour ses propres
  données (`shiny.s3.enabled` dans le tutoriel de référence).
- **Page de résultats publique post-événement** : une URL pérenne,
  consultable après la fin du webinaire (au-delà de l'export CSV destiné
  à l'animateur), pour la transparence et la communication a posteriori.
- **Tableau de bord multi-webinaires** pour une organisation : actuellement,
  chaque webinaire est géré indépendamment via son propre mot de passe ;
  un compte « organisateur » permettrait de retrouver tous ses webinaires
  passés et leurs résultats au même endroit.
- **Limitation anti-abus renforcée** : un participant est identifié par un
  jeton stocké dans son navigateur (suffisant pour un usage normal), mais
  rien n'empêche techniquement quelqu'un de voter plusieurs fois en
  rouvrant une fenêtre privée. Pour un webinaire très ouvert au public,
  ajouter une limitation complémentaire par adresse IP, voire une
  vérification légère (ex. code envoyé par e-mail) serait pertinent.

## Plus structurant

- **Analyse de clusters d'opinion façon Polis** : sur les votes
  accord/désaccord, une analyse en composantes principales permettrait
  d'identifier des groupes de participants aux opinions proches, et de
  repérer les propositions qui font consensus *au-delà* des clivages —
  c'est l'apport le plus distinctif de l'outil [Polis](https://pol.is/)
  dont s'inspire le mécanisme de vote de cette application. Demande un
  peu de calcul (numpy/scikit-learn) et une visualisation dédiée.
- **Nuage de mots** des contributions sur l'écran de projection, pour un
  visuel immédiat des thèmes qui reviennent le plus, en complément des
  listes triées par consensus.
- **Tests de charge** (k6, Locust) avant un webinaire à forte audience
  attendue, pour valider le dimensionnement des ressources Kubernetes
  (CPU/mémoire dans `values.yaml`) en conditions réelles.
- **API publique en lecture seule** (résultats agrégés et anonymisés) pour
  permettre une réutilisation Open Data des consultations.

## Conformité et gouvernance

- **RGPD** : informer clairement les participants sur les données
  collectées (texte des contributions, pseudonyme éventuel) et leur durée
  de conservation ; prévoir un mécanisme de suppression sur demande. Les
  identifiants participants étant déjà pseudonymes par défaut (pas de
  collecte d'e-mail ni de nom obligatoire), la base est saine, mais une
  mention explicite sur la page d'accueil reste recommandée pour un usage
  en administration publique.
- **Accessibilité (RGAA)** : l'interface utilise déjà une structure
  sémantique et des contrastes conformes aux standards de l'État, mais un
  audit RGAA complet (navigation clavier exhaustive, lecteurs d'écran sur
  les graphiques de résultats) serait nécessaire avant une mise en
  production grand public dans un cadre administratif.

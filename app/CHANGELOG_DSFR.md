# Audit DSFR strict + mise en évidence des axes — journal des changements

Comparaison faite contre le vrai code source du DSFR (package npm
`@gouvfr/dsfr@1.15.1`), pas contre des résumés de documentation.

## 1. Rayons de bordure (`--radius`, `--radius-sm`)

Le DSFR strict n'utilise **aucun rayon** sur ses composants de base
(`.fr-card`, `.fr-btn`, `.fr-tile` : `border-radius: 0`). Nos deux
variables globales étaient à `14px` / `8px` — un écart systémique
puisque tout le CSS de l'app (cartes, boutons, modales, champs, panneaux
projet…) est piloté par ces variables.

**Changement** : `--radius: 0` et `--radius-sm: 0`. Comme tout le CSS
consomme déjà ces variables (aucune valeur codée en dur trouvée), la
correction se propage automatiquement à `style.css` et `pages.css` sans
toucher à des dizaines de lignes une par une.

Exceptions volontaires, conservées :
- `--radius-pill: 999px` — pour les vraies pilules (compteurs, switches,
  barres de progression), cohérent avec `.fr-tag` du DSFR (pilule).
- `--radius-badge: 0.25rem` (nouvelle variable) — reprend exactement
  `.fr-badge { border-radius: 0.25rem }` du DSFR.
- `.stepper-step` (fil d'étapes custom, sans équivalent direct dans le
  DSFR officiel) reste en pilule par choix pragmatique.
- Les `border-radius: 50%` (avatars, pastilles de statut, spinner) sont
  des cercles légitimes, cohérents avec l'usage DSFR.

## 2. Champ de saisie (`.input`)

Le vrai `.fr-input` du DSFR n'a pas de cadre complet : fond gris clair,
coin haut arrondi (`0.25rem 0.25rem 0 0`), et un soulignement en bas
(`box-shadow: inset 0 -2px 0 0 ...`) plutôt qu'une bordure pleine.
Appliqué à `.input` / `textarea.input` / `select.input`. Le focus suit
la même logique (soulignement bleu + outline DSFR au lieu d'un halo).

## 3. Focus visible (`:focus-visible`)

Ancien style : `outline: 3px solid var(--france-blue)` + `border-radius:
4px` forcé sur l'outline (n'a pas de sens : l'outline doit suivre la
forme de l'élément ciblé, pas imposer son propre rayon).

**Changement** : `outline: 2px solid #0a76f6` (bleu de focus DSFR,
différent du bleu France standard) + `outline-offset: 2px`, sans rayon
forcé — valeurs reprises telles quelles du CSS officiel du DSFR.

## 4. Ombres (`--shadow-sm/md/lg`)

Nos ombres (`0 20px 48px`...) étaient bien plus marquées que les ombres
DSFR officielles (`--raised-shadow` / `--lifted-shadow` /
`--overlap-shadow`), volontairement discrètes.

**Changement** :
- `--shadow-sm: 0 1px 3px rgba(0,0,18,.16)` (cartes, boutons)
- `--shadow-md: 0 3px 9px rgba(0,0,18,.16)` (survol de carte)
- `--shadow-lg: 0 6px 18px rgba(0,0,18,.16)` (modales, toasts)

## 5. Espacement entre boutons groupés

Règle DSFR : un espacement de 16px doit séparer les boutons d'un même
groupe. `.host-actions-row` utilisait `gap: var(--sp-2)` (8px).

**Changement** : `gap: var(--sp-4)` (16px).

## 6. Mise en évidence des axes et leurs couleurs

### Constat de départ
`categoryBadge()` générait un badge quasi invisible : fond à 10%
d'opacité (`${bg}1a`) et bordure à 33% d'opacité (`${bg}55`) sur fond
blanc — le badge de catégorie (ex. `ADAPTATION`) n'était pas
perceptible, en contradiction directe avec l'objectif de mise en
évidence des axes.

### Palette
Les 7 axes utilisent la palette de couleurs métier existante (issue de
l'outil R d'origine), déjà déclarée en 3 niveaux par axe dans
`:root` (`--axe-*`, `--axe-*-dark`, `--axe-*-light`) : Adaptation,
Économie circulaire, Atténuation, Biodiversité, Eau, Pollution, Guide
transverse.

### Nouveau composant `.badge-axe`
Badge à fond plein dans la couleur de l'axe, `border-radius:
var(--radius-badge)` (0.25rem, conforme `.fr-badge`), avec un liseré
interne discret (`box-shadow: inset 0 0 0 1px rgba(0,0,18,.16)`) pour
garder du relief y compris en vidéoprojection sur fond clair.

### Contraste du texte (WCAG AA, ≥ 4.5:1)
Sur les 7 couleurs de la charte, seules 2 (Atténuation, Eau) passent le
seuil de contraste avec du texte blanc. `categoryBadge()` calcule
désormais la luminance relative de la couleur reçue (formule WCAG) et
bascule automatiquement le texte en `#161616` (gris très foncé) quand
le contraste avec le blanc est insuffisant — fonctionne aussi pour un
axe personnalisé ajouté par l'animateur avec une couleur imprévue.

Résultat mesuré (ratio de contraste) :

| Axe | Couleur | Texte choisi | Contraste |
|---|---|---|---|
| Adaptation | `#FFCA00` | `#161616` | 11.8:1 |
| Atténuation | `#6E445A` | `#fff` | 8.0:1 |
| Ressource en eau | `#465F9D` | `#fff` | 6.2:1 |
| Biodiversité | `#68A532` | `#161616` | 6.1:1 |
| Pollution | `#C08C65` | `#161616` | 6.2:1 |
| Économie circulaire | `#d5706f` | `#161616` | 5.5:1 |
| Guide transverse | `#8585f6` | `#161616` | 5.8:1 |

Tous ≥ 4.5:1 (seuil WCAG AA texte normal), avec marge confortable.

`categoryBadge()` est utilisée de façon centralisée par les 3 écrans
temps réel (host, participant, projector) — un seul point de correction
propage l'amélioration partout.

## Non traité dans cette passe

- Le logo de la page d'accueil (peu contrasté sur fond blanc) : hors
  périmètre strict DSFR/axes de cette demande, signalé pour référence.
- Mapping complet des 46 fichiers CSS DSFR composant par composant :
  l'audit a porté sur les composants effectivement utilisés par l'app
  (boutons, champs, cartes, badges, focus, ombres, espacement). Aucun
  autre écart structurel identifié dans `style.css`/`pages.css`.

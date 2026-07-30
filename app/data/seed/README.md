# Données de seed (§8 du cahier des charges)

## CSV
Placez le fichier CSV des projets ici, sous le nom :

    app/data/seed/projets.csv

Colonnes attendues (dans cet ordre) :
`id,titre,type,stade,population,budget,porteur,territoire,resume,contexte,contrainte,enjeux,url_boussole`

## Images
Placez les images correspondantes (une par projet, PNG) ici :

    app/static/img/seed/p1.png
    app/static/img/seed/p2.png
    ...
    app/static/img/seed/p10.png

Le nombre à la fin du nom de fichier (`pN.png`) doit correspondre à la
colonne `id` du CSV. Ce dossier est servi publiquement sous
`/static/img/seed/pN.png`.

Si une image est absente au moment de l'import, le projet est quand même
créé, simplement sans image. Vous pouvez déposer les images plus tard et
relancer le script avec `--replace` pour régénérer les projets avec leurs
images.

## Lancer l'import

```bash
# aperçu sans rien écrire en base
python -m app.scripts.seed_projects --webinar-code ABCD1234 --dry-run

# import réel
python -m app.scripts.seed_projects --webinar-code ABCD1234

# réimport en remplaçant les projets de seed déjà présents
python -m app.scripts.seed_projects --webinar-code ABCD1234 --replace
```

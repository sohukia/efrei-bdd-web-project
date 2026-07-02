# Projet de Base de Donnée et Web


Télécharge data.gouv.fr, les transfère dans DuckDB, transforme en SQL, peuple DuckDB avec les données.s
Dashboard web Streamlit.

![Diagramme](/diagramme.png)

```
E  download_sources.py   → .txt bruts (délimités par |) dans /data
L  DuckDB read_csv        → table raw_dvf, sans aucune boucle Python
T  sql/transform.sql      → tables analytiques dédupliquées (SQL DuckDB)
→  INSERT OR IGNORE       → copie de travail, puis swap atomique vers foncier.db
→  app.py (Streamlit)     → http://localhost:8501, lecture seule des vues SQL
```

## Démarrage rapide

> Prérequis : `docker` et `docker compose`.

```bash
git clone https://github.com/sohukia/efrei-bdd-web-project.git && cd efrei-bdd-web-project
docker compose up -d --build
```

C'est tout. La stack démarre dans cet ordre :

1. **etl** télécharge, traite et enregistre les données dans la DB
2. **app** (Streamlit) — démarre une fois l'ingestion terminée et sert le
   dashboard sur <http://localhost:8501>.

> Le premier lancement télécharge ~500 Mo d'archives ; l'ingestion des
> 15,7 M de lignes ne prend ensuite que quelques dizaines de secondes.
> Suivez la progression avec `docker compose logs -f etl`.

## Organisation du code

- `download_sources.py` — téléchargement et extraction des archives DVF (E)
- `sql/transform.sql` — transformations DuckDB : identifiants synthétisés
  (MD5), dates, dédoublonnage (T)
- `sql/schema.sql` — modèle relationnel de la base publiée (PK/FK)
- `sql/views.sql` — vues et **macros tables paramétrées** (équivalent DuckDB
  des variables Grafana `${annee}`, `${type_bien}`, `${departement}`) servies
  au dashboard : KPI médians, évolution du prix au m², répartition par type,
  top communes, carte
- `elt.py` — orchestrateur : DuckDB `read_csv` (L), exécution des `.sql`,
  publication par remplacement atomique de `foncier.db`
- `app.py` — dashboard Streamlit, lecture seule (`SELECT` sur les vues)

## Relancer l'ingestion

```bash
docker compose run --rm etl     # n'ingère que les années manquantes
```

Pour forcer un rechargement complet :

```bash
docker compose down && docker volume rm bdd-web-project_dvf_data
docker compose up -d
```

## Inspecter la base à la main

```bash
docker run --rm -it -v bdd-web-project_dvf_data:/data python:3.13-slim \
  bash -c "pip -q install duckdb && python -c \"
import duckdb; con = duckdb.connect('/data/foncier.db', read_only=True)
print(con.execute('SHOW TABLES').df())\""
```

ou, avec [uv](https://docs.astral.sh/uv/) sur l'hôte : `uvx duckdb chemin/vers/foncier.db`.

## Développement local (sans Docker)

> Prérequis : [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync                          # installe l'environnement
uv run elt.py                    # pipeline complet, écrit ./foncier.db
DB_PATH=./foncier.db uv run streamlit run app.py
```

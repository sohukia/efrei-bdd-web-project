"""Pipeline ELT : téléchargement, transformation DuckDB, publication.

E — les archives sont téléchargées et extraites en .txt bruts (httpx) ;
L — les .txt (délimités par '|') sont chargés dans une base
    DuckDB « scratch » jetable ;
T — les transformations sont exprimées en SQL DuckDB (sql/transform.sql) ;
"""

import os
import shutil
import sys
import time
from pathlib import Path

import duckdb
import httpx

from download_sources import download

# Centres des communes (lat/lon par code INSEE) pour la carte du dashboard.
# Les fichiers DVF codent Paris/Lyon/Marseille par arrondissement (75101…,
# 69381…, 13201…) : il faut interroger les deux types de l'API géo.
GEO_SOURCES = [
  (
    "https://geo.api.gouv.fr/communes?fields=code,centre&format=json",
    "communes_geo.json",
    "communes-geo.json",
    "commune coordinates (geo.api.gouv.fr)",
  ),
  (
    "https://geo.api.gouv.fr/communes?fields=code,centre&format=json&type=arrondissement-municipal",
    "arrondissements_geo.json",
    "arrondissements-geo.json",
    "arrondissement coordinates (geo.api.gouv.fr)",
  ),
]

# Contours des départements pour la choroplèthe (lu tel quel par app.py)
DEPT_GEOJSON_SOURCE = "https://raw.githubusercontent.com/gregoiredavid/france-geojson/master/departements.geojson"
DEPT_GEOJSON_FILE = "departements.geojson"

SOURCES = {
  2025: "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002321/valeursfoncieres-2025.txt.zip",
  2024: "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002306/valeursfoncieres-2024.txt.zip",
  2023: "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002251/valeursfoncieres-2023.txt.zip",
  2022: "https://static.data.gouv.fr/resources/demandes-de-valeurs-foncieres/20260405-002236/valeursfoncieres-2022.txt.zip",
}

DATA_DIR = Path(os.environ.get("DATA_DIR", "."))
SQL_DIR = Path(__file__).resolve().parent / "sql"
PUBLISHED_DB = DATA_DIR / "foncier.db"
WORK_DB = DATA_DIR / "foncier.work.duckdb"
SCRATCH_DB = DATA_DIR / "scratch.duckdb"

# Colonnes du fichier source réellement utilisées par sql/transform.sql
RAW_COLUMNS = [
  "No disposition",
  "Date mutation",
  "Nature mutation",
  "Valeur fonciere",
  "No voie",
  "B/T/Q",
  "Type de voie",
  "Voie",
  "Code postal",
  "Commune",
  "Code departement",
  "Code commune",
  "Prefixe de section",
  "Section",
  "No plan",
  "Type local",
  "Surface reelle bati",
  "Nombre pieces principales",
]

# Ordre de remplissage imposé par les clés étrangères
TABLES = [
  ("commune", ["code_commune", "nom_commune", "code_departement", "code_postal"]),
  ("parcelle", ["id_parcelle", "code_commune"]),
  (
    "mutation",
    ["id_mutation", "annee", "date_mutation", "nature_mutation", "valeur_fonciere"],
  ),
  ("mutation_parcelle", ["id_mutation", "id_parcelle"]),
  (
    "local",
    [
      "id_local",
      "type_local",
      "surface_reelle_bati",
      "nombre_pieces_principales",
      "id_parcelle",
    ],
  ),
]


def log(message: str):
  print(f"[INFO] {message}", flush=True)


def run_sql_file(con: duckdb.DuckDBPyConnection, name: str):
  con.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def configure(con: duckdb.DuckDBPyConnection):
  """Borne la RAM pour éviter que DuckDB ne déborde sur le disque."""
  tmp_dir = DATA_DIR / "tmp"
  tmp_dir.mkdir(parents=True, exist_ok=True)
  con.execute(f"SET memory_limit = '{os.environ.get('DUCKDB_MEMORY_LIMIT', '2GB')}'")
  con.execute(f"SET temp_directory = '{tmp_dir}'")
  con.execute("SET preserve_insertion_order = false")


def prepare_work_db() -> set[str]:
  """Copie la base publiée vers la copie de travail (ou la crée) et
  retourne les fichiers sources déjà ingérés."""
  WORK_DB.unlink(missing_ok=True)
  if PUBLISHED_DB.exists():
    shutil.copy2(PUBLISHED_DB, WORK_DB)

  with duckdb.connect(str(WORK_DB)) as con:
    run_sql_file(con, "schema.sql")
    return {row[0] for row in con.execute("SELECT filename FROM elt_file;").fetchall()}


def find_source_txt(year: int) -> Path | None:
  """Locates the extracted raw file for a year, whatever its casing."""
  for path in DATA_DIR.glob("*.txt"):
    if str(year) in path.name and "valeursfoncieres" in path.name.lower():
      return path
  return None


def collect_sources(years: list[int]) -> dict[int, Path]:
  """Ensures the raw .txt of each year is on disk, downloading missing ones."""
  missing = [year for year in years if find_source_txt(year) is None]
  if missing:
    log(f"Downloading source archives for {missing}...")
    download([SOURCES[year] for year in missing], str(DATA_DIR))

  paths = {}
  for year in years:
    path = find_source_txt(year)
    if path is None:
      print(f"[ERROR] Could not obtain raw file for {year}, skipping.", flush=True)
    else:
      paths[year] = path
  return paths


def fetch_file(url: str, filename: str, label: str) -> Path | None:
  """Télécharge (une fois) une ressource annexe ; non bloquant en cas
  d'échec réseau, la fonctionnalité correspondante sera simplement absente."""
  target = DATA_DIR / filename
  if target.exists():
    return target
  log(f"Downloading {label}...")
  try:
    response = httpx.get(url, timeout=60, follow_redirects=True)
    response.raise_for_status()
  except Exception as error:
    print(f"[WARN] Could not fetch {label}: {error}", flush=True)
    return None
  tmp = target.with_suffix(".part")
  tmp.write_bytes(response.content)
  os.replace(tmp, target)
  return target


def ingest_geo(already_loaded: set[str]):
  """Charge les centres des communes et arrondissements dans la copie de
  travail. Le GeoJSON expose centre.coordinates = [longitude, latitude]."""
  with duckdb.connect(str(WORK_DB)) as con:
    for url, filename, marker, label in GEO_SOURCES:
      if marker in already_loaded:
        continue
      geo_file = fetch_file(url, filename, label)
      if geo_file is None:
        continue
      con.execute(f"""
        INSERT OR IGNORE INTO commune_geo (code_commune, latitude, longitude)
        SELECT code, centre.coordinates[2]::DOUBLE, centre.coordinates[1]::DOUBLE
        FROM read_json_auto('{geo_file}')
        WHERE centre IS NOT NULL
      """)
      con.execute("INSERT OR IGNORE INTO elt_file (filename) VALUES (?)", [marker])
    count = con.execute("SELECT COUNT(*) FROM commune_geo").fetchone()[0]
  log(f"commune_geo: {count:,} communes geolocated")


def ingest(txt_paths: dict[int, Path]):
  """L + T + publication : .txt bruts → scratch → INSERT OR IGNORE dans la
  copie de travail, via une base scratch jetable pour que la base publiée
  ne garde aucune page morte des données brutes."""
  SCRATCH_DB.unlink(missing_ok=True)
  con = duckdb.connect(str(SCRATCH_DB))
  try:
    configure(con)

    files_sql = ", ".join(f"'{path}'" for path in txt_paths.values())
    columns_sql = ", ".join(f'"{col}"' for col in RAW_COLUMNS)
    log(f"Loading {len(txt_paths)} raw file(s) into DuckDB...")
    started = time.monotonic()
    # all_varchar préserve les zéros de tête des codes commune/département
    con.execute(f"""
      CREATE OR REPLACE TABLE raw_dvf AS
      SELECT {columns_sql}
      FROM read_csv([{files_sql}], delim='|', header=true, all_varchar=true)
    """)
    total = con.execute("SELECT COUNT(*) FROM raw_dvf").fetchone()[0]
    log(f"Loaded {total:,} raw rows in {time.monotonic() - started:.1f}s")

    log("Running SQL transformations...")
    started = time.monotonic()
    run_sql_file(con, "transform.sql")
    log(f"Transformations done in {time.monotonic() - started:.1f}s")

    log("Publishing into work database...")
    con.execute(f"ATTACH '{WORK_DB}' AS foncier")
    for table, columns in TABLES:
      started = time.monotonic()
      columns_list = ", ".join(columns)
      con.execute(f"""
        INSERT OR IGNORE INTO foncier."{table}" ({columns_list})
        SELECT {columns_list} FROM t_{table}
      """)
      count = con.execute(f'SELECT COUNT(*) FROM foncier."{table}"').fetchone()[0]
      log(f"{table}: {count:,} rows total ({time.monotonic() - started:.1f}s)")
    con.execute(
      """
      INSERT OR IGNORE INTO foncier.elt_file (filename)
      SELECT unnest($names)
      """,
      {"names": [f"valeursfoncieres-{year}.txt" for year in txt_paths]},
    )
  finally:
    con.close()
    SCRATCH_DB.unlink(missing_ok=True)


def publish():
  """Met à jour les vues analytiques puis remplace atomiquement la base
  publiée ; le dashboard voit le changement au rechargement suivant."""
  with duckdb.connect(str(WORK_DB)) as con:
    run_sql_file(con, "views.sql")
    con.execute("CHECKPOINT")
  os.replace(WORK_DB, PUBLISHED_DB)
  log(f"Published {PUBLISHED_DB}")


def cleanup(txt_paths: dict[int, Path]):
  shutil.rmtree(DATA_DIR / "tmp", ignore_errors=True)
  for path in txt_paths.values():
    path.unlink(missing_ok=True)


def main() -> int:
  DATA_DIR.mkdir(parents=True, exist_ok=True)

  already_loaded = prepare_work_db()
  todo = [
    year
    for year in sorted(SOURCES)
    if f"valeursfoncieres-{year}.txt" not in already_loaded
  ]
  geo_todo = any(marker not in already_loaded for _, _, marker, _ in GEO_SOURCES)

  # Contours des départements : simple fichier servi à l'app, pas d'ingestion
  fetch_file(DEPT_GEOJSON_SOURCE, DEPT_GEOJSON_FILE, "departement boundaries")

  if not todo and not geo_todo:
    publish()
    log("[SUCCESS] Database is up to date, nothing to ingest.")
    return 0

  if geo_todo:
    ingest_geo(already_loaded)

  if todo:
    log(f"Years to ingest: {todo}")
    txt_paths = collect_sources(todo)
    if not txt_paths:
      print("[ERROR] No source file available, aborting.", flush=True)
      WORK_DB.unlink(missing_ok=True)
      return 1
    ingest(txt_paths)
    publish()
    cleanup(txt_paths)
    log(f"[SUCCESS] Ingested {len(txt_paths)} year(s): {sorted(txt_paths)}")
  else:
    publish()
    log("[SUCCESS] Commune coordinates updated.")
  return 0


if __name__ == "__main__":
  sys.exit(main())

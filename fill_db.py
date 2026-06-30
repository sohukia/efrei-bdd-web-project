import os
import signal
from threading import Event
import psycopg

# Configuration constants
DB_DSN = "postgresql://username:password@localhost:5432/your_database"
PROCESSED_CSV_DIR = "./"
CSV_FILES = [
  "processed_valeursfoncieres-2022.csv",
  "processed_valeursfoncieres-2023.csv",
  "processed_valeursfoncieres-2024.csv",
  "processed_valeursfoncieres-2025.csv",
]

done_event = Event()


def handle_sigint(signum, frame):
  print("\n[INFO] Interruption requested. Exiting clean...")
  done_event.set()


signal.signal(signal.SIGINT, handle_sigint)

# SQL Statements to initialize Target Schema
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS commune (
    code_commune        VARCHAR(10)  PRIMARY KEY,
    nom_commune         VARCHAR(100),
    code_departement    VARCHAR(3),
    code_postal         VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS parcelle (
    id_parcelle         VARCHAR(20) PRIMARY KEY,
    code_commune        VARCHAR(10) REFERENCES commune(code_commune)
);

CREATE TABLE IF NOT EXISTS adresse (
    id_adresse          VARCHAR(12)  PRIMARY KEY,
    adresse_numero      VARCHAR(10),
    id_parcelle         VARCHAR(20) REFERENCES parcelle(id_parcelle)
);

CREATE TABLE IF NOT EXISTS mutation (
    id_mutation         VARCHAR(16)  PRIMARY KEY,
    annee               SMALLINT,
    date_mutation       DATE,
    nature_mutation     VARCHAR(50),
    valeur_fonciere     NUMERIC(15,2)
);

CREATE TABLE IF NOT EXISTS mutation_parcelle (
    id_mutation     VARCHAR(16) REFERENCES mutation(id_mutation),
    id_parcelle     VARCHAR(20) REFERENCES parcelle(id_parcelle),
    PRIMARY KEY (id_mutation, id_parcelle)
);

CREATE TABLE IF NOT EXISTS local (
    id_local                    VARCHAR(12)  PRIMARY KEY,
    type_local                  VARCHAR(30),
    surface_reelle_bati         NUMERIC(10,2),
    nombre_pieces_principales   SMALLINT,
    id_parcelle                 VARCHAR(20) REFERENCES parcelle(id_parcelle)
);
"""

# SQL Statement to initialize Unindexed Staging Table
STAGING_DDL = """
DROP TABLE IF EXISTS staging_dvf;
CREATE UNLOGGED TABLE staging_dvf (
    code_commune VARCHAR, nom_commune VARCHAR, code_departement VARCHAR, code_postal VARCHAR,
    id_parcelle VARCHAR, id_adresse VARCHAR, adresse_ TEXT, id_mutation VARCHAR,
    annee VARCHAR, date_mutation VARCHAR, nature_mutation VARCHAR, valeur_fonciere VARCHAR,
    id_local VARCHAR, type_local VARCHAR, surface_reelle_bati VARCHAR, nombre_pieces_principales VARCHAR
);
"""

# Relational distribution queries executing purely inside PostgreSQL storage layer
RELATIONAL_INSERT_QUERIES = [
  # 1. Populate communes (Unique entity resolution)
  """
    INSERT INTO commune (code_commune, nom_commune, code_departement, code_postal)
    SELECT DISTINCT code_commune, nom_commune, code_departement, code_postal
    FROM staging_dvf
    WHERE code_commune IS NOT NULL AND code_commune != ''
    ON CONFLICT (code_commune) DO NOTHING;
    """,
  # 2. Populate parcelles
  """
    INSERT INTO parcelle (id_parcelle, code_commune)
    SELECT DISTINCT id_parcelle, code_commune
    FROM staging_dvf
    WHERE id_parcelle IS NOT NULL AND id_parcelle != ''
    ON CONFLICT (id_parcelle) DO NOTHING;
    """,
  # 3. Populate mutations (Cast empty string or null matching values natively)
  """
    INSERT INTO mutation (id_mutation, annee, date_mutation, nature_mutation, valeur_fonciere)
    SELECT DISTINCT
        id_mutation,
        NULLIF(annee, '')::SMALLINT,
        NULLIF(date_mutation, '')::DATE,
        nature_mutation,
        NULLIF(valeur_fonciere, '')::NUMERIC(15,2)
    FROM staging_dvf
    WHERE id_mutation IS NOT NULL AND id_mutation != ''
    ON CONFLICT (id_mutation) DO NOTHING;
    """,
  # 4. Populate mutation_parcelle link table
  """
    INSERT INTO mutation_parcelle (id_mutation, id_parcelle)
    SELECT DISTINCT id_mutation, id_parcelle
    FROM staging_dvf
    WHERE id_mutation IS NOT NULL AND id_mutation != '' AND id_parcelle IS NOT NULL AND id_parcelle != ''
    ON CONFLICT (id_mutation, id_parcelle) DO NOTHING;
    """,
  # 5. Populate local entities
  """
    INSERT INTO local (id_local, type_local, surface_reelle_bati, nombre_pieces_principales, id_parcelle)
    SELECT DISTINCT
        id_local,
        type_local,
        NULLIF(surface_reelle_bati, '')::NUMERIC(10,2),
        NULLIF(nombre_pieces_principales, '')::SMALLINT,
        id_parcelle
    FROM staging_dvf
    WHERE id_local IS NOT NULL AND id_local != ''
    ON CONFLICT (id_local) DO NOTHING;
    """,
]


def init_database(conn):
  """Executes schema DDL queries to guarantee existence of target entities."""
  with conn.cursor() as cur:
    print("[INFO] Creating schema tables...")
    cur.execute(SCHEMA_DDL)
    print("[INFO] Creating staging schema structure...")
    cur.execute(STAGING_DDL)
  conn.commit()


def load_csv_to_staging(conn, file_path: str):
  """Streams data using COPY command protocol into unindexed staging table."""
  print(f"[INFO] Initializing COPY stream for target data file: {file_path}")
  copy_sql = """
        COPY staging_dvf (
            code_commune, nom_commune, code_departement, code_postal,
            id_parcelle, id_adresse, adresse_, id_mutation,
            annee, date_mutation, nature_mutation, valeur_fonciere,
            id_local, type_local, surface_reelle_bati, nombre_pieces_principales
        ) FROM STDIN WITH (FORMAT csv, HEADER true, DELIMITER ',');
    """
  with conn.cursor() as cur:
    with open(file_path, "r", encoding="utf-8") as f:
      with cur.copy(copy_sql) as copy:
        while True:
          if done_event.is_set():
            return
          chunk = f.read(1024 * 1024)  # Read 1MB memory blocks
          if not chunk:
            break
          copy.write(chunk)
  conn.commit()


def distribute_staging_data(conn):
  """Executes SQL internal ingestion pipelines."""
  with conn.cursor() as cur:
    for idx, query in enumerate(RELATIONAL_INSERT_QUERIES, 1):
      if done_event.is_set():
        return
      print(
        f"[INFO] Executing structural data distribution routine ({idx}/{len(RELATIONAL_INSERT_QUERIES)})..."
      )
      cur.execute(query)
  conn.commit()


def cleanup_staging(conn):
  """Drops volatile storage components from working context."""
  with conn.cursor() as cur:
    print("[INFO] Clearing volatile staging space...")
    cur.execute("DROP TABLE IF EXISTS staging_dvf;")
  conn.commit()


def fill_db():
  try:
    with psycopg.connect(DB_DSN) as conn:
      init_database(conn)

      for file_name in CSV_FILES:
        if done_event.is_set():
          break
        file_path = os.path.join(PROCESSED_CSV_DIR, file_name)
        if os.path.exists(file_path):
          load_csv_to_staging(conn, file_path)
        else:
          print(
            f"[WARN] Target path {file_path} does not exist, skipping extraction iteration."
          )

      if not done_event.is_set():
        distribute_staging_data(conn)
        cleanup_staging(conn)
        print("[SUCCESS] Data streaming ingestion workflow complete.")

  except Exception as error:
    print(f"[CRITICAL ERROR] Pipeline execution halted: {error}")


fill_db()

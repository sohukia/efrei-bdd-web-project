import os
import signal
from threading import Event

import mysql.connector

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

DB_CONFIG = {
  "host": "127.0.0.1",
  "user": "user",
  "password": "userpwd",
  "database": "foncier",
  "allow_local_infile": True,
}

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

# --------------------------------------------------------------------------
# Schema DDL
# --------------------------------------------------------------------------

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS commune (
    code_commune        VARCHAR(10)  PRIMARY KEY,
    nom_commune         VARCHAR(100),
    code_departement    VARCHAR(3),
    code_postal         VARCHAR(10)
);
"""

SCHEMA_DDL_2 = """
CREATE TABLE IF NOT EXISTS parcelle (
    id_parcelle         VARCHAR(20) PRIMARY KEY,
    code_commune        VARCHAR(10),
    FOREIGN KEY (code_commune) REFERENCES commune(code_commune)
);
"""

SCHEMA_DDL_3 = """
CREATE TABLE IF NOT EXISTS adresse (
    id_adresse          VARCHAR(12)  PRIMARY KEY,
    adresse_numero      VARCHAR(10),
    id_parcelle         VARCHAR(20),
    FOREIGN KEY (id_parcelle) REFERENCES parcelle(id_parcelle)
);
"""

SCHEMA_DDL_4 = """
CREATE TABLE IF NOT EXISTS mutation (
    id_mutation         VARCHAR(16)  PRIMARY KEY,
    annee               SMALLINT,
    date_mutation       DATE,
    nature_mutation     VARCHAR(50),
    valeur_fonciere     NUMERIC(15,2)
);
"""

SCHEMA_DDL_5 = """
CREATE TABLE IF NOT EXISTS mutation_parcelle (
    id_mutation     VARCHAR(16),
    id_parcelle     VARCHAR(20),
    PRIMARY KEY (id_mutation, id_parcelle),
    FOREIGN KEY (id_mutation) REFERENCES mutation(id_mutation),
    FOREIGN KEY (id_parcelle) REFERENCES parcelle(id_parcelle)
);
"""

SCHEMA_DDL_6 = """
CREATE TABLE IF NOT EXISTS `local` (
    id_local                    VARCHAR(12)  PRIMARY KEY,
    type_local                  VARCHAR(30),
    surface_reelle_bati         NUMERIC(10,2),
    nombre_pieces_principales   SMALLINT,
    id_parcelle                 VARCHAR(20),
    FOREIGN KEY (id_parcelle) REFERENCES parcelle(id_parcelle)
);
"""

STAGING_DDL_DROP = """
DROP TABLE IF EXISTS staging_dvf;
"""

STAGING_DDL_CREATE = """
CREATE TABLE staging_dvf (
    code_commune VARCHAR(255), nom_commune VARCHAR(255), code_departement VARCHAR(255), code_postal VARCHAR(255),
    id_parcelle VARCHAR(255), id_adresse VARCHAR(255), adresse_ TEXT, id_mutation VARCHAR(255),
    annee VARCHAR(255), date_mutation VARCHAR(255), nature_mutation VARCHAR(255), valeur_fonciere VARCHAR(255),
    id_local VARCHAR(255), type_local VARCHAR(255), surface_reelle_bati VARCHAR(255), nombre_pieces_principales VARCHAR(255)
) ENGINE=InnoDB;
"""

RELATIONAL_INSERT_QUERIES = [
  # 1. Populate communes
  """
    INSERT IGNORE INTO commune (code_commune, nom_commune, code_departement, code_postal)
    SELECT DISTINCT code_commune, nom_commune, code_departement, code_postal
    FROM staging_dvf
    WHERE code_commune IS NOT NULL AND code_commune != '';
    """,
  # 2. Populate parcelles
  """
    INSERT IGNORE INTO parcelle (id_parcelle, code_commune)
    SELECT DISTINCT id_parcelle, code_commune
    FROM staging_dvf
    WHERE id_parcelle IS NOT NULL AND id_parcelle != '';
    """,
  # 3. Populate mutations (Convert dates from DD/MM/YYYY string format to Standard MySQL YYYY-MM-DD)
  """
    INSERT IGNORE INTO mutation (id_mutation, annee, date_mutation, nature_mutation, valeur_fonciere)
    SELECT DISTINCT
        id_mutation,
        NULLIF(annee, ''),
        STR_TO_DATE(NULLIF(date_mutation, ''), '%d/%m/%Y'),
        nature_mutation,
        NULLIF(valeur_fonciere, '')
    FROM staging_dvf
    WHERE id_mutation IS NOT NULL AND id_mutation != '';
    """,
  # 4. Populate mutation_parcelle link table
  """
    INSERT IGNORE INTO mutation_parcelle (id_mutation, id_parcelle)
    SELECT DISTINCT id_mutation, id_parcelle
    FROM staging_dvf
    WHERE id_mutation IS NOT NULL AND id_mutation != '' AND id_parcelle IS NOT NULL AND id_parcelle != '';
    """,
  # 5. Populate local entities
  """
    INSERT IGNORE INTO `local` (id_local, type_local, surface_reelle_bati, nombre_pieces_principales, id_parcelle)
    SELECT DISTINCT
        id_local,
        type_local,
        NULLIF(surface_reelle_bati, ''),
        NULLIF(nombre_pieces_principales, ''),
        id_parcelle
    FROM staging_dvf
    WHERE id_local IS NOT NULL AND id_local != '';
    """,
]

# --------------------------------------------------------------------------
# Functions
# --------------------------------------------------------------------------


def init_database(conn):
  """Executes schema DDL queries to guarantee existence of target entities."""
  with conn.cursor() as cur:
    print("[INFO] Creating schema tables...")
    cur.execute(SCHEMA_DDL)
    cur.execute(SCHEMA_DDL_2)
    cur.execute(SCHEMA_DDL_3)
    cur.execute(SCHEMA_DDL_4)
    cur.execute(SCHEMA_DDL_5)
    cur.execute(SCHEMA_DDL_6)
    print("[INFO] Creating staging schema structure...")
    cur.execute(STAGING_DDL_DROP)
    cur.execute(STAGING_DDL_CREATE)
  conn.commit()


def load_csv_to_staging(conn, file_path: str):
  """Invokes MySQL's LOAD DATA LOCAL INFILE to stream file contents directly into staging."""
  print(f"[INFO] Executing high-speed LOAD DATA execution for: {file_path}")

  # Absolute path formatting required by MySQL engine context rules
  absolute_path = os.path.abspath(file_path).replace("\\", "/")

  load_sql = f"""
        LOAD DATA LOCAL INFILE '{absolute_path}'
        INTO TABLE staging_dvf
        FIELDS TERMINATED BY ','
        ENCLOSED BY '"'
        LINES TERMINATED BY '\\n'
        IGNORE 1 LINES
        (code_commune, nom_commune, code_departement, code_postal,
         id_parcelle, id_adresse, adresse_, id_mutation,
         annee, date_mutation, nature_mutation, valeur_fonciere,
         id_local, type_local, surface_reelle_bati, nombre_pieces_principales);
    """
  with conn.cursor() as cur:
    cur.execute(load_sql)
  conn.commit()


def distribute_staging_data(conn):
  """Executes the relational insert queries to parse staging contents into target schemas."""
  with conn.cursor() as cur:
    for idx, query in enumerate(RELATIONAL_INSERT_QUERIES, 1):
      if done_event.is_set():
        return
      print(
        f"[INFO] Running storage layer transformation routine ({idx}/{len(RELATIONAL_INSERT_QUERIES)})..."
      )
      cur.execute(query)
  conn.commit()


def cleanup_staging(conn):
  """Removes temporary staging elements from the target database."""
  with conn.cursor() as cur:
    print("[INFO] Purging staging infrastructure...")
    cur.execute("DROP TABLE IF EXISTS staging_dvf;")
  conn.commit()


def main():
  try:
    with mysql.connector.connect(**DB_CONFIG) as conn:
      init_database(conn)

      for file_name in CSV_FILES:
        if done_event.is_set():
          break
        file_path = os.path.join(PROCESSED_CSV_DIR, file_name)
        if os.path.exists(file_path):
          load_csv_to_staging(conn, file_path)
        else:
          print(
            f"[WARN] Target path {file_path} does not exist, skipping processing loop."
          )

      if not done_event.is_set():
        distribute_staging_data(conn)
        cleanup_staging(conn)
        print("[SUCCESS] Data streaming ingestion workflow complete.")

  except Exception as error:
    print(f"[CRITICAL ERROR] Pipeline execution halted: {error}")


if __name__ == "__main__":
  main()

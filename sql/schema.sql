-- Schéma relationnel de la base publiée /data/foncier.db (DuckDB).
-- Exécuté par elt.py à la création de la base (CREATE ... IF NOT EXISTS, idempotent).

CREATE TABLE IF NOT EXISTS commune (
    code_commune        VARCHAR(10)  PRIMARY KEY,
    nom_commune         VARCHAR(100),
    code_departement    VARCHAR(3),
    code_postal         VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS parcelle (
    id_parcelle         VARCHAR(20) PRIMARY KEY,
    code_commune        VARCHAR(10)   -- référence commune(code_commune)
);

CREATE TABLE IF NOT EXISTS adresse (
    id_adresse          VARCHAR(12)  PRIMARY KEY,
    adresse_numero      VARCHAR(10),
    id_parcelle         VARCHAR(20)   -- référence parcelle(id_parcelle)
);

CREATE TABLE IF NOT EXISTS mutation (
    id_mutation         VARCHAR(32)  PRIMARY KEY,
    annee               SMALLINT,
    date_mutation       DATE,
    nature_mutation     VARCHAR(50),
    valeur_fonciere     DECIMAL(15,2)
);

CREATE TABLE IF NOT EXISTS mutation_parcelle (
    id_mutation     VARCHAR(32),   -- référence mutation(id_mutation)
    id_parcelle     VARCHAR(20),   -- référence parcelle(id_parcelle)
    PRIMARY KEY (id_mutation, id_parcelle)
);

CREATE TABLE IF NOT EXISTS "local" (
    id_local                    VARCHAR(32)  PRIMARY KEY,
    type_local                  VARCHAR(30),
    surface_reelle_bati         DECIMAL(10,2),
    nombre_pieces_principales   SMALLINT,
    id_parcelle                 VARCHAR(20)   -- référence parcelle(id_parcelle)
);

-- Centres géographiques des communes (source geo.api.gouv.fr, code INSEE
-- = code_commune). Remplie par elt.py, sert à la carte du dashboard.
CREATE TABLE IF NOT EXISTS commune_geo (
    code_commune    VARCHAR(10) PRIMARY KEY,
    latitude        DOUBLE,
    longitude       DOUBLE
);

-- Fichiers sources déjà ingérés : permet de relancer la stack sans tout recharger.
CREATE TABLE IF NOT EXISTS etl_file (
    filename    VARCHAR(255) PRIMARY KEY,
    loaded_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

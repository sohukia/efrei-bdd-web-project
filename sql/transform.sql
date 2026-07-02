-- Transformations DuckDB (ELT) : de la table brute raw_dvf (colonnes du fichier
-- DVF, toutes en VARCHAR) vers les tables analytiques dédupliquées t_*,
-- exportées ensuite vers MySQL par elt.py.
--
-- Dans l'export open data DVF, « Reference document » et « Identifiant local »
-- sont toujours vides : id_mutation et id_local sont donc synthétisés par MD5.
-- Une mutation est identifiée par (date, n° disposition, valeur, commune) :
-- les lignes d'une vente multi-parcelles partagent ces champs et se regroupent
-- en une seule mutation, ce qui est le regroupement voulu.

CREATE OR REPLACE VIEW dvf_clean AS
WITH src AS (
  SELECT
    coalesce(trim("Code departement"), '')           AS dept,
    coalesce(trim("Code commune"), '')                AS comm,
    coalesce(nullif(trim("Prefixe de section"), ''), '000') AS prefixe,
    coalesce(trim("Section"), '')                     AS section,
    coalesce(trim("No plan"), '')                     AS no_plan,
    coalesce(trim("No disposition"), '')              AS no_disposition,
    coalesce(trim("Date mutation"), '')               AS date_brute,
    coalesce(trim("Nature mutation"), '')             AS nature_mutation,
    replace(coalesce(trim("Valeur fonciere"), ''), ',', '.') AS valeur_brute,
    coalesce(trim("Commune"), '')                     AS nom_commune,
    coalesce(trim("Code postal"), '')                 AS code_postal,
    coalesce(trim("Type local"), '')                  AS type_local,
    coalesce(trim("Surface reelle bati"), '')         AS surface_brute,
    coalesce(trim("Nombre pieces principales"), '')   AS pieces_brutes,
    concat_ws(' ',
      nullif(trim("No voie"), ''),
      nullif(trim("B/T/Q"), ''),
      nullif(trim("Type de voie"), ''),
      nullif(trim("Voie"), '')
    )                                                  AS adresse_
  FROM raw_dvf
),
ids AS (
  SELECT
    *,
    dept || comm AS code_commune,
    -- Identifiant cadastral à 14 caractères :
    -- département (2) + commune (3) + préfixe (3) + section (2) + n° plan (4)
    CASE
      WHEN dept <> '' AND comm <> '' AND section <> '' AND no_plan <> ''
      THEN dept || comm || prefixe || lpad(section, 2, '0') || lpad(no_plan, 4, '0')
    END AS id_parcelle,
    CASE
      WHEN date_brute <> ''
      THEN md5(date_brute || '|' || no_disposition || '|' || valeur_brute || '|' || dept || comm)
    END AS id_mutation
  FROM src
)
SELECT
  nullif(code_commune, '')                       AS code_commune,
  nom_commune,
  nullif(dept, '')                               AS code_departement,
  code_postal,
  id_parcelle,
  adresse_,
  id_mutation,
  year(try_strptime(date_brute, '%d/%m/%Y'))     AS annee,
  try_strptime(date_brute, '%d/%m/%Y')::DATE     AS date_mutation,
  nature_mutation,
  try_cast(nullif(valeur_brute, '') AS DECIMAL(15,2))  AS valeur_fonciere,
  CASE
    WHEN type_local <> '' AND id_parcelle IS NOT NULL
    THEN md5(coalesce(id_mutation, '') || '|' || id_parcelle || '|' || type_local
             || '|' || surface_brute || '|' || pieces_brutes)
  END                                            AS id_local,
  nullif(type_local, '')                         AS type_local,
  try_cast(nullif(surface_brute, '') AS DECIMAL(10,2)) AS surface_reelle_bati,
  try_cast(nullif(pieces_brutes, '') AS SMALLINT) AS nombre_pieces_principales
FROM ids;

-- Une ligne par clé primaire : any_value() arbitre les rares divergences
-- d'attributs, comme le faisait INSERT IGNORE dans l'ancien staging MySQL.

CREATE OR REPLACE TABLE t_commune AS
SELECT
  code_commune,
  any_value(nom_commune)      AS nom_commune,
  any_value(code_departement) AS code_departement,
  any_value(code_postal)      AS code_postal
FROM dvf_clean
WHERE code_commune IS NOT NULL
GROUP BY code_commune;

CREATE OR REPLACE TABLE t_parcelle AS
SELECT
  id_parcelle,
  any_value(code_commune) AS code_commune
FROM dvf_clean
WHERE id_parcelle IS NOT NULL
GROUP BY id_parcelle;

CREATE OR REPLACE TABLE t_mutation AS
SELECT
  id_mutation,
  any_value(annee)           AS annee,
  any_value(date_mutation)   AS date_mutation,
  any_value(nature_mutation) AS nature_mutation,
  any_value(valeur_fonciere) AS valeur_fonciere
FROM dvf_clean
WHERE id_mutation IS NOT NULL
GROUP BY id_mutation;

CREATE OR REPLACE TABLE t_mutation_parcelle AS
SELECT DISTINCT id_mutation, id_parcelle
FROM dvf_clean
WHERE id_mutation IS NOT NULL AND id_parcelle IS NOT NULL;

CREATE OR REPLACE TABLE t_local AS
SELECT
  id_local,
  any_value(type_local)                AS type_local,
  any_value(surface_reelle_bati)       AS surface_reelle_bati,
  any_value(nombre_pieces_principales) AS nombre_pieces_principales,
  any_value(id_parcelle)               AS id_parcelle
FROM dvf_clean
WHERE id_local IS NOT NULL
GROUP BY id_local;

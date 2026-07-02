CREATE OR REPLACE VIEW v_vente_local AS
SELECT
  m.id_mutation,
  m.annee,
  m.valeur_fonciere,
  l.type_local,
  l.surface_reelle_bati,
  p.code_commune
FROM mutation m
JOIN mutation_parcelle mp ON m.id_mutation = mp.id_mutation
JOIN "local" l            ON mp.id_parcelle = l.id_parcelle
JOIN parcelle p           ON mp.id_parcelle = p.id_parcelle
WHERE m.nature_mutation = 'Vente'
  AND m.valeur_fonciere > 0
  AND l.surface_reelle_bati > 0;

-- ===================================================================
-- 1. Nb ventes, Volume total, Prix médian, Prix m^2 médian
-- ===================================================================
CREATE OR REPLACE MACRO kpi_ventes(p_annee := NULL, p_type := NULL) AS TABLE
WITH paires AS (
  SELECT id_mutation, valeur_fonciere, valeur_fonciere / surface_reelle_bati AS prix_m2
  FROM v_vente_local
  WHERE (p_annee IS NULL OR annee = p_annee)
    AND (p_type IS NULL OR type_local = p_type)
),
ventes AS (
  SELECT DISTINCT id_mutation, valeur_fonciere FROM paires
)
SELECT
  (SELECT COUNT(*) FROM ventes)                       AS nb_ventes,
  (SELECT SUM(valeur_fonciere) FROM ventes)           AS volume_total_eur,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY valeur_fonciere)
     FROM ventes)                                     AS prix_median_eur,
  (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY prix_m2)
     FROM paires)                                     AS prix_m2_median_eur;

-- ===================================================================
-- 2. Évolution du prix au m^2 par année x type de bien
-- ===================================================================
CREATE OR REPLACE VIEW v_prix_m2_evolution AS
SELECT
  annee,
  type_local AS type_bien,
  ROUND(AVG(valeur_fonciere / surface_reelle_bati), 0) AS prix_m2_moyen,
  COUNT(DISTINCT id_mutation)                          AS nb_ventes
FROM v_vente_local
WHERE type_local IS NOT NULL AND annee IS NOT NULL
GROUP BY annee, type_local
ORDER BY annee, type_local;

-- ===================================================================
-- 3. Volume de mutations par année (toutes natures confondues)
-- ===================================================================
CREATE OR REPLACE VIEW v_mutations_par_annee AS
SELECT annee, COUNT(*) AS mutations
FROM mutation
WHERE annee IS NOT NULL
GROUP BY annee
ORDER BY annee;

-- ===================================================================
-- 4. Répartition des transactions par type de bien
-- ===================================================================
CREATE OR REPLACE MACRO repartition_types(p_annee := NULL) AS TABLE
SELECT
  type_local AS type_bien,
  COUNT(DISTINCT id_mutation) AS nb_transactions,
  ROUND(
    COUNT(DISTINCT id_mutation) * 100.0
    / SUM(COUNT(DISTINCT id_mutation)) OVER ()
  , 1) AS part_pct
FROM v_vente_local
WHERE type_local IS NOT NULL
  AND (p_annee IS NULL OR annee = p_annee)
GROUP BY type_local
ORDER BY nb_transactions DESC;

-- ===================================================================
-- 5. Top 10 communes par nombre de ventes
-- (prix moyen sur mutations dédupliquées, prix m^2 sur paires vente x local)
-- ===================================================================
CREATE OR REPLACE MACRO top_communes(p_departement := NULL, p_annee := NULL) AS TABLE
WITH paires AS (
  SELECT * FROM v_vente_local
  WHERE (p_annee IS NULL OR annee = p_annee)
),
prix_m2 AS (
  SELECT code_commune, AVG(valeur_fonciere / surface_reelle_bati) AS prix_m2_moyen
  FROM paires
  GROUP BY code_commune
),
ventes AS (
  SELECT DISTINCT id_mutation, code_commune, valeur_fonciere FROM paires
)
SELECT
  c.nom_commune                     AS commune,
  c.code_departement                AS dept,
  COUNT(*)                          AS nb_ventes,
  ROUND(AVG(v.valeur_fonciere), 0)  AS prix_moyen_eur,
  ROUND(any_value(p.prix_m2_moyen), 0) AS prix_m2_moyen
FROM ventes v
JOIN commune c    ON v.code_commune = c.code_commune
LEFT JOIN prix_m2 p ON v.code_commune = p.code_commune
WHERE (p_departement IS NULL OR c.code_departement = p_departement)
GROUP BY c.code_commune, c.nom_commune, c.code_departement
ORDER BY nb_ventes DESC
LIMIT 10;

-- ===================================================================
-- 6. Carte : ventes agrégées par commune (centres via geo.api.gouv.fr)
-- ===================================================================
CREATE OR REPLACE MACRO carte_communes(p_annee := NULL, p_departement := NULL) AS TABLE
WITH ventes AS (
  SELECT DISTINCT id_mutation, code_commune, valeur_fonciere
  FROM v_vente_local
  WHERE (p_annee IS NULL OR annee = p_annee)
)
SELECT
  c.nom_commune                    AS commune,
  g.latitude,
  g.longitude,
  COUNT(*)                         AS nb_ventes,
  ROUND(AVG(v.valeur_fonciere), 0) AS prix_moyen_eur
FROM ventes v
JOIN commune_geo g ON v.code_commune = g.code_commune
JOIN commune c     ON v.code_commune = c.code_commune
WHERE (p_departement IS NULL OR c.code_departement = p_departement)
GROUP BY c.nom_commune, g.latitude, g.longitude;

-- ===================================================================
-- 7. Carte France : ventes agrégées par département, avec centroïde
--    (moyenne des centres de communes) pour poser les badges
-- ===================================================================
CREATE OR REPLACE MACRO carte_departements(p_annee := NULL) AS TABLE
WITH paires AS (
  SELECT v.*, c.code_departement
  FROM v_vente_local v
  JOIN commune c ON v.code_commune = c.code_commune
  WHERE (p_annee IS NULL OR annee = p_annee)
),
prix_m2 AS (
  SELECT code_departement, AVG(valeur_fonciere / surface_reelle_bati) AS prix_m2_moyen
  FROM paires
  GROUP BY code_departement
),
ventes AS (
  SELECT DISTINCT id_mutation, code_departement, valeur_fonciere FROM paires
),
centres AS (
  SELECT c.code_departement, AVG(g.latitude) AS latitude, AVG(g.longitude) AS longitude
  FROM commune c
  JOIN commune_geo g ON c.code_commune = g.code_commune
  GROUP BY c.code_departement
)
SELECT
  v.code_departement                   AS dept,
  COUNT(*)                             AS nb_ventes,
  ROUND(AVG(v.valeur_fonciere), 0)     AS prix_moyen_eur,
  ROUND(any_value(p.prix_m2_moyen), 0) AS prix_m2_moyen,
  any_value(ce.latitude)               AS latitude,
  any_value(ce.longitude)              AS longitude
FROM ventes v
LEFT JOIN prix_m2 p USING (code_departement)
LEFT JOIN centres ce USING (code_departement)
GROUP BY v.code_departement;

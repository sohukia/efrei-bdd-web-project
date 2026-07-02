"""
Dashboard Streamlit
"""

import json
import os
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import pydeck as pdk
import streamlit as st

DB_PATH = Path(os.environ.get("DB_PATH", "/data/foncier.db"))
DEPT_GEOJSON_PATH = DB_PATH.parent / "departements.geojson"

st.set_page_config(page_title="Valeurs foncières", page_icon="", layout="wide")


@st.cache_resource
def get_connection(db_mtime: float) -> duckdb.DuckDBPyConnection:
  return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_data
def query(sql: str, db_mtime: float, params: tuple = ()):
  return get_connection(db_mtime).cursor().execute(sql, list(params)).df()


def fr_int(value) -> str:
  return f"{int(value):,}".replace(",", " ")


def fr_num(value) -> str:
  return "–" if pd.isna(value) else fr_int(value)


def badge(nb: int) -> str:
  """Comptage compact pour les pastilles de la carte : 243662 -> '244 k'."""
  return f"{round(nb / 1000)} k" if nb >= 1000 else str(int(nb))


def couleur_intensite(ratio: float) -> list[int]:
  """Dégradé jaune pâle -> rouge sombre selon l'intensité (0..1)."""
  pale, sombre = (254, 224, 139), (215, 48, 39)
  return [round(p + (s - p) * ratio) for p, s in zip(pale, sombre)] + [190]


@st.cache_data
def load_departements_geojson(geojson_mtime: float) -> dict:
  return json.loads(DEPT_GEOJSON_PATH.read_text(encoding="utf-8"))


st.title("Valeurs foncières (2022–2025)")

if not DB_PATH.exists():
  st.info(
    "La base de données n'est pas encore publiée : l'ingestion est "
    "probablement en cours (`docker compose logs -f elt`). "
    "Rechargez la page dans quelques instants."
  )
  st.stop()

mtime = DB_PATH.stat().st_mtime

# --- Filtres
with st.sidebar:
  st.header("Filtres")

  annees = query(
    "SELECT DISTINCT annee FROM mutation WHERE annee IS NOT NULL ORDER BY annee",
    mtime,
  )["annee"].tolist()
  annee_choix = st.selectbox("Année", ["Toutes"] + [str(a) for a in annees])
  annee = None if annee_choix == "Toutes" else int(annee_choix)

  types = query(
    'SELECT DISTINCT type_local FROM "local" WHERE type_local IS NOT NULL ORDER BY 1',
    mtime,
  )["type_local"].tolist()
  type_choix = st.selectbox("Type de bien (indicateurs)", ["Tous"] + types)
  type_bien = None if type_choix == "Tous" else type_choix

  depts = query("SELECT DISTINCT code_departement FROM commune ORDER BY 1", mtime)[
    "code_departement"
  ].tolist()
  dept_choix = st.selectbox("Département (top communes)", ["Tous"] + depts)
  departement = None if dept_choix == "Tous" else dept_choix

# --- KPI
kpi = query(
  "SELECT * FROM kpi_ventes(p_annee := ?, p_type := ?)", mtime, (annee, type_bien)
).iloc[0]

if not kpi["nb_ventes"]:
  st.warning("Aucune vente ne correspond aux filtres sélectionnés.")
  st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Ventes", fr_int(kpi["nb_ventes"]))
col2.metric("Volume total", f"{kpi['volume_total_eur'] / 1e9:.1f} Md€")
col3.metric("Prix médian", f"{fr_int(kpi['prix_median_eur'])} €")
col4.metric("Prix m² médian", f"{fr_int(kpi['prix_m2_median_eur'])} €/m²")

# --- Évolution et répartition
left, right = st.columns(2)

with left:
  st.subheader("Prix moyen au m² par année et type de bien")
  evolution = query("SELECT * FROM v_prix_m2_evolution", mtime)
  evolution["annee"] = evolution["annee"].astype(str)
  st.bar_chart(
    evolution,
    x="annee",
    y="prix_m2_moyen",
    color="type_bien",
    stack=False,
    x_label="",
    y_label="€ / m²",
  )

with right:
  st.subheader("Répartition des ventes par type de bien")
  repartition = query("SELECT * FROM repartition_types(p_annee := ?)", mtime, (annee,))
  pie = (
    alt.Chart(repartition)
    .mark_arc(innerRadius=60)
    .encode(
      theta=alt.Theta("nb_transactions:Q"),
      color=alt.Color("type_bien:N", title="Type de bien"),
      tooltip=[
        alt.Tooltip("type_bien:N", title="Type"),
        alt.Tooltip("nb_transactions:Q", title="Transactions", format=","),
        alt.Tooltip("part_pct:Q", title="Part (%)"),
      ],
    )
  )
  st.altair_chart(pie)

# --- Top communes et volume annuel
left, right = st.columns(2)

with left:
  titre_dept = f"département {departement}" if departement else "France entière"
  st.subheader(f"Top 10 communes par nombre de ventes ({titre_dept})")
  top = query(
    "SELECT * FROM top_communes(p_departement := ?, p_annee := ?)",
    mtime,
    (departement, annee),
  )
  st.dataframe(
    top,
    hide_index=True,
    width="stretch",
    column_config={
      "commune": "Commune",
      "dept": "Dépt",
      "nb_ventes": st.column_config.NumberColumn("Ventes", format="localized"),
      "prix_moyen_eur": st.column_config.NumberColumn("Prix moyen", format="euro"),
      "prix_m2_moyen": st.column_config.NumberColumn("Prix m² moyen", format="euro"),
    },
  )

with right:
  st.subheader("Mutations par année (toutes natures)")
  mutations = query("SELECT * FROM v_mutations_par_annee", mtime)
  mutations["annee"] = mutations["annee"].astype(str)
  st.bar_chart(mutations, x="annee", y="mutations", x_label="", y_label="")

# --- Carte des ventes : choroplèthe des départements (France entière)
# ou détail par commune quand un département est sélectionné
if departement is None:
  st.subheader("Ventes par département")
  depts_stats = query("SELECT * FROM carte_departements(p_annee := ?)", mtime, (annee,))

  if DEPT_GEOJSON_PATH.exists() and not depts_stats.empty:
    geojson = load_departements_geojson(DEPT_GEOJSON_PATH.stat().st_mtime)
    stats = depts_stats.set_index("dept")
    max_ventes = float(depts_stats["nb_ventes"].max())

    for feature in geojson["features"]:
      code = feature["properties"].get("code")
      if code in stats.index:
        ligne = stats.loc[code]
        ratio = (ligne["nb_ventes"] / max_ventes) ** 0.5
        feature["properties"].update(
          ventes_fmt=fr_int(ligne["nb_ventes"]),
          prix_fmt=fr_num(ligne["prix_moyen_eur"]),
          prix_m2_fmt=fr_num(ligne["prix_m2_moyen"]),
          fill_color=couleur_intensite(ratio),
        )
      else:
        feature["properties"].update(
          ventes_fmt="0",
          prix_fmt="–",
          prix_m2_fmt="–",
          fill_color=[210, 210, 210, 90],
        )

    pastilles = depts_stats.dropna(subset=["latitude", "longitude"]).copy()
    pastilles["texte"] = pastilles["nb_ventes"].map(badge)

    couches = [
      pdk.Layer(
        "GeoJsonLayer",
        data=geojson,
        get_fill_color="properties.fill_color",
        get_line_color=[255, 255, 255, 130],
        line_width_min_pixels=1,
        pickable=True,
        auto_highlight=True,
      ),
      pdk.Layer(
        "ScatterplotLayer",
        data=pastilles,
        get_position="[longitude, latitude]",
        radius_min_pixels=15,
        radius_max_pixels=15,
        get_fill_color=[255, 255, 255, 230],
        get_line_color=[120, 120, 120, 255],
        line_width_min_pixels=1,
        stroked=True,
        pickable=False,
      ),
      pdk.Layer(
        "TextLayer",
        data=pastilles,
        get_position="[longitude, latitude]",
        get_text="texte",
        get_size=12,
        get_color=[50, 50, 50, 255],
        pickable=False,
      ),
    ]
    st.pydeck_chart(
      pdk.Deck(
        layers=couches,
        initial_view_state=pdk.ViewState(latitude=46.6, longitude=2.4, zoom=4.8),
        tooltip={
          "html": "<b>{nom} ({code})</b><br/>"
          "Ventes : {ventes_fmt}<br/>"
          "Prix moyen : {prix_fmt} €<br/>"
          "Prix m² moyen : {prix_m2_fmt} €/m²"
        },
      )
    )
    st.caption(
      "Couleur et pastilles : nombre de ventes par département. "
      "Sélectionnez un département dans la sidebar pour le détail par commune."
    )
  else:
    st.info(
      "Contours des départements indisponibles : relancez "
      "`docker compose run --rm elt` pour les télécharger."
    )
else:
  st.subheader(f"Ventes par commune (département {departement})")
  communes = query(
    "SELECT * FROM carte_communes(p_annee := ?, p_departement := ?)",
    mtime,
    (annee, departement),
  )
  if communes.empty:
    st.info(
      "Coordonnées des communes indisponibles : relancez `docker compose run --rm elt`."
    )
  else:
    communes["ventes_fmt"] = communes["nb_ventes"].map(fr_int)
    communes["prix_fmt"] = communes["prix_moyen_eur"].map(fr_num)
    # Rayon en mètres proportionnel à la racine des ventes (aire ~ volume)
    communes["rayon"] = (communes["nb_ventes"] ** 0.5) * 120
    couche = pdk.Layer(
      "ScatterplotLayer",
      data=communes,
      get_position="[longitude, latitude]",
      get_radius="rayon",
      radius_min_pixels=4,
      radius_max_pixels=45,
      get_fill_color=[230, 120, 40, 170],
      pickable=True,
      auto_highlight=True,
    )
    st.pydeck_chart(
      pdk.Deck(
        layers=[couche],
        initial_view_state=pdk.ViewState(
          latitude=communes["latitude"].mean(),
          longitude=communes["longitude"].mean(),
          zoom=8,
        ),
        tooltip={
          "html": "<b>{commune}</b><br/>"
          "Ventes : {ventes_fmt}<br/>"
          "Prix moyen : {prix_fmt} €"
        },
      )
    )
    st.caption(
      f"{fr_int(len(communes))} communes géolocalisées — le rayon reflète "
      "le nombre de ventes."
    )

st.caption(
  "Sources : [Valeurs foncières]"
  "(https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/) — "
  "data.gouv.fr, années 2022 à 2025 ; centres des communes via "
  "[geo.api.gouv.fr](https://geo.api.gouv.fr)."
)

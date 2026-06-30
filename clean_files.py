import csv
import os
import re


def process_line(row: dict) -> dict:
  """Map raw fields to target fields and perform basic transformations."""
  # Build unique identifiers based on standard DVF definitions
  # id_parcelle: Code departement (2) + Code commune (3) + Prefixe de section (3, default 000) + Section (2) + No plan (4, zero-padded)
  dept = row.get("Code departement", "").strip()
  comm_code = row.get("Code commune", "").strip()
  prefix = row.get("Prefixe de section", "").strip() or "000"
  section = row.get("Section", "").strip()
  num_plan = row.get("No plan", "").strip()

  # Pad section and plan number to match standard 15-character cadastral parcel IDs
  if section:
    section = section.zfill(2)
  if num_plan:
    num_plan = num_plan.zfill(4)

  id_parcelle = (
    f"{dept}{comm_code}{prefix}{section}{num_plan}"
    if (dept and comm_code and section and num_plan)
    else ""
  )

  # Extract year from date (format is typically DD/MM/YYYY)
  date_mut = row.get("Date mutation", "").strip()
  annee = ""
  if date_mut:
    match = re.search(r"\d{4}", date_mut)
    if match:
      annee = match.group(0)

  # Reconstruct address string
  no_voie = row.get("No voie", "").strip()
  btq = row.get("B/T/Q", "").strip()
  type_voie = row.get("Type de voie", "").strip()
  voie = row.get("Voie", "").strip()
  address_parts = [no_voie, btq, type_voie, voie]
  adresse_ = " ".join([p for p in address_parts if p]).strip()

  # Generate a unique mutation id using Document Reference and Disposition Number if available
  ref_doc = row.get("Reference document", "").strip()
  no_disp = row.get("No disposition", "").strip()
  id_mutation = f"{ref_doc}_{no_disp}" if (ref_doc and no_disp) else ref_doc

  return {
    "code_commune": f"{dept}{comm_code}" if (dept and comm_code) else "",
    "nom_commune": row.get("Commune", "").strip(),
    "code_departement": dept,
    "code_postal": row.get("Code postal", "").strip(),
    "id_parcelle": id_parcelle,
    "id_adresse": "",  # Requires external geocoding framework to map to BAN (Base Adresse Nationale)
    "adresse_": adresse_,
    "id_mutation": id_mutation,
    "annee": annee,
    "date_mutation": date_mut,
    "nature_mutation": row.get("Nature mutation", "").strip(),
    "valeur_fonciere": row.get("Valeur fonciere", "").strip().replace(",", "."),
    "id_local": row.get("Identifiant local", "").strip(),
    "type_local": row.get("Type local", "").strip(),
    "surface_reelle_bati": row.get("Surface reelle bati", "").strip(),
    "nombre_pieces_principales": row.get("Nombre pieces principales", "").strip(),
  }


def convert_dvf_file(input_path: str, output_path: str):
  """Reads pipe-delimited DVF file and writes structured comma-delimited CSV."""
  fieldnames_out = [
    "code_commune",
    "nom_commune",
    "code_departement",
    "code_postal",
    "id_parcelle",
    "id_adresse",
    "adresse_",
    "id_mutation",
    "annee",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "id_local",
    "type_local",
    "surface_reelle_bati",
    "nombre_pieces_principales",
  ]

  with open(input_path, mode="r", encoding="utf-8", errors="replace") as infile:
    # DVF source files use vertical bar character as delimiter
    reader = csv.DictReader(infile, delimiter="|")
    with open(output_path, mode="w", encoding="utf-8", newline="") as outfile:
      writer = csv.DictWriter(outfile, fieldnames=fieldnames_out, delimiter=",")
      writer.writeheader()
      for row in reader:
        processed_row = process_line(row)
        writer.writerow(processed_row)


if __name__ == "__main__":
  convert_dvf_file("valeursfoncieres-2024.txt", "processed_valeursfoncieres-2024.csv")
  print("2024 done")
  convert_dvf_file("valeursfoncieres-2023.txt", "processed_valeursfoncieres-2023.csv")
  print("2023 done")
  convert_dvf_file("valeursfoncieres-2022.txt", "processed_valeursfoncieres-2022.csv")
  print("2022 done")

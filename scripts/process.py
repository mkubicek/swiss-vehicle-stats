#!/usr/bin/env python3
"""Process raw ASTRA NEUZU data into aggregated CSVs.

Loads one file at a time with dtype optimization. Applies mappings.yaml
for classification. Unknown values go to "Other" bucket.
"""

import json
import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
MAPPINGS_FILE = ROOT / "mappings.yaml"
WARNINGS_FILE = ROOT / "warnings.log"

# Columns we need (by name — position varies across years)
USE_COLS = [
    "Fahrzeugart",
    "Marke",
    "Treibstoff",
    "Farbe",
    "Schildfarbe",
    "Antrieb",
    "Erstinverkehrsetzung_Jahr",
    "Erstinverkehrsetzung_Monat",
    "Erstinverkehrsetzung_Kanton",
]

# Fuel types that count as EV (BEV + PHEV + FCEV)
EV_FUELS = {"BEV", "PHEV", "Hydrogen"}
BEV_FUELS = {"BEV"}


def load_mappings() -> dict:
    with open(MAPPINGS_FILE) as f:
        return yaml.safe_load(f)


def safe_map(value, mapping: dict, default: str = "Other") -> str:
    """Map a value using a dictionary, returning default if not found."""
    if pd.isna(value):
        return default
    v = str(value).strip()
    # Try exact match first (case-sensitive for fuel types with special chars)
    if v in mapping:
        return mapping[v]
    # Try case-insensitive for brand names
    v_upper = v.upper()
    for key, val in mapping.items():
        if str(key).upper() == v_upper:
            return val
    return default


def find_raw_files() -> list[Path]:
    """Find all NEUZU*.txt files in raw directory, sorted."""
    if not RAW_DIR.exists():
        print(f"ERROR: {RAW_DIR} does not exist. Run download.py first.")
        raise SystemExit(1)
    files = sorted(RAW_DIR.glob("NEUZU*.txt"))
    if not files:
        print(f"ERROR: No NEUZU*.txt files in {RAW_DIR}. Run download.py first.")
        raise SystemExit(1)
    return files


def detect_separator(filepath: Path) -> str:
    """Auto-detect TSV vs CSV."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        header = f.readline()
    return "\t" if "\t" in header else ","


def process_file(filepath: Path, mappings: dict, warnings: set) -> dict:
    """Process a single NEUZU file. Returns aggregation dicts."""
    sep = detect_separator(filepath)
    print(f"  Processing: {filepath.name}")

    # Check which columns exist in this file
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        header_cols = [c.strip() for c in f.readline().split(sep)]

    # Extract Datenstand (data-as-of date) if present
    datenstand = None
    if "Datenstand" in header_cols:
        ds_idx = header_cols.index("Datenstand")
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            f.readline()  # skip header
            first_row = f.readline().strip().split(sep)
            if ds_idx < len(first_row):
                raw = first_row[ds_idx].strip()
                try:
                    datenstand = datetime.strptime(raw, "%d.%m.%Y").strftime("%Y-%m-%d")
                except ValueError:
                    pass

    # Normalize known ASTRA typos (2016-2018 files have "Erstinvekehrsetzung_Kanton")
    col_renames = {}
    for hc in header_cols:
        if hc not in USE_COLS:
            for uc in USE_COLS:
                if hc.lower().replace("vek", "verk") == uc.lower() or \
                   (hc.startswith("Erstinv") and "kanton" in hc.lower() and "Kanton" in uc):
                    col_renames[hc] = uc
                    break

    available_cols = [c for c in USE_COLS if c in header_cols or c in col_renames.values()]
    # For loading, use actual header names
    load_cols = []
    for c in available_cols:
        inv = {v: k for k, v in col_renames.items()}
        load_cols.append(inv.get(c, c))

    missing = set(USE_COLS) - set(available_cols)
    if missing:
        print(f"    Missing columns: {missing}")
    if col_renames:
        print(f"    Fixed column typos: {col_renames}")

    # Load full file with dtype optimization
    dtype_map = {c: "category" for c in load_cols if c not in ("Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat") and col_renames.get(c, c) not in ("Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat")}
    dtype_map.update({c: "Int16" for c in load_cols if col_renames.get(c, c) in ("Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat")})

    try:
        df = pd.read_csv(
            filepath, sep=sep, usecols=load_cols, dtype=dtype_map,
            encoding="utf-8", on_bad_lines="skip",
        )
        # Apply column renames
        if col_renames:
            df = df.rename(columns=col_renames)
    except Exception as e:
        print(f"    ERROR: {e}")
        return {}

    # Filter to Personenwagen
    if "Fahrzeugart" in df.columns:
        df = df[df["Fahrzeugart"].astype(str).str.contains("Personenwagen", case=False, na=False)]

    print(f"    Personenwagen: {len(df):,}")
    if df.empty:
        return {}

    agg = {}
    m = mappings

    # Year/month
    if "Erstinverkehrsetzung_Jahr" in df.columns and "Erstinverkehrsetzung_Monat" in df.columns:
        df["_year"] = df["Erstinverkehrsetzung_Jahr"]
        df["_month"] = df["Erstinverkehrsetzung_Monat"]
    else:
        df["_year"] = pd.NA
        df["_month"] = pd.NA

    # Fuel type
    if "Treibstoff" in df.columns:
        df["_fuel"] = df["Treibstoff"].apply(lambda x: safe_map(x, m.get("fuel_types", {})))
        for v in df["Treibstoff"].dropna().unique():
            if safe_map(v, m.get("fuel_types", {})) == "Other" and str(v).strip():
                warnings.add(f"fuel:{v}")

    # Brand
    if "Marke" in df.columns:
        df["_brand"] = df["Marke"].astype(str).str.strip()
        df["_origin"] = df["Marke"].apply(lambda x: safe_map(x, m.get("brand_origin", {})))
        df["_group"] = df["Marke"].apply(lambda x: safe_map(x, m.get("brand_group", {})))
        df["_continent"] = df["_origin"].apply(lambda x: safe_map(x, m.get("country_continent", {})))
        for v in df["Marke"].dropna().unique():
            if safe_map(v, m.get("brand_origin", {})) == "Other" and str(v).strip():
                warnings.add(f"brand:{v}")

    # Color
    if "Farbe" in df.columns:
        df["_color"] = df["Farbe"].apply(lambda x: safe_map(x, m.get("colors", {})))
        for v in df["Farbe"].dropna().unique():
            if safe_map(v, m.get("colors", {})) == "Other" and str(v).strip():
                warnings.add(f"color:{v}")

    # Usage (plate color)
    if "Schildfarbe" in df.columns:
        df["_usage"] = df["Schildfarbe"].apply(lambda x: safe_map(x, m.get("plate_usage", {})))

    # Drive type (4x4)
    if "Antrieb" in df.columns:
        df["_drive"] = df["Antrieb"].apply(lambda x: safe_map(x, m.get("drive_types", {})))

    # Canton
    if "Erstinverkehrsetzung_Kanton" in df.columns:
        df["_canton"] = df["Erstinverkehrsetzung_Kanton"].astype(str).str.strip()

    # EV flags (derived from fuel type)
    if "_fuel" in df.columns:
        df["_is_ev"] = df["_fuel"].isin(EV_FUELS)
        df["_is_bev"] = df["_fuel"].isin(BEV_FUELS)

    # --- Aggregations ---
    valid = df.dropna(subset=["_year", "_month"])

    if not valid.empty:
        # Monthly totals
        agg["monthly_totals"] = valid.groupby(["_year", "_month"]).size().reset_index(name="count")

        # Fuel by month
        if "_fuel" in valid.columns:
            agg["fuel_by_month"] = valid.groupby(["_year", "_month", "_fuel"]).size().reset_index(name="count")

        # Brand by year (for winners/losers)
        if "_brand" in valid.columns:
            agg["brand_by_year"] = valid.groupby(["_year", "_brand"]).size().reset_index(name="count")

        # Canton EV by month (for ev_wave)
        if "_canton" in valid.columns and "_is_ev" in valid.columns:
            canton_grp = valid.groupby(["_canton", "_year", "_month"])
            canton_total = canton_grp.size().reset_index(name="total_count")
            canton_ev = canton_grp["_is_ev"].sum().reset_index(name="ev_count")
            agg["canton_ev_by_month"] = canton_total.merge(
                canton_ev, on=["_canton", "_year", "_month"]
            )

        # Brand BEV by month (for ev_race)
        if "_brand" in valid.columns and "_is_bev" in valid.columns:
            bev_only = valid[valid["_is_bev"]]
            if not bev_only.empty:
                agg["brand_bev_by_month"] = (
                    bev_only.groupby(["_year", "_month", "_brand"])
                    .size().reset_index(name="bev_count")
                )

        # Brand canton BEV by month (for ev_taste LQ)
        if "_canton" in valid.columns and "_brand" in valid.columns and "_is_bev" in valid.columns:
            bev_only = valid[valid["_is_bev"]]
            if not bev_only.empty:
                agg["brand_canton_bev"] = (
                    bev_only.groupby(["_canton", "_brand", "_year", "_month"])
                    .size().reset_index(name="bev_count")
                )

    # Totals (all rows, not just date-valid)
    if "_fuel" in df.columns:
        agg["fuel_totals"] = df["_fuel"].value_counts().reset_index()
        agg["fuel_totals"].columns = ["fuel_type", "count"]

    if "_brand" in df.columns:
        agg["brand_totals"] = df["_brand"].value_counts().reset_index()
        agg["brand_totals"].columns = ["brand", "count"]

    if "_origin" in df.columns:
        agg["origin_totals"] = df["_origin"].value_counts().reset_index()
        agg["origin_totals"].columns = ["country", "count"]

    if "_continent" in df.columns:
        agg["continent_totals"] = df["_continent"].value_counts().reset_index()
        agg["continent_totals"].columns = ["continent", "count"]

    if "_group" in df.columns:
        agg["group_totals"] = df["_group"].value_counts().reset_index()
        agg["group_totals"].columns = ["group", "count"]

    if "_color" in df.columns:
        agg["color_totals"] = df["_color"].value_counts().reset_index()
        agg["color_totals"].columns = ["color", "count"]

    if "_usage" in df.columns:
        agg["usage_totals"] = df["_usage"].value_counts().reset_index()
        agg["usage_totals"].columns = ["usage", "count"]

    if "_drive" in df.columns:
        agg["drive_totals"] = df["_drive"].value_counts().reset_index()
        agg["drive_totals"].columns = ["drive", "count"]

        # Drive by month
        if not valid.empty:
            agg["drive_by_month"] = valid.groupby(["_year", "_month", "_drive"]).size().reset_index(name="count")

    if datenstand:
        agg["_datenstand"] = datenstand

    return agg


def merge_aggs(total: dict, new: dict) -> dict:
    """Merge two aggregation dicts by concatenating DataFrames."""
    for key, value in new.items():
        if key == "_datenstand":
            total[key] = value  # last file wins
        elif key in total:
            total[key] = pd.concat([total[key], value], ignore_index=True)
        else:
            total[key] = value
    return total


def consolidate_and_save(agg: dict):
    """Consolidate merged DataFrames and save to CSV."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Monthly totals — sum duplicates
    if "monthly_totals" in agg:
        df = agg["monthly_totals"].groupby(["_year", "_month"])["count"].sum().reset_index()
        df.columns = ["year", "month", "count"]
        df = df.sort_values(["year", "month"])
        df.to_csv(OUT_DIR / "monthly_totals.csv", index=False)

    # Fuel by month
    if "fuel_by_month" in agg:
        df = agg["fuel_by_month"].groupby(["_year", "_month", "_fuel"])["count"].sum().reset_index()
        df.columns = ["year", "month", "fuel_type", "count"]
        df = df.sort_values(["year", "month", "fuel_type"])
        df.to_csv(OUT_DIR / "fuel_by_month.csv", index=False)

    # Brand by year
    if "brand_by_year" in agg:
        df = agg["brand_by_year"].groupby(["_year", "_brand"])["count"].sum().reset_index()
        df.columns = ["year", "brand", "count"]
        df = df.sort_values(["year", "brand"])
        df.to_csv(OUT_DIR / "brand_by_year.csv", index=False)

    # Simple totals — group and sum
    for name in ["fuel_totals", "brand_totals", "origin_totals", "continent_totals",
                  "group_totals", "color_totals", "usage_totals", "drive_totals"]:
        if name in agg:
            col = agg[name].columns[0]
            df = agg[name].groupby(col)["count"].sum().reset_index().sort_values("count", ascending=False)
            df.to_csv(OUT_DIR / f"{name}.csv", index=False)

    # Drive by month
    if "drive_by_month" in agg:
        df = agg["drive_by_month"].groupby(["_year", "_month", "_drive"])["count"].sum().reset_index()
        df.columns = ["year", "month", "drive", "count"]
        df = df.sort_values(["year", "month", "drive"])
        df.to_csv(OUT_DIR / "drive_by_month.csv", index=False)

    # Canton EV by month
    if "canton_ev_by_month" in agg:
        df = agg["canton_ev_by_month"].groupby(["_canton", "_year", "_month"])[["ev_count", "total_count"]].sum().reset_index()
        df.columns = ["canton", "year", "month", "ev_count", "total_count"]
        df = df.sort_values(["canton", "year", "month"])
        df.to_csv(OUT_DIR / "canton_ev_by_month.csv", index=False)

    # Brand BEV by month
    if "brand_bev_by_month" in agg:
        df = agg["brand_bev_by_month"].groupby(["_year", "_month", "_brand"])["bev_count"].sum().reset_index()
        df.columns = ["year", "month", "brand", "bev_count"]
        df = df.sort_values(["year", "month", "brand"])
        df.to_csv(OUT_DIR / "brand_bev_by_month.csv", index=False)

    # Brand canton BEV (for LQ chart)
    if "brand_canton_bev" in agg:
        df = agg["brand_canton_bev"].groupby(["_canton", "_brand", "_year", "_month"])["bev_count"].sum().reset_index()
        df.columns = ["canton", "brand", "year", "month", "bev_count"]
        df = df.sort_values(["canton", "brand", "year", "month"])
        df.to_csv(OUT_DIR / "brand_canton_bev.csv", index=False)

    # Write metadata
    metadata = {}
    if "_datenstand" in agg:
        metadata["data_date"] = agg["_datenstand"]
    if metadata:
        with open(OUT_DIR / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        print(f"  Wrote metadata.json: {metadata}")

    print(f"\nSaved CSVs to {OUT_DIR}/")


def save_warnings(warnings: set):
    """Save unmapped values to warnings.log (validate.py will merge and enrich)."""
    if not warnings:
        print("No unmapped values.")
        return
    with open(WARNINGS_FILE, "w") as f:
        f.write(f"# Unmapped values — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("# Add these to mappings.yaml to classify them properly.\n\n")
        for w in sorted(warnings):
            f.write(f"{w}\n")
    print(f"\nUnmapped: {len(warnings)} values -> {WARNINGS_FILE}")
    print("Run validate.py for plausibility checks.")


def main():
    print("=== ASTRA Data Processing ===\n")
    mappings = load_mappings()
    files = find_raw_files()
    warnings: set = set()
    total_agg: dict = {}

    for f in files:
        agg = process_file(f, mappings, warnings)
        total_agg = merge_aggs(total_agg, agg)

    consolidate_and_save(total_agg)
    save_warnings(warnings)
    print("\nDone.")


if __name__ == "__main__":
    main()

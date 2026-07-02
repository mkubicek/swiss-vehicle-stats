#!/usr/bin/env python3
"""Process raw ASTRA NEUZU data into aggregated CSVs.

Loads one file at a time with dtype optimization. Applies mappings.yaml
for classification. Unknown values go to "Other" bucket.
"""

import json
import re
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
    "Typ1",
    "Treibstoff",
    "Hybridcode",           # OVC-HEV / NOVC-HEV (2022+)
    "CO2",                  # CO2 g/km NEFZ (2016-2021)
    "CO2_WLTP",             # CO2 g/km WLTP (2022 only, underscore)
    "CO2-WLTP",             # CO2 g/km WLTP (2023+, dash)
    "Farbe",
    "Schildfarbe",
    "Antrieb",
    "Erstinverkehrsetzung_Jahr",
    "Erstinverkehrsetzung_Monat",
    "Erstinverkehrsetzung_Kanton",
]

# Fuel types that count as EV (plug-in vehicles only; excludes non-plug-in HEV)
EV_FUELS = {"BEV", "PHEV", "Diesel PHEV", "Hydrogen"}
BEV_FUELS = {"BEV"}

# CO2 threshold for PHEV classification when Hybridcode is unavailable
CO2_PHEV_THRESHOLD = 50  # g/km — EU regulation: PHEV < 50 g/km

# Market-entry threshold for entry-aligned ramp analysis (chart_china_entry_ramp).
# A single grey/direct import (Known Limitation #3) can put one BEV on the road
# years before a brand's commercial launch; requiring ≥5 in a month (and non-zero
# follow-through) filters those one-offs. Documented in METHODOLOGY.md.
ENTRY_MIN_MONTHLY = 5

# Intermediate fuel types from mappings.yaml that need runtime resolution
_HYBRID_INTERMEDIATES = {"_petrol_hybrid", "_diesel_hybrid"}


def load_mappings() -> dict:
    with open(MAPPINGS_FILE) as f:
        return yaml.safe_load(f)


_TESLA_RE = re.compile(r"^MODEL\s*([YS3X])")
_VW_ID_RE = re.compile(r"^ID\.?\s*([0-9]+|BUZZ)")
# BMW: a series digit + two trim digits + optional fuel letters ("320D",
# "330E", "M340I", "118I"). Collapse these onto the series so BMW sits at the
# same grain as Mercedes class letters and Audi A/Q lines. Full M cars (M2-M8,
# no two-digit tail), X SUVs, i/iX electrics and the Z roadster don't match and
# stay distinct nameplates.
_BMW_SERIES_RE = re.compile(r"^M?([1-8])\d{2}[A-Z]*$")
# ASTRA pre-2022 records often concatenate engine codes onto the model name
# without a space ("OCTAVIA2.0TDI", "TUCSON1.6TGDIPHEV", "VITARA1.6"). Strip
# the trailing engine code so those rows aggregate into the real nameplate.
# The regex requires a decimal (e.g. "2.0", "1.6") after the letters, which
# is the distinguishing feature of engine displacement codes — model
# designators like X1, Q3, A4, ID.3 (handled separately) have no decimal.
_ENGINE_CODE_RE = re.compile(r"^([A-Z][A-Z\-]*)\d+\.\d+.*$")


def _strip_duplicate_brand_prefix(typ1: str, brand_prefixes: list[str]) -> str:
    """Remove ASTRA rows that repeat the brand in Typ1 ("AUDI RS6")."""
    prefixes = set()
    for prefix in brand_prefixes:
        if not prefix:
            continue
        prefixes.add(prefix)
        prefixes.add(prefix.replace(" ", "-"))
        prefixes.add(prefix.replace(" ", ""))

    for prefix in sorted(prefixes, key=len, reverse=True):
        if typ1 == prefix:
            return ""
        if typ1.startswith(prefix + " "):
            return typ1[len(prefix):].lstrip()
    return typ1


def normalize_model(brand, typ1, overrides_sorted: list[tuple[str, str]]) -> str:
    """Combine brand + Typ1 into a stable model key for chart_model_race.

    1. Try the longest-prefix override (operator-curated splits/merges from
       mappings.yaml > model_overrides).
    2. Fall back to: brand + first Typ1 token, with Tesla / VW-ID regex
       specials that tolerate ASTRA's inconsistent spacing ("MODEL Y" vs
       "MODELY", "ID.3 PRO 150 KW" vs "ID.3PROS150KW", "ID4" → "ID.4").
    Returns "" when the row has no usable model info (filtered out upstream).
    """
    if not isinstance(brand, str) or not isinstance(typ1, str):
        return ""
    b = brand.strip().upper()
    t = typ1.strip().upper()
    # Empty / NaN guard. df["col"].astype(str) renders missing values as the
    # literal string "nan", which would otherwise survive into model keys.
    if not b or not t or b == "NAN" or t == "NAN":
        return ""
    original_b = b
    # Mercedes-AMG is recorded both as its own Marke and as "AMG ..." Typ1 under
    # MERCEDES-BENZ; treat it as Mercedes-Benz so AMG cars fold into the base.
    if b == "MERCEDES-AMG":
        b = "MERCEDES-BENZ"
    # DS model names legitimately start with "DS" ("DS 7", "DS7CRB"), so do
    # not treat that prefix as a duplicated brand.
    if b != "DS":
        t = _strip_duplicate_brand_prefix(t, [original_b, b])
        if not t:
            return ""
    raw = f"{b} {t}"
    for prefix, canonical in overrides_sorted:
        if raw == prefix or raw.startswith(prefix + " "):
            return canonical

    # Tesla: "MODEL Y", "MODELY", "MODEL3" all collapse to "MODEL <X>".
    if b == "TESLA":
        match = _TESLA_RE.match(t)
        if match:
            return f"TESLA MODEL {match.group(1)}"

    # VW ID family: "ID.3 PRO 150 KW", "ID.3PROS150KW", "ID4" → "ID.<N>".
    if b == "VW":
        match = _VW_ID_RE.match(t)
        if match:
            return f"VW ID.{match.group(1)}"

    # BMW: "320D"/"M340I"/"118I" → "BMW 3/3/1 Series". Full M cars (M2-M8) fold
    # into their series too (M3 -> 3 Series). X SUVs, i/iX electrics, Z stay.
    if b == "BMW":
        bmw_tok = t.split()[0]
        match = _BMW_SERIES_RE.match(bmw_tok)
        if match:
            return f"BMW {match.group(1)} Series"
        m_full = re.match(r"^M([1-8])(?:$|[A-Z])", bmw_tok)
        if m_full:
            return f"BMW {m_full.group(1)} Series"
        m_x = re.match(r"^(X[1-7])", bmw_tok)
        if m_x:
            return f"BMW {m_x.group(1)}"
        # i / iX electrics: fold trim suffixes ("I3S" -> I3, "I4EDRIVE40" -> I4).
        m_i = re.match(r"^(IX[1-9]?|I[1-9])", bmw_tok)
        if m_i:
            return f"BMW {m_i.group(1)}"

    # Mercedes-Benz: model names are class letters (A/B/C/E/S/G/V) or letter
    # groups (GLA/GLC/CLA/EQE/SL/...); the digits are ALWAYS engine displacement.
    # So the model is the leading alphabetic run. AMG folds into the same base
    # ("AMG C 63" -> C, "AMG GLC 43" -> GLC, "AMG GT 63" -> GT; "GLA200" -> GLA,
    # "V250D" -> V). The base then routes through model_merges / model_segments.
    if b == "MERCEDES-BENZ":
        tok = t
        if tok.startswith("Z "):          # stray "Z " prefix on some AMG rows
            tok = tok[2:].lstrip()
        if tok.startswith("AMG"):
            tok = tok[3:].lstrip(" -")
        base = re.match(r"[A-Z]+", tok)
        return f"MERCEDES-BENZ {base.group()}" if base else ""

    # Lexus: 2-3 letter model names (RX/NX/UX/ES/LC/LBX/RZ...), digits are always
    # engine/hybrid trim. "RX450H" -> RX, "NX450H+" -> NX, "UX250H" -> UX.
    if b == "LEXUS":
        base = re.match(r"[A-Z]+", t.split()[0])
        if base:
            return f"LEXUS {base.group()}"

    # Audi RS/S performance fold into the base A/Q line ("RS 3"/"S3" -> A3,
    # "RS Q8"/"SQ7" -> Q8/Q7). The e-tron GT sport sedan is distinct from the
    # e-tron SUV; R8 is a standalone supercar (falls through). The final A/Q rule
    # catches concatenated forms ("Q445E-TRON" -> Q4, "A4 Avant" -> A4).
    if b == "AUDI":
        atok = t.split()[0]
        if "E-TRON GT" in t or "E-TRONGT" in t.replace(" ", ""):
            return "AUDI E-TRON GT"
        # Q8 e-tron is the electric SUV (the renamed original "e-tron"); it must
        # NOT merge with the combustion Q8. Route it to the e-tron key. Q4/Q6
        # e-tron are EV-only (no combustion twin) so they keep their Q key.
        if re.match(r"^S?Q8", atok) and "E-TRON" in t.replace(" ", ""):
            return "AUDI E-TRON"
        if atok.startswith("TT"):           # TT / TTS / TTRS
            return "AUDI TT"
        m_rs = re.match(r"^RS\s*(Q)?\s*([1-8])", t)
        if m_rs:
            return f"AUDI {'Q' if m_rs.group(1) else 'A'}{m_rs.group(2)}"
        m_s = re.match(r"^S(Q)?([1-8])", atok)
        if m_s:
            return f"AUDI {'Q' if m_s.group(1) else 'A'}{m_s.group(2)}"
        m_aq = re.match(r"^(A|Q)([1-8])", atok)
        if m_aq:
            return f"AUDI {m_aq.group(1)}{m_aq.group(2)}"

    # Mini: ASTRA mixes body-style and trim-first names ("3DOOR COOPER S",
    # "COOPER S CLUBMAN", "COUNTRYMAN SE ALL4"). Keep the chart at nameplate
    # grain so a naming-era shift from 3DOOR/5DOOR to Cooper doesn't look like
    # a model collapsing while another one debuts.
    if b == "MINI":
        compact = t.replace(" ", "")
        if "COUNTRYMAN" in compact or "CONUNTRYMAN" in compact:
            return "MINI COUNTRYMAN"
        if "CLUBMAN" in compact:
            return "MINI CLUBMAN"
        if "ACEMAN" in compact:
            return "MINI ACEMAN"
        if "CABRIO" in compact:
            return "MINI CABRIO"
        if compact.startswith(("3DOOR", "5DOOR", "COOPER", "ONE", "JCW", "JOHN")):
            return "MINI COOPER"

    # DS: "DS7 Crossback ..." / "DS7CRB.E-TENSE4X4" -> DS DS7 (number = model).
    if b == "DS":
        m_ds = re.match(r"^(?:DS\s*)?([0-9])", t)
        if m_ds:
            return f"DS DS{m_ds.group(1)}"

    # Land Rover Range Rover family: ASTRA writes it as "RR ..." / "RANGE ROVER
    # ..." and the sub-model is a keyword. These span THREE segments, so split
    # them: Evoque (Compact SUV), Velar (Mid), Sport + full-size (Large).
    if b == "LAND ROVER" and (t.startswith("RR") or t.split()[0].startswith("RANGE")):
        if "EVOQUE" in t:
            return "LAND ROVER EVOQUE"
        if "VELAR" in t:
            return "LAND ROVER VELAR"
        # ASTRA abbreviates Range Rover Sport as "RR SP." / "RR SP" (no "SPORT").
        if "SPORT" in t or re.search(r"\bSP\b", t):
            return "LAND ROVER RANGE ROVER SPORT"
        return "LAND ROVER RANGE ROVER"

    # Porsche: ASTRA often concatenates trims/body styles to the model token
    # ("911CARRERA", "TAYCAN4S", "CAYENNET", "718SPYDERRS"). Keep these at
    # nameplate grain so sports/luxury segment volumes don't fragment.
    if b == "PORSCHE":
        ptok = t.split()[0]
        if ptok.startswith("911"):
            return "PORSCHE 911"
        if ptok.startswith("718"):
            return "PORSCHE 718"
        for prefix in ["MACAN", "CAYENNE", "PANAMERA", "TAYCAN", "BOXSTER", "CAYMAN"]:
            if ptok.startswith(prefix):
                return f"PORSCHE {prefix}"

    # Cupra: ASTRA concatenates trims/batteries ("FORMENTORE-HYBRID",
    # "BORN170KW...", "LEONSP") and recorded Cupra as a SEAT trim before it
    # became its own Marke. Fold to the nameplate; the proper-case label matches
    # the SEAT-era model_overrides so both recording eras unify into one key.
    if b == "CUPRA":
        ctok = t.split()[0]
        for name in ["FORMENTOR", "TERRAMAR", "TAVASCAN", "ATECA", "LEON", "BORN"]:
            if ctok.startswith(name):
                return f"Cupra {name.title()}"

    first = t.split()[0].rstrip(",.")
    # Punctuation-only Typ1 (".", ",") strips to nothing — no usable model
    # info, so drop the row rather than emit a malformed "BRAND " key.
    if not first:
        return ""
    # Strip concatenated engine codes ("OCTAVIA2.0TDI" -> "OCTAVIA").
    engine_match = _ENGINE_CODE_RE.match(first)
    if engine_match:
        first = engine_match.group(1)
    return f"{b} {first}"


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


# --- Ownership classification (owner_country dimension) -------------------
# Two orthogonal blocs for the Chinese-BEV charts (see METHODOLOGY.md):
#   China-branded = brand_origin == China          (heritage)
#   China-owned   = owner_country == China OR brand_origin == China (ownership)
# The union guarantees China-branded ⊆ China-owned, so the share lines never
# cross. Classification is data-driven from mappings.yaml — no logic here beyond
# reading the two maps.

def resolve_origin(brand, mappings: dict) -> str:
    """Brand heritage country (or 'Other' if unmapped)."""
    return safe_map(brand, mappings.get("brand_origin", {}))


def owner_country_or_none(brand, mappings: dict):
    """Ultimate controlling-shareholder country, or None if the brand has no
    explicit brand_owner_country entry. Used by validate.py to flag gaps."""
    m = mappings.get("brand_owner_country", {})
    result = safe_map(brand, m, default="__missing__")
    return None if result == "__missing__" else result


def is_china_branded(brand, mappings: dict) -> bool:
    """True when the brand's heritage is Chinese (brand_origin == China)."""
    return resolve_origin(brand, mappings) == "China"


def is_china_owned(brand, mappings: dict) -> bool:
    """True when the brand is ultimately controlled from China. Union of the
    explicit owner_country and the heritage-China set, so any Chinese-heritage
    brand counts as China-owned even without an explicit owner_country entry."""
    return owner_country_or_none(brand, mappings) == "China" or is_china_branded(brand, mappings)


# Manufacturer blocs for the displacement chart (chart_bev_bloc_share). The
# partition is mutually exclusive and exhaustive: every brand resolves to
# exactly one bloc, and any brand without a mapped origin falls through to
# "Other" (already surfaced by the existing brand-origin unmapped warning).
BLOC_CHINA_OWNED = "China-owned"
BLOC_TESLA = "Tesla"
BLOC_VW = "Volkswagen Group"
BLOC_EU_LEGACY = "European legacy"
BLOC_KOREAN = "Korean"
BLOC_JAPANESE = "Japanese"
BLOC_OTHER = "Other"
BLOC_ORDER = [BLOC_TESLA, BLOC_VW, BLOC_EU_LEGACY, BLOC_KOREAN, BLOC_JAPANESE,
              BLOC_OTHER, BLOC_CHINA_OWNED]


def bloc(brand, mappings: dict) -> str:
    """Resolve a brand to exactly one manufacturer bloc (see BLOC_ORDER).

    Resolution order (first match wins), so the partition is deterministic and
    the China-owned bloc absorbs Volvo/Polestar/Smart/MG before the European
    fallthrough can claim them (consistent with the owner_country dimension):
      1. China-owned  (owner_country == China OR origin == China)
      2. Tesla
      3. Volkswagen Group  (brand_group)
      4. Korean / Japanese  (brand_origin)
      5. European legacy  (any remaining Europe-origin brand)
      6. Other
    """
    if is_china_owned(brand, mappings):
        return BLOC_CHINA_OWNED
    if str(brand).strip().upper() == "TESLA":
        return BLOC_TESLA
    if safe_map(brand, mappings.get("brand_group", {})) == "Volkswagen Group":
        return BLOC_VW
    origin = resolve_origin(brand, mappings)
    if origin == "South Korea":
        return BLOC_KOREAN
    if origin == "Japan":
        return BLOC_JAPANESE
    if safe_map(origin, mappings.get("country_continent", {})) == "Europe":
        return BLOC_EU_LEGACY
    return BLOC_OTHER


def detect_entry_month(series):
    """First sustained market-entry month for a brand's monthly BEV series.

    ``series`` is an iterable of ``(year, month, count)`` in chronological order.
    Entry = the first month with ``count >= ENTRY_MIN_MONTHLY`` whose following
    three months are not all zero (the grey-import guard — a lone spike that
    immediately drops back to zero is not a market entry). Returns ``(year,
    month)`` or ``None`` if the brand never sustains the threshold.
    """
    rows = list(series)
    for i, (y, m, count) in enumerate(rows):
        if count is None or count < ENTRY_MIN_MONTHLY:
            continue
        nxt = rows[i + 1:i + 4]
        if not nxt or any((r[2] or 0) > 0 for r in nxt):
            return (int(y), int(m))
    return None


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
    _int_cols = {"Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat"}
    _str_cols = {"Hybridcode", "CO2", "CO2_WLTP", "CO2-WLTP"}  # convert later
    dtype_map = {}
    for c in load_cols:
        canonical = col_renames.get(c, c)
        if canonical in _int_cols:
            dtype_map[c] = "Int16"
        elif canonical in _str_cols:
            dtype_map[c] = "object"
        else:
            dtype_map[c] = "category"

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

        # Resolve _petrol_hybrid / _diesel_hybrid into PHEV vs HEV
        # using Hybridcode (2022+) with CO2 fallback (pre-2022)
        is_hyb = df["_fuel"].isin(_HYBRID_INTERMEDIATES)
        if is_hyb.any():
            # Unify CO2 columns (different names across years)
            co2 = pd.Series(float("nan"), index=df.index, dtype="float32")
            for col in ["CO2-WLTP", "CO2_WLTP", "CO2"]:
                if col in df.columns:
                    co2 = co2.fillna(pd.to_numeric(df[col], errors="coerce"))

            hc = df["Hybridcode"].fillna("").astype(str).str.strip() if "Hybridcode" in df.columns else pd.Series("", index=df.index)

            is_ovc = is_hyb & (hc == "OVC-HEV")
            is_novc = is_hyb & hc.isin(["NOVC-HEV", "NOVC-FCHV"])
            has_hc = is_ovc | is_novc
            is_co2_low = is_hyb & ~has_hc & co2.notna() & (co2 > 0) & (co2 <= CO2_PHEV_THRESHOLD)
            is_co2_high = is_hyb & ~has_hc & co2.notna() & (co2 > CO2_PHEV_THRESHOLD)
            no_data = is_hyb & ~has_hc & ~is_co2_low & ~is_co2_high

            is_pet = df["_fuel"] == "_petrol_hybrid"

            # Assign PHEV
            df.loc[is_ovc & is_pet, "_fuel"] = "PHEV"
            df.loc[is_ovc & ~is_pet, "_fuel"] = "Diesel PHEV"
            df.loc[is_co2_low & is_pet, "_fuel"] = "PHEV"
            df.loc[is_co2_low & ~is_pet, "_fuel"] = "Diesel PHEV"

            # Assign HEV (non-plug-in)
            df.loc[is_novc & is_pet, "_fuel"] = "Hybrid (Petrol)"
            df.loc[is_novc & ~is_pet, "_fuel"] = "Hybrid (Diesel)"
            df.loc[is_co2_high & is_pet, "_fuel"] = "Hybrid (Petrol)"
            df.loc[is_co2_high & ~is_pet, "_fuel"] = "Hybrid (Diesel)"
            df.loc[no_data & is_pet, "_fuel"] = "Hybrid (Petrol)"
            df.loc[no_data & ~is_pet, "_fuel"] = "Hybrid (Diesel)"

            n_phev = (is_ovc | is_co2_low).sum()
            n_hev = (is_novc | is_co2_high | no_data).sum()
            print(f"    Hybrid split: {n_phev:,} PHEV + {n_hev:,} HEV (of {is_hyb.sum():,} hybrids)")

    # Brand
    if "Marke" in df.columns:
        df["_brand"] = df["Marke"].astype(str).str.strip()
        df["_origin"] = df["Marke"].apply(lambda x: safe_map(x, m.get("brand_origin", {})))
        df["_group"] = df["Marke"].apply(lambda x: safe_map(x, m.get("brand_group", {})))
        df["_continent"] = df["_origin"].apply(lambda x: safe_map(x, m.get("country_continent", {})))
        for v in df["Marke"].dropna().unique():
            if safe_map(v, m.get("brand_origin", {})) == "Other" and str(v).strip():
                warnings.add(f"brand:{v}")

    # Model (brand + normalized Typ1) — feeds chart_model_race
    if "Marke" in df.columns and "Typ1" in df.columns:
        overrides = m.get("model_overrides", {}) or {}
        overrides_sorted = sorted(
            ((k.upper(), v) for k, v in overrides.items()),
            key=lambda kv: len(kv[0]),
            reverse=True,
        )
        df["_model"] = [
            normalize_model(b, t, overrides_sorted)
            for b, t in zip(df["Marke"].astype(str), df["Typ1"].astype(str))
        ]
        # Collapse spelling/concatenation variants that the auto-rule splits
        # ("SKODA OCTAVIAC" -> Skoda Octavia). model_overrides can't reach
        # these because it matches the raw "Marke Typ1" by space-prefix, and
        # ASTRA concatenates the suffix onto the model token with no space.
        merges = {k.upper(): v for k, v in (m.get("model_merges", {}) or {}).items()}
        if merges:
            df["_model"] = df["_model"].map(lambda k: merges.get(str(k).upper(), k))

        # Segment (market class) — keyed on the canonical model, case-insensitive
        # so it composes with both UPPER auto-keys and proper-case merge labels.
        # Lets a future chart compare segments across makers (BMW 3 Series vs
        # Mercedes C-Class vs Audi A4 = "Compact Executive"). Unmapped → "Other".
        segments = {k.upper(): v for k, v in (m.get("model_segments", {}) or {}).items()}
        df["_segment"] = df["_model"].map(
            lambda k: segments.get(str(k).upper(), "Other") if k else "Other"
        )
        # Brand for model/segment purposes only: ASTRA records some AMG cars
        # under the Marke "MERCEDES-AMG", which would split Mercedes-Benz across
        # two "makers" in a segment-by-maker comparison. normalize_model already
        # folds the AMG *model* into Mercedes-Benz; fold the brand to match.
        # (Scoped to _model_brand — the global _brand / brand charts are
        # unchanged. chart_model_race groups by model and ignores brand.)
        df["_model_brand"] = df["_brand"].replace({"MERCEDES-AMG": "MERCEDES-BENZ"})
        brand_overrides = {
            k.upper(): v for k, v in (m.get("model_brand_overrides", {}) or {}).items()
        }
        if brand_overrides:
            df["_model_brand"] = [
                brand_overrides.get(str(model).upper(), brand)
                for model, brand in zip(df["_model"], df["_model_brand"])
            ]

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

        # Canton BEV by month (for ev_wave — BEV only, not PHEV, for accuracy)
        if "_canton" in valid.columns and "_is_bev" in valid.columns:
            canton_grp = valid.groupby(["_canton", "_year", "_month"])
            canton_total = canton_grp.size().reset_index(name="total_count")
            canton_bev = canton_grp["_is_bev"].sum().reset_index(name="ev_count")
            agg["canton_ev_by_month"] = canton_total.merge(
                canton_bev, on=["_canton", "_year", "_month"]
            )

        # Model by month (for chart_model_race), annotated with segment
        if "_model" in valid.columns:
            model_valid = valid[valid["_model"] != ""]
            if not model_valid.empty:
                brand_col = "_model_brand" if "_model_brand" in model_valid.columns else "_brand"
                group_cols = ["_year", "_month", "_model", brand_col]
                if "_segment" in model_valid.columns:
                    group_cols.append("_segment")
                agg["model_by_month"] = (
                    model_valid.groupby(group_cols).size().reset_index(name="count")
                    .rename(columns={brand_col: "_brand"})
                )

        # Brand powertrain by month (brand x month x fuel category) — generic
        # aggregate powering chart_china_powertrain_mix and any future
        # per-brand powertrain view. Not China-filtered on purpose.
        if "_brand" in valid.columns and "_fuel" in valid.columns:
            agg["brand_powertrain_by_month"] = (
                valid.groupby(["_year", "_month", "_brand", "_fuel"])
                .size().reset_index(name="count")
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
    if "model_by_month" in agg:
        has_seg = "_segment" in agg["model_by_month"].columns
        keys = ["_year", "_month", "_model", "_brand"] + (["_segment"] if has_seg else [])
        df = agg["model_by_month"].groupby(keys)["count"].sum().reset_index()
        df.columns = ["year", "month", "model", "brand"] + (["segment"] if has_seg else []) + ["count"]
        df = df.sort_values(["year", "month", "model"])
        df.to_csv(OUT_DIR / "model_by_month.csv", index=False)

    if "brand_bev_by_month" in agg:
        df = agg["brand_bev_by_month"].groupby(["_year", "_month", "_brand"])["bev_count"].sum().reset_index()
        df.columns = ["year", "month", "brand", "bev_count"]
        df = df.sort_values(["year", "month", "brand"])
        df.to_csv(OUT_DIR / "brand_bev_by_month.csv", index=False)

    # Brand powertrain by month (brand x month x fuel category)
    if "brand_powertrain_by_month" in agg:
        df = agg["brand_powertrain_by_month"].groupby(
            ["_year", "_month", "_brand", "_fuel"])["count"].sum().reset_index()
        df.columns = ["year", "month", "brand", "powertrain", "count"]
        df = df.sort_values(["year", "month", "brand", "powertrain"])
        df.to_csv(OUT_DIR / "brand_powertrain_by_month.csv", index=False)

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


def add_model_mapping_warnings(agg: dict, mappings: dict, warnings: set):
    """Warn when watched brands have high-volume model keys still in Other."""
    cfg = mappings.get("model_mapping_warnings", {}) or {}
    watched_brands = {str(b).upper() for b in cfg.get("brands", [])}
    if not watched_brands or "model_by_month" not in agg:
        return

    min_count = int(cfg.get("min_count", 500))
    ignored_models = {str(m).upper() for m in cfg.get("ignore_models", [])}
    df = agg["model_by_month"]
    required = {"_model", "_brand", "_segment", "count"}
    if not required.issubset(df.columns):
        return

    totals = (
        df[df["_segment"] == "Other"]
        .groupby(["_brand", "_model"])["count"]
        .sum()
        .reset_index()
    )
    for brand, model, count in totals[["_brand", "_model", "count"]].itertuples(index=False, name=None):
        brand = str(brand)
        model = str(model)
        count = int(count)
        if brand.upper() not in watched_brands:
            continue
        if model.upper() in ignored_models or count < min_count:
            continue
        warnings.add(f"model_segment:{brand}:{model}:{count}")


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

    add_model_mapping_warnings(total_agg, mappings, warnings)
    consolidate_and_save(total_agg)
    save_warnings(warnings)
    print("\nDone.")


if __name__ == "__main__":
    main()

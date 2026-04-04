# Methodology

This document is the single source of truth for all definitions, classifications, and assumptions used in this project. All charts, reports, and code must conform to these rules.

For chart styling, see the **Chart Styleguide** section in [AGENTS.md](AGENTS.md). For pipeline details, see [README.md](README.md).

---

## Data Source

| Property | Value |
|----------|-------|
| **Provider** | Swiss Federal Roads Office (ASTRA) |
| **Dataset** | IVZ Open Data — Neuzulassungen (new registrations) |
| **URL** | https://opendata.astra.admin.ch/ivzod/1000-Fahrzeuge_IVZ/1200-Neuzulassungen/ |
| **Format** | Tab-separated values (TSV), one file per month/year |
| **Coverage** | 2016–present, ~250K–320K passenger cars per year |
| **Update frequency** | Monthly (typically available by 5th of following month) |
| **License** | Swiss Open Government Data (OGD) — free with attribution |
| **Attribution** | "Datenquelle: Bundesamt für Strassen ASTRA" |

### ASTRA vs auto.swiss

Both ASTRA and auto.swiss use the same underlying MOFIS database but differ in snapshot timing:

- **auto.swiss** publishes on the 1st–3rd business day of the following month
- **ASTRA** cumulates with retroactive corrections over time
- Monthly differences can reach ~5.5% for recent months; yearly differences are ≤0.07%
- Differences typically cancel out over a full year (+0.027% across 2.67M registrations)
- We use **ASTRA** as primary source and **auto.swiss** as validation reference

---

## Statistical Population

**Scope: Personenwagen (passenger cars) only.**

This corresponds to UNECE Category M1 — vehicles designed for passenger carriage with no more than 8 seats plus the driver.

**Excluded:**
- Lieferwagen (light commercial vehicles / vans)
- Lastwagen (trucks / heavy goods vehicles)
- Motorräder (motorcycles)
- Busse (buses)
- Landwirtschaftliche Fahrzeuge (agricultural vehicles)
- All other Fahrzeugarten

**Filter:** `Fahrzeugart = Personenwagen` applied at data ingestion.

---

## Powertrain Classification

### Decision Table

This table defines the complete classification logic. All code must implement these rules exactly.

| ASTRA Treibstoff | Hybridcode (2022+) | CO2 (g/km) | Classification |
|---|---|---|---|
| Elektrisch | — | — | **BEV** |
| Elektrisch mit RE | — | — | **PHEV** |
| Benzin / Elektrisch | OVC-HEV | — | **PHEV** |
| Benzin / Elektrisch | NOVC-HEV | — | **HEV (Petrol)** |
| Benzin / Elektrisch | *missing* | ≤ 50 | **PHEV** |
| Benzin / Elektrisch | *missing* | > 50 | **HEV (Petrol)** |
| Diesel / Elektrisch | OVC-HEV | — | **PHEV (Diesel)** |
| Diesel / Elektrisch | NOVC-HEV | — | **HEV (Diesel)** |
| Diesel / Elektrisch | *missing* | ≤ 50 | **PHEV (Diesel)** |
| Diesel / Elektrisch | *missing* | > 50 | **HEV (Diesel)** |
| Wasserstoff / Elektrisch | — | — | **FCEV** |
| Benzin | — | — | **Petrol** |
| Diesel | — | — | **Diesel** |
| Erdgas (CNG) / Benzin | — | — | **CNG** |
| Flüssiggas (LPG) / Benzin | — | — | **LPG** |

### Resolution Priority

1. **Hybridcode** (available 2022+): `OVC-HEV` = PHEV, `NOVC-HEV` = HEV. Most reliable method.
2. **CO2 ≤ 50 g/km fallback** (pre-2022 data): Matches EU regulation defining PHEV eligibility.
3. **Range Extenders** ("Elektrisch mit RE"): Always classified as PHEV per auto.swiss, Swiss eMobility, and ACEA standards.

### Aggregate Categories

| Category | Includes | Use case |
|----------|----------|----------|
| **EV** | BEV + PHEV + FCEV | Broad electrification metric (plug-in vehicles) |
| **BEV** | BEV only | Pure electric analysis |
| **Plug-in** | BEV + PHEV + FCEV | Same as EV (synonym) |

**HEV and MHEV are explicitly excluded from "EV" counts.** They cannot charge from the grid.

### Edge Cases

- **Mild hybrids (48V MHEV):** May appear as "Benzin / Elektrisch" in older ASTRA data. Correctly classified as HEV by our logic since they cannot achieve CO2 ≤ 50 g/km and don't carry OVC-HEV Hybridcode.
- **Chinese Extended-Range EVs** (Li Auto, AITO): Classified as "Elektrisch mit RE" or OVC-HEV in ASTRA data → correctly mapped to PHEV.
- **Hydrogen FCEVs:** Rare (<100/year). Grouped with EV in aggregate but shown separately in powertrain breakdowns.

---

## Brand and Corporate Classification

All brand classifications are driven by `mappings.yaml`. No classification logic lives in code.

### Brand Origin

Assigned by **brand heritage**, not corporate registration country:
- Fiat → Italy (even though Stellantis is Dutch-registered)
- MINI → Germany (even though it's BMW Group / historically British)
- Volvo → Sweden (even though Geely is Chinese-owned)

### Corporate Group

Maps brands to parent companies for group-level analysis:
- Volkswagen Group: VW, Audi, Porsche, Škoda, SEAT, Cupra, Bentley, Lamborghini
- Stellantis: Peugeot, Citroën, Fiat, Alfa Romeo, Jeep, Opel, DS
- etc.

### Display Names

Brand names use Title Case, never ALL CAPS. Exceptions maintained by `display_brand()`:
BMW, BYD, VW, MG, NIO (stay uppercase by convention).

### Unknown Brands

Any brand not in `mappings.yaml` → "Other" bucket + logged to `warnings.log`.

---

## Geographic Scope

- **Canton codes** in ASTRA data include non-Swiss entries (A, BA, FL, M, P)
- These are **filtered out** for map/geographic charts
- They remain in aggregate national totals (a vehicle registered in Switzerland with a foreign canton code is still a Swiss registration)

---

## Temporal Rules

### Partial Years

- Annual charts **exclude** incomplete years (< 12 months of data) to avoid misleading comparisons
- The current partial year is handled by the projection system (see below)

### Year-End Projection

`project.py` pro-rates YTD registrations using seasonal scaling factors:

1. Reference years: 2016–present, **excluding COVID-affected 2020–2021**
2. A capture ratio corrects for ASTRA reporting lag in the partial month
3. If capture ratio falls outside 0.4–1.3, the partial month is excluded
4. Output: `projection.json` consumed by chart.py and report.py

### Trailing Windows

Animated charts use **12-month trailing** sums/averages for trend stability. This smooths seasonal variation (December fleet dumps, summer lulls).

---

## Validation

### Reference Data

`reference.yaml` contains annual totals from auto.swiss for cross-validation.

### Tolerance Thresholds

| Comparison | Tolerance | Rationale |
|------------|-----------|-----------|
| Yearly totals | ±2% | Max observed: 0.07%. 2% provides generous margin |
| Monthly totals | ±2% | Max observed ~5.5% for recent months due to snapshot timing, but 2% catches most anomalies |
| BEV monthly | ±5% | Smaller absolute numbers make BEV counts more volatile across sources |

### Warning System

`validate.py` generates `warnings.log` containing:
- Plausibility check failures (totals outside tolerance)
- Unmapped values from `mappings.yaml` (new brands, fuel types)

---

## Known Limitations

1. **Registration ≠ purchase date.** ASTRA data records when a vehicle enters the road network, not when ordered or manufactured. Delivery lags of 3–12 months are common.
2. **Fleet vs private not disaggregated.** ASTRA data does not distinguish fleet registrations from private purchases. Bulk fleet renewals can create misleading monthly spikes.
3. **Direct imports.** Parallel/grey market imports undergo separate customs and conformity processes, introducing temporal gaps between border crossing and registration.
4. **Pre-2022 hybrid ambiguity.** Without Hybridcode, the CO2 ≤ 50 g/km threshold is a proxy. Some borderline vehicles may be misclassified.
5. **ASTRA column name typo.** 2016–2018 files use "Erstinvekehrsetzung_Kanton" (missing 'r'). Corrected automatically in `process.py`.

---

## Methodology Changelog

| Date | Version | Change |
|------|---------|--------|
| 2025-03 | 1.0 | Initial classification: all "Benzin/Elektrisch" mapped to PHEV |
| 2025-03 | 2.0 | **PR #4:** Split "Benzin/Elektrisch" into PHEV vs HEV using Hybridcode + CO2 fallback. Added HEV(Petrol) and HEV(Diesel) categories. REX reclassified from BEV to PHEV. |

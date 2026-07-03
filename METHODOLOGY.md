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
- Monthly differences can reach ~5.5% for very recent months; yearly differences are ≤0.07%
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

| ASTRA Treibstoff | Hybridcode | CO2 (g/km) | Classification |
|---|---|---|---|
| Elektrisch | — | — | **BEV** |
| Elektrisch mit RE | — | — | **PHEV** |
| Benzin / Elektrisch | OVC-HEV | — | **PHEV** |
| Benzin / Elektrisch | NOVC-HEV or NOVC-FCHV | — | **HEV (Petrol)** |
| Benzin / Elektrisch | *missing* | ≤ 50 | **PHEV** |
| Benzin / Elektrisch | *missing* | > 50 | **HEV (Petrol)** |
| Benzin / Elektrisch | *missing* | *missing* | **HEV (Petrol)** |
| Diesel / Elektrisch | OVC-HEV | — | **PHEV (Diesel)** |
| Diesel / Elektrisch | NOVC-HEV or NOVC-FCHV | — | **HEV (Diesel)** |
| Diesel / Elektrisch | *missing* | ≤ 50 | **PHEV (Diesel)** |
| Diesel / Elektrisch | *missing* | > 50 | **HEV (Diesel)** |
| Diesel / Elektrisch | *missing* | *missing* | **HEV (Diesel)** |
| Wasserstoff / Elektrisch | — | — | **FCEV** |
| Benzin | — | — | **Petrol** |
| Diesel | — | — | **Diesel** |
| Erdgas (CNG) / Benzin | — | — | **CNG** |
| Flüssiggas (LPG) / Benzin | — | — | **LPG** |

### Resolution Priority

1. **Hybridcode** (available from 2022, not always populated): `OVC-HEV` = PHEV, `NOVC-HEV` / `NOVC-FCHV` = HEV. Most reliable method.
2. **CO2 ≤ 50 g/km fallback** (when Hybridcode missing): Matches EU regulation defining PHEV eligibility. CO2 column priority: `CO2-WLTP` (2023+) → `CO2_WLTP` (2022) → `CO2` (NEFZ, pre-2022). The 50 g/km threshold applies to whichever value is available.
3. **No data fallback** (both Hybridcode and CO2 missing): Defaults to HEV, the conservative classification.
4. **Range Extenders** ("Elektrisch mit RE"): Always classified as PHEV per auto.swiss, Swiss eMobility, and ACEA standards.

### Aggregate Categories

| Category | Includes | Use case |
|----------|----------|----------|
| **EV** | BEV + PHEV + FCEV | Broad electrification metric (plug-in vehicles) |
| **BEV** | BEV only | Pure electric analysis |
| **Plug-in** | BEV + PHEV + FCEV | Same as EV (synonym) |

**HEV and MHEV are explicitly excluded from "EV" counts.** They cannot charge from the grid.

### Complete Output Categories

The pipeline produces these fuel/powertrain categories (as seen in `fuel_by_month.csv` and charts):

Petrol, Diesel, BEV, PHEV, Diesel PHEV, Hybrid (Petrol), Hybrid (Diesel), Hydrogen, CNG, LPG, Other

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
- MG → United Kingdom, Maxus → United Kingdom (British marques — Morris Garages, ex-LDV — now SAIC-owned; heritage governs origin, ownership is captured separately below)

### Ownership Classification (`owner_country`)

**Brand Origin is by heritage and is not repurposed.** The Chinese-ownership story needs a *second, orthogonal* dimension: **`owner_country`** — the country of the ultimate controlling shareholder of the brand's parent group. It lives in `mappings.yaml > brand_owner_country` (a flat per-brand map, mirroring `brand_origin`/`brand_group`); no classification logic lives in code.

Two blocs are derived from these two dimensions:

| Bloc | Rule | Examples |
|------|------|----------|
| **China-branded** | `brand_origin == China` | BYD, XPeng, Zeekr, NIO, Leapmotor, GWM/Ora, Aiways, JAC, GAC, Maxus (heritage note below), DFSK/Seres, Omoda & Jaecoo (Chery), Voyah, Hongqi, Lynk & Co |
| **China-owned** | `owner_country == China` **or** `brand_origin == China` | All China-branded brands **plus** MG (SAIC), Volvo, Polestar & Lotus (Geely), Smart (Geely–Mercedes JV) |

The union rule (`owner_country == China OR origin == China`) makes **China-branded ⊆ China-owned** by construction, so on `china_bev_share.png` the China-branded line is never above the China-owned line, and a new Chinese-heritage brand is counted as China-owned even before an explicit `owner_country` entry is added.

Decisions encoded (the recurring objections, pre-empted):

- **Lynk & Co** — Geely-created brand (2016), engineering in Gothenburg. Heritage is genuinely ambiguous → `origin: China`, `owner_country: China`. (The Gothenburg design heritage is why it is borderline.)
- **Smart** — 50/50 Geely–Mercedes JV, production in Xi'an → `origin: Germany` (unchanged), `owner_country: China`. This is the "but Smart is German" objection; the on-chart definition line names the JV. For the Geely callout on `china_bev_share.png`, Smart is counted **fully under Geely** (via `CHINA_GROUP_OVERRIDES` in `scripts/chart.py`) — no fractional JV attribution.
- **Volvo / Polestar / Lotus** — `origin` stays Sweden / Sweden / United Kingdom; `owner_country: China` (Geely Holding). Polestar's Nasdaq listing does not change controlling ownership.
- **MG / Maxus** — `origin` corrected to United Kingdom (British heritage; both were previously mapped `origin: China`), `owner_country: China` (SAIC).
- **Classification is by brand ownership, never by production location.** Dacia Spring, Tesla Model 3 (Shanghai) and the pre-2025 BMW iX3 are China-*made* but not China-*owned*, and stay with their owner's country.
- **Unknown brands** — the existing "Other" mechanism applies. `validate.py` additionally emits `owner_country:<brand>:<count>` to `warnings.log` for any brand contributing ≥ 20 BEV registrations in the trailing 12 months without an `owner_country` mapping, so new entrants (expected: several Chinese brands per year) surface for classification.

### Market Entry (entry markers)

The **market-entry markers** on `china_powertrain_mix.png` place one ▲ tick per China-branded brand at its **market entry** — the first month with **≥ `ENTRY_MIN_MONTHLY` (= 5)** BEV registrations whose following three months are not all zero. The threshold and follow-through guard filter single grey/direct imports (Known Limitation #3) that would otherwise date a brand's "entry" to a one-off registration years before commercial launch. Only brands with **≥ 300 cumulative** BEV registrations are marked (`ENTRY_MARKER_MIN_CUMULATIVE` in `scripts/chart.py`), and entries within four months of each other are staggered onto two levels. `ENTRY_MIN_MONTHLY` is a module constant in `scripts/process.py`.

### Manufacturer Blocs (displacement strip)

The manufacturer-bloc strip inside `china_bev_share.png` partitions **every** BEV brand into exactly one of seven mutually-exclusive, collectively-exhaustive **manufacturer blocs** via `process.bloc()`. Resolution is first-match-wins so the partition is deterministic:

1. **China-owned** — `owner_country == China OR origin == China` (absorbs Volvo, Polestar, Smart, MG before the European fallthrough can claim them, consistent with the `owner_country` dimension)
2. **Tesla** — its own bloc (the single largest non-group marque; kept separate so the legacy-vs-China story isn't muddied by Tesla's swings)
3. **Volkswagen Group** — `brand_group == "Volkswagen Group"`
4. **Korean** / **Japanese** — `brand_origin`
5. **European legacy** — any remaining Europe-origin brand (`country_continent[origin] == Europe`)
6. **Other** — everything else (incl. unmapped origins, already surfaced by the brand-origin unmapped warning)

`BLOC_ORDER` places China-owned **last** so it stacks on top of the compressed 100%-area strip. Only the China-owned wedge is drawn at full saturation; every legacy bloc is muted so the growing red wedge is the one thing the eye tracks (`BEV_BLOC_FILL_COLORS` in `scripts/chart.py`). The partition contract (every brand → exactly one bloc ∈ `BLOC_ORDER`) is asserted in `tests/test_process.py::TestBloc`. The report's `china_bloc_rank_line` mirrors this strip as a Headlines bullet.

### China-branded Powertrain Mix

`china_powertrain_mix.png` uses the **China-branded** set (tighter than China-owned — excludes Volvo, MG, Smart, Polestar) from the `brand_powertrain_by_month.csv` output. The full decision-table fuels collapse to **BEV / PHEV / HEV / ICE / Other** (`POWERTRAIN_COLLAPSE`): EREV/range-extender is already PHEV upstream, Diesel-PHEV folds into PHEV, both hybrid flavours into HEV, Petrol + Diesel into ICE. The lower panel tracks **PHEV share of China-branded registrations** — the "second wave" indicator as BYD/others push plug-in hybrids after their BEV entry. Per-brand market-entry ▲ ticks sit on the top panel's x-axis (see Market Entry above).

### Corporate Group

Maps brands to parent companies for group-level analysis:
- Volkswagen Group: VW, Audi, Porsche, Škoda, SEAT, Cupra, Bentley, Lamborghini
- Stellantis: Peugeot, Citroën, Fiat, Alfa Romeo, Jeep, Opel, DS
- etc.

### Display Names

Brand names use Title Case, never ALL CAPS. Exceptions maintained by `display_brand()`:
BMW, BYD, MG, DS, KGM, NIO, GWM, JAC, GAC, VW (stay uppercase by convention). VOLKSWAGEN is also renamed to VW.

### Unknown Brands

Any brand not in `mappings.yaml` → "Other" bucket + logged to `warnings.log`.

### Model Normalization

For model-level analytics (`chart_model_race`), ASTRA's `Marke` + `Typ1` columns are normalized into a stable model key by `process.normalize_model()`:

1. **Override match (longest-prefix wins)** — `mappings.yaml > model_overrides` provides operator-curated splits (e.g. `Toyota Yaris Cross` is distinct from `Toyota Yaris`) and merges (e.g. `MITSUBISHI SPACE STAR` reunites a two-token nameplate that the auto-rule would split as `MITSUBISHI SPACE`). Override values are the proper-cased display label.
2. **Duplicate brand cleanup** — some ASTRA `Typ1` values repeat the brand before the model (`AUDI RS6`, `POLESTAR 2`, `HONDA E`). The repeated brand prefix is stripped before parsing, except for DS where `DS` is part of the model name (`DS 7`, `DS7CRB`).
3. **Auto-rule (default)** — brand + first `Typ1` token, with regex specials for marques whose `Typ1` ASTRA writes inconsistently or at the wrong grain:
   - **Tesla:** `MODEL Y` / `MODELY` / `MODEL3` all collapse to `TESLA MODEL <X>`
   - **VW ID family:** `ID.3 PRO 150 KW` / `ID.3PROS150KW` / `ID4` all collapse to `VW ID.<N>`; `ID. BUZZ GTX` → `VW ID.BUZZ`
   - **BMW series:** the trim digits are engine codes, so `320D` / `M340I` / `118I` collapse to `BMW 3/3/1 Series`; full M cars fold too (`M3` / `M3COMPETITION` → 3 Series). X SUVs, `i`/`iX` electrics and `Z4` stay distinct (`X1XDRIVE20D` → X1, `i3s` → `i3`).
   - **Mercedes-Benz:** model names are class letters or letter groups and the digits are always engine displacement, so the model is the leading alphabetic run (`GLA200` → GLA, `C 220 d` → C, `V250d` → V). **AMG folds into the base** — the class after `AMG` decides it (`AMG C 63` → C-Class, `AMG GLC 43` → GLC); the standalone `AMG GT` keeps its own line. `MERCEDES-AMG` (a separate `Marke`) is treated as Mercedes-Benz.
   - **Audi RS/S:** performance trims fold into the base A/Q line (`RS 3`/`S3` → A3, `RS Q8`/`SQ7` → Q8/Q7, `TTS` → TT). `R8` and the `e-tron GT` sport sedan stay standalone (the GT is kept distinct from the `e-tron` SUV).
   - **Mini:** body-style/trim-first names collapse to nameplates (`3DOOR COOPER S`, `5DOOR ONE`, `COOPER S` → Mini Cooper; `COOPER S CLUBMAN` → Clubman; `COUNTRYMAN SE` → Countryman).
   - **Porsche:** concatenated trim/body suffixes fold back to the base nameplate (`911CARRERA` → 911, `TAYCAN4S` → Taycan, `CAYENNET` → Cayenne).
   - **Cupra:** concatenated trims/batteries fold to the nameplate (`FORMENTORE-HYBRID` → Cupra Formentor, `BORN170KW...` → Cupra Born, `LEONSP` → Cupra Leon). The proper-case label matches the SEAT-era `model_overrides`, so Marke=CUPRA and historical Marke=SEAT rows land on one key.
   - **Lexus:** 2–3 letter names, digits are hybrid trim — leading letters win (`RX450H` → RX, `NX450H+` → NX).
   - **Land Rover:** the `RR ...` / `RANGE ROVER ...` family spans three segments, so it's split by sub-model — Evoque (Compact SUV), Velar (Mid), Range Rover Sport + full-size Range Rover (Large). Discovery / Defender are separate keys.
4. **Empty / NaN guard** — rows with missing brand or `Typ1` (including pandas-stringified `"nan"`) drop out of the model aggregation entirely.
5. **Key merge (`mappings.yaml > model_merges`)** — a final pass collapses normalized keys that are the same nameplate split by ASTRA spelling (`SKODA OCTAVIAC` → Skoda Octavia, `VW PASSATV` → VW Passat, `HYUNDAI SANTA`/`SANTAFE` → Hyundai Santa Fe) and applies proper-case display labels (`MERCEDES-BENZ C` → Mercedes-Benz C-Class). This layer exists because `model_overrides` matches the raw `Marke Typ1` by space-prefix and can't reach the body/trim suffix ASTRA concatenates onto the model token with no space. Only merge genuinely identical nameplates — not distinct models (Fiat 500 vs 500X) or engine/trim variants.

Refresh `model_overrides` and `model_merges` every couple of months as new spellings appear.

### Market Segment

Each canonical model is tagged with a market segment (`mappings.yaml > model_segments`, emitted as the `segment` column of `model_by_month.csv`) for cross-maker comparison — BMW 3 Series, Mercedes C-Class and Audi A4 all map to **Compact Executive**. The map is scoped to premium/luxury makers; everything else is `Other`. The brand-fold rules above are the prerequisite: AMG/RS/M variants inherit their **base model's segment** (M3 sits with the 3 Series), and because they're folded into the base, the segment volume is complete — no separate "performance" bucket that would be lopsided across makers (ASTRA records AMG/RS differently than BMW M).

For a segment-by-maker comparison, the `brand` column on `model_by_month.csv` rolls `MERCEDES-AMG` (a separate ASTRA `Marke`) into Mercedes-Benz so a maker isn't split in two. This rollup is scoped to the model data — the global `_brand` and the brand-level charts are unchanged, and `chart_model_race` groups by model and ignores brand.

Historical `SEAT CUPRA ...` rows are normalized to Cupra model labels and rebranded to `CUPRA` in `model_by_month.csv`; this model-level rebrand is also scoped away from global ASTRA brand totals.

`mappings.yaml > model_mapping_warnings` defines a watched-brand list and count threshold. During processing, any watched brand with a high-volume model key still assigned to segment `Other` is written to `warnings.log` as `model_segment:<brand>:<model>:<count>`. This is the maintenance signal for new models, changed ASTRA spelling, or a normalization artifact that should be added to `model_overrides`, `model_merges`, or `model_segments`.

**Known aggregation artifacts:** a bare fallback `TOYOTA GR` key is filtered from `chart_model_race` via `MODEL_ARTIFACTS` in `scripts/chart.py`; current GR Yaris / GR86 / GR Corolla rows are split by `model_overrides`. A small tail of unparseable `Typ1` junk falls to segment `Other` and is excluded from segment charts.

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

1. Reference years: 2016–present, **excluding 2020–2021** (COVID-19 lockdowns and supply chain disruptions make these years unrepresentative of normal seasonal patterns)
2. A capture ratio corrects for ASTRA reporting lag in the partial month
3. If capture ratio falls outside 0.4–1.3, the partial month is excluded
4. Output: `projection.json` consumed by chart.py and report.py

### Trailing Windows

Animated charts use **12-month trailing** sums/averages for trend stability. This smooths seasonal variation (December fleet dumps, summer lulls).

---

## Validation

### Reference Data

`reference.yaml` contains annual totals from auto.swiss for cross-validation.

### Checks

`validate.py` runs 7 checks (warnings only, never blocks the pipeline):

| Check | What it does | Threshold |
|-------|-------------|-----------|
| `check_yearly_totals` | Compare yearly totals vs auto.swiss reference | ±2% (max observed: 0.07%) |
| `check_monthly_totals` | Compare monthly totals vs auto.swiss reference | ±2% (recent months can differ ~5.5% due to snapshot timing) |
| `check_bev_totals` | Compare BEV monthly totals vs auto.swiss reference | ±5% (smaller absolute numbers = more volatile) |
| `check_monthly_range` | Flag months outside expected registration range | 5,000–45,000 |
| `check_complete_years` | Detect missing months in completed years | All 12 months expected (current year excluded) |
| `check_yoy_spikes` | Flag suspicious month-over-month changes (skips 2020–2021) | >50% change |
| `check_fuel_consistency` | Verify fuel-type totals sum to monthly totals | Exact match |

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
| 2026-07 | 3.0 | **Chinese BEV brands:** Added the `owner_country` ownership dimension (China-owned vs China-branded blocs) and two dashboard charts. `china_bev_share` overlays the T12M China-owned / China-branded / Tesla share lines (with a Geely-concentration callout and a China-owned-passes-Tesla marker) on a compressed 100%-stacked manufacturer-bloc strip (`process.bloc()`, 7-bloc partition). `china_powertrain_mix` shows the China-branded powertrain collapse + PHEV-share "second wave" panel with per-brand market-entry ▲ markers (`ENTRY_MIN_MONTHLY = 5`, brands ≥300 cumulative; new `brand_powertrain_by_month.csv` output). MG and Maxus `origin` corrected China → United Kingdom (heritage); Chinese ownership carried by `owner_country`. Report gains China-owned-share, bloc-rank, and China-branded-PHEV-share Headlines. |

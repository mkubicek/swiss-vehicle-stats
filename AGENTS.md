# AGENTS.md — Swiss Vehicle Stats

## Project Overview

Automated analytics dashboard for Swiss new vehicle registrations from ASTRA/IVZ Open Data.

Pipeline: `download.py` → `process.py` → `validate.py` → `chart.py` → `report.py`

## Data Scope

- **Fahrzeugart = Personenwagen** (passenger cars only, ASTRA terminology)
- **EV:** BEV + PHEV + FCEV
- **BEV:** Fully electric only
- Source files: ~100MB TSV each, 2016–present

## Chart Styleguide

All charts must follow these rules for visual consistency.

### Colors

Fixed brand color palette — bright, high-contrast for dark backgrounds:

```
Tesla:       #f72585
BMW:         #4cc9f0
VW:          #4ade80
Mercedes:    #a78bfa
Audi:        #fb923c
Volvo:       #22d3ee
Hyundai:     #f87171
Kia:         #34d399
Porsche:     #e879f9
Polestar:    #fbbf24
Renault:     #facc15
Skoda:       #4ade80
BYD:         #ff6b6b
MG:          #fcd34d
Cupra:       #2dd4bf
Dacia:       #60a5fa
Peugeot:     #818cf8
Citroen:     #fb7185
Fiat:        #f9a8d4
Opel:        #fde047
Ford:        #7dd3fc
Toyota:      #f87171
Mini:        #86efac
Smart:       #fdba74
NIO:         #67e8f9
Nissan:      #fca5a5
```

### Typography

- **Font:** Use a single consistent font family across all charts (default: system sans-serif via matplotlib)
- **Brand names:** Title case, never ALL CAPS (Tesla, not TESLA; Mitsubishi, not MITSUBISHI). Use `display_brand()` which handles overrides (BMW, BYD, VW, MG, NIO stay uppercase).
- **ASTRA terms:** When using German terminology (e.g. "Personenwagen"), always annotate with English equivalent or note "(ASTRA terminology)" on first use

### Layout

- **Dark theme:** Background `#0d1117`, text white/light gray
- **Attribution line:** Always bottom-right, single line combining repo link + data source + dates:
  `{repo_url} | Data: ASTRA/IVZ Open Data (as of {data_date}) | Generated {today}`
  Data date comes from `data/processed/metadata.json` (extracted from ASTRA `Datenstand` column). Repo URL is detected at runtime from `GITHUB_REPOSITORY` env var or `git remote`.
  Use fontsize 8 for static charts, 11 for GIF frames (rendered at lower DPI).
- **Attribution positioning:** Use `ax.transAxes` coordinates (not `fig.text`) for charts saved with `bbox_inches="tight"` — figure coordinates create gaps on tall/non-standard aspect ratio charts.
- **Definition line:** Below chart title, gray text (`#94a3b8`), explains scope and methodology
- **No overlapping elements:** Ensure axes, labels, colorbars, and sparklines have clear spacing
- **Legends:** Place outside chart area (`bbox_to_anchor`) to avoid overlapping data. Use inline labels (annotations at end of lines) instead of legends when possible.
- **Year axes:** Always show every year — set explicit `xticks` to prevent matplotlib from skipping years
- **Partial years:** Exclude incomplete years (< 12 months) from annual charts to avoid misleading bars/points
- **Bar charts:** Fix axis width so bars don't jump when label lengths change — use a fixed xlim and consistent y-axis label width
- **Chart filenames:** Use descriptive names without number prefixes (e.g. `brand_rankings.png`, not `03_brand_rankings.png`)

### Animated Charts (GIFs)

- **Date label:** Consistent position, monospace font, not affected by content changes
- **Frame rate:** 300ms per frame, last frame 3000ms pause
- **Trailing windows:** Use 12-month trailing for trend stability (reduces seasonality)
- **Fixed axes:** Lock x/y limits across all frames so the chart doesn't jump

### Static Charts

- **Grid layouts:** Prefer 2×3 or vertical column over wide horizontal for small multiples
- **Colorbar:** Positioned with enough margin to not overlap adjacent elements
- **Annotation:** Canton labels on maps, value labels on bars

### Language

- English throughout, except ASTRA-specific terms
- When mixing: always explain German terms parenthetically
- Example: "Personenwagen (passenger cars, ASTRA classification)"

## Conventions

- `mappings.yaml` drives all classification — edit mappings, not code
- Unknown values → "Other" bucket + `warnings.log`
- `validate.py` cross-checks totals against `reference.yaml` (auto.swiss data)
- `warnings.log` is unified: unmapped values + plausibility checks
- Raw data not committed (~1GB), only aggregated CSVs and charts
- Charts regenerated on every pipeline run via GitHub Actions
- Archive year range is derived from current date — no hardcoded end year

## CI / GitHub Actions

- **Schedule:** 5th of each month at 08:00 UTC
- **Cache:** Raw data (~1GB) is compressed with zstd and cached between runs. Cache key includes `github.run_id` so each run saves its progress even on timeout.
- **Incremental downloads:** `download.py` uses HTTP `If-Modified-Since` to skip unchanged files. Downloads write to `.tmp` files and atomically rename on completion — partial downloads from killed processes are cleaned up on next run.
- **Soft timeout:** `DOWNLOAD_TIMEOUT=900` (15 min) stops starting new downloads before the 30-min job timeout, leaving headroom for cache save + processing steps.
- **Cache restore:** Uses `restore-keys` prefix matching so partial caches from earlier runs are reused. Extract condition uses `hashFiles()` (not `cache-hit`) since prefix matches don't set `cache-hit=true`.

## Data Notes

- ASTRA and auto.swiss both use the same MOFIS database but at different snapshot times. auto.swiss publishes on the 1st–3rd business day of the following month; ASTRA cumulates with retroactive corrections.
- Monthly differences typically cancel out over a year (overall diff: +0.027% across 2.67M registrations). July tends to show ASTRA < auto.swiss; January the opposite.
- 2% tolerance is well-calibrated for yearly comparisons (max observed: 0.07%). Monthly can reach ~5.5% for recent months due to snapshot timing.
- Canton codes in data include non-Swiss codes (A, BA, FL, M, P) — filtered out for map charts but harmlessly present in CSVs.
- 2016–2018 ASTRA files have a typo: "Erstinvekehrsetzung_Kanton" (missing 'r'). `process.py` auto-corrects this.

## File Structure

```
scripts/
  download.py     # Fetch raw data from ASTRA (incremental, cached)
  process.py      # Parse + aggregate → data/processed/
  validate.py     # Plausibility checks vs auto.swiss reference
  chart.py        # Generate charts → charts/
  report.py       # Monthly delta report → reports/
data/
  raw/            # NEUZU-*.txt (gitignored, ~1GB)
  processed/      # Aggregated CSVs + metadata.json
  ch-cantons.geojson
charts/           # Generated PNGs and GIFs
reports/          # Monthly markdown reports (YYYY-MM.md)
mappings.yaml     # Brand origins, groups, fuel types, colors
reference.yaml    # auto.swiss totals for plausibility checks
warnings.log      # Unified: unmapped values + plausibility checks
.github/workflows/update.yml  # Monthly CI pipeline
```

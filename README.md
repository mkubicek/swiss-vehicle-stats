# Swiss Vehicle Registration Analytics

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Automated analytics dashboard for Swiss new vehicle registrations, built from [ASTRA/IVZ Open Data](https://opendata.astra.admin.ch/ivzod/1000-Fahrzeuge_IVZ/1200-Neuzulassungen/).

A GitHub Actions pipeline downloads raw registration data monthly, aggregates it, generates charts, and produces a delta report with MoM, YoY, and YTD comparisons.

## Definitions

- **Scope:** `Fahrzeugart = Personenwagen` only (passenger cars). Excludes vans (Lieferwagen), trucks (Lastwagen), motorcycles (Motorrad), buses, tractors, and all other vehicle types.
- **EV:** BEV + PHEV + FCEV (Treibstoff: Elektrisch, Benzin/Elektrisch, Wasserstoff/Elektrisch, Elektrisch mit RE)
- **BEV:** Fully electric only (Treibstoff: Elektrisch, Elektrisch mit RE)

---

## Dashboard

### New Registrations Trend

Total passenger car (Personenwagen) registrations per year since 2016. The COVID-19 impact in 2020 is clearly visible, with the market not yet recovering to pre-pandemic levels.

![Yearly Registrations](charts/yearly_registrations.png)

### Powertrain Transition

How Switzerland's new car market is shifting from combustion to electric. Petrol and diesel are shrinking while BEV and PHEV grow year over year.

![Powertrain Mix](charts/powertrain_absolute.png)

### Brand Rankings Over Time

Position changes of the top 10 brands. Watch for brands climbing or falling through the ranks across a decade of data.

![Brand Rankings](charts/brand_rankings.png)

---

## EV Analytics

### The EV Wave

BEV + PHEV share of new passenger car registrations by canton, animated over time. Shows the electrification wave spreading across Switzerland.

![EV Wave](charts/ev_wave.gif)

### The BEV Race

Top 10 fully electric (BEV only) brands by trailing 12-month registrations. Watch Tesla's explosive rise and the competitive response.

![EV Race](charts/ev_race.gif)

### The BEV Brand Race (Cumulative)

Top 10 fully electric (BEV) brands by cumulative registrations since 2016. Watch Tesla build an early monopoly and then the competition slowly close in.

![Brand Race](charts/brand_race.gif)

### Geography of EV Taste

Location Quotient for top 6 BEV brands — where each brand over/underperforms vs the national average. LQ > 1.0 means overrepresented in that canton.

![EV Taste](charts/ev_taste_lq.png)

---

## How It Works

```
download.py -> process.py -> validate.py -> project.py -> chart.py -> report.py
```

1. **Download** -- fetches NEUZU.txt (current year) and archive files (2016–present) from ASTRA. Uses HTTP `If-Modified-Since` to skip unchanged files. Raw data is cached between CI runs.
2. **Process** -- parses TSV files with dtype optimization, applies `mappings.yaml` classifications, outputs aggregated CSVs
3. **Validate** -- plausibility checks against auto.swiss reference data, surfaces warnings
4. **Project** -- year-end projection based on YTD data with seasonal scaling factors
5. **Chart** -- generates charts with professional styling and dynamic attribution
6. **Report** -- produces a monthly delta report (MoM + YoY + YTD) in markdown

Runs automatically on the 5th of each month via GitHub Actions. Can also be triggered manually via `workflow_dispatch`.

## Classification

All classifications are driven by `mappings.yaml`:
- **Brand origin** -- brand heritage (Fiat = Italy, even though Stellantis is Dutch-registered)
- **Corporate group** -- parent company (Fiat = Stellantis, Audi = Volkswagen Group)
- **Fuel type** -- normalized powertrain categories
- **Colors** -- German to English translation
- **Drive type** -- AWD/FWD/RWD

Unknown values go to an "Other" bucket and are logged to `warnings.log` for review. Edit `mappings.yaml` to reclassify -- no code changes needed.

## Local Development

```bash
# Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Run pipeline
uv run scripts/download.py    # ~1GB total download
uv run scripts/process.py     # ~2-5 min
uv run scripts/project.py    # instant
uv run scripts/chart.py       # ~10 sec
uv run scripts/report.py      # instant
```

## Data Source

**Source:** [ASTRA IVZ Open Data](https://opendata.astra.admin.ch/ivzod/1000-Fahrzeuge_IVZ/1200-Neuzulassungen/1210-Datensaetze_monatlich/)
**Coverage:** 2016-present (~250k-320k passenger cars per year)
**Scope:** Passenger cars (Personenwagen) only

Raw data files (~100MB each) are not committed to this repo. Only aggregated CSVs and charts are tracked in git.

## Data Attribution

Vehicle registration data provided by the Swiss Federal Roads Office (ASTRA).

> Datenquelle: Bundesamt fuer Strassen ASTRA
> Source: Federal Roads Office FEDRO

Data is published under Swiss Open Government Data (OGD) guidelines. Free to use for informational, research, and commercial purposes with attribution. The analytics and charts in this repository are for **informational purposes only** and do not constitute official statistics.

## License

Code: [MIT](LICENSE)

Data: Swiss Federal Roads Office (ASTRA) -- [OGD Terms](https://www.astra.admin.ch/)

#!/usr/bin/env python3
"""Generate full-year projection from YTD data.

Outputs projection.json with projected full-year total and uncertainty bands.
Uses pro-rated YTD with lag correction against reference years.
"""

import json
import calendar
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "processed"

EXCLUDED_YEARS = [2020, 2021]  # COVID years
REF_YEAR_START = 2016


def load_metadata() -> dict:
    path = DATA_DIR / "metadata.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def write_null(reason: str):
    result = {"projection": None, "reason": reason}
    out_path = DATA_DIR / "projection.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  Projection: null ({reason})")
    print(f"  Saved: {out_path}")


def main():
    print("=== Generating Projection ===\n")

    monthly_path = DATA_DIR / "monthly_totals.csv"
    if not monthly_path.exists():
        print("ERROR: No processed data. Run process.py first.")
        return

    meta = load_metadata()
    if "data_date" not in meta:
        write_null("No metadata.json or data_date field")
        return

    data_date = datetime.strptime(meta["data_date"], "%Y-%m-%d").date()
    current_year = data_date.year

    df = pd.read_csv(monthly_path)
    current_data = df[df["year"] == current_year].sort_values("month")

    if current_data.empty:
        write_null(f"No data for {current_year}")
        return

    # Complete months = all months before data_date's month
    partial_month = data_date.month
    complete_months = list(range(1, partial_month))

    if len(complete_months) < 2:
        write_null(f"Only {len(complete_months)} complete month(s) — need at least 2")
        return

    # Current year counts
    complete_counts = current_data[current_data["month"].isin(complete_months)]
    complete_sum = int(complete_counts["count"].sum())

    partial_row = current_data[current_data["month"] == partial_month]
    partial_count = int(partial_row["count"].iloc[0]) if not partial_row.empty else 0

    ytd_actual = complete_sum + partial_count

    # Calendar progress for partial month
    days_in_partial = calendar.monthrange(current_year, partial_month)[1]
    days_elapsed = data_date.day
    partial_month_fraction = days_elapsed / days_in_partial

    # Reference years: complete 12-month years in range, excluding COVID
    months_per_year = df.groupby("year")["month"].nunique()
    ref_years = [y for y in range(REF_YEAR_START, current_year)
                 if y not in EXCLUDED_YEARS and months_per_year.get(y, 0) == 12]

    if not ref_years:
        write_null("No valid reference years")
        return

    # Capture ratio: observed daily rate vs historical daily rate for partial month
    historical_rates = []
    for y in ref_years:
        ref_month_count = df[(df["year"] == y) & (df["month"] == partial_month)]["count"].sum()
        dim = calendar.monthrange(y, partial_month)[1]
        historical_rates.append(ref_month_count / dim)

    mean_historical_rate = np.mean(historical_rates)
    observed_rate = partial_count / days_elapsed if days_elapsed > 0 else 0
    capture_ratio = observed_rate / mean_historical_rate if mean_historical_rate > 0 else 1.0

    # Effective fraction and method selection
    use_partial = 0.4 <= capture_ratio <= 1.3 and partial_count > 0
    effective_fraction = partial_month_fraction * capture_ratio if use_partial else 0.0
    method = "pro_rated_with_lag_correction" if use_partial else "complete_months_only"

    # Compute scaling factors from reference years
    factors = []
    for y in ref_years:
        ref_yearly = df[df["year"] == y]
        ref_full = ref_yearly["count"].sum()
        ref_complete = ref_yearly[ref_yearly["month"].isin(complete_months)]["count"].sum()

        if use_partial:
            ref_partial = ref_yearly[ref_yearly["month"] == partial_month]["count"].sum()
            ref_comparable = ref_complete + ref_partial * effective_fraction
        else:
            ref_comparable = ref_complete

        if ref_comparable > 0:
            factors.append(ref_full / ref_comparable)

    if not factors:
        write_null("Could not compute factors for any reference year")
        return

    mean_factor = np.mean(factors)
    std_factor = np.std(factors, ddof=1) if len(factors) > 1 else 0.0

    # Current comparable YTD (same basis as reference years)
    current_comparable = ytd_actual if use_partial else complete_sum

    projection = round(mean_factor * current_comparable)
    projection_low = round((mean_factor - std_factor) * current_comparable)
    projection_high = round((mean_factor + std_factor) * current_comparable)

    # YTD prorated: estimate including full partial month
    if use_partial and effective_fraction > 0:
        ytd_prorated = round(complete_sum + partial_count / effective_fraction)
    else:
        ytd_prorated = complete_sum

    cv_pct = round(std_factor / mean_factor * 100, 1) if mean_factor > 0 else 0.0

    result = {
        "year": current_year,
        "data_date": meta["data_date"],
        "complete_months": len(complete_months),
        "partial_month": partial_month if use_partial else None,
        "partial_month_fraction": round(partial_month_fraction, 3) if use_partial else None,
        "capture_ratio": round(capture_ratio, 2),
        "effective_fraction": round(effective_fraction, 3) if use_partial else None,
        "ytd_actual": ytd_actual,
        "ytd_prorated": ytd_prorated,
        "reference_years": ref_years,
        "excluded_years": EXCLUDED_YEARS,
        "mean_factor": round(mean_factor, 2),
        "std_factor": round(std_factor, 2),
        "projection": projection,
        "projection_low": projection_low,
        "projection_high": projection_high,
        "cv_pct": cv_pct,
        "method": method,
    }

    out_path = DATA_DIR / "projection.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"  Year: {current_year}")
    print(f"  Method: {method}")
    print(f"  Complete months: {len(complete_months)}")
    if use_partial:
        print(f"  Partial month: {partial_month} (fraction: {partial_month_fraction:.3f}, capture: {capture_ratio:.2f})")
    print(f"  YTD actual: {ytd_actual:,}")
    print(f"  Projection: ~{projection:,} ({projection_low:,} – {projection_high:,})")
    print(f"  CV: {cv_pct:.1f}%")
    print(f"  Saved: {out_path}")


if __name__ == "__main__":
    main()

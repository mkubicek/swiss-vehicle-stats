#!/usr/bin/env python3
"""Plausibility checks for processed ASTRA data.

Cross-references against auto.swiss reference data (MOFIS snapshots).
Merges unmapped warnings from process.py with plausibility warnings.
Always exits 0 — warnings only, never blocks the pipeline.
"""

import yaml
import pandas as pd
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "processed"
REFERENCE_FILE = ROOT / "reference.yaml"
WARNINGS_FILE = ROOT / "warnings.log"

# Tolerances
YEARLY_TOLERANCE = 0.02
MONTHLY_TOLERANCE = 0.02
BEV_MONTHLY_TOLERANCE = 0.05
MONTHLY_MIN = 5000
MONTHLY_MAX = 45000
MOM_SPIKE = 0.50


def load_reference() -> dict:
    if not REFERENCE_FILE.exists():
        return {}
    with open(REFERENCE_FILE) as f:
        return yaml.safe_load(f) or {}


def check_yearly_totals(monthly: pd.DataFrame, ref: dict) -> list[str]:
    """Compare yearly totals against auto.swiss reference."""
    warnings = []
    yearly = monthly.groupby("year")["count"].sum()
    for year, total in yearly.items():
        year = int(year)
        if year in ref and "total" in ref[year]:
            ref_total = ref[year]["total"]
            diff_pct = abs(total - ref_total) / ref_total
            if diff_pct > YEARLY_TOLERANCE:
                warnings.append(
                    f"plausibility:yearly_total:{year}: ASTRA={total:,.0f} vs auto.swiss={ref_total:,.0f} "
                    f"(diff={diff_pct:.1%}, tolerance={YEARLY_TOLERANCE:.0%})"
                )
    return warnings


def check_monthly_totals(monthly: pd.DataFrame, ref: dict) -> list[str]:
    """Compare monthly totals against auto.swiss reference."""
    warnings = []
    for _, row in monthly.iterrows():
        year, month, count = int(row["year"]), int(row["month"]), int(row["count"])
        if year in ref and "months" in ref[year] and month in ref[year]["months"]:
            ref_month = ref[year]["months"][month]
            if "total" in ref_month:
                ref_total = ref_month["total"]
                if ref_total > 0:
                    diff_pct = abs(count - ref_total) / ref_total
                    if diff_pct > MONTHLY_TOLERANCE:
                        warnings.append(
                            f"plausibility:monthly_total:{year}-{month:02d}: ASTRA={count:,.0f} vs auto.swiss={ref_total:,.0f} "
                            f"(diff={diff_pct:.1%})"
                        )
    return warnings


def check_bev_totals(ref: dict) -> list[str]:
    """Compare BEV totals against auto.swiss reference."""
    warnings = []
    fuel_path = DATA_DIR / "fuel_by_month.csv"
    if not fuel_path.exists():
        return warnings
    fuel = pd.read_csv(fuel_path)
    bev = fuel[fuel["fuel_type"] == "BEV"]
    bev_monthly = bev.groupby(["year", "month"])["count"].sum()

    for year in ref:
        if "months" not in ref[year]:
            continue
        for month in ref[year]["months"]:
            if "bev" not in ref[year]["months"][month]:
                continue
            ref_bev = ref[year]["months"][month]["bev"]
            actual = bev_monthly.get((year, month), 0)
            if ref_bev > 0:
                diff_pct = abs(actual - ref_bev) / ref_bev
                if diff_pct > BEV_MONTHLY_TOLERANCE:
                    warnings.append(
                        f"plausibility:bev_monthly:{year}-{month:02d}: ASTRA={actual:,.0f} vs auto.swiss={ref_bev:,.0f} "
                        f"(diff={diff_pct:.1%})"
                    )
    return warnings


def check_monthly_range(monthly: pd.DataFrame) -> list[str]:
    """Flag months outside expected range."""
    warnings = []
    for _, row in monthly.iterrows():
        year, month, count = int(row["year"]), int(row["month"]), int(row["count"])
        if count < MONTHLY_MIN:
            warnings.append(
                f"plausibility:monthly_range:{year}-{month:02d}: {count:,.0f} below minimum {MONTHLY_MIN:,.0f}"
            )
        elif count > MONTHLY_MAX:
            warnings.append(
                f"plausibility:monthly_range:{year}-{month:02d}: {count:,.0f} above maximum {MONTHLY_MAX:,.0f}"
            )
    return warnings


def check_complete_years(monthly: pd.DataFrame) -> list[str]:
    """Warn about years with missing months (except current year)."""
    warnings = []
    current_year = datetime.now().year
    for year, group in monthly.groupby("year"):
        year = int(year)
        if year == current_year:
            continue
        months = sorted(group["month"].unique())
        if len(months) < 12:
            missing = set(range(1, 13)) - set(months)
            warnings.append(
                f"plausibility:incomplete_year:{year}: missing months {sorted(missing)}"
            )
    return warnings


def check_yoy_spikes(monthly: pd.DataFrame) -> list[str]:
    """Flag suspicious month-over-month spikes (skip COVID years)."""
    warnings = []
    skip_years = {2020, 2021}
    monthly_sorted = monthly.sort_values(["year", "month"])
    prev_count = None
    prev_key = None
    for _, row in monthly_sorted.iterrows():
        year, month, count = int(row["year"]), int(row["month"]), int(row["count"])
        if prev_count is not None and prev_count > 0 and year not in skip_years:
            change = abs(count - prev_count) / prev_count
            if change > MOM_SPIKE:
                warnings.append(
                    f"plausibility:mom_spike:{year}-{month:02d}: {count:,.0f} vs prev {prev_count:,.0f} "
                    f"(change={change:.0%}, threshold={MOM_SPIKE:.0%})"
                )
        prev_count = count
        prev_key = (year, month)
    return warnings


def check_fuel_consistency() -> list[str]:
    """Verify fuel totals sum to monthly totals."""
    warnings = []
    fuel_path = DATA_DIR / "fuel_by_month.csv"
    monthly_path = DATA_DIR / "monthly_totals.csv"
    if not fuel_path.exists() or not monthly_path.exists():
        return warnings
    fuel = pd.read_csv(fuel_path)
    monthly = pd.read_csv(monthly_path)
    fuel_totals = fuel.groupby(["year", "month"])["count"].sum().reset_index()
    merged = monthly.merge(fuel_totals, on=["year", "month"], suffixes=("_total", "_fuel"))
    for _, row in merged.iterrows():
        if row["count_total"] != row["count_fuel"]:
            warnings.append(
                f"plausibility:fuel_mismatch:{int(row['year'])}-{int(row['month']):02d}: "
                f"total={int(row['count_total']):,} fuel_sum={int(row['count_fuel']):,}"
            )
    return warnings


def main():
    print("=== Plausibility Checks ===\n")

    monthly_path = DATA_DIR / "monthly_totals.csv"
    if not monthly_path.exists():
        print("No processed data. Run process.py first.")
        return

    monthly = pd.read_csv(monthly_path)
    ref = load_reference()

    all_warnings = []

    # Run checks
    if ref:
        print("Checking against auto.swiss reference...")
        all_warnings.extend(check_yearly_totals(monthly, ref))
        all_warnings.extend(check_monthly_totals(monthly, ref))
        all_warnings.extend(check_bev_totals(ref))
    else:
        print("No reference.yaml found, skipping reference checks.")

    print("Running range and consistency checks...")
    all_warnings.extend(check_monthly_range(monthly))
    all_warnings.extend(check_complete_years(monthly))
    all_warnings.extend(check_yoy_spikes(monthly))
    all_warnings.extend(check_fuel_consistency())

    # Merge with existing unmapped warnings from process.py
    existing_warnings = []
    if WARNINGS_FILE.exists():
        with open(WARNINGS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("plausibility:"):
                    continue
                # Strip existing unmapped: prefix to avoid doubling
                if line.startswith("unmapped:"):
                    line = line[len("unmapped:"):]
                existing_warnings.append(f"unmapped:{line}")

    # Write unified warnings
    combined = existing_warnings + [w for w in all_warnings]
    with open(WARNINGS_FILE, "w") as f:
        f.write(f"# Warnings — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write("# Unmapped values + plausibility checks\n\n")
        for w in sorted(combined):
            f.write(f"{w}\n")

    print(f"\nTotal warnings: {len(combined)}")
    if all_warnings:
        print(f"  Plausibility: {len(all_warnings)}")
        for w in all_warnings:
            print(f"    {w}")
    if existing_warnings:
        print(f"  Unmapped: {len(existing_warnings)}")
    print(f"\nSaved to {WARNINGS_FILE}")
    print("\nDone.")


if __name__ == "__main__":
    main()

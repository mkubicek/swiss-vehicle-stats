#!/usr/bin/env python3
"""Generate monthly delta report (MoM + YoY + YTD).

Produces a markdown report suitable for LinkedIn posting.
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from chart import display_brand

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "processed"
REPORT_DIR = ROOT / "reports"

def load_metadata() -> dict:
    """Load metadata.json if available."""
    path = DATA_DIR / "metadata.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def pct_change(new: float, old: float) -> str:
    """Calculate percentage change with arrow."""
    if old == 0:
        return "N/A"
    change = (new - old) / old * 100
    arrow = "+" if change >= 0 else ""
    return f"{arrow}{change:.1f}%"


def delta_str(new: float, old: float) -> str:
    """Format absolute + percentage change."""
    diff = new - old
    pct = pct_change(new, old)
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:,.0f} ({pct})"


def generate_report(target_year: int = None, target_month: int = None):
    """Generate delta report for the most recent complete month."""
    monthly = pd.read_csv(DATA_DIR / "monthly_totals.csv")

    # Find latest month with data
    if target_year and target_month:
        year, month = target_year, target_month
    else:
        latest = monthly.sort_values(["year", "month"]).iloc[-1]
        year, month = int(latest["year"]), int(latest["month"])

    month_name = MONTH_NAMES.get(month, str(month))

    # Current month
    current = monthly[(monthly["year"] == year) & (monthly["month"] == month)]["count"].sum()

    # Previous month
    prev_month = month - 1 if month > 1 else 12
    prev_month_year = year if month > 1 else year - 1
    prev = monthly[(monthly["year"] == prev_month_year) & (monthly["month"] == prev_month)]["count"].sum()

    # Same month last year
    yoy = monthly[(monthly["year"] == year - 1) & (monthly["month"] == month)]["count"].sum()

    # YTD
    ytd_current = monthly[(monthly["year"] == year) & (monthly["month"] <= month)]["count"].sum()
    ytd_prev = monthly[(monthly["year"] == year - 1) & (monthly["month"] <= month)]["count"].sum()

    # Fuel data
    fuel_monthly = pd.read_csv(DATA_DIR / "fuel_by_month.csv")
    fuel_current = fuel_monthly[(fuel_monthly["year"] == year) & (fuel_monthly["month"] == month)]
    fuel_prev_year = fuel_monthly[(fuel_monthly["year"] == year - 1) & (fuel_monthly["month"] == month)]

    fuel_current_dict = dict(zip(fuel_current["fuel_type"], fuel_current["count"]))
    fuel_prev_dict = dict(zip(fuel_prev_year["fuel_type"], fuel_prev_year["count"]))

    # Calculate plug-in share (BEV + PHEV + Diesel PHEV)
    bev = fuel_current_dict.get("BEV", 0)
    phev = fuel_current_dict.get("PHEV", 0) + fuel_current_dict.get("Diesel PHEV", 0)
    plugin_share = (bev + phev) / current * 100 if current > 0 else 0
    bev_share = bev / current * 100 if current > 0 else 0

    # Brand data
    brand_totals = pd.read_csv(DATA_DIR / "brand_totals.csv")
    top5 = brand_totals.head(5)

    # Momentum word
    if current > yoy:
        momentum = "grew" if (current - yoy) / yoy * 100 > 5 else "edged up"
    elif current < yoy:
        momentum = "declined" if (yoy - current) / yoy * 100 > 5 else "dipped slightly"
    else:
        momentum = "remained flat"

    # Build report
    meta = load_metadata()
    data_source = "ASTRA/IVZ Open Data"
    if "data_date" in meta:
        data_source += f" (as of {meta['data_date']})"
    lines = [
        f"# Swiss Vehicle Market Report: {month_name} {year}",
        "",
        f"*Generated {datetime.now().strftime('%Y-%m-%d')} | Data: {data_source}*",
        "",
        "---",
        "",
        "## Headlines",
        "",
        f"- **{current:,.0f}** new passenger cars registered in {month_name} {year}",
        f"- The market {momentum} compared to {month_name} {year - 1}",
        f"- BEV share: **{bev_share:.1f}%** | Plug-in share (BEV + PHEV): **{plugin_share:.1f}%**",
        "",
        "## Key Metrics",
        "",
        "| Metric | Value | Change |",
        "|--------|------:|-------:|",
        f"| {month_name} {year} | {current:,.0f} | — |",
    ]

    if prev > 0:
        lines.append(f"| vs. {MONTH_NAMES.get(prev_month, '')} {prev_month_year} (MoM) | {prev:,.0f} | {delta_str(current, prev)} |")
    if yoy > 0:
        lines.append(f"| vs. {month_name} {year - 1} (YoY) | {yoy:,.0f} | {delta_str(current, yoy)} |")
    if ytd_prev > 0:
        lines.append(f"| YTD {year} vs. YTD {year - 1} | {ytd_current:,.0f} vs. {ytd_prev:,.0f} | {delta_str(ytd_current, ytd_prev)} |")

    lines.extend([
        "",
        "## Powertrain Breakdown",
        "",
        "| Fuel Type | Count | Share | YoY Change |",
        "|-----------|------:|------:|-----------:|",
    ])

    for fuel in ["Petrol", "Diesel", "BEV", "PHEV", "Diesel PHEV", "Hybrid (Petrol)", "Hybrid (Diesel)"]:
        c = fuel_current_dict.get(fuel, 0)
        p = fuel_prev_dict.get(fuel, 0)
        share = c / current * 100 if current > 0 else 0
        yoy_change = pct_change(c, p) if p > 0 else "N/A"
        lines.append(f"| {fuel} | {c:,.0f} | {share:.1f}% | {yoy_change} |")

    lines.extend([
        "",
        "## Top 5 Brands (All-Time)",
        "",
        "| Rank | Brand | Total Registrations |",
        "|------|-------|--------------------:|",
    ])
    for i, row in top5.iterrows():
        lines.append(f"| {i + 1} | {display_brand(row['brand'])} | {row['count']:,.0f} |")

    lines.extend([
        "",
        "---",
        "",
        f"*Next report: {MONTH_NAMES.get(month % 12 + 1, 'January')} {year if month < 12 else year + 1}*",
        "",
    ])

    # Save
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{year}-{month:02d}.md"
    report_path = REPORT_DIR / filename
    report_path.write_text("\n".join(lines))
    print(f"Report saved: {report_path}")

    return report_path


def main():
    import sys
    print("=== Generating Delta Report ===\n")

    if not (DATA_DIR / "monthly_totals.csv").exists():
        print("ERROR: No processed data. Run process.py first.")
        return

    # Optional: specify year and month as arguments
    year = int(sys.argv[1]) if len(sys.argv) > 1 else None
    month = int(sys.argv[2]) if len(sys.argv) > 2 else None

    generate_report(year, month)
    print("\nDone.")


if __name__ == "__main__":
    main()

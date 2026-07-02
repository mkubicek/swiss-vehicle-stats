#!/usr/bin/env python3
"""Generate monthly delta report (MoM + YoY + YTD).

Produces a markdown report suitable for LinkedIn posting.
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from chart import (
    display_brand, china_bev_share_series, _t12m_matrix, POWERTRAIN_COLLAPSE,
)
from process import load_mappings, bloc, is_china_branded, BLOC_CHINA_OWNED

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


def china_owned_share_line(year: int, month: int, prev_year: int, prev_month: int):
    """One Headlines bullet: China-owned BEV share (T12M) + MoM delta in pp.

    Returns the markdown line, or None if brand BEV data is unavailable or the
    trailing window does not cover the target month (e.g. pre-2019 reports).
    """
    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        return None
    shares = china_bev_share_series(pd.read_csv(path), load_mappings())
    cur = shares[(shares["year"] == year) & (shares["month"] == month)]
    if cur.empty:
        return None
    cur_owned = float(cur["owned"].iloc[0]) * 100
    prev = shares[(shares["year"] == prev_year) & (shares["month"] == prev_month)]
    if prev.empty:
        return f"- China-owned BEV share (trailing 12 months): **{cur_owned:.1f}%**"
    delta = cur_owned - float(prev["owned"].iloc[0]) * 100
    return (f"- China-owned BEV share (trailing 12 months): **{cur_owned:.1f}%** "
            f"({delta:+.1f}pp MoM)")


def china_bloc_rank_line(year: int, month: int):
    """One Headlines bullet: where China-owned ranks among the seven manufacturer
    blocs (T12M share), naming the current leader. Mirrors
    charts/bev_bloc_share.png. Returns None if brand BEV data is unavailable or
    the trailing window does not cover the target month.
    """
    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    mappings = load_mappings()
    records = [(r.year, r.month, bloc(r.brand, mappings), int(r.bev_count))
               for r in df.itertuples(index=False)]
    months, keys, series = _t12m_matrix(records, start=(2019, 1))
    if (year, month) not in months:
        return None
    i = months.index((year, month))
    total = sum(series[k][i] for k in keys)
    if total == 0:
        return None
    ranked = sorted(((series[k][i] / total * 100, k) for k in keys), reverse=True)
    rank = next(n for n, (_, k) in enumerate(ranked, 1) if k == BLOC_CHINA_OWNED)
    china_share = next(s for s, k in ranked if k == BLOC_CHINA_OWNED)
    ordinal = {1: "largest", 2: "2nd-largest", 3: "3rd-largest"}.get(
        rank, f"{rank}th-largest")
    if rank == 1:
        return (f"- China-owned brands are the **largest** BEV manufacturer bloc "
                f"(**{china_share:.1f}%** of trailing-12-month registrations)")
    ahead = " and ".join(f"{k} ({s:.1f}%)" for s, k in ranked[:rank - 1])
    return (f"- China-owned brands are the {ordinal} BEV manufacturer bloc "
            f"(**{china_share:.1f}%**), behind {ahead}")


def china_branded_phev_share_line(year: int, month: int,
                                  prev_year: int, prev_month: int):
    """One Headlines bullet: PHEV share of China-branded registrations (T12M) —
    the 'second wave' indicator behind charts/china_powertrain_mix.png. Returns
    None if the powertrain file is missing (it lags the other outputs by a month
    right after release) or the window does not cover the target month.
    """
    path = DATA_DIR / "brand_powertrain_by_month.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    mappings = load_mappings()
    records = [(r.year, r.month, POWERTRAIN_COLLAPSE.get(r.powertrain, "Other"),
                int(r.count))
               for r in df.itertuples(index=False)
               if is_china_branded(r.brand, mappings)]
    if not records:
        return None
    months, keys, series = _t12m_matrix(records, start=(2019, 1))
    if (year, month) not in months:
        return None
    i = months.index((year, month))
    total = sum(series[k][i] for k in keys)
    if total == 0:
        return None
    cur = series.get("PHEV", [0] * len(months))[i] / total * 100
    line = ("- PHEV share of China-branded registrations "
            f"(trailing 12 months): **{cur:.0f}%**")
    if (prev_year, prev_month) in months:
        j = months.index((prev_year, prev_month))
        prev_total = sum(series[k][j] for k in keys)
        if prev_total:
            delta = cur - series.get("PHEV", [0] * len(months))[j] / prev_total * 100
            line += f" ({delta:+.1f}pp MoM)"
    return line


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
    ]

    china_line = china_owned_share_line(year, month, prev_month_year, prev_month)
    if china_line:
        lines.append(china_line)

    bloc_line = china_bloc_rank_line(year, month)
    if bloc_line:
        lines.append(bloc_line)

    phev_line = china_branded_phev_share_line(year, month, prev_month_year, prev_month)
    if phev_line:
        lines.append(phev_line)

    lines += [
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

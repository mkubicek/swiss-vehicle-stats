#!/usr/bin/env python3
"""Generate analytics charts from processed ASTRA data.

PNG and GIF output, professional style, dynamic attribution.
"""

import io
import json
import os
import subprocess
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.colors as mcolors
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "processed"
CHART_DIR = ROOT / "charts"

DPI = 150
FIGSIZE = (12, 7)

# Brands that should keep special casing (not .title())
BRAND_CASE = {
    "BMW": "BMW", "BYD": "BYD", "MG": "MG", "DS": "DS", "KGM": "KGM",
    "NIO": "NIO", "GWM": "GWM", "JAC": "JAC", "GAC": "GAC",
    "VW": "VW", "VOLKSWAGEN": "VW",
}


def display_brand(name: str) -> str:
    """Convert ALL CAPS brand to display form (title case, with overrides)."""
    upper = name.strip().upper()
    if upper in BRAND_CASE:
        return BRAND_CASE[upper]
    return name.strip().title()

# Dark theme (AGENTS.md styleguide)
BG = "#0d1117"
TEXT = "white"
SUBTLE = "#94a3b8"
GRID_COLOR = "#334155"

# Brand color palette — bright, high-contrast for dark backgrounds
BRAND_COLORS = {
    "TESLA": "#f72585", "BMW": "#4cc9f0", "VW": "#4ade80",
    "MERCEDES-BENZ": "#a78bfa", "AUDI": "#fb923c", "VOLVO": "#22d3ee",
    "HYUNDAI": "#f87171", "KIA": "#34d399", "PORSCHE": "#e879f9",
    "POLESTAR": "#fbbf24", "RENAULT": "#facc15", "SKODA": "#4ade80",
    "BYD": "#ff6b6b", "MG": "#fcd34d", "CUPRA": "#2dd4bf",
    "DACIA": "#60a5fa", "PEUGEOT": "#818cf8", "CITROEN": "#fb7185",
    "FIAT": "#f9a8d4", "OPEL": "#fde047", "FORD": "#7dd3fc",
    "TOYOTA": "#f87171", "MINI": "#86efac", "SMART": "#fdba74",
    "NIO": "#67e8f9", "NISSAN": "#fca5a5",
}

# Fallback palette for unknown brands
FALLBACK_COLORS = [
    "#4cc9f0", "#f72585", "#4ade80", "#fbbf24", "#a78bfa",
    "#fb923c", "#22d3ee", "#f87171", "#34d399", "#e879f9",
]

MONTH_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}

GEOJSON_PATH = ROOT / "data" / "ch-cantons.geojson"


def trailing_months(year: int, month: int, n: int = 12) -> list[tuple[int, int]]:
    """Return list of (year, month) tuples for n months ending at (year, month)."""
    result = []
    y, m = year, month
    for _ in range(n):
        result.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return result


def load_metadata() -> dict:
    """Load metadata.json if available."""
    path = DATA_DIR / "metadata.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def get_repo_url() -> str:
    """Get repo URL from environment or git remote."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return f"https://github.com/{repo}"
    try:
        url = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        if url.startswith("git@"):
            url = url.replace(":", "/").replace("git@", "https://")
        return url.removesuffix(".git")
    except Exception:
        return ""


def style_chart(ax, title: str, subtitle: str = "", xlabel: str = "", ylabel: str = ""):
    """Apply dark theme styling to an axis."""
    ax.set_facecolor(BG)
    ax.set_title(title, fontsize=16, fontweight="bold", pad=25 if subtitle else 15, color=TEXT)
    if subtitle:
        ax.text(0.5, 1.01, subtitle, transform=ax.transAxes,
                ha="center", va="bottom", fontsize=9, color=SUBTLE)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=12, color=TEXT)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=12, color=TEXT)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.tick_params(labelsize=10, colors=TEXT)
    ax.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")


def get_dark_attribution() -> str:
    """Attribution text for dark-theme charts (shorter, fits one line)."""
    from datetime import date
    repo = get_repo_url()
    meta = load_metadata()
    short = repo.replace("https://github.com/", "github.com/") if repo else ""
    data_str = "Data: ASTRA/IVZ Open Data"
    if "data_date" in meta:
        data_str += f" (as of {meta['data_date']})"
    return f"{short} | {data_str} | Generated {date.today()}"


def add_attribution(fig, prefix=""):
    text = f"{prefix} | {get_dark_attribution()}" if prefix else get_dark_attribution()
    fig.text(0.99, 0.01, text, ha="right", va="bottom",
             fontsize=8, color="#64748b", style="italic")


def save_chart(fig, name: str):
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    path = CHART_DIR / f"{name}.png"
    fig.savefig(path, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    size_kb = path.stat().st_size / 1024
    print(f"  Saved: {name}.png ({size_kb:.0f} KB)")


def load_projection() -> dict | None:
    """Load projection.json if it exists and has a non-null projection."""
    path = DATA_DIR / "projection.json"
    if not path.exists():
        return None
    with open(path) as f:
        data = json.load(f)
    if data.get("projection") is None:
        return None
    return data


def chart_yearly_registrations():
    """Total registrations as line chart with trend + optional projection."""
    df = pd.read_csv(DATA_DIR / "monthly_totals.csv")
    yearly = df.groupby("year")["count"].sum().reset_index()
    # Exclude partial current year (< 12 months)
    months_per_year = df.groupby("year")["month"].nunique()
    complete_years = months_per_year[months_per_year == 12].index
    yearly = yearly[yearly["year"].isin(complete_years) & (yearly["year"] >= 2016)]

    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=BG)
    ax.plot(yearly["year"], yearly["count"], marker="o", linewidth=2.5,
            color="#52b788", markersize=8, zorder=3)
    ax.fill_between(yearly["year"], yearly["count"], alpha=0.15, color="#52b788")

    for _, row in yearly.iterrows():
        ax.annotate(f"{row['count']:,.0f}", (row["year"], row["count"]),
                    textcoords="offset points", xytext=(0, 12),
                    ha="center", fontsize=8, fontweight="bold", color=TEXT)

    # Load projection and add YTD + projected point
    proj = load_projection()
    x_max = yearly["year"].max()
    xticks = list(yearly["year"])
    xticklabels = [str(int(y)) for y in yearly["year"]]

    if proj:
        proj_year = proj["year"]
        ytd = proj["ytd_actual"]
        projected = proj["projection"]
        proj_low = proj["projection_low"]
        proj_high = proj["projection_high"]
        x_max = proj_year

        # YTD point (diamond marker, lower alpha)
        ax.plot(proj_year, ytd, marker="D", markersize=9, color="#52b788",
                alpha=0.6, zorder=4, linestyle="none")
        ax.annotate(f"{ytd:,} YTD", (proj_year, ytd),
                    textcoords="offset points", xytext=(12, 0),
                    ha="left", va="center", fontsize=8, fontweight="bold", color=SUBTLE)

        # Dashed line from last complete year to projection
        last_complete_year = int(yearly["year"].iloc[-1])
        last_complete_count = int(yearly["count"].iloc[-1])
        ax.plot([last_complete_year, proj_year], [last_complete_count, projected],
                linestyle="--", linewidth=1.5, color="#52b788", alpha=0.5, zorder=2)

        # Projection point
        ax.plot(proj_year, projected, marker="o", markersize=8, color="#52b788",
                alpha=0.5, zorder=4, linestyle="none")
        margin = projected - proj_low
        ax.annotate(f"~{projected:,}\n±{margin:,}\n(projected)",
                    (proj_year, projected),
                    textcoords="offset points", xytext=(10, 0),
                    ha="left", va="center", fontsize=8, fontweight="bold",
                    color="#52b788", alpha=0.7, linespacing=1.3)

        # Uncertainty error bar
        ax.vlines(proj_year, proj_low, proj_high,
                  color="#52b788", alpha=0.35, linewidth=1.5, zorder=2)
        cap_w = 0.15
        for yval in (proj_low, proj_high):
            ax.hlines(yval, proj_year - cap_w, proj_year + cap_w,
                      color="#52b788", alpha=0.35, linewidth=1.5, zorder=2)

        xticks.append(proj_year)
        xticklabels.append(f"{proj_year}\n(YTD)")

    style_chart(ax,
                "New Passenger Car Registrations in Switzerland",
                subtitle="Personenwagen (passenger cars) per year | Source: ASTRA/IVZ Open Data",
                ylabel="Registrations")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.set_xlim(min(xticks) - 0.5, x_max + 0.5)
    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels)
    if proj:
        n_ref = len(proj["reference_years"])
        excl = "\u2013".join(str(y) for y in proj["excluded_years"][:1] + proj["excluded_years"][-1:])
        method_prefix = (
            f"Projection: YTD \u00d7 seasonal completion factor, "
            f"95% band from {n_ref} ref. years (excl. {excl})"
        )
        add_attribution(fig, prefix=method_prefix)
    else:
        add_attribution(fig)
    save_chart(fig, "yearly_registrations")


def chart_powertrain_absolute():
    """Powertrain mix as absolute stacked bar (annual)."""
    df = pd.read_csv(DATA_DIR / "fuel_by_month.csv")
    # Exclude partial current year
    monthly = pd.read_csv(DATA_DIR / "monthly_totals.csv")
    months_per_year = monthly.groupby("year")["month"].nunique()
    complete_years = months_per_year[months_per_year == 12].index

    yearly = df.groupby(["year", "fuel_type"])["count"].sum().reset_index()
    yearly = yearly[yearly["year"].isin(complete_years) & (yearly["year"] >= 2016)]

    order = ["Petrol", "Diesel", "BEV", "PHEV", "Diesel Hybrid", "Hydrogen", "CNG", "LPG", "Other"]
    color_map = {
        "Petrol": "#6b7280", "Diesel": "#4b5563", "BEV": "#2563eb",
        "PHEV": "#60a5fa", "Diesel Hybrid": "#93c5fd", "Hydrogen": "#16a34a",
        "CNG": "#f59e0b", "LPG": "#f97316", "Other": "#9ca3af",
    }

    pivot = yearly.pivot(index="year", columns="fuel_type", values="count").fillna(0)
    cols = [c for c in order if c in pivot.columns]
    pivot = pivot[cols]

    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=BG)
    bottom = pd.Series(0, index=pivot.index)
    for col in cols:
        ax.bar(pivot.index.astype(str), pivot[col], bottom=bottom,
               label=col, color=color_map.get(col, "#999"), width=0.7)
        bottom = bottom + pivot[col]

    style_chart(ax,
                "New Registrations by Powertrain",
                subtitle="Personenwagen (passenger cars) by fuel type per year | Complete years only",
                ylabel="Registrations")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    legend = ax.legend(loc="center left", fontsize=9, frameon=False,
                       bbox_to_anchor=(1.02, 0.5))
    for text in legend.get_texts():
        text.set_color(TEXT)
    ax.text(1.18, -0.06, get_dark_attribution(), transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "powertrain_absolute")


def chart_brand_rankings():
    """Brand ranking bump chart — position over time for top brands."""
    path = DATA_DIR / "brand_by_year.csv"
    if not path.exists():
        print("  Skip: brand rankings (no data)")
        return

    df = pd.read_csv(path)
    df = df[df["year"] >= 2016]

    top_brands = df.groupby("brand")["count"].sum().nlargest(10).index.tolist()
    ranked = df[df["brand"].isin(top_brands)].copy()
    ranked["rank"] = ranked.groupby("year")["count"].rank(ascending=False, method="min")

    fig, ax = plt.subplots(figsize=(14, 8), facecolor=BG)

    for i, brand in enumerate(top_brands):
        brand_data = ranked[ranked["brand"] == brand].sort_values("year")
        color = BRAND_COLORS.get(brand, FALLBACK_COLORS[i % len(FALLBACK_COLORS)])
        label = display_brand(brand)
        ax.plot(brand_data["year"], brand_data["rank"], marker="o", linewidth=2.5,
                color=color, markersize=7, zorder=3)
        if not brand_data.empty:
            last = brand_data.iloc[-1]
            ax.annotate(label, (last["year"], last["rank"]),
                        textcoords="offset points", xytext=(8, 0),
                        fontsize=9, fontweight="bold", color=color, va="center")

    ax.invert_yaxis()
    ax.set_yticks(range(1, 11))
    ax.set_yticklabels([f"#{i}" for i in range(1, 11)])
    style_chart(ax,
                "Top 10 Brand Rankings Over Time",
                subtitle="Personenwagen (passenger cars) ranked by annual registration volume",
                ylabel="Position")
    ax.set_xlabel("")
    all_years = sorted(ranked["year"].unique())
    ax.set_xticks(all_years)
    ax.set_xticklabels([str(int(y)) for y in all_years])
    ax.grid(axis="x", alpha=0.2, color=GRID_COLOR, linestyle="--")
    add_attribution(fig)
    save_chart(fig, "brand_rankings")


def chart_ev_wave():
    """Animated choropleth: EV share by canton over time with national sparkline."""
    path = DATA_DIR / "canton_ev_by_month.csv"
    if not path.exists() or not GEOJSON_PATH.exists():
        print("  Skip: ev_wave (no data or geojson)")
        return

    import geopandas as gpd
    cantons_geo = gpd.read_file(GEOJSON_PATH)
    all_cantons = sorted(cantons_geo["id"].tolist())
    df = pd.read_csv(path)

    # Build lookup tables for trailing window computation
    canton_lookup = {}
    nat_lookup = {}
    for _, row in df.iterrows():
        c, y, m = row["canton"], int(row["year"]), int(row["month"])
        ev, tot = int(row["ev_count"]), int(row["total_count"])
        canton_lookup[(c, y, m)] = (ev, tot)
        key = (y, m)
        prev = nat_lookup.get(key, (0, 0))
        nat_lookup[key] = (prev[0] + ev, prev[1] + tot)

    # Determine frame range from data
    years = sorted(df["year"].unique())
    target_months = [(y, m) for y in range(min(years), max(years) + 1)
                     for m in range(1, 13)
                     if (y, m) in nat_lookup]

    wave_cmap = mcolors.LinearSegmentedColormap.from_list("ev_seq",
        ["#0d1117", "#132a13", "#1e4d2b", "#2d6a4f", "#40916c",
         "#52b788", "#74c69d", "#95d5b2", "#b7e4c7", "#d8f3dc"], N=256)

    # Precompute all frames
    frames_data = []
    sparkline_data = []
    for y, m in target_months:
        trl = trailing_months(y, m, 12)
        canton_shares = {}
        for c in all_cantons:
            ev = sum(canton_lookup.get((c, ty, tm), (0, 0))[0] for ty, tm in trl)
            tot = sum(canton_lookup.get((c, ty, tm), (0, 0))[1] for ty, tm in trl)
            canton_shares[c] = (ev / tot * 100) if tot > 0 else 0
        nat_ev = sum(nat_lookup.get((ty, tm), (0, 0))[0] for ty, tm in trl)
        nat_tot = sum(nat_lookup.get((ty, tm), (0, 0))[1] for ty, tm in trl)
        nat_pct = (nat_ev / nat_tot * 100) if nat_tot > 0 else 0
        sparkline_data.append(nat_pct)
        frames_data.append(((y, m), canton_shares, nat_pct))

    wave_max = max(max(cd.values()) for _, cd, _ in frames_data)
    wave_norm = mcolors.Normalize(vmin=0, vmax=max(wave_max, 35))
    attribution = get_dark_attribution()

    images = []
    for i, ((y, m), canton_shares, nat_pct) in enumerate(frames_data):
        fig = plt.figure(figsize=(18, 10), facecolor=BG)
        ax_map = fig.add_axes([0.02, 0.08, 0.55, 0.78])
        ax_cb = fig.add_axes([0.58, 0.08, 0.015, 0.78])
        ax_spark = fig.add_axes([0.66, 0.08, 0.31, 0.78])

        # Title block
        fig.text(0.30, 0.97, f"{MONTH_NAMES[m].upper()} {y}", ha="center", va="top",
                 fontsize=32, fontweight="bold", color="#fbbf24", fontfamily="monospace")
        fig.text(0.30, 0.91, "EV Share of New Car Registrations by Canton",
                 ha="center", va="top", fontsize=16, fontweight="bold", color=TEXT)
        fig.text(0.30, 0.88,
                 "BEV + PHEV + FCEV as % of new Personenwagen (passenger car) registrations | 12-month trailing average",
                 ha="center", va="top", fontsize=8, color=SUBTLE)
        fig.text(0.82, 0.97, f"{nat_pct:.1f}%", ha="center", va="top",
                 fontsize=36, fontweight="bold", color="#52b788")
        fig.text(0.82, 0.90, "National Average", ha="center", va="top",
                 fontsize=9, color=SUBTLE)

        # Map
        ax_map.set_facecolor(BG)
        cdf = pd.DataFrame([{"canton": c, "ev_pct": v} for c, v in canton_shares.items()])
        merged = cantons_geo.merge(cdf, left_on="id", right_on="canton", how="left")
        merged["ev_pct"] = merged["ev_pct"].fillna(0)
        merged.plot(column="ev_pct", ax=ax_map, cmap=wave_cmap, edgecolor="#1e293b",
                    linewidth=0.8, legend=False, norm=wave_norm)
        for _, row in merged.iterrows():
            centroid = row.geometry.centroid
            v = row["ev_pct"]
            color = "black" if v > wave_max * 0.5 else "#c0c0c0"
            ax_map.annotate(f"{row['id']}\n{v:.0f}%", (centroid.x, centroid.y),
                            ha="center", va="center", fontsize=7, fontweight="bold", color=color)
        ax_map.set_axis_off()

        # Colorbar
        sm = plt.cm.ScalarMappable(cmap=wave_cmap, norm=wave_norm)
        sm.set_array([])
        cbar = fig.colorbar(sm, cax=ax_cb)
        cbar.set_label("EV % of New Registrations", fontsize=8, color=TEXT)
        cbar.ax.yaxis.set_tick_params(color=TEXT)
        plt.setp(cbar.ax.yaxis.get_ticklabels(), color=TEXT, fontsize=7)

        # Sparkline
        ax_spark.set_facecolor(BG)
        spark_x = list(range(len(sparkline_data[:i + 1])))
        ax_spark.fill_between(spark_x, sparkline_data[:i + 1], alpha=0.3, color="#52b788")
        ax_spark.plot(spark_x, sparkline_data[:i + 1], color="#52b788", linewidth=2)
        if spark_x:
            ax_spark.plot(spark_x[-1], sparkline_data[i], "o", color="#fbbf24", markersize=8, zorder=5)
        ax_spark.set_xlim(0, len(target_months) - 1)
        ax_spark.set_ylim(0, max(sparkline_data) * 1.15)
        ax_spark.set_title("National EV % of New Registrations", fontsize=10,
                           color=TEXT, fontweight="bold")
        ax_spark.spines["top"].set_visible(False)
        ax_spark.spines["right"].set_visible(False)
        ax_spark.spines["bottom"].set_color(GRID_COLOR)
        ax_spark.spines["left"].set_color(GRID_COLOR)
        ax_spark.tick_params(colors=TEXT, labelsize=7)
        year_ticks = [j for j, (yy, mm) in enumerate(target_months[:i + 1]) if mm == 1]
        ax_spark.set_xticks(year_ticks)
        ax_spark.set_xticklabels([str(target_months[j][0]) for j in year_ticks], fontsize=7)
        ax_spark.set_ylabel("%", fontsize=9, color=TEXT)

        fig.text(0.99, 0.01, attribution, ha="right", va="bottom",
                 fontsize=11, color="#64748b", style="italic")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        images.append(Image.open(buf).copy())
        if (i + 1) % 24 == 0:
            print(f"    ev_wave: frame {i + 1}/{len(frames_data)}")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "ev_wave.gif"
    durations = [300] * len(images)
    durations[-1] = 3000
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"  Saved: ev_wave.gif ({size_mb:.1f} MB)")


def chart_ev_race():
    """Animated bar chart race: top 10 BEV brands by trailing 12-month registrations."""
    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: ev_race (no data)")
        return

    df = pd.read_csv(path)

    # Build lookup: (brand, year, month) -> bev_count
    bev_lookup = {}
    for _, row in df.iterrows():
        bev_lookup[(row["brand"], int(row["year"]), int(row["month"]))] = int(row["bev_count"])

    all_brands = df["brand"].unique().tolist()
    years = sorted(df["year"].unique())
    target_months = [(y, m) for y in range(min(years), max(years) + 1)
                     for m in range(1, 13)
                     if any(bev_lookup.get((b, y, m)) for b in all_brands[:5])]

    attribution = get_dark_attribution()

    # Precompute all frames to find global max for fixed x-axis
    all_frame_data = []
    global_max = 0
    for y, m in target_months:
        trl = trailing_months(y, m, 12)
        brand_totals = {}
        for brand in all_brands:
            total = sum(bev_lookup.get((brand, ty, tm), 0) for ty, tm in trl)
            if total > 0:
                brand_totals[brand] = total
        top10 = sorted(brand_totals.items(), key=lambda x: x[1], reverse=True)[:10]
        if top10:
            global_max = max(global_max, top10[0][1])
        all_frame_data.append(((y, m), top10))

    fixed_xlim = global_max * 1.18
    images = []

    for i, ((y, m), top10) in enumerate(all_frame_data):
        if not top10:  # pragma: no cover
            continue

        brands = [b for b, _ in reversed(top10)]
        counts = [c for _, c in reversed(top10)]

        fig, ax = plt.subplots(figsize=(14, 8), facecolor=BG)
        ax.set_facecolor(BG)

        bar_colors = [BRAND_COLORS.get(b, FALLBACK_COLORS[j % len(FALLBACK_COLORS)]) for j, b in enumerate(brands)]
        bars = ax.barh(range(len(brands)), counts, color=bar_colors, height=0.7, edgecolor="none")

        for j, (brand, count) in enumerate(zip(brands, counts)):
            ax.text(count + global_max * 0.01, j, f" {count:,}",
                    va="center", ha="left", fontsize=10, color=TEXT, fontweight="bold")

        ax.set_yticks(range(len(brands)))
        ax.set_yticklabels([display_brand(b) for b in brands], fontsize=11, color=TEXT, fontweight="bold")
        ax.set_xlim(0, fixed_xlim)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color(GRID_COLOR)
        ax.spines["left"].set_color(GRID_COLOR)
        ax.grid(axis="x", alpha=0.2, color=GRID_COLOR)

        fig.text(0.50, 0.97, f"{MONTH_NAMES[m].upper()} {y}", ha="center", va="top",
                 fontsize=28, fontweight="bold", color="#fbbf24", fontfamily="monospace")
        fig.text(0.50, 0.92, "Top 10 BEV Brands — Trailing 12-Month Registrations",
                 ha="center", va="top", fontsize=14, fontweight="bold", color=TEXT)
        fig.text(0.50, 0.895,
                 "Fully electric (BEV) new Personenwagen (passenger cars) only",
                 ha="center", va="top", fontsize=8, color=SUBTLE)

        fig.subplots_adjust(top=0.85, bottom=0.08, left=0.18, right=0.92)

        fig.text(0.99, 0.005, attribution, ha="right", va="bottom",
                 fontsize=11, color="#64748b", style="italic")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        images.append(Image.open(buf).copy())
        if (i + 1) % 24 == 0:
            print(f"    ev_race: frame {i + 1}/{len(target_months)}")

    if not images:
        print("  Skip: ev_race (no frames)")
        return

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "ev_race.gif"
    durations = [300] * len(images)
    durations[-1] = 3000
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"  Saved: ev_race.gif ({size_mb:.1f} MB)")


def chart_brand_race():
    """Animated bar chart race: top 10 BEV brands by cumulative registrations since data start."""
    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: brand_race (no data)")
        return

    df = pd.read_csv(path)

    # Build lookup: (brand, year, month) -> bev_count
    bev_lookup = {}
    for _, row in df.iterrows():
        bev_lookup[(row["brand"], int(row["year"]), int(row["month"]))] = int(row["bev_count"])

    all_brands = df["brand"].unique().tolist()
    years = sorted(df["year"].unique())
    target_months = [(y, m) for y in range(min(years), max(years) + 1)
                     for m in range(1, 13)
                     if any(bev_lookup.get((b, y, m)) for b in all_brands[:5])]

    attribution = get_dark_attribution()
    start_year = min(years)

    # Precompute cumulative totals per frame
    cumulative = {b: 0 for b in all_brands}
    all_frame_data = []
    for y, m in target_months:
        # Add this month's counts to running totals
        for brand in all_brands:
            cumulative[brand] += bev_lookup.get((brand, y, m), 0)
        top10 = sorted(((b, c) for b, c in cumulative.items() if c > 0),
                        key=lambda x: x[1], reverse=True)[:10]
        all_frame_data.append(((y, m), top10))

    images = []

    for i, ((y, m), top10) in enumerate(all_frame_data):
        if not top10:  # pragma: no cover
            continue

        brands = [b for b, _ in reversed(top10)]
        counts = [c for _, c in reversed(top10)]
        frame_max = counts[-1]  # leader is last (reversed order)

        fig, ax = plt.subplots(figsize=(14, 8), facecolor=BG)
        ax.set_facecolor(BG)

        bar_colors = [BRAND_COLORS.get(b, FALLBACK_COLORS[j % len(FALLBACK_COLORS)]) for j, b in enumerate(brands)]
        bars = ax.barh(range(len(brands)), counts, color=bar_colors, height=0.7, edgecolor="none")

        for j, (brand, count) in enumerate(zip(brands, counts)):
            ax.text(count + frame_max * 0.01, j, f" {count:,}",
                    va="center", ha="left", fontsize=10, color=TEXT, fontweight="bold")

        ax.set_yticks(range(len(brands)))
        ax.set_yticklabels([display_brand(b) for b in brands], fontsize=11, color=TEXT, fontweight="bold")
        ax.set_xlim(0, frame_max * 1.18)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color(GRID_COLOR)
        ax.spines["left"].set_color(GRID_COLOR)
        ax.grid(axis="x", alpha=0.2, color=GRID_COLOR)

        fig.text(0.50, 0.97, f"{MONTH_NAMES[m].upper()} {y}", ha="center", va="top",
                 fontsize=28, fontweight="bold", color="#fbbf24", fontfamily="monospace")
        fig.text(0.50, 0.92, f"Top 10 BEV Brands — Total Registrations Since {start_year}",
                 ha="center", va="top", fontsize=14, fontweight="bold", color=TEXT)
        fig.text(0.50, 0.895,
                 "Fully electric (BEV) new Personenwagen (passenger cars) | Source: ASTRA/IVZ Open Data",
                 ha="center", va="top", fontsize=8, color=SUBTLE)

        fig.subplots_adjust(top=0.85, bottom=0.08, left=0.18, right=0.92)

        fig.text(0.99, 0.005, attribution, ha="right", va="bottom",
                 fontsize=11, color="#64748b", style="italic")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        images.append(Image.open(buf).copy())
        if (i + 1) % 24 == 0:
            print(f"    brand_race: frame {i + 1}/{len(target_months)}")

    if not images:
        print("  Skip: brand_race (no frames)")
        return

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "brand_race.gif"
    durations = [300] * len(images)
    durations[-1] = 3000
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"  Saved: brand_race.gif ({size_mb:.1f} MB)")


def chart_ev_taste():
    """Static heatmap: Location Quotient for top BEV brands by canton."""
    bev_path = DATA_DIR / "brand_canton_bev.csv"
    if not bev_path.exists():
        print("  Skip: ev_taste (no data)")
        return

    import geopandas as gpd

    df = pd.read_csv(bev_path)

    # Use only Swiss cantons (exclude FL, special codes)
    cantons_geo = gpd.read_file(GEOJSON_PATH)
    valid_cantons = set(cantons_geo["id"].tolist())
    df = df[df["canton"].isin(valid_cantons)]

    # Top 6 BEV brands nationally
    brand_totals = df.groupby("brand")["bev_count"].sum()
    top_brands = brand_totals.nlargest(6).index.tolist()

    # Compute LQ: (brand_share_in_canton) / (brand_share_nationally)
    df_top = df[df["brand"].isin(top_brands)]
    national_total = df_top["bev_count"].sum()
    national_by_brand = df_top.groupby("brand")["bev_count"].sum()

    canton_totals = df_top.groupby("canton")["bev_count"].sum()
    canton_brand = df_top.groupby(["canton", "brand"])["bev_count"].sum().reset_index()

    lq_rows = []
    for _, row in canton_brand.iterrows():
        c, b, count = row["canton"], row["brand"], row["bev_count"]
        ct = canton_totals.get(c, 0)
        nt = national_by_brand.get(b, 0)
        if ct > 0 and nt > 0 and national_total > 0:
            canton_share = count / ct
            national_share = nt / national_total
            lq = canton_share / national_share
            lq_rows.append({"canton": c, "brand": b, "lq": lq})

    lq_df = pd.DataFrame(lq_rows)
    if lq_df.empty:
        print("  Skip: ev_taste (insufficient data)")
        return

    pivot = lq_df.pivot(index="canton", columns="brand", values="lq").fillna(0)
    pivot = pivot[top_brands]

    # Sort cantons by mean LQ for visual grouping
    pivot = pivot.loc[pivot.mean(axis=1).sort_values(ascending=False).index]

    # Plot
    fig, ax = plt.subplots(figsize=(12, 14), facecolor=BG)
    ax.set_facecolor(BG)

    lq_cmap = mcolors.LinearSegmentedColormap.from_list("lq",
        ["#1e3a5f", "#0d1117", "#2d1b00", "#8b4000", "#ff6600"], N=256)
    norm = mcolors.TwoSlopeNorm(vmin=0, vcenter=1.0, vmax=max(3.0, pivot.max().max()))

    im = ax.imshow(pivot.values, cmap=lq_cmap, norm=norm, aspect="auto")

    # Labels
    ax.set_xticks(range(len(top_brands)))
    ax.set_xticklabels([display_brand(b) for b in top_brands], fontsize=11,
                       color=TEXT, fontweight="bold", rotation=0)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index.tolist(), fontsize=10, color=TEXT)
    ax.tick_params(colors=TEXT, length=0)
    ax.xaxis.set_ticks_position("top")

    # Annotate cells
    for row_i in range(pivot.shape[0]):
        for col_i in range(pivot.shape[1]):
            val = pivot.iloc[row_i, col_i]
            color = "black" if val > 1.5 else TEXT
            ax.text(col_i, row_i, f"{val:.2f}", ha="center", va="center",
                    fontsize=8, color=color, fontweight="bold" if abs(val - 1) > 0.3 else "normal")

    # Colorbar
    cbar = fig.colorbar(im, ax=ax, shrink=0.5, pad=0.04)
    cbar.set_label("Location Quotient (1.0 = national average)", fontsize=9, color=TEXT, labelpad=10)
    cbar.ax.yaxis.set_tick_params(color=TEXT)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=TEXT, fontsize=8)

    fig.text(0.50, 0.97, "Geography of EV Taste", ha="center", va="top",
             fontsize=18, fontweight="bold", color=TEXT)
    fig.text(0.50, 0.945,
             "Location Quotient for top 6 BEV brands by canton | LQ > 1.0 = overrepresented vs national average",
             ha="center", va="top", fontsize=9, color=SUBTLE)

    # Attribution anchored to bottom of axes (works with bbox_inches="tight")
    ax.text(1.15, -0.03, get_dark_attribution(), transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#64748b", style="italic")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "ev_taste_lq.png"
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    size_kb = out.stat().st_size / 1024
    print(f"  Saved: ev_taste_lq.png ({size_kb:.0f} KB)")


def main():
    print("=== Generating Charts ===\n")

    if not (DATA_DIR / "monthly_totals.csv").exists():
        print("ERROR: No processed data. Run process.py first.")
        return

    chart_yearly_registrations()
    chart_powertrain_absolute()
    chart_brand_rankings()
    chart_ev_wave()
    chart_ev_race()
    chart_brand_race()
    chart_ev_taste()

    print("\nDone.")


if __name__ == "__main__":
    main()

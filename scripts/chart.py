#!/usr/bin/env python3
"""Generate analytics charts from processed ASTRA data.

PNG and GIF output, professional style, dynamic attribution.
"""

import io
import json
import os
import re
import subprocess
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.colors as mcolors
from matplotlib.offsetbox import TextArea, HPacker, AnnotationBbox
from pathlib import Path
from PIL import Image

from process import (
    load_mappings, is_china_owned, is_china_branded, detect_entry_month,
    ENTRY_MIN_MONTHLY, bloc, safe_map, BLOC_ORDER, BLOC_CHINA_OWNED,
    BLOC_TESLA, BLOC_VW, BLOC_EU_LEGACY, BLOC_KOREAN, BLOC_JAPANESE, BLOC_OTHER,
)

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "processed"
CHART_DIR = ROOT / "charts"

DPI = 150
FIGSIZE = (12, 7)

# Brands that should keep special casing (not .title())
BRAND_CASE = {
    "BMW": "BMW", "BYD": "BYD", "MG": "MG", "DS": "DS", "KGM": "KGM",
    "NIO": "NIO", "GWM": "GWM", "JAC": "JAC", "GAC": "GAC",
    "VW": "VW", "VOLKSWAGEN": "VW", "XPENG": "XPeng",
}


def display_brand(name: str) -> str:
    """Convert ALL CAPS brand to display form (title case, with overrides)."""
    upper = name.strip().upper()
    if upper in BRAND_CASE:
        return BRAND_CASE[upper]
    return name.strip().title()


def display_model(name: str) -> str:
    """Convert ALL CAPS model key ("VW TIGUAN") to display ("VW Tiguan").

    Already-prettied values from mappings.yaml > model_overrides (containing
    any lowercase letter) pass through unchanged.
    """
    if any(c.islower() for c in name):
        return name
    tokens = name.split()
    if not tokens:
        return name

    def case_chunk(ch: str) -> str:
        # Digits => model designator (Q3, 30, ID.3, X1) — keep as-is.
        # <=3 chars => abbreviation (GLC, AMG, GT, ROC, HR) — keep as-is.
        # Otherwise title-case (CROSS->Cross, GOLF->Golf, MOKKA->Mokka).
        if not ch or any(c.isdigit() for c in ch):
            return ch
        return ch if len(ch) <= 3 else ch.title()

    def case_token(tok: str) -> str:
        # Recase each chunk of a hyphen/dot-joined token, keeping the separators
        # ("T-CROSS"->"T-Cross", "MOKKA-X"->"Mokka-X", but "ID.3"/"CX-30"/"T-ROC"
        # stay intact because their chunks are digits or <=3 chars).
        if "-" in tok or "." in tok:
            return re.sub(r"[^-.]+", lambda mm: case_chunk(mm.group()), tok)
        return case_chunk(tok)

    return " ".join([display_brand(tokens[0])] + [case_token(t) for t in tokens[1:]])

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

# Ownership-bloc colors for the Chinese-BEV charts. Kept in code alongside
# BRAND_COLORS (the repo keeps chart colors in code; mappings.yaml's `colors:`
# is the unrelated German→English paint-colour map).
#   china_owned   red family, echoes BYD #ff6b6b, distinct from Tesla #f72585
#   china_branded orange, clearly subordinate to china_owned
#   tesla_ref     existing Tesla color
#   rest_ref      neutral slate for "all other brands" / negative bars
BLOC_COLORS = {
    "china_owned": "#ef4444",
    "china_branded": "#f97316",
    "tesla_ref": "#f72585",
    "rest_ref": "#64748b",
    "mg_ref": "#fcd34d",
}

# Manufacturer-bloc fills for the displacement chart (chart_bev_bloc_share).
# Design intent: only the China wedge is saturated; every legacy bloc is muted so
# the growing red wedge is the one thing the eye tracks. Keyed by the bloc names
# from process.BLOC_ORDER.
DISPLACEMENT_COLORS = {
    BLOC_CHINA_OWNED: BLOC_COLORS["china_owned"],
    BLOC_TESLA: "#a63d5f",      # muted magenta (echoes Tesla #f72585 without competing)
    BLOC_VW: "#3f9068",         # muted green (echoes VW #4ade80)
    BLOC_EU_LEGACY: "#475569",  # slate
    BLOC_KOREAN: "#a35563",     # muted red-orange
    BLOC_JAPANESE: "#5a7d99",   # muted sky
    BLOC_OTHER: "#334155",      # darkest slate
}

# Corporate-group fills for chart_china_groups — each group takes its lead
# brand's BRAND_COLORS entry so the decomposition stays visually tied to the
# brands readers already know from the ev_race / bloc charts.
CHINA_GROUP_COLORS = {
    "Geely": BRAND_COLORS["VOLVO"],    # #22d3ee — Volvo is Geely's volume leader
    "SAIC": BRAND_COLORS["MG"],        # #fcd34d
    "BYD": BRAND_COLORS["BYD"],        # #ff6b6b
    "Leapmotor": "#5eead4",
    "XPeng": "#c084fc",
    "Chery": "#fb923c",
    "GWM": "#facc15",
    "JAC": "#a3e635",
    "Seres": "#f9a8d4",
    "NIO": BRAND_COLORS["NIO"],        # #67e8f9
}
CHINA_GROUP_OTHER_COLOR = "#64748b"
# Smart is a 50/50 Geely–Mercedes JV kept under Mercedes-Benz in the global
# brand_group map; for the China-only decomposition it is counted fully under
# Geely (stated on-chart and in METHODOLOGY.md — no fractional attribution).
CHINA_GROUP_OVERRIDES = {"SMART": "Geely"}

# Powertrain-category colors for chart_china_powertrain_mix, reusing the house
# vocabulary from chart_powertrain_absolute so categories read the same across
# the dashboard. Collapsed categories map to their representative color.
POWERTRAIN_MIX_COLORS = {
    "BEV": "#2563eb", "PHEV": "#60a5fa", "HEV": "#a3e635",
    "ICE": "#6b7280", "Other": "#9ca3af",
}
# Render-time floor for a challenger to earn a panel (chart_china_challengers).
CHALLENGER_MIN_T12M = 50

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

    order = ["Petrol", "Diesel", "BEV", "PHEV", "Diesel PHEV",
             "Hybrid (Petrol)", "Hybrid (Diesel)", "Hydrogen", "CNG", "LPG", "Other"]
    color_map = {
        "Petrol": "#6b7280", "Diesel": "#4b5563", "BEV": "#2563eb",
        "PHEV": "#60a5fa", "Diesel PHEV": "#818cf8",
        "Hybrid (Petrol)": "#a3e635", "Hybrid (Diesel)": "#65a30d",
        "Hydrogen": "#16a34a", "CNG": "#f59e0b", "LPG": "#f97316", "Other": "#9ca3af",
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
        fig.text(0.30, 0.91, "BEV Share of New Car Registrations by Canton",
                 ha="center", va="top", fontsize=16, fontweight="bold", color=TEXT)
        fig.text(0.30, 0.88,
                 "Fully electric (BEV) as % of new Personenwagen (passenger car) registrations | 12-month trailing average",
                 ha="center", va="top", fontsize=8, color=SUBTLE)
        fig.text(0.82, 0.97, f"{nat_pct:.1f}%", ha="center", va="top",
                 fontsize=36, fontweight="bold", color="#52b788")
        fig.text(0.82, 0.90, "National BEV %", ha="center", va="top",
                 fontsize=11, fontweight="bold", color=TEXT)
        fig.text(0.82, 0.87, "12-month trailing average", ha="center", va="top",
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
        cbar.set_label("BEV % of New Registrations", fontsize=8, color=TEXT)
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
        ax_spark.set_title("", fontsize=10)
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


# Models that are aggregation artifacts of normalize_model()'s auto-rule, not
# real nameplates. Keep this list tiny — it should only contain sub-brands or
# trim families that ASTRA stores in Typ1 where the first token isn't a model
# name (so the key mixes several distinct nameplates). Filtered out of
# chart_model_race. To split one into real nameplates instead of filtering,
# add per-variant model_overrides/model_merges in mappings.yaml.
# Mercedes AMG and Audi RS are NO LONGER filtered — normalize_model now parses
# them back to their base nameplate (AMG C 63 -> C-Class, RS 3 -> A3), so they
# fold into the base model instead of forming a cross-segment bucket.
#   TOYOTA GR — generic fallback only. Current GR Yaris / GR86 / GR Corolla
#   rows are split by model_overrides; a bare future "GR" row still can't be
#   placed, so keep the fallback filtered.
# (LAND ROVER RR is no longer filtered — it's relabelled to "Land Rover Range
#  Rover" via model_merges and classified Large SUV; all RR variants are large.)
MODEL_ARTIFACTS = {"TOYOTA GR"}

# First frame year for chart_model_race. Data goes back to 2016 but the
# pre-2020 era has fewer model debuts and weaker climbers/fallers signal,
# so the race starts here. Bump when the data window shifts.
MODEL_RACE_START_YEAR = 2020


def chart_model_race():
    """Animated dual-panel race: largest year-over-year model gains and losses, by month.

    Same layout as the static climbers chart (two stacked panels, names left,
    bars growing right, shared scale, "+" / "−" signs encoding direction),
    but plays as a GIF — one frame per month from Jan 2020 onward, with a big
    monospace date label at the top per the ev_race convention.

    This chart benefits from animation in a way the absolute-top-15 race
    did not: climbers and fallers are by definition the most volatile rows,
    so each frame tells a different story — debuts entering, post-peak models
    falling out, the Chinese-brand wave landing in 2024-2025, etc.
    """
    path = DATA_DIR / "model_by_month.csv"
    if not path.exists():
        print("  Skip: model_race (no data)")
        return

    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: model_race (empty data)")
        return

    # groupby (not set_index) so any future duplicate (model, year, month) —
    # e.g. one model under two brand spellings in a month — collapses to a
    # scalar instead of making lookup.get() return a Series and crashing the
    # delta sort. Today the key is unique; this keeps it that way defensively.
    lookup = df.groupby(["model", "year", "month"])["count"].sum()
    all_models = [m for m in df["model"].unique() if m not in MODEL_ARTIFACTS]

    months_present = sorted({(int(y), int(m)) for y, m in df[["year", "month"]].values})
    # Each frame's YoY delta needs 24 months of preceding data (12 current +
    # 12 prior). MODEL_RACE_START_YEAR focuses on the meaningful era; data
    # goes back to 2016 so prior windows are populated.
    target_months = [(y, m) for (y, m) in months_present if y >= MODEL_RACE_START_YEAR]

    # First pass: per-frame climbers/fallers + global max for fixed x-axis
    # across all frames (AGENTS.md GIF rule — locked axes prevent jumping).
    TOP_N = 12
    all_frame_data = []
    global_max = 0
    for (y, m) in target_months:
        cur_months = trailing_months(y, m, 12)
        py, pm = cur_months[-1]
        pm -= 1
        if pm == 0:
            pm = 12
            py -= 1
        prior_months = trailing_months(py, pm, 12)

        deltas = []
        for model in all_models:
            cur_sum = sum(lookup.get((model, ty, tm), 0) for ty, tm in cur_months)
            prior_sum = sum(lookup.get((model, ty, tm), 0) for ty, tm in prior_months)
            deltas.append((model, cur_sum, prior_sum, cur_sum - prior_sum))

        # Filter by sign before taking top-N. Without this, frames where
        # fewer than TOP_N models actually decline would render weak gains
        # as "fallers" with a "−" label — fabricating losses from positive
        # deltas. Same for climbers if all-decline scenario ever arises.
        climber_pool = sorted([r for r in deltas if r[3] > 0], key=lambda r: -r[3])
        faller_pool = sorted([r for r in deltas if r[3] < 0], key=lambda r: r[3])
        climbers = climber_pool[:TOP_N]
        fallers = faller_pool[:TOP_N]

        if climbers and fallers:
            frame_max = max(climbers[0][3], abs(fallers[0][3]))
            global_max = max(global_max, frame_max)
            all_frame_data.append(((y, m), climbers, fallers))

    if not all_frame_data:
        print("  Skip: model_race (no frames)")
        return

    CLIMB_COLOR = "#4ade80"
    FALL_COLOR = "#fb7185"
    PAD = 1.35
    xmax = global_max * PAD

    def render(ax, rows, color, sign):
        rows = list(reversed(rows))
        values = [abs(r[3]) for r in rows]
        # Bar height 0.7 matches ev_race / brand_race convention.
        ax.barh(range(len(rows)), values, color=color, height=0.7, edgecolor="none")
        for j, (_model, cur, prior, delta) in enumerate(rows):
            v = abs(delta)
            # Pack the bold delta and the muted (prior → current) parenthetical
            # side by side via HPacker so the gap between them is constant
            # regardless of how wide the delta number is. Two free-floating
            # ax.text calls at fixed offsets made the spacing jump (long
            # deltas collided with the parenthetical, short ones left a gap).
            delta_area = TextArea(f"{sign}{v:,}",
                                  textprops=dict(color=TEXT, fontsize=10, fontweight="bold"))
            paren_area = TextArea(f"({prior:,} → {cur:,})",
                                  textprops=dict(color=SUBTLE, fontsize=8.5))
            packed = HPacker(children=[delta_area, paren_area], align="baseline", pad=0, sep=6)
            ab = AnnotationBbox(
                packed, (v, j), xybox=(v + global_max * 0.012, j),
                xycoords="data", boxcoords="data",
                box_alignment=(0, 0.5), frameon=False, pad=0,
            )
            ax.add_artist(ab)
        ax.set_yticks(range(len(rows)))
        # Bold + fontsize 11 matches ev_race y-tick labels.
        ax.set_yticklabels([display_model(r[0]) for r in rows],
                           fontsize=11, color=TEXT, fontweight="bold")

    attribution = get_dark_attribution()
    images = []

    for i, ((y, m), climbers, fallers) in enumerate(all_frame_data):
        fig, (ax_climb, ax_fall) = plt.subplots(
            2, 1, figsize=(14, 9.5), facecolor=BG,
            gridspec_kw={"height_ratios": [1, 1]},
        )

        for ax in (ax_climb, ax_fall):
            ax.set_facecolor(BG)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_visible(False)
            ax.spines["bottom"].set_color(GRID_COLOR)
            ax.tick_params(axis="x", colors=SUBTLE, labelsize=9)
            ax.tick_params(axis="y", length=0)
            ax.grid(axis="x", alpha=0.18, color=GRID_COLOR)
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
            ax.set_xlim(0, xmax)

        render(ax_climb, climbers, CLIMB_COLOR, "+")
        render(ax_fall, fallers, FALL_COLOR, "−")

        ax_climb.set_title("▲  Largest Registration Gains", fontsize=12.5, fontweight="bold",
                           color=CLIMB_COLOR, pad=8, loc="left")
        ax_fall.set_title("▼  Largest Registration Losses", fontsize=12.5, fontweight="bold",
                          color=FALL_COLOR, pad=8, loc="left")
        ax_climb.tick_params(axis="x", labelbottom=False)

        # Title block matches ev_race / brand_race convention:
        # 1) big monospace date in #fbbf24 (fontsize 28)
        # 2) main title in bold white (fontsize 14)
        # 3) methodology subtitle in SUBTLE (fontsize 8)
        fig.text(0.50, 0.975, f"{MONTH_NAMES[m].upper()} {y}", ha="center", va="top",
                 fontsize=28, fontweight="bold", color="#fbbf24", fontfamily="monospace")
        fig.text(0.50, 0.925, "Fastest-Growing & Declining Car Models in Switzerland",
                 ha="center", va="top", fontsize=14, fontweight="bold", color=TEXT)
        fig.text(0.50, 0.900,
                 "Year-over-year change in trailing 12-month new Personenwagen (passenger car) registrations",
                 ha="center", va="top", fontsize=8, color=SUBTLE)

        fig.subplots_adjust(top=0.84, bottom=0.05, left=0.14, right=0.82, hspace=0.30)

        # Footer fontsize 11 matches AGENTS.md > Chart Styleguide > Layout
        # (GIF frames render at lower DPI; small text gets lost otherwise).
        fig.text(0.99, 0.005, attribution, ha="right", va="bottom",
                 fontsize=11, color="#64748b", style="italic")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=BG)
        plt.close(fig)
        buf.seek(0)
        images.append(Image.open(buf).copy())
        if (i + 1) % 24 == 0:
            print(f"    model_race: frame {i + 1}/{len(all_frame_data)}")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "model_race.gif"
    durations = [300] * len(images)
    durations[-1] = 3000
    images[0].save(out, save_all=True, append_images=images[1:],
                   duration=durations, loop=0, optimize=True)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"  Saved: model_race.gif ({size_mb:.1f} MB)")


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


def _months_between(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Signed count of calendar months from a to b ((2020,1)->(2020,3) == 2)."""
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def china_bev_share_series(df: pd.DataFrame, mappings: dict, start=(2019, 1)) -> pd.DataFrame:
    """Trailing-12-month ownership-bloc shares of new BEV registrations.

    Input: brand_bev_by_month (year, month, brand, bev_count). Returns a
    DataFrame [year, month, owned, branded, tesla] where each column is the
    T12M bloc registrations divided by T12M total BEV registrations (fractions
    in [0, 1]). China-branded ⊆ China-owned by construction, so branded ≤ owned
    at every point. Months before `start`, and any month whose trailing window
    has zero BEV registrations, are omitted.
    """
    cols = ["year", "month", "owned", "branded", "tesla"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    brands = df["brand"].unique()
    owned = {b for b in brands if is_china_owned(b, mappings)}
    branded = {b for b in brands if is_china_branded(b, mappings)}

    # Per-month bloc sums: (year, month) -> [total, owned, branded, tesla].
    monthly: dict[tuple[int, int], list[int]] = {}
    for row in df.itertuples(index=False):
        y, m, b, c = int(row.year), int(row.month), row.brand, int(row.bev_count)
        rec = monthly.setdefault((y, m), [0, 0, 0, 0])
        rec[0] += c
        if b in owned:
            rec[1] += c
        if b in branded:
            rec[2] += c
        if b == "TESLA":
            rec[3] += c

    latest = max(monthly)
    rows = []
    y, m = start
    while (y, m) <= latest:
        win = trailing_months(y, m, 12)
        tot = sum(monthly.get(k, (0, 0, 0, 0))[0] for k in win)
        if tot > 0:
            ow = sum(monthly.get(k, (0, 0, 0, 0))[1] for k in win)
            br = sum(monthly.get(k, (0, 0, 0, 0))[2] for k in win)
            te = sum(monthly.get(k, (0, 0, 0, 0))[3] for k in win)
            rows.append({"year": y, "month": m, "owned": ow / tot,
                         "branded": br / tot, "tesla": te / tot})
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return pd.DataFrame(rows, columns=cols)


def chart_china_bev_share():
    """Chinese share of Swiss BEV registrations — level (top) + momentum (bottom).

    Top: T12M share of BEV registrations for the China-owned and China-branded
    blocs, with Tesla as a scale reference; the gap between the two China lines
    is the story (Chinese-owned volume wearing European badges). Bottom: YoY
    change (percentage points) of the China-owned share — d/dt of market share
    in the house YoY vocabulary.
    """
    from datetime import date, timedelta

    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: china_bev_share (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: china_bev_share (empty data)")
        return

    series = china_bev_share_series(df, load_mappings(), start=(2019, 1))
    if series.empty:
        print("  Skip: china_bev_share (no share data)")
        return

    series = series.sort_values(["year", "month"]).reset_index(drop=True)
    xs = [date(int(r.year), int(r.month), 1) for r in series.itertuples(index=False)]
    owned = series["owned"].to_numpy() * 100
    branded = series["branded"].to_numpy() * 100
    tesla = series["tesla"].to_numpy() * 100

    # YoY momentum (pp): owned share now minus owned share 12 months earlier.
    yoy = np.full(len(owned), np.nan)
    if len(owned) > 12:
        yoy[12:] = owned[12:] - owned[:-12]

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(12, 8), facecolor=BG, sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    # --- Top: level ---
    ax_top.set_facecolor(BG)
    ax_top.fill_between(xs, branded, owned, color=BLOC_COLORS["china_owned"],
                        alpha=0.12, zorder=1)
    ax_top.plot(xs, tesla, color=BLOC_COLORS["tesla_ref"], linewidth=1.3,
                alpha=0.85, zorder=2)
    ax_top.plot(xs, branded, color=BLOC_COLORS["china_branded"], linewidth=2.2, zorder=3)
    ax_top.plot(xs, owned, color=BLOC_COLORS["china_owned"], linewidth=3.2, zorder=4)

    label_x = xs[-1] + timedelta(days=25)
    for value, text, color in [
        (owned[-1], f"China-owned {owned[-1]:.1f}%", BLOC_COLORS["china_owned"]),
        (tesla[-1], f"Tesla {tesla[-1]:.1f}%", BLOC_COLORS["tesla_ref"]),
        (branded[-1], f"China-branded {branded[-1]:.1f}%", BLOC_COLORS["china_branded"]),
    ]:
        ax_top.annotate(text, (label_x, value), va="center", ha="left",
                        fontsize=9, fontweight="bold", color=color)

    top_max = float(np.nanmax([owned.max(), tesla.max()]))
    ax_top.set_ylim(0, top_max * 1.18)
    ax_top.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    for spine in ("top", "right"):
        ax_top.spines[spine].set_visible(False)
    ax_top.spines["left"].set_color(GRID_COLOR)
    ax_top.spines["bottom"].set_color(GRID_COLOR)
    ax_top.tick_params(colors=TEXT, labelsize=10)
    ax_top.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")

    # --- Bottom: momentum (YoY pp change of owned share) ---
    ax_bot.set_facecolor(BG)
    bar_vals = np.nan_to_num(yoy)
    bar_colors = [BLOC_COLORS["china_owned"] if v >= 0 else BLOC_COLORS["rest_ref"]
                  for v in bar_vals]
    ax_bot.bar(xs, bar_vals, width=22, color=bar_colors)
    ax_bot.axhline(0, color=GRID_COLOR, linewidth=0.8)
    ax_bot.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:+.0f}"))
    ax_bot.set_title("Year-over-Year Change of China-Owned Share (pp)",
                     loc="left", fontsize=9.5, color=SUBTLE, pad=4)
    for spine in ("top", "right"):
        ax_bot.spines[spine].set_visible(False)
    ax_bot.spines["left"].set_color(GRID_COLOR)
    ax_bot.spines["bottom"].set_color(GRID_COLOR)
    ax_bot.tick_params(colors=TEXT, labelsize=10)
    ax_bot.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")

    # Every year on the shared X-axis; pad the right for the inline labels.
    years = sorted({d.year for d in xs})
    ax_bot.set_xticks([date(y, 1, 1) for y in years])
    ax_bot.set_xticklabels([str(y) for y in years])
    ax_bot.set_xlim(xs[0], xs[-1] + timedelta(days=430))

    # Title block (per styleguide: metric, not vibe).
    fig.text(0.5, 0.975, "Chinese-Owned Brands — Share of New BEV Registrations in Switzerland",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.938,
             "Trailing 12-month share of fully electric (BEV) Personenwagen (passenger cars) registrations | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.911,
             "China-owned = ultimate controlling shareholder in China (incl. MG/SAIC, Volvo & Polestar & Lotus/Geely, Smart/Geely-Mercedes JV) · "
             "China-branded = Chinese brand heritage (BYD, XPeng, Zeekr, NIO, …)",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")
    fig.text(0.5, 0.892,
             "Classified by ownership, not production location · Recent months subject to Nachmeldungen (late reports)",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.86, bottom=0.10, left=0.07, right=0.86, hspace=0.28)
    ax_bot.text(1.0, -0.32, get_dark_attribution(), transform=ax_bot.transAxes,
                ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "china_bev_share")


def _brand_monthly_counts(df: pd.DataFrame) -> dict:
    """brand -> {(year, month): bev_count} from brand_bev_by_month rows."""
    out: dict[str, dict[tuple[int, int], int]] = {}
    for row in df.itertuples(index=False):
        out.setdefault(row.brand, {})[(int(row.year), int(row.month))] = int(row.bev_count)
    return out


def _cumulative_since_entry(counts: dict, entry: tuple[int, int],
                            latest: tuple[int, int]) -> list[int]:
    """Cumulative BEV registrations by months-since-entry (index 0 == entry)."""
    n = _months_between(entry, latest) + 1
    cum, running = [], 0
    y, m = entry
    for _ in range(n):
        running += counts.get((y, m), 0)
        cum.append(running)
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return cum


def chart_china_entry_ramp():
    """Entry-aligned cumulative ramp curves for Chinese BEV brands.

    Each China-branded brand's cumulative BEV registrations are re-indexed to
    months-since-market-entry (month 0 = first sustained registrations), on a
    log axis, so ramp *speed* is comparable across cohorts. Tesla is the
    benchmark; MG is shown as the Chinese-owned / European-badge reference.
    """
    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: china_entry_ramp (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: china_entry_ramp (empty data)")
        return

    mappings = load_mappings()
    counts_by_brand = _brand_monthly_counts(df)
    latest = (int(df["year"].max()),
              int(df[df["year"] == df["year"].max()]["month"].max()))

    # Per-brand entry + cumulative total.
    meta = {}
    for brand, counts in counts_by_brand.items():
        chrono = sorted((y, m, c) for (y, m), c in counts.items())
        entry = detect_entry_month(chrono)
        meta[brand] = {"entry": entry, "total": sum(counts.values())}

    INCLUDE_MIN_CUM = 100
    included = sorted(
        [b for b in counts_by_brand
         if is_china_branded(b, mappings) and meta[b]["entry"]
         and meta[b]["total"] >= INCLUDE_MIN_CUM],
        key=lambda b: -meta[b]["total"],
    )
    if not included:
        print("  Skip: china_entry_ramp (no qualifying brands)")
        return

    # Fixed, comparable X-limit. The window spans the longest included ramp
    # (+3 months headroom), capped at 48. Younger brands simply end early — the
    # spec's "youngest included brand" wording is read as this span, since only
    # a limit that exceeds a brand's age lets it "end early" as the spec states.
    max_age = max(_months_between(meta[b]["entry"], latest) for b in included)
    x_limit = min(48, max_age + 3)

    # Micro-entrant collective line: ≥3 China-branded brands below the inclusion
    # threshold that nonetheless entered the market, aggregated into one series.
    micro = [b for b in counts_by_brand
             if is_china_branded(b, mappings) and meta[b]["entry"]
             and meta[b]["total"] < INCLUDE_MIN_CUM]

    fig, ax = plt.subplots(figsize=(12, 7.5), facecolor=BG)
    ax.set_facecolor(BG)

    # (brand-or-None, entry, counts, color, dashed, display_label)
    lines = []
    for i, brand in enumerate(included):
        color = BRAND_COLORS.get(brand, FALLBACK_COLORS[i % len(FALLBACK_COLORS)])
        lines.append((brand, meta[brand]["entry"], counts_by_brand[brand], color, False,
                      display_brand(brand)))

    # References: Tesla (benchmark) and MG (Chinese-owned, European badge).
    if meta.get("TESLA", {}).get("entry"):
        lines.append(("TESLA", meta["TESLA"]["entry"], counts_by_brand["TESLA"],
                      BLOC_COLORS["tesla_ref"], True, "Tesla (reference)"))
    if "MG" in counts_by_brand and meta["MG"]["entry"] and meta["MG"]["total"] >= INCLUDE_MIN_CUM:
        lines.append(("MG", meta["MG"]["entry"], counts_by_brand["MG"],
                      BLOC_COLORS["mg_ref"], True, "MG (SAIC — reference)"))

    if len(micro) >= 3:
        agg_counts: dict[tuple[int, int], int] = {}
        for b in micro:
            for k, c in counts_by_brand[b].items():
                agg_counts[k] = agg_counts.get(k, 0) + c
        agg_entry = detect_entry_month(sorted((y, m, c) for (y, m), c in agg_counts.items()))
        if agg_entry:
            lines.append((None, agg_entry, agg_counts, BLOC_COLORS["rest_ref"], True,
                          f"Other Chinese entrants (×{len(micro)})"))

    # Plot + collect end-of-line label anchors.
    labels = []
    y_top = 10
    for brand, entry, counts, color, dashed, disp in lines:
        cum = _cumulative_since_entry(counts, entry, latest)[:x_limit + 1]
        if not cum:  # pragma: no cover — entry ≤ latest guarantees ≥1 point
            continue
        x = list(range(len(cum)))
        ax.plot(x, cum, color=color, linewidth=2.4 if not dashed else 1.8,
                linestyle="--" if dashed else "-", alpha=0.95 if not dashed else 0.85,
                zorder=3 if not dashed else 2)
        y_top = max(y_top, cum[-1])
        labels.append({"x": x[-1], "y": max(cum[-1], 1), "color": color,
                       "text": f"{disp} · {cum[-1]:,} ({MONTH_NAMES[entry[1]]} {entry[0]})"})

    # De-clutter labels: greedily push a label up (in log space) only when it
    # would collide with an already-placed label that ALSO overlaps it
    # horizontally. Labels far apart in x keep their true height (staying next
    # to their line end); only genuine same-region clusters get spread.
    right_xlim = x_limit + max(6, x_limit * 0.55)
    x_gap = 0.20 * right_xlim
    min_log_gap = 0.16
    labels.sort(key=lambda l: l["y"])
    placed = []
    for lab in labels:
        ly = np.log10(max(lab["y"], 1))
        for p in placed:
            if abs(lab["x"] - p["x"]) < x_gap and ly < p["ly_log"] + min_log_gap:
                ly = p["ly_log"] + min_log_gap
        lab["ly_log"] = ly
        lab["ly"] = 10 ** ly
        placed.append(lab)
    for lab in labels:
        ax.annotate(lab["text"], (lab["x"], lab["ly"]),
                    textcoords="offset points", xytext=(7, 0),
                    va="center", ha="left", fontsize=8.5, fontweight="bold",
                    color=lab["color"])

    ax.set_yscale("log")
    ax.set_ylim(ENTRY_MIN_MONTHLY, y_top * 3.2)
    ax.set_yticks([10, 100, 1000, 10000])
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.set_xlim(-0.5, x_limit + max(6, x_limit * 0.55))
    ax.set_xlabel("Months since market entry", fontsize=11, color=TEXT)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT, labelsize=10)
    ax.grid(axis="y", which="major", alpha=0.2, color=GRID_COLOR, linestyle="--")

    fig.text(0.5, 0.975, "Chinese BEV Brands — Cumulative Swiss Registrations Since Market Entry",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.936,
             "Fully electric (BEV) Personenwagen (passenger cars), aligned by months since first sustained registrations | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.906,
             f"Market entry = first month with ≥{ENTRY_MIN_MONTHLY} BEV registrations · "
             "Tesla shown as reference · Log scale · Brands with ≥100 cumulative registrations",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.85, bottom=0.10, left=0.07, right=0.97)
    ax.text(1.0, -0.13, get_dark_attribution(), transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "china_entry_ramp")


def _t12m_matrix(records, start=(2019, 1)):
    """Trailing-12-month sums per key over time.

    ``records`` is an iterable of ``(year, month, key, count)``. Returns
    ``(months, keys, series)`` where ``months`` is the list of ``(year, month)``
    from ``start`` to the latest month with data, ``keys`` is the sorted key
    set, and ``series[key]`` is a list of T12M sums aligned to ``months``.
    Reused by the bloc-share, group, and powertrain charts.
    """
    monthly: dict[tuple[int, int], dict] = {}
    keys = set()
    for y, m, k, c in records:
        monthly.setdefault((int(y), int(m)), {})
        monthly[(int(y), int(m))][k] = monthly[(int(y), int(m))].get(k, 0) + int(c)
        keys.add(k)
    if not monthly:
        return [], [], {}
    latest = max(monthly)
    months = []
    y, m = start
    while (y, m) <= latest:
        months.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1
    keys = sorted(keys)
    series = {k: [] for k in keys}
    for (ty, tm) in months:
        acc = {k: 0 for k in keys}
        for wk in trailing_months(ty, tm, 12):
            for k, c in monthly.get(wk, {}).items():
                acc[k] += c
        for k in keys:
            series[k].append(acc[k])
    return months, keys, series


def _declutter(values, min_gap):
    """Greedy 1-D spread: nudge sorted-ascending anchors up so consecutive ones
    are >= min_gap apart. Returns new positions aligned to the input order."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = list(values)
    prev = None
    for i in order:
        v = values[i]
        if prev is not None and v < prev + min_gap:
            v = prev + min_gap
        out[i] = v
        prev = v
    return out


def chart_bev_bloc_share():
    """100% stacked share of BEV registrations by manufacturer bloc — the
    displacement view: whose share the growing China-owned wedge comes out of.
    """
    from datetime import date, timedelta

    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: bev_bloc_share (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: bev_bloc_share (empty data)")
        return

    mappings = load_mappings()
    records = [(r.year, r.month, bloc(r.brand, mappings), int(r.bev_count))
               for r in df.itertuples(index=False)]
    months, keys, series = _t12m_matrix(records, start=(2019, 1))
    if not months:
        print("  Skip: bev_bloc_share (no data)")
        return

    totals = [sum(series[k][i] for k in keys) for i in range(len(months))]
    first = next((i for i, t in enumerate(totals) if t > 0), None)
    if first is None:
        print("  Skip: bev_bloc_share (no share data)")
        return
    months, totals = months[first:], totals[first:]
    xs = [date(y, m, 1) for (y, m) in months]
    order = [b for b in BLOC_ORDER if b in keys]
    shares = {b: np.array([series[b][first + i] / totals[i] * 100
                           for i in range(len(months))]) for b in order}

    fig, ax = plt.subplots(figsize=(12, 7.5), facecolor=BG)
    ax.set_facecolor(BG)

    baseline = np.zeros(len(months))
    label_anchors, label_meta = [], []
    for b in order:
        top = baseline + shares[b]
        alpha = 1.0 if b == BLOC_CHINA_OWNED else 0.6
        ax.fill_between(xs, baseline, top, color=DISPLACEMENT_COLORS[b],
                        alpha=alpha, linewidth=0.4, edgecolor=BG)
        label_anchors.append((baseline[-1] + top[-1]) / 2)
        label_meta.append((b, shares[b][-1], DISPLACEMENT_COLORS[b]))
        baseline = top

    ys = _declutter(label_anchors, min_gap=4.2)
    label_x = xs[-1] + timedelta(days=20)
    for y_pos, (b, share, color) in sorted(zip(ys, label_meta), key=lambda t: t[0]):
        weight = "bold" if b == BLOC_CHINA_OWNED else "normal"
        ax.annotate(f"{b} {share:.1f}%", (label_x, y_pos), va="center", ha="left",
                    fontsize=9, fontweight=weight, color=color)

    ax.set_ylim(0, 100)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    years = sorted({d.year for d in xs})
    ax.set_xticks([date(y, 1, 1) for y in years])
    ax.set_xticklabels([str(y) for y in years])
    ax.set_xlim(xs[0], xs[-1] + timedelta(days=430))
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT, labelsize=10)

    fig.text(0.5, 0.975, "Swiss BEV Market — Share of New Registrations by Manufacturer Bloc",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.936,
             "Trailing 12-month share of fully electric (BEV) Personenwagen (passenger cars) registrations | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.906,
             "Blocs by ultimate ownership: China-owned incl. Volvo, Polestar, Smart, MG · "
             "European legacy excl. Chinese-owned brands · Recent months subject to Nachmeldungen (late reports)",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.85, bottom=0.10, left=0.07, right=0.80)
    ax.text(1.0, -0.10, get_dark_attribution(), transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "bev_bloc_share")


def china_owned_lq_by_canton(df: pd.DataFrame, mappings: dict, valid_cantons,
                             months: int = 24):
    """China-owned Location Quotient per canton over a trailing window.

    Returns ``(lq, bev_total, national_share)`` where ``lq[canton]`` is the
    canton's China-owned share of BEV registrations divided by the national
    share (1.0 = on par), and ``bev_total[canton]`` is the canton's BEV count in
    the window (for the small-sample flag). Uses a wider 24-month window than the
    house T12M default because small cantons have thin annual BEV counts.
    """
    latest_year = int(df["year"].max())
    latest_month = int(df[df["year"] == latest_year]["month"].max())
    win = set()
    y, m = latest_year, latest_month
    for _ in range(months):
        win.add((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1

    owned_brands = {b for b in df["brand"].unique() if is_china_owned(b, mappings)}
    canton_total: dict[str, int] = {}
    canton_owned: dict[str, int] = {}
    for r in df.itertuples(index=False):
        if (int(r.year), int(r.month)) not in win or r.canton not in valid_cantons:
            continue
        c = int(r.bev_count)
        canton_total[r.canton] = canton_total.get(r.canton, 0) + c
        if r.brand in owned_brands:
            canton_owned[r.canton] = canton_owned.get(r.canton, 0) + c

    nat_total = sum(canton_total.values())
    nat_owned = sum(canton_owned.values())
    national_share = nat_owned / nat_total if nat_total else 0
    lq = {}
    for c, tot in canton_total.items():
        share = canton_owned.get(c, 0) / tot if tot else 0
        lq[c] = share / national_share if national_share else 0
    return lq, canton_total, national_share


def chart_china_bev_lq():
    """Where Chinese-owned BEVs over/under-index: canton Location Quotient as a
    choropleth (left) and a sorted bar ranking (right)."""
    bev_path = DATA_DIR / "brand_canton_bev.csv"
    if not bev_path.exists() or not GEOJSON_PATH.exists():
        print("  Skip: china_bev_lq (no data or geojson)")
        return

    import geopandas as gpd
    cantons_geo = gpd.read_file(GEOJSON_PATH)
    valid_cantons = set(cantons_geo["id"].tolist())
    df = pd.read_csv(bev_path)
    if df.empty:
        print("  Skip: china_bev_lq (empty data)")
        return

    lq, bev_total, national_share = china_owned_lq_by_canton(
        df, load_mappings(), valid_cantons, months=24)
    if not lq or national_share == 0:
        print("  Skip: china_bev_lq (insufficient data)")
        return

    SMALL_SAMPLE = 300
    lq_cmap = mcolors.LinearSegmentedColormap.from_list(
        "china_lq", ["#1e3a5f", "#3b6ea5", "#0d1117", "#8b2f2f", "#ef4444"], N=256)
    vmax = max(2.0, max(lq.values()))
    norm = mcolors.TwoSlopeNorm(vmin=0, vcenter=1.0, vmax=vmax)

    fig, (ax_map, ax_bar) = plt.subplots(
        1, 2, figsize=(16, 9), facecolor=BG,
        gridspec_kw={"width_ratios": [1.1, 1]})

    # --- Left: choropleth ---
    ax_map.set_facecolor(BG)
    cdf = pd.DataFrame([{"canton": c, "lq": v} for c, v in lq.items()])
    merged = cantons_geo.merge(cdf, left_on="id", right_on="canton", how="left")
    merged["lq"] = merged["lq"].fillna(0)
    merged.plot(column="lq", ax=ax_map, cmap=lq_cmap, norm=norm,
                edgecolor="#1e293b", linewidth=0.8, legend=False)
    for _, row in merged.iterrows():
        c = row["id"]
        centroid = row.geometry.centroid
        val = lq.get(c, 0)
        flag = "*" if bev_total.get(c, 0) < SMALL_SAMPLE else ""
        txt_color = "black" if val > 1.6 else "#e2e8f0"
        ax_map.annotate(f"{c}{flag}\n{val:.2f}", (centroid.x, centroid.y),
                        ha="center", va="center", fontsize=7, fontweight="bold",
                        color=txt_color)
    ax_map.set_axis_off()

    sm = plt.cm.ScalarMappable(cmap=lq_cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax_map, shrink=0.55, pad=0.02)
    cbar.set_label("Location Quotient (1.0 = national average)", fontsize=8, color=TEXT)
    cbar.ax.yaxis.set_tick_params(color=TEXT)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color=TEXT, fontsize=7)

    # --- Right: sorted bar ranking ---
    ax_bar.set_facecolor(BG)
    ranked = sorted(lq.items(), key=lambda kv: kv[1])
    labels = [c for c, _ in ranked]
    vals = [v for _, v in ranked]
    colors = [lq_cmap(norm(v)) for v in vals]
    small = [bev_total.get(c, 0) < SMALL_SAMPLE for c in labels]
    bars = ax_bar.barh(range(len(labels)), vals, color=colors, height=0.72,
                       edgecolor=GRID_COLOR, linewidth=0.4)
    for bar, is_small in zip(bars, small):
        if is_small:
            bar.set_hatch("///")
    ax_bar.axvline(1.0, color="#94a3b8", linewidth=1.0, linestyle="--")
    for i, (c, v) in enumerate(ranked):
        flag = "*" if bev_total.get(c, 0) < SMALL_SAMPLE else ""
        ax_bar.text(v + vmax * 0.01, i, f"{v:.2f}{flag}", va="center", ha="left",
                    fontsize=8, color=TEXT, fontweight="bold")
    ax_bar.set_yticks(range(len(labels)))
    ax_bar.set_yticklabels(labels, fontsize=9, color=TEXT)
    ax_bar.set_xlim(0, vmax * 1.12)
    ax_bar.set_ylim(-0.6, len(labels) - 0.4)
    ax_bar.tick_params(colors=TEXT, labelsize=9)
    for spine in ("top", "right", "left"):
        ax_bar.spines[spine].set_visible(False)
    ax_bar.spines["bottom"].set_color(GRID_COLOR)
    ax_bar.text(1.0, 1.005, "* < 300 BEV in window (small sample)",
                transform=ax_bar.transAxes, ha="right", va="bottom",
                fontsize=7.5, color=SUBTLE)

    fig.text(0.5, 0.975, "Chinese-Owned BEV Brands — Location Quotient by Canton",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.94,
             "Over/under-representation vs national average, trailing 24-month BEV Personenwagen (passenger cars) registrations | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.912,
             "LQ > 1.0 = China-owned brands overrepresented · 24-month window for small-canton stability · China-owned incl. Volvo, Polestar, Smart, MG",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.88, bottom=0.05, left=0.02, right=0.97, wspace=0.05)
    ax_bar.text(1.0, -0.06, get_dark_attribution(), transform=ax_bar.transAxes,
                ha="right", va="top", fontsize=8, color="#64748b", style="italic")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "china_bev_lq.png"
    fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print(f"  Saved: china_bev_lq.png ({out.stat().st_size / 1024:.0f} KB)")


def chart_china_groups():
    """Corporate-group decomposition of the China-owned BEV wedge — the point
    being that Geely (Volvo + Polestar + Smart + Zeekr + Lotus) outweighs BYD."""
    from datetime import date, timedelta

    path = DATA_DIR / "brand_bev_by_month.csv"
    if not path.exists():
        print("  Skip: china_groups (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: china_groups (empty data)")
        return

    mappings = load_mappings()
    group_map = mappings.get("brand_group", {})

    def group_of(brand):
        b = str(brand).strip().upper()
        if b in CHINA_GROUP_OVERRIDES:
            return CHINA_GROUP_OVERRIDES[b]
        return safe_map(brand, group_map, default="Other")

    records = [(r.year, r.month, group_of(r.brand), int(r.bev_count))
               for r in df.itertuples(index=False)
               if is_china_owned(r.brand, mappings)]
    if not records:
        print("  Skip: china_groups (no china-owned data)")
        return
    months, keys, series = _t12m_matrix(records, start=(2019, 1))
    if not months:
        print("  Skip: china_groups (no data)")
        return

    FLOOR = 100
    final = {k: series[k][-1] for k in keys}
    big = [k for k in keys if final[k] >= FLOOR]
    small = [k for k in keys if final[k] < FLOOR]
    # Largest current volume at the bottom of the stack.
    big.sort(key=lambda k: final[k], reverse=True)
    stack = list(big)
    other = None
    if small:
        other = np.sum([np.array(series[k]) for k in small], axis=0)
        stack.append("Other Chinese groups")

    totals = np.sum([np.array(series[k]) for k in keys], axis=0)
    first = next((i for i, t in enumerate(totals) if t > 0), 0)
    xs = [date(y, m, 1) for (y, m) in months[first:]]

    fig, ax = plt.subplots(figsize=(12, 7.5), facecolor=BG)
    ax.set_facecolor(BG)

    baseline = np.zeros(len(xs))
    anchors, meta = [], []
    for k in stack:
        vals = (other if k == "Other Chinese groups" else np.array(series[k]))[first:]
        color = (CHINA_GROUP_OTHER_COLOR if k == "Other Chinese groups"
                 else CHINA_GROUP_COLORS.get(k, FALLBACK_COLORS[len(meta) % len(FALLBACK_COLORS)]))
        top = baseline + vals
        ax.fill_between(xs, baseline, top, color=color, alpha=0.9,
                        linewidth=0.4, edgecolor=BG)
        anchors.append((baseline[-1] + top[-1]) / 2)
        meta.append((k, vals[-1], color))
        baseline = top

    y_max = float(baseline[-1]) if len(baseline) else 1
    ys = _declutter(anchors, min_gap=y_max * 0.045)
    label_x = xs[-1] + timedelta(days=20)
    for y_pos, (k, val, color) in sorted(zip(ys, meta), key=lambda t: t[0]):
        ax.annotate(f"{k} · {int(val):,}", (label_x, y_pos), va="center",
                    ha="left", fontsize=9, fontweight="bold", color=color)

    ax.set_ylim(0, y_max * 1.12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    years = sorted({d.year for d in xs})
    ax.set_xticks([date(y, 1, 1) for y in years])
    ax.set_xticklabels([str(y) for y in years])
    ax.set_xlim(xs[0], xs[-1] + timedelta(days=520))
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.tick_params(colors=TEXT, labelsize=10)
    ax.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")
    ax.set_ylabel("Trailing 12-month BEV registrations", fontsize=11, color=TEXT)

    fig.text(0.5, 0.975, "Chinese-Owned Corporate Groups — Swiss BEV Registrations",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.936,
             "Trailing 12-month fully electric (BEV) Personenwagen (passenger cars) registrations by ultimate parent group | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.906,
             "Geely incl. Volvo, Polestar, Smart (JV), Zeekr, Lotus, Lynk & Co · SAIC incl. MG, Maxus · Smart JV counted fully under Geely",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.85, bottom=0.10, left=0.09, right=0.80)
    ax.text(1.0, -0.10, get_dark_attribution(), transform=ax.transAxes,
            ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "china_groups")


# Collapse of the full decision-table fuel categories into the 5 buckets used by
# chart_china_powertrain_mix. EREV ("Elektrisch mit RE") is already PHEV upstream.
POWERTRAIN_COLLAPSE = {
    "BEV": "BEV",
    "PHEV": "PHEV", "Diesel PHEV": "PHEV",
    "Hybrid (Petrol)": "HEV", "Hybrid (Diesel)": "HEV",
    "Petrol": "ICE", "Diesel": "ICE",
}
POWERTRAIN_MIX_ORDER = ["BEV", "PHEV", "HEV", "ICE", "Other"]


def chart_china_powertrain_mix():
    """China-branded registrations by powertrain over time (top) plus the PHEV
    share as the 'second wave' indicator (bottom)."""
    from datetime import date, timedelta

    path = DATA_DIR / "brand_powertrain_by_month.csv"
    if not path.exists():
        print("  Skip: china_powertrain_mix (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: china_powertrain_mix (empty data)")
        return

    mappings = load_mappings()
    records = [(r.year, r.month,
                POWERTRAIN_COLLAPSE.get(r.powertrain, "Other"), int(r.count))
               for r in df.itertuples(index=False)
               if is_china_branded(r.brand, mappings)]
    if not records:
        print("  Skip: china_powertrain_mix (no china-branded data)")
        return
    months, keys, series = _t12m_matrix(records, start=(2019, 1))
    if not months:
        print("  Skip: china_powertrain_mix (no data)")
        return

    order = [c for c in POWERTRAIN_MIX_ORDER if c in keys]
    totals = np.sum([np.array(series[k]) for k in keys], axis=0)
    first = next((i for i, t in enumerate(totals) if t > 0), 0)
    xs = [date(y, m, 1) for (y, m) in months[first:]]
    totals = totals[first:]
    phev = np.array(series.get("PHEV", [0] * len(months)))[first:]
    phev_share = np.array([phev[i] / totals[i] * 100 if totals[i] else 0
                           for i in range(len(xs))])

    fig, (ax_top, ax_bot) = plt.subplots(
        2, 1, figsize=(12, 8), facecolor=BG, sharex=True,
        gridspec_kw={"height_ratios": [3, 1]})

    ax_top.set_facecolor(BG)
    baseline = np.zeros(len(xs))
    anchors, meta = [], []
    for c in order:
        vals = np.array(series[c])[first:]
        top = baseline + vals
        ax_top.fill_between(xs, baseline, top, color=POWERTRAIN_MIX_COLORS[c],
                            alpha=0.9, linewidth=0.4, edgecolor=BG)
        anchors.append((baseline[-1] + top[-1]) / 2)
        meta.append((c, vals[-1], POWERTRAIN_MIX_COLORS[c]))
        baseline = top

    y_max = float(baseline[-1]) if len(baseline) else 1
    ys = _declutter(anchors, min_gap=y_max * 0.05)
    label_x = xs[-1] + timedelta(days=18)
    for y_pos, (c, val, color) in sorted(zip(ys, meta), key=lambda t: t[0]):
        ax_top.annotate(f"{c} · {int(val):,}", (label_x, y_pos), va="center",
                        ha="left", fontsize=9, fontweight="bold", color=color)

    ax_top.set_ylim(0, y_max * 1.12)
    ax_top.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    for spine in ("top", "right"):
        ax_top.spines[spine].set_visible(False)
    ax_top.spines["left"].set_color(GRID_COLOR)
    ax_top.spines["bottom"].set_color(GRID_COLOR)
    ax_top.tick_params(colors=TEXT, labelsize=10)
    ax_top.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")
    ax_top.set_ylabel("Trailing 12-month registrations", fontsize=10, color=TEXT)

    ax_bot.set_facecolor(BG)
    ax_bot.plot(xs, phev_share, color=POWERTRAIN_MIX_COLORS["PHEV"], linewidth=2.4)
    ax_bot.fill_between(xs, 0, phev_share, color=POWERTRAIN_MIX_COLORS["PHEV"], alpha=0.15)
    if len(xs):
        ax_bot.annotate(f"{phev_share[-1]:.0f}%", (xs[-1] + timedelta(days=18), phev_share[-1]),
                        va="center", ha="left", fontsize=9, fontweight="bold",
                        color=POWERTRAIN_MIX_COLORS["PHEV"])
    ax_bot.set_ylim(0, max(10, float(phev_share.max()) * 1.25) if len(phev_share) else 10)
    ax_bot.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax_bot.set_title("PHEV share of China-branded registrations", loc="left",
                     fontsize=9.5, color=SUBTLE, pad=4)
    for spine in ("top", "right"):
        ax_bot.spines[spine].set_visible(False)
    ax_bot.spines["left"].set_color(GRID_COLOR)
    ax_bot.spines["bottom"].set_color(GRID_COLOR)
    ax_bot.tick_params(colors=TEXT, labelsize=10)
    ax_bot.grid(axis="y", alpha=0.2, color=GRID_COLOR, linestyle="--")

    years = sorted({d.year for d in xs})
    ax_bot.set_xticks([date(y, 1, 1) for y in years])
    ax_bot.set_xticklabels([str(y) for y in years])
    ax_bot.set_xlim(xs[0], xs[-1] + timedelta(days=400))

    fig.text(0.5, 0.975, "Chinese Brands — Swiss Registrations by Powertrain",
             ha="center", va="top", fontsize=15.5, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.938,
             "Trailing 12-month Personenwagen (passenger cars) registrations of China-branded marques, all powertrains | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.909,
             "China-branded only (excl. Chinese-owned European marques like Volvo, MG) · Extended-range EVs (EREV) classified as PHEV · Pre-2022 PHEV/HEV split approximate (CO2 fallback)",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.86, bottom=0.09, left=0.08, right=0.83, hspace=0.22)
    ax_bot.text(1.0, -0.32, get_dark_attribution(), transform=ax_bot.transAxes,
                ha="right", va="top", fontsize=8, color="#64748b", style="italic")
    save_chart(fig, "china_powertrain_mix")


def chart_china_challengers():
    """Small multiples: each Chinese challenger model's T12M registrations vs the
    incumbent nameplate it targets, with the current ratio annotated."""
    from datetime import date, timedelta

    path = DATA_DIR / "model_by_month.csv"
    if not path.exists():
        print("  Skip: china_challengers (no data)")
        return
    df = pd.read_csv(path)
    if df.empty:
        print("  Skip: china_challengers (empty data)")
        return

    mappings = load_mappings()
    pairs = mappings.get("challenger_pairs", {}) or {}
    if not pairs:
        print("  Skip: china_challengers (no pairs configured)")
        return

    # Apply model_merges at read time so split spellings (MG 4 / MG MG4) collapse
    # even if the committed model_by_month.csv predates the merge entries.
    merges = {str(k).upper(): v for k, v in (mappings.get("model_merges") or {}).items()}
    df["model"] = df["model"].map(lambda k: merges.get(str(k).upper(), k))
    model_brand = (df.groupby("model")["brand"]
                   .agg(lambda s: s.mode().iloc[0]).to_dict())

    records = [(r.year, r.month, r.model, int(r.count))
               for r in df.itertuples(index=False)]
    months, _keys, series = _t12m_matrix(records, start=(2019, 1))
    if not months:
        print("  Skip: china_challengers (no data)")
        return
    xs = [date(y, m, 1) for (y, m) in months]

    # Keep only pairs whose challenger clears the floor and whose incumbent exists.
    panels = []
    for challenger, incumbent in pairs.items():
        if challenger not in series or incumbent not in series:
            continue
        if series[challenger][-1] < CHALLENGER_MIN_T12M:
            continue
        panels.append((challenger, incumbent))
    if not panels:
        print("  Skip: china_challengers (no pair clears the floor)")
        return

    n = len(panels)
    ncols = min(3, n)
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 3.6 * nrows),
                             facecolor=BG, squeeze=False)

    for idx, (challenger, incumbent) in enumerate(panels):
        ax = axes[idx // ncols][idx % ncols]
        ax.set_facecolor(BG)
        ch_vals = np.array(series[challenger])
        in_vals = np.array(series[incumbent])
        ch_brand = str(model_brand.get(challenger, "")).upper()
        ch_color = BRAND_COLORS.get(ch_brand, FALLBACK_COLORS[idx % len(FALLBACK_COLORS)])

        ax.plot(xs, in_vals, color=SUBTLE, linewidth=1.8, alpha=0.9)
        ax.plot(xs, ch_vals, color=ch_color, linewidth=2.6)

        ratio = ch_vals[-1] / in_vals[-1] if in_vals[-1] else 0
        ax.set_title(f"{display_model(challenger)}  vs  {display_model(incumbent)}",
                     loc="left", fontsize=10, fontweight="bold", color=TEXT, pad=6)
        ax.annotate(f"{display_model(challenger)} = {ratio:.2f}× {display_model(incumbent)}",
                    (0.03, 0.93), xycoords="axes fraction", ha="left", va="top",
                    fontsize=8.5, fontweight="bold", color=ch_color)

        # Value-only end labels (title + ratio identify the lines; colors match).
        y_hi = max(ch_vals.max(), in_vals.max()) * 1.18 or 1
        in_pos, ch_pos = _declutter([in_vals[-1], ch_vals[-1]], min_gap=y_hi * 0.09)
        lx = xs[-1] + timedelta(days=12)
        ax.annotate(f"{int(in_vals[-1]):,}", (lx, in_pos), va="center", ha="left",
                    fontsize=8, fontweight="bold", color=SUBTLE)
        ax.annotate(f"{int(ch_vals[-1]):,}", (lx, ch_pos), va="center", ha="left",
                    fontsize=8, fontweight="bold", color=ch_color)

        ax.set_ylim(0, y_hi)
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
        years = sorted({d.year for d in xs})
        ax.set_xticks([date(y, 1, 1) for y in years])
        ax.set_xticklabels([f"'{y % 100:02d}" for y in years], fontsize=8)
        ax.set_xlim(xs[0], xs[-1] + timedelta(days=330))
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color(GRID_COLOR)
        ax.spines["bottom"].set_color(GRID_COLOR)
        ax.tick_params(colors=TEXT, labelsize=8)
        ax.grid(axis="y", alpha=0.15, color=GRID_COLOR, linestyle="--")

    # Blank any unused grid cells.
    for idx in range(n, nrows * ncols):
        axes[idx // ncols][idx % ncols].set_visible(False)

    fig.text(0.5, 0.978, "Chinese Challenger Models vs Segment Incumbents — Swiss BEV Registrations",
             ha="center", va="top", fontsize=15, fontweight="bold", color=TEXT)
    fig.text(0.5, 0.947,
             "Trailing 12-month fully electric (BEV) Personenwagen (passenger cars) registrations per model | Source: ASTRA/IVZ Open Data",
             ha="center", va="top", fontsize=9, color=SUBTLE)
    fig.text(0.5, 0.923,
             "Pairings curated by market segment · Model keys per repo normalization · Challengers with <50 trailing-12-month registrations omitted",
             ha="center", va="top", fontsize=7.5, color="#94a3b8")

    fig.subplots_adjust(top=0.85, bottom=0.06, left=0.06, right=0.97,
                        hspace=0.48, wspace=0.30)
    fig.text(0.99, 0.01, get_dark_attribution(), ha="right", va="bottom",
             fontsize=8, color="#64748b", style="italic")

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    out = CHART_DIR / "china_challengers.png"
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: china_challengers.png ({out.stat().st_size / 1024:.0f} KB)")


def main():
    import sys
    skip_gifs = "--skip-gifs" in sys.argv

    print("=== Generating Charts ===\n")

    if not (DATA_DIR / "monthly_totals.csv").exists():
        print("ERROR: No processed data. Run process.py first.")
        return

    chart_yearly_registrations()
    chart_powertrain_absolute()
    chart_brand_rankings()
    chart_china_bev_share()
    chart_china_entry_ramp()
    chart_bev_bloc_share()
    chart_china_bev_lq()
    chart_china_groups()
    chart_china_powertrain_mix()
    chart_china_challengers()

    if skip_gifs:
        print("  Skipping GIF generation (--skip-gifs)")
    else:
        chart_ev_wave()
        chart_ev_race()
        chart_brand_race()
        chart_model_race()

    chart_ev_taste()

    print("\nDone.")


if __name__ == "__main__":
    main()

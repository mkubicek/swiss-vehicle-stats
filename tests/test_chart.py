"""Comprehensive tests for chart.py — targets 100 % line coverage.

Functions already covered in test_utils.py are skipped:
  - display_brand()
  - trailing_months()
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest

import chart


# ---------------------------------------------------------------------------
# Helpers – tiny CSV / JSON / GeoJSON writers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n")


def _monthly_totals_csv(data_dir: Path, years=range(2022, 2025)):
    """Write monthly_totals.csv with 12 months per year."""
    rows = ["year,month,count"]
    for y in years:
        for m in range(1, 13):
            rows.append(f"{y},{m},20000")
    _write_csv(data_dir / "monthly_totals.csv", "\n".join(rows))


def _fuel_by_month_csv(data_dir: Path, years=range(2022, 2025)):
    rows = ["year,month,fuel_type,count"]
    for y in years:
        for m in range(1, 13):
            rows.append(f"{y},{m},Petrol,10000")
            rows.append(f"{y},{m},BEV,5000")
            rows.append(f"{y},{m},PHEV,3000")
            rows.append(f"{y},{m},Diesel,2000")
    _write_csv(data_dir / "fuel_by_month.csv", "\n".join(rows))


def _brand_by_year_csv(data_dir: Path, years=range(2022, 2025)):
    brands = [
        "TESLA", "BMW", "VW", "MERCEDES-BENZ", "AUDI",
        "VOLVO", "HYUNDAI", "KIA", "PORSCHE", "POLESTAR",
        "RENAULT",
    ]
    rows = ["year,brand,count"]
    for y in years:
        for i, b in enumerate(brands):
            rows.append(f"{y},{b},{5000 - i * 200}")
    _write_csv(data_dir / "brand_by_year.csv", "\n".join(rows))


def _brand_bev_by_month_csv(data_dir: Path, months: list[tuple[int, int]]):
    """Write brand_bev_by_month.csv for the given (year, month) pairs."""
    brands = [
        "TESLA", "BMW", "VW", "MERCEDES-BENZ", "AUDI",
        "VOLVO", "HYUNDAI", "KIA", "PORSCHE", "POLESTAR",
        "RENAULT",
    ]
    rows = ["year,month,brand,bev_count"]
    for y, m in months:
        for i, b in enumerate(brands):
            rows.append(f"{y},{m},{b},{500 - i * 30}")
    _write_csv(data_dir / "brand_bev_by_month.csv", "\n".join(rows))


def _canton_ev_by_month_csv(data_dir: Path, months: list[tuple[int, int]]):
    rows = ["canton,year,month,ev_count,total_count"]
    for y, m in months:
        rows.append(f"ZH,{y},{m},500,2000")
        rows.append(f"BE,{y},{m},300,1500")
    _write_csv(data_dir / "canton_ev_by_month.csv", "\n".join(rows))


def _brand_canton_bev_csv(data_dir: Path, cantons=("ZH", "BE")):
    brands = [
        "TESLA", "BMW", "VW", "MERCEDES-BENZ", "AUDI", "VOLVO", "HYUNDAI",
    ]
    rows = ["canton,brand,year,month,bev_count"]
    for c in cantons:
        for b in brands:
            rows.append(f"{c},{b},2023,1,{100 if b == 'TESLA' else 50}")
    _write_csv(data_dir / "brand_canton_bev.csv", "\n".join(rows))


def _geojson(path: Path):
    geo = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "ZH", "name": "Zurich"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[8.4, 47.3], [8.6, 47.3], [8.6, 47.5], [8.4, 47.5], [8.4, 47.3]]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "BE", "name": "Bern"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[7.3, 46.9], [7.5, 46.9], [7.5, 47.1], [7.3, 47.1], [7.3, 46.9]]
                    ],
                },
            },
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(geo))


# ---------------------------------------------------------------------------
# Fixture that redirects chart module paths to tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def chart_dirs(tmp_path, monkeypatch):
    """Redirect DATA_DIR, CHART_DIR, GEOJSON_PATH, ROOT to tmp_path."""
    data_dir = tmp_path / "data" / "processed"
    chart_dir = tmp_path / "charts"
    geojson = tmp_path / "data" / "ch-cantons.geojson"
    data_dir.mkdir(parents=True)
    chart_dir.mkdir(parents=True)
    monkeypatch.setattr(chart, "DATA_DIR", data_dir)
    monkeypatch.setattr(chart, "CHART_DIR", chart_dir)
    monkeypatch.setattr(chart, "GEOJSON_PATH", geojson)
    monkeypatch.setattr(chart, "ROOT", tmp_path)
    # Remove GITHUB_REPOSITORY so get_repo_url falls through to git
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    return data_dir, chart_dir, geojson


# ===================================================================
# load_metadata
# ===================================================================

class TestLoadMetadata:
    def test_file_exists(self, chart_dirs):
        data_dir, _, _ = chart_dirs
        meta_path = data_dir / "metadata.json"
        meta_path.write_text(json.dumps({"data_date": "2025-03-01"}))
        result = chart.load_metadata()
        assert result == {"data_date": "2025-03-01"}

    def test_file_missing(self, chart_dirs):
        result = chart.load_metadata()
        assert result == {}


# ===================================================================
# get_repo_url
# ===================================================================

class TestGetRepoUrl:
    def test_github_repository_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "user/repo")
        assert chart.get_repo_url() == "https://github.com/user/repo"

    def test_git_remote_ssh(self, chart_dirs, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with patch("chart.subprocess.check_output", return_value="git@github.com:user/repo.git\n"):
            url = chart.get_repo_url()
        assert url == "https://github.com/user/repo"

    def test_git_remote_https(self, chart_dirs, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with patch("chart.subprocess.check_output", return_value="https://github.com/user/repo.git\n"):
            url = chart.get_repo_url()
        assert url == "https://github.com/user/repo"

    def test_git_remote_failure(self, chart_dirs, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with patch("chart.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "git")):
            url = chart.get_repo_url()
        assert url == ""


# ===================================================================
# style_chart
# ===================================================================

class TestStyleChart:
    def test_with_subtitle_and_labels(self):
        fig, ax = plt.subplots()
        chart.style_chart(ax, "Title", subtitle="Sub", xlabel="X", ylabel="Y")
        assert ax.get_title() == "Title"
        assert ax.get_xlabel() == "X"
        assert ax.get_ylabel() == "Y"
        plt.close(fig)

    def test_without_subtitle_or_labels(self):
        fig, ax = plt.subplots()
        chart.style_chart(ax, "Title Only")
        assert ax.get_title() == "Title Only"
        assert ax.get_xlabel() == ""
        assert ax.get_ylabel() == ""
        plt.close(fig)


# ===================================================================
# get_dark_attribution
# ===================================================================

class TestGetDarkAttribution:
    def test_with_data_date(self, chart_dirs):
        data_dir, _, _ = chart_dirs
        (data_dir / "metadata.json").write_text(json.dumps({"data_date": "2025-03-01"}))
        with patch("chart.get_repo_url", return_value="https://github.com/u/r"):
            text = chart.get_dark_attribution()
        assert "as of 2025-03-01" in text
        assert "github.com/u/r" in text

    def test_without_data_date(self, chart_dirs):
        with patch("chart.get_repo_url", return_value=""):
            text = chart.get_dark_attribution()
        assert "ASTRA" in text
        assert "as of" not in text


# ===================================================================
# add_attribution
# ===================================================================

class TestAddAttribution:
    def test_with_prefix(self, chart_dirs):
        fig = plt.figure()
        with patch("chart.get_dark_attribution", return_value="attr"):
            chart.add_attribution(fig, prefix="Prefix")
        texts = [t.get_text() for t in fig.texts]
        assert any("Prefix" in t and "attr" in t for t in texts)
        plt.close(fig)

    def test_without_prefix(self, chart_dirs):
        fig = plt.figure()
        with patch("chart.get_dark_attribution", return_value="attr"):
            chart.add_attribution(fig)
        texts = [t.get_text() for t in fig.texts]
        assert any("attr" in t for t in texts)
        # Make sure there's no leading " | "
        assert not any(t.startswith(" | ") for t in texts)
        plt.close(fig)


# ===================================================================
# save_chart
# ===================================================================

class TestSaveChart:
    def test_normal_save(self, chart_dirs, capsys):
        _, chart_dir, _ = chart_dirs
        fig, ax = plt.subplots()
        ax.plot([1, 2], [3, 4])
        chart.save_chart(fig, "test_chart")
        assert (chart_dir / "test_chart.png").exists()
        captured = capsys.readouterr()
        assert "Saved: test_chart.png" in captured.out


# ===================================================================
# load_projection
# ===================================================================

class TestLoadProjection:
    def test_file_missing(self, chart_dirs):
        assert chart.load_projection() is None

    def test_null_projection(self, chart_dirs):
        data_dir, _, _ = chart_dirs
        (data_dir / "projection.json").write_text(json.dumps({"projection": None}))
        assert chart.load_projection() is None

    def test_valid_projection(self, chart_dirs):
        data_dir, _, _ = chart_dirs
        proj = {
            "year": 2025,
            "ytd_actual": 50000,
            "projection": 240000,
            "projection_low": 220000,
            "projection_high": 260000,
            "reference_years": [2017, 2018, 2019, 2022, 2023, 2024],
            "excluded_years": [2020, 2021],
        }
        (data_dir / "projection.json").write_text(json.dumps(proj))
        result = chart.load_projection()
        assert result["year"] == 2025
        assert result["projection"] == 240000


# ===================================================================
# chart_yearly_registrations
# ===================================================================

class TestChartYearlyRegistrations:
    def test_without_projection(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _monthly_totals_csv(data_dir)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_yearly_registrations()
        assert (chart_dir / "yearly_registrations.png").exists()

    def test_with_projection(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _monthly_totals_csv(data_dir)
        proj = {
            "year": 2025,
            "ytd_actual": 50000,
            "projection": 240000,
            "projection_low": 220000,
            "projection_high": 260000,
            "reference_years": [2017, 2018, 2019, 2022, 2023, 2024],
            "excluded_years": [2020, 2021],
        }
        (data_dir / "projection.json").write_text(json.dumps(proj))
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_yearly_registrations()
        assert (chart_dir / "yearly_registrations.png").exists()


# ===================================================================
# chart_powertrain_absolute
# ===================================================================

class TestChartPowertrainAbsolute:
    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _monthly_totals_csv(data_dir)
        _fuel_by_month_csv(data_dir)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_powertrain_absolute()
        assert (chart_dir / "powertrain_absolute.png").exists()


# ===================================================================
# chart_brand_rankings
# ===================================================================

class TestChartBrandRankings:
    def test_file_missing(self, chart_dirs, capsys):
        chart.chart_brand_rankings()
        assert "Skip" in capsys.readouterr().out

    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _brand_by_year_csv(data_dir)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_brand_rankings()
        assert (chart_dir / "brand_rankings.png").exists()


# ===================================================================
# chart_ev_wave
# ===================================================================

class TestChartEvWave:
    def test_data_missing(self, chart_dirs, capsys):
        chart.chart_ev_wave()
        assert "Skip" in capsys.readouterr().out

    def test_geojson_missing(self, chart_dirs, capsys):
        data_dir, _, _ = chart_dirs
        _canton_ev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        chart.chart_ev_wave()
        assert "Skip" in capsys.readouterr().out

    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, geojson = chart_dirs
        _canton_ev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        _geojson(geojson)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_wave()
        assert (chart_dir / "ev_wave.gif").exists()
        assert "Saved: ev_wave.gif" in capsys.readouterr().out

    def test_progress_print(self, chart_dirs, capsys):
        """With 24+ frames the progress print fires (line 474)."""
        data_dir, chart_dir, geojson = chart_dirs
        months = [(2022, m) for m in range(1, 13)] + [(2023, m) for m in range(1, 13)]
        _canton_ev_by_month_csv(data_dir, months)
        _geojson(geojson)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_wave()
        out = capsys.readouterr().out
        assert "ev_wave: frame 24/" in out


# ===================================================================
# chart_ev_race
# ===================================================================

class TestChartEvRace:
    def test_file_missing(self, chart_dirs, capsys):
        chart.chart_ev_race()
        assert "Skip" in capsys.readouterr().out

    def test_no_frames(self, chart_dirs, capsys):
        """If bev_count is all zero, target_months is empty => no frames."""
        data_dir, _, _ = chart_dirs
        csv_text = "year,month,brand,bev_count\n2023,1,TESLA,0\n2023,1,BMW,0"
        _write_csv(data_dir / "brand_bev_by_month.csv", csv_text)
        chart.chart_ev_race()
        assert "Skip: ev_race (no frames)" in capsys.readouterr().out

    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _brand_bev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_race()
        assert (chart_dir / "ev_race.gif").exists()
        assert "Saved: ev_race.gif" in capsys.readouterr().out

    def test_progress_print(self, chart_dirs, capsys):
        """With 24+ frames the progress print fires (line 573)."""
        data_dir, chart_dir, _ = chart_dirs
        months = [(2022, m) for m in range(1, 13)] + [(2023, m) for m in range(1, 13)]
        _brand_bev_by_month_csv(data_dir, months)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_race()
        out = capsys.readouterr().out
        assert "ev_race: frame 24/" in out


# ===================================================================
# chart_brand_race
# ===================================================================

class TestChartBrandRace:
    def test_file_missing(self, chart_dirs, capsys):
        chart.chart_brand_race()
        assert "Skip" in capsys.readouterr().out

    def test_no_frames(self, chart_dirs, capsys):
        data_dir, _, _ = chart_dirs
        csv_text = "year,month,brand,bev_count\n2023,1,TESLA,0\n2023,1,BMW,0"
        _write_csv(data_dir / "brand_bev_by_month.csv", csv_text)
        chart.chart_brand_race()
        assert "Skip: brand_race (no frames)" in capsys.readouterr().out

    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, _ = chart_dirs
        _brand_bev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_brand_race()
        assert (chart_dir / "brand_race.gif").exists()
        assert "Saved: brand_race.gif" in capsys.readouterr().out

    def test_progress_print(self, chart_dirs, capsys):
        """With 24+ frames the progress print fires (line 673)."""
        data_dir, chart_dir, _ = chart_dirs
        months = [(2022, m) for m in range(1, 13)] + [(2023, m) for m in range(1, 13)]
        _brand_bev_by_month_csv(data_dir, months)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_brand_race()
        out = capsys.readouterr().out
        assert "brand_race: frame 24/" in out


# ===================================================================
# chart_ev_taste
# ===================================================================

class TestChartEvTaste:
    def test_file_missing(self, chart_dirs, capsys):
        chart.chart_ev_taste()
        assert "Skip" in capsys.readouterr().out

    def test_insufficient_data(self, chart_dirs, capsys):
        """Empty CSV after filtering by valid cantons => lq_df empty."""
        data_dir, _, geojson = chart_dirs
        _geojson(geojson)
        # Cantons that do NOT match geojson ids
        csv_text = "canton,brand,year,month,bev_count\nXX,TESLA,2023,1,100"
        _write_csv(data_dir / "brand_canton_bev.csv", csv_text)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_taste()
        assert "insufficient data" in capsys.readouterr().out

    def test_normal(self, chart_dirs, capsys):
        data_dir, chart_dir, geojson = chart_dirs
        _geojson(geojson)
        _brand_canton_bev_csv(data_dir)
        with patch("chart.get_repo_url", return_value=""):
            chart.chart_ev_taste()
        assert (chart_dir / "ev_taste_lq.png").exists()
        assert "Saved: ev_taste_lq.png" in capsys.readouterr().out


# ===================================================================
# main
# ===================================================================

class TestMainGuard:
    def test_run_as_main(self, chart_dirs):
        """Cover the ``if __name__ == '__main__'`` block."""
        source = Path(chart.__file__).read_text()
        code = compile(source, chart.__file__, "exec")
        exec(code, {"__name__": "__main__", "__file__": chart.__file__})


class TestMain:
    def test_no_processed_data(self, chart_dirs, capsys):
        """When monthly_totals.csv is absent, main() prints error and returns."""
        chart.main()
        assert "ERROR" in capsys.readouterr().out

    def test_normal_run(self, chart_dirs, capsys):
        """Full pipeline run with all data present."""
        data_dir, chart_dir, geojson = chart_dirs
        _monthly_totals_csv(data_dir)
        _fuel_by_month_csv(data_dir)
        _brand_by_year_csv(data_dir)
        _brand_bev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        _canton_ev_by_month_csv(data_dir, [(2023, 1), (2023, 2)])
        _brand_canton_bev_csv(data_dir)
        _geojson(geojson)

        with patch("chart.get_repo_url", return_value=""):
            chart.main()

        out = capsys.readouterr().out
        assert "Generating Charts" in out
        assert "Done" in out
        assert (chart_dir / "yearly_registrations.png").exists()
        assert (chart_dir / "powertrain_absolute.png").exists()
        assert (chart_dir / "brand_rankings.png").exists()
        assert (chart_dir / "ev_wave.gif").exists()
        assert (chart_dir / "ev_race.gif").exists()
        assert (chart_dir / "brand_race.gif").exists()
        assert (chart_dir / "ev_taste_lq.png").exists()


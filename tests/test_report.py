"""Tests for report.py — monthly delta report generation."""

import json
import pandas as pd
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

import report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path, df):
    df.to_csv(path, index=False)


def _monthly_totals_df(rows):
    """Build monthly_totals from list of (year, month, count)."""
    return pd.DataFrame(rows, columns=["year", "month", "count"])


def _fuel_by_month_df(rows):
    """Build fuel_by_month from list of (year, month, fuel_type, count)."""
    return pd.DataFrame(rows, columns=["year", "month", "fuel_type", "count"])


def _brand_totals_df(rows):
    """Build brand_totals from list of (brand, count)."""
    return pd.DataFrame(rows, columns=["brand", "count"])


def _setup_data_files(data_dir, monthly_rows, fuel_rows, brand_rows, metadata=None):
    """Write all required CSVs and optional metadata.json."""
    _write_csv(data_dir / "monthly_totals.csv", _monthly_totals_df(monthly_rows))
    _write_csv(data_dir / "fuel_by_month.csv", _fuel_by_month_df(fuel_rows))
    _write_csv(data_dir / "brand_totals.csv", _brand_totals_df(brand_rows))
    if metadata:
        with open(data_dir / "metadata.json", "w") as f:
            json.dump(metadata, f)


# ---------------------------------------------------------------------------
# Standard test data
# ---------------------------------------------------------------------------

MONTHLY_ROWS = [
    # 2023 data (for YoY comparison)
    (2023, 1, 20000), (2023, 2, 18000), (2023, 3, 22000),
    (2023, 4, 21000), (2023, 5, 23000), (2023, 6, 24000),
    (2023, 7, 19000), (2023, 8, 17000), (2023, 9, 20000),
    (2023, 10, 21000), (2023, 11, 19000), (2023, 12, 25000),
    # 2024 data
    (2024, 1, 21000), (2024, 2, 19500), (2024, 3, 23500),
]

FUEL_ROWS = [
    # 2023 March
    (2023, 3, "Petrol", 8000), (2023, 3, "Diesel", 5000),
    (2023, 3, "BEV", 4000), (2023, 3, "PHEV", 2000),
    (2023, 3, "Hybrid (Petrol)", 2000), (2023, 3, "Hybrid (Diesel)", 1000),
    # 2024 March
    (2024, 3, "Petrol", 7500), (2024, 3, "Diesel", 4000),
    (2024, 3, "BEV", 6000), (2024, 3, "PHEV", 2500),
    (2024, 3, "Hybrid (Petrol)", 2500), (2024, 3, "Hybrid (Diesel)", 1000),
]

BRAND_ROWS = [
    ("VOLKSWAGEN", 50000), ("BMW", 40000), ("MERCEDES-BENZ", 35000),
    ("AUDI", 30000), ("TOYOTA", 25000), ("TESLA", 20000),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    report_dir = tmp_path / "reports"
    report_dir.mkdir()

    monkeypatch.setattr(report, "DATA_DIR", data_dir)
    monkeypatch.setattr(report, "REPORT_DIR", report_dir)

    return {"data": data_dir, "reports": report_dir}


# ---------------------------------------------------------------------------
# load_metadata
# ---------------------------------------------------------------------------

class TestLoadMetadata:
    def test_file_exists(self, env):
        with open(env["data"] / "metadata.json", "w") as f:
            json.dump({"data_date": "2024-03-15"}, f)
        meta = report.load_metadata()
        assert meta["data_date"] == "2024-03-15"

    def test_file_missing(self, env):
        meta = report.load_metadata()
        assert meta == {}


# ---------------------------------------------------------------------------
# generate_report — explicit target year/month
# ---------------------------------------------------------------------------

class TestGenerateReportExplicit:
    def test_basic_report(self, env):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)

        assert path.exists()
        content = path.read_text()
        assert "March 2024" in content
        assert "23,500" in content  # current count
        assert "BEV share" in content
        assert "Top 5 Brands" in content

    def test_report_filename(self, env):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)
        assert path.name == "2024-03.md"

    def test_with_metadata(self, env):
        _setup_data_files(
            env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS,
            metadata={"data_date": "2024-03-15"},
        )
        path = report.generate_report(target_year=2024, target_month=3)
        content = path.read_text()
        assert "as of 2024-03-15" in content


# ---------------------------------------------------------------------------
# generate_report — auto-detect latest month
# ---------------------------------------------------------------------------

class TestGenerateReportAutoDetect:
    def test_auto_detect_latest(self, env):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        path = report.generate_report()
        content = path.read_text()
        # Latest row is (2024, 3)
        assert "March 2024" in content


# ---------------------------------------------------------------------------
# generate_report — MoM when prev month is December (month == 1)
# ---------------------------------------------------------------------------

class TestGenerateReportJanuary:
    def test_january_wraps_to_december(self, env):
        monthly_rows = [
            (2023, 12, 25000),
            (2024, 1, 21000),
        ]
        fuel_rows = [
            (2023, 1, "Petrol", 10000),
            (2024, 1, "Petrol", 8000), (2024, 1, "BEV", 5000),
            (2024, 1, "PHEV", 2000), (2024, 1, "Diesel", 3000),
            (2024, 1, "Hybrid (Petrol)", 2000), (2024, 1, "Hybrid (Diesel)", 1000),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=1)
        content = path.read_text()
        assert "January 2024" in content
        # Should reference December 2023 as prev month
        assert "December" in content


# ---------------------------------------------------------------------------
# generate_report — prev == 0, yoy == 0, ytd_prev == 0
# ---------------------------------------------------------------------------

class TestGenerateReportZeroPrevious:
    def test_no_prev_no_yoy(self, env):
        """First month ever — no MoM, no YoY, no YTD comparison in key metrics."""
        monthly_rows = [(2024, 1, 10000)]
        fuel_rows = [
            (2024, 1, "Petrol", 5000), (2024, 1, "BEV", 3000),
            (2024, 1, "PHEV", 1000), (2024, 1, "Diesel", 500),
            (2024, 1, "Hybrid (Petrol)", 300), (2024, 1, "Hybrid (Diesel)", 200),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=1)
        content = path.read_text()
        assert "January 2024" in content
        # MoM/YoY/YTD metric rows should not appear in Key Metrics table
        assert "(MoM)" not in content
        assert "vs. January 2023 (YoY)" not in content
        assert "YTD 2024 vs. YTD 2023" not in content


# ---------------------------------------------------------------------------
# generate_report — momentum words
# ---------------------------------------------------------------------------

class TestMomentumWords:
    def _run_with_yoy(self, env, current, yoy_val):
        """Helper: create data where current month and same-month-last-year differ."""
        monthly_rows = [
            (2023, 3, yoy_val),
            (2024, 2, 15000),  # prev month
            (2024, 3, current),
        ]
        fuel_rows = [
            (2023, 3, "Petrol", yoy_val),
            (2024, 3, "Petrol", current // 2), (2024, 3, "BEV", current // 2),
            (2024, 3, "PHEV", 0), (2024, 3, "Diesel", 0),
            (2024, 3, "Hybrid (Petrol)", 0), (2024, 3, "Hybrid (Diesel)", 0),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)
        return path.read_text()

    def test_grew(self, env):
        # current > yoy by > 5%
        content = self._run_with_yoy(env, current=20000, yoy_val=15000)
        assert "grew" in content

    def test_edged_up(self, env):
        # current > yoy by < 5%
        content = self._run_with_yoy(env, current=10400, yoy_val=10000)
        assert "edged up" in content

    def test_declined(self, env):
        # current < yoy by > 5%
        content = self._run_with_yoy(env, current=15000, yoy_val=20000)
        assert "declined" in content

    def test_dipped_slightly(self, env):
        # current < yoy by < 5%
        content = self._run_with_yoy(env, current=9600, yoy_val=10000)
        assert "dipped slightly" in content

    def test_remained_flat(self, env):
        # current == yoy
        content = self._run_with_yoy(env, current=10000, yoy_val=10000)
        assert "remained flat" in content


# ---------------------------------------------------------------------------
# generate_report — fuel YoY N/A
# ---------------------------------------------------------------------------

class TestGenerateReportFuelYoYNA:
    def test_fuel_no_prev_year(self, env):
        """When prev year fuel data is 0, YoY Change shows N/A."""
        monthly_rows = [(2024, 3, 10000)]
        fuel_rows = [
            (2024, 3, "Petrol", 5000), (2024, 3, "BEV", 3000),
            (2024, 3, "Diesel", 1000), (2024, 3, "PHEV", 500),
            (2024, 3, "Hybrid (Petrol)", 300), (2024, 3, "Hybrid (Diesel)", 200),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)
        content = path.read_text()
        assert "N/A" in content


# ---------------------------------------------------------------------------
# generate_report — next report line (December wraps to January)
# ---------------------------------------------------------------------------

class TestGenerateReportNextReport:
    def test_december_wraps_to_january(self, env):
        monthly_rows = [(2024, 11, 20000), (2024, 12, 25000)]
        fuel_rows = [
            (2024, 12, "Petrol", 10000), (2024, 12, "BEV", 8000),
            (2024, 12, "PHEV", 3000), (2024, 12, "Diesel", 2000),
            (2024, 12, "Hybrid (Petrol)", 1000), (2024, 12, "Hybrid (Diesel)", 1000),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=12)
        content = path.read_text()
        assert "January 2025" in content

    def test_mid_year_next_report(self, env):
        monthly_rows = [(2024, 5, 23000), (2024, 4, 21000)]
        fuel_rows = [
            (2024, 5, "Petrol", 10000), (2024, 5, "BEV", 5000),
            (2024, 5, "PHEV", 3000), (2024, 5, "Diesel", 2000),
            (2024, 5, "Hybrid (Petrol)", 2000), (2024, 5, "Hybrid (Diesel)", 1000),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=5)
        content = path.read_text()
        assert "June 2024" in content


# ---------------------------------------------------------------------------
# generate_report — top 5 brands display
# ---------------------------------------------------------------------------

class TestGenerateReportBrands:
    def test_top5_brands(self, env):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)
        content = path.read_text()
        # display_brand("VOLKSWAGEN") -> "VW"
        assert "VW" in content
        assert "BMW" in content


# ---------------------------------------------------------------------------
# generate_report — current == 0 (plugin_share/bev_share = 0)
# ---------------------------------------------------------------------------

class TestGenerateReportZeroCurrent:
    def test_zero_current(self, env):
        """Edge case: current month count is 0."""
        monthly_rows = [(2023, 3, 20000), (2024, 3, 0)]
        fuel_rows = [
            (2023, 3, "Petrol", 10000),
            (2024, 3, "BEV", 0), (2024, 3, "Petrol", 0),
            (2024, 3, "PHEV", 0), (2024, 3, "Diesel", 0),
            (2024, 3, "Hybrid (Petrol)", 0), (2024, 3, "Hybrid (Diesel)", 0),
        ]
        _setup_data_files(env["data"], monthly_rows, fuel_rows, BRAND_ROWS)
        path = report.generate_report(target_year=2024, target_month=3)
        content = path.read_text()
        assert "0.0%" in content  # bev_share and plugin_share


# ---------------------------------------------------------------------------
# main — with sys.argv
# ---------------------------------------------------------------------------

class TestMainWithArgs:
    def test_main_with_year_and_month(self, env, capsys, monkeypatch):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        monkeypatch.setattr(sys, "argv", ["report.py", "2024", "3"])
        report.main()
        out = capsys.readouterr().out
        assert "Generating Delta Report" in out
        assert "Done." in out

    def test_main_with_only_year(self, env, capsys, monkeypatch):
        """Only year arg — month should be None, auto-detected."""
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        monkeypatch.setattr(sys, "argv", ["report.py", "2024"])
        report.main()
        out = capsys.readouterr().out
        assert "Done." in out

    def test_main_no_args(self, env, capsys, monkeypatch):
        _setup_data_files(env["data"], MONTHLY_ROWS, FUEL_ROWS, BRAND_ROWS)
        monkeypatch.setattr(sys, "argv", ["report.py"])
        report.main()
        out = capsys.readouterr().out
        assert "Done." in out


# ---------------------------------------------------------------------------
# main — no data files
# ---------------------------------------------------------------------------

class TestMainNoData:
    def test_no_monthly_totals(self, env, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["report.py"])
        report.main()
        out = capsys.readouterr().out
        assert "No processed data" in out


# ---------------------------------------------------------------------------
# __name__ == "__main__" guard
# ---------------------------------------------------------------------------

class TestMainGuard:
    def test_run_as_main(self, env, monkeypatch):
        """Cover the ``if __name__ == '__main__'`` block."""
        monkeypatch.setattr(sys, "argv", ["report.py"])
        source = Path(report.__file__).read_text()
        code = compile(source, report.__file__, "exec")
        exec(code, {"__name__": "__main__", "__file__": report.__file__})

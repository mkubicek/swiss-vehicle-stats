"""Tests for scripts/project.py — targeting 100% line coverage."""

import json
import csv
import runpy
from pathlib import Path

import pytest

import project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path: Path, rows: list[dict]):
    """Write a list of dicts to a CSV file with fieldnames from first row."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _write_metadata(data_dir: Path, data_date: str):
    """Write metadata.json with the given data_date."""
    with open(data_dir / "metadata.json", "w") as f:
        json.dump({"data_date": data_date}, f)


def _read_projection(data_dir: Path) -> dict:
    with open(data_dir / "projection.json") as f:
        return json.load(f)


def _make_year_rows(year: int, monthly_counts: list[int]) -> list[dict]:
    """Return CSV row dicts for a single year, one per month."""
    return [{"year": year, "month": m + 1, "count": c}
            for m, c in enumerate(monthly_counts)]


# Full 12-month data for reference years (arbitrary but realistic counts)
REF_MONTHLY = [2000, 2500, 2800, 3000, 3200, 2700, 2400, 2300, 2600, 2900, 2500, 2100]
REF_TOTAL = sum(REF_MONTHLY)


# ---------------------------------------------------------------------------
# load_metadata
# ---------------------------------------------------------------------------

class TestLoadMetadata:
    def test_returns_empty_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        assert project.load_metadata() == {}

    def test_returns_contents(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        payload = {"data_date": "2026-03-15", "extra": 42}
        (tmp_path / "metadata.json").write_text(json.dumps(payload))
        assert project.load_metadata() == payload


# ---------------------------------------------------------------------------
# write_null
# ---------------------------------------------------------------------------

class TestWriteNull:
    def test_writes_projection_with_null(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        project.write_null("testing reason")
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert result["reason"] == "testing reason"
        out = capsys.readouterr().out
        assert "testing reason" in out


# ---------------------------------------------------------------------------
# main() — branch 1: no monthly_totals.csv → early return
# ---------------------------------------------------------------------------

class TestMainNoCSV:
    def test_no_csv_early_return(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        project.main()
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert not (tmp_path / "projection.json").exists()


# ---------------------------------------------------------------------------
# main() — branch 2: no metadata / no data_date
# ---------------------------------------------------------------------------

class TestMainNoMetadata:
    def test_no_metadata_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_csv(tmp_path / "monthly_totals.csv",
                   [{"year": 2026, "month": 1, "count": 100}])
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "metadata" in result["reason"].lower() or "data_date" in result["reason"]

    def test_metadata_without_data_date(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_csv(tmp_path / "monthly_totals.csv",
                   [{"year": 2026, "month": 1, "count": 100}])
        (tmp_path / "metadata.json").write_text(json.dumps({"other": "val"}))
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None


# ---------------------------------------------------------------------------
# main() — branch 3: no data for current year
# ---------------------------------------------------------------------------

class TestMainNoCurrentYearData:
    def test_no_current_year_rows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-06-15")
        # CSV has data only for 2025, not 2026
        _write_csv(tmp_path / "monthly_totals.csv",
                   _make_year_rows(2025, REF_MONTHLY))
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "2026" in result["reason"]


# ---------------------------------------------------------------------------
# main() — branch 4: too few complete months (< 2)
# ---------------------------------------------------------------------------

class TestMainTooFewMonths:
    def test_zero_complete_months(self, tmp_path, monkeypatch):
        """data_date in January → complete_months = range(1,1) = []"""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-01-15")
        _write_csv(tmp_path / "monthly_totals.csv",
                   [{"year": 2026, "month": 1, "count": 100}])
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "0 complete" in result["reason"]

    def test_one_complete_month(self, tmp_path, monkeypatch):
        """data_date in February → complete_months = [1] (len 1)"""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-02-15")
        _write_csv(tmp_path / "monthly_totals.csv",
                   [{"year": 2026, "month": 1, "count": 100},
                    {"year": 2026, "month": 2, "count": 50}])
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "1 complete" in result["reason"]


# ---------------------------------------------------------------------------
# main() — branch 5: no valid reference years
# ---------------------------------------------------------------------------

class TestMainNoRefYears:
    def test_no_ref_years_all_covid(self, tmp_path, monkeypatch):
        """Only years available are in EXCLUDED_YEARS or incomplete."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-06-15")
        # Current year partial + only 2020 as "reference" (excluded)
        rows = (_make_year_rows(2020, REF_MONTHLY) +
                _make_year_rows(2026, REF_MONTHLY[:5]))
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "reference" in result["reason"].lower()


# ---------------------------------------------------------------------------
# main() — branch 6: could not compute factors
# ---------------------------------------------------------------------------

class TestMainNoFactors:
    def test_ref_comparable_zero(self, tmp_path, monkeypatch):
        """Reference year has zero counts for the complete months → ref_comparable = 0."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")  # complete months = [1,2,3]
        # Reference year 2022: all 12 months but months 1-3 have 0 count
        ref_counts = [0, 0, 0, 100, 100, 100, 100, 100, 100, 100, 100, 100]
        rows = (_make_year_rows(2022, ref_counts) +
                [{"year": 2026, "month": 1, "count": 100},
                 {"year": 2026, "month": 2, "count": 200},
                 {"year": 2026, "month": 3, "count": 300},
                 {"year": 2026, "month": 4, "count": 50}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        # Month 4 is partial. capture_ratio check: partial_count=50,
        # historical rate for month 4 in 2022 = 100/30 ≈ 3.33
        # observed rate = 50/15 ≈ 3.33 → capture_ratio ≈ 1.0 → use_partial = True
        # ref_comparable = ref_complete(0) + ref_partial(100) * effective_fraction
        # effective_fraction = (15/30) * 1.0 = 0.5
        # ref_comparable = 0 + 100 * 0.5 = 50 → not zero, factor computed
        # So to truly get zero ref_comparable we also need month 4 to be 0 in ref year.
        # But then mean_historical_rate=0 → capture_ratio=1.0 → use_partial with
        # ref_partial=0 → ref_comparable=0. Let's do that.
        ref_counts2 = [0, 0, 0, 0, 100, 100, 100, 100, 100, 100, 100, 100]
        rows2 = (_make_year_rows(2022, ref_counts2) +
                 [{"year": 2026, "month": 1, "count": 100},
                  {"year": 2026, "month": 2, "count": 200},
                  {"year": 2026, "month": 3, "count": 300},
                  {"year": 2026, "month": 4, "count": 50}])
        _write_csv(tmp_path / "monthly_totals.csv", rows2)
        # mean_historical_rate = 0/30 = 0 → capture_ratio = 1.0 (fallback)
        # use_partial = True (0.4 <= 1.0 <= 1.3 and partial_count 50 > 0)
        # effective_fraction = (15/30) * 1.0 = 0.5
        # ref_comparable = 0 + 0 * 0.5 = 0 → factor not appended → factors empty
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is None
        assert "factors" in result["reason"].lower()


# ---------------------------------------------------------------------------
# main() — branch 7: capture_ratio outside [0.4, 1.3] → complete_months_only
# ---------------------------------------------------------------------------

class TestMainCaptureRatioOutOfRange:
    def test_capture_ratio_too_high(self, tmp_path, monkeypatch):
        """Partial month count much higher than historical → capture_ratio > 1.3."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        # data_date = 2026-04-15 → partial_month=4, complete=[1,2,3]
        _write_metadata(tmp_path, "2026-04-15")
        # Reference years 2022, 2023 with normal month-4 counts (~100)
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                # Current year: months 1-3 normal, month 4 very high → ratio > 1.3
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 # REF_MONTHLY[3]=3000 → daily rate ≈ 100; we want observed >> 100
                 {"year": 2026, "month": 4, "count": 9000}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        # observed_rate = 9000/15 = 600; historical_rate ≈ 3000/30 = 100
        # capture_ratio = 600/100 = 6.0 → way above 1.3
        project.main()
        result = _read_projection(tmp_path)
        assert result["projection"] is not None
        assert result["method"] == "complete_months_only"
        assert result["partial_month"] is None
        assert result["partial_month_fraction"] is None
        assert result["effective_fraction"] is None
        # use_partial=False → ytd_prorated = complete_sum
        assert result["ytd_prorated"] == 2000 + 2500 + 2800

    def test_capture_ratio_too_low(self, tmp_path, monkeypatch):
        """Partial month count much lower than historical → capture_ratio < 0.4."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 # Very low partial: observed_rate = 1/15 ≈ 0.067, hist ≈ 100
                 {"year": 2026, "month": 4, "count": 1}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["method"] == "complete_months_only"


# ---------------------------------------------------------------------------
# main() — branch 8: valid capture_ratio → pro_rated_with_lag_correction
# ---------------------------------------------------------------------------

class TestMainValidProjection:
    def test_pro_rated_method(self, tmp_path, monkeypatch, capsys):
        """Normal case: multiple ref years, partial month within ratio bounds."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        # Two complete ref years (2022, 2023) with identical data for simplicity
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                # Current year: same daily rates as ref → capture ≈ 1.0
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 1500}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)

        assert result["projection"] is not None
        assert result["method"] == "pro_rated_with_lag_correction"
        assert result["year"] == 2026
        assert result["data_date"] == "2026-04-15"
        assert result["complete_months"] == 3
        assert result["partial_month"] == 4
        assert result["partial_month_fraction"] is not None
        assert result["capture_ratio"] is not None
        assert result["effective_fraction"] is not None
        assert result["ytd_actual"] == 2000 + 2500 + 2800 + 1500
        assert result["reference_years"] == [2022, 2023]
        assert result["excluded_years"] == [2020, 2021]
        assert result["band"] == "2sigma"
        assert result["projection_low"] <= result["projection"] <= result["projection_high"]

        # ytd_prorated should be computed (use_partial=True, effective_fraction > 0)
        assert result["ytd_prorated"] > 0

        # Check stdout has the partial-month info line
        out = capsys.readouterr().out
        assert "Partial month" in out
        assert "Method:" in out
        assert "CV:" in out


# ---------------------------------------------------------------------------
# main() — branch 9: len(factors) == 1 → std_factor = 0.0
# ---------------------------------------------------------------------------

class TestMainSingleRefYear:
    def test_single_factor_std_zero(self, tmp_path, monkeypatch):
        """Only one valid reference year → std_factor = 0.0, low == high == projection."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        # Only 2022 as reference (2023 incomplete → excluded)
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 1500}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["std_factor"] == 0.0
        assert result["projection_low"] == result["projection"]
        assert result["projection_high"] == result["projection"]


# ---------------------------------------------------------------------------
# main() — branch 10: use_partial=True, effective_fraction > 0 → ytd_prorated
# ---------------------------------------------------------------------------

class TestMainYtdProrated:
    def test_ytd_prorated_computed(self, tmp_path, monkeypatch):
        """When use_partial=True and effective_fraction > 0, ytd_prorated =
        complete_sum + partial_count / effective_fraction."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 1500}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        # use_partial should be True here
        assert result["method"] == "pro_rated_with_lag_correction"
        complete_sum = 2000 + 2500 + 2800
        # ytd_prorated = round(complete_sum + partial_count / effective_fraction)
        eff = result["effective_fraction"]
        expected = round(complete_sum + 1500 / eff)
        assert result["ytd_prorated"] == expected


# ---------------------------------------------------------------------------
# main() — branch 11: use_partial=False → ytd_prorated = complete_sum
# ---------------------------------------------------------------------------

class TestMainYtdProratedFallback:
    def test_ytd_prorated_equals_complete_sum(self, tmp_path, monkeypatch):
        """When use_partial=False, ytd_prorated = complete_sum (no partial adjustment)."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 # Very high partial → capture_ratio >> 1.3 → use_partial=False
                 {"year": 2026, "month": 4, "count": 9000}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["method"] == "complete_months_only"
        assert result["ytd_prorated"] == 2000 + 2500 + 2800


# ---------------------------------------------------------------------------
# Edge: partial_count = 0 forces use_partial=False even if ratio is fine
# ---------------------------------------------------------------------------

class TestMainPartialCountZero:
    def test_partial_zero_means_no_partial(self, tmp_path, monkeypatch):
        """partial_count=0 → use_partial=False regardless of capture_ratio."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 0}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["method"] == "complete_months_only"


# ---------------------------------------------------------------------------
# Edge: no partial row at all (partial_row empty → partial_count = 0)
# ---------------------------------------------------------------------------

class TestMainNoPartialRow:
    def test_no_partial_row_in_csv(self, tmp_path, monkeypatch):
        """No row for partial_month in current year → partial_count = 0."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2022, REF_MONTHLY) +
                _make_year_rows(2023, REF_MONTHLY) +
                # Only months 1-3 for 2026, no month 4
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert result["method"] == "complete_months_only"


# ---------------------------------------------------------------------------
# Edge: excluded years are properly skipped
# ---------------------------------------------------------------------------

class TestMainExcludedYears:
    def test_covid_years_excluded(self, tmp_path, monkeypatch):
        """2020 and 2021 are not used as reference years."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2019, REF_MONTHLY) +
                _make_year_rows(2020, REF_MONTHLY) +  # excluded
                _make_year_rows(2021, REF_MONTHLY) +  # excluded
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 1500}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert 2020 not in result["reference_years"]
        assert 2021 not in result["reference_years"]
        assert 2019 in result["reference_years"]


# ---------------------------------------------------------------------------
# Edge: REF_YEAR_START boundary — years before it are excluded
# ---------------------------------------------------------------------------

class TestMainRefYearStart:
    def test_year_before_ref_start_excluded(self, tmp_path, monkeypatch):
        """Years before REF_YEAR_START (2016) are not reference years."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        _write_metadata(tmp_path, "2026-04-15")
        rows = (_make_year_rows(2015, REF_MONTHLY) +  # before REF_YEAR_START
                _make_year_rows(2016, REF_MONTHLY) +  # included
                [{"year": 2026, "month": 1, "count": 2000},
                 {"year": 2026, "month": 2, "count": 2500},
                 {"year": 2026, "month": 3, "count": 2800},
                 {"year": 2026, "month": 4, "count": 1500}])
        _write_csv(tmp_path / "monthly_totals.csv", rows)
        project.main()
        result = _read_projection(tmp_path)
        assert 2015 not in result["reference_years"]
        assert 2016 in result["reference_years"]


# ---------------------------------------------------------------------------
# __name__ == "__main__" guard
# ---------------------------------------------------------------------------

class TestMainGuard:
    def test_main_guard(self, tmp_path, monkeypatch):
        """Execute the module as __main__ to cover the if-guard."""
        monkeypatch.setattr(project, "DATA_DIR", tmp_path)
        # No CSV → hits early return quickly
        runpy.run_module("project", run_name="__main__", alter_sys=False)

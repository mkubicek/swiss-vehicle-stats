"""Tests for validate.py — plausibility checks for processed ASTRA data."""

import pandas as pd
import pytest
import yaml
from datetime import datetime
from pathlib import Path

import validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monthly_df(rows):
    """Build a minimal monthly_totals DataFrame from list of (year, month, count)."""
    return pd.DataFrame(rows, columns=["year", "month", "count"])


def _write_csv(path, df):
    df.to_csv(path, index=False)


def _write_yaml(path, data):
    with open(path, "w") as f:
        yaml.dump(data, f)


# ---------------------------------------------------------------------------
# load_reference
# ---------------------------------------------------------------------------

class TestLoadReference:
    def test_file_exists(self, tmp_path, monkeypatch):
        ref_file = tmp_path / "reference.yaml"
        _write_yaml(ref_file, {2023: {"total": 100000}})
        monkeypatch.setattr(validate, "REFERENCE_FILE", ref_file)
        result = validate.load_reference()
        assert result == {2023: {"total": 100000}}

    def test_file_does_not_exist(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        result = validate.load_reference()
        assert result == {}

    def test_empty_yaml_returns_empty_dict(self, tmp_path, monkeypatch):
        ref_file = tmp_path / "reference.yaml"
        ref_file.write_text("")  # yaml.safe_load returns None
        monkeypatch.setattr(validate, "REFERENCE_FILE", ref_file)
        result = validate.load_reference()
        assert result == {}


# ---------------------------------------------------------------------------
# check_yearly_totals
# ---------------------------------------------------------------------------

class TestCheckYearlyTotals:
    def test_within_tolerance(self):
        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        ref = {2023: {"total": 120000}}  # exact match
        warnings = validate.check_yearly_totals(monthly, ref)
        assert warnings == []

    def test_exceeds_tolerance(self):
        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        # ASTRA total = 120000, ref = 100000 -> diff_pct = 0.2 > 0.02
        ref = {2023: {"total": 100000}}
        warnings = validate.check_yearly_totals(monthly, ref)
        assert len(warnings) == 1
        assert "plausibility:yearly_total:2023" in warnings[0]

    def test_year_not_in_ref(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2024: {"total": 10000}}
        warnings = validate.check_yearly_totals(monthly, ref)
        assert warnings == []


# ---------------------------------------------------------------------------
# check_monthly_totals
# ---------------------------------------------------------------------------

class TestCheckMonthlyTotals:
    def test_within_tolerance(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2023: {"months": {1: {"total": 10000}}}}
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []

    def test_exceeds_tolerance(self):
        monthly = _monthly_df([(2023, 1, 12000)])
        ref = {2023: {"months": {1: {"total": 10000}}}}  # diff = 20%
        warnings = validate.check_monthly_totals(monthly, ref)
        assert len(warnings) == 1
        assert "plausibility:monthly_total:2023-01" in warnings[0]

    def test_year_not_in_ref(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2024: {"months": {1: {"total": 10000}}}}
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []

    def test_month_not_in_ref(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2023: {"months": {2: {"total": 10000}}}}
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []

    def test_no_months_key(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2023: {"total": 120000}}  # no "months" key
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []

    def test_no_total_key_in_month(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2023: {"months": {1: {"bev": 5000}}}}  # no "total" key
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []

    def test_ref_total_zero(self):
        monthly = _monthly_df([(2023, 1, 10000)])
        ref = {2023: {"months": {1: {"total": 0}}}}
        warnings = validate.check_monthly_totals(monthly, ref)
        assert warnings == []  # ref_total > 0 check prevents division


# ---------------------------------------------------------------------------
# check_bev_totals
# ---------------------------------------------------------------------------

class TestCheckBevTotals:
    def test_no_fuel_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        ref = {2023: {"months": {1: {"bev": 1000}}}}
        warnings = validate.check_bev_totals(ref)
        assert warnings == []

    def test_within_tolerance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [1000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        ref = {2023: {"months": {1: {"bev": 1000}}}}
        warnings = validate.check_bev_totals(ref)
        assert warnings == []

    def test_exceeds_tolerance(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [500],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        ref = {2023: {"months": {1: {"bev": 1000}}}}  # diff = 50% > 5%
        warnings = validate.check_bev_totals(ref)
        assert len(warnings) == 1
        assert "plausibility:bev_monthly:2023-01" in warnings[0]

    def test_no_bev_ref_data(self, tmp_path, monkeypatch):
        """ref year has months but no 'bev' key — should skip."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [1000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        ref = {2023: {"months": {1: {"total": 5000}}}}
        warnings = validate.check_bev_totals(ref)
        assert warnings == []

    def test_no_months_key_in_ref(self, tmp_path, monkeypatch):
        """ref year has no 'months' key — should continue to next year."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [1000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        ref = {2023: {"total": 120000}}
        warnings = validate.check_bev_totals(ref)
        assert warnings == []

    def test_ref_bev_zero(self, tmp_path, monkeypatch):
        """ref_bev == 0 should skip the tolerance check."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [100],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        ref = {2023: {"months": {1: {"bev": 0}}}}
        warnings = validate.check_bev_totals(ref)
        assert warnings == []


# ---------------------------------------------------------------------------
# check_monthly_range
# ---------------------------------------------------------------------------

class TestCheckMonthlyRange:
    def test_within_range(self):
        monthly = _monthly_df([(2023, 1, 20000)])
        warnings = validate.check_monthly_range(monthly)
        assert warnings == []

    def test_below_minimum(self):
        monthly = _monthly_df([(2023, 1, 3000)])
        warnings = validate.check_monthly_range(monthly)
        assert len(warnings) == 1
        assert "below minimum" in warnings[0]

    def test_above_maximum(self):
        monthly = _monthly_df([(2023, 1, 50000)])
        warnings = validate.check_monthly_range(monthly)
        assert len(warnings) == 1
        assert "above maximum" in warnings[0]


# ---------------------------------------------------------------------------
# check_complete_years
# ---------------------------------------------------------------------------

class TestCheckCompleteYears:
    def test_complete_year(self):
        monthly = _monthly_df([(2022, m, 10000) for m in range(1, 13)])
        warnings = validate.check_complete_years(monthly)
        assert warnings == []

    def test_incomplete_year(self):
        monthly = _monthly_df([(2022, m, 10000) for m in range(1, 11)])
        warnings = validate.check_complete_years(monthly)
        assert len(warnings) == 1
        assert "2022" in warnings[0]
        assert "11" in warnings[0]  # missing month 11
        assert "12" in warnings[0]  # missing month 12

    def test_current_year_skipped(self, monkeypatch):
        current_year = datetime.now().year
        # Only 3 months for current year — should NOT trigger warning
        monthly = _monthly_df([(current_year, m, 10000) for m in range(1, 4)])
        warnings = validate.check_complete_years(monthly)
        assert warnings == []


# ---------------------------------------------------------------------------
# check_yoy_spikes
# ---------------------------------------------------------------------------

class TestCheckYoySpikes:
    def test_no_spike(self):
        monthly = _monthly_df([
            (2023, 1, 10000),
            (2023, 2, 10500),  # 5% change, below 50% threshold
        ])
        warnings = validate.check_yoy_spikes(monthly)
        assert warnings == []

    def test_spike_detected(self):
        monthly = _monthly_df([
            (2023, 1, 10000),
            (2023, 2, 20000),  # 100% change > 50% threshold
        ])
        warnings = validate.check_yoy_spikes(monthly)
        assert len(warnings) == 1
        assert "plausibility:mom_spike:2023-02" in warnings[0]

    def test_covid_year_skipped(self):
        monthly = _monthly_df([
            (2019, 12, 10000),
            (2020, 1, 20000),  # 100% spike but 2020 is COVID year
        ])
        warnings = validate.check_yoy_spikes(monthly)
        assert warnings == []

    def test_covid_year_2021_skipped(self):
        monthly = _monthly_df([
            (2020, 12, 10000),
            (2021, 1, 20000),  # 100% spike but 2021 is COVID year
        ])
        warnings = validate.check_yoy_spikes(monthly)
        assert warnings == []

    def test_prev_count_is_none(self):
        """First row: prev_count is None, should not warn."""
        monthly = _monthly_df([(2023, 1, 10000)])
        warnings = validate.check_yoy_spikes(monthly)
        assert warnings == []

    def test_prev_count_is_zero(self):
        """prev_count == 0 should not trigger (guarded by prev_count > 0)."""
        monthly = _monthly_df([
            (2023, 1, 0),
            (2023, 2, 10000),
        ])
        warnings = validate.check_yoy_spikes(monthly)
        assert warnings == []


# ---------------------------------------------------------------------------
# check_fuel_consistency
# ---------------------------------------------------------------------------

class TestCheckFuelConsistency:
    def test_no_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        warnings = validate.check_fuel_consistency()
        assert warnings == []

    def test_only_fuel_file(self, tmp_path, monkeypatch):
        """fuel file exists but monthly_totals doesn't — early return."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023], "month": [1], "fuel_type": ["BEV"], "count": [1000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        warnings = validate.check_fuel_consistency()
        assert warnings == []

    def test_matching_totals(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023, 2023], "month": [1, 1],
            "fuel_type": ["BEV", "Petrol"], "count": [3000, 7000],
        })
        monthly = pd.DataFrame({
            "year": [2023], "month": [1], "count": [10000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        _write_csv(tmp_path / "monthly_totals.csv", monthly)
        warnings = validate.check_fuel_consistency()
        assert warnings == []

    def test_mismatched_totals(self, tmp_path, monkeypatch):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        fuel = pd.DataFrame({
            "year": [2023, 2023], "month": [1, 1],
            "fuel_type": ["BEV", "Petrol"], "count": [3000, 6000],
        })
        monthly = pd.DataFrame({
            "year": [2023], "month": [1], "count": [10000],
        })
        _write_csv(tmp_path / "fuel_by_month.csv", fuel)
        _write_csv(tmp_path / "monthly_totals.csv", monthly)
        warnings = validate.check_fuel_consistency()
        assert len(warnings) == 1
        assert "plausibility:fuel_mismatch:2023-01" in warnings[0]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    def test_no_monthly_totals(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "reference.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")
        validate.main()
        captured = capsys.readouterr()
        assert "No processed data" in captured.out

    def test_with_ref(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")
        ref_file = tmp_path / "reference.yaml"
        _write_yaml(ref_file, {2023: {"total": 120000, "months": {1: {"total": 10000}}}})
        monkeypatch.setattr(validate, "REFERENCE_FILE", ref_file)

        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        validate.main()
        captured = capsys.readouterr()
        assert "Checking against auto.swiss reference" in captured.out
        assert "Done." in captured.out
        assert (tmp_path / "warnings.log").exists()

    def test_without_ref(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")

        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        validate.main()
        captured = capsys.readouterr()
        assert "No reference.yaml found" in captured.out

    def test_existing_warnings_file_various_lines(self, tmp_path, monkeypatch, capsys):
        """Existing warnings.log has comments, blank lines, unmapped:, and plausibility: lines."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")

        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        # Pre-populate warnings.log with various line types
        warnings_content = (
            "# Header comment\n"
            "\n"
            "plausibility:old_warning\n"
            "unmapped:SOME_BRAND -> Unknown\n"
            "ANOTHER_BRAND -> Unknown\n"
        )
        (tmp_path / "warnings.log").write_text(warnings_content)

        validate.main()
        captured = capsys.readouterr()

        # Read resulting file
        result = (tmp_path / "warnings.log").read_text()
        # plausibility: lines from old file are dropped
        assert "plausibility:old_warning" not in result
        # unmapped: prefix is stripped and re-added
        assert "unmapped:SOME_BRAND -> Unknown" in result
        # bare line gets unmapped: prefix
        assert "unmapped:ANOTHER_BRAND -> Unknown" in result
        # Comments and blanks are skipped
        assert "# Header comment" not in result.split("\n")[2:]  # only new header
        assert "Unmapped:" in captured.out

    def test_main_with_plausibility_warnings(self, tmp_path, monkeypatch, capsys):
        """Trigger actual plausibility warnings so the print branches are hit."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")

        # Use out-of-range counts to trigger range warnings
        monthly = _monthly_df([(2023, m, 3000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        validate.main()
        captured = capsys.readouterr()
        assert "Plausibility:" in captured.out
        # warnings.log should contain plausibility warnings
        result = (tmp_path / "warnings.log").read_text()
        assert "plausibility:" in result

    def test_main_no_existing_warnings_file(self, tmp_path, monkeypatch, capsys):
        """No pre-existing warnings.log — the WARNINGS_FILE.exists() branch is False."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")

        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        # Ensure no warnings.log exists before calling main
        assert not (tmp_path / "warnings.log").exists()
        validate.main()
        assert (tmp_path / "warnings.log").exists()
        captured = capsys.readouterr()
        assert "Total warnings: 0" in captured.out


# ---------------------------------------------------------------------------
# __name__ == "__main__" guard
# ---------------------------------------------------------------------------

class TestMainGuard:
    def test_run_as_main(self, tmp_path, monkeypatch):
        """Cover the ``if __name__ == '__main__'`` block (line 236)."""
        monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
        monkeypatch.setattr(validate, "REFERENCE_FILE", tmp_path / "nonexistent.yaml")
        monkeypatch.setattr(validate, "WARNINGS_FILE", tmp_path / "warnings.log")

        monthly = _monthly_df([(2023, m, 10000) for m in range(1, 13)])
        _write_csv(tmp_path / "monthly_totals.csv", monthly)

        # Read the actual source, compile with the real filename so coverage
        # attributes execution to the correct file/lines, then exec with
        # __name__ set to "__main__".
        src = Path(validate.__file__).read_text()
        code = compile(src, validate.__file__, "exec")
        ns = {"__name__": "__main__", "__file__": validate.__file__}
        exec(code, ns)

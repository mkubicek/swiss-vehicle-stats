"""Tests for pure utility functions across scripts."""

import math
import pandas as pd
import pytest

from chart import trailing_months, display_brand
from process import safe_map, detect_separator, merge_aggs
from report import pct_change, delta_str


# --- trailing_months ---

class TestTrailingMonths:
    def test_year_boundary(self):
        """January should wrap back to previous year's December."""
        result = trailing_months(2024, 1, 3)
        assert result == [(2024, 1), (2023, 12), (2023, 11)]

    def test_single_month(self):
        result = trailing_months(2024, 6, 1)
        assert result == [(2024, 6)]

    def test_full_year_from_december(self):
        result = trailing_months(2024, 12, 12)
        assert result[0] == (2024, 12)
        assert result[-1] == (2024, 1)
        assert len(result) == 12

    def test_multi_year_span(self):
        result = trailing_months(2024, 3, 15)
        assert len(result) == 15
        assert result[0] == (2024, 3)
        assert result[-1] == (2023, 1)

    def test_mid_year(self):
        result = trailing_months(2024, 6, 6)
        assert result == [
            (2024, 6), (2024, 5), (2024, 4),
            (2024, 3), (2024, 2), (2024, 1),
        ]


# --- display_brand ---

class TestDisplayBrand:
    def test_brand_case_override_bmw(self):
        assert display_brand("bmw") == "BMW"

    def test_brand_case_override_vw(self):
        assert display_brand("VW") == "VW"

    def test_volkswagen_alias(self):
        assert display_brand("VOLKSWAGEN") == "VW"
        assert display_brand("volkswagen") == "VW"

    def test_title_case_fallback(self):
        assert display_brand("TOYOTA") == "Toyota"
        assert display_brand("mercedes-benz") == "Mercedes-Benz"

    def test_whitespace(self):
        assert display_brand("  BMW  ") == "BMW"
        assert display_brand("  toyota  ") == "Toyota"


# --- safe_map ---

class TestSafeMap:
    def test_exact_match(self):
        mapping = {"Elektrisch": "BEV", "Benzin": "Petrol"}
        assert safe_map("Elektrisch", mapping) == "BEV"

    def test_case_insensitive(self):
        mapping = {"TESLA": "Tesla", "BMW": "BMW"}
        assert safe_map("tesla", mapping) == "Tesla"

    def test_nan(self):
        mapping = {"a": "b"}
        assert safe_map(float("nan"), mapping) == "Other"

    def test_none(self):
        mapping = {"a": "b"}
        assert safe_map(None, mapping) == "Other"

    def test_missing_key(self):
        mapping = {"a": "b"}
        assert safe_map("z", mapping) == "Other"

    def test_custom_default(self):
        mapping = {"a": "b"}
        assert safe_map("z", mapping, default="Unknown") == "Unknown"

    def test_whitespace(self):
        mapping = {"TESLA": "Tesla"}
        assert safe_map("  TESLA  ", mapping) == "Tesla"

    def test_numeric_coercion(self):
        mapping = {"123": "match"}
        assert safe_map(123, mapping) == "match"


# --- detect_separator ---

class TestDetectSeparator:
    def test_tsv(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("col1\tcol2\tcol3\nval1\tval2\tval3\n")
        assert detect_separator(f) == "\t"

    def test_csv(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("col1,col2,col3\nval1,val2,val3\n")
        assert detect_separator(f) == ","


# --- merge_aggs ---

class TestMergeAggs:
    def test_disjoint_keys(self):
        a = {"monthly_totals": pd.DataFrame({"year": [2023], "count": [100]})}
        b = {"fuel_totals": pd.DataFrame({"fuel": ["BEV"], "count": [50]})}
        result = merge_aggs(a, b)
        assert "monthly_totals" in result
        assert "fuel_totals" in result

    def test_overlapping_concatenation(self):
        a = {"monthly_totals": pd.DataFrame({"year": [2023], "count": [100]})}
        b = {"monthly_totals": pd.DataFrame({"year": [2024], "count": [200]})}
        result = merge_aggs(a, b)
        assert len(result["monthly_totals"]) == 2

    def test_datenstand_last_wins(self):
        a = {"_datenstand": "2024-01-01"}
        b = {"_datenstand": "2024-02-01"}
        result = merge_aggs(a, b)
        assert result["_datenstand"] == "2024-02-01"


# --- pct_change ---

class TestPctChange:
    def test_positive(self):
        assert pct_change(110, 100) == "+10.0%"

    def test_negative(self):
        assert pct_change(90, 100) == "-10.0%"

    def test_zero_denominator(self):
        assert pct_change(100, 0) == "N/A"

    def test_no_change(self):
        assert pct_change(100, 100) == "+0.0%"


# --- delta_str ---

class TestDeltaStr:
    def test_positive(self):
        result = delta_str(1100, 1000)
        assert result == "+100 (+10.0%)"

    def test_negative(self):
        result = delta_str(900, 1000)
        assert result == "-100 (-10.0%)"

    def test_zero_denominator(self):
        result = delta_str(100, 0)
        assert result == "+100 (N/A)"

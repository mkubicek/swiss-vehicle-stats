"""Tests for pure utility functions across scripts."""

import math
import pandas as pd
import pytest

from chart import trailing_months, display_brand, china_bev_share_series
from process import (
    safe_map, detect_separator, merge_aggs,
    is_china_owned, is_china_branded, detect_entry_month,
)
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


# --- ownership blocs (owner_country) ---

# Edge cases from the spec: Volvo/Polestar (Sweden heritage, Geely-owned),
# MG (British heritage, SAIC-owned), Smart (German heritage, Geely-JV) are all
# China-owned but NOT China-branded; BYD is both; Tesla is neither.
BLOC_MAP = {
    "brand_origin": {
        "BYD": "China", "NIO": "China",
        "VOLVO": "Sweden", "POLESTAR": "Sweden",
        "SMART": "Germany", "MG": "UK", "TESLA": "USA", "VW": "Germany",
    },
    "brand_owner_country": {
        "BYD": "China", "VOLVO": "China", "POLESTAR": "China",
        "SMART": "China", "MG": "China", "TESLA": "USA", "VW": "Germany",
    },
}


class TestChinaBloc:
    def test_china_branded_is_by_heritage(self):
        assert is_china_branded("BYD", BLOC_MAP)
        assert not is_china_branded("VOLVO", BLOC_MAP)
        assert not is_china_branded("MG", BLOC_MAP)
        assert not is_china_branded("SMART", BLOC_MAP)
        assert not is_china_branded("TESLA", BLOC_MAP)

    def test_china_owned_includes_european_badges(self):
        for brand in ("BYD", "VOLVO", "POLESTAR", "SMART", "MG"):
            assert is_china_owned(brand, BLOC_MAP), brand
        assert not is_china_owned("TESLA", BLOC_MAP)
        assert not is_china_owned("VW", BLOC_MAP)

    def test_heritage_china_is_owned_without_explicit_entry(self):
        # NIO has origin China but no brand_owner_country entry — the union rule
        # still classifies it China-owned (branded ⊆ owned).
        assert is_china_branded("NIO", BLOC_MAP)
        assert is_china_owned("NIO", BLOC_MAP)

    def test_unmapped_brand_is_neither(self):
        assert not is_china_branded("FOOBAR", BLOC_MAP)
        assert not is_china_owned("FOOBAR", BLOC_MAP)


# --- detect_entry_month ---

class TestDetectEntryMonth:
    def test_first_sustained_month(self):
        series = [(2023, 1, 6), (2023, 2, 8), (2023, 3, 10)]
        assert detect_entry_month(series) == (2023, 1)

    def test_grey_import_spike_is_skipped(self):
        # A lone ≥5 spike that drops back to zero for 3 months is not entry;
        # the sustained run in 2023 is.
        series = [(2019, 3, 7), (2019, 4, 0), (2019, 5, 0), (2019, 6, 0),
                  (2023, 1, 9), (2023, 2, 12), (2023, 3, 15)]
        assert detect_entry_month(series) == (2023, 1)

    def test_single_import_below_threshold(self):
        # Spec example: 1 registration in 2019, sustained sales from 2023.
        series = [(2019, 5, 1), (2023, 1, 8), (2023, 2, 9), (2023, 3, 10)]
        assert detect_entry_month(series) == (2023, 1)

    def test_never_reaches_threshold(self):
        assert detect_entry_month([(2024, 1, 1), (2024, 2, 2), (2024, 3, 4)]) is None

    def test_entry_in_final_month_with_no_followup(self):
        # No following months to disprove a one-off, so the ≥5 month counts.
        assert detect_entry_month([(2024, 1, 2), (2024, 2, 9)]) == (2024, 2)


# --- china_bev_share_series ---

class TestChinaBevShareSeries:
    def _df(self):
        rows = []
        for y in (2022, 2023):
            for month in range(1, 13):
                rows.append((y, month, "BYD", 10))    # China-branded + owned
                rows.append((y, month, "VOLVO", 15))  # China-owned only
                rows.append((y, month, "TESLA", 30))
                rows.append((y, month, "VW", 45))
        return pd.DataFrame(rows, columns=["year", "month", "brand", "bev_count"])

    def test_shares_bounded_and_branded_leq_owned(self):
        series = china_bev_share_series(self._df(), BLOC_MAP, start=(2022, 12))
        assert not series.empty
        assert (series["branded"] <= series["owned"] + 1e-9).all()
        assert (series["owned"] <= 1.0 + 1e-9).all()

    def test_share_values(self):
        series = china_bev_share_series(self._df(), BLOC_MAP, start=(2022, 12))
        last = series.iloc[-1]
        # owned = (BYD 10 + VOLVO 15) / 100 ; branded = BYD 10 / 100 ; tesla 30/100
        assert last["owned"] == pytest.approx(0.25)
        assert last["branded"] == pytest.approx(0.10)
        assert last["tesla"] == pytest.approx(0.30)

    def test_empty_frame_returns_empty(self):
        empty = pd.DataFrame(columns=["year", "month", "brand", "bev_count"])
        assert china_bev_share_series(empty, BLOC_MAP).empty

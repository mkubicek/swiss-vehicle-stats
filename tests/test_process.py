"""Tests for process.py — ASTRA NEUZU data processing pipeline."""

import json
import pandas as pd
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

import process


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_MAPPINGS = {
    "fuel_types": {
        "Elektrisch": "BEV",
        "Benzin": "Petrol",
        "Diesel": "Diesel",
        "Benzin-elektrisch": "PHEV",
        "Wasserstoff": "Hydrogen",
    },
    "brand_origin": {
        "TESLA": "USA",
        "BMW": "Germany",
    },
    "brand_group": {
        "TESLA": "Tesla",
        "BMW": "BMW Group",
    },
    "country_continent": {
        "USA": "North America",
        "Germany": "Europe",
    },
    "colors": {
        "SCHWARZ": "Black",
        "WEISS": "White",
    },
    "plate_usage": {
        "Weiss": "Private",
        "Blau": "Commercial",
    },
    "drive_types": {
        "Hinterrad": "RWD",
        "Allrad": "4x4",
    },
}


def _write_mappings(path):
    with open(path, "w") as f:
        yaml.dump(MINIMAL_MAPPINGS, f)


def _make_tsv(filepath, rows, header=None, sep="\t"):
    """Write a TSV/CSV test file.  *rows* is a list of dicts (one per data row)."""
    if header is None:
        header = list(rows[0].keys()) if rows else []
    lines = [sep.join(header)]
    for row in rows:
        lines.append(sep.join(str(row.get(h, "")) for h in header))
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _full_row(**overrides):
    """Return a single data row dict with all USE_COLS populated."""
    base = {
        "Fahrzeugart": "Personenwagen",
        "Marke": "TESLA",
        "Treibstoff": "Elektrisch",
        "Farbe": "SCHWARZ",
        "Schildfarbe": "Weiss",
        "Antrieb": "Allrad",
        "Erstinverkehrsetzung_Jahr": "2024",
        "Erstinverkehrsetzung_Monat": "3",
        "Erstinverkehrsetzung_Kanton": "ZH",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Set up isolated directories and a minimal mappings file."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "processed"
    out_dir.mkdir()
    mappings_file = tmp_path / "mappings.yaml"
    warnings_file = tmp_path / "warnings.log"

    _write_mappings(mappings_file)

    monkeypatch.setattr(process, "ROOT", tmp_path)
    monkeypatch.setattr(process, "RAW_DIR", raw_dir)
    monkeypatch.setattr(process, "OUT_DIR", out_dir)
    monkeypatch.setattr(process, "MAPPINGS_FILE", mappings_file)
    monkeypatch.setattr(process, "WARNINGS_FILE", warnings_file)

    return {
        "tmp": tmp_path,
        "raw": raw_dir,
        "out": out_dir,
        "mappings_file": mappings_file,
        "warnings_file": warnings_file,
    }


# ---------------------------------------------------------------------------
# load_mappings
# ---------------------------------------------------------------------------

class TestLoadMappings:
    def test_loads_yaml(self, env):
        m = process.load_mappings()
        assert "fuel_types" in m
        assert m["fuel_types"]["Elektrisch"] == "BEV"


# ---------------------------------------------------------------------------
# find_raw_files
# ---------------------------------------------------------------------------

class TestFindRawFiles:
    def test_no_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(process, "RAW_DIR", tmp_path / "nonexistent")
        with pytest.raises(SystemExit):
            process.find_raw_files()

    def test_no_files(self, env, capsys):
        with pytest.raises(SystemExit):
            process.find_raw_files()
        assert "No NEUZU" in capsys.readouterr().out

    def test_files_found(self, env):
        (env["raw"] / "NEUZU_2024.txt").write_text("header\n")
        (env["raw"] / "NEUZU_2023.txt").write_text("header\n")
        files = process.find_raw_files()
        assert len(files) == 2
        # sorted order
        assert files[0].name == "NEUZU_2023.txt"


# ---------------------------------------------------------------------------
# process_file — full columns, TSV
# ---------------------------------------------------------------------------

class TestProcessFileFullColumns:
    def test_basic_aggregation(self, env):
        filepath = env["raw"] / "NEUZU_test.txt"
        rows = [_full_row(), _full_row(Marke="BMW", Treibstoff="Benzin", Farbe="WEISS")]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)

        assert "monthly_totals" in agg
        assert "fuel_by_month" in agg
        assert "brand_by_year" in agg
        assert "canton_ev_by_month" in agg
        assert "brand_bev_by_month" in agg
        assert "brand_canton_bev" in agg
        assert "fuel_totals" in agg
        assert "brand_totals" in agg
        assert "origin_totals" in agg
        assert "continent_totals" in agg
        assert "group_totals" in agg
        assert "color_totals" in agg
        assert "usage_totals" in agg
        assert "drive_totals" in agg
        assert "drive_by_month" in agg

    def test_counts_correct(self, env):
        filepath = env["raw"] / "NEUZU_test.txt"
        rows = [_full_row(), _full_row(), _full_row()]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert agg["monthly_totals"]["count"].sum() == 3


# ---------------------------------------------------------------------------
# process_file — CSV separator
# ---------------------------------------------------------------------------

class TestProcessFileCSV:
    def test_csv_separator(self, env):
        filepath = env["raw"] / "NEUZU_csv.txt"
        rows = [_full_row()]
        _make_tsv(filepath, rows, sep=",")
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert "monthly_totals" in agg


# ---------------------------------------------------------------------------
# process_file — missing columns
# ---------------------------------------------------------------------------

class TestProcessFileMissingColumns:
    def test_missing_most_columns(self, env, capsys):
        """File with only Fahrzeugart and year/month columns."""
        filepath = env["raw"] / "NEUZU_sparse.txt"
        header = ["Fahrzeugart", "Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat"]
        rows = [{"Fahrzeugart": "Personenwagen", "Erstinverkehrsetzung_Jahr": "2024", "Erstinverkehrsetzung_Monat": "1"}]
        _make_tsv(filepath, rows, header=header)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert "monthly_totals" in agg
        assert "fuel_by_month" not in agg
        assert "brand_totals" not in agg
        out = capsys.readouterr().out
        assert "Missing columns" in out

    def test_no_year_month_columns(self, env):
        """File without year/month — _year/_month set to NA."""
        filepath = env["raw"] / "NEUZU_noyear.txt"
        header = ["Fahrzeugart", "Marke", "Treibstoff"]
        rows = [{"Fahrzeugart": "Personenwagen", "Marke": "TESLA", "Treibstoff": "Elektrisch"}]
        _make_tsv(filepath, rows, header=header)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        # No valid year/month -> no monthly aggregations
        assert "monthly_totals" not in agg
        # But totals still produced
        assert "fuel_totals" in agg
        assert "brand_totals" in agg


# ---------------------------------------------------------------------------
# process_file — column typo (Erstinvekehrsetzung_Kanton)
# ---------------------------------------------------------------------------

class TestProcessFileColumnTypo:
    def test_typo_fix(self, env, capsys):
        filepath = env["raw"] / "NEUZU_typo.txt"
        header = [
            "Fahrzeugart", "Marke", "Treibstoff", "Farbe", "Schildfarbe", "Antrieb",
            "Erstinverkehrsetzung_Jahr", "Erstinverkehrsetzung_Monat",
            "Erstinvekehrsetzung_Kanton",  # typo: missing 'r'
        ]
        rows = [{
            "Fahrzeugart": "Personenwagen",
            "Marke": "TESLA",
            "Treibstoff": "Elektrisch",
            "Farbe": "SCHWARZ",
            "Schildfarbe": "Weiss",
            "Antrieb": "Allrad",
            "Erstinverkehrsetzung_Jahr": "2024",
            "Erstinverkehrsetzung_Monat": "3",
            "Erstinvekehrsetzung_Kanton": "ZH",
        }]
        _make_tsv(filepath, rows, header=header)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        out = capsys.readouterr().out
        assert "Fixed column typos" in out
        # Canton aggregation should still work
        assert "canton_ev_by_month" in agg


# ---------------------------------------------------------------------------
# process_file — Datenstand column
# ---------------------------------------------------------------------------

class TestProcessFileDatenstand:
    def test_datenstand_extracted(self, env):
        filepath = env["raw"] / "NEUZU_ds.txt"
        header = list(_full_row().keys()) + ["Datenstand"]
        row = _full_row()
        row["Datenstand"] = "15.03.2024"
        _make_tsv(filepath, [row], header=header)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert agg["_datenstand"] == "2024-03-15"

    def test_datenstand_invalid_format(self, env):
        filepath = env["raw"] / "NEUZU_ds_bad.txt"
        header = list(_full_row().keys()) + ["Datenstand"]
        row = _full_row()
        row["Datenstand"] = "not-a-date"
        _make_tsv(filepath, [row], header=header)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert "_datenstand" not in agg

    def test_datenstand_short_row(self, env):
        """Datenstand column index exceeds actual row length."""
        filepath = env["raw"] / "NEUZU_ds_short.txt"
        # Header has Datenstand at end, but data row is too short
        header = list(_full_row().keys()) + ["Datenstand"]
        # Write header + a short data row manually
        lines = ["\t".join(header)]
        short_vals = list(_full_row().values())  # missing Datenstand value
        lines.append("\t".join(short_vals))
        filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        # Datenstand not extracted because row too short
        assert "_datenstand" not in agg


# ---------------------------------------------------------------------------
# process_file — no Personenwagen rows
# ---------------------------------------------------------------------------

class TestProcessFileNoPersonenwagen:
    def test_non_personenwagen_filtered_out(self, env, capsys):
        filepath = env["raw"] / "NEUZU_moto.txt"
        rows = [_full_row(Fahrzeugart="Motorrad"), _full_row(Fahrzeugart="Lieferwagen")]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert agg == {}
        assert "Personenwagen: 0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# process_file — warnings for unmapped values
# ---------------------------------------------------------------------------

class TestProcessFileWarnings:
    def test_unknown_fuel_brand_color(self, env):
        filepath = env["raw"] / "NEUZU_unk.txt"
        rows = [_full_row(Treibstoff="UnknownFuel", Marke="XYZMOTOR", Farbe="NEONPINK")]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        process.process_file(filepath, mappings, warnings)
        assert "fuel:UnknownFuel" in warnings
        assert "brand:XYZMOTOR" in warnings
        assert "color:NEONPINK" in warnings


# ---------------------------------------------------------------------------
# process_file — read error
# ---------------------------------------------------------------------------

class TestProcessFileReadError:
    def test_bad_file_returns_empty(self, env, capsys):
        """Simulate a file that pandas cannot parse (usecols mismatch)."""
        filepath = env["raw"] / "NEUZU_bad.txt"
        # Write a header with known columns but binary garbage as data
        filepath.write_bytes(b"Fahrzeugart\tMarke\n\xff\xfe\x00\x01\n")
        mappings = process.load_mappings()
        warnings = set()

        # This should trigger the except branch if pandas raises;
        # if pandas tolerates it, process_file still returns something valid.
        # We mainly verify no unhandled exception.
        agg = process.process_file(filepath, mappings, warnings)
        assert isinstance(agg, dict)


# ---------------------------------------------------------------------------
# process_file — BEV empty subset
# ---------------------------------------------------------------------------

class TestProcessFileNoBEV:
    def test_no_bev_rows_skips_bev_agg(self, env):
        """All rows are Petrol — brand_bev_by_month / brand_canton_bev absent."""
        filepath = env["raw"] / "NEUZU_nobev.txt"
        rows = [_full_row(Treibstoff="Benzin"), _full_row(Treibstoff="Diesel")]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        assert "brand_bev_by_month" not in agg
        assert "brand_canton_bev" not in agg


# ---------------------------------------------------------------------------
# consolidate_and_save — full aggregation dict
# ---------------------------------------------------------------------------

class TestConsolidateAndSaveFull:
    def test_writes_all_csvs(self, env, capsys):
        filepath = env["raw"] / "NEUZU_test.txt"
        rows = [_full_row(), _full_row(Marke="BMW", Treibstoff="Benzin")]
        _make_tsv(filepath, rows)
        mappings = process.load_mappings()
        warnings = set()

        agg = process.process_file(filepath, mappings, warnings)
        agg["_datenstand"] = "2024-03-15"

        process.consolidate_and_save(agg)

        out_dir = env["out"]
        assert (out_dir / "monthly_totals.csv").exists()
        assert (out_dir / "fuel_by_month.csv").exists()
        assert (out_dir / "brand_by_year.csv").exists()
        assert (out_dir / "fuel_totals.csv").exists()
        assert (out_dir / "brand_totals.csv").exists()
        assert (out_dir / "origin_totals.csv").exists()
        assert (out_dir / "continent_totals.csv").exists()
        assert (out_dir / "group_totals.csv").exists()
        assert (out_dir / "color_totals.csv").exists()
        assert (out_dir / "usage_totals.csv").exists()
        assert (out_dir / "drive_totals.csv").exists()
        assert (out_dir / "drive_by_month.csv").exists()
        assert (out_dir / "canton_ev_by_month.csv").exists()
        assert (out_dir / "brand_bev_by_month.csv").exists()
        assert (out_dir / "brand_canton_bev.csv").exists()

        # metadata.json
        meta_path = out_dir / "metadata.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["data_date"] == "2024-03-15"
        assert "metadata.json" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# consolidate_and_save — minimal / empty aggregation
# ---------------------------------------------------------------------------

class TestConsolidateAndSaveMinimal:
    def test_empty_agg(self, env, capsys):
        process.consolidate_and_save({})
        out = capsys.readouterr().out
        assert "Saved CSVs" in out
        # No metadata.json should be written
        assert not (env["out"] / "metadata.json").exists()

    def test_only_monthly_totals(self, env):
        agg = {
            "monthly_totals": pd.DataFrame({
                "_year": [2024, 2024],
                "_month": [1, 2],
                "count": [100, 200],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "monthly_totals.csv")
        assert list(df.columns) == ["year", "month", "count"]
        assert len(df) == 2


# ---------------------------------------------------------------------------
# consolidate_and_save — individual optional keys
# ---------------------------------------------------------------------------

class TestConsolidateAndSaveOptionalKeys:
    def test_canton_ev_by_month(self, env):
        agg = {
            "canton_ev_by_month": pd.DataFrame({
                "_canton": ["ZH"],
                "_year": [2024],
                "_month": [1],
                "ev_count": [50],
                "total_count": [200],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "canton_ev_by_month.csv")
        assert list(df.columns) == ["canton", "year", "month", "ev_count", "total_count"]

    def test_brand_bev_by_month(self, env):
        agg = {
            "brand_bev_by_month": pd.DataFrame({
                "_year": [2024],
                "_month": [1],
                "_brand": ["TESLA"],
                "bev_count": [100],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "brand_bev_by_month.csv")
        assert list(df.columns) == ["year", "month", "brand", "bev_count"]

    def test_brand_canton_bev(self, env):
        agg = {
            "brand_canton_bev": pd.DataFrame({
                "_canton": ["ZH"],
                "_brand": ["TESLA"],
                "_year": [2024],
                "_month": [1],
                "bev_count": [30],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "brand_canton_bev.csv")
        assert list(df.columns) == ["canton", "brand", "year", "month", "bev_count"]

    def test_drive_by_month(self, env):
        agg = {
            "drive_by_month": pd.DataFrame({
                "_year": [2024],
                "_month": [1],
                "_drive": ["4x4"],
                "count": [50],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "drive_by_month.csv")
        assert list(df.columns) == ["year", "month", "drive", "count"]

    def test_brand_by_year(self, env):
        agg = {
            "brand_by_year": pd.DataFrame({
                "_year": [2024],
                "_brand": ["BMW"],
                "count": [500],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "brand_by_year.csv")
        assert list(df.columns) == ["year", "brand", "count"]

    def test_fuel_by_month(self, env):
        agg = {
            "fuel_by_month": pd.DataFrame({
                "_year": [2024],
                "_month": [1],
                "_fuel": ["BEV"],
                "count": [300],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / "fuel_by_month.csv")
        assert list(df.columns) == ["year", "month", "fuel_type", "count"]


# ---------------------------------------------------------------------------
# consolidate_and_save — simple totals
# ---------------------------------------------------------------------------

class TestConsolidateAndSaveSimpleTotals:
    @pytest.mark.parametrize("key,col_name", [
        ("fuel_totals", "fuel_type"),
        ("brand_totals", "brand"),
        ("origin_totals", "country"),
        ("continent_totals", "continent"),
        ("group_totals", "group"),
        ("color_totals", "color"),
        ("usage_totals", "usage"),
        ("drive_totals", "drive"),
    ])
    def test_each_total(self, env, key, col_name):
        agg = {
            key: pd.DataFrame({
                col_name: ["A", "B"],
                "count": [100, 200],
            }),
        }
        process.consolidate_and_save(agg)
        df = pd.read_csv(env["out"] / f"{key}.csv")
        assert col_name in df.columns
        assert "count" in df.columns


# ---------------------------------------------------------------------------
# consolidate_and_save — metadata only when _datenstand present
# ---------------------------------------------------------------------------

class TestConsolidateAndSaveMetadata:
    def test_no_datenstand_no_metadata(self, env):
        process.consolidate_and_save({"monthly_totals": pd.DataFrame({
            "_year": [2024], "_month": [1], "count": [10],
        })})
        assert not (env["out"] / "metadata.json").exists()

    def test_datenstand_writes_metadata(self, env, capsys):
        process.consolidate_and_save({"_datenstand": "2024-06-01"})
        meta = json.loads((env["out"] / "metadata.json").read_text())
        assert meta["data_date"] == "2024-06-01"
        assert "metadata.json" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# save_warnings
# ---------------------------------------------------------------------------

class TestSaveWarnings:
    def test_empty_warnings(self, env, capsys):
        process.save_warnings(set())
        assert "No unmapped values" in capsys.readouterr().out
        assert not env["warnings_file"].exists()

    def test_non_empty_warnings(self, env, capsys):
        process.save_warnings({"fuel:Unknown", "brand:XYZ"})
        assert env["warnings_file"].exists()
        content = env["warnings_file"].read_text()
        assert "fuel:Unknown" in content
        assert "brand:XYZ" in content
        out = capsys.readouterr().out
        assert "Unmapped: 2 values" in out
        assert "validate.py" in out


# ---------------------------------------------------------------------------
# main — orchestration
# ---------------------------------------------------------------------------

class TestMain:
    def test_full_pipeline(self, env, capsys):
        filepath = env["raw"] / "NEUZU_2024.txt"
        rows = [_full_row(), _full_row(Treibstoff="UnknownFuel")]
        _make_tsv(filepath, rows)

        process.main()

        out = capsys.readouterr().out
        assert "ASTRA Data Processing" in out
        assert "Done." in out
        assert (env["out"] / "monthly_totals.csv").exists()
        # Warnings file written (unknown fuel)
        assert env["warnings_file"].exists()

    def test_multiple_files_merged(self, env, capsys):
        f1 = env["raw"] / "NEUZU_2023.txt"
        f2 = env["raw"] / "NEUZU_2024.txt"
        rows_2023 = [_full_row(Erstinverkehrsetzung_Jahr="2023")]
        rows_2024 = [_full_row(Erstinverkehrsetzung_Jahr="2024")]
        _make_tsv(f1, rows_2023)
        _make_tsv(f2, rows_2024)

        process.main()

        df = pd.read_csv(env["out"] / "monthly_totals.csv")
        years = set(df["year"].unique())
        assert 2023 in years
        assert 2024 in years


# ---------------------------------------------------------------------------
# __name__ == "__main__" guard
# ---------------------------------------------------------------------------

class TestMainGuard:
    def test_run_as_main(self, tmp_path, monkeypatch):
        """Cover the ``if __name__ == '__main__'`` block."""
        monkeypatch.setattr(process, "RAW_DIR", tmp_path / "raw")
        monkeypatch.setattr(process, "OUT_DIR", tmp_path / "out")
        monkeypatch.setattr(process, "WARNINGS_FILE", tmp_path / "w.log")
        monkeypatch.setattr(process, "MAPPINGS_FILE", tmp_path / "m.yaml")
        (tmp_path / "m.yaml").write_text(yaml.dump({"fuel_types": {}}))
        raw = tmp_path / "raw"
        raw.mkdir()
        (raw / "NEUZU-test.txt").write_text("Fahrzeugart\nPersonenwagen\n")
        out = tmp_path / "out"
        out.mkdir()
        source = Path(process.__file__).read_text()
        code = compile(source, process.__file__, "exec")
        with patch("builtins.print"):
            exec(code, {"__name__": "__main__", "__file__": process.__file__})

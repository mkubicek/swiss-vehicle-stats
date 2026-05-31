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
# normalize_model
# ---------------------------------------------------------------------------

def _sort_overrides(overrides: dict) -> list[tuple[str, str]]:
    """Mirror process.process_file's overrides_sorted construction."""
    return sorted(
        ((k.upper(), v) for k, v in overrides.items()),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )


class TestNormalizeModel:
    def test_empty_inputs_return_empty(self):
        assert process.normalize_model("", "TIGUAN", []) == ""
        assert process.normalize_model("VW", "", []) == ""
        assert process.normalize_model("   ", "TIGUAN", []) == ""

    def test_punctuation_only_typ1_returns_empty(self):
        # Punctuation-only Typ1 strips to nothing — must not emit a malformed
        # "BRAND " key (brand + trailing space + empty model).
        assert process.normalize_model("JEEP", ".", []) == ""
        assert process.normalize_model("FIAT", ",", []) == ""
        assert process.normalize_model("VW", ".,", []) == ""

    def test_nan_string_inputs_return_empty(self):
        # df["col"].astype(str) renders NaN as the literal string "nan".
        assert process.normalize_model("nan", "TIGUAN", []) == ""
        assert process.normalize_model("VW", "nan", []) == ""

    def test_non_string_inputs_return_empty(self):
        assert process.normalize_model(None, "TIGUAN", []) == ""
        assert process.normalize_model("VW", None, []) == ""
        assert process.normalize_model(123, "TIGUAN", []) == ""

    def test_default_rule_brand_plus_first_token(self):
        assert process.normalize_model("VW", "TIGUAN 2.0 TSI 4M", []) == "VW TIGUAN"
        assert process.normalize_model("SKODA", "OCTAVIA 2.0TDI 4X4", []) == "SKODA OCTAVIA"
        assert process.normalize_model("DACIA", "Sandero", []) == "DACIA SANDERO"

    def test_default_rule_strips_trailing_punctuation(self):
        assert process.normalize_model("BMW", "X1,", []) == "BMW X1"
        assert process.normalize_model("AUDI", "Q3.", []) == "AUDI Q3"

    def test_tesla_keeps_model_designator(self):
        # ASTRA writes Tesla models inconsistently across years.
        assert process.normalize_model("TESLA", "Model Y", []) == "TESLA MODEL Y"
        assert process.normalize_model("TESLA", "MODELY", []) == "TESLA MODEL Y"
        assert process.normalize_model("TESLA", "Model 3 LongRange", []) == "TESLA MODEL 3"
        assert process.normalize_model("TESLA", "MODEL3", []) == "TESLA MODEL 3"
        assert process.normalize_model("TESLA", "Model S", []) == "TESLA MODEL S"
        assert process.normalize_model("TESLA", "Model X Plaid", []) == "TESLA MODEL X"

    def test_vw_id_family_keeps_number(self):
        assert process.normalize_model("VW", "ID.3 PRO 150 KW", []) == "VW ID.3"
        assert process.normalize_model("VW", "ID.3PROS150KW", []) == "VW ID.3"
        assert process.normalize_model("VW", "ID4", []) == "VW ID.4"
        assert process.normalize_model("VW", "ID.7 TOURER", []) == "VW ID.7"
        assert process.normalize_model("VW", "ID. BUZZ GTX", []) == "VW ID.BUZZ"

    def test_override_longest_prefix_wins(self):
        overrides = _sort_overrides({
            "TOYOTA YARIS CROSS": "Toyota Yaris Cross",
            "TOYOTA YARISCROSS": "Toyota Yaris Cross",
        })
        # "YARIS CROSS HYBRID" matches the longer prefix → override applies.
        assert process.normalize_model("TOYOTA", "YARIS CROSS HYBRID", overrides) == "Toyota Yaris Cross"
        # Concatenated spelling aliases to the same canonical.
        assert process.normalize_model("TOYOTA", "YARISCROSS", overrides) == "Toyota Yaris Cross"
        # Plain Yaris still falls through to the default rule.
        assert process.normalize_model("TOYOTA", "YARIS HYBRID", overrides) == "TOYOTA YARIS"

    def test_override_exact_match(self):
        overrides = _sort_overrides({"MITSUBISHI SPACE STAR": "Mitsubishi Space Star"})
        assert process.normalize_model("MITSUBISHI", "SPACE STAR", overrides) == "Mitsubishi Space Star"
        # Without override, first-token-only rule would mis-merge as "SPACE".
        assert process.normalize_model("MITSUBISHI", "SPACE STAR", []) == "MITSUBISHI SPACE"

    def test_toyota_gr_overrides_split_sub_brand_models(self):
        overrides = _sort_overrides({
            "TOYOTA GR YARIS": "Toyota GR Yaris",
            "TOYOTA GR86": "Toyota GR86",
            "TOYOTA GR 86": "Toyota GR86",
            "TOYOTA GR COROLLA": "Toyota GR Corolla",
        })
        assert process.normalize_model("TOYOTA", "GR YARIS CIRCUIT", overrides) == "Toyota GR Yaris"
        assert process.normalize_model("TOYOTA", "GR86", overrides) == "Toyota GR86"
        assert process.normalize_model("TOYOTA", "GR 86", overrides) == "Toyota GR86"
        assert process.normalize_model("TOYOTA", "GR COROLLA", overrides) == "Toyota GR Corolla"

    def test_unknown_brand_uses_default_rule(self):
        # No specials for non-Tesla / non-VW brands.
        assert process.normalize_model("FAKE", "Model Y", []) == "FAKE MODEL"
        assert process.normalize_model("FAKE", "ID.3", []) == "FAKE ID.3"

    def test_strips_duplicate_brand_prefix_before_normalizing(self):
        # Some ASTRA Typ1 values repeat Marke before the actual model. The
        # duplicate must not become keys like "AUDI AUDI" / "POLESTAR POLESTAR".
        assert process.normalize_model("POLESTAR", "POLESTAR 2", []) == "POLESTAR 2"
        assert process.normalize_model("AUDI", "AUDI RS6 AVANT", []) == "AUDI A6"
        assert process.normalize_model("HONDA", "HONDA E", []) == "HONDA E"
        assert process.normalize_model("CUPRA", "CUPRA ATECA", []) == "Cupra Ateca"
        assert process.normalize_model("LAND ROVER", "LAND-ROVER DEFENDER", []) == "LAND ROVER DEFENDER"

    def test_strips_concatenated_engine_codes(self):
        # ASTRA pre-2022 records often concatenate the trim/engine code onto
        # the model name without a space. Strip the engine code so those rows
        # aggregate into the real nameplate.
        assert process.normalize_model("SKODA", "OCTAVIA2.0TDI", []) == "SKODA OCTAVIA"
        assert process.normalize_model("SKODA", "OCTAVIA2.0TSI", []) == "SKODA OCTAVIA"
        assert process.normalize_model("HYUNDAI", "TUCSON1.6TGDIPHEV", []) == "HYUNDAI TUCSON"
        assert process.normalize_model("SUZUKI", "VITARA1.6TDI", []) == "SUZUKI VITARA"
        assert process.normalize_model("SEAT", "ALHAMBRA2.0TDI", []) == "SEAT ALHAMBRA"
        # Pure-decimal forms (no letters after the digits) also strip.
        assert process.normalize_model("SUZUKI", "VITARA1.5", []) == "SUZUKI VITARA"
        assert process.normalize_model("HYUNDAI", "TUCSON1.6", []) == "HYUNDAI TUCSON"

    def test_does_not_strip_model_designators(self):
        # The conservative regex (requires DECIMAL engine code) must NOT
        # over-strip short alphanumeric model designators.
        assert process.normalize_model("BMW", "X1", []) == "BMW X1"
        assert process.normalize_model("BMW", "X5 M50", []) == "BMW X5"
        assert process.normalize_model("AUDI", "Q3", []) == "AUDI Q3"
        assert process.normalize_model("AUDI", "A4 Avant", []) == "AUDI A4"
        assert process.normalize_model("FIAT", "500", []) == "FIAT 500"
        assert process.normalize_model("FORD", "C-MAX", []) == "FORD C-MAX"
        assert process.normalize_model("VW", "T-ROC", []) == "VW T-ROC"


class TestNormalizeModelBrandRules:
    """Brand-specific specials that collapse trims/performance onto the base
    model so segments compare across makers (BMW series, AMG/RS fold, etc.)."""

    def test_bmw_collapses_trims_and_m_cars_to_series(self):
        for typ1, exp in [
            ("320D xDrive", "BMW 3 Series"), ("330E", "BMW 3 Series"),
            ("M340I", "BMW 3 Series"), ("M3 Competition", "BMW 3 Series"),
            ("M3COMPETITIONMXDRIVE", "BMW 3 Series"),
            ("118i", "BMW 1 Series"), ("M135i", "BMW 1 Series"),
            ("520d", "BMW 5 Series"), ("M5", "BMW 5 Series"),
        ]:
            assert process.normalize_model("BMW", typ1, []) == exp

    def test_bmw_suv_electric_roadster_stay_distinct(self):
        assert process.normalize_model("BMW", "X3 M40i", []) == "BMW X3"
        assert process.normalize_model("BMW", "X3M40I", []) == "BMW X3"
        assert process.normalize_model("BMW", "X1XDRIVE20D", []) == "BMW X1"
        assert process.normalize_model("BMW", "X5MCOMPETITION", []) == "BMW X5"
        assert process.normalize_model("BMW", "X1", []) == "BMW X1"
        assert process.normalize_model("BMW", "iX3", []) == "BMW IX3"
        assert process.normalize_model("BMW", "i4 eDrive40", []) == "BMW I4"
        assert process.normalize_model("BMW", "i3s", []) == "BMW I3"  # trim folds to i3
        assert process.normalize_model("BMW", "Z4", []) == "BMW Z4"

    def test_mercedes_amg_folds_to_base_class(self):
        assert process.normalize_model("MERCEDES-BENZ", "AMG C 63 S E P", []) == "MERCEDES-BENZ C"
        assert process.normalize_model("MERCEDES-BENZ", "AMG GLC 43 4MA", []) == "MERCEDES-BENZ GLC"
        assert process.normalize_model("MERCEDES-BENZ", "AMG A 35 4MATI", []) == "MERCEDES-BENZ A"
        # AMG recorded as its own Marke folds in too; standalone GT keeps "GT".
        assert process.normalize_model("MERCEDES-AMG", "AMG GT 63 4MATI", []) == "MERCEDES-BENZ GT"

    def test_mercedes_strips_engine_digits_to_alpha_base(self):
        assert process.normalize_model("MERCEDES-BENZ", "GLA200", []) == "MERCEDES-BENZ GLA"
        assert process.normalize_model("MERCEDES-BENZ", "CLA250", []) == "MERCEDES-BENZ CLA"
        assert process.normalize_model("MERCEDES-BENZ", "C 220 d", []) == "MERCEDES-BENZ C"
        assert process.normalize_model("MERCEDES-BENZ", "V250d", []) == "MERCEDES-BENZ V"
        assert process.normalize_model("MERCEDES-BENZ", "GLC 300", []) == "MERCEDES-BENZ GLC"

    def test_audi_rs_s_fold_to_base_line(self):
        assert process.normalize_model("AUDI", "RS 3 Sportback", []) == "AUDI A3"
        assert process.normalize_model("AUDI", "RS 6 Avant", []) == "AUDI A6"
        assert process.normalize_model("AUDI", "RS Q8", []) == "AUDI Q8"
        assert process.normalize_model("AUDI", "S3", []) == "AUDI A3"
        assert process.normalize_model("AUDI", "SQ5", []) == "AUDI Q5"
        assert process.normalize_model("AUDI", "TTS", []) == "AUDI TT"
        assert process.normalize_model("AUDI", "Q445E-TRON", []) == "AUDI Q4"  # concatenated

    def test_audi_standalone_sports_and_etron_gt_distinct(self):
        assert process.normalize_model("AUDI", "R8 V10", []) == "AUDI R8"
        assert process.normalize_model("AUDI", "RS e-tron GT", []) == "AUDI E-TRON GT"
        assert process.normalize_model("AUDI", "e-tron 55", []) == "AUDI E-TRON"  # SUV, distinct

    def test_mini_body_style_and_trim_names_fold_to_nameplates(self):
        assert process.normalize_model("MINI", "3DOOR COOPER S", []) == "MINI COOPER"
        assert process.normalize_model("MINI", "5DOOR ONE", []) == "MINI COOPER"
        assert process.normalize_model("MINI", "COOPER S", []) == "MINI COOPER"
        assert process.normalize_model("MINI", "COOPER S CLUBMAN", []) == "MINI CLUBMAN"
        assert process.normalize_model("MINI", "COUNTRYMAN SE ALL4", []) == "MINI COUNTRYMAN"
        assert process.normalize_model("MINI", "ACEMAN SE", []) == "MINI ACEMAN"

    def test_lexus_strips_hybrid_trim_to_alpha_base(self):
        assert process.normalize_model("LEXUS", "RX450H", []) == "LEXUS RX"
        assert process.normalize_model("LEXUS", "NX450H+", []) == "LEXUS NX"
        assert process.normalize_model("LEXUS", "UX250H", []) == "LEXUS UX"

    def test_ds_number_is_the_model(self):
        assert process.normalize_model("DS", "DS7 Crossback", []) == "DS DS7"
        assert process.normalize_model("DS", "DS 7 E-TENSE4X4", []) == "DS DS7"
        assert process.normalize_model("DS", "7CRB.E-TENSE4X4", []) == "DS DS7"
        assert process.normalize_model("DS", "DS7CRB.E-TENSE4X4", []) == "DS DS7"

    def test_land_rover_range_rover_family_splits_by_submodel(self):
        # The "RR ..." bucket spans three segments — split it, don't lump.
        assert process.normalize_model("LAND ROVER", "RR EVOQUE", []) == "LAND ROVER EVOQUE"
        assert process.normalize_model("LAND ROVER", "RR VELAR", []) == "LAND ROVER VELAR"
        assert process.normalize_model("LAND ROVER", "RR SPORT 3.0", []) == "LAND ROVER RANGE ROVER SPORT"
        assert process.normalize_model("LAND ROVER", "RRSPORT", []) == "LAND ROVER RANGE ROVER SPORT"
        # ASTRA abbreviates Sport as "RR SP." — must not fall through to full-size.
        assert process.normalize_model("LAND ROVER", "RR SP.3.0SDV6", []) == "LAND ROVER RANGE ROVER SPORT"
        assert process.normalize_model("LAND ROVER", "RR SP. SI4 PHEV", []) == "LAND ROVER RANGE ROVER SPORT"
        assert process.normalize_model("LAND ROVER", "RR", []) == "LAND ROVER RANGE ROVER"
        assert process.normalize_model("LAND ROVER", "RR 3.0 TDV6", []) == "LAND ROVER RANGE ROVER"
        assert process.normalize_model("LAND ROVER", "RANGE ROVER", []) == "LAND ROVER RANGE ROVER"
        # Non-Range-Rover Land Rovers are untouched by this rule.
        assert process.normalize_model("LAND ROVER", "DEFENDER 110", []) == "LAND ROVER DEFENDER"

    def test_audi_q8_etron_does_not_merge_into_combustion_q8(self):
        # The electric Q8 e-tron (renamed original e-tron) must stay out of the
        # combustion Q8 key. Q4/Q6 e-tron are EV-only and keep their Q key.
        assert process.normalize_model("AUDI", "Q8 55 E-TRON", []) == "AUDI E-TRON"
        assert process.normalize_model("AUDI", "SQ8 E-TRON", []) == "AUDI E-TRON"
        assert process.normalize_model("AUDI", "E-TRON 55 QU", []) == "AUDI E-TRON"
        assert process.normalize_model("AUDI", "Q8 50 TDI", []) == "AUDI Q8"      # combustion
        assert process.normalize_model("AUDI", "SQ8 TDI", []) == "AUDI Q8"        # combustion perf
        assert process.normalize_model("AUDI", "Q4 45 E-TRON", []) == "AUDI Q4"   # EV-only, keeps Q

    def test_mercedes_strips_stray_z_prefix(self):
        # Some AMG rows carry a stray "Z " prefix that would corrupt the class key.
        assert process.normalize_model("MERCEDES-BENZ", "Z AMG G 63", []) == "MERCEDES-BENZ G"
        assert process.normalize_model("MERCEDES-BENZ", "Z AMG GLS 63 4MA", []) == "MERCEDES-BENZ GLS"

    def test_porsche_collapses_concatenated_trims(self):
        assert process.normalize_model("PORSCHE", "911CARRERA 4S", []) == "PORSCHE 911"
        assert process.normalize_model("PORSCHE", "718SPYDERRS", []) == "PORSCHE 718"
        assert process.normalize_model("PORSCHE", "TAYCAN4S", []) == "PORSCHE TAYCAN"
        assert process.normalize_model("PORSCHE", "CAYENNET", []) == "PORSCHE CAYENNE"
        assert process.normalize_model("PORSCHE", "PANAMERATURBOE-HYBRID", []) == "PORSCHE PANAMERA"

    def test_cupra_folds_to_nameplate_across_eras(self):
        # CUPRA-era rows (Marke CUPRA) must land on the SAME proper-case label as
        # the SEAT-era model_overrides, so a model isn't split across two keys
        # ("CUPRA FORMENTOR" vs "Cupra Formentor"). Also folds concat trims.
        assert process.normalize_model("CUPRA", "FORMENTOR", []) == "Cupra Formentor"
        assert process.normalize_model("CUPRA", "CUPRA FORMENTOR", []) == "Cupra Formentor"
        assert process.normalize_model("CUPRA", "FORMENTORE-HYBRID", []) == "Cupra Formentor"
        assert process.normalize_model("CUPRA", "BORN170KW77/82KWH", []) == "Cupra Born"
        assert process.normalize_model("CUPRA", "LEONSP", []) == "Cupra Leon"
        assert process.normalize_model("CUPRA", "ATECA", []) == "Cupra Ateca"


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
# model_merges (applied after normalize_model in process_file)
# ---------------------------------------------------------------------------

class TestModelMerges:
    def test_collapses_concatenation_fragments(self, tmp_path):
        """OCTAVIAC (combi spelling) and OCTAVIA must land on one model key.

        normalize_model alone splits them ("SKODA OCTAVIA" vs "SKODA OCTAVIAC");
        model_merges collapses both onto the canonical label.
        """
        filepath = tmp_path / "NEUZU_merge.txt"
        rows = [
            {**_full_row(Marke="SKODA"), "Typ1": "OCTAVIA 2.0 TDI"},
            {**_full_row(Marke="SKODA"), "Typ1": "OCTAVIAC1.6TDI"},
        ]
        _make_tsv(filepath, rows)
        mappings = {**MINIMAL_MAPPINGS, "model_merges": {
            "SKODA OCTAVIA": "Skoda Octavia",
            "SKODA OCTAVIAC": "Skoda Octavia",
        }}

        agg = process.process_file(filepath, mappings, set())

        models = set(agg["model_by_month"]["_model"])
        assert models == {"Skoda Octavia"}
        assert agg["model_by_month"]["count"].sum() == 2

    def test_merges_prettied_override_values_case_insensitively(self, tmp_path):
        """model_overrides emits proper-case labels; model_merges keys are
        normalized to uppercase internally, so lookup must uppercase the value.
        """
        filepath = tmp_path / "NEUZU_override_merge.txt"
        rows = [
            {**_full_row(Marke="TOYOTA"), "Typ1": "GR YARIS"},
            {**_full_row(Marke="TOYOTA"), "Typ1": "GR YARIS CIRCUIT"},
        ]
        _make_tsv(filepath, rows)
        mappings = {**MINIMAL_MAPPINGS,
            "model_overrides": {"TOYOTA GR YARIS": "Toyota GR Yaris"},
            "model_merges": {"Toyota GR Yaris": "Toyota GR Yaris Canonical"},
        }

        agg = process.process_file(filepath, mappings, set())

        assert set(agg["model_by_month"]["_model"]) == {"Toyota GR Yaris Canonical"}
        assert agg["model_by_month"]["count"].sum() == 2

    def test_seat_cupra_history_rebrands_to_cupra_model_brand(self, tmp_path):
        filepath = tmp_path / "NEUZU_seat_cupra.txt"
        rows = [
            {**_full_row(Marke="SEAT"), "Typ1": "CUPRA ATECA R"},
            {**_full_row(Marke="SEAT"), "Typ1": "CUPRA LEON EHYB"},
        ]
        _make_tsv(filepath, rows)
        mappings = {**MINIMAL_MAPPINGS,
            "model_overrides": {
                "SEAT CUPRA ATECA": "Cupra Ateca",
                "SEAT CUPRA LEON": "Cupra Leon",
            },
            "model_brand_overrides": {
                "Cupra Ateca": "CUPRA",
                "Cupra Leon": "CUPRA",
            },
            "model_segments": {
                "Cupra Ateca": "Compact SUV",
                "Cupra Leon": "Compact",
            },
        }

        agg = process.process_file(filepath, mappings, set())

        rows = agg["model_by_month"].sort_values("_model")
        assert rows["_model"].tolist() == ["Cupra Ateca", "Cupra Leon"]
        assert rows["_brand"].tolist() == ["CUPRA", "CUPRA"]
        assert rows["_segment"].tolist() == ["Compact SUV", "Compact"]

    def test_mercedes_mpv_aliases_merge_to_canonical_models(self, tmp_path):
        filepath = tmp_path / "NEUZU_mpv.txt"
        rows = [
            {**_full_row(Marke="MERCEDES-BENZ"), "Typ1": "MPH 300D 4M"},
            {**_full_row(Marke="MERCEDES-BENZ"), "Typ1": "MARCO POLO"},
            {**_full_row(Marke="MERCEDES-BENZ"), "Typ1": "VITOTOURER"},
        ]
        _make_tsv(filepath, rows)
        mappings = {**MINIMAL_MAPPINGS,
            "model_merges": {
                "MERCEDES-BENZ MPH": "Mercedes-Benz Marco Polo",
                "MERCEDES-BENZ MARCO": "Mercedes-Benz Marco Polo",
                "MERCEDES-BENZ VITOTOURER": "Mercedes-Benz Vito",
            },
            "model_segments": {
                "Mercedes-Benz Marco Polo": "MPV",
                "Mercedes-Benz Vito": "MPV",
            },
        }

        agg = process.process_file(filepath, mappings, set())

        totals = agg["model_by_month"].groupby(["_model", "_segment"])["count"].sum()
        assert totals[("Mercedes-Benz Marco Polo", "MPV")] == 2
        assert totals[("Mercedes-Benz Vito", "MPV")] == 1

    def test_no_merges_leaves_keys_untouched(self, tmp_path):
        filepath = tmp_path / "NEUZU_nomerge.txt"
        rows = [{**_full_row(Marke="SKODA"), "Typ1": "OCTAVIAC1.6TDI"}]
        _make_tsv(filepath, rows)
        mappings = {**MINIMAL_MAPPINGS}  # no model_merges key

        agg = process.process_file(filepath, mappings, set())

        assert set(agg["model_by_month"]["_model"]) == {"SKODA OCTAVIAC"}


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
# add_model_mapping_warnings
# ---------------------------------------------------------------------------

class TestModelMappingWarnings:
    def test_warns_for_watched_high_volume_other_models(self):
        agg = {"model_by_month": pd.DataFrame([
            {"_brand": "MINI", "_model": "MINI 3DOOR", "_segment": "Other", "count": 400},
            {"_brand": "MINI", "_model": "MINI 3DOOR", "_segment": "Other", "count": 200},
            {"_brand": "MINI", "_model": "MINI COOPER", "_segment": "City / Supermini", "count": 999},
            {"_brand": "SKODA", "_model": "SKODA OCTAVIA", "_segment": "Other", "count": 9999},
            {"_brand": "BMW", "_model": "BMW LOW", "_segment": "Other", "count": 499},
            {"_brand": "BMW", "_model": "BMW IGNORE", "_segment": "Other", "count": 999},
        ])}
        mappings = {"model_mapping_warnings": {
            "min_count": 500,
            "brands": ["MINI", "BMW"],
            "ignore_models": ["BMW IGNORE"],
        }}
        warnings = set()

        process.add_model_mapping_warnings(agg, mappings, warnings)

        assert warnings == {"model_segment:MINI:MINI 3DOOR:600"}

    def test_no_config_or_missing_model_data_is_noop(self):
        warnings = set()
        process.add_model_mapping_warnings({}, {}, warnings)
        assert warnings == set()


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

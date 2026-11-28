import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dc_centroid_gate_qualification as qualification


def test_global_centroid_gate_rejects_a_harmless_sub20_contaminant():
    result = qualification.case()
    assert abs(result["global_shift_pct"]) >= 15.6
    assert abs(result["protected_shift_pct"]) < 0.01


def test_the_qualified_band_excludes_the_contaminant_without_hiding_a_1khz_change():
    result = qualification.case()
    assert abs(result["protected_before_hz"] - 1000.0) < 0.01
    assert abs(result["protected_after_hz"] - 1000.0) < 0.01


# ---------------------------------------------------------------------------
# the record is written when a gate DISAGREES (#2144)
#
# RED FIRST on main @ fc6bb4f3 (2026-11-28): both `not_qualified` cases exited
# 1 and left no file at all, and the qualified record had no `qualified` key.
# The write sat below the two gate returns, so a reader of the file could not
# tell "not qualified, here is the measurement that says so" from "nothing ran".
# ---------------------------------------------------------------------------


def _force(gate, monkeypatch):
    """Make exactly one of main()'s gates disagree with a real measurement."""
    if gate == "global":
        monkeypatch.setattr(qualification, "TARGET_SHIFT_PCT", 90.0)
        return
    real_case = qualification.case

    def protected_band_moved(*args, **kwargs):
        return {**real_case(*args, **kwargs), "protected_shift_pct": 0.5}

    monkeypatch.setattr(qualification, "case", protected_band_moved)


def test_a_qualified_run_records_that_it_is_qualified(tmp_path):
    target = tmp_path / "record.json"
    assert qualification.main(["--json", str(target)]) == 0
    record = json.loads(target.read_text())
    assert record["qualified"] is True
    assert {k: v for k, v in record.items() if k != "qualified"} == \
        pytest.approx(qualification.case())


@pytest.mark.parametrize("gate", ["global", "protected"])
def test_a_not_qualified_run_still_writes_its_record(tmp_path, monkeypatch,
                                                     gate):
    measured = qualification.case()
    _force(gate, monkeypatch)
    target = tmp_path / "record.json"
    assert qualification.main(["--json", str(target)]) == 1
    assert target.is_file(), f"the {gate} gate disagreed and left no record"
    record = json.loads(target.read_text())
    assert record["qualified"] is False
    if gate == "protected":
        measured["protected_shift_pct"] = 0.5
    assert {k: v for k, v in record.items() if k != "qualified"} == \
        pytest.approx(measured)

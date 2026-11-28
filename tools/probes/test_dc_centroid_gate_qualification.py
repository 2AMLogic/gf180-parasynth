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
# the record's destination (#2100, a slice of #2040's class)
#
# RED FIRST on main @ 8eb04e5b (2026-11-28), through the real CLI:
# `--json no/such/dir/a.json` exited 1 on `FileNotFoundError` after the case
# had qualified -- the status of "measured and disagreed", for a pass whose
# record was lost. This probe had no refusal status at all (0 or 1 only), so
# the destination refusals below are the repository's REFUSED, 2.
# ---------------------------------------------------------------------------

def _nothing(*_a, **_k):
    raise AssertionError("the case ran: a refusal must happen BEFORE anything is measured")


def _unwritable(tmp_path, kind):
    """A directory given as the file, or a parent that is a regular file."""
    if kind == "a directory":
        target = tmp_path / "record.json"
        target.mkdir()
        return target
    (tmp_path / "not-a-dir").write_text("a file")
    return tmp_path / "not-a-dir" / "record.json"


def test_a_record_under_a_missing_directory_is_written(tmp_path, capsys):
    target = tmp_path / "no" / "such" / "dir" / "record.json"
    assert qualification.main(["--json", str(target)]) == 0
    assert json.loads(target.read_text()) == pytest.approx(qualification.case())
    assert "global centroid:" in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["a directory", "a parent that is a file"])
def test_an_unwritable_destination_is_refused_before_the_case_runs(tmp_path, capsys, monkeypatch, kind):
    target = _unwritable(tmp_path, kind)
    monkeypatch.setattr(qualification, "case", _nothing)
    assert qualification.main(["--json", str(target)]) == 2
    out = capsys.readouterr()
    assert "REFUSED" in out.out and str(target) in out.out, out.out
    assert "global centroid:" not in out.out, "a refused run must not report a measurement"


def test_a_write_failure_after_the_case_is_refused_with_the_verdict_printed(tmp_path, capsys, monkeypatch):
    """The pre-flight passed and the destination became unwritable before the
    write -- what a disk filling or a permission changing mid-run looks like."""
    target = tmp_path / "record.json"
    real_case = qualification.case

    def case_then_occupy(*a, **k):
        result = real_case(*a, **k)
        target.mkdir()
        return result

    monkeypatch.setattr(qualification, "case", case_then_occupy)
    assert qualification.main(["--json", str(target)]) == 2
    out = capsys.readouterr()
    assert "global centroid:" in out.out, "the verdict must still be printed"
    assert "REFUSED" in out.out and str(target) in out.out, out.out
    assert target.is_dir()

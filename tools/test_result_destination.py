"""`run_case.result_destination`: a model result must not replace an anchor.

The failure this guards against is cheap to cause and expensive to notice.
`make board` runs `run_case.py --batch "First 32"`, which scores every case with
the integer models; the board has one result path per case; so a single `make
board` after an integrated-rtl anchor lands would replace a measurement of the
chip with a measurement of the model of the chip and the board would go back to
saying the instrument has never been measured -- with nothing in the diff
naming what happened.
"""
import json

import run_case as rc


def _record(engine, *, commit="cafe1234"):
    return {"engine": engine, "case_id": "E1A", "source_commit": commit,
            "metrics": {}, "provenance": {}}


def test_a_fresh_destination_is_used_as_is(tmp_path):
    dest = tmp_path / "E1A.json"
    assert rc.result_destination(dest, _record("fixed-model")) == dest


def test_a_model_result_does_not_replace_an_integrated_rtl_anchor(tmp_path, capsys):
    dest = tmp_path / "E1A.json"
    dest.write_text(json.dumps(_record("integrated-rtl", commit="anchor01")))
    got = rc.result_destination(dest, _record("fixed-model"))
    assert got != dest
    assert got.parent.name == rc.MODEL_TWIN_DIR and got.name == "E1A.json"
    assert got.parent.is_dir()
    # the anchor is still exactly what it was
    assert json.loads(dest.read_text())["engine"] == "integrated-rtl"
    said = capsys.readouterr().out
    assert "integrated-rtl" in said and "rather than replacing it" in said


def test_an_anchor_may_replace_an_anchor(tmp_path):
    dest = tmp_path / "E1A.json"
    dest.write_text(json.dumps(_record("integrated-rtl", commit="old")))
    assert rc.result_destination(dest, _record("integrated-rtl", commit="new")) == dest


def test_a_model_result_replaces_a_model_result(tmp_path):
    dest = tmp_path / "E1A.json"
    dest.write_text(json.dumps(_record("fixed-model")))
    assert rc.result_destination(dest, _record("fixed-model")) == dest


def test_an_unreadable_record_is_not_treated_as_an_anchor(tmp_path):
    """A corrupt file must not become an unremovable block on re-measuring a
    case: `tools/scorecard.py` already reports it as a no-verdict."""
    dest = tmp_path / "E1A.json"
    dest.write_text("{not json")
    assert rc.result_destination(dest, _record("fixed-model")) == dest

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

import pytest

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


def test_a_model_result_does_not_overwrite_a_corrupt_record(tmp_path):
    """#610: a damaged file may be an anchor; fall-back-to-dest overwrote it."""
    dest = tmp_path / "E1A.json"
    dest.write_text("{not json")
    with pytest.raises(rc.Refused, match="unreadable"):
        rc.result_destination(dest, _record("fixed-model"))
    assert dest.read_text() == "{not json"


def test_an_anchor_result_selects_dest_even_over_a_corrupt_record(tmp_path):
    """Destination SELECTION only. This does not mean an anchor repairs a
    corrupt record: the write path refuses it one step later, in
    `carry_rubric_history` (see the write-path controls below)."""
    dest = tmp_path / "E1A.json"
    dest.write_text("{not json")
    assert rc.result_destination(dest, _record("integrated-rtl")) == dest
    assert dest.read_text() == "{not json"


# --- the write path as `--batch` runs it: destination AND history --------------
#
# The helper-level tests above cannot tell you what reaches disk; these drive
# `write_result`, which is the sequence the batch loop calls.

CASE = {"case_id": "E1A", "required_measurements": "decay"}
CORRUPT = '{"engine": "integrated-rtl", "rubric_history": [{"kind": "rubric'  # truncated


@pytest.mark.parametrize("engine", ["integrated-rtl", "fixed-model"])
def test_the_write_path_refuses_a_corrupt_rtl_destination_and_keeps_its_bytes(tmp_path, engine):
    """#610 review: no engine repairs a corrupt record. An integrated-rtl
    result passes destination selection and is refused by history
    preservation; a model result is refused by destination selection. Either
    way the damaged anchor's bytes are exactly as they were, and no twin is
    written beside it."""
    dest = tmp_path / "E1A.json"
    dest.write_text(CORRUPT)
    with pytest.raises(rc.Refused, match="unreadable"):
        rc.write_result(CASE, dest, _record(engine))
    assert dest.read_text() == CORRUPT
    assert not (tmp_path / rc.MODEL_TWIN_DIR).exists()


def test_the_write_path_replaces_a_valid_anchor_and_carries_its_history(tmp_path):
    """Positive leg, on a VALID existing record: an anchor replaces an anchor
    and its rubric_history is carried forward unchanged."""
    dest = tmp_path / "E1A.json"
    old = _record("integrated-rtl", commit="old")
    old["rubric_history"] = [{"kind": rc.RUBRIC_CHANGE, "worst": 1.5}]
    dest.write_text(json.dumps(old))
    got = rc.write_result(CASE, dest, _record("integrated-rtl", commit="new"))
    assert got == dest
    held = json.loads(dest.read_text())
    assert held["source_commit"] == "new"
    assert held["rubric_history"] == old["rubric_history"]


def test_the_write_path_puts_a_model_result_beside_a_valid_anchor(tmp_path):
    """Positive leg, on a VALID existing anchor: the model result goes to the
    twin and the anchor's bytes are untouched."""
    dest = tmp_path / "E1A.json"
    dest.write_text(json.dumps(_record("integrated-rtl", commit="anchor01")))
    before = dest.read_bytes()
    got = rc.write_result(CASE, dest, _record("fixed-model"))
    assert got == tmp_path / rc.MODEL_TWIN_DIR / "E1A.json"
    assert json.loads(got.read_text())["engine"] == "fixed-model"
    assert dest.read_bytes() == before

import json

import pytest

import regen_m1a_units as regen


def _new():
    return {"provenance": {"command": "tools/run_case.py"}}


def test_null_provenance_in_published_record_skips_outcome_code_carry():
    new = _new()
    old = {"provenance": None}
    regen.carry_scoring_stamps(new, old)
    assert "outcome_code" not in new["provenance"]
    assert new["provenance"]["command"] == "tools/regen_m1a_units.py (cached-audio reanalysis)"


def test_absent_provenance_in_published_record_skips_outcome_code_carry():
    new = _new()
    old = {}
    regen.carry_scoring_stamps(new, old)
    assert "outcome_code" not in new["provenance"]
    assert new["provenance"]["command"] == "tools/regen_m1a_units.py (cached-audio reanalysis)"


def test_outcome_code_is_carried_from_a_well_formed_published_record():
    new = _new()
    old = {"provenance": {"outcome_code": 0, "command": "tools/run_case.py"}}
    regen.carry_scoring_stamps(new, old)
    assert new["provenance"]["outcome_code"] == 0
    assert new["provenance"]["command"] == "tools/regen_m1a_units.py (cached-audio reanalysis)"

def test_a_published_record_without_diagnostics_is_refused(tmp_path, monkeypatch,
                                                           capsys):
    """Through main(): the cached-audio reanalysis REFUSES (exit 2) a published
    record it cannot read, where it exited 1 by KeyError (#1604, #1622), and
    writes nothing."""
    record = json.loads(regen.RECORD.read_text())
    del record["diagnostics"]
    record_copy = tmp_path / "M1A.json"
    record_copy.write_text(json.dumps(record))
    original = record_copy.read_bytes()
    monkeypatch.setattr(regen, "RECORD", record_copy)
    assert regen.main() == 2
    printed = capsys.readouterr().out
    assert "REFUSED" in printed and "'diagnostics'" in printed
    assert record_copy.read_bytes() == original


@pytest.mark.parametrize("record, why", [({}, "'diagnostics'"),
                                         ([], "not a JSON object"),
                                         (None, "not a JSON object")])
def test_a_published_record_that_is_not_an_object_is_refused(tmp_path,
                                                             monkeypatch, capsys,
                                                             record, why):
    """The reanalysis is replaced by a tripwire: with the guard absent, a `null`
    record reads as "no cached audio" and `bass.run` RENDERS, rewriting the
    published m1a-model.wav -- a control must never be able to do that."""
    def tripwire(*args, **kwargs):
        pytest.fail("the reanalysis started on a record the guard should have refused")
    monkeypatch.setattr(regen.bass, "run", tripwire)
    record_copy = tmp_path / "M1A.json"
    record_copy.write_text(json.dumps(record))
    monkeypatch.setattr(regen, "RECORD", record_copy)
    assert regen.main() == 2
    printed = capsys.readouterr().out
    assert "REFUSED" in printed and why in printed

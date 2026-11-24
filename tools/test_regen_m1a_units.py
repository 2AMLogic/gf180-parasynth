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

"""The gate that keeps a model result from being filed as chip evidence.

`tools/score_drum_i2s.py` overwrites a Drums row with a measurement taken off
the I2S pins. Everything downstream -- the board's engine column, #94's "four
anchors" claim -- rests on one bit: that the record says `integrated-rtl` and
that the bit was earned. These tests are that bit's only mechanical defence.
"""
import copy

import pytest

import compare_drum_i2s_candidate as compare


CASE = {"case_id": "D02A", "family": "Drums", "subject": "Snare / anchor",
        "split": "Development", "reference_target": "Fischer hardware sample",
        "required_measurements": "Body/noise balance; attack; noise decay"}

_METRICS = {
    "Body/noise balance": {"value": -5.0, "units": "dB", "reference": -3.26, "error": -1.74,
                           "tolerance": 3.0, "valid": True, "tolerance_basis": "energy ratio"},
    "attack": {"value": 4.23, "units": "ms", "reference": 4.33, "error": -0.10,
               "tolerance": 2.1655, "valid": True, "tolerance_basis": "time"},
    "noise decay": {"value": 71.1, "units": "ms", "reference": 69.46, "error": 1.64,
                    "tolerance": 34.73, "valid": True, "tolerance_basis": "time"},
}


def _record(engine, **over):
    rec = {
        "engine": engine, "case_id": "D02A", "subject": "Snare / anchor",
        "source_commit": "abc1234",
        "reference_profile": "fischer-tr808-103852:sd8/SD5050.WAV (TONE 5.0, SNAPPY 5.0)",
        "tolerance_policy": {"time": "50 %", "energy ratio": "3 dB"},
        "metrics": copy.deepcopy(_METRICS),
        "diagnostics": {"decoded_i2s_sha256": "cafe"},
        "provenance": {"engine": engine, "worktree": {"commit": "abc1234"},
                       "command": "tools/score_drum_i2s.py", "inputs": {"a": "sha256:1"}},
    }
    if engine == compare.CANDIDATE_ENGINE:
        rec["diagnostics"]["integration"] = {
            "simulator": "verilator", "rtl_build": "017e87c",
            "fixed_model_at_realised_strike": {
                "differing_samples": 0, "max_abs_difference": 0, "identical": True},
            "fixed_model_scorecard_render": {
                "strike_frame": 480, "differing_samples": 11411,
                "max_abs_difference": 4522, "identical": False}}
    rec.update(over)
    return rec


def _pair():
    return _record(compare.BASELINE_ENGINE), _record(compare.CANDIDATE_ENGINE)


def test_agreeing_records_compare_and_attribute_to_the_stimulus():
    baseline, candidate = _pair()
    report = compare.compare_records(baseline, candidate, CASE)
    assert report["case_verdicts_agree"] is True
    assert report["properties_that_differ"] == []
    assert report["properties_whose_verdict_flips"] == []
    assert report["attribution"]["attributable_to"] == "the stimulus"
    assert {p["property"] for p in report["properties"]} == set(_METRICS)


def test_a_difference_is_published_per_property_not_absorbed():
    baseline, candidate = _pair()
    candidate["metrics"]["attack"].update(value=5.23, error=0.90)
    report = compare.compare_records(baseline, candidate, CASE)
    row = next(p for p in report["properties"] if p["property"] == "attack")
    assert row["difference"] == pytest.approx(1.0)
    assert row["difference_in_tolerances"] == pytest.approx(1.0 / 2.1655)
    assert report["properties_that_differ"] == ["attack"]
    assert row["verdict_agrees"] is True          # both still inside tolerance


def test_a_verdict_flip_is_named():
    baseline, candidate = _pair()
    candidate["metrics"]["attack"].update(value=9.0, error=4.67)
    report = compare.compare_records(baseline, candidate, CASE)
    assert report["properties_whose_verdict_flips"] == ["attack"]
    assert report["case_verdicts_agree"] is False
    assert report["candidate"]["scorecard_state"] == "fail"


# ---- the gate -------------------------------------------------------------

@pytest.mark.parametrize("engine", ["fixed-model", "float-model", "board-digital", "", None])
def test_refuses_a_candidate_that_is_not_integrated_rtl(engine):
    """A model result, or one with no engine at all, must never be comparable
    as chip evidence -- whatever else about it is well formed."""
    baseline, candidate = _pair()
    if engine is None:
        candidate.pop("engine")
    else:
        candidate["engine"] = engine
    candidate["provenance"]["engine"] = engine
    with pytest.raises(compare.Refused, match="integrated-rtl"):
        compare.compare_records(baseline, candidate, CASE)


def test_refuses_a_candidate_whose_provenance_disagrees_about_the_engine():
    """The header says chip, the provenance says model. One of them is a lie
    and this cannot tell which, so it refuses rather than pick."""
    baseline, candidate = _pair()
    candidate["provenance"]["engine"] = "fixed-model"
    with pytest.raises(compare.Refused, match="disagree about the engine"):
        compare.compare_records(baseline, candidate, CASE)


def test_refuses_a_baseline_that_is_itself_a_chip_record():
    baseline, candidate = _pair()
    baseline["engine"] = compare.CANDIDATE_ENGINE
    baseline["provenance"]["engine"] = compare.CANDIDATE_ENGINE
    with pytest.raises(compare.Refused, match="fixed-model"):
        compare.compare_records(baseline, candidate, CASE)


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(case_id="D09A"), "candidate is"),
    (lambda rec: rec.update(reference_profile="some other WAV"), "different references"),
    (lambda rec: rec.update(tolerance_policy={"time": "90 %"}), "tolerance policies differ"),
    (lambda rec: rec["metrics"].__setitem__("extra", dict(_METRICS["attack"])),
     "different properties"),
    (lambda rec: rec["metrics"]["attack"].update(tolerance=9.0), "different tolerance"),
    (lambda rec: rec["metrics"]["attack"].update(units="s"), "measurement contract"),
    # these two are caught one gate earlier, by the board itself: an invalid or
    # non-finite property has no distance, so the record has no verdict to compare
    (lambda rec: rec["metrics"]["attack"].update(valid=False),
     "no pass/fail verdict on the board"),
    (lambda rec: rec["metrics"].pop("attack"), "no pass/fail verdict on the board"),
    (lambda rec: rec["metrics"]["attack"].update(error=float("nan")),
     "no pass/fail verdict on the board"),
    (lambda rec: rec["diagnostics"].pop("integration"), "no integration evidence"),
    (lambda rec: rec["diagnostics"]["integration"].pop("fixed_model_at_realised_strike"),
     "omits one of the two fixed-model comparisons"),
    (lambda rec: rec["diagnostics"]["integration"].pop("simulator"), "simulator"),
    (lambda rec: rec.update(scorecard_state="fail"), "but the board evaluates it"),
])
def test_refuses_incomparable_or_unattributable_candidates(mutation, match):
    baseline, candidate = _pair()
    mutation(candidate)
    with pytest.raises(compare.Refused, match=match):
        compare.compare_records(baseline, candidate, CASE)


def test_a_chip_that_is_not_the_model_is_attributed_to_the_chip_not_the_stimulus():
    """The one case where a property difference IS a defect report."""
    baseline, candidate = _pair()
    candidate["diagnostics"]["integration"]["fixed_model_at_realised_strike"] = {
        "differing_samples": 6659, "max_abs_difference": 2414, "identical": False}
    candidate["metrics"]["attack"].update(value=5.23, error=0.90)
    report = compare.compare_records(baseline, candidate, CASE)
    assert report["attribution"]["attributable_to"] == "the chip"
    assert "defect report" in report["attribution"]["why"]


def test_identical_samples_that_produce_different_numbers_indict_the_estimator():
    baseline, candidate = _pair()
    candidate["diagnostics"]["integration"]["fixed_model_scorecard_render"].update(
        differing_samples=0, max_abs_difference=0, identical=True)
    candidate["metrics"]["attack"].update(value=5.23, error=0.90)
    report = compare.compare_records(baseline, candidate, CASE)
    assert report["attribution"]["attributable_to"] == "the estimator"

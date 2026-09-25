import copy
import json

import pytest
import measure_mono_pulse_2x as experiment


def baseline():
    record = json.loads((experiment.ROOT / "docs/scorecard/results/M5B.json").read_text())
    return {"metrics": record["metrics"], "event_diagnostics": record["diagnostics"]["events"],
            "analysis_version": record["analysis_version"],
            "reference_sha256": record["diagnostics"]["reference_sha256"],
            "manifest_sha256": record["diagnostics"]["reference_manifest_sha256"]}


def test_disabled_candidate_cannot_promote_and_invalid_scores_refuse():
    row = baseline()
    assert not experiment.compare(row, copy.deepcopy(row))["accepts_incremental_improvement"]
    bad = copy.deepcopy(row)
    bad["metrics"]["Pitch"]["valid"] = False
    with pytest.raises(experiment.score.Refused, match="invalid metric"):
        experiment.compare(row, bad)
    bad = copy.deepcopy(row)
    bad["analysis_version"] = "unrelated"
    with pytest.raises(experiment.score.Refused, match="analysis_version"):
        experiment.compare(row, bad)


def test_pulse_improvement_cannot_hide_lost_saw_alias_pass():
    row = baseline()
    bad = copy.deepcopy(row)
    bad["metrics"]["Foldback energy"]["error"] -= 1.0
    bad["event_diagnostics"][0]["foldback_db"]["excess_over_reference_db"] = 3.1
    comparison = experiment.compare(row, bad)
    assert not comparison["accepts_incremental_improvement"]
    assert comparison["lost_per_note_passes"][0]["property"] == "Foldback energy"


def test_gain_control_does_not_select_pulse_oversampling():
    control = experiment.factory("gain_only")
    candidate = experiment.factory("pulse2x")
    assert not control.oversample_pulse_2x
    assert candidate.oversample_pulse_2x
    for obj in (control, candidate):
        assert obj.oversample_2x and obj.rate_converted_ladder
        assert obj.pulse479_filter_candidate and obj.causal_filter

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


def test_gain_only_control_uses_the_candidates_rectangle_gain():
    """#333: the control must apply the level change the candidate makes. With
    the rectangle headroom at 0.74 the control scales by 24248, not the saw's
    27853; and the candidate itself renders at that gain."""
    import numpy as np
    saved = experiment.RECT_GAIN_Q15
    try:
        experiment.set_rect_gain(24248)
        ctl = experiment.factory("gain_only")
        cand = experiment.factory("pulse2x")
        a = ctl.note(96, 0.1, waves=("pulse29",) * 3, mix=(1.0, 0.0, 0.0))
        experiment.set_rect_gain(experiment.vf._OS2_SUBSTEP_GAIN_Q15)
        b = experiment.factory("gain_only").note(96, 0.1, waves=("pulse29",) * 3, mix=(1.0, 0.0, 0.0))
        assert not np.array_equal(a, b)
        experiment.set_rect_gain(24248)
        c1 = cand.note(96, 0.1, waves=("pulse29",) * 3, mix=(1.0, 0.0, 0.0))
        experiment.set_rect_gain(experiment.vf._OS2_SUBSTEP_GAIN_Q15)
        c2 = experiment.factory("pulse2x").note(96, 0.1, waves=("pulse29",) * 3, mix=(1.0, 0.0, 0.0))
        assert not np.array_equal(c1, c2)
        saw = experiment.factory("pulse2x").note(84, 0.1, waves=("saw",) * 3, mix=(1.0, 0.0, 0.0))
        experiment.set_rect_gain(24248)
        saw2 = experiment.factory("pulse2x").note(84, 0.1, waves=("saw",) * 3, mix=(1.0, 0.0, 0.0))
        assert np.array_equal(saw, saw2)                 # the saw keeps 0.85
    finally:
        experiment.set_rect_gain(saved)

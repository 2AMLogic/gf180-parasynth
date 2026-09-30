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
    """#333: the control must apply the level change the candidate makes. The
    model now builds the rectangle headroom itself (voice_fx._OS2_RECT_GAIN_Q15,
    R2's 0.74), so the tool's gain IS the model's, the gain-only control scales
    rectangles by it (not the saw's 27853), and an override the model does not
    build is refused rather than measured."""
    import numpy as np
    assert experiment.RECT_GAIN_Q15 == experiment.vf._OS2_RECT_GAIN_Q15 == 24248
    ctl = experiment.factory("gain_only").note(96, 0.1, waves=("pulse29",) * 3,
                                               mix=(1.0, 0.0, 0.0))
    base = experiment.factory("baseline").note(96, 0.1, waves=("pulse29",) * 3,
                                               mix=(1.0, 0.0, 0.0))
    assert not np.array_equal(ctl, base)              # the control moved the level
    saw_ctl = experiment.factory("gain_only").note(84, 0.1, waves=("saw",) * 3,
                                                   mix=(1.0, 0.0, 0.0))
    saw_base = experiment.factory("baseline").note(84, 0.1, waves=("saw",) * 3,
                                                   mix=(1.0, 0.0, 0.0))
    assert np.array_equal(saw_ctl, saw_base)          # and left the saw alone
    saved = experiment.RECT_GAIN_Q15
    try:
        experiment.set_rect_gain(experiment.vf._OS2_SUBSTEP_GAIN_Q15)
        with pytest.raises(experiment.score.Refused, match="defines its own rectangle gain"):
            experiment.factory("pulse2x")
    finally:
        experiment.set_rect_gain(saved)

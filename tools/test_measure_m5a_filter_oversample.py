import pytest

import measure_m5a_filter_oversample as experiment
import mono_m5a_score as m5a


def _row(target_error):
    metrics = {}
    for name in m5a.TOLERANCES:
        error = target_error if name in ("Harmonic shape", "Foldback energy") else 0.1
        metrics[name] = {"error": error, "value": error, "units": "dB",
                         "tolerance": 1.0}
    return {"metrics": metrics, "event_diagnostics": [{"stages": {}}]}


def _configurations(candidate_error=1.0):
    return {
        mode: {
            pulse: _row(error)
            for pulse in ("pulse29", "pulse479")
        }
        for mode, error in (
            ("production_2x_sample_hold", 3.0),
            ("reconstructed_2x", 2.0),
            ("reconstructed_4x", candidate_error),
        )
    }


def test_screen_requires_both_target_improvements_and_complete_score_vectors():
    result = experiment._model_screen(_configurations())
    assert result["passes_model_screen_for_both_pulse_widths"] is True
    broken = _configurations()
    del broken["reconstructed_4x"]["pulse29"]["metrics"]["Envelope release"]
    with pytest.raises(m5a.Refused, match="incomplete seven-property score"):
        experiment._model_screen(broken)


def test_screen_does_not_hide_a_target_regression_behind_other_metrics():
    result = experiment._model_screen(_configurations(candidate_error=2.5))
    assert result["passes_model_screen_for_both_pulse_widths"] is False

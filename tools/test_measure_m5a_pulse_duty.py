from __future__ import annotations

import pytest

import measure_m5a_pulse_duty as probe


def test_pulse_probe_refuses_unknown_or_duplicate_shapes():
    with pytest.raises(ValueError, match="unique members"):
        probe.measure(["square", "unknown"])
    with pytest.raises(ValueError, match="unique members"):
        probe.measure(["square", "square"])


def test_pulse_probe_reports_measured_partial_errors_and_floor_bounds():
    model = {"h2": -40.0, "h3": -20.0, "floor4": -65.0}
    reference = {"h2": -30.0, "h3": -21.0, "h4": -30.0}
    result = probe._shape_summary(model, reference, 1000.0, 1000.0, 48000)
    assert result["harmonic_error_db_model_minus_reference"]["h2"] == -10.0
    assert result["harmonic_error_db_model_minus_reference"]["h3"] == 1.0
    assert result["harmonic_error_db_model_minus_reference"]["h4"] == -35.0
    assert result["harmonic_comparison"]["h4"] == "model_below_floor_bound"

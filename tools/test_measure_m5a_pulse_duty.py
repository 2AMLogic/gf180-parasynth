from __future__ import annotations

import pytest

import measure_m5a_pulse_duty as probe
import refusal


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


@pytest.mark.parametrize("f0", [9000.0, 12000.0])
def test_pulse_summary_refuses_fewer_than_two_comparable_partials(f0):
    # 9000 Hz leaves only h2 below Nyquist; 12000 Hz leaves none.
    signature = {"h2": -40.0, "h3": -30.0, "h4": -50.0}
    with pytest.raises(refusal.Refused, match="fewer than two comparable partials"):
        probe._shape_summary(signature, signature, f0, f0, 48000)


def test_pulse_cli_argument_error_is_not_masked_as_a_refusal():
    with pytest.raises(ValueError, match="unique members"):
        probe.main(["--shapes", "square", "square", "--out", "build/never-written.json"])

import json

import numpy as np
import pytest

import measure_m5a_filter_oversample as experiment
import mono_m5a_score as m5a
import refusal

TONES = (150, 1_000, 2_000, 4_000, 8_000, 12_000, 16_000)
MODES = ("production_2x_sample_hold", "reconstructed_2x", "reconstructed_4x")


def _row(target_error):
    metrics = {}
    for name in m5a.TOLERANCES:
        error = target_error if name in ("Harmonic shape", "Foldback energy") else 0.1
        metrics[name] = {"error": error, "value": error, "units": "dB",
                         "tolerance": 1.0}
    return {"metrics": metrics, "event_diagnostics": [
        {"wave": "pulse", "midi": 84, "stages": {},
         "harmonic_error_db_model_minus_reference": {"h2": target_error, "h3": -target_error}}
    ]}


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


def test_incremental_improvement_can_pass_while_case_remains_red():
    baseline, candidate = _row(0.8), _row(0.7)
    # Unchanged envelope misses remain failures of the case, not blockers to
    # accepting a measurable harmonic improvement.
    baseline["metrics"]["Envelope attack"]["error"] = 8.0
    candidate["metrics"]["Envelope attack"]["error"] = 8.0
    baseline["metrics"]["Envelope release"]["error"] = 200.0
    candidate["metrics"]["Envelope release"]["error"] = 200.0
    compared = experiment._compare_incremental(baseline, candidate)
    assert compared["accepts_incremental_improvement"] is True
    assert compared["case_passes"] is False
    assert compared["improved_components"]


def test_incremental_gate_rejects_any_property_regression_and_incomplete_evidence():
    baseline, candidate = _row(0.8), _row(0.7)
    candidate["metrics"]["Gain"]["error"] = 0.2
    assert experiment._compare_incremental(
        baseline, candidate)["accepts_incremental_improvement"] is False
    del candidate["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"]["h2"]
    with pytest.raises(m5a.Refused, match="partial evidence"):
        experiment._compare_incremental(baseline, candidate)


def test_incremental_gate_uses_declared_materiality_deadbands_not_case_limits():
    baseline, candidate = _row(0.8), _row(0.7)
    candidate["metrics"]["Gain"]["error"] = 0.105
    # A 0.005 dB change is below the declared meaningful regression threshold.
    result = experiment._compare_incremental(baseline, candidate)
    assert result["accepts_incremental_improvement"] is True
    candidate["metrics"]["Gain"]["error"] = 0.2
    result = experiment._compare_incremental(baseline, candidate)
    assert result["accepts_incremental_improvement"] is False


def _tone_of(x):
    crossings = np.count_nonzero(np.signbit(x[1:]) != np.signbit(x[:-1]))
    measured_hz = crossings * experiment.vf.SR / (2 * len(x))
    return min(TONES, key=lambda tone: abs(tone - measured_hz))


def _ladder(monkeypatch, output):
    def process(self, x, *args, **kwargs):
        return output(x, _tone_of(x))
    monkeypatch.setattr(experiment.fixed.LadderFx, "process", process)
    monkeypatch.setattr(experiment.frc.RateConvertedLadder, "process", process)


def _cli(monkeypatch, output):
    monkeypatch.setattr(experiment.m5a, "measure", lambda **kwargs: _row(1.0))
    _ladder(monkeypatch, output)
    report = experiment.ROOT / "build/scorecard/test-m5a-filter-rate-report.json"
    report.unlink(missing_ok=True)
    return experiment.main(["--out", str(report.relative_to(experiment.ROOT))]), report


@pytest.mark.parametrize("mode", MODES)
def test_zero_output_at_cutoff_is_json_null_while_the_other_bins_hold(monkeypatch, mode):
    _ladder(monkeypatch, lambda x, f: np.zeros_like(x) if f == 8_000 else np.array(x))
    report = experiment._linear_response(mode)
    assert report["measured_absolute_gain_db"] == {
        "150": 0.0, "1000": 0.0, "2000": 0.0, "4000": 0.0,
        "8000": None, "12000": 0.0, "16000": 0.0}
    assert report["measured_response_relative_to_150_hz_db"] == {
        "150": 0.0, "1000": 0.0, "2000": 0.0, "4000": 0.0,
        "8000": None, "12000": 0.0, "16000": 0.0}
    assert report["measured_at_cutoff_db"] is None
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("ratio", "expected_db"), [(1.0, 0.0), (0.5, -6.0206), (1e-16, -320.0)],
                         ids=["unity", "half", "sub-floor"])
def test_absolute_gain_is_20_log10_of_the_ratio_without_a_floor(monkeypatch, mode, ratio, expected_db):
    _ladder(monkeypatch, lambda x, f: x * ratio)
    report = experiment._linear_response(mode)
    assert set(report["measured_absolute_gain_db"].values()) == {expected_db}
    assert report["measured_at_cutoff_db"] == 0.0


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("all_zero", [True, False], ids=["all-zero ladder", "150 Hz only"])
def test_zero_output_at_the_150_hz_reference_refuses_instead_of_normalising(monkeypatch, mode, all_zero):
    _ladder(monkeypatch, lambda x, f: np.zeros_like(x) if all_zero or f == 150 else np.array(x))
    with pytest.raises(refusal.Refused, match="150 Hz"):
        experiment._linear_response(mode)


@pytest.mark.parametrize("mode", MODES)
def test_zero_input_projection_cannot_be_the_gain_denominator(monkeypatch, mode):
    _ladder(monkeypatch, lambda x, f: np.array(x))
    with pytest.raises(refusal.Refused, match="input projection is exactly zero"):
        experiment._linear_response(mode, amplitude=0.0)


@pytest.mark.parametrize("all_zero", [True, False], ids=["all-zero ladder", "150 Hz only"])
def test_cli_refuses_a_ladder_silent_at_150_hz_and_writes_no_report(monkeypatch, capsys, all_zero):
    status, report = _cli(monkeypatch,
                          lambda x, f: np.zeros_like(x) if all_zero or f == 150 else np.array(x))
    assert status == 2
    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("REFUSED:") and "150 Hz" in line]
    assert not report.exists()


def test_cli_writes_a_report_when_every_ladder_passes_every_tone(monkeypatch):
    status, report = _cli(monkeypatch, lambda x, f: np.array(x))
    try:
        assert status == 0
        written = json.loads(report.read_text())
        assert [written["filter_response"][mode]["measured_at_cutoff_db"] for mode in MODES] == [0.0, 0.0, 0.0]
    finally:
        report.unlink(missing_ok=True)

import json

import pytest

import measure_m5a_filter_oversample as experiment
import mono_m5a_score as m5a


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


# ---- a frozen manifest that cannot be read is REFUSED (exit 2) through main(),
# not a KeyError traceback (exit 1) (#1604, #1622). One control per key
# (`audio`), on a scratch copy of the committed manifest; the refusal fires
# before any render.
def _run_main(tmp_path, monkeypatch, capsys, manifest):
    damaged = tmp_path / "manifest.json"
    damaged.write_text(json.dumps(manifest))
    monkeypatch.setattr(experiment.m5a, "MANIFEST", damaged)
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    out = tmp_path / "report.json"
    rc = experiment.main(["--out", str(out)])
    return rc, capsys.readouterr().out, out


def test_a_frozen_manifest_without_audio_is_refused(tmp_path, monkeypatch, capsys):
    manifest = json.loads(experiment.m5a.MANIFEST.read_text())
    del manifest["audio"]
    rc, printed, out = _run_main(tmp_path, monkeypatch, capsys, manifest)
    assert rc == 2 and "REFUSED" in printed and "'audio'" in printed
    assert not out.exists()
    # nothing rendered: the directory main() creates is still empty
    assert not any((tmp_path / "build/scorecard/m5a-filter-rate-audio").iterdir())


@pytest.mark.parametrize("manifest, why", [({}, "'audio'"),
                                           ([], "not a JSON object"),
                                           (None, "not a JSON object")])
def test_a_frozen_manifest_that_is_not_an_object_is_refused(tmp_path, monkeypatch,
                                                            capsys, manifest, why):
    rc, printed, out = _run_main(tmp_path, monkeypatch, capsys, manifest)
    assert rc == 2 and "REFUSED" in printed and why in printed
    assert not out.exists()

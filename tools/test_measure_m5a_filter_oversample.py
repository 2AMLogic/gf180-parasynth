import json

import pytest

import measure_m5a_filter_oversample as experiment
import mono_m5a_score as m5a

_STUB_REPORT = {"source_commit": "stub", "source_dirty": False,
                "comparisons": [], "decision": {}}


def _output_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    monkeypatch.setattr(experiment, "ROOT", repo.resolve())
    return outside


def _escape(outside, case):
    return {"absolute": str(outside / "report.json"),
            "dotdot": "../report.json",
            "symlink": "link/report.json"}[case]


def _stub_run(monkeypatch):
    calls = []
    monkeypatch.setattr(experiment, "run",
                        lambda *args, **kwargs: calls.append(args) or _STUB_REPORT)
    return calls


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


@pytest.mark.parametrize("case", ["absolute", "dotdot", "symlink"])
def test_out_that_leaves_the_repository_is_refused_before_measuring(tmp_path, monkeypatch, capsys, case):
    outside = _output_tree(tmp_path, monkeypatch)
    calls = _stub_run(monkeypatch)
    assert experiment.main(["--out", _escape(outside, case)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert calls == []
    assert not list(tmp_path.rglob("report.json"))


def test_in_repo_out_is_written_and_named_relative_to_the_repository(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(experiment, "ROOT", repo.resolve())
    calls = _stub_run(monkeypatch)
    assert experiment.main(["--out", "build/scorecard/report.json"]) == 0
    assert len(calls) == 1
    assert json.loads((repo / "build/scorecard/report.json").read_text()) == _STUB_REPORT
    assert json.loads(capsys.readouterr().out)["report"] == "build/scorecard/report.json"


def test_in_repo_out_named_through_a_symlink_from_outside_is_written_in_repo(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(experiment, "ROOT", repo.resolve())
    _stub_run(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    assert experiment.main(["--out", str(alias / "build" / "report.json")]) == 0
    assert (repo / "build" / "report.json").is_file()
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"

from __future__ import annotations

import json

import pytest

import measure_m5a_pulse_duty as probe
import refusal

_STUB_REPORT = {"source_commit": "stub", "runs": []}


def _output_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    monkeypatch.setattr(probe, "ROOT", repo.resolve())
    return outside


def _escape(outside, case):
    return {"absolute": str(outside / "report.json"),
            "dotdot": "../report.json",
            "symlink": "link/report.json"}[case]


def _stub_measurement(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "measure",
                        lambda *args, **kwargs: calls.append(args) or _STUB_REPORT)
    return calls


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


@pytest.mark.parametrize("case", ["absolute", "dotdot", "symlink"])
def test_out_that_leaves_the_repository_is_refused_before_measuring(tmp_path, monkeypatch, capsys, case):
    outside = _output_tree(tmp_path, monkeypatch)
    calls = _stub_measurement(monkeypatch)
    assert probe.main(["--out", _escape(outside, case)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert calls == []
    assert not list(tmp_path.rglob("report.json"))


def test_in_repo_out_is_written_and_named_relative_to_the_repository(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(probe, "ROOT", repo.resolve())
    calls = _stub_measurement(monkeypatch)
    assert probe.main(["--out", "docs/report.json"]) == 0
    assert len(calls) == 1
    assert json.loads((repo / "docs" / "report.json").read_text()) == _STUB_REPORT
    assert json.loads(capsys.readouterr().out)["report"] == "docs/report.json"


def test_in_repo_out_named_through_a_symlink_from_outside_is_written_in_repo(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(probe, "ROOT", repo.resolve())
    _stub_measurement(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    assert probe.main(["--out", str(alias / "docs" / "report.json")]) == 0
    assert (repo / "docs" / "report.json").is_file()
    assert json.loads(capsys.readouterr().out)["report"] == "docs/report.json"


def test_out_symlink_to_in_repo_file_replaces_the_link_not_its_target(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(probe, "ROOT", repo.resolve())
    _stub_measurement(monkeypatch)
    baseline = repo / "baseline.json"
    baseline.write_text("ORIGINAL")
    (repo / "latest.json").symlink_to(baseline)
    assert probe.main(["--out", "latest.json"]) == 0
    latest = repo / "latest.json"
    assert not latest.is_symlink()
    assert json.loads(latest.read_text()) == _STUB_REPORT
    assert baseline.read_text() == "ORIGINAL"
    assert json.loads(capsys.readouterr().out)["report"] == "latest.json"


def test_out_symlink_leaf_pointing_outside_is_refused(tmp_path, monkeypatch, capsys):
    outside = _output_tree(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    (outside / "target.json").write_text("ORIGINAL")
    (repo / "leaf.json").symlink_to(outside / "target.json")
    calls = _stub_measurement(monkeypatch)
    assert probe.main(["--out", "leaf.json"]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert calls == []
    assert (outside / "target.json").read_text() == "ORIGINAL"

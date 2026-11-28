"""Independent controls for causal rate-converter measurement instrumentation."""

import json

import pytest

import measure_m5a_filter_headroom as headroom

_STUB_REPORT = {"source_commit": "stub", "source_dirty": False,
                "comparisons": {}, "causal_converter": {}}


def _output_tree(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    monkeypatch.setattr(headroom, "ROOT", repo.resolve())
    monkeypatch.chdir(repo)
    return outside


def _escape(outside, case):
    return {"absolute": str(outside / "report.json"),
            "dotdot": "../report.json",
            "symlink": "link/report.json"}[case]


def _stub(monkeypatch, option):
    calls = []
    name = "run" if option == "--out" else "_recompare_report"
    monkeypatch.setattr(headroom, name,
                        lambda *args, **kwargs: calls.append(args) or _STUB_REPORT)
    return calls


def test_control_latency_diagnostic_requires_and_measures_the_injected_step():
    result = headroom._measure_control_response()
    assert result["step_frame"] == 100
    assert 0 <= result["first_changed_latency_frames"] <= 10
    quantiles = result["squared_response_energy_quantiles_frames_from_start"]
    assert quantiles["0.1"] <= quantiles["0.5"] <= quantiles["0.9"]
    assert quantiles["0.9"] < 512


@pytest.mark.parametrize("case", ["absolute", "dotdot", "symlink"])
@pytest.mark.parametrize("option", ["--out", "--recompare-report"])
def test_output_that_leaves_the_repository_is_refused_before_anything_runs(tmp_path, monkeypatch, capsys, option, case):
    outside = _output_tree(tmp_path, monkeypatch)
    calls = _stub(monkeypatch, option)
    assert headroom.main([option, _escape(outside, case)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert calls == []
    assert not list(tmp_path.rglob("report.json"))


@pytest.mark.parametrize("option", ["--out", "--recompare-report"])
def test_in_repo_output_is_written_and_named_relative_to_the_repository(tmp_path, monkeypatch, capsys, option):
    _output_tree(tmp_path, monkeypatch)
    calls = _stub(monkeypatch, option)
    assert headroom.main([option, "build/report.json"]) == 0
    assert len(calls) == 1
    assert json.loads((tmp_path / "repo" / "build" / "report.json").read_text()) == _STUB_REPORT
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"


def test_in_repo_out_named_through_a_symlink_from_outside_is_written_in_repo(tmp_path, monkeypatch, capsys):
    _output_tree(tmp_path, monkeypatch)
    _stub(monkeypatch, "--out")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "repo")
    assert headroom.main(["--out", str(alias / "build" / "report.json")]) == 0
    assert (tmp_path / "repo" / "build" / "report.json").is_file()
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"

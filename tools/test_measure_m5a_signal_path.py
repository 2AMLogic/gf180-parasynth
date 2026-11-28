from __future__ import annotations

import json

import numpy as np
import pytest

import measure_m5a_signal_path as probe
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


def test_q15_trace_conversion_uses_one_full_scale_for_every_stage():
    got = probe._q15_to_full_scale(np.array([-32768, 0, 32767], dtype=np.int16))
    assert got.tolist() == [-1.0, 0.0, pytest.approx(32767 / 32768)]


def test_full_scale_conversion_is_gain_linear():
    q15 = np.array([-16384, 8192, 24576], dtype=np.int32)
    assert probe._q15_to_full_scale(q15) == pytest.approx([-0.5, 0.25, 0.75])


def test_stage_probe_constructs_the_selected_production_filter_path():
    voice = probe._selected_candidate_voice()
    assert voice.oversample_2x
    assert voice.rate_converted_ladder
    assert voice.ladder_cfg["oversample"] == 2
    assert voice.preserve_filter_headroom
    assert voice.causal_filter
    # #1570: this copy omitted the flag score_m5a_i2s._candidate_factory sets.
    assert voice.pulse479_filter_candidate


def test_selected_voice_matches_the_i2s_candidate_factory():
    import score_m5a_i2s

    flags = ("blep", "oversample_2x", "oversample_pulse_2x", "rate_converted_ladder",
             "preserve_filter_headroom", "causal_filter", "pulse479_filter_candidate")
    ours, theirs = probe._selected_candidate_voice(), score_m5a_i2s._candidate_factory()
    assert {flag: getattr(ours, flag) for flag in flags} == \
        {flag: getattr(theirs, flag) for flag in flags}
    assert ours.ladder_cfg == theirs.ladder_cfg
    assert probe._selected_candidate_voice(blep=False).blep is False


@pytest.mark.parametrize("partials", [1, 0])
def test_signal_path_refuses_fewer_than_two_comparable_partials(monkeypatch, partials):
    # #1570: the inline stage loop had no guard -- one partial was reported as
    # a max error, none crashed in max(). The real render is kept; only the
    # signatures are made sparse.
    sparse = {"h2": -40.0} if partials else {}
    monkeypatch.setattr(probe.am, "harmonic_signature", lambda *args, **kwargs: dict(sparse))
    with pytest.raises(refusal.Refused, match="fewer than two comparable partials"):
        probe.measure([14073], [0.75])


def test_cli_argument_error_is_not_masked_as_a_refusal():
    with pytest.raises(ValueError, match="cutoff 999999 outside"):
        probe.main(["--cutoff", "999999", "--drive", "0.75", "--out", "build/never-written.json"])


def test_polyblep_off_is_an_executed_distinct_oscillator_challenger():
    baseline = probe.measure([14073], [0.75], blep=True)
    candidate = probe.measure([14073], [0.75], blep=False)
    base_run, run = baseline["runs"][0], candidate["runs"][0]

    assert "PolyBLEP disabled" in candidate["oscillator_config"]
    assert "causal reconstructed 2x ladder with headroom preserved" in candidate["filter_config"]
    assert baseline["filter_config"] == candidate["filter_config"]
    assert candidate["reference_sha256"] == baseline["reference_sha256"]
    for got, old in zip(run["events"], base_run["events"], strict=True):
        assert got["midi"] == old["midi"]
        assert got["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"] != \
               old["stages"]["oscillator"]["harmonic_error_db_model_minus_reference"]
        assert "excess_alias_db" in got["stages"]["output"]
        assert "output_gain_error_db" in got


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
    assert probe.main(["--out", "build/verification/report.json"]) == 0
    assert len(calls) == 1
    written = repo / "build" / "verification" / "report.json"
    assert json.loads(written.read_text()) == _STUB_REPORT
    assert json.loads(capsys.readouterr().out)["report"] == "build/verification/report.json"


def test_in_repo_out_named_through_a_symlink_from_outside_is_written_in_repo(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(probe, "ROOT", repo.resolve())
    _stub_measurement(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    assert probe.main(["--out", str(alias / "build" / "report.json")]) == 0
    assert (repo / "build" / "report.json").is_file()
    assert json.loads(capsys.readouterr().out)["report"] == "build/report.json"

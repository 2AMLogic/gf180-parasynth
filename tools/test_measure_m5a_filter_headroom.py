"""Independent controls for causal rate-converter measurement instrumentation."""

import json

import pytest

import measure_m5a_filter_headroom as headroom


def test_control_latency_diagnostic_requires_and_measures_the_injected_step():
    result = headroom._measure_control_response()
    assert result["step_frame"] == 100
    assert 0 <= result["first_changed_latency_frames"] <= 10
    quantiles = result["squared_response_energy_quantiles_frames_from_start"]
    assert quantiles["0.1"] <= quantiles["0.5"] <= quantiles["0.9"]
    assert quantiles["0.9"] < 512


@pytest.mark.parametrize("value", [10**400, None, "not-a-number"],
                         ids=["beyond-float", "null", "non-numeric-string"])
def test_recompare_report_refuses_a_partial_that_float_cannot_read(tmp_path, capsys, value):
    """#1680, the second shipped path: `--recompare-report` hands JSON-loaded
    rows straight to `_compare_incremental`, with no record gate in front, so
    its unguarded `float()` left as a traceback -- exit 1 -- here too. The
    reason is asserted because any refusal would give the exit code."""
    shipped = headroom.ROOT / "docs/scorecard/mono-m5a-miniv3/filter-headroom-v1.json"
    report = json.loads(shipped.read_text())
    row = report["configurations"]["pulse29"]["headroom"]
    row["event_diagnostics"][0]["harmonic_error_db_model_minus_reference"]["h2"] = value
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))

    assert headroom.main(["--recompare-report", str(path)]) == 2

    assert ("REFUSED: candidate saw/84 harmonic partial h2 cannot be read as a float"
            in capsys.readouterr().out)
    assert path.read_text() == json.dumps(report)


# ---- a frozen manifest that cannot be read is REFUSED (exit 2) through main(),
# not a KeyError traceback (exit 1) (#1604, #1622). One control per key
# (`audio`), on a scratch copy of the committed manifest; the refusal fires
# before any render.
AUDIO_DIR = "build/scorecard/m5a-filter-headroom-audio"


def _run_main(tmp_path, monkeypatch, capsys, manifest):
    damaged = tmp_path / "manifest.json"
    damaged.write_text(json.dumps(manifest))
    monkeypatch.setattr(headroom.m5a, "MANIFEST", damaged)
    monkeypatch.setattr(headroom, "ROOT", tmp_path)
    out = tmp_path / "report.json"
    rc = headroom.main(["--out", str(out)])
    return rc, capsys.readouterr().out, out


def test_a_frozen_manifest_without_audio_is_refused(tmp_path, monkeypatch, capsys):
    manifest = json.loads(headroom.m5a.MANIFEST.read_text())
    del manifest["audio"]
    rc, printed, out = _run_main(tmp_path, monkeypatch, capsys, manifest)
    assert rc == 2 and "REFUSED" in printed and "'audio'" in printed
    assert not out.exists()
    # nothing rendered: the directory main() creates is still empty
    assert not any((tmp_path / AUDIO_DIR).iterdir())


@pytest.mark.parametrize("manifest, why", [({}, "'audio'"),
                                           ([], "not a JSON object"),
                                           (None, "not a JSON object")])
def test_a_frozen_manifest_that_is_not_an_object_is_refused(tmp_path, monkeypatch,
                                                            capsys, manifest, why):
    rc, printed, out = _run_main(tmp_path, monkeypatch, capsys, manifest)
    assert rc == 2 and "REFUSED" in printed and why in printed
    assert not out.exists()


def test_a_frozen_manifest_with_null_audio_is_refused(tmp_path, monkeypatch, capsys):
    manifest = json.loads(headroom.m5a.MANIFEST.read_text())
    manifest["audio"] = None
    rc, printed, out = _run_main(tmp_path, monkeypatch, capsys, manifest)
    assert rc == 2 and "REFUSED" in printed and "'audio'" in printed
    assert "null" in printed
    assert not out.exists()

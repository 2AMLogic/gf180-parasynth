"""Diva and Model D noise-response controls, substituting only the renderer."""
import json
import math

import numpy as np
import pytest

import audio_measure as am
import reference_compare as rc
import reference_rigs as rr


SOURCE = np.random.default_rng(282).uniform(-0.25, 0.25, 2**15)
SILENCE = np.zeros_like(SOURCE)
HALF_GAIN_DB = 20 * math.log10(0.5)


def rig_with_captures(rig_class, filtered, references):
    rig = object.__new__(rig_class)
    captures = iter(references)

    def render(cut, res, seconds, level):
        return next(captures) if (cut, res) == (1.0, 0.0) else filtered

    rig.noise_render = render
    return rig


@pytest.mark.parametrize("rig_class", [rr.DivaRig, rr.ModelDRig])
def test_half_amplitude_has_known_gain(rig_class):
    rig = rig_with_captures(rig_class, 0.5 * SOURCE, [SOURCE])
    gain = rig.noise_curve(rc.FREQS, 0.5, 0.05)
    assert gain.shape == rc.FREQS.shape
    np.testing.assert_allclose(gain, HALF_GAIN_DB, rtol=0, atol=1e-10)


@pytest.mark.parametrize("rig_class", [rr.DivaRig, rr.ModelDRig])
@pytest.mark.parametrize("filtered", [SILENCE, SOURCE], ids=["silent-filter", "live-filter"])
def test_silent_reference_refuses_for_either_filtered_capture(rig_class, filtered):
    rig = rig_with_captures(rig_class, filtered, [SILENCE])
    with pytest.raises(am.InsufficientEvidence, match="wide-open reference"):
        rig.noise_curve(rc.FREQS, 0.5, 0.05)


@pytest.mark.parametrize("rig_class", [rr.DivaRig, rr.ModelDRig])
@pytest.mark.parametrize("bad_reference", [
    SILENCE,
    np.append(SILENCE, 1.0),   # audible only past the final complete Welch window
    np.append(SOURCE, np.nan),
], ids=["silent", "silent-windows", "nonfinite-tail"])
def test_retry_at_same_level_and_duration_caches_only_valid_reference(rig_class, bad_reference):
    rig = rig_with_captures(rig_class, 0.5 * SOURCE, [bad_reference, SOURCE])
    with pytest.raises(am.InsufficientEvidence, match="wide-open reference"):
        rig.noise_curve(rc.FREQS, 0.5, 0.05)
    for res in (0.05, 0.1):
        gain = rig.noise_curve(rc.FREQS, 0.5, res)
        np.testing.assert_allclose(gain, HALF_GAIN_DB, rtol=0, atol=1e-10)


@pytest.mark.parametrize("stage", ["response", "peakdrive"])
def test_diva_reports_refusal_and_keeps_measuring(stage, tmp_path, monkeypatch, capsys):
    # The first reference fails. The same response sweep (or the next drive
    # level) has valid audio, so dropping the whole stage loses a measurement.
    rig = rig_with_captures(rr.DivaRig, 0.5 * SOURCE, [SILENCE] + [SOURCE] * 5)
    monkeypatch.setattr(rc, "build", lambda name: rig)
    args = ["--devices", "diva", "--out", str(tmp_path)]
    assert rc.main([*args, "--stage", stage]) == 0
    rows = json.loads((tmp_path / f"{stage}-diva.json").read_text())
    refused, recovered = rows[:2]
    assert refused["ok"] is False
    assert "wide-open reference" in refused["why"]
    for field in ("gain_db", "corner_hz", "slope_db_oct", "peak_db", "peak_hz", "peak_q"):
        assert refused.get(field) is None
    setting = "cut_setting" if stage == "response" else "level_dbfs"
    assert refused[setting] != recovered[setting]
    np.testing.assert_allclose(recovered["gain_db"], HALF_GAIN_DB, rtol=0, atol=1e-10)
    assert "wide-open reference" in capsys.readouterr().out
    rc.main([*args, "--report"])
    assert "wide-open reference" in capsys.readouterr().out

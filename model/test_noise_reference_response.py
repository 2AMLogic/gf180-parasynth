"""Qualification controls for the wide-open noise reference, without a plugin."""
import math

import numpy as np
import pytest

import audio_measure as am
import reference_compare as rc
import reference_rigs as rr


def measured_curve(rig_class, filtered, reference):
    rig = object.__new__(rig_class)

    def render(cut, res, seconds, level):
        return reference if (cut, res) == (1.0, 0.0) else filtered

    rig.noise_render = render
    if rig_class is rr.DivaRig:
        row, = rc.stage_response(rig, "diva", [(0.5, 800.0, 0.05)])
        return np.asarray(row["gain_db"])
    return rig.noise_curve(rc.FREQS, 0.5, 0.05)


def broadband():
    return np.random.default_rng(282).uniform(-0.25, 0.25, 2**15)


@pytest.mark.parametrize("rig_class", [rr.DivaRig, rr.ModelDRig])
def test_half_amplitude_has_known_gain(rig_class):
    source = broadband()
    gain = measured_curve(rig_class, source * 0.5, source)
    np.testing.assert_allclose(gain, 20 * math.log10(0.5), rtol=0, atol=1e-10)


@pytest.mark.parametrize("rig_class", [rr.DivaRig, rr.ModelDRig])
@pytest.mark.parametrize("filtered_audible", [False, True])
def test_no_gain_without_a_wide_open_reference(rig_class, filtered_audible):
    source = broadband()
    silent = np.zeros_like(source)
    try:
        gain = measured_curve(rig_class, source if filtered_audible else silent, silent)
    except am.InsufficientEvidence as exc:
        assert "reference" in str(exc).lower()
        return
    pytest.fail(f"silent reference yielded {len(gain)} gains from "
                f"{gain.min():.3f} to {gain.max():.3f} dB")

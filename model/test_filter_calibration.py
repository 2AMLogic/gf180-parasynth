"""plan074 B: the versioned filter calibration in the COMMON host conversion.

Every expected word here is #231's recorded value
(docs/scorecard/f1-level/README.md, "Register words recorded at the ladder's
process call"), not recomputed from the code under test.
"""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in ("model", "fpga", "audition", "rtl-sketch"):
    sys.path.insert(0, os.path.join(ROOT, p))

import voice_fx as vf        # noqa: E402

CAL = "surge-type2-clean-v1"


def test_words_are_231s_recorded_words():
    assert vf.ladder_regs(0.0, 1.0) == (0, 170394, 25206)
    assert vf.ladder_regs(0.0, 1.0, CAL) == (0, 42598, 100825)
    exp = vf.FILTER_CALIBRATIONS[CAL]["expected_words_res0_drive1"]
    assert (exp["gain"], exp["ogain"]) == (42598, 100825)


def test_quarter_words_are_recomputed_not_scaled_rounded_words():
    # 170394 / 4 = 42598.5 and 25206 * 4 = 100824: the rounded baseline words
    # scaled would be wrong in the last place on both.
    _, g, og = vf.ladder_regs(0.0, 1.0, CAL)
    assert g != round(170394 / 4) or og != 25206 * 4


def test_default_image_is_unchanged_and_carries_no_calibration_key():
    r = vf.VoiceFx.patch_regs(q=0.0, drive=1.0)
    assert "filter_calibration" not in r
    assert (r["gain"], r["ogain"]) == (170394, 25206)


def test_selected_image_records_its_calibration():
    r = vf.VoiceFx.patch_regs(q=0.0, drive=1.0, filter_calibration=CAL)
    assert r["filter_calibration"] == CAL
    assert (r["gain"], r["ogain"]) == (42598, 100825)


@pytest.mark.parametrize("kw", [dict(filter_calibration="surge-type2-clean-v2"),
                                dict(calibration=CAL), dict(filter_calib=CAL)])
def test_unknown_or_misspelt_calibration_refuses(kw):
    with pytest.raises(vf.CalibrationError):
        vf.VoiceFx.patch_regs(q=0.0, drive=1.0, **kw)


def test_out_of_range_word_refuses_where_legacy_clamps():
    # gain = 42598 * drive exceeds 20 bits above drive ~24.6: legacy clamps,
    # the calibration refuses.
    assert vf.ladder_regs(0.0, 30.0)[1] == (1 << 20) - 1
    with pytest.raises(vf.CalibrationError, match="refused, not clamped"):
        vf.ladder_regs(0.0, 30.0, CAL)
    with pytest.raises(vf.CalibrationError):
        vf.ladder_regs(2.0, 1.0, CAL)          # k = 131072, 18 bits


def test_image_naming_calibration_with_legacy_words_is_refused():
    v = vf.VoiceFx()
    r = vf.VoiceFx.patch_regs(q=0.0, drive=1.0, filter_calibration=CAL)
    r_bad = {**r, "gain": 170394, "ogain": 25206}
    with pytest.raises(vf.CalibrationError, match="not its words"):
        v._apply_patch(r_bad)
    v._apply_patch(r)                           # the honest image is accepted


def test_calibrated_words_change_the_audio():
    """The calibration is not inert: same note, different ladder output."""
    outs = []
    for cal in (None, CAL):
        v = vf.VoiceFx()
        kw = {} if cal is None else {"filter_calibration": cal}
        v.note(45, 0.05, cutoff=(250, 4000), q=0.0, drive=1.0, **kw)
        outs.append(np.asarray(v.trace["ladder"]))
    assert np.count_nonzero(outs[0] != outs[1]) > len(outs[0]) // 2


def test_spi_resonance_knob_keeps_the_calibration():
    import spi_host as sh
    image = vf.VoiceFx.patch_regs(q=0.0, drive=1.0, filter_calibration=CAL)
    h = sh.MusicHost(dict(image))
    h.knob(0, "resonance", 0.5)
    assert (h.regs["gain"], h.regs["ogain"]) == vf.ladder_regs(0.5, 1.0, CAL)[1:]
    legacy = sh.MusicHost(vf.VoiceFx.patch_regs(q=0.0, drive=1.0))
    legacy.knob(0, "resonance", 0.5)
    assert (legacy.regs["gain"], legacy.regs["ogain"]) == vf.ladder_regs(0.5, 1.0)[1:]

"""Known answers and controls for tools/cymbal_tone_realisation.py (#369/#396).

Three kinds of test, and the middle kind is the one that matters:

  * closed-form known answers -- a single RC pole is 3.01 dB down at its own
    corner, a two-pole cascade's coefficients are p1+p2 and -p1 p2, a
    coefficient that will not fit its register raises rather than clipping;
  * cross-checks against numbers this tool did not produce -- the LEVEL
    stage's 2-20 kHz tilt as `tools/werner_fig4.py` measured it off Figure 10,
    and the tone stage's as step 4 recorded it in
    `model/cymbal_candidate.TONE_TILT_2K_20K_DB`;
  * controls that must fail, as the properties x defects matrix, including
    CAND2_LEVEL_ONLY -- the realisation that actually shipped as candidate 2
    and measured a negative -- so the bound stays discriminating rather than
    decorative.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import cymbal_candidate as cc                  # noqa: E402
import cymbal_tone_realisation as ct           # noqa: E402
import modal_fixed as mf                       # noqa: E402
import werner_fig4 as wf                       # noqa: E402
import werner_fig9 as w9                       # noqa: E402

SR = 48000


@pytest.fixture(scope="module")
def figs():
    fig9 = w9.from_artifact()[0]
    return fig9, ct.tone_poles(fig9), ct.level_corner_hz()


# ---- closed-form known answers --------------------------------------------


def test_a_single_analog_pole_is_3_01_db_down_at_its_own_corner():
    f = 1511.2
    s = 2j * np.pi * np.array([1.0, f])
    w = 2 * math.pi * f
    db = 20 * np.log10(np.abs(w / (s + w)))
    assert db[0] == pytest.approx(0.0, abs=0.01)
    assert db[1] - db[0] == pytest.approx(-3.0103, abs=0.01)


def test_the_matched_z_pole_is_3_db_down_at_the_corner_it_was_given():
    """The bank's mapping is z = exp(-2 pi f / SR); at f << SR it must put the
    half-power point at the frequency it was given.

    The reference has to be DC, not "the lowest frequency in the sweep". This
    assertion first read 1542.5 Hz against 1511.2 -- 2.07 % -- because it
    normalised at 200 Hz, which is itself 0.075 dB down. Near a half-power
    point the curve is steep enough to turn 0.075 dB into 31 Hz, which is the
    same normalisation trap as #101's window leakage in miniature.

    Against DC the answer is 1516.2 Hz: matched-z puts the half-power point
    0.33 % high at f / SR = 1/32, and that residual is the discretisation
    error, not a bug. It is two orders of magnitude below the +-50 % the
    reference gives for the 808's own unit-to-unit component spread.
    """
    f = 1511.2
    a1, a2 = cc.real_pole_regs([f])
    assert a2 == 0
    hz = np.linspace(1.0, 6000.0, 60000)
    db = ct.section_db(a1 / (1 << 24), 0.0, mf.RAW, hz)
    db -= float(ct.section_db(a1 / (1 << 24), 0.0, mf.RAW, np.array([0.0]))[0])
    corner = float(hz[int(np.argmin(np.abs(db + 3.0103)))])
    assert corner == pytest.approx(f, rel=0.005), corner


def test_two_real_poles_pack_as_their_sum_and_minus_their_product():
    f1, f2 = cc.HH3_P1_HZ, 1511.2
    p1, p2 = (math.exp(-2 * math.pi * f / SR) for f in (f1, f2))
    a1, a2 = cc.real_pole_regs([f1, f2])
    assert a1 == round((p1 + p2) * (1 << 24))
    assert a2 == round(-p1 * p2 * (1 << 24))


def test_one_pole_matches_what_revision_2_wrote_for_hh3s_third_pole():
    """Revision 2 wrote a1 = round(r * 2^24), a2 = 0 by hand; the helper must
    reproduce it exactly, or revision 3 would move a value it did not intend to."""
    r = math.exp(-2 * math.pi * cc.HH3_P1_HZ / SR)
    assert cc.real_pole_regs([cc.HH3_P1_HZ]) == (int(round(r * (1 << 24))), 0)


def test_a_coefficient_that_will_not_fit_its_register_raises():
    with pytest.raises(ValueError):
        cc.real_pole_regs([0.0, 0.0])          # two poles at DC: a1 = 2.0, one LSB over
    with pytest.raises(ValueError):
        cc.real_pole_regs([100.0, 200.0, 300.0])


def test_the_bank_has_no_numerator_code_for_a_single_zero():
    """Why the low and decay bands drop BOTH stages instead of keeping one
    zero: the decode has (1-z^-1)^0, ^2 and ^3 (and 1-z^-2), never ^1."""
    hz = np.array([1000.0, 10000.0])
    for code in (mf.RAW, mf.BP, mf.HP, cc.HP3):
        n = ct.numerator(code, hz)
        assert not np.allclose(np.abs(n), np.abs(1 - ct._z(hz)))


# ---- cross-checks against numbers this tool did not produce ---------------


def test_the_level_model_reproduces_figure_10s_own_measured_tilt(figs):
    """`werner_fig4.py` measured the LEVEL buffer's 2-20 kHz tilt off the
    digitised envelope; our single-pole model of it must agree."""
    blob = json.loads(wf.ARTIFACT.read_text())
    theirs = float(blob["level_stage"]["tilt_2k_to_20k_db"])
    corner = float(blob["level_stage"]["one_pole_corner_hz"])
    s = 2j * np.pi * np.array([2000.0, 20000.0])
    w = 2 * math.pi * corner
    ours = np.diff(20 * np.log10(np.abs(s / (s + w))))[0]
    assert ours == pytest.approx(theirs, abs=0.3), (ours, theirs)


def test_the_net_tilt_equals_step_4s_two_separately_measured_halves(figs):
    """Step 4 recorded LEVEL +16.6 dB and Ht3 -17.7 dB over 2-20 kHz, net
    -1.1 dB, from two different figures. Recompute the net and land there."""
    _, poles, corner = figs
    blob = json.loads(wf.ARTIFACT.read_text())
    want = float(blob["level_stage"]["tilt_2k_to_20k_db"]) + cc.TONE_TILT_2K_20K_DB
    got = ct.net_tilt_db("short", poles, corner)
    assert got == pytest.approx(want, abs=0.3), (got, want)
    assert got == pytest.approx(-1.1, abs=0.2)


def test_the_tone_model_reproduces_werner_fig9s_published_tilt_table(figs):
    fig9, poles, corner = figs
    tilt = w9.tilt_table(fig9)
    for band, name in ct.BAND_OF.items():
        ours = ct.analog_target_db(band, ct.THIRDS, poles, corner, level=False)
        ours -= float(np.interp(1000.0, ct.THIRDS, ours))
        theirs = np.array([v for _, v, _ in tilt[name]])
        assert np.max(np.abs(ours - theirs)) < ct.TILT_TOL_DB, band


def test_the_recorded_shape_errors_in_the_model_match_the_tool(figs):
    """`model/cymbal_candidate.TONE_REALISATION` quotes the error of each
    band's realisation in its own comment; a comment that drifts from the tool
    is the failure mode docs/failure-modes.md calls status carried in prose."""
    _, poles, corner = figs
    for band in ct.BANDS:
        got = ct.shape_error(band, poles, corner)["shape_max_db"]
        assert got == pytest.approx(cc.TONE_REALISATION[band]["shape_err_db"], abs=0.01), band


def test_the_level_corner_in_the_model_is_the_artifacts(figs):
    _, _, corner = figs
    assert cc.LEVEL_CORNER_HZ == pytest.approx(corner, abs=1.0)


# ---- the realisation, and the bank that has to hold it --------------------


def test_the_integer_bank_reproduces_the_named_poles(figs):
    _, poles, _ = figs
    a1, a2, code, hz = ct.section_regs("short", poles)
    probe = ct.THIRDS[(ct.THIRDS >= 1000.0) & (ct.THIRDS <= 16000.0)]
    got = ct.bank_impulse_db(a1, a2, code, probe)
    den = np.ones_like(ct._z(probe))
    for f in hz:
        den = den * (1 - math.exp(-2 * math.pi * f / SR) * ct._z(probe))
    want = ct._db(ct.numerator(code, probe) / den)
    assert np.max(np.abs((got - got[0]) - (want - want[0]))) < ct.BANK_TOL_DB


def test_the_shipped_image_is_the_thing_measured(figs):
    _, poles, _ = figs
    ok, detail = ct._image_bind(poles)
    assert ok, detail


def test_the_candidate_writes_no_hp3_so_the_shared_decode_need_not_change():
    img = dict(cc.candidate_kit())
    import drums_fx as dx
    codes = {img[dx.A_MODE + m * dx.MODE_STRIDE + 3]
             for m in (cc.M_CYH1, dx.M_CYHI, cc.M_CYH3, cc.M_CYH3B)}
    assert cc.HP3 not in codes, codes


def test_every_band_is_inside_the_bound_and_revision_2_was_not(figs):
    """The bound has to be satisfiable AND discriminating: revision 2 realised
    the same two stages as the LEVEL zero alone, and fails it in every band."""
    _, poles, corner = figs
    for band in ct.BANDS:
        ours = ct.shape_error(band, poles, corner)["shape_max_db"]
        rev2 = ct.shape_error(band, poles, corner, defect="CAND2_LEVEL_ONLY")["shape_max_db"]
        assert ours <= ct.SHAPE_BOUND_DB, (band, ours)
        assert rev2 > ct.SHAPE_BOUND_DB, (band, rev2)


def test_the_active_range_excludes_where_the_band_has_no_energy(figs):
    """The decay band is 65 dB down at 1 kHz, so its 5.5 dB deviation there
    cannot reach the residual and must not be counted."""
    _, poles, corner = figs
    e = ct.shape_error("decay", poles, corner)
    assert e["active_hz"][0] >= 5000.0
    assert abs(e["deviation_db"][0]) > ct.SHAPE_BOUND_DB      # 1 kHz, outside the range
    assert e["shape_max_db"] <= ct.SHAPE_BOUND_DB


# ---- controls that must fail ---------------------------------------------


EXPECTED = {
    #  defect              the properties it MUST move
    "SIGN_A1":            {"shape-bound", "bank-exact", "image-bind"},
    "SWAP_BANDS":         {"tone-tilt", "image-bind"},
    "NO_LEVEL_STAGE":     {"net-cancel", "shape-bound"},
    "CAND2_LEVEL_ONLY":   {"shape-bound", "image-bind"},
    "HP_POLE_NOT_LP":     {"image-bind"},
}


@pytest.mark.parametrize("defect", sorted(EXPECTED))
def test_each_control_moves_exactly_the_properties_it_should(defect, figs):
    fig9, poles, corner = figs
    got = ct.properties(poles, corner, fig9, defect=defect)
    moved = {p for p, (ok, _) in got.items() if not ok}
    assert moved == EXPECTED[defect], (defect, moved, EXPECTED[defect])


def test_a_uniform_gain_change_is_blind_everywhere_by_construction(figs):
    """The level rule renormalises each band, so a suite that saw a uniform
    6 dB here would be reading a level where it claims to read a shape."""
    fig9, poles, corner = figs
    got = ct.properties(poles, corner, fig9, defect="UNIFORM_6DB")
    assert all(ok for ok, _ in got.values()), got


def test_the_clean_gate_passes_and_every_control_is_caught(figs):
    fig9, poles, corner = figs
    ok, lines = ct.check(poles, corner, fig9)
    assert ok, "\n".join(lines)
    assert any("5/5 controls" in ln for ln in lines), "\n".join(lines)


def test_an_absent_artifact_refuses_rather_than_answering(tmp_path):
    with pytest.raises(ct.Refused):
        ct.level_corner_hz(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"level_stage": {}}))
    with pytest.raises(ct.Refused):
        ct.level_corner_hz(bad)

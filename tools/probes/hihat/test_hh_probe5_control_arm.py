"""The `control` arm's per-band ratio must refuse two silent renders (#1743).

`hh_probe5.main()` compares the cascade's transparent stage against the 2-pole
render band by band as `20*log10(max(c, 1e-30) / max(r, 1e-30))` and passes the
arm when the worst compared band is within 0.5 dB. Two exact zeros read 0.0 dB
in every band, so the PER-BAND EXPRESSION read "transparent" on a
`decay_band_only` render that is silent on BOTH arms. (The enclosing gate did
not pass: its total, `math.log10(0)`, raised `ValueError: math domain error`
on the merge base. The defect was a crash that named nothing, not a pass.)
The repaired arm refuses with a `Refused` that says "silent". The ratios are
of ENERGIES (sums of squared samples), so the dB conversion is 10*log10, not
20*log10 (the factor of two predates #1743 and refused a legal 0.30 dB gain). Neither `render()`
nor `band_energy_abs()` refuses a silent record, and the CY_FIT precondition at
the top of `main()` covers the full kit only, not these two renders.

Measured on the pre-fix tree (origin/main @ 3309dd59, 2026-11-28): the
expression on `er = ec = zeros(6)` returned `[0.0] * 6`.

`main()` needs the Fischer corpus to reach the arm, so the ratio is a named
helper and these tests drive it directly. It raises `hh_probe5.Refused`, which
is `refusal.Refused` and, by #1640, an ordinary exception at the CLI.

The paired positive matters as much as the control: 0.0 dB is still the RIGHT
answer for two live renders that are genuinely equal, which is what the arm is
for. The refusal must be a refusal of silence, not of zero.
"""
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import hh_probe5                                # noqa: E402

LIVE = np.array([1e-9, 0.5, 0.3, 0.1, 0.05, 0.01])   # <2 kHz is ~100 dB down, not zero


def test_control_arm_ratio_refuses_two_silent_renders():
    """CONTROL (#1743): red on the pre-fix tree, where the inline expression
    returned 0.0 for every band and the arm passed."""
    z = np.zeros(len(hh_probe5.CY_BANDS))
    with pytest.raises(hh_probe5.Refused, match="silent"):
        hh_probe5._control_arm_db(z, z)


def test_control_arm_ratio_refuses_a_silent_reference_even_when_the_control_is_live():
    """The 2-pole render is the reference: with no energy in one of its bands
    there is nothing for the control to be "within 0.5 dB" of."""
    ec = LIVE.copy()
    er = LIVE.copy()
    er[3] = 0.0
    with pytest.raises(hh_probe5.Refused, match="2-pole"):
        hh_probe5._control_arm_db(er, ec)


def test_control_arm_ratio_refuses_a_non_finite_either_side():
    z = np.zeros(6)
    for bad in (np.nan, np.inf):
        er, ec = LIVE.copy(), LIVE.copy()
        er[2] = bad
        with pytest.raises(hh_probe5.Refused, match="non-finite"):
            hh_probe5._control_arm_db(er, ec)
        ec[2] = bad
        with pytest.raises(hh_probe5.Refused, match="non-finite"):
            hh_probe5._control_arm_db(LIVE.copy(), ec)
        with pytest.raises(hh_probe5.Refused):
            hh_probe5._control_arm_db(er, z)


def test_control_arm_ratio_still_reads_0_db_for_two_equal_live_renders():
    """Paired positive: "equal" is still 0 dB -- but only when both sides are
    real. Also a known answer for a deliberate 3 dB: control = 2-pole / 2."""
    db = hh_probe5._control_arm_db(LIVE, LIVE)
    assert db == [0.0] * 6
    half = hh_probe5._control_arm_db(LIVE, LIVE / 2.0)
    assert half == pytest.approx([10 * np.log10(0.5)] * 6)        # -3.0103 dB, an ENERGY ratio
    assert half[0] == pytest.approx(-3.0103, abs=1e-4)


def test_control_arm_ratio_of_a_band_the_control_kills_is_minus_inf_not_a_floor():
    """One side zero is not silence of the arm: a control that removes a band
    the 2-pole renders is -inf dB, the exact answer, and `main()`'s 0.5 dB gate
    refuses it as a non-transparent stage exactly as before."""
    ec = LIVE.copy()
    ec[2] = 0.0
    db = hh_probe5._control_arm_db(LIVE, ec)
    assert db[2] == float("-inf")
    assert np.isfinite(np.delete(db, 2)).all()


def test_control_arm_reads_a_known_sample_gain_through_the_real_filter_bank():
    """CONTROL: a 0.30 dB AMPLITUDE gain change is 0.30 dB in every band and in
    the total, inside the 0.5 dB allowance. The old 20*log10 of an energy ratio
    read -0.60 dB per band and refused it."""
    x = np.random.default_rng(1743).normal(size=4096)
    y = x * 10 ** (-0.30 / 20)
    db = hh_probe5._control_arm_db(hh_probe5.band_energy_abs(x), hh_probe5.band_energy_abs(y))
    assert db == pytest.approx([-0.30] * len(hh_probe5.CY_BANDS), abs=0.01)
    assert hh_probe5._control_total_db(x, y) == pytest.approx(-0.30, abs=1e-6)
    assert abs(hh_probe5._control_total_db(x, x * np.sqrt(0.5)) + 3.0103) < 1e-3


def test_control_total_of_a_muted_control_is_minus_inf_not_a_math_domain_error():
    """CONTROL: live reference, all-zero control. Used to raise `ValueError`
    from `math.log10(0)` before the 0.5 dB gate could refuse it."""
    x = np.random.default_rng(1743).normal(size=4096)
    assert hh_probe5._control_total_db(x, np.zeros_like(x)) == float("-inf")


def test_control_total_refuses_a_silent_reference_and_non_finite_input():
    x = np.random.default_rng(1743).normal(size=64)
    with pytest.raises(hh_probe5.Refused, match="silent"):
        hh_probe5._control_total_db(np.zeros(64), np.zeros(64))
    with pytest.raises(hh_probe5.Refused, match="non-finite"):
        hh_probe5._control_total_db(x, np.where(np.arange(64) == 3, np.nan, x))

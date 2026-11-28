#!/usr/bin/env python3
"""`probe_tom_numerator.deadband`: a silent block must not pass the departure
check (#1743).

THE DEFECT, MEASURED ON main @ 3309dd59 (2026-11-28, by running rather than
reading). The per-block comparison of the fixed-point ring against the float
reference was

    np.abs(20 * np.log10(np.maximum(e, 1e-9) / np.maximum(ef, 1e-9))) > 3.0

-- the same epsilon on both sides, so a block in which BOTH rings are exactly
zero reads 0 dB and is counted as "within 3 dB": agreement. Two inputs reach it.

  * the float reference below the floor. With `e = 0` (the fixed ring has died
    to exact zero -- the deadband this function exists to locate) and
    `ef = 1e-10` LSB, every block read 0.0 dB and none exceeded 3.0: the
    departure the function is for was masked by the floor. Unfloored it is
    -inf in every block.
  * an unrung ring. `deadband(levels=(0,))` -- no excitation at all -- returned
    `{"0": {"departs_at_ms": None, "ends_at_exact_zero": True,
    "fixed_tail_peak_lsb": 0.0}}`: "never departs from the float", on a ring
    that never rang. `departs_at_ms: None` is the same answer a perfectly
    tracking ring gets.

THE KNOWN ANSWER, independent of anything this repository models: 20*log10 of
a ratio is defined only when the denominator is positive. Where the float
reference is exactly zero there is nothing to compare the fixed ring against,
so the comparison REFUSES (`rc.Refused`, the module's own, which `main()`
already maps to exit 2 for its other paths). Where only the fixed ring is zero
the ratio is exactly 0, i.e. -inf dB: a departure, and the honest one.

`main()` calls `deadband()` BEFORE its `except refusal.Refused` boundary, so
the refusal is handled at that call site too -- otherwise it would escape as
an exit-1 traceback, the #1183 inversion this module already repaired once.

The default table is pinned as the paired positive: the repair must not move
a single `departs_at_ms`.
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import refusal                                     # noqa: E402
import run_case as rc                              # noqa: E402
import probe_tom_numerator as ptn                  # noqa: E402


def test_the_module_refusal_is_the_common_base():
    assert issubclass(rc.Refused, refusal.Refused)


def test_departure_ratio_refuses_a_block_whose_float_reference_is_zero():
    """CONTROL (#1743): red on the pre-fix tree, where the expression returned
    an array of 0.0 for this input."""
    z = np.zeros(50)
    with pytest.raises(rc.Refused, match=r"silent or non-finite in 50 of 50 blocks"):
        ptn._departures_db(z, z)
    # A partial case: the count must be the BAD blocks (3), not ~(good count).
    ef = np.array([1.0] * 47 + [0.0, 0.0, 0.0])
    with pytest.raises(rc.Refused, match=r"silent or non-finite in 3 of 50 blocks"):
        ptn._departures_db(np.ones(50), ef)


def test_departure_ratio_of_a_dead_fixed_ring_is_minus_inf_not_0_db():
    """CONTROL (#1743): red on the pre-fix tree -- `e = 0, ef = 1e-10` read
    0.0 dB in all 50 blocks and none cleared the 3 dB departure threshold.
    Unfloored, a fixed ring at exact zero against a live reference has
    departed by exactly -inf dB in every block."""
    dead = ptn._departures_db(np.zeros(50), np.full(50, 1e-10))
    assert np.all(np.isneginf(dead))
    assert np.all(np.abs(dead) > 3.0)


def test_departure_ratio_refuses_a_non_finite_input():
    z = np.zeros(50)
    for bad in (np.nan, np.inf):
        e, ef = z.copy(), z.copy() + 1.0
        e[7] = bad
        with pytest.raises(rc.Refused, match="non-finite"):
            ptn._departures_db(e, ef)
        ef[7] = bad
        with pytest.raises(rc.Refused, match="silent or non-finite"):
            ptn._departures_db(z + 1.0, ef)


def test_departure_ratio_known_answer_is_the_db_of_the_ratio():
    """sin-free known answer: 20*log10(e/ef), with no floor anywhere. A fixed
    ring at half the float is -6.02 dB in every block, and 0.0 dB only where
    the two genuinely agree."""
    ef = np.full(8, 4.0)
    e = np.array([4.0, 2.0, 1.0, 4.0, 2.0, 1.0, 4.0, 2.0])
    got = ptn._departures_db(e, ef)
    assert got[0] == 0.0
    assert got[1] == pytest.approx(20 * np.log10(0.5))
    assert got[2] == pytest.approx(20 * np.log10(0.25))
    assert [int(i) for i in np.flatnonzero(np.abs(got) > 3.0)] == [1, 2, 4, 5, 7]


def test_deadband_refuses_an_unrung_ring_rather_than_reporting_no_departure():
    """CONTROL (#1743): red on the pre-fix tree, which returned
    `departs_at_ms: None` for a level of 0."""
    with pytest.raises(rc.Refused, match="silent"):
        ptn.deadband(levels=(0,))


def test_deadband_default_table_is_unchanged_by_the_repair():
    """Paired positive, measured on the pre-fix tree (origin/main @ 3309dd59,
    2026-11-28): where the fixed ring first departs from the float, in ms."""
    res = ptn.deadband()
    assert {k: v["departs_at_ms"] for k, v in res.items()} == {
        "100": 10, "1000": 230, "10000": 410, "100000": 640}
    assert all(v["float_level_there_lsb"] is not None for v in res.values())


def test_main_reports_a_deadband_refusal_as_refused_not_as_a_traceback(tmp_path, monkeypatch, capsys):
    """The caller: `deadband()` runs before main()'s refusal boundary, so a
    refusal raised inside it must be caught at the call site -- exit 2 with
    the reason, and no report written (a refused run must not look complete)."""
    orig = ptn.deadband
    monkeypatch.setattr(ptn, "deadband", lambda: orig(levels=(0,)))
    out = tmp_path / "deadband.json"
    code = ptn.main(["--explain", "deadband", "--out", str(out)])
    captured = capsys.readouterr()
    assert code == 2
    assert "REFUSED:" in captured.out and "Traceback" not in captured.err
    assert not out.exists()


def test_deadband_counts_the_last_complete_block_at_the_exact_boundary():
    """CONTROL (#1743 review): `range(0, n - blk, blk)` dropped the only complete
    block of an n == blk record, so `levels=(0,)` returned departs_at_ms=None --
    the absence of a comparison read as agreement. At 481 samples it refused."""
    blk = int(0.01 * ptn.dx.SR)
    for n in (blk, blk + 1, 2 * blk):
        with pytest.raises(ptn.rc.Refused):
            ptn.deadband(levels=(0,), n=n)


def test_deadband_refuses_a_record_shorter_than_one_block():
    blk = int(0.01 * ptn.dx.SR)
    with pytest.raises(ptn.rc.Refused, match="shorter than one"):
        ptn.deadband(levels=(100,), n=blk - 1)

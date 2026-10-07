#!/usr/bin/env python3
"""Glide slew at and around the 0x800000 / 0xFFFFFF increment boundaries (#247).

SCOPE, stated plainly: this is the MODEL half only. It checks
`voice_fx.OscFx.slew` against an independent restatement of contract 6.7 and
against one hand-computed transition. It does NOT exercise rtl-sketch/
voice_dp.v, so it says nothing about the reported RTL mismatch; that needs the
pinned build box (see docs/deadline/glide-247.md). It exists so the build-box
regression has an independently grounded expectation to compare against, and
so a model-side defect at these increments is excluded or found first.

The reference below is written from the contract text with `divmod` and
explicit branches rather than the model's shifts/min/max, so a shared
truncation cannot make the two agree by construction.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pytest
import voice_fx as vf

TOP = 0xFFFFFF          # largest register-legal increment
HALF = 0x800000         # 2^23: Nyquist
ACC_MAX = TOP << 8      # 0xFFFFFF00: the largest legal Q24.8 accumulator

# Boundary increments: both sides of 2^23, the extremes, and the issue's own.
INCS = [0, 1, 0x7FFFFF, HALF, 0x800001, 0xC00000, 0xC80000, 0xD00000,
        0xF00000, 0xF80000, 0xFF0000, 0xFFFFFE, TOP]
# zero (snap), the floor (1), the reference rate, a fast and the maximum rate.
RATES = [0, 1, 2692, 1 << 20, (1 << 24) - 1]


def ref_slew(acc, tgt_inc, glide, n):
    """Contract 6.7, restated. Returns (increments seen, final acc, max acc)."""
    tgt = tgt_inc * 256
    if glide == 0:
        acc = tgt            # SET_INC with GLIDE = 0 snaps at once (5.2)
    seen, peak = [], acc
    for _ in range(n):
        seen.append(divmod(acc, 256)[0])
        if acc != tgt:
            if glide == 0:
                acc = tgt
            else:
                d = max(1, divmod(acc * glide, 1 << 24)[0])
                acc = min(tgt, acc + d) if tgt > acc else max(tgt, acc - d)
        peak = max(peak, acc)
    return seen, acc, peak


def model_slew(start, tgt, glide, n):
    o = vf.OscFx("saw")
    o.set_inc(start, jump=True)
    o.set_inc(tgt, jump=False, glide=glide)
    out = o.slew(n, glide)
    return list(map(int, out)), o.inc_acc


def _pairs():
    return [(a, b) for a in INCS for b in INCS]


@pytest.mark.parametrize("glide", RATES)
def test_model_matches_independent_restatement(glide):
    n = 64
    for a, b in _pairs():
        seen, acc, peak = ref_slew(a * 256, b, glide, n)
        got, gacc = model_slew(a, b, glide, n)
        assert got == seen, (a, b, glide)
        assert gacc == acc, (a, b, glide)
        assert peak <= ACC_MAX, "accumulator exceeded 32 bits' legal range"


def test_hand_computed_first_step():
    # 0xC00000 << 8 = 192 * 2^24 exactly, so d = 192 * 2692 = 516864 with no
    # truncation to hide behind: a hand number, not read off either side.
    acc = 0xC00000 << 8
    assert acc == 192 << 24
    _, gacc = model_slew(0xC00000, 0xFF0000, 2692, 1)    # one frame = one step
    assert gacc == acc + 192 * 2692 == 3221742336
    got, _ = model_slew(0xC00000, 0xFF0000, 2692, 2)
    assert got == [0xC00000, (acc + 516864) >> 8]


def test_exact_landing_and_no_overshoot_across_2_23_and_near_max():
    for a, b in [(0x7FFFF0, 0x800010), (0x800010, 0x7FFFF0), (0xFF0000, TOP),
                 (TOP, 0xFF0000), (0x400000, TOP), (TOP, 1)]:
        for g in (2692, 1 << 20, (1 << 24) - 1):
            got, gacc = model_slew(a, b, g, 250000)   # 2692 over 2^24 -> ~1e5 frames
            assert gacc == b * 256, (a, b, g)
            lo, hi = sorted((a, b))
            assert all(lo <= x <= hi for x in got), (a, b, g)


def test_equal_target_and_minimum_step_floor():
    got, gacc = model_slew(0xF00000, 0xF00000, 2692, 5)
    assert got == [0xF00000] * 5 and gacc == 0xF00000 << 8
    # glide = 1 at 2^23: (acc * 1) >> 24 = 128 here, floor never needed; at a
    # small increment the floor must move it by exactly 1 per frame.
    _, acc = model_slew(1, 100, 1, 3)
    assert acc == (1 << 8) + 3


# ---- controls: the defeating inputs this file claims to catch ---------------------------
def _mutant_wrap32(acc, tgt_inc, glide, n):
    """Accumulator wraps at 2^32 (no clamp against the target)."""
    tgt = tgt_inc * 256
    for _ in range(n):
        if acc != tgt:
            d = max(1, (acc * glide) >> 24)
            acc = ((acc + d) if tgt > acc else (acc - d)) & 0xFFFFFFFF
    return acc


def _mutant_inc23(acc, tgt_inc, glide, n):
    """Increment truncated to 23 bits on entry (a 2^23-wide path)."""
    return ref_slew(acc & ((1 << 31) - 1), tgt_inc & 0x7FFFFF, glide, n)[1]


def test_controls_turn_the_comparison_red():
    n = 20000
    wrap = inc23 = 0
    for a, b in _pairs():
        for g in (2692, (1 << 24) - 1):
            want = ref_slew(a * 256, b, g, n)[1]
            wrap += _mutant_wrap32(a * 256, b, g, n) != want
            inc23 += _mutant_inc23(a * 256, b, g, n) != want
    assert wrap > 0 and inc23 > 0     # the comparison sees each; counts are not asserted

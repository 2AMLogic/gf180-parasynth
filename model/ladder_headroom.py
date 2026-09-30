#!/usr/bin/env python3
"""Rung 1 of issue #46: how much is left in the ladder we already ship.

    python3 model/ladder_headroom.py                  # ~3 min, the full table
    python3 model/ladder_headroom.py --quick           # ~40 s
    python3 model/ladder_headroom.py --json build/ladder-headroom.json

Issue #46 asks whether any of four filter algorithms is worth changing to, and
says rung 1 first: **how much is available without changing the algorithm.**
Its one named rung-1 win -- Huovilainen's `fcr` tuning polynomial -- has been
shipped since DR 0011, so what is left is the question this file answers, on
five axes, each with a verdict and the number that decided it:

    tuning-law           can a better cutoff-to-coefficient LAW buy anything
    cutoff-offset        does the cutoff control mean the same thing at every
                         RESONANCE, which is a different question and was not
                         measured after DR 0011 changed the answer
    coefficient-rom      is the 129-entry Q0.16 table the limit
    numerical-precision  is the 24/20 state the limit
    drive                is the gain staging into the tanh in trim

**The result, and why it is a finding rather than a restatement.** DR 0011's
headline metric is the DRIFT of the self-oscillation frequency -- how much the
ratios differ from each other -- and that record says in as many words that a
drift metric is blind to a uniform offset. It guarded that with an injected
uniform skew at ONE operating point, res = 1.05, which is where `CUT_TRIM` was
fitted. Measured here across resonance:

    res    mean offset    drift spread
    1.02      +0.59 %        1.13 pp
    1.05      +0.07 %        1.12 pp
    1.20      -1.90 %        1.08 pp
    1.45      -3.81 %        0.99 pp
    2.00      -5.32 %        1.26 pp

The drift is closed -- 1.0 to 1.3 pp against 7.92 pp before DR 0011, and
Surge Type 2's 0.62 pp is the best any reference achieves. **The offset is
not**: the filter sings 5.3 % (92 cents, most of a semitone) flat of its
commanded cutoff at maximum resonance, and it is 90 cents of travel across the
resonance knob. That is the largest remaining rung-1 error by an order of
magnitude, it is invisible to the metric that motivated DR 0011, and no
tuning POLYNOMIAL can remove it because it is not a function of frequency.
It is also not addressed by any of rungs 2-4: where the loop sings is set by
how the correction was fitted, not by which large-signal model is inside it.

**What the external reference can and cannot settle.** `docs/discrimination.md`
section 8.4 compares our tracking against Surge XT Type 2's, and section 8.3
records that Surge Type 2 clamps resonance at 0.9925 and cannot self-oscillate
at all. So the frozen Surge row is a DECAYING resonant ring, 30-odd dB below
ours in level, and ours is a limit cycle. The drift comparison survives that --
both measure where the resonance sits, against a commanded cutoff read back in
Hz -- but the ABSOLUTE offset comparison does not, and this file refuses to
draw it. `test_the_frozen_surge_row_is_not_a_like_for_like_limit_cycle` asserts
the level gap out of the frozen profile so the caveat cannot be argued about.

METHOD, and where each number's ground truth comes from
------------------------------------------------------
Two instruments, deliberately, because one is cheap and one is true:

  * the **measured** free ring of the fixed-point filter (`reference_rigs
    .OurLadder.ring`, the same probe `docs/discrimination.md` section 8.4 used),
    which is what anybody actually hears, at about a second per point
  * the **linearised loop** of DR 0006 -- four one-poles at the ROM's own
    coefficient and the half-sample feedback delay -- which costs a bisection
    and so can be swept densely, inverted, and refitted

The second is only a proxy, so its accuracy is a reported result and not an
assumption: against DR 0011's six locked measured ratios it is inside 0.12 pp
below 3 kHz and 0.31 pp at 10 kHz (`test_the_predicted_tracking_agrees_with
_the_measured_table_dr_0011_locked`). Every conclusion that rests on the proxy
alone is stated at that accuracy, and the headline offset above is measured.

REFUSED is a first-class outcome. `assert_apparatus` checks that `voice_fx`'s
coefficient ROM and `fixed.tuned_cutoff` still describe one filter and that the
duplicated loop still reproduces `voice_fx.k_onset`, and raises `Refused`
rather than reporting if they do not: exit 2, never a number.

WHAT THIS FILE DELIBERATELY DOES NOT TOUCH
------------------------------------------
The `tanh` table width. Issue #46 puts it out of scope, and DR 0006's `k_comp`
ROM is derived from the table's bin-0 slope, so changing it moves the
resonance compensation (measured: the small-signal resonant peak moves 23.3 ->
20.2 dB). Every sweep here holds `tanh_entries` at the shipped 16.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

import audio_measure as am                                          # noqa: E402
import fixed                                                        # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402
from dsp import SR                                                  # noqa: E402

OVERSAMPLE = 2
FS_OS = SR * OVERSAMPLE

# The frozen reference profile's own cutoff grid, so our rows and its rows are
# the same experiment (model/reference_compare.py CUTOFFS_HZ).
REPORT_CUTS = (100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0, 6400.0)
QUICK_CUTS = (100.0, 800.0, 6400.0)
# Resonances for the offset table. 1.02 is just past DR 0006's onset, 1.05 is
# where DR 0011 fitted CUT_TRIM, 2.00 is the k register's clamp.
REPORT_RES = (1.02, 1.05, 1.10, 1.20, 1.45, 1.70, 2.00)
QUICK_RES = (1.05, 2.00)
# Dense grid for inverting and refitting the tuning law. Log-spaced over the
# whole commanded range, because that is the range the ROM has to cover.
REFIT_CUTS = np.geomspace(vf.CUT_MIN, vf.CUT_MAX, 24)

VERDICTS = ("worth pursuing", "available but small", "no further win available",
            "out of scope")

_PROFILE = os.path.join(HERE, "..", "docs", "reference-compare-results.json")


class Refused(RuntimeError):
    """A precondition of the measurement is unmet, so nothing was measured.

    Distinct from a failed audit: a tool that answers when it cannot is worse
    than one that is absent, because its output looks exactly like data."""


# ===========================================================================
# the coefficient law, and its inverse
# ===========================================================================
def g_exact_q16(cut_hz) -> np.ndarray:
    """The shipped coefficient law at infinite precision: `g = 1 - exp(-2 pi f'
    / f_os)` in Q0.16 as a FLOAT, where `f' = f * CUT_TRIM * fcr(f)` is
    `fixed.tuned_cutoff`. This is the thing the ROM is a quantisation of, and
    so is the known answer the ROM read is measured against."""
    c = np.clip(np.asarray(cut_hz, dtype=np.float64), 20.0, FS_OS * 0.45)
    return (1.0 - np.exp(-2.0 * math.pi * fixed.tuned_cutoff(c) / FS_OS)) * 65536.0


def cutoff_from_g(g_q16) -> np.ndarray:
    """The algebraic inverse: the TUNED cutoff a Q0.16 coefficient represents.
    Exact, so a coefficient error can be quoted in cents of cutoff without a
    derivative approximation anywhere."""
    g = np.clip(np.asarray(g_q16, dtype=np.float64), 1e-9, 65535.9)
    return -FS_OS / (2.0 * math.pi) * np.log(1.0 - g / 65536.0)


def _cents(a, b) -> np.ndarray:
    return 1200.0 * np.log2(np.maximum(np.asarray(a, dtype=np.float64), 1e-12) /
                            np.maximum(np.asarray(b, dtype=np.float64), 1e-12))


# ===========================================================================
# the linearised loop of DR 0006, evaluated at an arbitrary float coefficient
# ===========================================================================
def loop_onset(G: float, stages: int = 4) -> tuple[float, float]:
    """`voice_fx.k_onset`'s bisection, taking the small-signal loop coefficient
    `G = g/2^16 * s0` directly instead of a cutoff and a ROM.

    Same derivation, same bisection, same 80 iterations: `stages` one-poles
    `H1 = G / (1 - (1-G) z^-1)` and the half-sample feedback delay
    `Hfb = (z^-1 + z^-2)/2`, at the oversampled rate. Returns `(k, f_osc)` --
    the feedback at which the loop gain is exactly 1 where its phase is -180
    degrees, and the frequency that happens at.

    Why a duplicate at all: the audit has to evaluate the loop at the EXACT
    float coefficient (to separate the law's error from the ROM's) and at a
    refitted law that has no ROM yet, neither of which `k_onset`'s ROM-indexed
    signature can express. `test_the_linearised_loop_reproduces_voice_fx
    _k_onset` pins the two together to the last bit of a double, and
    `assert_apparatus` refuses if they ever part."""
    a = 1.0 - G

    def phase(w):
        return -stages * math.atan2(a * math.sin(w), 1.0 - a * math.cos(w)) - 1.5 * w

    lo, hi = 0.0, math.pi
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if phase(mid) > -math.pi:
            lo = mid
        else:
            hi = mid
    w = 0.5 * (lo + hi)
    h1 = G / abs(1.0 - a * complex(math.cos(w), -math.sin(w)))
    mag = h1 ** stages * abs(math.cos(0.5 * w))
    return 1.0 / mag, w * FS_OS / (2.0 * math.pi)


def assert_apparatus(rom: np.ndarray = None, slope: float = None) -> None:
    """Every precondition the numbers below rest on, asserted at the point of
    use, raising `Refused` rather than returning a verdict.

    1. the duplicated loop still reproduces `voice_fx.k_onset`
    2. the ROM under test is a quantisation of `fixed.tuned_cutoff`'s law --
       i.e. `voice_fx`'s ROM and `fixed.py`'s float path are ONE filter, which
       is the identity DR 0011 established and the thing a later edit to either
       side would break silently
    3. the `tanh` table is the shipped 16 entries, because DR 0006's `k_comp`
       ROM is derived from its bin-0 slope
    """
    rom = vf.make_g_rom() if rom is None else rom
    s0 = vf.tanh_bin0_slope() if slope is None else slope
    if vf.LADDER_CFG["tanh_entries"] != 16:
        raise Refused(f"tanh table is {vf.LADDER_CFG['tanh_entries']} entries, not the "
                      "shipped 16; DR 0006's k_comp ROM is derived from its bin-0 slope")
    for cut in (30, 800, 10000, 21600):
        k_ref, f_ref = vf.k_onset(cut, rom)
        g = int(vf.g_from_cut(np.array([cut]), rom)[0])
        k, f = loop_onset(g / 65536.0 * s0)
        if not (math.isclose(k, k_ref, rel_tol=1e-9) and math.isclose(f, f_ref, rel_tol=1e-9)):
            raise Refused(f"the duplicated loop has drifted from voice_fx.k_onset at "
                          f"{cut} Hz: {f:.6f} vs {f_ref:.6f} Hz")
    # The ROM is EDGE-sampled, so at an entry's own cutoff the stored word must
    # be the law's value rounded -- nothing else can be within a couple of LSB.
    step = (1 << 15) >> vf.GROM_BITS
    idx = np.arange(1, (1 << vf.GROM_BITS) + 1)
    want = np.clip(np.round(g_exact_q16(idx * step)), 0, 65535)
    if int(np.abs(rom[idx] - want).max()) > 1:
        raise Refused("the coefficient ROM is not a quantisation of "
                      "fixed.tuned_cutoff's law: worst entry differs by "
                      f"{int(np.abs(rom[idx] - want).max())} LSB. voice_fx and fixed.py "
                      "are describing two different filters (DR 0011)")


# ===========================================================================
# axis: the coefficient ROM
# ===========================================================================
def rom_read_error_cents(cuts, bits: int = None, rom: np.ndarray = None) -> dict:
    """What the hardware's ROM read costs, in cents of cutoff, split three ways.

    `total`   the integer read `g_from_cut` performs, against `g_exact_q16`
    `interp`  the chord deficit of linear interpolation between EDGE samples of
              a concave law, evaluated in float -- the part a re-fit of the
              stored entries could remove at zero cost
    `trunc`   what the `>> fb` in `g_from_cut` adds on top, because it floors
              the interpolation instead of rounding it -- up to one LSB, which
              at the bottom of the range is worth about 13 cents

    `interp + trunc == total` is an identity, asserted in the suite."""
    bits = vf.GROM_BITS if bits is None else bits
    rom = vf.make_g_rom(bits=bits) if rom is None else rom
    cuts = np.asarray(cuts, dtype=np.int64)
    fb = 15 - bits
    i = cuts >> fb
    frac = (cuts & ((1 << fb) - 1)) / float(1 << fb)
    g_int = vf.g_from_cut(cuts, rom, bits).astype(np.float64)
    g_flt = rom[i] * (1.0 - frac) + rom[i + 1] * frac
    g_ref = g_exact_q16(cuts)
    f_int, f_flt, f_ref = (cutoff_from_g(x) for x in (g_int, g_flt, g_ref))
    return dict(cuts=cuts, total=_cents(f_int, f_ref),
                interp=_cents(f_flt, f_ref), trunc=_cents(f_int, f_flt))


def rom_size_sweep(bits_range, cuts=None) -> dict:
    """Worst and rms read error in cents against table size. The point of the
    sweep is the FLOOR: past about 513 entries the error stops improving,
    because what is left is the Q0.16 coefficient word and not the entry
    count."""
    cuts = np.arange(vf.CUT_MIN, vf.CUT_MAX + 1, 1) if cuts is None else np.asarray(cuts)
    out = {}
    for bits in bits_range:
        e = rom_read_error_cents(cuts, bits=bits)["total"]
        j = int(np.argmax(np.abs(e)))
        out[bits] = dict(entries=(1 << bits) + 1, rom_bits=((1 << bits) + 1) * 16,
                         worst_cents=float(e[j]), worst_at_hz=int(cuts[j]),
                         rms_cents=float(np.sqrt((e ** 2).mean())))
    return out


def refit_rom_entries(bits: int = None) -> np.ndarray:
    """The 2^bits + 1 Q0.16 entries that minimise the INTERPOLATED read error,
    instead of sampling the law at the bin edges. Same table shape, same read,
    same ROM bits, no datapath change -- a ROM-build-time change of exactly DR
    0011's class. Reported, not shipped: moving the table moves DR 0006's `k`
    ROM, the contract revision and every bit-exact expectation, which is a
    decision record and not an audit."""
    bits = vf.GROM_BITS if bits is None else bits
    cuts = np.arange(vf.CUT_MIN, vf.CUT_MAX + 1, 1)
    fb = 15 - bits
    i = cuts >> fb
    frac = (cuts & ((1 << fb) - 1)) / float(1 << fb)
    n = (1 << bits) + 1
    A = np.zeros((len(cuts), n))
    A[np.arange(len(cuts)), i] = 1.0 - frac
    A[np.arange(len(cuts)), i + 1] = frac
    want = cutoff_from_g(g_exact_q16(cuts))
    e = vf.make_g_rom(bits=bits).astype(np.float64)
    for _ in range(6):                                   # Gauss-Newton on cents
        g = A @ e
        r = _cents(cutoff_from_g(g), want)
        d = 1200.0 / math.log(2.0) / (65536.0 - g) / np.log(1.0 / (1.0 - g / 65536.0))
        delta, *_ = np.linalg.lstsq(A * d[:, None], -r, rcond=None)
        e = e + delta
    return np.clip(np.round(e), 0, 65535).astype(np.int64)


# ===========================================================================
# axis: the tuning law
# ===========================================================================
def predicted_tracking(cuts, rom: np.ndarray = None, slope: float = None) -> dict:
    """`f_osc / commanded` from the linearised loop at the ROM's own
    coefficient: the cheap dense proxy for the measured free ring, accurate to
    0.31 pp at 10 kHz and 0.12 pp below 3 kHz against DR 0011's locked
    measured table."""
    rom = vf.make_g_rom() if rom is None else rom
    s0 = vf.tanh_bin0_slope() if slope is None else slope
    out = {}
    for c in cuts:
        g = int(vf.g_from_cut(np.array([c]), rom)[0])
        out[c] = loop_onset(g / 65536.0 * s0)[1] / float(c)
    return out


def ideal_tuned_ratio(cuts, slope: float = None) -> np.ndarray:
    """The ratio `f' / f` the loop actually needs -- found by inverting it, so
    this is what `CUT_TRIM * fcr(f)` is trying to be. Bisected on the loop's own
    monotone frequency, to a part in 10^9."""
    s0 = vf.tanh_bin0_slope() if slope is None else slope

    def f_osc_of(fp):
        return loop_onset((1.0 - math.exp(-2.0 * math.pi * fp / FS_OS)) * s0)[1]

    out = []
    for c in np.asarray(cuts, dtype=np.float64):
        lo, hi = 0.5 * c, min(3.0 * c, 0.49 * FS_OS)
        for _ in range(90):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if f_osc_of(mid) < c else (lo, mid)
        out.append(0.5 * (lo + hi) / c)
    return np.array(out)


def tuning_ratio_error(cuts, slope: float = None) -> np.ndarray:
    """The shipped `CUT_TRIM * fcr(f)` against the ratio the loop needs. This is
    the tuning LAW's own residual, with the ROM's quantisation taken out: about
    +0.79 % at 30 Hz rising to +1.00 % at 2.2 kHz and falling to -0.76 % at
    21.6 kHz -- a smooth S, which is why a single constant cannot flatten it."""
    cuts = np.asarray(cuts, dtype=np.float64)
    shipped = vf.CUT_TRIM * np.asarray(vf.fcr(cuts), dtype=np.float64)
    return shipped / ideal_tuned_ratio(cuts, slope) - 1.0


def refit_tuning(cuts, degree: int, slope: float = None) -> tuple[np.ndarray, float]:
    """Refit a polynomial in `fc = f / SR` -- `fcr`'s own variable -- to the
    ratio the loop needs, and report the worst relative error it leaves. Degree
    3 is `fcr`'s shape exactly, so it costs what `fcr` costs: one multiply per
    ROM entry at build time, and nothing at all in the datapath."""
    cuts = np.asarray(cuts, dtype=np.float64)
    ratio = ideal_tuned_ratio(cuts, slope)
    p = np.polyfit(cuts / SR, ratio, degree)
    return p, float(np.max(np.abs(np.polyval(p, cuts / SR) / ratio - 1.0)))


# ===========================================================================
# axis: the cutoff offset across resonance -- MEASURED, not predicted
# ===========================================================================
def _ring_ratio(dev, cut: float, res: float, seconds: float = 0.8) -> float:
    """`f_osc / commanded` from the fixed-point filter's own free ring, by the
    same rule `model/reference_compare.py`'s tracking stage uses: the
    zero-crossing frequency when it agrees with the spectral peak inside 2 %,
    the peak otherwise."""
    y = dev.ring(cut, res, seconds=seconds)
    e = am.dominant_frequency(y, 20.0, 20000.0, SR)
    z = am.zero_crossing_frequency(y, SR)
    if z.ok and e.ok and abs(z.value - e.value) / e.value < 0.02:
        f = z.value
    elif e.ok:
        f = e.value
    else:
        raise Refused(f"the ring at {cut:.0f} Hz, res {res} has no measurable frequency")
    return f / float(cut)


def tracking_summary(ratios) -> dict:
    """The three numbers the axes are decided on, kept apart because the first
    two answer different questions and DR 0011 only guarded the second.

    `mean_offset_pct`  how far the whole family sits from the commanded cutoff
                       -- what a player hears as the knob being out of tune
    `spread_pp`        how much the ratios differ FROM EACH OTHER -- the drift,
                       blind to the offset by construction
    `worst_pct`        the larger of the two failure modes at a single point
    """
    v = np.array([r for r in (ratios.values() if isinstance(ratios, dict) else ratios)
                  if r is not None and np.isfinite(r)], dtype=np.float64)
    if len(v) < 2:
        raise Refused("fewer than two usable tracking points")
    return dict(mean_offset_pct=float(100.0 * (v.mean() - 1.0)),
                spread_pp=float(100.0 * (v.max() - v.min())),
                worst_pct=float(100.0 * np.abs(v - 1.0).max()))


def resonance_offset_table(cuts=REPORT_CUTS, resonances=REPORT_RES, cfg=None) -> dict:
    """Measured `f_osc / commanded` over the cutoff grid, at each resonance.
    The headline: the offset walks from +0.6 % to -5.3 % while the drift stays
    at about 1.1 pp, so the two are independent and only one of them was ever
    measured after DR 0011."""
    dev = rr.OurLadder("ours", cfg=cfg)
    out = {}
    for res in resonances:
        ratios = {c: _ring_ratio(dev, c, res) for c in cuts}
        out[res] = dict(tracking_summary(ratios), ratios=ratios)
    return out


def offset_vs_resonance_fit(degree: int, cuts=REPORT_CUTS, resonances=REPORT_RES,
                            table: dict = None) -> tuple[np.ndarray, float]:
    """How much of `resonance_offset_table`'s `mean_offset_pct` is a smooth
    function of the RESONANCE KNOB ALONE -- issue #237's question, asked with a
    fit rather than a description. Fits a degree-`degree` polynomial in `res`
    to the mean offset (independent of cutoff, unlike `refit_tuning`'s fit in
    frequency) and returns `(coefficients, worst residual in percentage
    points)`. The residual is what NO function of resonance alone -- not "a
    better constant", not any polynomial, however high its degree -- could
    remove; a small residual says a per-frame term keyed on resonance (`k`,
    already available every frame in the datapath) could recover most of the
    travel IN PRINCIPLE, which is a different question from whether it is
    free: see `spec/decision-records/0011-cutoff-tuning-polynomial.md`'s
    amendment for issue #237, which sizes the win here and defers building it
    because it is a datapath change, not the ROM-build-time class `fcr` and
    `CUT_TRIM` are."""
    tab = resonance_offset_table(cuts, resonances) if table is None else table
    res = np.array(sorted(tab), dtype=np.float64)
    off = np.array([tab[r]["mean_offset_pct"] for r in res], dtype=np.float64)
    p = np.polyfit(res, off, degree)
    resid = off - np.polyval(p, res)
    return p, float(np.abs(resid).max())


# ===========================================================================
# axis: numerical precision
# ===========================================================================
def state_width_sweep(widths, cuts=QUICK_CUTS, res: float = 1.05) -> dict:
    """Measured tracking against the ladder state's width, holding the `tanh`
    table at the shipped 16 entries. The shipped 24/20 is four bits past the
    plateau, which is the answer: precision is not a lever here, and no
    candidate algorithm can be argued for on the ground that ours is
    arithmetic-limited."""
    out = {}
    for sb, sq in widths:
        dev = rr.OurLadder("ours", cfg=dict(state_bits=sb, state_q=sq))
        ratios = {c: _ring_ratio(dev, c, res) for c in cuts}
        out[(sb, sq)] = dict(tracking_summary(ratios), state_bits=sb, state_q=sq)
    return out


# ===========================================================================
# axis: drive
# ===========================================================================
DRIVE_H3_DB = -40.0


def drive_saturation_dbfs(f0: float = 220.0, cut: float = 12000.0, res: float = 0.1,
                          levels=(-24.0, -18.0, -12.0, -9.0, -8.0, -7.0, -6.0,
                                  -5.0, -3.0, 0.0)) -> float:
    """The input level at which the third harmonic reaches -40 dB: where the
    designed saturation engages, referred to the INPUT so it is comparable
    across devices whatever each one's internal scaling is.

    `docs/discrimination.md` section 8.3 reads our agreement with Arturia Mini
    V3 here (-6.6 against -5.7 dBFS, where Surge's Huovilainen subtype needs
    +18) as the evidence that the gain staging into the tanh is in trim.
    Linearly interpolated in dB between the bracketing levels."""
    dev = rr.OurLadder("ours")
    prev = None
    for db in levels:
        amp = (10.0 ** (db / 20.0))
        y = dev.drive_tone(f0, cut, res, amp)
        s = am.harmonic_signature(y, f_lo=100.0, f0=f0)
        h3 = s.get("h3")
        if h3 is None:
            continue
        if h3 >= DRIVE_H3_DB:
            if prev is None:
                return float(db)
            db0, h0 = prev
            return float(db0 + (DRIVE_H3_DB - h0) * (db - db0) / (h3 - h0))
        prev = (db, h3)
    raise Refused(f"h3 never reached {DRIVE_H3_DB} dB over {levels[0]}..{levels[-1]} dBFS")


# ===========================================================================
# the frozen external reference
# ===========================================================================
def frozen_reference_tracking(device: str, path: str = None) -> dict:
    """One `tracking-*` row out of `docs/reference-compare-results.json`, the
    frozen profile. Never re-rendered: most hosts in this fleet have neither the
    plugins nor `dawdreamer`, and a reference that is re-rendered on demand is
    not a reference (`tools/refprofile.py`)."""
    path = _PROFILE if path is None else path
    try:
        with open(path) as fh:
            rows = json.load(fh)[f"tracking-{device}"]
    except (OSError, KeyError) as exc:
        raise Refused(f"no frozen tracking row for {device!r} in {path}: {exc}") from exc
    cuts = [r["want_hz"] for r in rows if r.get("f_osc")]
    return dict(cuts=cuts,
                ratio={r["want_hz"]: r["f_osc"] / r["want_hz"] for r in rows if r.get("f_osc")},
                rms=[r["rms"] for r in rows if r.get("f_osc")],
                res=rows[0]["res"])


# ===========================================================================
# the report
# ===========================================================================
def audit(quick: bool = False) -> dict:
    """Every axis, each with a verdict from `VERDICTS` and the number that
    decided it. The overall conclusion is derived from the axes, not written."""
    assert_apparatus()
    cuts = QUICK_CUTS if quick else REPORT_CUTS
    resonances = QUICK_RES if quick else REPORT_RES
    refit_cuts = REFIT_CUTS[::3] if quick else REFIT_CUTS

    offsets = resonance_offset_table(cuts, resonances)
    res_lo, res_hi = min(resonances), max(resonances)
    offset_travel_cents = 1200.0 * math.log2(
        (1.0 + offsets[res_lo]["mean_offset_pct"] / 100.0) /
        (1.0 + offsets[res_hi]["mean_offset_pct"] / 100.0))

    law_err = float(np.abs(tuning_ratio_error(refit_cuts)).max())
    p3, refit3 = refit_tuning(refit_cuts, 3)
    p4, refit4 = refit_tuning(refit_cuts, 4)
    law_gain_cents = 1200.0 * math.log2((1.0 + law_err) / (1.0 + refit3))

    rom = rom_size_sweep(range(5, 11))
    shipped = rom[vf.GROM_BITS]
    doubling_cents = abs(shipped["worst_cents"]) - abs(rom[vf.GROM_BITS + 1]["worst_cents"])
    refit_entries = rom_read_error_cents(np.arange(vf.CUT_MIN, vf.CUT_MAX + 1),
                                        rom=refit_rom_entries())["total"]
    entry_refit_cents = abs(shipped["worst_cents"]) - float(np.abs(refit_entries).max())

    widths = ((20, 16), (24, 20), (32, 28)) if quick else \
             ((20, 16), (22, 18), (24, 20), (26, 22), (28, 24), (32, 28))
    state = state_width_sweep(widths, cuts)
    ref_spread = state[(24, 20)]["spread_pp"]
    state_move_pp = max(abs(r["spread_pp"] - ref_spread) for r in state.values())

    drive_dbfs = drive_saturation_dbfs()

    surge = frozen_reference_tracking("surge-huov")
    ours_rev8 = frozen_reference_tracking("ours")
    best_ref_drift = tracking_summary(surge["ratio"])["spread_pp"]
    now_drift = offsets[res_hi]["spread_pp"]

    axes = {
        "tuning-law": dict(
            verdict="available but small" if 3.0 <= law_gain_cents < 25.0 else
                    ("worth pursuing" if law_gain_cents >= 25.0 else
                     "no further win available"),
            deciding_number=round(law_gain_cents, 2), units="cents recoverable",
            detail=dict(shipped_worst_pct=round(100.0 * law_err, 3),
                        refit_cubic_worst_pct=round(100.0 * refit3, 3),
                        refit_quartic_worst_pct=round(100.0 * refit4, 3),
                        refit_cubic_coeffs=[round(float(x), 6) for x in p3],
                        refit_quartic_coeffs=[round(float(x), 6) for x in p4],
                        drift_now_pp=round(now_drift, 2),
                        best_reference_drift_pp=round(best_ref_drift, 2),
                        drift_before_dr_0011_pp=round(
                            tracking_summary(ours_rev8["ratio"])["spread_pp"], 2)),
            why="a cubic of fcr's own shape, refitted to THIS loop instead of the "
                "paper's implementation, costs one multiply per ROM entry at build "
                "time and nothing in the datapath -- but it buys under a tenth of "
                "what the offset axis below does"),
        "cutoff-offset": dict(
            verdict="worth pursuing" if abs(offset_travel_cents) > 20.0 else
                    "no further win available",
            deciding_number=round(abs(offset_travel_cents), 1),
            units="cents of travel across the resonance knob",
            detail={f"res_{r}": dict(mean_offset_pct=round(v["mean_offset_pct"], 2),
                                     spread_pp=round(v["spread_pp"], 2))
                    for r, v in offsets.items()},
            why="CUT_TRIM is one constant fitted at res = 1.05 and the residual it "
                "removes is resonance-dependent, so the cutoff control does not mean "
                "the same thing at every resonance. Invisible to DR 0011's drift "
                "metric by construction, and not addressed by any of rungs 2-4: "
                "where the loop sings is set by how the correction was fitted"),
        "coefficient-rom": dict(
            # Two independent levers, so two criteria. More ENTRIES is the one
            # the issue's framing expects and it is a poor buy. Refitting the
            # entries already there is free, and turns out to beat a table eight
            # times the size -- so the axis is not closed, it is just small and
            # confined to the bottom of the range.
            verdict="worth pursuing" if doubling_cents >= 15.0 else
                    ("available but small" if entry_refit_cents >= 15.0 else
                     "no further win available"),
            deciding_number=round(max(doubling_cents, entry_refit_cents), 1),
            units="cents recoverable (refitting the existing 129 entries, free)",
            detail=dict(
                doubling_the_table_saves_cents=round(doubling_cents, 1),
                shipped_worst_cents=round(shipped["worst_cents"], 1),
                shipped_worst_at_hz=shipped["worst_at_hz"],
                shipped_rms_cents=round(shipped["rms_cents"], 3),
                floor_cents=round(abs(rom[10]["worst_cents"]), 1),
                entry_refit_saves_cents=round(entry_refit_cents, 1),
                sweep={b: dict(entries=v["entries"], rom_bits=v["rom_bits"],
                               worst_cents=round(v["worst_cents"], 1))
                       for b, v in rom.items()}),
            why="the read error is worst at the bottom of the range, where g is a "
                "few hundred LSB, and is under a cent above 1 kHz. Past 513 entries "
                "the sweep FLOORS: what is left is the Q0.16 coefficient word, and "
                "widening that is a datapath change. But linear interpolation between "
                "EDGE samples of a concave law is one-sided, so refitting the 129 "
                "entries already there -- same ROM bits, same read, no datapath "
                "change -- beats a 1025-entry table"),
        "numerical-precision": dict(
            verdict="no further win available" if state_move_pp < 0.05 else
                    "worth pursuing",
            deciding_number=round(state_move_pp, 4),
            units="pp of drift moved by 4 fewer to 8 more state bits",
            detail={f"{sb}/{sq}": round(v["spread_pp"], 3)
                    for (sb, sq), v in state.items()},
            why="the shipped 24/20 state is four bits past the plateau, so the "
                "filter is not arithmetic-limited and no candidate can be argued "
                "for on that ground"),
        "drive": dict(
            verdict="no further win available" if abs(drive_dbfs + 5.7) < 4.0 else
                    "worth pursuing",
            deciding_number=round(drive_dbfs, 2),
            units="dBFS at which h3 reaches -40 dB",
            detail=dict(mini_v3_dbfs=-5.7, ours_section_8_3_dbfs=-6.6,
                        surge_rk_dbfs=-0.6),
            why="input-referred, so it is comparable across devices whatever each "
                "one's internal scaling is. Within a few dB of the dedicated "
                "Minimoog emulation, which is what section 8.3 reads as in trim"),
    }
    worth = [n for n, a in axes.items() if a["verdict"] == "worth pursuing"]
    if worth:
        conclusion = ("Rung 1 is not exhausted: " + ", ".join(worth) +
                      " still has a useful, reproducible win available without "
                      "changing the algorithm, and none of rungs 2-4 addresses it. "
                      "Retain the current filter and take the rung-1 work first.")
    else:
        conclusion = ("Rung 1 is exhausted on every axis measured here; any further "
                      "improvement has to come from the algorithm, i.e. rungs 2-4.")
    return dict(axes=axes, conclusion=conclusion,
                apparatus=dict(
                    proxy="linearised loop of DR 0006, accurate to 0.31 pp against "
                          "DR 0011's locked measured table",
                    external_reference="docs/reference-compare-results.json, frozen; "
                                       "Surge Type 2 cannot self-oscillate, so its "
                                       "tracking row is a decaying ring and only its "
                                       "DRIFT is comparable with ours",
                    tanh_entries=vf.LADDER_CFG["tanh_entries"],
                    grom_bits=vf.GROM_BITS, cut_trim=vf.CUT_TRIM,
                    quick=bool(quick)))


def _print(rep: dict) -> None:
    print("Rung 1 headroom audit -- the ladder we already ship (issue #46)\n")
    for name, ax in rep["axes"].items():
        print(f"  {name:20s} {ax['verdict']:26s} {ax['deciding_number']:>10} {ax['units']}")
    print()
    off = rep["axes"]["cutoff-offset"]["detail"]
    print("  self-oscillation frequency against commanded cutoff, MEASURED:")
    print(f"    {'res':>6} {'mean offset':>13} {'drift spread':>14}")
    for key, v in off.items():
        print(f"    {key.split('_')[1]:>6} {v['mean_offset_pct']:+12.2f} % "
              f"{v['spread_pp']:13.2f} pp")
    rom = rep["axes"]["coefficient-rom"]["detail"]["sweep"]
    print("\n  coefficient ROM read error against table size:")
    print(f"    {'bits':>5} {'entries':>8} {'ROM bits':>9} {'worst':>9}")
    for bits, v in rom.items():
        print(f"    {bits:5d} {v['entries']:8d} {v['rom_bits']:9d} "
              f"{v['worst_cents']:+8.1f} c")
    print("\n  " + rep["conclusion"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true",
                    help="three cutoffs and two resonances instead of seven each")
    ap.add_argument("--json", metavar="PATH", help="write the full report here")
    a = ap.parse_args(argv)
    try:
        rep = audit(quick=a.quick)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    _print(rep)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True)
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

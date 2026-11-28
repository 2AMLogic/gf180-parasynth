#!/usr/bin/env python3
"""The six measurement defects of #101/#103, #118, #119, #108, #139 and #150,
each shown producing its WRONG number beside the repaired one.

    .venv/bin/python tools/probes/estimator_defects.py
    .venv/bin/python tools/probes/estimator_defects.py --all-voices

This file exists because of `tools/probes/hihat/hh_probe4.py`, whose whole job
was to record that an earlier result of 7.2 was actually 30.6. **A record of a
result that looked good and was wrong is worth more than one that was right
first time, and it is exactly the file that gets deleted.** Every "shipped"
function below is a faithful reimplementation of the code as it stood before
the repair, so the red number stays reproducible after the red is gone.

It exits non-zero if any repaired invariant fails, so it is a check and not
only a story.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

import numpy as np
from scipy.signal import butter, sosfiltfilt

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am                                          # noqa: E402
import refusal                                                      # noqa: E402
import run_case as rc                                               # noqa: E402

REFS = rc.configured_refs()
SR = 48000
fails: list[str] = []
refusals: list[str] = []


def check(ok: bool, what: str):
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if not ok:
        fails.append(what)


def check_refused(what: str):
    """A precondition this probe could not establish -- REFUSED, a third
    outcome distinct from OK and FAIL (#805). It is recorded in `refusals`,
    never absorbed into `fails`: a refusal says the comparison could not be
    made at all, which no FAIL verdict says, and it must not be traded down
    to a failed invariant when the numbers could not be produced."""
    print(f"      REFUSED  {what}")
    refusals.append(what)


def verdict_exit_code() -> int:
    """2 = refused precondition (the corpus-absent code, :544-546's
    convention), 1 = a failed invariant, 0 = every invariant held. REFUSED
    is checked first and outranks FAILED (#805 AC3: a refused check exits
    2, never 0 and never 1) -- a probe whose precondition did not hold has
    made no comparison whose FAIL could be trusted."""
    if refusals:
        return 2
    if fails:
        return 1
    return 0


# ===========================================================================
# 1. #101 / #103 -- the lead, and the clamp that was the bug
# ===========================================================================
def prepare_shipped(x, sr):
    """`prepare()` exactly as it stood before the repair. The `max(0, ...)` is
    the defect: an onset closer to the start than 1 ms cannot be given a 1 ms
    lead, so it silently got whatever was there."""
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return x
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > 0.02 * pk))
    lead = max(0, i - int(1e-3 * sr))                       # <-- the clamp
    if lead >= int(5e-3 * sr):
        x = x - float(x[:lead].mean())
    y = x[lead:]
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


def window_shipped(y, sr, t0, t1):
    a = int(t0 * sr)
    b = len(y) if t1 is None else min(len(y), int(t1 * sr))
    return y[a:b]


def split_db_shipped(y, sr, sound, t0, t1):
    lo, hi = rc.BAND[sound]
    return rc.band_ratio_db(window_shipped(y, sr, t0, t1), sr, rc.SPLIT_HZ[sound], lo, hi)


def _prepend_silence_invariance(
        shipped=lambda p, sr: split_db_shipped(prepare_shipped(p, sr), sr,
                                               "LC", 0.0, 0.150),
        repaired=lambda p, sr: rc.DRUM_PLAN["LC"][1][2](rc.prepare(p, sr), sr),
        pads=(0.0, 0.5, 5.0, 500.0)):
    """Prepend-silence invariance on a synthetic strike -- defect 1's own
    measurement, extracted so `tools/probes/test_estimator_defects.py` can
    rig either estimator to refuse (#805 AC8), the seam
    `_trailing_silence_invariance` exposes as `measure=`.

    The BASELINE is asserted at the point of use (#805) -- and BEFORE THE
    FIRST `.value` READ, the table print included. The guard originally
    sat below a print loop that formatted the same readings it protects,
    so a None-encoded refusal (the shipped encoding, pinned by AC7)
    raised TypeError at the print and the guard below was never reached
    -- guard-defeat note #1 from the issue's own write-time audit, one
    loop up. Every reading is computed once here; the guard runs before
    any of them is read; on the guarded path the table print and the
    swing checks consume the guarded set."""
    x = np.zeros(int(0.6 * SR))
    t = np.arange(len(x) - 7) / SR
    x[7:] = np.exp(-t / 0.040) * np.sin(2 * math.pi * 220 * t)
    padded = {p: np.concatenate([np.zeros(int(p * 1e-3 * SR)), x]) for p in pads}
    old = [shipped(padded[p], SR) for p in pads]
    new = [repaired(padded[p], SR) for p in pads]
    base_o, pad_o = old[0], old[1:]
    base_n, pad_n = new[0], new[1:]
    # The BASELINE is asserted at the point of use (#805), above the FIRST
    # `.value` read: a swing measured from or against a refusing reading is
    # not a number, and an unguarded read raised on the shipped encoding
    # (TypeError, NO VERDICT) or printed a plausible nan on the other one.
    readings = [base_o, base_n, *pad_o, *pad_n]
    if any(not e.ok or e.value is None or not math.isfinite(e.value)
           for e in readings):
        bad = next(e for e in readings
                   if not e.ok or e.value is None or not math.isfinite(e.value))
        check_refused(
            f"defect 1's prepend-silence invariance baseline refused "
            f"({bad.reason or 'no reason given'}) -- the swing was NOT "
            "measured, so neither 'shipped really moves' nor 'repaired is "
            "invariant' was checked")
        return
    for pad_ms, o, n in zip(pads, old, new):
        print(f"      prepend {pad_ms:6.1f} ms of silence:  shipped {o.value:8.3f} dB"
              f"   repaired {n.value:8.3f} dB")
    worst_o = max(abs(e.value - base_o.value) for e in pad_o)
    worst_n = max(abs(e.value - base_n.value) for e in pad_n)
    print(f"      shipped swings {worst_o:.3f} dB from prepended silence alone;"
          f" repaired {worst_n:.6f} dB")
    print("   On a synthetic strike the swing is under a dB; on the real congas above")
    print("   it is 3.5-4.8 dB, because how much it is worth depends on how little")
    print("   energy the numerator band holds -- which is the sound, not the filter.")
    check(worst_o > 0.1, "the shipped path really was non-invariant (this is the defect)")
    check(worst_n < 0.01, "the repaired path is invariant to prepended silence")


def defect_1(voices):
    print("\n" + "=" * 78)
    print("1. #101 / #103 -- the two sides of every drum comparison were filtered")
    print("   under different boundary conditions, and nothing said so.")
    print("=" * 78)
    for sr in (48000, 44100):
        bp = rc._bandpass_sos(sr, 20.0, 400.0)
        lp = butter(4, 400 / (sr / 2), btype="lowpass", output="sos")
        print(f"   sr {sr}: 4th-order BAND-pass {len(bp)} sections, padlen "
              f"{rc._sosfiltfilt_padlen(bp)} = {rc._sosfiltfilt_padlen(bp)/sr*1e3:.3f} ms"
              f"   |   4th-order low-pass padlen {rc._sosfiltfilt_padlen(lp)}"
              f" = {rc._sosfiltfilt_padlen(lp)/sr*1e3:.3f} ms")
    print("   #101 and #103 both quote the LOW-pass figure. Every lead budget")
    print("   derived from 0.3 ms was half what it should have been.\n")

    print(f"   {'voice':<6}{'ref lead':>10}{'our lead':>10}{'SHIPPED err':>13}"
          f"{'REPAIRED err':>14}{'tol':>7}{'moved':>8}")
    for v in voices:
        ref_x, ref_sr, rel, _ = rc.load_reference(v, REFS)
        our_x, our_sr = rc.render_drum_solo(v)
        name = next(n for n, _u, _e, _t in rc.DRUM_PLAN[v]
                    if "spectrum" in n or "energy" in n.lower() or "balance" in n)
        est = next(e for n, _u, e, _t in rc.DRUM_PLAN[v] if n == name)
        old = [split_db_shipped(prepare_shipped(x, s), s, v, 0.0, 0.150)
               for x, s in ((our_x, our_sr), (ref_x, ref_sr))]
        new = [est(rc.prepare(x, s), s) for x, s in ((our_x, our_sr), (ref_x, ref_sr))]
        ref_lead = int(np.argmax(np.abs(ref_x) > 0.02 * np.abs(ref_x).max())) / ref_sr * 1e3
        our_lead = 1.0
        oe = old[0].value - old[1].value if old[0].ok and old[1].ok else float("nan")
        ne = new[0].value - new[1].value if new[0].ok and new[1].ok else float("nan")
        print(f"   {v:<6}{ref_lead:>9.2f}m{our_lead:>9.2f}m{oe:>13.3f}{ne:>14.3f}"
              f"{3.0:>7.1f}{ne-oe:>8.3f}")
    print("   (lead in ms of TRUE pre-onset silence the SHIPPED path gave each side;")
    print("    the pad it has to clear is 0.562 ms at 48 k and 0.612 ms at 44.1 k)")

    print("\n   The invariance that would have caught it, without knowing any answer.")
    print("   The strike starts 7 samples in, which is where the Fischer references")
    print("   start: inside the clamp. Prepending silence takes it OUT of the clamp,")
    print("   and the shipped number moves although the sound did not.")
    _prepend_silence_invariance()


# ===========================================================================
# 2. #119 -- a floor quoted as a constant that is not one
# ===========================================================================
def _addsaw(f0, n):
    t = np.arange(n) / SR
    y = sum(np.sin(2 * math.pi * k * f0 * t) / k for k in range(1, int(SR / 2 / f0)))
    return y / np.abs(y).max()


def _naivesaw(f0, n):
    t = np.arange(n) / SR
    y = 2 * ((f0 * t) % 1.0) - 1.0
    return y / np.abs(y).max()


def inharmonic_shipped(x, f0, sr, guard=5):
    """Hann-windowed, as it stood. The docstring quoted its floor as a constant
    "about -54 dB"; the table below is what that constant actually is."""
    n = len(x)
    p = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
    harm = am._harmonic_mask(n, f0, sr, guard)
    return 10.0 * math.log10(max(p[~harm].sum(), 1e-300) / p.sum())


def defect_2(inharmonic=None):
    print("\n" + "=" * 78)
    print("2. #119 / #92 -- the aliasing floor is not -54 dB, it is whatever f0 makes it")
    print("=" * 78)
    inharmonic = inharmonic or am.inharmonic_fraction_db
    n = int(0.5 * SR)
    print(f"   {'f0':>10}{'on a bin':>10}{'HANN floor':>12}{'BH4 floor':>12}"
          f"{'HANN answer':>13}{'BH4 answer':>12}{'answer moved':>14}")
    swing = []
    for f0 in (110.0, 111.0, 111.3, 261.626, 440.0, 441.0, 1000.0):
        onbin = abs(f0 / (SR / n) - round(f0 / (SR / n))) < 1e-9
        free, naive = _addsaw(f0, n), _naivesaw(f0, n)
        fh = inharmonic_shipped(free, f0, SR)
        # The alias-free saw's reading IS the floor, so `fb` asks for the
        # floor ON PURPOSE (`min_headroom_db=None`, the mode the headroom
        # gate's docstring names for exactly this control). The gate's 6 dB
        # minimum refuses a reading at its own floor -- which is every row
        # here -- and this was the one floor-on-purpose caller not updated
        # when the gate landed (d4c89f03; measure_mono_case, measure_saw_
        # alias_after_2x and estimator_domains all were): the print below
        # raised TypeError on the refusal's None and the probe died at its
        # first row, with NO VERDICT for every section after it (#843).
        fb = inharmonic(free, f0, SR, min_headroom_db=None)
        ah = inharmonic_shipped(naive, f0, SR)
        ab = inharmonic(naive, f0, SR)
        swing.append(fh)
        # Guarded ABOVE the first `.value` read, the print included (#805's
        # defeat note): a None-encoded refusal raised TypeError at the
        # print, a nan-carrying one printed a plausible nan row -- and both
        # would have run the floor-vs-answer verdict against a reading that
        # does not exist.
        readings = (fb, ab)
        bad = next((e for e in readings
                    if not e.ok or e.value is None or not math.isfinite(e.value)),
                   None)
        if bad is not None:
            check_refused(
                f"f0 {f0}: the inharmonic-floor row refused "
                f"({bad.reason or 'no reason given'}) -- no row was printed "
                "and the floor-vs-answer verdict did not run against a "
                "reading that does not exist")
            continue
        print(f"   {f0:>10.3f}{str(onbin):>10}{fh:>12.2f}{fb.value:>12.2f}"
              f"{ah:>13.2f}{ab.value:>12.2f}{ab.value-ah:>14.2f}")
        check(abs(fb.detail["headroom_db"]) < 1.0,
              f"f0 {f0}: the measured floor equals the alias-free reading")
    print(f"   the Hann floor swings {max(swing)-min(swing):.1f} dB across these f0."
          f"  A constant would be a lie.")


# ===========================================================================
# 3. #118 -- a level guard cannot see truncation
# ===========================================================================
def t20_shipped(x, sr, lo_db=-5.0, hi_db=-25.0, margin_db=10.0):
    """The level guard as it stood: refuse when the backward-integrated curve's
    LAST value is above -35 dB. The curve of any finite record falls towards
    -inf at its last sample, so this sees almost nothing."""
    e = np.cumsum((np.asarray(x, float) ** 2)[::-1])[::-1]
    L = 10.0 * np.log10(np.maximum(e / e[0], 1e-30))
    tail = float(L[-1])
    if tail > hi_db - margin_db:
        return None, tail
    i_lo, i_hi = int(np.argmax(L <= lo_db)), int(np.argmax(L <= hi_db))
    if i_hi <= i_lo + 8:
        return None, tail
    t = np.arange(i_lo, i_hi) / sr
    slope = np.polyfit(t, L[i_lo:i_hi], 1)[0]
    return (-20.0 / float(slope) if slope < 0 else None), tail


def defect_3():
    print("\n" + "=" * 78)
    print("3. #118 -- the truncation guard was a level, and truncation is a length")
    print("=" * 78)
    tau = 0.040
    exact = math.log(10) * tau * 1e3
    t = np.arange(SR) / SR
    x0 = np.exp(-t / tau) * np.sin(2 * math.pi * 220 * t)
    print(f"   a single exponential, tau {tau*1e3:.0f} ms, exact T20 {exact:.2f} ms\n")
    print(f"   {'record':>9}{'T20 read':>10}{'error':>9}{'tail_db':>10}"
          f"{'SHIPPED guard':>15}{'in T20s':>9}{'LENGTH guard':>14}")
    for sec in (1.0, 0.5, 0.3, 0.25, 0.2, 0.15, 0.1, 0.05):
        y = x0[:int(sec * SR)]
        old, tail = t20_shipped(y, SR)
        new = am.schroeder_t20(y, SR)
        ratio = (new.detail or {}).get("tail_in_t20s", float("nan"))
        print(f"   {sec*1e3:>8.0f}m{(old or 0)*1e3:>10.2f}"
              f"{100*((old or 0)*1e3/exact-1):>8.1f}%{tail:>10.1f}"
              f"{'passed' if old else 'refused':>15}{ratio:>9.2f}"
              f"{'passed' if new.ok else 'REFUSED':>14}")
    old100, tail100 = t20_shipped(x0[:int(0.1 * SR)], SR)
    check(old100 is not None and abs(old100 * 1e3 / exact - 1) > 0.15,
          f"the shipped guard passed a 100 ms cut at {100*(old100*1e3/exact-1):.1f} % error"
          f" with tail_db {tail100:.1f} (it wanted -35)")
    check(not am.schroeder_t20(x0[:int(0.1 * SR)], SR).ok,
          "the length guard refuses that same record")
    check(am.schroeder_t20(x0[:int(0.3 * SR)], SR).ok,
          "and does not refuse one that does contain its decay")


# ===========================================================================
# 4. #108 -- a rig that names a frequency instead of finding one
# ===========================================================================
def tone_ratio_shipped(x, sr, hz_num, hz_den):
    """`tone_ratio_db` as it stood before #108: probe the NOMINAL frequency.

    Deliberately UNGUARDED (#843, docs/verification-rules.md rule 8's stated
    reason): the exhibit's unguarded `.value` reads are part of what it
    exhibits. On a refusing reading the shipped probe raised TypeError --
    NO VERDICT -- and on two partials a rectangular projection at the
    nominal points reads the leakage between them for an answer. Guarding
    these reads would make the exhibit refuse where the shipped code
    crashed, and it would no longer be the code that was wrong.
    `test_estimator_defects.py` pins the faithfulness both ways: the wrong
    answer on two partials, and the TypeError on a refusing reading."""
    a, b = am.tone_amplitude(x, hz_num, sr), am.tone_amplitude(x, hz_den, sr)
    return 20.0 * math.log10(a.value / b.value)


def _defect4_row(label, y, sr):
    """One comparison row of defect 4, extracted so the control can rig the
    repaired-side estimators to refuse without the corpus (#843).

    The guard sits ABOVE the first `.value` read -- the print it protects,
    the defeat defect 1's own note describes, and it covers `lo.value` and
    `hi.value` too, which the issue's enumeration did not name but the same
    statement prints. The EXHIBIT calls stay outside the guard on purpose:
    `tone_ratio_shipped` raising TypeError on a refusing shipped reading is
    the shipped behaviour (see its docstring); what the guard owes the
    reader is that a refusal of the repaired machinery refuses the ROW
    instead of crashing the probe or printing a plausible nan."""
    lo, hi = rc.find_line(y, sr, 540.0), rc.find_line(y, sr, 800.0)
    old = tone_ratio_shipped(y, sr, 800.0, 540.0)
    new = rc.tone_ratio_db(y, sr, 800.0, 540.0)
    d_old = tone_ratio_shipped(y, sr, 260.0, 800.0)
    d_new = rc.difference_tone_db(y, sr, 800.0, 540.0)
    readings = (lo, hi, new, d_new)
    bad = next((e for e in readings
                if not e.ok or e.value is None or not math.isfinite(e.value)), None)
    if bad is not None:
        check_refused(
            f"defect 4's row for {label} refused ({bad.reason or 'no reason given'})"
            " -- no row was printed and no ratio moved against a reading "
            "that does not exist")
        return
    print(f"   {label}")
    print(f"      lines found        {lo.value:8.2f} Hz ({lo.detail['offset_pct']:+5.2f} %"
          f" off 540)   {hi.value:8.2f} Hz ({hi.detail['offset_pct']:+5.2f} % off 800)")
    print(f"      partial balance    nominal probe {old:+8.2f} dB"
          f"   measured lines {new.value:+8.2f} dB   moved {new.value-old:+6.2f}")
    print(f"      difference tone    nominal 260 Hz {d_old:+8.2f} dB"
          f"   at {d_new.detail['diff_hz']:6.2f} Hz {d_new.value:+8.2f} dB"
          f"   moved {d_new.value-d_old:+6.2f}")
    print(f"                         its own leakage floor {d_new.detail['floor_db']:+8.2f} dB,"
          f" headroom {d_new.detail['headroom_db']:.1f} dB")


def _defect4_synthetic_checks(tone_ratio=None, difference_tone=None):
    """Defect 4's closing verdicts on a synthetic two-partial strike,
    extracted so the control can rig either estimator to refuse (#843).

    The closed-form check used to read `.value` INSIDE the `check(...)`
    predicate itself: a nan-carrying reading made `abs(nan - ...) < 0.2`
    silently False -- a FAILED verdict where REFUSED is the truth, the
    exact shape #805 was filed for, now with a verdict downstream -- and a
    None-encoded one raised TypeError with no verdict at all. The guard
    runs before the predicate. The neighbouring difference-tone verdict is
    independent of this reading, so it runs either way: a refusal here must
    not quietly widen into skipping it."""
    tone_ratio = tone_ratio or rc.tone_ratio_db
    difference_tone = difference_tone or rc.difference_tone_db
    t = np.arange(int(0.1 * SR)) / SR
    two = 1.0 * np.sin(2 * math.pi * 823.70 * t) + 0.5 * np.sin(2 * math.pi * 558.35 * t)
    check(abs(tone_ratio_shipped(two, SR, 800.0, 540.0) - 20 * math.log10(2.0)) > 1.0,
          "the nominal probe is wrong on two partials at the machine's own frequencies")
    closed = tone_ratio(two, SR, 800.0, 540.0)
    if not closed.ok or closed.value is None or not math.isfinite(closed.value):
        check_refused(
            "defect 4's closed-form answer check refused "
            f"({closed.reason or 'no reason given'}) -- the two-partial "
            "strike was NOT measured, so 'finding the lines gets the "
            "closed-form answer' was neither confirmed nor denied")
    else:
        check(abs(closed.value - 20 * math.log10(2.0)) < 0.2,
              "finding the lines gets the closed-form answer")
    check(not difference_tone(two, SR, 800.0, 540.0).ok,
          "and a difference tone that is not there is refused, not reported")


def defect_4():
    print("\n" + "=" * 78)
    print("4. #108 -- the cowbell was probed at 540 / 800 Hz. Its lines are elsewhere.")
    print("=" * 78)
    ref_x, ref_sr, rel, _ = rc.load_reference("CB", REFS)
    our_x, our_sr = rc.render_drum_solo("CB")
    for label, x, sr in (("reference " + rel, ref_x, ref_sr), ("ours", our_x, our_sr)):
        y = rc.window(rc.prepare(x, sr), sr, 0.0, 0.100)
        _defect4_row(label, y, sr)
    print("   The difference tone moved by tens of dB on BOTH sides: a rectangular")
    print("   projection at 260 Hz was reading the two partials' leakage, not a tone.")
    _defect4_synthetic_checks()


# ===========================================================================
# 5. #139 -- the decay guard was a LENGTH, and silence satisfies length
# ===========================================================================
def t20_length_guard_shipped(x, sr, lo_db=-5.0, hi_db=-25.0, min_tail_t20=2.0):
    """`schroeder_t20`'s guard exactly as #118 left it: the criterion counts
    samples in the ARRAY after the -25 dB point. `np.zeros` are samples."""
    x = np.asarray(x, float)
    e = np.cumsum((x ** 2)[::-1])[::-1]
    L = 10.0 * np.log10(np.maximum(e / e[0], 1e-30))
    if float(L[-1]) > hi_db:
        return None, float("nan")
    i_lo, i_hi = int(np.argmax(L <= lo_db)), int(np.argmax(L <= hi_db))
    if i_hi <= i_lo + 8:
        return None, float("nan")
    t = np.arange(i_lo, i_hi) / sr
    slope = np.polyfit(t, L[i_lo:i_hi], 1)[0]
    if slope >= 0:
        return None, float("nan")
    t20 = -20.0 / float(slope)
    after = (len(x) - i_hi) / float(sr)
    return (t20 if after >= min_tail_t20 * t20 else None), after / t20


def _step_outlier(x, sr):
    """Candidate (b): the discontinuity a cut-then-pad leaves, as the largest
    sample-to-sample step divided by the local RMS envelope."""
    x = np.asarray(x, float)
    env = np.maximum(am.rms_envelope(x, 2.0, sr), 1e-300)
    return float((np.abs(np.diff(x)) / env[:-1]).max())


def _tail_residual(x, sr, min_tail_t20=2.0, lo_db=-5.0, hi_db=-25.0):
    """Candidate (c): how far the backward-integrated curve falls BELOW the
    line fitted to it, continued into the required tail. Signed and one-sided:
    a cut record's curve collapses, a slow ring-out's rises.

    Computed here on the array AS GIVEN, not on its sounding extent, because
    the question this table answers is what (c) would have done had it been
    chosen INSTEAD of the repair that shipped."""
    x = np.asarray(x, float)
    e = np.cumsum((x ** 2)[::-1])[::-1]
    if e[0] <= 0:
        return float("nan")
    L = 10.0 * np.log10(np.maximum(e / e[0], 1e-30))
    if float(L[-1]) > hi_db:
        return float("nan")
    i_lo, i_hi = int(np.argmax(L <= lo_db)), int(np.argmax(L <= hi_db))
    if i_hi <= i_lo + 8:
        return float("nan")
    t = np.arange(i_lo, i_hi) / sr
    slope, icept = np.polyfit(t, L[i_lo:i_hi], 1)
    if slope >= 0:
        return float("nan")
    end = min(i_hi + int(round(min_tail_t20 * (-20.0 / slope) * sr)), len(L))
    if end <= i_hi:
        return float("nan")
    tt = np.arange(i_hi, end) / sr
    return float((L[i_hi:end] - (slope * tt + icept)).min())


def _decay(tau, seconds, f=220.0, sr=SR):
    t = np.arange(int(seconds * sr)) / sr
    return np.exp(-t / tau) * np.sin(2 * math.pi * f * t)


def _trailing_silence_invariance(measure=am.schroeder_t20,
                                 pads=(0.2, 1.7, 5.0),
                                 tau=0.040, dur=0.500):
    """Trailing silence is EXACTLY invariant on a record that does contain
    its decay -- #139's repaired verdict, re-checked every run.

    The BASELINE is asserted at the point of use (#805): if it refuses, the
    comparison is REFUSED, never FAILED. Before the repair an unguarded
    `g.value - base.value` raised TypeError on the shipped `Estimate(None,
    False, ...)` encoding (NO VERDICT), and a nan-carrying refusal made
    `abs(nan - base) < 1e-12` silently False -- every pad row a wrong
    FAILED verdict with a plausible-looking cause. `measure` is injectable
    so `tools/probes/test_estimator_defects.py` can rig the BASELINE -- not
    the perturbed reading -- to refuse."""
    base = measure(_decay(tau, dur), SR)
    if not base.ok or base.value is None or not math.isfinite(base.value):
        check_refused(
            f"the trailing-silence invariance BASELINE (schroeder_t20 of a "
            f"clean {dur * 1e3:.0f} ms, tau {tau * 1e3:.0f} ms decay) refused "
            f"({base.reason or 'no reason given'}) -- the invariance below was "
            "NOT compared against it")
        return
    for pad_s in pads:
        g = measure(np.concatenate([_decay(tau, dur),
                                    np.zeros(int(pad_s * SR))]), SR)
        check(g.ok and abs(g.value - base.value) < 1e-12,
              f"{pad_s} s of trailing silence is EXACTLY invariant on a record "
              "that does contain its decay")


PROSE_LABEL_PAD_ZEROS = "PAD  zeros"
PROSE_LABEL_PAD_N60 = "PAD  noise  -60 dB"
PROSE_LABEL_FLOOR_B = "REAL on a -80 dBFS floor"
PROSE_LABEL_BD8 = "REAL bd8/BD5050.WAV"
PROSE_LABEL_CL8 = "REAL cl8/CL.WAV"


class ProseNeedsRefused(Exception):
    """A figure the WHY-THE-OTHER-TWO prose quotes was not measured this run
    (its corpus WAV is absent) or came back None / nan. REFUSED, not FAILED:
    the comparison the prose narrates was not made (#805's third outcome)."""


class ProseContradicted(Exception):
    """The measured values no longer have an ordering the prose asserts, so
    the words would be false with true numbers in them."""


def _other_two_prose(bc: dict) -> list[str]:
    """Defect 5's WHY-THE-OTHER-TWO block, every figure derived from the
    (b)/(c) table this run measured (`bc`: printed label -> (step ratio,
    tail residual)) -- the #988 repair class (#990): no gate reads prose, so
    a quoted figure drifts silently while the live table moves.

    Raises ProseNeedsRefused for a reading that does not exist and
    ProseContradicted when an ordering the wording asserts -- the zero pad
    BELOW the genuine -80 dBFS floor under (b), the -60 dB pad's residual
    ABOVE both corpus references under (c), which is what "INVERTED" and
    "refuses both references FIRST" claim -- no longer holds.

    GUARD-DEFEAT STATEMENT (rule 8): this guard cannot tell a measured dict
    from an invented one. The defeat is answered structurally -- `bc` is
    built in `defect_5` by the same loops that print the table, from the
    same measured values, so there is no seam where a quoted number can
    re-enter -- and behaviourally, by
    tools/probes/test_probe_prose_derivation.py, which perturbs entries
    through this seam and pins each ordering red."""
    def need(label: str, col: int) -> float:
        v = bc.get(label, (None, None))[col]
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ProseNeedsRefused(
                f"the (b)/(c) prose needs a finite reading for {label!r} "
                f"(column {'(b) step ratio' if col == 0 else '(c) tail resid dB'}); "
                f"this run measured {v!r}")
        return float(v)

    pad0_b = need(PROSE_LABEL_PAD_ZEROS, 0)
    floor_b = need(PROSE_LABEL_FLOOR_B, 0)
    pad60_c = need(PROSE_LABEL_PAD_N60, 1)
    bd8_c = need(PROSE_LABEL_BD8, 1)
    cl8_c = need(PROSE_LABEL_CL8, 1)
    if not pad0_b < floor_b:
        raise ProseContradicted(
            f"(b): the zero pad's step ratio {pad0_b:.2f} is not below the "
            f"genuine -80 dBFS floor's {floor_b:.2f} -- the prose's "
            "'property of the sound, not of the cut' ordering is gone")
    if not (pad60_c > cl8_c and pad60_c > bd8_c):
        raise ProseContradicted(
            f"(c): the -60 dB pad's tail residual {pad60_c:.1f} dB is not "
            f"above both corpus references (bd8 {bd8_c:.1f}, cl8 "
            f"{cl8_c:.1f}) -- INVERTED no longer holds, so a refuse-below "
            "threshold would not reject both references before the pad")
    return [
        "\n   (b) does not separate the two populations in EITHER direction: the zero",
        f"   pad reads {pad0_b:.2f} and a genuine -80 dBFS noise floor reads {floor_b:.2f}. It is",
        "   measuring the carrier's slew against its own envelope, which is a",
        "   property of the sound and not of the cut.",
        "   (c) separates zero pads cleanly, overlaps on noise pads, and on the",
        f"   real corpus it is INVERTED: the -60 dB pad reads {pad60_c:.1f} dB while",
        f"   bd8/BD5050.WAV -- the board's own bass-drum reference -- reads {bd8_c:.1f}",
        f"   and cl8/CL.WAV reads {cl8_c:.1f}. Any threshold that refuses the pad refuses",
        "   both references FIRST, so there is no threshold. (c) is REPORTED as",
        "   `tail_residual_db` and refuses nothing.",
        "   What shipped is the exact criterion: the estimate is read off the",
        "   record's SOUNDING extent, so a pad cannot change any verdict at all.",
    ]


def _read_corpus_wav(path: pathlib.Path) -> tuple[int, np.ndarray]:
    """One corpus WAV as `(sr, mono float64)`, or `refusal.Refused` (#1861):
    `defect_5` read the corpus with a bare `wavfile.read`, so a git-lfs
    pointer escaped `main()`'s refusal boundary as an exit-1 traceback."""
    from scipy.io import wavfile
    try:
        sr, y = wavfile.read(str(path))
    except (OSError, ValueError) as exc:
        raise refusal.Refused(f"{path}: unreadable WAV ({exc})") from exc
    y = np.asarray(y, float)
    return sr, (y.mean(1) if y.ndim > 1 else y)


def defect_5():
    print("\n" + "=" * 78)
    print("5. #139 -- appending zeros turned a refusal into an accepted wrong answer")
    print("=" * 78)
    tau = 0.200
    exact = math.log(10) * tau
    x = _decay(tau, 2.000)
    cut = x[:int(0.300 * SR)]
    pad = np.concatenate([cut, np.zeros(int(1.700 * SR))])
    print(f"   a single exponential, tau {tau*1e3:.0f} ms, exact T20 {exact*1e3:.1f} ms\n")
    print(f"   {'record':<30}{'SHIPPED (#118)':>18}{'in T20s':>9}{'REPAIRED (#139)':>18}")
    for label, y in (("full 2.0 s", x),
                     ("truncated to 0.30 s", cut),
                     ("truncated + 1.7 s of SILENCE", pad)):
        old, ratio = t20_length_guard_shipped(y, SR)
        new = am.schroeder_t20(y, SR)
        print(f"   {label:<30}"
              f"{(f'{old*1e3:8.1f} ms' if old else 'REFUSED'):>18}{ratio:9.2f}"
              f"{(f'{new.value*1e3:8.1f} ms' if new.ok else 'REFUSED'):>18}")
    print("\n   The pad satisfied the LENGTH criterion seven times over and moved the")
    print(f"   answer by {100*(t20_length_guard_shipped(pad, SR)[0]/exact-1):+.1f} %. "
          "Zeros carry no information about a decay.")
    check(t20_length_guard_shipped(pad, SR)[0] is not None,
          "the shipped length guard accepts the padded record")
    check(abs(t20_length_guard_shipped(pad, SR)[0] / exact - 1) > 0.40,
          "and the answer it accepts is nearly 50 % wrong")
    check(not am.schroeder_t20(pad, SR).ok,
          "the repaired guard refuses it")
    check(not am.schroeder_t20(cut, SR).ok,
          "and refuses the unpadded cut it is made of -- the SAME verdict, which "
          "is the invariance")
    _trailing_silence_invariance()

    print("\n   WHY THE OTHER TWO CANDIDATES IN #139 WERE NOT CHOSEN. Both were")
    print("   measured; the numbers are the reason, not a preference.\n")
    rng = np.random.default_rng(139)
    n = int(1.700 * SR)
    pop = [
        ("PAD  zeros",                 pad, False),
        ("PAD  noise -100 dB",         np.concatenate([cut, rng.normal(0, 1e-5, n)]), False),
        ("PAD  noise  -60 dB",         np.concatenate([cut, rng.normal(0, 1e-3, n)]), False),
        ("REAL tau 40 ms, 0.5 s",      _decay(0.040, 0.500), True),
        ("REAL 16-bit quantised",      np.round(_decay(0.040, 0.600) * 32767) / 32767, True),
        ("REAL on a -80 dBFS floor",   _decay(0.040, 0.600) + rng.normal(0, 1e-4, int(0.6 * SR)), True),
        ("REAL two-exp, slow late",    None, True),
    ]
    t = np.arange(int(1.2 * SR)) / SR
    pop[-1] = ("REAL two-exp, slow late",
               np.sin(2 * math.pi * 220 * t) * (np.exp(-t / 0.05) + 0.003 * np.exp(-t / 0.35)),
               True)
    print(f"   {'record':<28}{'(b) step ratio':>16}{'(c) tail resid dB':>20}   want")
    bc: dict = {}
    for label, y, good in pop:
        bc[label] = (_step_outlier(y, SR), _tail_residual(y, SR))
        print(f"   {label:<28}{bc[label][0]:16.2f}{bc[label][1]:20.2f}"
              f"   {'pass' if good else 'REFUSE'}")
    for label, path in (("bd8/BD5050.WAV", REFS / "bd8" / "BD5050.WAV"),
                        ("cl8/CL.WAV", REFS / "cl8" / "CL.WAV")):
        if not path.exists():
            continue
        sr, y = _read_corpus_wav(path)
        y = y / max(float(np.abs(y).max()), 1e-30)
        real = "REAL " + label
        bc[real] = (_step_outlier(y, sr), _tail_residual(y, sr))
        print(f"   {real:<28}{bc[real][0]:16.2f}"
              f"{bc[real][1]:20.2f}   pass")
    print("\n   THE FLOOR, MEASURED AGAINST THE CORPUS IT HAS TO CLEAR. `SOUNDING_")
    print("   FLOOR` is -180 dB of a record's own peak. What a REAL tail sits at:")
    import itertools
    rows = []
    for path in itertools.chain.from_iterable(
            sorted(d.glob("*.WAV")) for d in sorted(REFS.iterdir()) if d.is_dir()):
        sr2, y = _read_corpus_wav(path)
        pk = float(np.abs(y).max())
        if pk <= 0:
            continue
        y = y / pk
        k = am.sounding_extent(y)
        if k < 10:
            continue
        w = max(int(0.010 * sr2), 64)
        r = float(np.sqrt(np.mean(y[max(0, k - w):k] ** 2)))
        rows.append((20 * math.log10(max(r, 1e-300)),
                     path.parent.name + "/" + path.name, k, len(y)))
    rows.sort()
    stripped = sum(1 for r in rows if r[2] < r[3])
    print(f"      {len(rows)} references, last 10 ms of each SOUNDING extent:")
    print(f"      quietest {rows[0][0]:6.1f} dB  {rows[0][1]}")
    print(f"      loudest  {rows[-1][0]:6.1f} dB  {rows[-1][1]}")
    print(f"      {stripped} of {len(rows)} have any trailing sample stripped at all.")
    check(rows[0][0] - 20 * math.log10(am.SOUNDING_FLOOR) > 60.0,
          f"the quietest genuine tail in the corpus clears the floor by "
          f"{rows[0][0] - 20*math.log10(am.SOUNDING_FLOOR):.0f} dB")

    try:
        for line in _other_two_prose(bc):
            print(line)
    except ProseNeedsRefused as e:
        check_refused(f"defect 5's WHY-THE-OTHER-TWO prose: {e}")
    except ProseContradicted as e:
        check(False, f"defect 5's WHY-THE-OTHER-TWO prose: {e}")


# ===========================================================================
# 6. #150 -- a passband reference that moves with the commanded cutoff
# ===========================================================================
def corner_shipped(freqs, g, cut_hz):
    """`filt_corner` exactly as it stood before #150: -3 dB below the MEDIAN of
    a band whose top edge is 0.25 * the commanded cutoff."""
    return am.corner_from_curve(freqs, g, ref_band=rc._ref_band(freqs, cut_hz))


def _bad_corner(*estimates):
    """Section 6's readings guard (#845): every `.value` this section reads
    is a corner frequency or feeds a ratio of two, so return the reason the
    first unusable reading is unusable, or None when they all stand. Unusable
    means not ok, a None value (the shipped `_fail` encoding -- raised
    TypeError at the first arithmetic), non-finite (a nan-carrying estimate
    sailed through an `.ok`-only test and made every comparison silently
    False -- FAILED where REFUSED is the truth, #805's pathology), or
    nonpositive (a corner at or below 0 Hz is not a measurement: the
    readback's division raised ZeroDivisionError at 0 and printed a
    plausible percentage off a negative). GUARD-DEFEAT STATEMENT (rule 8):
    this certifies that a reading exists and is a physically possible
    frequency -- it cannot certify that the exhibit's wrong number is the
    RIGHT wrong number; that is what the printed columns and the swing
    verdicts are for. And each call sits ABOVE its block's first `.value`
    read, print included -- a guard below the print is defect 1's defeat
    note again: the None encoding dies at the print and the guard is never
    reached."""
    for e in estimates:
        if not e.ok:
            return e.reason or "no reason given"
        if e.value is None:
            return "a refusal carrying a None value"
        if not math.isfinite(e.value):
            return "a non-finite reading"
        if e.value <= 0.0:
            return f"a nonphysical corner ({e.value} Hz)"
    return None


def defect_6(shipped=None, repaired=None):
    import reference_compare as rcmp
    # The two estimators this section reads, exposed as seams (#845) so
    # `tools/probes/test_estimator_defects.py` can rig either one to refuse,
    # the `_prepend_silence_invariance(shipped=, repaired=)` pattern. The
    # defaults are the real things; a passing run is unchanged by them.
    shipped = shipped or corner_shipped
    repaired = repaired or rc.filt_corner
    print("\n" + "=" * 78)
    print("6. #150 -- the corner estimator droops, on a response that does not")
    print("=" * 78)
    F = np.asarray(rcmp.FREQS, dtype=np.float64)
    true_ratio = math.sqrt(10 ** (3.0 / 40.0) - 1.0)
    print(f"   an ideal 4-pole |H| = (1+(f/fc)^2)^-2 has its -3 dB point at")
    print(f"   f/fc = {true_ratio:.4f} for EVERY fc. Grid: geomspace("
          f"{F[0]:.0f}, {F[-1]:.0f}, {len(F)}), points {100*(F[1]/F[0]-1):.1f} % apart.\n")
    print(f"   {'commanded':>10}{'plateau used':>15}{'SHIPPED (pre-#150)':>19}"
          f"{'bias':>9}{'REPAIRED (#150)':>17}{'bias':>9}")
    for fc in (250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0):
        g = -40.0 * np.log10(1.0 + (F / fc) ** 2)
        old = shipped(F, g, fc)
        new = repaired(fc)(F, g)
        # Guarded above the FIRST `.value` read, the print included (#845):
        # all four figures in this row are ratios of corner readings, so a
        # refusing operand means the row does not exist.
        bad = _bad_corner(old, new)
        if bad is not None:
            check_refused(
                f"fc {fc:.0f}: defect 6's corner-estimator table refused "
                f"({bad}) -- no row was printed for this cutoff")
            continue
        print(f"   {fc:9.0f} {old.detail['plateau_db']:+14.3f}"
              f"{old.value/fc:19.4f}{100*(old.value/fc/true_ratio-1):+8.2f}%"
              f"{new.value/fc:17.4f}{100*(new.value/fc/true_ratio-1):+8.2f}%")
    print("\n   The plateau is the mechanism: the band's top is 0.25*fc, where a")
    print("   4-pole is 2.58 dB down, and its bottom is pinned at the grid's first")
    print("   point, so the median catches a different droop at every cutoff.")
    print("   A reference 0.90 dB low puts the corner 15 % high; 0.04 dB low, 0.02 %.\n")
    print("   A CONSTANT 16 % tuning error, read back:")
    print(f"   {'commanded':>10}{'SHIPPED (pre-#150)':>19}{'REPAIRED (#150)':>17}")
    for fc in (250.0, 1000.0, 4000.0):
        ref_g = -40.0 * np.log10(1.0 + (F / fc) ** 2)
        got_g = -40.0 * np.log10(1.0 + (F / (fc * 0.84)) ** 2)
        got_old = shipped(F, got_g, fc)
        ref_old = shipped(F, ref_g, fc)
        got_new = repaired(fc)(F, got_g)
        ref_new = repaired(fc)(F, ref_g)
        # Same guard, and this row is the DIVISION class (#845, the :501
        # shape): the readback is got/ref - 1 over two `.value` reads, so a
        # refusing-but-finite denominator raised ZeroDivisionError at 0 or
        # produced a plausible percentage off a negative -- the nonpositive
        # leg in `_bad_corner` is what catches it.
        bad = _bad_corner(got_old, ref_old, got_new, ref_new)
        if bad is not None:
            check_refused(
                f"fc {fc:.0f}: the constant-16 % readback refused ({bad}) "
                "-- the readback is a ratio of two readings, so no "
                "percentage was produced from a reading that does not exist")
            continue
        o = got_old.value / ref_old.value - 1
        n = got_new.value / ref_new.value - 1
        print(f"   {fc:9.0f}{100*o:18.2f}%{100*n:16.2f}%")
    print("\n   #150's TWO READINGS, RECONCILED. Both are right; the difference is")
    print("   the grid, which neither number was stated with.\n")
    print(f"   {'grid':<28}{'250 Hz':>9}{'1 kHz':>9}{'4 kHz':>9}   constant-16 % readback")
    for label, G in (("geomspace(40, 12000, 32)", F),
                     ("geomspace(20, 18000, 32)", np.geomspace(20.0, 18000.0, 32))):
        rs, es = [], []
        refused = None
        for fc in (250.0, 1000.0, 4000.0):
            ref_g = -40.0 * np.log10(1.0 + (G / fc) ** 2)
            got_g = -40.0 * np.log10(1.0 + (G / (fc * 0.84)) ** 2)
            r_ref = shipped(G, ref_g, fc)
            r_got = shipped(G, got_g, fc)
            bad = _bad_corner(r_ref, r_got)
            if bad is not None:
                refused = (fc, bad)
                break
            rs.append(r_ref.value / fc)
            es.append(100 * (r_got.value / r_ref.value - 1))
        if refused is not None:
            check_refused(
                f"{label}: the grid reconciliation refused at fc "
                f"{refused[0]:.0f} ({refused[1]}) -- the grid row was not "
                "printed, and neither grid was reconciled from a reading "
                "that does not exist")
            continue
        print(f"   {label:<28}" + "".join(f"{v:9.4f}" for v in rs)
              + "   " + " ".join(f"{v:7.2f}" for v in es))
    print("   The first is the external audit's and is `reference_compare.FREQS`,")
    print("   the grid the board is measured on. The second reproduces the issue")
    print("   author's 0.460/0.439/0.432 and -14.7/-16.0/-16.0 to three decimals.")
    print("   A 20 Hz floor puts the band lower relative to fc, so it catches less")
    print("   droop. The correction had to be sized against the FIRST.")

    for poles in (2, 4, 6):
        tr = math.sqrt(10 ** (3.0 / (10.0 * poles)) - 1.0)
        olds, news = [], []
        refused = None
        for fc in (250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0):
            g = -(10.0 * poles) * np.log10(1.0 + (F / fc) ** 2)
            old = shipped(F, g, fc)
            new = repaired(fc)(F, g)
            bad = _bad_corner(old, new)
            if bad is not None:
                refused = (fc, bad)
                break
            olds.append(old.value / fc)
            news.append(new.value / fc)
        if refused is not None:
            # The verdict guard (#845, the :529/:531 shape -- the reason this
            # issue exists): a nan operand made `max/min - 1 > 0.08` silently
            # False, a FAILED verdict where REFUSED is the truth; a None
            # operand raised at the append. A swing measured from a reading
            # that does not exist is not a number, so NEITHER verdict runs.
            check_refused(
                f"{poles}-pole: the corner-estimator spread refused at "
                f"fc {refused[0]:.0f} ({refused[1]}) -- NEITHER swing "
                "verdict ran")
            continue
        check(max(olds) / min(olds) - 1 > 0.08,
              f"{poles}-pole: the shipped estimator moves "
              f"{100*(max(olds)/min(olds)-1):.2f} % on a constant ratio")
        check(max(news) / min(news) - 1 < 0.015,
              f"{poles}-pole: the repaired one moves "
              f"{100*(max(news)/min(news)-1):.2f} %, against a true {tr:.4f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--all-voices", action="store_true",
                    help="section 1 over all sixteen sounds (slow: sixteen renders)")
    a = ap.parse_args()
    if not REFS.exists():
        print(f"the Fischer corpus is not at {REFS}", file=sys.stderr)
        return 2
    voices = sorted(rc.REF_MAIN) if a.all_voices else ["LC", "HC", "MC"]
    # The defect sections read the corpus through `rc.load_reference` /
    # `rc.prepare`, whose `preparation_contract.Refused` is a class this probe
    # never names (it has no local one) -- a silent corpus WAV escaped as an
    # exit-1 traceback, which `verdict_exit_code` renders exactly like a
    # failed invariant (#1183, `run_case.*` family). The boundary catches only
    # `refusal.Refused` and maps it to the probe's own refused exit (2); a
    # scipy ValueError on an unreadable corpus file still exits 1 where the
    # read is not wrapped as a refusal -- `defect_1` through
    # `run_case.load_reference` (#1860). `defect_5`'s own corpus reads refuse
    # (#1861, `_read_corpus_wav`).
    try:
        defect_1(voices)
        defect_2()
        defect_3()
        defect_4()
        defect_5()
        defect_6()
    except refusal.Refused as e:
        print(f"REFUSED  {e}")
        return 2
    print("\n" + "=" * 78)
    # Both summaries print before `verdict_exit_code()` decides the exit:
    # a run with refusals AND fails must not name only the refusals -- the
    # end-of-run summary is the part a reader quotes (#805 review).
    if refusals:
        print(f"{len(refusals)} check(s) REFUSED -- a precondition was not met, "
              "so the comparison(s) above were not made:")
        for r in refusals:
            print(f"   {r}")
    if fails:
        print(f"{len(fails)} check(s) FAILED:")
        for f in fails:
            print(f"   {f}")
    if not refusals and not fails:
        print("every repaired invariant holds, and every defect is still reproducible above")
    return verdict_exit_code()


if __name__ == "__main__":
    sys.exit(main())

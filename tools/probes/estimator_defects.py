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
import run_case as rc                                               # noqa: E402

REFS = rc.configured_refs()
SR = 48000
fails: list[str] = []


def check(ok: bool, what: str):
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if not ok:
        fails.append(what)


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
    x = np.zeros(int(0.6 * SR))
    t = np.arange(len(x) - 7) / SR
    x[7:] = np.exp(-t / 0.040) * np.sin(2 * math.pi * 220 * t)
    for pad_ms in (0.0, 0.5, 5.0, 500.0):
        p = np.concatenate([np.zeros(int(pad_ms * 1e-3 * SR)), x])
        o = split_db_shipped(prepare_shipped(p, SR), SR, "LC", 0.0, 0.150)
        n = rc.DRUM_PLAN["LC"][1][2](rc.prepare(p, SR), SR)
        print(f"      prepend {pad_ms:6.1f} ms of silence:  shipped {o.value:8.3f} dB"
              f"   repaired {n.value:8.3f} dB")
    base_o = split_db_shipped(prepare_shipped(x, SR), SR, "LC", 0.0, 0.150).value
    worst_o = max(abs(split_db_shipped(prepare_shipped(
        np.concatenate([np.zeros(int(p * 1e-3 * SR)), x]), SR), SR, "LC", 0.0, 0.150).value - base_o)
        for p in (0.5, 5.0, 500.0))
    base_n = rc.DRUM_PLAN["LC"][1][2](rc.prepare(x, SR), SR).value
    worst_n = max(abs(rc.DRUM_PLAN["LC"][1][2](rc.prepare(
        np.concatenate([np.zeros(int(p * 1e-3 * SR)), x]), SR), SR).value - base_n)
        for p in (0.5, 5.0, 500.0))
    print(f"      shipped swings {worst_o:.3f} dB from prepended silence alone;"
          f" repaired {worst_n:.6f} dB")
    print("   On a synthetic strike the swing is under a dB; on the real congas above")
    print("   it is 3.5-4.8 dB, because how much it is worth depends on how little")
    print("   energy the numerator band holds -- which is the sound, not the filter.")
    check(worst_o > 0.1, "the shipped path really was non-invariant (this is the defect)")
    check(worst_n < 0.01, "the repaired path is invariant to prepended silence")


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


def defect_2():
    print("\n" + "=" * 78)
    print("2. #119 / #92 -- the aliasing floor is not -54 dB, it is whatever f0 makes it")
    print("=" * 78)
    n = int(0.5 * SR)
    print(f"   {'f0':>10}{'on a bin':>10}{'HANN floor':>12}{'BH4 floor':>12}"
          f"{'HANN answer':>13}{'BH4 answer':>12}{'answer moved':>14}")
    swing = []
    for f0 in (110.0, 111.0, 111.3, 261.626, 440.0, 441.0, 1000.0):
        onbin = abs(f0 / (SR / n) - round(f0 / (SR / n))) < 1e-9
        free, naive = _addsaw(f0, n), _naivesaw(f0, n)
        fh = inharmonic_shipped(free, f0, SR)
        fb = am.inharmonic_fraction_db(free, f0, SR)
        ah = inharmonic_shipped(naive, f0, SR)
        ab = am.inharmonic_fraction_db(naive, f0, SR)
        swing.append(fh)
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
    a, b = am.tone_amplitude(x, hz_num, sr), am.tone_amplitude(x, hz_den, sr)
    return 20.0 * math.log10(a.value / b.value)


def defect_4():
    print("\n" + "=" * 78)
    print("4. #108 -- the cowbell was probed at 540 / 800 Hz. Its lines are elsewhere.")
    print("=" * 78)
    ref_x, ref_sr, rel, _ = rc.load_reference("CB", REFS)
    our_x, our_sr = rc.render_drum_solo("CB")
    for label, x, sr in (("reference " + rel, ref_x, ref_sr), ("ours", our_x, our_sr)):
        y = rc.window(rc.prepare(x, sr), sr, 0.0, 0.100)
        lo, hi = rc.find_line(y, sr, 540.0), rc.find_line(y, sr, 800.0)
        old = tone_ratio_shipped(y, sr, 800.0, 540.0)
        new = rc.tone_ratio_db(y, sr, 800.0, 540.0)
        d_old = tone_ratio_shipped(y, sr, 260.0, 800.0)
        d_new = rc.difference_tone_db(y, sr, 800.0, 540.0)
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
    print("   The difference tone moved by tens of dB on BOTH sides: a rectangular")
    print("   projection at 260 Hz was reading the two partials' leakage, not a tone.")
    t = np.arange(int(0.1 * SR)) / SR
    two = 1.0 * np.sin(2 * math.pi * 823.70 * t) + 0.5 * np.sin(2 * math.pi * 558.35 * t)
    check(abs(tone_ratio_shipped(two, SR, 800.0, 540.0) - 20 * math.log10(2.0)) > 1.0,
          "the nominal probe is wrong on two partials at the machine's own frequencies")
    check(abs(rc.tone_ratio_db(two, SR, 800.0, 540.0).value - 20 * math.log10(2.0)) < 0.2,
          "finding the lines gets the closed-form answer")
    check(not rc.difference_tone_db(two, SR, 800.0, 540.0).ok,
          "and a difference tone that is not there is refused, not reported")


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
    base = am.schroeder_t20(_decay(0.040, 0.500), SR)
    for pad_s in (0.2, 1.7, 5.0):
        g = am.schroeder_t20(np.concatenate([_decay(0.040, 0.500),
                                             np.zeros(int(pad_s * SR))]), SR)
        check(g.ok and abs(g.value - base.value) < 1e-12,
              f"{pad_s} s of trailing silence is EXACTLY invariant on a record "
              "that does contain its decay")

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
    for label, y, good in pop:
        print(f"   {label:<28}{_step_outlier(y, SR):16.2f}{_tail_residual(y, SR):20.2f}"
              f"   {'pass' if good else 'REFUSE'}")
    for label, path in (("bd8/BD5050.WAV", REFS / "bd8" / "BD5050.WAV"),
                        ("cl8/CL.WAV", REFS / "cl8" / "CL.WAV")):
        if not path.exists():
            continue
        from scipy.io import wavfile
        sr, y = wavfile.read(str(path))
        y = np.asarray(y, float)
        y = y.mean(1) if y.ndim > 1 else y
        y = y / max(float(np.abs(y).max()), 1e-30)
        print(f"   {'REAL ' + label:<28}{_step_outlier(y, sr):16.2f}"
              f"{_tail_residual(y, sr):20.2f}   pass")
    print("\n   THE FLOOR, MEASURED AGAINST THE CORPUS IT HAS TO CLEAR. `SOUNDING_")
    print("   FLOOR` is -180 dB of a record's own peak. What a REAL tail sits at:")
    import itertools
    from scipy.io import wavfile
    rows = []
    for path in itertools.chain.from_iterable(
            sorted(d.glob("*.WAV")) for d in sorted(REFS.iterdir()) if d.is_dir()):
        sr2, y = wavfile.read(str(path))
        y = np.asarray(y, float)
        y = y.mean(1) if y.ndim > 1 else y
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

    print("\n   (b) does not separate the two populations in EITHER direction: the zero")
    print("   pad reads 0.04 and a genuine -80 dBFS noise floor reads 4.18. It is")
    print("   measuring the carrier's slew against its own envelope, which is a")
    print("   property of the sound and not of the cut.")
    print("   (c) separates zero pads cleanly, overlaps on noise pads, and on the")
    print("   real corpus it is INVERTED: the -60 dB pad reads -24.8 dB while")
    print("   bd8/BD5050.WAV -- the board's own bass-drum reference -- reads -33.6")
    print("   and cl8/CL.WAV reads -25.3. Any threshold that refuses the pad refuses")
    print("   both references FIRST, so there is no threshold. (c) is REPORTED as")
    print("   `tail_residual_db` and refuses nothing.")
    print("   What shipped is the exact criterion: the estimate is read off the")
    print("   record's SOUNDING extent, so a pad cannot change any verdict at all.")


# ===========================================================================
# 6. #150 -- a passband reference that moves with the commanded cutoff
# ===========================================================================
def corner_shipped(freqs, g, cut_hz):
    """`filt_corner` exactly as it stood: -3 dB below the MEDIAN of a band
    whose top edge is 0.25 * the commanded cutoff."""
    return am.corner_from_curve(freqs, g, ref_band=rc._ref_band(freqs, cut_hz))


def defect_6():
    import reference_compare as rcmp
    print("\n" + "=" * 78)
    print("6. #150 -- the corner estimator droops, on a response that does not")
    print("=" * 78)
    F = np.asarray(rcmp.FREQS, dtype=np.float64)
    true_ratio = math.sqrt(10 ** (3.0 / 40.0) - 1.0)
    print(f"   an ideal 4-pole |H| = (1+(f/fc)^2)^-2 has its -3 dB point at")
    print(f"   f/fc = {true_ratio:.4f} for EVERY fc. Grid: geomspace("
          f"{F[0]:.0f}, {F[-1]:.0f}, {len(F)}), points {100*(F[1]/F[0]-1):.1f} % apart.\n")
    print(f"   {'commanded':>10}{'plateau used':>15}{'SHIPPED ratio':>15}{'bias':>9}"
          f"{'REPAIRED ratio':>16}{'bias':>9}")
    for fc in (250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0):
        g = -40.0 * np.log10(1.0 + (F / fc) ** 2)
        old = corner_shipped(F, g, fc)
        new = rc.filt_corner(fc)(F, g)
        print(f"   {fc:9.0f} {old.detail['plateau_db']:+14.3f}"
              f"{old.value/fc:15.4f}{100*(old.value/fc/true_ratio-1):+8.2f}%"
              f"{new.value/fc:16.4f}{100*(new.value/fc/true_ratio-1):+8.2f}%")
    print("\n   The plateau is the mechanism: the band's top is 0.25*fc, where a")
    print("   4-pole is 2.58 dB down, and its bottom is pinned at the grid's first")
    print("   point, so the median catches a different droop at every cutoff.")
    print("   A reference 0.90 dB low puts the corner 15 % high; 0.04 dB low, 0.02 %.\n")
    print("   A CONSTANT 16 % tuning error, read back:")
    print(f"   {'commanded':>10}{'SHIPPED':>12}{'REPAIRED':>12}")
    for fc in (250.0, 1000.0, 4000.0):
        ref_g = -40.0 * np.log10(1.0 + (F / fc) ** 2)
        got_g = -40.0 * np.log10(1.0 + (F / (fc * 0.84)) ** 2)
        o = corner_shipped(F, got_g, fc).value / corner_shipped(F, ref_g, fc).value - 1
        n = rc.filt_corner(fc)(F, got_g).value / rc.filt_corner(fc)(F, ref_g).value - 1
        print(f"   {fc:9.0f}{100*o:11.2f}%{100*n:11.2f}%")
    print("\n   #150's TWO READINGS, RECONCILED. Both are right; the difference is")
    print("   the grid, which neither number was stated with.\n")
    print(f"   {'grid':<28}{'250 Hz':>9}{'1 kHz':>9}{'4 kHz':>9}   constant-16 % readback")
    for label, G in (("geomspace(40, 12000, 32)", F),
                     ("geomspace(20, 18000, 32)", np.geomspace(20.0, 18000.0, 32))):
        rs, es = [], []
        for fc in (250.0, 1000.0, 4000.0):
            ref_g = -40.0 * np.log10(1.0 + (G / fc) ** 2)
            got_g = -40.0 * np.log10(1.0 + (G / (fc * 0.84)) ** 2)
            rs.append(corner_shipped(G, ref_g, fc).value / fc)
            es.append(100 * (corner_shipped(G, got_g, fc).value
                             / corner_shipped(G, ref_g, fc).value - 1))
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
        for fc in (250.0, 400.0, 630.0, 1000.0, 1600.0, 2500.0, 4000.0):
            g = -(10.0 * poles) * np.log10(1.0 + (F / fc) ** 2)
            olds.append(corner_shipped(F, g, fc).value / fc)
            news.append(rc.filt_corner(fc)(F, g).value / fc)
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
    defect_1(voices)
    defect_2()
    defect_3()
    defect_4()
    defect_5()
    defect_6()
    print("\n" + "=" * 78)
    if fails:
        print(f"{len(fails)} check(s) FAILED:")
        for f in fails:
            print(f"   {f}")
        return 1
    print("every repaired invariant holds, and every defect is still reproducible above")
    return 0


if __name__ == "__main__":
    sys.exit(main())

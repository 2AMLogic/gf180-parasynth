#!/usr/bin/env python3
"""What a TIME-RESOLVED low-band reading can and cannot do (#138, increment 3).

`docs/discrimination.md` 5c says twice that "a time-resolved low-band estimator
is needed and is not built", because `promoted_measures.lowband_level_db` puts
one Hann window over the whole 240 ms conditioned clip and therefore gives the
first 30 ms a mean amplitude weight of 0.05 -- and the excitation-pulse excess
`cqt.0-200Hz` was promoted for lives in exactly that region.

THIS PROBE EXISTS TO FIND THE PRECONDITION BEFORE THE ESTIMATOR IS WRITTEN,
because the obvious design (read the band in the first 30 ms) is arithmetically
impossible: 30 ms is 1.2 cycles of 40 Hz, and the Hann main lobe is four bins
wide, so a 40 Hz lower edge cannot be resolved in that window at all. An
estimator that answered anyway would be a tool answering when it cannot.

It reports three things and claims none of them as a gate:

  A  band-ratio error against the closed-form answer, as a function of window
     length, on a two-sine signal whose answer is the amplitude-squared ratio.
  B  what the whole-clip estimator and a windowed one each read on a clip whose
     low band lives ONLY in the first 80 ms -- the structure the trajectory
     report names -- so the claim "the whole-clip estimator cannot see it" is
     measured rather than asserted.
  C  `dominant_period_ms` on a DOWNWARD GLIDE, which `promoted_measures`' own
     header says has never been measured and which every 808 tom does.

Run:  python tools/probes/lowband_onset_window.py
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
import promoted_measures as pm  # noqa: E402

SR = 44100
BAND = (40.0, 200.0)


def _band_ratio_db(x, sr, band, win=None):
    """The quantity `lowband_level_db` computes, over `win` = (start_s, len_s)
    of `x` instead of all of it. Deliberately re-implemented here rather than
    imported: a probe that calls the thing it is probing cannot tell you the
    thing is wrong."""
    if win is not None:
        a = int(round(win[0] * sr))
        x = np.asarray(x, float)[a:a + int(round(win[1] * sr))]
    x = np.asarray(x, float)
    w = np.hanning(len(x))
    P = np.abs(np.fft.rfft(x * w)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / sr)
    tot = P.sum()
    if tot <= 0:
        return -math.inf
    frac = P[(f >= band[0]) & (f < band[1])].sum() / tot
    return 10 * math.log10(frac) if frac > 0 else -math.inf


def part_a():
    """Band-ratio error vs window length, closed-form answer."""
    print("A. band-ratio error vs window length (90 Hz + 0.3*1500 Hz, "
          f"band {BAND[0]:g}-{BAND[1]:g} Hz)")
    want = 10 * math.log10(1.0 / (1.0 + 0.3 ** 2))
    t = np.arange(int(0.24 * SR)) / SR
    x = np.sin(2 * np.pi * 90 * t) + 0.3 * np.sin(2 * np.pi * 1500 * t)
    need_ms = 1000.0 * pm.MIN_CYCLES_OF_LOWER_EDGE / BAND[0]
    print(f"   closed-form answer {want:+.4f} dB; three cycles of {BAND[0]:g} Hz "
          f"= {need_ms:.1f} ms")
    for ms in (10, 20, 30, 50, 75, 100, 150, 240):
        got = _band_ratio_db(x, SR, BAND, win=(0.0, ms / 1000.0))
        print(f"   {ms:4d} ms window -> {got:+9.4f} dB   error {got - want:+8.4f} dB"
              f"   {'(below the 3-cycle precondition)' if ms < need_ms else ''}")
    print("   ...and the same windows with the lower edge raised to 135 Hz, the "
          "lowest bucket the trajectory report reads in a 30 ms window:")
    want2 = want  # the 90 Hz line is now out of band; recompute properly below
    for ms in (20, 30, 50):
        got = _band_ratio_db(x, SR, (135.0, 400.0), win=(0.0, ms / 1000.0))
        print(f"   {ms:4d} ms, band 135-400 Hz -> {got:+9.4f} dB   "
              f"(90 Hz is out of band here; a low number is correct)")
    del want2


def part_b():
    """A clip whose low band lives only in the first 80 ms."""
    print("\nB. a clip whose LOW band is only in the first 80 ms and whose HIGH "
          "band is only after it")
    n = int(0.24 * SR)
    t = np.arange(n) / SR
    early = np.zeros(n)
    late = np.zeros(n)
    k = int(0.08 * SR)
    # raised-cosine edges so the split is not a click (a click is broadband and
    # would put energy in both bands for a reason that is not the signal)
    ramp = int(0.005 * SR)
    g = np.ones(k)
    g[-ramp:] = np.cos(np.linspace(0, np.pi / 2, ramp)) ** 2
    early[:k] = np.sin(2 * np.pi * 90 * t[:k]) * g
    h = np.ones(n - k)
    h[:ramp] = np.sin(np.linspace(0, np.pi / 2, ramp)) ** 2
    late[k:] = np.sin(2 * np.pi * 1500 * t[k:]) * h
    x = early + late
    whole = pm.lowband_level_db(x, SR)
    print(f"   lowband_level_db (whole 240 ms Hann) -> "
          f"{whole.value if whole.ok else whole.reason}")
    for start, length in ((0.0, 0.080), (0.080, 0.160), (0.0, 0.240)):
        got = _band_ratio_db(x, SR, BAND, win=(start, length))
        print(f"   window {start*1000:5.0f}-{(start+length)*1000:5.0f} ms -> {got:+9.3f} dB")
    e_early = float((early ** 2).sum())
    e_late = float((late ** 2).sum())
    print(f"   construction: {10*math.log10(e_early/(e_early+e_late)):+.3f} dB of the "
          f"clip's total energy is the in-band early burst")


def part_c():
    """`dominant_period_ms` on a downward glide."""
    print("\nC. dominant_period_ms on a DOWNWARD GLIDE (never measured before; "
          "every 808 tom does this)")
    dur = 0.24
    n = int(dur * SR)
    t = np.arange(n) / SR
    tau = 0.1
    print("   depth = f(0)/f(end) - 1.  reference = the ENERGY-WEIGHTED MEAN "
          "instantaneous frequency,")
    print("   which is what a single spectral line of a glide should report; "
          "glide_tau = 0.03 s")
    for depth in (0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.40):
        f_end = 90.0
        f0 = f_end * (1.0 + depth)
        gt = 0.03
        # f(t) = f_end + (f0-f_end)*exp(-t/gt); phase is its integral
        inst = f_end + (f0 - f_end) * np.exp(-t / gt)
        phase = 2 * np.pi * (f_end * t - (f0 - f_end) * gt * (np.exp(-t / gt) - 1.0))
        env = np.exp(-t / tau)
        x = np.sin(phase) * env
        wgt = env ** 2
        f_bar = float((inst * wgt).sum() / wgt.sum())
        e = pm.dominant_period_ms(x, SR, (40.0, 200.0))
        if not e.ok:
            print(f"   depth {depth*100:5.1f} %  REFUSED: {e.reason}")
            continue
        got_hz = 1000.0 / e.value
        print(f"   depth {depth*100:5.1f} %  f(0)={f0:6.2f}  f(end)={f_end:6.2f}  "
              f"weighted-mean={f_bar:6.2f}  read={got_hz:6.2f} Hz  "
              f"err vs weighted-mean {100*(got_hz/f_bar-1):+6.2f} %  "
              f"err vs f(end) {100*(got_hz/f_end-1):+6.2f} %")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    part_a()
    part_b()
    part_c()

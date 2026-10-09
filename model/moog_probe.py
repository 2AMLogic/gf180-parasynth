#!/usr/bin/env python3
"""Settings-independent ladder probes against free Minimoog recordings.

    !! PARTIALLY WITHDRAWN, 2026-09-18 -- read docs/discrimination.md 8.1 !!

    `harmonics()` below integrates FFT bins with NO WINDOW and NO FLOOR
    CHECK. A rectangular coherent projection leaks the fundamental sideways
    at about 1/(pi * delta_bins), which puts a phantom "harmonic" at -55 to
    -75 dB -- exactly where the h5 values this file was used to publish live.
    The 25 dB of h5-h3 separation in revision 1 of docs/discrimination.md 8
    was that leak: re-measured, four of its six numbers do not exist above
    their own noise floor.

    Use `audio_measure.harmonic_signature` instead. It projects through a
    Blackman-Harris window, measures a floor at four off-harmonic offsets and
    returns None rather than a number when a harmonic is under it, and is
    ground-truthed in model/test_reference_compare.py.

    What is NOT withdrawn: the admission test below (h2 - h3 and h3), the
    finding that none of the 222 recordings is a ladder ringing on its own,
    and the design of the probe. Only the harmonic LEVELS it printed.


    .venv/bin/python model/moog_probe.py --set /tmp/legowelt

Two claims must not be confused, and this script only ever supports the
second one when its preconditions are actually met:

  PATCH FITTING (sound matching)  shows the engine can REACH a family of
      tones. Needs no panel metadata, and proves nothing about structure.
  STRUCTURE PROBE                 shows the nonlinearity is where DR 0001 says
      it is. Needs a recording of the ladder ringing ON ITS OWN, because the
      harmonic series of a self-oscillating Moog ladder is a fingerprint of
      the per-stage tanh and does not depend on where the cutoff knob sat.

The material: Legowelt's pack from his 1970s Minimoog serial #5529, free
("the samples are free but please consider a donation"), 222 WAVs, 16-bit
44.1 kHz. The pack ships three photographs and an info text. It ships NO
panel settings -- the only per-file documentation is the name, and the names
are characterisations ("BASS-Mudsy", "SYNTH-Zoemer"), not settings.

What that does and does not permit:

  NOT extractable, and not inferred here: the cutoff-versus-knob transfer
  curve, absolute filter tuning, and whether a given distortion originates in
  the mixer or the filter. Those need panel settings or a controlled session.

  Extractable in principle: the self-oscillation harmonic signature, the
  rolloff slope above an identifiable resonant peak, and the family of
  resonant peak shapes.

THE PRECONDITION THAT DECIDES THE SELF-OSCILLATION PROBE. Without panel
metadata, a near-sine can be a self-oscillating filter OR a triangle
oscillator low-passed well below its harmonics. Both are characteristic
Minimoog sounds. The two are separable only by their harmonic signature, and
that is the very thing being measured -- so the test is circular unless the
candidates' harmonics land in the range a ladder can actually produce. This
script measures our own ladder's self-oscillation to establish that range,
measures a one-tanh linearised ladder as the negative control, and only then
asks whether any candidate falls inside it. If the purest candidates are far
PURER than any ladder can be, they are filtered oscillators and the probe has
no material to work on -- which is a result, and is reported as one.
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

from scipy.io import wavfile

SR = 48000


def harmonics(x: np.ndarray, sr: int, n_harm: int = 8, f0_lo=25.0, f0_hi=4000.0) -> dict:
    """h2..hN relative to the fundamental, in dB, from the steady middle of a
    clip. The measurement a ladder's per-stage tanh shows up in."""
    m = x[len(x) // 4: 3 * len(x) // 4]
    if len(m) < 4096:
        m = x
    w = m * np.hanning(len(m))
    X = np.abs(np.fft.rfft(w))
    fr = np.fft.rfftfreq(len(m), 1.0 / sr)
    sel = (fr > f0_lo) & (fr < f0_hi)
    f0 = float(fr[sel][np.argmax(X[sel])])
    out, h1 = {}, None
    for n in range(1, n_harm + 1):
        band = (fr > n * f0 * 0.985) & (fr < n * f0 * 1.015)
        a = float(np.sqrt((X[band] ** 2).sum())) if band.any() else 0.0
        if n == 1:
            h1 = max(a, 1e-12)
        out[f"h{n}"] = 20 * math.log10(max(a, 1e-12) / h1)
    out["f0"] = f0
    return out


def our_ladder_selfosc(f_hz: float, res: float = 1.25, seconds: float = 1.0) -> np.ndarray:
    """Our integer ladder, kicked once and left to ring past the resonance
    threshold. This is the contract's ladder: tanh in EVERY stage (DR 0001)."""
    from fixed import LadderFx
    from voice_fx import LADDER_CFG
    n = int(seconds * SR)
    lad = LadderFx(**LADDER_CFG)
    x = np.zeros(n, dtype=np.int16)
    x[0] = 12000                                   # one kick, then silence
    y = lad.process(x, np.full(n, f_hz, dtype=float), res)
    return np.asarray(y, dtype=float) / 32768.0


def one_tanh_selfosc(f_hz: float, res: float = 1.25, seconds: float = 1.0) -> np.ndarray:
    """NEGATIVE CONTROL: the linearised alternative DR 0001 rejected -- four
    LINEAR one-pole stages with a single tanh in the feedback path only. If
    this matches the hardware as well as ours does, the probe has no power to
    distinguish the two structures and must not be used to support DR 0001."""
    n = int(seconds * SR)
    g = 1.0 - math.exp(-2.0 * math.pi * f_hz / SR)
    k = 4.0 * res
    y = [0.0] * 4
    out = np.zeros(n)
    x = np.zeros(n)
    x[0] = 0.37
    for i in range(n):
        u = x[i] - k * math.tanh(y[3])             # the ONLY nonlinearity
        for s in range(4):
            prev = u if s == 0 else y[s - 1]
            y[s] += g * (prev - y[s])
        out[i] = y[3]
    return out


def rolloff_slope(x: np.ndarray, sr: int, band=(1.3, 4.0)) -> dict:
    """dB per octave above the resonant peak. Settings-independent even
    though the corner is not: a 4-pole ladder must approach -24 dB/oct.
    `band` is the fit window as a multiple of the peak frequency; it starts
    above the peak's own skirt and stops before the noise floor."""
    m = x[len(x) // 4: 3 * len(x) // 4] if len(x) > 8192 else x
    X = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    fr = np.fft.rfftfreq(len(m), 1.0 / sr)
    sel = (fr > 60) & (fr < 8000)
    fpk = float(fr[sel][np.argmax(X[sel])])
    lo, hi = fpk * band[0], min(fpk * band[1], sr / 2 * 0.9)
    m2 = (fr > lo) & (fr < hi) & (X > 0)
    if m2.sum() < 16:
        return dict(f_peak=fpk, slope_db_oct=float("nan"), n=int(m2.sum()))
    # smooth in log-frequency so a harmonic comb does not dominate the fit
    lf = np.log2(fr[m2])
    ld = 20 * np.log10(X[m2])
    bins = np.linspace(lf.min(), lf.max(), 24)
    idx = np.digitize(lf, bins)
    xs, ys = [], []
    for b in range(1, len(bins)):
        s = idx == b
        if s.sum() > 2:
            xs.append(lf[s].mean())
            ys.append(np.percentile(ld[s], 90))    # the envelope, not the nulls
    if len(xs) < 5:
        return dict(f_peak=fpk, slope_db_oct=float("nan"), n=len(xs))
    a = np.polyfit(xs, ys, 1)[0]
    return dict(f_peak=fpk, slope_db_oct=float(a), band_hz=(lo, hi), n=len(xs))


def peak_shape(x: np.ndarray, sr: int) -> dict:
    """Resonant peak height over the passband, and its Q. The knob position is
    unknowable, but the FAMILY of peak shapes in a set is characterisable."""
    m = x[len(x) // 4: 3 * len(x) // 4] if len(x) > 8192 else x
    X = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    fr = np.fft.rfftfreq(len(m), 1.0 / sr)
    sel = (fr > 60) & (fr < 10000)
    f, A = fr[sel], 20 * np.log10(np.maximum(X[sel], 1e-12))
    i = int(np.argmax(A))
    fpk, apk = float(f[i]), float(A[i])
    below = A[(f > fpk * 0.25) & (f < fpk * 0.7)]
    passband = float(np.median(below)) if len(below) else float("nan")
    half = apk - 3.0
    lo = f[:i][A[:i] < half]
    hi = f[i:][A[i:] < half]
    bw = (hi[0] - lo[-1]) if len(lo) and len(hi) else float("nan")
    return dict(f_peak=fpk, peak_over_passband_db=apk - passband,
                Q=float(fpk / bw) if bw and np.isfinite(bw) and bw > 0 else float("nan"))


class ScanRefused(Exception):
    """The capture set is not the set the caller thinks it is. `skipped` lists
    (file, reason) for every WAV that could not be used."""

    def __init__(self, msg: str, skipped: list):
        super().__init__(msg)
        self.skipped = skipped


def scan(setdir: str, n_harm: int = 8, expect: int | None = None) -> list:
    """Harmonic fingerprint of every WAV in `setdir` -- or REFUSE.

    #600: this used to `continue` past any file that failed to read or was
    under half a second, so a damaged set yielded fewer rows and no error, and
    every "N of M" printed afterwards was over a denominator nobody chose. Now
    any skipped file, an empty set, or fewer usable rows than `expect` raises
    ScanRefused. `expect` exists because a file missing from disk is invisible
    to a scan of what is on disk; the caller must say how many there are."""
    rows, skipped = [], []
    for p in sorted(glob.glob(os.path.join(setdir, "*.wav"))):
        name = os.path.basename(p)
        try:
            sr, x = wavfile.read(p)
        except (ValueError, OSError, EOFError) as e:
            skipped.append((name, f"unreadable: {type(e).__name__}: {e}"))
            continue
        x = x.astype(float)
        if x.ndim > 1:
            x = x.mean(1)
        if len(x) < sr // 2:
            skipped.append((name, f"{len(x) / sr:.3f} s, under the 0.5 s minimum"))
            continue
        x /= max(abs(x).max(), 1.0)
        h = harmonics(x, sr, n_harm)
        rows.append((name, sr, len(x) / sr, h))
    problems = [f"{n}: {why}" for n, why in skipped]
    if not rows:
        problems.append(f"no usable recording in {setdir}")
    if expect is not None and len(rows) < expect:
        problems.append(f"{len(rows)} usable recordings, {expect} expected")
    if problems:
        raise ScanRefused(f"{len(skipped)} file(s) skipped in {setdir}; "
                          + "; ".join(problems), skipped)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="/tmp/legowelt")
    ap.add_argument("--res", type=float, default=1.25)
    ap.add_argument("--expect", type=int, default=None,
                    help="number of recordings the set holds; fewer usable is REFUSED")
    a = ap.parse_args(argv)

    print("== what a ladder's self-oscillation LOOKS like (our model, DR 0001) ==")
    ours = {}
    for f in (129.0, 258.0, 516.0):
        h = harmonics(our_ladder_selfosc(f, a.res), SR)
        ours[f] = h
        print(f"  ours   f0 {h['f0']:7.1f}  " +
              "  ".join(f"h{n} {h[f'h{n}']:6.1f}" for n in range(2, 7)))
    print("\n== the NEGATIVE CONTROL: one tanh in the feedback path only ==")
    onet = {}
    for f in (129.0, 258.0, 516.0):
        h = harmonics(one_tanh_selfosc(f, a.res), SR)
        onet[f] = h
        print(f"  1-tanh f0 {h['f0']:7.1f}  " +
              "  ".join(f"h{n} {h[f'h{n}']:6.1f}" for n in range(2, 7)))
    # The ladder's tanh is ODD-symmetric, so a ladder ringing on its own emits
    # only ODD harmonics: h2 and h4 are absent by symmetry in BOTH structures
    # (ours -89..-104 dB, one-tanh -133..-160 dB), far under any recording's
    # noise floor. h2 is therefore useless here. The structural fingerprint is
    # h5 RELATIVE TO h3 -- how far the distortion spreads up the odd series,
    # which is exactly what having a tanh in every stage rather than one
    # changes. It is a ratio of harmonics, so it is level- and
    # settings-independent, which is what makes it usable without metadata.
    def spread(h):
        return h["h5"] - h["h3"]
    sep = float(np.mean([abs(spread(ours[f]) - spread(onet[f])) for f in ours]))
    print(f"\n  h5-h3 spread: ours " +
          ", ".join(f"{spread(ours[f]):+.1f}" for f in sorted(ours)) +
          " dB;  one-tanh " + ", ".join(f"{spread(onet[f]):+.1f}" for f in sorted(onet)) + " dB")
    print(f"  separation between the two structures: {sep:.1f} dB")
    if sep < 6.0:
        print("  -> the two structures are NOT separable by this probe. No conclusion "
              "about DR 0001 may be drawn from it.")
    else:
        print(f"  -> a usable probe EXISTS: {sep:.0f} dB is well clear of a recording's "
              f"noise floor. What it needs is material.")

    # A ladder ringing alone cannot emit an even harmonic comparable to its odd
    # ones. A candidate whose h2 is within a few dB of its h3 therefore has an
    # asymmetric source in the path -- an oscillator through the filter -- and
    # is not self-oscillation, whatever it sounds like.
    # TWO criteria, and both are needed. Odd symmetry alone is not enough: a
    # square wave is odd-symmetric too, and lands h3 at -9.5 dB. Ladder
    # self-oscillation is a near-sine, so its odd harmonics must also be far
    # DOWN on the fundamental.
    EVEN_ODD_MAX = -12.0      # h2 - h3: even harmonics must be well under odd
    H3_MAX = -25.0            # h3 itself: purer than any oscillator waveform
    print(f"\n  ADMISSION TEST, both required:")
    print(f"    (a) h2 - h3 <= {EVEN_ODD_MAX:.0f} dB -- an odd-symmetric ladder alone "
          f"cannot put an even harmonic near its odd ones. Ours: " +
          ", ".join(f"{ours[f]['h2'] - ours[f]['h3']:+.0f}" for f in sorted(ours)) + " dB.")
    print(f"    (b) h3 <= {H3_MAX:.0f} dB -- self-oscillation is a near-sine. A square "
          f"wave sits at -9.5 dB and would pass (a). Ours: " +
          ", ".join(f"{ours[f]['h3']:+.0f}" for f in sorted(ours)) + " dB.")

    if not os.path.isdir(a.set):
        if a.expect is not None:
            # #600: --expect says the set exists; an absent directory is the
            # whole set missing, the input a per-file guard cannot see.
            print(f"\nREFUSED: {a.set} not found but --expect {a.expect} was given; "
                  f"0 of {a.expect} recordings available.")
            return 2
        print(f"\n{a.set} not found; skipping the recordings.")
        return 0

    try:
        rows = scan(a.set, expect=a.expect)
    except ScanRefused as e:
        print(f"\nREFUSED: {e}")
        print("  The recordings were not read as a whole set, so no count or")
        print("  admission result over them would mean anything.")
        return 2
    print(f"\n== {len(rows)} recordings scanned ==")
    pure = sorted(rows, key=lambda r: r[3]["h2"])
    print("  the 10 purest tones in the set (lowest h2):")
    for nm, sr, dur, h in pure[:10]:
        print(f"    {nm[:46]:46s} f0 {h['f0']:7.1f}  h2 {h['h2']:6.1f}  h3 {h['h3']:6.1f}")
    admitted = [r for r in rows
                if r[3]["h2"] - r[3]["h3"] <= EVEN_ODD_MAX and r[3]["h3"] <= H3_MAX]
    odd_only = [r for r in rows if r[3]["h2"] - r[3]["h3"] <= EVEN_ODD_MAX]
    pure_only = [r for r in rows if r[3]["h3"] <= H3_MAX]
    print(f"\n  passing (a) odd-symmetric: {len(odd_only)} of {len(rows)} -- but these are "
          f"oscillator waveforms (median h3 "
          f"{np.median([r[3]['h3'] for r in odd_only]):.0f} dB, a square wave is -9.5)")
    print(f"  passing (b) near-sine:     {len(pure_only)} of {len(rows)} -- but these carry "
          f"even harmonics AT or ABOVE their odd ones (median h2-h3 "
          f"{np.median([r[3]['h2'] - r[3]['h3'] for r in pure_only]):+.0f} dB), which is an "
          f"asymmetric source in the path")
    print(f"\n  recordings passing the admission test: {len(admitted)} of {len(rows)}")
    if not admitted:
        print("  -> NO recording in this set is a ladder ringing on its own. Every one")
        print("     of them carries an even harmonic within 12 dB of its odd one, which")
        print("     an odd-symmetric ladder alone cannot produce: these are oscillator")
        print("     tones through the filter. THE AVAILABLE RECORDINGS DO NOT SUPPORT A")
        print("     LADDER-STRUCTURE PROBE. DR 0001 stays supported by circuit")
        print("     derivation alone, which is where it already was.")
    else:
        for nm, sr, dur, h in sorted(admitted, key=lambda r: r[3]["h5"] - r[3]["h3"])[:8]:
            print(f"    {nm[:44]:44s} f0 {h['f0']:7.1f}  h3 {h['h3']:6.1f}  "
                  f"h5-h3 {h['h5'] - h['h3']:+6.1f}")

    for nm in ("SYNTH-SimpleThinSquareFilterSlope.wav", "WEIRD-ResonanceZone.wav"):
        p = os.path.join(a.set, nm)
        if not os.path.exists(p):
            continue
        sr, x = wavfile.read(p)
        x = x.astype(float)
        if x.ndim > 1:
            x = x.mean(1)
        x /= max(abs(x).max(), 1.0)
        r = rolloff_slope(x, sr)
        q = peak_shape(x, sr)
        print(f"\n  {nm} (the only self-documenting file of its kind in the set)")
        print(f"    rolloff above the peak: {r['slope_db_oct']:+.1f} dB/oct over "
              f"{r.get('band_hz', (0, 0))[0]:.0f}-{r.get('band_hz', (0, 0))[1]:.0f} Hz "
              f"(peak {r['f_peak']:.0f} Hz)")
        print(f"    peak over passband {q['peak_over_passband_db']:+.1f} dB, Q {q['Q']:.2f}")
        # The slope and Q probes need a BROADBAND source with a resonant peak.
        # Fed a steady periodic tone they measure a spectral line, not a
        # filter: the give-aways are an impossible Q and a rolloff that does
        # not fall. Refuse the number rather than report it.
        if q["Q"] > 30 or not (-40 < r["slope_db_oct"] < -6):
            print("    -> INVALID INPUT, not a measurement: a 4-pole rolloff must be near")
            print("       -24 dB/oct and a ladder resonance cannot have Q in the hundreds.")
            print("       This is a steady periodic tone, so the FFT peak is a spectral")
            print("       line. The slope and peak-shape probes need a broadband source")
            print("       (noise or a bright saw) held under a resonant peak; the set has")
            print("       none that is identifiable without panel settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

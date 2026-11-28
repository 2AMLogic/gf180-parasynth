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
import pathlib
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))

from scipy.io import wavfile

_REFUSAL_TOOLS = str(pathlib.Path(__file__).resolve().parents[1] / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
import refusal  # noqa: E402

SR = 48000


class Refused(refusal.Refused, RuntimeError):
    """A precondition of the measurement is unmet, so nothing was measured."""


class ScanRefused(Refused):
    """The capture set is not the set the caller thinks it is. `skipped` lists
    (file, reason) for every WAV that could not be used."""

    def __init__(self, msg: str, skipped: list):
        super().__init__(msg)
        self.skipped = skipped


def harmonics(x: np.ndarray, sr: int, n_harm: int = 8, f0_lo=25.0, f0_hi=4000.0) -> dict:
    """h2..hN relative to the fundamental, in dB, from the steady middle of a
    clip. The measurement a ladder's per-stage tanh shows up in.

    No amplitude floor: every ratio is the exact one, so the answer does not
    depend on the clip's level. A harmonic of exactly zero amplitude has no dB
    level and is `None` (#1736).

    REFUSES (Refused, so nothing is measured) when the steady middle is empty
    or holds a non-finite sample, when `sr` is not a positive finite number,
    or when the fundamental band `(f0_lo, f0_hi)` holds no spectral energy.
    A harmonic whose centre `n*f0` exceeds Nyquist is `None`, whatever else
    falls inside its band.
    Exact silence is that last case. Until #1736 a `1e-12` floor read it as
    h1..hN = 0 dB at a fabricated f0 of 26 Hz, and read a clip under the
    floor as flat too."""
    m = np.asarray(x[len(x) // 4: 3 * len(x) // 4], dtype=float)
    if len(m) < 4096:
        m = np.asarray(x, dtype=float)
    if m.size == 0 or not np.all(np.isfinite(m)):
        raise Refused("the steady middle of the clip is empty or holds a non-finite sample")
    if not np.isfinite(sr) or sr <= 0:
        raise Refused(f"sample rate {sr!r} is not a positive finite number")
    peak = float(np.max(np.abs(m)))
    if peak > 0.0:
        m = m / peak          # scale-safe: band norms below neither underflow nor overflow
    w = m * np.hanning(len(m))
    X = np.abs(np.fft.rfft(w))
    fr = np.fft.rfftfreq(len(m), 1.0 / sr)
    sel = (fr > f0_lo) & (fr < f0_hi)
    if not sel.any() or not X[sel].max() > 0.0:
        raise Refused(f"no spectral energy in the {f0_lo:g}-{f0_hi:g} Hz fundamental "
                      "band: the clip is silent")
    f0 = float(fr[sel][np.argmax(X[sel])])
    out, h1 = {}, None
    for n in range(1, n_harm + 1):
        band = (fr > n * f0 * 0.985) & (fr < n * f0 * 1.015)
        # A harmonic whose centre is past Nyquist is unobservable: a
        # neighbouring component inside the +-1.5% band is not it (#1745).
        beyond = n * f0 > sr / 2.0
        a = float(np.sqrt((X[band] ** 2).sum())) if band.any() and not beyond else 0.0
        if n == 1:
            if not a > 0.0:
                raise Refused(f"the fundamental band at {f0:.1f} Hz holds no energy")
            h1 = a
        out[f"h{n}"] = 20.0 * math.log10(a / h1) if a > 0.0 else None
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
    if not sel.any():
        raise Refused("no spectral bins in the 60-8000 Hz band for the rolloff probe")
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
    unknowable, but the FAMILY of peak shapes in a set is characterisable.

    No amplitude floor: the peak is found on the linear magnitude, and the dB
    spectrum maps a bin of exactly zero to -inf, so the answer does not depend
    on the clip's level (#1736).

    REFUSES (Refused) when the analysis slice is empty or holds a non-finite
    sample, when `sr` is not a positive finite number, or when the band
    (60, 10000) Hz holds no spectral energy -- exact silence."""
    m = np.asarray(x[len(x) // 4: 3 * len(x) // 4] if len(x) > 8192 else x, dtype=float)
    if m.size == 0 or not np.all(np.isfinite(m)):
        raise Refused("the steady middle of the clip is empty or holds a non-finite sample")
    if not np.isfinite(sr) or sr <= 0:
        raise Refused(f"sample rate {sr!r} is not a positive finite number")
    X = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    fr = np.fft.rfftfreq(len(m), 1.0 / sr)
    sel = (fr > 60) & (fr < 10000)
    if not sel.any() or not X[sel].max() > 0.0:
        raise Refused("no spectral energy in the 60-10000 Hz band: the clip is silent")
    Xs, f = X[sel], fr[sel]
    A = np.where(Xs > 0.0, 20.0 * np.log10(np.where(Xs > 0.0, Xs, 1.0)), -np.inf)
    i = int(np.argmax(Xs))
    fpk, apk = float(f[i]), float(A[i])
    below = A[(f > fpk * 0.25) & (f < fpk * 0.7)]
    passband = float(np.median(below)) if len(below) else float("nan")
    half = apk - 3.0
    lo = f[:i][A[:i] < half]
    hi = f[i:][A[i:] < half]
    bw = (hi[0] - lo[-1]) if len(lo) and len(hi) else float("nan")
    return dict(f_peak=fpk,
                peak_over_passband_db=float(apk - passband) if np.isfinite(passband) else float("nan"),
                Q=float(fpk / bw) if bw and np.isfinite(bw) and bw > 0 else float("nan"))


def _read_wav(path: str):
    """(sr, x) with x float, mono, centred and peak-normalised -- or Refused.

    Unsigned 8-bit PCM stores silence as 128, so it is centred before use: left
    alone the midpoint reads as DC and a silent file as a tone (#1745).
    Finiteness is checked BEFORE normalisation, so a non-finite float sample is
    a refusal rather than a RuntimeWarning out of the divide."""
    sr, x = wavfile.read(path)
    if x.dtype == np.uint8:
        x = x.astype(float) - 128.0
    else:
        x = x.astype(float)
    if x.ndim > 1:
        x = x.mean(1)
    if not np.all(np.isfinite(x)):
        raise Refused("the recording holds a non-finite sample")
    x = x / max(abs(x).max() if x.size else 0.0, 1.0)
    return sr, x


def scan(setdir: str, n_harm: int = 8, expect: int | None = None) -> list:
    """Harmonic fingerprint of every WAV in `setdir` -- or REFUSE.

    #600: this used to `continue` past any file that failed to read or was
    under half a second, so a damaged set yielded fewer rows and no error, and
    every "N of M" printed afterwards was over a denominator nobody chose. Now
    any skipped file, an empty set, or fewer usable rows than `expect` raises
    ScanRefused. `expect` exists because a file missing from disk is invisible
    to a scan of what is on disk; the caller must say how many there are.
    #1736: a WAV `harmonics` refuses -- silent, non-finite -- is skipped the
    same way; it used to be admitted as a fingerprint of 0 dB harmonics."""
    rows, skipped = [], []
    for p in sorted(glob.glob(os.path.join(setdir, "*.wav"))):
        name = os.path.basename(p)
        try:
            sr, x = _read_wav(p)
        except Refused as e:
            skipped.append((name, str(e)))
            continue
        except (ValueError, OSError, EOFError) as e:
            skipped.append((name, f"unreadable: {type(e).__name__}: {e}"))
            continue
        if len(x) < sr // 2:
            skipped.append((name, f"{len(x) / sr:.3f} s, under the 0.5 s minimum"))
            continue
        try:
            h = harmonics(x, sr, n_harm)
        except Refused as e:
            skipped.append((name, str(e)))
            continue
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


def _db(v):
    """A harmonic level for arithmetic. One of exactly zero (None) has no dB
    level, which is -inf: the purest there is, and never a TypeError (#1736)."""
    return float("-inf") if v is None else float(v)


def _fmt(v) -> str:
    """A harmonic level for a printed table."""
    return f"{_db(v):6.1f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default="/tmp/legowelt")
    ap.add_argument("--res", type=float, default=1.25)
    ap.add_argument("--expect", type=int, default=None,
                    help="number of recordings the set holds; fewer usable is REFUSED")
    a = ap.parse_args(argv)

    def selfosc_table(ring, label: str, what: str):
        """harmonics() of `ring` at every probe frequency, printed only once
        all three are in hand -- or None, having printed the refusal (#1736).
        Until then a ladder that was not ringing printed a flat 0 dB series
        here and the run went on to exit 0."""
        table = {}
        for f in (129.0, 258.0, 516.0):
            try:
                table[f] = harmonics(ring(f, a.res), SR)
            except Refused as e:
                print(f"\nREFUSED: {what} at {f:.0f} Hz, resonance {a.res}: {e}.")
                print("  No self-oscillation harmonic series could be read from it, so no")
                print("  structural separation -- and nothing below -- is a measurement.")
                return None
        for f, h in table.items():
            print(f"  {label} f0 {h['f0']:7.1f}  " +
                  "  ".join(f"h{n} {_fmt(h[f'h{n}'])}" for n in range(2, 7)))
        return table

    print("== what a ladder's self-oscillation LOOKS like (our model, DR 0001) ==")
    ours = selfosc_table(our_ladder_selfosc, "ours  ", "our ladder")
    if ours is None:
        return 2
    print("\n== the NEGATIVE CONTROL: one tanh in the feedback path only ==")
    onet = selfosc_table(one_tanh_selfosc, "1-tanh", "the one-tanh control")
    if onet is None:
        return 2
    # The ladder's tanh is ODD-symmetric, so a ladder ringing on its own emits
    # only ODD harmonics: h2 and h4 are absent by symmetry in BOTH structures
    # (ours -89..-104 dB, one-tanh -133..-160 dB), far under any recording's
    # noise floor. h2 is therefore useless here. The structural fingerprint is
    # h5 RELATIVE TO h3 -- how far the distortion spreads up the odd series,
    # which is exactly what having a tanh in every stage rather than one
    # changes. It is a ratio of harmonics, so it is level- and
    # settings-independent, which is what makes it usable without metadata.
    def spread(h):
        return _db(h["h5"]) - _db(h["h3"])
    sep = float(np.mean([abs(spread(ours[f]) - spread(onet[f])) for f in ours]))
    if not math.isfinite(sep):
        print("\nREFUSED: h5-h3 is not a finite number at every probe frequency (an h3 or")
        print("  h5 of exactly zero), so the two structures cannot be compared.")
        return 2
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
          ", ".join(f"{_db(ours[f]['h2']) - ours[f]['h3']:+.0f}" for f in sorted(ours)) + " dB.")
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
    # a recording whose h2 is above Nyquist has no h2 to rank by: it sorts last
    pure = sorted(rows, key=lambda r: (2 * r[3]["f0"] > r[1] / 2.0, _db(r[3]["h2"])))
    print("  the 10 purest tones in the set (lowest h2):")
    for nm, sr, dur, h in pure[:10]:
        na = lambda n: "   n/a" if n * h["f0"] > sr / 2.0 else _fmt(h[f"h{n}"])
        print(f"    {nm[:46]:46s} f0 {h['f0']:7.1f}  h2 {na(2)}  h3 {na(3)}")

    def observable(r, n: int) -> bool:
        """Harmonic n sits at or below Nyquist. Past it there is no level to
        compare -- unlike an exactly-zero one, which is -inf (#1745)."""
        return n * r[3]["f0"] <= r[1] / 2.0

    def comparable(r) -> bool:
        """Both h2 and h3 exist for this recording, so (a) and (b) can be asked."""
        return observable(r, 2) and observable(r, 3)

    def odd_symmetric(r) -> bool:
        """(a). An exactly-zero h2 is -inf and passes; an exactly-zero h3 is
        no level to compare against and cannot (#1736)."""
        h = r[3]
        return comparable(r) and h["h3"] is not None and _db(h["h2"]) - h["h3"] <= EVEN_ODD_MAX

    def near_sine(r) -> bool:
        """(b), which is a statement about h3 and so needs one."""
        return comparable(r) and r[3]["h3"] is not None and r[3]["h3"] <= H3_MAX

    admitted = [r for r in rows if odd_symmetric(r) and near_sine(r)]
    odd_only = [r for r in rows if odd_symmetric(r)]
    pure_only = [r for r in rows if near_sine(r)]
    unavailable = [r for r in rows if not comparable(r)]

    def median_of(vals) -> str:
        return f"{np.median(vals):.0f}" if vals else "n/a"

    print(f"\n  passing (a) odd-symmetric: {len(odd_only)} of {len(rows)} -- but these are "
          f"oscillator waveforms (median h3 "
          f"{median_of([r[3]['h3'] for r in odd_only])} dB, a square wave is -9.5)")
    print(f"  passing (b) near-sine:     {len(pure_only)} of {len(rows)} -- but these carry "
          f"even harmonics AT or ABOVE their odd ones (median h2-h3 "
          f"{median_of([_db(r[3]['h2']) - r[3]['h3'] for r in pure_only])} dB), which is an "
          f"asymmetric source in the path")
    if unavailable:
        print(f"  comparison UNAVAILABLE for {len(unavailable)} of {len(rows)} (h2 or h3 above "
              f"Nyquist, nothing to compare): "
              + ", ".join(r[0][:30] for r in unavailable[:5]))
    print(f"\n  recordings passing the admission test: {len(admitted)} of {len(rows)}")
    if not admitted and unavailable:
        print(f"  -> no measurable recording passes, but {len(unavailable)} of {len(rows)} "
              "could not be compared at all; no conclusion about the set is drawn from them.")
    if not admitted and len(unavailable) < len(rows):
        scope = "Every measurable one" if unavailable else "Every one"
        print(f"  -> NO measurable recording in this set is a ladder ringing on its own. {scope}"
              if unavailable else "  -> NO recording in this set is a ladder ringing on its own. Every one")
        print("     of them carries an even harmonic within 12 dB of its odd one, which")
        print("     an odd-symmetric ladder alone cannot produce: these are oscillator")
        print("     tones through the filter. THE AVAILABLE RECORDINGS DO NOT SUPPORT A")
        print("     LADDER-STRUCTURE PROBE. DR 0001 stays supported by circuit")
        print("     derivation alone, which is where it already was.")
    elif admitted:
        for nm, sr, dur, h in sorted(admitted, key=lambda r: _db(r[3]["h5"]) - r[3]["h3"])[:8]:
            print(f"    {nm[:44]:44s} f0 {h['f0']:7.1f}  h3 {h['h3']:6.1f}  "
                  f"h5-h3 {_db(h['h5']) - h['h3']:+6.1f}")

    for nm in ("SYNTH-SimpleThinSquareFilterSlope.wav", "WEIRD-ResonanceZone.wav"):
        p = os.path.join(a.set, nm)
        if not os.path.exists(p):
            continue
        try:
            sr, x = _read_wav(p)
            q = peak_shape(x, sr)          # the guarded probe first (#1745)
            r = rolloff_slope(x, sr)
        except Refused as e:
            print(f"\nREFUSED: {nm}: {e}.")
            print("  No peak shape was measured for it, so the characterisation above")
            print("  is incomplete.")
            return 2
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

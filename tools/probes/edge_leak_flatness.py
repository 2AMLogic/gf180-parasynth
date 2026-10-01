#!/usr/bin/env python3
"""What `promoted_measures.EDGE_LEAK_MAX` fires on BESIDES a straddle (#515).

`lowband_onset_db`'s edge guard refuses a band share when energy within one
Hann main lobe of a band edge exceeds `EDGE_LEAK_MAX` of the band's own. It was
written from ONE case: the #111 synthetic bass drum's 50 Hz line against a
40 Hz edge (`promoted_measures.EDGE_LEAK_MAX`'s own note). This probe exists
because the statistic has a SECOND, structural reason to exceed the threshold
which has nothing to do with a line sitting near an edge: **the adjacent region
is a fixed fraction of the band, so flat-spectrum content exceeds the threshold
by construction.**

Five parts, and the last two are what decide a design question rather than
describing one:

  A  the flat-spectrum expectation, from BIN COUNTS rather than from the
     nominal band widths, and the distribution of the statistic around it on
     white noise -- the spread is the point, not the mean.
  A2 the closed form that generalises A to any band and window, swept against
     measured refusal rates: the leak ring is two bins per side whatever the
     settings, so flat content is refused unless BAND WIDTH x WINDOW LENGTH
     exceeds 4 / EDGE_LEAK_MAX = 80 Hz.s. The default is 12.8.
  B  a tonal shell plus `a x` broadband noise, the sweep from #515's body,
     with the seeds fixed so the table is reproducible.
  C  the ordering that decides whether the threshold should be NORMALISED by
     the flat expectation: the #111 straddle's leak against white noise's.
  D  rule 8, form 1: a search for an input that SATISFIES the guard
     (leak <= EDGE_LEAK_MAX) while VIOLATING its intent (the 80 ms share is
     not the band share of the signal).

Run:  python tools/probes/edge_leak_flatness.py
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "model"))
import promoted_measures as pm  # noqa: E402

SR = 44100
BAND = (40.0, 200.0)
CLIP_MS = 240.0
WIN_MS = pm.ONSET_WINDOW_MS


def _bins(n: int, band=BAND):
    """The bin counts the statistic actually sums, not the nominal widths.

    Deliberately re-derived here instead of imported: a probe that asks the
    thing it is probing for its own denominator cannot tell you the denominator
    is wrong."""
    f = np.fft.rfftfreq(n, 1.0 / SR)
    lo, hi = band
    half = 2.0 * SR / n
    inb = int(((f >= lo) & (f < hi)).sum())
    adj = int((((f >= max(0.0, lo - half)) & (f < lo))
               | ((f >= hi) & (f < hi + half))).sum())
    return adj, inb, half, float(SR) / n


def _leak(x):
    """`_band_share_db`'s statistic on the first WIN_MS of `x`, via the
    estimator itself, so the number quoted is the number that ships."""
    e = pm.lowband_onset_db(x, SR, BAND, WIN_MS)
    return float(e.detail["edge_leak"]), e


def part_a(seeds: int = 400):
    n = int(round(WIN_MS * SR / 1000.0))
    adj, inb, half, df = _bins(n)
    print("A. the flat-spectrum expectation is STRUCTURAL, and the spread is "
          "wider than the mean")
    print(f"   {WIN_MS:g} ms at {SR} Hz -> n={n}, bin spacing {df:.2f} Hz, "
          f"main-lobe half-width {half:.1f} Hz")
    print(f"   adjacent bins {adj}, in-band bins {inb} -> flat expectation "
          f"{adj / inb:.4f}  (nominal widths give "
          f"{2 * half / (BAND[1] - BAND[0]):.4f})")
    print(f"   EDGE_LEAK_MAX = {pm.EDGE_LEAK_MAX:g}, i.e. "
          f"{adj / inb / pm.EDGE_LEAK_MAX:.1f}x below the flat expectation")
    ks = int(round(CLIP_MS * SR / 1000.0))
    vals, ok = [], 0
    for s in range(seeds):
        x = np.random.default_rng(s).standard_normal(ks)
        leak, e = _leak(x)
        vals.append(leak)
        ok += bool(e.ok)
    v = np.array(vals)
    qs = np.percentile(v, [5, 25, 50, 75, 95])
    print(f"   white noise, {seeds} seeds: mean {v.mean():.3f}  "
          f"5/25/50/75/95 % " + "/".join(f"{q:.3f}" for q in qs))
    print(f"   answered {ok}/{seeds}; min {v.min():.3f} max {v.max():.3f}")
    # If the periodogram bins were independent exponentials, leak would be
    # (adj/inb) * F(2*adj, 2*inb) and its mean adj/(inb-1). Hann correlates
    # neighbouring bins, so this is a reference point, not a prediction.
    print(f"   independent-bin reference: mean adj/(inb-1) = "
          f"{adj / (inb - 1):.3f}")
    return adj, inb


def part_a2(seeds: int = 60):
    """The closed form behind part A, swept: the Hann leak ring is ALWAYS two
    bins per side, so `flat_leak_ref` = 4 / (W x T) in bins and flat content
    crosses `EDGE_LEAK_MAX` at W x T = 4 / 0.05 = 80 Hz.s of band-width times
    window-length. The default 40-200 Hz at 80 ms is 12.8 Hz.s, 6.25x inside
    it."""
    print(f"\nA2. the law, swept: flat content is refused unless band width x "
          f"window length > {4.0 / pm.EDGE_LEAK_MAX:.0f} Hz.s")
    print("    (4 adjacent bins / (W x T) bins in band, against "
          f"EDGE_LEAK_MAX = {pm.EDGE_LEAK_MAX:g}; white noise, "
          f"{seeds} seeds each)")
    rows = [((40.0, 200.0), 80.0, 240.0),      # the default
            ((150.0, 600.0), 30.0, 240.0),     # the legal short-window case
            ((40.0, 200.0), 240.0, 600.0),
            ((100.0, 500.0), 240.0, 600.0),
            ((40.0, 1000.0), 80.0, 240.0),
            ((40.0, 200.0), 500.0, 900.0),     # W x T = 80 exactly
            ((40.0, 2000.0), 80.0, 240.0)]
    print("        band        T ms     W.T    adj/in-band    answered")
    for band, t_ms, clip_ms in sorted(rows, key=lambda r: (r[0][1] - r[0][0]) * r[1]):
        n = int(round(t_ms * SR / 1000.0))
        adj, inb, _half, _df = _bins(n, band)
        ok = 0
        for s in range(seeds):
            x = np.random.default_rng(s).standard_normal(
                int(round(clip_ms * SR / 1000.0)))
            ok += bool(pm.lowband_onset_db(x, SR, band, t_ms).ok)
        wt = (band[1] - band[0]) * t_ms / 1000.0
        print(f"   {band[0]:6.0f}-{band[1]:<6.0f} {t_ms:6.0f} {wt:8.1f}   "
              f"{adj:2d}/{inb:<4d} {adj / inb:7.4f}   {ok:3d}/{seeds}")
    print("   The crossing is a MEDIAN, not a boundary: at W.T = 80 exactly the "
          "verdict splits ~half and half,")
    print("   and even 2x past it one flat window in eight still refuses. The "
          "statistic is a ratio of 4 bins")
    print("   to W.T bins, so its spread does not shrink with the mean.")


def _shell_plus_noise(a: float, seed: int, f0: float = 180.0):
    """A tonal shell inside the band plus `a x` broadband noise. 180 Hz is
    inside 40-200 and more than one main-lobe half-width (25 Hz) from both
    edges, so any leak is the NOISE's, not the line's."""
    t = np.arange(int(round(CLIP_MS * SR / 1000.0))) / SR
    tone = np.sin(2 * np.pi * f0 * t) * np.exp(-t / 0.12)
    return tone + a * np.random.default_rng(1000 + seed).standard_normal(len(t))


def part_b():
    print("\nB. a 180 Hz shell (clear of both edges) plus `a x` broadband "
          "noise, 5 fixed seeds")
    print("    a     mean leak   answered   leaks")
    for a in (0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0):
        ls, ok = [], 0
        for s in range(5):
            leak, e = _leak(_shell_plus_noise(a, s))
            ls.append(leak)
            ok += bool(e.ok)
        print(f"   {a:4.1f}   {np.mean(ls):9.3f}     {ok}/5     "
              + " ".join(f"{x:.3f}" for x in ls)
              + ("   <- verdict depends on the realization"
                 if 0 < ok < 5 else ""))


def part_c(adj: int, inb: int):
    print("\nC. would normalising by the flat expectation keep the guard the "
          "guard? (the #515 design question)")
    t = np.arange(int(round(CLIP_MS * SR / 1000.0))) / SR
    flat = adj / inb
    rows = [("#111 straddle: 50 Hz line, 10 Hz above a 40 Hz edge",
             np.sin(2 * np.pi * 50 * t) * np.exp(-t / 0.12)),
            ("clear line: 90 Hz, 2 half-widths above the edge",
             np.sin(2 * np.pi * 90 * t) * np.exp(-t / 0.12)),
            ("white noise, seed 0", np.random.default_rng(0).standard_normal(len(t)))]
    for label, x in rows:
        leak, e = _leak(x)
        print(f"   {label}\n      leak {leak:.4f}  "
              f"leak/flat {leak / flat:6.3f}  "
              f"{'REFUSED' if not e.ok else f'{e.value:+.4f} dB'}")
    print(f"   flat expectation {flat:.4f}. A normalised threshold of 1.0 "
          f"admits the straddle the guard was built for;")
    print(f"   a normalised threshold of "
          f"{pm.EDGE_LEAK_MAX / flat:.3f} is the absolute "
          f"{pm.EDGE_LEAK_MAX:g} rewritten at THIS band and window.")


def _true_band_fraction(x):
    """The band's share of the WHOLE record's energy, rectangular window.

    Valid only for a signal that has decayed to ~0 well before the record ends
    (checked by the caller): then the record holds the entire signal, no taper
    is needed, and the DFT is the signal's own spectrum rather than a window's
    reading of it. This is the external answer part D compares against."""
    P = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / SR)
    tot = float(P.sum())
    frac = float(P[(f >= BAND[0]) & (f < BAND[1])].sum()) / tot
    return 10.0 * math.log10(frac)


def part_d():
    print("\nD. rule 8: an input that SATISFIES the guard and VIOLATES its "
          "intent (search, not assertion)")
    print("   candidate class: a fast-decaying line WELL INSIDE the band. The "
          "Hann taper multiplies a")
    print("   signal that is over in a few ms by a steep ramp, which broadens "
          "the line past the 25 Hz")
    print("   lobe the leak ring looks at -- so the ring can undercount while "
          "the share is corrupted.")
    ks = int(round(CLIP_MS * SR / 1000.0))
    t = np.arange(ks) / SR
    print("     f0   tau     leak   80 ms dB   whole-signal dB    error   "
          "guard")
    worst = None
    for f0 in (80.0, 120.0, 160.0):
        for tau_ms in (1.0, 2.0, 3.0, 5.0, 10.0, 30.0):
            x = np.sin(2 * np.pi * f0 * t) * np.exp(-t / (tau_ms / 1000.0))
            # the record must hold the whole signal for the reference to be the
            # signal's own spectrum: 240 ms is >= 8 tau for every row here
            assert CLIP_MS / tau_ms >= 8.0
            leak, e = _leak(x)
            truth = _true_band_fraction(x)
            got = e.value if e.ok else float("nan")
            err = got - truth
            flag = "passes" if e.ok else "REFUSES"
            print(f"   {f0:5.0f} {tau_ms:5.1f} {leak:8.4f} {got:10.4f} "
                  f"{truth:16.4f} {err:+9.4f}   {flag}")
            if e.ok and (worst is None or abs(err) > abs(worst[-1])):
                worst = (f0, tau_ms, leak, got, truth, err)
    if worst is None:
        print("   no row passed the guard at all -- no defeating input in this "
              "class")
        return
    f0, tau_ms, leak, got, truth, err = worst
    print(f"   worst PASSING row: f0={f0:g} Hz tau={tau_ms:g} ms, leak "
          f"{leak:.4f} <= {pm.EDGE_LEAK_MAX:g}, 80 ms reading {got:+.4f} dB "
          f"against the signal's own {truth:+.4f} dB -> {err:+.4f} dB")
    print(f"   for scale, the #111 straddle the guard DOES catch is 0.798 dB "
          f"of the same kind of error.")


if __name__ == "__main__":
    adj, inb = part_a()
    part_a2()
    part_b()
    part_c(adj, inb)
    part_d()

#!/usr/bin/env python3
"""A QUALIFIED 1-2.5 kHz decay for the 808 cymbal (#400; #369 step 6).

`docs/scorecard/cymbal-369/candidate3/README.md` section 6 found that our
1-2.5 kHz energy falls 5-11 dB further than the 808's between the strike window
(0-50 ms) and 50-300 ms, identically in the shipped kit, candidate 2 and
candidate 3 -- and labelled the finding NOT QUALIFIED in those words, because it
is an energy ratio between two fixed windows and none of
`tools/cymbal_bands.BANDS` (L 2-5 kHz, Ln 2.9-4.1 kHz, H 6-14 kHz) covers
1-2.5 kHz. This module is the qualified measurement it asked for. It does NOT
touch `cymbal_bands.BANDS` or `cymbal_bands.measure()`: every number already
committed under `docs/scorecard/cymbal-369/` stays reproducible byte for byte,
and this is a new, separately-frozen quantity rather than a redefinition of the
frozen one.

WHAT IS MEASURED, and why it is a ratio
---------------------------------------
Per record, per band, the time the band's floor-subtracted backward-integrated
(Schroeder) energy curve takes to fall to each of -5, -10, -15, -20, -25 and
-30 dB. No line is fitted, so the measurement never has to assume the decay is
one exponential -- which matters here, because the 808's own 1-2.5 kHz is not
(`cymbal_bands.band_decay` refuses its late T20 at a 1.5 dB residual bound).

The reported quantity is the WITHIN-RECORD ratio

    rho(d) = T_M(d) / T_Ln(d)

against Ln (2.9-4.1 kHz, the low band's own 3.45 kHz peak, imported from
`cymbal_bands.BANDS` so it cannot drift from the frozen definition). A ratio
taken inside one record is invariant to that record's gain, to peak
normalisation and to the loudness matching of the listening pack, so the 808
and our render can be compared without any level rule linking them -- which is
exactly what the level rule's known 2.7 dB error (the shipped kit's own H-L)
makes impossible for an absolute comparison.

    rho > 1  the 1-2.5 kHz band outlasts the low band's own peak
    rho < 1  it dies first

BANDS, frozen here before this module was used to judge anything
----------------------------------------------------------------
    M   891-2828 Hz   exactly the union of the five 1/3-octave bands the
                      section-6 finding reports (centres 1.0, 1.26, 1.59, 2.0,
                      2.5 kHz; edges 1000/2^(1/6) and 2500*2^(1/6))
    Mn  891-1782 Hz   the union of the lowest three of those (centres 1.0-1.59
                      kHz): the part furthest from the low band's 3.45 kHz
                      band-pass peak and from Hh1's 2.5 kHz corner, and so the
                      one least exposed to skirt leakage
    Ln  2900-4100 Hz  cymbal_bands.BANDS["Ln"], unchanged

ANALYSIS-FILTER REJECTION, stated because #101 was window leakage
-----------------------------------------------------------------
The band-pass is `cymbal_bands._bp` unchanged -- a 4th-order Butterworth run
zero-phase through `sosfiltfilt`, so its effective amplitude response is
|H(f)|^2. `rejection_db()` reports that response at the three frequencies that
could leak in, and `test_cymbal_low_tail.py` asserts the numbers:

    band  at 3450 Hz (low band-pass)  at 7100 Hz (shared)  at 10320 Hz (Hh3)
    M          -24.6 dB                    -91.3 dB            -125.8 dB
    Mn         -85.1 dB                   -147.0 dB            -180.8 dB

M's -24.6 dB at the low band's own peak is the honest weak point of the wide
band, and it is why Mn exists and is reported beside it in every table: Mn puts
60 dB more between itself and 3.45 kHz, so a conclusion that holds in M and NOT
in Mn is analysis leakage, and one that holds in both is not.

That is the ANALYSIS filter only. The physical leakage -- the 3.45 kHz Q 6
band-pass's own skirt reaching down into M -- is real, is not removable, and is
the thing the `skirt-blind` property below exists to bound: a record whose
1-2.5 kHz content is nothing but that skirt must read rho ~ 1, because the
skirt carries the low band's envelope and nothing else. `detection_floor()`
states, in the M band's own energy, how large a separate low component has to
be before rho(-10) can see it at all.

REFUSALS (a depth is refused, not answered)
-------------------------------------------
  * the band's Schroeder curve never reaches the depth;
  * the band's 50 ms envelope at the end of the analysed window is less than
    TRUNC_MARGIN_DB below its level where the curve crosses that depth (the
    missing tail would bias it). For a single exponential that bounds what is
    measurable in a window T to depths |d| < 8.686*T/tau - TRUNC_MARGIN_DB, so
    in the 2.0 s window the Fischer files force, -30 dB needs tau < ~0.39 s;
  * the band holds no energy above its floor;
  * the record is shorter than the common analysis window (see next).

THE PRECONDITION THAT IS ASSERTED, NOT ASSUMED
----------------------------------------------
A Schroeder curve is computed from the END of the record backwards, so its
depths are NOT comparable between records of different length. The Fischer CY
files are 2.013 s; `run_case.render_drum_solo("CY")` is 3.6 s. Every record is
therefore trimmed to the same TRIM_S = 2.0 s window after t = 0 before anything
is measured, and a record that cannot supply it REFUSES.

Honest note on how much that trim buys: on the shipped render the untrimmed
(3.6 s) and trimmed (2.0 s) readings differ by 0.6 % at T_M(-10) (517.3 ->
514.4 ms), because the floor is subtracted and there is almost no energy left
out there to integrate. The trim is therefore a cheap precondition rather than a
large correction -- but the SHORT side of it is not cheap: the same record cut
to 1.5 s reads 500 ms, and the `refuse-short-record` property below is what
stops the tool answering at all in that case.

CONTROLS: `--check`
-------------------
Seven named properties over synthetic strikes with planted time constants, and a
properties x defects matrix over six injected defects (each of which must turn
at least one property red) plus one transformation asserted BLIND by
construction and verified to stay blind. `--check` exits non-zero if any
property fails on the clean measurement, if any defect is caught by nothing, or
if the blind transformation moves anything. `main()` runs it BEFORE measuring
any record and REFUSES to report a result if it does not pass.

Wrong-then-right rate of this module: 2 of the numbers below were wrong before
they were right, and the controls caught both rather than inspection.
  1. `t_edt` said a Schroeder curve falls 2*8.686/tau dB/s, so every planted
     time was half what it should have been; `edt-known` failed on the first
     run. See `t_edt`'s docstring.
  2. The first control matrix had THREE defects that turned nothing red
     (NO_TRIM, NO_TRUNC_GUARD, NO_FLOOR_SUB) and a separate noise-floor guard
     that could never fire before the end-margin guard. The cases were rebuilt
     until each defect had a property it genuinely moved, and the redundant
     guard was deleted rather than kept as a control that cannot fail.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import subprocess
import sys

import numpy as np
from scipy.signal import butter, lfilter, sosfilt, sosfreqz

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import run_case as rc  # noqa: E402
import cymbal_bands as cb  # noqa: E402

# FROZEN before this module was used to judge anything (#400 acceptance 1).
LOW_BANDS = {"M": (891.0, 2828.0), "Mn": (891.0, 1782.0)}
REF_BAND = "Ln"                                   # cymbal_bands.BANDS["Ln"]
BANDS = dict(LOW_BANDS, **{REF_BAND: cb.BANDS[REF_BAND]})
DEPTHS = (-5.0, -10.0, -15.0, -20.0, -25.0, -30.0)
TRIM_S = 2.0                     # the Fischer CY files are 2.013 s; see above
TRUNC_MARGIN_DB = 15.0
ENERGY_S = 1.0
ONSET_S = 0.005                  # the click window of the onset-confound figure

# The three frequencies that could leak into M or Mn: the low band's own
# band-pass, the shared high band-pass, and Hh3's 2-pole corner (§10).
LEAK_HZ = (3450.0, 7100.0, 10320.0)


class Refused(RuntimeError):
    pass


# --------------------------------------------------------------------------
# the measurement
# --------------------------------------------------------------------------
def rejection_db(sr: int = 48_000) -> dict:
    """The ANALYSIS band-pass's effective rejection at LEAK_HZ, relative to its
    own peak. `cymbal_bands._bp` runs `butter(4, ...)` through `sosfiltfilt`,
    which applies it forward and backward, so the effective amplitude response
    is |H(f)|^2 -- that square is included here rather than quoted as the
    one-way response, because the square is what the measurement sees."""
    out = {}
    for name, (lo, hi) in BANDS.items():
        sos = butter(4, [lo, min(hi, 0.45 * sr)], btype="bandpass", fs=sr, output="sos")
        w, h = sosfreqz(sos, worN=1 << 17, fs=sr)
        eff = 20 * np.log10(np.abs(h) ** 2 + 1e-300)
        peak = float(eff.max())
        out[name] = {f"{f:.0f}": round(float(eff[np.argmin(np.abs(w - f))]) - peak, 2) for f in LEAK_HZ}
    return out


def _trim(y, sr, trim_s):
    """`y` must come through run_case.prepare. Cut to exactly `trim_s` after
    t = 0, keeping the guaranteed lead, and REFUSE if it is not there."""
    o = rc.required_lead_samples(sr)
    need = o + int(round(trim_s * sr))
    if len(y) < need:
        raise Refused(f"record is {(len(y) - o) / sr:.3f} s after t=0, "
                      f"less than the {trim_s:.3f} s common analysis window")
    return np.asarray(y[:need], dtype=np.float64)


def schroeder_times(x, sr, depths=DEPTHS, *, defect=None) -> dict:
    """Times (ms) for one band's floor-subtracted Schroeder curve to reach each
    depth, or None with a reason. No line is fitted: the quantity is a crossing
    time, so it is defined whether or not the decay is one exponential."""
    x = np.asarray(x, dtype=np.float64)
    p = x * x
    nf = max(4, int(0.1 * sr))
    floor = float(np.mean(p[-nf:]))
    sm = max(1, int(0.05 * sr))
    env = np.convolve(p, np.ones(sm) / sm, mode="same")
    pk = float(np.max(env))
    if pk <= 0:
        raise Refused("band is silent")
    q = p - (0.0 if defect == "NO_FLOOR_SUB" else floor)
    q = np.maximum(q, 0.0)
    sch = np.cumsum(q[::-1])[::-1] if defect != "FORWARD_INTEGRAL" else np.cumsum(q)
    if sch[0] <= 0:
        raise Refused("band holds no energy above its floor")
    c = 10 * np.log10(np.maximum(sch / sch[0], 1e-30))
    t = np.arange(len(c)) / sr
    lvl_end = 10 * math.log10(max(float(np.mean(env[-sm:])), 1e-30) / pk)
    floor_db = 10 * math.log10(max(floor, 1e-30) / pk)
    out = {"floor_db_re_peak": round(floor_db, 2), "times_ms": {}, "refused": {}, "margin_db": {}}
    for d in depths:
        key = f"{d:.0f}"
        i = np.nonzero(c <= d)[0]
        if not len(i):
            out["times_ms"][key] = None
            out["refused"][key] = f"the energy curve never reaches {d:.0f} dB"
            continue
        j = int(i[0])
        lvl = 10 * math.log10(max(env[j], 1e-30) / pk)
        out["margin_db"][key] = round(lvl - lvl_end, 2)
        if defect != "NO_TRUNC_GUARD" and lvl - lvl_end < TRUNC_MARGIN_DB:
            out["times_ms"][key] = None
            out["refused"][key] = (f"analysed window too short: the envelope at its end is only "
                                   f"{lvl - lvl_end:.1f} dB below its level at the {d:.0f} dB "
                                   f"crossing (need {TRUNC_MARGIN_DB})")
            continue
        out["times_ms"][key] = round(1e3 * float(t[j]), 2)
    return out


def measure(y, sr, *, trim_s=TRIM_S, defect=None) -> dict:
    """Per-band Schroeder times, the within-record ratios rho(d) against Ln, and
    the onset-energy share each band carries (the confound rho's early depths
    are exposed to). `y` must come through run_case.prepare."""
    y = _trim(y, sr, trim_s) if defect != "NO_TRIM" else np.asarray(y, dtype=np.float64)
    bands = dict(BANDS)
    if defect == "WIDE_M":
        bands["M"] = (891.0, 4100.0)              # swallows Ln's own peak (#101's failure mode)
    if defect == "SWAP_BANDS":
        bands = dict(bands, M=BANDS[REF_BAND], **{REF_BAND: BANDS["M"]})
    n_e = int(ENERGY_S * sr)
    n_on = max(1, int(ONSET_S * sr))
    res = {"sr": sr, "trim_s": trim_s, "window_s": round((len(y) - rc.required_lead_samples(sr)) / sr, 4),
           "bands": {}}
    for name, (lo, hi) in bands.items():
        xb = cb._bp(y, sr, lo, hi)
        r = schroeder_times(xb, sr, defect=defect)
        e1 = float(np.sum(xb[:n_e] ** 2))
        e_on = float(np.sum(xb[:n_on] ** 2))
        r["onset_share_db"] = (round(10 * math.log10(e_on / e1), 2) if e1 > 0 and e_on > 0 else None)
        r["energy_j"] = e1
        res["bands"][name] = r
    ref = res["bands"][REF_BAND]["times_ms"]
    res["rho"] = {}
    for name in ("M", "Mn"):
        res["rho"][name] = {k: (None if res["bands"][name]["times_ms"].get(k) is None or ref.get(k) is None
                                else round(res["bands"][name]["times_ms"][k] / ref[k], 4))
                            for k in (f"{d:.0f}" for d in DEPTHS)}
    res["onset_excess_db"] = {
        name: (None if res["bands"][name]["onset_share_db"] is None or ref_on is None
               else round(res["bands"][name]["onset_share_db"] - ref_on, 2))
        for name, ref_on in ((n, res["bands"][REF_BAND]["onset_share_db"]) for n in ("M", "Mn"))}
    return res


# --------------------------------------------------------------------------
# synthetic strikes with planted constants (the known answers)
# --------------------------------------------------------------------------
SR = 48_000


def _q6(f0, q, n, seed, fs=SR):
    """The 808's own band-pass shape: a 2-pole/2-zero constant-skirt-gain
    band-pass (RBJ cookbook), the same analytic filter
    `test_cymbal_bands._q6_skirt_noise` uses -- so the synthetic low band has a
    REAL skirt reaching down into M, not a steep synthetic window with none
    (#376: a control with no skirt cannot fail)."""
    w0 = 2 * math.pi * f0 / fs
    al = math.sin(w0) / (2 * q)
    b = [q * al, 0.0, -q * al]
    a = [1 + al, -2 * math.cos(w0), 1 - al]
    b, a = [c / a[0] for c in b], [c / a[0] for c in a]
    return lfilter(b, a, np.random.default_rng(seed).standard_normal(n))


def _bandnoise(lo, hi, n, seed, fs=SR):
    return sosfilt(butter(6, [lo, hi], btype="bandpass", fs=fs, output="sos"),
                   np.random.default_rng(seed).standard_normal(n))


def synth(*, tau_l=0.35, tau_m=None, a_m=0.0, tau_h=0.05, a_h=0.5,
          click=0.0, floor=0.0, dur=3.0, seed=7, sr=SR):
    """A three-band strike with every constant planted.

    low band   3.45 kHz Q 6 skirt noise x exp(-t/tau_l). Its own skirt is the
               ONLY 891-2828 Hz content unless `a_m > 0`, which is what makes
               the `skirt-blind` property a real test.
    M source   891-2828 Hz noise x exp(-t/tau_m) at amplitude `a_m` -- a
               deliberately separate low component, the thing the 808 might
               have and we might not.
    high band  7.1 kHz Q 6 skirt noise x exp(-t/tau_h) at `a_h`.
    click      a single-sample impulse at t = 0 of this amplitude: the onset
               confound, broadband and therefore biggest in the widest band.
    floor      additive white noise at this amplitude.
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    y = _q6(3450.0, 6.0, n, seed, sr) * np.exp(-t / tau_l)
    y = y + a_h * _q6(7100.0, 6.0, n, seed + 1, sr) * np.exp(-t / tau_h)
    if a_m > 0.0:
        y = y + a_m * _bandnoise(891.0, 2828.0, n, seed + 2, sr) * np.exp(-t / (tau_m or tau_l))
    y = y / float(np.max(np.abs(y)))
    if click:
        y[0] += click
    if floor:
        y = y + floor * np.random.default_rng(seed + 3).standard_normal(n)
    return rc.prepare(np.concatenate([np.zeros(sr // 20), y]), sr, side="synthetic"), sr


def t_edt(tau, depth=-10.0):
    """When does a planted single exponential's Schroeder curve reach `depth`?

    An AMPLITUDE envelope exp(-t/tau) has power exp(-2t/tau), whose backward
    integral is (tau/2)exp(-2t/tau), so in dB the Schroeder curve is
    10*log10(exp(-2t/tau)) = -(20/ln 10)*t/tau = -8.686*t/tau dB -- that is
    8.686/tau dB per second, NOT 2*8.686/tau. An earlier revision of this
    docstring said the latter and the value below was therefore twice the
    planted time; the `edt-known` property below caught it on the first run
    (wrong-then-right 1 of this module). Consequence worth keeping in mind: a
    depth d needs |d|*tau/8.686 seconds of RECORD, so inside the 2.0 s window
    the Fischer files force, -30 dB is only reachable with TRUNC_MARGIN_DB to
    spare when tau < ~0.39 s."""
    return -depth * tau / (20.0 / math.log(10.0)) * 1e3


# --------------------------------------------------------------------------
# properties and the injected defects that must turn them red
# --------------------------------------------------------------------------
PROPERTIES = ("rho-tracks", "skirt-blind", "edt-known", "depth-monotone",
              "refuse-truncated", "refuse-short-record", "floor-subtracted")
DEFECTS = ("FORWARD_INTEGRAL", "WIDE_M", "SWAP_BANDS", "NO_TRIM",
           "NO_TRUNC_GUARD", "NO_FLOOR_SUB")
BLIND_BY_CONSTRUCTION = ("SCALE_2X",)
# There is deliberately NO separate noise-floor guard, and that is a measured
# decision rather than an omission. A band-level "the floor's energy must be
# FLOOR_MARGIN_DB below the band's own" test was written first and then dropped:
# on every synthetic case tried (tau 0.10-0.80 x floor 0-3e-2) the END-MARGIN
# guard already refuses at or before it, because a record whose tail is
# noise-limited has, by construction, a small envelope margin at the end. A
# second guard that cannot fire first is a control that cannot fail (#376's
# vacuous-crosstalk lesson), so it is not claimed as one. The floor is reported
# (`floor_db_re_peak`) and subtracted (`floor-subtracted` below is the property
# that the subtraction works), just not separately gated.

# One planted case per property, built once (they are the expensive part).
_CACHE: dict = {}


def _case(name):
    if name not in _CACHE:
        _CACHE[name] = _build(name)
    return _CACHE[name]


def _build(name):
    if name == "slow_m":
        # A planted M component decaying 2.5x slower than the low band.
        return synth(tau_l=0.35, tau_m=0.875, a_m=0.35)
    if name == "skirt_only":
        return synth(tau_l=0.35, a_m=0.0)
    if name == "single":
        # One band only, so every band reads the same planted exponential.
        return synth(tau_l=0.35, a_h=0.0, a_m=0.0)
    if name == "slow_tail":
        # Signal-limited: at tau 0.5 the margin at a depth d inside a 2.0 s
        # window is 8.686/tau*2.0 - |d| = 34.7 - |d| dB, so -10 answers with
        # 24.7 dB to spare and -30 must refuse with 4.7. Not a noise case --
        # this record's floor is the signal itself.
        return synth(tau_l=0.50, a_h=0.0, dur=3.0)
    if name == "too_short":
        y, sr = synth(tau_l=0.20, a_h=0.0, dur=3.0)
        return y[: rc.required_lead_samples(sr) + int(1.0 * sr)], sr
    if name == "quiet":
        return synth(tau_l=0.10, a_h=0.0, dur=3.0)
    if name == "noisy":
        # The SAME planted decay with a noise floor added: 0.03 puts the Ln
        # band's floor at -30.6 dB re its peak, big enough to bend the tail and
        # small enough that -10 dB still answers with margin. The paired
        # `quiet` case above is what it is compared against, so the property is
        # "adding a floor must not move the reading", which the subtraction is
        # the only thing making true.
        return synth(tau_l=0.10, a_h=0.0, dur=3.0, floor=0.03)
    raise KeyError(name)


def _tf(v, *bounds):
    lo, hi = bounds
    return (lo <= v <= hi), v


def properties(*, defect=None) -> dict:
    """Each property is (passed, value). `defect` injects a fault into the
    MEASUREMENT (never into the synthetic signal), so a defect that is caught
    by nothing is a property that cannot fail."""
    scale = 2.0 if defect == "SCALE_2X" else 1.0
    d = None if defect == "SCALE_2X" else defect
    out = {}

    y, sr = _case("slow_m")
    m = measure(scale * y, sr, defect=d)
    out["rho-tracks"] = _tf(m["rho"]["M"]["-10"] or 0.0, 1.10, 2.50)

    y, sr = _case("skirt_only")
    m = measure(scale * y, sr, defect=d)
    out["skirt-blind"] = _tf(m["rho"]["M"]["-10"] or 0.0, 0.90, 1.10)

    y, sr = _case("single")
    m = measure(scale * y, sr, defect=d)
    got = m["bands"][REF_BAND]["times_ms"]["-10"]
    want = t_edt(0.35, -10.0)
    out["edt-known"] = ((got is not None and abs(got - want) / want < 0.05),
                        None if got is None else round(got / want, 4))
    ts = [m["bands"][REF_BAND]["times_ms"][f"{x:.0f}"] for x in (-10.0, -20.0, -30.0)]
    out["depth-monotone"] = ((all(v is not None for v in ts) and ts[0] < ts[1] < ts[2]
                              and abs(ts[1] / ts[0] - 2.0) < 0.10),
                             None if ts[0] in (None, 0) or ts[1] is None else round(ts[1] / ts[0], 3))

    y, sr = _case("slow_tail")
    m = measure(scale * y, sr, defect=d)
    ref = m["bands"][REF_BAND]
    out["refuse-truncated"] = ((ref["times_ms"]["-30"] is None and "-30" in ref["refused"]
                                and ref["times_ms"]["-10"] is not None),
                               ref["times_ms"]["-30"])

    y, sr = _case("too_short")
    try:
        measure(scale * y, sr, defect=d)
        out["refuse-short-record"] = (False, "answered a 1.0 s record on a 2.0 s window")
    except Refused as e:
        out["refuse-short-record"] = (True, str(e)[:44])

    y, sr = _case("quiet")
    q = measure(scale * y, sr, defect=d)["bands"][REF_BAND]["times_ms"]["-10"]
    y, sr = _case("noisy")
    nz = measure(scale * y, sr, defect=d)["bands"][REF_BAND]["times_ms"]["-10"]
    out["floor-subtracted"] = ((q is not None and nz is not None and abs(nz / q - 1.0) < 0.04),
                               None if q in (None, 0) or nz is None else round(nz / q, 4))
    return out


def check() -> tuple[bool, list[str]]:
    lines, ok = [], True
    clean = properties()
    lines.append("  clean measurement")
    for p in PROPERTIES:
        passed, v = clean[p]
        lines.append(f"    {p:18s} {'PASS' if passed else 'FAIL'}   {v}")
        ok = ok and passed
    lines.append("")
    lines.append(f"  {'defect':18s} " + " ".join(f"{p:>17s}" for p in PROPERTIES))
    caught = []
    for name in DEFECTS + BLIND_BY_CONSTRUCTION:
        try:
            got = properties(defect=name)
            cells = ["MOVED" if got[p][0] is False else "blind" for p in PROPERTIES]
        except Refused as e:                  # a defect that makes the tool refuse is caught
            got, cells = None, ["REFUSED"] + [""] * (len(PROPERTIES) - 1)
        lines.append(f"  {name:18s} " + " ".join(f"{c:>17s}" for c in cells))
        n = sum(1 for c in cells if c in ("MOVED", "REFUSED"))
        if name in DEFECTS:
            caught.append(n > 0)
            if n == 0:
                ok = False
                lines.append(f"    ^ CONTROL CANNOT FAIL: {name} turned nothing red")
        elif n:
            ok = False
            lines.append(f"    ^ {name} is asserted BLIND by construction and moved something")
    lines.append("")
    lines.append(f"  {sum(caught)}/{len(DEFECTS)} injected defects turned at least one property red; "
                 f"{len(BLIND_BY_CONSTRUCTION)} asserted blind and verified blind")
    return ok, lines


# --------------------------------------------------------------------------
# against the recordings and our renders
# --------------------------------------------------------------------------
def fischer(refs: pathlib.Path, settings=None) -> dict:
    out = {}
    for s in (settings or [f"CY{t}{d}" for t in cb.CODES for d in cb.CODES]):
        p = refs / "cy8" / f"{s}.WAV"
        x, sr = cb._load(p)
        out[s] = measure(rc.prepare(x, sr, side=p.name), sr)
    return out


def shipped() -> dict:
    x, sr = rc.render_drum_solo("CY")
    return measure(rc.prepare(x, sr, side="shipped CY"), sr)


def candidate() -> dict:
    """The #369 candidate at the shipped setting, through the same calibration
    and render path `tools/cymbal_candidate_eval.py` uses (so the candidate
    measured here is the candidate that tool reports on). ~2 min."""
    import cymbal_candidate as cc
    import cymbal_candidate_eval as ce
    cal = ce.calibrate()
    y, sr = cc.render(ce.kit_with_levels(cal["amps"]), "CY")
    return measure(rc.prepare(y, sr, side="candidate CY"), sr)


def onset_sensitivity(levels=(0.0, 0.02, 0.05, 0.1, 0.2, 0.4)) -> list[dict]:
    """How far a broadband click at t = 0 can move rho, and what it costs in the
    onset-energy share that the same measurement reports for every record. This
    bounds the one confound rho's early depths have: a click is broadband, so it
    lands in M (1937 Hz wide) harder than in Ln (1200 Hz wide) and drags rho
    DOWN. If a click large enough to explain the 808-vs-ours gap would also show
    up as an onset share the records do not have, the click explanation is
    excluded -- and if it would not, that has to be said."""
    rows = []
    for c in levels:
        y, sr = synth(tau_l=0.35, a_h=0.5, click=c)
        m = measure(y, sr)
        rows.append({"click": c,
                     "rho_M_-10": m["rho"]["M"]["-10"],
                     "onset_excess_db": m["onset_excess_db"]["M"],
                     "M_onset_share_db": m["bands"]["M"]["onset_share_db"]})
    return rows


def detection_floor(levels=(0.0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.7)) -> list[dict]:
    """The smallest planted M component (tau 2.5x the low band's) that rho(-10)
    can see, expressed in the M band's own energy -- the instrument's floor,
    stated rather than assumed."""
    base = measure(*_case("skirt_only"))
    e0 = base["bands"]["M"]["energy_j"]
    rows = [{"a_m": 0.0, "M_energy_vs_skirt_db": 0.0, "rho_M_-10": base["rho"]["M"]["-10"]}]
    for a in levels[1:]:
        y, sr = synth(tau_l=0.35, tau_m=0.875, a_m=a)
        m = measure(y, sr)
        rows.append({"a_m": a,
                     "M_energy_vs_skirt_db": round(10 * math.log10(m["bands"]["M"]["energy_j"] / e0), 2),
                     "rho_M_-10": m["rho"]["M"]["-10"]})
    return rows


def _row(label, m) -> str:
    r, rn = m["rho"]["M"], m["rho"]["Mn"]
    f = lambda v: " REF " if v is None else f"{v:5.3f}"
    return (f"{label:14s} " + " ".join(f(r[f'{d:.0f}']) for d in DEPTHS) + "  | Mn "
            + " ".join(f(rn[f'{d:.0f}']) for d in DEPTHS)
            + f"  | onset M-Ln {m['onset_excess_db']['M']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--check", action="store_true", help="the properties x defects matrix only")
    ap.add_argument("--all-settings", action="store_true", help="all 25 Fischer CY files, not just CY5025")
    ap.add_argument("--candidate", action="store_true", help="also render and measure the #369 candidate (~2 min)")
    a = ap.parse_args(argv)

    if a.check:
        ok, lines = check()
        print("\n".join(lines))
        print("\n".join(f"  rejection {b}: " + " ".join(f"{k} Hz {v:+.1f} dB" for k, v in d.items())
                        for b, d in rejection_db().items()))
        return 0 if ok else 1

    res = {"rejection_db": rejection_db(), "depths": list(DEPTHS), "trim_s": TRIM_S,
           "bands": {k: list(v) for k, v in BANDS.items()}}
    ok, lines = check()
    res["controls_pass"] = ok
    print("\n".join(lines))
    if not ok:
        print("REFUSED: the measurement's own controls do not pass; no result is reported")
        return 1

    settings = None if a.all_settings else ["CY5025"]
    res["fischer"] = fischer(pathlib.Path(a.refs), settings)
    res["shipped"] = shipped()
    if a.candidate:
        res["candidate"] = candidate()
    res["onset_sensitivity"] = onset_sensitivity()
    res["detection_floor"] = detection_floor()

    print(f"\n{'record':14s} " + " ".join(f"{d:>5.0f}" for d in DEPTHS) + "  | Mn at the same depths"
          + " " * 20 + "| onset M-Ln")
    for s, m in res["fischer"].items():
        print(_row("808 " + s, m))
    print(_row("shipped", res["shipped"]))
    if a.candidate:
        print(_row("candidate", res["candidate"]))
    print("\nonset (click) sensitivity: " + ", ".join(
        f"click {r['click']}: rho {r['rho_M_-10']}, onset M-Ln {r['onset_excess_db']} dB"
        for r in res["onset_sensitivity"]))
    print("detection floor: " + ", ".join(
        f"+{r['M_energy_vs_skirt_db']} dB -> rho {r['rho_M_-10']}" for r in res["detection_floor"]))

    res["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                   capture_output=True, text=True).stdout.strip()
    res["sources_dirty"] = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "model", "tools"],
                                          cwd=ROOT).returncode != 0
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(res, indent=1, default=float) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

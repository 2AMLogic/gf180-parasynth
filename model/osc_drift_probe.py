#!/usr/bin/env python3
"""Oscillator drift, measured as a pitch trajectory on a SUSTAINED tone.

`spec/NUMERIC-CONTRACT.md` 17 open item 18 asked whether per-unit oscillator
drift should be modelled and how much. This module is the measurement side of
that question: given a held tone it answers "does this oscillator's pitch
wander, by how much, how fast, and is the wander bounded or a random walk".

WHY THIS AND NOT A PROXY
------------------------
Issue #138 proposed a multi-period-fold variance as a direct read of analog
instability, then RETRACTED it: the fold strides (2, 3, 5, 7, 11 samples) are
not commensurate with this instrument's cycle lengths, so a PERFECTLY STABLE
tone produces differing fold rows because the phase walks across a
non-matching stride. The correction, and this module's whole premise: measure
the pitch trajectory itself, and carry a control that separates drift from
ordinary beating.

**Static detuning alone produces a moving waveform.** Two stable oscillators a
few cents apart beat, and beating pushes the zero crossings around
periodically. Any estimator that reports "the pitch is moving" on that mix is
measuring beating, not drift. `test_osc_drift_probe.py` runs exactly that
signal as a negative control and this module must not call it DRIFTING.

METHOD
------
Two independent estimators over the same window, both from scratch:

  zc   interpolated SAME-DIRECTION zero crossings of the band-passed
       fundamental, one frequency estimate per period -- the methodology of
       `model/tom_pitch_probe.py`, whose `bandpass`, `crossings` and Schmitt
       trigger are IMPORTED rather than copied, adapted for a sustained tone
       (no decay, no excitation window to exclude). Same-direction because a
       full period cancels a DC offset and even-order asymmetry to first
       order.
  het  heterodyne: multiply by exp(-2 pi i f0 t), average over an integer
       number of nominal periods (which nulls the harmonics and the negative
       image), unwrap the phase and difference it. Regularly sampled, far
       lower noise, and completely unlike `zc` in its failure modes -- so
       a disagreement between the two is a reason to refuse.

The two must agree on the window's mean f0 to `XCHECK_CENTS`; `het` carries
the trajectory and `zc` measures the PER-FILE noise floor from its own
period-to-period scatter, exactly as `tom_pitch_probe` measures a floor per
file rather than quoting one as a constant.

FOUR OUTCOMES, AND REFUSED IS ONE OF THEM
-----------------------------------------
  REFUSED   a precondition of the apparatus is unmet. Nothing is reported.
  STABLE    the slow component of the trajectory does not beat the window's
            own measured floor by `SNR_SIGMA`.
  PERIODIC  the trajectory wanders, but the wander REPEATS (vibrato) or
            tracks the amplitude envelope (beating between partials). This is
            the verdict the static-detune control must get -- not DRIFTING.
  DRIFTING  the trajectory wanders, the wander does not repeat, and it does
            not track the envelope.

Preconditions asserted at the point of use, each its own refusal code:

  SR_MISMATCH        the file's rate is not the rate the caller declared
  TOO_FEW_PERIODS    fewer than MIN_PERIODS clean periods in the window
  LOW_LEVEL          the window sits within LEVEL_HEADROOM_DB of the file floor
  NO_TRACK           the zero-crossing tracker produced no usable series
  ESTIMATOR_DISAGREE zc and het disagree on the mean f0 by > XCHECK_CENTS
  MULTI_PARTIAL      the fundamental's envelope ripples by more than one
                     oscillator's can, so the window holds a MIX and its pitch
                     is not any one oscillator's pitch
  SHORT_FOR_WOBBLE   the window spans fewer than MIN_TAUS correlation times of
                     the wander it is about to call APERIODIC

The last two are the honest cost of a short note and of a chord. A window
holding one monotone excursion is equally the first quarter of something slow
and periodic, and no estimator can tell those apart; the probe refuses rather
than picking one. A drift magnitude here therefore needs SECONDS of ONE
oscillator held, which is a constraint on the stimulus, not a bug.

WHAT SEPARATES DRIFT FROM BEATING, in one sentence
--------------------------------------------------
Beating modulates the fundamental's AMPLITUDE and its apparent FREQUENCY
together -- measured at r = 1.00 on a statically-detuned pair, at every window
length down to a seventh of one beat cycle -- while a drifting single
oscillator's amplitude does not move at all (r <= 0.50 over ten seeds, ripple
0.003 dB against the pair's 0.4-1.0 dB). That correlation, not a window length
and not a spectral shape, is the discriminator.
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

from tom_pitch_probe import bandpass, crossings, read_wav, spectral_f0  # noqa: E402

# ---- the apparatus's constants ---------------------------------------------
MIN_PERIODS = 24           # clean periods needed before anything is reported
SNR_SIGMA = 3.0            # the slow component must beat this many floor sigma
XCHECK_CENTS = 5.0         # zc vs het agreement on the window's mean f0
LEVEL_HEADROOM_DB = 30.0   # the window must sit this far above the file's floor
BP_BAND = (0.55, 1.60)     # band-pass edges x f0: the fundamental alone. 1.60
                           #   is below h2 at 2.0, so no harmonic enters the band
HET_PERIODS = 3            # nominal periods in the heterodyne average
HET_CASCADE = 2            # boxcars in series. MEASURED, not chosen: ONE
                           #   boxcar's nulls land on multiples of SR/L, the
                           #   harmonics land near but not on them, and the
                           #   leakage beats -- 0.0038 cents of SLOW wobble on
                           #   a tone that is exactly stable, which is above
                           #   this probe's own floor and would be reported as
                           #   drift. Two boxcars make the nulls second order
                           #   and take that to 1e-6 cents (10x below the
                           #   floor); three buys nothing further.
OUT_RATE = 200.0           # trajectory sample rate, Hz
EDGE_PERIODS = 16          # periods dropped at each end of the zero-crossing
                           #   series: `tom_pitch_probe.bandpass` is a
                           #   zero-phase filtfilt and its pre/post-ringing
                           #   puts crossings up to 0.3 samples out of place
                           #   (59 cents on one period) for about ten periods
                           #   at each end. Measured; the interior periods sit
                           #   at 1e-5 cents.
SLOW_S = 0.20              # the smoother that defines "slow"; faster than this
                           #   is the estimator's own scatter, not pitch
MIN_TAUS = 5.0             # the window must span this many CORRELATION TIMES of
                           #   the wander it is about to call aperiodic. A third
                           #   of one beat cycle is a monotone ramp and is
                           #   indistinguishable from drift IN PRINCIPLE; the
                           #   correlation time is what detects that, and the
                           #   dominant spectral bin is not -- an
                           #   Ornstein-Uhlenbeck wander piles its power in the
                           #   lowest bin by construction, so a bin-based test
                           #   refuses exactly the signal it exists to accept
PERIODIC_R = 0.55          # |corr(deviation, envelope)| at or above this, with
AM_DEPTH_DB = 0.10         # this much envelope RIPPLE, is BEATING
MULTI_PARTIAL_DB = 2.0     # envelope ripple above this refuses outright: the
                           #   analysis band holds more than one partial, and
                           #   the pitch of a MIX is not any oscillator's pitch
REPEAT_R = 0.70            # autocorrelation at the dominant lag, and
REPEAT_LINE_FRAC = 0.70    # power concentrated in that one line: a REPEATED
                           #   wander (vibrato). An Ornstein-Uhlenbeck wander
                           #   reaches 0.44 on the first and 0.46 on the
                           #   second, measured; vibrato reaches 0.94 and 0.99
CENTS_PER_OCT = 1200.0

REFUSED, STABLE, PERIODIC, DRIFTING = "REFUSED", "STABLE", "PERIODIC", "DRIFTING"


class Refusal(Exception):
    """A precondition of the measurement is unmet. Carries the code so the
    caller can tell WHICH precondition, and never a number."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def cents(f: np.ndarray | float, f_ref: float) -> np.ndarray | float:
    return CENTS_PER_OCT * np.log2(np.asarray(f, dtype=np.float64) / f_ref)


# ---- estimator 1: same-direction zero crossings (tom_pitch_probe's method) --
def zc_series(x: np.ndarray, sr: int, f0: float) -> tuple[np.ndarray, np.ndarray]:
    """(t, f) from interpolated same-direction zero crossings of the
    band-passed fundamental, one estimate per full period.

    The band-pass is what makes this usable on a sawtooth: unfiltered, a saw's
    ramp crosses zero once per cycle but its PolyBLEP corner and any harmonic
    residual put extra crossings near the wrap. Inside [0.55, 1.6] x f0 the
    signal is the fundamental alone and there is exactly one rising crossing
    per period. Both directions are tracked and CONCATENATED after each is
    differenced within its own series, so a DC offset cannot leak in."""
    xb = bandpass(x, sr, f0, lo=BP_BAND[0], hi=BP_BAND[1])
    edge = EDGE_PERIODS * sr / f0
    lo_t, hi_t = edge, len(x) - edge
    ts, fs = [], []
    for rising in (True, False):
        t = crossings(xb, rising, sr, f0)
        t = t[(t >= lo_t) & (t <= hi_t)]
        if len(t) < 3:
            continue
        per = np.diff(t) / sr
        good = per > 0
        ts.append((t[:-1][good] + t[1:][good]) / 2.0 / sr)
        fs.append(1.0 / per[good])
    if not ts:
        raise Refusal("NO_TRACK", "the zero-crossing tracker produced no periods "
                                  "outside the band-pass's edge transients")
    t = np.concatenate(ts)
    f = np.concatenate(fs)
    o = np.argsort(t)
    return t[o], f[o]


# ---- estimator 2: heterodyne phase -----------------------------------------
def het_series(x: np.ndarray, sr: int, f0: float,
               periods: int = HET_PERIODS,
               out_rate: float = OUT_RATE) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(t, f, envelope) from the unwrapped phase of the signal heterodyned to
    baseband.

    The low-pass is `HET_CASCADE` boxcars of exactly `periods` NOMINAL
    periods, rounded to whole samples. A boxcar of one period has a null at
    every harmonic of f0 and at the negative-frequency image, so nothing but
    the fundamental's own slow phase survives -- no filter design, no phase
    response to correct, and the rejection is a property of the window length
    rather than of a coefficient set. It is CASCADED because the rounding to
    whole samples moves the nulls off the harmonics; see `HET_CASCADE`.

    Returns f in Hz on a regular grid and |z|, the fundamental's envelope,
    which is what separates beating from drift: beating modulates BOTH."""
    n = len(x)
    L = max(8, int(round(sr / f0)) * int(periods))
    if n < (2 + 2 * HET_CASCADE) * L:
        raise Refusal("TOO_FEW_PERIODS",
                      f"{n} samples is under {2 + 2 * HET_CASCADE} heterodyne "
                      f"windows of {L}")
    t = np.arange(n, dtype=np.float64) / sr
    z = np.asarray(x, dtype=np.float64) * np.exp(-2j * math.pi * f0 * t)
    k = np.ones(L) / L
    zf = z
    for _ in range(HET_CASCADE):
        zf = np.convolve(zf, k, mode="valid")          # centre moves by (L-1)/2
    tf = (np.arange(len(zf)) + HET_CASCADE * (L - 1) / 2.0) / sr
    hop = max(1, int(round(sr / out_rate)))
    zf, tf = zf[::hop], tf[::hop]
    if len(zf) < 8:
        raise Refusal("TOO_FEW_PERIODS", "fewer than 8 trajectory samples")
    ph = np.unwrap(np.angle(zf))
    dt = np.diff(tf)
    f = f0 + np.diff(ph) / (2.0 * math.pi * dt)
    tm = (tf[:-1] + tf[1:]) / 2.0
    env = (np.abs(zf)[:-1] + np.abs(zf)[1:]) / 2.0
    return tm, f, env


# ---- the report ------------------------------------------------------------
def _boxcar(y: np.ndarray, n: int) -> np.ndarray:
    n = max(1, int(n) | 1)
    if n >= len(y):
        return np.full(len(y), float(np.mean(y)))
    pad = n // 2
    yp = np.concatenate((np.full(pad, y[0]), y, np.full(pad, y[-1])))
    return np.convolve(yp, np.ones(n) / n, mode="valid")[:len(y)]


def _walk_exponent(d: np.ndarray, rate: float) -> float:
    """log-log slope of Var[d(t+T) - d(t)] against T.

    0 for a bounded process (the increment variance saturates at 2 Var[d]),
    1 for a random walk (it grows linearly), and it is measured over lags
    short enough that the fit is not reading the window's own length."""
    n = len(d)
    lags = [int(round(rate * s)) for s in (0.05, 0.1, 0.2, 0.4, 0.8)]
    lags = [L for L in lags if 1 <= L <= n // 4]
    if len(lags) < 3:
        return float("nan")
    xs, ys = [], []
    for L in lags:
        v = float(np.var(d[L:] - d[:-L]))
        if v <= 0:
            continue
        xs.append(math.log(L / rate))
        ys.append(math.log(v))
    if len(xs) < 3:
        return float("nan")
    return float(np.polyfit(xs, ys, 1)[0])


def _dominant_wobble(d: np.ndarray, rate: float) -> tuple[float, float]:
    """(frequency, line fraction) of the strongest line in the deviation's own
    spectrum, DC removed. The line fraction counts the strongest bin with its
    two neighbours, so a wander whose period does not divide the window is not
    split across bins and read as broadband."""
    y = d - np.mean(d)
    w = np.hanning(len(y))
    S = np.abs(np.fft.rfft(y * w)) ** 2
    fr = np.fft.rfftfreq(len(y), 1.0 / rate)
    if len(S) < 4:
        return float("nan"), float("nan")
    S[0] = 0.0
    tot = float(S.sum())
    if tot <= 0:
        return float("nan"), 0.0
    k = int(np.argmax(S))
    lo, hi = max(1, k - 1), min(len(S), k + 2)
    return float(fr[k]), float(S[lo:hi].sum() / tot)


def _acorr_time(d: np.ndarray, rate: float) -> float:
    """Seconds at which the deviation's normalised autocorrelation first falls
    below 1/e -- the wander's own correlation time, measured from the series
    rather than assumed. A series that never decays within the window (a
    fraction of one slow cycle) returns the window length, which is what makes
    `MIN_TAUS` refuse it."""
    y = np.asarray(d, dtype=np.float64)
    y = y - y.mean()
    n = len(y)
    if n < 8 or np.dot(y, y) <= 0:
        return float("nan")
    nf = 1 << int(math.ceil(math.log2(2 * n)))
    S = np.fft.rfft(y, nf)
    ac = np.fft.irfft(S * np.conj(S), nf)[:n]
    ac /= ac[0]
    below = np.nonzero(ac < 1.0 / math.e)[0]
    return float(below[0] / rate) if len(below) else float(n / rate)


def _repeat_r(d: np.ndarray, lag: int) -> float:
    """Normalised autocorrelation of the deviation at `lag`. A repeated wander
    (vibrato, or a beat that has gone round more than once) is high here; an
    Ornstein-Uhlenbeck wander with a comparable timescale is not, because its
    autocorrelation decays monotonically and never comes back up."""
    if lag < 1 or lag >= len(d) - 4:
        return float("nan")
    a, b = d[:-lag], d[lag:]
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / den) if den > 0 else float("nan")


def drift_report(x: np.ndarray, sr: int, f0_nominal: float, *,
                 sr_expected: int | None = None,
                 label: str = "", slow_s: float = SLOW_S) -> dict:
    """Measure the pitch trajectory of one sustained tone and classify it.

    `x` is a mono float signal holding ONE held tone at (nominally)
    `f0_nominal`. Level envelopes are fine -- an amplitude envelope does not
    move the zeros of a sinusoid -- but a note boundary is not: trim to the
    held part before calling.

    Returns a dict that always carries `verdict`; on REFUSED it carries
    `refusal` and NO magnitude, because a magnitude from an apparatus whose
    preconditions failed looks exactly like data."""
    out = {"label": label, "f0_nominal": float(f0_nominal),
           "n_samples": int(len(x)), "sr": int(sr)}
    try:
        if sr_expected is not None and int(sr) != int(sr_expected):
            raise Refusal("SR_MISMATCH", f"{sr} != declared {sr_expected}")
        x = np.asarray(x, dtype=np.float64)
        dur = len(x) / sr
        out["duration_s"] = dur
        if dur * f0_nominal < MIN_PERIODS:
            raise Refusal("TOO_FEW_PERIODS",
                          f"{dur * f0_nominal:.1f} periods < {MIN_PERIODS}")
        rms = float(np.sqrt(np.mean(x ** 2)))
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        out["rms"], out["peak"] = rms, peak
        if rms <= 0:
            raise Refusal("LOW_LEVEL", "the window is exactly silent")

        t_zc, f_zc = zc_series(x, sr, f0_nominal)
        if len(f_zc) < MIN_PERIODS:
            raise Refusal("TOO_FEW_PERIODS",
                          f"{len(f_zc)} clean periods < {MIN_PERIODS}")
        t_h, f_h, env = het_series(x, sr, f0_nominal)

        f_ref = float(np.median(f_h))
        out["f0_measured_hz"] = f_ref
        out["f0_offset_cents"] = float(cents(f_ref, f0_nominal))
        d_zc_mean = float(cents(float(np.median(f_zc)), f_ref))
        out["xcheck_cents"] = d_zc_mean
        if abs(d_zc_mean) > XCHECK_CENTS:
            raise Refusal("ESTIMATOR_DISAGREE",
                          f"zc - het = {d_zc_mean:.3f} cents > {XCHECK_CENTS}")

        # the per-file floor, from the zc estimator's OWN period-to-period
        # scatter -- measured here, never quoted as a constant.
        d_zc = np.asarray(cents(f_zc, f_ref), dtype=np.float64)
        resid = d_zc - _boxcar(d_zc, max(3, int(round(slow_s * f0_nominal))))
        per_period_sigma = float(np.std(resid))
        n_slow = max(1.0, slow_s * f0_nominal)
        floor = per_period_sigma / math.sqrt(n_slow)
        out["zc_period_sigma_cents"] = per_period_sigma
        out["floor_cents"] = floor

        rate = 1.0 / float(np.mean(np.diff(t_h)))
        d = np.asarray(cents(f_h, f_ref), dtype=np.float64)
        # `_boxcar` pads with the end values, which biases exactly one smoother
        # length at each end. Trim it rather than report it.
        nb = int(round(slow_s * rate))
        keep = slice(nb, len(d) - nb) if len(d) > 4 * nb + 8 else slice(None)
        slow = _boxcar(d, nb)[keep]
        env = env[keep]
        if len(slow) < 8:
            raise Refusal("TOO_FEW_PERIODS",
                          "the trajectory is shorter than the slow smoother")
        slow = slow - float(np.mean(slow))
        mag = float(np.std(slow))
        out.update(
            trajectory_rate_hz=rate,
            drift_rms_cents=mag,
            drift_p2p_cents=float(np.max(slow) - np.min(slow)),
            drift_rate_cents_per_s=float(np.mean(np.abs(np.diff(slow)) * rate)),
            walk_exponent=_walk_exponent(slow, rate),
        )
        wob_hz, line_frac = _dominant_wobble(slow, rate)
        out["wobble_hz"], out["wobble_line_fraction"] = wob_hz, line_frac

        # The fundamental's own envelope. An amplitude ENVELOPE (a decay, a
        # VCA) is a straight line in dB and is removed by a degree-1 fit; what
        # is left is RIPPLE, which one partial cannot produce and two always
        # do. The SAME detrend is applied to the deviation before the two are
        # correlated -- detrending one side and not the other destroys exactly
        # the correlation this test exists to find (it read 0.09 on a
        # statically-detuned pair whose true correlation is 1.00, and the probe
        # then called that pair DRIFTING).
        env_db = 20.0 * np.log10(np.maximum(env, 1e-30))
        env_db = env_db - float(np.mean(env_db))
        out["env_mod_db"] = float(np.max(env_db) - np.min(env_db))
        tt = np.linspace(-1.0, 1.0, len(env_db))
        ripple = env_db - np.polyval(np.polyfit(tt, env_db, 1), tt)
        out["env_ripple_db"] = float(np.max(ripple) - np.min(ripple))
        dev_dt = slow - np.polyval(np.polyfit(tt, slow, 1), tt)
        se = _boxcar(ripple, int(round(slow_s * rate)))
        sd = _boxcar(dev_dt, int(round(slow_s * rate)))
        if np.std(se) > 0 and np.std(sd) > 0:
            out["env_corr"] = float(np.corrcoef(sd, se)[0, 1])
        else:
            out["env_corr"] = float("nan")
        if out["env_ripple_db"] >= MULTI_PARTIAL_DB:
            raise Refusal(
                "MULTI_PARTIAL",
                f"the band's envelope ripples by {out['env_ripple_db']:.2f} dB "
                f"(>= {MULTI_PARTIAL_DB}); one oscillator's fundamental does "
                f"not do that, so this window holds a MIX and its pitch is not "
                f"any one oscillator's pitch. Render the oscillators apart.")

        if mag < SNR_SIGMA * floor:
            out["verdict"] = STABLE
            out["reason"] = (f"slow component {mag:.4f} cents is under "
                             f"{SNR_SIGMA} x the window's floor {floor:.4f}")
            return out

        # Something is moving. Decide what, and REFUSE if the window cannot
        # tell: a window shorter than MIN_TAUS correlation times of its own
        # wander cannot distinguish an aperiodic drift from the first part of a
        # slow periodic one, whatever estimator is used.
        if not math.isnan(wob_hz) and wob_hz > 0:
            out["wobble_cycles"] = dur * wob_hz
            out["repeat_r"] = _repeat_r(slow, int(round(rate / wob_hz)))
        else:
            out["wobble_cycles"] = float("nan")
            out["repeat_r"] = float("nan")
        tau = _acorr_time(slow, rate)
        out["acorr_time_s"] = tau
        out["taus_in_window"] = (dur / tau) if tau and tau > 0 else float("nan")

        # A POSITIVE identification comes first: if the wander tracks the
        # envelope, or repeats, we know what it is and the window length is
        # not the question.
        beating = (abs(out["env_corr"]) >= PERIODIC_R
                   and out["env_ripple_db"] >= AM_DEPTH_DB)
        repeating = (not math.isnan(out["repeat_r"])
                     and out["repeat_r"] >= REPEAT_R
                     and line_frac >= REPEAT_LINE_FRAC)
        if beating or repeating:
            out["verdict"] = PERIODIC
            out["reason"] = ("the wander tracks the envelope (beating)" if beating
                             else "the wander repeats at its own period (vibrato)")
            out["periodic_kind"] = "beating" if beating else "repeating"
            return out
        if math.isnan(tau) or dur < MIN_TAUS * tau:
            raise Refusal(
                "SHORT_FOR_WOBBLE",
                f"the wander's own correlation time is {tau:.2f} s and the "
                f"window is {dur:.2f} s ({dur / tau:.1f} < {MIN_TAUS} "
                f"correlation times): an aperiodic wander and the first part "
                f"of a slow periodic one are the same picture here")
        out["verdict"] = DRIFTING
        out["reason"] = (f"{mag:.3f} cents rms slow wander, "
                         f"{SNR_SIGMA} x floor {floor:.4f}, not repeating "
                         f"(r={out['repeat_r']:.2f}) and not envelope-tracked "
                         f"(r={out['env_corr']:.2f})")
        return out
    except Refusal as r:
        out["verdict"] = REFUSED
        out["refusal"] = r.code
        out["refusal_detail"] = r.detail
        for k in ("drift_rms_cents", "drift_p2p_cents", "drift_rate_cents_per_s"):
            out.pop(k, None)
        return out


# ---- a pair of notes of the SAME pitch, far apart in one render -------------
def across_note_drift(reports: list) -> dict:
    """Drift between two instances of the same note in one continuous render.

    This is the measurement a short note still supports: two takes of the same
    pitch seconds apart in the same render differ in f0 by exactly the drift
    accumulated between them, and the floor is each window's own f0 precision
    rather than its trajectory scatter. Refuses unless every input reported an
    f0 (a REFUSED window has none)."""
    ok = [r for r in reports if "f0_measured_hz" in r]
    if len(ok) < 2:
        return {"verdict": REFUSED, "refusal": "TOO_FEW_WINDOWS",
                "refusal_detail": f"{len(ok)} windows carried an f0"}
    fs = [r["f0_measured_hz"] for r in ok]
    ref = fs[0]
    dev = [float(cents(f, ref)) for f in fs]
    floor = max(r.get("floor_cents", 0.0) for r in ok)
    span = max(dev) - min(dev)
    return {"verdict": DRIFTING if span > SNR_SIGMA * floor else STABLE,
            "n_windows": len(ok), "cents_vs_first": dev,
            "span_cents": span, "floor_cents": floor,
            "labels": [r.get("label", "") for r in ok]}


# ---- generators used by the controls, and reusable as stimuli ---------------
def ou_cents(n: int, rate: float, *, rms_cents: float, tau_s: float,
             seed: int) -> np.ndarray:
    """A bounded (Ornstein-Uhlenbeck) wander in cents with a KNOWN rms and
    time constant: the ground truth for the positive control. Bounded rather
    than a random walk because the estimator is asked to report which it is,
    and an unbounded truth cannot have an rms."""
    rng = np.random.default_rng(seed)
    a = math.exp(-1.0 / (tau_s * rate))
    y = np.empty(n)
    v = 0.0
    sd = math.sqrt(1.0 - a * a)
    for i in range(n):
        v = a * v + sd * rng.standard_normal()
        y[i] = v
    y -= y.mean()
    s = y.std()
    return y * (rms_cents / s) if s > 0 else y


def fm_tone(sr: int, dur_s: float, f0: float, dev_cents: np.ndarray,
            *, harmonics: int = 1, amp: float = 0.5) -> np.ndarray:
    """A tone at f0 whose instantaneous frequency follows `dev_cents`
    (resampled to the audio rate), summed over `harmonics` harmonics at 1/k.
    The phase is the exact integral of the frequency, so the stimulus's own
    trajectory is known to machine precision and nothing about the estimator
    is assumed in constructing it."""
    n = int(dur_s * sr)
    t = np.arange(n) / sr
    dev = np.interp(t, np.linspace(0, dur_s, len(dev_cents)), dev_cents)
    f = f0 * 2.0 ** (dev / CENTS_PER_OCT)
    ph = 2 * math.pi * np.cumsum(f) / sr
    y = np.zeros(n)
    for k in range(1, harmonics + 1):
        y += np.sin(k * ph) / k
    return amp * y


def static_detune_mix(sr: int, dur_s: float, f0: float,
                      detune_cents=(0.0, 7.0, -4.0),
                      gains=(1.0, 0.8, 0.6), *, harmonics: int = 1) -> np.ndarray:
    """THE NEGATIVE CONTROL of issue #138: several PERFECTLY STABLE
    oscillators at FIXED offsets. Nothing in this signal drifts -- every
    partial's frequency is a constant -- yet the mix beats, its envelope moves
    and its zero crossings are pushed around at the beat rate. An estimator
    that calls this DRIFTING is measuring beating."""
    n = int(dur_s * sr)
    t = np.arange(n) / sr
    y = np.zeros(n)
    for dt, g in zip(detune_cents, gains):
        fk = f0 * 2.0 ** (dt / CENTS_PER_OCT)
        for k in range(1, harmonics + 1):
            y += g * np.sin(2 * math.pi * k * fk * t) / k
    return 0.4 * y / max(gains)


# ---- CLI -------------------------------------------------------------------
def _window(x: np.ndarray, sr: int, t0: float, t1: float) -> np.ndarray:
    return x[max(0, int(t0 * sr)):min(len(x), int(t1 * sr))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wav", action="append", default=[],
                    help="WAV to measure; repeatable")
    ap.add_argument("--f0", type=float, required=False,
                    help="nominal f0 of the held tone, Hz")
    ap.add_argument("--window", action="append", default=[],
                    help="t0:t1 seconds; repeatable, applied to every --wav")
    ap.add_argument("--sr-expected", type=int, default=None)
    ap.add_argument("--json", default=None, help="write the report here")
    a = ap.parse_args(argv)
    if not a.wav:
        ap.error("--wav is required (see model/test_osc_drift_probe.py for the "
                 "synthetic controls)")
    if a.f0 is None:
        ap.error("--f0 is required: the probe does not guess a nominal pitch")
    wins = []
    for w in a.window or ["0:1e9"]:
        t0, t1 = w.split(":")
        wins.append((float(t0), float(t1)))
    reports = []
    for path in a.wav:
        sr, x = read_wav(path)
        for (t0, t1) in wins:
            seg = _window(x, sr, t0, t1)
            r = drift_report(seg, sr, a.f0, sr_expected=a.sr_expected,
                             label=f"{os.path.basename(path)}@{t0}:{t1}")
            reports.append(r)
            print(f"{r['label']:<52} {r['verdict']:<9} "
                  + (f"{r.get('refusal', '')} {r.get('refusal_detail', '')}"
                     if r["verdict"] == REFUSED
                     else f"rms {r['drift_rms_cents']:.4f} c  "
                          f"p2p {r['drift_p2p_cents']:.4f} c  "
                          f"floor {r['floor_cents']:.4f} c  "
                          f"walk {r['walk_exponent']:.2f}"))
    out = {"reports": reports, "across_note": across_note_drift(reports)}
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        print(f"-- wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

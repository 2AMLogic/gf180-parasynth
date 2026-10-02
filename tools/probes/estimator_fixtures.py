#!/usr/bin/env python3
"""Synthetic fixtures whose answer is known BEFORE any estimator runs (#158).

    python3 tools/probes/estimator_fixtures.py          # catalogue + axis coverage

This module is the fixture half of the ground-truth suite; the checking half
is `tools/probes/estimator_ground_truth.py`. It produces the seven signal
types #158 names, each SWEPT over frequency, duration, phase, SNR and level
rather than exercised at one convenient parameter set:

    damped_sine           a damped sine at known f and tau
    two_modes             two damped modes, spacing and relative phase set
                          independently, so a beating pair is reachable
    transient_then_ring   a pitch transient of known law, then a steady ring
    envelope_plus_noise   a known envelope over a known noise floor
    repeated_hits         several hits at unequal levels and known times
    harmonic_mixture      a stationary waveform with known partial amplitudes
    filtered_noise        white noise through a filter whose response is known
                          in closed form, plus that filter's impulse response

WHY THE GROUND TRUTH IS INDEPENDENT OF THE THING BEING MEASURED
---------------------------------------------------------------
`CLAUDE.md`: "An estimator calibrated on our own model is not validated." So
nothing in this module imports `model/audio_measure.py`. Every `truth` entry
is either a synthesis parameter (the f, tau, level, duty the signal was BUILT
from) or a closed form derived from those parameters -- the RMS of a damped
sine, the power-weighted centroid of a known partial set, the -3 dB corner of
a known biquad from `scipy.signal.freqz`. An estimator's own reading is never
the reference for another estimator's check.

ONE-FACTOR-AT-A-TIME, AND WHY THAT IS THE RIGHT SWEEP HERE
----------------------------------------------------------
Each family has a NOMINAL parameter set and then varies one axis at a time
around it. A full grid of five axes is thousands of records and this suite has
to fit inside `make verify`; the question each axis answers ("does the reading
survive this axis moving at all") is answered by the OFAT sweep, and the
interactions that matter -- differential decay against spacing, envelope
against noise floor -- are built into the families' own parameters rather than
left to a grid.

`axis_coverage()` is the guard on that: it refuses a family whose axis took
fewer than three distinct values or spanned less than the stated minimum, so
"swept" cannot quietly degrade to "two values a per cent apart". An axis that
does not apply to a family must appear in `AXIS_NOT_APPLICABLE` with a reason,
which is the same REFUSE-rather-than-omit rule the estimators themselves
follow.

WHAT DEFEATS THAT GUARD, said out loud (docs/verification-rules.md rule 8):
`axis_coverage` checks the SPREAD of the axis values a family declares, not
that the family's signal actually responds to them. A family that recorded
`level=0.001` in `axes` and then synthesised at 1.0 anyway would pass. What
catches that is `test_estimator_fixtures.py::test_declared_axes_move_the_signal`,
which measures the record for each axis value and requires it to differ.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import signal

SR = 48000

#: The five axes #158 requires every fixture family to be swept across.
AXES = ("frequency", "duration", "phase", "snr_db", "level")

#: Minimum spread an axis must show inside one family for `axis_coverage` to
#: accept it as swept. `kind` picks how the spread is measured: "ratio" is
#: max/min of the values, "span" is max-minus-min. `snr_db` counts `inf` (a
#: clean record) as a distinct value but measures its span over the finite ones.
AXIS_MIN_SPREAD = {
    "frequency": ("ratio", 4.0),
    "duration": ("ratio", 4.0),
    "phase": ("span", math.pi),
    "snr_db": ("span", 20.0),
    "level": ("ratio", 10.0),
}
AXIS_MIN_DISTINCT = 3


# ---------------------------------------------------------------------------
# Synthesis primitives. THE single home for them in this package.
#
# #517 acceptance criterion 6 asks that `estimator_domains.py` and
# `verify_109_claims.py` be folded in or left standalone with a reason, and
# not duplicated. What was done, and why it is both:
#
#   * Their SYNTHESIS is folded in. Both files held `_damped` / `_sine` /
#     `_two_tone` that were the same functions as these, down to the phases
#     0.3 and 1.9 their own numbers were measured at; both now bind those
#     names to the functions below, so a change to the synthesis cannot move
#     their numbers and this catalogue's independently. `verify_109_claims`
#     passes `lead_ms=0`, because its numbers were taken without the
#     pre-onset lead `two_tone` adds by default.
#   * Their MEASUREMENTS stay standalone, with the reason: neither is a
#     per-estimator gate. `estimator_domains.py` measures the declared DOMAIN
#     BOUNDS `ValidatedDomain` quotes (#115) and prints the absence of the
#     TR-808 corpus as a result; `verify_109_claims.py` re-measures two
#     withdrawn numeric claims from #109 against reference WAVs. Folding
#     either into `estimator_ground_truth.py` would put a corpus dependency
#     inside a gate that must run with no corpus at all.
#
# Both still exit non-zero when a bound they measure stops holding, and both
# are wired into `make verify` alongside this suite -- which they were not
# before #517, and which is why a change here cannot break them unnoticed.
# ---------------------------------------------------------------------------
def damped(f, tau, amp, n, sr=SR, phase=0.0):
    """`amp * exp(-t/tau) * sin(2 pi f t + phase)`, `n` samples at `sr`."""
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def sine(f, amp, n, sr=SR, phase=0.0):
    """A stationary sine, `n` samples at `sr`."""
    t = np.arange(n) / sr
    return amp * np.sin(2 * math.pi * f * t + phase)


def two_tone(f1, f2, tau1, tau2, a1, a2, seconds, sr=SR, lead_ms=10.0,
             phase1=0.3, phase2=1.9):
    """Two damped partials, with a TRUE PRE-ONSET LEAD by default.

    `band_energy`'s own docstring states the precondition: `sosfiltfilt` pads
    by 27 samples with an odd extension through the first sample, so a segment
    that begins at full amplitude manufactures an edge worth up to 10 dB in a
    sparsely-occupied band. A synthetic struck signal starts at full amplitude
    by construction, so measuring a band ratio on one without a lead measures
    that edge and calls it the estimator. `run_case.prepare` guarantees the
    lead on real records; this guarantees it on synthetic ones."""
    n = int(seconds * sr)
    x = (damped(f1, tau1, a1, n, sr, phase1) + damped(f2, tau2, a2, n, sr, phase2))
    if lead_ms <= 0:
        return x
    return np.concatenate([np.zeros(int(lead_ms * 1e-3 * sr)), x])


def harmonic_series(f0, n, k, sr=SR, amps=None, phases=None):
    """Sum of `k` harmonics of `f0` with amplitudes `amps` (default 1/i, the
    sawtooth series) and phases `phases` (default all zero)."""
    t = np.arange(n) / sr
    out = np.zeros(n)
    for i in range(1, k + 1):
        a = (1.0 / i) if amps is None else amps[i - 1]
        p = 0.0 if phases is None else phases[i - 1]
        out += a * np.sin(2 * math.pi * i * f0 * t + p)
    return out


def add_noise(x, snr_db, seed, *, over=None):
    """`x` plus white Gaussian noise at `snr_db` below the RMS of `x[over]`.

    `over` selects the region whose RMS defines the SNR -- the SOUNDING region,
    not the whole record: a record that is nine tenths lead and trail would
    otherwise get noise nine tenths too quiet. Returns (y, noise_rms)."""
    if not np.isfinite(snr_db):
        return x, 0.0
    ref = x if over is None else x[over]
    r = math.sqrt(float(np.mean(np.asarray(ref, float) ** 2)))
    amp = r * 10 ** (-snr_db / 20.0)
    rng = np.random.default_rng(seed)
    return x + amp * rng.standard_normal(len(x)), amp


# ---------------------------------------------------------------------------
# The fixture record
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Fixture:
    """One synthetic record and everything known about it before measurement.

    `truth` holds synthesis parameters and closed forms derived from them.
    `axes` holds this record's value on each of the five swept axes, with
    `None` for an axis `AXIS_NOT_APPLICABLE` declares a reason for."""
    kind: str
    label: str
    x: np.ndarray
    sr: int
    truth: dict
    axes: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.x)


#: An axis a family cannot express, with the reason, and what is swept in its
#: place. Omitting an axis WITHOUT an entry here is a failure of
#: `axis_coverage`, not a silent pass.
AXIS_NOT_APPLICABLE = {
    ("filtered_noise", "phase"): (
        "white noise has no phase parameter: there is no phase to set. The "
        "analogous nuisance axis -- which realisation of the noise you drew -- "
        "is swept instead, as `truth['seed']`, over five seeds."),
    ("repeated_hits", "phase"): (
        "the carrier phase of an individual hit is swept by the `damped_sine` "
        "family; what this family is for is the TIMING and LEVEL pattern of "
        "several hits, so the phase-like axis swept here is the inter-onset "
        "spacing (`truth['gaps_s']`), regular and irregular."),
}


# ===========================================================================
# 1. damped sine at known f and tau
# ===========================================================================
#: lead and trail silence around every struck fixture. The lead is
#: `two_tone`'s precondition (above); the trail is what makes
#: `sounding_extent` / `strip_trailing_silence` checkable at all (#139).
LEAD_S = 0.010
TRAIL_S = 0.020


def _struck(core, sr, *, trail_s=TRAIL_S, lead_s=LEAD_S):
    """`core` with a lead and a trail of digital silence. Returns (x, i0, i1)."""
    lead = np.zeros(int(lead_s * sr))
    trail = np.zeros(int(trail_s * sr))
    return (np.concatenate([lead, core, trail]), len(lead), len(lead) + len(core))


def damped_peak(f, tau, amp, phase):
    """The largest |value| of `amp exp(-t/tau) sin(w t + phase)` over t >= 0.

    TWO candidates, and the first draft had only one. The interior extrema
    solve tan(w t + phase) = w tau, and the earliest positive one dominates
    all later ones because the envelope falls; but the record also has a
    BOUNDARY at t = 0, where the value is amp sin(phase). At phase 1.9 the
    boundary wins (0.9463 against 0.9352) and a truth that ignored it declared
    0.005 % of a clean record "clipped" at 1.01x its own peak. Returns
    (peak, where) with `where` naming which candidate won."""
    w = 2 * math.pi * f
    k = 0
    while True:
        tstar = (math.atan(w * tau) + k * math.pi - phase) / w
        if tstar >= 0:
            break
        k += 1
    interior = amp * math.exp(-tstar / tau) * (w * tau) / math.sqrt(1 + (w * tau) ** 2)
    boundary = abs(amp * math.sin(phase))
    return ((interior, "interior extremum") if interior >= boundary
            else (boundary, "t=0 boundary"))


def damped_mean_square(f, tau, amp, phase, dur):
    """EXACT mean square of `amp exp(-t/tau) sin(w t + phase)` over [0, D].

    The lazy form drops the oscillation term -- mean(sin^2) = 1/2 -- and is
    4.1 % wrong at 0.66 carrier cycles per tau, which is inside this
    catalogue's own frequency sweep. The second integral below is the term it
    drops, and it also has a closed form:

        int_0^D e^{-a t} cos(b t + c) dt
            = [e^{-a t} (b sin(b t + c) - a cos(b t + c))] / (a^2 + b^2)

    with a = 2/tau, b = 2 w, c = 2 phase."""
    w = 2 * math.pi * f
    a, b, c = 2.0 / tau, 2.0 * w, 2.0 * phase

    def F(t):
        return (math.exp(-a * t) * (b * math.sin(b * t + c)
                                    - a * math.cos(b * t + c))) / (a * a + b * b)

    i1 = tau / 2.0 * (1.0 - math.exp(-2 * dur / tau))        # int e^{-2t/tau}
    i2 = F(dur) - F(0.0)                                     # the osc term
    return amp * amp * 0.5 * (i1 - i2) / dur


def _damped_sine_truth(f, tau, amp, phase, dur, sr, i0, i1, n, snr_db, noise_rms):
    """Closed forms for a damped sine, all from the synthesis parameters.

    rms         `damped_mean_square` over the sounding region, scaled by
                n_sound / n_total for the whole record, with the noise power
                added in quadrature.
    peak        `damped_peak` -- NOT `amp`, which a decaying sine reaches only
                at phase pi/2.
    t20         ln(10) tau, the time to fall 20 dB.
    max_step    the largest sample-to-sample jump. For a STRUCK fixture this
                is normally the ONSET itself: the record steps from the lead's
                last zero to amp sin(phase) in one sample, which is 0.2955 for
                the nominal and ten times the 2 amp sin(pi f / sr) a
                stationary sine of the same frequency can manage. That is the
                click `max_sample_step` exists to find, and it has a closed
                form, so it is the gate; when the in-carrier bound is the
                larger of the two there is no exact answer and the check falls
                back to the bound.
    proj_amp    a coherent projection of the record at f reads the window
                average of the envelope: amp * tau / D_total * (1 - exp(-D_s/tau)).
    """
    ms = damped_mean_square(f, tau, amp, phase, dur)
    rms_sig = math.sqrt(ms * (i1 - i0) / n)
    peak, where = damped_peak(f, tau, amp, phase)
    onset_step = abs(amp * math.sin(phase))
    carrier_step = 2.0 * amp * math.sin(math.pi * f / sr)
    # how far into the record the envelope stays above 1e-9 of its own peak,
    # which is what `sounding_extent`'s default floor asks (#139)
    reach = i0 + int(math.ceil(tau * math.log(peak / (1e-9 * peak)) * sr))
    return dict(
        f=f, tau=tau, amp=amp, phase=phase, dur=dur, sr=sr, i0=i0, i1=i1,
        snr_db=snr_db, noise_rms=noise_rms,
        rms=math.sqrt(rms_sig ** 2 + noise_rms ** 2), rms_signal=rms_sig,
        peak=peak, peak_at=where, t20=math.log(10.0) * tau,
        proj_amp=amp * tau * sr / n * (1.0 - math.exp(-dur / tau)),
        sounding_extent=min(i1, reach),
        max_step=max(onset_step, carrier_step),
        max_step_exact=onset_step >= carrier_step,
        onsets=[i0], n_modes=1, taus=[tau], stationary=False,
    )


def _damped_sine(f, tau, amp, phase, dur, snr_db, seed, sr=SR):
    core = damped(f, tau, amp, int(dur * sr), sr, phase)
    x, i0, i1 = _struck(core, sr)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    t = _damped_sine_truth(f, tau, amp, phase, dur, sr, i0, i1, len(x), snr_db, nrms)
    t["seed"] = seed
    lab = (f"f={f:g}Hz tau={tau*1e3:g}ms dur={dur:g}s phase={phase:g} "
           f"level={amp:g} snr={snr_db:g}dB")
    fx = Fixture("damped_sine", lab, y, sr, t,
                 dict(frequency=f, duration=dur, phase=phase, snr_db=snr_db,
                      level=amp))
    return fx


#: nominal: a 220 Hz / 30 ms ring, the middle of the 808 tom and rimshot range
_DS_NOM = dict(f=220.0, tau=0.030, amp=1.0, phase=0.3, dur=0.40, snr_db=float("inf"))


def damped_sine_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("f", (56.0, 130.0, 220.0, 540.0, 3450.0, 7100.0)),
              ("tau", (0.003, 0.010, 0.030, 0.100, 0.300)),
              ("dur", (0.05, 0.15, 0.40, 1.00)),
              ("phase", (0.0, 0.9, 1.9, 3.3, 5.1)),
              ("snr_db", (float("inf"), 60.0, 40.0, 20.0)),
              ("amp", (1.0, 0.1, 0.01, 0.001))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_DS_NOM, **{axis: v})
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            # the 3 ms ring needs a short record to hold any decay at all and
            # the 300 ms one needs a long one: duration follows tau where the
            # nominal 0.40 s would make the sweep meaningless rather than hard
            if axis == "tau":
                p["dur"] = max(0.05, min(1.2, 12.0 * v))
            out.append(_damped_sine(p["f"], p["tau"], p["amp"], p["phase"],
                                    p["dur"], p["snr_db"], 1000 + 17 * i + j, sr))
    return out


# ===========================================================================
# 2. two damped modes, spacing and relative phase set independently
# ===========================================================================
def _two_modes(f1, spacing, tau1, tau2, a1, a2, phase2, dur, snr_db, seed, sr=SR):
    f2 = f1 + spacing
    n = int(dur * sr)
    core = damped(f1, tau1, a1, n, sr, 0.3) + damped(f2, tau2, a2, n, sr, phase2)
    x, i0, i1 = _struck(core, sr)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    #: Energy-weighted mean tau, which is what a single-exponential fit of a
    #: two-mode envelope tends towards. It is recorded so the suite can say
    #: that a confident reading landed there rather than on either component --
    #: it is NOT an acceptable answer, it is the diagnosis of a wrong one.
    e1, e2 = a1 * a1 * tau1, a2 * a2 * tau2
    taus = sorted({round(tau1, 12), round(tau2, 12)})
    t = dict(f1=f1, f2=f2, spacing=spacing, tau1=tau1, tau2=tau2, a1=a1, a2=a2,
             phase2=phase2, dur=dur, sr=sr, i0=i0, i1=i1, snr_db=snr_db,
             noise_rms=nrms, seed=seed, beat_hz=abs(spacing),
             taus=taus, n_modes=2, stationary=False, onsets=[i0],
             tau_energy_mean=(e1 * tau1 + e2 * tau2) / (e1 + e2),
             ambiguous_decay=len(taus) > 1,
             #: which component dominates the record's energy, so a reading
             #: that matches one component can be named rather than just
             #: accepted
             dominant_tau=tau1 if e1 >= e2 else tau2,
             dominant_f=f1 if e1 >= e2 else f2)
    lab = (f"f1={f1:g} spacing={spacing:g}Hz tau={tau1*1e3:g}/{tau2*1e3:g}ms "
           f"a={a1:g}/{a2:g} dphi={phase2:.2f} dur={dur:g}s snr={snr_db:g}dB")
    return Fixture("two_modes", lab, y, sr, t,
                   dict(frequency=f1, duration=dur, phase=phase2,
                        snr_db=snr_db, level=a1))


_TM_NOM = dict(f1=220.0, spacing=110.0, tau1=0.100, tau2=0.100, a1=1.0, a2=0.7,
               phase2=0.3, dur=0.50, snr_db=float("inf"))


def two_mode_fixtures(sr=SR) -> list[Fixture]:
    """Matched-decay pairs FIRST, then the differential-decay pairs that make
    the record's decay ambiguous. The matched ones are where an estimator must
    still answer; the mismatched ones are where it must name a component or
    refuse (#517 acceptance criterion 4)."""
    out, seen = [], set()
    sweeps = [("f1", (90.0, 220.0, 540.0, 1760.0)),
              ("spacing", (3.0, 13.0, 30.0, 110.0, 440.0, 1540.0)),
              ("dur", (0.12, 0.25, 0.50, 1.20)),
              ("phase2", (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)),
              ("snr_db", (float("inf"), 60.0, 40.0, 20.0)),
              ("a1", (1.0, 0.1, 0.01, 0.001)),
              # the ambiguity axis: two decay constants in one record
              ("tau2", (0.100, 0.050, 0.020, 0.300))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_TM_NOM, **{axis: v})
            if axis == "a1":
                p["a2"] = 0.7 * v
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_two_modes(p["f1"], p["spacing"], p["tau1"], p["tau2"],
                                  p["a1"], p["a2"], p["phase2"], p["dur"],
                                  p["snr_db"], 2000 + 17 * i + j, sr))
    # Two close modes with very different taus at matched level: the beating
    # pair that made `damped_sinusoid` report a tau matching neither component
    # before #517. Kept explicitly so the case cannot leave the catalogue by a
    # sweep edit.
    for spacing, (t1, t2) in ((13.0, (0.200, 0.050)), (13.0, (0.100, 0.020)),
                              (7.0, (0.300, 0.030)), (30.0, (0.050, 0.200))):
        out.append(_two_modes(220.0, spacing, t1, t2, 1.0, 1.0, 1.1, 0.80,
                              float("inf"), 2900 + int(spacing), sr))
    return out


# ===========================================================================
# 3. a pitch transient, then a steady ring
# ===========================================================================
def _transient_ring(f_end, depth, law, glide_s, tau_fast, tau_ring, dur,
                    phase, amp, snr_db, seed, sr=SR):
    """A glide of KNOWN law into a steady ring, with a KNOWN two-stage
    envelope: a fast attack decay for `glide_s`, then the ring's own tau.

    The phase is the exact integral of the instantaneous-frequency law, so the
    record's instantaneous frequency IS `inst` by construction rather than by
    approximation. `law` is the name `glide_law` must return:

      'constant-time'  log2 f approaches the target exponentially
      'constant-rate'  log2 f is linear in time (a fixed cents per second)
    """
    n = int(dur * sr)
    t = np.arange(n) / sr
    f0 = f_end * (1.0 + depth)
    if law == "constant-time":
        # log2 f = log2 f_end + (log2 f0 - log2 f_end) exp(-t/glide_s)
        lf = np.log2(f_end) + (np.log2(f0) - np.log2(f_end)) * np.exp(-t / glide_s)
    elif law == "constant-rate":
        frac = np.minimum(t / glide_s, 1.0)
        lf = np.log2(f0) + (np.log2(f_end) - np.log2(f0)) * frac
    else:                                                    # pragma: no cover
        raise ValueError(law)
    inst = np.power(2.0, lf)
    phi = phase + 2 * math.pi * np.cumsum(inst) / sr
    env = amp * (0.6 * np.exp(-t / tau_fast) + 0.4 * np.exp(-t / tau_ring))
    core = env * np.sin(phi)
    x, i0, i1 = _struck(core, sr)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    settle_s = glide_s * (4.0 if law == "constant-time" else 1.0)
    t_truth = dict(f_end=f_end, f_start=f0, depth=depth, law=law,
                   glide_s=glide_s, tau_fast=tau_fast, tau_ring=tau_ring,
                   dur=dur, amp=amp, phase=phase, sr=sr, i0=i0, i1=i1,
                   snr_db=snr_db, noise_rms=nrms, seed=seed,
                   inst_hz=inst, settle_s=settle_s,
                   #: the ring interval: past the transient, before the floor
                   ring_from_s=i0 / sr + settle_s,
                   ring_to_s=i1 / sr,
                   taus=sorted({round(tau_fast, 12), round(tau_ring, 12)}),
                   n_modes=2, stationary=False, onsets=[i0],
                   ambiguous_decay=tau_fast != tau_ring,
                   octaves=abs(math.log2(f0 / f_end)))
    lab = (f"{law} f {f0:.0f}->{f_end:g}Hz glide={glide_s*1e3:g}ms "
           f"tau={tau_fast*1e3:g}/{tau_ring*1e3:g}ms dur={dur:g}s "
           f"level={amp:g} snr={snr_db:g}dB")
    return Fixture("transient_then_ring", lab, y, sr, t_truth,
                   dict(frequency=f_end, duration=dur, phase=phase,
                        snr_db=snr_db, level=amp))


_TR_NOM = dict(f_end=120.0, depth=0.35, law="constant-time", glide_s=0.030,
               tau_fast=0.008, tau_ring=0.200, dur=0.60, phase=0.4, amp=1.0,
               snr_db=float("inf"))


def transient_ring_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("f_end", (60.0, 120.0, 330.0, 900.0)),
              ("dur", (0.20, 0.35, 0.60, 1.20)),
              ("phase", (0.0, 1.2, 2.6, 4.4)),
              ("snr_db", (float("inf"), 60.0, 40.0, 20.0)),
              ("amp", (1.0, 0.1, 0.01, 0.001)),
              ("depth", (0.12, 0.35, 0.8)),
              ("glide_s", (0.010, 0.030, 0.080)),
              ("law", ("constant-time", "constant-rate"))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_TR_NOM, **{axis: v})
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_transient_ring(p["f_end"], p["depth"], p["law"],
                                       p["glide_s"], p["tau_fast"],
                                       p["tau_ring"], p["dur"], p["phase"],
                                       p["amp"], p["snr_db"],
                                       3000 + 17 * i + j, sr))
    return out


# ===========================================================================
# 4. a known envelope over a known noise floor
# ===========================================================================
def _envelope_noise(f, shape, tau, dur, phase, amp, snr_db, seed, sr=SR,
                    step_ms=0.0, step_frac=0.0):
    """A carrier at `f` under an envelope of KNOWN shape over a KNOWN floor.

    `shape` is 'exp' (exp(-t/tau)), 'linear' (a straight ramp down) or
    'charge' (1 - exp(-4t/T), the RC charge whose `segment_shape` index is the
    closed-form +0.3808). `step_ms` > 0 quantises the envelope into a
    STAIRCASE of relative step `step_frac`, whose `envelope_ripple_db` has the
    closed form 20 log10(step / sqrt(12)) relative to the envelope mean."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    if shape == "exp":
        env = np.exp(-t / tau)
    elif shape == "linear":
        # down to 0.25, NOT to zero: a quantised ramp that reaches zero ends
        # in a long run of identical zeros, which becomes the record's longest
        # plateau and its sounding extent -- so the staircase members would
        # ground-truth the tail rather than the step.
        env = 1.0 - 0.75 * np.minimum(t / dur, 1.0)
    elif shape == "charge":
        env = 1.0 - np.exp(-4.0 * t / dur)
    else:                                                    # pragma: no cover
        raise ValueError(shape)
    held = int(round(step_ms * 1e-3 * sr))
    if held > 1:
        # hold the envelope for `held` samples at a time, quantised to
        # `step_frac` of full scale: a staircase with a known step
        idx = (np.arange(n) // held) * held
        env = np.round(env[idx] / step_frac) * step_frac
    env = amp * env
    core = env * np.sin(2 * math.pi * f * t + phase)
    x, i0, i1 = _struck(core, sr)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    #: when the envelope crosses the noise floor. Past this instant the record
    #: holds no information about the envelope, and an estimator that reports a
    #: decay read from past it is reporting the floor.
    sig_rms = amp / math.sqrt(2.0)
    t_floor = (tau * math.log(sig_rms / nrms) if (shape == "exp" and nrms > 0
                                                 and sig_rms > nrms) else float("inf"))
    t_truth = dict(f=f, shape=shape, tau=tau, dur=dur, amp=amp, phase=phase,
                   sr=sr, i0=i0, i1=i1, snr_db=snr_db, noise_rms=nrms,
                   seed=seed, env=env, t_floor_s=t_floor,
                   step_ms=step_ms, step_frac=step_frac,
                   #: closed form from `envelope_ripple_db`'s own docstring
                   ripple_db=(20.0 * math.log10(step_frac
                                                / (math.sqrt(12.0) * float(np.mean(env / amp))))
                              if held > 1 else None),
                   longest_plateau=(held if held > 1 else None),
                   #: `segment_shape`'s closed-form landmarks
                   shape_index={"exp": 0.3808, "linear": 0.0,
                                "charge": 0.3808}[shape],
                   taus=[tau] if shape == "exp" else [],
                   n_modes=1, stationary=False, onsets=[i0],
                   ambiguous_decay=False,
                   t20=math.log(10.0) * tau if shape == "exp" else None)
    lab = (f"{shape} env f={f:g}Hz tau={tau*1e3:g}ms dur={dur:g}s level={amp:g} "
           f"snr={snr_db:g}dB" + (f" step={step_frac:g}@{step_ms:g}ms" if held > 1 else ""))
    return Fixture("envelope_plus_noise", lab, y, sr, t_truth,
                   dict(frequency=f, duration=dur, phase=phase, snr_db=snr_db,
                        level=amp))


_EN_NOM = dict(f=440.0, shape="exp", tau=0.120, dur=0.70, phase=0.2, amp=1.0,
               snr_db=50.0)


def envelope_noise_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("f", (110.0, 440.0, 1200.0, 5000.0)),
              ("dur", (0.18, 0.35, 0.70, 1.40)),
              ("phase", (0.0, 1.1, 2.4, 4.7)),
              ("snr_db", (80.0, 60.0, 40.0, 25.0)),
              ("amp", (1.0, 0.1, 0.01, 0.001)),
              ("tau", (0.030, 0.120, 0.400)),
              ("shape", ("exp", "linear", "charge"))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_EN_NOM, **{axis: v})
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_envelope_noise(p["f"], p["shape"], p["tau"], p["dur"],
                                       p["phase"], p["amp"], p["snr_db"],
                                       4000 + 17 * i + j, sr))
    # The staircase members, whose ripple has a closed form. A smooth control
    # and two step sizes, so the measure is checked for ORDER as well as value.
    for step in (0.02, 0.05):
        out.append(_envelope_noise(440.0, "linear", 0.120, 0.70, 0.2, 1.0,
                                   float("inf"), 4900, sr,
                                   step_ms=2.0, step_frac=step))
    return out


# ===========================================================================
# 5. repeated hits at unequal levels
# ===========================================================================
def _repeated_hits(f, tau, levels, gaps_s, dur, phase, snr_db, seed, sr=SR):
    n = int(dur * sr)
    core = np.zeros(n)
    starts, t_now = [], 0.0
    for i, lvl in enumerate(levels):
        k = int(round(t_now * sr))
        if k >= n:
            break
        seg = damped(f, tau, lvl, n - k, sr, phase)
        core[k:] += seg
        starts.append(k)
        t_now += gaps_s[i % len(gaps_s)]
    x, i0, i1 = _struck(core, sr)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    regular = len(set(round(g, 9) for g in gaps_s)) == 1
    t_truth = dict(f=f, tau=tau, levels=list(levels), gaps_s=list(gaps_s),
                   dur=dur, phase=phase, sr=sr, i0=i0, i1=i1, snr_db=snr_db,
                   noise_rms=nrms, seed=seed,
                   onsets=[i0 + k for k in starts],
                   n_hits=len(starts),
                   period_s=gaps_s[0] if regular else None,
                   regular=regular,
                   peak=damped_peak(f, tau, max(levels), phase)[0],
                   taus=[tau], n_modes=len(starts), stationary=False,
                   sounding_extent=min(i1, (i0 + starts[-1] if starts else i0)
                                       + int(round(tau * math.log(
                                           levels[(len(starts) - 1)
                                                  % len(levels)]
                                           / (1e-9 * damped_peak(
                                               f, tau, max(levels), phase)[0]))
                                           * sr))),
                   #: several hits in one record = several decay intervals.
                   #: A whole-record decay number is ambiguous by construction.
                   ambiguous_decay=len(starts) > 1,
                   hit_windows_s=[((i0 + k) / sr, min(i1, i0 + k + int(round(
                       min(gaps_s) * 0.8 * sr))) / sr) for k in starts])
    lab = (f"{len(starts)} hits f={f:g}Hz tau={tau*1e3:g}ms "
           f"levels={'/'.join(f'{l:g}' for l in levels)} "
           f"gaps={'/'.join(f'{g*1e3:g}' for g in gaps_s)}ms dur={dur:g}s "
           f"snr={snr_db:g}dB")
    return Fixture("repeated_hits", lab, y, sr, t_truth,
                   dict(frequency=f, duration=dur, phase=None, snr_db=snr_db,
                        level=max(levels)))


_RH_NOM = dict(f=300.0, tau=0.020, levels=(1.0, 0.5, 0.25, 0.7),
               gaps_s=(0.120,), dur=0.70, phase=0.3, snr_db=float("inf"))


def repeated_hit_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("f", (120.0, 300.0, 900.0, 4000.0)),
              ("dur", (0.30, 0.50, 0.70, 1.40)),
              ("snr_db", (float("inf"), 60.0, 40.0, 25.0)),
              ("levels", ((1.0, 0.5, 0.25, 0.7), (1.0, 0.1, 0.05, 0.08),
                          (0.01, 0.005, 0.0025, 0.007),
                          (0.001, 0.0005, 0.00025, 0.0007))),
              # the phase-like axis for this family: the timing pattern
              ("gaps_s", ((0.120,), (0.060,), (0.200,), (0.070, 0.150, 0.095)))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_RH_NOM, **{axis: v})
            key = tuple(sorted((k, tuple(x) if isinstance(x, tuple) else x)
                               for k, x in p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_repeated_hits(p["f"], p["tau"], p["levels"],
                                      p["gaps_s"], p["dur"], p["phase"],
                                      p["snr_db"], 5000 + 17 * i + j, sr))
    return out


# ===========================================================================
# 6. a known harmonic mixture
# ===========================================================================
#: Naive (aliasing) shapes, whose TIME-DOMAIN properties have exact closed
#: forms -- a naive saw's `step_ratio` is N/2 for N samples per period, its
#: `rectangularity` is 0.5, a naive rectangle's is 1.0 -- and band-limited
#: shapes, whose SPECTRAL properties do (harmonic k of a duty-d rectangle,
#: the 1/k sawtooth series). Both are in the catalogue because neither alone
#: ground-truths both halves of the module.
def _naive(kind, f0, n, sr, duty=0.5, phase=0.0):
    ph = (np.arange(n) * f0 / sr + phase / (2 * math.pi)) % 1.0
    if kind == "saw":
        return 2.0 * ph - 1.0
    if kind == "rect":
        return np.where(ph < duty, 1.0, -1.0)
    if kind == "tri":
        return 4.0 * np.abs(ph - 0.5) - 1.0
    if kind == "sine":
        return np.sin(2 * math.pi * ph)
    raise ValueError(kind)                                   # pragma: no cover


def _bandlimited(kind, f0, n, sr, duty=0.5, phase=0.0):
    """The same shapes from their Fourier series, truncated below Nyquist, so
    the partial amplitudes are the ground truth rather than an approximation.

    The series are the textbook ones, and the PHASES matter as much as the
    amplitudes: the first draft of this function built the sawtooth from
    cosines (amplitudes 2/(pi k), phase -pi/2), which has the same partial
    MAGNITUDES as a saw and is not a saw -- it is continuous, so it has no
    jump per period, and `waveform_id` correctly declined to call it one
    ('UNQUALIFIED: a rectangle at a measured duty of 91.6 % would not give
    this spectrum'). A fixture whose label is wrong fails the estimator for
    being right, which is the worst failure mode a suite like this has.

        saw       -(2/pi) sum sin(2 pi k f t) / k
        rect(d)   (2d - 1) + sum (4/(pi k)) sin(pi k d)
                             sin(2 pi k f t - pi k d + pi/2)
        tri       (8/pi^2) sum_{k odd} (-1)^((k-1)/2) sin(2 pi k f t) / k^2
    """
    kmax = int(0.98 * (sr / 2) / f0)
    t = np.arange(n) / sr
    out = np.zeros(n)
    amps = {}
    for k in range(1, kmax + 1):
        if kind == "saw":
            a, ph = -2.0 / (math.pi * k), 0.0
        elif kind == "rect":
            a = 4.0 / (math.pi * k) * math.sin(math.pi * k * duty)
            ph = -math.pi * k * duty + math.pi / 2
            if abs(a) < 1e-12:
                amps[k] = 0.0
                continue
        elif kind == "tri":
            if k % 2 == 0:
                amps[k] = 0.0
                continue
            a, ph = 8.0 / (math.pi ** 2 * k * k) * (-1) ** ((k - 1) // 2), 0.0
        elif kind == "sine":
            if k > 1:
                break
            a, ph = 1.0, 0.0
        else:                                                # pragma: no cover
            raise ValueError(kind)
        amps[k] = abs(a)
        out += a * np.sin(2 * math.pi * k * f0 * t + ph + k * phase)
    if kind == "rect":
        out = out + (2.0 * duty - 1.0)
    return out, amps


def _harmonic_mixture(kind, band_limited, f0, duty, dur, phase, amp, snr_db,
                      seed, sr=SR, inharmonic_share_db=None):
    n = int(dur * sr)
    if band_limited:
        core, amps = _bandlimited(kind, f0, n, sr, duty, phase)
    else:
        core, amps = _naive(kind, f0, n, sr, duty, phase), None
    core = amp * core
    planted = planted_amp = None
    if inharmonic_share_db is not None:
        share = 10 ** (inharmonic_share_db / 10.0)
        planted_amp = math.sqrt(2 * share / (1 - share)
                                * float((core ** 2).sum()) / n)
        core = core + sine(1.5 * f0, planted_amp, n, sr, 0.7)
        planted = inharmonic_share_db
    x, i0, i1 = _struck(core, sr, trail_s=0.0, lead_s=0.0)
    y, nrms = add_noise(x, snr_db, seed, over=slice(i0, i1))
    samples_per_period = sr / f0
    centroid = rms_signal = None
    if amps:
        p = {k * f0: (amp * a) ** 2 for k, a in amps.items() if a > 0}
        if planted_amp:
            p[1.5 * f0] = planted_amp ** 2
        # the POWER-weighted centroid of a known line set: sum f P / sum P,
        # which is what `spectral_centroid(weight="power")` is defined as
        centroid = sum(f * v for f, v in p.items()) / sum(p.values())
        rms_signal = math.sqrt(0.5 * sum(p.values()))
    t_truth = dict(kind=kind, band_limited=band_limited, f0=f0, duty=duty,
                   dur=dur, phase=phase, amp=amp, sr=sr, i0=i0, i1=i1,
                   snr_db=snr_db, noise_rms=nrms, seed=seed, amps=amps,
                   n_per_period=samples_per_period, centroid=centroid,
                   inharmonic_share_db=planted, planted_amp=planted_amp,
                   rms_signal=rms_signal,
                   #: one period of the IDEAL naive shape, which is what the
                   #: per-period descriptors' closed forms are properties of.
                   #: `cycle_average`'s own resampling of a discontinuity
                   #: softens it -- a naive saw's rectangularity reads 0.414
                   #: through cycle_average against an exact 0.5 on the ideal
                   #: period, which is the docstring's own 0.42 for a saw and
                   #: is the resampling, not the estimator.
                   ideal_cycle=(None if band_limited else
                                amp * _naive(kind, 1.0, 1024, 1024, duty, 0.0)),
                   stationary=True, n_modes=len(amps or {}) or None,
                   taus=[], ambiguous_decay=False,
                   #: NO onsets, and that is the ground truth rather than a
                   #: gap: this family has no pre-onset lead, so the record
                   #: begins at full amplitude and holds no RISE, which is
                   #: what `onsets` detects. An estimator that found one here
                   #: would be reading the first sample's discontinuity as a
                   #: hit.
                   onsets=[],
                   #: exact closed forms for the per-period descriptors of the
                   #: NAIVE shapes (see the comment above `_naive`)
                   #: naive saw: one jump of 2A(N-1)/N against a mean step of
                   #: 4A(N-1)/N^2, so the ratio is exactly N/2. Naive
                   #: rectangle: two jumps of 2A and no other motion, so the
                   #: ratio is N/2 as well. Naive sine: 2A sin(pi/N) over a
                   #: mean 4A/N, so N sin(pi/N)/2 -> pi/2. The triangle has no
                   #: closed form (see the check) and is not gated.
                   step_ratio=(None if band_limited else
                               {"saw": samples_per_period / 2.0,
                                "rect": samples_per_period / 2.0,
                                "tri": None,
                                "sine": samples_per_period
                                * math.sin(math.pi / samples_per_period) / 2.0}[kind]),
                   rectangularity=(None if band_limited else
                                   {"saw": 0.5, "rect": 1.0, "tri": 0.5,
                                    "sine": 1.0 - 2.0 / math.pi * math.asin(0.5)}[kind]),
                   midpoint_crossings=2,
                   waveform=kind,
                   rms=(amp * {"saw": 1 / math.sqrt(3), "rect": 1.0,
                               "tri": 1 / math.sqrt(3),
                               "sine": 1 / math.sqrt(2)}[kind]
                        if not band_limited else None),
                   peak=(amp * 1.0 if not band_limited else None))
    lab = (f"{'BL' if band_limited else 'naive'} {kind} f0={f0:g}Hz "
           f"duty={duty:g} dur={dur:g}s phase={phase:g} level={amp:g} "
           f"snr={snr_db:g}dB"
           + ("" if planted is None else f" inharmonic={planted:g}dB"))
    return Fixture("harmonic_mixture", lab, y, sr, t_truth,
                   dict(frequency=f0, duration=dur, phase=phase,
                        snr_db=snr_db, level=amp))


_HM_NOM = dict(kind="saw", band_limited=True, f0=220.0, duty=0.5, dur=0.30,
               phase=0.0, amp=1.0, snr_db=float("inf"))


def harmonic_mixture_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("f0", (110.0, 220.0, 660.0, 1760.0)),
              ("dur", (0.08, 0.15, 0.30, 0.60)),
              ("phase", (0.0, 1.0, 2.2, 4.0)),
              ("snr_db", (float("inf"), 60.0, 40.0, 20.0)),
              ("amp", (1.0, 0.1, 0.01, 0.001)),
              ("kind", ("saw", "rect", "tri", "sine")),
              ("duty", (0.25, 0.5, 0.75)),
              ("band_limited", (True, False))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_HM_NOM, **{axis: v})
            if axis == "duty":
                p["kind"] = "rect"
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_harmonic_mixture(p["kind"], p["band_limited"], p["f0"],
                                         p["duty"], p["dur"], p["phase"],
                                         p["amp"], p["snr_db"],
                                         6000 + 17 * i + j, sr))
    # the naive per-period shapes, for the time-domain descriptors
    for kind in ("saw", "rect", "tri", "sine"):
        out.append(_harmonic_mixture(kind, False, 220.0, 0.5, 0.30, 0.0, 1.0,
                                     float("inf"), 6800, sr))
    # a known inharmonic share planted over a band-limited saw
    for share in (-10.0, -30.0, -60.0):
        out.append(_harmonic_mixture("saw", True, 220.0, 0.5, 0.30, 0.0, 1.0,
                                     float("inf"), 6900, sr,
                                     inharmonic_share_db=share))
    return out


# ===========================================================================
# 7. noise through a filter whose response is known in closed form
# ===========================================================================
def _known_filter(kind, fc, q, order, sr):
    """(sos, freqs, mag_db, truth) for the filter, from `scipy.signal` only.

    The reference response is `freqz`'s, computed from the SAME coefficients
    the noise is filtered with, on a dense log grid. That is ground truth
    independent of `audio_measure`: no estimator in this module is consulted
    to produce it."""
    if kind == "lowpass":
        sos = signal.butter(order, fc, btype="low", fs=sr, output="sos")
    elif kind == "highpass":
        sos = signal.butter(order, fc, btype="high", fs=sr, output="sos")
    elif kind == "resonant":
        # a single two-pole resonance at fc with quality q
        w0 = 2 * math.pi * fc / sr
        alpha = math.sin(w0) / (2 * q)
        b = np.array([alpha, 0.0, -alpha])
        a = np.array([1 + alpha, -2 * math.cos(w0), 1 - alpha])
        sos = signal.tf2sos(b / a[0], a / a[0])
    else:                                                    # pragma: no cover
        raise ValueError(kind)
    f = np.geomspace(5.0, 0.49 * sr, 4000)
    w, h = signal.sosfreqz(sos, worN=2 * math.pi * f / sr)
    mag = np.abs(h)
    mag_db = 20.0 * np.log10(np.maximum(mag, 1e-30))
    truth = dict(kind=kind, fc=fc, q=q, order=order,
                 ref_freqs=f, ref_mag_db=mag_db)
    if kind in ("lowpass", "highpass"):
        plateau = float(np.median(mag_db[f < fc / 4]) if kind == "lowpass"
                        else np.median(mag_db[f > fc * 4]))
        truth["plateau_db"] = plateau
        truth["corner_hz"] = float(np.interp(plateau - 3.0,
                                             *((mag_db[::-1], f[::-1]) if kind == "lowpass"
                                               else (mag_db, f))))
        truth["asymptote_db_oct"] = (-6.0 * order if kind == "lowpass"
                                     else 6.0 * order)
        # THE SLOPE A FIT OVER A FINITE BAND ACTUALLY HAS, which is not the
        # asymptote: a digital butterworth's magnitude plunges towards zero at
        # Nyquist (the bilinear map sends f = fs/2 to infinity), so a fit that
        # reaches 0.4 fs reads -13.0 dB/oct where the asymptote is -12. The
        # band is capped at 0.12 fs for that reason and the reference slope is
        # the least-squares slope of THIS curve over THIS band -- measured,
        # from freqz, not quoted from the order.
        lo = fc * 2.0 if kind == "lowpass" else max(f[0], fc / 8.0)
        hi = min(fc * 8.0, 0.12 * sr) if kind == "lowpass" else fc / 2.0
        truth["slope_band"] = (lo, hi)
        if hi / lo >= 2.0:
            sel = (f >= lo) & (f <= hi)
            truth["slope_db_oct"] = float(np.polyfit(np.log2(f[sel]),
                                                     mag_db[sel], 1)[0])
        else:
            truth["slope_db_oct"] = None
        truth["peak_db"] = None
        truth["f_peak"] = None
    else:
        i = int(np.argmax(mag_db))
        truth["f_peak"] = float(f[i])
        truth["peak_db"] = float(mag_db[i])
        truth["plateau_db"] = None
        truth["corner_hz"] = None
        truth["slope_db_oct"] = None
        truth["slope_band"] = None
        truth["asymptote_db_oct"] = None
        # -3 dB bandwidth of the peak, read off the reference curve
        lo = f[:i][mag_db[:i] <= mag_db[i] - 3.0]
        hi = f[i:][mag_db[i:] <= mag_db[i] - 3.0]
        truth["q_measured"] = (float(f[i] / (hi[0] - lo[-1]))
                               if len(lo) and len(hi) else None)
    return sos, truth


def _filtered_noise(kind, fc, q, order, dur, level, snr_db, seed, sr=SR):
    """White noise through the known filter, plus that filter's own impulse
    response in `truth['ir']` -- the input `resonant_peak` / `bandwidth_q` /
    `corner_3db` / `transfer` are defined on."""
    sos, ft = _known_filter(kind, fc, q, order, sr)
    n = int(dur * sr)
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(n)
    core = level * signal.sosfilt(sos, w)
    core = core / max(float(np.max(np.abs(core))), 1e-30) * level
    x, i0, i1 = _struck(core, sr)
    # `snr_db` here means a SECOND, unfiltered white noise floor under the
    # filtered one: the axis is still "how much of the record is the thing
    # being measured", which is what an SNR axis is for.
    y, nrms = add_noise(x, snr_db, seed + 1, over=slice(i0, i1))
    ir = np.zeros(1 << 14)
    ir[0] = 1.0
    ir = signal.sosfilt(sos, ir)
    t_truth = dict(dur=dur, level=level, sr=sr, i0=i0, i1=i1, snr_db=snr_db,
                   noise_rms=nrms, seed=seed, ir=ir, sos=sos,
                   stationary=True, taus=[], n_modes=None,
                   ambiguous_decay=False, onsets=[i0], **ft)
    lab = (f"{kind} order={order} fc={fc:g}Hz q={q:g} dur={dur:g}s "
           f"level={level:g} snr={snr_db:g}dB seed={seed}")
    return Fixture("filtered_noise", lab, y, sr, t_truth,
                   dict(frequency=fc, duration=dur, phase=None,
                        snr_db=snr_db, level=level))


_FN_NOM = dict(kind="lowpass", fc=1200.0, q=0.707, order=2, dur=0.40,
               level=0.5, snr_db=float("inf"))


def filtered_noise_fixtures(sr=SR) -> list[Fixture]:
    out, seen = [], set()
    sweeps = [("fc", (200.0, 600.0, 1200.0, 4800.0)),
              ("dur", (0.12, 0.25, 0.40, 0.90)),
              ("snr_db", (float("inf"), 60.0, 40.0, 25.0)),
              ("level", (1.0, 0.1, 0.01, 0.001)),
              ("order", (1, 2, 4)),
              ("kind", ("lowpass", "highpass", "resonant"))]
    for i, (axis, values) in enumerate(sweeps):
        for j, v in enumerate(values):
            p = dict(_FN_NOM, **{axis: v})
            if axis == "kind" and v == "resonant":
                p["q"] = 8.0
            key = tuple(sorted(p.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(_filtered_noise(p["kind"], p["fc"], p["q"], p["order"],
                                       p["dur"], p["level"], p["snr_db"],
                                       7000 + 17 * i + j, sr))
    # the noise-REALISATION axis, which stands in for phase here
    for s in range(5):
        out.append(_filtered_noise("resonant", 1200.0, 8.0, 2, 0.40, 0.5,
                                   float("inf"), 7700 + s, sr))
    for qq in (2.0, 8.0, 20.0):
        out.append(_filtered_noise("resonant", 900.0, qq, 2, 0.40, 0.5,
                                   float("inf"), 7800, sr))
    return out


# ===========================================================================
# the catalogue, and the guard on its sweeps
# ===========================================================================
FAMILIES = {
    "damped_sine": damped_sine_fixtures,
    "two_modes": two_mode_fixtures,
    "transient_then_ring": transient_ring_fixtures,
    "envelope_plus_noise": envelope_noise_fixtures,
    "repeated_hits": repeated_hit_fixtures,
    "harmonic_mixture": harmonic_mixture_fixtures,
    "filtered_noise": filtered_noise_fixtures,
}

#: The seven signal types #158 names, in its own words, against the family
#: that produces each. A family missing from here, or a phrase with no family,
#: is a catalogue that has drifted from the issue it answers.
ISSUE_158_TYPES = {
    "damped sine at known f and tau": "damped_sine",
    "two damped modes with adjustable spacing and phase": "two_modes",
    "a pitch transient followed by a steady ring": "transient_then_ring",
    "a known envelope plus a noise floor": "envelope_plus_noise",
    "repeated hits at unequal levels": "repeated_hits",
    "a known harmonic mixture": "harmonic_mixture",
    "noise through a known filter": "filtered_noise",
}


def catalogue(kinds=None, sr=SR) -> list[Fixture]:
    """Every fixture, or only those of `kinds`."""
    out = []
    for kind, fn in FAMILIES.items():
        if kinds and kind not in kinds:
            continue
        out.extend(fn(sr))
    return out


def axis_coverage(fixtures=None) -> list[tuple]:
    """(kind, axis, n_distinct, spread, ok, note) for every family and axis.

    `ok` is False when an axis took fewer than `AXIS_MIN_DISTINCT` distinct
    values, or spanned less than `AXIS_MIN_SPREAD`, AND the (kind, axis) pair
    has no stated reason in `AXIS_NOT_APPLICABLE`."""
    fixtures = catalogue() if fixtures is None else fixtures
    rows = []
    for kind in FAMILIES:
        fx = [f for f in fixtures if f.kind == kind]
        for axis in AXES:
            vals = [f.axes.get(axis) for f in fx]
            vals = [v for v in vals if v is not None]
            note = AXIS_NOT_APPLICABLE.get((kind, axis))
            distinct = sorted(set(vals))
            how, need = AXIS_MIN_SPREAD[axis]
            if how == "ratio":
                finite = [v for v in distinct if math.isfinite(v) and v > 0]
                spread = (max(finite) / min(finite)) if len(finite) > 1 else 0.0
            else:
                finite = [v for v in distinct if math.isfinite(v)]
                spread = (max(finite) - min(finite)) if len(finite) > 1 else 0.0
            ok = len(distinct) >= AXIS_MIN_DISTINCT and spread >= need
            if note is not None:
                rows.append((kind, axis, len(distinct), spread, True,
                             "NOT APPLICABLE: " + note))
            else:
                rows.append((kind, axis, len(distinct), spread, ok,
                             f"needs >={AXIS_MIN_DISTINCT} values and "
                             f"{how} >= {need:g}"))
    return rows


def main() -> int:
    fx = catalogue()
    print("=" * 78)
    print("Fixture catalogue")
    print("=" * 78)
    for kind in FAMILIES:
        rows = [f for f in fx if f.kind == kind]
        n = sum(f.n for f in rows)
        print(f"  {kind:22s} {len(rows):4d} records, {n/SR:8.2f} s of audio")
    print(f"  {'TOTAL':22s} {len(fx):4d} records, {sum(f.n for f in fx)/SR:8.2f} s")
    print("\n" + "=" * 78)
    print("Axis coverage (#158: frequency, duration, phase, SNR, level)")
    print("=" * 78)
    bad = 0
    for kind, axis, n, spread, ok, note in axis_coverage(fx):
        flag = "OK  " if ok else "FAIL"
        if not ok:
            bad += 1
        print(f"  {flag} {kind:22s} {axis:10s} {n:2d} values, spread {spread:9.3f}")
        if note.startswith("NOT APPLICABLE") or not ok:
            print(f"       {note}")
    missing = set(FAMILIES) - set(ISSUE_158_TYPES.values())
    if missing:
        print(f"\nFAIL: families with no #158 signal type: {sorted(missing)}")
        bad += 1
    print()
    if bad:
        print(f"{bad} coverage check(s) FAILED.")
        return 1
    print("Axis coverage OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

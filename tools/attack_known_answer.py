#!/usr/bin/env python3
"""Known-answer suite for the M1A waveform-fit attack estimator (analysis v3).

Every signal here is synthesized from closed-form parts, independently of both
synthesizers, and carries an ANALYTICALLY KNOWN 10-90 % attack time:

    x(t) = level * env(t) * c(t) + noise

* env(t): the amplitude envelope. It rises from 0 at t_on to 1 at t_on + R
  (R = the TOTAL RAMP duration) with a known shape, then optionally decays
  exponentially to a sustain level (an ADS envelope, as both M1A patches have).
  The known answer is the 10-90 % time of that rise:
      power law  env = u^p, u = (t - t_on) / R        -> R * (0.9^(1/p) - 0.1^(1/p))
      RC charge  env = (1 - e^(-u*k)) / (1 - e^(-k))  -> closed form below
  The RC shape is NOT in the estimator's model family; it is here because an
  analogue VCA attack is an RC charge, and the estimator must be shown to
  measure shapes it does not fit exactly.
* c(t): a harmonic-rich carrier (band-limited saw, or saw + octave saw as in
  the M1A patch) through a QUASI-STATIC two-pole low-pass whose cutoff follows
  a known trajectory: a filter-envelope-like brightening at the onset decaying
  to the rest cutoff. Each harmonic takes the magnitude AND phase of the
  low-pass at the instantaneous cutoff, so the waveform shape changes during
  the attack exactly as a moving filter changes it. c(t) is renormalised to
  constant power, so the brightening does not alter the level envelope and the
  answer stays the envelope's.

ONE QUANTITY. The estimator reports, and the tolerance applies to, the 10-90 %
attack time in milliseconds. `ramp_ms` (R, the total 0-100 % duration of the
fitted shape) is a fit parameter, not the reported quantity; the two differ by
the shape-dependent factor kfrac(p) = 0.9^(1/p) - 0.1^(1/p), which is 0.80 at
p = 1 and 0.41 at p = 4. m1a-envelope-score-v2 stated its qualification in the
10-90 quantity (the suite's `known_10_90_ms`) but gated on `ramp_ms`; v3 gates
on the 10-90 value, and treats R only as the search variable whose boundaries
(the 32-sample minimum and the 25 ms + 16-sample maximum) are search limits.
"""
from __future__ import annotations

import math

import numpy as np

SR = 48000


def power_kfrac(p):
    """10-90 % time as a fraction of the total ramp for env = u^p."""
    return 0.9 ** (1 / p) - 0.1 ** (1 / p)


def rc_kfrac(k):
    """10-90 % time as a fraction of the total ramp for the RC charge
    env = (1 - e^(-k u)) / (1 - e^(-k)): solve env = x for u."""
    full = 1 - math.exp(-k)
    u = lambda x: -math.log(1 - x * full) / k            # noqa: E731
    return u(0.9) - u(0.1)


def shape_kfrac(shape):
    kind, value = shape
    return power_kfrac(value) if kind == "p" else rc_kfrac(value)


def envelope(t, on, t1090_ms, shape, decay_s=None, sustain=1.0):
    """Amplitude envelope with an exactly known 10-90 % rise of `t1090_ms`."""
    kind, value = shape
    ramp = (t1090_ms / 1000.) / shape_kfrac(shape)
    u = np.clip((t - on) / ramp, 0., 1.)
    rise = u ** value if kind == "p" else (1 - np.exp(-value * u)) / (1 - math.exp(-value))
    if decay_s:
        after = t >= on + ramp
        rise = rise.copy()
        rise[after] = sustain + (1 - sustain) * np.exp(-(t[after] - on - ramp) / decay_s)
    return rise


def cutoff_trajectory(t, on, rest_hz, peak_ratio, decay_s):
    """Filter-envelope-like cutoff: rest before the onset, jumps to
    rest * peak_ratio at the onset and decays exponentially back to rest."""
    if peak_ratio == 1.0:
        return np.full_like(t, rest_hz)
    dt = np.maximum(t - on, 0.)
    boost = (peak_ratio - 1.) * np.exp(-dt / decay_s) * (t >= on)
    return rest_hz * (1. + boost)


def carrier(t, hz, fc, *, octave_mix=0.535, phase_cycles=0.0, q=0.707):
    """Band-limited saw (+ octave saw) through a quasi-static two-pole
    low-pass at the instantaneous cutoff `fc` (an array), constant power."""
    x = np.zeros_like(t)
    power = np.zeros_like(t)
    tt = t - phase_cycles / hz
    parts = [(1.0, hz)] + ([(octave_mix, 2 * hz)] if octave_mix else [])
    for gain, base in parts:
        k = 1
        while k * base < 0.45 * SR:
            f = k * base
            s = f / fc
            # H(jw) = 1 / (1 - s^2 + j s / q)
            re, im = 1 - s * s, s / q
            mag = 1. / np.sqrt(re * re + im * im)
            ph = -np.arctan2(im, re)
            a = gain * mag / k
            x += a * np.sin(2 * np.pi * f * tt + ph)
            power += 0.5 * a * a
            k += 1
    return x / np.sqrt(power)


SPECTRA = {
    # label: (rest cutoff Hz, peak ratio at onset, decay s)
    "steady-dull": (400., 1.0, 1.0),
    "steady-bright": (3000., 1.0, 1.0),
    "fenv-2x-50ms": (400., 2.0, 0.05),       # the model's own fenv law: 2x, 50 ms
    "fenv-4x-150ms": (400., 4.0, 0.15),      # stronger and slower
    "fenv-8x-20ms": (300., 8.0, 0.02),       # large, fast sweep inside the attack
}

SHAPES = {
    "p0.5": ("p", 0.5), "p1": ("p", 1.0), "p2": ("p", 2.0),
    "p3": ("p", 3.0), "p4": ("p", 4.0),
    "rc3": ("rc", 3.0), "rc5": ("rc", 5.0),
}

DURATIONS_MS = (0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0)
NOTES = (36, 43)
PHASES = (0.0, 0.37)


def note_hz(note):
    return 440. * 2 ** ((note - 69) / 12)


def render(case, seconds=1.2):
    """The known-answer signal for one case dict; returns audio, on, off."""
    t = np.arange(round(seconds * SR)) / SR
    on = 0.1 + 0.3 / SR                                  # off-grid onset
    off = on + 0.6
    hz = note_hz(case["note"])
    rest, ratio, decay = SPECTRA[case["spectrum"]]
    fc = cutoff_trajectory(t, on, rest, ratio, decay)
    env = envelope(t, on, case["t1090_ms"], SHAPES[case["shape"]],
                   decay_s=case.get("decay_s"), sustain=case.get("sustain", 1.0))
    rel = t >= off
    env[rel] = env[rel] * np.exp(-(t[rel] - off) / 0.1)
    x = 0.12 * env * carrier(t, hz, fc, phase_cycles=case["phase"])
    noise_db = case.get("noise_db")
    if noise_db is not None:
        rng = np.random.default_rng(case.get("seed", 0))
        x = x + 0.12 * 10 ** (noise_db / 20) * rng.standard_normal(len(t))
    x = np.round(x * 32768) / 32768                       # 16-bit, as the model's WAV
    return x, on, off


def suite_cases():
    """The full grid: shapes x 10-90 durations x spectra x notes x phases,
    each with an ADS decay to 0.75 and -60 dB noise (the realistic case),
    plus a no-decay/no-noise slice so the effect of each is visible."""
    cases = []
    for shape in SHAPES:
        for d in DURATIONS_MS:
            for spectrum in SPECTRA:
                for note in NOTES:
                    for phase in PHASES:
                        cases.append({"shape": shape, "t1090_ms": d, "spectrum": spectrum,
                                      "note": note, "phase": phase, "decay_s": 0.08,
                                      "sustain": 0.75, "noise_db": -60.,
                                      "seed": len(cases)})
    for shape in ("p1", "p3", "rc3"):
        for d in DURATIONS_MS:
            for note in NOTES:
                cases.append({"shape": shape, "t1090_ms": d, "spectrum": "steady-dull",
                              "note": note, "phase": 0.0, "slice": "clean"})
    return cases


def _estimator(name):
    if name == "v2":
        import measure_mono_m1a_reference as probe
        return probe.attack_fit
    if name == "v3":
        import attack_fit_v3
        return attack_fit_v3.attack_fit_v3
    raise ValueError(name)


def measure_case(case, estimator="v3"):
    """Run the estimator on one known-answer case; the row records the truth,
    the fit, and the error in the ONE reported quantity (10-90 % ms).
    `estimator` is "v2", "v3", or a callable (for injected-defect controls)."""
    import measure_mono_m1a_reference as probe
    fit = _estimator(estimator) if isinstance(estimator, str) else estimator
    x, on, off = render(case)
    try:
        r = fit(x, SR, note_hz(case["note"]), on, off)
    except probe.ref.Refused as exc:
        return {**case, "refused": str(exc)}
    return {**case, "measured_1090_ms": float(r["attack_10_90_ms"]),
            "error_ms": float(r["attack_10_90_ms"]) - case["t1090_ms"],
            "fit_ramp_ms": float(r["ramp_ms"]), "fit_shape_p": float(r["shape_p"]),
            "explained_ratio": float(r["explained_ratio"]), "t0_ms": float(r["t0_ms"]),
            "search_boundary": r.get("search_boundary", boundary_v2(r))}


def boundary_v2(fit):
    """v2 does not report its search limits; derive them from its grid
    (32-sample minimum; maximum = round(25 ms) + 16 samples)."""
    n = round(fit["ramp_ms"] * SR / 1000)
    return "minimum" if n <= 32 else "maximum" if n >= round(0.025 * SR) + 16 else None


def run_suite(cases=None, estimator="v3", processes=None):
    import functools
    import os
    from concurrent.futures import ProcessPoolExecutor
    cases = suite_cases() if cases is None else cases
    if not isinstance(estimator, str) or processes == 1:
        return [measure_case(c, estimator) for c in cases]
    os.environ.setdefault("OMP_NUM_THREADS", "1")       # one BLAS thread per worker
    with ProcessPoolExecutor(processes) as pool:
        return list(pool.map(functools.partial(measure_case, estimator=estimator),
                             cases, chunksize=4))

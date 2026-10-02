#!/usr/bin/env python3
"""Every estimator in `model/audio_measure.py` against synthetic ground truth.

    python3 tools/probes/estimator_ground_truth.py check      # the gate
    python3 tools/probes/estimator_ground_truth.py controls   # start red + mutants
    python3 tools/probes/estimator_ground_truth.py matrix     # the coverage table

`check` runs every estimator against every applicable fixture family from
`tools/probes/estimator_fixtures.py` -- seven signal types, each swept across
frequency, duration, phase, SNR and level -- and exits non-zero when a bound
it measures stops holding. The fixtures' answers are known from their
synthesis parameters and closed forms derived from them, never from another
estimator in the module under test (#158, #517).

FOUR OUTCOMES, NOT TWO
----------------------
`REFUSED` is a first-class result here, as `CLAUDE.md` requires, and so is
`NOT-MEASURED`:

    PASS            the reading matched the known answer
    REFUSED         the estimator declined, and declining is acceptable (or
                    required) for this fixture -- the reason is printed
    IDENTIFIED      an ambiguous fixture answered with a NAMED component
    INSUFFICIENT    an ambiguous fixture answered "I cannot tell"
    SKIP            this estimator does not apply to this family, with the
                    reason stated -- never a silent omission
    NOT-MEASURED    the CHECK's own precondition was unmet on this record
                    (e.g. a trailing-silence check on a record that has a
                    noise floor). Printed, never dropped.
    FAIL            the reading contradicted the known answer, or the
                    estimator refused where the fixture is inside its domain

THE AMBIGUITY RULE (#517 acceptance criterion 4, #115)
------------------------------------------------------
A record holding more than one decay constant -- a beating pair with
different taus, a transient over a ring, several hits -- has no single decay
number. On those fixtures an estimator must either NAME the component or
interval it measured, or report insufficient evidence. A confident number
matching neither component is `AMBIGUOUS-CONFIDENT`, which is a FAILURE of
this suite, not a pass. `damped_sinusoid` failed exactly this on 25 of 72
beating pairs when the suite was first run (it reported 93.3 ms for a pair
whose components were 200 ms and 50 ms); the repair is in `audio_measure` and
`test_audio_measure.py`, and this is the gate that holds it.

WHAT THIS SUITE IS BLIND TO, and the guard that says so
-------------------------------------------------------
`controls` is the other half, and it is required reading before any number
here is quoted. It runs the suite against two STUBS (rule 1: a harness nobody
has watched fail is not a harness) and against named mutants of the
estimators, and prints the estimators x defects matrix rule 4 asks for --
MOVED for the checks that saw each defect, BLIND for the ones that did not.
A check in the BLIND column for every mutant is decoration.

HOW A READER CALIBRATES THESE NUMBERS
-------------------------------------
Wrong-then-right rate while this suite was written: five bounds were set from
a guess, run, and corrected to the measured value before the first green run
(`spectral_flatness` on white noise, `tonality_db` on white noise,
`step_ratio` on a naive triangle, `band_energy`'s leakage against an analytic
integral, and `schroeder_t20`'s required-report condition). Every one was
caught by running the gate against the current state rather than by
inspection, which is the only reason they are listed and not shipped.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

#: numpy renamed `trapz` to `trapezoid` in 2.0 and CI pins 1.26, where only
#: the old name exists. One name, resolved once.
_TRAPZ = getattr(np, "trapezoid", None) or np.trapz

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import audio_measure as am                                            # noqa: E402
import estimator_fixtures as ef                                       # noqa: E402

PASS, FAIL, SKIP, REFUSED = "PASS", "FAIL", "SKIP", "REFUSED"
NOT_MEASURED, IDENTIFIED, INSUFFICIENT = "NOT-MEASURED", "IDENTIFIED", "INSUFFICIENT"
AMBIGUOUS = "AMBIGUOUS-CONFIDENT"
#: statuses that make the gate red
RED = (FAIL, AMBIGUOUS)
#: statuses that count as the estimator having been exercised on that family
GREEN = (PASS, REFUSED, IDENTIFIED, INSUFFICIENT)


@dataclass
class Verdict:
    estimator: str
    kind: str
    label: str
    status: str
    detail: str = ""


def _ok(detail=""):
    return (PASS, detail)


def _bad(detail):
    return (FAIL, detail)


def _nm(reason):
    return (NOT_MEASURED, reason)


def _ref(reason):
    return (REFUSED, reason)


def near(got, want, rtol, atol=0.0) -> bool:
    if got is None or want is None:
        return False
    if not (np.isfinite(got) and np.isfinite(want)):
        return False
    return abs(got - want) <= atol + rtol * abs(want)


def _err(got, want) -> str:
    if want in (0, 0.0):
        return f"got {got:.6g} want {want:.6g}"
    return f"got {got:.6g} want {want:.6g} ({100.0*(got/want - 1):+.2f} %)"


def clean(fx) -> bool:
    """No noise floor on this record, so the digital-silence and exact-peak
    checks have their precondition."""
    return not math.isfinite(fx.truth.get("snr_db", float("inf")))


def sounding(fx):
    return fx.x[fx.truth["i0"]:fx.truth["i1"]]


def carrier_slice(fx, *, margin_db=20.0, max_taus=8.0):
    """The part of the record where the KNOWN carrier is still the signal.

    THIS IS A PRECONDITION, NOT A CONVENIENCE, and getting it wrong was four
    of the suite's wrong-then-right results. Three estimators here read the
    record from end to end -- `zero_crossing_frequency` counts from its first
    upward crossing to its last, `instantaneous_frequency` differentiates the
    analytic phase everywhere, `glide_law` compares f[0] with f[-1] -- so
    handing them a record whose last 300 ms is 60 dB of decayed ring over a
    noise floor measures the floor. Measured, on the nominal 220 Hz / 30 ms
    fixture at 20 dB SNR: `zero_crossing_frequency` read 8752 Hz for a 220 Hz
    ring, and `glide_law` named 'constant-rate' for a record whose frequency
    does not move. Neither is a defect in the estimator; both are a record
    handed over past its own validity.

    The slice runs from the onset to the earlier of `max_taus` time constants
    and the instant the known envelope falls to `margin_db` above the known
    noise floor -- both computed from synthesis parameters, never measured."""
    t = fx.truth
    i0, i1 = t["i0"], t["i1"]
    tau = t.get("tau")
    amp = (t.get("amp") or t.get("level")
           or (max(t["levels"]) if t.get("levels") else None))
    nr = t.get("noise_rms", 0.0) or 0.0
    end = i1
    if tau and amp:
        lim = max_taus * tau
        if nr > 0:
            floor = nr * 10 ** (margin_db / 20.0)
            lim = min(lim, tau * math.log(amp / floor)) if amp > floor else 0.0
        end = min(i1, i0 + int(lim * fx.sr))
    if t.get("gaps_s"):                 # ONE hit, not the whole phrase
        end = min(end, i0 + int(0.8 * min(t["gaps_s"]) * fx.sr))
    return slice(i0, end)


# ===========================================================================
# the ambiguity rule
# ===========================================================================
def ambiguity(fx, value, *, detail=None, companion_freq=None, rtol=0.15,
              interval=None):
    """The #517 criterion-4 verdict for a decay number on an ambiguous record.

    Returns (status, note). `detail` is the estimator's own `Estimate.detail`;
    an interval key in it ('t0', 'start_s', 'end_s', 'n') is how an estimator
    STATES which interval it measured. `companion_freq` is a frequency the
    same estimator reported, which is how it can instead NAME the component --
    `damped_sinusoid` reports (freq, tau) together, so a tau matching the mode
    whose frequency it also reported is an identification, not a coincidence.
    """
    taus = fx.truth.get("taus") or []
    hit = [t for t in taus if near(value, t, rtol)]
    if not hit:
        return (AMBIGUOUS, f"reported {value*1e3:.2f} ms; components are "
                           f"{', '.join(f'{t*1e3:.1f}' for t in taus)} ms; "
                           f"energy-weighted mean is "
                           f"{fx.truth.get('tau_energy_mean', float('nan'))*1e3:.2f} ms. "
                           f"A number matching no component is not an answer.")
    if interval:
        return (IDENTIFIED, f"{value*1e3:.2f} ms matches the "
                            f"{hit[0]*1e3:.1f} ms component, over the interval "
                            f"the CALLER named ({interval})")
    names = set(detail or ()) & {"t0", "start_s", "end_s", "n", "window_s"}
    if names:
        return (IDENTIFIED, f"{value*1e3:.2f} ms matches the {hit[0]*1e3:.1f} ms "
                            f"component, over the interval its own detail names "
                            f"({sorted(names)})")
    if companion_freq is not None:
        fs = [fx.truth.get("f1"), fx.truth.get("f2"), fx.truth.get("f_end"),
              fx.truth.get("f")]
        if any(f and near(companion_freq, f, 0.05) for f in fs):
            return (IDENTIFIED, f"{value*1e3:.2f} ms matches the {hit[0]*1e3:.1f} ms "
                                f"component and the same reading names its "
                                f"frequency ({companion_freq:.1f} Hz)")
    return (AMBIGUOUS, f"reported {value*1e3:.2f} ms, which matches the "
                       f"{hit[0]*1e3:.1f} ms component -- but names neither the "
                       f"component nor the interval, so the match is not "
                       f"something a caller could have known")


# ===========================================================================
# per-estimator checks
# ===========================================================================
def c_rms(fx):
    want = fx.truth.get("rms")
    if want is None:
        return _nm("this family has no closed-form RMS (its envelope is a "
                   "noise realisation, not an analytic function)")
    got = am.rms(fx.x)
    return _ok(_err(got, want)) if near(got, want, 0.03) else _bad(_err(got, want))


def c_peak(fx):
    want = fx.truth.get("peak")
    if want is None:
        return _nm("this family's peak is a noise realisation, not a closed form")
    got = am.peak(fx.x)
    if clean(fx):
        return _ok(_err(got, want)) if near(got, want, 0.03) else _bad(_err(got, want))
    # a noise floor can only ADD to the largest excursion; six sigma of it is
    # the bound, and the reading may not fall below the signal's own peak
    hi = want + 6.0 * fx.truth.get("noise_rms", 0.0)
    return (_ok(f"{_err(got, want)}, inside [0.97, +6 sigma noise]")
            if 0.97 * want <= got <= hi else _bad(_err(got, want)))


def c_is_silent(fx):
    if am.is_silent(fx.x):
        return _bad("called a sounding record silent")
    if not am.is_silent(np.zeros(fx.n)):
        return _bad("called an all-zero record sounding")
    return _ok("False on the record, True on its zeroed copy")


def c_clipped_fraction(fx):
    if not clean(fx):
        return _nm("a noise floor pushes samples past the SIGNAL's known "
                   "peak, so the exact zero-fraction below has no closed "
                   "form here")
    pk = fx.truth.get("peak") or float(np.max(np.abs(fx.x)))
    got = am.clipped_fraction(fx.x, pk * 1.01 + 1e-12)
    if got != 0.0:
        return _bad(f"{got:.6g} of samples at or beyond 1.01x the known peak")
    if fx.truth.get("kind") == "sine" and not fx.truth.get("band_limited", True):
        # closed form: the fraction of a sine above s is 1 - (2/pi) asin(s)
        s = 0.5
        want = 1.0 - 2.0 / math.pi * math.asin(s)
        got = am.clipped_fraction(fx.x, s * fx.truth["amp"])
        return (_ok(f"sine at half scale {_err(got, want)}")
                if near(got, want, 0.02) else _bad(_err(got, want)))
    return _ok("0.0 beyond the known peak")


def c_quantisation_floor(fx):
    pk = am.peak(fx.x)
    lsb = pk / 1000.0
    got = am.quantisation_floor(fx.x, lsb)
    want = 60.0
    return (_ok(_err(got, want)) if near(got, want, 0.0, 0.3)
            else _bad(_err(got, want)))


def c_max_sample_step(fx):
    want = fx.truth.get("max_step")
    if want is None:
        return _nm("no closed-form largest step for this family")
    if not clean(fx):
        return _nm("a white-noise floor's own largest step is a draw from a "
                   "distribution, not a closed form")
    got = am.max_sample_step(fx.x)
    if fx.truth.get("max_step_exact"):
        return (_ok(f"{_err(got, want)} -- the ONSET step, "
                    f"amp |sin(phase)| in one sample")
                if near(got, want, 1e-9) else _bad(_err(got, want)))
    return (_ok(f"{got:.6g} inside [0.8, 1.0] x the closed-form in-carrier "
                f"bound {want:.6g}")
            if 0.8 * want <= got <= want * 1.001 else _bad(_err(got, want)))


def c_longest_plateau(fx):
    if not clean(fx):
        return _nm("a noise floor has no repeated samples, so the known "
                   "digital-silence run is not the record's longest plateau")
    held = fx.truth.get("longest_plateau")
    if held:
        # The ENVELOPE's own longest run, which is not `held`: a ramp falling
        # `rate` per sample stays inside one quantisation step of `step_frac`
        # for step/rate samples, rounded up to a whole number of holds.
        # Measured 960 where the hold is 96, which is that closed form
        # (0.02 / (0.75/33600) = 896 -> 960) and not a defect; the first draft
        # of this check asserted 96 and was wrong.
        rate = 0.75 / (fx.truth["dur"] * fx.sr)
        want = int(math.ceil(fx.truth["step_frac"] / rate / held)) * held
        got = am.longest_plateau(fx.truth["env"])
        return (_ok(f"{got} samples == the closed-form run {want} of a "
                    f"{fx.truth['step_frac']:g} staircase on a ramp of "
                    f"{rate*fx.sr:.3f} per second")
                if abs(got - want) <= held else _bad(f"got {got} want {want}"))
    lead = fx.truth["i0"]
    trail = fx.n - fx.truth["i1"]
    want = max(lead, trail)
    if want < 2:
        return _nm("this family has no lead or trail of digital silence and a "
                   "float carrier repeats no sample")
    got = am.longest_plateau(fx.x)
    return (_ok(f"{got} == the known run of digital silence {want}")
            if got == want else _bad(f"got {got} want {want}"))


def c_sounding_extent(fx):
    if not clean(fx):
        return _nm("a record with a noise floor has no trailing digital "
                   "silence, which is the thing this estimator is defined on "
                   "(#139)")
    want = fx.truth.get("sounding_extent")
    if want is None:
        want = fx.truth["i1"]
    got = am.sounding_extent(fx.x)
    tol = int(fx.sr / max(fx.truth.get("f", 100.0), 20.0)) + 2
    return (_ok(f"{got} within one period of the known {want}")
            if abs(got - want) <= tol else _bad(f"got {got} want {want} (+-{tol})"))


def c_strip_trailing_silence(fx):
    if not clean(fx):
        return _nm("no trailing digital silence on a record with a noise floor")
    want = am.sounding_extent(fx.x)
    got = len(am.strip_trailing_silence(fx.x))
    return (_ok(f"{got} samples, the sounding extent")
            if got == want else _bad(f"got {got} want {want}"))


def c_analytic_signal(fx):
    """The definitional identity: Re(analytic) is the signal itself."""
    z = am.analytic_signal(fx.x)
    if len(z) != fx.n:
        return _bad(f"length {len(z)} != {fx.n}")
    e = float(np.max(np.abs(z.real - fx.x)))
    return (_ok(f"Re(z) == x to {e:.2e}") if e < 1e-9 * max(1.0, am.peak(fx.x))
            else _bad(f"Re(z) departs from x by {e:.3e}"))


def c_compare(fx):
    """Closed form: a signal against twice itself is exactly 6.0206 dB of gain
    and no shape difference."""
    c = am.compare(fx.x, 2.0 * fx.x)
    if not near(c.level_db, -6.0206, 0.0, 0.01):
        return _bad(f"level_db {c.level_db:.4f}, expected -6.0206 for a "
                    f"signal against twice itself")
    if not near(c.residual_db, -6.0206, 0.0, 0.01):
        return _bad(f"residual_db {c.residual_db:.4f}: the difference of x and "
                    f"2x is x, which is 6.02 dB below 2x")
    if c.shape_db > -100.0:
        return _bad(f"shape_db {c.shape_db:.2f}, expected far below -100 for a "
                    f"pure gain change")
    return _ok(f"level_db {c.level_db:+.4f}, residual_db {c.residual_db:+.4f}, "
               f"shape_db {c.shape_db:.1f}")


def c_nonfinite_report(fx):
    if am.nonfinite_report(fx.x) is not None:
        return _bad("reported a finite record as non-finite")
    y = fx.x.copy()
    k = fx.n // 3
    y[k] = np.nan
    r = am.nonfinite_report(y)
    if r is None:
        return _bad("missed a planted NaN")
    return _ok(f"planted NaN at {k} reported as {dict(list(r.items())[:3])}")


def c_require_finite(fx):
    y = fx.x.copy()
    y[fx.n // 3] = np.inf
    try:
        am.require_finite(fx.x)
    except Exception as e:                                   # pragma: no cover
        return _bad(f"raised on a finite record: {e}")
    try:
        am.require_finite(y)
    except am.NonFiniteAudio:
        return _ok("raises NonFiniteAudio on a planted inf, returns x otherwise")
    return _bad("accepted a record holding inf")


def c_event_slices(fx):
    """A gate built from the fixture's KNOWN hit times, so the expected slices
    are known before the call."""
    gate = np.zeros(fx.n, dtype=int)
    want = []
    if fx.kind == "repeated_hits":
        ons = fx.truth["onsets"]
        w = int(round(min(fx.truth["gaps_s"]) * 0.5 * fx.sr))
        for k in ons:
            gate[k:k + w] = 1
            want.append((k, min(fx.n, k + w)))
    else:
        gate[fx.truth["i0"]:fx.truth["i1"]] = 1
        want.append((fx.truth["i0"], fx.truth["i1"]))
    got = [tuple(s) for s in am.event_slices(gate)]
    return (_ok(f"{len(got)} slice(s) == the known hit intervals")
            if got == want else _bad(f"got {got[:4]} want {want[:4]}"))


def c_onsets(fx):
    want = fx.truth.get("onsets")
    if want is None:
        return _nm("no known onset time for this family")
    if fx.truth.get("shape") == "charge":
        return _nm("a 1-exp(-4t/T) attack has no step at t0 by construction: "
                   "where its envelope first gains 12 dB inside an 8 ms "
                   "window is a property of the attack law, not of the hit "
                   "time, so this family's slow-attack members ground-truth "
                   "segment_shape rather than onsets")
    got = am.onsets(fx.x, fx.sr)
    if not want:
        return (_ok("no onset, correctly: this family has no pre-onset lead, "
                    "so the record begins at full amplitude and holds no RISE")
                if len(got) == 0 else
                _bad(f"found {len(got)} onset(s) at "
                     f"{[round(g/fx.sr*1e3, 1) for g in got[:4]]} ms in a "
                     f"record that begins at full amplitude and so holds no "
                     f"rise to detect"))
    tol = int(0.004 * fx.sr)
    if fx.kind == "filtered_noise":
        if not got:
            return _bad("found no onset where the record steps from digital "
                        "silence into a noise band")
        off = abs(got[0] - want[0])
        return (_ok(f"first onset {off/fx.sr*1e3:.2f} ms off the known step "
                    f"into the band; {len(got)-1} later onset(s) reported, "
                    f"NOT gated -- a noise envelope's 12 dB excursions are "
                    f"draws, not hits")
                if off <= tol else
                _bad(f"first onset {off/fx.sr*1e3:.2f} ms off the known step"))
    if len(got) != len(want):
        return _bad(f"found {len(got)} onsets, record holds {len(want)} "
                    f"({[round(g/fx.sr*1e3, 1) for g in got[:6]]} ms vs "
                    f"{[round(w/fx.sr*1e3, 1) for w in want[:6]]} ms)")
    off = [abs(g - w) for g, w in zip(got, want)]
    return (_ok(f"{len(got)} onsets, worst {max(off)/fx.sr*1e3:.2f} ms off")
            if max(off) <= tol else
            _bad(f"worst onset {max(off)/fx.sr*1e3:.2f} ms off a known time"))


def c_envelope_bursts(fx):
    want = len(fx.truth.get("onsets") or [])
    if fx.kind == "repeated_hits":
        want = fx.truth["n_hits"]
    elif fx.kind == "harmonic_mixture":
        return _nm("a stationary record's envelope has no peak-and-dip "
                   "structure, so the number of BURSTS in it is zero by "
                   "construction and says nothing about the estimator")
    elif fx.kind == "filtered_noise":
        return _nm("a noise envelope's peaks are a draw from a distribution, "
                   "not a known count")
    elif fx.kind == "two_modes":
        return _nm("a BEATING pair's envelope peaks once per beat by "
                   "construction, so its envelope-peak count is the beat "
                   "count and not the re-strike count this estimator is for. "
                   "The beating case is ground-truthed through decay_tau's "
                   "ambiguity rule instead")
    elif fx.kind == "transient_then_ring":
        return _nm("a two-stage envelope over a glide has a shoulder, not a "
                   "second strike")
    elif fx.kind == "damped_sine" and fx.truth["f"] * fx.truth["tau"] < 3.0:
        return _nm(f"at {fx.truth['f']*fx.truth['tau']:.2f} carrier cycles "
                   f"per tau the short-time envelope ripples at the carrier, "
                   f"so its peak count is the carrier's and not the strike's")
    if fx.kind == "repeated_hits":
        if not clean(fx):
            return _nm("the quietest hit of an unequal-level record can sit "
                       "under the noise floor, so the burst COUNT is not "
                       "known here")
        # THE CLOSED FORM IS THE ESTIMATOR'S OWN CONTRACT: a peak counts when
        # it reaches `level_frac` (0.4) of the envelope peak. With levels
        # 1/0.5/0.25/0.7 the 0.25 hit is BELOW that and must not be counted,
        # so the known answer is 3, not 4. The first draft of this check
        # expected 4 and was wrong about the estimator, not the reverse.
        lv = list(fx.truth["levels"])[:fx.truth["n_hits"]]
        want = sum(1 for v in lv if v >= 0.4 * max(lv))
    win = max(3.0, 2000.0 / fx.truth["f"]) if fx.truth.get("f") else 3.0
    env = am.rms_envelope(fx.x, win, fx.sr)
    got = am.envelope_bursts(env, fx.sr)
    if len(got) != want:
        return _bad(f"{len(got)} bursts, record holds {want} at or above 0.4 "
                    f"of its own peak (found at "
                    f"{[round(t*1e3, 1) for t, _l in got[:6]]} ms)")
    return _ok(f"{len(got)} burst(s) == the known count of hits at or above "
               f"0.4 of the record's own peak")


def c_decay_tau(fx):
    if fx.truth.get("stationary"):
        e = am.decay_tau(fx.x, fx.sr)
        return (_ref(f"stationary record: {e.reason}") if not e.ok
                else _bad(f"reported {e.value*1e3:.2f} ms of decay on a "
                          f"stationary record"))
    taus = fx.truth.get("taus") or []
    if not taus:
        return _nm("this family's envelope is not an exponential, so there is "
                   "no tau to be right about")
    e = am.decay_tau(fx.x, fx.sr)
    if fx.truth.get("ambiguous_decay"):
        if not e.ok:
            return (INSUFFICIENT, f"refused: {e.reason}")
        return ambiguity(fx, e.value, detail=e.detail)
    want = taus[0]
    if not e.ok:
        if _ds_inside_decay_tau_domain(fx):
            return _bad(f"refused inside its own domain: {e.reason}")
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.12)
            else _bad(_err(e.value, want)))


def _ds_inside_decay_tau_domain(fx) -> bool:
    """Is this record unambiguously inside `decay_tau`'s declared domain, so
    that a refusal is a failure rather than a result?

    The three conditions are the estimator's own: a decaying record, at least
    `DECAY_TAU_MIN_CYCLES_PER_TAU` carrier cycles per tau, and enough record
    to hold `min_range_db` of fall -- plus a clean one, because `decay_tau`
    has no SNR gate and the suite measures where noise starts to bite rather
    than asserting it does not."""
    t = fx.truth
    tau, f, dur = t.get("tau"), t.get("f"), t.get("dur")
    if None in (tau, f, dur) or not clean(fx):
        return False
    return (f * tau >= am.DECAY_TAU_MIN_CYCLES_PER_TAU
            and dur / tau >= 12.0 / (20.0 / math.log(10)) * 1.5
            and t.get("shape", "exp") == "exp")


def c_decay_tau_windowed(fx):
    """The other half of criterion 4: given an EXPLICIT interval, the same
    estimator must answer, and answer with that interval's own tau."""
    if fx.kind == "transient_then_ring":
        a, b, want = fx.truth["ring_from_s"], fx.truth["ring_to_s"], fx.truth["tau_ring"]
    elif fx.kind == "repeated_hits":
        a, b = fx.truth["hit_windows_s"][0]
        want = fx.truth["tau"]
    else:
        return _nm("this family has no second interval to ask about")
    e = am.decay_tau(fx.x, fx.sr, start_s=a, end_s=b)
    if not e.ok:
        return _ref(f"window [{a*1e3:.0f}, {b*1e3:.0f}] ms: {e.reason}")
    return (_ok(f"window [{a*1e3:.0f}, {b*1e3:.0f}] ms: {_err(e.value, want)}")
            if near(e.value, want, 0.25) else
            _bad(f"window [{a*1e3:.0f}, {b*1e3:.0f}] ms: {_err(e.value, want)}"))


def c_schroeder_t20(fx):
    if fx.truth.get("stationary"):
        e = am.schroeder_t20(fx.x, fx.sr)
        return (_ref(f"stationary record: {e.reason}") if not e.ok
                else _bad(f"reported a {e.value*1e3:.1f} ms T20 for a "
                          f"stationary record"))
    want = fx.truth.get("t20")
    if want is None:
        return _nm("no single exponential, so no closed-form T20")
    e = am.schroeder_t20(fx.x, fx.sr)
    if not e.ok:
        return _ref(e.reason)
    if fx.truth.get("ambiguous_decay"):
        return ambiguity(fx, e.value / math.log(10.0), detail=e.detail)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.15)
            else _bad(_err(e.value, want)))


def c_damped_sinusoid(fx):
    # The RING, not the record: a trailing region of digital silence adds rows
    # of zeros to the least-squares fit and pulled the frequency of a 220 Hz /
    # 300 ms ring to 199.0 Hz. `test_audio_measure.py` hands it `ir[8:]` for
    # the same reason.
    d = am.damped_sinusoid(fx.x[carrier_slice(fx, max_taus=6.0)], fx.sr)
    f_want = fx.truth.get("f") or fx.truth.get("dominant_f") or fx.truth.get("f_end")
    notes = []
    if d.freq.ok and f_want:
        if not near(d.freq.value, f_want, 0.03):
            if not fx.truth.get("ambiguous_decay") and fx.kind == "damped_sine":
                return _bad(f"freq {_err(d.freq.value, f_want)}")
        notes.append(f"freq {d.freq.value:.1f} Hz (known {f_want:.1f})")
    if not d.tau.ok:
        return (INSUFFICIENT if fx.truth.get("ambiguous_decay") else REFUSED,
                f"tau refused: {d.tau.reason}" + ("; " + "; ".join(notes) if notes else ""))
    if fx.truth.get("ambiguous_decay"):
        sl = carrier_slice(fx, max_taus=6.0)
        one_mode = fx.kind == "repeated_hits"   # the slice IS one hit
        return ambiguity(fx, d.tau.value, detail=d.tau.detail,
                         companion_freq=d.freq.value if d.freq.ok else None,
                         interval=(f"[{sl.start/fx.sr*1e3:.0f}, "
                                   f"{sl.stop/fx.sr*1e3:.0f}] ms, the first "
                                   f"hit's own window" if one_mode else None))
    taus = fx.truth.get("taus") or []
    if not taus:
        return _nm("no closed-form tau for this family")
    return (_ok(f"{_err(d.tau.value, taus[0])}; " + "; ".join(notes))
            if near(d.tau.value, taus[0], 0.12)
            else _bad(f"{_err(d.tau.value, taus[0])}; " + "; ".join(notes)))


def c_zero_crossing_frequency(fx):
    f = fx.truth.get("f")
    if f is None:
        return _nm("no single known carrier frequency in this family")
    sl = carrier_slice(fx, max_taus=4.0)
    seg = fx.x[sl]
    if len(seg) / fx.sr * f < 6:
        return _nm(f"fewer than the six upward crossings the estimator "
                   f"requires inside the {len(seg)/fx.sr*1e3:.0f} ms the "
                   f"carrier is still the signal")
    if fx.kind == "repeated_hits" and len(seg) / fx.sr * f < 12:
        return _nm("fewer than twelve crossings inside one hit")
    if fx.truth.get("f") and fx.truth.get("tau") and fx.truth["f"] * fx.truth["tau"] < 3.0:
        return _nm(f"at {fx.truth['f']*fx.truth['tau']:.2f} carrier cycles per "
                   f"tau the ring is over in a handful of crossings and the "
                   f"first-to-last span is dominated by where the last one "
                   f"lands: measured -3.2 % at 1.68 cycles per tau")
    e = am.zero_crossing_frequency(seg, fx.sr)
    if not e.ok:
        return _ref(e.reason)
    if not clean(fx):
        # ONE-SIDED BY CONSTRUCTION, which is why it is gated one-sided: a
        # white noise floor adds crossings AT the signal's own zeros, where
        # the local SNR is 0 dB however loud the record is, so the count can
        # only go up. Measured on the nominal 220 Hz ring: 220.0 Hz clean,
        # 289.8 Hz at 40 dB, 511.3 Hz at 20 dB. The bound is the direction,
        # and the magnitude is reported as this estimator's SNR boundary.
        return (_ok(f"{_err(e.value, f)} at {fx.truth['snr_db']:g} dB SNR -- "
                    f"noise can only ADD crossings, and it did not subtract")
                if e.value >= 0.97 * f else
                _bad(f"{_err(e.value, f)}: a noise floor cannot REMOVE "
                     f"crossings, so a reading below the known carrier is not "
                     f"a noise effect"))
    return (_ok(f"{_err(e.value, f)} over the "
                f"{len(seg)/fx.sr*1e3:.0f} ms the carrier is above its floor")
            if near(e.value, f, 0.03) else _bad(_err(e.value, f)))


def _line_band(f):
    return (f * 0.6, f * 1.6)


def c_dominant_frequency(fx):
    if fx.kind == "filtered_noise":
        if fx.truth["kind"] != "resonant":
            e = am.dominant_frequency(fx.x, 20.0, 0.45 * fx.sr, fx.sr)
            return (_ref(f"no line in a butterworth-shaped noise band: {e.reason}")
                    if not e.ok else
                    _ok(f"found {e.value:.0f} Hz; reported, not gated -- a "
                        f"noise band's strongest bin is a draw"))
        want = fx.truth["f_peak"]
        e = am.dominant_frequency(fx.x, want * 0.5, want * 2.0, fx.sr)
        if not e.ok:
            return _ref(e.reason)
        return (_ok(_err(e.value, want)) if near(e.value, want, 0.10)
                else _bad(_err(e.value, want)))
    want = (fx.truth.get("f") or fx.truth.get("f0") or fx.truth.get("f_end")
            or fx.truth.get("dominant_f"))
    if want is None:
        return _nm("no single known line in this family")
    e = am.dominant_frequency(fx.x, *_line_band(want), fx.sr)
    if not e.ok:
        return _ref(e.reason)
    tol = 0.02
    if fx.kind == "transient_then_ring":
        tol = 0.08                      # the glide puts energy below f_end
    elif fx.kind == "repeated_hits":
        tol = 0.04                      # several hits at unequal phase smear
                                        # the line over neighbouring bins
    return (_ok(_err(e.value, want)) if near(e.value, want, tol)
            else _bad(_err(e.value, want)))


def c_line_at(fx):
    want = fx.truth.get("f") or fx.truth.get("f0") or fx.truth.get("dominant_f")
    if want is None:
        return _nm("no single known line in this family")
    e = am.line_at(fx.x, want, fx.sr)
    if not e.ok:
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.02)
            else _bad(_err(e.value, want)))


def c_instantaneous_frequency(fx):
    if fx.kind == "transient_then_ring":
        if not clean(fx):
            return _nm("the known trajectory is the NOISELESS one; a phase "
                       "derivative under a noise floor departs from it by an "
                       "amount set by the floor, which is the subject of "
                       "glide_law's own precondition rather than a bound here")
        inst = fx.truth["inst_hz"]
        got = am.instantaneous_frequency(fx.x, fx.sr, smooth_ms=2.0)
        i0, i1 = fx.truth["i0"], fx.truth["i1"]
        pad = int(0.010 * fx.sr)
        g = got[i0 + pad:i1 - pad]
        w = inst[pad:i1 - i0 - pad]
        m = min(len(g), len(w))
        if m < 100:
            return _nm("record too short to compare trajectories away from "
                       "the edges")
        err = float(np.percentile(np.abs(g[:m] / w[:m] - 1.0), 98))
        return (_ok(f"98th-percentile departure from the known trajectory "
                    f"{100*err:.2f} %") if err < 0.06 else
                _bad(f"98th-percentile departure from the known trajectory "
                     f"{100*err:.2f} %"))
    f = fx.truth.get("f")
    if f is None:
        return _nm("no single known carrier frequency")
    sl = carrier_slice(fx, max_taus=4.0)
    pad = int(0.004 * fx.sr)
    seg = fx.x[sl.start + pad:sl.stop - pad]
    if len(seg) < 200 or len(seg) / fx.sr * f < 8:
        return _nm("too few carrier cycles above the floor to read a "
                   "trajectory away from the edges")
    got = float(np.median(am.instantaneous_frequency(seg, fx.sr, smooth_ms=1.0)))
    return (_ok(f"{_err(got, f)} (median over the "
                f"{len(seg)/fx.sr*1e3:.0f} ms the carrier is above its floor)")
            if near(got, f, 0.03) else _bad(_err(got, f)))


def _env_times(fx):
    tau = fx.truth["tau"]
    return [t for t in (0.25 * tau, 0.5 * tau, 1.0 * tau, 2.0 * tau)
            if t < 0.8 * fx.truth["dur"]]


def c_analytic_envelope(fx):
    if fx.kind == "damped_sine":
        amp, tau = fx.truth["amp"], fx.truth["tau"]
        if fx.truth["f"] * tau < 3.0:
            return _nm(f"at {fx.truth['f']*tau:.2f} carrier cycles per tau "
                       f"the analytic envelope ripples AT the carrier (12 % "
                       f"at 1.68 cycles, 20 % at 0.66, both measured), so "
                       f"'exactly A0 exp(-t/tau)' is not the closed form "
                       f"there -- which is the same boundary decay_tau's own "
                       f"DECAY_TAU_MIN_CYCLES_PER_TAU encodes")
        env = am.analytic_envelope(fx.x)
        i0 = fx.truth["i0"]
        worst, where = 0.0, None
        for t in _env_times(fx):
            k = i0 + int(t * fx.sr)
            want = amp * math.exp(-t / tau)
            if want < 10.0 * max(fx.truth["noise_rms"], 1e-18):
                continue
            e = abs(env[k] / want - 1.0)
            if e > worst:
                worst, where = e, t
        if where is None:
            return _nm("the known envelope never clears the noise floor by 20 dB")
        return (_ok(f"worst departure from amp*exp(-t/tau) {100*worst:.2f} % "
                    f"(at {where*1e3:.1f} ms)") if worst < 0.10
                else _bad(f"worst departure {100*worst:.2f} % at {where*1e3:.1f} ms"))
    if fx.kind == "envelope_plus_noise":
        env = am.analytic_envelope(fx.x)
        known = fx.truth["env"]
        i0 = fx.truth["i0"]
        sel = known > 20.0 * max(fx.truth["noise_rms"], 1e-18)
        pad = int(0.004 * fx.sr)
        sel[:pad] = False
        sel[-pad:] = False
        if sel.sum() < 200:
            return _nm("the known envelope never clears the noise floor by "
                       "26 dB")
        g = env[i0:i0 + len(known)][sel]
        w = known[sel]
        worst = float(np.percentile(np.abs(g / w - 1.0), 95))
        return (_ok(f"95th-percentile departure from the known envelope "
                    f"{100*worst:.2f} %") if worst < 0.12
                else _bad(f"95th-percentile departure {100*worst:.2f} %"))
    return _nm("no closed-form envelope for this family")


def c_rms_envelope(fx):
    if fx.kind != "envelope_plus_noise":
        return _nm("checked on the family that HAS a known envelope")
    env = am.rms_envelope(fx.x, 4.0, fx.sr)
    known = fx.truth["env"]
    i0 = fx.truth["i0"]
    sel = known > 20.0 * max(fx.truth["noise_rms"], 1e-18)
    pad = int(0.008 * fx.sr)
    sel[:pad] = False
    sel[-pad:] = False
    if sel.sum() < 200:
        return _nm("the known envelope never clears the noise floor by 26 dB")
    g = env[i0:i0 + len(known)][sel]
    w = known[sel]
    worst = float(np.percentile(np.abs(g / w - 1.0), 95))
    return (_ok(f"95th-percentile departure from the known envelope "
                f"{100*worst:.2f} % (a 4 ms window smooths a curved envelope, "
                f"which is the bound, not an error)")
            if worst < 0.15 else _bad(f"95th-percentile departure {100*worst:.2f} %"))


def c_average_envelope(fx):
    if fx.kind != "envelope_plus_noise":
        return _nm("checked on the family that HAS a known envelope")
    draws = []
    for s in (1, 2, 3, 4, 5):
        y, _ = ef.add_noise(fx.x, fx.truth["snr_db"], 9000 + s,
                            over=slice(fx.truth["i0"], fx.truth["i1"]))
        draws.append(y)
    env = am.average_envelope(draws, "rms", 4.0, fx.sr)
    known = fx.truth["env"]
    i0 = fx.truth["i0"]
    sel = known > 20.0 * max(fx.truth["noise_rms"], 1e-18)
    pad = int(0.008 * fx.sr)
    sel[:pad] = False
    sel[-pad:] = False
    if sel.sum() < 200:
        return _nm("the known envelope never clears the noise floor by 26 dB")
    g = env[i0:i0 + len(known)][sel]
    w = known[sel]
    worst = float(np.percentile(np.abs(g / w - 1.0), 95))
    return (_ok(f"five differently-seeded draws average to the known envelope, "
                f"95th-percentile departure {100*worst:.2f} %")
            if worst < 0.15 else _bad(f"95th-percentile departure {100*worst:.2f} %"))


def c_moving_average_envelope(fx):
    return (SKIP, "DEPRECATED by its own docstring -- 'kept only as the "
                  "counter-example ... do not use it to measure anything'. "
                  "Ground-truthing it would assert a bound on a measure the "
                  "module tells callers not to use; the counter-example it "
                  "exists for is in test_audio_measure.py.")


def c_segment_shape(fx):
    if fx.kind != "envelope_plus_noise":
        return _nm("defined on an ENVELOPE SEGMENT; the family with a known "
                   "envelope shape is envelope_plus_noise")
    shape, tau, dur = fx.truth["shape"], fx.truth["tau"], fx.truth["dur"]
    if fx.truth["step_ms"]:
        return _nm("the staircase members are for envelope_ripple_db; their "
                   "midpoint sits on a quantised step, not on the law")
    # closed form of the index for this exact segment, from the law alone
    if shape == "exp":
        a, b, mid = 1.0, math.exp(-dur / tau), math.exp(-dur / (2 * tau))
    elif shape == "linear":
        a, b, mid = 1.0, 0.0, 0.5
    else:
        a, b, mid = 0.0, 1 - math.exp(-4.0), 1 - math.exp(-2.0)
    want = (mid - a) / (b - a) - 0.5
    e = am.segment_shape(fx.truth["env"], fx.sr)
    if not e.ok:
        return _bad(f"refused the known {shape} segment: {e.reason}")
    return (_ok(f"{shape}: {_err(e.value, want)}") if near(e.value, want, 0.0, 0.02)
            else _bad(f"{shape}: {_err(e.value, want)}"))


def c_envelope_ripple_db(fx):
    if fx.kind != "envelope_plus_noise":
        return _nm("defined on an envelope; the known-staircase members are "
                   "in envelope_plus_noise")
    want = fx.truth["ripple_db"]
    if want is None:
        if fx.truth["shape"] != "linear":
            # The high-pass is a moving-average subtraction, which leaves
            # EXACTLY zero on a straight line and a closed-form residual on a
            # curve: for exp(-t/tau) and a window W the moving average is
            # (2 tau / W) sinh(W / 2 tau) times the envelope, so the residual
            # is a fixed relative 1 - (2 tau/W) sinh(W/2 tau) -- -30.7 dB for
            # tau = 30 ms and W = 25 ms, measured -26.8 dB. That is the
            # window's curvature term, not stepping, so the smooth control is
            # the LINEAR member and the curved ones are reported.
            e = am.envelope_ripple_db(fx.truth["env"], fx.sr)
            w = 1.0 / 40.0
            tau = fx.truth["tau"]
            model = (1.0 - (2 * tau / w) * math.sinh(w / (2 * tau))
                     if fx.truth["shape"] == "exp" else float("nan"))
            return _nm(f"a {fx.truth['shape']} envelope is CURVED, so the "
                       f"moving-average high-pass leaves its curvature: read "
                       f"{e.value if e.ok else float('nan'):.1f} dB against a "
                       f"relative curvature term of {model:+.4f}. Not "
                       f"stepping; the smooth control is the linear member")
        e = am.envelope_ripple_db(fx.truth["env"], fx.sr)
        if not e.ok:
            return _ref(e.reason)
        return (_ok(f"a straight ramp reads {e.value:.1f} dB -- a moving "
                    f"average subtracted from a straight line is exactly "
                    f"zero, so this is the measure's own floating-point "
                    f"floor, measured at -95 dB (the first draft of this "
                    f"check guessed -100 and was wrong)")
                if e.value < -80.0 else
                _bad(f"{e.value:.1f} dB of 'stepping' on a straight line, "
                     f"where the construction gives exactly zero"))
    e = am.envelope_ripple_db(fx.truth["env"], fx.sr)
    if not e.ok:
        return _bad(f"refused a known staircase: {e.reason}")
    return (_ok(f"{_err(e.value, want)} vs the closed form "
                f"20log10(d/(sqrt(12) m))")
            if near(e.value, want, 0.0, 3.0) else _bad(_err(e.value, want)))


def c_glide_law(fx):
    f = fx.truth.get("f") or fx.truth.get("f0")
    if fx.kind == "transient_then_ring":
        if not clean(fx):
            return _nm("the law is read from a trajectory, and a trajectory "
                       "read under a noise floor is the floor's; the clean "
                       "members carry this check")
        i0 = fx.truth["i0"]
        # THE WINDOW AND ITS EDGES ARE PART OF THE QUESTION, and getting them
        # wrong made this check red on seven fixtures with the estimator doing
        # nothing wrong. `glide_law`'s 'constant-time' model is
        # hi + (lo - hi) exp(-t/tau) with lo and hi FIXED to the trajectory's
        # first and last samples -- so one bad sample at either end anchors
        # the model to the wrong place and the fit's R^2 goes NEGATIVE
        # (-1.04 measured at 330 Hz with no trim). The first 4 ms of an
        # analytic phase derivative is a boundary artefact, which is exactly
        # what `instantaneous_frequency`'s docstring tells callers to mask.
        # Measured after trimming 4 ms and taking five time constants:
        # 'constant-time' wins on every clean member, R^2 0.66 to 0.999.
        pad = int(0.004 * fx.sr)
        # A 'constant-time' glide never arrives, so five time constants is
        # where its curvature is; a 'constant-rate' ramp ARRIVES at glide_s
        # and anything past that is a flat tail an exponential fits better
        # (measured: it lost to 'constant-time' by 0.549 of R^2 over five
        # time constants and won by 0.007 over its own ramp). The window is
        # the law's own duration, which the caller knows and the estimator
        # cannot.
        span = (5.0 if fx.truth["law"] == "constant-time" else 1.0) * fx.truth["glide_s"]
        inst = am.instantaneous_frequency(fx.x, fx.sr, smooth_ms=2.0)
        seg = inst[i0 + pad:i0 + int(span * fx.sr)]
        if len(seg) < 64:
            return _nm("fewer than the 16 usable frequency samples required")
        e = am.glide_law(seg, fx.sr)
        if not e.ok:
            return _ref(f"after trimming the trajectory's 4 ms boundary "
                        f"artefact: {e.reason}")
        if not e.ok:
            return _ref(e.reason)
        want = fx.truth["law"]
        got = e.detail["law"]
        r2 = e.detail["r2"]
        margin = r2[got] - max(v for k, v in r2.items() if k != got)
        if got != want:
            # The docstring's own warning: "a short glide fits every law well
            # and the useful output is the MARGIN between them, not the
            # winner." So a wrong winner is a FAIL only when it won by a
            # margin; inside the margin it is the estimator saying it cannot
            # separate them, which is a result.
            lost_by = r2[got] - r2[want]
            if lost_by <= 0.05:
                return (INSUFFICIENT,
                        f"named '{got}' over the synthesised '{want}' by only "
                        f"{lost_by:.4f} of R2 ({ {k: round(v, 4) for k, v in r2.items()} }) "
                        f"-- inside the margin its own docstring says to read "
                        f"instead of the winner")
            return _bad(f"named '{got}' for a '{want}' glide by {lost_by:.4f} "
                        f"of R2 ({ {k: round(v, 4) for k, v in r2.items()} })")
        return _ok(f"'{got}' R2={e.value:.4f}, margin over the next law "
                   f"{margin:.4f}")
    if f is None:
        return _nm("no known carrier to read a trajectory from")
    sl = carrier_slice(fx, max_taus=4.0)
    seg = fx.x[sl]
    if len(seg) / fx.sr * f < 8:
        return _nm("too few carrier cycles above the floor for a trajectory")
    # `instantaneous_frequency`'s own docstring: "valid only where the
    # envelope is well above the floor; callers should mask". The analytic
    # phase derivative at the two ENDS of a segment is a boundary artefact,
    # and an untrimmed one made f[0]/f[-1] differ by up to 1.2 octaves on a
    # record whose frequency does not move at all -- enough to get past
    # glide_law's own min_ratio and have it name a law. Trimming 4 ms is the
    # mask the docstring asks for.
    pad = int(0.004 * fx.sr)
    inst = am.instantaneous_frequency(seg, fx.sr, smooth_ms=2.0)[pad:-pad]
    if len(inst) < 64:
        return _nm("record too short for a trajectory")
    e = am.glide_law(inst, fx.sr)
    if not e.ok:
        return _ref(f"a record with no glide: {e.reason}")
    # `value` IS the R^2 of the winning fit, and the docstring says to read the
    # margin rather than the winner. On a static record every law fits the
    # jitter equally badly and the R^2 is ~0, which is the estimator saying so.
    # What would be a wrong answer is a HIGH R^2 on a record that does not
    # glide, and that is what is gated.
    #
    # RECORDED, not gated, and worth reading before quoting `glide_law`: its
    # only refusal is `max(f[0], f[-1]) / min(f[0], f[-1]) < min_ratio`, which
    # is TWO SINGLE SAMPLES of a trajectory. Hilbert jitter on a record whose
    # frequency does not move at all gets past it (0.07 to 0.77 octaves
    # measured here, against a 1.05 ratio), so the refusal cannot be relied
    # on; the R^2 can.
    return (_ok(f"named '{e.detail['law']}' at R2 {e.value:.4f} on a record "
                f"whose frequency does not move -- its own R2 says the fit is "
                f"meaningless, which is the answer. (It did NOT refuse: its "
                f"min_ratio gate reads f[0] against f[-1], and Hilbert jitter "
                f"spans {e.detail['octaves']:.4f} octaves across those two "
                f"samples.)")
            if e.value < 0.25 else
            _bad(f"named '{e.detail['law']}' at R2 {e.value:.3f} -- a "
                 f"confident law for a record whose frequency does not move"))


def c_repeat_period(fx):
    if fx.kind == "repeated_hits":
        want = fx.truth["period_s"]
        e = am.repeat_period(fx.x, fx.sr, min_lag_s=0.03)
        if want is None:
            return (_ref(f"irregular spacing: {e.reason}") if not e.ok else
                    _ok(f"reported {e.value*1e3:.1f} ms on an IRREGULARLY "
                        f"spaced record whose gaps are "
                        f"{[round(g*1e3) for g in fx.truth['gaps_s']]} ms -- "
                        f"reported, not gated: an autocorrelation of unequal "
                        f"gaps has a legitimate strongest lag"))
        if not e.ok:
            return _ref(e.reason)
        # A MULTIPLE OF THE GAP, not the gap. These hits are at UNEQUAL
        # LEVELS, so the record does not repeat at one gap -- it repeats at
        # whichever lag the normalised autocorrelation likes best, and 360 ms
        # (three 120 ms gaps, where the loudest hit lines up with the last
        # one) is a correct answer to "the lag at which this signal repeats
        # itself". The first draft of this check demanded 120 ms and was
        # wrong about the record, not about the estimator.
        mult = e.value / want
        return (_ok(f"{e.value*1e3:.1f} ms = {mult:.3f} x the known "
                    f"{want*1e3:g} ms inter-onset gap")
                if abs(mult - round(mult)) <= 0.02 and round(mult) >= 1
                else _bad(f"{e.value*1e3:.2f} ms is {mult:.3f} x the known "
                          f"{want*1e3:g} ms gap -- not a whole number of them"))
    e = am.repeat_period(fx.x, fx.sr)
    if fx.kind == "filtered_noise":
        return (_ref(f"filtered noise does not repeat: {e.reason}")
                if not e.ok else
                _bad(f"reported a {e.value*1e3:.1f} ms repeat on a noise "
                     f"record, which is this estimator's whole purpose to "
                     f"refuse"))
    # EVERY OTHER FAMILY IS QUASI-PERIODIC, and that is the ground truth
    # rather than a gap: a sustained or slowly-decaying carrier at f
    # autocorrelates at EVERY multiple of 1/f, and `min_lag_s` (50 ms by
    # default) decides which multiple is reported. So the known answer is
    # "refuses, or a near-integer number of carrier periods at or past
    # min_lag_s". The first draft of this check called a 50.0 ms reading on a
    # 220 Hz / 100 ms ring "a repeat on a record holding one event"; 50.0 ms
    # is exactly 11 periods of 4.5455 ms, and the estimator was right.
    periods = [1.0 / v for v in (fx.truth.get("f"), fx.truth.get("f0"),
                                 fx.truth.get("f1"), fx.truth.get("f2"),
                                 fx.truth.get("f_end")) if v]
    if not periods:
        return _nm("no known carrier period for this family")
    if not e.ok:
        return _ref(f"a decaying carrier past its floor at the estimator's "
                    f"50 ms minimum lag: {e.reason}")
    if e.value < 0.049:
        return _bad(f"reported {e.value*1e3:.2f} ms, below its own 50 ms "
                    f"minimum lag")
    best = min((abs(e.value / pp - round(e.value / pp)) * pp / e.value, pp)
               for pp in periods)
    if best[0] <= 0.02:
        return _ok(f"{e.value*1e3:.2f} ms = {e.value/best[1]:.3f} x the "
                   f"{best[1]*1e3:.4f} ms carrier period, to "
                   f"{100*best[0]:.2f} %")
    return _bad(f"reported {e.value*1e3:.2f} ms, which is not a whole number "
                f"of any known carrier period "
                f"({[round(e.value/pp, 3) for pp in periods]})")


def c_tonality_db(fx):
    if fx.kind == "filtered_noise":
        if fx.truth["kind"] != "lowpass":
            return _nm("the closed form below needs a FLAT band; a highpass "
                       "or resonant response has none wide enough")
        # THE BAND IS THE MEASUREMENT. Over (20 Hz, 0.45 fs) a 4th-order
        # lowpass reads 92.5 dB of "tonality" -- because the MEDIAN bin of
        # that band sits 90 dB down in the stopband while the peak sits in
        # the passband, so peak-over-median returns the filter's dynamic
        # range. Inside the flat passband the closed form applies: the
        # largest of N exponential periodogram bins is about ln(N) times
        # their median, i.e. 10 log10(ln N / ln 2) dB. First draft of this
        # check used the full band and was wrong by 70 dB.
        band = (fx.truth["fc"] * 0.1, fx.truth["fc"] * 0.5)
        if band[0] < 20.0:
            return _nm("the flat part of the passband falls below 20 Hz")
        got = am.tonality_db(fx.x, band, fx.sr)
        nb = max(4, int((band[1] - band[0]) / (fx.sr / fx.n)))
        want = 10.0 * math.log10(math.log(nb) / math.log(2.0))
        return (_ok(f"{_err(got, want)} vs the closed-form max-over-median of "
                    f"{nb} exponential periodogram bins in the flat passband")
                if near(got, want, 0.0, 6.0) else _bad(_err(got, want)))
    got = am.tonality_db(fx.x, (20.0, 0.45 * fx.sr), fx.sr)
    return (_ok(f"{got:.1f} dB, above the 20 dB a record dominated by known "
                f"lines must show") if got > 20.0
            else _bad(f"{got:.1f} dB on a record whose energy is in known lines"))


def c_spectral_flatness(fx):
    if fx.kind == "filtered_noise":
        if fx.truth["kind"] != "lowpass":
            return _nm("the closed form below is for a FLAT band; a highpass "
                       "or resonant response is not flat inside any band wide "
                       "enough to measure")
        band = (fx.truth["fc"] * 0.1, fx.truth["fc"] * 0.5)
        got = am.spectral_flatness(fx.x, band, fx.sr)
        # the geometric over arithmetic mean of an exponential periodogram is
        # exp(-gamma) = 0.5615; a Hann window correlates neighbouring bins and
        # raises it, which is why the bound is one-sided and wide
        return (_ok(f"{got:.3f} in the flat part of the passband, at or above "
                    f"exp(-gamma)=0.5615 for an uncorrelated periodogram")
                if got >= 0.45 else
                _bad(f"{got:.3f} in a flat noise band, below the 0.45 a "
                     f"windowed periodogram of white noise reads"))
    if fx.kind == "harmonic_mixture" and fx.truth["band_limited"]:
        band = (2000.0, 20000.0)
        got = am.spectral_flatness(fx.x, band, fx.sr)
        return _ok(f"{got:.3f} on a dense known comb -- reported, NOT gated: "
                   f"this estimator's own docstring exists to record that it "
                   f"does not separate a comb from noise")
    return _nm("no closed-form flatness for this family")


def c_spectral_centroid(fx):
    if fx.kind == "harmonic_mixture":
        want = fx.truth["centroid"]
        if want is None:
            return _nm("the naive members alias, so their partial set -- and "
                       "so their centroid -- is not the synthesis series")
        if not clean(fx):
            return _nm("a white noise floor puts energy at every frequency in "
                       "the band, so the centroid of the RECORD is not the "
                       "centroid of its line set (measured +15.6 % at 20 dB "
                       "SNR, which is the floor's own centroid pulling)")
        got = am.spectral_centroid(fx.x, (20.0, 0.49 * fx.sr), fx.sr)
        return (_ok(_err(got, want)) if near(got, want, 0.05)
                else _bad(_err(got, want)))
    if fx.kind == "filtered_noise":
        f, mag_db = fx.truth["ref_freqs"], fx.truth["ref_mag_db"]
        p = 10 ** (mag_db / 10.0)
        lo, hi = 20.0, 0.45 * fx.sr
        sel = (f >= lo) & (f <= hi)
        want = float(_TRAPZ(f[sel] * p[sel], f[sel])
                     / _TRAPZ(p[sel], f[sel]))
        got = am.spectral_centroid(fx.x, (lo, hi), fx.sr)
        return (_ok(f"{_err(got, want)} vs the centroid of the filter's own "
                    f"|H|^2") if near(got, want, 0.12)
                else _bad(_err(got, want)))
    return _nm("the centroid of a single-line record is that line, which "
               "dominant_frequency checks exactly; a centroid's own ground "
               "truth needs a KNOWN power distribution over frequency, which "
               "harmonic_mixture and filtered_noise have and this family "
               "does not")


def c_spectral_lines(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("needs a KNOWN number of lines in the band; the "
                   "band-limited harmonic mixtures have one")
    lo, hi = 2000.0, 12000.0
    amps = fx.truth["amps"]
    f0 = fx.truth["f0"]
    want = sum(1 for k, a in amps.items() if a > 0 and lo <= k * f0 <= hi)
    if want < 5:
        return _nm(f"only {want} known lines in [2, 12] kHz, below the five "
                   f"this estimator needs")
    st = am.spectral_lines(fx.x, (lo, hi), fx.sr)
    got = st.count
    return (_ok(f"{got} lines found, {want} planted")
            if abs(got - want) <= max(1, int(0.15 * want))
            else _bad(f"{got} lines found, {want} planted"))


def c_line_stability(fx):
    if fx.kind == "harmonic_mixture" and fx.truth["band_limited"]:
        e = am.line_stability(fx.x, (2000.0, 12000.0), fx.sr)
        if not e.ok:
            return _ref(e.reason)
        return (_ok(f"{e.value:.3f} of the lines of a STATIONARY known comb "
                    f"reappear in every window") if e.value > 0.9
                else _bad(f"only {e.value:.3f} of a stationary comb's lines "
                          f"reappear"))
    if fx.kind == "transient_then_ring":
        e = am.line_stability(fx.x, (fx.truth["f_end"] * 0.5,
                                     fx.truth["f_end"] * 8.0), fx.sr)
        if not e.ok:
            return _ref(e.reason)
        return _ok(f"{e.value:.3f} on a GLIDING record (reported: the "
                   f"contrast with the stationary comb is the ground truth)")
    return _nm("needs five or more known lines in one band")


def c_harmonic_powers(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("needs known partial amplitudes; the band-limited "
                   "harmonic mixtures have them")
    amps, f0 = fx.truth["amps"], fx.truth["f0"]
    ks = [k for k in range(1, 10) if amps.get(k, 0) > 0 and k * f0 < 0.45 * fx.sr]
    if len(ks) < 3:
        return _nm("fewer than three known partials below 0.45 fs")
    p = am.harmonic_powers(fx.x, f0, ks, fx.sr)
    worst, where = 0.0, None
    for i, k in enumerate(ks[1:], start=1):
        want = 20.0 * math.log10(amps[k] / amps[ks[0]])
        got = 10.0 * math.log10(max(p[i], 1e-300) / max(p[0], 1e-300))
        if abs(got - want) > worst:
            worst, where = abs(got - want), k
    return (_ok(f"worst departure from the planted series {worst:.3f} dB "
                f"(at k={where})") if worst < 0.6
            else _bad(f"worst departure {worst:.3f} dB at k={where}"))


def c_harmonic_signature(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("needs known partial amplitudes")
    amps, f0 = fx.truth["amps"], fx.truth["f0"]
    if fx.truth["kind"] == "sine":
        return _nm("a single partial has no h2..hk to be right about")
    sig = am.harmonic_signature(fx.x, fx.sr, f0=f0)
    if not sig.get("ok", True):
        return _ref(str(sig.get("reason")))
    worst, where = 0.0, None
    for k in range(2, 10):
        a = amps.get(k, 0.0)
        if a <= 0 or k * f0 >= 0.45 * fx.sr:
            continue
        got = sig.get(f"h{k}")
        if got is None or not np.isfinite(got):
            continue
        want = 20.0 * math.log10(a / amps[1])
        if abs(got - want) > worst:
            worst, where = abs(got - want), k
    if where is None:
        return _nm("no harmonic cleared the measured floor")
    return (_ok(f"worst departure from the planted series {worst:.3f} dB "
                f"(at h{where})") if worst < 1.0
            else _bad(f"worst departure {worst:.3f} dB at h{where}"))


def c_inharmonic_fraction_db(fx):
    if fx.kind != "harmonic_mixture":
        return _nm("defined against a known f0 and its harmonics")
    f0 = fx.truth["f0"]
    want = fx.truth["inharmonic_share_db"]
    e = am.inharmonic_fraction_db(fx.x, f0, fx.sr)
    if want is None:
        if not e.ok:
            return _ref(e.reason)
        if fx.truth["band_limited"] and not clean(fx):
            # a white noise floor IS inharmonic energy, and its share is
            # known: sigma^2 / (sigma^2 + signal^2). Measured -20.77 dB at
            # 20 dB SNR against a closed form of -20.04. The first draft of
            # this check called that "inharmonic energy in a purely harmonic
            # record" and was wrong about which record it had.
            nr = fx.truth["noise_rms"]
            sg = fx.truth["rms_signal"]
            if not sg:
                return _nm("no closed-form signal RMS for this member")
            share = 10.0 * math.log10(nr ** 2 / (nr ** 2 + sg ** 2))
            return (_ok(f"{_err(e.value, share)} against the known share of "
                        f"the record that IS the noise floor")
                    if near(e.value, share, 0.0, 2.0)
                    else _bad(_err(e.value, share)))
        if fx.truth["band_limited"]:
            return (_ok(f"{e.value:.2f} dB on a record whose energy is all at "
                        f"harmonics of f0 -- its own leakage floor")
                    if e.value < -30.0 else
                    _bad(f"{e.value:.2f} dB of inharmonic energy in a purely "
                         f"harmonic record"))
        return _ok(f"{e.value:.2f} dB on a NAIVE (aliasing) shape -- reported: "
                   f"the aliases are inharmonic by construction and their "
                   f"total is not a closed form")
    if not e.ok:
        return _bad(f"refused a record with a planted {want:g} dB share: {e.reason}")
    return (_ok(f"{_err(e.value, want)} against the planted share")
            if near(e.value, want, 0.0, 1.5) else _bad(_err(e.value, want)))


def c_foldback_alias_db(fx):
    if fx.kind != "harmonic_mixture":
        return _nm("defined against the predicted images of a known f0's "
                   "harmonics above Nyquist")
    f0 = fx.truth["f0"]
    e = am.foldback_alias_db(fx.x, f0, fx.sr)
    if not e.ok:
        return _ref(e.reason)
    if fx.truth["band_limited"]:
        return (_ok(f"{e.value:.1f} dB on a BAND-LIMITED series, which has no "
                    f"partial above Nyquist to fold") if e.value < -40.0
                else _bad(f"{e.value:.1f} dB of foldback in a series truncated "
                          f"below Nyquist"))
    return (_ok(f"{e.value:.1f} dB on the NAIVE shape at the same f0, which "
                f"does alias -- the band-limited/naive contrast is the ground "
                f"truth") if e.value > -40.0
            else _bad(f"{e.value:.1f} dB: no foldback found in a naive "
                      f"{fx.truth['kind']} at {f0:g} Hz, which aliases by "
                      f"construction"))


def c_refine_f0(fx):
    if fx.kind != "harmonic_mixture":
        return _nm("refuses a record more than 50 cents from the commanded "
                   "f0, so it is defined on a STATIONARY record at a known f0")
    f0 = fx.truth["f0"]
    e = am.refine_f0(fx.x, f0, fx.sr)
    if not e.ok:
        return _ref(e.reason)
    cents = 1200.0 * math.log2(e.value / f0)
    return (_ok(f"{cents:+.3f} cents from the synthesis f0")
            if abs(cents) < 3.0 else _bad(f"{cents:+.3f} cents from the "
                                          f"synthesis f0"))


def _fundamental_amp(fx):
    amps = fx.truth.get("amps")
    if not amps:
        return None
    return fx.truth["amp"] * amps.get(1, 0.0)


def c_tone_amplitude(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("a coherent projection reads the tone's amplitude only on "
                   "a STATIONARY record; on a decaying one it reads the "
                   "window average of the envelope, which is what decay_tau "
                   "and damped_sinusoid are for")
    want = _fundamental_amp(fx)
    if not want:
        return _nm("no fundamental in this member")
    e = am.tone_amplitude(fx.x, fx.truth["f0"], fx.sr)
    if not e.ok:
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.03)
            else _bad(_err(e.value, want)))


def c_windowed_tone_amplitude(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("same precondition as tone_amplitude: a stationary record")
    want = _fundamental_amp(fx)
    if not want:
        return _nm("no fundamental in this member")
    e = am.windowed_tone_amplitude(fx.x, fx.truth["f0"], fx.sr)
    if not e.ok:
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.03)
            else _bad(_err(e.value, want)))


def _cycle(fx):
    return am.cycle_average(fx.x, fx.truth["f0"], fx.sr)


def c_cycle_average(fx):
    if fx.kind != "harmonic_mixture" or fx.truth["band_limited"]:
        return _nm("checked on the NAIVE shapes, whose one period is an exact "
                   "analytic function of phase and so can be compared point "
                   "by point")
    cyc, resid = _cycle(fx)
    n = len(cyc)
    want = fx.truth["amp"] * ef._naive(fx.truth["kind"], 1.0, n, n,
                                       fx.truth["duty"], fx.truth["phase"])
    # a cycle average is phase-referenced to its own start: align by the
    # circular shift that minimises the difference, which is a property of the
    # comparison and not a freedom in the answer
    best = min(float(np.sqrt(np.mean((np.roll(cyc, s) - want) ** 2)))
               for s in range(0, n, max(1, n // 256)))
    rel = best / max(float(np.sqrt(np.mean(want ** 2))), 1e-30)
    return (_ok(f"one period matches the analytic {fx.truth['kind']} to "
                f"{100*rel:.2f} % RMS, per-period residual {resid:.4f}")
            if rel < 0.10 else
            _bad(f"one period departs from the analytic {fx.truth['kind']} by "
                 f"{100*rel:.2f} % RMS"))


def c_duty_cycle(fx):
    if (fx.kind != "harmonic_mixture" or fx.truth["band_limited"]
            or fx.truth["kind"] != "rect"):
        return _nm("defined on one period of a RECTANGLE; the naive rect "
                   "members are the ones with a known duty")
    cyc, _ = _cycle(fx)
    got = am.duty_cycle(cyc)
    want = fx.truth["duty"]
    return (_ok(_err(got, want)) if near(got, want, 0.0, 0.02)
            else _bad(_err(got, want)))


def c_pulse_edges(fx):
    if (fx.kind != "harmonic_mixture" or fx.truth["band_limited"]
            or fx.truth["kind"] != "rect"):
        return _nm("defined on one period of a rectangle")
    cyc, _ = _cycle(fx)
    got = am.pulse_edges(cyc)
    signs = sorted(s for _i, s in got)
    return (_ok(f"{len(got)} edges, one rising and one falling")
            if len(got) == 2 and signs == [-1, 1]
            else _bad(f"{len(got)} edges {got} on one period of a rectangle"))


def c_rectangularity(fx):
    if fx.kind != "harmonic_mixture" or fx.truth["rectangularity"] is None:
        return _nm("the closed form is for the NAIVE shapes, where every "
                   "sample sits exactly on the analytic waveform")
    want = fx.truth["rectangularity"]
    ideal = fx.truth["ideal_cycle"]
    got = am.rectangularity(ideal)
    via = am.rectangularity(_cycle(fx)[0])
    return (_ok(f"{_err(got, want)} on the ideal period; through "
                f"cycle_average's resampling it reads {via:.3f}, which for a "
                f"saw is the 0.42 the docstring quotes -- the resampling of a "
                f"discontinuity, not the estimator")
            if near(got, want, 0.0, 0.01) else _bad(_err(got, want)))


def c_midpoint_crossings(fx):
    if fx.kind != "harmonic_mixture" or fx.truth["band_limited"]:
        return _nm("exactly 2 for ONE period of a single-valued waveform; the "
                   "naive members are one exact period")
    cyc, _ = _cycle(fx)
    got = am.midpoint_crossings(cyc)
    want = fx.truth["midpoint_crossings"]
    return (_ok(f"{got} == the 2 any single-valued period must give")
            if got == want else _bad(f"got {got} want {want}"))


def c_step_ratio(fx):
    if fx.kind != "harmonic_mixture" or fx.truth["step_ratio"] is None:
        return _nm("the closed forms (N/2 for a naive saw or rectangle, "
                   "N sin(pi/N)/2 -> pi/2 for a sine) are properties of the "
                   "naive shapes")
    if fx.truth["kind"] == "tri":
        return _nm("a naive triangle's mean |step| depends on where the two "
                   "ramps' samples land relative to the apex, so N/4 is an "
                   "approximation rather than a closed form: not gated")
    got = am.step_ratio(sounding(fx))
    want = fx.truth["step_ratio"]
    return (_ok(_err(got, want)) if near(got, want, 0.10)
            else _bad(_err(got, want)))


def c_waveform_id(fx):
    if fx.kind != "harmonic_mixture" or not fx.truth["band_limited"]:
        return _nm("names a waveform from its partial amplitudes; the naive "
                   "members' aliases are not the series it reads")
    f0 = fx.truth["f0"]
    if f0 * 5 > 12000.0:
        return _nm(f"fewer than the five harmonics below 12 kHz it requires "
                   f"at f0={f0:g} Hz")
    if fx.truth["inharmonic_share_db"] is not None:
        return _nm("a planted inharmonic partner is not part of any waveform's "
                   "series; what those members ground-truth is "
                   "inharmonic_fraction_db")
    wid = am.waveform_id(fx.x, f0, fx.sr)
    #: the `requested` name `waveform_matches` takes, from `am.WAVE_EXPECT`
    want = {"saw": "saw", "rect": "square", "tri": "tri",
            "sine": "sine"}[fx.truth["kind"]]
    want_label = {"saw": "saw", "rect": "pulse", "tri": "tri",
                  "sine": "sine"}[fx.truth["kind"]]
    if fx.truth["kind"] == "rect" and fx.truth["duty"] != 0.5:
        want = "wide_rect" if fx.truth["duty"] > 0.5 else "narrow_rect"
    if not wid.ok:
        return _ref(f"declined to name a known {want_label}: {wid.reason}")
    got = (wid.label or "").split(":")[0]
    if got != want_label:
        return _bad(f"named '{wid.label}' for a synthesised "
                    f"'{want_label}' (reason: {wid.reason})")
    if want_label == "pulse":
        d = wid.detail.get("duty")
        wd = fx.truth["duty"]
        if d is None or not (near(d, wd, 0.0, 0.05) or near(d, 1.0 - wd, 0.0, 0.05)):
            return _bad(f"named a pulse of duty {d} for a known {wd:g} "
                        f"(a spectrum cannot tell d from 1-d, and neither "
                        f"bound is met)")
    ok, why = am.waveform_matches(wid, want)
    if not ok:
        return _bad(f"waveform_matches rejected '{wid.label}' against the name "
                    f"'{want}' the rig gave it: {why}")
    return _ok(f"'{wid.label}', and waveform_matches accepts it against "
               f"'{want}'")


def c_band_energy(fx):
    if fx.kind == "filtered_noise":
        f, mag_db = fx.truth["ref_freqs"], fx.truth["ref_mag_db"]
        p = 10 ** (mag_db / 10.0)
        fc = fx.truth["fc"]
        edges = [(20.0, fc), (fc, min(4 * fc, 0.4 * fx.sr))]
        tot = float(_TRAPZ(p, f))
        want = []
        for lo, hi in edges:
            sel = (f >= lo) & (f <= hi)
            want.append(float(_TRAPZ(p[sel], f[sel]) / tot))
        if not clean(fx):
            return _nm("the second, unfiltered noise floor is not part of the "
                       "filter's own |H|^2, so the band fractions of the "
                       "record are not the integral's")
        got = am.band_energy(fx.x, edges, fx.sr)
        worst = max(abs(10 * math.log10(max(g, 1e-12) / max(w, 1e-12)))
                    for g, w in zip(got, want))
        return (_ok(f"worst band fraction {worst:.2f} dB from the integral of "
                    f"the filter's own |H|^2 "
                    f"({[round(float(g), 4) for g in got]} vs "
                    f"{[round(w, 4) for w in want]})")
                if worst < 3.0 else
                _bad(f"worst band fraction {worst:.2f} dB from the analytic "
                     f"integral"))
    f, tau = fx.truth.get("f"), fx.truth.get("tau")
    if f is None or not clean(fx):
        return _nm("needs a single known line in a clean record")
    edges = [(f * 0.5, f * 2.0), (20.0, f * 0.5), (f * 2.0, 0.45 * fx.sr)]
    if edges[0][0] < 20.0 or edges[2][0] > 0.45 * fx.sr:
        return _nm("the known line sits too close to an edge of the band to "
                   "split the record around it")
    if tau is not None and f * tau < 3.0:
        return _nm(f"a ring of {f*tau:.2f} cycles per tau has a -3 dB width of "
                   f"{1.0/(math.pi*tau):.0f} Hz, comparable with the octave "
                   f"band itself, so the fraction INSIDE the band is not ~1 "
                   f"and the closed form would be the Lorentzian's integral, "
                   f"not the line's")
    got = am.band_energy(fx.x, edges, fx.sr)
    return (_ok(f"{100*got[0]:.2f} % of the energy in the octave around the "
                f"known line, {100*got[1]:.3f} % below and {100*got[2]:.3f} % "
                f"above") if got[0] > 0.90
            else _bad(f"only {100*got[0]:.2f} % of the energy in the octave "
                      f"around the only line the record holds"))


def c_psd_slope_db_oct(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a NOISE signal's spectral colour")
    if fx.truth["kind"] == "resonant" or not clean(fx):
        return _nm("the reference slope is a least-squares fit of the "
                   "filter's own freqz curve over one octave; a two-pole "
                   "resonance has no such region, and a second unfiltered "
                   "noise floor under the record is not part of |H|^2")
    want = fx.truth["slope_db_oct"]
    if want is None or fx.truth["slope_band"] is None:
        return _nm("fewer than one octave of single-slope region below 0.12 fs")
    lo, hi = fx.truth["slope_band"]
    e = am.psd_slope_db_oct(fx.x, (lo, hi), fx.sr)
    if not e.ok:
        return _ref(e.reason)
    return (_ok(f"{_err(e.value, want)} over [{lo:.0f}, {hi:.0f}] Hz, against "
                f"the fitted slope of the filter's own freqz curve "
                f"(asymptote {fx.truth['asymptote_db_oct']:+.0f})")
            if near(e.value, want, 0.0, 2.5) else _bad(_err(e.value, want)))


def c_transfer(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on an IMPULSE RESPONSE; the filtered-noise "
                   "fixtures carry their filter's own IR in truth['ir']")
    f, mag = am.transfer(fx.truth["ir"], fx.sr)
    ref_f, ref_db = fx.truth["ref_freqs"], fx.truth["ref_mag_db"]
    got_db = 20.0 * np.log10(np.maximum(mag, 1e-30))
    probes = [p for p in (50.0, 200.0, 1000.0, 4000.0, 12000.0)
              if 20.0 < p < 0.45 * fx.sr]
    worst, where = 0.0, None
    for p in probes:
        g = float(np.interp(p, f, got_db))
        w = float(np.interp(p, ref_f, ref_db))
        if abs(g - w) > worst:
            worst, where = abs(g - w), p
    return (_ok(f"worst departure from freqz of the same coefficients "
                f"{worst:.3f} dB (at {where:.0f} Hz)") if worst < 0.5
            else _bad(f"worst departure from freqz {worst:.3f} dB at "
                      f"{where:.0f} Hz"))


def c_resonant_peak(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on an impulse response")
    e = am.resonant_peak(fx.truth["ir"], fx.sr)
    if fx.truth["kind"] != "resonant":
        return (_ref(f"a butterworth {fx.truth['kind']} has no peak: {e.reason}")
                if not e.ok else
                _bad(f"reported a {e.value:.0f} Hz 'centre frequency' for a "
                     f"maximally-flat {fx.truth['kind']}"))
    want = fx.truth["f_peak"]
    if not e.ok:
        return _bad(f"refused a Q={fx.truth['q']:g} resonance: {e.reason}")
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.03)
            else _bad(_err(e.value, want)))


def c_bandwidth_q(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on an impulse response")
    e = am.bandwidth_q(fx.truth["ir"], fx.sr)
    if fx.truth["kind"] != "resonant":
        return (_ref(f"no peak to take a bandwidth of: {e.reason}") if not e.ok
                else _bad(f"reported Q={e.value:.2f} for a maximally-flat "
                          f"{fx.truth['kind']}"))
    want = fx.truth["q_measured"]
    if want is None:
        return _nm("the reference curve does not fall 3 dB on both sides "
                   "inside the measured span")
    if not e.ok:
        return _ref(e.reason)
    return (_ok(f"{_err(e.value, want)} against the -3 dB bandwidth of the "
                f"filter's own freqz curve") if near(e.value, want, 0.12)
            else _bad(_err(e.value, want)))


def c_corner_3db(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on an impulse response")
    if fx.truth["kind"] == "resonant":
        return _nm("for a resonant filter the -3 dB corner, the peak and f0 "
                   "are three different numbers and the estimator's own "
                   "docstring says to ask for whichever the reference "
                   "specifies: the peak is what this fixture declares, and "
                   "resonant_peak is what checks it")
    want = fx.truth["corner_hz"]
    fc = fx.truth["fc"]
    #: THE REF_BAND IS A PRECONDITION, NOT A CONVENIENCE. Asked with its
    #: default ("the top or bottom decade"), a 200 Hz lowpass at 48 kHz reads
    #: 298.8 Hz for a corner that is at 199.8 Hz -- 50 % high -- because the
    #: default bottom decade is 20..200 Hz and so contains the corner itself.
    #: The passband the corner is measured against is handed over explicitly,
    #: and the default's reading is reported alongside so the discrepancy is
    #: recorded rather than hidden by the fix.
    ref = ((5.0, fc / 4) if fx.truth["kind"] == "lowpass"
           else (fc * 4, 0.45 * fx.sr))
    e = am.corner_3db(fx.truth["ir"], fx.truth["kind"], fx.sr, ref_band=ref)
    d = am.corner_3db(fx.truth["ir"], fx.truth["kind"], fx.sr)
    note = (f"; with the DEFAULT ref_band it reads "
            f"{d.value:.1f} Hz" if d.ok else f"; default ref_band refuses "
            f"({d.reason})")
    if not e.ok:
        return _ref(e.reason + note)
    return (_ok(_err(e.value, want) + note) if near(e.value, want, 0.05)
            else _bad(_err(e.value, want) + note))


def _curve(fx):
    return fx.truth["ref_freqs"], fx.truth["ref_mag_db"]


def c_corner_from_curve(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a MEASURED frequency-response curve, not on a "
                   "time record")
    if fx.truth["kind"] != "lowpass":
        return _nm("its `kind` argument is 'lowpass' or 'highpass' against a "
                   "passband plateau; a resonant curve has no plateau and "
                   "peak_from_curve is the one for it")
    f, db_ = _curve(fx)
    want = fx.truth["corner_hz"]
    e = am.corner_from_curve(f, db_, ref_band=(f[0], fx.truth["fc"] / 4),
                             kind="lowpass")
    if not e.ok:
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.02)
            else _bad(_err(e.value, want)))


def c_peak_from_curve(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a measured frequency-response curve")
    f, db_ = _curve(fx)
    if fx.truth["kind"] != "resonant":
        e = am.peak_from_curve(f, db_, ref_band=(f[0], fx.truth["fc"] / 4))
        return (_ref(f"a maximally-flat {fx.truth['kind']} has no peak: "
                     f"{e.reason}") if not e.ok
                else _bad(f"reported a {e.value:.2f} dB peak on a "
                          f"maximally-flat {fx.truth['kind']}"))
    want = fx.truth["peak_db"]
    e = am.peak_from_curve(f, db_, ref_band=(f[0], fx.truth["fc"] / 8))
    if not e.ok:
        return _ref(e.reason)
    return (_ok(f"{_err(e.detail['f_peak'], fx.truth['f_peak'])} in frequency, "
                f"height {e.value:.2f} dB over the plateau (curve maximum "
                f"{want:.2f} dB)")
            if near(e.detail["f_peak"], fx.truth["f_peak"], 0.03)
            else _bad(f"peak at {e.detail['f_peak']:.1f} Hz, curve maximum at "
                      f"{fx.truth['f_peak']:.1f} Hz"))


def c_slope_db_oct(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a measured frequency-response curve")
    want = fx.truth["slope_db_oct"]
    if want is None or fx.truth["slope_band"] is None:
        return _nm("a resonant two-pole response has no single-slope region "
                   "wide enough to fit")
    f, db_ = _curve(fx)
    lo, hi = fx.truth["slope_band"]
    e = am.slope_db_oct(f, db_, (lo, hi))
    if not e.ok:
        return _ref(e.reason)
    return (_ok(f"{_err(e.value, want)} over [{lo:.0f}, {hi:.0f}] Hz "
                f"(asymptote {fx.truth['asymptote_db_oct']:+.0f} dB/oct; the "
                f"band's own fitted slope is the reference)")
            if near(e.value, want, 0.0, 0.5) else _bad(_err(e.value, want)))


def c_plateau_db(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a measured frequency-response curve")
    want = fx.truth["plateau_db"]
    if want is None:
        return _nm("a resonant response has no passband plateau")
    f, db_ = _curve(fx)
    band = ((f[0], fx.truth["fc"] / 4) if fx.truth["kind"] == "lowpass"
            else (fx.truth["fc"] * 4, 0.45 * fx.sr))
    got = am.plateau_db(f, db_, band)
    return (_ok(_err(got, want)) if near(got, want, 0.0, 0.3)
            else _bad(_err(got, want)))


def c_dc_plateau_db(fx):
    if fx.kind != "filtered_noise":
        return _nm("defined on a measured frequency-response curve")
    if fx.truth["kind"] != "lowpass":
        return _nm("extrapolates a LOW-pass passband to DC; a highpass has no "
                   "DC passband and a resonant response has no plateau")
    f, db_ = _curve(fx)
    want = fx.truth["plateau_db"]
    e = am.dc_plateau_db(f, db_, (f[0], fx.truth["fc"] / 4),
                         scale_hz=fx.truth["fc"])
    if not e.ok:
        return _ref(e.reason)
    return (_ok(_err(e.value, want)) if near(e.value, want, 0.0, 0.5)
            else _bad(_err(e.value, want)))


def c_spectrum(fx):
    """The definitional check: the largest bin of a stationary record at a
    known f0 is at f0, to within half a bin."""
    f0 = fx.truth.get("f0")
    if f0 is None or not fx.truth.get("stationary"):
        return _nm("checked where a known stationary line exists; a decaying "
                   "line's spectrum is a Lorentzian whose peak bin is f but "
                   "whose shape is the decay, which decay_tau measures")
    if fx.truth.get("kind") == "rect" and fx.truth["duty"] != 0.5:
        return _nm(f"a duty-{fx.truth['duty']:g} rectangle carries a DC term "
                   f"of 2d-1 = {2*fx.truth['duty']-1:+.2f}, and a magnitude "
                   f"spectrum's zero bin holds all of it while a windowed "
                   f"line is spread over three bins -- so the largest BIN is "
                   f"the DC one and is not the largest PARTIAL")
    f, mag = am.spectrum(fx.x, fx.sr)
    got = float(f[int(np.argmax(mag))])
    binw = fx.sr / fx.n
    return (_ok(f"peak bin {got:.2f} Hz, known f0 {f0:g} Hz, bin width "
                f"{binw:.2f} Hz") if abs(got - f0) <= binw
            else _bad(f"peak bin {got:.2f} Hz vs known f0 {f0:g} Hz "
                      f"(bin {binw:.2f} Hz)"))


# ===========================================================================
# the coverage declaration
# ===========================================================================
@dataclass
class Spec:
    """One estimator, the families it is checked on, and why the others are
    not applicable. The `skip_reason` is what makes an omission explicit."""
    name: str
    check: Callable
    applies_to: tuple
    skip_reason: str
    extra: dict = field(default_factory=dict)


#: every family
ALL = tuple(ef.FAMILIES)
#: the families whose record is a time-domain audio signal of a struck or
#: sustained event (i.e. not the response-curve fixtures)
TIME = ALL

#: Shorthands for the families a whole class of estimator applies to.
STRUCK = ("damped_sine", "two_modes", "transient_then_ring",
          "envelope_plus_noise", "repeated_hits")
STATIONARY = ("harmonic_mixture",)
RESPONSE = ("filtered_noise",)
#: families with ONE known carrier frequency and ONE known tau
ONE_CARRIER = ("damped_sine", "envelope_plus_noise", "repeated_hits")

_WHY_STATIONARY = (
    "its validated input is a STATIONARY record at a known f0 -- a coherent "
    "projection or a harmonic-series reading of a decaying or gliding record "
    "measures the window average of the envelope, not the partial. The "
    "decaying case belongs to decay_tau and damped_sinusoid, which are "
    "checked on it.")
_WHY_RESPONSE = (
    "its input is an IMPULSE RESPONSE or a measured frequency-response curve, "
    "not a time-domain record of a note. The filtered_noise family carries "
    "both -- its filter's IR in truth['ir'] and that filter's freqz curve in "
    "truth['ref_mag_db'] -- which is why it is the only family here that can "
    "ground-truth them.")
_WHY_PERIOD = (
    "defined on ONE PERIOD of a repeating waveform, which only the stationary "
    "family has: a struck record's period is modulated by its own envelope "
    "and a noise record has none.")
_WHY_ENVELOPE = (
    "defined on an ENVELOPE whose shape is known in closed form. "
    "envelope_plus_noise is the family built for that -- exponential, "
    "straight-ramp and RC-charge members plus a quantised staircase -- so it "
    "is where the closed forms live.")

SPECS = [
    # ---- primitives and guards, defined on any record --------------------
    Spec("rms", c_rms, ALL, ""),
    Spec("peak", c_peak, ALL, ""),
    Spec("is_silent", c_is_silent, ALL, ""),
    Spec("clipped_fraction", c_clipped_fraction, ALL, ""),
    Spec("quantisation_floor", c_quantisation_floor, ALL, ""),
    Spec("max_sample_step", c_max_sample_step, ALL, ""),
    Spec("longest_plateau", c_longest_plateau, ALL, ""),
    Spec("sounding_extent", c_sounding_extent, ALL, ""),
    Spec("strip_trailing_silence", c_strip_trailing_silence, ALL, ""),
    Spec("analytic_signal", c_analytic_signal, ALL, ""),
    Spec("compare", c_compare, ALL, ""),
    Spec("nonfinite_report", c_nonfinite_report, ALL, ""),
    Spec("require_finite", c_require_finite, ALL, ""),
    Spec("event_slices", c_event_slices, ALL, ""),
    Spec("onsets", c_onsets, ALL, ""),
    # ---- decay -----------------------------------------------------------
    Spec("decay_tau", c_decay_tau, ALL, ""),
    Spec("schroeder_t20", c_schroeder_t20, ALL, ""),
    Spec("damped_sinusoid", c_damped_sinusoid, ALL, ""),
    Spec("decay_tau (windowed)", c_decay_tau_windowed,
         ("transient_then_ring", "repeated_hits"),
         "the other half of the ambiguity rule needs a record with a SECOND "
         "interval to ask about -- a transient and then a ring, or several "
         "hits. A single-mode record has only the one interval, which the "
         "unwindowed check already covers.",
         dict(target="decay_tau")),
    # ---- frequency -------------------------------------------------------
    Spec("dominant_frequency", c_dominant_frequency, ALL, ""),
    Spec("line_at", c_line_at, ALL, ""),
    Spec("zero_crossing_frequency", c_zero_crossing_frequency, ONE_CARRIER,
         "counts interpolated upward zero crossings from the first to the "
         "last, so it needs ONE carrier frequency to be right about. A "
         "two-mode pair crosses at neither of its two, a glide crosses at a "
         "different rate every cycle, a harmonic mixture crosses once per "
         "period of its FUNDAMENTAL but also wherever a partial dominates, "
         "and noise has no carrier."),
    Spec("instantaneous_frequency", c_instantaneous_frequency,
         ("damped_sine", "transient_then_ring", "envelope_plus_noise",
          "repeated_hits"),
         "needs a known instantaneous-frequency TRAJECTORY. These four "
         "families have one in closed form (constant, or the exact integral "
         "of a glide law); a two-mode pair's analytic phase derivative is the "
         "beat and not either mode, and a noise record's is undefined."),
    Spec("glide_law", c_glide_law, ALL, ""),
    Spec("refine_f0", c_refine_f0, STATIONARY,
         "refuses a record more than 50 cents from the commanded f0 and reads "
         "the phase the fundamental accumulates between the two halves of the "
         "record, so " + _WHY_STATIONARY),
    Spec("repeat_period", c_repeat_period, ALL, ""),
    # ---- envelopes -------------------------------------------------------
    Spec("analytic_envelope", c_analytic_envelope,
         ("damped_sine", "envelope_plus_noise"),
         "compared point by point against a KNOWN envelope. Those two "
         "families have one; a beating pair's envelope is the beat, a glide's "
         "is two stages, several hits' is a sum of overlapping tails, and "
         "noise has no closed-form envelope at all."),
    Spec("rms_envelope", c_rms_envelope, ("envelope_plus_noise",), _WHY_ENVELOPE),
    Spec("average_envelope", c_average_envelope, ("envelope_plus_noise",),
         "averages several differently-seeded renders of the SAME event, so "
         "it needs a known envelope AND a noise floor to average down. " + _WHY_ENVELOPE),
    Spec("moving_average_envelope", c_moving_average_envelope, (),
         "DEPRECATED by its own docstring -- 'kept only as the counter-example "
         "in test_moving_average_envelope_ripples_where_the_analytic_one_does_"
         "not ... Do not use it to measure anything.' Ground-truthing it would "
         "assert a bound on a measure the module tells callers not to use, and "
         "the counter-example it exists for is already in "
         "model/test_audio_measure.py."),
    Spec("envelope_bursts", c_envelope_bursts, ALL, ""),
    Spec("segment_shape", c_segment_shape, ("envelope_plus_noise",),
         "reads the midpoint deviation of one envelope SEGMENT against the "
         "closed-form landmarks 0.0 (linear), +0.3808 (RC) and -0.25 (t^2). "
         + _WHY_ENVELOPE),
    Spec("envelope_ripple_db", c_envelope_ripple_db, ("envelope_plus_noise",),
         "reads the stepping in an envelope against the closed form "
         "20 log10(d / (sqrt(12) m)). " + _WHY_ENVELOPE),
    # ---- spectra ---------------------------------------------------------
    Spec("spectrum", c_spectrum, STATIONARY,
         "its definitional check is that the largest bin of a record at a "
         "known stationary f0 is at f0. A decaying line's spectrum is a "
         "Lorentzian whose width is the decay -- which decay_tau measures -- "
         "and a noise spectrum's largest bin is a draw."),
    Spec("tonality_db", c_tonality_db, ALL, ""),
    Spec("spectral_flatness", c_spectral_flatness, STATIONARY + RESPONSE,
         "needs either a flat known band (filtered noise, where the closed "
         "form is exp(-gamma) = 0.5615) or a dense known comb. A struck "
         "record's spectrum is neither."),
    Spec("spectral_centroid", c_spectral_centroid, STATIONARY + RESPONSE,
         "a centroid's ground truth is a KNOWN power distribution over "
         "frequency: sum f P / sum P over a known line set, or the same "
         "integral over a known |H|^2. The centroid of a single-line record "
         "is that line, which dominant_frequency already checks exactly."),
    Spec("spectral_lines", c_spectral_lines, STATIONARY,
         "counts lines in a band, so it needs a KNOWN number of them there "
         "(and at least five). " + _WHY_STATIONARY),
    Spec("line_stability", c_line_stability,
         STATIONARY + ("transient_then_ring",),
         "asks whether the first window's lines reappear in every other "
         "window, so it needs at least five known lines AND a known answer "
         "about whether they move: a stationary comb (they do not) against a "
         "glide (they do). The other families have neither."),
    Spec("harmonic_powers", c_harmonic_powers, STATIONARY,
         "reads the power at each k*f0 against the planted series, so " + _WHY_STATIONARY),
    Spec("harmonic_signature", c_harmonic_signature, STATIONARY,
         "reads h2..hk of a STEADY tone by coherent projection, so " + _WHY_STATIONARY),
    Spec("inharmonic_fraction_db", c_inharmonic_fraction_db, STATIONARY,
         "measures the energy outside the harmonics of a known f0 against a "
         "PLANTED share, so " + _WHY_STATIONARY),
    Spec("foldback_alias_db", c_foldback_alias_db, STATIONARY,
         "measures energy at the predicted images of the harmonics above "
         "Nyquist, which needs a known harmonic series to predict from -- and "
         "the band-limited/naive pair at the same f0 is the contrast that "
         "makes its answer falsifiable. " + _WHY_STATIONARY),
    Spec("tone_amplitude", c_tone_amplitude, STATIONARY, _WHY_STATIONARY),
    Spec("windowed_tone_amplitude", c_windowed_tone_amplitude, STATIONARY,
         _WHY_STATIONARY),
    Spec("band_energy", c_band_energy, ALL, ""),
    Spec("psd_slope_db_oct", c_psd_slope_db_oct, RESPONSE,
         "defined on a NOISE signal's spectral colour, against a known slope. "
         + _WHY_RESPONSE),
    # ---- one period of a waveform ----------------------------------------
    Spec("cycle_average", c_cycle_average, STATIONARY, _WHY_PERIOD),
    Spec("duty_cycle", c_duty_cycle, STATIONARY, _WHY_PERIOD),
    Spec("pulse_edges", c_pulse_edges, STATIONARY, _WHY_PERIOD),
    Spec("rectangularity", c_rectangularity, STATIONARY, _WHY_PERIOD),
    Spec("midpoint_crossings", c_midpoint_crossings, STATIONARY, _WHY_PERIOD),
    Spec("step_ratio", c_step_ratio, STATIONARY, _WHY_PERIOD),
    Spec("waveform_id", c_waveform_id, STATIONARY,
         "names a waveform from its partial amplitudes at a commanded f0, so "
         + _WHY_STATIONARY),
    Spec("waveform_matches", c_waveform_id, STATIONARY,
         "takes a WaveformID rather than a signal, so it is exercised exactly "
         "where waveform_id is: on the family that produces a nameable "
         "waveform."),
    # ---- filter responses ------------------------------------------------
    Spec("transfer", c_transfer, RESPONSE, _WHY_RESPONSE),
    Spec("resonant_peak", c_resonant_peak, RESPONSE, _WHY_RESPONSE),
    Spec("bandwidth_q", c_bandwidth_q, RESPONSE, _WHY_RESPONSE),
    Spec("corner_3db", c_corner_3db, RESPONSE, _WHY_RESPONSE),
    Spec("corner_from_curve", c_corner_from_curve, RESPONSE, _WHY_RESPONSE),
    Spec("peak_from_curve", c_peak_from_curve, RESPONSE, _WHY_RESPONSE),
    Spec("slope_db_oct", c_slope_db_oct, RESPONSE, _WHY_RESPONSE),
    Spec("plateau_db", c_plateau_db, RESPONSE, _WHY_RESPONSE),
    Spec("dc_plateau_db", c_dc_plateau_db, RESPONSE, _WHY_RESPONSE),
]

#: STRUCK is used by the shorthands above; named so a reader can see the four
#: groups at a glance rather than deriving them from the table.
assert set(STRUCK) | set(STATIONARY) | set(RESPONSE) == set(ALL)

#: Public names in `audio_measure` that are NOT fixture-checkable estimators,
#: each with the reason. The inventory guard below requires every public
#: callable to be either in `SPECS` or here: a new estimator landing in the
#: module with no entry anywhere turns this suite red, which is the only
#: mechanism that keeps "every estimator is checked" true over time.
#:
#: WHAT DEFEATS THIS GUARD, said out loud: it catches OMISSION, not
#: MISCLASSIFICATION. Someone can add a genuine estimator here with a
#: plausible-sounding reason and the guard will accept it. Nothing in this
#: repository can tell a wrong reason from a right one, so this list is
#: reviewed by reading, and the reasons are written to be falsifiable.
NOT_FIXTURE_CHECKED = {
    "db": ("conversion", "20 log10(a/ref): scalar algebra with no signal. "
                         "Checked by the closed-form cases in `conversions()`."),
    "t20_from_tau": ("conversion", "ln(10) tau. Closed-form case in `conversions()`."),
    "tau_from_t20": ("conversion", "the inverse of the above; the round trip "
                                   "is a closed-form case in `conversions()`."),
    "fold_frequency": ("conversion", "where a frequency lands after sampling: "
                                     "arithmetic on a scalar, closed-form case "
                                     "in `conversions()`."),
    "natural_frequency_from_peak": ("conversion",
                                    "f0 from f_peak and Q: the inverse of the "
                                    "closed form `filtered_noise` builds its "
                                    "resonances from, so it is checked against "
                                    "those same parameters in `conversions()`."),
    "poles_to_freq_tau": ("conversion",
                          "the CONTROL-path counterpart of damped_sinusoid: it "
                          "reads coefficients, not audio. Checked in "
                          "`conversions()` against the coefficients a known "
                          "(f, tau) implies."),
    "register_domain": ("bookkeeping",
                        "records an estimator's declared domain in a registry. "
                        "It measures nothing; its own contract (idempotent "
                        "re-declaration, conflict detection) is tested in "
                        "model/test_audio_measure.py."),
    "spectral_lines": None,      # placeholder removed below; see SPECS
}
del NOT_FIXTURE_CHECKED["spectral_lines"]

#: dataclasses and exceptions, which hold no measurement
NOT_CALLABLE_ESTIMATORS = {
    "Comparison", "Damped", "DomainAxis", "Estimate", "InsufficientEvidence",
    "LineStats", "NonFiniteAudio", "ValidatedDomain", "WaveformID",
}


def public_callables() -> list[str]:
    import inspect
    out = []
    for n, o in vars(am).items():
        if n.startswith("_") or not callable(o):
            continue
        if getattr(o, "__module__", None) != "audio_measure":
            continue
        out.append(n)
        _ = inspect
    return sorted(out)


def inventory() -> list[tuple]:
    """(name, where, note, ok) for every public callable in `audio_measure`."""
    checked = {s.extra.get("target", s.name) for s in SPECS}
    rows = []
    for n in public_callables():
        if n in NOT_CALLABLE_ESTIMATORS:
            rows.append((n, "dataclass/exception", "holds no measurement", True))
        elif n in checked:
            rows.append((n, "SPECS", "checked against fixtures", True))
        elif n in NOT_FIXTURE_CHECKED:
            role, why = NOT_FIXTURE_CHECKED[n]
            rows.append((n, role, why, True))
        else:
            rows.append((n, "UNDECLARED", "a public callable in audio_measure "
                                          "that this suite neither checks nor "
                                          "states a reason for", False))
    return rows


# ===========================================================================
# closed-form conversions: no signal, so no fixture
# ===========================================================================
def conversions() -> list[tuple]:
    """(label, ok, detail) for the scalar conversions, from closed forms."""
    out = []

    def chk(label, got, want, tol):
        out.append((label, near(got, want, 0.0, tol), _err(got, want)))

    chk("db(2) == 6.0206 dB", am.db(2.0), 6.020599913, 1e-6)
    chk("db(1e-3) == -60 dB", am.db(1e-3), -60.0, 1e-9)
    chk("t20_from_tau(1 s) == ln(10) s", am.t20_from_tau(1.0), math.log(10.0), 1e-12)
    chk("tau_from_t20(t20_from_tau(0.137)) round-trips",
        am.tau_from_t20(am.t20_from_tau(0.137)), 0.137, 1e-12)
    chk("fold_frequency(30 kHz at 48 kHz) == 18 kHz",
        am.fold_frequency(30000.0, 48000), 18000.0, 1e-9)
    chk("fold_frequency(1 kHz at 48 kHz) == 1 kHz",
        am.fold_frequency(1000.0, 48000), 1000.0, 1e-9)
    # f_peak = f0 / sqrt(1 - 1/(2 Q^2)) inverted
    for f0, q in ((1000.0, 8.0), (300.0, 2.0), (4000.0, 20.0)):
        f_peak = f0 / math.sqrt(1 - 1 / (2 * q * q))
        chk(f"natural_frequency_from_peak inverts f_peak(f0={f0:g}, Q={q:g})",
            am.natural_frequency_from_peak(f_peak, q), f0, 1e-6 * f0)
    # the coefficients a known (f, tau) implies, read back
    for f, tau in ((220.0, 0.100), (56.0, 0.300), (3450.0, 0.004)):
        r = math.exp(-1.0 / (48000 * tau))
        a1 = 2 * r * math.cos(2 * math.pi * f / 48000)
        a2 = -r * r
        fe, te = am.poles_to_freq_tau(a1, a2, 48000)
        out.append((f"poles_to_freq_tau reads back (f={f:g} Hz, tau={tau*1e3:g} ms)",
                    fe.ok and te.ok and near(fe.value, f, 1e-9)
                    and near(te.value, tau, 1e-9),
                    f"{_err(fe.value, f)} / {_err(te.value, tau)}"))
    out.append(("poles_to_freq_tau refuses a non-oscillating pole pair",
                not am.poles_to_freq_tau(1.5, 0.5, 48000)[0].ok, "a2 >= 0"))
    return out


# ===========================================================================
# the run
# ===========================================================================
def run(kinds=None, estimators=None, fixtures=None) -> list[Verdict]:
    fixtures = ef.catalogue(kinds) if fixtures is None else fixtures
    out = []
    for spec in SPECS:
        if estimators and spec.name not in estimators:
            continue
        for fx in fixtures:
            if fx.kind not in spec.applies_to:
                out.append(Verdict(spec.name, fx.kind, fx.label, SKIP,
                                   spec.skip_reason))
                continue
            try:
                status, detail = spec.check(fx)
            except Exception as e:                           # noqa: BLE001
                status, detail = FAIL, f"{type(e).__name__}: {e}"
            out.append(Verdict(spec.name, fx.kind, fx.label, status, detail))
    return out


def summarise(verdicts) -> dict:
    counts = {}
    for v in verdicts:
        counts[v.status] = counts.get(v.status, 0) + 1
    return counts


def _per_pair(verdicts) -> dict:
    """(estimator, kind) -> {status: n}, the per-fixture per-estimator table
    #517's test plan asks for instead of one pass/fail number."""
    table = {}
    for v in verdicts:
        table.setdefault((v.estimator, v.kind), {}).setdefault(v.status, 0)
        table[(v.estimator, v.kind)][v.status] += 1
    return table


_GLYPH = {PASS: ".", REFUSED: "r", IDENTIFIED: "i", INSUFFICIENT: "?",
          SKIP: "-", NOT_MEASURED: "n", FAIL: "X", AMBIGUOUS: "A"}


def print_matrix(verdicts, kinds):
    table = _per_pair(verdicts)
    names = [s.name for s in SPECS]
    w = max(len(n) for n in names)
    print(f"  {'':{w}}  " + "  ".join(f"{k[:11]:>11s}" for k in kinds))
    for n in names:
        cells = []
        for k in kinds:
            c = table.get((n, k), {})
            if not c:
                cells.append(f"{'':>11s}")
                continue
            worst = next((s for s in (FAIL, AMBIGUOUS, PASS, IDENTIFIED,
                                      INSUFFICIENT, REFUSED, NOT_MEASURED, SKIP)
                          if s in c), SKIP)
            cells.append(f"{_GLYPH[worst]}{sum(c.values()):>4d}"
                         f"{'/' + str(c.get(FAIL, 0) + c.get(AMBIGUOUS, 0)) if (c.get(FAIL) or c.get(AMBIGUOUS)) else '':>6s}")
        print(f"  {n:{w}}  " + "  ".join(f"{c:>11s}" for c in cells))
    print("\n  legend: . pass   r refused   i identified   ? insufficient   "
          "- skipped (reason stated)\n          n not measured (check's own "
          "precondition unmet)   X FAIL   A ambiguous-confident")


def cmd_check(args) -> int:
    kinds = args.kind or list(ef.FAMILIES)
    fixtures = ef.catalogue(kinds)
    print("=" * 78)
    print("estimator ground truth: audio_measure against synthetic fixtures")
    print("=" * 78)
    print(f"  {len(fixtures)} fixtures over {len(kinds)} families, "
          f"{len(SPECS)} estimator checks")

    print("\n" + "-" * 78)
    print("0. Axis coverage of the fixture catalogue")
    print("-" * 78)
    axis_bad = []
    for kind, axis, n, spread, ok, note in ef.axis_coverage(fixtures):
        if not ok:
            axis_bad.append(f"{kind}/{axis}: {n} values, spread {spread:g}")
        if note.startswith("NOT APPLICABLE"):
            print(f"  SKIP {kind:22s} {axis:10s} {note}")
    print(f"  {len(ef.AXES)} axes x {len(kinds)} families: "
          f"{'all swept' if not axis_bad else str(len(axis_bad)) + ' NOT swept'}")

    print("\n" + "-" * 78)
    print("1. Inventory: every public callable in audio_measure is declared")
    print("-" * 78)
    inv_bad = []
    for n, where, why, ok in inventory():
        if not ok:
            inv_bad.append(n)
            print(f"  FAIL {n:30s} {why}")
    print(f"  {len(public_callables())} public callables; "
          f"{len({s.extra.get('target', s.name) for s in SPECS})} fixture-checked, "
          f"{len(NOT_FIXTURE_CHECKED)} declared not fixture-checkable, "
          f"{len(NOT_CALLABLE_ESTIMATORS)} dataclasses/exceptions, "
          f"{len(inv_bad)} UNDECLARED")

    print("\n" + "-" * 78)
    print("2. Closed-form conversions (no signal, so no fixture)")
    print("-" * 78)
    conv_bad = []
    for label, ok, detail in conversions():
        print(f"  {'OK  ' if ok else 'FAIL'} {label}: {detail}")
        if not ok:
            conv_bad.append(label)

    print("\n" + "-" * 78)
    print("3. Estimators x fixtures")
    print("-" * 78)
    verdicts = run(kinds, args.estimator, fixtures)
    print_matrix(verdicts, kinds)

    reds = [v for v in verdicts if v.status in RED]
    print("\n" + "-" * 78)
    print("4. Per-fixture, per-estimator detail")
    print("-" * 78)
    shown = {}
    for v in verdicts:
        if v.status == SKIP:
            continue
        key = (v.estimator, v.kind, v.status)
        if key in shown and not args.verbose:
            shown[key] += 1
            continue
        shown[key] = 1
        print(f"  {v.status:12s} {v.estimator:26s} {v.kind:20s} {v.label}")
        if v.detail:
            print(f"               -> {v.detail}")
    if not args.verbose:
        rep = sum(n - 1 for n in shown.values())
        print(f"\n  ({rep} further records with the same (estimator, family, "
              f"outcome) collapsed; --verbose prints every one)")

    print("\n" + "-" * 78)
    print("5. Skipped pairs, with the stated reason")
    print("-" * 78)
    seen = set()
    for v in verdicts:
        if v.status != SKIP or (v.estimator, v.kind) in seen:
            continue
        seen.add((v.estimator, v.kind))
        print(f"  SKIP {v.estimator:26s} {v.kind:20s} {v.detail}")
    for spec in SPECS:
        if spec.applies_to != ALL and not spec.skip_reason:
            print(f"  WARN {spec.name}: applies_to is narrowed with no reason")

    counts = summarise(verdicts)
    print("\n" + "=" * 78)
    for k in (PASS, IDENTIFIED, INSUFFICIENT, REFUSED, NOT_MEASURED, SKIP,
              FAIL, AMBIGUOUS):
        if counts.get(k):
            print(f"  {k:14s} {counts[k]:5d}")
    bad = len(reds) + len(inv_bad) + len(conv_bad) + len(axis_bad)
    if bad:
        print(f"\n{bad} check(s) FAILED:")
        for a in axis_bad:
            print(f"  - axis not swept: {a}")
        for n in inv_bad:
            print(f"  - undeclared estimator: {n}")
        for c in conv_bad:
            print(f"  - conversion: {c}")
        for v in reds:
            print(f"  - {v.status} {v.estimator} on {v.kind} [{v.label}]: "
                  f"{v.detail}")
        return 1
    print("\nAll checks passed.")
    return 0


def cmd_matrix(args) -> int:
    kinds = list(ef.FAMILIES)
    verdicts = run(kinds)
    print_matrix(verdicts, kinds)
    for n, where, why, ok in inventory():
        if where in ("conversion", "bookkeeping", "dataclass/exception"):
            print(f"  NOT FIXTURE-CHECKED  {n:30s} ({where}) {why}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="run the gate")
    c.add_argument("--kind", action="append", choices=list(ef.FAMILIES))
    c.add_argument("--estimator", action="append")
    c.add_argument("--verbose", action="store_true")
    c.set_defaults(fn=cmd_check)
    m = sub.add_parser("matrix", help="the coverage table only")
    m.set_defaults(fn=cmd_matrix)
    try:
        import estimator_ground_truth_controls as ctl                 # noqa: E402
    except ImportError:                                               # pragma: no cover
        ctl = None
    if ctl is not None:
        ctl.add_parser(sub)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())

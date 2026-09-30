#!/usr/bin/env python3
"""Qualification for a plugin rig, asserted from the SIGNAL and shared across
hosts.

`model/reference_rigs.py`'s `_Plugin.qualify()` already refuses a rig whose
pinned settings do not hold by NAME and by READBACK. That half is about the
apparatus's *controls*. This module is the other half, about its *output*, and
it exists because issue #124 needed the same discipline under a second host
(`pedalboard`) without forking it:

    is it sounding                 a rig that renders silence has no verdict
    is the level usable            8.57 % of Model D's default patch sits at
                                   the rail; a clipped reference is not a
                                   reference
    is it playing the note asked   Model D's default patch answers MIDI 60
                                   (261.63 Hz) with 131.00 Hz -- an octave
                                   down, the same defect Mini V3's Range
                                   control has
    which waveform IS it           identified from the record, never from what
                                   was requested (the rig asked Surge for a saw
                                   and published a 50 % pulse for a study)
    do the COMMANDS cause anything issue #137: every check above can pass on a
                                   rig whose controls are not connected to
                                   anything. +12 semitones must move the
                                   measured pitch to 2x, and the cutoff knob
                                   must move the measured spectral centroid,
                                   monotonically, by a stated amount

THREE OUTCOMES, AND THEY ARE NOT INTERCHANGEABLE
------------------------------------------------
    PASS      the check answered and the rig is in the state it claims
    FAIL      the check answered and the rig is NOT in that state
    REFUSED   the check could not answer -- no signal, no fundamental, no
              identifiable waveform. There is no number to report

`Qualification.verdict` is `qualified` only when every check PASSes.
`REFUSED` outranks `FAIL` in the report because "we measured this and it is
wrong" and "we could not measure this" are different claims and only the first
one is evidence about the rig.

WHY THE CHECKS ARE NOT REDUNDANT, WITH THE CASE THAT SEPARATES EACH PAIR
------------------------------------------------------------------------
This matters because a suite of overlapping checks feels like rigour and buys
none. `model/test_rig_qualification.py` carries one injected defect per check
and asserts the discrimination matrix -- which check fires, and which do NOT.

  * **pitch vs. pitch-causality.** A rig an octave down at EVERY note still
    doubles correctly on +12, so `check_pitch_causality` reports 2.000 and
    passes. Only `check_pitch` catches the octave. Conversely a rig whose
    pitch command does nothing plays the right note at the base and
    `check_pitch` passes; only the causality check catches it.
  * **level vs. waveform.** A clipped saw is still a saw to a harmonic
    comparison at the tolerances used here; `check_level` is the only one that
    sees the rail.
  * **either of those vs. the pins.** Surge's Phaser is a chain of allpasses:
    at its default mix it moves phase and leaves every harmonic amplitude
    where it was. No signal check here can refuse it. The pin readback can,
    which is why `check_pins` is in this battery and not replaced by it.

THE SILENCE FLOOR, AND WHERE IT DIVERGES FROM THE PRIOR ART
-----------------------------------------------------------
`~/dev/generate-random-dexed-sounds/` gates its corpus on
`max_val_05 < 1e-6` -- the peak of the first 0.5 s, and a *corpus* filter:
its job is to throw away a bad draw cheaply and draw again, so a floor three
orders of magnitude above true silence is exactly right for it.

This module's `SILENCE_FLOOR` is `1e-9`, `audio_measure.is_silent`'s default,
and the two do NOT agree. The divergence is deliberate and it is in the other
direction from the one that would be dangerous:

  * `1e-9` is the "is there anything at all" question, and it is the only
    question a floor can answer without knowing the level the rig was set to.
    At `1e-9` a REFUSAL means the host produced a buffer of zeros -- which is
    precisely the Model-D-under-dawdreamer finding (peak 0.0, exactly).
  * "is the level USABLE" is a separate check with its own stated window
    (`check_level`), because the answer depends on the patch and not on the
    estimator. A rig peaking at 1e-7 is not silent and is not usable, and
    collapsing those two into one threshold is how a level defect gets
    reported as an absence of signal.
  * `tools/refprofile.py`'s `ESTIMATOR_FLOORS["probe level"]` is a THIRD
    number (-12.04 dBFS) and answers a third question: the input level at
    which OUR fixed-point ladder is inside its own measured stability window.
    It is a property of the thing being compared against the reference, not of
    the reference, and it is not a silence gate at all.

Three numbers, three questions. The prior art's is the right answer to its
question and the wrong answer to this one.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import audio_measure as am                                          # noqa: E402

SR = 48000

PASS, FAIL, REFUSED = "pass", "fail", "refused"

#: Ranked worst-first. A refusal is not a failure and is reported above one:
#: see the module docstring.
_RANK = {PASS: 0, FAIL: 1, REFUSED: 2}

#: `audio_measure.is_silent`'s default. See the module docstring for why this
#: is NOT the prior art's 1e-6 and not `ESTIMATOR_FLOORS["probe level"]`.
SILENCE_FLOOR = 1e-9

#: The level window a clip must be inside before it may be frozen. `peak_max`
#: is below 1.0 on purpose: a reference that touches the rail has already lost
#: information and no downstream measurement can put it back. `clipped_max` is
#: ZERO samples at or beyond full scale, not "few" -- Model D's default patch
#: puts 8.57 % there, and a tolerance chosen above that number would have been
#: chosen after seeing it.
PEAK_MIN, PEAK_MAX, CLIPPED_MAX, FULL_SCALE = 0.05, 0.99, 0.0, 1.0

#: `refine_f0`'s own window. 50 cents is a quarter tone: past it the rig is
#: playing a different note, which is an apparatus fault and not a measurement.
MAX_CENTS = 50.0

#: The pitch-causality interval and its tolerance. 2 % in frequency is 34
#: cents, comfortably inside the 100-cent gap to the next semitone, so a rig
#: that transposes by 11 or 13 semitones fails rather than rounding to pass.
CAUSALITY_SEMITONES = 12
CAUSALITY_RATIO_TOL = 0.02

#: The cutoff sweep must move the POWER-weighted spectral centroid by at least
#: this FACTOR from its lowest knob to its highest, and must never move
#: backwards by more than `MONOTONIC_SLACK` of the previous step.
#:
#: **1.25 IS A MEASURED NUMBER AND THE FIRST VALUE WRITTEN HERE WAS NOT.** This
#: constant started at 2.0 -- "an octave of centroid movement, a connected knob
#: cannot do less" -- and that gate was UNSATISFIABLE: a one-pole swept over a
#: 15.9x range of corners, driven by a fixed band-limited source, moves its
#: power-weighted centroid by 1.913 (saw) and 1.767 (25 % pulse). The reason is
#: the source and not the filter: a saw's power falls as 1/k^2, so the centroid
#: sits near the fundamental and a wide corner sweep drags it only a little.
#: The rejected gate is recorded because an unsatisfiable gate is worse than no
#: gate -- it trains everyone to ignore gates, including the ones that work.
#:
#: What separates the two states is not the size of the movement, it is that a
#: knob wired to nothing gives EXACTLY 1.000 (the same render twice). So the
#: measured window is
#:
#:     disconnected knob   1.000
#:     real low-pass       1.767 .. 1.913 (measured, see
#:                         model/test_rig_qualification.py's one-pole cases)
#:
#: and 1.25 sits between them with margin on both sides. It is deliberately
#: NOT set just under 1.767: the validation sweep spans 15.9x in corner
#: frequency and a plugin's own knob over the same normalised span may cover
#: less, so a gate pinned to the validation case would refuse a working rig
#: with a gentler taper.
#:
#: Power weighting, not amplitude: `audio_measure.spectral_centroid`'s own
#: docstring records that the amplitude-weighted centroid reads a quiet
#: wideband floor as brightness, and `sound_report --inject
#: sd-centroid-amp-weighted` reinstates that as a defect. It is 2.846 on the
#: same sweep -- more sensitive, and the wrong estimator. Sensitivity bought
#: from a known-bad measurand is not sensitivity.
CENTROID_MIN_RATIO = 1.25
MONOTONIC_SLACK = 0.02


class RigRefusal(AssertionError):
    """A precondition of the rig failed, so the rig must not be used.

    Carries the `Qualification` that produced it: a refusal whose evidence is
    only in the exception text is a refusal nobody can act on, and the record
    is exactly what issue #124 asks to be written down when Model D's octave
    default or its clipping turn out not to be controllable.
    """

    def __init__(self, message: str, qualification: "Qualification | None" = None):
        super().__init__(message)
        self.qualification = qualification


@dataclass(frozen=True)
class Check:
    """One asserted precondition. `detail` always carries the number the check
    read, including when it failed -- a check that reports only a verdict
    cannot be re-read later and is not evidence."""
    name: str
    outcome: str
    why: str
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome == PASS

    def as_dict(self) -> dict:
        return {"check": self.name, "outcome": self.outcome, "why": self.why,
                "detail": _jsonable(self.detail)}

    def __repr__(self) -> str:
        return f"Check({self.name}: {self.outcome.upper()}{'' if self.ok else ' -- ' + self.why})"


@dataclass(frozen=True)
class Qualification:
    """Every check a rig was put through, and the one verdict derived from
    them. Nothing here is a boolean standing in for a measurement."""
    rig: str
    host: str
    checks: tuple

    @property
    def verdict(self) -> str:
        """`qualified`, or the worst outcome among the checks.

        A rig with no checks is `refused`, not `qualified`. An empty battery
        that reports success is the shape of every false green this repository
        has had."""
        if not self.checks:
            return REFUSED
        worst = max(self.checks, key=lambda c: _RANK[c.outcome])
        return "qualified" if worst.ok else worst.outcome

    @property
    def qualified(self) -> bool:
        return self.verdict == "qualified"

    def failures(self) -> list:
        return [c for c in self.checks if not c.ok]

    def require(self) -> "Qualification":
        """Return self when qualified; otherwise REFUSE, with the record
        attached to the exception."""
        if self.qualified:
            return self
        bad = "; ".join(f"{c.name}: {c.outcome.upper()} -- {c.why}" for c in self.failures())
        raise RigRefusal(
            f"{self.rig} under {self.host}: {self.verdict.upper()} -- {bad}", self)

    def as_dict(self) -> dict:
        return {"rig": self.rig, "host": self.host, "verdict": self.verdict,
                "n_checks": len(self.checks),
                "n_passed": sum(1 for c in self.checks if c.ok),
                "checks": [c.as_dict() for c in self.checks]}

    def table(self) -> str:
        w = max([len(c.name) for c in self.checks] + [5])
        lines = [f"{'check'.ljust(w)}  {'outcome':<8}why",
                 "-" * (w + 10 + 60)]
        for c in self.checks:
            lines.append(f"{c.name.ljust(w)}  {c.outcome.upper():<8}{c.why}")
        lines.append("-" * (w + 10 + 60))
        lines.append(f"{self.rig} under {self.host}: {self.verdict.upper()}")
        return "\n".join(lines)


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return v if math.isfinite(v) else str(v)
    if isinstance(o, (np.integer, int)) and not isinstance(o, bool):
        return int(o)
    if isinstance(o, np.ndarray):
        return _jsonable(o.tolist())
    if isinstance(o, (str, bool)) or o is None:
        return o
    return str(o)


# ===========================================================================
# the checks. Each one takes audio (or a render callable) and returns a Check.
# None of them raises on a bad signal: a refusal is a return value here.
# ===========================================================================
def check_sounding(y, *, floor: float = SILENCE_FLOOR, name="sounding") -> Check:
    """Is there anything at all? REFUSED, never FAILED: a buffer of zeros is an
    absence of evidence about the rig, not evidence that the rig is wrong.

    Non-finite samples are caught FIRST, because NaN and Inf both compare
    False against any threshold -- `tools/refprofile.load_clip` had all-NaN
    audio pass every content check it had for exactly this reason."""
    y = np.asarray(y, dtype=np.float64).ravel()
    if not y.size:
        return Check(name, REFUSED, "the host returned an empty buffer", {"frames": 0})
    bad = int(np.count_nonzero(~np.isfinite(y)))
    if bad:
        return Check(name, REFUSED,
                     f"{bad} of {y.size} samples are not finite (first at index "
                     f"{int(np.argmax(~np.isfinite(y)))}): this is not audio",
                     {"frames": int(y.size), "non_finite": bad})
    pk = float(np.abs(y).max())
    det = {"frames": int(y.size), "peak": pk, "rms": float(am.rms(y)), "floor": floor}
    if pk <= floor:
        return Check(name, REFUSED,
                     f"peak {pk:.3g} is at or below the silence floor {floor:.0g}: "
                     f"the host rendered no signal", det)
    return Check(name, PASS, f"peak {pk:.6f}", det)


def check_level(y, *, peak_min: float = PEAK_MIN, peak_max: float = PEAK_MAX,
                clipped_max: float = CLIPPED_MAX, full_scale: float = FULL_SCALE,
                name="level") -> Check:
    """Is the level usable as a reference? Two separate reasons it might not
    be, reported separately:

      * **at the rail.** `clipped_fraction` at `full_scale`. Model D's default
        patch reads 0.0857 here, and a clipped reference has lost information
        no downstream measurement can recover.
      * **too quiet, or too close to the rail to be safe.** `peak` outside
        [`peak_min`, `peak_max`].

    This is a FAIL and not a REFUSAL: the check answered, and the answer is
    that the rig is in a state a reference cannot be taken from."""
    y = np.asarray(y, dtype=np.float64).ravel()
    s = check_sounding(y, name=name)
    if not s.ok:
        return Check(name, REFUSED, s.why, s.detail)
    pk = float(np.abs(y).max())
    clip = float(am.clipped_fraction(y, full_scale))
    dc = float(np.mean(y))
    det = {"peak": pk, "clipped_fraction": clip, "dc_offset": dc,
           "peak_window": [peak_min, peak_max], "clipped_max": clipped_max,
           "full_scale": full_scale,
           "peak_dbfs": (am.db(pk, full_scale) if pk > 0 else None)}
    if clip > clipped_max:
        return Check(name, FAIL,
                     f"{100 * clip:.2f} % of samples are at or beyond full scale "
                     f"({full_scale:g}); the limit is {100 * clipped_max:.2f} %. "
                     f"A clipped reference is not a reference", det)
    if pk < peak_min:
        return Check(name, FAIL,
                     f"peak {pk:.6f} is below {peak_min:g}: sounding, but too quiet "
                     f"for a reference", det)
    if pk > peak_max:
        return Check(name, FAIL,
                     f"peak {pk:.6f} is above {peak_max:g}: too close to the rail", det)
    return Check(name, PASS, f"peak {pk:.6f}, {100 * clip:.2f} % at the rail", det)


def check_pitch(y, commanded_hz: float, *, sr: int = SR, max_cents: float = MAX_CENTS,
                name="pitch") -> Check:
    """Does the rig play the note it was COMMANDED? Measured, via
    `audio_measure.refine_f0`, which is the estimator that already refuses a
    rig playing the wrong note and already looks below the measured
    fundamental for the octave-down case -- Model D's default answers MIDI 60
    with 131.00 Hz and a naive search around 261.63 Hz locks onto its second
    harmonic and reports no error at all.

    REFUSED when no fundamental can be found (nothing to compare). FAIL when
    one is found and it is the wrong one -- the estimator's `detail` carries
    `f0_measured` in exactly that case and not in the other, so the two are
    told apart structurally and not by matching its wording."""
    y = np.asarray(y, dtype=np.float64).ravel()
    s = check_sounding(y, name=name)
    if not s.ok:
        return Check(name, REFUSED, s.why, s.detail)
    e = am.refine_f0(y, float(commanded_hz), sr, max_cents=max_cents)
    det = {"commanded_hz": float(commanded_hz), "max_cents": max_cents}
    det.update(_jsonable(dict(e.detail)))
    if e.ok:
        det["f0_hz"] = float(e.value)
        return Check(name, PASS,
                     f"{e.value:.2f} Hz against a commanded {commanded_hz:.2f} Hz "
                     f"({e.detail.get('cents', 0.0):+.1f} cents)", det)
    answered = ("f0_measured" in e.detail) or ("subharmonic" in e.detail)
    return Check(name, FAIL if answered else REFUSED, e.reason, det)


def check_waveform(y, f0_hz: float, *, sr: int = SR, expect: str | None = None,
                   name="waveform") -> Check:
    """WHICH waveform is this, from the record?

    `f0_hz` must be the MEASURED fundamental, not the commanded one. Passing
    the commanded value here would re-introduce exactly the assumption this
    check exists to remove: `waveform_id` asks whether the record repeats at
    the f0 it is handed, so a rig an octave down handed its commanded note
    refuses with a period-residual complaint instead of naming the waveform it
    is actually producing.

    `expect=None` means "must be identifiable, whatever it is" and PASSES on
    any named waveform. That is the honest setting for a plugin whose
    wave-selector mapping has never been measured on this host: requiring a
    specific label there would be asserting the mapping, which is the mistake
    (`SurgeRig`'s saw that was a 50 % pulse). When `expect` IS given, a
    different family is a FAIL."""
    y = np.asarray(y, dtype=np.float64).ravel()
    s = check_sounding(y, name=name)
    if not s.ok:
        return Check(name, REFUSED, s.why, s.detail)
    wid = am.waveform_id(y, float(f0_hz), sr)
    det = {"f0_measured_hz": float(f0_hz), "expect": expect, "label": wid.label,
           "duty": wid.duty}
    det.update(_jsonable(dict(wid.detail)))
    if not wid.ok:
        return Check(name, REFUSED,
                     f"no waveform can be named for this record: {wid.reason}", det)
    if expect is not None and wid.family != expect:
        return Check(name, FAIL,
                     f"the record is {wid.label!r}; {expect!r} was requested", det)
    return Check(name, PASS, f"identified as {wid.label!r}", det)


def check_pitch_causality(render_note, base_note: int, note_hz, *, sr: int = SR,
                          semitones: int = CAUSALITY_SEMITONES,
                          tol: float = CAUSALITY_RATIO_TOL,
                          name="pitch causality") -> Check:
    """Issue #137: commanding a pitch change must CAUSE one.

    Renders `base_note` and `base_note + semitones` and measures both with
    `dominant_frequency` over a band derived from the commanded notes -- NOT
    with `refine_f0`, and that choice is the whole point of the check:
    `refine_f0` asserts the note, so on a rig an octave down it refuses both
    renders and the ratio is never computed. The ratio is meaningful even when
    both renders are transposed by the same factor, and it is the only thing
    that separates "the command does nothing" from "the command works and the
    patch is offset". `check_pitch` answers the second question; this one
    answers the first. Neither substitutes for the other.

    A rig whose pitch command does nothing reports a ratio of 1.000 here and
    passes every other check in this module."""
    want = 2.0 ** (semitones / 12.0)
    det = {"base_note": int(base_note), "semitones": int(semitones),
           "ratio_expected": want, "tol": tol}
    got = {}
    for label, note in (("base", int(base_note)), ("transposed", int(base_note) + int(semitones))):
        y = np.asarray(render_note(note), dtype=np.float64).ravel()
        s = check_sounding(y, name=name)
        if not s.ok:
            det[label] = {"note": note, **s.detail}
            return Check(name, REFUSED,
                         f"the {label} render at note {note} gave no signal: {s.why}", det)
        cmd = float(note_hz(note))
        # A band wide enough to hold an octave error either side of the
        # commanded note, so a transposed rig is still measured rather than
        # refused, and narrow enough that a sub-audio LFO or the top of the
        # spectrum cannot be mistaken for the fundamental.
        e = am.dominant_frequency(y, cmd / 4.0, cmd * 4.0, sr)
        det[label] = {"note": note, "commanded_hz": cmd, "peak": float(np.abs(y).max()),
                      "f0_hz": (float(e.value) if e.ok else None),
                      "why": (None if e.ok else e.reason)}
        if not e.ok:
            return Check(name, REFUSED,
                         f"no fundamental in the {label} render at note {note} "
                         f"({e.reason})", det)
        got[label] = float(e.value)
    ratio = got["transposed"] / got["base"]
    det["ratio"] = ratio
    det["cents_error"] = 1200.0 * math.log2(ratio / want)
    if abs(ratio / want - 1.0) > tol:
        return Check(name, FAIL,
                     f"+{semitones} semitones moved the measured pitch "
                     f"{got['base']:.2f} -> {got['transposed']:.2f} Hz, a ratio of "
                     f"{ratio:.4f} where {want:.4f} was required "
                     f"({det['cents_error']:+.0f} cents): the command did not cause "
                     f"the effect", det)
    return Check(name, PASS,
                 f"+{semitones} semitones moved {got['base']:.2f} -> "
                 f"{got['transposed']:.2f} Hz, ratio {ratio:.4f}", det)


def check_filter_causality(render_cutoff, knobs, *, sr: int = SR,
                           band=(20.0, 20000.0), min_ratio: float = CENTROID_MIN_RATIO,
                           slack: float = MONOTONIC_SLACK,
                           name="filter causality") -> Check:
    """Issue #137: sweeping the cutoff must move the spectrum, monotonically,
    by a stated amount.

    `render_cutoff(knob)` renders the rig at one cutoff-control position; the
    measured quantity is `audio_measure.spectral_centroid` over `band`. Two
    conditions, both stated as numbers rather than as "it moves":

      * **monotonic**: no step may move the centroid DOWN by more than `slack`
        of the previous value. A knob wired backwards fails here even though
        its total movement is large.
      * **at least `min_ratio`** from the lowest knob to the highest. A knob
        wired to nothing gives 1.000.

    The centroid, not a -3 dB corner: a corner needs a transfer function and
    therefore a second render of the same source with the filter out of the
    way, which not every plugin can be put into. The centroid is read off the
    one render and is monotonic in the cutoff of any low-pass over a fixed
    source -- which is checked in `model/test_rig_qualification.py` against a
    one-pole at known corners, a signal whose answer is known independently of
    anything in this repository."""
    knobs = [float(k) for k in knobs]
    det = {"knobs": knobs, "band": list(band), "min_ratio": min_ratio, "slack": slack}
    if len(knobs) < 2:
        return Check(name, REFUSED, f"a sweep needs at least two knob positions, got "
                                    f"{len(knobs)}", det)
    cents, rows = [], []
    for k in knobs:
        y = np.asarray(render_cutoff(k), dtype=np.float64).ravel()
        s = check_sounding(y, name=name)
        if not s.ok:
            rows.append({"knob": k, "centroid_hz": None, "why": s.why})
            det["sweep"] = rows
            return Check(name, REFUSED,
                         f"the render at knob {k:g} gave no signal: {s.why}", det)
        c = float(am.spectral_centroid(y, band, sr))
        rows.append({"knob": k, "centroid_hz": c, "peak": float(np.abs(y).max())})
        cents.append(c)
    det["sweep"] = rows
    det["centroid_hz"] = cents
    ratio = cents[-1] / cents[0] if cents[0] > 0 else float("inf")
    det["centroid_ratio"] = ratio
    back = [(knobs[i], cents[i - 1], cents[i]) for i in range(1, len(cents))
            if cents[i] < cents[i - 1] * (1.0 - slack)]
    det["backward_steps"] = [{"knob": k, "from_hz": a, "to_hz": b} for k, a, b in back]
    if back:
        k, a, b = back[0]
        return Check(name, FAIL,
                     f"the centroid moved BACKWARDS at knob {k:g} ({a:.1f} -> {b:.1f} Hz) "
                     f"and {len(back)} step(s) did: the cutoff control is not monotonic "
                     f"in this direction", det)
    if not (ratio >= min_ratio):
        return Check(name, FAIL,
                     f"the centroid moved {cents[0]:.1f} -> {cents[-1]:.1f} Hz across "
                     f"knob {knobs[0]:g}..{knobs[-1]:g}, a ratio of {ratio:.3f} where "
                     f"{min_ratio:.3f} was required: commanding the cutoff did not "
                     f"cause the effect", det)
    return Check(name, PASS,
                 f"the centroid rose {cents[0]:.1f} -> {cents[-1]:.1f} Hz "
                 f"monotonically, a ratio of {ratio:.3f}", det)


def check_pins(check_pins_fn, *, n_pins: int | None = None, name="pins") -> Check:
    """The rig's OWN pin check -- `_Plugin.check_pins`, name and readback --
    lifted into this battery so one record carries both halves.

    Not reimplemented here, and that is deliberate: the pin table and the
    name/readback rule are `reference_rigs`' and are already the thing that
    caught Surge's index 265 renaming from 'Unison Voices' to 'High Cut'. A
    second implementation of the same rule is a second thing to keep in step.

    A rig with ZERO pins is REFUSED, not passed. Every plugin here has
    sound-changing controls that are not the thing under test, so an empty pin
    table means nobody has written them down yet -- and an empty check that
    reports success is the exact shape of a false green."""
    det = {}
    if n_pins is not None:
        det["n_pins"] = int(n_pins)
        if int(n_pins) == 0:
            return Check(name, REFUSED,
                         "the rig pins no parameters, so there is nothing to hold: "
                         "a rig that cannot prove its own settings has no verdict", det)
    try:
        bad = list(check_pins_fn() or ())
    except Exception as e:                                          # pragma: no cover
        return Check(name, REFUSED, f"the pin check itself failed: "
                                    f"{type(e).__name__}: {e}", det)
    det["bad"] = _jsonable(bad)
    if bad:
        return Check(name, FAIL,
                     f"{len(bad)} pinned setting(s) did not hold: {bad}", det)
    return Check(name, PASS, f"{det.get('n_pins', '?')} pinned settings held", det)


# ===========================================================================
# the battery
# ===========================================================================
def qualify_voice(*, rig: str, host: str, render_note, render_cutoff, note_hz,
                  note: int, check_pins_fn=None, n_pins: int | None = None,
                  sr: int = SR, expect_wave: str | None = None,
                  semitones: int = CAUSALITY_SEMITONES,
                  cutoff_knobs=(0.3, 0.5, 0.7, 0.9), steady=None,
                  **level_kw) -> Qualification:
    """Every check above, in one order, on one rig. Returns the record; it does
    NOT raise -- call `.require()` for that.

    ORDER IS PART OF THE CONTRACT and it is the order the checks depend on
    each other in:

      1. pins        the rig's controls are where it says they are
      2. sounding    there is a signal to measure
      3. level       it is usable, and not at the rail
      4. pitch       it is the note that was commanded
      5. waveform    identified at the MEASURED f0 from step 4
      6. pitch causality      +`semitones` doubles the measured pitch
      7. filter causality     the cutoff moves the centroid monotonically

    Steps 6 and 7 are the expensive ones (2 and `len(cutoff_knobs)` extra
    renders), and they come last for that reason only -- they are NOT
    conditional on the earlier ones. A rig that fails the level check still
    gets its causality measured, because "the level is wrong AND the knob is
    disconnected" and "the level is wrong" are different findings and the
    second one sends the next person to the wrong place.

    `steady(y)` optionally trims the record to the part a periodic estimator
    may read -- past the attack, before the release. It is applied to the
    pitch and waveform checks only; the level check sees the WHOLE record,
    because an attack transient that hits the rail is exactly the clipping
    this battery is looking for."""
    steady = steady or (lambda y: y)
    checks = []
    if check_pins_fn is not None or n_pins is not None:
        checks.append(check_pins(check_pins_fn or (lambda: []), n_pins=n_pins))

    y = np.asarray(render_note(int(note)), dtype=np.float64).ravel()
    checks.append(check_sounding(y))
    checks.append(check_level(y, **level_kw))

    cmd_hz = float(note_hz(int(note)))
    ysteady = np.asarray(steady(y), dtype=np.float64).ravel()
    pitch = check_pitch(ysteady, cmd_hz, sr=sr)
    checks.append(pitch)

    # The MEASURED fundamental where there is one, and no waveform claim at all
    # where there is not. Falling back to the commanded value here would ask
    # `waveform_id` whether an octave-down record repeats at the note that was
    # asked for -- it does not, and the refusal would name a period residual
    # instead of the octave. That is a wrong reason, which is worse than none.
    #
    # `subharmonic` FIRST, and this was wrong before it was right. On an
    # octave-down record `refine_f0` reports `f0_measured` as the strongest
    # component NEAR the command -- which is the record's SECOND harmonic,
    # sitting exactly on the commanded note -- and `subharmonic` as the
    # fundamental it found below it. Reading `f0_measured` here handed
    # `waveform_id` 261.6 Hz for a 130.8 Hz saw, which refused with a 173.6 %
    # period residual: the octave-down row of the discrimination matrix was
    # green for the wrong reason. Caught by that matrix, not by inspection.
    f0 = (pitch.detail.get("f0_hz") or pitch.detail.get("subharmonic")
          or pitch.detail.get("f0_measured"))
    if f0:
        checks.append(check_waveform(ysteady, float(f0), sr=sr, expect=expect_wave))
    else:
        checks.append(Check("waveform", REFUSED,
                            "no fundamental was measured, so there is no period to "
                            "identify a waveform over",
                            {"expect": expect_wave, "pitch_outcome": pitch.outcome}))

    checks.append(check_pitch_causality(render_note, int(note), note_hz, sr=sr,
                                       semitones=semitones))
    checks.append(check_filter_causality(render_cutoff, cutoff_knobs, sr=sr))
    return Qualification(rig=rig, host=host, checks=tuple(checks))

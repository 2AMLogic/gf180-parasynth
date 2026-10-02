#!/usr/bin/env python3
"""Controls for the ground-truth suite: start red, then named mutants (#517).

    python3 tools/probes/estimator_ground_truth.py controls
    python3 tools/probes/estimator_ground_truth_controls.py       # same thing

`estimator_ground_truth.py check` is a gate, and `docs/verification-rules.md`
rule 1 is why it cannot be trusted on its own: *a harness nobody has watched
fail is not a harness.* Four harnesses in this repository had never been
observed to fail, and every one of them looked exactly like a passing one.
This file is the other half -- the runs in which the suite is REQUIRED to go
red -- and it is what makes `check`'s green meaningful.

WHAT IT DOES
------------
1. **Start red (rule 1).** The suite is run against two stub modules with
   `audio_measure`'s names and no behaviour: one that answers a confident
   constant (the analogue of an RTL output stuck at a value) and one that
   refuses everything (the analogue of X). Every check that stays GREEN
   against a stub is a check that would pass for a dead estimator, and is
   printed as a HOLE unless `STUB_MAY_PASS` states why it has no power.

2. **Named mutants (rule 2 / rule 5).** Each entry in `MUTANTS` is one
   plausible way an estimator could be wrong -- a knob a caller could have got
   wrong, a gate removed, a weighting swapped -- declared against the ONE
   (estimator, family) pair it must turn red. `any check went red` is NOT the
   verdict: a mutant that breaks something unrelated would pass that test
   while the check it was written for stayed blind, which is the hole the
   precedent in `tools/measure_promoted_bands.py` exists to prevent.

3. **The matrix (rule 4).** For every mutant, each check is printed MOVED (it
   saw the defect) or BLIND (it did not). A check BLIND to every mutant is
   decoration, and the summary names those checks explicitly. BLIND is not a
   failure on its own -- `corner_3db` cannot see a defect in `step_ratio` --
   but a check blind to ALL of them has never been observed to do anything.

THE MUTANT THAT HOLDS #517'S OWN REPAIR
---------------------------------------
`damped_sinusoid` reported a confident tau on records holding two decay
constants: 93.3 ms for a pair at 200/50 ms, with a fit residual comfortably
inside its own gate. The repair is the half-split consistency check in
`model/audio_measure.py`, and two mutants here remove it -- one by widening
`DAMPED_HALF_TAU_RATIO` to infinity, one by disabling `_ar2_tau` so the check
has nothing to compare -- because a repair with no injection is not closed
(rule 5).

WHAT THIS RUN HAS ALREADY FOUND, so its value is a record and not a claim
-------------------------------------------------------------------------
Run for the first time against a suite that was already fully green, the
start-red stubs found THREE checks that a dead estimator would also have
passed -- `clipped_fraction` (asserted only that nothing sits beyond the known
peak, which a stub answering 0.0 satisfies), `plateau_db` / `dc_plateau_db`
(compared a unity-gain passband against 0 dB, which a constant also gives) and
`harmonic_signature` (reported NOT-MEASURED for a signature containing no
harmonics at all) -- plus five branches that printed a number and gated
nothing while reporting PASS. All are repaired in
`estimator_ground_truth.py`; the point worth keeping is that reading the suite
would not have found them and running this did.

WHY THE CONTROLS RUN ON A REDUCED CATALOGUE, AND WHAT THAT COSTS
----------------------------------------------------------------
`check` runs 170 fixtures in about 15 s. Twenty-odd mutants at that price is
five minutes of a shared build box for a result that does not change between
them, so the controls run `CONTROL_PER_FAMILY` records per family instead
(the first few of each family's sweep, deterministically, plus every record
`MUTANTS` names). That is the whole cost and it is stated rather than hidden:
**a defect visible only at an extreme swept value -- the 7.1 kHz member, the
0.001 level, the 20 dB SNR -- would be BLIND here and still caught by
`check`.** The controls answer "can this check see this defect at all", not
"over what domain".
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT / "model"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import audio_measure as am                                           # noqa: E402
import estimator_fixtures as ef                                      # noqa: E402
import estimator_ground_truth as gt                                  # noqa: E402

#: How many records of each family the control runs use. See the docstring:
#: this is a cost decision with a stated blind spot, not a claim that the
#: extremes do not matter.
#:
#: EVENLY SPACED THROUGH THE FAMILY, not the first N, and that is not a
#: detail: the first four records of `damped_sine` are its frequency sweep at
#: one phase, and at that phase the largest excursion of the record happens to
#: be POSITIVE -- so the "peak reads the largest positive sample instead of
#: the largest magnitude" mutant was invisible on all four, and the control
#: reported NOT-CAUGHT for a defect the suite does catch one record later.
CONTROL_PER_FAMILY = 6


# ===========================================================================
# the stub module: `audio_measure`'s names, no behaviour
# ===========================================================================
#: Return shape per estimator, for the six whose signature carries no return
#: annotation. Everything else is read from the annotation, so a new estimator
#: with an annotation needs no entry here -- and one WITHOUT an annotation and
#: without an entry makes the stub raise, which shows up as a red run rather
#: than as a plausible answer.
UNANNOTATED_SHAPES = {
    "cycle_average": "cycle",
    "envelope_bursts": "list",
    "require_finite": "np.ndarray",
    "spectrum": "curve",
    "transfer": "curve",
    "waveform_matches": "match",
}


class _StubFailed(Exception):
    """Raised by a stub asked for a shape nobody declared. It propagates into
    `gt.run`'s own handler and becomes a FAIL, which is the correct outcome
    for a stub: what must never happen is a stub returning something that
    looks like an answer."""


def _shape(name: str) -> str:
    import inspect
    if name in UNANNOTATED_SHAPES:
        return UNANNOTATED_SHAPES[name]
    ann = inspect.signature(getattr(am, name)).return_annotation
    if ann is inspect.Signature.empty:
        raise _StubFailed(f"{name} has no return annotation and no entry in "
                          f"UNANNOTATED_SHAPES")
    return str(ann)


def _stub_return(shape: str, mode: str, args):
    """The degenerate value of `shape`. `mode` is 'constant' or 'refuse'."""
    refuse = mode == "refuse"
    x = np.asarray(args[0], float) if args is not None and len(args) else np.zeros(8)
    if shape == "Estimate":
        return (am.Estimate(None, False, "stub: no behaviour", {}) if refuse
                else am.Estimate(0.0, True, "", {}))
    if shape == "Damped":
        e = _stub_return("Estimate", mode, args)
        return am.Damped(e, e, 0.0, 0.0, 0.0)
    if shape == "Comparison":
        return am.Comparison(0.0, 0.0, 0.0)
    if shape == "LineStats":
        return am.LineStats(0, 0.0, np.zeros(0))
    if shape == "WaveformID":
        return (am.WaveformID(None, False, "stub: no behaviour", {}) if refuse
                else am.WaveformID("sine", True, "", {}))
    if shape == "bool":
        return refuse                      # is_silent: False, then True
    if shape == "dict":
        return {"ok": False, "reason": "stub"} if refuse else {"ok": True}
    if shape == "dict | None":
        return {"stub": True} if refuse else None
    if shape == "float":
        return 0.0
    if shape == "int":
        return 0
    if shape in ("list", "list[int]"):
        return []
    if shape == "np.ndarray":
        return np.zeros(len(x))
    if shape == "cycle":                   # cycle_average -> (period, residual)
        return np.zeros(1024), 0.0
    if shape == "curve":                   # spectrum / transfer -> (f, mag)
        return np.linspace(0.0, 1.0, 8), np.zeros(8)
    if shape == "match":                   # waveform_matches -> (ok, why)
        return (False, "stub") if refuse else (True, "")
    raise _StubFailed(f"no degenerate value declared for shape {shape!r}")


class StubModule:
    """A stand-in for `audio_measure`: constants pass through, every callable
    answers the degenerate value for its own return shape.

    `estimator_ground_truth` reaches the module only as `am.<name>`, so
    replacing `gt.am` with this swaps the whole thing under the suite without
    touching the real module -- which matters, because a half-patched module
    would make the stub run a measurement of something.
    """

    def __init__(self, mode: str):
        self.mode = mode

    def __getattr__(self, name):
        real = getattr(am, name)
        if not callable(real) or isinstance(real, type):
            return real                                   # constants, classes
        shape = _shape(name)

        def stub(*args, **kw):
            if name == "require_finite" and self.mode == "refuse":
                raise am.NonFiniteAudio("stub")
            return _stub_return(shape, self.mode, args)
        stub.__name__ = f"stub_{name}"
        return stub


#: An (estimator, family) pair the stub is ALLOWED to leave green, with the
#: reason it has no power there. Anything else green is a HOLE: a check that
#: would pass for an estimator with no behaviour at all.
#:
#: PER PAIR, not per estimator, because power is a property of the record:
#: `onsets` goes red against a stub on every family that HAS a known onset,
#: and cannot on the one whose known answer is 'no onsets' -- the empty list a
#: dead estimator also returns. Allowing the estimator everywhere would have
#: hidden the other six.
#:
#: WHAT DEFEATS THIS LIST, said out loud: it is a list of excuses, and an
#: excuse is cheap to write. Nothing here can tell a correct reason from a
#: plausible one, so the list is deliberately short, every entry names the
#: mechanism rather than the inconvenience, and the count is printed in the
#: run so a reader sees it growing.
STUB_MAY_PASS = {
    "constant": {
        ("compare", None):
            "a signal against twice itself is a RATIO, and a constant answer "
            "cancels a gain by construction -- the same reason "
            "measure_promoted_bands' 'gain cancels' case is excluded from its "
            "own stub count",
        ("onsets", "harmonic_mixture"):
            "the KNOWN answer on a record that begins at full amplitude is 'no "
            "onsets', i.e. the empty list, which is also what an estimator "
            "with no behaviour returns. The other six families carry this "
            "check against the stub",
        ("envelope_bursts", "repeated_hits"):
            "the stub's burst list is empty and the known count on the "
            "quietest-hit members is also reachable as zero; the gated count "
            "lives on the clean unequal-level members, which the full `check` "
            "run covers",
    },
    "refuse": {
        ("onsets", "harmonic_mixture"):
            "as above: 'no onsets' is the known answer and the empty list is "
            "the stub's answer",
        ("envelope_bursts", "repeated_hits"): "as above",
    },
}


def _stub_allowed(mode, pair) -> str | None:
    allow = STUB_MAY_PASS[mode]
    if pair in allow:
        return allow[pair]
    return allow.get((pair[0], None))


# ===========================================================================
# the mutants
# ===========================================================================
def _wrap(name, **kw):
    """`am.<name>` with `kw` forced -- a knob a caller could have got wrong.

    Forced, not defaulted: a mutant that the shipped call site overrides is a
    control that cannot activate, which is worse than no control (it reports
    CAUGHT for nothing). Every entry below is checked against that by the
    named-pair contract in `injected()`."""
    real = getattr(am, name)

    def mutant(*a, **k):
        return real(*a, **dict(k, **kw))
    mutant.__name__ = f"mutant_{name}"
    return mutant


#: EVERY MUTANT IS BUILT BY A FACTORY, and that is not a style choice: a
#: mutant written as a plain function that calls `am.<the name it replaces>`
#: recurses forever, because by the time it runs that name IS the mutant. The
#: factories below capture the original at build time, which happens before
#: the patch.
def _rms_as_mean_abs():
    return lambda x, *a, **k: float(np.mean(np.abs(np.asarray(x, float))))


def _peak_positive_only():
    return lambda x, *a, **k: float(np.max(np.asarray(x, float)))


def _rescaled_estimate(name, factor):
    """`am.<name>` with its value multiplied by `factor` -- the shape of every
    unit confusion in this module's history (tau against T20, ms against s)."""
    real = getattr(am, name)

    def mutant(*a, **k):
        e = real(*a, **k)
        return (am.Estimate(e.value * factor, True, "",
                            dict(e.detail or {}, mutant=f"x{factor:.6g}"))
                if e.ok else e)
    return mutant


def _ar2_tau_blind():
    """`_ar2_tau` answering 'too few samples' for every segment.

    'too few samples' is one of `damped_sinusoid`'s own SOFT reasons, so both
    halves come back unresolved for a benign reason and neither clause of the
    half-split check fires: this is the pre-#517 behaviour exactly, reached
    without reverting the function."""
    return lambda *a, **k: (None, "too few samples")


def _onsets_late():
    real = am.onsets

    def mutant(x, sr=48000, **k):
        return [i + int(0.020 * sr) for i in real(x, sr, **k)]
    return mutant


def _dominant_frequency_band_centre():
    return lambda x, lo, hi, sr=48000, **k: am.Estimate(
        0.5 * (lo + hi), True, "", dict(mutant="band centre"))


def _transfer_magnitude_squared():
    real = am.transfer

    def mutant(ir, sr=48000):
        f, mag = real(ir, sr)
        return f, mag ** 2
    return mutant


def _tonality_over_the_whole_spectrum():
    """`tonality_db` ignoring the band it was handed.

    The defect `c_tonality_db`'s own comment records measuring: over
    (20 Hz, 0.45 fs) a 4th-order lowpass reads 92.5 dB of 'tonality', because
    the median bin of that band sits in the stopband while the peak sits in
    the passband, so peak-over-median returns the filter's dynamic range.

    NOT peak-over-mean, which was the first draft of this mutant: the
    geometric difference between the mean and the median of exponential
    periodogram bins is 10 log10(ln 2) = -1.6 dB, well inside the check's own
    6 dB gate, so that mutant could not activate. Recorded here rather than
    shipped, because a control that cannot fire reports CAUGHT for nothing."""
    real = am.tonality_db

    def mutant(x, band=(20.0, 20000.0), sr=48000):
        return real(x, (20.0, 0.45 * sr), sr)
    return mutant


def _band_energy_unnormalised():
    """Band ENERGIES where band FRACTIONS were asked for: the normalisation
    forgotten, which is what makes the answer depend on the record's level."""
    real = am.band_energy

    def mutant(x, edges, sr=48000, **k):
        frac = real(x, edges, sr, **k)
        return frac * max(float(np.sum(np.asarray(x, float) ** 2)), 1e-30)
    return mutant


def _corner_from_curve_as_highpass():
    """A lowpass curve read with `kind='highpass'`: the plateau taken from the
    wrong end, so the corner is looked for in the stopband."""
    real = am.corner_from_curve

    def mutant(freqs, gain_db, **k):
        return real(freqs, gain_db, **dict(k, kind="highpass"))
    return mutant


def _step_ratio_over_max():
    def mutant(x):
        d = np.abs(np.diff(np.asarray(x, float)))
        if not len(d) or d.max() <= 0:
            return 0.0
        return float(d.max() / d.max())
    return mutant


def _envelope_ripple_from_std():
    def mutant(env, sr=48000, **k):
        e = np.asarray(env, float)
        m = float(np.mean(np.abs(e)))
        if m <= 0:
            return am.Estimate(None, False, "silent", {})
        return am.Estimate(float(20.0 * math.log10(max(np.std(e), 1e-300) / m)),
                           True, "", dict(mutant="std of the envelope itself"))
    return mutant


def _segment_shape_without_the_half():
    real = am.segment_shape

    def mutant(y, sr=48000):
        e = real(y, sr)
        return (am.Estimate(e.value + 0.5, True, "", e.detail) if e.ok else e)
    return mutant


def _analytic_envelope_abs_of_the_signal():
    return lambda x: np.abs(np.asarray(x, float))


def _instantaneous_frequency_wrong_sample_rate():
    """A 48 kHz record read as 50 kHz: every reading 4.2 % high."""
    real = am.instantaneous_frequency

    def mutant(x, sr=48000, smooth_ms=0.0):
        return real(x, sr, smooth_ms) * (50000.0 / 48000.0)
    return mutant


def _harmonic_powers_off_f0():
    real = am.harmonic_powers

    def mutant(x, f0, ks, sr=48000, **k):
        return real(x, f0 * 1.01, ks, sr, **k)
    return mutant


#: (label, attribute on `audio_measure`, factory, the ONE pair it must redden)
#:
#: Each label is a defect CLASS, not a typo: a wrong weighting, a removed
#: gate, a sample rate taken from the wrong clip, a dB point read 3 dB too
#: low. `named` is (estimator, family) and must be PASS/IDENTIFIED clean and
#: RED under the mutant -- `injected()` checks both halves, because a named
#: pair that is NOT-MEASURED clean would report CAUGHT for a check that never
#: ran.
MUTANTS = (
    ("rms as the mean of |x| instead of its root mean square",
     "rms", _rms_as_mean_abs, ("rms", "damped_sine")),
    ("peak as the largest POSITIVE sample instead of the largest magnitude",
     "peak", _peak_positive_only, ("peak", "damped_sine")),
    ("sounding_extent's silence floor 1e-3 of peak instead of 1e-9",
     "sounding_extent", lambda: _wrap("sounding_extent", floor=1e-3),
     ("sounding_extent", "damped_sine")),
    ("decay_tau returns T20 where a tau was asked for (the ln 10 confusion)",
     "decay_tau", lambda: _rescaled_estimate("decay_tau", math.log(10.0)),
     ("decay_tau", "damped_sine")),
    # NOT HERE EITHER: "decay_tau's cycles-per-tau precondition disabled"
    # (`min_cycles_per_tau=None`) cannot activate on this catalogue, and the
    # measurement that says so is worth more than the mutant would have been.
    # Over every clean damped_sine record below the 1.5 cycles-per-tau gate,
    # plus a 4x4 search around it (f = 40..130 Hz, tau = 6..30 ms), the
    # ungated estimator either refuses anyway -- for its own residual or
    # range reasons, not the precondition -- or answers within 3.2 % of the
    # synthesised tau: 30.29 ms for 30 ms at 1.20 cycles, 9.68 ms for 10 ms at
    # 1.30 cycles. So the precondition is CONSERVATIVE on clean synthetic
    # records: it refuses records whose tau it would have got right. That is
    # evidence about the declared domain (#115), and it means there is no
    # record here on which removing the gate produces a wrong number.
    ("schroeder_t20 returns tau where a T20 was asked for",
     "schroeder_t20",
     lambda: _rescaled_estimate("schroeder_t20", 1.0 / math.log(10.0)),
     ("schroeder_t20", "damped_sine")),
    # NOT HERE, and the reason is the same design point as
    # measure_promoted_bands' note about `DEFAULT_LOWBAND_HZ`: a mutant that
    # narrows `schroeder_t20`'s fit range to -5..-15 dB CANNOT ACTIVATE,
    # because the function fits between lo_db and hi_db and then SCALES the
    # slope to 20 dB. On an exponential the slope is the same over either
    # range, so the reading does not move and the control would report CAUGHT
    # for nothing. The rescale mutant above is the one with power.
    ("damped_sinusoid's half-split tau ratio widened to infinity (#517's "
     "repair, removed through its own constant -- read at call time, so the "
     "mutant reaches the shipped body)",
     "DAMPED_HALF_TAU_RATIO", lambda: float("inf"),
     ("damped_sinusoid", "two_modes")),
    ("damped_sinusoid's half-split check blinded at the source: both halves "
     "report a soft reason, which is the pre-#517 behaviour",
     "_ar2_tau", _ar2_tau_blind, ("damped_sinusoid", "two_modes")),
    ("onsets reported 20 ms late (twice the 10 ms the Hilbert precursor costs)",
     "onsets", _onsets_late, ("onsets", "repeated_hits")),
    ("zero_crossing_frequency counting crossings in both directions",
     "zero_crossing_frequency",
     lambda: _rescaled_estimate("zero_crossing_frequency", 2.0),
     ("zero_crossing_frequency", "damped_sine")),
    ("dominant_frequency answers the centre of the band it was handed",
     "dominant_frequency", _dominant_frequency_band_centre,
     ("dominant_frequency", "damped_sine")),
    ("instantaneous_frequency reading a 48 kHz record as 50 kHz",
     "instantaneous_frequency", _instantaneous_frequency_wrong_sample_rate,
     ("instantaneous_frequency", "damped_sine")),
    ("analytic_envelope as |x| instead of the analytic magnitude (the "
     "rectified-envelope mistake)",
     "analytic_envelope", _analytic_envelope_abs_of_the_signal,
     ("analytic_envelope", "damped_sine")),
    ("spectral_centroid weighted by amplitude instead of power",
     "spectral_centroid", lambda: _wrap("spectral_centroid", weight="amplitude"),
     ("spectral_centroid", "harmonic_mixture")),
    ("tonality_db ignoring the band it was handed and reading the whole "
     "spectrum, so a filter's dynamic range reads as tonality",
     "tonality_db", _tonality_over_the_whole_spectrum,
     ("tonality_db", "filtered_noise")),
    ("band_energy returning band energies where band FRACTIONS were asked "
     "for (the normalisation forgotten)",
     "band_energy", _band_energy_unnormalised,
     ("band_energy", "filtered_noise")),
    ("transfer returns |H|^2 instead of |H| (a power/amplitude confusion, and "
     "the one corner_3db, resonant_peak and bandwidth_q all read through)",
     "transfer", _transfer_magnitude_squared,
     ("transfer", "filtered_noise")),
    ("corner_from_curve reading a lowpass curve as a highpass, so the "
     "plateau comes from the wrong end of it",
     "corner_from_curve", _corner_from_curve_as_highpass,
     ("corner_from_curve", "filtered_noise")),
    ("envelope_ripple_db as the envelope's own standard deviation, with no "
     "high-pass (so a curve reads as stepping)",
     "envelope_ripple_db", _envelope_ripple_from_std,
     ("envelope_ripple_db", "envelope_plus_noise")),
    ("segment_shape without the 0.5 that makes a straight ramp read zero",
     "segment_shape", _segment_shape_without_the_half,
     ("segment_shape", "envelope_plus_noise")),
    ("step_ratio divided by the largest step instead of the mean step",
     "step_ratio", _step_ratio_over_max,
     ("step_ratio", "harmonic_mixture")),
    ("repeat_period with its minimum-lag floor removed",
     "repeat_period", lambda: _wrap("repeat_period", min_lag_s=0.0),
     ("repeat_period", "damped_sine")),
    ("harmonic_powers read 1 % off the commanded f0",
     "harmonic_powers", _harmonic_powers_off_f0,
     ("harmonic_powers", "harmonic_mixture")),
)


# ===========================================================================
# the run
# ===========================================================================
def control_fixtures():
    """`CONTROL_PER_FAMILY` records of each family, evenly spaced through its
    sweeps so that more than one axis is represented. Deterministic."""
    out = []
    for kind in ef.FAMILIES:
        fx = ef.catalogue([kind])
        if len(fx) <= CONTROL_PER_FAMILY:
            out.extend(fx)
            continue
        step = len(fx) / CONTROL_PER_FAMILY
        out.extend([fx[int(i * step)] for i in range(CONTROL_PER_FAMILY)])
    return out


def _pairs(verdicts) -> dict:
    """(estimator, family) -> the SET of statuses seen over the control set.

    A set and not a single worst status, because the two questions asked of a
    pair are different: 'did any record go red' (the mutant was seen) and 'did
    any record answer at all' (the pair has power). Collapsing to one status
    answers neither cleanly -- a pair with three PASSes and one NOT-MEASURED
    would drop out of the population under a `worst` rule and so could never
    be reported as a hole."""
    out = {}
    for v in verdicts:
        out.setdefault((v.estimator, v.kind), set()).add(v.status)
    return out


def _run(fixtures, am_module=None) -> dict:
    saved = gt.am
    if am_module is not None:
        gt.am = am_module
    try:
        return _pairs(gt.run(list(ef.FAMILIES), None, fixtures))
    finally:
        gt.am = saved


def _run_mutated(fixtures, attr, replacement) -> dict:
    saved = getattr(am, attr)
    setattr(am, attr, replacement)
    try:
        return _pairs(gt.run(list(ef.FAMILIES), None, fixtures))
    finally:
        setattr(am, attr, saved)


def _any_red(statuses) -> bool:
    return bool(set(statuses or ()) & set(gt.RED))


def _answered(statuses) -> bool:
    """Did this pair produce a READING? PASS or IDENTIFIED only.

    Used for the stub runs. REFUSED and INSUFFICIENT are green but carry no
    number, so a stub that refuses everything leaves them green for the right
    reason; counting them as 'answered' would turn every correct refusal into
    a reported hole."""
    return bool(set(statuses or ()) & {gt.PASS, gt.IDENTIFIED})


def _had_power(statuses) -> bool:
    """Did this pair reach a VERDICT, of any green kind, with nothing red?

    Used for the mutant runs, and it is a different question from `_answered`:
    `damped_sinusoid` on a beating pair is REFUSED in the clean run, and that
    refusal IS the thing #517 repaired -- the mutant has to turn it into a
    confident wrong number. Requiring a reading there would have declared the
    control for this issue's own repair impossible to run."""
    s = set(statuses or ())
    return bool(s & set(gt.GREEN)) and not (s & set(gt.RED))


def start_red(fixtures, clean) -> list:
    """Rule 1. (mode, reddened pairs, holes) for each stub, where a hole is a
    check that ANSWERED under the stub and was not allowed to."""
    rows = []
    for mode in ("constant", "refuse"):
        got = _run(fixtures, StubModule(mode))
        red = sorted(k for k, s in got.items() if _any_red(s))
        holes = sorted((k, sorted(s)) for k, s in got.items()
                       if _answered(s) and _answered(clean.get(k))
                       and not _any_red(s)
                       and _stub_allowed(mode, k) is None)
        rows.append((mode, red, holes))
    return rows


def injected(fixtures, clean) -> list:
    """Rule 2 + rule 4. (label, named, caught, moved, blind, note) per mutant."""
    checks = sorted({k[0] for k in clean})
    full_clean: dict = {}
    rows = []
    for label, attr, make, named in MUTANTS:
        if not hasattr(am, attr):
            rows.append((label, named, False, [], checks,
                         f"audio_measure has no attribute {attr!r}: this "
                         f"control cannot activate"))
            continue
        got = _run_mutated(fixtures, attr, make())
        moved = sorted({k[0] for k, s in got.items()
                        if _any_red(s) and not _any_red(clean.get(k))})
        blind = [c for c in checks if c not in moved]
        before, note = clean.get(named), ""
        if not _had_power(before):
            # THE REDUCED CONTROL SET DID NOT CARRY THIS PAIR, which is a
            # property of the sampling and not of the mutant: `step_ratio`'s
            # closed form lives on the four NAIVE members of
            # harmonic_mixture, and six evenly-spaced records of twenty-nine
            # need not include one. Re-run this mutant over the WHOLE of the
            # named family rather than reporting NOT-CAUGHT for a control that
            # was never handed its own case. The retry is announced, because
            # the alternative -- a silent widening -- would make the cost
            # claim in this file's docstring untrue without saying so.
            fam = ef.catalogue([named[1]])
            if named[1] not in full_clean:
                full_clean[named[1]] = _run(fam)
            before = full_clean[named[1]].get(named)
            got_full = _run_mutated(fam, attr, make())
            got = {**got, **got_full}
            note = (f"[retried over the whole {named[1]} family: the reduced "
                    f"control set does not carry this pair] ")
            moved = sorted(set(moved) | {k[0] for k, s in got_full.items()
                                         if _any_red(s)
                                         and not _any_red(full_clean[named[1]].get(k))})
            blind = [c for c in checks if c not in moved]
        if not _had_power(before):
            rows.append((label, named, False, moved, blind,
                         note + f"the named pair reads {sorted(before or [])} "
                                f"in the clean run, so it reaches no green "
                                f"verdict this mutant could have turned red"))
            continue
        caught = _any_red(got.get(named))
        rows.append((label, named, caught, moved, blind,
                     note + f"{sorted(got.get(named) or [])} under the mutant; "
                            f"{len(moved)} check(s) moved"))
    return rows


def cmd_controls(args=None) -> int:
    fixtures = control_fixtures()
    print("=" * 78)
    print("estimator ground truth: CONTROLS (start red, then named mutants)")
    print("=" * 78)
    print(f"  {len(fixtures)} control records "
          f"({CONTROL_PER_FAMILY} per family of {len(ef.FAMILIES)}), "
          f"{len(gt.SPECS)} checks, {len(MUTANTS)} mutants")
    clean = _run(fixtures)
    answered = sorted(k for k, s in clean.items() if _answered(s))
    red = sorted(k for k, s in clean.items() if _any_red(s))
    print(f"  clean run: {len(answered)} (estimator, family) pairs answered, "
          f"{len(red)} red")
    bad = 0
    if red:
        bad += len(red)
        for k in red:
            print(f"  FAIL clean run is already red: {k[0]} on {k[1]} "
                  f"({sorted(clean[k])})")

    print("\n" + "-" * 78)
    print("1. Start red (rule 1): the suite against a stub with no behaviour")
    print("-" * 78)
    stub_moved = set()
    for mode, reds, holes in start_red(fixtures, clean):
        stub_moved |= {k[0] for k in reds}
        print(f"  {'RED ' if not holes else 'HOLE'} stub '{mode}': "
              f"{len(reds)} pair(s) red, {len(holes)} green with no stated "
              f"reason")
        for (est, kind), status in holes:
            print(f"       HOLE {est} on {kind} is {status} against a stub "
                  f"that has no behaviour")
        bad += len(holes)
        for (est, kind), why in sorted(STUB_MAY_PASS[mode].items(),
                                       key=lambda kv: kv[0][0]):
            print(f"       allowed green: {est} on "
                  f"{kind or 'every family'} -- {why}")

    print("\n" + "-" * 78)
    print("2. Named mutants (rule 2): each must redden the check it names")
    print("-" * 78)
    rows = injected(fixtures, clean)
    for label, named, caught, moved, _blind, note in rows:
        print(f"  {'CAUGHT    ' if caught else 'NOT-CAUGHT'} "
              f"{named[0]} on {named[1]}: {label}")
        print(f"             -> {note}")
        if not caught:
            bad += 1

    print("\n" + "-" * 78)
    print("3. Checks x defects (rule 4): MOVED for the checks that saw it")
    print("-" * 78)
    for label, _named, _caught, moved, blind, _note in rows:
        print(f"  {label}")
        print(f"       MOVED ({len(moved)}): {', '.join(moved) or '-'}")
        print(f"       BLIND ({len(blind)})")
    all_checks = {k[0] for k in clean}
    never = sorted(set.intersection(*[set(b) for _l, _n, _c, _m, b, _no in rows])
                   if rows else all_checks)
    print("\n" + "-" * 78)
    print("4. Checks never observed to fail, in any control run")
    print("-" * 78)
    print(f"  BLIND to every mutant: {len(never)} of {len(all_checks)}. That "
          f"is a gap in THIS file's mutant list, not necessarily in the "
          f"check -- `corner_3db` cannot see a defect in `step_ratio`.")
    print(f"  Of those, {len(sorted(set(never) & stub_moved))} were reddened "
          f"by a start-red stub, so they HAVE been watched fail.")
    # THE ONE THAT IS GATED. A check that ANSWERED in the clean run and was
    # reddened by neither a mutant nor a stub has never been observed to do
    # anything, which is rule 1's whole subject.
    #
    # Restricted to the checks that answered, and that restriction is the
    # difference between a gate and an unsatisfiable one: the first draft of
    # this gate fired on `duty_cycle`, `pulse_edges` and `harmonic_signature`,
    # all three of which are NOT-MEASURED throughout the reduced control set
    # (their closed forms live on members it does not sample). 'Never red' says
    # nothing about a check that never ran, so counting those would have been
    # a gate that reports a hole in the sampling as a hole in the suite.
    answered_checks = {k[0] for k, s in clean.items() if _answered(s)}
    decoration = sorted((set(never) & answered_checks) - stub_moved
                        - {s.name for s in gt.SPECS if not s.applies_to})
    print(f"  NEVER RED ANYWHERE: {len(decoration)}")
    for c in decoration:
        print(f"       FAIL never-red {c}: no mutant and neither stub moved "
              f"this check, so nothing has shown it can fail")
    bad += len(decoration)
    for c in never:
        print(f"       blind-to-all-mutants {c}"
              f"{' (a stub reddened it)' if c in stub_moved else ''}")

    print("\n" + "=" * 78)
    if bad:
        print(f"controls: FAIL ({bad})")
        return 1
    print(f"controls: PASS -- {len(MUTANTS)} mutants each reddened the check "
          f"they name, and both stubs were watched fail")
    return 0


def add_parser(sub):
    """Registered by `estimator_ground_truth.main`, so `check` and `controls`
    are one command with two subcommands rather than two scripts."""
    c = sub.add_parser("controls", help="start red, then the named mutants")
    c.set_defaults(fn=cmd_controls)
    return c


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.parse_args(argv)
    return cmd_controls()


if __name__ == "__main__":
    raise SystemExit(main())

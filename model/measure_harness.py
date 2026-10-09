#!/usr/bin/env python3
"""Validation methodology for acoustic measurements -- not DSP primitives.

`model/audio_measure.py` already holds this repo's general DSP primitives
(spectrum, envelope, decay, resonance estimators). This module holds the
*methodology* that says whether one of those estimators, wired up for a
particular measurement, can be trusted at all: known-answer controls,
noise-floor characterization, windowing/apparatus-agreement checks, and
cross-correlation "is this actually a second recording" checks. It does not
import `audio_measure` itself: callers wire those primitives into the closures
they pass in, so nothing here re-derives them.

Extracted from `tools/measure_conga_body_spread.py` (issue #104): that tool
built `validate_known_answer`, `floor_for_these_signals`, `windowed_alike` and
`descent_test` for the conga body-spectrum measurement specifically, but none
of the four is conga-specific in its *shape* -- only in the closures a caller
plugs into it. A second agent, working the hi-hat/cymbal high band the same
session, ran probes with the same methodology and committed nothing; #104 is
the fix for both halves of that at once: one shared, tested module, and every
future measurement tool imports it rather than re-deriving it.

    from measure_harness import (validate_known_answer, floor_for_these_signals,
                                 windowed_alike, descent_test, assert_precondition)

WHAT EACH ONE IS FOR

* `validate_known_answer` -- run your estimator against a signal whose answer
  is known analytically, by construction, with no reference to anything this
  repository measured. "An estimator calibrated on our own model is not
  validated" (CLAUDE.md) is the failure mode this exists to catch; a probe
  calibrated the other way once reported 25 dB of separation that was window
  leakage.

* `floor_for_these_signals` -- the estimator's floor is NOT one constant
  (issue #92 withdrew a whole column of #61 over exactly that assumption).
  Report it per PERTURBATION and per SIGNAL: each perturbation changes only
  the measurement APPARATUS (window start, sample rate, bit depth, ...) and
  leaves the machine and the strike untouched, so any change in the reading
  is the apparatus, not the sound.

* `windowed_alike` -- the same signal read through two different alignment
  geometries should read alike; if it does not, the discrepancy IS the
  windowing artifact, and it belongs in front of the reader next to whatever
  score used the naive alignment. This is the general shape of "do two
  measurement pathways of the same thing agree" -- of which a windowing
  geometry is one instance and an offline emulator standing in for a
  fixed-point block (the hi-hat probes' `capture_cy` / `chain` apparatus) is
  another.

* `descent_test` -- is a candidate recording a RE-PRESSING of a known
  reference corpus, rather than an independent second source? Peak-normalised
  cross-correlation of the trimmed onset against every file of the claimed
  class, allowing a small resampling ratio because a re-pressing is usually
  pitch-shifted. A best correlation near 1.0 means the two carry no
  independent information -- the conga tool's run against `MC50.WAV` found
  exactly that. THIS IS THE CHECK EVERY FUTURE REFERENCE-CORPUS CLAIM SHOULD
  RUN: "a pack that says 808 on the tin" is only a second machine if it is
  a second recording, and that is checkable before anything downstream reads
  it as a second unit's worth of spread.

* `assert_precondition` -- REFUSE (do not report) when a measurement's own
  stated precondition does not hold. CLAUDE.md: "assert your apparatus's
  preconditions at the point of use, and REFUSE rather than report when they
  fail" -- `REFUSED` is a first-class outcome, distinct from pass and fail.
  The hi-hat probes reimplemented this ad hoc four times (an offline emulator
  reproducing a fixed-point render, a cached band-energy estimator matching
  the uncached one); this is the one copy.

Nothing here is trusted until `model/test_measure_harness.py` passes: every
function below has at least one injected-bug control that must turn red, in
the same style as `tools/test_measure_conga_body_spread.py`'s
`test_check_fails_an_injected_band_direction_error`.
"""
from __future__ import annotations

import math
import pathlib
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

# The best cross-correlation at or above which `descent_test` calls a
# candidate a re-pressing. One constant so a caller that relabels the verdict
# (as the conga tool does) derives its threshold from here rather than
# hard-coding a second copy that can drift.
MATCH_THRESHOLD = 0.95


Measure = Callable[[np.ndarray, int], tuple[float | None, str]]
DescentMeasure = Callable[[np.ndarray, int, dict], tuple[float | None, str]]


# ---------------------------------------------------------------------------
# 1. known-answer controls
# ---------------------------------------------------------------------------
def validate_known_answer(cases: Sequence[Mapping], measure: Measure) -> list[dict]:
    """Run `measure` on each case whose answer is known analytically.

    Each case is a mapping with:
      case:     a label for the row
      signal:   (x, sr) -- the synthetic probe signal, constructed so its true
                answer follows from its OWN construction (e.g. two sines'
                amplitude ratio), never from anything this repository measured
      expected: the known-true value, in whatever units `measure` returns
      kwargs:   optional dict of extra keyword arguments passed to `measure`
                (a band split, a pair of edges -- whatever varies case to case)

    Returns one dict per case: case, expected_db, measured_db, error_db, why
    (kept as `_db` for continuity with every existing caller; nothing here
    requires the unit actually be decibels -- `measure` decides that).
    """
    out = []
    for c in cases:
        x, sr = c["signal"]
        got, why = measure(x, sr, **c.get("kwargs", {}))
        want = float(c["expected"])
        out.append(dict(
            case=c["case"],
            expected_db=round(want, 4),
            measured_db=None if got is None else round(got, 4),
            error_db=None if got is None else round(got - want, 4),
            why=why,
        ))
    return out


# ---------------------------------------------------------------------------
# 2. noise-floor characterization, per signal and per perturbation
# ---------------------------------------------------------------------------
def floor_for_these_signals(signals: Iterable[tuple]) -> list[dict]:
    """The estimator's floor, per PERTURBATION and per SIGNAL (issue #92: a
    floor that is not constant withdrew a whole column of #61 when treated as
    one). Each perturbation changes only the apparatus; the machine and the
    strike are untouched.

    `signals` is an iterable of (label, x, sr, measure, perturbations):
      label:         a dict merged verbatim into the output row (identifies
                     the file/case -- e.g. {"voice": "LC", "file": "LC50.WAV"})
      x, sr:         the untouched signal
      measure:       Callable[[ndarray, int], (float | None, str)] -- the
                     estimator under test, already bound to this signal's case
                     (e.g. which voice/band it is)
      perturbations: dict[str, Callable[[ndarray, int], float | None]] --
                     name -> a callable that returns the estimator's value
                     under one changed apparatus precondition (it may itself
                     re-measure a transformed signal, or compute the value by
                     any other route; what it returns is compared against this
                     signal's own base reading). Returns None when its own
                     precondition fails (e.g. a resample produced no energy).

    Each output row is `label` plus `base_db` and one `<name>_db` per
    perturbation, holding the SIGNED CHANGE from base -- not the raw reading.
    A perturbation that itself needs to report an unsigned "how far either
    geometry moved it" quantity (as the conga tool's `window_10ms` originally
    did) does so by returning `base + that_quantity`, so the same subtraction
    reconstructs it; that is between the caller and its perturbation, not this
    function's business.
    """
    out = []
    for label, x, sr, measure, perturbations in signals:
        base, _ = measure(x, sr)
        if base is None:
            continue
        row = dict(label, base_db=round(base, 4))
        for name, perturb in perturbations.items():
            v = perturb(x, sr)
            row[name] = None if v is None else round(v - base, 4)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 3. apparatus/windowing agreement
# ---------------------------------------------------------------------------
def windowed_alike(pairs: Iterable[Mapping]) -> list[dict]:
    """The scorecard distance when two sides of a comparison get the SAME
    measurement geometry, reported alongside the as-shipped (unaligned)
    comparison so the artifact's SIZE is visible next to the naive claim it
    corrupts.

    `pairs` is an iterable of mappings, each:
      label:      a dict merged verbatim into the output row
      tol:        the tolerance a `worst` figure is expressed in units of
      as_shipped: (reference_value, candidate_value) with no attempt at
                  alignment -- the historical/shipped comparison
      variants:   dict[name -> (reference_value, candidate_value)], any number
                  of aligned geometries (e.g. "both_lead_1ms", "both_lead_0ms")

    Each output row is `label` plus `as_shipped` and one entry per variant,
    each `{ref_db, ours_db, worst}` where `worst = |ours - ref| / tol`.
    """
    out = []
    for p in pairs:
        tol = float(p["tol"])

        def _entry(ref_v: float, cand_v: float) -> dict:
            return dict(ref_db=round(ref_v, 4), ours_db=round(cand_v, 4),
                        worst=round(abs(cand_v - ref_v) / tol, 4))

        row = dict(p["label"])
        row["as_shipped"] = _entry(*p["as_shipped"])
        for name, (ref_v, cand_v) in p["variants"].items():
            row[name] = _entry(ref_v, cand_v)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 4. descent test: is a candidate a re-pressing of a known reference corpus?
# ---------------------------------------------------------------------------
def _default_read_candidate(path: pathlib.Path) -> tuple[np.ndarray, int]:
    """Read one candidate file with `soundfile`, mixed to mono float64 at its
    own native rate. Any sample-rate normalisation belongs in a caller-supplied
    `read_candidate`, alongside whatever `prepare` assumes about its input."""
    import soundfile as sf
    x, sr = sf.read(str(path))
    if np.ndim(x) > 1:
        x = x.mean(axis=1)
    return np.asarray(x, dtype=np.float64), int(sr)


# Exceptions that mean "this file's data cannot be read" (soundfile's
# LibsndfileError is a RuntimeError). Everything else from a reader is a bug.
_DATA_ERRORS = (OSError, EOFError, ValueError, RuntimeError)


def descent_test(candidates: pathlib.Path,
                 classify: Callable[[pathlib.Path], dict | None],
                 read_ref: Callable[[pathlib.Path], tuple[np.ndarray, int]],
                 prepare: Callable[[np.ndarray, int], np.ndarray], *,
                 read_candidate: Callable[[pathlib.Path], tuple[np.ndarray, int]] | None = None,
                 window_s: float = 0.150,
                 measure: DescentMeasure | None = None,
                 measure_field: str = "measured_value",
                 min_ratio: float = 0.90, max_ratio: float = 1.1001,
                 ratio_step: float = 0.0025,
                 match_threshold: float = MATCH_THRESHOLD) -> list[dict]:
    """Is a candidate "recording" a RE-PRESSING of `classify`'s reference
    corpus, rather than an independent second source?

    "Different pressings are not different machines." A file that claims to
    be an independent source is only that if it is a second recording, and
    that is checkable: peak-normalised cross-correlation of the first
    `window_s` seconds against every reference file of the same class,
    allowing a small resampling ratio because a re-pressing is usually
    pitch-shifted. A best correlation at or above `match_threshold` (default
    0.95) is the same recording and carries no independent information. This
    is the check any future reference-corpus claim should run, not a
    conga-specific one -- see this module's docstring.

    `candidates`: directory of candidate files.
    `classify(path)`: returns `None` to skip a file, or a dict with at least
      `refs` (an iterable of reference wav paths to test against) plus
      whatever other keys (e.g. `voice`) should be merged into that file's
      output row.
    `read_ref(path)`: reads ONE reference file, returning (x, sr) in the same
      convention `prepare` expects (e.g. normalised to +-1).
    `prepare(x, sr)`: your apparatus's onset trim, applied identically to the
      candidate and every reference before correlating. Must not change the
      effective sample rate -- do any resampling in `read_candidate`/`read_ref`,
      where the returned `sr` can change alongside the array.
    `read_candidate(path)`: reads ONE candidate file, returning (x, sr) in the
      same convention as `read_ref`. Defaults to `soundfile`, mono-mixed, at
      the file's own rate; refuses outright (one row, `status=REFUSED`) rather
      than silently skipping every file when `soundfile` is absent and no
      override is given.
    `measure(x, sr, info)`: optional, reported under `measure_field` purely
      for context (e.g. the candidate's own body-spectrum reading, which needs
      `info["voice"]` to pick a band) -- NOT used to decide re-pressing, which
      is correlation alone.
    """
    from scipy.signal import correlate, resample
    if read_candidate is None:
        try:
            import soundfile  # noqa: F401  (import-only precondition check)
        except ImportError:
            return [dict(status="REFUSED", why="soundfile is not installed")]
        read_candidate = _default_read_candidate

    def _n(v: np.ndarray) -> np.ndarray:
        v = v - v.mean()
        k = float(np.linalg.norm(v))
        return v / k if k else v

    out = []
    for path in sorted(candidates.iterdir()):
        info = classify(path)
        if not info:
            continue
        info = dict(info)
        refs = list(info.pop("refs"))
        if not refs:
            continue
        try:
            x, sr = read_candidate(path)
        except _DATA_ERRORS as e:
            # The FILE is bad (truncated, wrong format): unavailable data.
            out.append(dict(info, file=path.name, status="unreadable",
                            error_type=type(e).__name__,
                            why=f"{type(e).__name__}: {e}"))
            continue
        except Exception as e:  # noqa: BLE001 -- report, do not crash the sweep
            # Anything else (TypeError, NameError, KeyError...) is a bug in the
            # reader, not a property of the file; it must not read as
            # "unreadable file" (#610).
            out.append(dict(info, file=path.name, status="error",
                            error_type=type(e).__name__,
                            why=f"{type(e).__name__}: {e}"))
            continue

        val = None
        if measure is not None:
            v, _ = measure(x, sr, info)
            val = None if v is None else round(v, 4)

        a0 = prepare(x, sr)[:int(window_s * sr)]
        best = (0.0, 1.0, "")
        for q in refs:
            xr, s2 = read_ref(q)
            b = _n(prepare(xr, s2)[:int(window_s * s2)])
            for ratio in np.arange(min_ratio, max_ratio, ratio_step):
                aa = resample(a0, max(1, int(len(a0) * ratio)))
                n = min(len(aa), len(b))
                if n < 2:
                    continue
                c = float(np.abs(correlate(_n(aa[:n]), b[:n], mode="full")).max())
                if c > best[0]:
                    best = (c, float(ratio), q.name)
        out.append(dict(
            info, file=path.name, sr_in=int(sr), **{measure_field: val},
            best_match=best[2], best_correlation=round(best[0], 4),
            at_resample_ratio=round(best[1], 4),
            verdict=("a re-pressing of the reference set -- not an independent source"
                     if best[0] >= match_threshold else
                     "no reference file matches it")))
    return out


# ---------------------------------------------------------------------------
# 5. precondition assertion: REFUSE, do not report, when apparatus disagrees
# ---------------------------------------------------------------------------
def assert_precondition(measured, reference, tol: float, *, what: str) -> float:
    """REFUSE (raise `SystemExit`) unless `measured` agrees with `reference`
    to within `tol` -- the worst absolute difference, for scalars or
    equal-length sequences alike. Returns that worst difference so the caller
    can report it when it does NOT refuse.

    CLAUDE.md: "assert your apparatus's preconditions at the point of use,
    and REFUSE rather than report when they fail" -- an answer produced past
    a failed precondition looks exactly like data and is worse than no answer
    at all. This is the one copy of a check the hi-hat/cymbal probes
    (`tools/probes/hihat/hh_probe.py`, `hh_probe2.py`) reimplemented ad hoc
    four times: an offline post-filter emulator reproducing a fixed-point
    render, and a cached band-energy estimator matching the uncached one.
    """
    a = np.atleast_1d(np.asarray(measured, dtype=np.float64))
    b = np.atleast_1d(np.asarray(reference, dtype=np.float64))
    if a.shape != b.shape:
        raise SystemExit(
            f"REFUSED: {what}: shape mismatch {a.shape} vs {b.shape}")
    diff = float(np.max(np.abs(a - b)))
    if not math.isfinite(diff) or diff > tol:
        raise SystemExit(f"REFUSED: {what}: off by {diff:.6g} (tolerance {tol:.6g})")
    return diff

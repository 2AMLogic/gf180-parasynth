#!/usr/bin/env python3
"""Perturbation ladder on REAL anchor recordings, five questions per
estimator and perturbation (#518, part of #158).

    python3 tools/probes/perturbation_ladder.py
    python3 tools/probes/perturbation_ladder.py --estimator decay_tau --json /tmp/pl.json

#158 asks whether each scorecard estimator stays useful on a REAL recording,
not just on a synthetic probe: "An estimator can pass every sine test and
fail on one." This file applies seven controlled perturbation types -- gain,
narrowband tone, broadband/tail noise, quantisation, clipping, delay/leading
silence, and resampling (noise is run twice, broadband and tail-only, so
there are eight named sweeps over the seven types) -- to a real Fischer
TR-808 anchor recording per estimator, at several strengths each, and asks
the same five questions of every (estimator, perturbation) pair:

    1. Does it detect above its declared resolution?
    2. Does reported magnitude track introduced magnitude?
    3. Does more defect generally mean more error?
    4. Does it falsely report unrelated properties moving?
    5. Does it correctly abstain when the signal is insufficient?

THIS IS NOT #517'S SYNTHETIC-FIXTURE SUITE. A synthetic fixture has a truth
independent of any estimator; a real recording does not -- nobody measured
the TRUE partial balance of this particular rimshot strike by a route other
than the estimator itself. So instead of comparing against an external
truth, every question here is answered against the PHYSICS of what a given
perturbation SHOULD do to the BASE reading on this same recording -- exactly
`model/measure_harness.py:floor_for_these_signals`'s own frame ("each
perturbation changes only the measurement APPARATUS ... and leaves the
machine and the strike untouched, so any change in the reading is the
apparatus, not the sound"), extended from one named delta per signal to a
whole strength sweep with an explicit, derived expectation attached.

WHY SIX OF THE SEVEN TYPES ARE EXPECTED-INVARIANT, AND ONE IS NOT
------------------------------------------------------------------
Gain, narrowband tone (placed outside anything the estimator reads),
broadband noise, tail noise, quantisation and delay/leading silence are all
APPARATUS/CHANNEL degradations layered on the SAME underlying strike: the
true ratio or time constant they stand in front of has not changed, by
construction, at every strength. (Gain is the cleanest case and holds
EXACTLY: `band_pair_db` and `tone_ratio_db` are ratios of the same signal
scaled by the same constant, so the ratio is invariant to machine precision;
`decay_tau` reads the SLOPE of the log-envelope, which a constant multiplier
shifts but does not tilt.) A measured delta on one of these six is therefore
either apparatus noise within the estimator's own declared resolution, or a
genuine Q4 finding -- the measured_conga_body_spread precedent for exactly
this (`lead_1ms_db`'s documented `sosfiltfilt` edge artefact) is why this
ladder expects to find a few.

Resampling is different, and that difference is this issue's second
acceptance criterion. A tape-speed change by ratio `a` (`perturb_resample`,
the same primitive as `tools/probes/audio_distance_floor.py`'s `perturb_f0`
and `tools/measure_conga_body_spread.py`'s `perturb_f0`, generalised under
its true name) moves every frequency component by `a` AND every decay time
constant by `1/a` -- NOT a pure pitch shift. For `decay_tau`, whose OUTPUT
quantity literally IS a time constant, that gives an exact closed-form
expected delta: `tau' = tau / a`. For the three RATIO estimators
(`band_pair_db`, `tone_ratio_db`, `inharmonic_fraction_db`), the quantity
under test is a ratio between two things that may decay at DIFFERENT rates,
so a uniform time-stretch changes how much of each one's own decay a FIXED
measurement window captures -- `band_pair_db`'s own domain already measures
and bounds exactly this contamination for an ASYMMETRIC decay difference
(`tools/probes/estimator_domains.py` section 2, "the A^2*tau term"); a
uniform stretch is the same mechanism with both sides scaled together, and
deriving its closed form needs each partial's own fitted tau, which this
probe does not fit. That is stated here, not silently skipped: resample's
expected delta for the three ratio estimators is reported as NOT DERIVABLE,
and Q2/Q3 (which need a known expected delta to grade against) say so
explicitly rather than guessing. Q1, Q4 and Q5 still run.

REFUSE, NOT SKIP, WHEN THE CORPUS IS ABSENT
--------------------------------------------
Same convention as `tools/probes/estimator_domains.py` section 4 and
`tools/run_case.py`'s `Refused`: if the Fischer corpus
(`$GF180_TR808_REFS`, else `/tmp/tr808-ref`) is not on this host, `main()`
prints a REFUSED banner and exits 1. It does not fabricate a report, and it
does not quietly exit 0 the way a skipped check would.

Nothing here is trusted until `tools/probes/test_perturbation_ladder.py`
passes: every derived expectation (resample's dual effect, gain's exact
invariance) is checked against a signal of known answer, and the
five-question machinery itself carries injected-bug controls -- a mocked
estimator rigged to drift under an invariant perturbation must turn Q4 red,
and one rigged to ignore a known scaling relation must turn Q2 red.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import pathlib
import sys
from typing import Callable

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am                                           # noqa: E402
import run_case as rc                                                 # noqa: E402

# Reuse the band split `tools/probes/estimator_domains.py` measured
# `band_pair_db`'s domain against, rather than a second copy that can drift.
import estimator_domains as ed                                        # noqa: E402

BAND_A = ed.BAND_A
BAND_B = ed.BAND_B


# ===========================================================================
# 1. Perturbations -- seven types named by #158, eight named sweeps
# ===========================================================================
GAIN_DB = (-6.0, -3.0, -1.0, 1.0, 3.0, 6.0)
#: tape-speed ratio `a`: `a > 1` speeds up (frequency x a, decay / a).
RESAMPLE_RATIO = (0.90, 0.95, 0.99, 1.01, 1.05, 1.10)
DELAY_MS = (0.1, 0.5, 1.0, 5.0, 10.0)
QUANTISATION_BITS = (16, 12, 10, 8, 6, 4)
#: fraction of the signal's own peak the clipper's rail sits at; 1.0 = no clip.
CLIP_FRACTION = (1.0, 0.5, 0.2, 0.1, 0.05, 0.02)
NOISE_SNR_DB = (80.0, 60.0, 45.0, 36.0, 24.0, 12.0)
#: narrowband tone level, dB relative to the signal's own peak.
TONE_REL_DB = (-80.0, -60.0, -40.0, -30.0, -20.0, -10.0)


def perturb_gain(x: np.ndarray, sr: int, db: float) -> np.ndarray:
    """Pure amplitude scale. Every ratio/slope estimator this ladder covers
    is invariant to this EXACTLY, to machine precision -- there is no fitted
    parameter here to be wrong about."""
    return x * (10.0 ** (db / 20.0))


def perturb_resample(x: np.ndarray, sr: int, ratio: float) -> np.ndarray:
    """Tape-speed change by `ratio` (`a`): every frequency moves to `a * f`
    and every decay time constant moves to `tau / a`, in closed form, by
    construction -- NOT a pure pitch shift. Same primitive as
    `tools/probes/audio_distance_floor.py:perturb_f0` and
    `tools/measure_conga_body_spread.py:perturb_f0`, generalised under its
    true name rather than re-derived a third time."""
    n = max(1, int(round(len(x) / ratio)))
    t = np.linspace(0.0, len(x) - 1.0, n)
    y = np.interp(t, np.arange(len(x)), x)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y[: len(x)]


def perturb_delay(x: np.ndarray, sr: int, lead_ms: float) -> np.ndarray:
    """Prepend `lead_ms` of exact digital silence -- an operation that
    cannot change what the machine did (`tools/run_case.py:prepare`'s own
    guarantee), so any change in the reading is the apparatus, never the
    sound. The known counter-example is `band_pair_db`'s `sosfiltfilt` edge,
    documented in `tools/measure_conga_body_spread.py:floor_for_these_signals`
    -- this ladder exists partly to find that class of defect on the OTHER
    three estimators too."""
    k = int(round(lead_ms * 1e-3 * sr))
    if k <= 0:
        return x
    return np.concatenate([np.zeros(k), x])


def perturb_quantise(x: np.ndarray, sr: int, bits: float) -> np.ndarray:
    """Requantise to `bits` bits full-scale, peak-referenced."""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak <= 0.0:
        return x
    levels = 2.0 ** (bits - 1)
    return np.round(x / peak * levels) / levels * peak


def perturb_clip(x: np.ndarray, sr: int, frac: float) -> np.ndarray:
    """Hard-clip at `frac` of the signal's own peak (1.0 = no-op control)."""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    limit = max(frac, 0.0) * peak
    return np.clip(x, -limit, limit)


def _signal_rms_for_snr(x: np.ndarray) -> float:
    """RMS over the SOUNDING extent only (`audio_measure.sounding_extent`),
    not the whole buffer -- a real anchor recording trails seconds of near-
    silence, and an SNR referenced to the whole buffer would understate the
    signal and overstate how much noise `snr_db` actually injects."""
    n = am.sounding_extent(x)
    seg = x[:n] if n > 0 else x
    return math.sqrt(float(np.mean(seg ** 2))) if len(seg) else 0.0


def perturb_noise(x: np.ndarray, sr: int, snr_db: float, *,
                  tail: bool = False, seed: int = 0) -> np.ndarray:
    """Additive white noise at `snr_db` re the signal's own sounding-extent
    RMS. `tail=True` confines it to AFTER the sounding extent -- the
    "tail noise" half of #158's "broadband/tail noise" perturbation type,
    which stresses an estimator's own silence/floor handling specifically
    rather than its whole-signal SNR tolerance."""
    rng = np.random.default_rng(seed)
    ref = _signal_rms_for_snr(x)
    amp = ref * (10.0 ** (-snr_db / 20.0))
    noise = amp * rng.standard_normal(len(x))
    if tail:
        n = am.sounding_extent(x)
        mask = np.zeros(len(x))
        mask[n:] = 1.0
        noise = noise * mask
    return x + noise


def perturb_tone(x: np.ndarray, sr: int, rel_db: float, *, hz: float) -> np.ndarray:
    """Add a sine at `hz`, `rel_db` relative to the signal's own peak."""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    amp = peak * (10.0 ** (rel_db / 20.0))
    t = np.arange(len(x)) / sr
    return x + amp * np.sin(2.0 * math.pi * hz * t)


@dataclasses.dataclass(frozen=True)
class Perturbation:
    name: str
    strengths: tuple
    apply: Callable[[np.ndarray, int, float, "EstimatorCase"], np.ndarray]
    unit: str
    no_op: float   # the strength value that is a true no-op, for display


def _apply_gain(x, sr, strength, case):
    return perturb_gain(x, sr, strength)


def _apply_resample(x, sr, strength, case):
    return perturb_resample(x, sr, strength)


def _apply_delay(x, sr, strength, case):
    return perturb_delay(x, sr, strength)


def _apply_quant(x, sr, strength, case):
    return perturb_quantise(x, sr, strength)


def _apply_clip(x, sr, strength, case):
    return perturb_clip(x, sr, strength)


def _apply_noise_broadband(x, sr, strength, case):
    return perturb_noise(x, sr, strength, tail=False)


def _apply_noise_tail(x, sr, strength, case):
    return perturb_noise(x, sr, strength, tail=True)


def _apply_tone(x, sr, strength, case):
    hz = min(case.tone_hz, 0.45 * sr)
    return perturb_tone(x, sr, strength, hz=hz)


PERTURBATIONS: dict[str, Perturbation] = {
    "gain_db": Perturbation("gain_db", GAIN_DB, _apply_gain, "dB", 0.0),
    "resample_ratio": Perturbation("resample_ratio", RESAMPLE_RATIO,
                                   _apply_resample, "x", 1.0),
    "delay_ms": Perturbation("delay_ms", DELAY_MS, _apply_delay, "ms", 0.0),
    "quantisation_bits": Perturbation("quantisation_bits", QUANTISATION_BITS,
                                      _apply_quant, "bits", math.inf),
    "clip_fraction": Perturbation("clip_fraction", CLIP_FRACTION, _apply_clip,
                                  "x peak", 1.0),
    "noise_broadband_snr_db": Perturbation("noise_broadband_snr_db", NOISE_SNR_DB,
                                           _apply_noise_broadband, "dB SNR", math.inf),
    "noise_tail_snr_db": Perturbation("noise_tail_snr_db", NOISE_SNR_DB,
                                      _apply_noise_tail, "dB SNR", math.inf),
    "narrowband_tone_rel_db": Perturbation("narrowband_tone_rel_db", TONE_REL_DB,
                                           _apply_tone, "dB re peak", -math.inf),
}

# Resampling's expected relation is SPECIAL per #158's second acceptance
# criterion: a closed form only where the estimator's own OUTPUT quantity is
# a time constant. Every other (estimator, perturbation) pair defaults to
# "invariant" -- see the module docstring for why that default is justified,
# not assumed.
RELATION_OVERRIDES: dict[tuple[str, str], str] = {
    ("decay_tau", "resample_ratio"): "scales_inverse",
    ("band_pair_db", "resample_ratio"): "not_derivable",
    ("tone_ratio_db", "resample_ratio"): "not_derivable",
    ("inharmonic_fraction_db", "resample_ratio"): "not_derivable",
}


def relation_for(estimator_name: str, perturbation_name: str) -> str:
    return RELATION_OVERRIDES.get((estimator_name, perturbation_name), "invariant")


# ===========================================================================
# 2. Estimators under test -- the same four `estimator_domains.py` measures
# ===========================================================================
@dataclasses.dataclass(frozen=True)
class EstimatorCase:
    name: str
    voice: str                               # key into `run_case.REF_MAIN`
    measure: Callable[[np.ndarray, int], am.Estimate]
    resolution: float                        # in `measure`'s own output units
    relative: bool                           # True: `resolution` is a fraction of base_value
    tone_hz: float                           # where to place the narrowband tone


def _measure_band_pair(x, sr):
    return rc.band_pair_db(x, sr, BAND_B, BAND_A)


def _measure_tone_ratio(x, sr):
    return rc.tone_ratio_db(x, sr, 800.0, 540.0)


def _measure_decay_tau(x, sr):
    return am.decay_tau(x, sr)


def _measure_inharmonic(x, sr):
    return am.inharmonic_fraction_db(x, 49.4, sr)


ESTIMATOR_CASES: list[EstimatorCase] = [
    # 0.2 dB: `estimator_domains.py` section 1's measured stationary accuracy.
    EstimatorCase("band_pair_db", "RS", _measure_band_pair, 0.2, False, 8000.0),
    # 0.05 dB: `estimator_domains.py` section 4(a)'s measured detuning accuracy.
    EstimatorCase("tone_ratio_db", "CB", _measure_tone_ratio, 0.05, False, 6000.0),
    # 8 %: `estimator_domains.py` section 5(b)'s measured in-domain accuracy.
    EstimatorCase("decay_tau", "BD", _measure_decay_tau, 0.08, True, 2000.0),
    # 0.1 dB: `estimator_domains.py` section 6's measured closed-form model error.
    EstimatorCase("inharmonic_fraction_db", "BD", _measure_inharmonic, 0.1, False, 2000.0),
]


# ===========================================================================
# 3. Expected delta, derived from the base reading and the perturbation
# ===========================================================================
def expected_delta(relation: str, base_value: float, strength: float) -> float | None:
    """The PHYSICALLY derived change the base reading should show, or `None`
    when no closed form is claimed (see module docstring). Never guessed:
    a caller that cannot derive the expectation gets `None`, not a value
    that merely looks plausible."""
    if relation == "invariant":
        return 0.0
    if relation == "scales_inverse":
        return base_value * (1.0 / strength - 1.0)
    return None


def _exceeds(value: float | None, resolution: float, relative: bool,
            base_value: float) -> bool:
    if value is None:
        return False
    if relative:
        if not base_value:
            return False
        return abs(value / base_value) > resolution
    return abs(value) > resolution


# ===========================================================================
# 4. One (estimator, perturbation) sweep
# ===========================================================================
def sweep_one(case: EstimatorCase, pert: Perturbation, x: np.ndarray, sr: int,
              base: am.Estimate) -> tuple[str, list[dict]]:
    relation = relation_for(case.name, pert.name)
    rows = []
    for strength in pert.strengths:
        y = pert.apply(x, sr, strength, case)
        got = case.measure(y, sr)
        exp = expected_delta(relation, base.value, strength)
        measured = (got.value - base.value) if got.ok else None
        rows.append(dict(
            strength=strength,
            refused=not got.ok,
            why=("" if got.ok else got.reason),
            measured_value=(round(got.value, 6) if got.ok else None),
            measured_delta=(round(measured, 6) if measured is not None else None),
            expected_delta=(None if exp is None else round(exp, 6)),
            expected_known=(exp is not None),
        ))
    return relation, rows


# ===========================================================================
# 5. The five questions
# ===========================================================================
def five_questions(relation: str, resolution: float, relative: bool,
                   base_value: float, rows: list[dict]) -> dict:
    usable = [r for r in rows if r["measured_delta"] is not None]
    q: dict = {}

    # Q1: does it detect above its declared resolution?
    sensitive = [r for r in usable if r["expected_known"]
                and _exceeds(r["expected_delta"], resolution, relative, base_value)]
    if not sensitive:
        q["q1_detects_above_resolution"] = dict(
            answer=None,
            why="no rung in this sweep has a derivable expected effect larger "
                "than this estimator's own declared resolution")
    else:
        detected = [r for r in sensitive
                   if _exceeds(r["measured_delta"], resolution, relative, base_value)]
        q["q1_detects_above_resolution"] = dict(
            answer=(len(detected) == len(sensitive)),
            rungs_checked=[r["strength"] for r in sensitive],
            rungs_detected=[r["strength"] for r in detected])

    # Q2: does reported magnitude track introduced magnitude?
    if relation != "scales_inverse":
        q["q2_tracks_introduced_magnitude"] = dict(
            answer=None,
            why=("no known non-zero expected effect to track against "
                "(this perturbation is expected-invariant here)" if relation == "invariant"
                else "expected effect not derivable in closed form for this "
                     "(estimator, perturbation) pair"))
    elif len(usable) < 3:
        q["q2_tracks_introduced_magnitude"] = dict(
            answer=None, why="fewer than 3 usable rungs")
    else:
        expv = np.array([r["expected_delta"] for r in usable], dtype=np.float64)
        measv = np.array([r["measured_delta"] for r in usable], dtype=np.float64)
        corr = (float(np.corrcoef(expv, measv)[0, 1])
               if np.std(expv) > 0 and np.std(measv) > 0 else None)
        q["q2_tracks_introduced_magnitude"] = dict(
            answer=(corr is not None and corr > 0.8), correlation=corr)

    # Q3: does more defect generally mean more error?
    if relation != "scales_inverse":
        q["q3_more_defect_more_error"] = dict(
            answer=None,
            why=("this perturbation's true expected effect is constant (zero) "
                "by construction -- see Q4 instead" if relation == "invariant"
                else "expected effect not derivable in closed form for this "
                     "(estimator, perturbation) pair"))
    elif len(usable) < 3:
        q["q3_more_defect_more_error"] = dict(
            answer=None, why="fewer than 3 usable rungs")
    else:
        mag = np.array([abs(r["strength"] - pert_no_op_for(relation)) for r in usable])
        err = np.array([abs(r["measured_delta"] - r["expected_delta"]) for r in usable])
        if not any(_exceeds(float(e), resolution, relative, base_value) for e in err):
            # Every rung's error already sits inside this estimator's own
            # declared resolution -- there is no error large enough to grow,
            # which is the best possible finding, not a missing one.
            q["q3_more_defect_more_error"] = dict(
                answer=True, correlation=None,
                why="error stays within this estimator's declared resolution "
                    "at every rung checked")
        else:
            corr = (float(np.corrcoef(mag, err)[0, 1])
                   if np.std(mag) > 0 and np.std(err) > 0 else None)
            q["q3_more_defect_more_error"] = dict(
                answer=(corr is not None and corr > 0.0), correlation=corr)

    # Q4: does it falsely report unrelated properties moving?
    if relation != "invariant":
        q["q4_false_unrelated_move"] = dict(
            answer=None,
            why=("this perturbation has a genuine non-zero expected effect; "
                "a moved reading is correct, not a false alarm"
                if relation == "scales_inverse" else
                "expected effect not derivable, so a false-alarm verdict "
                "cannot be computed honestly"))
    else:
        bad = [r for r in usable
              if _exceeds(r["measured_delta"], resolution, relative, base_value)]
        q["q4_false_unrelated_move"] = dict(
            answer=(len(bad) == 0),
            violating_strengths=[r["strength"] for r in bad])

    # Q5: does it correctly abstain when the signal is insufficient?
    refused = [r["strength"] for r in rows if r["refused"]]
    q["q5_abstains_when_insufficient"] = dict(
        any_refusal=bool(refused), refused_at_strengths=refused)

    return q


def pert_no_op_for(relation: str) -> float:
    return 1.0 if relation == "scales_inverse" else 0.0


# ===========================================================================
# 6. Run the whole ladder
# ===========================================================================
def run_ladder(refs: pathlib.Path | None = None, *,
               estimators: list[str] | None = None) -> dict:
    """Run every (estimator, perturbation) sweep against the real anchor
    recordings and answer the five questions for each.

    REFUSES -- status "REFUSED", no sweep attempted -- rather than silently
    producing an empty report when the corpus is absent: matching
    `tools/probes/estimator_domains.py` section 4's convention
    ("the corpus's absence [prints] rather than letting a skipped check look
    like a passed one")."""
    refs = rc.configured_refs() if refs is None else refs
    if not refs.exists():
        return dict(
            status="REFUSED", refs=str(refs),
            why=f"the Fischer TR-808 corpus is not at {refs} "
                f"({rc.REFS_ENV} unset, default {rc.REFS_DEFAULT} absent) -- "
                "nothing below was measured against a real recording")

    cases = [c for c in ESTIMATOR_CASES if estimators is None or c.name in estimators]
    out: dict = dict(status="OK", refs=str(refs), estimators={})
    for case in cases:
        try:
            x, sr, rel, _setting = rc.load_reference(case.voice, refs)
        except rc.Refused as e:
            out["estimators"][case.name] = dict(status="REFUSED", voice=case.voice,
                                                 why=str(e))
            continue
        base = case.measure(x, sr)
        if not base.ok:
            out["estimators"][case.name] = dict(
                status="BASE_REFUSED", voice=case.voice, file=rel, why=base.reason)
            continue
        est_out: dict = dict(status="OK", voice=case.voice, file=rel,
                             base_value=round(base.value, 6), perturbations={})
        for pname, pert in PERTURBATIONS.items():
            relation, rows = sweep_one(case, pert, x, sr, base)
            questions = five_questions(relation, case.resolution, case.relative,
                                       base.value, rows)
            est_out["perturbations"][pname] = dict(
                relation=relation, unit=pert.unit, rows=rows, questions=questions)
        out["estimators"][case.name] = est_out
    return out


# ===========================================================================
# 7. Reporting
# ===========================================================================
def print_report(result: dict) -> None:
    if result["status"] == "REFUSED":
        print(f"REFUSED: {result['why']}")
        return
    print(f"corpus: {result['refs']}")
    for name, est in result["estimators"].items():
        print("\n" + "=" * 78)
        if est["status"] != "OK":
            print(f"{name}: {est['status']} -- {est['why']}")
            continue
        print(f"{name}  (voice {est['voice']}, file {est['file']}, "
              f"base reading {est['base_value']})")
        for pname, sweep in est["perturbations"].items():
            print(f"\n  {pname}  [{sweep['relation']}]")
            for key in ("q1_detects_above_resolution", "q2_tracks_introduced_magnitude",
                       "q3_more_defect_more_error", "q4_false_unrelated_move",
                       "q5_abstains_when_insufficient"):
                print(f"      {key}: {sweep['questions'][key]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refs", default=None,
                    help=f"the Fischer TR-808 corpus (default {rc.REFS_DEFAULT}, "
                         f"${rc.REFS_ENV})")
    ap.add_argument("--estimator", action="append", default=None,
                    help="restrict to this estimator (repeatable); default: all")
    ap.add_argument("--json", default=None, help="write the full report as JSON")
    args = ap.parse_args(argv)

    refs = pathlib.Path(args.refs) if args.refs else None
    result = run_ladder(refs, estimators=args.estimator)
    print_report(result)
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(result, indent=2, default=str))
    return 1 if result["status"] == "REFUSED" else 0


if __name__ == "__main__":
    sys.exit(main())

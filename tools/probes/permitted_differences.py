#!/usr/bin/env python3
"""The false-alarm half of #158: differences that are PERMITTED must not move
the estimator, and differences that are NOT permitted must still move it.

    python3 tools/probes/permitted_differences.py                 # the suite
    python3 tools/probes/permitted_differences.py --calibrate      # thresholds
    python3 tools/probes/permitted_differences.py --inject BLANKET_INVARIANCE
    python3 tools/probes/permitted_differences.py --false-alarm-rate 50
    python3 tools/probes/permitted_differences.py --safety-sweep 20

#158: "**A detector that fires on every difference is useless.** Leading
silence must not move an onset-relative decay; polarity reversal must not move
spectral magnitude or estimated frequency; gain must not move a level-normalised
descriptor; independent noise realisations must not require waveform equality;
free-running phase must not invalidate a phase-insensitive comparison." And
immediately after: "**These are not universally benign** -- gain matters for
accent tests, polarity when mixing correlated signals, timing for latency.
**The case definition decides which differences are permitted.**"

Both halves are the suite. Every row below carries BOTH statements -- what this
case permits and what the same case does not -- and five of the eighteen rows
are NOT-PERMITTED rows whose job is to fail if the suite ever degenerates into
one blanket invariance rule. `--inject BLANKET_INVARIANCE` is that degeneration,
written as an input, and it turns exactly those five red (rule 8 of
docs/verification-rules.md: a guard ships with the input that defeats it).

MEASURED FALSE-ALARM RATE, before this was trusted in CI
--------------------------------------------------------
`--false-alarm-rate 50 --trials 12` on `FPR_BASE`: **0 red rows in 900
row-level comparisons** (10,800 individual trials), 0 of 50 suite runs red,
one-sided 95 % Clopper-Pearson upper bound **0.33 %** per row. Zero is not a
proof -- 50 runs cannot see a 1-in-10,000 rate -- which is why the bound is
quoted beside it and why `--false-alarm-rate` is a mode rather than a one-off
number in a commit message.

WHAT THIS IS NOT
----------------
It is not the synthetic ground-truth suite (does the estimator measure the
right value -- the sibling sub-issue of #158) and it is not the perturbation
ladder on real recordings. Nothing here needs a corpus: a permitted difference
is a transform of a signal compared against the untransformed signal, so the
answer is known without knowing the estimator's answer. That independence is
the point -- it is the one shape of estimator check in this repository that
cannot be satisfied by an estimator calibrated against our own model.

HOW THE THRESHOLDS WERE CHOSEN, PER ROW
---------------------------------------
Not picked. Measured, by `--calibrate`, on a seed stream DISJOINT from the one
the shipped suite and the false-alarm check draw from (`CALIBRATE_BASE` vs
`VALIDATE_BASE` vs `FPR_BASE`, asserted disjoint at import):

  * PERMITTED rows and TRACKS rows: `SAFETY` (4) x the worst |delta| (or worst
    tracking residual) over `CAL_TRIALS` (96) independent draws, rounded up to
    two significant figures. `--calibrate` re-measures and exits non-zero if a
    committed threshold has fallen BELOW that recommendation (it would
    false-alarm) or has drifted more than `SLACK_FACTOR` (20x) ABOVE it (it
    would be vacuous). An unsatisfiable gate and a vacuous gate are both
    failures of the same check.
  * MOVES rows: a RESOLUTION floor in dB -- the smallest difference the
    comparison has to be able to see -- not a tightness-limited number. The
    calibration report prints the measured margin between the floor and the
    smallest difference the draws actually produced, so a reader can see
    whether the floor is doing any work.

AND WHY `SAFETY` IS 4, SWEPT RATHER THAN ARGUED
-----------------------------------------------
`SAFETY` is the one constant a per-row `how` string cannot justify, because
every calibrated threshold inherits it. `--safety-sweep 20` re-derives the five
calibrated thresholds at each factor and measures BOTH costs of the choice on
360 row-level comparisons apiece -- false alarms, and whether the row still
catches its own injected defect. False alarms alone would recommend infinity:

  safety   red rows   rate    red runs   injected defects still caught
    0.5     58/360   16.11%     20/20    5/5
    1        3/360    0.83%      3/20    5/5
    2        0/360    0.00%      0/20    5/5
    4        0/360    0.00%      0/20    5/5   <- shipped
    8        0/360    0.00%      0/20    4/5   (post-#528; was 3/5)
   64        0/360    0.00%      0/20    3/5
  512        0/360    0.00%      0/20    3/5

So the working window is 2-4x and it is bounded on both sides by measurement:
below it the suite false-alarms, above it rows stop detecting (one at 8x, three by 64x). 4 is the
conservative end of a two-element window, not a number somebody liked.

NOT in `docs/sensitivity/registry.json`, deliberately and recorded here so the
decision is visible rather than missed: that gate re-extracts a grid from a
committed fixed-width-table artefact and checks it against an independent
prediction, which is the right shape for a synthesis dial whose measurement
costs an hour. `SAFETY`'s measurement costs minutes and is re-RUN by
`test_safety_is_bounded_from_below_by_false_alarms` and
`test_safety_is_bounded_from_above_by_lost_detection` on every pytest pass, so
a transcription gate on top of it would be the weaker of the two checks.
`docs/sensitivity-sweeps.md`'s own "what this does not catch" makes the same
distinction for the per-feature sweep instruments.

ONE ROW BINDS BOTH ENDS, which is the part worth carrying forward:
`noise/psd_slope` is the only row that false-alarms at 1x (3 runs of 20, every
one of them that row) and one of the two that stops detecting at 8x. A Welch
slope over a 2.0 s record is the least averaged estimate in the table, and the
window is narrow there for that reason rather than for a reason about SAFETY.

The pair that bounds it from above WAS thin, and #528 widened it. Before, at 4x,
`SINGLE_WINDOW_SLOPE`'s residual on `noise/psd_slope` was 0.629 dB/oct against a
0.6 threshold -- 1.05x, the thinnest in the table, and the reason 8x lost it
(3/5 caught). Now the residual is 1.301 against the same 0.6: 2.17x, and 8x
(threshold 1.18) catches it; the row that stops detecting at 8x is
`noise/centroid` (`SHORT_WINDOW_SPECTRUM`, 1.87x), so 8x still loses 1 of 5 and
SAFETY's upper bound has not moved (4/5 at 8x, 3/5 from 64x).

WHY THE DEFECT WAS MADE GROSSER RATHER THAN THE FIXTURE LONGER (#528 option 1
was tried first and measured, not argued). `--calibrate` at 4.0 s: worst of 96
draws 0.138 against 0.148 at 2.0 s -- the threshold would have moved 0.60 ->
0.56, a margin of 1.12x. A 96-pair spread check on bare white noise gave
worst/mean of 0.144/0.052 (2 s), 0.117/0.047 (4 s), 0.090/0.023 (8 s): the
worst draw is dominated by the lowest 1/6-octave bins, so doubling the length
does not halve it, and 8 s would cost 4x the three noise rows' runtime. The
fixture stays 2.0 s. The defect is now a 2048-sample excerpt Welch-averaged at
nfft=512 (7 segments, 2x coarser than before) -- still a short-excerpt
estimate read as a property of the process, the same class of mistake, just
grosser. The trade named in the issue is real: the previous parameters were
the ones a plausible mistake would use.

Stochastic rows (independent noise realisation, free-running phase, and every
row whose transform draws its own magnitude) are run over `--trials` draws and
report mean / p95 / max |delta| plus a one-sided 95 % Clopper-Pearson bound on
the per-trial exceedance rate. A single draw is not a verdict.

REFUSED IS NOT PASS AND IT IS NOT FAIL
--------------------------------------
Every row asserts its own preconditions at the point of use -- the fixture is
finite, sounding, not clipped, carries the pre-onset lead it promises; the
transform actually changed the array; both sides produced a number. A trial
whose preconditions fail, or on which the estimator legitimately refuses, is
REFUSED, and a row that refuses more than `MAX_REFUSAL_RATE` of its trials is
REFUSED as a row. Exit status: 0 all PASS, 1 any FAIL, 2 any REFUSED with no
FAIL. A tool that answers when it cannot is worse than one that is absent.

THE NINE INJECTED CONTROLS, AND WHAT EACH PROVES
------------------------------------------------
A permitted-differences suite passes trivially if its tolerances are loose, so
each tolerance ships with a defect it must catch. `--inject NAME` reinstates one
defect in the pipeline of its DECLARED target rows only (`INJECTIONS[name]
.targets`), and the suite then asserts the red set equals the declared one:
a control that reds its target proves the threshold is tight enough to be
evidence; a control that reds a row it does not target would mean the hook is
not the mechanism it claims.

  DECAY_FROM_ARRAY_START   both leading-silence decay rows, by measuring the
                           decay from the array's first sample instead of from
                           the onset -- the class of defect #101 was.
  SIGNED_PEAK_NORM         the polarity row, by normalising on `x.max()`
                           instead of `abs(x).max()`.
  CLIP_BEFORE_MEASURE      all four gain rows and both polarity descriptor
                           rows, by an ASYMMETRIC clip ahead of the estimator
                           -- the question `tools/probes/cymbal_candidate4_
                           gain_invariance.py` asked of one candidate, asked
                           here of the estimator.
  SINGLE_WINDOW_SLOPE      the noise-realisation slope row, by taking one short
                           window instead of a Welch average.
  SHORT_WINDOW_SPECTRUM    the noise-realisation centroid row, by reading 43 ms
                           of one realisation as a property of the process.
  TWO_POINT_TAIL_DECAY     the noise-realisation decay row, by reading tau off
                           two envelope samples that land in the record's noise
                           floor rather than in its decay.
  PHASE_SENSITIVE_SPECTRUM the two free-phase rows, by reading the real part of
                           the transform instead of its magnitude.
  PER_STRIKE_NORMALISATION the accent row, by peak-normalising each strike --
                           exactly what `run_case.prepare` does per RECORD, and
                           the reason an accent test may not be routed through
                           a per-strike normaliser.
  BLANKET_INVARIANCE       all five not-permitted rows, by substituting each
                           row's own no-difference reading. This is the input
                           that defeats a suite built on one invariance rule.

WRONG BEFORE IT WAS RIGHT: FIVE, ALL CAUGHT BY A GATE RATHER THAN BY READING
----------------------------------------------------------------------------
CLAUDE.md asks for this rate to be published where the numbers are read.

  1. The per-row seed came from `hash(cid)`, which is salted per process, so
     the "deterministic" calibration was not reproducible: two consecutive
     runs disagreed by 70 % on `noise/decay_tau`'s binding draw. `case_seed`.
  2. A `moves` floor was compared against `abs(delta)`, which read -300 dB --
     two IDENTICAL records -- as an enormous difference, so
     BLANKET_INVARIANCE passed on three of the five not-permitted rows. Fixed
     by giving each one-sided row a direction and each row a `null_delta`.
  3. `phase/band_ratio_db` used fixed 100-1000 Hz / 1-8 kHz bands and measured
     0.45 dB of free-phase sensitivity. That was the band edge landing on a
     harmonic, not the estimator: anchoring the edges to f0 took it to
     0.00025 dB, 1500x tighter.
  4. `DECAY_FROM_ARRAY_START` v1 fitted a slope over a fixed window and left
     `ls/decay_tau` REFUSED rather than FAIL. A control that refuses has not
     shown the row catches anything.
  5. `FIT_INTO_THE_NOISE_FLOOR` did not fire at all, because `decay_tau`'s
     amplitude weighting gives floor samples a weight of 1e-4. Replaced by
     TWO_POINT_TAIL_DECAY -- and the non-firing is itself a result about
     `decay_tau`'s robustness, recorded in `_tau_from_two_tail_points`.

Items 4 and 5 are the two the controls caught; 1-3 are the ones the
calibration gate and the blanket-invariance input caught. None was found by
inspection.

`tools/probes/test_permitted_differences.py` runs all nine controls, the
96-draw calibration replay and the false-alarm check, and is collected by
`make verify`'s pytest job.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                            # noqa: E402

SR = 48000

#: Disjoint seed namespaces. The thresholds are calibrated on CALIBRATE_BASE,
#: the shipped suite runs on VALIDATE_BASE and the false-alarm rate is measured
#: on FPR_BASE. A threshold calibrated on the draws it is then validated on is
#: not calibrated, and that is the mistake this repository keeps making in other
#: forms (CLAUDE.md: "an estimator calibrated on our own model is not
#: validated").
CALIBRATE_BASE = 100_000
FPR_BASE = 500_000
VALIDATE_BASE = 900_000
SEED_SPAN = 100_000       # trials per namespace before they could collide
assert CALIBRATE_BASE + SEED_SPAN <= FPR_BASE <= FPR_BASE + SEED_SPAN <= VALIDATE_BASE

CAL_TRIALS = 96           # draws behind every committed threshold
SAFETY = 4.0              # committed threshold = SAFETY x worst calibration draw
SLACK_FACTOR = 20.0       # above this x the recommendation, a threshold is vacuous
TRIALS_DEFAULT = 12       # trials per row in the shipped suite
MAX_REFUSAL_RATE = 0.25   # a row that refuses more than this is REFUSED, not PASS

PASS, FAIL, REFUSED = "PASS", "FAIL", "REFUSED"


# ===========================================================================
# signals and measurements
# ===========================================================================
@dataclass(frozen=True)
class Signal:
    x: np.ndarray
    sr: int
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Meas:
    """One side's number, or a refusal to produce one."""
    value: float | None
    ok: bool = True
    reason: str = ""

    @staticmethod
    def of(e) -> "Meas":
        if isinstance(e, am.Estimate):
            return Meas(e.value, e.ok, e.reason)
        if e is None or not np.isfinite(e):
            return Meas(None, False, "estimator returned a non-finite number")
        return Meas(float(e))


@dataclass
class Trial:
    """One (fixture draw, transform draw) pair, measured on both sides."""
    delta: float | None = None
    ok: bool = True
    reason: str = ""
    va: float | None = None
    vb: float | None = None
    info: dict = field(default_factory=dict)


def _refused(reason: str, **info) -> Trial:
    return Trial(None, False, reason, info=info)


# ===========================================================================
# injection plumbing
# ===========================================================================
@dataclass(frozen=True)
class Injection:
    name: str
    what: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class Ctx:
    """What a pipeline needs to know: which row it is, and whether this row is
    currently carrying an injected defect."""
    cid: str
    inject: str | None = None

    def hooked(self, name: str) -> bool:
        if self.inject != name:
            return False
        return self.cid in INJECTIONS[name].targets


# ===========================================================================
# fixtures -- synthetic, parameterised, rebuildable from their parameters
# ===========================================================================
def _noise(seed: int, n: int) -> np.ndarray:
    return np.random.default_rng([7, seed]).standard_normal(n)


def draw_damped(rng) -> dict:
    """A single damped partial with a true pre-onset lead and a quiet floor.

    `f0 * tau >= 3` over the whole box, so `decay_tau`'s
    `min_cycles_per_tau = 1.5` domain bound (#115) is satisfied by every draw
    rather than by luck, and the record is long enough for `schroeder_t20`'s
    truncation guard (#118) at the longest tau drawn."""
    return dict(kind="damped",
                f0=float(rng.uniform(100.0, 400.0)),
                tau=float(rng.uniform(0.030, 0.090)),
                amp=float(rng.uniform(0.40, 0.90)),
                phase=float(rng.uniform(0.0, 2 * math.pi)),
                lead_ms=5.0,
                seconds=1.1,
                floor_db=-78.0,
                noise_seed=int(rng.integers(1, 2**31)))


def build_damped(p) -> Signal:
    n = int(p["seconds"] * SR)
    lead = int(p["lead_ms"] * 1e-3 * SR)
    t = np.arange(n - lead) / SR
    body = p["amp"] * np.exp(-t / p["tau"]) * np.sin(2 * math.pi * p["f0"] * t + p["phase"])
    x = np.zeros(n)
    x[lead:] = body
    floor = p["amp"] * 10 ** (p["floor_db"] / 20.0)
    x += floor * _noise(p["noise_seed"], n)
    return Signal(x, SR, dict(lead_ms=p["lead_ms"], onset_ms=p["lead_ms"]))


def draw_asym(rng) -> dict:
    """Six harmonics at 1/k, which is asymmetric about zero: its positive peak
    and its negative peak differ, so a normaliser that forgets the absolute
    value is visible here and invisible on a sine. `amp <= 0.90` keeps the
    untransformed side clear of any clip threshold."""
    return dict(kind="asym",
                f0=float(rng.uniform(110.0, 330.0)),
                amp=float(rng.uniform(0.50, 0.90)),
                phase=float(rng.uniform(0.0, 2 * math.pi)),
                seconds=0.5,
                harmonics=6,
                floor_db=-80.0,
                noise_seed=int(rng.integers(1, 2**31)))


def build_asym(p) -> Signal:
    n = int(p["seconds"] * SR)
    t = np.arange(n) / SR
    y = np.zeros(n)
    for k in range(1, p["harmonics"] + 1):
        y += np.sin(2 * math.pi * k * p["f0"] * t + p["phase"]) / k
    y /= np.abs(y).max()
    x = p["amp"] * y
    x += p["amp"] * 10 ** (p["floor_db"] / 20.0) * _noise(p["noise_seed"], n)
    return Signal(x, SR, dict(f0=p["f0"], harmonics=p["harmonics"]))


def draw_noise(rng) -> dict:
    """White noise, 2.0 s. `psd_slope_db_oct` needs 4 x nfft = 32768 samples
    for a Welch estimate and refuses below it, so the length is a precondition
    of the row and not a convenience -- and it is 2.0 s rather than the 1.0 s
    this started at because the length is also the row's RESOLUTION: 1.0 s gave
    ten Welch segments and a realisation spread of 0.34 dB/oct, 2.0 s gives
    twenty-two and halves it. A tolerance is as tight as the averaging behind
    it, so the averaging is part of the case definition."""
    return dict(kind="noise",
                amp=float(rng.uniform(0.20, 0.80)),
                seconds=2.0,
                noise_seed=int(rng.integers(1, 2**31)))


def build_noise(p) -> Signal:
    n = int(p["seconds"] * SR)
    x = _noise(p["noise_seed"], n)
    x = p["amp"] * x / np.abs(x).max()
    return Signal(x, SR, {})


def draw_two_strike(rng) -> dict:
    """Two damped strikes at a KNOWN level ratio -- the accent test. The ratio
    is the thing measured, so a transform that changes it is not permitted
    however benign the same transform is for a spectral descriptor."""
    return dict(kind="two_strike",
                f0=float(rng.uniform(120.0, 300.0)),
                tau=float(rng.uniform(0.030, 0.060)),
                amp=float(rng.uniform(0.40, 0.80)),
                ratio_db=float(rng.uniform(-12.0, -2.0)),
                phase=float(rng.uniform(0.0, 2 * math.pi)),
                gap_s=0.30,
                seconds=0.80,
                floor_db=-80.0,
                noise_seed=int(rng.integers(1, 2**31)))


def build_two_strike(p) -> Signal:
    n = int(p["seconds"] * SR)
    split = int(p["gap_s"] * SR)
    x = np.zeros(n)
    for i0, a in ((int(0.010 * SR), p["amp"]),
                  (split + int(0.010 * SR), p["amp"] * 10 ** (p["ratio_db"] / 20.0))):
        t = np.arange(n - i0) / SR
        x[i0:] += a * np.exp(-t / p["tau"]) * np.sin(2 * math.pi * p["f0"] * t + p["phase"])
    x += p["amp"] * 10 ** (p["floor_db"] / 20.0) * _noise(p["noise_seed"], n)
    return Signal(x, SR, dict(split=split, ratio_db=p["ratio_db"]))


FIXTURES: dict[str, tuple[Callable, Callable]] = {
    "damped": (draw_damped, build_damped),
    "asym": (draw_asym, build_asym),
    "noise": (draw_noise, build_noise),
    "two_strike": (draw_two_strike, build_two_strike),
}


def build(p) -> Signal:
    return FIXTURES[p["kind"]][1](p)


# ===========================================================================
# the five differences of #158, plus the two per-part variants its own
# counter-examples require
# ===========================================================================
@dataclass(frozen=True)
class Applied:
    params_b: dict
    post: Callable[[Signal], Signal]
    info: dict


def _identity(s: Signal) -> Signal:
    return s


def t_leading_silence(p, rng) -> Applied:
    pad_ms = float(rng.uniform(5.0, 120.0))
    k = int(round(pad_ms * 1e-3 * SR))

    def post(s: Signal) -> Signal:
        meta = dict(s.meta)
        if "onset_ms" in meta:
            meta["onset_ms"] = meta["onset_ms"] + k / s.sr * 1e3
        if "split" in meta:
            meta["split"] = meta["split"] + k
        return Signal(np.concatenate([np.zeros(k), s.x]), s.sr, meta)

    return Applied(p, post, dict(pad_ms=k / SR * 1e3, pad_samples=k))


def t_polarity(p, rng) -> Applied:
    return Applied(p, lambda s: Signal(-s.x, s.sr, s.meta), dict(polarity=-1.0))


def t_gain(p, rng) -> Applied:
    """A uniform scale, CAPPED AT THE RECORD'S HEADROOM to full scale.

    Without the cap a +6 dB draw on a 0.90 peak leaves the record above full
    scale, where any real path clips -- and a row that then reported a moved
    descriptor would be reporting clipping and calling it gain. The cap is a
    precondition of the gain rows, which is why `check_preconditions` also
    refuses a side that reaches full scale: if this arithmetic is ever wrong
    the rows refuse instead of answering."""
    head_db = 20 * math.log10(0.98 / p["amp"])
    g_db = float(min(rng.uniform(-12.0, 6.0), head_db))
    g = 10 ** (g_db / 20.0)
    return Applied(p, lambda s: Signal(g * s.x, s.sr, s.meta),
                   dict(gain_db=g_db, headroom_db=head_db))


def t_gain_second_strike(p, rng) -> Applied:
    """Gain on ONE strike of two. A uniform gain leaves an accent RATIO alone
    (`gain/accent_ratio_db` is the row that proves it); this one does not, and
    the difference between the two is the whole content of "the case definition
    decides which differences are permitted"."""
    g_db = float(rng.uniform(-9.0, -2.0))
    g = 10 ** (g_db / 20.0)

    def post(s: Signal) -> Signal:
        x = s.x.copy()
        x[s.meta["split"]:] *= g
        return Signal(x, s.sr, s.meta)

    return Applied(p, post, dict(gain_db=g_db))


def t_noise_realisation(p, rng) -> Applied:
    """The same parameters, a different noise seed: an independent realisation
    of the same process, not a different process."""
    q = dict(p)
    q["noise_seed"] = int(rng.integers(1, 2**31))
    return Applied(q, _identity, dict(seed_a=p["noise_seed"], seed_b=q["noise_seed"]))


def t_free_phase(p, rng) -> Applied:
    """A free-running oscillator starts where it starts. The partial amplitudes
    are untouched, so every magnitude descriptor is unmoved and every
    sample-wise comparison is destroyed.

    THE OFFSET EXCLUDES A NEIGHBOURHOOD OF ZERO, and `--calibrate` is why.
    With the offset drawn over the full circle, one of 96 draws landed within a
    few degrees of the original phase, the two records were then 47 dB apart
    instead of a few dB, and the -6 dB floor on `phase/sample_rms_db` was
    reported UNSATISFIABLE -- correctly: a redraw that reproduces the original
    phase is not a test of phase sensitivity in either direction. The exclusion
    is a stated precondition of these rows rather than an assumed one, and it
    was found by running the gate against the current state before committing
    it."""
    off = float(rng.uniform(math.pi / 4, 7 * math.pi / 4))
    q = dict(p)
    q["phase"] = float((p["phase"] + off) % (2 * math.pi))
    return Applied(q, _identity, dict(phase_offset_rad=off,
                                      phase_a=p["phase"], phase_b=q["phase"]))


TRANSFORMS: dict[str, tuple[Callable, str]] = {
    "leading_silence": (t_leading_silence, "5-120 ms of exact digital silence prepended"),
    "polarity": (t_polarity, "the whole record multiplied by -1"),
    "gain": (t_gain, "the whole record scaled by -12..+6 dB"),
    "gain_second_strike": (t_gain_second_strike, "-9..-2 dB on the SECOND strike only"),
    "noise_realisation": (t_noise_realisation, "same parameters, independent noise seed"),
    "free_phase": (t_free_phase, "same parameters, oscillator start phase redrawn"),
}


# ===========================================================================
# estimator pipelines (the thing under test), with their injected defects
# ===========================================================================
def _spectrum(ctx: Ctx, s: Signal):
    if ctx.hooked("PHASE_SENSITIVE_SPECTRUM"):
        n = len(s.x)
        X = np.real(np.fft.rfft(s.x * np.hanning(n)))
        return np.fft.rfftfreq(n, 1.0 / s.sr), X
    return am.spectrum(s.x, s.sr)


def _maybe_clip(ctx: Ctx, s: Signal) -> Signal:
    """An ASYMMETRIC clip, because that is what defeats both invariances at
    once: a scale-free descriptor becomes level-dependent (the gain rows) and a
    sign-free one becomes polarity-dependent (the polarity rows). A real signal
    path with unequal headroom on the two rails does exactly this, and
    `tools/probes/cymbal_candidate4_gain_invariance.py` exists because one
    candidate here was suspected of it."""
    if ctx.hooked("CLIP_BEFORE_MEASURE"):
        return Signal(np.clip(s.x, -0.95, 0.60), s.sr, s.meta)
    return s


def est_decay_tau(ctx: Ctx, s: Signal) -> Meas:
    if ctx.hooked("DECAY_FROM_ARRAY_START"):
        return _tau_from_array_start(s)
    if ctx.hooked("TWO_POINT_TAIL_DECAY"):
        return _tau_from_two_tail_points(s)
    s = _maybe_clip(ctx, s)
    return Meas.of(am.decay_tau(s.x, s.sr))


def _tau_from_array_start(s: Signal) -> Meas:
    """The 1/e crossing timed from the START OF THE ARRAY instead of from the
    envelope peak -- an absolute-time decay where an onset-relative one
    belongs, which is the class of mistake #101 was.

    It is very nearly right on a record with no lead, which is exactly why a
    defect of this shape survives: the error IS the lead, and the lead is
    usually small.

    WHY THIS IS THE SECOND VERSION. The first fitted a log-linear slope over a
    fixed 0.25 s window from the array start, and `--controls` reported
    `ls/decay_tau` REFUSED rather than FAIL: with a 120 ms pad the fitted slope
    is positive and the shipped variant declined to answer. A control that
    refuses has not demonstrated that the row catches anything, so it was
    replaced rather than its expectation relaxed."""
    env = am.analytic_envelope(s.x)
    pk = int(np.argmax(env))
    thresh = float(env[pk]) / math.e
    below = np.nonzero(env[pk:] < thresh)[0]
    if not len(below):
        return Meas(None, False, "the envelope never falls to 1/e")
    return Meas((pk + int(below[0])) / s.sr)     # <-- the defect: + pk, not - pk


def _tau_from_two_tail_points(s: Signal, t1: float = 0.80, t2: float = 1.05) -> Meas:
    """tau from the ratio of two envelope samples -- read where the record's
    noise floor is, not where its decay is.

    The damped fixture's tau is at most 90 ms, so by 0.80 s the decay is more
    than 8 tau down and 0.80-1.05 s is the -78 dB floor. Two points are enough
    to produce a confident tau from it, and that tau is a property of the NOISE
    REALISATION, which is the thing `noise/decay_tau` says must not move the
    answer. The absolute value is taken so that it always reports a number
    even when the later point is the louder one: answering anyway is the
    pathology, not a detail of the control.

    ALSO A SECOND VERSION. The first fitted `decay_tau` with `floor_db=-100`
    on the same record, reasoning that a fit window below the noise floor would
    make the floor's realisation matter. `--controls` showed it did not fire at
    all: the fit's amplitude weighting (`w = fit / fit.max()`) gives the floor
    samples a weight of 10^-3.9, so a window extended into the noise barely
    moves the slope. That is a fact about `decay_tau` worth having -- its
    weighting is what makes it robust here -- and it is why the control had to
    stop using the real estimator's own fit."""
    env = am.analytic_envelope(s.x)
    i1, i2 = int(t1 * s.sr), int(t2 * s.sr)
    if i2 >= len(env):
        return Meas(None, False, "record too short for the two tail points")
    a, b = float(env[i1]), float(env[i2])
    if min(a, b) <= 0:
        return Meas(None, False, "a zero envelope sample")
    r = abs(math.log(a / b))
    if r <= 0:
        return Meas(None, False, "the two points are equal")
    return Meas((i2 - i1) / s.sr / r)


def est_schroeder_t20(ctx: Ctx, s: Signal) -> Meas:
    if ctx.hooked("DECAY_FROM_ARRAY_START"):
        return _t20_from_array_start(s)
    return Meas.of(am.schroeder_t20(s.x, s.sr))


def _t20_from_array_start(s: Signal) -> Meas:
    """T20 read as "when did the backward energy curve reach -25 dB", measured
    from the start of the ARRAY instead of from the -5 dB point. Leading
    silence then enters the answer one millisecond for one millisecond."""
    x = am.strip_trailing_silence(s.x)
    e = np.cumsum((x * x)[::-1])[::-1]
    if e[0] <= 0:
        return Meas(None, False, "no energy")
    curve = 10 * np.log10(np.maximum(e / e[0], 1e-30))
    below = np.nonzero(curve <= -25.0)[0]
    if not len(below):
        return Meas(None, False, "curve never reaches -25 dB")
    return Meas(float(below[0]) / s.sr)


def est_onset_ms(ctx: Ctx, s: Signal) -> Meas:
    o = am.onsets(s.x, s.sr)
    if len(o) != 1:
        return Meas(None, False, f"expected one onset, found {len(o)}")
    return Meas(o[0] / s.sr * 1e3)


def est_norm_rms_db(ctx: Ctx, s: Signal) -> Meas:
    """A LEVEL-normalised descriptor: RMS of the record after its own peak has
    been divided out. #158's "gain must not move a level-normalised
    descriptor" -- and the permission comes from the NORMALISER in this
    pipeline, not from the estimator, which is why the same estimator read
    without it is an accent test and gain is not permitted there."""
    s = _maybe_clip(ctx, s)
    x = s.x
    norm = float(x.max()) if ctx.hooked("SIGNED_PEAK_NORM") else float(np.abs(x).max())
    if not np.isfinite(norm) or norm <= 0:
        return Meas(None, False, "no peak to normalise on")
    return Meas(am.db(am.rms(x / norm)))


def est_centroid(ctx: Ctx, s: Signal, band=(20.0, 20000.0)) -> Meas:
    s = _maybe_clip(ctx, s)
    if ctx.hooked("SHORT_WINDOW_SPECTRUM"):
        s = Signal(s.x[:2048], s.sr, s.meta)
    f, X = _spectrum(ctx, s)
    sel = (f >= band[0]) & (f <= band[1])
    w = X[sel] ** 2
    tot = float(np.sum(w))
    if tot <= 0:
        return Meas(None, False, "no energy in the band")
    return Meas(float(np.sum(f[sel] * w) / tot))


def est_band_ratio_db(ctx: Ctx, s: Signal, lo=(0.5, 2.5), hi=(2.5, 6.5)) -> Meas:
    """Energy in the first two partials against the next four, with the band
    edges placed BETWEEN harmonics as multiples of the fixture's own f0.

    WRONG-THEN-RIGHT, kept because the first number looked fine: the first
    version of this row used fixed 100-1000 Hz and 1-8 kHz edges, and measured
    a free-phase sensitivity of **0.45 dB** over 12 draws -- 200x what the
    centroid row sees. That was not the estimator. With f0 drawn over
    110-330 Hz a 1 kHz edge lands on a harmonic for some draws and between
    harmonics for others, and a Hann window's leakage across the edge depends
    on the start phase, so the row was measuring its own band edge. Anchoring
    the edges to f0 is what the case definition should have said in the first
    place; `--calibrate` prints what it now measures."""
    f, X = _spectrum(ctx, s)
    f0 = s.meta.get("f0")
    if f0 is None:
        return Meas(None, False, "this fixture does not declare an f0 to anchor to")
    out = []
    for a, b in ((lo[0] * f0, lo[1] * f0), (hi[0] * f0, hi[1] * f0)):
        sel = (f >= a) & (f < b)
        out.append(float(np.sum(X[sel] ** 2)))
    if min(out) <= 0:
        return Meas(None, False, "an empty band")
    return Meas(10 * math.log10(out[0] / out[1]))


def est_psd_slope(ctx: Ctx, s: Signal, band=(500.0, 8000.0)) -> Meas:
    if ctx.hooked("SINGLE_WINDOW_SLOPE"):
        return Meas.of(am.psd_slope_db_oct(s.x[:2048], band, s.sr, nfft=512))
    return Meas.of(am.psd_slope_db_oct(s.x, band, s.sr))


def est_accent_ratio_db(ctx: Ctx, s: Signal) -> Meas:
    """Level of the second strike relative to the first, in dB. A RATIO, so a
    uniform gain cannot move it; a per-strike gain is exactly what it
    measures."""
    s = _maybe_clip(ctx, s)
    split = s.meta["split"]
    a, b = s.x[:split], s.x[split:]
    if ctx.hooked("PER_STRIKE_NORMALISATION"):
        # What `run_case.prepare` does per RECORD, applied per STRIKE: the
        # accent is normalised away and the test measures nothing.
        a = a / max(float(np.abs(a).max()), 1e-30)
        b = b / max(float(np.abs(b).max()), 1e-30)
    ra, rb = am.rms(a), am.rms(b)
    if min(ra, rb) <= 0:
        return Meas(None, False, "a silent strike")
    return Meas(am.db(rb, ra))


# --- pairwise comparisons: the descriptor IS the comparison ----------------
def pair_correlated_mix_db(ctx: Ctx, a: Signal, b: Signal, info) -> Trial:
    """Mix level of A with the transformed side, against mixing A with itself.
    #158: polarity matters "when mixing correlated signals"."""
    n = min(len(a.x), len(b.x))
    ref = am.rms(a.x[:n] + a.x[:n])
    mix = am.rms(a.x[:n] + b.x[:n])
    if ref <= 0:
        return _refused("silent reference mix")
    # Floored at -300 dB: a polarity-reversed mix cancels to float noise, and
    # `am.db`'s own 1e-300 clamp would otherwise print several thousand dB in
    # the column a reader is meant to compare against a 40 dB floor.
    return Trial(max(am.db(mix, ref), -300.0), True, "", am.db(ref),
                 max(am.db(max(mix, 1e-300)), -300.0))


def pair_waveform_max_db(ctx: Ctx, a: Signal, b: Signal, info) -> Trial:
    """Peak sample-by-sample difference, relative to the peak of A.
    #158: independent noise realisations "must not require waveform
    equality" -- this row is the demonstration that waveform equality is the
    WRONG comparison for them, not a complaint about it."""
    n = min(len(a.x), len(b.x))
    pk = am.peak(a.x[:n])
    if pk <= 0:
        return _refused("silent side A")
    d = float(np.max(np.abs(a.x[:n] - b.x[:n])))
    return Trial(am.db(d, pk), True, "", am.db(pk), am.db(max(d, 1e-300)))


def pair_sample_rms_diff_db(ctx: Ctx, a: Signal, b: Signal, info) -> Trial:
    """RMS of the sample-wise difference at lag zero, relative to A.
    #158: free-running phase "must not invalidate a phase-insensitive
    comparison" -- and it does invalidate a phase-SENSITIVE one."""
    n = min(len(a.x), len(b.x))
    ra = am.rms(a.x[:n])
    if ra <= 0:
        return _refused("silent side A")
    return Trial(am.db(am.rms(a.x[:n] - b.x[:n]), ra), True, "", am.db(ra), None)


# ===========================================================================
# how a row turns two numbers into a verdict
# ===========================================================================
def per_side(est: Callable, basis: str, **kw) -> Callable:
    """Delta between the two sides' measurements, in `basis` units:
    "rel" fractional, "abs" in the estimator's own unit, "db" as 20log10 of the
    ratio."""
    def fn(ctx: Ctx, a: Signal, b: Signal, info) -> Trial:
        ma, mb = est(ctx, a, **kw), est(ctx, b, **kw)
        for side, m in (("A", ma), ("B", mb)):
            if not m.ok or m.value is None:
                return _refused(f"side {side} refused: {m.reason}")
        if basis == "rel":
            if abs(ma.value) <= 0:
                return _refused("side A measured zero; a relative delta is undefined")
            d = (mb.value - ma.value) / abs(ma.value)
        elif basis == "abs":
            d = mb.value - ma.value
        elif basis == "db":
            if min(abs(ma.value), abs(mb.value)) <= 0:
                return _refused("a zero level; a dB ratio is undefined")
            d = am.db(mb.value, ma.value)
        else:
            raise ValueError(basis)
        return Trial(float(d), True, "", ma.value, mb.value)
    return fn


@dataclass(frozen=True)
class Policy:
    """A row's threshold, and -- inseparably -- how the number was arrived at.

    `tightness` is the part a reader needs and a threshold table usually omits:

      "calibrated"  the threshold IS `SAFETY` x the worst calibration draw, so
                    it is as tight as the estimator's own spread allows.
                    `--calibrate` fails it both for being below that (it would
                    false-alarm) and for being more than `SLACK_FACTOR` above
                    it (it would be vacuous).
      "floor"       the threshold is a declared RESOLUTION floor -- one sample,
                    a float-noise bound, a cancellation depth -- and the draws
                    sit orders of magnitude inside it. Tightening it to 4x a
                    float-noise measurement would make it a portability
                    hazard, not a better gate, so the slack check does not
                    apply. What keeps a floor honest instead is that every row
                    carrying one has an injected control that must turn it
                    red (`EXPECT_RED`, asserted complete at import).
    """
    kind: str                  # permitted | tracks | moves_above | moves_below
    threshold: float
    how: str                   # how this number was chosen
    tightness: str = "calibrated"
    predict: str = ""          # key in `info` holding the applied magnitude
    calibrated: float = float("nan")   # the measurement behind `threshold`

    #: THE DIRECTION IS PART OF THE THRESHOLD, and getting it wrong is how the
    #: first version of this file passed `--inject BLANKET_INVARIANCE` on three
    #: of its five not-permitted rows. `abs(delta) >= floor` on a quantity
    #: already expressed in dB relative to the signal (-300 dB for two
    #: identical records) reads a PERFECT match as a large difference. A
    #: not-permitted row therefore declares which side of its floor it must
    #: land on, and `null_delta` on the Case declares what the quantity reads
    #: when the two records are identical, so the degenerate suite can be
    #: constructed as an input rather than argued about.
    @property
    def one_sided(self) -> bool:
        return self.kind.startswith("moves")

    def residual(self, delta: float, info: dict) -> float:
        """The quantity the threshold is compared against."""
        if self.kind == "tracks":
            return abs(delta - float(info[self.predict]))
        if self.one_sided:
            return delta
        return abs(delta)

    def passes(self, delta: float, info: dict) -> bool:
        r = self.residual(delta, info)
        if self.kind == "moves_above":
            return r >= self.threshold
        if self.kind == "moves_below":
            return r <= self.threshold
        return r <= self.threshold

    def binding(self, residuals: list[float]) -> float:
        """The draw nearest this threshold -- the one that decides whether it
        is satisfiable."""
        if not residuals:
            return float("nan")
        return min(residuals) if self.kind == "moves_above" else max(residuals)


@dataclass(frozen=True)
class Case:
    cid: str
    transform: str
    fixture: str
    estimator: str
    unit: str
    fn: Callable
    policy: Policy
    permitted: str
    not_permitted: str
    #: What this row's quantity reads when the two records are IDENTICAL. 0 for
    #: a difference between two measurements; -300 dB for a level read off a
    #: difference SIGNAL, which is this file's dB floor for exact cancellation.
    null_delta: float = 0.0

    @property
    def stochastic(self) -> bool:
        """Every row's fixture parameters are drawn, so every row is reported
        over repeated trials; these two transforms are the ones whose SECOND
        side is an independent draw of the same process rather than a
        deterministic function of the first."""
        return self.transform in ("noise_realisation", "free_phase")


# ===========================================================================
# preconditions -- asserted at the point of use, refusing rather than reporting
# ===========================================================================
def check_preconditions(c: Case, a: Signal, b: Signal, info: dict) -> str | None:
    for side, s in (("A", a), ("B", b)):
        if not np.all(np.isfinite(s.x)):
            return f"side {side} is not finite"
        if am.is_silent(s.x):
            return f"side {side} is silent"
        if am.clipped_fraction(s.x, 1.0) > 0:
            return f"side {side} is clipped at full scale"
    if c.fixture == "damped":
        lead = int(a.meta["lead_ms"] * 1e-3 * a.sr)
        if lead < 1 or am.peak(a.x[:lead]) > 0.02 * am.peak(a.x):
            return "side A does not carry the pre-onset lead it promises"
    if np.array_equal(a.x, b.x):
        return "the transform did not change the record"
    if c.policy.kind == "tracks" and abs(float(info[c.policy.predict])) < c.policy.threshold:
        return (f"the applied {c.policy.predict} is smaller than this row's own "
                f"tolerance; nothing could be tracked")
    return None


# ===========================================================================
# the matrix
# ===========================================================================
def _p(threshold, how, calibrated=float("nan"), tightness="calibrated") -> Policy:
    return Policy("permitted", threshold, how, tightness, calibrated=calibrated)


def _exact(threshold, how, calibrated=float("nan")) -> Policy:
    return Policy("permitted", threshold, how, "floor", calibrated=calibrated)


def _tracks(key, threshold, how, calibrated=float("nan"), tightness="calibrated") -> Policy:
    return Policy("tracks", threshold, how, tightness, predict=key, calibrated=calibrated)


def _moves_above(threshold, how, calibrated=float("nan")) -> Policy:
    return Policy("moves_above", threshold, how, "floor", calibrated=calibrated)


def _moves_below(threshold, how, calibrated=float("nan")) -> Policy:
    return Policy("moves_below", threshold, how, "floor", calibrated=calibrated)


#: Every threshold below was produced by `--calibrate` at CAL_TRIALS=96 on the
#: CALIBRATE_BASE seed stream (see the module docstring). `calibrated=` records
#: the measurement it came from so drift is visible in the diff, and
#: `--calibrate` re-derives both and fails on a threshold that has become
#: either unsatisfiable or vacuous.
CASES: list[Case] = [
    # ---------------- leading silence ------------------------------------
    Case("ls/decay_tau", "leading_silence", "damped", "decay_tau", "relative",
         per_side(est_decay_tau, "rel"),
         _exact(1e-9, "float-noise floor: the fit reads the same samples whether "
                "or not the pad is there, so the only difference is the length "
                "of the envelope's FFT. Worst of 96 draws: 1.7e-15", 1.678e-15),
         "Leading silence. `decay_tau` fits from the envelope PEAK, so the fit "
         "window is onset-relative and exact zeros before the strike carry no "
         "information it reads.",
         "A leading pad is NOT permitted for anything reading absolute time -- "
         "`ls/onset_time` is the same pad measured by a row where it must "
         "appear in full."),
    Case("ls/schroeder_t20", "leading_silence", "damped", "schroeder_t20", "relative",
         per_side(est_schroeder_t20, "rel"),
         _exact(1e-9, "float-noise floor: once the pad is outside the fit the "
                "two records ARE the same array. Worst of 96 draws: 7.0e-15",
                7.0e-15),
         "Leading silence. The backward energy curve is read from the -5 dB "
         "point over the record's SOUNDING extent (#139), so both the pad and "
         "trailing digital silence are outside the fit by construction.",
         "Low-level NOISE in place of the pad is NOT permitted and is not "
         "tested here: `schroeder_t20`'s own docstring records that it is "
         "signal by every measure the function has."),
    Case("ls/onset_time", "leading_silence", "damped", "onsets[0]", "ms",
         per_side(est_onset_ms, "abs"),
         _tracks("pad_ms", 0.05, "resolution floor: the onset index is an integer "
                 "sample, so 0.05 ms is 2.4 samples at 48 kHz -- and the pads "
                 "drawn are 5-120 ms, three orders larger. Worst tracking "
                 "residual over 96 draws: 1.4e-14 ms", 1.421e-14,
                 tightness="floor"),
         "Nothing. This row exists because #158 names timing as a case where a "
         "delay is the measurement: an onset time MUST move by the inserted "
         "silence, one millisecond for one millisecond.",
         "Leading silence. A latency or alignment measurement that did not "
         "move would be reporting the pad as zero delay."),

    # ---------------- polarity -------------------------------------------
    Case("pol/norm_rms_db", "polarity", "asym", "peak-normalised RMS", "dB",
         per_side(est_norm_rms_db, "abs"),
         _exact(1e-9, "exact invariance: |x| and |-x| are the same array, so the "
                "threshold is a float-noise floor, not a calibrated tolerance. "
                "All 96 draws returned a delta of exactly 0", 0.0),
         "Polarity. The normaliser is abs(x).max() and the descriptor is an "
         "RMS, both even functions of the sample values.",
         "Polarity is NOT permitted once this record is MIXED with another -- "
         "`pol/correlated_mix_db` is the same two records added together."),
    Case("pol/centroid", "polarity", "asym", "spectral_centroid", "relative",
         per_side(est_centroid, "rel"),
         _exact(1e-9, "exact invariance: the magnitude spectrum of -x equals that "
                "of x bin for bin. All 96 draws returned exactly 0", 0.0),
         "Polarity. A magnitude spectrum discards the sign.",
         "A PHASE-sensitive spectral comparison is not polarity-invariant; this "
         "row's threshold is 1e-9, so `--inject PHASE_SENSITIVE_SPECTRUM` on "
         "the free-phase rows is the same mistake caught elsewhere."),
    Case("pol/correlated_mix_db", "polarity", "asym", "mix level vs self-mix", "dB",
         pair_correlated_mix_db,
         _moves_below(-40.0, "resolution floor: the mix must drop at least 40 dB. "
                      "A polarity-reversed mix cancels to the -300 dB floor this "
                      "file clamps at, so the floor is not tightness-limited; "
                      "the margin over 96 draws is 260 dB", -300.0),
         "Nothing. #158 names mixing correlated signals as the case where "
         "polarity is the measurement.",
         "Polarity. Summing a record with its own reversal cancels it, so a "
         "mix-level comparison must see the reversal.",
         null_delta=0.0),

    # ---------------- gain ------------------------------------------------
    Case("gain/norm_rms_db", "gain", "asym", "peak-normalised RMS", "dB",
         per_side(est_norm_rms_db, "abs"),
         _exact(1e-9, "exact invariance: the peak normaliser divides the gain out "
                "before the RMS is taken. Worst of 96 draws: 3.6e-15 dB",
                3.553e-15),
         "Gain, and the permission comes from this PIPELINE's peak normaliser "
         "rather than from the estimator. #158's phrasing is precise: a "
         "level-NORMALISED descriptor.",
         "Gain is NOT permitted for the same RMS read without the normaliser, "
         "which is a level measurement, nor for an accent ratio between two "
         "strikes gained unequally (`gain/accent_second_only`)."),
    Case("gain/centroid", "gain", "asym", "spectral_centroid", "relative",
         per_side(est_centroid, "rel"),
         _exact(1e-9, "exact invariance: a centroid is a ratio of two sums that "
                "scale together. Worst of 96 draws: 7.8e-16", 7.752e-16),
         "Gain. The centroid is a ratio of two sums over the same bins, so a "
         "scale cancels in it exactly -- no normalising step is needed and "
         "none is applied here.",
         "Gain is NOT permitted once the signal path is non-linear: a clip, a "
         "saturation or an absolute spectral floor makes a scale-free "
         "descriptor level-dependent, which is what "
         "`--inject CLIP_BEFORE_MEASURE` reinstates."),
    Case("gain/decay_tau", "gain", "damped", "decay_tau", "relative",
         per_side(est_decay_tau, "rel"),
         _exact(1e-9, "float-noise floor: a scale is an additive offset on the "
                "log envelope and the fit window is relative to the peak, so "
                "the slope is the same arithmetic. Worst of 96 draws: 2.3e-14",
                2.331e-14),
         "Gain. tau is the slope of a log envelope and a scale is an additive "
         "offset on that log.",
         "Gain is NOT permitted when it moves the envelope into or out of the "
         "fit's own floor_db window on a record with a noise floor; the draws "
         "here keep the floor 78 dB down, which is a stated precondition of "
         "this row and not a general claim."),
    Case("gain/accent_ratio_db", "gain", "two_strike", "accent ratio", "dB",
         per_side(est_accent_ratio_db, "abs"),
         _exact(1e-9, "exact invariance: a uniform gain multiplies both strikes. "
                "Worst of 96 draws: 5.3e-15 dB", 5.329e-15),
         "A UNIFORM gain. The accent ratio is a ratio between two strikes of "
         "the same record.",
         "A PER-STRIKE gain, which is the next row. The two rows differ only "
         "in which part of the record the gain was applied to, and that is "
         "what makes 'the case definition decides' operational rather than "
         "rhetorical."),
    Case("gain/accent_second_only", "gain_second_strike", "two_strike",
         "accent ratio", "dB",
         per_side(est_accent_ratio_db, "abs"),
         _tracks("gain_db", 0.02, "resolution floor: 0.02 dB is far under the "
                 "2-9 dB accents drawn, and scaling one segment scales its RMS "
                 "exactly, so the residual is float noise. Worst tracking "
                 "residual over 96 draws: 8.9e-15 dB", 8.882e-15,
                 tightness="floor"),
         "Nothing. #158 names accent tests as the case where gain is the "
         "measurement.",
         "A gain on one strike. The measured ratio must track it within "
         "0.40 dB, which is why this row cannot be satisfied by a general "
         "invariance rule -- `--inject BLANKET_INVARIANCE` turns it red."),

    # ---------------- independent noise realisation -----------------------
    Case("noise/psd_slope", "noise_realisation", "noise", "psd_slope_db_oct", "dB/oct",
         per_side(est_psd_slope, "abs"),
         _p(0.60, "4x the worst of 96 calibration draws (0.148 dB/oct), which is "
            "set by the number of Welch segments the 2.0 s record provides",
            0.148084),
         "An independent realisation. The Welch average over 1/6-octave bins "
         "is an estimate of the PROCESS, and two realisations of one process "
         "share it to within the estimator's variance -- which is what the "
         "calibrated tolerance is.",
         "Waveform equality is NOT permitted to be required of two "
         "realisations (`noise/waveform_max_db`), and a single short window "
         "is not a realisation-invariant estimate of the slope at all "
         "(`--inject SINGLE_WINDOW_SLOPE`)."),
    Case("noise/centroid", "noise_realisation", "noise", "spectral_centroid", "relative",
         per_side(est_centroid, "rel"),
         _p(0.059, "4x the worst of 96 calibration draws (1.45 % of the centroid)",
            0.0145103),
         "An independent realisation, for a descriptor that integrates over "
         "the whole band.",
         "Per-BIN spectral equality is not permitted to be required; this row "
         "integrates precisely so that it is not asking for it."),
    Case("noise/decay_tau", "noise_realisation", "damped", "decay_tau", "relative",
         per_side(est_decay_tau, "rel"),
         _p(0.0042, "4x the worst of 96 calibration draws (0.103 % of tau)",
            0.00102561),
         "An independent realisation of the record's -78 dB floor. The decay "
         "itself is identical; only the noise under it is redrawn.",
         "A LOUDER floor is not covered by this row's tolerance and is not "
         "claimed to be: the floor level is a precondition of the draw."),
    Case("noise/waveform_max_db", "noise_realisation", "noise",
         "peak sample difference", "dB",
         pair_waveform_max_db,
         _moves_above(-12.0, "resolution floor: two independent realisations "
                      "differ at the order of the signal itself, so -12 dB is a "
                      "floor on what the comparison must be able to see rather "
                      "than a tight bound. Smallest of 96 draws: +1.22 dB, so "
                      "the floor has 13.2 dB it is not using", 1.21762),
         "Nothing. This row is #158's statement that waveform equality is the "
         "WRONG comparison for stochastic signals, made measurable.",
         "An independent realisation, for any sample-wise comparison. A suite "
         "that reported these two records as equal would be reporting that a "
         "bit-exact check is appropriate for noise.",
         null_delta=-300.0),

    # ---------------- free-running phase ----------------------------------
    Case("phase/centroid", "free_phase", "asym", "spectral_centroid", "relative",
         per_side(est_centroid, "rel"),
         _p(7.9e-05, "4x the worst of 96 calibration draws (2.0e-5 relative)",
            1.95089e-05),
         "A free-running start phase. The partial amplitudes are unchanged, so "
         "a magnitude-domain descriptor is unchanged up to the window's edge "
         "treatment -- which is what the calibrated tolerance covers.",
         "A sample-wise or cross-correlation-at-lag-zero comparison is NOT "
         "phase-insensitive (`phase/sample_rms_db`)."),
    Case("phase/band_ratio_db", "free_phase", "asym", "band ratio h1-2 / h3-6", "dB",
         per_side(est_band_ratio_db, "abs"),
         _p(0.0017, "4x the worst of 96 calibration draws (4.1e-4 dB) -- see the "
            "estimator's docstring for the 1500x this tightened by when the band "
            "edges were moved off the harmonics", 0.000411545),
         "A free-running start phase, for a ratio of band energies.",
         "The same ratio read off the REAL part of the transform is phase-"
         "sensitive, which is what `--inject PHASE_SENSITIVE_SPECTRUM` "
         "reinstates."),
    Case("phase/sample_rms_db", "free_phase", "asym", "RMS of the difference", "dB",
         pair_sample_rms_diff_db,
         _moves_above(-6.0, "resolution floor: a redrawn phase leaves a "
                      "difference at the order of the signal, so -6 dB is a "
                      "floor on what must be visible. Smallest of 96 draws: "
                      "-2.37 dB, so the floor has 3.6 dB it is not using",
                      -2.37286),
         "Nothing. A phase-SENSITIVE comparison is invalidated by a free "
         "phase, and #158 asks for exactly that distinction to be drawn.",
         "A free start phase, for any lag-zero sample comparison. This row is "
         "why `phase/centroid`'s calibrated tolerance is not a blanket rule.",
         null_delta=-300.0),
]

CASE_BY_ID = {c.cid: c for c in CASES}
assert len(CASE_BY_ID) == len(CASES), "duplicate case id"

NOT_PERMITTED = tuple(c.cid for c in CASES
                      if c.policy.one_sided or c.policy.kind == "tracks")

INJECTIONS: dict[str, Injection] = {
    "DECAY_FROM_ARRAY_START": Injection(
        "DECAY_FROM_ARRAY_START",
        "decay measured from the array's first sample, not from the onset (#101's shape)",
        ("ls/decay_tau", "ls/schroeder_t20")),
    "SIGNED_PEAK_NORM": Injection(
        "SIGNED_PEAK_NORM",
        "level normalised on x.max() instead of abs(x).max()",
        ("pol/norm_rms_db",)),
    "CLIP_BEFORE_MEASURE": Injection(
        "CLIP_BEFORE_MEASURE",
        "an ASYMMETRIC hard clip (-0.95, +0.60) ahead of the estimator: a "
        "scale-free descriptor becomes level-dependent and a sign-free one "
        "becomes polarity-dependent",
        ("gain/centroid", "gain/norm_rms_db", "gain/decay_tau",
         "gain/accent_ratio_db", "pol/centroid", "pol/norm_rms_db")),
    "SINGLE_WINDOW_SLOPE": Injection(
        "SINGLE_WINDOW_SLOPE",
        "the noise slope from a 2048-sample excerpt at nfft=512 instead of the whole record",
        ("noise/psd_slope",)),
    "SHORT_WINDOW_SPECTRUM": Injection(
        "SHORT_WINDOW_SPECTRUM",
        "the centroid from the first 2048 samples -- 43 ms of one realisation "
        "read as a property of the process",
        ("noise/centroid",)),
    "TWO_POINT_TAIL_DECAY": Injection(
        "TWO_POINT_TAIL_DECAY",
        "tau read from two envelope samples 0.80-1.05 s in, which on this "
        "record is the -78 dB noise floor rather than the decay",
        ("noise/decay_tau",)),
    "PHASE_SENSITIVE_SPECTRUM": Injection(
        "PHASE_SENSITIVE_SPECTRUM",
        "the real part of the transform read where its magnitude belongs",
        ("phase/centroid", "phase/band_ratio_db")),
    "PER_STRIKE_NORMALISATION": Injection(
        "PER_STRIKE_NORMALISATION",
        "each strike peak-normalised before the accent ratio -- what prepare() "
        "does per record, applied per strike",
        ("gain/accent_second_only",)),
    "BLANKET_INVARIANCE": Injection(
        "BLANKET_INVARIANCE",
        "every delta forced to zero: the degenerate suite that permits "
        "everything, which must red every not-permitted row",
        tuple(c.cid for c in CASES)),
}

#: What each injection must turn red. For eight of the nine that is the rows
#: whose pipeline it hooks; for BLANKET_INVARIANCE it is every row where a
#: difference is NOT permitted, because a no-difference reading satisfies every
#: permitted-difference check by construction.
EXPECT_RED: dict[str, tuple[str, ...]] = {
    name: (NOT_PERMITTED if name == "BLANKET_INVARIANCE" else inj.targets)
    for name, inj in INJECTIONS.items()
}

#: NO ROW SHIPS WITHOUT A DEFECT IT MUST CATCH. This is what keeps a
#: resolution-floor threshold from being vacuous: a floor is allowed to sit
#: orders of magnitude above the draws, but it is not allowed to sit above the
#: defect class the row exists to detect. Asserted at import so a row added
#: without a control cannot be merged quietly.
_uncovered = sorted(set(CASE_BY_ID) - {cid for v in EXPECT_RED.values() for cid in v})
assert not _uncovered, f"rows with no injected control: {_uncovered}"


# ===========================================================================
# running one row
# ===========================================================================
def case_seed(cid: str) -> int:
    """A STABLE per-row seed offset.

    `hash(str)` is salted per process (PEP 456), so the first version of this
    file -- which used it here -- produced a different draw set in every
    interpreter. The calibration table and the false-alarm rate are both
    claims about specific draws, and neither is reproducible if the draws
    move: two consecutive `--calibrate` runs disagreed by 70 % on
    `noise/decay_tau`'s binding draw, which is how this was found. `crc32` is
    fixed by the standard."""
    return zlib.crc32(cid.encode()) % 2**31


def run_trial(c: Case, seed_base: int, trial: int, inject: str | None) -> Trial:
    rng = np.random.default_rng([seed_base, trial, case_seed(c.cid)])
    draw, _ = FIXTURES[c.fixture]
    p = draw(rng)
    applied = TRANSFORMS[c.transform][0](p, rng)
    a = build(p)
    b = applied.post(build(applied.params_b))
    bad = check_preconditions(c, a, b, applied.info)
    if bad:
        return _refused(bad, **applied.info)
    ctx = Ctx(c.cid, inject)
    t = c.fn(ctx, a, b, applied.info)
    t.info = dict(applied.info)
    if t.ok and inject == "BLANKET_INVARIANCE":
        # The defeating input: a suite that answers "no difference" to every
        # comparison. Applied here, after the measurement, because that is
        # where a blanket invariance rule would live -- and with the row's OWN
        # no-difference reading, so a dB-of-a-difference row is handed -300 dB
        # rather than a zero that happens to clear a negative floor.
        t.delta = c.null_delta
    if t.ok and (t.delta is None or not np.isfinite(t.delta)):
        return _refused("delta is not finite", **applied.info)
    return t


@dataclass
class RowResult:
    case: Case
    verdict: str
    trials: int
    refusals: int
    exceedances: int
    residuals: list[float]
    reasons: list[str]

    @property
    def worst(self) -> float:
        """The draw nearest this row's threshold, in the row's own direction."""
        return self.case.policy.binding(self.residuals)

    def stat(self, q: float) -> float:
        return float(np.percentile(self.residuals, q)) if self.residuals else float("nan")

    @property
    def exceedance_upper(self) -> float:
        return clopper_pearson_upper(self.exceedances, max(self.trials - self.refusals, 1))

    def as_dict(self) -> dict:
        return dict(case=self.case.cid, verdict=self.verdict, transform=self.case.transform,
                    estimator=self.case.estimator, policy=self.case.policy.kind,
                    threshold=self.case.policy.threshold, unit=self.case.unit,
                    trials=self.trials, refusals=self.refusals,
                    exceedances=self.exceedances,
                    residual_mean=float(np.mean(self.residuals)) if self.residuals else None,
                    residual_p95=self.stat(95.0) if self.residuals else None,
                    residual_worst=self.worst if self.residuals else None,
                    exceedance_rate_upper95=self.exceedance_upper,
                    reasons=sorted(set(self.reasons)))


def run_case(c: Case, trials: int, seed_base: int, inject: str | None) -> RowResult:
    res, reasons, exceed, refus = [], [], 0, 0
    for i in range(trials):
        t = run_trial(c, seed_base, i, inject)
        if not t.ok:
            refus += 1
            reasons.append(t.reason)
            continue
        r = c.policy.residual(t.delta, t.info)
        res.append(r)
        if not c.policy.passes(t.delta, t.info):
            exceed += 1
    if refus > MAX_REFUSAL_RATE * trials:
        verdict = REFUSED
    elif exceed:
        verdict = FAIL
    else:
        verdict = PASS
    return RowResult(c, verdict, trials, refus, exceed, res, reasons)


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided 95 % upper bound on a binomial rate. With k = 0 this is the
    closed form 1 - alpha**(1/n), which is the honest way to report "we saw no
    false alarms in n draws": at n = 12 it is still 22 %."""
    if n <= 0:
        return 1.0
    if k <= 0:
        return 1.0 - alpha ** (1.0 / n)
    try:
        from scipy.stats import beta
        return float(beta.ppf(1.0 - alpha, k + 1, n - k))
    except Exception:                                   # pragma: no cover
        return min(1.0, (k + 1.0) / n)


# ===========================================================================
# reporting
# ===========================================================================
def run_suite(trials: int, seed_base: int, inject: str | None,
              cases: list[Case] | None = None) -> list[RowResult]:
    return [run_case(c, trials, seed_base, inject) for c in (cases or CASES)]


def print_suite(rows: list[RowResult], trials: int, inject: str | None) -> None:
    print("=" * 100)
    print(f"PERMITTED DIFFERENCES ({len(rows)} rows, {trials} trials each"
          + (f", inject {inject}" if inject else "") + ")")
    print("=" * 100)
    print(f"{'row':<28}{'policy':<13}{'unit':<10}{'thresh':>10}{'mean':>10}"
          f"{'p95':>10}{'binding':>10}{'exc':>5}{'ref':>5}{'exc<=':>8}  verdict")
    print("   (mean / p95 / binding are over the row's own trials; `exc<=` is the "
          "one-sided 95 %")
    print("    Clopper-Pearson upper bound on this row's per-trial exceedance rate "
          "-- with 0 of 12")
    print("    exceedances it is 22 %, which is what 12 trials can say and is "
          "reported rather than")
    print("    rounded to a single pass. `--false-alarm-rate N` is the same bound "
          "over N suite runs.")
    last = None
    for r in rows:
        if r.case.transform != last:
            last = r.case.transform
            print(f"-- {last}: {TRANSFORMS[last][1]}")
        print(f"   {r.case.cid:<25}{r.case.policy.kind:<13}{r.case.unit:<10}"
              f"{r.case.policy.threshold:>10.4g}"
              f"{(np.mean(r.residuals) if r.residuals else float('nan')):>10.4g}"
              f"{r.stat(95.0):>10.4g}{r.worst:>10.4g}"
              f"{r.exceedances:>5}{r.refusals:>5}"
              f"{r.exceedance_upper * 100:>7.1f}%  {r.verdict}")
        if r.verdict != PASS and r.reasons:
            for why in sorted(set(r.reasons))[:3]:
                print(f"        refused: {why}")
    reds = [r.case.cid for r in rows if r.verdict == FAIL]
    refs = [r.case.cid for r in rows if r.verdict == REFUSED]
    print(f"\n   FAIL {len(reds)}  REFUSED {len(refs)}  PASS "
          f"{sum(1 for r in rows if r.verdict == PASS)}")
    if reds:
        print(f"   red: {' '.join(reds)}")
    if refs:
        print(f"   refused: {' '.join(refs)}")


def print_policies() -> None:
    print("=" * 100)
    print("WHICH DIFFERENCES EACH CASE PERMITS, AND WHICH IT DOES NOT")
    print("=" * 100)
    for c in CASES:
        print(f"\n{c.cid}  [{c.policy.kind}]  {c.estimator} on {c.fixture}, "
              f"{c.transform}")
        print(f"   PERMITTED:     {c.permitted}")
        print(f"   NOT PERMITTED: {c.not_permitted}")
        print(f"   THRESHOLD:     {c.policy.threshold:g} {c.unit} -- {c.policy.how}")


# ===========================================================================
# calibration
# ===========================================================================
def _signif(v: float, digits: int = 2) -> float:
    if v == 0 or not np.isfinite(v):
        return v
    mag = math.floor(math.log10(abs(v)))
    step = 10 ** (mag - digits + 1)
    return math.ceil(abs(v) / step) * step * (1 if v > 0 else -1)


def calibrate(trials: int) -> int:
    print("=" * 100)
    print(f"CALIBRATION -- {trials} draws per row on the CALIBRATE_BASE seed stream")
    print("=" * 100)
    print("A PERMITTED or TRACKS threshold should be SAFETY x the worst draw; a MOVES")
    print("floor is a resolution floor and the margin to the smallest draw is reported.")
    print(f"{'row':<28}{'kind':<12}{'binding':>12}{'typical':>12}{'recommend':>12}"
          f"{'committed':>12}{'ref':>5}  state")
    bad = []
    for c in CASES:
        r = run_case(c, trials, CALIBRATE_BASE, None)
        if not r.residuals:
            print(f"   {c.cid:<25}{c.policy.kind:<10}{'--':>12}{'--':>12}{'--':>12}"
                  f"{c.policy.threshold:>12.4g}{r.refusals:>5}  NO DATA")
            bad.append(f"{c.cid}: every calibration draw refused")
            continue
        worst = r.worst
        if c.policy.one_sided:
            # The binding draw is the one closest to the floor from the
            # permitted side; the margin is how much room the floor is NOT
            # using, which is the number that says whether it is doing work.
            margin = (worst - c.policy.threshold if c.policy.kind == "moves_above"
                      else c.policy.threshold - worst)
            state = "FLOOR" if margin > 0 else "UNSATISFIABLE"
            if state == "UNSATISFIABLE":
                bad.append(f"{c.cid}: floor {c.policy.threshold:g} is on the wrong "
                           f"side of the binding draw {worst:g} -- it would "
                           f"false-alarm")
            print(f"   {c.cid:<25}{c.policy.kind:<12}{worst:>12.4g}"
                  f"{r.stat(50.0):>12.4g}{'--':>12}{c.policy.threshold:>12.4g}"
                  f"{r.refusals:>5}  {state} (margin {margin:+.3g} {c.unit})")
            if abs(c.policy.calibrated - worst) > max(0.25 * abs(worst), 1e-12):
                print(f"        NOTE committed `calibrated=` records "
                      f"{c.policy.calibrated:g}, this run measured {worst:g}")
            continue
        rec = _signif(worst * SAFETY)
        # `_signif` rounds UP, so a threshold committed FROM a previous run of
        # this report is bit-for-bit the recommendation it is compared against
        # -- and `0.059 < 0.059000000000000004` is True in binary floating
        # point. The slack is 1e-9 relative, nine orders under the two
        # significant figures the recommendation carries.
        if c.policy.threshold < rec * (1 - 1e-9) and worst > 0:
            state = "TIGHT"
            bad.append(f"{c.cid}: committed {c.policy.threshold:g} is below the "
                       f"recommendation {rec:g} ({SAFETY:g}x worst draw {worst:g})")
        elif c.policy.tightness == "floor":
            # A declared resolution floor is EXEMPT from the slack check -- see
            # Policy.tightness. What stands in for it is the row's injected
            # control, and `EXPECT_RED` is asserted complete at import.
            state = (f"FLOOR ({c.policy.threshold / worst:.3g}x the worst draw)"
                     if worst > 0 else "FLOOR")
        elif worst > 0 and c.policy.threshold > rec * SLACK_FACTOR:
            state = "SLACK"
            bad.append(f"{c.cid}: committed {c.policy.threshold:g} is more than "
                       f"{SLACK_FACTOR:g}x the recommendation {rec:g} -- vacuous")
        else:
            state = "OK"
        print(f"   {c.cid:<25}{c.policy.kind:<12}{worst:>12.4g}{r.stat(95.0):>12.4g}"
              f"{rec:>12.4g}{c.policy.threshold:>12.4g}{r.refusals:>5}  {state}")
        if abs(c.policy.calibrated - worst) > max(0.25 * worst, 1e-12) and worst > 0:
            print(f"        NOTE committed `calibrated=` records {c.policy.calibrated:g}, "
                  f"this run measured {worst:g}")
    if bad:
        print("\nCALIBRATION PROBLEMS")
        for b in bad:
            print(f"   {b}")
        return 1
    print("\nevery committed threshold is satisfiable and non-vacuous against this run")
    return 0


# ===========================================================================
# controls
# ===========================================================================
def run_controls(trials: int, seed_base: int) -> tuple[int, list[dict]]:
    out, bad = [], 0
    print("=" * 100)
    print("INJECTED CONTROLS -- each must turn its declared rows red, and only those")
    print("=" * 100)
    for name, inj in INJECTIONS.items():
        rows = run_suite(trials, seed_base, name)
        red = tuple(sorted(r.case.cid for r in rows if r.verdict == FAIL))
        want = tuple(sorted(EXPECT_RED[name]))
        refused = tuple(sorted(r.case.cid for r in rows if r.verdict == REFUSED))
        ok = red == want and not refused
        bad += 0 if ok else 1
        print(f"\n{'OK  ' if ok else 'FAIL'}  {name}")
        print(f"      {inj.what}")
        print(f"      expected red: {' '.join(want) or '(none)'}")
        print(f"      observed red: {' '.join(red) or '(none)'}")
        if refused:
            print(f"      REFUSED rows (a control may not refuse): {' '.join(refused)}")
        out.append(dict(injection=name, expected=list(want), observed=list(red),
                        refused=list(refused), ok=ok))
    print(f"\n   {len(INJECTIONS) - bad}/{len(INJECTIONS)} controls behaved as declared")
    return (1 if bad else 0), out


# ===========================================================================
# false-alarm rate
# ===========================================================================
def count_false_alarms(reps: int, trials: int,
                       cases: list[Case]) -> tuple[int, int, int]:
    """Red rows, refused rows and red RUNS over `reps` suite runs on FPR_BASE.

    The counting half of `false_alarm_rate`, over an arbitrary case list and
    without the per-row report. `safety_sweep` calls it rather than reimplementing
    the count, so the row it prints for the shipped factor is the same
    measurement `--false-alarm-rate` prints, on the same seeds."""
    red = ref = red_runs = 0
    for rep in range(reps):
        rows = run_suite(trials, FPR_BASE + rep * 1000, None, cases)
        n = sum(1 for r in rows if r.verdict == FAIL)
        red += n
        ref += sum(1 for r in rows if r.verdict == REFUSED)
        red_runs += 1 if n else 0
    return red, ref, red_runs


def scaled_cases(safety: float) -> list[Case]:
    """Every CALIBRATED threshold re-derived at a different safety factor.

    `tightness="floor"` rows are left alone on purpose: a resolution floor is
    not SAFETY x anything, so scaling it would sweep a parameter it does not
    depend on and would make the sweep's own rows incomparable."""
    out = []
    for c in CASES:
        if c.policy.tightness == "calibrated" and np.isfinite(c.policy.calibrated):
            pol = Policy(c.policy.kind, _signif(c.policy.calibrated * safety),
                         f"safety-sweep at {safety:g}x", c.policy.tightness,
                         c.policy.predict, c.policy.calibrated)
            out.append(Case(c.cid, c.transform, c.fixture, c.estimator, c.unit,
                            c.fn, pol, c.permitted, c.not_permitted, c.null_delta))
        else:
            out.append(c)
    return out


#: The rows SAFETY actually moves, and the (injection, row) pairs that bound it
#: from ABOVE: a threshold can always be made to stop false-alarming by raising
#: it, so a sweep that only measures false alarms recommends infinity.
CALIBRATED_ROWS = tuple(c.cid for c in CASES if c.policy.tightness == "calibrated")
DETECTION_PAIRS = tuple((name, cid) for name, inj in INJECTIONS.items()
                        if name != "BLANKET_INVARIANCE"
                        for cid in inj.targets if cid in CALIBRATED_ROWS)


def detection_at(cases: list[Case], trials: int, seed_base: int) -> int:
    """How many of the `DETECTION_PAIRS` still go red with this case list.

    The other half of the sweep. `--controls` asks this question of the shipped
    table; here it is asked of every candidate table, so the factor is bounded
    from both sides by measurement instead of from one side by measurement and
    the other by taste."""
    by_id = {c.cid: c for c in cases}
    return sum(1 for name, cid in DETECTION_PAIRS
               if run_case(by_id[cid], trials, seed_base, name).verdict == FAIL)


def safety_sweep(reps: int, trials: int,
                 factors=(0.5, 1.0, 2.0, 4.0, 8.0, 64.0, 512.0)) -> tuple[int, dict]:
    """WHY SAFETY IS 4 AND NOT A NUMBER SOMEBODY LIKED.

    `SAFETY` is the one constant in this file that a per-row `how` string
    cannot justify: every calibrated threshold is `SAFETY x` its worst
    calibration draw, so the whole table inherits it. CLAUDE.md's rule is to
    sweep a parameter before arguing about it, so this mode re-derives the five
    calibrated thresholds at each factor and measures the false-alarm rate of
    each resulting table on the FPR_BASE stream -- the stream none of them was
    calibrated on.

    What it shows is the shape the argument needs, and it needs BOTH columns.
    False alarms alone recommend infinity: any threshold stops false-alarming
    if you raise it far enough. So each candidate table is also handed the
    injected defects of the rows SAFETY moves (`DETECTION_PAIRS`), and the
    factor is bounded from above by the first one that stops catching them."""
    print("=" * 100)
    print(f"SAFETY SWEEP -- {reps} suite runs x {trials} trials at each factor, "
          f"on FPR_BASE")
    print("=" * 100)
    print(f"Only the {len(CALIBRATED_ROWS)} CALIBRATED thresholds move; the "
          f"{len(CASES) - len(CALIBRATED_ROWS)} resolution floors do not")
    print("depend on SAFETY and are held fixed. `shipped` marks the committed "
          "factor. `caught` is")
    print(f"how many of the {len(DETECTION_PAIRS)} (injected defect, calibrated "
          f"row) pairs still go red:")
    print("a table with no false alarms and no detection is the vacuous gate, not "
          "a good one.")
    print(f"{'safety':>8}{'red rows':>10}{'of':>8}{'rate':>9}{'upper95':>10}"
          f"{'red runs':>10}{'refused':>9}{'caught':>9}")
    rows = []
    for k in factors:
        cases = scaled_cases(k)
        red, ref, red_runs = count_false_alarms(reps, trials, cases)
        caught = detection_at(cases, trials, VALIDATE_BASE)
        n = reps * len(cases)
        print(f"{k:>8g}{red:>10}{n:>8}{red / n:>9.2%}"
              f"{clopper_pearson_upper(red, n):>10.2%}{red_runs:>10}{ref:>9}"
              f"{f'{caught}/{len(DETECTION_PAIRS)}':>9}"
              + ("   <- shipped" if k == SAFETY else ""))
        rows.append(dict(safety=k, red_rows=red, comparisons=n, rate=red / n,
                         upper95=clopper_pearson_upper(red, n),
                         red_runs=red_runs, refused=ref, caught=caught,
                         detection_pairs=len(DETECTION_PAIRS)))
    shipped = [r for r in rows if r["safety"] == SAFETY]
    rc, notes = 0, []
    if not shipped:
        notes.append(f"the shipped factor {SAFETY:g} is not in the sweep")
        rc = 1
    else:
        if shipped[0]["red_rows"]:
            notes.append(f"the shipped factor {SAFETY:g} false-alarms "
                         f"{shipped[0]['red_rows']} times")
            rc = 1
        if shipped[0]["caught"] != len(DETECTION_PAIRS):
            notes.append(f"the shipped factor {SAFETY:g} catches only "
                         f"{shipped[0]['caught']}/{len(DETECTION_PAIRS)} of its "
                         f"own injected defects")
            rc = 1
    if not any(r["red_rows"] for r in rows):
        # A sweep where nothing ever reds has not located the lower cliff, so it
        # has not shown that SAFETY is doing anything. That is a failure of the
        # SWEEP, not of the table: widen the factors.
        notes.append("NO factor false-alarmed, so this sweep does not bound "
                     "SAFETY from below -- widen `factors` downward")
        rc = 1
    if all(r["caught"] == len(DETECTION_PAIRS) for r in rows):
        # Not a failure: it is a fact about these defects, which are gross
        # compared with the estimators' own spread. It is printed because the
        # alternative is a reader assuming the upper bound was located.
        notes.append(f"every factor up to {max(factors):g}x still catches all "
                     f"{len(DETECTION_PAIRS)} injected defects, so this sweep "
                     f"bounds SAFETY from below only; the upper bound on margin "
                     f"is outside the range swept")
    if rc == 0:
        notes.insert(0, f"the shipped factor {SAFETY:g} false-alarms 0 times, "
                        f"catches all {len(DETECTION_PAIRS)} injected defects, "
                        f"and at least one smaller factor false-alarms")
    print()
    for n_ in notes:
        print(f"   {n_}")
    return rc, dict(reps=reps, trials=trials, shipped_safety=SAFETY, sweep=rows,
                    notes=notes)


def false_alarm_rate(reps: int, trials: int) -> tuple[int, dict]:
    """Re-run the whole suite `reps` times on fresh seeds and count the reds.

    #158: "with hundreds of comparisons, uncalibrated thresholds produce a
    steady stream of false regressions." This is the measurement that says
    whether ours do, BEFORE the suite is trusted in CI -- and it draws from
    FPR_BASE, which is disjoint from the stream the thresholds were calibrated
    on."""
    print("=" * 100)
    print(f"FALSE-ALARM RATE -- {reps} independent suite runs x {len(CASES)} rows "
          f"x {trials} trials")
    print("=" * 100)
    per_case = {c.cid: 0 for c in CASES}
    per_case_ref = {c.cid: 0 for c in CASES}
    red_runs = 0
    t0 = time.time()
    for rep in range(reps):
        rows = run_suite(trials, FPR_BASE + rep * 1000, None)
        reds = [r.case.cid for r in rows if r.verdict == FAIL]
        for r in rows:
            if r.verdict == FAIL:
                per_case[r.case.cid] += 1
            if r.verdict == REFUSED:
                per_case_ref[r.case.cid] += 1
        if reds:
            red_runs += 1
            print(f"   run {rep}: RED {' '.join(reds)}")
    n_comp = reps * len(CASES)
    k = sum(per_case.values())
    rate = k / n_comp
    up = clopper_pearson_upper(k, n_comp)
    print(f"\n   suite runs with any red:  {red_runs}/{reps}")
    print(f"   row-level false alarms:   {k}/{n_comp} = {rate:.4%}")
    print(f"   one-sided 95% upper bound on the per-row false-alarm rate: {up:.4%}")
    print(f"   (a row's verdict is the worst of {trials} trials, so the per-TRIAL "
          f"rate is lower still)")
    worst = sorted(((v, c) for c, v in per_case.items() if v), reverse=True)
    for v, c in worst:
        print(f"   {c}: {v} false alarms in {reps} runs")
    refs = sorted(((v, c) for c, v in per_case_ref.items() if v), reverse=True)
    for v, c in refs:
        print(f"   {c}: REFUSED in {v}/{reps} runs")
    print(f"   {time.time() - t0:.1f} s")
    summary = dict(reps=reps, trials=trials, rows=len(CASES), comparisons=n_comp,
                   false_alarms=k, rate=rate, upper95=up, red_runs=red_runs,
                   per_case={c: v for c, v in per_case.items() if v},
                   refused={c: v for c, v in per_case_ref.items() if v})
    return (1 if (k or refs) else 0), summary


# ===========================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trials", type=int, default=TRIALS_DEFAULT,
                    help=f"trials per row (default {TRIALS_DEFAULT})")
    ap.add_argument("--inject", choices=sorted(INJECTIONS),
                    help="reinstate one defect in its declared target rows")
    ap.add_argument("--expect-fail", action="store_true",
                    help="with --inject: exit 0 only if the declared rows went red")
    ap.add_argument("--calibrate", action="store_true",
                    help=f"re-derive every threshold from {CAL_TRIALS} fresh draws")
    ap.add_argument("--cal-trials", type=int, default=CAL_TRIALS)
    ap.add_argument("--controls", action="store_true",
                    help="run every injected control and check its declared red set")
    ap.add_argument("--false-alarm-rate", type=int, default=0, metavar="N",
                    help="re-run the suite N times on fresh seeds and report the rate")
    ap.add_argument("--safety-sweep", type=int, default=0, metavar="N",
                    help=f"re-derive the calibrated thresholds at 0.5-8x and "
                         f"measure each table's false-alarm rate over N suite "
                         f"runs -- why SAFETY is {SAFETY:g}")
    ap.add_argument("--policies", action="store_true",
                    help="print what each case permits and does not permit")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)

    report: dict = dict(trials=a.trials, inject=a.inject, rows=len(CASES))
    rc = 0

    if a.policies:
        print_policies()
        return 0
    if a.calibrate:
        return calibrate(a.cal_trials)
    if a.controls:
        rc, report["controls"] = run_controls(a.trials, VALIDATE_BASE)
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(report, indent=2))
        return rc
    if a.safety_sweep:
        rc, report["safety_sweep"] = safety_sweep(a.safety_sweep, a.trials)
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(report, indent=2))
        return rc
    if a.false_alarm_rate:
        rc, report["false_alarm"] = false_alarm_rate(a.false_alarm_rate, a.trials)
        if a.json:
            a.json.parent.mkdir(parents=True, exist_ok=True)
            a.json.write_text(json.dumps(report, indent=2))
        return rc

    rows = run_suite(a.trials, VALIDATE_BASE, a.inject)
    print_suite(rows, a.trials, a.inject)
    report["results"] = [r.as_dict() for r in rows]
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(report, indent=2))

    red = tuple(sorted(r.case.cid for r in rows if r.verdict == FAIL))
    refused = tuple(sorted(r.case.cid for r in rows if r.verdict == REFUSED))
    if a.inject:
        want = tuple(sorted(EXPECT_RED[a.inject]))
        ok = red == want and not refused
        print(f"\n   control {a.inject}: expected red {' '.join(want)}")
        print(f"                     observed red {' '.join(red) or '(none)'}")
        if a.expect_fail:
            print(f"   {'OK' if ok else 'FAIL'}: the control behaved as declared")
            return 0 if ok else 1
        return 1 if red else 0
    if red:
        return 1
    if refused:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

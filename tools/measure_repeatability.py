#!/usr/bin/env python3
"""How much does a real TR-808 differ from itself? -- issue #111.

    tools/measure_repeatability.py --self-test    estimator noise, first
    tools/measure_repeatability.py --audit        is the corpus repeats at all?
    tools/measure_repeatability.py --measure      the machine's own spread
    tools/measure_repeatability.py --all [--json OUT]

Every tolerance on the scorecard -- 3.0 dB on an energy ratio, 10 % on an f0,
50 % on a time -- is a *convention*. None has a measurement of the machine's
own variation behind it, so nobody knows whether a case that fails at 1.036 is
a defect or a tolerance finer than the machine. This tool answers that with
recordings, or REFUSES and says why.

THREE OUTCOMES, kept apart, the repository's verifier convention:

    exit 0   the spread was measured
    exit 1   it was measured and something it asserts is false
    exit 2   REFUSED -- a precondition is unmet, so nothing was measured

WHAT IT REFUSES, AND THE ONE THAT FIRED
---------------------------------------
`refaudio/README.md` and issue #111 both state that the 808 From Mars clean
bass drum is **24 settings x 6 takes = 144 files** and is "the only place in
the corpus where the same machine plays the same thing more than once."

**It is not. The trailing `01`..`06` is the TONE knob, not a take index.**
`--audit` establishes that from the files rather than from the file names, and
`--measure` therefore refuses the take-to-take question and answers the one the
corpus *can* answer: the same machine, the same nominal knob positions, two
independent recording sessions years apart (the vendor's current edition and
its superseded legacy edition), which is session-to-session reproducibility --
a strictly larger and more relevant quantity than take-to-take, because it is
what anyone comparing against a single recorded reference is actually exposed
to.

ORDER OF OPERATIONS, AND WHY
----------------------------
The estimator is measured before the machine is. An estimator with its own
scatter measures itself, and the ratio of the two is the only thing that says
whether a machine number means anything. `--self-test` runs three controls:

  1. DETERMINISM -- the same array six times must give bit-identical answers.
     Zero, or every number below is contaminated by the estimator's own RNG.
  2. GROUND TRUTH -- a synthetic bass drum whose f0 and T20 are known in closed
     form, so an estimator that is stable but wrong is still caught.
  3. EDITING NOISE -- one real recording, six copies differing only by what the
     vendor's *editor* did and the machine did not: the start-trim and
     end-trim jitter actually observed in the corpus. That spread is the floor;
     a machine spread beneath it is not a measurement.

The estimators are IMPORTED from `tools/run_case.py`, never re-implemented, so
what is characterised here is the path the board actually scores through --
including its known defects. Where a defect biases a spread, the defect is
measured rather than described: see `--audit`'s windowing column (#101) and
`truncation_sensitivity` (#118).

WHAT THIS CANNOT DO
-------------------
It bounds ONE machine. Every real-808 recording reachable from this repository
descends from a single unit, and nominally different "808" sets cross-correlate
at 1.000 -- the same events re-pressed. Unit-to-unit is the larger term and
there is no data for it here at all. So the result is a FLOOR on the machine's
variation. A tolerance already tighter than the floor is definitely wrong; one
wider than it is merely unproven.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import math
import pathlib
import re
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "model"))
sys.path.insert(0, str(REPO / "tools"))

import audio_measure as am                                        # noqa: E402
import run_case as rc                                             # noqa: E402
import promoted_bands as pb                                       # noqa: E402
import promoted_measures as pm                                    # noqa: E402

MEASURED, FALSE, REFUSED = 0, 1, 2

CACHE = REPO / "refaudio" / "cache"
CURRENT = (CACHE / "808-from-mars" / "808 From Mars" / "WAV" /
           "01. Individual Hits" / "01. Bass Drum" / "Clean")
LEGACY = (CACHE / "808_from_mars_legacy" / "808 From Mars - Legacy" / "WAV" /
          "1. Individual Hits" / "01. Bass Drum" / "1. Clean")

FETCH = ("tools/refaudio_local.py --prefix 808-from-mars.zip "
         "'808 From Mars/WAV/01. Individual Hits/01. Bass Drum/Clean/'")


class Refused(Exception):
    """A precondition of the apparatus failed. Nothing was attempted."""


def longest_true_run(mask) -> int:
    """Longest run of True. `audio_measure.longest_plateau` is the wrong tool
    for this -- it is the longest run of any identical value, which on a mask
    that is mostly False is the silence."""
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return 0
    best = run = 0
    for v in m:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


# ===========================================================================
# 1. Loading, with the preconditions asserted at the point of use
# ===========================================================================
def load(path: pathlib.Path) -> tuple[np.ndarray, int]:
    """One recording as float64 mono, or a REFUSAL.

    24-bit files, so NOT `scipy.io.wavfile` + /32768 the way `run_case.py`
    reads the 16-bit Fischer set: that divisor is wrong by 256 here and would
    read every file 48 dB hot. Level is normalised away downstream, which is
    exactly why a scaling error of this kind survives unnoticed -- so it is
    asserted here instead: a correctly read 24-bit file peaks below 1.0."""
    import soundfile as sf
    if not path.is_file():
        raise Refused(f"{path.name} is not in refaudio/cache -- run:\n         {FETCH}")
    x, sr = sf.read(str(path), dtype="float64", always_2d=False)
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    if am.is_silent(x):
        raise Refused(f"{path.name} is silent")
    pk = float(np.abs(x).max())
    if not (0.0 < pk <= 1.0):
        raise Refused(f"{path.name} peaks at {pk:.4f}: not a correctly scaled float read")
    # A clipped file cannot carry an energy ratio. But a SINGLE sample at
    # exactly full scale is peak normalisation, not clipping -- the legacy
    # pack is normalised per file and 57 of its 144 bass drums have exactly
    # one such sample, none of them consecutive. Clipping is a PLATEAU at the
    # rail; refuse on that and not on the level.
    run = longest_true_run(np.abs(x) >= 1.0 - 1e-9)
    if run > 1:
        raise Refused(f"{path.name} is flat-topped at the rail: {run} consecutive samples")
    if int(sr) != 44100:
        raise Refused(f"{path.name} is {sr} Hz; the indexed corpus is 44.1 kHz")
    return x, int(sr)


#: (decay letter, tone index) -> path, for one chain/accent folder.
def grid(folder: pathlib.Path, pattern: re.Pattern) -> dict[tuple[str, int], pathlib.Path]:
    out: dict[tuple[str, int], pathlib.Path] = {}
    for p in sorted(folder.glob("*.wav")):
        m = pattern.match(p.name)
        if m:
            out[(m.group("decay"), int(m.group("tone")))] = p
    return out


CUR_RE = re.compile(r"BD (?P<accent>[AB]) 808 (?:Tape )?Decay (?P<decay>[A-F]) (?P<tone>\d\d)\.wav$")
LEG_RE = re.compile(r"BD_(?:Clean|Tape)_(?:NoAcc|Acc)_(?P<decay>[A-F])_808_(?P<tone>\d\d)\.wav$")


def current_grid(accent: str = "A", chain: str = "Digital"):
    return grid(CURRENT / chain / accent, CUR_RE)


def legacy_grid(accent: str = "1. No Accent", chain: str = "1. Digital"):
    return grid(LEGACY / chain / accent, LEG_RE)


# ===========================================================================
# 2. The metrics -- the board's own estimators, imported, not re-implemented
# ===========================================================================
#: name -> (units, estimator(y, sr) -> Estimate, tolerance rule, on_the_board)
#
# The first three ARE case D01A's `required_measurements`, taken straight off
# `run_case.DRUM_PLAN["BD"]` so they cannot drift from what the board scores.
# The rest are board estimator FAMILIES that other drum cases use, pointed at
# the bass drum, because #111 asks for the spread of "every metric the board
# scores" and the bass drum is the only voice that can supply one.
def metrics() -> dict:
    m = {}
    for name, units, est, tol in rc.DRUM_PLAN["BD"]:
        m[name] = (units, est, tol, "D01A")
    m["body spectrum"] = ("dB", rc._split_db("BD", 0.0, 0.150), rc.tol_db,
                          "D03A-D08A family")
    m["body spectrum (padded)"] = ("dB", _split_db_padded("BD", 0.150), rc.tol_db,
                                   "#101 control")
    m["attack"] = ("ms", rc._attack("BD"), rc.tol_time, "D02A/D09A family")
    m["pitch drop"] = ("Hz", rc._pitch_drop("BD"), rc.tol_frequency_of_f0,
                       "D03A/D05A/D07A family")
    m["band energy 20-200"] = ("dB", _band_frac_db("BD", 0, 0.150), rc.tol_db,
                               "band energies")
    m["band energy 200-2000"] = ("dB", _band_frac_db("BD", 1, 0.150), rc.tol_db,
                                 "band energies")
    # #138's two promoted estimators, registered HERE and not only in
    # tools/promoted_bands.py, because THIS is the harness that produces a
    # floor. Without one `band_tolerance` refuses and neither metric can join
    # the board; with one, the three verdicts this file already applies to
    # every other metric apply to them unchanged -- finer-than-the-machine,
    # dominated-by-the-apparatus, and wider-than-the-whole-knob-travel, which
    # is the guard that matters most here because `lowband_level_db`
    # SATURATES on the bass drum (0.25 dB of travel across the Fischer grid,
    # docs/promoted-bands-results.json). A floor that produced a usable-
    # looking tolerance on a metric with no travel would be a gate that
    # cannot fail, and `verdicts` is what says so.
    m["lowband_level_db"] = ("dB", _promoted("lowband_level_db"),
                             _promoted_tol("lowband_level_db"),
                             "#138 promotion, not yet on the board")
    m["dominant_period_ms"] = ("ms", _promoted("dominant_period_ms"),
                               _promoted_tol("dominant_period_ms"),
                               "#138 promotion, not yet on the board")
    return m


#: The bass drum's fundamental sits at 49.2-50.6 Hz across both sessions
#: (`session_to_session.metrics["Pitch trajectory"]`) and the TR-808 bass drum
#: has NO tuning control, so one fixed search band holds the fundamental and
#: excludes its second harmonic at every setting this corpus has. A per-voice
#: band is required -- `promoted_measures.dominant_period_ms` deliberately has
#: no default -- and this is the bass drum's.
BD_PERIOD_HZ = (40.0, 100.0)


def _promoted(metric: str):
    """A #138 promoted estimator, read on the clip IT was defined on.

    `model/promoted_measures.py`'s header states both estimators are measured
    on `test_discrimination.condition`'s output: onset-aligned, 240 ms, DC
    removed, 20 Hz high-passed. Every other metric in `metrics()` reads the
    whole 3 s record. **A floor measured on a different window is a floor for
    a different quantity** -- exactly the conflation that header warns about
    -- so the conditioning is applied here rather than assumed away, and the
    floor this harness reports is the floor of the shipped estimator.

    The import is lazy because the study harness costs ~1.5 s to load and most
    callers of `metrics()` only read the plan. A conditioning failure becomes
    a REFUSAL, not an exception: `measure_all` records it as such and
    `cross_session` drops the pair rather than reporting a number taken on a
    clip it could not prepare."""
    def f(y, sr):
        import test_discrimination as td
        try:
            c = td.condition(np.asarray(y, float), sr, level_match=False)
        except Exception as why:                        # noqa: BLE001 - see above
            return am.Estimate(None, False, f"conditioning refused: {why}", {})
        if metric == "lowband_level_db":
            return pm.lowband_level_db(c, sr)
        return pm.dominant_period_ms(c, sr, BD_PERIOD_HZ)
    return f


def _promoted_tol(metric: str):
    """A `run_case`-shaped tolerance rule with no fixed number in it.

    `promoted_bands.band_tolerance` derives the tolerance from this harness's
    own floor and the voice's knob travel and REFUSES (NaN) until both exist.
    Stored in the plan so the plan says where the tolerance comes from; the
    plan's tolerance slot is documentation, not something this file calls."""
    def f(ref: float, ctx: dict) -> tuple:
        return pb.band_tolerance(metric, "BD", (ctx or {}).get("ceiling"))
    return f


def _split_db_padded(sound: str, t1: float):
    """`_split_db` with 10 ms of silence in front of the strike.

    The #101 control, and **it has since become a cross-check rather than a
    correction.** When this was written, `run_case.prepare` clamped a short
    lead to zero, so `body spectrum` opened its window 0.16 ms before the
    strike -- inside `sosfiltfilt`'s 27-sample pad -- and the odd extension
    manufactured an edge worth up to 6.07 dB against a 3.0 dB tolerance. Every
    file in this corpus opens on the strike, so every one of them had it.
    `prepare` now guarantees 20 padlens of TRUE silence on both sides of every
    comparison, which is what this control was doing by hand.

    So the two now agree, and the pair is kept for that reason: prepending
    silence cannot change what the machine did, so `body spectrum` and `body
    spectrum (padded)` disagreeing again is the apparatus coming back.
    `test_the_shipped_band_split_does_not_move_when_the_head_trim_moves`
    asserts it, and shows the pre-repair path still moving."""
    inner = rc._split_db(sound, 0.0, t1 + 0.010)

    def f(y, sr):
        return inner(np.concatenate([np.zeros(int(0.010 * sr)), y]), sr)
    return f


def _band_frac_db(sound: str, which: int, t1: float):
    """The two band energies of `body spectrum`, each as its own fraction of
    total energy in dB, rather than only their ratio. A ratio hides which side
    moved; #111 asks for band ENERGIES."""
    b_lo, b_hi = rc.BAND[sound]
    split = rc.SPLIT_HZ[sound]

    def f(y, sr):
        seg = rc.window(y, sr, 0.0, t1)
        e = am.band_energy(seg, ((b_lo, split), (split, b_hi)), sr)
        v = float(e[which])
        if v <= 0:
            return am.Estimate(None, False, "band holds no energy", dict(frac=v))
        return am.Estimate(10.0 * math.log10(v), True, "", dict(frac=v))
    return f


def measure_all(y: np.ndarray, sr: int, plan: dict) -> dict:
    """Every metric on one prepared recording. A refusal stays a refusal."""
    out = {}
    for name, (_u, est, _t, _w) in plan.items():
        e = est(y, sr)
        out[name] = float(e.value) if e.ok else None
    return out


def truncation_sensitivity(y: np.ndarray, sr: int) -> float | None:
    """How much T20 moves when the record is shortened by 10 %.

    Issue #118: `schroeder_t20`'s guard is a LEVEL criterion (`tail_db`) and
    the backward integral of ANY finite record falls toward -inf at its last
    sample, so the guard cannot see truncation -- a 100 ms cut of a 92 ms T20
    reads -18.4 % while `tail_db` reports a comfortable -79 dB. Every file in
    this corpus is editor-trimmed, so the question is live for all of them.

    A LENGTH criterion, measured rather than asserted: cut 10 % more off and
    re-read. A record with enough decay after the fitting range does not care;
    a truncation-limited one moves. Returned in percent, so a caller can refuse
    on it.

    **#118 has since landed in `audio_measure.schroeder_t20` itself** -- the
    guard there is now a length criterion, `MIN_TAIL_T20` times the fitted T20
    after the -25 dB point, and it REFUSES. This probe therefore disables that
    guard on both reads (`min_tail_t20=0.0`): it is not trying to find out
    whether the record is long enough, it is trying to MEASURE how much the
    length is worth, and a refusal is not a measurement. The two now
    cross-check each other -- a record the guard refuses should be one this
    probe finds sensitive, and the corpus reads -0.00 % for every decay
    position while the guard passes every one of them."""
    seg = rc.window(y, sr, 0.005, None)
    full = am.schroeder_t20(seg, sr, min_tail_t20=0.0)
    if not full.ok:
        return None
    short = am.schroeder_t20(seg[: int(0.90 * len(seg))], sr, min_tail_t20=0.0)
    if not short.ok:
        return math.inf
    return 100.0 * (short.value - full.value) / full.value


# ===========================================================================
# 3. The audit: is a nominal repeat group actually repeats?
#
# `refaudio/README.md` and #111 both read the trailing `01`..`06` on the bass
# drum as round-robin take numbers. The file names do not say that, and a file
# name is not evidence. This asks the recordings.
#
# The vendor's own notes, in catalog.json's `about` for the pack, say what the
# folders are:
#
#     A / B / C -- A = No Accent, B = Accent, C = More Accent
#     Bass Drum / Clean -- "Multi-Sampled Levels of 808 Decay and Tone at 2
#     accent levels"
#
# so the grid is 2 chains x 2 accents x 6 DECAY x 6 TONE = 144, with no take
# axis at all. The naming convention is confirmed by the voices that have no
# knob to sweep: Cowbell, Rim Shot and Claves are 2 accents x 2 chains = FOUR
# clean files each and carry NO trailing number. The congas, "2 accent levels
# at 11 tunings", carry 01..11. The trailing number is a knob index wherever it
# appears.
#
# That is documentary evidence. The three tests below are physical, so the
# conclusion does not rest on a vendor's prose either.
# ===========================================================================
def spearman(y: list[float]) -> float:
    """Rank correlation of `y` against its own index order. +-1 is monotone.

    For six values in random order, P(|rho| = 1) = 2/6! = 1/360."""
    n = len(y)
    r = np.empty(n)
    r[np.argsort(np.argsort(np.asarray(y, float)))] = np.arange(n)
    i = np.arange(n, dtype=float)
    return float(np.corrcoef(r, i)[0, 1])


def align(sigs: list[np.ndarray], maxlag: int = 256) -> np.ndarray:
    """Integer-lag align to the first signal and truncate to a common length."""
    n = min(len(s) for s in sigs)
    ref = sigs[0][:n] - sigs[0][:n].mean()
    out = [sigs[0][:n]]
    for s in sigs[1:]:
        s = s[:n]
        best, bl = -np.inf, 0
        c = s - s.mean()
        for lag in range(-maxlag, maxlag + 1):
            v = float(np.dot(ref[maxlag:n - maxlag], c[maxlag + lag:n - maxlag + lag]))
            if v > best:
                best, bl = v, lag
        out.append(np.roll(s, -bl))
    m = np.array([o[maxlag:n - maxlag] for o in out])
    return m / np.abs(m).max(axis=1, keepdims=True)


def rank1_fraction(sigs: list[np.ndarray]) -> tuple[float, list[float]]:
    """Fraction of the between-recording variance carried by ONE component,
    and that component's loading per recording.

    Six recordings that differ by one knob are (fixed voice) + c_k x (fixed
    additive term): a rank-1 family, with loadings monotone in knob position.
    Six repeats of one setting differ by trigger jitter, thermal drift and
    noise -- several uncorrelated terms, no single component dominating and no
    reason for a monotone loading."""
    m = align(sigs)
    d = m - m.mean(axis=0, keepdims=True)
    s = np.linalg.svd(d, compute_uv=False)
    u, sv, _ = np.linalg.svd(d, full_matrices=False)
    loading = (u[:, 0] * sv[0]).tolist()
    if loading[-1] < loading[0]:
        loading = [-v for v in loading]
    return float(s[0] ** 2 / np.sum(s ** 2)), loading


def audit(report=print) -> tuple[bool, dict]:
    """Are the trailing-numbered bass-drum files repeats of one setting?

    Returns (is_repeats, evidence). False is the finding, not a failure."""
    ev: dict = {"groups": [], "vendor_note": "A/B = accent; per catalog.json "
                "the BD clean set is 'Multi-Sampled Levels of 808 Decay and "
                "Tone at 2 accent levels'"}
    plan = metrics()
    report("  group                       metric                   rho   span")
    mono = collections.Counter()
    for chain in ("Digital",):
        for accent in ("A", "B"):
            g = current_grid(accent, chain)
            if len(g) != 36:
                raise Refused(f"{chain}/{accent} holds {len(g)} of the 36 indexed files")
            for decay in "ABCDEF":
                sigs, vals = [], collections.defaultdict(list)
                for tone in range(1, 7):
                    x, sr = load(g[(decay, tone)])
                    sigs.append(x)
                    y = rc.prepare(x, sr)
                    for k, v in measure_all(y, sr, plan).items():
                        vals[k].append(v)
                frac, loading = rank1_fraction(sigs)
                row = {"group": f"{chain}/{accent}/Decay {decay}",
                       "rank1_variance_fraction": round(frac, 4),
                       "rank1_loading_spearman": round(spearman(loading), 4),
                       "metrics": {}}
                for k, v in vals.items():
                    if any(t is None for t in v):
                        continue
                    rho, span = spearman(v), max(v) - min(v)
                    row["metrics"][k] = {"spearman_vs_tone_index": round(rho, 4),
                                         "span": round(span, 4),
                                         "values": [round(t, 4) for t in v]}
                    if abs(rho) == 1.0:
                        mono[k] += 1
                ev["groups"].append(row)
                report(f"  {row['group']:26s} rank-1 variance {frac*100:5.1f} %  "
                       f"loading rho {spearman(loading):+.2f}")
                for k in ("body spectrum", "early/body energy", "Pitch trajectory", "decay"):
                    if k in row["metrics"]:
                        d_ = row["metrics"][k]
                        report(f"      {k:24s} rho {d_['spearman_vs_tone_index']:+.2f}"
                               f"   span {d_['span']:8.3f}")
    ev["monotone_group_count"] = dict(mono)
    ev["groups_total"] = len(ev["groups"])
    return False, ev


# ===========================================================================
# 4. The estimator, before the machine
#
# "If an estimator is itself noisy, you will measure the estimator rather than
# the machine." Three controls, in increasing severity.
# ===========================================================================
def synthetic_bd(sr: int = 44100, f0: float = 50.0, tau: float = 0.120,
                 seconds: float = 3.0, click: float = 0.25,
                 seed: int | None = None, lead: int = 8) -> np.ndarray:
    """A bass drum with a closed-form answer: one damped sinusoid at `f0` with
    amplitude time constant `tau`, plus a short click so the band split and the
    attack have something to measure.

    T20 of a single exponential is ln(10)*tau exactly, and that identity is
    `audio_measure`'s own ground truth for `schroeder_t20`, so an estimator
    that is STABLE but WRONG is still caught here.

    `lead` is 8 samples of silence in front of the strike, which is what this
    corpus has -- its onsets land on sample 5 to 9. It is not cosmetic: since
    #101's repair, `run_case.prepare` REFUSES a record that begins at or above
    2 % of its own peak, because such a record was cut into the strike and
    there is no pre-onset region to build a filter lead out of. Without a lead
    this fixture is exactly that record. Prepending silence cannot change any
    number here -- T20, f0 and every band ratio are invariant to it, which
    `test_run_case.py`'s invariance tests assert directly."""
    n = int(seconds * sr) - lead
    t = np.arange(n) / sr
    y = np.exp(-t / tau) * np.sin(2 * np.pi * f0 * t)
    k = int(0.002 * sr)
    y[:k] += click * np.exp(-np.arange(k) / (0.0004 * sr))
    if seed is not None:
        y = y + np.random.default_rng(seed).normal(0.0, 1e-5, n)
    y = np.concatenate([np.zeros(lead), y])
    return y / np.abs(y).max()


def self_test(report=print) -> tuple[bool, dict]:
    """Estimator noise, so the machine numbers have something to be a ratio to."""
    plan = metrics()
    sr = 44100
    ev: dict = {}
    ok = True

    # --- 1. determinism -----------------------------------------------------
    x = synthetic_bd(sr)
    runs = [measure_all(rc.prepare(x.copy(), sr), sr, plan) for _ in range(6)]
    nondet = [k for k in plan
              if len({None if r[k] is None else round(r[k], 12) for r in runs}) > 1]
    ev["determinism"] = {"identical": not nondet, "nondeterministic": nondet}
    report(f"  determinism      six runs of one array: "
           f"{'identical' if not nondet else 'DIFFER: ' + str(nondet)}")
    ok &= not nondet

    # --- 2. ground truth ----------------------------------------------------
    f0, tau = 50.0, 0.120
    y = rc.prepare(synthetic_bd(sr, f0=f0, tau=tau), sr)
    got_f0 = plan["Pitch trajectory"][1](y, sr)
    got_t20 = plan["decay"][1](y, sr)
    want_t20 = am.t20_from_tau(tau) * 1e3
    e_f0 = abs(got_f0.value - f0) / f0 * 100 if got_f0.ok else None
    e_t20 = abs(got_t20.value - want_t20) / want_t20 * 100 if got_t20.ok else None
    ev["ground_truth"] = {"f0_hz": f0, "f0_measured": got_f0.value, "f0_error_pct": e_f0,
                          "t20_ms": want_t20, "t20_measured": got_t20.value,
                          "t20_error_pct": e_t20}
    report(f"  ground truth     f0 {got_f0.value:.4f} Hz vs {f0} ({e_f0:+.3f} %)   "
           f"T20 {got_t20.value:.3f} ms vs ln(10)*tau = {want_t20:.3f} ({e_t20:+.3f} %)")
    ok &= (e_f0 is not None and e_f0 < 1.0) and (e_t20 is not None and e_t20 < 1.0)

    # --- 3. editing noise ---------------------------------------------------
    # The floor that matters. One REAL recording, six copies differing only by
    # what the vendor's editor did and the machine did not: where the file was
    # cut at the head and at the tail. Both jitters are taken from the corpus
    # itself rather than invented -- the onset lands on sample 5 to 9 across the
    # bass drums, and file lengths at one decay position spread by up to 0.8 %.
    g = current_grid("A", "Digital")
    x, sr = load(g[("C", 3)])
    copies = []
    for head in (0, 2, 4):
        for tail in (0, -0.004):
            z = x[head:]
            if tail:
                z = z[: int(len(z) * (1.0 + tail))]
            copies.append(measure_all(rc.prepare(z, sr), sr, plan))
    floor = {}
    for k in plan:
        v = [c[k] for c in copies if c[k] is not None]
        floor[k] = {"n": len(v), "span": (max(v) - min(v)) if len(v) > 1 else None,
                    "sd": float(np.std(v, ddof=1)) if len(v) > 1 else None}
    ev["editing_noise"] = floor
    report("  editing noise    one recording, six editor-trim variants:")
    for k, v in floor.items():
        if v["span"] is not None:
            report(f"      {k:26s} span {v['span']:9.4f} {plan[k][0]}   sd {v['sd']:.4f}")

    # --- truncation, #118 ---------------------------------------------------
    ts = {}
    for decay in "ABCDEF":
        xx, sr2 = load(g[(decay, 3)])
        ts[f"Decay {decay}"] = truncation_sensitivity(rc.prepare(xx, sr2), sr2)
    ev["t20_truncation_sensitivity_pct"] = ts
    report("  truncation (#118) T20 move when 10 % more of the record is cut:")
    report("      " + "  ".join(f"{k.split()[-1]} {v:+.2f} %" if v is not None
                                else f"{k.split()[-1]} n/a" for k, v in ts.items()))

    # START RED. A probe that answers 0.00 % on every file it is shown has not
    # been observed to fail, and this repository has shipped four harnesses in
    # that state. Cut a record while it is still sounding and the probe must
    # say so -- and `schroeder_t20`'s own tail_db guard must NOT, which is the
    # whole of #118.
    xx, sr2 = load(g[("E", 3)])
    yy = rc.prepare(xx, sr2)
    cut = yy[: int(0.40 * sr2)]
    red = truncation_sensitivity(cut, sr2)
    guard = am.schroeder_t20(rc.window(cut, sr2, 0.005, None), sr2)
    ev["truncation_red_test"] = {
        "cut_to_s": 0.40, "full_t20_ms": am.schroeder_t20(rc.window(yy, sr2, 0.005, None), sr2).value * 1e3,
        "cut_t20_ms": None if not guard.ok else guard.value * 1e3,
        "cut_tail_db": guard.detail.get("tail_db"),
        "probe_pct": red, "probe_fires": red is None or abs(red) > 5.0}
    t = ev["truncation_red_test"]
    said = "REFUSED" if not guard.ok else f"{t['cut_t20_ms']:.0f} ms, tail_db {t['cut_tail_db']:.0f}"
    report(f"  red test         a 400 ms cut of a {t['full_t20_ms']:.0f} ms T20: "
           f"length probe {red:+.1f} %; schroeder_t20 says {said}")
    ok &= bool(ev["truncation_red_test"]["probe_fires"])
    return ok, ev


# ===========================================================================
# 5. What the corpus CAN answer
#
# Take-to-take is refused: there are no repeats. Two quantities remain, and
# between them they say what #111 wanted to know -- whether a tolerance is
# finer than the machine.
#
#   KNOB TRAVEL. How far each metric moves across the machine's OWN controls,
#   over the whole 6 decay x 6 tone grid. A tolerance is only meaningful beside
#   this: one that is a large fraction of a knob's entire travel cannot tell
#   two settings apart, and one that is a small fraction of a single step is
#   asking for more resolution than the machine offers.
#
#   SESSION-TO-SESSION. The vendor recorded this machine twice, years apart --
#   the current edition and the superseded legacy edition. The DECAY letters do
#   NOT correspond between the two sessions (current Decay F reads a 2250 ms
#   T20, legacy Decay F reads 719 ms), so the vendor's six positions are
#   session-local labels and most settings cannot be compared at all. ONE can:
#   a knob against its end stop is reproducible without calibration, and Decay
#   A is the only letter whose T20 agrees across the two sessions -- 38.9 vs
#   38.3 ms, 1.4 % -- while every other letter disagrees by 10 to 213 %. That
#   pattern is what an end stop looks like.
#
#   And the bass drum's f0 HAS NO KNOB. The TR-808 bass drum offers LEVEL, TONE
#   and DECAY and no tuning control, so whatever f0 does between two sessions
#   is the machine and its converter clock, with no knob-setting error in it at
#   all. That makes f0 the one metric here that is cleanly attributable.
# ===========================================================================
def knob_travel(report=print) -> dict:
    """Every board metric's full range across the 6 x 6 decay/tone grid."""
    plan = metrics()
    out: dict = {}
    for accent in ("A", "B"):
        g = current_grid(accent, "Digital")
        vals = collections.defaultdict(list)
        per_axis = {"decay": collections.defaultdict(list), "tone": collections.defaultdict(list)}
        for (d, t), path in sorted(g.items()):
            x, sr = load(path)
            m = measure_all(rc.prepare(x, sr), sr, plan)
            for k, v in m.items():
                if v is None:
                    continue
                vals[k].append(v)
                per_axis["decay"][(k, d)].append(v)
                per_axis["tone"][(k, t)].append(v)
        row = {}
        for k in plan:
            v = vals[k]
            if len(v) < 2:
                continue
            dec = [np.mean(per_axis["decay"][(k, d)]) for d in "ABCDEF"
                   if per_axis["decay"][(k, d)]]
            ton = [np.mean(per_axis["tone"][(k, t)]) for t in range(1, 7)
                   if per_axis["tone"][(k, t)]]
            row[k] = {"units": plan[k][0], "n": len(v),
                      "grid_min": round(min(v), 4), "grid_max": round(max(v), 4),
                      "grid_span": round(max(v) - min(v), 4),
                      "decay_axis_span": round(max(dec) - min(dec), 4) if dec else None,
                      "tone_axis_span": round(max(ton) - min(ton), 4) if ton else None}
        out[f"accent {accent}"] = row
    # ACCENT is a control too, so "everything the machine's own controls can do"
    # is the union over both accent settings, not one of them.
    both = {}
    for k in out["accent A"]:
        if k not in out["accent B"]:
            continue
        a, b = out["accent A"][k], out["accent B"][k]
        lo, hi = min(a["grid_min"], b["grid_min"]), max(a["grid_max"], b["grid_max"])
        both[k] = {"units": a["units"], "grid_min": lo, "grid_max": hi,
                   "grid_span": round(hi - lo, 4),
                   "decay_axis_span": max(a["decay_axis_span"], b["decay_axis_span"]),
                   "tone_axis_span": max(a["tone_axis_span"], b["tone_axis_span"])}
    out["both accents"] = both
    report("  metric                      units      min        max       span"
           "    by DECAY   by TONE")
    for k, r in out["both accents"].items():
        report(f"  {k:26s} {r['units']:4s} {r['grid_min']:10.3f} {r['grid_max']:10.3f} "
               f"{r['grid_span']:10.3f} {r['decay_axis_span']:10.3f} {r['tone_axis_span']:9.3f}")
    return out


def cross_session(report=print) -> dict:
    """The same machine, two recording sessions, at the one knob position that
    is reproducible without calibration."""
    plan = metrics()
    cur = current_grid("A", "Digital")
    leg = legacy_grid("1. No Accent", "1. Digital")
    if len(cur) != 36 or len(leg) != 36:
        raise Refused(f"expected a 6x6 grid in each session, got {len(cur)} and {len(leg)}")

    # The end-stop evidence, stated as a table rather than assumed.
    t20 = rc._t20_ms(0.005)
    stops = {}
    for d in "ABCDEF":
        a, sa = load(cur[(d, 1)])
        b, sb = load(leg[(d, 1)])
        va, vb = t20(rc.prepare(a, sa), sa), t20(rc.prepare(b, sb), sb)
        if va.ok and vb.ok:
            stops[f"Decay {d}"] = {"current_t20_ms": round(va.value, 2),
                                   "legacy_t20_ms": round(vb.value, 2),
                                   "disagreement_pct": round(
                                       100 * abs(va.value - vb.value) / va.value, 1)}
    report("  which knob letters correspond between the two sessions?")
    for k, v in stops.items():
        report(f"      {k}   current {v['current_t20_ms']:9.2f} ms   "
               f"legacy {v['legacy_t20_ms']:9.2f} ms   {v['disagreement_pct']:6.1f} % apart")

    # Independence: two editions of one vendor's pack could be the same events
    # re-pressed. Nominally different "808" sets in this project's other corpora
    # cross-correlate at 1.000 and are exactly that.
    a, sa = load(cur[("A", 1)])
    b, sb = load(leg[("A", 1)])
    n = min(len(a), len(b))
    ca = a[:n] - a[:n].mean()
    cb = b[:n] - b[:n].mean()
    r = float(np.max(np.abs(np.correlate(ca / np.linalg.norm(ca),
                                         cb / np.linalg.norm(cb), "full"))))
    if r > 0.9999:
        raise Refused(f"the two editions cross-correlate at {r:.5f}: the same events "
                      "re-pressed, not a second recording session")
    report(f"  independence     best cross-correlation between editions r = {r:.4f} "
           f"(1.0000 would be a re-press)")

    # The measurement, at the end stop, across the tone axis.
    rows = {}
    for tone in range(1, 7):
        xa, sa = load(cur[("A", tone)])
        xb, sb = load(leg[("A", tone)])
        ma = measure_all(rc.prepare(xa, sa), sa, plan)
        mb = measure_all(rc.prepare(xb, sb), sb, plan)
        for k in plan:
            if ma[k] is None or mb[k] is None:
                continue
            rows.setdefault(k, []).append((ma[k], mb[k]))
    out = {"end_stop_evidence": stops, "cross_correlation": round(r, 5), "metrics": {}}
    report("  the machine, twice, at DECAY's counter-clockwise stop:")
    report("  metric                      units   session A   session B      diff"
           "     rel %")
    for k, pairs in rows.items():
        da = [abs(p[0] - p[1]) for p in pairs]
        rel = [100 * abs(p[0] - p[1]) / abs(p[0]) for p in pairs if p[0]]
        out["metrics"][k] = {"units": plan[k][0], "n": len(pairs),
                             "mean_current": round(float(np.mean([p[0] for p in pairs])), 4),
                             "mean_legacy": round(float(np.mean([p[1] for p in pairs])), 4),
                             "abs_diff_median": round(float(np.median(da)), 4),
                             "abs_diff_max": round(float(np.max(da)), 4),
                             "rel_diff_median_pct": round(float(np.median(rel)), 3) if rel else None}
        o = out["metrics"][k]
        report(f"  {k:26s} {o['units']:4s} {o['mean_current']:11.3f} {o['mean_legacy']:11.3f} "
               f"{o['abs_diff_median']:9.3f} {(o['rel_diff_median_pct'] or 0):9.2f}")
    return out


def f0_knob_attribution(report=print) -> dict:
    """How much of the cross-session f0 difference could be knob position?

    f0 is not directly settable on the TR-808 bass drum, but it is not
    completely independent of DECAY either -- across the current session's own
    decay axis it moves 1.77 Hz. So "the two sessions differ in f0" is only the
    machine if the two sessions' DECAY knobs are in the same place, and they
    are only known to be within the 1.3 % of T20 measured at the end stop.

    This bounds the knob-attributable part: take df0/dln(T20) from the current
    session's own A->B step, multiply by the observed ln(T20) mismatch. If that
    is small against the f0 difference, the difference is the machine."""
    cur = current_grid("A", "Digital")
    leg = legacy_grid("1. No Accent", "1. Digital")
    t20, f0 = rc._t20_ms(0.005), rc._f0("BD", 0.010, 0.500)

    def read(p):
        x, sr = load(p)
        y = rc.prepare(x, sr)
        return t20(y, sr).require("T20"), f0(y, sr).require("f0")

    tA, fA = read(cur[("A", 1)])
    tB, fB = read(cur[("B", 1)])
    tL, fL = read(leg[("A", 1)])
    slope = (fB - fA) / math.log(tB / tA)                     # Hz per ln(T20)
    knob_hz = abs(slope * math.log(tL / tA))
    got_hz = abs(fL - fA)
    out = {"df0_per_ln_t20_hz": round(slope, 4),
           "t20_mismatch_pct": round(100 * abs(tL - tA) / tA, 2),
           "f0_attributable_to_knob_hz": round(knob_hz, 4),
           "f0_difference_hz": round(got_hz, 4),
           "knob_share_pct": round(100 * knob_hz / got_hz, 2) if got_hz else None}
    report(f"  f0 attribution   df0/dln(T20) = {slope:+.3f} Hz; the sessions' T20 differ by "
           f"{out['t20_mismatch_pct']:.2f} %,")
    report(f"                   so at most {knob_hz:.4f} Hz of the {got_hz:.3f} Hz f0 "
           f"difference is knob position ({out['knob_share_pct']:.2f} %).")
    return out


# ===========================================================================
# 6. The verdicts. Each tolerance beside the three things it has to beat.
# ===========================================================================
#: For a tolerance to mean anything it has to sit above what the APPARATUS does
#: on its own and above what the MACHINE does on its own, and below what the
#: machine's own KNOBS do -- otherwise it cannot tell two settings apart.
def verdicts(machine: dict, estimator: dict, travel: dict, report=print) -> dict:
    rows = []
    cases = [
        ("energy ratio", 3.0, "dB", "body spectrum (padded)", "body spectrum",
         "the band split: D02A-D08A, D13A, D14A. Machine floor from the #101-"
         "corrected variant; apparatus noise from the path that ships"),
        ("energy ratio", 3.0, "dB", "early/body energy", "early/body energy", "D01A"),
        ("frequency", None, "Hz", "Pitch trajectory", "Pitch trajectory",
         "D01A, D04A, D06A, D08A -- 10 % of the reference"),
        ("time", None, "ms", "decay", "decay", "every drum case -- 50 % of the reference"),
        ("time", None, "ms", "attack", "attack", "D02A, D09A -- 50 % of the reference"),
    ]
    for basis, fixed, units, metric, shipped_metric, used_by in cases:
        m = machine["metrics"].get(metric)
        if m is None:
            continue
        ref = abs(m["mean_current"])
        tol = fixed if fixed is not None else (
            0.10 * ref if basis == "frequency" else 0.50 * ref)
        mach = m["abs_diff_median"]
        est = estimator["editing_noise"][shipped_metric]["span"]
        tr = travel["both accents"][metric]
        rows.append({
            "tolerance_basis": basis, "metric": metric, "units": units,
            "tolerance": round(tol, 4), "used_by": used_by,
            "machine_session_to_session": mach,
            "estimator_editing_noise": round(est, 4) if est else None,
            "knob_travel_full_grid": tr["grid_span"],
            "knob_travel_tone_axis": tr["tone_axis_span"],
            "tolerance_over_machine": round(tol / mach, 1) if mach else None,
            "tolerance_over_estimator": round(tol / est, 1) if est else None,
            "tolerance_over_tone_travel": round(tol / tr["tone_axis_span"], 2)
            if tr["tone_axis_span"] else None,
            # A tolerance wider than everything the machine's OWN controls can
            # do to a metric cannot distinguish any two settings of it.
            "tolerance_over_full_travel": round(tol / tr["grid_span"], 3)
            if tr["grid_span"] else None,
            # The ratio the brief asks for: below 1 the number describes the
            # apparatus, not the machine, and the spread is not a measurement
            # of anything the TR-808 did.
            "machine_over_estimator": round(mach / est, 2) if est else None,
            "finer_than_the_machine": bool(mach and tol < mach),
            "finer_than_the_apparatus": bool(est and tol < est)})
    report("  metric                        tol   machine  apparatus  mach/appar  tol/mach"
           "   tol/knob travel")
    for r in rows:
        report(f"  {r['metric']:24s} {r['tolerance']:8.3f} {r['machine_session_to_session']:9.3f} "
               f"{(r['estimator_editing_noise'] or 0):10.4f} "
               f"{(r['machine_over_estimator'] or 0):11.2f} "
               f"{(r['tolerance_over_machine'] or 0):9.1f} "
               f"{(r['tolerance_over_full_travel'] or 0):16.3f}")
    too_tight = [r["metric"] for r in rows if r["finer_than_the_machine"]]
    apparatus = [r["metric"] for r in rows
                 if r["machine_over_estimator"] is not None and r["machine_over_estimator"] < 1.0]
    blind = [r["metric"] for r in rows
             if r["tolerance_over_full_travel"] is not None and r["tolerance_over_full_travel"] > 1.0]
    report(f"  finer than the machine's own floor (scoring noise):  "
           f"{', '.join(too_tight) if too_tight else 'NONE'}")
    report(f"  dominated by the apparatus (mach/appar < 1):         "
           f"{', '.join(apparatus) if apparatus else 'NONE'}")
    report(f"  wider than the machine's whole knob travel (blind):  "
           f"{', '.join(blind) if blind else 'NONE'}")
    return {"rows": rows, "finer_than_the_machine": too_tight,
            "dominated_by_the_apparatus": apparatus,
            "wider_than_the_machines_knob_travel": blind}


# ===========================================================================
# 7. CLI
# ===========================================================================
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", type=pathlib.Path)
    a = ap.parse_args(argv)
    if not (a.self_test or a.audit or a.measure or a.all):
        a.all = True
    out: dict = {"issue": 111, "corpus": {
        "archive": "808-from-mars.zip", "second_session": "808_from_mars_legacy.zip",
        "path": "808 From Mars/WAV/01. Individual Hits/01. Bass Drum/Clean/Digital",
        "files": 144, "verified": "SHA-256 of the archive against refaudio/catalog.json, "
                                  "per-member size against refaudio/index/"}}
    try:
        if a.self_test or a.all:
            print("ESTIMATOR, before the machine")
            ok, ev = self_test()
            out["self_test"] = ev
            if not ok:
                print("REFUSED  the estimators did not pass their own controls; "
                      "a machine number taken with them would be meaningless")
                return REFUSED
            print()
        if a.audit or a.all:
            print("AUDIT    are the trailing-numbered files repeats of one setting?")
            is_repeats, ev = audit()
            out["audit"] = ev
            out["audit"]["is_repeats"] = is_repeats
            print()
            if not is_repeats:
                print("REFUSED  the take-to-take question, as #111 asks it, cannot be")
                print("         answered from this corpus: the trailing 01..06 is the TONE")
                print("         knob, so there are no repeated takes to take a spread over.")
                print("         808_loops_from_mars.zip, whose bass-drum-only 4/4 loops")
                print("         would hold repeated strikes inside one continuous take, is")
                print("         the one 808 pack of the three NOT present on this host.")
                print()
        if a.measure or a.all:
            print("KNOB TRAVEL   what each metric does across the machine's own controls")
            travel = knob_travel()
            out["knob_travel"] = travel
            print()
            print("SESSION TO SESSION   the same machine, recorded twice")
            machine = cross_session()
            machine["f0_attribution"] = f0_knob_attribution()
            out["session_to_session"] = machine
            print()
            print("VERDICTS   each tolerance beside what it has to beat")
            out["verdicts"] = verdicts(machine, out.get("self_test") or self_test(
                report=lambda *x: None)[1], travel)
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {a.json}")
    # The take-to-take question was refused, and a refusal is the outcome.
    return REFUSED if (a.all or a.audit) and not out.get("audit", {}).get("is_repeats", True) else MEASURED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

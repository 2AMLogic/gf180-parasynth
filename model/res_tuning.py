#!/usr/bin/env python3
"""Issue #257: a per-frame, resonance-keyed cutoff correction, measured.

    python3 model/res_tuning.py qualify      # the frequency estimator, on known signals
    python3 model/res_tuning.py sweep        # the two pre-registered sweeps (~4 min)
    python3 model/res_tuning.py validate     # baseline vs candidate on the UNTOUCHED grid (~1 min)

THE DEFECT (issue #237, `model/ladder_headroom.py`). The ladder's free ring sings
progressively flat of its commanded cutoff as resonance rises: +0.6 % at
res 1.02, -5.3 % at res 2.00, about 105 cents of travel across the resonance
knob. `CUT_TRIM` is one constant and cannot remove a resonance-dependent error.

THE CANDIDATE. Three changes to the cutoff -> coefficient path, taken together
because they all move the coefficient ROM and so cost one contract revision:

  1. a tuning law refitted to OUR linearised loop (`ladder_headroom
     .refit_tuning`) in place of `CUT_TRIM * fcr` -- `LAW_DEGREE`
  2. the 129 stored coefficient entries refitted to minimise the INTERPOLATED
     read error (`ladder_headroom.refit_rom_entries`) -- `REFIT_ENTRIES`
  3. a per-frame correction: a small Q1.15 table indexed by the host's k
     register (4*res, Q3.14) ABOVE the onset, which scales the commanded
     cutoff before both ROM reads -- `CORR_ENTRIES`

The correction's sequencing is what removes the circularity the curator
flagged between a corrected `g` and a corrected `k`: it is keyed on the HOST k
register, which nothing downstream modifies, and BOTH reads (`g_from_cut`,
`kc_from_cut`) then see the same corrected cutoff. DR 0006's compensation is
therefore re-derived unchanged in form -- `k_eff = res * k_onset(cut')` with
`g(cut')`, so `res = 1` is still the onset at every cutoff. Below the onset
(k <= 65536) the table reads its entry 0, which is unity, so the filter there
is bit-identical to the same ROMs without a correction.

WHAT IS GROUND TRUTH FOR WHAT
  * the frequency estimator is qualified on synthetic signals whose frequency
    is known by construction (`qualify_estimator`), never on the filter
  * the fit, the selection and the validation use three DISJOINT grids, all
    committed in `docs/res-tuning/plan.json` before any candidate was measured;
    this module refuses to run if that file is missing or modified
  * the shipped filter is the baseline and is measured by the same code path
    (`CorrectedLadder` with the shipped ROMs and no table is asserted
    bit-identical to `reference_rigs.OurLadder('ours')`)

WHAT THIS IS NOT. A model-level candidate. No RTL, no contract revision, no
regenerated expectation: see `spec/decision-records/0024-resonance-keyed
-cutoff-correction.md` for the datapath it proposes and what is still owed.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "audition"))

import audio_measure as am                                          # noqa: E402
import ladder_headroom as lh                                        # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import voice_fx as vf                                               # noqa: E402

SR = rr.SR
PLAN = os.path.join(ROOT, "docs", "res-tuning", "plan.json")
OUT_DIR = os.path.join(ROOT, "docs", "res-tuning")

# ---- the candidate's dials (each one a registered sensitivity record) -------
LAW_DEGREE = 3          # 0 = shipped CUT_TRIM * fcr; 3 / 4 = refitted polynomial
CORR_ENTRIES = 33       # 0 = no correction; else 2^b + 1 entries over res 1..2 (selected: sweeps.txt)
REFIT_ENTRIES = 1       # 1 = the 129 coefficient entries refitted for interpolation

# ---- the correction's integer contract --------------------------------------
CORR_Q = 15                         # table words: unsigned Q1.15, unity = 32768
CORR_K0 = 1 << 16                   # the host k register at res = 1.0 (Q3.14)
CORR_SPAN_LOG2 = 16                 # the table spans k = 65536 .. 131072 (res 1..2)
CORR_WORD_BITS = 16                 # Q1.15 unsigned: values 0 .. 1.99997

# ---- the estimator's thresholds (plan.json "estimator") ---------------------
MIN_CYCLES = 10
AGREE_REL = 0.005
SUSTAIN_FLOOR = 0.01                # last-quarter peak, full scale: a ring, not a dead band
SUSTAIN_DROP_DB = -1.0              # last vs first quarter peak


class Refused(RuntimeError):
    """A precondition of the measurement is unmet, so nothing was measured."""


# =============================================================================
# the plan, read at the point of use
# =============================================================================
def load_plan(path: str = PLAN, *, require_committed: bool = True) -> dict:
    """The committed grids and targets. REFUSES if the file is missing, or --
    with `require_committed` -- untracked or modified relative to HEAD: a target
    edited after its result was seen must be a visible commit, not a working
    tree change."""
    if not os.path.exists(path):
        raise Refused(f"no plan at {path}")
    if require_committed:
        rel = os.path.relpath(path, ROOT)
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
                                 capture_output=True, text=True)
        if tracked.returncode != 0:
            raise Refused(f"{rel} is not committed; a grid stated after the results is not a grid")
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT)
        if dirty.returncode != 0:
            raise Refused(f"{rel} is modified relative to HEAD; commit the change first")
    with open(path) as fh:
        plan = json.load(fh)
    if plan.get("schema") != "res-tuning-plan-v1" or plan.get("stated_before_results") is not True:
        raise Refused(f"{path}: not a res-tuning-plan-v1 stated before results")
    return plan


# =============================================================================
# the estimator, and its refusals
# =============================================================================
def _finite(y) -> np.ndarray:
    y = np.asarray(y, dtype=np.float64)
    if y.size == 0 or not np.all(np.isfinite(y)):
        raise Refused("the record is empty or holds non-finite samples")
    return y


def is_sustained(y) -> bool:
    """Whether a free-ring record holds a SUSTAINED oscillation: the last
    quarter's peak stands above `SUSTAIN_FLOOR` and has not fallen more than
    `SUSTAIN_DROP_DB` below the first quarter's. A growing ring is sustained.

    Two below-onset shapes it has to reject, both measured on the shipped
    filter: an ordinary decay (30 Hz, res 0.97: -4.2 dB across the window), and
    the fixed-point DEAD-BAND limit cycle the decay ends in -- 1131 Hz at res
    0.97 holds a perfectly steady 6e-4 full scale (20 LSB), which a ratio test
    alone would call sustained. The level floor is what rejects it.

    Defeating input, stated (verification-rules rule 8): a decay slow enough to
    lose under 1 dB across the window while staying above 0.01 -- a resonance a
    hair below the onset, at a low cutoff. Not constructed on the plan's grids:
    its below-onset point is res 0.97, where the slowest measured decay is
    -4.2 dB. The test suite pins that number so the margin is visible."""
    y = _finite(y)
    q = len(y) // 4
    if q < 1:
        raise Refused("record too short to compare its quarters")
    first, last = float(np.abs(y[:q]).max()), float(np.abs(y[-q:]).max())
    if last < SUSTAIN_FLOOR:
        return False
    return 20.0 * math.log10(last / max(first, 1e-300)) >= SUSTAIN_DROP_DB


def ring_frequency(y, sr: int = SR, *, min_cycles: int = MIN_CYCLES,
                   agree_rel: float = AGREE_REL) -> float:
    """The frequency of a sustained free ring, in Hz, or `Refused`.

    The value is the interpolated zero-crossing frequency, which is immune to
    the envelope smearing a growing ring puts on a spectrum; it is accepted only
    when the spectral peak agrees within `agree_rel`. Unlike
    `ladder_headroom._ring_ratio`, which falls back to the peak when the two
    disagree, a disagreement here is AMBIGUOUS and refused: two estimators of
    one quantity that disagree are not a measurement of it."""
    y = _finite(y)
    if am.is_silent(y, 1e-6):
        raise Refused("silent record")
    if not is_sustained(y):
        raise Refused("not a sustained ring (decaying, or a dead-band limit cycle)")
    z = am.zero_crossing_frequency(y, sr, min_crossings=min_cycles + 1)
    if not z.ok:
        raise Refused(f"insufficient duration: {z.reason} ({z.detail})")
    cycles = z.value * len(y) / sr
    if cycles < min_cycles:
        raise Refused(f"insufficient duration: {cycles:.1f} cycles, need {min_cycles}")
    e = am.dominant_frequency(y, 0.5 * z.value, min(1.5 * z.value, 0.49 * sr), sr)
    if not e.ok:
        raise Refused(f"no spectral line to confirm {z.value:.2f} Hz: {e.reason}")
    if abs(z.value - e.value) / z.value > agree_rel:
        raise Refused(f"ambiguous: zero crossings say {z.value:.3f} Hz, "
                      f"the spectral peak {e.value:.3f} Hz")
    return float(z.value)


def _cents(a: float, b: float) -> float:
    return 1200.0 * math.log2(a / b)


QUALIFY_FREQS = (30.0, 47.0, 100.0, 333.3, 1000.0, 2718.0, 6400.0, 9000.0, 12000.0, 15731.7, 16000.0)
QUALIFY_SECONDS = 0.48              # what OurLadder.ring(seconds=0.8) leaves to analyse


def _synthetic(f: float, seconds: float, shape: str, phase: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    s = np.sin(2 * math.pi * f * t + phase)
    if shape == "sine":
        return 0.1 * s
    if shape == "saturated":          # odd harmonics, the shape a tanh limit cycle has
        return 0.1 * np.tanh(3.0 * s) / np.tanh(3.0)
    raise ValueError(shape)


def qualify_estimator() -> dict:
    """The estimator against signals whose frequency is known by construction.
    Every accept case must land within plan.json's `accuracy_cents_max`; every
    refuse case must raise `Refused`. Returns the table; raises on a failure."""
    plan = load_plan(require_committed=False)
    tol = float(plan["estimator"]["accuracy_cents_max"])
    rows = []
    for shape in ("sine", "saturated"):
        for f in QUALIFY_FREQS:
            got = ring_frequency(_synthetic(f, QUALIFY_SECONDS, shape))
            err = _cents(got, f)
            rows.append(dict(shape=shape, f=f, got=got, err_cents=err, ok=abs(err) <= tol))
    dom = _synthetic(1000.0, QUALIFY_SECONDS, "sine") + 1.2 * _synthetic(1370.0, QUALIFY_SECONDS, "sine")
    got = ring_frequency(dom)
    rows.append(dict(shape="dominant-of-two", f=1370.0, got=got, err_cents=_cents(got, 1370.0),
                     ok=abs(_cents(got, 1370.0)) <= tol))
    t = np.arange(int(QUALIFY_SECONDS * SR)) / SR
    refuse = {
        "silence": np.zeros(len(t)),
        "nan": np.where(t > 0.2, np.nan, _synthetic(1000.0, QUALIFY_SECONDS, "sine")),
        "inf": np.where(t > 0.2, np.inf, _synthetic(1000.0, QUALIFY_SECONDS, "sine")),
        "too-short-15Hz": _synthetic(15.0, QUALIFY_SECONDS, "sine"),
        "too-short-20Hz": _synthetic(20.0, QUALIFY_SECONDS, "sine"),
        "decaying": _synthetic(500.0, QUALIFY_SECONDS, "sine") * np.exp(-t / 0.08),
        "dead-band": 6e-4 * np.sign(np.sin(2 * math.pi * 1131.0 * t)),
        # Two lines whose crossings no longer follow either one: the 2500 Hz
        # line's SLOPE exceeds the 1000 Hz line's, so it adds crossings while
        # the 1000 Hz line still owns the spectral peak. (The first fixture
        # here, 1000 + 1.2 x 1370 Hz, was not ambiguous at all: the louder line
        # owns both the crossings and the peak, and the estimator correctly
        # returned 1370 Hz. Kept below as an ACCEPT case with that answer.)
        "two-tone": _synthetic(1000.0, QUALIFY_SECONDS, "sine")
                    + 0.8 * _synthetic(2500.0, QUALIFY_SECONDS, "sine"),
    }
    refusals = {}
    for name, y in refuse.items():
        try:
            refusals[name] = dict(refused=False, got=ring_frequency(y))
        except Refused as exc:
            refusals[name] = dict(refused=True, reason=str(exc))
    out = dict(rows=rows, refusals=refusals, accuracy_cents_max=tol,
               worst_cents=max(abs(r["err_cents"]) for r in rows))
    out["ok"] = all(r["ok"] for r in rows) and all(v["refused"] for v in refusals.values())
    return out


# =============================================================================
# the candidate's integer path
# =============================================================================
def corr_bits(entries: int) -> int:
    """log2(entries - 1); the table must be 2^b + 1 entries (b >= 0)."""
    b = int(round(math.log2(entries - 1))) if entries >= 2 else -1
    if entries < 2 or (1 << b) + 1 != entries:
        raise ValueError(f"a correction table has 2^b + 1 entries, not {entries}")
    if b > CORR_SPAN_LOG2:
        raise ValueError(f"{entries} entries is finer than the k register")
    return b


def corr_from_k(k_q14, rom) -> np.ndarray:
    """The Q1.15 correction for the host k register: (k - 65536) clamped to
    [0, 65535], its top `b` bits index the table, the rest interpolate -- the
    same floor-shifted linear read as `voice_fx.g_from_cut`. k at or below the
    onset reads entry 0."""
    rom = np.asarray(rom, dtype=np.int64)
    b = corr_bits(len(rom))
    fb = CORR_SPAN_LOG2 - b
    d = np.clip(np.asarray(k_q14, dtype=np.int64) - CORR_K0, 0, (1 << CORR_SPAN_LOG2) - 1)
    i = d >> fb
    frac = d & ((1 << fb) - 1)
    return rom[i] + (((rom[i + 1] - rom[i]) * frac) >> fb)


def corrected_cut(cut_hz, k_q14, rom) -> np.ndarray:
    """The cutoff both ROM reads see: (cut * c + 2^14) >> 15 -- rounded, not
    floored, because a floor is a systematic flat bias of up to one Hz, 58
    cents at 30 Hz -- then clamped to [CUT_MIN, CUT_MAX]. `rom` None is the
    identity (no correction stage)."""
    cut = np.asarray(cut_hz, dtype=np.int64)
    if rom is None:
        return cut
    c = corr_from_k(k_q14, rom)
    return np.clip((cut * c + (1 << (CORR_Q - 1))) >> CORR_Q, vf.CUT_MIN, vf.CUT_MAX)


@lru_cache(maxsize=None)
def law_poly(degree: int) -> tuple:
    """The refitted tuning polynomial in f/SR, or () for the shipped law."""
    if degree == 0:
        return ()
    p, _ = lh.refit_tuning(lh.REFIT_CUTS, degree)
    return tuple(float(x) for x in p)


def law_fn(degree: int):
    """commanded Hz -> tuned Hz for a law degree; None is the shipped law."""
    p = law_poly(degree)
    if not p:
        return None
    return lambda c: np.asarray(c, dtype=np.float64) * np.polyval(p, np.asarray(c, dtype=np.float64) / SR)


@lru_cache(maxsize=None)
def _g_rom_cached(degree: int, refit: int) -> bytes:
    law = law_fn(degree)
    if refit:
        rom = lh.refit_rom_entries(law=law)
    elif law is None:
        rom = vf.make_g_rom()
    else:
        n = (1 << vf.GROM_BITS) + 1
        step = (1 << 15) >> vf.GROM_BITS
        rom = np.clip(np.round(lh.g_exact_q16(np.arange(n) * step, law)), 0, 65535)
        rom[0] = 0
    return np.asarray(rom, dtype=np.int64).tobytes()


def g_rom_for(degree: int, refit: int) -> np.ndarray:
    return np.frombuffer(_g_rom_cached(degree, refit), dtype=np.int64).copy()


@lru_cache(maxsize=None)
def _k_rom_cached(g_bytes: bytes) -> bytes:
    """DR 0006's construction (`voice_fx.make_k_rom`) against a substituted g
    ROM, cached on the ROM's BYTES. `reference_rigs._k_rom_for` caches on
    `id(g_rom)`, which a garbage-collected candidate ROM can hand to the next
    one -- a stale compensation table for a different filter."""
    g = np.frombuffer(g_bytes, dtype=np.int64)
    step = (1 << 15) >> vf.KROM_BITS
    return np.array([int(round(vf.k_onset(min(max(vf.CUT_MIN, i * step), vf.CUT_MAX),
                                          g, vf.GROM_BITS, 2)[0] / 4.0 * 32768))
                     for i in range((1 << vf.KROM_BITS) + 1)], dtype=np.int64).tobytes()


def k_rom_for(g_rom: np.ndarray) -> np.ndarray:
    return np.frombuffer(_k_rom_cached(np.asarray(g_rom, dtype=np.int64).tobytes()),
                         dtype=np.int64).copy()


class CorrectedLadder(rr.OurLadder):
    """`reference_rigs.OurLadder` with the candidate's cutoff path: the
    correction stage ahead of BOTH ROM reads. With the shipped ROMs and
    `corr_rom=None` it is bit-identical to `OurLadder('ours')` (asserted in the
    suite and by `assert_baseline_path`)."""

    def __init__(self, g_rom=None, k_rom=None, corr_rom=None, name="cand", **kw):
        super().__init__(name, **kw)
        if g_rom is not None:
            self.g_rom = np.asarray(g_rom, dtype=np.int64)
            self.k_rom = k_rom_for(self.g_rom) if k_rom is None else np.asarray(k_rom, dtype=np.int64)
        self.corr_rom = None if corr_rom is None else np.asarray(corr_rom, dtype=np.int64)

    def _regs(self, res, cut, drive):
        k, gain, ogain = rr._REAL_LADDER(**self.cfg).regs(res, drive)
        c = int(corrected_cut(np.array([int(round(cut * self.cut_skew))]), k, self.corr_rom)[0])
        g = int(vf.g_from_cut(np.array([c]), self.g_rom)[0])
        if self.compensated:
            kc = int(vf.kc_from_cut(np.array([c]), self.k_rom)[0])
            k = int(vf.k_effective(k, kc))
        return g, k, gain, ogain


def assert_baseline_path() -> None:
    """The baseline arm is measured through `CorrectedLadder`; refuse unless it
    renders exactly what `OurLadder('ours')` renders."""
    for cut, res in ((100, 1.2), (3200, 2.0)):
        a = rr.OurLadder("ours").ring(cut, res, seconds=0.2)
        b = CorrectedLadder().ring(cut, res, seconds=0.2)
        if not np.array_equal(a, b):
            raise Refused(f"the baseline arm differs from OurLadder('ours') at {cut} Hz, res {res}")


# =============================================================================
# fitting the table
# =============================================================================
def knot_res(entries: int) -> list[float]:
    return [1.0 + i / (entries - 1) for i in range(entries)]


def mean_offset_cents(dev, cuts, res) -> float:
    return float(np.mean([_cents(ring_frequency(dev.ring(c, res, seconds=0.8)), c) for c in cuts]))


def fit_corr_rom(entries: int, g_rom, k_rom, cuts, *, iters: int = 5,
                 tol_cents: float = 0.3) -> np.ndarray:
    """Entry i (i >= 1) is the Q1.15 scale that puts the across-`cuts` mean
    offset at knot resonance 1 + i/(entries-1) to zero, solved on the integer
    path itself (a constant table, so the knot reads exactly that word). Entry
    0, the onset, is unity by construction: the refitted law targets the
    linearised loop, which is exact at the onset. Knots above the k register's
    clamp (res 2.0 -> k 131071) are measured at the clamp."""
    b = corr_bits(entries)
    del b
    rom = [1 << CORR_Q]
    c = 1.0
    for r in knot_res(entries)[1:]:
        for _ in range(iters):
            word = int(round(c * (1 << CORR_Q)))
            dev = CorrectedLadder(g_rom, k_rom, np.full(entries, word))
            m = mean_offset_cents(dev, cuts, min(r, 2.0))
            if abs(m) < tol_cents:
                break
            c *= 2.0 ** (-m / 1200.0)
        word = int(round(c * (1 << CORR_Q)))
        if not 0 < word < (1 << CORR_WORD_BITS):
            raise Refused(f"knot res {r}: scale {c} does not fit Q1.15")
        rom.append(word)
    return np.array(rom, dtype=np.int64)


@dataclass(frozen=True)
class Candidate:
    law_degree: int
    corr_entries: int
    refit_entries: int = 1

    def build(self, fit_cuts) -> CorrectedLadder:
        g = g_rom_for(self.law_degree, self.refit_entries)
        k = k_rom_for(g)
        corr = None if self.corr_entries == 0 else fit_corr_rom(self.corr_entries, g, k, fit_cuts)
        return CorrectedLadder(g, k, corr)


# =============================================================================
# metrics over a grid
# =============================================================================
def offset_table(dev, cuts, resonances) -> dict:
    return {(int(c), float(r)): _cents(ring_frequency(dev.ring(c, r, seconds=0.8)), c)
            for r in resonances for c in cuts}


def grid_metrics(tab: dict, cuts, resonances) -> dict:
    cuts = [int(c) for c in cuts]
    resonances = [float(r) for r in resonances]
    means = {r: float(np.mean([tab[(c, r)] for c in cuts])) for r in resonances}
    per_cut = {c: float(max(tab[(c, r)] for r in resonances) - min(tab[(c, r)] for r in resonances))
               for c in cuts}
    vals = np.array(list(tab.values()))
    return dict(mean_offset_travel_cents=max(means.values()) - min(means.values()),
                mean_offset_by_res=means, per_cut_travel_cents=per_cut,
                worst_per_cut_travel_cents=max(per_cut.values()),
                worst_abs_cents=float(np.abs(vals).max()),
                mean_abs_cents=float(np.abs(vals).mean()))


def onset_table(dev, cuts, below: float, above: float) -> dict:
    return {int(c): dict(below=is_sustained(dev.ring(c, below, seconds=0.8)),
                         above=is_sustained(dev.ring(c, above, seconds=0.8)))
            for c in cuts}


def verdict(plan: dict, base: dict, cand: dict, onset_base: dict, onset_cand: dict) -> dict:
    """Each target and margin from plan.json, PASS/FAIL, with the numbers."""
    t, n = plan["targets"], plan["non_regression"]
    checks = {
        "T1_mean_offset_travel": (cand["mean_offset_travel_cents"]
                                  <= t["T1_mean_offset_travel_cents_max"]),
        "T2_worst_per_cut_travel": (cand["worst_per_cut_travel_cents"]
                                    <= t["T2_worst_per_cut_travel_max_fraction_of_baseline"]
                                    * base["worst_per_cut_travel_cents"]),
        "N1_worst_abs": (cand["worst_abs_cents"]
                         <= base["worst_abs_cents"] + n["N1_worst_abs_cents_max_increase"]),
        "N2_mean_abs": (cand["mean_abs_cents"]
                        <= base["mean_abs_cents"] + n["N2_mean_abs_cents_max_increase"]),
        "N3_onset": all(not v["below"] and v["above"]
                        for o in (onset_base, onset_cand) for v in o.values()),
    }
    return dict(checks=checks, ok=all(checks.values()))


# =============================================================================
# the two pre-registered sweeps, and the validation
# =============================================================================
def run_sweeps(plan: dict) -> dict:
    sel, fit = plan["grids"]["select"], plan["grids"]["fit"]
    sw = plan["sweeps"]
    out = {}
    law = sw["res-cut-law-degree"]
    rows = []
    for d in law["values"]:
        dev = Candidate(d, law["held_fixed"]["CORR_ENTRIES"], law["held_fixed"]["REFIT_ENTRIES"]
                        ).build(fit["cuts_hz"])
        m = grid_metrics(offset_table(dev, sel["cuts_hz"], sel["res"]), sel["cuts_hz"], sel["res"])
        rows.append(dict(LAW_DEGREE=d, CORR_ENTRIES=law["held_fixed"]["CORR_ENTRIES"], **m))
        print(f"  law {d}: travel {m['mean_offset_travel_cents']:.2f} c, worst |off| "
              f"{m['worst_abs_cents']:.2f} c", flush=True)
    out["law"] = rows
    chosen_law = _select(rows, "LAW_DEGREE", "worst_abs_cents", law["flat_within_relative"])
    ent = sw["res-cut-correction-entries"]
    rows = []
    for n in ent["values"]:
        dev = Candidate(chosen_law, n, ent["held_fixed"]["REFIT_ENTRIES"]).build(fit["cuts_hz"])
        m = grid_metrics(offset_table(dev, sel["cuts_hz"], sel["res"]), sel["cuts_hz"], sel["res"])
        rows.append(dict(LAW_DEGREE=chosen_law, CORR_ENTRIES=n,
                         corr_rom=None if dev.corr_rom is None else [int(x) for x in dev.corr_rom],
                         **m))
        print(f"  entries {n}: travel {m['mean_offset_travel_cents']:.2f} c, worst |off| "
              f"{m['worst_abs_cents']:.2f} c", flush=True)
    out["entries"] = rows
    out["chosen"] = dict(LAW_DEGREE=chosen_law,
                         CORR_ENTRIES=_select(rows, "CORR_ENTRIES", "mean_offset_travel_cents",
                                              ent["flat_within_relative"]))
    return out


def _select(rows, dial, objective, tol) -> int:
    """The lowest dial value in the run (tools/sensitivity.py's grouping) that
    holds the best point -- the selection rule both sweeps pre-registered."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import sensitivity
    pts = sorted((float(r[dial]), float(r[objective])) for r in rows)
    groups = sensitivity.runs(pts, tol)
    best = min(pts, key=lambda p: p[1])[0]
    for g in groups:
        if best in g:
            return int(min(g))
    raise AssertionError("best point in no run")


def write_sweep_artifact(res: dict, path: str) -> None:
    lines = ["Issue #257 pre-registered sweeps -- written by `python3 model/res_tuning.py sweep`.",
             "Grids, rules and predictions: docs/res-tuning/plan.json (committed before this ran).",
             "Every number is on the SELECT grid; the validate grid is untouched here.", ""]
    lines.append("law_degree -- worst |offset| against the tuning law, correction held at 9 entries")
    lines.append("LAW_DEGREE CORR_ENTRIES REFIT_ENTRIES worst_abs_cents travel_cents mean_abs_cents")
    for r in res["law"]:
        lines.append(f"{r['LAW_DEGREE']} {r['CORR_ENTRIES']} 1 {r['worst_abs_cents']:.2f} "
                     f"{r['mean_offset_travel_cents']:.2f} {r['mean_abs_cents']:.2f}")
    lines.append("")
    lines.append(f"corr_entries -- mean-offset travel against table size, law held at "
                 f"{res['chosen']['LAW_DEGREE']}")
    lines.append("CORR_ENTRIES LAW_DEGREE REFIT_ENTRIES travel_cents worst_abs_cents mean_abs_cents")
    for r in res["entries"]:
        lines.append(f"{r['CORR_ENTRIES']} {r['LAW_DEGREE']} 1 {r['mean_offset_travel_cents']:.2f} "
                     f"{r['worst_abs_cents']:.2f} {r['mean_abs_cents']:.2f}")
    lines.append("")
    lines.append(f"chosen by the pre-registered selection rules: LAW_DEGREE "
                 f"{res['chosen']['LAW_DEGREE']}, CORR_ENTRIES {res['chosen']['CORR_ENTRIES']}")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def run_validation(plan: dict, law_degree: int = LAW_DEGREE, entries: int = CORR_ENTRIES) -> dict:
    assert_baseline_path()
    v, fit = plan["grids"]["validate"], plan["grids"]["fit"]
    base_dev = CorrectedLadder()
    cand_dev = Candidate(law_degree, entries, REFIT_ENTRIES).build(fit["cuts_hz"])
    tb = offset_table(base_dev, v["cuts_hz"], v["res"])
    tc = offset_table(cand_dev, v["cuts_hz"], v["res"])
    mb, mc = grid_metrics(tb, v["cuts_hz"], v["res"]), grid_metrics(tc, v["cuts_hz"], v["res"])
    ob = onset_table(base_dev, v["cuts_hz"], v["onset_below_res"], v["onset_above_res"])
    oc = onset_table(cand_dev, v["cuts_hz"], v["onset_below_res"], v["onset_above_res"])
    return dict(law_degree=law_degree, corr_entries=entries,
                corr_rom=None if cand_dev.corr_rom is None else [int(x) for x in cand_dev.corr_rom],
                g_rom_sha=__import__("hashlib").sha256(cand_dev.g_rom.tobytes()).hexdigest()[:16],
                k_rom=[int(x) for x in cand_dev.k_rom],
                baseline=dict(points={f"{c}@{r}": x for (c, r), x in tb.items()}, metrics=mb,
                              onset=ob),
                candidate=dict(points={f"{c}@{r}": x for (c, r), x in tc.items()}, metrics=mc,
                               onset=oc),
                verdict=verdict(plan, mb, mc, ob, oc))


def write_validation_artifact(rep: dict, plan: dict, path: str) -> None:
    v = plan["grids"]["validate"]
    lines = ["Issue #257 validation on the UNTOUCHED grid -- `python3 model/res_tuning.py validate`.",
             f"candidate: LAW_DEGREE {rep['law_degree']}, CORR_ENTRIES {rep['corr_entries']}, "
             f"REFIT_ENTRIES 1, correction table {rep['corr_rom']}", "",
             "offset_cents -- every point, cents of f_osc against commanded (baseline | candidate)"]
    lines.append("cut_hz " + " ".join(f"b{r:g} c{r:g}" for r in v["res"]))
    for c in v["cuts_hz"]:
        row = [f"{c}"]
        for r in v["res"]:
            row += [f"{rep['baseline']['points'][f'{c}@{float(r)}']:+.2f}",
                    f"{rep['candidate']['points'][f'{c}@{float(r)}']:+.2f}"]
        lines.append(" ".join(row))
    lines.append("")
    for arm in ("baseline", "candidate"):
        m = rep[arm]["metrics"]
        lines.append(f"{arm}: mean-offset travel {m['mean_offset_travel_cents']:.2f} c, worst "
                     f"per-cut travel {m['worst_per_cut_travel_cents']:.2f} c, worst |offset| "
                     f"{m['worst_abs_cents']:.2f} c, mean |offset| {m['mean_abs_cents']:.2f} c")
        lines.append("  per-cut travel: " + ", ".join(
            f"{c} Hz {x:.1f}" for c, x in m["per_cut_travel_cents"].items()))
        lines.append("  mean offset by res: " + ", ".join(
            f"{r:g} {x:+.1f}" for r, x in m["mean_offset_by_res"].items()))
        lines.append("  onset (0.97 sustains / 1.03 sustains): " + ", ".join(
            f"{c} {int(o['below'])}/{int(o['above'])}" for c, o in rep[arm]["onset"].items()))
    lines.append("")
    for k, ok in rep["verdict"]["checks"].items():
        lines.append(f"{'PASS' if ok else 'FAIL'} {k}")
    lines.append(f"OVERALL {'PASS' if rep['verdict']['ok'] else 'FAIL'}")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# =============================================================================
# injected controls: each must turn the acceptance verdict red
# =============================================================================
CONTROLS = {
    "disabled": "the correction stage removed; refitted ROMs kept",
    "reversed": "the table inverted about unity (65536 - word): corrects the wrong way",
    "keyed-on-k-eff": "the table indexed by the COMPENSATED k (k * kc) instead of the host "
                      "register -- the circular representation the curator warned of",
    "index-off-by-one": "the table read one entry high",
    "floor-not-round": "(cut * c) >> 15 floored instead of rounded",
    "k-rom-not-rederived": "the refitted g ROM with the SHIPPED k ROM: DR 0006's compensation "
                           "not re-derived against the new coefficients",
}
PROPERTIES = ("T1_mean_offset_travel", "T2_worst_per_cut_travel", "N1_worst_abs",
              "N2_mean_abs", "N3_onset")


class MutantLadder(CorrectedLadder):
    """`CorrectedLadder` with exactly one injected defect from `CONTROLS`."""

    def __init__(self, mutation: str, g_rom, k_rom, corr_rom):
        if mutation not in CONTROLS:
            raise ValueError(mutation)
        self.mutation = mutation
        if mutation == "disabled":
            corr_rom = None
        elif mutation == "reversed":
            corr_rom = (2 << CORR_Q) - np.asarray(corr_rom, dtype=np.int64)
        elif mutation == "k-rom-not-rederived":
            k_rom = vf.make_k_rom()
        super().__init__(g_rom, k_rom, corr_rom)

    def _regs(self, res, cut, drive):
        m = self.mutation
        if m not in ("keyed-on-k-eff", "index-off-by-one", "floor-not-round"):
            return super()._regs(res, cut, drive)
        k, gain, ogain = rr._REAL_LADDER(**self.cfg).regs(res, drive)
        cut = int(round(cut))
        key = k
        if m == "keyed-on-k-eff":
            key = int(vf.k_effective(k, vf.kc_from_cut(np.array([cut]), self.k_rom)[0]))
        if m == "index-off-by-one":
            rom = np.append(self.corr_rom[1:], self.corr_rom[-1])
            cr = int(corr_from_k(np.array([key]), rom)[0])
        else:
            cr = int(corr_from_k(np.array([key]), self.corr_rom)[0])
        half = 0 if m == "floor-not-round" else (1 << (CORR_Q - 1))
        c = int(np.clip((cut * cr + half) >> CORR_Q, vf.CUT_MIN, vf.CUT_MAX))
        g = int(vf.g_from_cut(np.array([c]), self.g_rom)[0])
        kc = int(vf.kc_from_cut(np.array([c]), self.k_rom)[0])
        return g, int(vf.k_effective(k, kc)), gain, ogain


def committed_candidate() -> tuple:
    """(g_rom, k_rom, corr_rom) for the validated candidate, the table read
    from docs/res-tuning/validation.json and REFUSED unless it is the 33-entry
    table the sweep also produced -- one fitted table, not two."""
    with open(os.path.join(OUT_DIR, "validation.json")) as fh:
        val = json.load(fh)
    with open(os.path.join(OUT_DIR, "sweeps.json")) as fh:
        sw = json.load(fh)
    swept = [r["corr_rom"] for r in sw["entries"] if r["CORR_ENTRIES"] == val["corr_entries"]]
    if (val["law_degree"], val["corr_entries"]) != (LAW_DEGREE, CORR_ENTRIES) or \
            not swept or swept[0] != val["corr_rom"]:
        raise Refused("validation.json, sweeps.json and the module's dials do not describe "
                      "one candidate")
    g = g_rom_for(LAW_DEGREE, REFIT_ENTRIES)
    k = k_rom_for(g)
    if val["k_rom"] != [int(x) for x in k]:
        raise Refused("the re-derived k ROM differs from the one validated")
    return g, k, np.array(val["corr_rom"], dtype=np.int64)


CORR_HEX = os.path.join(OUT_DIR, "corr_rom33.hex")


def corr_hex_text(rom) -> str:
    """The table as the RTL's $readmemh image: one 4-digit hex word per line.
    REFUSES a word that does not fit the 16-bit Q1.15 unsigned field."""
    rom = [int(x) for x in rom]
    if len(rom) != 33 or any(not 0 <= x < (1 << CORR_WORD_BITS) for x in rom) or rom[0] != 1 << CORR_Q:
        raise Refused("the correction table must be 33 words of 16 bits with entry 0 = unity")
    return "".join(f"{x:04x}\n" for x in rom)


def load_corr_hex(path: str = CORR_HEX) -> np.ndarray:
    with open(path) as fh:
        return np.array([int(l, 16) for l in fh.read().split()], dtype=np.int64)


def run_controls(plan: dict, names=None) -> dict:
    """Every control on the validate grid, against the baseline, as a
    properties x controls matrix: MOVED where the control turns that check red,
    BLIND where the check still passes. The clean candidate is re-run first and
    must pass every check, or nothing here is a verdict."""
    v = plan["grids"]["validate"]
    g, k, corr = committed_candidate()

    def evaluate(dev):
        t = offset_table(dev, v["cuts_hz"], v["res"])
        o = onset_table(dev, v["cuts_hz"], v["onset_below_res"], v["onset_above_res"])
        return grid_metrics(t, v["cuts_hz"], v["res"]), o

    mb, ob = evaluate(CorrectedLadder())
    mc, oc = evaluate(CorrectedLadder(g, k, corr))
    clean = verdict(plan, mb, mc, ob, oc)
    if not clean["ok"]:
        raise Refused(f"the clean candidate does not pass ({clean['checks']}); no control "
                      "can be read")
    out = dict(clean=dict(checks=clean["checks"], metrics=mc), controls={})
    for name in (names or CONTROLS):
        mm, om = evaluate(MutantLadder(name, g, k, corr))
        vv = verdict(plan, mb, mm, ob, om)
        out["controls"][name] = dict(
            caught=not vv["ok"],
            matrix={p: ("BLIND" if vv["checks"][p] else "MOVED") for p in PROPERTIES},
            metrics={key: mm[key] for key in ("mean_offset_travel_cents",
                                              "worst_per_cut_travel_cents",
                                              "worst_abs_cents", "mean_abs_cents")},
            onset_failures=[c for c, x in om.items() if x["below"] or not x["above"]])
    return out


def print_controls(rep: dict) -> None:
    print(f"{'control':22s} " + " ".join(f"{p.split('_')[0]:>6s}" for p in PROPERTIES)
          + "   travel  per-cut  worst  mean")
    for name, c in rep["controls"].items():
        m = c["metrics"]
        print(f"{name:22s} " + " ".join(f"{c['matrix'][p]:>6s}" for p in PROPERTIES)
              + f"  {m['mean_offset_travel_cents']:7.1f} {m['worst_per_cut_travel_cents']:7.1f}"
                f" {m['worst_abs_cents']:6.1f} {m['mean_abs_cents']:5.1f}"
              + ("" if c["caught"] else "   NOT CAUGHT"))
    caught = sum(c["caught"] for c in rep["controls"].values())
    print(f"{caught} of {len(rep['controls'])} controls caught by the acceptance verdict")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("qualify", "sweep", "validate", "controls", "hex"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "qualify":
            q = qualify_estimator()
            for r in q["rows"]:
                print(f"  {r['shape']:9s} {r['f']:8.1f} Hz -> {r['got']:10.3f}  "
                      f"{r['err_cents']:+.4f} c {'ok' if r['ok'] else 'FAIL'}")
            for name, v in q["refusals"].items():
                print(f"  {name:15s} {'REFUSED: ' + v['reason'] if v['refused'] else 'ACCEPTED ' + str(v['got'])}")
            print(f"estimator {'QUALIFIED' if q['ok'] else 'NOT QUALIFIED'}; worst "
                  f"{q['worst_cents']:.4f} c against {q['accuracy_cents_max']} c")
            return 0 if q["ok"] else 1
        if a.cmd == "hex":
            with open(os.path.join(OUT_DIR, "validation.json")) as fh:
                text = corr_hex_text(json.load(fh)["corr_rom"])
            with open(CORR_HEX, "w") as fh:
                fh.write(text)
            print(f"wrote {CORR_HEX}")
            return 0
        plan = load_plan()
        if a.cmd == "controls":
            rep = run_controls(plan)
            with open(os.path.join(OUT_DIR, "controls.json"), "w") as fh:
                json.dump(rep, fh, indent=1)
            print_controls(rep)
            return 0
        if a.cmd == "sweep":
            res = run_sweeps(plan)
            write_sweep_artifact(res, os.path.join(OUT_DIR, "sweeps.txt"))
            with open(os.path.join(OUT_DIR, "sweeps.json"), "w") as fh:
                json.dump(res, fh, indent=1, default=str)
            print(f"chosen: {res['chosen']}")
            return 0
        rep = run_validation(plan)
        write_validation_artifact(rep, plan, os.path.join(OUT_DIR, "validation.txt"))
        with open(os.path.join(OUT_DIR, "validation.json"), "w") as fh:
            json.dump(rep, fh, indent=1)
        print(open(os.path.join(OUT_DIR, "validation.txt")).read())
        return 0 if rep["verdict"]["ok"] else 1
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

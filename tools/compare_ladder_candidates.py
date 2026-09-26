#!/usr/bin/env python3
"""Rungs 2-4 of issue #46: four candidate ladders, six dimensions, one verdict.

    python3 tools/compare_ladder_candidates.py --quick            # ~4 min
    python3 tools/compare_ladder_candidates.py                    # ~20 min
    python3 tools/compare_ladder_candidates.py --json build/ladder-candidates.json
    python3 -m pytest model/test_ladder_candidates.py -q          # its ground truth

The filters are in `model/ladder_candidates.py`; this file only measures them.
Issue #46 names six questions every candidate must face, and they are the
six stages below, run on IDENTICAL conditions -- the same stimuli, the same
input levels, the same 129-entry Q0.16 coefficient table, the same 24/20 state,
the same 16-entry `tanh`, the same 2x oversampling:

    bass          does it keep weight as resonance rises
    drive         does pushing several oscillators in compress and thicken
    movement      is a fast cutoff sweep smooth
    resonance     does it ring and self-oscillate PREDICTABLY
    cleanliness   aliasing, numerical buzz, overflow
    cost          does the complete instrument still meet its sample deadline

TWO SCORES, KEPT APART, BECAUSE THE ISSUE SAYS SO
-------------------------------------------------
"sounds bigger" and "matches the reference better" are different questions and
a change may legitimately win one and lose the other. `matches` is distance
from the ladder's own closed-form response (`ladder_candidates
.analytic_response_db`) and from the rung-4 converged model; `bigger` is bass
retention, drive compression and self-oscillation level. They are never added
together, and the report prints them in separate columns.

WHAT MAKES THIS A MEASUREMENT AND NOT A SELF-PORTRAIT
-----------------------------------------------------
Three things, in the order they can go wrong:

  * **the yardstick is not one of us.** Tuning and resonance are scored
    against the ladder's transfer function, which is textbook and checkable by
    hand. The rung-4 model earns its place as a large-signal reference by being
    measured against that same closed form first (`assert_apparatus`).
  * **it starts red.** Every stage runs against `null`, a core with the right
    ports and no behaviour, before any candidate is believed. A stage that
    returns a plausible number for silence is not a measurement.
  * **it carries injected defects.** A dropped pole, a 6 % cutoff skew, a
    4-entry `tanh` and a 32 Hz control quantum, each of which must move its own
    dimension by more than the whole spread between candidates -- otherwise the
    dimension cannot see a real difference either.

WHAT IT DOES NOT SETTLE
-----------------------
  * The `tanh` table is held at 16 entries throughout. Issue #46 puts it out of
    scope and DR 0006's `k_comp` ROM is derived from its bin-0 slope.
  * The movement dimension uses the SWEEP-RATE methodology of
    `model/reference_movement.py` (#53). #53's continuous
    resonance-through-threshold sweep and its audio-rate cutoff modulation do
    not exist yet; they are named gaps here, not silently-assumed coverage.
  * No plugin is rendered. The frozen external profile
    (`docs/reference-compare-results.json`) describes the shipped filter only,
    so it cannot score a candidate; re-deriving `docs/discrimination.md`
    section 8's `ours` rows is separate work.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

import audio_measure as am                                          # noqa: E402
import ladder_candidates as lc                                      # noqa: E402
import voice_fx as vf                                               # noqa: E402
from ladder_candidates import Candidate, Refused                    # noqa: E402
from dsp import SR                                                  # noqa: E402

# ---------------------------------------------------------------------------
# the grids. `docs/analysis-conventions.md`: the same cutoffs every other
# filter measurement in this repository uses, so rows can be put side by side.
# ---------------------------------------------------------------------------
LINEAR_CUTS = (100.0, 400.0, 1600.0, 6400.0)
LINEAR_CUTS_QUICK = (400.0, 6400.0)
LINEAR_RES = (0.5, 0.9)
RING_CUTS = (100.0, 400.0, 1600.0, 3200.0, 6400.0)
RING_CUTS_QUICK = (400.0, 3200.0)
RING_RES = (1.05, 1.45, 2.00)
RING_RES_QUICK = (1.05, 2.00)
BASS_FREQS = (55.0, 80.0, 110.0, 220.0)
BASS_CUT = 1000.0
BASS_RES = (0.1, 1.8)
DRIVE_F0, DRIVE_CUT, DRIVE_RES = 220.0, 12000.0, 0.1
DRIVE_H3_DB = -40.0
DRIVE_LEVELS = (-24.0, -18.0, -12.0, -9.0, -8.0, -7.0, -6.0, -5.0, -3.0, 0.0)
CHORD_F0, CHORD_CUT, CHORD_RES, CHORD_AMP = 110.0, 2000.0, 1.2, 0.20
MOVE_SWEEPS = ((60.0, 960.0, 0.1), (500.0, 8000.0, 0.4))
# CLEAN_F0 IS 2093 AND NOT 2000, AND THE REASON IS THE ESTIMATOR'S PRECONDITION.
# `audio_measure.foldback_alias_db` finds aliases by looking at the PREDICTED
# image frequency of each harmonic above Nyquist. 48000 / 2000 = 24 exactly, so
# every image of every harmonic of a 2 kHz tone folds back onto a multiple of
# 2 kHz -- i.e. onto a real harmonic -- and none of them can be attributed.
# Measured: at 2000 Hz the estimator reports 0 usable images and 121 collisions
# and correctly REFUSES; the report then printed `alias_db=None` for every
# candidate, so the aliasing half of the cleanliness dimension was unmeasured
# and looked like a measurement. 2093 Hz (C7, not a submultiple of the sample
# rate) gives 126 usable images and 0 collisions.
CLEAN_F0, CLEAN_CUT, CLEAN_RES, CLEAN_AMP = 2093.0, 9000.0, 0.8, 0.5

# The clock budget of DR 0001: 12.288 MHz over 48 kHz. `DIV_CLOCKS` is a
# restoring divider's latency for a 17-bit quotient; `TANH_CLOCKS` is one ROM
# read plus the interpolating multiply. Both are swept in the report rather
# than argued about, because the whole cost difference between candidates is
# divides.
CLOCKS_PER_SAMPLE = 256
DIV_CLOCKS = (1, 8, 17)
TANH_CLOCKS = 2
MULT_CLOCKS = 1
# The coefficient update, per output sample, under MOVING controls: the g ROM's
# interpolating read, the k ROM's, and `k_effective`. Every candidate here pays
# exactly this and no more -- none of them needs a transcendental per sample,
# because `tanh(w/2)` is baked into the table at build time exactly as
# `1 - exp(-w)` is. That is a result, not an omission; see the report.
COEF_UPDATE_MULTS = 3


def _cand(name, **kw):
    return Candidate(name, **kw)


def _render_fn(cand, cut, res):
    return lambda x: cand.render(x * lc.FS_Q15, cut, res)


# ===========================================================================
# 1. tuning and resonance against the closed form  (dimensions: resonance)
# ===========================================================================
def stage_linear(names, cuts, resonances, **kw) -> dict:
    """Where the resonant peak is and how tall, against the ladder's own
    transfer function. Three errors per operating point, all in units a player
    would recognise: cents of tuning, dB of peak height, dB of passband gain a
    decade below the cutoff."""
    out = {}
    for name in names:
        rows = {}
        for cut in cuts:
            for res in resonances:
                cand = _cand(name, **kw)
                try:
                    r = lc.peak_probe(_render_fn(cand, cut, res), cut, res,
                                      label=f"{name} {cut:.0f}/{res}")
                except (Refused, am.InsufficientEvidence) as exc:
                    rows[f"{cut:.0f}/{res}"] = dict(refused=str(exc))
                    continue
                rows[f"{cut:.0f}/{res}"] = dict(
                    cents=round(r["cents"], 2), peak_db=round(r["peak_db"], 3),
                    pass_db=round(r["pass_db"], 3))
        ok = [v for v in rows.values() if "refused" not in v]
        out[name] = dict(
            points=rows,
            worst_cents=round(max((abs(v["cents"]) for v in ok), default=float("nan")), 2),
            worst_peak_db=round(max((abs(v["peak_db"]) for v in ok), default=float("nan")), 3),
            worst_pass_db=round(max((abs(v["pass_db"]) for v in ok), default=float("nan")), 3),
            refused=sum(1 for v in rows.values() if "refused" in v))
    return out


# ===========================================================================
# 2. self-oscillation across the full cutoff range  (dimension: resonance)
# ===========================================================================
def _ring_facts(y, cut) -> dict:
    """Does it sing, where, and how cleanly. `sustain_db` is the tail against
    the head of the ring: a limit cycle holds level, a decaying resonance does
    not, and the difference is the whole of "self-oscillates predictably"."""
    n = len(y)
    head = am.rms(y[:n // 4])
    tail = am.rms(y[-n // 4:])
    if tail < 1e-7:
        return dict(sings=False, sustain_db=-120.0, tail_rms=float(tail))
    cents = lc._cents_of_ring(y[n // 2:], cut)
    sig = am.harmonic_signature(y[n // 2:], f_lo=20.0)
    return dict(sings=bool(tail > 3e-4 and am.db(tail, head) > -12.0),
                sustain_db=round(am.db(tail, head), 2),
                tail_rms=float(tail), cents=round(cents, 2),
                h3=sig.get("h3"), h5=sig.get("h5"))


def stage_resonance(names, cuts, resonances, **kw) -> dict:
    """DR 0001's reversal condition, word for word: *measured stable at
    resonance >= 1.0 across the full cutoff range*. Uncompensated, because
    DR 0006's compensation ROM is derived from the SHIPPED loop and applying it
    to a candidate would hide the very property being judged -- how much
    compensation that candidate's own structure needs."""
    out = {}
    for name in names:
        rows, sings, cents = {}, [], []
        for cut in cuts:
            for res in resonances:
                cand = _cand(name, **kw)
                try:
                    f = _ring_facts(cand.ring(cut, res), cut)
                except (Refused, am.InsufficientEvidence) as exc:
                    rows[f"{cut:.0f}/{res}"] = dict(refused=str(exc))
                    sings.append(False)
                    continue
                rows[f"{cut:.0f}/{res}"] = f
                sings.append(f["sings"])
                if f["sings"]:
                    cents.append(f["cents"])
        out[name] = dict(
            points=rows,
            sings_everywhere=bool(sings and all(sings)),
            sings_fraction=round(sum(sings) / max(len(sings), 1), 3),
            tuning_spread_cents=round(max(cents) - min(cents), 1) if len(cents) > 1 else None,
            worst_cents=round(max((abs(c) for c in cents), default=float("nan")), 1))
    return out


# ===========================================================================
# 3. bass  (dimension: bass)
# ===========================================================================
def stage_bass(names, **kw) -> dict:
    """Does the low end survive the resonance knob. The ladder loses passband
    gain as `k` rises (`H(0) = 1/(1 + k)`) and DR 0005's `ogain` puts
    `(1 + 2 res)` back; what is left is the algorithm's own, so the score is
    the MEASURED change against the change the transfer function predicts."""
    freqs = lc.snap_freqs(BASS_FREQS)
    ana = {r: lc.analytic_response_db(BASS_CUT, r, freqs) for r in BASS_RES}
    out = {}
    for name in names:
        meas = {}
        for res in BASS_RES:
            cand = _cand(name, **kw)
            try:
                meas[res] = lc.stepped_tone_db(_render_fn(cand, BASS_CUT, res),
                                               freqs, lc.PROBE_AMP, label=f"{name} bass")
            except (Refused, am.InsufficientEvidence) as exc:
                out[name] = dict(refused=str(exc))
                break
        else:
            lo, hi = BASS_RES[0], BASS_RES[-1]
            keep = meas[hi] - meas[lo]
            want = ana[hi] - ana[lo]
            out[name] = dict(
                freqs=[round(float(f), 1) for f in freqs],
                measured_db=[round(float(v), 2) for v in keep],
                analytic_db=[round(float(v), 2) for v in want],
                weight_db=round(float(keep[1]), 2),            # 80 Hz
                error_db=round(float(np.abs(keep - want).max()), 2))
    return out


# ===========================================================================
# 4. drive  (dimension: drive)
# ===========================================================================
def stage_drive(names, **kw) -> dict:
    """Where the designed saturation engages, how hard it compresses once it
    does, and whether three simultaneous notes survive it -- issue #46's
    "test it with several simultaneous tones now"."""
    out = {}
    for name in names:
        row, prev = {}, None
        sat_dbfs = None
        for db in DRIVE_LEVELS:
            cand = _cand(name, **kw)
            y = cand.drive_tone(DRIVE_F0, DRIVE_CUT, DRIVE_RES, 10.0 ** (db / 20.0))
            if am.is_silent(y):
                continue
            h3 = am.harmonic_signature(y, f_lo=100.0, f0=DRIVE_F0).get("h3")
            if h3 is None:
                continue
            if h3 >= DRIVE_H3_DB and sat_dbfs is None:
                sat_dbfs = float(db) if prev is None else float(
                    prev[0] + (DRIVE_H3_DB - prev[1]) * (db - prev[0]) / (h3 - prev[1]))
            prev = (db, h3)
        # compression: dB of output per dB of input, from -20 dBFS to 0 dBFS
        lvl = {}
        for db in (-20.0, 0.0):
            cand = _cand(name, **kw)
            lvl[db] = am.rms(cand.drive_tone(DRIVE_F0, DRIVE_CUT, DRIVE_RES,
                                             10.0 ** (db / 20.0)))
        chord = _cand(name, **kw)
        yc = chord.chord_tone(CHORD_F0, CHORD_CUT, CHORD_RES, CHORD_AMP)
        inh = am.inharmonic_fraction_db(yc, CHORD_F0)
        row.update(
            saturation_dbfs=None if sat_dbfs is None else round(sat_dbfs, 2),
            compression_db_per_db=None if min(lvl.values()) <= 0 else
            round(am.db(lvl[0.0], lvl[-20.0]) / 20.0, 3),
            chord_clamp_fraction=round(chord.last_clamp, 5),
            chord_inharmonic_db=round(inh.value, 1) if inh.ok else None,
            chord_rms=round(float(am.rms(yc)), 5))
        out[name] = row
    return out


# ===========================================================================
# 5. movement  (dimension: movement)
# ===========================================================================
def stage_movement(names, quantum: int = 1, **kw) -> dict:
    """#53's sweep-rate methodology, applied to each candidate: the same
    exponential cutoff sweep rendered twice through the same filter, once with
    the shipping integer control path and once with the coefficient in float.
    The difference IS the control path's contribution -- there is nothing else
    it can be.

    The coefficient-update cost under moving controls is reported here too,
    and it is the same for every candidate: one interpolated read of a
    129-entry Q0.16 table, one of the 33-entry compensation table, and one
    multiply. No candidate needs a transcendental per sample, because each
    one's law is baked into its table at build time."""
    out = {}
    for name in names:
        rows = []
        for lo, hi, secs in MOVE_SWEEPS:
            n = int(secs * SR)
            t = np.arange(n) / SR
            cut_f = lo * (hi / lo) ** (t / secs)
            carrier = 220.0 if lo < 100 else 2000.0
            x = 0.25 * lc.FS_Q15 * np.sin(2 * math.pi * carrier * t)
            cand = _cand(name, **kw)
            try:
                a = cand.render_modulated(x, cut_f, 0.3, quantum=quantum)
                moving_cost = cand.last_cost
                b = _cand(name, **kw).render_modulated(x, cut_f, 0.3, smooth=True)
            except Refused as exc:
                rows.append(dict(lo=lo, hi=hi, seconds=secs, refused=str(exc)))
                continue
            if am.is_silent(a) or am.is_silent(b):
                rows.append(dict(lo=lo, hi=hi, seconds=secs, refused="silent"))
                continue
            held = _cand(name, **kw)
            held.render(x[:n // 4], math.sqrt(lo * hi), 0.3)
            rip = am.envelope_ripple_db(am.analytic_envelope(a), SR,
                                        lp_hz=220.0 if lo < 100 else 800.0)
            rows.append(dict(
                lo=lo, hi=hi, seconds=secs,
                oct_per_s=round(math.log2(hi / lo) / secs, 2),
                residual_db=round(am.db(am.rms(a - b), am.rms(b)), 2),
                ripple_db=round(rip.value, 2) if rip.ok else None,
                moving_tanh=round(moving_cost["tanh"], 2),
                held_tanh=round(held.last_cost["tanh"], 2)))
        good = [r for r in rows if "residual_db" in r]
        out[name] = dict(
            sweeps=rows,
            worst_residual_db=round(max((r["residual_db"] for r in good),
                                        default=float("nan")), 2),
            inner_loop_cost_moves=any(
                abs(r["moving_tanh"] - r["held_tanh"]) > 1e-9 for r in good),
            coefficient_update_mults_per_sample=COEF_UPDATE_MULTS,
            refused=len(rows) - len(good))
    return out


# ===========================================================================
# 6. cleanliness  (dimension: cleanliness)
# ===========================================================================
def stage_cleanliness(names, **kw) -> dict:
    """Three ways a filter can be dirty, kept apart because they have different
    causes: inharmonic energy from the oversampled nonlinearity folding back, a
    numerical limit cycle that never decays into silence, and state overflow."""
    out = {}
    for name in names:
        cand = _cand(name, **kw)
        y = cand.drive_tone(CLEAN_F0, CLEAN_CUT, CLEAN_RES, CLEAN_AMP, seconds=0.5)
        alias = am.foldback_alias_db(y, CLEAN_F0)
        # a resonance well below onset must decay into EXACT silence
        quiet = _cand(name, **kw)
        q = quiet.ring(800.0, 0.5, seconds=1.4)
        tail = q[int(0.8 * len(q)):]
        out[name] = dict(
            alias_db=round(alias.value, 1) if alias.ok else None,
            # a bare `None` is indistinguishable from "we did not look", which
            # is how this axis sat unmeasured; say WHY instead
            alias_refused=None if alias.ok else str(alias.reason),
            alias_images=(alias.detail or {}).get("images"),
            clamp_fraction=round(cand.last_clamp, 5),
            decay_tail_rms=float(am.rms(tail)),
            decays_to_silence=bool(am.is_silent(tail, floor=1e-9)),
            drive_clamp_fraction=round(cand.last_clamp, 5))
    return out


# ===========================================================================
# 7. hardware cost  (dimension: hardware cost)
# ===========================================================================
def stage_cost(names, **kw) -> dict:
    """Ops per output sample, instrumented, and the clock estimate that follows
    from them -- against DR 0001's own budget of 256 clocks at 12.288 MHz over
    48 kHz. The divider latency is swept (1, 8, 17) rather than assumed,
    because it is the entire cost difference between these candidates.

    **And then the sweep is turned into the exact threshold**, because a
    three-point grid is a place to read the wrong answer off. Cost is affine in
    the divider latency -- `clocks = fixed + divides * d` -- so the largest `d`
    that fits is arithmetic, and `max_divider_latency_that_fits` states it.
    Reading it off the grid instead cost this record an off-by-one: DR 0017
    first wrote the 2-iteration solve's reversal condition as "a reciprocal
    unit of <= 8 clocks" because 8 was the grid point below 17, when the solve
    needs 129 + 16 d <= 256, i.e. **d <= 7** -- at exactly 8 it is 257 clocks
    against a 256 budget and misses by one. `None` means it does not fit at any
    latency, not even a combinational divide."""
    x = np.zeros(400)
    x[0] = 20000.0
    out = {}
    for name in names:
        cand = _cand(name, **kw)
        cand.render(x, 1600.0, 1.2)
        c = cand.last_cost
        per_sample = {k: v * lc.OVERSAMPLE for k, v in c.items()
                      if k in ("tanh", "divide", "multiply", "add")}
        clocks = {}
        for d in DIV_CLOCKS:
            clocks[d] = (per_sample["tanh"] * TANH_CLOCKS
                         + per_sample["multiply"] * MULT_CLOCKS
                         + per_sample["divide"] * d
                         + COEF_UPDATE_MULTS * MULT_CLOCKS)
        # exact, not read off the grid: clocks(d) = fixed + divides * d
        fixed_clocks = (per_sample["tanh"] * TANH_CLOCKS
                        + per_sample["multiply"] * MULT_CLOCKS
                        + COEF_UPDATE_MULTS * MULT_CLOCKS)
        slack = CLOCKS_PER_SAMPLE - fixed_clocks
        if slack < 0:
            d_max = None                       # fits at no divider latency
        elif per_sample["divide"] == 0:
            d_max = float("inf")               # no divider to pay for
        else:
            d_max = int(slack // per_sample["divide"])
        out[name] = dict(
            per_sample={k: round(v, 2) for k, v in per_sample.items()},
            iterations=c["iterations"],
            clocks_by_divider_latency={d: round(v, 1) for d, v in clocks.items()},
            fits_budget={d: bool(v <= CLOCKS_PER_SAMPLE) for d, v in clocks.items()},
            clocks_without_divider=round(fixed_clocks, 1),
            max_divider_latency_that_fits=d_max,
            budget=CLOCKS_PER_SAMPLE,
            note="multiply/add are declared from each core's source; tanh and "
                 "divide are counted by the inner loop")
    return out


# ===========================================================================
# the controls: start red, then inject defects
# ===========================================================================
def stage_controls(cuts, resonances, ring_cuts, ring_res) -> dict:
    """Rules 1 and 2 of `docs/verification-rules.md`, as a stage that runs in
    the same report as the result it guards.

    Start red: `null` has the right ports and no behaviour, and every dimension
    must refuse it or score it worst. Then four injected defects, each of which
    must move ITS OWN dimension by more than the spread between real
    candidates -- a dimension that cannot see a dropped pole cannot see an
    algorithm change either."""
    base = "shipped"
    red = {}
    for nm, fn in (("linear", lambda: stage_linear(["null"], cuts, resonances)),
                   ("resonance", lambda: stage_resonance(["null"], ring_cuts, ring_res)),
                   ("bass", lambda: stage_bass(["null"])),
                   ("drive", lambda: stage_drive(["null"])),
                   ("movement", lambda: stage_movement(["null"])),
                   ("cleanliness", lambda: stage_cleanliness(["null"]))):
        try:
            red[nm] = fn()["null"]
        except (Refused, am.InsufficientEvidence) as exc:
            red[nm] = dict(refused=str(exc))
    ref = stage_linear([base], cuts, resonances)[base]
    ring = stage_resonance([base], ring_cuts, ring_res)[base]
    inj = {}
    # a dropped pole: 3 stages, not 4.
    #
    # THIS CONTROL GOES RED BY REFUSING, AND THE REPORT MUST SAY SO. It was
    # first written to subtract two `worst_peak_db` figures, and three poles
    # push the resonant peak out of the four-pole search window at EVERY
    # operating point -- so the stage refuses, `worst_peak_db` is `nan`, and
    # the report printed `moved_peak_db=nan`: an unknown rendered in the place
    # where a verdict belongs, which is the `FAIL(??)` failure `tools/
    # run_all.py` exists to prevent. A refusal is a first-class red outcome and
    # is recorded as one; a MIXTURE of refusals and small moves would be the
    # suspicious result, so both halves are reported.
    d = stage_linear([base], cuts, resonances, stages=3)[base]
    n_pts = len(d["points"])
    moved = {}
    if d["refused"] < n_pts:            # some points still scored -- use them
        moved = dict(moved_peak_db=round(
            abs(d["worst_peak_db"] - ref["worst_peak_db"]), 2),
            moved_cents=round(abs(d["worst_cents"] - ref["worst_cents"]), 1))
    inj["dropped-pole"] = dict(
        dimension="linear/resonance",
        moved_refused_points=f"{d['refused']}/{n_pts}",
        baseline_refused_points=f"{ref['refused']}/{len(ref['points'])}",
        **moved)
    # a 6 % cutoff skew: the tuning defect DR 0011 guards with
    d = stage_linear([base], cuts, resonances, cut_skew=1.06)[base]
    inj["cutoff-skew-6pct"] = dict(
        dimension="linear", moved_cents=round(abs(d["worst_cents"] - ref["worst_cents"]), 1))
    # a 4-entry tanh: a deliberately crude nonlinearity
    d = stage_drive([base], cfg=dict(tanh_entries=4))[base]
    b = stage_drive([base])[base]
    inj["tanh-4-entries"] = dict(
        dimension="drive",
        moved_saturation_db=None if None in (d["saturation_dbfs"], b["saturation_dbfs"])
        else round(abs(d["saturation_dbfs"] - b["saturation_dbfs"]), 2),
        moved_inharmonic_db=None if None in (d["chord_inharmonic_db"], b["chord_inharmonic_db"])
        else round(abs(d["chord_inharmonic_db"] - b["chord_inharmonic_db"]), 1))
    # a 32 Hz control quantum: the movement defect
    d = stage_movement([base], quantum=32)[base]
    m = stage_movement([base])[base]
    inj["control-quantum-32hz"] = dict(
        dimension="movement",
        moved_residual_db=round(abs(d["worst_residual_db"] - m["worst_residual_db"]), 2))
    return dict(start_red=red, injected=inj,
                baseline=dict(linear=ref, resonance=ring))


# ===========================================================================
# the report
# ===========================================================================
def compare(quick: bool = False, names=None) -> dict:
    names = list(lc.SHIPPABLE) if names is None else list(names)
    cuts = LINEAR_CUTS_QUICK if quick else LINEAR_CUTS
    ring_cuts = RING_CUTS_QUICK if quick else RING_CUTS
    ring_res = RING_RES_QUICK if quick else RING_RES
    apparatus = lc.assert_apparatus(quick=quick)

    rep = dict(apparatus=apparatus, quick=bool(quick), candidates={})
    rep["linear"] = stage_linear(names, cuts, LINEAR_RES)
    rep["resonance"] = stage_resonance(names, ring_cuts, ring_res)
    rep["bass"] = stage_bass(names)
    rep["drive"] = stage_drive(names)
    rep["movement"] = stage_movement(names)
    rep["cleanliness"] = stage_cleanliness(names)
    rep["cost"] = stage_cost(names)
    rep["controls"] = stage_controls(cuts, LINEAR_RES, ring_cuts, ring_res)
    rep["as_ships"] = stage_linear(["shipped"], cuts, LINEAR_RES,
                                   tuned=True, compensated=True)["shipped"]

    for n in names:
        cost = rep["cost"][n]
        rep["candidates"][n] = dict(
            rung=lc.CORES[n][0].RUNG,
            description=lc.CORES[n][0].DESC,
            coefficient_law=lc.CORES[n][0].COEF_LAW,
            # "matches the reference better": distance from the closed form
            matches=dict(worst_cents=rep["linear"][n]["worst_cents"],
                         worst_peak_db=rep["linear"][n]["worst_peak_db"],
                         worst_pass_db=rep["linear"][n]["worst_pass_db"]),
            # "sounds bigger": weight, compression, and whether it sings
            bigger=dict(bass_weight_db=rep["bass"][n].get("weight_db"),
                        compression_db_per_db=rep["drive"][n]["compression_db_per_db"],
                        saturation_dbfs=rep["drive"][n]["saturation_dbfs"],
                        sings_everywhere=rep["resonance"][n]["sings_everywhere"]),
            cost=dict(tanh=cost["per_sample"]["tanh"],
                      divide=cost["per_sample"]["divide"],
                      multiply=cost["per_sample"]["multiply"],
                      clocks=cost["clocks_by_divider_latency"],
                      max_divider_latency_that_fits=cost[
                          "max_divider_latency_that_fits"],
                      fits=cost["fits_budget"]))
    rep["verdict"] = _verdict(rep, names)
    return rep


def _verdict(rep: dict, names) -> dict:
    """Issue #46's own bar: **retain the current filter unless a candidate
    gives a useful, reproducible improvement within budget.** Derived from the
    numbers, not written."""
    base = "shipped"
    b = rep["candidates"][base]
    wins, blocked, notes = [], [], []
    for n in names:
        if n == base:
            continue
        c = rep["candidates"][n]
        better_tuning = c["matches"]["worst_cents"] < b["matches"]["worst_cents"] - 5.0
        better_peak = c["matches"]["worst_peak_db"] < b["matches"]["worst_peak_db"] - 0.5
        sings = c["bigger"]["sings_everywhere"] and not b["bigger"]["sings_everywhere"]
        fits = c["cost"]["fits"][DIV_CLOCKS[-1]]
        on_merit = bool(better_tuning or better_peak or sings)
        if on_merit and fits:
            wins.append(n)
        elif on_merit:
            # A candidate that WINS on sound and is excluded by cost alone is
            # the whole of DR 0001's reversal condition, and burying it in a
            # boolean would hide the one result a reader must not miss.
            blocked.append((n, c["cost"]["max_divider_latency_that_fits"]))
        notes.append(dict(candidate=n, better_tuning=bool(better_tuning),
                          better_peak=bool(better_peak), sings_where_we_do_not=bool(sings),
                          wins_on_merit=on_merit,
                          fits_budget_worst_case_divider=bool(fits),
                          max_divider_latency_that_fits=c["cost"][
                              "max_divider_latency_that_fits"]))
    if wins:
        text = ("A candidate clears the bar: " + ", ".join(wins) +
                " improves on the shipped ladder by more than the harness's own "
                "floor AND fits the clock budget. Rung 2-4 work is justified.")
    else:
        text = ("RETAIN THE CURRENT FILTER. No candidate improves on the shipped "
                "ladder by more than this harness can resolve while fitting the "
                "clock budget -- which is issue #46's own bar, and a complete "
                "result rather than a failure to deliver a change.")
    if blocked:
        text += ("\n  EXCLUDED BY COST ALONE, which is DR 0001's reversal "
                 "condition and not a tie: "
                 + "; ".join(f"{n} wins on sound and needs a divider of "
                             f"{'no latency that fits' if d is None else f'<= {d} clocks'}"
                             for n, d in blocked) + ".")
    return dict(retain=not wins, winners=wins,
                blocked_by_cost=[n for n, _ in blocked],
                per_candidate=notes, text=text)


def _print(rep: dict) -> None:
    print("Rungs 2-4: candidate ladders against the shipped one (issue #46)\n")
    print(f"  apparatus: rung-4 reference within "
          f"{rep['apparatus']['worst_db']} dB of the closed form at "
          f"{rep['apparatus']['oversample']}x\n")
    print(f"  {'candidate':14s} {'rung':>4} {'tune':>9} {'peak':>8} {'pass':>8} "
          f"{'bass':>8} {'sat':>8} {'sings':>6} {'tanh':>6} {'div':>5} {'clocks':>7}")
    print(f"  {'':14s} {'':>4} {'cents':>9} {'dB':>8} {'dB':>8} {'dB':>8} "
          f"{'dBFS':>8} {'':>6} {'/samp':>6} {'/samp':>5} {'@17':>7}")
    for n, c in rep["candidates"].items():
        m, g, k = c["matches"], c["bigger"], c["cost"]
        print(f"  {n:14s} {c['rung']:>4} {m['worst_cents']:>9.1f} "
              f"{m['worst_peak_db']:>8.2f} {m['worst_pass_db']:>8.2f} "
              f"{(g['bass_weight_db'] if g['bass_weight_db'] is not None else float('nan')):>8.2f} "
              f"{(g['saturation_dbfs'] if g['saturation_dbfs'] is not None else float('nan')):>8.2f} "
              f"{str(g['sings_everywhere']):>6} {k['tanh']:>6.1f} {k['divide']:>5.1f} "
              f"{k['clocks'][DIV_CLOCKS[-1]]:>7.0f}")
    print(f"\n  budget {CLOCKS_PER_SAMPLE} clocks/sample (DR 0001)")
    print("\n  DIVIDER LATENCY THE BUDGET ALLOWS -- exact, not read off the "
          "swept grid:")
    for n, c in rep["candidates"].items():
        d = c["cost"]["max_divider_latency_that_fits"]
        if c["cost"]["divide"] == 0:
            say = "no divider"
        elif d is None:
            say = "does not fit at any divider latency"
        else:
            say = f"<= {d} clocks"
        print(f"    {n:14s} {say}")
    red = rep["controls"]["start_red"]
    print("\n  START RED -- the null core, which has no behaviour:")
    for k_, v in red.items():
        state = "REFUSED" if "refused" in v else "scored"
        print(f"    {k_:14s} {state}")
    print("\n  INJECTED DEFECTS -- each must move its own dimension:")
    for k_, v in rep["controls"]["injected"].items():
        # every key except the dimension label, so a control that goes red by
        # REFUSING cannot be printed as a blank line or as `nan`
        moved = ", ".join(f"{kk}={vv}" for kk, vv in v.items() if kk != "dimension")
        print(f"    {k_:22s} ({v['dimension']:18s}) {moved}")
    print("\n  " + rep["verdict"]["text"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true",
                    help="two cutoffs and two resonances instead of four and three")
    ap.add_argument("--only", nargs="*", default=None,
                    help=f"candidates to run (default {' '.join(lc.SHIPPABLE)})")
    ap.add_argument("--json", metavar="PATH", help="write the full report here")
    a = ap.parse_args(argv)
    try:
        rep = compare(quick=a.quick, names=a.only)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    _print(rep)
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as fh:
            json.dump(rep, fh, indent=1, sort_keys=True, default=str)
        print(f"\nwrote {a.json}")
    return 0 if rep["verdict"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())

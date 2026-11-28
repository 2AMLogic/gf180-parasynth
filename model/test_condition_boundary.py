#!/usr/bin/env python3
"""Controls for `condition_boundary.knob_equivalents` (#1371).

`knob_equivalents` is the reachable report path for the fit-only pathology:
it skips a voice with no knob itself but sends an adjustable voice with fit
recordings only into `ours_distance`, whose removed fallback paired real and
ours on the settings the knob law was fitted on -- so the report's
knob-equivalent was quoted from the very settings the law was fitted
through, silently, with no refusal and no label, on both scales in both
conditioning modes.

Every fixture here is synthetic feature vectors: no corpus, no render, the
production report function exercised directly. That is the point -- the
instrument and its controls are buildable on any host, and only the
measurement needs the Fischer set.

    .venv/bin/python -m pytest model/test_condition_boundary.py -q
"""
from __future__ import annotations

import math
import os
import sys
from itertools import product

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import condition_boundary as cb                                   # noqa: E402
from test_discrimination import Clip                              # noqa: E402

# nine bass-drum settings, neither axis held out: the #1371 reproduction grid
BD_FIT_GRID = list(product((0.0, 5.0, 10.0), repeat=2))
LT_FIT = [(0.0,), (5.0,), (10.0,)]
LT_HELD = [(2.5,), (7.5,)]


def _cache(entries):
    """One finite vector per (voice, knobs, side), per conditioning mode.
    `ours` sits a fixed step off `real`, and the step differs per mode, so a
    mode that stopped flowing through the report would show up as two equal
    distances rather than two different ones."""
    out = {}
    for voice, knobs, side in entries:
        base = np.array([knobs[0] / 10.0 if knobs else 0.0,
                         float(len(knobs)), 1.0])
        out[(voice, knobs, side)] = {
            m: base + (np.array([step, 0.0, 0.0]) if side == "ours"
                       else np.zeros(3))
            for m, step in zip(cb.MODES, (0.25, 0.35))
        }
    return out


def test_a_fit_only_adjustable_voice_is_refused_in_both_conditioning_modes():
    """The #1371 reproduction, kept as the automated control. Nine synthetic
    bass-drum fit settings and zero held-out settings used to come out of the
    report as knob_equivalent 5.0 on both scales in both conditioning modes;
    the row must now be a refusal that names the missing held-out settings,
    carrying no distance and no knob-equivalent on either scale in either
    mode. Restoring the fallback in `ours_distance` fails the refusal
    assertion and puts the numbers back."""
    refs = [Clip("BD", k, "real") for k in BD_FIT_GRID]
    assert all(not c.is_test for c in refs)
    cache = _cache([("BD", k, s) for k in BD_FIT_GRID for s in ("real", "ours")])
    for mode in cb.MODES:
        row = cb.knob_equivalents(refs, cache, mode)["BD"]
        assert "refused" in row, (mode, row)
        assert "no held-out recording" in row["refused"], (mode, row)
        for key in ("knob_equivalent", "knob_equivalent_frozen",
                    "ours_distance", "ours_distance_frozen"):
            assert key not in row, (mode, key, row)


def test_valid_and_fixed_voices_still_report_alongside_the_refusal():
    """Continuation and distinct reasons. A valid adjustable voice (a mixed
    low-tom grid: fit settings plus held-out ones) still receives distances
    and knob-equivalents on both scales in both conditioning modes while the
    fit-only voice in the same report is refused; a fixed voice keeps its
    existing no-knob refusal, which is a different reason from the fit-only
    one; and the two conditioning modes still give the valid voice two
    different distances, as they must while the mode flows through."""
    refs = ([Clip("BD", k, "real") for k in BD_FIT_GRID]
            + [Clip("LT", k, "real") for k in LT_FIT + LT_HELD]
            + [Clip("CB", (), "real")])
    cache = _cache([("BD", k, s) for k in BD_FIT_GRID for s in ("real", "ours")]
                   + [("LT", k, s) for k in LT_FIT + LT_HELD
                      for s in ("real", "ours")])
    rows = {m: cb.knob_equivalents(refs, cache, m) for m in cb.MODES}
    for mode, r in rows.items():
        assert "refused" in r["BD"] and "no held-out recording" in r["BD"]["refused"]
        assert "refused" in r["CB"] and "no knob" in r["CB"]["refused"]
        assert r["BD"]["refused"] != r["CB"]["refused"], mode
        lt = r["LT"]
        assert "refused" not in lt, (mode, lt)
        assert math.isfinite(lt["ours_distance"]), (mode, lt)
        assert math.isfinite(lt["ours_distance_frozen"]), (mode, lt)
        for key in ("knob_equivalent", "knob_equivalent_frozen"):
            assert key in lt, (mode, key)
    assert (rows["legacy"]["LT"]["ours_distance"]
            != rows["fixed"]["LT"]["ours_distance"])

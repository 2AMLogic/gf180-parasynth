#!/usr/bin/env python3
"""Validation cases for tools/probes/octave_incidence_corpus.py -- #778 AC 5.

A COUNTER CALIBRATED ONLY ON ITSELF COUNTS NOTHING
(docs/failure-modes.md). Every case below has an answer that comes from
somewhere other than this probe:

  * the DISAGREE case is #774's own published defect, reproduced against the
    shipped module: 880 Hz built at 48 kHz and resampled to 44.1 kHz returns
    101 samples against 55 on the other side (the numbers in
    `discrimination_features.dominant_period`'s docstring), under the bare
    argmax (`--margin 0`). The probe must call that pair DISAGREE and must
    agree with `fold_rate_sweep.octave_refused`, the predicate it imports.
  * the same pair under the SHIPPED margin picks 55 vs 51 -- the #774 repair
    resolving the condition it fixed -- and must be called AGREE.
  * 440 Hz, far from the band edge, is the control that says the bare argmax
    does not disagree everywhere: its two period READINGS differ in samples
    because the rates differ but agree in seconds, inside OCTAVE_TOL_REL.
  * a clip shorter than JIT_MIN_CYCLES of the lowest in-band lag makes
    `dominant_period` return 0 on both sides: NO-PICK, never silently folded
    into agree or disagree.
  * the honesty case: the classifier's predicate IS the sweep's, by import --
    if this module ever grew its own comparison, the count would drift from
    the definition the margin dial was swept against.

    python3 -m pytest tools/probes/test_octave_incidence_corpus.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import fold_rate_sweep as FRS                           # noqa: E402
import octave_incidence_corpus as M                     # noqa: E402
from model import discrimination_features as D          # noqa: E402

BARE_ARGMAX = 0


def _octave_pair(f0: float, build_sr: int = FRS.SR_B) -> tuple:
    a = FRS.stimulus(f0, build_sr)
    b = D._resample(a, build_sr, FRS.SR_A)
    return a, build_sr, b, FRS.SR_A


def test_disagree_reproduces_the_774_defect_and_matches_the_sweep_predicate():
    a, sr_a, b, sr_b = _octave_pair(880.0)
    r = M.classify_pair(a, sr_a, b, sr_b, octave_margin=BARE_ARGMAX)
    assert (r["pa_samples"], r["pb_samples"]) == (55, 101), r
    assert r["label"] == "disagree"
    assert FRS.octave_refused(a, sr_a, b, sr_b, octave_margin=BARE_ARGMAX)


def test_shipped_margin_resolves_the_same_pair_to_agree():
    a, sr_a, b, sr_b = _octave_pair(880.0)
    r = M.classify_pair(a, sr_a, b, sr_b, octave_margin=None)
    assert r["label"] == "agree", r
    assert abs(r["ratio"] - 1.0) < FRS.OCTAVE_TOL_REL


def test_a_clip_far_from_the_band_edge_agrees_even_under_the_bare_argmax():
    a, sr_a, b, sr_b = _octave_pair(440.0)
    r = M.classify_pair(a, sr_a, b, sr_b, octave_margin=BARE_ARGMAX)
    assert r["label"] == "agree", r
    # the two readings differ in SAMPLES because the rates differ; in seconds
    # they are the same period, which is what OCTAVE_TOL_REL compares.
    ms_a = r["pa_samples"] / sr_a * 1000
    ms_b = r["pb_samples"] / sr_b * 1000
    assert abs(ms_a - ms_b) < 0.05


def test_no_pick_is_its_own_outcome_not_a_disagreement():
    rng = np.random.default_rng(0)
    short = np.zeros(200)
    short[:120] = rng.standard_normal(120)
    r = M.classify_pair(short, FRS.SR_B,
                        D._resample(short, FRS.SR_B, FRS.SR_A), FRS.SR_A)
    assert r["pa_samples"] == 0 and r["pb_samples"] == 0
    assert r["label"] == "no-pick" and r["ratio"] is None


def test_the_predicate_is_the_sweeps_by_import_not_a_restatement():
    assert M.FRS.octave_refused is FRS.octave_refused
    assert M.PINNED_COMMIT == "85fbecf"


def test_main_refuses_without_a_corpus_and_writes_nothing(tmp_path):
    out = tmp_path / "incidence.json"
    rc = M.main(["--refs", str(tmp_path / "absent"), "--out", str(out)])
    assert rc == 3 and not out.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = M.main(["--refs", str(empty), "--out", str(out)])
    assert rc == 3 and not out.exists()

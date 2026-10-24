#!/usr/bin/env python3
"""The guards in `noise_fixture_duration.py`, each driven by the input that
would defeat it (docs/verification-rules.md 8). Seconds, not minutes: nothing
here runs the experiment itself -- its decision rule, its dial hook, its
invariance precondition and its confirmation check are tested on synthetic
inputs, with one case per guard that must stay green as the control.

    python3 -m pytest tools/probes/test_noise_fixture_duration.py -q
"""
from __future__ import annotations

import copy
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import noise_fixture_duration as nfd                                 # noqa: E402
import permitted_differences as pd                                    # noqa: E402


# ---------------------------------------------------------------------------
# the dial must actually move the fixture, and must be put back
# ---------------------------------------------------------------------------
def test_the_dial_sets_the_fixture_length_and_restores_it():
    assert pd.NOISE_SECONDS == 2.0
    with nfd.noise_seconds(4.0):
        p = pd.draw_noise(np.random.default_rng(1))
        assert len(pd.build(p).x) == 4 * pd.SR
    assert pd.NOISE_SECONDS == 2.0


def test_a_dial_that_does_not_take_is_refused(monkeypatch):
    """The defeating input: a fixture that ignores NOISE_SECONDS. Without the
    check, 2 s and 4 s would be the same experiment twice and read as FLAT."""
    real = pd.draw_noise

    def stuck(rng):
        p = real(rng)
        p["seconds"] = 2.0
        return p
    monkeypatch.setattr(pd, "draw_noise", stuck)
    with pytest.raises(nfd.Refused, match="did not take"):
        with nfd.noise_seconds(4.0):
            pass
    assert pd.NOISE_SECONDS == 2.0


def test_the_seed_populations_are_disjoint_from_every_other_namespace():
    bases = [pd.CALIBRATE_BASE, pd.FPR_BASE, pd.VALIDATE_BASE, pd.SELECT_BASE,
             pd.CONFIRM_BASE]
    assert bases == sorted(bases)
    for a, b in zip(bases, bases[1:]):
        assert a + pd.SEED_SPAN <= b
    sel = {pd.SELECT_BASE + g * nfd.GROUP_STRIDE for g in range(nfd.SELECT_GROUPS)}
    rec = {pd.SELECT_BASE + nfd.RECAL_OFFSET + k * nfd.GROUP_STRIDE
           for k in range(nfd.RECAL_POPS)}
    assert not sel & rec


# ---------------------------------------------------------------------------
# the derived invariance is a precondition, not an assumption
# ---------------------------------------------------------------------------
def _res(residuals):
    return {nfd.CONTROL[0]: dict(residuals=list(residuals))}


def test_identical_mutant_residuals_pass_the_invariance_check():
    assert nfd.check_invariance([_res([0.1, 0.62]), _res([0.1, 0.62])]) == 0.0


def test_a_mutant_that_moves_with_the_dial_is_refused():
    with pytest.raises(nfd.Refused, match="must not"):
        nfd.check_invariance([_res([0.1, 0.62]), _res([0.1, 0.70])])


def test_the_mutant_really_is_blind_to_the_fixture_length():
    """The derivation itself, on two real trials: `_noise` is a prefix-stable
    draw and the mutant reads x[:4096], so its delta cannot see the dial."""
    c = pd.CASE_BY_ID[nfd.CONTROL[1]]
    out = []
    for s in nfd.GRID:
        with nfd.noise_seconds(s):
            out.append([pd.run_trial(c, pd.VALIDATE_BASE, i, nfd.CONTROL[0]).delta
                        for i in range(2)])
    assert np.max(np.abs(np.subtract(out[0], out[1]))) <= nfd.INVARIANCE_TOL


# ---------------------------------------------------------------------------
# the decision rule
# ---------------------------------------------------------------------------
def _cand(margin=1.05, state=pd.CAUGHT, caught=30, groups=40, survives=4, pops=8,
          clean_fail=0, clean_ref=0, ctl_ref=0, validate=pd.PASS):
    ctl = {pd.CAUGHT: caught, pd.MISSED: groups - caught - ctl_ref}
    if ctl_ref:
        ctl[pd.REFUSED] = ctl_ref
    clean = {cid: {pd.PASS: groups} for cid in nfd.NOISE_ROWS}
    if clean_fail:
        clean["noise/psd_slope"] = {pd.PASS: groups - clean_fail, pd.FAIL: clean_fail}
    if clean_ref:
        clean["noise/centroid"] = {pd.PASS: groups - clean_ref, pd.REFUSED: clean_ref}
    return {
        nfd.CONTROL[0]: dict(margin=margin, state=state),
        "rows": {cid: dict(validate_verdict=validate) for cid in nfd.NOISE_ROWS},
        "select": dict(control=ctl, clean=clean),
        "select_groups": groups,
        "recal": [dict(survives=i < survives) for i in range(pops)],
    }


BASE = _cand()


def test_a_material_improvement_ships():
    """The control for every refusal below: resolvable detection gain."""
    ok, why = nfd.decide(BASE, _cand(margin=1.5, caught=40))
    assert ok, why


def test_surviving_every_recalibration_is_also_material():
    ok, why = nfd.decide(BASE, _cand(margin=1.2, caught=31, survives=8))
    assert ok, why


@pytest.mark.parametrize("label,cand,rule", [
    ("nominal-only", _cand(margin=1.12, caught=31, survives=5), "4."),
    ("worse-margin", _cand(margin=1.01, caught=40, survives=8), "1."),
    ("nan-margin", _cand(margin=float("nan"), caught=40, survives=8), "1."),
    ("control-missed", _cand(margin=0.9, state=pd.MISSED, caught=40, survives=8), "1."),
    ("control-refused", _cand(margin=None, state=pd.REFUSED, caught=40, survives=8), "1."),
    ("clean-validate-red", _cand(margin=1.5, caught=40, validate=pd.FAIL), "2."),
    ("select-false-alarm", _cand(margin=1.5, caught=40, clean_fail=1), "3."),
    ("select-clean-refused", _cand(margin=1.5, caught=40, clean_ref=1), "3."),
    ("select-control-refused", _cand(margin=1.5, caught=39, ctl_ref=1), "3."),
])
def test_a_candidate_that_defeats_a_condition_does_not_ship(label, cand, rule):
    ok, why = nfd.decide(BASE, cand)
    assert not ok, (label, why)
    assert any(w.startswith(rule) for w in why), (label, why)


def test_mismatched_group_counts_refuse_rather_than_compare():
    with pytest.raises(nfd.Refused):
        nfd.decide(BASE, _cand(margin=1.5, caught=40, groups=41))


# ---------------------------------------------------------------------------
# the confirmation check
# ---------------------------------------------------------------------------
GOOD = dict(groups=10,
            rows={"noise/psd_slope": {pd.PASS: 10}, "phase/centroid": {pd.PASS: 10}},
            pairs={"SINGLE_WINDOW_SLOPE|noise/psd_slope": {pd.CAUGHT: 10}})


def test_a_clean_confirmation_holds():
    assert nfd.confirmation_holds(GOOD) == []


@pytest.mark.parametrize("where,key,state", [
    ("rows", "noise/psd_slope", pd.FAIL),
    ("rows", "phase/centroid", pd.REFUSED),
    ("pairs", "SINGLE_WINDOW_SLOPE|noise/psd_slope", pd.MISSED),
    ("pairs", "SINGLE_WINDOW_SLOPE|noise/psd_slope", pd.REFUSED),
    ("pairs", "SINGLE_WINDOW_SLOPE|noise/psd_slope", pd.NO_VERDICT),
])
def test_any_non_pass_or_non_caught_group_contradicts(where, key, state):
    bad = copy.deepcopy(GOOD)
    good_state = pd.PASS if where == "rows" else pd.CAUGHT
    bad[where][key] = {good_state: 9, state: 1}
    found = nfd.confirmation_holds(bad)
    assert found and state in found[0], found

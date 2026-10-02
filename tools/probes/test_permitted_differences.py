#!/usr/bin/env python3
"""Is the permitted-differences suite evidence, or does it only look like it?

    python3 -m pytest tools/probes/test_permitted_differences.py -q

Collected by `make verify`'s broad pytest job (`pytest model/ spec/ tools/ ...`)
and by CI's `pytest tools/ -q` job (rungs.yml, "every tools/ test, by
directory"). 51 tests, 89 s measured alone on an 8-core box and 129 s with two
other jobs beside it, nearly all of it in the nine injected controls and the
deterministic 96-draw calibration replay -- both of which are the point: a
suite of invariance checks
is trivially green if its tolerances are loose, so what has to be tested is
that each tolerance is tight enough to catch a defect and that none of them
false-alarms.

Five things are asserted here that the probe cannot assert about itself:

  1. **Every row states what it permits AND what it does not.** #158 is
     explicit that these differences are "not universally benign", so a row
     carrying only an invariance claim is incomplete by construction.
  2. **Every row ships with an injected defect that reds it**, and each
     injection reds exactly its declared rows -- neither fewer (the threshold
     is too loose to be evidence) nor more (the hook is not the mechanism it
     claims).
  3. **The degenerate suite fails.** `--inject BLANKET_INVARIANCE` is the
     "one blanket invariance rule" the acceptance criteria name as the thing
     that must not pass: it reds exactly the five not-permitted rows.
  4. **The thresholds do not false-alarm**, measured on a seed stream disjoint
     from the one they were calibrated on. The number quoted in the PR comes
     from `--false-alarm-rate 50`; this file re-measures a smaller N so the
     property is enforced on every run rather than asserted once.
  5. **The calibration gate itself can go red.** A gate asserted green on the
     current table is indistinguishable from a gate that cannot fail, so each
     of its three verdicts -- too tight, vacuous, unsatisfiable floor -- is
     driven as an input, with the unmutated row as the control.
  6. **`SAFETY = 4` is inside a measured window.** Removing the margin
     false-alarms; doubling it loses two of the five injected defects. Both
     ends are re-measured here, not transcribed from the docstring's
     `--safety-sweep` table.
"""
from __future__ import annotations

import dataclasses
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import permitted_differences as pd                                    # noqa: E402

QUICK = 12          # trials per row inside this file


# ---------------------------------------------------------------------------
# 1. the matrix says what it permits and what it does not
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", pd.CASES, ids=lambda c: c.cid)
def test_every_row_states_both_halves_of_its_case_definition(case):
    assert len(case.permitted) > 40, f"{case.cid}: no statement of what it permits"
    assert len(case.not_permitted) > 40, \
        f"{case.cid}: no statement of what it does NOT permit -- #158's whole " \
        f"point is that a permitted difference is permitted PER CASE"
    assert len(case.policy.how) > 30, \
        f"{case.cid}: the threshold does not say how it was chosen"
    assert case.policy.tightness in ("calibrated", "floor")
    assert case.transform in pd.TRANSFORMS
    assert case.fixture in pd.FIXTURES


def test_the_five_named_differences_of_158_are_all_covered():
    """Leading silence, polarity, gain, independent noise realisation,
    free-running phase -- each with at least one PERMITTED row and the set as a
    whole with the not-permitted counterparts #158 names."""
    permitted_by_transform = {}
    for c in pd.CASES:
        permitted_by_transform.setdefault(c.transform, []).append(c.policy.kind)
    for t in ("leading_silence", "polarity", "gain", "noise_realisation",
              "free_phase"):
        assert "permitted" in permitted_by_transform.get(t, []), \
            f"{t} has no permitted-difference row"
    # gain is the one #158 splits explicitly: permitted for a level-normalised
    # descriptor, not permitted for an accent test.
    assert "gain/norm_rms_db" in pd.CASE_BY_ID
    assert "gain/accent_ratio_db" in pd.CASE_BY_ID          # uniform gain, permitted
    assert "gain/accent_second_only" in pd.CASE_BY_ID       # per-strike gain, not
    assert len(pd.NOT_PERMITTED) >= 5, pd.NOT_PERMITTED


def test_every_row_ships_with_a_defect_it_must_catch():
    """What stops a resolution-floor threshold from being vacuous. Also
    asserted at import; here for the message."""
    covered = {cid for v in pd.EXPECT_RED.values() for cid in v}
    assert not set(pd.CASE_BY_ID) - covered


def test_a_not_permitted_row_fails_on_its_own_no_difference_reading():
    """The structural version of the blanket-invariance control: whatever a row
    reads when the two records are identical must NOT satisfy it."""
    for cid in pd.NOT_PERMITTED:
        c = pd.CASE_BY_ID[cid]
        info = {c.policy.predict: 100 * max(c.policy.threshold, 1.0)} \
            if c.policy.kind == "tracks" else {}
        assert not c.policy.passes(c.null_delta, info), \
            f"{cid}: a no-difference reading of {c.null_delta} satisfies it"


# ---------------------------------------------------------------------------
# 2. the draws are reproducible, and the three streams are disjoint
# ---------------------------------------------------------------------------
def test_case_seed_is_stable_across_processes():
    """`hash(str)` is salted per process; this file's thresholds and quoted
    false-alarm rate are claims about specific draws, so the per-row seed must
    be fixed by the standard and not by the interpreter."""
    import zlib
    for cid in pd.CASE_BY_ID:
        assert pd.case_seed(cid) == zlib.crc32(cid.encode()) % 2**31
    # And the seed a given row gets is pinned to a literal, so a change to the
    # derivation that silently moves every draw cannot pass.
    assert pd.case_seed("pol/centroid") == 1329328499, pd.case_seed("pol/centroid")


def test_the_same_trial_twice_gives_the_same_delta():
    c = pd.CASE_BY_ID["noise/psd_slope"]
    a = pd.run_trial(c, pd.VALIDATE_BASE, 3, None)
    b = pd.run_trial(c, pd.VALIDATE_BASE, 3, None)
    assert a.ok and b.ok
    assert a.delta == b.delta


def test_calibration_validation_and_false_alarm_streams_are_disjoint():
    for base, other in ((pd.CALIBRATE_BASE, pd.FPR_BASE),
                        (pd.FPR_BASE, pd.VALIDATE_BASE)):
        assert base + pd.SEED_SPAN <= other


# ---------------------------------------------------------------------------
# 3. preconditions refuse rather than report
# ---------------------------------------------------------------------------
def _sig(x, meta=None):
    return pd.Signal(np.asarray(x, dtype=float), pd.SR, meta or {})


@pytest.mark.parametrize("bad,expect", [
    ("silent", "silent"),
    ("nonfinite", "finite"),
    ("identical", "did not change"),
    ("no_lead", "pre-onset lead"),
])
def test_a_failed_precondition_is_refused_not_answered(bad, expect):
    c = pd.CASE_BY_ID["ls/decay_tau"]
    p = pd.draw_damped(np.random.default_rng(1))
    a = pd.build(p)
    b = pd.t_leading_silence(p, np.random.default_rng(2)).post(pd.build(p))
    if bad == "silent":
        a = _sig(np.zeros(len(a.x)), a.meta)
    elif bad == "nonfinite":
        x = a.x.copy()
        x[1000] = np.nan
        a = _sig(x, a.meta)
    elif bad == "identical":
        b = a
    elif bad == "no_lead":
        a = _sig(np.abs(a.x) + 0.5, a.meta)        # full amplitude from sample 0
    why = pd.check_preconditions(c, a, b, {})
    assert why is not None and expect in why, (bad, why)


def test_a_row_whose_estimator_refuses_is_REFUSED_and_not_PASS():
    """REFUSED is a first-class outcome: a row that could not measure must not
    report the absence of a difference as the absence of a defect."""
    always_refuses = pd.Case(
        "test/refuser", "polarity", "asym", "refuser", "relative",
        lambda ctx, a, b, info: pd._refused("by construction"),
        pd._exact(1e-9, "irrelevant, this row never produces a number", 0.0),
        "nothing, this is a test double of an estimator that cannot answer",
        "nothing, this is a test double of an estimator that cannot answer")
    r = pd.run_case(always_refuses, 4, pd.VALIDATE_BASE, None)
    assert r.verdict == pd.REFUSED
    assert r.refusals == 4 and r.exceedances == 0


def test_the_suite_exit_status_separates_refused_from_failed():
    assert (pd.PASS, pd.FAIL, pd.REFUSED) == ("PASS", "FAIL", "REFUSED")


# ---------------------------------------------------------------------------
# 4. the suite is green where it should be, red where it must be
# ---------------------------------------------------------------------------
def test_the_suite_is_green_on_the_validation_stream():
    rows = pd.run_suite(QUICK, pd.VALIDATE_BASE, None)
    bad = [(r.case.cid, r.verdict, r.reasons[:1]) for r in rows if r.verdict != pd.PASS]
    assert not bad, bad
    assert len(rows) == len(pd.CASES) >= 18


@pytest.mark.parametrize("name", sorted(pd.INJECTIONS))
def test_each_injected_control_reds_exactly_its_declared_rows(name):
    rows = pd.run_suite(QUICK, pd.VALIDATE_BASE, name)
    red = tuple(sorted(r.case.cid for r in rows if r.verdict == pd.FAIL))
    refused = tuple(sorted(r.case.cid for r in rows if r.verdict == pd.REFUSED))
    assert red == tuple(sorted(pd.EXPECT_RED[name])), \
        f"{name}: declared {pd.EXPECT_RED[name]}, observed {red}"
    assert not refused, \
        f"{name}: a control that REFUSES has not shown the row catches anything: {refused}"


def test_blanket_invariance_cannot_pass_this_suite():
    """The acceptance criteria's edge case, stated as its own test because it
    is the one that decides whether anything else here means much: a suite
    that answers "no difference" to every comparison must fail the rows where
    a difference is the measurement, and it must not be rescued by the
    permitted rows passing."""
    rows = pd.run_suite(QUICK, pd.VALIDATE_BASE, "BLANKET_INVARIANCE")
    red = {r.case.cid for r in rows if r.verdict == pd.FAIL}
    assert red == set(pd.NOT_PERMITTED)
    assert {r.case.cid for r in rows if r.verdict == pd.PASS} == \
        set(pd.CASE_BY_ID) - set(pd.NOT_PERMITTED)


# ---------------------------------------------------------------------------
# 5. the thresholds, re-derived
# ---------------------------------------------------------------------------
def test_the_committed_thresholds_are_satisfiable_and_not_vacuous(capsys):
    """The deterministic replay of the calibration the table was built from.
    Exits non-zero on a threshold that has fallen below `SAFETY x` the worst
    draw (it would false-alarm) or risen more than `SLACK_FACTOR x` above it
    (it would be vacuous). Both directions matter: an unsatisfiable gate and a
    gate that cannot fail are the same kind of mistake."""
    rc = pd.calibrate(pd.CAL_TRIALS)
    out = capsys.readouterr().out
    assert rc == 0, out[-2000:]
    assert "every committed threshold is satisfiable" in out


def test_every_calibrated_threshold_records_the_measurement_behind_it():
    for c in pd.CASES:
        assert not math.isnan(c.policy.calibrated), \
            f"{c.cid}: `calibrated=` does not record the draw the threshold came from"


# --- the calibration gate ships with the inputs that defeat it -------------
# `test_the_committed_thresholds_are_satisfiable_and_not_vacuous` asserts the
# gate is GREEN on the current table, which on its own is also what a gate that
# can never go red looks like (docs/verification-rules.md 8, and the three
# unsatisfiable gates CLAUDE.md records being written here in one day). So each
# of the three verdicts the gate can return is driven as an input: a threshold
# below the recommendation, one far above it, and a resolution floor on the
# wrong side of its binding draw. Eight draws on the 0.5 s `asym` fixture
# rather than 96, because what is under test is the GATE, not the table.
GATE_TRIALS = 8


def _one_row(cid: str, **policy_overrides):
    c = pd.CASE_BY_ID[cid]
    return [dataclasses.replace(c, policy=dataclasses.replace(c.policy,
                                                              **policy_overrides))]


def test_the_calibration_gate_is_green_on_the_rows_it_is_handed_unmutated(monkeypatch,
                                                                          capsys):
    """The control for the three below: the same row, same draw count, committed
    threshold untouched. Without this, a gate that returned 1 unconditionally
    would pass all three."""
    monkeypatch.setattr(pd, "CASES", _one_row("phase/centroid"))
    rc = pd.calibrate(GATE_TRIALS)
    out = capsys.readouterr().out
    assert rc == 0, out
    monkeypatch.setattr(pd, "CASES", _one_row("phase/sample_rms_db"))
    assert pd.calibrate(GATE_TRIALS) == 0, capsys.readouterr().out


@pytest.mark.parametrize("cid,overrides,state,why", [
    # A PERMITTED threshold tightened under SAFETY x the worst draw: it would
    # false-alarm, and an unsatisfiable gate is worse than no gate.
    ("phase/centroid", dict(threshold=1e-12), "TIGHT", "below the recommendation"),
    # The same threshold loosened past SLACK_FACTOR x: it cannot fail, which is
    # the defect this whole file exists to rule out.
    ("phase/centroid", dict(threshold=1.0), "SLACK", "vacuous"),
    # A MOVES floor moved above the smallest difference the transform produces:
    # the row would red on a difference it is supposed to see.
    ("phase/sample_rms_db", dict(threshold=50.0), "UNSATISFIABLE", "false-alarm"),
])
def test_the_calibration_gate_reds_a_threshold_that_defeats_it(
        monkeypatch, capsys, cid, overrides, state, why):
    monkeypatch.setattr(pd, "CASES", _one_row(cid, **overrides))
    rc = pd.calibrate(GATE_TRIALS)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert state in out, out
    assert why in out, out
    assert "every committed threshold is satisfiable" not in out


# ---------------------------------------------------------------------------
# 6. and does not produce a steady stream of false regressions
# ---------------------------------------------------------------------------
def test_the_suite_does_not_false_alarm_on_fresh_seeds(capsys):
    """#158: "with hundreds of comparisons, uncalibrated thresholds produce a
    steady stream of false regressions." Eight suite runs is 144 row-level
    comparisons; `--false-alarm-rate 50` (900 comparisons) is the figure the
    PR quotes. Both draw from FPR_BASE, which the thresholds were NOT
    calibrated on."""
    rc, summary = pd.false_alarm_rate(8, QUICK)
    out = capsys.readouterr().out
    assert rc == 0, out[-2000:]
    assert summary["false_alarms"] == 0, summary["per_case"]
    assert not summary["refused"], summary["refused"]
    assert summary["comparisons"] == 8 * len(pd.CASES)


# ---------------------------------------------------------------------------
# 7. and SAFETY is where it is for a measured reason, in both directions
# ---------------------------------------------------------------------------
def test_safety_is_bounded_from_below_by_false_alarms():
    """`SAFETY = 4` is inherited by every calibrated threshold, so it gets the
    sweep CLAUDE.md asks for rather than a sentence. The full table is in the
    probe's docstring (`--safety-sweep 20`, 360 comparisons per factor); this
    is the cheap regression on the fact that makes 4 a choice rather than a
    taste: with the margin removed the suite false-alarms on a seed stream it
    was not calibrated on, and at 4x it does not.

    WHY 0.5x AND NOT 1x, WHICH IS THE MORE INTERESTING FACTOR. This assertion
    was written against 1x first and FAILED at `reps=3`: 1x reds on 3 of 20
    runs and the first is run 12, so a 3-run regression on it would have been
    an unsatisfiable gate -- the exact mistake CLAUDE.md records being made
    three times in one day, caught here by running the gate before committing
    it. 0.5x reds on run 0 and on all 20, so two runs are decisive. The 1x
    figure stays a `--safety-sweep` number, where it is measured over enough
    runs to be one."""
    bare, _, red_runs = pd.count_false_alarms(2, QUICK, pd.scaled_cases(0.5))
    shipped, _, _ = pd.count_false_alarms(2, QUICK, pd.scaled_cases(pd.SAFETY))
    assert bare > 0 and red_runs == 2, \
        f"half the margin no longer false-alarms on either run ({bare} red " \
        f"rows, {red_runs} red runs), so this no longer bounds SAFETY from " \
        f"below -- re-run --safety-sweep and re-derive the window"
    assert shipped == 0, shipped


def test_safety_is_bounded_from_above_by_lost_detection():
    """The half a false-alarm measurement cannot give you: raising a threshold
    always stops the false alarms, so the upper bound has to come from the
    injected defects. At 4x all five (defect, calibrated row) pairs still go
    red; at 8x two of them do not, and `noise/psd_slope` -- whose defect sits
    only 1.05x above its committed threshold -- is one of them."""
    assert pd.detection_at(pd.scaled_cases(pd.SAFETY), QUICK, pd.VALIDATE_BASE) \
        == len(pd.DETECTION_PAIRS)
    assert pd.detection_at(pd.scaled_cases(2 * pd.SAFETY), QUICK, pd.VALIDATE_BASE) \
        < len(pd.DETECTION_PAIRS), \
        "doubling SAFETY no longer loses any injected defect, so the sweep no " \
        "longer bounds it from above and the shipped factor is unconstrained"


def test_the_sweep_only_moves_the_thresholds_that_depend_on_safety():
    """A resolution floor is not SAFETY x anything. If the sweep scaled one, it
    would be reporting a sensitivity to a parameter the row does not have."""
    for a, b in zip(pd.CASES, pd.scaled_cases(100.0)):
        assert a.cid == b.cid
        if a.policy.tightness == "floor":
            assert a.policy.threshold == b.policy.threshold, a.cid
        else:
            assert b.policy.threshold > a.policy.threshold, a.cid
    assert len(pd.CALIBRATED_ROWS) == 5, pd.CALIBRATED_ROWS
    assert len(pd.DETECTION_PAIRS) == 5, pd.DETECTION_PAIRS

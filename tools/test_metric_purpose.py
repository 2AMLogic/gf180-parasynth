"""#141: a metric's DIRECTION is part of its definition (plan075 section 6).

The cowbell (D13A) failed at 11.34x on "unwanted difference tone". The machine
holds that component at -67.97 dB; ours measures -102.00 dB. We have 34 dB
LESS of a component the metric's own name calls unwanted, and a symmetric
3 dB tolerance scored the deficit as a mismatch. A metric cannot mean "less
is better" and also penalise less.

The repair is a RUBRIC change, not a sound change: the metric is declared a
DEFECT CEILING, so only EXCESS over the reference is a distance. These tests
pin the direction, the known answers either side of the ceiling, the old
symmetric scoring as a control that still reproduces the historical 11.34,
and -- the one that matters most -- that the ceiling still catches the defect
it exists for (DR 0010: one swing gate on the SUM of the two squares).
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am                                          # noqa: E402
import run_case as rc                                               # noqa: E402
import scorecard as sc                                              # noqa: E402

NAME = "unwanted difference tone"
CASE = {"case_id": "D13A",
        "required_measurements": "Partial balance; unwanted difference tone; decay"}

# The D13A metrics exactly as the committed record held them before this
# change (source 7dd6337, run_case@531aa8a3731d), and as a clean re-run on
# origin/main ffc1c00 reproduces them to the fourth decimal.
HISTORICAL = {
    "Partial balance": {"value": 6.5461, "units": "dB", "reference": 15.0177,
                        "error": -8.4716, "tolerance": 3.0, "valid": True,
                        "tolerance_basis": "energy ratio"},
    NAME: {"value": -101.9984, "units": "dB", "reference": -67.9664,
           "error": -34.032, "tolerance": 3.0, "valid": True,
           "tolerance_basis": "energy ratio"},
    "decay": {"value": 225.4697, "units": "ms", "reference": 255.2691,
              "error": -29.7993, "tolerance": 127.6345, "valid": True,
              "tolerance_basis": "time"},
}
HISTORICAL_WORST = 34.032 / 3.0          # 11.344, the board's old headline


def record(metrics: dict) -> dict:
    return {"engine": "fixed-model",
            "provenance": {"command": "tools/run_case.py D13A",
                           "worktree": {"commit": "abc", "dirty": False},
                           "inputs": {"model/audio_measure.py": "sha256:AAAA",
                                      "tools/run_case.py": "sha256:BBBB"}},
            "metrics": metrics}


def with_purpose(purpose: str | None, error: float = -34.032) -> dict:
    """The historical record with the difference tone's purpose and error set."""
    m = copy.deepcopy(HISTORICAL)
    m[NAME]["error"] = error
    m[NAME]["value"] = round(m[NAME]["reference"] + error, 4)
    if purpose is not None:
        m[NAME]["purpose"] = purpose
    for other in ("Partial balance", "decay"):
        if purpose is not None:
            m[other]["purpose"] = sc.MATCH
    return record(m)


# --- the declaration -----------------------------------------------------------

def test_the_cowbell_difference_tone_is_declared_a_defect_ceiling():
    """The direction is written down next to the estimator, not inferred from
    the name. Everything else the cowbell measures stays two-sided."""
    assert rc.metric_purpose(NAME) == sc.DEFECT_CEILING
    assert rc.metric_purpose("Partial balance") == sc.MATCH
    assert rc.metric_purpose("decay") == sc.MATCH


def test_measure_pair_writes_the_purpose_into_the_record():
    """The board reads the purpose from the RECORD, so the record must carry
    it -- a direction that lives only in the runner is invisible to the
    scorecard and to compare()."""
    est = lambda v: (lambda *_a: am.Estimate(v, True, "", {}))
    m = rc.measure_pair(NAME, "dB", est(-102.0), (None, 0), (None, 0),
                        rc.tol_db, {}, est_ref=est(-68.0))
    assert m["purpose"] == sc.DEFECT_CEILING and m["error"] == pytest.approx(-34.0)
    m = rc.measure_pair("Partial balance", "dB", est(6.5), (None, 0), (None, 0),
                        rc.tol_db, {}, est_ref=est(15.0))
    assert m["purpose"] == sc.MATCH


# --- known answers either side of the ceiling ------------------------------------

def test_less_than_the_reference_is_no_distance_on_a_ceiling():
    """34 dB LESS of an unwanted component is not a defect: distance 0."""
    r = sc.evaluate(CASE, with_purpose(sc.DEFECT_CEILING))
    assert r["properties"][NAME] == 0.0
    # and the headline is now the partial balance, the real defect (#107)
    assert r["state"] == sc.FAIL and r["why"] == "worst: Partial balance"
    assert r["worst"] == pytest.approx(8.4716 / 3.0)


def test_more_than_the_reference_by_more_than_the_tolerance_fails():
    """Excess is still the defect: 3.5 dB over a 3 dB ceiling fails, and the
    distance is the excess over the tolerance, not clamped."""
    r = sc.evaluate(CASE, with_purpose(sc.DEFECT_CEILING, error=+3.5))
    assert r["properties"][NAME] == pytest.approx(3.5 / 3.0)
    r = sc.evaluate(CASE, with_purpose(sc.DEFECT_CEILING, error=+42.2))
    assert r["properties"][NAME] == pytest.approx(42.2 / 3.0)
    assert r["why"] == f"worst: {NAME}"


def test_excess_inside_the_tolerance_passes_the_ceiling():
    r = sc.evaluate(CASE, with_purpose(sc.DEFECT_CEILING, error=+2.9))
    assert r["properties"][NAME] == pytest.approx(2.9 / 3.0)
    assert r["properties"][NAME] <= 1.0


# --- the old scoring, kept as a control -----------------------------------------

def test_the_symmetric_control_reproduces_the_historical_11_34():
    """The same record scored two-sided gives the number the board used to
    show. If this stops reproducing, the rubric change is no longer the only
    thing that moved."""
    for r in (sc.evaluate(CASE, with_purpose(sc.MATCH)),
              sc.evaluate(CASE, record(copy.deepcopy(HISTORICAL)))):   # legacy: no purpose
        assert r["properties"][NAME] == pytest.approx(HISTORICAL_WORST)
        assert r["worst"] == pytest.approx(11.344, abs=5e-4)
        assert r["why"] == f"worst: {NAME}"


def test_an_unknown_purpose_is_refused_not_scored():
    """A direction the board does not understand is no verdict. Guessing
    two-sided would silently re-introduce #141; guessing one-sided would hide
    deficits."""
    r = sc.evaluate(CASE, with_purpose("lower is better, probably"))
    assert r["state"] == sc.NO_VERDICT and NAME in r["why"]


def test_a_rubric_change_is_incomparable_not_an_improvement():
    """compare() must not read 11.34 -> 2.82 as the cowbell getting better.
    The measurement policy differs, so the verdict is INCOMPARABLE."""
    old = sc.evaluate(CASE, record(copy.deepcopy(HISTORICAL)))
    new = sc.evaluate(CASE, with_purpose(sc.DEFECT_CEILING))
    out = sc.compare(old, new)
    assert out["verdict"] == sc.INCOMPARABLE, out
    assert any("policy" in reason for reason in out["reasons"]), out


# --- the control that shows the ceiling still sees its defect --------------------

def _diff_tone_of(inject: str | None) -> am.Estimate:
    x, sr = rc.render_drum_solo("CB", inject=inject)
    y = rc.prepare(x, sr, side="our CB render")
    est = next(e for n, _u, e, _t in rc.DRUM_PLAN["CB"] if n == NAME)
    return est(y, sr)


def test_the_ceiling_still_catches_gating_the_sum():
    """DR 0010's defect -- one swing gate on the SUM of the two squares,
    nl(a + b) -- put the difference tone 42 dB above the machine's. A
    one-sided metric is only honest if it still fails that. Scored against
    the machine's recorded -67.9664 dB."""
    ref = HISTORICAL[NAME]["reference"]
    clean, bad = _diff_tone_of(None), _diff_tone_of("CB_GATE_THE_SUM")
    assert clean.ok and bad.ok, (clean, bad)
    tol = HISTORICAL[NAME]["tolerance"]
    d_clean = sc.metric_distance({"error": clean.value - ref, "tolerance": tol,
                                  "purpose": sc.DEFECT_CEILING})
    d_bad = sc.metric_distance({"error": bad.value - ref, "tolerance": tol,
                                "purpose": sc.DEFECT_CEILING})
    assert d_clean == 0.0, clean.value
    assert d_bad > 1.0, f"gated sum reads {bad.value:.1f} dB against {ref} -- not caught"


# --- the preserved history -------------------------------------------------------

def test_the_d13a_record_preserves_its_pre_rubric_score():
    """The old 11.34 is kept in the record, labelled a rubric change, so the
    drop to the partial balance cannot be read as a sound improvement."""
    res = json.loads((ROOT / "docs/scorecard/results/D13A.json").read_text())
    hist = res["rubric_history"]
    assert hist and hist[0]["kind"] == "rubric change (measurement-version change), not a sound change"
    assert hist[0]["worst"] == pytest.approx(11.344, abs=5e-3)
    assert hist[0]["metrics"][NAME]["error"] == HISTORICAL[NAME]["error"]
    assert res["metrics"][NAME]["purpose"] == sc.DEFECT_CEILING


# --- the history mechanism itself -------------------------------------------------

def _write(tmp_path, res):
    p = tmp_path / "D13A.json"
    p.write_text(json.dumps(res))
    return p


def test_a_changed_purpose_appends_a_labelled_history_entry(tmp_path):
    dest = _write(tmp_path, record(copy.deepcopy(HISTORICAL)))
    new = with_purpose(sc.DEFECT_CEILING)
    rc.carry_rubric_history(CASE, dest, new)
    (h,) = new["rubric_history"]
    assert h["kind"] == rc.RUBRIC_CHANGE and h["state"] == sc.FAIL
    assert h["worst"] == pytest.approx(HISTORICAL_WORST)
    assert list(h["changed"]) == [NAME]
    assert h["changed"][NAME]["before"]["purpose"] == sc.MATCH
    assert h["changed"][NAME]["after"]["purpose"] == sc.DEFECT_CEILING


def test_a_re_measurement_under_the_same_rubric_adds_no_history(tmp_path):
    """Only the ruler moving is a rubric change; the device moving is not,
    and earlier history is carried forward untouched."""
    old = with_purpose(sc.DEFECT_CEILING)
    old["rubric_history"] = [{"kind": rc.RUBRIC_CHANGE, "worst": 11.344}]
    dest = _write(tmp_path, old)
    new = with_purpose(sc.DEFECT_CEILING, error=-30.0)
    rc.carry_rubric_history(CASE, dest, new)
    assert new["rubric_history"] == old["rubric_history"]
    fresh = with_purpose(sc.DEFECT_CEILING)
    rc.carry_rubric_history(CASE, tmp_path / "absent.json", fresh)
    assert "rubric_history" not in fresh

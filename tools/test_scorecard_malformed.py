"""A record the board cannot read is `no verdict` -- never a traceback, never
`inf` (issue #1636).

`scorecard.evaluate` is where a verdict is made, and callers that are not the
board's own CLI hand it records straight from disk (`score_drum_i2s.py`,
`score_ensemble_i2s.py`, `verify_mono_case.py`, the two comparators). Before
#1636 a metric that was not an object raised `AttributeError` out of it, an
integer beyond float range raised `OverflowError`, and two finite numbers
whose ratio overflowed were scored `fail` at `worst: inf` -- which the
comparators then published as a bare `Infinity` token, and #1406 / #1409 had
already established that that is not JSON.

Each test is one row of the issue, kept as the control its guard must stay red
against (`docs/verification-rules.md` rule 8). The tests under "what must NOT
be refused" are the other half: a guard that refused every large number would
pass the malformed rows just as easily.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scorecard as sc                                              # noqa: E402

MAX = sys.float_info.max

CASE = {"case_id": "D02A", "family": "Drums", "subject": "Snare / anchor",
        "split": "Development", "reference_target": "Fischer hardware sample",
        "required_measurements": "Body/noise balance; attack; noise decay"}


def _metric(**over):
    metric = {"value": 4.23, "units": "ms", "reference": 4.33, "error": -0.10,
              "tolerance": 2.1655, "valid": True}
    metric.update(over)
    return metric


def _record(**over):
    record = {"engine": "fixed-model", "case_id": "D02A",
              "provenance": {"worktree": {"commit": "abc1234"},
                             "command": "tools/run_case.py",
                             "inputs": {"a": "sha256:1"}},
              "metrics": {name: _metric() for name in
                          ("Body/noise balance", "attack", "noise decay")}}
    record.update(over)
    return record


def _attack(record, **fields):
    record["metrics"]["attack"].update(fields)
    return record


def _no_verdict(record, *named):
    """`evaluate` answers, the answer is NO VERDICT with no distance, and its
    `why` names the malformed part."""
    verdict = sc.evaluate(CASE, record)
    assert verdict["state"] == sc.NO_VERDICT
    assert verdict["worst"] is None
    for name in named:
        assert name in verdict["why"]
    return verdict


# ---- row 1: a record, metric, `metrics` or `provenance` that is not an object


@pytest.mark.parametrize("not_an_object", [5, [1], "x", None])
def test_a_metric_that_is_not_an_object_has_no_verdict(not_an_object):
    record = _record()
    record["metrics"]["attack"] = not_an_object
    _no_verdict(record, "attack")


@pytest.mark.parametrize("not_an_object",
                         [5, ["Body/noise balance", "attack", "noise decay"], "attack"])
def test_metrics_that_are_not_an_object_have_no_verdict(not_an_object):
    _no_verdict(_record(metrics=not_an_object), "metrics")


@pytest.mark.parametrize("not_an_object", [5, ["worktree"], "worktree"])
def test_provenance_that_is_not_an_object_has_no_verdict(not_an_object):
    _no_verdict(_record(provenance=not_an_object), "provenance")


@pytest.mark.parametrize("not_an_object", [5, [1], "x"])
def test_a_record_that_is_not_an_object_has_no_verdict(not_an_object):
    assert _no_verdict(not_an_object, "record")["engine"] == ""


# ---- row 2: an integer beyond float range ----------------------------------


@pytest.mark.parametrize("field", ["error", "tolerance", "value", "reference"])
@pytest.mark.parametrize("integer", [10**400, -(10**400), 10**5000],
                         ids=["10^400", "-10^400", "10^5000"])
def test_an_integer_beyond_float_range_has_no_verdict(field, integer):
    """`math.isfinite` converts its argument to float and raises
    `OverflowError` on one it cannot. `10**5000` is past the interpreter's
    int-to-string limit as well, so the refusal cannot quote the number: it
    has to name the field instead."""
    _no_verdict(_attack(_record(), **{field: integer}), "attack", "invalid number")


def test_an_integer_within_float_range_is_still_a_number():
    assert sc.metric_distance({"error": 10**308, "tolerance": 1e300}) == pytest.approx(1e8)


# ---- row 3: a finite error over a finite tolerance whose ratio overflows ---


@pytest.mark.parametrize("purpose,error",
                         [(sc.MATCH, MAX), (sc.MATCH, -MAX), (sc.DEFECT_CEILING, MAX)])
def test_a_distance_that_overflows_has_no_verdict(purpose, error):
    """Every number passes the finiteness checks; only their ratio is not a
    number. It was scored `fail` at `worst: inf`."""
    _no_verdict(_attack(_record(), error=error, tolerance=0.01, purpose=purpose),
                "attack", "finite")


def test_metric_distance_itself_refuses_a_ratio_that_overflows():
    for purpose in (sc.MATCH, sc.DEFECT_CEILING):
        with pytest.raises(ValueError, match="finite"):
            sc.metric_distance({"error": 1e300, "tolerance": 1e-300, "purpose": purpose})
    with pytest.raises(ValueError, match="invalid number"):
        sc.metric_distance({"error": 10**400, "tolerance": 1.0})


# ---- what must NOT be refused ----------------------------------------------


def test_the_well_formed_record_still_passes():
    verdict = sc.evaluate(CASE, _record())
    assert verdict["state"] == sc.PASS
    assert verdict["worst"] == pytest.approx(0.10 / 2.1655)


def test_a_large_finite_distance_is_still_a_verdict():
    """The guard is on the distance, not on the size of the numbers."""
    at_limit = sc.evaluate(CASE, _attack(_record(), error=MAX, tolerance=1.0))
    assert at_limit["state"] == sc.FAIL and at_limit["worst"] == MAX
    far = sc.evaluate(CASE, _attack(_record(), error=10**308, tolerance=1e298))
    assert far["state"] == sc.FAIL and far["worst"] == pytest.approx(1e10)
    assert sc.evaluate(CASE, _attack(_record(), error=1e300, tolerance=1e300))["worst"] == 1.0


def test_a_defect_ceiling_still_clamps_a_large_negative_error_to_zero():
    record = _attack(_record(), error=-MAX, tolerance=0.01, purpose=sc.DEFECT_CEILING)
    verdict = sc.evaluate(CASE, record)
    assert verdict["state"] == sc.PASS
    assert verdict["properties"]["attack"] == 0.0
    assert verdict["worst"] == pytest.approx(0.10 / 2.1655)


# ---- the board's own CLI ----------------------------------------------------

MALFORMED_ROWS = {
    "metric-not-object": lambda r: r["metrics"].__setitem__("attack", 5),
    "metrics-not-object": lambda r: r.__setitem__("metrics", 5),
    "provenance-not-object": lambda r: r.__setitem__("provenance", 5),
    "int-beyond-float": lambda r: r["metrics"]["attack"].update(error=10**400),
    "distance-overflows": lambda r: r["metrics"]["attack"].update(error=MAX, tolerance=0.01),
}


def _board(monkeypatch, tmp_path, record):
    """`tools/scorecard.py --verbose --check --markdown ...` over one case,
    read from a results directory of the caller's making."""
    (tmp_path / "D02A.json").write_text(json.dumps(record))
    monkeypatch.setattr(sc, "ROOT", tmp_path)
    monkeypatch.setattr(sc, "RESULTS", tmp_path)
    monkeypatch.setattr(sc, "load_cases", lambda: [dict(CASE)])
    monkeypatch.setattr(sc, "checkout_staleness", lambda: None)
    board = tmp_path / "BOARD.md"
    monkeypatch.setattr(sys, "argv",
                        ["scorecard.py", "--verbose", "--check", "--markdown", str(board)])
    rc = sc.main()
    return rc, board


@pytest.mark.parametrize("break_record", MALFORMED_ROWS.values(), ids=MALFORMED_ROWS.keys())
def test_the_cli_renders_a_malformed_record_as_no_verdict_instead_of_raising(
        monkeypatch, tmp_path, capsys, break_record):
    record = _record()
    break_record(record)
    rc, board = _board(monkeypatch, tmp_path, record)
    assert rc == 0
    line = next(text for text in capsys.readouterr().out.splitlines()
                if text.startswith("D02A"))
    assert "no verdict" in line
    assert "inf" not in line.split()
    assert "no verdict" in board.read_text()

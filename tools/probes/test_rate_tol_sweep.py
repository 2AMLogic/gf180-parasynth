#!/usr/bin/env python3
"""Validation cases for tools/probes/rate_tol_sweep.py -- #775.

A sweep instrument is only evidence if its arithmetic is the arithmetic the
test it is pricing actually applies, and if it refuses rather than answers
when it cannot. So:

  * THE CASE THAT MATTERS MOST -- `red()`'s re-evaluated counts are identical,
    pair for pair, to what `_rate_gaps(tol_rel=...)` itself returns on the
    shipped panel. The instrument measures each clip once and re-runs only the
    comparison, which is what makes a seven-point grid cost the same as one
    point; the thing that could go wrong is that the re-run comparison drifts
    from the shipped one, and this is the case that would catch it.

  * AN ANSWER THIS MODULE DID NOT PRODUCE -- `red()` and `crossing()` on
    hand-built pairs whose verdict is arithmetic (`gap > max(tol_rel * peak,
    floor)`), including both sides of the strict inequality.

  * THE REFUSALS -- an uncommitted or dirty grid, and a panel entry whose own
    precondition fails, must REFUSE and not report.

    python3 -m pytest tools/probes/test_rate_tol_sweep.py -q
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rate_tol_sweep as S  # noqa: E402
import sensitivity as sens  # noqa: E402

from model import discrimination_features as D  # noqa: E402

TWO_F0 = (440.0, 660.0)


def pairs(**kw):
    """One hand-built (entry, column) pair in the shape `red()` reads."""
    row = {"label": "x", "column": "jit.cyclecorr.seg0", "unit": "ratio",
           "gap": 0.0, "peak": 1.0, "refused": False}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
# the comparison is the shipped comparison


def test_the_reevaluated_counts_match_rate_gaps_itself():
    """THE VALIDATION CASE. `measure_panel` + `red` must agree with
    `_rate_gaps(tol_rel=...)` on the same panel -- the same (entry, column)
    pairs, not merely the same count -- or the sweep is pricing a comparison
    the test does not apply.

    Run on the shipped eleven-entry RATE_PANEL at three values of the dial
    including the shipped one, which is about twenty seconds and is the whole
    reason to trust every other number this instrument prints. The shipped
    value is the per-unit dict since #782, so the first pass exercises the
    dict path through both sides; the scalars are #775's dial, which overrides
    every unit at once.
    """
    rows = S.measure_panel(D.RATE_PANEL, None)
    for tol_rel in (D.RATE_TOL_REL, 0.03, 0.0):
        mine = {(r["label"], r["column"]) for r in S.red(rows, tol_rel)}
        theirs = {(label, nm) for label, nm, _, _, _ in D._rate_gaps(tol_rel=tol_rel)}
        assert mine == theirs, (
            f"at tol_rel={tol_rel}: only the sweep says "
            f"{sorted(mine - theirs)[:4]}, only _rate_gaps says "
            f"{sorted(theirs - mine)[:4]}")


def test_the_reevaluated_counts_match_rate_gaps_through_a_per_unit_override():
    """The `--unit db` mode's comparison is also the shipped comparison: a
    `{"db": value}` override through `red` must return exactly the pairs
    `_rate_gaps(tol_rel={"db": value})` returns, with the other four units at
    their shipped terms on both sides. This is the case that would catch the
    per-unit merge drifting from what the test applies."""
    rows = S.measure_panel(D.RATE_PANEL, None)
    for override in ({"db": 0.057}, {"db": 0.09}, {"db": 0.0}, {"ms": 0.3}):
        mine = {(r["label"], r["column"]) for r in S.red(rows, override)}
        theirs = {(label, nm) for label, nm, _, _, _ in D._rate_gaps(tol_rel=override)}
        assert mine == theirs, (
            f"at tol_rel={override}: only the sweep says "
            f"{sorted(mine - theirs)[:4]}, only _rate_gaps says "
            f"{sorted(theirs - mine)[:4]}")


def test_a_per_unit_override_leaves_other_units_at_their_shipped_terms():
    """The override merges over the shipped values, it does not replace them:
    moving the dB term alone must leave a ratio column's tolerance at its
    shipped value, and vice versa. A `--unit db` sweep that leaked into other
    units would attribute their movement to the dB dial."""
    col_ratio, col_db = "jit.cyclecorr.seg0", "cqt3225Hz.seg0"
    base_r = D.rate_tolerance(col_ratio, 1.0, 0.0)
    base_d = D.rate_tolerance(col_db, 80.0, 0.0)
    # 0.12 is above the shipped 0.1 on the committed grid, so the first
    # assertion loosens rather than tightens whatever the shipped value is
    assert D.rate_tolerance(col_ratio, 1.0, 0.0, {"db": 0.12}) == base_r
    assert D.rate_tolerance(col_db, 80.0, 0.0, {"db": 0.12}) > base_d
    assert D.rate_tolerance(col_db, 80.0, 0.0, {"ratio": 0.12}) == base_d
    # and the shipped comparison itself is unchanged by the machinery
    assert D.rate_tolerance(col_ratio, 1.0, 0.0) == base_r


def test_a_per_unit_override_naming_an_unknown_unit_refuses():
    """A typo'd unit key (`{"Db": 0.1}`) would silently override nothing and
    the sweep would price the shipped dial while believing it had moved one.
    It must refuse rather than answer."""
    with pytest.raises(ValueError, match="names unit"):
        D.rate_tolerance("cqt3225Hz.seg0", 80.0, 0.0, {"Db": 0.1})


def test_the_dial_changes_only_the_comparison_not_the_measurement():
    """`measure_panel` takes no tol_rel, and must not: the gaps and peaks are
    a property of the clips and the fold, and a sweep whose stimulus moved
    with its dial would be two dials."""
    import inspect
    a = S.measure_panel(D.RATE_PANEL[:2], None)
    b = S.measure_panel(D.RATE_PANEL[:2], None)
    assert [r["gap"] for r in a] == [r["gap"] for r in b]
    assert list(inspect.signature(S.measure_panel).parameters) == ["panel", "taps"]


# ---------------------------------------------------------------------------
# answers from arithmetic, not from this module


def test_red_is_the_strict_inequality_on_both_sides():
    floor = D.RATE_TOL_ABS["ratio"]
    # peak 1.0, so the relative term is tol_rel itself and the floor is 0.02
    assert S.red([pairs(gap=0.05)], 0.0499) == [pairs(gap=0.05)]
    assert S.red([pairs(gap=0.05)], 0.05) == []        # not strictly greater
    assert S.red([pairs(gap=0.05)], 0.0501) == []
    # and the floor is the other leg: below floor/peak the dial is inert
    assert S.red([pairs(gap=floor)], 0.0) == []
    assert S.red([pairs(gap=floor * 1.001)], 0.0) != []
    assert S.red([pairs(gap=floor * 1.001)], 0.019) != []


def test_a_gap_under_its_own_floor_is_unreachable_at_every_value():
    """The finding that decides #775's third acceptance criterion, as
    arithmetic: `max()` means no value of the dial can reach a pair whose gap
    is already inside its unit's absolute floor."""
    floor = D.RATE_TOL_ABS["ratio"]
    r = pairs(gap=floor * 0.9)
    assert S.crossing(r) == -1.0
    assert all(S.red([r], t) == [] for t in (0.0, 1e-9, 0.02, 0.12, 1.0))
    reachable = pairs(gap=0.3, peak=1.0)
    assert S.crossing(reachable) == pytest.approx(0.3)
    assert S.red([reachable], 0.2999) and not S.red([reachable], 0.3001)


def test_refused_pairs_are_in_no_count_and_in_no_table():
    """A #774 octave-error row has the largest crossing value on the panel and
    would head the reach table while saying nothing about this dial."""
    r = pairs(gap=0.9, refused=True)
    assert S.red([r], 0.0) == []
    res = S.measure([0.0, 0.12], D.RATE_PANEL[:1], panel_11=False)
    assert all(not x["refused"] for x in res["reach"])


def test_the_counts_are_monotone_in_the_dial():
    """`tol = max(tol_rel * peak, floor)` is non-decreasing in tol_rel, so
    both counts must be non-increasing. If this failed, the record's runs
    would not be intervals and `tools/sensitivity.runs` would be the wrong
    reader for the artefact."""
    res = S.measure(S.load_plan(allow_dirty=True)["_grid"],
                    S.wide_panel(TWO_F0), panel_11=False)
    caught = [r["caught"] for r in res["rows"]]
    cost = [r["cost"] for r in res["rows"]]
    assert caught == sorted(caught, reverse=True), caught
    assert cost == sorted(cost, reverse=True), cost
    assert caught[0] >= caught[-1] > 0, "the injected fold must be red somewhere"


def test_the_per_unit_counts_are_monotone_in_the_dial():
    """Same argument, per-unit dial (#782): raising one unit's relative term
    can only loosen that unit's tolerance, so every count is non-increasing.
    If caught_db rose anywhere, the `{"db": value}` override leaked into
    another unit and the sweep is not pricing one dial."""
    unit = "db"
    res = S.measure([0.0, 0.02, 0.09], S.wide_panel(TWO_F0),
                    panel_11=False, unit=unit)
    caught = [r["caught"] for r in res["rows"]]
    caught_unit = [r[f"caught_{unit}"] for r in res["rows"]]
    cost = [r["cost"] for r in res["rows"]]
    assert caught == sorted(caught, reverse=True), caught
    assert caught_unit == sorted(caught_unit, reverse=True), caught_unit
    assert cost == sorted(cost, reverse=True), cost
    assert caught_unit[0] > 0, "the injected fold must be red in the swept unit"


@pytest.mark.parametrize("unit", ["ms", "ratio", "count"])
def test_a_non_db_unit_names_its_own_columns_not_the_dbs(unit):
    """The swept unit names its own columns. #937: `measure` and `report`
    hardcoded the db names `caught_db`/`cost_db` for whatever unit was
    swept, so `--unit ms` printed a table whose columns claimed to be dB
    counts -- the apparatus answering in a vocabulary it was not asked, which
    is exactly how a mislabelled instrument ships: the db run was doubly
    lucky, its literal names happened to be right, so nothing failed. These
    three units each break differently: ms is where the literal and the unit
    disagree; ratio is where the per-unit columns duplicate the
    always-present correlation-family pair (`caught_ratio`/`cost_ratio`),
    so the header carries a name twice; count is where the name does not
    even fit the fixed width, so the rows stop tokenising. Asserted through
    `sens.parse_fixed_width_tables` -- the real consumer of the artefact --
    not a substring of the header."""
    res = S.measure([0.0, 0.12], S.wide_panel(TWO_F0), panel_11=False,
                    unit=unit)
    row = res["rows"][0]
    assert f"caught_{unit}" in row and f"cost_{unit}" in row, sorted(row)
    assert "caught_db" not in row and "cost_db" not in row, sorted(row)
    out = io.StringIO()
    S.report(res, S.load_plan(allow_dirty=True, path=S.PLAN_BY_UNIT), out)
    tables = sens.parse_fixed_width_tables(out.getvalue())
    rows = tables[f"rate_tol_rel_{unit}"]
    assert len(rows) == len(res["rows"]), sorted(tables)
    assert f"caught_{unit}" in rows[0], rows[0]
    assert "caught_db" not in rows[0] and "cost_db" not in rows[0], rows[0]


def test_the_integer_fold_is_caught_more_than_the_shipped_one_everywhere():
    """The benefit column is only a benefit if the injected defect is redder
    than the clean panel at every value. A dial where the two crossed would be
    measuring something other than rate dependence."""
    res = S.measure([0.0, 0.03, 0.12], S.wide_panel(TWO_F0), panel_11=False)
    for row in res["rows"]:
        assert row["caught"] > row["cost"], row


# ---------------------------------------------------------------------------
# the margin, and the reason a margin test cannot exist for this dial


def test_the_absolute_floor_is_above_the_cyclecorr_effect():
    """What makes #775's third acceptance criterion fall on its second branch:
    the smallest tolerance this dial can produce for `jit.cyclecorr.seg0` is
    `RATE_TOL_ABS['ratio']`, and that is already above the whole effect the
    column reports. Measured here, asserted in
    `test_no_relative_tolerance_can_reach_the_cyclecorr_effect`."""
    m = S.cyclecorr_margin()
    assert m["tol_floor_only"] == D.RATE_TOL_ABS["ratio"]
    assert m["floor_over_effect"] > 1.0, m
    assert m["at_012_over_effect"] > m["floor_over_effect"], m
    assert m["effect_over_gap"] > 10.0, (
        "the column's own rate reproducibility is not far below its effect, "
        "so the effect is not the thing a margin should be stated against")


# ---------------------------------------------------------------------------
# the refusals


def test_an_uncommitted_grid_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "PLAN", tmp_path / "nope.json")
    with pytest.raises(S.Refused, match="no plan at|state the grid first"):
        S.load_plan()


def test_a_dirty_grid_refuses(tmp_path, monkeypatch):
    """The temporal proof, and it is the whole reason this file can be trusted
    as a sweep rather than as a measurement with a grid drawn round it
    afterwards. `tools/sweep_m1a_attack.py` refuses the same way."""
    original = S.PLAN.read_bytes()
    rel = str(S.PLAN.relative_to(S.ROOT))
    if subprocess.check_output(["git", "status", "--porcelain", "--", rel],
                               cwd=S.ROOT, text=True).strip():
        pytest.skip(f"{rel} is already dirty in this tree; nothing to prove")
    try:
        # same content, different bytes -- a reformat is still a dirty grid
        S.PLAN.write_bytes(original + b"\n")
        assert subprocess.check_output(["git", "status", "--porcelain", "--", rel],
                                       cwd=S.ROOT, text=True).strip()
        with pytest.raises(S.Refused, match="dirty"):
            S.load_plan()
        assert S.load_plan(allow_dirty=True)["_dirty"] is True
    finally:
        S.PLAN.write_bytes(original)           # never `git checkout --`: that
        # would discard a developer's in-progress edit to the grid instead of
        # restoring exactly what this test changed


def test_a_grid_out_of_order_refuses(tmp_path, monkeypatch):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"schema": "sensitivity-plan-v1",
                             "grid": [0.12, 0.0, 0.03]}))
    monkeypatch.setattr(S, "PLAN", p)
    with pytest.raises(S.Refused, match="strictly increasing"):
        S.load_plan(allow_dirty=True)


def test_a_panel_entry_whose_precondition_fails_refuses_the_sweep():
    """A jittered entry the generator rounded back to jitterless is a positive
    control in name only, and `_rate_pair` asserts so. The sweep must turn
    that into a REFUSAL rather than a quietly dropped row."""
    bad = (("fake jitter", 330.0, 1e-9, 3, "square", 44100),)
    with pytest.raises(S.Refused, match="rounded the jitter away"):
        S.measure_panel(bad, None)


# ---------------------------------------------------------------------------
# the artefact has to be readable by the gate that will cite it


def test_numbers_are_written_in_the_only_form_the_gate_can_read():
    """`tools/sensitivity.parse_fixed_width_tables` matches
    `-?\\d+(?:\\.\\d+)?` and SKIPS anything else, so a row printed as `1e-05`
    is not a bad row -- it is an invisible one, and the record would then be
    missing a grid point the gate says it holds. Checked against that
    module's own pattern, not a copy of it."""
    for v in (0.0, 1e-05, 0.00625, 0.0146, 0.12, 102.0, 1234567.0, -0.5):
        assert sens.NUMBER.match(S._num(v)), (v, S._num(v))
        assert float(S._num(v)) == pytest.approx(v, rel=1e-9, abs=1e-12)


def test_the_artefact_the_record_cites_parses_and_holds_the_whole_grid():
    """The record this sweep exists for cites a committed artefact; a grid
    point missing from it is how a plateau gets manufactured. Reads the
    committed artefact if it is there, and says plainly that it is not yet
    when the sweep has not been run."""
    art = ROOT / "docs/sensitivity/rate-tol-rel-sweep.txt"
    if not art.exists():
        pytest.skip("the sweep has not been run yet; the record cannot exist either")
    tables = sens.parse_fixed_width_tables(art.read_text())
    grid = [float(v) for v in json.loads(S.PLAN.read_text())["grid"]]
    assert "rate_tol_rel" in tables
    assert sorted(r["tol_rel"] for r in tables["rate_tol_rel"]) == grid
    for name in ("rate_tol_rel_cost_by_unit", "rate_tol_rel_shipped_panel",
                 "rate_tol_rel_cyclecorr_margin"):
        assert tables.get(name), f"{name} is missing from the artefact"


# ---- a plan missing the key load_plan() reads with [] is REFUSED (exit 2),
# not a traceback (#1604, #1623), through the tool's own main(). The refusal
# precedes the measurement, so the sweep never starts.
def _main_over(tmp_path, monkeypatch, capsys, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    monkeypatch.setattr(S, "PLAN", p)
    monkeypatch.setattr(S, "measure", lambda *a, **k: pytest.fail("a partial plan was measured"))
    rc = S.main(["--allow-dirty-plan"])
    return rc, capsys.readouterr().err


def test_a_plan_without_grid_is_refused(tmp_path, monkeypatch, capsys):
    rc, err = _main_over(tmp_path, monkeypatch, capsys, {"schema": "sensitivity-plan-v1"})
    assert rc == 2 and "REFUSED" in err and "'grid'" in err


def test_a_plan_with_null_grid_is_refused(tmp_path, monkeypatch, capsys):
    rc, err = _main_over(tmp_path, monkeypatch, capsys,
                         {"schema": "sensitivity-plan-v1", "grid": None})
    assert rc == 2 and "REFUSED" in err and "'grid'" in err


def test_a_plan_that_is_a_json_list_is_refused_not_an_attribute_error(tmp_path, monkeypatch, capsys):
    rc, err = _main_over(tmp_path, monkeypatch, capsys, [1, 2, 3])
    assert rc == 2 and "REFUSED" in err and "not a JSON object" in err

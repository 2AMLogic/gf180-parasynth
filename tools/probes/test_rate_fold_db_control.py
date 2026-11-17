#!/usr/bin/env python3
"""Validation cases for tools/probes/rate_fold_db_control.py -- #935.

The probe answers whether a dB `must` row is constructible for the rate
control's injected-bug test at the shipped licence. That answer is only
evidence if the instrument measures the comparison that ships, refuses rather
than answers when its preconditions fail, and writes a record the gate can
re-read. So:

  * THE CASE THAT MATTERS MOST -- the probe's red set on BOTH folds is
    `_rate_gaps`'s red set, pair for pair, on the shipped panel. The runtime
    precondition inside `measure()` asserts the same thing on every real run;
    this is the standalone form a future edit that breaks the tie can be
    seen by.

  * `_must_rows`'s verdict arithmetic on hand-built pairs -- red on the
    integer fold and green on the shipped one is a row; red on both folds is
    not (it is the suite failing); a non-dB unit is never a row here.

  * THE REFUSALS -- a non-empty RATE_KNOWN_RED, which would let a quarantine
    entry absorb a pair and turn the clean-panel cost reading into a pass.

  * THE ARTEFACT -- the committed record parses through the sensitivity
    gate's own parser and holds every point of the reused committed grid.

    python3 -m pytest tools/probes/test_rate_fold_db_control.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import rate_fold_db_control as C  # noqa: E402
import rate_tol_sweep as S  # noqa: E402
import sensitivity as sens  # noqa: E402

from model import discrimination_features as D  # noqa: E402

ART = ROOT / "docs/sensitivity/rate-fold-db-control.txt"
PLAN = ROOT / "docs/sensitivity/rate-tol-rel-by-unit-plan.json"


def pair(label, column, unit, gap, peak=1.0, refused=False):
    """One hand-built (entry, column) row in the shape the probe reads."""
    return {"label": label, "column": column, "unit": unit,
            "gap": gap, "peak": peak, "refused": refused}


# ---------------------------------------------------------------------------
# the comparison is the shipped comparison, on both folds


def test_the_probe_red_set_matches_rate_gaps_on_both_folds():
    """THE VALIDATION CASE, standalone. `measure()` refuses if its own red
    set drifts from `_rate_gaps`'s; this asserts the same tie directly, so a
    regression in the tie is visible even if nothing ever runs the probe.
    About half a minute: the whole reason to trust the artefact's counts."""
    injected = S.measure_panel(D.RATE_PANEL, -1)
    shipped = S.measure_panel(D.RATE_PANEL, None)
    C._tie_to_rate_gaps(injected, shipped)  # raises Refused on any drift


def test_the_tie_refuses_when_the_red_set_drifts(monkeypatch):
    """And the refusal fires: a probe whose red set is not `_rate_gaps`'s is
    measuring a comparison the shipped test does not apply, and must say so
    rather than print numbers. `_rate_gaps` is pinned to a one-pair answer so
    the hand-broken side is deterministic -- the probe's rows carry the pair,
    `_rate_gaps` is asked for the same pair and finds none."""
    theirs = [("220Hz", "cqt3225Hz.seg0", 0.0, 0.0, 0.0)]
    monkeypatch.setattr(D, "_rate_gaps", lambda taps=None: list(theirs))
    rows = [pair("220Hz", "cqt3225Hz.seg0", "db", 9.0, peak=80.0)]
    assert {(r["label"], r["column"]) for r in S.red(rows, None)} == {
        ("220Hz", "cqt3225Hz.seg0")}
    C._tie_to_rate_gaps(rows, rows)          # identical sets: no refusal
    with pytest.raises(S.Refused, match="drifted from"):
        C._tie_to_rate_gaps([], rows)        # the probe lost the pair


# ---------------------------------------------------------------------------
# the verdict arithmetic, on hand-built pairs


def test_a_must_row_is_red_on_the_integer_fold_and_green_on_the_shipped_one():
    """A dB pair the integer fold turns red and the shipped fold leaves green
    is exactly what a `must` row asserts. peak 80 dB is the floor_db clamp:
    at {"db": 0.1} the tolerance there is 8.0, so gap 9 is red and gap 7 is
    green -- both sides of the shipped licence in one pair."""
    injected = [pair("220Hz", "cqt3225Hz.seg0", "db", 9.0, peak=80.0)]
    shipped = [pair("220Hz", "cqt3225Hz.seg0", "db", 7.0, peak=80.0)]
    must = C._must_rows(injected, shipped, {"db": 0.1})
    assert [(r["label"], r["column"]) for r in must] == [
        ("220Hz", "cqt3225Hz.seg0")]


def test_a_pair_red_on_both_folds_is_not_a_must_row():
    """Red on the shipped fold too is the SUITE failing -- a quarantine
    candidate, not a control of the injected defect -- and the verdict must
    not offer it as a row. This is the input that defeats the naive reading
    of `db_red_injected` alone."""
    rows = [pair("220Hz", "cqt3225Hz.seg0", "db", 9.0, peak=80.0)]
    assert C._must_rows(rows, list(rows), {"db": 0.1}) == []


def test_a_non_db_unit_is_never_a_must_row():
    """The question is about the dB family; a ratio pair red only on the
    integer fold is the control's existing business (and `must` already
    carries twelve of them). `_must_rows` filters on the unit, so a wide
    integer-fold failure in another unit cannot be reported as a dB row."""
    injected = [pair("220Hz", "jit.cyclecorr.seg0", "ratio", 0.05, peak=1.0)]
    shipped = [pair("220Hz", "jit.cyclecorr.seg0", "ratio", 0.0, peak=1.0)]
    assert C._must_rows(injected, shipped, {"db": 0.1}) == []


def test_a_refused_pair_is_in_no_count():
    """A #774 octave-refused row has a huge gap and would head any table it
    reached; `S.red` already drops it, asserted here rather than assumed."""
    rows = [pair("220Hz", "cqt3225Hz.seg0", "db", 9.0, peak=80.0,
                 refused=True)]
    assert S.red(rows, {"db": 0.1}, "db") == []


# ---------------------------------------------------------------------------
# the refusals


def test_a_non_empty_quarantine_refuses(monkeypatch):
    """`RATE_KNOWN_RED` non-empty means a pair can be absorbed into a pass,
    which is exactly the reading the verdict's "green on the shipped fold"
    half needs to be false. Refuse rather than measure. Must fire before any
    panel is walked: a refusal that costs thirty seconds is a slow answer,
    not a refusal."""
    monkeypatch.setattr(D, "RATE_KNOWN_RED",
                        {("220Hz", "cqt3225Hz.seg0"):
                         (7.1, "a quarantine entry with a stated mechanism #0")})
    with pytest.raises(S.Refused, match="non-empty"):
        C.measure([0.1])


def test_measures_clean_and_reports_the_shipped_dial():
    """The clean-tree path runs end to end and reports the shipped dial it
    read, so an artefact cannot silently describe a dial other than the one
    the tree ships. Runs the real panel on both folds -- the probe's own
    cost, paid here once."""
    res = C.measure(sorted(float(v) for v in
                           json.loads(PLAN.read_text())["grid"]))
    assert res["rel_db_shipped"] == D.RATE_TOL_REL["db"]
    assert [r["rel_db"] for r in res["grid"]] == \
        sorted(float(v) for v in json.loads(PLAN.read_text())["grid"])


# ---------------------------------------------------------------------------
# the artefact has to be readable by the gate that will cite it


def test_the_artefact_the_record_cites_parses_and_holds_the_whole_grid():
    """The comment in `discrimination_features.py` cites a committed
    artefact; a grid point missing from it is how a finding gets
    manufactured. Reads the committed artefact if it is there, and says
    plainly that it is not yet when the probe has not been run."""
    if not ART.exists():
        pytest.skip("the probe has not been run yet; the record cannot exist either")
    tables = sens.parse_fixed_width_tables(ART.read_text())
    grid = sorted(float(v) for v in json.loads(PLAN.read_text())["grid"])
    assert "rate_fold_db_grid" in tables
    assert sorted(r["rel_db"] for r in tables["rate_fold_db_grid"]) == grid
    for name in ("cqt_clamp_tolerance", "rate_fold_injected_by_unit"):
        assert tables.get(name), f"{name} is missing from the artefact"


def test_the_figures_table_holds_the_numbers_the_issue_quotes():
    """The issue body's 8.0 / 1.0 dB, re-measured: a cqt bin at the floor_db
    clamp (peak 80) and one 10 dB down, at the shipped dial, through
    `rate_tolerance` itself. If the shipped dial moves, this test's numbers
    move with it BY ARITHMETIC -- max(rel * peak, floor) -- not by a second
    copy of the expression."""
    rows = {r["peak_db"]: r for r in C.figures()}
    floor = D.RATE_TOL_ABS["db"]
    for peak, expect in rows.items():
        assert expect["tol_shipped"] == D.rate_tolerance(
            "cqt3225Hz.seg0", peak, 0.0), (peak, expect)
        assert expect["tol_floor_only"] == floor, (peak, expect)
    assert rows[80.0]["tol_shipped"] > rows[10.0]["tol_shipped"], (
        "the licence no longer grows with distance from 0 dB; #782's fifth "
        "limit and #935's premise have both moved and the record needs rewriting")

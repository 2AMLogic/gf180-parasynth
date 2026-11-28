#!/usr/bin/env python3
"""#2141: the INJECTED DEFECTS block of `compare_ladder_candidates` printed
`moved_cents=nan` when a side of the move had refused every point.
red-first on origin/main @ fb778dd2, 2026-11-28.

THE DEFECT, MEASURED on the unfixed tree through the tool's real `__main__`,
with every ladder silenced (`TOTAL_SILENT` below, PR #2079's seam) and
`--quick --only shipped`:

    INJECTED DEFECTS -- each must move its own dimension:
      dropped-pole           (linear/resonance  ) moved_refused_points=4/4, baseline_refused_points=4/4
      cutoff-skew-6pct       (linear            ) moved_cents=nan
      tanh-4-entries         (drive             ) moved_saturation_db=None, moved_inharmonic_db=None
      control-quantum-32hz   (movement          ) moved_residual_db=nan

and the same two fields as `NaN` in `--json`'s `controls.injected`. Until this
run they were PR #2079's Builder's report; the issue and its curation both say
so. The mechanism is the one they suspected, and is measured here as one: the
move is `abs(defective - baseline)` over two stage summaries, a summary is `nan`
when every one of its points refused (`max(..., default=float("nan"))`), and
`nan` passes through a subtraction without a word.

WHAT THE RUN ADDED TO THE REPORT

  * `dropped-pole` printed no `nan` under that seam, and does not on real input
    either: its defective run refuses at EVERY point -- 4/4 on the quick grid,
    8/8 on the full one, as `docs/ladder-rungs-2-4-results.json` records -- and
    its guard omitted the move whenever that side refused. The BASELINE side
    had no guard, so a baseline refusing every point under a scoring defect
    printed `moved_peak_db=nan`. That is the input verification-rules rule 8
    asks for at write time and `test_a_dead_baseline_...` is its control.
  * a finite summary over all-refused points was printed as a measurement
    (`moved_peak_db=6.2`). No stage writes one, but an operand-only fix would
    keep printing it, which is why the fix counts points as well.
  * the healthy numbers, on the unfixed tree, identical on the quick and the
    full grid: cutoff-skew 72.2 cents, control-quantum 24.51 dB, tanh 4.22 dBFS
    and 4.6 dB. The fix must not move them (`test_the_real_controls_...`).
  * nine further non-finite values under `controls.start_red` and
    `controls.baseline`. Those are the STAGES' own `nan` summaries, not this
    block: the committed healthy record holds five of them too. #2036 put
    stage-level rendering out of scope, so they are not touched here.

WHAT THE FIX DOES. One helper, `_moved`, builds each of the three moves that
subtract a stage summary. It refuses -- the field reads `REFUSED` -- when
either side refused every point OR either summary is not a finite number, and
otherwise returns the `round(abs(a - b), n)` the sites always computed. Both
signals are read because each can fail where the other is blind: the summary
alone is defeated by a finite figure over all-refused points
(`test_a_summary_that_contradicts_its_own_points...`), and a refusal count
alone cannot see a stage that scores a `nan`. A side with no operating points
at all counts as one that refused every point
(`test_a_side_with_no_operating_points_at_all...`): it scored nothing, and
over an empty grid even `0.0` is not a measurement. Every move now also carries
`moved_refused_points` and `baseline_refused_points`, so the two directions
-- defect dead over a scoring baseline, and the reverse -- do not read alike.

RED, THEN GREEN, this file whole against each tree. Unfixed: 17 failed, 4
passed. Fixed: 21 passed. The four are green on both trees by construction,
which is what lets them speak: the two number controls
(`...keep_the_numbers_the_subtraction_gave`, `...partially...is_still_a_measurement`),
the one that tells the two directions apart, and the block check's own control.
The seventeen, on the unfixed tree:

    both sides dead, end to end   moved_cents=nan, moved_residual_db=nan, and
                                  dropped-pole's two moves absent altogether
    both sides dead, in process   the same
    dead baseline, scoring defect moved_peak_db=nan, moved_cents=nan from
                                  dropped-pole -- the old guard's blind side
    dead defect, scoring baseline the same nan in the other two controls
    a summary over no points      moved_peak_db=6.2 printed as a measurement
    a side with no points         nan printed as the move
    operand nan / inf / -inf      printed as the move
    operand None / a str          TypeError: a traceback where a refusal belongs
    operand a bool                37.7 printed as the move
    the real block, read anew     dropped-pole carries no move field at all
    the three rule-5 controls     red because their baseline is red (#1125)

AND THE HEALTHY RUNS DID NOT MOVE. The whole `controls` dict of a healthy run,
before against after, field by field, measured by
`tools/probes/ladder_controls_before_after.py` (one pass of the real stages;
their rows replayed into the unfixed assembly): quick grid 98 of 98 identical,
full grid 204 of 204, zero changed and zero lost, six fields added in each
(dropped-pole's two moves, now `REFUSED`, and the two counts on each of the
other controls). Under the seam, whole quick report against whole quick report,
two EXISTING leaves change -- the two `nan` fields, now `REFUSED` -- the same
six are added, and the other 179 are identical.

WRONG-THEN-RIGHT, six in this file's own apparatus. The first five were caught
by a run disagreeing with what reading had predicted, none by inspection; the
sixth by reading the helper against its own docstring, and then measured
before it was touched:

  1. Two `assert`s carried their message on the next line without parentheses:
     a SyntaxError, caught by compiling before the first run. The class was
     then grepped for, not the two sites.
  2. The partial-refusal control was RED on the unfixed tree, which a control
     meant to stay green must never be: its expected value assumed the router
     hands back the skew row (it expected 28.8 where the tool rightly gave
     4.6). The counts test split off from it then asked for a movement row that
     is partially refused, which does not exist -- the 100 Hz refusal is a
     linear-stage fact.
  3. "No `nan` in the block" was a substring test, and `nan` sits inside
     "reso-nan-ce": the dropped-pole control's own dimension label. Four
     correct blocks were reported as failing. A whole-token match now, with
     `test_the_block_check_refuses_a_nan_and_not_the_word_resonance` as its control.
  4. The movement counts were expected as `/1`; the stage runs two sweeps.
  5. The rule-5 control credits a red only when the named assertion's message
     is the one that failed. The summary-over-no-points test failed on another
     message, and the control said so -- "Regex pattern did not match" -- rather
     than count it. The test now fails on the shared one.
  6. The fix as first written skipped the count for a side with no operating
      points (`row[rows] and ...`), while its own docstring said a summary could
      be finite over nothing. Reading had taken the line for tighter than it
      was. Asked directly -- the real stage over an empty grid, a finite summary
      put on the row -- it printed a move of `0.0`: "the defect moved nothing".
      `test_a_side_with_no_operating_points_at_all...` is red on that tree, and
      `NO_POINTS_EXEMPT` keeps it there.

WHAT IS STUBBED, AND WHAT IS NOT

  in process   `stage_controls` and `_print`, the producer and the renderer of
               the block, over rows the REAL stages wrote. `_real()` runs the
               real `stage_controls` once on a one-point grid and six single
               stages beside it; `_wire` then answers the stage calls with
               those rows, so each side of each move is made dead, partially
               dead, or lying, independently and in milliseconds. No row here
               is a hand-drawn picture of a stage, and `_real()` asserts its
               own preconditions before any test reads it.
  end to end   the real stages, the real cores, the real `__main__` (runpy, so
               argv is a direct invocation's). The seam WRAPS `process` and
               zeroes its output, never replaces it. The test reads the seam's
               precondition back off the report rather than trusting it.

Nothing here asserts on the verdict or the exit status: under this seam the
unfixed tree prints RETAIN at exit 0, which is #2036's defect and PR #2079's
to fix. The two files must stay green whichever lands first.

THE RULE-5 CONTROL LIVES IN THIS FILE. `test_control_...` re-instates a
broken move in memory and calls the green tests by name: each must pass
unmutated first (a red before the mutation is that test's own, #1125) and
then fail on the assertion it was written for. Three ways the fix can be
undone: the subtraction as it shipped, the fix with its points never counted,
and the fix as it was first written, with an empty grid left out of the count.
It is not a `make controls`
harness because that edits the Makefile and
`tools/test_control_harness_baselines.py` at the lines open PR #2079 edits.
"""
from __future__ import annotations

import io
import json
import math
import pathlib
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
import compare_ladder_candidates as cc                             # noqa: E402

BASE = "shipped"
COMPARE = ROOT / "tools" / "compare_ladder_candidates.py"
CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "audition", "tools")]

#: reported as that when the seam has not taken: never a verdict about the tool
SEAM_FAILED = "SEAM FAILED"
WRAP = (
    "def _silence(cls):\n"
    "    _real = cls.process\n"
    "    def _process(self, x, *a, **kw):\n"
    "        _real(self, x, *a, **kw)\n"
    "        return np.zeros(len(x), dtype=np.int64)\n"
    "    cls.process = _process\n")
#: every ladder renders digital silence, on both sides of the bit-exactness
#: check, so `assert_apparatus` passes and the stages run
TOTAL_SILENT = (
    "import numpy as np, fixed, reference_rigs as rr, ladder_candidates as lc\n"
    "_c = lc.Candidate('shipped')\n"
    "_p = np.ones(4800)\n"
    "_ours = lambda: rr.OurLadder('ours')._render(_p, 1000.0, 0.9)\n"
    "assert np.any(_ours()) and np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder was silent before the fault'!r}\n"
    + WRAP +
    "for _k in (fixed.LadderFx, rr._Variant, lc.ShippedCore, lc.NewtonCore, lc.ExplicitDFCore):\n"
    "    _silence(_k)\n"
    "assert not np.any(_ours()) and not np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder still renders'!r}\n")

DROPPED, SKEW, TANH, QUANTUM = ("dropped-pole", "cutoff-skew-6pct", "tanh-4-entries",
                                "control-quantum-32hz")
#: the controls that subtract one stage summary from another, and the fields
#: that hold the move. `tanh-4-entries` subtracts too but reports `None` for an
#: unmeasured side, a convention #2141 leaves alone.
MOVES = {DROPPED: ("moved_peak_db", "moved_cents"), SKEW: ("moved_cents",),
         QUANTUM: ("moved_residual_db",)}
LINEAR_SUMMARIES = ("worst_cents", "worst_peak_db", "worst_pass_db")
REFUSED = "REFUSED"
#: the assertion every refusal test fails on, as a `match` for the rule-5 control
UNKNOWN = "an unknown rendered where a measurement belongs"
ABSENT = "<absent>"
nan = float("nan")


# ---------------------------------------------------------------------------
# what the block must and must not say
# ---------------------------------------------------------------------------
def _number(value) -> bool:
    """A finite number that is not a bool."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _moves(inj: dict) -> dict:
    """`control.field -> value` for every move the subtracting controls report. A
    field the control omitted reads `<absent>`, which is not a measurement."""
    return {f"{c}.{k}": inj[c].get(k, ABSENT) for c in MOVES for k in MOVES[c]}


def _assert_no_unknown(inj: dict) -> None:
    wrong = {k: v for k, v in _moves(inj).items() if not (v == REFUSED or _number(v))}
    assert not wrong, f"{UNKNOWN}: {wrong}"


def _assert_all_refused(inj: dict) -> None:
    _assert_no_unknown(inj)
    quiet = {k: v for k, v in _moves(inj).items() if v != REFUSED}
    assert not quiet, f"a side of every move refused every point; these do not say so: {quiet}"


def _assert_json_is_clean(inj: dict) -> None:
    text = json.dumps(inj)
    assert "NaN" not in text and "Infinity" not in text, text


def _injected_block(controls: dict) -> str:
    """The INJECTED DEFECTS block as the real `_print` renders it."""
    rep = dict(apparatus=dict(worst_db=0.0, oversample=2), candidates={},
               controls=controls, verdict=dict(text=""))
    out = io.StringIO()
    with redirect_stdout(out):
        cc._print(rep)
    _, found, tail = out.getvalue().partition("INJECTED DEFECTS")
    assert found, "the report carries no INJECTED DEFECTS block"
    return tail.split("\n\n", 1)[0]


def _assert_no_unknown_in_block(block: str) -> None:
    # a whole token: `nan` as a substring also sits inside "reso-nan-ce", the
    # dropped-pole control's own dimension label
    assert not re.search(r"\b(nan|inf)\b", block, re.I), block


def _assert_block_says_refused(block: str) -> None:
    _assert_no_unknown_in_block(block)
    for field in ("moved_peak_db=REFUSED", "moved_cents=REFUSED", "moved_residual_db=REFUSED"):
        assert field in block, (field, block)


def _controls_of(**injected: dict) -> dict:
    return dict(injected=injected, start_red={})


# ---------------------------------------------------------------------------
# rows the real stages wrote
# ---------------------------------------------------------------------------
CUTS, RES = (400.0,), (0.9,)
RING_CUTS, RING_RES = (400.0,), (1.05,)
OK, DEAD, PARTIAL = "ok", "dead", "partial"
_REAL: dict = {}


def _real() -> dict:
    """The control block the real `stage_controls` builds on a one-point grid,
    and the single-stage rows its moves are read from. Built once."""
    if not _REAL:
        R = _REAL
        R["controls"] = cc.stage_controls(CUTS, RES, RING_CUTS, RING_RES)
        R["linear"] = R["controls"]["baseline"]["linear"]
        R["linear_dead"] = R["controls"]["start_red"]["linear"]
        R["movement"] = cc.stage_movement([BASE])[BASE]
        R["movement_dead"] = R["controls"]["start_red"]["movement"]
        R["skew"] = cc.stage_linear([BASE], CUTS, RES, cut_skew=1.06)[BASE]
        R["quantum"] = cc.stage_movement([BASE], quantum=32)[BASE]
        R["drive"] = cc.stage_drive([BASE])[BASE]
        # 100 Hz is a point the real ladder refuses: its passband probe holds
        # too few periods of a 10 Hz tone (#1881), and the committed full-grid
        # record refuses both of its 100 Hz rows
        R["linear_partial"] = cc.stage_linear([BASE], (100.0, 400.0), (0.5,))[BASE]
        # the empty grid: the real stage called with no operating points at all,
        # which is the smallest "a summary over nothing"
        R["linear_empty"] = cc.stage_linear([BASE], (), ())[BASE]
        # the apparatus's own preconditions, before any test reads a row
        assert R["linear"]["refused"] == 0 and all(
            _number(R["linear"][k]) for k in LINEAR_SUMMARIES), R["linear"]
        row = R["linear_dead"]
        assert row["refused"] == len(row["points"]) > 0, row
        assert all("refused" in p for p in row["points"].values()), row
        assert all(math.isnan(row[k]) for k in LINEAR_SUMMARIES), row
        assert (R["movement"]["refused"] == 0
                and _number(R["movement"]["worst_residual_db"])), R["movement"]
        dead = R["movement_dead"]
        assert dead["refused"] == len(dead["sweeps"]) > 0, dead
        assert math.isnan(dead["worst_residual_db"]), dead
        part = R["linear_partial"]
        assert part["refused"] == 1 < len(part["points"]), part
        assert all(_number(part[k]) for k in LINEAR_SUMMARIES), part
        empty = R["linear_empty"]
        assert empty["points"] == {} and empty["refused"] == 0, empty
        assert all(math.isnan(empty[k]) for k in LINEAR_SUMMARIES), empty
    return _REAL


def _wire(monkeypatch, *, linear=None, movement=None) -> None:
    """Answer `stage_controls`' stage calls with rows the real stages wrote.

    `linear` and `movement` give `(defective side, baseline side)`. A name --
    "defective", "baseline", "both" or None -- picks a state; a tuple picks the
    rows, each a row key (OK, DEAD, PARTIAL) or an explicit dict. The start-red
    run is always the dead row: that is what the null core is."""
    R = _real()
    table = {"linear": {OK: R["linear"], DEAD: R["linear_dead"], PARTIAL: R["linear_partial"]},
             "movement": {OK: R["movement"], DEAD: R["movement_dead"]}}
    states = {"defective": (DEAD, OK), "baseline": (OK, DEAD), "both": (DEAD, DEAD),
              None: (OK, OK)}
    lin = states[linear] if linear is None or isinstance(linear, str) else linear
    mov = states[movement] if movement is None or isinstance(movement, str) else movement

    def row(stage, which):
        return table[stage][which] if isinstance(which, str) else which

    def linear_stage(names, cuts, resonances, **kw):
        return {names[0]: row("linear", lin[0 if names == ["null"] or kw else 1])}

    def movement_stage(names, quantum=1, **kw):
        return {names[0]: row("movement", mov[0 if names == ["null"] or quantum != 1 else 1])}

    def drive_stage(names, **kw):
        return {names[0]: R["drive"]}

    def not_under_test(names, *a, **kw):
        raise cc.Refused("a stub: this stage is not what #2141 is about")

    for name, fn in (("stage_linear", linear_stage), ("stage_movement", movement_stage),
                     ("stage_drive", drive_stage), ("stage_bass", not_under_test),
                     ("stage_cleanliness", not_under_test),
                     ("stage_resonance", lambda names, *a, **kw: {names[0]: dict(points={})})):
        monkeypatch.setattr(cc, name, fn)


def _controls(monkeypatch, **sides) -> dict:
    _wire(monkeypatch, **sides)
    return cc.stage_controls(CUTS, RES, RING_CUTS, RING_RES)


#: operating points each control's stage runs here: one linear point, and the
#: movement stage's two sweeps (`MOVE_SWEEPS`)
TOTAL = {DROPPED: 1, SKEW: 1, QUANTUM: len(cc.MOVE_SWEEPS)}


def _tallies(control: str, mine: int, theirs: int) -> tuple:
    """The `(defective, baseline)` refusal counts `control` carries when `mine`
    of its own points and `theirs` of the baseline's refused."""
    return f"{mine}/{TOTAL[control]}", f"{theirs}/{TOTAL[control]}"


# ---------------------------------------------------------------------------
# the refusal, in both directions
# ---------------------------------------------------------------------------
def test_every_point_refused_on_both_sides_reads_refused_not_nan(monkeypatch):
    """THE start-red control, in process: the reported defect's shape."""
    c = _controls(monkeypatch, linear="both", movement="both")
    inj = c["injected"]
    _assert_all_refused(inj)
    _assert_json_is_clean(inj)
    _assert_block_says_refused(_injected_block(c))
    for control in MOVES:
        got = (inj[control]["moved_refused_points"], inj[control]["baseline_refused_points"])
        assert got == _tallies(control, TOTAL[control], TOTAL[control]), (control, got)


def test_a_dead_baseline_under_a_scoring_defect_reads_refused(monkeypatch):
    """Rule 8's input for the guard that was there: the baseline refused every
    point and the defective run scored. `dropped-pole` covered only the
    opposite direction, so this printed `moved_peak_db=nan`."""
    c = _controls(monkeypatch, linear="baseline", movement="baseline")
    inj = c["injected"]
    _assert_all_refused(inj)
    _assert_block_says_refused(_injected_block(c))
    for control in MOVES:
        got = (inj[control]["moved_refused_points"], inj[control]["baseline_refused_points"])
        assert got == _tallies(control, 0, TOTAL[control]), (control, got)


def test_a_dead_defect_over_a_scoring_baseline_still_reads_red(monkeypatch):
    """The direction real input takes: a dropped pole refuses at every point.
    The control went red by refusing before and must still say so, now in the
    move fields as well as in the count."""
    c = _controls(monkeypatch, linear="defective", movement="defective")
    inj = c["injected"]
    _assert_all_refused(inj)
    _assert_block_says_refused(_injected_block(c))
    for control in MOVES:
        got = (inj[control]["moved_refused_points"], inj[control]["baseline_refused_points"])
        assert got == _tallies(control, TOTAL[control], 0), (control, got)


def test_the_two_directions_do_not_read_alike(monkeypatch):
    dead_defect = _controls(monkeypatch, linear="defective", movement="defective")["injected"]
    dead_baseline = _controls(monkeypatch, linear="baseline", movement="baseline")["injected"]
    for control in MOVES:
        assert dead_defect[control] != dead_baseline[control], control


def test_a_side_that_refused_only_some_points_is_still_a_measurement(monkeypatch):
    """The real full grid's shape -- the shipped baseline refuses 2 of its 8
    linear points -- on both sides of the move: refused-some is a number.

    Still-green on the unfixed tree too, so it says the guard is not too tight
    rather than announcing the fix: it reads only the numbers, which both
    trees compute the same way. The counts it now travels with are
    `test_the_counts_say_how_much_of_each_side_refused`."""
    R = _real()
    for sides in ((OK, PARTIAL), (PARTIAL, OK)):
        inj = _controls(monkeypatch, linear=sides)["injected"]
        defect = R["linear_partial"] if sides[0] == PARTIAL else R["linear"]
        base = R["linear_partial"] if sides[1] == PARTIAL else R["linear"]
        assert inj[SKEW]["moved_cents"] == round(
            abs(defect["worst_cents"] - base["worst_cents"]), 1), (sides, inj[SKEW])
        moved = _moves(inj)
        assert moved and all(_number(v) for v in moved.values()), (sides, moved)


def test_the_counts_say_how_much_of_each_side_refused(monkeypatch):
    """Every move carries `<refused>/<total>` for its own side and for the
    baseline's, so a reader can see how thin a measured move is. The partially
    refused row is a linear-stage fact (the 100 Hz probe): two points, one
    refused, where a whole row here has one."""
    for linear, movement, lin, mov in (
            ((OK, PARTIAL), None, ("0/1", "1/2"), ("0/2", "0/2")),
            ((PARTIAL, OK), None, ("1/2", "0/1"), ("0/2", "0/2")),
            ("both", "both", ("1/1", "1/1"), ("2/2", "2/2")),
            ("defective", "defective", ("1/1", "0/1"), ("2/2", "0/2")),
            ("baseline", "baseline", ("0/1", "1/1"), ("0/2", "2/2"))):
        inj = _controls(monkeypatch, linear=linear, movement=movement)["injected"]
        for control in MOVES:
            got = tuple(inj[control].get(k) for k in ("moved_refused_points", "baseline_refused_points"))
            assert got == (mov if control == QUANTUM else lin), (linear, movement, control, got)


def test_a_summary_that_contradicts_its_own_points_is_not_a_measurement(monkeypatch):
    """Rule 8's question for this guard: what reads "measured" while nothing
    was? A row whose every point refused and whose summary is a finite number
    anyway. No stage here writes one -- each derives its summary from the
    points it scored -- but the default that makes it impossible is one edit
    away in three open pull requests' neighbourhood."""
    R = _real()
    lying = dict(R["linear_dead"], worst_cents=38.68, worst_peak_db=7.415, worst_pass_db=0.014)
    inj = _controls(monkeypatch, linear=(OK, lying))["injected"]
    linear_moves = {k: v for k, v in _moves(inj).items() if not k.startswith(f"{QUANTUM}.")}
    assert all(v == REFUSED for v in linear_moves.values()), f"{UNKNOWN}: {linear_moves}"
    assert inj[DROPPED]["baseline_refused_points"] == "1/1", inj[DROPPED]


def test_a_side_with_no_operating_points_at_all_is_not_a_measurement(monkeypatch):
    """Rule 8's question asked of the fix itself, and the empty grid is its
    smallest answer. A row with no points refused nothing (`0/0`), and a real
    stage leaves its summary `nan` over nothing. The fix as it was first
    written skipped the count for such a row, so a summary that was finite
    anyway came out as a move of 0.0 -- "the defect moved nothing", the one
    number that reads as the dimension being blind. A side that scored nothing
    is refused whatever its summary says, on the linear stage and on the
    movement stage alike."""
    R = _real()
    lying = dict(R["linear_empty"], **{k: R["linear"][k] for k in LINEAR_SUMMARIES})
    no_sweeps = dict(R["movement"], sweeps=[], refused=0)
    for empty in (R["linear_empty"], lying):
        inj = _controls(monkeypatch, linear=(OK, empty),
                        movement=(OK, no_sweeps))["injected"]
        quiet = {f"{c}.{k}": inj[c][k] for c in MOVES for k in MOVES[c]
                 if inj[c][k] != REFUSED}
        assert not quiet, f"{UNKNOWN}: a baseline with no operating points: {quiet}"
        assert [inj[c]["baseline_refused_points"] for c in MOVES] == ["0/0"] * 3, inj
        assert [inj[c]["moved_refused_points"] for c in MOVES] == ["0/1", "0/1", "0/2"], inj


@pytest.mark.parametrize("value", [nan, float("inf"), float("-inf"), None, True, "38.68"],
                         ids=["nan", "inf", "neg-inf", "none", "bool", "text"])
def test_a_move_operand_that_is_not_a_finite_number_is_refused(value, monkeypatch):
    """A FINITE check, not a comparison: `nan < x` and `inf < x` are both
    False and `None < x` is a TypeError (#134's shape). A bool is an `int` and
    a numeric string converts, so neither is a measurement."""
    R = _real()
    inj = _controls(monkeypatch, linear=(OK, dict(R["linear"], worst_cents=value)))["injected"]
    assert inj[SKEW]["moved_cents"] == REFUSED, inj[SKEW]
    assert inj[DROPPED]["moved_cents"] == REFUSED, inj[DROPPED]
    # the field beside it, whose operand is a number, still is one
    assert _number(inj[DROPPED]["moved_peak_db"]), inj[DROPPED]


# ---------------------------------------------------------------------------
# the healthy path is the path it was
# ---------------------------------------------------------------------------
def test_the_real_controls_on_real_input_keep_the_numbers_the_subtraction_gave():
    """No stub anywhere: `stage_controls` as it ships, on the shipped ladder.
    Each move is exactly the `round(abs(a - b), n)` the sites computed before
    the fix, and `tanh-4-entries` keeps its keys and its `None` convention.

    Still-green on the unfixed tree too: it reads only what both trees write,
    so a number that moves when the fix lands is caught here and nowhere
    quieter."""
    R = _real()
    inj = R["controls"]["injected"]
    cents = (R["skew"]["worst_cents"], R["linear"]["worst_cents"])
    residual = (R["quantum"]["worst_residual_db"], R["movement"]["worst_residual_db"])
    assert inj[SKEW]["moved_cents"] == round(abs(cents[0] - cents[1]), 1), inj[SKEW]
    assert inj[QUANTUM]["moved_residual_db"] == round(abs(residual[0] - residual[1]), 2), inj[QUANTUM]
    assert _number(inj[SKEW]["moved_cents"]), inj[SKEW]
    assert _number(inj[QUANTUM]["moved_residual_db"]), inj[QUANTUM]
    assert set(inj[TANH]) == {"dimension", "moved_saturation_db", "moved_inharmonic_db"}, inj[TANH]
    assert inj[DROPPED]["moved_refused_points"] == "1/1", inj[DROPPED]
    assert inj[DROPPED]["baseline_refused_points"] == "0/1", inj[DROPPED]


def test_on_real_input_the_dropped_pole_says_refused_where_a_move_would_be():
    """The same healthy block, read for the new fields. A dropped pole refuses
    at every point on real input, so that control goes red by refusing -- and
    now says so in the move fields, not only in a count."""
    controls = _real()["controls"]
    inj = controls["injected"]
    assert inj[DROPPED].get("moved_peak_db") == REFUSED, inj[DROPPED]
    assert inj[DROPPED].get("moved_cents") == REFUSED, inj[DROPPED]
    for control, moved, whole in ((SKEW, "moved_cents", "0/1"),
                                  (QUANTUM, "moved_residual_db", "0/2")):
        assert _number(inj[control][moved]), (control, inj[control])
        got = (inj[control]["moved_refused_points"], inj[control]["baseline_refused_points"])
        assert got == (whole, whole), (control, got)
    _assert_no_unknown(inj)
    _assert_json_is_clean(inj)
    block = _injected_block(controls)
    _assert_no_unknown_in_block(block)
    assert "moved_peak_db=REFUSED, moved_cents=REFUSED" in block, block


def test_the_block_check_refuses_a_nan_and_not_the_word_resonance():
    """Its own control, because it was wrong first. Written as a substring
    test, `nan` matched inside `resonance` -- the dropped-pole control's own
    dimension label -- and the check failed on a correct block."""
    bad = _injected_block(_controls_of(**{SKEW: dict(dimension="linear", moved_cents=nan)}))
    with pytest.raises(AssertionError, match="moved_cents=nan"):
        _assert_no_unknown_in_block(bad)
    for value in (float("inf"), float("-inf")):
        block = _injected_block(_controls_of(
            **{SKEW: dict(dimension="linear", moved_cents=value)}))
        with pytest.raises(AssertionError):
            _assert_no_unknown_in_block(block)
    fine = _injected_block(_controls_of(**{
        DROPPED: dict(dimension="linear/resonance", moved_peak_db=REFUSED, moved_cents=REFUSED)}))
    assert "resonance" in fine and REFUSED in fine, fine
    _assert_no_unknown_in_block(fine)
    _assert_block_says_refused(_injected_block(_controls_of(**{
        DROPPED: dict(dimension="linear/resonance", moved_peak_db=REFUSED, moved_cents=REFUSED),
        QUANTUM: dict(dimension="movement", moved_residual_db=REFUSED)})))


# ---------------------------------------------------------------------------
# end to end: the real stages, the real cores, the real __main__
# ---------------------------------------------------------------------------
_RUN: dict = {}


def _seamed(*argv: str) -> tuple[str, str, dict]:
    """Run the tool through its real `__main__` with every ladder silenced."""
    if argv not in _RUN:
        with tempfile.TemporaryDirectory(prefix="ladder-injected-") as tmp:
            out = pathlib.Path(tmp) / "rep.json"
            src = (f"import sys, runpy\n{CHILD_PATHS}\n{TOTAL_SILENT}\n"
                   f"runpy.run_path({str(COMPARE)!r}, run_name='__main__')\n")
            p = subprocess.run([sys.executable, "-c", src, *argv, "--json", str(out)],
                               capture_output=True, text=True, timeout=900, cwd=str(ROOT))
            _RUN[argv] = (p.stdout, p.stderr, json.loads(out.read_text()))
    return _RUN[argv]


def test_end_to_end_every_ladder_dead_prints_refused_not_nan():
    """THE start-red control, through the shipped entry point: the block as the
    issue reported it. Red on the unfixed tree with two `nan` fields."""
    out, err, rep = _seamed("--quick", "--only", BASE)
    assert SEAM_FAILED not in err, err[-600:]
    assert "Traceback" not in err, err[-800:]
    # the seam's precondition, read back off the report: both sides of every
    # move refused every point
    lin, mov = rep["linear"][BASE], rep["movement"][BASE]
    assert lin["refused"] == len(lin["points"]) == 4, lin
    assert mov["refused"] == len(mov["sweeps"]) == 2, mov
    inj = rep["controls"]["injected"]
    _assert_all_refused(inj)
    _assert_json_is_clean(inj)
    _assert_block_says_refused(out.partition("INJECTED DEFECTS")[2].split("\n\n", 1)[0])
    # and each move says both sides refused, in counts off the real grids
    for control, tally in ((DROPPED, "4/4"), (SKEW, "4/4"), (QUANTUM, "2/2")):
        assert inj[control]["moved_refused_points"] == tally, (control, inj[control])
        assert inj[control]["baseline_refused_points"] == tally, (control, inj[control])


# ---------------------------------------------------------------------------
# the rule-5 control: the broken move, re-instated in memory
# ---------------------------------------------------------------------------
def _pre_fix(d, b, key, ndigits, rows):
    """#2141 as it shipped: the subtraction, and nothing guarding it."""
    return round(abs(d[key] - b[key]), ndigits)


def _summary_only(d, b, key, ndigits, rows):
    """The fix read narrowly: a non-finite summary refuses and the points are
    never counted."""
    return (cc.UNMEASURED if not (_number(d[key]) and _number(b[key]))
            else round(abs(d[key] - b[key]), ndigits))


def _no_points_exempt(d, b, key, ndigits, rows):
    """The fix as it was first written: a side with no operating points at all
    was left out of the count, so a finite summary over an empty grid came out
    as a move."""
    if any(row[rows] and row["refused"] == len(row[rows]) for row in (d, b)):
        return cc.UNMEASURED
    return _summary_only(d, b, key, ndigits, rows)


def _calls(t, mutation=None) -> None:
    """Call test `t` on a MonkeyPatch of its own, with `cc._moved` replaced by
    `mutation` when one is given."""
    mp = pytest.MonkeyPatch()
    try:
        if mutation is not None:
            mp.setattr(cc, "_moved", mutation)
        t(mp)
    finally:
        mp.undo()


MUST_GO_RED = (
    ("PRE_FIX_SUBTRACTION", _pre_fix, (
        test_every_point_refused_on_both_sides_reads_refused_not_nan,
        test_a_dead_baseline_under_a_scoring_defect_reads_refused,
        test_a_dead_defect_over_a_scoring_baseline_still_reads_red,
        test_a_summary_that_contradicts_its_own_points_is_not_a_measurement,
        test_a_side_with_no_operating_points_at_all_is_not_a_measurement)),
    ("POINTS_NEVER_COUNTED", _summary_only, (
        test_a_summary_that_contradicts_its_own_points_is_not_a_measurement,
        test_a_side_with_no_operating_points_at_all_is_not_a_measurement)),
    ("NO_POINTS_EXEMPT", _no_points_exempt, (
        test_a_side_with_no_operating_points_at_all_is_not_a_measurement,)),
)


@pytest.mark.parametrize("name, mutation, must_red", MUST_GO_RED,
                         ids=[name for name, _, _ in MUST_GO_RED])
def test_control_re_instating_the_broken_move_turns_the_named_tests_red(name, mutation, must_red):
    """THE injected-bug control (verification-rules rule 5). Each mutation is a
    way the fix can be undone; every test named for it must pass UNMUTATED
    first -- a red before the mutation is that test's own (#1125) -- and then
    fail on the assertion it was written for, not on some other one."""
    for t in must_red:
        _calls(t)
        with pytest.raises(AssertionError, match=re.escape(UNKNOWN)):
            _calls(t, mutation)
    # and the guard is not made tighter than its contract by either mutation:
    # a side that refused only some points is still a measurement
    _calls(test_a_side_that_refused_only_some_points_is_still_a_measurement, mutation)

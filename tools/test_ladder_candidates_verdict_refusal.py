#!/usr/bin/env python3
"""#2036: `compare_ladder_candidates` printed RETAIN at exit 0 when there was
nothing to compare. red-first on origin/main @ 616b8d63, 2026-11-28.

THE DEFECT, MEASURED on the unfixed tree through the tool's real `__main__`,
with every ladder silenced (`TOTAL_SILENT` below) and `--quick --only shipped`:

    exit 0, stderr empty, `nan` in every score column, and the last line
    "RETAIN THE CURRENT FILTER. No candidate improves on the shipped ladder by
    more than this harness can resolve while fitting the clock budget --
    which is issue #46's own bar, and a complete result rather than a failure
    to deliver a change."

`_verdict`'s inputs under that seam, read before it was treated as the fix
target (the issue asks for that): `matches = {worst_cents: nan, worst_peak_db:
nan, worst_pass_db: nan}` from `linear: 4 points, 4 refused`. Every comparison
it makes is then `nan < nan - 5.0`, which is False, so `wins` stays empty and
the "retain" branch is reached by default rather than by evidence. The same
run with the candidates dead and the baseline healthy (`CANDIDATES_SILENT`
below, all three candidates) printed the same line at exit 0: the candidates
were scored "no better" on no measurement at all. And `main`'s `0 if
rep["verdict"] is not None else 1` could never take its second branch, because
`_verdict` always returned a dict: a guard defeated by the very state it was
written for (verification-rules rule 8).

The other direction, measured in process against the same unfixed tree: a
baseline whose every RING point refused did not RETAIN. The stage books a
refused ring as `False`, so the baseline read "does not sing anywhere", the
rival was credited with "sings where the shipped ladder does not", and the
verdict was `retain: False, winners: ['zdf-newton-2']` -- a refusal deciding
to REPLACE the filter. The booking is #1851's defect; what is withheld here is
the verdict built on it.

WHAT IS STUBBED, AND WHAT IS NOT
--------------------------------
Two layers, because each can fail where the other is blind.

  in process   `_verdict` and `main` over reports built by `_rep`, in the
               shape the real stages write. `_rep`'s dead rows are pinned to
               the real `stage_linear` and `stage_resonance` run on a silent
               stub (`test_the_dead_rows_are_the_shape_the_real_stages_write`),
               so the fixture is not a guess at the stage's output.
  end to end   the real stages, the real cores, the real `__main__` (runpy, so
               argv is a direct invocation's). The seams WRAP `process` and
               zero its output, never replace it, so the cost counters
               `Candidate.render` reads have run. Each seam asserts its own
               precondition -- audible before the fault, silent after, and for
               `CANDIDATES_SILENT` the baseline still audible -- and reports
               `SEAM FAILED` rather than letting a seam that did not take read
               as a result.

WHAT THE FIX DOES. `_verdict` returns a WITHHELD verdict (`retain=None`, every
merit `None`, `text` naming what is unmeasured and with how many points
refused) when the baseline was not run, when no candidate was run, or when any
input to the comparison is unmeasured on either side. `main` prints `REFUSED:
no verdict -- ...` to stderr and exits 2, as its `Refused` path does. The
verdict is withheld as a whole rather than candidate by candidate: one
unmeasured input leaves some candidate uncompared, and "no candidate improves"
cannot be said while one was. `--only shipped` is withheld too -- zero
candidates makes RETAIN vacuous, which is the same default, reached one step
earlier.

THE BOUNDARY, STATED (rule 8). A stage that refuses SOME points is not
unmeasured: on the full grid the shipped ladder refuses 2 of 8 linear points
(a 100 Hz passband probe with too few periods) and on the quick grid 1 of 4
ring points, and both reports carry verdicts. Only refused-everywhere (or a
non-finite summary, or `None`) withholds. The counts are on every
`per_candidate` row as `refused_points`, so a reader can see how thin a
compared verdict is. A threshold on partial refusal is #1851's site and is
deliberately not duplicated here. A resonance row with no `points` at all is
not read as a refusal: only a stub writes that, and a run never does. A
summary that contradicts its own points -- finite over zero surviving points
-- is read by the points (`test_a_summary_that_contradicts_its_own_points...`).

The permanent form of the red-first record is
`tools/control_ladder_verdict_refusal.py` (`make controls`): each way the fix
can be undone, re-instated in memory, and the test here that must go red.

WRONG-THEN-RIGHT (five, every one in this file's own apparatus, each caught
because the run disagreed with what reading predicted):

  1. The first red run was red for the wrong reason in five tests. The
     fixture lacked the keys `_print` reads (`apparatus`, `controls`, `rung`,
     `cost.tanh`) and its fake `compare` returned no `verdict`, so `main` died
     on KeyError where exit 0 was the expected red. The fixture was completed
     and the red fell where the defect is.
  2. `_rep(**{(name, group, key): value})` is a TypeError -- `**` keys must be
     strings. Overrides are a dict argument now.
  3. The boundary control was RED on the unfixed tree, which a still-green
     control must never be. It refused one ring point on ONE side and had
     rebuilt #1851's defect in the fixture: the rival won the sings merit. An
     equal refusal on both sides is the only partial shape the unfixed verdict
     reads as "no merit". Every still-green control here now reads new keys
     with `.get`, so it is green on both trees by construction.
  4. Two tests reading one healthy run paid for two subprocess runs (243 s for
     the file): the `--json` path was part of the run cache's key. One report
     directory; 151 s.
  5. A test asserted both that the withheld list was empty and that its first
     entry existed. The structural gap IS an entry; the assertion says so now.
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
import sys
import tempfile

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
import compare_ladder_candidates as cc                             # noqa: E402
import exit_contract                                              # noqa: E402

BASE, RIVAL, OTHER = "shipped", "zdf-newton-2", "zdf-explicit"
LINEAR_INPUTS = ["worst_cents", "worst_peak_db", "worst_pass_db"]
COMPARE = ROOT / "tools" / "compare_ladder_candidates.py"
CHILD_PATHS = "sys.path[:0] = %r\n" % [str(ROOT / p) for p in ("model", "audition", "tools")]

#: reported as that when a seam has not taken: never a verdict about the tool
SEAM_FAILED = "SEAM FAILED"
RETAIN_LINE = "RETAIN THE CURRENT FILTER"

CANDIDATE_CORES = "lc.ShippedCore, lc.NewtonCore, lc.ExplicitDFCore"
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
    f"for _k in (fixed.LadderFx, rr._Variant, {CANDIDATE_CORES}):\n    _silence(_k)\n"
    "assert not np.any(_ours()) and not np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder still renders'!r}\n")

#: the candidate cores are dead and the baseline is NOT: the injected-bug
#: control. `ShippedCore`, `fixed.LadderFx` and `reference_rigs._Variant` are
#: left alone, and the seam says so before the tool runs.
CANDIDATES_SILENT = (
    "import numpy as np, ladder_candidates as lc\n"
    "_b = lc.Candidate('shipped')\n"
    "_c = lc.Candidate('zdf-newton-2')\n"
    "_p = np.ones(4800)\n"
    "assert np.any(_b.render(_p, 1000.0, 0.9)) and np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a ladder was silent before the fault'!r}\n"
    + WRAP +
    "for _k in (lc.NewtonCore, lc.ExplicitDFCore):\n    _silence(_k)\n"
    "assert np.any(_b.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' the baseline was silenced too'!r}\n"
    "assert not np.any(_c.render(_p, 1000.0, 0.9)), "
    f"{SEAM_FAILED + ' a candidate still renders'!r}\n")


# ---------------------------------------------------------------------------
# in-process reports, in the shape the stages write
# ---------------------------------------------------------------------------
def _linear(refused: int = 0, points: int = 4) -> dict:
    """`stage_linear`'s row: `points` operating points, the first `refused`
    of them refused, and the summaries `stage_linear` derives from the rest."""
    rows = {}
    for i in range(points):
        key = f"{400 + 1600 * i}/0.9"
        rows[key] = (dict(refused="insufficient evidence -- silent") if i < refused
                     else dict(cents=38.7, peak_db=7.42, pass_db=0.01))
    ok = [v for v in rows.values() if "refused" not in v]
    nan = float("nan")
    return dict(points=rows, refused=refused,
                worst_cents=max((abs(v["cents"]) for v in ok), default=nan),
                worst_peak_db=max((abs(v["peak_db"]) for v in ok), default=nan),
                worst_pass_db=max((abs(v["pass_db"]) for v in ok), default=nan))


def _ring(refused: int = 0, points: int = 4) -> dict:
    """`stage_resonance`'s row. A refused ring point is booked `False` here
    exactly as the stage books it on this tree (#1851's defect, not this
    one's), which is why the verdict reads the ring by its points, never by
    the bool."""
    rows, booked = {}, []
    for i in range(points):
        key = f"{400 + 800 * i}/1.05"
        if i < refused:
            rows[key] = dict(refused="the ring has no measurable frequency")
            booked.append(False)
        else:
            rows[key] = dict(sings=True, sustain_db=0.0, tail_rms=1e-3, cents=0.0)
            booked.append(True)
    return dict(points=rows, sings_everywhere=bool(booked and all(booked)),
                sings_fraction=round(sum(booked) / max(len(booked), 1), 3),
                refused=refused)


def _rep(linear=None, ring=None, names=(BASE, RIVAL), overrides=None) -> dict:
    """A healthy report for `names`. `linear`/`ring` give `(refused, points)`
    per name; an entry of `overrides` is `(name, group, key): value` on the
    summary `compare` builds for that ladder."""
    linear, ring, overrides = linear or {}, ring or {}, overrides or {}
    rep = dict(candidates={}, linear={}, resonance={}, quick=True,
               apparatus=dict(worst_db=0.0, oversample=2),
               controls=dict(start_red={}, injected={}))
    for n in names:
        rep["linear"][n] = _linear(*linear.get(n, (0, 4)))
        rep["resonance"][n] = _ring(*ring.get(n, (0, 4)))
        lin, rg = rep["linear"][n], rep["resonance"][n]
        rep["candidates"][n] = dict(
            rung=1, description="", coefficient_law="expo",
            matches=dict(worst_cents=lin["worst_cents"], worst_peak_db=lin["worst_peak_db"],
                         worst_pass_db=lin["worst_pass_db"]),
            bigger=dict(bass_weight_db=0.0, compression_db_per_db=0.5,
                        saturation_dbfs=-9.0, sings_everywhere=rg["sings_everywhere"]),
            cost=dict(tanh=10.0, divide=0.0, multiply=6.0,
                      clocks={d: 100.0 for d in cc.DIV_CLOCKS},
                      max_divider_latency_that_fits=7,
                      fits={d: True for d in cc.DIV_CLOCKS}))
    for (name, group, key), value in overrides.items():
        rep["candidates"][name][group][key] = value
    return rep


def _row(rep: dict, stage: str, name: str) -> dict:
    return rep["linear"][name] if stage == "linear" else rep["resonance"][name]


def _all_refused(rep: dict, stage: str, name: str) -> None:
    """Refuse every point of one stage row the way the stage does when the
    ladder is dead, and let the summary follow as the stage makes it follow."""
    row = _row(rep, stage, name)
    for v in row["points"].values():
        v.clear()
        v["refused"] = "insufficient evidence -- silent"
    if stage == "linear":
        nan = float("nan")
        row.update(worst_cents=nan, worst_peak_db=nan, worst_pass_db=nan, refused=len(row["points"]))
        rep["candidates"][name]["matches"].update(
            worst_cents=nan, worst_peak_db=nan, worst_pass_db=nan)
    else:
        row.update(sings_everywhere=False, sings_fraction=0.0, refused=len(row["points"]))
        rep["candidates"][name]["bigger"]["sings_everywhere"] = False


def _note(verdict: dict, name: str) -> dict:
    return next(r for r in verdict["per_candidate"] if r["candidate"] == name)


def _gaps(verdict: dict) -> list:
    return [(g["candidate"], tuple(g["inputs"])) for g in verdict["withheld"]]


# --- the fixture's own precondition -----------------------------------------

def test_the_dead_rows_are_the_shape_the_real_stages_write(monkeypatch):
    """`_rep` + `_all_refused` must hand `_verdict` what a dead ladder really
    produces, or every refusal test below passes against a picture of the
    stage instead of the stage."""

    class _Silent:
        def render(self, x, cut, res):
            return np.zeros(len(x))

        def ring(self, cut, res, **_):
            return np.zeros(4800)

    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Silent())
    lin = cc.stage_linear([BASE], (400.0,), (0.9,))[BASE]
    mine = _linear(1, 1)
    assert set(lin) == set(mine), (sorted(set(lin) ^ set(mine)))
    assert set(next(iter(lin["points"].values()))) == {"refused"}
    assert lin["refused"] == 1
    assert all(math.isnan(lin[k]) for k in LINEAR_INPUTS), lin
    rep = _rep(names=(BASE,))
    _all_refused(rep, "linear", BASE)
    assert all(math.isnan(rep["candidates"][BASE]["matches"][k]) for k in LINEAR_INPUTS)

    ring = cc.stage_resonance([BASE], (400.0,), (1.05,))[BASE]
    # A dead ring is NOT refused by the stage: it is booked "does not sing",
    # with nothing refused. So the ring alone cannot catch a dead ladder, and
    # `linear` is the input that does. What the verdict must catch through the
    # ring is an ESTIMATOR refusing every point, which is `_all_refused`'s
    # shape for it.
    assert ring["sings_everywhere"] is False, ring
    assert "refused" not in next(iter(ring["points"].values())), ring
    rep = _rep(names=(BASE,))
    _all_refused(rep, "resonance", BASE)
    assert rep["resonance"][BASE]["sings_everywhere"] is False
    assert all("refused" in v for v in rep["resonance"][BASE]["points"].values())


# --- the withheld state -----------------------------------------------------

def test_a_dead_baseline_withholds_the_verdict():
    rep = _rep()
    _all_refused(rep, "linear", BASE)
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None, v
    assert v["winners"] == [] and v["blocked_by_cost"] == []
    assert v["text"].startswith("NO VERDICT"), v["text"]
    assert RETAIN_LINE not in v["text"]
    for word in (*LINEAR_INPUTS, "linear 4/4", "shipped (the baseline)"):
        assert word in v["text"], (word, v["text"])
    assert _gaps(v) == [(BASE, tuple(LINEAR_INPUTS))], v["withheld"]
    assert v["not_compared"] == [RIVAL]
    assert _note(v, RIVAL)["state"] == "NOT COMPARED"


def test_a_dead_candidate_is_not_compared_and_is_not_scored_no_better():
    """THE injected-bug control, in process: the baseline is healthy, the
    candidate is dead. Before the fix the candidate was scored "no better" --
    every merit False -- and the verdict read RETAIN over it."""
    rep = _rep()
    _all_refused(rep, "linear", RIVAL)
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    n = _note(v, RIVAL)
    assert n["state"] == "NOT COMPARED"
    # unknown is unknown: not False, which is a measurement
    assert n["better_tuning"] is None and n["better_peak"] is None
    assert n["sings_where_we_do_not"] is None and n["wins_on_merit"] is None
    assert n["refused_points"] == dict(linear="4/4", resonance="0/4")
    assert n["unmeasured"] == dict(candidate=LINEAR_INPUTS, shipped=[])
    assert RIVAL in v["text"] and "linear 4/4" in v["text"], v["text"]
    assert _gaps(v) == [(RIVAL, tuple(LINEAR_INPUTS))], v["withheld"]


def test_a_baseline_whose_every_ring_point_refused_withholds_the_verdict():
    rep = _rep()
    _all_refused(rep, "resonance", BASE)
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert _gaps(v) == [(BASE, ("sings_everywhere",))], v["withheld"]
    assert "resonance 4/4" in v["text"], v["text"]


def test_a_candidate_whose_every_ring_point_refused_is_not_compared():
    rep = _rep()
    _all_refused(rep, "resonance", RIVAL)
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert _note(v, RIVAL)["unmeasured"]["candidate"] == ["sings_everywhere"]
    assert "resonance 4/4" in v["text"], v["text"]


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), None],
                         ids=["inf", "neg-inf", "none"])
def test_a_summary_that_is_not_a_finite_number_is_unmeasured(value):
    """A FINITE check, not a comparison: `inf < x` and `nan < x` are both
    False and `None < x` is a TypeError, so a guard written as a comparison
    is defeated by all three (#134's shape)."""
    rep = _rep(overrides={(RIVAL, "matches", "worst_cents"): value})
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert _gaps(v) == [(RIVAL, ("worst_cents",))], v["withheld"]
    assert f"reports {value!r}" in v["text"], v["text"]


def test_a_summary_that_contradicts_its_own_points_does_not_pass():
    """Rule 8's question for this guard -- what says "measured" while nothing
    was? A row whose every point refused and whose summary is a finite
    number anyway. `compare` cannot build one (it copies the summary off the
    same row), so this is the constructed input, and the points win."""
    rep = _rep()
    _all_refused(rep, "linear", RIVAL)
    for k in LINEAR_INPUTS:
        rep["candidates"][RIVAL]["matches"][k] = 1.0
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert _gaps(v) == [(RIVAL, tuple(LINEAR_INPUTS))], v["withheld"]
    assert "linear 4/4" in v["text"], v["text"]


def test_the_missing_evidence_on_both_sides_is_all_listed():
    rep = _rep(names=(BASE, RIVAL, OTHER))
    _all_refused(rep, "linear", BASE)
    _all_refused(rep, "resonance", RIVAL)
    v = cc._verdict(rep, [BASE, RIVAL, OTHER])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert _gaps(v) == [(BASE, tuple(LINEAR_INPUTS)), (RIVAL, ("sings_everywhere",))], v["withheld"]
    assert v["not_compared"] == [RIVAL, OTHER]
    # OTHER is measured on its own side and still not compared: the verdict
    # is withheld as a whole, and the text says so rather than leaving the
    # reader to infer it from OTHER's absence
    assert OTHER in v["text"], v["text"]
    assert _note(v, OTHER)["state"] == "NOT COMPARED"
    assert _note(v, OTHER)["unmeasured"] == dict(candidate=[], shipped=LINEAR_INPUTS)


# --- the boundary: partial refusal is still a measurement -------------------

def test_partial_refusal_is_still_a_measurement_and_the_verdict_stands():
    """The real grids' shape. Full: the shipped ladder refuses 2 of 8 linear
    points. Quick: 1 of 4 ring points. Both reports carry verdicts, and this
    one must too -- refused-everywhere withholds, refused-some does not.

    This is the control that the guard is not too TIGHT, so it is written to
    be green on the unfixed tree as well (`withheld` is read with `.get`): a
    control that is red before the fix says nothing about the fix. The ring
    refusals are the SAME on both sides on purpose -- one refused point is
    booked "does not sing" on this tree (#1851), and an equal booking on
    both sides is the only partial-refusal shape the unfixed verdict reads
    as "no merit" rather than as a win for whoever refused less."""
    rep = _rep(linear={BASE: (2, 8), RIVAL: (2, 8)}, ring={BASE: (1, 4), RIVAL: (1, 4)})
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["retain"] is True and v["winners"] == [], v
    assert v.get("withheld", []) == []
    assert v["text"].startswith(RETAIN_LINE), v["text"]
    # and the verdict is still the comparison it was: equal numbers, no win
    n = _note(v, RIVAL)
    assert n["better_tuning"] is False and n["wins_on_merit"] is False


def test_a_compared_verdict_keeps_the_text_and_invariant_it_had():
    """The healthy path's wording is pinned to what the unfixed tree printed,
    so the fix cannot have edited the result it was not about. Green on both
    trees, for the reason above."""
    equal = cc._verdict(_rep(), [BASE, RIVAL])
    assert equal["text"] == (
        "RETAIN THE CURRENT FILTER. No candidate improves on the shipped "
        "ladder by more than this harness can resolve while fitting the "
        "clock budget -- which is issue #46's own bar, and a complete "
        "result rather than a failure to deliver a change.")
    assert equal["retain"] is (not equal["winners"])
    better = cc._verdict(_rep(overrides={(RIVAL, "matches", "worst_cents"): 1.0}), [BASE, RIVAL])
    assert better["winners"] == [RIVAL] and better["retain"] is False
    assert better["text"].startswith("A candidate clears the bar: zdf-newton-2")
    tight = _rep(overrides={(RIVAL, "matches", "worst_cents"): 1.0,
                            (RIVAL, "cost", "max_divider_latency_that_fits"): None})
    for d in cc.DIV_CLOCKS:
        tight["candidates"][RIVAL]["cost"]["fits"][d] = False
    blocked = cc._verdict(tight, [BASE, RIVAL])
    assert blocked["blocked_by_cost"] == [RIVAL] and blocked["retain"] is True
    assert "EXCLUDED BY COST ALONE" in blocked["text"]
    for v in (equal, better, blocked):
        assert v["retain"] is (not v["winners"]) and v.get("withheld", []) == []


def test_a_compared_row_says_so_and_carries_its_refusal_counts():
    """How thin a compared verdict is belongs on the verdict, not only in
    the stage tables: the counts are read off each stage's own points."""
    rep = _rep(linear={BASE: (2, 8), RIVAL: (2, 8)}, ring={BASE: (1, 4), RIVAL: (2, 4)})
    v = cc._verdict(rep, [BASE, RIVAL])
    assert v["not_compared"] == []
    assert _note(v, RIVAL)["state"] == "COMPARED"
    assert _note(v, RIVAL)["refused_points"] == dict(linear="2/8", resonance="2/4")


# --- `--only` without both sides --------------------------------------------

def test_only_without_the_baseline_withholds_instead_of_raising_key_error():
    """`--only zdf-newton-2`: no baseline, so nothing to compare against.
    Before the fix this was `KeyError: 'shipped'` -- a traceback, exit 1,
    where a refusal belongs."""
    rep = _rep(names=(RIVAL,))
    v = cc._verdict(rep, [RIVAL])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert "was not run" in v["text"] and BASE in v["text"], v["text"]
    assert v["not_compared"] == [RIVAL]
    # one reason, and it is structural: no ladder has an unmeasured input
    assert _gaps(v) == [(None, ())], v["withheld"]
    assert _note(v, RIVAL)["unmeasured"] == dict(candidate=[], shipped=["not run"])


def test_only_the_baseline_withholds_a_vacuous_verdict():
    """`--only shipped`: zero candidates, so "no candidate improves" is true
    of nothing. That is RETAIN by default -- the same default this issue
    exists to remove, reached one step earlier."""
    v = cc._verdict(_rep(names=(BASE,)), [BASE])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert "no candidate was run" in v["text"], v["text"]
    assert v["per_candidate"] == [] and v["not_compared"] == []


def test_no_names_at_all_is_refused_too():
    """`--only` with nothing after it: `nargs='*'` gives `[]`."""
    v = cc._verdict(dict(candidates={}, linear={}, resonance={}), [])
    assert v["retain"] is None and RETAIN_LINE not in v["text"], v
    assert "was not run" in v["text"], v["text"]


# --- main: the exit status and the two streams ------------------------------

def _main(monkeypatch, capsys, rep, tmp_path, *only) -> tuple:
    """`main` over a report the real `compare` would have finished: the
    verdict is derived from `rep` exactly as `compare` derives it."""
    def finished(quick=False, names=None):
        rep["verdict"] = cc._verdict(rep, list(rep["candidates"]) if names is None else names)
        return rep

    monkeypatch.setattr(cc, "compare", finished)
    out = tmp_path / "rep.json"
    rc = cc.main(["--quick", "--only", *only, "--json", str(out)])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err, json.loads(out.read_text())


def test_main_exits_refused_and_names_the_gap_on_stderr(monkeypatch, capsys, tmp_path):
    rep = _rep()
    _all_refused(rep, "linear", BASE)
    rc, out, err, saved = _main(monkeypatch, capsys, rep, tmp_path, BASE, RIVAL)
    assert rc == exit_contract.REFUSED, (rc, err)
    assert err.strip().startswith("REFUSED: no verdict -- "), err
    assert "shipped (the baseline)" in err and "linear 4/4" in err, err
    # the table is still printed: the per-point refusals are the record of
    # what was tried, and withholding the verdict must not withhold them
    assert "Rungs 2-4" in out and "NO VERDICT" in out, out[-600:]
    assert RETAIN_LINE not in out
    # `--json` records the state
    assert saved["verdict"]["retain"] is None
    assert [g["candidate"] for g in saved["verdict"]["withheld"]] == [BASE]
    assert saved["verdict"]["withheld"][0]["points"] == [4, 4]


def test_main_exits_0_on_a_compared_verdict(monkeypatch, capsys, tmp_path):
    """Still-green on both trees: the exit status a compared report always
    had."""
    rc, out, err, saved = _main(monkeypatch, capsys, _rep(), tmp_path, BASE, RIVAL)
    assert rc == exit_contract.PASS, (rc, err)
    assert "REFUSED" not in err and RETAIN_LINE in out, (err, out[-400:])
    assert saved["verdict"]["retain"] is True and saved["verdict"].get("withheld", []) == []


def test_main_writes_no_json_when_the_apparatus_refuses(monkeypatch, capsys, tmp_path):
    """Unchanged, and pinned beside the new path so the two refusals are not
    confused: `compare` raising means nothing ran, so there is no report."""
    def boom(quick=False, names=None):
        raise cc.Refused("the rung-4 reference is dead")

    monkeypatch.setattr(cc, "compare", boom)
    out = tmp_path / "rep.json"
    rc = cc.main(["--quick", "--only", BASE, "--json", str(out)])
    captured = capsys.readouterr()
    assert rc == exit_contract.REFUSED and not out.exists()
    assert captured.err.strip() == "REFUSED: the rung-4 reference is dead", captured.err
    assert "Rungs 2-4" not in captured.out


# ---------------------------------------------------------------------------
# end to end: the real stages, the real cores, the real __main__
# ---------------------------------------------------------------------------
_RUNS: dict = {}
#: one directory for every end-to-end report, so two tests reading the same
#: run share one subprocess instead of each paying for its own
_OUT = pathlib.Path(tempfile.mkdtemp(prefix="ladder-verdict-"))


def _run(seam_id: str, *argv: str) -> subprocess.CompletedProcess:
    key = (seam_id, argv)
    if key not in _RUNS:
        seam = {"total_silent": TOTAL_SILENT, "candidates_silent": CANDIDATES_SILENT,
                "none": ""}[seam_id]
        src = (f"import sys, runpy\n{CHILD_PATHS}\n{seam}"
               f"runpy.run_path({str(COMPARE)!r}, run_name='__main__')\n")
        _RUNS[key] = subprocess.run([sys.executable, "-c", src, *argv], capture_output=True,
                                    text=True, timeout=900, cwd=str(ROOT))
    return _RUNS[key]


def _e2e(seam_id: str, *names: str) -> tuple:
    out = _OUT / f"{seam_id}-{'-'.join(names)}.json"
    p = _run(seam_id, "--quick", "--only", *names, "--json", str(out))
    assert SEAM_FAILED not in p.stderr, p.stderr[-600:]
    assert "Traceback" not in p.stderr, p.stderr[-800:]
    return p, json.loads(out.read_text())


def test_end_to_end_every_ladder_dead_refuses_instead_of_retaining():
    """THE start-red control: the reported defect, through the shipped
    entry point. Red on the unfixed tree with exit 0 and the RETAIN line."""
    p, rep = _e2e("total_silent", BASE)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert RETAIN_LINE not in p.stdout, p.stdout[-600:]
    assert "REFUSED: no verdict -- " in p.stderr, p.stderr[-600:]
    assert "shipped (the baseline)" in p.stderr and "linear 4/4" in p.stderr, p.stderr[-600:]
    # the stages ran and their refusals are reported, not hidden
    assert "Rungs 2-4" in p.stdout and "NO VERDICT" in p.stdout, p.stdout[-600:]
    assert rep["verdict"]["retain"] is None
    assert math.isnan(rep["candidates"][BASE]["matches"]["worst_cents"])
    assert rep["linear"][BASE]["refused"] == len(rep["linear"][BASE]["points"]) == 4


def test_end_to_end_a_dead_candidate_with_a_healthy_baseline_is_not_scored():
    """THE injected-bug control, end to end. Before the fix: exit 0, RETAIN."""
    p, rep = _e2e("candidates_silent", BASE, RIVAL)
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert RETAIN_LINE not in p.stdout, p.stdout[-600:]
    assert f"{RIVAL}" in p.stderr and "linear 4/4" in p.stderr, p.stderr[-600:]
    assert "NOT COMPARED" in p.stdout, p.stdout[-600:]
    v = rep["verdict"]
    assert v["retain"] is None and v["not_compared"] == [RIVAL]
    n = v["per_candidate"][0]
    assert n["state"] == "NOT COMPARED" and n["wins_on_merit"] is None
    assert n["refused_points"]["linear"] == "4/4"
    # the apparatus precondition of this control, read from the report: only
    # the candidate is dead
    assert math.isfinite(rep["candidates"][BASE]["matches"]["worst_cents"])
    assert rep["linear"][BASE]["refused"] == 0 and rep["linear"][RIVAL]["refused"] == 4


def test_end_to_end_a_healthy_run_still_renders_a_verdict():
    """THE still-green control: no seam. The fix changed no exit status and
    no verdict on the path every real report takes. Green on the unfixed
    tree too, by the same argument as the in-process boundary tests."""
    p, rep = _e2e("none", BASE, OTHER)
    assert p.returncode == exit_contract.PASS, (p.returncode, p.stdout[-600:], p.stderr[-600:])
    assert "REFUSED" not in p.stderr, p.stderr[-600:]
    v = rep["verdict"]
    assert v["retain"] is (not v["winners"]) and v["retain"] is not None, v
    assert v.get("withheld", []) == []
    assert v["text"].startswith(RETAIN_LINE) or v["text"].startswith("A candidate clears the bar"), v["text"]
    assert RETAIN_LINE in p.stdout or "A candidate clears the bar" in p.stdout, p.stdout[-600:]
    assert math.isfinite(rep["candidates"][BASE]["matches"]["worst_cents"])
    assert math.isfinite(rep["candidates"][OTHER]["matches"]["worst_cents"])


def test_end_to_end_a_compared_report_carries_its_refusal_counts():
    """The same healthy report, read for the new fields: every row says
    COMPARED, and the quick grid's own partial ring refusals -- 1 of 4 for
    the shipped ladder, 2 of 4 for `zdf-explicit`, measured on this tree --
    are on the row rather than folded into a verdict that never mentions
    them."""
    p, rep = _e2e("none", BASE, OTHER)
    v = rep["verdict"]
    assert v["not_compared"] == []
    assert [n["state"] for n in v["per_candidate"]] == ["COMPARED"]
    counts = v["per_candidate"][0]["refused_points"]
    assert re.fullmatch(r"\d+/\d+", counts["linear"]) and re.fullmatch(r"\d+/\d+", counts["resonance"]), counts
    ring = rep["resonance"][OTHER]["points"]
    refused = sum(1 for q in ring.values() if "refused" in q)
    assert counts["resonance"] == f"{refused}/{len(ring)}", (counts, ring)

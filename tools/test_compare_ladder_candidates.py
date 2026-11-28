"""A refused ring point is a refusal, never a measurement (#1851).

    python3 -m pytest tools/test_compare_ladder_candidates.py -q

`stage_resonance` asks each candidate ladder whether it sings at every
operating point, and `_verdict` turns the two `sings_everywhere` figures into
one merit: "sings where the shipped ladder does not". Before #1851 a ring the
estimator REFUSED was appended to `sings` as `False`. One refused point then
read as a measured "does not sing": a candidate whose every evaluated point
sang lost `sings_everywhere`, and a rival that sang everywhere was credited
with a difference nobody measured (#1785's shape, a refusal booked as a
verdict).

This was not latent under the current estimator. Measured on the unfixed tree
with `compare(quick=True)`, 2026-11-28: `shipped` refuses 1 of its 4 ring
points and sings at the other 3, and is booked `sings_everywhere=False`;
`zdf-newton-2` and `zdf-newton-3` refuse nothing and are each credited with
`sings_where_we_do_not`. Over the full grid `shipped` refuses 2 of 15.

WHAT IS STUBBED, AND WHAT IS NOT
--------------------------------
The candidate, and nothing the fix touches. `_Ring` has the one port the
resonance stage uses, `ring`, and no filter behind it: a steady sine at `cut`
(which the real `_ring_facts` judges as singing), digital silence at named
points (evaluated, does not sing) or a `Refused` at named points.
`stage_resonance`, `_ring_facts`, `compare`, `_verdict` and `_print` all run
for real. The stages that render a filter and that this issue does not touch
answer with fixed numbers (`_report`), equal on both sides, so `sings` is the
only merit that report can award.

`_Ring`'s own precondition is a test. If the estimator stops reading a
synthetic sine as a ring, that test fails by name instead of every refusal
test failing for a reason of its own.

The permanent form of the red-first record is
`tools/control_ladder_sings_refusal.py`: each way the fix can be undone,
re-instated in memory, and the test here that must go red.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_ladder_candidates as cc                             # noqa: E402
from dsp import SR                                                 # noqa: E402
from ladder_candidates import Refused                              # noqa: E402

#: Real candidate names, so `compare` reads each core's rung and law as it
#: does for a real report. Only `ring` answers differently.
BASE, RIVAL = "shipped", "zdf-newton-2"
CUTS, RES = (100.0, 400.0, 1600.0), (1.05, 2.00)
REFUSED_POINT = (400.0, 2.0)
ALL = "all"
_SECONDS, _AMP = 0.5, 0.05


class _Ring:
    """A candidate with one port and no filter. `ring` is a steady sine at
    `cut`, which sings by the real `_ring_facts`'s own criterion; digital
    silence at the points in `quiet`, which is evaluated and does not sing;
    and `Refused` at the points in `refuse`. Either may be `ALL`."""

    def __init__(self, refuse=(), quiet=()):
        self.refuse, self.quiet = refuse, quiet

    def ring(self, cut, res, **_):
        if self.refuse == ALL or (cut, res) in self.refuse:
            raise Refused(f"stub ring at {cut:.0f} Hz refused")
        n = int(_SECONDS * SR)
        if self.quiet == ALL or (cut, res) in self.quiet:
            return np.zeros(n)
        return _AMP * np.sin(2 * np.pi * cut * np.arange(n) / SR)


def _report(monkeypatch, **rings):
    """The real `compare` over stub rings. Tuning and peak are equal on both
    sides and every candidate fits the budget, so `sings` is the only merit
    this report can award, and a refusal on either side is the only way to
    make it unmeasured."""
    def fixed(**answer):
        return lambda names, *a, **kw: {n: dict(answer) for n in names}
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: rings[name])
    monkeypatch.setattr(cc, "stage_linear", fixed(
        worst_cents=1.0, worst_peak_db=0.1, worst_pass_db=0.1))
    monkeypatch.setattr(cc, "stage_bass", fixed(weight_db=0.0))
    monkeypatch.setattr(cc, "stage_drive", fixed(
        compression_db_per_db=0.5, saturation_dbfs=-9.0))
    monkeypatch.setattr(cc, "stage_movement", fixed())
    monkeypatch.setattr(cc, "stage_cleanliness", fixed())
    monkeypatch.setattr(cc, "stage_cost", fixed(
        per_sample=dict(tanh=1.0, divide=1.0, multiply=1.0),
        clocks_by_divider_latency={d: 100.0 for d in cc.DIV_CLOCKS},
        max_divider_latency_that_fits=7,
        fits_budget={d: True for d in cc.DIV_CLOCKS}))
    monkeypatch.setattr(cc, "stage_controls",
                        lambda *a, **kw: dict(start_red={}, injected={}))
    monkeypatch.setattr(cc.lc, "assert_apparatus",
                        lambda quick=False: dict(worst_db=0.0, oversample=2))
    return cc.compare(quick=True, names=list(rings))


def _note(rep, name):
    return next(r for r in rep["verdict"]["per_candidate"] if r["candidate"] == name)


def _row(out, name):
    """The candidate's line in the summary table, which `_print` prints first."""
    return next(ln for ln in out.splitlines()
                if ln.split()[:1] == [name]).split()


# --- the stub's own precondition -------------------------------------------

def test_the_stub_is_read_by_the_real_harness_as_singing_and_as_silent():
    for cut in cc.RING_CUTS:
        assert cc._ring_facts(_Ring().ring(cut, 1.05), cut)["sings"] is True, cut
        quiet = cc._ring_facts(_Ring(quiet=ALL).ring(cut, 1.05), cut)
        assert quiet["sings"] is False, quiet
    with pytest.raises(Refused):
        _Ring(refuse=ALL).ring(400.0, 1.05)


# --- the stage: where the refusal was booked as "does not sing" ------------

def test_a_refused_point_is_counted_and_enters_no_sings_figure(monkeypatch):
    """THE defect of #1851, at the stage that produced it. Every evaluated
    point sings and one refuses: that is unmeasured, not "does not sing"."""
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Ring(refuse={REFUSED_POINT}))
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["sings_everywhere"] is None, r
    assert r["sings_fraction"] == 1.0, r
    assert r["refused"] == 1, r
    assert sum("refused" in v for v in r["points"].values()) == 1, r["points"]
    assert "refused" in r["points"]["400/2.0"], r["points"]


def test_every_point_refused_leaves_nothing_measured(monkeypatch):
    """All six refused is not "sings at 0% of its points". Before #1851 it
    read exactly that: `sings_fraction == 0.0` out of no measurement at all."""
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Ring(refuse=ALL))
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["sings_everywhere"] is None, r
    assert r["sings_fraction"] is None, r
    assert r["refused"] == len(CUTS) * len(RES), r
    assert all("refused" in v for v in r["points"].values()), r["points"]


def test_a_refusal_beside_a_real_non_singing_point_keeps_them_apart(monkeypatch):
    """One point refuses and a different one is evaluated and does not sing.
    The fraction counts the evaluated one only (4 of 5, not 4 of 6, and not
    the 3 of 6 the defect gave), and the refusal still leaves
    `sings_everywhere` unmeasured rather than False."""
    stub = _Ring(refuse={REFUSED_POINT}, quiet={(100.0, 1.05)})
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: stub)
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["sings_everywhere"] is None, r
    assert r["sings_fraction"] == 0.8, r
    assert r["refused"] == 1, r


def test_no_refusal_is_counted_as_zero(monkeypatch):
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Ring())
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["refused"] == 0, r


def test_a_point_evaluated_and_not_singing_is_still_a_measured_false(monkeypatch):
    """Unchanged behaviour, pinned: a point the estimator did evaluate and
    found silent is a measurement of False, and stays one."""
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Ring(quiet=ALL))
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["sings_everywhere"] is False, r
    assert r["sings_fraction"] == 0.0, r
    assert not any("refused" in v for v in r["points"].values()), r["points"]


def test_no_refusal_changes_nothing(monkeypatch):
    monkeypatch.setattr(cc, "_cand", lambda name, **kw: _Ring())
    r = cc.stage_resonance([BASE], CUTS, RES)[BASE]
    assert r["sings_everywhere"] is True and r["sings_fraction"] == 1.0, r


# --- the verdict: where the refusal decided which candidate sounds bigger ---

def test_the_report_awards_the_merit_for_a_real_measured_difference(monkeypatch):
    """`_report`'s own precondition. Unless it CAN award the merit, every
    "does not award" test below would pass on a report that never awards it."""
    both = _report(monkeypatch, **{BASE: _Ring(), RIVAL: _Ring()})
    assert _note(both, RIVAL)["sings_where_we_do_not"] is False, both["verdict"]
    assert both["verdict"]["winners"] == [], both["verdict"]
    real = _report(monkeypatch, **{BASE: _Ring(quiet=ALL), RIVAL: _Ring()})
    assert _note(real, RIVAL)["sings_where_we_do_not"] is True, real["verdict"]
    assert real["verdict"]["winners"] == [RIVAL], real["verdict"]
    assert "NOT MEASURED" not in real["verdict"]["text"], real["verdict"]["text"]


def test_a_refused_point_on_the_shipped_ladder_is_not_a_merit_for_the_rival(monkeypatch):
    """THE defect of #1851, where it decided something. The rival sings at
    every point and so does the shipped ladder wherever it was evaluated; one
    shipped ring refused. Before #1851 that refusal booked the shipped ladder
    as not singing everywhere and the rival won on it."""
    rep = _report(monkeypatch, **{BASE: _Ring(refuse={REFUSED_POINT}), RIVAL: _Ring()})
    note = _note(rep, RIVAL)
    assert note["sings_where_we_do_not"] is False, note
    assert note["wins_on_merit"] is False, note
    assert rep["verdict"]["winners"] == [], rep["verdict"]
    assert rep["verdict"]["blocked_by_cost"] == [], rep["verdict"]
    assert note["sings_measured"] is False, note
    assert note["sings_refused_points"] == {"candidate": 0, "shipped": 1}, note
    assert "RETAIN THE CURRENT FILTER" in rep["verdict"]["text"]
    assert RIVAL in rep["verdict"]["text"] and "shipped 1" in rep["verdict"]["text"]


def test_a_refused_point_on_the_rival_is_recorded_and_decides_nothing(monkeypatch):
    rep = _report(monkeypatch, **{BASE: _Ring(quiet=ALL),
                                  RIVAL: _Ring(refuse={REFUSED_POINT})})
    note = _note(rep, RIVAL)
    assert note["sings_where_we_do_not"] is False, note
    assert note["wins_on_merit"] is False, note
    assert note["sings_measured"] is False, note
    assert note["sings_refused_points"] == {"candidate": 1, "shipped": 0}, note
    assert rep["verdict"]["winners"] == [], rep["verdict"]


def test_both_sides_refused_is_unmeasured_on_both(monkeypatch):
    rep = _report(monkeypatch, **{BASE: _Ring(refuse=ALL), RIVAL: _Ring(refuse=ALL)})
    note = _note(rep, RIVAL)
    assert note["sings_where_we_do_not"] is False, note
    points = len(cc.RING_CUTS_QUICK) * len(cc.RING_RES_QUICK)
    assert note["sings_refused_points"] == {"candidate": points, "shipped": points}, note


def test_a_measured_sings_merit_records_no_refusal(monkeypatch):
    rep = _report(monkeypatch, **{BASE: _Ring(quiet=ALL), RIVAL: _Ring()})
    note = _note(rep, RIVAL)
    assert note["sings_measured"] is True, note
    assert note["sings_refused_points"] == {"candidate": 0, "shipped": 0}, note


def test_the_summary_carries_the_refusal_from_the_stage_to_the_verdict(monkeypatch):
    """The whole path `compare` takes, unstubbed: the stage's `None` is the
    summary's `None`, the refusal count reaches the verdict, and the report
    still serialises the way `main` writes it."""
    rep = _report(monkeypatch, **{BASE: _Ring(refuse={REFUSED_POINT}), RIVAL: _Ring()})
    assert rep["candidates"][BASE]["bigger"]["sings_everywhere"] is None
    assert rep["candidates"][RIVAL]["bigger"]["sings_everywhere"] is True
    assert rep["resonance"][BASE]["refused"] == 1, rep["resonance"][BASE]
    assert rep["resonance"][RIVAL]["refused"] == 0, rep["resonance"][RIVAL]
    assert rep["verdict"]["retain"] is True
    json.dumps(rep, indent=1, sort_keys=True, default=str)


# --- the table: the one a reader looks at -----------------------------------

def test_the_table_prints_a_refusal_as_REFUSED_and_a_measurement_as_true_or_false(
        monkeypatch, capsys):
    rep = _report(monkeypatch, **{BASE: _Ring(refuse={REFUSED_POINT}), RIVAL: _Ring()})
    cc._print(rep)
    out = capsys.readouterr().out
    base, rival = _row(out, BASE), _row(out, RIVAL)
    assert "REFUSED" in base, base
    assert not {"False", "None", "True"} & set(base), base
    assert "True" in rival and "REFUSED" not in rival, rival
    rep = _report(monkeypatch, **{BASE: _Ring(quiet=ALL), RIVAL: _Ring()})
    cc._print(rep)
    base = _row(capsys.readouterr().out, BASE)
    assert "False" in base and "REFUSED" not in base, base


@pytest.mark.parametrize("entry,state", [
    ({"refused": 0, "sings_everywhere": False}, "scored"),   # evaluated, condemned
    ({"refused": 4}, "REFUSED"),                              # per-point count
    ({"refused": "null bass 55.7 Hz: insufficient"}, "REFUSED"),  # stage reason
    ({"sings_everywhere": False}, "scored"),                  # key absent
])
def test_start_red_state_is_decided_by_what_refused_not_by_key_presence(entry, state):
    assert cc._start_red_state(entry) == state

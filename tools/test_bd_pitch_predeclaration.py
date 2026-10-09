"""The BD pitch-envelope pre-tuning freeze (#557 acceptance 3) is checkable.

The committed record `docs/bd-pitch-predeclaration.json` must pass
`bd_pitch_predeclaration.check`, and each defect the check exists to catch is
injected into a COPY of the real record and must be caught for its own reason
(verification rules 1, 2 and 8):

  overlap         an untouched condition copied from development
  axis-not-held   untouched re-uses development's values on one axis
  missing-probe   a preservation probe names a file / symbol that is absent
  bare-constant   the minimum-improvement rule replaced by a number, or a
                  formula with no apparatus floor, or one whose quoted floor
                  is not in the cited document
  vocabulary      a condition outside the repository's existing vocabulary
  refused         the refused-reading rule removed / unsatisfiable / not
                  refusing, and a refusal folded into the median as a 0-cent
                  reading or a 0-cent error (both move the known answer),
                  the measured-condition floor lowered below its derivation,
                  the TONE or DECAY holdout lost, a finite reading at an
                  unqualified knob admitted
  onset phase     the coarse-grid worst case put back (17.5 c at knob 1.0),
                  a claimed worst below the fine-grid sweep, the sweep run on
                  the old 3-pitch grid

START RED: BPP_IMPL=bd_pitch_predeclaration_stub PYTHONPATH=tools/stubs runs
this file against a stub that accepts everything; every control fails.
No corpus, no render: this is a laptop-safe focused test.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
bp = importlib.import_module(os.environ.get("BPP_IMPL", "bd_pitch_predeclaration"))


@pytest.fixture(scope="module")
def record():
    assert bp.RECORD.is_file(), f"the frozen record {bp.RECORD} is absent"
    return json.loads(bp.RECORD.read_text())


def _caught(violations, needle):
    return any(needle in v for v in violations)


# --------------------------------------------------------- the record itself -
def test_committed_record_is_clean(record):
    assert bp.check(record) == []


def test_record_states_no_parameter_and_no_registry_entry(record):
    assert record["parameters_proposed"] == []
    assert record["sensitivity_registry_entries_added"] == []


def test_every_axis_present_in_both_sets(record):
    for side in ("development", "untouched"):
        focus = {c["axis_focus"] for c in record["conditions"][side]}
        assert {"tone", "decay", "accent", "retrigger"} <= focus, (side, focus)


# ------------------------------------------------------- injected-bug controls
def test_control_overlap_is_caught(record):
    bad = copy.deepcopy(record)
    leak = copy.deepcopy(bad["conditions"]["development"][0])
    leak["id"] = "LEAKED"
    bad["conditions"]["untouched"].append(leak)
    assert _caught(bp.check(bad), "overlap: untouched LEAKED")


def test_control_axis_not_held_out_is_caught(record):
    """Disjoint tuples are not enough: re-spell every untouched retrigger with
    a development value and the axis holds nothing out."""
    bad = copy.deepcopy(record)
    dev_r = next(c["model"]["retrigger_ms"] for c in bad["conditions"]["development"]
                 if c["model"]["retrigger_ms"] is not None)
    for c in bad["conditions"]["untouched"]:
        if c["model"]["retrigger_ms"] is not None:
            c["model"]["retrigger_ms"] = dev_r
    assert _caught(bp.check(bad), "axis retrigger")


@pytest.mark.parametrize("ref,needle", [
    ("model/bd_pedestal_GONE.py::tail_pedestal", "does not exist"),
    ("model/bd_pedestal.py::tail_pedestal_GONE", "defines no tail_pedestal_GONE"),
])
def test_control_missing_probe_is_caught(record, ref, needle):
    bad = copy.deepcopy(record)
    bad["preservation"][0]["probes"].append(ref)
    assert _caught(bp.check(bad), needle)


def test_control_missing_metric_function_is_caught(record):
    bad = copy.deepcopy(record)
    bad["primary_metric"]["functions"] = ["tools/pitch_trajectory.py::glide_cents_v2"]
    assert _caught(bp.check(bad), "primary_metric")


def test_control_dropped_property_is_caught(record):
    bad = copy.deepcopy(record)
    bad["preservation"] = [p for p in bad["preservation"] if p["property"] != "click"]
    assert _caught(bp.check(bad), "required property click")


@pytest.mark.parametrize("rule", [30.0, "30", {"formula": "30", "terms": []}])
def test_control_bare_constant_minimum_improvement_is_caught(record, rule):
    bad = copy.deepcopy(record)
    bad["minimum_improvement"] = rule
    assert _caught(bp.check(bad), "bare constant")


def test_control_rule_without_baseline_spread_is_caught(record):
    bad = copy.deepcopy(record)
    r = bad["minimum_improvement"]
    r["terms"] = [t for t in r["terms"] if "baseline_key" not in t]
    r["formula"] = "2 * max(" + ", ".join(t["name"] for t in r["terms"]) + ")"
    assert _caught(bp.check(bad), "no term read from the baseline JSON")


def test_control_floor_not_in_cited_doc_is_caught(record):
    """A floor typed in rather than quoted: 9.9 is not what the doc says."""
    bad = copy.deepcopy(record)
    t = next(t for t in bad["minimum_improvement"]["terms"] if "quote" in t)
    t["quote"] = t["quote"].replace(str(t["value"]), "9.9")
    t["value"] = 9.9
    assert _caught(bp.check(bad), "not found in docs/bd-pitch-baseline-request.md")


def test_control_preservation_limit_bare_constant_is_caught(record):
    bad = copy.deepcopy(record)
    bad["preservation"][0]["limit"] = 0.5
    assert _caught(bp.check(bad), "bare constant")


@pytest.mark.parametrize("field,val,needle", [
    ("tone", "60", "not in perceptual_gate.CODES"),
    ("decay_knob", 11.0, "outside BD_DECAY_Q"),
])
def test_control_condition_outside_vocabulary_is_caught(record, field, val, needle):
    bad = copy.deepcopy(record)
    c = next(c for c in bad["conditions"]["untouched"] if c["reference"]["corpus"] == "fischer")
    if field == "tone":
        c["tone"] = val
    else:
        c["model"]["decay_knob"] = val
    assert _caught(bp.check(bad), needle)


def test_control_mars_name_not_matching_cur_re_is_caught(record):
    bad = copy.deepcopy(record)
    c = next(c for c in bad["conditions"]["untouched"] if c["reference"]["corpus"] == "mars")
    c["reference"]["files"]["A"] = c["reference"]["files"]["A"].replace("Decay", "Dcy")
    assert _caught(bp.check(bad), "CUR_RE")


def test_control_registry_claim_without_parameter_is_caught(record):
    bad = copy.deepcopy(record)
    bad["sensitivity_registry_entries_added"] = ["bd-pitch-glide.json"]
    assert _caught(bp.check(bad), "registry entries are claimed")


# ---------------------------------------------------- the rule at use (later)
def test_rule_refuses_without_baseline(record):
    with pytest.raises(bp.Refused):
        bp.minimum_improvement_cents(record, None)


@pytest.mark.parametrize("v", [None, float("nan"), "12"])
def test_rule_refuses_non_finite_spread(record, v):
    with pytest.raises(bp.Refused):
        bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": v})


def test_rule_evaluates_the_declared_formula(record):
    """2 x the largest quoted floor (13.9) + the baseline's spread.  The 10 is a
    SYNTHETIC spread for arithmetic only, not a measurement."""
    got = bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": 10.0})
    assert got == pytest.approx(2 * 13.9 + 10.0)


def _baseline(spread=10.0, deficit=20.0):
    """SYNTHETIC baseline JSON content (arithmetic only, not a measurement)."""
    return {"provenance": {"commit": "abc", "sources_dirty": False},
            "recording_glide_spread_cents": spread,
            "pairs": {"fischer_vs_ours": {"glide_deficit_cents": deficit}}}


def _ship_readings(record, untouched_deficit):
    """SYNTHETIC reference/shipped readings on every untouched Fischer
    condition, all with |d| = untouched_deficit (the Judge's construction:
    reference 100, shipped 100 - deficit, candidate 100)."""
    return {c["id"]: {"reference": 100.0, "shipped": 100.0 - untouched_deficit,
                      "candidate": 100.0} for c in bp.untouched_fischer(record)}


def test_one_take_deficit_does_not_claim_experiment_wide_satisfiability(record):
    """The baseline's glide_deficit_cents is ONE take (REF_MAIN BD5050), a
    DEVELOPMENT condition.  Without untouched shipped readings satisfiability
    is not claimed either way: neither a green nor a red verdict."""
    s = bp.satisfiable(record, _baseline(deficit=20.0))
    assert s["satisfiable"] is None
    assert s["development_take_diagnostic"]["glide_deficit_cents"] == 20.0
    s = bp.satisfiable(record, _baseline(deficit=100.0))
    assert s["satisfiable"] is None


@pytest.mark.parametrize("dev_deficit,untouched_deficit,want", [
    (20.0, 100.0, True),    # one-take reading says unsatisfiable; the experiment is not
    (100.0, 20.0, False),   # one-take reading says satisfiable; the experiment cannot be
])
def test_satisfiable_uses_the_acceptance_statistic(record, dev_deficit, untouched_deficit, want):
    """PR #601 third review, both opposite outcomes: satisfiability is the
    shipped median over the measured untouched set (the statistic acceptance
    uses, same exclusions), never the development take's deficit."""
    s = bp.satisfiable(record, _baseline(spread=10.0, deficit=dev_deficit),
                       _ship_readings(record, untouched_deficit))
    assert s["satisfiable"] is want, s
    assert s["shipped_median"] == pytest.approx(untouched_deficit)
    assert sorted(e["id"] for e in s["excluded"]) == sorted(D00)
    # the acceptance path agrees: a perfect candidate's improvement passes iff satisfiable
    agg = bp.primary_aggregate(record, _ship_readings(record, untouched_deficit))
    assert (agg["improvement"] >= s["min_improvement_cents"]) is want


def test_satisfiable_allows_equality(record):
    """The frozen pass rule is improvement >= minimum_improvement: a shipped
    median exactly equal to the threshold is satisfiable (a perfect candidate
    passes), so the gate must not demand a strict excess."""
    need = bp.minimum_improvement_cents(record, _baseline(spread=10.0))
    rd = {c["id"]: {"reference": need, "shipped": 0.0} for c in bp.untouched_fischer(record)}
    s = bp.satisfiable(record, _baseline(spread=10.0), rd)
    assert s["shipped_median"] == need
    assert s["satisfiable"] is True


def test_satisfiable_refuses_with_too_few_measured(record):
    """Same exclusion rules as acceptance: too few measured -> REFUSED."""
    rd = _ship_readings(record, 100.0)
    rd["U-F-T50-D75"]["shipped"] = R
    with pytest.raises(bp.Refused, match="measurable"):
        bp.satisfiable(record, _baseline(), rd)


def test_unsatisfiable_gate_is_reported_not_passed(record):
    """Run the gate against a state before trusting it: a shipped median over
    the untouched set smaller than the required improvement cannot show one."""
    s = bp.satisfiable(record, _baseline(spread=10.0), _ship_readings(record, 20.0))
    assert s["satisfiable"] is False


@pytest.fixture(scope="module")
def sw():
    import bd_glide_phase_sweep as m
    return m


@pytest.fixture(scope="module")
def phase_sweep(record, sw):
    """Closed-form constant-pitch tones (true glide 0) on the FINE grid: every
    knob of the record's declared scope x f0 40-65 Hz in 0.5 Hz steps x both
    rates (tools/bd_glide_phase_sweep.py).  ~400 cells, ~10 s."""
    return sw.scoped_sweep(record)


@pytest.fixture(scope="module")
def apparatus_part(record):
    return bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": 0.0})


def test_phase_sweep_grid_is_fine(record, sw, phase_sweep):
    """The resolution is part of the tool: twice a coarse grid set the worst
    case (one knob, then 7 knobs x 3 pitches).  The scope must be every
    declared Fischer knob except the unqualified ones."""
    assert sw.F0_STEP <= 0.5 and sw.F0_LO <= 40.0 and sw.F0_HI >= 65.0
    assert set(sw.RATES) == {48000, 44100}
    assert sorted({r["f0"] for r in phase_sweep}) == list(sw.F0_GRID)
    assert len(sw.F0_GRID) == 51
    ka = record["primary_metric"]["onset_phase_known_answer"]
    assert list(sw.scope_knobs(record)) == ka["scope"]["decay_knobs"] == [2.5, 5.0, 7.5, 10.0]
    assert (ka["scope"]["f0_hz"] == {"lo": sw.F0_LO, "hi": sw.F0_HI, "step": sw.F0_STEP}
            and ka["scope"]["rates"] == list(sw.RATES))


def test_onset_phase_alone_cannot_satisfy_the_rule(record, sw, phase_sweep, apparatus_part):
    """Rule 8, the input that could defeat the primary metric: #558 found the
    gate's pitch_shape reads onset phase, and glide_cents does too.  On the
    fine grid, over the declared knobs, every cell must stay below the rule's
    apparatus part (spread 0), and the record's claimed worst must be the
    swept one: not below it (the error the review caught twice), not above it,
    not at another cell.  The range-wide figure is checked against the edge
    probe and its own cell, recomputed."""
    live = [r for r in phase_sweep if not r["refused"]]
    assert len(live) == len(phase_sweep), "a declared knob refuses: re-derive unqualified_knobs"
    for r in live:
        assert r["phase_only"] < apparatus_part, r
    ka = record["primary_metric"]["onset_phase_known_answer"]
    assert sw.claim_violations(ka, phase_sweep, apparatus_part) == []
    w = sw.worst(phase_sweep)
    assert w["phase_only"] > 3.0, "the phase effect vanished: re-measure and update the record"
    unt = [r for r in live if r["knob"] in (5.0, 7.5, 10.0)]
    wu = sw.worst(unt)
    assert ka["untouched_knobs_worst_cents"] == pytest.approx(wu["phase_only"], abs=0.05)
    assert (ka["untouched_knobs_worst_at"]["decay_knob"], ka["untouched_knobs_worst_at"]["f0_hz"],
            ka["untouched_knobs_worst_at"]["sr"]) == (wu["knob"], wu["f0"], wu["sr"])
    # absolute error of a true-zero glide (fixed F_REF): record and sweep agree
    ab = ka["abs_error_scoped_cents"]
    assert ab["f0_40_65"] == pytest.approx(sw.worst(live, "abs_err")["abs_err"], abs=0.05)
    mid = [r for r in live if 48.0 <= r["f0"] <= 56.0]
    assert ab["f0_48_56"] == pytest.approx(sw.worst(mid, "abs_err")["abs_err"], abs=0.05)


@pytest.fixture(scope="module")
def edge_probe(sw):
    return sw.edge_probe()


def test_range_wide_worst_exceeds_the_apparatus_part(record, sw, edge_probe, apparatus_part):
    """The record must NOT claim the whole DECAY range is safe: near the
    refusal edge onset phase alone is above 27.8 c (knob 0.6, 52 Hz, 48k)."""
    rw = record["primary_metric"]["onset_phase_known_answer"]["range_wide"]
    assert sw.range_claim_violations(rw, edge_probe, apparatus_part) == []
    assert rw["exceeds_apparatus_part"] is True and rw["worst_cents"] > apparatus_part


@pytest.mark.parametrize("worst_cents,at", [
    (17.5, {"sr": 48000, "decay_knob": 1.0, "f0_hz": 49.4}),   # the first-review figure
    (10.67, {"sr": 48000, "decay_knob": 5.0, "f0_hz": 51.0}),  # untouched-only, claimed for all
])
def test_control_coarse_worst_case_is_caught(record, sw, phase_sweep, edge_probe,
                                             apparatus_part, worst_cents, at):
    """Injected bug: the coarse-grid claim put back.  It must fail the scoped
    check AND, as a range-wide figure, the edge probe."""
    bad = copy.deepcopy(record["primary_metric"]["onset_phase_known_answer"])
    bad.update(worst_cents=worst_cents, worst_at=at, margin_x=round(apparatus_part / worst_cents, 2))
    v = sw.claim_violations(bad, phase_sweep, apparatus_part)
    assert v and any("worst" in x for x in v), v
    rw = dict(bad["range_wide"], worst_cents=worst_cents, worst_at=at, exceeds_apparatus_part=False)
    assert any("BELOW the edge probe" in x for x in sw.range_claim_violations(rw, edge_probe,
                                                                              apparatus_part))


def test_control_coarse_grid_is_caught(record, sw, apparatus_part):
    """Injected bug: the sweep run on the old coarse grid (3 pitches)."""
    ka = record["primary_metric"]["onset_phase_known_answer"]
    coarse = sw.sweep(ka["scope"]["decay_knobs"], f0s=(45.0, 49.4, 55.0))
    assert any("pitch grid" in x for x in sw.claim_violations(ka, coarse, apparatus_part))


def test_control_claim_below_swept_is_caught(record, sw, phase_sweep, apparatus_part):
    bad = copy.deepcopy(record["primary_metric"]["onset_phase_known_answer"])
    bad["worst_cents"] = 12.0
    assert any("BELOW the swept worst" in x
               for x in sw.claim_violations(bad, phase_sweep, apparatus_part))


def test_glide_unqualified_at_decay_knob_0(record, sw, apparatus_part):
    """Known answer (PR #601 reviews): at DECAY knob 0 (Q 2.3, tau ~15 ms) the
    ring is mostly gone by the 80-130 ms late window.  On the fine grid
    glide_cents refuses on most cells, and where it does answer it is wrong by
    more than the apparatus part (FINDING #602) -- so knob 0 is unqualified,
    and unqualified_knobs is exactly the declared knobs where that happens."""
    rows0 = sw.sweep((0.0,))
    answered = [r for r in rows0 if not r["refused"]]
    assert sum(r["refused"] for r in rows0) > len(rows0) // 2
    assert answered, "knob 0 now refuses everywhere: update the record's finding (#602)"
    for r in answered:
        assert r["abs_err"] > apparatus_part, r
    rr = record["primary_metric"]["refused_readings"]
    assert rr["unqualified_knobs"] == [0.0]
    want = {c["id"] for c in bp.untouched_fischer(record) if c["model"]["decay_knob"] == 0.0}
    assert want == {"U-F-T50-D00", "U-F-T00-D00"}
    assert set(rr["predicted_refusals"]) == want


def test_edge_finding_glide_answers_wrong_just_above_refusal(sw, apparatus_part):
    """FINDING #602 pinned: a true-zero glide at knob 0.6 / 52 Hz / 48 kHz is
    ANSWERED, tens of cents wrong.  When #602 is fixed this goes red and the
    record's edge_finding / unqualified_knobs must be revisited."""
    c = sw.cell(48000, 0.6, 52.0)
    assert not c["refused"]
    assert abs(c["sin"]) > 2 * apparatus_part and c["phase_only"] > apparatus_part, c


# ---------------------------------- REFUSED Fischer readings in the aggregate
R = "REFUSED: late window has too few live frames"


REF = 100.0
SHIP_D = (10.0, 20.0, 30.0, 40.0, 50.0, 70.0, 80.0, 90.0)   # |d| shipped, cents
CAND_D = (5.0, 10.0, 15.0, 20.0, 25.0, 35.0, 40.0, 45.0)    # |d| candidate


def _readings(record, refuse=(), cand_refuse=()):
    """SYNTHETIC glide readings (cents) for arithmetic only, not measurements.
    Measured conditions, in record order, are given distinct |d| so that any
    way of folding a refusal into the median moves it (see _known_answer)."""
    out = {}
    for n, c in enumerate(bp.untouched_fischer(record)):
        i = c["id"]
        out[i] = {"reference": REF, "shipped": R if i in refuse else REF - SHIP_D[n],
                  "candidate": R if (i in refuse or i in cand_refuse) else REF - CAND_D[n]}
    return out


D00 = ("U-F-T50-D00", "U-F-T00-D00")


def _known_answer(record):
    """Expected result with D00 refused on ours: those two excluded and listed,
    medians over the other six only.  Refusal read as a 0-cent READING puts
    |d| = 100 in twice, refusal read as a 0-cent ERROR puts |d| = 0 in twice;
    either shifts both medians by more than 5 cents with these values."""
    import statistics
    keep = [n for n, c in enumerate(bp.untouched_fischer(record)) if c["id"] not in D00]
    assert len(keep) == 6
    return (statistics.median(SHIP_D[n] for n in keep),
            statistics.median(CAND_D[n] for n in keep))


def _known_answer_holds(record, readings=None) -> bool:
    ship, cand = _known_answer(record)
    got = bp.primary_aggregate(record, readings or _readings(record, refuse=D00))
    return (got["verdict"] == "EVALUATED"
            and sorted(e["id"] for e in got["excluded"]) == sorted(D00)
            and len(got["measured"]) == 6
            and got["shipped_median"] == pytest.approx(ship)
            and got["candidate_median"] == pytest.approx(cand))


def test_refused_reading_is_excluded_and_listed(record):
    assert _known_answer_holds(record)


def _zero_reading(x):
    """Injected bug A: a refusal read as a 0-cent glide reading."""
    return float(x) if isinstance(x, (int, float)) and x == x else 0.0


def test_control_refusal_read_as_zero_reading_breaks_the_known_answer(record, monkeypatch):
    """Knob 0 is excluded by knob (unqualified), so a refusal folded in THERE
    cannot move anything any more -- that is the stronger guarantee, and the
    first assertion pins it.  At a QUALIFIED knob the known answer for one
    more refusal is REFUSED (5 < 6 measured); read as 0 it would be evaluated."""
    rd = _readings(record, refuse=D00 + ("U-F-T50-D75",))
    with pytest.raises(bp.Refused, match="measurable"):
        bp.primary_aggregate(record, rd)
    monkeypatch.setattr(bp, "_measured", _zero_reading)
    assert _known_answer_holds(record)                          # D00: excluded by knob
    got = bp.primary_aggregate(record, rd)                      # D75: the bug answers
    assert got["verdict"] == "EVALUATED" and "U-F-T50-D75" in got["measured"]


def test_control_refusal_read_as_zero_error_breaks_the_known_answer(record):
    """Injected bug B: a refusal turned into a 0-cent |d| (ours set equal to the
    reference) before the aggregate sees it.  At knob 0 it is excluded by knob
    (known answer unchanged); at a qualified knob it moves the medians away
    from the known answer, so the known-answer test goes red."""
    rd = _readings(record, refuse=D00)
    for i in D00:
        rd[i]["shipped"] = rd[i]["candidate"] = REF
    assert _known_answer_holds(record, rd)
    # U-F-T10-D50 sits ABOVE both medians (|d| 70 / 35 of 45 / 22.5), so a
    # 0-cent |d| there moves both (to 35 / 17.5).  U-F-T50-D75 (|d| 10 / 5,
    # the smallest) would not move either median: a control that cannot fail.
    rd = _readings(record, refuse=D00)
    rd["U-F-T10-D50"]["shipped"] = rd["U-F-T10-D50"]["candidate"] = REF
    assert not _known_answer_holds(record, rd)


def test_candidate_refusal_where_shipped_measured_is_a_failure(record):
    """A candidate that makes a measurable condition unmeasurable must FAIL; read
    as 0 cents it would look like the best improvement in the set."""
    got = bp.primary_aggregate(record, _readings(record, refuse=D00,
                                                 cand_refuse=("U-F-T50-D75",)))
    assert got["verdict"] == "FAILURE"
    assert got["candidate_refused"] == ["U-F-T50-D75"]
    assert got["improvement"] is None and got["candidate_median"] is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "0", "0.0", True])
def test_non_finite_or_non_number_reading_is_a_refusal(record, bad):
    """Not a number -> a refusal on that side, listed; with knob 0 already
    excluded that leaves 5 < 6 measured, so the aggregate REFUSES."""
    rd = _readings(record)
    rd["U-F-T50-D75"]["shipped"] = bad
    with pytest.raises(bp.Refused, match=r"'U-F-T50-D75', 'refused': \['shipped'\]"):
        bp.primary_aggregate(record, rd)


def test_too_few_measured_refuses_never_passes(record):
    """One refusal beyond the two predicted leaves 5 < 6: REFUSED, not a pass."""
    with pytest.raises(bp.Refused, match="measurable"):
        bp.primary_aggregate(record, _readings(record, refuse=D00 + ("U-F-T75-D50",)))


def test_lost_decay_holdout_refuses(record):
    """With the count floor lowered, losing every held-out DECAY value (75, 10)
    must still refuse: the remaining set would hold nothing out on DECAY."""
    rec = copy.deepcopy(record)
    rec["primary_metric"]["refused_readings"]["min_measured_conditions"] = 1
    gone = D00 + ("U-F-T50-D75", "U-F-T50-D10", "U-F-T10-D10")
    with pytest.raises(bp.Refused, match="holds out no decay"):
        bp.primary_aggregate(rec, _readings(rec, refuse=gone))


def test_lost_tone_holdout_refuses(record):
    """TONE twin: with the count floor lowered, losing every held-out TONE
    value (75, 00, 10) must refuse while DECAY (75, 10) is still held out --
    so only the TONE branch of the axis loop can catch it."""
    rec = copy.deepcopy(record)
    rec["primary_metric"]["refused_readings"]["min_measured_conditions"] = 1
    gone = D00 + ("U-F-T75-D50", "U-F-T00-D50", "U-F-T10-D50", "U-F-T10-D10")
    with pytest.raises(bp.Refused, match="holds out no tone"):
        bp.primary_aggregate(rec, _readings(rec, refuse=gone))


def test_unqualified_knob_reading_is_excluded_even_if_finite(record):
    """A knob-0 reading that happens to measure (8 of 102 fine-grid cells do,
    76-97 c wrong) is still excluded: same known answer as a refusal."""
    rd = _readings(record)
    for i in D00:
        rd[i] = {"reference": REF, "shipped": REF - 1.0, "candidate": REF - 0.5}
    assert _known_answer_holds(record, rd)
    got = bp.primary_aggregate(record, rd)
    assert all(e["refused"] == ["unqualified_knob"] for e in got["excluded"])


def test_control_unqualified_knobs_removed_is_caught(record):
    bad = copy.deepcopy(record)
    del bad["primary_metric"]["refused_readings"]["unqualified_knobs"]
    assert _caught(bp.check(bad), "unqualified_knobs must be a list")
    with pytest.raises(bp.Refused):
        bp.primary_aggregate(bad, _readings(bad, refuse=D00))


def test_control_predicted_refusals_not_at_unqualified_knobs_is_caught(record):
    bad = copy.deepcopy(record)
    bad["primary_metric"]["refused_readings"]["predicted_refusals"] = ["U-F-T50-D00", "U-F-T75-D50"]
    assert _caught(bp.check(bad), "not the untouched Fischer conditions at unqualified knobs")


@pytest.mark.parametrize("k", [1, 5])
def test_control_min_measured_not_its_derivation_is_caught(record, k):
    """Rule 8: lowering the floor to admit a result must fail check()."""
    bad = copy.deepcopy(record)
    bad["primary_metric"]["refused_readings"]["min_measured_conditions"] = k
    assert _caught(bp.check(bad), "is not its derivation")


def test_readings_must_cover_every_untouched_fischer_condition(record):
    """Dropping a condition from the readings is not an exclusion: refuse."""
    rd = _readings(record)
    del rd["U-F-T50-D00"]
    with pytest.raises(bp.Refused, match="missing"):
        bp.primary_aggregate(record, rd)


def test_control_refused_rule_removed_is_caught(record):
    bad = copy.deepcopy(record)
    del bad["primary_metric"]["refused_readings"]
    assert _caught(bp.check(bad), "no refused_readings rule")
    with pytest.raises(bp.Refused):
        bp.primary_aggregate(bad, _readings(bad, refuse=D00))


@pytest.mark.parametrize("k,needle", [(7, "unsatisfiable"), (9, "must be an integer"),
                                      ("6", "must be an integer")])
def test_control_min_measured_unsatisfiable_or_malformed_is_caught(record, k, needle):
    bad = copy.deepcopy(record)
    bad["primary_metric"]["refused_readings"]["min_measured_conditions"] = k
    assert _caught(bp.check(bad), needle)


def test_control_too_few_outcome_that_passes_is_caught(record):
    bad = copy.deepcopy(record)
    bad["primary_metric"]["refused_readings"]["too_few_outcome"] = "evaluate the remainder"
    assert _caught(bp.check(bad), "must REFUSE")


def test_cli_refuses_missing_baseline(tmp_path, capsys):
    assert bp.main(["min-improvement", "--baseline", str(tmp_path / "none.json")]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_cli_refuses_dirty_baseline(tmp_path, capsys):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"provenance": {"commit": "abc", "sources_dirty": True},
                             "recording_glide_spread_cents": 1.0}))
    assert bp.main(["min-improvement", "--baseline", str(p)]) == 2
    assert "dirty" in capsys.readouterr().out


def _cli(tmp_path, baseline: dict | str, readings: dict | None = None) -> list:
    p = tmp_path / "b.json"
    p.write_text(baseline if isinstance(baseline, str) else json.dumps(baseline))
    argv = ["min-improvement", "--baseline", str(p)]
    if readings is not None:
        r = tmp_path / "r.json"
        r.write_text(json.dumps(readings))
        argv += ["--readings", str(r)]
    return argv


# PR #601 third review, the Judge's exact four inputs: each produced a usable
# answer and exit 0.  Carried on the ACCEPTANCE path, with untouched readings
# that WOULD make the experiment satisfiable, so the only reason left to refuse
# is the malformed baseline value.  Infinity is written as the JSON token
# json.dumps emits, the form a baseline file would carry.
JUDGE_FOUR = [
    pytest.param(-100.0, 100.0, "recording_glide_spread_cents", id="negative-spread"),
    pytest.param(10.0, float("inf"), "glide_deficit_cents", id="infinite-deficit"),
    pytest.param(True, 100.0, "recording_glide_spread_cents", id="bool-spread"),
    pytest.param(10.0, "100", "glide_deficit_cents", id="string-deficit"),
]


@pytest.mark.parametrize("spread,deficit,needle", JUDGE_FOUR)
def test_cli_refuses_malformed_baseline_values(record, tmp_path, capsys, spread, deficit, needle):
    rc = bp.main(_cli(tmp_path, _baseline(spread, deficit), _ship_readings(record, 100.0)))
    out = capsys.readouterr().out
    assert rc == 2, out
    assert "REFUSED" in out and needle in out, out
    assert '"satisfiable": true' not in out, out


@pytest.mark.parametrize("spread,deficit,needle", JUDGE_FOUR)
def test_satisfiable_refuses_malformed_baseline_values(record, spread, deficit, needle):
    with pytest.raises(bp.Refused, match=needle):
        bp.satisfiable(record, _baseline(spread, deficit), _ship_readings(record, 100.0))


@pytest.mark.parametrize("baseline,needle", [
    pytest.param("{not json", "not valid JSON", id="bad-json"),
    pytest.param({"provenance": {"commit": "abc", "sources_dirty": False},
                  "recording_glide_spread_cents": 10.0}, "glide_deficit_cents", id="no-pairs"),
    pytest.param({"provenance": {"commit": "abc", "sources_dirty": False},
                  "recording_glide_spread_cents": 10.0, "pairs": []}, "glide_deficit_cents",
                 id="pairs-not-object"),
    pytest.param({"provenance": {"commit": "abc", "sources_dirty": False},
                  "pairs": {"fischer_vs_ours": {"glide_deficit_cents": 1.0}}},
                 "recording_glide_spread_cents", id="no-spread"),
    pytest.param([], "not a JSON object", id="baseline-not-object"),
])
def test_cli_refuses_malformed_baseline_fields(record, tmp_path, capsys, baseline, needle):
    """Missing/malformed fields REFUSE (exit 2), never a traceback or an answer."""
    rc = bp.main(_cli(tmp_path, baseline, _ship_readings(record, 100.0)))
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out and needle in out, out


def test_cli_refuses_non_positive_threshold(record, tmp_path, capsys):
    """The EVALUATED threshold is validated too: a formula that comes out at or
    below zero would admit a worsening as an 'improvement'."""
    rec = copy.deepcopy(record)
    rec["minimum_improvement"]["formula"] = (
        "steady_tone_worst_cents - glide_readout_bias_cents - closed_form_glide_worst_cents"
        " - recording_glide_spread_cents")
    with pytest.raises(bp.Refused, match="threshold"):
        bp.minimum_improvement_cents(rec, _baseline(spread=10.0))
    rp = tmp_path / "rec.json"
    rp.write_text(json.dumps(rec))
    rc = bp.main(_cli(tmp_path, _baseline(spread=10.0), _ship_readings(record, 100.0))
                 + ["--record", str(rp)])
    out = capsys.readouterr().out
    assert rc == 2 and "threshold" in out, out


def test_cli_without_readings_refuses_to_claim_satisfiability(tmp_path, capsys):
    """A well-formed baseline but no untouched readings: the threshold and the
    one-take diagnostic are printed, satisfiability is REFUSED (not claimed)."""
    rc = bp.main(_cli(tmp_path, _baseline(spread=10.0, deficit=100.0)))
    out = capsys.readouterr().out
    assert rc == 2 and "REFUSED" in out and "untouched" in out, out
    assert '"satisfiable": null' in out, out


@pytest.mark.parametrize("untouched_deficit,rc_want", [(100.0, 0), (20.0, 2)])
def test_cli_acceptance_path_both_outcomes(record, tmp_path, capsys, untouched_deficit, rc_want):
    rc = bp.main(_cli(tmp_path, _baseline(spread=10.0, deficit=100.0 if rc_want else 20.0),
                      _ship_readings(record, untouched_deficit)))
    out = capsys.readouterr().out
    assert rc == rc_want, out
    assert ('"satisfiable": true' in out) is (rc_want == 0), out

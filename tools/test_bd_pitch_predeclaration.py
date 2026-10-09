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
                  reading or a 0-cent error (both move the known answer)

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


def _baseline(spread=10.0, deficit=100.0):
    return {"provenance": {"commit": "abc", "sources_dirty": False},
            "recording_glide_spread_cents": spread,
            "pairs": {"fischer_vs_ours": {"glide_deficit_cents": deficit}}}


def _cli(tmp_path, baseline, readings=None, capsys=None):
    bpath = tmp_path / "b.json"
    bpath.write_text(json.dumps(baseline))
    argv = ["min-improvement", "--baseline", str(bpath)]
    if readings is not None:
        rpath = tmp_path / "r.json"
        rpath.write_text(json.dumps(readings))
        argv += ["--readings", str(rpath)]
    code = bp.main(argv)
    return code, capsys.readouterr().out


# The Judge's exact inputs (PR #601 review, item 1): each used to exit 0 with a
# usable threshold or "satisfiable".
@pytest.mark.parametrize("spread,deficit", [
    (-100, 100), (-5, 100), (10, float("inf")), (True, 100), (10, "100"), (10, True),
    (float("nan"), 100), ("10", 100), (None, 100), (10, None)])
def test_cli_refuses_invalid_baseline_numbers(tmp_path, capsys, record, spread, deficit):
    code, out = _cli(tmp_path, _baseline(spread, deficit), _readings(record), capsys)
    assert code == 2 and "REFUSED" in out, out
    code, out = _cli(tmp_path, _baseline(spread, deficit), None, capsys)
    assert code == 2 and "REFUSED" in out, out


@pytest.mark.parametrize("bl", [[], "x", {"provenance": []}, {"provenance": {"commit": "a", "sources_dirty": False}}])
def test_cli_refuses_malformed_baseline_without_traceback(tmp_path, capsys, bl):
    code, out = _cli(tmp_path, bl, None, capsys)
    assert code == 2 and "REFUSED" in out, out


def test_negative_evaluated_threshold_is_refused(record):
    bad = copy.deepcopy(record)
    bad["minimum_improvement"]["formula"] = "recording_glide_spread_cents - 1000"
    with pytest.raises(bp.Refused, match="non-negative"):
        bp.minimum_improvement_cents(bad, {"recording_glide_spread_cents": 10.0})


def _untouched_readings(record, dev_deficit, unt_deficit):
    """Reference 100, shipped 100 - deficit.  Item 2's construction: the
    baseline's one take is the DEVELOPMENT BD target; the untouched Fischer
    conditions are what acceptance takes the median over."""
    return {c["id"]: {"reference": 100.0, "shipped": 100.0 - unt_deficit, "candidate": 100.0}
            for c in bp.untouched_fischer(record)}


def test_satisfiable_follows_the_untouched_median_not_the_development_take(record):
    """Threshold with spread 10 is 37.8.  Development take 20, untouched 100:
    the experiment IS satisfiable.  Development 100, untouched 20: it is NOT."""
    b = _baseline(spread=10.0, deficit=20.0)
    ok = bp.satisfiable(record, b, _untouched_readings(record, 20.0, 100.0))
    assert ok["satisfiable"] is True and ok["min_improvement_cents"] == pytest.approx(37.8)
    b = _baseline(spread=10.0, deficit=100.0)
    no = bp.satisfiable(record, b, _untouched_readings(record, 100.0, 20.0))
    assert no["satisfiable"] is False
    assert bp.satisfiable(record, b, _untouched_readings(record, 100.0, 20.0))["max_possible_improvement_cents"] == pytest.approx(20.0)


def test_satisfiable_at_equality(record):
    need = bp.minimum_improvement_cents(record, _baseline(spread=10.0))
    at = bp.satisfiable(record, _baseline(spread=10.0), _untouched_readings(record, 0, need))
    assert at["satisfiable"] is True, "pass is improvement >= minimum: equality satisfies it"
    below = bp.satisfiable(record, _baseline(spread=10.0), _untouched_readings(record, 0, need - 0.5))
    assert below["satisfiable"] is False


def test_satisfiable_uses_the_frozen_exclusions(record):
    rd = _untouched_readings(record, 0, 100.0)
    for i in D00:
        rd[i]["shipped"] = R
    got = bp.satisfiable(record, _baseline(), rd)
    assert sorted(e["id"] for e in got["excluded"]) == sorted(D00) and len(got["measured"]) == 6
    for i in D00 + ("U-F-T75-D50",):
        rd[i]["shipped"] = R
    with pytest.raises(bp.Refused, match="measurable"):
        bp.satisfiable(record, _baseline(), rd)


def test_cli_without_readings_does_not_claim_satisfiability(tmp_path, capsys, record):
    """The one-take figure is information, not the acceptance statistic: whatever
    it says, the CLI must not answer 'satisfiable' either way."""
    for deficit in (20.0, 100.0):
        code, out = _cli(tmp_path, _baseline(10.0, deficit), None, capsys)
        assert code == 2 and "REFUSED" in out and "not_satisfiability" in out, out
        assert '"satisfiable"' not in out


def test_cli_with_readings_answers_on_the_untouched_median(tmp_path, capsys, record):
    code, out = _cli(tmp_path, _baseline(10.0, 20.0), _untouched_readings(record, 20.0, 100.0), capsys)
    assert code == 0 and '"satisfiable": true' in out, out
    code, out = _cli(tmp_path, _baseline(10.0, 100.0), _untouched_readings(record, 100.0, 20.0), capsys)
    assert code == 2 and '"satisfiable": false' in out, out


@pytest.fixture(scope="module")
def sweep_mod():
    import bd_glide_phase_sweep as sw
    return sw


@pytest.fixture(scope="module")
def phase_sweep(sweep_mod):
    """Closed-form constant-pitch tones (true glide zero) over the record's own
    DECAY knobs, f0 40 to 65 Hz in 0.5 Hz steps, both rates.  The grid is the
    tool's (tools/bd_glide_phase_sweep.py), so a coarse one cannot set the worst
    case again."""
    return sweep_mod.sweep()


@pytest.fixture(scope="module")
def edge_sweep(sweep_mod):
    return sweep_mod.edge_sweep()


def test_sweep_grid_is_not_coarse(sweep_mod, record):
    """The input that defeated the first two versions: a grid too coarse to see
    the worst cell.  Pin the resolution the reviewer's finer scan had."""
    f = sweep_mod.F0S
    assert f[0] <= 40.0 and f[-1] >= 65.0 and max(b - a for a, b in zip(f, f[1:])) <= 0.5
    assert max(b - a for a, b in zip(sweep_mod.EDGE_F0S, sweep_mod.EDGE_F0S[1:])) <= 0.5
    ek = sweep_mod.EDGE_KNOBS
    assert ek[0] == 0.0 and ek[-1] >= 1.5 and max(b - a for a, b in zip(ek, ek[1:])) <= 0.1 + 1e-9
    assert set(sweep_mod.record_knobs()) == {0.0, 2.5, 5.0, 7.5, 10.0}, "read from the record"
    assert set(sweep_mod.RATES) == {48000, 44100}


def test_onset_phase_alone_cannot_satisfy_the_rule_at_declared_knobs(record, phase_sweep, sweep_mod):
    """Rule 8, the input that could defeat the primary metric: #558 found the
    gate's pitch_shape reads onset phase.  glide_cents does too.  At every knob
    a declared condition uses, every measurable cell must stay below the rule's
    apparatus part (spread 0), and the worst must be the figure the record
    states, so record and measurement cannot drift apart."""
    floor = bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": 0.0})
    live = [r for r in phase_sweep if not r["refused"]]
    assert {r["knob"] for r in live} >= {2.5, 5.0, 7.5, 10.0}
    for r in live:
        assert r["phase_only"] < floor, r
    w = sweep_mod.worst(phase_sweep)
    assert w["phase_only"] > 3.0, "the phase effect vanished: re-measure and update the record"
    ka = record["primary_metric"]["onset_phase_known_answer"]["declared_knobs"]
    assert w["phase_only"] == pytest.approx(ka["worst_cents"], abs=0.01)
    assert (w["sr"], w["knob"], w["f0"]) == (ka["worst_at"]["sr"], ka["worst_at"]["decay_knob"],
                                              ka["worst_at"]["f0_hz"])
    assert floor / w["phase_only"] == pytest.approx(ka["margin_x"], abs=0.01)


def test_edge_scan_worst_exceeds_the_apparatus_part_and_is_recorded(record, edge_sweep, sweep_mod):
    """The range-wide claim is NOT 'below the rule': near the refusal edge onset
    phase alone moves a zero-glide reading by more than 27.8 cents.  The record
    states that figure; this pins it to the measurement."""
    floor = bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": 0.0})
    w = sweep_mod.worst(edge_sweep)
    ek = record["primary_metric"]["onset_phase_known_answer"]["edge_scan"]
    assert w["phase_only"] > floor and ek["exceeds_apparatus_part"] is True
    assert w["phase_only"] == pytest.approx(ek["worst_cents"], abs=0.01)
    assert (w["sr"], w["knob"], w["f0"]) == (ek["worst_at"]["sr"], ek["worst_at"]["decay_knob"],
                                              ek["worst_at"]["f0_hz"])
    declared = set(sweep_mod.record_knobs())
    assert not declared & {r["knob"] for r in edge_sweep if r["knob"] != 0.0}, \
        "the edge scan must cover knobs the declared conditions do NOT use"


def test_glide_cents_accepts_wrong_readings_just_above_the_refusal_edge(edge_sweep):
    """Finding for a tool follow-up, pinned so it cannot be forgotten or
    silently fixed unnoticed: a zero-glide tone at DECAY knob 0.6, 52 Hz,
    48 kHz is NOT refused and reads tens of cents of glide."""
    cells = [r for r in edge_sweep if r["knob"] == 0.6 and r["f0"] == 52.0 and r["sr"] == 48000]
    assert len(cells) == 1 and not cells[0]["refused"]
    assert abs(cells[0]["sin"]) > 50.0 and abs(cells[0]["cos"]) > 25.0, cells[0]


def test_glide_refuses_at_decay_knob_0(record, phase_sweep):
    """Known answer (PR #601 review): at DECAY knob 0 (Q 2.3, tau ~15 ms) the
    ring is ~-46 dB by the 80-130 ms late window, so glide_cents must REFUSE --
    never return a number -- at every pitch from 44 Hz up at 48 kHz and at every
    pitch at 44.1 kHz.  Below 44 Hz at 48 kHz it does not refuse (and reads
    wrongly); that is recorded, not hidden.  The record's predicted_refusals must
    be exactly the untouched Fischer conditions at a knob where it refuses."""
    refusing = {r["knob"] for r in phase_sweep if r["refused"]}
    assert refusing == {0.0}, refusing
    k0 = [r for r in phase_sweep if r["knob"] == 0.0]
    assert all(r["refused"] for r in k0 if r["sr"] == 44100 or r["f0"] >= 44.0)
    assert all(isinstance(r["sin"], str) or isinstance(r["cos"], str) for r in k0 if r["refused"])
    leak = [r for r in k0 if not r["refused"]]
    assert leak and all(r["sr"] == 48000 and r["f0"] < 44.0 for r in leak)
    assert all(abs(r["sin"]) > 50.0 and abs(r["cos"]) > 50.0 for r in leak), "reads wrongly, not zero"
    want = {c["id"] for c in bp.untouched_fischer(record) if c["model"]["decay_knob"] in refusing}
    assert want == {"U-F-T50-D00", "U-F-T00-D00"}
    assert set(record["primary_metric"]["refused_readings"]["predicted_refusals"]) == want


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
    monkeypatch.setattr(bp, "_measured", _zero_reading)
    ship, cand = _known_answer(record)
    got = bp.primary_aggregate(record, _readings(record, refuse=D00))
    assert got["shipped_median"] != pytest.approx(ship)       # the medians move ...
    assert got["candidate_median"] != pytest.approx(cand)
    assert not _known_answer_holds(record)                     # ... so the test goes red


def test_control_refusal_read_as_zero_error_breaks_the_known_answer(record):
    """Injected bug B: a refusal turned into a 0-cent |d| (ours set equal to the
    reference) before the aggregate sees it."""
    rd = _readings(record, refuse=D00)
    for i in D00:
        rd[i]["shipped"] = rd[i]["candidate"] = REF
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
    rd = _readings(record)
    rd["U-F-T50-D75"]["shipped"] = bad
    got = bp.primary_aggregate(record, rd)
    assert [e["id"] for e in got["excluded"]] == ["U-F-T50-D75"]


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
    """TONE twin of test_lost_decay_holdout_refuses (the loop over axes had one
    branch uncovered): lose every held-out TONE value (75, 00, 10); the
    remaining tone-50 conditions hold nothing out on TONE."""
    rec = copy.deepcopy(record)
    rec["primary_metric"]["refused_readings"]["min_measured_conditions"] = 1
    gone = D00 + ("U-F-T75-D50", "U-F-T00-D50", "U-F-T10-D50", "U-F-T10-D10")
    with pytest.raises(bp.Refused, match="holds out no tone"):
        bp.primary_aggregate(rec, _readings(rec, refuse=gone))


def test_control_min_measured_lowered_below_its_derivation_is_caught(record):
    """Rule 8: lowering the minimum alone used to pass check() and every test,
    contradicting 'the minimum is not lowered to admit a result'."""
    for k in (1, 5):
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

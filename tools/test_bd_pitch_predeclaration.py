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


def test_unsatisfiable_gate_is_reported_not_passed(record):
    """Run the gate against a state before trusting it: a present deficit
    smaller than the required improvement cannot show an improvement."""
    b = {"recording_glide_spread_cents": 10.0,
         "pairs": {"fischer_vs_ours": {"glide_deficit_cents": 20.0}}}
    assert bp.satisfiable(record, b)["satisfiable"] is False


@pytest.mark.parametrize("sr", [48000, 44100])
def test_onset_phase_alone_cannot_satisfy_the_rule(record, sr):
    """Rule 8, the input that could defeat the primary metric: #558 found the
    gate's pitch_shape reads onset phase.  glide_cents does too (~10.5 cents at
    48k, ~6.6 at 44.1k on a CONSTANT-pitch tone, closed-form signals written
    here), so a phase-only change must stay below the rule's apparatus part
    even with a zero recording spread."""
    import math
    import numpy as np
    import pitch_trajectory as pt
    t = np.arange(sr) / sr
    g = {}
    for name, ph in (("sin", 0.0), ("cos", math.pi / 2)):
        y = np.concatenate([np.zeros(sr // 100), np.sin(2 * np.pi * 49.4 * t + ph) * np.exp(-t / 0.142)])
        g[name] = pt.glide_cents(pt.trajectory(0.5 * y, sr, 52.0))
    phase_only = abs(g["cos"] - g["sin"])
    assert phase_only > 3.0, "the phase effect vanished: re-measure and update the record"
    assert phase_only < bp.minimum_improvement_cents(record, {"recording_glide_spread_cents": 0.0})


def test_cli_refuses_missing_baseline(tmp_path, capsys):
    assert bp.main(["min-improvement", "--baseline", str(tmp_path / "none.json")]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_cli_refuses_dirty_baseline(tmp_path, capsys):
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"provenance": {"commit": "abc", "sources_dirty": True},
                             "recording_glide_spread_cents": 1.0}))
    assert bp.main(["min-improvement", "--baseline", str(p)]) == 2
    assert "dirty" in capsys.readouterr().out

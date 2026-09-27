#!/usr/bin/env python3
"""Tests for report_halfslot.py's verdict, which is the consequential logic here.

#33 branches on this section's output: confirm the half slot, or hand a
money-versus-capability decision to a human.  So the tests below are about the two
ways that branch can be taken wrongly:

  * reporting "does not fit" for a design that placed, routed and closed timing,
    because the router left a few shorts -- an AREA verdict decided by a DRC count;
  * reporting "fits" while a per-corner slack that no post-route step ever wrote is
    read as though it were fresh -- the §6.1 finding, which already produced one
    wrong number on this run.

Run: pytest pnr/shuttle/test_report_halfslot.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_route as cr        # noqa: E402
import report_halfslot as rh    # noqa: E402


def metrics(*, util=0.73, drc=None, pads=754, ss=None, tt=None):
    m = {
        "design__instance__utilization": util,
        "design__instance__count__padcells": pads,
        "timing__setup__ws": 34.0607,
    }
    if drc is not None:
        m["route__drc_errors"] = drc
    if ss is not None:
        m["timing__setup__ws__corner:nom_ss_125C_4v50"] = ss
    if tt is not None:
        m["timing__setup__ws__corner:nom_tt_025C_5v00"] = tt
    return m


POST = {"timing__setup__ws__corner:nom_ss_125C_4v50",
        "timing__setup__ws__corner:nom_tt_025C_5v00",
        "route__drc_errors"}


def test_a_few_router_shorts_do_not_make_the_area_answer_no():
    """The exact inversion this split exists to prevent.

    Three Metal2 shorts is a sign-off problem.  If it renders as "does not fit one
    half slot", #33 goes to a business decision about dies per wafer on the strength
    of a routing artefact.
    """
    out = rh.verdict_section(metrics(drc=3, ss=12.0, tt=34.0), POST, {})
    fit, clean = out.split("#### Is the layout clean?")
    assert "Yes — measured, on one half slot" in fit
    assert "does not arise" in fit
    assert "the route is not clean" in clean
    assert "**3**" in clean


def test_the_area_answer_does_not_launder_the_drc_count():
    """The opposite failure: a clean area verdict must not imply a clean layout."""
    out = rh.verdict_section(metrics(drc=3, ss=12.0), POST, {})
    assert "cleared by the area answer" in out
    assert "sign-off question, not an area question" in out


def test_a_clean_route_says_so_without_claiming_signoff():
    out = rh.verdict_section(metrics(drc=0, ss=12.0), POST, {})
    assert "reports no violations of its own" in out
    assert "does not stand in for them" in out


def test_a_negative_slow_corner_fails_the_fit_verdict():
    """Timing IS an area-adjacent condition: a chip that misses ss does not fit."""
    out = rh.verdict_section(metrics(drc=0, ss=-178.5, tt=34.0), POST, {})
    assert "does not establish" in out
    assert "FAILED: setup closes post-route" in out


def test_a_stale_per_corner_slack_is_not_measured_not_passing():
    """Not in POST => not a post-route number => the row is 'not measured'."""
    out = rh.verdict_section(metrics(drc=0, ss=-178.5), post=set(), src={})
    assert "*not measured*" in out
    assert "NOT MEASURED: setup closes post-route" in out
    assert "FAILED: setup closes post-route" not in out


def test_an_unfinished_route_is_not_measured_in_both_tables():
    out = rh.verdict_section(metrics(drc=None, ss=12.0), POST, {})
    assert "NOT MEASURED: the detailed route ran to completion" in out
    assert "The router has not reported a violation count" in out
    assert "the route is not clean" not in out


def test_utilisation_over_one_fails_the_fit_verdict():
    out = rh.verdict_section(metrics(util=1.18, drc=0, ss=12.0), POST, {})
    assert "FAILED: placed inside the template's own die and core" in out


def _router_run(tmp_path, log, metric):
    import json
    d = tmp_path / "43-openroad-detailedrouting"
    d.mkdir(parents=True)
    (d / "openroad-detailedrouting.log").write_text(log)
    (d / "state_out.json").write_text(json.dumps({"metrics": {"route__drc_errors": metric}}))
    return str(tmp_path)


LOG = ("[INFO DRT-0195] Start 46th stubborn tiles iteration.\n"
       "[INFO DRT-0199]   Number of violations = 3.\n"
       "Viol/Layer      Metal2\n"
       "Short                3\n")


def test_router_section_prints_both_sources_and_the_layer_table(tmp_path):
    r = _router_run(tmp_path, LOG, 3)
    out = rh.router_section(r, {"route__drc_errors": 3}, {})
    assert "The two agree" in out
    assert "| `Metal2` | 3 |" in out
    assert "46th" in out


def test_router_section_refuses_when_the_sources_disagree(tmp_path):
    r = _router_run(tmp_path, LOG, 0)
    with pytest.raises(cr.Refusal):
        rh.router_section(r, {"route__drc_errors": 0}, {})


CENSUS = {"flops_declared_per_module": {"drum_regs": 3520}}


def test_verdict_json_passed_is_the_area_question_only(tmp_path):
    """Three Metal2 shorts must not turn docs/dag.json node S2 red.

    S2 is named "Fits a real shuttle padframe".  A node that goes red on a routing
    artefact reports a sign-off problem where a reader looks for an area answer.
    """
    v = rh.verdict_json(str(tmp_path), "43-x", metrics(drc=3, ss=12.0, tt=34.0),
                        POST, {}, CENSUS)
    assert v["passed"] is True
    assert v["router_clean"] is False
    assert v["route__drc_errors"] == 3
    assert v["failed"] == []


def test_verdict_json_not_measured_is_not_passed(tmp_path):
    v = rh.verdict_json(str(tmp_path), "42-x", metrics(drc=None, ss=12.0),
                        POST, {}, CENSUS)
    assert v["passed"] is False
    assert "the detailed route ran to completion" in v["not_measured"]
    assert v["failed"] == []
    assert v["router_clean"] is None


def test_verdict_json_a_missed_corner_is_a_failure_not_an_omission(tmp_path):
    v = rh.verdict_json(str(tmp_path), "48-x", metrics(drc=0, ss=-178.5, tt=34.0),
                        POST, {}, CENSUS)
    assert v["passed"] is False
    assert v["failed"] == ["setup closes post-route at every corner against 81.38 ns"]
    assert v["worst_post_route_setup_ns"] == -178.5
    assert v["router_clean"] is True


def test_verdict_json_records_that_no_signoff_check_ran(tmp_path):
    v = rh.verdict_json(str(tmp_path), "48-x", metrics(drc=0, ss=12.0), POST, {}, CENSUS)
    assert v["signoff_checks_run"] == []
    assert "NOT run" in v["note"]

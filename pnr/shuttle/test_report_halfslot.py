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

import json
import os
import shutil
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


# ---- the two output paths (#2096): --doc and --verdict-json -------------------------
# THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): `--verdict-json
# NEWDIR/v.json` read the run, rendered every section, wrote the document and
# then died at `open(a.verdict_json, "w")` on a FileNotFoundError -- a traceback
# and exit 1, STALE's code, for a run that had been reported. And `--doc
# NEWDIR/doc.md`: the document is READ first, as the template being spliced, so
# a missing one died at that read -- the same traceback, where exit 1 is what
# `--check` means by "stale". This tool refuses with REFUSED (3) and keeps it.
#
# THE CONTRACT, the #2040 pattern: both destinations are checked BEFORE the
# run's metrics are read. `--doc` must be an existing document -- one under a
# missing directory cannot be created and written, because there is no template
# to splice -- so that is REFUSED, not repaired. `--verdict-json`: a directory
# given as the file, or a parent that cannot be made, is REFUSED; a missing
# parent is created. An OSError at either write is REFUSED.
#
# WHAT IS DOUBLED: everything between the argument parse and the first write
# (the three metric reads, the eight sections and `verdict_json`; each has its
# own fixture above). Real: `main`, the document read, `splice`, both writes
# and the exit mapping.
MARKERS = ("measured:verdict", "measured:area", "measured:timing", "measured:padframe",
           "measured:growth", "measured:router", "measured:iterations", "measured:crosscheck")
TEMPLATE = "# half slot\n\n" + "".join(
    f"<!-- BEGIN {n} -->\nold {n}\n<!-- END {n} -->\n\n" for n in MARKERS)


def _double_the_measurement(monkeypatch, calls, *, before_the_writes=None):
    def read_metrics(run_dir):
        calls.append("read_metrics")
        return "43-openroad-detailedrouting", {}

    def section(name):
        def render(*args):
            calls.append(name)
            if name == "measured:crosscheck" and before_the_writes:
                before_the_writes()
            return f"new {name}\n"
        return render

    in_table = list(rh.SECTIONS)
    monkeypatch.setattr(rh.cr, "read_metrics", read_metrics)
    monkeypatch.setattr(rh.cr, "post_route_keys", lambda run_dir: set())
    monkeypatch.setattr(rh.cr, "metric_source_step", lambda run_dir: {})
    monkeypatch.setattr(rh, "SECTIONS", {n: (section(n), "prov") for n in in_table})
    for marker, fn in (("growth", "growth_section"), ("router", "router_section"),
                       ("iterations", "iteration_section"),
                       ("crosscheck", "crosscheck_section")):
        monkeypatch.setattr(rh, fn, section(f"measured:{marker}"))
    monkeypatch.setattr(rh, "verdict_json", lambda *args: {"passed": True, "router_clean": True})


def _template(tmp_path):
    doc = tmp_path / "docs" / "halfslot.md"
    doc.parent.mkdir()
    doc.write_text(TEMPLATE)
    return doc


def _argv(tmp_path, doc, vj, *extra):
    census = tmp_path / "census.json"
    census.write_text(json.dumps(CENSUS))
    return [str(tmp_path / "run"), "--doc", str(doc), "--census", str(census),
            "--verdict-json", str(vj), *extra]


def _an_unwritable_verdict_json(tmp_path, how):
    if how == "directory":
        return tmp_path
    if how == "file-in-the-way":
        (tmp_path / "file").write_text("not a directory")
        return tmp_path / "file" / "verdict.json"
    (tmp_path / "read-only").mkdir()
    (tmp_path / "read-only").chmod(0o500)
    return tmp_path / "read-only" / "new" / "verdict.json"


def test_verdict_json_under_a_missing_directory_is_created(tmp_path, monkeypatch, capsys):
    calls = []
    _double_the_measurement(monkeypatch, calls)
    doc, vj = _template(tmp_path), tmp_path / "evidence" / "new" / "verdict.json"
    assert rh.main(_argv(tmp_path, doc, vj)) == 0
    assert "new measured:verdict" in doc.read_text() and "old measured:" not in doc.read_text()
    assert json.loads(vj.read_text()) == {"passed": True, "router_clean": True}
    assert calls[0] == "read_metrics"
    assert f"wrote {vj}: passed=True router_clean=True" in capsys.readouterr().out


@pytest.mark.parametrize("how", ["missing", "directory"],
                         ids=["under-a-missing-directory", "given-as-a-directory"])
def test_a_doc_that_cannot_be_read_is_refused_before_the_metrics_are_read(
        tmp_path, monkeypatch, capsys, how):
    doc = _template(tmp_path)
    doc = doc.parent / "missing" / "halfslot.md" if how == "missing" else doc.parent
    vj = tmp_path / "evidence" / "verdict.json"
    calls = []
    _double_the_measurement(monkeypatch, calls)
    assert rh.main(_argv(tmp_path, doc, vj)) == rh.REFUSED
    assert "REFUSED" in capsys.readouterr().err
    assert calls == []
    assert not vj.parent.exists()


READ_ONLY = pytest.param("read-only", marks=pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory, so nothing refuses"))


def _lose_the_directory(parent, how):
    """Between the pre-flight and the write: the directory is removed, or made
    read-only -- the input that passes `mkdir(exist_ok=True)` (rule 8)."""
    if how == "removed":
        shutil.rmtree(parent, ignore_errors=True)
    else:
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o500)


@pytest.mark.parametrize("how", ["directory", "file-in-the-way", READ_ONLY],
                         ids=["directory-given-as-the-file", "parent-uncreatable", "parent-read-only"])
def test_an_unwritable_verdict_json_is_refused_before_the_metrics_are_read(
        tmp_path, monkeypatch, capsys, how):
    doc = _template(tmp_path)
    vj = _an_unwritable_verdict_json(tmp_path, how)
    calls = []
    _double_the_measurement(monkeypatch, calls)
    try:
        code = rh.main(_argv(tmp_path, doc, vj))
        err = capsys.readouterr().err
    finally:
        if how == "read-only":
            (tmp_path / "read-only").chmod(0o700)
    assert code == rh.REFUSED, err
    assert "REFUSED" in err, err
    assert calls == []
    assert "old measured:verdict" in doc.read_text()


@pytest.mark.parametrize("how", ["removed", READ_ONLY], ids=["directory-removed", "parent-read-only"])
def test_a_verdict_json_write_failure_is_refused_not_a_traceback(tmp_path, monkeypatch, capsys, how):
    doc, vj = _template(tmp_path), tmp_path / "evidence" / "verdict.json"
    calls = []
    _double_the_measurement(monkeypatch, calls,
                            before_the_writes=lambda: _lose_the_directory(vj.parent, how))
    try:
        code = rh.main(_argv(tmp_path, doc, vj))
        err = capsys.readouterr().err
    finally:
        if how == "read-only":
            vj.parent.chmod(0o700)
    assert code == rh.REFUSED
    assert "REFUSED" in err and str(vj) in err, err
    assert "new measured:verdict" in doc.read_text()


def test_a_doc_write_failure_is_refused_not_a_traceback(tmp_path, monkeypatch, capsys):
    """The document's directory is lost after the pre-flight, before the first
    write: the real `open(..., "w")` then raises OSError, which must be REFUSED.

    Only the removed case applies to --doc. The document already exists (it is
    the template), and an existing file is overwritten through a read-only
    directory: only creating an entry needs the directory's write bit, which is
    why the read-only case above is a write failure for --verdict-json alone."""
    doc, vj = _template(tmp_path), tmp_path / "evidence" / "verdict.json"
    calls = []
    _double_the_measurement(monkeypatch, calls,
                            before_the_writes=lambda: _lose_the_directory(doc.parent, "removed"))
    assert rh.main(_argv(tmp_path, doc, vj)) == rh.REFUSED
    err = capsys.readouterr().err
    assert "REFUSED" in err and str(doc) in err, err
    assert not vj.exists()


def test_check_mode_creates_nothing_for_the_verdict_json(tmp_path, monkeypatch, capsys):
    """Green before and after the fix: `--check` never writes, so the
    pre-flight must not create --verdict-json's directory there either."""
    doc, vj = _template(tmp_path), tmp_path / "evidence" / "new" / "verdict.json"
    _double_the_measurement(monkeypatch, [])
    assert rh.main(_argv(tmp_path, doc, vj, "--check")) == 1
    assert "is stale" in capsys.readouterr().err
    assert not vj.parent.exists()

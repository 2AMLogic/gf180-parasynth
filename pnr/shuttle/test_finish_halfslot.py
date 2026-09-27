#!/usr/bin/env python3
"""Tests for finish_halfslot.py.

The driver's only failure mode that matters is a false green: reporting a
post-route result for a run that did not route, or moving a run out from under a
live router and calling it done.  Every test below is one of those.

Run: pytest pnr/shuttle/test_finish_halfslot.py
"""

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import finish_halfslot as fh  # noqa: E402


# A real excerpt, shortened: two iterations, the second one the last.
DRT_LOG = """
[INFO DRT-0195] Start 43rd stubborn tiles iteration.
    Completing 100% with 9 violations.
[INFO DRT-0199]   Number of violations = 9.
Viol/Layer      Metal2  Metal3
Short                7       2
[INFO DRT-0267] cpu time = 01:56:09, elapsed time = 00:19:20
[INFO DRT-0195] Start 46th stubborn tiles iteration.
    Completing 100% with 3 violations.
[INFO DRT-0199]   Number of violations = 3.
Viol/Layer      Metal2
Short                3
[INFO DRT-0267] cpu time = 01:58:06, elapsed time = 00:23:12
Total wire length = 7334772 um.
"""

CLEAN_LOG = """
[INFO DRT-0195] Start 7th optimization iteration.
[INFO DRT-0199]   Number of violations = 0.
[INFO DRT-0267] cpu time = 00:00:11, elapsed time = 00:00:06
"""


def test_parse_drt_log_takes_the_last_count_not_the_first():
    """The router improves across iterations; an early count is not the result."""
    got = fh.parse_drt_log(DRT_LOG)
    assert got["violations"] == 3
    assert got["iteration"] == 46
    assert got["by_layer"] == {"Metal2": 3}
    assert got["iterations_seen"] == 2


def test_parse_drt_log_clean_route():
    got = fh.parse_drt_log(CLEAN_LOG)
    assert got["violations"] == 0
    assert got["by_layer"] == {}


def test_parse_drt_log_no_count_is_none_not_zero():
    """A log with no verdict must not read as a clean route."""
    got = fh.parse_drt_log("[INFO DRT-0195] Start 1st optimization iteration.\n")
    assert got["violations"] is None


def _run(tmp_path, step, *, state_out=True, log=None, metrics=None):
    d = tmp_path / step
    d.mkdir(parents=True)
    (d / "config.json").write_text("{}")
    if log is not None:
        (d / "openroad-detailedrouting.log").write_text(log)
    if state_out:
        (d / "state_out.json").write_text(json.dumps({"metrics": metrics or {}}))
    return str(tmp_path)


def test_step_completed_needs_state_out_not_just_the_directory(tmp_path):
    """A step directory exists as soon as the step STARTS.  That is the whole trap."""
    started = _run(tmp_path / "a", "43-openroad-detailedrouting", state_out=False)
    assert fh.find_step(started, fh.DETAILED_ROUTING)
    assert not fh.step_completed(started, fh.DETAILED_ROUTING)

    done = _run(tmp_path / "b", "43-openroad-detailedrouting")
    assert fh.step_completed(done, fh.DETAILED_ROUTING)


def test_find_step_matches_the_suffix_not_the_number(tmp_path):
    """The numeric prefix moves when the skip list changes; the step id does not."""
    r = _run(tmp_path, "99-openroad-detailedrouting")
    assert fh.find_step(r, fh.DETAILED_ROUTING) == "99-openroad-detailedrouting"


def test_drc_verdict_refuses_when_the_two_sources_disagree(tmp_path):
    r = _run(tmp_path, "43-openroad-detailedrouting",
             log=DRT_LOG, metrics={"route__drc_errors": 0})
    with pytest.raises(fh.Refusal) as e:
        fh.drc_verdict(r)
    assert "disagrees" in str(e.value)


def test_drc_verdict_agrees(tmp_path):
    r = _run(tmp_path, "43-openroad-detailedrouting",
             log=DRT_LOG, metrics={"route__drc_errors": 3})
    got = fh.drc_verdict(r)
    assert got["route__drc_errors"] == 3
    assert got["log_violations"] == 3
    assert got["by_layer"] == {"Metal2": 3}


def test_drc_verdict_refuses_a_run_with_no_router_step(tmp_path):
    r = _run(tmp_path, "13-openroad-floorplan")
    with pytest.raises(fh.Refusal):
        fh.drc_verdict(r)


def test_relocate_refuses_a_live_container(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "marker").write_text("x")
    monkeypatch.setattr(fh, "librelane_containers", lambda: ["deadbeef"])
    with pytest.raises(fh.Refusal) as e:
        fh.relocate(str(src), str(tmp_path / "dest"))
    assert "still running" in str(e.value)
    assert (src / "marker").exists(), "the source must be untouched by a refusal"


def test_relocate_refuses_an_existing_destination(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    with pytest.raises(fh.Refusal) as e:
        fh.relocate(str(src), str(dest))
    assert "already exists" in str(e.value)


def test_relocate_moves_when_nothing_is_live(tmp_path, monkeypatch):
    src = tmp_path / "src"
    (src / "43-openroad-detailedrouting").mkdir(parents=True)
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    dest = tmp_path / "wt" / "runs" / "halfslot"
    fh.relocate(str(src), str(dest))
    assert (dest / "43-openroad-detailedrouting").is_dir()
    assert not src.exists()


def test_librelane_containers_refuses_rather_than_answering_none(monkeypatch):
    """An empty list must mean 'none running', never 'could not tell'."""
    monkeypatch.setattr(fh.shutil, "which", lambda _: None)
    with pytest.raises(fh.Refusal):
        fh.librelane_containers()


def test_finish_refuses_when_the_route_did_not_complete(tmp_path, monkeypatch, capsys):
    runs = tmp_path / "librelane" / "runs"
    _run(runs / "halfslot", "43-openroad-detailedrouting", state_out=False, log=DRT_LOG)
    monkeypatch.setattr(fh, "HERE", str(tmp_path))
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    rc = fh.finish("halfslot", None, poll=0, timeout=0, dry_run=True)
    assert rc == fh.REFUSED
    assert "recorded no state_out.json" in capsys.readouterr().err


def test_finish_does_not_resume_a_flow_that_already_reached_post_route_sta(
        tmp_path, monkeypatch):
    """Resuming a completed flow would re-run steps over a finished layout."""
    runs = tmp_path / "librelane" / "runs" / "halfslot"
    _run(runs, "43-openroad-detailedrouting", log=DRT_LOG,
         metrics={"route__drc_errors": 3})
    (runs / "48-openroad-stapostpnr").mkdir(parents=True)
    (runs / "48-openroad-stapostpnr" / "state_out.json").write_text('{"metrics":{}}')
    monkeypatch.setattr(fh, "HERE", str(tmp_path))
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    called: list[list[str]] = []
    monkeypatch.setattr(fh, "run", lambda cmd, cwd=None: called.append(cmd) or 0)
    fh.finish("halfslot", None, poll=0, timeout=0, dry_run=True)
    assert not any("resume" in c for c in called)


def test_wait_returns_not_exited_on_timeout(monkeypatch):
    monkeypatch.setattr(fh, "librelane_containers", lambda: ["live"])
    monkeypatch.setattr(fh.time, "sleep", lambda _: None)
    got = fh.wait_for_exit(poll=0, timeout=-1)
    assert got["exited"] is False


ITER_LOG = """
[INFO DRT-0195] Start 33rd optimization iteration.
[INFO DRT-0199]   Number of violations = 9.
[INFO DRT-0267] cpu time = 00:00:11, elapsed time = 00:00:06, memory = 1
[INFO DRT-0195] Start 35th stubborn tiles iteration.
[INFO DRT-0199]   Number of violations = 3.
[INFO DRT-0267] cpu time = 00:34:01, elapsed time = 00:07:17, memory = 1
[INFO DRT-0195] Start 46th stubborn tiles iteration.
[INFO DRT-0199]   Number of violations = 3.
[INFO DRT-0267] cpu time = 01:58:06, elapsed time = 00:23:12, memory = 1
"""


def test_parse_drt_iterations_keeps_optimization_iterations():
    """`optimization` iterations carry no ' tiles'; requiring it dropped them all."""
    got = fh.parse_drt_iterations(ITER_LOG)
    assert [r["iteration"] for r in got] == [33, 35, 46]
    assert [r["kind"] for r in got] == ["optimization", "stubborn", "stubborn"]


def test_parse_drt_iterations_pairs_counts_and_times_with_their_iteration():
    got = fh.parse_drt_iterations(ITER_LOG)
    assert got[0] == {"iteration": 33, "kind": "optimization", "violations": 9,
                      "elapsed_s": 6, "cpu_s": 11}
    assert got[-1]["elapsed_s"] == 23 * 60 + 12
    assert got[-1]["cpu_s"] == 3600 + 58 * 60 + 6


def test_parse_drt_iterations_flat_tail_is_visible():
    """The §6.5 evidence: the count stops moving long before the router stops."""
    got = fh.parse_drt_iterations(ITER_LOG)
    flat = [r for r in got if r["violations"] == got[-1]["violations"]]
    assert len(flat) == 2
    assert sum(r["cpu_s"] for r in flat[1:]) > 0


def test_resume_starts_at_the_first_step_after_the_route_not_at_rcx(tmp_path,
                                                                   monkeypatch):
    """Resuming at OpenROAD.RCX skips nine steps, two of which matter a lot.

    It skips the POST-ROUTE antenna check (#33 asks for antenna violations by name)
    and OpenROAD.FillInsertion (a die with no fillers is the shape of
    klayout-tools#2086).  The result would be post-route timing for a layout missing
    both, with the flow reporting success.
    """
    runs = tmp_path / "librelane" / "runs" / "halfslot"
    _run(runs, "43-openroad-detailedrouting", log=DRT_LOG,
         metrics={"route__drc_errors": 3})
    monkeypatch.setattr(fh, "HERE", str(tmp_path))
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    called: list[list[str]] = []
    monkeypatch.setattr(fh, "run", lambda cmd, cwd=None: called.append(cmd) or 0)
    fh.finish("halfslot", None, poll=0, timeout=0, dry_run=False)
    resume = next(c for c in called if "resume" in c)
    assert "OpenROAD.RCX" not in resume
    assert resume[resume.index("--from") + 1] == "Odb.RemoveRoutingObstructions"


def test_resume_makes_trdrc_non_fatal_on_the_command_line_only(tmp_path, monkeypatch):
    """The override must not live in the committed config.

    A config that says "do not stop on router DRC" changes what every future run
    means; the override belongs where a reader of the command sees it.
    """
    runs = tmp_path / "librelane" / "runs" / "halfslot"
    _run(runs, "43-openroad-detailedrouting", log=DRT_LOG,
         metrics={"route__drc_errors": 3})
    monkeypatch.setattr(fh, "HERE", str(tmp_path))
    monkeypatch.setattr(fh, "librelane_containers", lambda: [])
    called: list[list[str]] = []
    monkeypatch.setattr(fh, "run", lambda cmd, cwd=None: called.append(cmd) or 0)
    fh.finish("halfslot", None, poll=0, timeout=0, dry_run=False)
    resume = next(c for c in called if "resume" in c)
    assert "--override-config" in resume
    assert resume[resume.index("--override-config") + 1] == "ERROR_ON_TR_DRC=false"
    cfg = pathlib.Path(__file__).parent / "librelane" / "config.yaml"
    assert "ERROR_ON_TR_DRC" not in cfg.read_text(), \
        "the override must stay out of the committed config"

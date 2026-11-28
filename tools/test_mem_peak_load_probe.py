#!/usr/bin/env python3
"""Controls for `tools/mem_peak_load_probe.py`. Its one failure mode that
matters is a table that looks like data: a hog that was not running, an
errored run read as a failed test, or a control's `[obs]` line read as a
measurement of the shipped wrapper.

    python -m pytest tools/test_mem_peak_load_probe.py -q
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mem_peak_load_probe as probe  # noqa: E402

probe_start_hogs = probe.start_hogs    # the real one, for the test that replaces it

TREE = "test_the_peak_is_the_sum_across_a_tree_not_its_largest_process"
RECORD = "test_the_json_record_carries_the_number_and_the_sample_count"

#: The shape of `pytest -n 2 -q -rPf`: a section header names the test whose
#: captured stdout follows, the controls included, a failed test's included.
PYTEST_OUTPUT = f"""\
==================== PASSES ====================
________ {TREE} ________
[gw1] linux -- Python 3.12.3
--------------------------- Captured stdout call ---------------------------
[obs] scenario=tree interval=0.02 samples=25 wall=0.8 peak=320
________ test_control_a_grandchild_that_says_touched_without_touching_goes_red ________
[gw0] linux -- Python 3.12.3
--------------------------- Captured stdout call ---------------------------
[obs] scenario=tree interval=0.02 samples=22 wall=0.7 peak=170
=========================== FAILURES ===========================
_______________________ {RECORD} _______________________
[gw0] linux -- Python 3.12.3
---------------------------- Captured stdout call ----------------------------
[obs] scenario=record interval=0.05 samples=4 wall=0.8 peak=159
E       AssertionError: ...
=========================== short test summary info ============================
FAILED tools/test_mem_peak.py::{RECORD}
1 failed, 16 passed in 5.10s
"""
PASSING = (f"________ {TREE} ________\n"
           "[obs] scenario=tree interval=0.02 samples=25 wall=0.8 peak=320\n"
           "17 passed in 5.50s\n")


def test_each_obs_line_belongs_to_the_test_whose_stdout_it_sits_in():
    seen = probe.parse_obs(PYTEST_OUTPUT)
    assert set(seen) == {TREE, RECORD}, "a control's line is a measurement of nothing"
    assert seen[TREE] == {"scenario": "tree", "interval": 0.02, "samples": 25.0,
                          "wall": 0.8, "peak": 320.0}


def test_a_failed_test_still_reports_what_it_observed_before_failing():
    assert probe.parse_obs(PYTEST_OUTPUT)[RECORD]["samples"] == 4.0


def test_the_summary_is_min_med_max_and_the_ratio_of_samples_to_periods():
    records = [{"obs": {RECORD: {"scenario": "record", "interval": 0.05, "samples": s,
                                 "wall": w, "peak": 159}}} for s, w in ((8, 0.5), (4, 0.8), (9, 0.5))]
    s = probe.summarize(records)[RECORD]
    assert s["n"] == 3 and s["scenario"] == "record"
    assert s["samples"] == (4, 8.0, 9.0)
    assert s["ratio"] == (0.25, 0.8, 0.9)          # 4 / (0.8 / 0.05) is the measured tail
    assert s["peak"] == (159, 159.0, 159)


def test_tally_reads_errors_apart_from_failures():
    assert probe.tally("x\n1 failed, 16 passed in 5.10s\n") == (16, 1, 0)
    assert probe.tally("x\n2 errors in 0.31s\n") == (0, 0, 2)
    assert probe.tally("x\n17 passed in 5.50s\n") == (17, 0, 0)


def test_a_hog_runs_until_stopped_and_is_then_gone():
    live = probe.start_hogs(1, None)
    try:
        time.sleep(0.3)
        assert live[0].poll() is None, "the hog is supposed to be running"
    finally:
        probe.stop_hogs(live)
    assert live[0].poll() is not None
    with pytest.raises(ProcessLookupError):
        os.kill(live[0].pid, 0)


def test_a_hog_expires_on_its_own_so_a_killed_probe_leaves_none_behind():
    h = subprocess.Popen(probe.hog_argv(lifetime_s=0.5), start_new_session=True)
    try:
        assert h.wait(timeout=60) == 0
    finally:
        if h.poll() is None:
            h.kill()
            h.wait()


@pytest.mark.parametrize("status, output, why", [
    (None, "TIMEOUT", "no verdict"),
    (3, "INTERNALERROR> ...", "not a verdict"),
    (4, "usage: ...", "not a verdict"),
    (1, "1 error in 0.31s\n", "not a verdict"),
    (0, "17 passed in 5.50s\n", r"without printing an \[obs\] line"),
])
def test_a_run_that_is_not_a_verdict_refuses_rather_than_reports(monkeypatch, status, output, why):
    monkeypatch.setattr(probe, "run", lambda argv, timeout: (status, output))
    with pytest.raises(probe.Refused, match=why):
        probe.probe(hogs=0, pin=None, runs=1, timeout=1.0)


def test_a_hog_that_dies_mid_run_refuses_rather_than_reports_that_run_as_load(monkeypatch):
    box = {}
    monkeypatch.setattr(probe, "start_hogs",
                        lambda n, pin: box.setdefault("live", probe_start_hogs(n, pin)))

    def the_hog_dies(argv, timeout):
        probe.stop_hogs(box["live"])
        return 0, PASSING

    monkeypatch.setattr(probe, "run", the_hog_dies)
    with pytest.raises(probe.Refused, match="died during run 0"):
        probe.probe(hogs=1, pin=None, runs=1, timeout=1.0)


def test_one_real_run_beside_one_real_hog_harvests_both_observations(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "BASTEMP", tmp_path / "pytest-tmp")
    r = probe.probe(hogs=1, pin=None, runs=1, timeout=300.0)["runs"][0]
    assert r["exit"] in (0, 1) and r["errored"] == 0, r
    if r["exit"] == 0:
        assert set(r["obs"]) == {TREE, RECORD} and r["passed"] >= 2, r

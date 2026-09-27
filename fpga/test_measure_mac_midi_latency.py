#!/usr/bin/env python3
"""fpga/test_measure_mac_midi_latency.py -- the latency apparatus's known answer (#322).

fpga/measure_mac_midi_latency.py computes receipt -> applied itself, because
T-LIVE-MIDI's check() cannot follow a real clock's re-anchoring. So its
pairing and endpoint arithmetic must first reproduce check()'s -- which pairs
through the independently built oracle -- on SIMULATED time, where both see
the same run. And its one control (a host stall) must move `on_time`.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_mac_midi_latency as mml                     # noqa: E402


def test_the_probe_reproduces_check_latency_on_simulated_time():
    mine, ref = mml.sim_reference(5.0)
    assert ref["verdict"] == "PASS", ref["reasons"]
    theirs = ref["latency"]
    assert mine["endpoint_ms"]["n"] == theirs["n"] > 200
    for k in ("min", "p50", "p95", "p99", "max"):
        assert abs(mine["endpoint_ms"][k] - theirs[k + "_ms"]) < 1e-9, k
    assert mine["props"] == {"target": True, "on_time": True, "load_admitted": True}
    assert mine["anchors_unpaired"] == 0
    assert max(map(abs, mine["map_error_frames"].values())) <= 1   # check()'s own tolerance


def test_one_host_stall_moves_on_time_on_simulated_time():
    """The control, deterministic: the same stall the Mac run injects."""
    mine, _ref = mml.sim_reference(5.0, stall_ms=25.0)
    assert not mine["props"]["on_time"]
    assert mine["anchors_off_due"] or mine["device_errors"] or mine["deadline_misses"]

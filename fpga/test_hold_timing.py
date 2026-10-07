#!/usr/bin/env python3
"""fpga/test_hold_timing.py -- the held-note hold, asserted on the DEVICE (#306).

The acceptance assertion is the device's: gate-on to gate-off, read from the
scripted device's executed-write log by register identity on its unwrapped
timeline (fpga/hold_timing.py), through the shipped CLI
(`uart_host.main(..., bridge_factory=Harness.factory)`). Never the planner's
numbers, never the host's own timing record.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hold_timing as ht                          # noqa: E402
import uart_host as uh                            # noqa: E402


# ---- the acceptance assertion: start red on the historical host --------------
@pytest.mark.parametrize("key", sorted(ht.HELD_COMMANDS))
def test_the_cli_delivers_the_requested_hold_on_the_device(key):
    """Requested 1920 frames; the device must hold 1920 within the host's
    own per-run bound, and that bound within the declared maximum. On the
    tree before #306 this FAILED on all three pinned commands: the device
    held 3155 frames (error +1235) and the host claimed no bound."""
    m = ht.run_condition(dict(key=key))
    assert m["verdict"] == ht.PASS, (m["reason"], m.get("host_record"))

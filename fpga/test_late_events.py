#!/usr/bin/env python3
"""fpga/test_late_events.py -- the late-event policy and the device model (#329).

The RTL half (iverilog, tb_uart_late.v) runs on the box through
`fpga/verify_late_events.py`; these are the parts that need no simulator."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import subprocess                                          # noqa: E402

import pytest                                              # noqa: E402

import late_event_policy as pol                            # noqa: E402
import verify_late_events as vle                           # noqa: E402


def test_policy_classification_at_the_window_edges():
    assert not pol.is_late(100, 101) and not pol.is_late(100, 100 + 32767)
    assert pol.is_late(100, 100) and pol.is_late(100, 100 + 32768) and pol.is_late(100, 99)
    assert not pol.is_late(65535, 0) and pol.is_late(0, 65535)          # across the wrap


def test_policy_late_is_next_frame_and_queue_order_holds():
    r = pol.expected([(100, 101), (100, 100), (100, 90), (100, 200), (101, 150)])
    assert [(o.verdict, o.executes) for o in r] == [
        ("ack", 101), ("late", 101), ("late", 102), ("ack", 200), ("drop-order", None)]


def test_policy_overflow_drops_and_recovers():
    ev = [(10 + i, 1000) for i in range(66)] + [(2000, 2005)]
    r = pol.expected(ev)
    assert [o.verdict for o in r[64:66]] == ["drop-full", "drop-full"]
    assert r[63].executes == 1031 and r[66].verdict == "ack" and r[66].executes == 2005


def test_the_repaired_model_meets_the_policy_on_every_scenario(tmp_path):
    assert vle.main(["--model-only", "--json", str(tmp_path / "v.json")]) == 0


def test_the_model_as_shipped_before_329_is_caught(tmp_path):
    """The control: the exact pre-#329 model, from git, executes accepted
    events in the wrong frame (or a revolution late, beyond the run)."""
    have = subprocess.run(["git", "-C", str(vle.ROOT), "cat-file", "-e",
                           vle.PRE_329_MODEL_COMMIT + "^{commit}"], capture_output=True)
    if have.returncode:
        pytest.skip("the pre-#329 model commit is not in this clone (shallow checkout)")
    assert vle.main(["--model-only", "--control", "model-revolution",
                     "--json", str(tmp_path / "c.json")]) == 0

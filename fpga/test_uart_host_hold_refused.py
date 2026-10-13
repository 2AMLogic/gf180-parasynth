"""fpga/test_uart_host_hold_refused.py -- issue #458.

`uart_host.py run --hold-frames N` used to die with a raw ValueError out of
plan() ("... is inside the wrap guard") when the gate-off due sat beyond the
+-32768-frame horizon one event can reason about. That is a REFUSED outcome
(exit 2), not a traceback -- and it must be reached BEFORE the note-on is
sent, or a refusal leaves a gate sounding.

Measured (test_wrap_guard_does_not_depend_on_the_anchor): the guard compares
the due's distance from its own acceptance frame, so it is a function of the
hold and the lead, NOT of where the free-running frame counter happens to
be. The issue's "intermittent, re-anchor and retry" hypothesis does not
hold, so no retry loop is built: it would burn round trips on a verdict that
cannot change. The sweep below is the evidence, and fails if that changes.

No hardware: the device is fpga/uart_device_sim on simulated time.
"""
from __future__ import annotations

import contextlib
import io

import pytest

import synth_top_model as stm
import uart_device_sim as dev
import uart_host as uh


def _bridge(epoch_frame=0):
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock, epoch_frame=epoch_frame)
    ser = dev.SimSerial(sim)
    return uh.Bridge.on_serial(ser, clock=clock), sim, ser


def _note_commands():
    on = [("write", f, s, a, d) for f, s, a, d in uh.note_writes(45, True)]
    return on + [("gate-off", 0, 0, stm.A_GATE_OFF, 0)]


def _run(bridge, hold):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return bridge.run(_note_commands(), hold_frames=hold, quiet=True)


def test_over_horizon_hold_is_refused_not_a_traceback():
    br, _sim, _ser = _bridge()
    with pytest.raises(uh.Refused) as ei:
        _run(br, 48000)
    assert "48000" in str(ei.value)


def test_refusal_happens_before_the_note_on_is_sent():
    br, sim, ser = _bridge()
    with pytest.raises(uh.Refused):
        _run(br, 48000)
    ser.run_until(ser.clock.t + 0.05)
    assert sim.writes == [], "a refused hold must not leave a gate sounding"


def test_main_exits_2_with_reason(capsys):
    br, _sim, _ser = _bridge()
    rc = uh.main(["--port", "sim", "run", "--note", "45",
                  "--hold-frames", "48000"], bridge_factory=lambda p, b: br)
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err


def test_wrap_guard_does_not_depend_on_the_anchor():
    # the control for "retry would help": sweep the counter across its range
    # and require an identical verdict at every anchor
    verdicts = set()
    for epoch in range(0, 1 << 16, 7919):
        br, _sim, _ser = _bridge(epoch_frame=epoch)
        try:
            _run(br, 48000)
            verdicts.add("ran")
        except uh.Refused:
            verdicts.add("refused")
    assert verdicts == {"refused"}


def test_in_horizon_hold_still_runs():
    # a refusal that fires on everything is not a guard
    br, _sim, _ser = _bridge()
    rows = _run(br, 24000)
    assert any(r.kind == "event" for r in rows)

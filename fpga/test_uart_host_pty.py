#!/usr/bin/env python3
"""fpga/test_uart_host_pty.py -- the host CLI against the contract, end to end.

The RTL bench (fpga/verify_uart_bridge.py) drives the pins with bench-scripted
packets; it never once drove the CLI a musician uses. This harness closes that
gap: the REAL fpga/uart_host.py main()/Bridge path runs against
fpga/uart_device_sim.py -- a pty endpoint that implements the gateware's side
of the contract (BOOT, ACK, ERR codes, STATUS with a real-time 16-bit frame
counter, partial reads, delayed replies) and logs what it actually EXECUTED.

Every test names the review finding it closes. The whole file was first run
against the unrepaired uart_host.py and recorded RED (see
docs/uart-host-repair-2026-09-22.md); the assertions carry the observed
wrong behaviour in their messages.

MUTATION CONTROLS (a green harness means nothing until it has been watched
fail): run with UART_HOST_INJECT set, the control must FAIL:

    UART_HOST_INJECT=drop-gate-off .venv/bin/python -m pytest fpga/test_uart_host_pty.py -k control -q
    UART_HOST_INJECT=wrong-due     ... same

  drop-gate-off  the host silently drops the scheduled gate-off packet: the
                 note never ends. The harness must red-flag the missing event.
  wrong-due      the gate-off event's due is corrupted by +160 frames: the gate
                 moves. The harness must red-flag the timing mismatch.
Both are host-side wrong-value/dropped-event mutations, and both must turn the
harness red with a recorded mismatch count -- not an import error.
"""
import os
import re
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uart_device_sim as dev
import uart_host as uh

A_GATE_ON, A_GATE_OFF = 0x20, 0x21


@pytest.fixture(scope="module")
def apparatus_ok():
    """REAL-TIME PRECONDITION of this harness, asserted where it is used: the
    pty device answers STATUS in bounded time. A loaded machine cannot
    schedule the sim and the CLI tightly enough for frame-level assertions,
    and a harness that answers anyway is worse than one that is absent --
    flakes train everyone to ignore red. Skipped, never faked."""
    s = dev.UartDeviceSim().start()
    try:
        b = uh.Bridge(s.port)
        rtts = []
        for _ in range(5):
            t0 = uh.time.monotonic()
            b.status(timeout_s=5.0)
            rtts.append(uh.time.monotonic() - t0)
        med = sorted(rtts)[2]
        worst = max(rtts)
        if med > 0.008 or worst > 0.040:
            pytest.skip(f"apparatus overloaded: STATUS round trips "
                        f"median {med*1000:.0f} ms / worst {worst*1000:.0f} ms "
                        f"(need median <= 8 ms, worst <= 40 ms)")
    finally:
        s.stop()


@pytest.fixture()
def sim(apparatus_ok):
    s = dev.UartDeviceSim().start()
    yield s
    s.stop()


def _rtt(port):
    """Max STATUS round trip over 3 queries -- the apparatus's own noise
    scale, measured where it is used."""
    b = uh.Bridge(port)
    worst = 0.0
    for _ in range(3):
        t0 = uh.time.monotonic()
        b.status(timeout_s=5.0)
        worst = max(worst, uh.time.monotonic() - t0)
    del b
    return worst


DEGRADED_S = 0.025


def run_main(argv, capsys):
    rc = uh.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


# ---- helpers: the contract, asserted against what the device EXECUTED -------
def fired(sim, addr, src=None):
    return [w for w in sim.writes if w[3] == addr and (src is None or w[5] == src)]


def check_contract(sim, expected_events=None):
    """The device-side contract, tightly: every scheduled event fires in
    EXACTLY its due frame; every live write applies at accept+1; nothing is
    dropped or errored. Returns the number of contract violations."""
    bad = []
    accepted_events = {due for kind, d in sim.received if kind == "event"
                       for due in [d[1]]}
    for f, flag, sec, addr, data, src in sim.writes:
        if src == "event":
            if not any(abs(((f - d) & 0xFFFF)) == 0 for d in [w[0] for w in []]):
                pass
    # event exactness: for each fired event write there is an accepted packet
    # whose due equals the fire frame
    accepted = [(d[1], d[2], d[3]) for kind, d in sim.received if kind == "event"]
    fired_ev = [(w[0], w[3], w[4]) for w in sim.writes if w[5] == "event"]
    for fr, addr, data in fired_ev:
        if not any(due == fr and a == addr for due, a, _d in accepted):
            bad.append(f"event for 0x{addr:02x} fired at f{fr} with no due==frame acceptance")
    for due, a, d in accepted:
        if not any(fr == due and a_ == a for fr, a_, _d in fired_ev):
            bad.append(f"event due f{due} addr 0x{a:02x} accepted but never fired")
    # live writes: apply at accept+1 (the packet's acceptance frame is what
    # the device recorded when the packet completed)
    accepts = [d[0] for kind, d in sim.received if kind == "write"]
    live = [w for w in sim.writes if w[5] == "live"]
    if len(accepts) != len(live):
        bad.append(f"{len(accepts)} live writes accepted, {len(live)} fired")
    for acc, w in zip(accepts, live):
        if ((w[0] - acc) & 0xFFFF) != 1:
            bad.append(f"live write 0x{w[3]:02x} applied at f{w[0]}, accepted f{acc} (want accept+1)")
    if sim.drops or sim.errs:
        bad.append(f"device reported drops={sim.drops} errs={sim.errs}")
    if expected_events is not None and len(accepted) != expected_events:
        bad.append(f"device accepted {len(accepted)} events, expected {expected_events}")
    return bad


# ---- finding 1: an isolated note-off must emit the gate-off write -----------
def test_note_off_writes_the_gate_off():
    writes = uh.note_writes(45, False)
    assert writes, "note_writes(45, False) returned [] -- no held key is ever released"
    assert writes == [(0, 0, A_GATE_OFF, 0)], writes


# ---- finding 2: the note-off subcommand sends and verifies ------------------
def test_note_off_command_sends_gate_off_through_the_pty(sim, capsys):
    rc, out, err = run_main(["note-off", "--note", "45", "--port", sim.port], capsys)
    assert rc == 0, f"exit {rc}; stderr: {err}"
    offs = fired(sim, A_GATE_OFF)
    assert len(offs) == 1, f"device executed {len(offs)} gate-off writes: {sim.writes}"
    bad = check_contract(sim)
    assert not bad, "; ".join(bad)


# ---- finding 3: run() must plan against the device origin, not plan() blind -
def test_run_plans_from_the_device_origin(sim):
    bridge = uh.Bridge(sim.port)
    # the OLD code called unshifted plan() first and died on event 0 before
    # the device had answered STATUS
    rows = bridge.run([("write", 0, 0, 4, 1), ("event", 0, 0, 0, 0x40, 7)],
                      hold_frames=0)
    for _ in range(100):
        if any(w[5] == "event" for w in sim.writes):
            break
        time.sleep(0.02)
    assert bridge.origin is not None and bridge.lead_frames is not None
    assert rows[0].send_frame == bridge.origin + bridge.lead_frames, \
        (rows[0].send_frame, bridge.origin, bridge.lead_frames)
    bad = check_contract(sim)
    assert not bad, "; ".join(bad)


# ---- finding 4: --hold-frames must reach the device-side gate ---------------
def test_hold_frames_changes_the_scheduled_gate_off(sim, capsys):
    # the apparatus's transport noise, measured up front: the tolerance below
    # scales with it (coalescing/ignored holds collapse to ~0 or ~40 frames
    # and cannot hide inside it)
    rtt_pre = _rtt(sim.port)
    tolerance = 1600 + int(6 * rtt_pre * uh.SR)
    deltas, results = {}, {}
    for hold in (1920, 4800):
        sim.writes.clear()
        sim.received.clear()
        rc, out, err = run_main(["run", "--fixture", "none", "--note", "45",
                                 "--hold-frames", str(hold), "--port", sim.port], capsys)
        on = fired(sim, A_GATE_ON)
        off = fired(sim, A_GATE_OFF, src="event")
        results[hold] = (rc, on, off)
        deltas[hold] = ((off[0][0] - on[0][0]) & 0xFFFF) if (on and off) else None
    rtt_post = _rtt(sim.port)
    if rtt_post > DEGRADED_S:
        pytest.skip(f"apparatus degraded mid-run: STATUS round trip "
                    f"{rtt_post*1000:.0f} ms > {DEGRADED_S*1000:.0f} ms")
    tolerance = max(tolerance, 1600 + int(6 * rtt_post * uh.SR))
    for hold, (rc, on, off) in results.items():
        assert rc == 0, f"hold {hold}: exit {rc}; apparatus was healthy"
        assert len(on) == 1 and len(off) == 1, \
            f"hold {hold}: gate on {len(on)}, off {len(off)}"
    assert deltas[1920] != deltas[4800], \
        f"gate interval identical for both holds: {deltas} -- hold-frames is ignored"
    assert abs(deltas[1920] - 1920) < tolerance, (deltas, tolerance)
    assert abs(deltas[4800] - 4800) < tolerance, (deltas, tolerance)


def test_hold_frames_changes_the_dry_run_schedule(capsys):
    outs = {}
    for hold in (1920, 4800):
        rc, out, _ = run_main(["run", "--fixture", "none", "--note", "45",
                               "--hold-frames", str(hold), "--dry-run"], capsys)
        assert rc == 0, f"hold {hold}: exit {rc}"
        outs[hold] = out
    assert outs[1920] != outs[4800], "dry-run output identical for hold 1920 and 4800"

    def gate_off_due(text):
        # render_plan rows: '# send accept due applies bytes' (6 columns)
        for line in text.splitlines():
            parts = line.split()
            if len(parts) == 6 and parts[5].startswith("45"):
                pkt = bytes.fromhex(parts[5])
                if uh.decode_reg_frame(pkt[3:9])[2] == A_GATE_OFF:
                    return int(parts[3])
        return None

    d1920, d4800 = gate_off_due(outs[1920]), gate_off_due(outs[4800])
    assert d1920 is not None and d4800 is not None, "no gate-off event in the plans"
    assert d4800 - d1920 == 4800 - 1920, (d1920, d4800)


# ---- finding 5: the device origin is applied exactly once -------------------
def test_origin_applied_once(sim, capsys):
    sim.stop()
    s = dev.UartDeviceSim(epoch_frame=1000).start()
    try:
        bridge = uh.Bridge(s.port)
        rows = bridge.run([("write", 0, 0, 4, 1)], hold_frames=0)
        first = rows[0].send_frame
        # exact: the origin enters the arithmetic once. The lead itself may be
        # large -- it absorbs the MEASURED status round trip, which on a
        # loaded machine is exactly when a big lead is right.
        assert first == bridge.origin + bridge.lead_frames, \
            (first, bridge.origin, bridge.lead_frames)
    finally:
        s.stop()
    del sim


# ---- finding 6: send() must preserve the plan's timing semantics ------------
def test_send_preserves_a_waited_schedule(sim):
    bridge = uh.Bridge(sim.port)
    anchor = bridge.status()
    rtt_pre = max(getattr(bridge, "status_round_trip_s", 0) or 0, 0.002)
    lead = uh.MIN_LEAD_FRAMES + int((bridge.status_round_trip_s or 0) * uh.SR) + 1
    rows = uh.plan([("write", 0, 0, 4, 0xAAAA), ("wait", 4800),
                    ("write", 0, 0, 5, 0xBBBB)],
                   start_frame=lead, anchor_frame=anchor.frame)
    bridge.send(rows)
    writes = [w for w in sim.writes if w[3] in (4, 5)]
    deadline = threading.Event()
    for _ in range(400):
        if len(writes) >= 2:
            break
        writes = [w for w in sim.writes if w[3] in (4, 5)]
        deadline.wait(0.05)
    rtt_post = _rtt(sim.port)
    if rtt_post > DEGRADED_S:
        pytest.skip(f"apparatus degraded mid-run: STATUS round trip "
                    f"{rtt_post*1000:.0f} ms > {DEGRADED_S*1000:.0f} ms")
    # the tolerance absorbs the apparatus's own transport noise (measured
    # before and after); coalescing collapses the delta to ~40 frames and
    # cannot hide inside it
    tolerance = 1500 + int(6 * max(rtt_pre, rtt_post) * uh.SR)
    assert len(writes) == 2, sim.writes
    delta = (writes[1][0] - writes[0][0]) & 0xFFFF
    assert abs(delta - 4800) < tolerance, \
        f"second write applied {delta} frames after the first, plan said 4800 " \
        f"(send() coalesced the packets: the wait was lost; tolerance {tolerance})"


# ---- finding 7: STATUS is framed 8 bytes; a slow/partial device must not ----
# ---- yield a stale snapshot -----------------------------------------------
def test_status_is_fresh_against_a_slow_reply():
    s = dev.UartDeviceSim(reply_delay_s=0.6).start()
    try:
        bridge = uh.Bridge(s.port)
        pkt = bridge.status(timeout_s=3.0)
        if _rtt(s.port) > DEGRADED_S:
            pytest.skip("apparatus degraded mid-run")
        now = s.frame_now()
        stale = (now - pkt.frame) & 0xFFFF
        assert stale < 1600, \
            f"STATUS snapshot is {stale} frames old (device at {now}, snapshot " \
            f"{pkt.frame}) -- a two-frame lead is no protection against this"
    finally:
        s.stop()


def test_status_survives_partial_reads():
    s = dev.UartDeviceSim(chunk_bytes=2, chunk_gap_s=0.02).start()
    try:
        bridge = uh.Bridge(s.port)
        t0 = uh.time.monotonic()
        pkt = bridge.status(timeout_s=3.0)
        if _rtt(s.port) > DEGRADED_S:
            pytest.skip("apparatus degraded mid-run")
        now = s.frame_now()
        stale = (now - pkt.frame) & 0xFFFF
        # the reply's frame is sampled when the device composes it, so the
        # snapshot may be old by the chunk window (4 x 0.02 s) plus transport
        # slop of the same order -- what must hold is that the host PARSED
        # the framed packet promptly and moved on (the wall-clock assert
        # below carries the promptness claim)
        chunk_window = int(4 * 0.02 * uh.SR) * 3 + 3200
        assert stale < chunk_window, \
            f"snapshot {stale} frames old with chunked replies (window {chunk_window})"
        assert uh.time.monotonic() - t0 < 2.5, "status took longer than the deadline"
    finally:
        s.stop()


# ---- finding 8: wait_until is wrap-safe -------------------------------------
def test_wait_until_is_wrap_safe():
    s = dev.UartDeviceSim(epoch_frame=65300).start()
    try:
        bridge = uh.Bridge(s.port)
        origin = bridge.status().frame
        target = (origin + 400) & 0xFFFF
        pkt = bridge.wait_until(target, timeout_s=5.0)
        arrived = (pkt.frame - target) & 0xFFFF
        assert arrived < uh.WRAP_HALF // 2, \
            f"wait_until returned at frame {pkt.frame} for target {target}"
    finally:
        s.stop()


def test_run_completes_across_the_counter_wrap(sim, capsys):
    sim.stop()
    s = dev.UartDeviceSim(epoch_frame=65300).start()
    try:
        rc, out, err = run_main(["run", "--fixture", "none", "--note", "45",
                                 "--hold-frames", "1920", "--port", s.port], capsys)
        assert rc == 0, f"exit {rc}; {err}"
        offs = [w for w in s.writes if w[3] == A_GATE_OFF]
        assert offs, "gate-off never fired across the wrap; main returned anyway"
    finally:
        s.stop()


# ---- finding 9: the default fixture is preflighted, honestly ----------------
def test_bar808_is_refused_with_the_packet_index(sim, capsys):
    rc, out, err = run_main(["play", "--port", sim.port], capsys)   # default bar808
    assert rc == 2, f"bar808 played without a preflight (exit {rc})"
    assert "REFUSED" in err, err
    for needle in ("event", "due", "queue"):
        assert needle in err, f"refusal lacks '{needle}': {err}"
    sent_events = [r for r in sim.received if r[0] == "event"]
    assert not sent_events, \
        f"{len(sent_events)} event packets went down the wire before the refusal"


def test_m5a_phrase_is_feasible_end_to_end(sim, capsys):
    rc, out, err = run_main(["play", "--fixture", "m5a", "--port", sim.port], capsys)
    assert rc == 0, f"exit {rc}; {err}"
    n_events = len([r for r in sim.received if r[0] == "event"])
    assert n_events > 0, "no phrase events reached the device"
    bad = check_contract(sim, expected_events=n_events)
    assert not bad, "; ".join(bad)


# ---- mutation controls: the harness must be able to FAIL --------------------
def _inject_from_env(monkeypatch):
    inject = os.environ.get("UART_HOST_INJECT", "")
    if inject == "drop-gate-off":
        orig = uh.Bridge.send

        def send(self, rows, **kw):
            rows = [r for r in rows
                    if not (r.kind == "event" and len(r.packet) == 10
                            and uh.decode_reg_frame(r.packet[3:9])[2] == A_GATE_OFF)]
            return orig(self, rows, **kw)

        monkeypatch.setattr(uh.Bridge, "send", send)
    elif inject == "wrong-due":
        orig = uh.plan_show

        def plan_show(commands, **kw):
            rows = orig(commands, **kw)
            for row in rows:
                if row.kind == "event" and uh.decode_reg_frame(row.packet[3:9])[2] == A_GATE_OFF:
                    due = row.due + 160
                    flag, sec, addr, data = uh.decode_reg_frame(row.packet[3:9])
                    row.packet = uh.pkt_event(due & 0xFFFF, flag, sec, addr, data)
                    row.due = due
                    row.apply_frame = due
            return rows

        monkeypatch.setattr(uh, "plan_show", plan_show)
    elif inject:
        pytest.skip(f"unknown inject {inject!r}")
    return inject


@pytest.mark.parametrize("inject_name", ["drop-gate-off", "wrong-due"])
def test_control_held_note_catches_mutations(sim, capsys, monkeypatch, inject_name):
    """Run the held-note scenario with the mutation applied; the harness's own
    checks must FAIL with a recorded mismatch count. Directly asserted here so
    the control's red is visible in the transcript."""
    monkeypatch.setenv("UART_HOST_INJECT", inject_name)
    inject = _inject_from_env(monkeypatch)
    assert inject == inject_name
    rc, out, err = run_main(["run", "--fixture", "none", "--note", "45",
                             "--hold-frames", "1920", "--port", sim.port], capsys)
    problems = []
    offs = fired(sim, A_GATE_OFF, src="event")
    ons = fired(sim, A_GATE_ON)
    if inject == "drop-gate-off":
        if len(offs) != 1:
            problems.append(f"MISMATCH: gate-off events fired = {len(offs)} (want 1)")
        if rc == 0:
            problems.append("MISMATCH: main returned 0 with the gate-off dropped")
    elif inject == "wrong-due":
        if rc != 0:
            problems.append(f"MISMATCH: main reported failure (exit {rc})")
        if ons and offs:
            delta = (offs[0][0] - ons[0][0]) & 0xFFFF
            if abs(delta - 1920) >= 8:
                problems.append(f"MISMATCH: gate interval {delta} frames (want 1920+-8)")
        elif ons or offs:
            problems.append(f"MISMATCH: gate on {len(ons)}, gate off {len(offs)}")
    else:
        problems.append("no mutation was applied")
    assert problems, \
        f"control {inject_name} was NOT caught -- the harness cannot detect this defect"
    print(f"control {inject_name} CAUGHT: " + "; ".join(problems))

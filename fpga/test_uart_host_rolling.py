"""fpga/test_uart_host_rolling.py -- the rolling scheduler, deterministically.

Ordinary-CI tests for musical-length playback. The device is the contract
mirror (fpga/uart_device_sim.UartDeviceSim) on SIMULATED time through
SimSerial: no pty, no thread, no wall clock, so every scheduling decision
is reproducible and independent of the machine running the test. The pty
boundary keeps its own smoke run (test_uart_host_pty.py); these check the
host's LOGIC -- window cuts, horizon, queue bound, setup/performance split,
refusal on loss -- against a device that answers what the contract says.

Expectations come from the fixture (fpga/verify_rolling_playback.intended),
never from the planner under test.
"""
from __future__ import annotations

import contextlib
import io

import pytest

import uart_device_sim as dev
import uart_host as uh
import verify_rolling_playback as vrp


def fixture_commands(name):
    static, events, _end = uh.phrase_static_and_events(name)
    cmds = [("write", f, s, a, d) for f, s, a, d in static]
    cmds += [("event", d, f, s, a, dd) for d, f, s, a, dd in events]
    return cmds


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


# ---- the split: setup is what load() emitted, by position --------------------
def test_setup_is_the_load_image_and_accents_stay_in_the_music():
    for name in ("bar808-full", "demo"):
        static, timed, _n, host = uh.fixture_split(name)
        first, end, _frame = host.load_span
        assert len(static) == end - first, name
        # hits write accents too: those are music, at their step
        accents = [w for w in timed if w.tag == "accent"]
        assert accents and min(w.frame for w in accents) > 0, name


def test_sim_decodes_sec_where_the_encoder_puts_it():
    # regression: the sim read SEC from bit 6; every drum write was logged
    # as a voice write
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(clock=clock)
    ser = dev.SimSerial(sim)
    ser.write(uh.pkt_write(0, 1, 0x40, 0x1234))
    ser.write(uh.pkt_write(1, 0, 0x41, 0x5678))
    ser.run_until(0.01)
    assert [(w[1], w[2], w[3], w[4]) for w in sim.writes] == \
        [(0, 1, 0x40, 0x1234), (1, 0, 0x41, 0x5678)]


# ---- the window cut, without a device ----------------------------------------
def test_virtual_plan_delivers_every_event_in_order():
    for name in ("bar808-full", "demo"):
        static, events, _end = uh.phrase_static_and_events(name)
        rows, windows = uh.plan_rolling_virtual(fixture_commands(name))
        ev = [r for r in rows if r.kind == "event"]
        assert len(ev) == len(events), name
        dues = [r.due for r in ev]
        assert dues == sorted(dues), name
        assert windows >= len(events) // uh.ROLLING_BATCH_CAP
        wr = [r for r in rows if r.kind == "write"]
        assert len(wr) == len(static)
        assert max(r.send_frame for r in wr) <= min(r.send_frame for r in ev)


def test_window_cut_respects_wire_horizon_and_queue():
    F = uh.event_packet_frames(uh.DEFAULT_BAUD)
    lead = uh.MIN_LEAD_FRAMES + 1
    bound = uh.EVENT_QUEUE_DEPTH - uh.ROLLING_QUEUE_MARGIN
    # 200 events one packet apart, far enough out that the queue binds first
    ev_abs = [(50_000 + j * F, ("event", 0, 0, 0, 0x20, 0)) for j in range(200)]
    batch, wait = uh.rolling_batch(ev_abs, 0, 0, lead)
    assert batch == [] and wait == ev_abs[0][0] - uh.ROLLING_HORIZON_FRAMES
    origin = wait
    batch, wait = uh.rolling_batch(ev_abs, 0, origin, lead)
    assert 0 < len(batch) <= uh.ROLLING_BATCH_CAP and wait is None
    for j, (due, _c) in enumerate(batch):
        assert due - origin >= lead + (j + 1) * F + uh.ROLLING_PACKET_SLACK_FRAMES
        assert due - origin <= uh.ROLLING_HORIZON_FRAMES
    # everything just sent is still queued: the next cut must stop at the
    # bound and name the frame a slot frees
    inflight = [d for d, _c in batch]
    more, wait = uh.rolling_batch(ev_abs, len(batch), origin + len(batch) * F,
                                  lead, inflight=inflight)
    assert len(inflight) + len(more) <= bound
    if not more:
        assert wait == inflight[0] + 1 - F


def test_an_event_the_wire_cannot_reach_is_refused_not_waited_for():
    lead = uh.MIN_LEAD_FRAMES + 1
    with pytest.raises(ValueError, match="cannot be accepted before its due"):
        uh.rolling_batch([(1000 + 5, ("event", 0, 0, 0, 0x20, 0))], 0, 1000, lead)


# ---- the shipped CLI, rolled, on the contract device --------------------------
@pytest.mark.parametrize("name", ["bar808-full", "demo"])
@pytest.mark.parametrize("epoch", [0, 65300])
def test_cli_plays_the_fixture_exactly_across_wraps(name, epoch):
    r = vrp.check(vrp.run_cli(name, epoch=epoch))
    assert r["ok"], r["reasons"]
    m = r["metrics"]
    assert m["counter_wraps_crossed"] >= 3
    assert m["peak_queue"] <= uh.EVENT_QUEUE_DEPTH - uh.ROLLING_QUEUE_MARGIN
    assert m["min_deadline_slack_frames"] > uh.ROLLING_PACKET_SLACK_FRAMES


@pytest.mark.parametrize("control", sorted(vrp.CONTROLS))
def test_every_control_turns_the_verifier_red_for_its_reason(control):
    c = vrp.run_control(control)
    assert c["caught"], c


def test_note_off_during_queued_traffic_lands_at_accept_plus_one():
    """A live gate-off sent while a window's events fill the queue applies
    at accept+1, and every scheduled event still lands on its frame."""
    h = vrp.Harness()
    want = vrp.intended("demo")
    gate_off = uh.note_writes(45, False)
    orig_send = uh.Bridge.send
    state = {"windows": 0, "evq_at_send": None}

    def send(self, rows, **kw):
        orig_send(self, rows, **kw)
        if any(r.kind == "event" for r in rows):
            state["windows"] += 1
            if state["windows"] == 3:
                state["evq_at_send"] = h.sim.evq_count + sum(
                    1 for b in h.sim._wire_buf if b == dev.OP_EVENT)
                for f, s, a, d in gate_off:
                    self.ser.write(uh.pkt_write(f, s, a, d))
    br = uh.Bridge.on_serial(h.ser, clock=h.clock)
    uh.Bridge.send = send
    try:
        quiet(br.run_rolling, [c for c in _run_commands("demo")], quiet=True)
    finally:
        uh.Bridge.send = orig_send
    h.ser.run_until(h.clock.t + 1.0)
    assert state["evq_at_send"] and state["evq_at_send"] > 10
    offs = [(f, a, d) for f, s, a, d in gate_off]
    recv = [v for k, v in h.sim.received if k == "write"]
    hits = [(w[0], (w[3], w[4])) for w in h.sim.writes if w[5] == "live"
            and (w[1], w[3], w[4]) in offs]
    assert len(hits) == len(gate_off)
    for frame, (addr, data) in hits:
        acc = next(f for f, a, d in recv if (a, d) == (addr, data))
        assert frame == (acc + 1) & 0xFFFF
    ev = [w for w in h.sim.writes if w[5] == "event"]
    p0 = br.performance_origin
    got = [f - p0 for f in vrp._unwrap([w[0] for w in ev], p0)]
    assert got == [t[0] for t in want["timed"]]


def _run_commands(name):
    cmds = [("write", *w) for w in uh.voice_image_writes(None)]
    return cmds + fixture_commands(name)


def test_musical_fixtures_roll_and_short_ones_do_not():
    for name in ("bar808-full", "demo"):
        assert uh.rolling_needed(fixture_commands(name)), name
    for name in ("m5a", "bar808"):
        events, _end = uh.phrase_events(name)
        cmds = [("event", d, f, s, a, dd) for d, f, s, a, dd in events]
        assert not uh.rolling_needed(cmds), name


def test_dry_run_renders_the_rolling_schedule():
    assert quiet(uh.main, ["--dry-run", "run", "--fixture", "bar808-full"]) == 0


def test_compressed_bar808_fits_once_setup_is_separated(capsys):
    """The compressed stress fixture's old refusal (peak demand 189 vs 64)
    was the 182-write load image due at t=0. Separated, it preloads."""
    assert uh.main(["--dry-run", "play", "--fixture", "bar808"]) == 0
    out = capsys.readouterr().out
    assert "FEASIBLE (preload)" in out, out
    assert uh.main(["--dry-run", "play", "--fixture", "bar808",
                    "--baud", "19200"]) == 2


# ---- occupancy across windows, not per window ---------------------------------
def _schedule_peak(rows):
    """Most events the device holds at once over the WHOLE schedule: each
    event occupies the queue from its acceptance to its due."""
    ev = [r for r in rows if r.kind == "event"]
    return max(sum(1 for q in ev if q.accept_frame <= r.accept_frame <= q.due)
               for r in ev)


def _dense_far_stream(n=240, spacing=45, first=20_000):
    # every due far out and inside the horizon, spaced just wider than one
    # packet: each window alone fits, and windows pile up unless the cut
    # counts what earlier windows left queued (the reviewer's 100-slot case)
    return [("event", first + j * spacing, 0, 0, 0x20, j) for j in range(n)]


def test_whole_schedule_occupancy_stays_within_the_bound():
    bound = uh.EVENT_QUEUE_DEPTH - uh.ROLLING_QUEUE_MARGIN
    for name in ("bar808-full", "demo"):
        rows, _w = uh.plan_rolling_virtual(fixture_commands(name))
        assert _schedule_peak(rows) <= bound, name
    rows, _w = uh.plan_rolling_virtual(_dense_far_stream())
    assert _schedule_peak(rows) <= bound


def test_queue_unaware_cut_overfills_across_windows(monkeypatch):
    # the control for the test above: per-window checks alone pass every
    # window and still ask the device for more than 64 slots
    monkeypatch.setattr(uh, "INJECT_BUGS", {"ROLL_QUEUE_UNAWARE"})
    rows, _w = uh.plan_rolling_virtual(_dense_far_stream())
    assert _schedule_peak(rows) > uh.EVENT_QUEUE_DEPTH


# ---- the WIP's musical control, on the faithful device ------------------------
def test_frozen_device_refuses_rather_than_retimes():
    """The device's counter stops 1.5 s into the phrase (host clock runs
    on). The roller must REFUSE -- shifting t=0 to rescue the remaining
    dues would be a musical edit -- and nothing after the freeze fires."""
    h = vrp.Harness()
    h.sim.frame_freeze_t = 1.5
    br = uh.Bridge.on_serial(h.ser, clock=h.clock)
    with pytest.raises(uh.Refused, match="frame counter"):
        quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)
    assert h.sim.status_requests > 0 and not h.sim.errors
    fired = [w for w in h.sim.writes if w[5] == "event"]
    assert 0 < len(fired) < len(vrp.intended("bar808-full")["timed"])

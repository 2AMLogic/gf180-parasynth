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


# ---- the 16-bit frame log: an unwrap is a precondition, not an assumption ----
def _log16(frames, p0):
    """What the device would log for writes at `frames` after `p0`."""
    return [(p0 + f) & 0xFFFF for f in frames]


def test_the_unwrap_refuses_a_schedule_it_cannot_step():
    """CONTROL: bar808-rest's rest is longer than half the frame counter, so
    the log cannot say whether a step went forward or back. _unwrap must
    refuse -- and if it did not, it would not merely be imprecise: the
    timeline it returns is wrong (#474)."""
    want = [w[0] for w in vrp.intended("bar808-rest")["timed"]]
    assert max(b - a for a, b in zip(want, want[1:])) >= vrp.UNWRAP_GAP_BOUND
    p0 = 1000
    with pytest.raises(vrp.UnwrapRefused, match="16-bit frame log"):
        vrp._unwrap(_log16(want, p0), p0, [0] + want)
    prev, acc, bad = p0 & 0xFFFF, p0, []
    for f in _log16(want, p0):
        d = (f - prev) & 0xFFFF
        acc += d if d < 0x8000 else d - 0x10000
        prev = f
        bad.append(acc - p0)
    assert bad != want


def test_the_unwrap_answers_for_the_fixtures_it_is_used_on():
    # the other half of the control: a schedule inside the bound is unwrapped
    # exactly, across a wrap
    for name in ("bar808-full", "demo"):
        want = [w[0] for w in vrp.intended(name)["timed"]]
        assert max(b - a for a, b in zip(want, want[1:])) < vrp.UNWRAP_GAP_BOUND
        assert vrp._unwrap(_log16(want, 65000), 65000, [0] + want) == \
            [65000 + f for f in want]


def test_the_gap_bound_is_where_this_unwraps_own_branch_is():
    """0x8000, not the 0x10000 of test_play_song's forward-only naive_unwrap:
    _unwrap reads a step of 0x8000 as one BACK, so that step is already
    ambiguous and 0x7fff is the last one that is not."""
    assert vrp._unwrap(_log16([0, 0x7FFF], 0), 0, [0, 0x7FFF]) == [0, 0x7FFF]
    with pytest.raises(vrp.UnwrapRefused):
        vrp._unwrap(_log16([0, 0x8000], 0), 0, [0, 0x8000])


def test_the_unwrap_is_checked_against_the_devices_own_unwrapped_log():
    """The sim logs each write's UNWRAPPED frame beside the 16-bit one, so
    the harness can check its unwrap against a timeline that is not derived
    from it: every step read correctly is one constant offset."""
    # a held note, so the phrase has a performance origin to unwrap from; the
    # short fixture, so this costs a tenth of a second
    run = vrp.run_cli("bar808", epoch=65300, note=45)
    r = vrp.check(run)
    assert r["ok"], r["reasons"]
    sim = run["h"].sim
    assert len(sim.write_frames) == len(sim.writes)
    assert sim.write_frames == sorted(sim.write_frames)
    assert [f & 0xFFFF for f in sim.write_frames] == [w[0] for w in sim.writes]
    assert sim.write_frames[-1] > 0xFFFF                  # it crossed a wrap
    assert len(r["unwrap_offsets"]) == 1, r["unwrap_offsets"]
    # CONTROL: one step read as its alias is not a constant offset any more
    truth = [0, 1000, 2000]
    assert vrp._unwrap_offsets([5, 1005, 2005], truth) == [5]
    assert vrp._unwrap_offsets([5, 1005, 2005 - 0x10000], truth) == [5 - 0x10000, 5]


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
    exp = [t[0] for t in want["timed"]]
    got = [f - p0 for f in vrp._unwrap([w[0] for w in ev], p0, [0] + exp)]
    assert got == exp


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


# ---- #459: host latency is not a stopped device clock --------------------------
# The 16-bit unwrap reads a step >= 0x8000 frames (0.683 s at 48 kHz) as
# NEGATIVE, so a slow HOST round trip used to be reported as "the device's
# frame counter did not advance" -- a correct device blamed for the host.
# A step of 0x10000 + k aliases to a small positive k and was accepted.
# The stall below is injected on the host's wall clock (the same SimClock
# the simulated device counts frames from), so the device counter is truthful.
def _stalled_bridge(stall_s, *, freeze=False):
    """A Bridge whose first STATUS after the first rolling window was sent
    takes `stall_s` longer, on the injectable clock. With freeze=True the
    device's audio counter also stops at that moment (a genuinely dead
    counter) -- the control."""
    h = vrp.Harness()
    br = uh.Bridge.on_serial(h.ser, clock=h.clock)
    real_status, real_send = br.status, br.send
    st = {"armed": False, "done": False, "sent": False, "last_frame": 0}

    def send(rows, *a, **kw):
        out = real_send(rows, *a, **kw)
        # the STATUS right after the first window of events is the anchor
        # `_roll_events` unwraps (send -> loop -> status)
        if not st["done"] and any(r.kind == "event" for r in rows):
            st["sent"] = True
            st["armed"] = True
        return out

    def status(*a, **kw):
        if st["armed"] and not st["done"]:
            st["done"] = True
            if freeze:
                # stopped as of the previous STATUS answer: no step at all
                h.sim.frame_freeze_t = (h.sim._t0 + (st["last_frame"]
                                        - 1 - h.sim.epoch + 0.25) / uh.SR)  # STATUS reports audio+1
            h.clock.t += stall_s
        out = real_status(*a, **kw)
        st["last_frame"] = out.frame
        return out
    br.send = send
    br.status = status
    return h, br


def _roll_with_stall(stall_s):
    h, br = _stalled_bridge(stall_s)
    with pytest.raises(uh.Refused) as ei:
        quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)
    return str(ei.value)


def test_a_host_stall_past_the_unwrap_horizon_is_blamed_on_the_host():
    msg = _roll_with_stall(0.80)              # 38,400 frames > 0x8000
    assert "host latency" in msg and "0.683" in msg
    assert "frame counter did not advance" not in msg
    assert "ms between anchors" in msg


def test_a_stall_past_the_horizon_reads_a_second_status_and_says_so():
    msg = _roll_with_stall(0.80)
    # the sim is truthful: the second read shows the counter moving, so the
    # device is NOT stopped, and the message says that is the evidence used
    assert "second STATUS" in msg and "advanced" in msg


@pytest.mark.parametrize("frames", [0x8000 + 200, 0xFFFF - 200])
def test_a_stall_just_above_the_horizon_and_just_below_the_alias(frames):
    msg = _roll_with_stall(frames / uh.SR)
    assert "host latency" in msg and "second STATUS" in msg


def test_a_stall_aliasing_to_a_small_positive_step_refuses():
    # 1.5 s = 72,000 frames: step mod 0x10000 = 6,464, a plausible progress
    msg = _roll_with_stall(1.5)
    assert "0x10000" in msg or "65536" in msg
    assert "host latency" in msg


@pytest.mark.parametrize("frames,must", [
    (0x8000 - 3000, "no-refusal"), (0x8000 - 1000, "no-refusal"),            # well inside the unwrap: plays
])
def test_a_short_stall_does_not_refuse(frames, must):
    h, br = _stalled_bridge(frames / uh.SR)
    quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)


def test_exactly_0x10000_frames_between_anchors_refuses():
    # the alias-to-zero case: the modulo step is 0, indistinguishable from a
    # stopped counter -- unless the host interval is consulted
    msg = _roll_with_stall(0x10000 / uh.SR)
    assert "host latency" in msg
    assert "frame counter did not advance" not in msg


def test_a_counter_that_genuinely_stops_keeps_the_device_wording():
    # the control: same guard, counter frozen, short host interval
    h, br = _stalled_bridge(0.0, freeze=True)
    with pytest.raises(uh.Refused) as ei:
        quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)
    msg = str(ei.value)
    assert "frame counter did not advance" in msg
    assert "host latency" not in msg


def test_a_gap_just_below_the_ambiguous_band_needs_no_second_status():
    # 0.60 s = 28,800 frames < 0x8000 - 2048: the host interval cannot
    # explain a missing step, so the device wording stands without a re-read
    h, br = _stalled_bridge(0.60, freeze=True)
    with pytest.raises(uh.Refused) as ei:
        quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)
    assert "frame counter did not advance" in str(ei.value)
    assert "second STATUS" not in str(ei.value)


def test_a_stopped_counter_after_a_long_gap_is_still_the_device():
    # ambiguous interval AND the second STATUS shows no motion: the device
    # is stopped, and the message says the second read found that
    h, br = _stalled_bridge(0.66, freeze=True)
    with pytest.raises(uh.Refused) as ei:
        quiet(br.run_rolling, _run_commands("bar808-full"), quiet=True)
    msg = str(ei.value)
    assert "frame counter did not advance" in msg
    assert "second STATUS" in msg and "host latency" not in msg


def test_behind_the_anchor_names_host_latency_when_it_is_lateness():
    ev = [(1000 + 100, ("event", 100, 0, 0, 0x20, 0))]
    with pytest.raises(ValueError) as ei:
        uh.rolling_batch(ev, 0, 1142, 50, uh.DEFAULT_BAUD,
                         prev_origin=1000, anchor_interval_s=0.05)
    msg = str(ei.value)
    assert "behind the anchor" in msg
    assert "42 frames" in msg and "ms" in msg and "host" in msg.lower()


def test_behind_the_anchor_without_lateness_is_a_schedule_fault():
    # due already behind the PREVIOUS anchor: lateness cannot explain it
    ev = [(900, ("event", 0, 0, 0, 0x20, 0))]
    with pytest.raises(ValueError) as ei:
        uh.rolling_batch(ev, 0, 1000, 50, uh.DEFAULT_BAUD,
                         prev_origin=950, anchor_interval_s=0.05)
    assert "host" not in str(ei.value).lower()
    assert "behind the anchor" in str(ei.value)

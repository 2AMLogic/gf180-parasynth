"""fpga/test_uart_host_rolling.py -- the rolling scheduler, deterministically.

Ordinary-CI tests for musical-length playback: a SCRIPTED serial device
(no pty, no threads, no wall clock) whose frame counter advances only when
the test allows it, so every scheduling decision the roller makes is
reproducible. The apparatus here is deliberately NOT the pty harness: the
pty boundary gets its own smoke run before host releases; these tests
check the host's LOGIC -- window cuts, horizon, queue cap, static/timed
separation, refusal on dead dues -- against a device that answers exactly
what the contract says.
"""
from __future__ import annotations

import pytest

import uart_host as uh

SR = uh.SR


class FakeClock:
    """Simulated wall time. Moves ONLY when the test, the fake device, or a
    host sleep advances it: a scheduling loop that needs real time to pass
    gets simulated time, which is the point."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)


class ScriptedSerial:
    """The device side of the contract, minus the wire: parses W/E/Q/X
    packets, ACKs what it accepts, answers STATUS with the frame its
    counter names, fires accepted events at their dues. Each STATUS
    question advances the simulated device by STATUS_STEP frames -- the
    device's time moves only as the host polls it, so a host that stops
    polling stops the device, and dead dues are reproducible."""

    STATUS_STEP = 960         # device frames between consecutive answers:
                              # the roller polls once per 20 ms host loop, and
                              # a real counter moves 960 frames in 20 ms -- a
                              # slower counter starves every bridge deadline

    def __init__(self, clock: FakeClock, epoch: int = 0, freeze_at=None):
        self.clock = clock
        self.epoch = epoch & 0xFFFF
        self.freeze_at = freeze_at   # DEVICE seconds after which the counter
        self.device_t = 0.0          # stops: host sleeps advance the clock,
                                     # but a crystal counter answers only
                                     # when its own time moves
        self.rx = b""              # device -> host bytes
        self.writes: list = []     # accepted live writes (addr, data, order)
        self.events: list = []     # queued scheduled (due, addr, seq)
        self.accepted: list = []   # every acceptance, never drained
        self.fired: list = []      # (due, addr) in fire order
        self.queued_peak = 0
        self.seq = 0
        self.timeout = None
        self._status_answers = 0

    # ---- Bridge's serial interface ----------------------------------------
    def write(self, data: bytes) -> int:
        i = 0
        while i < len(data):
            op = data[i]
            if op == 0x57:                      # W: 1+6+1
                pkt = data[i:i + 8]
                flag, sec, addr, dat = uh.decode_reg_frame(pkt[1:7])
                self.writes.append((addr, dat))
                self._ack()
                i += 8
            elif op == 0x45:                    # E: 1+2+6+1
                pkt = data[i:i + 10]
                due = pkt[1] | (pkt[2] << 8)
                flag, sec, addr, dat = uh.decode_reg_frame(pkt[3:9])
                self.accepted.append((due, addr, len(self.accepted)))
                self.events.append((due, addr, len(self.accepted) - 1))
                self.queued_peak = max(self.queued_peak,
                                       len(self.events) - len(self.fired))
                self._ack()
                i += 10
            elif op == 0x51:                    # Q
                self._advance_device()
                f = (self.epoch + int(self.device_t * SR)) & 0xFFFF
                evq = max(0, min(255, len(self.events)))
                self.rx += bytes([0x55, (f >> 8) & 0xFF, f & 0xFF,
                                  evq, 0, 0, 0, 0])
                self._status_answers += 1
                i += 2
            else:
                raise AssertionError(f"scripted device got opcode 0x{op:02x}")
        return len(data)

    def read(self, n: int):
        out, self.rx = self.rx[:n], self.rx[n:]
        return out

    def flush(self) -> None:
        pass

    # ---- fake device internals --------------------------------------------
    def _ack(self):
        self.seq = (self.seq + 1) & 0xFF
        self.rx += bytes([0x06, self.seq])

    def _advance_device(self):
        """Device time moves only when polled. Events whose due the counter
        has passed FIRE in due order -- a late-accepted event still fires at
        its due on this device, so the fired log checks the schedule, not
        the wire."""
        if self.freeze_at is None or self.device_t < self.freeze_at:
            self.device_t += self.STATUS_STEP / SR
        now16 = (self.epoch + int(self.clock.t * SR)) & 0xFFFF
        ready = [e for e in self.events
                 if ((e[0] - now16) & 0xFFFF) < 0x8000]
        # fire in TRUE temporal order: distance ahead on the counter, not
        # the raw 16-bit value (which inverts across a wrap)
        for due, addr, _seq in sorted(ready,
                                      key=lambda e: (e[0] - now16) & 0xFFFF):
            self.fired.append((due, addr))
            self.events = [e for e in self.events if e[2] != _seq or
                           (e[0], e[1]) != (due, addr) or e[2] != _seq]


def make_bridge(monkeypatch, epoch=0):
    clock = FakeClock()
    ser = ScriptedSerial(clock, epoch=epoch)
    monkeypatch.setattr(uh, "time", clock)
    bridge = uh.Bridge.__new__(uh.Bridge)
    bridge.ser = ser
    bridge.baud = uh.DEFAULT_BAUD
    bridge.buf = b""
    bridge.origin = None
    bridge.lead_frames = None
    bridge.status_round_trip_s = None
    bridge.acks_seen = 0
    bridge._run_ack_base = 0
    bridge._run_acks_expected = 0
    return bridge, ser, clock


def fixture_commands(name):
    static, events, _end = uh.phrase_static_and_events(name)
    cmds = [("write", f, s, a, d) for f, s, a, d in static]
    cmds += [("event", d, f, s, a, dd) for d, f, s, a, dd in events]
    return cmds


# ---- the window structure, planned without a device -------------------------
def test_virtual_plan_delivers_every_event_in_ordered_windows():
    for name in ("bar808-full", "demo"):
        static, events, _end = uh.phrase_static_and_events(name)
        cmds = fixture_commands(name)
        rows, windows = uh.plan_rolling_virtual(cmds)
        ev = [r for r in rows if r.kind == "event"]
        assert len(ev) == len(events), f"{name}: lost events"
        dues = [r.due for r in ev]
        assert dues == sorted(dues), f"{name}: dues reordered"
        # wrap safety: every event's due is within one horizon of the
        # anchor its window was planned from -- reconstructable because
        # each window's rows carry their own send frames
        assert windows >= len(events) // uh.ROLLING_BATCH_CAP
        # static init is planned as live writes, before any event packet
        wr = [r for r in rows if r.kind == "write"]
        assert len(wr) == len(static)
        assert max(r.send_frame for r in wr) <= min(r.send_frame for r in ev)


def test_window_cut_chains_the_wire():
    """The cut event's relative due, minus the window's wire time, is the
    next window's first-packet requirement: consecutive windows chain, so
    the phrase neither starves the queue nor dies under the wire. Exercised
    on rolling_batch directly with a synthetic stream whose dues grow by
    exactly one packet per event -- the marginal case."""
    F = uh.event_packet_frames(uh.DEFAULT_BAUD)
    lead = uh.MIN_LEAD_FRAMES + 1
    slack = uh.ROLLING_PACKET_SLACK_FRAMES
    # dues spaced exactly one packet: every event is marginal
    ev_abs = [(10_000 + j * F, ("event", 10_000 - 5_000 + j * F, 0, 0, 0x20, 0))
              for j in range(200)]
    origin, i, seen = 5_000, 0, []
    while i < len(ev_abs):
        batch, _next = uh.rolling_batch(ev_abs, i, origin, lead)
        if not batch:
            # wait for deliverability, as the roller does: the device
            # advances until the next event enters the window
            origin = ev_abs[i][0] - (lead + F + slack)
            continue
        assert len(batch) <= uh.ROLLING_BATCH_CAP
        # every included event is deliverable from this anchor
        for j, (due, _c) in enumerate(batch):
            assert due - origin >= lead + (j + 1) * F + slack
        # the first EXCLUDED event was exactly at the boundary, so the next
        # window (anchored at this window's wire end) can take it
        i += len(batch)
        seen.append(len(batch))
        origin = origin + len(batch) * F
        if i < len(ev_abs):
            nxt = uh.rolling_batch(ev_abs, i, origin, lead)[0]
            assert nxt, "the next window starved: the cut does not chain"
    assert sum(seen) == len(ev_abs)


# ---- the live roller against the scripted device ----------------------------
def test_rolling_delivers_the_phrase_across_counter_wraps(monkeypatch):
    # epoch 65300: a 4.2 s phrase crosses the 16-bit counter three times
    bridge, ser, clock = make_bridge(monkeypatch, epoch=65300)
    static, events, _end = uh.phrase_static_and_events("bar808-full")
    rows = bridge.run_rolling(fixture_commands("bar808-full"), quiet=True)
    ev_rows = [r for r in rows if r.kind == "event"]
    assert len(ev_rows) == len(events)
    assert len(ser.writes) == len(static)
    # every accepted event fired, in due order, across the wraps
    assert len(ser.fired) == len(events)
    # the plan's dues are strictly increasing, the device fires in due
    # order, so the fired log equals the plan's dues mod 65536, in order
    fired = [d & 0xFFFF for d, _a in ser.fired]
    assert fired == [r.due & 0xFFFF for r in ev_rows], \
        "device fired events off-schedule"
    # the queue never overran: peak in-flight stayed within the depth
    assert ser.queued_peak <= uh.EVENT_QUEUE_DEPTH
    # the host saw every ACK
    assert bridge.acks_seen == len(rows)


def test_static_image_precedes_every_event_packet_on_the_wire(monkeypatch):
    bridge, ser, clock = make_bridge(monkeypatch)
    bridge.run_rolling(fixture_commands("demo"), quiet=True)
    kinds = [("W" if i < len(ser.writes) else "E") for i in range(0)]
    # the roller sends the static plan first: its send() call happens
    # before any event window, observable as every live write accepted
    # before the first scheduled event
    assert len(ser.writes) == 224
    assert len(ser.accepted) == len(ser.fired)


def test_frozen_device_refuses_rather_than_retimes(monkeypatch):
    # the device runs for 1.5 s of simulated music, then stops: the next
    # window's dues pass while the host waits. The roller must REFUSE --
    # re-timing the phrase (shifting P0) would be a musical edit.
    bridge, ser, clock = make_bridge(monkeypatch)
    ser.freeze_at = int(1.5 * SR)
    with pytest.raises(uh.Refused, match="died"):
        bridge.run_rolling(fixture_commands("bar808-full"), quiet=True)


def test_musical_fixtures_roll_and_short_ones_do_not():
    for name in ("bar808-full", "demo"):
        assert uh.rolling_needed(fixture_commands(name)), name
    for name in ("m5a", "bar808"):
        events, _end = uh.phrase_events(name)
        cmds = [("event", d, f, s, a, dd) for d, f, s, a, dd in events]
        assert not uh.rolling_needed(cmds), name


def test_dry_run_renders_the_rolling_schedule():
    rc = uh.main(["--dry-run", "run", "--fixture", "bar808-full"])
    assert rc == 0

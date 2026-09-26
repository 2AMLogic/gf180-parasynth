#!/usr/bin/env python3
"""fpga/midi_session.py -- a live MIDI host session on the UART bridge (issue #281).

    # the documented start command (simulated board, scripted keyboard):
    .venv/bin/python fpga/midi_session.py --port sim --midi-in scripted:coverage

    # on hardware (Linux raw MIDI device; NOT YET EXERCISED on a board):
    .venv/bin/python fpga/midi_session.py --port /dev/ttyUSB1 --midi-in /dev/snd/midiC1D0

A keyboard's MIDI stream in, register writes out, over the existing USB-UART
control link (fpga/uart_host.py's packets, rtl-sketch/uart_bridge.v's device
contract). No GUI, no on-chip USB, no sequencer, no new protocol: every write
is an ordinary scheduled event packet, applied by the device in its due frame.

THE DEFAULTS (docs/live-midi.md is the long form; the contract's numbers are
fpga/live_midi_contract.py):

  * channels: the mono voice and its controllers on MIDI channel 1; the 808
    drums on channel 10 (General MIDI). Anything on another channel is REFUSED.
  * voice: last-note priority, held-note tracking, single trigger (a new key
    while one is held changes pitch without re-striking the envelope), glide
    as the patch sets it. Releasing the sounding key returns to the most
    recently pressed key still held. Note-on with velocity 0 is note-off. The
    voice has no velocity input: velocity does not change the voice (the
    documented contract, not an ignored control).
  * drums: the General-MIDI-derived map DRUM_MAP; velocity sets the 808 accent
    (0.6 .. 1.4). Exclusive pairs are respected: closed and open hat are one
    instrument (CH chokes OH in the kit) and two hat strikes within 1 ms are
    one strike -- the second is REFUSED, as is a second strike of one stop
    within 1 ms (it could not re-strike). The toms' conga positions, the rim
    shot and the maracas share circuits with the loaded tom/claves/clap
    positions and are REFUSED as unmapped. Drum note-offs are accepted and do
    nothing (one-shot voices).
  * controllers: CC74 cutoff (40 Hz..8 kHz, exponential), CC71 resonance
    (q 0..1), CC7 voice volume (0..0.9; 0.45 is the reference), CC1 modulation
    wheel -- only when the loaded patch routes modulation; the default patch
    does not, so CC1 is REFUSED there. CC120 All Sound Off and CC123 All Notes
    Off are PANIC on either channel.
  * REFUSED, explicitly, never silently ignored: sustain (CC64), every other
    CC, pitch bend, program change, channel and poly aftertouch, system
    exclusive, system common and real-time messages other than Active
    Sensing, notes outside the release domain (fpga/release/qualified_domain
    .check_note, #255: every oscillator's increment in [phase_inc(MIDI 0),
    phase_inc(MIDI 127)]; a patch outside the release refuses to start), and
    note-ons / drum hits the link cannot deliver on time (queue pressure).
    Each refusal is recorded with its reason; the live CLI prints the first of
    each kind and a count at exit.
  * Active Sensing (0xFE) is honoured: once seen, 300 ms of silence is a lost
    connection and the session panics. Closing the session (end of input,
    Ctrl-C, a lost connection) always sends the panic.

THE TIMING CONTRACT. A MIDI message received at host time t, which the host's
map puts in device frame r, schedules its first write at frame r + 16 ms
(LOOKAHEAD_FRAMES) and later writes behind it, two to a frame. Each packet is
sent exactly one lookahead before its due frame, so the device's single FIFO
event queue sees dues in order; latency is constant rather than jittered. A
note-on or drum hit is ADMITTED only if a projection of the wire shows every
packet accepted before its due with room for four more (unconditional traffic:
note-offs, knob updates, panic); otherwise it is REFUSED. A controller is sent
at most once per 10 ms; a newer value replaces an unsent one (redundant writes
are not sent). The known-state image is sent once, at start, as live writes.
"""
from __future__ import annotations

import argparse
import bisect
import math
import os
import select
import sys
import threading
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, os.path.join(HERE, "release"), os.path.join(ROOT, "model"),
           os.path.join(ROOT, "audition")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import live_midi_contract as C                           # noqa: E402
import qualified_domain as qd                            # noqa: E402
import uart_host as uh                                   # noqa: E402

SR = C.SR
BYTE_S = uh.BITS_PER_BYTE / uh.DEFAULT_BAUD

# the session's drum map: GM note -> 808 stop name (docs/live-midi.md)
DRUM_MAP = {
    35: "BD", 36: "BD",                  # acoustic / electric bass drum
    38: "SD", 40: "SD",                  # snare, electric snare
    41: "LT", 43: "LT",                  # low floor tom, high floor tom
    45: "MT", 47: "MT",                  # low tom, low-mid tom
    48: "HT", 50: "HT",                  # hi-mid tom, high tom
    42: "CH", 44: "CH",                  # closed hat, pedal hat
    46: "OH",                            # open hat
    39: "CP",                            # hand clap
    56: "CB",                            # cowbell
    75: "CL",                            # claves
    49: "CY", 57: "CY",                  # crash 1, crash 2
}
EXCLUSIVE = {"CH": ("CH", "OH"), "OH": ("CH", "OH")}
CC_CUTOFF, CC_RESONANCE, CC_VOLUME, CC_MOD, CC_SUSTAIN = 74, 71, 7, 1, 64
CC_ALL_SOUND_OFF, CC_ALL_NOTES_OFF = 120, 123

# Injected session defects (verify_live_midi.py's controls): each must be
# caught by the property named there, for its own reason.
#   DROP_NOTE_OFF   the first voice note-off that would close the gate is lost
#   WRONG_DRUM_MAP  GM 38 strikes the clap instead of the snare
#   DELAYED_EVENT   the fourth scheduled event is placed 5 ms (240 frames) late
DELAY_INJECT_FRAMES = 240


@dataclass
class Refusal:
    t: float
    raw: bytes
    category: str
    detail: str


# ---- MIDI 1.0 byte stream -> messages ---------------------------------------
class MidiParser:
    """Bytes in any chunking -> complete messages (raw bytes, running status
    expanded). Real-time bytes are delivered where they arrive, even inside
    another message; a data byte with no status is delivered alone so it can
    be refused rather than lost."""
    COMMON_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF6: 0, 0xF4: 0, 0xF5: 0, 0xF7: 0}

    def __init__(self):
        self.status = None
        self.data: list = []
        self.sysex = None

    @staticmethod
    def _need(status: int) -> int:
        hi = status & 0xF0
        if hi in (0xC0, 0xD0):
            return 1
        if hi == 0xF0:
            return MidiParser.COMMON_LEN.get(status, 0)
        return 2

    def feed(self, data: bytes) -> list:
        out = []
        for b in data:
            if b >= 0xF8:
                out.append(bytes([b]))
                continue
            if self.sysex is not None:
                if b == 0xF7:
                    out.append(bytes(self.sysex + [b]))
                    self.sysex = None
                    continue
                if b < 0x80:
                    self.sysex.append(b)
                    continue
                out.append(bytes(self.sysex))            # aborted by a new status
                self.sysex = None
            if b == 0xF0:
                self.sysex = [b]
                self.status = None
                continue
            if b >= 0x80:
                self.status, self.data = b, []
                if self._need(b) == 0:
                    out.append(bytes([b]))
                    self.status = None
                continue
            if self.status is None:
                out.append(bytes([b]))                  # stray data byte
                continue
            self.data.append(b)
            if len(self.data) == self._need(self.status):
                out.append(bytes([self.status] + self.data))
                self.data = []
                if self.status >= 0xF0:
                    self.status = None                  # system common: no running status
        return out


# ---- the mono keyboard (the reference KeyHost's policy, one event at a time) --
class MonoKeys:
    """Last-note priority, single trigger, glide 'always', mono: the default
    policy of model/voice_fx.KeyHost (contract 5.6), stepped one event at a
    time so a live host can decide each event before the next arrives.
    `plan()` never mutates; `commit()` adopts what `plan()` returned."""

    def __init__(self, regs: dict):
        import voice_fx as vf
        from dsp import note_hz, phase_inc
        self.vf, self.note_hz, self.phase_inc = vf, note_hz, phase_inc
        self.detune = tuple(regs["detune"])
        self.track = regs["track"]
        self.held: list = []
        self.cur = None
        self.prev = None
        self.first_from_reset = True

    def incs(self, note: int) -> list:
        return [self.phase_inc(self.note_hz(note) * 2.0 ** (dt / 12.0)) for dt in self.detune]

    def plan(self, on: bool, note: int):
        import synth_top_model as stm
        was_held = bool(self.held)
        if on:
            held = [n for n in self.held if n != note] + [note]
        else:
            if note not in self.held:
                return None
            held = [n for n in self.held if n != note]
        state = {"held": held, "cur": self.cur, "prev": self.prev}
        writes = []
        new = held[-1] if held else None
        if new is None:
            writes.append((0, 0, stm.A_GATE_OFF, 0))
            state["cur"] = None
            return writes, state
        if new != self.cur:
            jump = self.prev is None and self.first_from_reset
            for k, v in enumerate(self.incs(new)):
                writes.append((1 if jump else 0, 0, stm.A_INC + k, v & 0xFFFFFFFF))
            writes.append((0, 0, stm.A_TRACK, self.vf.VoiceFx.note_track(new, self.track)
                           & 0xFFFFFFFF))
            state["cur"] = new
            state["prev"] = new
        if on and not was_held:
            writes.append((0, 0, stm.A_GATE_ON, 0))
        return writes, state

    def commit(self, state: dict) -> None:
        self.held, self.cur, self.prev = state["held"], state["cur"], state["prev"]

    def panic(self) -> None:
        self.held, self.cur = [], None


# ---- the schedule -------------------------------------------------------------
@dataclass
class Pkt:
    due: int                        # absolute device frame
    seq: int
    write: tuple                    # (flag, sec, addr, data)
    gid: int
    role: str
    not_before: float
    finish: float = 0.0             # projected completion on the wire (host s)
    sent: bool = False

    @property
    def key(self):
        return (self.due, self.seq)


@dataclass
class GroupRec:
    gid: int
    kind: str
    t: float
    r: int
    dues: list = field(default_factory=list)
    pushed: int = 0


class MidiSession:
    """One live session on one open link. `ser` is pyserial-shaped (real port,
    or uart_device_sim.SimSerial); `clock` has monotonic()/sleep()."""

    def __init__(self, ser, *, clock=time, baud: int = uh.DEFAULT_BAUD,
                 preset: str | None = None, patch: dict | None = None,
                 inject=frozenset(), out=None):
        import spi_host as sh
        import voice_fx as vf
        import selected_preset
        if baud != uh.DEFAULT_BAUD:
            raise ValueError("the contract's wire arithmetic is for 115200 baud")
        self.ser, self.clock, self.baud = ser, clock, baud
        self.bridge = uh.Bridge.on_serial(ser, baud, clock=clock)
        self.inject = set(inject)
        self.out = out
        regs = (dict(patch) if patch is not None else
                selected_preset.definition(preset)["registers"] if preset else
                vf.VoiceFx.patch_regs())
        self.regs = regs
        # the release's player-facing domain (#255): a patch outside it is
        # refused before anything is sent
        self.patch_summary = qd.check_patch(regs, name=preset or "default")
        self.mh = sh.MusicHost(patch=dict(regs))
        self.keys = MonoKeys(regs)
        self.mod_routed = bool(int(regs["mroute"]) & (vf.MR_OSC | vf.MR_FILT))
        self.parser = MidiParser()
        self.anchor = None
        self.anchors: list = []
        self.timeline: list = []            # every planned Pkt, sorted by key
        self.unsent: list = []              # the unsent suffix, sorted by key
        self.slots: dict = {}
        self.seq = 0
        self.cursor = -(10 ** 9)
        self.gid = 0
        self.groups: list = []
        self.refusals: list = []
        self.knob_last: dict = {}
        self.knob_pending: dict = {}
        self.last_strike: dict = {}
        self.wire_free = 0.0
        self.polls: list = []               # outstanding STATUS polls: accept time
        self.next_poll = None
        self.rx = b""
        self.sensing = False
        self.last_rx_t = None
        self.closed_at = None
        self.voice_offs_closing = 0
        self.stats = {"host_queue_peak": 0, "device_queue_peak": 0, "deadline_misses": 0,
                      "pushed_events": 0, "reanchors": 0, "device_errors": [],
                      "boots": 0, "groups": {}, "refused": {}, "superseded": 0,
                      "packets": 0, "drum_noteoffs": 0, "noops": 0}

    # ---- output ---------------------------------------------------------------
    def _say(self, msg: str) -> None:
        if self.out is not None:
            print(msg, file=self.out, flush=True)

    # ---- start: the known-state image, then the time anchor --------------------
    def init_writes(self) -> list:
        import synth_top_model as stm
        import drums_fx as dx
        self.mh.load(0)
        w = [(x.flag, x.sec, x.addr, x.data & 0xFFFFFFFF) for x in self.mh.w]
        self.mh.w.clear()
        self.mh.events.clear()
        for addr, key in ((stm.A_NSEL, "nsel"), (stm.A_MROUTE, "mroute"), (stm.A_MMIX, "mmix"),
                          (stm.A_MWHEEL, "mwheel"), (stm.A_MPD, "mpd"), (stm.A_MFD, "mfd")):
            w.append((0, 0, addr, int(self.regs[key])))
        w += [(0, 0, stm.A_GATE_OFF, 0), (0, 1, dx.A_STOPS, 0)]
        return w

    def start(self, *, timeout_s: float = 5.0) -> None:
        writes = self.init_writes()
        acks0 = self.bridge.acks_seen
        # the write queue drains two a frame and a write packet takes 33 frames
        # of wire: one burst cannot overflow it
        self._write(b"".join(uh.pkt_write(*w) for w in writes))
        deadline = self.clock.monotonic() + timeout_s + len(writes) * 8 * BYTE_S
        while self.bridge.acks_seen - acks0 < len(writes):
            if self.bridge._take({"ack", "err", "boot"}, deadline) is None:
                raise uh.Refused(f"the known-state image was not acknowledged: "
                                 f"{self.bridge.acks_seen - acks0} of {len(writes)} ACKs")
            if self.bridge.device_errors:
                raise uh.Refused(f"the device rejected the known-state image: "
                                 f"{self.bridge.device_errors[:3]}")
        self._anchor()
        now = self.clock.monotonic()
        self.next_poll = now + C.STATUS_POLL_S
        self.stats["init_writes"] = len(writes)
        self._say(f"midi_session: known state sent ({len(writes)} writes, acknowledged); "
                  f"device frame {self.anchor[0]} at host {self.anchor[1]:.6f} s")

    def _anchor(self) -> None:
        """The host-time -> device-frame map: one STATUS on an idle wire. The
        reply carries the frame REGISTER (audio + 1) as of the 2-byte
        request's acceptance, which completes two byte-times after it is
        written."""
        t_send = max(self.clock.monotonic(), self.wire_free)
        st = self.bridge.status()
        t_acc = t_send + 2 * BYTE_S
        base = self.anchor[0] if self.anchor else None
        f = st.frame - 1
        if base is not None:                    # keep frames absolute across wraps
            f = base + ((f - base) & 0xFFFF) - (0x10000 if ((f - base) & 0xFFFF) >= 0x8000 else 0)
        self.anchor = (f, t_acc)
        self.anchors.append(self.anchor)
        self.wire_free = max(self.wire_free, t_acc)

    def frame_of(self, t: float) -> int:
        fa, ta = self.anchor
        return fa + math.floor((t - ta) * SR)

    def release_of(self, due: int) -> float:
        fa, ta = self.anchor
        return ta + (due - C.LOOKAHEAD_FRAMES - fa) / SR

    # ---- the wire -------------------------------------------------------------
    def _write(self, data: bytes) -> None:
        now = self.clock.monotonic()
        self.wire_free = max(now, self.wire_free) + len(data) * BYTE_S
        self.ser.write(data)

    def _send_due(self, now: float) -> None:
        k = 0
        while k < len(self.unsent) and max(self.release_of(self.unsent[k].due),
                                           self.unsent[k].not_before) <= now:
            k += 1
        if not k:
            return
        batch, self.unsent = self.unsent[:k], self.unsent[k:]
        start = max(now, self.wire_free)
        blob = b""
        for i, p in enumerate(batch):
            fin = start + (i + 1) * 10 * BYTE_S
            if self.frame_of(fin) > p.due - uh.MIN_LEAD_FRAMES:
                self.stats["deadline_misses"] += 1
            p.sent = True
            blob += uh.pkt_event(p.due & 0xFFFF, *p.write)
        self.stats["packets"] += k
        self._write(blob)

    def _poll(self, now: float) -> None:
        if self.next_poll is None or now < self.next_poll:
            return
        self._write(uh.pkt_status())
        self.polls.append(self.wire_free)
        self.next_poll = now + C.STATUS_POLL_S

    def _drain(self) -> None:
        old = getattr(self.ser, "timeout", None)
        try:
            self.ser.timeout = 0
            chunk = self.ser.read(4096)
        finally:
            self.ser.timeout = old
        if not chunk:
            return
        self.rx += chunk
        pkts, used = uh.scan_packets(self.rx)
        self.rx = self.rx[used:]
        for p in pkts:
            if p.kind == "err":
                self.stats["device_errors"].append((p.code, p.info))
                self._say(f"midi_session: DEVICE {p.describe()}")
            elif p.kind == "boot":
                self.stats["boots"] += 1
                self._say("midi_session: the device RESET (BOOT on the wire); its state and "
                          "queue are gone -- stop and restart the session")
            elif p.kind == "status":
                self.stats["device_queue_peak"] = max(self.stats["device_queue_peak"], p.evq)
                if self.polls:
                    t_acc = self.polls.pop(0)
                    predicted = self.frame_of(t_acc) + 1
                    d = (p.frame - predicted) & 0xFFFF
                    d = d - 0x10000 if d >= 0x8000 else d
                    if abs(d) > C.RESYNC_TOLERANCE_FRAMES:
                        fa, ta = self.anchor
                        self.anchor = (predicted - 1 + d, t_acc)
                        self.anchors.append(self.anchor)
                        self.stats["reanchors"] += 1
                        self._say(f"midi_session: re-anchored: device {d:+d} frames off the map")

    # ---- the send loop --------------------------------------------------------
    def next_action(self) -> float:
        cands = []
        if self.unsent:
            p = self.unsent[0]
            cands.append(max(self.release_of(p.due), p.not_before))
        if self.next_poll is not None:
            cands.append(self.next_poll)
        if self.sensing and self.last_rx_t is not None:
            cands.append(self.last_rx_t + C.ACTIVE_SENSING_TIMEOUT_S)
        return min(cands) if cands else float("inf")

    def _step(self, now: float) -> None:
        self._send_due(now)
        self._poll(now)
        self._drain()
        if (self.sensing and self.last_rx_t is not None
                and now - self.last_rx_t >= C.ACTIVE_SENSING_TIMEOUT_S):
            self.sensing = False
            self._say("midi_session: Active Sensing stopped: connection lost, PANIC")
            self._panic(now, "connection-lost")

    def service(self, until: float) -> None:
        """Run everything due up to host time `until`, sleeping between."""
        while True:
            now = self.clock.monotonic()
            self._step(now)
            if now >= until:
                return
            self.clock.sleep(max(0.0, min(self.next_action(), until) - now))

    # ---- scheduling -------------------------------------------------------------
    def _place(self, head: list, tail: list, g0: int):
        used: dict = {}

        def take(f):
            while self.slots.get(f, 0) + used.get(f, 0) >= C.WRITE_SLOTS:
                f += 1
            used[f] = used.get(f, 0) + 1
            return f
        out, f = [], g0
        for i, w in enumerate(head):
            f = take(f)
            out.append((f, w, "anchor" if i == len(head) - 1 else "head"))
        anchor = prev = f
        for off, w in sorted(tail, key=lambda x: x[0]):
            prev = take(max(anchor + off, prev))
            out.append((prev, w, "tail"))
        return out, anchor

    def _projection_ok(self, new: list, reserve: int) -> tuple:
        keys = [p.key for p in self.timeline]
        new = sorted(new, key=lambda p: p.key)
        at = bisect.bisect_left(keys, new[0].key)
        tail = sorted(self.timeline[at:] + new, key=lambda p: p.key)
        fin = self.timeline[at - 1].finish if at > 0 else -1e9
        res_s = reserve * 10 * BYTE_S
        ok, fins = True, []
        for p in tail:
            fin = max(self.release_of(p.due), p.not_before, fin) + 10 * BYTE_S
            fins.append(fin)
            if self.frame_of(fin + res_s) > p.due - 1 - C.DEADLINE_MARGIN_FRAMES:
                ok = False
        return ok, at, tail, fins

    def _schedule(self, kind: str, t: float, head: list, tail: list = (), *,
                  conditional: bool, nominal: int | None = None, deferred: bool = False):
        """Place and admit one event's writes; None when refused."""
        r = self.frame_of(t)
        g0 = (r + C.LOOKAHEAD_FRAMES) if nominal is None else nominal
        if not deferred:
            g0 = max(g0, self.cursor)
        if "DELAYED_EVENT" in self.inject and self.gid == 3:
            g0 += DELAY_INJECT_FRAMES
        if conditional and len(self.unsent) + len(head) + len(tail) > C.HOST_QUEUE_MAX_PACKETS:
            return None
        push = 0
        while True:
            placed, anchor = self._place(head, list(tail), g0 + push)
            new = [Pkt(f, self.seq + i, w, self.gid, role, t)
                   for i, (f, w, role) in enumerate(placed)]
            ok, at, merged, fins = self._projection_ok(
                new, C.ADMISSION_RESERVE_PACKETS if conditional else 0)
            if ok:
                break
            if conditional:
                return None
            push += 1
            if push > C.MAX_PUSH_FRAMES:
                placed, anchor = self._place(head, list(tail), g0)
                new = [Pkt(f, self.seq + i, w, self.gid, role, t)
                       for i, (f, w, role) in enumerate(placed)]
                ok, at, merged, fins = self._projection_ok(new, 0)
                push = -1
                self.stats["deadline_misses"] += 1   # admitted at risk, counted now
                break
        for p, fin in zip(merged, fins):
            p.finish = fin
        self.timeline = self.timeline[:at] + merged
        for p in new:
            self.slots[p.due] = self.slots.get(p.due, 0) + 1
            bisect.insort(self.unsent, p, key=lambda q: q.key)
        self.seq += len(new)
        if push:
            self.stats["pushed_events"] += 1
        if not deferred:
            self.cursor = anchor
        rec = GroupRec(self.gid, kind, t, r, [p.due for p in new], push)
        self.groups.append(rec)
        self.gid += 1
        self.stats["groups"][kind] = self.stats["groups"].get(kind, 0) + 1
        self.stats["host_queue_peak"] = max(self.stats["host_queue_peak"], len(self.unsent))
        self._prune(t)
        return new

    def _prune(self, now: float) -> None:
        horizon = self.frame_of(now) - 4 * SR
        k = 0
        while k < len(self.timeline) - 1 and self.timeline[k].sent and \
                self.timeline[k].due < horizon:
            k += 1
        if k:
            self.timeline = self.timeline[k:]
        for f in [f for f in self.slots if f < horizon]:
            del self.slots[f]

    # ---- the musical front end --------------------------------------------------
    def _refuse(self, t: float, raw: bytes, category: str, detail: str) -> None:
        first = category not in self.stats["refused"]
        self.stats["refused"][category] = self.stats["refused"].get(category, 0) + 1
        self.refusals.append(Refusal(t, bytes(raw), category, detail))
        if first:
            self._say(f"midi_session: REFUSED {category}: {detail} "
                      f"(further {category} refusals are counted, not printed)")

    def _panic(self, t: float, why: str) -> None:
        import synth_top_model as stm
        import drums_fx as dx
        self._schedule("panic", t, [(0, 0, stm.A_GATE_OFF, 0), (0, 1, dx.A_STOPS, 0)],
                       conditional=False)
        self.keys.panic()

    def feed(self, t: float, data: bytes) -> None:
        """MIDI bytes the input handed over at host time t."""
        if self.closed_at is not None:
            raise RuntimeError("feed after close")
        self.last_rx_t = t
        for msg in self.parser.feed(data):
            self._message(t, msg)
        self._send_due(self.clock.monotonic())

    def _message(self, t: float, m: bytes) -> None:
        V, D = C.VOICE_CHANNEL, C.DRUM_CHANNEL
        st = m[0]
        if st < 0x80:
            return self._refuse(t, m, "stray-data", f"data byte 0x{st:02x} with no status")
        if st == 0xFE:
            self.sensing = True
            return
        if st >= 0xF8:
            return self._refuse(t, m, "realtime", f"real-time 0x{st:02x} (clock/transport/"
                                "reset are not supported; the session has no sequencer)")
        if st == 0xF0 or st == 0xF7:
            return self._refuse(t, m, "sysex", "system exclusive is not supported")
        if st >= 0xF0:
            return self._refuse(t, m, "system", f"system common 0x{st:02x} is not supported")
        hi, ch = st & 0xF0, st & 0x0F
        if ch not in (V, D):
            return self._refuse(t, m, "channel", f"MIDI channel {ch + 1}: the voice is on "
                                f"{V + 1} and the drums on {D + 1}")
        if hi == 0x90 and m[2] == 0:
            hi = 0x80                                    # velocity 0 is note-off
        if hi == 0xB0 and m[1] in (CC_ALL_SOUND_OFF, CC_ALL_NOTES_OFF):
            return self._panic(t, "cc")
        other = {0xE0: ("pitch-bend", "pitch bend"), 0xC0: ("program-change", "program change"),
                 0xD0: ("aftertouch", "channel aftertouch"),
                 0xA0: ("poly-aftertouch", "polyphonic aftertouch")}
        if hi in other:
            cat, what = other[hi]
            return self._refuse(t, m, cat, f"{what} is not supported")
        if ch == V:
            if hi in (0x80, 0x90):
                return self._voice_key(t, m, hi == 0x90, m[1])
            if hi == 0xB0:
                return self._cc(t, m, m[1], m[2])
        else:
            if hi == 0x80:
                self.stats["drum_noteoffs"] += 1         # one-shot voices: a no-op by contract
                return
            if hi == 0x90:
                return self._hit(t, m, m[1], m[2])
            if hi == 0xB0:
                cat = "sustain" if m[1] == CC_SUSTAIN else "cc-unsupported"
                return self._refuse(t, m, cat, f"CC{m[1]} on the drum channel is not supported")
        return self._refuse(t, m, "system", f"status 0x{st:02x} not understood")

    def _voice_key(self, t: float, m: bytes, on: bool, note: int) -> None:
        if on:
            try:
                qd.check_note(note, self.regs)
            except qd.Rejected as exc:
                return self._refuse(t, m, "domain", str(exc))
        planned = self.keys.plan(on, note)
        if planned is None:
            self.stats["noops"] += 1                     # release of a key not held
            return
        writes, state = planned
        if not on and "DROP_NOTE_OFF" in self.inject and any(
                w[2] == 0x21 for w in writes) and self.voice_offs_closing == 0:
            self.voice_offs_closing += 1
            return                                       # the injected defect: lost
        if writes:
            if self._schedule("note-on" if on else "note-off", t, writes,
                              conditional=on) is None:
                return self._refuse(t, m, "queue-pressure", f"note {note}: the link cannot "
                                    "deliver it within the lookahead")
        else:
            self.stats["noops"] += 1
        self.keys.commit(state)

    def _hit(self, t: float, m: bytes, note: int, vel: int) -> None:
        import drums_fx as dx
        name = DRUM_MAP.get(note)
        if "WRONG_DRUM_MAP" in self.inject and note == 38:
            name = "CP"
        if name is None:
            return self._refuse(t, m, "unmapped-drum", f"GM note {note} has no 808 voice "
                                "in the loaded kit")
        r = self.frame_of(t)
        for other in EXCLUSIVE.get(name, (name,)):
            if other in self.last_strike and r - self.last_strike[other] < C.SIMULTANEOUS_FRAMES:
                return self._refuse(t, m, "simultaneous", f"{name} within 1 ms of {other}: "
                                    "one instrument, one strike")
        stop = dx.STOP_NAMES.index(name)
        accent = 0.6 + 0.8 * (vel - 1) / 126.0
        image = dict(self.mh.image)
        n0 = len(self.mh.w)
        self.mh.hits([(0, stop, accent)])
        new = self.mh.w[n0:]
        del self.mh.w[n0:]
        self.mh.events.clear()
        head = [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in new if w.frame == 0]
        tail = [(w.frame, (w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF)) for w in new if w.frame]
        if self._schedule("hit", t, head, tail, conditional=True) is None:
            self.mh.image = image
            return self._refuse(t, m, "queue-pressure", f"{name}: the link cannot deliver "
                                "it within the lookahead")
        self.last_strike[name] = r

    def _cc(self, t: float, m: bytes, cc: int, v: int) -> None:
        import synth_top_model as stm
        import voice_fx as vf
        if cc == CC_SUSTAIN:
            return self._refuse(t, m, "sustain", "sustain (CC64) is not supported: "
                                "a held pedal would silently hold nothing")
        if cc == CC_CUTOFF:
            name, value = "cutoff", 40.0 * 200.0 ** (v / 127.0)
        elif cc == CC_RESONANCE:
            name, value = "resonance", v / 127.0
        elif cc == CC_VOLUME:
            name, value = "volume", 0.9 * v / 127.0
        elif cc == CC_MOD:
            if not self.mod_routed:
                return self._refuse(t, m, "mod-unrouted", "the loaded patch routes no "
                                    "modulation (MROUTE osc/filter off): the wheel would do nothing")
            name, value = "mod", int(round(v / 127.0 * vf.MMIX_FULL))
        else:
            return self._refuse(t, m, "cc-unsupported", f"CC{cc} is not mapped")
        n0 = len(self.mh.w)
        if name == "mod":
            self.mh.voice(0, stm.A_MWHEEL, value, tag="mod")
        else:
            self.mh.knob(0, name, value)
        writes = [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in self.mh.w[n0:]]
        del self.mh.w[n0:]
        self.mh.events.clear()
        pend = self.knob_pending.get(cc)
        if pend and not pend[0].sent:
            for p, w in zip(pend, writes):               # a newer value, same frames
                p.write = w
            self.stats["superseded"] += 1
            return
        r = self.frame_of(t)
        nominal, deferred = r + C.LOOKAHEAD_FRAMES, False
        last = self.knob_last.get(cc)
        if last is not None and last + C.KNOB_INTERVAL_FRAMES > nominal:
            nominal, deferred = last + C.KNOB_INTERVAL_FRAMES, True
        new = self._schedule("knob", t, writes, conditional=False, nominal=nominal,
                             deferred=deferred)
        self.knob_last[cc] = new[-1].due
        self.knob_pending[cc] = new

    # ---- close --------------------------------------------------------------------
    def close(self, *, why: str = "end of input", timeout_s: float = 5.0) -> dict:
        """Panic, deliver everything still planned, and ask the device how it went."""
        now = self.clock.monotonic()
        self.closed_at = now
        self._panic(now, why)
        last_due = max((p.due for p in self.unsent), default=self.frame_of(now))
        done_t = max(self.release_of(last_due) + C.LOOKAHEAD_FRAMES / SR, now) + 4 / SR
        self.service(done_t)
        self._drain()
        final = self.bridge.status(timeout_s=timeout_s)
        self.stats["final_status"] = {"evq": final.evq, "wrq": final.wrq, "drops": final.drops,
                                      "errs": final.errs, "flags": final.flags}
        self.stats["device_errors"] += list(self.bridge.device_errors)
        self._say(f"midi_session: closed ({why}): {self.stats['packets']} event packets, "
                  f"{sum(self.stats['refused'].values())} refused {self.stats['refused']}, "
                  f"{self.stats['superseded']} knob values superseded; device queue "
                  f"{final.evq}, drops {final.drops}, errors {final.errs}")
        return self.stats


# ---- the real-port MIDI input (standard library only) ---------------------------
class RawMidiInput:
    """A byte-stream MIDI input: a Linux raw MIDI device (/dev/snd/midiC*D*),
    a FIFO, or a pipe. Timestamps are the host's monotonic clock when the
    bytes are read -- the session's "receipt"."""

    def __init__(self, path_or_fd):
        self.fd = path_or_fd if isinstance(path_or_fd, int) else \
            os.open(path_or_fd, os.O_RDONLY | os.O_NONBLOCK)

    def read(self, timeout: float):
        """(t, bytes) -- bytes b"" on timeout, None at end of input."""
        r, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not r:
            return time.monotonic(), b""
        try:
            data = os.read(self.fd, 256)
        except BlockingIOError:
            return time.monotonic(), b""
        return time.monotonic(), (data if data else None)

    def close(self) -> None:
        os.close(self.fd)


def scripted_input(name: str, delay_s: float = 0.3) -> tuple:
    """A pipe fed in real time from one of verify_live_midi's scenarios: the
    RawMidiInput adapter reads it exactly as it would a device."""
    import verify_live_midi as vlm
    events = vlm.SCENARIOS[name]()
    rfd, wfd = os.pipe()

    def play():
        t0 = time.monotonic() + delay_s
        try:
            for e in events:
                time.sleep(max(0.0, t0 + e.t - time.monotonic()))
                os.write(wfd, vlm.enc(e))
        finally:
            os.close(wfd)                                # end of input
    th = threading.Thread(target=play, daemon=True)
    return RawMidiInput(rfd), th, len(events)


def run_live(session: MidiSession, source: RawMidiInput, *, duration_s: float | None = None) -> str:
    t_end = None if duration_s is None else time.monotonic() + duration_s
    why = "end of input"
    try:
        while True:
            now = time.monotonic()
            if t_end is not None and now >= t_end:
                why = "duration reached"
                break
            wait = min(session.next_action(), now + 0.05) - now
            t, data = source.read(wait)
            if data is None:
                break
            if data:
                session.feed(t, data)
            session.service(time.monotonic())
    except KeyboardInterrupt:
        why = "interrupted"
    session.close(why=why)
    return why


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n", 1)[1])
    ap.add_argument("--port", required=True, help="serial port of the board, or `sim` for "
                    "the device contract behind a pty (fpga/uart_device_sim.py)")
    ap.add_argument("--midi-in", required=True, help="raw MIDI byte device / FIFO path, "
                    "`-` for stdin, or scripted:<scenario> (coverage, pressure, sustained)")
    ap.add_argument("--preset", default=None, help="selected_preset name (default patch if omitted)")
    ap.add_argument("--duration", type=float, default=None, help="stop after this many seconds")
    a = ap.parse_args(argv)
    sim = None
    if a.port == "sim":
        import uart_device_sim as dev
        sim = dev.UartDeviceSim().start()
        port = sim.port
    else:
        port = a.port
    uh._require_serial()
    import serial
    try:
        ser = serial.Serial(port, uh.DEFAULT_BAUD, timeout=1.0)
    except (OSError, serial.SerialException) as exc:
        print(f"midi_session: REFUSED -- cannot open {port}: {exc}", file=sys.stderr)
        return 2
    feeder = None
    if a.midi_in.startswith("scripted:"):
        source, feeder, n = scripted_input(a.midi_in.split(":", 1)[1])
        print(f"midi_session: scripted keyboard `{a.midi_in}` ({n} messages) through a pipe")
    elif a.midi_in == "-":
        source = RawMidiInput(sys.stdin.fileno())
    else:
        try:
            source = RawMidiInput(a.midi_in)
        except OSError as exc:
            print(f"midi_session: REFUSED -- cannot open MIDI input {a.midi_in}: {exc}",
                  file=sys.stderr)
            return 2
    session = MidiSession(ser, preset=a.preset, out=sys.stdout)
    try:
        session.start()
    except uh.Refused as exc:
        print(f"midi_session: REFUSED -- {exc}", file=sys.stderr)
        return 2
    print(f"midi_session: playing -- voice on MIDI channel {C.VOICE_CHANNEL + 1}, drums on "
          f"{C.DRUM_CHANNEL + 1}, lookahead {C.LOOKAHEAD_MS:.0f} ms; Ctrl-C ends with a panic")
    if feeder:
        feeder.start()
    run_live(session, source, duration_s=a.duration)
    st = session.stats
    rc = 0
    if st["device_errors"] or st["deadline_misses"] or st["final_status"]["drops"] \
            or st["boots"]:
        print(f"midi_session: FAIL -- device errors {st['device_errors'][:5]}, deadline "
              f"misses {st['deadline_misses']}, boots {st['boots']}")
        rc = 1
    if sim is not None:
        ex = [w for w in sim.writes if w[5] == "event"]
        print(f"midi_session: simulated device executed {len(ex)} scheduled writes, "
              f"{len(sim.writes) - len(ex)} live; errors {sorted({e[0] for e in sim.errors})}, "
              f"drops {sim.drops}, queue peak {sim.evq_peak}")
        if sim.errors or sim.drops:
            rc = 1
        sim.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

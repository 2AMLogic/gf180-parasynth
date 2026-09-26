#!/usr/bin/env python3
"""T-LIVE-MIDI: a live MIDI session, checked against an independently built schedule.

    .venv/bin/python fpga/verify_live_midi.py                 # sim scenarios + controls
    .venv/bin/python fpga/verify_live_midi.py --rtl coverage  # + UART RTL replay, I2S
    .venv/bin/python fpga/verify_live_midi.py --start-red     # against the stub session

WHAT IS UNDER TEST. `midi_session.MidiSession` (issue #281) -- the MIDI
parser, channel routing, last-note priority, drum map, CC map, refusals,
lookahead placement, admission and its send loop -- writing real packets
(fpga/uart_host.py's encoders) into the device contract (fpga/
uart_device_sim.py) on SIMULATED time. Only the serial port and the clock
are substituted. `--rtl` replays the session's ACTUAL transmitted bytes
through the Arty UART wrapper (fpga/verify_uart_bridge.py) and compares the
decoded I2S with the model driven by THIS file's expected schedule.

WHAT IT IS CHECKED AGAINST. `Oracle`, built here from the SCENARIO's structured
events -- never from the bytes the session parsed, nor from anything the
session computed -- with its own drum map, CC formulas, held-note model (the
model's reference KeyHost, contract 5.6, run in batch), knob-rate rule,
placement and wire admission. The two share only fpga/live_midi_contract.py
(the frozen parameters) and the register encoders already verified
bit-exact at the pins (spi_host.MusicHost, voice_fx.KeyHost). The one number
the session chooses -- its host-time -> device-frame map -- is read from it
and checked against the device's own timeline before anything else is.

PROPERTIES (rule 4: every control prints which saw it, MOVED or BLIND):

  static_image  the known-state image executed exactly, once, in order
  voice_gate    GATE_ON / GATE_OFF edges, frame and order
  voice_pitch   INC / TRACK writes, values, jump flags and frames
  drum_strikes  STOPS rising edges: which stop, which frame
  drum_coeffs   accents and the timed coefficient sequences (BD window, tom drop)
  knobs         cutoff / resonance / volume / mod-wheel register writes
  timing        every write that matches in value lands in its expected frame
  stuck_notes   frames a gate is open that the schedule says are closed
  refusals      what was refused, and why, equals the schedule's refusals
  release_domain  fpga/release/qualified_domain.check_stream (#255) accepts
                every write the device executed
  queues        device queue peak, host queue peak, zero drops / ERR / late
  latency       receipt -> applied distribution (the target on `sustained`)

CONTROLS (each must be caught by the property named, for its own reason):

  DROP_NOTE_OFF   one voice note-off is lost in the session -> voice_gate, stuck_notes
  WRONG_DRUM_MAP  GM 38 (snare) is struck as the clap -> drum_strikes
  DELAYED_EVENT   one event is scheduled 5 ms late -> timing

Exit 0 PASS, 1 FAIL, 2 NO VERDICT (an apparatus precondition failed).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "fpga/release", "rtl-sketch", "model", "audition"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import numpy as np                                # noqa: E402

import live_midi_contract as C                    # noqa: E402
import uart_device_sim as dev                     # noqa: E402
import uart_host as uh                            # noqa: E402
import spi_host as sh                             # noqa: E402
import synth_top_model as stm                     # noqa: E402
import drums_fx as dx                             # noqa: E402
import voice_fx as vf                             # noqa: E402
from dsp import note_hz, phase_inc                # noqa: E402

SR = C.SR
BT = uh.BITS_PER_BYTE / uh.DEFAULT_BAUD            # one byte on the wire, seconds
EVENT_PKT_S = 10 * BT
REPORT_DIR = ROOT / "fpga/reports/live-midi"
RTL_TAIL_S = 0.3

# ---- the oracle's OWN maps (docs/live-midi.md; written here, not imported) ---
DRUM_MAP = {35: "BD", 36: "BD", 38: "SD", 40: "SD", 41: "LT", 43: "LT",
            45: "MT", 47: "MT", 48: "HT", 50: "HT", 42: "CH", 44: "CH",
            46: "OH", 39: "CP", 56: "CB", 75: "CL", 49: "CY", 57: "CY"}
HAT_PAIR = {"CH", "OH"}


def cc_value(cc: int, v: int):
    """The CC map's physical values, re-derived from the documented formulas."""
    if cc == 74:
        return "cutoff", 40.0 * 200.0 ** (v / 127.0)          # 40 Hz .. 8 kHz, exponential
    if cc == 71:
        return "resonance", v / 127.0                        # q 0 .. 1
    if cc == 7:
        return "volume", 0.9 * v / 127.0                     # 0 .. 0.9 (0.45 = reference)
    if cc == 1:
        return "mod", int(round(v / 127.0 * vf.MMIX_FULL))
    return None, None


def accent_of(vel: int) -> float:
    return 0.6 + 0.8 * (vel - 1) / 126.0                     # 808 accent 0.6 .. 1.4


# ---- scenarios: structured MIDI, then bytes -----------------------------------
@dataclass
class Ev:
    t: float                     # seconds after the performance start
    kind: str                    # on off cc pb pc cat pat sysex rt sys
    ch: int = 0
    a: int = 0
    b: int = 0


def enc(e: Ev) -> bytes:
    """Standard MIDI 1.0 bytes for one structured event (no running status)."""
    ch = e.ch & 0x0F
    if e.kind == "on":
        return bytes([0x90 | ch, e.a, e.b])
    if e.kind == "off":
        return bytes([0x80 | ch, e.a, e.b or 64])
    if e.kind == "cc":
        return bytes([0xB0 | ch, e.a, e.b])
    if e.kind == "pb":
        return bytes([0xE0 | ch, e.a & 0x7F, (e.a >> 7) & 0x7F])
    if e.kind == "pc":
        return bytes([0xC0 | ch, e.a])
    if e.kind == "cat":
        return bytes([0xD0 | ch, e.a])
    if e.kind == "pat":
        return bytes([0xA0 | ch, e.a, e.b])
    if e.kind == "sysex":
        return bytes([0xF0, 0x7D, 0x01, 0x02, 0xF7])
    if e.kind == "rt":
        return bytes([e.a])
    raise ValueError(e.kind)


def sc_coverage() -> list:
    V, D = C.VOICE_CHANNEL, C.DRUM_CHANNEL
    ev = [
        # simultaneous voice and drums
        Ev(0.000, "on", V, 60, 100), Ev(0.000, "on", D, 36, 110), Ev(0.000, "on", D, 42, 80),
        # overlap: legato to 64, release the covered key (no change), then the last
        Ev(0.100, "on", V, 64, 90), Ev(0.180, "off", V, 60), Ev(0.260, "off", V, 64),
        # fast note-off: released 1 ms after the press, before its writes are sent
        Ev(0.300, "on", V, 67, 100), Ev(0.301, "off", V, 67),
        # repeated notes, velocity-0 as note-off, a repeat while held (no-op)
        Ev(0.350, "on", V, 62, 100), Ev(0.400, "on", V, 62, 0), Ev(0.450, "on", V, 62, 100),
        Ev(0.470, "on", V, 62, 70), Ev(0.500, "off", V, 62),
        # last-note priority with return to the previously held key
        Ev(0.550, "on", V, 55, 100), Ev(0.560, "on", V, 58, 100), Ev(0.570, "on", V, 62, 100),
        Ev(0.600, "off", V, 62), Ev(0.620, "off", V, 55), Ev(0.640, "off", V, 58),
        # a note under a knob burst
        Ev(0.720, "on", V, 48, 100), Ev(0.780, "off", V, 48),
        Ev(0.750, "cc", V, 7, 100),
        # hats: CH and OH 0.2 ms apart are one strike (the second REFUSED);
        # an open hat choked by a later closed hat is the kit's own behaviour
        Ev(0.820, "on", D, 46, 100), Ev(0.8202, "on", D, 42, 100),
        Ev(0.900, "on", D, 46, 100), Ev(1.000, "on", D, 42, 90),
        # the heavy ones: three toms (each a 60 ms pitch drop)
        Ev(0.950, "on", D, 41, 120), Ev(0.951, "on", D, 45, 100), Ev(0.952, "on", D, 48, 80),
        Ev(1.100, "on", D, 38, 100), Ev(1.110, "on", D, 39, 100), Ev(1.120, "on", D, 56, 100),
        Ev(1.130, "on", D, 75, 100), Ev(1.140, "on", D, 49, 100), Ev(1.150, "off", D, 38),
        # refused, each for its own reason
        Ev(1.200, "cc", V, 64, 127), Ev(1.201, "pb", V, 9000), Ev(1.202, "pc", V, 5),
        Ev(1.203, "cc", V, 1, 64), Ev(1.204, "on", 2, 60, 100), Ev(1.205, "on", D, 37, 100),
        Ev(1.206, "on", V, 127, 100), Ev(1.207, "cc", V, 121, 0), Ev(1.208, "cat", V, 50),
        Ev(1.209, "sysex"), Ev(1.210, "rt", a=0xF8), Ev(1.211, "pat", V, 60, 30),
        Ev(1.212, "on", V, 5, 100),
        # panic by All Notes Off with two keys down; their later releases are no-ops
        Ev(1.300, "on", V, 50, 100), Ev(1.350, "on", V, 53, 100), Ev(1.400, "cc", V, 123, 0),
        Ev(1.450, "off", V, 50), Ev(1.460, "off", V, 53),
        # panic by All Sound Off on the drum channel, under a held note and a kick
        Ev(1.500, "on", V, 57, 100), Ev(1.500, "on", D, 36, 127), Ev(1.600, "cc", D, 120, 0),
        Ev(1.700, "on", V, 45, 100), Ev(1.800, "off", V, 45),
    ]
    # knob bursts: cutoff swept at 1 kHz, resonance at 500 Hz
    ev += [Ev(0.700 + i * 0.001, "cc", V, 74, min(127, i * 127 // 100)) for i in range(101)]
    ev += [Ev(0.700 + i * 0.002, "cc", V, 71, 20 + i * 2) for i in range(31)]
    return sorted(ev, key=lambda e: e.t)


def sc_pressure() -> list:
    """Offered load well beyond the wire: most toms and many notes are REFUSED
    for queue pressure, knobs are rate-limited, and nothing is late."""
    V, D = C.VOICE_CHANNEL, C.DRUM_CHANNEL
    ev = []
    toms = (41, 45, 48)
    for i in range(150):                                   # a tom roll at 500 hits/s
        ev.append(Ev(0.002 * i, "on", D, toms[i % 3], 90 + i % 30))
    for i in range(60):                                    # a trill at 200 notes/s
        n = 60 if i % 2 == 0 else 62
        ev.append(Ev(0.005 * i + 0.0005, "on", V, n, 100))
        ev.append(Ev(0.005 * i + 0.0035, "off", V, n))
    for i in range(300):                                   # three knobs at 1 kHz each
        ev.append(Ev(0.001 * i + 0.0002, "cc", V, 74, (i * 3) % 128))
        ev.append(Ev(0.001 * i + 0.0004, "cc", V, 71, (i * 5) % 128))
        ev.append(Ev(0.001 * i + 0.0006, "cc", V, 7, 40 + (i % 60)))
    ev += [Ev(0.320, "cc", V, 123, 0),
           Ev(0.400, "on", V, 60, 100), Ev(0.400, "on", D, 36, 120), Ev(0.500, "off", V, 60)]
    return sorted(ev, key=lambda e: e.t)


def sc_sustained(seconds: float = C.SUSTAINED_S, seed: int = C.SUSTAINED_SEED) -> list:
    """The DECLARED LOAD (live_midi_contract.LATENCY_TARGET): a mono line of 8
    note-ons/s with 40-140 ms gates, 808 drums on a 16th grid at 120 bpm with at
    most three stops a step and a tom fill every two bars, two knobs twisted
    at up to 100 CC/s."""
    V, D = C.VOICE_CHANNEL, C.DRUM_CHANNEL
    rng = random.Random(seed)
    ev = []
    step = 60.0 / 120.0 / 4.0
    n_steps = int(seconds / step)
    scale = [40, 43, 45, 47, 48, 50, 52, 55, 57, 59, 60, 62, 64, 67, 69, 71, 72, 74, 76]
    for i in range(n_steps):
        t = i * step
        j = rng.uniform(0.0, 0.004)                         # human timing
        note = rng.choice(scale)
        ev.append(Ev(t + j, "on", V, note, rng.randint(60, 127)))
        ev.append(Ev(t + j + rng.uniform(0.040, 0.140), "off", V, note))
        s16 = i % 16
        bar2 = (i // 16) % 2
        hits = []
        if s16 in (0, 8) or (s16 == 10 and rng.random() < 0.5):
            hits.append(36)
        if s16 in (4, 12):
            hits.append(38)
        if bar2 == 1 and s16 >= 12:
            hits.append((41, 45, 48, 45)[s16 - 12])
        elif s16 % 2 == 0:
            hits.append(46 if s16 == 14 else 42)
        if s16 == 0 and (i // 16) % 4 == 0:
            hits.append(49)
        for k, n in enumerate(hits[:3]):
            ev.append(Ev(t + rng.uniform(0.0, 0.002) + k * 0.0003, "on", D, n,
                         rng.randint(70, 127)))
    t = 0.3
    while t < seconds - 0.6:                               # knob twists
        cc = rng.choice((74, 71))
        rate = rng.choice((50, 100))
        v0 = rng.randint(0, 127)
        for k in range(int(0.5 * rate)):
            ev.append(Ev(t + k / rate, "cc", V, cc, max(0, min(127, v0 + rng.randint(-3, 3) + k))))
        t += rng.uniform(1.0, 2.0)
    return sorted(ev, key=lambda e: e.t)


SCENARIOS = {"coverage": sc_coverage, "pressure": sc_pressure, "sustained": sc_sustained}


# ---- the oracle: the expected schedule, built independently ------------------
@dataclass
class Entry:
    due: int
    seq: int
    write: tuple                    # (flag, sec, addr, data)
    group: int
    role: str                       # head | anchor | tail
    not_before: float
    finish: float = 0.0


@dataclass
class Group:
    gid: int
    kind: str
    event: int                      # index of the scenario event (-1: session close)
    receipt_t: float
    receipt_frame: int
    entries: list = field(default_factory=list)
    value_event: int = -1           # the message whose value the update carries
    deferred: bool = False
    pushed: int = 0


class Oracle:
    """What must happen, from the structured scenario alone."""

    def __init__(self, frame_of, patch: dict | None = None):
        self.frame_of = frame_of
        self.regs = dict(patch or vf.VoiceFx.patch_regs())
        self.mh = sh.MusicHost(patch=dict(self.regs))
        self.mh.load(0)
        self.static = [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in self.mh.w]
        for addr, key in ((stm.A_NSEL, "nsel"), (stm.A_MROUTE, "mroute"), (stm.A_MMIX, "mmix"),
                          (stm.A_MWHEEL, "mwheel"), (stm.A_MPD, "mpd"), (stm.A_MFD, "mfd")):
            self.static.append((0, 0, addr, int(self.regs[key])))
        self.static += [(0, 0, stm.A_GATE_OFF, 0), (0, 1, dx.A_STOPS, 0)]
        self.mh.w.clear()
        self.mh.events.clear()
        self.plan: list = []                              # Entry, sorted by (due, seq)
        self.slots: dict = {}
        self.seq = 0
        self.cursor = -10 ** 9
        self.groups: list = []
        self.refusals: list = []                          # (event, category)
        self.superseded: list = []                        # (event, by-event)
        self.segment: list = []                           # accepted key events since panic
        self.segment_first = True
        self.knob_last: dict = {}                         # cc -> anchor frame
        self.knob_pending: dict = {}                      # cc -> Group (unsent)
        self.strikes: dict = {}                           # stop name -> receipt frame
        self.inc_lo = phase_inc(note_hz(0))
        self.inc_hi = phase_inc(note_hz(127))
        self.mod_routed = bool(int(self.regs["mroute"]) & (vf.MR_OSC | vf.MR_FILT))

    # -- time
    def release(self, due: int) -> float:
        fa, ta = self.anchor
        return ta + (due - C.LOOKAHEAD_FRAMES - fa) / SR

    # -- placement (contract: 2 slots a frame; heads in order from the start
    #    frame; tails at anchor + offset)
    def _place(self, head, tail, g0):
        used = {}

        def take(f):
            while self.slots.get(f, 0) + used.get(f, 0) >= C.WRITE_SLOTS:
                f += 1
            used[f] = used.get(f, 0) + 1
            return f
        out, f = [], g0
        for i, w in enumerate(head):
            f = take(f)
            out.append((f, w, "anchor" if i == len(head) - 1 else "head"))
        anchor = f
        prev = anchor
        for off, w in sorted(tail, key=lambda x: x[0]):
            ft = take(max(anchor + off, prev))
            out.append((ft, w, "tail"))
            prev = ft
        return out, anchor

    def _project(self, new_entries, reserve: int) -> bool:
        """Merge and project the wire: packets leave at max(due - L, their
        group's receipt) in (due, seq) order, one after another."""
        merged = sorted(self.plan + new_entries, key=lambda e: (e.due, e.seq))
        first = min(merged.index(e) for e in new_entries)
        finish = merged[first - 1].finish if first > 0 else -1e9
        res_s = reserve * EVENT_PKT_S
        ok = True
        fins = []
        for e in merged[first:]:
            start = max(self.release(e.due), e.not_before, finish)
            finish = start + EVENT_PKT_S
            fins.append((e, finish))
            if self.frame_of(finish + res_s) > e.due - 1 - C.DEADLINE_MARGIN_FRAMES:
                ok = False
        return ok, merged, fins

    def _schedule(self, g: Group, head, tail, t, *, conditional, nominal=None,
                  deferred=False) -> bool:
        r = g.receipt_frame
        base = r + C.LOOKAHEAD_FRAMES if nominal is None else nominal
        g0 = base if deferred else max(base, self.cursor)
        pending = sum(1 for e in self.plan if self.release(e.due) > t)
        if conditional and pending + len(head) + len(tail) > C.HOST_QUEUE_MAX_PACKETS:
            return False
        push = 0
        while True:
            placed, anchor = self._place(head, tail, g0 + push)
            ents = []
            for f, w, role in placed:
                ents.append(Entry(f, self.seq + len(ents), w, g.gid, role, t))
            ok, merged, fins = self._project(ents, C.ADMISSION_RESERVE_PACKETS if conditional else 0)
            if ok:
                break
            if conditional:
                return False
            push += 1
            if push > C.MAX_PUSH_FRAMES:
                placed, anchor = self._place(head, tail, g0)
                ents = [Entry(f, self.seq + i, w, g.gid, role, t)
                        for i, (f, w, role) in enumerate(placed)]
                ok, merged, fins = self._project(ents, 0)
                push = -1                                   # deadline at risk, recorded
                break
        self.seq += len(ents)
        for e in ents:
            self.slots[e.due] = self.slots.get(e.due, 0) + 1
        for e, fin in fins:
            e.finish = fin
        self.plan = merged
        g.entries = ents
        g.pushed = push
        g.deferred = deferred
        if not deferred:
            self.cursor = anchor
        self.groups.append(g)
        return True

    # -- the musical front end
    def _group(self, kind, idx, t) -> Group:
        return Group(len(self.groups), kind, idx, t, self.frame_of(t), value_event=idx)

    def _keys(self, idx, op, note) -> list:
        evs = [(i, o, n) for i, o, n in self.segment] + [(idx, op, note)]
        out = vf.KeyHost().writes(evs, self.regs, first_from_reset=self.segment_first)
        writes = []
        for f, kind, *args in out:
            if f != idx:
                continue
            if kind == "INC":
                k, v, jump = args
                writes.append((1 if jump else 0, 0, stm.A_INC + k, v & 0xFFFFFFFF))
            elif kind == "TRACK":
                writes.append((0, 0, stm.A_TRACK, args[0] & 0xFFFFFFFF))
            elif kind == "GATE":
                writes.append((0, 0, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0))
            else:
                raise AssertionError(f"KeyHost op {kind} outside the single-trigger contract")
        return writes

    def _panic(self, idx, t):
        g = self._group("panic", idx, t)
        self._schedule(g, [(0, 0, stm.A_GATE_OFF, 0), (0, 1, dx.A_STOPS, 0)], [], t,
                       conditional=False)
        # the reference host's pitch memory survives a panic: a later note
        # glides from the last pitch -- unless no note has sounded yet
        if any(op == "on" for _i, op, _n in self.segment):
            self.segment_first = False
        self.segment = []

    def event(self, idx: int, t: float, e: Ev):
        V, D = C.VOICE_CHANNEL, C.DRUM_CHANNEL
        refuse = lambda cat: self.refusals.append((idx, cat))      # noqa: E731
        kind = e.kind
        if kind == "on" and e.b == 0:
            kind = "off"
        if kind == "rt":
            refuse("realtime")
            return
        if kind == "sysex":
            refuse("sysex")
            return
        if e.ch not in (V, D):
            refuse("channel")
            return
        if kind == "cc" and e.a in (120, 123):
            self._panic(idx, t)
            return
        if e.ch == V:
            if kind in ("on", "off"):
                note = e.a
                if kind == "on":
                    incs = [phase_inc(note_hz(note) * 2.0 ** (dt / 12.0))
                            for dt in self.regs["detune"]]
                    if not all(self.inc_lo <= v <= self.inc_hi for v in incs):
                        refuse("domain")
                        return
                writes = self._keys(idx, kind, note)
                if writes:
                    g = self._group("note-" + kind, idx, t)
                    if not self._schedule(g, writes, [], t, conditional=(kind == "on")):
                        refuse("queue-pressure")
                        return
                self.segment.append((idx, kind, note))
                return
            if kind == "cc":
                if e.a == 64:
                    refuse("sustain")
                    return
                name, value = cc_value(e.a, e.b)
                if name is None:
                    refuse("cc-unsupported")
                    return
                if name == "mod" and not self.mod_routed:
                    refuse("mod-unrouted")
                    return
                self._knob(idx, t, e.a, name, value)
                return
            refuse({"pb": "pitch-bend", "pc": "program-change", "cat": "aftertouch",
                    "pat": "poly-aftertouch"}[kind])
            return
        # the drum channel
        if kind == "off":
            return                                    # one-shot voices: no-op by contract
        if kind == "cc":
            refuse("sustain" if e.a == 64 else "cc-unsupported")
            return
        if kind != "on":
            refuse({"pb": "pitch-bend", "pc": "program-change", "cat": "aftertouch",
                    "pat": "poly-aftertouch"}[kind])
            return
        name = DRUM_MAP.get(e.a)
        if name is None:
            refuse("unmapped-drum")
            return
        r = self.frame_of(t)
        pair = HAT_PAIR if name in HAT_PAIR else {name}
        if any(r - self.strikes[p] < C.SIMULTANEOUS_FRAMES for p in pair if p in self.strikes):
            refuse("simultaneous")
            return
        stop = dx.STOP_NAMES.index(name)
        image = dict(self.mh.image)
        n0 = len(self.mh.w)
        self.mh.hits([(0, stop, accent_of(e.b))])
        new = self.mh.w[n0:]
        del self.mh.w[n0:]
        self.mh.events.clear()
        head = [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in new if w.frame == 0]
        tail = [(w.frame, (w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF)) for w in new if w.frame]
        # the stop bit's rising edge is the audible instant: it is the head's LAST write
        assert head[-1][2] == dx.A_STOPS
        g = self._group("hit", idx, t)
        if not self._schedule(g, head, tail, t, conditional=True):
            self.mh.image = image
            refuse("queue-pressure")
            return
        self.strikes[name] = r

    def _knob(self, idx, t, cc, name, value):
        regs = dict(self.mh.regs)
        n0 = len(self.mh.w)
        if name == "mod":
            self.mh.voice(0, stm.A_MWHEEL, value, tag="mod")
        else:
            self.mh.knob(0, name, value)
        writes = [(w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF) for w in self.mh.w[n0:]]
        del self.mh.w[n0:]
        self.mh.events.clear()
        del regs
        p = self.knob_pending.get(cc)
        if p is not None and self.release(p.entries[0].due) > t:
            # not yet sent: this value supersedes the pending one, same frames
            for ent, w in zip(p.entries, writes):
                ent.write = w
            self.superseded.append((p.value_event, idx))
            p.value_event = idx
            return
        r = self.frame_of(t)
        nominal = r + C.LOOKAHEAD_FRAMES
        deferred = False
        if cc in self.knob_last and self.knob_last[cc] + C.KNOB_INTERVAL_FRAMES > nominal:
            nominal = self.knob_last[cc] + C.KNOB_INTERVAL_FRAMES
            deferred = True
        g = self._group("knob", idx, t)
        self._schedule(g, writes, [], t, conditional=False, nominal=nominal, deferred=deferred)
        self.knob_last[cc] = g.entries[-1].due
        self.knob_pending[cc] = g

    def build(self, events: list, times: list, anchor, close_t: float) -> dict:
        self.anchor = anchor
        for i, (e, t) in enumerate(zip(events, times)):
            self.event(i, t, e)
        self._panic(-1, close_t)                          # the session's own cleanup
        timed = sorted((e for g in self.groups for e in g.entries), key=lambda e: (e.due, e.seq))
        pos = {id(e): i for i, e in enumerate(timed)}
        anchors = []
        for g in self.groups:
            a = next(e for e in g.entries if e.role == "anchor")
            anchors.append({"group": g.gid, "kind": g.kind, "event": g.event,
                            "value_event": g.value_event, "index": pos[id(a)],
                            "due": a.due, "deferred": g.deferred, "pushed": g.pushed})
        return {"static": self.static,
                "timed": [(e.due, *e.write) for e in timed],
                "anchors": anchors, "refusals": self.refusals,
                "superseded": self.superseded}


# ---- one run of the session on the simulated device ---------------------------
def session_class(stub: bool = False):
    if stub:
        sys.path.insert(0, str(ROOT / "fpga/stubs"))
        import midi_session_stub as ms
    else:
        import midi_session as ms
    return ms


def run_session(scenario: str, *, inject: str | None = None, epoch: int = 0,
                stub: bool = False, seconds: float | None = None) -> dict:
    ms = session_class(stub)
    events = (SCENARIOS[scenario](seconds) if (scenario == "sustained" and seconds)
              else SCENARIOS[scenario]())
    clock = dev.SimClock()
    sim = dev.UartDeviceSim(epoch_frame=epoch, clock=clock)
    ser = dev.SimSerial(sim)
    s = ms.MidiSession(ser, clock=clock, inject={inject} if inject else set())
    s.start()
    t0 = clock.t + 0.020
    times = [t0 + e.t for e in events]
    for t, e in zip(times, events):
        s.service(t)
        s.feed(t, enc(e))
    s.close()
    ser.run_until(clock.t + RTL_TAIL_S)
    return {"scenario": scenario, "inject": inject, "events": events, "times": times,
            "session": s, "sim": sim, "ser": ser, "clock": clock, "epoch": epoch}


def truth_frame(run: dict, t: float) -> int:
    """The device frame containing host instant t, on the device's own
    timeline, numbered as the session numbers frames (its anchor fixes which
    65536-frame revolution is which; the counter itself is 16 bits)."""
    sim = run["sim"]
    return run["epoch"] + math.floor((t - sim._t0) * SR) + run.get("revolution", 0)


def set_revolution(run: dict, anchor) -> None:
    run["revolution"] = 0
    d = anchor[0] - truth_frame(run, anchor[1])
    run["revolution"] = 65536 * round(d / 65536)


def _unwrap(frames16: list, start: int) -> list:
    out, prev, acc = [], start & 0xFFFF, start
    for f in frames16:
        d = (f - prev) & 0xFFFF
        acc += d if d < 0x8000 else d - 0x10000
        prev = f
        out.append(acc)
    return out


# ---- the checks -----------------------------------------------------------------
def props_of(timed: list) -> dict:
    """Decompose a timed write stream into the named properties."""
    V_KNOB = {stm.A_CUT_LO, stm.A_CUT_HI, stm.A_K, stm.A_GAIN, stm.A_OGAIN, stm.A_VOL,
              stm.A_MWHEEL}
    gate, pitch, knobs, strikes, coeffs = [], [], [], [], []
    stops = 0
    for f, flag, sec, addr, data in timed:
        if sec == 0 and addr in (stm.A_GATE_ON, stm.A_GATE_OFF):
            gate.append((f, addr == stm.A_GATE_ON))
        elif sec == 0 and (stm.A_INC <= addr < stm.A_INC + 3 or addr == stm.A_TRACK):
            pitch.append((f, flag, addr, data))
        elif sec == 0 and addr in V_KNOB:
            knobs.append((f, addr, data))
        elif sec == 1 and addr == dx.A_STOPS:
            rising = data & ~stops
            for b in range(dx.N_STOPS):
                if rising >> b & 1:
                    strikes.append((f, dx.STOP_NAMES[b]))
            stops = data
        elif sec == 1:
            coeffs.append((f, addr, data))
        else:
            coeffs.append((f, ("voice", addr), data))
    return {"voice_gate": gate, "voice_pitch": pitch, "knobs": knobs,
            "drum_strikes": strikes, "drum_coeffs": coeffs}


def seq_diff(got: list, want: list) -> int:
    """How many elements differ (inserted, deleted or replaced)."""
    sm = difflib.SequenceMatcher(a=want, b=got, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def gate_open_frames(gate: list, end: int) -> set:
    out, on_at = set(), None
    for f, on in gate:
        if on and on_at is None:
            on_at = f
        elif not on and on_at is not None:
            out.update(range(on_at, f))
            on_at = None
    if on_at is not None:
        out.update(range(on_at, end))
    return out


def pctl(xs: list, p: float) -> float:
    if not xs:
        return float("nan")
    ys = sorted(xs)
    k = max(0, min(len(ys) - 1, math.ceil(p / 100.0 * len(ys)) - 1))
    return ys[k]


def check(run: dict, *, target: bool = False) -> dict:
    s, sim, ser = run["session"], run["sim"], run["ser"]
    res = {"scenario": run["scenario"], "inject": run["inject"], "props": {}, "reasons": [],
           "preconditions": []}
    # -- the one number the session chooses: its time map, against the device
    anchor = getattr(s, "anchor", None)
    if anchor is None:
        res["preconditions"].append("the session published no time anchor")
        res["verdict"] = "NO VERDICT"
        return res
    set_revolution(run, anchor)
    worst = max((abs(s.frame_of(t) - truth_frame(run, t)) for t in run["times"]), default=0)
    res["time_map_worst_error_frames"] = worst
    if worst > 1:
        res["preconditions"].append(f"the session's time map is {worst} frames off the device")
    if sim.resets:
        res["preconditions"].append("the device reset during the run")
    if res["preconditions"]:
        res["verdict"] = "NO VERDICT"
        return res
    exp = Oracle(s.frame_of).build(run["events"], run["times"], anchor, s.closed_at)
    res["expected"] = {"static": len(exp["static"]), "timed": len(exp["timed"]),
                       "refusals": len(exp["refusals"]), "superseded": len(exp["superseded"])}
    live = [w for w in sim.writes if w[5] == "live"]
    evw = [w for w in sim.writes if w[5] == "event"]
    frames = _unwrap([w[0] for w in evw], anchor[0])
    got = [(f, w[1], w[2], w[3], w[4] & 0xFFFFFFFF) for f, w in zip(frames, evw)]
    want = exp["timed"]
    end = max([g[0] for g in got] + [w[0] for w in want] + [0]) + 1
    P = res["props"]

    def put(name, moved, detail):
        P[name] = {"moved": bool(moved), "detail": detail}

    got_static = [(w[1], w[2], w[3], w[4] & 0xFFFFFFFF) for w in live]
    put("static_image", got_static != exp["static"],
        f"{len(got_static)} live writes executed, {len(exp['static'])} expected; "
        f"{seq_diff(got_static, exp['static'])} differ")
    pg, pw = props_of(got), props_of(want)
    for name in ("voice_gate", "voice_pitch", "drum_strikes", "drum_coeffs", "knobs"):
        d = seq_diff(pg[name], pw[name])
        first = None
        if d:
            sm = difflib.SequenceMatcher(a=pw[name], b=pg[name], autojunk=False)
            op = next(o for o in sm.get_opcodes() if o[0] != "equal")
            first = {"expected": pw[name][op[1]:op[2]][:2], "got": pg[name][op[3]:op[4]][:2]}
        put(name, d, f"{d} of {len(pw[name])} differ" + (f"; first {first}" if first else ""))
    # timing: writes equal in value, compared in frame
    vals_g = [g[1:] for g in got]
    vals_w = [w[1:] for w in want]
    sm = difflib.SequenceMatcher(a=vals_w, b=vals_g, autojunk=False)
    off, worst_off, first_off = 0, 0, None
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            dw, dg = want[blk.a + k][0], got[blk.b + k][0]
            if dw != dg:
                off += 1
                worst_off = max(worst_off, abs(dg - dw))
                if first_off is None:
                    first_off = {"write": list(want[blk.a + k][1:]), "expected": dw, "got": dg}
    put("timing", off, f"{off} value-matched writes off their frame, worst {worst_off}"
        + (f"; first {first_off}" if first_off else ""))
    stuck = gate_open_frames(pg["voice_gate"], end) - gate_open_frames(pw["voice_gate"], end)
    put("stuck_notes", stuck, f"{len(stuck)} frames with the gate open where the schedule "
        f"closes it ({len(stuck) * 1000 / SR:.1f} ms)")
    # refusals: the session's record, mapped to scenario events by (time, bytes)
    by_key = {}
    for i, (t, e) in enumerate(zip(run["times"], run["events"])):
        by_key.setdefault((t, enc(e)), []).append(i)
    got_ref = []
    for rf in getattr(s, "refusals", []):
        idxs = by_key.get((rf.t, bytes(rf.raw)), [])
        got_ref.append((idxs.pop(0) if idxs else None, rf.category))
    got_ref.sort(key=lambda x: (x[0] is None, x[0] or 0))
    d = seq_diff(got_ref, exp["refusals"])
    put("refusals", d, f"{len(got_ref)} refused, {len(exp['refusals'])} expected, {d} differ"
        + (f"; expected {sorted(set(c for _, c in exp['refusals']))}" if d else ""))
    st = getattr(s, "stats", {})
    qbad = []
    if sim.evq_peak > C.DEVICE_QUEUE_BOUND:
        qbad.append(f"device queue peak {sim.evq_peak} > {C.DEVICE_QUEUE_BOUND}")
    if st.get("host_queue_peak", 0) > C.HOST_QUEUE_MAX_PACKETS:
        qbad.append(f"host queue peak {st.get('host_queue_peak')}")
    if sim.errors or sim.drops:
        qbad.append(f"device errors {sorted({e[0] for e in sim.errors})}, drops {sim.drops}")
    if st.get("deadline_misses", 0):
        qbad.append(f"{st['deadline_misses']} packets projected past their deadline")
    # the release's own validator (#255, written for the CLI, not for this
    # session) over everything the device executed, in apply order
    import qualified_domain as qd
    try:
        dom = qd.check_stream(got_static + [g[1:] for g in got], initial="unknown",
                              mod_initial="unknown")
        put("release_domain", False, f"{dom['inc_writes']} increment writes, "
            f"{dom['glide_transitions']} glides, all inside the qualified domain")
    except qd.Rejected as exc:
        put("release_domain", True, f"qualified_domain REJECTED the executed stream: {exc}")
    put("queues", qbad, "; ".join(qbad) or
        f"device queue peak {sim.evq_peak}, host queue peak {st.get('host_queue_peak')}, "
        "no drops, errors or late packets")
    # latency, from truth: the device frame containing the receipt instant to
    # the frame the anchor executed in, for every update whose value is its own
    lat, comp, stale = [], [], []
    # latency from truth: the device frame containing the receipt instant to
    # the frame the anchor executed in. Writes are paired with the schedule by
    # VALUE alignment, so a control that moves some writes still measures the
    # rest; an anchor with no counterpart has no latency.
    sent = [(t, pkt) for t, data in ser.tx_log for pkt in _packets(data)
            if pkt[0] == dev.OP_EVENT]
    acc_u = _unwrap([v[0] for k, v in sim.received if k == "event"], anchor[0])
    pair = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            pair[blk.a + k] = blk.b + k
    unpaired = 0
    for a in exp["anchors"]:
        if a["event"] < 0:
            continue                                 # the session's own closing panic
        j = pair.get(a["index"])
        if j is None or j >= len(sent) or j >= len(acc_u):
            unpaired += 1
            continue
        t_r = run["times"][a["value_event"]]
        f_exec, r_true, t_send = got[j][0], truth_frame(run, t_r), sent[j][0]
        lat.append((f_exec - r_true) * 1000.0 / SR)
        comp.append({"kind": a["kind"],
                     "host_hold_ms": (t_send - t_r) * 1000.0,
                     "uart_ms": (acc_u[j] - truth_frame(run, t_send)) * 1000.0 / SR,
                     "device_queue_ms": (f_exec - acc_u[j]) * 1000.0 / SR})
    for old, new in exp["superseded"]:
        a = next((x for x in exp["anchors"] if x["value_event"] == new), None)
        if a is not None and pair.get(a["index"]) is not None:
            stale.append((got[pair[a["index"]]][0] - truth_frame(run, run["times"][old]))
                         * 1000.0 / SR)
    dist = {"n": len(lat), "min_ms": min(lat, default=None), "p50_ms": pctl(lat, 50),
            "p95_ms": pctl(lat, 95), "p99_ms": pctl(lat, 99), "max_ms": max(lat, default=None),
            "histogram_ms": _hist(lat),
            "components_mean_ms": {k: round(sum(c[k] for c in comp) / len(comp), 4)
                                   for k in ("host_hold_ms", "uart_ms", "device_queue_ms")}
            if comp else {},
            "components_max_ms": {k: round(max(c[k] for c in comp), 4)
                                  for k in ("host_hold_ms", "uart_ms", "device_queue_ms")}
            if comp else {},
            "superseded_staleness": {"n": len(stale), "p50_ms": pctl(stale, 50),
                                     "p95_ms": pctl(stale, 95), "max_ms": max(stale, default=None)},
            "pushed_events": sum(1 for a in exp["anchors"] if a["pushed"]),
            "anchors_unpaired": unpaired,
            "by_kind_max_ms": {}}
    for c, x in zip(comp, lat):
        dist["by_kind_max_ms"][c["kind"]] = max(dist["by_kind_max_ms"].get(c["kind"], 0.0), x)
    res["latency"] = dist
    lbad = []
    if target and not lat:
        lbad.append("no latency measured (the write streams did not match)")
    elif target:
        if dist["p95_ms"] > C.LATENCY_TARGET["p95_ms"]:
            lbad.append(f"p95 {dist['p95_ms']:.2f} ms > {C.LATENCY_TARGET['p95_ms']}")
        if dist["p99_ms"] > C.LATENCY_TARGET["p99_ms"]:
            lbad.append(f"p99 {dist['p99_ms']:.2f} ms > {C.LATENCY_TARGET['p99_ms']}")
    put("latency", lbad, "; ".join(lbad) or (
        f"n {dist['n']}, p50 {dist['p50_ms']:.2f}, p95 {dist['p95_ms']:.2f}, "
        f"p99 {dist['p99_ms']:.2f}, max {dist['max_ms']:.2f} ms" if lat else
        f"not measured: {unpaired} anchors had no executed counterpart")
        + (" (target applies)" if target else " (reported; the target applies to `sustained`)"))
    if target:
        dropped = [c for _, c in exp["refusals"] if c == "queue-pressure"]
        pushed = dist["pushed_events"]
        put("load_admitted", bool(dropped or pushed),
            f"{len(dropped)} events refused for queue pressure and {pushed} pushed late "
            "under the DECLARED load (both must be 0: a supported load is played whole)")
    res["session_stats"] = st
    res["refusal_categories"] = sorted({c for _, c in exp["refusals"]})
    res["reasons"] = [f"{k}: {v['detail']}" for k, v in P.items() if v["moved"]]
    res["verdict"] = "FAIL" if res["reasons"] else "PASS"
    res["_run"] = run
    res["_exp"] = exp
    return res


def _hist(xs: list) -> dict:
    out = {}
    for x in xs:
        b = f"{math.floor(x):d}-{math.floor(x) + 1:d}"
        out[b] = out.get(b, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: int(kv[0].split("-")[0])))


def _packets(data: bytes) -> list:
    sizes = {dev.OP_WRITE: 8, dev.OP_EVENT: 10, dev.OP_STATUS: 2, dev.OP_ABORT: 2}
    out, i = [], 0
    while i < len(data):
        n = sizes[data[i]]
        out.append(data[i:i + n])
        i += n
    return out


# ---- controls -----------------------------------------------------------------
CONTROLS = {
    "DROP_NOTE_OFF": ("coverage", ("voice_gate", "stuck_notes"),
                      "a lost note-off leaves the gate open past its release"),
    "WRONG_DRUM_MAP": ("coverage", ("drum_strikes",),
                       "GM 38 strikes the clap, not the snare"),
    "DELAYED_EVENT": ("coverage", ("timing",),
                      "one event's writes land 5 ms after their frame"),
}
PROPS = ("static_image", "voice_gate", "voice_pitch", "drum_strikes", "drum_coeffs", "knobs",
         "timing", "stuck_notes", "refusals", "release_domain", "queues", "latency",
         "load_admitted")


def run_control(name: str, *, stub: bool = False) -> dict:
    scenario, must, reason = CONTROLS[name]
    r = check(run_session(scenario, inject=name, stub=stub))
    moved = {p: r["props"][p]["moved"] for p in PROPS if p in r["props"]}
    caught = (r["verdict"] == "FAIL" and all(moved.get(p) for p in must))
    return {"control": name, "scenario": scenario, "intended_reason": reason,
            "must_move": list(must), "caught": caught, "verdict": r["verdict"],
            "matrix": {p: ("MOVED" if m else "BLIND") for p, m in moved.items()},
            "latency_max_ms": r.get("latency", {}).get("max_ms"),
            "reasons": r["reasons"], "preconditions": r["preconditions"]}


# ---- the RTL replay of the session's bytes --------------------------------------
def write_rtl_capture(res: dict, prefix: Path) -> dict:
    """<prefix>.cmds/.plan.json in verify_uart_bridge's replay format: the
    STIMULUS is the session's transmit log (the bytes it wrote, when it wrote
    them); the EXPECTATION is the oracle's schedule. An epoch-0 run, so device
    frames and bench frames coincide."""
    run, exp = res["_run"], res["_exp"]
    ser = run["ser"]
    if run["epoch"] != 0 or run["sim"].resets:
        raise ValueError("RTL capture needs an epoch-0 run with no reset")
    bc = 10 * uh.CLK_HZ / uh.DEFAULT_BAUD
    div = (uh.CLK_HZ + uh.DEFAULT_BAUD // 2) // uh.DEFAULT_BAUD
    rows, si, ti = [], 0, 0
    wire_free = 0
    for t, data in ser.tx_log:
        send = int(t * SR) + 1
        for pkt in _packets(data):
            start = max(send * uh.CYC_PER_FRAME + 1, wire_free)
            wire_free = start + len(pkt) * bc
            push = start + (len(pkt) - 1) * bc + 4 + 9 * div + div // 2
            accept = int(push // uh.CYC_PER_FRAME)
            row = {"index": len(rows), "packet": pkt.hex(), "send_frame": send,
                   "accept_frame": accept}
            if pkt[0] == dev.OP_WRITE:
                f, s_, a, d = exp["static"][si]
                si += 1
                row.update(kind="write", due=-1, apply_frame=accept + 1,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == dev.OP_EVENT:
                due, f, s_, a, d = exp["timed"][ti]
                ti += 1
                row.update(kind="event", due=due, apply_frame=due,
                           expect={"flag": f, "sec": s_, "addr": a, "data": d})
            elif pkt[0] == dev.OP_STATUS:
                row.update(kind="status", due=-1, apply_frame=-1, expect=None)
            else:
                raise ValueError(f"unexpected opcode 0x{pkt[0]:02x} in the session's log")
            rows.append(row)
    if si != len(exp["static"]) or ti != len(exp["timed"]):
        raise ValueError(f"capture has {si}/{ti} writes/events, schedule "
                         f"{len(exp['static'])}/{len(exp['timed'])}")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{prefix}.cmds", "w") as fh:
        for r in rows:
            fh.write(f"S {r['send_frame']} {bytes.fromhex(r['packet']).hex(' ')}\n")
    rec = {"origin": 0, "baud": uh.DEFAULT_BAUD, "base_send_frame": 0,
           "source": "MidiSession SimSerial transmit log; expectations from the oracle",
           "rows": rows}
    Path(f"{prefix}.plan.json").write_text(json.dumps(rec, indent=1) + "\n")
    return {"rows": len(rows), "writes": si, "events": ti,
            "last_due": max((r["due"] for r in rows), default=0)}


def rtl_replay(res: dict, outdir: Path, *, reuse: bool = False, timeout_s: int = 10_800) -> dict:
    import verify_uart_bridge as vub
    name = res["scenario"] + (f"-{res['inject']}" if res["inject"] else "")
    if res["verdict"] != "PASS" and not res["inject"]:
        return {"state": "NO VERDICT", "reason": "the sim run is not clean; replaying it "
                "would test nothing", "sim_reasons": res["reasons"]}
    cap = write_rtl_capture(res, outdir / name)
    rr = vub.simulate_replay(str(outdir / name), ROOT / "build/live-midi-rtl" / name,
                             tail_frames=int(RTL_TAIL_S * SR), timeout_s=timeout_s, reuse=reuse)
    if rr is None:
        return {"state": "NO VERDICT", "reason": "the RTL replay did not run", "capture": cap}
    ok, comp, detail = vub.analyze(rr)
    receipt = Path(rr["outdir"]) / "run_identity.json"
    published = outdir / f"{name}.run_identity.json"
    published.write_bytes(receipt.read_bytes())
    return {"state": "PASS" if ok else "FAIL", "capture": cap, "comparison": comp,
            "detail": detail[:10], "reused_rtl_run": bool(rr.get("reused")),
            "run_receipt": {"path": published.name,
                            "sha256": hashlib.sha256(published.read_bytes()).hexdigest()}}


# ---- main -------------------------------------------------------------------------
def summary(r: dict) -> str:
    if r["verdict"] == "NO VERDICT":
        return f"NO VERDICT -- {r['preconditions']}"
    lat = r.get("latency", {})
    return (f"{r['verdict']} -- timed {r['expected']['timed']}, refusals "
            f"{r['expected']['refusals']} ({', '.join(r['refusal_categories'])}), "
            f"superseded {r['expected']['superseded']}; latency n {lat.get('n')} "
            f"p95 {lat.get('p95_ms')} p99 {lat.get('p99_ms')} max {lat.get('max_ms')} ms"
            + ("" if r["verdict"] == "PASS" else f" -- {r['reasons']}"))


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--outdir", type=Path, default=REPORT_DIR)
    ap.add_argument("--start-red", action="store_true",
                    help="run the harness against fpga/stubs/midi_session_stub.py")
    ap.add_argument("--rtl", nargs="*", default=None, metavar="SCENARIO",
                    help="also replay the session's bytes through the UART RTL wrapper")
    ap.add_argument("--rtl-inject", nargs="*", default=[], metavar="CONTROL",
                    choices=("WRONG_DRUM_MAP", "DELAYED_EVENT"),
                    help="also replay these controls' bytes through the RTL: the I2S "
                         "comparison must FAIL. (DROP_NOTE_OFF changes the packet count, "
                         "so its bytes cannot be paired with the schedule write for write; "
                         "it is caught at the device contract.)")
    ap.add_argument("--reuse-rtl", action="store_true")
    ap.add_argument("--sustained-s", type=float, default=C.SUSTAINED_S)
    ap.add_argument("--rtl-sustained-s", type=float, default=3.0,
                    help="length of the sustained session replayed through the RTL")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--control", choices=sorted(CONTROLS), default=None,
                    help="run ONE injected control on the sim and record it (trial child)")
    ap.add_argument("--expect-fail", action="store_true",
                    help="with --control: exit 0 only when it is caught by its own property")
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)
    if a.control:
        c = run_control(a.control)
        rec = {"tool": "fpga/verify_live_midi.py", "trial": "T-LIVE-MIDI",
               "criterion_version": C.CRITERION_VERSION, "control": c,
               "verdict": c["verdict"]}
        (a.json or a.outdir / "verification.json").write_text(
            json.dumps(_clean(rec), indent=1, default=str) + "\n")
        cols = " ".join(f"{p}={'M' if v == 'MOVED' else '.'}" for p, v in c["matrix"].items())
        print(f"live-midi control {a.control}: {'CAUGHT' if c['caught'] else 'MISSED'} "
              f"(must move {c['must_move']}) -- {cols}")
        if a.expect_fail:
            return 0 if c["caught"] else 1
        return {"PASS": 0, "FAIL": 1}.get(c["verdict"], 2)
    record = {"tool": "fpga/verify_live_midi.py", "trial": "T-LIVE-MIDI",
              "criterion_version": C.CRITERION_VERSION, "timing_contract": C.TIMING_CONTRACT,
              "lookahead_ms": C.LOOKAHEAD_MS, "target": C.LATENCY_TARGET,
              "stub": a.start_red, "clean": {}, "controls": {}}
    verdicts = []
    for name, kw in (("coverage", {}), ("coverage@65000", {"epoch": 65000}),
                     ("pressure", {}), ("sustained", {"seconds": a.sustained_s})):
        sc = name.split("@")[0]
        r = check(run_session(sc, stub=a.start_red, **kw), target=(sc == "sustained"))
        verdicts.append(r["verdict"])
        record["clean"][name] = _clean(r)
        print(f"live-midi[{name}]: {summary(r)}")
        if sc == "pressure" and r["verdict"] == "PASS" and not any(
                c == "queue-pressure" for _, c in r["_exp"]["refusals"]):
            print("live-midi[pressure]: NO VERDICT -- the scenario exerted no queue pressure")
            verdicts.append("NO VERDICT")
    for name in CONTROLS:
        c = run_control(name, stub=a.start_red)
        record["controls"][name] = c
        verdicts.append("PASS" if c["caught"] else "FAIL")
        cols = " ".join(f"{p}={'M' if v == 'MOVED' else '.'}" for p, v in c["matrix"].items())
        print(f"live-midi control {name}: {'CAUGHT' if c['caught'] else 'MISSED'} "
              f"(must move {c['must_move']}) -- {cols}")
    if a.rtl is not None or a.rtl_inject:
        record["rtl"] = {}
        for sc in ([] if a.rtl is None else (a.rtl or ["coverage"])):
            kw = {"seconds": a.rtl_sustained_s} if sc == "sustained" else {}
            r = check(run_session(sc, **kw))
            rr = rtl_replay(r, a.outdir / "rtl-replay", reuse=a.reuse_rtl)
            record["rtl"][sc] = rr
            verdicts.append(rr["state"])
            comp = rr.get("comparison", {})
            print(f"live-midi RTL[{sc}]: {rr['state']} -- writes {comp.get('writes_seen')}/"
                  f"{comp.get('writes_sent')}, timing bad {comp.get('frame_pred_bad')}, "
                  f"I2S mismatch {comp.get('wire_mismatch')} over {comp.get('periods')} periods"
                  + (f" -- {rr.get('detail') or rr.get('reason')}" if rr["state"] != "PASS" else ""))
        for ctl in a.rtl_inject:
            r = check(run_session(CONTROLS[ctl][0], inject=ctl))
            rr = rtl_replay(r, a.outdir / "rtl-replay", reuse=a.reuse_rtl)
            comp = rr.get("comparison", {})
            caught = rr["state"] == "FAIL" and (comp.get("wire_mismatch", 0) > 0)
            rr["caught_by_i2s"] = caught
            record["rtl"][f"control-{ctl}"] = rr
            verdicts.append("PASS" if caught else "FAIL")
            print(f"live-midi RTL control {ctl}: {'CAUGHT' if caught else 'MISSED'} -- "
                  f"I2S mismatch {comp.get('wire_mismatch')} over {comp.get('periods')} periods")
    if "NO VERDICT" in verdicts:
        verdict = "NO VERDICT"
    elif all(v == "PASS" for v in verdicts):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    if a.start_red:
        # the stub must be RED for a reason a check names, never a crash
        verdict = "FAIL" if verdict == "PASS" else verdict
    record["verdict"] = verdict
    out = a.json or (a.outdir / ("start-red.json" if a.start_red else "verification.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_clean(record), indent=1, default=str) + "\n")
    print(f"T-LIVE-MIDI: {verdict}")
    return {"PASS": 0, "FAIL": 1}.get(verdict, 2)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""fpga/uart_host.py -- the USB-UART control bridge, HOST side.

The Arty's FTDI USB-UART is the control link a musician actually has: no SPI
header, just the connector the board is programmed through. This module is the
host half of that link. `spi_host.py` remains the reference for WHAT a musical
event becomes (48-bit DR 0007 revision 2 register frames, named presets, the
KeyHost note logic); this module is about DELIVERY over a byte stream, and the
one rule the project plan makes non-negotiable:

    TIMED EVENTS EXECUTE ON THE DEVICE. Host-side sleeps cannot honour an
    audio-frame deadline over USB, so they are not used. The host uploads
    commands stamped with the FRAME each write applies in; the device's event
    queue fires them.

THE WIRE. 115200 8N1 by default, LSB-first bytes. Every packet is

    opcode payload... checksum

with checksum = two's complement of the byte sum (mod 256), so a corrupted
packet is REJECTED at the device and reported, never half-applied. The payload
of a write is exactly the SPI framing's six bytes, MSB first:
{F, 6'b0, SEC, A[7:0], D[31:0]}.

PACKETS (host -> device):
  W 0x57  6 reg bytes ck          write now: applies at the start of the frame
                                  after the packet is accepted (the live path)
  E 0x45  due_lsb due_msb 6 ck    scheduled write: applies AT the due frame,
                                  executed from the device's event queue
  Q 0x51  ck                      status query; device answers on its TX
  X 0x58  ck                      abort: clear both queues (queued, unexecuted
                                  events are discarded; counters survive)

PACKETS (device -> host):
  ACK    06 seq                   accepted into a queue
  ERR    1C code seq info         1 write-queue overflow, 2 event-queue
                                  overflow, 3 due in the past / out of order,
                                  4 resync (framing error or byte gap),
                                  5 bad checksum, 6 bad opcode. `info` is the
                                  register address for drops.
  STATUS 55 fr1 fr0 evq wrq drops errs flags
                                  flags bit3..0: {resync, late, wrq_ovf, evq_ovf}

THE CONTRACT (mirrors rtl-sketch/uart_bridge.v; the bench checks the RTL
against THIS module, and the host refuses to schedule what the contract
cannot deliver):

  * accepted commands are ordered: within each queue, FIFO, never reordered;
  * per frame the device executes at most WRITE_SLOTS = 2 register writes for
    the UART path (the two cycles after the SPI drain window -- the SPI link
    keeps priority by construction), due-scheduled first, then live writes;
  * a scheduled event accepted with due >= accept_frame + 1 lands in EXACTLY
    its due frame (the deadline the SPI host could only predict);
  * a live write applies at accept_frame + 1, +-1 on real hardware from
    link-phase quantisation -- the live path has no deadline to keep;
  * both queues have published depths (EVENT_QUEUE = 64, WRITE_QUEUE = 8).
    Overflow drops the arriving packet, is counted, and is REPORTED on the
    device TX -- never silent;
  * a note-off is a live write: it is never blocked by a full event queue,
    and if it is ever dropped the ERR packet names its register address;
  * reset (BTN0 or clock unlock) clears both queues and counters. A queued
    phrase dies with the reset BY CONSTRUCTION (the queue is RAM behind the
    reset); the host detects it: status after reset shows an empty queue and
    a frame counter that did not advance as scheduled;
  * timestamps are 16-bit frames, wrap-safe within +-32768 frames (1.37 s
    modulo window). `plan()` refuses anything wider.

FLOW CONTROL (the contract this module implements and the preflight enforces):

  The device's event queue is 64 deep and the wire delivers one event packet
  per `budget()["frames_per_event_packet"]` frames. A phrase the queue cannot
  hold in flight is a phrase the device will DROP, and a dropped event is
  music that silently does not happen. The host's answer, in order of
  preference:

  1. PRELOAD: if the whole phrase's peak in-flight demand (deterministically
     computed by `preflight()` from the plan's ideal acceptance times) fits
     the queue, send it all; the device fires each event at its own due.
  2. WATERMARK BATCHING: otherwise the host must hold packets back, polling
     STATUS (the device's own evq count, ACK/ERR codes) until the queue
     drains below the watermark before sending more. That works only if the
     wire's sustained event rate meets the fixture's due rate; if it does
     not, the fixture is REFUSED with the exact packet index and reason.
  3. REFUSE: the host never relies on the device dropping packets, and never
     silently re-times a fixture to fit the wire. `preflight()` is the gate;
     REFUSED exits 2 with the packet index, the queue peak and the bandwidth
     numbers.

`DeviceClock`/`plan()` are the timing model. They are derived from the device
contract above -- NOT from USB sleeps -- and `--dry-run` prints exactly what
would go down the wire and when it would land, before any hardware is opened.

REFUSAL is a first-class outcome: no pyserial, no port, no status answer, or a
schedule the contract cannot deliver -- all exit 2 with the reason, and never
fall through to a best-effort attempt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ("model", "audition"):
    _q = os.path.join(ROOT, _p)
    if _q not in sys.path:
        sys.path.insert(0, _q)

# ---- the device contract (mirror of rtl-sketch/uart_bridge.v) ---------------
CLK_HZ = 12_288_000
CYC_PER_FRAME = 256
SR = 48_000
FRAME_S = Fraction(CYC_PER_FRAME, CLK_HZ)

DEFAULT_BAUD = 115_200
UART_DATA_BITS = 8                 # 8N1: start + 8 data (LSB first) + stop
BITS_PER_BYTE = 10

OP_WRITE = 0x57
OP_EVENT = 0x45
OP_STATUS = 0x51
OP_ABORT = 0x58
RSP_ACK = 0x06
RSP_ERR = 0x1C
RSP_STATUS = 0x55

ERR_WRQ_FULL = 1
ERR_EVQ_FULL = 2
ERR_DUE = 3
ERR_RESYNC = 4
ERR_CHECKSUM = 5
ERR_OPCODE = 6
ERR_NAMES = {1: "write-queue overflow", 2: "event-queue overflow",
             3: "due in the past or out of order", 4: "resync",
             5: "bad checksum", 6: "bad opcode"}

EVENT_QUEUE_DEPTH = 64
WRITE_QUEUE_DEPTH = 8
WRITE_SLOTS = 2                    # register-write slots the UART path gets per frame
WRAP_HALF = 32_768                 # 16-bit due arithmetic, wrap-safe window
MIN_LEAD_FRAMES = 2                # due must be >= accept_frame + MIN_LEAD at send time
PREFLIGHT_WATERMARK = 48           # batching threshold the preflight simulates
PLAN_SLACK_FRAMES = 500            # minimum due-minus-acceptance margin (~10 ms):
SEND_GATE_FRAMES = 120             # the send gate: send once the first due is at
                                   # least this far ahead of the verify STATUS
                                   # (2.5 ms -- beyond that we re-anchor, and a
                                   # saturated host may take a few rounds to get
                                   # there, but a schedule sent from a fresh
                                   # anchor is never a stale one)
                                   # covers USB/host arrival jitter. It is NOT
                                   # the anchor-to-send latency budget -- that is
                                   # re-anchored away by run()'s verify loop --
                                   # because for a phrase whose dues run 1 frame
                                   # apart, every frame of preload margin is a
                                   # queued event: margin and queue depth are
                                   # coupled, and the preflight holds both.


class Refused(Exception):
    """A first-class outcome, distinct from pass and fail: the link, the
    device or the schedule cannot deliver this. main() prints the reason and
    exits 2; it never falls through to a best-effort attempt."""
    pass


def checksum(payload: bytes) -> int:
    return (-sum(payload)) & 0xFF


def reg_frame(flag: int, sec: int, addr: int, data: int) -> bytes:
    """The SPI framing's six bytes, MSB first: {F, 6'b0, SEC, A[7:0], D[31:0]}."""
    word = (((flag & 1) << 47) | ((sec & 1) << 40)
            | ((addr & 0xFF) << 32) | (data & 0xFFFFFFFF))
    return word.to_bytes(6, "big")


def decode_reg_frame(b: bytes) -> tuple:
    if len(b) != 6:
        raise ValueError("register frame must be 6 bytes")
    word = int.from_bytes(b, "big")
    if (word >> 41) & 0x3F:
        raise ValueError(f"reserved bits set in {word:012x}")
    return ((word >> 47) & 1, (word >> 40) & 1, (word >> 32) & 0xFF, word & 0xFFFFFFFF)


def pkt_write(flag: int, sec: int, addr: int, data: int) -> bytes:
    body = bytes([OP_WRITE]) + reg_frame(flag, sec, addr, data)
    return body + bytes([checksum(body)])


def pkt_event(due: int, flag: int, sec: int, addr: int, data: int) -> bytes:
    if not 0 <= due <= 0xFFFF:
        raise ValueError("due must be a 16-bit frame index")
    body = bytes([OP_EVENT, due & 0xFF, (due >> 8) & 0xFF]) + reg_frame(flag, sec, addr, data)
    return body + bytes([checksum(body)])


def pkt_status() -> bytes:
    body = bytes([OP_STATUS])
    return body + bytes([checksum(body)])


def pkt_abort() -> bytes:
    body = bytes([OP_ABORT])
    return body + bytes([checksum(body)])


# ---- parsing the device's TX stream -----------------------------------------
@dataclass
class DevicePacket:
    kind: str                       # "ack" | "err" | "status" | "boot"
    seq: int = -1
    code: int = -1
    info: int = -1
    frame: int = -1
    evq: int = -1
    wrq: int = -1
    drops: int = -1
    errs: int = -1
    flags: int = -1

    def describe(self) -> str:
        if self.kind == "ack":
            return f"ACK seq {self.seq}"
        if self.kind == "err":
            return (f"ERR {ERR_NAMES.get(self.code, self.code)} (code {self.code})"
                    f" seq {self.seq} info 0x{self.info:02x}")
        if self.kind == "boot":
            return "BOOT 0xA5 (device left reset)"
        return (f"STATUS frame {self.frame} evq {self.evq} wrq {self.wrq}"
                f" drops {self.drops} errs {self.errs} flags 0x{self.flags:02x}")


def scan_packets(buf: bytes) -> tuple:
    """Resynchronising scan for BOOT/ACK/ERR/STATUS packets, with consumption:
    returns (packets, consumed) where `consumed` is the offset just past the
    last COMPLETE packet. An incomplete tail stays buffered, so a caller that
    drops `buf[:consumed]` can never serve a stale reply twice. BOOT (0xA5,
    one byte at reset release) is how a host SEES a device reset on the wire
    rather than inferring it."""
    out, i, consumed = [], 0, 0
    while i < len(buf):
        b = buf[i]
        if b == RSP_ACK and i + 1 < len(buf):
            out.append(DevicePacket("ack", seq=buf[i + 1])); i += 2; consumed = i
        elif b == RSP_ERR and i + 3 < len(buf):
            out.append(DevicePacket("err", code=buf[i + 1], seq=buf[i + 2],
                                    info=buf[i + 3])); i += 4; consumed = i
        elif b == RSP_STATUS and i + 7 < len(buf):
            out.append(DevicePacket("status", frame=(buf[i + 1] << 8) | buf[i + 2],
                                    evq=buf[i + 3], wrq=buf[i + 4], drops=buf[i + 5],
                                    errs=buf[i + 6], flags=buf[i + 7])); i += 8; consumed = i
        elif b == 0xA5:
            out.append(DevicePacket("boot")); i += 1; consumed = i
        else:
            i += 1
    return out, consumed


def parse_device_stream(buf: bytes) -> list:
    """The whole-buffer scan, for benches and logs: every parseable packet
    from the first valid header on. The live link uses `scan_packets`, which
    also says how many bytes it consumed."""
    return scan_packets(buf)[0]


# ---- the timing model: THE DEVICE CONTRACT, AS CODE -------------------------
@dataclass
class Placed:
    """One host packet, planned. Cycles are device core cycles from the origin
    the plan was anchored to; `apply_frame` is the frame the write applies in."""
    index: int
    kind: str                       # "write" | "event" | "status" | "abort"
    packet: bytes
    send_frame: int                 # frame boundary the first byte leaves at
    end_cycle: int                  # last byte's stop bit done (device cycles)
    accept_frame: int = -1          # frame the packet is accepted (push) in
    due: int = -1                   # scheduled events
    apply_frame: int = -1           # live writes: accept + 1
    note: str = ""


def byte_cycles(baud: int) -> int:
    """Device cycles per byte, as the receiver counts them: 10 bit times at
    ceil(CLK_HZ/baud). The wire's nominal rate differs by <1 %; the receiver
    re-synchronises at every start bit, so the difference never accumulates."""
    div = (CLK_HZ + baud // 2) // baud
    return BITS_PER_BYTE * div


def plan(commands: list, *, baud: int = DEFAULT_BAUD, start_frame: int = 0,
         anchor_frame: int = 0) -> list:
    """Lay packets on the wire against the device contract.

    commands: ("write", flag, sec, addr, data) | ("event", due, flag, sec, addr, data)
              | ("status",) | ("abort",) | ("wait", frames), executed in order.
              ("wait", n) sends nothing; it holds the wire idle for n frames,
              which is how a phrase's inner timing is expressed -- the DEVICE
              still fires every event at its own due frame.

    Each packet starts at the first frame boundary at or after the previous
    packet's end, so the acceptance instant sits mid-frame by
    construction. `plan` REFUSES (raises ValueError) when the schedule asks
    the contract for something it does not deliver: a due inside the
    +-32768-frame wrap window of the anchor, a due that has already passed at
    upload speed, or dues out of order.

    `anchor_frame` is the device frame that `start_frame` names (from a STATUS
    query). Frames in the result are device frames; the caller subtracts the
    anchor to get relative time.
    """
    if baud <= 0 or CLK_HZ // baud < 16:
        raise ValueError(f"baud {baud} unsupported: need CLK_HZ/baud >= 16")
    cyc_per_byte = byte_cycles(baud)
    placed, t = [], start_frame * CYC_PER_FRAME
    last_apply = -1
    last_due = None
    for index, cmd in enumerate(commands):
        kind = cmd[0]
        if kind == "wait":
            t += max(0, int(cmd[1])) * CYC_PER_FRAME
            continue
        if kind == "write":
            packet = pkt_write(*cmd[1:5])
        elif kind == "event":
            packet = pkt_event(*cmd[1:6])
        elif kind == "status":
            packet = pkt_status()
        elif kind == "abort":
            packet = pkt_abort()
        elif kind == "raw":
            packet = bytes(cmd[1])          # a deliberately damaged packet
        else:
            raise ValueError(f"unknown command kind {kind!r}")
        # first frame boundary at or after the previous packet's end
        end_prev = t
        send_frame = -(-end_prev // CYC_PER_FRAME)
        t = send_frame * CYC_PER_FRAME
        nbytes = len(packet)
        t_end = t + nbytes * cyc_per_byte
        # acceptance: stop-bit centre of the last byte, plus receiver sync
        div = (CLK_HZ + baud // 2) // baud
        push = t + (nbytes - 1) * cyc_per_byte + 4 + 9 * div + div // 2
        accept = push // CYC_PER_FRAME
        row = Placed(index=index, kind=kind, packet=packet,
                     send_frame=anchor_frame + send_frame,
                     end_cycle=anchor_frame * CYC_PER_FRAME + t_end,
                     accept_frame=anchor_frame + accept)
        if kind == "write":
            row.apply_frame = anchor_frame + accept + 1
            last_apply = max(last_apply, row.apply_frame)
        elif kind == "event":
            due = cmd[1]
            rel = (due - (anchor_frame + accept)) % (1 << 16)
            if rel >= WRAP_HALF:
                raise ValueError(f"event {index}: due {due} is at or before its "
                                 f"acceptance frame {anchor_frame + accept}")
            if rel > WRAP_HALF - 1 - (t_end - t) // CYC_PER_FRAME:
                raise ValueError(f"event {index}: due {due} is inside the wrap guard")
            if last_due is not None and kind == "event":
                span = (due - last_due) % (1 << 16)
                if span == 0 or span >= WRAP_HALF:
                    raise ValueError(f"event {index}: due {due} is not strictly "
                                     f"after the previous due {last_due}")
            last_due = due
            row.due = due
            row.apply_frame = due
        placed.append(row)
        t = t_end
    return placed


# ---- the musical front end --------------------------------------------------
def voice_image_writes(preset: str | None = None) -> list:
    """The boot image as (flag, sec, addr, data) writes, in send order.

    The same named presets `fpga/play.py --preset` accepts, through the same
    register conversion -- this module decides DELIVERY, never values."""
    import spi_host as sh
    import synth_top_model as stm
    import voice_fx as vf
    import drums_fx as dx
    import selected_preset
    regs = (selected_preset.definition(preset)["registers"] if preset
            else vf.VoiceFx.patch_regs())
    wave_code = dict(vf.WAVE_CODE)
    w = []
    for k, s in enumerate(regs["waves"]):
        w.append((0, sh.SEC_VOICE, stm.A_WAVE + k, wave_code[s]))
    for base, key in ((stm.A_AMP, "amp"), (stm.A_FILT, "fenv")):
        for j, v in enumerate(regs[key]):
            w.append((0, sh.SEC_VOICE, base + j, v))
    for addr, key in ((stm.A_CUT_LO, "cut_lo"), (stm.A_CUT_HI, "cut_hi"),
                      (stm.A_K, "k"), (stm.A_GAIN, "gain"), (stm.A_OGAIN, "ogain"),
                      (stm.A_GLIDE, "glide"), (stm.A_VOL, "vol")):
        w.append((0, sh.SEC_VOICE, addr, regs[key]))
    w.append((0, sh.SEC_VOICE, stm.A_DVOL, dx.accent_reg(0.45)))
    w.append((0, sh.SEC_VOICE, stm.A_BVOL, dx.accent_reg(0.45)))
    return w


def note_writes(note: int, on: bool, *, preset_regs: dict | None = None,
                held: bool = False) -> list:
    """One key event through the model's own KeyHost (contract 5.6): pitch
    increments, note track and the gate. `held` says a key is already down, so
    a note-off of the last key is GATE_OFF and a note-on while held is TRIG.

    An OFF is stateless by construction -- this host is a command line, it
    does not know which keys are down -- so an off ALWAYS means "release what
    is sounding": the gate-off write. A gate-off addressed to a silent voice
    is a no-op on the device; a gate-off that is not sent is a note that
    never ends. The second failure is the one that matters."""
    import voice_fx as vf
    import synth_top_model as stm
    import spi_host as sh
    if not on:
        return [(0, sh.SEC_VOICE, stm.A_GATE_OFF, 0)]
    regs = preset_regs or vf.VoiceFx.patch_regs()
    events = [(0, "on", note)] + ([(1, "off", 45)] if held else [])
    out = vf.KeyHost().writes(events, regs, first_from_reset=not held)
    writes = []
    for f, op, *args in out:
        if op == "INC":
            k, v, jump = args
            writes.append((1 if jump else 0, sh.SEC_VOICE, stm.A_INC + k, v))
        elif op == "TRACK":
            writes.append((0, sh.SEC_VOICE, stm.A_TRACK, args[0]))
        elif op == "GATE":
            writes.append((0, sh.SEC_VOICE, stm.A_GATE_ON if args[0] else stm.A_GATE_OFF, 0))
    return writes


def phrase_events(fixture: str = "bar808") -> tuple:
    """A scripted phrase as scheduled events: (due, flag, sec, addr, data),
    reusing the existing fixtures and the link's own spreading rules. Dues are
    >= 1 frame apart, which the device's two write slots deliver exactly.

    bar808 is the full musical fixture; `m5a` is the short scripted M5A
    phrase the UART bench (fpga/verify_uart_bridge.py scenario `phrase`)
    already proves feasible end to end. Whether a fixture ACTUALLY fits the
    queue and the wire is `preflight()`'s verdict, not this function's claim.
    """
    if fixture == "m5a":
        return _phrase_events_m5a()
    import fixtures
    import spi_host as sh
    host, n_frames, _cover = fixtures.FIXTURES[fixture]()
    link = sh.LinkTiming.contract_max()
    step = link.min_land_gap()
    ws = sh.feasible(sorted(host.w, key=lambda w: w.frame), link)
    out, prev = [], None
    for w in ws:
        due = w.frame if prev is None else max(w.frame, prev + 1)
        out.append((due, w.flag, w.sec, w.addr, w.data & 0xFFFFFFFF))
        prev = due
    return out, max(d for d, *_ in out) + 64


def _phrase_events_m5a() -> tuple:
    """The short M5A smoke phrase, as scheduled events. The same rendering the
    RTL bench's `phrase` scenario runs, so the CLI and the bench exercise one
    fixture -- and the bench's clean run is the evidence the CLI's phrase is
    bit-exact against the integer model.

    Dues are spread to the UART link's own event pacing (one 10-byte event
    packet per wire round-up of the packet time). The SPI reference spread its
    writes the same way through `feasible()` at the SPI budget; the smoke
    fixture's claim -- register schedule and bit-exact audio -- is invariant
    to that pacing, and the schedule the model is driven with is exactly the
    schedule the device fires. (bar808's tempo, by contrast, is load-bearing:
    that fixture is REFUSED rather than stretched.)"""
    gap = -(-10 * byte_cycles(DEFAULT_BAUD) // CYC_PER_FRAME)   # one event packet
    rtl = os.path.join(ROOT, "rtl-sketch")
    if rtl not in sys.path:
        sys.path.insert(0, rtl)
    import verify_synth_top as top
    cmds, tail, _info = top.m5a_script(
        str(os.path.join(ROOT, "docs/scorecard/mono-m5a-miniv3/manifest.json")),
        smoke=True, saw_cutoff_hz=20000, saw_volume_correction_db=-0.45428)
    f, prev, out = 0, None, []
    for wait, flag, sec, addr, data in cmds:
        f += wait
        due = f if prev is None else max(f, prev + gap)
        out.append((due, flag, sec, addr, data & 0xFFFFFFFF))
        prev = due
        f += 1
    return out, prev + 64


# ---- the tool ---------------------------------------------------------------
def _require_serial():
    try:
        import serial                          # noqa: F401
    except ImportError:
        print("uart_host: REFUSED -- pyserial is required for hardware and is not "
              "installed. Run `.venv/bin/pip install pyserial` (see README). "
              "--dry-run works without it.", file=sys.stderr)
        raise SystemExit(2)


class Bridge:
    """The open link. REFUSES rather than guessing: no port, no answer, no play.

    STATE the apparatus asserts, in one place: `origin` is the device frame
    the anchoring STATUS named, `lead_frames` is the send lead measured from
    THIS link's own STATUS round trip (a constant two-frame lead is no
    protection against USB/host delay), and every STATUS answer is read as
    the framed 8-byte packet it is, consumed from the buffer, so a stale
    snapshot can never be served twice."""

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, timeout: float = 2.0):
        _require_serial()
        import serial
        try:
            self.ser = serial.Serial(port, baud, timeout=timeout)
        except (OSError, serial.SerialException) as exc:
            print(f"uart_host: REFUSED -- cannot open {port}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        self.baud = baud
        self.buf = b""
        self.origin = None
        self.lead_frames = None
        self.status_round_trip_s = None
        self.acks_seen = 0

    def _take(self, kinds: set, deadline: float) -> DevicePacket | None:
        """Read bytes until one complete packet of `kinds` parses. The buffer
        is consumed only up to the last complete packet, so partial reads are
        retried and stale replies are never re-served."""
        while True:
            pkts, consumed = scan_packets(self.buf)
            self.buf = self.buf[consumed:]
            for p in pkts:
                if p.kind == "ack":
                    self.acks_seen += 1
                if p.kind in kinds:
                    return p
            now = time.monotonic()
            if now >= deadline:
                return None
            self.ser.timeout = min(0.05, max(0.005, deadline - now))
            chunk = self.ser.read(8)          # a STATUS packet is 8 bytes
            if chunk:
                self.buf += chunk

    def send(self, rows: list, *, paced: bool = True) -> None:
        """Write the plan to the wire, preserving its timing semantics.

        Contiguous packets -- each one the natural continuation of the
        previous packet's last byte -- go as one serial write: the WIRE paces
        them at exactly the spacing the plan assumed, so timing is preserved
        byte for byte; per-packet write syscalls (each ~2-3 ms on USB/pty
        drivers) would NOT preserve it, and would push scheduled dues into
        the past. Where the plan says the wire must idle (an explicit wait
        item), the burst is split and the device's own frame -- polled by
        STATUS, never a host sleep -- must reach the next send window first.
        Application timing is the device's throughout: live writes apply at
        accept+1, events at their due."""
        bursts: list[list] = []
        prev_end = None
        for row in rows:
            natural = None if prev_end is None else -(-prev_end // CYC_PER_FRAME)
            contiguous = (prev_end is not None
                          and row.send_frame <= (natural or 0))
            if bursts and contiguous:
                bursts[-1].append(row)
            else:
                bursts.append([row])
            prev_end = row.end_cycle
        for i, burst in enumerate(bursts):
            if i > 0 and paced:
                window = burst[0].send_frame - MIN_LEAD_FRAMES
                self.wait_until(window)
            self.ser.write(b"".join(r.packet for r in burst))
        self.ser.flush()

    def status(self, attempts: int = 3, timeout_s: float = 2.0) -> DevicePacket:
        """One STATUS question, one fresh answer, framed. The round trip is
        measured and carried in `status_round_trip_s` for the plan's lead."""
        for _ in range(attempts):
            self.buf = b""
            t0 = time.monotonic()
            self.send([Placed(0, "status", pkt_status(), 0, 0)], paced=False)
            pkt = self._take({"status"}, t0 + timeout_s)
            if pkt is not None:
                self.status_round_trip_s = time.monotonic() - t0
                return pkt
        print("uart_host: REFUSED -- no STATUS reply from the device; check the "
              "bitstream, wiring (A9/D10) and baud", file=sys.stderr)
        raise SystemExit(2)

    def run(self, commands: list, *, dry_run: bool = False, baud: int = DEFAULT_BAUD,
            quiet: bool = False, hold_frames: int = 0) -> list:
        """Origin FIRST, then the plan: the schedule is anchored to the
        device's own frame counter and the origin is applied EXACTLY ONCE
        (`start_frame` is relative to it). The plan is then preflighted; a
        fixture the queue and wire cannot deliver is REFUSED before the first
        packet leaves, with the packet index and the arithmetic.

        Because a real host takes real milliseconds to plan (and USB takes
        more to deliver), the anchor is VERIFIED before sending: a fresh
        STATUS must still leave slack before the first event's due, or the
        plan is re-anchored -- a plan whose dues died during planning is
        re-planned, never sent late."""
        rows = None
        ahead = 0
        planned = list(commands)
        for _ in range(6):
            anchor = self.status()
            self.origin = origin = anchor.frame
            round_trip = self.status_round_trip_s or 0.0
            self.lead_frames = lead = (MIN_LEAD_FRAMES
                                       + int(round_trip * SR) + 1)
            rows = plan_show(planned, hold_frames=hold_frames, baud=baud,
                             start_frame=lead, anchor_frame=origin)
            dues = [r.due for r in rows if r.kind == "event"]
            if not dues:
                break
            now = self.status()
            ahead = (dues[0] - now.frame) & 0xFFFF
            if WRAP_HALF > ahead >= SEND_GATE_FRAMES:
                break               # the first due is comfortably ahead: send
            # planning + the STATUS round trips consumed part of the margin:
            # shift the dues by the deficit (spacing intact) and re-anchor,
            # so the schedule is re-established rather than sent late. The
            # deficit never goes negative: a wrapped `ahead` means the dues
            # are BEHIND the device by 65536-ahead frames, and the correction
            # must add that, not subtract.
            behind = 0 if ahead < WRAP_HALF else (1 << 16) - ahead
            deficit = max(0, SEND_GATE_FRAMES - ahead) + behind
            planned = [c if c[0] != "event" else
                       ("event", (c[1] + deficit) & 0xFFFF, *c[2:])
                       for c in planned]
        else:
            raise Refused("the device's frame counter kept outrunning "
                          f"planning (last first-due margin {ahead} frames); "
                          "refusing to send a schedule whose dues are "
                          "already dying")
        verdict = preflight(rows, baud=baud)
        if verdict["verdict"] != "FEASIBLE":
            raise Refused(verdict["reason"])
        if dry_run:
            return rows
        self.send(rows)
        if not quiet:
            for row in rows:
                print(f"  send f{row.send_frame:<6} {row.kind:<7} "
                      f"{row.packet.hex()} -> applies f{row.apply_frame}")
        return rows

    @staticmethod
    def _reached(frame: int, target: int) -> bool:
        """Wrap-safe 'has the 16-bit frame counter reached target': true iff
        frame is at or after target within the +-32768-frame window the
        contract defines. An ordinary integer compare answers the question
        backwards for half the counter's range."""
        return ((frame - target) & 0xFFFF) < WRAP_HALF

    def wait_until(self, due_frame: int, *, timeout_s: float = 30.0) -> DevicePacket:
        deadline = time.monotonic() + timeout_s
        target = due_frame & 0xFFFF
        last = self.status()
        while not self._reached(last.frame, target):
            if time.monotonic() > deadline:
                print(f"uart_host: REFUSED -- device frame {last.frame} did not "
                      f"reach {due_frame} within {timeout_s:.0f}s", file=sys.stderr)
                raise SystemExit(2)
            time.sleep(0.02)
            last = self.status()
        return last


def render_plan(plan_rows: list, *, anchor_frame: int = 0) -> str:
    lines = [f"{'#':>3} {'send@frame':>10} {'accept':>7} {'due':>7} {'applies':>8}  bytes",
             f"{'':>3} {'contract: device time, not host sleeps':>48}"]
    for row in plan_rows:
        due = f"{row.due}" if row.due >= 0 else "-"
        accept = f"{row.accept_frame}" if row.accept_frame >= 0 else "-"
        lines.append(f"{row.index:>3} {row.send_frame:>10} {accept:>7} {due:>7} "
                     f"{row.apply_frame:>8}  {row.packet.hex()}")
    return "\n".join(lines)


def plan_shifted(commands: list, *, baud: int = DEFAULT_BAUD, start_frame: int = 0,
                 anchor_frame: int = 0) -> list:
    """plan() with the whole phrase shifted later, dues' spacing intact, until
    the contract accepts it: a phrase rendered from absolute musical frames
    often starts before its own upload finishes, and the contract refuses
    what the device cannot deliver. The shift is whole frames; the schedule
    stays the device's. EVERY event carries PLAN_SLACK_FRAMES of margin over
    its acceptance -- the last packet of a dense phrase is the one that
    otherwise arrives with none."""
    shift = PLAN_SLACK_FRAMES
    for _ in range(400):
        shifted = [c if c[0] != "event" else ("event", (c[1] + shift) & 0xFFFF, *c[2:])
                   for c in commands]
        try:
            rows = plan(shifted, baud=baud, start_frame=start_frame,
                        anchor_frame=anchor_frame)
        except ValueError as exc:
            if "acceptance" not in str(exc):
                raise
            shift += 256
            continue
        slack = _min_event_slack(rows)
        if slack >= PLAN_SLACK_FRAMES:
            return rows
        shift += PLAN_SLACK_FRAMES - slack
    raise Refused("could not plan the phrase within 400 shifts of 256 frames: "
                  "no schedule puts every due far enough past its packet's "
                  "acceptance")


def _min_event_slack(rows: list) -> int:
    """The smallest (due - acceptance) margin over a plan's events, in the
    contract's 16-bit wrap window."""
    return min((((r.due - r.accept_frame) & 0xFFFF) for r in rows
                if r.kind == "event"), default=PLAN_SLACK_FRAMES)


def plan_show(commands: list, *, hold_frames: int = 0, baud: int = DEFAULT_BAUD,
              start_frame: int = 0, anchor_frame: int = 0) -> list:
    """Plan a whole performance. A ("gate-off", flag, sec, addr, data) command
    marks the note-off: it is scheduled ON THE DEVICE as an event due at the
    note-on's gate apply frame + `hold_frames` -- the hold is the device's,
    never a host sleep (host-side sleeps cannot honour an audio-frame
    deadline over USB). Phrase events follow the gate-off, shifted later in
    whole frames so their dues stay strictly increasing and each lands after
    its packet can possibly be accepted; a shift never reorders or rescales
    musical time inside the phrase.

    `start_frame` is RELATIVE to `anchor_frame` (the device frame the STATUS
    reply named); the caller's origin enters the arithmetic exactly once."""
    import synth_top_model as stm
    markers = [i for i, c in enumerate(commands) if c[0] == "gate-off"]
    if not markers:
        return plan_shifted(commands, baud=baud, start_frame=start_frame,
                            anchor_frame=anchor_frame)
    if len(markers) > 1:
        raise Refused("more than one scheduled note-off in one plan: upload "
                      "them as separate performances")
    mi = markers[0]
    prefix, marker, rest = commands[:mi], commands[mi], commands[mi + 1:]
    for c in rest:
        if c[0] != "event":
            raise Refused(f"a ({c[0]!r} ...) command sits after the gate-off: "
                          "the scheduled note-off must come after the live "
                          "writes and before the phrase events")
    live_rows = plan(prefix, baud=baud, start_frame=start_frame,
                     anchor_frame=anchor_frame)
    gate_apply = None
    for r in live_rows:
        if r.kind == "write" and decode_reg_frame(r.packet[1:7])[2] == stm.A_GATE_ON:
            gate_apply = r.apply_frame
    if gate_apply is None:
        raise Refused("a gate-off was requested but the live writes carry no "
                      "note-on gate (A_GATE_ON) write")
    if hold_frames <= 0:
        raise Refused(f"hold_frames {hold_frames} is not a hold: the note-off "
                      "must be scheduled after the note-on's gate lands")
    gate_off_due = (gate_apply + int(hold_frames)) & 0xFFFF
    if gate_off_due == gate_apply:
        raise Refused(f"hold_frames {hold_frames} wraps the 16-bit due onto "
                      "the gate frame itself")
    # the gate-off's own packet must be accepted strictly before its due;
    # a hold shorter than the packet's upload time is refused, not bent
    try:
        probe = plan(prefix + [("event", gate_off_due, *marker[1:5])],
                     baud=baud, start_frame=start_frame, anchor_frame=anchor_frame)
    except ValueError as exc:
        raise Refused(f"hold_frames {hold_frames} is shorter than the gate-off "
                      f"packet's own upload time: {exc}") from exc
    gate_off_row = probe[-1]
    first_phrase = min((c[1] for c in rest), default=None)
    offset = 0
    if first_phrase is not None:
        # the phrase starts after the note ends: one ordered due sequence,
        # carrying the same planning-slack margin as every scheduled batch
        offset = max(0, gate_off_due + 1 - first_phrase) + PLAN_SLACK_FRAMES
    for attempt in range(400):
        if attempt == 0:
            pass
        ev_cmds = [("event", gate_off_due & 0xFFFF, *marker[1:5])]
        ev_cmds += [("event", (c[1] + offset) & 0xFFFF, *c[2:]) for c in rest]
        # the gate-off event goes on the wire FIRST: its due is ~hold_frames
        # in the future, while the boot image ahead of it is live writes the
        # device applies whenever they arrive. Sending the event after 20-odd
        # live packets would tie its arrival to the whole upload and push it
        # past its due on a slow host; sending it first makes the deliverable
        # deadline the device's, not the host's.
        try:
            rows = plan(ev_cmds[:1] + prefix + ev_cmds[1:], baud=baud,
                        start_frame=start_frame, anchor_frame=anchor_frame)
        except ValueError as exc:
            if "acceptance" not in str(exc):
                raise
            offset += 256
            continue
        # the gate-on's apply frame in THIS layout is what the hold is
        # measured against; the leading event moved it by one packet
        # position, so re-derive the due once from the final layout
        gate_apply_final = None
        for r in rows:
            if r.kind == "write" and decode_reg_frame(r.packet[1:7])[2] == stm.A_GATE_ON:
                gate_apply_final = r.apply_frame
        if gate_apply_final is not None and (gate_apply_final + int(hold_frames)) & 0xFFFF != (gate_off_due & 0xFFFF):
            gate_off_due = (gate_apply_final + int(hold_frames)) & 0xFFFF
            if attempt >= 398:
                raise Refused("could not settle the gate-off due")
            continue
        slack = min((((r.due - r.accept_frame) & 0xFFFF) for r in rows
                     if r.kind == "event"
                     and r.due != (gate_off_due & 0xFFFF)),
                    default=PLAN_SLACK_FRAMES)
        if slack >= PLAN_SLACK_FRAMES:
            return rows
        offset += PLAN_SLACK_FRAMES - slack
    raise Refused("could not plan the phrase within 400 shifts of 256 frames")


def preflight(rows: list, *, baud: int = DEFAULT_BAUD,
              event_queue: int = EVENT_QUEUE_DEPTH,
              watermark: int = PREFLIGHT_WATERMARK) -> dict:
    """Queue AND bandwidth feasibility, deterministic from the plan's ideal
    acceptance times. Nothing here is measured on a live link: the plan says
    when each packet is accepted and each event is due, and arithmetic says
    whether the device's 64-deep queue absorbs the difference.

    In-flight demand at the moment packet i is accepted is every earlier
    event whose due is still in the future. Peak demand over the whole plan
    against the queue depth is the whole verdict:

      * peak <= event_queue: FEASIBLE by preload -- send it all; the device
        fires each event at its own due and nothing is dropped.
      * peak >  event_queue: the watermark schedule (hold packets back until
        STATUS shows the queue below the watermark) is simulated; it lands
        every event before its due only if the wire's sustained event rate
        meets the fixture's due rate. If it cannot, REFUSED -- naming the
        first packet whose deadline fails, the peak, the index where the
        queue first overflows, and the bandwidth arithmetic.

    The host never relies on the device dropping packets and never silently
    re-times a fixture to fit the wire."""
    cyc = byte_cycles(baud)
    packet_frames = {8: 8 * cyc / CYC_PER_FRAME, 10: 10 * cyc / CYC_PER_FRAME}
    ev_rows = [r for r in rows if r.kind == "event"]
    total_bytes = sum(len(r.packet) for r in rows)
    report = {
        "verdict": "FEASIBLE", "baud": baud, "packets": len(rows),
        "events": len(ev_rows), "bytes": total_bytes,
        "event_queue": event_queue, "watermark": watermark,
        "frames_per_event_packet": round(packet_frames[10], 1),
    }
    if not ev_rows:
        report["reason"] = "no scheduled events: nothing can queue"
        return report
    # dues are 16-bit and strictly increasing in plan order: unwrap them so
    # the arithmetic is ordinary integers
    unwrapped, prev = [], None
    for r in ev_rows:
        d = r.due
        if prev is not None:
            while d <= prev:
                d += 1 << 16
        unwrapped.append(d)
        prev = d
    acc = [r.accept_frame for r in ev_rows]
    span = unwrapped[-1] - unwrapped[0] + 1
    report["due_span_frames"] = span
    report["wire_frames"] = -(-total_bytes * cyc // CYC_PER_FRAME)
    in_seq = [unwrapped[i] - unwrapped[0] for i in range(len(ev_rows))]
    avg_gap = in_seq[-1] / max(1, len(ev_rows) - 1)
    report["avg_frames_between_dues"] = round(avg_gap, 1)

    def inflight_curve():
        peak, peak_i, first_excess = 0, 0, None
        for i in range(len(ev_rows)):
            n = sum(1 for j in range(i + 1) if unwrapped[j] > acc[i])
            if n > peak:
                peak, peak_i = n, i
            if n > event_queue and first_excess is None:
                first_excess = i
        return peak, peak_i, first_excess

    peak, peak_i, first_excess = inflight_curve()
    report["peak"] = peak
    report["peak_index"] = peak_i
    report["first_excess_index"] = first_excess
    if peak <= event_queue:
        report["mode"] = "preload"
        report["reason"] = (f"peak in-flight demand {peak} fits the "
                            f"{event_queue}-deep event queue: preload the "
                            "whole schedule, the device fires at each due")
        return report

    # watermark batching: hold packets back until the queue drains below the
    # watermark, then resume. Send times slide; dues do not. A held packet
    # still costs the wire its own transmission time -- the pacing constraint
    # chains through the holds (accept_i >= prev_accept + packet wire time).
    pace = [-(-len(r.packet) * cyc // CYC_PER_FRAME) for r in ev_rows]
    queued, prev_acc, first_miss = [], None, None
    for i, (a, d) in enumerate(zip(acc, unwrapped)):
        if prev_acc is None:
            earliest = a
        else:
            earliest = max(a, prev_acc + pace[i])
        if len(queued) >= watermark:
            need = len(queued) - watermark + 1
            resume = sorted(queued)[need - 1] + 1     # fires free slots next frame
            accept = max(earliest, resume)
        else:
            accept = earliest
        prev_acc = accept
        queued = [q for q in queued if q > accept]
        queued.append(d)
        if first_miss is None and accept >= d:
            first_miss = {"index": i, "accept": accept, "due": d}
    report["watermark_first_miss"] = first_miss
    if first_miss is None:
        report["verdict"] = "FEASIBLE"
        report["mode"] = "watermark"
        report["reason"] = (f"peak demand {peak} exceeds the queue; the "
                            f"watermark-{watermark} schedule lands every "
                            "event before its due")
        return report
    i = first_miss["index"]
    report["verdict"] = "REFUSED"
    report["mode"] = "none"
    report["reason"] = (
        f"event packet {i} cannot be delivered in time: earliest acceptance "
        f"frame {first_miss['accept']} is at or after its due "
        f"{first_miss['due']}. The wire carries one 10-byte event packet per "
        f"{packet_frames[10]:.1f} frames at {baud} baud but this fixture "
        f"schedules one every {avg_gap:.1f} frames; the queue's peak demand "
        f"is {peak} against a depth of {event_queue} (first excess at packet "
        f"index {first_excess}). The fixture cannot preload and flow control "
        f"cannot keep up: REFUSED at {baud} baud. Musical dues are not "
        "silently moved to fit the wire.")
    return report


def _row_expect(r):
    """The decoded intent of one planned row: what the device should do."""
    if r.kind == "write":
        flag, sec, addr, data = decode_reg_frame(r.packet[1:7])
    elif r.kind == "event":
        flag, sec, addr, data = decode_reg_frame(r.packet[3:9])
    else:
        return None
    return {"flag": flag, "sec": sec, "addr": addr, "data": data}


def write_capture(prefix: str, rows: list, *, origin: int,
                  baud: int = DEFAULT_BAUD) -> str:
    """The exact bytes the tool emits, in plan order, with the schedule the
    planner gave them: `<prefix>.cmds` in the RTL bench's S-line format and
    `<prefix>.plan.json` with every row (rebased so frame 0 is the first
    send). The bench path (fpga/verify_uart_bridge.py --replay) feeds these
    bytes through the UART RX of the real wrapper -- what is verified there
    is what this CLI emits, not a bench-scripted lookalike."""
    base = rows[0].send_frame if rows else 0
    parent = os.path.dirname(os.path.abspath(prefix))
    os.makedirs(parent, exist_ok=True)
    cmds_path, plan_path = f"{prefix}.cmds", f"{prefix}.plan.json"
    with open(cmds_path, "w") as fh:
        for row in rows:
            fh.write(f"S {row.send_frame - base} {row.packet.hex(' ')}\n")
    # "packet" is what the tool EMITS (the stimulus); "expect" is the decoded
    # schedule the tool INTENDED (the expectation). Keeping them separate lets
    # a replay mutate the emitted bytes and still know what should have
    # happened -- a stimulus-only mutation is otherwise invisible by
    # construction, which is exactly the kind of self-consistent pass this
    # repository does not trust.
    record = {
        "origin": origin, "baud": baud, "base_send_frame": base,
        "rows": [{"index": r.index, "kind": r.kind,
                  "packet": r.packet.hex(),
                  "expect": _row_expect(r),
                  "send_frame": r.send_frame - base,
                  "accept_frame": r.accept_frame - base,
                  "due": (r.due - base) if r.due >= 0 else -1,
                  "apply_frame": r.apply_frame - base} for r in rows],
    }
    with open(plan_path, "w") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    return cmds_path


def budget(baud: int = DEFAULT_BAUD) -> dict:
    """The link's numbers, from the contract. Quoted wherever a schedule is."""
    cyc = byte_cycles(baud)
    per_frame = CYC_PER_FRAME / cyc
    return {
        "baud": baud,
        "bytes_per_s": CLK_HZ / cyc,
        "frames_per_write_packet": CYC_PER_FRAME / (8 * cyc),
        "frames_per_event_packet": CYC_PER_FRAME / (10 * cyc),
        "event_packets_per_frame": per_frame / 10,
        "write_slots_per_frame": WRITE_SLOTS,
        "event_queue": EVENT_QUEUE_DEPTH,
        "write_queue": WRITE_QUEUE_DEPTH,
        "max_sustained_event_rate_per_kframe": per_frame / 10 * 1000,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=os.environ.get("UART_BRIDGE_PORT", ""))
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--preset", default=None, help="named selected preset to load")
    ap.add_argument("--note", type=int, default=None, help="MIDI note for on/off/run")
    ap.add_argument("--hold-frames", type=int, default=1920, help="note hold (40 ms default x48)")
    ap.add_argument("--fixture", default=None,
                    help="scripted phrase: bar808 (full fixture; preflighted), "
                         "m5a (the short phrase the UART bench proves), "
                         "none (no phrase; run's note-only mode)")
    ap.add_argument("--dry-run", action="store_true",
                    help="render the exact byte schedule and landing frames; no hardware")
    ap.add_argument("--capture", default=None, metavar="PREFIX",
                    help="write the exact emitted bytes to PREFIX.cmds (RTL bench "
                         "S-line format) and PREFIX.plan.json, for replay through "
                         "the wrapper sim (fpga/verify_uart_bridge.py --replay)")
    # the same flags on every subcommand, so `run --note 45` and
    # `--note 45 run` both parse
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS defaults: a subparser that does not see the flag must not
    # clobber the value the top-level parser already parsed
    common.add_argument("--port", default=argparse.SUPPRESS,
                        help="serial device (or UART_BRIDGE_PORT)")
    common.add_argument("--baud", type=int, default=argparse.SUPPRESS)
    common.add_argument("--preset", default=argparse.SUPPRESS)
    common.add_argument("--note", type=int, default=argparse.SUPPRESS)
    common.add_argument("--hold-frames", type=int, default=argparse.SUPPRESS)
    common.add_argument("--fixture", default=argparse.SUPPRESS)
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    common.add_argument("--capture", default=argparse.SUPPRESS, metavar="PREFIX",
                        help="write the exact emitted bytes to PREFIX.cmds (RTL bench "
                             "S-line format) and PREFIX.plan.json, for replay through "
                             "the wrapper sim (fpga/verify_uart_bridge.py --replay)")
    sub = ap.add_subparsers(dest="cmd")
    for name, help_text in (("load", "load the preset image"),
                            ("note-on", "start a note"),
                            ("note-off", "release a note"),
                            ("play", "replay a scripted phrase, scheduled on the device"),
                            ("run", "ONE command: preset, note on, hold, off, phrase"),
                            ("status", "query the device"),
                            ("abort", "clear both queues")):
        sub.add_parser(name, parents=[common], help=help_text)
    a = ap.parse_args(argv)

    commands = []
    phrase = None
    if a.cmd in ("load", "run") or a.cmd is None:
        for flag, sec, addr, data in voice_image_writes(a.preset):
            commands.append(("write", flag, sec, addr, data))
    if a.cmd == "note-on" or (a.cmd == "run" and a.note is not None):
        note = a.note if a.note is not None else 45
        for flag, sec, addr, data in note_writes(note, True):
            commands.append(("write", flag, sec, addr, data))
    if a.cmd == "note-off":
        # a standalone off releases what is sounding: the live gate-off write
        note = a.note if a.note is not None else 45
        for flag, sec, addr, data in note_writes(note, False):
            commands.append(("write", flag, sec, addr, data))
    if a.cmd == "run" and a.note is not None:
        # the run's note-off is a DEVICE-side event: gate lands, the device
        # counts hold_frames, the gate goes off -- no host sleep involved
        note = a.note if a.note is not None else 45
        for flag, sec, addr, data in note_writes(note, False):
            commands.append(("gate-off", flag, sec, addr, data))
    if a.cmd == "play" or a.cmd == "run":
        fixture = a.fixture or "bar808"
        if fixture != "none":
            events, _end = phrase_events(fixture)
            for due, flag, sec, addr, data in events:
                commands.append(("event", due, flag, sec, addr, data))
    if a.cmd == "status":
        bridge = Bridge(a.port, a.baud)
        print(bridge.status().describe())
        return 0
    if a.cmd == "abort":
        bridge = Bridge(a.port, a.baud)
        bridge.send([Placed(0, "abort", pkt_abort(), 0, 0)])
        print("sent abort (queued events discarded)")
        return 0
    if not commands:
        ap.print_usage(); return 2

    if a.dry_run:
        try:
            rows = plan_show(commands, hold_frames=a.hold_frames, baud=a.baud)
            verdict = preflight(rows, baud=a.baud)
        except Refused as exc:
            print(f"uart_host: REFUSED -- {exc}", file=sys.stderr)
            return 2
        if a.capture:
            write_capture(a.capture, rows, origin=0, baud=a.baud)
        b = budget(a.baud)
        print(f"uart_host: {len(rows)} packets, {sum(len(r.packet) for r in rows)} bytes "
              f"at {b['baud']} baud; device schedule (frames are device frames):")
        print(render_plan(rows))
        print(f"contract: {b['write_slots_per_frame']} write slots/frame, "
              f"event queue {b['event_queue']}, write queue {b['write_queue']}, "
              f"1 event packet per {1/b['frames_per_event_packet']:.1f} frames at this baud")
        print(f"preflight: {verdict['verdict']} ({verdict.get('mode', 'none')}) -- "
              f"{verdict['reason']}")
        if verdict["verdict"] != "FEASIBLE":
            print(f"uart_host: REFUSED -- {verdict['reason']}", file=sys.stderr)
            return 2
        return 0

    if not a.port:
        print("uart_host: REFUSED -- no --port given (or UART_BRIDGE_PORT); "
              "use --dry-run for the schedule without hardware", file=sys.stderr)
        return 2
    try:
        bridge = Bridge(a.port, a.baud)
        rows = bridge.run(commands, baud=a.baud, hold_frames=a.hold_frames)
    except Refused as exc:
        print(f"uart_host: REFUSED -- {exc}", file=sys.stderr)
        return 2
    if a.capture:
        write_capture(a.capture, rows, origin=bridge.origin, baud=a.baud)
    acked_before = bridge.acks_seen
    last = max(r.apply_frame for r in rows)
    end = bridge.wait_until(last + 64)
    acked = bridge.acks_seen - acked_before
    if acked < len(rows):
        # the device ACKs every accepted packet; a packet with no ACK never
        # arrived -- and nothing on the wire announces an absence
        print(f"uart_host: FAIL -- {len(rows) - acked} of {len(rows)} packets "
              "were never ACKed by the device", file=sys.stderr)
        return 1
    st = bridge.status()
    print(f"uart_host: done; device frame {st.frame}, evq {st.evq}, wrq {st.wrq}, "
          f"drops {st.drops}, errs {st.errs}, flags 0x{st.flags:02x}")
    if st.drops or st.errs or (st.flags & 0x0F):
        print("uart_host: FAIL -- the device reported dropped or corrupted traffic",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

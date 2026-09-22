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

`DeviceClock`/`plan()` are the timing model. They are derived from the device
contract above -- NOT from USB sleeps -- and `--dry-run` prints exactly what
would go down the wire and when it would land, before any hardware is opened.

REFUSAL is a first-class outcome: no pyserial, no port, no status answer, or a
schedule the contract cannot deliver -- all exit 2 with the reason, and never
fall through to a best-effort attempt.
"""
from __future__ import annotations

import argparse
import os
import sys
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


def parse_device_stream(buf: bytes) -> list:
    """Resynchronising scan for BOOT/ACK/ERR/STATUS packets. A byte stream that
    lost sync still yields every parseable packet from the first valid header
    on. BOOT (0xA5, one byte at reset release) is how a host SEES a device
    reset on the wire rather than inferring it."""
    out, i = [], 0
    while i < len(buf):
        b = buf[i]
        if b == RSP_ACK and i + 1 < len(buf):
            out.append(DevicePacket("ack", seq=buf[i + 1])); i += 2
        elif b == RSP_ERR and i + 3 < len(buf):
            out.append(DevicePacket("err", code=buf[i + 1], seq=buf[i + 2],
                                    info=buf[i + 3])); i += 4
        elif b == RSP_STATUS and i + 7 < len(buf):
            out.append(DevicePacket("status", frame=(buf[i + 1] << 8) | buf[i + 2],
                                    evq=buf[i + 3], wrq=buf[i + 4], drops=buf[i + 5],
                                    errs=buf[i + 6], flags=buf[i + 7])); i += 8
        elif b == 0xA5:
            out.append(DevicePacket("boot")); i += 1
        else:
            i += 1
    return out


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
                     end_cycle=t_end, accept_frame=anchor_frame + accept)
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
    a note-off of the last key is GATE_OFF and a note-on while held is TRIG."""
    import voice_fx as vf
    import synth_top_model as stm
    import spi_host as sh
    regs = preset_regs or vf.VoiceFx.patch_regs()
    kind = "on" if on else "off"
    events = [(0, kind, note)] + ([(1, "off", 45)] if kind == "on" and held else [])
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
    >= 1 frame apart, which the device's two write slots deliver exactly."""
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
    """The open link. REFUSES rather than guessing: no port, no answer, no play."""

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

    def _read_some(self) -> bytes:
        chunk = self.ser.read(64)
        if chunk:
            self.buf += chunk
        return chunk

    def drain(self, seconds: float = 0.3):
        self.ser.timeout = seconds
        self._read_some()
        self.ser.timeout = 2.0

    def send(self, packets: list) -> None:
        self.ser.write(b"".join(p.packet for p in packets))
        self.ser.flush()

    def status(self, attempts: int = 3) -> DevicePacket:
        for _ in range(attempts):
            self.send([Placed(0, "status", pkt_status(), 0, 0)])
            self.drain(0.5)
            pkts = [p for p in parse_device_stream(self.buf) if p.kind == "status"]
            if pkts:
                self.buf = b""
                return pkts[-1]
        print("uart_host: REFUSED -- no STATUS reply from the device; check the "
              "bitstream, wiring (A9/D10) and baud", file=sys.stderr)
        raise SystemExit(2)

    def run(self, commands: list, *, dry_run: bool = False, baud: int = DEFAULT_BAUD,
            quiet: bool = False) -> list:
        """Plan, show, and (unless dry-run) execute. Returns the plan."""
        plan_rows = plan(commands, baud=baud)
        if dry_run:
            return plan_rows
        anchor = self.status()
        origin = anchor.frame
        plan_rows = plan_shifted(commands, baud=baud,
                                 start_frame=origin + MIN_LEAD_FRAMES,
                                 anchor_frame=origin)
        self.send(plan_rows)
        if not quiet:
            for row in plan_rows:
                print(f"  send f{row.send_frame:<6} {row.kind:<7} "
                      f"{row.packet.hex()} -> applies f{row.apply_frame}")
        return plan_rows

    def wait_until(self, due_frame: int, *, timeout_s: float = 30.0) -> DevicePacket:
        import time
        deadline = time.monotonic() + timeout_s
        last = self.status()
        while last.frame < due_frame:
            if time.monotonic() > deadline:
                print(f"uart_host: REFUSED -- device frame {last.frame} did not "
                      f"reach {due_frame} within {timeout_s:.0f}s", file=sys.stderr)
                raise SystemExit(2)
            time.sleep(0.05)
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
    stays the device's."""
    shift = 0
    for _ in range(400):
        shifted = [c if c[0] != "event" else ("event", c[1] + shift, *c[2:])
                   for c in commands]
        try:
            return plan(shifted, baud=baud, start_frame=start_frame,
                        anchor_frame=anchor_frame)
        except ValueError as exc:
            if "acceptance" not in str(exc):
                raise
            shift += 256
    raise ValueError("could not plan within 400 shifts")


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
    ap.add_argument("--fixture", default=None, help="scripted phrase: bar808 (default fixture set)")
    ap.add_argument("--dry-run", action="store_true",
                    help="render the exact byte schedule and landing frames; no hardware")
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
    if a.cmd in ("load", "run") or a.cmd is None:
        for flag, sec, addr, data in voice_image_writes(a.preset):
            commands.append(("write", flag, sec, addr, data))
    if a.cmd == "note-on" or (a.cmd == "run" and a.note is not None):
        note = a.note if a.note is not None else 45
        for flag, sec, addr, data in note_writes(note, True):
            commands.append(("write", flag, sec, addr, data))
    if a.cmd == "play" or a.cmd == "run":
        events, _end = phrase_events(a.fixture or "bar808")
        for due, flag, sec, addr, data in events:
            commands.append(("event", due, flag, sec, addr, data))
    if a.cmd == "note-off" or (a.cmd == "run" and a.note is not None):
        note = a.note if a.note is not None else 45
        for flag, sec, addr, data in note_writes(note, False):
            commands.append(("write", flag, sec, addr, data))
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
        rows = plan_shifted(commands, baud=a.baud)
        b = budget(a.baud)
        print(f"uart_host: {len(rows)} packets, {sum(len(r.packet) for r in rows)} bytes "
              f"at {b['baud']} baud; device schedule (frames are device frames):")
        print(render_plan(rows))
        print(f"contract: {b['write_slots_per_frame']} write slots/frame, "
              f"event queue {b['event_queue']}, write queue {b['write_queue']}, "
              f"1 event packet per {1/b['frames_per_event_packet']:.1f} frames at this baud")
        return 0

    if not a.port:
        print("uart_host: REFUSED -- no --port given (or UART_BRIDGE_PORT); "
              "use --dry-run for the schedule without hardware", file=sys.stderr)
        return 2
    bridge = Bridge(a.port, a.baud)
    rows = bridge.run(commands, baud=a.baud)
    last = max(r.apply_frame for r in rows)
    end = bridge.wait_until(last + 64)
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

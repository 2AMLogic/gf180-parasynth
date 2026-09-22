#!/usr/bin/env python3
"""fpga/uart_device_sim.py -- the gateware contract, as a serial endpoint.

A python-pty loopback that speaks rtl-sketch/uart_bridge.v's side of the wire,
so the REAL host path (fpga/uart_host.py's main()/Bridge) can be exercised
end to end without hardware. This is the testing gap the RTL bench left: the
bench (tb_uart_bx.v) drove the pins, but nothing drove the CLI.

WHAT IT IMPLEMENTS (the device contract, mirrored from the Verilog):

  * BOOT 0xA5 once after the sim starts (a reset the host can SEE);
  * packets  opcode payload... checksum ; checksum = (-sum) mod 256. A bad
    checksum is rejected WHOLE and reported ERR 5 -- never half-applied;
  * W write-now: live queue (8 deep). Full -> ERR 1 + drop counter. Applied at
    accept_frame + 1 (never blocked by event traffic -- a note-off waits for
    nothing);
  * E scheduled: event queue (64 deep), FIFO, dues strictly increasing.
    Overflow -> ERR 2 + drop counter. Due at/before the accept frame -> ERR 3
    (late) -- and it still executes, loudly, like the RTL;
  * Q status: 8 bytes {0x55, frame16, evq, wrq, drops, errs, flags}; the frame
    is the device's frame REGISTER (audio frame + 1 during a frame), advancing
    in real time at 48 kHz, wrapping at 65536;
  * X abort: valid checksum clears both queues;
  * 4 byte-times of idle mid-packet -> resync ERR 4, parser back to opcode;
  * at most WRITE_SLOTS = 2 register writes presented per device frame.

APPARATUS KNOBS (preconditions a test can assert, not decorations):

  epoch_frame     initial frame counter value (wrap tests start near 65536);
  reply_delay_s   delay before ANY reply (a slow device);
  chunk_bytes/chunk_gap_s
                  replies written in partial chunks with gaps, so a host that
                  only reads "size" bytes with a finite timeout gets exactly
                  the stale/short reads the finding describes.

Every write the device executes is logged as
(frame, flag, sec, addr, data, src) with src in {"live", "event"} -- the
harness asserts gate timing and due exactness against this log, never against
what the host CLAIMED.
"""
from __future__ import annotations

import os
import pty
import select
import struct
import termios
import threading
import time

OP_WRITE = 0x57
OP_EVENT = 0x45
OP_STATUS = 0x51
OP_ABORT = 0x58
RSP_ACK = 0x06
RSP_ERR = 0x1C
RSP_STATUS = 0x55
BOOT = 0xA5

SR = 48_000
CYC_PER_FRAME = 256
WRITE_SLOTS = 2
DEFAULT_BAUD = 115_200
BITS_PER_BYTE = 10
ERR_WRQ_FULL = 1
ERR_EVQ_FULL = 2
ERR_DUE = 3
ERR_RESYNC = 4
ERR_CHECKSUM = 5
ERR_OPCODE = 6

BYTE_TIMES_GAP = 40          # RTL GAP_CYCLES = 40 * DIV


class UartDeviceSim:
    """Run on a thread behind a pty. `port` is what the host opens."""

    def __init__(self, *, epoch_frame: int = 0, evq_depth: int = 64,
                 wrq_depth: int = 8, reply_delay_s: float = 0.0,
                 chunk_bytes: int = 0, chunk_gap_s: float = 0.0,
                 baud: int = DEFAULT_BAUD):
        self.master, slave = pty.openpty()
        self.port = os.ttyname(slave)
        self._slave = slave                     # held so the name stays valid
        # The device owns its side of the line: raw, no echo. Without this a
        # BSD pty echoes the slave's input queue back to the master, and the
        # sim reads its own replies as if the host had sent them.
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG
                      | termios.IEXTEN | termios.ECHOE | termios.ECHOK
                      | termios.ECHONL)
        attrs[1] &= ~(termios.OPOST)
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        self.epoch = epoch_frame & 0xFFFF
        self.evq_depth = evq_depth
        self.wrq_depth = wrq_depth
        self.reply_delay_s = reply_delay_s
        self.chunk_bytes = chunk_bytes
        self.chunk_gap_s = chunk_gap_s
        self.baud = baud
        self._byte_time = BITS_PER_BYTE / baud
        self._wire_buf = bytearray()            # received but not yet deserialised
        self._wire_t = None                     # when the next byte completes
        self._t0 = 0.0
        self.buf = b""
        self._last_byte_t = None
        self._partial = bytearray()
        self.evq: list = []                     # [due, flag, sec, addr, data]
        self.last_due = None
        self.wrq: list = []                     # [accept_frame, flag, sec, addr, data]
        self.evq_count = 0
        self.wrq_count = 0
        self.drops = 0
        self.errs = 0
        self.flags_sticky = 0                   # {resync4, late2, wrq_ovf1, evq_ovf0}? RTL: bit3..0 {resync, late, wrq_ovf, evq_ovf}
        self.seq = 0
        self.booted = False
        # observation, the harness's ground truth
        self.writes: list = []                  # (frame, flag, sec, addr, data, src)
        self.received: list = []                # ("write"|"event"|"status"|"abort", detail)
        self.errors: list = []                  # (code, seq, info)
        self.status_requests = 0
        self._fires_this_frame = 0
        self._fire_frame = -1
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # ---- device time ---------------------------------------------------------
    def frame_now(self) -> int:
        return (self.epoch + int((time.monotonic() - self._t0) * SR)) & 0xFFFF

    def frames_elapsed(self) -> int:
        """Frames since start, UNwrapped: for assertions about ordering."""
        return int((time.monotonic() - self._t0) * SR)

    def frame_at_elapsed(self, elapsed: int) -> int:
        return (self.epoch + elapsed) & 0xFFFF

    # ---- wire ----------------------------------------------------------------
    def _send(self, data: bytes, *, delay: bool = True) -> None:
        if delay and self.reply_delay_s:
            time.sleep(self.reply_delay_s)
        if self.chunk_bytes:
            for i in range(0, len(data), self.chunk_bytes):
                os.write(self.master, data[i:i + self.chunk_bytes])
                if self.chunk_gap_s and i + self.chunk_bytes < len(data):
                    time.sleep(self.chunk_gap_s)
        else:
            os.write(self.master, data)

    @staticmethod
    def _cksum(body: bytes) -> int:
        return (-sum(body)) & 0xFF

    def _ack(self) -> None:
        self.seq = (self.seq + 1) & 0xFF
        self._send(bytes([RSP_ACK, self.seq]))

    def _err(self, code: int, info: int = 0) -> None:
        self.errs += 1
        self.errors.append((code, self.seq, info))
        self._send(bytes([RSP_ERR, code, self.seq, info & 0xFF]))

    def _status(self) -> None:
        self.status_requests += 1
        if self.reply_delay_s:
            # the latency is before the device COMPOSES its answer: a real
            # device samples the frame register when it builds the reply, so
            # the reply's frame is fresh at send time -- what the delay models
            # is link/host latency, which the host must absorb with lead.
            time.sleep(self.reply_delay_s)
        f = (self.frame_now() + 1) & 0xFFFF     # the frame REGISTER: audio+1
        flags = self.flags_sticky
        self.flags_sticky = 0                    # sticky until the STATUS that reads them
        self._send(bytes([RSP_STATUS, (f >> 8) & 0xFF, f & 0xFF,
                          self.evq_count, self.wrq_count,
                          self.drops & 0xFF, self.errs & 0xFF, flags]),
                   delay=False)

    # ---- parser (mirrors the RTL's per-byte state machine) -------------------
    def _rx_byte(self, b: int, t: float) -> None:
        # a 40-byte-time gap while a packet is INCOMPLETE is a resync, like
        # the RTL's GAP_CYCLES. A gap between packets is idle line, not an
        # error -- that is the normal shape of a host that plans its sends.
        if (self._partial and self._last_byte_t is not None
                and t - self._last_byte_t > BYTE_TIMES_GAP * self._byte_time):
            self._partial.clear()
            self.flags_sticky |= 0x8        # resync
            self._err(ERR_RESYNC, 0)
        self._last_byte_t = t
        self._partial.append(b)
        while self._partial:
            op = self._partial[0]
            need = {OP_WRITE: 8, OP_EVENT: 10, OP_STATUS: 2, OP_ABORT: 2}.get(op)
            if need is None:
                del self._partial[0]
                self._err(ERR_OPCODE, op)
                continue
            if len(self._partial) < need:
                return                      # incomplete: more bytes at baud
            pkt = bytes(self._partial[:need])
            del self._partial[:need]
            if (sum(pkt[:-1]) + pkt[-1]) & 0xFF:
                self._err(ERR_CHECKSUM, pkt[3] if len(pkt) > 3 else 0)
                continue
            self._accept(op, pkt)

    @staticmethod
    def _reg(pkt: bytes, off: int):
        """Parse the six SPI-framing bytes at `off`: {F, 6'b0, SEC, A[7:0],
        D[31:0]} -- flag and sec share the first byte."""
        b0 = pkt[off]
        flag = (b0 >> 7) & 1
        sec = (b0 >> 6) & 1
        addr = pkt[off + 1]
        data = int.from_bytes(pkt[off + 2:off + 6], "big")
        return flag, sec, addr, data

    def _accept(self, op: int, pkt: bytes) -> None:
        # the acceptance instant is the packet's last byte on the device's
        # own timeline (the cursor), not post-stall wall time
        f = self._frame_at(self._cursor)
        if op == OP_STATUS:
            self.received.append(("status", f))
            self._status()
        elif op == OP_ABORT:
            self.received.append(("abort", f))
            self.evq.clear()
            self.wrq.clear()
            self.evq_count = self.wrq_count = 0
            self._ack()
        elif op == OP_WRITE:
            flag, sec, addr, data = self._reg(pkt, 1)
            self.received.append(("write", (f, addr, data)))
            if self.wrq_count >= self.wrq_depth:
                self.drops += 1
                self.flags_sticky |= 0x2
                self._err(ERR_WRQ_FULL, addr)
            else:
                self.wrq.append([f, flag, sec, addr, data])
                self.wrq_count += 1
                self._ack()
        elif op == OP_EVENT:
            due = pkt[1] | (pkt[2] << 8)
            flag, sec, addr, data = self._reg(pkt, 3)
            self.received.append(("event", (f, due, addr, data)))
            ddiff = (due - f) & 0xFFFF
            due_past = ddiff == 0 or ddiff > 0x7FFF
            out_of_order = (self.evq_count != 0
                            and ((due - self.last_due) & 0xFFFF) >= 0x8000)
            if due_past:
                self.flags_sticky |= 0x4         # late
                if self.evq_count >= self.evq_depth:
                    self.drops += 1
                    self.flags_sticky |= 0x1
                    self._err(ERR_EVQ_FULL, addr)
                else:
                    # the RTL executes a late event next window, loudly
                    self.evq.append([f, flag, sec, addr, data])
                    self.last_due = f
                    self.evq_count += 1
                    self._err(ERR_DUE, addr)
            elif out_of_order:
                self._err(ERR_DUE, addr)
            elif self.evq_count >= self.evq_depth:
                self.drops += 1
                self.flags_sticky |= 0x1
                self._err(ERR_EVQ_FULL, addr)
            else:
                self.evq.append([due, flag, sec, addr, data])
                self.last_due = due
                self.evq_count += 1
                self._ack()

    # ---- execution -----------------------------------------------------------
    def _rel_frames(self, frame: int) -> int:
        """A device frame as frames-since-start (unwrapped)."""
        return (frame - self.epoch) & 0xFFFF

    def _frame_at(self, t: float) -> int:
        return (self.epoch + int((t - self._t0) * SR)) & 0xFFFF

    def _mono_of_frame(self, frame: int, *, mid: bool = True) -> float:
        """The wall-clock instant a device frame occurs. A half-frame offset
        lands the wakeup INSIDE the frame, so the execution cursor processes
        the write IN its due frame."""
        rel = self._rel_frames(frame)
        t = self._t0 + (rel + (0.5 if mid else 0.0)) / SR
        now = self._cursor
        while t < now:                      # wrapped past: next occurrence
            t += 65536 / SR
        return t

    def _next_fire_time(self) -> float | None:
        """Earliest pending execution instant: due-scheduled before live."""
        t = None
        if self.evq_count:
            t = self._mono_of_frame(self.evq[0][0])
        if self.wrq_count:
            w = self._mono_of_frame(self.wrq[0][0] + 1)
            t = w if t is None else min(t, w)
        return t

    def _fire_one(self) -> bool:
        """Execute at most one ready write. The write EXECUTES in its
        scheduled device frame -- the due frame for events, accept+1 for live
        -- which is what the contract and the RTL deliver. Wall-clock jitter
        around that instant is apparatus noise below the device's frame
        resolution and must not smear into the recorded schedule."""
        f = self._frame_at(self._cursor)
        ev_ready = bool(self.evq_count) and (((f - self.evq[0][0]) & 0xFFFF) < 0x8000)
        wr_ready = (bool(self.wrq_count) and not ev_ready
                    and (((f - self.wrq[0][0] - 1) & 0xFFFF) < 0x8000))
        if ev_ready:
            logged = self.evq[0][0]
        elif wr_ready:
            logged = self.wrq[0][0] + 1
        else:
            return False
        if logged != self._fire_frame:      # the 2-slots-per-frame cap
            self._fire_frame = logged
            self._fires_this_frame = 0
        if self._fires_this_frame >= WRITE_SLOTS:
            return False
        if ev_ready:
            due, flag, sec, addr, data = self.evq.pop(0)
            self.evq_count -= 1
            self.writes.append((logged, flag, sec, addr, data, "event"))
        else:
            stamp, flag, sec, addr, data = self.wrq.pop(0)
            self.wrq_count -= 1
            self.writes.append((logged, flag, sec, addr, data, "live"))
        self._fires_this_frame += 1
        return True

    def _advance(self, now: float) -> None:
        """Process the device timeline in CHRONOLOGICAL order up to `now`:
        wire bytes complete at their baud spacing, writes fire at their
        scheduled frames, and a host/GIL stall is absorbed as catch-up rather
        than corrupting acceptance stamps and dues with post-stall time."""
        while self._cursor < now:
            fire_t = self._next_fire_time()
            byte_t = self._wire_next_t if self._wire_buf else None
            t = min(fire_t or now, byte_t or now, now)
            if t > now:
                t = now
            self._cursor = t
            did = False
            if fire_t is not None and fire_t <= t:
                did = self._fire_one() or did
            if byte_t is not None and byte_t <= t:
                b = self._wire_buf.pop(0)
                if self._wire_buf:
                    self._wire_next_t = t + self._byte_time
                else:
                    self._wire_next_t = None
                self._rx_byte(b, t)
                did = True
            if not did:
                self._cursor = now
                break

    def _loop(self) -> None:
        self._t0 = time.monotonic()
        self._cursor = self._t0
        self._last_byte_t = self._t0
        self._send(bytes([BOOT]))
        while not self._stop.is_set():
            now = time.monotonic()
            self._advance(now)
            next_t = self._next_fire_time()
            if self._wire_buf and self._wire_next_t is not None:
                next_t = (self._wire_next_t if next_t is None
                          else min(next_t, self._wire_next_t))
            timeout = 0.05 if next_t is None else min(0.05, max(0.0, next_t - now))
            r, _, _ = select.select([self.master], [], [], timeout)
            if r:
                try:
                    chunk = os.read(self.master, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    rt = time.monotonic()
                    first = not self._wire_buf
                    self._wire_buf += chunk
                    if first:
                        # the first buffered byte completes one byte-time out
                        self._wire_next_t = max(rt, self._cursor) + self._byte_time
        os.close(self.master)
        os.close(self._slave)

    def start(self) -> "UartDeviceSim":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

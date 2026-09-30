#!/usr/bin/env python3
"""fpga/late_event_policy.py -- the device's LATE-EVENT POLICY, stated before measurement (#329).

Written and committed BEFORE any RTL or model result for #329 was looked at.
It is derived from the device contract as the musician needs it, not from
what rtl-sketch/uart_bridge.v or fpga/uart_device_sim.py happen to do. The
two implementations are then held to it (fpga/verify_late_events.py). Where
one disagrees, the policy is not edited to match. The layer that violates it
is repaired, or the violation is recorded for the next image.

TERMS. Frames are 16-bit AUDIO frames, the numbering dues are written in (the
contract's "dues are in audio frames"). A packet is ACCEPTED in audio frame A,
the frame in which the device finishes receiving its checksum byte. For an
event packet with due D:

    d = (D - A) mod 65536
    ON TIME  when 1 <= d <= 32767   (the contract's +-32768 wrap window)
    LATE     when d == 0 or d >= 32768   (due now, already gone, or too far
                                          ahead to tell apart from the past)

THE POLICY. The device has one FIFO event queue of depth Q (64). It executes
at most two queued writes per frame, in FIFO order. An event runs in the
first frame F with F >= its effective due and a free slot, and no earlier
than everything queued ahead of it.

  P1 on time. An ON-TIME event that is in order (below) and fits in the queue
     is acknowledged. Its effective due is D, so with nothing ahead of it it
     executes in EXACTLY frame D.
  P2 late. A LATE event that is in order and fits is ACCEPTED and reported
     with ERR 3 (never silently), with the late flag set. Its effective due is
     A + 1, the next frame: with nothing ahead of it, it executes in frame
     A + 1. It is never discarded for being late, and it never waits for the
     frame counter to come round to it.
  P3 order (AMENDED by the operator's decision on #339). An event is IN
     ORDER when the queue is empty, or when its due AS SENT is not before the
     effective due of the last event queued (wrap-safe, within the +-32768
     window). An event that is NOT in order is DROPPED and reported with
     ERR 3; the queue is unchanged. This is the numeric contract
     ("an event whose due is out of order with the queue's last due is
     dropped and reported") and what uart_bridge.v does. It covers a late
     event too: a late event whose sent due is before a draining queue's last
     due is dropped, counted as an error and reported -- not re-dated behind
     the tail.

     As first stated (committed at cd318c7, before any result), P3 compared
     EFFECTIVE dues, so such a late event would have been accepted and
     executed after the tail. The bench found the RTL disagreeing in exactly
     that one case (#339); the operator chose the contract and the RTL, and
     no RTL change. The original wording is kept here so the amendment is
     visible, not silent.
  P4 overflow. An event that arrives while Q events are queued is DROPPED
     and reported with ERR 2. The drop counter increments and the overflow
     flag is set until the next STATUS reply. The queue's contents are
     unchanged.
  P5 recovery. Once the queue has room again, the next in-order event is
     handled by P1/P2 as if nothing had happened. Nothing in the
     device's state is left wedged by a late event or an overflow.
  P6 no stall. No accepted event executes later than
         max(effective due, frame of the previous queued write's execution)
     plus what the two-per-frame cap forces. In particular, a queued event
     whose effective due is already past executes in the next frame with a
     free slot. It never waits anything like a counter revolution
     (65536 frames, 1.365 s).
  P7 boundaries. Classification uses the frame of acceptance, whatever the
     cycle within it: an event due A + 1 accepted in the LAST cycle of frame A
     is on time (P1, executes A + 1); the same packet accepted in the FIRST
     cycle of frame A + 1 is late (d == 0; P2, executes A + 2). All of the
     above holds unchanged across the wrap 65535 -> 0.

Everything below is the policy as code: an independent reference that turns
an ordered list of (acceptance frame, due) into the expected outcome of each
event. It knows nothing about cycles, the UART or either implementation.
"""
from __future__ import annotations

from dataclasses import dataclass

WRAP = 65536
HALF = 32768
SLOTS_PER_FRAME = 2
QUEUE_DEPTH = 64


def fwd(a: int, b: int) -> int:
    """(b - a) mod 2^16: how far b is ahead of a."""
    return (b - a) % WRAP


def is_late(accept: int, due: int) -> bool:
    d = fwd(accept, due)
    return d == 0 or d >= HALF


@dataclass
class Outcome:
    index: int
    verdict: str                    # "ack" | "late" | "drop-order" | "drop-full"
    error: int | None               # ERR code reported (None for a plain ACK)
    effective_due: int | None       # absolute (unwrapped) frame
    executes: int | None = None     # absolute (unwrapped) frame, when accepted


def expected(events: list, *, depth: int = QUEUE_DEPTH) -> list:
    """events: [(accept_abs, due16)] in acceptance order, accept_abs an
    UNWRAPPED absolute audio frame (so wraps are arithmetic, not guesses);
    due16 as sent on the wire. Returns one Outcome per event.

    The queue is simulated at frame granularity. Every event accepted in
    frame A is decided against the queue as it stands after all executions
    of frames <= A that precede the acceptance. Acceptances in the same
    frame as an execution are decided AFTER that frame's executions only if
    the execution slots precede them in the frame. This reference places
    acceptances after the frame's execution slots, so tests must not accept
    a packet in the few cycles before a frame's slots unless they state
    which side it is on (fpga/verify_late_events.py keeps acceptances
    clear of the slots, or names the side explicitly)."""
    out: list = []
    queue: list = []                 # [(effective_due_abs, index)]
    last_exec_frame, used_in_last = None, 0

    def run_until(frame: int) -> None:
        """Execute everything the policy executes in frames <= frame."""
        nonlocal last_exec_frame, used_in_last
        while queue:
            due, idx = queue[0]
            f = due if last_exec_frame is None else max(due, last_exec_frame)
            if f == last_exec_frame and used_in_last >= SLOTS_PER_FRAME:
                f += 1
            if f > frame:
                return
            queue.pop(0)
            if f == last_exec_frame:
                used_in_last += 1
            else:
                last_exec_frame, used_in_last = f, 1
            out[idx].executes = f

    for i, (accept, due16) in enumerate(events):
        run_until(accept)
        late = is_late(accept % WRAP, due16)
        eff = accept + 1 if late else accept + fwd(accept % WRAP, due16)
        # P3 (amended, #339): the SENT due against the last queued effective due
        if queue and fwd(queue[-1][0] % WRAP, due16) >= HALF:
            out.append(Outcome(i, "drop-order", 3, None))
            continue
        if len(queue) >= depth:
            out.append(Outcome(i, "drop-full", 2, None))
            continue
        out.append(Outcome(i, "late" if late else "ack", 3 if late else None, eff))
        queue.append((eff, i))
    run_until(10 ** 12)
    return out

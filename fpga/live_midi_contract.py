#!/usr/bin/env python3
"""fpga/live_midi_contract.py -- the T-LIVE-MIDI contract, FROZEN before measurement.

Issue #281 (Milestone D of epic #282). Two implementations read this file and
nothing else they share decides behaviour:

  * fpga/midi_session.py      the live host session (the thing under test);
  * fpga/verify_live_midi.py  the independently built expected schedule
                              (the oracle) and the checks.

What lives here is the CONTRACT'S PARAMETERS -- the numbers a reviewer has to
agree to before any result is read -- and the latency TARGET with its
endpoints and declared load. Behaviour (MIDI interpretation, note priority,
the drum and CC maps, placement, admission) is implemented twice, once on
each side, so that a defect in one is visible against the other.

CRITERION_VERSION changes whenever any number below changes. A change to the
target, the endpoints or the declared load is a criterion change, never a
"sound improvement" (docs/trials.md rule 3).
"""
from __future__ import annotations

SR = 48_000

# ---- timing contract (the host's scheduling rule) ----------------------------
# Every accepted MIDI event is scheduled at a FIXED lookahead from its receipt:
# its first write's nominal frame is receipt_frame + LOOKAHEAD_FRAMES. A fixed
# lookahead makes latency constant (no jitter) and lets the device's event
# queue land every write in EXACTLY its due frame. 15 ms is sized from the
# wire, not chosen for the target: an event packet is 10 bytes at 115200 baud,
# 41.7 frames, and the heaviest musically ordinary cluster -- a note-on (5
# writes) with a kick (4 head writes) and a hat (2) -- is 11 packets, 458
# frames of wire, which must be accepted before its dues.
LOOKAHEAD_MS = 15.0
LOOKAHEAD_FRAMES = int(round(LOOKAHEAD_MS * 1e-3 * SR))          # 720

WRITE_SLOTS = 2                  # device register writes per frame (uart_host)
DEADLINE_MARGIN_FRAMES = 12      # accept <= due - 1 - margin: MIN_LEAD, one
                                 # STATUS poll (8.3 frames) and bench phase
ADMISSION_RESERVE_PACKETS = 4    # headroom a note-on / drum hit must leave for
                                 # the unconditional traffic (note-offs, knob
                                 # updates, panic) that may follow it
KNOB_INTERVAL_FRAMES = 480       # one update per controller per 10 ms at most:
                                 # a 100 Hz control rate; superseded values are
                                 # redundant writes and are not sent
SIMULTANEOUS_FRAMES = 48         # 1 ms: two hits on one stop, or on the two
                                 # halves of an exclusive pair, this close are
                                 # one strike -- the second is REFUSED
MAX_PUSH_FRAMES = 2400           # an unconditional event the wire cannot carry
                                 # at its nominal frame is moved later, at most
                                 # this far (50 ms), and the move is reported
HOST_QUEUE_MAX_PACKETS = 256     # host-side pending packets; a note-on or hit
                                 # beyond it is REFUSED (queue pressure)
DEVICE_QUEUE_BOUND = 60          # the device's 64-deep event queue, less margin
STATUS_POLL_S = 0.5              # device frame counter re-read this often
RESYNC_TOLERANCE_FRAMES = 2      # |host map - device| beyond this re-anchors
ACTIVE_SENSING_TIMEOUT_S = 0.300 # MIDI 1.0: 300 ms without a byte after an
                                 # Active Sensing message is a lost connection

# ---- channels (MIDI channel numbers are 1-based in prose, 0-based on the wire)
VOICE_CHANNEL = 0                # MIDI channel 1: the mono voice and its CCs
DRUM_CHANNEL = 9                 # MIDI channel 10: the General MIDI drum channel

# ---- the latency target, frozen before measurement ---------------------------
CRITERION_VERSION = "live-midi/1"
LATENCY_TARGET = {
    "p95_ms": 20.0,
    "p99_ms": 30.0,
    "endpoints": {
        "start": "host MIDI receipt: the instant the session's MIDI input hands "
                 "the message over (the stream timestamp in simulation), read as "
                 "the DEVICE frame containing that instant",
        "end": "the device frame in which the event's anchor write is applied: "
               "GATE_ON / GATE_OFF, the drum STOPS rising edge, the last pitch "
               "write of a legato change, the last register of a knob update",
    },
    "population": "every MIDI message whose own value is written by an update "
                  "the session scheduled. A knob value superseded before it was "
                  "sent has no update of its own; its staleness (receipt to the "
                  "first update carrying a newer value) is reported separately "
                  "and is NOT part of the target's population",
    "components": {
        "host_hold": "receipt -> the anchor packet's first byte written to the link",
        "uart": "first byte written -> the device accepts the packet",
        "device_queue": "acceptance -> the due frame (the write applies)",
        "excluded": "synthesis (the write applies at the start of its frame; the "
                    "sample computed in that frame is the first affected, proved "
                    "by the bit-exact I2S comparison) and the patch's intentional "
                    "attack are reported, not counted",
    },
    "declared_load": "the `sustained` workload of fpga/verify_live_midi.py, seed "
                     "SUSTAINED_SEED, SUSTAINED_S seconds: one mono line of 8 "
                     "note-ons/s with 40-140 ms gates (overlaps included), 808 "
                     "drums on a 16th grid at 120 bpm (<= 3 stops a step, a tom "
                     "fill every 2 bars), two knobs twisted at up to 100 CC/s",
    "if_it_fails": "investigate lookahead, redundant writes and batching first; "
                   "never relax the target",
}
SUSTAINED_SEED = 281
SUSTAINED_S = 20.0

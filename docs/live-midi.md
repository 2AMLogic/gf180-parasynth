# Live MIDI session (T-LIVE-MIDI, issue #281)

Milestone D of epic #282, **simulator half**. A keyboard's MIDI stream drives
the instrument over the existing USB-UART control link. There is no GUI, no
on-chip USB, no sequencer and no new protocol. Every write is an ordinary
scheduled event packet (`fpga/uart_host.py`), applied by the device in its
due frame.

| file | role |
|---|---|
| `fpga/midi_session.py` | the session: parser, note logic, maps, refusals, scheduling, send loop, live CLI |
| `fpga/live_midi_contract.py` | the frozen parameters and the latency target (committed before any measurement) |
| `fpga/verify_live_midi.py` | the independently built expected schedule, eleven properties, three controls, RTL replay |
| `fpga/sweep_live_midi.py` | the lookahead / reserve / redundant-write sweep that chose the lookahead |
| `fpga/test_midi_session.py` | unit tests |
| `fpga/reports/live-midi/` | evidence: `verification.json`, `start-red.log`, `sweep.json`, RTL captures and receipts |

## Start command

The one documented start command, **run on the build box** against the
simulated board (the device contract behind a pty, in real time), with a
scripted keyboard played into a pipe that the standard-library MIDI input
reads:

```
.venv/bin/python fpga/midi_session.py --port sim --midi-in scripted:coverage
```

The hardware form is the same command with a serial port and a Linux raw MIDI
device, for example `--port /dev/ttyUSB1 --midi-in /dev/snd/midiC1D0`. **It has
not been exercised on a board.** Hardware MIDI-to-audio latency is a later
capture measurement with its own endpoints (plan087 section 8). `--midi-in -`
reads raw MIDI bytes from stdin. The raw input uses only the standard library
(`os`, `select`); no MIDI package is required.

## Conventional defaults (chosen by the owner, per plan087 section 8)

**Channels.** The mono voice and its controllers are on MIDI channel 1. The
808 drums are on channel 10, the General MIDI drum channel. Any other channel
is refused.

**Voice.** Last-note priority with held-note tracking and single trigger: a
new key while another is held changes pitch and does not restart the
envelope. Glide is as the patch sets it. Releasing the sounding key returns
to the most recently pressed key that is still held. Releasing a key that is
not sounding changes nothing. A repeated note-on of the sounding key is a
no-op. Note-on with velocity 0 is note-off. The voice has no velocity input,
so velocity does not change it; that is part of this contract, not an ignored
control. This is `model/voice_fx.KeyHost`'s default policy (contract 5.6),
stepped one event at a time, and the unit tests hold it equal to KeyHost.

**Drum map** (GM note → 808 stop). Velocity sets the accent over 0.6 .. 1.4.

| GM | stop | GM | stop | GM | stop |
|---|---|---|---|---|---|
| 35, 36 | BD | 41, 43 | LT | 42, 44 | CH |
| 38, 40 | SD | 45, 47 | MT | 46 | OH |
| 39 | CP | 48, 50 | HT | 49, 57 | CY |
| 56 | CB | 75 | CL | | |

The map respects exclusive pairs. Closed and open hat are one instrument: the
kit's CH chokes OH. Two hat strikes within 1 ms are one strike, and the
second is **refused**, as is a second strike of the same stop within 1 ms
(it could not re-strike). The loaded image selects the tom, claves and clap
positions of their shared circuits, so the congas, rim shot and maracas are
unmapped and **refused**. Drum note-offs are accepted and do nothing, because
the drum voices are one-shot.

**Controllers** (voice channel):

| CC | control | mapping |
|---|---|---|
| 74 | cutoff (`cut_lo`) | 40 Hz · 200^(v/127): 40 Hz .. 8 kHz |
| 71 | resonance | q = v/127 |
| 7 | voice volume | 0.9 · v/127 (0.45 is the reference level) |
| 1 | modulation wheel | v/127 of full scale, **only if the patch routes modulation**. The default patch does not, so CC1 is refused there |
| 120, 123 | All Sound Off / All Notes Off | **panic** (either channel) |

**Refused explicitly.** Nothing is silently ignored: each refusal is recorded
with its reason, and the CLI prints the first of each kind plus counts at exit.

- sustain (CC64);
- every other CC;
- pitch bend, program change, channel and poly aftertouch;
- system exclusive, system common, and real-time messages other than Active
  Sensing (MIDI clock and transport: the session has no sequencer);
- stray data bytes;
- notes outside the release domain;
- note-ons and drum hits the link cannot deliver on time (`queue-pressure`).

The domain rule is #255's: every oscillator's increment, after detune, must
lie in `[phase_inc(MIDI 0), phase_inc(MIDI 127)]`. On the default patch that
admits MIDI 12 .. 126.

**Panic and cleanup.** CC120 and CC123 send GATE_OFF and clear the stops word,
and clear the held-note list. Closing the session always panics: at end of
input, on Ctrl-C, on a lost connection. Active Sensing is honoured: once it
has been seen, 300 ms without a byte counts as a lost connection and panics.
A device reset is seen on the wire (BOOT) and reported.

**Known state, once per session.** At start the session sends the whole image
as live writes: patch, mixer weights, the modulation registers, kit, accents,
gate off, stops clear. That is 190 writes. It waits for every ACK and then
anchors its time map with one STATUS. Nothing is reset per note.

## Timing contract (`fpga/live_midi_contract.py`, timing contract 2)

- **Fixed lookahead.** A message received at host time *t*, which the host's
  map puts in device frame *r*, has its first write's nominal frame at
  *r* + 768 (16 ms). Each event's writes follow in order, two per frame. Timed
  coefficient sequences (the BD attack window, the tom pitch drop) are placed
  at their offsets from the event's anchor.
- **Send exactly one lookahead early.** A packet leaves the host at its due
  minus the lookahead (or at receipt, if later). The device's single FIFO event
  queue therefore sees non-decreasing dues, and every write lands in exactly
  its due frame. Latency is constant rather than jittered.
- **Admission.** A note-on or drum hit is admitted only if a projection of the
  wire has every packet accepted `DEADLINE_MARGIN_FRAMES` before its due, with
  room for 4 more packets. Otherwise it is **refused** (`queue-pressure`).
  Note-offs, knob updates and panic are never refused. If needed they are
  moved later (at most 50 ms) and the move is reported.
- **Knob rate.** At most one update per controller per 10 ms. A newer value
  replaces an unsent one in place. Superseded values are redundant writes and
  are not sent.
- **Queues.** At most 256 host packets are pending; beyond that, note-ons and
  hits are refused. The device event queue is bounded at 60 of its 64 slots
  and is checked. On the declared load the measured peaks are 17 host packets
  and 18 device queue slots.

### Why 16 ms (a measured choice)

Timing contract 1 used 15 ms. The frozen declared load then **refused five
crash hits** on downbeats in 20 s: note, kick, hat and crash together are 13
head packets, and with the reserve they needed 721 frames against 720. #281
says to investigate lookahead, redundant writes and batching first, and never
to relax the target. `fpga/sweep_live_midi.py` measured all three:

- **Lookahead.** Every row passes at 16 ms and above, for every reserve (2..4).
- **Redundant writes** cannot help. 549 of 2648 writes re-write a held value,
  but 545 of those are knob registers (resonance's gain word never changes
  with q), and none are in the refused clusters.
- **Batching** the cluster's strikes into one STOPS write would need a
  deliberate hold, which is latency by another name.

The target, its endpoints and the declared load are unchanged.

## Latency: target, endpoints, result

Frozen before measurement (`LATENCY_TARGET`, criterion `live-midi/1`):
**p95 ≤ 20 ms and p99 ≤ 30 ms**, measured under the declared load.

- **Start:** host MIDI receipt, read as the device frame that contains that
  instant, on the device's own timeline.
- **End:** the device frame in which the event's anchor write executes: GATE
  on/off, the STOPS rising edge, the last pitch write of a legato change, or
  the last register of a knob update.
- **Population:** every message whose own value is written. A superseded knob
  value is reported separately, as staleness.

Measured (simulated device, `sustained`, 20 s, seed 281, n = 905):

| | min | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| receipt → applied | 16.00 | 16.02 | **16.73** | **16.98** | 17.04 ms |

Histogram: 896 events in 16–17 ms, 9 in 17–18 ms. The mean components are
host hold 0.09 ms, UART 2.79 ms and device queue 13.21 ms. Nothing was refused
and nothing was pushed.

Two costs sit outside the endpoint. **Synthesis:** a write applies at the
start of its frame and that frame's sample is the first one affected; the
bit-exact I2S comparison proves this. **Patch attack:** the default amp attack
is 5 ms.

Outside the declared load:

- **coverage:** p95 17.0 ms, max 26.0 ms. The maximum is a rate-limited knob
  update: at most one per controller per 10 ms.
- **pressure:** max 17.6 ms for what was admitted. 193 note-ons and hits were
  refused. Superseded knob values reached the device within p95 18.0 ms.

## Verification

```
.venv/bin/python fpga/verify_live_midi.py                  # seconds: sim + controls
.venv/bin/python fpga/verify_live_midi.py --rtl coverage pressure sustained \
      --rtl-inject WRONG_DRUM_MAP DELAYED_EVENT             # the UART RTL + I2S (box)
.venv/bin/python fpga/verify_live_midi.py --start-red      # against the stub
```

The **expected schedule** comes from `verify_live_midi.Oracle`, built from the
scenario's structured events. It never uses the bytes the session parsed or
anything the session computed. It has its own drum map, CC formulas,
knob-rate rule, placement and wire admission, and runs the model's KeyHost in
batch. It shares with the session only the frozen parameters and the register
encoders already proved bit-exact at the pins (`spi_host.MusicHost`,
`voice_fx.KeyHost`). The one number the session chooses, its time map, is
checked against the device's own timeline first. A map more than one frame
off is NO VERDICT, never a result.

Scenarios:

- **coverage** covers:
  - simultaneous voice and drums;
  - overlaps (legato, then release of the covered key);
  - fast note-off (1 ms);
  - repeated notes, velocity-0 note-off, and a repeat while held;
  - last-note return;
  - a cutoff burst at 1 kHz and a resonance burst at 500 Hz;
  - the hat pair and the toms;
  - every refusal;
  - panic by CC123 and by CC120.

  It also runs from device counter 65000, across the wrap.
- **pressure** offers a tom roll at 500 hits/s, a trill at 200 notes/s and
  three knobs at 1 kHz each, then a panic.
- **sustained** is the declared load.

| control | property that must move | reason |
|---|---|---|
| DROP_NOTE_OFF | voice_gate, stuck_notes | the lost note-off leaves the gate open past its release |
| WRONG_DRUM_MAP | drum_strikes | GM 38 strikes the clap, not the snare |
| DELAYED_EVENT | timing | one event's writes land 5 ms after their frame |

Each control prints the full MOVED/BLIND matrix (rule 4).

- **Latency** stays BLIND for all three. One 5 ms delay does not breach the
  target, and the control's record shows the moved maximum instead.
- **RTL replay.** `--rtl` replays the session's transmitted bytes through the
  Arty UART wrapper (`fpga/verify_uart_bridge.py`) and compares the decoded
  I2S with the model driven by the oracle's schedule.
- **RTL controls.** WRONG_DRUM_MAP and DELAYED_EVENT are replayed too, and their
  I2S must mismatch. DROP_NOTE_OFF changes the packet count, so its bytes cannot
  be paired write-for-write with the schedule; it is caught at the device
  contract.

**Start red.** Against `fpga/stubs/midi_session_stub.py` every scenario FAILS
by named property (static image 0/190, gates 0/19, strikes 0/14, refusals
0/14, ...; `fpga/reports/live-midi/start-red.log`). A first stub with a naive
time map 34 frames off was refused as NO VERDICT by the precondition before
any property was read.

## Wrong-then-right record

Six results were wrong before they were right. Five were caught by a control,
a precondition or a test. The sixth was caught by re-measuring a number
before publishing it:

1. **Naive time map.** The first stub's time map ignored the STATUS
   request's two byte-times and the reply's eight, and was 34 frames off. The
   precondition refused it.
2. **Counter-wrap numbering.** The counter-wrap run compared the session's
   16-bit-based numbering with an unwrapped truth, reading 65536 frames "off"
   (NO VERDICT). This was a harness error.
3. **Refusal passed as agreement.** A declared-load refusal matched the oracle
   and so PASSED. A supported load must be admitted whole, so the check
   `load_admitted` was added, and the valid FAIL led to the sweep.
4. **Latency MOVED when not computable.** Latency read as MOVED on every
   control when it had merely not been computed. It now pairs by value
   alignment.
5. **Empty distribution crash.** An empty latency distribution crashed the
   harness's formatting, so `--start-red` would have crashed instead of
   failing red. The unit test on the stub caught it.
6. **Device queue peak.** This document first stated a device queue peak of
   17 (copied from the host peak). Measured, it is 18.

## Known limits

- **USB latency is not modelled.** The FTDI USB path adds delay that the
  simulated wire does not have, and the 16 ms lookahead's margin has to absorb
  it. That is the first thing the hardware capture must measure.
- **Note-off avalanche.** Releasing many held keys in reverse order within a
  millisecond generates a pitch return for each release. These are
  unconditional, so they are delivered late (up to 50 ms) rather than refused,
  and each move is counted. The declared load never does this.
- **The mod wheel is untested on the release image.** CC1 is refused on the
  default patch; a routed patch is exercised only by a unit test.

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
| `fpga/verify_live_midi.py` | the independently built expected schedule, twelve properties, three controls, RTL replay |
| `fpga/sweep_live_midi.py` | the lookahead / reserve / redundant-write sweep that chose the lookahead |
| `fpga/test_midi_session.py` | unit tests |
| `fpga/coremidi_input.py` | the macOS MIDI input (CoreMIDI through ctypes, #322) |
| `fpga/test_coremidi_input.py` | its tests: hardware-free, plus two Mac-only tests through a virtual source |
| `fpga/measure_mac_midi_latency.py` | Mac host scheduling latency on the contract's endpoints (#322) |
| `fpga/reports/live-midi/` | evidence: `verification.json`, `start-red.log`, `sweep.json`, RTL captures and receipts |

## Start command

The one documented start command, **run on the build box** against the
simulated board (the device contract behind a pty, in real time), with a
scripted keyboard played into a pipe that the standard-library MIDI input
reads:

```
.venv/bin/python fpga/midi_session.py --port sim --midi-in scripted:coverage
```

The run exits 0 (`fpga/reports/live-midi/start-command.log`, regenerated on
the R1 candidate (revision 14, with the known-state preamble) for #279): 193 init writes acknowledged (kit for image
tree, contract revision 14), 203 scheduled writes executed, 14 refusals printed
by name, device errors 0, drops 0, queue peak 17 (the simulated board runs in real time behind a pty, so the peak moves by one between runs: 18 on the previous run). Its first run (before #273,
revision 11's 190-write known state) REFUSED at start: 48 of 190 ACKs were
counted. The fault was in `uart_host.Bridge._take`, which dropped
every ACK after the first in a chunk; it is fixed, with a regression test.

The hardware form is the same command with a serial port and a Linux raw MIDI
device, for example `--port /dev/ttyUSB1 --midi-in /dev/snd/midiC1D0`. **It has
not been exercised on a board.** Hardware MIDI-to-audio latency is a later
capture measurement with its own endpoints (plan087 section 8). `--midi-in -`
reads raw MIDI bytes from stdin. The raw input uses only the standard library
(`os`, `select`), and no MIDI package is required. On macOS, use
`--midi-in coremidi:<name>` (next section).

**The image decides the kit (#273).** The drum kit in the known state is a
property of the Arty image on the board, not of the tree the session runs
from (`uart_host.image_kit`, as for `uart_host.py run`). `--image release`,
the default on a serial port, sends the frozen revision-11 kit the published R0
image plays: it has no `ENV_FRATE[8]`, which that image does not decode.
`--image tree` sends this tree's revision-14 kit, for a board built from this
tree (the R1 candidate, `fpga/release/R1.md`). `--port sim` is this tree's device contract, so it implies `tree`, and
`--port sim --image release` is REFUSED before anything is opened. The frozen
kit refuses (`KitRefused`) if it no longer hashes to the image it was verified
with. `fpga/test_midi_image_kit.py` holds all of this, with controls.

## macOS: a USB controller through CoreMIDI (#322)

`--midi-in coremidi:<source name>` reads a CoreMIDI source, such as the
operator's Novation Launchkey 37 over USB. `--list-midi-ports` lists the
sources. The adapter is `fpga/coremidi_input.py`. It calls the CoreMIDI
system framework through `ctypes`, so it needs no package and nothing changes
in `spec/trial-environment.json`, on the build box or in CI.

The adapter is only an input. It hands the received bytes to the same
`MidiSession.feed` as the raw and scripted inputs, and through the same
`read(timeout)` shape. It filters no channel and parses nothing. Routing
(channel 1 voice, channel 10 drums), every refusal and all scheduling stay the
session's, and T-LIVE-MIDI is still their source of truth.

- **Selection is by name.** An exact display name wins. Otherwise one unique
  case-insensitive substring is accepted. The adapter never guesses; each of
  these is **REFUSED** (exit 2), listing the sources that do exist:
  - two or more matches;
  - no match;
  - no sources at all;
  - an offline source (CoreMIDI remembers it but the device is gone).

  The MIDI input is opened before any serial port or simulated device, so a
  missing controller refuses before anything is sent.
- **Unplugging the controller mid-session:** everything received before the
  loss is still played. Then the session closes and sends its PANIC
  (GATE_OFF, stops cleared) **over the UART**. It prints `ERROR -- the MIDI
  input was lost mid-session (...)` on stderr and exits 1.
  - The panic silences the board **only if the UART link is still up**. A
    lost MIDI input with a live UART is the case tested here.
  - If the UART itself is lost (USB cable pulled, board reset), the host
    cannot reach the board. Nothing in this session can guarantee a
    hardware mute, and none is claimed.
  - A board reset is seen as BOOT on the wire; a dead serial port surfaces
    as the serial library's error. The adapter detects the loss from CoreMIDI's setup notifications
  and a 100 ms re-check of the endpoint (removed, or `offline`). A raw device
  whose read fails (`OSError`) is handled the same way. Before #322 that
  escaped `run_live` with no panic.
- **Receipt time** is the host monotonic clock in CoreMIDI's read callback.
  That is the latency endpoint's start: "the instant the session's MIDI input
  hands the message over".
- `--echo-midi` prints every chunk of bytes received, with its receipt time.
  At exit the CLI prints `accepted events by kind {...}`.
- CoreMIDI may deliver several messages in one packet: it did so on macOS
  26.5 for back-to-back sends. The session's parser already accepts any
  chunking.

Tests (`fpga/test_coremidi_input.py`):

- **Hardware-free**, on the simulated device with a fake CoreMIDI backend:
  - naming and selection;
  - the refusals;
  - a missing port refusing before any device opens;
  - all 16 channels reaching the session byte for byte (1 and 10 routed,
    the other 14 refused as `channel`), each with its note-off: the voice
    note-off closes the gate, the drum note-off is the documented no-op, and
    the other channels' note-offs are refused like their note-ons;
  - a disconnect mid-session (played, then panic, then error);
  - a raw device's `OSError`;
  - the CLI's exit 1.
- **Mac-only** (skipped elsewhere): they drive the real framework through an
  in-process virtual source, with no controller.
  - The `MIDIPacketList` layout is checked against CoreMIDI's own encoder.
  - A control: the other alignment rule must misread it.
  - Listing, open by name, delivery on channels 1/10/16, and loss after
    `MIDIEndpointDispose`.
- **Start red.** Against a mutant `run_live` that does not catch a lost
  input, the disconnect, raw-`OSError` and channel tests fail with the
  uncaught loss. Against `origin/main`, the missing-port test fails because
  a device was opened first.

### Operator smoke procedure (Launchkey 37): PENDING OPERATOR (not yet run)

A person runs this on the Mac with the controller plugged in. It is not
claimed until they do. Paste the terminal output and the log into #322. The
missing hardware run does not block landing the adapter: the adapter's
evidence is the tests and the virtual-source dry run below.

The live player maps **11 of the 16 808 sounds** (BD, SD, LT, MT, HT, CH, OH,
CP, CB, CL, CY). Pads that send conga, rimshot or maraca notes (for example GM
37, the side stick) are refused as `unmapped-drum`. That is expected, and
#298 tracks it.

Run this on a quiet Mac. The `--port sim` board is a real-time simulation in
the same process. At a load average around 100 on this laptop, one CoreMIDI
dry run ended with `FAIL -- device errors ... deadline misses`, and the same
run at load ~38 exited 0. That is host timing, measured below. It is not a
MIDI input fault.

From the repository root (zsh, the macOS default shell; the repository's `.venv`):

```
# 0. the adapter works on this Mac, with no controller (expect 0 skipped)
.venv/bin/python -m pytest fpga/test_coremidi_input.py -q -rs

# 1. list the sources with the Launchkey plugged in
.venv/bin/python fpga/midi_session.py --list-midi-ports
```

Step 1 should list the Launchkey's MIDI port and its DAW port. Use the
**MIDI** port, not the DAW port, and copy its exact name.

```
# 2. a 60 s session on the simulated board, driven by the Launchkey
mkdir -p build
.venv/bin/python fpga/midi_session.py --port sim \
    --midi-in "coremidi:<exact MIDI port name from step 1>" \
    --echo-midi --duration 60 2>&1 | tee build/live-midi-launchkey.log
echo "exit=${pipestatus[1]}"     # the session's status, not tee's
```

During the 60 s:

- **Note.** Press and release a key. The log shows
  `MIDI in ... 90 <note> <vel>` and then `80 <note> ..` (or `90 <note> 00`).
- **Drum.** Put the pads in Drum mode and hit several. The log shows
  `MIDI in ... 99 <note> <vel>`: 36 is BD, 38 SD, 42 CH, 46 OH. Pad pressure
  may add `REFUSED poly-aftertouch`, which is expected.
- **Knob.** Turn a pot. The log shows `MIDI in ... b0 <cc> <value>`.
  - If the CC is 74, 71 or 7, it is accepted as a knob event.
  - Any other CC prints `REFUSED cc-unsupported: CC<n>`. The event arrived,
    but that CC is unmapped. To exercise a mapped knob, set a pot to CC74 in
    the Launchkey's custom mode (Novation Components).

At the end, check the log for:

- the line `accepted events by kind {...}`, with `note-on`, `note-off` and
  `hit` at least 1, plus `knob` if a mapped CC was used, and `panic: 1`;
- `device errors 0, drops 0`;
- `exit=0`.

```
# 3. unplug: start the same session without --duration, hold a key, pull the USB cable
.venv/bin/python fpga/midi_session.py --port sim \
    --midi-in "coremidi:<exact MIDI port name>" --echo-midi 2>&1 | tee build/live-midi-unplug.log
echo "exit=${pipestatus[1]}"
```

After step 3, expect:

- `closed (MIDI input lost (... removed ... / ... offline ...))`;
- `ERROR -- the MIDI input was lost mid-session`;
- `'panic': 1`;
- `exit=1`.

```
# 4. Mac host latency on a quiet machine (refuses if the load average > CPU count)
.venv/bin/python fpga/measure_mac_midi_latency.py
```

The same session was dry-run on this Mac with an in-process virtual keyboard
instead of the Launchkey (`Parasynth Smoke Keyboard`). It listed the source,
played two note-ons, a note-off, three hits and a CC74 knob. It refused GM 37,
CC21, poly aftertouch and channel 3 by name, and exited 0 at load ~38. On an
unplug (`MIDIEndpointDispose`) it printed the explicit error and exited 1.
Asking for `coremidi:Launchkey` with no Launchkey attached was REFUSED
(exit 2), listing the two sources present. That is evidence for the adapter
and the CLI, **not** for the Launchkey.

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

**The live player maps 11 of the 16 808 sounds:** BD, SD, LT, MT, HT, CH, OH,
CP, CB, CL and CY. The three congas, the rimshot and the maracas are refused
as unmapped; #298 tracks exposing them. It is not yet the full 16-sound
instrument.

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
gate off, stops clear. On the tree image (R1, #279) the image is preceded by
voice RESET and drum RESET, which zero every register and state, and the
session first REFUSES if the device reports queued events or writes from an
earlier session. That is 193 writes on the tree image (revision 14) and
190 on the release image (revision 11, no `ENV_FRATE[8]`). It waits for every ACK and then
anchors its time map with one STATUS. Nothing is reset per note.

## Timing contract (`fpga/live_midi_contract.py`, timing contract 3)

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

- **Overload (contract 3, #330).** A late packet is sent re-dated to the
  first frame the wire can meet, and counted. A chunk the host reaches more
  than `STALE_MS` (100 ms) after its receipt is handled as received now, and
  its note-ons and drum hits are refused `stale`. See "Host overload" below.

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

### Mac host scheduling (#322, #330): quiet-host run pending

**Scope: the host scheduling endpoint only. This is NOT key-to-speaker
latency.** The start is the moment the session's MIDI input hands a message
over. Each boundary on either side of the host is separate and none is
measured here:

- controller scan and USB ingress, before the receipt;
- the real UART/FTDI link and the device, modelled here;
- the DAC;
- the audio interface and capture.


`fpga/measure_mac_midi_latency.py` separates the host from UART and device
timing:

- **Real:** the session and `run_live` loop run on this Mac's scheduler and
  wall clock. The CoreMIDI adapter feeds them from an **in-process virtual
  source** that plays the declared load (`sustained`, seed 281, 20 s) in
  real time.
- **Modelled:** the UART and the device are the contract model
  (`SimSerial` evaluated on the wall clock, with no pty and no thread).
  USB, the FTDI bridge and the board belong to the hardware capture.

It reports four measurements:

- **endpoint:** receipt → applied, on the device's own timeline, using the
  contract's endpoints. A superseded knob value starts at its own receipt.
- **host hold:** receipt → the anchor packet written.
- **lateness:** each packet's write time minus its planned release
  (due − 16 ms). This is the number the host alone decides.
- **delivery:** CoreMIDI's own hand-off, which comes before the endpoint's
  start and is not in the target.

How the apparatus is checked:

- **Known answer.** On simulated time its endpoint distribution equals
  `verify_live_midi.check()`'s to 1e-9 (n, min, p50, p95, p99, max).
  `check()` pairs through the independent oracle.
- **Control.** One 25 ms send-loop stall must turn `on_time` red. It does, and
  deterministically on simulated time (`fpga/test_measure_mac_midi_latency.py`).
- **Load precondition.** The tool REFUSES when the 1-minute load average
  exceeds the CPU count, unless `--allow-loaded` labels the result.

It also **accounts for every offered message** (#330). Each message the
input handed over ends as exactly one of:

- scheduled and delivered in its frame;
- delivered **late**;
- **lost** (no executed anchor);
- superseded (a knob value carried by an earlier update);
- refused by name;
- a contract no-op.

The target counts as met only when the accounting is complete and **nothing
is lost**. Percentiles over the anchors that survived say nothing about the
ones that did not, and are never reported without the lost count beside them.

**Pending a quiet host: NOT YET RUN.** The two runs below are **load samples
on a heavily loaded laptop**, not evidence about a quiet one and not a proved
worst case. They were also measured before two repairs: the device model's
late-event stall (#329, repaired in #341) and the offered-message accounting
above. Their endpoint percentiles excluded 114 and 205 lost anchors. On an
available quiet Mac, run:

```
# quiet: the tool REFUSES if the 1-minute load average exceeds the CPU count
.venv/bin/python fpga/measure_mac_midi_latency.py \
    --json fpga/reports/live-midi/mac-host-latency-quiet.json
# the specified controlled load: 4 CPU-bound busy processes during the run
.venv/bin/python fpga/measure_mac_midi_latency.py --controlled-load 4 \
    --json fpga/reports/live-midi/mac-host-latency-load4.json
```

Report each result's `offered`, `accounting`, `lost` and `late` alongside its
percentiles.

Historical load samples (development laptop, M-series, 10 CPUs, macOS 26.5.1;
pre-#329 model; lost anchors excluded from the percentiles):

| run (`fpga/reports/live-midi/`) | load (1 min) | lateness p50 / p95 / p99 / max | deadline misses | endpoint p50 / p95 / p99 / max (paired only) | lost anchors | CoreMIDI delivery p50 / p99 / max |
|---|---|---|---|---|---|---|
| `mac-host-latency-run1.json` | 80 | 0.56 / 4.33 / **27.15** / 36.72 ms | 49 | 16.13 / **22.79** / **32.63** / 45.40 ms | **205** | 0.05 / 1.25 / 57.9 ms |
| `mac-host-latency-run2.json` | 30 | 0.46 / 2.00 / 4.97 / 14.37 ms | 8 | 16.10 / 19.75 / 20.88 / 21.31 ms | **114** | 0.05 / 0.47 / 1.53 ms |

Neither run meets the target: both lost anchors. The host-only numbers
(lateness, hold, delivery) are the usable part: under that load a packet left
up to 27 ms (p99) after its planned release.

### Host overload: defined, reported, recovered (#330, timing contract 3)

The host is overloaded in two ways, and each has a defined response:

- **Too much offered traffic for the link.** A note-on or drum hit is
  admitted only if the wire can still deliver it; otherwise it is REFUSED
  `queue-pressure`. That is unchanged, and the `pressure` scenario tests it.
- **A stalled host loop** (scheduler, GC, a loaded Mac):
  - **Late packets are re-dated.** A packet that leaves after its deadline is
    sent with the first due the wire can still meet, never with its stale
    one. It is counted as a deadline miss; the first is printed live
    (`LATE -- ...`) and the CLI exits 1 at close.
  - **Stale notes are refused.** A chunk the host reaches more than
    `STALE_MS` (100 ms) after its receipt is handled as received now.
    A note-on or drum hit in it is REFUSED `stale`, because a late note is
    not played. Note-offs, knobs and panic in it are still delivered, because
    the device state must converge.
  - **100 ms is a product choice, decided by the operator** (#330). It sits
    well past the frozen p99 target, so only a real stall trips it. The
    alternative, playing late notes, was considered and rejected: a note
    that sounds a quarter-second after the key is worse than no note.
  - **Sent dues never decrease** (#339). The device drops an event whose due
    is before the last one it queued. This is the numeric contract and
    `uart_bridge.v`'s behaviour, and the operator adopted it as policy P3.
    The host avoids creating that case:
    - A note handed over a few milliseconds after its receipt can be
      scheduled before a kick's already-sent timed tail (the BD attack
      window, about 190 frames after its anchor).
    - Without a guard, the device dropped **every packet of that note**
      (measured: ERR 3 on all five).
    - The host now sends such a packet at the last sent due instead, at most
      the tail's offset later (about 4 ms). It prints the first case live
      (`ORDER -- ...`) and counts each one (`order_guarded`, in the closing
      line).
    - A drop the device still reports arrives as `DEVICE ERR ... due in the
      past or out of order`, and the CLI exits 1.

Why re-dating matters: before it, a 1 s stall with receipts still
timestamped **lost 57–69 of 233 scheduled messages**. A due more than half a
counter revolution (0.68 s) old reads as the *future* on the device's 16-bit
timeline, parks at the head of the FIFO, and everything behind it is dropped
as out of order.

Measured on simulated time with the repaired model: the Probe, `sustained`,
8 s, one stall at 2 s (`fpga/test_measure_mac_midi_latency.py` holds this).

| stall | lost | stale refused | device errors | received after the stall and over target |
|---|---|---|---|---|
| 25 ms | 0 | 0 | none | 0 |
| 100 ms | 0 | 0 | none | 0 |
| 250 ms | 0 | 4 | none | 0 |
| 1 s | 0 | 14 | none | 0 |
| 3 s | 0 | 44 | none | 0 |

Recovery is immediate: no message received after a stall ends misses the
target. No note or hit sounds more than `STALE_MS` plus the lookahead late.

The controls are each repair removed (`inject` `NO_REDATE` / `NO_STALE`):

- without re-dating, the same 1 s stall **loses** events again (54);
- without the stale rule, notes sound up to a second late;
- without the order guard (`NO_ORDER_GUARD`), the device drops the note that
  was handed over late behind a kick's tail.

The late-hand-over case is a likely cause of the lost anchors in the loaded
laptop runs above. There, host hold reached tens of milliseconds, and the
declared load has a kick on most beats. That is an inference from the
mechanism, not re-measured, and the quiet-host re-run will show it.

## Verification

```
make trial T=T-LIVE-MIDI ARGS="--mode sim"                 # the trial: receipt under build/trials/
make trial T=T-LIVE-MIDI ARGS="--mode rtl"                 # + UART RTL and I2S (build box, ~1 h)
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
off is NO VERDICT, never a result. A twelfth property, `release_domain`, runs
the release's own validator (`fpga/release/qualified_domain.check_stream`,
#255) over every write the device executed.

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

**RTL results on the R1 candidate (revision 14, #279).** `make trial
T=T-LIVE-MIDI ARGS="--mode rtl"` on the build box at `ccf7ed4` (RTL frozen at
`6864435`): **PASS**, 3 of 3 controls caught; receipt in
`fpga/reports/r1-candidate/receipts.tgz` (`T-LIVE-MIDI/*-rtl-*`). The session
sends `--image tree` (193-write known state with the preamble, kit
`321a9354…`); the oracle is the frozen R1 target, not the session's selector.

| replay | writes | frame errors | I2S mismatch | worst strobe |
|---|---|---|---|---|
| coverage | 397/397 | 0 | 0 of 109 053 periods | 198 of 256 |
| pressure | 588/588 | 0 | 0 of 46 653 periods | 198 |
| sustained (first 3 s) | 502/502 | 0 | 0 of 166 513 periods | 198 |
| control WRONG_DRUM_MAP | wrong address at write 349 | -- | **47 842** of 109 053 (caught) | |
| control DELAYED_EVENT | writes 208.. land 240 frames late | 4 | **84 474** of 109 053 (caught) | |

Latency under the declared load, same run: p95 16.73 ms, p99 16.98 ms (n 905).

**Historical: revision 11 (before #273; 190-write known state, no
`ENV_FRATE[8]`), kept as measured, not evidence about R1.**

| replay | writes | frame errors | I2S mismatch |
|---|---|---|---|
| coverage | 394/394 | 0 | 0 of 108 912 periods |
| pressure | 585/585 | 0 | 0 of 46 512 periods |
| sustained (first 3 s) | 499/499 | 0 | 0 of 166 372 periods |
| control WRONG_DRUM_MAP | wrong address at write 346 | -- | **47 824** of 108 912 (caught) |
| control DELAYED_EVENT | writes 205.. land 240 frames late | 240 | **84 447** of 108 912 (caught) |

Its records stay at `fpga/reports/live-midi/verification.json` and `rtl-replay/`.

`make trial T=T-LIVE-MIDI ARGS="--mode sim"` gives PASS with 3 of 3 controls
caught (`trial-sim.log`, `trial-sim.receipt.json`).

**Start red.** Against `fpga/stubs/midi_session_stub.py` every scenario FAILS
by named property (static image 0/193, gates 0/19, strikes 0/14, refusals
0/14, ...; `fpga/reports/live-midi/start-red.log`). A first stub with a naive
time map 34 frames off was refused as NO VERDICT by the precondition before
any property was read.

## Wrong-then-right record

Seven results were wrong before they were right. Five were caught by a
control, a precondition or a test. One was caught by re-measuring a number
before publishing it. One was caught by actually running the start command:

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
7. **Start command REFUSED at start.** The documented start command's first
   run on the box REFUSED: 48 of 190 ACKs. `SimSerial` delivers one ACK per
   read, and every deterministic test passed. A real-time pty delivers four,
   and `Bridge._take` counted only the first of each chunk.

### Wrong-then-right, #322 (Mac input and host latency)

Four more, all caught before publishing:

8. **One packet per send.** The Mac loopback test assumed one CoreMIDI
   packet per `MIDIReceived`. CoreMIDI coalesced three back-to-back sends
   into one. The test now compares the byte stream (chunking is the parser's
   job). The test caught it on its first Mac run.
9. **Real clock through `check()`.** The first latency apparatus ran
   T-LIVE-MIDI's `check()` on a real-clock run. `check()` assumes one time
   anchor; a real host re-anchored 34 times, and every frame was unwrapped
   from the final anchor, giving an "endpoint" p50 of 17.8 **seconds**. The
   tool now computes the endpoint itself, and is held to `check()`'s numbers
   on simulated time.
10. **Greedy pairing.** The second version paired executed writes with sent
    packets greedily in order. After one dropped packet it consumed the rest
    of the stream: 3 anchors paired of about 230. It now uses sequence
    matching. The unpaired count caught it.
11. **Negative histogram bin.** The stall control crashed `check()` in
    `_hist` on a negative histogram bin instead of returning a verdict. The
    control test caught it, and it is reported in #329.

All four were caught by a test, a control or an absurd number before
anything was published.

12. **The cause of the overload loss.** With the pre-#329 model, the 1 s-stall
    loss showed up as device queue overflow (ERR 2), and it looked like the
    model's revolution stall. After the model repair the loss remained, with
    only ERR 3. The real cause is a stale due, more than 0.68 s old, read as
    the future on the 16-bit timeline. The offered-message accounting caught
    it. Before that accounting, the lost anchors were only an "unpaired"
    count beside the percentiles.

## Known limits

- **USB latency is not modelled.** The FTDI USB path adds delay that the
  simulated wire does not have, and the 16 ms lookahead's margin has to absorb
  it. That is the first thing the hardware capture must measure.
- **Note-off avalanche.** Releasing many held keys in reverse order within a
  millisecond generates a pitch return for each release. These are
  unconditional, so they are delivered late (up to 50 ms) rather than refused,
  and each move is counted. The declared load never does this.
- **Late events (#329, repaired in #341).** The RTL was tested against a
  pre-stated late-event policy: a late event executes in the next frame,
  with no stall. The device model had waited a whole counter revolution and
  is repaired to match. One policy edge case is open in #339.
- **The Launchkey itself is unverified.** The smoke procedure above is
  pending the operator, and the Mac latency has only been measured on a
  loaded host, for the host scheduling endpoint only.
- **A lost UART cannot be muted from the host.** The panic on a lost MIDI
  input travels over the UART.
- **The mod wheel is untested on the release image.** CC1 is refused on the
  default patch; a routed patch is exercised only by a unit test.

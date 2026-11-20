# First hardware session: one sheet, one stage at a time

This is the operator sheet for the first hardware session (#325). It records R0
first. R1 steps come later (see the end of this sheet).

**Rule of the sheet:** each stage has a check you can observe. **Do not start a
stage until the previous one has passed**, and when a stage fails, change only
that stage. For example, if the UART does not answer, do not touch the DAC
wiring. If there is no sound, do not reprogram the board until the UART has
been re-checked. One change at a time is the only way to find out which stage
is at fault.

The detailed R0 capture procedure is [`docs/capture-r0.md`](capture-r0.md). Its
section numbers are given below as §N. This sheet adds the stage boundaries, the
parts, the soldering, and a record to fill in as you go.

**Nothing here says the board works or is faulty.** That is only known once
someone has observed it. Write down what you saw, not what you expected.

---

## Parts (a recommendation, not an order)

| Part | Notes |
|---|---|
| Digilent **Arty A7-100T** | check that the silkscreen says 100T |
| Micro-USB cable **known to carry data** | proven by having moved files with it; charge-only cables light the board but hide it from the Mac |
| **Adafruit PCM5102 I2S DAC**, product 6250 | an assembled breakout; its supplied header must be **soldered** |
| 5 **short** female-to-female jumper leads | 10 cm or less, all the same length |
| **3.5 mm TRS → two 1/4-inch TS** cable | tip to M4 **LINE IN 3**, ring to **LINE IN 4** |
| MOTU M4 | rear LINE IN 3/4 are fixed-gain line inputs |
| Soldering iron, solder; a multimeter if you have one | the multimeter is only for stage 5's supply check |

The DAC's jack is a **line output**, not a headphone driver. Listen through the
M4, not on headphones plugged into the breakout.

**Not needed for these stages:** the DAC is not needed for stages 1–4, and the
Launchkey is not needed at all. Every R0 command is host-driven.

## Soldering the DAC header (before the session)

1. Solder the header strip **only** to the pads named below (Adafruit labels).
   Leave the configuration pads **MCK, DE, FIL, MU, FM** unconnected. Their
   defaults are: internal clock, de-emphasis off, normal filter, unmuted, I2S
   format (§3).
2. Needed: **VIN, GND, BCK, WSEL, DIN**.
3. Inspect every joint for bridges, especially between adjacent pins. A bridge
   from **MU** to GND mutes the output, and a bridge on **FM** changes the data
   format. Both give silence with everything else correct.

---

## Stage 1: power (USB cable only; no DAC, no programming)

| Do | Pass when | Record |
|---|---|---|
| Plug the data cable into the Arty's **J10** and into the Mac directly, with no hub | **LD11** (power-good) is lit | LD11 lit or not; DONE lit or not; whether any user LED blinks |

Blinking user LEDs are **not** a test. A blank flash, or an open mode jumper
JP1, blinks nothing and is normal (§2.1). If LD11 stays off, follow the §2.1
table: try another port, another cable, then an external 7–15 V supply on
J13. Nothing else in this sheet can proceed until LD11 lights.

## Stage 2: USB and JTAG detection

| Do | Pass when | Record |
|---|---|---|
| `openFPGALoader -b arty_a7_100t --detect > $S/detect.txt 2>&1; echo "exit $?" >> $S/detect.txt` (§2.2) | the last line is `exit 0` and the output names **xc7a100t** | keep `detect.txt` as it is |

**Fails, but LD11 is lit:** suspect the cable first (a charge-only cable).
Then check `system_profiler SPUSBDataType | grep -i -B2 -A6 "0x0403"`, which
should show the board's FTDI vendor ID. Do **not** change the jumpers, the
power supply or anything else yet.

## Stage 3: programming R0

| Do | Pass when | Record |
|---|---|---|
| the five-line block in §5, appending to `$S/program.txt` | `release_manifest: BOUND` with `exit 0`; R0's `a66c9349…4cb95` shasum line; the programmer's own `exit 0`; **DONE** lit; LD4 and LD5 lit; LD6 blinking about every 1.4 s | keep `program.txt` as it is |

If the programmer fails, fix the cause and **repeat only the last three lines**
(shasum, `--Version`, program), appending to the same file. The failed attempt
stays in the file, and the analysis records it (§5). Do not edit
`program.txt`.

This transcript is **not a readback** of the FPGA. Programming is volatile, so
if the board loses power, return to this stage.

## Stage 4: UART (the host link; still no DAC)

| Do | Pass when | Record |
|---|---|---|
| `ls /dev/cu.usbserial-*`, then `.venv/bin/python fpga/uart_host.py --port $P status` (§6) | a STATUS line with a frame counter | the port name and the STATUS line |

`status` is a read-only query. It writes no register and plays nothing.

**Fails:** try the board's other serial port, which is usually the one ending
in `1`. Confirm stage 3's LEDs are still as recorded. **Do not wire or debug
the DAC to fix a UART failure.** Audio cannot work until this stage passes.

## Stage 5: I2S and DAC (power off to wire)

1. **Unplug J10.** Wire the DAC as in §3:
   - JA1 → BCK
   - JA2 → WSEL
   - JA3 → DIN
   - JA5 → GND
   - JA6 → VIN

   Pin 1 is marked on the Arty's silkscreen. Keep the leads short.
2. Plug the TRS cable in: tip to M4 **LINE IN 3**, ring to **LINE IN 4**.
3. Plug J10 back in, then **repeat stage 3** (programming is lost at power-off)
   and **stage 4**.
4. Press **BTN0** once.
5. Play the held note:
   `.venv/bin/python fpga/uart_host.py --port $P run --note 45 --fixture none`

| Pass when | Record |
|---|---|
| the M4's input meters for **3 and 4** move together, and you hear a short low note (MIDI 45) through the M4's monitor or headphone output | meters seen or not; heard or not; the host's closing `uart_host: done … drops 0, errs 0` line |

**No sound, but stage 4 passed.** The fault is now between the FPGA pins and
the M4. Work through these in order, changing one thing at a time:

1. **LD7** (LRCLK) should be steadily half-lit. That means I2S is running at
   the FPGA.
2. Check the DAC's supply between VIN and GND with the multimeter: 3.3 V
   expected.
3. Check that each lead goes to the right pad (BCK/WSEL/DIN).
4. Check the solder joints for a bridge on **MU** or **FM**.
5. Check that the TRS cable is in the rear **LINE IN 3/4**, not the front
   combo jacks.

**Only one meter moves:** the TRS cable or its TS ends. The DAC drives both
channels identically.

This is a smoke check, **not** a T-PHYSICAL result.

## Stage 6: audio capture setup (the M4 and the recorder)

| Do | Pass when | Record |
|---|---|---|
| In Audio MIDI Setup, set M4 to 48,000 Hz; check with `system_profiler SPAudioDataType \| grep -A8 "  M4:"` (§4) | `Input Channels: 4`, `Current SampleRate: 48000` | the output lines |
| The recorder check in §4 step 4 (`sox -V3 -D -t coreaudio M4 -b 24 …`) | `Channels : 4`, `Sample Rate : 48000`, and an effects chain of only input → trim → output | the output lines |

Do not touch the M4's knobs or buttons from here on.

## Stage 7: the R0 capture

1. Run `.venv/bin/python tools/r0_capture.py new-session --bundle $S`.
2. Fill in `image.programmer`, `board.revision`, `board.power` and each take's
   `started` time.
3. Record the nine takes of §7 **in their order**:
   - `silence-1` right after a BTN0 reset;
   - `demo-1`, the calibration take;
   - the rest in the listed order.
4. Any take whose host run did not end `drops 0, errs 0` is invalid. Delete its
   WAV and record it again. Never keep a failed run as a take.

## Stage 8: analysis

```sh
.venv/bin/python tools/r0_capture.py analyse --bundle $S
R0_CAPTURE_BUNDLE=$S .venv/bin/python tools/trial.py run T-PHYSICAL
```

The three possible outcomes are distinct (§8):

| Outcome | Exit | Meaning |
|---|---|---|
| **REFUSED** | 2 | missing or inconsistent evidence; the reason names it |
| **ERROR** | 3 | the analysis itself failed |
| **FAIL** | 1 | a measured property missed its limit |

A PASS names its scope. On the held-note takes, residual, timing, gain, clock
and dropout are **not evaluated**, and releases are not compared. Report the
verdict, its scope and the receipt path on #208.

---

## Session record (fill in as you go)

| Stage | Result | Observed | File kept |
|---|---|---|---|
| 1 power | | LD11: ___  DONE: ___ | |
| 2 detect | | device named: ___ | `detect.txt` |
| 3 program R0 | | DONE: ___  LD4/5/6: ___ | `program.txt` |
| 4 UART | | port: ___  STATUS: ___ | |
| 5 DAC note | | meters 3/4: ___  heard: ___ | |
| 6 M4 and recorder | | rate: ___  channels: ___ | |
| 7 takes | | takes recorded: ___ / 9 | `takes/`, `host/`, `session.json` |
| 8 analysis | | verdict: ___  exit: ___ | `analysis.json`, receipt |

## R1 (not yet)

The R1 steps are added when #323 (the named R1 host selection) and #324 (the
R1 references and capture commands) land. Until then **there is no R1 capture
procedure**: do not program an R1 image into this sheet's stages, and do not
reuse R0's references or commands for it. Each R0/R1 mismatch is meant to
refuse before any sound is evaluated.

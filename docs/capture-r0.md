# Recording R0: the first physical capture

This is the operator procedure for plan087 Milestone E0 (epic #282, issue #208).
You record the published Arty image, **R0**, through the real line output. Then
one command compares each recording with what the simulation says the image
should play.

You need no FPGA or audio background to follow it. Do the steps in order, and
paste each command exactly as written. If a step's check fails, stop at that
step. Record what you saw, and do not work around it.

**What R0 is:** the bitstream
`fpga/reports/arty/integrated-baseline-2025.1/arty.bit`, SHA-256
`a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95`.
Release `arty-a7-100t baseline 2025.1, r1`
([fpga/release/RELEASE.md](../fpga/release/RELEASE.md), manifest
[baseline-2025.1.json](../fpga/release/baseline-2025.1.json)). The
configuration is `OSC2X=1 FILTER2X=1 PULSE2X=0`, and the host is
`fpga/uart_host.py` over the Arty's USB-UART at 115200 baud.

**What this produces:** a capture *bundle*. It holds the raw recordings, your
settings, the host's own log of every byte it sent, and the programming
transcript. `tools/r0_capture.py` analyses the bundle against the R0 reference
(`fpga/release/evidence/r0-reference/`, rendered by `tools/r0_reference.py` from
the image's own RTL). Trial `T-PHYSICAL` reports its verdict. Until a bundle
exists, T-PHYSICAL is **NO VERDICT (operator-blocked)**, never PASS.

---

## 1. What you need

| Item | Notes |
|---|---|
| Digilent Arty A7-**100T** | Check the silkscreen. The 35T takes a different bitstream, and this one will not load on it |
| Micro-USB cable **that carries data** | Many phone cables are charge-only. They light the board, but the Mac cannot see it |
| Adafruit PCM5102 I2S DAC breakout, product 6250 | Solder the header, or use jumper wires |
| 5 female-female jumper wires | Short and of equal length |
| 3.5 mm TRS (stereo) to two 1/4-inch TS (mono) cable | Tip to M4 input 3, ring to input 4 |
| MOTU M4 on the Mac's USB | Rear **LINE IN 3 / 4** |
| This repository with `.venv` | `.venv/bin/python -c "import numpy, scipy, serial"` must print nothing |
| `openFPGALoader`, `sox` | `brew install openfpgaloader sox`. Verified on this Mac: openFPGALoader 1.1.1, sox 14.4 (CoreAudio input) |

Set two shell variables once per terminal. Every command below uses them.

```sh
cd ~/dev/gf180-parasynth          # your checkout
export S=captures/r0              # the bundle directory (R0_CAPTURE_BUNDLE)
export P=/dev/cu.usbserial-XXXX1  # set in step 3
```

---

## 2. Power-on and detect

### 2.1 Power

Plug the data-capable micro-USB cable into the Arty's **J10** (the only USB
connector, labelled PROG/UART) and into the Mac.

What you should see. Per the Digilent *Arty A7 Reference Manual*, sections 3–4
(checked 2026-09-26 against the archived page, 2024-11-07 snapshot):

- **LD11, power-good, lights.** It is driven by the 3.3 V regulator output, so
  lit means the board's supplies are working.
- **DONE lights only if the FPGA is configured.** The manual says "After being
  successfully programmed, the FPGA will cause the DONE LED to illuminate."
  At power-on the FPGA loads itself from the on-board flash **only when the
  mode jumper JP1 is fitted**. It then runs whatever the flash holds, which on a
  new board is usually Digilent's demo with blinking LEDs. **A board that
  lights LD11 but does not blink is not broken.** Its flash may be blank, or
  JP1 may be open. JTAG programming works either way, and "regardless of
  whether the mode jumper (JP1) is set."

**The Arty A7 has no power-select jumper.** Section 3, "Power Supplies," says
the board "uses a combination of a USB load switch (IC13), a MOSFET (Q8), and
some additional control circuitry to automatically determine the 5V power
source." An external 7–15 V DC supply on the barrel jack J13 (center-positive,
2.1 mm, at least 1 A) takes over when present. The board's two jumpers are
different things:

- **JP1** is the configuration mode: boot from flash.
- **JP2** connects the red RESET net to the FTDI's DTR line.

Guides for the Basys 3 or the Nexys boards describe a power-select jumper.
They do not apply to this board.

| Symptom | Likely cause | What to do |
|---|---|---|
| LD11 off on USB | No power reaching the board: dead cable or port, or an unpowered hub | Try another cable and another Mac port directly, without a hub. Then try an external 7–15 V center-positive 2.1 mm supply on J13; J13 is selected automatically when present. If LD11 stays off on both, the board is faulty. Contact Digilent. |
| LD11 flickers or toggles | Brown-out. The manual names "the power-good LED toggles" as its sign | Use the external J13 supply |
| LD11 on, nothing blinks, DONE off | Flash blank or JP1 open. **Normal** | Go to 2.2 |
| LD11 on, but 2.2 finds no device | Charge-only cable, or the FTDI driver is not bound | Swap to a known data cable (one that has moved files). Run `system_profiler SPUSBDataType \| grep -i -B2 -A6 "0x0403"`. The board's FT2232H has vendor ID `0x0403`. If it is absent, the Mac does not see the board at all |

### 2.2 Detect

```sh
mkdir -p $S
openFPGALoader --list-boards | grep -w arty_a7_100t
openFPGALoader -b arty_a7_100t --detect > $S/detect.txt 2>&1; echo "exit $?" >> $S/detect.txt; cat $S/detect.txt
```

Pass condition: the last line is `exit 0`, and the output names an
**xc7a100t** device. `arty` is the board name for the 35T and is not this
board. If the output names `xc7a35t`, you have the 35T: stop. Keep
`detect.txt` in the bundle.

The `echo "exit $?"` line records the programmer's own exit status. Do not pipe
the command through `tee`, because the pipe would report `tee`'s status
instead.

---

## 3. Wiring (power the board off first)

Unplug J10. The pin map is `fpga/ARTY.md` § Wiring, which cites Digilent's
master XDC. **Pmod numbers are connector positions, not FPGA pins.** Pin 1 is
marked on the Arty's silkscreen: the top row is 1–6, and 5 and 6 are GND and
3.3 V.

| Arty Pmod **JA** position | FPGA pin (for reference) | Breakout pad (Adafruit label) | Signal |
|---|---|---|---|
| JA1 | G13 | **BCK** | I2S bit clock, 3.072 MHz |
| JA2 | B11 | **WSEL** | I2S word select (LRCLK), 48 kHz |
| JA3 | A11 | **DIN** | I2S data |
| JA5 | — | **GND** | common ground |
| JA6 | — | **VIN** | 3.3 V supply. The breakout accepts 3.3–5 V |

Leave **MCK, DE, FIL, MU, FM** unconnected. Adafruit's pinout page states these
defaults:

- the PCM5102 generates its own main clock (MCK is optional);
- de-emphasis "by default it is off";
- the filter is normal until FIL is pulled high;
- the outputs are unmuted until MU is pulled low;
- the format is I2S until FM is pulled high. The design emits standard I2S: MSB
  on the second BCK after the WSEL edge, WSEL low for left.

Nothing goes on Pmod JB. JB is the SPI controller header, and this procedure
does not use it.

**Audio cable:** plug the breakout's 3.5 mm jack into the TRS-to-dual-TS cable.
Connect the **tip** (left) to **M4 LINE IN 3** and the **ring** (right) to
**M4 LINE IN 4**. R0 sends the same sample on both channels, so the order
cannot be heard. Wire it as stated anyway, and record the cable in the
session file.

Plug J10 back in. LD11 lights.

---

## 4. The M4 and the Mac

1. **Sample rate 48 kHz.** Open *Audio MIDI Setup*, select **M4**, set
   *Format* to **48,000 Hz**. Check it:

   ```sh
   system_profiler SPAudioDataType | grep -A8 "  M4:"
   ```

   The output must show `Input Channels: 4` and `Current SampleRate: 48000`.
   The analysis REFUSES any take that is not 48000 Hz. Nothing is resampled.

2. **Fixed gain.** LINE IN 3/4 have no gain knob. The MOTU M Series User
   Guide specifies the M4 line inputs at +18 dBu maximum, with no gain range.
   The front GAIN knobs affect inputs 1/2 only. Leave them where they are, and
   do not touch anything on the M4 during the session.

3. **No processing.** Nothing inserts effects on the recording path. `sox`
   records the device's raw samples, and `-D` turns off sox's dither. The MON
   buttons and the monitor-mix knob change only what you hear, not what is
   recorded. Leave them as they are.

4. **Check the recorder once** before the board plays anything:

   ```sh
   mkdir -p $S/takes $S/host
   sox -V3 -D -t coreaudio M4 -b 24 /tmp/m4check.wav trim 0 1 2>&1 | grep -E "Channels|Sample Rate|effects chain"
   ```

   The output must show `Channels : 4`, `Sample Rate : 48000`, and an effects
   chain of only `input`, `trim`, `output`. It must not show `rate` or
   `dither`. This exact check was run on this Mac on 2026-09-26.

**Expected level.** The PCM5102's full-scale line output is 2.1 Vrms (about
+8.6 dBu). The M4 line input clips at +18 dBu. DAC full scale therefore lands
near −9 dBFS in the recording, and the interface cannot clip on this DAC. A
clipped take means something else is wrong: the wrong input, or a mic preamp
in the path.

---

## 5. Program R0 and record the transcript

```sh
.venv/bin/python fpga/release/release_manifest.py > $S/program.txt 2>&1; echo "exit $?" >> $S/program.txt
shasum -a 256 fpga/reports/arty/integrated-baseline-2025.1/arty.bit >> $S/program.txt
openFPGALoader --Version >> $S/program.txt 2>&1
openFPGALoader -b arty_a7_100t fpga/reports/arty/integrated-baseline-2025.1/arty.bit >> $S/program.txt 2>&1; echo "exit $?" >> $S/program.txt
cat $S/program.txt
```

Pass conditions:

- `release_manifest` printed `BOUND`, followed by `exit 0`;
- the `shasum` line starts `a66c9349ef9b5572…4cb95`;
- the programmer's last line is `exit 0`;
- the **DONE** LED is lit.

**This transcript is not a readback.** It shows which file the programmer sent
and that the programmer reported success. It does not show what the FPGA holds.
The session file records `"readback": false`, and the analysis labels the image
identity "programming transcript only -- NOT a readback". Set `readback` to
true only if you performed an actual configuration readback and compared it.

Programming is volatile. A power cycle returns the FPGA to its flash image.
Complete the whole session in one power-on, and if the board loses power,
repeat this step and note it.

**LEDs after programming** (`fpga/rtl/arty_a7_top.v`):

| LED | Shows |
|---|---|
| LD4 (led[0]) | clock locked |
| LD5 (led[1]) | reset released |
| LD6 (led[2]) | heartbeat, blinking about every 1.4 s |
| LD7 (led[3]) | LRCLK, which looks steadily half-lit |

These lights do not prove audio. Press **BTN0** once now to reset the design
into a known state. The release requires a reset after any engineering-interface
use.

## 6. Find the UART port and check the link

```sh
ls /dev/cu.usbserial-*
```

The board's FT2232H shows two ports. One is JTAG and one is the UART, usually
the one ending in `1`. Set `P` to a candidate and ask the device for its status:

```sh
export P=/dev/cu.usbserial-XXXX1
.venv/bin/python fpga/uart_host.py --port $P status
```

A STATUS line with a frame counter means the link is up. If the command
REFUSES with no answer, try the other port.

`status` is a read-only link check. It sends one query packet, writes no
register and plays nothing. It is not one of the release's playback commands.

JP2 connects DTR to the red RESET net only. This design resets from BTN0 and
does not use that net, so opening the port does not reset R0.

---

## 7. The diagnostic set: exact commands

Create the session file once, then fill in `image.programmer` (paste the
`openFPGALoader --Version` line), `board.revision` (from the silkscreen), and
`board.power` (`USB J10` or `external J13 <volts>`):

```sh
.venv/bin/python tools/r0_capture.py new-session --bundle $S
open -e $S/session.json
```

Every take follows the same pattern:

1. Start a fixed-length recording in the background.
2. Wait one second.
3. Run exactly one release command with `--capture`, which writes the host's
   log of what it sent.
4. `wait` for the recording to finish.

Adding `--capture` changes nothing on the wire. The analysis compares that log
with the reference's pinned command and REFUSES a take whose register writes
differ.

Do not talk, tap or touch the rig during a take. Run the takes **in this
order**: `demo-1` is the calibration take, and `silence-1` must directly follow
the BTN0 reset.

| # | Take id | Tests | Exact command (one line each) |
|---|---|---|---|
| 1 | `silence-1` | noise floor, hum, idle output | `sox -D -t coreaudio M4 -b 24 $S/takes/silence-1.wav trim 0 10` |
| 2 | `demo-1` | **calibration take**: clock offset and gain, and the mixed phrase (voice, drums, knob sweeps) | `sox -D -t coreaudio M4 -b 24 $S/takes/demo-1.wav trim 0 12 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --fixture demo --capture $S/host/demo-1; wait` |
| 3 | `tone-1` | reference tone: MIDI 72, one sawtooth (m5a-saw), pitch and sample clock | `sox -D -t coreaudio M4 -b 24 $S/takes/tone-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --preset m5a-saw --note 72 --fixture none --capture $S/host/tone-1; wait` |
| 4 | `pulse-1` | second tone, pulse29 (47.9 %) at MIDI 72 | `sox -D -t coreaudio M4 -b 24 $S/takes/pulse-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --preset m5a-pulse --note 72 --fixture none --capture $S/host/pulse-1; wait` |
| 5 | `held-1` | held-note onset and release (default patch, MIDI 45) | `sox -D -t coreaudio M4 -b 24 $S/takes/held-1.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --note 45 --fixture none --capture $S/host/held-1; wait` |
| 6 | `phrase-1` | the bench-proven voice phrase | `sox -D -t coreaudio M4 -b 24 $S/takes/phrase-1.wav trim 0 8 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P play --fixture m5a --capture $S/host/phrase-1; wait` |
| 7 | `drums-1` | every drum identity the release plays: BD, SD, CH, OH, CP, CB, LT, HT over the bass line, two bars at 118 bpm | `sox -D -t coreaudio M4 -b 24 $S/takes/drums-1.wav trim 0 12 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --fixture bar808-full --capture $S/host/drums-1; wait` |
| 8 | `demo-2` | repeat-take stability, long | `sox -D -t coreaudio M4 -b 24 $S/takes/demo-2.wav trim 0 12 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --fixture demo --capture $S/host/demo-2; wait` |
| 9 | `held-2` | repeat-take stability, held note | `sox -D -t coreaudio M4 -b 24 $S/takes/held-2.wav trim 0 6 & sleep 1; .venv/bin/python fpga/uart_host.py --port $P run --note 45 --fixture none --capture $S/host/held-2; wait` |

After each take:

- `uart_host.py` must end with `uart_host: done; … drops 0, errs 0`. A `FAIL`
  or `REFUSED` line means the take is invalid. Delete its `.wav`, then fix and
  repeat it. Never keep a take whose host run failed.
- Fill in that take's `"started"` time in `session.json`.

The recording lengths cover each reference plus about 1 s of lead-in and the
host's setup time. The analysis REFUSES a take that ends before its reference
does, so do not shorten them.

### Commands in the release domain that this set uses

Each command above is one of the manifest's six pinned commands
(`commands.*` in `baseline-2025.1.json`): `held-default`, `held-m5a-saw`,
`held-m5a-pulse`, `phrase-m5a`, `demo` and `bar808-full`. The only exceptions
are the added `--capture` flag, which writes a local log and does not change the
bytes, and the idle silence take, which sends nothing.
`uart_host.py main()` runs the release-domain validator on each command before a
byte leaves the Mac.

### Diagnostics this set does NOT contain, and why

| Wanted | Why it is not here |
|---|---|
| **Each drum hit in isolation** | No R0 release command strikes a single drum. The six pinned commands contain no isolated hit. `--fixture bar808`, the compressed regression fixture, is not in the manifest's supported set. The engineering interface (`--engineering`, `spi_host.py`, `play.py`) is outside the qualified domain. Drum identity is therefore captured only inside `bar808-full` (take 7), where the hits overlap each other and the bass line. An isolated-hit command would first need to be added to a release, with its pinned bytes and RTL evidence |
| **A pure sine reference tone** | R0 has no sine oscillator path in its release presets. The reference tone is the single-sawtooth `m5a-saw` note 72. Its fundamental is measured against the RTL reference's own fundamental, so it needs no model of the waveform |
| **A longer held tone** (`--hold-frames N`) | It is accepted by the domain validator, but its bytes differ from the manifest's pinned `cmds_sha256`, so it is not a release command. The pinned hold is 1,920 frames (40 ms) |
| **`note-on` / `note-off` / `load` on their own** | The release refuses a standalone `note-on` (rule `WAVES`: the device's image is unknown to it). `load` is not a playback command |
| **The Launchkey 37 / live MIDI** | R0 has no live controller path ("No live controller path exists in this release"). That comes with R1 (#281) |
| **A loopback of the M4 alone** | Useful for characterising the interface, but it neither qualifies the DAC nor proves the instrument was in the path (plan087 §9). It is not part of T-PHYSICAL |

---

## 8. Analyse

```sh
.venv/bin/python tools/r0_capture.py analyse --bundle $S
R0_CAPTURE_BUNDLE=$S .venv/bin/python tools/trial.py run T-PHYSICAL
```

The first command writes `$S/analysis.json` and exits 0 (PASS), 1 (FAIL) or
2 (REFUSED, which is NO VERDICT). The second runs the trial with its control,
the synthetic defect suite, and writes a receipt under `build/trials/`.

**What is compared, and the one alignment that is allowed.** The model is
`capture[n] = g · ref((n − d_t) · ρ)`:

- **ρ (sample-clock ratio) and g (gain)** are estimated **once per session**
  from the whole calibration take, `demo-1`.
- **d_t (delay)** is estimated **once per take**, inside the reference's
  declared 30 ms calibration window just after the first onset. This is needed
  because you start each recording by hand.

All three are then frozen. Local lags and local gains are *measured* against
them and never used to re-align.

Every waveform comparison happens inside one **declared analysis band**,
100 Hz–16 kHz (`BAND_HZ`). The same zero-phase filter is applied to the capture
and to the prediction alike, so it removes only what the analog path is allowed
to do outside the audio band: AC coupling and the converters' anti-image
filters. It is never fitted. Without it, the clean synthetic session failed:
a 5 Hz coupling high-pass moved the kick's local lag by a full sample. Pitch,
clipping and the noise floor are measured on the raw capture.

Each reference carries 1 s of tail after its last write. The m5a presets'
release is still sounding at about −27 dBFS when that tail ends. The comparison
therefore stops 0.1 s before each reference ends (`END_GUARD_S`), and the
capture must run at least that long. A timing slip, a clock mismatch or a gain
change therefore shows up as a failure. It is not absorbed.

The checks and their limits are the `LIMITS` table in `tools/r0_capture.py`,
criterion `r0-capture/1`:

| Check | Fails when |
|---|---|
| routing | a declared input does not carry the reference; the reference is on an undeclared input; or inputs 3 and 4 differ (inverted or unequal) |
| silence | a declared input is quiet where the reference sounds |
| clipping | 3 or more consecutive samples at full scale |
| pitch | a tone take's f0 ratio (capture / reference) differs from the frozen clock ratio by more than 3 cents |
| clock | the session offset exceeds 200 ppm, or a take of 1 s or longer drifts more than 20 ppm from the frozen clock |
| timing | local lag moves more than 0.5 sample off the frozen delay |
| gain | the median local gain is more than 0.5 dB off the frozen gain |
| noise | the silence take's floor is above −70 dBFS |
| dropout | a 5 ms block is 20 dB below prediction where the prediction is at least −50 dBFS |
| stuck | 0.1 s or more of output where the reference is silent |
| residual | Σ(c − p)² / Σp² is above −20 dB after the calibration window |
| repeat | two takes of one command differ by more than −30 dB |

**What it cannot see.** R0 is dual-mono (`rtl-sketch/i2s_tx.v` sends one sample
on both channels), so a left/right swap is **unobservable**. The record reports
`channel-identity: UNOBSERVABLE` rather than PASS. A missing, inverted or
foreign channel *is* seen.

**Held notes.** On hardware, `uart_host` anchors the note-off to the observed
gate frame, a STATUS minus its round trip. Its planned hold can therefore
differ from the dry-run's by a few frames. The analysis reads that difference
from the host log. When it is not zero, it compares the held note up to the
release only and records `release_compared: false`. The stuck-note check still
covers the whole take.

**The limits are provisional** until the first real capture. If a clean-looking
rig fails the residual only because of the analog response (the DAC's
reconstruction filter and the interface's coupling), the fix is **not** a looser
limit or a per-take fit. The fix is a *declared, frozen* response measured once
from the calibration take, recorded as a criterion change (`r0-capture/2`).
Relaxing a tolerance to make a candidate pass is an operator decision
(plan087 §12).

## 9. Keep the evidence

- **Never edit, trim, normalise or re-export a take.** `analysis.json` records
  the SHA-256 of every input it read.
- The bundle is `session.json`, `detect.txt`, `program.txt`, `host/`, `takes/`
  and `analysis.json`. `*.wav` files are git-ignored. Zip the whole directory
  and attach it to #208, and commit `session.json`, `analysis.json`,
  `program.txt` and `host/` so the hashes are reviewable.
- Report the trial receipt path and its verdict on #208.

## 10. How the analysis was validated (before any hardware)

- **Known answers.** Every estimator is tested on a signal whose answer is known
  in closed form (`tools/test_r0_capture.py`): fractional delay, band-limited
  interpolation, clock and gain recovery, f0 of a sine and of a band-limited
  saw, clipping runs, and noise RMS.
- **Synthetic sessions.** These are built from the R0 reference through a known
  analog path: injected per-take delays, gain 0.30, +35 ppm clock, −100 dBFS
  noise and a 5 Hz AC-coupling high-pass.
  - The clean session PASSes, with the delay, gain and clock recovered.
  - Each defect FAILs for its own property: `timing` for a 24-sample slip,
    `gain` for +3 dB, `clock` for 300 ppm, `noise` for −45 dBFS, `dropout` for
    20 ms of zeros, `clipping`, `stuck` for a note that never releases, `pitch`
    for +20 cents, and `routing` for a missing, inverted or foreign channel.
  - The swap is reported BLIND, as declared.
  - `python tools/r0_capture.py controls --out build/r0-controls` prints the
    properties × defects matrix (docs/verification-rules.md rule 4).
- **Start red.** Against a stub analyser that answers PASS, no defect is caught
  (`--analyser stub`).

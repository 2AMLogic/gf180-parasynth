# Arty A7-100T first audio

The bring-up target is **Arty A7-100T, XC7A100T-CSG324-1**, with an Adafruit
PCM5102 breakout (#6250). IcePi capacity work is parked. The ULX3S bitstream
cannot program this board; this is a separate Xilinx port.

## What is implemented and measured

**Vivado 2025.1 produced a routed bitstream and passing internal timing.**
The [published build](reports/arty/vivado-2025.1/publication.json) binds the
bitstream to its source inputs, digital verification and implementation reports.
External I/O timing, DSP feedback warnings and physical playback remain under
review; this is not yet a qualified hardware audio result.

The fixed first-playback configuration is `OSC2X=1 FILTER2X=1 PULSE2X=0`.
It uses the selected reconstructed filter and near-47.9% pulse, addressed by
the existing `pulse29` register label. Pulse 2x remains a separate upgrade;
this baseline build does not establish its Artix fit.

| Routed implementation | Result |
|---|---|
| Slice LUTs | 12,695 / 63,400 (20.02%) |
| Registers | 12,171 / 126,800 (9.60%) |
| DSP blocks | 100 / 240 (41.67%) |
| Core clock | 12.288 MHz; reported period 81.380 ns |
| Final setup / hold slack | +46.498 ns / +0.050 ns; zero failing endpoints |
| Internal timing coverage | zero unclocked or unconstrained internal endpoints |
| External timing | seven outputs without delay constraints; unqualified |
| DRC | 268 warnings, including 13 DPREG-4 DSP feedback warnings; zero errors or critical warnings |

The [bitstream](reports/arty/vivado-2025.1/arty.bit) is 3,825,912 bytes,
SHA-256 `fe6c8d7e2349c45dcfb99cbbb7696c2f7ab5fdb5bcc5ada1bccdbb59586c7439`.
It was built from `602f7a09adb33d36b1fbc82de00f3e10d0890498` in 413 seconds.
Final timing comes from `timing.rpt`, not the router's intermediate estimate.
All warning classes remain recorded. Review DPREG-4 feedback behavior before
claiming implementation correctness; the RTL simulation below does not model
the mapped DSP primitives. Report host headers are omitted for privacy, with
both original and published report hashes retained.

| Digital check | Result |
|---|---|
| Arty wrapper SPI writes | 67/67 delivered, zero frame-prediction mismatches |
| Decoded I2S vs integer model | 4,821 periods match; both channels agree |
| Deadline | latest sample cycle 175/256; no overrun, overflow or missing sample |
| Muted-output starting stub | 4,527 wire mismatches; zero internal-core mismatches |
| MOSI forced low | all 67 intended writes mismatch |
| I2S data forced low | 4,527 wire mismatches; zero internal-core mismatches |

The clean run must pass before a mutation can count as caught. Compilation
failure, unavailable simulator and unrelated failures do not count. Reports
and transcripts are in [reports/arty](reports/arty). The preserved starting
stub is [stubs/arty_a7_silent.v](stubs/arty_a7_silent.v). Its report records the
original wrapper pathname; its hash matches the preserved stub, not today's
wrapper. All reports retain their original transcript hashes.

This is a short four-event stimulus, not a complete envelope/phrase result.
It bypasses the MMCM and supplies the core clock directly. The reset test
checks button assertion, delayed release and loss/recovery of lock; it does
not model MMCM analog behavior. Hardware synthesis explicitly forces
`SIM_NO_MMCM=0`.

Wrong-then-right accounting: three setup errors were found and corrected: a
Verilog declaration-order error before the numerical start-red run, and an
incomplete Vivado snapshot that omitted the voice lookup tables, and the first
real build refusing our misplaced Verilog-define option. A regression
test reproduces the missing-ROM failure using filenames from the actual HDL.
The [failed first build](reports/arty/first-vivado-attempt.json) retains the
exact Tcl error and repair.
Fresh digital evidence hashes all six ROM inputs. The compiler error was not
credited as a detected defect. The three intentional broken
configurations above all failed numerically; there was no accepted sound
measurement in this porting work.

## Wiring

The pin map comes from Digilent's
[Arty A7-100 master XDC](https://github.com/Digilent/digilent-xdc/blob/00a3404901f35aa9567b01ecb3f2c233b6efe9f4/Arty-A7-100-Master.xdc)
(Rev D/E) and [schematic](https://digilent.com/reference/_media/reference/programmable-logic/arty-a7/arty_a7_sch.pdf).
Check the board's model and revision before applying this map. Pmod numbers
below mean **connector positions**, not FPGA package pins.

| Arty position | FPGA pin | Connection |
|---|---|---|
| JA1 | G13 | DAC BCLK |
| JA2 | B11 | DAC WSEL / LRCLK |
| JA3 | A11 | DAC DIN |
| JA5 or JA11 | ground | DAC GND |
| JA6 or JA12 | 3.3 V | DAC VIN |
| JB1 | E15 | external controller SCK |
| JB2 | E16 | external controller MOSI |
| JB3 | D15 | external controller MISO |
| JB4 | C15 | external controller CS_N |
| JB5 or JB11 | ground | controller ground |

Use 3.3 V logic and a common ground. Power off while wiring. The
[Adafruit breakout](https://www.adafruit.com/product/6250) accepts 3–5 V supply
and 3.3 V data. Its default I2S configuration needs BCLK, WSEL and DIN, with no
separate master clock. Its jack is **line output**, not a headphone driver.
Connect it through a stereo TRS-to-two-mono cable to two line inputs on the
recording interface; leave automatic gain and effects off.

The expected nominal rates are 48 kHz LRCLK and 3.072 MHz BCLK, with 32-bit
slots carrying 16-bit samples. The 100 MHz E3 oscillator feeds an MMCM:
`100 / 5 * 48 / 78.125 = 12.288 MHz`; VCO is 960 MHz. This arithmetic is
checked independently of the HDL model and confirmed by Vivado's generated
clock report. Jitter and DAC setup/hold still require external timing review
and physical measurement. The XDC deliberately leaves external output delays
unqualified rather than inventing a DAC timing guarantee.

BTN0 (D9) resets the design. LEDs 0/1 show clock lock/reset release, LED2 is a
heartbeat, and LED3 carries LRCLK. These lights do not prove correct audio.

## USB and timed controls

Arty's USB programming/UART connector is now a control link. The reserved
pins are in use: **A9 is FPGA RX** (`uart_rxd`, the FTDI's TX) and **D10 is
FPGA TX** (`uart_txd`, the FTDI's RX), 3.3 V, pulled up. The bridge
(`rtl-sketch/uart_bridge.v`, instantiated by the wrapper through `synth_top`'s
`WITH_UART=1`) feeds the SAME register-write port the SPI slave drains, in the
two window cycles after the SPI drain's last possible write — the SPI path
keeps priority by construction and the two sources cannot collide.

THE WIRE. 115200 8N1 (the `UART_BAUD` parameter accepts any baud with
`CLK_HZ/BAUD >= 16`). Packets are `opcode payload... checksum`, checksum =
two's complement of the byte sum, so a corrupted or shortened packet is
rejected whole and reported, never half-applied. Write payloads are the
`spi_host.py` framing's six bytes, MSB first: `{F, 6'b0, SEC, A[7:0],
D[31:0]}`. Opcodes: `W` write-now (live path), `E` scheduled write (16-bit
due frame, LSB first — the device fires it from its own event queue),
`Q` status, `X` abort queues. The device answers on TX: `BOOT` 0xA5 after any
reset, `ACK`, `ERR` (overflow, late due, resync, bad checksum, bad opcode —
a dropped event is reported with its register address, never silently), and
`STATUS`.

THE CONTRACT (host side: `fpga/uart_host.py`; device side: the RTL — the
bench `fpga/verify_uart_bridge.py` checks the RTL against the host module):

* accepted commands are ordered; each queue is FIFO, never reordered;
* the UART path gets 2 register-write slots per frame, due-scheduled events
  before live writes;
* a scheduled event accepted with `due >= frame+1` fires in EXACTLY its due
  frame — the deadline the SPI host could only predict;
* a live write applies at acceptance+1, ±1 from window-phase quantisation;
  a note-off is a live write and is never blocked by a full event queue;
* queues: 64 scheduled events, 8 live writes; overflow drops the arriving
  packet, counts it, and REPORTS it;
* BTN0 reset or clock unlock clears both queues and counters — a queued
  phrase dies with the reset, and the host SEES the reset (BOOT byte, empty
  STATUS queues, frame counter restart). After a reset the host re-reads
  STATUS and re-anchors; its schedules are device-frame-relative from there.

ONE-COMMAND PLAYBACK. From a host with pyserial installed:

```text
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45
```

`run` loads the patch image, starts the note, holds it, releases it and
replays the scripted phrase — all as device-scheduled events, no host-side
sleeps in the timing path (host sleeps pace BYTES onto the link; the musical
deadlines are enforced by the device). Subcommands: `load`, `note-on`,
`note-off`, `play`, `run`, `status`, `abort`. `--dry-run` renders the exact
byte schedule and landing frames from the device contract without hardware.
Without pyserial, or without a port or a STATUS answer, the tool REFUSES
(exit 2).

QUALIFICATION. Digital only, so far: `fpga/verify_uart_bridge.py` runs the
real wrapper at the pins — 7 scenarios (held note to envelope floor, the
scripted phrase, queue overflow, note-off during queue-full, reset
mid-phrase, dropped byte, corrupted byte) all bit-exact against the integer
model with exact device-frame timing, plus 5 injected-bug controls each
demonstrated to turn the bench red. The evidence lives in
[reports/arty/uart-clean](reports/arty/uart-clean). No physical playback has
been attempted. The UART-bridge bitstream is PREPARED but NOT built: this
box's Vivado 2025.1 runner is stopped (see Remote build status), so the build
inputs, Tcl script and validated wrapper evidence stand ready in
`build/arty-prepared` / [reports/arty/uart-clean](reports/arty/uart-clean);
running `fpga/build_arty.py --verification
fpga/reports/arty/uart-clean/verification.json` on a Vivado host produces the
bitstream, labelled BUILT_REQUIRES_TIMING_REVIEW like every build here. The
published baseline bitstream above predates the bridge and does not contain
it. The SPI path on the modified wrapper is re-verified unchanged
([reports/arty/spi-smoke-uart](reports/arty/spi-smoke-uart): 67/67 writes,
4,821 I2S periods bit-exact). External I/O timing is unqualified.

First exercise a held note, then the complete scripted phrase, then drums
and simultaneous voice — on hardware, decode/record what actually leaves
the device and compare against the same named configuration, preserving raw
gain and timing.

## Build and verify

On a checkout with Python, numpy, scipy, pytest and Icarus Verilog:

```text
python -m pytest fpga/test_build_arty.py fpga/test_publish_arty.py -q
python fpga/verify_arty_controls.py
python fpga/build_arty.py --prepare-only
```

The build preparer checks clean numerical evidence and source/ROM/transcript
hashes, then snapshots the actual build inputs. Missing or changed evidence
refuses the build. The CI workflow repeats the wrapper checks and preparation.

On x86-64 Linux with Vivado available on PATH:

```text
python fpga/build_arty.py
```

To reuse the committed digital evidence on that host, without rerunning the
same unchanged RTL smoke:

```text
python fpga/build_arty.py --verification fpga/reports/arty/clean/verification.json
```

This produces a batch Tcl script, tool log, input hashes, utilization, clock,
DRC and timing reports, checkpoints and `arty.bit`. Missing Vivado returns
`REFUSED` (exit 2). A nonzero tool exit or absent fresh artifact fails. Even
successful execution is labelled `BUILT_REQUIRES_TIMING_REVIEW`, not a timing
or playback pass. Review the MMCM clock, unconstrained endpoints and any setup,
hold or DRC failures before programming. Preserve the final bitstream hash.

Publish a successful build into an empty directory:

```text
python fpga/publish_arty.py build/arty --out build/arty-publication
```

The publisher requires matching source, verification and artifact hashes,
checks the actual routed reports, and refuses missing reports, timing failures,
wrong clocks, resource overflow and DRC errors. Tests mutate the real report
fixture to demonstrate those refusals. Its successful state is
`BUILT_INTERNAL_TIMING_PASS_REVIEW_REQUIRED`; it retains the separate,
unqualified external-timing, DSP-review and physical-playback fields.

For first programming from the Mac, the upstream
[openFPGALoader board database](https://github.com/trabucayre/openFPGALoader/blob/master/src/board.hpp)
names this board `arty_a7_100t`. Use that exact identifier (`arty` is the
35T alias). After the bitstream and wiring are reviewed:

```text
brew install openfpgaloader
openFPGALoader --list-boards
openFPGALoader -b arty_a7_100t --detect
openFPGALoader -b arty_a7_100t fpga/reports/arty/vivado-2025.1/arty.bit
```

The final command loads volatile FPGA configuration; the board returns to
its flash image at power cycle. openFPGALoader 1.1.1 is installed on the Mac and its board entry is verified.
Physical programming has not been tested because the board has not arrived. Vivado Hardware Manager on a supported
USB-connected host is another programming route.

## Remote build status

The official AMD Vivado 2025.1 Marketplace subscription is enabled. An
8-vCPU/32-GB Ubuntu 22.04 runner completed the build using Vivado
2025.1, SW Build 6140274. Its catalog recognizes `xc7a100tcsg324-1`, and
synthesis successfully checked out the device license. The artifacts have
been collected and AWS confirms the runner is **stopped**. Its disk is retained
for future builds; storage charges continue. The idle guard remains set to
120 minutes for subsequent runs.
Private cloud/access details stay in local operator state, outside git.

The earlier empty Ubuntu fallback was terminated. The initial readiness probe
returned before the new guest accepted SSH; bounded retries succeeded without
changing ingress or credentials. This is tracked in
[Repo Remote #449](https://github.com/rjwalters/repo/issues/449).

The first real build refused `read_verilog -define` outside compile-unit mode.
The script now passes both macros explicitly to `synth_design`, following
[AMD UG904](https://docs.amd.com/r/2025.1-English/ug904-vivado-implementation/synth_design).
This was a build-script error, not an RTL sound change. The corrected retry
produced the published fit, timing and bitstream evidence above.

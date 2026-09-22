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

Since 2026-09-22 the publisher additionally binds, at publication time and
refusing drift (regression-tested in `fpga/test_publish_binding.py`):

- the digital verification record is **derived from the wrapper the build
  compiled** (`build.tcl` `-top` → `VERIFICATION_BY_WRAPPER`:
  `arty_a7_top` → `reports/arty/clean/verification.json`,
  `arty_a7_uart_top` → `reports/arty/uart-clean/verification.json`; an
  unlisted wrapper has no evidence and is refused), and the proof is
  hash-validated against the wrapper's compiled source set;
- the compiled `read_verilog`/`read_xdc` set must equal the build record's
  `source_sha256` minus ROM data files — an artifact whose build script
  reads sources the proof never covered is refused;
- the compiled XDC must carry **exactly** the approved external-I/O
  constraints (`ext_io_timing.py::xdc_contract_drift`), the routed
  report's output-delay exception list must be exactly
  `["i2s_bclk"]`, and UART ports (TX D10 / RX A9, upcoming in the
  integrated tree) may appear only with pin, constraint and — for TX — a
  recorded receiver/baud disposition (`uart_gate_drift`; inert until the
  ports exist).

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
| External timing | budgets derived and committed; proven against this routed checkpoint (below); publication regeneration pending build-host re-provisioning |
| DRC | 268 warnings, including 13 DPREG-4 DSP feedback warnings; zero errors or critical warnings |

The [bitstream](reports/arty/vivado-2025.1/arty.bit) is 3,825,912 bytes,
SHA-256 `fe6c8d7e2349c45dcfb99cbbb7696c2f7ab5fdb5bcc5ada1bccdbb59586c7439`.
It was built from `602f7a09adb33d36b1fbc82de00f3e10d0890498` in 413 seconds.
Final timing comes from `timing.rpt`, not the router's intermediate estimate.
All warning classes remain recorded. Review DPREG-4 feedback behavior before
claiming implementation correctness; the RTL simulation below does not model
the mapped DSP primitives. Report host headers are omitted for privacy, with
both original and published report hashes retained.

## External I/O timing

The seven outputs the routed baseline left unconstrained now carry committed
constraints ([arty-a7-100.xdc](boards/arty-a7-100.xdc)); every budget number is
derived in [ext_io_timing.py](ext_io_timing.py) with its citation, and the
constraint set was proven against the published routed checkpoint
([ext-io-checkpoint/analysis.json](reports/arty/ext-io-checkpoint/analysis.json)):

- **i2s_bclk (G13)** is a forwarded clock: BCLK is a bit of the core-clock
  frame counter (`i2s_tx.v`) = 12.288 MHz / 4 = 3.072 MHz, 50 % duty. Declared
  with `create_generated_clock -divide_by 4` sourced from the MMCM, so SDATA
  and LRCLK are analyzed as source-synchronous data against the clock that
  actually reaches the DAC. The DAC's clock-input requirements — TI PCM5102
  SLAS764B Table 7 p.13 (an earlier revision mis-cited the wrong identifier
  SLOS811 — withdrawn): tBCY ≥ 40 ns, tBCH/tBCL ≥ 16 ns, fBCK ≤
  24.576 MHz — are met with ≥ 146 ns of margin and checked arithmetically.
  A forwarded clock carries no output delay: adding one fails a
  self-referential hold check (measured WHS −1.021 ns), and Vivado classifies
  the port as "no output delay but with a timing clock defined on it" (LOW).
  That classification is the one permitted exception in the publication
  machinery, recorded as `output_delay_exceptions: ["i2s_bclk"]`.
- **i2s_sdata (A11), i2s_lrclk (B11)**: setup −max 8.200 ns = tDS/tLB 8 ns
  (SLAS764B Table 7 p.13) + 0.2 ns assumed flight imbalance. Hold is a skew budget:
  the RTL switches these outputs only on BCLK-falling cycles (`i2s_tx.v:48`),
  so the DAC's tDH/tBL is guaranteed by the half-period structure; `-min
  154.560` (= 162.760 half period − 8.200 tDH budget) makes STA verify
  clock-vs-data skew ≤ 154.56 ns (measured 1.4–4.9 ns). STA's worst
  launch/capture pair is one core period (81.380 ns) — pessimistic against
  the real half-BCLK window in the safe direction. Checkpoint slacks: setup
  +71.8/+72.5 ns, hold +153.2/+152.9 ns.
- **spi_miso (D15)**: the FPGA is the SPI slave (DR 0007 rev 2; mode 0,
  48-bit frames). MISO is re-driven in the core-clock domain through the same
  synchroniser that samples SCK (`spi_ctl.v:124-141`), so a bit leaves the
  FPGA 3–4 core clocks (244.1–325.5 ns) after the SCK falling edge, plus
  clock-to-out. A mode-0 controller samples on the SCK rising edge, so the
  guarantee is T_sck/2 ≥ 4·T_core + CO + flight + t_su. **At the 2.0 MHz write
  ceiling this is unsatisfiable for any clock-to-out: status readback is NOT
  qualified at the write rate.** Qualified readback rate: 1.4 MHz, with CO
  bounded to ≤ 26.422 ns by `-max 54.958` against the core clock (measured CO
  7.853 ns → max guaranteed readback 1.4768 MHz). Writes (MOSI/SCK/CS_N in
  through two-flop synchronisers) remain supported to the contract's 2.0 MHz.
  The generic virtual-SCK output-delay recipe does not fit this RTL: with an
  unrelated launch clock, Vivado analyzes an arbitrary core-to-SCK edge
  alignment and fails regardless of real margins.
- **led[1..3]** (reset release, heartbeat, LRCLK) are indicators with no
  synchronous receiver. No receiver-derived budget exists to quote, so the
  XDC applies a real, checkable constraint of one core period (any
  clock-to-out below 81.38 ns is invisible on a human timescale) instead of a
  false path. These are documented exceptions from receiver-derived
  budgeting, not silent suppressions. Checkpoint slacks: setup ≈ +69 ns,
  hold ≈ +3–4 ns.

**Assumptions (recorded, not measured):** jumper flight ~0.2 ns per net,
equal-length data/clock jumpers (short jumper wires on the Arty headers);
external controller setup 5 ns — **no guaranteed spec exists** for "a Mac
driving a Pmod jumper"; every SPI readback figure depends on that assumption.
Nominal rates (48 kHz LRCLK, 3.072 MHz BCLK) rest on the existing simulation
evidence and Vivado's clock report; no oscillator-error or signal-quality
claim is made from arithmetic.

A full rebuild with these constraints completed once on the build host
(Vivado 2025.1: WNS +14.199 ns — now bounded by the spi_miso output path —
WHS +0.032 ns, zero failing endpoints of 38,310, and the publisher produced
`external_io_timing_qualified: true` through its own machinery). The shared
build box was then re-imaged between calls (fresh boot, no `/tools/Xilinx`,
work directories wiped), deleting the generated publication and raw
checkpoint reports before they could be fetched; this document and the
committed code carry the captured numbers, and the bitstream/publication
regeneration reruns `python fpga/build_arty.py` +
`python fpga/publish_arty.py` unchanged once Vivado is available again.

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

Wrong-then-right accounting for the external-timing session: five things were
wrong before they were right, each caught by a control, a rehearsal or a
refusal rather than by inspection — (1) a test's own expected readback rate
was mis-derived (1.4851 MHz; recomputed 1.5118); (2) an experiment driver
referenced its Tcl by the wrong filename, and the Tcl then referenced its
XDCs by wrong names — both refused loudly instead of reporting data; (3)
`set_output_delay` silently dropped every constraint written with combined
`-max`/`-min` (Vivado Common 17-165), found only because the checkpoint
rehearsal re-listed the unconstrained ports; (4) the I2S hold constraint
`-min -8.2` produced two phantom hold violations (WHS −9.874 ns) from a
launch/capture pair the RTL never creates, replaced by the skew-budget form
after reading the failing paths; (5) a nominal output delay on the forwarded
i2s_bclk port failed a self-referential hold check, so the exception
classification — not a token constraint — is that port's disposition.

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

Arty's USB programming/UART connector does not directly provide this design's
SPI controller. The current wrapper accepts external SPI on JB. It does not
yet implement a USB-UART-to-timed-SPI bridge. Reserve the board UART pins:
**A9 is FPGA RX** (`UART_TXD_IN`); **D10 is FPGA TX** (`UART_RXD_OUT`).

The existing [spi_host.py](spi_host.py) supplies the six-byte, MSB-first,
mode-0 register framing and schedules. The bridge must execute queued events
on the device; host-side USB sleeps cannot guarantee audio-frame deadlines.
Preserve kick/tom coefficient writes and note-off events, report queue errors,
and test disconnect/reset behavior. `play.py` and the named preset renderers
produce model audio; they are not physical playback commands.

First exercise a held note, then the complete scripted phrase, then drums
and simultaneous voice. Decode/record what actually leaves the device and
compare against the same named configuration, preserving raw gain and timing.

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
`BUILT_INTERNAL_TIMING_PASS_REVIEW_REQUIRED`. `external_io_timing_qualified`
is computed from the routed report's own `check_timing` classification: it is
true only when no output port is unconstrained and none is excused by a false
path; ports carrying a timing clock (the forwarded i2s_bclk) are the one
permitted remainder and are recorded as `output_delay_exceptions` data, while
any remaining review items move to `remaining_review`.

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

2026-09-22: the runner came back **re-imaged** — fresh boot with no
`/tools/Xilinx` and no retained volume attached (`lsblk` shows only the
100 GB root device), while the "retained disk" note above is what the
external-I/O session relied on. One full build and publication with the
committed external constraints completed before the image swap; its numbers
are captured in the External I/O timing section. Regenerating the published
bitstream, reports and `publication.json` requires the operator to restore
Vivado 2025.1 (or attach the retained volume); no code change is needed, and
hand-editing the publication is not an option.

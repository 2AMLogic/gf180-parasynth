# Arty A7-100T first audio

The bring-up target is **Arty A7-100T, XC7A100T-CSG324-1**, with an Adafruit
PCM5102 breakout (#6250). IcePi capacity work is parked. The ULX3S bitstream
cannot program this board; this is a separate Xilinx port.

## What is implemented and measured

**R2 (pulse2x + the scheduling saving + the #354 ladder repair + #315) has its own
image**, built once from the settled sources (`3182638`) and published beside R0 and R1:
[r2-2025.1](reports/arty/r2-2025.1/publication.json), bitstream `167a6c7f...`, routed
checkpoint `e8fd3431...`, Vivado 2025.1 SW Build 6140274. WNS +14.700 ns, WHS +0.020 ns,
0 failing endpoints; external I/O qualified (`i2s_bclk` exception only; spi_miso readback
to 1.4601 MHz); DPREG-4 13/13 dismissed; the #315 UART-RX constraints bind and take effect
on this checkpoint; LUT 15,484, FF 13,490, DSP 124. Bound with the host's `--image r2`
bytes (equal to R1's), presets, RTL evidence and R1 as rollback by
[release/r2-2025.1.json](release/r2-2025.1.json) (T-RELEASE-BOUND-R2). pulse2x is in it by
operator override, with known limitations: [release/R2.md](release/R2.md).

**R1 (the player preview, contract revision 14) now has its own image**, built
once from the frozen R1 sources and published beside R0, not over it:
[r1-player-preview-2025.1](reports/arty/r1-player-preview-2025.1/publication.json),
bitstream `544499e2...`, routed checkpoint `0f81026e...`, Vivado 2025.1 SW Build
6140274. WNS +15.445 ns, WHS +0.036 ns, 0 failing endpoints; external I/O
qualified (`i2s_bclk` exception only); DPREG-4 13/13 dismissed on this
checkpoint; LUT 14,006, FF 13,485, DSP 100. Bound with the host's `--image tree`
bytes, presets and domain by [release/r1-2025.1.json](release/r1-2025.1.json)
(T-RELEASE-BOUND-R1); identity, per-port I/O timing and the default-image
switch in [release/R1.md](release/R1.md#the-r1-image-280-plan087-milestone-c).
The rest of this section describes **R0**, the integrated baseline, which
remains the published default and the named rollback.

**Vivado 2025.1 produced a routed bitstream and passing internal timing.**
The head of the bring-up state is the integrated baseline
([published build](reports/arty/integrated-baseline-2025.1/publication.json)):
the UART control bridge merged into the wrapper, the external-I/O
constraints shipped in the compiled XDC, and the DSP DPREG-4 disposition
re-bound to this image's routed checkpoint. The publication binds the
bitstream to its source inputs, digital verification and implementation
reports. Physical playback remains under review; this is not yet a
qualified hardware audio result.

**The published baseline predates per-oscillator drift and the clap's final
strike (contract revision 14), and only a real Vivado run can change that.** Contract 6.11 / DR 0019 added drift to
`rtl-sketch/voice_dp.v` (five datapath states, three 16-bit walk
accumulators, three deviations, a 10-bit decimation counter and register
`0x2D`). Every figure in the table below — LUTs, registers, DSPs, both slack
numbers, the DRC census, the DPREG-4 disposition and the bitstream hash —
describes the **pre-drift** netlist, and the publisher knows it: `publish()`
compares a build record's `source_sha256` against the live tree and would
refuse to re-publish this artifact. Refreshing it is not a document edit and
is not available in a sandbox. It needs, in order:

1. `python fpga/build_arty.py` on the x86-64 Linux build box with Vivado
   2025.1 on `PATH` — a real synthesis, place, route and bitstream from the
   current tree, bound to `reports/arty/rev14-clean/verification.json`;
2. `python tools/dsp_dpreg_extract.py` against the **new** `routed.dcp`, to
   re-derive the DPREG-4 DSP-feedback disposition for that image (the
   committed evidence is bound by digest to routed checkpoint
   `6c3c22c5…` and correctly refuses any other);
3. `python fpga/publish_arty.py`, then a fresh table here.

Until that happens, the digital evidence in this document is current with the
tree and **the routed/hardware evidence is not**. `python3
tools/check_arty_evidence_binding.py --list-historical` prints the split.

That split is by *compiled source*, and **the constraint file is in neither
comparison set by default** (#421). The digital bench drives no physical pin,
so the default `verification` scope is `sources() + roms()` — what
`build_arty.validate_verification` checks — and no verification record this
repository has produced hashes `boards/arty-a7-100.xdc` at all, nor ever will.
Ask the publication question explicitly:

```text
python3 tools/check_arty_evidence_binding.py --scope publication
```

Until #436 that could only **REFUSE** (exit 2, `NOT COVERED`): the bound record
never read those bytes, and "never hashed" is not the same finding as "hashed
and moved". It now exits 0, and **not by widening the digital record** — a
record claiming coverage of bytes its bench never opened is the failure
`docs/failure-modes.md` is about. The scope is answered by two records, each
covering only what its own bench read:

| record | bench | answers for |
|---|---|---|
| `reports/arty/rev14-clean/verification.json` | `fpga/verify_uart_bridge.py` | `sources() + roms()` |
| `reports/arty/xdc-binding/binding.json` | `fpga/verify_xdc_binding.py` | the XDC + the sources its names resolve against |

A file no bound record answers for REFUSES rather than passing unasked, so
narrowing a bench's read set cannot make this gate greener.
<!-- claim: test=tools/test_check_arty_evidence_binding.py::test_publication_scope_is_satisfied_against_the_current_tree -->
<!-- claim: test=tools/test_check_arty_evidence_binding.py::test_control_a_file_no_record_answers_for_refuses_rather_than_passing -->

The constraint record is a **binding** record, not an effect record: it says
the constraint file still refers to this wrapper — every `get_ports` naming a
real port, every hierarchical path joining a generate block with a dot and an
instance with a slash, every output delay equal to the budget
`ext_io_timing.py` derives from the PCM5102 datasheet. How many objects a query
*matched* still needs the netlist, and is `constraint_matches.rpt` (below).
Its start-red is the pre-#315 file that R0 and R1 were routed against, which
fails `hier_separators` on lines 42 and 46.
<!-- claim: test=fpga/test_verify_xdc_binding.py::test_red_first_the_constraints_r0_and_r1_shipped_fail_by_name -->

What the wider scope also answers is the published images — R1
(`r1-player-preview-2025.1`) was built on a branch that did not carry
`383f10b`, so **its only divergence from this tree is the XDC**, which the
default scope reports as no divergence at all.
<!-- claim: test=tools/test_check_arty_evidence_binding.py::test_the_newest_published_image_moved_only_its_constraints -->

Since 2026-09-22 the publisher additionally binds, at publication time and
refusing drift (regression-tested in `fpga/test_publish_binding.py`):

- the digital verification record is **derived from the wrapper the build
  compiled** (`build.tcl` `-top` → `VERIFICATION_BY_WRAPPER`:
  `arty_a7_top` — which, since the bridge merged, IS the UART wrapper —
  → `reports/arty/rev14-clean/verification.json`; an
  unlisted wrapper has no evidence and is refused), and the proof is
  hash-validated against the wrapper's compiled source set, so **the map
  moves whenever a compiled source changes** and superseded runs stay where
  they are (`reports/arty/clean` pre-uart, `reports/arty/uart-clean`
  pre-drift);
- the compiled `read_verilog`/`read_xdc` set must equal the build record's
  `source_sha256` minus ROM data files — an artifact whose build script
  reads sources the proof never covered is refused;
- the compiled XDC must carry **exactly** the approved external-I/O
  constraints (`ext_io_timing.py::xdc_contract_drift`), the routed
  report's output-delay exception list must be exactly
  `["i2s_bclk"]`, and the UART ports (TX D10 / RX A9, present in this
  wrapper) must appear with pin, constraint and — for TX — the recorded
  receiver/baud disposition (`uart_gate_drift`; ACTIVE since the bridge
  merged: the gate constants name the real `uart_txd`/`uart_rxd` ports,
  and the first integrated publication exercised them).

The fixed first-playback configuration is `OSC2X=1 FILTER2X=1 PULSE2X=0`.
It uses the selected reconstructed filter and near-47.9% pulse, addressed by
the existing `pulse29` register label. Pulse 2x remains a separate upgrade;
this baseline build does not establish its Artix fit.

| Routed implementation (integrated baseline) | Result |
|---|---|
| Slice LUTs | 13,226 / 63,400 (20.86%) — +531 over the pre-uart baseline: the bridge |
| Registers | 12,612 / 126,800 (9.95%) |
| DSP blocks | 100 / 240 (41.67%) |
| Core clock | 12.288 MHz; reported period 81.380 ns |
| Final setup / hold slack | +16.190 ns / +0.024 ns; zero failing endpoints |
| Internal timing coverage | zero unclocked or unconstrained internal endpoints |
| External timing | QUALIFIED at publication: every output budgeted except the forwarded i2s_bclk clock, recorded as `output_delay_exceptions: ["i2s_bclk"]` |
| DRC | DRC report (`drc.rpt`): 268 warnings (64 DPIP-1, 97 DPOP-1, 94 DPOP-2, 13 DPREG-4), zero errors or critical violations (the build log has two CRITICAL WARNINGs for the dropped UART-RX constraints, #315); the 13 DPREG-4 re-extracted and dismissed on THIS image (below) |

The [bitstream](reports/arty/integrated-baseline-2025.1/arty.bit) is
3,825,912 bytes, SHA-256
`a66c9349ef9b5572f3c3453777f38e1b143136755620fe419e730d6f5c84cb95`;
routed checkpoint SHA-256
`6c3c22c591671eb1f6790ac0500980f9660b8433ef8a1e235d3da2ee4a21fbf8`.
It was built in 420.7 seconds on Vivado 2025.1, its digital proof is the
UART-wrapper clean run (6,734 I2S periods, hash-validated against the
compiled source set), and the publisher set
`external_io_timing_qualified=true`. Final timing comes from `timing.rpt`,
not the router's intermediate estimate. Report host headers are omitted for
privacy, with both original and published report hashes retained.

Earlier images, kept as history with their hashes:

- `fe6c8d7e…` — [vivado-2025.1](reports/arty/vivado-2025.1): the first
  routed baseline, pre-UART, seven outputs unconstrained;
- `1a562b42…` — the same directory's regenerated publication with external
  I/O constraints (Vivado 2025.1 re-provisioned);
- `1d547016…` — [uart-bridge-2025.1](reports/arty/uart-bridge-2025.1): the
  UART-bridge bitstream, BUILT but never published as the baseline.

## DSP DPREG-4 disposition on this image

The three prior checkpoints are not this build, so the disposition was
re-derived: `tools/dsp_dpreg_extract.py` ran read-only against THIS routed
checkpoint (DCP hash asserted on the box before Vivado opened it) and THIS
drc.rpt, with the dumped cell set derived from that report at run time;
the hardened analyser (`tools/dsp_dpreg_analyse.py --evidence
reports/arty/integrated-baseline-2025.1/dsp-dpreg-evidence`) re-parsed the
required set from the new DRC and hash-bound the dump, manifest and names
file. Verdict: **13/13 flagged instances dismissed** — P-feedback
unreachable on every reachable OPMODE — with per-instance structural facts
(PREG/MREG/OPMODEREG, dynamic-net counts, reachable OPMODE value sets, P
fanout counts) identical to the dismissed structures of the earlier
checkpoint. The prior reasoning carries with new evidence hashes; the
record is
[dsp-opmode-analysis.json](reports/arty/integrated-baseline-2025.1/dsp-dpreg-evidence/dsp-opmode-analysis.json),
the full argument in the original
[disposition](reports/arty/vivado-2025.1/dsp-dpreg-disposition.md).
`dsp_feedback_review_complete` is DERIVED in the publication, never
flipped: `publish_arty.dsp_disposition()` refuses (recording the reason as
data in `dsp_disposition.reason` and in `remaining_review`) unless the
evidence set is present and manifest-valid, `routed_dcp.sha256` names the
**routed.dcp digest the publication names** (and the dump opened that
checkpoint), the accepted analysis covers exactly the DPREG-4 target set
derived from this artifact's `drc.rpt`, and a fresh
`dsp_dpreg_analyse.derive()` agrees with it and finds no reachable OPMODE
that selects P. `fpga/test_publish_arty.py` runs the true path and every
refusal through `publish()` in CI on a self-contained digest-bound fixture.
Publication ships the evidence bundle as `dsp-dpreg-evidence/` beside the
reports (every file hash-checked against the artifact after copying, digests
in `published_evidence_sha256`), and refuses to publish a true verdict the
published directory cannot reproduce on its own.

**This baseline's committed flag is `true`, and that is the derived
answer** (since 2026-09-25). The first extraction named the checkpoint by
path only, so until then the flag was derived `false`. A bounded read-only
re-extraction closed it: `tools/dsp_dpreg_extract.py` (which now records
the digest itself and has no defaults, so a stale target cannot be used)
asserted routed.dcp `6c3c22c5…` on the build host before Vivado 2025.1
opened it, wrote `dsp-dpreg-evidence/routed_dcp.sha256` into the box
manifest, and re-hashed the checkpoint after Vivado exited (unchanged; the
Tcl contains no write_checkpoint/opt/place/route). The new dump is
byte-identical to the path-only one (`e738fb0c…`). The artifact directory
(every file hash-matched against `report.json`) was then published through
`publish()` and the published directory reopened: `dsp_disposition()` on
it alone reproduces the published record exactly (13/13 dismissed, bound to
`6c3c22c5…`). The committed `publication.json` is that output, differing from
its predecessor only in the DSP fields and `published_evidence_sha256`, and
`publish_arty.py <dir> --rederive-dsp` is a fixed point on it. To repeat:
`python tools/dsp_dpreg_extract.py --box <host> --dcp <routed.dcp> \
--dcp-sha256 <digest> --drc-rpt <drc.rpt> --evidence <fresh dir>/dsp-dpreg-evidence`,
then `tools/dsp_dpreg_analyse.py --evidence …` and publish. If a checkpoint
no longer exists, its flag stays false until a re-implementation is
extracted.

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
- **uart_txd (D10), uart_rxd (A9)** — ACTIVE since the bridge merged; the
  gate constants name the wrapper's real port names (an earlier revision
  anticipated `uart_tx`/`uart_rx`, which would have left the gate silently
  inert against the actual ports). TX: core-clock launch into the FTDI's
  USB-UART bridge at 115200 8N1; no receiver-derived setup figure exists,
  so the budget is one core period as a real output delay (0.94 % of the
  8.681 µs bit period), with the receiver/baud disposition recorded as
  `UART_TX_DISPOSITION` in `ext_io_timing.py` — publication refuses when
  the port ships and the record does not. RX: two-flop synchroniser with
  `ASYNC_REG` on both stages and a false path into the first stage only,
  mirroring the SPI input pattern; the engine behind it stays timed.

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
checkpoint reports before they could be fetched. The integrated baseline
publication at the head of this document is that regeneration, produced by
the unchanged `python fpga/build_arty.py` +
`python fpga/publish_arty.py` once Vivado was restored (WNS +16.190 ns,
WHS +0.024 ns on this image).

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

## No-DAC demo output (sigma-delta on JD)

**A demo path, not a measurement path.** To *hear* the board without the
PCM5102 breakout, build `fpga/rtl/arty_a7_sd_top.v` instead of
`arty_a7_top.v` (#406). It is `arty_a7_top` unchanged, with the I2S output
still on JA. It also decodes that I2S wire back into samples
(`fpga/rtl/i2s_rx.v`) and drives a second-order, one-bit sigma-delta
modulator (`fpga/rtl/sd_dac.v`) at the 12.288 MHz core clock, which is
256x the 48 kHz frame rate. A resistor and a capacitor turn that stream into
audio. Recordings for #208 and every R0/R1 figure stay on the I2S/PCM5102 path.
An RC-filtered one-bit stream has a worse floor than the DAC, and its tone
depends on the driver's rise/fall symmetry and the supply. The digital SNR
below says nothing about either.

**Mono, on two pins.** The core's sample is mono by contract, because both I2S
slots carry the same word. One modulator therefore drives both JD1 and JD2.
Either pin alone carries the whole signal, and both pins give a stereo input
the same signal in each channel.

| Arty position | FPGA pin | Connection |
|---|---|---|
| JD1 | D4 | `sd_left`: RC filter, left (or mono) |
| JD2 | D3 | `sd_right`: RC filter, right (the same stream) |
| JD5 or JD11 | ground | filter and line-input ground |

Per channel, from the pin (JD's on-board 200 Ω series resistor is in addition
to R1):

```text
JD1 --[R1 1 kΩ]--+--[R2 1 kΩ]--+--[C3 10 µF +]--+--[R3 10 kΩ]--+---> line in (tip)
                 |             |                |              |
               [C1 10 nF]    [C2 10 nF]        (DC block)    [R4 4.7 kΩ]
                 |             |                               |
GND -------------+-------------+-------------------------------+---> line in (sleeve)
```

- **One RC stage** (R1/C1 only) is about 13 kHz with the 200 Ω, which is enough
  to hear the synth. **Two stages** (add R2/C2) are recommended: the modulator
  pushes its noise up into the MHz range, and a single pole leaves much of it
  in the signal. The line input's own anti-alias filter removes the rest.
- **Level.** Before the divider the swing is up to **3.3 V peak-to-peak on a
  1.65 V DC offset**. Full scale is 7/8 of that, about 2.9 V p-p, because the
  modulator scales its input by 7/8 for stability. C3 blocks the DC, with the
  + side toward the FPGA. R3/R4 divide by about 3, to roughly 0.9 V p-p, which
  is consumer line level. **Do not connect this straight to headphones or a
  headphone amplifier.** Feed powered speakers or a line input.
- Power off while wiring; share the ground.

**Pins and timing.** The pins are set in `fpga/boards/arty-a7-100-sd.xdc`,
which is read after the shared XDC. The shared XDC is not edited, because its
text is bound by hash to published evidence. A PDM stream into an RC filter
has no receiving clock, so each port gets an explicit `set_false_path -to`
with the reason written beside it, not a token output delay.

**Never published.** The release gate requires
`output_delay_exceptions == ["i2s_bclk"]` and binds evidence by wrapper
(`VERIFICATION_BY_WRAPPER`). #406 decided that this wrapper is never a release
image, rather than scoping the gate per wrapper. `publish_arty.py` refuses it
because it has no entry, and `fpga/test_arty_sd_top.py` asserts that absence.
<!-- claim: test=fpga/test_arty_sd_top.py::test_never_published_as_a_release_image -->
Its build gate is `fpga/build_arty_sd.py`. It requires internal timing to pass
(WNS ≥ 0, zero failing endpoints) and every XDC query to bind. The routed
report's output ports must be classified as exactly: nothing unconstrained,
`sd_left` and `sd_right` as user false paths, and `i2s_bclk` as the forwarded
clock. Before Vivado runs, it also binds two digital proofs to the live tree:
the core wrapper's proof, as `build_arty.py` does, and a PASS from
`fpga/verify_sd_dac.py`.

**Verification before hardware** (`python3 fpga/verify_sd_dac.py`, part of
`make verify`, about 15 s). The bench runs the chip's `i2s_tx` into `i2s_rx`
into `sd_dac`. The script decimates the one-bit stream with an exact integer
CIC (order 4, ÷64) and a Kaiser FIR low-pass (20.5 kHz, ÷4). It then compares
the result against the source file, zero-order held and put through the same
decimator. Before it reports any RTL figure, the estimator must read a
reference with a known −70 dB in-band tone as 70.0 ± 0.5 dB and reject a
full-scale 1.5 MHz tone. If either check fails, the run is REFUSED.

| Case | In-band SNR, RTL | Bound | 1st-order control |
|---|---|---|---|
| sine, −6 dBFS, ~1 kHz | 103.9 dB | ≥ 95 dB | red (~70 dB) |
| model held note (A2, fixed patch) | 99.5 dB | ≥ 90 dB | red (~62 dB) |
| full-scale square (overload) | 84.3 dB | ≥ 75 dB | red when the integrator clamp is removed |
| silence | exactly zero in-band error | ≤ −110 dBFS | red (−104.7 dBFS) when one LSB of DC is added to the loop input |

It also asserts the wire latency (324 core clocks from frame start to the
modulator), and four injected defects must each turn their cases red:
first-order quantisation, wrapping integrators, a receiver one bit early, and
one LSB of DC at the modulator's loop input. The last exists because the other
three cannot reach the silence case — a shifted receiver bit is the same bit on
a constant-zero sample, the clamp never engages near zero, and a first-order
quantiser still idles on a mean-zero pattern — so silence was the one case
measured with nothing able to prove the measurement could fail (#472).
Wrong-then-right, from making it: a float64 CIC read 72 dB for the held note.
At order 4 the integrator sums pass 2^53, and at orders 5 and 6 the same
estimator read the known −70 dB tone as 30 dB and −4 dB. The known-answer
check caught it, and the CIC is now exact integer arithmetic. A derived
latency of 323 was also wrong: it missed the output register, and the
measurement was 324.

**Built.** On 2026-09-28, Vivado 2025.1 built it in 242 s with state
`BUILT_DEMO_TIMING_PASS`. WNS was +15.085 ns and WHS +0.037 ns, with zero failing
endpoints and zero critical warnings, and the DRC census matched R0's. Every
port sits where `report_io` on the routed design says it should. The record and
bitstream (`95a4f92f…`) are in
[reports/arty/sd-demo-2025.1](reports/arty/sd-demo-2025.1/README.md): a build
record, not a publication.

**This image's JA is laid out for the purple PCM5102 breakout plugged straight
in.** The breakout's header (SCK BCK DIN LCK GND VIN) sits in JA's top row:
JA1 (G13) is driven low for SCK, BCK is on JA2 (B11), DIN on JA3 (A11), LCK
on JA4 (D12), GND on JA5 and VIN on JA6. The published R0/R1 images keep the
jumper layout in §Wiring (BCK on JA1, LCK on JA2, DIN on JA3), so **do not
plug the breakout straight into an R0/R1 image**. The power pins line up, so
nothing is damaged, but the signals are wrong. The breakout's back jumpers
must set XSMT high (unmuted) and FMT low (I2S).

**On the bench.** Rebuild with `python fpga/build_arty_sd.py` on a Vivado 2025.1
host. Load with `openFPGALoader -b arty_a7_100t
fpga/reports/arty/sd-demo-2025.1/arty.bit` (SRAM) or `-f` (flash). Then play with the same `fpga/uart_host.py run` that
#208 uses. The operator confirms by ear.

**Reuse.** `sd_dac.v` is PDK- and board-independent, and this file is its
**master**. A sibling synth takes a stamped copy (the file unmodified, plus
the commit it came from) rather than writing its own.

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

ONE-COMMAND PLAYBACK. From a host with pyserial installed. The FIRST
playback is the held note alone; the second adds the bench-proven smoke
phrase:

```text
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45 --fixture none
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45 --fixture m5a
```

**R1 player preview (the revision-14 tree; image published, #280:
[r1-player-preview-2025.1](reports/arty/r1-player-preview-2025.1), `544499e2...`).** Its
supported quick-start is the same commands with the named selection `--image r1` (#323), and without
`play --fixture m5a` (it sends no patch image, so what it plays depends on the
device's prior state; `run --note 45 --fixture m5a` plays the same phrase from
the known state). Every R1 image-loading command starts with voice RESET and
drum RESET, and refuses to start over queued events from an earlier session.
Identity and evidence: [fpga/release/R1.md](release/R1.md).

```text
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45 --fixture none --image r1
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --preset m5a-saw --note 72 --fixture none --image r1
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --preset m5a-pulse --note 72 --fixture none --image r1
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --note 45 --fixture m5a --image r1
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --fixture demo --image r1
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --fixture bar808-full --image r1
.venv/bin/python fpga/midi_session.py --port /dev/ttyUSB1 --midi-in /dev/snd/midiC1D0 --image r1
```

**R2 (pulse2x, the scheduling saving, the #354 ladder repair and #315; image built,
[r2-2025.1](reports/arty/r2-2025.1), `167a6c7f...`).** The same quick-start with
`--image r2`: it sends exactly R1's bytes (R2 has no kit or preset change), to a board
programmed with the R2 bitstream (`OSC2X=1 FILTER2X=1 PULSE2X=1`). pulse2x is in R2 by
**operator override**: five extreme rectangle patches at drive 1.6 are 1.6-4.8 dB darker
than R1, and R1 (`--image r1`) is the rollback. Identity, evidence and known limitations:
[fpga/release/R2.md](release/R2.md).

```text
openFPGALoader -b arty_a7_100t fpga/reports/arty/r2-2025.1/arty.bit
.venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX run --fixture demo --image r2
.venv/bin/python fpga/midi_session.py --port /dev/ttyUSB1 --midi-in /dev/snd/midiC1D0 --image r2
```

**Release r1, which plan087 calls R0** ([fpga/release/RELEASE.md](release/RELEASE.md)) binds this image,
these commands and their evidence in one manifest. It also enforces the
player-facing domain on every command: the final oscillator increments, the
glide transitions, the waveform sets and the drum-filter route. Until that
release, the note-only command above was **silent**. Its image carried no
mixer weights, and a replay of those bytes through the wrapper decodes an I2S
peak of 0. The note-only image now writes them.

**A SONG.** `fpga/play_song.py` plays a tune over the same link, with the same
device-scheduled events: the patch goes first as live writes, then every
note-on and note-off is stamped with its audio frame and fired by the FPGA.
Long songs roll through the 64-deep queue in batches. A note the patch cannot
play in the qualified range is refused, never re-pitched. Built-in songs are
`ode` and `twinkle`; `--notes "C3:1 E3:1 G3:2 R:1"` and `--song-file` take
any other. `--render out.wav` plays the same writes through the integer model,
so you can hear it without a board.

```text
python3 fpga/play_song.py --port /dev/cu.usbserial-XXXX --song ode
python3 fpga/play_song.py --song twinkle --render twinkle.wav
```

`run` loads the patch image, starts the note, holds it, releases it and —
with `--fixture m5a` — replays the scripted phrase, all as device-scheduled
events, no host-side sleeps in the timing path (host sleeps pace BYTES onto
the link; the musical deadlines are enforced by the device). The default
fixture is `none` (note only, always within budget).

MUSICAL-LENGTH PLAYBACK. `--fixture bar808-full` (two bars at tempo,
4.2 s) and `--fixture demo` (4.0 s) span three wraps of the 16-bit frame
counter, so no single schedule can hold them. The host delivers them in
ROLLING WINDOWS at the existing 115200 baud: the fixture's `load()` image
(patch, kit, initial accents: 182 writes) goes first as live setup, then
the timed writes (192 and 306 — every hit's accent, the BD attack/restore
pairs and tom pitch drops included) go in batches, each planned from a
fresh STATUS with every due inside the wrap-safe horizon and the queue
bounded against everything still in flight. Music t=0 lands 50 ms after
setup completes. Tempo is never stretched; an event the wire cannot reach
is REFUSED. `fpga/verify_rolling_playback.py` runs the shipped CLI against
the device contract on simulated time and checks every executed write
against the fixture itself: both patterns land every timed write on its
frame from counter epochs 0, 32000 and 65300, peak queue 55 and 60 of 64,
minimum deadline slack ~9000 frames; five injected controls (queue-unaware
cut, tag-based setup split, missing wrap unwrap, corrupted packet, reset
mid-phrase, watermark schedule sent unthrottled) each turn it red for their
reason. The host's ACTUAL bytes (STATUS polls included) were then replayed
through the UART RTL wrapper: `demo` 508/508 writes, 0 off-frame, 0 I2S
mismatches over 257,185 periods; `bar808-full` 394/394, 0, 0 over 266,695
periods, each with 1 s of decay tail, against the model driven by the
fixture's schedule; moving one intended event by one frame is caught.
Records: [reports/arty/rolling-playback](reports/arty/rolling-playback)
(`--rtl` is ~50 minutes a fixture). No physical playback yet.

`--fixture bar808` is the COMPRESSED regression fixture (rests removed,
157 ms of events), not the musical two bars. It is the stress case: once
its load image is setup rather than 182 events due at t=0 it preloads at
115200 (peak demand 36 of 64) and remains feasible down to 38400; at
19200 preflight refuses it with the packet index. Subcommands: `load`, `note-on`,
`note-off`, `play`, `run`, `status`, `abort`. `--dry-run` renders the exact
byte schedule and landing frames from the device contract without hardware.
Without pyserial, or without a port or a STATUS answer, the tool REFUSES
(exit 2).

QUALIFICATION. Digital only, so far: `fpga/verify_uart_bridge.py` runs the
real wrapper at the pins — 7 scenarios (held note to envelope floor, the
scripted phrase, queue overflow, note-off during queue-full, reset
mid-phrase, dropped byte, corrupted byte) all bit-exact against the integer
model with exact device-frame timing, plus 5 injected-bug controls each
demonstrated to turn the bench red. The controls are bench-logic controls
(parser, event queue, reset) and are unaffected by the voice datapath:
[reports/arty/uart-controls](reports/arty/uart-controls).

The clean run for the **current** tree is
[reports/arty/rev14-clean](reports/arty/rev14-clean): contract revision 14,
polyBLAMP (13) and the clap's final strike (14). Against its two parents,
[shark-blamp-clean](reports/arty/shark-blamp-clean) and
[l2-clean](reports/arty/l2-clean), only source hashes move. No measured field
or transcript byte moves, because the `phrase` scenario is a sawtooth phrase
that plays neither feature. Their pin-level evidence is their own benches, and
the clap's is in `docs/scorecard/clap-l2/`. Before those,
[reports/arty/drift-clean](reports/arty/drift-clean) — 87/87 writes
delivered, 6,734 I2S periods bit-exact, carried with its own start-red run
against `stubs/arty_a7_uart_stub.v` (`FAIL`, 0 of 26 writes). Drift moved one
number and no others: the same bench on a pristine `main` tree reproduces
[reports/arty/uart-clean](reports/arty/uart-clean) exactly, and between the
two trees the decoded I2S wire, the sampled audio, the TX capture and the
write log are **byte-identical** while `worst_strobe_cycle` goes 175 → 176 of
256 — the one unconditional `S_DA0` cycle per frame. No physical playback has
been attempted.

The integrated baseline bitstream above CONTAINS the bridge and is published:
Vivado 2025.1 routed it from the then-current tree with `fpga/build_arty.py
--verification fpga/reports/arty/uart-clean/verification.json`, and the
publication passed the publisher's active UART disposition gates. That
bitstream predates drift — see "the published baseline predates
per-oscillator drift" at the head of this document — and
`reports/arty/uart-clean` is never rewritten because it is the proof that
image cites by hash. The bridge-only build remains
as history in
[reports/arty/uart-bridge-2025.1](reports/arty/uart-bridge-2025.1)
(bitstream 3,825,912 bytes, SHA-256
`1d54701662149bd7118351dfbc78126e23ad0343a8da32d0a5a06eaf9cd204a5`, never
published as the baseline). The SPI path on the modified wrapper is
re-verified unchanged
([reports/arty/spi-smoke-uart](reports/arty/spi-smoke-uart): 67/67 writes,
4,821 I2S periods bit-exact).

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
python fpga/build_arty.py --verification fpga/reports/arty/rev14-clean/verification.json
```

(the preparer hash-checks the record against the live source set, so only a
run of the current tree binds: the pre-uart `clean/verification.json` misses
`rtl-sketch/uart_bridge.v`, and the pre-drift `uart-clean` and pre-L2
`drift-clean` records refuse with `verification source differs: <file>`. Ask before
the 39-minute suite does: `python3 tools/check_arty_evidence_binding.py`.)

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
openFPGALoader -b arty_a7_100t fpga/reports/arty/integrated-baseline-2025.1/arty.bit
```

The final command loads volatile FPGA configuration; the board returns to
its flash image at power cycle. openFPGALoader 1.1.1 is installed on the Mac and its board entry is verified.
Physical programming has not been tested yet: the board is on hand but not yet powered up (#208). The operator
procedure for programming R0 and recording it is [docs/capture-r0.md](../docs/capture-r0.md). Vivado Hardware Manager on a supported
USB-connected host is another programming route.

## Remote build status

**The Vivado host is documented once, in 2am's Arty note:**
[2am `hardware/arty-a7/README.md`, §Build remote, program local](https://github.com/2AMLogic/2am/blob/main/hardware/arty-a7/README.md#build-remote-program-local).
That note covers the instance and its `.env` (AMD Vivado ML 2025.1 AMI,
`m7a.2xlarge`, 60-minute idle stop), Vivado's install path, the moving
SSH-egress trap, and the `--ftdi-serial 210319C088B7` needed whenever the
CJMCU-2232HL is also plugged in. Update the facts there, not here. Its J8
pinout is measured, and its first-edge pin is **TMS**, not VREF.

**What is specific to this repository:**

- **Builds run through the checked build scripts**, never a bare
  `vivado -source`: `fpga/build_arty.py` for the release wrapper. It
  binds its digital proofs before Vivado runs and gates the routed reports
  afterwards.
- **Ship the tree as a `git bundle`** and run the script on the box inside
  a venv with numpy and scipy, with `settings64.sh` sourced. Copy results
  back with `tar` over ssh; `scp` with `{a,b}` braces does not expand on
  current OpenSSH.
- **Start and stop the box only with `repo-remote.sh`**, run from a
  directory whose basename is the box's name (`arty-blink`) and whose
  `.env` pins `REPO_REMOTE_INSTANCE_ID`. Stop it after fetching the
  artifacts, never before.

**History, kept because published evidence cites it:**

- The first real build refused `read_verilog -define` outside compile-unit
  mode. The script now passes both macros to `synth_design`, following
  [AMD UG904](https://docs.amd.com/r/2025.1-English/ug904-vivado-implementation/synth_design).
  This was a build-script error, not an RTL change.
- On 2026-09-22 the runner of that time came back re-imaged, without
  `/tools/Xilinx`. The external-I/O numbers above were captured before
  that. The readiness-probe race seen then is
  [Repo Remote #449](https://github.com/rjwalters/repo/issues/449).
- Superseded since 2026-09-27: the 2am note's box has Vivado 2025.1 at
  `/tools/Xilinx/2025.1`, and the note records a successful `blink` build
  there on 2026-09-27.

## Pads demo (#449)

Press a button on the Arty and hear an 808 voice. No host is needed. It is a
**separate image**: `fpga/rtl/arty_a7_pads_top.v` with
`fpga/boards/arty-a7-100-pads.xdc`, built by `fpga/build_arty_pads.py`.
`arty_a7_top.v`, `synth_top.v` and the release XDC are unchanged, so no
published evidence moves. It is never a release image.

| Control | Pin | What it does |
|---|---|---|
| BTN0 | D9 | BD |
| BTN1 | C9 | SD |
| BTN2 | B9 | CH (closed hat) |
| BTN3 | B8 | CP (clap) |
| SW0 | A8 | UART source. Down = host mode (the FTDI drives the bridge, as `arty_a7_top`). Up = pads mode. |
| SW3 | A10 | Reset. Flip it either way; only the motion counts. |
| LD4..LD7 | H5 J5 T9 T10 | clock locked, core out of reset, pads ready (kit loaded), a press accepted (~170 ms) |

The pins are cited line by line in the XDC from the Digilent master XDC this
file's Wiring section already uses. The button, switch and LED pins have
**not** been checked against a physical board.

**The DAC wiring is the direct-plug layout, not the Wiring section's jumper
layout.** `arty-a7-100-pads.xdc` follows `arty-a7-100-sd.xdc` (#408):
`JA1 dac_sck` held at 0, `JA2 BCK`, `JA3 DIN`, `JA4 LRCLK`, so a PCM5102
breakout whose header reads `SCK BCK DIN LCK GND VIN` plugs straight into JA's
top row. The jumper layout would leave JA4 unassigned and deliver no word
clock, which is silence with every digital check still passing -- measured on
this bench on 2026-09-28. The breakout's own control pins must also be tied:
**XSMT high** (it hard-mutes floating) and **FMT low** for I2S; see #460.

**How it works.** The wrapper sends the core the same UART packets
`fpga/uart_host.py` would send, on `synth_top`'s own `uart_rxd`. No core port
is added. When pads mode is entered, including after a reset with SW0 up, a
boot ROM replays R1's known-state preamble, R1's drum kit
(`fpga/release/r1-kit.json`) and the drum-bus gains as WRITE packets. After
that, each button press becomes that stop's `model/drums_fx.hit_writes`
program, sent as EVENT packets due `LAT` = 704 frames (14.7 ms) after the
press. 14.7 ms is the demo's press-to-sound latency. It is sized for the worst
case: all four buttons pressed in the same frame.

- **Debounce:** eager. The first synchronised edge is the press, then 5 ms
  lockout.
- **Retrigger:** a held button does not retrigger. A press on a voice whose
  last write is still pending is dropped.
- **BD timing:** BD's first frame carries four writes against the bridge's two
  write slots, so its trigger lands one frame after its attack retune. That
  is the only place an isolated hit departs from `hit_writes`' frames.

**The kit cannot drift.** `fpga/rtl/pads_rom.v` is generated by
`python3 fpga/pads_rom.py --write`. The check is `python3 fpga/pads_rom.py`,
which reports BOUND, STALE or REFUSED. It REFUSES unless the ROM's kit hashes
to R1's digest, meaning both `uart_host.R1_KIT_SHA256` and the `sha256` field
of `r1-kit.json`. The bench and the build each run this gate before anything
else.
<!-- claim: test=fpga/test_pads_rom.py::test_committed_rom_is_bound -->

**Reset moved from BTN0 to SW3 (a design choice awaiting sign-off).** BTN0 is
a pad now, so the reset became a toggle of SW3 rather than a switch level.
With a level, one switch position would hold the board in reset with nothing
to say so. With a toggle, the position means nothing and only the flip resets.
A long-press on a pad was the alternative. It was rejected because it makes a
held BD, which the bench covers, a reset hazard.

**Verification** (`python3 fpga/verify_pads_top.py`, iverilog, at the
wrapper's pins). The bench plays the buttons and switches, decodes the I2S
wire, and compares it bit for bit with `SynthTopModel` driven by `hit_writes`
at the frames the contract names. The expectation is never the design's own
report. The scenarios cover four frame phases per button, a held and
bouncing press, a four-button chord, a host-to-pads switch and a mid-hit
reset. Controls, run with `make controls`, must turn it red:

- a double-firing debounce;
- a trigger bit that is never dropped;
- a kit ROM whose hash differs from `r1-kit.json`;
- a stuck source switch.

`--start-red` runs the bench against a behaviour-free stub.

Measured on 2026-09-28. The evidence is in
[reports/arty/pads-bench](reports/arty/pads-bench).

| Scenario | Presses | Writes (contract frame) | I2S periods | Mismatches |
|---|---|---|---|---|
| phases | 23 | 245 / 245 | 15,611 | 0 |
| switch | 2 | 165 / 165 | 7,903 | 0 |
| reset | 3 | 310 / 310 | 13,118 | 0 |

All three scenarios have zero device ERRs and zero missed samples.

- **Start-red:** 9,593 mismatches and 0 writes.
- **Controls:** all four were caught for their recorded reasons. The
  double-fire made 7 extra writes, one extra BD hit. The stuck trigger made
  44 bad writes and 8,395 mismatches. The stuck switch lost all three host
  writes and put 162 writes out of place. The tampered kit was REFUSED before simulation.

**Wrong before right: two of seven results.**

1. The double-fire control first went red for the wrong reason.
   - The extra BD hit kept BD busy through the four-button chord, so the
     chord's own BD press was dropped. The write count came out equal.
   - The chord now starts after any busy window such a hit could open.
2. The reset scenario first failed on one "missed sample". This was a bench
   error: the frame a reset cuts short was being counted as a miss. That
   frame is now excluded, and audio frame 0 of every segment is still
   checked.

**Not yet done:** the Vivado build, which needs the build box, and the by-ear
check on the bench. `fpga/build_arty_pads.py --prepare-only` passes its gates
locally. The full build decides PASS only with WNS ≥ 0, WHS ≥ 0 and zero
failing endpoints, and checks the six human inputs on the routed design.
Each input must feed only its two-flop synchroniser and be a false path from
the port. The reason is in the XDC.

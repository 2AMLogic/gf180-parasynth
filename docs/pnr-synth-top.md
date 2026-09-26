# `synth_top` placed and routed — the whole chip, measured

Date: 2026-09-18. Machine: macOS 26.5.1 / Apple Silicon, Docker Desktop, `openroad/orfs:26Q3-296-gda37dce1c`
(`sha256:ebc8142d…`), OpenROAD `26Q3-1260-g06a5a02279`, Yosys `0.68+post`, running linux/amd64 under
emulation. Platform `gf180`, `TRACK_OPTION=7t` (`gf180mcu_fd_sc_mcu7t5v0`, site `GF018hv5v_mcu_sc7`,
5 metal layers), `CORNER=TC` = `tt_025C_5v00`, clock 81.38 ns (12.288 MHz). The liberty in this image
is the project's own PDK, `gf180mcuD` — `docs/pnr-first-run.md` section 1 establishes that and it is
not re-established here.

Everything below was measured by the runs in `pnr/orfs/evidence/synth_top/`. Nothing is estimated
unless the line says so.

**This is a physical result and nothing else.** A clean route says nothing about whether the chip
computes the right thing; `docs/verification-rules.md` rule 3, and this repository has quoted the
area of an all-X netlist three times. Function is `verify_synth_top.py`'s business, not this
document's.

---

## 1. The headline

`synth_top` had never been placed and routed. Every area figure this repository had for the complete
design was arithmetic on separately-measured cell areas. Two things came out of doing it:

1. **The `synth_top` that `docs/ARCHITECTURE.md` section 10 describes — with
   `drum_section_placeholder` — routes, cleanly, inside the wafer.space quarter slot.** Die
   1.7319 mm², **56.7 % utilisation achieved**, 2 detailed-route DRC violations, positive setup and
   hold slack at all three corners including `ss_125C_4v50`. Section 3.
2. **The `synth_top` that exists now — with the real `drum_kit` engine (`d1e5068`) — does not fit
   that slot at all.** Its standard cells are **1,979,380 µm²**: *larger than the entire 1.7319 mm²
   die*, a floorplan utilisation of **118.3 %**. Section 4.
3. **It does route, cleanly, in two quarter slots.** On a fixed 3.4650 mm² die it reaches
   **60.1 % utilisation with 0 detailed-route DRC violations**, 0 antenna violations, and positive
   setup and hold slack at all three corners (`ss_125C_4v50`: setup **+10.17 ns**, hold **+1.14 ns**).
   Section 4.1.

"It fits in a keychain" was true of the chip with a placeholder where the drums go. It is not true of
the chip with the drums in it: **that chip needs about twice the silicon that was budgeted.** The gap
is not subtle and it is not a routing effect — it is 1.05 mm² of extra standard cells, of which
**the drum section is 1.27 mm² and its control register file alone is 0.32 mm²** (section 5).

---

## 2. How the die area was fixed, and why that matters

This repository has one recorded self-inflicted wound on this exact point. A previous run reported
"cell area × 2.00 = die area" as a finding; its `par_request.json` said
`{"method": "utilization", "utilization_pct": 50}`, so dividing the cell area by 0.50 only recovered
the input. **If you set a target utilisation, the die area you get back is your assumption.**

So `CORE_UTILIZATION` is not set anywhere in `pnr/orfs/synth_top/config.mk`. `DIE_AREA` and
`CORE_AREA` are set to fixed rectangles and **the utilisation is the measured quantity**:

| floorplan | die | core | chosen because |
|---|---:|---:|---|
| `quarterslot` | 1314.88 × 1317.12 µm = **1.7319 mm²** | 1.6734 mm² | the wafer.space gf180mcu quarter slot inside the default pad ring, the 1.73 mm² premise `docs/area-budget.md` budgets against |
| `base` | 1860.88 × 1862.00 µm = **3.4650 mm²** | 3.3821 mm² | **two** quarter slots (2 × 1.7319 = 3.4637 mm², +0.04 % from site-grid rounding). Chosen by this flow, not by the product: the next whole multiple of the product's own unit that holds the joined design at a routable density |

Both are aspect ≈ 1 and land on the 7t site grid (0.56 µm × 3.92 µm), so the row structure is exact.
The die area in every row below is an **input**. The utilisation next to it is the **result**.

**That is now a check rather than a convention** (issue #245).
`pnr/orfs/area_provenance.py` reads the run's own `config.mk` or
`par_request.json` and `pnr/orfs/summarize.py` refuses — exit 2, no ratio printed
— when a utilisation target is set; the shipped `{"method": "utilization",
"utilization_pct": 50}` request above is one of its two permanent injections.
Writing the check found two runs nobody had classified: **`pnr/orfs/ladder_dp`
and `pnr/orfs/synth_core` both set `CORE_UTILIZATION = 50`**, so their die and
core areas are the cell area divided by 0.50 and `summarize.py` would have quoted
a die/cell ratio of about 2 for either. `docs/pnr-first-run.md` §1.2 does declare
that setting among the flow settings "that matter for reading the numbers", which
is why this is a latent trap rather than a published wrong number — but the tool
that prints the ratio did not know, and now it does.

---

## 3. The placeholder chip, routed — `2a88c35`

`rtl-sketch/{synth_top,spi_ctl,voice_dp,recip_div,ladder_dp_n,i2s_tx,modal_dp_rom,modal_coef_rom_p8}.v`
at `2a88c35`: `docs/ARCHITECTURE.md` section 10's file list, the design row G of
`docs/area-budget.md` measures. This is the first `synth_top` ever placed and routed.
Evidence: `pnr/orfs/evidence/synth_top/placeholder-2a88c35/`.

| stage | instances | std-cell area µm² | utilisation | setup WNS @tt |
|---|---:|---:|---:|---:|
| synth (yosys + ABC, stock `DONT_USE_CELLS = *_1`) | 28,810 | 925,387 | | |
| floorplan (fixed die) | 28,810 | 925,387 | 55.3 % | +53.26 ns |
| place (+ I/O buffers, resizing) | 30,209 | 917,291 | 54.8 % | +48.09 ns |
| CTS (+ clock tree) | 30,542 | 948,981 | 56.7 % | +48.04 ns |
| **finish** (count includes fillers/taps/endcaps) | 77,751 | **948,985** | **56.7 %** | **+46.39 ns** |

- **die 1,731,850 µm²** (1314.88 × 1317.12 µm) — *input*; **core 1,673,400 µm²** — *input*
- **final standard-cell area 948,985 µm² = 56.7 % of the core** — *measured*
- 2,925 flops, 21,216 multi-input combinational cells, 3,156 inverters, 255 clock buffers,
  260 timing-repair buffers, 1,992 tap cells, 660 endcaps, 1 antenna diode, 47,208 fillers
- routed wirelength **1,574,795 µm**; **detailed-route DRC violations: 2**; antenna-violating nets 0
- ORFS `fmax` 28.6 MHz, clock skew 0.236 ns, power 118.72 mW (ORFS's estimate with default switching
  activity: `power__total` = 0.1187 W — not a characterised number)

The 2 DRC violations are **one** site reported twice: a Metal2 spacing violation between net
`_16820_` and `VSS` at (1198.260, 1227.342)–(1198.540, 1227.610), a 0.28 × 0.268 µm box near the
top-right of the core (`5_route_drc.rpt`). It is not a class of failure spread over the die, and it
is also not zero — the two per-block runs in `docs/pnr-first-run.md` finished at 0.

### 3.1 Timing on the routed design, per corner

`pnr/orfs/sta-corners.tcl` on `6_final.odb`, each corner with its own OpenRCX extraction
(`typ`/`wst`/`bst` rules), propagated clocks. Clock period 81.38 ns.

| corner | setup WNS | setup TNS | hold WNS | hold TNS | implied min period |
|---|---:|---:|---:|---:|---:|
| `tt_025C_5v00` | **+46.391 ns** | 0.000 | **+0.572 ns** | 0.000 | 34.99 ns (28.6 MHz) |
| **`ss_125C_4v50`** | **+15.417 ns** | 0.000 | **+1.090 ns** | 0.000 | 65.96 ns (15.2 MHz) |
| `ff_n40C_5v50` | **+59.802 ns** | 0.000 | **+0.344 ns** | 0.000 | 21.58 ns (46.3 MHz) |

Setup closes with 19 % of the period to spare at the slow corner and hold is positive everywhere.
The "implied min period" is `period − WNS`, an estimate from one run and not a closure sweep.

### 3.2 Where the flops are, measured on the routed layout

ORFS flattens before ABC, so only cells carrying an RTL signal name keep their hierarchy — in
practice the registers. Combinational cells are renamed `_01234_` and cannot be attributed to a block
from the final netlist at all. So this table covers the flops and says so
(`pnr/orfs/blockarea.py`, run on `6_final.def`):

| block | flops | flop area µm² | % |
|---|---:|---:|---:|
| `u_voice` own (oscillators, mixer, ADSRs, ROMs, VCA, mix) | 1,514 | 103,040.5 | 51.7 |
| `u_voice.u_ladder` (`ladder_dp_n`, NCH = 2) | 530 | 36,078.1 | 18.1 |
| `u_drums.u_modal` (`modal_dp_rom`, 8 presets) | 385 | 26,199.7 | 13.2 |
| `u_spi` (`spi_ctl`) | 250 | 17,034.8 | 8.6 |
| `u_drums` (placeholder sources) | 87 | 5,920.5 | 3.0 |
| `u_voice.u_div` (`recip_div`) | 77 | 5,239.9 | 2.6 |
| `u_i2s` (`i2s_tx`) | 49 | 3,334.5 | 1.7 |
| `synth_top` own (cyc, frame, overrun, reset sync) | 33 | 2,289.6 | 1.1 |
| **total** | **2,925** | **199,137.6** | 100 |

The same table for the joined chip's routed layout (8,298 flops, 564,974.2 µm²):

| block | flops | flop area µm² | % |
|---|---:|---:|---:|
| `u_dregs` (`drum_regs`) | **2,276** | 154,884.5 | 27.4 |
| `u_drums.bank` (`modal_dp`) | 2,072 | 141,046.0 | 25.0 |
| `u_voice` own | 1,590 | 108,212.4 | 19.2 |
| `u_drums.src` (`drum_dp`) | 1,367 | 93,069.9 | 16.5 |
| `u_voice.u_ladder` | 530 | 36,078.1 | 6.4 |
| `u_spi` | 292 | 19,881.9 | 3.5 |
| `u_voice.u_div` | 77 | 5,239.9 | 0.9 |
| `u_i2s` | 49 | 3,334.5 | 0.6 |
| `synth_top` own | 45 | 3,226.9 | 0.6 |

`drum_regs` places **exactly 2,276 flops** — precisely the bit count
`docs/integration-area.md` section 4.1 censused from the register map. The bit count was right; only
the µm²/bit was low (section 5).

This is also the check that the run did the work it claims. `docs/ARCHITECTURE.md` section 10
independently measures 2,939 flops for this RTL under a different tool recipe; the routed layout has
2,925. **0.5 % apart on a number that a collapsed, all-X netlist could not produce at all** — the
`ladder_dp_t16` failure this repository records collapsed to 1,917 cells total. Yosys `check` reported
0 problems, and no `$readmemh` failed (the ROM tables are real).

---

## 4. The chip that exists now does not fit — `d1e5068`

`main` replaced `drum_section_placeholder` with the real engine while this run was going:
`drum_kit` (= `drum_dp` + `modal_dp`) plus `drum_regs`, its control image. `docs/integration-area.md`
section 4.2 said plainly that nobody had synthesised a `synth_top` containing `drum_kit` and that its
own joined figure was a projection. Here it is synthesised and floorplanned.
Evidence: `pnr/orfs/evidence/synth_top/quarterslot-does-not-fit/`.

Same flow, same stock `DONT_USE_CELLS = *_1`, same fixed quarter-slot die:

| | placeholder `2a88c35` | joined `d1e5068` | change |
|---|---:|---:|---:|
| instances (synthesis) | 28,810 | **59,289** | ×2.06 |
| standard-cell area | 925,387 µm² | **1,979,380 µm²** | **×2.14, +1,053,993 µm²** |
| sequential area | 199,050 µm² | **564,689 µm²** | ×2.84 |
| utilisation of the 1.6734 mm² core | 56.7 % routed | **118.285 %** | — |

**1.979 mm² of standard cells does not go into a 1.7319 mm² die.** There is no placement to report and
no routing: the flow stops at the floorplan because the cells are larger than the core they would sit
in. This is a measurement of the design, not of the flow — no utilisation target was set, and no
amount of placement effort changes it.

### 4.1 …and routes clean in two quarter slots

Same RTL, same flow, same stock `DONT_USE_CELLS`, on the fixed 3.4650 mm² die of section 2.
Evidence: `pnr/orfs/evidence/synth_top/joined-d1e5068/`.

| stage | instances | std-cell area µm² | utilisation | setup WNS @tt |
|---|---:|---:|---:|---:|
| synth | 59,289 | 1,979,380 | | |
| floorplan (fixed die) | 59,289 | 1,979,380 | 58.5 % | +50.69 ns |
| place (+ I/O buffers, resizing) | 61,120 | 1,950,350 | 57.7 % | +45.54 ns |
| CTS (+ clock tree) | 61,998 | 2,033,110 | 60.1 % | +45.26 ns |
| **finish** (count includes fillers/taps/endcaps) | 154,246 | **2,033,110** | **60.1 %** | **+43.59 ns** |

- **die 3,464,960 µm²** — *input*; **core 3,382,070 µm²** — *input*
- **final standard-cell area 2,033,110 µm² = 60.1 % of the core** — *measured*
- 8,298 flops, 38,419 multi-input combinational cells, 8,617 inverters, 689 clock buffers,
  609 timing-repair buffers, 4,239 taps, 938 endcaps, 92,248 fillers (1,348,960 µm²)
- routed wirelength **3,362,120 µm**; **detailed-route DRC violations: 0**; antenna-violating nets 0,
  antenna diodes 0; max-slew violations 0, **max-capacitance violations 2**
- clock skew 0.212 ns, ORFS `fmax` 26.5 MHz, power 143.54 mW (ORFS estimate, default activity)

| corner | setup WNS | setup TNS | hold WNS | hold TNS | implied min period |
|---|---:|---:|---:|---:|---:|
| `tt_025C_5v00` | **+43.592 ns** | 0.000 | **+0.600 ns** | 0.000 | 37.79 ns (26.5 MHz) |
| **`ss_125C_4v50`** | **+10.173 ns** | 0.000 | **+1.140 ns** | 0.000 | 71.21 ns (14.0 MHz) |
| `ff_n40C_5v50` | **+58.055 ns** | 0.000 | **+0.363 ns** | 0.000 | 23.32 ns (42.9 MHz) |

Setup still closes at the slow corner, with 12.5 % of the period to spare against the placeholder
chip's 19 %. The whole flow took 9,072 s; detailed routing was the long pole and it did not converge
quickly — **17,929 → 3,839 → 3,042 → 23 → 1 (held through seven ripup passes) → 2 → 0**
across the thirteen iterations the router reported (numbered 0th to 18th), about 2.5 h. Global placement's own routability pass had already reported the die
comfortable (0.26 % of tiles overflowed, weighted congestion 1.0000 against a 1.0100 target), so the
grind was pin access and shorts, not global congestion. `PLACE_DENSITY` was 0.69 against a 58.5 %
floorplan utilisation; a target nearer the utilisation was not tried and might converge faster.

### 4.2 What it costs in silicon, plainly

| | quarter slot (1.7319 mm²) | two quarter slots (3.4650 mm²) |
|---|---|---|
| placeholder chip `2a88c35` | **routes at 56.7 %**, 2 DRC | — |
| joined chip `d1e5068` | **118.3 % — will not place** | **routes at 60.1 %, 0 DRC** |

---

## 5. Where the area went, by block

Hierarchical synthesis of the joined chip (`FLOW_VARIANT=hier SYNTH_HIERARCHICAL=1`), same corner and
same cell policy, so module boundaries survive and every block can be seen. Keeping the hierarchy
costs 4.3 % of cross-boundary optimisation (2,063,852 hierarchical against 1,979,380 flat), which is
the price of the visibility and is the same effect `docs/area-budget.md` section 1 measures at 4 %.

| block | µm² | % of the hierarchical total | what it is |
|---|---:|---:|---|
| **`drum_kit`** | **956,352** | **46.3** | the drum engine: `drum_dp` + `modal_dp` |
|  `drum_dp` | 424,934 | 20.6 | 12 envelopes, 16 paths, one 25×16 multiplier, the LFSR, the buses |
|  `modal_dp` | 531,418 | 25.7 | the resonator bank at MODES = 12 with per-mode accumulated excitation |
| **`voice_dp`** | **719,604** | **34.9** | the Minimoog half |
|  `ladder_dp_n` (NCH = 2) | 196,499 | 9.5 | inside `voice_dp` |
|  `recip_div` | 23,741 | 1.2 | inside `voice_dp` |
|  `voice_dp` own | 499,364 | 24.2 | oscillators, mixer, ADSRs, cutoff ROMs, VCA, master mix |
| **`drum_regs`** | **318,300** | **15.4** | the drum control register file |
| `spi_ctl` | 54,138 | 2.6 | the link |
| `i2s_tx` | 9,490 | 0.5 | |
| `synth_top` own | 5,969 | 0.3 | frame counter, tick, go, reset sync |
| **total (hierarchical)** | **2,063,852** | 100 | flat: 1,979,380 |

Two things the block list settles.

**The drum section is 1,274,652 µm² — 62 % of the 2,063,852 µm² hierarchical chip** (`drum_kit`
956,352 + `drum_regs` 318,300). It is now much larger than the voice, and `modal_dp` alone (531,418 µm²) is
bigger than `voice_dp`'s own logic.

**`drum_regs` is real and it is 318,300 µm².** `docs/integration-area.md` section 4.1 called this out
as "~210,000 µm² of register file that exists in no area number in this repository" and derived it
from 2,276 bits × 92.20 µm²/bit. Synthesised, it is **318,300 µm² — 51 % more than that projection**.
The per-bit rule was checked against a strawman of the *mode* registers only; the envelope, path,
oscillator and accent storage evidently costs more per bit than the mode coefficients do. The
projection was labelled a projection and it was low.

---

## 6. `DONT_USE_CELLS`

ORFS's `gf180` platform sets `DONT_USE_CELLS = *_1`, so no ×1-drive cell is available to logic
mapping (the adder techmap still instantiates `addf_1`/`addh_1` directly). Every routed number in
sections 3 and 4 is under that stock policy — **the flow was not changed for them.** The cost of the
policy, measured on both chips by re-synthesising with `DONT_USE_CELLS=` (empty) and nothing else
different:

| chip | stock (`*_1` excluded) | `*_1` allowed | cost of the exclusion |
|---|---:|---:|---:|
| placeholder `2a88c35` | 925,387 µm² / 28,810 inst | 717,049 µm² / 30,539 inst | **+29.1 %** |
| joined `d1e5068` | 1,979,380 µm² / 59,289 inst | **1,510,153 µm²** | **+31.1 %** |

Both sit at the bottom of the 22–42 % band `docs/verification-rules.md` rule 3 states for this PDK,
and the joined chip confirms it at twice the size. Note the instance count goes *up* when `*_1` is
allowed and the area goes *down*: the mapper uses more, smaller cells.

**Allowing `*_1` does not rescue the quarter slot.** 1,510,153 µm² of cells in a 1,673,400 µm² core is
90.2 % utilisation before placement — no routable design sits there on five metal layers. The policy is
worth 469,227 µm² on the joined chip and 208,338 µm² on the placeholder one — real, and an order of
magnitude short of the 1,053,993 µm² the drum engine added.

`docs/integration-area.md` section 1 decomposes the placeholder chip's A/B/C numbers further; that
analysis was written from this run's own `phaseA`/`phaseC1` output and is not repeated here.

---

## 7. Against the block-sum extrapolation, honestly

`docs/ARCHITECTURE.md` section 10 gives two models for the placeholder chip, both from cell area:

| model | predicted core | routed core actually used | error |
|---|---:|---:|---:|
| 663,267 µm² of cells at 50 % utilisation | 1.33 mm² | **1.673 mm² at 56.7 %** | the prediction is **20 % low** |
| "routed core ≈ 2.8 × cells", calibrated on `ladder_dp` and `synth_core` | 1.86 mm² | **1.673 mm²** | the prediction is **11 % high** |

The two calibrations were offered as a bracket and the routed answer does land between them, nearer
the pessimistic end. Being precise about what each number is:

- the core area is a **fixed input** here (1.6734 mm²), so "1.673 mm² at 56.7 %" is not a die
  measurement — it is a utilisation measurement at a chosen die. Read it as: *the chip and everything
  the flow adds to it occupy 56.7 % of a quarter slot's core, and route there*;
- cell-area to routed-cell-area, which **is** an apples-to-apples ratio: 663,267 µm² (the yosys/klt
  recipe, `*_1` allowed) → 948,985 µm² of standard cells in the finished layout. **×1.43.** Of that,
  ×1.291 is the `DONT_USE_CELLS` policy and the remaining ×1.108 is the ORFS synthesis recipe plus
  everything place, resize and CTS added (+23,598 µm² of buffers and clock tree over synthesis, +2.6 %);
- the 47,208 filler cells that finish the rows are another 724,416 µm², and the taps and endcaps
  11,643 µm² on top — real silicon, absent from every cell-area model in this repository because the
  50 %-utilisation convention absorbs them silently.

So: **the extrapolation was not wildly wrong for the placeholder chip — it was 20 % optimistic.** The
number that broke the product story was not the routing overhead. It was the RTL changing: the real
drum engine is 2.14× the chip the extrapolation was made for.

---

## 8. What this does not say

- Nothing here is evidence that the chip works. No gate-level simulation of any routed netlist was
  run: the ORFS `gf180` platform ships no behavioural Verilog cell models and none is installed on
  this machine, so the netlist was checked structurally (yosys `check`: 0 problems; flop count within
  0.5 % of an independent measurement of the same RTL) and not dynamically.
- ORFS's `gf180` platform has **no KLayout sign-off DRC deck** (`make drc` answers "DRC not supported
  on this platform"). The DRC counts quoted are the detailed router's own, which is a real check of
  the routing but is not a sign-off DRC run. No LVS was run.
- Power figures are ORFS's estimate with default switching activity.
- IR drop was not analysed: `platforms/gf180/setRC.tcl` only sets via resistances at `CORNER=WC`, so
  `analyze_power_grid` aborts at TC (`docs/pnr-first-run.md` section 1.1 item 4).
- The joined chip's `*_1`-allowed number (1,510,153 µm², 63,667 instances) is a synthesis, not a
  route.
- The pad ring is not modelled. The 1.73 mm² premise is stated to be *inside* the default pad ring,
  and these dice are core+margin only.

---

## 9. Reproduce

```sh
cd pnr/orfs
./run-orfs.sh synth_top                                  # the joined chip on the 2-slot die
./run-orfs.sh synth_top FLOW_VARIANT=quarterslot \
    DIE_AREA='0 0 1314.88 1317.12' CORE_AREA='10.64 11.76 1304.24 1305.36' floorplan
./run-orfs.sh synth_top FLOW_VARIANT=x1 DONT_USE_CELLS= synth
./run-orfs.sh synth_top FLOW_VARIANT=hier SYNTH_HIERARCHICAL=1 synth
./run-orfs.sh synth_top run RUN_SCRIPT=$PWD/sta-corners.tcl RUN_LOG_NAME_STEM=sta_corners
python3 summarize.py synth_top base
python3 blockarea.py work/results/gf180/synth_top/base/6_final.def work/cells_7t.lef
```

`pnr/orfs/synth_top/config.mk` carries both floorplans in its header comment. Logs, reports and the
metric JSONs are committed under `pnr/orfs/evidence/synth_top/`; the 61 MB DEF and 52 MB GDS are not,
and regenerate from the config.

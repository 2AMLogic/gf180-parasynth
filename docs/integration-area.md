# What the integrated design actually costs

Two questions this repository had been arguing rather than measuring: how many
resonator/filter modes the drum section really occupies, and why `synth_top`
synthesised 39 % larger in ORFS than the same design measured in yosys. Both are
measured here. Every number says which flow produced it and whether it is a
**cell**, **synthesized**, **routed** or **FPGA** number, because this
repository has conflated those (`docs/verification-rules.md` rule 3).

**None of this is evidence of correctness.** A netlist all of whose outputs are
X has an area too, and one was quoted here three times. Area is area.

> **Every `synth_top` number in this document is of a chip with PLACEHOLDER
> DRUMS** (`rtl-sketch/synth_top.v:81` — `drum_section_placeholder`, not
> `drum_kit`). Contract item 17.23: both halves are verified and nothing
> verifies them joined. Section 2 gives the size of the gap; section 4 gives a
> projection for the joined design and labels it a projection.

---

## 1. The `DONT_USE_CELLS` gap: 79 % of it is drive strengths, and none of it
## is integration overhead

### The three numbers

| # | flow | area | instances | what it is |
|---|---|---:|---:|---|
| A | ORFS `synth`, gf180 7t TC, **stock `DONT_USE_CELLS`** (excludes `*_1`) | **925,387 µm²** | 28,810 | *synthesized cell* area |
| B | ORFS `synth`, same, **`DONT_USE_CELLS=`** (empty, `*_1` allowed) | **717,049 µm²** | 30,539 | *synthesized cell* area |
| C | yosys `synth` + `abc -liberty`, klt's recipe, no `dont_use` | **663,267 µm²** | — | *cell* area, `docs/area-budget.md` row G |
| C′ | same recipe, re-run in this branch | **656,275 µm²** | 29,235 | *cell* area, reproduction of C |

All three are the **same eight files** — `ARCHITECTURE.md` section 10's list,
the same set `pnr/orfs/synth_top/config.mk` gives ORFS — and the same RTL
commit. A and B come from `pnr/orfs/phaseA.out` and `phaseC1.out` in the
`flow/synth-top-par` run; C′ is this branch's reproduction of C, in
`rtl-sketch/build/area/repro_top_7t/`.

**C reproduces to 1.05 %** (656,275 against 663,267). The residual is a yosys
version difference — `docs/area-budget.md` records 0.69+62 and this is
0.69+post `143eb14f` — and 1 % on a re-run of the same script with a different
build of the same tool is the expected size of that effect. It is quoted here
rather than smoothed over: the decomposition below uses the published 663,267
so that it lines up with the document it is correcting, and the arithmetic
moves by 7 k µm² if C′ is used instead.

### The decomposition

```
925,387  A  ORFS, *_1 excluded
-208,338     drive-strength restriction   79.5 % of the gap
=717,049  B  ORFS, *_1 allowed
- 53,782     yosys-recipe vs ORFS-recipe  20.5 % of the gap
=663,267  C  klt-style yosys synthesis
```

**Allowing `*_1` cells buys 208,338 µm² — 22.5 % of the ORFS number**, or, read
the other way, excluding them costs **+29.1 %**. That is the bottom of the
22–42 % band `docs/verification-rules.md` already states for this PDK, so the
rule is confirmed on the full chip and not only on blocks.

**The residual 53,782 µm² is not integration overhead.** The framing this
measurement was asked to settle assumed C was a *block-sum extrapolation* and
that A − C was therefore the price of wiring blocks together. It is not: row G
of `docs/area-budget.md` says "measured in one synthesis", and it is a synthesis
of the whole integrated `synth_top`, not a sum of parts. A, B and C are three
measurements of one integrated design, so **the integration overhead in these
numbers is zero by construction** — there is no un-integrated baseline here to
subtract. What is left after the drive-strength effect is removed is the
difference between two synthesis recipes: ORFS's `synth.tcl` with
timing-driven ABC mapping against klt's `synth` + `abc -liberty`, which is
8.1 % on this design.

### Where the 208,338 µm² is

| | A (stock) | B (`*_1` allowed) | change |
|---|---:|---:|---:|
| sequential area | 199,050 µm² | 186,208 µm² | **−6.5 %** |
| flop count | 2,925 | 2,925 | **0** |
| combinational area | 726,337 µm² | 530,841 µm² | **−26.9 %** |
| combinational instances | 25,885 | 27,614 | **+6.7 %** |
| mean combinational cell | 28.06 µm² | 19.22 µm² | **−31.5 %** |
| distinct `*_1` cell types used | 2 | 34 | |

The flop count is identical, and 186,207.84 ÷ 63.66 = 2,925.0 exactly — the
`dffq_1` area from `docs/area-budget.md`. The sequential saving is the whole of
`dffq_2` → `dffq_1`, 68.03 → 63.66 µm², and nothing else. **Everything else is
combinational**: ABC given the `_1` strengths maps to *more* cells (+6.7 %) that
are *much* smaller (−31.5 % mean), which is the signature of a library whose
x1 and x2 variants differ by roughly a third in area at the same function.

### What it costs in timing

Floorplan-stage STA (pre-CTS, pre-route, estimated parasitics), TC corner,
81.38 ns period = 12.288 MHz:

| | worst slack | critical path | implied Fmax | margin at 12.288 MHz |
|---|---:|---:|---:|---:|
| A stock | +53.262 ns | 28.118 ns | 35.6 MHz | 2.89× |
| B `*_1` allowed | +52.086 ns | 29.294 ns | 34.1 MHz | 2.78× |

**Allowing `*_1` costs 1.18 ns of critical path — 4.2 % — and buys 22.5 % of
the area.** At this clock that trade is free: the design has 2.8× margin either
way, and 53 ns of positive slack is not a number anyone should be defending.
These are *synthesized/floorplan* numbers; a post-route figure will be worse and
is not in hand.

### The same A/B, run per block, on this branch

ORFS's A/B is one design in one flow. `fpga/scripts/x1_ab.sh` repeats it
locally on five designs, excluding the same 62 `*_1` cells from both
`dfflibmap` and `abc`. *Cell* area, gf180mcu 7t TC:

| design | `*_1` allowed | `*_1` excluded | penalty |
|---|---:|---:|---:|
| `synth_top` (placeholder drums) | 656,275 | 904,436 | **+37.8 %** |
| `drum_section_placeholder` | 158,770 | 221,685 | +39.6 % |
| `drum_kit` MODES=8 NUMS=6 | 519,777 | 702,569 | +35.2 % |
| `drum_kit` MODES=12 NUMS=6 (shipped) | 603,118 | 799,624 | +32.6 % |
| `drum_kit` MODES=16 NUMS=11 (complete 808) | 630,433 | 846,449 | +34.3 % |

Two cross-checks that had to pass before these were usable:

- `synth_top` with `*_1` allowed comes out at **656,274.8 µm², identical to
  C′** — the same number from a second, independently written script. 
- `synth_top` with `*_1` excluded comes out at 904,436 against ORFS's 925,387,
  **2.3 % apart**, which is the recipe difference already quantified as C′ → B.

**The penalty is 33–40 % across every block**, against ORFS's own 29.1 % on the
same design. The two disagree because ORFS maps with timing-driven ABC and this
script with plain `abc -liberty`; both are honest measurements of the penalty
*in their flow*, and neither is "the" number. Taken together the range for this
design in this PDK is **+29 % to +40 %**, which sits at the top of the 22–42 %
band `docs/verification-rules.md` states.

A third check, because a number that agrees too well is also a warning: the
extractor for this table selects the top module by its exact `\<name>` key.
An earlier version took `modules.values()[0]` and reported `drum_kit` at
325,388 µm² — which is `modal_dp`, a submodule, because yosys keys
parameterised modules as `$paramod$<hash>\<name>` and the top is not first.
That wrong number was caught by comparing it against the MODES sweep, not by
inspection. `fpga/scripts/x1_report.py` documents the trap in its docstring.

### The die area is an assumption in one of these flows and a measurement in the other

`pnr/orfs/synth_top/config.mk` deliberately does **not** set
`CORE_UTILIZATION`: the die is fixed at 1314.88 × 1317.12 µm = 1.7319 mm²
(the wafer.space quarter slot), the core at 1.6734 mm², and utilisation is what
comes out:

- A: 925,387 / 1,673,400 = **55.3 % utilisation — measured**, on a die that is a product input.
- B: 717,049 / 1,673,400 = **42.8 % utilisation — measured**, same fixed die.

`docs/area-budget.md` does the opposite everywhere, and says so: it divides cell
area by an assumed 50 % to get core area. Its "1.33 mm², 77 % of the slot" for
row G is 663,267 ÷ 0.50. **That is the assumption restated, not a measurement**,
and the earlier "cell area × 2.00 = die area" finding in this repository was
exactly that circle closing. The ORFS config gets this right and explains why
in its own comments; this note is here so the two documents are not read as
saying the same kind of thing.

### What was checked, because a surprisingly clean number is a warning

- The A and B netlists have the same 2,925 flops and the same top module with
  `Found and reported 0 problems`; B is not a different design.
- B's cell list contains 34 `*_1` types and A's contains 2, so the variable
  under test actually moved.
- The reproduction of C was grepped for constant-X assignments: **0**. Its
  yosys log carries 27 warnings, all of them ABC's routine "the network is
  combinational" and "detected multi-output cells" notices, and no
  `Can't open file` — the `$readmemh` tables really loaded. Netlist and log are
  committed at `rtl-sketch/build/area/repro_top_7t/`.
- C′'s netlist contains 2,896 `dffq_1`, against 2,925 flops in A and B. The
  three flows agree on the register count of this design to 1 %.

---

## 2. What `synth_top` does *not* contain

Both ORFS numbers are of a `synth_top` whose drum section is
`drum_section_placeholder` — eight trigger bits firing a decaying LFSR burst
into a **four-mode** `modal_dp_rom`. The real drum section, `drum_kit`
(`drum_dp` + a twelve-mode `modal_dp`), is on the `drums` branch and **is not
instantiated by `synth_top` on any branch in this repository**. So:

- 925,387 µm² is not "the whole instrument". It is the voice, the link, the
  I2S, and a placeholder where the 808 goes.
- The mode accounting in section 3 is therefore measured on `drum_kit`
  standalone, and says so, because there is no integrated design to measure it
  on yet. That is the finding, not a limitation of the measurement.

**How big is the gap?** Measured, both sides in the same flow:

| | `*_1` allowed | `*_1` excluded |
|---|---:|---:|
| `drum_section_placeholder` (what is in `synth_top`) | 158,770 | 221,685 |
| `drum_kit` MODES=12 NUMS=6 (what belongs there) | 603,118 | 799,624 |
| **swap delta** | **+444,348 µm²** | **+577,939 µm²** |
| ratio | 3.80× | 3.61× |

**One correction while this is in front of us.** The 89,004 µm² figure quoted
in `synth_top.v`'s own header is **not the placeholder's area** — it is
`docs/area-budget.md`'s `drum_src_seq` strawman, a *prediction of the real drum
sources*. The placeholder measures **158,770 µm²**, and the real `drum_dp`
measures **272,894 µm²**, so that strawman was low by **3.1×**. Any ratio
computed against 89,004 (7.3×, for instance) is comparing the real thing to a
withdrawn prediction rather than to what is in the design. The measured ratio
is **3.6–3.8×**; the delta, which is the number that matters, is
**0.44–0.58 mm² of cells**.

---

## 3. Mode accounting: the kit uses 11 of 12, the filter half is already full,
## and the real boundary is 16, not 12

### What the shipped kit actually occupies

Measured, not counted off the voice list: `fpga/scripts/mode_census.py` reads
the register image `model/drums_fx.py:kit_808()` writes and reports what lands
in the bank.

| mode | name | numerator | live |
|---:|---|---|:-:|
| 0 | `M_HATBP` | BP filter | ✓ |
| 1 | `M_OHHP` | HP filter | ✓ |
| 2 | `M_CHHP` | HP filter | ✓ |
| 3 | `M_SDN` | BP filter | ✓ |
| 4 | `M_CPBP` | BP filter | ✓ |
| 5 | `M_CBBP` | BP filter | ✓ |
| 6 | `M_BD` | RAW body | ✓ |
| 7 | `M_SDLO` | RAW body | ✓ |
| 8 | `M_SDHI` | RAW body | ✓ |
| 9 | `M_LT` | RAW body | ✓ |
| 10 | `M_HT` | RAW body | ✓ |
| 11 | `M_SPARE` | — | unconfigured |

**Eleven active modes — six filters and five bodies — for eight named stops**,
plus 12 of 12 envelopes, 14 of 16 paths and 6 of 6 oscillators. The DAG's
statement is confirmed: instrument names do not map one-to-one onto resonators.

**The sharper fact the DAG does not state: the filter half is already 100 %
full.** `modal_dp` gives a numerator only to modes below `NUMS`, and the kit is
instantiated at `NUMS = 6` using modes 0–5 as exactly those six filters. Mode
11, the spare, sits *above* `NUMS` and can therefore only ever be a RAW body.
So the bank is not "11 of 12 with one to spare" — it is **6 of 6 filters with
none to spare, and 5 of 6 bodies with one to spare.**

### What a mode costs — and the answer is a staircase, not a slope

gf180mcu 7t, `tt_025C_5v00`, **cell** area, klt's recipe
(`rtl-sketch/area/synth_area.py`, the same harness `docs/area-budget.md` uses).
`fpga/scripts/mode_sweep.sh` regenerates all of it.

| MODES | `modal_dp` cells | `modal_dp` µm² | flops | `drum_kit` µm² |
|---:|---:|---:|---:|---:|
| 4 | 8,640 | 190,166 | 646 | — |
| 8 | 10,548 | 241,979 | 1,039 | 519,777 |
| **9** | 12,531 | **321,331** | **1,656** | — |
| 11 | 13,256 | 329,730 | 1,698 | 607,142 |
| **12** | 13,187 | **325,976** | **1,656** | **603,118** |
| 14 | 13,233 | 326,925 | 1,656 | 606,843 |
| **16** | 13,271 | **329,713** | **1,656** | **607,052** |
| 17 (MW=5) | 19,811 | 505,245 | 3,101 | — |
| **18** (MW=5) | 20,002 | **516,142** | 3,143 | **792,164** |

**MODES = 9, 12, 14 and 16 all have exactly 1,656 flops.** That is not a
coincidence and it is not a broken measurement — it was checked for precisely
that, because a flat curve is the shape of a bug. The bank's state arrays
(`y1`, `y2`, `exc`, `h1`, `h2`, all `[0:MODES-1]`, all addressed by the
`MW`-bit `mode` counter) are mapped by yosys to a memory padded to a **power of
two words**. MODES 9–16 therefore all buy sixteen modes' worth of state, and
MODES 5–8 all buy eight.

The consequence is the useful part:

- **Going from 12 modes to 16 is free.** 603,118 → 607,052 µm² of `drum_kit`
  cells, +0.65 %, which is mapping noise. Four more resonators for nothing.
- **Going to 17 is a cliff.** `MW` must become 5, the state doubles to 32
  words, and `drum_kit` goes to 792,164 µm² — **+189,046 µm², +31 %**.
- **Going down to 8 saves 83,341 µm² (−14 %)**, and only because 8 is the next
  power of two down. Trimming 12 to 11, or to 9, saves nothing at all.

So **the 12-vs-8 mode question is the wrong question.** The choices this RTL
actually offers are **8, 16 or 32**. Twelve is simply a point inside the
sixteen-mode bracket that the design is already paying for.

#### The 17th mode is not the whole price of a 17th mode

The +189,046 µm² above is what `MODES 16 → 17` costs *the bank*. Anything that
actually wants a 17th mode is likely to want more than the bank, and a caller
citing this row alone will under-price its change. #102 proposed a 17th mode to
give the cymbal's Hh3 a third pole; `tools/probes/hihat/hh_probe5.py` found that
the mode was the smaller half of the bill:

- **a 24th `PATH`** (`N_PATH` 23 → 24), because a cascade needs its own path
  word. Not priced in this table at all;
- **18.1 dB of level**, thrown away by `TAP_SHIFT = 3`, which the destination
  mode's `amp` register **cannot** recover — `amp` is Q0.16 and saturates at
  1.0. A cascaded mode therefore also spends the path word's second envelope
  slot, and possibly the drive envelope's peak, on getting back to unity;
- **a forced cascade order**: `PATH.src` is 5 bits, so `SRC_TAP + m` reaches
  only `m ≤ 15`. **A 17th mode cannot be tapped.** Making it tappable is a
  contract change (`path` 25 → 26 bits), not a synthesis result.

**These rows were NOT re-measured for that work** — the structure lost on
fidelity first (DR 0022), so nothing was re-synthesised and the figures above
are still the 2026-09-18 measurement they always were. Re-verify them against
the tree you are pricing before quoting them for a change that does go ahead.

### Configuration storage — linear, and it does not exist

`drum_kit.v` takes `a1_bus`, `a2_bus`, `amp_bus`, `num_bus`, `env_ctl_bus`,
`env_peak_bus`, `env_rate_bus`, `path_bus`, `osc_inc_bus` and `accent_bus` as
**input ports**. `tb_drums.v` drives them from a file. **No module in
`rtl-sketch/` holds any of it**, so every drum-section area number in this
repository — including `docs/area-budget.md`'s — excludes the registers a chip
would have to add.

At MODES = 12 that is **2,276 bits**: 840 of mode configuration (70 per mode:
two 26-bit coefficients, a 16-bit level, a 2-bit numerator select), 804 of
envelopes, 352 of paths, 144 of oscillators and 136 of stops and accents.

`rtl-sketch/area/mode_cfg_regs.v` is a **strawman** — the smallest honest
stand-in for the mode part, one host write port and the buses back out,
labelled a strawman everywhere it is quoted. Measured:

| MODES | cells | µm² | flops |
|---:|---:|---:|---:|
| 4 | 912 | 27,023 | 264 |
| 8 | 1,852 | 52,685 | 528 |
| 12 | 2,535 | 77,300 | 792 |
| 16 | 3,549 | 103,802 | 1,056 |
| 18 | 3,832 | 116,247 | 1,188 |

Perfectly linear: **66 flops and ≈ 6,400 µm² per mode**, no staircase, because
registers are registers. It agrees with `docs/area-budget.md`'s own rule of
thumb — a written-under-enable state bit costs 92 µm² in this library, and
70 × 92 = 6,440.

**This inverts the per-mode cost picture.** Inside the 9–16 bracket the
resonator itself is free and *the configuration registers are the entire
marginal cost of a mode*: ~6,400 µm² each, about 1 % of the drum kit per mode.

### Correcting the DAG's arithmetic

`docs/capability-dag.md` says the 808 is 11–12 circuits, we hold 8, so a
complete kit is **+3 circuits** — and then says every mode-count claim must be
measured. Measured:

| missing circuit | what it needs in the bank |
|---|---|
| MC / MT mid conga or tom | **+1 body**, exactly like LT and HT |
| CL / RS claves or rim shot | **+1 body** (one bridged-T) |
| CY cymbal | **+2 band-pass filters** (3.45 kHz Q 6 and 7.1 kHz Q 6, `docs/tr808-reference.md` §10, which says outright that "the modal bank can host these"), **plus 2–3 high-passes** if its three bands are built the way CH and OH already are here — one shared band-pass tapped into per-band high-passes |

Counting it out:

| | modes | filters (`NUMS`) |
|---|---:|---:|
| shipped eight-stop kit | 11 | 6 |
| + MT body | 12 | 6 |
| + CL/RS body | 13 | 6 |
| + CY, band-passes only | 15 | 8 |
| + CY, band-passes + 1 high-pass | **16** | 9 |
| + CY, band-passes + 2 high-passes | 17 | 10 |
| + CY, band-passes + 3 high-passes (as CH/OH are built) | 18 | 11 |

So **+3 circuits is +4 to +7 modes**, taking the bank from 11 active to
**15–18** and `NUMS` from 6 to **8–11**. The DAG's closing line — that +3
circuits is "a far smaller ask than the '+5 to 7 modes' an earlier draft
guessed at" — is **wrong in mode terms**: the discarded guess of +5 to 7 modes
was very nearly right, and it was discarded for the wrong reason. Both
statements can be true at once because circuits and modes are different units,
which is exactly the DAG's own point applied to its own conclusion.

### What raising `NUMS` costs, measured separately

`NUMS` is a second dial and it had to be measured on its own, because every
row above moves both. At a fixed MODES = 16:

| MODES | NUMS | `modal_dp` µm² | flops | `drum_kit` µm² |
|---:|---:|---:|---:|---:|
| 16 | 6 | 329,713 | 1,656 | 607,052 |
| 16 | 11 | 350,653 | 1,866 | 630,433 |
| 16 | 16 | 377,037 | 2,076 | — |

Linear, and exactly what the RTL says it should be: **42 flops (the `h1`/`h2`
excitation-history pair, 21 bits each) and ≈ 4,190 µm² per numerator slot.**
No staircase — `h1` and `h2` are genuinely pruned above `NUMS`, so unlike the
state arrays this dial is a slope and you pay only for what you ask for.

### The cost of a complete 808, measured

Against the shipped kit (`drum_kit` at MODES = 12, NUMS = 6, 603,118 µm²):

| | bank | + configuration | **total added** | |
|---|---:|---:|---:|---|
| complete 808 at **16 modes / 11 filters** | +27,315 | +26,502 | **+53,817 µm²** | **+8.9 %** of the drum kit |
| complete 808 at **18 modes / 11 filters** | +189,046 | +38,947 | **+227,993 µm²** | **+37.8 %** |

(The 16-mode bank figure is measured at NUMS = 11, the pessimistic end; NUMS = 9
would be ~8,400 µm² less at the measured 4,190 µm² per slot. The configuration
column is the **measured** `mode_cfg_regs` strawman at 16 and 18 modes against
12 — 103,802 − 77,300 and 116,247 − 77,300. Section 4.1 instead projects
configuration from the 92.20 µm²-per-bit rule, which gives 25,816 for the same
280 bits: **686 µm², 1.3 %, less**. The two are not the same kind of number and
this document does not average them — the measured one is used where a
synthesis exists, the rule where none does.)

**The engineering decision this hands over is therefore narrow, specific, and
not about voice count:** thirteen modes are spoken for before the cymbal is
placed, so it is *whether the cymbal can be built in three bank modes — its two
band-passes plus at most one high-pass*. Three keeps a complete 808 inside the
sixteen-mode bank the design **already pays for**, for about +54,000 µm² —
under 6 % of `synth_top` — of which half is registers that do not exist yet.
Four or more falls off the `MW = 5` cliff and costs four times as much.

That is a question about CY's three Sallen-Key high-passes
(`docs/tr808-reference.md` §10 — Hh1 at 2.5 kHz on the low band, Hh2 and Hh3
both resonant near 10 kHz on the two high bands, which is why merging them is
the thing worth trying). It belongs to whoever implements the cymbal. It is not
a decision to take from an area budget, and this document does not take it.

### One more thing the DAG's table cannot see

Both ORFS numbers in section 1 are of a `synth_top` containing a **four-mode**
`modal_dp_rom` and a placeholder drum source. The real `drum_kit` at MODES = 12
is **603,118 µm² of cells on its own** — 65 % of the entire 925,387 µm²
`synth_top`, and larger than that whole design is on an FPGA (section 2 of
`docs/fpga-build.md`: 6,777 LUT4 for `drum_kit` against 5,885 for all of
`synth_top`). Integration is not a rounding error here. Nobody has measured the
two together, because no `synth_top` on any branch instantiates `drum_kit`.

---

## 4. The configuration storage nobody has counted, and a projection that is
## labelled a projection

### 4.1 The missing registers

`drum_kit.v` takes **every** control input as a port: `a1_bus`, `a2_bus`,
`amp_bus`, `num_bus`, `env_ctl_bus`, `env_peak_bus`, `env_rate_bus`,
`path_bus`, `osc_inc_bus`, `accent_bus`, `stops`. `tb_drums.v` drives them from
a file. **No module in `rtl-sketch/` holds any of it on any branch.** Every
drum-section area number in this repository therefore excludes the register
file a chip must add.

Counted from the contract's own register map (`fpga/scripts/mode_census.py`):

| what | bits at MODES=12 | bits at MODES=16 |
|---|---:|---:|
| mode coefficients, level, numerator (70/mode) | 840 | 1,120 |
| envelopes, 12 × (27 + 24 + 16) | 804 | 804 |
| paths, 16 × 22 | 352 | 352 |
| oscillators, 6 × 24 | 144 | 144 |
| stops + accents | 136 | 136 |
| **total** | **2,276** | **2,556** |

This library **has no enable flop**, so a state bit written under an enable
costs `dffq_1` + `mux2_1` = 63.66 + 28.54 = **92.20 µm²** before any logic
touches it (`docs/area-budget.md` §0). That gives:

| | bits | × 92.20 µm² |
|---|---:|---:|
| drum configuration at MODES=12 | 2,276 | **209,847 µm²** |
| drum configuration at MODES=16 | 2,556 | **235,663 µm²** |

**That rule is not taken on faith — it was checked against a synthesis.**
`rtl-sketch/area/mode_cfg_regs.v`, the strawman for the mode part only, is
840 bits at MODES=12 and measures **77,300 µm²** = 92.02 µm² per bit. The
rule-of-thumb and the synthesis agree to **0.2 %** on the average, so the
extrapolation to the other 1,436 bits is a reasonable one — but it *is* an
extrapolation, and the 1,436 bits of envelope, path, oscillator and accent
storage have **not** been synthesised by anyone.

The *marginal* rate is slightly worse than the average: 12 → 16 modes is
280 bits and measures 26,502 µm², i.e. 94.65 µm²/bit, because the address
decode grows too. Section 3 quotes the measured 26,502; this section's
projection uses the flat rule and would say 25,816. **1.3 % apart, and they are
different kinds of number** — one is a synthesis of a strawman, the other is
arithmetic on a per-bit constant. Neither is averaged into the other anywhere
in this document.

**Call it what it is: ~210,000 µm² of register file that exists in no area
number in this repository, including this document's.** It is a quarter of the
whole placeholder `synth_top`.

### 4.2 A joined-design projection — NOT a measurement

**Nobody has synthesised a `synth_top` containing `drum_kit`.** What follows is
cells-plus-cells arithmetic. It is a projection. This repository has already
withdrawn one chip-area projection built exactly this way — the previous one
used the `drum_src_seq` strawman's 89,004 µm² for the drum sources, and the
real `drum_dp` measures **272,894 µm²**, so the strawman was low by 3.1×.
Treat what follows with the same suspicion.

Built entirely from this branch's own A/B so that one flow is used throughout
(mixing ORFS and local numbers would hide the 2.3 % recipe difference inside the
result). "Chip without drums" is `synth_top` minus the placeholder, measured on
both sides:

| | `*_1` allowed | `*_1` excluded |
|---|---:|---:|
| `synth_top` with placeholder (measured) | 656,275 | 904,436 |
| − `drum_section_placeholder` (measured) | −158,770 | −221,685 |
| = chip without any drum section | 497,505 | 682,751 |
| + `drum_kit` MODES=12 NUMS=6 (measured) | +603,118 | +799,624 |
| + configuration register file (**projected**, §4.1) | +209,847 | +209,847 |
| **= joined design, MODES=12** | **1,310,470** | **1,692,222** |
| **= joined design, complete 808 (16/11)** | **1,363,601** | **1,764,863** |

Against the fixed core of `pnr/orfs/synth_top/config.mk` — 1,673,400 µm², the
1.73 mm² wafer.space quarter slot's core area:

| | cells | utilisation the core would need |
|---|---:|---:|
| joined, MODES=12, `*_1` allowed | 1,310,470 | **78.3 %** |
| joined, complete 808, `*_1` allowed | 1,363,601 | **81.5 %** |
| joined, MODES=12, `*_1` excluded | 1,692,222 | **101.1 %** |
| joined, complete 808, `*_1` excluded | 1,764,863 | **105.5 %** |

**This is the sentence the `DONT_USE_CELLS` question was actually worth asking
for.** Allowing the `*_1` drive strengths is the difference between a joined
design that needs 78–82 % placement density — high, but ordinary for an
all-standard-cell design with no macros — and one that **does not fit the slot
at all** at any density. The measured cost of allowing them, from section 1,
is 4.2 % of critical path on a design with 2.8× timing margin.

It is still a projection. The placeholder chip was *measured* at 55.3 %
utilisation on this die; 78 % is a different regime, and whether the router
gets there is a P&R result on a netlist nobody has built.

Three things that would make this projection wrong, in the direction that
matters:

- **Cross-boundary optimisation cuts both ways.** `docs/area-budget.md` §0
  measured 5.8 % lost by keeping hierarchy on one design and 4 % on another.
  Joining two blocks at a top level typically *saves* a few percent against
  their standalone sum, so the projection is mildly pessimistic on that axis.
- **The scheduler may not absorb the drum section for free.** `modal_dp` takes
  3 clocks per mode + 2 — 38 clocks at MODES=12, 50 at 16 — inside a 256-clock
  frame that `voice_dp` is also using. If the frame schedule has to change,
  that is logic this projection does not contain.
- **The register file is a strawman**, and a real one with an address decode
  shared across the whole chip could be smaller or larger.

### 4.3 What this does and does not settle about the 1.73 mm² slot

It does not settle it, and no arithmetic here can. The two die-area methods in
this repository disagree by construction and the disagreement is the whole
question:

- `docs/area-budget.md` divides cell area by an **assumed** 50 % utilisation.
  On that method a joined design lands well over the 1.73 mm² quarter slot.
- `pnr/orfs/synth_top/config.mk` fixes the die at 1.7319 mm² and **measures**
  the utilisation, which came out at 55.3 % for the placeholder chip.

**The first is an assumption and the second is a measurement, and they are not
comparable.** Whether the joined design fits depends on achievable placement
density on a fixed die, which is a P&R result nobody has, on a netlist nobody
has built. The honest statement is: *the placeholder chip routes at 55.3 %
utilisation on the slot; the real drum section is roughly two thirds of that
chip again plus a register file nobody has synthesised; someone has to run it.*

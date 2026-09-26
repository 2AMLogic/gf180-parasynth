# The joined chip on a real shuttle half slot — LibreLane, measured

Companion to `docs/pnr-synth-top.md`, which measured the same RTL on ORFS against a
bare die with **no padframe**. This document measures it inside the wafer.space
gf180mcu project template's **half-height (1×0.5) slot**, with the template's own
padring, its five mandatory IP cells, and LibreLane 3 — a different toolchain, so
nothing here is transferred from the ORFS result.

**Read the caveats in §5 before quoting any number from this page.** In particular
this run **skipped every sign-off checker** (KLayout DRC, Magic DRC, Netgen LVS,
the GDS XOR, the density and antenna decks) and ran **no gate-level simulation**.
Where this page says "DRC" it means *the detailed router's own violation count*
and nothing else.

---

## 1. What question this answers

Issue #33 asked whether the joined chip (`synth_top` with the real `drum_kit`)
needed **two** quarter slots or a **structural cut** — dropping the modal bank,
the voice, or the writable configuration. Two quarter slots was already proven
(#36 / PR #40, 60.1 % utilisation, DRC-clean).

A later comment on #33 found the shuttle actually offers a **half slot** whose core
is 5.02 mm², about 1.5× the quarter-slot core the issue had budgeted against, and
computed that the design would sit near **40 % utilisation** there. That figure was
explicitly labelled *arithmetic on their measured core, not a routed result*, and
this repository has withdrawn three area projections, so it stood as a hypothesis.

**This page is what the flow has measured so far, and the verdict table says which
questions it has not answered.** Every table below is generated from the run's own
metrics by `pnr/shuttle/report_halfslot.py`, re-run with one command as the flow
progresses; nothing on the page is typed. That matters because an earlier draft of
this page, written while the run was still in global placement, asserted that measured
utilisation would come in *below* the ORFS route's 60.1 % and read the gap as margin.
It came in **above**. The prose was a prediction wearing the clothes of a result, and
the fix was to compute the comparison rather than to correct it.

**The run this page reports was still in detailed routing when the page was
generated.** Two of the four conditions below are therefore *not measured* — which is
a different outcome from failing, and the table keeps them apart.

### Verdict

<!-- BEGIN measured:verdict -->

| condition | verdict | measured |
|---|---|---|
| placed inside the template's own die and core | **yes** | `design__instance__utilization` = 73.07 % |
| detailed router reports no violations | *not measured* | the run has not completed a detailed route — no `route__drc_errors` |
| a populated padframe (S2's own condition) | **yes** | `design__instance__count__padcells` = 754 |
| setup closes post-route at every corner against 81.38 ns | *not measured* | no per-corner setup slack was recorded after detailed routing |

**This run does not establish that the joined chip fits one half slot.**

- NOT MEASURED: detailed router reports no violations

- NOT MEASURED: setup closes post-route at every corner against 81.38 ns

**NOT MEASURED is not FAILED**, and neither is evidence that the design is too big: §4.1 shows the cells placed inside the core with room left. Every condition above is a claim about **this run of this flow**. Read which condition is which before re-opening the area question — and in particular do not read an unfinished run as a negative result.

<!-- END measured:verdict -->

## 2. Provenance — what actually ran

| | |
|---|---|
| toolchain | LibreLane **3.1.0.dev2**, container `ghcr.io/librelane/librelane:3.1.0.dev2-x86_64` |
| flow | `Chip` (LibreLane's padring flow), `meta.version: 3` |
| PDK | gf180mcuD, ciel `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`, **all libraries** |
| standard cells | `gf180mcu_fd_sc_mcu7t5v0` — 7-track, 5.0 V, same library as `pnr/orfs` |
| pad cells | `gf180mcu_fd_io` |
| clock | 81.38 ns = **12.288 MHz**, the architecture's core clock — same period as `pnr/orfs/synth_top/constraint.sdc` |
| die / core | **fixed inputs**, from the template's own `librelane/slots/slot_1x0p5.yaml`, copied byte for byte and not edited |
| reproduce | `pnr/shuttle/run_librelane.py full` (`--help`, and `check` runs the preconditions without starting a tool) |
| host | **not** a sanctioned one — see §7 |
| committed evidence | `pnr/shuttle/evidence/halfslot-metrics.json` — every metric below with the step that wrote it (`check_route.py metrics`). The run directory itself is gigabytes and gitignored. |
| flop census | `pnr/shuttle/evidence/flop-census.json` (`check_route.py census`) |

The upstream template drives LibreLane from a Nix shell. There is no Nix on the
hosts this repository runs on, so `run_librelane.py` runs the **same version the
template's `flake.lock` pins** from LibreLane's own container instead. That is a
substitution, and it is the reason the toolchain row above names an image digest.

### 2.1 The die and the core are inputs; utilisation is the measurement

`FP_SIZING: absolute`, and no utilisation target is set anywhere in the config
layering, so utilisation cannot be an input to this flow:

```
DIE_AREA  [0, 0, 3932, 2531]      = 9,952,492 µm²   INPUT
CORE_AREA [442, 442, 3490, 2089]  = 5,020,056 µm²   INPUT
```

`pnr/orfs/synth_top/config.mk` makes the same point at greater length and
`pnr/orfs/area_provenance.py` enforces it. The same trap applies here:
`PL_TARGET_DENSITY_PCT` **is** an input and is not a utilisation measurement — it
is the global placer's spreading target. This run left it unset, so LibreLane used
its own default; nothing on this page is `PL_TARGET_DENSITY_PCT` read back.

## 3. The chip is bigger than the arithmetic assumed

The ~40 % figure was computed against **2,033,110 µm²** of standard cells. That
number describes the `d1e5068`-era RTL at `MODES = 12`. `main` carries revision 10:
`MODES = 16`, and `drum_regs` grew with it.

<!-- BEGIN measured:growth -->

| | `d1e5068` (ORFS, `docs/pnr-synth-top.md`) | this run, `main` | source of the right-hand column |
|---|---:|---:|---|
| `drum_regs` flops declared | 2,276 | **3,520** | `check_route.py census` (yosys on `rtl-sketch/`) |
| sequential-cell area | 564,689 µm² | **781,500 µm²** | `design__instance__area__class:sequential_cell` |

Sequential cells alone grew by 38.4 % between the two runs. That is the design, not the flow.

<!-- END measured:growth -->

`drum_regs`'s bit count is re-derived from the RTL by `pnr/shuttle/check_route.py
census` (yosys, `hierarchy -top synth_top`, `proc`, `memory`) rather than quoted
from a document, because **both documented figures were wrong**:

- #33's own acceptance criterion asks for **2,276** — the `MODES = 12` figure, no
  longer in the RTL.
- `docs/capability-dag.md` says **3,232** for revision 10 — 288 short, one
  `ENVS × 16` bus.

The census is `pnr/shuttle/evidence/flop-census.json` and is regenerated, not
maintained by hand.

## 4. Measured

### 4.1 Area and utilisation

<!-- BEGIN measured:area -->

| quantity | value | metric key |
|---|---:|---|
| die area — **INPUT** | 9,951,890 µm² | `design__die__area` |
| core area — **INPUT** | 5,005,490 µm² | `design__core__area` |
| standard-cell area | 3,533,360 µm² | `design__instance__area__stdcell` |
| **utilisation of the core — MEASURED** | **73.07 %** | `design__instance__utilization` |
| standard-cell instances | 134,618 | `design__instance__count__stdcell` |
| of which sequential | 12,275 | `design__instance__count__class:sequential_cell` |
| tap cells | 32,635 | `design__instance__count__class:tap_cell` |
| standard-cell rows | 419 | `design__rows` |
| power (flow estimate) | 0.0124 | `power__total` |

**73.07 % of a 5.01 mm² core**, against **60.1 %** for the two-quarter-slot ORFS route on a 3.38 mm² core (`docs/pnr-synth-top.md`) — 13.0 points **above** it. The two are not the same measurement: this one is a padframe flow and its `design__instance__area__stdcell` includes the tap cells, end caps, clock tree and timing-repair buffers the flow inserted, which the earlier figure's core logic does not. Read §3 before treating the gap as a change in the design.

- timing-repair buffers: **814,435 µm²**, 23.0 % of the standard-cell area (`design__instance__area__class:timing_repair_buffer`)

- tap cells: **143,281 µm²**, 4.1 % of the standard-cell area (`design__instance__area__class:tap_cell`)

- end caps: **507,779 µm²**, 14.4 % of the standard-cell area (`design__instance__area__class:endcap_cell`)

- clock buffers: **87,615 µm²**, 2.5 % of the standard-cell area (`design__instance__area__class:clock_buffer`)

<!-- END measured:area -->

### 4.2 Timing per corner — and which of these are post-route

<!-- BEGIN measured:timing -->

Clock period 81.38 ns (12.288 MHz). Slack in ns; positive closes.

**The `written by` column is the point of this table.** LibreLane's metrics are cumulative, so a per-corner key that no step has updated since synthesis is still present in the final payload and reads exactly like a fresh measurement. A row whose step is not a post-route one is **not a post-route number** and is marked so; see §5.

| corner | setup WNS | hold WNS | implied min period | written by | post-route? |
|---|---:|---:|---:|---|---|
| `nom_tt_025C_5v00` | +34.061 | +0.724 | 47.32 ns | `42-openroad-stamidpnr-3` | **NO — written before the router ran** |
| **`nom_ss_125C_4v50`** | **-178.517** | +0.782 | 259.90 ns | `12-openroad-staprepnr` | **NO — written before the router ran** |
| `nom_ff_n40C_5v50` | -2.400 | +0.077 | 83.78 ns | `12-openroad-staprepnr` | **NO — written before the router ran** |

**This run has no post-route timing at any corner** — no per-corner setup slack was recorded after detailed routing. The un-suffixed `timing__setup__ws` reads **+34.061 ns** and was written by `42-openroad-stamidpnr-3` — it is the **nominal corner only**, so it is not the design's slack and must not be quoted as one. The per-corner rows above are `OpenROAD.STAPrePNR`'s: an unplaced, unrouted netlist with an ideal clock. The step that would produce real per-corner numbers is `OpenROAD.STAPostPNR`, which runs after detailed routing with extracted parasitics. This run has not reached it.

So **the 12.288 MHz question is open**, in both directions: this page neither shows the chip closing timing nor shows it failing to.

- setup TNS: **0** (`timing__setup__tns`, written by `35-openroad-stamidpnr-1`)

- max-slew violations: **20** (`design__max_slew_violation__count`, written by `42-openroad-stamidpnr-3`)

- max-cap violations: **112** (`design__max_cap_violation__count`, written by `42-openroad-stamidpnr-3`)

<!-- END measured:timing -->

### 4.3 The padframe

<!-- BEGIN measured:padframe -->

`docs/dag.json` node `S2` was blocked with *"routed die has padcells: 0"*. This is that number.

| quantity | value | metric key |
|---|---:|---|
| **pad cells placed** | **754** | `design__instance__count__padcells` |
| signal / bidirectional pads | 50 | `design__instance__count__class:input_output_pad` |
| input-only pads | 6 | `design__instance__count__class:input_pad` |
| power pads | 16 | `design__instance__count__class:power_pad` |
| pad spacers / fill | 682 | `design__instance__count__class:pad_spacer` |
| hard macros (the wafer.space IP cells) | 5 | `design__instance__count__macros` |
| top-level IO ports | 58 | `design__io` |

The five mandatory wafer.space IP cells (`qrcode_id`, `shuttle_id`, `project_id`, `marker`, `logo`) sit in the die margin **outside** `CORE_AREA`, so they consume no core area and do not appear in the utilisation figure above. Their contents are placeholders — see §5.

<!-- END measured:padframe -->

### 4.4 The cross-check that the run did real work

<!-- BEGIN measured:crosscheck -->

A DRC count cannot tell a real result from a collapsed netlist: **both are smaller and cleaner when the design has collapsed.** This repository quoted a "1,917-cell" `ladder_dp` three times before noticing every output was X. So the check is whether the flops the RTL *declares* are the flops the layout *places* — yosys on `rtl-sketch/` against the run's own DEF, two tools sharing no code path.

From `41-openroad-repairantennas/1-openroad-diodeinsertion/chip_top.def` (135,377 components), by `pnr/shuttle/check_route.py verify`:

| block | flops placed | flops declared (RTL) | |
|---|---:|---:|---|
| `u_dregs` | 3,520 | 3,520 | **the discriminator** |
| `u_drums.src` | 3,043 | 3,148 |  |
| `u_drums.bank` | 2,075 | 2,091 |  |
| `u_voice` | 2,624 | 2,732 |  |
| `u_voice.u_ladder` | 511 | 535 |  |
| `u_voice.u_div` | 55 | 79 |  |
| `u_spi` | 251 | 344 |  |
| `u_i2s` | 48 | 65 |  |
| `synth_top` | 126 | 29 |  |
| **total placed** | **12,275** | | |

**Only the `u_dregs` row is expected to match exactly, and only that row is asserted on.** Synthesis flattens `synth_top` and optimises across module boundaries, so a per-module census taken *before* flattening does not have to agree block by block with what survives after it — every other row here is lower than its census figure and that is the normal amount. `drum_regs` is different because it is a register file: every bit is architecturally visible at a port, so nothing can be merged away without changing the chip.

`drum_regs` places **3,520** flops; the RTL declares **3,520** — **equal**. `drum_regs` is the best single probe in this design: it is the largest register file and every bit of it is a declared `reg`, so a collapsed netlist cannot produce the number by accident.

The flop is attributed to a block by **the name of the net its `Q` drives**, not by its instance name: the netlist is flattened before placement, so every instance in the DEF is an auto-name (`_141690_`) and no hierarchy survives there. An earlier version of this check keyed on instance names, bucketed all 12,275 placed flops as 'outside `synth_top`', and reported the collapse signature for a layout that is fine. `drum_regs`'s own internal names do not survive either (`memory` rewrites its `reg [25:0] a1 [0:MODES-1]` arrays), so its bucket is the set of nets `u_dregs` drives — read out of `rtl-sketch/synth_top.v` on every run rather than listed here, so a port rename cannot quietly shrink it.

The flow's own count of sequential cells anywhere on the die is **12,275** (`design__instance__count__class:sequential_cell`), which includes the clock tree's own registers and anything outside `synth_top`.

<!-- END measured:crosscheck -->

## 5. What this run does **not** show

Stated positively so it cannot be read as a claim by omission.

- **No sign-off DRC.** `run_librelane.py full` passes `--skip` for
  `KLayout.DRC`, `Magic.DRC`, `KLayout.Antenna`, `KLayout.Density`,
  `Netgen.LVS`, `KLayout.XOR` and `OpenROAD.IRDropReport`, and for each one's
  `Checker.*`. They are **skipped, not silenced**: the flow did not run them, so
  it cannot have passed them. `run_librelane.py signoff` runs them and is the mode
  a tapeout claim would need. This is the same distinction
  `docs/pnr-synth-top.md` §"⚠️ Read '0 DRC' precisely" draws for the ORFS result,
  and it applies here for the same reason.
- **No LVS.** The routed layout has not been compared against the netlist.
- **No gate-level simulation.** Nothing here shows the chip *computes* anything
  (`docs/verification-rules.md` rule 3). The flop cross-check in §4.4 shows the
  layout is the design's *structure*, which is a much weaker claim than
  correctness — it rules out a collapsed netlist, not a wrong one.
- **No IR-drop report.** `OpenROAD.IRDropReport` was skipped.
- **The five wafer.space IP cells are placeholders.** `macros_5v.yaml`'s own
  comments say so: "required: will be replaced with actual content". They occupy
  the right area in the die margin; their contents are not the shuttle's.
- **`WITH_UART = 0`.** The UART bridge is not elaborated in this build; its two
  pins are brought out and the transmit pin idles high. A `WITH_UART = 1` chip has
  not been placed and routed and would be bigger.

## 6. Tool findings

Recorded here because this block is a canary for the flow, and a workaround that
lives only in a script is a finding nobody else can use (`CLAUDE.md`).

<!-- BEGIN tool-findings -->

### 6.1 A stale per-corner slack is indistinguishable from a fresh one

**LibreLane 3.1.0.dev2.** LibreLane's metrics are cumulative: every step's
`state_out.json` carries forward every key any earlier step set. The mid-PnR STA
steps (`OpenROAD.STAMidPNR*`) update the un-suffixed `timing__setup__ws` but **not**
the `timing__setup__ws__corner:*` keys. So on this run,
`timing__setup__ws__corner:nom_ss_125C_4v50 = -178.5 ns` is present, byte-identical,
in all thirty steps after the one that wrote it — `12-openroad-staprepnr`, an
unplaced, unrouted netlist with an ideal clock.

A consumer reading the last step's metrics sees a routed chip missing the slow
corner by more than two clock periods. **That paragraph was drafted here before the
provenance was checked.** Nothing in the payload distinguishes a live key from a
fossil; it took diffing the metrics step by step to see it.

- *Mitigation in this repo*: `check_route.metric_source_step()` and
  `post_route_keys()`; the §4.2 table prints the writing step for every row and
  `report_halfslot.worst_setup()` returns "not measured" rather than falling back to
  the nominal-corner key.
- *Upstream shape*: a step that re-computes a metric family should clear or rewrite
  the whole family, or the payload should carry the writing step per key. **Not yet
  filed** — see §7.

### 6.2 `--skip Checker.X` does not remove X from the flow's advertised claims

**LibreLane 3.1.0.dev2.** `run_librelane.py full` skips seven sign-off steps and each
one's `Checker.*`. The flow then completes with no indication in its own summary that
the skipped checks never ran — the distinction lives only in the invocation. §5 of
this page exists to carry it, which is the same workaround shape `CLAUDE.md` records
for `klt` placing no PDN without saying so.

### 6.3 gf180mcu's default `ciel` library set omits the pad library

**ciel `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`, gf180mcuD.** `ciel enable` without
`-l all` installs the standard-cell libraries only. A padframe flow then fails tens of
minutes in with an opaque Tcl `no files matched glob pattern` from
`libs.tech/librelane/config.tcl`, naming no library. `run_librelane.py`'s `check` mode
asserts the pad library's presence up front and REFUSES; `bootstrap-pdk` installs with
`-l all`. The same glob also swallows a pad library predating `*__blackbox_pp.v`.

### 6.4 `DRT-0349`: `LEF58_ENCLOSURE with no CUTCLASS is not supported`

**OpenROAD 2026-02-17, gf180mcuD tech LEF.** The detailed router prints this for
`Via1` through `Via4` and skips the rule. Two lines per via layer, no summary, and the
run proceeds. A skipped enclosure rule is a rule the router is not honouring, so the
router's own violation count is an *under*-count against sign-off by an unknown
amount — which is a further reason §5's "no sign-off DRC" caveat is not a formality.

<!-- END tool-findings -->

## 7. What has not been done, and by whom

- **The run was not finished.** It was still in `OpenROAD.DetailedRouting` when this
  page was generated. Re-run `pnr/shuttle/report_halfslot.py <run-dir>` against the
  completed run and every table above updates; `--check` fails if the page is stale.
- **The run was not on a sanctioned host.** It was started on a shared Loom dispatch
  worker by a sweep attempt that then died, leaving it running with no session behind
  it (filed as #310). `CLAUDE.md` places work of this size on the pinned build box.
  Nothing about the *numbers* is affected — but the eight-thread router was competing
  with other sweeps for eight cores, and the completing run belongs on the box.
- **The tool findings in §6 are not filed upstream yet.** §6.1 is the one worth
  filing; it is a defect shape rather than a configuration mistake.

<!-- generated-from: halfslot step 42-openroad-stamidpnr-3 by pnr/shuttle/report_halfslot.py -->

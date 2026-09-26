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

**This page is the routed result**, and the verdict table below is generated from the
run's own metrics rather than written — an earlier draft of this page, written before
the run landed, asserted that the measured utilisation would come in *below* the ORFS
route's 60.1 % and read the gap as margin. It came in above. That sentence is gone and
the comparison is now computed; §4.1 carries the number and the reason.

### Verdict

<!-- BEGIN measured:verdict -->
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
| reproduce | `pnr/shuttle/run_librelane.py full` (see `pnr/shuttle/README.md`) |

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
<!-- END measured:area -->

### 4.2 Timing on the routed design, per corner

<!-- BEGIN measured:timing -->
<!-- END measured:timing -->

### 4.3 The padframe

<!-- BEGIN measured:padframe -->
<!-- END measured:padframe -->

### 4.4 The cross-check that the run did real work

<!-- BEGIN measured:crosscheck -->
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
<!-- END tool-findings -->

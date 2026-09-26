# Tool findings: what was filed upstream, and what was looked at and not filed

This is the investigation recorded on **2026-09-18**, against the versions
named below. Statements about upstream `main` refer to those commits. At
landing on 2026-09-19, upstream issues
[#2085](https://github.com/2AMLogic/klayout-tools/issues/2085),
[#2088](https://github.com/2AMLogic/klayout-tools/issues/2088), and
[#2089](https://github.com/2AMLogic/klayout-tools/issues/2089) were closed;
[#2086](https://github.com/2AMLogic/klayout-tools/issues/2086) and
[#2090](https://github.com/2AMLogic/klayout-tools/issues/2090) remained open.
Closing an upstream issue does not revalidate our older flow artifacts.

A comb of this repository for **defects in the tools we use** that had been
discovered here and recorded only as workarounds in shell scripts, run logs and
doc footnotes.

The point is stated in `README.md` §"Why this block exists": a fixed tool helps
every project that follows; the instrument helps one. A workaround that lives
only in a shell script is a finding nobody else can use.

**Two lists follow, and the second matters as much as the first.** "Looked at
and not filed, with the reason" is what stops the next comb re-examining the
same ground.

Scope of this pass: filing target was `2AMLogic/klayout-tools`. Findings whose
upstream is a different project are recorded below with the correct home named,
not filed in the wrong repository.

---

## 1. Filed

### klayout-tools#2088 — `klt synthesize` has no `gf180mcu_fd_sc_mcu7t5v0` entry in any per-library table

**New.** klt `0.5.0+g604c4fb8a1ce`, PDK `gf180mcuD` @ ciel `54435919…`,
`gf180mcu_fd_sc_mcu7t5v0`, `tt_025C_5v00`. Source checked on klayout-tools
`main` @ `c28dc868`.

#1649 added the 7-track library to `klt place-and-route`. `klt synthesize`
never got the same treatment: `grep -c mcu7t5v0 synthesize.py` = **0**, the same
grep on `place_and_route.py` = **30**. `_ABC_CONSTR_INPUTS` (`:341`) and
`_TIE_CELLS` (`:487`) carry the 9-track library only, keyed on the exact name.

Consequence, all from one missing dict key, with our own run's committed
artefacts as evidence:

- the generated `synth_ladder_dp.run.ys` has **no `-constr`**, **no
  `setundef -zero`**, **no `hilomap`**, and no ABC log;
- the response is `status: "ok"`, `warnings.total: 0`, **`timing: null`** — for
  a request that supplied `constraints.clock_period_ns = 81.38`;
- all 34 mapped cell types are `_1` drive, which is unconstrained `abc -liberty`;
- bare constant assigns survive → `[ERROR DRT-0305]` downstream (#2085 item 2);
- **#2006's `setundef -zero` fix is inert on this library**, because it is
  scoped to the same `_TIE_CELLS` condition — so #1973's Failure 2 is still
  reachable on current `main` for any 7t design.

The silent half is the dangerous half: a hollow `timing: null` inside an
`"ok"` envelope is indistinguishable from "this design has no timing to
report". Same family as #1988, in a verb #1988 does not cover.

Filed with the three table entries sourced against the installed PDK
(`__tieh` pin `Z` `function : "1"`, `__tiel` pin `ZN` `function : "0"`,
`__buf_4` pin `I` `capacitance : 0.009315` under `capacitive_load_unit(1, pf)`),
and with the cheapest fix regardless of the tables: **warn when `cell_library`
matches no table.**

Workaround in our tree: `pnr/klt/ladder_dp/run-klt.sh`.

### klayout-tools#2089 — `detailed_route` runs twice into the same `-output_drc` path, so the DRC count comes back doubled

**New.** Same environment. Root cause in klt source: one `detailed_route_call`
string (`place_and_route.py:3683`) emitted twice — once after `global_route`
(`:3720`) and once per antenna-repair iteration after `repair_antennas`
(`:3743`) — with the **same** `-output_drc` and `-output_maze` paths. Visible
verbatim in the committed `.klt/place-and-route/pnr_ladder_dp_route.tcl`.

Measured on our committed artefacts:

| artefact | value |
|---|---|
| `ladder_dp_route_drc.rpt` — `violation type:` records | **46** |
| same file — distinct `(type, srcs, bbox)` | **23**, every one appearing exactly twice, always as an adjacent pair |
| `_count_route_drc_violations` (counts header lines) | returns **46** |
| `ladder_dp_route_metrics.json` | one object, **97 duplicate keys**; `route__drc_errors` = **23** (line 118) and **46** (line 216) |
| what `json.loads` / `JSON.parse` sees | **46** |

`route_drc_violation_count` is a `_TOP_LEVEL_METRIC_KEYS` field (#938) and is
the only routing-DRC number klt produces. A clean design is unaffected (0
doubles to 0), so the error appears exactly when the number is being consulted.
With `max_antenna_repair_iterations = N` the multiplier is N+1.

Independently sighted under plain ORFS on the same platform: our `synth_top`
run's "2 detailed-route DRC violations" are **one** site listed twice
(`docs/pnr-synth-top.md` §3).

### klayout-tools#2090 — `provenance.klt_version` reports the package version, not the build version

**New.** `klt version` prints `0.5.0+g604c4fb8a1ce`; the committed
`synth_response.json` says `"klt_version": "0.5.0"`.
`build_identity.build_version()` (#1202) exists precisely for this and is wired
only into the CLI banner (`cli/__init__.py:22`, `cli/parser.py:111`);
`_provenance.py:356` → `_klt_version()` returns `klayout_tools.__version__`,
which `build_identity.py`'s own docstring calls *"correct for packaging and
useless for identity"*.

Demonstrated harm, ours: **#2085 was filed with two items that were already
fixed**, because `"0.5.0"` is true on both sides of #2006 (`652d1536`, landed
the day after the build we ran). We only recovered `604c4fb8` because our
wrapper happens to run `klt version` into `run.log`. Sibling of the open #2035.

### klayout-tools#2085 — comment: correction and narrowing

Our own earlier filing, corrected. Items 1 (`signed`/`STA-0171`) and 3
(dangling all-`x`) are already fixed by #2006, which landed after our build.
Item 2 (`_TIE_CELLS` 9t-only) is **still live at `c28dc868`** and additionally
makes item 3's fix inert for 7t — carried forward as #2088.

Also added: the `signed`/`STA-0171` failure **reproduces outside klt**, under
plain ORFS on the same RTL and platform (workaround:
`pnr/orfs/gf180_7t/synth_unsigned.tcl`). It is a yosys-`write_verilog` /
OpenSTA-reader interaction, not a klt-specific defect — which supports #2006
doing the rewrite inside klt.

### klayout-tools#2086 — comment: the positive control, and a detector

Added the counts from klt's own DEF for the run **with** a power block — 9,789
fillers, 244 `filltie`, 240 `endcap`, 2 SPECIALNETS — which are already in the
DEF klt writes and reach no response field. That is issue #2086's suggestion 3,
costed.

Also offered `pnr/orfs/check-pdn.py` as a spec for the refusal: ~70 lines of
pure DEF text parsing, exits 1 on no `FOLLOWPIN` rails, no straps above Metal1,
no PDN vias, no tap/endcap, or no fillers.

Stated honestly in the comment: our committed artefact is the **positive**
case. The negative case is a recorded observation, not a kept artefact.

---

## 2. Looked at and not filed, with the reason

### Real defects, but the upstream is not klayout-tools

Filing these into `klayout-tools` would be wrong-repo noise. Recorded here with
the correct home so the next pass does not re-derive them.

| Finding | Evidence | Upstream |
|---|---|---|
| **ORFS gf180 `TRACK_OPTION=7t` is broken out of the box.** `platforms/gf180/cells_adders.v` / `cells_latch.v` hard-code `gf180mcu_fd_sc_mcu9t5v0__addf_1`/`addh_1`/`latq_1`; with the 7t liberty yosys aborts with 414 `is used but has no driver` in `check -assert`. Workaround: `pnr/orfs/gf180_7t/`. | `docs/pnr-first-run.md` §1.1 item 1 | OpenROAD-flow-scripts |
| **IR-drop aborts at every corner but WC.** `platforms/gf180/setRC.tcl` sets via resistances only when `CORNER=WC`; at TC `analyze_power_grid` dies with `[ERROR PSM-0021] Resistance map constains invalid values.` (typo upstream's), *after* the final DEF/ODB/SPEF/netlist are written but before `report_metrics`. Exact text in `pnr/orfs/work/ladder_dp.log:778`. | `docs/pnr-first-run.md` §1.1 item 4 | OpenROAD / ORFS gf180 platform |
| **`DESIGN_CONFIG` is included before the platform config**, so platform variables set with plain `=` (e.g. `DONT_USE_CELLS = *_1`) can only be overridden on the make command line. | `pnr/orfs/README.md` | OpenROAD-flow-scripts |
| **`ecppll -i 25 -o 12.288` answers 12.5 MHz and exits 0** — 17,253 ppm, 29.7 cents sharp — without saying it has given up. Pure silent wrong answer. Workaround: `fpga/scripts/pll_search.py` (92 lines), result 725/59 MHz = +11 ppm, hand-instantiated in `fpga/rtl/ulx3s_top.v`. | `docs/fpga-clock.md:58`, `fpga/scripts/pll_search.py:8` | prjtrellis |
| **nextpnr prints "Max frequency for clock" twice and the first is the post-placement estimate**, unlabelled — and it is not a conservative bound (30.30 vs 30.49 on one design; 29.82 vs 30.88 on another). Workaround: `fpga/scripts/report.sh:54` labels both. | `fpga/reports/logs/ecp5_pnr.log:118` vs `:609` | nextpnr |
| **`nextpnr-ice40 --version` reports an empty version string** (`"nextpnr-ice40" -- Next Generation Place and Route (Version )`), so the iCE40 half of the flow is unversioned in our provenance record. | `fpga/reports/provenance.txt:7-9` | nextpnr packaging |
| **`gf180mcu_fd_io` operating-conditions table says 4.5–5.5 V DVDD while its characterization-corners page lists 3.3 V corners.** Unresolved. | `docs/DESIGN.md:331-336` | gf180mcu PDK |

### Judged not a defect

- **ORFS `LEC_CHECK=1` dies with `child killed: illegal instruction`** in CTS.
  That is a Kepler binary under amd64 Rosetta emulation on Apple Silicon — an
  **environment problem**, per the brief's own exclusion. `LEC_CHECK=0`.
- **`SYNTH_MEMORY_MAX_BITS` default 4096 rejects a 257×16 = 4,112-bit ROM.** A
  documented, tunable limit doing what it says. Raised to 65536.
- **GNU make keeps trailing whitespace before an inline `#`**, so
  `CORNER = TC   # …` silently yields `$(TC   _LIB_FILES)` = empty. Documented
  GNU make semantics, not a tool defect. There *is* an ORFS-side ask — refuse
  when `$(CORNER)_LIB_FILES` resolves empty rather than calling `read_liberty`
  with no file — but we did not keep the artefact of what that run produced, so
  it is not filable to the bar.
- **ORFS's gf180 default corner is `BC = ff_n40C_5v50`, the optimistic one.** A
  platform default, not a defect; we set `CORNER=TC` and said so.
- **`DONT_USE_CELLS = *_1`: klt allows `*_1`, ORFS excludes it**, a 22–42 %
  area difference on the same RTL (`docs/verification-rules.md`). klt's
  divergence is **deliberate, sourced and measured** — `synthesize.py:368-395`
  documents it at length under #807 (+31 % area, +12 % delay on an 8×8
  multiplier) and explains why a congestion policy does not belong at synthesis
  time. Our own +29 % to +40 % across every block on gf180 7t corroborates
  their number rather than contradicting it. Both responses carry
  `script_path`/`run_script_path`, so the policy actually applied is always
  recoverable from the artefact.
- **yosys pads inferred memories to a power of two** (MODES 9–16 cost
  identically), and **keys parameterised modules as `$paramod$<hash>\<name>`**.
  Both are correct documented behaviour. The second one produced a wrong number
  here — `drum_kit` reported at 325,388 µm² when that is the submodule
  `modal_dp` — but that was **our** extractor taking `modules.values()[0]`;
  fixed and documented in `fpga/scripts/x1_report.py`.
- **yosys optimised an all-X datapath down to 1,917 cells and reported a
  plausible area.** Correct behaviour on a design that was don't-care. **Our**
  RTL bug; it is the origin of `docs/verification-rules.md` rule 3. Reproduced
  and now permanently injected (`pnr/report_synth_area.py --inject
  TANH_INDEX_OOR`, issue #245), and the reproduction sharpens the finding: on
  the fixture the collapse is **invisible from the netlist**. It contains no
  `x`, it simulates `x`-free at the gate level against the same bench, and its
  cell count moves 0.1 % (1,677 against 1,679) — yosys resolved the don't-care
  to concrete values, as it is entitled to. The **behavioural** simulation of
  the sources is the only thing that sees it (`y` is `x` on 506 of 512 cycles).
  So "check the netlist for X before quoting its area" is not a weaker version
  of the right check; it is not a check at all.
- **`iverilog -Ptb.dut.IDX_BITS=5` silently does nothing.** `-P` assigns
  parameters of a **root module instance** only, so a hierarchical path through
  an instance is accepted on the command line, applied to nothing, and reported
  with no warning: `iverilog -g2012 -o x.vvp -Ptb.dut.IDX_BITS=5 tb.v dut.v` &&
  `vvp x.vvp` printed `IDX_BITS=4`. Icarus Verilog 13.0 (stable, v13_0).
  Documented semantics, arguably, and **our** misuse — recorded because the
  *shape* is the one `docs/verification-rules.md` rule 5 condition 2 exists for:
  an injection that never activates while every log looks clean, and the run
  reports the clean design's numbers under the mutant's name. It cost about
  twenty minutes here and only surfaced because the fixture was expected to go
  X and did not. The workaround is not to use `-P`: `pnr/report_synth_area.py`
  writes parameter overrides into a generated wrapper that **both** yosys and
  iverilog read, and refuses the run unless two independent receipts — the
  `$paramod\<mod>\<P>=…` module yosys derived, and the value the running
  simulation prints from inside the elaborated hierarchy — agree with what was
  asked for.
- **`FREQUENCY PORT "clk" 12.288 MHz` on the ULX3S's 25 MHz oscillator pin,
  with no PLL, and nextpnr reported PASS.** Documented LPF semantics — a
  `FREQUENCY PORT` is an assertion the checker verifies, not a clock the board
  produces. **Our** constraint-file bug. Recorded because the *shape* (a green
  sign-off on a hollow premise; the only tell was `EHXPLLL: 0/2`) is the class
  this ledger exists for.
- **`fpga/Makefile`'s nextpnr recipes are prefixed with `-` and piped through
  `| tail -40`**, so make sees `tail`'s status either way. **Our** bug, and
  exactly the anti-pattern `CLAUDE.md` §"Write Python, not bash" records. Not a
  tool defect; worth fixing here.
- **GitHub does not fail a PR for an unparseable workflow — it silently does
  not register it.** Real and dangerous, but not an EDA tool and not
  klayout-tools.

### Questions the brief asked, answered

**"ORFS's `gf180` platform has no KLayout sign-off DRC deck (`make drc` answers
'DRC not supported'). Is that a klayout-tools gap, an ORFS gap, or expected?"**

**An ORFS gap, and klayout-tools already fills it.** `klt drc` ships a curated
**gf180mcu** deck — `src/klayout_tools/drc.py` carries gf180mcu rules
throughout (`DF.1a`/`DF.3a` `_LV`/`_MV` voltage pairs, `MIMTM.1`, `mim.space.1`,
the `Dualgate` 55/0 marker layer), and `:1711` names `sky130`/`gf180mcu` as the
curated engine's deck identifiers. So there is nothing to file against
klayout-tools; the remedy is to run `klt drc` on the P&R output.

Not established, and deliberately not tested here (this pass does not re-run
P&R): whether that deck behaves sensibly on a full standard-cell digital GDS
as opposed to the analog blocks it was built for. That is the next experiment,
not a finding.

**"No behavioural Verilog cell models, so no gate-level simulation" — also
repeated as a v0.3 non-claim.** True of the **ORFS platform**, false of the
**PDK we already have installed**. `gf180mcuD` at ciel `54435919…` ships
`libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/{gf180mcu_fd_sc_mcu7t5v0.v,primitives.v}`
— 964 KB, with `USE_POWER_PINS`/`FUNCTIONAL` guards and `specify` timing blocks.
klt's own `functional_verification.py` is PDK-agnostic (the caller supplies the
cell models). So gate-level simulation of a routed netlist is **unblocked
today** and needs no tool fix — nobody wired it up. `docs/v0.3-scope.md` and
`docs/pnr-synth-top.md` §8 should be narrowed from "impossible" to "not done".

### Candidates left on the table, with what is missing

- **`[WARNING DRT-0349] LEF58_ENCLOSURE with no CUTCLASS is not supported.
  Skipping for layer Via1`** — and the same for Via2, Via3, Via4, in every
  routing log we have (`pnr/orfs/work/ladder_dp.log:127`,
  `synth_core.log:138`, `ladder_dp_util70.log:1020`). TritonRoute silently
  skips a whole rule class on **every** via layer of this platform, and the
  resulting count is the only DRC number this project quotes. The klt-side ask
  — surface which rule classes the router skipped alongside
  `route_drc_violation_count` — is a coverage feature in the shape of #1988,
  but we have not established what klt could feasibly capture from the router,
  so it is not filed. **Next step: establish whether the skipped enclosure
  rules are checkable by `klt drc`'s gf180mcu deck instead.**
- **`pnr/klt/ladder_dp/par_response.json` is 0 bytes.** `klt place-and-route
  --format json` produced no output on a run that had written a routed DEF,
  ODB, DRC report and metrics. A JSON-consuming caller gets a parse error
  rather than a structured failure envelope. **Not filed:** stderr was not
  captured, and the file's mtime (Sep 18 01:37) predates the route artefacts
  (Sep 18 15:57), so we cannot establish which invocation produced it or why.
  Re-running is out of scope for this pass. To file this, capture stderr and
  the exit status.

---

## 3. What this pass changes about how findings get recorded

Three of the five items above needed information that was only available by
accident:

- **#2090 exists because `provenance.klt_version` could not identify the
  build.** Our correction to #2085 was only possible because a wrapper script
  happened to run `klt version` into `run.log`.
- **The `par_response.json` candidate is unfilable because stderr was not
  captured.**
- **The GNU-make candidate is unfilable because the bad run was fixed rather
  than kept.**

So: when a tool is worked around, keep the failing artefact, capture stderr,
and record the tool's own self-reported build string next to it. A workaround
without those is a finding that cannot be filed.

# First placed-and-routed area numbers on gf180mcu (ORFS, 7-track, 5.0 V)

Date: 2026-09-17. Machine: macOS 26.5.1 / Apple Silicon (10 cores, 32 GB), Docker Desktop 29.7.2.
Sources: `rtl-sketch/ladder_dp.v` at `226a7a8` (this repo), `gf180-polysynth/rtl/{synth_core,synth_voice,uart_rx}.v`
at `b60e424`. Everything below was measured here; nothing is estimated unless it says so.
Reproduce with `pnr/orfs/run-orfs.sh <design>` (configs in `pnr/orfs/`, see `pnr/orfs/README.md`).

__HEADLINE__

## 1. Which flow ran, and why

Tried in the order asked:

| # | Flow | Result on this machine |
|---|---|---|
| (a) | TinyTapeout LibreLane config (`prep/tinytapeout/tt_um_2amlogic_nco_gf`, `librelane==3.0.14` in its venv) | **Did not run.** LibreLane runs its tools from the `ghcr.io/librelane/librelane:3.0.14` Docker image; the image cannot be extracted because the Docker VM disk has 4.1 GB free of 59 GB (same `no space left on device` as in `prep/tinytapeout/REPORT.md` section 5.1; `docker system df` shows 15 GB of reclaimable images belonging to other work, which I did not prune). No native `openroad`, `magic`, `netgen` or `klayout` binary exists on the host (`which` finds none; OSS CAD Suite ships only yosys/iverilog/verilator), and Nix is not installed, so the non-Docker route is closed too. |
| (b) | **OpenROAD-flow-scripts (ORFS)** inside the pinned `openroad/orfs:26Q3-296-gda37dce1c` image (`sha256:ebc8142d…`, already present, 4.64 GB, used by `prep/asic`) | **Ran end to end.** The image ships the complete `flow/platforms/gf180` platform: `gf180mcu_fd_sc_mcu7t5v0` and `9t5v0` liberty at all 15 corners, 5LM tech/cell LEF, cell GDS, PDN strategy (`pdn_grid_strategy_7t_6M.cfg`), tapcell script, fill config, OpenRCX rules, KLayout tech files. OpenROAD `26Q3-1260-g06a5a02279`, Yosys `0.68+post`, KLayout `0.30.7`. Runs as linux/amd64 under emulation (Rosetta; `avx2` visible). |
| (c) | LibreLane/OpenLane standalone | Same blocker as (a): needs the LibreLane image or a Nix toolchain. |
| (d) | klt (`klayout-tools` 0.5.0+g604c4fb8, provisioned in `prep/asic/.venv`, yosys/openroad via the same ORFS image) | **Ran as a cross-check** on `ladder_dp` (`pnr/klt/ladder_dp/`). Needed three netlist patches (below). **Those three patches were retired in #765**: klt `0.9.2.dev986+gaadc1d351523` and `0.9.2.dev1029+g76f47afa48f1` emit a byte-identical netlist with none of the three defects, so this row is history, not current practice. The replacement driver is `pnr/klt/ladder_dp/run_klt.py` and it re-measures the three patches' own matchers on every run. Its committed record is bounded at `cts` (a full route here is ~5 h); the end-to-end route is #808. See `docs/tool-findings.md` under klayout-tools#2088. |

The liberty files in the ORFS image are byte-identical in content to the PDK the project points at
(`gf180mcuD` at ciel `54435919…`; e.g. the 7t `tt_025C_5v00` liberty carries the same cell set of 229
cells, adders/latches/ties/taps/fills included), so the numbers are for the project's own PDK.

### 1.1 What had to be fixed to make ORFS run 7-track gf180 (all in `pnr/orfs/`)

Each of these cost one failed run; none touches the verified RTL.

1. **`TRACK_OPTION=7t` is broken out of the box.** `platforms/gf180/cells_adders.v` and `cells_latch.v`
   hard-code `gf180mcu_fd_sc_mcu9t5v0__addf_1`/`addh_1`/`latq_1`, so with the 7t liberty every full
   adder is an unknown cell and yosys aborts with 414 `is used but has no driver` problems in
   `check -assert`. Fix: 7t copies of both files (`gf180_7t/`), selected via `ADDER_MAP_FILE`/`LATCH_MAP_FILE`.
2. **OpenSTA rejects `signed` in netlist port declarations** (`[ERROR STA-0171] … syntax error`),
   which yosys carries over from `input wire signed [15:0] x_in`. Fix: `gf180_7t/synth_unsigned.tcl`
   (`SYNTH_SCRIPT` wrapper) strips the qualifier from `1_2_yosys.v` after synthesis. This is the same
   trap `gf180-polysynth/rtl/README.md` documents; `synth_core` was written around it, `ladder_dp` was not.
3. **`LEC_CHECK=1` in the image** runs a Kepler formal binary in the CTS step that dies with
   `child killed: illegal instruction` under amd64 emulation. `LEC_CHECK=0`.
4. **IR-drop analysis aborts at the TC corner** (`[ERROR PSM-0021] Resistance map contains invalid
   values`): `platforms/gf180/setRC.tcl` sets via resistances only when `CORNER=WC`. It fires after
   the final DEF/ODB/SPEF/netlist are written but before `report_metrics`. `PWR_NETS_VOLTAGES=`/`GND_NETS_VOLTAGES=`
   (empty) skips it.
5. **`SYNTH_MEMORY_MAX_BITS`** (default 4096) rejects `synth_core`'s 257-entry sine ROM, which yosys
   folds into a 4096-bit `$mem`; raised to 65536 so it is synthesised as logic (as the FPGA build does).
6. GNU make keeps trailing whitespace before an inline `#` comment in a variable value, so
   `CORNER = TC   # comment` silently makes `$(TC   _LIB_FILES)` empty (`read_liberty` with no file).
7. `DESIGN_CONFIG` is included *before* the platform config, so platform variables set with plain
   `=` (notably `DONT_USE_CELLS = *_1`) can only be overridden on the make command line.

### 1.2 Flow settings that matter for reading the numbers

- Library `gf180mcu_fd_sc_mcu7t5v0`, site `GF018hv5v_mcu_sc7` (7-track, 5.0 V), 5 metal layers, signal routing Metal2..Metal5.
- **Corner = TC = `tt_025C_5v00`** for synthesis, placement, CTS, routing, RCX (`…_typ.rules`) and the
  flow's own timing. ORFS's default for gf180 is **BC = `ff_n40C_5v50`**, which is the optimistic one;
  I did not use it for the flow. Section 4 adds ss_125C_4v50 and ff_n40C_5v50 STA on the routed design.
- Clock 81.38 ns (12.288 MHz) on `clk`, input/output delays 20 % of the period. `ABC_AREA=1`.
- Floorplan by `CORE_UTILIZATION=50`, aspect 1, 2 um core margin; `PLACE_DENSITY=0.60`.
- ORFS's gf180 platform sets **`DONT_USE_CELLS = *_1`**: no x1-drive cells are used for logic (the
  adder techmap still instantiates `addf_1`/`addh_1` directly). This inflates cell area relative to a
  free yosys mapping; section 5 quantifies it with a second run (`FLOW_VARIANT=x1`, `DONT_USE_CELLS=`).
- PDN: ORFS's `pdn_grid_strategy_7t_6M.cfg` — Metal1 follow-pin rails 0.6 um, Metal4 straps 4.48 um
  at 44.8 um pitch (0.56 um pair spacing), Metal5 straps 4.48 um at 89.6 um pitch; tapcells
  (`filltie`, 100 um) + `endcap`; fillers `fill_1..64`. Metal density fill (`USE_FILL`) off, as in ORFS's own gf180 designs.

__RESULTS__

# OpenROAD-flow-scripts design config: ladder_dp (rtl-sketch) on gf180mcu, 7-track, 5.0 V.
# Run with ../run-orfs.sh ladder_dp   (see ../README.md). Everything here is a plain ORFS variable.
export PLATFORM        = gf180
# gf180mcu_fd_sc_mcu7t5v0 (ORFS default is 9t)
export TRACK_OPTION    = 7t
# TC = tt_025C_5v00. ORFS default is BC = ff_n40C_5v50 (optimistic); WC = ss_125C_4v50
export CORNER          = TC
export DESIGN_NAME     = ladder_dp
export DESIGN_NICKNAME = ladder_dp

DESIGN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
export VERILOG_FILES   = $(abspath $(DESIGN_DIR)/../../../rtl-sketch/ladder_dp.v)
export SDC_FILE        = $(DESIGN_DIR)/constraint.sdc

# The stock ORFS gf180 platform techmaps adders/latches to hard-coded 9t cell names; use 7t copies.
export ADDER_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_adders.v
export LATCH_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_latch.v
# OpenSTA rejects `signed` port declarations that yosys carries over from the RTL; this wrapper runs the
# stock synth.tcl and strips the qualifier from the gate-level netlist (a no-op for connectivity).
export SYNTH_SCRIPT = $(DESIGN_DIR)/../gf180_7t/synth_unsigned.tcl
# NOTE: GNU make keeps trailing whitespace before a "#" comment inside a value, so no inline comments here.
# ladder_dp's tanh16.hex ROM is $readmemh'd relative to the source file (yosys falls back to the
# source directory), so no copy is needed; the synth log must NOT contain "Can't open file".

# area-oriented ABC mapping (timing is trivial at 81 ns)
export ABC_AREA          = 1
# % of core area covered by cells after synthesis.
# THE DIE AND CORE AREAS OF THIS RUN ARE NOT MEASUREMENTS. Setting a target makes them the
# cell area divided by 0.50, so a die/cell ratio computed from them recovers 2.00 and nothing
# else -- the bug docs/pnr-synth-top.md section 2 records. ../summarize.py REFUSES to print
# that ratio for this design (exit 2, ../area_provenance.py, issue #245); the utilisation
# DRIFT the resizer and CTS add on top of the target is the only measured part. Keeping the
# target here is deliberate: this is a per-block area probe, not a floorplan of a product.
export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
# global-placement target density
export PLACE_DENSITY     = 0.60
# The image sets LEC_CHECK=1 with a Kepler formal binary that dies with "illegal instruction" under Docker's
# amd64 emulation on Apple Silicon (CTS step, run_lec_test). Not needed for area/timing numbers.
export LEC_CHECK = 0
# Skip the static IR-drop analysis in the finish step: platforms/gf180/setRC.tcl only sets via
# resistances for CORNER=WC, so at TC analyze_power_grid aborts ([ERROR PSM-0021] zero via resistance)
# after the final DEF/ODB/SPEF/netlist are written but before report_metrics runs.
export PWR_NETS_VOLTAGES =
export GND_NETS_VOLTAGES =

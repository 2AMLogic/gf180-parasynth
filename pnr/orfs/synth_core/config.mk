# OpenROAD-flow-scripts design config: synth_core NV=4 (gf180-polysynth/rtl) on gf180mcu, 7-track, 5.0 V.
# The RTL lives in the sibling gf180-polysynth checkout; run-orfs.sh exports POLYSYNTH_RTL and mounts it.
export PLATFORM        = gf180
# gf180mcu_fd_sc_mcu7t5v0 (ORFS default is 9t)
export TRACK_OPTION    = 7t
# TC = tt_025C_5v00. ORFS default is BC = ff_n40C_5v50 (optimistic); WC = ss_125C_4v50
export CORNER          = TC
export DESIGN_NAME     = synth_core
export DESIGN_NICKNAME = synth_core

DESIGN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
POLYSYNTH_RTL ?= $(abspath $(DESIGN_DIR)/../../../../gf180-polysynth/rtl)
export VERILOG_FILES   = $(POLYSYNTH_RTL)/synth_core.v $(POLYSYNTH_RTL)/synth_voice.v $(POLYSYNTH_RTL)/uart_rx.v
# note_inc_rom.vh / sine_q_rom.vh
export VERILOG_INCLUDE_DIRS = $(POLYSYNTH_RTL)
# the contract's 4 voices (RTL default is 2)
export VERILOG_TOP_PARAMS = NV 4
export SDC_FILE        = $(DESIGN_DIR)/constraint.sdc
# The 257-entry sine ROM and 128-entry note table are `always @*` case blocks; yosys folds them into a
# 4096-bit $mem, which hits ORFS's default SYNTH_MEMORY_MAX_BITS=4096 guard. Allow them as logic.
export SYNTH_MEMORY_MAX_BITS = 65536

# The stock ORFS gf180 platform techmaps adders/latches to hard-coded 9t cell names; use 7t copies.
export ADDER_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_adders.v
export LATCH_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_latch.v
# OpenSTA rejects `signed` port declarations that yosys carries over from the RTL; this wrapper runs the
# stock synth.tcl and strips the qualifier from the gate-level netlist (a no-op for connectivity).
export SYNTH_SCRIPT = $(DESIGN_DIR)/../gf180_7t/synth_unsigned.tcl

export ABC_AREA          = 1
# THE DIE AND CORE AREAS OF THIS RUN ARE NOT MEASUREMENTS: a target makes them the cell area
# divided by 0.50, so a die/cell ratio recovers 2.00 and nothing else (docs/pnr-synth-top.md
# section 2). ../summarize.py REFUSES to print it for this design -- exit 2, via
# ../area_provenance.py, issue #245. Deliberate here: a per-block area probe, not a product
# floorplan. The fixed-die design is ../synth_top.
export CORE_UTILIZATION  = 50
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN       = 2
export PLACE_DENSITY     = 0.60
# The image sets LEC_CHECK=1 with a Kepler formal binary that dies with "illegal instruction" under Docker's
# amd64 emulation on Apple Silicon (CTS step, run_lec_test). Not needed for area/timing numbers.
export LEC_CHECK = 0
# Skip the static IR-drop analysis in the finish step: platforms/gf180/setRC.tcl only sets via
# resistances for CORNER=WC, so at TC analyze_power_grid aborts ([ERROR PSM-0021] zero via resistance)
# after the final DEF/ODB/SPEF/netlist are written but before report_metrics runs.
export PWR_NETS_VOLTAGES =
export GND_NETS_VOLTAGES =

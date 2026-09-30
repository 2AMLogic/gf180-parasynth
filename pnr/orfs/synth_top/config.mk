# OpenROAD-flow-scripts design config: synth_top -- THE WHOLE CHIP -- on gf180mcu, 7-track, 5.0 V.
# Run with ../run-orfs.sh synth_top   (see ../README.md, docs/pnr-synth-top.md).
#
# READ THIS BEFORE QUOTING A DIE AREA.
# The die here is a FIXED INPUT, not a result. CORE_UTILIZATION is deliberately NOT set:
# setting a target utilisation makes the die area a restatement of the cell area divided by
# that target, which is an assumption wearing the clothes of a measurement. Here the die is
# fixed and the UTILISATION IS THE MEASURED QUANTITY.
#
# THIS PARAGRAPH IS NO LONGER THE ENFORCEMENT (issue #245). ../area_provenance.py reads this
# file -- and any par_request.json archived with a run -- and ../summarize.py REFUSES to print
# a die/cell-area ratio, exit 2, for a run whose die came from a target. Adding CORE_UTILIZATION
# below will therefore turn the report into a refusal rather than into a confident wrong number.
# ../ladder_dp/config.mk and ../synth_core/config.mk do set it, deliberately, and are refused.
#
# Two dice have been floorplanned with this config, both aspect ~1 on the 7t site grid
# (0.56 x 3.92 um):
#
#   quarterslot  die 1314.88 x 1317.12 um = 1.7319 mm^2, core 1.6734 mm^2
#                the wafer.space gf180mcu quarter slot inside the default pad ring that
#                docs/area-budget.md budgets against.
#                MEASURED: 118.3 % utilisation.  The joined chip does not fit it; its
#                standard cells alone (1.979 mm^2) are larger than the whole die.
#                Reproduce with
#                  ./run-orfs.sh synth_top FLOW_VARIANT=quarterslot \
#                     DIE_AREA='0 0 1314.88 1317.12' CORE_AREA='10.64 11.76 1304.24 1305.36' floorplan
#
#   base         die 1860.88 x 1862.00 um = 3.4650 mm^2, core 1839.6 x 1838.48 = 3.3821 mm^2
#                TWO quarter slots (2 x 1.7319 = 3.4637 mm^2, +0.04 % from site-grid rounding).
#                Chosen by this flow, not by the product: it is the next whole number of the
#                product's own unit that holds the design at a routable density. The
#                utilisation it reaches is the measurement.
export PLATFORM        = gf180
# gf180mcu_fd_sc_mcu7t5v0 (ORFS default is 9t)
export TRACK_OPTION    = 7t
# TC = tt_025C_5v00. ORFS default is BC = ff_n40C_5v50 (optimistic); WC = ss_125C_4v50
export CORNER          = TC
export DESIGN_NAME     = synth_top
export DESIGN_NICKNAME = synth_top

DESIGN_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
RTL := $(abspath $(DESIGN_DIR)/../../../rtl-sketch)
# The chip as it now stands: synth_top -> spi_ctl, drum_regs -> drum_kit (drum_dp + modal_dp),
# voice_dp (recip_div + ladder_dp_n), i2s_tx.  There is no drum_section_placeholder any more,
# so this is the FIRST floorplan of the joined design (docs/integration-area.md section 4.2
# projected it and said plainly that nobody had synthesised it).
export VERILOG_FILES   = $(RTL)/synth_top.v $(RTL)/spi_ctl.v $(RTL)/drum_regs.v $(RTL)/drum_kit.v $(RTL)/drum_dp.v $(RTL)/modal_dp.v $(RTL)/voice_dp.v $(RTL)/recip_div.v $(RTL)/ladder_dp_n.v $(RTL)/i2s_tx.v
export VERILOG_INCLUDE_DIRS = $(RTL)
export SDC_FILE        = $(DESIGN_DIR)/constraint.sdc
# voice_dp's sine (256 x 16) / g (129 x 16) / kc (33 x 16) tables and the ladder's tanh16 are
# $readmemh'd reg arrays; yosys folds them into $mem and the default SYNTH_MEMORY_MAX_BITS=4096
# would turn them into a macro request. Allow them as logic, as every other flow here does.
export SYNTH_MEMORY_MAX_BITS = 65536

# The stock ORFS gf180 platform techmaps adders/latches to hard-coded 9t cell names; use 7t copies.
export ADDER_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_adders.v
export LATCH_MAP_FILE = $(DESIGN_DIR)/../gf180_7t/cells_latch.v
# OpenSTA rejects `signed` port declarations that yosys carries over from the RTL; this wrapper runs the
# stock synth.tcl and strips the qualifier from the gate-level netlist (a no-op for connectivity).
export SYNTH_SCRIPT = $(DESIGN_DIR)/../gf180_7t/synth_unsigned.tcl

export ABC_AREA          = 1
# Fixed floorplan, two quarter slots: die 3.4650 mm^2, core 3285 sites x 469 rows =
# 1839.6 x 1838.48 um = 3.3821 mm^2, margin 10.64 um (19 sites) in x, 11.76 um (3 rows) in y.
export DIE_AREA          = 0 0 1860.88 1862.00
export CORE_AREA         = 10.64 11.76 1850.24 1850.24
# Global-placement target density. Pass it on the make command line as (measured floorplan
# utilisation + 0.10); the resizer and CTS add 1-4 points in this platform's other runs here.
export PLACE_DENSITY     = 0.68
# The image sets LEC_CHECK=1 with a Kepler formal binary that dies with "illegal instruction" under Docker's
# amd64 emulation on Apple Silicon (CTS step, run_lec_test). Not needed for area/timing numbers.
export LEC_CHECK = 0
# Skip the static IR-drop analysis in the finish step: platforms/gf180/setRC.tcl only sets via
# resistances for CORNER=WC, so at TC analyze_power_grid aborts ([ERROR PSM-0021] zero via resistance)
# after the final DEF/ODB/SPEF/netlist are written but before report_metrics runs.
export PWR_NETS_VOLTAGES =
export GND_NETS_VOLTAGES =

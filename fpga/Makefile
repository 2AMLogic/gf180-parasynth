# fpga/Makefile -- the REAL joined instrument on an open FPGA flow, reproducibly.
#
#   make            everything: ECP5 (the board), iCE40 (the comparison),
#                   per-block breakdown, reports/
#   make ecp5       ECP5 25F, ULX3S board wrapper WITH the PLL  <- the playable one
#   make ice40      iCE40 UP5K (iCEBreaker), bare wrapper, no PLL possible
#   OSC2X=1 (default) selects the measured 2x saw chain; OSC2X=0 builds the
#   legacy single-rate waveform for comparison.
#   FILTER2X=1 opts into the reconstructed 2x filter/headroom candidate; it
#   requires OSC2X=1 and remains off until the full scorecard is accepted.
#   make headroom   route fpga/rtl/headroom_top.v -- the same design with the
#                   drum bank grown to the completed 808 (MODES=16 NUMS=11) --
#                   to measure the margin rather than estimate it
#   make blocks     per-block resource breakdown (synthesis only)
#   make clean
#
# THE CONTROL PATH (issue #81) -- no FPGA tools needed, iverilog only:
#   make budget     the SPI link budget in absolute numbers, and the gate
#   make host       the host's unit tests, then a musical fixture through the
#                   PINS and bit-compared against model/synth_top_model.py
#   make host-controls  every deliberately wrong transaction stream that must
#                   turn `make host` red, in parallel, in one turn
#   make play       the demo: the instrument with no keyboard attached
#
# Needs: yosys; nextpnr-ecp5 + prjtrellis for ecp5; nextpnr-ice40 + icestorm for
# ice40. Set NEXTPNR_ECP5 / NEXTPNR_ICE40
# if they are not on PATH. A missing tool skips its target and says so.
#
# WHAT IS IN THIS BUILD. The REAL drum section: drum_regs, drum_kit, drum_dp and
# modal_dp, the engine rtl-sketch/verify_drums.py shows bit-exact against
# model/drums_fx.py. There is no drum_section_placeholder anywhere in it. Every
# figure in reports/ before 2026-09-18 described a drumless chip; those are gone.
#
# Nothing in reports/ claims the design is correct -- these are AREA and TIMING
# numbers. The correctness evidence for this same file set is
# rtl-sketch/verify_synth_top.py, run against the same commit;
# fpga/reports/sim_alongside.txt records that run so rule 3 of
# docs/verification-rules.md is satisfied where the area is quoted.

SHELL     := /bin/sh
FPGA      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT      := $(abspath $(FPGA)/..)
RTL       := $(ROOT)/rtl-sketch
BUILD     := $(FPGA)/build
REPORTS   := $(FPGA)/reports

YOSYS         ?= yosys
NEXTPNR_ICE40 ?= nextpnr-ice40
ICEPACK       ?= icepack
ICETIME       ?= icetime
NEXTPNR_ECP5  ?= nextpnr-ecp5
ECPPACK       ?= ecppack
PYTHON        ?= $(firstword $(wildcard $(ROOT)/.venv/bin/python) $(wildcard /Users/joseph/dev/2amlogic/gf180-monosynth/.venv/bin/python) python3)
OSC2X         ?= 1
FILTER2X      ?= 0
PULSE2X       ?= 0
ifneq ($(filter 1 yes true,$(PULSE2X)),)
ifeq ($(filter 1 yes true,$(OSC2X)),)
$(error PULSE2X=1 requires OSC2X=1)
endif
endif
ifneq ($(filter 1 yes true,$(FILTER2X)),)
ifeq ($(filter 1 yes true,$(OSC2X)),)
$(error FILTER2X=1 requires OSC2X=1)
endif
endif
RTL_DEFS      := $(if $(filter 1 yes true,$(OSC2X)),-DVOICE_OSC_2X,) $(if $(filter 1 yes true,$(FILTER2X)),-DVOICE_FILTER_2X,) $(if $(filter 1 yes true,$(PULSE2X)),-DVOICE_PULSE_2X,)

# The chip. This is rtl-sketch/verify_synth_top.py's own SRCS list -- the set
# that bench elaborates and compares against the model -- and it must stay that
# set, or the thing being routed is not the thing being verified.
# `make srccheck` asserts exactly that, and `all` runs it first.
CORE := $(RTL)/synth_top.v $(RTL)/spi_ctl.v $(RTL)/voice_dp.v $(RTL)/recip_div.v \
        $(RTL)/ladder_dp_n.v $(RTL)/i2s_tx.v \
        $(RTL)/drum_regs.v $(RTL)/drum_kit.v $(RTL)/drum_dp.v $(RTL)/modal_dp.v \
        $(RTL)/osc_2x_saw_bank.v $(RTL)/osc_2x_saw_path.v $(RTL)/polyblep_saw_pair.v \
        $(RTL)/osc_substep_pair.v $(RTL)/decimate_2x_tm_sym.v $(RTL)/rate_conv_2x.v

SRC_ECP5  := $(FPGA)/rtl/ulx3s_top.v $(CORE)
SRC_ICE40 := $(FPGA)/rtl/fpga_top.v  $(CORE)
TOP_ECP5  := ulx3s_top
TOP_ICE40 := fpga_top

# The core clock. On the ULX3S this is what the PLL in fpga/rtl/ulx3s_top.v
# actually produces from the board's 25 MHz oscillator -- 725/59 MHz -- not a
# wish. nextpnr derives it from the PLL's FREQUENCY_PIN_CLKOS attribute, so it
# is NOT passed as --freq for the ECP5 target; passing it would override the
# derived constraint with an assertion. The iCE40 target has no PLL and the
# board must supply the frequency, so there it is a real constraint.
FREQ_MHZ  ?= 12.288

.PHONY: all candidate ecp5 ice40 blocks headroom clean provenance srccheck \
        budget host host-controls play

# ---------------------------------------------------------- the control path
# Issue #81: the keyboard demo drove the floating-point audition engine, so
# everything demonstrated had been made by poking model state. These targets
# drive the verified integer chip through its 48-bit SPI link instead.
budget:
	@$(PYTHON) $(FPGA)/link_budget.py

host:
	@$(PYTHON) -m pytest $(FPGA)/test_spi_host.py -q
	@$(PYTHON) $(FPGA)/verify_fixture.py --outdir $(ROOT)/build/fx-base

## Every wrong transaction stream that must turn `make host` red. A bench that
## has never been watched fail is not a bench (docs/verification-rules.md 1),
## and `no-coef-seq` is the one that matters: it is a host that loads the kit
## and forwards triggers and does nothing else -- the bridge issue #81 warns
## about, and the reason the timed sequences of contract 15.7.1 exist.
host-controls:
	@$(PYTHON) $(ROOT)/tools/run_all.py --timeout 1800 \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong no-coef-seq   --expect-fail --outdir $(ROOT)/build/fx-nocoef" \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong drop-restore  --expect-fail --outdir $(ROOT)/build/fx-droprest" \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong late-window   --expect-fail --outdir $(ROOT)/build/fx-late" \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong no-tom-bend   --expect-fail --outdir $(ROOT)/build/fx-notom" \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong drop-tom-step --expect-fail --outdir $(ROOT)/build/fx-tomstep" \
	  "$(PYTHON) $(FPGA)/verify_fixture.py --wrong burst         --expect-fail --outdir $(ROOT)/build/fx-burst"

play:
	@$(PYTHON) $(FPGA)/play.py --demo --out $(ROOT)/build/demo.wav

# Named sound checkpoint: this target always selects both measured 2x paths.
candidate: srccheck
	@$(PYTHON) $(FPGA)/build_selected.py --nextpnr $(NEXTPNR_ECP5)

all: srccheck provenance ecp5 ice40 blocks
	@echo "--- reports in $(REPORTS)"

$(BUILD) $(REPORTS):
	@mkdir -p $@

# ---- the file list is the verified file list, asserted, not assumed ---------
srccheck:
	@$(PYTHON) -c "import re,sys,os; \
	  s=open('$(RTL)/verify_synth_top.py').read(); \
	  want=set(re.search(r'SRCS = \((.*?)\)', s, re.S).group(1).replace('\"','').replace('\n','').split(',')); \
	  want={w.strip() for w in want if w.strip()}; \
	  got={os.path.basename(f) for f in '$(CORE)'.split()}; \
	  sys.exit(0) if want==got else (print('fpga/Makefile CORE != verify_synth_top.py SRCS'), \
	    print('  only in the bench :', sorted(want-got)), print('  only in the build:', sorted(got-want)), sys.exit(1))"
	@echo "srccheck: the routed file set is the verified file set ($(words $(CORE)) files)"

provenance: | $(REPORTS)
	@{ echo "# FPGA build provenance"; echo; \
	   echo "date        : $$(date -u '+%Y-%m-%dT%H:%M:%SZ')"; \
	   echo "oscillator  : $(if $(filter 1 yes true,$(OSC2X)),2x saw candidate,legacy single-rate waveform) (OSC2X=$(OSC2X))"; \
	   echo "rtl commit  : $$(git -C $(ROOT) rev-parse HEAD)"; \
	   echo "rtl branch  : $$(git -C $(ROOT) rev-parse --abbrev-ref HEAD)"; \
	   echo "tree state  : $$(git -C $(ROOT) status --porcelain -- rtl-sketch fpga/rtl fpga/scripts fpga/boards fpga/Makefile | wc -l | tr -d ' ') modified file(s) among the RTL and the flow (reports/ excluded: this file is one of them)"; \
	   echo "yosys       : $$($(YOSYS) -V 2>/dev/null | head -1)"; \
	   echo "nextpnr-ice40: $$($(NEXTPNR_ICE40) --version 2>&1 | head -1)"; \
	   echo "nextpnr-ecp5 : $$($(NEXTPNR_ECP5) --version 2>&1 | head -1)"; \
	   echo; echo "## what is in this build"; \
	   echo "  THE REAL DRUM SECTION. drum_regs + drum_kit (drum_dp + modal_dp) are"; \
	   echo "  instantiated by rtl-sketch/synth_top.v and are in the file list below."; \
	   echo "  There is no drum_section_placeholder in this tree: grep says so, and"; \
	   echo "  fpga/reports/scope.txt records the check. Every synth_top FPGA figure"; \
	   echo "  published by this repository before 2026-09-18 was a chip WITHOUT them"; \
	   echo "  (provenance of that build: commit 3372e789, branch flow/fpga)."; \
	   echo "  The routed file set equals rtl-sketch/verify_synth_top.py's SRCS; the"; \
	   echo "  \`srccheck\` target asserts it and \`all\` runs it first."; \
	   echo; echo "## the clock"; \
	   echo "  ULX3S: 25 MHz oscillator -> EHXPLLL -> 12.288135593 MHz (+11.03 ppm,"; \
	   echo "  +0.019 cents; fs = 48000.530 Hz). CLKI_DIV=1 CLKFB_DIV=1 CLKOP_DIV=29"; \
	   echo "  CLKOS_DIV=59, VCO 725 MHz, PFD 25 MHz. Reproduce with"; \
	   echo "  fpga/scripts/pll_search.py. The constraint nextpnr met is the one it"; \
	   echo "  DERIVED from that PLL, not one asserted in the LPF."; \
	   echo "  iCE40: no PLL can reach 12.288 MHz from 12 MHz (output range starts at"; \
	   echo "  16 MHz); that target is constrained at $(FREQ_MHZ) MHz and the board"; \
	   echo "  must carry the oscillator."; \
	   echo; echo "## source files (git blob hashes)"; for f in $(SRC_ECP5) $(FPGA)/rtl/fpga_top.v; do \
	     echo "  $$(git -C $(ROOT) hash-object $$f 2>/dev/null || echo '-') $${f#$(ROOT)/}"; done; \
	   echo; echo "NOTHING HERE IS EVIDENCE OF CORRECTNESS. It is an area and timing"; \
	   echo "record. fpga/reports/sim_alongside.txt is the correctness record for the"; \
	   echo "same file set, and names its own commit. See verification-rules.md rule 3."; \
	 } > $(REPORTS)/provenance.txt
	@cat $(REPORTS)/provenance.txt

# ----------------------------------------------------------------- ECP5 25F
ecp5: $(REPORTS)/ecp5_25f.txt
$(REPORTS)/ecp5_25f.txt: $(SRC_ECP5) $(FPGA)/boards/ulx3s.lpf | $(BUILD) $(REPORTS)
	@mkdir -p $(BUILD)
	cd $(RTL) && $(YOSYS) -l $(BUILD)/ecp5_synth.log -p \
	  'read_verilog -defer $(RTL_DEFS) $(SRC_ECP5); \
	   synth_ecp5 -top $(TOP_ECP5) -json $(BUILD)/ecp5.json; \
	   tee -o $(BUILD)/ecp5_stat.txt stat -top $(TOP_ECP5); \
	   write_verilog -noattr $(BUILD)/ecp5_netlist.v'
	-cd $(BUILD) && $(NEXTPNR_ECP5) --25k --package CABGA381 \
	  --json $(BUILD)/ecp5.json --lpf $(FPGA)/boards/ulx3s.lpf \
	  --textcfg $(BUILD)/ecp5.config --seed 1 \
	  --report $(BUILD)/ecp5_report.json --log $(BUILD)/ecp5_pnr.log 2>&1 | tail -40
	-cd $(BUILD) && test -f ecp5.config && $(ECPPACK) ecp5.config ecp5.bit
	@$(FPGA)/scripts/report.sh ecp5 $(TOP_ECP5) > $@
	@cat $@

# ---------------------------------------------------------------- iCE40 UP5K
ice40: $(REPORTS)/ice40_up5k.txt
$(REPORTS)/ice40_up5k.txt: $(SRC_ICE40) $(FPGA)/boards/icebreaker.pcf | $(BUILD) $(REPORTS)
	@mkdir -p $(BUILD)
	cd $(RTL) && $(YOSYS) -l $(BUILD)/ice40_synth.log -p \
	  'read_verilog -defer $(RTL_DEFS) $(SRC_ICE40); \
	   synth_ice40 -top $(TOP_ICE40) -dsp -json $(BUILD)/ice40.json; \
	   tee -o $(BUILD)/ice40_stat.txt stat -top $(TOP_ICE40)'
	-cd $(BUILD) && $(NEXTPNR_ICE40) --up5k --package sg48 \
	  --json $(BUILD)/ice40.json --pcf $(FPGA)/boards/icebreaker.pcf \
	  --asc $(BUILD)/ice40.asc --freq $(FREQ_MHZ) --seed 1 \
	  --report $(BUILD)/ice40_report.json --log $(BUILD)/ice40_pnr.log 2>&1 | tail -40
	-cd $(BUILD) && test -f ice40.asc && $(ICEPACK) ice40.asc ice40.bin && $(ICETIME) -d up5k -mtr $(BUILD)/ice40_icetime.txt ice40.asc
	@$(FPGA)/scripts/report.sh ice40 $(TOP_ICE40) > $@
	@cat $@

# ------------------------------------------------- per-block area breakdown
blocks: $(REPORTS)/blocks.txt
$(REPORTS)/blocks.txt: $(SRC_ECP5) $(FPGA)/scripts/blocks.sh | $(BUILD) $(REPORTS)
	$(FPGA)/scripts/blocks.sh > $@
	@cat $@

# ------------------------------------------------ the completed-808 margin
headroom: $(REPORTS)/ecp5_25f_headroom.txt
$(REPORTS)/ecp5_25f_headroom.txt: $(FPGA)/rtl/headroom_top.v $(SRC_ECP5) $(FPGA)/boards/ulx3s.lpf | $(BUILD) $(REPORTS)
	@mkdir -p $(BUILD)
	cd $(RTL) && $(YOSYS) -l $(BUILD)/ecp5hr_synth.log -p \
	  'read_verilog -defer $(FPGA)/rtl/headroom_top.v $(SRC_ECP5); \
	   synth_ecp5 -top headroom_top -json $(BUILD)/ecp5hr.json; \
	   tee -o $(BUILD)/ecp5hr_stat.txt stat -top headroom_top'
	-cd $(BUILD) && $(NEXTPNR_ECP5) --25k --package CABGA381 \
	  --json $(BUILD)/ecp5hr.json --lpf $(FPGA)/boards/ulx3s.lpf \
	  --textcfg $(BUILD)/ecp5hr.config --seed 1 \
	  --report $(BUILD)/ecp5hr_report.json --log $(BUILD)/ecp5hr_pnr.log 2>&1 | tail -20
	@$(FPGA)/scripts/report.sh ecp5hr headroom_top > $@
	@cat $@

clean:
	rm -rf $(BUILD)

// Checked in deliberately: the upstream template generates this from the Makefile's
// SLOT/SRAM variables. This build targets exactly one slot -- the wafer.space
// half-height (1x0.5) -- so the define is fixed here and version-controlled rather
// than regenerated, and `SRAM_...` is defined only to satisfy chip_top.sv's ifdef
// ladder: this design instantiates no SRAM macro (see librelane/macros/macros_5v.yaml).
`define SLOT_1X0P5
`define SRAM_gf180mcu_fd_ip_sram

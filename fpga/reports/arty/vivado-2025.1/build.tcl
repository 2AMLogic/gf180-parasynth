set_param general.maxThreads 4
read_verilog [list {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/fpga/rtl/arty_a7_top.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/synth_top.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/voice_dp.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/spi_ctl.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/drum_regs.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/drum_kit.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/drum_dp.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/modal_dp.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/i2s_tx.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/ladder_dp_n.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/recip_div.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/osc_2x_saw_bank.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/osc_2x_saw_path.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/polyblep_saw_pair.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/osc_substep_pair.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/decimate_2x_tm_sym.v} {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/rtl-sketch/rate_conv_2x.v}]
read_xdc {/home/ubuntu/ext-timing-rebuild/build/arty/inputs/fpga/boards/arty-a7-100.xdc}
synth_design -top arty_a7_top -part xc7a100tcsg324-1 -generic {SIM_NO_MMCM=0 POR_BITS=12} -flatten_hierarchy none -verilog_define VOICE_OSC_2X -verilog_define VOICE_FILTER_2X
write_checkpoint -force {/home/ubuntu/ext-timing-rebuild/build/arty/synthesized.dcp}
opt_design
place_design
phys_opt_design
route_design
report_utilization -file {/home/ubuntu/ext-timing-rebuild/build/arty/utilization.rpt}
report_timing_summary -check_timing_verbose -file {/home/ubuntu/ext-timing-rebuild/build/arty/timing.rpt}
report_clocks -file {/home/ubuntu/ext-timing-rebuild/build/arty/clocks.rpt}
report_drc -file {/home/ubuntu/ext-timing-rebuild/build/arty/drc.rpt}
write_checkpoint -force {/home/ubuntu/ext-timing-rebuild/build/arty/routed.dcp}
write_bitstream -force {/home/ubuntu/ext-timing-rebuild/build/arty/arty.bit}
exit

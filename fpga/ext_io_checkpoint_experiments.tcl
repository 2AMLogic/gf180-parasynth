#!/usr/bin/env python3
set_param general.maxThreads 4
set dcp /home/ubuntu/parasynth-arty-attempt02/build/arty/routed.dcp
set here /home/ubuntu/ext-timing

proc variant {tag} {
    global dcp
    open_checkpoint $dcp
    puts "JK-VARIANT $tag opened"
}

proc io_reports {tag} {
    global here
    report_timing -delay_type max -max_paths 16 -to [get_ports {i2s_sdata i2s_lrclk spi_miso led[1] led[2] led[3]}] -file $here/${tag}_io_setup.rpt
    report_timing -delay_type min -max_paths 16 -to [get_ports {i2s_sdata i2s_lrclk spi_miso led[1] led[2] led[3]}] -file $here/${tag}_io_hold.rpt
}

# ---- C1: committed budgets (od_min -8.2) -- captures the failing hold paths
variant C1
read_xdc $here/ext_io_hold_diag.xdc
report_timing_summary -check_timing_verbose -file $here/C1_timing.rpt
io_reports C1
close_design

# ---- C2: candidate budgets (od_min 154.560 = half period - tDH budget)
variant C2
read_xdc $here/ext_io_real.xdc
report_timing_summary -check_timing_verbose -file $here/C2_timing.rpt
report_clocks -file $here/C2_clocks.rpt
io_reports C2
puts "JK-C2-GENCLOCK [get_clocks -quiet i2s_bclk_ext]"
close_design

# ---- D: candidate plus a nominal output delay on the forwarded clock itself
variant D
read_xdc $here/ext_io_real.xdc
set_output_delay -clock i2s_bclk_ext -max 0.000 [get_ports i2s_bclk]
set_output_delay -clock i2s_bclk_ext -min 0.000 [get_ports i2s_bclk]
report_timing_summary -check_timing_verbose -file $here/D_timing.rpt
close_design

exit

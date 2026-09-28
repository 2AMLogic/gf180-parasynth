set_param general.maxThreads 4
read_verilog [list {<box>/build/arty-sd/inputs/fpga/rtl/arty_a7_sd_top.v} {<box>/build/arty-sd/inputs/fpga/rtl/i2s_rx.v} {<box>/build/arty-sd/inputs/fpga/rtl/sd_dac.v} {<box>/build/arty-sd/inputs/fpga/rtl/arty_a7_top.v} {<box>/build/arty-sd/inputs/rtl-sketch/synth_top.v} {<box>/build/arty-sd/inputs/rtl-sketch/voice_dp.v} {<box>/build/arty-sd/inputs/rtl-sketch/spi_ctl.v} {<box>/build/arty-sd/inputs/rtl-sketch/drum_regs.v} {<box>/build/arty-sd/inputs/rtl-sketch/drum_kit.v} {<box>/build/arty-sd/inputs/rtl-sketch/drum_dp.v} {<box>/build/arty-sd/inputs/rtl-sketch/modal_dp.v} {<box>/build/arty-sd/inputs/rtl-sketch/i2s_tx.v} {<box>/build/arty-sd/inputs/rtl-sketch/ladder_dp_n.v} {<box>/build/arty-sd/inputs/rtl-sketch/recip_div.v} {<box>/build/arty-sd/inputs/rtl-sketch/osc_2x_saw_bank.v} {<box>/build/arty-sd/inputs/rtl-sketch/osc_2x_saw_path.v} {<box>/build/arty-sd/inputs/rtl-sketch/polyblep_saw_pair.v} {<box>/build/arty-sd/inputs/rtl-sketch/osc_substep_pair.v} {<box>/build/arty-sd/inputs/rtl-sketch/decimate_2x_tm_sym.v} {<box>/build/arty-sd/inputs/rtl-sketch/rate_conv_2x.v} {<box>/build/arty-sd/inputs/rtl-sketch/uart_bridge.v}]
read_xdc {<box>/build/arty-sd/inputs/fpga/boards/arty-a7-100.xdc}
read_xdc {<box>/build/arty-sd/inputs/fpga/boards/arty-a7-100-sd.xdc}
synth_design -top arty_a7_sd_top -part xc7a100tcsg324-1 -generic {SIM_NO_MMCM=0 POR_BITS=12} -flatten_hierarchy none -verilog_define VOICE_OSC_2X -verilog_define VOICE_FILTER_2X
write_checkpoint -force {<box>/build/arty-sd/synthesized.dcp}
set cm_fh [open {<box>/build/arty-sd/constraint_matches.rpt} w]
set cm_bad 0
set cm_objs [get_cells -quiet -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]}]
set cm_n [llength $cm_objs]
set cm_ar 0; foreach c $cm_objs { if {[get_property ASYNC_REG $c] == 1} { incr cm_ar } }
puts $cm_fh "MATCH\t41\tcells\t$cm_n\t6\t$cm_ar\t5fda6dc9729dc173"
if {$cm_n != 6 || $cm_ar != 6} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 41: cells query 5fda6dc9729dc173 matched $cm_n, required 6 (ASYNC_REG $cm_ar)" }
set cm_objs [get_cells -quiet -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[[01]\]}]
set cm_n [llength $cm_objs]
set cm_ar 0; foreach c $cm_objs { if {[get_property ASYNC_REG $c] == 1} { incr cm_ar } }
puts $cm_fh "MATCH\t46\tcells\t$cm_n\t2\t$cm_ar\t71789020be86bc4b"
if {$cm_n != 2 || $cm_ar != 2} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 46: cells query 71789020be86bc4b matched $cm_n, required 2 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/sck_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t47\tpins\t$cm_n\t1\t$cm_ar\t070590953c27a355"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 47: pins query 070590953c27a355 matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/mosi_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t48\tpins\t$cm_n\t1\t$cm_ar\t03a157a020495dab"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 48: pins query 03a157a020495dab matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/csn_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t49\tpins\t$cm_n\t1\t$cm_ar\tef71b7cbbe459ee2"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 49: pins query ef71b7cbbe459ee2 matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t50\tpins\t$cm_n\t1\t$cm_ar\tf1977611219c37db"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 50: pins query f1977611219c37db matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
puts $cm_fh "END\t$cm_bad"
close $cm_fh
if {$cm_bad} { puts "CONSTRAINT_MATCH_REFUSED: $cm_bad constraint(s) bound the wrong objects"; exit 3 }
opt_design
place_design
phys_opt_design
route_design
set rc_fh [open {<box>/build/arty-sd/constraint_matches.rpt} a]
set rc_bad 0
proc rc_put {fh name ok detail} { upvar rc_bad bad; puts $fh "CHECK\t$name\t$ok\t$detail"; if {!$ok} { incr bad; puts "CONSTRAINT_EFFECT_REFUSED $name: $detail" } }
set rc_cells [get_cells -quiet -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[[01]\]}]
set rc_pin [get_pins -quiet -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[0\]/D}]
set rc_s1 [get_cells -quiet -of_objects $rc_pin]
set rc_fan [all_fanout -from [get_ports uart_rxd] -flat -endpoints_only -only_cells]
set rc_s2 {}; if {[llength $rc_s1] == 1} { set rc_s2 [all_fanout -from [get_pins -of_objects $rc_s1 -filter {REF_PIN_NAME == Q}] -flat -endpoints_only -only_cells] }
set rc_ok [expr {[llength $rc_s1] == 1 && [llength $rc_fan] == 1 && [string equal $rc_fan $rc_s1] && [llength $rc_s2] == 1 && [lsort [concat $rc_s1 $rc_s2]] eq [lsort $rc_cells]}]
rc_put $rc_fh uart_stage1 $rc_ok "stage1 $rc_s1; uart_rxd drives $rc_fan; stage2 $rc_s2; async cells $rc_cells"
set rc_p [get_timing_paths -from [get_ports uart_rxd] -max_paths 10 -nworst 10]
set rc_ok [expr {[llength $rc_p] >= 1}]; set rc_d {}
foreach x $rc_p { set e [get_property EXCEPTION $x]; set t [get_property ENDPOINT_PIN $x]; lappend rc_d "$t:$e"; if {$e ne {False Path} || $t ne $rc_pin} { set rc_ok 0 } }
rc_put $rc_fh uart_false $rc_ok "[llength $rc_p] path(s): $rc_d"
set rc_p [get_timing_paths -from $rc_s1 -to $rc_s2 -max_paths 1]
set rc_ok [expr {[llength $rc_p] == 1}]; set rc_d {}
foreach x $rc_p { set e [get_property EXCEPTION $x]; set sl [get_property SLACK $x]; set rc_d "slack $sl exception {$e}"; if {$e ne {} || $sl eq {} || $sl < 0} { set rc_ok 0 } }
rc_put $rc_fh uart_s1s2 $rc_ok "[llength $rc_p] path: $rc_d"
set rc_p [get_timing_paths -from $rc_s2 -max_paths 20 -nworst 1]
set rc_ok [expr {[llength $rc_p] >= 1}]; set rc_w {}
foreach x $rc_p { set e [get_property EXCEPTION $x]; set sl [get_property SLACK $x]; if {$rc_w eq {} || ($sl ne {} && $sl < $rc_w)} { set rc_w $sl }; if {$e ne {} || $sl eq {} || $sl < 0} { set rc_ok 0 } }
rc_put $rc_fh uart_down $rc_ok "[llength $rc_p] path(s), worst slack $rc_w, none excepted"
puts $rc_fh "ROUTE_END\t$rc_bad"
close $rc_fh
report_exceptions -file {<box>/build/arty-sd/exceptions.rpt}
if {$rc_bad} { puts "CONSTRAINT_EFFECT_REFUSED: $rc_bad check(s) failed"; exit 3 }
report_utilization -file {<box>/build/arty-sd/utilization.rpt}
report_timing_summary -check_timing_verbose -file {<box>/build/arty-sd/timing.rpt}
report_clocks -file {<box>/build/arty-sd/clocks.rpt}
report_drc -file {<box>/build/arty-sd/drc.rpt}
write_checkpoint -force {<box>/build/arty-sd/routed.dcp}
write_bitstream -force {<box>/build/arty-sd/arty.bit}
exit

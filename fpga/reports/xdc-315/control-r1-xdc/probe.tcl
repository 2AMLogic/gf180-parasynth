open_checkpoint {/home/ubuntu/work/r1-image-archive/r1-routed-0f81026e.dcp}

set cm_fh [open {/home/ubuntu/work/xdc-315/build/xdc-probe/control-r1-xdc/constraint_matches.rpt} w]
set cm_bad 0
set cm_objs [get_cells -quiet -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]}]
set cm_n [llength $cm_objs]
set cm_ar 0; foreach c $cm_objs { if {[get_property ASYNC_REG $c] == 1} { incr cm_ar } }
puts $cm_fh "MATCH\t41\tcells\t$cm_n\t6\t$cm_ar\t5fda6dc9729dc173"
if {$cm_n != 6 || $cm_ar != 6} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 41: cells query 5fda6dc9729dc173 matched $cm_n, required 6 (ASYNC_REG $cm_ar)" }
set cm_objs [get_cells -quiet -hier -regexp {.*g_uart/u_uart/rx_q_reg\[[01]\]}]
set cm_n [llength $cm_objs]
set cm_ar 0; foreach c $cm_objs { if {[get_property ASYNC_REG $c] == 1} { incr cm_ar } }
puts $cm_fh "MATCH\t42\tcells\t$cm_n\t2\t$cm_ar\t965b747a367f05c8"
if {$cm_n != 2 || $cm_ar != 2} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 42: cells query 965b747a367f05c8 matched $cm_n, required 2 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/sck_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t43\tpins\t$cm_n\t1\t$cm_ar\t070590953c27a355"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 43: pins query 070590953c27a355 matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/mosi_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t44\tpins\t$cm_n\t1\t$cm_ar\t03a157a020495dab"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 44: pins query 03a157a020495dab matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*u_spi/csn_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t45\tpins\t$cm_n\t1\t$cm_ar\tef71b7cbbe459ee2"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 45: pins query ef71b7cbbe459ee2 matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
set cm_objs [get_pins -quiet -hier -regexp {.*g_uart/u_uart/rx_q_reg\[0\]/D}]
set cm_n [llength $cm_objs]
set cm_ar -
puts $cm_fh "MATCH\t46\tpins\t$cm_n\t1\t$cm_ar\td1e7f9feb51a2827"
if {$cm_n != 1} { incr cm_bad; puts "CONSTRAINT_MATCH_REFUSED line 46: pins query d1e7f9feb51a2827 matched $cm_n, required 1 (ASYNC_REG $cm_ar)" }
puts $cm_fh "END\t$cm_bad"
close $cm_fh
if {$cm_bad} { puts "CONSTRAINT_MATCH_REFUSED: $cm_bad constraint(s) bound the wrong objects"; exit 3 }
set rc_fh [open {/home/ubuntu/work/xdc-315/build/xdc-probe/control-r1-xdc/constraint_matches.rpt} a]
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
report_exceptions -file {/home/ubuntu/work/xdc-315/build/xdc-probe/control-r1-xdc/exceptions.rpt}
if {$rc_bad} { puts "CONSTRAINT_EFFECT_REFUSED: $rc_bad check(s) failed"; exit 3 }
exit 0

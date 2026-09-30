
if {![string match "2025.1*" [version -short]]} { puts "REFUSED_VERSION [version -short]"; exit 3 }
open_checkpoint {/home/ubuntu/work/r1-image-build/arty/routed.dcp}
set fh [open {/home/ubuntu/work/r1-image-build/ext-io/ext_io_paths.txt} w]
proc measure {fh variant ports} {
  foreach p $ports {
    foreach kind {max min} {
      set tp [get_timing_paths -to [get_ports $p] -delay_type $kind -max_paths 1 -nworst 1]
      if {[llength $tp] == 0} { puts $fh "PATH\t$variant\t$p\t$kind\tNONE\t\t\t\t"; continue }
      puts $fh "PATH\t$variant\t$p\t$kind\t[get_property SLACK $tp]\t[get_property REQUIREMENT $tp]\t[get_property DATAPATH_DELAY $tp]\t[get_property STARTPOINT_CLOCK $tp]\t[get_property ENDPOINT_CLOCK $tp]"
    }
  }
}
set outs {{i2s_lrclk} {i2s_sdata} {spi_miso} {led[1]} {led[2]} {led[3]} {uart_txd}}
measure $fh clean $outs
foreach p {spi_sck spi_mosi spi_cs_n uart_rxd btn_reset} {
  set tp [get_timing_paths -from [get_ports $p] -delay_type max -max_paths 1 -nworst 1]
  if {[llength $tp] == 0} { puts $fh "INPUT\t$p\tNONE"; continue }
  puts $fh "INPUT\t$p\t[get_property SLACK $tp]\t[get_property ENDPOINT_PIN $tp]"
}
set objs [get_cells -quiet -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]}]
puts $fh "SYNC\txdc:spi_async_reg\t[llength $objs]\t[join $objs ,]"
set objs [get_pins -quiet -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[0\]/D}]
puts $fh "SYNC\txdc:spi_false_path_d\t[llength $objs]\t[join $objs ,]"
set objs [get_cells -quiet -hier -regexp {.*g_uart/u_uart/rx_q_reg\[[01]\]}]
puts $fh "SYNC\txdc:uart_async_reg\t[llength $objs]\t[join $objs ,]"
set objs [get_pins -quiet -hier -regexp {.*g_uart/u_uart/rx_q_reg\[0\]/D}]
puts $fh "SYNC\txdc:uart_false_path_d\t[llength $objs]\t[join $objs ,]"
set objs [get_cells -quiet -hier -regexp {.*u_uart/rx_q_reg\[[01]\]}]
puts $fh "SYNC\tnetlist:uart_rx_q\t[llength $objs]\t[join $objs ,]"
foreach c [get_cells -quiet -hier -regexp {.*u_(uart|spi)/(rx_q|sck_q|mosi_q|csn_q)_reg\[[01]\]}] {
  puts $fh "ASYNC\t$c\t[get_property ASYNC_REG $c]"
}
set_output_delay -clock [get_clocks {hardware_clock.clock_raw}] -max 80.000 [get_ports spi_miso]
measure $fh IMPOSSIBLE_MISO $outs
close $fh
puts "EXTRACT_DONE"
exit

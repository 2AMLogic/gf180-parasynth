// deadline_mon_top.v -- deadline_mon.vh attached to the SPI pin bench
// (tb_top_bx.dut). An extra simulation root; see deadline_mon.vh.
`define DL_DUT tb_top_bx.dut
module deadline_mon_top;
`include "deadline_mon.vh"
endmodule
`undef DL_DUT

// deadline_mon_arty.v -- deadline_mon.vh attached to the Arty wrapper's UART
// bench (tb_uart_bx.board.u_synth). An extra simulation root; see deadline_mon.vh.
`define DL_DUT tb_uart_bx.board.u_synth
module deadline_mon_arty;
`include "deadline_mon.vh"
endmodule
`undef DL_DUT

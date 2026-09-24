// Start-red stub for the UART bridge bench: the wrapper's ports (including the
// reserved UART pins), but none of the behaviour. A bench run against this
// must FAIL -- that is the run that shows the bench can fail.
module arty_a7_top #(parameter SIM_NO_MMCM=0, parameter POR_BITS=12,
                     parameter UART_BAUD=115200, parameter UART_EVQ_DEPTH=64,
                     parameter UART_WRQ_DEPTH=8)(
    input wire clk_100mhz, input wire btn_reset, output wire [3:0] led,
    input wire spi_sck, spi_mosi, spi_cs_n, output wire spi_miso,
    input wire uart_rxd, output wire uart_txd,
    output wire i2s_bclk, i2s_lrclk, i2s_sdata
);
    wire core_rst_n = ~btn_reset;
    assign led = 0;
    assign uart_txd = 1'b1;                 // idle, forever: no bridge behind it
    assign i2s_sdata = 0;
    synth_top #(.WITH_UART(0)) u_synth(.clk(clk_100mhz), .rst_n_pad(core_rst_n),
        .sck(spi_sck), .mosi(spi_mosi), .cs_n(spi_cs_n), .miso(spi_miso),
        .uart_rxd(uart_rxd), .uart_txd(),
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata());
endmodule

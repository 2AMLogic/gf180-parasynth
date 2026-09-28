// Start-red stub for the pads bench: the pads wrapper's ports, a core so the
// bench's frame anchor exists, and none of the pads behaviour (no sequencer,
// no boot ROM, no source select). A bench run against this must FAIL -- that
// is the run that shows the bench can fail.
module arty_a7_pads_top #(parameter SIM_NO_MMCM=0, parameter POR_BITS=12,
                          parameter RST_HOLD_BITS=20,
                          parameter UART_BAUD=115200, parameter UART_EVQ_DEPTH=64,
                          parameter UART_WRQ_DEPTH=8, parameter DB_CYCLES=61440)(
    input wire clk_100mhz, input wire [3:0] btn, input wire sw_pads,
    input wire sw_reset, output wire [3:0] led,
    input wire spi_sck, spi_mosi, spi_cs_n, output wire spi_miso,
    input wire uart_rxd, output wire uart_txd,
    output wire i2s_bclk, i2s_lrclk, i2s_sdata
);
    reg [POR_BITS-1:0] por_count = 0;
    always @(posedge clk_100mhz) if (!(&por_count)) por_count <= por_count + 1'b1;
    wire core_rst_n = &por_count;
    assign led = 0;
    synth_top #(.WITH_UART(1), .UART_BAUD(UART_BAUD)) u_synth(
        .clk(clk_100mhz), .rst_n_pad(core_rst_n),
        .sck(spi_sck), .mosi(spi_mosi), .cs_n(spi_cs_n), .miso(spi_miso),
        .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata(i2s_sdata));
endmodule

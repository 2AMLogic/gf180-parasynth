// Arty A7-100T: 100 MHz / 5 * 48 / 78.125 = 12.288 MHz (48 kHz audio).
// A9 = uart_rxd (FTDI TX -> FPGA), D10 = uart_txd (FPGA TX -> FTDI): the
// USB-UART control bridge alongside the external SPI header.
module arty_a7_top #(parameter SIM_NO_MMCM=0, parameter POR_BITS=12,
                     parameter UART_BAUD=115200, parameter UART_EVQ_DEPTH=64,
                     parameter UART_WRQ_DEPTH=8)(
    input wire clk_100mhz, input wire btn_reset, output wire [3:0] led,
    input wire spi_sck, spi_mosi, spi_cs_n, output wire spi_miso,
    input wire uart_rxd, output wire uart_txd,
    output wire i2s_bclk, i2s_lrclk, i2s_sdata
);
    wire core_clk, clock_locked;
    generate if (SIM_NO_MMCM) begin: simulation_clock
        // Testbench supplies 12.288 MHz here. Never select for a bitstream.
        assign core_clk = clk_100mhz;
        assign clock_locked = 1'b1;
    end else begin: hardware_clock
        wire feedback_raw, feedback, clock_raw;
        MMCME2_BASE #(
            .BANDWIDTH("OPTIMIZED"), .CLKIN1_PERIOD(10.0),
            .DIVCLK_DIVIDE(5), .CLKFBOUT_MULT_F(48.0),
            .CLKOUT0_DIVIDE_F(78.125), .CLKOUT0_DUTY_CYCLE(0.5),
            .STARTUP_WAIT("FALSE")
        ) mmcm (
            .CLKIN1(clk_100mhz), .CLKFBIN(feedback),
            .CLKFBOUT(feedback_raw), .CLKOUT0(clock_raw),
            .LOCKED(clock_locked), .PWRDWN(1'b0), .RST(btn_reset)
        );
        BUFG feedback_buffer (.I(feedback_raw), .O(feedback));
        BUFG core_buffer (.I(clock_raw), .O(core_clk));
    end endgenerate
    wire reset_request = btn_reset | ~clock_locked;
    // Asynchronous assertion; deassertion waits for lock and two core clocks.
    (* ASYNC_REG = "TRUE" *) reg [1:0] ready_sync = 0;
    always @(posedge core_clk or posedge reset_request)
        if (reset_request) ready_sync <= 0;
        else ready_sync <= {ready_sync[0], 1'b1};
    reg [POR_BITS-1:0] por_count = 0;
    always @(posedge core_clk or posedge reset_request)
        if (reset_request) por_count <= 0;
        else if (!ready_sync[1]) por_count <= 0;
        else if (!(&por_count)) por_count <= por_count + 1'b1;
    wire core_rst_n = &por_count;
    reg [23:0] heartbeat = 0;
    always @(posedge core_clk)
        if (!core_rst_n) heartbeat <= 0;
        else heartbeat <= heartbeat + 1'b1;
    assign led = {i2s_lrclk, heartbeat[23], core_rst_n, clock_locked};
    wire synth_data;
`ifdef INJECT_BUG_ARTY_MOSI_ZERO
    wire synth_mosi = 1'b0;
`else
    wire synth_mosi = spi_mosi;
`endif
    synth_top #(.WITH_UART(1), .UART_BAUD(UART_BAUD),
                .UART_EVQ_DEPTH(UART_EVQ_DEPTH), .UART_WRQ_DEPTH(UART_WRQ_DEPTH)
    ) u_synth(.clk(core_clk), .rst_n_pad(core_rst_n),
        .sck(spi_sck), .mosi(synth_mosi), .cs_n(spi_cs_n), .miso(spi_miso),
        .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata(synth_data));
`ifdef INJECT_BUG_ARTY_SDATA_ZERO
    assign i2s_sdata = 1'b0;
`else
    assign i2s_sdata = synth_data;
`endif
endmodule

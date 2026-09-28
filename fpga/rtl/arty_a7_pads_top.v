// arty_a7_pads_top.v -- the pads demo (#449): BTN0..BTN3 play four 808 voices
// with no laptop attached. A SEPARATE wrapper: arty_a7_top.v and synth_top.v
// are unchanged, so no published evidence moves, and this is never a release
// image.
//
//   SW0 (A8)   source select: down = host mode (the FTDI's uart_rxd drives the
//              bridge, exactly arty_a7_top); up = pads mode (the kit is loaded
//              from the boot ROM, then the buttons play).
//   SW3 (A10)  reset: FLIP IT, either direction. BTN0 was the reset on
//              arty_a7_top and is a pad here. A toggle rather than a level so
//              that no switch position can leave the board silently held in
//              reset -- the switch's position means nothing, only its motion.
//   BTN0..3    BD, SD, CH, CP (fpga/pads_rom.VOICES).
//   LD4..LD7   led[0] clock locked, led[1] core out of reset, led[2] pads mode
//              ready (kit loaded), led[3] a press was accepted (~170 ms).
//
// Clocking and the core reset release are arty_a7_top's, line for line; only
// the reset REQUEST differs (the SW3 toggle, not BTN0), and the MMCM's RST is
// tied off rather than wired to a pad. SPI and the FTDI TX are kept as they
// were, so host mode is the full arty_a7_top link.
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
            .LOCKED(clock_locked), .PWRDWN(1'b0), .RST(1'b0)
        );
        BUFG feedback_buffer (.I(feedback_raw), .O(feedback));
        BUFG core_buffer (.I(clock_raw), .O(core_clk));
    end endgenerate

    // ---- SW3: a toggle is a reset request. On the CORE clock, deliberately:
    // the request clears core-clock flops asynchronously, and a 100 MHz source
    // would make that a clk100 -> core_clk recovery arc between two MMCM-related
    // clocks. These flops take no reset (they make it); before lock the
    // request is ~clock_locked anyway. At 12.288 MHz the hold is 2^20 cycles
    // (85 ms), re-armed by every bounce of the slide switch.
    (* ASYNC_REG = "TRUE" *) reg [1:0] swr_q = 2'b00;
    reg swr_last = 1'b0;
    reg [RST_HOLD_BITS-1:0] swr_hold = {RST_HOLD_BITS{1'b0}};
    reg sw_reset_req = 1'b0;
    always @(posedge core_clk) begin
        swr_q <= {swr_q[0], sw_reset};
        swr_last <= swr_q[1];
        if (swr_q[1] != swr_last) swr_hold <= {RST_HOLD_BITS{1'b1}};
        else if (swr_hold != 0) swr_hold <= swr_hold - 1'b1;
        sw_reset_req <= (swr_hold != 0);
    end
    wire reset_request = sw_reset_req | ~clock_locked;
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

    wire core_rxd, pads_ready, pads_active;
    pads_seq #(.CLK_HZ(12_288_000), .BAUD(UART_BAUD), .DB_CYCLES(DB_CYCLES)) u_pads (
        .clk(core_clk), .rst_n_pad(core_rst_n), .btn(btn), .sel_pads(sw_pads),
        .ext_rxd(uart_rxd), .core_rxd(core_rxd), .mode_pads(),
        .ready(pads_ready), .active(pads_active));

    synth_top #(.WITH_UART(1), .UART_BAUD(UART_BAUD),
                .UART_EVQ_DEPTH(UART_EVQ_DEPTH), .UART_WRQ_DEPTH(UART_WRQ_DEPTH)
    ) u_synth(.clk(core_clk), .rst_n_pad(core_rst_n),
        .sck(spi_sck), .mosi(spi_mosi), .cs_n(spi_cs_n), .miso(spi_miso),
        .uart_rxd(core_rxd), .uart_txd(uart_txd),
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata(i2s_sdata));
    assign led = {pads_active, pads_ready, core_rst_n, clock_locked};
endmodule

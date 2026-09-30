// Arty A7-100T with a no-DAC demo output (#406): everything arty_a7_top does
// (SPI on JB, UART on the USB bridge, I2S on JA for the PCM5102), plus a
// one-bit sigma-delta stream on Pmod JD for a resistor-and-capacitor filter.
//
//   sd_left  = JD1 (D4), sd_right = JD2 (D3), ground on JD5 or JD11.
//
// JA IS LAID OUT FOR A PCM5102 BOARD PLUGGED STRAIGHT IN (the purple
// "SCK BCK DIN LCK GND VIN" breakout), NOT for the R0/R1 jumper wiring:
//
//   JA1 (G13) dac_sck = 0     JA2 (B11) i2s_bclk     JA3 (A11) i2s_sdata
//   JA4 (D12) i2s_lrclk       JA5 GND                JA6 3.3 V
//
// SCK low makes the PCM5102 derive its clock from BCK. The move is made in
// fpga/boards/arty-a7-100-sd.xdc (the shared XDC is untouched), so the
// published images keep BCLK/LRCLK/DATA on JA1/JA2/JA3.
//
// MONO, on both pins. The core's sample is mono by contract (both I2S slots
// carry the same word), so one modulator drives both pads: a stereo line
// input hears it in both channels, and either pin alone is the whole signal.
//
// A DEMO PATH, NOT A MEASUREMENT PATH, and never published as a release
// image: fpga/publish_arty.py's VERIFICATION_BY_WRAPPER has no entry for
// this wrapper, so publication refuses it. Recordings (#208) stay on I2S.
//
// Structure. arty_a7_top is instantiated UNCHANGED, so its source (and the
// evidence bound to it by hash) does not move. It is built here with
// SIM_NO_MMCM=1, which means "the clock arrives already made": this wrapper
// owns the MMCM -- the same parameters, in a generate block of the same name,
// so the shared XDC's `hardware_clock.mmcm/CLKOUT0` and generated clock
// `hardware_clock.clock_raw` bind here exactly as they do in arty_a7_top --
// and folds !LOCKED into the button reset. That reproduces arty_a7_top's
// reset contract (asynchronous assertion; release after lock, two core clocks
// and the POR count); fpga/test_arty_sd_top.py drives it both ways.
//
// The modulator reads the I2S WIRE through i2s_rx, in the core clock domain,
// so the stream on JD is decoded from the same bits the PCM5102 receives.
module arty_a7_sd_top #(parameter SIM_NO_MMCM=0, parameter POR_BITS=12,
                        parameter UART_BAUD=115200, parameter UART_EVQ_DEPTH=64,
                        parameter UART_WRQ_DEPTH=8)(
    input wire clk_100mhz, input wire btn_reset, output wire [3:0] led,
    input wire spi_sck, spi_mosi, spi_cs_n, output wire spi_miso,
    input wire uart_rxd, output wire uart_txd,
    output wire i2s_bclk, i2s_lrclk, i2s_sdata,
    output wire sd_left, sd_right,
    output wire dac_sck                 // JA1: held low for a plugged-in PCM5102
);
    assign dac_sck = 1'b0;
    wire core_clk, clock_locked;
    generate if (SIM_NO_MMCM) begin: simulation_clock
        // Testbench supplies 12.288 MHz here. Never select for a bitstream.
        assign core_clk = clk_100mhz;
        assign clock_locked = 1'b1;
    end else begin: hardware_clock
        // arty_a7_top.v's MMCM, parameter for parameter (test_arty_sd_top.py
        // compares the two texts).
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

    arty_a7_top #(.SIM_NO_MMCM(1), .POR_BITS(POR_BITS), .UART_BAUD(UART_BAUD),
                  .UART_EVQ_DEPTH(UART_EVQ_DEPTH), .UART_WRQ_DEPTH(UART_WRQ_DEPTH)
    ) u_arty (
        .clk_100mhz(core_clk), .btn_reset(reset_request), .led(led),
        .spi_sck(spi_sck), .spi_mosi(spi_mosi), .spi_cs_n(spi_cs_n), .spi_miso(spi_miso),
        .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .i2s_bclk(i2s_bclk), .i2s_lrclk(i2s_lrclk), .i2s_sdata(i2s_sdata));

    // The demo path's own reset: asserted with the core's, released two core
    // clocks after it (the modulator starts from zeroed integrators).
    (* ASYNC_REG = "TRUE" *) reg [1:0] sd_ready = 0;
    always @(posedge core_clk or posedge reset_request)
        if (reset_request) sd_ready <= 0;
        else sd_ready <= {sd_ready[0], 1'b1};
    wire sd_rst_n = sd_ready[1];

    wire signed [15:0] sd_sample;
    i2s_rx u_rx (.clk(core_clk), .rst_n(sd_rst_n), .bclk(i2s_bclk), .lrclk(i2s_lrclk),
                 .sdata(i2s_sdata), .sample(sd_sample));
    wire pdm;
    sd_dac u_sd (.clk(core_clk), .rst_n(sd_rst_n), .sample(sd_sample), .pdm(pdm));
    assign sd_left = pdm;
    assign sd_right = pdm;
endmodule

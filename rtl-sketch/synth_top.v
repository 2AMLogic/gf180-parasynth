// synth_top.v -- the chip: docs/ARCHITECTURE.md as RTL.
//
//   SPI pins -> spi_ctl (link, 48-bit frames, 4-deep write queue, drain at the tick)
//            -> SEC = 0: voice_dp (three oscillators, mixer, envelopes, cutoff
//                        ROMs, the verified ladder_dp_n -- two filter contexts:
//                        the voice's and the drum filter's -- VCA, volume,
//                        the master mix)
//            -> SEC = 1: drum_regs -> drum_kit (drum_dp + modal_dp), the REAL
//                        drum engine of DR 0008
//            -> i2s_tx  (BCLK / LRCLK / SDATA)
//
//   WITH_UART=1 adds a second front end for the SAME register-write port:
//   uart_bridge (rtl-sketch/uart_bridge.v), fed from the reserved UART pins.
//   The mux gives the SPI drain strict priority -- the bridge may present a
//   write only in window cycles [UART_WIN_LO, GO_CYCLE), which begin after the
//   SPI drain's last possible write (the snapshot at the tick pops at most the
//   queue's four entries, so spi wr_valid cannot assert past cycle 5) -- so
//   the two sources can never collide, whatever either link does. The bridge
//   gets two write slots per frame, due-scheduled events before live writes.
//
// WHAT IS REAL AND WHAT IS A PLACEHOLDER -- read this before quoting a number:
//   There is no placeholder left in this file. drum_section_placeholder is
//   gone; `drum_kit` is the engine verify_drums.py shows bit-exact against
//   model/drums_fx.py, and drum_regs.v is its control image.
//
//   REAL, verified bit-exact against a model:  ladder_dp_n (inside voice_dp; each channel),
//                                              modal_dp and drum_dp (inside drum_kit)
//   REAL, verified through the pins:           spi_ctl (verify_ctl.py: every write of both
//                                              models' images arrives intact), this file and
//                                              i2s_tx (verify_synth_top.py, which decodes the
//                                              I2S wire and compares it against the MODEL)
//   REAL, verified through the pins:           uart_bridge (fpga/verify_uart_bridge.py: the Arty
//                                              wrapper's UART pins, device-scheduled events vs
//                                              the contract and the model) -- WITH_UART=1 only
//
// One clock (12.288 MHz, 256 cycles per 48 kHz frame), one reset (the pad,
// synchronised), synchronous resets throughout; no derived clocks -- BCLK and
// LRCLK are bits of the frame cycle counter. Frame schedule: ARCHITECTURE.md
// section 5; `go` at cycle GO_CYCLE starts every block after the write drain.
//
// TWO SOFT RESETS, one per page, because there are two register images:
// SEC = 0 address 0x23 resets the voice datapath (contract 14), SEC = 1
// address 0xFF resets the drum section (contract 15.8). Neither touches the
// link or the queue, so writes queued behind one still apply, in order.
`default_nettype none
module synth_top #(
    parameter GO_CYCLE = 8,
    parameter ENVS  = 18,
    parameter PATHS = 23,
    parameter MODES = 16,
    parameter NUMS  = 11,
    parameter STOPS = 11,
    parameter WITH_UART = 0,          // second control front end on the UART pins
    parameter UART_BAUD = 115_200,
    parameter UART_EVQ_DEPTH = 64,
    parameter UART_WRQ_DEPTH = 8
)(
    input  wire clk,          // 12.288 MHz
    input  wire rst_n_pad,    // active-low reset from the MCU
    // control link (DR 0007)
    input  wire sck,
    input  wire mosi,
    input  wire cs_n,
    output wire miso,
    // control link (USB-UART bridge; unused when WITH_UART = 0)
    input  wire uart_rxd,
    output wire uart_txd,
    // audio (contract 13)
    output wire bclk,
    output wire lrclk,
    output wire sdata
);
    // ---- reset synchroniser ------------------------------------------------------
    reg [1:0] rst_q;
    always @(posedge clk) rst_q <= {rst_q[0], rst_n_pad};
    wire rst_n = rst_q[1];

    // ---- the frame: cycle counter, tick, go, frame counter, overrun flag ----------
    reg [7:0]  cyc;
    reg [15:0] frame;
    reg        overrun;
    wire tick = (cyc == 8'd0);
    wire go   = (cyc == GO_CYCLE[7:0]);
    wire voice_busy, drum_busy;
    always @(posedge clk) begin
        if (!rst_n) begin cyc <= 8'd0; frame <= 16'd0; overrun <= 1'b0; end
        else begin
            cyc <= cyc + 8'd1;
            if (tick) frame <= frame + 16'd1;
            if (tick && (voice_busy || drum_busy)) overrun <= 1'b1;     // a defect if it ever sets
        end
    end

    // ---- the link and the write port (DR 0007 revision 2) ----------------------------
    wire        spi_wr_valid, spi_wr_flag, spi_wr_sec;
    wire [7:0]  spi_wr_addr;
    wire [31:0] spi_wr_data;
    wire        u_wr_valid, u_wr_flag, u_wr_sec;
    wire [7:0]  u_wr_addr;
    wire [31:0] u_wr_data;
    wire        fresh, overflow;
    wire [2:0]  q_count;
    spi_ctl u_spi (.clk(clk), .rst_n(rst_n), .sck(sck), .mosi(mosi), .cs_n(cs_n), .miso(miso),
                   .tick(tick), .frame(frame), .overrun(overrun),
                   .wr_valid(spi_wr_valid), .wr_flag(spi_wr_flag), .wr_sec(spi_wr_sec),
                   .wr_addr(spi_wr_addr), .wr_data(spi_wr_data),
                   .fresh(fresh), .overflow(overflow), .q_count(q_count));

    generate if (WITH_UART) begin : g_uart
        // The bridge may present writes only in the window cycles after the
        // SPI drain's last possible write: the drain pops at most the queue's
        // four entries, so spi_wr_valid cannot assert past cycle 1 + 4 = 5.
        // Two slots per frame, due-scheduled events before live writes.
        localparam integer UART_WIN_LO = GO_CYCLE - 2;
        wire uart_grant = (cyc >= UART_WIN_LO) && (cyc < GO_CYCLE);
        uart_bridge #(.CLK_HZ(12_288_000), .BAUD(UART_BAUD),
                      .EVQ_DEPTH(UART_EVQ_DEPTH), .WRQ_DEPTH(UART_WRQ_DEPTH)) u_uart (
            .clk(clk), .rst_n(rst_n), .rx(uart_rxd), .tx(uart_txd),
            .frame(frame), .grant(uart_grant),
            .wr_valid(u_wr_valid), .wr_flag(u_wr_flag), .wr_sec(u_wr_sec),
            .wr_addr(u_wr_addr), .wr_data(u_wr_data),
            .evq_count(), .wrq_count(),
            .evq_overflow(), .wrq_overflow(), .late_seen(), .resync_seen());
    end else begin : g_no_uart
        assign uart_txd = 1'b1;
        assign u_wr_valid = 1'b0;
        assign u_wr_flag = 1'b0;  assign u_wr_sec = 1'b0;
        assign u_wr_addr = 8'h00; assign u_wr_data = 32'h0;
    end endgenerate
    // the two sources are exclusive by construction (window vs drain); the
    // data mux picks the live source, never the idle one's X
    wire        wr_valid = spi_wr_valid | u_wr_valid;
    wire        wr_flag  = u_wr_valid ? u_wr_flag  : spi_wr_flag;
    wire        wr_sec   = u_wr_valid ? u_wr_sec   : spi_wr_sec;
    wire [7:0]  wr_addr  = u_wr_valid ? u_wr_addr  : spi_wr_addr;
    wire [31:0] wr_data  = u_wr_valid ? u_wr_data  : spi_wr_data;

    wire wr_voice = wr_valid && !wr_sec;                 // SEC = 0: the voice and master page
    wire wr_drum  = wr_valid &&  wr_sec;                 // SEC = 1: the drum section's page
    // RESET (SEC 0, 0x23) resets every datapath register of contract 14 and nothing on the link
    wire soft_rst  = wr_voice && (wr_addr == 8'h23);
    wire rst_n_dp  = rst_n & ~soft_rst;

    // ---- the drum section: its control image and the engine (DR 0008) ----------------
    wire                  d_soft_rst;
    wire [STOPS-1:0]      d_stops;
    wire [STOPS*16-1:0]   d_accent;
    wire [6*24-1:0]       d_osc;
    wire [ENVS*27-1:0]    d_ectl;
    wire [ENVS*24-1:0]    d_peak;
    wire [ENVS*16-1:0]    d_rate;
    wire [PATHS*25-1:0]   d_path;
    wire [MODES*26-1:0]   d_a1, d_a2;
    wire [MODES*16-1:0]   d_amp;
    wire [MODES*2-1:0]    d_num;
    wire rst_n_drum = rst_n & ~d_soft_rst;
    drum_regs #(.ENVS(ENVS), .PATHS(PATHS), .MODES(MODES), .STOPS(STOPS)) u_dregs (
        .clk(clk), .rst_n(rst_n_drum), .wr_valid(wr_drum), .wr_addr(wr_addr), .wr_data(wr_data),
        .soft_rst(d_soft_rst),
        .stops(d_stops), .accent_bus(d_accent), .osc_inc_bus(d_osc),
        .env_ctl_bus(d_ectl), .env_peak_bus(d_peak), .env_rate_bus(d_rate), .path_bus(d_path),
        .a1_bus(d_a1), .a2_bus(d_a2), .amp_bus(d_amp), .num_bus(d_num));

    wire signed [21:0] dmix;                 // the mix bus (15.5), 22 bits, exact:
                                             // 23 paths x 17 bits needs 22 (revision 10)
    wire signed [18:0] body;                 // the body bus (15.6), 19 bits Q4.15
    wire               mix_valid, body_valid;
    drum_kit #(.ENVS(ENVS), .PATHS(PATHS), .MODES(MODES), .NUMS(NUMS), .STOPS(STOPS)) u_drums (
        .clk(clk), .rst_n(rst_n_drum), .frame_tick(go),
        .stops(d_stops), .accent_bus(d_accent), .osc_inc_bus(d_osc),
        .env_ctl_bus(d_ectl), .env_peak_bus(d_peak), .env_rate_bus(d_rate), .path_bus(d_path),
        .a1_bus(d_a1), .a2_bus(d_a2), .amp_bus(d_amp), .num_bus(d_num),
        .mix_out(dmix), .mix_valid(mix_valid), .body_out(body), .body_valid(body_valid));

    // `drum_done` is the handshake of ARCHITECTURE 4.4: cleared at `go`, set only
    // when THIS frame's two buses are on the wires. The master mix waits for it.
    reg drum_done, drum_run;
    assign drum_busy = drum_run;
    always @(posedge clk) begin
        if (!rst_n_drum) begin drum_done <= 1'b0; drum_run <= 1'b0; end
        else begin
            if (go) begin drum_done <= 1'b0; drum_run <= 1'b1; end
            else if (body_valid) begin drum_done <= 1'b1; drum_run <= 1'b0; end
        end
    end

    // ---- the two integration defects this join can have, as negative controls ---------
    // Both produce PLAUSIBLE audio -- a full kit under a note, one frame out of
    // step -- which is why they are here rather than left to inspection. A mix
    // that silently uses the previous frame's bus passes every check that looks
    // at the voice alone or the drums alone.
`ifdef INJECT_BUG_DRUM_BUS_STALE
    // The master mix takes the PREVIOUS frame's drum buses: the handshake is
    // satisfied, the format is right, the deadline is met, and the audio is one
    // frame stale.
    reg signed [21:0] dmix_q;
    reg signed [18:0] body_q;
    always @(posedge clk) if (!rst_n_drum) begin dmix_q <= 22'sd0; body_q <= 19'sd0; end
                          else if (go) begin dmix_q <= dmix; body_q <= body; end
    wire signed [21:0] dmix_v = dmix_q;
    wire signed [18:0] body_v = body_q;
    wire               done_v = 1'b1;
`elsif INJECT_BUG_DRUM_DONE_NOWAIT
    // The handshake removed and nothing else: the mix does not WAIT for this
    // frame's buses. Whether that is audible depends on whether the voice
    // reaches its drum stage before drum_kit finishes -- which is the question
    // the handshake exists to answer, and it must not be left to timing.
    wire signed [21:0] dmix_v = dmix;
    wire signed [18:0] body_v = body;
    wire               done_v = 1'b1;
`else
    wire signed [21:0] dmix_v = dmix;
    wire signed [18:0] body_v = body;
    wire               done_v = drum_done;
`endif

    // ---- the voice, the ladder, the master mix -------------------------------------------
    wire signed [15:0] sample;
    wire        sample_valid;
    voice_dp u_voice (.clk(clk), .rst_n(rst_n_dp), .go(go),
                      .wr_valid(wr_voice), .wr_flag(wr_flag), .wr_addr(wr_addr), .wr_data(wr_data),
                      .dmix(dmix_v), .body(body_v), .drum_done(done_v),
                      .sample(sample), .sample_valid(sample_valid), .busy(voice_busy),
                      .mixed(), .ae(), .fe(), .cut(), .k_eff(), .y19());

    // ---- I2S -------------------------------------------------------------------------------
    i2s_tx u_i2s (.clk(clk), .rst_n(rst_n), .cyc(cyc), .sample_valid(sample_valid), .sample(sample),
                  .bclk(bclk), .lrclk(lrclk), .sdata(sdata));
endmodule
`default_nettype wire

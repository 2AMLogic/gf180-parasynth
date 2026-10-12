// drum_kit.v -- the drum section: drum_dp (sources, envelopes, routing) and
// modal_dp (the bodies and filters) as one instrument, contract section 15.
// This is the module verify_drums.py compares against model/drums_fx.py.
//
// bank_start from the drum datapath is the bank's sample_valid; the paths'
// values reach the bank through its exc accumulate port before that, so the
// excitation timing contract of 15.6 is met by construction. mix_out is
// held from mix_valid until the next frame; body_out from body_valid.
// The bank's y_valid follows bank_start by 3*MODES + 2 clocks.
`default_nettype none
module drum_kit #(
    parameter ENVS  = 18,
    parameter PATHS = 23,
    parameter MODES = 16,
    parameter NUMS  = 11,
    parameter STOPS = 11,
    parameter SB    = 28,
    parameter CF    = 24,
    parameter EW    = 21,
    parameter OW    = 19,
    parameter HR    = 0,
    parameter MW    = 4,
    parameter ROM_FILE = "tanh16.hex"
)(
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    frame_tick,
    input  wire [STOPS-1:0]        stops,
    input  wire [STOPS*16-1:0]     accent_bus,
    input  wire [6*24-1:0]         osc_inc_bus,
    input  wire [ENVS*27-1:0]      env_ctl_bus,
    input  wire [ENVS*24-1:0]      env_peak_bus,
    input  wire [ENVS*16-1:0]      env_rate_bus,
    input  wire [ENVS*16-1:0]      env_frate_bus,
    input  wire [PATHS*25-1:0]     path_bus,
    input  wire [MODES*(CF+2)-1:0] a1_bus,
    input  wire [MODES*(CF+2)-1:0] a2_bus,
    input  wire [MODES*16-1:0]     amp_bus,
    input  wire [MODES*2-1:0]      num_bus,
    input  wire                    couple_en,       // COUPLE bit 0 (15.10)
    output wire signed [22:0]      mix_out,
    output wire                    mix_valid,
    output wire signed [OW:0]      body_out,
    output wire                    body_valid
);
    wire [MW-1:0]        tap_sel;
    wire signed [SB-1:0] tap_y1;
    wire                 exc_we, bank_start;
    wire [MW-1:0]        exc_mode;
    wire signed [EW-1:0] exc_val;
    wire signed [21:0]   raw_mix;
    wire signed [OW-1:0] raw_body;
    wire                 mix_valid_raw, body_valid_raw;

    drum_dp #(.ENVS(ENVS), .PATHS(PATHS), .MODES(MODES), .STOPS(STOPS), .SB(SB), .EW(EW), .MW(MW), .ROM_FILE(ROM_FILE)) src (
        .clk(clk), .rst_n(rst_n), .frame_tick(frame_tick), .stops(stops), .accent_bus(accent_bus),
        .osc_inc_bus(osc_inc_bus), .env_ctl_bus(env_ctl_bus), .env_peak_bus(env_peak_bus),
        .env_rate_bus(env_rate_bus), .env_frate_bus(env_frate_bus), .path_bus(path_bus), .tap_sel(tap_sel), .tap_y1(tap_y1),
        .exc_we(exc_we), .exc_mode(exc_mode), .exc_val(exc_val), .bank_start(bank_start),
        .mix_out(raw_mix), .mix_valid(mix_valid_raw));

    modal_dp #(.MODES(MODES), .NUMS(NUMS), .SB(SB), .CF(CF), .HR(HR), .OW(OW), .EW(EW), .MW(MW)) bank (
        .clk(clk), .rst_n(rst_n), .exc_we(exc_we), .exc_mode(exc_mode), .exc_val(exc_val),
        .sample_valid(bank_start), .a1_bus(a1_bus), .a2_bus(a2_bus), .amp_bus(amp_bus), .num_bus(num_bus),
        .tap_sel(tap_sel), .tap_y1(tap_y1), .y_out(raw_body), .y_valid(body_valid_raw));

    // ---- shared-bus DC coupling (contract 15.10; DR 0025; #552) -----------------------
    // One recurrence per bus, K = 10:  d = acc >>> 10;  y = x - d;  acc += y.
    // Placement: AFTER the bank (the body word is final at body_valid_raw and the mix bus has
    // been final since the drain before it), BEFORE the output stage's gains and its one clamp.
    // Both states advance once per frame on body_valid_raw, ENABLED OR NOT; the enable only
    // selects what leaves (y when 1, the raw bus when 0). The enable is captured ONCE, at
    // frame_tick (a write lands at the start of its frame, 4.3), so the two buses -- which are
    // final at different clocks -- can never select different enables for one frame.
    reg couple_q;
    always @(posedge clk) begin
        if (!rst_n) couple_q <= 1'b0;
        else if (frame_tick) couple_q <= couple_en;
    end
    reg step_q;                                    // the coupled buses are valid
    always @(posedge clk) step_q <= rst_n & body_valid_raw;

    // Defect inputs (inert unless a mutant is compiled in): clear the charge on a strike / retune
    reg [STOPS-1:0]        stops_p;
    reg [MODES*(CF+2)-1:0] a1_p;
    always @(posedge clk) begin stops_p <= stops; a1_p <= a1_bus; end
`ifdef INJECT_BUG_DRUM_COUPLE_HIT_CLEAR
    wire dc_clr = (stops != stops_p) && (stops & ~stops_p) != 0;      // a stop rising = a strike
`elsif INJECT_BUG_DRUM_COUPLE_RETUNE_CLEAR
    wire dc_clr = (a1_bus != a1_p);                                   // a coefficient retune
`else
    wire dc_clr = 1'b0;
`endif

`ifdef INJECT_BUG_DRUM_COUPLE_MISPLACED
    // NEGATIVE CONTROL: the filter sits AFTER a 16-bit clip of the mix bus, the placement
    // contract 12 forbids for the gains and 15.10 forbids for this
    wire signed [21:0] x_mix = (raw_mix > 22'sd32767) ? 22'sd32767 : (raw_mix < -22'sd32768) ? -22'sd32768 : raw_mix;
`else
    wire signed [21:0] x_mix = raw_mix;
`endif
    drum_dcblock #(.N(22)) dc_mix (.clk(clk), .rst_n(rst_n), .step(body_valid_raw), .en(couple_q),
                                   .clr(dc_clr), .x(x_mix), .y(mix_out));
    drum_dcblock #(.N(OW)) dc_body (.clk(clk), .rst_n(rst_n), .step(body_valid_raw), .en(couple_q),
                                    .clr(dc_clr), .x(raw_body), .y(body_out));
    assign mix_valid = step_q;
    assign body_valid = step_q;
endmodule

// drum_dcblock -- one bus's coupling stage. Widths are 15.10's: charge N + K bits, output N + 1,
// no saturation and no wrap (the bounds are closed, proven in model/drums_fx.py DcBlockFx).
// `>>>` on a signed operand is the arithmetic shift the contract names (floor, not truncation).
// It lives in this file so that no source list changes (every flow already compiles drum_kit.v).
module drum_dcblock #(
    parameter N = 22,                 // the declared bus width, signed
    parameter K = 10                  // FIXED: not a register (15.10)
)(
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    step,       // one pulse per completed bus sample
    input  wire                    en,
    input  wire                    clr,        // defect hook, tied 0 in the product
    input  wire signed [N-1:0]     x,
    output reg  signed [N:0]       y
);
`ifdef INJECT_BUG_DRUM_COUPLE_NARROW_ACC
    localparam AW = N + K - 1;        // NEGATIVE CONTROL: the charge is one bit short
`else
    localparam AW = N + K;
`endif
    reg signed [AW-1:0] acc;
`ifdef INJECT_BUG_DRUM_COUPLE_SHIFT_LOGICAL
    wire signed [N:0] d = $signed({1'b0, acc} >> K);          // NEGATIVE CONTROL: zero-fill, not sign
`else
    wire signed [N:0] d = acc >>> K;
`endif
    wire signed [N:0]   xe = x;
    wire signed [N:0]   yv = xe - d;
    wire signed [AW:0]  an = acc + yv;                         // exact in AW bits by the closed bound
    always @(posedge clk) begin
        if (!rst_n) begin acc <= 0; y <= 0; end
        else if (step) begin
`ifdef INJECT_BUG_DRUM_COUPLE_FROZEN
            if (en) acc <= an[AW-1:0];                         // NEGATIVE CONTROL: frozen while bypassed
`else
            if (clr) acc <= 0; else acc <= an[AW-1:0];
`endif
`ifdef INJECT_BUG_DRUM_COUPLE_BYPASS
            y <= xe;                                           // NEGATIVE CONTROL: the stage never reaches the output
`elsif INJECT_BUG_DRUM_COUPLE_NARROW_OUT
            y <= en ? {yv[N-1], yv[N-1:0]} : xe;               // NEGATIVE CONTROL: output is N bits, not N + 1
`else
            y <= en ? yv : xe;
`endif
        end
    end
endmodule
`default_nettype wire

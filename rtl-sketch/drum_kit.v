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
    output wire signed [21:0]      mix_out,
    output wire                    mix_valid,
    output wire signed [OW-1:0]    body_out,
    output wire                    body_valid
);
    wire [MW-1:0]        tap_sel;
    wire signed [SB-1:0] tap_y1;
    wire                 exc_we, bank_start;
    wire [MW-1:0]        exc_mode;
    wire signed [EW-1:0] exc_val;

    drum_dp #(.ENVS(ENVS), .PATHS(PATHS), .MODES(MODES), .STOPS(STOPS), .SB(SB), .EW(EW), .MW(MW), .ROM_FILE(ROM_FILE)) src (
        .clk(clk), .rst_n(rst_n), .frame_tick(frame_tick), .stops(stops), .accent_bus(accent_bus),
        .osc_inc_bus(osc_inc_bus), .env_ctl_bus(env_ctl_bus), .env_peak_bus(env_peak_bus),
        .env_rate_bus(env_rate_bus), .env_frate_bus(env_frate_bus), .path_bus(path_bus), .tap_sel(tap_sel), .tap_y1(tap_y1),
        .exc_we(exc_we), .exc_mode(exc_mode), .exc_val(exc_val), .bank_start(bank_start),
        .mix_out(mix_out), .mix_valid(mix_valid));

    modal_dp #(.MODES(MODES), .NUMS(NUMS), .SB(SB), .CF(CF), .HR(HR), .OW(OW), .EW(EW), .MW(MW)) bank (
        .clk(clk), .rst_n(rst_n), .exc_we(exc_we), .exc_mode(exc_mode), .exc_val(exc_val),
        .sample_valid(bank_start), .a1_bus(a1_bus), .a2_bus(a2_bus), .amp_bus(amp_bus), .num_bus(num_bus),
        .tap_sel(tap_sel), .tap_y1(tap_y1), .y_out(body_out), .y_valid(body_valid));
endmodule
`default_nettype wire

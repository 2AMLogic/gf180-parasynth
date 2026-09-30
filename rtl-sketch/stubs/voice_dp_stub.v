// voice_dp_stub.v -- voice_dp's ports with no behaviour, for the red run of
// docs/verification-rules.md section 1: every data output is X, and the only
// thing it does is strobe sample_valid after `go` so the bench runs to the
// end and has to say what it makes of an X sample. verify_voice.py must exit
// 1 (mismatch, reporting the frames as undefined/X), never 0 and never 2:
//
//     .venv/bin/python rtl-sketch/verify_voice.py --set quick --only default --rtl stubs/voice_dp_stub.v
//
// The taps tb_voice.v reads hierarchically (state, kk, osc, inc_acc, ...) are
// declared here with the same names and left X for the same reason.
`default_nettype none
module voice_dp #(
    parameter G_ROM_FILE = "", parameter K_ROM_FILE = "", parameter SINE_FILE = "", parameter TANH_FILE = ""
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        go,
    input  wire        wr_valid,
    input  wire        wr_flag,
    input  wire [7:0]  wr_addr,
    input  wire [31:0] wr_data,
    input  wire signed [20:0] dmix,
    input  wire signed [18:0] body,
    input  wire        drum_done,
    output reg  signed [15:0] sample,
    output reg         sample_valid,
    output wire        busy,
    output reg  signed [15:0] mixed,
    output reg  [14:0] ae,
    output reg  [14:0] fe,
    output reg  [14:0] cut,
    output reg  [16:0] k_eff,
    output reg  signed [18:0] y19
);
    // what the bench taps by hierarchical name, all undefined
    reg [5:0]  state = 6'bx;
    reg [1:0]  kk = 2'bx;
    wire signed [15:0] osc = 16'bx;
    reg [31:0] inc_acc [0:2];
    reg [23:0] phase [0:2];
    reg [23:0] phase_os2 [0:2];        // the 2x path's taps (bd7ba32); the red run died on them
    reg [23:0] inc_mod [0:2];
    reg [4:0]  sh [0:2];
    reg [15:0] r [0:2];
    reg signed [24:0] ma = 25'bx;
    reg signed [20:0] mb = 21'bx;
    reg [15:0] g = 16'bx;
    reg signed [35:0] macc = 36'bx;   // tb_voice taps macc >>> 15 for the out_v column
    reg [23:0] level_a = 24'bx, level_f = 24'bx;
    reg [1:0]  seg_a = 2'bx, seg_f = 2'bx;
    integer i;
    initial for (i = 0; i < 3; i = i + 1) begin inc_acc[i] = 32'bx; phase[i] = 24'bx; phase_os2[i] = 24'bx; inc_mod[i] = 24'bx; sh[i] = 5'bx; r[i] = 16'bx; end

    reg [1:0] d;
    assign busy = 1'b0;
    always @(posedge clk) begin
        if (!rst_n) begin d <= 2'd0; sample_valid <= 1'b0; end
        else begin
            sample_valid <= 1'b0;
            if (go) d <= 2'd1;
            else if (d != 2'd0) begin d <= d + 2'd1; if (d == 2'd3) begin sample_valid <= 1'b1; d <= 2'd0; end end
        end
        sample <= 16'bx; mixed <= 16'bx; ae <= 15'bx; fe <= 15'bx; cut <= 15'bx; k_eff <= 17'bx; y19 <= 19'bx;
    end
endmodule
`default_nettype wire

// sd_dac.v -- a second-order, one-bit sigma-delta modulator: a no-DAC audio
// output for any FPGA target with a free pin, a resistor and a capacitor
// (#406). PDK- and board-independent.
//
// MASTER COPY. This repository is the first consumer and holds the master; a
// sibling synth takes a stamped copy (this file, unmodified, with the commit
// it came from) and does not write its own. A change here is a change to the
// master: run fpga/verify_sd_dac.py and bump nothing else.
//
// Loop (CIFB, both integrators non-delaying, the feedback one clock late):
//     i1 <- sat(i1 + 7*x - y)
//     i2 <- sat(i2 + i1' - y)          i1' = the new i1
//     q  <- i2' >= 0,   y = q ? +8*FS : -8*FS,   FS = 2^15
// Solving the loop gives STF = 1 and NTF = (1 - z^-1)^2 exactly: no signal
// delay and a textbook second-order noise shape. At 12.288 MHz against the
// 20 kHz band that is ~307x oversampling.
//
// Input scale 7/8, done exactly (7x against +-8*FS, never x - x>>3, which
// truncates: a Python prototype of the truncating form lost ~20 dB at
// -40 dBFS to rounding against an exact reference). The 7/8 is the headroom
// a one-bit second-order loop needs: unscaled, a -0.1 dBFS sine fell to
// 66 dB; scaled, full scale holds ~100 dB. The analog cost is 1.2 dB of swing.
//
// Integrators saturate at +-2^22 (24-bit registers). Unclamped, the second
// integrator reaches ~60 * 2^18 on a full-scale square and would wrap a
// 24-bit register; the clamp is what keeps a clipped synth output from
// becoming a limit cycle (INJECT_BUG_SD_NO_SAT removes it and turns the
// square test red).
//
// At idle the loop settles into a period-4 pattern, 0011, whose only energy is
// at 12.288/4 = 3.072 MHz -- exactly the 16th null of the verifier's R = 64
// CIC -- and whose mean is zero, so a correct modulator's silence error
// decimates to EXACTLY zero. That is what the silence bound is measuring, and
// INJECT_BUG_SD_DC_BIAS is its negative control: one LSB added to the loop
// input, which the loop turns into a DC offset on the output (mean(y) =
// mean(x7)). Measured at -104.7 dBFS: buried under every signal case's own
// error floor, but 5.3 dB above the silence bound, so it reds silence alone.
//
// `sample` is read every clock; hold it between updates (the wrapper's
// i2s_rx does). Measured in-band SNR and its bounds: fpga/verify_sd_dac.py.
`default_nettype none
module sd_dac (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [15:0] sample,
    output reg                pdm
);
    localparam integer W = 24;
    localparam signed [W:0] HI = (25'sd1 <<< 22) - 25'sd1;
    localparam signed [W:0] LO = -(25'sd1 <<< 22);
    localparam signed [W:0] FB = 25'sd1 <<< 18;          // 8 * FS

    reg signed [W-1:0] i1, i2;
    wire signed [W:0] y  = pdm ? FB : -FB;
`ifdef INJECT_BUG_SD_DC_BIAS
    // NEGATIVE CONTROL: one LSB of DC at the loop input. The loop forces
    // mean(y) = mean(x7), so this puts a DC offset of 1 part in 2^18 on the
    // output. It turns the silence test red and nothing else. See the header.
    wire signed [W:0] x7 = $signed({{(W-15){sample[15]}}, sample}) * 25'sd7 + 25'sd1;
`else
    wire signed [W:0] x7 = $signed({{(W-15){sample[15]}}, sample}) * 25'sd7;
`endif

    function signed [W-1:0] sat(input signed [W+1:0] v);
`ifdef INJECT_BUG_SD_NO_SAT
        sat = v[W-1:0];                                   // NEGATIVE CONTROL: wraps
`else
        sat = (v > HI) ? HI[W-1:0] : (v < LO) ? LO[W-1:0] : v[W-1:0];
`endif
    endfunction

    wire signed [W-1:0] i1n = sat($signed({i1[W-1], i1[W-1], i1}) + x7 - y);
    wire signed [W-1:0] i2n = sat($signed({i2[W-1], i2[W-1], i2}) + i1n - y);

    always @(posedge clk) begin
        if (!rst_n) begin
            i1 <= 0; i2 <= 0; pdm <= 1'b0;
        end else begin
            i1 <= i1n;
            i2 <= i2n;
`ifdef INJECT_BUG_SD_FIRST_ORDER
            pdm <= ~i1n[W-1];                             // NEGATIVE CONTROL: 1st order
`else
            pdm <= ~i2n[W-1];
`endif
        end
    end
endmodule
`default_nettype wire

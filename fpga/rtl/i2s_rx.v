// i2s_rx.v -- decode rtl-sketch/i2s_tx.v's wire back into a sample, in the
// transmitter's own clock domain (#406).
//
// The sigma-delta output takes its sample from the I2S WIRE, not from inside
// synth_top: the core is untouched, and the no-DAC output plays exactly the
// bits a PCM5102 would receive. i2s_tx's convention: BCLK = clk/4, LRCLK low
// = left, MSB on the second BCLK after the LRCLK edge, 16 bits left-justified
// in a 32-bit slot, SDATA changing on BCLK's falling edge, L = R.
//
// Everything here is synchronous to `clk` (the core clock that also clocks
// i2s_tx), so "BCLK rising" is a registered edge detect, not a clock. The
// left slot's 16th data bit updates `sample`, which then holds for one LRCLK
// period. Only the left slot is decoded: by contract the right repeats it.
`default_nettype none
module i2s_rx (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               bclk,
    input  wire               lrclk,
    input  wire               sdata,
    output reg signed [15:0]  sample
);
    reg bclk_q, lr_q;
    reg [4:0] bit_n;               // BCLKs since the LRCLK edge; 0 = the delay bit
    reg [15:0] shift;
    wire rise = bclk & ~bclk_q;
    always @(posedge clk) begin
        if (!rst_n) begin
            bclk_q <= 1'b0; lr_q <= 1'b0; bit_n <= 5'd31; shift <= 16'd0; sample <= 16'sd0;
        end else begin
            bclk_q <= bclk;
            if (rise) begin
                lr_q <= lrclk;
                bit_n <= (lrclk != lr_q) ? 5'd0 : (bit_n == 5'd31 ? 5'd31 : bit_n + 5'd1);
`ifdef INJECT_BUG_I2S_RX_SHIFT
                // NEGATIVE CONTROL: capture from the delay bit, one bit early
                if (!lrclk && (lrclk != lr_q || bit_n <= 5'd14)) shift <= {shift[14:0], sdata};
                if (!lrclk && lrclk == lr_q && bit_n == 5'd14) sample <= {shift[14:0], sdata};
`else
                if (!lrclk && lrclk == lr_q && bit_n <= 5'd15) shift <= {shift[14:0], sdata};
                if (!lrclk && lrclk == lr_q && bit_n == 5'd15) sample <= {shift[14:0], sdata};
`endif
            end
        end
    end
endmodule
`default_nettype wire

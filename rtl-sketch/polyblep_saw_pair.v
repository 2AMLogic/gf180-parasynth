// Two PolyBLEP saw samples for one base-rate phase point.
// `sh` and `recip` are the same reciprocal state already held by voice_dp.
module polyblep_saw_pair(
    input wire [23:0] phase, input wire [23:0] inc,
    input wire [4:0] sh, input wire [15:0] recip,
    output wire signed [15:0] sample0, output wire signed [15:0] sample1
);
    function automatic signed [17:0] saw_blep(input [23:0] p, input [23:0] step, input [4:0] shift);
        reg [23:0] x; reg [24:0] q; reg [38:0] xs; reg [15:0] frac, u; reg [31:0] uu; reg [16:0] s; reg [33:0] ss;
        reg signed [15:0] base; reg signed [17:0] raw; reg signed [17:0] c;
        begin
            q = 25'h1000000 - {1'b0,p};
            c = 0;
            if (p < step) begin
                x = p;
                xs = (shift == 0) ? ({x,15'b0} << 1) : ({x,15'b0} >> shift);
                frac = xs[15:0]; uu = frac * recip; u = uu >> 15;
                s = 17'h10000 - {1'b0,u}; ss = s*s; c = -$signed(ss >> 17);
            end else if (q < {1'b0,step}) begin
                x = q[23:0];
                xs = (shift == 0) ? ({x,15'b0} << 1) : ({x,15'b0} >> shift);
                frac = xs[15:0]; uu = frac * recip; u = uu >> 15;
                s = 17'h10000 - {1'b0,u}; ss = s*s; c = $signed(ss >> 17);
            end
            base = {~p[23],p[22:8]};
            raw = $signed(base) - c;
            if (raw > 18'sd32767) saw_blep = 18'sd32767;
            else if (raw < -18'sd32768) saw_blep = -18'sd32768;
            else saw_blep = raw;
        end
    endfunction
    wire signed [23:0] inc2 = inc >> 1;
    // Halving the increment keeps the mantissa and reciprocal but lowers the
    // normalization exponent by one bit.
    wire [4:0] sh2 = (sh == 0) ? 0 : sh - 1'b1;
    wire signed [17:0] s0 = saw_blep(phase, inc2, sh2);
    wire signed [17:0] s1 = saw_blep(phase + inc2, inc2, sh2);
    assign sample0 = s0[15:0];
    assign sample1 = s1[15:0];
endmodule

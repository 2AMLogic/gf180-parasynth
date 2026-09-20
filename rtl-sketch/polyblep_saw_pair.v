// Two PolyBLEP saw samples for one base-rate phase point.
// `sh` and `recip` are the same reciprocal state already held by voice_dp.
module polyblep_saw_pair(
    input wire [23:0] phase, input wire [23:0] inc,
    input wire [4:0] sh, input wire [15:0] recip,
    output wire signed [15:0] sample0, output wire signed [15:0] sample1
);
    function automatic signed [17:0] saw_blep(input [23:0] p, input [23:0] step);
        reg [23:0] x; reg [38:0] xs; reg [15:0] frac, u; reg [16:0] s;
        reg signed [17:0] raw; reg signed [17:0] c;
        begin
            x = p;
            xs = ({x,15'b0} >> sh);
            frac = xs[15:0];
            u = (frac * recip) >> 15;
            s = 17'h10000 - {1'b0,u};
            // Saw falls at the phase wrap; the PolyBLEP correction is
            // negative on the leading window (voice_dp's c_pp sign).
            c = (p < step) ? -$signed((s*s) >> 17) : 0;
            raw = $signed({{2{(~p[23])}}, p[22:8]}) - c;
            if (raw > 18'sd32767) saw_blep = 18'sd32767;
            else if (raw < -18'sd32768) saw_blep = -18'sd32768;
            else saw_blep = raw;
        end
    endfunction
    wire signed [23:0] inc2 = inc >> 1;
    wire signed [17:0] s0 = saw_blep(phase, inc2);
    wire signed [17:0] s1 = saw_blep(phase + inc2, inc2);
    assign sample0 = s0[15:0];
    assign sample1 = s1[15:0];
endmodule

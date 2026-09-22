// Two samples at the 96 kHz phase rate.  The dedicated phase accumulator is
// advanced by 2*floor(inc/2) per base frame; its tiny pitch quantization is
// measured in model/oversampled_osc.py.
module polyblep_saw_pair(
    input wire [23:0] phase, input wire [23:0] inc,
    input wire [4:0] sh, input wire [15:0] recip,
    input wire rectangular, input wire [23:0] duty,
    output wire signed [15:0] sample0, output wire signed [15:0] sample1
);
    function automatic signed [17:0] correction(input [23:0] p, input [23:0] step,
                                               input [4:0] shift, input [15:0] recip_i);
        reg [23:0] x; reg [24:0] q; reg [38:0] xs;
        reg [15:0] frac,u; reg [31:0] uu; reg [16:0] s; reg [33:0] ss;
        reg signed [17:0] c;
        begin
            q=25'h1000000-{1'b0,p}; c=0;
            if(p<step) begin
                x=p; xs=(shift==0)?({x,15'b0}<<1):({x,15'b0}>>shift);
                frac=xs[15:0]; uu=frac*recip_i; u=uu>>15; s=17'h10000-{1'b0,u};
                ss=s*s; c=-$signed(ss>>17);
            end else if(q<{1'b0,step}) begin
                x=q[23:0]; xs=(shift==0)?({x,15'b0}<<1):({x,15'b0}>>shift);
                frac=xs[15:0]; uu=frac*recip_i; u=uu>>15; s=17'h10000-{1'b0,u};
                ss=s*s; c=$signed(ss>>17);
            end
            correction=c;
        end
    endfunction
    function automatic signed [17:0] waveform(input [23:0] p, input [23:0] step,
                                               input [4:0] shift, input [15:0] recip_i,
                                               input rect, input [23:0] width);
        reg signed [15:0] base;
        reg signed [17:0] raw;
        reg [23:0] edge_phase;
        begin
            edge_phase=p-width;
`ifdef VOICE_PULSE_2X
            if(rect) begin
                base=(p<width)?16'sh7fff:16'sh8000;
                raw=$signed(base)+correction(p,step,shift,recip_i)
                    -correction(edge_phase,step,shift,recip_i);
            end else begin
`endif
                base={~p[23],p[22:8]};
                raw=$signed(base)-correction(p,step,shift,recip_i);
`ifdef VOICE_PULSE_2X
            end
`endif
            if(raw>18'sd32767) waveform=18'sd32767;
            else if(raw < -18'sd32768) waveform=-18'sd32768;
            else waveform=raw;
        end
    endfunction
    wire [23:0] inc2=inc>>1;
    wire [4:0] sh2=(sh==0)?0:sh-1'b1;
    wire signed [17:0] s0=waveform(phase,inc2,sh2,recip,rectangular,duty);
    wire signed [17:0] s1=waveform(phase+inc2,inc2,sh2,recip,rectangular,duty);
    // FIR ringing exceeded Q1.15 on the full-scale saw and the output
    // saturation created broadband distortion. A MIDI sweep measured 0.85 as
    // enough headroom to keep the decimator output below its Q1.15 rail.
`ifdef INJECT_BUG_VOICE_OSC2X_HEADROOM
    localparam signed [15:0] SUBSTEP_GAIN_Q15=16'sd32767;
`else
    localparam signed [15:0] SUBSTEP_GAIN_Q15=16'sd27853;
`endif
    wire signed [31:0] scaled0=$signed(s0[15:0])*SUBSTEP_GAIN_Q15;
    wire signed [31:0] scaled1=$signed(s1[15:0])*SUBSTEP_GAIN_Q15;
    assign sample0=scaled0 >>> 15; assign sample1=scaled1 >>> 15;
endmodule

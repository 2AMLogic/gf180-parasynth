// Streaming 2:1 decimator for the oscillator oversampling path.
// Input samples arrive at 2*SR; every odd sample produces one SR output.
// Coefficients match model/oversampled_osc.py (Q15, sum = 32768).
module decimate_2x(
    input  wire               clk,
    input  wire               rst_n,
    input  wire               in_valid,
    input  wire signed [15:0] in_sample,
    output reg                out_valid,
    output reg  signed [15:0] out_sample
);
    reg signed [15:0] h[0:30];
    reg signed [15:0] x[0:30];
    reg parity;
    integer i;
    reg signed [47:0] acc;
    initial begin
        h[0]=39; h[1]=54; h[2]=-44; h[3]=-138; h[4]=34; h[5]=323;
        h[6]=72; h[7]=-609; h[8]=-397; h[9]=957; h[10]=1133;
        h[11]=-1296; h[12]=-2819; h[13]=1544; h[14]=10175; h[15]=14712;
        h[16]=10175; h[17]=1544; h[18]=-2819; h[19]=-1296; h[20]=1133;
        h[21]=957; h[22]=-397; h[23]=-609; h[24]=72; h[25]=323;
        h[26]=34; h[27]=-138; h[28]=-44; h[29]=54; h[30]=39;
    end
    always @(posedge clk) begin
        out_valid <= 1'b0;
        if (!rst_n) begin
            parity <= 1'b0;
            out_sample <= 0;
            for (i=0; i<31; i=i+1) x[i] <= 0;
        end else if (in_valid) begin
            for (i=30; i>0; i=i-1) x[i] <= x[i-1];
            x[0] <= in_sample;
            if (parity) begin
                acc = 0;
                for (i=0; i<31; i=i+1) acc = acc + h[i] * x[i];
                out_sample <= (acc >>> 15);
                out_valid <= 1'b1;
            end
            parity <= ~parity;
        end
    end
endmodule

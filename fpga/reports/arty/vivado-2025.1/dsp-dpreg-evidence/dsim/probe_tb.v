`timescale 1ns/1ps
module probe_tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg ce = 1, rst = 0;
    reg signed [15:0] a2s = 16'sd100;
    reg signed [47:0] c2s = -48'sd1000;
    reg [4:0] tap = 5'd1;
    wire [6:0] prod1 = {7{(tap != 5'd31)}} & 7'b0110000;
    wire [6:0] tapq  = {7{tap[0]}} & 7'b0000101;
    wire [6:0] opmode2 = prod1 | tapq;
    wire [47:0] p2, pcout2;
    DSP48E1 #(
        .AREG(2), .ACASCREG(2), .BREG(0), .BCASCREG(0), .CREG(0), .DREG(1),
        .MREG(0), .PREG(0), .OPMODEREG(0), .USE_DPORT("FALSE"),
        .MASK(48'h3FFFFFFFFFFF), .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D2 (
        .CLK(clk), .CEA1(ce), .CEA2(ce), .CEAD(1'b0), .CEB1(1'b0), .CEB2(1'b0),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A({{14{a2s[15]}}, a2s}), .B(18'sd14712), .C(c2s), .D(25'b0),
        .INMODE(5'b0), .OPMODE(opmode2), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(48'b0),
        .P(p2), .PCOUT(pcout2), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );
    integer i;
    initial begin
        for (i = 0; i < 6; i = i + 1) begin
            @(negedge clk);
            #1;
            $display("t=%0t opmode=%b p2=%h  qz=%h qx=%h qy=%h qmult=%h qa2=%h",
                     $time, opmode2, p2, D2.qz_o_mux, D2.qx_o_mux,
                     D2.qy_o_mux, D2.qmult_o_mux, D2.qa_o_reg2);
        end
        $finish;
    end
endmodule

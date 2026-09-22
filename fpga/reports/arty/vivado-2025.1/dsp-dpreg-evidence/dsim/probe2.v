`timescale 1ns/1ps
module probe2_tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg ce = 1, rst1 = 0;
    reg [47:0] pcin_stim = 48'b0;
    wire [47:0] p1;
    wire [29:0] a1 = p1[47:18];
    wire [17:0] b1 = p1[17:0];
    DSP48E1 #(
        .AREG(1), .ACASCREG(1), .BREG(1), .BCASCREG(1), .CREG(1), .DREG(1),
        .MREG(0), .PREG(0), .OPMODEREG(0), .USE_DPORT("FALSE"),
        .USE_MULT("NONE"),
        .MASK(48'h3FFFFFFFFFFF), .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D1 (
        .CLK(clk), .CEA1(1'b0), .CEA2(ce), .CEAD(1'b0), .CEB1(1'b0), .CEB2(ce),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst1), .RSTB(rst1), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A(a1), .B(b1), .C(48'hFFFFFFFFFFFF), .D(25'b0),
        .INMODE(5'b0), .OPMODE(7'b0001011), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(pcin_stim),
        .P(p1), .PCOUT(), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );
    integer i;
    initial begin
        for (i = 0; i < 4; i = i + 1) begin
            @(negedge clk); #1;
            $display("t=%0t pcin=%h p1=%h", $time, pcin_stim, p1);
        end
        $finish;
    end
endmodule

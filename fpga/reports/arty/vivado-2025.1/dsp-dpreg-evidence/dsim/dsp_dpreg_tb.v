
`timescale 1ns/1ps
`default_nettype none
module dsp_dpreg_tb;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg        ce_a2  = 1'b0;
    reg        rst_a2 = 1'b1;
    reg signed [15:0] a2s = 16'sd0;
    reg signed [47:0] c2s = 48'sd0;
    reg [4:0]  tap    = 5'd0;
    reg        ce1    = 1'b0;
    reg        rst1   = 1'b1;
    reg        xflag;
    integer f, fo, rc;
    integer cea_i, rsta_i, a2_i, tap_i, ce1_i, rst1_i;
    reg [47:0] c2_i;

    wire [6:0] prod1 = {7{(tap != 5'd31)}} & 7'b0110000;
    wire [6:0] tapq  = {7{tap[0]}} & 7'b0000101;
`ifdef MODE_OPMODEP
    wire [6:0] opmode2 = (tap == 5'd7) ? {3'b010, prod1[3:0] | tapq[3:0]}
                                       : (prod1 | tapq);
`else
    wire [6:0] opmode2 = prod1 | tapq;
`endif

    wire [47:0] p2, pcout2;
    wire [47:0] p1;
    wire [29:0] a1 = p1[47:18];
    wire [17:0] b1 = p1[17:0];

    DSP48E1 #(
        .AREG(2), .ACASCREG(2), .BREG(0), .BCASCREG(0), .CREG(0), .DREG(1),
        .MREG(0),
`ifdef MODE_PREGFLIP
        .PREG(1),
`else
        .PREG(0),
`endif
        .OPMODEREG(0), .USE_DPORT("FALSE"), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D2 (
        .CLK(clk),
        .CEA1(ce_a2), .CEA2(ce_a2), .CEAD(1'b0), .CEB1(1'b0), .CEB2(1'b0),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst_a2), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A({{14{a2s[15]}}, a2s}), .B(18'sd14712), .C(c2s), .D(25'b0),
        .INMODE(5'b0), .OPMODE(opmode2), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(48'b0),
        .P(p2), .PCOUT(pcout2), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );

    DSP48E1 #(
        .AREG(1), .ACASCREG(1), .BREG(1), .BCASCREG(1), .CREG(1), .DREG(1),
        .MREG(0), .PREG(0), .USE_MULT("NONE"),
        .OPMODEREG(0), .USE_DPORT("FALSE"), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D1 (
        .CLK(clk),
        .CEA1(1'b0), .CEA2(ce1), .CEAD(1'b0), .CEB1(1'b0), .CEB2(ce1),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst1), .RSTB(rst1), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A(a1), .B(b1), .C(48'hFFFFFFFFFFFF), .D(25'b0),
        .INMODE(5'b0), .OPMODE(7'b0010011), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(pcout2),
        .P(p1), .PCOUT(), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );

    initial begin
        f = $fopen("stim.txt", "r");
        if (!f) begin $display("STIMULUS-OPEN-FAILED"); $finish; end
        fo = $fopen("actual.txt", "w");
        while (!$feof(f)) begin
            @(negedge clk);
            rc = $fscanf(f, "%d %d %d %h %d %d %d",
                         cea_i, rsta_i, a2_i, c2_i, tap_i, ce1_i, rst1_i);
            if (rc == 7) begin
                ce_a2  <= cea_i[0];
                rst_a2 <= rsta_i[0];
                a2s    <= a2_i[15:0];
                c2s    <= c2_i;
                tap    <= tap_i[4:0];
                ce1    <= ce1_i[0];
                rst1   <= rst1_i[0];
                #1;
                xflag = ((^p2) === 1'bx) || ((^p1) === 1'bx);
                $fwrite(fo, "%h %h %0d\n", p2 & 48'hFFFFFFFFFFFF,
                        p1 & 48'hFFFFFFFFFFFF, xflag ? 1 : 0);
            end
        end
        $fclose(f);
        $fclose(fo);
        $display("TB-DONE");
        $finish;
    end
endmodule
`default_nettype wire

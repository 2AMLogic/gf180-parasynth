// tb_outpath.v -- NEW (#552). The output stage of voice_dp (contract 12) with the COUPLED drum
// buses (23-bit mix, 20-bit body, contract 15.10) driven directly, so the widths a real stream
// never reaches are reached: dmix and body at the ends of their declared words, both gains at 65535.
// The voice is idle (vol = 0, no gate): v * vol = 0 and the sample is exactly
//     sat16((dmix * dvol + body * bvol) >> 15)           with ROUTE.DFILT = 0
// and the drum filter's input rail (voice_dp's `dx`) is the same sat16 of the same word with DFILT = 1.
//
// Case file, one case per line, hex: <dmix 23b> <body 20b> <dvol> <bvol> <dfilt>
// Output, one line per case: "<sample> <dx>" (signed decimal; "x" if undefined)
`timescale 1ns/1ps
module tb_outpath;
    parameter MAXC = 4096;
    reg clk = 0, rst_n = 0, go = 0, wr_valid = 0;
    reg [7:0] wr_addr = 0; reg [31:0] wr_data = 0;
    reg signed [22:0] dmix = 0; reg signed [19:0] body = 0;
    wire signed [15:0] sample; wire sample_valid, busy;
    voice_dp dut (.clk(clk), .rst_n(rst_n), .go(go),
                  .wr_valid(wr_valid), .wr_flag(1'b0), .wr_addr(wr_addr), .wr_data(wr_data),
                  .dmix(dmix), .body(body), .drum_done(1'b1),
                  .sample(sample), .sample_valid(sample_valid), .busy(busy),
                  .mixed(), .ae(), .fe(), .cut(), .k_eff(), .y19());
    always #10 clk = ~clk;

    task wr(input [7:0] a, input [31:0] d);
        begin wr_addr = a; wr_data = d; wr_valid = 1; @(negedge clk); wr_valid = 0; end
    endtask

    reg [127:0] cs [0:MAXC-1];
    reg [8*256-1:0] cfile, ofile;
    integer nc, i, fd, t, fr;
    reg signed [15:0] s_got; reg signed [15:0] dx_got;
    initial begin
        if (!$value$plusargs("cases=%s", cfile)) cfile = "build/outpath_cases.hex";
        if (!$value$plusargs("out=%s", ofile)) ofile = "build/outpath_rtl_out.txt";
        if (!$value$plusargs("n=%d", nc)) nc = 1;
        for (i = 0; i < MAXC; i = i + 1) cs[i] = {128{1'bx}};
        $readmemh(cfile, cs);
        fd = $fopen(ofile, "w");
        repeat (4) @(negedge clk); rst_n = 1; repeat (2) @(negedge clk);
        for (i = 0; i < nc; i = i + 1) begin
            dmix = cs[i][102:80]; body = cs[i][79:60];
            wr(8'h0E, cs[i][47:32]);                       // DVOL
            wr(8'h2C, cs[i][31:16]);                       // BVOL
            wr(8'h0F, {31'b0, cs[i][0]});                  // ROUTE.DFILT
            for (fr = 0; fr < 2; fr = fr + 1) begin        // the second frame is the one read
                go = 1; @(negedge clk); go = 0;
                t = 0; dx_got = 16'bx;
                while (!sample_valid && t < 3000) begin
                    if (dut.state == dut.S_VCA1) dx_got = dut.dx;
                    @(negedge clk); t = t + 1;
                end
                s_got = sample;
                if (t >= 3000) begin $display("tb_outpath: TIMEOUT case %0d", i); $fclose(fd); $finish; end
                while (busy) @(negedge clk);
                repeat (4) @(negedge clk);
            end
            if (^s_got === 1'bx || (cs[i][0] && ^dx_got === 1'bx)) $fdisplay(fd, "x x");
            else $fdisplay(fd, "%0d %0d", s_got, dx_got);
        end
        $fclose(fd);
        $display("tb_outpath: %0d cases", nc);
        $finish;
    end
endmodule

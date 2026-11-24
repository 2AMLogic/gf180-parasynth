// tb_couple.v -- NEW (#552). Drives drum_dcblock (drum_kit.v), the contract 15.10 shared-bus
// DC-coupling stage, directly with arbitrary bus words: the widths a drum stream never reaches
// (full-scale reversals needing the added output bit, the charge's every bit) are driven here.
// One instance per bus, at the production widths 22 (mix) and 19 (body).
//
// Vector file, one 64-bit hex word per sample:  [45] reset before this sample, [44] en,
//                                               [43:22] x_mix (22b), [18:0] x_body (19b)
// Output file, one sample per line:        <y_mix> <y_body>   (signed decimal; "x" if undefined)
// Each sample is one `step` pulse, a few idle clocks apart (the stage is per-sample, not per-clock).
// bit 45 pulses rst_n low before that sample (the hardware/drum RESET: charge and output cleared).
`timescale 1ns/1ps
module tb_couple;
    parameter MAXV = 1 << 18;
    reg clk = 0, rst_n = 0, step = 0, en = 0;
    reg signed [21:0] xm = 0;
    reg signed [18:0] xb = 0;
    wire signed [22:0] ym;
    wire signed [19:0] yb;
    drum_dcblock #(.N(22)) dm (.clk(clk), .rst_n(rst_n), .step(step), .en(en), .clr(1'b0), .x(xm), .y(ym));
    drum_dcblock #(.N(19)) db (.clk(clk), .rst_n(rst_n), .step(step), .en(en), .clr(1'b0), .x(xb), .y(yb));
    always #10 clk = ~clk;

    reg [63:0] vec [0:MAXV-1];
    reg [8*256-1:0] vfile, ofile;
    integer nv, i, fd;
    initial begin
        if (!$value$plusargs("vec=%s", vfile)) vfile = "build/couple_vec.hex";
        if (!$value$plusargs("out=%s", ofile)) ofile = "build/couple_rtl_out.txt";
        if (!$value$plusargs("n=%d", nv)) nv = 1000;
        for (i = 0; i < MAXV; i = i + 1) vec[i] = {64{1'bx}};
        $readmemh(vfile, vec);
        fd = $fopen(ofile, "w");
        repeat (3) @(negedge clk); rst_n = 1; @(negedge clk);
        for (i = 0; i < nv; i = i + 1) begin
            if (vec[i][45]) begin rst_n = 0; @(negedge clk); @(negedge clk); rst_n = 1; end
            en = vec[i][44]; xm = vec[i][43:22]; xb = vec[i][18:0];
            step = 1; @(negedge clk); step = 0;
            @(negedge clk);                       // the registered output is visible now
            if (^ym === 1'bx || ^yb === 1'bx) $fdisplay(fd, "x x");
            else $fdisplay(fd, "%0d %0d", ym, yb);
        end
        $fclose(fd);
        $display("tb_couple: %0d samples", nv);
        $finish;
    end
endmodule

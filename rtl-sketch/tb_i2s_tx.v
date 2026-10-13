// tb_i2s_tx.v -- i2s_tx.v on its own, driven so that the RIGHT channel is observable (#619).
//
// WHY THIS EXISTS. At the whole chip (verify_synth_top.py) the core strobes its
// sample as late as cycle 156, past the LRCLK transition at 128. i2s_tx re-reads
// `held` for the right slot at cycle 127, so `held` still equals `cur` there and
// INJECT_BUG_I2S_SWAP emits a stream bit-identical to the correct one: the
// control is blind in that configuration. Here the strobe is at STROBE_CYC (20),
// well before 127, and every frame carries a different word, so `held` differs
// from `cur` at the right slot and a swapped channel is a different stream.
//
// Decoded from BCLK/LRCLK/SDATA as a DAC does, never from i2s_tx's own
// registers. The sample strobed in frame f is on the wire, in BOTH slots, in
// period f+1 (contract 13, D = 1).
//
//   prints   tb_i2s_tx: periods <n> L_bad <n> R_bad <n> LR_differ <n>
`timescale 1ns/1ps
`default_nettype none
module tb_i2s_tx;
    parameter STROBE_CYC = 20;
    parameter FRAMES     = 40;

    reg clk = 0;
    always #40.69 clk = ~clk;
    reg rst_n = 0;
    reg [7:0] cyc = 8'd0;
    reg sample_valid = 0;
    reg [15:0] sample = 0;
    wire bclk, lrclk, sdata;

    i2s_tx dut (.clk(clk), .rst_n(rst_n), .cyc(cyc), .sample_valid(sample_valid),
                .sample(sample), .bclk(bclk), .lrclk(lrclk), .sdata(sdata));

    integer frame = 0;
    function [15:0] word(input integer f);
        word = 16'h1234 + f * 16'h0B1D;               // distinct every frame
    endfunction

    always @(posedge clk) begin
        if (rst_n) begin
            cyc <= cyc + 8'd1;
            sample_valid <= (cyc == STROBE_CYC - 1);
            if (cyc == STROBE_CYC - 1) sample <= word(frame);
            if (cyc == 8'd255) frame <= frame + 1;
        end
    end

    // wire decoder: BCLK k = cyc[6:2]; SDATA is stable at the rising edge (cyc[1:0] == 2).
    reg [15:0] shreg = 0;
    reg [15:0] left = 0;
    integer periods = 0, l_bad = 0, r_bad = 0, lr_differ = 0;
    always @(posedge clk) if (rst_n && cyc[1:0] == 2'd2) begin
        if (cyc[6:2] >= 5'd1 && cyc[6:2] <= 5'd16) shreg = {shreg[14:0], sdata};
        if (cyc[6:2] == 5'd16) begin
            if (!cyc[7]) left = shreg;
            else if (frame >= 2) begin                // period `frame` carries word(frame-1)
                periods = periods + 1;
                if (left  !== word(frame - 1)) l_bad = l_bad + 1;
                if (shreg !== word(frame - 1)) r_bad = r_bad + 1;
                if (left !== shreg) lr_differ = lr_differ + 1;
            end
        end
    end

    initial begin
        repeat (4) @(posedge clk);
        rst_n = 1;
        wait (frame == FRAMES);
        $display("tb_i2s_tx: periods %0d L_bad %0d R_bad %0d LR_differ %0d",
                 periods, l_bad, r_bad, lr_differ);
        $finish;
    end
endmodule
`default_nettype wire

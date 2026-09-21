// tb_ladder_n.v -- bit-exact bench for ladder_dp_n (NCH channels) against
// model/fixed.py: tb_ladder.v's 128-bit vector format (see there), every
// y_out compared with no tolerance and its channel tag checked.
// verify_ladder.py --nch N runs it.
//
// ROW j GOES TO CHANNEL j % NCH, and to that channel only. The earlier version
// of this bench drove EVERY row to EVERY channel, with x_in, g, k, gain and
// ogain assigned once outside the per-channel loop (lines 59-61 as it stood),
// so every channel ran the identical signal at the identical cutoff, Q and
// gains. That is not what the chip does: NCH = 2 here is the voice's filter
// context and the DRUM FILTER's, and the drum filter has its own DCUT, DK,
// DGAIN and DOGAIN (ARCHITECTURE.md 4.1). A bench in which the two contexts
// can never differ has not tested that they can.
//
// MEASURED, and it corrects the claim this fix was asked to make: the old
// stimulus is NOT blind to cross-channel state bleeding.
// INJECT_BUG_LADDER_CH_BLEED shares the half-sample delay line across all
// channels -- the classic "forgot to index by channel" -- and BOTH stimuli
// catch it (--legacy-stimulus: 56 681 of 57 600 differ, first at sample 413;
// row-per-channel: 28 210 of 28 800, first at sample 249). That is not an
// accident: two interleaved channels sharing one state register each advance
// it every row, so neither can be reading its own history, whatever the
// stimulus. The change is still right -- each channel now runs its own signal
// and its own coefficients, which is the thing the bench claimed to test, and
// it does NCH times less simulation for the same number of rows -- but
// "cross-channel bleeding was undetectable" was not true, and is not claimed.
// --legacy-stimulus keeps the old behaviour so both numbers stay reproducible.
//
// NOTE ON WHAT NCH IS. NCH = 2 in this chip is TWO FILTER CONTEXTS -- the
// voice's and the drum filter's -- not two notes. The voice has THREE
// oscillators (model/voice_fx.py line 533, range(3)) summed into ONE filter.
// NCH is not a paraphony count and must not be read as one.

`timescale 1ns/1ps
module tb_ladder_n;
    parameter NCH      = 1;
    localparam CHW = (NCH > 1) ? $clog2(NCH) : 1;
    reg [CHW-1:0] ch = 0; wire [CHW-1:0] y_ch; integer c;
    parameter LOG2N    = 4;
    parameter ROM_FILE = "tanh16.hex";
    parameter OW       = 19;
    parameter MAXN     = 1 << 17;

    reg clk = 0, rst_n = 0, sample_valid = 0;
    reg signed [15:0] x_in = 0;
    reg        [15:0] g = 0;
    reg        [16:0] k = 0;
    reg        [19:0] gain = 0, ogain = 0;
    wire signed [OW-1:0] y_out;
    wire                 y_valid;

    ladder_dp_n #(.TANH_LOG2N(LOG2N), .ROM_FILE(ROM_FILE), .NCH(NCH), .OW(OW)) dut (
        .clk(clk), .rst_n(rst_n), .sample_valid(sample_valid), .os2x(1'b1), .ch(ch), .x_in(x_in),
        .g(g), .k(k), .gain(gain), .ogain(ogain), .y_out(y_out), .y_valid(y_valid), .y_ch(y_ch));

    always #10 clk = ~clk;

    reg [127:0] vec [0:MAXN-1];
    reg [8*256-1:0] vecfile, outfile;
    integer n, i, fd, nout, mism, first_i, first_exp, first_got, maxerr, err, timeout;
    reg signed [23:0] expv;

    // Every y_valid is one output sample: record it and score it.
    always @(posedge clk) if (rst_n && y_valid) begin
`ifdef LEGACY_STIMULUS
        expv = vec[nout / NCH][23:0];
`else
        expv = vec[nout][23:0];
`endif
        if (y_ch !== nout % NCH) mism = mism + 1000000;
        if (^y_out === 1'bx) $fdisplay(fd, "x"); else $fdisplay(fd, "%0d", y_out);
        if (^y_out === 1'bx || y_out !== expv) begin
            if (mism == 0) begin first_i = nout; first_exp = expv; first_got = y_out; end
            mism = mism + 1;
        end
        err = (^y_out === 1'bx) ? (1 << OW) : (y_out > expv) ? y_out - expv : expv - y_out;
        if (err > maxerr) maxerr = err;
        nout = nout + 1;
    end

    initial begin
        if (!$value$plusargs("vec=%s", vecfile)) vecfile = "build/ladder_vectors.hex";
        if (!$value$plusargs("out=%s", outfile)) outfile = "build/ladder_rtl_out.txt";
        for (i = 0; i < MAXN; i = i + 1) vec[i] = {128{1'bx}};
        $readmemh(vecfile, vec);
        n = 0; while (n < MAXN && vec[n] !== {128{1'bx}}) n = n + 1;
        fd = $fopen(outfile, "w");
        nout = 0; mism = 0; maxerr = 0; first_i = -1; first_exp = 0; first_got = 0;
        repeat (4) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
        for (i = 0; i < n; i = i + 1) begin
            @(negedge clk);
            // row i belongs to ONE channel; each channel therefore runs its own
            // signal, its own cutoff sweep and its own resonance
            x_in  = vec[i][127:112]; g = vec[i][111:96]; k = vec[i][95:72];
            gain  = vec[i][71:48];   ogain = vec[i][47:24];
`ifdef LEGACY_STIMULUS
            for (c = 0; c < NCH; c = c + 1) begin       // THE OLD BENCH: every row to every
                ch = c;                                 // channel, so every channel holds the
                sample_valid = 1;                       // same state and bleeding is invisible
                @(negedge clk); sample_valid = 0;
                timeout = 0;
                while (!y_valid && timeout < 1000) begin @(negedge clk); timeout = timeout + 1; end
                if (timeout >= 1000) begin
                    $display("tb_ladder: TIMEOUT -- no y_valid within 1000 clocks for sample %0d", i);
                    $fclose(fd); $finish;
                end
            end
`else
            ch = i % NCH; c = ch;
            sample_valid = 1;
            @(negedge clk); sample_valid = 0;
            timeout = 0;
            while (!y_valid && timeout < 1000) begin @(negedge clk); timeout = timeout + 1; end
            if (timeout >= 1000) begin
                $display("tb_ladder: TIMEOUT -- no y_valid within 1000 clocks for sample %0d", i);
                $fclose(fd); $finish;
            end
`endif
        end
        repeat (2) @(posedge clk);
        $fclose(fd);
        $display("tb_ladder: %0d samples driven, %0d outputs, %0d mismatches, worst |error| %0d LSB",
                 n, nout, mism, maxerr);
        if (mism != 0)
            $display("tb_ladder: first mismatch at sample %0d: model %0d, RTL %0d",
                     first_i, first_exp, first_got);
`ifdef LEGACY_STIMULUS
        if (mism == 0 && nout == n * NCH) $display("tb_ladder: PASS (legacy stimulus, NCH=%0d, %0d channel-samples)", NCH, nout); else $display("tb_ladder: FAIL");
`else
        if (mism == 0 && nout == n) $display("tb_ladder: PASS (NCH=%0d, %0d rows, %0d per channel)", NCH, nout, nout / NCH); else $display("tb_ladder: FAIL");
`endif
        $finish;
    end
endmodule

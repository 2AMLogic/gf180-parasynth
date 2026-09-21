// tb_top_bx.v -- the BIT-EXACT top-level bench: synth_top through its PINS,
// compared against model/synth_top_model.py.
//
// Why this exists and tb_synth_top.v is not enough. tb_synth_top decodes the
// I2S wire and checks it against `dut.sample` -- the DUT's OWN sample stream.
// That comparison is circular: it cannot see a bit shift, a channel swap, an
// off-by-one in D, or a wrong sample, because whatever the core produces is
// also what it expects. A bug of exactly that shape shipped in trial1. Here
// the expected words come from the MODEL and nothing else; the DUT's internal
// stream is dumped too, but only as a diagnostic, so that a failure says
// whether the core or the serialiser is wrong.
//
// The SPI master sends DR 0007 revision 2's 48-bit frames. The I2S receiver is
// pin-only: it never looks inside the DUT, it tracks LRCLK transitions on BCLK
// rising edges as a PCM5102A does, and it counts the bits of every slot.
//
//   +cmd=<file>    one line per write: "wait_frames flag sec addr data"
//   +i2s=<file>    one line per completed LRCLK period: "period left right nbits_l nbits_r"
//   +samp=<file>   one line per frame: "frame sample"   (diagnostic)
//   +wrs=<file>    one line per write that reached the register port:
//                  "frame flag sec addr data predicted_frame". `predicted_frame`
//                  is derived from the CS_N PIN, not from the DUT: the frame the
//                  acceptance cycle falls in (the pin edge plus DR 0007 section
//                  5's three synchroniser cycles), plus one, because a write
//                  received during frame f applies at the start of f+1. The
//                  model is driven by the PREDICTION and the chip is separately
//                  required to agree with it -- if the model were driven by the
//                  frame the chip reported, a link that delayed a write by a
//                  frame would move the model with it and the test could never
//                  see it.
//   +frames=N      run this many frames after the last write
`timescale 1ns/1ps
module tb_top_bx;
    reg clk = 0, rst_n = 0;
    reg sck = 0, mosi = 0, cs_n = 1;
    wire miso, bclk, lrclk, sdata;
    synth_top dut (.clk(clk), .rst_n_pad(rst_n), .sck(sck), .mosi(mosi), .cs_n(cs_n), .miso(miso),
                   .bclk(bclk), .lrclk(lrclk), .sdata(sdata));
    always #40.69 clk = ~clk;                                      // 12.288 MHz

    // ---- the frame index: frame 0 is the first frame after the reset release ------
    integer fr = 0, ticks = 0;
    always @(posedge clk) if (!rst_n) begin fr <= 0; ticks <= 0; end
        else if (dut.cyc == 8'd255) begin fr <= fr + 1; ticks <= ticks + 1; end

    // ---- when SHOULD each write land? Pin observation, no DUT internals ----------
    // Pin to acceptance is three core cycles (two synchroniser stages and the
    // edge detector, DR 0007 section 5, informative). A write accepted during
    // frame f is applied at the start of frame f+1, whatever cycle of f it was
    // accepted in -- the drain snapshots the queue at the tick, so a push in the
    // tick cycle belongs to the frame that starts there and drains in the next.
    localparam PIN_TO_ACCEPT = 3;
    parameter  MAXQ = 4096;
    integer pred [0:MAXQ-1];
    integer pred_w = 0, pred_r = 0, pred_bad = 0;
    reg  csn_d = 1;
    integer csn_cd = 0;
    always @(posedge clk) if (rst_n) begin
        csn_d <= cs_n;
        if (cs_n && !csn_d) csn_cd <= PIN_TO_ACCEPT;      // CS_N rose at the pin
        else if (csn_cd > 0) begin
            csn_cd <= csn_cd - 1;
            // Count the edges, not the cycles. The pin rise is SAMPLED at edge 1
            // (csn_q[0]); csn_s follows at edge 2; csn_rise is then true for the
            // cycle between edges 2 and 3, so the push registers AT EDGE 3 --
            // which is csn_cd == 2 here, because edge 1 loaded 3 without
            // decrementing. Reading `fr` with a blocking read at that edge gives
            // the frame the acceptance cycle belongs to, before any tick update.
            if (csn_cd == 2) begin
                if (pred_w < MAXQ) pred[pred_w] = fr + 1;
                pred_w = pred_w + 1;
            end
        end
    end

    // ---- the DUT's own sample stream: a DIAGNOSTIC, never the expectation ---------
    integer samp_fd = 0, wr_fd = 0, i2s_fd = 0, env_fd = 0;
    reg signed [15:0] samp = 0;
    integer n_strobe = 0, n_nostrobe = 0, worst_cyc = 0, busy_at_tick = 0;
    reg got = 0;
    always @(posedge clk) if (rst_n) begin
        if (dut.sample_valid) begin
            samp <= dut.sample; got <= 1'b1; n_strobe = n_strobe + 1;
            if (dut.cyc > worst_cyc) worst_cyc = dut.cyc;
        end
        if (dut.cyc == 8'd0 && (dut.voice_busy || dut.drum_busy)) busy_at_tick = busy_at_tick + 1;
        if (dut.cyc == 8'd255) begin
            if (!got) n_nostrobe = n_nostrobe + 1;
            if (samp_fd) $fdisplay(samp_fd, "%0d %0d", fr, dut.sample_valid ? dut.sample : samp);
            if (env_fd) $fdisplay(env_fd, "%0d %0d %0d %0d %0d %0d %0d",
                                  fr, dut.u_voice.gate, dut.u_voice.rate_a,
                                  dut.u_voice.level_a, dut.u_voice.seg_a,
                                  dut.u_voice.rate_f, dut.u_voice.level_f);
            got <= 1'b0;
        end
        if (dut.wr_valid) begin
            if (wr_fd)
                $fdisplay(wr_fd, "%0d %0d %0d %0d %0d %0d", fr, dut.wr_flag, dut.wr_sec,
                          dut.wr_addr, dut.wr_data,
                          (pred_r < pred_w && pred_r < MAXQ) ? pred[pred_r] : -1);
            if (pred_r < pred_w && pred_r < MAXQ && pred[pred_r] != fr) pred_bad = pred_bad + 1;
            pred_r = pred_r + 1;
        end
    end

    // ---- I2S receiver: pins only, exactly what a DAC sees -------------------------
    // In a 32-BCLK slot: rising edge 0 is the delay bit, edges 1..16 the 16 data
    // bits MSB first, the rest padding. A slot ends at the LRCLK transition.
    reg [15:0] cap = 0;
    reg signed [15:0] lword = 0, rword = 0;
    integer nbit = 0, nbl = 0, nbr = 0, nper = 0, i2s_words = 0;
    reg last_lr = 0;
    always @(posedge bclk) if (rst_n) begin
        if (lrclk !== last_lr) begin                                // a slot just ended
            if (last_lr == 1'b0) begin lword = $signed(cap); nbl = nbit; end
            else begin
                rword = $signed(cap); nbr = nbit;
                if (i2s_fd) $fdisplay(i2s_fd, "%0d %0d %0d %0d %0d", nper, lword, rword, nbl, nbr);
                nper = nper + 1; i2s_words = i2s_words + 1;
            end
            nbit = 0; cap = 0; last_lr = lrclk;
        end
        if (nbit >= 1 && nbit <= 16) cap = {cap[14:0], sdata};
        nbit = nbit + 1;
    end

    // ---- SPI master, mode 0, MSB first, SCK = clk/8 = 1.536 MHz -------------------
    reg [31:0] miso_word;
    task spi_write(input flag, input sec, input [7:0] addr, input [31:0] data);
        integer b; reg [47:0] word;
        begin
            word = {flag, 6'b0, sec, addr, data};
            cs_n = 0; #200;
            for (b = 47; b >= 0; b = b - 1) begin
                mosi = word[b]; #325.5; sck = 1;
                if (47 - b < 32) miso_word[31 - (47 - b)] = miso;
                #325.5; sck = 0;
            end
            #200; cs_n = 1; #700;
        end
    endtask

    // ---- the script --------------------------------------------------------------
    integer cmd_fd, rc, wait_f, flag_i, sec_i, addr_i, data_i, n_sent = 0, run_frames = 200, t0;
    reg [8*512-1:0] cmd_file, i2s_file, samp_file, wr_file, env_file;
    task wait_ticks(input integer n); begin t0 = ticks; wait (ticks >= t0 + n); end endtask
    initial begin
        if (!$value$plusargs("cmd=%s", cmd_file))   cmd_file  = "build/top_bx_cmds.txt";
        if ($value$plusargs("i2s=%s", i2s_file))    i2s_fd    = $fopen(i2s_file, "w");
        if ($value$plusargs("samp=%s", samp_file))  samp_fd   = $fopen(samp_file, "w");
        if ($value$plusargs("wrs=%s", wr_file))     wr_fd     = $fopen(wr_file, "w");
        if ($value$plusargs("env=%s", env_file))    env_fd    = $fopen(env_file, "w");
        if ($value$plusargs("frames=%d", run_frames)) ;
        repeat (8) @(posedge clk);
        @(negedge clk) rst_n = 1;                         // released with cyc = 0: frame 0 starts here
        cmd_fd = $fopen(cmd_file, "r");
        if (cmd_fd == 0) begin $display("tb_top_bx: cannot open %0s", cmd_file); $finish; end
        while (!$feof(cmd_fd)) begin
            rc = $fscanf(cmd_fd, "%d %d %d %d %d\n", wait_f, flag_i, sec_i, addr_i, data_i);
            if (rc == 5) begin
                if (wait_f > 0) wait_ticks(wait_f);
                spi_write(flag_i[0], sec_i[0], addr_i[7:0], data_i);
                n_sent = n_sent + 1;
                if (n_sent == 1) $display("tb_top_bx: status word on MISO = %08x", miso_word);
            end
        end
        $fclose(cmd_fd);
        wait_ticks(run_frames);
        $display("tb_top_bx: %0d writes sent, %0d frames, %0d I2S periods decoded", n_sent, fr, i2s_words);
        $display("tb_top_bx: transactions accepted at the pin %0d, writes drained %0d, landing frame differs from the pin prediction %0d times (must be 0)",
                 pred_w, pred_r, pred_bad);
        $display("tb_top_bx: sample strobed in %0d frames, MISSING in %0d, worst strobe cycle %0d of 256",
                 n_strobe, n_nostrobe, worst_cyc);
        $display("tb_top_bx: datapath busy at a tick: %0d (must be 0); overrun %0d; overflow %0d",
                 busy_at_tick, dut.overrun, dut.u_spi.overflow);
        if (i2s_fd) $fclose(i2s_fd);
        if (samp_fd) $fclose(samp_fd);
        if (wr_fd) $fclose(wr_fd);
        $finish;
    end
endmodule

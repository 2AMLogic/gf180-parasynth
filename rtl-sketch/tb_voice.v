// tb_voice.v -- bit-exact bench for voice_dp against model/voice_fx.py, at
// the register port of contract 16.2 (the physical layer bypassed, so the
// bench decides exactly which frame every write lands in).
//
// verify_voice.py writes two files: +wr=<writes>, one "frame flag addr data"
// line per write in application order, and +exp=<expected>, one line per
// frame whose FIRST field is the model's int16 sample (the rest are the
// model's taps; verify_voice.py compares those, this bench compares only the
// sample as a convenience). The bench runs its own 256-cycle frames, as the
// chip does: the writes of frame f are applied one per cycle from cycle 1,
// `go` is pulsed at cycle GO (after the last write; synth_top uses cycle 8),
// and the sample strobed during the frame is recorded. The drum bus is silent
// and always "done", so the master mix is the contract's sat16((v * vol) >> 15).
//
// Every tap of contract 16.4 goes to +out=, one line per frame:
//   frame sample osc0 osc1 osc2 inc0 inc1 inc2 sh0 sh1 sh2 r0 r1 r2
//         mixed ae fe cut g kc k_eff y19 v out_v cycles
// where sh = e + 15 is the reciprocal's shift (recip_div.v), v the VCA's
// output, out_v = (v * vol) >> 15 before the rail -- which since the master mix
// was rebuilt to contract 12 is no longer a register in voice_dp: the chip
// keeps `macc` = v * vol EXACTLY and shifts once, at the end, over the sum of
// all three products. The column is `macc >>> 15`, the same number, so
// verify_voice.py's comparison is unchanged -- and cycles the cycles
// from `go` to sample_valid in that frame. The oscillator taps are read
// hierarchically from voice_dp's sequencer in the cycle each oscillator is
// mixed (state S_MIX), kc and v from its multiplier operand registers, so
// no tap costs the RTL a flop. A final line, "STATE phase0..2 inc_acc0..2
// level_a level_f seg_a seg_f", is the state the model must also end in.
// verify_voice.py is the comparator of record.
`timescale 1ns/1ps
module tb_voice;
    parameter GO   = 48;              // writes occupy cycles 1..GO-2 (46; the whole patch image is 27)
    parameter MAXW = 1 << 17;
    parameter MAXN = 1 << 20;          // the full set is 383,460 frames since contract rev 9;
                                       // at 1 << 18 the bench stopped short and SAID so (status 2)
    // voice_dp's sequencer states this bench taps; they must match voice_dp.v
    // THESE TRACK voice_dp.v's STATE ENCODING BY NUMBER and must be updated with
    // it. S_VCA2 was 32 until the master mix was rebuilt to contract 12 (which
    // removed S_VCA0 and added S_DM1/S_DM2/S_DFLT); at 32 the bench read `ma`
    // one cycle early and the `v` tap column was wrong on 35 737 frames while
    // every audio sample still matched. The `t_v_seen` check below makes that
    // failure loud instead of silent.
    localparam S_MIX = 6, S_KEFF1 = 15, S_VCA2 = 33;
    // New states are APPENDED in voice_dp.v (36 and up) precisely so these three
    // numbers do not move when the datapath grows.

    reg clk = 0, rst_n = 0;
    reg go = 0, wr_valid = 0, wr_flag = 0;
    reg [7:0]  wr_addr = 0;                    // DR 0007 revision 2: 8 bits on page 0
    reg [31:0] wr_data = 0;                    //                      32-bit datum
    wire signed [15:0] sample;
    wire sample_valid, busy;
    wire signed [15:0] mixed; wire [14:0] ae, fe, cut; wire [16:0] k_eff; wire signed [18:0] y19;
    voice_dp dut (.clk(clk), .rst_n(rst_n), .go(go),
                  .wr_valid(wr_valid), .wr_flag(wr_flag), .wr_addr(wr_addr), .wr_data(wr_data),
                  .dmix(22'sd0), .body(19'sd0), .drum_done(1'b1),
                  .sample(sample), .sample_valid(sample_valid), .busy(busy),
                  .mixed(mixed), .ae(ae), .fe(fe), .cut(cut), .k_eff(k_eff), .y19(y19));
    always #10 clk = ~clk;

    // ---- taps read from inside the sequencer ----------------------------------
    reg signed [15:0] t_osc [0:2];
    reg        [23:0] t_inc [0:2];
    reg        [4:0]  t_sh  [0:2];
    reg        [15:0] t_r   [0:2];
    reg        [15:0] t_kc;
    reg signed [24:0] t_v;
    reg t_v_seen = 0;                                    // did S_VCA2 ever happen? (see the localparams)
    reg st_def   = 0;                                    // was `state` ever DEFINED this frame?
    always @(posedge clk) begin
`ifdef VOICE_OSC_2X
        if (dut.state == dut.S_OSCWAIT && dut.shape_osc2x && dut.osc2_valid) begin
            t_osc[dut.kk] <= dut.osc2_sample;
            t_inc[dut.kk] <= dut.inc_mod[dut.kk]; t_sh[dut.kk] <= dut.sh[dut.kk]; t_r[dut.kk] <= dut.r[dut.kk];
        end
        if (dut.state == S_MIX && !dut.shape_osc2x) begin
            t_osc[dut.kk] <= dut.osc;
            t_inc[dut.kk] <= dut.inc_mod[dut.kk]; t_sh[dut.kk] <= dut.sh[dut.kk]; t_r[dut.kk] <= dut.r[dut.kk];
        end
`else
        if (dut.state == S_MIX) begin                     // oscillator kk is being mixed: its sample, inc, (e, r)
            t_osc[dut.kk] <= dut.osc;
            t_inc[dut.kk] <= dut.inc_mod[dut.kk];      // the MODULATED increment (6.9)
            t_sh[dut.kk]  <= dut.sh[dut.kk];
            t_r[dut.kk]   <= dut.r[dut.kk];
        end
`endif
        if (dut.state == S_KEFF1) t_kc <= dut.mb[15:0];   // the operand loaded at S_KEFF0 is kc
        if (^dut.state !== 1'bx)  st_def = 1'b1;
        if (dut.state == S_VCA2)  begin t_v <= dut.ma; t_v_seen = 1'b1; end
                                                         // the operand loaded at S_VCA1 is v = (y * ae) >> 15
    end

    reg [8*512-1:0] wrfile, expfile, outfile, line;
    integer wfd, efd, ofd, rc, nw, nexp, f, cyc, wi, nout, mism, first_f, first_exp, first_got, maxerr, err;
    integer lat, lat_total, lat_worst, lat_best;
    integer wr_f [0:MAXW-1]; integer wr_fl [0:MAXW-1]; integer wr_a [0:MAXW-1]; integer wr_d [0:MAXW-1];
    integer expv [0:MAXN-1];
    reg signed [15:0] got; reg got_valid;

    initial begin
        if (!$value$plusargs("wr=%s", wrfile)) wrfile = "build/voice_writes.txt";
        if (!$value$plusargs("exp=%s", expfile)) expfile = "build/voice_expected.txt";
        if (!$value$plusargs("out=%s", outfile)) outfile = "build/voice_rtl_out.txt";
        wfd = $fopen(wrfile, "r"); nw = 0;
        while (!$feof(wfd) && nw < MAXW) begin
            rc = $fscanf(wfd, "%d %d %d %d\n", wr_f[nw], wr_fl[nw], wr_a[nw], wr_d[nw]);
            if (rc == 4) nw = nw + 1;
        end
        $fclose(wfd);
        efd = $fopen(expfile, "r"); nexp = 0;
        while (!$feof(efd) && nexp < MAXN) begin
            // Only the first integer is used here; Python compares all taps.
            // Scanning the entire 512-byte buffer hits Verilator 5.020's
            // VL_VALUE_STRING_MAX_WORDS limit. Read the integer from the file
            // and consume the remaining fields (or the final STATE row).
            rc = $fscanf(efd, "%d", expv[nexp]);
            if (rc == 1) nexp = nexp + 1;
            rc = $fgets(line, efd);
        end
        $fclose(efd);
        ofd = $fopen(outfile, "w");
        nout = 0; mism = 0; maxerr = 0; first_f = -1; first_exp = 0; first_got = 0; wi = 0;
        lat_total = 0; lat_worst = 0; lat_best = 1 << 20;
        repeat (4) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
        for (f = 0; f < nexp; f = f + 1) begin
            got_valid = 0; lat = -1;
            for (cyc = 0; cyc < 256; cyc = cyc + 1) begin
                @(negedge clk);
                wr_valid = 0; go = 0;
                if (cyc >= 1 && cyc < GO - 1 && wi < nw && wr_f[wi] == f) begin
                    wr_valid = 1; wr_flag = wr_fl[wi]; wr_addr = wr_a[wi]; wr_data = wr_d[wi]; wi = wi + 1;
                end
                if (cyc == GO) go = 1;
                if (cyc == GO + 1 && wi < nw && wr_f[wi] == f) begin
                    $display("tb_voice: frame %0d has more writes than cycles 1..%0d", f, GO - 2); $finish;
                end
                @(posedge clk); #1;
                if (sample_valid) begin got = sample; got_valid = 1; lat = cyc - GO; end
            end
            if (busy) begin $display("tb_voice: datapath still busy at the end of frame %0d", f); $finish; end
            // The encoding-moved guard, and why it is conditional. Against the
            // all-X stub of docs/verification-rules.md section 1 `state` is X, so
            // S_VCA2 can never match -- and aborting there would turn the stub's
            // RED RUN from "mismatch" (status 1) into "did not run" (status 2),
            // which is the outcome that rule exists to forbid. So the guard only
            // fires when the DUT's state was DEFINED and S_VCA2 still never
            // happened: that is an encoding change, not an unimplemented design.
            if (st_def && !t_v_seen) begin
                $display("tb_voice: the S_VCA2 tap never fired in frame %0d -- voice_dp's state encoding moved; fix the localparams at the top of this file", f);
                $finish;
            end
            t_v_seen = 1'b0; st_def = 1'b0;
            if (!got_valid) begin $display("tb_voice: no sample in frame %0d", f); $finish; end
            lat_total = lat_total + lat; if (lat > lat_worst) lat_worst = lat; if (lat < lat_best) lat_best = lat;
            $fdisplay(ofd, "%0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                      f, got, t_osc[0], t_osc[1], t_osc[2], t_inc[0], t_inc[1], t_inc[2],
                      t_sh[0], t_sh[1], t_sh[2], t_r[0], t_r[1], t_r[2],
                      mixed, ae, fe, cut, dut.g, t_kc, k_eff, y19, t_v, ($signed(dut.macc) >>> 15),
                      dut.phase_os2[0], dut.phase_os2[1], dut.phase_os2[2], lat);
            if (got !== expv[f]) begin
                if (mism == 0) begin first_f = f; first_exp = expv[f]; first_got = got; end
                mism = mism + 1;
            end
            err = (got > expv[f]) ? got - expv[f] : expv[f] - got;
            if (err > maxerr) maxerr = err;
            nout = nout + 1;
        end
        $fdisplay(ofd, "STATE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                  dut.phase[0], dut.phase[1], dut.phase[2], dut.inc_acc[0], dut.inc_acc[1], dut.inc_acc[2],
                  dut.level_a, dut.level_f, dut.seg_a, dut.seg_f,
                  dut.phase_os2[0], dut.phase_os2[1], dut.phase_os2[2],
                  dut.drift_cnt, $signed(dut.drift_acc[0]), $signed(dut.drift_acc[1]),
                  $signed(dut.drift_acc[2]));
        $fclose(ofd);
        $display("tb_voice: %0d writes, %0d frames, %0d sample mismatches, worst |error| %0d LSB", nw, nout, mism, maxerr);
        $display("tb_voice: cycles from go to sample_valid: best %0d, mean %0d, worst %0d (chip: go at cycle 8 of 256)",
                 lat_best, (nout > 0) ? lat_total / nout : 0, lat_worst);
        if (mism != 0) $display("tb_voice: first mismatch at frame %0d: model %0d, RTL %0d", first_f, first_exp, first_got);
        if (mism == 0 && nout == nexp) $display("tb_voice: PASS"); else $display("tb_voice: FAIL");
        $finish;
    end
endmodule

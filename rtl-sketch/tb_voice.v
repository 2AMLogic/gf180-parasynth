// tb_voice.v -- bit-exact bench for voice_dp against model/voice_fx.py, at
// the register port of contract 16.2 (the physical layer bypassed, so the
// bench decides exactly which frame every write lands in).
//
// verify_voice.py writes two files: +wr=<writes>, one "frame flag addr data"
// line per write in application order, and +exp=<expected>, one line per
// frame whose FIRST field is the model's int16 sample (the rest are the
// model's taps; verify_voice.py compares those, this bench compares only the
// sample as a convenience). The bench runs its own 256-cycle frames, as the
// chip does: the writes of
// frame f are applied one per cycle IMMEDIATELY BEFORE `go`, which is pulsed
// at cycle GO -- synth_top's GO_CYCLE, the PRODUCTION launch (verify_voice.py
// refuses a bench whose GO differs from synth_top.v's) -- and the sample
// strobed during the frame is recorded.
//
// THE SETUP/LAUNCH CONTRACT (plan075 T1, docs/deadline/README.md). Until
// 2026-09-25 this bench applied a frame's writes from cycle 1 and pulsed `go`
// at cycle 48, so the datapath had 208 cycles where the chip gives it 248: a
// configuration that fits the chip could be refused here ("datapath still
// busy at the end of frame 0", three 2x saws plus the 2x filter, PR #235).
// Now `go` is at the chip's cycle and the frame's writes occupy the n cycles
// ending at GO - 1. When n > GO - 1 they begin in the PREVIOUS frame's tail,
// which is observationally the same register state at `go` (voice_dp has no
// tick input; it computes only between go and busy falling), and the bench
// ASSERTS the datapath is idle in every write cycle -- a write that would land
// mid-computation REFUSES the run (it is not the chip's schedule) rather than
// being applied. The chip's own drain delivers at most six writes per frame
// (four SPI, two UART), all before cycle 8; the bench's larger bursts are the
// verification convenience of whole-image loads in one frame.
//
// THE DEADLINE, checked here as the chip defines it: busy in the last cycle of
// a frame is an OVERRUN (synth_top latches `overrun` when a datapath is busy
// at the tick) and a strobe after cycle 254 is a LATE SAMPLE (i2s_tx loads the
// next period's word at cycle 255). Either is a FAIL of the design, reported
// as such -- the verification of a real-time datapath, not a bench limit.
//
// The drum bus is silent and always "done", so the master mix is the contract's sat16((v * vol) >> 15).
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
    parameter GO   = 8;               // synth_top's GO_CYCLE: the production launch (see the header)
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
    integer abs_cyc, g, j, k0, strobe_worst, busy_last, busy_worst, late_n, first_late, first_late_cyc;
    integer wr_abs [0:MAXW-1];                 // the absolute cycle each write is applied in
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
        // the n writes of frame g occupy the n cycles ending at cycle GO - 1 of frame g
        k0 = 0;
        while (k0 < nw) begin
            g = k0;
            while (g < nw && wr_f[g] == wr_f[k0]) g = g + 1;
            for (j = k0; j < g; j = j + 1) wr_abs[j] = 256 * wr_f[k0] + GO - (g - k0) + (j - k0);
            if (k0 > 0 && wr_abs[k0] <= wr_abs[k0 - 1]) begin
                $display("tb_voice: REFUSED -- frame %0d has %0d writes, more than one frame of cycles before go", wr_f[k0], g - k0);
                $finish;
            end
            k0 = g;
        end
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
        strobe_worst = -1; busy_worst = -1; late_n = 0; first_late = -1; first_late_cyc = -1;
        repeat (4) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
        // frame -1 is a pre-roll with no `go`: the tail that frame 0's writes may need
        for (f = -1; f < nexp; f = f + 1) begin
            got_valid = 0; lat = -1; busy_last = -1;
            for (cyc = 0; cyc < 256; cyc = cyc + 1) begin
                abs_cyc = 256 * f + cyc;
                @(negedge clk);
                wr_valid = 0; go = 0;
                if (wi < nw && wr_abs[wi] == abs_cyc) begin
                    if (busy === 1'b1) begin
                        $display("tb_voice: REFUSED -- frame %0d's writes begin in cycle %0d of frame %0d, while the datapath is still computing that frame: not the chip's schedule", wr_f[wi], cyc, f);
                        $finish;
                    end
                    wr_valid = 1; wr_flag = wr_fl[wi]; wr_addr = wr_a[wi]; wr_data = wr_d[wi]; wi = wi + 1;
                end
                if (f >= 0 && cyc == GO) begin
                    if (wi < nw && wr_f[wi] <= f) begin
                        $display("tb_voice: REFUSED -- frame %0d's writes were not all applied before go", f); $finish;
                    end
                    go = 1;
                end
                @(posedge clk); #1;
                // Read just AFTER the edge that ends cycle `cyc`, a registered output is
                // the value the chip holds DURING cycle cyc + 1 -- the index synth_top's
                // i2s_tx and overrun logic see, and the one the DEADLINE fields report.
                // (busy after the edge ending cycle 255 is busy at the tick: the
                // OVERRUN check below, not a last-busy cycle.)
                if (busy === 1'b1 && cyc < 255) busy_last = cyc + 1;
                if (f >= 0 && sample_valid) begin
                    got = sample; got_valid = 1; lat = cyc - GO;
                    if (cyc + 1 > strobe_worst) strobe_worst = cyc + 1;
                    if (cyc + 1 > 254) begin
                        late_n = late_n + 1;
                        if (first_late < 0) begin first_late = f; first_late_cyc = cyc + 1; end
                    end
                end
            end
            if (f >= 0) begin
            if (busy_last > busy_worst) busy_worst = busy_last;
            if (busy === 1'b1) begin
                // the chip's `overrun`: a datapath busy at the tick. A FAIL of the design at the
                // production launch -- not a bench budget -- so it is reported as one.
                $display("tb_voice: OVERRUN at frame %0d: the datapath is still busy at the end of the frame (go at cycle %0d of 256, the production launch)", f, GO);
                $display("tb_voice: FAIL");
                $finish;
            end
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
        $display("tb_voice: DEADLINE go at cycle %0d; worst strobe cycle %0d (sample slack %0d to cycle 254); worst last-busy cycle %0d (busy slack %0d to cycle 255); late samples %0d",
                 GO, strobe_worst, 254 - strobe_worst, busy_worst, 255 - busy_worst, late_n);
        if (late_n != 0) $display("tb_voice: LATE SAMPLE: %0d frame(s) strobed after cycle 254, first frame %0d at cycle %0d", late_n, first_late, first_late_cyc);
        if (mism != 0) $display("tb_voice: first mismatch at frame %0d: model %0d, RTL %0d", first_f, first_exp, first_got);
        if (mism == 0 && nout == nexp && late_n == 0) $display("tb_voice: PASS"); else $display("tb_voice: FAIL");
        $finish;
    end
endmodule

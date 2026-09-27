// tb_f1_chain.v -- the SELECTED voice filter path, in RTL, driven by external audio.
//
// WHY THIS BENCH EXISTS. The F1A/F1B/F1C cutoff-response cases scored the
// fixed-point model of the selected filter path (`model/filter_rate_chain.py`
// RateConvertedLadder, built by `tools/f1_filter_path.py`). Nothing on the
// Filters family had ever been measured on the instrument that ships. To score
// an F1 case on the RTL, the stepped-tone stimulus has to enter the FILTER --
// and `synth_top` has no audio input, its filter is fed by the oscillator
// mixer. So this bench composes the two modules the voice composes, wired
// exactly as `voice_dp.v` wires them under `VOICE_FILTER_2X`:
//
//     x (Q1.15, base rate)
//       -> rate_conv_2x  interpolation  (x_even, x_odd, Q2.30 taps)
//       -> ladder_dp_n   NCH=1, os2x=0  (one 96 kHz update per subframe)
//       -> rate_conv_2x  decimation     (y_even/y_odd -> 19-bit y_out)
//
// The sequencing below is a transcription of voice_dp.v's filter block
// (`filter_input_valid` -> capture interp words -> ladder subframe 0 -> ladder
// subframe 1 -> read the combinational `y_out` on the second `y_valid`), not a
// new design. If it drifts from the voice, `tools/f1_rtl_filter_path.py`'s
// bit-exact comparison against the model chain fails and no number is reported.
//
// Vectors: +vec=<file> holds one decimal signed Q1.15 input per line. The
// coefficient words are constant over an F1 curve (one commanded cutoff, one
// resonance, one drive) and are passed as plusargs, so the vector file stays a
// single column and the host image's words are visibly the ones driven:
//
//   +g=<Q0.16>  +k=<Q3.14, 17 bit>  +gain=<Q4.16>  +ogain=<Q4.16>
//
// Output: +out=<file>, one decimal 19-bit (Q4.15) base-rate sample per line,
// one per input frame. `tools/f1_rtl_filter_path.py` is the comparator of
// record; the line printed here is a convenience.
`timescale 1ns/1ps
`default_nettype none
module tb_f1_chain;
    parameter LOG2N    = 4;
    parameter ROM_FILE = "tanh16.hex";
    parameter OW       = 19;

    reg clk = 1'b0, rst_n = 1'b0;
    always #10 clk = ~clk;

    reg                interp_valid = 1'b0;
    reg  signed [15:0] x_in = 16'sd0;
    wire signed [16:0] interp_even, interp_odd;
    reg  signed [16:0] x_even_r = 17'sd0, x_odd_r = 17'sd0;
    reg  signed [18:0] y_even_r = 19'sd0;
    reg                phase = 1'b0;
    reg                lad_sv = 1'b0;
    reg         [15:0] g = 16'd0;
    reg         [16:0] k = 17'd0;
    reg         [19:0] gain = 20'd0, ogain = 20'd0;
    wire signed [OW-1:0] lad_y;
    wire                 lad_yv;
    wire signed [18:0]   y_out;

    // The decimator consumes the pair (even subframe, odd subframe) on the odd
    // subframe's y_valid -- voice_dp.v's `(lad_yv && !lad_ych) && voice_filter_phase`.
    wire decim_valid = lad_yv && phase;

    rate_conv_2x u_rc (
        .clk(clk), .rst_n(rst_n),
        .interp_valid(interp_valid), .x_in(x_in),
        .x_even(interp_even), .x_odd(interp_odd),
        .decim_valid(decim_valid), .y_even(y_even_r), .y_odd(lad_y),
        .y_out(y_out));

    // NEGATIVE CONTROL: the interpolator is bypassed, the base-rate word driven
    // into both subframes -- voice_dp.v's own INJECT_BUG_VOICE_FILTER2X_OFF. The
    // F1 probe sits at -12 dBFS and never reaches the ladder's saturation or the
    // interpolator's clamp, so the arithmetic-corner injects in ladder_dp_n.v /
    // rate_conv_2x.v are NOT exercised by this stimulus (recorded as such on the
    // result). These two are: they change every frame of every F1 curve.
`ifdef INJECT_BUG_F1_CHAIN_SKIP_INTERP
    wire signed [16:0] lad_x = {{1{x_in[15]}}, x_in};
`else
    wire signed [16:0] lad_x = phase ? x_odd_r : x_even_r;
`endif
    wire lad_ych;

    ladder_dp_n #(.NCH(1), .TANH_LOG2N(LOG2N), .ROM_FILE(ROM_FILE), .OW(OW)) u_lad (
        .clk(clk), .rst_n(rst_n), .sample_valid(lad_sv), .os2x(1'b0), .ch(1'b0),
        .x_in(lad_x), .g(g), .k(k), .gain(gain), .ogain(ogain),
        .y_out(lad_y), .y_valid(lad_yv), .y_ch(lad_ych));

    reg [8*256-1:0] vecfile, outfile;
    integer fdin, fdout, rc, xv, gv, kv, gainv, ogainv;
    integer frames_in, frames_out, stall, st;
    reg eof;

    initial begin
        if (!$value$plusargs("vec=%s", vecfile)) begin
            $display("tb_f1_chain: REFUSED -- missing +vec=<file>");
            $fatal(2);
        end
        if (!$value$plusargs("out=%s", outfile)) begin
            $display("tb_f1_chain: REFUSED -- missing +out=<file>");
            $fatal(2);
        end
        if (!$value$plusargs("g=%d", gv) || !$value$plusargs("k=%d", kv)
                || !$value$plusargs("gain=%d", gainv)
                || !$value$plusargs("ogain=%d", ogainv)) begin
            $display("tb_f1_chain: REFUSED -- missing +g/+k/+gain/+ogain");
            $fatal(2);
        end
        fdin = $fopen(vecfile, "r");
        if (!fdin) begin
            $display("tb_f1_chain: REFUSED -- cannot open %0s", vecfile);
            $fatal(2);
        end
        fdout = $fopen(outfile, "w");
        if (!fdout) begin
            $display("tb_f1_chain: REFUSED -- cannot open %0s for writing", outfile);
            $fatal(2);
        end
        g = gv[15:0]; k = kv[16:0]; gain = gainv[19:0]; ogain = ogainv[19:0];
        frames_in = 0; frames_out = 0; stall = 0; st = 0; eof = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
    end

    // One frame at a time, in voice_dp.v's order. `st` is this bench's own
    // sequencer; every signal the two modules see is driven from one block, so
    // there is no second driver and no race with the stimulus.
    always @(posedge clk) begin
        if (!rst_n) begin
            interp_valid <= 1'b0; lad_sv <= 1'b0; phase <= 1'b0;
            x_even_r <= 17'sd0; x_odd_r <= 17'sd0; y_even_r <= 19'sd0;
        end else begin
            interp_valid <= 1'b0;
            lad_sv <= 1'b0;
            case (st)
                0: begin                                   // next base-rate frame
                    rc = $fscanf(fdin, "%d\n", xv);
                    if (rc == 1) begin
                        x_in <= xv[15:0];
                        interp_valid <= 1'b1;
                        frames_in = frames_in + 1;
                        st <= 1;
                    end else begin
                        eof <= 1'b1;
                        st <= 3;
                    end
                end
                1: begin                                   // interp_valid is high now
                    x_even_r <= interp_even;                //   (voice_dp: filter_x_even)
                    x_odd_r <= interp_odd;
                    phase <= 1'b0;
                    lad_sv <= 1'b1;
                    stall <= 0;
                    st <= 2;
                end
                2: begin
                    if (lad_yv) begin
                        if (!phase) begin
                            y_even_r <= lad_y;             // voice_dp: filter_y_even
                            phase <= 1'b1;
                            lad_sv <= 1'b1;
                            stall <= 0;
                        end else begin
                            // y_out is combinational from (y_even_r, lad_y) and
                            // decim_valid shifts the FIR history at this edge --
                            // voice_dp reads `decimated_voice_y` here too.
`ifdef INJECT_BUG_F1_CHAIN_DROP_DECIM
                            $fdisplay(fdout, "%0d", y_even_r);  // NEGATIVE CONTROL: no
                                                                // decimation filter, just
                                                                // the even subframe
`else
                            $fdisplay(fdout, "%0d", y_out);
`endif
                            frames_out = frames_out + 1;
                            phase <= 1'b0;
                            st <= 0;
                        end
                    end else begin
                        stall <= stall + 1;
                        if (stall > 2000) begin
                            $display("tb_f1_chain: TIMEOUT -- no y_valid within 2000 clocks at frame %0d",
                                     frames_in);
                            $fclose(fdout); $fclose(fdin);
                            $finish;
                        end
                    end
                end
                3: begin
                    $display("tb_f1_chain: DONE frames_in=%0d frames_out=%0d", frames_in, frames_out);
                    $fclose(fdout); $fclose(fdin);
                    $finish;
                end
            endcase
        end
    end
endmodule
`default_nettype wire

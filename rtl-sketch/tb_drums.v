// tb_drums.v -- bit-exact bench for drum_kit (drum_dp + modal_dp) against
// model/drums_fx.py.
//
// The bench IS the host's register file (contract 15.1): it holds the whole
// control image and drives it onto drum_kit's buses. verify_drums.py writes
// the model's write stream, one 64-bit word per write --
//     [63:40] frame   [39:32] address   [31:0] value
// -- in frame order; the bench applies every write for frame f before
// frame f's tick (4.3), pulses frame_tick, waits for body_valid and writes
// "mix_out body_out" for that frame to +out=. verify_drums.py compares.
//
// +frames=N   how many frames to run
// +jitter=K   NEGATIVE CONTROL of the timing contract: apply frame f's writes
//             K clocks AFTER its tick, while the datapath is busy, instead of
//             before it. The buses must be held from tick to body_valid; this
//             shows the comparison sees a violation.
// Address 0xFF is RESET: rst_n is pulled low for two clocks and the bench's
// image is cleared, as the model's write(A_RESET) does.
`timescale 1ns/1ps
module tb_drums;
    parameter ENVS = 18, PATHS = 23, MODES = 16, NUMS = 11, STOPS = 11, MW = 4;
    parameter MAXW = 1 << 16;

    reg clk = 0, rst_n = 0, frame_tick = 0;
    reg [STOPS-1:0] stops_r;
    reg [15:0] accent_r [0:STOPS-1];
    reg [23:0] osc_r    [0:5];
    reg [26:0] ectl_r   [0:ENVS-1];
    reg [23:0] peak_r   [0:ENVS-1];
    reg [15:0] rate_r   [0:ENVS-1];
    reg [15:0] frate_r  [0:ENVS-1];
    reg [24:0] path_r   [0:PATHS-1];
    reg [25:0] a1_r     [0:MODES-1];
    reg [25:0] a2_r     [0:MODES-1];
    reg [15:0] amp_r    [0:MODES-1];
    reg [1:0]  num_r    [0:MODES-1];
    reg [STOPS*16-1:0] accent_bus;
    reg [6*24-1:0]     osc_inc_bus;
    reg [ENVS*27-1:0]  env_ctl_bus;
    reg [ENVS*24-1:0]  env_peak_bus;
    reg [ENVS*16-1:0]  env_rate_bus;
    reg [ENVS*16-1:0]  env_frate_bus;
    reg [PATHS*25-1:0] path_bus;
    reg [MODES*26-1:0] a1_bus, a2_bus;
    reg [MODES*16-1:0] amp_bus;
    reg [MODES*2-1:0]  num_bus;
    integer k;
    always @* begin
        for (k = 0; k < STOPS; k = k + 1) accent_bus[k*16 +: 16] = accent_r[k];
        for (k = 0; k < 6; k = k + 1) osc_inc_bus[k*24 +: 24] = osc_r[k];
        for (k = 0; k < ENVS; k = k + 1) begin
            env_ctl_bus[k*27 +: 27] = ectl_r[k]; env_peak_bus[k*24 +: 24] = peak_r[k]; env_rate_bus[k*16 +: 16] = rate_r[k];
            env_frate_bus[k*16 +: 16] = frate_r[k];
        end
        for (k = 0; k < PATHS; k = k + 1) path_bus[k*25 +: 25] = path_r[k];
        for (k = 0; k < MODES; k = k + 1) begin
            a1_bus[k*26 +: 26] = a1_r[k]; a2_bus[k*26 +: 26] = a2_r[k]; amp_bus[k*16 +: 16] = amp_r[k]; num_bus[k*2 +: 2] = num_r[k];
        end
    end

    wire signed [21:0] mix_out; wire mix_valid;
    wire signed [18:0] body_out; wire body_valid;
    drum_kit #(.ENVS(ENVS), .PATHS(PATHS), .MODES(MODES), .NUMS(NUMS), .STOPS(STOPS), .MW(MW)) dut (
        .clk(clk), .rst_n(rst_n), .frame_tick(frame_tick), .stops(stops_r), .accent_bus(accent_bus),
        .osc_inc_bus(osc_inc_bus), .env_ctl_bus(env_ctl_bus), .env_peak_bus(env_peak_bus),
        .env_rate_bus(env_rate_bus), .env_frate_bus(env_frate_bus), .path_bus(path_bus), .a1_bus(a1_bus), .a2_bus(a2_bus),
        .amp_bus(amp_bus), .num_bus(num_bus), .mix_out(mix_out), .mix_valid(mix_valid),
        .body_out(body_out), .body_valid(body_valid));
    always #10 clk = ~clk;

    task clear_image;
        integer j;
        begin
            stops_r = 0;
            for (j = 0; j < STOPS; j = j + 1) accent_r[j] = 0;
            for (j = 0; j < 6; j = j + 1) osc_r[j] = 0;
            for (j = 0; j < ENVS; j = j + 1) begin ectl_r[j] = 0; peak_r[j] = 0; rate_r[j] = 0; frate_r[j] = 0; end
            for (j = 0; j < PATHS; j = j + 1) path_r[j] = 0;
            for (j = 0; j < MODES; j = j + 1) begin a1_r[j] = 0; a2_r[j] = 0; amp_r[j] = 0; num_r[j] = 0; end
        end
    endtask

    task apply(input [7:0] a, input [31:0] v);
        integer idx, fld;
        begin
            if (a == 8'hFF) begin
                clear_image; rst_n = 0; @(negedge clk); @(negedge clk); rst_n = 1;
            end else if (a == 8'h00) stops_r = v[STOPS-1:0];
            else if (a >= 8'h10 && a < 8'h10 + STOPS) accent_r[a - 8'h10] = v[15:0];
            else if (a >= 8'h20 && a < 8'h26) osc_r[a - 8'h20] = v[23:0];
            else if (a >= 8'h40 && a < 8'h40 + ENVS * 4) begin
                idx = (a - 8'h40) >> 2; fld = a & 3;
                if (fld == 0) ectl_r[idx] = v[26:0]; else if (fld == 1) peak_r[idx] = v[23:0]; else if (fld == 2) rate_r[idx] = v[15:0]; else frate_r[idx] = v[15:0];
            end else if (a >= 8'h90 && a < 8'h90 + PATHS) path_r[a - 8'h90] = v[24:0];
            else if (a >= 8'hB0 && a < 8'hB0 + MODES * 4) begin
                idx = (a - 8'hB0) >> 2; fld = a & 3;
                if (fld == 0) a1_r[idx] = v[25:0]; else if (fld == 1) a2_r[idx] = v[25:0];
                else if (fld == 2) amp_r[idx] = v[15:0]; else num_r[idx] = v[1:0];
            end
        end
    endtask

    reg [63:0] wr [0:MAXW-1];
    reg [8*256-1:0] wfile, outfile;
    integer nw, wi, f, nframes, fd, jitter, timeout, cyc, total, worst, nout, started;
    always @(posedge clk) if (started) cyc = cyc + 1;

    initial begin
        if (!$value$plusargs("writes=%s", wfile)) wfile = "build/drum_writes.hex";
        if (!$value$plusargs("out=%s", outfile)) outfile = "build/drum_rtl_out.txt";
        if (!$value$plusargs("frames=%d", nframes)) nframes = 1000;
        if (!$value$plusargs("jitter=%d", jitter)) jitter = 0;
        for (wi = 0; wi < MAXW; wi = wi + 1) wr[wi] = {64{1'bx}};
        $readmemh(wfile, wr);
        nw = 0; while (nw < MAXW && wr[nw] !== {64{1'bx}}) nw = nw + 1;
        clear_image;
        fd = $fopen(outfile, "w");
        wi = 0; nout = 0; total = 0; worst = 0; started = 0;
        repeat (4) @(posedge clk); rst_n = 1; repeat (2) @(negedge clk);
        for (f = 0; f < nframes; f = f + 1) begin
            if (jitter == 0)
                while (wi < nw && wr[wi][63:40] == f) begin apply(wr[wi][39:32], wr[wi][31:0]); wi = wi + 1; end
            else if (wi < nw && wr[wi][63:40] == f && wr[wi][39:32] == 8'hFF) begin
                apply(8'hFF, 0); wi = wi + 1;             // a RESET is never jittered: it would stop the frame
            end
            frame_tick = 1; cyc = 0; started = 1;
            @(negedge clk); frame_tick = 0;
            if (jitter != 0) begin
                repeat (jitter) @(negedge clk);
                while (wi < nw && wr[wi][63:40] == f) begin apply(wr[wi][39:32], wr[wi][31:0]); wi = wi + 1; end
            end
            timeout = 0;
            while (!body_valid && timeout < 2000) begin @(negedge clk); timeout = timeout + 1; end
            if (timeout >= 2000) begin
                $display("tb_drums: TIMEOUT -- no body_valid within 2000 clocks for frame %0d", f);
                $fclose(fd); $finish;
            end
            started = 0; total = total + cyc; if (cyc > worst) worst = cyc;
            if (^mix_out === 1'bx || ^body_out === 1'bx) $fdisplay(fd, "x x");
            else $fdisplay(fd, "%0d %0d", mix_out, body_out);
            nout = nout + 1;
            @(negedge clk);
        end
        $fclose(fd);
        $display("tb_drums: %0d frames, %0d writes applied of %0d, cycles/frame mean %0d worst %0d (tick to body_valid)",
                 nout, wi, nw, total / nout, worst);
        $finish;
    end
endmodule

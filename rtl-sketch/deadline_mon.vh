// deadline_mon.vh -- per-frame SCHEDULE monitor for synth_top, the production
// frame of docs/ARCHITECTURE.md section 5 (plan075 T1, docs/deadline/).
//
// Included by a one-line root module that defines `DL_DUT` as the synth_top
// instance's hierarchical path, so the SAME monitor observes the SPI bench
// (tb_top_bx.dut) and the Arty wrapper bench (tb_uart_bx.board.u_synth)
// without either bench being edited. It is compiled as an extra root; it
// drives nothing.
//
// Output file: the bench's own +wrs=<path> with ".sched" appended (both
// benches take +wrs=, and a separate plusarg could not be passed through
// their drivers). One line per COMPLETE frame, written at the frame's last
// cycle (255):
//
//   F frame go_cyc v_start v_last_busy strobe d_start d_last_busy
//     wr_n wr_first wr_last wr_in_compute busy_at_tick rwait oscwait dwait
//     win w1 im1 sk ywait
//
// Every cycle field is the frame-cycle index (dut.cyc, 0..255) of the cycle in
// which the condition was OBSERVED (sampled at the rising edge that ends that
// cycle). -1 = never in this frame. v_last_busy / d_last_busy: the last cycle
// of this frame in which voice_busy / drum_busy was high; busy_at_tick: 1 if
// either was high in cycle 0 of this frame (the overrun condition synth_top
// latches). wr_in_compute: register-port writes observed in a cycle where go
// or either datapath's busy was high -- a write landing mid-computation,
// which production's drain window (writes before go) is meant to forbid.
// rwait / oscwait / dwait: cycles voice_dp spent waiting on the reciprocal
// divider, the 2x oscillator bank, and the drum handshake (attribution only).
// win / w1 / im1 / sk / ywait: cycles in S_WIN (PolyBLEP windows examined),
// S_W1 (windows ACTIVE: each costs S_W1 + S_W2), S_IM1 (a modulated
// increment), S_SK0 (a shark-tooth), S_YWAIT (the ladder / drum_done wait).
//
// Deadlines, from the RTL rather than asserted here (verify_deadline.py
// computes them from these rows):
//   * sample: i2s_tx latches `held` into the next period at cycle 255, and
//     `held` takes the strobe at the edge ending the strobe cycle, so a strobe
//     in cycle 255 would miss its period. Sample slack = 254 - strobe.
//   * busy: synth_top sets `overrun` if either datapath is busy in cycle 0.
//     Busy slack = 255 - last_busy.
integer dl_fd = 0;
reg [8*512-1:0] dl_wrs;
reg [8*520-1:0] dl_path;
integer dl_frame = -1;
integer dl_go, dl_vs, dl_vl, dl_st, dl_ds, dl_dl, dl_wn, dl_wf, dl_wl, dl_wc, dl_bt, dl_rw, dl_ow, dl_dw;
integer dl_win, dl_w1, dl_im, dl_sk, dl_yw;
reg dl_vb_q = 0, dl_db_q = 0;
initial begin
    if ($value$plusargs("wrs=%s", dl_wrs)) begin
        $sformat(dl_path, "%0s.sched", dl_wrs);
        dl_fd = $fopen(dl_path, "w");
    end
end
task dl_clear; begin
    dl_go = -1; dl_vs = -1; dl_vl = -1; dl_st = -1; dl_ds = -1; dl_dl = -1;
    dl_wn = 0; dl_wf = -1; dl_wl = -1; dl_wc = 0; dl_bt = 0; dl_rw = 0; dl_ow = 0; dl_dw = 0;
    dl_win = 0; dl_w1 = 0; dl_im = 0; dl_sk = 0; dl_yw = 0;
end endtask
initial dl_clear;
always @(posedge `DL_DUT.clk) begin
    if (!`DL_DUT.rst_n) begin
        dl_frame = -1; dl_clear; dl_vb_q = 0; dl_db_q = 0;
    end else begin
        if (`DL_DUT.cyc == 8'd0) begin
            dl_frame = dl_frame + 1;
            dl_clear;
            if (`DL_DUT.voice_busy || `DL_DUT.drum_busy) dl_bt = 1;
        end
        if (`DL_DUT.go) dl_go = `DL_DUT.cyc;
        if (`DL_DUT.voice_busy) begin
            if (!dl_vb_q && dl_vs < 0) dl_vs = `DL_DUT.cyc;
            dl_vl = `DL_DUT.cyc;
        end
        if (`DL_DUT.drum_busy) begin
            if (!dl_db_q && dl_ds < 0) dl_ds = `DL_DUT.cyc;
            dl_dl = `DL_DUT.cyc;
        end
        dl_vb_q = `DL_DUT.voice_busy; dl_db_q = `DL_DUT.drum_busy;
        if (`DL_DUT.sample_valid === 1'b1) dl_st = `DL_DUT.cyc;
        if (`DL_DUT.wr_valid) begin
            dl_wn = dl_wn + 1;
            if (dl_wf < 0) dl_wf = `DL_DUT.cyc;
            dl_wl = `DL_DUT.cyc;
            if (`DL_DUT.go || `DL_DUT.voice_busy || `DL_DUT.drum_busy) dl_wc = dl_wc + 1;
        end
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_RWAIT)   dl_rw = dl_rw + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_OSCWAIT) dl_ow = dl_ow + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_DWAIT)   dl_dw = dl_dw + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_WIN)     dl_win = dl_win + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_W1)      dl_w1 = dl_w1 + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_IM1)     dl_im = dl_im + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_SK0)     dl_sk = dl_sk + 1;
        if (`DL_DUT.u_voice.state == `DL_DUT.u_voice.S_YWAIT)   dl_yw = dl_yw + 1;
        if (`DL_DUT.cyc == 8'd255 && dl_fd && dl_frame >= 0)
            $fdisplay(dl_fd, "F %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                      dl_frame, dl_go, dl_vs, dl_vl, dl_st, dl_ds, dl_dl,
                      dl_wn, dl_wf, dl_wl, dl_wc, dl_bt, dl_rw, dl_ow, dl_dw,
                      dl_win, dl_w1, dl_im, dl_sk, dl_yw);
    end
end

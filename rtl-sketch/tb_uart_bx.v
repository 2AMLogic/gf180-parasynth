// tb_uart_bx.v -- the UART-path bench: the Arty wrapper through its PINS, with
// commands arriving as serial bytes on the reserved UART RX pin (A9) and the
// bridge's responses captured from the UART TX pin (D10).
//
// Why a new bench rather than tb_top_bx with another script: the whole point
// of the bridge is that the COMMAND STREAM is not the SPI link. The bench is
// the host: it serialises packets onto uart_rxd at the real baud, it never
// touches the SPI pins (they are held idle all run), and every register write
// is observed at the write port together with the frame it landed in. The I2S
// receiver is pins-only, exactly tb_top_bx's, and the expectation comes from
// fpga/uart_host.py's device contract -- the chip is required to agree with
// the CONTRACT, not with anything it reported itself.
//
// Frames here are "total frames": a free-running counter from t=0 that no
// reset clears. The bench prints a SEG line whenever reset is released, giving
// the absolute frame the core restarted in and the I2S period count at that
// moment; every later comparison is anchored to those, never to an assumption.
//
//   +uart=<file>   commands, one per line:
//                    S <abs_frame> <hex byte> [<hex byte> ...]   send a packet
//                    R <abs_frame>                               pulse BTN0 reset
//   +txd=<file>    one line per captured TX byte: "frame byte"
//   +i2s=<file>    one line per completed LRCLK period (see tb_top_bx)
//   +wrs=<file>    "frame flag sec addr data src" per write at the port
//   +samp=<file>   one line per frame: "frame sample"   (diagnostic)
//   +frames=N      run N frames past the last command
`timescale 1ns/1ps
module tb_uart_bx;
    parameter BAUD = 115200;
    parameter EVQ_DEPTH = 64;
    parameter WRQ_DEPTH = 8;

    reg clk = 0;
    reg btn_reset = 1;
    wire rst_n = board.core_rst_n;
    reg uart_rxd = 1'b1;
    wire uart_txd;
    wire miso, bclk, lrclk, sdata;
    // the SPI pins are held idle: this run's control traffic is UART-only
    arty_a7_top #(.SIM_NO_MMCM(1), .POR_BITS(3), .UART_BAUD(BAUD),
                  .UART_EVQ_DEPTH(EVQ_DEPTH), .UART_WRQ_DEPTH(WRQ_DEPTH)) board (
        .clk_100mhz(clk), .btn_reset(btn_reset), .led(),
        .spi_sck(1'b0), .spi_mosi(1'b0), .spi_cs_n(1'b1), .spi_miso(miso),
        .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .i2s_bclk(bclk), .i2s_lrclk(lrclk), .i2s_sdata(sdata));
    always #40.69 clk = ~clk;                                     // 12.288 MHz

    // ---- total frames: free-running, reset-independent --------------------
    reg [31:0] tcc = 0;
    always @(posedge clk) tcc <= tcc + 32'd1;
    // the DUT's own frame index per segment (tb_top_bx's `fr`): incremented at
    // cyc 255, reset to 0 at each SEG anchor. Labels in the output files are
    // seg_base + dfr, i.e. the frame of the AUDIO timeline the model uses.
    integer dfr = 0;
    integer seg_base = 0;
    always @(posedge clk) if (rst_n && board.u_synth.cyc == 8'd255) dfr = dfr + 1;

    // ---- write-port monitor -----------------------------------------------
    integer wr_fd = 0, i2s_fd = 0, txd_fd = 0;
    integer writes_seen = 0, collisions = 0;
    always @(posedge clk) if (rst_n) begin
`ifdef UART_DBG
        if (board.u_synth.cyc == 8'd1 && (tcc >> 8) < 4)
            $display("CORE fr=%0d sample=%h sv=%b dmix=%h body=%h done=%b mixv=%b bodyv=%b wra=%h wrd=%h y19=%h mix=%h cut=%h k=%h",
                     tcc >> 8, board.u_synth.sample, board.u_synth.sample_valid,
                     board.u_synth.dmix_v, board.u_synth.body_v, board.u_synth.done_v,
                     board.u_synth.mix_valid, board.u_synth.body_valid,
                     board.u_synth.wr_addr, board.u_synth.wr_data,
                     board.u_synth.u_voice.y19, board.u_synth.u_voice.mixed,
                     board.u_synth.u_voice.cut, board.u_synth.u_voice.k_eff);
        if (board.u_synth.g_uart.u_uart.rx_done)
            $display("RXB t=%0t byte=%h pstate=%0d", $time,
                     board.u_synth.g_uart.u_uart.rx_byte,
                     board.u_synth.g_uart.u_uart.pstate);
        if (board.u_synth.g_uart.u_uart.wrq_push_d)
            $display("PUSH t=%0t cyc=%0d fr=%0d w=%h", $time,
                     board.u_synth.cyc, board.u_synth.frame,
                     board.u_synth.g_uart.u_uart.wrq_push_w);
        if (board.u_synth.g_uart.u_uart.wrq_head_fire)
            $display("FIRE t=%0t cyc=%0d fr=%0d dfr=%0d segb=%0d label=%0d stamp=%0d rp=%0d",
                     $time, board.u_synth.cyc, board.u_synth.frame, dfr, seg_base,
                     seg_base + dfr,
                     board.u_synth.g_uart.u_uart.wrq_head_stamp,
                     board.u_synth.g_uart.u_uart.wrq_rp);
        if (board.u_synth.g_uart.u_uart.evq_head_fire)
            $display("EFIRE t=%0t frame=%0d due=%0d cyc=%0d addr=%h", $time,
                     board.u_synth.g_uart.u_uart.frame,
                     board.u_synth.g_uart.u_uart.evq_head_due,
                     board.u_synth.cyc,
                     board.u_synth.u_wr_addr);
        if (board.u_synth.wr_valid)
            $display("PORT t=%0t fr=%0d v=%b f=%b s=%b a=%h d=%h", $time, tcc >> 8,
                     board.u_synth.wr_valid, board.u_synth.wr_flag,
                     board.u_synth.wr_sec, board.u_synth.wr_addr,
                     board.u_synth.wr_data);
`endif
    end
    always @(posedge clk) if (rst_n) begin
        if (board.u_synth.wr_valid) begin
            writes_seen = writes_seen + 1;
            if (wr_fd) $fdisplay(wr_fd, "%0d %0d %0d %0d %0d %0d", seg_base + dfr,
                                 board.u_synth.wr_flag, board.u_synth.wr_sec,
                                 board.u_synth.wr_addr, board.u_synth.wr_data,
                                 board.u_synth.spi_wr_valid ? 0 : 1);
        end
`ifdef UART_HIER
        if (board.u_synth.spi_wr_valid === 1'b1 &&
            board.u_synth.g_uart.u_uart.wr_valid === 1'b1)
            collisions = collisions + 1;
`endif
    end

    // ---- I2S receiver: pins only, exactly tb_top_bx's ---------------------
    reg [15:0] cap = 0;
    reg signed [15:0] lword = 0, rword = 0;
    integer nbit = 0, nbl = 0, nbr = 0, nper = 0;
    reg last_lr = 1'b0;
    always @(posedge bclk) if (rst_n) begin
        if (lrclk !== last_lr) begin
            if (last_lr == 1'b0) begin lword = $signed(cap); nbl = nbit; end
            else begin
                rword = $signed(cap); nbr = nbit;
                if (i2s_fd) $fdisplay(i2s_fd, "%0d %0d %0d %0d %0d", nper, lword, rword, nbl, nbr);
                nper = nper + 1;
            end
            nbit = 0; cap = 0; last_lr = lrclk;
        end
        if (nbit >= 1 && nbit <= 16) cap = {cap[14:0], sdata};
        nbit = nbit + 1;
    end
    // a reset freezes the wire with LRCLK low; realign the slot tracker
    always @(negedge rst_n) begin last_lr = 1'b0; nbit = 0; cap = 0; end

    // ---- per-segment frame-budget counters --------------------------------
    integer n_strobe = 0, n_nostrobe = 0, worst_cyc = 0, busy_at_tick = 0;
    integer samp_fd = 0;
    reg got = 0;
    reg seg_ready = 0;                   // counting starts at the SEG anchor
    reg signed [15:0] samp_latch = 0;           // the last strobed sample, tb_top_bx-style
    always @(posedge clk) if (rst_n && seg_ready) begin
        if (board.u_synth.sample_valid) begin
            got <= 1'b1; n_strobe = n_strobe + 1;
            samp_latch <= board.u_synth.sample;
            if (board.u_synth.cyc > worst_cyc) worst_cyc = board.u_synth.cyc;
        end
        if (board.u_synth.cyc == 8'd0) begin
            if (board.u_synth.voice_busy || board.u_synth.drum_busy)
                busy_at_tick = busy_at_tick + 1;
            if (!got) begin
                n_nostrobe = n_nostrobe + 1;
`ifdef UART_DBG
                $display("NOSTROBE fr=%0d tcc=%0d", tcc >> 8, tcc);
`endif
            end
            if (samp_fd) $fdisplay(samp_fd, "%0d %0d", seg_base + dfr - 1, samp_latch);
            got <= 1'b0;
        end
    end

    // ---- the host side: serialise bytes at the real baud -------------------
    real bit_ns;
    integer i;
    task send_byte(input [7:0] b);
        begin
            uart_rxd = 1'b0; #(bit_ns);                 // start bit
            for (i = 0; i < 8; i = i + 1) begin
                uart_rxd = b[i]; #(bit_ns);             // LSB first
            end
            uart_rxd = 1'b1; #(bit_ns);                 // stop bit
        end
    endtask

    // ---- TX capture: a receiver that samples at the bit centres -----------
    localparam TXDIV = (12288000 / BAUD);
    integer tx_bytes = 0;
    reg [7:0] txdiv = 0;
    reg [3:0] txbit = 0;
    reg [7:0] txsh = 0;
    reg tx_busy = 0;
    always @(posedge clk) if (rst_n) begin
        if (!tx_busy) begin
            if (uart_txd === 1'b0) begin tx_busy <= 1'b1; txdiv <= 0; txbit <= 0; end
        end else begin
            if (txdiv == TXDIV - 1) txdiv <= 0;
            else txdiv <= txdiv + 8'd1;
            if (txdiv == ((TXDIV >> 1) - 1)) begin
`ifdef UART_DBG
                $display("TXCAP t=%0t busy=%b bit=%0d lvl=%b", $time, tx_busy, txbit, uart_txd);
`endif
                if (txbit == 4'd0) begin
                    if (uart_txd !== 1'b0) tx_busy <= 1'b0;      // glitch, not a start bit
                    txbit <= 4'd1;
                end else if (txbit <= 4'd8) begin
                    txsh <= {uart_txd, txsh[7:1]};
                    txbit <= txbit + 4'd1;
                end else begin
                    tx_bytes = tx_bytes + 1;
                    // bit9's sample is the stop bit: the byte itself is txsh
                    if (txd_fd) $fdisplay(txd_fd, "%0d %0d", tcc >> 8, txsh);
                    tx_busy <= 1'b0;
                end
            end
        end
    end

    // ---- reset segment bookkeeping ----------------------------------------
    integer seg_idx = -1;
    task do_reset;
        begin
            btn_reset = 1'b1;
            wait (rst_n === 1'b0);
            repeat (40) @(posedge clk);
            @(negedge clk) btn_reset = 1'b0;
            wait (rst_n === 1'b1);
            @(posedge clk);
            while (board.u_synth.cyc !== 8'd1) @(posedge clk);
            seg_idx = seg_idx + 1;
            $display("tb_uart_bx: SEG %0d origin_tcc %0d periods %0d strobes %0d",
                     seg_idx, tcc, nper, n_strobe);
            n_strobe = 0; n_nostrobe = 0; worst_cyc = 0; busy_at_tick = 0;
            seg_base = tcc >> 8; dfr = 0;
            seg_ready = 1'b1;
        end
    endtask

    // ---- the script --------------------------------------------------------
    integer cmd_fd, rc, run_frames = 200, k;
    integer abs_frame, nbytes, line_no = 0, tail_target;
    reg [8*512-1:0] cmd_file, i2s_file, wr_file, txd_file, samp_file;
    reg [1023:0] line;
    reg [7:0] bytev [0:15];
    reg [7:0] kindc;
    initial begin
        bit_ns = 1e9 / BAUD;
        if (!$value$plusargs("uart=%s", cmd_file)) begin
            $display("tb_uart_bx: +uart=<file> is required"); $finish;
        end
        if ($value$plusargs("i2s=%s", i2s_file)) i2s_fd = $fopen(i2s_file, "w");
        if ($value$plusargs("wrs=%s", wr_file))  wr_fd  = $fopen(wr_file, "w");
        if ($value$plusargs("txd=%s", txd_file)) txd_fd = $fopen(txd_file, "w");
        if ($value$plusargs("samp=%s", samp_file)) samp_fd = $fopen(samp_file, "w");
        if ($value$plusargs("frames=%d", run_frames)) ;
        repeat (8) @(posedge clk);
        @(negedge clk) btn_reset = 1'b0;
        wait (rst_n === 1'b1);
        @(posedge clk);
        while (board.u_synth.cyc !== 8'd1) @(posedge clk);
        seg_idx = seg_idx + 1;
        $display("tb_uart_bx: SEG %0d origin_tcc %0d periods %0d strobes %0d",
                 seg_idx, tcc, nper, n_strobe);
        n_strobe = 0; n_nostrobe = 0; worst_cyc = 0; busy_at_tick = 0;
        seg_base = tcc >> 8; dfr = 0;
        seg_ready = 1'b1;
        cmd_fd = $fopen(cmd_file, "r");
        if (cmd_fd == 0) begin $display("tb_uart_bx: cannot open %0s", cmd_file); $finish; end
        while (!$feof(cmd_fd)) begin
            rc = $fgets(line, cmd_fd);
            if (rc == 0) continue;
            line_no = line_no + 1;
            rc = $sscanf(line, " %c %d %h %h %h %h %h %h %h %h %h %h %h %h",
                         kindc, abs_frame,
                         bytev[0], bytev[1], bytev[2], bytev[3], bytev[4],
                         bytev[5], bytev[6], bytev[7], bytev[8], bytev[9],
                         bytev[10], bytev[11]);
            if (rc < 2) continue;
            if (kindc == "R") begin
                wait (tcc >= abs_frame * 256);
                @(negedge clk);
                do_reset;
            end else if (kindc == "S") begin
                nbytes = rc - 2;
                wait (tcc >= abs_frame * 256);
                @(negedge clk);
                for (k = 0; k < nbytes; k = k + 1) send_byte(bytev[k]);
            end else begin
                $display("tb_uart_bx: bad command line %0d: %c", line_no, kindc);
                $finish;
            end
        end
        $fclose(cmd_fd);
        tail_target = ((tcc >> 8) + run_frames) * 256;
        wait (tcc >= tail_target);
        $display("tb_uart_bx: ran %0d frames in %0d segments; %0d I2S periods decoded",
                 tcc >> 8, seg_idx + 1, nper);
        $display("tb_uart_bx: writes drained %0d, spi+uart slot collisions %0d (must be 0)",
                 writes_seen, collisions);
        $display("tb_uart_bx: sample strobed in %0d frames, MISSING in %0d, worst strobe cycle %0d of 256",
                 n_strobe, n_nostrobe, worst_cyc);
        $display("tb_uart_bx: datapath busy at a tick: %0d; core overrun %0d; link overflow %0d",
                 busy_at_tick, board.u_synth.overrun, board.u_synth.u_spi.overflow);
        $display("tb_uart_bx: TX bytes captured %0d", tx_bytes);
        if (i2s_fd) $fclose(i2s_fd);
        if (wr_fd) $fclose(wr_fd);
        if (txd_fd) $fclose(txd_fd);
        if (samp_fd) $fclose(samp_fd);
        $finish;
    end
endmodule

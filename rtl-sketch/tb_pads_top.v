// tb_pads_top.v -- the pads bench (#449): the pads wrapper through its PINS.
// The bench is the player: it presses the buttons (with contact bounce when
// asked), throws the two switches, and optionally plays a host on the FTDI RX
// pin. It decodes the I2S wire the way a DAC does, logs every write at the
// core's register port with the frame it landed in, and captures the bridge's
// TX bytes (ACK / ERR) -- the device's own report of whether every event was
// accepted before its due. The expectation is fpga/pads_rom.py's contract and
// model/synth_top_model.py, never anything the design reports about itself.
//
// TIME. `g` is the core-cycle index within the current reset SEGMENT: g = 0 is
// the cycle in which synth_top's cyc = 1 and frame = 1 (audio frame 0), so the
// audio frame of cycle g is g / 256 (pads_rom.cycle_frame). A segment begins at
// every reset release; the bench prints SEG with the I2S period count there.
//
//   +cmd=<file>   one command per line, times are g of the CURRENT segment:
//                   B <g> <mask>        buttons take <mask> (bit i = BTNi) in cycle g
//                   M <g> <0|1>         the source switch SW0 (1 = pads)
//                   S <g> <hex> ...     host bytes on uart_rxd from cycle g
//                   R <g>               flip SW3 (reset); later lines are relative
//                                       to the NEXT segment
//                   E <g>               end of run
//   +wrs=<file>   "seg frame flag sec addr data src" per write at the port
//   +i2s=<file>   "period left right nbits_l nbits_r" per LRCLK period
//   +txd=<file>   "seg frame byte" per byte the bridge sends
`timescale 1ns/1ps
module tb_pads_top;
    parameter BAUD = 115200;

    reg clk = 0;
    reg [3:0] btn = 4'd0;
    reg sw_pads = 1'b0, sw_reset = 1'b0;
    reg uart_rxd = 1'b1;
    wire uart_txd, miso, bclk, lrclk, sdata;
    wire [3:0] led;
    arty_a7_pads_top #(.SIM_NO_MMCM(1), .POR_BITS(3), .RST_HOLD_BITS(4),
                       .UART_BAUD(BAUD)) board (
        .clk_100mhz(clk), .btn(btn), .sw_pads(sw_pads), .sw_reset(sw_reset), .led(led),
        .spi_sck(1'b0), .spi_mosi(1'b0), .spi_cs_n(1'b1), .spi_miso(miso),
        .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .i2s_bclk(bclk), .i2s_lrclk(lrclk), .i2s_sdata(sdata));
    always #40.69 clk = ~clk;                                     // 12.288 MHz

    wire rst_n = board.u_synth.rst_n;

    // ---- the segment clock -----------------------------------------------
    integer g = 0, seg = -1, nper = 0;
    always @(posedge clk) begin
        if (rst_n === 1'b1 && board.u_synth.cyc == 8'd1 && board.u_synth.frame == 16'd1) begin
            g <= 1;
            seg <= seg + 1;
            $display("tb_pads_top: SEG %0d periods %0d", seg + 1, nper);
        end else g <= g + 1;
    end

    // ---- write-port monitor -----------------------------------------------
    integer wr_fd = 0, i2s_fd = 0, txd_fd = 0;
    integer writes_seen = 0, collisions = 0;
    always @(posedge clk) if (rst_n === 1'b1 && seg >= 0) begin
        if (board.u_synth.wr_valid) begin
            writes_seen = writes_seen + 1;
            if (wr_fd) $fdisplay(wr_fd, "%0d %0d %0d %0d %0d %0d %0d", seg, g >> 8,
                                 board.u_synth.wr_flag, board.u_synth.wr_sec,
                                 board.u_synth.wr_addr, board.u_synth.wr_data,
                                 board.u_synth.spi_wr_valid ? 0 : 1);
        end
        if (board.u_synth.spi_wr_valid === 1'b1 && board.u_synth.u_wr_valid === 1'b1)
            collisions = collisions + 1;
    end

`ifdef PADS_HIER
    // ---- diagnostics from inside the sequencer (never used as expectation) ---
    reg last_ready = 1'b0;
    integer k;
    always @(posedge clk) if (rst_n === 1'b1 && seg >= 0) begin
        for (k = 0; k < 4; k = k + 1)
            if (board.u_pads.press[k])
                $display("tb_pads_top: PRESS seg %0d btn %0d g %0d frame %0d", seg, k, g, g >> 8);
        if (board.u_pads.ready !== last_ready)
            $display("tb_pads_top: READY seg %0d level %0d g %0d", seg, board.u_pads.ready, g);
        last_ready = board.u_pads.ready;
    end
`endif

    // ---- I2S receiver: pins only, exactly tb_top_bx's ---------------------
    reg [15:0] cap = 0;
    reg signed [15:0] lword = 0, rword = 0;
    integer nbit = 0, nbl = 0, nbr = 0;
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
    always @(negedge rst_n) begin last_lr = 1'b0; nbit = 0; cap = 0; end

    // ---- the frame budget --------------------------------------------------
    integer n_strobe = 0, n_nostrobe = 0, worst_cyc = 0, busy_at_tick = 0;
    reg got = 0;
    // A reset cuts the frame in flight short, so that frame is not a missed
    // sample. `got` is cleared while reset is held, and a frame is judged only
    // at a cyc-0 tick that closes a frame the core ran from its start since
    // the release (frame >= 1: audio frame 0 of every segment is still judged).
    always @(posedge clk) if (rst_n !== 1'b1) got <= 1'b0;
    else if (seg >= 0) begin
        if (board.u_synth.sample_valid) begin
            got <= 1'b1; n_strobe = n_strobe + 1;
            if (board.u_synth.cyc > worst_cyc) worst_cyc = board.u_synth.cyc;
        end
        if (board.u_synth.cyc == 8'd0 && board.u_synth.frame >= 16'd1) begin
            if (board.u_synth.voice_busy || board.u_synth.drum_busy)
                busy_at_tick = busy_at_tick + 1;
            if (!got) n_nostrobe = n_nostrobe + 1;
            got <= 1'b0;
        end
    end

    // ---- TX capture: a receiver sampling at the bit centres --------------
    localparam TXDIV = (12288000 + BAUD / 2) / BAUD;
    integer tx_bytes = 0;
    reg [7:0] txdiv = 0;
    reg [3:0] txbit = 0;
    reg [7:0] txsh = 0;
    reg tx_busy = 0;
    always @(posedge clk) if (rst_n === 1'b1) begin
        if (!tx_busy) begin
            if (uart_txd === 1'b0) begin tx_busy <= 1'b1; txdiv <= 0; txbit <= 0; end
        end else begin
            if (txdiv == TXDIV - 1) txdiv <= 0;
            else txdiv <= txdiv + 8'd1;
            if (txdiv == ((TXDIV >> 1) - 1)) begin
                if (txbit == 4'd0) begin
                    if (uart_txd !== 1'b0) tx_busy <= 1'b0;
                    txbit <= 4'd1;
                end else if (txbit <= 4'd8) begin
                    txsh <= {uart_txd, txsh[7:1]};
                    txbit <= txbit + 4'd1;
                end else begin
                    tx_bytes = tx_bytes + 1;
                    if (txd_fd) $fdisplay(txd_fd, "%0d %0d %0d", seg, g >> 8, txsh);
                    tx_busy <= 1'b0;
                end
            end
        end
    end

    // ---- the host side: serialise bytes at the real baud -------------------
    real bit_ns;
    integer i;
    task send_byte(input [7:0] b);
        begin
            uart_rxd = 1'b0; #(bit_ns);
            for (i = 0; i < 8; i = i + 1) begin uart_rxd = b[i]; #(bit_ns); end
            uart_rxd = 1'b1; #(bit_ns);
        end
    endtask

    // ---- the script --------------------------------------------------------
    integer cmd_fd, rc, t, v, nb, line_no = 0, kk, seg_before;
    reg [8*512-1:0] cmd_file, wr_file, i2s_file, txd_file;
    reg [1023:0] line;
    reg [7:0] bytev [0:15];
    reg [7:0] kindc;
    initial begin
        bit_ns = 1e9 / BAUD;
        if (!$value$plusargs("cmd=%s", cmd_file)) begin
            $display("tb_pads_top: +cmd=<file> is required"); $finish;
        end
        if ($value$plusargs("wrs=%s", wr_file))  wr_fd  = $fopen(wr_file, "w");
        if ($value$plusargs("i2s=%s", i2s_file)) i2s_fd = $fopen(i2s_file, "w");
        if ($value$plusargs("txd=%s", txd_file)) txd_fd = $fopen(txd_file, "w");
        cmd_fd = $fopen(cmd_file, "r");
        if (cmd_fd == 0) begin $display("tb_pads_top: cannot open %0s", cmd_file); $finish; end
        wait (seg >= 0);
        while (!$feof(cmd_fd)) begin
            rc = $fgets(line, cmd_fd);
            if (rc == 0) continue;
            line_no = line_no + 1;
            rc = $sscanf(line, " %c %d %h %h %h %h %h %h %h %h %h %h %h %h", kindc, t,
                         bytev[0], bytev[1], bytev[2], bytev[3], bytev[4], bytev[5],
                         bytev[6], bytev[7], bytev[8], bytev[9], bytev[10], bytev[11]);
            if (rc < 2) continue;
            if (g > t) begin
                $display("tb_pads_top: SCRIPT LATE at line %0d: g %0d > %0d", line_no, g, t);
                $finish;
            end
            wait (g == t);
            @(negedge clk);
            if (kindc == "B") btn = bytev[0][3:0];
            else if (kindc == "M") sw_pads = bytev[0][0];
            else if (kindc == "S") begin
                nb = rc - 2;
                for (kk = 0; kk < nb; kk = kk + 1) send_byte(bytev[kk]);
            end else if (kindc == "R") begin
                seg_before = seg;
                sw_reset = ~sw_reset;
                wait (seg == seg_before + 1);
            end else if (kindc == "E") begin
                $display("tb_pads_top: ran %0d segments; %0d I2S periods decoded", seg + 1, nper);
                $display("tb_pads_top: writes drained %0d, spi+uart slot collisions %0d (must be 0)",
                         writes_seen, collisions);
                $display("tb_pads_top: sample strobed in %0d frames, MISSING in %0d, worst strobe cycle %0d of 256",
                         n_strobe, n_nostrobe, worst_cyc);
                $display("tb_pads_top: datapath busy at a tick: %0d; core overrun %0d",
                         busy_at_tick, board.u_synth.overrun);
                $display("tb_pads_top: TX bytes captured %0d", tx_bytes);
                if (wr_fd) $fclose(wr_fd);
                if (i2s_fd) $fclose(i2s_fd);
                if (txd_fd) $fclose(txd_fd);
                $finish;
            end else begin
                $display("tb_pads_top: bad command line %0d: %c", line_no, kindc);
                $finish;
            end
        end
        $display("tb_pads_top: script ended without E");
        $finish;
    end
endmodule

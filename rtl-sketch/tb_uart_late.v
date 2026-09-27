// tb_uart_late.v -- the REAL uart_bridge.v under late events, cycle-exact (#329).
//
// The bridge alone, with the frame counter and the write grant produced
// EXACTLY as synth_top.v produces them (cyc wraps every 256 cycles; frame
// increments on the cycle cyc == 0; grant in cycles [GO_CYCLE-2, GO_CYCLE)).
// fpga/verify_late_events.py asserts that those synth_top.v lines are still
// what this bench copies, and REFUSES if they drift.
//
// One deliberate difference from synth_top: the frame counter starts at
// +frame0 instead of 0, so the counter wrap is reached without simulating
// 65536 frames. The bridge sees only the counter value, so this reaches the
// same states.
//
//   +cmds=<file>    lines "S <cycle> <hex byte> ..." : start the packet's
//                   start bit at that absolute cycle (after reset release);
//                   packets must not overlap
//   +log=<file>     ACC <cycle> <frame> <cyc>              event checksum byte accepted
//                   W   <cycle> <frame> <flag> <sec> <addr> <data>   a write at the port
//                   T   <cycle> <byte>                     a byte decoded from TX
//   +frame0=N       initial frame counter value
//   +cycles=N       cycles to run after reset release
`timescale 1ns/1ps
`default_nettype none
module tb_uart_late;
    parameter BAUD = 115200;
    parameter GO_CYCLE = 8;
    localparam integer DIV = (12288000 + BAUD / 2) / BAUD;   // the bridge's own divisor

    reg clk = 0;
    always #40.69 clk = ~clk;
    reg rst_n = 0;
    reg rx = 1'b1;
    wire tx;

    // ---- synth_top.v's frame, tick and UART grant, verbatim in behaviour ----
    reg [7:0]  cyc;
    reg [15:0] frame;
    wire tick = (cyc == 8'd0);
    reg [15:0] frame0;
    always @(posedge clk) begin
        if (!rst_n) begin cyc <= 8'd0; frame <= frame0; end
        else begin
            cyc <= cyc + 8'd1;
            if (tick) frame <= frame + 16'd1;
        end
    end
    localparam integer UART_WIN_LO = GO_CYCLE - 2;
    wire uart_grant = (cyc >= UART_WIN_LO) && (cyc < GO_CYCLE);

    wire wr_valid, wr_flag, wr_sec;
    wire [7:0] wr_addr;
    wire [31:0] wr_data;
    uart_bridge #(.CLK_HZ(12_288_000), .BAUD(BAUD), .EVQ_DEPTH(64), .WRQ_DEPTH(8)) u (
        .clk(clk), .rst_n(rst_n), .rx(rx), .tx(tx), .frame(frame), .grant(uart_grant),
        .wr_valid(wr_valid), .wr_flag(wr_flag), .wr_sec(wr_sec), .wr_addr(wr_addr),
        .wr_data(wr_data), .evq_count(), .wrq_count(), .evq_overflow(),
        .wrq_overflow(), .late_seen(), .resync_seen());

    integer cycle = 0;
    always @(posedge clk) if (rst_n) cycle <= cycle + 1;

    integer log_fd;
    // the event parser's checksum byte (pstate P_ECK = 16) arriving: the
    // acceptance instant the policy's frame A is read at
    always @(posedge clk) if (rst_n && u.rx_done && u.pstate == 5'd16)
        $fdisplay(log_fd, "ACC %0d %0d %0d", cycle, frame, cyc);
    always @(posedge clk) if (rst_n && wr_valid)
        $fdisplay(log_fd, "W %0d %0d %0d %0d %0d %0d", cycle, frame, wr_flag, wr_sec,
                  wr_addr, wr_data);

    // ---- TX: a receiver sampling at bit centres -------------------------------
    reg [7:0] txdiv = 0;
    reg [3:0] txbit = 0;
    reg [7:0] txsh = 0;
    reg tx_busy = 0;
    always @(posedge clk) if (rst_n) begin
        if (!tx_busy) begin
            if (tx === 1'b0) begin tx_busy <= 1'b1; txdiv <= 0; txbit <= 0; end
        end else begin
            if (txdiv == DIV - 1) txdiv <= 0; else txdiv <= txdiv + 8'd1;
            if (txdiv == ((DIV >> 1) - 1)) begin
                if (txbit == 4'd0) begin
                    if (tx !== 1'b0) tx_busy <= 1'b0;
                    txbit <= 4'd1;
                end else if (txbit <= 4'd8) begin
                    txsh <= {tx, txsh[7:1]};
                    txbit <= txbit + 4'd1;
                end else begin
                    $fdisplay(log_fd, "T %0d %0d", cycle, txsh);
                    tx_busy <= 1'b0;
                end
            end
        end
    end

    // ---- the host: packets serialised at the bridge's divisor ------------------
    task send_byte(input [7:0] b);
        integer i;
        begin
            rx = 1'b0; repeat (DIV) @(posedge clk);
            for (i = 0; i < 8; i = i + 1) begin rx = b[i]; repeat (DIV) @(posedge clk); end
            rx = 1'b1; repeat (DIV) @(posedge clk);
        end
    endtask

    reg [8*1024-1:0] cmd_file, log_file;
    reg [8*4096-1:0] line;
    integer fd, rc, at, n, total, b0, b1, b2, b3, b4, b5, b6, b7, b8, b9, b10, b11;
    integer bytes [0:11];
    integer k;
    initial begin
        if (!$value$plusargs("cmds=%s", cmd_file)) begin $display("tb_uart_late: +cmds"); $finish; end
        if (!$value$plusargs("log=%s", log_file)) begin $display("tb_uart_late: +log"); $finish; end
        if (!$value$plusargs("frame0=%d", frame0)) frame0 = 16'd0;
        if (!$value$plusargs("cycles=%d", total)) total = 100000;
        log_fd = $fopen(log_file, "w");
        fd = $fopen(cmd_file, "r");
        if (fd == 0) begin $display("tb_uart_late: cannot open %0s", cmd_file); $finish; end
        repeat (4) @(posedge clk);
        rst_n = 1;
        while (!$feof(fd)) begin
            rc = $fgets(line, fd);
            if (rc > 0) begin
                n = $sscanf(line, "S %d %h %h %h %h %h %h %h %h %h %h %h %h", at,
                            b0, b1, b2, b3, b4, b5, b6, b7, b8, b9, b10, b11);
                if (n >= 2) begin
                    if (cycle > at) begin
                        $display("tb_uart_late: OVERLAP packet at %0d starts late (%0d)", at, cycle);
                        $fdisplay(log_fd, "OVERLAP %0d %0d", at, cycle);
                    end
                    while (cycle < at) @(posedge clk);
                    bytes[0]=b0; bytes[1]=b1; bytes[2]=b2; bytes[3]=b3; bytes[4]=b4;
                    bytes[5]=b5; bytes[6]=b6; bytes[7]=b7; bytes[8]=b8; bytes[9]=b9;
                    bytes[10]=b10; bytes[11]=b11;
                    for (k = 0; k < n - 1; k = k + 1) send_byte(bytes[k]);
                end
            end
        end
        $fclose(fd);
        while (cycle < total) @(posedge clk);
        $fdisplay(log_fd, "END %0d %0d", cycle, frame);
        $fclose(log_fd);
        $display("tb_uart_late: done at cycle %0d frame %0d", cycle, frame);
        $finish;
    end
endmodule
`default_nettype wire

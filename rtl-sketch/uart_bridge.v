// uart_bridge.v -- the USB-UART control bridge: the Arty FTDI link as a
// second front end for the SAME register-write port the SPI slave drains.
//
// WHY. The musician's link is the USB connector, not a SPI header. Host-side
// sleeps cannot hold an audio-frame deadline over USB, so timed events are
// uploaded stamped with the FRAME each write applies in and are fired by THIS
// module from the device's own event queue. The SPI path keeps priority by
// construction: this bridge may only present writes in the two window cycles
// after the SPI drain can possibly finish (see synth_top.v), so the two
// sources can never collide on the port, whatever either link does.
//
// THE WIRE. 8N1, LSB-first bytes, `BAUD` (default 115200; any baud with
// CLK_HZ/BAUD >= 16). Every packet is  opcode payload... checksum  with
// checksum = (-sum bytes) mod 256, so a corrupted or shortened packet is
// rejected whole and reported -- never half-applied. Write payloads are the
// SPI framing's six bytes, MSB first: {F, 6'b0, SEC, A[7:0], D[31:0]}.
//
// PACKETS (host -> device)
//   W 0x57 <6 reg bytes> ck                 write now: live path
//   E 0x45 <due lsb> <due msb> <6> ck       schedule: fires in its due frame
//   Q 0x51 ck                               status reply on TX
//   X 0x58 ck                               abort: both queues cleared
// (device -> host)
//   0xA5                                    BOOT: emitted once after any reset
//   06 <seq>                                ACK, accepted into a queue
//   1C <code> <seq> <info>                  ERR: 1 write-queue overflow,
//                                           2 event-queue overflow,
//                                           3 due in the past / out of order
//                                             (a late event still executes),
//                                           4 resync (framing or byte gap),
//                                           5 bad checksum, 6 bad opcode.
//                                           `info` names the offending write's
//                                           register address.
//   55 <frame16> <evq> <wrq> <drops> <errs> <flags>   STATUS reply
//
// THE CONTRACT (fpga/uart_host.py is the same contract as code):
//   * accepted commands are ordered: each queue is FIFO and never reordered;
//   * per frame this module presents at most two writes (the two window
//     cycles), every due-scheduled event first, then live writes;
//   * an event accepted with due >= frame+1 fires in EXACTLY its due frame;
//   * queue overflow drops the arriving packet, counts it, and reports it.
//     Overflow cannot come from link bandwidth alone (one event packet per
//     ~42 frames at 115200 against a 64-deep queue); it means the host ran
//     further ahead than the contract allows, and it says so;
//   * a live write applies at accept_frame+1 and is never blocked by the
//     event queue -- a note-off waits for nothing;
//   * timestamps are 16-bit frames, wrap-safe within +-32768; an event whose
//     due is out of order with the queue's last due is dropped and reported;
//   * reset clears both queues, the parsers and the counters. A queued phrase
//     dies with the reset, by construction; the host sees the BOOT byte.
//
// NEGATIVE CONTROLS (each demonstrated to turn fpga/verify_uart_bridge.py red
// for the reason recorded there):
//   INJECT_BUG_UART_NO_CHECKSUM     the checksum is not enforced: a corrupted
//                                   or shortened packet's write executes
//   INJECT_BUG_UART_EVQ_OVF_SILENT  a full event queue silently overwrites
//   INJECT_BUG_UART_NOFF_BLOCKED    the live path is starved by event traffic
//   INJECT_BUG_UART_RESET_LEAK      the queues survive a reset
`default_nettype none
module uart_bridge #(
    parameter CLK_HZ     = 12_288_000,
    parameter BAUD       = 115_200,
    parameter EVQ_DEPTH  = 64,          // scheduled events (power of two)
    parameter EVQ_AW     = 6,           // log2(EVQ_DEPTH)
    parameter WRQ_DEPTH  = 8,           // live writes
    parameter TXF_DEPTH  = 16           // response bytes in flight
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        rx,               // A9: host TX -> FPGA RX
    output reg         tx,               // D10: FPGA TX -> host RX, idle high
    input  wire [15:0] frame,            // the audio frame counter
    input  wire        grant,            // this cycle may carry one write
    output reg         wr_valid,
    output reg         wr_flag,
    output reg         wr_sec,
    output reg  [7:0]  wr_addr,
    output reg  [31:0] wr_data,
    output reg  [7:0]  evq_count,
    output reg  [7:0]  wrq_count,
    output reg         evq_overflow,     // sticky until the next STATUS reply
    output reg         wrq_overflow,
    output reg         late_seen,
    output reg         resync_seen
);
    localparam DIV  = (CLK_HZ + BAUD / 2) / BAUD;    // core cycles per bit
    localparam HALF = DIV >> 1;
    localparam DIVW = $clog2(DIV);
    localparam [7:0] EVQ_FULL = EVQ_DEPTH;
    localparam [7:0] WRQ_FULL = WRQ_DEPTH;
    localparam [15:0] GAP_CYCLES = 40 * DIV;         // 4 byte times idle mid-packet

    wire [7:0] flags = {4'b0, resync_seen, late_seen, wrq_overflow, evq_overflow};

    // ---- RX: deserialiser --------------------------------------------------
    (* ASYNC_REG = "TRUE" *) reg [1:0] rx_q;
    reg       rx_p;
    wire      rx_s    = rx_q[1];
    wire      rx_fall = !rx_s && rx_p;
    reg [DIVW-1:0] rxdiv;
    reg [3:0] rbit;                      // 0 start, 1..8 data, 9 stop
    reg [7:0] rxsh;
    reg       rx_busy;
    reg [7:0] rx_byte;
    reg       rx_done;                   // one-cycle pulse: byte accepted
    reg       rx_framing;                // one-cycle pulse: stop bit was not 1

    always @(posedge clk) begin
        if (!rst_n) begin
            rx_q <= 2'b00; rx_p <= 1'b0; rxdiv <= 0; rbit <= 4'd0;
            rxsh <= 8'd0; rx_busy <= 1'b0; rx_byte <= 8'd0;
            rx_done <= 1'b0; rx_framing <= 1'b0;
        end else begin
            rx_q  <= {rx_q[0], rx};
            rx_p  <= rx_s;
            rx_done <= 1'b0;
            rx_framing <= 1'b0;
            if (!rx_busy) begin
                if (rx_fall) begin
                    rx_busy <= 1'b1;
                    rxdiv   <= 0;
                    rbit    <= 4'd0;
                end
            end else if (rxdiv == DIV - 1) begin
                rxdiv <= 0;
                rbit  <= rbit + 4'd1;
            end else begin
                rxdiv <= rxdiv + 1'b1;
                if (rxdiv == HALF - 1) begin
                    if (rbit == 4'd0) begin
                        // start bit confirmed; rbit stays 0 -- the wrap at the
                        // end of the start bit makes it 1 for data bit 0
                        if (!rx_s) begin end
                        else       rx_busy <= 1'b0;      // glitch
                    end else if (rbit <= 4'd8) begin
                        rxsh <= {rx_s, rxsh[7:1]};       // LSB first
                    end else begin
                        rx_busy <= 1'b0;
                        if (rx_s) begin
                            rx_byte <= rxsh;
                            rx_done <= 1'b1;
                        end else begin
                            rx_framing <= 1'b1;
                        end
                    end
                end
            end
        end
    end

    // ---- the packet parser -------------------------------------------------
    localparam [4:0] P_OP=5'd0,
                     P_W1=5'd1, P_W2=5'd2, P_W3=5'd3, P_W4=5'd4, P_W5=5'd5,
                     P_W6=5'd6, P_WCK=5'd7,
                     P_F0=5'd8, P_F1=5'd9,
                     P_E1=5'd10, P_E2=5'd11, P_E3=5'd12, P_E4=5'd13, P_E5=5'd14,
                     P_E6=5'd15, P_ECK=5'd16;
    reg [4:0]  pstate;
    reg [47:0] pay;
    reg [15:0] due;
    reg [7:0]  cksum;
    reg [15:0] gap;                      // cycles since the last byte, mid-packet
    wire       gap_fire = (pstate != P_OP) && (gap == GAP_CYCLES - 1);

    // ---- the queues --------------------------------------------------------
    reg [63:0] evq [0:EVQ_DEPTH-1];      // {due[15:0], write[47:0]}
    reg [EVQ_AW:0] evq_wp, evq_rp;
    reg [15:0] last_due;
    reg [63:0] wrq [0:WRQ_DEPTH-1];      // {stamp[15:0], write[47:0]}
    reg [2:0]  wrq_wp, wrq_rp;           // WRQ_DEPTH is 8
    reg [7:0]  seq, drops, errs;

`ifdef INJECT_BUG_UART_CORRUPT_ADDR
    wire [47:0] pay_out = {pay[47:40], pay[39:32] ^ 8'h01, pay[31:0]}; // NEGATIVE
`else                                                                    // CONTROL
    wire [47:0] pay_out = pay;
`endif
    wire [15:0] ddiff    = due - frame;               // wrap-safe
    wire        due_past = (ddiff == 16'd0) || (ddiff > 16'h7FFF);

    // parser decisions, applied one cycle later (so a push landing in the same
    // cycle as a fire updates the count by the NET amount)
    reg        evq_push_d, wrq_push_d;
    reg [63:0] evq_push_w, wrq_push_w;
    reg        ack_d, ack_err_d;
    reg [7:0]  err_code_d, err_info_d;

    wire [63:0] evq_head = evq[evq_rp[EVQ_AW-1:0]];
    wire [15:0] evq_head_due = evq_head[63:48];
    wire        evq_due = (evq_count != 8'd0) &&
                          ((frame - evq_head_due) < 16'h8000);   // due <= frame
    wire [63:0] wrq_head = wrq[wrq_rp];
    wire [15:0] wrq_head_stamp = wrq_head[63:48];
`ifdef INJECT_BUG_UART_NOFF_BLOCKED
    wire wrq_ready = (wrq_count != 8'd0) && (evq_count == 8'd0) &&   // NEGATIVE
                     ((frame - wrq_head_stamp) != 16'd0);            // CONTROL
`else
    wire wrq_ready = (wrq_count != 8'd0) &&
                     ((frame - wrq_head_stamp) != 16'd0);  // strictly after push frame
`endif
    wire evq_head_fire = grant && evq_due;
    wire wrq_head_fire = grant && !evq_due && wrq_ready;

    // one write per granted cycle; due events before live writes
    // (the RHS must be sliced to the port's 42 bits: flag, sec, addr, data)
    always @(*) begin
        wr_valid = grant && (evq_due || wrq_ready);
        {wr_flag, wr_sec, wr_addr, wr_data} =
            evq_due ? {evq_head[47], evq_head[40], evq_head[39:32], evq_head[31:0]}
                    : {wrq_head[47], wrq_head[40], wrq_head[39:32], wrq_head[31:0]};
    end

    // ---- responses: one pattern register, fed one byte per cycle ------------
    reg [7:0] txf [0:TXF_DEPTH-1];
    reg [4:0] txf_wp, txf_rp;            // TXF_DEPTH is 16
    wire      txf_full  = (txf_wp[3:0] == txf_rp[3:0]) && (txf_wp[4] != txf_rp[4]);
    wire      txf_empty = (txf_wp == txf_rp);
    reg [3:0]  resp_len;                 // bytes waiting to enter the FIFO
    reg [63:0] resp_bytes;               // up to 8, most significant first

    // ---- the parser, the queue effects, the response feeder -----------------
    reg booted;
    always @(posedge clk) begin
        evq_push_d <= 1'b0; wrq_push_d <= 1'b0;
        ack_d <= 1'b0; ack_err_d <= 1'b0;
        if (!rst_n) begin
`ifdef INJECT_BUG_UART_RESET_LEAK
            // NEGATIVE CONTROL: the queues survive the reset that should kill
            // them; pointers, counts and the order bookkeeping stay as they were
`else
            evq_wp <= 0; evq_rp <= 0; wrq_wp <= 0; wrq_rp <= 0;
            evq_count <= 8'd0; wrq_count <= 8'd0; last_due <= 16'd0;
`endif
            pstate <= P_OP; pay <= 48'd0; due <= 16'd0; cksum <= 8'd0;
            seq <= 8'd0; drops <= 8'd0; errs <= 8'd0;
            evq_overflow <= 1'b0; wrq_overflow <= 1'b0;
            late_seen <= 1'b0; resync_seen <= 1'b0;
            gap <= 16'd0;
            resp_len <= 4'd0;
            booted <= 1'b0;
            txf_wp <= 5'd0;
        end else begin
            if (rx_framing) begin                            // stop bit != 1
                resync_seen <= 1'b1;
                ack_err_d <= 1'b1; err_code_d <= 8'd4; err_info_d <= 8'h00;
                pstate <= P_OP;
            end else if (gap_fire) begin                     // stalled packet
                resync_seen <= 1'b1;
                ack_err_d <= 1'b1; err_code_d <= 8'd4; err_info_d <= 8'h00;
                pstate <= P_OP;
            end else if (rx_done) begin
                gap <= 16'd0;
                case (pstate)
                P_OP: begin
                    cksum <= rx_byte;
                    case (rx_byte)
                    8'h57: pstate <= P_W1;
                    8'h45: begin pstate <= P_F0; due <= 16'd0; end
                    8'h51: begin                       // STATUS reply
                        resp_len   <= 4'd8;
                        resp_bytes <= {8'h55, frame[15:8], frame[7:0],
                                       evq_count, wrq_count, drops, errs, flags};
                        evq_overflow <= 1'b0; wrq_overflow <= 1'b0;
                        late_seen <= 1'b0; resync_seen <= 1'b0;
                    end
                    8'h58: begin                       // ABORT: queues cleared
                        evq_wp <= evq_rp; wrq_wp <= wrq_rp;
                        evq_count <= 8'd0; wrq_count <= 8'd0;
                        seq <= seq + 8'd1;
                        resp_len   <= 4'd2;
                        resp_bytes <= {8'h06, seq, 48'd0};
                    end
                    default: begin                     // bad opcode
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd6; err_info_d <= rx_byte;
                    end
                    endcase
                end
                P_W1, P_W2, P_W3, P_W4, P_W5, P_W6: begin
                    pay <= {pay[39:0], rx_byte};   // MSB first: first byte ends at the top
                    cksum <= cksum + rx_byte;
                    pstate <= (pstate == P_W6) ? P_WCK : pstate + 5'd1;
                end
                P_WCK: begin
                    pstate <= P_OP;
`ifdef INJECT_BUG_UART_NO_CHECKSUM
                    // NEGATIVE CONTROL: the checksum is not enforced
                    begin
`else
                    if (cksum + rx_byte != 8'd0) begin
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd5; err_info_d <= pay[39:32];
                    end else begin
`endif
                        if (wrq_count == WRQ_FULL) begin
                            wrq_overflow <= 1'b1; drops <= drops + 8'd1;
                            ack_err_d <= 1'b1;
                            err_code_d <= 8'd1; err_info_d <= pay_out[39:32];
                        end else begin
                            wrq_push_d <= 1'b1;
                            wrq_push_w <= {frame, pay_out};
                            seq <= seq + 8'd1;
                            ack_d <= 1'b1;
                        end
                    end
                end
                P_F0: begin
                    due[7:0] <= rx_byte;
                    cksum <= cksum + rx_byte;
                    pstate <= P_F1;
                end
                P_F1: begin
                    due[15:8] <= rx_byte;
                    cksum <= cksum + rx_byte;
                    pstate <= P_E1;
                end
                P_E1, P_E2, P_E3, P_E4, P_E5, P_E6: begin
                    pay <= {pay[39:0], rx_byte};   // MSB first: first byte ends at the top
                    cksum <= cksum + rx_byte;
                    pstate <= (pstate == P_E6) ? P_ECK : pstate + 5'd1;
                end
                P_ECK: begin
                    pstate <= P_OP;
`ifdef INJECT_BUG_UART_NO_CHECKSUM
                    // NEGATIVE CONTROL: the checksum is not enforced
                    begin
`else
                    if (cksum + rx_byte != 8'd0) begin
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd5; err_info_d <= pay[39:32];
                    end else if (evq_count != 8'd0 && due_past
                                 && (due - last_due) >= 16'h8000) begin
                        ack_err_d <= 1'b1;             // past AND out of order:
                        err_code_d <= 8'd3;            //   dropped and reported
                        err_info_d <= pay[39:32];
                    end else begin
`endif
                    if (due_past) begin
                        // due already gone: still execute, next window, loudly
                        late_seen <= 1'b1;
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd3; err_info_d <= pay_out[39:32];
                        if (evq_count == EVQ_FULL) begin
                            evq_overflow <= 1'b1; drops <= drops + 8'd1;
                            err_code_d <= 8'd2; err_info_d <= pay_out[39:32];
                        end else begin
                            evq_push_d <= 1'b1;
                            evq_push_w <= {frame + 16'd1, pay_out};
                            last_due <= frame + 16'd1;
                            seq <= seq + 8'd1;
                        end
                    end else if (evq_count != 8'd0
                                 && ((due - last_due) >= 16'h8000)) begin
                        // out of order with the queue: dropped and reported
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd3; err_info_d <= pay[39:32];
                    end else if (evq_count == EVQ_FULL) begin
`ifdef INJECT_BUG_UART_EVQ_OVF_SILENT
                        evq_rp <= evq_rp + 1'b1;       // NEGATIVE CONTROL:
                        evq[evq_wp[EVQ_AW-1:0]] <= {due, pay_out};  // evict the
                        evq_wp <= evq_wp + 1'b1;       //   oldest, say nothing
`else
                        evq_overflow <= 1'b1; drops <= drops + 8'd1;
                        ack_err_d <= 1'b1;
                        err_code_d <= 8'd2; err_info_d <= pay_out[39:32];
`endif
                    end else begin
                        evq_push_d <= 1'b1;
                        evq_push_w <= {due, pay_out};
                        last_due <= due;
                        seq <= seq + 8'd1;
                        ack_d <= 1'b1;
                    end
                    end
                end
                default: pstate <= P_OP;
                endcase
            end else if (pstate != P_OP) begin
                gap <= gap + 16'd1;
            end

            // ---- resolve the previous decision; net counts stay exact -------
            if (evq_push_d) begin
                evq[evq_wp[EVQ_AW-1:0]] <= evq_push_w;
                evq_wp <= evq_wp + 1'b1;
            end
            if (wrq_push_d) begin
                wrq[wrq_wp] <= wrq_push_w;
                wrq_wp <= wrq_wp + 3'd1;
            end
            case ({evq_push_d, evq_head_fire})
                2'b10: evq_count <= evq_count + 8'd1;
                2'b01: evq_count <= evq_count - 8'd1;
                default: ;                       // push+fire: net zero
            endcase
            case ({wrq_push_d, wrq_head_fire})
                2'b10: wrq_count <= wrq_count + 8'd1;
                2'b01: wrq_count <= wrq_count - 8'd1;
                default: ;
            endcase
            if (evq_head_fire) evq_rp <= evq_rp + 1'b1;
            if (wrq_head_fire) wrq_rp <= wrq_rp + 3'd1;

            // ---- responses: one byte into the FIFO per cycle ----------------
            if (resp_len != 4'd0) begin
                if (!txf_full) begin
                    txf[txf_wp[3:0]] <= resp_bytes[63:56];
                    txf_wp <= txf_wp + 5'd1;
                end else begin
                    errs <= errs + 8'd1;    // a report we cannot send is still
                end                          // an error, and is counted
                resp_bytes <= {resp_bytes[55:0], 8'd0};
                resp_len   <= resp_len - 4'd1;
            end else if (ack_err_d) begin
                errs <= errs + 8'd1;
                resp_len   <= 4'd4;
                resp_bytes <= {8'h1C, err_code_d, seq, err_info_d, 24'd0};
            end else if (ack_d) begin
                resp_len   <= 4'd2;
                resp_bytes <= {8'h06, seq, 48'd0};
            end else if (!booted) begin
                resp_len   <= 4'd1;          // BOOT: the host SEES resets
                resp_bytes <= {8'hA5, 48'd0};
                booted <= 1'b1;
            end
        end
    end

    // ---- TX serialiser -------------------------------------------------------
    reg       tx_busy;
    reg [DIVW-1:0] txdiv;
    reg [3:0] tbit;
    reg [9:0] txsh;                      // {stop, byte, start}
    always @(posedge clk) begin
        if (!rst_n) begin
            tx <= 1'b1; tx_busy <= 1'b0; txdiv <= 0; tbit <= 4'd0;
            txsh <= 10'h3FF; txf_rp <= 5'd0;
        end else begin
            if (!tx_busy) begin
                tx <= 1'b1;
                if (!txf_empty) begin
                    txsh    <= {1'b1, txf[txf_rp[3:0]], 1'b0};
                    txf_rp  <= txf_rp + 5'd1;
                    tx_busy <= 1'b1;
                    txdiv   <= 0;
                    tbit    <= 4'd0;
                    tx      <= 1'b0;                     // start bit
                end
            end else if (txdiv == DIV - 1) begin
                txdiv <= 0;
                if (tbit == 4'd9) begin
                    tx_busy <= 1'b0;                     // line returns to idle
                end else begin
                    tbit <= tbit + 4'd1;
                    txsh <= {1'b1, txsh[9:1]};
                    tx   <= txsh[1];
                end
            end else begin
                txdiv <= txdiv + 1'b1;
            end
        end
    end
endmodule
`default_nettype wire

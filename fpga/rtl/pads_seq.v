// pads_seq.v -- the pads demo's front end (#449): four buttons in, UART packets
// out, onto synth_top's own uart_rxd. The core sees exactly the traffic
// fpga/uart_host.py would send, so no core port is added and synth_top is
// unchanged. The contract this module keeps is written as code in
// fpga/pads_rom.py (schedule, apply_frames, press_cycles) and the bench
// (fpga/verify_pads_top.py) checks the chip against THAT, bit for bit.
//
//   buttons -> eager debounce (DB_CYCLES lockout) -> one program per button
//     (hit ROM = drums_fx.hit_writes) -> EVENT packets, due = t0 + off + LAT
//   entering pads mode -> guard idle -> boot ROM (R1 preamble, R1 kit, drum
//     bus gains) as WRITE packets -> RUN
//   source select: the bridge hears the external pin (host mode) or this
//     sender (pads mode), never both; the effective select changes only
//     between this sender's packets.
//
// The audio frame is MIRRORED, not read: the same two-flop synchroniser on the
// same reset pad and the same cycle/frame counters as synth_top.v, on the same
// clock, so `afr` equals the bridge's audio frame in every cycle. A mirror that
// drifted would land every due in the wrong frame, which the bench would see.
//
// NEGATIVE CONTROLS (fpga/verify_pads_top.py turns red for each, for its reason):
//   INJECT_BUG_PADS_DEBOUNCE_DOUBLE  no lockout: every contact bounce is a press
//   INJECT_BUG_PADS_TRIG_STUCK       the stop's bit is never dropped, so the next
//                                    press of that button writes no 0->1 edge
//   INJECT_BUG_PADS_SRC_STUCK        the source select ignores the switch
`default_nettype none
module pads_seq #(
    parameter CLK_HZ    = 12_288_000,
    parameter BAUD      = 115_200,
    parameter DB_CYCLES = 61_440            // 5.0 ms at 12.288 MHz (pads_rom.DB_CYCLES)
)(
    input  wire       clk,
    input  wire       rst_n_pad,           // the same pad synth_top's rst_n_pad gets
    input  wire [3:0] btn,                 // raw pins, asynchronous
    input  wire       sel_pads,            // raw switch, asynchronous: 1 = pads mode
    input  wire       ext_rxd,             // the external UART RX pin
    output wire       core_rxd,            // -> synth_top.uart_rxd
    output wire       mode_pads,           // the effective source is this sender
    output wire       ready,               // pads mode and the kit is loaded
    output wire       active               // a press was accepted in the last ~170 ms
);
    localparam integer DIV = (CLK_HZ + BAUD / 2) / BAUD;
    localparam integer DIVW = $clog2(DIV);
    localparam integer GUARD = 64 * DIV;   // pads_rom.GUARD_BITS bit times
    localparam integer DBW = $clog2(DB_CYCLES + 1);
    localparam [1:0] ST_HOST = 2'd0, ST_GUARD = 2'd1, ST_BOOT = 2'd2, ST_RUN = 2'd3;
    localparam [1:0] K_PLAIN = 2'd0, K_RISE = 2'd1, K_DROP = 2'd2;

    // ---- the mirror of synth_top's reset and frame ---------------------------
    reg [1:0] rst_q;
    always @(posedge clk) rst_q <= {rst_q[0], rst_n_pad};
    wire rst_n = rst_q[1];
    reg [7:0]  cyc;
    reg [15:0] frame;
    always @(posedge clk)
        if (!rst_n) begin cyc <= 8'd0; frame <= 16'd0; end
        else begin
            cyc <= cyc + 8'd1;
            if (cyc == 8'd0) frame <= frame + 16'd1;
        end
    wire [15:0] afr = frame - 16'd1;          // the audio frame (uart_bridge.v's view)

    // ---- synchronisers: the only flops that see the asynchronous pins --------
    (* ASYNC_REG = "TRUE" *) reg [3:0] btn_q0, btn_q1;
    (* ASYNC_REG = "TRUE" *) reg       sel_q0, sel_q1;
    always @(posedge clk) begin
        btn_q0 <= btn;   btn_q1 <= btn_q0;
        sel_q0 <= sel_pads; sel_q1 <= sel_q0;
    end

    // ---- eager debounce: the first change flips, then DB_CYCLES are ignored ---
    reg [4:0]     db_state;                 // [3:0] buttons, [4] the source switch
    reg [3:0]     press;                    // one-cycle pulse, the cycle after the flip
    reg [DBW-1:0] db_lock [0:4];
    wire [4:0]    db_in = {sel_q1, btn_q1};
    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            db_state <= 5'd0; press <= 4'd0;
            for (i = 0; i < 5; i = i + 1) db_lock[i] <= {DBW{1'b0}};
        end else begin
            press <= 4'd0;
            for (i = 0; i < 5; i = i + 1) begin
                if (db_lock[i] != {DBW{1'b0}}) db_lock[i] <= db_lock[i] - 1'b1;
                else if (db_in[i] != db_state[i]) begin
                    db_state[i] <= db_in[i];
`ifdef INJECT_BUG_PADS_DEBOUNCE_DOUBLE
                    // NEGATIVE CONTROL: no lockout -- a bouncing contact fires
                    // once per bounce edge
                    if (i == 4) db_lock[i] <= DB_CYCLES - 1;
`else
                    db_lock[i] <= DB_CYCLES - 1;
`endif
                    if (i < 4) press[i] <= db_in[i];
                end
            end
        end
    end
`ifdef INJECT_BUG_PADS_SRC_STUCK
    wire sel_want = 1'b1;                   // NEGATIVE CONTROL: the switch is ignored
`else
    wire sel_want = db_state[4];
`endif

    // ---- the ROMs ------------------------------------------------------------
    reg  [7:0]  boot_idx;
    wire [41:0] boot_word;
    wire [7:0]  boot_len;
    wire [15:0] lat;
    pads_boot_rom u_boot (.idx(boot_idx), .word(boot_word), .len(boot_len), .lat(lat));

    reg  [3:0]  busy;
    reg  [2:0]  step [0:3];
    reg  [15:0] t0   [0:3];
    wire [51:0] hw   [0:3];
    wire [2:0]  hlen [0:3];
    wire [7:0]  hlast [0:3];
    genvar gv;
    generate for (gv = 0; gv < 4; gv = gv + 1) begin : g_rom
        localparam [1:0] VID = gv;
        pads_hit_rom u_hit (.voice(VID), .step(step[gv]), .word(hw[gv]),
                            .len(hlen[gv]), .last_off(hlast[gv]));
    end endgenerate

    // per voice: the scheduled frame of its next write, and whether it may go.
    // A write goes once its frame has PASSED (afr > sched), so every press
    // registered in that frame is already known: dues leave in (sched, button,
    // step) order and never decrease.
    wire [15:0] sched   [0:3];
    wire [15:0] until_f [0:3];
    wire [3:0]  rdy, done, free;
    generate for (gv = 0; gv < 4; gv = gv + 1) begin : g_v
        assign sched[gv]   = t0[gv] + {8'd0, hw[gv][49:42]};
        assign until_f[gv] = t0[gv] + {8'd0, hlast[gv]} + lat;
        wire [15:0] d_s = afr - sched[gv];
        wire [15:0] d_u = afr - until_f[gv];
        assign done[gv] = (step[gv] == hlen[gv]);
        assign rdy[gv]  = busy[gv] && !done[gv] && (d_s != 16'd0) && !d_s[15];
        assign free[gv] = busy[gv] && done[gv] && (d_u != 16'd0) && !d_u[15];
    end endgenerate

    // the earliest ready write; ties go to the lower button
    function earlier(input [15:0] a, input [15:0] b);   // a strictly earlier than b
        begin earlier = (a - b) >= 16'h8000; end
    endfunction
    reg        pick_ok;
    reg [1:0]  pick_v;
    integer k;
    always @(*) begin
        pick_ok = 1'b0; pick_v = 2'd0;
        for (k = 0; k < 4; k = k + 1)
            if (rdy[k] && (!pick_ok || earlier(sched[k], sched[pick_v]))) begin
                pick_ok = 1'b1; pick_v = k;
            end
    end
    wire [51:0] pw    = hw[pick_v];
    wire [1:0]  pkind = pw[51:50];
    reg  [15:0] shadow;                     // the stops mask as this sender last wrote it
    reg  [15:0] shadow_n;
    always @(*) begin
        case (pkind)
        K_RISE:  shadow_n = shadow | pw[15:0];
`ifdef INJECT_BUG_PADS_TRIG_STUCK
        K_DROP:  shadow_n = shadow;         // NEGATIVE CONTROL: the bit is never dropped
`else
        K_DROP:  shadow_n = shadow & ~pw[15:0];
`endif
        default: shadow_n = shadow;
        endcase
    end
    wire [31:0] pdata = (pkind == K_PLAIN) ? pw[31:0] : {16'd0, shadow_n};
    wire [15:0] pdue  = sched[pick_v] + lat;

    // ---- packets ----------------------------------------------------------------
    // EVENT (10 bytes): 45 due_lo due_hi {F,0,SEC} A D3 D2 D1 D0 ck
    // WRITE (8 bytes):  57 {F,0,SEC} A D3 D2 D1 D0 ck
    wire [47:0] ev_reg = {pw[41], 6'd0, pw[40], pw[39:32], pdata};
    wire [71:0] ev_body = {ev_reg[7:0], ev_reg[15:8], ev_reg[23:16], ev_reg[31:24],
                           ev_reg[39:32], ev_reg[47:40], pdue[15:8], pdue[7:0], 8'h45};
    wire [47:0] wr_reg = {boot_word[41], 6'd0, boot_word[40], boot_word[39:32], boot_word[31:0]};
    wire [55:0] wr_body = {wr_reg[7:0], wr_reg[15:8], wr_reg[23:16], wr_reg[31:24],
                           wr_reg[39:32], wr_reg[47:40], 8'h57};
    function [7:0] cksum(input [79:0] body, input integer n);
        integer b; reg [7:0] s;
        begin
            s = 8'd0;
            for (b = 0; b < 10; b = b + 1) if (b < n) s = s + body[8*b +: 8];
            cksum = 8'd0 - s;
        end
    endfunction

    // ---- the serialiser: 8N1, LSB first, bytes back to back -------------------
    reg [87:0]     pkt;                     // byte 0 in [7:0]
    reg [3:0]      nleft;
    reg [9:0]      bits;                    // {stop, data, start}, bits[0] on the wire
    reg [3:0]      bitn;
    reg [DIVW-1:0] divc;
    reg            tx_busy;
    wire           tx_line = tx_busy ? bits[0] : 1'b1;

    reg [1:0]  st;
    reg        eff;                         // the effective source: 1 = this sender
    reg [$clog2(GUARD+1)-1:0] guard;
    reg [7:0]  drops;                       // presses refused while their button was busy
    reg [20:0] act;
    integer j;
    wire load_ev   = (st == ST_RUN) && !tx_busy && pick_ok && eff;
    wire load_boot = (st == ST_BOOT) && !tx_busy && (boot_idx != boot_len) && eff;

    always @(posedge clk) begin
        if (!rst_n) begin
            st <= ST_HOST; eff <= 1'b0; guard <= 0; boot_idx <= 8'd0;
            busy <= 4'd0; shadow <= 16'd0; drops <= 8'd0; act <= 21'd0;
            tx_busy <= 1'b0; nleft <= 4'd0; bits <= 10'h3FF; bitn <= 4'd0; divc <= 0;
            pkt <= 88'd0;
            for (j = 0; j < 4; j = j + 1) begin step[j] <= 3'd0; t0[j] <= 16'd0; end
        end else begin
            if (act != 21'd0) act <= act - 1'b1;
            // -- the serialiser --------------------------------------------------
            if (tx_busy) begin
                if (divc == DIV - 1) begin
                    divc <= 0;
                    if (bitn == 4'd9) begin
                        if (nleft == 4'd1) tx_busy <= 1'b0;
                        else begin
                            nleft <= nleft - 4'd1;
                            bits  <= {1'b1, pkt[15:8], 1'b0};
                            pkt   <= pkt >> 8;
                            bitn  <= 4'd0;
                        end
                    end else begin
                        bits <= {1'b1, bits[9:1]};
                        bitn <= bitn + 4'd1;
                    end
                end else divc <= divc + 1'b1;
            end
            // -- the source switch: only between this sender's packets ------------
            if (!tx_busy && eff != sel_want) begin
                eff <= sel_want;
                busy <= 4'd0; shadow <= 16'd0;
                for (j = 0; j < 4; j = j + 1) step[j] <= 3'd0;
                if (sel_want) begin st <= ST_GUARD; guard <= GUARD - 1; boot_idx <= 8'd0; end
                else st <= ST_HOST;
            end else begin
                case (st)
                ST_GUARD: if (guard == 0) st <= ST_BOOT; else guard <= guard - 1'b1;
                ST_BOOT: begin
                    if (load_boot) begin
                        pkt <= {24'd0, cksum({24'd0, wr_body}, 7), wr_body};
                        bits <= {1'b1, wr_body[7:0], 1'b0};
                        nleft <= 4'd8; bitn <= 4'd0; divc <= 0; tx_busy <= 1'b1;
                        boot_idx <= boot_idx + 8'd1;
                    end else if (!tx_busy && boot_idx == boot_len) st <= ST_RUN;
                end
                default: ;
                endcase
                // -- the programs ------------------------------------------------------
                for (j = 0; j < 4; j = j + 1) begin
                    if (press[j] && st == ST_RUN && eff && (!busy[j] || free[j])) begin
                        busy[j] <= 1'b1; step[j] <= 3'd0; t0[j] <= afr;
                        act <= 21'h1FFFFF;
                    end else begin
                        if (press[j] && st == ST_RUN && eff) drops <= drops + 8'd1;
                        if (free[j]) busy[j] <= 1'b0;
                    end
                end
                if (load_ev) begin
                    pkt <= {8'd0, cksum({8'd0, ev_body}, 9), ev_body};
                    bits <= {1'b1, ev_body[7:0], 1'b0};
                    nleft <= 4'd10; bitn <= 4'd0; divc <= 0; tx_busy <= 1'b1;
                    step[pick_v] <= step[pick_v] + 3'd1;
                    shadow <= shadow_n;
                end
            end
        end
    end

    assign core_rxd  = eff ? tx_line : ext_rxd;
    assign mode_pads = eff;
    assign ready     = eff && (st == ST_RUN);
    assign active    = (act != 21'd0);
endmodule
`default_nettype wire

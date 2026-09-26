// drum_dp.v -- the drum section's sources, envelopes and routing, bit-exact
// against model/drums_fx.py (DrumsFx). Supersedes drum_src_seq.v, the area
// strawman that was verified against nothing.
//
// model/drums_fx.py is the specification (contract section 15, DR 0008):
// verify_drums.py / tb_drums.v drive this module inside drum_kit.v with the
// model's write stream and compare both buses every frame with no
// tolerance; the INJECT_BUG_DRUM_* defines prove the comparison can fail.
// Nothing below is a numeric choice the model did not already make.
//
// Per frame (15.2), on ONE 25 x 16 multiplier:
//   tick      fire = stops & ~stops_q; the LFSR (x^31 + x^15 + x^13 + x^11 + 1) leaps 16 bits (15.4); the six
//             squares are read from their phases and the phases advance
//   ENVS x 1  each envelope: fire (peak * accent), re-strike (strike * 13/16),
//             or decay (level * rate) -- one multiply each; hold and choke
//             are muxes (15.3)
//   PATHS x 2 each path: the tanh interpolation of its nonlinearity, then
//             source' * (ENV(e1) + ENV(e2)); the product >> (15 + att) is
//             added to the mix bus or pushed to the bank's exc register of
//             its destination mode (15.5)
//   drain     the last path's value; then bank_start, mix_out, mix_valid
// 1 + ENVS + 1 + 2*PATHS + 2 = 68 clocks for 18 envelopes and 23 paths
// (tb_drums.v measures it), before the bank's 3*MODES+2 = 50. 118 of the
// frame's 256 (ARCHITECTURE 5), and synth_top's `overrun` flag watches it.
//
// REVISION 10 widened the PATH word from 22 bits to 25: the envelope fields
// and the destination field are 5 bits each. At 4 bits the block could
// address twelve envelopes and fifteen modes, and DEST_MIX was 15 -- which
// is also mode 15, so at MODES = 16 the last mode could never be a
// destination. The sentinels moved with the fields: envelope index 31 reads
// full scale, destination 31 is the mix bus.
//
// Formats, all the model's: level 24-bit unsigned; rate Q0.16; accent Q0.15;
// sources Q1.15 signed; envsum 16-bit unsigned (up to 65534); v 17-bit
// signed; mix_out 21-bit signed, exact. The tanh is the ladder's TANH16
// (Appendix C, tanh16.hex) read on a Q1.15 argument: idx = |u| >> 13,
// fr = |u| & 0x1FFF, clamp at |u| >= 2^17 (4.0) -- identical to
// LadderFx.tanh_fx(u << 5).
//
// The control image arrives as buses (the bench is the host's register
// file, as ladder_dp's g/k/gain/ogain are the bench's); they MUST be held
// from frame_tick until body_valid. frame_tick is ignored while busy.
`default_nettype none
module drum_dp #(
    parameter ENVS     = 18,
    parameter PATHS    = 23,
    parameter MODES    = 16,
    parameter STOPS    = 11,
    parameter SB       = 28,             // the bank's state width, for the tap
    parameter EW       = 21,             // the bank's excitation width
    parameter MW       = 4,              // mode index width
    parameter ROM_FILE = "tanh16.hex"
)(
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  frame_tick,
    input  wire [STOPS-1:0]      stops,
    input  wire [STOPS*16-1:0]   accent_bus,
    input  wire [6*24-1:0]       osc_inc_bus,
    input  wire [ENVS*27-1:0]    env_ctl_bus,
    input  wire [ENVS*24-1:0]    env_peak_bus,
    input  wire [ENVS*16-1:0]    env_rate_bus,
    input  wire [ENVS*16-1:0]    env_frate_bus,       // revision 13: the final strike (15.3)
    input  wire [PATHS*25-1:0]   path_bus,
    output wire [MW-1:0]         tap_sel,
    input  wire signed [SB-1:0]  tap_y1,
    output reg                   exc_we,
    output reg  [MW-1:0]         exc_mode,
    output reg  signed [EW-1:0]  exc_val,
    output reg                   bank_start,
    output reg  signed [21:0]    mix_out,
    output reg                   mix_valid
);
    localparam EI = 5;                              // envelope index width (<= 31 envelopes)
    localparam PI = 5;                              // path index width (<= 32 paths)
    localparam [4:0] ENV_FULL = 5'd31;              // the envelope index that reads full scale
    localparam [4:0] DEST_MIX = 5'd31;              // the destination that is the mix bus
    localparam T_MAX = 11'd2047;
    localparam [15:0] BURST_C = 16'd53248;          // 13/16 in Q0.16

    // ---- tanh table: TANH16 plus the model's top word 32767 -------------------
    reg signed [15:0] rom [0:16];
    initial $readmemh(ROM_FILE, rom);

    // ---- state ----------------------------------------------------------------
    reg [STOPS-1:0] stops_q, fire_q;
    reg [30:0] lfsr;
    reg signed [15:0] noise_r, sq_r, sqpair_r;
    reg [5:0]         sqmsb_r;
    reg [23:0] phase [0:5];
    reg [23:0] level  [0:ENVS-1];
    reg [23:0] strike [0:ENVS-1];
    reg [23:0] fcap   [0:ENVS-1];   // revision 13: the fire level, captured once per hit (15.3)
    reg [10:0] tcnt   [0:ENVS-1];
    reg signed [21:0] dmix;
    reg [1:0]  st;                                  // 0 idle, 1 envelopes, 2 paths, 3 drain
    reg [EI:0] e;                                   // envelope slot (one extra for the pipeline)
    reg [PI:0] p;                                   // path slot
    reg        half;                                // path cycle A (0) / B (1)

    // ---- the one multiplier: 25-bit signed x 16-bit unsigned -----------------
    reg  signed [24:0] mul_a;
    reg         [15:0] mul_b;
    wire signed [40:0] mul_r = mul_a * $signed({1'b0, mul_b});

    // ---- the LFSR, 16 steps at once (contract 15.4) -----------------------------
    wire [15:0] nbits;
    genvar gi;
    generate for (gi = 0; gi < 16; gi = gi + 1) begin : g_lfsr
`ifdef INJECT_BUG_DRUM_LFSR_TAP
        assign nbits[15 - gi] = lfsr[30 - gi] ^ lfsr[15 - gi] ^ lfsr[18 - gi] ^ lfsr[19 - gi]; // NEGATIVE CONTROL: one tap off
`else
        assign nbits[15 - gi] = lfsr[30 - gi] ^ lfsr[15 - gi] ^ lfsr[17 - gi] ^ lfsr[19 - gi]; // x^31 + x^15 + x^13 + x^11 + 1
`endif
    end endgenerate

    // ---- the six squares ---------------------------------------------------------
    wire signed [15:0] sq0 = phase[0][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sq1 = phase[1][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sq2 = phase[2][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sq3 = phase[3][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sq4 = phase[4][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sq5 = phase[5][23] ? -16'sd5461 : 16'sd5461;
    wire signed [15:0] sqsum_w  = sq0 + sq1 + sq2 + sq3 + sq4 + sq5;
    wire signed [15:0] sqpair_w = (phase[4][23] ? -16'sd16383 : 16'sd16383)
                                + (phase[5][23] ? -16'sd16383 : 16'sd16383);
    // one oscillator alone, +-16383 (contract 15.4, src 5..10): the cowbell's
    // two gates. Only the six sign bits are held for the frame, not six words.
    wire [5:0] sqmsb_w = {phase[5][23], phase[4][23], phase[3][23],
                          phase[2][23], phase[1][23], phase[0][23]};

    // ---- envelope slot e: decode, select the multiply ----------------------------
    wire [EI-1:0] ei = e[EI-1:0];
    wire [26:0] ectl   = env_ctl_bus[ei*27 +: 27];
    wire [3:0]  e_stop = ectl[3:0];
    wire [3:0]  e_chk  = ectl[7:4];
    wire [7:0]  e_hold = ectl[15:8];
    wire [1:0]  e_bur  = ectl[17:16];
    wire [8:0]  e_per  = ectl[26:18];
    wire [23:0] e_peak = env_peak_bus[ei*24 +: 24];
    wire [15:0] e_rate = env_rate_bus[ei*16 +: 16];
    wire [15:0] e_frate = env_frate_bus[ei*16 +: 16];
`ifdef INJECT_BUG_DRUM_LEVEL_TRIG
    wire [STOPS-1:0] fire_src = stops_q;            // NEGATIVE CONTROL: level, not edge
`else
    wire [STOPS-1:0] fire_src = fire_q;
`endif
    // Padded to sixteen so a 4-bit stop index never selects past the end of
    // the vector: an index of STOPS..15 means "never" (contract 15.3) and must
    // read 0, not X.
    wire [15:0] fire_x = {{(16-STOPS){1'b0}}, fire_src};
    wire        e_fired = (e_stop < STOPS) && fire_x[e_stop];
    wire        e_chok  = (e_chk  < STOPS) && fire_x[e_chk];
    wire [10:0] t_cur   = tcnt[ei];
    wire [10:0] t_nx    = (t_cur == T_MAX) ? T_MAX : t_cur + 11'd1;
    wire [10:0] per2    = {e_per, 1'b0};
    wire [10:0] per3    = per2 + {2'b0, e_per};
    wire        e_holdp = !e_fired && (t_nx < {3'b0, e_hold});
    wire        e_rs    = !e_fired && !e_holdp && (e_per != 9'd0) &&
                          ((e_bur >= 2'd1 && t_nx == {2'b0, e_per}) ||
                           (e_bur >= 2'd2 && t_nx == per2) ||
                           (e_bur == 2'd3 && t_nx == per3));
    wire [1:0]  e_op    = e_fired ? 2'd0 : e_holdp ? 2'd1 : e_rs ? 2'd2 : 2'd3;   // FIRE HOLD RESTRIKE DECAY
    // Revision 13 (15.3): with FRATE != 0 the LAST re-strike (t = bursts*period)
    // restores the captured fire level instead of 13/16 of the last strike, and
    // every decay after it runs at FRATE. FRATE = 0 is revision 10 exactly.
    // Neither needs a multiply of its own: the final strike is a register copy
    // (the slot's product is unused) and the final decay only swaps mul_b.
    wire        e_fen   = (e_frate != 16'd0);
`ifdef INJECT_BUG_DRUM_FINAL_SHIFT
    // NEGATIVE CONTROL: the final strike one burst early -- the 4th strike omitted
    wire [10:0] last_t  = (e_bur == 2'd2) ? {2'b0, e_per} : (e_bur == 2'd3) ? per2 : 11'd0;
`else
    wire [10:0] last_t  = (e_bur == 2'd1) ? {2'b0, e_per} : (e_bur == 2'd2) ? per2 :
                          (e_bur == 2'd3) ? per3 : 11'd0;
`endif
`ifdef INJECT_BUG_DRUM_FINAL_WEAK
    wire        e_fin   = 1'b0;                     // NEGATIVE CONTROL: revision 10's 13/16 final strike
`else
    wire        e_fin   = e_rs && e_fen && (t_nx == last_t);
`endif
`ifdef INJECT_BUG_DRUM_FINAL_SHORT
    wire        e_usefr = 1'b0;                     // NEGATIVE CONTROL: the final strike keeps RATE
`else
    wire        e_usefr = e_fen && (t_nx > last_t);
`endif
    wire [16*16-1:0] accent_x = {{((16-STOPS)*16){1'b0}}, accent_bus};
    wire [15:0] e_acc   = accent_x[e_stop*16 +: 16];
    reg  [1:0]  op_q;  reg [EI-1:0] e_q;  reg chok_q;  reg [10:0] t_q;  reg fin_q;

    // ---- envelope slot e-1: apply the product --------------------------------------
    wire [24:0] fire_lvl25 = mul_r[39:15];                                  // (peak * accent) >> 15
    wire [23:0] fire_lvl   = fire_lvl25[24] ? 24'hFFFFFF : fire_lvl25[23:0]; // usat24
    wire [23:0] rs_lvl     = mul_r[39:16];                                  // (strike * 13/16)
    wire [23:0] dec        = mul_r[39:16];                                  // (level * rate) >> 16
`ifdef INJECT_BUG_DRUM_ENV_FLOOR
    wire [23:0] dec1       = dec;                                           // NEGATIVE CONTROL: no max(1, .)
`else
    wire [23:0] dec1       = (dec == 24'd0) ? 24'd1 : dec;
`endif
    wire [23:0] lvl_q      = level[e_q];
    wire [23:0] dec_lvl    = (lvl_q < dec1) ? 24'd0 : lvl_q - dec1;

    // ---- path slot p: decode ------------------------------------------------------------
    wire [PI-1:0] pi = p[PI-1:0];
    wire [24:0] pw     = path_bus[pi*25 +: 25];
    wire [4:0]  p_src  = pw[4:0];
    wire [4:0]  p_e1   = pw[9:5];
    wire [4:0]  p_e2   = pw[14:10];
    wire [1:0]  p_nl   = pw[16:15];
    wire [2:0]  p_att  = pw[19:17];
    wire [4:0]  p_dest = pw[24:20];
    assign tap_sel = p_src[MW-1:0];
    wire signed [SB-1:0] tap_sh = tap_y1 >>> 3;
`ifdef INJECT_BUG_DRUM_TAP_NOSAT
    wire signed [15:0] tap16 = tap_sh[15:0];                                // NEGATIVE CONTROL: wrap
`else
    wire signed [15:0] tap16 = (tap_sh > 32767) ? 16'sd32767 : (tap_sh < -32768) ? -16'sd32768 : tap_sh[15:0];
`endif
`ifdef INJECT_BUG_DRUM_SQ_LONE
    // NEGATIVE CONTROL: one oscillator's gate sees the PAIR -- rev 5's defect,
    // which makes nl(a + b) where the machine makes nl(a) + nl(b) and puts a
    // difference tone 42 dB above the machine's (contract 15.5, DR 0010)
    wire signed [15:0] sq_lone = sqpair_r;
`else
    wire signed [15:0] sq_lone = sqmsb_r[p_src[2:0] - 3'd5] ? -16'sd16383 : 16'sd16383;
`endif
    wire signed [15:0] s_raw = (p_src == 5'd1) ? noise_r : (p_src == 5'd2) ? sq_r :
                               (p_src == 5'd3) ? 16'sd32767 : (p_src == 5'd4) ? sqpair_r :
                               (p_src >= 5'd5 && p_src < 5'd5 + 6) ? sq_lone :
                               // six bits: at MODES = 16 the tap range is 16..31 and
                               // `5'd16 + MODES` would wrap to 0 in five
                               ({1'b0, p_src} >= 6'd16 && {1'b0, p_src} < 6'd16 + MODES) ? tap16 : 16'sd0;
    // the swing VCA: x4 on the positive half, /8 on the negative (15.5)
    wire signed [17:0] u = (p_nl == 2'd1) ? (s_raw[15] ? {{5{s_raw[15]}}, s_raw[15:3]} : {s_raw, 2'b00})
                                          : {{2{s_raw[15]}}, s_raw};
    wire        tneg  = u[17];
    wire [17:0] tmag  = tneg ? -u : u;
    wire        tsat  = tmag[17];                   // |u| >= 2^17: 4.0
    wire [3:0]  tidx  = tmag[16:13];
    wire [4:0]  tidx1 = {1'b0, tidx} + 1'b1;
    wire [12:0] tfr   = tmag[12:0];
    wire signed [15:0] t0 = rom[tidx];
    wire signed [15:0] t1 = rom[tidx1];
    wire [15:0] tdelta = t1 - t0;
    wire [14:0] env1 = (p_e1 < ENVS) ? level[p_e1[EI-1:0]][23:9] : (p_e1 == ENV_FULL) ? 15'd32767 : 15'd0;
    wire [14:0] env2 = (p_e2 < ENVS) ? level[p_e2[EI-1:0]][23:9] : (p_e2 == ENV_FULL) ? 15'd32767 : 15'd0;
    wire [15:0] envsum = {1'b0, env1} + {1'b0, env2};
    reg  signed [15:0] t0_q;  reg tneg_q, tsat_q;  reg [1:0] nl_q;  reg signed [15:0] slin_q;
    reg  [15:0] envsum_q;  reg [2:0] att_q, att_c;  reg [4:0] dest_q, dest_c;
    // path cycle B: the nonlinearity's value
    wire [15:0] tr_mag = tsat_q ? 16'd32767 : (t0_q + mul_r[13 +: 16]);
    wire signed [15:0] tr = tneg_q ? -tr_mag : tr_mag;
    wire signed [15:0] s_nl = (nl_q == 2'd0) ? slin_q : tr;
    // consume: v = (s' * envsum) >> (15 + att), 17 bits
    wire signed [40:0] vsh = mul_r >>> (5'd15 + {2'b0, att_c});
    wire signed [16:0] v   = vsh[16:0];

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            stops_q <= 0; fire_q <= 0; lfsr <= 31'd1; noise_r <= 0; sq_r <= 0; sqpair_r <= 0;
            sqmsb_r <= 0;
            for (i = 0; i < 6; i = i + 1) phase[i] <= 0;
            for (i = 0; i < ENVS; i = i + 1) begin level[i] <= 0; strike[i] <= 0; tcnt[i] <= 0; fcap[i] <= 0; end
            fin_q <= 1'b0;
            dmix <= 0; st <= 0; e <= 0; p <= 0; half <= 0; mul_a <= 0; mul_b <= 0;
            op_q <= 0; e_q <= 0; chok_q <= 0; t_q <= 0;
            t0_q <= 0; tneg_q <= 0; tsat_q <= 0; nl_q <= 0; slin_q <= 0; envsum_q <= 0;
            att_q <= 0; att_c <= 0; dest_q <= 0; dest_c <= 0;
            exc_we <= 0; exc_mode <= 0; exc_val <= 0; bank_start <= 0; mix_out <= 0; mix_valid <= 0;
        end else begin
            exc_we <= 1'b0; bank_start <= 1'b0; mix_valid <= 1'b0;
            case (st)
            2'd0: if (frame_tick) begin                          // tick (15.2 steps 1-2, 4)
                fire_q  <= stops & ~stops_q;
                stops_q <= stops;
                lfsr    <= {lfsr[14:0], nbits};
                noise_r <= nbits;
                sq_r    <= sqsum_w;
                sqpair_r <= sqpair_w;
                sqmsb_r  <= sqmsb_w;
                for (i = 0; i < 6; i = i + 1) phase[i] <= phase[i] + osc_inc_bus[i*24 +: 24];
                dmix <= 0; e <= 0; st <= 2'd1; op_q <= 2'd1; e_q <= 0; chok_q <= 1'b0;
            end
            2'd1: begin                                          // envelopes (15.3)
                // apply slot e-1 (op_q = HOLD with chok_q = 0 on the first cycle is a no-op)
                case (op_q)
                    2'd0: begin level[e_q] <= chok_q ? 24'd0 : fire_lvl; strike[e_q] <= fire_lvl; tcnt[e_q] <= 11'd0;
`ifdef INJECT_BUG_DRUM_FCAP_STALE
                                // NEGATIVE CONTROL: a retrigger keeps the previous hit's captured level
                                fcap[e_q] <= chok_q ? 24'd0 : (fcap[e_q] != 24'd0) ? fcap[e_q] : fire_lvl;
`else
                                fcap[e_q] <= chok_q ? 24'd0 : fire_lvl;
`endif
                          end
                    2'd1: begin if (chok_q) begin level[e_q] <= 24'd0; fcap[e_q] <= 24'd0; end
                                if (e != 0) tcnt[e_q] <= t_q; end
                    2'd2: begin level[e_q] <= chok_q ? 24'd0 : (fin_q ? fcap[e_q] : rs_lvl);
                                strike[e_q] <= fin_q ? fcap[e_q] : rs_lvl; tcnt[e_q] <= t_q;
                                if (chok_q) fcap[e_q] <= 24'd0; end
                    default: begin level[e_q] <= chok_q ? 24'd0 : dec_lvl; tcnt[e_q] <= t_q;
                                if (chok_q) fcap[e_q] <= 24'd0; end
                endcase
                if (e < ENVS) begin                              // load slot e
                    mul_a <= e_fired ? {1'b0, e_peak} : e_rs ? {1'b0, strike[ei]} : {1'b0, level[ei]};
                    mul_b <= e_fired ? e_acc : e_rs ? BURST_C : e_usefr ? e_frate : e_rate;
                    op_q <= e_op; e_q <= ei; chok_q <= e_chok; t_q <= e_fired ? 11'd0 : t_nx;
                    fin_q <= !e_fired && !e_holdp && e_fin;
                    e <= e + 1'b1;
                end else begin
                    st <= 2'd2; p <= 0; half <= 1'b0; op_q <= 2'd1; chok_q <= 1'b0;
                end
            end
            2'd2: begin                                          // paths (15.5), two cycles each
                if (!half) begin                                 // A: consume p-1, request tanh(p)
                    if (p != 0) begin
                        if (dest_c == DEST_MIX) dmix <= dmix + {{5{v[16]}}, v};
                        else if (dest_c < MODES) begin
                            exc_we <= 1'b1; exc_mode <= dest_c[MW-1:0]; exc_val <= {{(EW-17){v[16]}}, v};
                        end
                    end
                    t0_q <= t0; tneg_q <= tneg; tsat_q <= tsat; nl_q <= p_nl; slin_q <= s_raw;
                    envsum_q <= envsum; att_q <= p_att; dest_q <= p_dest;
                    mul_a <= {12'b0, tfr}; mul_b <= tdelta;
                    half <= 1'b1;
                end else begin                                   // B: load s' * envsum
                    mul_a <= {{9{s_nl[15]}}, s_nl}; mul_b <= envsum_q;
                    att_c <= att_q; dest_c <= dest_q;
                    half <= 1'b0;
                    if (p == PATHS - 1) st <= 2'd3;
                    p <= p + 1'b1;
                end
            end
            default: begin                                       // drain: the last path, then launch
`ifndef INJECT_BUG_DRUM_LAST_PATH
                if (dest_c == DEST_MIX) mix_out <= dmix + {{5{v[16]}}, v};
                else begin
                    mix_out <= dmix;
                    if (dest_c < MODES) begin
                        exc_we <= 1'b1; exc_mode <= dest_c[MW-1:0]; exc_val <= {{(EW-17){v[16]}}, v};
                    end
                end
`else
                mix_out <= dmix;                                 // NEGATIVE CONTROL: the strawman's
`endif                                                           //   dropped last drum
                bank_start <= 1'b1; mix_valid <= 1'b1; st <= 2'd0;
            end
            endcase
        end
    end
endmodule
`default_nettype wire

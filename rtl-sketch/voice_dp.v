// voice_dp.v -- the voice of spec/NUMERIC-CONTRACT.md as one sequenced
// datapath around the verified ladder: three oscillators with glide and
// PolyBLEP, the mixer, two ADSRs, the cutoff path (g ROM, kc ROM, k_eff), the
// ladder (ladder_dp_n.v, two filter contexts, bit-exact on each), the VCA,
// and the master mix of the voice bus with the drum bus behind the chip's
// one hard rail (docs/ARCHITECTURE.md section 4).
//
// STATUS: REAL RTL, written operation by operation to the contract's
// sections 5-12, with the contract's own pinned tables read from
// spec/reference/tables/ ($readmemh) and the verified ladder inside. Whether
// it is BIT-EXACT against model/voice_fx.py is what verify_voice.py /
// tb_voice.v establish at the register port; docs/ARCHITECTURE.md section 9
// states the result at the time of writing. Its AREA and CYCLE COUNT are
// measurements of this RTL either way.
//
// Structure: ONE multiplier (25 x 21 signed, covering 24u x 20u), one
// sequential divider (recip_div.v) for the PolyBLEP reciprocal, three ROMs as
// logic, and a sequencer. Per frame, after the write drain (spi_ctl.v), `go`
// starts the following, in this order (ARCHITECTURE.md section 5):
//
//   1. reciprocals: for each oscillator whose increment differs from the one
//      its (e, r) was computed for, one division (19 cycles)          6.6.1
//   2. oscillators: naive wave, up to four PolyBLEP windows at two multiplies
//      each, then osc x w into the mixer; phase advance                 6.4-6.6, 7
//   3. envelope outputs ae, fe (the levels before this frame's update)  8.2
//   4. cutoff: span x fe, clamp, g and kc interpolation, k x kc         10
//   5. mixed = sat16(sum >> 15); the voice's ladder context starts     7, 11
//      while it runs (24 cycles), on the idle multiplier:
//        the envelope updates (8.3) and the glide slews (6.7) -- step 9 of
//        4.2, whose inputs are all latched by now -- and the DRUM FILTER's
//        coefficients from DCUT (its own g, kc, k_eff; DK, DGAIN, DOGAIN)
//   6. the drum gains, then the drum filter: dacc = dmix*dvol + body*bvol
//      EXACTLY (no shift, no clamp); if ROUTE.DFILT the second ladder context
//      runs on sat16(dacc >> 15) (24 more cycles); meanwhile the VCA
//      (y19 x ae) >> 15 and macc = v x vol, exact                        9, 12
//   7. master, contract 12 / DR 0008 -- ONE shift and ONE clamp over the
//      EXACT sum of the three products:
//        sample = sat16((v*vol + dmix*dvol + body*bvol) >> 15)
//      With ROUTE.DFILT the two drum terms are replaced by the drum filter's
//      output word at unity, d19 << 15 (ARCHITECTURE.md 4.1); DOGAIN is that
//      path's level. Shifting each product by 15 BEFORE the sum -- which is
//      what this file did while the drum bus was a placeholder -- is a
//      different circuit: two floors instead of one, up to 1 LSB per sample
//      on every sample that has drums in it. INJECT_BUG_VOICE_MASTER_PRESHIFT
//      is that version, kept as the negative control.
//
// The drum section presents TWO buses (DR 0008): `dmix`, the 22-bit exact sum
// of the paths routed to MIX, and `body`, the modal bank's 19-bit Q4.15 word.
// Both reach the master at full width -- a bus clipped to 16 bits before the
// gains could not be recovered by lowering them (contract 12) -- and both must
// be valid (drum_done) before this frame's master mix: the sequencer waits, it
// does not assume.
//
// Every register is the contract's width (5.1); the write port is DR 0007's
// register map. rst_n is the chip reset AND the RESET write (the top ANDs
// them), so RESET resets exactly the registers of contract 14.
`default_nettype none
module voice_dp #(
    parameter G_ROM_FILE = "../spec/reference/tables/g_rom128.hex",   // Appendix D, 129 x Q0.16
    parameter K_ROM_FILE = "../spec/reference/tables/k_rom32.hex",    // Appendix E,  33 x Q1.15
    parameter SINE_FILE  = "../spec/reference/tables/sine_q256.hex",  // Appendix B, 256 x Q1.15
    parameter EXP_FILE   = "../spec/reference/tables/exp_rom65.hex",  // Appendix H,  65 x Q0.15
    parameter TANH_FILE  = "tanh16.hex"                               // Appendix C, the ladder's
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        go,                 // one cycle per frame, after the write drain
    // register write port (contract 16.2; DR 0007 section 3)
    input  wire        wr_valid,          // already gated on SEC = 0 (DR 0007 rev 2)
    input  wire        wr_flag,
    input  wire [7:0]  wr_addr,
    input  wire [31:0] wr_data,
    // the drum section's two buses (DR 0008, 15.5/15.6), valid this frame once drum_done
    input  wire signed [21:0] dmix,
    input  wire signed [18:0] body,
    input  wire        drum_done,
    // out
    output reg  signed [15:0] sample,
    output reg         sample_valid,
    output wire        busy,
    // taps (contract 16.4)
    output reg  signed [15:0] mixed,
    output reg  [14:0] ae,
    output reg  [14:0] fe,
    output reg  [14:0] cut,
    output reg  [16:0] k_eff,
    output reg  signed [18:0] y19
);
    // ---- control image (contract 5.1; DR 0007) ---------------------------------
    reg [23:0] inc_tgt [0:2];
    reg [31:0] inc_acc [0:2];          // Q24.8
    reg [3:0]  wave    [0:2];               // nine shapes now (6.4): 4 bits
    reg [15:0] w       [0:2];
    reg [15:0] wn;                         // the noise source's mixer weight (6.10)
    reg        nsel;                       // 0: white to audio, pink to mod; 1: pink, red (2.5)
    reg [2:0]  mroute;                     // bit0 osc mod, bit1 filter mod, bit2 OSC-3 CONTROL
    reg [15:0] mmix, mwheel, mpd, mfd;     // the MOD MIX pan, the wheel, the two depths (6.9)
    reg [23:0] a_inc_a, d_dec_a, sus_a, a_inc_f, d_dec_f, sus_f;
    reg [15:0] rate_a, rate_f;
    reg        gate;
    reg [23:0] glide;
    reg [15:0] vol, dvol, bvol;
    reg        dfilt;                  // ROUTE bit 0: the drum bus through the second ladder context
    reg [15:0] cut_lo, cut_hi, track_hz;
    reg [16:0] k;
    reg [19:0] gain, ogain;
    reg [15:0] dcut;                   // the drum filter's cutoff, integer Hz, no envelope, no tracking
    reg [16:0] dk;
    reg [19:0] dgain, dogain;
    // ---- state (contract 5.1, 6.1, 8.1) ---------------------------------------
    reg [23:0] phase   [0:2];
    // Three-tap binomial smoothing removes the oscillator's top-of-band
    // residual before it reaches the nonlinear path. It is causal and keeps
    // two samples of state per oscillator so the model and RTL agree across
    // note boundaries.
    reg signed [15:0] osc_d1 [0:2], osc_d2 [0:2];
    reg [4:0]  sh      [0:2];          // e + 15
    reg [15:0] r       [0:2];
    reg [23:0] inc_er  [0:2];          // the inc (sh, r) was computed for
    reg [23:0] level_a, level_f;
    reg [1:0]  seg_a, seg_f;
    // the noise board (6.10): the LFSR, the pink biquad's history, the red one-pole
    reg [30:0] lfsr;
    reg signed [15:0] px1, px2;            // pink input history, Q1.15
    reg signed [31:0] py1, py2;            // pink output history, Q5.27
    reg signed [31:0] prl;                 // red one-pole state, Q5.27
    reg signed [15:0] nz_audio, nz_mod;    // this frame's two colours
    // the modulation path (6.9)
    reg signed [15:0] mod_sig;             // registered: computed at the end of a frame, read
    reg signed [15:0] naive3;              //   at the start of the next; osc 3's naive sample
    reg signed [15:0] amt;                 // (mod_sig * mwheel) >> 15
    reg signed [15:0] octp, octf;          // Q3.12 octaves, saturated to +-4
    reg [16:0] mant_p, mant_f;             // 2^frac, Q1.15 with 32768 = 1.0
    reg [4:0]  shf_p, shf_f;               // 15 - integer octaves: 12 .. 19
    reg [23:0] inc_mod [0:2];              // the increment each oscillator RUNS on

    // ---- ROMs: the contract's pinned images ----------------------------------
    reg [15:0] grom [0:128];
    reg [15:0] krom [0:32];
    reg [15:0] srom [0:255];
    reg [15:0] erom [0:64];
    initial begin
        $readmemh(G_ROM_FILE, grom);
        $readmemh(K_ROM_FILE, krom);
        $readmemh(SINE_FILE, srom);
        $readmemh(EXP_FILE, erom);
    end

    // ---- the constants of 6.9 and 6.10 (docs/minimoog-reference.md N4, N5) ----
    localparam signed [24:0] PB0 =  25'sd912164, PB1 = -25'sd741208, PB2 = -25'sd117831;  // Q21
    localparam signed [24:0] PA0 = -25'sd28689,  PA1 =  25'sd12348;                        // Q14
    localparam signed [24:0] RED_G = 25'sd904, RED_GAIN = 25'sd29841;
    localparam signed [24:0] SHK_SAW = 25'sd5749, SHK_TRI = 25'sd27019;
    localparam [23:0] DUTY_WIDE = 24'd4865393, DUTY_NARROW = 24'd2516582;

    // ---- the one multiplier -------------------------------------------------------
    reg  signed [24:0] ma;
    reg  signed [20:0] mb;
    wire signed [45:0] mr = ma * mb;

    // ---- the divider ---------------------------------------------------------------
    reg         div_start;
    wire        div_done;
    wire [4:0]  div_sh;
    wire [15:0] div_r;
    reg  [23:0] div_inc;
    recip_div u_div (.clk(clk), .rst_n(rst_n), .start(div_start), .inc(div_inc),
                     .done(div_done), .sh(div_sh), .r(div_r));

    // ---- the ladder (verified): context 0 the voice, context 1 the drum filter --------
    reg         lad_sv, lad_ch;
    reg  [15:0] g, g2;
    reg  [16:0] k_eff2;
    reg  signed [15:0] dx;                                        // sat16(drum_bus), the drum filter's input
    wire signed [18:0] lad_y;
    wire        lad_yv;
    wire        lad_ych;
    ladder_dp_n #(.NCH(2), .ROM_FILE(TANH_FILE), .OW(19)) u_ladder (
        .clk(clk), .rst_n(rst_n), .sample_valid(lad_sv), .ch(lad_ch),
        .x_in(lad_ch ? dx : mixed), .g(lad_ch ? g2 : g), .k(lad_ch ? k_eff2 : k_eff),
        .gain(lad_ch ? dgain : gain), .ogain(lad_ch ? dogain : ogain),
        .y_out(lad_y), .y_valid(lad_yv), .y_ch(lad_ych));

    // ---- sequencer state -------------------------------------------------------------
    localparam [6:0]
        S_IDLE = 0, S_RCHK = 1, S_RWAIT = 2,
        S_WIN = 3, S_W1 = 4, S_W2 = 5, S_MIX = 6, S_ACC = 7,
        S_ENV = 8, S_CUT1 = 9, S_ROM0 = 10, S_ROM1 = 11, S_ROM2 = 12, S_ROM3 = 13, S_KEFF0 = 14, S_KEFF1 = 15,
        S_LGO = 16,
        S_EA1 = 17, S_EF1 = 18, S_SL0 = 19, S_SL1 = 20, S_SL2 = 21,
        S_DC0 = 22, S_DC1 = 23, S_DC2 = 24, S_DC3 = 25, S_DC4 = 26, S_DC5 = 27,
        S_YWAIT = 28, S_DM1 = 29, S_DM2 = 30, S_DFLT = 31, S_VCA1 = 32, S_VCA2 = 33,
        S_DWAIT = 34, S_OUT2 = 35,
        // appended, so that S_MIX / S_KEFF1 / S_VCA2 keep the NUMBERS tb_voice.v tracks
        S_NZ0 = 36, S_NZ1 = 37, S_NZ2 = 38, S_NZ3 = 39, S_NZ4 = 40, S_NZ5 = 41, S_NZ6 = 42, S_NZ7 = 43,
        S_MD0 = 44, S_MD1 = 45, S_MD2 = 46, S_MD3 = 47, S_MD4 = 48,
        S_EP0 = 49, S_EP1 = 50, S_EP2 = 51, S_EP3 = 52,
        S_EF0 = 53, S_EF1B = 54, S_EF2 = 55, S_EF3 = 56,
        S_IM0 = 57, S_IM1 = 58,
        S_SK0 = 59, S_SK1 = 60, S_SK2 = 61,
        S_NMIX = 62, S_NACC = 63, S_CUTM = 64,
        S_RD0 = 65, S_RD1 = 66, S_RD2 = 67, S_RD3 = 68, S_RD4 = 69, S_RD5 = 70,
        S_PN0 = 71, S_PN1 = 72, S_PN2 = 73, S_OSCWAIT = 74;
    reg [6:0]  state;
    reg [1:0]  kk;                     // oscillator index
    reg [1:0]  win;                    // PolyBLEP window 0..3
    reg signed [16:0] c_pp, c_ps;      // corrections at the two edges
    reg signed [34:0] mixacc;
    reg signed [51:0] bacc, facc;      // the pink biquad's two accumulators, Q36 and Q41
    reg signed [51:0] racc;            // red: the one-pole step, then the make-up product
    reg signed [34:0] pnacc;           // the modulation pan's first product
    reg signed [15:0] shk;             // the shark-tooth mix, Q1.15
    reg signed [34:0] mixacc_shk;      // its first product
    reg [15:0] ep0, ep1;               // the 2^x ROM's two entries
    reg signed [15:0] nx0_r, nz_pink;  // this frame's white word and pink word
    reg signed [31:0] pyr;             // this frame's pink state, for the red pole
    reg        y_seen, d_seen;
    reg [15:0] g0, g1, kc0, kc1;
    reg signed [16:0] kd;
    reg [39:0] pacc;
    // the output stage's accumulators, all EXACT (contract 12): no shift and no
    // clamp until the single one at the end.
    reg signed [38:0] dacc;            // dmix*dvol + body*bvol   , |.| < 2^37
    reg signed [35:0] macc;            // v*vol                   , |.| < 2^34
    reg signed [39:0] tacc;            // the whole sum           , |.| < 2^38
    reg signed [18:0] d19;             // the drum bus after the drum filter
    assign busy = (state != S_IDLE);

    // ---- per-oscillator combinational view (index kk) --------------------------------
    wire [23:0] ph   = phase[kk];
    wire [3:0]  wv   = wave[kk];
    wire [23:0] inc  = inc_mod[kk];                    // the MODULATED increment (6.9)
    wire        is_saw = (wv == 4'd0), is_sq = (wv == 4'd1), is_p25 = (wv == 4'd2), is_tri = (wv == 4'd3);
    wire        is_shark = (wv == 4'd5), is_rev = (wv == 4'd6),
                is_p29 = (wv == 4'd7), is_p15 = (wv == 4'd8);
    wire        two_edge = is_sq | is_p25 | is_p29 | is_p15;
    wire        blep     = is_saw | is_rev | is_shark | two_edge;
    wire [23:0] dutyv = is_sq  ? 24'h800000 : is_p25 ? 24'h400000
                      : is_p29 ? DUTY_WIDE : DUTY_NARROW;
    // naive waveforms (6.4, 6.5)
    wire [16:0] tq = ph[23:7];
    wire signed [17:0] tri_hi = 18'sd98303 - $signed({1'b0, tq});
    wire signed [15:0] triv  = tq[16] ? $signed(tri_hi[15:0]) : $signed({~tq[15], tq[14:0]});
    wire signed [15:0] sawv  = $signed({~ph[23], ph[22:8]});
    wire signed [16:0] revr  = -$signed({sawv[15], sawv});          // -(-32768) saturates
    wire [9:0]  sidx = ph[23:14];
    wire [7:0]  sa   = sidx[8] ? ~sidx[7:0] : sidx[7:0];
    wire [15:0] sq   = srom[sa];
    wire signed [15:0] sinev = sidx[9] ? -$signed(sq) : $signed(sq);
    // the shark-tooth's NAIVE form is a mix and needs the multiplier, so it is
    // built in S_SK0..2; `naive` carries every shape that is one expression.
    wire signed [15:0] naive = is_saw   ? sawv
                             : is_rev   ? ((revr > 17'sd32767) ? 16'sd32767 : revr[15:0])
                             : two_edge ? ((ph < dutyv) ? 16'sh7FFF : 16'sh8000)
                             : is_tri   ? triv
                             : is_shark ? shk
                             : sinev;
    // PolyBLEP windows (6.6.3, 6.6.4): win[1] selects the shifted phase, win[0] the side of the edge
    wire [23:0] P   = win[1] ? (ph + (24'd0 - dutyv)) : ph;
    wire [24:0] q   = 25'h1000000 - {1'b0, P};
    wire        active = win[0] ? (q < {1'b0, inc}) : (P < inc);
    wire [23:0] x   = win[0] ? q[23:0] : P;
    wire [38:0] xs  = {x, 15'b0} >> sh[kk];
    wire [15:0] p   = xs[15:0];                                   // 6.6.2
    wire [15:0] u   = mr[30:15];                                  // (p * r) >> 15, < 2^16
    wire [16:0] s   = 18'h10000 - {1'b0, u};                      // 1..65536
    wire [15:0] c   = mr[32:17];                                  // (s * s) >> 17, 0..32768
    wire        last_win = (win == 2'd3) || (win == 2'd1 && !two_edge);
    // the oscillator sample (6.6.4) and the mixer term (7). The square and
    // pulse step UP at the wrap where the saw steps DOWN, so their correction
    // at p = 0 has the OPPOSITE sign to the saw's; the second edge, at the
    // duty point, steps down and takes -c at the shifted phase.
`ifdef INJECT_BUG_VOICE_SQUARE_SIGN
    wire signed [17:0] osc_two = $signed({{2{naive[15]}}, naive}) - c_pp + c_ps;   // NEGATIVE CONTROL: the saw's
`else                                                                                //   sign on the square: ~5 dB
    wire signed [17:0] osc_two = $signed({{2{naive[15]}}, naive}) + c_pp - c_ps;   //   WORSE than no correction
`endif
    // the band-limited sawtooth, which three shapes are built from: the saw
    // itself, oscillator 3's Q20 inversion of it (W5), and the shark-tooth's
    // 10/57 share of it (W3).
    wire signed [17:0] sawc_raw = $signed({{2{sawv[15]}}, sawv}) - c_pp;
    wire signed [15:0] sawc = (sawc_raw > 18'sd32767) ? 16'sd32767
                            : (sawc_raw < -18'sd32768) ? -16'sd32768 : sawc_raw[15:0];
    wire signed [17:0] revc = -$signed({{2{sawc[15]}}, sawc});
    wire signed [17:0] osc_raw = is_saw ? sawc_raw
                               : is_rev ? revc
                               : two_edge ? osc_two
                               : $signed({{2{naive[15]}}, naive});
    wire signed [15:0] osc_raw_clamped = (osc_raw > 18'sd32767) ? 16'sd32767 : (osc_raw < -18'sd32768) ? -16'sd32768 : osc_raw[15:0];
    wire osc2_valid;
    wire signed [15:0] osc2_sample;
    osc_2x_saw_path osc2_path(
        .clk(clk), .rst_n(rst_n), .frame_valid(state == S_MIX),
        .phase(ph), .inc(inc), .sh(sh[kk]), .recip(r[kk]),
        .out_valid(osc2_valid), .out_sample(osc2_sample));
    wire signed [18:0] osc_smooth_sum = $signed({{3{osc_raw_clamped[15]}}, osc_raw_clamped})
                                      + ($signed({{3{osc_d1[kk][15]}}, osc_d1[kk]}) <<< 1)
                                      + $signed({{3{osc_d2[kk][15]}}, osc_d2[kk]});
`ifdef INJECT_BUG_VOICE_OSC_SMOOTH_OFF
    wire signed [15:0] osc = osc_raw_clamped;         // NEGATIVE CONTROL: bypass alias filter
`else
    wire signed [15:0] osc = sat16t(osc_smooth_sum >>> 2);
`endif

    // ---- envelopes (8.3) --------------------------------------------------------------
    function [25:0] env_update(input g_, input [1:0] seg, input [23:0] level,
                               input [23:0] ai, input [23:0] dd, input [23:0] su, input [23:0] dec);
        reg [24:0] sum; reg signed [25:0] diff; reg [23:0] step;
        begin
            if (g_) begin
                case (seg)
                    2'd0: begin sum = {1'b0, level} + {1'b0, ai};
                                if (sum >= 25'h0FFFFFF) env_update = {2'd1, 24'hFFFFFF};
                                else env_update = {2'd0, sum[23:0]}; end
                    2'd1: begin diff = $signed({2'b00, level}) - $signed({2'b00, dd});
                                if (diff <= $signed({2'b00, su})) env_update = {2'd2, su};
                                else env_update = {2'd1, diff[23:0]}; end
                    default: env_update = {seg, su};
                endcase
            end else begin
`ifdef INJECT_BUG_VOICE_ENV_FLOOR
                step = dec;                                   // NEGATIVE CONTROL: no max(1, .): below
`else                                                         //   2^16 / rate the note never ends (8.3)
                step = (dec == 24'd0) ? 24'd1 : dec;
`endif
                diff = $signed({2'b00, level}) - $signed({2'b00, step});
                env_update = {seg, diff[25] ? 24'd0 : diff[23:0]};
            end
        end
    endfunction

    // ---- cutoff (10), for the voice (cut) and the drum filter (dcut, clamped) ----------
    wire signed [16:0] span = $signed({1'b0, cut_hi}) - $signed({1'b0, cut_lo});
    wire signed [16:0] t    = mr[31:15];                          // (span * fe) >> 15
    wire signed [18:0] cut_raw = $signed({3'b0, cut_lo}) + $signed({{2{t[16]}}, t}) + $signed({3'b0, track_hz});
    wire [14:0] cut_n  = (cut_raw < 19'sd30) ? 15'd30 : (cut_raw > 19'sd21600) ? 15'd21600 : cut_raw[14:0];
    // 5b: the filter-modulation factor, one variable right shift of 12..19 places
    wire [45:0] cutm_s = mr >> shf_f;
    wire [14:0] cut_m  = (cutm_s < 46'd30) ? 15'd30 : (cutm_s > 46'd21600) ? 15'd21600 : cutm_s[14:0];
    wire [45:0] incm_s = mr >> shf_p;
    wire [23:0] inc_m_n = (incm_s > 46'hFFFFFF) ? 24'hFFFFFF : incm_s[23:0];
    wire [14:0] dcut_c = (dcut < 16'd30) ? 15'd30 : (dcut > 16'd21600) ? 15'd21600 : dcut[14:0];
    wire        dphase = (state >= S_DC0) && (state <= S_DC5);
    wire [14:0] rc  = dphase ? dcut_c : cut;                      // which cutoff the ROMs serve
    wire [6:0]  gi  = rc[14:8];
    wire [7:0]  gf  = rc[7:0];
    wire [4:0]  ki  = rc[14:10];
    wire [9:0]  kf  = rc[9:0];
    wire        rom_second = (state == S_ROM1) || (state == S_DC1);
    wire [15:0] grd = rom_second ? grom[gi + 7'd1] : grom[gi];
    wire [15:0] krd = rom_second ? krom[ki + 5'd1] : krom[ki];
    wire [15:0] kc_n = kc0 + mr[25:10];                           // kc0 + ((kd * frac) >> 10)

    // ---- the noise board (6.10) ----------------------------------------------------------
    // 16 LFSR steps at once. Every tap is at bit 15 or above, so all 16 new bits
    // are a function of the OLD state -- the same leap the drum section uses.
`ifdef INJECT_BUG_VOICE_LFSR_TAP
    wire [15:0] nbits = lfsr[30:15] ^ lfsr[15:0] ^ lfsr[18:3] ^ lfsr[19:4];  // NEGATIVE CONTROL: one tap off
`else
    wire [15:0] nbits = lfsr[30:15] ^ lfsr[15:0] ^ lfsr[17:2] ^ lfsr[19:4];  // x^31+x^15+x^13+x^11+1
`endif
    wire signed [15:0] nx0 = $signed(nbits);
    wire signed [51:0] pink_t = (bacc <<< 5) - facc;                 // Q36 -> Q41, ONE floor
    wire signed [37:0] pink_s = pink_t >>> 14;                       // Q27
    wire signed [31:0] py0 = (pink_s > 38'sd2147483647) ? 32'sh7FFFFFFF
                           : (pink_s < -38'sd2147483648) ? 32'sh80000000 : pink_s[31:0];
    wire signed [17:0] pink_q = py0 >>> 14;                          // Q27 -> Q1.15, >> NOISE_SHIFT
    wire signed [15:0] pink_w = (pink_q > 18'sd32767) ? 16'sd32767
                              : (pink_q < -18'sd32768) ? -16'sd32768 : pink_q[15:0];
    wire signed [15:0] white_w = nx0_r >>> 2;                        // NOISE_SHIFT
    wire signed [32:0] rdif = $signed({pyr[31], pyr}) - $signed({prl[31], prl});
    wire signed [51:0] prl_s = $signed({{20{prl[31]}}, prl}) + (racc >>> 16);
    wire signed [31:0] prl_n = (prl_s > 52'sd2147483647) ? 32'sh7FFFFFFF
                             : (prl_s < -52'sd2147483648) ? 32'sh80000000 : prl_s[31:0];
    wire signed [23:0] red_q = racc >>> 28;                          // Q41 -> Q1.15, >> NOISE_SHIFT
    wire signed [15:0] red_w = (red_q > 24'sd32767) ? 16'sd32767
                             : (red_q < -24'sd32768) ? -16'sd32768 : red_q[15:0];

    // ---- the modulation path (6.9) --------------------------------------------------------
    wire signed [16:0] amt_r = mr[31:15];
    wire signed [15:0] amt_n = (amt_r > 17'sd32767) ? 16'sd32767
                             : (amt_r < -17'sd32768) ? -16'sd32768 : amt_r[15:0];
    wire signed [16:0] oct_r = mr[31:15];
    wire signed [15:0] oct_n = (oct_r > 17'sd16383) ? 16'sd16383            // +-4 octaves
                             : (oct_r < -17'sd16384) ? -16'sd16384 : oct_r[15:0];
    wire        ep_phase = (state >= S_EP0) && (state <= S_EP3);
    wire signed [15:0] octv = ep_phase ? octp : octf;
    wire [6:0]  eidx = {1'b0, octv[11:6]};        // 7 bits: entry 64 is the guard
    wire [5:0]  efrc = octv[5:0];
    wire        e_second = (state == S_EP1) || (state == S_EF1B);
    wire [15:0] erd = e_second ? erom[eidx + 7'd1] : erom[eidx];
    wire [16:0] mant_n = 17'd32768 + {1'b0, ep0} + mr[21:6];
    wire [4:0]  shf_n  = 5'd15 - {{1{octv[15]}}, octv[15:12]};   // 12 .. 19
    wire [15:0] mmix_c = (mmix > 16'd32768) ? 16'd32768 : mmix;
    wire signed [16:0] mpan_r = (pnacc + mr) >>> 15;
    wire signed [15:0] mpan_n = (mpan_r > 17'sd32767) ? 16'sd32767
                              : (mpan_r < -17'sd32768) ? -16'sd32768 : mpan_r[15:0];
    wire signed [17:0] shk_r = (mixacc_shk + mr) >>> 15;
    wire signed [15:0] shk_n = (shk_r > 18'sd32767) ? 16'sd32767
                             : (shk_r < -18'sd32768) ? -16'sd32768 : shk_r[15:0];

    // ---- glide slew (6.7) ---------------------------------------------------------------
    wire [31:0] tgt   = {inc_tgt[kk], 8'b0};
    wire [55:0] Pfull = {mr[39:0], 16'b0} + {16'b0, pacc};       // inc_acc * glide, exact
    wire [31:0] d     = Pfull[55:24];
`ifdef INJECT_BUG_VOICE_GLIDE_FLOOR
    wire [31:0] dmax  = d;                                        // NEGATIVE CONTROL: no max(1, .): a small
`else                                                             //   increment times a small rate never moves
    wire [31:0] dmax  = (d == 32'd0) ? 32'd1 : d;
`endif
    wire signed [33:0] acc_up = $signed({2'b0, inc_acc[kk]}) + $signed({2'b0, dmax});
    wire signed [33:0] acc_dn = $signed({2'b0, inc_acc[kk]}) - $signed({2'b0, dmax});

    // ---- output (9, 12; ARCHITECTURE.md 4) ---------------------------------------------
    wire signed [19:0] msh = mixacc[34:15];
    function signed [15:0] sat16m(input signed [19:0] v);
        sat16m = (v > 20'sd32767) ? 16'sd32767 : (v < -20'sd32768) ? -16'sd32768 : v[15:0];
    endfunction
    function signed [15:0] sat16q(input signed [23:0] v);          // the drum filter's input rail
        sat16q = (v > 24'sd32767) ? 16'sd32767 : (v < -24'sd32768) ? -16'sd32768 : v[15:0];
    endfunction
    function signed [15:0] sat16t(input signed [24:0] v);          // the chip's ONE rail
        sat16t = (v > 25'sd32767) ? 16'sd32767 : (v < -25'sd32768) ? -16'sd32768 : v[15:0];
    endfunction
    wire signed [23:0] dq = dacc >>> 15;                           // the drum bus as a Q4.15 word
    wire signed [24:0] tsh = tacc >>> 15;
    // the two buses as they enter the gains. NEGATIVE CONTROL: clipped to 16
    // bits first, which contract 12 says must not happen ("a bus clipped to 16
    // bits before the master gains could not be recovered by lowering them").
`ifdef INJECT_BUG_VOICE_DRUM_CLAMP16
    wire signed [24:0] dmix_m = {{9{dmix[21]}}, ((dmix > 22'sd32767) ? 16'sd32767 :
                                 (dmix < -22'sd32768) ? -16'sd32768 : dmix[15:0])};
    wire signed [24:0] body_m = {{9{body[18]}}, ((body > 19'sd32767) ? 16'sd32767 :
                                 (body < -19'sd32768) ? -16'sd32768 : body[15:0])};
`else
    wire signed [24:0] dmix_m = {{3{dmix[21]}}, dmix};
    wire signed [24:0] body_m = {{6{body[18]}}, body};
`endif
    // NEGATIVE CONTROL: each product floored to Q.15 before the sum -- two
    // floors instead of contract 12's one.
    wire signed [39:0] macc_r = $signed({macc[35:15], 15'b0});
    wire signed [39:0] dacc_r = $signed({dacc[38:15], 15'b0});

    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (i = 0; i < 3; i = i + 1) begin
                inc_tgt[i] <= 0; inc_acc[i] <= 0; wave[i] <= 0; w[i] <= 0;
                phase[i] <= 0; sh[i] <= 5'd15; r[i] <= 0; inc_er[i] <= 0; inc_mod[i] <= 0;
                osc_d1[i] <= 0; osc_d2[i] <= 0;
            end
            // contract 14. The LFSR returns to its SEED, not to zero: an all-zero
            // LFSR is a fixed point and the noise source would never start.
            lfsr <= 31'h7F215FF7;
            px1 <= 0; px2 <= 0; py1 <= 0; py2 <= 0; prl <= 0; pyr <= 0;
            nz_audio <= 0; nz_mod <= 0; nz_pink <= 0; nx0_r <= 0;
            mod_sig <= 0; naive3 <= 0; amt <= 0; octp <= 0; octf <= 0;
            mant_p <= 17'd32768; mant_f <= 17'd32768; shf_p <= 5'd15; shf_f <= 5'd15;
            wn <= 0; nsel <= 0; mroute <= 0; mmix <= 0; mwheel <= 0; mpd <= 0; mfd <= 0;
            bacc <= 0; facc <= 0; racc <= 0; pnacc <= 0; shk <= 0; mixacc_shk <= 0;
            ep0 <= 0; ep1 <= 0;
            a_inc_a <= 0; d_dec_a <= 0; sus_a <= 0; rate_a <= 0; a_inc_f <= 0; d_dec_f <= 0; sus_f <= 0; rate_f <= 0;
            gate <= 0; glide <= 0; vol <= 0; dvol <= 0; bvol <= 0; dfilt <= 0; cut_lo <= 0; cut_hi <= 0; track_hz <= 0;
            k <= 0; gain <= 0; ogain <= 0; dcut <= 0; dk <= 0; dgain <= 0; dogain <= 0;
            level_a <= 0; level_f <= 0; seg_a <= 0; seg_f <= 0;
            state <= S_IDLE; kk <= 0; win <= 0; c_pp <= 0; c_ps <= 0; mixacc <= 0; y_seen <= 0; d_seen <= 0;
            g0 <= 0; g1 <= 0; kc0 <= 0; kc1 <= 0; kd <= 0; pacc <= 0; dacc <= 0; macc <= 0; tacc <= 0; d19 <= 0;
            ma <= 0; mb <= 0; div_start <= 0; div_inc <= 0; lad_sv <= 0; lad_ch <= 0; g <= 0; g2 <= 0; k_eff2 <= 0; dx <= 0;
            sample <= 0; sample_valid <= 0; mixed <= 0; ae <= 0; fe <= 0; cut <= 0; k_eff <= 0; y19 <= 0;
        end else begin
            sample_valid <= 1'b0; div_start <= 1'b0; lad_sv <= 1'b0;
            if (lad_yv && !lad_ych) begin y19 <= lad_y; y_seen <= 1'b1; end
            if (lad_yv &&  lad_ych) begin d19 <= lad_y; d_seen <= 1'b1; end
            case (state)
                // ---- 0. the noise board (6.10), then the modulation path (6.9) ----
                S_IDLE: if (go) begin
                    nx0_r <= nx0; lfsr <= {lfsr[14:0], nbits};
                    ma <= PB0; mb <= {{5{nx0[15]}}, nx0};
                    kk <= 2'd0; mixacc <= 0; state <= S_NZ0;
                end
                S_NZ0: begin bacc <= mr;            ma <= PB1; mb <= {{5{px1[15]}}, px1}; state <= S_NZ1; end
                S_NZ1: begin bacc <= bacc + mr;     ma <= PB2; mb <= {{5{px2[15]}}, px2}; state <= S_NZ2; end
                S_NZ2: begin bacc <= bacc + mr;     ma <= PA0; mb <= {5'b0, py1[15:0]};   state <= S_NZ3; end
                S_NZ3: begin facc <= mr;            ma <= PA0; mb <= {{5{py1[31]}}, py1[31:16]}; state <= S_NZ4; end
                S_NZ4: begin facc <= facc + (mr <<< 16); ma <= PA1; mb <= {5'b0, py2[15:0]}; state <= S_NZ5; end
                S_NZ5: begin facc <= facc + mr;     ma <= PA1; mb <= {{5{py2[31]}}, py2[31:16]}; state <= S_NZ6; end
                S_NZ6: begin facc <= facc + (mr <<< 16); state <= S_NZ7; end
                S_NZ7: begin
                    px2 <= px1; px1 <= nx0_r; py2 <= py1; py1 <= py0; pyr <= py0;
                    nz_pink <= pink_w;
`ifdef INJECT_BUG_VOICE_NOISE_SEL
                    nz_audio <= white_w;                          // NEGATIVE CONTROL: the selector
`else                                                             //   stuck on white (2.5)
                    nz_audio <= nsel ? pink_w : white_w;
`endif
                    ma <= {{9{mod_sig[15]}}, mod_sig}; mb <= {5'b0, mwheel};
                    state <= S_MD0;
                end
                S_MD0: begin amt <= amt_n; ma <= {{9{amt_n[15]}}, amt_n}; mb <= {5'b0, mpd}; state <= S_MD1; end
                S_MD1: begin octp <= mroute[0] ? oct_n : 16'sd0;
                             ma <= {{9{amt[15]}}, amt}; mb <= {5'b0, mfd}; state <= S_MD2; end
                S_MD2: begin octf <= mroute[1] ? oct_n : 16'sd0; state <= S_EP0; end
                S_EP0: begin ep0 <= erd; state <= S_EP1; end
                S_EP1: begin ep1 <= erd; state <= S_EP2; end
                S_EP2: begin ma <= {9'b0, ep1 - ep0}; mb <= {15'b0, efrc}; state <= S_EP3; end
                S_EP3: begin mant_p <= mant_n; shf_p <= shf_n; state <= S_EF0; end
                S_EF0: begin ep0 <= erd; state <= S_EF1B; end
                S_EF1B:begin ep1 <= erd; state <= S_EF2; end
                S_EF2: begin ma <= {9'b0, ep1 - ep0}; mb <= {15'b0, efrc}; state <= S_EF3; end
                S_EF3: begin mant_f <= mant_n; shf_f <= shf_n; kk <= 2'd0; state <= S_IM0; end
                // the increment each oscillator RUNS on: only oscillators 1 and 2
                // unconditionally -- SW2 takes oscillator 3 off the modulation bus (M1)
                S_IM0: begin
                    if (mroute[0] && ((kk != 2'd2) || mroute[2])) begin
                        ma <= {1'b0, inc_acc[kk][31:8]}; mb <= {4'b0, mant_p}; state <= S_IM1;
                    end else begin
                        inc_mod[kk] <= inc_acc[kk][31:8];
                        if (kk == 2'd2) begin kk <= 2'd0; state <= S_RCHK; end
                        else kk <= kk + 2'd1;
                    end
                end
                S_IM1: begin
                    inc_mod[kk] <= inc_m_n;
                    if (kk == 2'd2) begin kk <= 2'd0; state <= S_RCHK; end
                    else begin kk <= kk + 2'd1; state <= S_IM0; end
                end
                // ---- 1. reciprocals ----
                S_RCHK: begin
                    if (inc != inc_er[kk]) begin
                        div_start <= 1'b1; div_inc <= inc; state <= S_RWAIT;
                    end else if (kk == 2'd2) begin
                        state <= S_WIN; kk <= 2'd0; win <= 2'd0; c_pp <= 0; c_ps <= 0;
                    end else kk <= kk + 2'd1;
                end
                S_RWAIT: if (div_done) begin
                    sh[kk] <= div_sh; r[kk] <= div_r; inc_er[kk] <= inc;
                    if (kk == 2'd2) begin state <= S_WIN; kk <= 2'd0; win <= 2'd0; c_pp <= 0; c_ps <= 0; end
                    else begin kk <= kk + 2'd1; state <= S_RCHK; end
                end
                // ---- 2. oscillators ----
                S_WIN: begin
                    if (!blep) state <= S_MIX;
                    else if (!active) begin
                        if (last_win) state <= is_shark ? S_SK0 : S_MIX; else win <= win + 2'd1;
                    end else begin
                        ma <= {9'b0, p}; mb <= {5'b0, r[kk]}; state <= S_W1;
                    end
                end
                S_W1: begin ma <= {8'b0, s}; mb <= {4'b0, s}; state <= S_W2; end
                S_W2: begin
                    case (win)
                        2'd0: c_pp <= -$signed({1'b0, c});
                        2'd1: c_pp <=  $signed({1'b0, c});
                        2'd2: c_ps <= -$signed({1'b0, c});
                        default: c_ps <= $signed({1'b0, c});
                    endcase
                    if (last_win) state <= is_shark ? S_SK0 : S_MIX;
                    else begin win <= win + 2'd1; state <= S_WIN; end
                end
                // the shark-tooth: the switch mixes the two BUFFERED waveform
                // outputs through R030 and R031 (W3), so the saw arrives already
                // band-limited and the mix is 10/57 of it plus 47/57 triangle.
                S_SK0: begin ma <= {{9{sawc[15]}}, sawc}; mb <= SHK_SAW[20:0]; state <= S_SK1; end
`ifdef INJECT_BUG_VOICE_SHARK_MIX
                S_SK1: begin mixacc_shk <= mr; ma <= {{9{triv[15]}}, triv};
                             mb <= SHK_SAW[20:0]; state <= S_SK2; end   // NEGATIVE CONTROL: R030 = R031
`else
                S_SK1: begin mixacc_shk <= mr; ma <= {{9{triv[15]}}, triv};
                             mb <= SHK_TRI[20:0]; state <= S_SK2; end
`endif
                S_SK2: begin shk <= shk_n; state <= S_MIX; end
                S_MIX: begin
`ifdef VOICE_OSC_2X
                    state <= S_OSCWAIT;
`else
                    ma <= {{9{osc[15]}}, osc}; mb <= {5'b0, w[kk]};
                    osc_d2[kk] <= osc_d1[kk];
                    osc_d1[kk] <= osc_raw_clamped;
                    phase[kk] <= ph + inc;                                    // step 9: advance
                    if (kk == 2'd2) naive3 <= naive;                          // the modulation tap (M5)
                    state <= S_ACC;
`endif
                end
                S_OSCWAIT: begin
                    if (osc2_valid) begin
                        ma <= {{9{osc2_sample[15]}}, osc2_sample}; mb <= {5'b0, w[kk]};
                        phase[kk] <= ph + inc;
                        if (kk == 2'd2) naive3 <= naive;
                        state <= S_ACC;
                    end
                end
                S_ACC: begin
                    mixacc <= mixacc + {{3{mr[31]}}, mr[31:0]};
                    if (kk == 2'd2) begin
                        ma <= {{9{nz_audio[15]}}, nz_audio}; mb <= {5'b0, wn}; state <= S_NMIX;
                    end else begin kk <= kk + 2'd1; win <= 2'd0; c_pp <= 0; c_ps <= 0; state <= S_WIN; end
                end
                S_NMIX: begin                                                 // the mixer's fourth source
                    mixacc <= mixacc + {{3{mr[31]}}, mr[31:0]};
                    state <= S_ENV;
                end
                // ---- 3, 4. envelope outputs, cutoff ----
                S_ENV: begin
                    ae <= level_a[23:9]; fe <= level_f[23:9];
                    ma <= {{8{span[16]}}, span}; mb <= {6'b0, level_f[23:9]};
                    state <= S_CUT1;
                end
                S_CUT1: begin ma <= {10'b0, cut_n}; mb <= {4'b0, mant_f}; state <= S_CUTM; end
                S_CUTM: begin cut <= cut_m; state <= S_ROM0; end             // 5b: filter modulation
                S_ROM0: begin g0 <= grd; kc0 <= krd; state <= S_ROM1; end
                S_ROM1: begin g1 <= grd; kc1 <= krd; state <= S_ROM2; end
                S_ROM2: begin
                    ma <= {9'b0, g1 - g0}; mb <= {13'b0, gf};
                    kd <= $signed({1'b0, kc1}) - $signed({1'b0, kc0});
                    state <= S_ROM3;
                end
                S_ROM3: begin
                    g  <= g0 + mr[23:8];
                    ma <= {{8{kd[16]}}, kd}; mb <= {11'b0, kf};
                    state <= S_KEFF0;
                end
                S_KEFF0: begin ma <= {8'b0, k}; mb <= {5'b0, kc_n}; state <= S_KEFF1; end
`ifdef INJECT_BUG_VOICE_KEFF
                S_KEFF1: begin k_eff <= k; state <= S_LGO; end                // NEGATIVE CONTROL: no compensation
`else                                                                         //   (rev 1): dies above ~3 kHz
                S_KEFF1: begin k_eff <= mr[32] ? 18'h1FFFF : mr[31:15]; state <= S_LGO; end
`endif
                // ---- 5. the voice's ladder context starts ----
                S_LGO: begin
`ifdef INJECT_BUG_VOICE_MIX_SAT
                    mixed <= msh[15:0];                                       // NEGATIVE CONTROL: the mixer wraps
`else
                    mixed <= sat16m(msh);
`endif
                    lad_sv <= 1'b1; lad_ch <= 1'b0; y_seen <= 1'b0; d_seen <= 1'b0;
                    ma <= {1'b0, level_a}; mb <= {5'b0, rate_a};
                    state <= S_EA1;
                end
                // ---- step 9 while the ladder runs: envelope updates, glide slews ----
                S_EA1: begin
                    {seg_a, level_a} <= env_update(gate, seg_a, level_a, a_inc_a, d_dec_a, sus_a, mr[39:16]);
                    ma <= {1'b0, level_f}; mb <= {5'b0, rate_f};
                    state <= S_EF1;
                end
                S_EF1: begin
                    {seg_f, level_f} <= env_update(gate, seg_f, level_f, a_inc_f, d_dec_f, sus_f, mr[39:16]);
                    kk <= 2'd0; state <= S_SL0;
                end
                S_SL0: begin
                    if (inc_acc[kk] == tgt) begin
                        if (kk == 2'd2) begin ma <= RED_G; mb <= {5'b0, rdif[15:0]}; state <= S_RD0; end else kk <= kk + 2'd1;
                    end else if (glide == 24'd0) begin
                        inc_acc[kk] <= tgt;
                        if (kk == 2'd2) begin ma <= RED_G; mb <= {5'b0, rdif[15:0]}; state <= S_RD0; end else kk <= kk + 2'd1;
                    end else begin
                        ma <= {1'b0, glide}; mb <= {5'b0, inc_acc[kk][15:0]}; state <= S_SL1;
                    end
                end
                S_SL1: begin pacc <= mr[39:0]; ma <= {1'b0, glide}; mb <= {5'b0, inc_acc[kk][31:16]}; state <= S_SL2; end
                S_SL2: begin
                    if (tgt > inc_acc[kk]) inc_acc[kk] <= (acc_up > $signed({2'b0, tgt})) ? tgt : acc_up[31:0];
                    else                   inc_acc[kk] <= (acc_dn < $signed({2'b0, tgt})) ? tgt : acc_dn[31:0];
                    if (kk == 2'd2) begin ma <= RED_G; mb <= {5'b0, rdif[15:0]}; state <= S_RD0; end
                    else begin kk <= kk + 2'd1; state <= S_SL0; end
                end
                // ---- still in the ladder's shadow: red noise, then the modulation mix ----
                // Red is pink through one pole at 106 Hz (N5) and is ONLY ever a
                // modulation source, so it can be computed after the mixer has run.
                S_RD0: begin racc <= mr; ma <= RED_G; mb <= {{4{rdif[32]}}, rdif[32:16]}; state <= S_RD1; end
                S_RD1: begin racc <= racc + (mr <<< 16); state <= S_RD2; end
                S_RD2: begin prl <= prl_n; ma <= RED_GAIN; mb <= {5'b0, prl_n[15:0]}; state <= S_RD3; end
                S_RD3: begin racc <= mr; ma <= RED_GAIN; mb <= {{5{prl[31]}}, prl[31:16]}; state <= S_RD4; end
                S_RD4: begin racc <= racc + (mr <<< 16); state <= S_RD5; end
                S_RD5: begin
                    nz_mod <= nsel ? red_w : nz_pink;                        // 2.5: pink or RED for mod
                    ma <= {{9{naive3[15]}}, naive3}; mb <= {5'b0, (16'd32768 - mmix_c)};
                    state <= S_PN0;
                end
                // the MOD MIX pot is a PAN (M4): the two weights sum to 32768
                S_PN0: begin pnacc <= mr; ma <= {{9{nz_mod[15]}}, nz_mod}; mb <= {5'b0, mmix_c}; state <= S_PN1; end
`ifdef INJECT_BUG_VOICE_MOD_NODELAY
                S_PN1: begin mod_sig <= naive3; state <= S_DC0; end          // NEGATIVE CONTROL: the pan
`else                                                                        //   and the register both gone
                S_PN1: begin mod_sig <= mpan_n; state <= S_DC0; end
`endif
                // ---- the drum filter's coefficients (same ROMs, DCUT; DK x kc) ----
                S_DC0: begin g0 <= grd; kc0 <= krd; state <= S_DC1; end
                S_DC1: begin g1 <= grd; kc1 <= krd; state <= S_DC2; end
                S_DC2: begin
                    ma <= {9'b0, g1 - g0}; mb <= {13'b0, gf};
                    kd <= $signed({1'b0, kc1}) - $signed({1'b0, kc0});
                    state <= S_DC3;
                end
                S_DC3: begin g2 <= g0 + mr[23:8]; ma <= {{8{kd[16]}}, kd}; mb <= {11'b0, kf}; state <= S_DC4; end
                S_DC4: begin ma <= {8'b0, dk}; mb <= {5'b0, kc_n}; state <= S_DC5; end
                S_DC5: begin k_eff2 <= mr[32] ? 18'h1FFFF : mr[31:15]; state <= S_YWAIT; end
                // ---- 6. the voice context done and the drum buses in: the drum gains ----
                // Both buses are needed whatever ROUTE says, so the wait is on
                // drum_done either way (ARCHITECTURE.md 4.4: wait, do not assume).
                S_YWAIT: if (y_seen && drum_done) begin
                    ma <= dmix_m; mb <= {5'b0, dvol}; state <= S_DM1;
                end
                S_DM1: begin dacc <= $signed(mr[38:0]); ma <= body_m; mb <= {5'b0, bvol}; state <= S_DM2; end
                S_DM2: begin dacc <= dacc + $signed(mr[38:0]); state <= S_DFLT; end
                // the drum filter runs on sat16 of the GAIN-SCALED drum bus
                // (ARCHITECTURE.md 4.1: its input is sat16(drum_bus), and its
                // rail is the designed mixer-overload rail); the VCA multiply
                // starts in the same cycle, in the filter's shadow.
                S_DFLT: begin
                    if (dfilt) begin dx <= sat16q(dq); lad_sv <= 1'b1; lad_ch <= 1'b1; end
                    ma <= {{6{y19[18]}}, y19}; mb <= {6'b0, ae};
                    state <= S_VCA1;
                end
                S_VCA1: begin ma <= mr[39:15]; mb <= {5'b0, vol}; state <= S_VCA2; end
                S_VCA2: begin macc <= $signed(mr[35:0]); state <= S_DWAIT; end
                // ---- 7. the master mix: one exact sum, one shift, one rail ----
                S_DWAIT: begin
                    if (dfilt) begin
                        if (d_seen) begin
                            tacc <= macc + $signed({{6{d19[18]}}, d19, 15'b0});
                            state <= S_OUT2;
                        end
                    end else begin
`ifdef INJECT_BUG_VOICE_MASTER_PRESHIFT
                        tacc <= macc_r + dacc_r;                              // NEGATIVE CONTROL: two floors
`else
                        tacc <= macc + dacc;
`endif
                        state <= S_OUT2;
                    end
                end
                S_OUT2: begin
`ifdef INJECT_BUG_VOICE_OUT_SAT
                    sample <= tsh[15:0];                                       // NEGATIVE CONTROL: no rail
`else
                    sample <= sat16t(tsh);
`endif
                    sample_valid <= 1'b1; state <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase

            // ---- the register write port (DR 0007 section 3); applied while idle ----
            if (wr_valid) case (wr_addr)
                8'h00, 8'h01, 8'h02: begin
                    inc_tgt[wr_addr[1:0]] <= wr_data[23:0];
                    if (wr_flag || glide == 24'd0) inc_acc[wr_addr[1:0]] <= {wr_data[23:0], 8'b0};
                end
                8'h04, 8'h05, 8'h06: wave[wr_addr[1:0]] <= wr_data[3:0];
                8'h08, 8'h09, 8'h0A: w[wr_addr[1:0]] <= wr_data[15:0];
                8'h0B: wn <= wr_data[15:0];                                    // the noise source (6.10)
                8'h0C: glide <= wr_data[23:0];
                8'h0D: vol <= wr_data[15:0];
                8'h0E: dvol <= wr_data[15:0];
                8'h0F: dfilt <= wr_data[0];
                8'h10: a_inc_a <= wr_data[23:0];  8'h11: d_dec_a <= wr_data[23:0];  8'h12: sus_a <= wr_data[23:0];  8'h13: rate_a <= wr_data[15:0];
                8'h14: a_inc_f <= wr_data[23:0];  8'h15: d_dec_f <= wr_data[23:0];  8'h16: sus_f <= wr_data[23:0];  8'h17: rate_f <= wr_data[15:0];
                8'h18: cut_lo <= wr_data[15:0];  8'h19: cut_hi <= wr_data[15:0];  8'h1A: track_hz <= wr_data[15:0];
                8'h1B: nsel <= wr_data[0];                                     // white/pink selector (2.5)
                8'h1C: k <= wr_data[16:0];  8'h1D: gain <= wr_data[19:0];  8'h1E: ogain <= wr_data[19:0];
                8'h1F: mroute <= wr_data[2:0];                                 // OSC MOD / FILT MOD / OSC-3
                8'h24: mmix <= wr_data[15:0];   8'h25: mwheel <= wr_data[15:0];
                8'h26: mpd <= wr_data[15:0];    8'h27: mfd <= wr_data[15:0];
                8'h20: begin gate <= 1'b1; seg_a <= 2'd0; seg_f <= 2'd0;       // GATE_ON: ATTACK from the
`ifdef INJECT_BUG_VOICE_TRIG_RESET                                             //   current level (8.5)
                       level_a <= 24'd0; level_f <= 24'd0;                     // NEGATIVE CONTROL: the reset-to-
`endif                                                                         //   zero envelope DR 0003 rejects
                end
                8'h21: gate <= 1'b0;                                           // GATE_OFF
                8'h22: begin seg_a <= 2'd0; seg_f <= 2'd0;                     // TRIG
`ifdef INJECT_BUG_VOICE_TRIG_RESET
                       level_a <= 24'd0; level_f <= 24'd0;
`endif
                end
                8'h28: dcut <= wr_data[15:0];  8'h29: dk <= wr_data[16:0];
                8'h2A: dgain <= wr_data[19:0]; 8'h2B: dogain <= wr_data[19:0];
                8'h2C: bvol <= wr_data[15:0];                                  // the body bus's gain (12)
                default: ;                                                     // RESET is rst_n; NOP, reserved, drums
            endcase
        end
    end
endmodule
`default_nettype wire

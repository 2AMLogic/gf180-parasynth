# Monosynth Voice — Numeric Contract

**Revision 11 — 2026-09-19 — status: PROPOSED. Not ratified.**

This document is a proposal for the complete, bit-exact specification of the
gf180-parasynth voice: three band-limited oscillators with an on-chip glide, a
saturating mixer, Huovilainen's nonlinear ladder with a resonance-compensation
ROM, two integer ADSRs and a VCA — and, since revision 5, the drum section: a
TR-808-shaped set of eleven stops whose bodies and filters are the modal
resonator bank — producing one signed 16-bit sample per frame. It is written
from the committed reference model and claims nothing the model does not do.
It becomes the specification RTL is verified against only when ratified
through the two-key process this fleet uses; until then it is revision 11,
proposed, and the status line above must not be read as
anything else (the rule is gf180-drone-fc DR-0005's: the status field must not
claim ratification before that act has happened).

**The model wins.** The normative text below is derived from these files, at
the commit this revision was written against, and where prose and code could be
read differently the code is what "bit-exact" means:

| file | what it is the specification of |
|---|---|
| `model/voice_fx.py` | the voice: oscillators, glide, PolyBLEP, mixer, envelopes, cutoff path, resonance compensation, VCA and output (sections 6–10, 12); `KeyHost`, the reference host (5.6, informative) |
| `model/fixed.py` | `LadderFx`, the ladder filter (section 11) |
| `audition/dsp.py` | the tables the voice imports: `phase_inc`, `note_hz`, `_QUARTER` (Appendices A, B) |
| `model/drums_fx.py` | `DrumsFx`, the drum section: stops, envelopes, sources, paths, the two buses, the output stage `output_fx`, the register map (section 15; 12); the reference kit `kit_808` (Appendix G, informative) |
| `model/modal_fixed.py` | `ModalFx`, the modal resonator bank — the drum section's bodies and filters (15.6) |

Their tests (`model/test_voice_fx.py`, `model/test_fixed.py`,
`model/test_modal_fixed.py`, `model/test_drums_fx.py`, `spec/reference/test_tables.py`) lock the sizing decisions; the RTL
sketches `rtl-sketch/ladder_dp.v`, `rtl-sketch/modal_dp.v` and
`rtl-sketch/drum_kit.v` are bit-exact against `LadderFx`, `ModalFx` and
`DrumsFx` respectively
(`rtl-sketch/test_rtl.py`). Every table in the appendices is regenerated from
the model by `spec/reference/gen_tables.py`, and `gen_tables.py --check` fails
if any hash or table image in this document is no longer the model's.

If two readers could interpret a sentence differently, that is a defect in
this document. File it; do not resolve it by picking one reading. Any change to
a normative statement bumps the revision number.

Words: **MUST** is normative. *Informative* paragraphs explain, motivate or
show a host-side derivation and carry no obligation on the implementation.
**OPEN** marks something this revision deliberately does not specify; every
OPEN item is collected in section 17. Numbers written `0x..` are hexadecimal.

---

## 1. What the block is

A monophonic — or, with a host that assigns held keys to oscillators, a
three-voice paraphonic — subtractive voice. Three phase-accumulator
oscillators, each with an on-chip glide and a PolyBLEP correction on its
discontinuous shapes, are mixed with saturation, filtered by a four-pole
nonlinear ladder whose cutoff is driven by a second envelope plus keyboard
tracking and whose resonance is compensated by cutoff, scaled by an amplitude
envelope, then by a host volume. A host writes the voice's control registers
over a serial link (SPI register writes, section 5.4, DR 0007); the host owns
all musical time. One sample leaves per frame, as I2S.

```
                 control image (section 5), applied at frame boundaries
                          │
   ┌──────────────────────┼─────────────────────────────────────────────────┐
   │ osc 0: glide ─► phase acc ─► wave ─► PolyBLEP ─► ×w0 ─┐                                      │
   │ osc 1:   "          "           "         "      ×w1 ─┼─► Σ ─► sat16 ─► ladder ─► sat19 ─► × amp env ─► × vol ─► sat16 ─► s16
   │ osc 2:   "          "           "         "      ×w2 ─┘   (mixer)     (§10,11)      (§8,9)         (§12)
   │                                cutoff = clamp(lo + span·filt env + track) ─► g ROM, kc ROM ─┘      │
   └──────────────────────────────────────────────────────────────────────────┘
```

The drum section (section 15) runs beside the voice on the same frames and
joins it at the output stage: `sample = sat16((v·vol + dmix·dvol +
body·bvol) >> 15)` (12), where `dmix` and `body` are its two buses.

The block's observable behaviour is exactly two things: the sequence of output
samples, and how that sequence depends on the sequence of control writes and
the frames in which they arrived. Everything below defines those two things.

The order of the chain is the Minimoog's — mixer, filter, VCA, volume — and
not `engines.mono_note`'s as auditioned, which put the amplitude envelope
before the filter (DR 0005): a note ends when its VCA closes whatever the
filter is doing, and the filter is driven at the mixer's level whatever the
envelope.

---

## 2. Fixed numbers

| Quantity | Value |
|---|---|
| Nominal frame rate | 48 000 frames/s (`dsp.SR`) |
| Core clock | 12.288 MHz = 256 × 48 000 |
| Cycles per frame | 256 |
| Output sample | signed 16-bit two's complement, mono, one per frame, saturated (section 12) |
| Oscillators | 3, numbered 0..2 |
| Phase accumulator / increment | 24-bit unsigned per oscillator (`dsp.PHASE_BITS`) |
| Waveform sample | Q1.15, signed 16-bit |
| PolyBLEP mantissa / reciprocal | 16 bits / 16 bits (`MANT_BITS`, `RECIP_BITS`) |
| Mixer weight | Q0.15, 16-bit unsigned register; a weight of 32768 is 1.0 |
| Envelope level | 24-bit unsigned, Q0.24 (`ENV_BITS`) |
| Voice envelope release rate | 24-bit code: Q0.16 mantissa plus 8-bit binary exponent (`RATE_Q`, `RATE_EXP_BITS`) |
| Cutoff | integer Hz, clamped to 30..21600 (`CUT_MIN`, `CUT_MAX`) |
| Cutoff → g ROM | 128 entries + 1 guard, Q0.16, linear interpolation (`GROM_BITS` = 7) |
| Cutoff → kc ROM | 32 entries + 1 guard, unsigned Q1.15, linear interpolation (`KROM_BITS` = 5) — DR 0006 |
| Ladder state | 24-bit signed, 20 fraction bits, in units of 2·Vt (`LADDER_CFG`) |
| Ladder coefficients | g Q0.16 (16 bits); k_eff Q3.14 (17 bits, per frame from `k` and `kc`); gain, ogain Q4.16 (20 bits) |
| Ladder output word | Q4.15 signed, 19 bits, saturated (`LADDER_OUT_BITS`) — DR 0005 |
| Ladder tanh table | 16 entries, edge-sampled, interpolated |
| Ladder oversampling | 2 passes per frame |
| Volume | `vol`, unsigned Q0.15, 16 bits; the reference host writes 14746 = 0.45 (`VOL_REF`) — DR 0005 |
| Tuning | A4 (MIDI note 69) = 440 Hz |
| Glide | `glide`, unsigned Q0.24, 24 bits, the ratio per frame minus 1; increment accumulator Q24.8 (`GLIDE_BITS`, `INC_FRAC`); the reference host writes 2692 = 90 ms per octave (`GLIDE_REF_S`) — DR 0004 |
| Drum stops / accents | 11 stops, edge-triggered; accent Q0.15, 16 bits per stop (15.2) — DR 0008 |
| Drum envelopes | 18; level 24-bit unsigned Q0.24, rate Q0.16, hold 8 bits, bursts 2, period 9, frame counter 11 (15.3) |
| Drum paths | 23; source 5 bits, two envelope indices of 5 bits, nonlinearity 2 bits, attenuation 3, destination 5 (15.5) |
| Drum sources | a 31-bit LFSR (16 bits per frame), six 24-bit square-wave phase accumulators, a pulse (15.4) |
| Modal bank | 12 modes, the first 6 with a selectable numerator; coefficients Q2.24 signed (26 bits); amp Q0.16; state 28 bits, 15 fraction; excitation 21 bits; output Q4.15, 19 bits (15.6) |
| Drum buses / gains | `dmix` 22 bits (23 paths × 17 bits; 21 at revision 8's 16 paths), `body` 19 bits; `dvol`, `bvol` unsigned Q0.15, 16 bits (12) |

---

## 3. Arithmetic conventions

1. All signed quantities are two's complement.
2. `x >> k` on a signed value is an **arithmetic** shift: `floor(x / 2^k)`,
   rounding toward −∞. `−1 >> 15 = −1`; `−7123 >> 15 = −1`.
3. `x >> k` on an unsigned value is a logical shift; the result is the same.
4. `x << k` is multiplication by 2^k, computed exactly (widen as needed).
5. Products and sums MUST be computed exactly and then shifted or saturated
   as stated. Nothing in this document truncates an intermediate. Every
   product fits in 64 signed bits; the widest is the glide's 32 × 24 (6.7);
   the ladder's is 24 × 20.
6. Only the phase accumulators wrap (modulo 2^24). Every other addition is
   either provably in range or explicitly saturated. The saturation points are
   listed in section 12.
7. `sat16(v)` clamps to −32768..+32767. `sat24(v)` clamps to −8388608..+8388607
   (`fixed.sat(v, 24)`).
8. `floor(a / b)` for integers is Python's `//`: rounding toward −∞ for a
   negative dividend. No run-time operation of this revision divides; the
   floors at run time are the arithmetic shifts of rule 2 (the glide slew's
   operands are non-negative, 6.7). Division appears only in the note-on
   reciprocal (6.6.1), with a non-negative dividend.
9. `round(x)` in table derivations is Python's `round()`: round half to even.
   No table entry lies within 2.7 × 10⁻³ of a tie (the smallest margin is in
   NOTE_INC; `gen_tables.py` does not re-derive the tables, it evaluates the
   model's functions), so any correct evaluation reproduces them and
   round-half-up gives the identical tables. No rounding other than the floors
   above happens at run time.

---

## 4. Frames and the clock

### 4.1 Definition

A **frame** is the production of one output sample. Frames are numbered from
0; frame 0 is the first frame after reset is released, and frame f produces
sample f. Nominally a frame lasts 1/48 000 s. The core clock is divided so that
a **frame tick** occurs once every 256 cycles; the cycle in which the f-th tick
is asserted is **cycle 0 of frame f**, and cycle 255 of frame f is the cycle
before tick f+1.

### 4.2 What happens in a frame

Sample f is a pure function of the register state at the start of frame f
(after step 1). Conceptually, in this order, per frame:

1. **Apply control.** Every control write that became complete during frame
   f−1 (section 4.3) is applied, in order of completion.
2. **Oscillators** (section 6): for each oscillator k, `osc_k` from `phase_k`
   *before* it is advanced, `inc_k = inc_acc_k >> 8`, and the reciprocal
   state `(e_k, r_k)`.
3. **Mixer** (section 7): `mixed = sat16((Σ osc_k · w_k) >> 15)`.
4. **Envelopes** (section 8): `ae = level_amp >> 9`, `fe = level_filt >> 9`,
   both from the levels *before* this frame's update, after a GATE_ON or
   TRIG applied in step 1 has set the segment (8.3).
5. **Cutoff** (section 10): `cut = clamp(cut_lo + ((cut_hi − cut_lo) · fe >> 15) + track_hz, 30, 21600)`;
   `g = G(cut)` and `kc = KC(cut)` from the ROMs; `k_eff = min((k · kc) >> 15, 2^17 − 1)`.
6. **Ladder** (section 11): two oversampling passes on `mixed` with `g,
   k_eff, gain, ogain`, producing `y`, saturated to 19 bits.
7. **Amplitude** (section 9): `v = (y · ae) >> 15`.
8. **Output** (section 12): `sample_f = sat16((v · vol + dmix · dvol + body · bvol) >> 15)`,
   with `dmix` and `body` the drum section's buses for this frame (15.2);
   `sat16((v · vol) >> 15)` when the drum gains are 0.
9. **Advance.** For each oscillator `phase_k ← (phase_k + inc_k) mod 2^24`,
   then the glide slew of 6.7 moves `inc_acc_k` toward its target. Each
   envelope's level is updated by the rule of 8.3. The ladder's state was
   already advanced in step 6 (it is recursive; its state after step 6 is the
   state for frame f+1).

An implementation may schedule this across the 256 cycles however it likes,
provided the sample sequence is identical. In particular a control write
received during frame f MUST NOT affect sample f.

### 4.3 "Immediately before frame f": control timing

A control write is *received during frame f* if the cycle in which the core
accepts its last unit (byte, or the last bit of a packet — the unit is the
physical layer's, section 5.4) is cycle c with `tick_f ≤ c < tick_{f+1}`; a
unit accepted in the tick cycle itself belongs to the frame that starts in
that cycle. A write is *complete during frame f* when its last unit was
received during frame f.

**Every write complete during frame f MUST be applied at the start of frame
f+1, in order of completion, before sample f+1 is computed, and MUST NOT
affect sample f.** "Immediately before frame f" therefore means: complete
during frame f−1. Each write is applied exactly once and atomically — all the
registers it names change together and no sample is computed from a partially
applied write. A packet that carries several registers is one write.

### 4.4 Cycle budget

An implementation MUST finish steps 1–9 and the drum section's frame (15.2)
within 256 cycles of the tick. The measured sequenced ladder
(`rtl-sketch/ladder_dp.v`) takes 24 of them per frame including both
oversampling passes, fixed, worst case equal to mean (`rtl-sketch/tb_cycles.v`);
the drum section (`rtl-sketch/drum_kit.v`) takes 85, fixed, from its tick to
`body_valid` (`rtl-sketch/tb_drums.v`: 48 in the drum datapath and 39 in the
bank, one clock overlapped). Nothing in the sample sequence depends on the
clock frequency; an implementation that needs fewer cycles MAY be clocked
slower. *Informative:* revision 3 adds six multiplies per frame to the front
end — three glide slews, the kc interpolation, `k · kc` and the VCA — all on
a shared multiplier; the output stage of 12 adds two more. The whole voice as
sequenced in `rtl-sketch/voice_dp.v` (one multiplier, one divider, the ladder
inside) measures 150 cycles in its worst frame — three reciprocals, three
squares, the drum filter of `docs/ARCHITECTURE.md` on — and 66 in a steady one
(`tb_synth_top.v`); that figure is the chip with the *placeholder* drum
sources, not the drum section of 15 (17.23).

---

## 5. Control interface

This revision specifies the **semantics** of control — which registers exist,
what each does, and when a write takes effect — and, since revision 4, the
physical layer (5.4, DR 0007): an SPI slave carrying one register write per
32-bit transaction, applied at the next frame tick. The product's host (DR
0002) is a microcontroller translating USB-MIDI into those writes.

### 5.1 The control image

The voice's control state is the following registers. "Width" is the register
width an implementation MUST hold; "model" says where the value comes from in
the reference model, whose `VoiceFx.note_on` computes the whole image from a
patch's physical units in float — the one place float is allowed, and in the
product the host's job (5.5).

| Register | Width | Per | Meaning | Model |
|---|---:|---|---|---|
| `inc_tgt[k]` | 24 u | osc | phase-increment target; `inc[k] = inc_acc[k] >> 8` is what the phase accumulator adds (6.7) | `OscFx.inc_tgt` |
| `wave[k]` | 3 | osc | one of saw, square, pulse25, tri, sine (encoding in 5.2) | `waves[k]` |
| `w[k]` | 16 u | osc | mixer weight, Q0.15 | `weights[k]` |
| `a_inc`, `d_dec`, `sus` | 24 u | env ×2 | attack increment, decay decrement, sustain level, Q0.24 | `AdsrFx.a_inc`, `.d_dec`, `.sus` |
| `rate` | 24 u | env ×2 | release fraction as 16-bit Q0.16 mantissa + 8-bit binary exponent | `AdsrFx.rate` |
| `gate` | 1 | voice | envelope gate (both envelopes) | `VoiceFx.gate` |
| `glide` | 24 u | voice | glide rate, Q0.24, the ratio per frame minus 1; 0 = off (6.7) | `VoiceFx.glide` |
| `vol` | 16 u | voice | output volume, Q0.15 (12) | `VoiceFx.vol` |
| `cut_lo`, `cut_hi`, `track_hz` | 16 u | voice | cutoff floor, ceiling and keyboard-tracking offset, integer Hz | `cut_lo`, `cut_hi`, `track_hz` |
| `k` | 17 u | voice | ladder resonance, 4·res in Q3.14, before the compensation of 10.2 | `LadderFx.regs`, `VoiceFx.k_reg` |
| `gain` | 20 u | voice | ladder input gain, drive·2.6 in Q4.16 | same |
| `ogain` | 20 u | voice | ladder output gain, (1+2·res)/2.6 in Q4.16 | same |

The two envelopes are `amp` (section 9) and `filt` (section 10); each has its
own `a_inc`, `d_dec`, `sus`, `rate`.

**Every conversion of 5.5 clamps its result to the width in this table**
(`voice_fx.REG_BITS`, through `fixed.usat`), so the model never holds a value
a register-limited implementation cannot, and the two cannot differ on any
input a host might send. (Rev 2; this was OPEN 17.7. Where each clamp fires
is recorded in 5.5.) Conversely the model accepts every value every register
can hold — `test_every_legal_register_value_runs` walks the extremes of each
through the per-sample path — so a legal write never makes the model raise
or misbehave.

The widths of `cut_lo`, `cut_hi` and `track_hz` are 16 bits unsigned (DR
0007, closing 17.10): derived from the largest value any audition patch
produces (`track_hz` = 45 158 at MIDI note 127 with `track` = 0.9; `cut_hi`
= 7000), from full keyboard tracking (`track` = 1.0 at note 127 gives
50 175) and from the clamp in section 10, past which larger values change
nothing; and fixed by the write format, which carries 16 bits for them. The
sum in section 10 MUST be computed exactly (19 bits signed) before the clamp. `k`, `gain` and
`ogain` are the RTL's port widths, and the RTL is bit-exact against the model
within them; the model clamps to them (`LadderFx.regs`), the ladder runs on
`k_eff`, which 10.2 saturates to `2^17 − 1`, and at every extreme of all three
and of `g` every pre-saturation value in the ladder stays below 2^27, inside
the datapath width of 11.4.

State registers (not host-writable except by RESET): `phase[k]` (24),
`inc_acc[k]` (32, section 6.7), `e[k]` and `r[k]` (section 6.6.1), `level`
and `seg` per envelope (section 8.1), the ladder's `y[0..3]`, `w[0..3]`,
`d1`, `d2` (section 11.2).

### 5.2 Writes and their semantics

| Write | Effect at the next frame boundary (4.3) |
|---|---|
| SET_INC k, v, jump | `inc_tgt[k] ← v`. If `jump = 1` or `glide = 0`, `inc_acc[k] ← v << 8` at once; otherwise the slew of 6.7 walks it there frame by frame. Whenever `inc_acc[k] >> 8` changes, `(e[k], r[k])` MUST be recomputed by 6.6.1 before it is next used, i.e. before the next sample's PolyBLEP. `phase[k]` is not touched. |
| SET_WAVE k, s | `wave[k] ← s`. Takes effect from the next sample, mid-note. |
| SET_WEIGHT k, v | `w[k] ← v`. |
| SET_ENV e, a_inc/d_dec/sus/rate | the named parameter of envelope e ← v. The envelope update at the end of the frame in which the write was applied already uses it. |
| SET_CUT lo/hi/track | the named cutoff register ← v. |
| SET_LADDER k/gain/ogain | the named coefficient ← v. Held for the whole frame (both passes). |
| SET_GLIDE v | `glide ← v`. |
| SET_VOL v | `vol ← v`. |
| GATE_ON | `gate ← 1`, and for both envelopes `seg ← ATTACK` with `level` unchanged (8.5, DR 0003). Nothing else changes: no phase, no ladder state. |
| TRIG | for both envelopes `seg ← ATTACK` with `level` and `gate` unchanged (8.5): the multi-trigger retrigger while a key is held. |
| GATE_OFF | `gate ← 0`. Both envelopes take the release branch of 8.3 from wherever their level is. |
| RESET | every register of section 14 ← its reset value. |

Each of these is one 48-bit SPI transaction (5.4): a flag bit, six reserved
bits, a page bit, an 8-bit address and 32 data bits,
`{F, 6'b0, SEC, A[7:0], D[31:0]}` MSB first (DR 0007 **revision 2**; revision
1's 32-bit frame could not address the drum image of 15.1 at all, and the
measurement is in that record). `SEC` = 0 is this page. The addresses
(DR 0007 section 3): `INC_TGT[k]` 0x00–0x02 with `F` = jump; `WAVE[k]`
0x04–0x06; `W[k]` 0x08–0x0A; `GLIDE` 0x0C; `VOL` 0x0D; amp envelope
`a_inc, d_dec, sus, rate` 0x10–0x13 and filter envelope 0x14–0x17;
`CUT_LO, CUT_HI, TRACK_HZ` 0x18–0x1A; `K, GAIN, OGAIN` 0x1C–0x1E; `GATE_ON`
0x20, `GATE_OFF` 0x21, `TRIG` 0x22, `RESET` 0x23 (data ignored); `NOP` 0x3F.
`BVOL` is 0x2C and `DVOL` 0x0E (12). A register narrower than 32 bits takes
the low bits of `D`; the rest MUST be zero. `SEC` = 1 selects the drum
section's page, whose map is 15.1's unchanged. **`wave[k]`: 0 saw, 1 square, 2 pulse25, 3 tri, 4 sine, and 5–7 also
sine** (bit 2 set selects sine, so every 3-bit value is defined). The chip
adds registers outside this voice — the drum bus level, the drum routing
and the drum filter, 0x0E, 0x0F, 0x28–0x2B, and the drum section's 0x40–0x7F
— which `docs/ARCHITECTURE.md` and the drum section's own record define.

Any register value is legal; nothing is rejected for range. Consequences of
out-of-range values follow from the formulas (an `inc` ≥ 2^23 is above
Nyquist; `inc` = 0 stalls its oscillator at DC, 6.3; weights summing above
32768 saturate in the mixer; `a_inc` = 0 holds the attack at its current
level forever; `rate` = 0 releases at one LSB per frame).

### 5.3 The unit of work: a sequence of writes to one voice

The model's unit of work is a sequence of writes applied at frame boundaries
to **one continuous voice** — `VoiceFx.play(regs, writes, n)` — from reset or
from whatever state the previous sequence left (DR 0003). A note from reset,
`VoiceFx.note`, is the special case: RESET, the patch registers, SET_INC with
`jump = 1` for each oscillator, SET_CUT track, GATE_ON at frame 0 and
GATE_OFF at `gate_n`. `render_mono_fx` turns a note list into writes through
the reference host `KeyHost` (5.6) and plays them through one voice; it no
longer sums overlapping notes. Reference sequences (section 16) are sequences
of writes.

### 5.4 Physical layer — DR 0007

**SPI, register writes, applied at the frame tick.** The full record, with
its reasons against the UART event stream and the SPI time-slice packets, is
`spec/decision-records/0007-control-interface-spi-register-writes.md`; the
normative content:

- SPI slave, mode 0 (MOSI sampled on SCK's rising edge, MISO changes on the
  falling edge), MSB first; pins `SCK`, `MOSI`, `CS_N`, `MISO`. The receiver
  samples the pins in the core clock domain, so SCK MUST be ≤ 2.0 MHz, and
  `CS_N` MUST be high for ≥ 4 core cycles between transactions.
- One transaction is **exactly 48 bits** between a falling and a rising edge
  of `CS_N` and is one write of 5.2; a transaction of any other length is
  discarded and applies nothing. The word is
  `{F, 6'b0, SEC, A[7:0], D[31:0]}`: `SEC` selects the page (0 the voice and
  master of 5.1, 1 the drum section of 15.1) and `A` the register in it.
- **The unit of 4.3 is the transaction, and its acceptance cycle is the core
  cycle in which the synchronised rising edge of `CS_N` is registered with a
  bit count of 48.** Accepted writes enter a queue of depth 4 in acceptance
  order; at each tick the queue's occupancy is snapshotted and that many
  writes are applied, one per cycle, before any datapath block reads a
  control register. At the specified SCK at most one write can complete in
  a frame, so the queue cannot overflow; a host outside the specification
  that overflows it loses the write and sets a sticky status flag.
- During every transaction the chip returns a 32-bit status word on `MISO`:
  `{0x4D, version 0x2, flags[3:0], frame[15:0]}`, loaded at the falling edge
  of `CS_N` — the flags are `overrun`, `queue non-empty`, `overflow` and
  `fresh` (no write since hardware reset); `frame` is the 16-bit frame
  counter of 4.1, wrapping.
- RESET is per page: `SEC` = 0 address 0x23 resets the registers of section
  14, `SEC` = 1 address 0xFF resets the drum section's (15.8). Both leave the
  link and the queue alone, so writes queued behind either still apply,
  in order, after it.

*Informative:* pin to acceptance is three core cycles; a `CS_N` edge within
about one core cycle of a tick may be accepted in either frame, and the
contract is satisfied either way because the frame is defined by the
acceptance cycle. `rtl-sketch/spi_ctl.v` implements this section and
`rtl-sketch/tb_synth_top.v` drives it through the pins.

### 5.5 Host-side conversions (informative)

`VoiceFx.note_on` is the reference for how a patch's physical units become
register values. These formulas are informative — the block never sees Hz,
seconds or `res` — but a host that wants the model's sound uses them:

```
fitN(v)    = clamp(v, 0, 2^N − 1)          N = the register's width in 5.1 (fixed.usat)
inc[k]     = fit24( round(f0 · 2^(detune_k/12) · 2^24 / 48000) )      f0 = 440 · 2^((note−69)/12)
w[k]       = fit16( floor(mix_k / Σmix · 2^15) )     "floor-normalised": Σ w ≤ 32768 for mix_k ≥ 0;
                                                     Σmix = 0 gives every w[k] = 0
a_inc      = fit24( ceil(2^24 / max(1, floor(attack_s · 48000))) )
sus        = fit24( round(sustain · (2^24 − 1)) )
d_dec      = fit24( ceil((2^24 − 1 − sus) / max(1, floor(decay_s · 48000))) )
alpha      = clamp(1 − exp(−4 / (release_s · 48000)), 0, 1)
exp        = clamp(max(0, ceil(−log2(alpha)) − 1), 0, 255)
mantissa   = clamp(round(alpha · 2^(16 + exp)), 1, 65535)
rate       = (exp << 16) | mantissa
                                                     release_s ≤ 0: alpha = 1
                                                     alpha = 0: rate = 0
cut_lo/hi  = fit16( round(cutoff_lo/hi_hz) )
track_hz   = fit16( round(track · f0 · 4) )
k          = fit17( round(4 · res · 2^14) )
gain       = fit20( round(drive · 0.13 / 0.05 · 2^16) )  = fit20( round(drive · 2.6 · 65536) )
ogain      = fit20( round(0.05 / 0.13 · (1 + 2 · res) · 2^16) )
glide      = fit24( max(1, round((2^(1 / (T_oct · 48000)) − 1) · 2^24)) )    T_oct = seconds per octave;
                                                     T_oct ≤ 0 → 0, off; clamps below 1.7 µs per octave (DR 0004)
vol        = fit16( round(volume · 2^15) )           reference 0.45 → 14746; clamps at volume ≥ 2 (DR 0005)
```

**Where the clamps fire.** (Rev 2; this was OPEN 17.7.)
`test_every_host_conversion_fits_its_register` walks each conversion over
its full plausible input domain — all 128 notes with detune −24..+24
semitones, attack/decay/release 0..30 s, sustain 0..1, cutoff 0..65 535 Hz,
`track` 0..1, `res` 0..1.5, `drive` 0..4, every mix on a quarter grid, every
waveform — and pins the following:

- `a_inc`: the raw value is 2^24, one bit too wide, for an attack of fewer
  than **two frames**, `attack_s < 2/48000 = 41.67 µs` (`attack_s = 0`
  included); it is clamped to 2^24 − 1. The clamp is unobservable: either
  value completes the attack in one update from any level, so the register
  stays 24 bits rather than growing a bit that changes no sample
  (`test_attack_increment_clamps_below_two_frames`).
- `rate`: the 16-bit mantissa is normalized with a binary exponent in the
  high byte. This keeps the control word at 24 bits and the shared multiplier
  at 24 × 16, while representing slow exponential releases without rounding
  the decrement to only two or three Q0.16 values. For `release_s ≤ 0`, the
  host selects the fastest representable code; code zero retains the defined
  one-level-per-frame floor (`test_release_rate_clamps_below_seven_microseconds`).
- `inc`: the raw value exceeds 24 bits only for an oscillator at or above
  48 kHz, the sample rate — note 127 with a detune of +23.24 semitones or
  more; it is clamped to 2^24 − 1, which is already above Nyquist (6.3).
- `k` overflows 17 bits at `res ≥ 2.0`; `gain` overflows 20 bits at
  `drive ≥ 6.152`; `sus` overflows at `sustain > 1`. None is inside the
  range any audition patch uses (`res` ≤ 1.06, `drive` ≤ 3.6); all clamp.
- `w[k]`: a mix summing to zero has nothing to normalise by and rev 1's
  model divided by zero; every weight is 0.
- `d_dec`, `cut_lo`, `cut_hi`, `track_hz` and `ogain` never clamp inside
  their domains.
- `glide` (rev 3) clamps only below 1.7 µs per octave, a ratio of 2 per
  frame; `vol` clamps at a volume of 2.0 and above. Neither is inside any
  host's plausible range.

`a_inc` and `rate` are at least 1 by construction. `d_dec` is 0 only for
`sustain = 1.0`, where DECAY ends at once because `level = FULL ≤ sus`.

Default patch values, for reference (the `note_on` defaults): waves (saw,
saw, square), detune (0, +0.07, −12) semitones, mix (1.0, 0.8, 0.5) → weights
(14246, 11397, 7123), cutoff (400, 4000), res 0.62 → k = 40632, drive 1.6 →
gain = 272630, ogain = 56462, amp ADSR (5 ms, 250 ms, 0.75, 120 ms) → a_inc =
69906, d_dec = 350, sus = 12582911, rate = 45; filter ADSR (4 ms, 300 ms,
0.25, 100 ms); track 0.35; glide 2692 (90 ms per octave); vol 14746.

### 5.6 The reference host (informative)

Which held key sounds, whether a new key retriggers, whether it glides, and
how held keys are assigned to oscillators are the host's decisions in the
product (DR 0002: the MCU sees the MIDI keys). `voice_fx.KeyHost` is the
reference: its policies are parameters and its defaults are what the
audition sequences play with. Nothing here binds an implementation of the
block.

| policy | default | alternatives | record |
|---|---|---|---|
| priority | last note | low, high | DR 0003 |
| trigger | single: GATE_ON when no key was held; a legato key changes pitch only | multi: TRIG on every new key while one is held | DR 0003 |
| release to a held key | return to it, no retrigger | — | DR 0003 |
| glide | always (from the last pitch, the Minimoog's switch), `jump` on the first note after reset | off; legato only (`jump = 1` on the first key of a phrase) | DR 0004 |
| paraphonic | held keys to oscillators 0..2 in press order; the rest double the newest key; one shared gate and trigger | — | DR 0003 |

---

## 6. Oscillators

### 6.1 Registers

Per oscillator: `phase` (24-bit unsigned), `inc_tgt` (24-bit unsigned),
`inc_acc` (32-bit unsigned, Q24.8; `inc = inc_acc >> 8`), `wave`, and the
PolyBLEP state `e` (signed, −15..+8 over all 24-bit `inc` ≥ 1 and 0 for
`inc = 0`; −4..+7 over NOTE_INC) and `r` (16-bit unsigned).

### 6.2 Phase advance

Once per frame, step 9 of 4.2: `phase ← (phase + inc) mod 2^24`. The
oscillator sample of a frame is computed from `phase` **before** the advance.
The first sample of a note from reset uses `phase = 0`.

### 6.3 Frequency to increment; NOTE_INC

An increment `inc` produces a nominal frequency `inc × 48 000 / 2^24` Hz
(resolution 0.00286 Hz). MIDI note n has `f(n) = 440 × 2^((n − 69)/12)` Hz and

```
NOTE_INC[n] = round( f(n) × 2^24 / 48000 )        n = 0..127
```

**Appendix A is normative**; the formula is stated so the table can be
re-derived. `NOTE_INC[69] = 153791`, `NOTE_INC[0] = 2858`, `NOTE_INC[127] =
4384395`; every entry is below 2^23. The table is identical, value for value
and hash for hash, to gf180-polysynth's Appendix A. Detuned oscillators use
`round(f(n) · 2^(detune/12) · 2^24 / 48000)`; the block does not compute this
— the host writes `inc` — so NOTE_INC pins the values a host MUST produce at
zero detune and the tuning reference, no more.

`inc` may hold any 24-bit value. `inc = 0` stalls the oscillator at its
current phase; the PolyBLEP is then identically zero (6.6.3) and the output
is the naive waveform of that phase (6.4) — DC, not silence: from reset a
stalled saw reads −32768 and a stalled square +32767. No host conversion
produces it (NOTE_INC's smallest entry is 2858, and 0 needs an oscillator
below 0.00143 Hz, a detune under −149 semitones at note 0), but it is the
reset value (14) and a legal write, and
`test_zero_increment_stalls_the_oscillator` checks it for every shape from
several phases. Rev 1's model raised here; the prose was right and the model
was wrong (resolved 17.9). A patch with fewer than three oscillators leaves
the rest at `inc = 0` and `w = 0` (`VoiceFx.patch_regs`), so the reference
sequences of the one-oscillator whistle patch exercise it.

### 6.4 Naive waveforms

Let `p` be the 24-bit phase before advance. `naive` is signed 16-bit
(`voice_fx.naive_fx`):

| code | shape | formula |
|---:|---|---|
| 0 | saw | `(p >> 8) − 32768` — rising ramp, −32768 at p = 0, +32767 at p ≥ 0xFFFF00 |
| 1 | square | `+32767` if `p < 0x800000`, else `−32768` — **50 % duty** |
| 2 | pulse25 | `+32767` if `p < 0x400000`, else `−32768` — 25 %; **not a Model D width** |
| 3 | tri | `q = p >> 7` (0..131071); `q − 32768` if `q < 65536`, else `98303 − q` |
| 4 | sine | `SINE(p)`, section 6.5; **not a Model D waveform** |
| 5 | shark | `sat16(( 5749 · saw + 27019 · tri ) >> 15)` — the shark-tooth |
| 6 | revsaw | `sat16(−saw)` — oscillator 3's reverse sawtooth |
| 7 | pulse29 | `+32767` if `p < 4 865 393`, else `−32768` — **29 % duty**, the wide rectangle |
| 8 | pulse15 | `+32767` if `p < 2 516 582`, else `−32768` — **15 % duty**, the narrow rectangle |
| 9–15 | sine | every register value is defined |

Codes 5–8 and the register's fourth bit are revision 9 (DR 0012). Six of the
nine are the Model D's waveform switch, and `docs/minimoog-reference.md` W1–W7
carries the source for each: the shark-tooth's 10/57 and 47/57 are the R030 /
R031 divider on drawing 1448, and the three rectangular duties are that
drawing's pulse-width divider read against SM 2.3's 50 % and 15 %.

The square and pulse step **up** at the wrap (p = 0) and **down** at the
duty point; the saw steps **down** at the wrap. That difference fixes the sign
of the correction in 6.6.4. The shark-tooth mixes the **corrected** saw (6.6)
with the naive triangle, as the switch mixes two buffered outputs, so its step
at the wrap is 10/57 of the sawtooth's and needs no second correction; the
reverse sawtooth negates the corrected saw, for the same reason.

### 6.5 Sine

`SINE_Q256` (Appendix B, normative) is the 256-entry quarter wave
`round(32767 · sin(π/2 · (i + 0.5) / 256))`, i = 0..255 — sampled at bin
**midpoints**, because it is read nearest-entry with no interpolation. (This
differs from gf180-polysynth's 257-entry edge-sampled table; the two blocks'
sines are not the same values.) From the phase (`voice_fx.sine_fx`):

```
idx  = (p >> 14) & 1023           top 10 bits of the phase
quad = idx >> 8                   0..3
i    = idx & 255
q    = SINE_Q256[255 − i]  if quad is odd, else  SINE_Q256[i]
SINE = −q                  if quad ≥ 2,     else  q
```

Consequences: the full 1024-entry expansion (hash in Appendix B) has
`SINE(0) = 101`, `SINE[255] = SINE[256] = 32767`, `SINE[512] = −101`; there is
no zero and no −32768 in it; it is odd-symmetric about index 512 and
even-symmetric about 255.5.

### 6.6 PolyBLEP

Applied to saw, square and pulse25 only. Triangle and sine are the naive
waveform (the model constructs `OscFx` with `blep = blep and shape in (saw,
square, pulse25)`). The integer model's aliasing suppression equals the float
PolyBLEP's at every note measured (DESIGN.md section 6); the widths below were
set by tracking the float waveform inside Q1.15, not by aliasing.

#### 6.6.1 Reciprocal at increment change

Whenever `inc` takes a new value (SET_INC with `jump`, each frame in which
the glide slew changes `inc_acc >> 8`), compute (`voice_fx.recip_of`, with `MANT_BITS = RECIP_BITS = 16`):

```
e = bit_length(inc) − 16                  bit_length(v) = number of bits in v, so 2^(bl−1) ≤ v < 2^bl
m = inc >> e         if e ≥ 0             m is 16 bits: 2^15 ≤ m < 2^16
  = inc << (−e)      if e < 0
r = min( floor(2^31 / m), 65535 )         16 bits
```

So `inc = m · 2^e` with a 16-bit mantissa, and `r ≈ 2^31 / m`. The `min`
fires only when `m = 2^15`, i.e. `inc` is a power of two, a 1-LSB error; no
NOTE_INC entry is a power of two. Over NOTE_INC, `e` ranges −4..+7 and `r`
33209..62696. Example: `inc = 153791` (note 69) has 18 bits, `e = 2`,
`m = 38447`, `r = floor(2147483648 / 38447) = 55855`.

`inc = 0` has no mantissa: `(e, r) ← (0, 0)`, the reset values of section
14, and no division is performed. Neither is observable — with `inc = 0`
neither window of 6.6.3 can open, so `c = 0` whatever `(e, r)` hold — and an
implementation whose divider yields something else for `m = 0` is still
bit-exact.

This is one integer division per increment change. *Informative:* a
sequential divider takes 24 clocks of the 256-cycle frame; the specification
is the exact floor quotient however it is obtained.

#### 6.6.2 The fraction `x / inc` in Q0.16

For `0 ≤ x < inc` (`voice_fx.frac_q16`):

```
p = x >> e           if e ≥ 0        p ≤ m, 16 bits
  = x << (−e)        if e < 0
u = (p · r) >> 15                    exact 32-bit product
u = min(u, 65535)
```

`u ≈ x · 2^16 / inc`. The `min` cannot fire (`p ≤ m` and `r ≤ 2^31/m` give
`p·r ≤ 2^31`, with equality impossible after the clamp of 6.6.1); an
implementation that omits it is bit-identical. `frac(0) = 0`.

#### 6.6.3 The correction

For phase `p` and increment `inc` (`voice_fx.blep_fx`), the correction `c(p)`
is:

```
c = 0
if p < inc:                        just after the wrap
    s = 65536 − frac(p)            1..65536
    c = −((s · s) >> 17)           −32768..0
q = 2^24 − p
if q < inc:                        just before the wrap  (q is 1..inc−1 here)
    s = 65536 − frac(q)            1..65536
    c = +((s · s) >> 17)           0..+32768
```

`c` ranges **−32768..+32768** — 17 bits signed; the +32768 occurs when
`frac(q) = 0`, which happens for `q < 2^e`. If both conditions hold (possible
only when `inc > 2^23`) the second assignment wins. With `inc = 0` neither
condition can hold and `c = 0` for every `p`.

*Informative:* this is `dsp._blep` in integers: the polynomial `−(1 − t/dt)^2`
just after a downward step and `+(1 − (1−t)/dt)^2` just before it, in Q0.16
squared and shifted to Q1.15. It is the correction to **subtract** from a
naive saw.

#### 6.6.4 Application per shape

With `naive` from 6.4 and `c(·)` from 6.6.3 evaluated with this oscillator's
`inc, e, r` (`voice_fx.OscFx.render`):

```
saw:      osc = sat16( naive − c(p) )
square:   p2 = (p + 0x800000) mod 2^24          duty = 2^23
          osc = sat16( naive + c(p) − c(p2) )
pulse25:  p2 = (p + 0xC00000) mod 2^24          duty = 2^22, so p2 = (p + 2^24 − duty) mod 2^24
          osc = sat16( naive + c(p) − c(p2) )
tri, sine: osc = naive
```

**The square's sign is opposite to the saw's at p = 0**: the saw steps down
there and takes `−c`; the square steps up and takes `+c`. Its second edge, at
`p = duty`, steps down and takes `−c` evaluated at the phase shifted so that
edge lands on the wrap. Getting this backwards measures about 5 dB *worse*
than the naive square (`test_square_correction_has_the_right_sign`).

Consequences: at `p = 0` the band-limited saw is `sat16(−32768 − (−32768)) =
0`, the midpoint of its step, and the square is `sat16(32767 − 32768 − 0) =
−1`. Both differ from the naive values by design.

### 6.7 Glide (DR 0004)

The glide is on the chip and is **constant rate, linear in pitch**: every
frame the increment moves toward its target by a fixed ratio of itself — a
constant number of cents per frame, geometric in the increment — so a
two-octave glide takes twice as long as a one-octave one, every oscillator
keeps its detune through the glide, and it lands exactly. `glide` is the
voice's Q0.24 register (the ratio per frame minus 1; 0 = off); each
oscillator holds `inc_tgt` (24) and `inc_acc` (32, Q24.8) and adds
`inc = inc_acc >> 8` to its phase (6.2).

SET_INC k, v, jump (5.2): `inc_tgt ← v`; if `jump = 1` or `glide = 0`,
`inc_acc ← v << 8`. Then at the end of every frame (step 9 of 4.2), for each
oscillator with `inc_acc ≠ inc_tgt << 8` (`OscFx.slew`):

```
tgt     = inc_tgt << 8
d       = max(1, (inc_acc · glide) >> 24)         exact 56-bit product; both operands non-negative
inc_acc = min(tgt, inc_acc + d)      if tgt > inc_acc
        = max(tgt, inc_acc − d)      if tgt < inc_acc
inc_acc = tgt                        if glide = 0
```

Frame f's oscillator uses `inc_acc >> 8` as it stands at the start of the
frame; `(e, r)` MUST be recomputed whenever that value changes (6.6.1). The
`max(1, ·)` is the release's lesson (8.3): without it a small increment times
a small rate truncates to no motion at all.

*Informative:* the host's conversion is `glide = round((2^(1/(T_oct · 48000))
− 1) · 2^24)` for `T_oct` seconds per octave (5.5); the reference host's 90 ms
is 2692, and one octave then takes 4320 frames within 1 %. Constant-time
glide is a host policy (compute `T_oct` from each interval); legato-only glide
is `jump = 1` on the first key of a phrase. The float audition model glides
geometrically over a constant time, so the two models differ on a glided
patch by design (`voice_fx_render.py`).

---

### 6.9 Modulation (DR 0012)

State: `mod_sig`, signed 16-bit, **computed at the END of a frame and read at
the START of the next**. Oscillator 3 is the modulation source and, with
`MROUTE.OSC3` set, also a destination; the register is what breaks that loop.
One frame is 20.8 µs.

Per frame, before the oscillators (`voice_fx.VoiceFx._modulate`):

```
amt    = sat16( ( mod_sig · MWHEEL ) >> 15 )                       Q1.15
oct_p  = MROUTE.OSC  ? sat_oct( ( amt · MPD ) >> 15 ) : 0          Q3.12 octaves, ±4
oct_f  = MROUTE.FILT ? sat_oct( ( amt · MFD ) >> 15 ) : 0
(m, s) = EXP2(oct)          m = 32768 + EXP_ROM65[i] + (((EXP_ROM65[i+1] − EXP_ROM65[i]) · f) >> 6)
                            i = oct[11:6],  f = oct[5:0],  s = 15 − oct[15:12]  (signed; 12..19)
inc_k  = usat24( ( inc_k · m_p ) >> s_p )   for k = 0, 1, and for k = 2 only if MROUTE.OSC3
cut    = clamp( ( cut · m_f ) >> s_f , 30, 21600 )                 applied after 10's clamp
```

and at the end of the frame, after the oscillators have run:

```
mmix'    = min( MMIX, 32768 )
mod_sig  = sat16( ( naive_3 · (32768 − mmix') + noise_mod · mmix' ) >> 15 )
```

`naive_3` is oscillator 3's **naive** waveform (6.4) at the phase that produced
this frame's sample — before PolyBLEP, because the modulation path is a control
voltage and is never summed into the mixer. `noise_mod` is 6.10's pink or red.
The two pan weights sum to exactly 32768, so `mod_sig` is a convex combination.
`oct = 0` gives `(m, s) = (32768, 15)`, i.e. `(v · 32768) >> 15 = v` exactly —
so a voice with `MWHEEL = 0` is bit-identical to one with no modulation path at
all, which is what makes every pre-revision-10 reference sequence unchanged.

### 6.10 The noise source (DR 0012)

State: a 31-bit LFSR (reset to `0x7F215FF7`, **not** zero), the pink biquad's
`x1, x2` (Q1.15) and `y1, y2` (32-bit Q5.27), and the red pole's 32-bit state.

```
w      = lfsr[30:15] ^ lfsr[15:0] ^ lfsr[17:2] ^ lfsr[19:4]        16 new bits at once
lfsr  <- (lfsr << 16) | w                                          x^31+x^15+x^13+x^11+1
x0     = signed(w)
T      = (( 912164·x0 − 741208·x1 − 117831·x2 ) << 5) − ( −28689·y1 + 12348·y2 )
y0     = sat32( T >> 14 )                                          Q5.27; ONE floor
rl    <- sat32( rl + ((( y0 − rl ) · 904 ) >> 16) )                 one pole, 106.1 Hz
white  = x0 >> 2      pink = sat16( y0 >> 14 )      red = sat16( ( rl · 29841 ) >> 28 )
```

The `>> 2` is `NOISE_SHIFT`, applied to all three colours equally: they leave at
the same RMS because the instrument's do (drawing 1431 labels all three outputs
−4 dBm), and pink's crest factor of 4.6 needs the headroom. `NSEL` selects the
pair: clear puts **white** in the mixer and **pink** on the modulation bus, set
puts **pink** in the mixer and **red** on the bus.

---

## 7. Mixer

Per frame (`voice_fx.mix_fx`):

```
acc   = osc_0 · w_0 + osc_1 · w_1 + osc_2 · w_2 + noise · WN   exact; |acc| < 4 · 2^31
mixed = sat16( acc >> 15 )                                     arithmetic shift, then clamp
```

`noise` is 6.10's white or pink, per `NSEL`. The fourth term is revision 9
(DR 0012): the Model D's mixer has five sources and this chip has four, the
external input being the one it does not have. `w_k` and `WN` are 16-bit
unsigned. Weights the host derives by 5.5 sum to at most
32768 and cannot clip (`test_normalised_mix_cannot_clip`); unnormalised
weights are legal and saturate, never wrap (`test_mixer_saturates_instead_of_wrapping`).
With the default weights and oscillators 1 and 2 at the values of 6.6.4's
consequence, `mixed` at the first frame of a note is `(0 + 0 − 7123) >> 15 =
−1`.

---

## 8. Envelopes

Two identical integer ADSRs, `amp` and `filt`, share the `gate`
(`voice_fx.AdsrFx`). Attack and decay are linear ramps, matching the float
model that was auditioned; release is exponential with a floor.

### 8.1 Registers

| Register | Width | Meaning |
|---|---|---|
| `level` | 24-bit unsigned | Q0.24 level, 0..0xFFFFFF; `FULL = 2^24 − 1` |
| `seg` | 2-bit | ATTACK = 0, DECAY = 1, SUSTAIN = 2 |
| `a_inc` | 24-bit unsigned | level increment per frame in ATTACK |
| `d_dec` | 24-bit unsigned | level decrement per frame in DECAY |
| `sus` | 24-bit unsigned | DECAY target and SUSTAIN level |
| `rate` | 24-bit unsigned | Q0.16 mantissa in bits 15:0, binary exponent in bits 23:16 |

There is no IDLE state and no RELEASE state: the gate selects the branch. A
voice from reset has `level = 0`, `seg = ATTACK`, `gate = 0`, and the release
branch holds the level at 0 (8.3), so it is silent.

### 8.2 Output

The envelope's output in a frame is the 15-bit value `level >> 9`, from the
level **before** this frame's update (8.3). It is 0 in the first frame of a
note from reset and is 32767 for `level ≥ 0xFFFE00`, in particular at `FULL`.

### 8.3 Update rule (step 9 of 4.2, once per frame)

A GATE_ON or TRIG applied at the start of the frame (step 1 of 4.2) sets
`seg ← ATTACK` with the level untouched, before the output of 8.2 is taken
and before this update. Then exactly one branch executes, chosen by `gate`
then `seg` as they are at the start of the update. Comparisons are on exact
integers.

```
gate = 1:
  ATTACK:   level ← level + a_inc
            if level ≥ FULL:    level ← FULL ; seg ← DECAY
  DECAY:    level ← level − d_dec                     (exact; may be negative before the test)
            if level ≤ sus:     level ← sus ;  seg ← SUSTAIN
  SUSTAIN:  level ← sus

gate = 0 (release, from any seg; seg is NOT changed):
  dec   ← (level · mantissa) >> (16 + exponent)       exact 40-bit product, then scale shift
  level ← level − max(1, dec)
  if level < 0:  level ← 0
```

**`max(1, dec)` is load-bearing.** Below `level = 2^(16+exponent) / mantissa`
the product truncates to zero and, without it, the level would never move
again and the note would never end; with it the tail below that floor decays
at one LSB per frame and reaches exactly zero (`test_release_reaches_exactly_zero`).
The floor is `floor(2^(16+exponent) / mantissa) + 1` levels
(`AdsrFx.floor_level`); at 24 level bits it is below −60 dBFS for every
release up to 1 s (`test_release_floor_is_below_the_noise_floor`), which is
why the level is 24 bits and not 20.

Notes that follow from the rule and are intentional:

- `a_inc = 0` holds ATTACK forever at the current level; `d_dec = 0` holds
  DECAY forever unless `level ≤ sus` already; `rate = 0` releases at one LSB
  per frame (2^24 frames = 350 s from full). The host conversions of 5.5
  give `a_inc` and `rate` at least 1; they give `d_dec = 0` only for
  `sustain = 1.0`, where DECAY ends at once because `level = FULL ≤ sus`.
- SUSTAIN re-asserts `level ← sus` every frame, so a change of `sus` while
  sustaining moves the level in one frame (unlike gf180-polysynth's envelope).
- In DECAY, if `sus` is above the current level the level jumps **up** to
  `sus` in one frame.
- A gate that drops during ATTACK releases from the partial level; the attack
  does not complete. The release branch leaves `seg` alone, and GATE_ON sets
  it to ATTACK, so a gate that comes back always attacks from the current
  level (8.5).

*Informative, derived:* ATTACK from 0 takes `ceil(FULL / a_inc)` updates; the
default `a_inc = 69906` reaches FULL at update 240 (5.0 ms) and the output
first reads 32767 in frame 240.

### 8.4 Gate timing in the model

`AdsrFx.render(n, gate, trig)` takes a per-frame `gate` array and a per-frame
`trig` array (1 in the frames at whose start a GATE_ON or TRIG was applied);
`render(n, gate_n)` with an integer is the single-note form, the gate on for
frames `0..gate_n−1`, `gate_n = min(n, max(1, floor(gate_s · 48000)))`, with
`gate_s = 0.8 · dur` by default. In hardware the gate is the register of 5.1
and its timing is the host's.

### 8.5 Retrigger (DR 0003)

- **GATE_ON** and **TRIG** re-enter ATTACK with the level unchanged: the
  attack proceeds from wherever the envelope is — from zero only when it is
  at zero. There is no reset to zero (the Minimoog's contour continues from
  its current level, and so does every modern Moog's by default).
- A pitch change while the gate is on (SET_INC without TRIG) leaves both
  envelopes alone: single triggering, the Minimoog's. A host that wants
  multiple triggering sends TRIG with the new pitch.
- **Nothing else changes at a note.** `phase[k]` and the ladder's state are
  written by RESET only; the oscillators free-run and the filter is
  continuous across notes — a ring, or a self-oscillation, carries into the
  next note, and the note ends because the VCA (9) closes.
- Key priority and paraphonic allocation are the host's (5.6).

Testable, and tested (`test_voice_fx.py`, the DR 0003 tests): no step in the
envelope output at a TRIG and then `a_inc` per frame from the current level;
the envelope constant across a legato pitch change; the oscillator stream
equal to a free-running oscillator's; a phrase split across two `play` calls
identical to the unsplit one.

---

## 9. Amplitude (the VCA)

Step 7 of 4.2 (`VoiceFx._render`):

```
v = (y · ae) >> 15          y the ladder's 19-bit output (11.4), ae = level_amp >> 9, 0..32767
```

`y` is −262144..262143, so `|v| < 2^18` — 19 bits signed — and it cannot
overflow. The amplitude envelope is applied **after** the filter (DR 0005),
so the ladder's input is `mixed` (7) at the mixer's level, and a note ends
when the envelope reaches zero whatever the filter's state.

---

## 10. Cutoff, the g ROM and the kc ROM

### 10.1 Cutoff and g

Step 5 of 4.2:

```
span = cut_hi − cut_lo                                 exact, signed
cut  = cut_lo + ((span · fe) >> 15) + track_hz          fe = level_filt >> 9; exact sum
cut  = clamp(cut, 30, 21600)                            integer Hz, 15 bits
```

`span` may be negative (a patch with `cut_hi < cut_lo` is legal), in which
case the shift floors toward −∞. The sum MUST be computed exactly before the
clamp whatever the register widths of 5.1. `21600 = 0.45 × 48000`, the float
model's clamp.

The coefficient (`voice_fx.g_from_cut`, `GROM_BITS = 7`):

```
i    = cut >> 8                        0..84
frac = cut & 255
g    = G_ROM128[i] + (( (G_ROM128[i+1] − G_ROM128[i]) · frac ) >> 8)       Q0.16
```

**Appendix D is normative**: `G_ROM128[i] = round((1 − exp(−2π · 256i / 96000))
· 65536)`, i = 0..128, sampled at bin **edges** because it is interpolated;
`96000` is the ladder's oversampled rate. The table is strictly increasing
(every delta is 129..1089), so the product is non-negative and the shift is a
logical one.
`g(30) = 127`, `g(21600) = 49594`; entries above index 85 are never read.
Against the exact coefficient the ROM is within 4 LSB everywhere and within
1 % relative above 100 Hz (`test_cutoff_rom_tracks_the_float_coefficient`).
Example: `cut = 515` gives `i = 2`, `frac = 3`, `g = 2160 + ((3213 − 2160) · 3
>> 8) = 2172`.

`g` is held for both oversampling passes of the frame.

### 10.2 Resonance compensation (DR 0006)

The feedback the loop needs for a given resonance varies with cutoff
(Huovilainen §5.3; measured in 11.5). The host's `k` is scaled per frame by a
second ROM read with the same cutoff (`voice_fx.kc_from_cut`, `k_effective`;
`KROM_BITS = 5`, `K_BITS = 17`):

```
i     = cut >> 10                        0..21
frac  = cut & 1023
kc    = K_ROM32[i] + (((K_ROM32[i+1] − K_ROM32[i]) · frac) >> 10)      unsigned Q1.15; the delta may be negative: arithmetic shift
k_eff = min( (k · kc) >> 15, 2^17 − 1 )                                Q3.14, 17 bits; exact 33-bit product
```

**Appendix E is normative**: `K_ROM32[i] = round(k_onset(clamp(1024 i, 30,
21600)) / 4 · 32768)`, i = 0..32, where `k_onset(cut)` is the loop gain at
which the linearised ladder — four one-poles `G/(1 − (1 − G)z⁻¹)` with `G =
g(cut) · s0 / 2^16`, `s0 = TANH16[1] / 2^13` the tanh table's first-bin
slope, and the half-sample feedback delay `(z⁻¹ + z⁻²)/2` at 96 kHz — has a
phase of −180° and a magnitude of 1 (`voice_fx.k_onset`, a bisection; float,
ROM-building only). `kc = 32768` means `k_eff = k`; the table is 32799 at
30 Hz, peaks at 39879 (1.217) at entry 11 (11 264 Hz) and is 33964 at the
clamp; entries 22..32 are never read. The saturation to `2^17 − 1` is the
ladder's port width and fires only for `res · kc > 2`, above `res` = 1.64 at
the peak; no audition patch reaches it. `kc` and `k_eff` are held for both
oversampling passes.

The consequence, measured (11.5): **`res = 1.0` is the onset of
self-oscillation at every cutoff** within 0.39 %.

---

## 11. The ladder filter

Huovilainen, DAFx-04, "Non-Linear Digital Implementation of the Moog Ladder
Filter" — the nonlinear model, not the linearised one (DR 0001). The
nonlinearity is in every stage; the paper's equation (17) reuse gives five
`tanh` per pass rather than eight; the feedback carries a half-sample delay as
the average of the last two outputs; the whole filter runs at 2× the frame
rate. Anyone reimplementing this MUST NOT simplify to a single feedback-path
`tanh`; that is a different filter.

### 11.1 Normalisation (informative)

The paper's stage is `y += 2·Vt·g·(tanh(x/2Vt) − tanh(y/2Vt))`. The model
holds the state in units of 2·Vt, so the stage becomes `Y += g·(tanh(X) −
tanh(Y))`: the tanh argument **is** the state, the table is indexed by it, and
the 2·Vt multiply is gone. `2·Vt = 0.05` and the audio-to-volts scale `0.13`
(`volts_per_unit`) survive only in the two host constants `gain` and `ogain`
of 5.5.

### 11.2 Formats and state

| | format | width |
|---|---|---|
| input `x` | Q1.15 signed | 16 |
| state `y[0..3]`, `d1`, `d2` | Q4.20 signed, units of 2·Vt; ±8.0 | 24 |
| stored tanh `w[0..3]` | Q1.15 signed, −32767..32767 | 16 |
| `g` | Q0.16 unsigned; up to 61659 at a 43.2 kHz cutoff, 49594 at this block's clamp — bit 15 is data | 16 |
| `k_eff` | Q3.14 unsigned, per frame (10.2); 65536 is a loop gain of 4 | 17 |
| `gain`, `ogain` | Q4.16 unsigned | 20 |
| output `y_out` | Q4.15 signed, saturated to 19 bits (`out_bits`, DR 0005) | 19 |

`TQ = 5` is the shift from Q1.15 to the state's 20 fraction bits. All state is
0 at reset. The ±8.0 state clamp (`sat24`) is part of the arithmetic and MUST
be implemented, although it has been shown never to fire: once |y| ≥ 4.0 the
stage's own tanh is pinned and the state turns back, peaking near 4.0 + 2g
(README, "Verifying the RTL").

### 11.3 tanh from the 16-entry table

`TANH16` (Appendix C, normative): `round(tanh(i/16 · 4.0) · 32767)`, i =
0..15, edge-sampled over [0, 4). For a 24-bit state value `v`
(`LadderFx.tanh_fx` with `N = 16`, `dom_fx = 4 << 20 = 4194304`):

```
neg = v < 0
a   = |v|                                     0..2^23 (|−2^23| = 2^23 is representable as unsigned)
if a ≥ 4194304:                               |v| ≥ 4.0
    t = 32767
else:
    idx  = a >> 18                            0..15   (a · 16 / 2^22)
    fr   = a & 0x3FFFF                        18-bit fraction within the bin
    t0   = TANH16[idx]
    t1   = TANH16[idx + 1]   if idx < 15, else 32767
    t    = t0 + (((t1 − t0) · fr) >> 18)
tanh(v) = −t if neg else t
```

The top word 32767 is **not** tanh(4.0) (which would round to 32745): the last
bin interpolates up to the clamp so the curve meets it with no step. An
implementation that uses tanh(4.0) there differs from the model by up to 22
LSB across the top bin. `tanh(±1) = 0` (the first bin's slope is 8025/2^18 per
LSB), `tanh(262144) = 8025`, `tanh(4194303) = 32766`.

*Informative:* values sit at bin edges because the table is interpolated;
midpoint values (right for nearest-entry reading) would put a half-bin skew on
every lookup, which measured 8 dB worse and read as "interpolation made it
worse" (`test_interpolated_beats_nearest_at_the_same_size`). 16 interpolated
entries score identically to 256 on every patch (`test_small_table_is_enough`);
the 256-entry alternative is not part of this contract.

### 11.4 Per-frame algorithm

Step 6 of 4.2, for frame input `x = mixed` and this frame's `g`, `k_eff`,
`gain`, `ogain` (`LadderFx.process`, with `g_q16` and `k_q14` per sample). Two
passes; each pass advances every state register once.

```
xg = (x · gain) >> 11                     exact 36-bit product; the same for both passes

for pass in 0, 1:
    fb  = (d1 + d2) >> 1                  half-sample delay: mean of the last two outputs, 25-bit sum, arithmetic shift
    u   = sat24( xg − ((k_eff · fb) >> 14) )  input stage, state units
    w0  = tanh(u)                         11.3
    for s in 0, 1, 2, 3:                  IN ORDER; stage s uses the w[s−1] just written in this pass
        prev = w0 if s = 0 else w[s−1]
        diff = prev − w[s]                −65534..65534
        y[s] = sat24( y[s] + ((g · (diff << 5)) >> 16) )
        w[s] = tanh(y[s])
    d2 ← d1 ; d1 ← y[3]

y_out = sat19( ((y[3] >> 5) · ogain) >> 16 )     from the state after pass 1; Q4.15, ±8.0
```

Every shift is arithmetic. Product widths: `x·gain` 36 bits, `k·fb` 41,
`g·(diff<<5)` 38, `(y[3]>>5)·ogain` 39; every pre-saturation value is under
2^27 in magnitude (`ladder_dp.v`, `AW = 28`). The sequence of operations is
the model's; an implementation that reorders the four stages, uses the
previous pass's `w[s−1]`, computes `fb` once per frame, or averages
differently is not this filter (the injected defect `INJECT_BUG_LADDER_FB`,
a unit delay in place of the average, mismatches 23 377 of 28 800 samples).

### 11.5 Coefficients (informative)

`k`, `gain`, `ogain` are registers; the host derives them by 5.5 from `res`
and `drive`, and 10.2 turns `k` into the per-frame `k_eff`. Measured
(`model/k_comp_sweep.py`, DR 0006): the loop gain at which the fixed-point
filter starts to self-oscillate rises from 4.00 at 30 Hz through 4.32 at
2.5 kHz to 4.85 near 11 kHz and falls to 4.14 at the clamp — which is why,
at a fixed `k = 4 · 1.08`, rev 1 did not sustain above about 3 kHz — and is
within 0.25 % of the linearised prediction the ROM is built from at every
cutoff. With the ROM, the ring decays at `res = 0.995` and grows at `1.005`
at 200 Hz, 3 kHz and 10 kHz (`test_self_oscillation_starts_at_res_1_everywhere`).
The frequency it oscillates at is 1.002 × the cutoff at 30 Hz, 1.004 at
1.6 kHz, 0.991 × at 10 kHz and 0.988 × at the clamp — worst error 0.90 % from
30 Hz to 10 kHz, against revision 8's 6.85 %, since revision 9 built
Huovilainen's `fcr` tuning polynomial and one constant scale into the g ROM
(DR 0011). What remains is the paper's own two-dimensional caveat and is
17.12. `ogain`'s `(1 + 2·res)` term is a partial passband-loss compensation.

### 11.6 Where the ladder saturates

Three clamps, all part of the arithmetic: `u` to 24 bits (input stage), each
`y[s]` to 24 bits (never reached in practice, 11.2), and `y_out` to 19 bits —
a width, not a clip in practice: the eight audition patches peak at 1.94 ×
full scale and the RTL bench's stimulus at 1.96, against the word's 8.0
(section 12).

---

## 12. Output stage and the saturation points (DR 0005)

Step 8 of 4.2, after the VCA of section 9 (`drums_fx.output_fx`):

```
sample = sat16( (v · vol + dmix · dvol + body · bvol) >> 15 )
             v     the VCA's output (9), 20 bits signed;  vol   the voice's Q0.15 register
             dmix  the drum section's mix bus (15.5), 22 bits signed;  dvol  its Q0.15 register
             body  the modal bank's word (15.6), 19 bits signed;      bvol  its Q0.15 register
             exact sum of three products (under 2^38), one arithmetic shift, ONE clamp
```

With `dvol = bvol = 0` — or with no drum section — this is rev 3's
`sat16((v · vol) >> 15)` bit for bit, and every rev-3 reference sequence is
unchanged. Rev 5 adds the two drum terms (DR 0008) so that the two drum buses
reach the rail at their full width: a bus clipped to 16 bits before the
master gains could not be recovered by lowering them.

`vol` replaces rev 1's fixed gain of 0.9. The reference host writes `vol =
14746` (0.45), at which the loudest audition patch (`growl-bass`) peaks at
0.86 × full scale and none clips; at rev 1's 0.9 `growl-bass` clips 4.8 % of
its samples (`test_reference_volume_clips_no_audition_patch`). The rail is
hard, and the headroom above the reference is the host's to spend — the
policy Sequential states for the Prophet-6 ("rather than limit the outputs
… we allow you to adjust levels", DR 0005) — not a limiter's.

**Saturation is designed, and it is in three places.** The overdrive of the
instrument is the ladder's `tanh` in every stage, driven by `gain` (11.3,
11.4; DR 0001); the mixer's `sat16` is the hard rail of the Q1.15 word, which
weights the host normalises never reach (7); and the drum section's swing VCA
is the same `tanh` table on an asymmetric drive (15.5; DR 0008), the 808's
own nonlinearity. The signal path has exactly these clamps, in signal order,
and no others:

| # | where | clamp | section |
|---|---|---|---|
| 1 | each oscillator, after PolyBLEP | `sat16` | 6.6.4 |
| 2 | mixer sum | `sat16` | 7 |
| 3 | ladder input stage `u` | `sat24` (state units, ±8.0) | 11.4 |
| 4 | ladder state `y[s]` after each integrator | `sat24` | 11.4 |
| 5 | ladder output `y_out` | `sat19` (Q4.15, ±8.0) — never reached on the audition patches or the bench | 11.4 |
| 6 | output sample | `sat16` — reached only if the host raises `vol`, `dvol` or `bvol` past the reference | 12 |
| 7 | drum path tap `TAP m` | `sat16` of the mode's state ÷ 8 — the rail of a resonator's output; never reached by the reference kit | 15.5 |
| 8 | modal bank state `y[m]` | `sat28` — part of the bank's arithmetic since rev 1; reached only by register values that put a mode past ±4096 × full scale, never by the reference kit | 15.6 |
| 9 | modal bank word `body` | `sat19` (Q4.15, ±8.0) — a width; never reached by the reference kit at any accent | 15.6 |

The VCA multiply (9), the cutoff shift (10, before its clamp to Hz), the kc
interpolation and `k · kc` (10.2, saturated only at the port width), the tanh
interpolation and the glide slew cannot overflow and have no clamp; nor do
the drum section's path values, its two buses or a mode's excitation sum,
which are carried exactly at the widths of 15.5. The drum section adds no
clip of its own to the path of a signal: the tap rail (7) and the state
word (8) are reachable by register values only, and the reference kit is
tested not to reach them (`test_bank_headroom_zero_and_nineteen_bits_hold_the_kits_loudest_hit`).

*Informative:* `rtl-sketch/synth_top.v` does **not** yet implement the output
stage above. It carries the placeholder drum section of
`docs/ARCHITECTURE.md` section 4 — one 19-bit bus, two terms,
`sample = sat16(((v · vol) >> 15) + ((d · dvol) >> 15))`, two shifts and two
rails rather than the one exact sum of 12 — and with `dvol = 0`, or a silent
drum section, that is this section's sample bit for bit, which is what
`rtl-sketch/verify_voice.py` checks. Closing that gap is 17.23. In rev 1
the ladder's output was 16 bits and clamp 5 was where four of the eight
audition patches clipped; with the width, the VCA after the filter and the
volume, the float-versus-fixed gap on `growl-bass` is −32 dB instead of
−13 dB, the ladder's own quantisation (`voice_fx_render.py`).

---

## 13. Output format: I2S

The block's audio leaves as I2S (DESIGN.md section 7; the 1-bit modulator
discussed there is a debug pad, not the output, and is not specified here).
The convention below is the one gf180-polysynth's `fpga/i2s_tx.v` implements
and `fpga/tb_i2s.v` decodes as a PCM5102A does; it is adopted unchanged so
the two blocks are interchangeable at the DAC. No transmitter exists in this
repository yet; this section is what one MUST do.

- **Clocks.** `BCLK = 12.288 MHz / 4 = 3.072 MHz = 64 × fs`. `LRCLK = 12.288
  MHz / 256 = 48 kHz`; one LRCLK period is 256 core cycles, the same length
  as a frame. Both are integer divisions of the core clock; no PLL.
- **Slots.** 32 BCLKs per channel: LRCLK **low = left**, high = right.
- **Word.** The 16-bit sample, MSB first, **left-justified** in the 32-bit
  slot; the remaining 16 bits of the slot are zero. Standard I2S timing: the
  MSB is on the **second** BCLK after the LRCLK edge (one BCLK of delay), and
  SDATA changes on the falling edge of BCLK so a receiver samples it on the
  rising edge. (The prototype defect that put the MSB one BCLK late made
  every negative sample decode positive; `tb_i2s.v` catches it.)
- **Mono.** Both channels carry the **same** sample in one LRCLK period.
- **Which sample lands in which period.** Sample f is transmitted in LRCLK
  period `f + D` for a constant `D ≥ 1` fixed by the implementation, with
  every sample sent exactly once, in order: no sample is skipped or repeated,
  and left and right of one period are never different samples. `D` is a
  latency, not part of the sample sequence, and an implementation MUST state
  it. *Informative:* the sibling latches the core's sample when it is strobed
  and loads it at the last BCLK of the right slot preceding the next period,
  so `D = 1` when the strobe precedes that load.

The DAC side (PCM5102A with SCK tied low, MAX98357A, or a TLV320DAC3100) is
board material and outside the contract.

---

## 14. Reset and initial state

On hardware reset, and on RESET, every register takes the value below. There
is no other observable state.

| Register | Reset | Notes |
|---|---|---|
| `phase[k]`, `inc_tgt[k]`, `inc_acc[k]`, `e[k]`, `r[k]` | 0 | `inc = 0` gives `c = 0` by 6.6.3 and `(e, r) = (0, 0)` by 6.6.1; `(e, r)` are recomputed at the first change of `inc` |
| `wave[k]`, `w[k]` | 0 | `wave` = 0 is saw (5.2) |
| `level`, `seg` (both envelopes) | 0, ATTACK | |
| `gate` | 0 | |
| `glide` | 0 | off: SET_INC takes effect at once |
| `vol` | 0 | silent until the host writes a volume (17.8) |
| `a_inc`, `d_dec`, `sus`, `rate` (both) | 0 | |
| `cut_lo`, `cut_hi`, `track_hz` | 0 | the clamp makes the cutoff 30 Hz |
| `k`, `gain`, `ogain` | 0 | |
| ladder `y[0..3]`, `w[0..3]`, `d1`, `d2` | 0 | `LadderFx.reset()` |
| `dvol`, `bvol` | 0 | the drum buses are silent until the host writes a gain (17.8) |
| every drum register of 15.1, every envelope level, `strike`, `t`, `stops_prev`, the six phases | 0 | 15.8; all-zero paths are OFF, so the section is silent |
| LFSR state | 1 | 15.4: frame 0's noise word is 1 |
| modal bank `y1[m]`, `y2[m]`, `exc[m]`, `h1[m]`, `h2[m]` | 0 | `ModalFx.reset()` |
| output sample register | 0 | |
| control parser / queue | idle, empty | on hardware reset only: the RESET write leaves the link and its queue alone (5.4) |

Consequences: from reset the voice outputs 0 every frame until programmed —
the envelope holds at 0 by the release branch, `vol` is 0, and a zero-state
ladder with zero input stays at zero. All-zero control is the model's reset
of *state*; the model has no reset values for *control* because `note_on`
always writes the whole image. **The power-on defaults are these zeros (DR
0007 section 6, closing 17.8): a bare GATE_ON is silent.** The host is a
microcontroller with the patch in flash; it writes the image at boot and
whenever the status word's `fresh` flag reads 1, and non-zero defaults would
be a second, silent copy of a default patch in metal.

---

## 15. The drum section and the modal resonator bank (DR 0008)

`model/drums_fx.py` (`DrumsFx`) and `model/modal_fixed.py` (`ModalFx`) are
the specification of this section; `rtl-sketch/drum_kit.v` (`drum_dp.v` +
`modal_dp.v`) is bit-exact against them (16). **Proposed, not ratified**, as
the rest of this document.

The drum section is the TR-808 as `docs/tr808-reference.md` describes it,
on this chip's parts: the bridged-T bodies (BD, SD, the toms) are modes of
the modal bank pinged by a pulse and left to ring; the metallic voices (CH,
OH, CB) are six square-wave oscillators through a band-pass mode, an
asymmetric "swing" VCA and a high-pass mode; the snare's snap and the clap
are one white-noise source through a high-pass or band-pass mode under an
envelope. Everything that plays is *envelope × source → a mode or the mix*,
so the hardware is one sequenced datapath — envelopes, sources, a routing
table on one multiplier — in front of the bank, and a kit is a table of
register values (15.7, Appendix G), not a circuit.

```
   stops[11], accent[11] ─► 18 envelopes ─┐           ┌──────────── modal bank, 16 modes ────────────┐
   LFSR ─► NOISE ─┐                      │            │ exc[m] ─► num (RAW | 1−z⁻² | (1−z⁻¹)²) ─► y[m] │
   6 squares ─► SQSUM, SQPAIR ─┼─► 23 paths: v = nl(src) · (ENV(e1)+ENV(e2)) >> (15+att) ─┼─► mix ─► body (Q4.15)
   PULSE ─────────┘  TAP m ◄───┼──────────────────────┴── y1[m] >> 3 ────────────────────┘
                               └─► dmix (the paths routed to MIX, 22 bits)
                                                 dmix, body ─► output stage (12), with the voice
```

### 15.1 Registers

The drum section's control image, host-written like the voice's (5.1);
addresses are 8 bits, values up to 32 (`drums_fx.write`). On the wire this
page is reached with `SEC` = 1 in the 48-bit control frame (5.4, DR 0007
revision 2); `rtl-sketch/drum_regs.v` holds the image (**3 232 flops** at
revision 10's sizes, 2 276 at revision 8's — DR 0007 section 9) and drives the
engine's buses. Every register the engine reads is
stable from the frame's `go` to `body_valid`, because the write drain runs at
cycles 2..5 and `go` is at cycle 8. Every value is
legal; nothing is rejected for range. Writes apply at frame boundaries by
4.3. An address that names no register is ignored.

| Address | Register | Width | Meaning |
|---|---|---:|---|
| `0x00` | `STOPS` | 11 | the stop mask; bit s is stop s (15.2) |
| `0x10 + s` | `ACCENT[s]` | 16 u | Q0.15 strike level of stop s; 32768 = 1.0, 65535 = 2.0 (15.3) |
| `0x20 + i` | `OSC_INC[i]` | 24 u | phase increment of square oscillator i = 0..5 (15.4) |
| `0x40 + 4e` | `ENV_CTL[e]` | 27 | `[3:0] stop`, `[7:4] choke`, `[15:8] hold`, `[17:16] bursts`, `[26:18] period` (15.3); a stop or choke index ≥ `N_STOPS` means never |
| `0x41 + 4e` | `ENV_PEAK[e]` | 24 u | Q0.24 level at a strike, before the accent |
| `0x42 + 4e` | `ENV_RATE[e]` | 16 u | Q0.16 decay rate, the voice's `rate` (8.3) |
| `0x90 + p` | `PATH[p]` | 25 | `[4:0] src`, `[9:5] e1`, `[14:10] e2`, `[16:15] nl`, `[19:17] att`, `[24:20] dest` (15.5) |
| `0xB0 + 4m` | `MODE_A1[m]` | 26 s | Q2.24 coefficient a1 = 2r·cos ω |
| `0xB1 + 4m` | `MODE_A2[m]` | 26 s | Q2.24 coefficient a2 = −r² |
| `0xB2 + 4m` | `MODE_AMP[m]` | 16 u | Q0.16 level of mode m in the body bus |
| `0xB3 + 4m` | `MODE_NUM[m]` | 2 | numerator: 0 RAW, 1 BP, 2 HP, 3 reads as RAW; effective on modes 0..`N_NUMS`−1 only (15.6) |
| `0xFF` | `RESET` | — | every register and state of this section to 15.8 |

e = 0..17, p = 0..22, m = 0..15. Sizes (`drums_fx.N_*`): **11 stops, 18
envelopes, 23 paths, 16 modes of which the first 11 (`N_NUMS`) carry a
numerator**, 6 oscillators.

**Revision 10 moved `PATH` and `MODE` and widened `PATH`, and neither was
cosmetic.** Eighteen envelopes span `0x40..0x87` and collide with `PATH` at
`0x80`; sixteen modes based at `0xC0` span `0xC0..0xFF`, so `MODE_NUM[15]`
would be `0xFF` — which is `RESET`. `drum_regs.v` decodes `RESET` as a
continuous assign outside the write decoder, so that is not a
decode-priority question and cannot be fixed by ordering: the address means
both things at once. The `PATH` word's envelope and destination fields went
from 4 bits to 5 for the same class of reason — at 4 bits only twelve
envelopes were addressable and `DEST_MIX` was 15, which at `MODES = 16` is
also mode 15, so the last mode could never be a path's destination. The
sentinels moved with the fields: **`ENV_FULL` = 31** reads full scale, any
other envelope index ≥ `N_ENV` reads zero, and **`DEST_MIX` = 31** is the mix
bus.

State registers, not host-writable except by
RESET: `stops_prev` (11), per envelope `level` (24), `strike` (24), `t`
(11), the six phases (24 each), the LFSR (31), and the bank's `y1[m]`,
`y2[m]`, `exc[m]` (21), `h1[m]`, `h2[m]` (21, modes 0..`N_NUMS`−1). The gains
`dvol`, `bvol` of the output stage (12) are the instrument's, 16 bits
unsigned each.

### 15.2 What happens in a frame

After step 1 of 4.2 (control applied), in this order (`DrumsFx.frame`):

1. **Fire.** `fire = STOPS & ~stops_prev`; `stops_prev ← STOPS`. A stop
   fires when its bit is 1 at the start of this frame and was 0 at the start
   of the previous one — the register's value, however many writes produced
   it: a held bit fires once, `1, 0, 1` in three consecutive frames fires
   twice, a rewrite of 1 over 1 does not fire, and two hits of one stop in
   consecutive frames cannot both fire (gf180-polysynth issue 7 §1's
   "0→1 between consecutive slices"; `test_stops_fire_on_the_edge_between_frames_only`).
2. **Envelopes** (15.3), every one, before the paths read them.
3. **Sources** (15.4): the LFSR advances 16 bits and yields `noise`; the six
   squares are read from their phases and the phases advance.
4. **Paths** (15.5), in order p = 0..15: each computes `v` and adds it to
   `dmix` or to `exc[dest]`. `TAP m` reads the bank's `y1[m]` as the
   previous frame's step 5 left it.
5. **The bank** (15.6): one step on `exc[0..11]`, producing `body`; every
   `exc[m]` is consumed and cleared.
6. `dmix` (22 bits) and `body` (19 bits) are this frame's buses for step 8
   of 4.2.

An implementation may schedule this across the frame however it likes,
provided the buses are identical; `drum_kit.v` takes 85 clocks (4.4).

### 15.3 Envelopes

Twelve identical exponential-decay envelopes (`drums_fx.EnvFx`). Registers
per 15.1; state `level` (24-bit unsigned), `strike` (24), `t` (11-bit frame
counter, saturating at 2047). Per frame, for envelope e with `fire` from
15.2:

```
if stop < 8 and fire[stop]:                                 fired
    level  ← usat24( (peak · ACCENT[stop]) >> 15 )          exact 40-bit product
    strike ← level ;  t ← 0
else:
    t ← min(t + 1, 2047)
    if t < hold:                                            held: level unchanged
    elif period ≠ 0 and t = k·period for some k in 1..bursts:
        strike ← (strike · 53248) >> 16 ;  level ← strike   re-strike at 13/16 of the last
    else:                                                   decay, the release rule of 8.3
        dec   ← (level · rate) >> 16
        level ← max(0, level − max(1, dec))
if choke < 8 and fire[choke]:  level ← 0                    choked, after everything above
ENV(e) = level >> 9                                         Q0.15, what the paths multiply by
```

`ENV(e)` is read *after* this frame's update: the fired frame reads the
strike, the next frame the first decay. A `hold` of H keeps the strike for
H frames (frames t = 0..H−1; H = 48 is the 808's 1 ms trigger pulse); with
`rate = 65535` the level then falls to 0 in three frames (5.5). Bursts:
`bursts` re-strikes, at `t = period, 2·period, 3·period`, each 13/16 of the
last (1.0, 0.8125, 0.660, 0.536 — the reference's 1.0 / ≈0.8 / ≈0.65 for
the clap); `period = 0` means no re-strike. A choke zeroes the level and
nothing else; a strike and a choke in one frame leave the level at 0.

**The dead zone is the voice's, closed the same way.** `dec` truncates to
zero below `level = 2^16 / rate`; without `max(1, ·)` the level would stall
there and the drum would never end — measured: it does
(`test_envelope_reaches_exactly_zero_and_the_dead_zone_is_closed`, `EnvFx(floor=False)`). With
it the tail below that floor is one LSB per frame to exactly zero; at 24
bits the floor is below −60 dBFS for every time constant up to 0.25 s
(`rate` ≥ 5) and −48 dBFS at `rate = 1` (a 1.4 s time constant). `rate = 0`
decays at one LSB per frame from any level (350 s from full). The negative
control `INJECT_BUG_DRUM_ENV_FLOOR` removes the `max(1, ·)` and is caught
by the bench's decay-to-silence segment.

### 15.4 Sources

Four source values are computed per frame, all Q1.15 signed:

- **NOISE** (`drums_fx.lfsr_frame`): a 31-bit LFSR `s ← (s << 1) | (s[30]
  xor s[15] xor s[17] xor s[19])`, the recurrence `b[n] = b[n−31] + b[n−16]
  + b[n−18] + b[n−20]` over GF(2), whose characteristic polynomial x³¹ +
  x¹⁵ + x¹³ + x¹¹ + 1 is primitive (2³¹ − 1 is prime, so irreducibility —
  `test_lfsr_polynomial_is_primitive…` checks x^(2³¹) ≡ x and no root — is
  enough): period 2³¹ − 1 bits. It is stepped **16 times per frame**; the
  frame's word is the 16 bits shifted in, oldest first, as signed Q1.15.
  Every bit is one output of a maximal-length sequence and the words are
  disjoint windows of it; reading the low bits of a once-per-frame LFSR
  (the audition's `dsp.lfsr_noise`) makes each word a shifted copy of the
  last, a one-pole low-pass, and is not this source. A pentanomial rather
  than the strawman's trinomial because a sparse feedback recovers slowly
  from the sparse reset state: x³¹ + x³ + 1 measured a +351 mean (a 0.5 %
  ones deficit) over the first 200 000 words, this polynomial −5
  (`test_noise_is_white_and_full_scale`). An implementation computes the
  16 new bits at once: bit i (i = 0 first) is the XOR of `s[t−i]` over the
  four taps, all from the old state since every tap is at bit 15 or above
  (`lfsr_frame_leap`). Reset state 1; frame 0's word is 1 (Appendix F).
  The 808 has one noise generator shared by every voice, and so does this
  section.
- **SQSUM**: six 24-bit phase accumulators, `phase[i] ← (phase[i] +
  OSC_INC[i]) mod 2²⁴` per frame after being read; square i is +5461 if
  `phase[i] < 2²³` else −5461; SQSUM is their sum, the 808's seven-level
  staircase, ±32766. The oscillators free-run and are never reset by a
  stop.
- **SQPAIR**: squares 4 and 5 at ±16383 each, ±32766: the 808's trimmed
  oscillators 5 and 6 summed *before* any gate. Retained for compatibility;
  the reference kit no longer uses it (15.7, DR 0010).
- **SQ i**, `src = 5 + i`, i = 0..5: square i **alone** at ±16383, the same
  step as SQPAIR's terms, so `SQ 4 + SQ 5` is SQPAIR term for term and
  splitting a voice into two paths costs no level. This is what the 808
  actually presents to its gates: reference 9 — "each oscillator has its own
  transistor gate (Q15, Q14)" — so a nonlinearity belongs on each square
  separately. `nl(a) + nl(b)` and `nl(a + b)` are not the same function, and
  the difference is audible (DR 0010).
- **PULSE**: the constant 32767. Shaped by an envelope it is the trigger
  pulse of the bridged-T voices.

### 15.5 Paths: the one multiplier, the nonlinearity, the tap, the buses

Sixteen paths, in order (`DrumsFx.frame`). Path p's word gives `src`,
`e1`, `e2`, `nl`, `att`, `dest`:

```
s   =  NOISE            src = 1        SQSUM  src = 2        PULSE  src = 3        SQPAIR  src = 4
       SQ i  src = 5 + i (i = 0..5, one square alone at +-16383)
       sat16(y1[m] >> 3) src = 16 + m  (TAP m, m = 0..11)     0      otherwise (0 = OFF, 11..15, 28..31)
s'  =  s                              nl = 0  LIN
       tanh( sat24( u << 5 ) )        nl = 1  SWING, u = s << 2 if s > 0 else s >> 3
       tanh( sat24( s << 5 ) )        nl = 2, 3  TANH
env =  ENV(e1) + ENV(e2)              ENV(15) = 32767, ENV(12..14) = 0;  0..65534
v   =  (s' · env) >> (15 + att)       exact 32-bit product, arithmetic shift; |v| ≤ 65532, 17 bits
dest = 15:  dmix ← dmix + v           dest < 12:  exc[dest] ← exc[dest] + v         12..14:  dropped
```

`tanh` is the ladder's (11.3, Appendix C): the Q1.15 value shifted to the
table's Q4.20 argument, so `TANH` maps 1.0 to tanh(1.0) = 0.762 and `SWING`
maps a positive full scale to 32766 (four times it is one LSB short of the
table's clamp, and the last bin interpolates to it) and a negative one to
tanh(−0.125) — ×4 on the positive half, ÷8 on the negative, the "swing
VCA" of the 808's hats, cymbal, cowbell and rimshot (reference 1.3; W14b's
fit). The negative control `INJECT_BUG_DRUM_TAP_NOSAT` wraps the tap
instead of railing it.

`TAP m` is a mode's state divided by 8, saturated to Q1.15: a resonator's
output rails at ±8.0 × full scale as an op-amp's does at its supply. It is
the only way a mode feeds a path (the hats' band-pass into their VCAs, the
clap's band-pass into its VCAs); `y1[m]` is read as the previous frame left
it, so a band-pass → VCA → high-pass chain has one frame of latency per
tap. `dmix` and every `exc[m]` are exact sums of at most 16 values of 17
bits: 21 bits, no clamp (`test_path_sums_are_exact_and_bounded`).

A path whose word is 0 is OFF and contributes nothing, so the reset image
is silent. `att` divides by 2^att.

### 15.6 The modal bank: excitation, numerators, output, timing

Twelve two-pole resonators (`ModalFx`, `modal_dp.v`), the sizing of rev 3
unchanged — coefficients Q2.24 signed (26 bits), `amp` Q0.16, state 28 bits
with 15 fraction bits, floor rounding in the recursion — with three
additions for the drum section. Per frame, for each mode m:

```
e   = exc[m]                                            21 bits, the sum of 15.5; then exc[m] ← 0
x   = e                                    RAW  (num = 0 or 3, or m ≥ 6)
    = e − h2[m]                            BP   (num = 1, m < 6)      the (1 − z⁻²) numerator
    = e − 2·h1[m] + h2[m]                  HP   (num = 2, m < 6)      the (1 − z⁻¹)² numerator
h2[m] ← h1[m] ;  h1[m] ← e                (m < 6)
acc = a1[m] · y1[m] + a2[m] · y2[m]        exact, no rounding constant
y   = sat28( (acc >> 24) + x )             shift, then clamp
y2[m] ← y1[m] ;  y1[m] ← y
mix += (y · amp[m]) >> 16
body = sat19( mix >> 0 )                   headroom 0: Q4.15, 19 bits (DR 0005's width)
```

The pre-differenced input turns a mode into the 808's band-pass (the hats'
7.1 kHz, the clap's 1.07 kHz, the cowbell's) or high-pass (the hats' 7.8
and 11.7 kHz, the snare's 2.75 kHz) filters at the cost of two 21-bit
history registers on six of the modes; the other six are the bodies and
need none. `test_numerators_reject_dc_and_the_resonator_passes_it` and
the negative control `INJECT_BUG_MODAL_NUM_HOLD` cover it. The `sat28` of
the state word is part of the arithmetic since rev 1 and is the analogue
of the ladder's; the 19-bit word is a width, reached only by register
values that put more than ±8.0 × full scale on the bus, never by the
reference kit at any accent (12).

**Excitation timing.** The excitation is not a port to be held: an
implementation MUST accumulate each mode's excitation into `exc[m]` while
the bank is idle (`modal_dp.v`: `exc_we`, `exc_mode`, `exc_val`), consume
every `exc[m]` exactly once in the frame's step and clear it. The
coefficient and `num` registers MUST be held from the frame's tick to
`body_valid`, as the ladder's coefficients are held while it is busy (11);
`tb_drums.v +jitter` shows the comparison detects a violation, and
`INJECT_BUG_MODAL_EXC_NOCLEAR` an excitation consumed twice.

**Presets.** The 808 bodies and filters of Appendix G are the bank's
*required* presets — the drums' bodies; the struck bar of
`ModalFx.coefficients(note)` (rev 3's four-mode preset, `physical.modal`'s
ratios 1 : 2.76 : 5.40 : 8.93, `headroom = 10`) is an *optional* family the
same hardware can carry, kept so that an area cut can take it without
touching the drums. A host may write any mode's coefficients on any frame
while it rings (the BD decay presets of reference 2 are three coefficient
pairs; `06-bd-decay-short-mid-long.wav`).

### 15.7 Host conversions and the reference kit (informative)

Host side, float, as 5.5 (`drums_fx`):

```
rate      = fit16( max(1, round((1 − exp(−1 / (τ · 48000))) · 2^16)) )     τ the amplitude 1/e time; τ ≤ 0 → 65535
accent    = fit16( round(level · 2^15) )                                   1.0 → 32768; 2.0 is the maximum
peak      = fit24( round(level · (2^24 − 1)) )
amp       = fit16( round(level · 2^16) )
OSC_INC   = fit24( round(f · 2^24 / 48000) )                               6.3's phase_inc
a1, a2    = fit26( round(2 r cos ω · 2^24) ), fit26( round(−r² · 2^24) )   r = exp(−π f0 / (Q · 48000)), ω = 2π f0 / 48000
```

`pole_regs(f0, Q)` reproduces every Q2.24 pair of `docs/tr808-reference.md`
§14 (`test_pole_regs_reproduce_the_808_reference_table`).

**The reference kit** (`drums_fx.kit_808`, Appendix G) is one 808 as the
reference tabulates it, later-unit snare. Every frequency, Q and time
constant is the reference's where it gives one; the rest is chosen and
says so:

| stop | what | modes (num) | envelopes | sourced | chosen |
|---|---|---|---|---|---|
| 0 BD | PULSE × 0.1 ms exponential → mode 6 (**49.4 Hz**, Q 22.3, DECAY 5.0); PULSE × 1 ms rectangle → MIX at 0.06 (the click); the host's 4 ms attack window retunes mode 6 to 130 Hz / Q 6 and back (15.7.1) | 6 (RAW) | 0, 1 | f0 **and** Q **and** the attack window, all reference 2 | the 0.1 ms kick as the pulse shaper's rising edge (reference 2, "what to implement"); no sigh, no tone filter (17.14) |
| 1 SD | PULSE × 0.1 ms → modes 7 (173 Hz, Q 16.3) and 8 (336 Hz, Q 9.9); NOISE × 15 ms → mode 3 (**BP** 2.75 kHz, Q 0.7) | 7, 8 (RAW), 3 (**BP**) | 2, 3 | both f0/Q, the snappy filter's pole, τ 15 ms | both bodies from the pulse, not the cascade (17.15); the snappy filter's **numerator**: reference 3 calls it a high-pass and the machine measures a band-pass on the same pole (17.22); SNAPPY level set to the knob's own curve at 5.0 |
| 2 LT, 3 HT | PULSE × 0.1 ms → mode 9 (90 Hz, Q 25) / 10 (185 Hz, Q 25); the host's diode pitch drop sweeps f0 from ×1.06 down over 60 ms, scaled by accent above a threshold and by the TUNING pot (15.7.1) | 9, 10 (RAW) | 4, 5 | f0, Q, **the pitch drop** (reference 4) | no pink-noise rumble (17.14) |
| 4 CH, 5 OH | SQSUM → mode 0 (BP 7117 Hz, Q 6, amp 0); TAP 0, SWING × envelope → mode 2 (HP 11.7 kHz, Q 2.5) / mode 1 (HP 7.8 kHz, Q 2.5); CH chokes OH | 0 (BP), 1, 2 (HP) | 6 (20 ms), 7 (150 ms, choke 4) | oscillators, BP, HPs, CH τ, the choke | OH τ 150 ms (DECAY mid) |
| 6 CP | NOISE → mode 4 (BP 1071 Hz, Q 1.6, amp 0); TAP 4, TANH × (3 bursts τ 4 ms every 480 frames + tail τ 47 ms at 0.32) → MIX | 4 (BP) | 8, 9 | BP, three bursts, τ 47 ms | period 480 = 10 ms, tail −10 dB (17.17) |
| 7 CB | **SQ 4 and SQ 5 on two separate paths**, each SWING × (τ 5 ms at 0.5 + **τ 100 ms** at 0.5) → mode 5 (BP **1100 Hz, Q 2.8**) | 5 (BP) | 10, 11 | oscillators 540/800 Hz, two-slope envelope, **one gate per oscillator** (reference 9, DR 0010) | nothing: the BP centre was 17.16 and is now fitted to a recording (1100 Hz Q 2.8), and the tail is the measured 98 ms |

Fourteen of the sixteen paths are used; mode 11 is spare (zero). Levels are
balanced by `drums_fx_render.py
--balance` so that each voice alone at accent 1.0 peaks at Roland's chart
proportions with the loudest at 0.5 × full scale on its bus
(`test_kit_voices_sit_at_the_chart_levels`); the bodies' exciters are 0.25 so
that an accent of 2.0 keeps the BD's state — the bank's loudest ring,
≈ 720 × its kick — under a quarter of the 28-bit rail. All eight stops in
one frame at accent 2.0 stay inside every width
(`test_bank_headroom_zero_and_nineteen_bits_hold_the_kits_loudest_hit`).
The reference host (`hit_writes`) writes a stop's accent and raises its
bit in the hit's frame and drops the bit in the next; `pattern_hits` turns
`engines.render_groove`'s step strings into hits.

#### 15.7.1 Coefficient sequences (informative, the host's)

Two of the reference's behaviours are changes **over time** to one mode's
coefficients, not settings a register image can hold. The block already
allows them — a host may retune any mode on any frame (15.6) — so they live
in the reference host (`drums_fx.hit_writes`, `bd_attack_writes`,
`tom_pitch_drop_writes`) and **Appendix G does not contain them**:

- **The BD attack window** (reference 2, W14a §8.1 / SN p.6): while Q43 is on
  it shorts R165, the foot resistance falls and f0 rises to ≈130 Hz at Q ≈ 6
  for ≈4 ms. It is the *same* resonator retuned, so the host writes mode 6's
  `a1`/`a2` at the hit and writes them back 192 frames (4 ms) later — four
  writes per hit.
- **The toms' diode pitch drop** (reference 4, SN text): with the germanium
  diodes conducting the foot resistance collapses and f0 starts above the
  small-signal value, relaxing back as the ring decays over 60 ms in six
  steps, two writes each. **HARDWARE-MEASURED** (#110, 99 clean-digital tom
  files of a real TR-808; `model/tom_pitch_probe.py`,
  `docs/tom-pitch-drop-measurement.md`), replacing the ×1.7 this section
  carried while the magnitude was marked *[inferred]*:

  | accent | n | onset f0 ÷ settled f0 | range over 11 tunings |
  |---|--:|--:|---|
  | no accent | 23 | **×1.063** | ×1.040 – ×1.094 |
  | accent | 33 | **×1.140** | ×1.085 – ×1.272 |
  | more accent | 33 | **×1.236** | ×1.169 – ×1.344 |

  **×1.7 occurs in none of the 99 files**, at any accent or tuning; the largest
  drop anywhere is ×1.344. The excess is

  ```
  excess = (TOM_DROP_RATIO − 1) · max(0, accent − A0)/(1 − A0_tom)
                                · exp(G · (f0/f0_nominal − 1))
  ```

  with `TOM_DROP_RATIO = 1.060` the measured onset ratio at a **stated**
  reference setting — accent 1.0, the TUNING pot at its centre, the TOM
  position of the circuit. Three terms because the measurement found three
  separate faults in the inferred law: the magnitude; the accent **clamp**,
  which gave an unaccented hit the *full* sweep where the machine gives it
  ×1.06 (germanium diodes do not conduct below a drive, so a soft hit does not
  sweep at all); and the **TUNING pot**, which the sequence ignored — LT at
  *More Accent* runs ×1.169 at 82 Hz and ×1.325 at 101 Hz.

  `A0` and `G` are the selected **position's**: the tom and conga halves of one
  circuit differ, and not because of frequency — HT and LC are both nominally
  185 Hz on the same bridged-T with a capacitor switched (§4 SW8) and
  unaccented their excesses are 0.061 and 0.0055, eleven times apart at the
  same pitch. The shape and the 60 ms are unchanged: the exponential beat a
  linear ramp in 88 of 89 measured rows and τ = 20 ms sits against a measured
  24.5 ms at *Accent*. The measured τ is itself accent-dependent (13 / 24.5 /
  33 ms) and this law is not — a known deviation, recorded in
  `docs/tom-pitch-drop-correction.md`.

  The write count does **not** depend on the accent: below the threshold the
  six steps still run, writing the settled coefficients, so a host's timing
  never depends on what it played.

A host that sequences coefficients itself passes `coef_seq=False` and gets
the bare register image. Neither sequence changes the block, the buses or
any width; both are visible to a bench only as ordinary register writes,
which is why `verify_drums.py`'s stimulus carries them.

### 15.8 Reset

RESET (`0xFF`, or hardware reset) sets every register of 15.1 and every
state register to 0, except the LFSR, which takes 1. Consequences: every
path is OFF, every mode has zero coefficients and amp, no stop can fire
(no bit is set), and both buses are 0 until the host writes a kit. The
output stage's `dvol` and `bvol` reset to 0 with the voice's `vol` (14).

### 15.9 Cycles and area (informative)

Measured at **revision 10's** sizes (`tb_drums.v`): the drum datapath takes 68
clocks per frame (1 + 18 envelopes + 1 + 2 × 23 paths + 2) and the bank 50
(3 × 16 + 2), **117 from tick to `body_valid`** with one overlapped — `tb_drums`
reports mean 117 and worst 117 over 191 560 frames. With the ladder's 24 that is
141 of the 256, before the voice's own front end, and `synth_top`'s `overrun`
flag stays clear.

AREA AT REVISION 10 IS NOT IN gf180 UNITS. The PDK was not installed on the
machine revision 10 was built on, so the figures below are **yosys generic
`synth`, no liberty**, revision-8 RTL and revision-10 RTL in the same flow, and
a cell count is not an area (rule 3):

| | cells | flip-flops |
|---|---:|---:|
| `drum_kit` rev 8 | 24 484 | 3 017 |
| `drum_kit` rev-10 RTL at rev-8 sizes | 24 885 | 3 023 |
| + `MODES` 12→16, `NUMS` 6→11 | 26 433 | 3 233 |
| + `ENVS` 18, `PATHS` 23, `STOPS` 11 | 31 972 | 4 186 |
| `drum_regs` rev 8 → rev 10 | 2 598 → 3 754 | 2 276 → 3 232 |
| whole drum section | 27 082 → 35 726 (+32 %) | 5 293 → 7 418 (+40 %) |

`modal_dp` alone is **1 656 flops at 12 modes / 6 nums and 1 866 at 16 / 11** —
the state arrays pad to a power of two, so `MODES` 9…16 cost the same and the
210 extra flops are `h1`/`h2` for the five modes that gained a numerator. It is
`NUMS`, not `MODES`, that moves the bank's state. `drum_regs`'s 3 232 agrees
with the register declarations of 15.1 to the bit.

THE FIGURES BELOW ARE REVISION 8's, in gf180mcu 7t `tt_025C_5v00` cell area
(`rtl-sketch/area/synth_area.py`), kept because nothing has re-measured them:
`drum_dp` is
0.278 mm² (10 716 cells, 1 355 flops), the 12-mode bank 0.367 mm² (13 845
cells, 2 076 flops; 0.370 with numerators on all twelve; 0.251 at 8 modes
with 4; 0.188 for rev 3's four with two), `drum_kit` 0.646 mm² (0.605 with
`synth -booth`) and 0.461 mm² at 8 modes / 8 envelopes / 12 paths. The
cost is state and its muxing, ≈120 bits per envelope and ≈100 per mode,
not the multipliers. Whether the product takes 8 or 12 modes is 17.13.

---

## 16. Verification obligations

For an implementation to be checked against the model it MUST expose, in
simulation:

1. **The sample stream**: a 16-bit signed output with a one-cycle valid
   strobe asserted exactly once per frame; the f-th strobe after reset carries
   sample f.
2. **The control input** at the register level, bypassing the physical layer,
   so a test bench controls exactly which frame each write completes in (4.3).
3. **The frame tick**.
4. Recommended taps, matching `VoiceFx.trace`: each `osc_k` and `inc_k`,
   `mixed`, `ae`, `fe`, `cut`, `kc`, `k_eff`, the ladder's 19-bit `y_out`,
   and `v`. The ladder alone is checkable through
   `rtl-sketch/verify_ladder.py`'s vector format (`x, g, k, gain, ogain` in,
   `y_out` out, `k` per sample), which drives 28 800 samples that reach
   every clamp and both coefficient MSBs.
5. **The drum section's two buses**, `dmix` and `body`, with `body_valid`
   once per frame, and its control input at the register level (15.1) so
   that a bench delivers each write to a chosen frame. `rtl-sketch/verify_drums.py`
   is the reference: the model's write stream (the kit of Appendix G, every
   stop soloed, the edge semantics of 15.2, all eight stops at accent 2.0,
   a bar with the hi-hat choke and a BD retune while ringing, register
   extremes on the last paths and the spare mode, a mid-run RESET, and
   decay into silence — 172 063 frames) applied by `tb_drums.v` to
   `drum_kit.v`, both buses compared every frame with no tolerance; the
   modal bank alone through `verify_modal.py` (57 600 samples: the bar,
   the numerators on noise and DC, coefficient extremes).

A test compares the implementation's sample f with the model's for every f,
for a scripted sequence of (frame, write) deliveries. Any mismatch is a
failure; there is no tolerance. In this revision the reference sequences are
single notes from reset — `VoiceFx().note(note, dur, **patch)` — and the
eight audition patches of `audition/patches.py::MONO` played through one
continuous voice by `render_mono_fx`, whose write lists (from `KeyHost`,
5.6) are the sequences; for the drum section, the write stream of
`verify_drums.stimulus`. A bench MUST also be shown to fail: the injected defects of
`rtl-sketch/ladder_dp.v` (`INJECT_BUG_LADDER_FB`, `_SAT`, `_TANH_CLAMP`) are
the pattern, and the voice's are `INJECT_BUG_VOICE_SQUARE_SIGN` (6.6.4),
`_ENV_FLOOR` (8.3), `_KEFF` (10.2), `_MIX_SAT` (7), `_GLIDE_FLOOR` (6.7),
`_RECIP_CLAMP` (6.6.1), `_TRIG_RESET` (8.5) and `_OUT_SAT` (12), each run on
the scenario of `rtl-sketch/verify_voice.py` that reaches it. A voice bench
MUST compare the taps of item 4 as well as the sample and MUST report an
undefined (X) output as a mismatch, never a pass: a sample-only comparison
is blind while the tail is quiet (with the release floor of 8.3 removed the
first tap to differ precedes the first sample to differ by 36 frames), and
`rtl-sketch/stubs/voice_dp_stub.v` — the ports with every output X — is the
run that shows the bench can tell X from wrong from right.

The drum section's are the same pattern: `modal_dp.v` carries `_SHIFT`,
`_SAT`, `_PREEXC`, `_NUM_HOLD`, `_EXC_NOCLEAR`, and `drum_dp.v` `_ENV_FLOOR` (no `max(1, ·)`), `_LEVEL_TRIG`
(level- not edge-triggered stops), `_LFSR_TAP`, `_TAP_NOSAT`, `_LAST_PATH`
(the strawman's dropped last drum), `_SQ_LONE` (a lone-square source that
returns the pair — revision 5's cowbell defect, 15.4), and `tb_drums.v`'s `+jitter` applies a
frame's writes while the datapath is busy, which the comparison MUST see
(the hold requirement of 15.6). Every one is required to fail by
`rtl-sketch/test_rtl.py`.

Table freshness: `spec/reference/gen_tables.py --check` MUST pass; it fails
if any hash in the appendices, any image under `spec/reference/tables/`, or
`rtl-sketch/tanh16.hex` is not what the model generates — since rev 5 that
includes the noise sequence of Appendix F and the kit of Appendix G.

Register widths: `test_every_host_conversion_fits_its_register` walks every
conversion of 5.5 over its input domain and fails if any result leaves the
width of 5.1; `test_every_legal_register_value_runs` walks the extremes of
every register through the model and fails if any legal value raises or
leaves the ranges stated here. A change to a width in 5.1 must change
`voice_fx.REG_BITS` and both tests with it.

---

## 17. Open items

Everything this revision does not decide, in one place. Each needs a decision
record that extends this document; none may be resolved by picking a reading.

1. **Note-on retrigger semantics** (8.5) — **closed in rev 3 by
   DR 0003**: GATE_ON and TRIG re-enter ATTACK from the current level; no
   phase or ladder reset; priority, single/multi trigger and paraphonic
   allocation are the host's, with the reference host's defaults informative
   (5.6).
2. **Glide** (6.7) — **closed in rev 3 by DR 0004**: on the chip, constant rate,
   geometric in the increment (linear in pitch), the `glide` register.
3. **Physical control layer** (5.4) — **closed in rev 4 by DR 0007, widened
   in DR 0007 revision 2**: SPI register writes, one 48-bit transaction per
   write of 5.2 carrying a page bit, an 8-bit address and a 32-bit datum
   (revision 1's 32-bit frame could carry 37 of the 155 writes the models
   perform; `rtl-sketch/verify_ctl.py` measures it), accepted at the
   synchronised `CS_N` rising edge and applied at the next tick; the
   encodings of every address and of `wave[k]` (saw 0, square 1, pulse25 2,
   tri 3, sine 4–7) are in 5.2 and DR 0007.
4. **Modal bank** (15) — **closed in rev 5 by DR 0008**: the bank is the
   drum section's bodies and filters; excitation is the drum paths'
   routed values accumulated per mode (15.5, 15.6), mixing is the output
   stage of 12 with `bvol`, gain staging is `headroom = 0`, a 19-bit word
   and the per-mode `amp`; sizing 12 modes / 6 numerators, still proposed.
5. **Resonance compensation above ~3 kHz** (11.5) — **closed in rev 3 by
   DR 0006**: `K_ROM32` (10.2, Appendix E); `res = 1` is the onset within
   0.39 %.
6. **Output gain staging** (12) — **closed in rev 3 by DR 0005**: a 19-bit
   ladder output, the VCA after the filter, the `vol` register and a hard
   rail.
7. **Register-width clamps in the host conversion** (5.5) — **resolved in
   rev 2**: every conversion clamps to its register width; where each clamp
   fires, and why `a_inc` and `rate` clamp rather than widen, is in 5.5. Rev
   3 adds `glide` and `vol` to the clamped set.
8. **Power-on control defaults** (14) — **closed in rev 4 by DR 0007**: all
   zero; a bare GATE_ON is silent; the MCU host writes the image at boot.
9. **`inc = 0`** (6.3) — **resolved in rev 2**: the oscillator stalls at DC
   with `c = 0` and `(e, r) = (0, 0)` (6.6.1); model-checked for every shape.
10. **Widths of `cut_lo`, `cut_hi`, `track_hz`** (5.1) — **closed in rev 4 by
    DR 0007**: 16 bits unsigned, fixed by the write format; the sum of
    section 10 is computed exactly at 19 bits before the clamp.
11. **Ratification itself.** This document is proposed. Ratification is the
    two-key act this fleet uses and is not claimed here.
12. **Self-oscillation tuning** (11.5, DR 0006, DR 0011): mostly closed.
    Revision 9 put Huovilainen's `fcr` polynomial and a constant trim into the
    g ROM and the worst error over 30 Hz .. 10 kHz went from 6.85 % to 0.90 %.
    What is left is the paper's two-dimensional caveat: one table cannot make
    both the zero-resonance corner and the self-oscillation frequency exact,
    and DR 0011 chose the latter at `res = 1.05`. The residual is also
    resonance-dependent — the trim was fitted at one operating point — and the
    top two octaves (16 kHz, the 21.6 kHz clamp) still run 1.2 .. 1.3 % flat.
13. **The cutoff registers are integer hertz** (5.1, 10): one LSB of
    `CUT_LO` / `CUT_HI` / `TRACK_HZ` is one hertz, and one hertz at 30 Hz is
    **53.7 cents** — 27 cents at 60 Hz, 10.2 at 200 Hz, 0.9 at 2 kHz, and a
    1.07 dB gain step at 30 Hz against a 24 dB/octave slope. The `g` ROM is
    **not** the limit here: it interpolates. This is a register-format
    question — a fractional-hertz or log-domain cutoff register are both
    plausible and the 48-bit frame has room in its 32-bit data field — and it
    is therefore a change to the control interface (DR 0007), not to the voice
    alone. Not taken in this revision.
14. **The `g` ROM's first bin.** Separately from 13, the ROM is EDGE sampled
    every 256 Hz and linearly interpolated, and `1 − exp(−x)` is concave, so
    below the first entry the chord runs under the curve: measured against its
    own target the ROM is **29 cents flat at 30 Hz, 20 at 60 Hz, 12 at 120 Hz**
    and under 1 cent above 1 kHz. DR 0011's polynomial and trim did **not** fix
    this — they changed the offset, not the interpolation — and against the
    COMMANDED cutoff the tuned ROM now reads 19 to 35 cents sharp below 200 Hz
    where the untuned one read 9 to 21 flat. Closing it is a ROM-size decision
    (128 → 256 entries is 2048 more ROM bits) or a non-uniform first bin, and
    both want the self-oscillation probe of DR 0011 re-run against them.
15. **Oscillator aliasing is the largest measured defect in the voice.**
    PolyBLEP removes about 15 dB of inharmonic energy uniformly (6.6, DR
    0001) and the acceptance suite proves it removes the predicted fold-back
    images — but against software references it is **19–32 dB behind Mini V3
    and 9–32 dB behind Surge on every waveform**, and ours **degrades with
    pitch** where Surge's is flat. Our own numbers, which were always in the
    suite's docstring and never had a target beside them, say the same thing:
    sawtooth inharmonic fraction **−42.7 dB at 82 Hz, −36.6 at 330 Hz, −31.0
    at 1.3 kHz, −28.5 at 2.6 kHz** — about 2.8 dB lost per octave, worst
    exactly where a lead line lives. Textbook-exact waveform shapes (all four
    match the closed form within 0.1 dB) with poor aliasing is the signature
    of a correct implementation of an insufficient method.

    **One option has been measured and is ruled out IN THAT FORM**, and since
    instrumented and explained (#80, `docs/oversampling-paradox.md`,
    `model/alias_probe.py`). Oversampling the oscillators and letting a
    last-sub-step decimation do the rest makes it **worse, not better**:
    sawtooth at 82 Hz goes −42.7 → **−33.1** at 2× and **−29.8** at 4×.

    The mechanism is now measured rather than inferred, and it is narrower than
    the sentence that used to stand here. **The oversampling itself works**: at
    96 kHz the corrected sawtooth reads −55.5 dB, the estimator's floor, 12.8 dB
    better than the base rate. The whole loss enters at the rate reduction, and
    it equals the share of the oversampled signal's power held in **harmonics
    above 24 kHz** — −33.1 dB predicted, −33.1 dB read, within 0.3 dB at every
    register. Those harmonics do not exist at 48 kHz; oversampling created them
    and dropping samples folded them down. PolyBLEP is not rate-mismatched (peak
    ratio 1.0000, window one sample per side at both rates), fixed point is not
    involved (float64 regresses identically), and an exact additive band-limited
    sawtooth through the same decimator reads −29.8 dB, **3.3 dB worse than
    ours**.

    Oversampling the oscillators is therefore **not** the cheap option; it is a
    decimation-filter decision. A float64 bound puts an 11-tap half-band at
    break-even with the shipped base-rate PolyBLEP and a 63-tap one at 6.9 dB
    better at 82 Hz and **18.2 dB better at 2.6 kHz** — the shape of this defect.
    No area number is attached; that belongs with an implementation.

    A sizing study of longer band-limited-step residuals (2, 4, 8, 16, 32
    correction samples) was built and **withdrawn**: it failed its own sanity
    check, reporting worse suppression at 32 samples than our 2-sample
    PolyBLEP achieves, which is impossible. No number from it is quoted. The
    remaining options — a higher-order PolyBLEP, a longer BLEP residual, or
    oversampling with a decimator — are each a different area cost and none
    has a number yet.
16. **The shark-tooth's saw share disagrees with a reference by one
    parameter** (6.4, `docs/minimoog-reference.md` W3). Drawing 1448's R030 /
    R031 give 10/57 = 0.175, load-independently; the reference-emulation
    comparison implies 0.25–0.30. Our odd harmonics match its target within
    0.6 dB and every even harmonic is uniformly 4.8 dB low, which is exactly
    what a smaller saw share looks like. Not changed on an emulation's
    evidence; settling it needs a real Model D or a second source for the two
    resistors.
17. **Raw white noise is uniform, not Gaussian** (6.10,
    `docs/minimoog-reference.md` N6a). A multi-bit LFSR slice is uniform by
    construction: kurtosis 1.80, crest 4.8 dB, against references at 2.23–2.64
    and 8.1–11.3. **Through the ladder it is not a defect** — measured at res
    0.7, crest 10.0–11.8 dB and kurtosis 2.54–2.92, every value inside the
    references' own span, because a four-pole low-pass Gaussianises. The
    residual case is a patch with the cutoff wide open and no resonance. The
    cheap fix was measured and rejected: summing k independent slices buys
    2.40 at k = 2 and 2.60 at k = 3, for two or three times the LFSR work and
    an adder tree, to reach what the filter already delivers.
18. **Per-unit oscillator drift is not modelled** (6.4,
    `docs/minimoog-reference.md` W3a). Our square is a true 50 % and has no
    even harmonics; a reference emulation measures 52 % with h2 at −24 dB.
    SM 2.3 is explicit that 50 % is the design and that Moog hand-selected
    R137 per unit to hit it, so 52 % is a unit out of trim rather than the
    instrument. Whether to model drift anyway — three oscillators beating
    against each other is part of the sound — is a musical decision and one
    constant.
19. **The drum section's size** (15.9) — **closed in rev 10** at 16 modes /
    11 with numerators / 18 envelopes / 23 paths / 11 stops, which is the
    complete TR-808: all sixteen named sounds on eleven circuits. Rev 8 asked
    which size the product takes; rev 10 answers "the one that plays the whole
    machine", at +32 % cells and +40 % flip-flops on the drum section (15.9).
    Whether the ladder and the bank share a multiplier
    (docs/area-budget.md 3.2) is still a budget decision, and the **gf180 area
    of the revision-10 configuration has not been measured** — the PDK was not
    installed where it was built.
20. **What the reference kit does not model** (15.7) — **mostly closed in
    rev 10**: the cymbal, rimshot, claves, maracas, the mid tom and the three
    congas are all in `kit_808()` and `preset_writes()` now, and the BD attack
    and both tom pitch drops have been coefficient sequences since rev 6.
    What is still not modelled: the **toms' pink-noise rumble** (no measured
    level exists for it), the **BD tone low-pass**, the cymbal's third
    high-pass (reference 10's Hh1) and its +6 dB/oct output tilt, and the
    maracas' 18 ms attack ramp — the envelope generator has no rising segment
    and its `hold` tops out at 5.3 ms.
21. **The snare's cascade** (15.7): the 808 drives the high resonator from
    the low one's output ×1/38; the kit drives both from the pulse.
22. **The cowbell's band-pass centre** (15.7) — **closed in rev 6 by
    DR 0010**: fitted to a recording of the reference unit, 16 identified
    partials with the duty cycle and the two gates' relative level free:
    **1100 Hz, Q 2.8**, rms residual 2.8 dB. Sound On Sound's 2.64 kHz is
    refuted; the reference's own 0.9 kHz is ~200 Hz low with the Q too high.
23. **The clap's burst period and tail ratio** (15.7): 480 frames (10 ms)
    and −10 dB are the reference's bounds, not measurements.
24. **Per-unit oscillator tuning** (15.4): the four untrimmed 808
    oscillators vary by tens of percent between units; the kit uses the
    schematic's nominal values. A host models a unit by writing `OSC_INC`.
25. **The reference drum gains** (12): at `dvol = bvol = 14746` (0.45, the
    voice's reference) the combined render clips 65 samples where all
    eight stops land accented under a bass note; at 0.30 it clips 5, and
    all eight stops in one frame at accent 1.4 clip 5 on their own (rev 5
    measured 68 and 24; the kit's levels moved in rev 6, and the body bus
    peaks 2.37 × full scale of the word's 8.0 where it peaked 2.8). The
    rail is the host's to manage (DR 0005); a reference value for the two
    gains is not decided.
26. **The excitation is an impulse where the machine's is a shaped pulse**
    (15.5, 15.7). Every bridged-T voice is struck with `PULSE` under a
    0.1 ms exponential — effectively an impulse — where the 808's pulse
    shaper produces a positive kick at t = 0 and a clamped negative kick
    1 ms later (reference 2, "what to implement"). Reference 2 gives the
    recipe (a 1-pole high-pass with τ ≈ 0.1 ms and a one-sided clamp) and
    this revision does not implement it. Two independent measurements say
    this is the largest remaining difference: the reference unit's BD puts
    41.2 % of its first 4 ms in 80–150 Hz against our 22.3 % *with* the
    attack window of 15.7.1 and 2.7 % without it, and a discrimination
    study over the whole kit finds the attack, not the body, carries most
    of the separability on every voice — including after every fix this
    revision makes. **This is the next thing to do to the drum section**,
    and it is a change to the sources of 15.4, not to the kit.
27. **What the kit still does not match on the reference unit** (15.7).
    Recorded rather than tuned away, because the rule is that the
    reference document wins over a single machine (17.18): the BD's body
    rings at the circuit table's τ = 144 ms where the unit measures
    178.0 ± 29.1 ms (+23 %, inside the ±50 % on Q that 15.7 says is normal
    between units); the BD attack window closes about half of the first
    4 ms band-energy gap and not all of it (20); the hats are ≈6 % bright
    and their filters too selective; the clap's burst period is 10.0 ms
    against the unit's 12.3 ms; the toms have no pink-noise rumble (14).
28. **The snappy filter's numerator is the measurement's, not the
    reference document's** (15.7). Reference 3 describes the snare's noise
    path as a 2-pole **high-pass** at 2.75 kHz, Q 0.7. On that pole a
    high-pass numerator is flat to Nyquist, and the reference unit's noise
    — recovered as the residual after subtracting the two body modes —
    peaks at 3–5 kHz and falls above, with 2.9 % of its energy above
    12 kHz. The same pole read as a **band-pass** fits it to 1.9 dB
    weighted rms against the high-pass's 5.2 dB, so rev 6 changes the
    numerator and keeps the reference's f0 and Q exactly. Whether the
    schematic supports that reading, or whether a further stage the
    walk-through missed does the band-limiting, is not settled;
    `docs/tr808-reference.md` §3 carries the amendment.
29. **The chip does not yet carry this drum section** (12, 15):
    `rtl-sketch/synth_top.v` instantiates `drum_section_placeholder` — the
    modal bank alone on a single 19-bit bus, no sources of its own — and its
    master mix is the two-term `sat16(((v · vol) >> 15) + ((d · dvol) >> 15))`
    of `docs/ARCHITECTURE.md` section 4, not the one exact sum of 12.
    `drum_kit.v` is verified bit-exact against `model/drums_fx.py`
    standalone (`rtl-sketch/verify_drums.py`) and the placeholder is
    verified through the pins (`rtl-sketch/verify_top.py`), but nothing
    verifies the two joined. Replacing the placeholder with `drum_kit` and
    the mix with 12's formula, and re-running `rtl-sketch/headroom_check.py`
    and the area flow on the result, is unscheduled work, not a decision.
30. **The snare's two partials are balanced by MEASUREMENT, not from the
    schematic** (15.7). Roland states that VR8 TONE sets "the output ratio of
    the two" bridged-T resonators, and the reference unit at TONE 5.0 puts the
    336 Hz partial at 1.42× the 173 Hz one — the same figure with the snappy
    path up (1.43) and down (1.41), which is what says the quantity belongs to
    the resonators and not to the noise. Rev 7 writes that ratio. What is
    *not* solved is the divider that produces it: reference 3 reads IC14a's
    output into IC14b through R191/R192 at ≈1/38 and sums the two through VR8
    with R200 shorted by the 1983 design change, and nobody has computed the
    resulting ratio from those values. The number is right because it was
    measured; the circuit explanation is open.
31. **The snappy envelope's rate is MEASURED, and disagrees with the
    reference's RC by 2×** (15.7). Reference 3 gives the snare's noise
    envelope as C51 0.47 µF charged through R186 33 kΩ, τ ≈ 15.5 ms — and
    that is the **charge** path. The machine's burst measures T20 63–78 ms
    over six files (τ ≈ 30 ms), where 15 ms gives 34 ms, so either the
    discharge path is not R186 or Q48's VCA law stretches the envelope it
    sees. Rev 7 writes the measured 30 ms. Reference 3's own prose already
    says the snap is "a 30–40 ms burst", which the measurement agrees with
    and the RC does not; which of the two mechanisms accounts for it is not
    settled. `docs/drum-verification.md` §8.6 carries the measurement.

---

## 18. Revision history

- **Rev 1 (2026-09-17)** — initial proposal, written from `model/voice_fx.py`
  and `model/fixed.py` as committed; appendices generated by
  `spec/reference/gen_tables.py`. Not ratified.
- **Rev 2 (2026-09-17)** — resolves 17.7 and 17.9. Every host conversion of
  5.5 clamps to its register width of 5.1: `a_inc` to 2^24 − 1 below two
  frames of attack; `rate` to 65535 at or below 7.072 µs of release and at
  `release_s = 0`, which the rev-1 model divided by (a third raise the rev-1
  prose had not recorded); also `inc` (≥ 48 kHz), `k` (`res` ≥ 2), `gain`
  (`drive` ≥ 6.152), `sus` (`sustain` > 1), and a zero-sum mix, which also
  divided by zero. `inc = 0` is defined and model-checked: the oscillator
  stalls at DC, `(e, r) = (0, 0)` (6.3, 6.6.1) — here the rev-1 prose was
  right and the model was wrong. The model names the ladder registers
  (`LadderFx.regs`) and accepts them directly, and the whole control image
  is walked at its extremes (5.1, 16). Five tests added (45). No table,
  hash or reference sequence changed. Not ratified.
- **Rev 3 (2026-09-17)** — resolves 17.1, 17.2, 17.5 and 17.6. DR 0003
  (note-on: GATE_ON and TRIG re-enter ATTACK from the current level, one
  continuous voice, no phase or ladder reset, the reference host), DR 0004
  (glide: constant rate on the chip, the `glide` register), DR 0005 (gain
  structure: 19-bit ladder output, the VCA after the filter, the `vol`
  register, a hard rail), DR 0006 (resonance compensation: `K_ROM32`,
  Appendix E, `k_eff` per frame). `glide` and `vol` join the clamped
  conversions of 5.5 and the extremes walk of 16. One table added; every
  reference sequence's values change (the chain order, the volume, the
  compensation). Rev 1 and 2 were proposed, not frozen, so their text is
  revised rather than extended. Not ratified.
- **Rev 4 (2026-09-17)** — resolves 17.3, 17.8 and 17.10 by DR 0007 (the
  control interface: SPI register writes applied at the frame tick, the
  register map and the `wave` encoding, all-zero power-on defaults, 16-bit
  cutoff registers; RESET leaves the link and queue alone). No arithmetic,
  table, hash or reference sequence changes. The voice is now implemented
  (`rtl-sketch/voice_dp.v`) and verified bit-exact against the model at the
  register port by `rtl-sketch/verify_voice.py`: 255 060 frames over 24
  scenario segments (every waveform, every NOTE_INC entry and the increments
  where 5.5's clamps fire, glide up, down and at its limits, GATE_ON / TRIG /
  GATE_OFF in every segment, a release to exactly zero, paraphonic keys, the
  register extremes, three of the audition reference sequences), every sample,
  every tap of 16.4 and the final state; eight injected defects (16) each
  caught, and an all-X stub caught. The worst frame measured is 136 cycles
  from `go` with the drum filter off (the all-maximum image: three reciprocals
  and both PolyBLEP windows on every edge). The chip around it is
  `docs/ARCHITECTURE.md`. Not ratified.
- **Rev 11 (2026-09-19)** — reconcile the pinned image with the measured
  tom correction already merged in #154 (section 15.7.1). **KIT808 moves from
  `feb8c6fd…` to `a43fe2a7…`, still 147 writes.** Exactly three registers
  differ: MODE_AMP[11] at 0xDE, 0x201 → 0x13B; MODE_AMP[12] at 0xE2,
  0x296 → 0x196; MODE_AMP[13] at 0xE6, 0x42C → 0x28B. These are the
  LT/MT/HT level reductions documented in `drums_fx.AMP_TOM`: the corrected
  sweep removes the old detuning loss, so the same balance needs less drive.
  The prior merge changed the model and this section's host law but left the
  generated image and hash pins stale. Every other table is unchanged.
  `test_revision_11_changes_only_the_three_documented_tom_levels` reconstructs
  the revision-10 hash by reversing only those three writes, so updating the
  new pin cannot conceal an unrelated register change. This revision changes
  no model or RTL arithmetic, interface, schedule, or width. Not ratified.

- **Rev 10 (2026-09-18)** — **the complete TR-808: all sixteen named sounds on
  eleven circuits.** No width, clamp or formula of the VOICE changes; what
  changes is the drum section's sizes, the PATH word, the drum page's address
  map and the mix bus's width.

  **One pinned table moves, loudly: KIT808**
  `7ea9a2e3…` → `feb8c6fd…`, **100 → 147 writes**. Every other hash is
  byte-identical, G_ROM128 and K_ROM32 (which revision 9 moved) and TANH16_ROM
  included — `spec/reference/test_tables.py` checks exactly that against
  revision 9's pins before accepting the new one, so re-pinning cannot hide a
  second table moving at the same time. This is the kit's third move and the
  first that is not a refit of a voice already there: it is six more sounds.
  - **Sizes** (15.1, 15.9): 8 → **11 stops**, 12 → **18 envelopes**, 16 → **23
    paths**, 12 → **16 modes**, `N_NUMS` 6 → **11**. The first eight stops keep
    their indices, so every revision-8 register image still means the same
    thing. `modal_dp` gives a numerator only *below* `NUMS`, so `NUMS` is the
    register that decides how many filters the bank can hold — and it, not
    `MODES`, is what moves the bank's state (1 656 flops at 12/6, 1 866 at
    16/11).
  - **The PATH word is 25 bits** (15.5), envelope and destination fields 5 bits
    each. At 4 bits only twelve envelopes were addressable and `DEST_MIX` was
    15 — which at `MODES = 16` is also mode 15, so the last mode could never be
    a destination. `ENV_FULL` and `DEST_MIX` are both 31 now.
  - **`PATH` moved 0x80 → 0x90 and `MODE` 0xC0 → 0xB0** (15.1). Eighteen
    envelopes run to 0x87 and collide with PATH at 0x80; sixteen modes based at
    0xC0 put `MODE_NUM[15]` on **0xFF, which is RESET**, and `drum_regs.v`
    decodes RESET outside the write decoder so the address would have meant
    both things at once. `INJECT_BUG_DRUM_RESET_ALIAS` is that defect kept as a
    negative control.
  - **`dmix` is 22 bits** (12, 15.5): 23 paths × 17 bits no longer fits 21.
    `voice_dp`'s port widens with it; nothing else in the voice moves.
  - Bit-exact against `model/drums_fx.py` over 191 560 frames, 117 clocks per
    frame of the 256; `verify_ctl` and `verify_synth_top` re-run green, the
    latter also at its pins with the new image.

- **Rev 8 (2026-09-18)** — **the control frame, because it could not carry
  the register image this contract specifies.** No pinned table moves, no
  width, bus, clamp or formula of the audio path changes, and every rev-7
  reference sequence is unchanged; what changes is 5.2 and 5.4, the transport,
  and one addition to 12's registers.
  - **The transaction is 48 bits, `{F, 6'b0, SEC, A[7:0], D[31:0]}`** (DR 0007
    **revision 2**), where rev 3 to rev 7 all said 32 bits carrying a 7-bit
    address and a 24-bit datum. 15.1 has specified the drum image as an 8-bit
    address space with values up to 32 bits since rev 5, and DR 0007 reserved
    64 addresses for a block that needs 117: the two were never compatible.
    Measured on the two models' own writes — the voice patch image from
    `voice_fx.patch_regs()` and Appendix G's kit — **118 of 155 writes are
    corrupted** by the 32-bit frame: 118 drum writes have no page to land in,
    67 addresses do not fit in 7 bits (`A_PATH` 0x80, `A_MODE` 0xC0,
    `A_RESET` 0xFF) and 26 data do not fit in 24 (`ENV_CTL` is 27 bits,
    `MODE_A1`/`A2` 26). `rtl-sketch/verify_ctl.py` is the bench that measures
    it and `rtl-sketch/stubs/spi_ctl_dr7rev1.v` keeps the old receiver so the
    number stays reproducible.
  - **`SEC` selects the page**: 0 the voice and master map of 5.2, 1 the drum
    map of 15.1. Both maps are byte-for-byte what they already were, so
    **Appendix G's hash does not move for this** and its 100 writes are sent
    unchanged. RESET is per page (0x23 and 0xFF).
  - **`BVOL` gains an address, 0x2C.** The output stage of 12 has named two
    drum gains since rev 5 (`dvol` for `dmix`, `bvol` for `body`); the
    register map had one. The arithmetic of 12 is untouched.
  - The status word's VERSION field reads 0x2 (it names DR 0007's map, and
    the map changed).
  Nothing here was reachable from a bench before: every bench in the
  repository drove the register WRITE PORT, not the link, which is why both
  sides could be bit-exact against their models and still not be connectable.
  `rtl-sketch/verify_synth_top.py` now compares the I2S wire against
  `model/synth_top_model.py` end to end. Not ratified.
- **Rev 7 (2026-09-18)** — the snare, from the same recordings rev 6 used
  and by the same validated separator. **A PINNED TABLE CHANGES AGAIN:
  Appendix G (KIT808) moves, and only it.** No other hash moves; no width,
  bus, clamp or formula changes; the write COUNT is unchanged at 100. Three
  values move inside it:
  - **The two body modes' amplitude ratio** becomes the machine's measured
    1.42 (upper over lower, +3.0 dB at TONE 5.0) where rev 6 shipped 0.394
    (−8.1 dB): 11 dB of the snare's upper partial was missing. The pair is
    scaled together so the voice's peak is unchanged at 0.46 FS (17.24).
  - **The snappy envelope's rate** becomes the measured τ = 30 ms where rev 6
    shipped reference 3's RC of 15 ms; the burst's T20 goes 34 → 72 ms
    against the machine's 63–78 (17.25).
  - **The snappy envelope's peak** falls to 0.3046, which holds the noise
    share at the machine's 27.7 % now that the rate is longer. Rev 6's band
    and numerator are untouched and still measure right.
  Rev 6's `06f47f30…b869914a` is kept in `spec/reference/test_tables.py` as
  the literal it stated, so the second move of this table is as visible as
  the first. Open items 24–25 added. Not ratified.
- **Rev 6 (2026-09-18)** — the drum section measured against a real TR-808
  (`docs/drum-verification.md`), and three faults fixed. **A PINNED TABLE
  CHANGES: Appendix G (KIT808) is
  `06f47f30…b869914a`, where rev 5 stated `819ef081…db66b3dc`, and it now
  holds 100 writes rather than 99.** No other hash moves — NOTE_INC,
  SINE_Q256, SINE_FULL1024, TANH16, TANH16_ROM, G_ROM128, K_ROM32 and
  NOISE64 are byte-identical, and only `spec/reference/tables/kit808.hex`
  is rewritten. Nothing about the *arithmetic* changes: no width, no bus,
  no clamp, no formula, and every rev-5 reference sequence that does not
  use the kit is unchanged. What changes is the kit's contents and one
  source encoding:
  - **15.4 gains `SQ i` (src 5..10)**, one square alone at ±16383, because
    reference 9 says each of the cowbell's oscillators has its own gate and
    `nl(a) + nl(b)` is not `nl(a + b)`. `drum_dp.v` carries the decode and
    `_SQ_LONE` is the control that shows a bench sees it (DR 0010).
  - **The cowbell** takes two separately-gated paths, its tail the measured
    τ = 98 ms rather than 30, and its band-pass the fitted 1100 Hz / Q 2.8
    rather than the chosen 900 Hz / Q 4, closing 17.16.
  - **The snare's snappy filter keeps the reference's pole and changes its
    numerator** to BP (17.22), and its level is set to the SNAPPY knob's own
    measured curve at 5.0.
  - **The bass drum's f0 becomes the reference circuit's 49.4 Hz** rather
    than Roland's chart's 56 (DR 0009). Its **Q table is untouched** — the
    kit already shipped reference 2's Q = 22.3, and that table's τ column is
    only self-consistent at 49.4 Hz, so the "45 % short decay" was the pitch
    error propagating through τ = Q/(π f0) and not a decay fault at all.
  - **15.7.1 (new)**: the BD's 4 ms attack window and the toms' diode pitch
    drop, as reference-host coefficient sequences. They are writes, not
    hardware, and Appendix G does not contain them.
  Open items 20–23 added, 16 closed. Not ratified.
- **Rev 5 (2026-09-18)** — resolves 17.4 by DR 0008: the drum section
  (section 15, rewritten; `model/drums_fx.py`) and the modal bank as one
  instrument — eight edge-triggered stops with accents, twelve envelopes on
  the voice's release rule with hold, bursts and choke, sixteen routing
  paths on one multiplier, a 31-bit pentanomial LFSR, six square oscillators, the swing
  VCA on the ladder's tanh table, and the bank grown to twelve modes with
  numerators on six, a 19-bit word, per-mode accumulated excitation and a
  stated hold requirement on its coefficients. The output stage (12) sums
  the two drum buses with the voice under `dvol`, `bvol` before the one
  rail — rev 3's formula bit for bit at zero gains, so no rev-3 or rev-4
  reference
  sequence changes. Two tables added (Appendices F, G); no existing table
  or hash changed. Open items 13–20 added. Not ratified.

---

<!-- BEGIN GENERATED APPENDICES -->

### Appendix A -- NOTE_INC: MIDI note number -> 24-bit phase increment

Normative. `NOTE_INC[n] = round(440 * 2^((n-69)/12) * 2^24 / 48000)`, evaluated by `dsp.phase_inc(dsp.note_hz(n))`. Nominal frequency for reference only. Two notes per row.

| note | inc (dec) | inc (hex) | nominal Hz | | note | inc (dec) | inc (hex) | nominal Hz |
|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 0 | 2858 | 0x000B2A | 8.176 | | 64 | 115213 | 0x01C20D | 329.628 |
| 1 | 3028 | 0x000BD4 | 8.662 | | 65 | 122064 | 0x01DCD0 | 349.228 |
| 2 | 3208 | 0x000C88 | 9.177 | | 66 | 129322 | 0x01F92A | 369.994 |
| 3 | 3398 | 0x000D46 | 9.723 | | 67 | 137012 | 0x021734 | 391.995 |
| 4 | 3600 | 0x000E10 | 10.301 | | 68 | 145160 | 0x023708 | 415.305 |
| 5 | 3815 | 0x000EE7 | 10.913 | | 69 | 153791 | 0x0258BF | 440.000 |
| 6 | 4041 | 0x000FC9 | 11.562 | | 70 | 162936 | 0x027C78 | 466.164 |
| 7 | 4282 | 0x0010BA | 12.250 | | 71 | 172625 | 0x02A251 | 493.883 |
| 8 | 4536 | 0x0011B8 | 12.978 | | 72 | 182890 | 0x02CA6A | 523.251 |
| 9 | 4806 | 0x0012C6 | 13.750 | | 73 | 193765 | 0x02F4E5 | 554.365 |
| 10 | 5092 | 0x0013E4 | 14.568 | | 74 | 205287 | 0x0321E7 | 587.330 |
| 11 | 5395 | 0x001513 | 15.434 | | 75 | 217494 | 0x035196 | 622.254 |
| 12 | 5715 | 0x001653 | 16.352 | | 76 | 230426 | 0x03841A | 659.255 |
| 13 | 6055 | 0x0017A7 | 17.324 | | 77 | 244128 | 0x03B9A0 | 698.456 |
| 14 | 6415 | 0x00190F | 18.354 | | 78 | 258645 | 0x03F255 | 739.989 |
| 15 | 6797 | 0x001A8D | 19.445 | | 79 | 274025 | 0x042E69 | 783.991 |
| 16 | 7201 | 0x001C21 | 20.602 | | 80 | 290319 | 0x046E0F | 830.609 |
| 17 | 7629 | 0x001DCD | 21.827 | | 81 | 307582 | 0x04B17E | 880.000 |
| 18 | 8083 | 0x001F93 | 23.125 | | 82 | 325872 | 0x04F8F0 | 932.328 |
| 19 | 8563 | 0x002173 | 24.500 | | 83 | 345249 | 0x0544A1 | 987.767 |
| 20 | 9072 | 0x002370 | 25.957 | | 84 | 365779 | 0x0594D3 | 1046.502 |
| 21 | 9612 | 0x00258C | 27.500 | | 85 | 387529 | 0x05E9C9 | 1108.731 |
| 22 | 10184 | 0x0027C8 | 29.135 | | 86 | 410573 | 0x0643CD | 1174.659 |
| 23 | 10789 | 0x002A25 | 30.868 | | 87 | 434987 | 0x06A32B | 1244.508 |
| 24 | 11431 | 0x002CA7 | 32.703 | | 88 | 460853 | 0x070835 | 1318.510 |
| 25 | 12110 | 0x002F4E | 34.648 | | 89 | 488256 | 0x077340 | 1396.913 |
| 26 | 12830 | 0x00321E | 36.708 | | 90 | 517290 | 0x07E4AA | 1479.978 |
| 27 | 13593 | 0x003519 | 38.891 | | 91 | 548049 | 0x085CD1 | 1567.982 |
| 28 | 14402 | 0x003842 | 41.203 | | 92 | 580638 | 0x08DC1E | 1661.219 |
| 29 | 15258 | 0x003B9A | 43.654 | | 93 | 615165 | 0x0962FD | 1760.000 |
| 30 | 16165 | 0x003F25 | 46.249 | | 94 | 651744 | 0x09F1E0 | 1864.655 |
| 31 | 17127 | 0x0042E7 | 48.999 | | 95 | 690499 | 0x0A8943 | 1975.533 |
| 32 | 18145 | 0x0046E1 | 51.913 | | 96 | 731558 | 0x0B29A6 | 2093.005 |
| 33 | 19224 | 0x004B18 | 55.000 | | 97 | 775059 | 0x0BD393 | 2217.461 |
| 34 | 20367 | 0x004F8F | 58.270 | | 98 | 821146 | 0x0C879A | 2349.318 |
| 35 | 21578 | 0x00544A | 61.735 | | 99 | 869974 | 0x0D4656 | 2489.016 |
| 36 | 22861 | 0x00594D | 65.406 | | 100 | 921705 | 0x0E1069 | 2637.020 |
| 37 | 24221 | 0x005E9D | 69.296 | | 101 | 976513 | 0x0EE681 | 2793.826 |
| 38 | 25661 | 0x00643D | 73.416 | | 102 | 1034579 | 0x0FC953 | 2959.955 |
| 39 | 27187 | 0x006A33 | 77.782 | | 103 | 1096099 | 0x10B9A3 | 3135.963 |
| 40 | 28803 | 0x007083 | 82.407 | | 104 | 1161276 | 0x11B83C | 3322.438 |
| 41 | 30516 | 0x007734 | 87.307 | | 105 | 1230329 | 0x12C5F9 | 3520.000 |
| 42 | 32331 | 0x007E4B | 92.499 | | 106 | 1303488 | 0x13E3C0 | 3729.310 |
| 43 | 34253 | 0x0085CD | 97.999 | | 107 | 1380998 | 0x151286 | 3951.066 |
| 44 | 36290 | 0x008DC2 | 103.826 | | 108 | 1463116 | 0x16534C | 4186.009 |
| 45 | 38448 | 0x009630 | 110.000 | | 109 | 1550118 | 0x17A726 | 4434.922 |
| 46 | 40734 | 0x009F1E | 116.541 | | 110 | 1642292 | 0x190F34 | 4698.636 |
| 47 | 43156 | 0x00A894 | 123.471 | | 111 | 1739948 | 0x1A8CAC | 4978.032 |
| 48 | 45722 | 0x00B29A | 130.813 | | 112 | 1843411 | 0x1C20D3 | 5274.041 |
| 49 | 48441 | 0x00BD39 | 138.591 | | 113 | 1953026 | 0x1DCD02 | 5587.652 |
| 50 | 51322 | 0x00C87A | 146.832 | | 114 | 2069159 | 0x1F92A7 | 5919.911 |
| 51 | 54373 | 0x00D465 | 155.563 | | 115 | 2192197 | 0x217345 | 6271.927 |
| 52 | 57607 | 0x00E107 | 164.814 | | 116 | 2322552 | 0x237078 | 6644.875 |
| 53 | 61032 | 0x00EE68 | 174.614 | | 117 | 2460658 | 0x258BF2 | 7040.000 |
| 54 | 64661 | 0x00FC95 | 184.997 | | 118 | 2606977 | 0x27C781 | 7458.620 |
| 55 | 68506 | 0x010B9A | 195.998 | | 119 | 2761996 | 0x2A250C | 7902.133 |
| 56 | 72580 | 0x011B84 | 207.652 | | 120 | 2926232 | 0x2CA698 | 8372.018 |
| 57 | 76896 | 0x012C60 | 220.000 | | 121 | 3100235 | 0x2F4E4B | 8869.844 |
| 58 | 81468 | 0x013E3C | 233.082 | | 122 | 3284585 | 0x321E69 | 9397.273 |
| 59 | 86312 | 0x015128 | 246.942 | | 123 | 3479896 | 0x351958 | 9956.063 |
| 60 | 91445 | 0x016535 | 261.626 | | 124 | 3686822 | 0x3841A6 | 10548.082 |
| 61 | 96882 | 0x017A72 | 277.183 | | 125 | 3906052 | 0x3B9A04 | 11175.303 |
| 62 | 102643 | 0x0190F3 | 293.665 | | 126 | 4138318 | 0x3F254E | 11839.822 |
| 63 | 108747 | 0x01A8CB | 311.127 | | 127 | 4384395 | 0x42E68B | 12543.854 |

SHA-256 of the 128 decimal values joined by commas (no spaces): `e771e6b7b39d3941c471b772bfb5cdca398b78ee7fa964c3c90388d2cc888ba4`

### Appendix B -- SINE_Q256: quarter-wave sine table, i = 0..255

Normative. `SINE_Q256[i] = round(32767 * sin(pi/2 * (i + 0.5) / 256))` -- MIDPOINT sampled, 256 entries, no interpolation (`dsp._QUARTER`). The full 1024-entry table is derived by the symmetry rules in section 6.5. Eight entries per row; the first column is the index of the first entry in the row.

| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 101 | 302 | 503 | 704 | 905 | 1106 | 1307 | 1507 |
| 8 | 1708 | 1909 | 2110 | 2310 | 2511 | 2711 | 2911 | 3112 |
| 16 | 3312 | 3512 | 3712 | 3911 | 4111 | 4310 | 4509 | 4708 |
| 24 | 4907 | 5106 | 5305 | 5503 | 5701 | 5899 | 6096 | 6294 |
| 32 | 6491 | 6688 | 6885 | 7081 | 7277 | 7473 | 7669 | 7864 |
| 40 | 8059 | 8254 | 8448 | 8642 | 8836 | 9030 | 9223 | 9416 |
| 48 | 9608 | 9800 | 9992 | 10183 | 10374 | 10564 | 10754 | 10944 |
| 56 | 11133 | 11322 | 11511 | 11699 | 11886 | 12074 | 12260 | 12446 |
| 64 | 12632 | 12817 | 13002 | 13187 | 13370 | 13554 | 13736 | 13919 |
| 72 | 14101 | 14282 | 14462 | 14643 | 14822 | 15001 | 15180 | 15358 |
| 80 | 15535 | 15712 | 15888 | 16063 | 16238 | 16413 | 16586 | 16759 |
| 88 | 16932 | 17104 | 17275 | 17445 | 17615 | 17784 | 17953 | 18121 |
| 96 | 18288 | 18454 | 18620 | 18785 | 18950 | 19113 | 19276 | 19438 |
| 104 | 19600 | 19761 | 19921 | 20080 | 20238 | 20396 | 20553 | 20709 |
| 112 | 20865 | 21019 | 21173 | 21326 | 21479 | 21630 | 21781 | 21930 |
| 120 | 22079 | 22227 | 22375 | 22521 | 22667 | 22812 | 22956 | 23099 |
| 128 | 23241 | 23382 | 23522 | 23662 | 23801 | 23938 | 24075 | 24211 |
| 136 | 24346 | 24480 | 24613 | 24746 | 24877 | 25007 | 25137 | 25265 |
| 144 | 25393 | 25519 | 25645 | 25770 | 25893 | 26016 | 26138 | 26259 |
| 152 | 26378 | 26497 | 26615 | 26732 | 26848 | 26962 | 27076 | 27189 |
| 160 | 27300 | 27411 | 27521 | 27629 | 27737 | 27843 | 27949 | 28053 |
| 168 | 28157 | 28259 | 28360 | 28460 | 28560 | 28658 | 28755 | 28850 |
| 176 | 28945 | 29039 | 29131 | 29223 | 29313 | 29403 | 29491 | 29578 |
| 184 | 29664 | 29749 | 29832 | 29915 | 29997 | 30077 | 30156 | 30234 |
| 192 | 30311 | 30387 | 30462 | 30535 | 30607 | 30679 | 30749 | 30818 |
| 200 | 30885 | 30952 | 31017 | 31082 | 31145 | 31206 | 31267 | 31327 |
| 208 | 31385 | 31442 | 31498 | 31553 | 31607 | 31659 | 31710 | 31760 |
| 216 | 31809 | 31857 | 31903 | 31949 | 31993 | 32036 | 32077 | 32118 |
| 224 | 32157 | 32195 | 32232 | 32267 | 32302 | 32335 | 32367 | 32397 |
| 232 | 32427 | 32455 | 32482 | 32508 | 32533 | 32556 | 32578 | 32599 |
| 240 | 32619 | 32637 | 32655 | 32671 | 32685 | 32699 | 32711 | 32722 |
| 248 | 32732 | 32741 | 32748 | 32755 | 32759 | 32763 | 32766 | 32767 |

SHA-256 of the 256 decimal values joined by commas: `66cfc2e50e0ea6c326d698bd2aa14cc8b67f8e518530c9bb9c3f8f62d0fd19a0`  
SHA-256 of the derived 1024-entry full table (`voice_fx.sine_fx` at phases `i << 14`), same encoding: `41a30c959df1413245a6817c2d398c9d571f33460b34634b433c0717fb3c52ea`

### Appendix C -- TANH16: the ladder's tanh table, i = 0..15

Normative. `TANH16[i] = round(tanh(i / 16 * 4.0) * 32767)` -- EDGE sampled over [0, 4), Q1.15, read with linear interpolation (section 11.3). The interpolation's top word, used above entry 15 AND returned by the clamp for |v| >= 4.0, is `fixed.TANH_GUARD` = 32767 and is NOT tanh(4.0) (which would round to 32745). That leaves the top bin [3.75, 4) up to 6.5e-4 high. It is a known wrong constant and DR 0013 records BOTH the measurement of what correcting it buys -- the top bin twelve times more accurate, and no movement at all in the harmonic fingerprint at self-oscillation, because the 16-entry table's own worst error is nine times larger -- and why it is not corrected here: `rtl-sketch/drum_dp.v` reads this same image with its own hardcoded clamp, so the word cannot move without a matching change in the drum section.

| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 8025 | 15142 | 20812 | 24955 | 27796 | 29659 | 30846 |
| 8 | 31588 | 32047 | 32328 | 32500 | 32605 | 32669 | 32707 | 32731 |

SHA-256 of the 16 decimal values joined by commas: `65a5fa4b38b807735e09eed0eadd49b2a42850151daa47e3abb97a1641542c04`  
SHA-256 of the 17-word ROM image (`TANH16` followed by the guard word), which is exactly `rtl-sketch/tanh16.hex`: `3aa73628ec4f1b6eec99e77524a5460813c531dd9703a8fdea6df799dc91efeb`

### Appendix D -- G_ROM128: cutoff (Hz) -> ladder coefficient g, i = 0..128

Normative. `G_ROM128[i] = clip(round((1 - exp(-2*pi * (256*i) / 96000)) * 65536), 0, 65535)` -- EDGE sampled every 256 Hz at the ladder's 2x-oversampled rate, Q0.16, 128 entries plus entry 128 as the interpolation guard (`voice_fx.make_g_rom`). Entries 0..85 are reachable through the cutoff clamp of section 10; entries 86..128 are part of the table but never read. Eight entries per row.

| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 1116 | 2206 | 3270 | 4309 | 5324 | 6315 | 7284 |
| 8 | 8231 | 9157 | 10062 | 10947 | 11813 | 12661 | 13490 | 14301 |
| 16 | 15096 | 15875 | 16637 | 17385 | 18117 | 18835 | 19540 | 20231 |
| 24 | 20910 | 21576 | 22230 | 22872 | 23504 | 24124 | 24735 | 25335 |
| 32 | 25927 | 26508 | 27081 | 27646 | 28202 | 28751 | 29292 | 29826 |
| 40 | 30353 | 30873 | 31387 | 31895 | 32397 | 32893 | 33384 | 33870 |
| 48 | 34352 | 34828 | 35300 | 35768 | 36232 | 36691 | 37147 | 37600 |
| 56 | 38049 | 38495 | 38938 | 39378 | 39814 | 40249 | 40680 | 41109 |
| 64 | 41536 | 41960 | 42382 | 42801 | 43219 | 43634 | 44047 | 44458 |
| 72 | 44867 | 45274 | 45679 | 46082 | 46483 | 46881 | 47278 | 47673 |
| 80 | 48065 | 48455 | 48843 | 49228 | 49611 | 49992 | 50370 | 50745 |
| 88 | 51118 | 51487 | 51854 | 52218 | 52578 | 52936 | 53289 | 53640 |
| 96 | 53986 | 54329 | 54668 | 55003 | 55333 | 55660 | 55982 | 56299 |
| 104 | 56612 | 56920 | 57223 | 57520 | 57813 | 58100 | 58382 | 58659 |
| 112 | 58930 | 59195 | 59454 | 59708 | 59955 | 60197 | 60432 | 60661 |
| 120 | 60884 | 61101 | 61312 | 61517 | 61715 | 61907 | 62092 | 62272 |
| 128 | 62445 |  |  |  |  |  |  |  |

SHA-256 of the 129 decimal values joined by commas: `7d03fb29bdf97a177c31274f95864cb69111b70b7164dc5eb05c0e04a6f83414`

### Appendix E -- K_ROM32: cutoff (Hz) -> resonance compensation, i = 0..32

Normative (DR 0006). `K_ROM32[i] = round(k_onset(clamp(1024*i, 30, 21600)) / 4 * 32768)` -- unsigned Q1.15, EDGE sampled every 1024 Hz, 32 entries plus entry 32 as the interpolation guard (`voice_fx.make_k_rom`). `k_onset` is the small-signal onset of self-oscillation of the linearised loop, section 10.2 (`voice_fx.k_onset`); 32768 means k = 4 res, the uncompensated filter. Entries 0..21 are reachable through the cutoff clamp of section 10; entries 22..32 are evaluated at the clamp and never read. Eight entries per row.

| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 32800 | 33847 | 34863 | 35806 | 36666 | 37436 | 38110 | 38681 |
| 8 | 39147 | 39503 | 39746 | 39871 | 39875 | 39753 | 39501 | 39114 |
| 16 | 38588 | 37923 | 37119 | 36182 | 35120 | 33951 | 33837 | 33837 |
| 24 | 33837 | 33837 | 33837 | 33837 | 33837 | 33837 | 33837 | 33837 |
| 32 | 33837 |  |  |  |  |  |  |  |

SHA-256 of the 33 decimal values joined by commas: `19da75793533fc6d34eed44858cac4e934388d20ab0916fb7e54fea0afe69c28`

### Appendix H -- EXP_ROM65: 2^x for the modulation path, i = 0..64

Normative (DR 0012). `EXP_ROM65[i] = round(2^(i/64) * 32768) - 32768` -- the modulation path's exponential, EDGE sampled over ONE octave, 64 entries plus entry 64 as the interpolation guard (`voice_fx.make_exp_rom`). Stored biased by -32768 so that the top entry (2.0 in Q1.15, 65536) still fits in 16 bits; `voice_fx.exp2_q` adds it back, reads the table on the top 6 bits of the Q3.12 octave word's fraction and interpolates on the low 6, and turns the integer part into a right shift of 12..19 places. Worst relative error over the octave 3.57e-5, which is 0.062 cents. Eight entries per row.

| i | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 357 | 718 | 1082 | 1451 | 1823 | 2200 | 2581 |
| 8 | 2966 | 3355 | 3748 | 4146 | 4548 | 4954 | 5365 | 5780 |
| 16 | 6200 | 6624 | 7053 | 7487 | 7925 | 8368 | 8816 | 9269 |
| 24 | 9727 | 10190 | 10657 | 11130 | 11608 | 12091 | 12580 | 13074 |
| 32 | 13573 | 14078 | 14588 | 15103 | 15625 | 16152 | 16684 | 17223 |
| 40 | 17767 | 18317 | 18874 | 19436 | 20005 | 20579 | 21160 | 21747 |
| 48 | 22341 | 22941 | 23548 | 24161 | 24781 | 25408 | 26041 | 26681 |
| 56 | 27329 | 27983 | 28645 | 29313 | 29989 | 30673 | 31364 | 32062 |
| 64 | 32768 |  |  |  |  |  |  |  |

SHA-256 of the 65 decimal values joined by commas: `6a1cbbf81f383149c4ececcbd0eef37e979c24e9f700bfd6efc31185f520d557`

### Appendix F -- NOISE64: the first 64 noise words from reset

Normative (DR 0008), derived. The drum section's noise source (section 15.4) is a 31-bit LFSR, `s <- (s << 1) | (s[30] xor s[15] xor s[17] xor s[19])` -- the recurrence `b[n] = b[n-31] + b[n-16] + b[n-18] + b[n-20]` over GF(2), characteristic polynomial x^31 + x^15 + x^13 + x^11 + 1, primitive, period 2^31 - 1 bits -- seeded with 1 at reset and stepped 16 times per frame; the noise word of a frame is the 16 bits shifted in, oldest first, read as signed Q1.15 (`drums_fx.lfsr_frame`). Frame 0's word is 1: the seed's bit reaches the tap at bit 15 on the frame's last step. Eight words per row; the first column is the frame.

| frame | +0 | +1 | +2 | +3 | +4 | +5 | +6 | +7 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1 | 20483 | 4353 | 17495 | 8965 | 4442 | 16915 | 21506 |
| 8 | 12388 | 14207 | 6431 | 16439 | 22023 | 30344 | 28748 | 1611 |
| 16 | 10021 | 20488 | 8000 | 14708 | 2622 | 15098 | 17303 | 703 |
| 24 | 13589 | 3711 | 30509 | 16746 | 11132 | -32320 | 16213 | 12533 |
| 32 | 20845 | 12746 | 3198 | 19506 | 8961 | 4501 | 747 | 12629 |
| 40 | 19395 | 12069 | 16792 | 7085 | 8140 | -16743 | 25935 | -10374 |
| 48 | 5452 | -24641 | 23346 | 18227 | 17640 | 12282 | 12075 | 23015 |
| 56 | 25686 | 30920 | 18906 | 11937 | -27561 | -19132 | 3054 | 8995 |

SHA-256 of the 64 decimal values joined by commas: `41f2adb399b60f0d7f1d77a03bf004d9b2ec28ab220f476cfafe96c36f99a613`

### Appendix G -- KIT808: the reference kit as register writes

Informative, pinned so that the renders and the RTL bench are reproducible: the write list `drums_fx.kit_808()` produces (section 15.7), address and value per row, in write order; the register map is section 15.1. Every number is docs/tr808-reference.md's where it gives one; the levels are the balance of `drums_fx_render.py --balance`; what was chosen rather than sourced is marked in `kit_808`'s comments and in 15.7.

| addr | value | register | | addr | value | register |
|---:|---:|---|---|---:|---:|---|
| 0x20 | 0x1184E | OSC_INC[0] | | 0x45 | 0xF5C29 | ENV_PEAK[1] |
| 0x21 | 0x1F8A1 | OSC_INC[1] | | 0x46 | 0xFFFF | ENV_RATE[1] |
| 0x22 | 0x19F9C | OSC_INC[2] | | 0x48 | 0xF1 | ENV_CTL[2] |
| 0x23 | 0x2C9A9 | OSC_INC[3] | | 0x49 | 0x400000 | ENV_PEAK[2] |
| 0x24 | 0x44444 | OSC_INC[4] | | 0x4A | 0x3025 | ENV_RATE[2] |
| 0x25 | 0x2E148 | OSC_INC[5] | | 0x4C | 0xF1 | ENV_CTL[3] |
| 0xB0 | 0x11A9D23 | MODE_A1[0] | | 0x4D | 0x4DFA44 | ENV_PEAK[3] |
| 0xB1 | 0x324D110 | MODE_A2[0] | | 0x4E | 0x2D | ENV_RATE[3] |
| 0xB2 | 0x0 | MODE_AMP[0] | | 0x50 | 0xF2 | ENV_CTL[4] |
| 0xB3 | 0x1 | MODE_NUM[0] | | 0x51 | 0x400000 | ENV_PEAK[4] |
| 0xB4 | 0xDA1B85 | MODE_A1[1] | | 0x52 | 0x3025 | ENV_RATE[4] |
| 0xB5 | 0x355D5AE | MODE_A2[1] | | 0x70 | 0xF8 | ? |
| 0xB6 | 0x7333 | MODE_AMP[1] | | 0x71 | 0x400000 | ? |
| 0xB7 | 0x2 | MODE_NUM[1] | | 0x72 | 0x3025 | ? |
| 0xB8 | 0xECC30 | MODE_A1[2] | | 0x54 | 0xF3 | ENV_CTL[5] |
| 0xB9 | 0x37543CC | MODE_A2[2] | | 0x55 | 0x400000 | ENV_PEAK[5] |
| 0xBA | 0xB0A4 | MODE_AMP[2] | | 0x56 | 0x3025 | ENV_RATE[5] |
| 0xBB | 0x2 | MODE_NUM[2] | | 0x58 | 0xF4 | ENV_CTL[6] |
| 0xBC | 0x1728A19 | MODE_A1[3] | | 0x59 | 0xFFFFFF | ENV_PEAK[6] |
| 0xBD | 0x366ECC6 | MODE_A2[3] | | 0x5A | 0x44 | ENV_RATE[6] |
| 0xBE | 0x34B6 | MODE_AMP[3] | | 0x5C | 0x45 | ENV_CTL[7] |
| 0xBF | 0x1 | MODE_NUM[3] | | 0x5D | 0xFFFFFF | ENV_PEAK[7] |
| 0xC0 | 0x1E53ED0 | MODE_A1[4] | | 0x5E | 0x9 | ENV_RATE[7] |
| 0xC1 | 0x31579F2 | MODE_A2[4] | | 0x60 | 0x78200F6 | ENV_CTL[8] |
| 0xC2 | 0x0 | MODE_AMP[4] | | 0x61 | 0xB0A3D6 | ENV_PEAK[8] |
| 0xC3 | 0x1 | MODE_NUM[4] | | 0x62 | 0x154 | ENV_RATE[8] |
| 0xC4 | 0x1EDD6CC | MODE_A1[5] | | 0x64 | 0xF6 | ENV_CTL[9] |
| 0xC5 | 0x30CD4FE | MODE_A2[5] | | 0x65 | 0x3851EB | ENV_PEAK[9] |
| 0xC6 | 0x592 | MODE_AMP[5] | | 0x66 | 0x1D | ENV_RATE[9] |
| 0xC7 | 0x1 | MODE_NUM[5] | | 0x68 | 0xF7 | ENV_CTL[10] |
| 0xD0 | 0x1FFEA42 | MODE_A1[8] | | 0x69 | 0x800000 | ENV_PEAK[10] |
| 0xD1 | 0x3001300 | MODE_A2[8] | | 0x6A | 0x110 | ENV_RATE[10] |
| 0xD2 | 0xD9 | MODE_AMP[8] | | 0x6C | 0xF7 | ENV_CTL[11] |
| 0xD3 | 0x0 | MODE_NUM[8] | | 0x6D | 0x800000 | ENV_PEAK[11] |
| 0xD4 | 0x1FF8366 | MODE_A1[9] | | 0x6E | 0xE | ENV_RATE[11] |
| 0xD5 | 0x3005AFC | MODE_A2[9] | | 0x74 | 0xF9 | ? |
| 0xD6 | 0xAF | MODE_AMP[9] | | 0x75 | 0xF5C29 | ? |
| 0xD7 | 0x0 | MODE_NUM[9] | | 0x76 | 0x3025 | ? |
| 0xD8 | 0x1FE5EB2 | MODE_A1[10] | | 0x78 | 0xF9 | ? |
| 0xD9 | 0x3012282 | MODE_A2[10] | | 0x79 | 0x57CED9 | ? |
| 0xDA | 0x245 | MODE_AMP[10] | | 0x7A | 0x3E | ? |
| 0xDB | 0x0 | MODE_NUM[10] | | 0x7C | 0xFA | ? |
| 0xDC | 0x1FFD807 | MODE_A1[11] | | 0x7D | 0x52F1AA | ? |
| 0xDD | 0x3001EE0 | MODE_A2[11] | | 0x7E | 0x72 | ? |
| 0xDE | 0x13B | MODE_AMP[11] | | 0x80 | 0xFA | ? |
| 0xDF | 0x0 | MODE_NUM[11] | | 0x81 | 0x6E978D | ? |
| 0xE0 | 0x1FFBB4C | ? | | 0x82 | 0xA | ? |
| 0xE1 | 0x300303D | ? | | 0x84 | 0xFA | ? |
| 0xE2 | 0x196 | ? | | 0x85 | 0x161E4F | ? |
| 0xE3 | 0x0 | ? | | 0x86 | 0x3 | ? |
| 0xE4 | 0x1FF9A1F | ? | | 0x90 | 0x807803 | PATH[0] |
| 0xE5 | 0x3003F74 | ? | | 0x91 | 0x1F07823 | PATH[1] |
| 0xE6 | 0x28B | ? | | 0x92 | 0x907843 | PATH[2] |
| 0xE7 | 0x0 | ? | | 0x93 | 0xA07843 | PATH[3] |
| 0xE8 | 0x1FCD356 | ? | | 0x94 | 0x307861 | PATH[4] |
| 0xE9 | 0x30243FF | ? | | 0x95 | 0xB07883 | PATH[5] |
| 0xEA | 0x0 | ? | | 0x96 | 0xC07983 | PATH[6] |
| 0xEB | 0x0 | ? | | 0x97 | 0xD078A3 | PATH[7] |
| 0xEC | 0x1EDC70C | ? | | 0x98 | 0x7BE2 | PATH[8] |
| 0xED | 0x3046527 | ? | | 0x99 | 0x20F8D0 | PATH[9] |
| 0xEE | 0x0 | ? | | 0x9A | 0x10F8F0 | PATH[10] |
| 0xEF | 0x0 | ? | | 0x9B | 0x407BE1 | PATH[11] |
| 0xC8 | 0x1BB8EB8 | MODE_A1[6] | | 0x9C | 0x1F12514 | PATH[12] |
| 0xC9 | 0x31293A2 | MODE_A2[6] | | 0x9D | 0x50AD49 | PATH[13] |
| 0xCA | 0x0 | MODE_AMP[6] | | 0x9E | 0x50AD4A | PATH[14] |
| 0xCB | 0x1 | MODE_NUM[6] | | 0x9F | 0xE079A3 | PATH[15] |
| 0xCC | 0x4BE113 | MODE_A1[7] | | 0xA0 | 0xF079A3 | ? |
| 0xCD | 0x36C44A6 | MODE_A2[7] | | 0xA1 | 0x1F0F9DE | ? |
| 0xCE | 0xFFFF | MODE_AMP[7] | | 0xA2 | 0x1F0F9DF | ? |
| 0xCF | 0x1 | MODE_NUM[7] | | 0xA3 | 0x607BE2 | ? |
| 0x40 | 0xF0 | ENV_CTL[0] | | 0xA4 | 0x20F9F0 | ? |
| 0x41 | 0x400000 | ENV_PEAK[0] | | 0xA5 | 0x70FA10 | ? |
| 0x42 | 0x3025 | ENV_RATE[0] | | 0xA6 | 0x1F0FA36 | ? |
| 0x44 | 0x30F0 | ENV_CTL[1] | | | | |

SHA-256 of the 147 decimal words `address << 32 | value`, joined by commas, which is `spec/reference/tables/kit808.hex` read as decimal: `a43fe2a7d596a417ae3c9949fe43f94cc8e64482f7cac6ede5bc271009a5ff19`

<!-- END GENERATED APPENDICES -->

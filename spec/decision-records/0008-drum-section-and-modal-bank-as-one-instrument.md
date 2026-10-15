# 0008: The drum section — a TR-808 built on the modal bank

- **Status**: proposed
- **Date**: 2026-09-18
- **Decided by**: block agent, from `docs/tr808-reference.md` (Roland's June-1981 service notes and the Werner/Abel/Smith papers, 110 verified claims) and measurements in `model/` (`test_drums_fx.py`, `drums_fx_render.py --balance`) and `rtl-sketch/` (`verify_drums.py`, `area/`)

## Context

The sponsor has confirmed that drums ship. What existed was
`rtl-sketch/drum_src_seq.v`, an area strawman verified against nothing: a
pitch-swept sine kick, an LFSR, eight shift-subtract envelopes and a
saturating mix, feeding "the bodies" through an `exc_out` it never
specified — and the modal bank's trigger, excitation, mixing and gain
staging were open item 17.4. The strawman also dropped its last drum (the
final accumulate was never output) and the bank read its excitation port
per mode across its 15-cycle sequence instead of consuming it once.

`docs/tr808-reference.md` settled what an 808 is, and it is not what the
strawman built:

- BD, SD, the toms and congas, RS and CL are **bridged-T resonators** pinged
  by a 1 ms pulse and left to ring [SN p.5–6; W14a; W16 Table 2.2]. A
  bridged-T is a two-pole resonator: exactly one mode of `modal_dp.v`. The
  BD DECAY knob raises feedback that cancels the resonator's damping; f0
  does not move [W14a §6]. There is no swept oscillator anywhere.
- CH, OH, CB and CY are **six square-wave oscillators** (205.3 / 369.6 /
  304.4 / 522.7 / 800 / 540 Hz, one HD14584) summed and band-passed, then a
  **swing VCA** whose asymmetric clipping is the sound, then a high-pass
  [SN p.6, p.13; W14b]. Not noise. The cowbell is oscillators 5 and 6.
- SD adds white noise through a 2.75 kHz high-pass under a 15 ms envelope
  (SNAPPY); CP is band-passed noise under **three bursts 10 ms apart plus a
  47 ms tail**; the 808 has one noise generator [SN p.6, p.8].

So the bodies are coefficient presets of a bank that already exists and is
verified, and the new hardware is small: six phase accumulators, an LFSR,
a dozen envelopes, one nonlinearity and a routing table. What was not
small, and had to be measured, is how many modes a *kit* needs: four cover
one bridged-T voice at a time, and a kick, a snare and a tom ringing
together is ordinary 808 material (reference §14).

## Decision

The drum section and the modal bank are one instrument, contract section 15:

1. **The bank is the bodies and the filters.** `ModalFx` / `modal_dp.v`
   gain per-mode excitation, a selectable numerator on the first `NUMS`
   modes (RAW all-pole; BP `1 − z⁻²`; HP `(1 − z⁻¹)²`, from two history
   registers), a 19-bit Q4.15 output word (DR 0005's width) and a
   `headroom` of 0 in the instrument, balanced by the per-mode `amp`. The
   struck bar of `ModalFx.coefficients(note)` stays as the bank's optional
   preset family; the 808 bodies of Appendix G are the required ones.
   Sizing: **12 modes, 6 with numerators** (hat BP, OH HP, CH HP, SD-noise
   HP, CP BP, CB BP; then BD, SD-lo, SD-hi, LT, HT and one spare), 39
   clocks per frame. The 4-mode bank keeps its own verification
   (`verify_modal.py`, now with numerator and extreme segments).
2. **Excitation is accumulated, not held.** The bank's `exc[m]` registers
   are written through `exc_we / exc_mode / exc_val` while it is idle,
   consumed once — each mode in its own step — and cleared. That removes
   the hazard the review found (a port read per mode across the sequence)
   without a second copy of the excitation, and it is the interface the
   drum datapath's paths need anyway. The coefficient buses must be held
   from the frame's tick to `body_valid`, as the ladder's coefficients are;
   the bench proves the comparison sees a violation (`--jitter`).
3. **The drum datapath** (`model/drums_fx.py`, `rtl-sketch/drum_dp.v`):
   8 edge-triggered stops with a Q0.15 accent each; 12 envelopes, each
   `level ← usat24(peak · accent >> 15)` at its stop's edge, held `hold`
   frames, re-struck `bursts` times every `period` frames at 13/16 of the
   last strike, choked to 0 by another stop, and otherwise the voice's
   release rule `level −= max(1, (level · rate) >> 16)` (8.3) — the same
   dead zone, closed the same way, tested the same way (contract revision
   18, #656, gives `rate` a 3-bit exponent: `(level · m) >> (16 + e)`, the
   voice release's own encoding; see the amendment at the end); 16 paths, each
   `v = nl(source) · (ENV(e1) + ENV(e2)) >> (15 + att)` on one 25×16
   multiplier, summed exactly into the mix bus or one mode's excitation;
   sources NOISE (a 31-bit LFSR, x³¹ + x¹⁵ + x¹³ + x¹¹ + 1, 16 bits per frame), SQSUM
   (the six squares), PULSE, SQPAIR (squares 4 and 5), TAP m (a mode's
   state ÷ 8, railed at Q1.15 like an op-amp output); nonlinearities LIN,
   SWING (×4 / ÷8 then the ladder's tanh) and TANH. 48 clocks per frame.
4. **Two buses and one rail.** `dmix` (21 bits, the exact sum of the paths
   routed to MIX) and `body` (the bank's 19-bit word) leave the block; the
   instrument's output stage is `sample = sat16((v·vol + dmix·dvol +
   body·bvol) >> 15)` — the voice's formula bit for bit when `dvol = bvol
   = 0`. Inside the block the only clamps are the bank's 28-bit state word
   (part of its arithmetic since rev 1, the analogue of the ladder's) and
   the tap's rail; neither is reached by the reference kit at any accent.
5. **The kit is a table, not hardware.** Appendix G is the reference kit
   as 99 register writes (100 since revision 6), SHA-pinned so the renders and the RTL bench are
   reproducible; every frequency, Q and time constant in it is the
   reference's, the levels are balanced to Roland's tuning chart, and the
   choices the reference could not settle are marked (15.7). Fitting a
   measured unit is a table change.

## Alternatives considered

- **The strawman's swept sine kick** — the 808 BD is a 56 Hz resonator with
  a 4 ms attack at 130 Hz and a slow sigh, not a sweep (reference §2); the
  swept sine is the 909's, and it needed a second sine table the contract
  had told us not to add. Rejected on the evidence.
- **Noise hats** — "the single most-heard tell" of a digital 808 (reference
  §15.1). Six phase accumulators cost 144 flops; rejected.
- **A per-voice datapath** — the 808's 16 circuits as 16 blocks; every
  voice is envelope × source → resonator, so one sequenced datapath and a
  routing table cover all of them at one multiplier.
- **Four modes, allocated per trigger** — cheap, but a tom fired while the
  BD rings would steal a body; a static allocation is what the analog
  machine has, and the cost is measured below rather than assumed.
- **Latching the excitation inside the bank** — a second copy of 12 × 21
  bits; the accumulate interface gives the same guarantee for one copy.
- **The snare's cascade** (the high resonator driven from the low one's
  output ×1/38) — needs a linear tap of a ringing body; both modes are
  excited from the pulse instead, which the reference calls a subtle loss.
  Recorded in 17.15.
- **The strawman's trinomial LFSR** (x²³ + x¹⁸ + 1, one bit per frame) —
  one bit per frame makes each word a shifted copy of the last (a one-pole
  low-pass, not white), and a trinomial recovers slowly from the sparse
  reset state: x³¹ + x³ + 1 stepped 16 bits per frame still measured a
  0.5 % ones deficit over the first 200 000 words. A pentanomial whose taps
  all lie at bit 15 or above keeps the one-cycle leap and measures none.
- **Shift-subtract envelopes** (the strawman's `level −= level >> sh`) —
  decay times in octaves only; the multiplier is on the block anyway and
  the voice's rule is proven. One rule for every envelope on the chip.

## Consequences

- Contract 12, 14, 15, 16, 17 and 18 change and Appendices F and G are
  added; revision 5. `modal_dp.v`'s interface changes (buses, the
  accumulate port, `tap_y1`, `MODES`/`NUMS`/`OW`/`EW` parameters);
  `modal_dp_rom.v` and `modal_dp_regs.v` keep the rev-3 interface and
  sizing and are area variants only. `drum_src_seq.v` is deleted.
- Verified: `drum_kit.v` against `DrumsFx` over 172 063 frames (every stop
  soloed, the edge semantics, all eight at accent 2.0, a bar with the choke
  and a BD retune while ringing, register extremes on the last paths and
  the spare mode, a mid-run RESET, decay into silence), both buses
  identical; eight negative controls caught (`test_rtl.py`).
- Measured (gf180mcu 7t, tt_025C_5v00, cell area): `drum_dp` 0.278 mm²
  (10 716 cells, 1 355 flops); the 12-mode bank 0.367 mm² (13 845 cells,
  2 076 flops; 0.370 with numerators on all twelve, 0.251 at 8 modes,
  0.188 at the rev-3 four with two numerators); `drum_kit` 0.646 mm²
  (0.605 Booth), 0.461 mm² at 8 modes / 8 envelopes / 12 paths. **The full
  kit is four times the ladder.** The cost is the state and its muxing:
  ~120 bits per envelope, ~100 per mode. The 8-mode configuration is the
  same RTL with three bodies fewer; which the product takes is a budget
  decision, not made here (17.13).
- Cycles: 85 of 256 per frame for the whole section (48 + 39 minus the
  overlap of the launch), against the ladder's 24 and the bank's 15 in
  rev 3.
- Every kit level, the CB band-pass centre, the clap's tail ratio and the
  BD's attack shift are register values and can be re-fitted without an
  RTL change; the BD attack shift, the toms' diode pitch fall, the tom
  noise, the BD tone filter and the cymbal are not in the reference kit
  (17.14).

## Amendment (contract revision 18, #656): the envelope rate exponent

The Q0.16 `rate` carried by this decision stops at `rate = 1`, which is
τ = 2^16 / SR = 1.3653 s, and the step above it halves τ. The cymbal's DECAY
knob needed slower and finer settings than that. Model and RTL now read
`RATE` and `FRATE` as `{e[2:0], m[15:0]}`, with
`dec = (level · m) >> (16 + e)`.

- **Compatibility.** `e = 0` is this decision's arithmetic exactly. The
  reference kit writes only `e = 0` codes (`drums_fx.rate_reg`), so every
  shipped stream and every pinned hash is unchanged. Only the cymbal DECAY
  knob's off-anchor images use `e > 0` (`drums_fx.rate_code`).
- **Shared multiplier audit.** The 25 × 16 multiplier is untouched. Its
  `mul_b` is still the 16-bit mantissa. The envelope slot's product, and only
  that slot's, is shifted by `e` before the same `[39:16]` slice. The fire
  (`[39:15]`), re-strike (`[39:16]` with `BURST_C`), tanh interpolation
  (`[28:13]`) and path (`>>> (15 + att)`) slices read the unshifted product.
  The schedule is still 68 clocks.
- **Cost.** 3 bits × 2 registers × 18 envelopes = 108 flip-flops, plus a
  3-stage shift of a 40-bit word on the decay slot. This is stated from the
  RTL. No synthesis was run for this amendment.
- **Host writers.** The only producers of drum `RATE` / `FRATE` words are
  `drums_fx.rate_reg` (e = 0, ≤ 16 bits), `rate_code` (≤ 19 bits) and the
  benches. `verify_ctl.py` drives `(1 << REG_BITS["rate"]) − 1` through the
  link, so its corner moved to 19 bits with `REG_BITS`.
- **Controls.** `INJECT_BUG_DRUM_RATE_Q16` (datapath ignores `e`) and
  `INJECT_BUG_DRUM_RATE_REG16` (`drum_regs` truncates the write to 16 bits).
  Both are caught bit-exactly at `drum_kit` and at the I2S pins.


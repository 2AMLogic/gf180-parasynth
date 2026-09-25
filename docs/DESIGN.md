# Design state

One place for what has been decided and measured, because the reasoning is
otherwise scattered across issue comments. Every number here was measured or
computed in this repository or a sibling; nothing is quoted from intuition.
Where a figure is an estimate it says so.

**Read the gap list at the bottom first if you are deciding whether to rely on
any of this.**

---

## 1. What it is

A three-voice paraphonic Minimoog-shaped voice — three detuned oscillators
from the held keys into one nonlinear four-pole ladder — with a drum section
whose tuned bodies are the modal resonator bank, on gf180mcu: a Minimoog and
a TR-808 in one chip. Control comes from a host MCU over SPI (DR 0007); audio
leaves as I2S. The host owns all musical time; there is no sequencer on the
chip. The chip-level design is [ARCHITECTURE.md](ARCHITECTURE.md).

The product it targets is a small sound module that a MIDI keyboard plugs into,
with a speaker so it demonstrates itself and a jack for real listening. See
[DR 0002](../spec/decision-records/0002-the-product-this-block-is-for.md) —
**and its Corrections section, which withdraws the commercial argument.**

---

## 2. The per-sample budget

12.288 MHz over 48 kHz is **256 core cycles per audio frame**. Everything
below is measured — cycle counts from iverilog, cell counts from yosys generic
mapping.

| block | cycles | cells | RAM | status |
|---|---:|---:|---:|---|
| ladder filter, time-shared, one context | **24** | 5,711 | 0 | RTL bit-exact against `model/fixed.py`, in simulation; 19-bit output (DR 0005); two contexts (the voice's and the drum filter's) bit-exact on every channel |
| drum section: sources, 12 envelopes, 16 paths (`drum_dp`) | **48** | 10,716 (0.278 mm² of gf180 7t cells) | 0 | RTL bit-exact against `model/drums_fx.py` (DR 0008) |
| modal bank, 12 modes / 6 numerators — the drums' bodies and filters | **39** (15 at 4 modes, 7,017 cells) | 13,845 (0.367 mm²) | 0 | RTL bit-exact against `model/modal_fixed.py`; sizing proposed, not ratified |
| the whole voice around the ladder (`rtl-sketch/voice_dp.v`) | **54 best, 64 mean, 136 worst** from `go` (drum filter off; +24 with it on, +8 for `go` at cycle 8: 168 of 256 worst) | 20,522 (7t-mapped) | 0 | RTL bit-exact against `model/voice_fx.py`: 255,060 frames, every sample, every tap of contract 16.4 and the final state; eight injected defects each caught; the worst frame is the all-maximum image (three reciprocals, both PolyBLEP windows on every edge of every oscillator) |
| the chip (`rtl-sketch/synth_top.v`: link, voice, modal bank, **placeholder** drum sources, I2S) | **150 of 256 worst** | 29,812 (7t-mapped) | 0 | elaborates, synthesises, runs through its pins; ARCHITECTURE.md. The drum section above is not in it yet — contract 17.23 |
| formant voice, 5 resonators | ~20–25 *(est)* | — | ~1.8 kbit ROM | not written |
| existing 4-voice core (sibling repo) | not measured | 19,049 | 0 | verified, in production |
| **used** | drum section **85 of 256** tick to `body_valid` (48 in the datapath + 39 in the bank, one clock overlapped) | | | the chip's own worst frame is 150, measured without it |

The ladder's worst-case cycle count equals its mean — the fixed latency that
justified choosing an explicit solver over an iterative one. The chip's worst
frame is 150 cycles, measured with every oscillator gliding (three
reciprocal divisions), three square waves and the drum filter engaged
(ARCHITECTURE.md section 5).

**Nothing needs a faster clock.** The formant family, the feature a 4× clock
was contemplated for, fits nine times over at 12.288 MHz.

The drum section's cell counts above are yosys → ABC on `gf180mcu_fd_sc_mcu7t5v0`
at `tt_025C_5v00` (the method of `area-budget.md`), not the PDK-neutral
counts of the other rows: `drum_kit` (both together) is 0.646 mm² of cells,
0.605 with `synth -booth`, and 0.461 mm² at 8 modes / 8 envelopes / 12
paths — four times the ladder, almost all of it state and its muxing. The
strawman `drum_src_seq.v` (0.089 mm², verified against nothing) is gone.

---

## 3. The process is slower than "180 nm" implies

GF180 *is* a 180 nm process. What is slower is the **digital standard-cell
libraries**: `gf180mcu_fd_sc_mcu7t5v0` cells are built from 5 V transistors
with **0.5–0.6 µm gate lengths** (`nfet_05v0 W=0.82u L=0.6u`). The PDK also
contains 0.28 µm 3.3 V devices; these libraries do not use them.

The practical rule is narrower than "gf180 is slow": **take timing from the
library, never from the process name.**

FO4 from the liberty tables (7-track; 9-track ≈ 5 % faster):

| corner | FO4 | vs textbook 180 nm / 1.8 V (~60 ps) |
|---|---:|---:|
| ff_n40C_5v50 | 0.164 ns | 2.7× slower |
| tt_025C_5v00 | 0.251 ns | 4.2× |
| ss_125C_4v50 | 0.442 ns | 7.4× |
| ss_125C_3v00 | 0.669 ns | **11×** |

An 18×18 MAC (Dadda, Baugh-Wooley, Kogge-Stone CPA, ~2,420 cells) times at
**13.9 ns at tt/5 V and 42.1 ns at ss/3.0 V** — from a Python STA over the
liberty tables with **no clock-tree skew and no detailed routing**. That is
enough to support the clock choice and is *not* timing closure. At 81.4 ns it closes everywhere
with 38 ns of margin; at 20.35 ns (49.152 MHz) it closes only at typical 5 V
and needs 2–4 pipeline stages otherwise.

**When reading other people's gf180 results:** ORFS judges at `ff_n40C_5v50`;
open_pdks' LibreLane config loads only 5 V corners and fails violations only at
`*tt*`. Caravel's gf180mcu core is constrained at 33 MHz; a cycle-accurate
68000 closed at 20 MHz. Every published "50 MHz on gf180" is a typical- or
fast-corner number.

---

## 4. Fixed point

| | format | why |
|---|---|---|
| signal | Q1.15 | |
| filter state | 24-bit, **20 fraction bits**, in units of 2·Vt | 20 is the floor — below it the low-cutoff dead zone opens (at 16 bits a 40 Hz cutoff is 5.8 dB off) |
| coefficients | Q0.16 | |
| `tanh` table | **16 entries, edge-sampled, interpolated** — 256 ROM bits | 16 scores identically to 256 on every patch |
| phase accumulator | 24-bit | |
| PolyBLEP reciprocal | 16-bit mantissa + 16-bit reciprocal, computed at note-on; one 16×16 multiply per sample | set by tracking the float waveform inside Q1.15, not by aliasing — 8 bits already reach the float's suppression |
| envelope | 24-bit level; 24-bit voice rate code (Q0.16 mantissa + 8-bit exponent), release `L −= max(1, (L·mantissa) >> (16 + exponent))` | 20 is the floor for attack time; the exponent preserves long-release precision |
| cutoff → `g` ROM | 128 × Q0.16, interpolated — 2 kbit | −0.6 % at 120 Hz, −0.05 % at 1 kHz |
| drum envelope | the voice's release rule, 24-bit level, Q0.16 rate, plus a hold count, up to three re-strikes at 13/16 and a choke | one rule for every envelope on the chip; the dead zone closed the same way |
| drum bodies and filters | modes of the modal bank: Q2.24 coefficients, 28-bit state; a numerator (`1 − z⁻²` or `(1 − z⁻¹)²`) on six of twelve | the 808's bridged-T voices are presets, its band- and high-passes the same resonator pre-differenced |
| drum noise | 31-bit LFSR, 16 bits per frame | a one-cycle leap-forward; whiter than reading the low bits of a once-per-frame register |
| swing VCA | ×4 / ÷8 into the ladder's 16-entry tanh table | the 808's asymmetric clipping on hats and cowbell, on the table already there |

Holding state in units of 2·Vt rather than volts turns the paper's stage into
`Y += g·(tanh(X) − tanh(Y))`: the `tanh` argument becomes the state itself and
one multiply leaves the inner loop.

Two things fixed point found that float hid:

- **A truncation limit cycle at −73 dBFS.** Real, permanent, and below any
  DAC's noise floor. Bounded by a test.
- **Four of eight audition patches exceed full scale** — `growl-bass` peaks at
  1.94 × full scale at the ladder's output. Invisible in float because the
  renderer normalises afterwards. Saturation is now *designed*
  ([DR 0005](../spec/decision-records/0005-gain-structure-headroom-and-the-vca.md)):
  the ladder's output word is Q4.15, the VCA is after the filter, and a host
  `vol` register precedes the one hard rail; at the reference volume nothing
  clips.

A methodological note worth keeping: table values sit at bin **edges** when
interpolating and **midpoints** when not. Mixing them costs ~8 dB and presents
as "interpolation made accuracy worse," which is impossible and is the tell.

---

## 5. The filter

Huovilainen, DAFx-04 — not the linearised model. The nonlinearity is in every
stage; equation (17)'s reuse makes that five `tanh` per sample rather than
eight; the feedback carries a half-sample delay (average of the last two
outputs) or the resonant peak drifts off the cutoff; 2× oversampling is
mandatory.

At a fixed `k = 4·res` self-oscillation was sustained to about 3 kHz and
not above — the paper's own caveat that the required feedback varies with
frequency. Measured, the onset loop gain rises from 4.00 at 30 Hz to 4.85 at
11 kHz; [DR 0006](../spec/decision-records/0006-resonance-compensation-rom.md)
adds a 32-entry compensation ROM (528 bits, two multiplies per frame) so that
`res = 1` is the onset everywhere within 0.4 %. The frequency it oscillates
at is 0.97 × the cutoff at 30 Hz and 1.07 × at 10 kHz; that tuning error is
recorded and not corrected.

Why not the alternatives ([DR 0001](../spec/decision-records/0001-ladder-filter-model.md)):
ZDF/TPT with Newton-Raphson has better tuning but needs iteration, and converges
*slower* as feedback and cutoff rise — its worst case is exactly how the
instrument gets played. Levien's matrix form is exact on the linear part but is
16 multiplies against 5, with the nonlinearity still unsolved. **An FIR cannot
do it at all**: matching the ladder at 200 Hz / res 0.9 needs 15,355 taps, and
at res 1.0 it cannot self-oscillate because it has no feedback.

---

## 6. Oscillators

The committed sibling's oscillators use direct phase-bit formulas and alias
badly — **−14.8 dB of inharmonic energy at MIDI note 88**, −20.8 dB at middle
C. That is worse, and more objectionable, than any difference between filter
models.

PolyBLEP recovers **~16 dB uniformly** on saw and square, for a comparison and
about three multiplies applied only within one phase increment of the
discontinuity. No iteration, fixed latency. It needs 1/dt, which is constant
for a held note and so computed at note-on.

Note the square's correction has the **opposite sign** to the saw's — a square
steps up at the wrap where a saw steps down. Getting this backwards measures
5 dB *worse* than naive.

In fixed point (`model/voice_fx.py`) the suppression is identical to float at
every note measured (−42.7 / −36.6 / −31.0 dB at notes 40 / 64 / 88). The
reciprocal's width turned out not to matter for aliasing at all — a constant
per-note error is periodic with f0 and lands on the harmonics — so its 16 bits
are set by waveform accuracy against the float instead. Note also that the
float voice as auditioned (`engines.mono_note`) used the naive oscillators;
`blep=True` is now an opt-in flag there, and the integer voice always
band-limits.

---

## 7. Output

**I2S, with a 1-bit modulator as a debug pad.**

The sigma-delta loop is fine — 103 dB simulated at OSR 256, above the 16-bit
floor, and 3rd order buys nothing. The **pad** is the limit: rise/fall
asymmetry from this PDK's liberty is up to 0.44 ns at 3.3 V, capping it at
**65–80 dB SINAD**, and PSRR is 0 dB by construction because the output *is*
the supply.

The decisive problem is images, not noise. With no interpolator, a 19 kHz tone
puts a zero-order-hold image at 29 kHz at **−12 dBFS**. A 20 kHz-passband
filter *can* reach down to 29 kHz — an ideal 7th-order elliptic does — but it
needs that steep a transition, which is why every audio DAC interpolates
instead. The 65–80 dB SINAD ceiling is likewise a model result from the
liberty's edge asymmetry, not a measured universal limit.

And both claimed advantages dissolve: "bit-exactness extends to the pin" is
**already true of I2S** and already tested that way; "no DAC in the BOM"
replaces a specified 112 dB part with an unspecified one made of a pad.

The modulator is still worth a fourth pad (~500–1000 cells, zero BOM) as a
characterisation instrument: scope-and-RC bring-up before any DAC is trusted.

---

## 8. Board

| | |
|---|---|
| host link | SPI register writes, DR 0007 — one 32-bit transaction per write, applied at the next frame tick; the MCU translates USB-MIDI |
| MIDI in | USB-MIDI through the MCU; a DIN/TRS input would be the MCU's UART, not the chip's |
| USB | CH32V203F8U6, **3 × 3 mm, $0.33**, TinyUSB MIDI works today |
| audio | I2S → MAX98357A (speaker, has its own DAC) + PCM5102A (line/jack) |
| headphones | PCM5102A is **line level**; 32 Ω needs 66 mA. A TPA6132A2 (~$1, 3 × 3 mm) is the honest fix |
| one-part alternative | TLV320DAC3100: headphone *and* 1.6 W class-D in one 5 × 5 mm, needs I2C |

Size, from component footprints × 2.1 for routing:

| | board | thick | like |
|---|---|---|---|
| dongle (TRS MIDI, no speaker) | 29 × 20 mm | 8.6 mm | car key fob |
| **all-in-one (+ speaker, amp, 200 mAh)** | **38 × 25 mm** | **12.6 mm** | book of matches |
| + USB-A host | 45 × 30 mm | 16 mm | Zippo |

**Our bare die is 2.2 mm².** The USB-A host connector is 87× that; the battery
273×. The chip is never the size constraint.

Power: 11 mA quiescent, 35 mA at normal listening levels, 247 mA at 1 W peak,
against USB 2.0's 500 mA. A 400 mAh cell gives ~7.5 h of normal use.

**A 15 mm speaker in a matchbox cannot produce bass.** It proves the thing is
alive; it does not demonstrate the sound. The jack is the real output.

### The keychain dongle, itemised

The dongle row above is an envelope with no parts list behind it. This is the
BOM the product target actually names (issue 1, DR 0002): **USB-C in, 3.5 mm
out, no speaker, no amplifier, no battery** — with every footprint traced to a
manufacturer's drawing rather than to memory.

`drawing` = read off the manufacturer's mechanical drawing · `package` =
JEDEC/vendor package outline · `estimate` = not sourced, and labelled so

| part | package | land, mm | area, mm² | tall, mm | source |
|---|---|---|---:|---:|---|
| USB-C receptacle | GCT USB4105-GF-A, SMT | 8.94 × 7.35 | 65.7 | 3.31 | `drawing` |
| MCU | CH32V203F8U6, QFN-20 3 × 3 | 3.2 × 3.2 | 10.2 | 0.9 | `package` |
| **this chip** | QFN-20 4 × 4 | 4.4 × 4.4 | 19.4 | 0.9 | `package`, sized below |
| DAC | PCM5102A, TSSOP-20 (PW0020A) | 7.1 × 5.8 | 41.2 | 1.2 | `drawing` 4220206/A ¹ |
| 3.5 mm TRS jack | CUI/Same Sky MJ-3523-SMT-TR, right-angle SMT | 14.5 × 6.0 | 87.0 | 5.0 | `drawing` 2025-04-15 |
| LDO, 12.288 MHz crystal, ~20 × 0402, LED | SOT-23-5, 3.2 × 2.5, 0402 | — | 20 | ≤ 1.0 | `estimate` |
| | | **sum** | **243.5** | | |
| | | **× 2.1 routing** | **511** | | |

¹ TI's land-pattern example gives 5.8 mm along the pin rows; 7.1 mm across is
the 6.4 mm lead span plus pad overhang. Body is 6.5 × 4.4 mm, JEDEC MO-153.

**511 mm² against the dongle row's 29 × 20 = 580 mm². It fits, with 12 %
slack** — and issue 1's placeholder "~20 × 40 mm" (800 mm²) is 1.6× more board
than the parts need, so the guess was loose in the safe direction. The
smallest rectangle that satisfies both the area and the two edge-mounted
connectors is about **20 × 26 mm**: USB-C on one short edge (7.35 mm deep),
the jack across the other (6.0 mm deep, using 14.5 mm of the 20 mm width),
leaving ~12.6 mm of middle for three ICs whose lands total 14.7 mm across a
20 mm board.

**That is an area budget plus a connector edge check, not a placed layout.**
Nothing here has been through a CAD tool; the ×2.1 routing factor is the same
one the table above uses, and it is the assumption most likely to be wrong. A
routed board is the real answer and does not exist yet.

**The jack sets the thickness, not the die.** 5.0 mm tall + 1 mm PCB + 1.2 mm
of wall each side = 8.4 mm, which the 8.6 mm row above covers with 0.2 mm to
spare. The common *through-hole* alternative (Kycon STX-3120-3B) is a
**10.0 mm** body on its own drawing and would push the case past 13 mm on the
jack alone. A keychain needs the right-angle SMT part, and that is a real
constraint on the BOM rather than a preference.

**Why QFN-20 4 × 4 and not 3 × 3.** ARCHITECTURE.md section 8 needs 15 pads,
17 with a second core supply pair, so QFN-20 is the pin count either way — the
body size is set by the die. 2.2 mm² (1.48 mm square) sits on a 3 × 3 QFN's
pad; the routed two-slot die measured in issue 33's follow-up status (issue
36) is 3,464,960 µm², **1.86 mm square**, and wants the 4 × 4.

**That is the whole board-level exposure to issue 33** ("the joined chip does
not fit in one quarter slot"): whichever slot count wins, the package steps by
one size and the board by ~10 mm² out of 511. Issue 33 is a **cost** risk to
issue 1's BOM — the $3.50–4.50 die line and the $19–46 retail figure both
assume a single quarter slot — and it is not a fit risk. The chip is still
never the size constraint.

---

## 9. What is NOT done

The honest list. Nothing below is in progress unless a linked PR says so.

- **The reference model's per-sample path is fully integer** (`model/voice_fx.py`),
  but float still turns the patch's physical units into note-on register
  values and ROM contents — Hz to phase increment, seconds to envelope rate,
  the tanh / sine / `g` tables. In the product those are the host's job or a
  ROM's, and neither is specified yet. Note-on semantics and the glide are
  decided in [DR 0003](../spec/decision-records/0003-note-on-gate-trigger-and-a-continuous-voice.md)
  and [DR 0004](../spec/decision-records/0004-glide-constant-rate-linear-in-pitch.md)
  (proposed) and implemented in the integer model, which is now one
  continuous voice; the float audition still renders note by note.
- **The chip carries the complete drum kit, and the join is verified at the
  pins.** `rtl-sketch/ladder_dp.v` / `ladder_dp_n.v`, `modal_dp.v` /
  `modal_dp_rom.v` and `drum_kit.v` (`drum_dp.v` + `modal_dp.v`) are
  bit-exact against `model/fixed.py`, `model/modal_fixed.py` and
  `model/drums_fx.py` under iverilog, with negative controls that show each
  bench can fail (`rtl-sketch/test_rtl.py`), and so is the whole voice,
  `voice_dp.v`, against `model/voice_fx.py`. `drum_section_placeholder` is
  **gone**: `synth_top.v` instantiates `drum_kit` at revision 10's size
  (16 modes / 11 numerators / 18 envelopes / 23 paths / 11 stops) through the
  22-bit mix bus, the 19-bit body bus, the `drum_done` handshake and the
  output stage of contract 12. `rtl-sketch/verify_synth_top.py` drives all
  eleven circuits and all sixteen sounds over the SPI pins and compares the
  decoded I2S wire against `model/synth_top_model.py`; it was observed RED
  against the pre-integration drum section first (`--rtl`), which is what
  makes the green run mean anything. `drum_src_seq.v`, the area strawman
  verified against nothing, and `touch_dp.v`, never compared against
  anything, are both deleted. The earlier "20 cycles, 1,917 cells" ladder figure was the area of
  a circuit whose ROM reads were out of range — every output was X — and is
  withdrawn; the table above has the measured numbers.
- **The drum section is an 808 by circuit, and now partly by measurement.**
  [`docs/drum-verification.md`](drum-verification.md) compares every voice
  against a real TR-808 (s/n 103852, CC0). Its section 8 withdraws one
  measurement method and three of its own headline numbers, and records what
  was actually wrong: the snare's noise *band* (not its level, which was
  2.3 dB down and not 16), the cowbell's shared gate, and the bass drum's f0,
  which the kit took from Roland's chart while taking its Q from the circuit.
  Contract revision 6 fixes those; **the excitation is still an impulse where
  the machine's is a shaped pulse, and that is the largest thing left
  (17.20)**. DR 0008 builds
  it from `docs/tr808-reference.md`: bridged-T bodies as modal presets,
  six square oscillators for the hats and cowbell, one noise source, the
  swing VCA. What the reference kit leaves out is listed in the contract's
  17.14 (the BD's 4 ms attack shift and sigh, the toms' diode pitch fall,
  the cymbal, rimshot, claves, maracas); the levels are balanced to
  Roland's tuning chart, not to a recording; and the cowbell's band-pass
  centre and the clap's timing are choices the reference could not settle.
  The renders in `audio/drums/` are what a listener judges.
- **The numeric contract is proposed, not ratified.**
  [`spec/NUMERIC-CONTRACT.md`](../spec/NUMERIC-CONTRACT.md) (revision 6)
  writes the integer voice and the drum section down section by section,
  pins its seven tables by SHA-256 (`spec/reference/gen_tables.py --check`
  keeps them the model's), and lists in its section 17 what it deliberately
  leaves open: the self-oscillation tuning table, the modal bank's sizing,
  the drum section's size and what its kit does not model, and the chip's
  unwired drum section (17.23). The physical control layer, the power-on
  defaults and the cutoff register widths are closed by
  [DR 0007](../spec/decision-records/0007-control-interface-spi-register-writes.md),
  the drum section by
  [DR 0008](../spec/decision-records/0008-drum-section-and-modal-bank-as-one-instrument.md),
  and the kit's fit to real hardware by DR 0009 and DR 0010 (all proposed). Until the contract is ratified, the filter, the modal
  bank, the drum section and the voice are bit-exact against their own
  models and nothing more.
- **The chip has been synthesised to gf180mcu but not placed or routed.**
  Cell area is measured (`area-budget.md`, ARCHITECTURE.md section 10: 0.663
  mm² of cells, 0.603 with Booth, 7-track, tt/5 V, `*_1` cells allowed) —
  **and that figure does not include the drum section's 0.646 mm²**, which is
  not in `synth_top.v` yet. No floorplan, route, GDS, DRC, LVS, STA or power
  of the top level; the routed `ladder_dp` and polysynth core in `pnr/` are
  the only calibration of cells to core, and under ORFS's default policy they
  say the chip is at the edge of the quarter slot (ARCHITECTURE.md section 10).
- **No hardware.** No FPGA bitstream for this block, no board, no silicon.
- **The drum branch's material is not in the headroom measurement** of
  ARCHITECTURE.md section 4.3; `rtl-sketch/headroom_check.py` must be re-run
  on it.
- **The commercial case is withdrawn** (DR 0002 Corrections) and the consumer
  promise still does not explain why someone would want to play it.
- The filter's self-oscillation tuning error (−3 % at 30 Hz, +7 % at 10 kHz)
  is measured and not corrected; DR 0006 designs the resonance compensation
  only.
- Signoff voltage is undecided, and **core and IO need separate answers**.
  5 V vs 3.3 V is 2.3× in *switching* power at unchanged capacitance, activity
  and frequency — not total device power — and ~1.5× in speed. The open flow
  defaults to 5 V, which works against a battery product, and USB pads need
  3.3 V. A documentation discrepancy has to be resolved first: the
  `gf180mcu_fd_io` **operating-conditions** table specifies 4.5–5.5 V DVDD,
  while its **characterization-corners** page does list 3.3 V corners. Resolve
  that against the pinned models before relying on either. Target 3.3 V and
  ratify it from timing and interface checks.

---

## 10. Capacitive touch — cut

The sponsor cut capacitive touch on 2026-09-17: **the product is
MIDI-keyboard-driven** (USB-MIDI through the MCU, DR 0002), so the chip has no
local playing surface to sense. `rtl-sketch/touch_dp.v` — eight pads, an
all-digital charge-time counter, 714 cells / 19,985 µm² on 7t, never compared
against anything — is deleted; its rows are removed from `area-budget.md`'s
configurations and the sums recomputed there. What it would have cost beyond
its cells was eight bidirectional pads on a package that now needs nine
signal pins in total (ARCHITECTURE.md section 8), and the analog question of
whether gf180mcu's pads discharge measurably through a finger, which nobody
had answered. If a touch surface ever returns, issue 7's own addendum had it
right: touch sensing is a host-MCU job (several common MCUs have it built
in), and the chip would see it as SPI writes like any other key.

## 11. The modal bank is drum hardware, not an optional extra

Issue 1 recorded the modal bank as "the first thing cut if area forces a
choice". That policy was written when the bank was "something you can hit"
beside a monosynth. With drums shipping, the bank is the tuned drum bodies
(ARCHITECTURE.md section 4.2), and if the concurrent TR-808 research confirms
that the bass drum, toms, congas, claves and rimshot are bridged-T resonators
then most of the 808 *is* the bank. The cut policy therefore has to
distinguish **required percussion bodies** — which go only if the drums go —
from **optional struck-bar presets**, the stored bars of `modal_coef_rom_p8`,
which are the cuttable part and cost 5,005 µm² of ROM. The area lever that
does not touch the feature is the derived shared multiplier of ARCHITECTURE.md
section 6 (≈ −0.06 mm² of cells), and Booth (−20 % on the bank, measured).

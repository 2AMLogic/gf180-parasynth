# 0007: The control interface — SPI transport, register-write semantics, applied at the frame tick

- **Status**: proposed, **revision 2** (2026-09-18: the frame is 48 bits, not 32)
- **Date**: 2026-09-17, revised 2026-09-18
- **Decided by**: block agent, from the product constraints of DR 0002 (an MCU is the client), the key model of DR 0003, the operator's proposal in gf180-polysynth issue 7 and the analysis posted there, and the measurements in `docs/area-budget.md`
- **Closes**: contract open items 17.3 (physical layer and every encoding), 17.8 (power-on defaults), 17.10 (the widths of `cut_lo`, `cut_hi`, `track_hz`)

## Context

Contract revision 3 specifies *what* control does — the register image of 5.1,
the writes of 5.2, and the timing rule of 4.3 (a write complete during frame
f applies at the start of frame f+1, in order, atomically) — and leaves the
physical layer OPEN (5.4). Two candidates were on the table:

1. **A UART event stream**, gf180-polysynth's contract section 10: 115 200 8N1,
   status/data bytes, one command per opcode, applied at the next frame
   boundary. Designed for a human or a MIDI cable at the other end.
2. **SPI time-slice "stop packets"**, gf180-polysynth issue 7: the host sends
   one packet per slice of N frames carrying the complete control state (the
   *stops*), with a repeat count; percussion stops are edge-triggered on slice
   boundaries; N = 480 (10 ms) as a strawman, and 256 (5.33 ms) argued for in
   the same thread on drum-timing perception.

Both were written before the client was known. It is now known (DR 0002, the
sponsor's decisions on issue 1): **the client is a CH32V203F8U6 microcontroller
that owns USB and translates class-compliant USB-MIDI into whatever this
record decides.** No human types at this link, no MIDI cable plugs into it,
and the host holds the patch in flash. That fact decides most of what follows.

Two more facts bear on it:

- **The key model is a sequence of writes** (DR 0003). Paraphony is the host
  assigning held keys to `inc_tgt[0..2]`; single or multi triggering is
  GATE_ON or TRIG; a legato pitch change is a bare SET_INC. The model's unit
  of work — `VoiceFx.play(regs, writes, n)` — and the verification obligation
  of contract 16 are literally "a scripted sequence of (frame, write)
  deliveries". GATE_ON and TRIG are *events* (they re-enter ATTACK); they
  cannot be expressed as a level.
- **The register image is large.** The voice alone is 451 bits (three
  oscillators 129, two envelopes 176, the voice registers 146); with the
  drum section it is more. In this library a flop under an enable costs
  92 µm² (`area-budget.md` section 0), so a second copy of the image — which
  an atomically applied whole-image packet needs — is on the order of
  0.04 mm² of cells before the drums.

## Decision

**SPI for the wires; register writes for the meaning; the frame tick for the
time.** One 32-bit SPI transaction is one write of contract 5.2, applied at
the next frame tick exactly as 4.3 says. Nothing about the stop/slice model
is adopted except its transport and its CS framing.

### 1. Physical layer

| | |
|---|---|
| Transport | SPI slave, **mode 0** (CPOL = 0, CPHA = 0): MOSI sampled on the rising edge of SCK, MISO changes on the falling edge, MSB first |
| Pins | `SCK`, `MOSI`, `CS_N` (active low) in; `MISO` out. Four pins. |
| Transaction | exactly **48 bits** (six bytes) between a falling and a rising edge of `CS_N`. A transaction with any other bit count is **discarded** — no partial or over-long write is ever applied, and the stream cannot lose byte alignment (a UART can, and needs the status-bit scheme of the sibling's 10.2 to recover). Revision 1 specified 32; section 2 has the measurement that changed it. |
| Sampling | the receiver is **synchronous to the 12.288 MHz core clock**: `SCK`, `MOSI` and `CS_N` pass through two-flop synchronisers and edges are detected in the core domain. There is no second clock domain and no CDC. |
| SCK | **≤ 2.0 MHz guaranteed** (the design's limit is f_core / 4 = 3.072 MHz with a 50 % duty cycle; the guaranteed figure leaves margin for duty-cycle distortion and the synchroniser). One transaction is therefore ≥ 24 µs. |
| CS_N | must be high for **≥ 4 core cycles (≥ 0.33 µs)** between transactions; the MCU's SPI peripheral does this by default. |
| Rate | at 2 MHz, one write per 24.3 µs — 1.17 frames. The voice image with the master and drum-filter registers (41 writes) takes 1.0 ms; the reference drum kit (100 writes, contract Appendix G) 2.4 ms. Both are written once at boot; the figure that matters for playing is the chord's — three SET_INC and a GATE_ON, 97 µs. |
| MISO | during every transaction the chip shifts out a **32-bit status word** (section 4), loaded at the falling edge of `CS_N`. A host that does not want it leaves `MISO` unconnected. |

### 2. The write: one transaction, one register — REVISION 2

```
byte 0                 byte 1           bytes 2..5
bit 47   46 ... 41  40    39 ... 32        31 ... 0
 F       000000     SEC   A[7:0]           D[31:0]
 flag    reserved   page  address          data, MSB first, right-aligned
```

- `SEC` selects the **page**: 0 the voice and the master (section 3 below),
  1 the drum section, whose map is contract 15.1's, unchanged. Read
  `{SEC, A}` as a 9-bit address split so that neither field straddles a byte.
- `A` selects a register or an action within the page. `D` is 32 bits; a
  register narrower than 32 bits takes the low bits and the rest of `D`
  **MUST be zero** (the implementation ignores them; the contract's "any
  register value is legal, nothing is rejected" holds for the bits that
  exist).
- `F` is the **jump** bit of SET_INC (5.2) and is reserved, MUST be zero, on
  every other address. Bits 46..41 are reserved and MUST be zero.
- On page 0, `A[7]` is reserved and MUST be zero. Page 0's map is unchanged
  from revision 1 except that 0x40–0x7F, which revision 1 reserved for drums,
  are now ordinary reserved addresses — the drums have their own page — and
  0x2C is the new `BVOL`.

#### Why 48 bits and not 32 — the measurement

Revision 1's frame was written before the drum section existed, and reserved
0x40–0x7F for it: **64 addresses for a block that needs 117.** Contract 15.1
had meanwhile specified the drum image as an 8-bit address space with values
up to 32 bits wide. The two are not compatible, and nothing noticed, because
every bench in this repository drives the register *write port* and not the
link. `rtl-sketch/verify_ctl.py` is the bench that drives the link; run
against revision 1 it reports, of the **155 register writes
`model/voice_fx.py` and `model/drums_fx.py` perform for one patch and the
reference kit**:

| corrupted | how |
|---:|---|
| **118** | there is no drum page: every drum write aliases onto a voice address |
| **67** | the address does not fit in 7 bits — `A_PATH` = 0x80, `A_MODE` = 0xC0, `A_RESET` = 0xFF |
| **26** | the datum does not fit in 24 bits — `ENV_CTL` is 27 bits, `MODE_A1` and `MODE_A2` are 26 |
| 37 | survive |

The narrowest frame that carries a 27-bit datum and a 9-bit address is 37
bits, so the frame had to grow. 48 was chosen over 40 because it is six whole
bytes: `A` is one byte and `D` is a 32-bit big-endian word, which is what
contract 15.1 already says the drum image is, so **neither model's address map
moves** and Appendix G's SHA-pinned kit still applies unchanged. A 40-bit
frame would have saved 66 flops (section 9) at the cost of a 9-bit address and
a 30-bit datum straddling byte boundaries in the host's packing, and would
have made contract 15.1's "values up to 32" false.

`rtl-sketch/stubs/spi_ctl_dr7rev1.v` is revision 1's receiver, kept as a
standing negative control: `verify_ctl.py --link dr7rev1` reproduces the table
above at any time.

### 3. Register map

Every register of contract 5.1 has one address. Widths are 5.1's; the write
carries 24 bits and the register keeps its own width.

| A | name | width | write semantics (contract 5.2) | reset |
|---:|---|---:|---|---:|
| 0x00–0x02 | `INC_TGT[k]`, k = A[1:0] | 24 | SET_INC k, D, jump = F | 0 |
| 0x04–0x06 | `WAVE[k]` | **4** | SET_WAVE k, D[3:0] — **0 saw, 1 square, 2 pulse25, 3 tri, 4 sine, 5 shark, 6 revsaw, 7 pulse29, 8 pulse15; 9–15 also sine** (every value is defined). Widened from 3 bits in revision 10 for the Model D waveform set (DR 0012) | 0 (saw) |
| 0x08–0x0A | `W[k]` | 16 | SET_WEIGHT k, D[15:0] | 0 |
| 0x0B | `WN` | 16 | the **noise source's** mixer weight, Q0.15 — the mixer's fourth input (DR 0012) | 0 (silent) |
| 0x0C | `GLIDE` | 24 | SET_GLIDE | 0 (off) |
| 0x0D | `VOL` | 16 | SET_VOL — the voice bus level (contract 12) | 0 |
| 0x0E | `DVOL` | 16 | the drum bus level at the master mix, Q0.15 (ARCHITECTURE.md section 4) | 0 |
| 0x0F | `ROUTE` | 1 | bit 0 `DFILT`: the drum bus passes through the **drum filter** — the second context of the ladder, with its own `DCUT`, `DK`, `DGAIN`, `DOGAIN` — before the master mix (ARCHITECTURE.md section 4) | 0 (bypass) |
| 0x10–0x13 | `AMP_A_INC`, `AMP_D_DEC`, `AMP_SUS`, `AMP_RATE` | 24, 24, 24, 16 | SET_ENV amp | 0 |
| 0x14–0x17 | `FILT_A_INC`, `FILT_D_DEC`, `FILT_SUS`, `FILT_RATE` | 24, 24, 24, 16 | SET_ENV filt | 0 |
| 0x18–0x1A | `CUT_LO`, `CUT_HI`, `TRACK_HZ` | **16** | SET_CUT (closes 17.10, below) | 0 |
| 0x1B | `NSEL` | 1 | the noise colour selector: 0 puts **white** in the mixer and **pink** on the modulation bus, 1 puts **pink** in the mixer and **red** on the bus. One bit, two destinations — the Model D's switch selects a pair (DR 0012) | 0 (white / pink) |
| 0x1C | `K` | 17 | SET_LADDER k | 0 |
| 0x1D | `GAIN` | 20 | SET_LADDER gain | 0 |
| 0x1E | `OGAIN` | 20 | SET_LADDER ogain | 0 |
| 0x1F | `MROUTE` | 3 | bit 0 **OSCILLATOR MODULATION**, bit 1 **FILTER MODULATION**, bit 2 **OSC-3 CONTROL** — with bit 2 clear, oscillator 3's pitch is not modulated, which is the half of the Model D's SW2 that lives in the datapath (DR 0012) | 0 |
| 0x24 | `MMIX` | 16 | the MODULATION MIX **pan**, Q0.15: 0 is oscillator 3 alone, 32768 is noise alone, values above 32768 clamp. The two weights sum to 32768, so the bus is a convex combination (DR 0012) | 0 (oscillator 3) |
| 0x25 | `MWHEEL` | 16 | the modulation AMOUNT — the wheel, Q0.15. A performance control, so it is applied per frame like `TRACK_HZ` (DR 0012) | 0 |
| 0x26 | `MPD` | 16 | pitch-modulation depth at full wheel, **Q3.12 octaves** of peak deviation. The reference value is 0.75 (3072), which is 18 semitones of total swing — the middle of the Model D's 13–23 factory window (DR 0012) | 0 |
| 0x27 | `MFD` | 16 | filter-modulation depth at full wheel, Q3.12 octaves. The reference value is 1.30 (5325); the Model D's floor is 1.224 (DR 0012) | 0 |
| 0x28 | `DCUT` | 16 | the drum filter's cutoff, integer Hz, clamped to 30..21 600 like the voice's; no envelope, no tracking | 0 (30 Hz) |
| 0x2C | `BVOL` | 16 | the **body** bus's level at the master mix, Q0.15 (contract 12; `DVOL` is the mix bus's). The drum section has had two buses since DR 0008 and the output stage has always named two gains; revision 1 had only one address for them | 0 |
| 0x29 | `DK` | 17 | the drum filter's resonance, `4·res` in Q3.14, compensated by the same kc ROM at `DCUT` | 0 |
| 0x2A | `DGAIN` | 20 | the drum filter's input gain, Q4.16 | 0 |
| 0x2B | `DOGAIN` | 20 | the drum filter's output gain, Q4.16 | 0 |
| 0x2D | `DRIFT` | 16 | **per-oscillator drift** depth, Q0.16 (contract 6.11, DR 0019): the rms deviation of three independent bounded walks on the three phase increments, scaled so 65535 is 5.604 cents rms. 0 is off and bit-identical to no drift mechanism at all, which is why it can be added to this page without re-baselining a single register image that predates it | 0 (off) |
| 0x20 | `GATE_ON` | — | `gate ← 1`, both envelopes `seg ← ATTACK`, level unchanged; D ignored | |
| 0x21 | `GATE_OFF` | — | `gate ← 0`; D ignored | |
| 0x22 | `TRIG` | — | both envelopes `seg ← ATTACK`, level and gate unchanged; D ignored | |
| 0x23 | `RESET` | — | every datapath register of contract 14 ← its reset value; D ignored. **The link and the write queue are not touched**: writes queued behind a RESET in the same frame still apply, in order, after it (the sibling's rule, 10.5; it is what makes 4.3's "every write complete during frame f MUST be applied" true through a RESET). | |
| 0x3F | `NOP` | — | no effect; exists so the host can read the status word without changing anything | |
| 0x03, 0x07, 0x2E–0x3E, 0x40–0xFF | reserved on page 0 | | ignored, no effect | |

**Page 1 (`SEC` = 1) is the drum section**, and its map is contract 15.1's,
address for address and bit for bit: `0x00` STOPS, `0x10 + s` ACCENT,
`0x20 + i` OSC_INC, `0x40 + 4e` ENV_CTL / ENV_PEAK / ENV_RATE, `0x80 + p`
PATH, `0xC0 + 4m` MODE_A1 / MODE_A2 / MODE_AMP / MODE_NUM, `0xFF` RESET.
`rtl-sketch/drum_regs.v` holds that image and drives `drum_kit`'s buses.

**There are two soft resets, one per page**, because there are two register
images: page 0's `0x23` resets the voice datapath (contract 14), page 1's
`0xFF` resets the drum section (contract 15.8). Neither touches the link or
the queue, so writes queued behind either still apply, in order, after it. A
host that wants the whole chip silent sends both.

### 4. The status word (MISO)

Loaded at the falling edge of `CS_N` and shifted out MSB first during the
transaction, whatever the transaction writes:

```
bits 31:24  ID       0x4D ('M')            constant; the host checks the link at boot
bits 23:20  VERSION  0x2                   this record's register map (revision 2; revision 1 read 0x1)
bits 19:16  FLAGS    [3] overrun   the datapath was still busy at a frame tick (sticky; a defect, never expected)
                     [2] queue     the write queue was non-empty when the word was loaded
                     [1] overflow  a write was dropped because the queue was full (sticky, cleared by this load)
                     [0] fresh     no write has been accepted since hardware reset
bits 15:0   FRAME    the 16-bit frame counter (frame 0 = the first frame after reset, contract 4.1), wrapping
```

`fresh` lets the MCU detect an unexpected chip reset (brown-out, a watchdog
on the board) and re-send the image; `FRAME` lets a sequencing host phase
itself to the chip's frames if it wants to, without a tick pin.

### 5. Which frame a write lands in

Contract 4.3 defines a write as *received during frame f* by the cycle in
which the core accepts its last unit. For this link:

- **The unit is the transaction, and its acceptance cycle is the core cycle
  in which the synchronised rising edge of `CS_N` is registered with a bit
  count of exactly 32.** That cycle is `c`; the write is received during
  frame f if `tick_f ≤ c < tick_{f+1}`, a transaction accepted in the tick
  cycle belonging to the frame that starts in that cycle — 4.3 verbatim.
- Accepted writes enter a **queue of depth 4** in acceptance order. At each
  tick the queue's occupancy is snapshotted, and that many writes are applied,
  **one per cycle from cycle 2 of the new frame (cycles 2..5 for four), in
  order**, before any datapath block reads a control register (every block
  starts at cycle 8, `synth_top`'s `GO_CYCLE`). A write accepted in the tick
  cycle or later belongs to the new frame and waits for the next tick.
- **The queue cannot overflow within the specification**: at SCK ≤ 2 MHz a
  48-bit transaction plus the CS_N gap takes ≥ 24.3 µs, longer than the
  20.83 µs frame, so **at most one** write completes in any frame, against a
  depth of four. (Revision 1's 32-bit frame allowed two.) A host outside the
  specification that does overflow it loses the write and sets the sticky
  `overflow` flag; nothing else is disturbed.
- Each write is applied exactly once and atomically — it names one register
  or one action, so atomicity is a single register write.

*Informative:* pin to acceptance is three core cycles (two synchroniser
stages and the edge detector), a constant. Because the pin is asynchronous
to the core clock, a `CS_N` edge within about one core cycle of a frame tick
may be accepted in either frame; the host cannot tell which and the contract
is satisfied either way, since the frame is defined by the acceptance
cycle. The same is true of any physical layer.

*Informative:* a musical event that is several writes — a chord is three
SET_INC and a GATE_ON, a patch change is 34 writes — may straddle a tick, so
its registers change over consecutive frames, 20.83 µs apart at the maximum
rate. That is the ordering rule of 4.3 working as written. A "group commit"
was considered and rejected (Alternatives).

### 6. Power-on defaults — closes 17.8

**Every control register resets to 0, as contract 14 already lists; a bare
GATE_ON is silent.** The sibling's non-zero envelope defaults (its section 9)
exist so that a human sending only NOTE_ON hears something. Here the client
is an MCU with the patch in flash; it writes the whole image at boot (34
writes, 0.54 ms) and again whenever `fresh` reads 1. Non-zero defaults would
cost a set/reset value per register bit for nothing the product uses, and
would put a second, silent copy of "the default patch" in metal beside the
one in the MCU's flash, to drift apart. `WAVE` = 0 means saw (section 3), so
the reset encoding of `wave[k]` is now defined too.

### 7. The cutoff register widths — closes 17.10

`CUT_LO`, `CUT_HI` and `TRACK_HZ` are **16 bits unsigned**, the width contract
5.1 proposed and the model has clamped to since revision 2. The evidence was
already in 5.1 and 5.5: the largest value any host conversion produces is
50 175 (full keyboard tracking at note 127), which fits, and any larger
value is indistinguishable after the clamp of section 10 (30..21 600 Hz). The
sum of section 10 is computed exactly at 19 bits signed before that clamp:
`cut_lo` (16 u) + `(span · fe) >> 15` (span 17 s, so |·| < 2^16) + `track_hz`
(16 u). This record fixes the width because the write format fixes what the
host can send; the arithmetic is unchanged.

### 8. What the MCU does (informative)

USB-MIDI arrives in 1 ms USB frames. The firmware is the reference host of
contract 5.6 (`voice_fx.KeyHost`) plus the conversions of 5.5:

| MIDI | writes |
|---|---|
| note on, no key held | SET_INC k for each oscillator that changes, TRACK_HZ, GATE_ON (single trigger) |
| note on, keys held | SET_INC for the oscillators that change (paraphonic allocation, DR 0003); TRIG if multi-trigger |
| note off, keys remain | SET_INC back to the remaining keys' pitches |
| last note off | GATE_OFF |
| CC 74 / 71 / 5 / 7 … | SET_CUT, SET_LADDER, SET_GLIDE, SET_VOL through 5.5 |
| program change | the patch image, 34 writes |

Worst-case latency from a MIDI event to the sample that reflects it: ≤ 1 ms
(USB) + firmware + 16 µs (one transaction) + ≤ 20.8 µs (the next tick). The
link adds about 40 µs to a 1 ms budget; a 256-frame slice would add up to
5.33 ms, a 480-frame slice up to 10 ms.

## Why, against the two facts

**The MCU-client fact.** An MCU forwarding MIDI has *events* in hand and a
DMA-capable SPI master. Register writes are a one-to-one translation with no
state to maintain on the host; a slice packet makes the MCU keep the whole
image, resend it 187.5 times a second, keep its slice timer phase-locked to
the chip's frame counter (or the packet lands in the wrong slice), and still
encode GATE_ON and TRIG as per-packet edge flags — i.e. as events. The
firmware for the event interface is smaller and has fewer ways to be wrong,
and every 5.33 ms of slice latency is spent on a product whose whole claim
is a playable instrument. The UART's advantages — a human can type at it, a
DIN-MIDI opto feeds it — are advantages for a client this product does not
have; its costs — a baud rate 12.288 MHz cannot divide to (115 200 needs a
divisor of 106.67; the sibling accepts −0.31 % at 107), no framing so a lost
byte desynchronises until the next status byte, 26× less bandwidth than
2 MHz SPI, and a second pin for any readback — are real.

**The paraphonic key model.** DR 0003 made the chip a mechanism and the host
the policy, and made the model's unit of work a sequence of writes. This
record is that sequence on wires: the reference sequences of contract 16 are
executable on the link exactly as the model plays them, frame for frame, and
the bench of 16.2 drives the same write port the queue drains into. A state
model would need a translation layer between the model's writes and the
packets in both directions — in the firmware and in the testbench — and
the translation would be where the timing was lost.

### 9. Where the drum image is kept, and what the frame cost — revision 2

Widening the frame was only half the defect. `drum_kit.v` takes its entire
control image as **input ports**, and until revision 2 nothing drove them but
a testbench reading a file: there was no storage on the chip for a write to
land in. Both halves are decided here.

**The image is flops, all of them writable**, in `rtl-sketch/drum_regs.v`.
Measured with yosys generic `synth` (no liberty; the `synth_count.sh` flow —
these are **cell counts, not µm²**, and no gf180 liberty was available in the
environment that produced them, so no area figure is quoted):

| | cells | of which flops |
|---|---:|---:|
| `drum_regs` (the drum image) | 2 598 | **2 276** |
| `spi_ctl`, 48-bit frame | 760 | 317 |
| `spi_ctl`, 32-bit frame (revision 1) | 624 | 251 |
| `synth_top`, whole hierarchy | 48 348 | 7 860 |

So the wider frame costs **+136 cells and +66 flops**, and the drum image is
**2 276 of the chip's 7 860 flops — 29 %**. In this library every enabled bit
is a flop plus a `mux2`, so the routed cost is larger than the flop count
alone suggests; that number belongs to whoever runs the flow with the PDK
installed, and it is a number this project had not been counting at all.

**Rejected: a ROM of the 808 kit with a sparse writable overlay.** It is the
obvious saving — `kit_808()` writes the same 100 values every time — and it
is rejected on a precedent this record already set. Section 6 refused
non-zero power-on defaults because they "would put a second, silent copy of
the default patch in metal beside the one in the MCU's flash, to drift
apart"; a kit ROM is exactly that, one order of magnitude larger, and DR 0008
section 5 says in terms that the kit is "a table, not hardware" and that
"fitting a measured unit is a table change". DR 0003's division — the chip is
the mechanism, the host is the policy — decides it.

**Not rejected, not taken here: an SRAM or a small coefficient ROM read per
step.** `modal_dp_rom.v` already demonstrates the shape (the bank reads one
coefficient per cycle from a ROM, in the cycle before the multiplier needs
it), and the drum datapath is sequenced over 12 modes, 12 envelopes and 16
paths, so it could read its image a word at a time instead of seeing all
2 276 bits at once. That is a change to `drum_kit.v` / `drum_dp.v` /
`modal_dp.v`, which are another agent's files, so it is recorded as the next
decision rather than made here. The measurement above is what it has to beat.

**Write atomicity is the drain, and it is measured.** Queued writes are
applied at cycles 2..5 and `go` is at cycle 8, so no register the drum engine
reads can change between `frame_tick` and `body_valid`: the buses are stable
for the whole pass by construction, which is the same guarantee DR 0008
section 2 asks for. A coefficient *pair* split across two frames (`MODE_A1`
in one, `MODE_A2` in the next) does leave one frame running on a mixed pair —
that is 4.3's ordering rule working as written, the same as a chord
straddling a tick. `verify_synth_top.py` retunes the bass drum while it rings,
one coefficient per frame, on purpose, and the chip stays bit-exact against
the model through it.

## Alternatives considered

- **Keeping the 32-bit frame and splitting wide writes** (a `DATA_HI` latch
  consumed by the next write, or two transactions per wide register).
  Rejected: it breaks 4.3's "each write is applied exactly once and
  atomically". A pair that straddles a tick would apply a coefficient with a
  stale high byte — for `MODE_A1` that is not a 20.8 µs skew, it is an
  arbitrary pitch, and possibly `r > 1` and a diverging resonator. The queue
  would also have to know which addresses are wide, which is the
  address-dependent length table this record rejected below for smaller stakes.
- **A 32-bit frame with an 8-bit flat address and a 24-bit datum.** Does not
  exist: 1 + 8 + 24 = 33. Dropping `F` to make room only reaches 32 bits with
  a 24-bit datum, which still cannot carry `ENV_CTL`'s 27.
- **Paging the address space with a bank register.** Solves the address and
  nothing else — `ENV_CTL` and the coefficients still do not fit in 24 bits —
  and adds a mode bit the host must keep in step with the chip through a
  reset, which is the slice-clock failure mode this record rejected once.
- **A 40-bit frame, `{F, A[8:0], D[29:0]}`.** Cheapest that works: 66 flops
  less than 48. Rejected on the host and the contract, not on the gates —
  neither field is byte-aligned, so the firmware packs a 40-bit integer by
  hand instead of writing a byte and a `uint32`, and `D` at 30 bits makes
  contract 15.1's "values up to 32" untrue, which would move the drum model's
  register map and with it Appendix G's SHA-pinned kit.
- **SPI time-slice packets (issue 7) at 256 frames.** Kept: SPI, mode 0,
  CS framing, "the last bit before the boundary lands at the boundary".
  Rejected: the state model. It quantises live timing to 5.33 ms by
  construction (my own analysis on issue 7 — 10–20 ms is perceptible in
  ensemble timing, 5.33 ms is not, but 20.8 µs is better for free); its edge
  semantics for percussion and triggers are events anyway; an atomically
  applied whole-image packet needs a shadow image (≈ 0.04 mm² of cells at
  92 µm² per bit before drums; the sibling's 8-deep 35-bit command FIFO
  alone measured 33 505 µm²); and it needs the host's slice clock locked to
  the chip's. What it bought — explicit host-owned timing and a sequencer's
  repeat count — a sequencing host gets from `FRAME` in the status word and
  from timing its own writes.
- **UART event stream (the sibling's section 10).** Rejected for the client;
  reasons above. The sibling keeps it because its client is a human.
- **Raw MIDI in (31 250 baud) with the mapping on the chip.** Rejected: the
  host conversions of 5.5 are float (Hz to increment, seconds to rate,
  `k_onset`), the key policy is the host's by DR 0003, and the MCU is
  already there.
- **Variable-length transactions (address-dependent byte counts).** Rejected:
  a length table in the receiver for a saving of 1–3 bytes per write when
  the link has 26× the bandwidth it needs; fixed 32 bits is one shift
  register, one counter, and DMA-friendly.
- **A "hold" bit for group commit** (writes marked hold apply together with
  the next unmarked one). Rejected: the queue would have to hold a whole
  patch — 34 × 32 bits, on the order of 0.1 mm² at the FIFO's measured
  120 µm² per bit — to make a 20.8 µs skew that no one can hear atomic.
- **An asynchronous SPI slave clocked by SCK** with a CDC handshake into the
  core. Rejected: a second clock domain, constraints and a synchroniser for
  every completed word, for an SCK ceiling the product does not need; the
  synchronous receiver has none of that and reaches 3 MHz.
- **Non-zero power-on defaults.** Rejected, section 6.
- **A frame-tick output pin.** Rejected: `FRAME` in the status word serves a
  sequencing host, and the I2S `LRCLK` *is* the frame clock on a pin already.

## Consequences

- Contract 5.2 (encodings), 5.4 (physical layer: this record), 14 (`wave`
  reset encoding; RESET leaves the link and queue alone), 17.3, 17.8 and
  17.10 change; revision 4. Sections 4.3 and 16 are unchanged and are what
  this record implements.
- The register-level write port of contract 16.2 is the queue's output:
  `{valid, F, A[6:0], D[23:0]}`, one per cycle from cycle 2 of a frame. A
  bench drives it directly (`rtl-sketch/tb_voice.v`, which is how the voice
  was shown bit-exact); the SPI receiver is exercised separately by driving
  the pins (`tb_synth_top.v`).
- `DVOL`, `ROUTE` and the drum filter's `DCUT`, `DK`, `DGAIN`, `DOGAIN` are
  new registers (the master mix and the drum routing of ARCHITECTURE.md
  section 4); they are not in the revision-3 voice and are proposed with it.
  The drum bus itself is 19 bits, Q4.15, at the master mix (ARCHITECTURE.md
  section 4), so `DVOL` scales a word with headroom, not a clipped one.
- The drum section decodes addresses 0x40–0x7F from the same port; what
  they mean is the `drums` branch's record.
- RTL: `rtl-sketch/spi_ctl.v` (receiver, queue, status word, drain) and
  `rtl-sketch/drum_regs.v` (the page-1 image), driven through the pins by
  `rtl-sketch/tb_ctl.v` / `verify_ctl.py` and `rtl-sketch/tb_top_bx.v` /
  `verify_synth_top.py`. Measured: pin-to-acceptance 3 cycles; every queued
  write applied in cycle 2, before `go` at 8; the status word reads back
  `0x4D210001` on the first transaction after reset (revision 1 read
  `0x4D110001`); all 155 of the two models' writes arrive intact.
- `verify_ctl.py`'s negative controls, each demonstrated to turn it red:
  `SPI_ADDR7` (67 wrong addresses), `SPI_DATA24` (26 wrong data),
  `SPI_NOSEC` (118 wrong sections), `SPI_ANYLEN` (2 mis-sized transactions
  accepted), `SPI_DRAIN_LATE` (155 writes applied at cycle 10, after `go`),
  and `--link dr7rev1` (118 corrupted).
- Board: the 12.288 MHz core clock cannot come from the MCU's own crystal
  (USB needs 48 MHz and 48 : 12.288 = 125 : 32), so it is a separate
  oscillator or crystal on the board; that is ARCHITECTURE.md's clock
  section, not this record's.

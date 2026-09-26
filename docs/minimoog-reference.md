# The Minimoog Model D — an engineering reference for a digital Minimoog

**Purpose.** This is to the voice what `docs/tr808-reference.md` is to the drum
section: the primary-source reference for *what the instrument actually does*,
so that the numbers in `model/voice_fx.py` and the properties in
`model/test_moog_acceptance.py` have a citation behind them instead of a
plausible constant.

It exists because of a gap the acceptance suite could not close on its own.
`model/test_moog_acceptance.py` has always checked the implementation against
**our own decision records** — self-oscillation onset, 24 dB/octave stopband,
PolyBLEP foldback suppression, glide geometry. That proves we built what we
said. **Nothing had ever checked that what we said is a Minimoog.** Every claim
below is tagged, and every property in the suite that cites this file cites a
specific tag.

**How claims are tagged.**

- **[verified: KEY]** — read directly in the cited primary source. Source keys
  are expanded in §0.
- **[inferred]** — computed by me from verified values, with the arithmetic
  shown. Reproducible; not independently measured.
- **[ours]** — a decision this chip makes that the instrument does not
  constrain, or a deliberate deviation. Stated so it cannot be mistaken for a
  fact about the Model D.
- **[could not establish]** — no primary source found; do not build on it.

**A note on propagated constants.** This project has been burned by exactly one
failure mode more than any other: a number that is right-looking, widely
repeated, and wrong. §6 records one caught during this work — a tuning
polynomial constant printed as `0.4995` in two of our own files where the cited
source spells it `0.4955`. Prefer the tag over the number.

---

## 0. Sources actually consulted

| key | source | what it gives | reached |
|---|---|---|---|
| **SM** | Moog Music / Norlin, *Minimoog Model 204D Service Manual*, manual no. 993443232402. Scan: <https://archive.org/details/synthmanual-moog-minimoog-service-notes> (OCR text: <https://archive.org/download/synthmanual-moog-minimoog-service-notes/moogminimoogservicenotes_djvu.txt>) | Specifications; §2.2.2–2.2.3 and §2.4–2.5 (modulation mix amplifier, noise generator); §2.3 the "D" oscillator board; §2.13–2.18 the older oscillator board and oscillators 1/2/3; §5.14–5.37 the factory adjustment and acceptance procedures | yes, full OCR text |
| **1448** | R. A. Moog Co. drawing **1448**, "WAVEFORM SWITCHING MINI 'D'", Circuit #000 Front Panel. In: *Moog MiniMoog Schematics*, <https://archive.org/details/Moog_MiniMoog_Schematics> (page 3) | SW6/SW7/SW8, the six waveform positions per oscillator, R030/R031 (the shark-tooth divider), R032/R033/R034 (the pulse-width divider), and oscillator 3's reverse-sawtooth contact | yes, page read as an image at 150 dpi |
| **1431** | R. A. Moog Co. drawing **1431**, "RANDOM SIGNAL GENERATOR (FOR THE MINI SYN.)", 11/3/70, Board #3 Connector 3. Same item, page 6 | Q901 the noise transistor, the amplifier, the "−3 db/OCTAVE FILTER" (R907/C904/R908/C905/R909), the "100 Hz Lowpass Filter" (R914/C908), and the −4 dBm labels on the white, pink and red outputs | yes, page read as an image at 150 dpi |
| **HUOV** | A. Huovilainen, "Non-Linear Digital Implementation of the Moog Ladder Filter", DAFx-04. <https://www.dafx.de/paper-archive/2004/P_061.PDF> | the filter structure DR 0001 implements, and the `fcr` tuning polynomial DR 0011 adds | yes |
| **SST** | `sst-filters`, `include/sst/filters/VintageLadders.h`, namespace `Huov` (Surge XT's `LP Vintage Ladder` Type 2). <https://github.com/surge-synthesizer/sst-filters> | the tuning polynomial's constants as a working implementation spells them; attributes its own origin to Victor Lazzarini for Csound 5 | yes, source read |

**Not reached in this pass**, stated rather than guessed: the Minimoog *owner's*
manual (the panel legend order per knob position), Moog's own published
frequency-response or noise-spectrum measurements, and any measurement of a
real Model D. No web search was available for this pass; every source above was
fetched by direct URL. **Nothing in this file is a measurement of a physical
instrument.**

---

## 1. What the instrument is, at the level this chip copies

| | Model D | this chip |
|---|---|---|
| sound sources | **5: three oscillators, one noise source, one external input** **[verified: SM Specifications]** | 4 — the external input is **[ours]**, omitted: there is no audio input pin |
| oscillator range | 0.1 Hz to 20 kHz in **six overlapping ranges** **[verified: SM Specifications]** | the 24-bit increment register spans 0.0029 Hz to 24 kHz; the range switch is a host-side offset (§5) |
| oscillator waveforms | **six per oscillator** **[verified: 1448]**, see §2 | six, plus two of our own **[ours]** |
| noise | **white or pink** for audio, **pink or red** for modulation **[verified: SM 2.5]** | white/pink for audio, pink/red for modulation (§3) |
| filter | low-pass, **24 dB/octave**, resonant peak, cutoff continuously variable **10 Hz .. 20 kHz** **[verified: SM Specifications]** | 24 dB/octave (locked in the acceptance suite), cutoff clamped to 30 Hz .. 21.6 kHz |
| envelopes | two contour generators (filter, loudness) | two ADSRs |
| modulation | oscillator 3 and noise, panned, to pitch and/or filter (§4) | §4 |

---

## 2. The oscillators

### W1. Six waveforms per oscillator [verified: 1448]

Drawing 1448 shows SW6, SW7 and SW8 — one six-position waveform switch per
oscillator — with these inputs:

| position | oscillators 1 and 2 | oscillator 3 |
|---|---|---|
| triangle | `#n TRIANGLE IN` | `#3 TRIANGLE IN` |
| second | the junction of **R030 47 k** (from the saw) and **R031 10 k** (from the triangle) — the *shark-tooth* | **`#3 REV SAW`** — a reverse sawtooth |
| sawtooth | `#n SAW IN` | `#3 SAW IN` |
| square | the pulse output, width deck at **0 V** | same |
| wide rectangular | width deck at **−1.5 V** | same |
| narrow rectangular | width deck at **−2.5 V** | same |

The Specifications page agrees: "OSCILLATOR WAVEFORM OUTPUTS: Triangular,
Sawtooth, Triangular-Sawtooth Mix… Reverse Sawtooth… 3 widths of Rectangular"
**[verified: SM Specifications]** — its parenthetical oscillator numbers are
garbled by the OCR, but §2.3 settles it: "all three oscillators are identical
from this point on, **except for the addition of a reverse sawtooth circuit
associated with Q20 in oscillator 3**", and §2.16 gives oscillator 1 the mix:
"Resistors R36 and R37 on the WAVEFORM select switch sum the sawtooth and
triangle to generate another waveform" **[verified: SM 2.3, 2.16, 2.18]**.

**The legend order on the panel knob is [could not establish]** from these two
sources — which contact is position 2 and which is 3 on SW8 cannot be read off
the drawing unambiguously. It does not matter to the chip: waveform selection
is a register value, and the order is the host's legend.

### W2. All waveforms reach the switch at the same amplitude [verified: SM 2.3]

"The values of this network are chosen so that the voltage appearing on the
sawtooth output (pin 13B) is **precisely +1.75 VDC to −1.75 VDC**"; "The output
of the follower IC14B is a triangular waveform which goes from **+1.75 VDC to
−1.75 VDC**"; the rectangular output is divided "to give a **0 VDC to −3.5 VDC**"
wave. Three shapes, 3.5 V peak-to-peak each.

**Consequence:** our Q1.15 full-scale-per-shape convention is faithful, and the
shark-tooth mix below is a pure amplitude mix with no scaling correction.

### W3. The shark-tooth is 10/57 saw + 47/57 triangle [inferred]

The switch taps the junction of R030 (47 k, from the saw buffer) and R031 (10 k,
from the triangle buffer) **[verified: 1448]**. Both sources are buffered
emitter followers, so unloaded the junction sits at

```
V = (V_saw·R031 + V_tri·R030) / (R030 + R031)
  = (10·V_saw + 47·V_tri) / 57
  = 0.1754·V_saw + 0.8246·V_tri
```

In Q0.15: **5749 and 27019, which sum to exactly 32768** (`SHARK_W_SAW`,
`SHARK_W_TRI`).

The wiper's load does **not** change this. With the tap fed from two sources
through R030 and R031 and loaded by the mixer volume pot R_L to ground, the two
contributions are in the ratio `(1/R030) : (1/R031)` = 10 : 47 **whatever R_L
is** — the load scales both equally and changes the level, not the mix. The
saw's own source impedance (it reaches the switch through the bias divider
R114/R115/R116, §2.3, where the triangle comes from an op-amp buffer) can only
move the ratio *further* toward the triangle.

**A recorded disagreement.** The reference-emulation comparison recorded a
shark-tooth target of `h2 −16.9, h3 −18.4, h4 −22.9, h5 −26.3, h6 −26.4,
h7 −30.8` at 110 Hz. Measured on ours: `h2 −21.7, h3 −18.2, h4 −27.7,
h5 −25.8, h6 −31.2, h7 −30.2`. **The odd harmonics agree within 0.6 dB and
every even harmonic is uniformly 4.8 dB low.** A shark-tooth's even harmonics
come entirely from its sawtooth share, so that is a single-parameter
disagreement: the reference implies a saw share near 0.25–0.30 where drawing
1448's resistors give 0.175. The drawing was re-read at 150 dpi to confirm the
topology and the values; the ratio is load-independent, as above. **We are not
changing the constant on the strength of an emulation**, and the experiment
that would settle it is a real Model D or a second scan of drawing 1448. This
is contract open item 16.

The shark-tooth's step at the wrap is therefore **10/57 of the sawtooth's**, so
its PolyBLEP correction is the saw's scaled by the same weight — which is what
`OscFx.render` computes, by mixing the *already-corrected* saw with the
triangle exactly as the switch mixes the two buffered outputs.

### W3a. The square's duty is 50 %, and Moog hand-selected a resistor to make it so [verified: SM 2.3]

Recorded because a reference emulation measured **52 %** with **h2 at −24 dB**,
and the service manual is explicit that this is a *unit out of trim*, not the
design:

> "Resistor R137 is a selected resistor whose value is chosen to achieve
> accurate symmetry in this output waveform. **This symmetry is important to
> achieve an accurate 50 percent duty cycle** of the rectangular waveform
> appearing on pin 15B."

A hand-selected part per unit, whose stated purpose is the 50 %. Our square is
a true 50 % and therefore has no even harmonics at all, which is what the
circuit is trimmed to produce. **Whether to model per-unit drift** — a slightly
asymmetric square is audibly fatter, and three oscillators drifting against
each other is part of what a Minimoog sounds like — **is a separate and real
question**, and it is a musical decision rather than a fidelity one. It is
contract open item 17; it is one constant if taken.

The same comparison recorded the two rectangles as **14.3 % and 16.7 %**.
Those are 1/7 and 1/6, which is what reading a duty cycle off the position of a
spectral null produces. SM 2.3 pins 15 % directly, in words, and drawing 1448's
divider gives the third tap. Ours are 29 % and 15 %.

### W4. The three rectangular widths are 50 %, 29 % and 15 % [verified + inferred]

Drawing 1448's second switch deck selects one of three nodes on a divider from
ground to −10 V: **R034 1.5 k, R033 1 k, R032 7.5 k** — 10 k total, 1 mA, so the
taps are **0 V, −1.5 V and −2.5 V** **[verified: 1448]**.

The service manual pins both ends of that control range: "When the control
voltage on pin 16B is 0.0 VDC, a square wave output… appears"; "When the
voltage applied to the control input on 16B is taken to **−2.5 VDC, a 15
percent duty cycle** should appear on pin 1 of IC18A" **[verified: SM 2.3]**.
§2.16 says the same of the older board: "varies from a 50 percent to a 15
percent duty cycle."

The comparator fires where a **linear** ramp crosses a fixed threshold, so duty
is linear in the threshold voltage, and the middle tap is

```
50 % − 1.5 V × (50 − 15) %/2.5 V = 29 %          [inferred]
```

In 24-bit phase: 8 388 608, **4 865 393** and **2 516 582**.

`pulse25` (25 %) is **[ours]** and is **not** a Model D width. It is kept
because contract revision 4 shipped it and every bit-exact expectation in the
repository references it; it is documented as ours rather than quietly counted
as one of the three.

### W5. Oscillator 3's reverse sawtooth is an inversion of its own sawtooth [verified: SM 2.3, 2.18]

"Oscillator 3 has a sawtooth inverter circuit comprised of R123, R124, R128,
R129, R130, R133, R134 and Q20. This is a standard common emitter transistor
inverter… to provide a reverse sawtooth signal which goes from +1.75 VDC to
−1.75 VDC." So the reverse saw is the *buffered, already-shaped* sawtooth
negated — which is why the model negates the band-limited saw rather than
running a second PolyBLEP.

A reverse sawtooth is spectrally identical to a sawtooth and, alone through an
odd-symmetric nonlinearity, inaudible. It earns its place in two situations,
both of which the Model D puts it in: beating against another oscillator, where
the phase relationship is audible, and **as a modulation source**, where a
falling ramp sweeps the opposite way from a rising one.

### W6. Our sawtooth runs the other way up from the Model D's [ours]

The Model D's oscillator core is a **negative-going** relaxation ramp: "a +4
volt to −4 volt sawtooth appears on the collector of Q3" **[verified: SM 2.16]**.
Ours rises. The triangle is derived from the saw in both, so the two shapes
keep the same *relative* phase and the shark-tooth mix is identical in shape;
the whole voice is simply sign-inverted with respect to a Model D, which is
inaudible and consistent across every shape.

### W7. `sine` is not a Model D waveform [ours]

It is in the contract and the wave-code table because the chip has a sine ROM
for other reasons. Listed here so that "our oscillator has more waveforms than a
Minimoog" cannot be mistaken for a claim of fidelity.

---

## 3. The noise source

### N1. It is one of five mixer sources, not an afterthought [verified: SM Specifications]

"NO. OF SOUND SOURCES: 5 (3 Oscillators, 1 Noise Source, 1 External
Input/Microphone Preamp)." Noise through the ladder is a signature Minimoog
sound; before this change `model/voice_fx.py` contained **zero** references to
noise of any kind.

### N2. White, pink and red, with a two-position selector [verified: SM 2.2.3, 2.5]

"The Minimoog contains a noise generator using a transistor generating white
noise in the range of −60 dB which is amplified to produce white, pink or red
noise, selected by the noise selector switch. **White or pink noise is used for
audio and pink or red for modulation.**"

So the panel's WHITE/PINK switch selects a *pair*:

| switch | audio gets | modulation gets |
|---|---|---|
| WHITE | white | pink |
| PINK | pink | red |

`nsel` is that one bit. Red is never heard directly — it exists only to make
the modulation slower and rounder than pink.

### N3. The generator is a transistor in avalanche breakdown [verified: SM 2.5]

"…uses a small signal transistor operated in the avalanche mode. The
base-to-emitter junction is biased in reverse breakdown… Transistor Q15 is the
noise generator transistor which is **selected, burned-in and retested** for
uniform noise clear of pops and clicks."

**[ours]** We use a 31-bit maximal-length LFSR, `x^31 + x^15 + x^13 + x^11 + 1`,
16 steps per frame, the word read as signed Q1.15 — the same polynomial and
step count the drum section already carries (contract 15.4), where it is
separately justified. An LFSR's amplitude distribution is uniform where an
avalanche source's is Gaussian; the spectra are both flat, and the spectrum is
what the filter hears.

**The two generators do not share a seed.** The drum section seeds with 1; the
voice seeds with the drum section's state advanced **1 060 921** steps
(`0x7F215FF7`). Sharing one generator would have been nearly free in area — it
is 31 flip-flops and 48 XOR gates — but it would make the voice's noise and the
drums' noise **the same signal**, and two identical noises sum at +6 dB where
two independent ones sum at +3. The lag is deliberately not a multiple of 16, so
the 16-bit words are not merely a time-shift of each other; two shifts of one
m-sequence cross-correlate at −1/(2³¹−1). Sharing the *generator* would also
have required editing `rtl-sketch/drum_dp.v` and `rtl-sketch/synth_top.v`, which
another agent owns.

### N4. Pink is a two-leg RC staircase, and the drawing calls it −3 dB/octave [verified: 1431]

Drawing 1431 labels the section between the white output and the pink amplifier
"**−3 db/OCTAVE FILTER**". Its five components are **R907 10 k** in series from
the white emitter follower, with two shunt legs to ground: **C904 0.12 µF +
R908 3.3 k**, and **C905 0.033 µF + R909 240 Ω**. §2.5 names the same five: "The
white noise output is filtered by R16, C3, R8, C2 and R13 to provide pink
noise" (that board's designators).

Evaluating that network:

```
H(s) = (1+sR908C904)(1+sR909C905)
     / ( sR907[C905(1+sR908C904) + C904(1+sR909C905)] + (1+sR908C904)(1+sR909C905) )
```

| f | 20 Hz | 100 Hz | 500 Hz | 2 kHz | 8 kHz | 20 kHz |
|---|---|---|---|---|---|---|
| gain | −0.22 dB | −4.57 dB | −11.63 dB | −16.09 dB | −24.83 dB | −30.21 dB |

Least-squares slope **20 Hz .. 20 kHz: −3.10 dB/octave**, maximum deviation
3.05 dB from that straight line; over 100 Hz .. 10 kHz, **−3.12 dB/octave**,
deviation 1.83 dB **[inferred]**. The drawing's label is accurate and the
staircase ripple is the price of two legs.

**[ours]** We implement the bilinear transform of exactly this network at
48 kHz as a biquad, coefficients `PINK_B` (Q21) and `PINK_A` (Q14). It tracks
the analog network within **0.03 dB below 2 kHz** and is **2.5 dB low at
20 kHz** — bilinear frequency warping, on a component that is 30 dB down.
Quantising the coefficients costs a further 0.095 dB worst case.

### N5. Red is one more pole at about 100 Hz [verified: 1431 label; inferred: the value]

Drawing 1431 labels the next section "**100 Hz Lowpass Filter**", and §2.5 says
it is a single R and C: "The pink noise output is then filtered by R12 and C7
and amplified by Q6 to provide the red noise output." On the drawing the series
resistor is **R914 10 k** and the shunt capacitor **C908 0.15 µF**:

```
f = 1 / (2π · 10 kΩ · 0.15 µF) = 106.1 Hz          [inferred]
```

which is the "100 Hz" of the label. In Q0.16 the one-pole coefficient is **904**.

### N6. All three colours leave at the same level [verified: 1431, SM 5.27]

The drawing labels the white (pin 5), pink (pin 6) and red (pin 2) outputs
**−4 dBm each**, and each colour has its own make-up amplifier (Q903, Q904,
Q906). §5.27's acceptance test asks for "both WHITE and PINK noise… Noise level
should be **−5 ± 3 dB**" at the same output, i.e. the switch is not a level
change.

**[ours]** Our three colours are equalised by *noise power*: the pink and red
paths carry the exact make-up gain that puts their RMS on white's. Measured over
200 000 frames: white −16.81 dBFS, pink −16.78, red −16.85 — **within 0.07 dB**.

### N6a. Measured against the reference emulations [ours, measured]

Four properties, our three colours against the three references. The
distribution columns are the ones that were expected to fail and the result is
not what the expectation was:

| source | slope dB/oct | vs saw | crest dB | kurtosis |
|---|---|---|---|---|
| **ours, white (raw)** | +0.03 | −12.0 | **4.8** | **1.80** |
| **ours, white through the ladder, 600 Hz** | — | — | **11.6** | **2.92** |
| **ours, white through the ladder, 2 kHz** | — | — | **11.8** | **2.85** |
| **ours, white through the ladder, 8 kHz** | — | — | **10.0** | **2.54** |
| **ours, pink** | **−3.07** | −12.0 | **12.3** | **2.91** |
| Mini V3 white | −0.2 | −7.1 | 8.1 | 2.23 |
| Mini V3 pink | −3.0 | −9.3 | 13.0 | 3.00 |
| Surge white | −0.1 | +0.6 | 11.3 | 2.64 |

**Pink hits every column** — slope −3.07 against −3.0, crest 12.3 against 13.0,
kurtosis 2.91 against 3.00 — and it hits them from drawing 1431's component
values rather than from a fit.

**Raw white is uniform**, as any multi-bit LFSR slice must be: kurtosis 1.80,
crest 4.8 dB, against references at 2.23–2.64 and 8.1–11.3. *(It is not the
LFSR's output BIT, which would be kurtosis 1.00 and crest 0.00 dB — a square
wave of random sign. A 16-bit slice of the register is what both this voice and
the drum section use.)*

**But raw white is never what is heard.** The noise source is a mixer input and
the mixer feeds the ladder; a four-pole low-pass is a strong Gaussianiser.
Measured through it at res 0.7, drive 2.0: **crest 10.0–11.8 dB, kurtosis
2.54–2.92 — every value inside the span of the three references**, and at the
top of the cutoff range (8 kHz, 10.0 dB / 2.54) sitting between Mini V3's white
and Surge's.

So the cheap Gaussianiser — summing k independent slices, Irwin-Hall — was
measured and is **not worth building**: k = 2 buys 7.8 dB / 2.40 and k = 3
buys 9.4 / 2.60, for two or three times the LFSR work and an adder tree, to
reach a distribution the filter already delivers. The residual case is a patch
with the cutoff wide open and no resonance, where the raw uniform distribution
does reach the output; that is contract open item 18.

### N7. …and that costs 12 dB of headroom, which is ours to explain [ours]

Equal RMS between colours is a fact about the instrument; a hard digital rail is
a fact about this chip, and the two fight. The pink network's crest factor is
**4.6** — it needs 13 dB of peak headroom above its RMS — so equal-RMS colours
at white's natural level (uniform, RMS 0.577 of full scale) would put pink's
peaks at **2.6 × full scale** and clip 0.05 % of its samples even at half that.

`NOISE_SHIFT = 2` divides all three equally. It keeps them equal, keeps pink's
measured peak at **0.66** of full scale, and costs one shift. What it costs
musically is that white at mixer weight 1.0 sits **12 dB below a sawtooth at
mixer weight 1.0**, where the Minimoog-relevant balance measured on Mini V3 is
**−7.1 dB**.

**That 4.9 dB is a patch value, not a hardware limit.** `WN` is a Q0.15
register that reaches 2.0, and the balance is monotonic in it: measured,
weight 1.0 gives −12.0 dB, **weight 1.76 gives −7.1 dB**, weight 2.0 gives
−6.0 dB. The reference balance is inside the register's range with an eighth
of it still spare, so the deviation costs a patch constant and not a
redesign. `voice_fx_render.py`'s `18-noise-at-the-reference-balance` is that
patch. The alternative — raising the common scale instead — would have put
pink's peaks past the rail, which is what N7 exists to prevent.

### N8. Noise goes through the ladder, like everything else [verified: SM 2.2.1 signal flow]

"Audio signals from the three VCO's, the noise…" are summed in the mixer ahead
of the filter. Ours is the mixer's fourth input, so a swept filter on noise —
wind, surf, breath, a filtered snare — works exactly as it does on the
instrument.

---

## 4. Oscillator 3 as a modulation source

### M1. It is the defining feature, and it is a switch out of the audio path [verified: SM 2.18]

"Oscillator three can be used as a tone oscillator or as a modulation
oscillator. A switch, SW2, **interrupts the keyboard, modulation, external, and
pitchbend voltage on oscillator three** and also increases the range of
oscillator three's tune control. This allows oscillator three to be used as a
wide range modulation oscillator."

Two separate things happen at that switch, and this chip splits them:

- *Oscillator 3 stops following the keyboard* — **[ours]** the host's job. The
  host simply stops writing oscillator 3's increment per note and writes an
  LO-range one instead. No hardware.
- *Oscillator 3 stops receiving modulation* — the chip's job, because the mod
  bus is inside the datapath. `MROUTE` bit 2 (`MR_OSC3`) is that half of SW2.

### M2. The LO range reaches 0.2–0.5 Hz [verified: SM 5.36]

"Set WAVEFORM selector switch to sawtooth, RANGE switch to LO, and
OSCILLATOR-3 FREQUENCY counterclockwise to minimum… Listen to the audible clicks
which should occur **between two to five seconds apart**." That is
**0.2 Hz to 0.5 Hz**. The same paragraph requires that "the high end of the LO
range **overlaps the low end of the 32′ range**".

**[ours]** No hardware is needed: the 24-bit increment register's smallest
non-zero step is `SR/2²⁴ = 0.00286 Hz` and its largest is 24 kHz, so LO and 32′
are the same register at different values, and they necessarily overlap. The
range switch is the host's Hz→increment conversion.

### M3. The frequency knob's range widens when oscillator 3 leaves the keyboard [verified: SM 2.3]

"Oscillator 3 has two additional inputs which are both driven by the
OSCILLATOR-3 FREQUENCY CONTROL providing **± a musical fifth** when the
OSCILLATOR-3 CONTROL is on… or providing **+3 octaves** of control when the
OSCILLATOR-3 CONTROL is off." **[ours]** Host-side; a knob range, not a circuit
this chip contains.

### M4. The modulation mix is a PAN between oscillator 3 and noise [verified: SM 2.4]

"There are two modulation signals available in the Minimoog; the output of
Oscillator 3 and noise… The Modulation Mix amplifier selects either or both,
sums them and routes them to the Modulation Amount Control in the Left-hand
controller." And, decisively: "**The wiper of R23 is connected to ground and,
therefore, when the MODULATION MIX potentiometer is rotated, it pans between the
two modulation signals.**"

A pan, not two independent levels: the two weights sum to a constant. `mmix` is
that pot, 0 = oscillator 3 alone, 32768 = noise alone, and `mod_pan` is a convex
combination — so the modulation bus cannot be driven past full scale by turning
the pot.

### M5. The source is oscillator 3's waveform selector output [verified: SM 2.4]

"The output of OSCILLATOR-3's WAVEFORM SELECTOR SWITCH, SW8, and the output of
the NOISE SELECTOR SWITCH, SW14, are fed thru R23 and R24 respectively…" — so
the modulation source is whichever of the six waveforms oscillator 3 is set to,
including the reverse sawtooth. §2.4 also names what those waveforms are for:
"Oscillator 3 produces periodic modulation utilizing triangle, sawtooth and
pulse waveforms."

**[ours]** We tap the *naive* waveform, before PolyBLEP. The modulation path is
a control voltage: it is never summed into the mixer and never heard, so
band-limiting it would only cost area. At LO-range rates the corrected and
naive waveforms differ on **only the single sample that lands on each
discontinuity** — at 0.2 Hz that is three samples in 96 000 — and are
bit-identical everywhere else. The acceptance suite measures that rather than
repeating the claim, because the first version of this paragraph said "the
window never opens", and the window does open, for one sample per wrap.

### M6. The wheel is the amount, and the two destinations are switched [verified: SM 5.19, 5.37]

The mod-mix amplifier's output "is fed through R57 to the **AMOUNT of
MODULATION** control in the Left-Hand Controller" **[verified: SM 2.4]**. From
there, two front-panel switches gate the two destinations: OSCILLATOR
MODULATION and FILTER MODULATION. `MROUTE` bits 0 and 1.

### M7. Pitch modulation at full wheel is 13 to 23 semitones of swing [verified: SM 5.37]

"Place OSCILLATOR-1 switch in ON position and set RANGE control for 2′ and
WAVEFORM control for TRIANGLE. **Turn on OSCILLATOR MODULATION switch and rotate
MOD control wheel fully up. The oscillator should change 13 to 23 semitones.**
Use keyboard to determine how many semitones it actually changes."

That is the factory acceptance window for the whole path, with a square-wave
oscillator 3 (the paragraph sets "OSCILLATOR-3 WAVEFORM control on low square
wave" for the oscillator-2 half of the same test). 18 semitones of total swing
is **±0.75 octave** of peak deviation, which is `MPD_REF_OCT`.

### M8. Filter modulation at full wheel is at least 2.45 octaves of swing [verified: SM 5.19]

"Turn on the FILTER MODULATION switch and set the MOD wheel fully up. Tune
OSCILLATOR-3 to produce the lowest frequency square wave. Adjust CUTOFF
FREQUENCY control for **440 Hz** when pitch is low. When pitch switches to high,
check to see that frequency is **a minimum of 2.4 kHz**."

log₂(2400/440) = **2.448 octaves of total swing**, i.e. at least ±1.224 octaves
of peak deviation. It is a *floor*, not a nominal; `MFD_REF_OCT = 1.30`
**[inferred]** leaves margin over it, and the acceptance suite asserts the
≥ 2.4 kHz that the manual states.

Note that the two depths differ: the filter is swept harder than the pitch from
the same wheel. That is a fact about the instrument, not a convenience, and it
is why there are two depth registers and not one.

### M9. The modulation value is registered, one frame old [ours]

Oscillator 3 is the modulation *source*, and with OSC-3 CONTROL on it is also a
modulation *destination* — the Model D wires the mod bus into all three
oscillators' CV summers and only SW2 takes oscillator 3 off it. That is a
feedback path. This chip breaks it with a register: the modulation value is
computed at the end of a frame and read at the start of the next, **20.8 µs** of
delay, four orders of magnitude below the fastest modulation rate the
instrument reaches. Both the model and the RTL do this, identically, so it is a
specification and not an artefact.

---

## 5. The range switch

**[verified: SM Specifications]** "OSCILLATOR FREQUENCY: 0.1 to 20 kHz in six
overlapping ranges" — LO, 32′, 16′, 8′, 4′, 2′.

**[ours]** Already covered, and it needs no hardware: `VoiceFx.note_incs`
converts a note and a per-oscillator detune in semitones to an increment, and
a range is a detune of 0, ±12, ±24 semitones. The clamp that contract 5.5
records fires only above 48 kHz. §M2 covers LO. **What the chip does not have is
a range *register*** — the host computes the increment. That is a deliberate
split and the reason there is no "range" anywhere in `voice_fx.py`.

---

## 6. A constant that was wrong on the way into this repository

DR 0011 adds Huovilainen's tuning polynomial to the cutoff ROM. The polynomial
is

```
fcr = 1.8730·fc³ + 0.4955·fc² − 0.6490·fc + 0.9988
```

**[verified: SST]**, where the implementation spells the quadratic constant
`m04955`. Both `docs/discrimination.md` §8.3 and `model/reference_rigs.py` print
it as **0.4995**.

The two differ by **0.03 percentage points** on the measurement that motivated
the change. **No measurement in this repository would ever have caught it.** It
was caught by fetching the source that was already cited. That is the whole
argument for this document: a number with a tag can be checked, and a number
without one gets copied.

---

## 7. What this chip does NOT have

Stated, because a list of what we added is not a claim of completeness.

- **The external input / microphone preamp**, the fifth mixer source
  **[verified: SM Specifications]**, with its overload lamp (§2.12). No audio
  input pin. A *digital* internal return (the chip's own output summed back
  into its own mixer) is a different, feasible feature — see
  `spec/decision-records/0016-external-feedback-loop.md`.
- **The A-440 reference oscillator** (§2.2.7) — a tuning aid.
- **The pitch wheel** as a separate control. The host writes increments.
- **The second VCA** with its external loudness control input (§5.24.1).
- **The decay switch**, which on the Model D makes the release equal to the
  decay rather than instant.
- **Per-unit oscillator drift and the temperature-compensated exponential
  converter** (§2.16). Ours are exact; a Model D's are not, and the beating of
  three slightly-drifting oscillators is part of the sound.
- **Any measurement of a real Model D.** Everything above is a schematic, a
  service procedure, or arithmetic on one.

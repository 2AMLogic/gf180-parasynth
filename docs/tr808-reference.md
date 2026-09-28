# The Roland TR-808 voice circuits — an engineering reference for a digital 808

**Purpose.** This is the circuit-level reference for implementing an 808 in the
fixed-point drum model and the modal resonator bank of this chip
(`rtl-sketch/modal_dp.v`, `model/modal_fixed.py`). It answers, per voice, *what
generates the tone, at what frequency and Q, with what envelope, through what
filter*, and it ends each voice with a "what to implement" paragraph in DSP
terms. Breadth was traded for accuracy: every number is tagged.

**How claims are tagged.**

- **[verified: KEY]** — read directly in the cited primary source (the service
  notes' schematic or text, or a peer-reviewed analysis of it). Source keys are
  expanded in §0.
- **[inferred]** — computed by me from verified component values with the
  formulas given in §1, or read by me off the schematic scan where the wiring
  is unambiguous. Reproducible; not independently measured.
- **[measured]** — read off recordings of a real TR-808, with the instrument
  that read them committed and validated. This tag **outranks [inferred]**
  where the two disagree, and §4's pitch drop is the reason it exists: a
  magnitude tagged *[inferred]* here was shipped as ×1.7 and measures ×1.06.
- **[could not establish]** — no primary source found; do not build on it.

Where a number below disagrees with folklore, the folklore is wrong or refers
to a different unit; see §13 and §15.

---

## 0. Sources actually consulted

| key | source | what it gives | reached |
|---|---|---|---|
| **SN** | Roland, *TR-808 Service Notes*, 1st ed., 15 June 1981 (3rd printing June 1983). Scan: <https://archive.org/details/synthmanual-roland-tr-808-service-notes> (PDF: <https://archive.org/download/synthmanual-roland-tr-808-service-notes/rolandtr-808servicenotes.pdf>) | p.5–6 "Sound Generators" circuit description of every voice; p.9 main-board voice schematic (BD, SD, toms/congas, RS/CL, CP/MA) with component values; p.13 voicing-board schematic (six oscillators, CB, CY, OH, CH); p.14 "typical and variable" tuning chart (frequency, decay time and output amplitude per voice); p.15 design changes | yes, full PDF, read as images |
| **W14a** | K. J. Werner, J. S. Abel, J. O. Smith, "A Physically-Informed, Circuit-Bendable, Digital Model of the Roland TR-808 Bass Drum Circuit", DAFx-14. <https://www.dafx.de/paper-archive/2014/dafx14_kurt_james_werner_a_physically_informed,_ci.pdf> | block-by-block BD analysis with transfer functions, the decay/attack/sigh mechanisms, comparison against SPICE and recordings | yes, full text |
| **W14b** | Werner, Abel, Smith, "The TR-808 Cymbal: a Physically-Informed, Circuit-Bendable, Digital Model", ICMC\|SMC 2014. Zenodo: <https://zenodo.org/record/850891> (DOI 10.5281/zenodo.850890; PDF <https://zenodo.org/api/records/850891/files/smc_2014_220.pdf/content>) | the six Schmitt oscillators and their nominal frequencies, the two band-pass filters, envelope generators, swing VCAs, high-pass filters, tone stage | yes, full text |
| **W14c** | Werner, Abel, Smith, "More Cowbell: a Physically-Informed, Circuit-Bendable, Digital Model of the TR-808 Cowbell", AES 137th Conv., 2014. Abstract: <https://pure.qub.ac.uk/en/publications/more-cowbell-a-physically-informed-circuit-bendable-digital-model>; paper: AES e-lib 17530 (paywalled) | cowbell band-pass analysis | abstract only |
| **W16** | K. J. Werner, *Virtual Analog Modeling of Audio Circuitry Using Wave Digital Filters*, Ph.D. dissertation, Stanford, 2016. <https://stacks.stanford.edu/file/druid:jy057cz8322/KurtJamesWernerDissertation-augmented.pdf> | Table 2.2 (bridged-T R/C values for BD, SD, toms, congas, RS, CL), Table 2.3/4.2 (BD feedback values), Appendix C (tom/conga diode branch) | yes, full text |
| **SOS-BD** | G. Reid, "Practical Bass Drum Synthesis", *Sound On Sound* Synth Secrets. <https://www.soundonsound.com/techniques/practical-bass-drum-synthesis> | qualitative BD description, 808 vs 909 | yes |
| **SOS-SD** | G. Reid, "Practical Snare Drum Synthesis". <https://www.soundonsound.com/techniques/practical-snare-drum-synthesis> | qualitative SD block diagram | yes |
| **SOS-CY** | G. Reid, "Practical Cymbal Synthesis". <https://www.soundonsound.com/techniques/practical-cymbal-synthesis> | six square oscillators, three bands | yes |
| **SOS-CB** | G. Reid, "Synthesizing Cowbells & Claves". <https://www.soundonsound.com/techniques/synthesizing-cowbells-claves> | cowbell/claves description | yes |
| **RW** | R. Whittle, "Modifications for the Roland TR-808". <http://www.firstpr.com.au/rwi/tr-808/> | practitioner statements: per-unit oscillator tuning, noise source, BD self-oscillation mod, snare/clap composition | yes |
| **MI** | Mutable Instruments `eurorack` sources: `peaks/drums/{bass_drum,snare_drum,high_hat}.cc`, `plaits/dsp/drums/{analog_bass_drum,analog_snare_drum,hi_hat}.h`. <https://github.com/pichenettes/eurorack> | a widely-copied open 808 model, for comparison | yes, source read |
| **WP** | Wikipedia, "Roland TR-808". <https://en.wikipedia.org/wiki/Roland_TR-808> | provenance only | yes |
| **MD** | M. Delp, "Anatomy of a Drum Machine". <http://mickeydelp.com/blog/anatomy-of-a-drum-machine> | qualitative only | yes |

Not reached in this pass (state this rather than guess): Eric Archer's 808 pages
(site returns 404/certificate errors), Werner's companion audio pages
(404), the AES cowbell paper body, the Nava/Yocto/x0x clone documentation, and
the Csound/Pd/SuperCollider/VCV implementations. No web search was available
for this pass; everything above was fetched by direct URL.

All frequencies/Q/τ marked [inferred] were computed with the formulas of §1
from the component values in SN and W16. τ is the amplitude 1/e time.

---

## 1. Things common to every voice

### 1.1 Trigger and accent

The CPU (µPD650C) produces a **common trigger** whose ON voltage is set by the
global ACCENT level, **4–14 V**, and per-instrument data that is ANDed with it
to give each voice a **1 ms pulse** of that amplitude **[verified: W14a §3;
SN p.5 Fig. 7 and p.14 "Common Trig with pulse width longer or shorter than
1 ms will be a cause of deteriorative voices"]**. Accent therefore scales the
*excitation* of every voice, not a VCA after it; and because several voices
have nonlinear VCAs downstream, accent changes timbre as well as level
**[verified: W14b §5]**. Roland's chart gives the resulting output swing per
voice at "normal" and "accent" (§1.6): ≈3× for the drums, only ≈2× for
CY/OH/CH, 1.7× for MA **[verified: SN p.14]**.

### 1.2 The bridged-T resonator (the tone generator of BD, SD, toms, congas, RS, CL)

Roland's own text: "The bridged T-network filter shown in Fig. 11 is used to
generate periodic damping drum sound. This configuration has variations
according to application… With this circuit, the decay time becomes longer as
Q increases" **[verified: SN p.5]**. Werner: "The 808 represented Roland's
first implementation of the bridged-T network, and it was used in every single
one of its sound generators in some form… The TR-808 snare drum, lo/mid/hi
tom/congas, and rim shot/clave all use bridged-T networks in similar ways to
the bass drum, to create decaying pseudo-sinusoids in response to impulsive
input. Bridged-T networks are also used as band pass filters in the remaining
voices: handclap, cowbell, cymbal, and open/closed hihat" **[verified: W14a
§5, fn. 15]**.

Topology **[verified: W14a §5–7; W16 Fig. 2.13, 2.27]**: an op-amp with the T
between its inverting input and output — two capacitor "arms" C1 (input side)
and C2 (output side) in series, bridged by R2, with the arms' junction ("foot",
Werner's V_comm) tied to ground through R1. The trigger pulse enters the
non-inverting input. In BD, toms/congas and RS/CL an extra inverting amplifier
feeds the output back into the foot through a resistor R_fb (BD: R170; toms:
R230/R259/R289; CL: R313) — Roland calls this "multi-feedback".

Transfer function from the pulse input (Werner's eq. 5, denominator):

    D(s) = s² R1 R2 C1 C2 + s R1 (C1 + C2) + 1
    f0 = 1 / (2π √(R1 R2 C1 C2))                        (Roland gives this formula, W14a fn. 16)
    Q  = √(R2/R1) · √(C1 C2) / (C1 + C2)   (= ½ √(R2/R1) for C1 = C2)
    τ  = Q / (π f0)                                       (amplitude 1/e)

R1 is the *total* resistance seen from the foot to ground (all shunt paths in
parallel) **[verified: W14a §5, "R_effective = R161 ‖ (R165+R166) ‖ R170"]**.

**With feedback** of (inverting) gain −g into the foot through R_fb, I derived
by nodal analysis the closed-loop denominator (it reproduces Werner's eq. 5
and eq. 7 as special cases) **[inferred]**:

    D_fb(s) = s² R1 R2 C1 C2 + s R1 [ (C1 + C2) − g · R2 C1 / R_fb ] + 1

so **feedback subtracts from the damping term and leaves f0 unchanged to first
order**; Q is multiplied by (C1+C2) / ((C1+C2) − g R2 C1/R_fb), and the network
self-oscillates when g R2 C1 / R_fb ≥ C1 + C2. This is exactly "decay raises
feedback toward self-oscillation". Checked against Roland's chart in §2.

Two real frequency effects exist on top of this, and neither is a "pitch
sweep oscillator": (a) BD only — for the first ≈4–6 ms the foot resistor is
shorted by a transistor, raising f0 by ≈2.6× (§2); (b) toms/congas — a diode
pair in the foot branch conducts at large amplitude, so the pitch starts high
and falls to the small-signal value as the ring decays (§4).

### 1.3 The "swing-type VCA"

Roland's name for a single-transistor common-emitter stage whose collector
supply *is* the envelope voltage, with a diode that shuts it fully off: "The
swing type VCA shown in Fig. 12 is used to generate metallic sound (noise).
This circuit features its output waveform having many high harmonic
components to provide ringing metallic sound" **[verified: SN p.5]**. Werner
analyses it as a modified common-emitter amplifier that clips "wildly" between
the envelope voltage and a lower edge that is itself a function of the
envelope, and fits it with a soft-clip of sharpness α = 3.5 **[verified: W14b
§8]**. It is used by RS (Q62), CB (Q14/Q15), CY (Q16–Q18), OH (Q27) and CH (Q30);
MA's gate (Q65) and the clap's tail VCA (Q70) are similar single-transistor
stages, while the clap's burst VCA is an OTA (IC22, BA662). Its practical
signature is asymmetric clipping whose amount tracks the envelope — i.e. the
hats and cymbal get *more* distorted, not just quieter, as they decay. MI's
Peaks approximates it as "amplifies only the positive section of the signal"
and Plaits as `s *= s > 0 ? 4 : 0.1; s = s/(1+|s|)` **[verified: MI]**.

### 1.4 The noise source

One generator on the main board: a reverse-biased transistor base–emitter
junction in avalanche (Q35, 2SC828-R, with trimmer TM4 setting the white-noise
level to 130 mV rms), buffered by IC24, distributed as a **W.N. (white)** bus
and a **P.N. (pink)** bus **[verified: SN p.8 schematic "NOISE GENERATOR",
p.14 adjustment table "NOISE GENERATOR TM-4 130 mV rms"; RW: "real noise from
a reverse-biased transistor junction going into avalanche breakdown"]**.
Whittle adds that there is "no significant difference in the audible quality
of the noise between one TR-808 and another" **[verified: RW]**. Wikipedia's
story that Kakehashi bought "faulty transistors" for the sizzle is provenance
only **[verified: WP]**.

SD (white, via its own VCA), CP (white, band-passed), MA (white, gated), and
LT/MT/HT (pink, low-passed) consume it. **CY, OH and CH do not use noise at
all** (§1.5).

### 1.5 The six Schmitt-trigger square-wave oscillators (CB, CY, OH, CH)

"The TR-808's Cowbell, Cymbal, Open Hihat, and Closed Hihat voice circuits all
work by filtering and enveloping rectangular waves. In fact, they all share a
common bank of six of these oscillators, ingeniously implemented with a single
HD14584 hex Schmitt trigger inverter chip" **[verified: W14b §3; SN p.6 "The
combined square wave outputs of six Schmitt triggers including two for CB
generator"; SN p.13 schematic, IC1 HD14584]**. Each is a one-resistor
one-capacitor astable; the chip is run from **5 V** (Q9/R60/R61 regulator),
giving amplitude 5 V and duty cycle 47.98 % **[verified: W14b §3]**.

Component values read from the p.13 schematic **[verified: SN]** and the
nominal frequencies Werner derives from them with the HD14584's typical
thresholds **[verified: W14b §3]**:

| osc | R | C | nominal f (W14b) | note |
|---|---|---|---|---|
| 1 (pins 5/6) | R40 560 kΩ | C1 0.018 µF | **205.3 Hz** | fixed |
| 2 (pins 3/4) | R41 560 kΩ | C2 0.01 µF | **369.6 Hz** | fixed |
| 3 (pins 1/2) | R42 680 kΩ | C3 0.01 µF | **304.4 Hz** | fixed |
| 4 (pins 9/8) | R43 220 kΩ | C4 0.018 µF | **522.7 Hz** | fixed |
| 5 (pins 11/10) | TM2 220 kΩ + R45 100 kΩ | C5 0.018 µF | 359.4–1149.9 Hz, **trimmed to 800 Hz** | "COWBELL FREQ.2 1.25 ms" |
| 6 (pins 13/12) | TM1 220 kΩ + R44 150 kΩ | C6 0.022 µF | 254.3–627.2 Hz, **trimmed to 540 Hz** | "COWBELL FREQ.1 1.85 ms" |

Werner's numbers correspond to f = 1/(k·RC) with k = 0.483, i.e. HD14584
thresholds of about V_T+ ≈ 2.7 V, V_T− ≈ 2.1 V at 5 V **[inferred; I
back-computed k from his 205.3 Hz and it reproduces all six of his figures to
0.1 Hz]**. The hysteresis of a 4584/40106 varies substantially between parts
(Roland's own design-change list blames "variations in HD14584 hysteresis" for
the tempo clock drifting out of range **[verified: SN p.15 item 1]**), so the
four untrimmed frequencies are nominal ±(tens of) percent, and "each machine's
six square wave oscillators [have] a unique set of frequencies" **[verified:
RW; W14b fn. 7]**. Only 540 and 800 Hz are factory-trimmed. Early units
(before serial 000300) used R44 = 390 kΩ and R45 = 330 kΩ **[verified: SN
p.15; W14b fn. 9]**.

The six outputs are summed passively through 120 kΩ each (R35, R37, R39, R46,
R48, R50) into R53 = 1 kΩ to ground **[verified: SN p.13; W14b eq. 4]** — a
sum of six 0/5 V squares, heavily attenuated, i.e. a 7-level staircase. This
sum feeds the two cymbal band-pass filters (§10) and, through separate gates,
the cowbell (§9).

### 1.6 Roland's tuning chart (the only manufacturer-published measurements)

Service notes p.14, "CHECKING VOICES … values are typical and variable",
measured at the voice outputs with level at maximum, accent at minimum then
maximum, all other knobs at 12 o'clock. Reproduced verbatim (period in ms,
frequency in Hz) **[verified: SN p.14]**:

| voice | normal Vpp | accent Vpp | f (low knob) | f (mid) | f (high knob) | decay short | decay mid | decay long |
|---|---|---|---|---|---|---|---|---|
| BD | 3.5 | 10 | — | 18 ms (56) | — | 50 ms | 300 ms | 800 ms |
| SD (H / L) | 3 | 10 | — | 2.1 ms (476) / 4.2 ms (238) | — | — | 60 ms | — |
| LC | 3.5 | 12 | 6.1 (165) | 5.4 (185) | 4.5 (220) | — | 180 ms | — |
| LT | 3.5 | 12 | 12.5 (80) | 11.1 (90) | 10 (100) | — | 200 ms | — |
| MC | 3 | 10 | 4 (250) | 3.6 (280) | 3.2 (310) | — | 100 ms | — |
| MT | 3 | 11 | 8.3 (120) | 7.4 (135) | 6.3 (160) | — | 130 ms | — |
| HC | 3.5 | 12 | 2.7 (370) | 2.5 (400) | 2.2 (455) | — | 80 ms | — |
| HT | 3.5 | 12 | 6.1 (165) | 5.4 (185) | 4.5 (220) | — | 100 ms | — |
| C (claves) | 2.5 | 8 | — | 0.4 (2500) | — | — | 25 ms | — |
| RS (H / L) | 3 | 10 | — | 0.6 (1667) / 2.2 (455) | — | — | 10 ms | — |
| M (maracas) | 3 | 5 | — | — | — | 25 ms | — | 35 ms |
| CP | 6 | 2 (sic) | — | — | — | — | 100 ms | — |
| CB (H / L) | 3.5 | 12 | — | 1.25 (800) / 1.85 (540) | — | — | 50 ms | — |
| CY | 3.5 | 7 | — | — | — | 350 ms | 800 ms | 1200 ms |
| OH | 3.5 | 7 | — | — | — | 90 ms | 450 ms | 600 ms |
| CH | 3 | 6 | — | — | — | — | 50 ms | — |

"Decay time" is not defined in the chart. Comparing it with τ computed from
the component values for the voices where the analysis is unambiguous (SD low
60 ms vs τ = 22 ms; LT 200 vs 92; LC 180 vs 91; CP tail 100 vs 47; BD mid 300
vs 144) gives a consistent ratio of **≈2.2–2.7 τ, i.e. the chart's "decay" is
about the time to −20 dB** **[inferred]**. Use τ ≈ chart/2.3 when only the
chart value is available.

### 1.7 Tolerances

"The voice circuits featured ±20 % capacitors and ±5 % resistors. These
variations have significant effects on the gain, center frequency, Q, decay
time, &c. of filter sections, especially when they are in feedback
configurations" **[verified: W14a §11]**. Every f0 below is therefore a ±10 %
nominal, and every high-Q figure a ±50 % nominal. Make all of them
programmable; do not hard-wire.

---

## 2. BD — bass drum

**Topology [verified: W14a §2–9; SN p.5–6; SN p.9 schematic].** Trigger
logic (Q39, Q40) → **pulse shaper** (C40 0.015 µF, R163 100 kΩ, R162 4.7 kΩ,
D53) → **bridged-T resonator** around IC12a (arms C41 = C42 = 0.015 µF,
bridge R167 = 1 MΩ, foot R166 6.8 kΩ + R165 47 kΩ to ground, with Q43 across
R165) with a **feedback buffer** IC12b (R164 47 kΩ in, R169 47 kΩ ‖ (VR6
500 kΩ "DECAY" + C43 33 µF) feedback) returning through R170 470 kΩ into the
foot; an **envelope generator** (Q41, Q42, C38 0.1 µF, R156 1 MΩ) that drives
Q43 and also produces a **retrigger pulse** through C39 0.033 µF / R161 1 MΩ /
D52 into the foot; then **tone** (R171 220 Ω + R172 10 kΩ ‖ VR5 10 kΩ, into
C45 0.1 µF — a passive 1-pole low-pass), **level** VR4, and an output buffer
Q44 with a DC-blocking high-pass (C49 0.47 µF, R176 100 kΩ, R177 82 kΩ).
Component values: W16 Tables 2.3 and 4.2 and the p.9 schematic agree.

**Pulse shaper [verified: W14a §4].** A passive low-shelf plus a diode. The
rising edge delivers the full trigger amplitude V_TRIG (4–14 V); the falling
edge, 1 ms later, is ≈ V_TRIG·R162/(R162+R163) + 0.71 V ≈ 0.045·V_TRIG + 0.7 V
and is clamped by D53 so the output never goes below ≈ −0.7 V. "It is the
edges of the shaped pulse that will kick the bridged-T network into
oscillation."

**Resonator [verified: W14a §5; computed with §1.2, inferred].**
R1 = R161 ‖ (R165+R166) ‖ R170 = 46.1 kΩ, R2 = 1 MΩ, C = 0.015 µF:
**f0 = 49.4 Hz, open-loop Q = 2.33 (τ = 15 ms)**. Werner reports "≈49.5 Hz,
which is close to the entry in Roland's 'typical and variable' tuning chart
(56 Hz)"; the service-notes text speaks of "16 ms … inherent oscillation
period" (62.5 Hz); Werner's SPICE/model plots of instantaneous frequency sit
between 48 and 58 Hz over the first 300 ms **[verified: W14a Fig. 11]**. Treat
**50–56 Hz** as the target and make it tunable.

**Decay control [verified: W14a §6; law inferred, §1.2].** The feedback
buffer's audio-band gain is g = (R169 ‖ k·VR6)/R164 for knob position k
(0 at k = 0; 0.914 at k = 1; C43 only blocks DC). Closed-loop Q from the §1.2
law with R_fb = R170:

| VR6 position k | g | Q | τ | 2.3 τ | Roland chart |
|---|---|---|---|---|---|
| 0 | 0 | 2.3 | 15 ms | 34 ms | — |
| 0.1 | 0.515 | 5.2 | 33 ms | 76 ms | "short" 50 ms |
| 0.5 | 0.842 | 22.3 | 144 ms | 330 ms | "mid" 300 ms |
| 0.9 | 0.905 | 63 | 408 ms | 0.94 s | "long" 800 ms |
| 1.0 | 0.914 | 84 | 544 ms | 1.25 s | |

Self-oscillation needs g = 0.94; the stock circuit tops out at 0.914, so the
stock BD **cannot quite sustain**, which is why Whittle's modification
"extends the Bass Drum decay range to include self-oscillation, so any decay
time from the normal minimum to infinity can be achieved" **[verified: RW]**.
The frequency is unchanged by the decay knob to first order. **Hypothesis 1
for the BD is confirmed.**

**Attack frequency shift [verified: W14a §8.1; SN p.6].** For the duration of
the envelope generator's pulse the collector of Q43 shorts R165, so the foot
resistance drops from 53.8 kΩ to 6.8 kΩ (R1 → 6.66 kΩ) and **f0 rises to
≈130 Hz with Q ≈ 6** for ≈4 ms (SN: "the ON period of Q43 is determined by
R156 and C38 and equals 4 ms which is ½ × ½ of 16 ms") — Werner measures ≈6 ms.
"Although this brief change of center frequency (≈6 ms, less than a single
period at the higher frequency) isn't long enough to be perceived as a pitch
shift, it greatly affects the sound of the bass drum's attack, making it
'punchier' and 'crisper'." When Q43 releases, C39/R161/D52 apply a
**retriggering pulse** so the amplitude does not collapse when the resonance
jumps back down **[verified: W14a §8.1; SN p.6]**.

**Pitch "sigh" [verified: W14a §8.2].** Separate from the attack shift: when
the foot node swings below ≈ −0.7 V, Q43's base–emitter conducts, drawing
current that lowers R_effective and raises f0 slightly; as the ring decays the
frequency relaxes back — a downward glide of a few percent over the first few
hundred ms ("goes slightly flat at long decays" **[verified: SOS-BD]**).
Werner fits it as a memoryless nonlinearity i_C = f(V_comm) (his eq. 8,
α = 14.315, V0 = 0.556, m = 1.4765·10⁻⁵).

**Tone [verified: W14a §9; values SN p.9; corner inferred].** One-pole RC
low-pass, R_eq = R171 + (R172 ‖ VR5·l), C45 = 0.1 µF: **corner from ≈7.2 kHz
(VR5 = 0, all click) down to ≈305 Hz (VR5 max)**. It acts on resonator *and*
click alike; the click *is* the shaped pulse leaking straight through (Plaits
models this as `exciter_leak = 0.08·(tone+0.25)` **[verified: MI]**).

**Retrigger while ringing [verified: W14a §11].** Because the resonator is a
filter with state, "each note is slightly different, as in a real 808, since
the remaining filter states may interfere constructively or destructively with
the response to a new trigger".

**What to implement (BD).** *Amended 2026-09-18 (DR 0009,
`drum-verification.md` §8.3): **f0 = 49.4 Hz**, not 56. The 56 Hz below was
Roland's chart's, and it contradicts this section's own derivation from the
component values four paragraphs above. It is also incompatible with the decay
table above: that table's Q and τ columns satisfy τ = Q/(π f0) to 1.5 % at
49.4 Hz and only to 12.8 % at 56, so the table is computed at 49.4. Two sample
sets measure 48.8–51.6 Hz.*

One two-pole resonator (a modal-bank mode) at
~~f0 = 56 Hz~~ **f0 = 49.4 Hz** (tunable 45–65 Hz), excited by a **1 ms rectangular pulse** of
amplitude ∝ accent. (The analog pulse shaper turns that pulse into a
positive kick of A at t = 0 and a negative kick of ≈0.05A + 0.7 V at t = 1 ms,
clamped at −0.7 V: a 1-pole high-pass with τ ≈ 0.1 ms and a one-sided clamp
reproduces it; feeding the raw 1 ms pulse into the bank is close enough to
start with.) Set the pole radius set from the decay knob by the table above
(Q = 2.3 → 84, τ = 15 → 540 ms; r = exp(−π f0/(Q f_s))). Optional but cheap
and audible: for the first 4 ms load the "attack" coefficients (f0 ≈ 130 Hz,
Q ≈ 6) and then switch to the normal ones while adding a second, smaller
negative kick (the retrigger). Optional: a pitch glide of −2…−5 % over 300 ms
that scales with amplitude (the sigh). Follow with a 1-pole low-pass whose
corner is the tone knob (305 Hz … 7.2 kHz) applied to resonator + a copy of the
excitation pulse (the click). Do **not** reset the resonator state on a new
trigger. The output high-pass is at 3.4 Hz — ignore it. Q2.24 coefficients at
48 kHz: see §14.

---

## 3. SD — snare drum

**Topology [verified: SN p.6 "This sound generator has two bridged
T-networks for fundamental waveforms and harmonic waveforms. The output ratio
of the two can be changed by VR8 (TONE) to tailor sound characteristic. The
amplitude of snappy envelope can be controlled by VR9 (SNAPPY)"; SN p.9
schematic; W16 Table 2.2; RW: "composed of the Snappy noise pulse and the
ringing of two 'Bridged T-Network' resonators, with the Tone pot controlling
the mix of the outputs of the two resonators"].**

Two bridged-T resonators around the two halves of IC14, with no feedback
buffer (the SD is not "multi-feedback"):

| | R1 (foot) | R2 (bridge) | C1, C2 | f0 [inferred] | Q [inferred] | τ | Roland chart |
|---|---|---|---|---|---|---|---|
| low, IC14a | R196 680 Ω | R197 820 kΩ | C58 = C59 = 0.027 µF | **250 Hz** | 17.4 | 22 ms | **238 Hz**, 60 ms |
| high, IC14b | R195 2.2 kΩ | R198 1 MΩ | C60 = C61 = 0.0068 µF | **499 Hz** | 10.7 | 6.8 ms | **476 Hz** |

So the June-1981 machine's snare is **≈240 Hz + ≈480 Hz, a 1 : 2 ratio**, with
the upper partial decaying 3× faster **[verified: SN chart; values SN/W16;
Q, τ inferred]**. The hypothesis "≈180 Hz and ≈330 Hz" is **refuted for the
1981 unit — but see the design change below, which lands exactly there.**

**Design change ("PORTION CHANGED" box on the p.9 schematic, 1983 printing)
[verified: SN p.9]:** "SD: C58 .027 to .056; C61 .0068 to .015; R191 8.2k to
10k; R200 47k to zero." No serial number or reason is given (the p.15
design-change table does not list it). With those values **[inferred]**:
low f0 = 1/(2π√(680·820k·0.056µ·0.027µ)) = **173 Hz** (Q 16, τ 30 ms);
high f0 = **336 Hz** (Q 10, τ 9.4 ms) — ratio 1.94. Later 808s therefore have
a snare a fourth lower than early ones. The tuning chart on p.14 was not
updated. Build both presets; default to the later one (it is the sound most
recordings and samples come from, but that is [inferred] from the chronology,
not measured).

**Excitation and the cascade [inferred from the schematic, SN p.9].** The
trigger (Q45/Q46) is shaped by R189 100 kΩ ‖ C57 0.0068 µF into R190 680 Ω and
enters IC14a's non-inverting input. IC14a's *output* is divided by R191
8.2 kΩ / R192 220 Ω (≈1/38) into IC14b's non-inverting input — the **high
resonator is driven by the low resonator's output, not by the pulse
directly**, so it is excited continuously by the fundamental and the two
partials are phase-locked. (Peaks and Plaits excite both with the pulse; the
difference is subtle.) IC13 sums IC14a (via C63 0.047 µF) and IC14b (via R200
47 kΩ → 0 Ω after the change, C65 0.047 µF) through VR8 TONE 100 kΩ(B), plus
the noise.

**Snappy / noise path [verified: SN p.6 text; wiring inferred from p.9].**
VR9 SNAPPY 10 kΩ(B) sets the amplitude of the trigger pulse fed to Q47, which
charges C51 0.47 µF through R186 33 kΩ (τ ≈ 15.5 ms) — the *noise envelope*.
That envelope controls Q48, a transistor VCA on the white-noise bus (W.N.
via C56 0.022 µF, R188 100 Ω). Q48's output goes through a Sallen-Key
high-pass on emitter follower Q49 (C66 = C67 = 0.0018 µF, R201 22 kΩ feedback,
R202 47 kΩ): **f0 ≈ 2.75 kHz, Q ≈ 0.7** **[inferred]**, then C69 0.01 µF /
R205 27 kΩ into the IC13 mixer. **"Snappy" is the noise level (via its
envelope amplitude); it does not change the resonators** — contrary to
SOS-SD's description, in which "the snappy signal is attenuated and added to
the trigger itself and directed to both oscillators"; the schematic shows VR9
only in the Q47 noise-envelope path **[inferred from SN p.9]**. The noise
envelope's decay is fixed (≈15 ms RC, so the "snap" is a 30–40 ms burst);
the tone resonators' decays are fixed by their component values.

**What to implement (SD).** Two modal-bank modes: low at 173 Hz (Q 16) and
high at 336 Hz (Q 10) [later units], or 238/476 Hz (Q 17/11) [early units].
Excite the low mode with the 1 ms pulse; excite the high mode from the low
mode's output ×1/38 (or, if the bank cannot chain, from the same pulse — the
loss is small). TONE = crossfade of the two mode amplitudes (Roland: "output
ratio of the two"). Noise: white LFSR × exponential envelope → 2-pole filter
at 2.75 kHz Q 0.7 → add. Total: 2 resonator modes + 1 noise biquad + 1
envelope.

> **AMENDED 2026-09-18 from hardware** (`docs/drum-verification.md` §8.1,
> §8.6; contract 17.22, 17.24, 17.25). Three numbers in this section are
> right and three readings of them are not:
>
> - the noise filter is a **band-pass** on the stated pole, not a high-pass.
>   The pole (2.75 kHz, Q 0.7) is kept exactly; only the numerator changes. A
>   high-pass on that pole is flat to Nyquist, and the machine's snare noise
>   peaks at 3–5 kHz and falls above (band-pass 1.9 dB weighted rms against
>   the high-pass's 5.2);
> - the noise envelope's decay is **τ ≈ 30 ms**, not the 15.5 ms of
>   R186 × C51 — which is the *charge* path. The machine's burst measures T20
>   63–78 ms over six files, and this section's own prose ("the snap is a
>   30–40 ms burst") agrees with the measurement rather than with the RC;
> - TONE's crossfade has a **measured value at 12 o'clock**: the upper partial
>   sits at **1.42×** the lower, the same with the snappy path up and down.
>   What is *not* solved is that ratio from R191/R192/VR8/R200; it is measured,
>   not derived.

---

## 4. LT / MT / HT and LC / MC / HC — toms and congas (three shared circuits)

**Topology [verified: SN p.6: "These three sound generators are composed of
the circuits based on the same principle… composed of a multi-feedback,
bridged T-network including IC5 as an active element. Voices are switched by
SW8 (C77 — frequency, R224 — level). While the oscillation is large in
amplitude immediately after triggering, it is on a higher frequency due to
conductions of D80 and D81, which reduce time constant of the filter. As the
resonance is damped, its frequency is lowered by the effect of increasing
diodes' internal resistance… Pink noise with a slightly longer decay time is
mixed for Low Tom Tom to provide artificial reverberation"; SN p.9 schematic;
W16 Table 2.2, Appendix C].** The tom and the conga of each pair are **the same
resonator with a capacitor switched in (tom) or out (conga)** and, for toms
only, a pink-noise path switched in. The panel switch selects one or the other;
they cannot sound together.

Values (SN p.9 / W16 Table 2.2) **[verified]**; frequencies **[inferred]** with
R1 = (1−x)·VR + R_side = 1.0–1.5 kΩ (VR11/13/15 = 500 Ω TUNING, R231/260/287 =
1 kΩ, diodes 1S188FM germanium *non-conducting* at small signal), R2 = 820 kΩ
(R228/R257/R284), feedback g = 1 through 820 kΩ (R230/R259/R289; IC15b/17b/18b
are unity-gain inverters, 33 kΩ/33 kΩ):

| voice | C1 (input arm) | C2 (output arm) | f0 at tuning min/mid/max | Roland chart | Q (with fb) | τ | chart decay |
|---|---|---|---|---|---|---|---|
| LT | C76 0.056 µF | C78 0.012 + C77 0.047 = 0.059 µF | 79 / 86 / 97 Hz | 80 / 90 / 100 | 25 | 92 ms | 200 ms |
| LC | C76 0.056 | C78 0.012 | 175 / 192 / 214 | 165 / 185 / 220 | 55 | 92 ms | 180 ms |
| MT | C89+C90 0.0352 | C91+C92 0.039 | 123 / 134 / 150 | 120 / 135 / 160 | 24 | 58 ms | 130 ms |
| MC | C90 0.027 | C92 0.012 | 252 / 276 / 309 | 250 / 280 / 310 | 38 | 44 ms | 100 ms |
| HT | C103 0.027 | C104+C105 0.0276 | 166 / 182 / 204 | 165 / 185 / 220 | 25 | 44 ms | 100 ms |
| HC | C103 0.027 | C105 0.0056 | 369 / 404 / 452 | 370 / 400 / 455 | 56 | 44 ms | 80 ms |

The agreement with Roland's chart is within 5 % across all six once the diodes
are treated as open **[inferred]**. (W16 Table 2.2 lists R1 = 333–500 Ω, which
is the diode-*conducting* limit; with those values every frequency comes out
1.7× too high against the chart — that is the large-signal **bound**, not the
pitch the machine reaches, see next.)

**Pitch drop [verified: SN text; magnitude MEASURED — and it is not ×1.7].**
With the diodes fully conducting the foot resistance becomes (1−x)·500 + 1 kΩ
‖ x·500 → 333–500 Ω, i.e. f0 would reach ≈1.7× the small-signal value
(LT ≈ 145 Hz, settling to 86 Hz) — but that is the resistance **limit**, what
the branch would do with the diodes held hard on, and a real hit does not get
there. The transition is amplitude-dependent and gradual (germanium diode,
soft knee), not a stepped envelope. The congas share the mechanism. This, not
any envelope, is the toms' characteristic "doom" sweep; it also means
**accent changes the pitch envelope**.

> **AMENDED 2026-09-26 from hardware** (measured in #110, shipped in #154;
> `docs/tom-pitch-drop-measurement.md`, `docs/tom-pitch-drop-correction.md`,
> contract 15.7.1). The *mechanism* above survives. The ≈1.7× did not: it was
> tagged *magnitude inferred* here, was carried into the contract as the
> shipped sweep, and is the largest single error the drum section has had.
> Onset f0 ÷ settled f0, 99 clean-digital tom files of a real TR-808, median
> over 11 TUNING positions × 3 voices:
>
> | accent | n | onset ÷ settled | range over the pot | τ |
> |---|--:|--:|---|--:|
> | no accent | 23 | **×1.063** | ×1.040 – ×1.094 | 13.0 ms |
> | accent | 33 | **×1.140** | ×1.085 – ×1.272 | 24.5 ms |
> | more accent | 33 | **×1.236** | ×1.169 – ×1.344 | 33.1 ms |
>
> **×1.7 occurs in none of the 99 files**; the largest drop anywhere is
> ×1.344. Two further readings of this paragraph were wrong independently of
> the magnitude, and both are now measured:
>
> - **an unaccented hit barely sweeps at all.** Germanium diodes do not
>   conduct below a drive, so the accent law has a **threshold** — accent
>   0.670 in the tom position, 1.064 in the conga position — where the
>   shipped law had a `min(max(accent,0),1)` **clamp** that handed a soft hit
>   the *full* sweep. Measured excesses at the three recorded accent levels
>   are 0.054 / 0.143 / 0.239: a straight line that does not pass through the
>   origin.
> - **the TUNING pot changes the drop**, which the shipped sequence ignored
>   entirely. LT at *More Accent* runs ×1.169 at 82 Hz and ×1.325 at 101 Hz;
>   d ln(excess)/d(f0/f0_nominal) is **3.58 ± 0.14** in the tom position and
>   **7.46 ± 0.09** in the conga position. HT and LC are both nominally
>   185 Hz on this same bridged-T with a capacitor switched (SW8) and their
>   unaccented excesses differ **11×**, so the dependence is on the **switch
>   position**, not on frequency.
>
> Unamended: the **shape** (the exponential beat a linear ramp in 88 of 89
> measured rows) and the ≈20 ms relaxation carried by the 60 ms window.
<!-- claim: test=model/test_tom_drop_law.py::test_the_reference_documents_carry_the_measured_magnitude -->

**Noise (toms only) [verified: SN text; values inferred from p.9].** The
P.N. bus is gated by Q52/Q55/Q58 with an envelope from a diode-charged RC
(D55, C72 0.039 µF, R218 2.2 MΩ / R216 15 kΩ — ≈85 ms decay), low-passed by
R222 22 kΩ / C75 0.018 µF (**≈400 Hz, 1-pole**) and mixed in only in the tom
position of the switch. SN mentions it for LT ("slightly longer decay time
… artificial reverberation"); the schematic has the same path on all three.
It is a quiet, dark rumble under the tone.

**What to implement (toms/congas).** One modal-bank mode per voice at the
chart frequency (LT 90, MT 135, HT 185 / LC 185, MC 280, HC 400 Hz; the TUNING
pot spans ±10 %), Q ≈ 25 (toms) / ≈ 40–55 (congas), excited by the 1 ms pulse
× accent. Add the **amplitude-dependent pitch offset**: ~~f = f0·(1 + 0.7·
sat(|y|/y_knee)) or, cheaper, a decaying pitch offset of +40 % → 0 over ≈ 2τ
scaled by accent~~ — both of those are the ≈1.7× bound above, and the
measurement says otherwise. Use the **measured** law (contract 15.7.1,
`drums_fx.tom_drop_excess`):

```
f(t) = f0 · (1 + excess · exp(−3t/60 ms))
excess = 0.060 · max(0, accent − A0)/(1 − A0_tom) · exp(G · (f0/f0_nominal − 1))
```

| constant | tom position | conga position |
|---|--:|--:|
| accent threshold `A0` | **0.670** | **1.064** |
| tuning slope `G` | **3.58** | **7.46** |
<!-- claim: test=model/test_tom_drop_law.py::test_the_reference_documents_carry_the_measured_magnitude -->

so that a tom at accent 1.0 with the pot centred sweeps **×1.06**, not ×1.7,
and an *unaccented conga* does not sweep at all. `f0_nominal` is the
**position's own centre** — this section's chart frequency — not a global one;
`u` is clamped to the pot's ±10 %, because that is the span the recordings
cover and the law must not extrapolate past its own evidence. The bank must
accept per-sample coefficient updates or a short coefficient ramp (a 4–8 step
ramp of a1 is enough; a2 changes little).
Toms only: pink noise (LFSR + 1-pole low-pass at ≈400 Hz) × envelope
(τ ≈ 85 ms) at low level. Congas: no noise.

---

## 5. RS — rimshot, and 6. CL — claves (one shared circuit, switch SW11)

**Topology [verified: SN p.6 "RS/CL"; SN p.9 schematic; W16 Table 2.2].**
Two bridged-T networks:

- **IC21 network** (R315 5.6 kΩ, R316 1 MΩ, C115 = C116 = 0.0047 µF):
  **f0 = 453 Hz, Q = 6.7, τ = 4.7 ms** **[inferred]** — Roland chart RS "L"
  = 455 Hz **[verified]**. Used by RS; in the CL position its output "routed
  via R320 can be ignored because of its minimized level" **[verified: SN]**.
- **IC20a network** (R312 1 kΩ, R308 820 kΩ, C117 = C119 = 0.0022 µF):
  **f0 = 2526 Hz, Q0 = 14** **[inferred]** — Roland chart C = 2500 Hz
  **[verified]**. In the **CL** position IC20b (unity inverter, R314/R309
  33 kΩ) feeds back through **R313 390 kΩ** into the foot ("wired for high Q"
  **[verified: SN]**); by the §1.2 law g·R2·C1/R_fb = 0.00463 µF exceeds
  C1+C2 = 0.0044 µF, i.e. the linear model puts the claves **at or past the
  self-oscillation boundary** (Q → ∞; at g = 0.9 Q ≈ 270) **[inferred;
  component tolerance decides on which side a given unit sits]**. The output
  is hard-gated (below), so this is harmless — and it is why the claves ring
  so purely.
- In the **RS** position R313 is disconnected ("makes IC20b just a buffer"
  **[verified: SN]**) and the switch connects **C118 0.0022 µF** across the
  output arm, in parallel with C117 → C2 = 0.0044 µF: **f0 = 1786 Hz, Q = 13.5,
  τ = 2.4 ms** **[inferred from the switch wiring on p.9]** — Roland chart RS
  "H" = 1667 Hz **[verified]** (7 % apart, within tolerance).

**Gating and envelope [verified: SN text; values inferred].** Both voices'
outputs pass IC19 only while JFET Q74 (2SK30A) is released by the trigger
pulse through Q61 (C112 0.022 µF, R305 1 MΩ: **≈22 ms window**); "this
switching is provided to eliminate noise leaking from IC20, especially for CL"
**[verified: SN]**. For RS additionally, IC20b's output (via R318 220 kΩ) and
IC21's (via R320 22 kΩ) are summed into the **swing VCA Q62** whose envelope is
R107 1 kΩ / C24 0.47 µF (**τ ≈ 0.5 ms** — essentially the trigger edge) —
"VCA of this type is intended to provide many high harmonics in the output
signals" **[verified: SN]**. Chart decays: CL 25 ms, RS 10 ms **[verified]**.

**What to implement (CL).** One modal-bank mode at 2500 Hz with very high Q
(≥ 100; or literally a sine oscillator) excited by the pulse, multiplied by
a **gate envelope of ≈22 ms** (rectangular with a ≈2 ms fall is enough; Roland
chart 25 ms). Level ≈ 0.7 of the drums (2.5 vs 3.5 Vpp).

**What to implement (RS).** Two modes: 455 Hz (Q 6.7) and 1700–1800 Hz
(Q 13), both excited by the pulse; sum → **swing-VCA nonlinearity** (asymmetric
soft clip, positive gain ≈ 4×, negative ≈ 0.1×) with an envelope of τ ≈ 1 ms
→ gate of ≈22 ms → optionally a 2-pole high-pass to remove the thump. The
distortion is the sound; do not skip it.

---

## 7. CP — handclap

**Topology [verified: SN p.6 "CP/MA" text and Fig. 13; SN p.9 schematic].**
"White noise passed through the band pass filter (IC21) is applied to two
VCAs in parallel to have different envelopes… Since an envelope with a
relatively long decay time is applied to the VCA Q70, output from this VCA
constitutes reverberation of CP sound. The output envelope at the VCA (IC22,
Q71 and Q72) is a unique sawtooth shape, and is a main component of this sound
generator." **Whittle:** "a series of close-spaced pulses of filtered and
distorted noise plus a softer pseudo-reverb exponential decay pulse of softly
filtered noise" **[verified: RW]**.

**Noise filter [values verified: SN p.9; response inferred].** IC21 is a
bridged-T band-pass (R333 10 kΩ foot, R334 100 kΩ bridge, C128 = C129 =
0.0047 µF, with C127 0.0018 µF ‖ R332 27 kΩ (22 kΩ in lots 1–5) in the feedback):
**f0 ≈ 1.07 kHz, Q ≈ 1.6**. This one filter feeds both VCAs.

**Burst envelope [verified: SN p.6 + Fig. 13; spacing inferred].** Quad
comparator IC23 (AN6912): the trigger is integrated by R350 1 MΩ / C140
0.0027 µF into a **30 ms** pulse (Fig. 13-2). Inside that window a relaxation
oscillator runs: Q73 charges C144 0.47 µF abruptly to −15 V; it discharges
through R365 82 kΩ (+ D71) (RC = 38.5 ms) until pin 5 crosses the reference at
pin 4, whereupon it is recharged, "and after this process is repeated and
advanced to the middle of the third time, pin 1 of IC23 rises to 0 V" and the
oscillator is stopped. So: **three sawtooth ramps inside 30 ms, i.e. a burst
period of ≈10–12 ms [inferred from "middle of the third" in 30 ms]**; the
comparator threshold that fixes it exactly is set by R354 5.6 kΩ / R355 2.7 kΩ
/ R356 1 MΩ and was not resolved. The sawtooth (fast rise, RC fall) is
converted **exponentially by Q72** (with Q71 carrying the "C.P. OFFSET" trimmer
TM3 and the accent through D68/C143/R362) into the control current of **IC22,
a BA662 OTA**, which is the burst VCA. Each burst is therefore an
exponential-decay envelope re-struck every ≈10 ms, three times, each starting a
little lower than the last (Fig. 13-4).

**Reverb tail [values verified; τ inferred].** Q69 charges C138 0.047 µF on the
trigger; it decays through R348 1 MΩ: **τ ≈ 47 ms** (chart "100 ms" ≈ 2.1 τ).
It drives the second VCA Q70 (a transistor VCA, not the OTA), whose output
is summed with the bursts in IC19. Level trims: R346 1 kΩ → 680 Ω and R332
22 kΩ → 27 kΩ from serial 010600 because "CP sound overmatches the rest in
level" **[verified: SN p.15]**.

**What to implement (CP).** One white LFSR → **2-pole band-pass 1.07 kHz,
Q 1.6** → split: (a) burst VCA: envelope = three exponential decays (τ ≈ 4 ms
each is a reasonable start; the analog one is a sawtooth through an
exponential converter) restruck at t = 0, ≈10, ≈20 ms with levels 1.0, ≈0.8,
≈0.65, then off at ≈30 ms; (b) tail VCA: exponential τ ≈ 47 ms starting at
t = 0 at ≈ −10 dB relative to the burst peak (the offset trimmer and the
level resistors set this; no measured figure — [could not establish] the
exact ratio, tune by ear against a sample). Sum. Roland's normal/accent
amplitude entry for CP is garbled ("6 / 2"); the p.15 change implies CP was
too loud, so scale to the drums after the fact. The whole voice is two
envelopes, one biquad, one noise source: cheaper than any drum.

---

## 8. MA — maracas

**Topology [verified: SN p.6 "White noise is gated by Q65 and supplied to the
same buffer IC19 as for the CP sound generator through the filter Q68.
Envelope for MA sound generator is generated by Q66 and Q67"; values SN p.9;
filter response inferred].** W.N. → transistor gate Q65 (base via C130
0.047 µF / R335 6.8 kΩ, R336 1 MΩ) with an envelope from Q66/Q67 (R341 470 kΩ
/ C134 0.033 µF: ≈15 ms; C135 0.1 µF / R344 220 kΩ / R345 150 kΩ shape the
attack) → Sallen-Key high-pass on emitter follower Q68 (C132 = C133 =
0.001 µF, R339 3.3 kΩ feedback, R340 68 kΩ): **f0 ≈ 10.6 kHz, Q ≈ 2.3 (a
resonant peak of ≈ +7 dB)** → IC19 (shared with CP; switch SW12 selects CP or
MA). Chart: decay 25–35 ms, output 3 Vpp / 5 Vpp accent **[verified]**.

**What to implement (MA).** White LFSR × envelope (attack ≈ 1–2 ms, exponential
decay τ ≈ 12 ms; total ≈ 30 ms) → **2-pole high-pass 10.6 kHz, Q 2.3**. The
gate is a transistor, so a little asymmetric clipping is authentic but not
essential.

---

## 9. CB — cowbell

**Oscillators [verified: SN p.6 "This sound generator uses the outputs of two
square waveform oscillators with different frequencies (by Schmitt triggers)";
SN p.13: TM1 "COWBELL FREQ.1 1.85 ms", TM2 "COWBELL FREQ.2 1.25 ms"; SN p.14
chart CB H = 1.25 ms (800 Hz), L = 1.85 ms (540 Hz); W14b §3: "The last two
[oscillators #5–6], which form the basis of the Cowbell voice circuit, are
tunable via trimpots… for factory tuning to specific frequencies (800 and
540 Hz, respectively)"].** **Hypothesis 3 confirmed: the cowbell is
oscillators 5 and 6 of the hi-hat/cymbal bank, the two trimmed ones, at 800 Hz
and 540 Hz** (ratio 1.48). SOS-CB quotes "approximately 587 Hz and 845 Hz,
ratio 1 : 1.44" **[verified: SOS-CB]** — presumably measured on one unit, and
consistent with Roland's ±tolerance. Early units (serial < 000300) had
different R44/R45 and "difficulty in setting COW BELL sound frequency within
the specified range" **[verified: SN p.15]**.

**Gates and envelope [verified: SN p.6; values SN p.13].** Each oscillator has
its own transistor gate (Q15, Q14, "exclusive gate (VCA)"), driven by the
envelope on C9 0.47 µF; "A series of R82 [33 kΩ] and C34 [1 µF] connected in
parallel with C9 forms an envelope having abrupt level decay at the initial
trailing edge to emphasize attack effect" — a two-slope decay: a fast initial
drop then a slower tail (chart: 50 ms) **[verified: SN]**. SOS-CB describes
the same ("a high-amplitude, short-duration 'impact', followed by a more
extended tail") **[verified: SOS-CB]**.

**Filter [could not establish precisely].** The two gated squares are "mixed
by the filter IC2" **[verified: SN]**, a single op-amp with R26/R27 10 kΩ,
C30/C31 0.0022 µF, C29 0.0033 µF, C28 0.01 µF, R24 2.2 kΩ, R25 470 kΩ
**[values verified: SN p.13]**. Werner derived its transfer function with
Mason's gain formula in W14c (paywalled; abstract only). My reading of the
topology as a multiple-feedback band-pass with series input capacitors gives a
peak near **0.9 kHz, Q ≈ 4–5**, which would put 800 Hz near the peak and 540 Hz
≈15 dB down **[inferred, low confidence — the gates' source impedance and the
exact node wiring change the answer]**; SOS-CB says "band-pass filter centred
at 2.64 kHz with 12 dB/oct slope and resonance" **[verified: SOS-CB, but it is
unclear whether that describes the 808 or the author's patch]**. Treat the
centre frequency as a parameter to fit against a recording.

**What to implement (CB).** Two square oscillators at 540 and 800 Hz (phase
accumulators; they are free-running, never reset, and their phases are
uncorrelated) → each × swing-VCA gate × envelope (fast segment: exponential
τ ≈ 5 ms from 1.0 down to ≈0.5, then τ ≈ 30 ms; chart decay 50 ms) → sum →
2-pole band-pass, centre 0.9–2.6 kHz (parameter), Q ≈ 4–8. The swing-VCA
asymmetry matters: the gated square is not a symmetric square, and its DC
shift through the band-pass is part of the "clank".

---

## 10. CY — cymbal

**Topology [verified: W14b §2 overview; SN p.6 "CY"].** Trigger → attack
smoother (Q19, τ ≈ 0.1 ms) → three envelope generators → three swing VCAs →
three Sallen-Key high-passes → tone stage → level. The six-oscillator sum
(§1.5) is band-passed by **two** filters in IC3; the higher one is split to two
of the VCAs:

| band | source | band-pass (bridged-T type, IC3) [values SN p.13; f0/Q inferred] | VCA | envelope | high-pass |
|---|---|---|---|---|---|
| high, short | IC3 pin 7 | **7.1 kHz, Q ≈ 6** (R56 560 Ω, R57 82 kΩ, C13 = C14 = 0.0033 µF) | Q16 | fixed, short ("its decay time is short") | Hh3: 3rd-order Sallen-Key, op-amp. **2-pole 10.32 kHz Q 5.64 + 1-pole high-pass 5.20 kHz, pass band +8.86 dB** [measured off W14b Fig. 4, `tools/werner_fig4.py`, 0.008 dB rms] |
| high, variable | IC3 pin 7 | same 7.1 kHz | Q17 | **DECAY** VR2 2 MΩ ‖ R93 470 kΩ × C41 1 µF: RC up to ≈0.38 s [verified: W14b §7; value inferred] | Hh2: 2nd-order non-unity-gain Sallen-Key. **8.84 kHz, Q 1.00, pass band +6.03 dB (gain ×2)** — not resonant [measured off W14b Fig. 4, `tools/werner_fig4.py`, 0.007 dB rms] |
| low | IC3 pin 1 | **3.45 kHz, Q ≈ 6** (R58 560 Ω, R59 82 kΩ, C15 = C16 = 0.0068 µF) | Q18 | fixed, medium | Hh1: 2nd-order Sallen-Key on emitter follower Q25, C48 = C59 = 0.0015 µF, R124 22 kΩ, R127 82 kΩ: **2.5 kHz, Q 0.97** [inferred from W14b eq. 16 + values] |

Werner states the band-pass centres as "around 3440 Hz" and "around 7100 Hz"
**[verified: W14b §4]**, matching the bridged-T formula on the schematic
values. "These band pass filters strongly accentuate the upper overtones of
the square waves, while de-emphasizing their fundamental frequencies"
**[verified: W14b]** — which is why the result does not sound like a chord.
"Each time one of the six rectangular wave oscillators flips state, the edge
kicks some more AC energy into each band pass filter" **[verified: W14b §4]**
— the cymbal is really *ring-down of two resonators struck by an aperiodic
edge train*, plus VCA distortion.

**Controls [verified: SN p.6; W14b §7, §10; VR4 value SN p.13].** DECAY
changes only the middle band's RC (chart: 350/800/1200 ms overall). TONE
(VR4 20 kΩ(B), linear taper) is a passive network that mainly attenuates the
third (highest) band but also shifts the others ("weakly-separated,
non-orthogonal controls… like guitar amplifier tone stacks") — see below for
the network itself. LEVEL's buffer "also acts as a differentiator in the audio
band — a 6 dB/octave rising slope" **[verified: W14b §11]**. That slope has a
corner: W14b Fig. 10's family is a **single-pole differentiator with its corner
at 18.97 kHz** (0.02 dB rms over 21 Hz–19 kHz), which tilts **+16.6 dB across
2–20 kHz**, not the +20 dB of an ideal 6 dB/octave. A discrete `(1 - z^-1)` at
48 kHz tilts +17.4 dB over the same span. **[measured off W14b Fig. 10,
`tools/werner_fig4.py`]**

**The tone stage [measured off W14b Fig. 9, `tools/werner_fig9.py`].** W14b §10
gives three *fifth-order* transfer functions, Ht1 = Vtone/Vh1, Ht2 = Vtone/Vh2,
Ht3 = Vtone/Vh3, and declines to print their coefficients. Fig. 9 plots all
three families for k ∈ [0.01, 1.0]. At **k = 1.0** (TONE fully open) each is a
**2-pole band-pass with two real poles** — an RC high-pass cascaded with an RC
low-pass, which is what a passive network builds:

| | f0 | Q | real poles | peak | plotted over |
|---|---|---|---|---|---|
| Ht1 (low band → out) | 274 Hz | 0.38 | 128 / 590 Hz | −26.44 dB | 121–564 Hz only |
| Ht2 (DECAY band → out) | 972 Hz | 0.45 | 610 / 1549 Hz | −15.12 dB | 562–1640 Hz only |
| Ht3 (short band → out) | 783 Hz | 0.41 | 406 / 1511 Hz | −22.09 dB | **20 Hz–20 kHz** |

All fifteen plotted curves fit that form to ≤ 0.05 dB rms. **Ht3 is the only
one measured in the cymbal's own band**: Fig. 9 draws Ht1 on a 4 dB tall axis
and Ht2 on a 3 dB tall axis, so both leave the plot far below 3.45 kHz, and
naively extrapolating the local 2-pole window fit above would carry an 18 dB
(Ht1) and 9 dB (Ht2) spread at 7.1 kHz. **That balance is now resolved
instead** — see below and §18.

**The one number that was already resolved, and it matters:** across 2–20 kHz
Ht3 tilts **−17.7 dB**, against the LEVEL buffer's +16.6 dB over the same
span. The tone stage very nearly cancels the level stage's rising slope. Each
path's tilt relative to 1 kHz is −24.7 (Ht1), −19.4 (Ht2), −20.2 dB (Ht3) at
20 kHz.

**The inter-band balance, resolved by nodal analysis [verified: SN p.13
schematic values, `tools/tone_stage_schematic.py`].** SN p.13's voicing board
(VG 3116-140) prints the tone network's own resistors and capacitors around
VR4 ("CY TONE", 20 kΩ(B) linear) and VR6/IC6 ("CY LEVEL"): two op-amp outputs
(the two 7.1 kHz-band Sallen-Keys, Hh2 and Hh3) and Q25's emitter (Hh1) feed a
4-node passive bridging network (C55/R112/R119 on one rail, C56/R120 + Q25's
own C58/R123/C57/R121 pre-filter on the other, VR4 splitting attenuation
between them, C90 loading the mix node into IC6's virtual ground). Solving it
by nodal analysis — the same route that gave Hh1 from R124/R127/C48/C59 above
— and fitting only the pot's wiper fraction (no per-path gain) against Fig.
9's own digitised k = 1.0 curves gives a single wiper position that matches
**all three families at once**, 707 points across three independent windows,
to 0.001–0.013 dB rms — including the fully-measured Ht3 curve across three
decades, not merely its narrow local window. The network is independently
5th-order (five capacitors, no cap-only loop), matching W14b's own word for
it, and its three transfer functions share **exactly one pole set**
(128.3/509.1/681.4/1635.7/4191.5 Hz) — "one network, one denominator" is now
an algebraic fact rather than a plausibility argument, because neither matrix
of the `(G + sC)` pencil depends on which source is driven; only the
right-hand side does. Two wrong rail assignments (swapping which op-amp is
which, or putting Hh1's pre-filter on the other band) fit 20–100× worse,
which is what makes this a measurement and not a curve-fit coincidence
(`tools/test_tone_stage_schematic.py`).

**The read is pinned to a re-renderable source, and re-reading it settled one
more thing than the fit could.** "SN p.13" is not checkable on its own, so the
scan is pinned by SHA-256 with the page and the two crop boxes every value was
read off; `tools/tone_stage_schematic.py --verify-source <sn.pdf>` re-renders
them and **REFUSES** on a missing file or a hash mismatch rather than
answering from a different printing. Re-verified 2026-09-28: all values, VR4's
wiper-to-ground wiring, and Q25's emitter as Ht1's source match the scan.
The crop also **confirms the rail assignment independently of the fit** — the
top rail's op-amp has a three-capacitor input network (C49 .0033, C53 .001,
C54 .001) and the bottom rail's has two (C51 .001, C52 .001), which is exactly
the 3rd-order/2nd-order split recorded for Hh3/Hh2 in the table above, and the
bottom op-amp is the one wired to VR2 "CY DECAY", which is Hh2's band by
definition. So the assignment is reached two independent ways, not one. The
scan is a third-party download and deliberately **not** a test dependency:
`make verify` never needs the network.

Reading the three transfer functions directly off the solved network (no
extrapolation needed — it covers the whole audio band) at the cymbal's own
corners:

| | 3.45 kHz | 7.1 kHz | vs. Fig. 9's own extrapolation bound at 7.1 kHz |
|---|---|---|---|
| Ht1 | −42.0 dB | −51.8 dB | inside [−54.5, −36.5] |
| Ht2 | −20.5 dB | −26.7 dB | inside [−31.1, −22.0] |
| Ht3 | −28.1 dB | −33.7 dB | matches the *measured* value (−33.67 dB) to 0.01 dB |

i.e. relative to Ht3, **Ht2 sits +7.0 to +7.6 dB above it and Ht1 sits 13.9 to
18.1 dB below it** across the cymbal's own band — the balance a candidate
would need, resolved rather than bounded. This is a schematic/nodal result,
not a fresh figure measurement, so it inherits the SN scan's own limits (a
1981 print, hand-read component values) rather than Fig. 9's digitisation
error; the fit residual above is the honest measure of how much slack that
leaves, and it is small.

Two defects in Fig. 9 itself, both resolved against W14b §10's prose and both
asserted by the tool: its legend prints `Ht3` twice and `Ht1` never (the
bottom sub-plot is Ht1, and also mistitles both of its own axes), and **the
k = 1.0 asterisk marks the topmost curve only in Ht3's family** — in Ht1's and
Ht2's it marks the lowest.

**What to implement (CY).** Six phase accumulators → 7-level staircase sum
→ two 2-pole band-passes (3.45 kHz Q 6; 7.1 kHz Q 6; the modal bank can host
these if its input is pre-differenced, see §14) → three swing-VCA × envelope
paths: (low band: τ ≈ 100 ms fixed), (high band: τ = decay knob, ≈40 ms …
≈400 ms), (high band: τ ≈ 20 ms fixed) → high-passes (2.5 kHz Q 0.97 unity-gain on the
low band; 8.84 kHz Q 1.00 at +6.03 dB on the DECAY band; 10.32 kHz Q 5.64
cascaded with a 1-pole high-pass at 5.20 kHz, at +8.86 dB, on the short band)
→ **tone stage** — per band, a 2-pole band-pass with the real poles in the
table above, which costs each band about −20 dB of tilt from 1 kHz to 20 kHz,
**and now also an inter-band balance resolved by nodal analysis** (Ht2 +7.0 to
+7.6 dB above Ht3, Ht1 13.9 to 18.1 dB below Ht3 across 3.45–7.1 kHz, see
above) → the level stage's rising slope, a 1-pole differentiator cornered
at 18.97 kHz. The two band-passes' own peak gains are +22.95 dB (3.45 kHz) and
+24.10 dB (7.1 kHz), so the **filter chain alone** puts the DECAY band +7.2 dB
and the short band +10.0 dB above the low band. The
VCAs' asymmetric clipping is what makes the sum "sizzle"; a linear VCA gives a
flat, chorus-like tone. Applying this balance into a candidate is #396's job,
not this section's — see §18.

**The filter chain is not the whole band balance, and the missing factor is the
larger one [measured: `tools/cymbal_band_balance.py`,
`docs/scorecard/cymbal-369/balance/`].** Including the tone stage — taken from
SN p.13's *solved* VR4 network (§18, `sn-p13-vr4.json`) rather than read off
Figure 9 — and evaluated at the frequency each band's level is actually set
(3175 Hz for the low band, 10079 Hz for both high bands), the circuit puts the
DECAY band **+17.5 dB** and the short band **+26.2 dB** above the low band. But
**three separate envelope generators and three separate swing VCAs
(Q16/Q17/Q18) sit between the band-passes and the high-passes, and no W14b
figure plots them.** Against the model's own shipped-kit level rule that leaves
a gap of **+10.1 dB (DECAY)** and **+39.8 dB (short)** unaccounted for — nearly
three orders of magnitude more than the tone stage's own uncertainty at those
frequencies (0.008 / 0.008 / 0.067 dB), and still ~1.9× the widest bound the
figure route ever offered for it (21.3 dB). Applying the resolved factors with
the VCA drives held equal was rendered and measured: H−L 25.07 dB against the
808 CY5025's 8.16, where the level rule it replaced reads 12.09. **So the band
balance needs the VCA drives from the schematic as well as VR4's network; VR4
alone does not resolve it — #420 resolved VR4 and the gap grew.**

**Do not implement the level stage without the tone stage.** They are the same
size and opposite in sign (+16.6 and −17.7 dB across 2–20 kHz), so a model with
the rising slope and no tone stage is *further* from the machine than one with
neither. That is the state `docs/scorecard/cymbal-369/candidate2/` measured:
15.3 dB short at 1 kHz and 8.7 dB long at 20 kHz, a +24.0 dB excess tilt
against the tone stage's 19.4–24.7 dB.

---

## 11. OH / CH — open and closed hi-hat

**Topology [verified: SN p.6 "OH: The high frequency range component signal
obtained by the above ½ IC3 is gated by Q27 and supplied to the buffer IC7
through the filter Q26. When the CLOSED HI-HAT (CH) is triggered while the OH
circuit is activated, Q23 turns on… At this moment, the decay time of the OH
circuit terminates. CH: This shares the same sound source with the OH. The
signal is gated by Q30 and supplied to the filter Q31 and the buffer IC7";
SN p.13 schematic].**

Both hats take the **7.1 kHz band-pass output** (IC3 pin 7) of the six-square
sum — **no noise anywhere in the hats** (hypothesis 2 confirmed). They differ
in envelope and in the high-pass after the VCA:

| | VCA | envelope [values SN p.13; τ inferred] | high-pass after VCA [values SN p.13; f0/Q inferred by the same Sallen-Key reading Werner uses for Hh1] | chart decay |
|---|---|---|---|---|
| OH | Q27 (swing) | Q24/Q29 network, **DECAY** VR3 2 MΩ(B) + R134 100 kΩ, C62 0.47 µF: RC ≈ 47 ms … 0.99 s | Q26, C66 = C68 = 0.0015 µF, R147 2.7 kΩ (fb), R146 68 kΩ: **7.8 kHz, Q 2.5** (≈ +8 dB peak) | 90 / 450 / 600 ms |
| CH | Q30 (swing) | Q28, R141 33 kΩ, R173 330 kΩ, C63 0.47 µF: fixed | Q31, C72 = C73 = 0.001 µF, R153 2.7 kΩ (fb), R155 68 kΩ: **11.7 kHz, Q 2.5** | 50 ms |

**Choke [verified: SN p.6].** A CH trigger while OH is sounding turns on Q23
(through R173) and terminates the OH envelope: the classic hi-hat choke is in
the voice circuit, not the sequencer. Implement as: CH trigger → force OH
envelope to its CH-like fast decay.

**What to implement (OH/CH).** Shared: six square oscillators (the same six as
CY and CB — one bank for the whole machine) → 7.1 kHz 2-pole band-pass Q 6.
OH: swing-VCA × exponential envelope (τ from the decay knob, 20 ms … 400 ms;
chart 90–600 ms to −20 dB) → 2-pole high-pass 7.8 kHz Q 2.5. CH: swing-VCA ×
exponential envelope τ ≈ 20 ms (chart 50 ms) → 2-pole high-pass 11.7 kHz
Q 2.5. CH trigger chokes OH. With 6 accumulators, 1 band-pass biquad, 2 VCA
nonlinearities, 2 envelopes and 2 high-pass biquads, both hats and their
choke cost less than one drum voice.

---

## 12. Summary table

f0/Q/τ are nominal for one unit built to the schematic; ±10 % on f0 and
±50 % on Q are normal (§1.7). "Chart" = Roland's measured typical values,
§1.6. The hats/cymbal filters are listed by their fixed centres; their
"decay" is the VCA envelope.

| voice | generator | f0 (Hz) | Q | τ (ms) | chart decay (ms) | envelope | post-filter | controls |
|---|---|---|---|---|---|---|---|---|
| BD | bridged-T, pulse-struck, feedback | **49.4** *(amended: not 56 — DR 0009)* (attack ≈130 for 4 ms) | 2.3 → 84 (decay knob) | 15 → 540 | 50 / 300 / 800 | ring-down (+ retrigger kick) | 1-pole LP 305 Hz–7.2 kHz (tone) | tone, decay, level |
| SD | 2 × bridged-T + noise | 238 & 476 (1981) / 173 & 336 (later) | 17 & 11 / 16 & 10 | 22 & 7 / 30 & 9 | 60 | ring-down; noise exp τ ≈ 15 ms | noise: 2-pole **BP** 2.75 kHz Q 0.7 *(amended: the pole is right, the response is a band-pass — `drum-verification.md` §8.1)* | tone (mix), snappy (noise level), level |
| LT / MT / HT | bridged-T + feedback + pink noise; diode pitch drop | 90 / 135 / 185 (±10 %) | ≈25 | 92 / 58 / 44 | 200 / 130 / 100 | ring-down; noise τ ≈ 85 ms | noise: 1-pole LP 400 Hz | tuning, level |
| LC / MC / HC | same circuit, smaller C2, no noise | 185 / 280 / 400 | 55 / 38 / 56 | 92 / 44 / 44 | 180 / 100 / 80 | ring-down | — | tuning, level |
| RS | 2 × bridged-T → swing VCA → gate | 455 & ≈1700 | 6.7 & 13.5 | 4.7 & 2.4 | 10 | VCA τ ≈ 0.5 ms; gate ≈22 ms | — | level |
| CL | bridged-T, near self-oscillation → gate | 2500 | ≥100 | (gated) | 25 | gate ≈22 ms | — | level |
| CP | white noise → BP → 2 VCAs | BP 1070 | 1.6 | — | 100 | 3 bursts @ ≈10 ms in 30 ms + tail τ ≈ 47 ms | — | level |
| MA | white noise → gate → HP | HP 10.6 k | 2.3 | — | 25–35 | ≈15 ms | 2-pole HP 10.6 kHz Q 2.3 | level |
| CB | 2 squares (540, 800) → **separate** gates → BP | BP **1100** *(fitted, DR 0010; was "0.9–2.6 k unresolved")* | **2.8** | — | 50 | two-slope exp | 2-pole BP | level |
| CY | 6 squares → 2 BP → 3 VCA → 3 HP | BP 3450 & 7100 | 6 | — | 350 / 800 / 1200 | 3 exp (one variable) | HP 2.5 k Q1; ≈10 k resonant | tone, decay, level |
| OH | 6 squares → BP 7100 → VCA → HP | HP 7800 | 2.5 | — | 90 / 450 / 600 | exp, variable; choked by CH | 2-pole HP 7.8 kHz Q 2.5 | decay, level |
| CH | same source → VCA → HP | HP 11700 | 2.5 | — | 50 | exp, fixed | 2-pole HP 11.7 kHz Q 2.5 | level |

Six of sixteen voices (BD, SD, LT/MT/HT, LC/MC/HC, RS, CL — 13 sounds) are
bridged-T ring-downs; four (CB, CY, OH, CH) are the square-oscillator bank;
two (CP, MA) are noise; and the SD and toms add noise to a ring-down.

---

## 13. The five hypotheses, answered

1. **Bridged-T resonators, not swept oscillators — confirmed** for BD, SD,
   LT/MT/HT, LC/MC/HC, RS, CL **[verified: SN p.5–6; W14a fn. 15; W16 Table
   2.2]**. The BD DECAY knob raises negative feedback into the resonator's
   foot node, cancelling its damping; f0 does not move; self-oscillation is
   just out of reach (needs g = 0.94, stock max 0.914) **[law inferred from
   W14a's transfer functions; mod that reaches self-oscillation verified: RW]**.
   Component values and the resulting f0/Q: §2–6. Two caveats that *look* like
   sweeps but are not oscillator sweeps: the BD's 4 ms attack at ≈2.6× f0 and
   its slow sigh; the toms' diode-driven pitch fall (~~up to ≈1.7× → 1×~~
   **measured ×1.06 – ×1.34 → 1×**, by accent and TUNING — §4's amendment).
2. **Six square-wave oscillators, summed, band-passed, high-passed —
   confirmed; not noise [verified: W14b §3; SN p.6, p.13]**. Nominal
   frequencies **205.3, 369.6, 304.4, 522.7, 800 (trimmed), 540 (trimmed) Hz**
   **[verified: W14b]**, from R40–R45/TM1/TM2 and C1–C6 **[verified: SN
   p.13]**; the four untrimmed ones vary per unit. CH and OH: same source
   (the 7.1 kHz band), separate VCAs, different envelopes (CH fixed ≈50 ms, OH
   variable 90–600 ms) **and** different high-pass corners (11.7 vs 7.8 kHz,
   both Q 2.5) **[inferred]**; CH chokes OH **[verified: SN]**. CY: both bands
   (3.45 and 7.1 kHz), three VCA/envelope/high-pass paths, a tone mix and a
   +6 dB/oct output tilt **[verified: W14b; SN]**.
3. **Cowbell = oscillators 5 and 6 (800 Hz and 540 Hz), the trimmed pair —
   confirmed [verified: SN p.13/p.14; W14b §3]**. Its band-pass centre is the
   one thing not established (§9).
4. **Handclap = multi-burst — confirmed [verified: SN p.6 + Fig. 13; RW]**:
   a 30 ms window containing three sawtooth-envelope bursts (period ≈10–12 ms
   **[inferred]**) of ≈1 kHz band-passed noise through an OTA, plus a
   separate transistor-VCA "reverberation" tail with τ ≈ 47 ms (chart
   100 ms).
5. **Snare = two bridged-T tones + noise — confirmed [verified: SN, RW]**.
   Frequencies: **238 and 476 Hz** on the June-1981 chart **[verified: SN
   p.14]**, computed 250/499 from the schematic values; **≈173 and 336 Hz** on
   later units per the "portion changed" note **[verified: SN p.9; numbers
   inferred]**. SNAPPY sets the *amplitude of the noise envelope*; TONE sets
   the *ratio of the two resonators* **[verified: SN p.6]**. The upper
   resonator is driven from the lower one's output **[inferred: SN p.9]**.

---

## 14. Mapping onto this chip's modal resonator bank

`modal_dp.v` computes, per mode, y[n] = x[n] + a1·y[n−1] + a2·y[n−2] with
a1 = 2r cos ω, a2 = −r², coefficients Q2.24, state 28 bits, four modes, one
multiplier, 15 clocks per sample, no RAM. A bridged-T voice **is** this
structure: a two-pole resonator struck by a short pulse and left to ring. So
**every bridged-T voice becomes a coefficient preset in the bank, not new
hardware**, provided the bank accepts (a) an excitation that is a 1 ms pulse
rather than the 1.6 ms noise burst the sizing sweep used (it does — `exc` is
just a Q1.15 input), (b) a coefficient change while ringing (for the BD attack
and the tom pitch fall; the RTL takes coefficients from ports/ROM every sample,
so a preset switch is already possible; a short ramp of a1 avoids a click),
and (c) per-mode output tapping or two banks, where a voice post-processes one
mode differently from another (SD: noise is separate anyway; RS: both modes go
through the same VCA — fine).

Pole placement: r = exp(−π f0 / (Q f_s)) = exp(−1/(τ f_s)), ω = 2π f0/f_s.
In `ModalFx.coefficients()` terms, `decay = τ / 0.9` seconds and `ratio·f0(note)`
must equal f0 — i.e. these presets are absolute frequencies, so either bypass
`note_hz` or pick the MIDI note nearest each f0 and correct with `ratio`.

Presets at f_s = 48 kHz **[inferred from the tables above]**:

| preset | f0 (Hz) | Q | τ (ms) | r | a1 | a2 | a1 (Q2.24) | a2 (Q2.24) |
|---|---|---|---|---|---|---|---|---|
| BD, chart 56 Hz, decay mid | 56 | 22.3 | 127 | 0.999836 | +1.999618 | −0.999671 | 33548016 | −16771702 |
| BD, decay short (k = 0.1) | 56 | 5.2 | 29 | 0.999289 | +1.998523 | −0.998578 | 33529659 | −16753353 |
| BD, decay long (k = 0.9) | 56 | 62 | 352 | 0.999941 | +1.999828 | −0.999882 | 33551547 | −16775233 |
| BD, attack window (≈4 ms) | 130 | 6.1 | 15 | 0.998606 | +1.996923 | −0.997214 | 33502810 | −16730478 |
| SD low (later units) | 173 | 16.3 | 30 | 0.999306 | +1.998099 | −0.998612 | 33522534 | −16753924 |
| SD high (later units) | 336 | 9.9 | 9.4 | 0.997781 | +1.993632 | −0.995567 | 33447602 | −16702846 |
| SD low (1981 chart) | 238 | 17.4 | 23 | 0.999105 | +1.997241 | −0.998211 | 33508139 | −16747204 |
| SD high (1981 chart) | 476 | 10.7 | 7.2 | 0.997093 | +1.990315 | −0.994194 | 33391953 | −16679803 |
| LT | 90 | 25 | 88 | 0.999764 | +1.999390 | −0.999529 | 33544199 | −16769312 |
| MT | 135 | 24 | 57 | 0.999632 | +1.998952 | −0.999264 | 33536844 | −16764867 |
| HT | 185 | 25 | 43 | 0.999516 | +1.998445 | −0.999032 | 33528351 | −16760972 |
| LC | 185 | 55 | 95 | 0.999780 | +1.998973 | −0.999560 | 33537210 | −16769831 |
| MC | 280 | 32 | 36 | 0.999427 | +1.997513 | −0.998855 | 33512699 | −16758011 |
| HC | 400 | 57 | 45 | 0.999541 | +1.996342 | −0.999082 | 33493060 | −16761812 |
| RS low | 455 | 6.7 | 4.7 | 0.995565 | +1.987600 | −0.991150 | 33346390 | −16628737 |
| RS high | 1667 | 13.5 | 2.6 | 0.991951 | +1.936856 | −0.983966 | 32495057 | −16508214 |
| CL (then gate 22 ms) | 2500 | 200 | 25 | 0.999182 | +1.892311 | −0.998365 | 31747718 | −16749787 |
| CP noise band-pass | 1071 | 1.6 | 0.5 | 0.956605 | +1.894439 | −0.915093 | 31783409 | −15352705 |
| CY low band-pass | 3453 | 6 | 0.6 | 0.963334 | +1.733186 | −0.928012 | 29078034 | −15569464 |
| CY/OH/CH band-pass | 7117 | 6 | 0.3 | 0.925897 | +1.104669 | −0.857284 | 18533267 | −14382844 |

Precision: the BD long-decay preset has 1 − r = 5.9·10⁻⁵ and ω = 0.0073 rad;
the sizing sweep in `model/modal_fixed.py` found Q2.24 holds pitch to 0.005 %
and decay to 0.04 % at MIDI 28 (41 Hz, r = 1 − 2.3·10⁻⁵), so the BD is inside
the validated range. **Headroom:** the sweep found the bank rings to 657× the
strike; the BD at Q 84 struck by a full-scale 1 ms pulse will be similar. The
HR = 10 output shift already assumes this.

Bank sizing per voice (modes): BD 1 (+1 if the attack preset is switched in
rather than ramped), SD 2, each tom/conga 1, RS 2, CL 1, CP 1, CY 2, hats 1
(shared with CY). Four modes therefore cover any one of {BD+SD+tom(s)} playing
together only if modes are allocated dynamically per trigger; a full kit
sounding at once needs about 8–10 modes. The ROM variant
(`modal_coef_rom_p8.v`) with 8 presets fits the bridged-T set if the presets
are {BD, SD-lo, SD-hi, LT, MT, HT, RS-lo, CL} and the congas/RS-hi/tom tuning
are host-written registers.

**Two things the bank does not do and the 808 needs:**

- *Band-pass rather than all-pole.* The bank's H(z) = 1/(1 − a1 z⁻¹ − a2 z⁻²)
  has no zeros; used on the six-square sum or on noise it passes the low
  fundamentals and DC. Pre-differencing the input twice (x[n] − x[n−2], one
  subtract and two registers) adds the (1 − z⁻²) numerator of a standard
  band-pass. For the CP/CY/hat band-passes do this; for the struck voices
  do not.
- *Time-varying coefficients.* The BD attack (4 ms at 130 Hz then 56 Hz) and
  the toms' amplitude-dependent pitch (≈1.4× → 1× over the ring) need a1 to
  change while the mode rings. Switching a1 abruptly between two presets is
  audible as a small click on the toms but is essentially what the analog
  circuit does at the BD retrigger; an 8-sample linear ramp of a1 is enough.

**What is new hardware (small):** six 16- or 24-bit phase accumulators with a
3-bit adder tree for the square bank; the swing-VCA nonlinearity (a 16-entry
asymmetric table, or `s>0 ? 4s : 0.1s` then the existing tanh ROM); ≈6
exponential-decay envelope generators (`drum_src_seq.v` already has the
shift-subtract form); the clap's 3-burst sequencer (a 5-bit counter and three
constants); two or three 2-pole high-pass biquads (a second small datapath, or
the modal bank with double pre-differencing plus a zero at DC — a high-pass is
a resonator with a (1 − z⁻¹)² numerator); and a white/pink LFSR (already in
`drum_src_seq.v`, which needs a 1-pole low-pass at ≈400 Hz for the toms).

---

## 15. What digital 808s get wrong — the practitioners' tells

Cited where a source says it; the last two are my reading of the open
implementations.

1. **Hats and cymbal made of noise.** "The user manual for the Novation Drum
   Station (an early rack-mount TR-808/TR-909 emulator) says the cymbal sound
   is generated by 'multiple noise sources,' which is patently false"
   **[verified: W14b fn. 2]**. The real signal is six squares whose *edges*
   strike two resonators; filtered noise has no periodic structure, no
   beating between partials, and no per-unit tuning. This is the single
   most-heard tell.
2. **Machine-gun retriggers.** Sample playback restarts identically; the
   808's resonators keep their state, so "each note is slightly different, as
   in a real 808… the model avoids the 'machine gun effect'" **[verified:
   W14a §11]**; likewise "inaccurate behavior when a new note is triggered
   before the previous one has died out" **[verified: W14a §1]**.
3. **Accent as a volume knob.** Accent is the trigger amplitude; through the
   pulse shaper, the swing VCAs and the tom diodes it changes attack, pitch
   envelope and distortion — "inaccurate behavior under various accent
   voltages" is a named failure of emulations **[verified: W14a §1; W14b
   §5]**. Roland's chart shows accent adds ≈+10 dB on drums but only ≈+6 dB on
   hats/cymbal **[verified: SN p.14]**.
4. **The wrong story about nonlinearity.** "The device's ingenious and
   satisfying properties are often attributed to mere circuit element
   nonlinearities. In addition to being inaccurate, this mindset directs
   attention away from a more interesting story… the architecture of the 808
   bass drum and complex interactions between subcircuits are more important
   than subtle device nonlinearities" **[verified: W14a §1, §12]**. Where
   nonlinearity *does* matter it is architectural: the swing VCAs (hats, CY,
   RS, CB), the tom diodes, the BD pulse-shaper diode.
5. **Sampling one machine.** Component tolerances (±20 % C, ±5 % R) and the
   untrimmed HD14584 oscillators mean "unmodded bass drums can sound very
   different from one another" **[verified: W14a §2]** and each unit's cymbal
   is unique **[verified: RW; W14b fn. 7]**; a sample set captures one unit at
   one knob setting. A model should expose the tolerances as parameters
   rather than bake in one instance.
6. **Missing the BD's two frequency effects.** Emulations either add a large
   pitch sweep (909-style) or none; the 808 has a ≈4 ms attack at ≈2.6× f0 that
   is heard as punch, not pitch, plus a slow sigh of a few percent
   **[verified: W14a §8]**. "The TR-808 and TR-909 together… generate their
   sounds in entirely different ways" **[verified: SOS-BD]**.
7. **Open models with non-schematic oscillator tunings.** Mutable
   Instruments' Peaks hi-hat uses six squares (correct in kind) at 414, 540,
   607, 740, 800 and 1050 Hz — computed from its phase increments at 48 kHz;
   Plaits uses the same ratios (nominal f0 414 Hz × {1, 1.304, 1.466, 1.787,
   1.932, 2.536}) **[verified: MI source]**. Only 540 and 800 match the
   schematic; the other four do not. Plaits also adds clocked noise it
   labels "not at all part of the 808 circuit" **[verified: MI
   `hi_hat.h`]**, and both MI snares excite the two modes from the pulse
   rather than in cascade. These are the models most digital 808s are
   derived from; do not copy their constants.
8. **Linear VCAs.** MI's Peaks notes "The 808-style VCA amplifies only the
   positive section of the signal" **[verified: MI `high_hat.cc`]**; Werner
   shows the swing VCA "leads to clipping — a wild swing of the output"
   **[verified: W14b §8]**. A linear multiply on the hats gives a sound that
   is right in pitch and wrong in texture.
9. **The clap as one noise burst with a fast attack.** It is three bursts
   ≈10 ms apart plus a separate tail (§7) **[verified: SN; RW]**.

---

## 16. Which voices carry "this is an 808"

Ranked judgement **[inferred]**, with what each costs on this chip:

1. **BD** — "Among all of its voices, perhaps the most influential has been
   the bass drum" **[verified: W14a §1]**; the sustained sub-bass with its
   click is the sound the name means. Cost: 1 mode + 1-pole LP + pulse
   shaper. Non-negotiable.
2. **CH / OH** — the six-square band is the second-most recognisable
   element (the "tsss" that noise-based clones miss, §15.1). Cost: 6
   accumulators + 1 BP + 2 HP + 2 VCA + 2 envelopes. Cheap; include.
3. **SD** — the 2-tone + snap snare. Cost: 2 modes + noise biquad.
4. **CP** — the burst clap is a genre marker on its own. Cost: 1 BP +
   2 envelopes + noise. Very cheap; include.
5. **CB** — iconic and the cheapest voice once the six oscillators exist
   (2 of them + 1 BP + envelope). Include.
6. **Toms / congas** — the diode pitch-fall toms are distinctive; congas are
   the same circuit. Cost: 1 mode each (time-shared, since each pair is
   exclusive). Include at least LT/MT/HT if modes allow.
7. **CY** — expensive relative to its use (2 BP + 3 VCA + 3 HP + tone stage)
   and the hats already contain its generator. Reasonable to drop or
   simplify to one band if area forces it.
8. **RS / CL / MA** — small, cheap, rarely the reason anyone wants an 808;
   CL and MA are one mode / one HP each, RS two modes + VCA. Include if the
   presets are free; drop first otherwise.

If a subset must be chosen: **BD, SD, CH, OH, CP, CB** are the six that
identify the machine; with the shared oscillator bank and the modal bank they
need 3 resonator modes, 2 biquads (band-pass for hats/clap can be the
same pre-differenced modal structure), one noise source, and about eight
envelopes.

---

## 17. Published measurements and data a model can be fitted to

- **Roland's tuning chart (SN p.14)** — per-voice frequency, decay time and
  output amplitude at normal and accent; the only manufacturer-published
  measurements (§1.6) **[verified]**.
- **W14a Figs. 10–12** — BD time-domain transient (first 13 ms), instantaneous
  frequency over 300 ms (48–58 Hz, showing the sigh), and retrigger behaviour,
  each compared with SPICE; the model was also checked against "recordings of
  a TR-808 [Michael Fischer, 'Roland TR-808 Rhythm Composer Sound Sample Set
  1.0.0', Sept. 1994]" **[verified: W14a §11, ref. 26]**. The fitted
  nonlinearity parameters (α, V0, m) are given in §8.2 of the paper.
- **W14b Figs. 3–11** — oscillator sum, band-pass/high-pass magnitude
  responses, envelope traces (log time), VCA time-domain detail, tone-stage
  families, and cymbal spectrogram/waveform pairs, with the VCA fit
  coefficients in its Table 1 **[verified]**.
- **W16 Tables 2.2, 2.3, 4.2, C.1** — the component values behind every
  bridged-T voice **[verified]**.
- The companion audio/data pages the papers cite
  (`ccrma.stanford.edu/~kwerner/papers/dafx14.html`, `…/icmcsmc2014.html`)
  **returned 404** at the time of writing **[could not establish]**.
- No independent, public impulse-response or spectrum measurement set of a
  TR-808 with documented knob settings was found in this pass **[could not
  establish]**; the Fischer 1994 sample set is the de-facto reference that
  Werner used.

---

## 18. Open items

- **CB band-pass centre and Q** — W14c (AES) is the source; paywalled.
  Candidate answers 0.9 kHz (my topology reading) vs 2.64 kHz (SOS). Fit to a
  recording.
- **CP burst period** — bounded to ≈10–12 ms by Roland's description of Fig.
  13; the comparator threshold that fixes it exactly was not solved.
- **Ratio of clap tail to bursts**, and **absolute level of the tom noise** —
  set by trimmers/resistors; no measured figure.
- **Which serial numbers have the changed snare capacitors** — the note gives
  values only.
- **CY high-pass #2/#3 exact corners — CLOSED (2026-09-27, #369).** Werner
  gives no component values for either, but **W14b Figure 4 plots both
  responses**, and that figure is vector: the coordinates are in the PDF.
  `tools/werner_fig4.py` reads them, gated on the three curves SN p.13 already
  fixes (Hbp1 3450/Q6 read as 3437/6.02; Hbp2 7100/Q6 as 7095/6.07; Hh1
  2500/Q0.97/unity as 2506/0.96/+0.00 dB) and on W14b §9's own "around
  10500 Hz". **Hh2 is a 2-pole high-pass at 8.84 kHz, Q 1.00, pass band
  +6.03 dB — not resonant. Hh3 is a 2-pole at 10.32 kHz Q 5.64 cascaded with a
  1-pole high-pass at 5.20 kHz, pass band +8.86 dB**; a 2-pole model of that
  curve leaves 0.717 dB rms against the 3-pole's 0.008, so the third pole is
  located rather than assumed, and it sits at **half** the corner, not at it
  (#102 and `docs/scorecard/cymbal-369/candidate/README.md` both assumed the
  same corner). Evidence: `docs/scorecard/cymbal-369/werner-fig4.json`;
  derivation `docs/scorecard/cymbal-369/candidate2/README.md`.

  **The paragraph below inferred the opposite from a recording, and it was
  wrong about the mechanism.** A high-pass at 10.3 kHz *can* make a 9–13 kHz
  shoulder — at Q 5.64 it peaks 15 dB above its own asymptote and is still
  11 dB down from that peak at 20 kHz, because under one octave of spectrum
  remains above the corner. What the recording ruled out was a *low-Q* (2.5)
  high-pass, not a high-pass. The rest of the paragraph stands:

  A real machine's cymbal
  (Fischer s/n 103852, `cy8/CY5025.WAV`, TONE 5.0 and **DECAY 2.5** — this read
  "TONE and DECAY at 5.0" until #102, and the second filename code is DECAY,
  where `25` means 2.5) puts
  1.1 / 10.3 / 53.2 / 23.3 / 6.0 % of its energy in <2k / 2–5k / 5–9k / 9–13k /
  >13k, with its strongest line at **3153 Hz**. Three things in §10 do not
  survive that: the long tail is the **low** band, not a high one (2–5 kHz
  measures T20 1244 ms against 745 ms at 5–9 kHz); every band lengthens with
  the DECAY knob, not only the middle one; and the ≈10.5 kHz stage behaves like
  a **band-pass**, since the machine has a 9–13 kHz shoulder with only 6 % above
  13 kHz and a low-Q high-pass at that corner cannot make that shape.
  `docs/drum-verification.md` §10 has the derivation.

- **The tone stage — SHAPE and inter-band BALANCE are both closed (2026-09-28,
  #390).** W14b Figure 9 has been digitised (`tools/werner_fig9.py`,
  `docs/scorecard/cymbal-369/werner-fig9.json`,
  `docs/scorecard/cymbal-369/tone-stage/`). §10 carries all three paths as
  2-pole band-passes with real poles (their local window shape), and the
  headline: the tone stage tilts each band about −20 dB from 1 kHz to 20 kHz,
  which very nearly cancels the LEVEL buffer's +16.6 dB. That is the right
  sign and size for candidate 2's +24.0 dB excess tilt at CY5025, and it is
  the cymbal's largest single modelling error.

  **The balance that Figure 9 alone could only bound to 18 dB (Ht1) / 9 dB
  (Ht2) at 3.45/7.1 kHz is now resolved by route 1 of the three named below**:
  SN p.13's schematic gives the tone network's own R/C values around VR4
  ("CY TONE") and VR6/IC6 ("CY LEVEL"), and solving that network by nodal
  analysis (`tools/tone_stage_schematic.py`, §10 above) reproduces Figure 9's
  own digitised k = 1.0 curves — all three families, 707 points, including the
  fully-measured Ht3 curve across three decades — to 0.001–0.013 dB rms with
  **one** shared free parameter (the pot's wiper fraction) and no per-path
  gain fudge. Route 2 (a shared-denominator constrained fit, holding Ht3's
  poles fixed while refitting Ht1/Ht2) was accordingly not needed — route 1
  succeeded outright rather than merely narrowing the bound, and it did so on
  a *stronger* footing than route 2 could have offered: route 2's premise is
  the shared denominator, whereas route 1 *derives* it, so there is nothing
  left for route 2 to test. The two wrong-rail-assignment controls in
  `tools/test_tone_stage_schematic.py` show the fit is not a coincidence
  (20–100× worse when a band is put on the wrong rail). Resolved balance,
  relative to Ht3 across 3.45–7.1 kHz: **Ht2 +7.0 to +7.6 dB, Ht1 −13.9 to
  −18.1 dB.** Whether/how to apply this into a candidate is **#396's job, not
  this issue's** — #396 already scopes "the tilt only, levels unchanged" for
  its own step. What it can now take from here is a *level* per band as well
  as a shape: normalise each path to 0 dB at 1 kHz as it already plans, then
  reinstate the three offsets above rather than the current inter-band gain
  rule. That is a revision-3 change and needs its own before/after on
  conditions not used to select it; nothing here claims it sounds better yet.

  **Two things about this entry's own evidence, recorded because they were
  wrong-then-right.** (1) The component values were cited as "SN p.13" and
  were not independently checkable; they have since been re-read against a
  SHA-256-pinned scan at a recorded page and crop box, and all of them, plus
  VR4's grounded wiper and Q25's emitter as Ht1's source, hold. The re-read
  also confirmed the rail assignment from the schematic itself (three-cap vs
  two-cap op-amp input networks, and which op-amp VR2 "CY DECAY" is wired to),
  where previously only the fit had chosen it. (2) The "fifth-order, one
  shared denominator" claim — the most structural claim in §10 — was tested
  only under `sympy`, which **no workflow here installs**. The first account
  of what that cost was itself wrong, and the correction is the more useful
  half, so both are recorded: this entry originally said the test *skipped* in
  CI and read like a pass. **It did not skip in CI; it was never collected in
  CI.** `tools/test_tone_stage_schematic.py` was named by no workflow — the
  `python` job in `.github/workflows/rungs.yml` runs `model/`, `spec/` and a
  named list of `tools/test_*.py` files, full `pytest tools/` runs only under
  `make verify` (which no workflow invokes), and `docs/dag.json` has no node
  under `tools/`. The `sympy` gate was a real but second-order problem on top
  of that. Both are now repaired: the file is named in that job (#417), so it
  runs on every pull request, and the claim is also derived with numpy/scipy
  only, as a count of finite generalised eigenvalues of the `(G + sC)` pencil,
  cross-checked against a second, independent node formulation that agrees to
  2.1e-14 dB — an agreement whose non-vacuity is itself a committed control
  (swapping R119/R129 in one formulation alone parts them by 2.75 dB). A
  load-bearing check that no job runs is the failure mode
  `docs/failure-modes.md` names, arriving one level up in the measurement
  apparatus rather than in the evidence; the general question to ask of any
  check here is **"which job names this file?"**, and for most of `tools/` the
  honest answer is still *none*.

  **Still open, and explicitly out of scope for #390: which curve of each
  family is which k.** Only k = 1.0 is marked, and the four other members
  carry no k value, so no TONE knob position except fully-open is readable
  from Figure 9, and the wiper fraction fitted above (`alpha ≈ 0.398`) is a
  property of k = 1.0 only — nothing here claims a linear or otherwise known
  map from W14b's own `k` parameter to this pot's physical rotation. That
  stays the knob-law repair named in `docs/scorecard/cymbal-369/README.md`
  §4.

  **Also still open, and NOT resolved by #390: the per-band VCA drive levels,
  which are the larger term in the inter-band balance** [`#396`, `#420`,
  `tools/cymbal_band_balance.py`, `docs/scorecard/cymbal-369/balance/`]. #396's
  own step measured the balance's decomposition before #390 landed, and its
  finding is untouched by #390's success: the unresolved factor is one **no
  W14b figure carries at all** — the three envelope generators' and swing VCAs'
  drive levels (Q16/Q17/Q18, §10).

  **#420 re-derived that decomposition from the nodal solution above**, so the
  tone term is no longer #396's bounded reading off Figure 9 (Ht1 7.4 dB / Ht2
  13.9 dB / Ht3 0.04 dB wide at each band's own calibration third) but a
  circuit value bounded by the solution's own residual — **0.008 / 0.008 /
  0.067 dB**, emitted as `docs/scorecard/cymbal-369/sn-p13-vr4.json` by
  `tools/tone_stage_schematic.py --emit`. Against the model's shipped-kit level
  rule the filters-plus-tone balance alone now leaves a **+10.1 dB (DECAY)** and
  **+39.8 dB (short)** gap (was +9.9 / +38.2 on the figure route), and applying
  it with the VCA drives held equal was re-rendered and lands 16.9 dB from the
  808's band split where the rule it replaced is 3.9 dB from it. That step also
  said, **before #390 was done, that VR4's network alone would not unblock the
  balance** — #390 resolved VR4, #420 applied it, and the gap **grew** rather
  than closing, so the prediction held in the direction that could have broken
  it. The VCA-drive half of route 1 — the envelope generator and swing VCA
  drive networks from SN p.13 — is what is still missing.

- **LC / MC / HC decay — closed, and §4's Q column is amended.** §4's three
  TOM rows land on a real machine within 3 % (LT 88.4 computed against 87.6
  measured, MT 56.6 against 57.7, HT 43.0 against 41.7). Its three CONGA rows
  are long by 12–30 % (LC 94.6 against 76.9, MC 43.2 against 38.7, HC 44.6
  against 34.3). The congas' Q is therefore taken from the machine — 44.7 /
  34.0 / 43.1 at the chart's f0 — which §1.7's ±50 % on any high-Q figure
  already allows for. Measured off `lc8/LC50.WAV`, `mc8/MC50.WAV`,
  `hc8/HC50.WAV` at R² 0.999.

- **MA envelope — closed, and §8 is missing half of it.** `ma8/MA.WAV` shows
  an **18.2 ms rise** and then a fall of τ 2.65 ms, t(−20 dB) 9.0 ms: about a
  28 ms event, which is what Roland's chart's 25–35 ms is measuring. §8 gives
  the decay (R341/C134, ≈15 ms) and mentions the attack network (C135 0.1 µF,
  R344 220 kΩ, R345 150 kΩ) only as "shape the attack" — it is 18 ms and it is
  most of the sound's character. A voice generator with no attack ramp cannot
  reproduce it; ours makes the same 28 ms event with the shape reversed.
- The hats' and maracas' Sallen-Key Q ≈ 2.5 rests on reading R147/R153/R339 as
  the feedback resistor (the drawing convention Werner's Hh1 coefficients
  confirm for the cymbal's Q25 stage) — if a scope shows a flat rather than
  peaked ≈8 kHz response on a real unit, swap R_fb and R_gnd in §11.

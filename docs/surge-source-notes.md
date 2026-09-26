# What Surge's filter source says, and what it costs us

Surge XT is the only reference in `docs/discrimination.md` §8 whose internals
can be read. This file is what reading them produced, with our own numbers
beside each one. Source: `surge-synthesizer/sst-filters` `main`
(`include/sst/filters/`) and `surge-synthesizer/sst-basic-blocks`
(`include/sst/basic-blocks/dsp/FastMath.h`). The `VintageLadder::Huov`
namespace is mathematically identical at the Surge 1.2.3-era commit `8ea9b8d`
and on `main`, so `main` describes the 1.2.3 that is installed here.

**Nothing here is a recommendation.** Each section gives the reference's
choice, ours, and the cost of changing, so the agent that owns
`model/fixed.py` can make the trade.

---

## 1. Coefficient smoothing — Surge band-limits its cutoff control; we do not

`FilterCoefficientMaker_Impl.h`, and `constexpr float smooth = 0.2f`:

```cpp
tC[i] = (1.f - smooth) * tC[i] + smooth * N[i];   // one-pole LP on the TARGET, per block
dC[i] = (tC[i] - C[i]) * blockSizeInv;            // then a linear ramp of C across the block
```
and inside every filter's `process`, per oversampled sub-step:
```cpp
f->C[k] = A(f->C[k], M(dFac, f->dC[k]));          // dFac = 0.5 at 2x, 0.25 at 4x
```

Two stages: a one-pole low-pass on the coefficient **target**, then a linear
ramp of the coefficient itself across the block. At Surge's 32-sample block
and 48 kHz the first stage is a time constant of about **3 ms (a ~53 Hz
corner)**. Surge deliberately band-limits the control signal before it reaches
the filter.

**Ours has no smoothing of any kind.** `voice_fx._render` computes the cutoff
per frame from the filter envelope, reads `g` from the ROM per frame, and
`LadderFx.process` uses `g_tab[i]` per sample. Per-sample update is the
*favourable* case for stepping — it is block-rate updates that zipper — but it
also means an envelope step reaches the coefficient with no lag and no limit.

Neither is obviously right: smoothing costs 3 ms of lag on a fast filter
envelope, which is audible in its own way. But ours has not been chosen
deliberately, and `model/reference_movement.py` now measures the consequence.

---

## 2. Our cutoff control resolution, in closed form

`model/reference_movement.py --stage control`. The control path is
`cutoff in INTEGER Hz -> g in Q0.16, from a 128-entry ROM read with linear
interpolation`. Inverting the realised `g` back to an effective cutoff gives:

| commanded | effective | static error | one step | step | gain step at 24 dB/oct |
|---|---|---|---|---|---|
| 30 Hz | 29.64 | **−21.1 cents** | 1 Hz | **53.7 cents** | **1.075 dB** |
| 60 Hz | 59.57 | −12.6 | 1 Hz | 27.0 | 0.540 |
| 120 Hz | 119.36 | −9.2 | 1 Hz | 13.6 | 0.272 |
| 200 Hz | 199.46 | −4.7 | 1 Hz | 10.2 | 0.204 |
| 500 Hz | 499.77 | −0.8 | 1 Hz | 3.3 | 0.067 |
| 2 kHz | 1999.50 | −0.4 | 1 Hz | 0.92 | 0.018 |
| 6.4 kHz | 6399.83 | −0.05 | 1 Hz | 0.19 | 0.004 |
| 16 kHz | 15999.26 | −0.08 | 1 Hz | 0.14 | 0.003 |

Two separate findings:

- **The step is one hertz, because the cutoff register is integer hertz**
  (contract 5.1: `cut_lo`, `cut_hi`, `track_hz` are 16-bit integer Hz). One
  hertz at 30 Hz is 58 cents. The `g` ROM is not the limit — it interpolates.
  The staircase is therefore **coarse at the bottom of the range and invisible
  at the top**, which is the opposite of where a test that sweeps the top
  octave would look.
- **The ROM is 9–21 cents flat below 120 Hz**, statically, and under a cent
  above 300 Hz. That is the 128-entry ROM's 256 Hz-spaced linear interpolation
  across the part of `1 − exp(−2πf/fs)` that bends most, with entry 0 pinned at
  `g(0) = 0`. It is a static accuracy defect, not a movement one, and it sits
  in exactly the region DR 0006's compensation also lives in.

---

## 3. Movement, measured

`model/reference_movement.py --stage sweep|plugins`. A steady 2 kHz carrier,
the cutoff swept 500 Hz → 8 kHz, ripple = what survives a high-pass of the
output's envelope (`audio_measure.envelope_ripple_db`, ground-truthed against
a staircase of known step size).

| filter | 10 oct/s | 2.5 oct/s | 1 oct/s |
|---|---|---|---|
| **ours** | **−46.1 dB** | **−69.9** | **−82.0** |
| Surge Type 2 (Huov) | −52.0 | −76.4 | −91.2 |
| Surge Type 1 (RK) | −51.6 | −76.1 | −90.9 |
| Mini V3 | −51.5 | −74.5 | −86.2 |
| ~~Diva~~ | ~~−32.0~~ | ~~−33.0~~ | ~~−33.1~~ | **WITHDRAWN: unlicensed, see `docs/reference-integrity.md` §1** |

**Ours has a 5–9 dB higher movement ripple floor than Surge and Mini V3**, and
the gap widens as the sweep slows. Every filter's ripple falls with the sweep
rate at roughly 12 dB per octave of rate, which is the signature of a smooth
process — **nobody here is stepping badly**, ours included. The size of our
excess is consistent with the control quantisation of §2, but this measurement
does not isolate the cause.

**Diva's row is not comparable**: at a flat −33 dB at every rate it is
reporting the floor of its own oscillator (it has no audio input, so its own
triangle is the carrier), not its filter.

**A harness artefact that had to be found first, and is worth recording.** At
dawdreamer's default 512-sample block, parameter automation is applied per
block — 93.75 Hz — so a swept cutoff moves in 93.75 Hz steps. The first run
measured *all three plugins* at an identical 94 Hz dominant ripple rate while
ours (which moves per sample) sat at the floor, and it looked exactly like
"the references step and we do not". Re-running at a 16-sample block moved the
artefact out of band and reversed the conclusion. The table above is the
16-sample run.

This section sweeps the **cutoff** and holds everything else still. The other
two moving controls — resonance swept continuously through the
self-oscillation onset, and the filter-modulation bus driven at an audio rate
— are **§8**, measured the same way and in the same band.

**Differential check, ours only and the strongest evidence available**: render
the same sweep twice through the same filter, once with the shipping integer
control path and once with the cutoff and `g` in float. The difference is
**−49 dB** over a 60–960 Hz sweep and **−66 dB** over 500 Hz–8 kHz, and it is
**independent of sweep rate** over a 40× range — which says it is the *static*
error of §2, not a staircase. Injected control: rounding the commanded cutoff
to 32 Hz moves that differential by **+24.5 dB**, so the measurement has power.

---

## 4. The `tanh`: a table against a polynomial, with both costs

`docs/discrimination.md` §8.5 established that our excess 5th harmonic at
self-oscillation is the 16-entry table. Surge uses
`basic_blocks::dsp::fasttanhSSEclamped` — a Padé rational, clamped to ±5:

```cpp
auto x2  = x * x;
auto num = x * (135135 + x2 * (17325 + x2 * (378 + x2)));
auto den = 135135 + x2 * (62370 + x2 * (3150 + 28 * x2));
return num / den;
```

Measured over our own domain [0, 4):

| approximation | ROM bits | max &#124;err&#124; | rms err | cost per evaluation |
|---|---|---|---|---|
| **LUT 16 linear (shipping)** | **256** | **6.00e−03** | 2.09e−03 | 1 mul, 1 add, 2 reads |
| LUT 32 linear | 512 | 1.52e−03 | 5.35e−04 | 1 mul, 1 add, 2 reads |
| LUT 64 linear | 1024 | 6.71e−04 | 1.48e−04 | 1 mul, 1 add, 2 reads |
| LUT 128 linear | 2048 | 6.71e−04 | 5.64e−05 | 1 mul, 1 add, 2 reads |
| LUT 16 quadratic | 256 | 3.25e−02 | 5.95e−03 | 3 mul, 4 add, 3 reads |
| Padé [3/2] `x(27+x²)/(27+9x²)` | 0 | 2.35e−02 | 1.33e−02 | 3 mul + **1 divide** |
| Padé [7/6] (Surge) | 0 | **1.50e−05** | 3.39e−06 | 7 mul + **1 divide** |

The ladder evaluates `tanh` **five times per oversampled sub-step and runs two
sub-steps per frame — ten evaluations per sample, 480 000 per second.** So
Surge's polynomial is **70 multiplies and 10 divides per sample** against our
table's 10 multiplies and 20 ROM reads. The ECP5 build already uses 14 of 28
DSPs; on the ASIC a divider is a sequential unit. **A polynomial does not
obviously win here, and this table is the trade, not a recommendation.**

Two results that were not expected:

- **A quadratic interpolator on the existing table is WORSE than the linear
  one** (3.25e−02 against 6.00e−03). The table is *edge-sampled for linear
  interpolation* and its guard entry is the clamp value, so a 3-point
  Lagrange runs into that discontinuity. A table designed for quadratic
  interpolation was not measured and might do better; this one does not.
- **There is a 6.707e−04 step discontinuity in our `tanh` at x = 4, and it
  costs nothing to remove.** `tanh(4) = 0.9993293`, which is 32745 in Q1.15,
  but `fixed.LadderFx.tanh_fx` returns **32767** above the domain and the last
  bin interpolates toward 32767. Measured max error restricted to [0, 3.9]
  versus over [0, 4.0]:

  | entries | [0, 3.9] | [0, 4.0] |
  |---|---|---|
  | 16 | 6.00e−03 | 6.00e−03 |
  | 32 | 1.52e−03 | 1.52e−03 |
  | 64 | 4.08e−04 | **6.71e−04** |
  | 128 | 1.32e−04 | **6.71e−04** |
  | 256 | 6.42e−05 | **6.71e−04** |

  **Past 64 entries the clamp step is the entire error.** Widening the table
  beyond 64 buys nothing until the guard entry is changed to 32745 (or the
  domain extended). That is a one-constant change with no area cost, and it
  should be made *before* anyone pays 1792 ROM bits for 128 entries.

---

## 5. Decimation — we match the reference exactly, and neither filters

We run 2× oversampling, and `fixed.LadderFx.process` takes `y[3]` from the
**last sub-step** with no decimation filter, and holds the input across both
sub-steps with no interpolation filter. Naive 2×.

**Surge's `VintageLadder::Huov` does exactly the same thing**: its `process`
runs two sub-steps and `return outputOS[1];`. Surge's RK model does slightly
better — 4× with a 4-tap Lanczos-ish window (`windowFactors = {−0.0637, 0,
0.5732, 1}`, scaled by 1.5), described in its own comment as "a bit of a
hack... really we should do a proper little FIR".

`sst-filters` *does* ship `HalfRateFilter.h` — a polyphase all-pass half-band,
`M` = 1…6 stages, steep and soft coefficient sets — but **no filter in
`sst-filters` uses it.** It is Surge's synth-wide oversampler, not part of any
ladder. So on decimation quality we are identical to the implementation of the
same paper, and the honest statement is that *neither* of us filters, not that
we are behind.

---

## 6. `CutoffWarp.h` and `ResonanceWarp.h`

Nonlinear cutoff and resonance variants. `CutoffWarp` puts a saturator in the
loop selected by the low bits of the subtype — `stages = subtype & 3` and
`sat = (subtype >> 2) & 3` — choosing between `softclip_ps`, `fasttanhSSEclamped`
and others, with a per-subtype `lpNormTable` makeup gain and, for the OJD
subtypes, a resonance makeup of `1/sqrt(reso)`. Nothing here is closer to a
Minimoog than what we have; it is a menu of saturator placements. Worth
knowing it exists when the drive character is next argued about; nothing to
adopt today.

---

## 7. The errand: does `ddiakopoulos/MoogLadders` exist, and what is in it?

**Yes.** `github.com/ddiakopoulos/MoogLadders`, created 2012, last pushed
**2026-06-13**, 397 stars, default licence **Unlicense**, CI on three
platforms. Verified by fetching the repository tree, not from recollection.

**Ten ladder models**, one header each, no external dependencies:
`StilsonModel`, `HuovilainenModel`, `KrajeskiModel`, `MicrotrackerModel`,
`MusicDSPModel`, `OberheimVariationModel`, `ImprovedModel`,
`RKSimulationModel`, `SimplifiedModel`, `HyperionModel` (19.6 kB, the newest,
2025). Plus `LadderFilterBase.h`, `LadderFilterOversampledBase.h`,
`Oversampler.h`, `HalfBandFilter.h`, `NoiseGenerator.h`, `MoogUtils.h`, a
`RunFilters` example, and `assets/sample.wav`.

**It also ships its own validation suite**, which the coordinator's
recollection did not include and which is the most useful part:
`scripts/filter_verification.py` (71 kB) generates impulse, step, chirp, sine,
two-tone, white-noise and near-DC test signals, runs them through each filter,
and computes frequency response, phase, group delay, THD, IMD, spectrogram,
step metrics, RMS, PSD and **self-oscillation detection**, with
`scripts/dashboard_generator.py` (28 kB) producing an interactive HTML
dashboard.

**Two cautions before it is treated as a reference set:**

- **Licences are per model, and they are not all permissive.** The README's
  own table: Huovilainen is **LGPLv3** (from CSound) and "closed-source
  friendly: if dynamically linked"; `Simplified` is a custom licence and
  "No"; `MusicDSP` is suggested CC-BY-SA. The repository default is Unlicense
  but that does not cover those files. We implement Huovilainen **from the
  paper**, not from that code, so nothing here contaminates us — but anyone
  copying a header needs to read its own licence first.
- **The README states the filters "have not been rigorously verified for all
  combinations of cutoff, resonance, and sampling rate"** and warns of
  blow-ups at untested parameters. It is a collection of *candidates*, not a
  set of validated references.

**Which is the better reference set for #46?** They answer different
questions. `sst-filters` is better as a *reference implementation*: it ships
in a product, is exercised by users daily, has one consistent API and
oversampling wrapper, and includes the non-ladder family (`DiodeLadder.h`,
`K35Filter.h`, `OBXDFilter.h`, `TriPoleFilter.h`, `CytomicSVF.h`) alongside
the two Vintage Ladder models we already measured. `MoogLadders` is better as
a *candidate survey*: ten ladder topologies side by side in one readable style
with a ready-made analysis harness, which is exactly what "pick a ladder
variant" needs. **Use MoogLadders to choose, and `sst-filters` to check the
choice against something that ships** — and take neither on trust, because
this project already found that Surge's Vintage Ladder Type 2 cannot
self-oscillate and never reaches its own nonlinearity at any usable level
(§8.3 of `docs/discrimination.md`).

---

## 8. Movement, part two: the resonance moving, and modulation at audio rate (2026-09-26)

§3 swept the **cutoff** and held everything else still. Issue #53 named two
more moving controls that no test reached, and this section is both of them.
`model/reference_movement.py --stage resonance` and `--stage audio-rate-mod`;
standing checks in `model/test_moog_acceptance.py`
(`test_a_continuous_resonance_sweep_through_self_oscillation_does_not_zipper`,
`test_audio_rate_filter_modulation_adds_no_more_than_the_static_control_error`)
with their injected controls beside them. Same estimators as §3, same 800 Hz
band limit, so the numbers here and the numbers there are comparable.

Two of #53's four proposals were already done when this was written and are
**not** re-measured here: the sweep-rate ladder is §3, and the
envelope-driven fast sweep is `test_a_cutoff_jump_mid_note_does_not_click`,
which predates the issue.

### 8.1 Resonance swept continuously through the self-oscillation onset

`test_a_cutoff_jump_mid_note_does_not_click` already toggles `k_eff` between 0
and the onset as a 5 ms square wave. That is two abrupt edges; #53 asked for
the continuous crossing, where the loop sits at unity gain for as long as the
ramp takes to pass through it. The cutoff is held at 2000 Hz, the carrier sits
on it, and `res` ramps through the onset — which DR 0006's compensation ROM
puts at **res = 1.0000**, computed rather than assumed. With the input muted
half way the output still grows **+5.6 dB**, so the ramp genuinely reaches
self-oscillation and this is not a measurement of a resonant peak.

<!-- claim: test=model/test_moog_acceptance.py::test_a_continuous_resonance_sweep_through_self_oscillation_does_not_zipper -->

| ramp | 4 res/s | 1 res/s | 0.25 res/s |
|---|---|---|---|
| 0.6 → 1.4 (up through onset) | −61.8 † | **−82.2** ‡ | **−90.2** ‡ |
| 1.4 → 0.6 (down through onset) | −62.2 † | −82.9 ‡ | −90.6 ‡ |
| 0.1 → 0.9 (same rate, never crosses) | −58.6 † | −78.2 ‡ | −85.3 ‡ |

† the dominant residual rate is 20–27 Hz, i.e. the ramp's own trajectory
leaking through the 40 Hz high-pass — an upper bound, not a measurement of
stepping. ‡ the dominant rate is the 2 kHz carrier, i.e. the analytic
envelope's own residual — also an upper bound. **Every cell in this table is a
floor**, and the tool says so per-cell rather than leaving the reader to
check; that is what `reference_movement.ripple_verdict` is for.

Three findings:

- **Nothing measurable happens at the crossing.** A ramp that never reaches
  the onset reads 3–5 dB *worse* at every rate (it is quieter, so the same
  residual is a larger fraction of it). If entering self-oscillation thumped,
  the crossing rows would be the loud ones; they are the quiet ones.
- **Direction does not matter**, 0.4–0.7 dB between up and down at every rate.
- **The reading falls ~12 dB per halving of ramp rate**, the same
  smooth-process signature §3 found for cutoff sweeps — which here is the
  floor falling, not the artefact.

**Injected control (START RED), and it is what makes the table usable.** Write
`k` every N frames instead of every frame — a firmware updating the filter
from a timer rather than from the sample clock:

| `k` write interval | ripple | residual vs shipping | separation |
|---|---|---|---|
| every frame (shipping) | −82.2 ‡ | — | — |
| every 96 frames (2 ms, 500 Hz) | −74.3 | −57.6 dB | **+7.9 dB** |
| every 240 frames (5 ms, 200 Hz) | −58.3 | −49.1 dB | **+23.9 dB** |

Both injected rows report their dominant rate as the write rate itself (500
and 201 Hz), which is the estimator naming the staircase it was built to find.
The measurement has power, it is graded rather than binary, and the shipping
reading is 24 dB below the coarsest injection.

### 8.2 Oscillator 3 on `MR_FILT` at an audio rate

`test_the_mod_wheel_at_full_sweeps_the_cutoff_from_440_to_at_least_2400`
already drives the filter-modulation bus from oscillator 3, but as a
square-wave LFO, and it measures the *depth* of the swing. #53 asked for the
same bus at an audio rate, "far harder than a hand on a knob". The trajectory
is taken from `VoiceFx.trace['cut']` — the shipping code, not a
reimplementation of the modulation arithmetic — and the carrier is the same
2 kHz sine §3 used.

**First result, and it is about the instrument rather than the filter:
`envelope_ripple_db` cannot answer this question, at any of the nine operating
points.** An audio-rate cutoff modulation *is* an envelope modulation: the
estimator reads −4 to −41 dB, forty decibels above anything the control path
could contribute, and what it is reading is the intended signal. The tool
REFUSES all nine rather than printing them. Two independent preconditions
catch it — the dominant residual rate matching the modulation rate or one of
its first three harmonics, and, for the intermodulation products that test
cannot enumerate, **the same estimator run on the artefact-free float render
returning the same number**. The second is the general one: if a render with
no control-path error in it by construction reads the same ripple, the ripple
is not the control-path error.

So the answer comes from §2's differential instead: the same carrier through
the same ladder twice, once with the shipping control path and once with that
arithmetic in float, `k` held identical between the two.

<!-- claim: test=model/test_moog_acceptance.py::test_audio_rate_filter_modulation_adds_no_more_than_the_static_control_error -->

| depth | cutoff reached | osc3 at 110 Hz | 440 Hz | 1760 Hz |
|---|---|---|---|---|
| 0.25 oct | 1681–2377 Hz | −53.1 dB | −53.1 | −53.1 |
| 1.30 oct (`MFD_REF_OCT`) | 812–4923 Hz | −56.5 | −54.1 | −55.4 |
| 3.90 oct (at the ±4 oct clamp) | 133–21600 Hz | −57.1 | −52.3 | −63.0 |

- **Flat in rate and in depth**, −52 to −63 dB over a 16× range of modulation
  rate and a 16× range of depth. That is the signature of a *static* control
  error, which is the same conclusion §3 reached for cutoff sweeps by the same
  argument. Audio-rate modulation is not a harder case for our control path
  than a hand on a knob — because our control path has no rate-dependent term
  in it at all.
- **It is ~13 dB worse than §3's 500 Hz–8 kHz sweep differential (−66 dB), and
  the extra is the modulation bus's own arithmetic**, which §3 never
  contained. Splitting the −54.1 dB at 1.30 oct / 440 Hz by rendering the
  intermediate that has one quantiser and not the other:

  | contribution | residual |
  |---|---|
  | Q3.12 octave word + interpolated exp ROM | **−59.7 dB** |
  | 16-bit integer-hertz register + g ROM | **−60.2 dB** |
  | total | −54.1 dB |

  **The two are equal to within half a decibel.** A change that improves only
  the cutoff register or only the `g` ROM — the target §2 and §3 point at —
  leaves half of the audio-rate modulation error in place. This is the first
  measurement in the repository of the modulation path's own contribution.

**Injected controls (START RED).** Two, because they break the control path in
different places:

| injection | residual | separation |
|---|---|---|
| shipping | −54.1 dB | — |
| modulated cutoff rounded to 8 Hz | −54.0 | +0.1 dB |
| modulated cutoff rounded to 32 Hz | −48.1 | **+6.0 dB** |
| cutoff held 128 frames (375 Hz) | +0.9 | **+55.0 dB** |
| cutoff held 512 frames (93.75 Hz) | +1.3 | **+55.4 dB** |

The 8 Hz row is the honest bottom of this measurement's resolution: rounding
the cutoff to 8 Hz is *invisible* to it at this operating point, so the
smallest coarsening it can resolve is somewhere between 8 and 32 Hz.

**The block-hold rows are a result, not only a control.** Holding the
modulated cutoff for one 512-sample block — what a block-based plugin does,
and the exact mechanism behind §3's 94 Hz harness artefact — does not merely
coarsen the control at an audio modulation rate, it **aliases the modulation**:
the "artefact" comes out 1.3 dB *larger than the signal*. §1 called our lack
of coefficient smoothing "not obviously right, and not chosen deliberately".
For audio-rate filter modulation it is not a preference: per-sample update is
required, and Surge's 3 ms one-pole on the coefficient target would be a
~53 Hz corner across a bus that routinely carries 1760 Hz.

### 8.3 Wrong-then-right, for calibration

Two of the five numbers in this section were wrong before they were right, and
both were caught by a precondition rather than by inspection:

- The first audio-rate ripple table printed **−30.5 dB** as a clean `ok`
  reading at 3.90 oct / 1760 Hz. It was intermodulation of the intended
  modulation. The harmonic check passed it because the product was not at
  *n*·1760 Hz; the artefact-free-render check caught it, and is the
  precondition that now ships.
- `mod_trajectory`'s "is this the same modulation" assertion was first written
  as an absolute 3 Hz bound and fired on the 3.90 oct case at 5 Hz — correctly,
  in the sense that it refused rather than reported, but for the wrong reason:
  at a modulated cutoff of 21.6 kHz the control path's own resolution *is*
  more than 3 Hz. The bound is now relative.

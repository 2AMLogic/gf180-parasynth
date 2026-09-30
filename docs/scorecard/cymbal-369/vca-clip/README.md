# #369 step 8 — §10's asymmetric clipping, measured and eliminated; and two preconditions that cost step 7 most of its headline

Step 7 (`../low-tail/README.md`) ended by naming exactly one next question and deliberately not asking it:

> **Does the VCAs' asymmetric clipping supply 1–2.5 kHz with a slower decay than the low band's?** It is the only
> element §10 names for this region and does not quantify, and it is testable the same way this step was: put the
> documented nonlinearity in the probe's mixture, measure rho and the M/Ln energy with the *same* instrument, and see
> whether it crosses the bound in §5 and the skirt baseline in §2.

This is that question, asked and answered. **No kit, RTL or scorer change. R1 is unchanged. No candidate is
promoted.** Nothing already committed under `docs/scorecard/cymbal-369/` moves.

Instrument: `tools/cymbal_vca_clip.py` (+ `tools/test_cymbal_vca_clip.py`, 39 tests). Result:
`vca-clip.json`, carrying the commit it was produced at and a `sources_dirty` flag, clean.

---

## 1. Headline

**No.** No position, drive or asymmetry of §10's asymmetric clipping lands inside the 808's measured range on both
quantities at once. The energy half of the prediction was right; the decay half was wrong **in sign**, and wrong by
too much to be tuned into place.

| asym 0.5, §1.5 staircase source | M re Ln | rho_M(−10) | rho_Mn(−10) |
|---|---:|---:|---:|
| linear, no clipper, §10's balance | −7.90 | 0.9917 | 1.0966 |
| `post_vca` (§10's literal position), −20 dB | −6.92 | 1.1108 | 1.4791 |
| `post_vca`, 0 dB | −5.57 | **2.0660** | 2.6894 |
| `sum`, 0 dB | −4.53 | **2.6492** | 3.3319 |
| `sum`, +12 dB | −3.34 | **2.8195** | 3.2741 |
| **the 808** | **−5.00 … −3.45** | **1.048 … 1.139** | **0.949 … 1.097** |

By the drive at which M re Ln enters the machine's range, rho_M has reached **2.0–3.1** against the machine's
1.05–1.14. The closest single row over all four asymmetries is `post_vca` at −20 dB, still 1.4–2.6 dB short in
energy. **There is no drive in between**: the ratio in which clipping moves the two is a property of the mechanism,
not a free parameter.

**And the same is true of the linear chain.** Over the 64 inter-band balances `rendered_bound()` sweeps, with either source,
`n_balances_in_808_box` is **0**. What replaces both step 7's finding and this one is a *joint* constraint — see §4.

---

## 2. Why the answer can be believed

### The chain is the magnitude model, exactly

`chain_db` reads the response off the very `lfilter` sections `render` runs, built from the same bank registers
(`modal_fixed.pole_regs`, `cymbal_candidate.real_pole_regs`) that `cymbal_tone_realisation.band_chain_db` and
`realised_db` use. The two agree to **3e-13 dB rms** over 200 Hz–20 kHz (the artifact rounds properties to six
decimals, so it records this as `0.0`). That is not a tolerance chosen to pass: they
are the same filter written twice, so anything but equality is a transcription bug. `NO_HIGHPASS` moves it past
1 dB, so the check is not vacuous.

`analytic_band_db` reproduces `cymbal_m_origin.band_energies`' committed `cand3` column to 0.02 dB, which is what
makes everything below a statement about the *same* circuit step 7 measured rather than a different one.

### The clipper's own known answers

§10 says "asymmetric clipping" and quantifies nothing, so the block is a biased tanh normalised so that **its linear
term is the identity at every drive**:

    y(x) = [tanh(g x + b) − tanh(b)] / [g (1 − tanh(b)²)] = x − tanh(b)·g·x² + O(x³)

Two consequences, both used as controls. Drive → −∞ is the linear chain *exactly* (the −40 dB row of every sweep
reproduces the `none` row to three decimals, and the residual is asserted to fall a decade per 20 dB). And at b = 0
the second-order term vanishes **identically**, which is the paired negative.

| control | reading | bound |
|---|---:|---|
| `im-known-answer` — two sines, difference tone at 800 Hz against the analytic `a2·A1·A2` | **0.9865** | 0.98–1.02 |
| `asym-load-bearing` — the same measurement with the bias forced to 0 | **−191 dB** | < −40 dB |
| `product-law` — a quadratic on `exp(−t/τ)` yields a component of time constant τ/2 | **1.041** | 0.92–1.08 |
| `tau-reads-back` — §10's τ_low read back off the render by the shared instrument | **1.047** | 0.90–1.10 |
| `source-comb` — all six §1.5 fundamentals present, sum takes exactly 7 levels | **7.0** | = 7.0 |
| `source-alias` — the generator's own fold-back, against the same source at 16× | **0.0176 dB** | < 0.30 dB |
| `chain-exact` | **3e-13** (`0.0` in the artifact, rounded) | < 1e-6 |
| `linear-limit` — low band vs `m_origin`'s committed figure | **0.252 dB** | < 0.6 dB |

`source-alias` deserves a note: the whole source-tilt correction in §3 rests on the staircase's real 1/n harmonic
content in 891–2828 Hz, and a naively sampled square folds its own out-of-band harmonics back into exactly that
region. That would have looked identical to the finding. It is bounded rather than assumed.

### The defect matrix

Six injected defects, each verified to move at least one named property; two asserted blind and verified blind.
Generated from the run rather than transcribed:

| injected defect | properties it turns red |
|---|---|
| `NO_NONLINEARITY` | `im-known-answer`, `asym-load-bearing`, `drive-monotone` |
| `SYMMETRIC_NL` | `im-known-answer`, `asym-load-bearing`, `product-law` |
| `SINGLE_OSC` | `source-comb`, `drive-monotone` |
| `NO_HIGHPASS` | `chain-exact`, `linear-limit`, `tau-reads-back`, `drive-monotone` |
| `SWAP_TAUS` | `tau-reads-back` |
| `NO_LEVEL_STAGE` | `chain-exact` |
| `POST_GAIN_2X` | *(blind by construction, verified blind in all ten)* |
| `SEED_PHASE` | *(blind by construction, verified blind in all ten)* |

`SEED_PHASE` matters beyond hygiene: §1.5 says only two of the six oscillators are factory-trimmed and the other four
are nominal ±(tens of) percent unit to unit, so a conclusion that depended on one machine's particular six
frequencies would not be a conclusion about the TR-808.

### The targets are read from the artifacts, not from the prose

`targets()` re-reads `../low-tail/low-tail.json` and `../low-tail/m-origin.json` and **REFUSES** if any number this
module quotes has drifted, including the analysis window. It refused on its first run and was right to — see
wrong-then-right 3.

---

## 3. What this corrects in step 7, which is the larger half of the result

Step 7's §5 headline — *"the documented chain is short by 9.4 dB in M and 16.6 dB in Mn"* — compares an **analytic**
bound against a **filtered** measurement. Two preconditions were assumed there and are asserted here.

### 3a. The analysis filter reads M high on any band peaking at 7.1 kHz

`leakage_error()`, white source, per band: the cascade rendered and read through `cymbal_low_tail.measure`, against
the same cascade's analytic band energy.

| band | rendered M re Ln | analytic M re Ln | the analysis filter reads | and in Mn |
|---|---:|---:|---:|---:|
| low | −13.30 | −13.05 | **−0.25 dB** | −0.78 dB |
| decay | −9.07 | −12.74 | **+3.67 dB** | +6.85 dB |
| short | −6.77 | −16.26 | **+9.49 dB** | +18.81 dB |

The low band reads true because **Ln *is* its peak**. The two bands peaking at 7.1 kHz do not, because the analysis
band-pass's skirt admits more of that peak's shoulder than the band truly holds. Step 7's own rejection table quotes
−91.3 dB at 7100 Hz — that is the rejection **at** the peak, and what matters is the integral over the shoulder
between 2.8 and 7.1 kHz, which is nowhere near 91 dB down.

### 3b. The source is not flat

`cymbal_m_origin.band_energies` integrates the chain's magnitude response, i.e. the energy a **white** source leaves.
§1.5's source is a 7-level staircase of six squares whose harmonics fall at 6 dB/octave. Through the same chain and
the same analysis filter:

| band | white | staircase | moves |
|---|---:|---:|---:|
| low | −13.30 | −8.75 | **+4.55 dB** |
| decay | −9.07 | −9.11 | −0.04 dB |
| short | −6.77 | −13.20 | −6.43 dB |

### 3c. The bound, computed the way the measurement it is compared against was computed

`rendered_bound()`, best over 64 inter-band balances, same instrument on both sides:

| | M re Ln | Mn re Ln |
|---|---:|---:|
| analytic, flat source (**step 7's number**) | **−12.74** (−13.10 for the `ref` cascade) | −27.66 |
| rendered, flat source | −8.72 | −23.66 |
| rendered, §1.5's staircase source | **−5.07** | −16.40 |
| the 808, 20 settings | **−5.00 … −3.45** | −15.20 … −13.05 |

**So the energy gap is about 1.3 dB at the best balance and 4.2 dB at §10's own balance, not 9.4 dB.** In Mn it is
about 1.2 dB rather than 16.6.

This does **not** make the chain right and it does **not** touch step 7's within-record decay ratio, which is immune
to both corrections by construction. It means *"a missing mechanism supplies 9–17 dB"* overstates the case by most
of its size, and **the two things that were missing were in the apparatus, not in the machine.**

### 3d. And step 7's linear-decay claim does not hold for the rendered chain

Step 7 §5: *"sweeping the balance from −12 to +30 dB … rho_M(−10) stays between 0.89 and 0.97 — it never even clears
the skirt baseline."* Over 64 balances of the rendered chain it reaches **1.2413** (staircase) and **1.2643**
(white), past the 808's own top of 1.139. Step 7's sweep varied one level of an FFT-shaped white mixture; this one
renders the documented filters from the documented source over a two-dimensional balance grid.

---

## 4. What replaces both halves: a joint constraint

Neither quantity is the finding on its own, because they are not independent — **the balances that raise rho are the
balances that starve M**:

| linear chain, staircase, 64 balances | M re Ln | rho_M(−10) |
|---|---:|---:|
| at the best M re Ln | **−5.07** | below the box |
| at the best rho_M (decay +18, short +30) | −7.70 | **1.2413** |
| the 808 needs **both** | −5.00 … −3.45 | 1.048 … 1.139 |

`n_balances_in_808_box` is **0** for both sources, and 0 for the clipper at every position, drive and asymmetry. The
documented chain traces a locus in the (energy, decay) plane that does not pass through the machine's box, and
neither the inter-band balance nor §10's nonlinearity moves it onto one.

That is a sharper claim than either step 7's or this step's halves, and it is the one the next increment has to
break. It is also a much smaller gap than step 7 reported: 1.3 dB and 0.06 of rho, not 9.4 dB.

---

## 5. Where the prediction was wrong, recorded because it was written first

The module's docstring states the prediction before any number was measured. The energy half was right. The decay
half was wrong in sign, and the reason is worth keeping:

> A quadratic term in an envelope g(t) produces a product that decays as g(t)² — **twice as fast** as the band it
> came from, not slower.

True, and irrelevant, because it reasons only about a band's product with **itself**. The DECAY band's self-product
decays with τ_decay/2 = **190 ms**, which still outlasts the low band's own **100 ms**. So the products that dominate
M late are longer-lived than Ln, not shorter, and rho goes **up**. The algebra was right and the term that mattered
was not the one that got written down.

This is why the clipper is eliminated by *overshoot* rather than by doing nothing, and why the elimination is firm:
a mechanism that moves the right quantity the wrong distance cannot be rescued by a gain.

---

## 6. Wrong-then-right rate: 4, all caught by controls rather than by inspection

Published here because that rate is how a reader calibrates any single figure above.

| # | what was wrong | what caught it |
|---|---|---|
| 1 | `im-known-answer` read **0.35** against a predicted 1.0. The block was right and the **test** was wrong: it drove the clipper at the sweep's own 0 dB, where the tanh argument has unit RMS and the second-order coefficient is not the whole story. A known answer is only known where the expansion holds | the control failed on its first run; `KA_DRIVE_DB` now pins it at −20 dB |
| 2 | `product-law` read **2.83** against a predicted 1.0 — the dB slope of a *power* envelope is −8.686/T and the first version used −20/T. The same family of error as step 7's own wrong-then-right 1 | the control failed |
| 3 | `targets()` **REFUSED** on its first run: the module quoted the 808's weakest rho_M(−10) as 1.048 and the file says 1.0213. Both are right — 1.048 is the weakest in the 2.0 s window, 1.0213 the weakest over all three — and low-tail's own rule is that a comparison is never made across windows. The quote was under-specified | the refusal. It is now window-keyed and asserts both figures |
| 4 | `verdict()` reported **"reaches both targets at `['sum']`"** — from two rows *twelve dB of drive apart*, and it treated "above the top of the 808's range" as a match, crediting a rho of 3.02 against a machine that reads 1.05–1.14 | reading the rows beside the verdict. Both are now permanent controls: a planted matching row must be accepted, two half-matching rows must not be, and an overshoot must not be |

Number 4 is the one worth dwelling on. It is the failure mode this repository keeps producing: **a verdict function
that answers the question it can answer cheaply rather than the one that was asked.** "Does any row reach the target"
is one line; "does any single row land inside both boxes" is five. The first would have reported this step as a
*positive* result.

---

## 7. Against #369's acceptance

| acceptance | state |
|---|---|
| **1. the structure matches the 808's metal circuit** | **advanced, negatively and usefully.** §10's only named candidate for this region — the VCAs' asymmetric clipping — is eliminated at three discrete positions, seven drives and four asymmetries, by overshoot rather than by absence. Taken from §10 and §1.5, as discrete structural choices, not a Q sweep |
| **2. a qualified measurement first** | **met for this question.** 9 named properties, 6 injected defects each verified to move one, 2 asserted blind and verified blind, 39 tests, the generator's own aliasing bounded at 0.018 dB, the chain asserted equal to the committed magnitude model at 3e-13 dB, and the targets re-read from the step-7 artifacts with a refusal when they drift |
| **3. accurate across the knobs** | **not advanced here**, and deliberately: the answer is flat in the knobs because the mechanism under test is structural. Step 7 reports all 25 settings; nothing here changes them |
| **4. preservation · 5. model → RTL → I²S · 6. listening pack** | **not touched.** No kit change, so nothing to preserve and nothing to carry. `tools/ab_808.py` and `tools/ab_808_loud.py` are already committed from an earlier increment |

### Next question, one at a time

**What supplies 1–2.5 kHz with the 808's energy *and* its decay at the same time?** The gap is now 1.3 dB and 0.06 of
rho, not 9.4 dB — small enough that the next candidates are things previously below the noise:

1. **Hh1's realisation.** The shipped kit omits Hh1 entirely (`../README.md` §3) and candidate 3 moved M 3.6 dB the
   wrong way. The locus above is for the *documented* chain; our own realisation is a different locus and the
   comparison between them has not been made with this instrument.
2. **The tone stage's unresolved inter-band balance** (§18, TONE_K1's `peak_db`, 9–18 dB uncertain, #396). The
   balance sweep here spans it, but the tone stage's *shape* for Ht1 and Ht2 is extrapolated outside Figure 9's
   plotted range, and the locus's position depends on it.
3. **The band-pass Q.** §10 infers Q ≈ 6 for both bridged-T filters from the schematic values; the reference marks it
   inferred. A lower Q widens the skirt into M and lengthens nothing, which is the right direction for the energy and
   the wrong one for rho — i.e. it moves along the locus rather than off it, and that is a prediction this
   instrument can check.

Two things that must **not** happen next, both excluded by the numbers above:

- another nonlinearity candidate for this region — clipping overshoots rho by 2–3× at every position and drive that
  supplies the energy;
- quoting step 7's 9.4 dB gap. It is 1.3 dB once the bound and the measurement use the same instrument and the same
  source, and `../low-tail/README.md` §5 should be read with §3 of this document beside it.

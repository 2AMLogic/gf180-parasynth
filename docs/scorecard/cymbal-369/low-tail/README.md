# #369 step 7 — the 1–2.5 kHz band decay, qualified, measured at all 25 settings, and traced to a missing mechanism

Step 5 (`../candidate3/README.md` §6) found that our 1–2.5 kHz energy falls 5–11 dB further than the 808's between
the strike and 50–300 ms, identically in the shipped kit, candidate 2 and candidate 3 — and labelled it **NOT
QUALIFIED**, because it is an energy ratio between two fixed windows and none of `tools/cymbal_bands.BANDS` covers
1–2.5 kHz. #400 asked for the qualified measurement. This is it, plus what it says.

**No kit, RTL or scorer changes here. R1 is unchanged. No candidate is promoted.** Nothing under
`docs/scorecard/cymbal-369/` outside this directory moves, so every number already committed stays reproducible.

Instruments: `tools/cymbal_low_tail.py` (+ `tools/test_cymbal_low_tail.py`, 42 tests) and
`tools/cymbal_m_origin.py` (+ `tools/test_cymbal_m_origin.py`, 13 tests). Results: `low-tail.json`, `m-origin.json`,
both carrying the commit they were produced at and a `sources_dirty` flag, both clean.

---

## 1. Headline

**The 808's 1–2.5 kHz band outlasts its own 3.45 kHz low band at every one of the 25 settings. Ours dies first at
every setting. The instrument's own zero point lies between the two.** And the reason is not a filter we got wrong:
the three-band chain §10 of the reference documents **cannot put that much energy in 1–2.5 kHz at any inter-band
balance** — it is short by 9.4 dB in 891–2828 Hz and 16.6 dB in 891–1782 Hz. Something the reference describes but
does not quantify is supplying it, and §10 names exactly one candidate.

| | rho_M(−10) | rho_Mn(−10) |
|---|---|---|
| 808, the 25 settings | **1.021 – 1.139** | 0.949 – 1.097 |
| the instrument's zero (skirt only, tau_l 0.10–0.60 s) | 0.943 – 1.017 | 0.892 – 0.985 |
| shipped kit | **0.866 – 0.871** | 0.878 – 0.883 |
| candidate 3 | **0.884 – 0.894** | 0.781 – 0.791 |

`rho(d) = T_M(d) / T_Ln(d)`, the ratio of the times the two bands' floor-subtracted Schroeder curves take to fall
`d` dB. Above 1 means 1–2.5 kHz outlasts the low band's own 3.45 kHz peak; below 1 means it dies first.

---

## 2. Why the measurement can be believed, and where it cannot

### The bands, frozen before anything was judged

| band | range | what it is |
|---|---|---|
| M | 891–2828 Hz | exactly the union of the five 1/3 octaves step 5's finding reports (1.0, 1.26, 1.59, 2.0, 2.5 kHz) |
| Mn | 891–1782 Hz | the lowest three of those — furthest from the low band's 3.45 kHz peak and from Hh1's 2.5 kHz corner |
| Ln | 2900–4100 Hz | `cymbal_bands.BANDS["Ln"]`, imported unchanged so it cannot drift from the frozen definition |

**Why a ratio.** Taken inside one record it is invariant to that record's gain, to peak normalisation and to the
listening pack's loudness matching — so the 808 and our renders can be compared with no level rule linking them,
which the level rule's known 2.7 dB error makes impossible for an absolute comparison. Asserted, not assumed
(`test_rho_is_invariant_to_record_gain`).

**Why no line is fitted.** The quantity is a crossing time, so it is defined whether or not the decay is one
exponential — and the 808's 1–2.5 kHz is not (`cymbal_bands.band_decay` refuses its late T20 at a 1.5 dB residual).

### rho's zero point is **not 1.0**, and that is measured

Sweeping `tau_l` over 0.10–0.60 s on records whose *entire* 891–2828 Hz content is the 3.45 kHz Q 6 band-pass's own
skirt — records with, by construction, no separate low component at all:

| band | rho(−5) | rho(−10) | rho(−20) |
|---|---|---|---|
| M | 0.914 – 1.012 (median 0.968) | 0.943 – 1.017 (median 0.970) | 0.957 – 1.008 (median 0.993) |
| Mn | 0.847 – 0.937 (median 0.900) | 0.892 – 0.985 (median 0.919) | 0.913 – 0.995 (median 0.956) |

Mn's zero sits near 0.92 at −10 dB and near 0.87 at −5. The cause is physical, not a defect: the skirt's spectrum
falls steeply below 3.45 kHz, so Mn's copy of the low band's envelope is more noise-limited and its curve crosses
each depth slightly early. **A reading of rho_Mn = 0.92 is therefore the instrument's zero, not a finding.** Every
downward claim below is a departure from this baseline at the same tau. This is what made wrong-then-right 5 (§6).

### The three frozen windows, and why there are three

A Schroeder curve is integrated from the END of the record, so depths are not comparable between records of
different length. The Fischer set does not have one length — it is a function of the DECAY code alone (all 25
checked, `test_the_full_decay_to_length_map_is_the_one_the_docstring_states`):

| DECAY | 00 | 25 | 50 | 75 | 10 |
|---|---|---|---|---|---|
| record length | 1.501 s | 2.001 s | 2.501 s | 3.501 s | 4.001 s |

Every record is trimmed to a common window and one that cannot supply it REFUSES visibly. Being long enough is
necessary and not sufficient: at DECAY 10 and 75 the **low band — the reference band rho divides by — is still
ringing at 2.0 s** (CY5010's Ln has 12.5 dB of end margin against the 15 dB the truncation guard requires), so Ln
refuses and rho refuses with it at every depth.

| window | records long enough | rho answers at | settings |
|---|---|---|---|
| 1.5 s | 25 | DECAY 00, 25 | 10 |
| 2.0 s | 20 | DECAY 25, 50 | 10 |
| 3.5 s | 10 | DECAY 75, 10 | 10 |

**Their union is all 25 settings and no two of them manage it** (dropping 3.5 s leaves 15; dropping either other
leaves 20). `coverage()` computes this and `test_the_three_frozen_windows_cover_every_setting_and_no_two_of_them_do`
asserts it both ways, so a redundant window cannot sit here unnoticed. A comparison is never made across windows.

### Analysis-filter rejection, because #101 was window leakage

The band-pass is `cymbal_bands._bp` unchanged — 4th-order Butterworth run zero-phase, so the effective response is
|H|², and that square is what is reported rather than the one-way response:

| band | at 3450 Hz | at 7100 Hz | at 10320 Hz |
|---|---|---|---|
| M | −24.6 dB | −91.3 dB | −125.8 dB |
| Mn | −85.1 dB | −147.0 dB | −180.8 dB |

M's −24.6 dB at the low band's own peak is the honest weak point of the wide band, and it is why **Mn is reported
beside M everywhere**: Mn puts 60 dB more between itself and 3.45 kHz, so a conclusion holding in M and *not* in Mn
is analysis leakage. The headline holds in both.

### Controls: 8 properties × 6 defects, plus one asserted blind

```
  clean measurement
    rho-tracks   PASS 1.2663     rho-inverts  PASS -0.1186    skirt-blind      PASS 0.9961
    edt-known    PASS 1.0246     depth-monotone PASS 1.957    refuse-truncated PASS None
    refuse-short-record PASS     floor-subtracted PASS 1.0227

  defect             rho-tracks  rho-inverts  skirt-blind  edt-known  depth-mono  refuse-trunc  refuse-short  floor-sub
  FORWARD_INTEGRAL   REFUSED
  WIDE_M               MOVED       MOVED
  SWAP_BANDS           MOVED       MOVED                                                                        MOVED
  NO_TRIM                                                                            MOVED        MOVED         MOVED
  NO_TRUNC_GUARD                                                                     MOVED
  NO_FLOOR_SUB                                                                                                  MOVED
  SCALE_2X            (blind by construction, verified blind in all eight)
```

**Both directions of rho are properties**, and that is deliberate: the sign our own renders read is the *downward*
one, and a suite with only `rho-tracks` would have carried no control at all on it. Two defects move `rho-inverts`
specifically, so it is not decoration. `main()` runs this matrix BEFORE measuring any record and refuses to report a
result if it does not pass; `summarise()` refuses again at the point of use, because the console refusal is gone by
the time a reader picks the JSON up.

### The instrument's detection floors, in both directions

What a measured departure is *worth*, so a reading can be converted into a size (planted component's energy relative
to the M band's own):

| planted M energy | rho_M(−10), slower component | Δrho_M vs baseline, faster component | Δrho_Mn |
|---|---|---|---|
| +0.0 dB | 0.996 | 0.000 | 0.000 |
| +0.1 dB | 1.008 | −0.008 | −0.051 |
| +0.35 dB | 1.104 | −0.031 | −0.130 |
| +0.71 dB | — | −0.079 | −0.246 |
| +1.18 dB | 1.266 | −0.119 | −0.330 |
| +3.53 dB | — | −0.334 | −0.567 |

**Mn is about 3× more sensitive downward than M**, because M is dominated by the skirt it shares with the low band
and Mn is not. Reading the table backwards: the 808's rho_M excess (median 1.048–1.096) is worth **+0.15 to +0.35 dB**
of M-band energy in a component decaying ~2.5× slower, and our shipped kit's deficit (0.871 against a 0.970
baseline, −0.099) is worth **about +0.95 dB** of extra fast-decaying M energy. Both are under 1.5 dB — which is
precisely why the band-balance measurements that came before (H−L, the 1/3 octaves) could not resolve this. It is a
*timing* difference far below the balance tolerance.

---

## 3. Every setting, reported

rho in the window that answers it. All 25 are here; five appear twice because two windows answer them.

| setting | window | rho_M(−5) | rho_M(−10) | rho_Mn(−5) | rho_Mn(−10) |
|---|---|---|---|---|---|
| CY0000 | 1.5 s | 1.186 | 1.087 | 1.130 | 1.047 |
| CY0010 | 3.5 s | 1.041 | 1.021 | 0.963 | 1.008 |
| CY0025 | 1.5 s | 1.100 | 1.051 | 0.990 | 0.949 |
| CY0025 | 2.0 s | 1.100 | 1.053 | 0.992 | 0.949 |
| CY0050 | 2.0 s | 1.065 | 1.055 | 0.992 | 0.991 |
| CY0075 | 3.5 s | 1.074 | 1.056 | 1.020 | 1.064 |
| CY1000 | 1.5 s | 1.316 | 1.130 | 1.210 | 1.097 |
| CY1010 | 3.5 s | 1.140 | 1.072 | 1.135 | 1.034 |
| CY1025 | 1.5 s | 1.284 | 1.137 | 1.240 | 1.075 |
| CY1025 | 2.0 s | 1.281 | 1.139 | 1.237 | 1.082 |
| CY1050 | 2.0 s | 1.145 | 1.069 | 1.085 | 1.046 |
| CY1075 | 3.5 s | 1.197 | 1.084 | 1.036 | 1.010 |
| CY2500 | 1.5 s | 1.184 | 1.117 | 1.037 | 1.065 |
| CY2510 | 3.5 s | 1.076 | 1.040 | 1.060 | 1.021 |
| CY2525 | 1.5 s | 1.053 | 1.063 | 0.974 | 0.965 |
| CY2525 | 2.0 s | 1.053 | 1.062 | 0.973 | 0.967 |
| CY2550 | 2.0 s | 1.071 | 1.048 | 1.038 | 1.007 |
| CY2575 | 3.5 s | 1.081 | 1.035 | 1.038 | 1.010 |
| CY5000 | 1.5 s | 1.192 | 1.104 | 1.068 | 1.014 |
| CY5010 | 3.5 s | 1.104 | 1.038 | 1.062 | 0.997 |
| **CY5025** (D14A anchor) | 1.5 s | 1.215 | 1.124 | 1.116 | 1.071 |
| **CY5025** | 2.0 s | 1.217 | 1.120 | 1.116 | 1.069 |
| CY5050 | 2.0 s | 1.067 | 1.049 | 1.049 | 1.032 |
| CY5075 | 3.5 s | 1.135 | 1.068 | 1.017 | 1.022 |
| CY7500 | 1.5 s | 1.159 | 1.088 | 1.082 | 1.009 |
| CY7510 | 3.5 s | 1.082 | 1.037 | 1.064 | 0.993 |
| CY7525 | 1.5 s | 1.153 | 1.076 | 1.072 | 1.015 |
| CY7525 | 2.0 s | 1.153 | 1.077 | 1.071 | 1.016 |
| CY7550 | 2.0 s | 1.143 | 1.054 | 1.068 | 1.014 |
| CY7575 | 3.5 s | 1.155 | 1.066 | 1.076 | 0.988 |
| **shipped** | 1.5 / 2.0 / 3.5 s | 0.794 / 0.790 / 0.790 | 0.870 / 0.871 / 0.866 | 0.790 / 0.798 / 0.796 | 0.883 / 0.878 / 0.883 |
| **candidate 3** | 1.5 / 2.0 / 3.5 s | 0.827 / 0.824 / 0.819 | 0.894 / 0.884 / 0.886 | 0.692 / 0.689 / 0.694 | 0.791 / 0.785 / 0.781 |

**30 of 30** setting-measurements put the 808's rho_M(−10) above the instrument's skirt baseline maximum (1.017). In
Mn — the leakage-proof band — **26 of 30** do, and the four that do not are CY0025 and CY2525 measured in each of
the two windows that answer them (0.949, 0.949, 0.965, 0.967 against a baseline top of 0.985). That is the honest
weaker figure rather than a rounding, and it is two settings rather than four independent failures.

**CY2500 is D14B's holdout** and it is not special: 1.117 in M and 1.065 in Mn at −10 dB, inside the pack on both. Both our renders sit **below the baseline minimum** in every band and
every window.

**Knob tracking.** rho is roughly flat across TONE and DECAY: 1.021–1.139 over the whole grid, with no monotone
trend in either knob. That is itself informative — whatever supplies the slow 1–2.5 kHz component is **structural,
not knob-dependent**, so the repair is a structural one and not a knob-law change.

**Candidate 3 is worse here, not better.** It moves rho_M up by 0.013 (0.871 → 0.884) and rho_Mn **down by 0.093**
(0.878 → 0.785), i.e. further from the machine in the leakage-proof band. Its own README already declined to promote
it on the tail; this is the qualified measurement agreeing, and adding that Mn got worse.

---

## 4. The two confounds, bounded rather than assumed away

### The recordings' noise floor: excluded

A Schroeder curve is a floor-**subtracted** backward integral, so a residual floor is the most plausible way an
apparatus could manufacture a longer apparent tail for one record than another. The direction of the test is decided
by measurement, and it is the opposite of what was assumed:

| | M-band floor, dB re that band's peak | rho_M(−10) |
|---|---|---|
| our shipped render | **−33.87** | 0.8709 |
| 808 CY5025 | **−60.88** | 1.1202 |
| 808 CY5025 + noise to match OUR floor (−33.87) | −33.87 | **1.1217** |
| 808 CY5025 + 10 dB more noise still (−23.85) | −23.85 | **1.1364** |

**The recording is 27 dB cleaner in this band than the thing we make.** Adding our own floor to the 808's record
moves its rho by 0.0015 — 0.6 % of the 0.2493 gap, and in the *wrong* direction. The floor is not the explanation.
(This control was broken and silently reported the same number three times; see §6, wrong-then-right 6.)

### The strike onset: bounds at most a quarter of our deficit

A broadband click is wider than Ln (1200 Hz) and lands harder in M (1937 Hz), so it drags rho **down** — it can only
ever explain a rho below 1, never one above. And it cannot do so invisibly, because the same measurement reports each
band's first-5 ms energy share. Calibrated:

| planted click, dB re the record's first-second energy | onset excess M−Ln | rho_M(−10) |
|---|---|---|
| none | −3.87 dB | 0.9961 |
| −20 | −0.85 dB | 0.9888 |
| **−13** | **+3.14 dB** | **0.9716** |
| −10 | +5.22 dB | 0.9444 |
| −6 | +7.82 dB | 0.8892 |

Our shipped render's measured onset excess is **+3.24 dB**, which the sweep says costs about **0.025** of rho. Our
deficit from the baseline median is 0.099, so the onset bounds **at most a quarter of it**, and 10 % of the 0.249
gap to the 808 at CY5025. The 808's own onset excess is −0.96 to −10.01 dB at most settings, i.e. *less*
onset-concentrated than the synthetic reference, so the confound cannot be helping the 808's side either.

Both columns of that table are asserted monotone, because it is read **backwards** — a record's measured onset
excess is looked up to bound the deficit a click could explain, and that lookup has to be single-valued.

---

## 5. Where it comes from: not a filter we got wrong

`tools/cymbal_m_origin.py`, arithmetic on the two digitised W14b artifacts. For each band, the documented cascade —
its own Q 6 band-pass, its Sallen-Key high-pass, its tone-stage path and the LEVEL stage — puts its energy this far
below its energy in Ln:

| band | M re Ln (808's cascade) | M re Ln (candidate 3's) | tone stage's contribution | Mn re Ln |
|---|---|---|---|---|
| low | −13.10 dB | −13.05 dB | +0.05 dB | −31.92 dB |
| decay | −13.54 dB | −12.74 dB | **+0.80 dB** | −30.30 dB |
| short | −16.23 dB | −16.26 dB | −0.03 dB | −36.33 dB |

**A mix cannot beat the best of its parts.** For positive weights, `(Σ a·X)/(Σ a·R) ≤ maxᵦ(Xᵦ/Rᵦ)` — a weighted
mediant never exceeds the largest ratio it mixes. So **no** inter-band balance, including the one §10 leaves
unresolved (TONE_K1's `peak_db`, 9–18 dB uncertain, #396), can put the documented chain's M above **−13.10 dB re Ln**
or its Mn above **−30.30 dB**. The lemma is checked against a brute-force sweep of the balance, and against 200
random cases whose answer is known by hand.

Against the machine, over the first second, same instrument:

| | M re Ln | Mn re Ln |
|---|---|---|
| what the documented chain can reach, at best | −13.10 dB | −30.30 dB |
| 808, 20 settings | −5.00 … −3.45 (median **−3.73**) | −15.20 … −13.05 (median **−13.75**) |
| **the chain is short by** | **9.36 dB** (8.10 dB at the closest setting) | **16.55 dB** (15.11 dB at the closest) |
| shipped kit | −3.29 | −8.91 |
| candidate 3 | −7.34 | −19.96 |

**20 of 20 settings exceed the bound.** This is not a discrepancy inside anybody's tolerance — it is a missing
mechanism. And the same chain cannot reproduce the *decay* either: sweeping the balance from −12 to +30 dB and
measuring the mix with the same instrument, rho_M(−10) stays between 0.89 and 0.97 — it never even clears the skirt
baseline, let alone the 808's weakest 1.048, and the tool **refuses to name a level** rather than extrapolating. In
Mn it refuses outright, because the documented chain puts Mn 30 dB below Ln, 8 dB under anything the instrument has
been qualified on. Both halves point the same way.

Two further results, neither expected:

- **The shipped kit's 1–2.5 kHz *energy* is already about right** (−3.29 against the machine's −3.73, a 0.45 dB
  error), and candidate 3 moved it **3.6 dB away** in M and **6.2 dB away** in Mn. What is wrong in both is the
  decay, not the level. This is why a decay instrument was needed.
- **The tone stage's shape is not the difference**, which refutes the hypothesis that looked most obvious. §10
  records, off Figure 9, that the DECAY band's tone path Ht2 is a 2-pole band-pass **peaking at 972 Hz** (real poles
  610 and 1549 Hz — inside Figure 9's plotted range for Ht2, so measured rather than extrapolated), and the DECAY
  band carries the longest of the three envelopes. So the 808 appears to route its longest envelope into the middle
  of the M band, while candidate 3 deliberately drops that pole. Right sign, right story — and worth **0.80 dB**,
  because each band's own filters roll off so steeply below 3 kHz that the tone stage barely reaches M at all. A
  candidate built on it would have cost a render cycle to find out.

### The one thing §10 describes and does not quantify

> "The VCAs' asymmetric clipping is what makes the sum sizzle; a linear VCA gives a flat, chorus-like tone."

Six square waves beating through an asymmetric nonlinearity put intermodulation products at **difference**
frequencies, which is where 9–17 dB of unexplained 1–2.5 kHz energy would have to come from. **This is a hypothesis,
not a result** — this step deliberately does not test it (one question at a time). What it establishes is that the
next candidate should not be another filter.

**A reference gap, worth recording as such.** §10's chain, taken at face value, is 9–17 dB short in 1–2.5 kHz at any
balance. The reference is not wrong about the filters; it is incomplete about what else contributes there, and it
says so itself in one qualitative sentence. Filed as a follow-up rather than patched here.

---

## 6. Wrong-then-right rate: 7, all caught by controls rather than by inspection

Published here because that rate is how a reader calibrates any single figure above.

| # | what was wrong | what caught it |
|---|---|---|
| 1 | `t_edt` said a Schroeder curve falls 2·8.686/τ dB/s, so every planted time was half what it should be | `edt-known` failed on the first run |
| 2 | three of six injected defects turned nothing red (`NO_TRIM`, `NO_TRUNC_GUARD`, `NO_FLOOR_SUB`), and a separate noise-floor guard could never fire before the end-margin guard | the matrix; the cases were rebuilt and the redundant guard **deleted** rather than kept as a control that cannot fail |
| 3 | the onset-confound control was a single-sample impulse swept in *amplitude*; it carries so little energy next to a 1 s strike that rho moved by **0.0001** over the whole sweep | the control could not fail. Now a 0.2 ms burst specified in *energy* |
| 4 | "the Fischer CY files are 2.013 s" — they are 1.501–4.001 s, set by the DECAY code. A single 2.0 s window would have silently refused five settings | writing the corpus length down as a test rather than as prose |
| 5 | the downward paired negative asserted rho_M < 0.92 and read 0.944 — a FAIL whose *assertion* was wrong, because it measured distance from 1.0 and 1.0 is not this instrument's zero | the test failed; the skirt-baseline sweep explained why |
| 6 | the noise-floor control printed **three identical rows** (−33.87 dB, 0.8709, three times). Its premise was inverted — it assumed our renders noiseless and the recordings noisy, so `match_floor` was asked for a target 27 dB below the record's own floor, and its bisection converged on zero amplitude and returned the record unchanged | the three identical rows. `match_floor` now REFUSES an unreachable target, and the refusal is a permanent control paired with a positive that asserts the record actually **changed** |
| 7 | the origin probe's first mixture normalised each band to its *own* energy in Ln (asserting all three contribute equally there) and generated the overlapping M and Mn separately (double-counting 891–1782 Hz). The sweep came out **backwards** — rho falling 1.22 → 0.98 as the DECAY band got louder | the sign. The numbers themselves looked entirely plausible |

An eighth, caught in the same session and fixed rather than counted here because it never produced a quoted number:
the probe's envelope **stepped** from 0 to 1 at t = 0, and in a band holding almost no steady content that broadband
click was all the band held — reported as rho_Mn = 0.006. §10's own attack smoother (Q19, τ ≈ 0.1 ms) is now in the
mixture, and a paired test asserts it is load-bearing.

---

## 7. What this does and does not settle, against #369's acceptance

| acceptance | state |
|---|---|
| 2. a qualified per-band energy and decay measurement, with known answers, must-fail controls, and a check against the recordings, **before** any candidate is judged | **done for the 1–2.5 kHz decay.** 8 properties, 6 defects each verified to move one, 1 asserted blind and verified blind, 55 tests across the two modules, the #101 leakage guard stated as numbers in both bands, both confounds bounded, and the zero point measured rather than assumed |
| 3. report every setting; freeze the development subset; confirm on untouched settings | **every one of the 25 is reported** (§3), in the window that answers it, via three frozen windows whose union covers the grid and none of which is redundant. D14A's anchor CY5025 and D14B's holdout CY2500 both appear and neither is special |
| 1. the structure matches the 808's metal circuit | **advanced, negatively and usefully.** The tone stage's shape is eliminated as the cause (0.80 dB), and the documented three-band chain is eliminated as a *sufficient* structure (9.4/16.6 dB short at any balance) |
| 4. preservation; 5. model → RTL → I²S; 6. listening pack | **not touched here.** No kit change, so nothing to preserve and nothing to carry |

### Next question, one at a time

**Does the VCAs' asymmetric clipping supply 1–2.5 kHz with a slower decay than the low band's?** It is the only
element §10 names for this region and does not quantify, and it is testable the same way this step was: put the
documented nonlinearity in the probe's mixture, measure rho and the M/Ln energy with the *same* instrument, and see
whether it crosses the bound in §5 and the skirt baseline in §2. If it does not, the reference is missing something
larger than a clipper.

Two things that must **not** happen next, both of which this step's numbers exclude:

- another filter-magnitude candidate for this region — the tone stage's shape is worth 0.80 dB against a 9.4 dB gap;
- crediting a candidate with fixing a 1–2.5 kHz *energy* error. The shipped kit's is 0.45 dB. Candidate 3 made it
  3.6 dB worse while barely moving the decay.

# #369 step 9 — the TONE knob is VR4's wiper, and the schematic predicts the machine's own TONE law

Step 8 (`../vca-clip/README.md`) ended by eliminating §10's asymmetric clipping and naming three candidates for
the 1–2.5 kHz gap. This step does **not** ask any of them. It asks the question #369 acceptance 3 needs answered
before any of them can be judged across the knobs, and which no increment of this chain has asked:

> **Does VR4's wiper position, solved from SN p.13, predict the 808's own H − L versus TONE — with the wiper
> fraction taken from the pot's linear taper rather than fitted to the recordings?**

**Yes.** With `alpha = TONE / 100` read off the component marking and **one** inter-band balance shared by all 25
settings, the nodal solution of the tone network reproduces the machine's H − L versus TONE to **0.33 dB worst
case** against a 3.0 dB bound stated in advance, over a measured span of **7.1–8.1 dB per DECAY column**. The
inverted wiper law, no tone network at all, and the low band on the top rail each have the *same* two free numbers
and miss by **5.5, 5.5 and 6.9 dB**.

**No kit, RTL or scorer change. R1 is unchanged. No candidate is promoted.** Nothing already committed under
`docs/scorecard/cymbal-369/` moves.

Instrument: `tools/cymbal_tone_knob.py` (+ `tools/test_cymbal_tone_knob.py`, 47 tests). Record: `tone-knob.json`,
carrying the commit it was produced at and a `sources_dirty` flag, clean.

---

## 1. Why this question, and why it could not be asked of our own render

`../README.md` §4 records that `model/test_discrimination.kit_at`'s CY TONE law is **closed-loop**: `_cy_hi_amp`
solves, in closed form, for the 808's own measured 5–13 kHz / 2–5 kHz ratio at each TONE position. A render whose
band ratio is calibrated to the reference cannot then be compared against the reference **on band ratio**. That is
`docs/failure-modes.md`'s root cause wearing a knob: an estimator calibrated on the thing it is measuring.

So the TONE law had to come from somewhere else, and #390/PR #417 had just put it within reach without saying so.
`tools/tone_stage_schematic.py` solves the tone network from SN p.13 by nodal analysis, **parameterised in the
wiper fraction `alpha`**, and explicitly leaves the knob mapping out of scope:

> `alpha` is a wiper FRACTION, not W14b's own knob parameter `k` — the two need not be linearly related, and
> nothing here claims they are.

Nothing there claims it, but the schematic states the part: **VR4 is "20K(B)"**, and a B taper is linear, so the
wiper fraction *is* the fraction of rotation. `alpha = TONE / 100` is therefore a prediction read off a component
marking, with **no parameter fitted to the recordings at all** — which is what makes comparing it against the
recordings an external check rather than a consistency check.

## 2. What is measured, and against what

| | |
|---|---|
| quantity | **H − L** — `cymbal_bands.BANDS`' L (2–5 kHz) and H (6–14 kHz), the qualified instrument's own bands |
| why a ratio | `run_case.prepare` peak-normalises each file, so an absolute band energy is not comparable across settings. H − L is a ratio inside one file and survives both that and any per-file capture gain |
| windows | **strike** 0–20 ms, **tail** 100–300 ms, **1 s** 0–1000 ms, frozen with their reasons in `WINDOWS` |
| settings | all 25, every DECAY column reported separately; no averaged score |
| bound | **3.0 dB**, `docs/audio-distance-metrics.md`'s board tolerance for band tilt — the same constant `cymbal_tone_realisation.SHAPE_BOUND_DB` uses, asserted equal to it so the two cannot drift |
| free parameters | **two**: the decay and short bands' drive relative to the low band (§10's Q16/Q17/Q18, the term `../balance/README.md` REFUSED). ONE balance for all 25 settings, not one per setting |

The 1 s window is read from the committed `../fischer.json`, so the headline property runs in CI **without** the
~30 MB corpus; the strike and tail windows are measured from the WAVs and REFUSE, rather than answer, when it is
absent. The re-measured 1 s window reproduces the committed artifact to **0.0009 dB** at all 25 settings, which is
what makes the other two windows statements about the same recordings the qualified instrument already measured.

## 3. The result

`alpha = TONE/100`, one balance (decay **+10.0 dB**, short **+11.5 dB** relative to the low band):

| DECAY | measured span | worst | residual at TONE 0 / 25 / 50 / 75 / 100 |
|---|---:|---:|---|
| 00 | 7.33 | **0.26** | −0.26 +0.03 +0.00 +0.02 +0.10 |
| 25 | 8.09 | **0.24** | +0.15 −0.01 +0.00 −0.15 −0.24 |
| 50 | 7.73 | **0.33** | −0.29 +0.15 +0.00 +0.17 −0.33 |
| 75 | 7.14 | **0.28** | −0.28 −0.13 +0.00 −0.05 +0.27 |
| 100 | 7.28 | **0.30** | −0.30 −0.07 +0.00 −0.08 +0.12 |

(TONE 50 is the anchor every difference is taken against: the D14A setting's TONE, inside `cymbal_bands.DEVELOPMENT`.
CY2500, D14B's holdout, is in the DECAY 00 row and is not special — the residual is flat across the whole grid,
which is what a *structural* law predicts and a fitted one would not.)

And in the two windows the corpus is needed for, each with its own single balance:

| window | worst | balance (decay / short, dB re low) |
|---|---:|---|
| strike 0–20 ms | 1.79 | +15.0 / +22.0 |
| tail 100–300 ms | 0.70 | +36.0 / +33.0 |
| 1 s | **0.33** | +10.0 / +11.5 |

## 4. Why the small residual is not, by itself, the evidence — and this is the more useful half

**An early draft of this file said it was.** A control written to break the claim did not break the way it was
meant to, and measuring what it did instead is §6's wrong-then-right 6. `shape_audit()` fits the *same two free
numbers* to eight stated five-point curves:

| curve | worst | reachable inside 3 dB? |
|---|---:|---|
| the machine's own | **0.20** | yes |
| a straight ramp of the same span | 0.34 | **yes** |
| **flat — no TONE dependence at all** | 0.38 | **yes**, at a decay-dominated balance (+36 / −30 dB) |
| concave (all the rise in the first step) | 0.82 | yes |
| convex (all the rise in the last step) | 1.76 | yes |
| **step at the middle** | 4.51 | no |
| **the machine's curve REVERSED** | 5.47 | no |
| **a V** | 7.75 | no |

So the family is flexible across monotone rises: "0.33 dB over a 7.3 dB span" is *consistent* with the circuit and
is not on its own decisive. What is decisive is what the same freedom **cannot** reach — three of the eight shapes,
and on the real data the three structural alternatives:

| structure, each fitted with the SAME two free numbers | worst | verdict |
|---|---:|---|
| **alpha = TONE/100, rails as #417 resolved them** | **0.33** | the prediction |
| the low band on the top rail | 6.95 | excluded |
| alpha = 1 − TONE/100 (the wiper turned the other way) | 5.47 | excluded |
| no tone network at all (TONE does nothing) | 5.46 | excluded |
| Ht2 and Ht3 exchanged | 0.31 | **NOT excluded — blind by construction, see §5** |

## 5. A second, independent confirmation: a decay that moves because an energy balance moved

Nothing in the machine lets TONE change any band's own RC. So the three-band structure makes two predictions about
the *decays* the committed artifact already holds, and they pull in opposite directions:

| | TONE 0 → 100, per DECAY column | bound |
|---|---|---|
| **H's EDT10 must FALL** — TONE shifts H's composition toward the 20 ms short band | ratio 1.43 / 1.65 / 1.92 / 2.16 / 2.36 | falls in 5 of 5 |
| **the low band's own EDT10 must NOT move** — TONE cannot touch its RC | spread 9.7 / 5.7 / 3.1 / 2.5 / 3.5 % | ≤ 50 %, `run_case.TOLERANCE_POLICY`'s time tolerance |

The ratio of the two is the property that carries the verdict (`h-edt-falls-with-tone`, weakest column **4.4×**),
because a bare "does it fall" is not discriminating: the low band drifts ~10 % down the TONE column too, and the
paired control that reads H's trend off the low band *passed* until the property became a contrast. Wrong-then-right 5.

**Consistent with, and not a measurement of:** the fitted balance's short-minus-decay term is +7.0 dB in the strike
window, +1.5 dB at 1 s and −3.0 dB in the tail — the ordering an envelope of 20 ms against one of up to 380 ms
requires. It is reported rather than claimed because the tail window's balance sits at the edge of the swept grid,
so its value is not resolved even though its ordering is.

## 6. What this does NOT resolve, each with its number

1. **The exact shape of `alpha(TONE)`.** Seven mappings swept: linear **0.33**, upper-half 0.38, sqrt 0.59, square
   0.75, lower-half 2.20, fixed 5.46, inverted 5.47. Five of the seven are inside the bound. Linear is the best
   *and* is the one the component marking predicts — but it is not the only one the recordings admit, and the
   margin over `upper-half` is 0.05 dB.
2. **The Ht2/Ht3 rail assignment.** Exchanging them reads 0.31 dB against the prediction's 0.33: **blind by
   construction**, because both bands share the 7.1 kHz band-pass and both land in H, so the fitted balance absorbs
   the swap exactly. Asserted blind in `BLIND_PAIRS` and verified blind against every alpha-law property.
   #417's fit against Figure 9 is what resolves it; this instrument cannot, and says so rather than borrowing the
   confidence.
3. **The inter-band balance itself.** Inside 3.0 dB the admitted region spans the **whole swept grid** (−30 to
   +36 dB) in both absolute weights; only their difference is constrained, to [−9.0, +8.0] dB. **This does not lift
   the balance refusal of #396/#414**, whose blocking factor is the VCA drives' absolute levels.
4. **Anything about our own cymbal.** No render of ours appears anywhere in this step. What it delivers is the law a
   future candidate must be driven by instead of the closed-loop one in §4 of `../README.md`. That is the follow-up
   named below, and it is tracked by **#369 itself** (§5 of `../README.md`, "Next step") — *not* by a separate issue,
   which this sentence said in an earlier draft. `../README.md` records what that costs: a blocker cited four times
   as "#371" when #371 is a merged pull request, so the thing gating acceptance was tracked by nothing until it
   became #413. A write-up that says "filed" had better name the number.

## 7. The controls

`python3 tools/cymbal_tone_knob.py --check` — 12 properties, every injected defect's row, and the blindness
assertions verified blind. It needs no corpus and runs in 4.8 s (measured; 9.2 s where the
corpus is present and the two gated rows are actually decided).

| injected defect | properties it turns red |
|---|---|
| `NO_TONE_NETWORK` | `tone-exact`, all three `alpha-law-*`, `tone-load-bearing`, `family-selective` |
| `ALPHA_INVERTED` | all three `alpha-law-*`, `orientation-decisive`, `family-selective` |
| `RAIL_LOW_ON_TOP` | `tone-exact`, all three `alpha-law-*`, `low-band-rail`, `family-selective` |
| `RAIL_SWAP_HT2_HT3` | `tone-exact`, `orientation-decisive`, `low-band-rail` — and **verified blind** to all three `alpha-law-*` and to `tone-load-bearing` |
| `EDT_BAND_SWAP` | `low-decay-tone-invariant`, `h-edt-falls-with-tone` |
| `WRONG_ANALYSIS_BANDS` *(needs the corpus)* | `artifact-binds` — and **verified blind** to all three `alpha-law-*` |
| `SHORT_RECORD` *(needs the corpus)* | `artifact-binds`, `floor-margin`, `alpha-law-tail` |
| `SOURCE_FLAT` | **blind by construction, verified blind in all 12.** Step 8 measured the source tilt worth 4.6 dB on an *absolute* band energy; on this *relative* quantity it is worth 0.02 dB |

The two corpus-gated rows report **NO VERDICT** where the WAVs are absent, which is not a pass. Be exact about
what that buys, because a draft of this line was not: the no-corpus invocation still **exits 0** with those two
rows undecided, so the CI job is not what enforces them. `--check --require-corpus` is, and `make
reference-integration` is the gate that passes it — added here, together with the CLI flag itself, which was
described in this file and in the module docstring and which **`main()` never read**. Wrong-then-right 7: the
escalation both documents promised was unreachable, and `test_require_corpus_is_reachable_from_the_command_line`
is the known answer that fails if argparse ever stops wiring it — the same tree and the same absent corpus,
exit 0 without the flag and exit 1 with it.

Known answers (`tools/test_cymbal_tone_knob.py`): a planted balance reads back to the grid step at a residual of
1e-6 on a curve spanning more than twice the bound; a planted **inverted** law is rejected; the family's reach is
pinned in both directions (three shapes unreachable, two uncomfortable ones reachable); every alternative is
asserted to be an involution of the configuration, which is the mechanism that makes a defect and an alternative
compose at all; and five refusal paths are exercised — a missing setting, a refused setting, an absent artifact, a
window that does not fit its record, and a record too short to carry both the window and its floor estimate.

## 8. Wrong-then-right rate: 7, all caught by controls or by the gate, none by inspection

Published here because that rate is how a reader calibrates any single figure above.

| # | what was wrong | what caught it |
|---|---|---|
| 1 | `h-edt-falls-with-tone` demanded **strict** monotonicity. The machine does not do that — four of five columns have one 3–5 % wiggle — so the first gate was unsatisfiable, the thing CLAUDE.md says is worse than no gate | running the gate before committing it |
| 2 | the alternatives were applied as **absolute** settings rather than as transformations of the instrument's configuration, so injecting `NO_TONE_NETWORK` silently dropped the "no-tone-network" alternative's own flat network. The property went red **for the wrong reason** and read as a catch — rule 5's condition 3 | the defect matrix: `WRONG_ANALYSIS_BANDS` turned `tone-load-bearing` red, which no band shift can legitimately do |
| 3 | the floor guard compared each window against a **same-length window at the record's end**. On a 1.501 s record (every DECAY 00 setting) the 1 s window's "floor" is 0.5–1.5 s — still full of the cymbal's own tail — so the guard compared the signal against itself and reported a 14.7 dB margin for a recording whose floor is 66 dB down | the property failed, at 14.69 against a 15.0 bound, and the failure was in the apparatus rather than the data. Now the last 100 ms scaled to the window, `cymbal_bands.band_decay`'s own convention, and a permanent control that computes both |
| 4 | trends were read down `cymbal_bands.CODES`, which is in **filename** order — and "10" means 100 %, so TONE = full sat second and a falling quantity reported a **109 % local rise** | the same property failing, with a number too large to be a wiggle |
| 5 | `h-edt-falls-with-tone` asked only whether H's EDT10 falls. Its paired control — reading the trend off the **low** band instead — **passed**, because the low band drifts ~10 % down the TONE column too | the defect matrix: `EDT_BAND_SWAP` moved one property where it should move two. The property is now the contrast between the two bands |
| 6 | **the headline was overstated.** "0.33 dB over a 7.3 dB span, with two free numbers" was written as decisive. A test written to show a flat TONE law is rejected found the family reaches one, at a decay-dominated balance | that test failing. `shape_audit()` now measures the family's reach in both directions and §4 is the honest version: the residual is consistent, the alternatives are what is decisive |
| 7 | **`--require-corpus` did not exist.** This file and the module docstring both described it as what makes the two corpus-gated controls binding; `main()` had no such argument and never passed one, and the target they named (`make reference-integration`) did not run this file at all. Two controls documented as enforced were enforced nowhere, and the tool's `--check` reported them as NO VERDICT while exiting 0 | reading the shipped `main()` against its own docstring — the *only* one of the seven that no control caught, which is the argument for the flag now having one (`test_require_corpus_is_reachable_from_the_command_line`) and for `_refs_or_skip` failing rather than skipping under `GF180_REQUIRE_TR808_REFS=1` |

Numbers 3 and 4 are the same shape as this project's recurring failure — **a correct instrument in a wrong state** —
and both were found because a property had a bound it could fail rather than a number it merely printed. Number 6 is
the one worth dwelling on for the result: it is the only one that would have changed what a reader believed, and
nothing but a control that tried to break the result would have found it. Number 7 is the one worth dwelling on for
the *apparatus*, and it is the worst of the seven by the standard this repository actually uses: the other six were
caught by something that ran, and this one was a documented gate that could not be run — CLAUDE.md's "worse than no
gate", found only by reading. It is also the reason the numbers above are unaffected: the flag gates *controls*, and
every control it gates was executed by hand and by `test_the_corpus_gated_controls_fire` before this was noticed.

## 9. Against #369's acceptance

| acceptance | state |
|---|---|
| **1. the structure matches the 808's metal circuit** | **advanced, positively.** The TONE control is now taken from the circuit — VR4's wiper fraction off SN p.13, nothing fitted to the recordings — and three structural alternatives are excluded against the machine's own recordings. Two structural questions are stated as unresolved with their numbers rather than closed |
| **2. a qualified measurement first** | **met for this question.** 12 named properties, 7 injected defects each verified to move at least one, 8 (defect, property) blindness assertions verified blind, one control blind in all 12 and verified so, 47 tests, and the measured side bound to the committed artifact at 0.0009 dB |
| **3. accurate across the knobs** | **the TONE half now has a law that is not circular**, and all 25 settings are reported in three windows. The kit side is untouched: applying it to a candidate is the follow-up, not this step |
| **4. preservation · 5. model → RTL → I²S · 6. listening pack** | **not touched.** No kit change, so nothing to preserve and nothing to carry. `tools/ab_808.py` and `tools/ab_808_loud.py` remain committed from an earlier increment |

### Next question, one at a time

**Does a candidate driven by this law track the 808 as TONE moves?** That is a kit change and needs a render, the
frozen development/confirmation split, and the hats' preservation set — a separate increment of **#369**, deliberately
not started here. The DECAY knob's law is a second, separate question: VR2 sits on the bottom rail's op-amp
(`tone_stage_schematic`'s own read of SN p.13 confirms it) and this step says nothing about it.

**What this step forbids the next one to do:** drive a TONE comparison through
`model/test_discrimination.kit_at`'s `CY.tone_ratio` law. It solves for the reference's own band ratio, so any
band-balance agreement it produces is an artefact of the law rather than evidence about the instrument — §4 of
`../README.md` measured that as a 13 dB discrepancy against the shipped kit, and this step is what makes replacing
it possible rather than merely desirable.

# Rungs 2–4: three candidate ladders, six dimensions, one verdict

Issue #46 asks whether any of four filter algorithms is worth changing to.
Rung 1 — *how much is available without changing the algorithm* — was audited
separately in [`ladder-rung1-audit.md`](ladder-rung1-audit.md). This is the
rest: three alternative discretisations of the same ODE, put behind one
interface, run in one fixed-point frame, and scored on the six dimensions the
issue names. Reproduce it:

```
python3 tools/compare_ladder_candidates.py                             # ~20 min
python3 tools/compare_ladder_candidates.py --quick                     # ~6 min
python3 tools/compare_ladder_candidates.py --json docs/ladder-rungs-2-4-results.json
python3 -m pytest model/test_ladder_candidates.py -q                   # its ground truth
python3 model/probe_rungs_2_4_gaps.py                                  # the probes behind §7
```

**Conclusion, up front. Retain the current filter.** The verdict is derived
from the numbers by `compare_ladder_candidates._verdict`, not written by hand:
no candidate improves on the shipped ladder by more than the harness can
resolve *while fitting the clock budget*, which is issue #46's own bar. A
well-measured "no candidate wins" is the complete result the issue asks for,
not a failure to deliver a change.

Three findings are worth more than the verdict:

1. **The half-sample feedback delay is load-bearing.** Removing it — the
   delay-free rung 3 — stops the filter self-oscillating above about 4 kHz.
   The structure DR 0001 calls an approximation is doing real work.
2. **The implicit trapezoidal form wins on sound, and it is not close.** It
   reproduces the closed form's resonant peak to **0.16 dB** where the shipped
   filter is **7.42 dB** short, it self-oscillates at every one of the fifteen
   operating points where the shipped filter (uncompensated) misses one, and
   its self-oscillation tuning is **50 cents less spread** across the grid.
   This is exactly the region DR 0001 predicted would matter.
3. **And it is excluded by cost alone, by a margin that is now an exact
   number.** It needs a divider of **7 clocks or fewer**; a restoring divider
   at 17 puts it at 401 clocks against a 256-clock budget. DR 0001's reversal
   is no longer a filter question, it is a question about one arithmetic unit.

**"No candidate wins" and "a candidate wins on sound and cannot be afforded"
are different results and the report keeps them apart.** This is the second,
and it is the more useful one, because it names the single thing that would
change the answer.

All three are recorded as
[DR 0017](../spec/decision-records/0017-the-half-sample-delay-is-load-bearing.md),
which answers DR 0001's stated reversal condition rather than re-deciding it.

---

## 1. The candidates, and why these three

Four discretisations of one ODE behind one interface
(`model/ladder_candidates.py`), so the only thing that differs between rows is
the algorithm:

| rung | candidate | `CORES` name | what it is |
|---:|---|---|---|
| 1 | Huovilainen, as ships | `shipped` | explicit forward Euler, half-sample feedback delay (DAFx-04) |
| 2 | implicit, fixed-iteration Newton | `zdf-newton-2`, `zdf-newton-3` | trapezoidal stages, delay-free loop, 2 or 3 Newton steps |
| 3 | explicit delay-free | `zdf-explicit` | explicit stages, loop closed in one linearisation, one divide |
| 4 | converged reference | `reference_ode` | 32× float, **not a shipping candidate** — it is the yardstick |

**On the D'Angelo–Välimäki papers specifically.** Issue #46 names DAV 2013 and
2014, and notes that the author's page carries reference code and errata for
2014. *Neither paper's reference code was available in this environment, and
neither was reimplemented from its prose.* What is measured here are the
**structural** choices the issue is actually asking about — implicit versus
explicit, delay-free versus half-sample-delayed, iterative versus closed-form —
with rung 2 built as DR 0001's own stated candidate (a fixed 2- or 3-iteration
Newton solve) rather than as a paper reimplementation. The harness interface is
one class and one method, so anyone with the authors' code can run it against
these numbers without rebuilding the frame. **This document claims only what it
measured**, and a DAV-labelled row is not among the things it measured.

Issue #46's own caution is worth carrying either way: DAV's implementation
still inserts delays and should not be described as a true zero-delay-feedback
solver. Neither should rung 3 here, and `ExplicitDFCore`'s docstring says so.

### Identical conditions, and what "identical" is allowed to mean

Every candidate runs the same stimuli at the same levels, the same 129-entry
Q0.16 coefficient table with the same interpolated read, the same 24/20 state
word, the same 16-entry `tanh`, the same 2× oversampling, and the same
coefficient-update path.

**One thing is deliberately not held identical, and it would be a strawman to
hold it.** The trapezoidal stages need `G = tanh(w/2)` where the explicit ones
need `g = 1 − exp(−w)`; these are the exact pole of each integrator. Backward
Euler with *our* law would need `G = e^w − 1 = 16.9` at the cutoff clamp, which
does not fit the Q0.16 word at all. So the *word*, the *table shape*, the
*read* and the *entry count* are identical — which is what silicon pays for —
and the arithmetic identity used to fill the table is each candidate's own.
Measuring a candidate on a coefficient law its structure cannot use would
measure a mis-tuning we chose.

Every candidate is compared **untuned**: DR 0011's `CUT_TRIM · fcr()` is a
constant fitted to the shipped `expo` law at `res = 1.05`, and no candidate has
one. `Candidate` refuses `tuned=True` for any other law rather than silently
applying it — the same class of apparatus defect the rung-1 audit found and
fixed in `reference_rigs.OurLadder`. The report additionally carries an
`as_ships` row (tuned *and* DR 0006-compensated) so the untuned baseline can be
read against the filter that actually ships.

`compensated=True` is refused for every non-shipped structure for the same
reason: DR 0006's `k_comp` ROM is bisected on the shipped loop's linearisation,
so applying it to a different loop would hide the very property the candidate
is being judged on — how much compensation its own structure needs.

## 2. What makes this a measurement and not a self-portrait

`docs/failure-modes.md` names the trap this harness is most likely to fall
into: internal consistency is cheap and external grounding is expensive, so a
comparison drifts toward scoring candidates against *us*. Three defences, in
the order they can fail.

**The yardstick is not one of us.** Tuning and resonance are scored against the
ladder's closed-form transfer function

```
H(jw) = Z(w) · gi · go · A⁴ / (1 + k A⁴),   A = 1/(1 + j w/wc),   k = 4·res
```

written out in `ladder_candidates.analytic_response_db` in six lines that can
be read against any DSP text. The only things borrowed from this repository are
the gain structure and the input rate, both arithmetic. The rung-4 converged
model earns its place as a *large-signal* reference by being measured against
that same closed form first, and `assert_apparatus` **REFUSES the whole report**
rather than printing one if the reference is not within 0.05 dB of it.

**That guard is close to firing and a reader should know it.** The measured
worst case is **0.047 dB**, at 6.4 kHz, against a 0.05 dB threshold — 0.002 dB
at 200 Hz and 0.001 dB at 1 kHz, so the whole margin is spent in the top
octave. Nothing in this document turns on a difference smaller than 0.1 dB, so
the floor is adequate for what is claimed; it would not be adequate for a
finer one, and raising the reference's oversampling is the lever if a future
comparison needs it.

<!-- claim: test=model/test_ladder_candidates.py::test_the_rung4_reference_converges_at_second_order -->

**It starts red.** Every stage runs against `null` — a core with the right
ports and no behaviour — before any candidate is believed. A stage that returns
a plausible number for silence is not a measurement, and
`docs/verification-rules.md` lists four harnesses in this repository that
shipped in exactly that state.

**It carries injected defects**, each of which must move *its own* dimension by
more than the whole spread between real candidates — because a dimension that
cannot see a dropped pole cannot see an algorithm change either:

| control | dimension | what it does |
|---|---|---|
| a dropped pole (3 stages) | linear | **refuses 8 of 8 points** against the baseline's 2 of 8 |
| a 6 % cutoff skew | linear | reads as 72.2 cents of movement (the skew is ~101) |
| a 4-entry `tanh` | drive | moves the saturation point 4.22 dB, inharmonic 4.6 dB |
| a 32 Hz control quantum | movement | raises the sweep residual 24.51 dB |

One of these does not behave the way it was written to behave, and the way it
actually behaves is better; see §7.

**A refusal is a red outcome, and the report has to say so in words.** The
dropped-pole control first printed `moved_peak_db=nan`, because it subtracted
two peak figures and the defective run had refused every point. An unknown
rendered in the place where a verdict belongs is the `FAIL(??)` failure
`tools/run_all.py` exists to prevent, and it is now reported as a refusal count
against the baseline's.

## 3. The six dimensions

<!-- claim: test=model/test_ladder_candidates.py::test_the_committed_report_scores_every_candidate_on_every_dimension -->

**Two scores, kept apart, because the issue says so.** "Sounds bigger" and
"matches the reference better" are different questions and a change may
legitimately win one and lose the other. They are never added together.

**Reproduced from the committed report**
([`ladder-rungs-2-4-results.json`](ladder-rungs-2-4-results.json), a full run —
`quick: false`). All rows are **untuned and uncompensated**, which is each
candidate's native behaviour; the `as ships` row is the shipped filter *with*
DR 0011's tuning and DR 0006's compensation, for scale.

**"Matches the reference better"** — distance from the ladder's closed-form
transfer function, worst case over four cutoffs (100 Hz – 6.4 kHz) × two
resonances:

| candidate | rung | peak tuning | peak height | passband | points refused |
|---|---:|---:|---:|---:|---:|
| shipped (Huovilainen) | 1 | 38.7 cents | **7.42 dB** | 0.01 dB | 2 / 8 |
| implicit Newton, 2 iterations | 2 | 88.3 cents | **0.16 dB** | 0.06 dB | 2 / 8 |
| implicit Newton, 3 iterations | 2 | 87.8 cents | 0.29 dB | 0.06 dB | 2 / 8 |
| explicit delay-free | 3 | 144.4 cents | 19.89 dB | 0.05 dB | 2 / 8 |
| *(shipped, as ships)* | *1* | *11.4 cents* | *0.40 dB* | *1.09 dB* | *2 / 8* |

The two refused points are the **100 Hz** pair, at both resonances, for every
row alike: the passband probe sits a decade below the cutoff, at 8.8 Hz, which
is about three periods in the 16384-sample coherent window — `tone_amplitude`
refuses "too few periods for a coherent projection" rather than projecting onto
a partial cycle. It refuses identically for every candidate, so it costs the
comparison two operating points and biases nothing.

**"Sounds bigger"** — the separate question, never added to the one above:

| candidate | bass weight | drive saturation | compression | chord RMS | sings |
|---|---:|---:|---:|---:|---:|
| shipped (Huovilainen) | −5.25 dB | −8.75 dBFS | 0.988 dB/dB | 0.200 | 14 / 15 |
| implicit Newton, 2 iterations | −4.97 dB | −7.38 dBFS | 0.993 dB/dB | **0.226** | **15 / 15** |
| implicit Newton, 3 iterations | −5.00 dB | −7.38 dBFS | 0.993 dB/dB | 0.226 | **15 / 15** |
| explicit delay-free | **−4.16 dB** | −8.73 dBFS | 0.988 dB/dB | 0.143 | 9 / 15 |

The delay-free form keeps the most bass weight and is the worst filter in the
table on every other axis — which is exactly why the two scores are kept apart.

### Dimension by dimension

**bass** — gain at 55–220 Hz with the cutoff at 1 kHz, as resonance rises from
0.1 to 1.8, against the closed form's own prediction of the same thing:

| candidate | weight retained | error vs closed form |
|---|---:|---:|
| shipped | −5.25 dB | 1.90 dB |
| implicit Newton, 2 | −4.97 dB | **1.31 dB** |
| implicit Newton, 3 | −5.00 dB | 1.35 dB |
| explicit delay-free | **−4.16 dB** | 1.92 dB |

**drive** — a 220 Hz tone pushed from −24 to 0 dBFS through an open filter,
plus a three-note chord at 110 Hz, since the issue asks for several
simultaneous tones now rather than after paraphony:

| candidate | h3 reaches −40 dB at | compression | chord inharmonic | chord clamped |
|---|---:|---:|---:|---:|
| shipped | −8.75 dBFS | 0.988 dB/dB | −4.3 dB | 0.0 |
| implicit Newton, 2 | −7.38 dBFS | 0.993 dB/dB | −5.3 dB | 0.0 |
| implicit Newton, 3 | −7.38 dBFS | 0.993 dB/dB | −5.3 dB | 0.0 |
| explicit delay-free | −8.73 dBFS | 0.988 dB/dB | −1.9 dB | 0.0 |

Nothing clamps, at any level, on any candidate — so none of these differences
is an overflow artefact.

**movement** — a cutoff sweep at 40 oct/s and at 10 oct/s, scored by the
residual against the same candidate driven by a *float* control path, so what
is measured is the control path and not the filter's response:

| candidate | residual, 60→960 Hz @ 40 oct/s | residual, 500→8000 Hz @ 10 oct/s |
|---|---:|---:|
| shipped | −49.28 dB | −66.91 dB |
| implicit Newton, 2 | **−54.95 dB** | **−69.99 dB** |
| implicit Newton, 3 | −54.95 dB | −69.94 dB |
| explicit delay-free | −49.28 dB | −66.99 dB |

**resonance** — five cutoffs (100 Hz – 6.4 kHz) × three resonances at and above
onset, uncompensated:

| candidate | sings | tuning spread | worst offset |
|---|---:|---:|---:|
| shipped | 14 / 15 | 190.6 cents | 150.0 cents |
| implicit Newton, 2 | **15 / 15** | **140.1 cents** | 186.9 cents |
| implicit Newton, 3 | **15 / 15** | 141.4 cents | 188.2 cents |
| explicit delay-free | 9 / 15 | 524.7 cents | 384.1 cents |

The one point the shipped filter misses is **6.4 kHz at `res = 1.05`**, and it
misses it *uncompensated* — which is DR 0006's whole reason for existing and
DR 0001's caveat about self-oscillation above ~3 kHz, now with an operating
point attached. The implicit form needs no such help. Note the two resonance
numbers disagree in direction: the implicit form is **more consistent** across
the grid (smaller spread) and **further from the commanded pitch** in absolute
terms, which is the same resonance-dependent offset #237 is about — it is a
property of how any single correction constant is fitted, not of the structure.

**cleanliness** — foldback aliasing of a hot 2093 Hz tone through an open
filter, state clamping, and whether a sub-onset ring decays:

| candidate | foldback aliases | clamped samples | ring tail |
|---|---:|---:|---:|
| shipped | **−63.7 dB** | 0.0 | 1 LSB |
| implicit Newton, 2 | −68.2 dB | 0.0 | 1 LSB |
| implicit Newton, 3 | −68.5 dB | 0.0 | 1 LSB |
| explicit delay-free | −69.1 dB | 0.0 | 1 LSB |

The shipped filter carries about **5 dB more foldback energy** than any
candidate. This axis returned `None` for every row until the probe frequency
was fixed (§7), so it is new information and it is the one dimension where the
shipped filter is measurably worst. It is small in absolute terms — −63.7 dB —
and it did not change the verdict.

No candidate decays to *exact* silence below onset: all four ring at one Q15
LSB. That is the arithmetic floor of a 16-bit state, identical across rows,
and it is not a discriminator.

## 4. Cost, including the part that is usually left out

Per output sample, at 2× oversampling. `tanh` and divide counts are
**counted by the inner loop**; multiply and add are declared from each core's
source and pinned against the instrumented count by a test, so a core that
changes shape cannot keep a stale cost.

| candidate | `tanh` | divide | multiply | add | clocks @ d=1 | @ d=8 | @ d=17 |
|---|---:|---:|---:|---:|---:|---:|---:|
| shipped | 10 | **0** | 12 | 20 | 35 | 35 | 35 |
| implicit Newton, 2 | 20 | **16** | 86 | 118 | 145 | **257** | 401 |
| implicit Newton, 3 | 30 | **24** | 120 | 168 | 207 | 375 | 591 |
| explicit delay-free | 20 | **2** | 50 | 50 | 95 | 109 | 127 |

The budget is DR 0001's: **256 clocks per sample**, 12.288 MHz over 48 kHz.
`d` is the divider's latency in clocks, swept rather than assumed, because the
whole cost difference between these candidates is divides.

And then solved rather than read off the grid — which is how this record's
central number came out wrong the first time (§7):

| candidate | divider latency the budget allows |
|---|---|
| shipped | no divider at all |
| implicit Newton, 2 iterations | **≤ 7 clocks** |
| implicit Newton, 3 iterations | ≤ 3 clocks |
| explicit delay-free | ≤ 81 clocks |

**Coefficient-update cost under moving controls, measured rather than
assumed.** Issue #46 calls this out as where a "cheap" algorithm hides real
cost, so the movement stage renders every candidate with the cutoff *moving*
and compares its instrumented inner-loop cost against the same candidate with
the cutoff held:

| candidate | coefficient-update multiplies / sample | inner-loop cost under a moving cutoff |
|---|---:|---|
| shipped | 3 | unchanged |
| implicit Newton, 2 | 3 | unchanged |
| implicit Newton, 3 | 3 | unchanged |
| explicit delay-free | 3 | unchanged |

The result is a negative one and it is the useful kind: **no candidate here
pays anything extra for a moving control.** None of them needs a transcendental
per sample, because `tanh(w/2)` is baked into the build-time table exactly as
`1 − exp(−w)` is, and the per-sample update is the same three multiplies for
every row — the `g` ROM's interpolating read, the `k` ROM's, and `k_effective`.
That is a property of putting the law in the table rather than in the datapath,
and it is why the cost comparison reduces cleanly to divides.

## 5. DR 0001's reversal condition, answered

DR 0001 rejected an iterative solver on the solver's *shape* and named its own
reversal condition in one sentence:

> **What would reverse this:** a fixed 2- or 3-iteration Newton solve, measured
> stable at resonance ≥ 1.0 across the full cutoff range and inside the clock
> budget.

Taken clause by clause:

| DR 0001's clause | measured | met? |
|---|---|---|
| *a fixed 2- or 3-iteration Newton solve* | built as `zdf-newton-2` / `zdf-newton-3`; the third iteration changes the peak by 0.13 dB and the tuning by 0.5 cents, so the answer to "2 or 3" is **2** | yes |
| *stable at resonance ≥ 1.0* | sings at **15 of 15** operating points, `res` 1.05 / 1.45 / 2.00, sustain within 0.1 dB over the tail; nothing clamps | **yes** |
| *across the full cutoff range* | 100 Hz to 6.4 kHz, the same grid every other filter measurement here uses | **yes** |
| *inside the clock budget* | 401 clocks against 256 at a 17-clock restoring divider; needs **≤ 7 clocks** | **no** |

**Partially met, and the part that is not met is the clock budget.** The full
reasoning and what would now reverse *it* is DR 0017. The short version: the
2-iteration solve costs `129 + 16·d` clocks per sample against a 256-clock
budget, so it needs a divider of **7 clocks or fewer**. That is a question
about one arithmetic unit, not about the filter.

**A third Newton iteration buys nothing.** DR 0001 says "2- or 3-iteration";
they are the same filter to within this harness's resolution, so the answer to
"which" is 2, and the third iteration's extra 4 divides and 5 `tanh` per
sub-step are spent for nothing.

<!-- claim: test=model/test_ladder_candidates.py::test_a_third_newton_iteration_buys_nothing_over_a_second -->

## 6. The movement dimension, and the parts of it that do not exist yet

The movement score uses the **sweep-rate** methodology of
`model/reference_movement.py` (#53): a cutoff sweep at a stated rate, scored by
the residual against the same candidate driven by a float control path, so what
is measured is the *control path's* smoothness and not the filter's response.

**Two parts of #53's methodology do not exist yet and are named gaps here, not
silently-assumed coverage:**

- a **continuous** resonance sweep through the self-oscillation threshold
  (the suite has a discrete on/off step, `model/test_moog_acceptance.py`);
- an **audio-rate** cutoff-modulation depth measurement, as opposed to the
  LFO-rate sweep used here.

A complete movement score for a candidate needs both. Neither changes this
document's verdict, because the verdict turns on cost and on resonance, but a
future candidate that wins on *movement specifically* cannot be accepted on
this dimension as it stands.

## 7. Wrong before it was right

**Seven things in this work were wrong before they were right**, against
roughly thirty reported figures. Every one was caught by a control, a sweep or
a refusal rather than by inspection, and that rate is how a reader should
calibrate any single figure above.

Three of the seven are the same shape and it is worth naming the shape: **the
apparatus was correct, it refused, and the report rendered the refusal as
something that looked like data** — `nan`, `None`, or a dimension quietly
absent from the pytest control set. `REFUSED` being a first-class outcome is
only half the job; the other half is that it has to survive being printed.

1. **The delay-free ladder's oscillation ceiling was stated as its closed-form
   bound, 5295.6 Hz.** That bound is on the *phase*, and it is loose: swept to
   resonance 4.0 the delay-free form does not sing at 4800 Hz either, which is
   *below* the bound. The usable ceiling is between 4000 and 4800 Hz. The
   closed form remains correct as the frequency above which no feedback
   whatsoever suffices — it is simply not the number a player meets.
   (`model/probe_rungs_2_4_gaps.py` probe 3.)
2. **The reversal condition's divider threshold was written as "≤ 8 clocks"**,
   because 8 was the grid point below 17 on a three-point sweep. The solve
   needs `129 + 16·d ≤ 256`, so `d ≤ 7`; at exactly 8 it is **257 clocks
   against a 256 budget**, one over. `stage_cost` now solves for the threshold
   instead of reading it off the grid, and prints it per candidate.
3. **The peak-locator's own ground-truth test built its probe grid around the
   cutoff** while the apparatus builds it around the analytic peak. At
   `res = 0.5` the ladder's peak sits at 0.819× cutoff, outside the window — so
   the test was measuring a window the apparatus does not use. The apparatus
   had already been fixed and the test had not. (Probe 1.)
4. **The cost test instantiated each core without the kwargs `CORES` carries**,
   and `NewtonCore.MULS` is computed in `__init__` from `iters`. It was
   therefore comparing the 3-iteration core's instrumented 60 multiplies
   against the 2-iteration default's declared 43 — a stale cost of exactly the
   kind that test exists to catch. (Probe 2.)
5. **The dropped-pole control was written expecting a moved dB and goes red by
   REFUSING** — and printed `moved_peak_db=nan` when it did. Three poles push
   the resonant peak clean out of the four-pole search window at 6 of 6
   operating points, every one at the *top* edge of the bracket. That is not a
   weaker signal than a large number; it is the apparatus declining to report a
   peak it cannot support, which is what it should do with a filter that is not
   the one the closed form describes. The control now asserts unanimity and
   direction, paired with the four-pole run scoring all the same points so the
   refusal cannot be vacuous, and the report prints a refusal count rather than
   a subtraction of two unknowns. (Probe 4. The report's own grid includes the
   two 100 Hz points that refuse for everyone, so it reads 8/8 against 2/8; the
   test's grid excludes them and reads 6/6 against 0/6.)
6. **The aliasing half of the cleanliness dimension was never measured.** The
   probe tone was 2000 Hz and `48000 / 2000 = 24` exactly, so every image of
   every harmonic above Nyquist folds back *onto a real harmonic* and none can
   be attributed. `foldback_alias_db` reported **0 usable images against 121
   collisions** and correctly refused — and the report turned that into
   `alias_db: None` for all four candidates, which reads as "measured, nothing
   found" rather than "not measured". The probe is now 2093 Hz (C7, not a
   submultiple of the rate: 126 usable images, 0 collisions), the report
   carries the refusal reason alongside the value, and the precondition is
   asserted at the point of use with the degenerate frequency kept as the
   known-fails case.
7. **Start-red covered four dimensions in pytest and six in the report** — and
   the two that were missing, movement and cleanliness, are exactly the two
   whose estimators are most likely to answer for silence. Pinning them turned
   up an asymmetry worth stating: `decays_to_silence` is a sub-axis on which
   **the null core scores best**, because silence does decay to silence and a
   real fixed-point ladder rings at the Q15 floor. It can never condemn
   anything on its own, and what condemns silence in that dimension is the
   aliasing estimator refusing outright.

An eighth, inherited rather than made here and recorded because it set this
harness's floor: the closed form omitted the **zero-order hold** of the 48 kHz
input, which read as a 1.02 dB "discretisation error" at 6.4 kHz that did not
shrink when the reference's oversampling was doubled — because it was never
discretisation. It carries its own control now.

## 8. What this does not settle

- **It is not a D'Angelo–Välimäki comparison.** See §1: neither paper's
  reference code was available and neither was reimplemented. The structural
  questions the issue asks about are measured; the papers' specific
  formulations are not.
- **It holds the `tanh` table at 16 entries throughout.** Issue #46 puts the
  table out of scope, and DR 0006's `k_comp` ROM derives from its bin-0 slope —
  changing the table moves the small-signal resonant peak 23.3 → 20.2 dB, so a
  table change is a coupled ROM re-derivation and not a candidate property.
- **It renders no plugin.** The frozen external profile
  (`docs/reference-compare-results.json`) describes the *shipped* filter, so it
  cannot score a candidate. `docs/discrimination.md` §8's `ours` rows are still
  revision 8 — the filter before DR 0011 — and re-deriving them is the
  prerequisite for scoring any candidate against the one strong external
  comparison this project has. That work is named in #239 and is not done here.
- **It does not address the resonance-dependent cutoff offset** the rung-1
  audit found (105 cents of travel across the resonance knob, #237). No
  candidate here addresses it either: where the loop sings is set by how the
  correction was fitted against the loop's own gain, and every candidate has a
  loop gain. If that ROM is rebuilt, DR 0017 §3 notes the marginal cost of also
  emitting the `tanh(w/2)` law is one line, and the two should be sequenced
  together rather than paying the contract-revision blast radius twice.
- **The movement dimension is incomplete** in the two specific ways §6 names.

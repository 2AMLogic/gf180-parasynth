# 0018: DR 0001's reversal condition, measured — and the half-sample delay turns out to be load-bearing

- **Status**: proposed
- **Date**: 2026-09-26
- **Decided by**: block agent, from `tools/compare_ladder_candidates.py`
- **Supersedes**: nothing. **Amends**: DR 0001, whose reversal condition this
  record answers rather than re-deciding.

## Context

DR 0001 chose Huovilainen's explicit ladder over an iterative Zavalishin/ZDF
solver, on the solver's shape rather than its accuracy, and named its own
reversal condition in one sentence:

> **What would reverse this:** a fixed 2- or 3-iteration Newton solve, measured
> stable at resonance ≥ 1.0 across the full cutoff range and inside the clock
> budget. That is an experiment, not a matter of taste, and it is worth running
> if tuning accuracy above 3 kHz ever becomes the limiting complaint.

Issue #46 asked for that experiment as its rungs 2–4. It has now been run:
`model/ladder_candidates.py` puts four discretisations of the same ODE behind
one interface in one fixed-point frame, and
`tools/compare_ladder_candidates.py` scores them on issue #46's six named
dimensions against the ladder's closed-form transfer function.

**This record does not re-decide DR 0001.** It states what the condition
measured to, which parts of it are met, and what a reversal would now cost.

## Decision

**Retain Huovilainen. DR 0001's reversal condition is _partially_ met, and the
part that is not met is the clock budget.**

Three findings, in the order they matter.

### 1. The half-sample feedback delay is not a defect. It is what lets the filter sing.

<!-- claim: test=model/test_ladder_candidates.py::test_the_delay_free_explicit_ladder_cannot_oscillate_above_a_known_cutoff -->

Removing the half-sample delay `(d1 + d2)/2` and closing the loop in one
linearisation — an explicit delay-free method, issue #46's rung 3 — makes the
filter **stop self-oscillating above a cutoff this record now puts a number
on**, and costs a divider to do it.

The reason is arithmetic and is not a property of our implementation. A forward
Euler one-pole `H = g / (1 - (1-g) z^-1)` has a maximum phase lag of

    arctan( a sin w / (1 - a cos w) )  maximised at  cos w = a,  a = 1 - g

which is **below 45° once `g` is large enough**, and four stages of a lag below
45° never reach the −180° the loop needs. At a 6.4 kHz cutoff `g = 0.342`, the
maximum lag per stage is **41.1°** and the four-stage total is **164.6°** — 15°
short. The shipped filter reaches −180° because the half-sample delay
contributes the missing phase.

So the structure DR 0001 describes as an approximation is doing load-bearing
work, and "fixing" it removes a feature.

#### The closed form is a ceiling, and it is not the ceiling a player meets

**This record first stated the limit as 5295.6 Hz and that is a loose upper
bound, not the measurement.** The bound is on the *phase*: below it a −180°
crossing exists, but as the per-stage lag falls towards 45° the crossing
retreats into a band where the cascade's own magnitude is small, so the
feedback needed to close the loop runs away before the phase does. Swept
(`model/probe_rungs_2_4_gaps.py` probe 3, resonance to 4.0 — well past anything
the knob commands):

| cutoff | per-stage max lag | delay-free: first resonance that sings | shipped |
|---:|---:|---|---|
| 800 Hz | 71.6° | 1.45 | 1.05 |
| 1600 Hz | 64.2° | 1.45 | 1.05 |
| 3200 Hz | 54.2° | 2.00 | 1.05 |
| 3600 Hz | 52.2° | 2.00 | 1.05 |
| 4000 Hz | 50.3° | 2.00 | 1.45 |
| 4800 Hz | 46.9° | **never**, to res 4.0 | 1.45 |
| 6400 Hz | 41.1° | **never**, to res 4.0 | 1.45 |

So the delay-free ladder's **usable** ceiling is between 4000 and 4800 Hz —
*below* the closed form's 5295.6 Hz, which remains correct as the frequency
above which no feedback whatsoever suffices. The contrast that makes the delay
load-bearing is the right-hand column: the shipped filter sings at every one of
those cutoffs, including both where the delay-free form cannot.

### 2. The implicit trapezoidal form holds the resonance where ours loses it

<!-- claim: test=model/test_ladder_candidates.py::test_the_implicit_candidate_holds_the_resonant_peak_where_the_shipped_one_loses_it -->

A trapezoidal implicit ladder with a fixed 2-iteration Newton — DR 0001's own
candidate — reproduces the closed form's resonant peak at a 6.4 kHz cutoff to
**a fraction of a dB, where the shipped filter is several dB short**, and it
self-oscillates at every cutoff measured. That is a real improvement and it is
in exactly the region DR 0001 said would matter ("if tuning accuracy above
3 kHz ever becomes the limiting complaint").

It costs a coefficient law of its own — `G = tanh(w/2)` instead of
`1 - exp(-w)` — which is a **build-time** change to the same 129-entry Q0.16
table, the same word, the same interpolated read and no datapath change. The
law is not optional: backward Euler with our own law needs `G = e^w - 1 = 16.9`
at the cutoff clamp, which does not fit the Q0.16 word at all.

### 3. The budget is the part that fails, and it fails on divider latency

<!-- claim: test=model/test_ladder_candidates.py::test_the_instrumented_cost_is_the_shape_each_core_declares -->

Operation counts are **per oversampled sub-step**, counted by the inner loop
rather than declared; the clock columns are **per output sample** at 2×
oversampling. The two unit systems are mixed deliberately here — the operation
counts are a property of the solver, the clock budget is a property of the
sample rate — so each column says which it is:

| candidate | `tanh`/sub-step | divide/sub-step | multiply/sub-step | clocks/sample @ d=1 | @ d=8 | @ d=17 |
|---|---:|---:|---:|---:|---:|---:|
| shipped (Huovilainen) | 5 | **0** | 6 | 35 | 35 | 35 |
| implicit Newton, 2 iterations | 10 | **8** | 43 | 145 | **257** | 401 |
| implicit Newton, 3 iterations | 15 | **12** | 60 | 207 | 375 | 591 |
| explicit delay-free | 10 | **1** | 25 | 95 | 109 | 127 |

**`docs/ladder-rungs-2-4.md` §4 gives the same operation counts per output
sample, so every one of them is exactly 2× the value here** (2-iteration
Newton: 20 `tanh`, 16 divides, 86 multiplies per sample against 10 / 8 / 43 per
sub-step). That is the oversampling factor `lc.OVERSAMPLE`, not a disagreement;
the clock columns are already per output sample in both documents and are
identical. `docs/ladder-rungs-2-4-results.json` is the arbiter and it stores the
per-output-sample form, `cost/<candidate>/per_sample` — divide those by
`lc.OVERSAMPLE` to recover this table.

DR 0001's budget is 256 clocks per sample at 12.288 MHz over 48 kHz. **The
whole cost difference is divides**, and whether the 2-iteration solve fits
depends entirely on the divider's latency — which is why
`tools/compare_ladder_candidates.py` sweeps it instead of assuming one.

**And the sweep is then solved rather than read off, because reading it off got
this record's central number wrong.** Cost is affine in the divider latency, so
the threshold is arithmetic: the 2-iteration solve is `129 + 16 d` clocks and
needs `d <= 7`. This record first said "≤ 8 clocks", because 8 was the grid
point below 17 — and at exactly 8 it is **257 clocks against a 256 budget**, one
clock over. The report now prints `max_divider_latency_that_fits` per candidate
and `model/test_ladder_candidates.py::test_the_divider_latency_the_budget_allows_is_exact_not_read_off_the_grid`
pins it from both sides.

| candidate | divider latency the budget allows |
|---|---|
| shipped | no divider at all |
| implicit Newton, 2 iterations | **≤ 7 clocks** |
| implicit Newton, 3 iterations | ≤ 3 clocks |
| explicit delay-free | ≤ 81 clocks |

That turns DR 0001's reversal from a filter question into a **divider**
question, which is a different and much more tractable piece of work than
re-deciding the filter.

## What would reverse *this*

Three things, all now specific rather than general:

1. **A reciprocal unit of ≤ 7 clocks in the ladder datapath**, measured in
   place, not estimated. With one the 2-iteration solve fits and the reversal
   condition is met in full. Seven, not eight: see §3.
2. **A complaint that is actually about resonance above 3 kHz.** The shipped
   filter's peak deficit up there is real and measured; nothing currently says
   a player minds. DR 0015 (the scorecard) is where that would show up.
3. **A coefficient ROM rebuild anyway.** #237 already proposes rebuilding the
   cutoff and compensation tables for the resonance-dependent tuning offset. If
   that work happens, the marginal cost of also emitting the `tanh(w/2)` law is
   one line, and the two should be sequenced together rather than paying the
   contract-revision blast radius twice.

## Consequences

- `rtl-sketch/ladder_dp.v` is unchanged, as is every bit-exact expectation, the
  contract revision, `G_ROM128`, `K_ROM32` and every rendered `.wav`.
- **DR 0001's caveat about self-oscillation above ~3 kHz now has a second
  cause on the record.** It is not only "the paper's required feedback varies
  with frequency": the explicit one-pole's phase lag saturates, and DR 0006's
  compensation ROM is what buys the shipped filter its range back.
- The comparison harness is committed and reproducible, so the next person to
  propose a ladder algorithm has somewhere to run it rather than an argument to
  have. `docs/ladder-rungs-2-4.md` is the reader's entry point.
- **Neither published D'Angelo–Välimäki paper was reimplemented from its
  authors' code**, which was not available in this environment. The candidates
  are named structurally and the record claims only what it measured. Anyone
  with the reference code should re-run the harness against it; the interface
  is one class and one method.

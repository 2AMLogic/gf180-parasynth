# Rung 1: how much is left in the ladder we already ship

Issue #46 asks whether any of four filter algorithms is worth changing to, and
says rung 1 first — **how much is available without changing the algorithm.**
This is that audit. Reproduce it:

```
python3 model/ladder_headroom.py --json build/ladder-headroom.json   # ~3 min
python3 model/ladder_headroom.py --quick                            # ~40 s
python3 -m pytest model/test_ladder_headroom.py -q                  # its ground truth
```

**Conclusion, up front. Retain the current filter.** One rung-1 item is worth
pursuing and it is not an algorithm problem — the self-oscillation frequency is
a function of the *resonance* knob, travelling **105 cents** from `res = 1.02`
to `res = 2.00`. Nothing in rungs 2–4 addresses that; it is set by how the
cutoff correction was fitted, not by which large-signal model is inside the
loop. Two axes hold small, free wins worth about 13 cents each. Two are closed.

<!-- claim: test=model/test_ladder_headroom.py::test_the_audit_reports_every_axis_with_a_verdict_and_a_number -->

| axis | verdict | deciding number |
|---|---|---|
| `cutoff-offset` | **worth pursuing** | 105 cents of travel across the resonance knob |
| `tuning-law` | available but small | 13 cents recoverable, for one build-time multiply |
| `coefficient-rom` | available but small | 21 cents recoverable, for nothing at all |
| `numerical-precision` | no further win available | 0.004 pp moved by 8 more state bits |
| `drive` | no further win available | h3 hits −40 dB at −8.2 dBFS, Mini V3 at −5.7 |

**The issue's premise has moved on and this audit starts from the corrected
one.** Issue #46 names one rung-1 win — Huovilainen's `fcr` tuning polynomial —
and says we omit it. We have applied it since **DR 0011** (2026-09-18), which
is the same day the issue was filed; `model/voice_fx.py` carries a comment
placed specifically to stop that sentence being read in the present tense. So
rung 1's remaining scope is what is left *after* `fcr`, which is what follows.

---

## 1. What DR 0011 fixed, and the half of it nobody measured afterwards

DR 0011's headline metric is the **drift** of the self-oscillation frequency:
how much the ratio `f_osc / commanded` differs *from one cutoff to another*.
That record is explicit that a drift metric is blind to a uniform offset — it
has a section called "Two metrics, because one of them is blind" — and it
guards that blindness with an injected uniform skew at **one operating point**,
`res = 1.05`, which is where `CUT_TRIM` was fitted.

Measured across the resonance knob on the fixed-point filter, seven cutoffs
from 100 Hz to 6.4 kHz:

| `res` | mean offset | drift spread |
|---:|---:|---:|
| 1.02 | **+0.59 %** | 1.13 pp |
| 1.05 | **+0.07 %** | 1.12 pp |
| 1.10 | −0.64 % | 1.10 pp |
| 1.20 | −1.90 % | 1.08 pp |
| 1.45 | −3.81 % | 0.99 pp |
| 1.70 | −4.84 % | 1.02 pp |
| 2.00 | **−5.32 %** | 1.26 pp |

The drift is closed: **1.0 to 1.3 pp at every resonance**, against 7.92 pp
before DR 0011 and Surge XT Type 2's 0.62 pp, which is the best figure any
reference achieves. `fcr` did its job and it did it uniformly.

The **offset** is not. `CUT_TRIM` is a single constant fitted at `res = 1.05`,
and it is excellent there (+0.07 %); by maximum resonance the filter sings
5.3 % — 92 cents — flat of the cutoff it was commanded. Across the knob that is
**105 cents of travel**, and the drift barely moves while it happens, so the two
really are independent errors and only one of them has ever been measured after
the change that moved it.

<!-- claim: test=model/test_ladder_headroom.py::test_dr_0011_removed_the_drift_and_left_a_resonance_dependent_offset -->

**Why this matters more than any candidate algorithm.** Used as an oscillator at
high resonance — a characteristic Minimoog sound — the filter is most of a
semitone out, and the amount depends on where the resonance knob sits, so the
player cannot learn it. No tuning *polynomial* can remove it: it is not a
function of frequency. And no rung-2-to-4 candidate removes it either, because
where the loop sings is set by how the correction was fitted against the loop's
own gain, which every one of them also has.

**What it would take.** `g` and `k` are both available per frame in the
datapath already, so a resonance-dependent term in the cutoff mapping is
conceivable — but it is not the free ROM-build-time change `fcr` was, it moves
DR 0006's compensation ROM with it, and choosing which operating point is
"right" is a decision record and not a measurement. Filed as **#237**, bundled
with the two ROM-build-time wins of §3 and §4 so that blast radius is paid once.

## 2. The external reference, and exactly how far it can be trusted

`docs/discrimination.md` §8.4 is the strongest external evidence this project
has about the filter: our tracking against Surge XT's `LP Vintage Ladder`
Type 2, which is an implementation of the same paper DR 0001 implements, with a
cutoff commanded *and read back* in Hz.

**The drift comparison survives; the absolute one does not.** §8.3 records that
Surge Type 2 clamps resonance at 0.9925 and cannot self-oscillate at all, so the
frozen Surge row is a *decaying resonant ring* where ours is a limit cycle. In
the frozen profile the two differ by more than 30 dB of ring level at the same
nominal maximum resonance. Both measure where the resonance sits, so comparing
how much that *drifts* is fair; comparing the absolute offset is not, and this
audit refuses to draw it.

<!-- claim: test=model/test_ladder_headroom.py::test_the_frozen_surge_row_is_not_a_like_for_like_limit_cycle -->

That caveat is asserted out of `docs/reference-compare-results.json` — the
frozen profile, never re-rendered — rather than argued about.

## 3. The tuning law: a small free win, and the reason it is small

Two instruments, because one is cheap and one is true. The **measured** free ring
of the fixed-point filter is what anybody hears, at about a second per point.
The **linearised loop** of DR 0006 — four one-poles at the ROM's coefficient plus
the half-sample feedback delay — costs a bisection, so it can be swept densely,
*inverted*, and refitted. The proxy's accuracy is a reported result and not an
assumption: against DR 0011's six locked measured ratios it is inside 0.12 pp
below 3 kHz and 0.31 pp at 10 kHz.

<!-- claim: test=model/test_ladder_headroom.py::test_the_predicted_tracking_agrees_with_the_measured_table_dr_0011_locked -->

Inverting the loop gives the ratio `f' / f` the coefficient law *actually* needs.
The shipped `CUT_TRIM · fcr(f)` misses it by a smooth S — **+0.79 % at 30 Hz,
+1.00 % at 2.2 kHz, −0.76 % at 21.6 kHz** — which is why a single constant cannot
flatten it: `fcr` is the paper's cubic fitted to a *different* implementation.

Refitting a cubic of the same shape to *this* loop takes the worst ratio error
from **0.997 % to 0.249 %**, and a quartic reaches 0.023 %. Either costs one
multiply per ROM entry at build time and **nothing in the datapath** — exactly
DR 0011's class of change.

<!-- claim: test=model/test_ladder_headroom.py::test_a_refitted_tuning_polynomial_recovers_most_of_the_remaining_drift -->

**Reported as available-but-small.** 0.75 pp is about 13 cents, against §1's
105. It is worth taking *with* the offset work (#237), because both are edits to
`make_g_rom` and both move DR 0006's ROM, the contract revision and every
bit-exact expectation — and paying that blast radius twice would be wasteful.

## 4. The coefficient ROM: more entries is a poor buy, refitting them is free

The 129 × Q0.16 cutoff ROM is read with a linear interpolation and a truncating
shift. Measured as cents of cutoff error against the exact law, over every
integer cutoff from 30 Hz to 21.6 kHz:

| index bits | entries | ROM bits | worst error |
|---:|---:|---:|---:|
| 5 | 33 | 528 | −90.4 c |
| 6 | 65 | 1040 | −49.8 c |
| **7 (shipped)** | **129** | **2064** | **−29.0 c** |
| 8 | 257 | 4112 | −20.3 c |
| 9 | 513 | 8208 | −9.2 c |
| 10 | 1025 | 16400 | −9.2 c |

Two things come out of the sweep, and neither was guessable.

**The error is confined to the bottom of the range** — −29.0 c at 30 Hz, −17 c
at 100 Hz, under a cent above 1 kHz — because that is where `g` is only a few
hundred LSB, so one LSB is worth about 13 cents of cutoff.

**More entries is a poor buy and then stops working.** Doubling the table costs
2048 ROM bits and buys 8.7 cents; past 513 entries the sweep **floors** at
9.2 cents, because what is left is the Q0.16 coefficient word itself. Widening
that is a datapath change, not a ROM-build one.

<!-- claim: test=model/test_ladder_headroom.py::test_more_coefficient_rom_entries_is_a_poor_buy_and_the_sweep_floors -->

**But the error is one-sided, and removing that bias is free.** The ROM is
edge-sampled and `1 − exp(−x)` is concave, so linear interpolation between edge
samples always lands *below* the curve: every entry is biased the same
direction. Choosing the 129 stored words to minimise the interpolated error
instead of to sample the law takes the worst error from **29.0 to 8.0 cents** —
better than a 1025-entry table — for the same 2064 ROM bits, the same read, and
no datapath change at all.

<!-- claim: test=model/test_ladder_headroom.py::test_refitting_the_existing_129_entries_beats_a_1025_entry_table -->

Measured, not shipped, for the same reason as §3: it moves the same tables. Also
filed into #237; `ladder_headroom.refit_rom_entries()` computes the words.

## 5. Numerical precision: closed, with four bits of margin

Sweeping the ladder state from 20/16 to 32/28 bits (holding `tanh_entries` at
the shipped 16, because DR 0006's `k_comp` ROM is derived from that table's
bin-0 slope) moves the measured drift by **0.004 pp**. Eight extra bits change
nothing, and *four fewer than we ship* change nothing either: the shipped 24/20
is past the plateau with margin.

<!-- claim: test=model/test_ladder_headroom.py::test_the_state_width_has_no_precision_headroom_and_four_bits_of_margin -->

This closes an argument before it is made: **no candidate algorithm can be
justified on the ground that ours is arithmetic-limited.** It is not.

## 6. Drive: closed

The input-referred level at which the third harmonic reaches −40 dB — where the
designed saturation engages — is **−8.2 dBFS** on the shipped filter.
`docs/discrimination.md` §8.3 has Arturia Mini V3, the dedicated Minimoog
emulation, at −5.7 dBFS and Surge's RK model at −0.6, and reads our agreement
with Mini V3 as the evidence that the gain staging into the `tanh` is in trim.
Re-measured post-DR-0011 it still is, to 2.5 dB.

<!-- claim: test=model/test_ladder_headroom.py::test_the_drive_stage_saturation_point_is_in_the_references_range -->

## 7. Two apparatus defects found on the way, and fixed

Both are the failure mode `CLAUDE.md` names: *a correct instrument in a wrong
state*, whose output looks exactly like data.

**`reference_rigs.OurLadder.fcr` still said "which Surge applies and we do
not".** True when written, false since DR 0011. The rig's `huov_fcr=True`
device multiplies the cutoff by `fcr()` *before* the ROM lookup, which was the
candidate when the ROM did not carry the polynomial — and since DR 0011 it does,
so that device applied the correction **twice**, while
`model/reference_compare.py` still built it as `ours-huovtune` and the report
still printed it as a probe. The precondition is now asserted in `__init__`,
which refuses; `build()` passes `make_g_rom(tune=False)`, so the device means
the pre-DR-0011 candidate again rather than being deleted — the rung-2-to-4
comparison this audit hands on needs a harness whose devices mean what they say.

<!-- claim: test=model/test_ladder_headroom.py::test_the_rig_refuses_to_apply_the_tuning_polynomial_twice -->

**The `ours` rows of `docs/discrimination.md` §8.4 and §8.6 are pre-DR-0011.**
They are correct as history and are labelled as measured on 2026-09-18, but
§8.4's headline "7.92 pp against Surge Type 2's 0.62" is **revision 8's**
filter. The shipped figure at the same operating point is 1.26 pp. Re-deriving
the whole of §8 is a separate job — most of it needs the plugins — so it is
filed into **#239** rather than done here; this document is the shipped filter's
number in the meantime.

## 8. What this audit does not settle

- **It does not compare any candidate algorithm.** That is rungs 2–4, filed as
  **#239** with the harness issue #46's acceptance criteria describe. The one thing this audit
  contributes to that decision is negative and useful: neither arithmetic
  precision nor the coefficient table is our limit, so a candidate has to win on
  the *law* and the *nonlinearity*, where the available margin is about 13 cents
  of tuning and the `tanh` table's fifth harmonic.
- **It holds the `tanh` table at 16 entries throughout**, because issue #46 puts
  the table out of scope and DR 0006's `k_comp` ROM is derived from its bin-0
  slope (measured elsewhere: the small-signal resonant peak moves 23.3 → 20.2 dB
  when the table changes).
- **It does not say the offset of §1 should be corrected**, only that it is the
  largest remaining rung-1 error and that its size is now known. Which operating
  point the cutoff control should be exactly right at is a decision, and one
  constant cannot serve two of them.

## Wrong before it was right

One measurement in this audit was wrong first, and the control caught it rather
than inspection. The coefficient-ROM axis was initially scored on "what does
doubling the table buy" alone — 8.7 cents, a poor buy — and reported as **no
further win available**. Writing the sweep's own control then produced the free
entry-refit number, 21 cents, which is two and a half times larger and costs
nothing; the verdict moved to *available but small*. A single-lever criterion on
a two-lever axis returned a confident wrong answer, and what found it was
running the sweep rather than reasoning about it.

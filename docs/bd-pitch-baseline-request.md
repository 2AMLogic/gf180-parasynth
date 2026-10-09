# BD pitch baseline (#557): what exists, what the build box must run

Status: **instrument qualified; baseline against real recordings NOT yet run.**
`~/dev/refs` (Fischer corpus, `GF180_TR808_REFS`) is absent on the loom worker
that built this, which is a missing capability, not a verdict. The 22.2x /
~230 cents figure in the issue is historical (`docs/scorecard/gate-379/README.md`
s4) and is NOT remeasured here.

## Delivered (this pass)

| piece | file | evidence |
|---|---|---|
| estimator | `tools/pitch_trajectory.py` | `tools/test_pitch_trajectory.py`: 15 pass; against `tools/stubs/pitch_trajectory_stub.py` (flat, never refuses) 15 fail (`PT_IMPL=pitch_trajectory_stub PYTHONPATH=tools/stubs`) |
| baseline script | `tools/bd_pitch_baseline.py` | `tools/test_bd_pitch_baseline.py`: 5 pass (4 refusals + end-to-end on SYNTHETIC stand-in corpus) |

Known answers: steady 50 Hz at 48 and 44.1 kHz; closed-form exponential glide
(58 -> 50 Hz, tg 20 ms) at both rates; onset shifts and gain changes; constant
tuning shift moves `offset` only; independently generated missing-glide
(50.3 -> 49.4 Hz) control; NaN/Inf/silent/cut-onset/too-short/short-decay
REFUSED (short decay raises, it is never a 0-cent glide); a blind (flat)
mutant of the estimator fails the glide qualification.

### Measured apparatus limits (use these as the variability floor)

- edge-limited for the first ~20 ms: a steady 50 Hz tone reads +40 cents at
  t = 0, +14 at 20 ms; metrics therefore start at 20 ms;
- steady-tone worst error from 20 ms: 13.9 cents (48k); glide readout bias on
  a flat tone up to -8.5 cents (44.1k);
- closed-form glide worst error from 20 ms: 12 cents or less (asserted);
- a 4 ms attack retune (130 Hz burst) is below the estimator's resolution
  (window ~15 ms): it is *not separable* here and does not leak into the
  sustained glide (<10 cents). Attribution of the attack retune stays with
  `model/bd_excitation_probe.py`. An `attack_excursion_cents` function was
  written, found unable to see the burst (28 vs 51 cents for plain/burst: the
  wrong way round) and deleted.

### Wrong-then-right (this pass)

1. Early window first set at 10-40 ms: steady tone read +40 cents there. Moved
   to 20-50 ms after the per-frame error profile.
2. Last frames read +80 cents because the zero-phase filter ran on a truncated
   record; fixed by filtering 100 ms of tail beyond the analysed span.
3. First assumed a tg = 20 ms glide reads >100 cents between windows; closed
   form says ~56 cents (most of the glide is over by 20 ms). Bounds follow the
   formula.
4. `attack_excursion_cents` (above).
Rate: 4 corrections in one pass, all caught by the known-answer signals.

### Local probe, not a baseline

Our own BD (render now, `perceptual_gate.condition`, onset at the gate's
convention): glide reads +13 cents (window 20-50 ms vs 80-130 ms), line
49.07 Hz. That is inside the apparatus bias above, so locally it says only
"no glide larger than the estimator's floor in the 20 ms-onward trajectory",
consistent with the historical flat 50.3 -> 49.4 Hz. It says nothing about the
Fischer take.

## What the build box must run (one workload)

```
export GF180_TR808_REFS=~/dev/refs/sounds-tr808-fischer   # + manifest
python tools/bd_pitch_baseline.py --refs $GF180_TR808_REFS \
    --second <a DIFFERENT 808 recording of a BD, e.g. the MARS take used by
              `perceptual_gate.py crosscheck`> --out build/bd-pitch-baseline.json
```
Commit the JSON (it carries commit, dirty flag, sha256 of both takes, estimator
sha, conventions). Read `recording_glide_spread_cents`: it bounds any exact
glide target. Possible outcomes, all publishable: the Fischer glide exceeds
ours by more than apparatus bias + recording spread (defect confirmed, go on);
or it does not (defect refuted at this estimator; keep the historical record).

## Pre-tuning freeze (acceptance bullet 3, second pass)

`docs/bd-pitch-predeclaration.json` is the frozen record, checked by
`tools/bd_pitch_predeclaration.py check` (tests:
`tools/test_bd_pitch_predeclaration.py`). It was committed before any
candidate existed and before the real-recording baseline ran. It holds:

- **Conditions.** 5 development and 14 untouched conditions, in the
  repository's existing vocabulary: Fischer `bd8/BD<TONE><DECAY>.WAV` codes
  from `perceptual_gate.CODES`, and MARS names matching
  `measure_repeatability.CUR_RE`. Model accents come from
  `tom_drop_fit.ACCENT_MAP`, and DECAY knobs from `BD_DECAY_Q`'s table.
  Development holds the gate's target BD5050, one DECAY step, one TONE step,
  one MARS accent cell and one retrigger at 300 ms. Untouched holds DECAY
  00/75/10, TONE 00/75/10, two corners, three MARS cells, retriggers at
  120 ms and at DECAY 10, and accent 2.0.
- **Primary metric.** The median over untouched Fischer conditions of
  |`glide_cents`(808) - `glide_cents`(ours)|.
- **Refused readings (PR #601 review).** `glide_cents` REFUSES at DECAY knob 0
  (tau about 15 ms: the ring is gone by the 80 to 130 ms late window), and two
  untouched Fischer conditions sit there. The record now fixes the rule before
  candidates exist, and `primary_aggregate` implements it. A refusal is never
  a number. A condition whose reference or shipped reading refuses is excluded
  from both medians and listed. A candidate refusal where shipped measured is a
  candidate FAILURE. Fewer than 6 measured conditions, or a measured set that
  no longer holds out a TONE and a DECAY value, gives REFUSED, never a pass.
- **DECAY knob 0 is unqualified (PR #601 fourth review, #602).** Knob 0 does
  not reliably refuse. On a constant-pitch tone (true glide 0) at 48 kHz,
  40 to 43.5 Hz, `glide_cents` ANSWERS +76 to +97 cents. If both sides
  answered, the earlier rule admitted those readings into both medians. The
  record now declares `refused_readings.unqualified_knobs: [0.0]`. A condition
  at that knob (`U-F-T50-D00`, `U-F-T00-D00`) is excluded whatever it reads,
  and is listed with reason `unqualified_knob`. `check()` requires
  `predicted_refusals` to be exactly those conditions, so the 6-condition
  minimum is still derived, not chosen. One function, `measured_set`, picks the
  conditions for both `primary_aggregate` and the satisfiability check, so the
  two cannot drift. A malformed readings entry (not an object) REFUSES rather
  than raising.
- **Minimum improvement.** The formula is
  `2 * max(13.9, 8.5, 12) + recording_glide_spread_cents`. The three floors
  are quoted from this document, and the checker verifies the quotes. The
  spread is read from the baseline JSON when the rule is used. If that sum
  exceeds the largest improvement the SHIPPED untouched median allows (a perfect
  candidate; equality still satisfies `improvement >= minimum`),
  `min-improvement --readings` REFUSES. Without `--readings` it prints the
  threshold and the baseline's one development take as a diagnostic and
  refuses to claim satisfiability: that take is a different statistic from the
  one acceptance uses. Non-finite, boolean, string or negative baseline
  numbers, and a negative evaluated threshold, REFUSE. The rule is not
  loosened in that case.
- **Preservation.** Each limit names a probe or test in the repository: decay,
  level, attack, click, tail/DC, the other 15 voices (bit-exact) and settled
  pitch.
- **Parameters.** None are proposed, so no sensitivity-registry entry is
  added.

Stated limits of the split:

- **Accent.** Only two BD accent levels exist with a recording (MARS A and B),
  and the differential metric needs both. So accent *levels* cannot be held
  out with a reference, only MARS cells can.
- **Retrigger.** No recording exists on either corpus, so retrigger
  conditions carry no glide bar.
- **TONE.** The model has no BD TONE control. TONE is a reference-side axis
  only.

Start red: before the record existed, the suite gave 25 errors, 1 failure
and 1 pass. Against `tools/stubs/bd_pitch_predeclaration_stub.py`, which
accepts everything and folds a refusal into the median as 0 cents, it gives
65 failures and 6 passes. The 6 passes are record-content assertions and sweep
measurements that do not call the validator, which a permissive validator
cannot fail. Real validator: 71 pass (44/3 against 47 before the second
review). The refused-reading
tests were first run against the record without the rule: 19 failed. Each injected control is caught for its own reason:
overlap, an axis not held out, a missing probe file or symbol, a dropped
property, a bare constant (number, string, or a formula with no terms), a
rule without the baseline spread, a floor not found in its cited document,
off-vocabulary conditions, a MARS name not matching `CUR_RE`, and a registry
claim without a parameter, a removed refused-reading rule, an unsatisfiable
or malformed measured-condition minimum, a too-few outcome that does not
refuse, and a refusal read as a 0-cent reading or as a 0-cent error (both
move the known-answer medians).

### Wrong-then-right (this pass)

1. **Accent axis.** The first draft's untouched set used the same accent
   values as development, so it held nothing out on accent. The axis-held-out
   rule was written while drafting, and checking the draft against it exposed
   the problem. Fixed by adding the reference-less accent-2.0 condition and
   stating the limit above.
2. **Click.** The draft assumed `impulse` measures our click. #558 (merged
   during this work as `d4d4b682`) measured that for BD and the toms/congas,
   `impulse` is dominated by the gate's 44.1 to 48 kHz rate path. The click
   limit is a candidate-minus-shipped difference, so that common term cancels
   to first order. The caveat is recorded, and #588 owns the question.
3. **Onset phase (measured).** #558 found the gate's `pitch_shape` reads onset
   phase. The same check on `glide_cents`, using a constant-pitch decaying
   sinusoid with sin-phase versus cos-phase onset, first read 10.4 cents at
   DECAY knob 5 (one knob, wrong as a worst case), then 17.5 cents on a
   five-knob, three-pitch grid (also a coarse-grid worst case; margin "1.6x").
   The reviewer's finer scan found 34.2 cents at knob 0.6, 52 Hz.
   `tools/bd_glide_phase_sweep.py` now fixes the grid (f0 40 to 65 Hz in
   0.5 Hz steps, both rates, the record's own knobs, plus an edge scan of knobs
   0 to 1.5 in 0.1 steps) and a test pins its resolution. Result: at the knobs
   the declared conditions use the worst is **13.63 cents** (knob 2.5,
   51.5 Hz, 48 kHz; margin 2.0x), below the rule's apparatus part (27.8). Over
   the whole range it is **34.21 cents** near the refusal edge, which EXCEEDS
   27.8; the freeze's claim is scoped to the declared knobs. The same scan
   shows `glide_cents` does not refuse a zero-glide tone at knob 0.6 (it reads
   -68 / -34 cents), nor at knob 0 below 44 Hz at 48 kHz (+76 to +97 cents).
   Its `MIN_FRAMES` precondition is too loose. This is filed as #602 and not
   fixed here. #602 also reports an `F_REF` bias of up to 15.7 cents within
   48 to 56 Hz at the declared knobs, which the 13.9-cent steady-tone floor
   does not cover. The knob-0 half is closed for this freeze by item 7.
4. **Refused readings (caught in review).** The record said what a refused
   retrigger reading means, but not a refused Fischer one. The same sweep
   showed knob 0 refuses (from 44 Hz up at 48 kHz, everywhere at 44.1 kHz), and
   two untouched Fischer conditions are at knob 0. Fixed by the
   refused-readings rule above.
5. **Citations.** Every cited file and symbol was re-checked against
   `origin/main` after it advanced. None was missing.
6. **Baseline numbers and satisfiability (caught in review).** `min-improvement`
   accepted a negative spread, an infinite deficit, a boolean and a numeric
   string, and judged satisfiability on one development take while acceptance
   uses the untouched median (a development deficit of 20 with untouched 100,
   and the reverse, each gave the wrong answer). Fixed at point of use with the
   reviewer's exact inputs as controls; `<` became `<=` to match the pass rule.
7. **Knob-0 readings admitted (caught in review).** Item 4 treated knob 0 as a
   knob that refuses. It refuses only from 44 Hz up. Below that it answers
   wrongly, and the record's own `prediction_basis` said such readings were
   included when both sides measured. The two knob-0 conditions are now
   excluded by declaration (`unqualified_knobs`), whatever they read. The
   reviewer's +81.13 / +96.80 cent pair is the control, and it fails on the
   previous head.

Rate: 3 corrections to the record before the first review (1-3), plus the
citation check (5), which needed no change. Review then caught three more
rounds: the refused-reading rule (4), the onset-phase worst case twice (3: a
single knob, then a coarse grid), and the baseline-number and satisfiability
defects (6), and then the admitted knob-0 readings (7). That is 8
wrong-then-right corrections in total, 5 of them found by the reviewer. Three
were the same mistake (a coarse sample read as the worst case). The mutation
controls are committed as `tools/bd_pitch_predeclaration_mutants.py`. It runs
on a scratch copy of the tree and removes one guard per mutant. All 12 mutants
turn their focused controls red. Nine are from the third round: bool accepted
as a number, negative spread, negative threshold, non-finite deficit, `<` at
equality, satisfiability from the development take, the minimum-count
derivation unchecked, the TONE holdout branch removed, and a 5 Hz pitch grid.
Three are from the fourth round: a knob-0 condition admitted, `predicted_refusals`
not tied to `unqualified_knobs`, and the non-object readings-entry guard
removed.

## Not done (needs a human/coordinator decision or the box)

- baseline on real recordings (above); second-recording choice and its hash.
  The frozen record also needs the MARS BD cells C03, A01, E05 and F06 (A and B
  accents) on the box, for the accent differential;
- sensitivity-registry entries for any proposed parameter (none proposed: no
  repair is selected, and none should be before the baseline);
- the repair, model/RTL/I2S equivalence, deadlines, image inclusion;
- the multi-property MOVED/BLIND matrix (only one property so far);
- the defect-class search (pitch estimation / live-window / coefficient
  restoration elsewhere): not done; `perceptual_gate._pitch_track` smooths over
  40 ms at 50 Hz and was not changed here.

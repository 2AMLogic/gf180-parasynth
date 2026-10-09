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
- **Minimum improvement.** The formula is
  `2 * max(13.9, 8.5, 12) + recording_glide_spread_cents`. The three floors
  are quoted from this document, and the checker verifies the quotes. The
  spread is read from the baseline JSON when the rule is used. If that sum
  exceeds the present deficit, `min-improvement` REFUSES. The rule is not
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
accepts everything, it gives 26 failures and 3 passes. The 3 passes are
record-content assertions that a permissive validator cannot fail. Real
validator: 29 pass. Each injected control is caught for its own reason:
overlap, an axis not held out, a missing probe file or symbol, a dropped
property, a bare constant (number, string, or a formula with no terms), a
rule without the baseline spread, a floor not found in its cited document,
off-vocabulary conditions, a MARS name not matching `CUR_RE`, and a registry
claim without a parameter.

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
   phase. The same check on `glide_cents`, using a constant-pitch 49.4 Hz
   decaying sinusoid with sin-phase versus cos-phase onset, reads
   10.4 to 10.5 cents of spurious glide at 48 kHz and 6.6 at 44.1 kHz. That is
   below the rule's apparatus part (27.8 cents), so onset phase alone cannot
   pass the rule. This is now a test.
4. **Citations.** Every cited file and symbol was re-checked against
   `origin/main` after it advanced. None was missing.

Rate: 3 corrections to the record before freezing (1-3), plus the check in
4, which needed no change. Item 1 was caught by checking against the rule,
item 2 by reading a newly merged sibling, and item 3 by a known-answer
signal.

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

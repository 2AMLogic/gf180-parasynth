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

## Not done (needs a human/coordinator decision or the box)

- baseline on real recordings (above); second-recording choice and its hash;
- frozen development / untouched TONE-DECAY-accent-retrigger split, primary
  metric, minimum improvement above the floor above, preservation limits;
- sensitivity-registry entries for any proposed parameter (none proposed: no
  repair is selected, and none should be before the baseline);
- the repair, model/RTL/I2S equivalence, deadlines, image inclusion;
- the multi-property MOVED/BLIND matrix (only one property so far);
- the defect-class search (pitch estimation / live-window / coefficient
  restoration elsewhere): not done; `perceptual_gate._pitch_track` smooths over
  40 ms at 50 Hz and was not changed here.

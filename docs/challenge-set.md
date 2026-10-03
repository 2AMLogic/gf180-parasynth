# The estimator challenge set (#521, the bounded first experiment of #158)

Instrument: [`tools/probes/challenge_set.py`](../tools/probes/challenge_set.py).
Definition: [`docs/challenge-set.json`](challenge-set.json) (frozen).
Results: [`docs/challenge-set-results.json`](challenge-set-results.json).
Tests: [`tools/probes/test_challenge_set.py`](../tools/probes/test_challenge_set.py).

```sh
python3 tools/probes/challenge_set.py check     # definition rules + are the results current?
python3 tools/probes/challenge_set.py report    # re-render the committed results
GF180_TR808_REFS=<fischer clone at 85fbecf> \
python3 tools/probes/challenge_set.py run --json docs/challenge-set-results.json
```

**This validates the judges, not the instrument.** Every number here is about
how an estimator behaves, and nothing here says the drum kit or the voice is
right. The property columns are graded against the *physics* of each
perturbation (`perturbation_ladder.py`'s frame). No external truth about the
recording is used, because nobody has one.

<!-- RESULTS -->

## What is frozen

The challenge set is data, and a re-freeze shows up as a diff.
`definition_sha256` covers everything in the definition except the `freeze`
block, including every anchor's sha256. Changing any of it without a re-freeze
fails rule C1.

| part | from | frozen as |
|---|---|---|
| synthetic fixture subset | #517 `estimator_fixtures.catalogue()` | the seven families, 170 fixtures, and one sha256 over every fixture's label and samples |
| perturbation strengths | #518 `perturbation_ladder.PERTURBATIONS` | a copy of every strength tuple. C7 fails if the live module differs |
| permitted transforms | #519 `permitted_differences.TRANSFORMS` | the transform names, trial counts, and a seed namespace of their own (5,210,000) that is disjoint from that module's calibration, validation and false-alarm streams |
| genuine corrections | `perturbation_ladder.perturb_resample` | four tape-speed ratios inside the machine's own 2.74 % f0 spread. C7 fails a ratio outside that spread |
| real anchors | lineage per #520 | 24 anchors in four categories, each pinned by the sha256 of its bytes |
| estimator resolutions | `perturbation_ladder.ESTIMATOR_CASES` | a copy. C7 fails if it drifts |
| `mel_dac` | `audio_distance_floor.py` | `DAC_MEL`, `LOG_FLOOR_REL`, the floor definition, the detection rule, the K grid |

### The anchors, and the split

| category | calibration | validation | development |
|---|---|---|---|
| tonal drum (8) | BD5050, LT50, HT75, RS | BD2550, MT25, LC50, CL | |
| noisy drum (6) | SD5050, SD7525, MA | SD2575, SD0050, CP | |
| metallic (6) | CB, OH50, CY5025 | CH, OH10, CY7550 | |
| sustained synth (4) | | | Surge drive +0 dBFS, Surge drive −12 dBFS *(software)*; Legowelt ×2 *(hardware, unfrozen)* |

**The split is by source recording, and recording identity is content.**
Rule C5 compares sha256s as well as paths. A byte-identical re-pressing (the
`dirt-samples-808` directories are exactly that) cannot sit in a second split
under a new name. A path comparison alone would let it through, and the test
suite carries that input.

**The split must be admissible under #520's groups** (rule C4). Calibration
uses `threshold-calibration`, validation uses `held-out-validation`, and
development uses `analyzer-development`. Each anchor's pack must list the
group in its `roles`. A `claimed` lineage never backs validation.

The Fischer lineage is in both of the first two groups. Its own
`role_overlap_why` says the separation in force is at **setting** level, and
this file uses that separation and no other. Calibration and validation hold
different recordings of one machine. That separates settings, **not machines**.

**Sustained synth is the weak category, and the table says why.** No
hardware sustained-synth recording can be reached from a Loom host. The two
Legowelt Minimoog files (serial 5529, `analyzer-development` only) are in the
definition but unfrozen, with a stated reason, so they are REFUSED on every
run until an operator with the pack runs `freeze`. The two Surge clips come
from `refprofile/frozen-cache.zip`. They are committed and hashed, but they
are a **software** reference, outside corpus-lineage by that manifest's own
`out_of_scope` entry. They are admitted to the development split only (C4
fails them anywhere else), and nothing about them is evidence about hardware.

**Excluded**: `cy8/CY2500.WAV`, D14B's holdout recording in
`tools/probes/hihat/hh_probe5.py` (C6 enforces this), and all of
`dirt-samples-808`.

## What a matrix cell means

Each cell is an estimator crossed with a failure mode, with its counts over
(anchor, rung) pairs:

| column class | label | meaning |
|---|---|---|
| defect | **MOVED** | the reading moved beyond the declared resolution (for `mel_dac`, beyond 1 × its floor) on at least one rung, so it protects |
| defect | **ABSTAINS** | nothing moved, but the defect made the estimator refuse. That is protection: a refused required metric makes a scorecard case NO_VERDICT, never PASS |
| defect | **BLIND** | there are scored rungs, and none moved and none refused |
| permitted | **STILL** / **FALSE-ALARM** / **REFUSES** | correct / moved / a false no-verdict |
| correction | **TRACKS** / **MISTRACKS** | `decay_tau` moved by the closed-form τ/a, or by something else |
| correction | **NOT-DERIVABLE** | a ratio estimator under resampling, where `perturbation_ladder.RELATION_OVERRIDES` claims no closed form |
| correction | **FLAGS-CORRECTION** | `mel_dac` read a correction the board passes above its floor |
| any | **NO-VERDICT** | nothing was scored |

**NO-VERDICT is not BLIND.** A tail-noise injection that starts after the
closed hat has already ended changes nothing. Scoring it as a miss would
blame the estimator for a defect that never happened. A transform that leaves
the array identical is therefore a no-verdict, and the test suite holds the
input that shows this. A no-op rung (a sweep's identity point, such as a clip
at 1.0 × peak) is not counted at all.

## The `mel_dac` floor, defined where the number is read

**floor(anchor) = mel_dac(p, shift(p, 1))**. Here *p* is the prepared,
peak-normalised anchor, and *shift(p, 1)* is *p* delayed by one sample and
zero-padded back to length.

This is the floor `docs/audio-distance-metrics.md` §7 used. There it read
0.0085, on our own deterministic BD render. Here it is measured on each real
anchor. It is an **apparatus** floor: what the distance reads for a change
nobody could hear. It is not the machine's session-to-session floor (0.264 in
that document, and constructed), and it is not an our-versus-reference
baseline, which has never been measured. "N× floor" without the floor's value
and this definition beside it is not a result. Every row in the results
carries the distance, the floor and the ratio together.

Detection is `distance > K × floor`. K is swept over the frozen grid rather
than chosen. **K_cal** is the smallest K at which the *calibration* split
shows no false alarms on its permitted and correction columns. Validation
rates are read at K_cal and nowhere else. If no K in the grid achieves this,
K_cal is REFUSED, and the validation split gets the whole grid rather than a
headline rate.

Two conventions shape what `mel_dac` can show:

- **Peak normalisation makes gain an exact invariance by construction.** The
  gain columns cannot false-alarm on `mel_dac`, and that tells you nothing
  about `mel_dac`.
- **It is called at the anchor's own sample rate.** `audio_distance_floor.
  distances()` calls `_mel_dac` with its default `sr=48000`, including on the
  44.1 kHz Fischer files. That puts its mel band edges 8.8 % off where it was
  used on them. This file passes the true rate instead. The effect on a
  floor-sized reading is small (0.01185 against 0.01195 on BD5050), but it is
  a correct instrument in a wrong state, and it is recorded here so it is not
  copied.

## The policy: frozen, and re-measured whenever an estimator changes

1. **The challenge set is frozen** in `docs/challenge-set.json`. Changing an
   anchor, a strength, a transform, a correction or a resolution is a
   re-freeze. It changes `definition_sha256`, so it is visible in review.
   `freeze` refuses to replace a frozen sha256 with different bytes, because
   that would be a different recording under the same name.
2. **The results carry the apparatus they were measured under.** That is the
   content hash of `model/audio_measure.py` and `tools/run_case.py`, the two
   estimator modules `tools/scorecard.py` `compare()` already treats as
   apparatus (the import asserts they are still in `scorecard.APPARATUS`),
   plus the probe files that define the challenge.
3. **When any of that changes, the results are STALE**, and `check` exits 2.
   The remedy is `run`. It re-measures the **retained** anchors, which are
   the same audio because they are pinned by sha256, under the repaired
   estimator. This is #157/#159's rule from the scorecard (`compare()` returns
   INCOMPARABLE with "re-measure the baseline before comparing" across
   apparatus) applied to the judges themselves. A repaired estimator's old
   numbers are not evidence about the new one.
4. **It is not a pytest gate, on purpose.** If it were, every estimator PR
   would go red on any host without the corpus, and CI has no corpus. That is
   the unsatisfiable-gate failure recorded in `docs/failure-modes.md`. The
   tests do enforce that the definition is frozen and well-formed, and that
   the committed results describe the committed definition.

**Out-of-band, and not closed by any run:** whether this challenge set stays
meaningful as the estimators evolve is a claim about the future. A green run
today cannot establish it. What is established is the mechanism: staleness is
detected, and re-measurement is one command. Whether the anchors, strengths
and columns are still the right ones after the next estimator repair is a
judgement someone has to make then.

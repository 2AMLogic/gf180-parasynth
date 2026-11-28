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

**The sound requirement this unblocks.** Drum-kit qualification (#282) passes
or fails renders on these estimators. This table answers which defect classes
a drum render can carry past them unseen, and which estimators cannot be
trusted on which voices. Section 3 below answers that, voice family by voice
family.

## Results (definition `62c7e497…`, full results digest `f96fa833…`)

Run on 22 of 24 anchors. The two Legowelt hardware anchors are REFUSED as
unfrozen. That makes the run PARTIAL, and it is labelled PARTIAL. The corpus
is the Fischer set at upstream commit `85fbecf`, each file checked against
its frozen sha256.

**Re-run deterministically.** Two complete runs on the same inputs produced
the same `full_results_sha256` and byte-identical result bodies. Provenance
is the only block excluded from that comparison.

### 1. The coverage matrix

Each cell gives `moved/scored` over (anchor, rung) pairs. *r* is rungs the
estimator refused, and *A* is anchors refused at base. Run
`challenge_set.py report` for the full table.

| failure mode | band_pair_db | tone_ratio_db | decay_tau | inharmonic_fraction_db | mel_dac (K=1) |
|---|---|---|---|---|---|
| quantisation | NO-VERDICT A20 | **MOVED** 26/31 r5 | abstains 0/21 r15 | **MOVED** 10/12 | **MOVED** 121/132 |
| clipping | NO-VERDICT A20 | **MOVED** 14/25 r4 | **MOVED** 2/2 r28 | **MOVED** 10/10 | **MOVED** 109/109 |
| broadband noise | NO-VERDICT A20 | **MOVED** 22/28 r8 | abstains 0/22 r14 | **MOVED** 10/12 | **MOVED** 121/132 |
| tail noise (ladder) | NO-VERDICT A20 | NO-VERDICT | NO-VERDICT | NO-VERDICT | NO-VERDICT |
| narrowband tone | NO-VERDICT A20 | BLIND 0/36 | abstains 0/14 r22 | **MOVED** 12/12 | **MOVED** 127/132 |
| escape: 12 kHz tone −40 dBFS | NO-VERDICT A20 | BLIND 0/6 | abstains 0/2 r4 | **MOVED** 2/2 | **MOVED** 22/22 |
| escape: 6-bit requantise | NO-VERDICT A20 | **MOVED** 5/5 | abstains r6 | **MOVED** 2/2 | **MOVED** 22/22 |
| escape: tail noise −45 dBFS | NO-VERDICT A20 | **MOVED** 4/5 | abstains 0/3 r3 | **MOVED** 2/2 | **MOVED** 20/20 |
| gain (ladder) | — | still 0/36 | still 0/36 | still 0/12 | still 0/132 *(by construction)* |
| delay 0.1–10 ms | — | **FALSE 9/28** | still 0/30 | **FALSE 7/10** | **FALSE 110/110** |
| polarity | — | still 0/6 | still 0/6 | still 0/2 | still 0/22 |
| leading silence 5–120 ms | — | **FALSE 9/15** | still 0/18 | **FALSE 6/6** | **FALSE 66/66** |
| gain (#519 draws) | — | still 0/18 | still 0/18 | still 0/6 | still 0/66 *(by construction)* |
| genuine pitch ±2.74 % | — | not derivable (17/24 moved) | **TRACKS 24/24** | not derivable (8/8 moved) | **FLAGS 88/88** |

What it says, estimator by estimator:

- **band_pair_db has no real-anchor coverage at all.** It refuses at base on
  all 20 drum anchors. That includes RS, the voice #518's ladder reads it on.
  In 15 cases the reason is detuning below the validated domain, and in 5 it
  is an A²τ decay bias outside it (RS −3.8 dB, CB 1.4, CY5025 10.8, CY7550
  16.3, SD2575 18.4 dB). Every refusal is legitimate by its own declared
  domain. The consequence is that on the real kit, its guard leaves nothing
  for the rest of the table to say.
- **decay_tau answers on 6 of 22 anchors** (BD5050, BD2550, LT50, MT25, HT75,
  LC50) and refuses the other 16 with stated reasons. Where it answers:
  - It never false-alarms (0 of 108 permitted rungs).
  - It tracks the genuine pitch correction's closed-form τ/a on 24 of 24.
  - It protects against quantisation, noise and tone by refusing rather than
    by moving: no rung moved, and 15, 14 and 22 rungs refused respectively.
    That is protection only because the scorecard turns a refused required
    metric into NO_VERDICT.
  - Known answers (#517, 170 frozen fixtures): 37 PASS with a parsed error,
    bias +0.05 %, mean |error| 0.16 %, max 0.93 %, no FAIL.
- **tone_ratio_db** (metallic remit, 6 anchors):
  - It sees broadband defects and is **blind to both tones**, including #158's
    12 kHz escape (0 of 6).
  - It **false-alarms on leading silence** on 9 of 15 rungs, and on delay on
    9 of 28. Recomputed outside this file: CB reads 9.959 dB, and with 10 ms
    of leading zeros it reads 10.141 dB. That is +0.18 dB against a declared
    resolution of 0.05 dB.
  - 11 rank reversals. For example, OH50's error is 19.96 dB when clipped to
    0.5 of peak, and 13.67 dB when clipped harder, to 0.2.
  - Outside its remit it answers instead of refusing on 11 of 16 anchors. On
    BD5050, which has no 540/800 Hz lines, it reports 0.095 dB.
  - #517 does not cover it. Its known-answer evidence is
    `estimator_domains.py` §4(a), which is cited here and not re-run.
- **inharmonic_fraction_db** (BD remit, 2 anchors):
  - It moves on every defect column, including the narrowband tone at
    −80 dB re peak. That tone sits at 2 kHz, which is inharmonic to 49.4 Hz,
    so the ladder's premise that the tone is "placed outside anything the
    estimator reads" does not hold for this estimator. *(Mechanism: inferred.
    The tone's position against the BD harmonics was computed; its
    contribution to the estimate was not measured.)*
  - It **false-alarms on leading silence** (6 of 6) and on delay (7 of 10).
  - Outside its remit it answers on 13 anchors (for example LC50 at −52.3 dB).
  - Known answers: 6 PASS, bias +1.1 %, max 3.6 %.

### 2. mel_dac, with absolute values

Floors on the 20 Fischer anchors run from **0.0113 to 0.0373 nats**. The two
Surge clips read 0.0114. For comparison, `docs/audio-distance-metrics.md`
measured 0.0085 on our own render, under the same definition.

| column | distance, nats (min / median / max) | × that anchor's floor (min / median / max) |
|---|---|---|
| escape: 12 kHz tone −40 dBFS | 0.139 / 0.447 / 0.558 | 3.7 / 24.7 / 47.2 |
| escape: 6-bit requantise | 0.903 / 1.99 / 2.88 | 25 / 104 / 252 |
| escape: tail noise −45 dBFS | 0.101 / 1.68 / 3.08 | 5.2 / 78 / 265 |
| delay 0.1–10 ms *(permitted)* | 0.038 / 0.275 / 0.914 | 2.5 / 14.6 / 61.6 |
| leading silence *(permitted)* | 0.377 / 1.14 / 3.47 | 17 / 63 / 221 |
| polarity, gain *(permitted)* | 0 (gain ≤ 3.7e-14) | 0 |
| genuine pitch ±2.74 % *(correction)* | 0.290 / 0.429 / 0.591 | 12 / 23 / 41 |

**"238× floor" in context.** That figure was 2.029 nats over a 0.0085 floor,
measured on our own BD render. On the real BD5050 the same 6-bit escape reads
**2.395 nats** over a floor of **0.01185** (202×). The 12 kHz tone reads 0.536
nats (45×), and a genuine ±2.74 % pitch correction reads **0.365–0.393 nats**
(31–33×). On this anchor, a correction the board passes reads about 70 % of
the HF-tone escape's distance. On 18 (anchor, escape) pairs a correction
reads *farther* than the escape does. The worst of these is on the snares:
the 12 kHz tone on SD5050 reads 0.331 nats, and pitch ×1.0274 reads 0.518.

**Rates.** These are from the K sweep, as detected/scored.

| K | calibration: defect / permitted / correction | validation: defect / permitted / correction |
|---|---|---|
| 1 | 248/260, 80/180, 40/40 | 246/259, 80/180, 40/40 |
| 10 | 211/260, 66/180, 40/40 | 207/259, 64/180, 40/40 |
| 30 | 176/260, 34/180, 4/40 | 163/259, 30/180, 4/40 |
| 100 | 81/260, 10/180, 0/40 | 74/259, 6/180, 0/40 |
| **300 = K_cal** | 7/260, 0/180, 0/40 | **5/259, 0/180, 0/40** |
| 1000 | 0/260, 0/180, 0/40 | 0/259, 0/180, 0/40 |

**Read at K_cal, the frozen rule gives `mel_dac` a validation detection
rate of 5 of 259 injected-defect rungs (1.9 %)**, with no false alarms. The
false alarms that force K up to 300 are all leading silence: in calibration
they reach 178× floor (SD7525, 3.47 nats over 0.0195).

**Conditional result, not part of the frozen rule.** Leaving the two
alignment columns out gives K = 100. Validation there detects 74 of 259 defect
rungs, with 6 of 180 permitted false alarms and 0 of 40 corrections flagged.
This alternative selection was added *after* the first run showed alignment
alone sets K_cal, and no alignment step exists to make it real. It agrees
with `docs/audio-distance-metrics.md` §6 (alignment gates everything). It
does not change §8's "guard only" contract. It does add a number to that
contract: as a guard on real recordings, without alignment, `mel_dac` flags
a pitch change the board passes at K ≤ 10 every time.

By instrument family at K = 1 (defect, then permitted): tonal drum 208/208
and 64/144; noisy drum 145/156 and 48/108; metallic 141/155 and 48/108;
sustained (Surge, software) 48/50 and 16/36. A diagnostic starts an
investigation. None of these numbers names a coefficient.

### 3. What this changes for the kit, and what it does not

- Of the four property estimators, **nothing sees a 12 kHz feedthrough tone
  on a non-BD voice**. tone_ratio_db is blind to it on 6 of 6 metallic
  anchors. decay_tau never moves on it (2 rungs scored, 4 refused), and
  band_pair_db refuses everywhere. On the BD, inharmonic_fraction_db sees it.
- **Leading-silence handling is a live defect class in two estimators**
  (tone_ratio_db and inharmonic_fraction_db), not only in mel_dac. A drum
  render compared with a reference whose onset lead differs reads a property
  difference that is not in the sound. `run_case.prepare` normalises the lead
  for the board's own cases. Any reader that skips it inherits this.
- Nothing here changes RTL or the model. It changes how far the next
  drum-repair claim can lean on these four estimators.

### Wrong before right, in this session

Four results were wrong before they were right. None was caught by inspection
of code. One was caught by a test, and three by reading the first real run's
report for values that could not be true.

1. A test asserted that decay_tau MOVES on clipped records. In fact it
   refuses on every clipped rung. The matrix gained ABSTAINS, which is
   distinct from BLIND.
2. mel_dac read **BLIND 0/42** on tail noise. On seven anchors the transform
   had changed only the last one or two samples, which no STFT frame reaches,
   so the distance was exactly 0.0. A change under 1 ms is now NO-VERDICT,
   and that input is a committed test.
3. The sustained category had **no mel_dac at all**, because `run_case.prepare`
   refuses a held tone. Sustained anchors are now peak-normalised only.
4. band_pair_db's cells read "NO-VERDICT nv0", as if empty, when it had
   refused on 20 anchors. Base refusals are now counted in the cell.

A fifth problem was found and **not** fixed here, because the fix belongs to
the file that owns it: `audio_distance_floor.distances()` computes mel_dac at
48 kHz on the 44.1 kHz Fischer files (#533).

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

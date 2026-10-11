# Residual drum DC coupling: non-cymbal screen, CH / RS detail (#152)

Diagnostic code and evidence only. No production enable, no RTL, no image
change. Cymbal carry-through is #510/#552/#553; MA/RS brightness is #556;
modal arithmetic is #220/#350; toms and congas are #558/#591-#593. Nothing
here repairs any of them.

Regenerate: `python3 tools/probes/residual_dc.py --screen|--detail|--controls|--reference`.
Output-stage quantisation (#618): `--output-floor` per condition, `--floor-verdict DEV.json CONFIRM.json`.
Controls: `python3 -m pytest tools/probes/test_residual_dc.py -q` (123 tests).
Per-subject ending: `python3 tools/probes/residual_dc.py --verdict DEV.json CONFIRM.json`, reading the
`--screen --rows-out` records of both conditions (refuses a missing/duplicate condition, a dirty
record, a record without a valid 40-hex source commit, mixed commits, moved limits, a malformed
row, or a non-REFUSED row whose `mean_frac` is not a finite real; exit 2). Each guard's defeating
inputs are permanent tests; `red-verdict-guards.txt` records them failing before the fix.

## Status: PARTIAL, and the missing part is stated, not hidden

| item | state |
|---|---|
| apparatus qualified on constant-offset / short-burst / zero-mean / added-HF ground truth, MOVED/BLIND matrix | **done**, `controls-matrix.txt`, 123 passing |
| start-red record | **done**, `red-start.txt` (estimator stubbed to NaN, fixtures executed, assertions failed); `red-h-ch2.txt` (the H_CH2 defeater against the one-sided rule); `red-verdict-guards.txt` (the `--verdict` provenance / non-finite / singleton defeaters) |
| dev condition, CH / RS / BD / HT | **done**, `screen-dev-subjects.txt` / `.json`, `detail-dev.txt`, rendered from clean commit `cae21c566571` |
| per-subject ending produced by the tool | **done**: `--verdict`; on the dev record alone it REFUSES (confirm missing), as it must |
| dev condition, the other 11 non-CY voices | **NO VERDICT: not run on this host** (batch below) |
| confirm condition (untouched), all voices | **NO VERDICT: not run on this host** (batch below) |
| external reference comparison | **REFUSED**: no corpus on this host, and see "What recordings can establish" |
| `make verify`, `make controls` | **NO VERDICT: not run** (host rules); pending on the build box |

Pending batch (build box, `--jobs 2`; also `docs/residual-dc/batch-spec.txt`). The last
line produces every subject's ending from the two screen records:

```
python3 tools/run_all.py --jobs 2 --timeout 3600 --json build/residual-dc-batch.json \
    "python3 tools/probes/residual_dc.py --screen --condition dev --rows-out build/residual-dc-screen-dev.json" \
    "python3 tools/probes/residual_dc.py --screen --condition confirm --rows-out build/residual-dc-screen-confirm.json" \
    "python3 tools/probes/residual_dc.py --detail --condition dev" \
    "python3 tools/probes/residual_dc.py --detail --condition confirm" \
  && python3 tools/probes/residual_dc.py --verdict build/residual-dc-screen-dev.json build/residual-dc-screen-confirm.json
make verify && make controls
```

## Declared before any datum from THIS probe was read

`python3 tools/probes/residual_dc.py --declared` prints every limit. They landed
in the commit with the stubbed estimator. The class limits were placed from the
**prior** `dc_blocker.py --screen` beta values for these same voices (CH 0.97,
RS 0.38, ...), so `dev` is not a fully clean test of them; `confirm` is. Frozen conditions: `dev` (2.4 s,
gain 0.45, velocity 1.0) and `confirm` (4.8 s, gain 0.75, velocity 0.6). Confirm
was **not** run; limits are not to be revisited after it is read. A class that
differs between the two is reported as NO VERDICT, not tuned.

Two statistics decide whether sub-20 Hz energy is a standing offset or a finite
burst's skirt, and both must agree or the class is MIXED (REFUSED as a class):
`beta` (share of sub-20 Hz energy below the blocker corner, 7.46 Hz) and `S`
(flatness of 0-20 Hz, `phi * (2n-1)`: 1 for a burst much shorter than 50 ms, 79
for a standing offset). Both are read on a fixed 2.0 s **onset-aligned** window,
so leading silence and trailing padding cannot move them.

## Production baseline (recorded in every file header)

`DrumsFx().couple_en == 0` after reset (A_COUPLE = 0x30); nothing in the shipped
writes sets it. All "baseline" columns are therefore the uncoupled block. The
"coupled" columns write A_COUPLE = 1 at frame 0 -- a **diagnostic toggle**, an
experiment. Raw bus (pre-clamp) and clamped int16 output are both classified;
no voice clips at either condition's gain, so they agree.

## Dev results (4 voices; NOT confirmed)

| voice | role | class | beta | S | sub-20 Hz | window mean | coupling toggle, sub-20 |
|---|---|---|--:|--:|--:|--:|--:|
| CH | subject | OFFSET | 0.973 | 8.33 | -102.4 dBFS (= a 0.25 LSB constant) | -0.081 LSB, 0.00 % of peak | -5.03 dB (bound 11.66) |
| RS | subject | SKIRT | 0.379 | 0.99 | -59.0 dBFS | +4.1 LSB, 0.07 % | -2.70 dB (bound 2.71) |
| BD | control | MIXED | 0.433 | 14.18 | -51.8 dBFS | -35.6 LSB, 0.48 % | -3.10 dB |
| HT | control | SKIRT | 0.358 | 0.25 | -61.2 dBFS | +1.6 LSB, 0.02 % | -2.57 dB |

Absolute band energy accompanies every share (`detail-dev.txt`): with the toggle
on, 5-20 kHz moves +0.00 / +0.00 / -0.17 / +0.01 dB (CH / RS / BD / HT) and the
HF share rises on CH, RS and HT are read as LF removal. **No HF was added to any
voice.** BD's -0.17 dB HF is a loss, not a gain.

**Measured effects (evidence: model, bit-exact integer render):**

- RS: S = 0.99 against 1.00 for a flat spectrum. Its 0-20 Hz band is the
  skirt of a finite burst. The steady-state bound is 2.71 dB and the toggle
  delivers 2.70 dB. The required 6 dB (#165) is not reachable and, by the
  construction above, was never a property of this voice.
- CH: the 0-20 Hz content is a standing level of about 0.25 LSB (mean -0.081
  LSB, 0.00 % of peak). It is below the declared 2 % magnitude, so even if
  `confirm` reproduces the OFFSET class the ending is "OFFSET-like, no defect
  established". **CH's S of 8.33 sits 4 % above the 8.0 limit**; it may flip to
  MIXED on `confirm`, and that is an outcome, not a failure.
- CH's attenuation shortfall (5.03 dB measured against an 11.66 dB bound) is
  **not** the blocker starting from rest. An independent float one-pole run
  from rest predicts 11.66 dB, 6.63 dB away from the render (H_CH, predeclared
  tolerance 1.0 dB, NOT SUPPORTED).

**Mechanism hypothesis (evidence: intervention on a surrogate, not hardware):
H_CH2 is NO VERDICT on dev.** The hypothesis is that the shortfall is an integer
quantiser. The surrogate re-blocks the pre-coupling buses with `DcBlockFx` at the
declared widths and is **bit-exact** against the model's own A_COUPLE = 1 buses.
A test asserts that, with a wrong-corner defeater. Removing the output stage's
`>> 15` floor gives 21.22 dB of attenuation, and louder buses (x8, x64) give
15.35 and 21.21 dB.

Each variant changes its own baseline, so its attenuation can only be compared
with the steady-state bound computed on **that variant's own baseline**. Those
bounds are 11.58, 11.57 and 11.58 dB. Every variant **overshoots** its own bound
by about 10 dB, and a causal one-pole does not beat its steady-state
attenuation. So the variants are not like-for-like, and they support nothing.

The first rule was one-sided and compared against the *production* bound
(`float-out >= 11.66 - 1`). The #614 review found it read this exact row as
SUPPORTED, which the paragraph that used to stand here had already called an
invalid comparison. That rule was satisfied by the input that defeats it
(rule 8). `test_h_ch2_defeater_a_variant_that_moves_its_own_baseline_is_not_support`
now carries both defeating inputs, and was committed red (`red-h-ch2.txt`).

The rule is now two-sided against the own bound (`reach_kind`, tolerance
unchanged at 1.0 dB). **It was changed after the dev row was read**, so `confirm`
is the first data it sees. The CH shortfall's mechanism is open; it is routed to
#618. Nothing here bears on audibility: the residual is below 1 LSB.

## What recordings can establish, and what their coupling prevents

Nothing external was read: `reference.txt` is REFUSED (no corpus here). The gate
(`reference_gate`) refuses an absent corpus, missing manifest, missing or
hash-mismatched file, undeclared sample rate and undeclared capture coupling,
each with a test. Even a verified corpus: a capture declared AC-coupled yields
`dc = REFUSED` -- its zero mean is the capture chain's, and **a recording
without DC is not evidence that the machine has none**. Only a verified capture
declared DC-coupled permits a raw-DC comparison. Shape and band comparisons
with identical onset-relative windows, causal conditioning
(`excitation_energy.condition_causal`, whose lead-in baseline is what is
actually used; the whole-clip `condition_meansub` is kept as a control) and the
same rate treatment on both sides remain available. The manifest schema in the
gate (`files[].sha256`, `sample_rate`, `capture_coupling`) is this probe's
requirement, not the corpus's. The model compared with itself cannot establish
sound fidelity, so **every subject's fidelity question ends in capability
refusal until a verified reference exists.**

## Per-subject ending

Typed from the dev rows here; once the batch runs, the ending is the `--verdict` output, not this table.

| subject | ending |
|---|---|
| CH | dev: OFFSET-like but 0.00 % of peak: no defect established. **NO VERDICT** until `confirm` runs. Shortfall mechanism: NO VERDICT (#618). Fidelity: capability REFUSED. |
| RS | dev: SKIRT: no standing offset, nothing a DC blocker can remove. **NO VERDICT** until `confirm`. Fidelity: capability REFUSED. Brightness: #556. |
| BD (control) | dev: MIXED, offset and skirt not separable. No defect established. |
| HT (control) | dev: SKIRT. No defect established. |
| other 11 voices | **NO VERDICT: not run here.** Route any finding to the owners above. |

## Wrong-then-right count

Eight. Six were caught by a control, guard or predeclared test; #7 and #8 were caught by review, not by inspection:

1. 1 % envelope floor: a brick-wall at 20 Hz leaks 2.2 % of peak into a fixture's tail (offset fixture refused as "still sounding").
2. The brick-wall extent also read a pulse's own sinc ringing as sounding; replaced by a time-domain tail-ring test.
3. I predicted a 1 LSB offset would sit under the quantisation floor. It is 41 dB over it (one bin holds all of it). The guard's defeater is now a zero-mean burst.
4. The inherited explanation of the shortfall (the filter's start-up tail, `dc_blocker.py`'s `steadystate_...` docstring) is not supported for CH by an independent float prediction (6.63 dB off).
5. First surrogate (integer blocker on the *summed* bus) gave 20.86 dB against production's 5.03: its own fidelity guard refused it. Replaced by the bit-exact per-bus surrogate.
6. My wrong-corner defeater ran on the CH's `dmix` bus, which is all zeros, so it was trivially exact. Moved to the body bus.
7. H_CH2 read SUPPORTED on a one-sided comparison against the production bound. Against each variant's own bound, every variant overshoots by about 10 dB, so the reading is NO VERDICT. The Judge caught it (#614 review). The rule now ships with that defeating input.
8. `--verdict` accepted two records with absent, null or `"?"` commits as "the same commit", and read a NaN `mean_frac` as "below the declared magnitude" (exit 0, a negative finding). The Judge caught both (#614 review of `ba62bc8`). Provenance and verdict-bearing fields are now validated where the JSON is read, and invalid input REFUSES (exit 2).

## Class search (conditioning, window and DC assumptions)

`git grep` for whole-clip mean subtraction and zero-phase filtering in `tools/`
and `model/` finds the same class at: `model/drum_fit.py:124` (after trim and
window cut: onset-relative, bounded), `model/drum_verify.py:213` and
`model/discrimination_features.py:203` (whole-clip mean, then peak-normalise or
autocorrelate), `model/measure_harness.py:276`, `model/audio_measure.py:1464,
2889`, and `sosfiltfilt` sites in `tools/cymbal_bands.py`, `tools/cymbal_*`,
`tools/diagnose_tom_body.py`, `model/tom_drop_measure.py`. **I did not assess
whether any of them is prefix-dependent on a quantity that matters** -- this is
a list of sites, not a finding. The whole-clip-mean form is the one
`condition_meansub` was retired for. Recommended follow-up: one issue to audit
the drum-facing ones (`drum_verify.py`, `discrimination_features.py`) for
trailing-silence sensitivity using `p_pad_invariant` / `p_lead_silence_invariant`
as the fixture pattern. **Filed: #617.**

Related, unowned as far as this search found: the output stage's `>> 15` floor
may leave a sub-LSB bias that a pre-output blocker cannot remove (H_CH2, pending
`confirm`, and now NO VERDICT on dev). It is an arithmetic matter adjacent to #220/#350 but not inside
either brief. **Filed: #618, and measured 2026-10-11 -- see the next section.**

## #618: the output stage's final quantisation (2026-10-11)

The production output stage forms ONE int64 accumulator and floors it:
`output_fx` computes `sat16((dmix*g + body*g) >> 15)` (`model/drums_fx.py:619-627`); the
RTL is the same shape (`rtl-sketch/voice_dp.v`: `tacc`, |.| < 2^38, line 281; `tsh = tacc >>> 15`,
line 601; `sat16t`, line 597; `sample <= sat16t(tsh)`, line 1004). Arithmetic `>>>` on ints is
floor, so production FLOORS where exact rounding would round-half-to-even. #618 measured what
that choice costs, from the SAME accumulator, without re-rendering:
`tools/probes/residual_dc.py --output-floor` (per condition) and `--floor-verdict DEV.json CONFIRM.json`.

Method, frozen in the committing commit (5407b1f) before confirm was read: retain each integer
accumulator once (`render_bus` keeps the dmix/body buses and gain); derive the production floor
AND the exact integer round-half-even of acc/2^15 from that one accumulator by quotient/remainder
only (r = acc − (q<<15) in [0, 32767]; the tie r = 16384 goes to the even quotient — correct for
negative ties too), apply the SAME rails; require the floor variant to equal the shipped
`output_fx` sample for sample before interpreting anything; CH the hypothesis subject, the other
14 SCREEN_VOICES controls; declared dev/confirm conditions and the baseline's onset-relative
2.0 s window; named REFUSED at the point of use for short windows, insufficient level,
accumulator overflow (|acc| ≥ 2^38), failed gain reach and failed baseline identity. Clipped
frames are counted and kept out of the attribution population — none occurred: 0 clipped frames
in all 30 rows.

### The figures, generated from the committed records

Every number and condition name in the endings below is quoted from this block, which is
generated from the records in `output-floor/qualification.json` by
`python3 tools/probes/residual_dc.py --floor-summary docs/residual-dc/output-floor/qualification.json`.
`test_readme_floor_summary_block_is_generated_from_the_committed_records` fails if the block
and the records ever disagree. (The first version of this section was typed by hand. It
attached the −0.0031 and −0.9188 extremes to dev when both are confirm rows, quoted SD dev at
9.2 % exact multiples and −0.70 LSB when the record says 0.08 % and −0.3315, and miscounted
ties. The saved endings had dev and confirm swapped as well, because the formatter labelled
figures by sorted index. That is wrong-then-right number 10. See below.)

<!-- floor-summary:begin -->
```
rows: 30 usable + 0 refused; clipped frames (floor+round): 0; max per-sample |floor-round|: 1 LSB
mean floor-round, smallest |.|: -0.0031 (RS confirm) LSB; largest: -0.9188 (SD confirm) LSB
largest |floor-round| as % of baseline peak: -0.0140 (MT dev) %
pre-rail mean floor error, smallest |.|: -0.0197 (MA confirm) LSB; largest: -0.7535 (BD dev) LSB
rows with zero ties: 12 of 30 (CB dev, CH dev, CL dev, HC dev, HT dev, LC dev, MA dev, MC dev, MT dev, OH dev, RS dev, SD dev)
rows with no high residue (bias would rest on ties alone): 0 (none)
```

| voice | cond | exact multiples | ties | high | floor err LSB | floor-round LSB | % of peak |
|---|---|---|---|---|---|---|---|
| BD | dev | 89/96000 (0.09 %) | 1 | 71999 | -0.7535 | -0.7500 | -0.0101 |
| BD | confirm | 65794/96000 (68.54 %) | 10070 | 9997 | -0.1570 | -0.1570 | -0.0021 |
| SD | dev | 75/96000 (0.08 %) | 0 | 6805 | -0.3315 | -0.0709 | -0.0011 |
| SD | confirm | 3263/96000 (3.40 %) | 3161 | 86788 | -0.7018 | -0.9188 | -0.0138 |
| LT | dev | 122/96000 (0.13 %) | 1 | 15728 | -0.3325 | -0.1638 | -0.0022 |
| LT | confirm | 7106/96000 (7.40 %) | 7196 | 74453 | -0.6380 | -0.8125 | -0.0105 |
| LC | dev | 222/96000 (0.23 %) | 0 | 80084 | -0.7003 | -0.8342 | -0.0113 |
| LC | confirm | 74187/96000 (77.28 %) | 7308 | 7299 | -0.1139 | -0.1141 | -0.0015 |
| MT | dev | 130/96000 (0.14 %) | 0 | 84981 | -0.5385 | -0.8852 | -0.0140 |
| MT | confirm | 81497/96000 (84.89 %) | 4806 | 4907 | -0.0758 | -0.0761 | -0.0012 |
| MC | dev | 79710/96000 (83.03 %) | 0 | 8182 | -0.0855 | -0.0852 | -0.0013 |
| MC | confirm | 84263/96000 (87.77 %) | 3879 | 3828 | -0.0606 | -0.0599 | -0.0009 |
| HT | dev | 104/96000 (0.11 %) | 0 | 87445 | -0.5404 | -0.9109 | -0.0123 |
| HT | confirm | 4099/96000 (4.27 %) | 4136 | 4071 | -0.2713 | -0.0635 | -0.0008 |
| HC | dev | 81490/96000 (84.89 %) | 0 | 7300 | -0.0757 | -0.0760 | -0.0010 |
| HC | confirm | 85761/96000 (89.33 %) | 3471 | 3446 | -0.0537 | -0.0538 | -0.0007 |
| RS | dev | 85056/96000 (88.60 %) | 0 | 10433 | -0.0619 | -0.1087 | -0.0017 |
| RS | confirm | 85742/96000 (89.31 %) | 230 | 209 | -0.0284 | -0.0031 | -0.0001 |
| CL | dev | 88205/96000 (91.88 %) | 0 | 5261 | -0.0418 | -0.0548 | -0.0010 |
| CL | confirm | 89691/96000 (93.43 %) | 1192 | 1147 | -0.0255 | -0.0176 | -0.0006 |
| CP | dev | 65708/96000 (68.45 %) | 1 | 15237 | -0.1568 | -0.1587 | -0.0021 |
| CP | confirm | 73749/96000 (76.82 %) | 7310 | 7231 | -0.1146 | -0.1121 | -0.0015 |
| MA | dev | 90526/96000 (94.30 %) | 0 | 3302 | -0.0286 | -0.0344 | -0.0005 |
| MA | confirm | 91620/96000 (95.44 %) | 1065 | 1069 | -0.0197 | -0.0164 | -0.0002 |
| CB | dev | 55880/96000 (58.21 %) | 0 | 20527 | -0.2068 | -0.2138 | -0.0029 |
| CB | confirm | 66190/96000 (68.95 %) | 9773 | 9563 | -0.1529 | -0.1464 | -0.0020 |
| OH | dev | 33782/96000 (35.19 %) | 0 | 32638 | -0.3285 | -0.3400 | -0.0046 |
| OH | confirm | 50415/96000 (52.52 %) | 14784 | 14533 | -0.2329 | -0.2264 | -0.0031 |
| CH | dev | 87274/96000 (90.91 %) | 0 | 5271 | -0.0481 | -0.0549 | -0.0009 |
| CH | confirm | 89310/96000 (93.03 %) | 2039 | 2027 | -0.0333 | -0.0309 | -0.0005 |
<!-- floor-summary:end -->

### Endings, kept separate

1. **Model-side bias: CONFIRMED, all 15 voices, both conditions.** The signed integer sum of
   floor−round is nonzero, with the same sign, in dev and in confirm. Mean floor−round runs
   from −0.0031 LSB (RS confirm) to −0.9188 LSB (SD confirm). The largest row as a share of
   its baseline peak is −0.0140 % (MT dev). The max per-sample difference is 1 LSB
   everywhere, and no frame clipped. The per-voice endings, with each figure labelled by its
   condition, are under `endings` in `output-floor/qualification.json`. That file also holds
   provenance, residue classes, per-row accumulator hashes and the controls matrix (commit
   5407b1f, clean tree, one commit for both records). The endings were re-derived from those
   unchanged records by `--refresh-endings`, with no re-render.
2. **Mechanism: MEASURED at the arithmetic level.** The floor error is exactly −r/Q15 per
   sample, so its mean is set by the residue distribution. The exact-multiple share alone does
   not set it. CH dev has 90.91 % exact multiples and a −0.0481 LSB error. SD dev has 0.08 %
   and −0.3315 LSB, because 89120 of its residues are low. BD dev has 0.09 % and −0.7535 LSB,
   because 71999 are high. So the often-quoted −0.5 LSB is a residue-distribution hypothesis,
   NOT a constant. Measured pre-rail means run from −0.0197 LSB (MA confirm) to −0.7535 LSB
   (BD dev). Ties are absent from 12 of the 30 rows, all of them dev rows. Every confirm row
   has ties, from 230 (RS) to 14784 (OH), and SD confirm has 3161. Ties cannot decide any
   bias here. floor−round = −(high residues) − (odd-quotient ties), and every row has high
   residues, so each sum would stay nonzero whatever the ties did.
3. **Reference fidelity / DC inference: REFUSED.** The reference gate stands refused (no
   verified corpus on this host; an AC-coupled capture could not prove DC absent in any case).
   This does not erase the model arithmetic result above; it forbids a fidelity claim from it.
4. **CH attenuation mechanism (H_CH2): unchanged, NO VERDICT.** The inherited own-baseline
   overshoot guard (#614 review) is reproduced in the suite and was not tuned to any CH result.
   The steady spectral prediction is NOT claimed as a hard upper bound here: finite window,
   filter start state and the quantiser's nonlinearity are open applicability questions. The
   #152 screen/verdict itself was not re-run in this issue.
5. **RTL delivery: NOT claimed.** The exact round-half-even variant is a model-only
   counterfactual; the RTL floors too, so the table quantifies what the shipped floor choice
   costs against exact rounding — it does not propose a change and does not claim the hardware
   delivers the rounded variant. No audibility, sound-defect, production-default or image claim
   follows from any of this.

### Wrong-then-right: 11, nine before any measurement was read, two after

Seven caught RED by the controls/fixtures or by execution crashes, two by tracing the mutant
wiring before the matrix ran: a 4-tuple unpack of a 3-tuple bus record; the wrong-gain mutant
never consulted by the row path (its control would have been silently BLIND); six hand-oracle
rows wrong at the rails and at −32767 — **every time the integer arithmetic under test was
right and the hand-written expectation was wrong**; two properties rendering a 0.3 s fixture a
declared 2.0 s window must refuse; the identity-stub mutant invisible to every property (caught
by the "each mutant MOVES" contract, fixed with a composed-guard property); a duplicate-base-class
crash; `diff_sum` validated as non-negative though it is a signed sum; `all_refused` reading
`label` from rows that carry `status`. The fixtures did their job: the apparatus could not say
anything until it agreed with hand arithmetic.

**Wrong-then-right number 10 happened AFTER the measurements were read, and review caught it,
not a control** (PR #653). The measurements were right, but the reporting was wrong in two
ways. First, `bias_ending` labelled each figure by its position in `sorted(...)`. `confirm`
sorts before `dev`, so every saved ending printed dev's figures as confirm's and the reverse.
The ending tests used identical values in both conditions, so they could not see the swap.
Second, the narrative above was typed by hand from those endings. It carried the swap and
added errors of its own: the 9.2 % / −0.70 LSB SD figures, the "MT confirm" worst case (the
record says MT dev), and the "0 ties in 27 of 30 rows" count (the record says 12 of 30). The
fix has four parts. Figures are looked up by condition name. Asymmetric regressions use
different values in each condition, with `confirm` given the values that sort first; they
were committed red against the old formatter in `output-floor/red-condition-labels.txt`. The
saved endings were re-derived from the unchanged records. The narrative figures are
generated by `floor_summary` and checked against the records by test.

Class search for positional condition labels: `rg -nE 'sorted\([^)]*\)\[[0-9]\]|\((dev|confirm)\)|conds?\[[01]\]' tools/probes`,
plus a read of every per-condition formatter in `residual_dc.py`. `subject_verdict` and both
`*_verdicts` combiners build and pass name-keyed dicts, so none of them labels by position.
No other instance was found in `tools/probes`. I did not search outside it.

**Wrong-then-right number 11 was in the gates, not the figures, and review caught it again**
(PR #653, second round). No published number changed, but two paths would have published
numbers they should have refused. First, `--floor-summary` (the command that generates the
block above) called `floor_summary` with no record validation. Records marked `dirty=True` with
`limits={}` printed the full block, although `floor_verdicts` refused them. Second,
`floor_row_problems` checked shape only: finite numbers and integer counts that partition
`n_frames`. Every BD row rewritten to `diff_sum=96000`, `mean_diff_lsb=1.0`,
`diff_pct_of_peak=100/peak_lsb` therefore reached `BIAS CONFIRMED ... +1.0000 LSB`, and a
zero-frame OK row was admissible. That is impossible: floor minus round-half-even under the
same monotone rails is −1 or 0 per sample. The fix has two parts. `floor_summary` now raises
`Refused` unless `floor_verdicts` accepts the records, and the CLI prints a named
`FLOOR SUMMARY REFUSED:` with exit 2 and no block. `floor_arithmetic_problems` reconciles
every OK row before it can reach an ending. It checks diff_sum ≤ 0, and that the −1 count
lies between (high − clip_floor − clip_exact) and (high + ties). It checks max_abs_diff ∈ {0, 1}
and that it agrees with the sum. mean_diff_lsb must equal diff_sum/n, and both %-of-peak
figures must equal their mean over peak_lsb. The pre-rail error must lie inside the interval
the residue counts allow. n_frames must be the declared 96000-frame window and `window` must
span it. peak_lsb must lie in [16, 32768], and clip counts may not exceed n_frames. The
review's exact defeater is a permanent test
(`test_floor_verdicts_refuse_the_positive_difference_defeater`), with fifteen further
impossible rows. All of them were committed red in `output-floor/red-arithmetic-gate.txt`. The
committed records pass the stricter gate (`test_the_committed_records_pass_the_arithmetic_gate`),
and the block above is byte-identical. The new gate also caught one of the suite's own
fixtures: it had used 1000-frame rows, off the declared window. The old shape-only fixtures
were impossible too (for example, `diff_sum=-5271` with 601 high residues and 6 ties), so
they are now built by `_crow` from the arithmetic.

Class search for "figures emitted from saved records without the gate", across every reader
of a saved record in `residual_dc.py` (`rg -n 'json.loads|records\]|\["rows"\]'
tools/probes/residual_dc.py`). `--floor-summary` was the only emitter that skipped the gate.
`--refresh-endings` and `--floor-verdict` already gated through `floor_verdicts`, but they
crashed with a traceback on an unreadable or non-object input. They now name the refusal and
exit 2 (`test_floor_report_paths_refuse_an_unreadable_input`). `--output-floor` prints only rows
it has just computed, so it reads no saved record. The pre-existing #152 `--verdict` path
(`verdicts`/`row_problems`, on `main`) has the same shape gap. It trusts each record's
`label` and does not re-derive the label from the β/S statistics in `detail` under the frozen
limits. It is outside this PR's diff, so it is filed as a follow-up rather than fixed here.

### Class search: signed shift / final quantisation / rails, across model and RTL

- `model/drums_fx.py`: `output_fx:627` is the only audio-output `>> 15` floor+clip.
  `:181/:230` (`peak*accent >> 15`, usat-clipped, non-negative) are the same shift class at
  control rate — an envelope level, floor-biased < 1 LSB, NOT measured here (out of scope).
  `:523` is register field extraction, exact.
- `rtl-sketch/voice_dp.v`: floor sites `>>> 15` at `:600/:601` (dq, tsh — tsh is the measured
  counterpart), mixer taps `:507/:510`, noise paths `:476-:487`; the drift paths `:554-:556`
  add `+17'sd32` before `>>> 6` — round-half-UP. The codebase therefore already mixes floor
  and round-half-up BY STAGE; the output stage floors. Rail assumptions: `sat16t` is the ONE
  rail (`:597`), with the no-rail negative control at `:1002`.
- Mixer/modal recursion quantisation: adjacent, owned by #350/#641 — not expanded here. No
  other output-stage quantisation site was found in either tree.


### Class search: guards on imported JSON (#614 review of `ba62bc8`)

`rg -n 'mean_frac|r.get\("commit"\)|isfinite|\.get\("' tools/probes/residual_dc.py`, plus
`rg -n 'json.loads|isinstance\(.*int\)'`. The file has two JSON consumption boundaries:

- `--verdict` records (`verdicts`, `subject_verdict`): the two reviewed defects, plus three more of
  the same shape fixed with them. `dirty` was truthiness-tested, so `null`/`0` passed as clean.
  `mean_frac: true` cleared the magnitude threshold, since `abs(True) == 1`. And `subject_verdict` checked
  agreement only among the labels supplied, so a singleton or an empty input passed.
- the reference manifest (`reference_gate`): `isinstance(sample_rate, int)` accepted JSON `true`,
  because bool is an int subclass. A non-object manifest or file entry raised instead of refusing.
  Fixed, with defeaters (red first, in `red-verdict-guards.txt`).

The only other `isfinite` is `classify`'s sample check, which already refuses non-finite audio.
The remaining `.get(` sites read dicts this module built in-process (`classify` details,
`bus_variants`, `hypotheses` rows). No JSON reaches them, so they are not this class.
I did not search outside this file.

## Registry

No new cutoff or placement parameter is proposed, so nothing was added to
`docs/sensitivity/registry.json`. (The diagnostic limits above are apparatus
limits, not hardware parameters.) `COUPLE_K` is already registered
(`coupling-k.json`).

# Residual drum DC coupling: non-cymbal screen, CH / RS detail (#152)

Diagnostic code and evidence only. No production enable, no RTL, no image
change. Cymbal carry-through is #510/#552/#553; MA/RS brightness is #556;
modal arithmetic is #220/#350; toms and congas are #558/#591-#593. Nothing
here repairs any of them.

Regenerate: `python3 tools/probes/residual_dc.py --screen|--detail|--controls|--reference`.
Controls: `python3 -m pytest tools/probes/test_residual_dc.py -q` (98 tests).
Per-subject ending: `python3 tools/probes/residual_dc.py --verdict DEV.json CONFIRM.json`, reading the
`--screen --rows-out` records of both conditions (refuses a missing/duplicate condition, a dirty
record, a record without a valid 40-hex source commit, mixed commits, moved limits, a malformed
row, or a non-REFUSED row whose `mean_frac` is not a finite real; exit 2). Each guard's defeating
inputs are permanent tests; `red-verdict-guards.txt` records them failing before the fix.

## Status: PARTIAL, and the missing part is stated, not hidden

| item | state |
|---|---|
| apparatus qualified on constant-offset / short-burst / zero-mean / added-HF ground truth, MOVED/BLIND matrix | **done**, `controls-matrix.txt`, 98 passing |
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
either brief. **Filed: #618.**

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

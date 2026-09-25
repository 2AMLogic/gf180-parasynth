# F1 input-level scaling: matched-level Surge references and one compensated parameter

A bounded measurement. **No global default, patch, ROM, `CUT_TRIM` or board
record is changed.** `refprofile/` is read only. The rule was committed before
any candidate was rendered: [`selection-rule.md`](selection-rule.md), commit
`001cdfe`.

Reproduce (about 5 minutes; step 1 needs Surge XT 1.2.3 and dawdreamer, and
steps 2–3 need neither):

```bash
.venv/bin/python tools/refprofile_restore.py && .venv/bin/python tools/refprofile.py
.venv/bin/python tools/refprofile_repro.py --runs 2          # apparatus state
.venv/bin/python tools/f1_level_capture.py --capture         # step 1 (plugin)
.venv/bin/python tools/f1_level_capture.py                   # verify captures (no plugin)
.venv/bin/python tools/probes/f1_level_scaling.py --json docs/scorecard/f1-level/results.json
```

## Step 1: apparatus state, then matched-level references

**Reproduction of the frozen nominal captures.** `refprofile_repro.py --runs 2`
rendered two independent processes. All 16 clips were bit-identical run to run,
and all 16 reproduced the committed sha256 (exit 0;
`nominal-repro.log`, `nominal-repro-report.json`). `profile.json` is unchanged.
The capture tool's own amp-0.25 takes equal the frozen bytes for `open20k` and
`cut250`. For `cut1000` and `cut4000` they differ by at most 5.2e-7 and 7.5e-9
full scale (≤ 1.3e-8 dB on the projected curve). The difference comes from
render order: the frozen profile renders ten resonance clips before those two.

**Captures** (`captures/manifest.json`, `captures/audio.zip`, 24 takes, 12
unique byte streams). The stimulus is the frozen profile's stepped tone (32
tones, 40 Hz–12 kHz) at amplitude 0.25, 0.125 and 0.0625 (−12.04, −18.06 and
−24.08 dBFS). Conditions are cut 250, 1000 and 4000 plus open 20 kHz, all at
res 0, two takes each. Each take ran in its own process, so the VST3 was
reloaded and the rig re-qualified each time. Recorded: Surge XT 1.2.3, binary
sha256 `2bc28ffe729965aa` (must equal the frozen profile's, or the tool
refuses), dawdreamer 0.9.0, block 512, 48 kHz, pinned readbacks. The brief asked
for 0.25 and 0.0625 (16 takes). 0.125 was added so that each ½/¼ candidate has
a matched internal-level reference, at a cost of 8 extra takes.

**Take-to-take:** 12 of 12 condition/level pairs are bit-identical (max abs
difference 0.0).

**Surge Type 2 is level-independent over −12…−24 dBFS**, which re-verifies the
documented claim:

| case | amp | corner Hz | rolloff dB/oct | low-band dB | open-path noise (dB re stim) |
|---|---|---|---|---|---|
| F1A | 0.25 / 0.125 / 0.0625 | 117.63 / 117.63 / 117.64 | −18.69 ×3 | −0.907 ×3 | −90.9 ×3 |
| F1B | 0.25 / 0.125 / 0.0625 | 450.53 ×3 | −17.92 ×3 | −0.256 ×3 | |
| F1C | 0.25 / 0.125 / 0.0625 | 1718.58 ×3 | −16.41 ×3 | −0.136 ×3 | |

The largest change is 0.01 Hz, in F1A's corner. Because the reference does not
move with level, any level dependence in the F1 comparison is ours.

## Step 2–3: compensated input scaling on the selected path

The parameter `s` enters through `LadderFx.regs` at `volts_per_unit = 0.13·s`,
so `gain` is scaled by `s` and `ogain` by `1/s`. Source level, ROMs,
`CUT_TRIM`, resonance 0, drive 1.0, precision and the 2x causal rate chain are
held fixed. The path is #228's selected path: identity passed, and the exact
match shows 0 mismatches in 12,000 frames.

**Register words recorded at the ladder's `process` call** (identical for all
three cases and all levels):

| candidate | s | gain (Q4.16) | ogain (Q4.16) | differs from baseline |
|---|---|---|---|---|
| baseline | 1 | 170394 | 25206 | — |
| half | ½ | 85197 | 50412 | yes (asserted) |
| quarter | ¼ | 42598 | 100825 | yes (asserted) |

**Table vs the matched-level Surge reference.** Errors are ours minus Surge's.
Noise and THD are for the open path, median over tones ≤ 4 kHz. Clipping lists
reconstruction clips, output-rail words, internal `sat()` events and
tanh-domain clamps. It was **0 in every cell**, so it is not repeated below.

| cand | amp | F1A corner err % | F1B | F1C | F1A rolloff err | F1B | F1C | low-band err (A/B/C) | noise dB | THD dB |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 0.25 | **−16.80** | −16.45 | −16.38 | **+1.81** | **+1.94** | +1.30 | −0.37/+0.02/+0.07 | −52.0 | −34.9 |
| baseline | 0.125 | −9.51 | −6.96 | −5.92 | **+1.66** | +0.03 | +0.15 | −0.08/+0.09/+0.09 | −60.7 | −44.8 |
| baseline | 0.0625 | −7.63 | −4.17 | −3.63 | +0.89 | +0.01 | +0.13 | +0.01/+0.11/+0.09 | −71.1 | −86.8 |
| half | 0.25 | −9.51 | −6.95 | −5.92 | **+1.66** | +0.03 | +0.15 | −0.08/+0.09/+0.09 | −60.8 | −44.8 |
| half | 0.125 | −7.63 | −4.17 | −3.63 | +0.89 | +0.02 | +0.14 | +0.01/+0.11/+0.09 | −75.1 | −88.0 |
| half | 0.0625 | −7.64 | −4.18 | −3.64 | +0.88 | −0.00 | +0.12 | +0.01/+0.11/+0.09 | −69.1 | −82.5 |
| quarter | 0.25 | −7.62 | −4.17 | −3.63 | +0.89 | +0.03 | +0.14 | +0.01/+0.11/+0.09 | −77.7 | −88.3 |
| quarter | 0.125 | −7.63 | −4.18 | −3.64 | +0.88 | +0.01 | +0.13 | +0.01/+0.11/+0.09 | −71.9 | −83.1 |
| quarter | 0.0625 | −7.64 | −4.19 | −3.66 | +0.86 | +0.00 | +0.10 | +0.01/+0.11/+0.09 | −65.8 | −76.1 |

Bold marks a result outside tolerance (corner 10 %, rolloff 1.5 dB/oct, gain 3
dB). The Surge open path reads −90.9 dB noise and −117 dB THD.

What the table shows:

- **The compensated scaling is an internal level shift and nothing more.**
  Half at 0.25 equals baseline at 0.125, and quarter at 0.25 equals baseline
  at 0.0625, to ≤ 0.02 points of corner and ≤ 0.02 dB/oct. The −12 dBFS F1
  failure is our ladder's own saturation, as #228 hypothesised. Matched-level
  references now confirm it.
- **At s = ¼ all nine F1 metrics are within tolerance at the F1 level**
  (corner −7.6/−4.2/−3.6 %, rolloff +0.89/+0.03/+0.14, gain within 0.11 dB), and
  corner error is flat across the three levels (spread ≤ 0.03 points).
- **What remains is not a level effect.** A −7.6 → −3.6 % small-signal corner
  error with a 4-point trend across cutoffs survives at every level. This is
  the cutoff-mapping residual. Only now can it be separated cleanly, and it is
  the next candidate parameter (not in this task).
- **Cost: noise.** The small-signal noise floor rises by 6 dB per halving of
  `s`: quarter at 0.0625 is −65.8 dB against baseline's −71.1 dB. It stays
  inside the rule's −60 dB limit, and is still 25 dB worse than Surge's −90.9 dB.
  The high-level "noise" figures (−52 dB baseline at 0.25) are mostly
  distortion products above h9, which the fit does not remove. THD reads
  alongside them.

## Step 4: selection within the demonstrated scope

The pre-declared rule (`results.json` → `evaluation`) found **both candidates
eligible** on all eight clauses. **Selected: quarter (s = ¼)**, with mean
|corner err| 5.14 % against 7.46 % for half and 16.54 % for the baseline. The
tie-break did not apply, because the gap is 2.3 points against a 0.5-point
margin.

**Scope of this selection.** It is a calibrated operating point for *this rig*:
the Surge Type 2 comparator, the F1 stepped tone at −12…−24 dBFS, res 0, drive
1.0, the selected path. It says that our ladder matches Surge's small-signal
F1 behaviour when its tanh stages see ¼ of today's input. It does **not**
license changing `LADDER_CFG`/`volts_per_unit`, the drive→gain conversion, or
any patch. Nothing was changed.

**What a global change would additionally require (not done here):**

1. **Deliberate overdrive must survive.** `s` multiplies the gain word exactly
   as drive does, so global s = ¼ at drive 0.75 puts the same word into the
   ladder as drive 0.1875 today (with ogain compensated). #226 showed that
   lowering effective drive loses harmonic passes: 4 lost at 0.50 and 6 at 0.25.
   A global change therefore needs a drive-mapping re-derivation that keeps
   each musical patch's effective tanh input, not the raw scaling.
2. **M1A's qualified properties** must be re-scored on the candidate, with none
   of today's passes lost: pitch, envelope, the qualified harmonic and volume
   properties, and the 8/33 harmonic-cell passes at drive 0.75.
3. **M5A/M5B** must be re-scored on the selected engine with the change.
4. **Resonance.** This task measured res 0 only. The `(1+2·res)` ogain
   compensation and self-oscillation onset/level depend on where the tanh
   operates, so the F2 resonance ladder and self-oscillation tuning need
   re-measuring.
5. **Production path.** Register widths must be checked (ogain at s = ¼ is
   100825, which fits 20 bits, but any resonance compensation on top must be
   checked against the 20-bit clamp). RTL bit-exact verification of the voice
   and the host-conversion change (contract 5.5) are also needed. A change to
   the host conversion is also a change to `patch_regs`, which is the global
   path the trap below bypasses.
6. **Noise budget.** Is the +5–6 dB small-signal noise acceptable at
   the production output, or does it need state bits?

## Controls (`controls-run_all.json`, all by exit status)

| job | expectation |
|---|---|
| `--inject CONSTRUCTOR_ONLY --expect-refused 'equal the baseline'` | **the trap:** candidates built with a scaled `volts_per_unit` in the voice constructor get the global words (170394/25206) from `patch_regs`, and the probe REFUSES |
| `--inject WORDS_NOT_ENTERING --expect-refused 'not the candidate'` | predicted words differ, baseline words enter the ladder, REFUSED |
| clean run under `--expect-refused` | must not count as a caught control (exit 1) |
| `tools/test_f1_level_scaling.py` + `tools/test_f1_selected_path.py` | includes: the trap renders the baseline audio bit for bit, real candidate words change > 50 % of samples, instrumentation changes no output word, clamp counter sees an overdriven input, noise estimator known answer (−70 dB noise, −20 dB THD), tampered capture hash refuses, and the rule's clauses on synthetic tables |
| `tools/f1_level_capture.py` verify | 24 of 24 captures hash-verified |
| `tools/refprofile.py` | frozen profile still verifies (16 of 16) |

## Wrong-then-right and apparatus incidents

No reported number was wrong before it was right. There were three apparatus
incidents, each caught by a check rather than by inspection:

1. **Surge pin names flip after the first render.** The capture tool re-checks
   pins after every clip, and it REFUSED the first capture. Indices
   259/260/264/265 report Classic-oscillator names at construction and their
   Audio In names (Channel, Gain, Low Cut, High Cut) after the next render.
   Readbacks are unchanged. `refprofile.render` checks pins only before
   rendering and writes `pins_held_after_render: true` as a constant, so it
   has never observed this. The capture tool now accepts exactly those four
   aliases and still requires the readback. The fix belongs in
   `refprofile.py`/`reference_rigs.py`. It is left for a follow-up, because it
   would change the builder hash of the frozen profile.
2. **`exit=$?` after a pipe.** In one early verification command, `$?` after
   `| tail` captured `tail`'s status. The output text was correct, but the
   status was not evidence. The command was re-run without the pipe
   (`post-repro-verify.log`).
3. **Controls reported as NO-VERDICT.** The first `run_all` batch ran the two
   register controls bare. They refused correctly (exit 2), which `run_all`
   rightly calls NO-VERDICT rather than a pass (`experiment-run_all.log`). The
   `--expect-refused` mode was added so that a control passes only for its
   stated reason.

# Lead ladder drive on the phase-free Mini V3 lead references (#337)

**Experiment only; nothing is promoted.** No engine, patch, scorer, tolerance, RTL
or image changes. This is one bounded question under #337's "drive and
resonance" item, on the part of "drive" that #583 did not cover.

## Why this question

#583 showed that lowering the M1A bass's ladder input drive (0.75 to 0.25) fixes
its harmonic timbre once phase is controlled. M1A has two free-running
oscillators, so that answer needed a phase-aware method. The M5A and M5B lead
references play **one** oscillator (saw, or pulse), so they have no relative
phase: the comparison is clean without it. Their harmonic shape fails by 7.6 dB
(M5A) and 6.0 dB (M5B), and every upper partial is too dark. The only drive
experiment done on them is a two-point trial, 1.00 to 0.75
(`../mono-m5a-miniv3/drive075-comparison.md`), isolated to the saw. It stopped at
0.75 without testing lower.

Resonance and oscillator mixtures are not asked here: there is no frozen
resonant mono reference, and the only frozen mixture (M1A) is covered by #583.
Drift: #586 found the frozen notes give 0 usable windows. All three stay open.

## Data status, stated before the rule

- **Diagnostic period (development data, already seen).** I rendered the M5A
  phrase at drive 0.75, 0.50 and 0.25 *uncompensated*, on the selected engine,
  and read the harmonic summary, excess alias and level per event. That is the
  first sight of any candidate number. It showed the saw improving with lower
  drive and the pulse not (pulse mean error -3.0 dB at 0.75, -3.7 at 0.50,
  -4.0 at 0.25 at MIDI 84), and that 0.25 loses 8-9 dB of level. The rules below
  were written after seeing that, so **the pulse outcome at 0.50 is not a
  prediction; it is a known result that the brightness rule is built to
  respect.** Selection on M5A is a fit by construction, labelled as one.
- **Baseline on the confirmation set was also seen** (drive 0.75 on M5B, from
  `results/M5B.json` and a re-render). No candidate number on M5B existed
  before this commit.
- **Confirmation set: M5B MIDI 72**, saw and pulse. M5A does not play MIDI 72.
  M5B MIDI 84 is the **same audio and the same model render** as M5A MIDI 84
  (reference level -19.19 dBFS and model level agree to 1e-3 dB in both
  records), so it is not an independent condition and is reported but not
  counted.
- Not pristine: M5B's MIDI 72 is a different note, but the same patch family,
  same reference instrument and same patch settings as M5A (per the frozen manifests' patch records; not re-audited here). It tests
  generalisation across pitch, not across patches.

## The question (one)

For each waveform of the single-oscillator Mini V3 lead, does lowering the
ladder input drive from 0.75 reduce the harmonic-shape error on a note that was
not used to choose the drive, without winning by being quieter, darker, more
aliased or off pitch?

## Baseline, candidates, budget

- **Baseline:** drive 0.75, the selected engine (`engine="selected"`), exactly
  `tools/mono_m5a_score.measure`. The tool REFUSES unless the baseline
  reproduces `results/M5A.json` and `results/M5B.json` Harmonic shape, Foldback
  energy, Gain and Pitch to 5e-3, and unless a drive change demonstrably
  changes the rendered audio (the apparatus applies what it claims to).
- **Candidates:** drive in {0.50, 0.25}: the grid #583 and the M1A drive
  experiment used, unchanged. Two candidates, one mechanism. Not refined.
- **Level compensation in the patch,** per waveform, one pass, never iterated:
  render uncompensated; delta = mean over that waveform's M5A events of
  (baseline Gain-window level minus uncompensated level); scale `vol` by
  10^(delta/20). If that pushes `vol` past 1.0 the candidate is
  **UNREACHABLE** and ineligible for that waveform. It does not get to win by
  staying quiet. The confirmation reuses the M5A delta unchanged.

## Target property

Per waveform: the official Harmonic shape quantity, max over the waveform's
events and partials h2..h12 of |model minus reference ratio error| (dB),
`tools/mono_m5a_score` unchanged. Also the RMS over the same cells, the mean
signed error (negative means dark), the maximum excess alias, and the
mean model level.

## Selection rule (per waveform; choose at most one; development = M5A)

Eligible iff all hold:

1. reachable (vol <= 1.0 after compensation);
2. max |error| at least 0.5 dB below baseline;
3. RMS error not above baseline;
4. brightness: mean signed error not lower than min(baseline mean, 0) - 0.5 dB.
   It may not win by going darker than both baseline and reference;
5. level: mean model level within 0.5 dB of baseline after compensation
   (the one-pass rule must actually have held);
6. excess alias (max over events) not above baseline + 0.5 dB;
7. Pitch error (max |model - reference cents|) not above baseline + 0.05 c.

Among eligible, lowest max |error|. None eligible: STOP for that waveform
(negative result; it keeps the lead harmonic requirement visible).

## Confirmation rule (chosen drive, per waveform, M5B MIDI 72)

CONFIRMED iff on M5B MIDI 72, chosen vs baseline: rules 2-7 above hold, with
the M5A compensation delta applied unchanged. Anything else NOT CONFIRMED. A
missing precondition is REFUSED, which is neither.

## What either outcome would mean

CONFIRMED does not ship a sound change: M5A is an integrated-RTL case, so a
new drive needs a new `gain` register word through the default register
images, and RTL plus production-path ladder verification with explicit
counts, on the build box. The official lead Harmonic shape would also still
fail its 1 dB limit; the report prints what the official number would become.
NOT CONFIRMED / none eligible closes the drive mechanism for that waveform.

## Preservation set

The three clean F1 passes use their own drive 1.0 and resonance 0 and do not
read the M5 patch. They are untouched and not extrapolated to any resonant
setting. M1A's selected patch is untouched. Pitch, Clipping and the envelope
properties of the M5 cases must not change validity (checked in the report).

---

## Results (written after the run; nothing above was changed)

Record: `report.json`, from commit `f31cc09` (`worktree_dirty: false`), one
process of 2 min 8 s, `nice`'d, on the selected engine. Preconditions held: the
drive-0.75 baseline reproduced the Harmonic shape, Foldback, Gain and Pitch of
`results/M5A.json` and `results/M5B.json` to 5e-3, and a drive change changed
the rendered audio (SHA differs).

**Saw lead: drive 0.50 chosen on M5A and CONFIRMED on M5B MIDI 72, a note not
used to choose it. Pulse lead: no candidate eligible; stays at 0.75.**

### Saw (development M5A MIDI 84 + 96; confirmation M5B MIDI 72)

| | baseline 0.75 | drive 0.50 | drive 0.25 |
|---|---:|---:|---:|
| dev max abs error (dB) | 7.56 | **4.64** | UNREACHABLE |
| dev RMS error (dB) | 4.52 | **3.27** | |
| dev mean signed error (dB, negative = dark) | -4.09 | -2.69 | |
| dev model level (dBFS) | -18.34 | -18.34 | |
| dev max excess alias (dB) | 2.21 | 2.31 | |
| compensation / effective vol | 0 / 0.427 | +2.73 dB / 0.585 | needs vol > 1.0 |
| **confirmation max abs error (dB)** | 5.51 | **4.43** | |
| **confirmation RMS error (dB)** | 4.45 | **2.92** | |
| confirmation mean signed error (dB) | -4.23 | -2.58 | |
| confirmation model level (dBFS) | -17.08 | -17.41 | |
| confirmation max excess alias (dB) | 1.45 | 1.84 | |

All seven rules passed on both sets. 0.25 was declared unreachable because
the one-pass level compensation would need `vol` above 1.0, so it could only
have been compared by being quieter.

### Pulse (development M5A; nothing to confirm)

| | baseline 0.75 | drive 0.50 | drive 0.25 |
|---|---:|---:|---:|
| max abs error (dB) | 5.32 | 5.43 | 8.49 |
| RMS error (dB) | 3.20 | 3.79 | 5.15 |
| mean signed error (dB) | -2.98 | -3.61 | -4.73 |
| max excess alias (dB) | 10.27 | 7.81 | 1.82 |

Both candidates fail rules 2, 3 and 4. Lowering drive makes the pulse
**darker**, the opposite of the saw. The alias falls (10.3 to 1.8 dB at 0.25),
but only because the output is darker, which rule 4 exists to refuse.

### In sound terms

The saw lead's upper partials are 4.1 dB too dark on average at drive 0.75 on
the M5A notes. At 0.50, with level held, they are 2.7 dB too dark. The effect
holds at MIDI 72 (4.2 to 2.6 dB). That is a real improvement and **still a
fail**: the official limit is 1 dB per partial and the best saw error is 4.4 dB.
<!-- claim: grep="real improvement and" in=docs/scorecard/mono-m5-drive-337/README.md note="effect; report.json confirmation.saw.summary vs confirmation.baseline.saw" -->

If the saw were given 0.50 and the pulse kept 0.75, the official Harmonic
shape of M5A would move from 7.56 to 5.32 dB, and the maximum would then be a
**pulse** cell. That is arithmetic on the report's per-wave maxima (saw 4.64,
pulse 5.32), not a re-scored run. Both leads would still fail the 1 dB limit.

### What this does not establish

- **Mechanism: not tested.** Only drive was varied. Why saw wants less input
  drive and pulse wants more is a reading, not a measurement. One candidate
  reading is that the pulse's missing brightness has a different source (its
  duty/spectrum, tracked by `../pulse-cutoff-347/`), which drive cannot fix
  and which the saw does not share. This is unverified.
- **Per-waveform drive is a patch change the RTL may not support as such.** A
  saw segment with drive 0.50 and a pulse segment with 0.75 means different
  `gain` register words per wave. Whether the production path and default
  register images express that was not checked.
- **The 'official' numbers in `report.json` under `confirmation.saw.official`
  mix the two waveforms:** that render applied drive 0.50 to the pulse too,
  uncompensated, so its Gain and Foldback entries describe the pulse at a
  rejected setting. Only the `summary` blocks are the rule inputs. The envelope
  release of the saw render moved 1213 to 1188 ms in that run (limit 125 ms);
  attack 8.9 to 9.1 ms. Neither is judged here.
- Only one confirmation note (MIDI 72, 11 cells). The 72-vs-84 behaviour
  agreed in sign and size, which is evidence of consistency, not a fresh
  patch or a fresh capture.
- Measured against Mini V3 software, not hardware. Drive 0.50 is a model-side
  setting that this comparison does not map to any Mini V3 control.

### Next, in order (none done here)

1. Decide whether the lead saw may carry its own drive. If yes: new default
   register image words, then RTL and production-path ladder verification at
   the new `gain` word with explicit sample counts (**build box**), and a
   registry entry for the drive in `docs/sensitivity/registry.json` with a
   prediction made independently of this measurement.
2. The remaining saw error (4.4 dB, dark) and the pulse darkness are not
   drive. They are the unmet lead requirement, left visible.

### Wrong-then-right

Zero corrected measurements in a reported number. One design correction before
any result: the first instrument draft scaled `vol` for both waveforms at once,
which could have pushed the pulse segment's volume past 1.0 unchecked while
measuring the saw; it was changed to scale only the waveform under test and to
check reachability explicitly before rendering.

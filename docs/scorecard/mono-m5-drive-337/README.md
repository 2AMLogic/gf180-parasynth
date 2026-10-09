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
  same reference instrument, same operator and same session as M5A. It tests
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

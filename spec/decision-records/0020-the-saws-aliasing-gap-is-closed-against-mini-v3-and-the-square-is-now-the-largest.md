# 0020: The saw's aliasing gap is closed against Mini V3; the square is now the largest

- **Status**: proposed
- **Date**: 2026-09-26
- **Decided by**: block agent, from `tools/measure_saw_alias_after_2x.py` run
  against commit `12f51c5` (`docs/saw-alias-2x-results.json`,
  `docs/saw-alias-2x.md`)
- **Extends**: contract open item 15 (the aliasing gap); cites DR 0001's
  aliasing measure and `fpga/SELECTED.md`'s `OSC2X=1 FILTER2X=1 PULSE2X=0`
- **Issue**: #61; hands the remaining largest gap to #205

## Context

Contract open item 15 and issue #61 recorded aliasing as **the largest known
defect in the voice**, on a table that this repository then spent three
separate corrections walking back: the Surge rig had asked for a saw and
received a 50 % pulse (#87), every settle render carried the previous note's
tail, and the whole 55 Hz column was the estimator's own floor (#92, #132).

The fix direction the thread settled on — **a decimation filter, not a longer
BLEP and not more oversampling** — then shipped for the saw
(`voice_fx._render_2x`, `_DECIM2_TAPS`) and was selected into the FPGA baseline
as `OSC2X=1`. What did not happen is the measurement: `test_the_sawtooths_
aliasing_floor_degrades_with_pitch_and_is_locked` still locked only the
**unfixed** curve, and its own docstring said "when the fix lands, these numbers
move and this docstring is the before". Nobody wrote the after. A shipped fix
with no measurement behind it is a claim, and the priority ordering of every
other piece of voice work was resting on it.

## Decision

**Ratify the measured after-curve as the shipped saw's aliasing behaviour, and
move "the largest known voice defect" from the saw to the square/pulse family.**

Measured 2026-09-26 against `12f51c5`, 0.5 s records at 48 kHz, every reading
carrying its own measured floor:

1. **The saw's curve, through the path it ships on.** −61.9 dB at note 40
   rising to −52.4 dB at note 100; **17.3 to 23.9 dB better** than the
   base-rate oscillator at every one of the six registers; degradation with
   pitch down from **2.88 to 1.90 dB/octave**, and flat within 0.9 dB above
   note 76. Headroom over the estimator's floor is 28.3–38.0 dB throughout.
   This is locked by `test_the_sawtooths_aliasing_floor_after_the_2x_
   decimator_is_locked` at ±1.5 dB.
2. **Against the references, at matched pitch.** The gap to **Mini V3 closes**
   from 17.7–19.9 dB to **−0.3 to +1.8 dB** (and Mini V3's unbypassable filter
   can only remove aliases, so those are upper bounds on our disadvantage). The
   gap to **Surge XT narrows** from 24.9–60.6 dB to **9.9–41.9 dB** and does
   not close.
3. **The claim that moves.** Ranked by worst margin over 110 Hz … 1760 Hz, the
   saw on the 2x path is now the **best** of our five waveforms against Mini V3
   (1.8 dB); the **square is the worst at 19.9 dB**, unchanged, because
   `PULSE2X=0`. Contract open item 15 therefore stays open, but it is an open
   item about rectangular waves, not about the saw.
4. **Two figures that are withdrawn rather than updated.** The 55 Hz column
   stays withdrawn: on the instrument the reference table was made with, every
   cell in that row — ours and both references — sits on a −53.4 dB floor.
   And the Mini V3 cell at 1760 Hz stays excluded, because what that rig
   produced there did not qualify as a saw.

**Every dB figure in this record names `12f51c5`**, and the run that produced
them is committed as `docs/saw-alias-2x-results.json` rather than transcribed.

## Alternatives considered

- **Delete the "before" test now that the fix has landed.** Rejected: the
  base-rate oscillator is *live shipped behaviour* for square, pulse, triangle
  and sine (`PULSE2X=0`), so its curve is a lock on something that can still
  regress, not a historical note. Both curves are kept, each labelled with the
  path it measures.
- **Re-render Surge XT and Mini V3 for this comparison.** Not possible here and
  not faked: the rigs need macOS VST3 bundles and `dawdreamer`, and this ran on
  a Linux build host — the same constraint DR 0019 met. The frozen
  `docs/reference-voice-report.txt` is used instead, with its provenance
  asserted before any margin is computed, and `--live-refs` left in the tool
  for a host that has the plugins.
- **Compare today's Blackman-Harris readings against the frozen table
  directly.** Rejected, and this is the substantive methodological choice.
  #87 wrote that table at `74ce6a0`; #132 rewindowed the estimator at
  `ba14af2` **four hours later** and never regenerated it. Both sides of every
  margin are therefore read with the *report's own* estimator, reconstructed
  out of its own commit, and accepted only because it reproduces the report's
  `ours/saw` and `ideal/saw` rows to 0.01 dB.
- **Quote the numbers and skip the injected-defect controls.** Rejected on
  `docs/verification-rules.md`: the drop-decimator control (issue #80) is
  carried inside the locked test so that the improvement is attributed to the
  FIR rather than to oversampling, and the tool's three preconditions each have
  a test that makes them fire.

## Consequences

- **Priority moves.** "Aliasing, and it worsens with pitch" as a *saw* defect
  is retired. The same mechanism on the rectangles — already implemented behind
  `oversample_pulse_2x`, already measurable, not selected — becomes the largest
  reference-backed gap in the voice and the argument for **#205** is now a
  measured 19.9 dB rather than an inherited claim.
- **A different defect becomes visible.** The sine carries −55 dB of
  inharmonic energy where an ideal sine is at the estimator's floor: 58.2 dB of
  margin against a closed form, with no fold-down mechanism to explain it,
  because a sine has no harmonics above Nyquist. The decimation filter does
  nothing for it and no reference row exists to size it. That is new work, not
  covered here.
- **Surge XT remains 9.9–41.9 dB ahead on the saw**, still worsening with
  pitch. Closing that is a further decision (more taps, a higher ratio, or a
  different correction) and DR-worthy in its own right; nothing here selects
  one. Note that raising the *ladder's* oversample ratio is separately not free
  — a 16x render is a different filter (`k_onset` 4.769 → 4.119, `f_osc` −120
  cents) and re-baselines everything measured before it.
- **Two artefacts are now known-stale and one is guarded.**
  `docs/reference-voice-results.json` predates #87 and carries the withdrawn
  flat −60 dB Surge rows; the tool refuses it by name, with a test that fails
  if the refusal ever stops being true. `docs/reference-voice-report.txt`
  predates #132's rewindowing and can only be read through the estimator it was
  made with. Regenerating either needs a host with the VST3 bundles.
- **The cost of the fix is not re-litigated here.** `_render_2x` runs the
  oscillator at 2x and convolves 31 taps per output sample; the area and timing
  consequences belong to the FPGA/RTL records, not to this measurement.

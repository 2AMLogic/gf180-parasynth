# The sawtooth's aliasing after the 2x decimation filter

Issue #61 asked whether the shipped fix closed the gap. **For the saw against
Mini V3 it is closed; against Surge XT it is narrowed by 15–19 dB and still
large; for the other waveforms nothing has changed, because nothing was
applied to them.**

Every figure below was measured on **2026-09-26 against commit `12f51c5`** by
`tools/measure_saw_alias_after_2x.py`, whose run is committed verbatim as
[`saw-alias-2x-results.json`](saw-alias-2x-results.json) with its own
provenance block (`dirty: false`). The tool's controls are
`tools/test_measure_saw_alias_after_2x.py`.
<!-- claim: test=tools/test_measure_saw_alias_after_2x.py::test_the_reconstructed_estimator_reproduces_the_frozen_tables_own_rows -->

## 1. Ours, before and after

`VoiceFx(oversample_2x=True)` renders the saw at 2x, applies the 31-tap
half-band `_DECIM2_TAPS`, and keeps every second sample. The six registers are
the ones `model/test_moog_acceptance.py`'s locked "before" test uses. 0.5 s at
48 kHz; `hr` is headroom over the estimator's own **measured** floor for that
call.

| note | f0 | base rate | hr | 2x + FIR | hr | the filter buys |
|---|---|---|---|---|---|---|
| 40 | 82 Hz | −42.9 dB | 47.2 | **−61.9 dB** | 28.3 | 19.0 dB |
| 52 | 165 Hz | −39.8 dB | 48.7 | **−57.5 dB** | 31.1 | 17.6 dB |
| 64 | 330 Hz | −36.7 dB | 53.8 | **−54.0 dB** | 36.5 | 17.3 dB |
| 76 | 659 Hz | −33.8 dB | 54.8 | **−52.1 dB** | 36.5 | 18.3 dB |
| 88 | 1.3 kHz | −31.0 dB | 58.5 | **−51.6 dB** | 38.0 | 20.6 dB |
| 100 | 2.6 kHz | −28.6 dB | 59.7 | **−52.4 dB** | 35.9 | 23.9 dB |

<!-- claim: test=model/test_moog_acceptance.py::test_the_sawtooths_aliasing_floor_after_the_2x_decimator_is_locked -->

**The degradation with pitch falls from 2.88 to 1.90 dB/octave, and above note
76 it stops**: −52.1, −51.6, −52.4 is flat within 0.9 dB over the top two
octaves. That matters more than the average, because the original complaint was
specifically that the error was worst where a lead line lives.

Every one of those twelve readings has at least 28 dB of headroom over its own
floor. None of them is the estimator.

**The gain is the filter, not the oversampling.** The same 2x oscillator
reduced by keeping the last sub-step — issue #80's defect — reads −34.3 dB at
note 40 against −42.9 at the base rate, i.e. **9 dB worse**. That control is
carried inside the locked test, so a green "after" number always has something
it is green against.

## 2. Against Surge XT and Mini V3, at matched pitch

**The references were not re-rendered.** `model/reference_rigs.py` hosts VST3
plugins through `dawdreamer` from `/Library/Audio/Plug-Ins/VST3`; this ran on a
Linux build host with neither, the same situation DR 0019 met. The reference
rows come from the frozen [`reference-voice-report.txt`](reference-voice-report.txt)
— which is why the pitches in this table are the six that file renders (55 Hz …
1760 Hz), not the six in section 1. A reference reading at 82 Hz does not exist
and is not invented.

| note | f0 | ideal | ours base | ours 2x | Surge | Mini V3 | 2x − Surge | 2x − Mini V3 |
|---|---|---|---|---|---|---|---|---|
| 33 | 55 Hz | −53.4 | −44.1 | −52.9 | −53.4 | −53.3 | *floor* | *floor* |
| 45 | 110 Hz | −112.9 | −41.7 | **−59.6** | −69.5 | −61.4 | +9.9 | **+1.8** |
| 57 | 220 Hz | −113.2 | −38.7 | **−57.0** | −80.1 | −57.4 | +23.1 | **+0.4** |
| 69 | 440 Hz | −113.2 | −35.5 | **−53.3** | −74.3 | −54.3 | +21.0 | **+1.0** |
| 81 | 880 Hz | −113.2 | −32.6 | **−51.8** | −79.3 | −51.5 | +27.5 | **−0.3** |
| 93 | 1760 Hz | −113.2 | −28.9 | **−46.8** | −88.6 | — | +41.9 | — |

Positive = we carry more inharmonic energy than the reference.

- **Mini V3: closed.** 17.7–19.9 dB before, **−0.3 to +1.8 dB** after — and at
  880 Hz we are 0.3 dB *better*. Mini V3's filter cannot be bypassed and can
  only remove aliases, so its row is a best case for it and these margins are
  upper bounds on our disadvantage. The true gap is at most this and may be
  smaller.
- **Surge XT: narrowed, not closed.** 24.9–60.6 dB before, **9.9–41.9 dB**
  after. Surge still worsens our relative position with pitch, so whatever is
  left is the same *shape* of defect at a lower level.
- **1760 Hz has no Mini V3 cell.** The frozen study excluded that row: what the
  rig produced there did not qualify as a saw (h6 −19.1 dB against a model
  expecting −15.6). An excluded row stays excluded rather than becoming a
  caveated number.
- **55 Hz is still not quotable.** The frozen estimator's own floor is −53.4 dB
  there and every cell in that row — ours and both references — sits on it.
  This is the column issue #61 withdrew once already; it is not revived. On
  today's Blackman-Harris estimator our 2x saw reads **−62.4 dB with 26.1 dB of
  headroom** at 55 Hz, a real measurement, but no reference reading exists on
  that instrument so there is no margin to take.

### Why a second estimator appears in that table

`docs/reference-voice-report.txt` was written by `74ce6a0` (#87, 2026-09-18).
`inharmonic_fraction_db` was rewindowed from Hann to Blackman-Harris by
`ba14af2` (#132) **four hours later on the same day**, and the report was never
regenerated. Comparing today's readings with those rows directly would mix two
instruments.

So the tool reconstructs the report's estimator out of the report's own commit
(`git show 74ce6a0:model/audio_measure.py`) and reads *both* sides of every
margin with it — and it refuses to print a margin at all unless that
reconstruction reproduces the report's own `ours/saw` and `ideal/saw` rows.
It does, to **0.01 dB at all twelve cells**. Handing the check today's
estimator instead makes it refuse, which is the control that says the check can
fail.
<!-- claim: test=tools/test_measure_saw_alias_after_2x.py::test_control_todays_estimator_is_refused_against_the_frozen_table -->

The window choice turns out not to matter for our own rows: from 110 Hz up the
two estimators agree on our 2x saw to **0.01 dB**. They differ only at 55 Hz,
which is exactly where the old one is at its floor.

### `docs/reference-voice-results.json` is wrong and is refused by name

It is the machine-readable half of the same study and #87 **did not regenerate
it** — `git log` puts it at `0929159`, before the fix. Its Surge rows still
read the flat ≈−60 dB at every pitch that #87 traced to the harness adding a
MIDI note per settle render. Reading it would republish a withdrawn figure, so
the tool refuses it by name with the reason attached, rather than leaving the
next person to rediscover it.
<!-- claim: test=tools/test_measure_saw_alias_after_2x.py::test_control_the_stale_results_json_is_named_and_refused -->

## 3. Is aliasing still "the largest known voice defect"?

Worst margin over 110 Hz … 1760 Hz, by waveform. 55 Hz excluded everywhere.
`vs ideal` is against the alias-free closed form — the one column that does not
depend on any plugin's settings being right.

| waveform | path in the selected baseline | vs Surge | vs Mini V3 | vs ideal |
|---|---|---|---|---|
| saw | **2x + FIR (`OSC2X=1`)** | **41.9** | **1.8** | 66.5 |
| square | base rate (`PULSE2X=0`) | 60.6 | **19.9** | 81.2 |
| pulse25 | base rate | 56.9 | — | 82.8 |
| triangle | base rate | — | 8.0 | 71.1 |
| sine | base rate | — | — | 58.2 |

**The answer is yes for the voice and no for the saw.** After the fix the saw
is the *best* of our five waveforms against Mini V3 and the **square/pulse
family is now the largest reference-backed gap in the voice**, unchanged at
17.7–19.9 dB because `PULSE2X=0` in the selected baseline. The same decimation
filter is already wired for those shapes behind `oversample_pulse_2x`; whether
to select it is issue #205, and this measurement is the argument for doing so.

Two things the table says that a reference comparison cannot:

- **The sine carries −55 dB of inharmonic energy where an ideal sine is at the
  estimator's floor** — 58.2 dB of margin against a closed form, with no
  aliasing mechanism to blame, since a sine has no harmonics above Nyquist to
  fold. That is a *different* defect from the one this issue tracked and the
  decimation filter does nothing for it.
- **Every dash is a refusal, not a zero.** Mini V3 has no 25 % pulse and no
  sine row; Surge's sine and triangle cells sat on the estimator's floor. A
  waveform with no qualified reference cell is ranked nowhere rather than
  ranked at zero.

## 4. What this does not say

- It does not say the saw is *fixed*. It is 66.5 dB from an alias-free
  waveform, and it still loses 1.9 dB per octave up to note 76. It says the
  measured gap to one reference is gone and to the other is 15–19 dB smaller.
- It does not measure the RTL. This is the integer model; `OSC2X=1
  FILTER2X=1` is the selected FPGA preset and `model/test_oversampled_osc.py`
  checks the coefficients and headroom match, but nothing here is a
  gate-level result.
- It does not re-derive the *audibility* of any of these numbers. Inharmonic
  fraction is an energy ratio, not a loudness.

**Wrong-then-right rate for this work: 1.** The warm-up test first asserted
that a cold decimator's first 30 samples were smaller than a warm one's, and
failed 0.877 against 0.123 — phase 0 is the sawtooth's own discontinuity, so
the two renders start at different parts of the waveform and the comparison was
meaningless. The corrected test asserts stream-continuation instead, and
records that the warm-up is worth ~0.001 dB at this record length, so nobody
spends time defending a choice that does not move the number.
<!-- claim: test=tools/test_measure_saw_alias_after_2x.py::test_the_warmup_is_a_continuation_and_costs_the_reading_nothing -->

# Paired comparison boundaries: who asserts preparation state

**Frozen 2026-10-10 against `origin/main` @ `06519797`**, plus the one wiring
this document ships with (#163, first slice). Line numbers and tags are true at
that commit and go stale with it. Re-run the search below before relying on
any row.

A paired comparison is fair when both sides are in the same state when the
shared function runs, not when they call the same function
(`docs/failure-modes.md`, "symmetry of code is not symmetry of treatment"). This
table lists where ours and a reference (or two recordings standing in for them)
are prepared and then measured against each other. For each site it says
whether that site **asserts** the preparation state of the pair, only
**records** it, or does **neither**.

**This is tagging only.** #163's first slice wires exactly one boundary, the
drum pair site in `tools/run_case.py`. Wiring any other row is out of scope and
is a separate issue.

## How the list was produced

```
git grep -nE "^def (prepare|condition)\b|assert_same_preparation|preparation_state" -- '*.py'
git grep -nlE "ref_y|ref_x|reference" -- 'tools/*.py' 'model/*.py' 'tools/probes/*.py' \
  | xargs grep -lE "rc\.prepare|prepare\(|condition\(" | grep -v "^tools/test_\|^model/test_"
```

The tag is mechanical:

- **asserts**: the site calls `preparation_contract.check_prepared_pair` or
  `assert_same_preparation` on the pair.
- **records only**: the site writes `lead_report`, `preparation_state` or
  `_prep` into its output, but does not refuse a mismatch.
- **neither**: the site does neither.

The "paired?" column is what reading the module header showed. "not traced"
means the header was not enough to say, and the callers were not walked. Treat
it as unknown, not as a no.

## The six named in #163

| site | preparation path | paired? | tag |
|---|---|---|---|
| `tools/run_case.py:3624` `drum_measurements` (`ref_y = prepare(…)`, `ours_y = prepare(…)`) | `run_case.prepare` on both sides | yes: every #282 drum case, and `tools/score_drum_i2s.py:171` through it | **asserts** (this slice: `preparation_contract.check_prepared_pair`, before any metric). It also **records** per-side `lead_report` in `windowing` |
| `tools/perceptual_gate.py:221` `condition` | its own path: resample to 48 kHz, AC-couple, align, level | yes (gate compares ours vs reference) | **neither** |
| `model/test_discrimination.py:319` `condition` | its own path: onset-align, cut, DC, high-pass, normalise | yes (real vs ours clips) | **neither** at the production call sites. The helper `assert_same_preparation` (`:299`) is called only by a test (`:1885`), and it compares `dur_ms`, which legitimately differs between sounds. **Superseded** for the drum pair by `tools/preparation_contract.py` and left as-is (not widened) |
| `model/condition_boundary.py:126` | `test_discrimination.condition` | yes | **records only** (`_prep` = `preparation_state`, and the median lead per side at `:219-221`) |
| `tools/measure_partial_balance.py` `prepare_pair` | `run_case.prepare` (was a private copy with the pre-#132 clamp, `lead = max(0, i - 1 ms)`; replaced in #636) | yes | **asserts** (`preparation_contract.check_prepared_pair` before any metric; `tools/test_measure_partial_balance.py` injects the clamp and expects REFUSED) |
| `tools/measure_conga_body_spread.py` `check_pair` | `run_case.prepare` (was a private "verbatim" copy that was the pre-#132 version; replaced in #636) | yes | **asserts** (`check_pair` in `windowed_alike`; `tools/test_measure_conga_body_spread.py` injects the clamp and expects REFUSED) |
| `tools/probes/estimator_defects.py:49` `prepare_shipped` | the pre-#132 `prepare`, kept on purpose as a historical control | control, not a comparison | **neither** (by design) |

The two private copies are the same shape as this issue in a second form: a
function copied as "identical on both sides" that stopped being identical to
the one it copied when #132 changed the original. Filed as #636 and closed by it.

## Everything else the search matched

All of these use `run_case.prepare` (`rc.prepare`) or a module-local
`prepare`/`condition`. They therefore inherit the guaranteed lead, but none
asserts the pair state.

| site | paired? | tag |
|---|---|---|
| `model/discrimination_run.py:78` | not traced | neither |
| `model/discrimination_trajectory.py:113` | not traced | neither |
| `model/measure_harness.py:267` | not traced | neither |
| `tools/bd_pitch_baseline.py:73` | not traced | neither |
| `tools/bd_pitch_predeclaration.py:163` | not traced | neither |
| `tools/clap_burst_timing_qual.py:125` | no: synthetic apparatus check | neither |
| `tools/clap_d12a_probe.py:41` | not traced | records only (`lead_report`, `:416`) |
| `tools/clap_final_strike_experiment.py:187` | not traced | neither |
| `tools/cymbal_bands.py:18` | not traced | neither |
| `tools/cymbal_candidate_eval.py:159` | not traced | neither |
| `tools/cymbal_m_origin.py:334` | not traced | neither |
| `tools/cymbal_mid.py:469` | yes (Fischer CY5025 vs ours, `:482-484`) | neither |
| `tools/cymbal_tone_knob.py:664` | not traced | neither |
| `tools/cymbal_tone_render.py:311` | not traced | neither |
| `tools/cymbal_vca_clip.py:520` | not traced | neither |
| `tools/diagnose_tom_body.py:186` | yes (`ref_y`/`ours_y`, `:186-187`) | neither |
| `tools/measure_m5a_attack_bias.py:94` | no: synthetic carriers | neither |
| `tools/measure_promoted_bands.py:698` | not traced | neither |
| `tools/measure_repeatability.py:268` | not traced | neither |
| `tools/noise_stage_attribution.py:165` | not traced | neither |
| `tools/probe_tom_numerator.py:134` | not traced | neither |
| `tools/probes/audio_distance_floor.py:431` | not traced | neither |
| `tools/probes/balance_line_shape.py:453` | not traced | neither |
| `tools/probes/excitation_energy.py:19` | not traced | neither |
| `tools/probes/hihat/hh_probe.py:187` | no: float-model-only probe | neither |
| `tools/probes/hihat/hh_probe2.py:135` | no: float-model-only probe | neither |
| `tools/probes/hihat/hh_probe5.py:526` | yes | records only (`lead_report` per side, `:558-559`) |
| `tools/probes/permitted_differences.py:1226` | not traced | neither |
| `tools/probes/rs_guard_band.py:167` | not traced | neither |
| `tools/probes/rs_mode_drive.py:206` | not traced | neither |
| `tools/probes/tom_conga_gate.py:351` | not traced | neither |
| `tools/probes/verify_109_claims.py:129` | not traced | neither |

Count at this commit: **1 asserts (and also records), 3 record only, 35
neither**, across 39 rows. The three that record only are
`model/condition_boundary.py`, `clap_d12a_probe` and `hh_probe5`. Seven rows
are named by #163, and 32 come from the wider search.

## A residual asymmetry inside `prepare()` itself

`run_case.prepare` estimates DC from the pre-onset region **only when the
record supplies at least 5 ms of it**. Our renders always do, because they start
with 10 ms of digital silence. No Fischer reference does, since each has 5 to 52
pre-onset samples. So the two sides of every drum pair get different treatment,
decided by state upstream of the call. The function's own docstring states this
and deliberately leaves it unfixed.

The boundary control in `tools/test_preparation_contract.py` measures the
effect on a known-answer fixture: a constant 1.0e-4 of peak, worth at most
0.22 % of any metric's tolerance (CH band energy, 0.0066 dB of 3.0 dB). That is
small on that fixture. It has not been measured on the real references, whose
converter offset is 0.1 to 0.4 % of peak, so it is an open item and not a
clearance. Filed as #637.

## Added 2026-11-26 against `origin/main` @ `4b4c4935`: a paired site the search does not reach (#1407)

The two tables above come from the search under "How the list was produced".
`model/osc_drift_probe.py` `across_note_drift` is in neither, and no entry
matches `osc_drift` or `drift`, so until this entry the site was recorded
nowhere. The search cannot reach it: no audio passes through it. Each window
is measured on its own by `drift_report`, and the comparison reads only the
reports that come back, so there is no shared `prepare` or `condition` to
find.

| site | paired? | tag |
|---|---|---|
| `model/osc_drift_probe.py` `across_note_drift` | yes: two or more windows of ONE note in ONE render, ours or a reference. `tools/measure_osc_drift_reference.py` reaches it with the frozen Mini V3 pairs, and the module's own `--wav` command reaches it with every window it is given | **asserts what the windows declare** (`f0_nominal`, `sr`); **neither** for whether they are one render. Not the **asserts** tag of this document: it calls no shared preparation helper |

The mechanical tag means one thing: the site calls
`preparation_contract.check_prepared_pair` or `assert_same_preparation` on the
pair. This site calls neither and prepares nothing. What it does is local to
`across_note_drift` and runs over the reports, which is why the row does not
borrow that tag. Whether a window is usable at all is a separate question,
filed as #1405, and not this row's subject.

It checks two fields, and only those two. Every usable window must declare the
same nominal pitch (`f0_nominal`) and the same sample rate (`sr`), compared
directly, before any span is computed. A mismatch in either returns `REFUSED`
in the aggregate result with the field and the offending window named, and no
drift value.
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_refuses_two_notes_each_stable_at_its_own_pitch -->
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_refuses_one_note_rendered_at_two_sample_rates -->
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_finds_a_mismatch_wherever_it_sits[3-2-f0_nominal-F0_NOMINAL_MISMATCH] -->

A window whose `f0_nominal` or `sr` is missing, or is not a finite positive
number, is refused the same way, so matching missing values do not read as
agreement.
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_refuses_when_no_window_declares_the_comparison_field[f0_nominal-F0_NOMINAL_MISMATCH] -->
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_refuses_when_no_window_declares_the_comparison_field[sr-SAMPLE_RATE_MISMATCH] -->

Measured pitch (`f0_measured_hz`) is not compared, because it is the
measurement: the same two tones that refuse when the second is declared 17
cents up are reported as a 17-cent drift when both declare one pitch.
<!-- claim: test=model/test_osc_drift_probe.py::test_across_note_still_measures_a_pitch_step_under_one_declared_pitch -->

The `--wav` command refuses the same way, read back from its `--json`.
<!-- claim: test=model/test_osc_drift_probe.py::test_cli_across_note_refuses_two_rates_under_one_declared_pitch -->

**What it cannot assert, and the caller must: that the windows come from ONE
continuous render.** Two renders, two takes, or two files that declare the
same pitch and rate pass every check here, and their difference is reported
as drift. The reports carry nothing that separates one render from two, so
the assumption is neither asserted nor recorded at the site, and inferring it
from filenames or labels was left out of #1407.

`tools/measure_osc_drift_reference.py` selects each of its three M1A pairs
by label prefix and note together (`m1a/phrase0/` with 36, `m1a/osc1_open/`
with 36 and `m1a/osc2_open/` with 48). Each prefix identifies one WAV that
the M1A capture script wrote from a single render. Re-read after the
WAV-length checks of #2080 were added to the tool: its selectors are
unchanged by them.
[M1A capture script](https://github.com/2AMLogic/gf180-parasynth/blob/91123a05807213ad767f9d0fca80a2720b13feae/tools/measure_mono_m1a_reference.py#L144-L169)
<!-- claim: grep='startswith\("m1a/phrase0/"\)' in=tools/measure_osc_drift_reference.py covers=tools/measure_osc_drift_reference.py -->
<!-- claim: grep='startswith\("m1a/osc1_open/"\)' in=tools/measure_osc_drift_reference.py covers=tools/measure_osc_drift_reference.py -->
<!-- claim: grep='startswith\("m1a/osc2_open/"\)' in=tools/measure_osc_drift_reference.py covers=tools/measure_osc_drift_reference.py -->

With the frozen WAVs verified, both MIDI-36 pairs return `TOO_FEW_PERIODS`
and the oscillator-2 MIDI-48 pair reports `STABLE` with a span of about
0.01018 cents.
<!-- claim: test=tools/test_measure_osc_drift_reference.py::test_m1a_pairs_keep_their_own_outcomes -->

The selectors also receive each refused window's label and note. Flipping one
bit in the oscillator-2 WAV makes all three windows report `CLIP_HASH`. The
MIDI-48 pair still identifies `n0-midi48` and `n2-midi48` and returns
`REFUSED` without a span. The intervening `n1-midi55` has a different note.
<!-- claim: test=tools/test_measure_osc_drift_reference.py::test_altered_osc2_wav_refuses_each_window_and_the_pair[mismatch] -->

The fourth pair, `m5a-midi84-saw-vs-pulse`, selects every report with the
`m5a/` prefix and note 84 (re-read after #2080: unchanged).
<!-- claim: grep='r\["label"\]\.startswith\("m5a/"\)' in=tools/measure_osc_drift_reference.py covers=tools/measure_osc_drift_reference.py note="the fourth pair's predicate reads the m5a/ label prefix and the note, so no M1A report can join it" -->

The M5A picker takes every window from the manifest's one WAV,
`m5a-miniv3-raw.wav`. Its timeline has two note-84 events, one in the saw
segment and one in the pulse segment.

The M5A capture script created a fresh Mini V3 instance for each take, then
joined the saw take, a gap of silence and the pulse take into that WAV.
The driver assigns capture identity only when the manifest names the reviewed
producer commit and records the SHA-256 of that producer file. A missing or
mismatched pin yields `CAPTURE_UNKNOWN`. With the producer pin and WAV digest
verified, the M5A pair returns `INDEPENDENT_CAPTURES` and reports the pitch
difference only as a comparison between takes (#1433).
[Rig creation](https://github.com/2AMLogic/gf180-parasynth/blob/899e86d68cf37bacf79188bce142b42ea2d13564/tools/measure_mono_m5a_reference.py#L152-L167) ·
[WAV assembly](https://github.com/2AMLogic/gf180-parasynth/blob/899e86d68cf37bacf79188bce142b42ea2d13564/tools/measure_mono_m5a_reference.py#L418-L449)

An M1A window cannot join that pair, whatever its note, because the prefix
excludes it. Any other caller of `across_note_drift`, including one passing
several `--wav` files to the module's own command, must still establish
render continuity to compare observations of one running oscillator. The
comparison leaves pitch between the observed windows unmeasured.
<!-- claim: test=tools/test_measure_osc_drift_reference.py::test_m5a_saw_and_pulse_takes_refuse_as_a_drift_measurement -->
<!-- claim: grep='fn = man\["audio"\]\["file"\]' in=tools/measure_osc_drift_reference.py note="the M5A picker reads one audio file for every window" -->
<!-- claim: commit=014fdbc6 covers=docs/scorecard/mono-m5a-miniv3/manifest.json note="the two note-84 events above were read from this manifest at this commit; a later commit to it makes the claim STALE so the count is re-derived with _windows_m5a" -->

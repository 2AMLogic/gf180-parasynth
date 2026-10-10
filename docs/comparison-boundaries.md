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
| `tools/run_case.py:3624` `drum_measurements` (`ref_y = prepare(…)`, `ours_y = prepare(…)`) | `run_case.prepare` on both sides | yes: every #282 drum case, and `tools/score_drum_i2s.py:177` through it | **asserts** (this slice: `preparation_contract.check_prepared_pair`, before any metric). It also **records** per-side `lead_report` in `windowing` |
| `tools/perceptual_gate.py:221` `condition` | its own path: resample to 48 kHz, AC-couple, align, level | yes (gate compares ours vs reference) | **neither** |
| `model/test_discrimination.py:319` `condition` | its own path: onset-align, cut, DC, high-pass, normalise | yes (real vs ours clips) | **neither** at the production call sites. The helper `assert_same_preparation` (`:299`) is called only by a test (`:1885`), and it compares `dur_ms`, which legitimately differs between sounds. **Superseded** for the drum pair by `tools/preparation_contract.py` and left as-is (not widened) |
| `model/condition_boundary.py:126` | `test_discrimination.condition` | yes | **records only** (`_prep` = `preparation_state`, and the median lead per side at `:219-221`) |
| `tools/measure_partial_balance.py:67` `prepare` | **a private copy that still carries the pre-#132 clamp** (`lead = max(0, i - 1 ms)`, `:73`) | yes | **neither** |
| `tools/measure_conga_body_spread.py:86` `prepare` | **a private copy, "verbatim from run_case.prepare", that is the pre-#132 version** (`:93`) | yes | **neither** |
| `tools/probes/estimator_defects.py:49` `prepare_shipped` | the pre-#132 `prepare`, kept on purpose as a historical control | control, not a comparison | **neither** (by design) |

The two private copies are the same shape as this issue in a second form: a
function copied as "identical on both sides" that stopped being identical to
the one it copied when #132 changed the original. They are filed separately
(see the PR for #163).

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
clearance. It is filed separately.

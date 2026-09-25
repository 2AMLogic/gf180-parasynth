# F1A–F1C cutoff-response baseline: legacy component and selected path

A measurement baseline only. **No sound change, no promotion, no production
edit.** `docs/scorecard/results/F1*.json` (the board) are untouched; everything
here is in this directory.

Reproduce (about 3 minutes, no plugin):

```bash
.venv/bin/python tools/refprofile_restore.py && .venv/bin/python tools/refprofile.py
.venv/bin/python tools/run_case.py F1A F1B F1C --results docs/scorecard/f1-baseline/legacy-component
.venv/bin/python tools/probes/f1_selected_path.py --level-sweep --json docs/scorecard/f1-baseline/selected-path.json
```

## 1. Frozen basis

| item | value |
|---|---|
| tree | origin/main `c993946` |
| reference | Surge XT 1.2.3, LP Vintage Ladder Type 2 (`VintageLadder::Huov`), frozen clips `lp-cut250/1000/4000-res0.00` (sha256 `bd2279368babf5b6` / `042d97c684b418f5` / `e18a16e6f2203fdf`) and `lp-open20k-res0.00` (`ad30cebd73b77be3`). Restored from `refprofile/frozen-cache.zip` (`a478639c95465c08`) against `refprofile/profile.json` (`c49b2e9046de0c13`): 16 restored and 16 verified, 0 failed or absent (`restore.log`, `verify.log`). No plugin was run and no clip was substituted. |
| stimulus | the profile's stepped tone: 32 tones from 40 Hz to 12 kHz (log grid), amplitude 0.25 (−12.04 dBFS), 48 kHz, resonance 0 (k = 4·res), drive 1.0. These are the same settings as the committed F1 records. |
| ROMs | `GROM_BITS` 7, `KROM_BITS` 5, `CUT_TRIM` 1.030, coefficient oversample 2. The g ROM `48c6c974faf01776` and k ROM `f27ec0e198d0b1f8` are identical on both paths. |
| selected engine | `mono_m5a_score.ENGINE_PROFILES["selected"]`, `selected-m5a-reconstructed-filter2x`: `RateConvertedLadder` factor 2, causal (20-frame latency), int32 headroom preserved, `LADDER_CFG` with `out_bits` 19, integer ROMs, `k_comp` on |
| legacy engine | `reference_rigs.OurLadder` (`run_case.our_filter_curve`), which is the base-rate ladder with a 2x zero-order-hold subframe loop |
| code hashes | `run_case.py` `77739a9434f2605e`, `audio_measure.py` `cd27a0a67dfde218`, `reference_rigs.py` `99b1c862857a34a8`, `voice_fx.py` `eb26dc7d7a9e9cea`, `filter_rate_chain.py` `134b958dec5308fd`, `fixed.py` `f5687bec088f7fd9`, `mono_m5a_score.py` `a27fbddee20a0827` |

## 2. What changed in the measurement (the curves did not change)

On today's legacy curves the `531aa8a` estimators reproduce every committed
board number to 4 decimal places: ours corner, low-band and rolloff, plus the
reference corner and rolloff (`committed_531aa8a_reproduced: true` for all three
cases). The response data is therefore unchanged, and every difference below
comes from the estimator (#178, which moved `filt_rolloff` onto the
DC-extrapolated plateau).

| case | ours rolloff @531aa8a | ours rolloff now | Δ | reference @531aa8a → now | verdict @531aa8a → now |
|---|---|---|---|---|---|
| F1A | −18.54 | −16.87 | **+1.67** | −18.69 → −18.69 | pass → **fail** (error 0.15 → 1.82, tol 1.5) |
| F1B | −15.99 | −15.99 | 0.00 | −17.92 → −17.92 | fail → fail (1.93) |
| F1C | −15.32 | −15.32 | 0.00 | −16.41 → −16.41 | pass → pass (1.09) |

The corner and low-band gain do not change: they were already on the current
estimator. The F1A rolloff failure is a measurement change, not a sound change.

## 3. The table (current estimator, −12.04 dBFS, resonance 0)

| case | path | corner Hz | corner err % | rolloff dB/oct (err) | low-band gain dB (err) | valid | within tol (corner / rolloff / gain) |
|---|---|---|---|---|---|---|---|
| F1A (250 Hz) | Surge Type 2 reference | 117.63 | -- | -18.69 | -0.91 | yes | -- |
| | legacy component (OurLadder) | 97.85 | -16.82 | -16.87 (+1.82) | -1.28 (-0.37) | yes | **no** / **no** / yes |
| | selected path (RateConvertedLadder) | 97.88 | -16.80 | -16.88 (+1.81) | -1.28 (-0.37) | yes | **no** / **no** / yes |
| F1B (1000 Hz) | Surge Type 2 reference | 450.53 | -- | -17.92 | -0.26 | yes | -- |
| | legacy component (OurLadder) | 376.35 | -16.47 | -15.99 (+1.93) | -0.23 (+0.02) | yes | **no** / **no** / yes |
| | selected path (RateConvertedLadder) | 376.41 | -16.45 | -15.98 (+1.94) | -0.23 (+0.02) | yes | **no** / **no** / yes |
| F1C (4000 Hz) | Surge Type 2 reference | 1718.58 | -- | -16.41 | -0.14 | yes | -- |
| | legacy component (OurLadder) | 1433.50 | -16.59 | -15.32 (+1.09) | -0.06 (+0.07) | yes | **no** / yes / yes |
| | selected path (RateConvertedLadder) | 1437.05 | -16.38 | -15.11 (+1.30) | -0.07 (+0.07) | yes | **no** / yes / yes |

Tolerances are unchanged: corner 10 % of the reference, rolloff 1.5 dB/oct, gain
3 dB. The table is generated from `selected-path.json`. The legacy rows equal
`legacy-component/F1*.json`, which `run_case` wrote at `77739a9434f2`.
Surge is the filter-implementation comparator here. It is not hardware Model D
identity.

## 4. The selected path is what was measured

- **Identity:** `check_identity` refuses anything other than a causal,
  headroom-preserving 2x `RateConvertedLadder` whose ROMs are
  `make_g_rom/make_k_rom(…, 2)`.
- **Exact match:** the probe played a real selected-voice note (A2, 0.25 s,
  cutoff envelope 404–4153 Hz). It then fed that voice's integer mixer output
  and cutoff trace through the probe's `render_path`. The g stream, k stream
  and ladder output words showed **0 mismatches in 12,000 frames**, at a ladder
  RMS of 11,867 Q15, so the match is not vacuous.
- **Control, wrong path:** `--inject WRONG_LADDER_PATH` measures the legacy
  profile's `LadderFx`. The probe refuses it by name ("ladder is LadderFx, not
  RateConvertedLadder"). Its output differs from the selected component in
  **11,996 of 12,000 frames** while g and k match exactly, and the probe exits
  1 (`control-wrong-ladder-path.log`). This check is needed because **the F1
  numbers alone cannot tell the two paths apart.** At F1A and F1B they agree to
  0.03 % in corner and 0.01 dB/oct in rolloff. At F1C they differ by +0.25 %
  in corner and +0.21 dB/oct in rolloff, from the 0.1–0.5 dB by which the rate
  chain differs above 5 kHz.

## 5. Discrimination controls (`controls.log`, `controls-run_all.json`, 7/7 PASS by exit status)

| job | result |
|---|---|
| `tools/test_f1_selected_path.py`: identity, exact match, wrong path refused and mismatched, non-causal and clipped-headroom refused, tampered reference refused for its hash | 9 passed |
| `tools/test_run_case.py -k 'filt_ or filter'`: known-answer tests for corner and rolloff on ideal 2/4/6-pole filters, the no-corner refusal and the missing-cache no-verdict | 16 passed |
| `model/test_filter_rate_chain.py` | 17 passed |
| `run_case F1A-C --inject REF_PROFILE_MISSING --expect 'no verdict'` | refused 3/3: "not a clip in the frozen reference profile" |
| `run_case F1A-C --inject REF_PROFILE_TAMPERED --expect 'no verdict'` | refused 3/3: "hashes 042d…, the profile says 142d…" |
| `run_case F1A-C --inject REF_CORNER_2X --expect changed` | changed 3/3 (worst 1.68 → 6.45 / 6.97 / 6.77) |
| `tools/refprofile.py` | 16 verified |

Both refusals give the stated reason. Neither is an absent cache or an import error.

## 6. Usable probe range of the selected path

- **Frequency:** from 40 Hz to about 4 kHz the selected path matches the legacy
  component within 0.05 dB. Above about 5 kHz the rate chain differs by 0.1–1.1
  dB. The −70 dB stopband floor ends the usable curve at 1.59 kHz for F1A,
  6.9 kHz for F1B and 12 kHz for F1C. The F1C rolloff fit band is capped at
  9 kHz. With the ladder commanded at 20 kHz, the wide-open curve falls 6.35 dB
  by 12 kHz (the legacy curve falls 6.71 dB). Low-band gain reads only its
  40–100 Hz band, so this droop does not affect it.
- **Level** (`level_sweep` in `selected-path.json`): from −36 to −6 dBFS there
  are no refusals and no reconstruction clipping (peak 9,313 Q15 at −12 dBFS).
  **The corner is level-dependent**, and the table's −12 dBFS is not in the
  small-signal region:

  | level dBFS | −36 | −30 | −24 | −18 | **−12** | −6 |
  |---|---|---|---|---|---|---|
  | F1A corner err % | −7.64 | −7.64 | −7.63 | −9.51 | **−16.80** | −33.13 |
  | F1B corner err % | −4.20 | −4.19 | −4.18 | −6.95 | **−16.45** | −40.92 |
  | F1C corner err % | −3.66 | −3.65 | −3.63 | −5.92 | **−16.38** | −43.13 |

  The error is measured against the reference's only frozen level, −12 dBFS.
  `refprofile/README.md` documents Surge Type 2 as level-independent over this
  range (thermal = 1/70). **This deliverable did not re-verify that claim**,
  because no second-level reference clip is frozen. The legacy component
  follows the same curve, within 0.4 % in corner at every level. At −24 dBFS
  and below, F1A rolloff is −17.8 dB/oct, within tolerance of −18.69.

## 7. Recommendation

The "constant ≈ −16.6 % low corner" is **mostly a level effect, not a cutoff
mapping error**. At small signal the corner error is −7.6 / −4.2 / −3.7 %, and
it is not constant: its 4-point trend is well above the estimator's ~1 %
residual. At −12 dBFS the ladder's own saturation adds another 9–13 points and
makes the result look uniform. So the F1A rolloff failure and all three corner
failures depend on level.

**One next parameter: the ladder's input level scaling** (the drive→`gain`
register and `volts_per_unit`, i.e. where our tanh stages start to compress
relative to Surge's thermal scaling). Do **not** derive a `CUT_TRIM` correction
from the −12 dBFS numbers, because it would absorb about 10 points of
saturation. The precondition before any change: freeze one Surge Type 2 clip at
−24 dBFS with the same stimulus, to verify the reference's documented level
independence. Until then, the small-signal comparison above is a hypothesis.

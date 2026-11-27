# Rounded-verdict audit: the 544 census pairs, read

Review record for #1441: the reading of the census that #1434 committed. Question asked of every
group: **does a full-precision decision exist that a comparison of the
rounded value can override, as `best_correlation` did in #1334?** Sound
requirement behind it: mono attack qualification (M1A) and per-note M5A/M5B
decisions must not flip because a number was rounded for display.

## Result in one paragraph

The per-note M5A/M5B paths preserve their producers' full-precision
decisions. Two cymbal tools can accept a 3.0004 dB error against a 3.0 dB
bound, and the M1A attack aggregate can count a 5.000004 ms worst-event error
as passing while the scorer has already failed that event. All three reversals
were constructed. Of the 544 census pairs in 30 fields, **61 point to three
defect groups**: `worst_db` (5 nodal, 32 knob) and `tolerance` (24, the M1A
attack property). **483 are not a defect** and **0 are unresolved**. The counts
are name matches grouped by comparison site. Each reversal occurs within one
tool chain.

## Population and method

| item | value |
|---|---|
| source revision | `8e8551b3` (`origin/main`, merge of #1443 = #1434; `tools/` and `model/` read from the worktree, which equals main there) |
| command | `python3 -I tools/rounded_verdict_census.py .` |
| report line | `files=314 test-files-skipped=261 rounded-stores=571 ordered-field-compares=479 cross-module-pairs=544` |
| fields | 30 |
| difference from the issue's snapshot | the issue quotes 30 fields / 531 pairs at `4b56692d`; main has 544 (+13, same 30 fields; `compares` 473 -> 479). This review covers **544 at `8e8551b3`**. |

**Control for the method (#1334).** At `65d5c023` the census reports
`best_correlation` (`model/measure_harness.py:363` stored,
`tools/measure_conga_body_spread.py:503` compared; 545 pairs). At `00409889`
(PR #1421 head) that block is absent (0 `best_correlation` lines). The method
reads the repaired consumer as removed, which is what it must do.

**Reconciliation.** Per field, `stores x compares` minus same-file partners
equals the pair count (e.g. `value`: 15 x 23 - 23 same-file = 322;
`worst_db`: 6 x 9 - 17 = 37). The totals, from `census()`:

    amp 7, d_unwanted_rel_db 1, db 2, decay 1, error 45, explained_ratio 4, f0 2,
    f0_hz 13, fit_residual_db 1, headroom_db 3, inharmonic_db 1, level 3, margin 2,
    margin_db 34, max 6, max_abs_error_ms 1, median 1, onset_s 4, over_leak_1s_db 1,
    peak 3, peak_db 2, residual_db 1, rms 6, rms_dbfs 4, tol 2, tolerance 33,
    value 322, worst 1, worst_cents 1, worst_db 37        = 544

**How it was read.** Groups, not individual pairs: a census pair is a name
match, so every store and every comparison *site* was opened, the value
the comparison reads was traced to its producer, and sites sharing a producer
or a consumer were grouped. A pair is **defect** only when (a) the producer
makes a decision at full precision and (b) the consumer re-decides it from the
rounded field. When only the rounded value ever decides, there is nothing to
override: the effective threshold moves by at most half a rounding step, the
same at every consumer.

**Depth and corrections.** All three defect groups have boundary runs below.
The other classifications rest on source traces. Two initial readings were
corrected. At `model/tom_drop_docs.py:229`, `c["worst"]` comes from a local
unrounded maximum. The first reading attributed it to `measure_harness`.
The first audit also missed the full-precision count inside `fit_balance` and
classified the 32 knob pairs as rounded-only. The one-point control below
exposes that disagreement. The first audit also classified all 33 `tolerance`
pairs as rounded-only, which missed the producer's per-event attack grade; the
M1A section below corrects it. The conga corpus trace is recorded in the
`f0_hz` row.

## Defects in the cymbal tools

**`worst_db`, 5 pairs: `tools/cymbal_tone_nodal.py:385` vs the five rounded
`worst_db` stores in `model/ladder_candidates.py:1069`,
`tools/cymbal_tone_knob.py:455,459`, `tools/cymbal_tone_render.py:388,477`.**
The census pairs `cymbal_tone_nodal.py:385` with those five stores by name
only; the live defect is the same-file pair inside `cymbal_tone_nodal.choose`
(its own store at `:380` is excluded from the census as same-module):

- Full-precision decision, `cymbal_tone_nodal.py:375`:
  `key = (worst > SHAPE_BOUND_DB, ...)` on the unrounded `worst`.
- The record stores `"worst_db": round(worst, 3)` and `choose` then re-decides,
  `:385`: `if got["worst_db"] > SHAPE_BOUND_DB: raise Refused(...)`.
- `SHAPE_BOUND_DB = 3.0` (`tools/cymbal_tone_realisation.py:74`).

Reproducible boundary (constructed, not a shipped recording):
`shape_error_db` forced to a constant, `choose("low", cfg)` run from the
worktree on Python 3.12 (numpy, scipy only):

| worst (full precision) | full-precision verdict | `choose` result |
|---|---|---|
| 3.0004 | outside bound | **ACCEPTED**, `worst_db = 3.0` |
| 3.0006 | outside bound | REFUSED |
| 2.9996 | inside bound | ACCEPTED (nearby value, both agree) |

The rounded comparison admits the first out-of-bound realisation. The repair
can forward the decision in `key[0]`. [Nodal repair #1499](https://github.com/2AMLogic/gf180-parasynth/issues/1499)

**`worst_db`, 32 pairs in `tools/cymbal_tone_knob.py`.** Eight comparisons
each match four external stores by name (`ladder_candidates.py:1069`,
`cymbal_tone_nodal.py:380`, `cymbal_tone_render.py:388,477`). All eight
actually read the top-level `worst_db` from `fit_balance` at line 455. The
producer selects the smallest unrounded worst residual and rounds it to three
decimals for display. In the same record, line 465 counts balances with
`worst <= BOUND_DB` at full precision. A positive count means the best fit
meets the bound. Independently, `balance_region` applies the same default
3.0 dB bound to the unrounded residuals at line 486 and refuses an empty region.

| Consumer in `cymbal_tone_knob.py` | Decision using the rounded fit |
|---|---|
| `shape_audit:529`, `_alpha_law:806` | Mark a shape reachable or the tone law acceptable at or below the bound. |
| `_alternative_fails:846` | Mark a structural alternative excluded above the bound. |
| `verdict:968,981,983,985` | Classify the headline, classify and describe each fitted alternative, and list mappings inside the bound. The headline fit at line 955 also passes through `balance_region` at line 962, so an empty headline region raises before line 968. |
| CLI `main:1065` | Print whether the alternative fit exceeds the bound. |

This constructed input fixes the balance grid at one point. Adding a known
delta to the last TONE setting leaves the anchor intact and makes that delta
the worst residual. Run from the repository root at `8e8551b3` with NumPy and
SciPy installed.

```python
from unittest.mock import patch

import numpy as np
from tools import cymbal_tone_knob as knob

g = knob.gain_table()
prediction = knob.predicted_hml(g, np.zeros(3))
with patch.object(knob, "BALANCE_DB", np.array([0.0])):
    for delta in (3.0004, 3.0006, 2.9996):
        column = dict(zip(knob.CODES, prediction))
        column[knob.CODES[-1]] += delta
        measured = {knob.ANCHOR_TONE: column}
        fit = knob.fit_balance(g, measured)
        try:
            region = knob.balance_region(g, measured)["n_inside"]
        except knob.Refused as exc:
            region = str(exc)
        ok = knob._alpha_law(g, measured)["ok"]
        print(delta, fit["n_inside_bound"], region, fit["worst_db"], ok)
```

| Added residual (dB) | Expected at 3.0 dB | `n_inside_bound` | `balance_region` | Displayed `worst_db` | Observed `_alpha_law["ok"]` |
|---:|---|---:|---|---:|---|
| 3.0004 | outside | 0 | REFUSED, no balance reaches 3.0 dB | 3.000 | **True** |
| 3.0006 | outside | 0 | REFUSED, no balance reaches 3.0 dB | 3.001 | False |
| 2.9996 | inside | 1 | admits one balance | 3.000 | True |

Only the first row reverses the law verdict. The construction measures how the
tool decides at the boundary. Its effect on shipped recordings is unmeasured.
[Knob repair #1505](https://github.com/2AMLogic/gf180-parasynth/issues/1505)

## Defect in the M1A attack selection

**`tolerance`, 24 of 33 pairs.** The three stores (`measure_repeatability.py:930`,
`probes/f1_selected_path.py:259`, `run_case.py:3824`) are never the producer
of the value read. The eleven comparison sites read `properties[...]` of a
measurement from one of two producers, so the sites were split by producer and
by the property they read:

| comparison site | reads | verdict |
|---|---|---|
| `check_m1a_selection.py:91` (`_passes`), `:189` (printed grade) | M1A properties from `mono_m1a_score.compare_audio` (live candidate, or the stored record's `diagnostics.properties`) | **defect** for `Envelope attack` |
| `measure_m1a_drive.py:61` (`passes`, recorded at `:204`; compared at `:77`) | same producer | **defect** |
| `measure_m1a_volume_mapping.py:35,36` (`counts`), `:110,112` (preservation of a baseline pass) | same producer | **defect** |
| `sweep_m1a_attack.py:65` (`passes`, used at `:136`, `:158`) | same producer | **defect** |
| `measure_m1a_volume_mapping.py:107` | per-note gain recomputed from `events[].rms_dbfs` (full precision) against the Gain tolerance | NC: not the rounded field. The `:110,112` Gain comparison is subsumed, since `:107` refuses first on any note above the Gain limit |
| `measure_m5a_filter_oversample.py:266` (and `:69-70`) | `mono_m5a_score.measure` metrics | ONLY, see below |
| `clap_d12a_probe.py:207` | `run_case.measure_pair`, `error`/`tolerance` rounded to 4 dp | ONLY: `measure_pair` keeps no full-precision pass |

Eight defect sites x 3 stores = **24 pairs**; the other three sites x 3 = 9
pairs are not a defect. This follows the convention used for the knob group: a
cross-module name match is counted by the comparison site that reads a live
defect. Every one of the eight sites reads all M1A properties; only
`Envelope attack` has a producer-side decision to override. `Pitch`,
`Harmonic shape`, `Envelope release`, `Gain` and `Clipping` are decided only
from the rounded aggregate (ONLY).

Producer-to-decision trace for `Envelope attack`:

- `mono_m1a_score.py:270-274`: each event's `attack_state` is graded at full
  precision, `abs(model - reference) <= 5.0`, or `unqualified`.
- `:277-293`: the property takes the worst event by the same difference and
  `mono_m5a_score._metric` (`:162-167`) stores `error` at 5 dp. The tolerance
  is `5.0` (`:287-292`).
- The eight sites re-decide `abs(error) <= tolerance` from that rounded error.
- The board repeats it: `required_metrics` (`:314-332`) stores the normalized
  `envelope` distance through `_metric` again, and `scorecard.metric_distance`
  reads it. This is outside the 33 census pairs; the same repair covers it
  ([repair #1509](https://github.com/2AMLogic/gf180-parasynth/issues/1509)).

Reproduced on constructed three-note phrases (the reproducer is in #1509; run
from the repository root at this branch's merge of `8e8551b3`, Python 3.12,
NumPy, SciPy; only the attack ramp of the two MIDI 36 events changes, the
middle note is identical and is the internal pass control):

| worst attack error (ms, full precision) | event grades | stored `error` | `_passes` includes attack | board `envelope` distance |
|---:|---|---:|---|---:|
| 5.000004425139033 | fail, pass, fail | 5.0 | **yes** (reversal) | 1.0 (inside) |
| 5.016671091805701 | fail, pass, fail | 5.01667 | no (agrees) | 1.00333 |
| 4.983337758472366 | pass, pass, pass | 4.98334 | yes (agrees) | 0.99667 |

Only the first row reverses the verdict. #1509 records that the committed M1A
board record already marks attack and envelope unqualified, so no shipped
decision is known to flip. The effect on shipped recordings is unmeasured.

**M5A is distinct.** `mono_m5a_score.measure` (`:447-462`) selects the worst
pair per metric by the unrounded `abs(ours - ref)` and then emits it through
`_metric`. That is a selection of *which* measurement to report, not a
verdict: no pass/fail is made at full precision there, so the later
`error <= tolerance` comparisons (`measure_m5a_filter_oversample.py:69-70,266`)
are the only decision. ONLY; the effective threshold moves by at most half of
the 5th decimal.

## Groups: not a defect

`NC` = name collision: the comparison reads a different object than the
rounded store (numpy array, dataclass attribute, a constant, a local, another
record with the same key). `ONLY` = the rounded value is the only decision
and every consumer sees the same rounded value.

| field (pairs) | group, sites, pairs | verdict and reason |
|---|---|---|
| `value` (322) | 15 dict/keyword stores x 23 compares, all `[attribute]` (`.value` of an `Estimate`, an AST node, etc.), 322 | NC. A dict key cannot be read by attribute; the one keyword store (`measure_saw_alias_after_2x.py:441`) builds a `dict(...)` |
| `error` (45) | 9 stores x 5 compares (`compare_m5a_i2s_candidate.py:184,185,194`, `measure_mono_pulse_2x.py:115` x2), 45 | NC, the control. The compares read the dict built by `_event_errors` (`compare_m5a_i2s_candidate.py:101`) from `event_diagnostics` fields, not the `error` stores. Note the real per-note producer (`mono_m5a_score.py:428-439`, rounded to 4-5 dp) has no full-precision per-note verdict; the aggregate metrics are decided from unrounded `observed`, and the per-note comparator is a separate decision of its own: ONLY (shift <= 1e-4 of ms/dB/cents) |
| `tolerance` (9 of 33) | 3 stores x 3 compares: `measure_m1a_volume_mapping.py:107` (NC, per-note gain), `measure_m5a_filter_oversample.py:266`, `clap_d12a_probe.py:207`; the other 24 are the M1A defect above | NC / ONLY. `run_case.measure_pair` (`:3821-3824`) stores `error` and `tolerance` rounded to 4 dp and keeps no full-precision pass; the M5A producer selects its worst measurement unrounded but decides nothing there (see the M1A section) |
| `explained_ratio` (4) | 2 stores (`attack_fit_v3.py:109`, `measure_mono_m1a_reference.py:266`) x 2 compares (`qualify_m1a_attack.py:69,104`), 4 | ONLY. The full-precision gate (`explained < 0.6`) refuses upstream of the round; the floors (0.7 to 0.99) are applied only to the rounded value, both when the domain table is built (`:69`) and when it is used (`:104`) |
| `max_abs_error_ms` (1) | `qualify_m1a_attack.py:84` -> `mono_m1a_score.py:135` | ONLY, and monotone: the producer keeps rows with `worst <= 1.0` at full precision, and `round(worst, 4) <= 1.0` follows because the bound is on the rounding grid, so the guard cannot flip a pass |
| `d_unwanted_rel_db` (1) | `mono_artifact_probe.py:596` vs `measure_m5_saw_drive.py:158` | NC for the pairing: `:158` reads the delta that module builds at `:153`. That in-module rounded delta (3 dp) against `<= 1.0` is ONLY (M5B saw drive; shift <= 0.0005 dB on a 1 dB limit) |
| `margin_db` (34) | 18 pairs: `probes/residual_dc.py:268,1632` x 9 stores; 16 pairs: `measure_saw_alias_after_2x.py:560,569,587,596` x 4 cross-module stores | NC for the 18 (`st['margin_db']` is computed unrounded in-module). The 16: a running-max tracker compares an unrounded candidate with the rounded stored best; it chooses which note is reported worst, not a verdict. ONLY |
| `rms` (6), `rms_dbfs` (4) | `lead_drive_337.py:75` (local `summarise`), `reference_compare.py:394`, `measure_mono_m1a_reference.py:438` (unrounded local) | NC |
| `amp` (7), `db` (2), `decay` (1), `f0` (2), `level` (3), `max` (6), `median` (1), `onset_s` (4), `peak` (3), `peak_db` (2), `tol` (2), `worst` (1) | comparisons read arrays, dataclass attributes, constants (`rule['peak_db']`, `TOL['level']`, `np.iinfo().max`), baselines or per-record locals; the `worst` case is the wrong-then-right above | NC (all 34 pairs) |
| `f0_hz` (13) | 13 external stores × 1 comparison in `measure_conga_body_spread.py:554` | NC for all 13. In the same file, `measure_corpus` (lines 372–388) stores `round(f, 3)` for a valid `f0_of` result. Line 712 saves those rows as `report["corpus"]` and line 743 passes them to `within_10pct_of_f0`. Lines 553–554 read both the anchor and each candidate from those rows. ONLY in-module, since the ±10% selection first occurs on the rounded values. |
| `fit_residual_db` (1), `headroom_db` (3), `inharmonic_db` (1), `margin` (2), `over_leak_1s_db` (1), `residual_db` (1), `worst_cents` (1) | `audio_measure.py:2202` and `estimator_domains.py:411` read `Estimate.detail` (unrounded); `reference_voice.py:394` is a `>` display marker; `noise_fixture_duration.py:419` is a printed line; `cymbal_400_production_tail.py:230` re-checks `over_leak_1s_db` that `cymbal_mid` never tests at 1 s (its own gates are `edt`/`late`); `r0_capture.py:957` and `compare_ladder_candidates.py:575` read their own records | NC or ONLY (shift <= 0.005 dB / 0.05 cents against 6 dB / 5 cents limits) |

The clear groups sum to 322 + 45 + 9 + 4 + 1 + 1 + 34 + 10 + 34 + 13 +
10 = 483. Adding the 5 nodal, 32 knob and 24 M1A pairs accounts for all 544.

## Outside the census population (not counted above)

The census omits same-module pairs and values passed through a local. At
`tools/r0_capture.py:956-957` (`residual_db`) and
`tools/measure_m5_saw_drive.py:153-158` (the saw-drive delta), the rounded
record supplies the only threshold decision. The conga selection above has
the same property. In both cymbal tools and in the M1A attack aggregate, the
producer also makes the decision at full precision. Cross-module name matches led to those consumers, and the
same-module traces and boundary runs establish the defects.

## Reproduce

    python3 -I tools/rounded_verdict_census.py .                 # at 8e8551b3
    git archive --format=tar 65d5c023 tools model | tar -x -C /tmp/pre
    python3 -I tools/rounded_verdict_census.py /tmp/pre | grep -A2 '^best_correlation'

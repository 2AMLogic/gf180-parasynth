# Rounded-verdict audit: the 544 census pairs, read

Review record for #1441: the reading of the census that #1434 committed. Question asked of every
group: **does a full-precision decision exist that a comparison of the
rounded value can override, as `best_correlation` did in #1334?** Sound
requirement behind it: mono attack qualification (M1A) and per-note M5A/M5B
decisions must not flip because a number was rounded for display.

## Result in one paragraph

Of 544 pairs in 30 fields, **5 pairs (1 group, field `worst_db`) are a
defect** with #1334's exact shape, in the **cymbal** tone-network tool, not in
the mono path. **539 are not a defect.** **0 are "cannot tell".** None of the
mono attack (M1A) or per-note M5A/M5B consumers re-decides a verdict that the
producer made at full precision. Three in-module sites outside the census
population (below) are the same arithmetic with no overridden verdict.

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

**Depth, stated plainly.** Boundary execution was done for the one defect
(below) and for the `best_correlation` control only. For "not a defect"
groups, the argument is the trace, not a run. No "cannot tell" remains because
every comparison read resolved to a local, an attribute, a constant, or a
record with no full-precision twin (the `f0_hz` corpus origin is the one
untraced read, and it is bounded by the window argument). Wrong-then-right on this review: 1
(`model/tom_drop_docs.py` `c["worst"]` first read as a consumer of
`measure_harness`'s rounded `worst`; it reads a local unrounded `float(np.max
...)` at `tom_drop_docs.py:229`).

## The defect

**`worst_db`, 5 pairs: `tools/cymbal_tone_nodal.py:385` vs the five rounded
`worst_db` stores in `model/ladder_candidates.py:1069`,
`tools/cymbal_tone_knob.py:455,459`, `tools/cymbal_tone_render.py:388,477`.**
The census pairs `cymbal_tone_nodal.py:385` with those five stores by name
only; the live defect is the same-file pair inside `cymbal_tone_nodal.choose`
(its own store at `:380` is excluded from the census as same-module):

- Full-precision decision, `cymbal_tone_nodal.py:~374`:
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

So an out-of-bound realisation within 0.0005 dB of the bound is accepted.
Consequence on sound: negligible (a cymbal tone-network shape error 0.0005 dB
over a 3 dB bound, no reachable audible difference). It is a real instance of
the class and the repair is #1334's: take the verdict from the unrounded
`key[0]` (forward it) instead of recomputing from `got["worst_db"]`. Repair
work is out of scope here; repair is tracked in #1499.

## Groups: not a defect

`NC` = name collision: the comparison reads a different object than the
rounded store (numpy array, dataclass attribute, a constant, a local, another
record with the same key). `ONLY` = the rounded value is the only decision
and every consumer sees the same rounded value.

| field (pairs) | group, sites, pairs | verdict and reason |
|---|---|---|
| `value` (322) | 15 dict/keyword stores x 23 compares, all `[attribute]` (`.value` of an `Estimate`, an AST node, etc.), 322 | NC. A dict key cannot be read by attribute; the one keyword store (`measure_saw_alias_after_2x.py:441`) builds a `dict(...)` |
| `error` (45) | 9 stores x 5 compares (`compare_m5a_i2s_candidate.py:184,185,194`, `measure_mono_pulse_2x.py:115` x2), 45 | NC, the control. The compares read the dict built by `_event_errors` (`compare_m5a_i2s_candidate.py:101`) from `event_diagnostics` fields, not the `error` stores. Note the real per-note producer (`mono_m5a_score.py:428-439`, rounded to 4-5 dp) has no full-precision per-note verdict; the aggregate metrics are decided from unrounded `observed`, and the per-note comparator is a separate decision of its own: ONLY (shift <= 1e-4 of ms/dB/cents) |
| `tolerance` (33) | 3 stores x 11 compares (`abs(p['error']) <= p['tolerance']` in `check_m1a_selection`, `measure_m1a_drive`, `measure_m1a_volume_mapping`, `measure_m5a_filter_oversample`, `sweep_m1a_attack`, `clap_d12a_probe`), 33 | ONLY. `run_case.measure_pair` (`:3821-3824`) stores `error` and `tolerance` rounded to 4 dp and keeps no full-precision pass; the board itself (`scorecard.normalised`) divides the same rounded fields. Every consumer agrees; no reversal exists |
| `explained_ratio` (4) | 2 stores (`attack_fit_v3.py:109`, `measure_mono_m1a_reference.py:266`) x 2 compares (`qualify_m1a_attack.py:69,104`), 4 | ONLY. The full-precision gate (`explained < 0.6`) refuses upstream of the round; the floors (0.7 to 0.99) are applied only to the rounded value, both when the domain table is built (`:69`) and when it is used (`:104`) |
| `max_abs_error_ms` (1) | `qualify_m1a_attack.py:84` -> `mono_m1a_score.py:135` | ONLY, and monotone: the producer keeps rows with `worst <= 1.0` at full precision, and `round(worst, 4) <= 1.0` follows because the bound is on the rounding grid, so the guard cannot flip a pass |
| `d_unwanted_rel_db` (1) | `mono_artifact_probe.py:596` vs `measure_m5_saw_drive.py:158` | NC for the pairing: `:158` reads the delta that module builds at `:153`. That in-module rounded delta (3 dp) against `<= 1.0` is ONLY (M5B saw drive; shift <= 0.0005 dB on a 1 dB limit) |
| `worst_db` (32 of 37) | 4 stores x 8 compares in `cymbal_tone_knob.py` (`:529,806,846,968,981,983,985,1065`) | ONLY. `fit_balance` returns rounded `worst_db` and decides `<= BOUND_DB` from it everywhere; no full-precision verdict exists beside it |
| `margin_db` (34) | 18 pairs: `probes/residual_dc.py:268,1632` x 9 stores; 16 pairs: `measure_saw_alias_after_2x.py:560,569,587,596` x 4 cross-module stores | NC for the 18 (`st['margin_db']` is computed unrounded in-module). The 16: a running-max tracker compares an unrounded candidate with the rounded stored best; it chooses which note is reported worst, not a verdict. ONLY |
| `rms` (6), `rms_dbfs` (4) | `lead_drive_337.py:75` (local `summarise`), `reference_compare.py:394`, `measure_mono_m1a_reference.py:438` (unrounded local) | NC |
| `amp` (7), `db` (2), `decay` (1), `f0` (2), `level` (3), `max` (6), `median` (1), `onset_s` (4), `peak` (3), `peak_db` (2), `tol` (2), `worst` (1) | comparisons read arrays, dataclass attributes, constants (`rule['peak_db']`, `TOL['level']`, `np.iinfo().max`), baselines or per-record locals; the `worst` case is the wrong-then-right above | NC (all 34 pairs) |
| `f0_hz` (13) | 13 stores x 1 compare, `measure_conga_body_spread.py:554` `0.9*f0 <= r['f0_hz'] <= 1.1*f0` | The corpus origin was not traced. Even if it holds the rounded values, it is a +-10 % selection window, so a step of at most 5e-4 Hz is not a verdict: ONLY |
| `fit_residual_db` (1), `headroom_db` (3), `inharmonic_db` (1), `margin` (2), `over_leak_1s_db` (1), `residual_db` (1), `worst_cents` (1) | `audio_measure.py:2202` and `estimator_domains.py:411` read `Estimate.detail` (unrounded); `reference_voice.py:394` is a `>` display marker; `noise_fixture_duration.py:419` is a printed line; `cymbal_400_production_tail.py:230` re-checks `over_leak_1s_db` that `cymbal_mid` never tests at 1 s (its own gates are `edt`/`late`); `r0_capture.py:957` and `compare_ladder_candidates.py:575` read their own records | NC or ONLY (shift <= 0.005 dB / 0.05 cents against 6 dB / 5 cents limits) |

Group sizes reconcile to the table at the top: 322 + 45 + 33 + 4 + 1 + 1 +
32 + 34 + 10 + 34 + 13 + 10 = 539 = 544 - 5 (the defect group).

## Outside the census population (not counted above)

The census cannot see a field compared in the module that stored it, or one
passing through a local (its docstring lists both). Three sites met while
reading are that shape with **no** full-precision verdict, hence not defects:
`tools/r0_capture.py:956-957` (`residual_db` rounded to 2 dp, then compared),
`tools/measure_m5_saw_drive.py:153-158` (above), `tools/cymbal_tone_knob.py`
(`worst_db` rounded in `fit_balance`). The defect above is in this class's
sibling: the same module holds both the rounded record and the re-check, but a
full-precision key beside it. That is the case a future reading should look for
first, and the census does not find it (`cymbal_tone_nodal.py` stores and
compares in one file, so the `worst_db` pair that names it is accidental).

## Reproduce

    python3 -I tools/rounded_verdict_census.py .                 # at 8e8551b3
    git archive --format=tar 65d5c023 tools model | tar -x -C /tmp/pre
    python3 -I tools/rounded_verdict_census.py /tmp/pre | grep -A2 '^best_correlation'

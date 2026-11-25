# #1317: distance-independent take loss reaches CALIBRATED

**The complete 772-take record is refused.** Marking 226 takes as unreadable
without using their measured distances produced a calibration in 66 of 5000 draws.

| Record | Eligible | Coverage at k=3 | Automatic selection |
| --- | ---: | ---: | --- |
| All 772 takes | 9 | 3/9 | `REFUSED`, no `k` |
| 226 selected development takes in `missing` (issue control) | 9 | 9/9 | `CALIBRATED`, `k=3` |
| 226 uniformly random takes in `missing`, 5000 seeded trials | 9 | see below | **66 of 5000 `CALIBRATED`** |

Random-draw outcomes (all 5000 trials counted, 0 errors): `COVERAGE_REFUSED`
4934, `CALIBRATED` 66, `EVIDENCE_REFUSED` 0. Observed rate 1.32%
(Clopper-Pearson 95% interval 1.02% to 1.68%). Every hit selected `k=5`
(coverage at k=3 in the hits: 3/9 to 6/9). Nothing here says how often real
cache failures remove 226 takes, nor what the shifted tolerance sounds like.

## Model and apparatus

- The original 5000-trial run used the probe at `d84a5dd2` and default
  `calibrate()` with the #1305 indexed evidence guard. Input SHA-256 digests
  are `29f648c93be36cbd2be70f86d767de799e459b22283cdb7c9e87ddf11c6492ba`
  for `tables.json` and
  `d148c98a2d5333ba3ce198c3fc4fd4a23b52147aa51e7d639abfc6be508dfbf6`
  for `refaudio/index/808-from-mars.tsv`.
- Each trial `i` (seed `i`, 0 to 4999, trial count fixed in the probe before
  any outcome was inspected) draws exactly 226 of the 772 sorted paths with
  `random.Random(i).sample`, regardless of sound, group or distance, and moves
  them `rows` to `missing`. This models present-but-unreadable takes.
  Absent-from-listing files are a different failure and are not modelled.
- The probe runs both controls first. For every draw it checks each sound's
  retained and missing paths against the index and the requested removals.
  Losing the last readable development or calibration take for an eligible
  sound counts as an evidence refusal. An index error or an invalid partition
  aborts the run before it reports a frequency bound.
- Raw output: `missing-random-1317.json` (counts, every hit's seed, `k`,
  coverage and path-set hash; seed 127's removed path set in full).

```
python -I tools/probes/gate_calibrate_missing_random.py --out docs/scorecard/gate-379/mars-calibration/missing-random-1317.json
python -I tools/probes/gate_calibrate_missing_random.py --replay 127   # CALIBRATED k=5 5/9 at k=3
```

## Disposition

The 66 crossings refute the forgery-only explanation. The 29.3% loss fraction
(226/772) was fixed by the sampling model. Review found a counterexample to
the earlier claim that this much loss was necessary. Restoring 208 of seed
127's missing paths leaves **18 of 772** missing, and default `calibrate()`
still selects `k=5` at 8/9. For each eligible sound, scan development and
calibration takes in order of increasing `spec` distance. Keep only the
published removals encountered before reaching five retained development takes
and one retained calibration take. The focused test replays this selection.

The random-draw frequency at 18 removals, the minimum loss for a crossing,
and the frequency of crossings in real caches remain unmeasured. A repair must
preserve legitimate unreadable takes. A follow-up could re-measure the missing
takes on the build box or evaluate a missing-fraction threshold through a
registered sensitivity sweep.

**Corrections from review.** One minimum-loss claim was withdrawn after the
18-path replay. Review also showed that an index read failure could produce a
spurious frequency bound. The original 5000-trial record contains zero
evidence refusals, the category that accepted that injected failure.

# #1317: distance-independent take loss reaches CALIBRATED

**Result: positive.** Take loss unrelated to the measured distances can make
the complete 772-take record calibrate. This is not only an adversarial route.

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

- Inputs: `tables.json` sha256 `29f648c9...c6492ba`; index
  `refaudio/index/808-from-mars.tsv` sha256 `d148c98a...bfbf6`; code at the
  `origin/main` revision `e265de6c` plus this PR (default `calibrate()`
  with the #1305 indexed evidence guard, unmodified).
- Each trial `i` (seed `i`, 0 to 4999, trial count fixed in the probe before
  any outcome was inspected) draws exactly 226 of the 772 sorted paths with
  `random.Random(i).sample`, regardless of sound, group or distance, and moves
  them `rows` to `missing`. This models present-but-unreadable takes.
  Absent-from-listing files are a different failure and are not modelled.
- Controls run first in the same invocation and abort it if wrong.
- Raw output: `missing-random-1317.json` (counts, every hit's seed, `k`,
  coverage and path-set hash; seed 127's removed path set in full).

```
python -I tools/probes/gate_calibrate_missing_random.py --out docs/scorecard/gate-379/mars-calibration/missing-random-1317.json
python -I tools/probes/gate_calibrate_missing_random.py --replay 127   # CALIBRATED k=5 5/9 at k=3
```

## Disposition

The forgery-only framing is **refuted**: the issue's own test ("if random
degradation cannot cross the threshold ... the priority drops") fails. The
route needs a loss of about 29% of takes, which a healthy cache does not
produce, but a degraded one could, and `evidence_refusal` does not see it.
Fix shape for a follow-up (not made here, per scope): refuse on the
proportion or per-sound concentration of `missing` entries, with the dial
registered in `tools/sensitivity.py` when proposed, or re-measure the
`missing` takes on the build box. Legitimate unreadable takes (today: none)
must still be allowed. Wrong-then-right in this work: none observed; the
issue's uniform-construction bound from #1305 review was the earlier wrong one.

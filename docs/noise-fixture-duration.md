# Does a longer `noise` fixture widen the slope control's margin? (#528)

**Result: no, not in a way that holds on untouched seeds, so `NOISE_SECONDS`
stays 2.0.** The experiment found a larger problem than the one #528 filed. The
original `SINGLE_WINDOW_SLOPE` control is not "1.05x thin". It is caught in
about 60 % of independent 12-trial seed groups. It is green in CI because CI's
seeds are fixed. That is #539.

This is measurement tooling. No sound, RTL or image change is claimed.

## What was run

| | |
|---|---|
| instrument | `tools/probes/noise_fixture_duration.py` (guards: `tools/probes/test_noise_fixture_duration.py`) |
| command | `python tools/probes/noise_fixture_duration.py --json docs/noise-fixture-duration-results.json` |
| source commit | `9dd22ff941cfe06f275384f9c684f27ee35a2ee2`, clean, 2 behind `origin/main` (the two are a DAG regeneration and a Loom resync, neither touches `tools/probes` or `model/`) |
| first run | `8e863b5a`, with identical numbers and a decision rule that was missing one clause. Kept as `docs/noise-fixture-duration-run1-results.json` / `-run1.log` |
| environment | Python 3.12.11, numpy 2.5.3, scipy 1.18.1, macOS arm64, 18 cores, **load average about 32** (shared developer laptop, not the build box; none was configured on this host) |
| wall time | 934 s (run 2), 759 s (run 1) |
| raw output | `docs/noise-fixture-duration.log`, `docs/noise-fixture-duration-results.json` (provenance block included) |

Every draw is seeded, so the numbers are deterministic. Runs 1 and 2 agree on
every figure except wall time.

## What was stated before the run, in the instrument's first commit (`8e863b5a`)

- **Grid:** 2.0 s (shipped) and 4.0 s (#528 option 1). Bounded, as the
  Curator asked.
- **Prediction, from Welch theory rather than from any measurement here.**
  `psd_slope_db_oct` averages 22 Hann segments at 2 s and 45 at 4 s, so the
  slope's realisation spread should scale by sqrt(22/45) = **0.699x**. That is
  not the "halve" #528 hoped for, which was a variance reduction read as a
  spread reduction.
- **Derived invariance, asserted as a precondition:** the mutant reads only
  `x[:4096]` of a prefix-stable draw, so its residuals must not move with the
  fixture length. The tool REFUSES if they do.
- **Decision rule (`decide()`), reading selection data only.** The candidate
  must meet all of these:
  - its VALIDATE_BASE margin beats the baseline's;
  - the clean rows pass;
  - the selection groups show zero false alarms and zero refusals;
  - the improvement is material, meaning either a detection-rate gain whose
    95 % Clopper-Pearson intervals do not overlap, or survival of every
    independent recalibration where the baseline does not survive.
- **Seed populations, all disjoint and asserted so at import:**
  - CALIBRATE_BASE: thresholds.
  - VALIDATE_BASE: the established control run.
  - SELECT_BASE: 40 groups of 12 trials, plus 8 recalibration populations of
    96 draws.
  - CONFIRM_BASE: 10 groups of 12 trials over all 18 rows, never read by the
    decision.

## Results, per candidate

| | 2.0 s (baseline) | 4.0 s |
|---|---|---|
| `noise/psd_slope` calibration, worst of 96 | 0.1481 | 0.1380 (0.932x) |
| `noise/psd_slope` calibration, std | 0.03771 | 0.02666 (**0.707x**; predicted 0.699x) |
| rounded threshold (4 x worst) | 0.60 | 0.56 |
| original `SINGLE_WINDOW_SLOPE` residual, VALIDATE_BASE worst of 12 | 0.6288 | 0.6288 (invariant: max \|delta\| 6.1e-16) |
| **original control margin**, VALIDATE_BASE | **1.05x** CAUGHT | **1.12x** CAUGHT |
| `noise/centroid` threshold / `SHORT_WINDOW_SPECTRUM` margin | 0.059 / 1.87x | 0.043 / 2.57x |
| thresholds from 8 independent recalibrations | 0.60-0.99 | 0.41-0.56 |
| control survives recalibration | **1/8** | **8/8** |
| control on 40 selection groups | CAUGHT 23, MISSED 17 | CAUGHT 26, MISSED 14 |
| clean noise rows on selection groups, false alarms / REFUSED | 0 / 0 | 0 / 0 |
| ms per trial, psd_slope / centroid / waveform (run 2; run 1) | 80 / 55 / 23 (91 / 63 / 34) | 227 / 180 / 43 (136 / 103 / 57) |

Runtimes were taken at load average 32 on a shared machine and moved by up to
1.7x between two identical runs. Read them as "4 s costs roughly 1.5-3x per
noise-row trial", not as a measurement.

**Decision on selection data: SHIP 4 s.** Condition 4 held through
recalibration survival (8/8 against 1/8). The detection-rate gain (26/40
against 23/40) is not resolvable.

## Confirmation on CONFIRM_BASE (10 groups x 12 trials, never read by the decision)

| | 2.0 s | 4.0 s |
|---|---|---|
| all 18 clean rows | PASS 10/10 each; 0 FAIL, 0 REFUSED | PASS 10/10 each; 0 FAIL, 0 REFUSED |
| `SINGLE_WINDOW_SLOPE` / `noise/psd_slope` | CAUGHT 7, **MISSED 3** | CAUGHT 8, **MISSED 2** |
| `SHORT_WINDOW_SPECTRUM` / `noise/centroid` | CAUGHT 8, **MISSED 2** | CAUGHT 10 |
| `TWO_POINT_TAIL_DECAY`, both `PHASE_SENSITIVE_SPECTRUM` pairs | CAUGHT 10 each | CAUGHT 10 each |

**Final: 2.0 s ships (no change).** 4 s was selected, but its own reserved
confirmation missed the original control in 2 of 10 groups. A selection that
does not survive its confirmation does not ship. The tool exits 1 because the
configuration left shipped is itself contradicted on the confirmation
population: 3/10 and 2/10 groups missed. That is the finding filed as #539, not
a defect of this experiment.

### Why the literal criterion is not the decision

#528's acceptance criterion read literally is "margin beats baseline", and
1.12x > 1.05x satisfies it. That comparison is printed beside the decision so a
reader can disagree with it. It does not decide, for two reasons. First, both
margins are worst-of-12 statistics on a single population. Second, the
population view shows the control's detection is the problem, not its point
margin. Shipping 4 s would have doubled the noise rows' runtime and
invalidated the docstring's 2.0 s false-alarm and SAFETY-sweep measurements,
which would then need build-box re-runs. In return it would have bought a
control that still misses on one untouched seed group in five.

## The upper bound on SAFETY, located rather than bracketed

`permitted_differences.py --margins` (seconds), with the original mutant
restored:

```
SINGLE_WINDOW_SLOPE       noise/psd_slope        0.6    0.6288  1.05x CAUGHT        lost above 4.19x
SHORT_WINDOW_SPECTRUM     noise/centroid       0.059    0.1105  1.87x CAUGHT        lost above 7.59x
TWO_POINT_TAIL_DECAY      noise/decay_tau     0.0042     5.447  1.3e+03x CAUGHT     lost above 5.27e+03x
PHASE_SENSITIVE_SPECTRUM  phase/centroid     7.9e-05     1.238  1.57e+04x CAUGHT    lost above 6.15e+04x
PHASE_SENSITIVE_SPECTRUM  phase/band_ratio_db 0.0017     18.85  1.11e+04x CAUGHT    lost above 4.37e+04x
```

The measured upper bound is **4.19x**, so the shipped 4 is 4.6 % under it.
This agrees with the committed `--safety-sweep 20` table: 3/5 caught at 8x,
because 4.19 and 7.59 are both under 8, and 3/5 at 64x and 512x, because the
other three cliffs are over 5,000x. The lower bound (3/360 false alarms at 1x)
is unchanged by construction. With the original mutant restored and the
fixture unchanged, the clean table is byte-identical to `origin/main`'s.
`test_the_upper_cliff_is_where_the_pair_is_lost` re-runs the binding row
either side of the cliff. As #539 says, the cliff is a property of the
VALIDATE_BASE draw.

## What was not run

- **`--safety-sweep 20`**: about 13 minutes on the box, much longer at load 32
  here. Its new per-pair print path was exercised by a 1-run, 3-factor smoke
  run only (in the PR comment), which is not the measurement.
- **`make verify` and `make controls`**: these belong to the build box, and
  none was configured on this host (no `.env`, no `~/.config/repo`).
- **`--false-alarm-rate 50`**: not re-run. The clean table is unchanged.

## Wrong, then right

Six items. Two came from this session's own tooling, and every one was caught
by a gate, a control or a population rather than by reading.

1. **The first fix for #528 changed the defect, not the detection.** PR #537's
   first head replaced the mutant with a 2048-sample, nfft=512 excerpt and
   reported 2.17x. It was reverted on review. Three tests now pin the original
   mutant and the measured cliff, and all three were observed red against that
   head.
2. **"Doubling the length does not halve the spread because the low bins
   dominate."** That was the first Builder's prose explanation for 0.148 to
   0.138. The spread did scale as theory predicts (std 0.707x against 0.699x).
   0.932x is the ratio of two worst-of-96 statistics, which are much noisier
   than the spread they bound.
3. **"Halve the spread" (#528 itself).** That expectation was a 1/N variance
   reduction read as a 1/sqrt(N) spread reduction. Theory says 0.70x for
   2 s to 4 s.
4. **"1.05x: thin but detected" (the probe's docstring, `origin/main`).** True
   of the one fixed draw. On independent populations the control is caught in
   23/40 and 7/10 groups, and 7 of 8 recalibrations put the threshold above
   the defect. The docstring's own sentence ("a recalibration that pushed the
   slope threshold past its defect turns `make controls` red") describes the
   likely case, not an edge case.
5. **This session's first `pair_margin`** read every non-PASS verdict as
   caught, so REFUSED counted as a catch. 7 of its 8 adversarial cases were
   observed red against it before the real implementation was written.
6. **This session's first decision rule** said what to select and what to
   confirm, but not what a failed confirmation meant. Run 1 selected 4 s and
   its confirmation contradicted it. The rule was completed in `9dd22ff9`,
   labelled in the code as added after the run, and run 2 reproduces run 1's
   numbers under it.

There is also a related docstring claim that was not re-measured: `draw_noise`'s
statement that 1.0 s to 2.0 s "halves" the spread (0.34 to 0.148). That is the
same worst-of-96 reading, and theory predicts sqrt(10/22) = 0.67x. It was left
as written and is flagged here.

## Same-shape search: other thin calibrated-control margins

Commands, run against `origin/main`:

```
git grep -n -i -E "SAFETY *=|x the worst|worst of [0-9]+ (calibration )?draws|calibrated=|margin of [0-9.]+x|[0-9.]+x (margin|above (its|the) threshold)" origin/main -- 'tools/*.py' 'model/*.py' 'rtl-sketch/*.py' 'tools/probes/*.py'
git grep -n -i -E "(margin|headroom|clears?|above).{0,40}\b1\.[0-4][0-9]?x\b|\b1\.[0-4][0-9]?x\b.{0,40}(margin|threshold|tolerance|bound)" origin/main -- '*.py'
```

- **In this probe**: `SHORT_WINDOW_SPECTRUM` on `noise/centroid` shows 1.87x on
  VALIDATE_BASE and is MISSED in 2/10 confirmation groups. It is the same
  defect, measured, and covered by #539.
- `tools/run_case.py` around line 270: the BD f0 tolerance is 1.54x the worst
  session pair. This is a floor (false-alarm side), not an injected control,
  but it is the same worst-of-small-N shape. **Not examined**; listed in #539.
- `model/promoted_measures.py` around line 140: the edge-leak 0.05 threshold
  is 25x the worst passing case and 0.25x the worst failing one. Not thin.
- `tools/probes/estimator_ground_truth.py` (#517 controls): tolerances are
  against analytic truth; the seeded-average check is around line 922. **Not
  examined** for margin; listed in #539.

No other `SAFETY x worst-draw` calibration exists in the tree.

## Why `NOISE_SECONDS` is not in `docs/sensitivity/registry.json`

The Champion asked for the dial to be registered before it was selected. It
was not selected, and the registry cannot hold it as things stand.
`tools/sensitivity.py` knows only the `verilog-parameter` kind and only the
fixed-width-table evidence format. Its own documentation says that
registering a sound or Python dial would need a second format. The same
reasoning the probe already gives for not registering `SAFETY` applies: the
measurement is re-runnable in minutes, and its guards run on every pytest
pass. The instrument does carry the registry's discipline. The grid,
prediction and decision rule were committed before the run (`8e863b5a`), and
the one later addition is labelled. If a later change proposes a different
fixture length, teaching `sensitivity.py` a `python-constant` kind is the
point at which to register it.

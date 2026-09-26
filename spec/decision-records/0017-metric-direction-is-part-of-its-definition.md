# 0017: A metric's direction is part of its definition — the cowbell's difference tone is a defect ceiling

- **Status**: proposed
- **Date**: 2026-09-25
- **Decided by**: sound/measurement owner, for #141, under plan075 §6

## Context

`D13A` (cowbell) failed at **11.34×** on *"unwanted difference tone"*, the
worst number on the board. The machine holds that component at **−67.97 dB**
(relative to the upper partial). Ours measures **−102.00 dB**. We have **34 dB
less** of it. The scorecard scored every metric as `|error| / tolerance`, so a
3 dB symmetric tolerance counted the deficit as a mismatch (34.03 / 3 = 11.34).
A metric cannot mean "less is better" and also penalise less.

The metric exists because of DR 0010's defect. One swing gate on the **sum**
of the two squares, `nl(a + b)`, put a difference tone **42 dB above** the
machine's. The model test that guards the fix is already one-sided.
`test_cowbell_has_no_difference_tone` asserts `< −65 dB`, which is the
machine's line plus 3 dB, and does not require it to be present. The board
contradicted it.

## Decision

1. **Each metric declares a purpose** (plan075 §6), and the purpose is written
   into every metric in every record:
   - `match`: two-sided, `|error| / tolerance`. A deficit and an excess are
     both distance.
   - `defect ceiling`: one-sided, `max(0, error) / tolerance`. Only **excess**
     over the reference is distance.

   A record without the field predates it and scores as `match`, so every
   historical number still reproduces. `scorecard.evaluate` returns **no
   verdict** for an unknown purpose and never guesses one.
2. **"unwanted difference tone" is a `defect ceiling`**, with the same 3 dB
   tolerance. The ceiling is the machine's level plus 3 dB, which is
   −64.97 dB on the current reference. It is not renamed, and it is not kept
   two-sided.
3. **Partial balance and decay stay `match`.**
4. **The purpose is part of `measurement_policy`.** `compare()` therefore calls
   the old and new D13A records **INCOMPARABLE**, not an improvement. When
   `run_case` replaces a record that was scored under a different policy, it
   appends the old verdict to `rubric_history` with the label *"rubric change
   (measurement-version change), not a sound change"*.

### Why a ceiling, and why not "rename it and keep the deficit"

The deficit-matters reading would need a reason why a **missing**
intermodulation product at −68 dB makes the cowbell sound wrong. Nothing
supports one:

- The component is 68 dB below the 824 Hz partial. It is 18.9 dB above the
  recording's own floor in 230–290 Hz (DR 0010), so it is real. But it lies
  between two partials that are 60+ dB louder. No masking or listening
  measurement here says its absence is audible. Asserting that it is would
  create a target from our own reading, which is failure-modes' "cheap check".
- Its origin in the machine is unknown. It could be supply coupling or
  shared-rail intermodulation. Fitting ours to match it would reproduce a
  parasitic that DR 0010 deliberately removed the mechanism for.
- The machine's own spread at this level is unmeasured. #126's 0.159 dB is
  about band splits, not a −68 dB line. A 3 dB two-sided window has no basis
  there.

A ceiling needs none of that. It needs the metric to be reliable where it
**fails**, near and above −65 dB. That is 20+ dB above the recording floor,
and `difference_tone_db` refuses any reading within 6 dB of its own leakage
floor. How precisely we read a deficit no longer matters.

## Evidence

- **Start red.** Before any implementation, all 10 tests then in
  `tools/test_metric_purpose.py` failed (pytest exit 1). The two
  history-mechanism tests added afterwards were run red separately against
  `origin/main`'s tools. Both are recorded in `docs/scorecard/cowbell-141/`.
- **The metric still sees its defect.** The injected control `CB_GATE_THE_SUM`
  puts DR 0010's `nl(a + b)` back: one path takes `SRC_SQPAIR` and the other
  is switched off. It reads **−20.53 dB** against the machine's −67.97, an
  excess of +47.44 dB, which is **15.81×** as a ceiling. The headline moves
  to the difference tone. In `run_case --expect changed` the control fires
  and exits 0.
  One reading was wrong before it was right. The first control run used
  `--expect fail`, and the runner REFUSED it (exit 2): a fail control needs a
  clean baseline that passes, and clean D13A still fails on the partial
  balance. `--expect changed` is the right qualification. Both runs are kept.
- **Same audio.** `build/scorecard/D13A-ours.wav` has sha256 `31bb9100…`
  (full hash in `audio-sha256.txt`). It is identical before the change (on
  `origin/main` ffc1c00) and after it. The reference `cb8/CB.WAV` has sha256
  `1468cbd6…` on both sides.
- **D13A property vector.**

  | property | before (match) | after (declared purpose) |
  |---|---:|---:|
  | Partial balance | 2.824 | 2.824 |
  | unwanted difference tone | **11.344** | **0.000** |
  | decay | 0.233 | 0.233 |

  Headline: **11.34 "worst: unwanted difference tone"** becomes **2.82
  "worst: Partial balance"**. The case is still `fail`. The board's valid,
  pass and fail counts do not change.

## Alternatives considered

- **Widen the tolerance.** Rejected. It would retune a tolerance after seeing
  the error, and a large enough window would also pass DR 0010's defect.
- **Drop the metric.** Rejected. `scorecard.py` treats a missing required
  measurement as invalidating the case for good reason. The metric is the
  only thing on the board that would catch `nl(a + b)` coming back.
- **Rename it and keep it two-sided.** Rejected for the reasons above.

## Consequences

- The board's worst number is no longer a rubric artefact. The cowbell's
  remaining defect is the partial balance at −8.47 dB, whose mechanism is
  known (#107). This record does not fix it.
- Every metric written from now on carries `purpose`. Other metrics that name
  an unwanted component, such as alias energy, can declare `defect ceiling`
  in `run_case.METRIC_PURPOSE`. They must first get a control showing the
  ceiling still fails their defect.
- `behavior/range` and `integrity` (plan075 §6) are not implemented. A record
  that declares either is refused until someone implements one against a
  case that needs it.
- Other scripts that compute `|error| <= tolerance` themselves, such as the
  M1A/M5A tools, do not read `purpose`. None of them scores this metric.

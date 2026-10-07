# 0023: A Pekonen-style post-oscillator one-pole is measured, and not taken

- **Status**: proposed
- **Date**: 2026-10-07
- **Decided by**: block agent, from `tools/pekonen_coloration_probe.py` run
  against `docs/reference-voice-results.json`
- **Issue**: #243 (split from #48 finding 4; sibling of DR 0017)

## Context

Pekonen et al. (2011) give an analogue oscillator its measured coloration with
an antialiased saw followed by a first-order IIR equaliser. **Their parameters
were fitted to a Minimoog Voyager. Only the method is reusable; no Voyager
number appears in this record or the tool, and none is Model D ground truth.**
Provenance if a stage were ever adopted: *Voyager, method reused*; the
parameter itself would be *fitted to Mini V3, whole-path*, never "Model D".

The reference actually available is the committed Mini V3 saw rows. Mini V3's
filter cannot be bypassed, so the recording was made with cutoff at maximum and
its residual response is inside every number (`model/reference_voice.py`). That
is a systematic and is stated, not corrected. Any coloration fitted to it fits
the whole path.

## Decision

**Measured, and we are not taking it.** No stage is added; the oscillator and
RTL are unchanged (nothing to keep bit-exact; `verify_voice.py` not run because
no RTL or model path changed).

The rule was fixed in the tool before the data was read: adopt only if
(A) leave-one-pitch-out RMS falls >= 25 %, (B) the no-stage error exceeds 1 dB
(a stated judgement, about a single partial's level JND), (C) the fitted pole is
stable across folds (spread <= 0.1), and (D) a residual shuffled across pitch
does not also pass A.

Result (saw, notes 33..81, harmonics 2..12, Mini V3 minus ours):

- Our saw is already within **0.5 dB RMS** of Mini V3 (below the 1 dB bar,
  failing B). At 55-440 Hz every harmonic is within +0.5 / -0.0 dB.
- The one-pole that best fits all rows is p = +0.16, RMS 0.52 -> 0.37 dB.
  That is **in-sample calibration on our own comparison, not validation.**
- Out of sample (leave one pitch out) it **makes things worse**: 0.52 -> 0.78 dB
  (-52 %), pole unstable across folds (+0.16 ... -0.23). The only large
  residual is 880 Hz at h9-h12 (-0.9 to -2.6 dB, i.e. 7.9-10.6 kHz); a falling
  high end at the top of the range is what Mini V3's unbypassed filter and our
  own 2x decimator both predict, so it cannot be attributed to the oscillator.
- Mini V3 at 1760 Hz is excluded (not a saw, h6 off by 4.1 dB; re-derived from
  the rows, matching the existing exclusion).

Controls (all run on every invocation, exit 1 if any fails): a known injected
pole is recovered and adopted; an identical reference, a harmonic-number-only
tilt, and iid noise are not adopted; a stale `ours` row is REFUSED; the Q15
quantisation of p stays < 0.05 dB; the integer recurrence has unity DC gain.

Wrong-then-right rate: the first run of the null control divided by zero on an
identical-reference input (caught by the controls, fixed); no figure above was
revised after being read.

## Alternatives considered

- **Adopt p = +0.16** — a 0.15 dB in-sample gain that does not survive a held-out
  pitch; shipping it would be the borrowed/self-calibrated number this issue
  exists to avoid.
- **Fit to a Model D / Voyager recording** — none is available here (Surge XT
  and Model D plugins are not on this host either; Mini V3 rows are the
  committed record, not re-measured). Would be the proper evidence; this record
  is inconclusive about it, not negative.
- **Spec the Q15 stage anyway** — `y += ((x - y) * (32768 - p)) >> 15` is
  sketched in the tool (`stage_int`) for cost-bounding only. No RTL.

## Consequences

- #243 closes as measured and not taken; reopen if a bypassed-filter or Model D
  oscillator recording appears (`docs/moog-recording-protocol.md`), at which
  point the tool is re-run unchanged.
- Limits: 5 pitches, 11 harmonics, one reference whose filter is in the path,
  saw only; the square and shark-tooth were not tested. The reference rows were
  not re-measured here (plugins unavailable on this host); the tool refuses if
  the recorded `ours` rows disagree with the model as rendered now.
- No tunable parameter ships, so nothing is registered in `tools/sensitivity.py`.

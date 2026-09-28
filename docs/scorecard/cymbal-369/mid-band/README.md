# #369 step 6: the 1–2.5 kHz decay, qualified — and §6's indication does not survive it

Step 5 (`../candidate3/README.md` §6) found that the cymbal's remaining 1–2.5 kHz error is
**time-dependent**, and said so with an explicit caveat: the quantity behind it is an energy ratio
between two windows of the frozen `thirds()` instrument, **not a qualified decay measurement**, and
none of the three qualified bands (L, Ln, H) covers 1–2.5 kHz. It filed the missing measurement as
**#400** and named it the next step. This is that measurement, and the question it was built to
answer, asked in `tools/cymbal_mid.py`'s docstring **before any 808 file was read**:

> If §6's indication is real, the 808's mid band should decay measurably longer than ours — by more
> than the repo's own ±50 % time tolerance, which is the only way the difference counts as a
> difference.

**It does not.** At CY5025 the 808's qualified mid-band EDT10 is **632 ms** and the shipped kit's is
**528 ms**: ours is **16 % faster**, well inside ±50 %. Candidate 3's is 451 ms, 29 % faster — also
inside. **So the 1–2.5 kHz defect is not a decay-rate difference.** What the same instrument does
qualify is a **level** difference, and it points at a part of the circuit already named.

Nothing here changes the kit, the RTL or R1. No candidate is selected, promoted or rendered new.

## 1. The instrument (`tools/cymbal_mid.py`, 36 tests in `tools/test_cymbal_mid.py`)

A **separate module**, not a new entry in `cymbal_bands.BANDS`: plan098 forbids changing the frozen
instrument mid-selection, so every L/Ln/H number and every committed scorecard JSON in this
directory is unaffected by this file. It reuses `cymbal_bands`'s already-qualified machinery (the
zero-phase band-pass from `prepare()`'s guaranteed lead, the floor-subtracted Schroeder curve,
EDT10 and late T20 with their refusals) and adds a band, two preconditions, and a second estimator.

| band | range | status |
|---|---|---|
| **M** | 891–1782 Hz (the 1.0 / 1.26 / 1.59 kHz thirds) | **qualified.** The low band's 3.45 kHz Q 6 band-pass is 18.7 dB down at 1782 Hz and 26.7 dB at 891 Hz — what Ln is to the high bands |
| M25 | 891–2818 Hz (adds the 2.0 and 2.5 kHz thirds §6 quotes) | **reported, not separable.** The same skirt is only 8.4 dB down at 2818 Hz, and for exactly that reason the leakage precondition below refuses M25's EDT10 on **11 of the 25** Fischer records and its late T20 on **24 of 25** — including, in both cases, CY5025, the setting this step's whole comparison rests on |

**Preconditions, asserted per record, that REFUSE rather than answer.**

1. **Floor.** The 50 ms envelope where the Schroeder curve crosses −10 dB — the point EDT10 is read
   at — must be ≥ 10 dB above the record's own floor. `cymbal_bands.band_decay` guards the −30 dB
   point this way (15 dB) but computes nothing for the −10 dB point, so it will report an EDT10 for
   a band that is pure noise floor, and 1–2 kHz is where a 1994 16-bit transfer's floor and rumble
   live. The two guards cover different quantities and neither subsumes the other: at a −40 dB
   planted floor the frozen guard refuses the late T20 while EDT10 still has 34 dB of margin, which
   is correct, and a test asserts exactly that.
2. **Leakage, per window.** The band's energy must exceed the energy the low band leaks into it by
   ≥ 6 dB, checked **separately over each window a quantity is read from** ([0, −10 dB) for EDT10,
   [−10 dB, −30 dB) for the late T20). 6 dB rather than the 3 dB energy-ratio convention because
   the neighbour must be not merely smaller but subordinate.

**A second estimator, on different arithmetic** (`two_window_t20`): two adjacent windows of
floor-subtracted power, each as long as the envelope's next 6 dB of fall, anchored at the −10 dB
point. For an exponential, E1/E2 = exp(2W/τ) exactly, so τ = 2W / ln(E1/E2) — no integration, no
line fit, no residual bound. **The late T20 is only QUALIFIED if the two agree inside 25 %**, and
that gate is load-bearing rather than decorative (§2, wrong-then-right 1).

**Known answers, all planted and none of them produced by any drum model of ours.** 12 planted
exponentials (4 time constants × 3 seeds) against the *analytic* answers EDT10 = 1.1513 τ and
T20 = τ ln 10: EDT10 and the Schroeder T20 within 10 % and 6 %, the two-window T20 within 15 %.
A noiseless decaying tone, where both are within 3 %. Planted two-slope mixes, a planted level
change, invariance to scale and to prepended silence, a truncated record.

**Controls, each stated with its failing opposite** (#376 is the precedent — the cymbal's first
crosstalk control could not fail and passed vacuously for weeks):

| control | negative (must fail/refuse) | positive (must answer) |
|---|---|---|
| leakage | M's own content removed, only the Q6 low band's skirt left: **refuses**, 0.7 dB *below* the prediction | the same record with M content: answers, reads the planted 691 ms to 8 % |
| leakage, 2nd | at a_m = 1.0 the planted M is 4.0 dB clear: **still refuses** | at a_m = 4.0 it is 15 dB clear: answers |
| leakage is real | with the guard bypassed, M's EDT10 tracks the **low** band's planted decay over 2× | — |
| floor | a wide-band floor 12 dB below peak: **refuses on the floor** | the same at −90 dB: answers, planted value to 8 % |
| resolution | a window pair across which the power falls 1.5 dB: **refuses**, ln ratio 0.35 < 0.8 | the default 6 dB span: answers |

## 2. Wrong-then-right — two, both pinned as tests

**1. The leak check over the strike's first second passed a tail it had no business passing.** A
planted 345 ms mid-band tail, with the low band's 350 ms decay present, cleared a single
first-second leak margin with 13.6 dB to spare — and the Schroeder estimator then read its late T20
as **497 ms, +44 %**: the slower low band's decay under M's name. Two fixes, both kept: the leak
check is evaluated **per window** (that tail's late window is 7.9 dB clear, still passing), and the
cross-estimator gate (the two-window estimator reads 344 ms, a 31 % disagreement) refuses to
qualify it. `test_control_a_tail_that_passes_the_leak_margin_can_still_be_biased` asserts the
number exists, is wrong, and is not quotable.

**2. The band-pass-only leak prediction refused all 25 recordings, and that was the prediction's
fault, not the machine's.** With the low band modelled as the bare 3.45 kHz Q 6 band-pass — chosen
as the deliberate worst case — every Fischer CY file sits only **1.9–5.5 dB** above the predicted
skirt over the EDT window (1.95 dB at CY1000, 5.46 dB at CY0050; six of the 25 are below 4 dB) and
every one REFUSES against the 6 dB margin. The machine's low path is the band-pass **then Hh1**
(2.5 kHz Q 0.97, reference §10's "low" row), and Hh1's further **8.63 dB** of rejection over
0.9–1.8 kHz belongs in the prediction — with it every record clears the margin, the tightest of the
25 sitting **10.6 dB** above the prediction against the 6 dB required. So `low_has_hh1` became a
**required** argument of `measure_mid` with no default,
because it decides whether the 808's own mid band is measurable at all; both predictions are
reported for every record, and **the EDT10/T20 values themselves do not depend on it** — only
whether they are qualified.

Wrong-then-right rate for this step: **2 results that were wrong before they were right, both
caught by a control or a paired estimator rather than by inspection.**

**And 4 numbers in this document and in `cymbal_mid`'s docstrings that were wrong before they were
right, none of them caught by anything mechanical — they were caught by a reviewer recomputing them
from `mid-band.json`.** The measurements were correct throughout; the prose describing them was not
(§2's leak range stated as 4.1–5.5 dB where the artefact says 1.9–5.5; the Hh1 delta stated as
"~10 dB" in three docstrings where the integral gives 8.63; M25 said to refuse "on most records"
where it refuses on 11 of 25; §3's TONE spread bounded at ≤ 7 % where it reaches 7.8 %). The one
number that *was* pinned by a test — `skirt_db` at the band edges — did not drift. That is the
lesson, and §6 lists the four tests that now pin the rest.

## 3. The external check: the recordings' own knob

Not a fit — a consistency check against physics known independently of any model of ours. **All 25
CY recordings' M EDT10 are qualified** (25/25 measured, none refused), and M EDT10 is **strictly
monotone in DECAY in every TONE column** while being nearly independent of TONE:

| TONE \ DECAY | 0 | 2.5 | 5.0 | 7.5 | 10 |
|---|---:|---:|---:|---:|---:|
| 0 | 420 | 586 | 888 | 1216 | 1291 |
| 2.5 | 416 | 595 | 916 | 1158 | 1302 |
| 5.0 | 402 | 632 | 929 | 1143 | 1269 |
| 7.5 | 391 | 609 | 899 | 1131 | 1271 |
| 10 | 399 | 632 | 924 | 1134 | 1278 |

(ms. The spread across TONE at fixed DECAY is 29–85 ms — at worst **7.8 %** of its column, 45.7 ms
on 586.0 ms at DECAY 2.5 — against a 3.3× range over DECAY.)

This agrees with what `../README.md` §1 already found for Ln: **DECAY moves the low band's envelope
too**, which contradicts §10's "DECAY changes only the middle band's RC". The mid band, whose
content is the low band's own low-frequency skirt plus whatever else lives there, tracks it the
same way. The late T20 is qualified on only **6 of 25** records (elsewhere the frozen estimator
refuses it as not one exponential, or the two estimators disagree), so **EDT10 is the mid band's
qualified decay measure**, exactly as it is for Ln.

## 4. The answer at CY5025

Measured on the three records §6's table is about. CY5025 is the D14A anchor and is in the frozen
development set; the renders have no TONE/DECAY control, so a knob-tracking claim is still blocked
by #371 and is not made here.

| | low path declared | M energy share (dB re total, first 1 s) | **M EDT10 (qualified)** | M late T20 |
|---|---|---:|---:|---:|
| **808 CY5025** | band-pass + Hh1 (the machine) | **−26.77** | **632 ms** | refused — not one exponential |
| shipped kit | band-pass only (Hh1 omitted) | **−22.83** (+3.94) | **528 ms** (−16 %) | 1249 ms |
| candidate 3 | band-pass + Hh1 (revision 3 restores it) | **−35.26** (−8.49) | **451 ms** (−29 %) | refused — estimators disagree 26 % |

**The decay answer is negative, and it is the useful half.** 16 % and 29 % are both inside the
repo's ±50 % time tolerance for a decay (`tools/run_case.TOLERANCE_POLICY["time"]`, sourced from the
808's own ±50 % component spread). By this instrument's standard the mid band's decay rate is **not**
where the 1–2.5 kHz error lives — in the shipped kit or in candidate 3. §6's energy-ratio indication
was real as a measurement of *something*, and §6 said so carefully; what it is not is a decay-rate
difference. Its "5–11 dB further fall" is the joint effect of a level difference and a modestly
faster decay, and the qualified split of those two is what this step adds.

**The level answer is positive, and it names a part of the circuit.** Measured against the *same*
yardstick for all three (the band-pass-only prediction, so the comparison does not depend on any
declaration), the independent 1–1.8 kHz content each record has, relative to its own low band, is:

| | over the band-pass-only skirt prediction, EDT window |
|---|---:|
| 808 CY5025 | **+4.41 dB** |
| shipped kit | **+10.52 dB** (6.1 dB more than the 808) |
| candidate 3 | **+0.04 dB** (4.4 dB less than the 808) |

So the shipped kit has ~6 dB more independent mid-band content than the machine, and **candidate 3
overcorrects to ~4 dB less** — which is the same sign and order as step 5's unexplained tail
deficit (−8.8 to −11.6 dB at 1–2 kHz in the 50–300 ms window) and is now attached to a quantity
with known-answer tests behind it. The omitted Hh1 is the documented difference between the shipped
kit's low path and the machine's, and restoring it is what candidate 3 does; the measurement says
it is restored **too hard**, not that it should not be there.

## 5. What this does and does not support

- **Supports**: the mid band is a separable, measurable band on the 808's own records once the low
  path is modelled as the circuit has it; its decay tracks DECAY monotonically on all 25 records;
  and at CY5025 neither the shipped kit's nor candidate 3's mid-band decay differs from the 808's
  by more than the repo's stated time tolerance.
- **Does not support**: any claim about the *cause* of the level difference beyond "it is where Hh1
  acts and the shipped kit omits Hh1". Hh1 is the documented difference, not a demonstrated one —
  isolating it needs a render with Hh1 alone changed, which is a candidate, not a measurement.
- **Does not support**: anything about 2.0–2.5 kHz. The basis for that is the **band-level**
  declaration in §1 — M25 is reported, not qualified for separation, because the low band's skirt is
  only 8.4 dB down at 2818 Hz — plus the fact that **CY5025 itself refuses**, at 5.92 dB against the
  6 dB margin, and CY5025 is the one setting every comparison in §4 is made at. It is **not** that
  M25 refuses on most records: M25's EDT10 is qualified on **14 of the 25** and refused on 11, so a
  per-record majority claim would be false (its *late T20* is the quantity refused on nearly all of
  them, 24 of 25). Either way the upper half of §6's 1–2.5 kHz span has **no** qualified decay
  measure at the setting the answer rests on. Widening M would not fix that: the skirt is 8.4 dB
  down there and the content genuinely is mostly the low band's.
- **Cannot catch**: two estimators biased the same way by the same cause. EDT10's own second
  estimator was built, measured (−36 %..+10 % scatter on planted exponentials, against the Schroeder
  EDT10's ≤ 9 %) and **demoted to a diagnostic** rather than made a gate, so EDT10 rests on its
  preconditions plus its analytic known answer. For the 808's CY5025 the demoted estimator reads
  564 ms against 632 ms — corroboration, not qualification.
- **Not a knob claim.** #371 still blocks acceptance item 3: the knob-law render is not the
  instrument, so the frozen 16-setting confirmation set cannot be rendered against any candidate.

## 6. Files and reproduction

- `mid-band.json` — every number above, both leak predictions per record, all 25 recordings and the
  three renders, with commit and dirty flag. Re-derived from a clean tree by this session, **twice**:
  once before this branch was rebased onto a `main` that had changed `model/drums_fx.py`, and again
  after. `tools/run_case.py` refuses a stale checkout outright ("these inputs differ from
  origin/main: model/drums_fx.py"), and it is right to: the renders here come from that file. The
  change was the rimshot's drive (#388), the cymbal is untouched by it, and **every field of both
  runs is identical** — which is a checked statement rather than an assumed one.
- `tools/cymbal_mid.py`, `tools/test_cymbal_mid.py` — the instrument and its known answers,
  controls and refusals. `python3 -m pytest tools/test_cymbal_mid.py -q` needs **no reference
  corpus, no render and no simulator** — every signal is planted by the test file, and the only
  external input is `mid-band.json` in this directory — so it is cheap enough for a broad pytest
  job. **No wall-clock figure is quoted here on purpose:** it was first stated as 11 s, and a
  reviewer measured 154 s for the same 33 tests on a different machine and Python. The load-bearing
  property is that it needs no external input, not a duration.
- **The four numbers in this document that drifted are now pinned to the artefact**, in the
  `the committed artefact vs. the prose` section of `tools/test_cymbal_mid.py`:
  `test_the_documented_bp_only_over_leak_range_is_the_artefacts_range` (§2's 1.9–5.5 dB and the
  10.6 dB tightest with-Hh1 over-leak), `test_m25_is_refused_on_a_minority_of_records_not_most_of_them`
  (§1 and §5's 11-of-25) and `test_the_documented_tone_spread_at_fixed_decay_is_the_artefacts_spread`
  (§3's 29–85 ms and 7.8 %); the 8.63 dB Hh1 delta is pinned to ±0.02 dB by
  `test_hh1_changes_the_leak_prediction_by_8_63_db_and_must_be_declared`, which previously allowed
  ±2 dB and so let "~10 dB" stand in three docstrings. These are ordinary tests rather than
  `docs/claim-markers.md` markers because `tools/check_doc_claims.py` reads only `docs/*.md`, and a
  marker nothing evaluates is worse than none.
- Reproduce: `python3 tools/cymbal_mid.py --fischer --renders --out <path>` (≈9 min, most of it
  candidate 3's level calibration; `--no-candidate` drops it to ≈4 min).

## 7. The next question, and it is one question

**Does restoring Hh1 at the level the circuit gives, and nothing else, bring the mid band's
independent content to the 808's +4.4 dB — and does it move the 50–300 ms residual step 5 could not
explain?** That is a candidate-4 question with a measurable target now attached to it: candidate 3
is 4.4 dB under, the shipped kit 6.1 dB over, and the mid band's decay rate is already inside
tolerance in both, so a level-only change is the thing to test rather than another filter.

Filed as a follow-up to #400, whose original framing — "find what the 808 has that we do not" in the
tail — is **answered in the negative for decay** by this step and redirected to level.

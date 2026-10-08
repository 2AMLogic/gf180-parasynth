# M1A as a phase-aware question (#337): drive, judged where phase is equal on both sides

**Experiment only; nothing is promoted.** This work adds an instrument
(`tools/phase_aware_m1a.py`), its tests (`tools/test_phase_aware_m1a.py`), a
report and this README. The selected patch (`m1a-gain-minus4db-v2`), the engine,
the frozen reference, the scorer (`m1a-envelope-score-v3`), its windows and its
tolerances are unchanged. No rubric is corrected here. A rubric correction is a
separate change and needs its own harmful counterexamples (plan098 §7).

## Why this question, and why it is phase-aware

The official M1A harmonic score compares one 250 ms window per note. The model's
two oscillators are phase-locked: ψ is about 1°, 5° and 9° at the three notes.
The Mini V3's oscillators run freely, so its ψ is +156°, −53° and +43° at those
notes (#218 §2–3). The even partials are interference terms between the
oscillators, and their level swings by 12–25 dB over a ψ cycle (#219). So the
official per-note even-partial errors mix two separate things. One is phase
behaviour (where each oscillator sits in its cycle). The other is persistent
timbre, which is what this task asks about.

The earlier drive experiment (`../drive-experiment/`) showed two things. Lowering the
ladder input drive removes the model's excess odd-partial phase sensitivity
(mean h5–h11 range 10.14 → 1.68 dB, against 1.43 dB for the reference). It also
raised the official phrase's even-partial errors, so the experiment stopped.
That stop was decided by the phase-blind score. This README asks the same
mechanism a phase-aware question. The issue requires that this is not another
unrestricted fit to the three historical phases. The answer is therefore
judged on predeclared conditions where ψ is **equal on both sides by
construction**:

- across a whole ψ cycle (development), and
- at each frozen note's measured ψ (confirmation).

## Pre-declaration (committed before any confirmation number existed)

### Data status, stated before the rule

- **Development (selection) data: the #219 MIDI 36 phase-cycle capture**
  (`../phase-cycle-capture/`). It has been inspected repeatedly. During this
  task's diagnostic period (the up-to-45-minute window in the issue), the
  per-partial numbers in the table below were computed from the committed
  `../drive-experiment/report.json` *before* this rule was written. Selection on
  this data is therefore a fit by construction, and it is labelled as one.
- **Confirmation data: the frozen M1A phrase** (`../m1a-repeat-0.wav`). The
  model is rendered phase-matched at each of the three scored events. This
  condition was **not** used to select anything below. It is **not pristine**,
  for two reasons:
  1. #218 published the baseline's phase-matched errors (its §5 "at matched
     phase" column).
  2. The drive experiment published the candidates' errors at the model's
     *locked* phase, which is not the matched phase.

  No candidate's phase-matched error at these events had been computed before
  this commit. MIDI 43 is a different note from the development note and is
  reported on its own.
- **The pristine test is a fresh Mini V3 capture.** That would be a MIDI 43 phase
  cycle, the independent condition #219 named. It needs the macOS plugin rig,
  which neither this host nor the build box has. Its rule is frozen below and
  the capture is left to an operator. It is **not** claimed here.

Diagnostic-period numbers (development data; legacy engine report; the
instrument recomputes them on the current engine):

| drive | phase-aware max over h2–h12 (dB) | mean (dB) | worst partial |
|---:|---:|---:|---|
| 0.75 (baseline) | 3.81 | 2.29 | h7 |
| 0.50 | 1.84 | 1.02 | h9 |
| 0.25 | 1.60 | 0.55 | h12 |
| reference take A vs B (noise floor) | 0.80 | — | h10 |

### The question (one)

On the M1A round-bass patch, the model and the Mini V3 can be compared at equal
relative oscillator phase. Under that comparison, does a lower ladder input
drive bring the model's harmonic timbre closer to the reference? And does it do
so on a condition that was not used to choose the drive?

### Baseline, candidates, budget

- **Baseline:** drive 0.75. This is the selected patch, rendered on the current
  selected engine. The instrument first REFUSES unless the baseline reproduces
  three things exactly: the committed `m1a-model.wav`, `results/M1A.json`'s
  valid properties to 1e-6, and #219's moving-phase model curves.
- **Candidates:** drive ∈ {0.50, 0.25}. This is two candidates under one
  mechanism: the drive grid predeclared in `../drive-experiment/`, reused
  unchanged. It was not refined after the diagnostic numbers above.
- **Level compensation:** the drive experiment's predeclared one-pass rule, in
  the patch, not by post-normalisation. Render the phrase uncompensated, take
  Δ = mean Gain-window RMS difference from baseline, and set
  `vol = vol_base·10^(Δ/20)`. It is never iterated or fitted to the reference.

### Target property (development, MIDI 36 phase cycle)

For each partial k = 2…12, form the ratio curve: the partial's 20° ψ-binned
power-mean level minus h1's, one value per bin, 18 bins (#219's estimator,
unchanged). The phase-aware error is

`E_k = RMS over the 18 bins of (model ratio curve − reference pooled ratio curve)`, in dB,

and the target property is `max_k E_k`. That is the same reduction (max over
partials) the official Harmonic shape uses, taken at equal ψ everywhere on the
cycle instead of at three unequal phases.

### Selection rule (choose at most one)

A candidate is eligible iff all of these hold:

1. `max_k E_k` is at least 0.5 dB below the baseline's;
2. on the complete official phrase, every baseline-passing graded property
   still passes. Those are Pitch (1 c), Envelope release (20 ms), Gain (3 dB)
   and Clipping (0.01 %). No property changes validity. So the candidate
   cannot win by getting quieter, losing pitch or clipping;
3. **brightness:** its development ψ-power-mean of h2–h12 relative to h1 is
   not lower than **both** the baseline's and the reference's by more than
   0.5 dB. It may not win by going darker than either.

Among eligible candidates, choose the lowest `max_k E_k`. If none is eligible:
**STOP**. Only the chosen candidate goes on to confirmation; nobody computes the
other candidate's confirmation numbers.

### Confirmation rule (frozen phrase, phase-matched; chosen candidate vs baseline)

Each event uses the #218 method:

- oscillator 2 is detuned by the diagnostic −3.49 c;
- its starting phase is set so that the model's ψ at the window centre equals
  the reference's ψ, measured from the frozen open-filter controls;
- the match is checked on the model's own isolated open-filter renders, and
  the instrument REFUSES if any |ψ error| exceeds 5°.

Baseline and candidate differ only in drive and vol. Window: the scorer's
on + 0.30 … 0.55 s. Quantity: `harmonic_signature` ratio error,
model − reference, h2–h12, at three events, which gives 33 cells.

- **Ill-conditioned cells are excluded by a reference-only criterion fixed
  here.** For each even partial 2j at each event, take the frozen open-filter
  controls' complex projections a (oscillator 1) and b (oscillator 2). The
  conditioning is `c = 20·log10(|a+b| / (|a|+|b|))`, and a cell with
  c < −6 dB is excluded as a notch. The value does not depend on any model
  render. Odd partials have no oscillator-2 contributor and are always
  included.
- **E_conf** = RMS of the included cells' errors (dB), pooled. **E_43** = the
  same over the MIDI 43 event alone.

**CONFIRMED** iff all of these hold:

1. E_conf(candidate) ≤ E_conf(baseline) − 0.5 dB;
2. E_43(candidate) < E_43(baseline);
3. the number of included cells with |error| ≤ 1 dB does not decrease;
4. brightness on the confirmation set: the power mean over events of h2–h12
   relative to h1 is not lower than **both** the baseline's and the
   reference's by more than 0.5 dB.

Anything else is **NOT CONFIRMED**. A missing precondition is **REFUSED**, which
is neither.

### What either outcome would mean

- **CONFIRMED** does not make this a shipped sound change. The official M1A
  rubric is phase-blind and scores the candidate worse. Promotion needs three
  further things:
  - a separate, phase-aware rubric change with harmful counterexamples;
  - the frozen fresh-capture rule below;
  - RTL and production-path verification of the ladder at the new gain
    register. The M1A patch is in the default register images
    (`tools/check_default_register_images.py`).
- **NOT CONFIRMED** closes the drive mechanism for M1A timbre. It leaves the
  unmet requirement visible.

### Frozen rule for the pristine test (fresh Mini V3 MIDI 43 phase-cycle capture; NOT run here)

The capture follows #219's protocol and preconditions unchanged, except that
the note is MIDI 43 instead of 36. The analysis is identical, with the model
detuned by that capture's measured octave offset. **CONFIRMED-FRESH** iff
both hold:

- the chosen candidate's `max_k E_k` is at least 0.5 dB below the baseline's;
- the candidate's `max_k E_k` ≤ max(1.0 dB, 2 × that capture's own take-A-vs-B
  max E_k).

Otherwise **NOT CONFIRMED**.

### Out of scope here (kept visible, not claimed)

Oscillator drift amount, resonance and drive at resonant settings, the envelope
response and other mixtures are all outside this one question. The three clean
F1 passes are untouched: F1 sets its own drive 1.0 and resonance 0 and does not
read the M1A patch. Their qualification is not extended to any resonant setting.

---

## Results (written after the run; nothing above was changed)

**Selection: drive 0.25. Confirmation on the frozen phrase, phase-matched:
CONFIRMED.** Every rule passed and none failed. The record is `report.json`,
written from clean commit `4de76f0` (`worktree_dirty: false`). One process
ran for 3 min 38 s.

**In sound terms:** the M1A round bass already sounds like the Mini V3 once the
two oscillators are compared at the same relative phase. That holds if the
ladder input drive is 0.25 instead of 0.75, with the level compensated in the
patch. At 0.75 the model's upper partials (h5–h12) sit 1.5–2.2 dB too dark on
average over the phase cycle, and they swing about 10 dB with phase where the
Mini V3's swing 0.4–2.2 dB. At 0.25 both quantities sit within the reference's
own take-to-take spread, for h2 through h9. **This does not reach the RTL or
the image.** The patch is unchanged (see "What this does not do").

### Preconditions (all held)

- The current selected engine reproduces the committed `m1a-model.wav`
  bit-exactly.
- The baseline properties equal `results/M1A.json` to within 1e-6.
- The baseline moving-phase curves equal #219's exactly.
- All three of the baseline's phase-matched renders reproduce #218's PCM
  hashes.
- The known-answer control passed 10/10.
- Matched ψ errors were 0.14 / 0.17 / 0.14° for the baseline and
  0.06 / 0.09 / 0.06° for the candidate, against the 5° limit.

The development numbers recomputed on the current engine equal the diagnostic
period's legacy-report numbers to the stated precision (3.81 / 1.84 / 1.60).

### Development: MIDI 36 phase cycle (selection; a fit by construction)

| | 0.75 (baseline) | 0.50 | 0.25 | reference take A vs B |
|---|---:|---:|---:|---:|
| phase-aware E, max over h2–h12 (dB) | 3.81 (h7) | 1.84 (h9) | **1.60** (h12) | 0.80 (h10) |
| mean over h2–h12 (dB) | 2.29 | 1.02 | **0.55** | — |
| E for h2 … h9 (dB) | 0.45–3.81 | 0.25–1.84 | **0.15–0.51** | 0.00–0.72 |
| E for h10 / h11 / h12 (dB) | 2.14 / 2.24 / 2.32 | 0.73 / 1.47 / 0.94 | 1.00 / 1.13 / 1.60 | 0.80 / 0.09 / 0.67 |
| mean odd ψ-range h5–h11 (ref 1.43 dB) | 10.14 | 5.96 | 1.68 | — |
| brightness h2–h12 re h1 (ref −0.47 dB) | −1.15 | −0.80 | −0.59 | — |
| vol (Q0.15) / compensation | 9304 / 0 | 13158 / +3.01 dB | 25422 / +8.73 dB | — |
| ladder `gain` register | 127 795 | 85 197 | 42 598 | — |

Selection rules on the official phrase, both candidates:

- Pitch −0.05 c, Envelope release +8.5 ms, Gain −0.95 / −0.93 dB and
  Clipping 0 % all still pass, and no property changes validity.
- Peak is −16.9 dBFS for drive 0.25.
- Neither candidate is darker than the baseline; both are brighter, toward the
  reference.

Both candidates were eligible. The rule chooses the lowest maximum, which is
0.25. **What still fails in development:** h10–h12 at 0.25 are 0.9–1.6 dB too
bright on the ψ mean (+0.92 / +1.05 / +1.56 dB). That exceeds the reference's
own spread on those partials.

### Confirmation: frozen phrase, each event at its measured reference ψ (not used for selection)

Excluded as notches by the reference-only rule (c < −6 dB), fixed before the
run:

- 36 @0.1 (ψ = +156.2°): h2, h12
- 43 @2.1 (ψ = −53.4°): h6, h8
- 36 @4.1 (ψ = +42.6°): h6, h8, h10

That leaves 26 of 33 cells.

| | baseline 0.75 | candidate 0.25 | rule |
|---|---:|---:|---|
| E_conf, RMS over 26 included cells (dB) | 2.68 | **0.56** | 1. ≤ baseline − 0.5 ✓ |
| E_43, MIDI 43 alone, a note not used in selection (dB) | 2.55 | **0.33** | 2. < baseline ✓ |
| included cells within 1 dB | 8 / 26 | **23 / 26** | 3. no decrease ✓ |
| brightness h2–h12 re h1 (reference −0.30 dB) | −0.98 | −0.41 | 4. not darker than both ✓ |
| max \|error\| over included cells (dB) | 8.68 | 1.74 | (reported) |
| *descriptive, not the rule:* all 33 cells, RMS / max (dB) | 3.57 / 10.52 | 0.73 / 2.07 | — |

The 33-cell row shows that the verdict does not depend on the notch exclusion.
#218 left the 36 @4.1 h8 cell unresolved at +10.52 dB after phase matching. At
drive 0.25 it is +2.07 dB. That cell is excluded by the rule; it is quoted here
only because #218 named it.

**What still fails on confirmation:** three included cells exceed 1 dB, and all
are upper partials on MIDI 36, too bright:

- 36 @0.1 h11: +1.23 dB
- 36 @4.1 h11: +1.02 dB
- 36 @4.1 h12: +1.74 dB

The development overshoot on h10–h12 does the same thing. On MIDI 43, every
included cell is within 0.78 dB.

### Effect, mechanism, and their status (verification rule 7)

The measured effect: changing only the ladder input drive from 0.75 to 0.25,
with level compensated in the patch, cuts the phase-matched harmonic error on
the frozen phrase from 2.68 to 0.56 dB RMS.
<!-- claim: grep="cuts the phase-matched harmonic error" in=docs/scorecard/mono-m1a-miniv3/phase-aware-337/README.md note="effect; report.json confirmation.baseline/candidate.e_conf_db" -->

The mechanism: the model's ladder input stage is driven harder than the Mini
V3's equivalent stage at this patch. That excess nonlinearity is what both
darkens the upper partials and makes the odd partials swing with ψ.
<!-- claim: grep="driven harder than the Mini V3" in=docs/scorecard/mono-m1a-miniv3/phase-aware-337/README.md mechanism=inferred note="only drive was varied, so the drive register is measured to carry the effect; that the Mini V3 has a correspondingly lighter input stage is a reading of that, not a measurement of the Mini" -->

### What this does not do, and what remains

- **Nothing is promoted.** The official phase-blind M1A rubric scores drive
  0.25 *worse*, at 22.63 dB against 19.82, with six per-cell regressions
  (unchanged from `../drive-experiment/`). It compares the locked model at
  ψ ≈ 1–9° with the reference at +156 / −53 / +43°. Promotion needs the
  following, in order:
  1. a **separate rubric change** that makes M1A's harmonic score phase-aware,
     tested against harmful counterexamples (plan098 §7);
  2. the patch identity (`vol` 25422, `drive` 0.25), regenerated default
     register images, and RTL and production-path ladder verification at
     `gain` 42 598 with explicit sample counts. That runs on the build box;
  3. registering the M1A drive in `docs/sensitivity/registry.json` under the
     sweep convention. **This was due when the proposal was made, and it is not
     done: drive 0.25 is unqualified under that convention, and this PR does not
     claim otherwise.** `rg -n 'm1a|drive' docs/sensitivity/registry.json`
     finds nothing. Two things block registering it now. `tools/sensitivity.py`
     reads only integer constants (`verilog-parameter`, `python-constant`) and
     fixed-width tables, while M1A's drive is a float keyword in
     `tools/mono_m1a_score.py` with its measurements in JSON. And the gate needs
     a shape prediction made independently of the measurement, and none exists:
     writing one after seeing this result would be the retrofit the convention
     warns about. So the gate cannot be run on this candidate yet, and promotion
     (step 2) must not proceed until it can.
- **The pristine test is still open.** The frozen fresh-capture rule above
  (Mini V3, MIDI 43 phase cycle) needs the macOS plugin rig.
- **Phase behaviour itself is unmet.** The shipping model's oscillators are
  locked, so ψ ≈ 1–9° at every note, while the Mini V3's free-run 3.49 c apart.
  That is why the locked model loses the official score. Drive does not address
  it. Oscillator drift is a contract choice (DR 0019). Whether to make the
  model's octave free-run is a separate question.
- **Out of scope and untouched:** resonance and drive at resonant settings,
  envelope response and other mixtures. The F1 passes are not extended.

### Known blind spots (verification rule 8, form 2)

- **E_k and the brightness guard see only h1–h12.** A candidate that added
  content above h12, or inharmonic content such as aliasing, would pass them
  unseen. No such input was constructed. Lowering drive reduces rather than
  adds nonlinear products, and Clipping stays at 0 %.
- **The notch criterion is linear.** It uses the open-filter controls' linear
  sum, and the full patch passes through the nonlinear ladder, where notch
  depth can differ. The 33-cell row above shows the verdict does not depend
  on the exclusion.
- **The control does not exercise ψ estimation.** It uses analytic ψ-binned
  curves, not renders, so the estimators (#219's ψ, #218's projections) are
  taken as they were qualified there. The guard against their failure is the
  baseline-reproduction preconditions above.

### Wrong-then-right (this task): 2, none in a reported number

1. The first draft of the pre-registration cited the drive experiment as
   "#283". That issue number was never checked. It was removed before the
   commit.
2. The first test written for confirmation rule 3 used an input that did not
   exercise the rule: the candidate *gained* in-band cells. The test failed,
   and the input was rebuilt so that rule 3 alone fires.

## Reproduce

```sh
.venv/bin/python tools/phase_aware_m1a.py            # ~4 min, one process; REFUSES (exit 2) on any precondition
.venv/bin/python tools/phase_aware_m1a.py --control  # known-answer control only
.venv/bin/python -m pytest -q tools/test_phase_aware_m1a.py
```

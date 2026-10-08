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

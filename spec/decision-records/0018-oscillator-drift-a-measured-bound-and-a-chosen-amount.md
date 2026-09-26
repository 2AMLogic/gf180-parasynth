# 0018: Oscillator drift — the references bound it, so the amount is a chosen range

- **Status**: proposed
- **Date**: 2026-09-26
- **Decided by**: block agent, from the reference measurement in
  `docs/scorecard/mono-osc-drift/reference-drift-v1.json`, the retracted
  measurement method on issue #138, and the existing DR 0012 voice LFSR
- **Extends**: contract 6.11 (new), 5.1, 5.2, 5.5, 14, 16; **closes** contract
  open item 18 in the half that is a mechanism, and answers rather than closes
  the other half
- **Issue**: #56

## Context

Three stable oscillators do not sound like three analog ones. The Model D's
thickness comes from three VCOs that wander independently and *continuously*;
what this repository had was `detune`, a fixed offset, whose beat is periodic.
Contract rev 11's open item 18 said the gap was real and that closing it "is a
musical decision and one constant". That framing turned out to be right, but
only because the measurement was done first and came back the wrong way.

Two things forced the shape of this record.

**The references cannot supply the amount.** Issue #56 asked for the magnitude,
rate, boundedness, independence and pitch-dependence of drift measured across
Surge XT, Arturia Mini V3 and u-he Diva before implementing anything. Measured
from the frozen, hash-bound renders already in `docs/scorecard/` (this host has
neither `dawdreamer` nor the VST3 bundles, so nothing was re-rendered):

- **Mini V3**: every isolated-oscillator window wanders by **0.001–0.024 cents
  rms**, and the same commanded note 13.6 s apart in one continuous render
  differs by **0.0011 cents**. The three renders of the same patch are
  byte-identical. That is 30–100× below anything a player hears: Mini V3's
  instability is off, or absent, at these settings. The references therefore
  **bound** drift rather than measure it.
- **Surge XT**: `REFUSED` — all sixteen frozen Surge clips drive the *filter*
  from our own stepped tone through Audio In; Surge's oscillators appear in none
  of them.
- **u-he Diva**: `REFUSED` — no frozen Diva audio exists in this repository.

A refusal is recorded as a refusal with the missing precondition named, not as a
zero. `tools/measure_osc_drift_reference.py` is the instrument and
`docs/scorecard/mono-osc-drift/reference-drift-v1.json` is its output.

**The negative control is mandatory, not optional.** Issue #138 proposed a
multi-period-fold measurement of this exact phenomenon and retracted it: the
strides are not commensurate with this instrument's cycle lengths. The deeper
point survives the retraction. **Static detune alone produces a combined
waveform that never settles**, through ordinary beating, so a naive
pitch-trajectory estimator fires on it and reports drift that is not there.
`model/osc_drift_probe.py` therefore ships with that control, and the control
caught three of its own defects before any of its numbers were used — recorded
at the constants they moved, including one where the probe called a statically
detuned pair `DRIFTING` at 0.2 cents.

## Decision

**1. The mechanism: three independent bounded walks on the three phase
increments** (contract 6.11). Per oscillator a 16-bit signed accumulator
updated once every 2^10 frames (21.33 ms):

```
acc_k <- sat16( acc_k + step_k − ((acc_k + 32) >> 6) )
inc_k <- clamp24( inc_k + ((inc_k · sat16((acc_k · DRIFT) >> 16)) >> 20) )
```

with `step_k = ((2·b_k + 1) − 32) << 5` from three **non-overlapping** 5-bit
fields `b_k` of the same 16-bit word the noise board's LFSR produced this frame.

Four properties are normative, and each has a negative control in
`rtl-sketch/voice_dp.v` because each is a defect that looks like the feature:

| property | why | control |
|---|---|---|
| the three walks are independent | three oscillators in lockstep is vibrato; it is also invisible in any measurement of one oscillator | `DRIFT_SHARED` |
| the step's mean is exactly 0 | a −0.5 mean step against a leak of `acc/64` parks the walk at −32: a permanent detune wearing drift's clothes | `DRIFT_MEANSTEP` |
| the leak rounds to nearest | a floor pulls every negative state up one LSB per update — a bias, not a rounding difference | `DRIFT_LEAKFLOOR` |
| the walk is bounded and never saturates | a saturating walk has a *maximum* detune, which is a different mechanism | measured: `sat16` has 2.1× headroom over the largest state in 2^18 updates |

**2. Entropy is the existing voice LFSR, not a second generator.** DR 0012's
31-bit LFSR already runs every frame. The three fields are reads of one
m-sequence 5 and 10 places apart, and two shifts of an m-sequence
cross-correlate at −1/(2^31 − 1) — the same argument DR 0012 uses for the
voice/drum seed separation, reused rather than reinvented, and the *realised*
correlation is measured (below 0.01 for the steps, 0.05 for the walks, over
2^18 updates) rather than assumed from it.

**3. Deterministic and seeded, because bit-exactness is a constraint on the
design.** `rtl-sketch/verify_voice.py` compares the model and the RTL with zero
tolerance. A free-running generator would break that, so drift is a pure
function of the seed and the frame count — and the *state* is compared too
(`drift_cnt` and all three `drift_acc` are in the final-state vector), not only
the samples it produces.

**4. `DRIFT = 0` is bit-identical to no drift mechanism at all**, and the reset
value is 0. `dev_k = 0` makes `inc_k + ((inc_k · 0) >> 20) = inc_k` exactly.
This is a design requirement, not an observation: every register image, every
recorded scenario, every pinned table and every frozen scorecard case predates
this record, and a mechanism that moved one LSB when off would have to be
re-baselined against all of them instead of reviewed on its own.

**5. The amount is a MUSICAL decision and it is a RANGE: 0.8–4.0 cents rms per
oscillator**, reference value **1.5 cents** (`DRIFT` = 17543). It is a range
because SM 2.3 documents different oscillator boards by serial range, and
because the references bound drift rather than supplying a value — stating a
single number would dress a choice as a measurement. The register spans
0–5.604 cents rms, so the range sits inside it with headroom either side.

**6. The host conversion divides by a MEASURED constant.**
`DRIFT_ACC_RMS = 3394` is the walk's stationary rms measured from the shipped
integer generator over 2^18 updates (3406.4 / 3343.0 / 3432.3 for the three
oscillators, whose spread is that run's own sampling error) — not the
continuous-time Ornstein-Uhlenbeck formula. A conversion grounded in the
closed-form value would silently rescale every drift depth ever written by
whatever the integer generator's leak-and-saturate arithmetic actually does.

## Alternatives considered

- **A depth on the existing `MR_OSC` / `osc_mod` modulation path (6.9).** It is
  already there and costs nothing new, and it cannot work: 6.9 carries **one**
  `mod_sig` to all three oscillators by construction, so any depth on it moves
  the three together. That is vibrato — which the Model D also has, and which
  6.9 is the right mechanism for — not drift. "The three must drift
  independently" is the requirement that rules this out, whatever the depth.
- **A second LFSR, or three.** Three independent generators are the obvious way
  to get three independent walks. Rejected: the existing LFSR's word already
  carries 16 fresh bits per frame and the decimation uses 15 of them, so a
  second generator buys flops and a second seed to keep in sync between model
  and RTL for decorrelation the m-sequence argument already provides — and now
  measures.
- **An unbounded random walk (no leak).** Simpler: one adder, no rounding
  question. Rejected because it has no stationary rms, so there is nothing for
  the host conversion of 5.5 to divide by, and because it eventually reaches the
  saturation rail, at which point the mechanism silently becomes "a maximum
  detune". It is kept as the control in the boundedness measurement instead.
- **Additive rather than multiplicative perturbation** (`inc + dev`). One fewer
  multiply. Rejected: a constant Hz offset is ~40 dB more cents at the bottom of
  the keyboard than at the top, so the effect would be a different effect at
  every note and no single register value could describe it.
- **Drifting the duty cycle as well** (W3a's 50 % vs a reference's 52 %). A
  slightly asymmetric square is audibly fatter. Not taken: it is a second
  mechanism in a second place, SM 2.3 is explicit that 50 % is the design and
  that R137 was hand-selected per unit to hit it, and nothing measured here
  argues for the amount. That half of contract item 18 stays open.
- **A faster or slower update rate.** 2^10 frames (21.33 ms) with a leak of
  `acc/64` gives a correlation time of 1.365 s, which is the timescale a
  listener calls drift rather than vibrato. Not swept, and that is a gap: the
  rate is a power of two because the counter is, and 2^9 or 2^11 would cost the
  same.

## Consequences

**Both scores of `docs/target.md`, reported separately, because an improvement
in one is not evidence about the other.**

- **Implementation** (*did we build the model we specified?*): **strong, and
  extended.** `verify_voice.py --set quick --only drift` is bit-exact over 9 728
  frames — every sample, every tap and the final state including `drift_cnt` and
  all three `drift_acc` — across the reference depth, the register maximum
  together with the shared modulation bus at full wheel, an oscillator drifted
  into the increment clamp, and a window straddling a walk-update boundary. All
  three injected defects turn it red. The full quick set (74 144 frames) is
  unchanged.
- **Sound model** (*is that model the instrument we want?*): **no improvement is
  claimed, and none should be expected from this record.** Two reasons, both
  measured: the default is off, so every frozen scorecard case renders sample for
  sample as before and no spectral-match distance moves at all; and when it is
  on there is no reference delta to close, because the frozen references bound
  drift at 0.001–0.024 cents rms. The claim this record makes is a
  **playability** one — that three oscillators which wander independently sound
  thicker than three that do not — and it is **unvalidated against any external
  reference**. It rests on the service manual, on the circuit, and on taste.
  Saying so is the point: `docs/target.md`'s whole distinction exists because
  this project has repeatedly let a strong implementation score stand in for a
  sound-model one.

**What this makes possible.** A patch can ask for drift in cents and get the
same audible amount at every pitch. The scorecard can be re-run with drift on as
a deliberate, separately reviewable change, and because 0 is bit-identical to
absent, that change's diff is exactly the drift.

**What it makes harder.** Every future voice measurement has one more register
that must be 0 to reproduce a historical number, and `DRIFT` is now part of the
image a host must write. `verify_voice.py` grows a scenario key that must be run
for the three new controls to be satisfiable at all — on any other scenario they
are silent, which is why contract 16 names the scenario rather than just the
defect.

**What is not done.** The update rate is not swept (above). Nothing here is
validated against a real Model D, or against any reference with measurable
oscillator drift: closing that needs either a Surge XT / Diva render of a
*sustained oscillator* with instability enabled, or the hardware session
`docs/target.md` specifies. Until then 0.8–4.0 cents is a choice this record
owns, and the reference JSON records the bound that constrains it.

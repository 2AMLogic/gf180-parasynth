# Why this project keeps producing confident wrong answers

Eight measurement claims, three area projections and two "confirmed defects"
were withdrawn in a single day's work. That is too many to treat as
carelessness, and the incidents have one mechanism in common. This document
names it and says what to automate, because the goal is a design and build
process that does not need a human to notice.

## The root cause

**Internal consistency is cheap to check. External grounding is expensive. So
work drifts toward the cheap check, and the cheap check feels like rigor
because it is rigorous in form.**

Every failure below is a version of that trade:

| what we did | cost | what it could not tell us |
|---|---|---|
| validated estimators against **our own model** | seconds | whether the estimator is right |
| tested the voice against **our own decision records** | seconds | whether the model is a Minimoog |
| verified **blocks** bit-exactly | minutes | whether the blocks talk to each other |
| computed area by **arithmetic** | instant | what routes |
| **argued** about 8 vs 12 modes | free | which variable the objective depends on |

None of those is wrong to do. Each is genuinely rigorous *within its frame*.
The failure is that the frame was never checked, and a suite that cannot see
outside itself reports success in exactly the same voice whether or not the
thing is right.

**The gradient is real and it will act on any agent**, not just a careless one:
internal checks are fast, deterministic and always available; external
grounding is slow, sometimes unavailable, and occasionally blocked entirely. An
agent under time pressure will take the cheap check every time unless the
expensive one is *mandatory and scheduled*, not aspirational.

## Five mechanisms, and what to automate for each

### 1. Measuring without a known answer

Nearly every withdrawn number came from running an estimator on real data and
reporting the output, having never run it on a signal whose answer was known.
A 5 ms moving average on a 56 Hz carrier. A Hann-windowed 700 Hz split that
returned 1.25 % where the true answer was exactly 18.55 %. An
amplitude-weighted centroid. Spectral flatness used as a comb-versus-noise
test.

**Calibrating on our own model does not count.** `moog_probe.py`'s "25 dB of
separation" was calibrated that way and turned out to be window leakage. A
self-comparison establishes repeatability, not correctness.

> **Automate:** an estimator may not be used by an acceptance test unless it
> has a closed-form ground-truth test — a signal whose answer is known
> *independently of the thing being measured*. Enforce with a meta-test that
> every measurement function reachable from an acceptance test appears in the
> ground-truth suite. Partly in place: `model/test_audio_measure.py` exists and
> found six estimator bugs on the day it was written.

### 2. No external referent for a whole class of claim

The drums have `docs/tr808-reference.md` — 110 facts traced to schematics and
service manuals — plus a real recording corpus. The voice has **neither**, and
so accumulated 42 rigorous tests that could all pass on a filter that sounds
nothing like a Moog. The gap was invisible because the test count looked
healthy.

> **Automate:** classify every test as **implementation** (against our model)
> or **fidelity** (against an external reference), and report the two counts
> separately per subsystem. A subsystem with zero fidelity tests is
> **UNVALIDATED** and says so in CI, however many implementation tests pass.
> Then the voice's gap is a visible red state rather than something a person
> has to notice.

### 3. The verified artifact was not the shipped artifact

The FPGA build omitted every drum module while its reports quoted utilization
for "the instrument". `synth_top` instantiated a placeholder while area figures
were quoted for the chip. `drum_kit`'s configuration storage did not exist in
RTL — the values were input ports a testbench drove. And **every bench drove
the register write port rather than the link**, which is why the control path
delivered 37 of 155 writes with every block still bit-exact.

One mechanism: **the thing under test was a different object from the thing
that ships**, and everything passed the whole time.

> **Automate:** every artifact asserts its own provenance against the thing
> that verified it. `make srccheck` is the working example — it fails the build
> if the routed file set differs from what the bench elaborates, and I broke it
> deliberately to confirm it fires. Generalise: reports carry the commit that
> produced them and are marked stale when it is not an ancestor of HEAD; a
> testbench that drives an internal port rather than a pin says so in its own
> output.

### 4. Claims outliving their evidence

The `modal_dp` excitation hazard was cited in briefs and documents for hours
*after* DR 0008 fixed it. The capability DAG carried red on four drum circuits
that had been repaired. A withdrawn snare figure survived into a GitHub issue.
A strict xfail stayed red for an unrelated reason and hid a real closure.

Stale findings are a distinct failure from wrong ones and need a distinct
remedy: being right once is not a property that persists.

> **Automate:** a claim in a document carries the test or commit that justifies
> it, and a checker flags claims whose backing test no longer exists, now
> passes, or predates the file it describes. When a tracked-defect marker
> fires, assert the failure is the recorded one.

### 5. Optimising a variable before measuring whether it matters

Hours went into 8 modes versus 12. The answer was that yosys pads the bank's
state to a power of two, so 9 through 16 cost **identically** — and that the
real variable was `NUMS`, which nobody had looked at. Hours went into the bass
drum, which measurement later ranked our *best* voice. Then into the snare's
noise balance, which was correct; the fault was burst length.

Each was a plausible hypothesis acted on before anyone measured where the
objective was actually sensitive.

> **Automate:** before a parameter debate is allowed to consume time, sweep it.
> A one-line sweep would have ended the modes argument in minutes. Make
> sensitivity analysis a gate on optimisation work, not an afterthought.
>
> **In place.** `tools/sensitivity.py check`, in `make verify` and in the
> `python` job of `rungs.yml`. A parameter joins
> `docs/sensitivity/registry.json` when someone *proposes* changing it; from
> then on the gate asserts that the value which ships is a point on a committed
> grid, that the grid **is** the set of points the measurement artefact holds
> (so a plateau cannot be manufactured by dropping one), that every recorded
> point re-extracts from that artefact, that the flat/sensitive verdict is
> recomputed rather than asserted, and that the measured shape matches a
> prediction derived **independently of the measurement**. Six injected
> controls in `make controls`. `MODES` and `NUMS` are retrofitted — the
> argument, recomputed, is a step of +16.8 % from 8 into the 9–16 bracket and
> then a plateau of 0.67 % across 11–16, against +6.35 % and +7.52 % for the
> dial nobody swept. Convention and limits: `docs/sensitivity-sweeps.md`.
>
> **What it still cannot do:** see an argument. It gates the artefact the
> argument should have been settled by, and it only watches parameters someone
> registered.

## A sixth, different in kind: rejecting imperfect evidence

`docs/discrimination.md` §8 rejected software-synth references because
comparing against them "would test our chip against another emulation". The
reasoning is valid. The consequence was **zero validation instead of imperfect
validation** — and it discarded the one property a hardware corpus cannot
supply: a reference you can set to a known patch.

When that reasoning was overruled, the first run found a term of Huovilainen's
paper that we had omitted (cutoff drift 7.92 pp over six octaves against Surge
Type 2's 0.62; worst error 6.85 % → 0.89 %), correctly diagnosed our excess
5th harmonic as the 16-entry `tanh` table rather than the structure, and
withdrew one of our own false claims.

> **Rule:** reject a reference only when a better one is actually available,
> never on principle. Imperfect evidence with its limitation labelled beats an
> unfalsifiable claim. Purity standards that produce no measurement are not
> rigour; they are the absence of it wearing rigour's clothes.

## What is already mechanical, and what is not

**In place.** Ground-truth estimator suite. Injected-defect controls on every
integration check, including the exact defect that shipped (`SPI_ADDR7`).
`make srccheck`. CI running the integrated verifier, not only block checks. A
negative control that mutates *arithmetic* rather than syntax — which caught
that inflating `rms` by 5 % passed all sixty ground-truth tests. Sensitivity
sweeps as a gate (`tools/sensitivity.py`, mechanism 5 above) — registry-scoped,
so it watches the parameters someone registered and not every parameter.

**Not yet.** The estimator meta-test. Implementation-versus-fidelity test
classification and the UNVALIDATED state. Report staleness. Claim-to-evidence
linking.

Those four are the difference between a process that catches this class of
error and one that relies on someone reading carefully at the right moment.

---

# The second batch: the apparatus, not the evidence

The mechanisms above are about **evidence** — we validated against ourselves.
A later day produced a different cluster, and it has its own root cause.

## Root cause: preconditions assumed rather than asserted

**We verified what a tool measures, but not that it was in a state to measure
anything.** Every one of these is a correct instrument in a wrong state:

| what happened | the precondition nobody asserted |
|---|---|
| u-he **Diva** contaminated every measurement it touched for hours | it was an **unlicensed demo** — 20 broadband clicks in 360 s, nothing for the first 167 s then clusters every ~33 s |
| **Model D** renders exact digital silence | authorisation is not visible to the plugin in a host called "Python" |
| **Surge** parameter 265 is "Unison Voices" — or **"High Cut"** | Surge **renames parameters 259–267 by oscillator type**, and the change is not in effect until a render. The pin would have put a **13.75 Hz high cut** on every measurement |
| **Mini V3** measured at the wrong octave | `Range Osc1` had defaulted to the **sub-audio `Low`** setting |
| all three plugins "stepped" identically at **94 Hz** | dawdreamer applies automation **per host block**; at the default 512 samples that is 93.75 Hz. A 16-sample block **reversed the conclusion** |
| a Surge "square" reported **h2 = +79.6 dB as data** | the rig was not making the waveform it had been asked for |

None of these is a measurement error in the sense the first section describes.
The estimators were right. **The apparatus was not in the state the estimator
assumed.**

### The practice that works, because it worked six times

**Every apparatus asserts its own preconditions at the point of use, and
REFUSES rather than reports when they fail.**

`REFUSED` has to be a first-class outcome, distinct from pass and from fail. A
tool that cannot currently answer must say so; a tool that answers anyway is
worse than one that is absent, because its output looks exactly like data.

What caught each of the above was a precondition check at the point of use:
the **parameter-name check** caught Surge's rename on the first run; a
**plausibility guard** caught the +79.6 dB square; **`make srccheck`** caught
an FPGA build that quoted utilization for an instrument it did not contain; a
**pin read-back** caught Mini V3's range. None was caught by inspection.

## Root cause: gates that cannot be satisfied

**Three in a single day**, all written by the same author, all with the same
shape — an assertion committed without checking the job asserting it could
ever pass:

- a CI step invoking `sound_report.py --all --out`, written **before the tool
  existed**; the real interface is `--changed / --inject / --list-injections`
- a fidelity warning that fired on **Foundation, Integration and Silicon**,
  where it cannot be satisfied — a control link has no Minimoog to be compared
  against
- `compile_dag.py --check`, which embedded the commit SHA (so the README went
  stale the instant anything merged) **and** failed on any RED or STALE node,
  which the per-push job cannot refresh because those verifiers take hours

**An unsatisfiable gate is worse than no gate**, because it trains everyone to
ignore gates — including the ones that work.

> **Practice: run a gate against the current state before committing it, and
> require that it can pass.** If the job that runs it cannot reach the state
> the gate demands, the gate belongs in a different job. `--check` asserts the
> document is true; `--strict` asserts the project is healthy; only the nightly,
> which can refresh slow evidence, is entitled to demand the second.

## Publish the wrong-then-right rate

One agent-session produced **five** measurements that were confidently wrong
before they were right, every one caught by a control or a plausibility guard
rather than by inspection.

That is not a failure to hide. **A harness that has caught itself out five
times is more trustworthy than one that has never noticed anything** — but only
if the reader knows the rate.

> **Practice: report the rate where the numbers are read**, not in a private
> summary. It is how a reader calibrates how much weight any single figure can
> carry.

---

# The third batch: the root cause underneath the other two

Both root causes above are instances of one thing, and naming it makes the fix
generative instead of reactive.

## Root cause: status is carried in prose, not in data

**Every failure here had a cheap signal available that had the same SHAPE as the
expensive answer, and nothing forced the distinction at the point of use.**

- `grep -c` returned `1`, which is shaped like a finding. The match was a
  comment saying the opposite of what was concluded.
- A brief is prose, which is shaped like current truth. Three agents worked from
  premises that had gone false underneath them.
- `[verified: SN text; magnitude inferred]` is a comment, which is shaped like a
  measurement. It shipped as `TOM_DROP_RATIO = 1.7`; the machine measures
  **x1.063**, and the excess is **11.1x** too large.
- A tolerance is a number, which is shaped like a justified threshold. Not one
  of ours has a measured spread behind it.
- A window is a choice, which is shaped like a convention. Two sides were
  windowed differently and the difference was worth **6 dB against a 3 dB
  tolerance**.
- An uncommitted file is shaped like a saved file. Finished work was lost twice
  in one session.

## The evidence: what saved us has exactly one shape

Six things caught errors in this session. Every one is **an artifact that
carries its own status, plus a tool that refuses when the status is
insufficient**:

| what caught it | what it carried | what it refused |
|---|---|---|
| provenance block on every result | commit, dirty flag, input hashes | told a stale checkout from a capability gap -- 8 false refusals |
| whole-batch base check | tree vs `origin/main` | blocked a coefficient change being scored against a modified tree |
| `"valid": false` with **no** `error` key | validity, separately from value | stopped an artefact reading as a perfect zero |
| estimator validation on known signals | the validated domain | **nine** wrong-then-rights in one agent, none caught by inspection |
| injected controls | that the test can fail | a drive level overflowing the input by 0.34 dB |
| the stale-tree banner | commits behind `origin/main` | a board read as current that was ten commits old |

And every failure above is the **absence** of that pattern. That is the whole
theory: not "be careful", but **put the status in the data and make the consumer
refuse.**

## The generative form: invariance over expected value

An expected-value test catches the error you already know about. **An invariance
test catches errors nobody has thought of**, because it asserts something that
must hold *regardless of what the right answer is.*

Prepending digital silence cannot change what a machine did in 1980. That one
property, asserted, would have caught the 6 dB windowing bias without anyone
knowing the correct band split. Scaling by a constant cannot change a *ratio*.
Shifting a signal cannot change an onset-relative measure.

**We keep adding a check after each failure.** That is reactive and the list
grows forever. Asserting the invariant is generative: it catches the whole class,
including the instances not yet hit.

## What follows, mechanically

1. **Constants carry provenance as data, not comments**, so "list every inferred
   constant a failing case depends on" is a query rather than a grep.
2. **Estimators declare their validated domain and refuse outside it.**
   `tone_ratio_db` is exact on a stationary two-tone and loses **16 dB at 5 %
   detuning** -- knowable, and therefore checkable.
3. **Tolerances carry the measurement that justifies them**, or are marked as
   guesses. Today they are all guesses.
4. **Briefs carry machine-checkable preconditions**, not prose ones.
5. **Uncommitted work in a worktree blocks reporting completion.**

The test of whether this is working is not that the list of checks grows. It is
that **the next unknown failure is caught by a check written before anyone knew
about it.**

# The scorecard

`cases.csv` is the source — 100 cases, **80 development and 20 holdout**, split
Drums 32 / Mono 32 / Filters 24 / Ensemble 12. Results are one JSON per case in
`results/`. `tools/scorecard.py` renders the board.

There is deliberately **no spreadsheet in the repository.** A binary you cannot
read from a console cannot be diffed, grepped or reviewed in a pull request. The
sheet is a view; this is the evidence.

## The completion gate for the first 32

**Thirty-two honestly accounted-for cases — not thirty-two passing ones.** A
baseline of failures and no-verdicts is a baseline; a baseline of unrun cases is
not. What makes later improvement measurable is that every case has a stated
outcome, including the ones we could not measure and why.

## Rules the tool enforces, each because it is a way a scorecard starts lying

**An invalid measurement has no distance, not zero distance.** Zero reads as a
perfect match. It is `no verdict`, and it counts against coverage.

**A missing required component invalidates the case** rather than being dropped
from the maximum — otherwise the cheapest route to a better score is to stop
measuring the inconvenient thing.

**Distances are never averaged across units.** Milliseconds, cents and decibels
do not combine. Each is normalised by *its own* tolerance; the case reports the
worst, which is dimensionless and passes at ≤ 1.

**Coverage is reported separately and always** — *"20 passing, 4 failing, 6
without verdicts"*, never *"83 % passing"*, which conceals what was not checked.

**Every result names the engine that produced it:** `float-model`,
`fixed-model`, `integrated-rtl`, `board-digital`, `board-analog`. These are not
interchangeable, and the tool says so out loud when no case has been measured on
the integrated RTL:

> *no case has been measured on the integrated RTL. Results describe a model,
> not the instrument.*

**That is the failure this column exists to prevent** — optimising eighty cases
against a model the built instrument does not reproduce.

## Two scores for one change: bass compensation

A ladder loses bass as its resonance rises — `H(0) = 1/(1 + k)` in the
small-signal model, which `model/test_moog_acceptance.py` now asserts against
our filter directly.
<!-- claim: test=model/test_moog_acceptance.py::test_the_ladders_low_frequency_gain_is_one_over_one_plus_k -->
DR 0005's `ogain` gives part of it back (`(1 + 2 res)`), and DR 0006's `k_comp`
ROM moves the onset. **Both are level policy, and a change to either has two
effects that must never be added up:**

| property | asks | measured against |
|---|---|---|
| **`Bass loss`** | *does it match the reference?* | the frozen Surge Type 2 profile — the low-frequency gain versus resonance of another implementation of the same filter |
| **`Playing weight`** | *does it sound bigger?* | our own declared level at that node, unnormalised — no reference, and no claim that a reference would agree |

The two are **separate named properties on the same case** (`F2A`–`F2D` in
[`cases.csv`](cases.csv)), never combined into one number, because the
interesting change is the one that moves them in opposite directions:
compensation that makes the instrument feel better to play while moving it
*away* from the reference. That is a legitimate product choice — and it has to
be **visible as a choice**, which means seeing both numbers, not an average
that hides which half paid for which.
<!-- claim: test=tools/test_acceptance_policy.py::test_a_bass_compensation_trade_is_two_properties_not_an_average -->

Two consequences of listing both as required measurements, both deliberate:

- **You cannot report the weight and call the compensation validated.**
  A case missing either half is `no verdict`, by the rule three sections up.
  `Bass loss` is currently a stated not-run (`run_case.NOT_RUN["F2A"]`: the
  comparison is not well posed until a matched-drive definition is written
  down), and `Playing weight` is measurable on our own output today — exactly
  the asymmetry that would otherwise let the easy half stand in for the hard
  one.
  <!-- claim: test=tools/test_acceptance_policy.py::test_every_case_scoring_bass_loss_also_scores_playing_weight -->
- **A regression in either is a regression.** `scorecard.compare` rejects a
  candidate where any property regresses past its allowance, whatever the
  others did; widening one property's allowance is available and is a recorded
  decision, which is the difference between a trade and an accident.

## What is frozen before results are collected, and why

Reference identity and patch · parameter mappings · allowed alignment and level
matching · measurement definitions · acceptance tolerances.

**For the filter cases that is a file: [`refprofile/`](../../refprofile/README.md).**
The reference audio is rendered **once** through the qualified rig of #87,
cached, and hashed; `refprofile/profile.json` — which is committed — holds the
hashes, the plugin's bundle version *and* its binary SHA-256, all 2,855
parameters after setup, the readbacks the rig qualified on, and the commit it
was built at. `tools/run_case.py` reads that cache and **never renders a
plugin**; a cache that is absent or whose bytes do not hash to what the profile
says is a stated no-verdict, and re-rendering is an explicit act whose diff
somebody reviews.

The profile is also one of the runner's `DEPENDENCIES`: a batch whose profile
differs from `origin/main` refuses the whole batch, for the same reason a stale
`drums_fx.py` does.

### The profile says no, three times, and those entries are the point

Moog's **Model D** — the cross-check every Mono case names — renders **exact
silence** headlessly: peak 0.0 with oscillator 1 on at full level, and 0.0 with
the filter self-oscillating. **Mini V3** makes sound, but its envelope knobs are
bare 0..1 values that nothing here maps to a time, and every Mono case requires
envelope timing. **Diva** is unlicensed and clicks. So the eight First-32 Mono
cases stay `not run`, each carrying that measurement as its reason — a profile
covering one subject honestly beats one covering four with three quietly wrong.

A fixed, calibrated cutoff conversion between synths is legitimate. **Retuning
each patch after inspecting its error is not** — it conceals a deficient control
response by fitting around it.

Holdout settings are chosen now, and they hold out **meaningful settings and
playing sequences, not different noise seeds.** A different stochastic strike
tests repeatability; it is not evidence of generalisation to a new knob setting.

And once a holdout case's detailed errors have guided a change, **it has become
development data** — a fresh independent claim needs new holdout cases.

## Filling it

`tools/run_case.py` writes the result files. It renders our side in-process
from the integer models (never a committed WAV), loads or renders the reference
side, measures both with the same estimator, and writes one JSON per case.

```
tools/run_case.py D01A                  one case
tools/run_case.py --batch "First 32"    the first batch
tools/run_case.py --list                what is covered, what is not, and why
make board                              the batch, then re-render this board
```

Its exit status is the repository's verifier convention — **0 match, 1
mismatch (a result), 2 did not run (no evidence)** — and the same code is
written onto each record as `provenance.outcome_code`. A first batch that holds
deliberate not-runs exits 2 by design: the board, not the status, is the report.

### The two controls

A runner's only failure mode that matters is a false green, so the two states
that are easy to get wrong are injectable and run by `make controls`:

```
tools/run_case.py --inject REF_F0_20PCT D01A --results build/x --expect fail
tools/run_case.py --inject REF_MISSING  D01A --results build/x --expect 'no verdict'
```

The first moves the reference pitch by 20 %, twice the frequency tolerance: the
case must come back **fail**. The second points the reference at a file that is
not there: it must come back **no verdict**, with the reason on the record and
no `error` key at all. `--inject` refuses to write into `results/` — a
control's output is not evidence about the instrument.

Three more cover the frozen profile, and the last is the one that makes
"frozen" mean anything:

```
tools/run_case.py --inject REF_CORNER_2X        F1A --results build/x --expect fail
tools/run_case.py --inject REF_PROFILE_MISSING  F1A --results build/x --expect 'no verdict'
tools/run_case.py --inject REF_PROFILE_TAMPERED F1A --results build/x --expect 'no verdict'
```

`REF_CORNER_2X` time-stretches the frozen clip by two, which moves the
reference filter's corner down an octave — far past the 10 % frequency
tolerance, so the case must come back **fail**. `REF_PROFILE_TAMPERED` makes
the cached audio disagree with the hash in the committed profile: the runner
must refuse to read it at all. A frozen reference whose only failure mode that
matters is *drifted audio read as though it were the reference* needs that
control more than it needs any other.

### The tolerances, and that they are not per case

Three classes, frozen in `tools/run_case.py` before any number was computed,
and every metric names the class it used:

| class | tolerance | where it comes from |
|---|---|---|
| frequency | 10 % of the reference value | the TR-808's own component tolerance on f0, `docs/tr808-reference.md` §1.7 |
| time | 50 % of the reference value | §1.7's ±50 % on Q, and τ ∝ Q for these bridged-T resonators |

Every decay is a **T20 off the backward-integrated energy curve**
(`audio_measure.schroeder_t20`), never a single exponential's τ. It was a τ
first, and `decay_tau` refused five of the eight references outright — *"not a
single exponential"*, residuals of 4 to 23 dB. It was right to: the hats, the
cowbell and the cymbal are sums of incommensurate squares whose envelope beats
by 6–10 dB, and the clap is three bursts over a tail. **The response to a
refused precondition is an estimator whose precondition holds, not a looser
threshold on the first one.** The Schroeder curve is monotone by construction
and equals ln(10)·τ exactly on a signal that really is one exponential. As an
external check, the T20s it reads off the reference recordings agree with the
τ values `docs/drum-verification.md` published from a different estimator:
LT 202.7 ms measured against 202 predicted, BD 537 against 530.
| energy ratio | 3 dB | the half-power convention: a stated convention, not a number derived from any error of ours |

A tolerance chosen per case, after seeing the error, is fitting around the
deficiency it was supposed to catch.

## The premise of the batch, asserted before it runs

`run_case.py` refuses a whole batch when an input it depends on differs from
`origin/main`. That check exists because of one run: eight drum cases came back

> REFUSED: the eight-stop kit does not implement LC / MT / MC / HC / CL / RS /
> MA / CY

which was exactly right about the worktree it had, and false about the project
— the complete sixteen-sound kit had landed on `origin/main` two commits
earlier. **Eight honest per-case refusals read as a permanent hole in the
instrument.** A stale premise is a property of the checkout, not of the
instrument, and the two must never come out looking alike.

It refuses on the *dependencies* — `drums_fx.py`, `voice_fx.py`,
`audio_measure.py`, `drum_verify.py`, `cases.csv` — and on the drum circuit
count, not on the raw commit count. `main` moves several times an hour here;
a gate that fires on commits which cannot change a measurement trains everyone
to pass `--allow-stale`, and an ignored gate is worse than no gate.

## Provenance: what a result was measured against

**A result that cannot say what produced it is a number, not evidence.**
Nothing else in this repository records it — no verifier here calls
`rev-parse` — so a stale result has been indistinguishable from a current one,
and many worktrees are live at once. Every result now carries:

```json
"provenance": {
  "engine": "fixed-model",
  "worktree": {"commit": "0d8a763", "branch": "tools/case-runner",
               "dirty": true, "uncommitted_sha256": "…", "untracked_files": 3},
  "command": "tools/run_case.py --batch First 32",
  "config": {"voice": "BD", "refs": "/tmp/tr808-ref", "bus_gain": 0.45},
  "inputs": {"model/drums_fx.py": "sha256:…", "reference:bd8/BD5050.WAV": "sha256:…"},
  "artefacts": {"ours": "build/scorecard/D01A-ours.wav", "reference": "…/BD5050.WAV"},
  "outcome_code": 1, "outcome_code_meaning": "0 match, 1 mismatch (a result), 2 did not run"
}
```

A clean commit alone is not enough: a SHA that silently means "plus whatever
was in the working tree" is worse than no SHA, so the uncommitted diff and
every untracked file are hashed alongside it. **`tools/scorecard.py` gives no
verdict to a result without a provenance block** — it counts against coverage,
never towards it.

## What a result file looks like

```json
{
  "engine": "integrated-rtl",
  "source_commit": "2024889",
  "reference_profile": "miniv3-1.3.0-patch-a",
  "render_run": "…", "analysis_run": "…", "audio": "…",
  "metrics": {
    "Pitch trajectory": {"value": 49.8, "units": "Hz", "reference": 49.4,
                         "error": 0.4, "tolerance": 1.0, "valid": true},
    "decay":            {"value": 70.0, "units": "ms", "reference": [90, 110],
                         "error": 20.0, "tolerance": 10.0, "valid": true}
  }
}
```

**Distance is kept per property, not just per case.** An accepted τ of 90–110 ms
measured at 70 is *20 ms below the range*; improving it to 85 is visible progress
before it passes — which is the point of a continuous score.

# Sweep a parameter before arguing about it

Hours went into 8 modes versus 12. yosys pads the mode bank's state arrays to a
power of two words, so **9 through 16 cost identically**, and the variable that
actually moved the objective was `NUMS`, which nobody had swept.

That is `docs/failure-modes.md` mechanism 5. Its remedy was one sentence of
prose for a year. This document is the convention and `tools/sensitivity.py` is
its enforcement.

```
python3 tools/sensitivity.py check                        the gate; in `make verify`
python3 tools/sensitivity.py check --changed-since origin/main    price what this branch moves
python3 tools/sensitivity.py show drum-kit-modes          the whole record, before you argue
```

Today that prints, in four lines, the thing the debate lacked:

```
parameter source                     objective               ships  verdict
MODES    rtl-sketch/drum_kit.v      drum_kit cell area um2     16  MIXED     runs [8, 11-16] at 2%
         -> anything in 11..16 is the same drum_kit cell area to within 2%: arguing inside that range cannot move it
NUMS     rtl-sketch/drum_kit.v      modal_dp cell area um2     11  SENSITIVE runs [6, 11, 16] at 2%
```

## What kind of gate this is, and why that kind

**It is a mechanical check, scoped by a committed registry**, plus the
convention below that says when a parameter enters that registry.

Of the three shapes issue #224 offered — a script that checks a change includes
a sweep artefact, a documented convention with no enforcement, or a lint on a
marker in the PR description — this is the first, with one change: it does not
wait for a diff. The registry-wide assertions run on **every** push, so a
record that goes wrong is caught even when nobody touched the parameter, and
`--changed-since` adds the diff-triggered part on top. A lint on the PR
description was rejected outright: the PR description is prose, and
`docs/failure-modes.md`'s third root cause is that status carried in prose has
the same *shape* as status carried in data.

It is **not a detector of arguments**: an argument is prose in a chat window
and no script can see it. What a script *can* see is the artefact the argument
should have been settled by, so the gate is on that:

| the gate asserts | because |
|---|---|
| the value that **ships** is a point on the grid | a shipped value with no sweep behind it is the state the debate started from |
| the grid **is** the set of points the measurement artefact holds | otherwise a plateau can be manufactured by leaving a point out |
| every recorded point **re-extracts** from that artefact | a number typed into a JSON file is a claim; a number re-derived from `fpga/reports/mode_sweep.txt` is a transcription of a measurement |
| the verdict is **recomputed** from the points under the record's own rule | a stored verdict that disagrees with its own data is a claim that outlived its evidence — `docs/failure-modes.md` mechanism 4 |
| the measured shape matches a **prediction made independently of the measurement** | a sweep checked only against itself establishes repeatability, not correctness. `moog_probe.py`'s "25 dB of separation" was calibrated that way and was window leakage |

The registry is what keeps it satisfiable. A parameter is under the gate
because someone put it there, so the gate can never fail on a PR that has
nothing to do with sweeping — the failure mode that produced three
unsatisfiable gates here in a single day and trained everyone to ignore gates.

## The convention

**A parameter joins `docs/sensitivity/registry.json` the moment someone
proposes changing its value to improve something.** Not when the change lands —
when the proposal is made. That is the whole point: the sweep is what the
argument is instead of, not a thing the argument concludes with.

Adding the entry is the reviewable act. From then on the gate refuses to let
that parameter ship a value its own sweep does not cover.

### Opening a sweep

1. **State the grid first, and commit it.** Values, and a `range_basis` saying
   why those values bracket the plausible range. `tools/sweep_m1a_attack.py` is
   the working precedent: it refuses to render a point while
   `attack-sweep/grid.json` is uncommitted or dirty, because *a grid stated
   after seeing results is not a grid*.
2. **State the decision rule first.** `flat_within_relative` — the fraction
   within which two points are the same value of the objective — plus a `why`
   that derives it from **the granularity of the decision**, not from the
   spread of the data. A threshold fitted to its own measurements makes every
   curve flat.
3. **Sweep one dial.** `held_fixed` names everything pinned while it moved.
   `fpga/scripts/mode_sweep.sh` says it plainly: *every naive sweep moves
   both.* A point measured with two dials moved is not evidence about either,
   and belongs in `limits`, not on the grid.
4. **Predict the shape before you look.** From the RTL, from how the tool maps
   arrays, from the physics — anything derivable without the measured numbers.
   The gate checks the measurement against it. This is the only part of a
   record that can tell you the sweep is *right* rather than merely repeatable.
5. **Run `check`.** The verdict is computed, never written by hand.

### Reading a verdict

| state | meaning | what to do |
|---|---|---|
| `FLAT` | the objective cannot separate any two points on the grid | stop arguing about it; the debate is not about this objective |
| `MIXED` | a staircase — plateaus separated by steps | argue only across a step; inside a plateau there is nothing to win |
| `SENSITIVE` | every adjacent pair separates | the debate is real; now it is a measurement, and it is already done |

`--changed-since <ref>` prices what your branch actually moved:

- `FLAT-MOVE` — the change is inside a plateau. **Not an error.** A flat
  objective does not make a change wrong; it makes *this* objective unable to
  justify it. Say what does. (`MODES` 12 → 16 really did happen, and it was
  right — four more resonators for +0.65 % of area.)
- `PRICED` — the change costs or saves what it says. Quote the number.
- `RED` — the new value is not on the grid. Sweep it before changing it.

## What this does not catch

Written down because a gate whose limits are unstated gets trusted past them.

- **It does not see the argument.** If nobody registers the parameter, nothing
  fires. The registry is a convention with a mechanical consequence, not a
  mechanism that finds its own work.
- **It cannot prove a grid was stated before the results.** It requires
  `stated_before_results` to be explicit, and a `false` to carry a `retrofit`
  block saying so out loud — which both of today's records do, because their
  measurements predate them. The *temporal* proof is per-sweep and lives in the
  instrument, as in `sweep_m1a_attack.py`'s refusal on a dirty grid.
- **It does not re-run the measurement.** It re-extracts from a committed
  artefact. If `fpga/reports/mode_sweep.txt` is regenerated with different
  numbers, the gate goes red (the record no longer transcribes it) — which is
  the intended behaviour, but the gate is not what re-synthesised anything.
- **It says nothing about correctness.** `docs/verification-rules.md` rule 3:
  a cell count is not evidence that the circuit computes anything.
- **It does not subsume the per-feature sweep instruments.**
  `tools/sweep_m1a_attack.py` renders audio, scores it against a reference and
  applies preservation criteria. This gate reads records. Registering a sound
  parameter would mean teaching `sensitivity.py` a second evidence format; it
  has one, `fixed-width-table`, and the record names which it uses.

## The two records that exist

Both are **retrofits** of the case in `docs/failure-modes.md` mechanism 5, and
both say so in the record.

- **`drum-kit-modes.json`** — `MODES` against `drum_kit` cell area, `NUMS` held
  at 6. `MIXED`: a step from 8 to the 9–16 bracket worth **+16.8 %**, then a
  plateau across 11, 12, 14, 16 whose whole spread is **0.67 %**. So "8 versus
  12" was two questions glued together — one real and one that no amount of
  arguing could have answered, because the objective does not vary there.
- **`modal-bank-nums.json`** — `NUMS` against `modal_dp` cell area, `MODES`
  held at 16. `SENSITIVE`: **+6.35 %** and **+7.52 %** between adjacent grid
  points, ~4,190 µm² per numerator slot.

Side by side, that is the finding of mechanism 5 as data rather than as a
sentence: *the dial that was argued about is flat across the range that was
argued about, and the dial nobody looked at is the one the objective responds
to.* `tools/test_sensitivity.py` asserts both from the committed measurements
rather than restating them, so the claim goes red if either moves.

# Verification rules

Short, because there are only five, and they exist because each was learned
the expensive way in this repository — the first three on 2026-09-17, rules 4
and 5 on 2026-09-26 (issue #52).

---

## 1. Start red

**Before an implementation exists, run its harness against a stub with the
right ports and no behaviour, and watch it fail.** Record the result. Only
then write the implementation.

A `verify_*.py` script must support this directly:

```bash
.venv/bin/python rtl-sketch/verify_ladder.py --rtl rtl-sketch/stubs/ladder_dp_stub.v
#   first mismatch at sample 6: model -1, RTL 0, error +1 LSB
#   worst |error| 64290 LSB; error RMS 25961.9 LSB vs signal RMS 25961.9 LSB (+0.0 dB)
#   exit 1
```

Model-first is not enough. This repository was already model-first — a Python
reference model as the specification, RTL compared against it — and still shipped
four separate harnesses that had never been observed to fail:

| what | how it hid |
|---|---|
| the cocotb bit-exact suite | died at `import synth_ref` before running a test; the negative-control job read *any* non-zero exit as "bug caught" and reported four catches in 1.6 s |
| `ladder_dp_t16.v` | its `tanh` index was out of range, yosys marked the datapath don't-care, **every output was X** — and it was quoted at 1,917 cells for three rounds |
| `tb_ladder_n.v` | read 120-bit words from a 128-bit vector file; it could never have passed against anything |
| `voice_dp.v` | bit-exact over 43,200 frames with **zero** injected-bug controls, so nothing had shown the bench could detect a defect |

Every one is the same shape: **a harness nobody had watched fail.** Starting
red catches all four by construction, and costs about fifteen lines of stub.

### The hardware-specific part

In software an unimplemented function raises. In hardware it outputs **X**, and
an X comparison can silently pass depending on how the bench is written — which
is exactly how `ladder_dp_t16` survived. So the red run must distinguish three
outcomes, not two:

- **X** — the design did not elaborate, or synthesis optimised it away
- **wrong value** — it computes, incorrectly
- **right value** — it computes correctly

A bench that reports "mismatch" for all three is fine. A bench that reports
"pass" for X is not a bench. Check this explicitly; do not assume it.

---

## 2. Every bench carries injected-bug controls

A green bench means nothing until each control has been demonstrated to turn it
red, with a mismatch count. Target the things most likely to be silently wrong
rather than the easy ones — in this design that has meant the PolyBLEP square
sign flip (backwards measures 5 dB *worse* than no correction at all), the
envelope release floor (without it a note never ends), and the resonance
compensation lookup (without it the filter dies above 3 kHz).

---

## 3. A cell count is not evidence of correctness

Synthesis and cycle counts establish area and schedule. They say nothing about
whether the circuit computes anything: a netlist whose every output is X has an
area, and it was reported here three times before anyone simulated it.

Quote area only alongside a simulation result, and say which flow produced it —
`klt` allows `*_1` cells and ORFS excludes them by default, which is a 22–42 %
difference on the same RTL.

This is now enforced rather than advised, for one tool: `pnr/report_synth_area.py`
simulates before it answers and REFUSES — withholding the cell count it already
has — when any output is X. Rule 5 records what that refusal cost to make
trustworthy, and why the netlist-side checks cannot do the job.

---

## 4. A multi-property suite reports what it is BLIND to, not just what failed

Rule 2 asks whether a control turns the bench red. That is a yes/no about the
**bench**. It does not say which of the bench's properties actually saw the
defect, and a property that never sees anything is decoration wearing the
costume of coverage.

So: **when a suite measures more than one property, every injected control
prints a properties × defects matrix — MOVED for the properties that saw it,
BLIND for the ones that did not.**

This is not a proposal; it has already caught a hole in a metric that looked
rigorous. `model/sound_report.py --inject ladder-cut30` — a uniform 30 %
cutoff error — put `corner ratio drift` in the BLIND column, because a
**non-uniformity** metric cannot by construction see a **uniform** skew. Both
properties were correct. One of them could never have failed for that defect.

Two suites print the matrix, and they are the only two that can:

| suite | its "properties" | example |
|---|---|---|
| `model/sound_report.py --inject` | named acoustic properties per voice | `sd-centroid-amp-weighted` moves SD brightness 1918 → 5868 Hz and leaves SD's other **five** properties BLIND |
| `rtl-sketch/verify_ctl.py --inject` | the four fields of a register write, plus the write `count` and the `drain` window | `SPI_ADDR7` moves `address` on 105 of 206 writes and the other five are BLIND. `SPI_DATA24` moves `data` on 42 of 206. `SPI_ANYLEN` moves only `count` (208 writes reach the port for 206 sent) and `SPI_DRAIN_LATE` only `drain` (206 of 206 applied at `go`) — each row is printed against its OWN population, so `count` is over the 206 sent while `drain` is over the 208 that arrived |

**Every other `--expect-fail` suite here is single-property by construction and
a matrix would be a table with one column.** `verify_ladder.py`,
`verify_modal.py`, `verify_voice.py`, `verify_synth_top.py`, `verify_drums.py`,
`tools/verify_rate_conv_2x.py` and `fpga/verify_fixture.py` all compare an RTL
sample stream against the Python model **with no tolerance**, sample for
sample. There is exactly one question — "is the stream identical" — so there is
exactly one property, and "which property saw it" has one possible answer.
Those suites already report the thing a matrix would add: the first mismatching
sample, the mismatch count, and the error in LSB.

The test for whether this rule applies is therefore: *does the suite reduce its
comparison to more than one named quantity?* If yes, print the matrix. If it is
one bit-exact stream comparison, do not invent columns to fill.

`verify_ctl.py` is the one that was **not** obvious — its verdict is pass/fail
like the others, but `compare_writes` had already been decomposing the failure
into four per-field counters for its own error message. The matrix was one
function away and nobody had asked for it.

---

## 5. A bug is not closed until it is an injection

**The failure mode of injection testing is that you inject the bugs you already
thought of.** That is `docs/failure-modes.md`'s root cause wearing a lab coat:
validating against our own imagination. An injection suite grown only from
what its authors imagined is as internally consistent, and as ungrounded, as an
estimator calibrated on our own model.

There is one source of defects guaranteed **not** to come from our imagination:
the bugs this project actually made. So when a bug is fixed, the fix is half
the work; the other half is reinstating the exact broken behaviour as a
permanent control that must stay red.

Six already work this way — two at the control layer, two at the measurement
layer, and two in the build/report tools:

| the bug, as it shipped | the injection it became |
|---|---|
| the SPI address truncated to 7 bits | `verify_ctl.py --inject SPI_ADDR7` — the exact broken frame |
| the SPI datum truncated to 24 bits | `verify_ctl.py --inject SPI_DATA24` |
| a 5 ms moving average used as an envelope on a 56 Hz carrier — 0.28 of a cycle | `sound_report.py --inject bd-ma-envelope` |
| an amplitude-weighted centroid read where a power-weighted one belonged | `sound_report.py --inject sd-centroid-amp-weighted` |
| `ladder_dp_t16`'s out-of-range tanh index quoted at **1,917 cells** for three rounds | `pnr/report_synth_area.py --inject TANH_INDEX_OOR --expect refused-x` — the tool REFUSES and withholds the number |
| a die area recovered from its own `{"method": "utilization", "utilization_pct": 50}` | `pnr/orfs/area_provenance.py --inject UTILIZATION_TARGET --expect refused-circular`, and `CORE_UTILIZATION_SET` for the ORFS spelling |

The last two are the measurement layer, which is where most of this project's
errors actually lived, and both were already pinned by a helper-function unit
test before this rule existed. **A unit test on the helper is not the same
control**: it proves the broken function is broken, not that the acceptance
path would have noticed someone using it. Reinstating them through
`sound_report.py` puts them where the verdict is issued.

A third historical measurement bug — the Hann-windowed 700 Hz noise-share
split, wrong by 15× — is kept the other way round, as a method retained
*because it must stay wrong*, in `model/test_drum_fit.py`. That is the same
rule with the sign flipped and is equally valid.

**Not yet injections**: the list is empty. It held two entries — X-propagation
quoted as a 1,917-cell area, and a die area recovered from its own utilization
input — and both became controls under issue #245. It is a debt marker, not
coverage; it shrinks only when an entry becomes a control, and it grows again
the next time a bug is fixed without one.

The two newest are refusal controls rather than comparison controls, which is a
distinction worth keeping straight. The four above ask "does the bench notice a
wrong number?" The two below ask "does the tool decline to produce a number it
cannot stand behind?", so their `--expect` names the *reason* for the refusal
(`refused-x`, `refused-circular`), not merely that one occurred. Without that, a
missing yosys would have made the X control look like it had fired.

**What enforcing the second one immediately found**, and this is the argument
for a check over a comment: `pnr/orfs/ladder_dp/config.mk` and
`pnr/orfs/synth_core/config.mk` both set `CORE_UTILIZATION = 50`, the setting
`synth_top/config.mk`'s own header warns against at length, so
`pnr/orfs/summarize.py` would have quoted a `die / synth cell area` of about 2
for either of them without a word. The convention held exactly where somebody
had written a paragraph about it and nowhere else.

**And what the first one measured is a warning about which checks are worth
anything here.** Under `TANH_INDEX_OOR` — a tanh index field one bit wider than
the table it reads, the `ladder_dp_t16` defect — the synthesised netlist contains
no `x`, simulates `x`-free at the gate level, and its cell count moves by
**0.1 %** (1,677 against 1,679). Yosys is entitled to resolve a don't-care to
anything it likes, and does. Only a behavioural simulation of the sources sees
it: `y` is `x` on 506 of 512 sampled cycles. The area is not a weak detector of
this class of defect, it is not a detector at all.

### The three conditions, because a control that cannot run looks like one that works

A control counts as caught only if **all three** hold, and anything else is
`NO VERDICT` — which is red, and is *not* a fail:

1. the clean run passes;
2. the mutant **builds, activates and actually executes**;
3. the intended assertion is the one that fails.

Condition 2 is not theoretical. The nightly's injection check once swallowed
the exit status with `|| true` and then grepped the output for `MISS`/`FAIL`/
`MOVED` — so **a traceback containing any of those words counted as a caught
defect.** A tool that could not run was indistinguishable from a control that
worked. Separately, this repository has shipped a "negative control" that
mutated a function signature into invalid Python and passed, proving only that
Python rejects syntax errors.

And per `CLAUDE.md`: **run the gate against the current state before committing
it.** An unsatisfiable gate is worse than no gate — it trains everyone to
ignore gates, including the working ones.

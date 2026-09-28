# The `python` job's SIGSEGV of 2026-09-28: what was measured, and what was not

Issue #422. The `python` job of `.github/workflows/rungs.yml` exited **139** —
SIGSEGV, core dumped — inside `python -m pytest model/ spec/ -q`. This file is
the measurement behind the mitigation pinned in that job, and it is also the
record of a crash that **was not reproduced**. A rate of zero is a result; it is
not a repair, and nothing here claims the crash is gone.

## The facts, none of them inferred

| | |
|---|---|
| run | [36377452558](https://github.com/2AMLogic/gf180-parasynth/actions/runs/36377452558/job/108786095160), `push`, head `5dbec422` |
| the same commit, other trigger | 36377455006, `pull_request` — **passed** |
| runner | `blacksmith-4vcpu-ubuntu-2404` |
| stack | Python 3.12.12, numpy 2.5.3, scipy 1.18.1, pytest 9.1.1 (`pip install --upgrade`, unpinned) |
| when | 12 min 42 s into the step, at `[ 30%]` collected |
| where | `model/drums_fx.py:256 in out`, under `test_drum_fit.py::test_separator_matches_the_exact_share_of_our_own_render` |
| native modules loaded | 99 |

The innermost frame is

```python
return self.level >> (ENV_BITS - 15)
```

— a shift of two Python `int`s, which cannot fault. So the crash is of the
interpreter or of a native extension, surfacing at whatever frame was innermost
when the signal arrived.

## What the log alone rules out

**Runaway Python recursion.** The traceback is ~30 frames and runs unbroken from
`Env.out` through `play` / `frame`, the test's `_render_sd`, and on into
`_pytest.python.pytest_pyfunc_call`, `pluggy._callers._multicall` and the rest of
the runner. `faulthandler` truncates a deep stack and says so; this one is
complete and shallow. A Python-level stack overflow would look nothing like it.

**A crash in a BLAS worker thread.** `faulthandler` printed exactly one thread
block, headed `Current thread`, whose stack is the main thread's. The signal was
delivered to the main interpreter thread while it was executing pure-Python
bytecode. This narrows the mechanism considerably: whatever went wrong, the
object graph or the heap under the main thread was already bad by the time a
small integer needed allocating.

**An OOM kill.** A cgroup OOM kill is SIGKILL — 137, not 139 — and the peak RSS
of the same command is 245 MB (below). Memory pressure is not a candidate.

## What was measured

Every number below was produced by code committed with this document:
`tools/crash_rate.py` (crash rate, per-run peak RSS, thread and extension probe)
and `tools/run_with_rss.py` (the whole step's envelope). Both carry controls in
`tools/test_crash_rate.py` and `tools/test_run_with_rss.py`, including a child
that really does `faulthandler._sigsegv()`.
<!-- claim: grep=faulthandler\._sigsegv in=tools/test_crash_rate.py,tools/test_run_with_rss.py -->

### 1. The crash rate is 0 in 120 runs — and that is not "no crashes"

```
tools/crash_rate.py --runs 60
env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 ... tools/crash_rate.py --runs 60
```

| arm | PASS | CRASH | ERROR | runs that reached the code | median | peak RSS |
|---|---|---|---|---|---|---|
| default threading | 60 | 0 | 0 | 60 | 13.26 s | 98 MB |
| thread limits = 1 | 60 | 0 | 0 | 60 | 12.86 s | 98 MB |

Same wheels as the crash (numpy 2.5.3 / scipy 1.18.1 / Python 3.12), 8-vCPU
worker, `ulimit -s` 8192 kB, 25 GB MemAvailable, both arms run concurrently so
they saw one load.

Three things this rate is **not**:

- It is not a measurement of the runner. 8 dedicated vCPU and 25 GB free is not
  `blacksmith-4vcpu-ubuntu-2404`, and the one mechanism the traceback leaves
  standing — heap state going bad under the main thread — is exactly the kind
  that a different core count and a different allocator arena layout change.
- It is not a measurement of the *shape* of the failing run. CI crashed 12m42s
  into **one** long-lived process that had already executed 30 % of `model/` and
  `spec/`. 120 fresh processes running one test each cannot exhibit anything that
  accumulates. Reproducing that shape means re-running the whole suite
  repeatedly, which is hours per sample at a rate of at most one crash in some
  large N — it is the honest next experiment and it did not fit here.
- It is not "could not reproduce, therefore not real". The crash is on the record
  with a core dump and a traceback.

`tools/crash_rate.py` refuses rather than reporting a rate when no run reached
the code under test — a target that cannot be imported exits non-zero without
executing a line, and averaged into a denominator it is indistinguishable from
stability. `REFUSED` is exit 2, distinct from 0.
<!-- claim: test=tools/test_crash_rate.py::test_a_batch_that_never_ran_refuses_instead_of_reporting_zero -->

### 2. The failing path spawns 14 native threads — and the count is not in the commit

`tools/crash_rate.py --probe` renders one SD and runs one `noise_share`, then
counts `/proc/self/task`:

| stack | threads before import | threads after the render | native extensions |
|---|---|---|---|
| pip wheels (numpy 2.5.3 / scipy 1.18.1) — **what CI installs** | 1 | **15** | 75 |
| the same wheels, all five thread vars = 1 — **what is now pinned** | 1 | **1** | 75 |
| the same wheels, `OPENBLAS_NUM_THREADS=1` alone | 1 | **1** | 75 |
| Ubuntu apt numpy 1.26.4 / scipy 1.11.4 | 1 | **1** | 78 |

The first and last rows are the original session's. **Rows 2 and 3 were added on
a second pass, on a different 8-vCPU host, which also re-derived rows 1 and 4
unchanged** (same wheels, same 75 extensions, 1 → 15) — so the finding
replicates across hosts, not just across runs on one.

Rows 2 and 3 exist because the first pass pinned a mitigation without ever
measuring the pinned arm. The table justified the pin by showing the environment
*varies*; it did not show that the pin *removes the variation*. That is the
repository's own second root cause — a precondition assumed rather than asserted
— committed into the fix for a bug about assumed preconditions. It now measures:
the pin takes the path from 15 threads to 1, and `OPENBLAS_NUM_THREADS` alone
accounts for every one of the 14. The other four are carried anyway because they
cost nothing and a future wheel resolution can ship MKL or a second OpenMP
runtime; `--require-thread-pins` therefore requires all five, so dropping one is
a refusal rather than a silent narrowing.

This is the finding that justifies the mitigation, and it is also this
investigation's **wrong-then-right**: the first probe ran under the host's apt
numpy, reported one thread, and pointed straight at "no threading is involved
here". It was re-run against a venv built the way the workflow builds it —
`pip install numpy scipy pytest pyyaml`, resolving to the same versions the
crashing run installed — and the answer changed from 1 to 15. A probe of the
wrong interpreter is a correct instrument in a wrong state, which is the whole
subject of `CLAUDE.md`'s second root cause.

The consequence is not "threads caused it". It is that **the number of native
threads in this job is currently a function of the runner tier and of whatever
pip resolved that morning**, so two runs of the same commit do not have the same
numeric environment — which is precisely the situation in which one run crashes
and the other does not, and precisely the situation in which no future crash rate
is comparable to any other.

### 3. The whole step's envelope, which the crashing job never recorded

`tools/run_with_rss.py -- python -m pytest model/ spec/ -q`, 8-vCPU worker:

| | default threading | thread limits = 1 |
|---|---|---|
| peak RSS | 245 MB | 236 MB |
| wall | 21m 07s | 19m 58s |
| exit | 0 | 0 |

Both arms ran concurrently, so the wall times are under mutual contention and
are not a clean per-arm cost; the paired 60+60 short runs in §1 are the cost
measurement. What matters here is the magnitude of the RSS: a few hundred MB.

## What could not be established, stated as such

- **The runner's own `ulimit -s` and MemTotal are still unknown.** They were
  never recorded, which is the defect `tools/run_with_rss.py` exists to close.
  The next run of this job prints both. Until then the stack-limit question is
  answered only by the traceback's shallowness, not by a number.
- **No root cause.** The evidence is consistent with a native extension
  corrupting heap state that the main thread later trips over, and with a fault
  in the microVM's memory. Nothing distinguishes them without a core dump, and
  the core was not preserved.
- **Whether the mitigation helps.** Unknowable from a zero-crash baseline. The
  other jobs in `rungs.yml` are deliberately left unpinned so that a recurrence
  there and not here is evidence, and a recurrence in both is evidence against.

## What changed in the workflow, and why each piece

1. **`env:` pinning `OPENBLAS_NUM_THREADS` / `OMP_NUM_THREADS` /
   `MKL_NUM_THREADS` / `NUMEXPR_NUM_THREADS` / `VECLIB_MAXIMUM_THREADS` to 1** on
   the `python` job. Justified by §2 (the environment is otherwise not a property
   of the commit) and paid for by §1 (3 % faster, not slower — the fits are over
   a few hundred samples, where starting a pool costs more than it returns).
2. **`tools/run_with_rss.py` in front of the crashing step.** Justified by the
   first two questions #422 asked being unanswerable from the log. It inherits
   the child's streams and re-raises the exit status, so a SIGSEGV still exits
   139 through it; that is asserted, not asserted-in-prose.
   <!-- claim: test=tools/test_run_with_rss.py::test_a_segfaulting_child_still_exits_139 -->
3. **Both instruments' controls named as a CI step, before the step they
   guard.** A status-propagating wrapper that nothing exercises is a false green
   waiting to happen, and `pytest model/ spec/` does not collect `tools/`.
4. **`--require-thread-pins` on that same step**, so the mitigation asserts its
   own precondition at the point of use. Justified by §2 rows 1–2: pinned and
   unpinned differ by 14 OS threads and by nothing visible in the log. A
   job-level `env:` block can stop being in force without the step that depends
   on it changing a character — a renamed key, a step-level `env:` shadowing it,
   the job split in two — and every crash rate measured afterwards would be
   incomparable with no signal that anything had changed. It exits **126**
   without running the suite; 126 and 127 are used because no pytest run
   produces either, so a refusal can never be read as an interrupted suite (2)
   or a pass.
   <!-- claim: test=tools/test_run_with_rss.py::test_require_thread_pins_refuses_when_the_pin_is_not_in_force -->

   Both directions were run against the **committed** workflow before the gate
   was committed — the job's `env:` block and the step's `run:` string parsed
   out of `rungs.yml` and executed, passing with them and refusing with them
   removed — because an unsatisfiable gate is worse than no gate. And the
   property the wrapper exists for survives the addition: with the pin in force,
   a child that segfaults still exits 139, not 126 and not 0.
   <!-- claim: test=tools/test_run_with_rss.py::test_the_pin_check_never_launders_a_segfault -->

## The blast-radius question (#422 item 3), and its answer

The crash killed the whole process, so the 70 % of `model/` and `spec/` after it
was never collected. In this job that reads as a failure, correctly. The question
was whether any job could reach the same state and still report green.

**One can. It is filed as #438**, separately, per #422's own instruction not to
fold it in: `rungs.yml`'s and `dag.yml`'s

```
python tools/compile_dag.py --run || true
python tools/compile_dag.py --check
```

`--run` executes every fast node's suite as a subprocess, and `|| true` discards
its status by design ("a RED node is a result"). But `tools/compile_dag.py`
returns non-zero for a RED or STALE node **only under `--strict`**, which neither
call passes — so a node whose suite segfaults is recorded RED, printed to stderr,
and the step exits 0. The inline comment above that step in `rungs.yml` states
the opposite ("`--check` then fails … if any node is RED"), which is a comment
asserting an enforcement no job performs.
<!-- claim: grep="args\.check and args\.strict and bad" in=tools/compile_dag.py -->

The same suites also run under the un-tolerated `everything else` step, so this
is a second line of defence that is missing rather than a hole with nothing
behind it. It is still a gate that does not gate.

## Re-running any of this

```bash
uv venv --python 3.12 /tmp/ci-venv                       # the workflow's stack
uv pip install --python /tmp/ci-venv/bin/python numpy scipy pytest pyyaml
/tmp/ci-venv/bin/python tools/crash_rate.py --probe      # §2 row 1: 15 threads
/tmp/ci-venv/bin/python tools/crash_rate.py --runs 60    # the rate

env OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    /tmp/ci-venv/bin/python tools/crash_rate.py --probe   # §2 row 2: 1 thread

/tmp/ci-venv/bin/python tools/run_with_rss.py -- \
    /tmp/ci-venv/bin/python -m pytest model/ spec/ -q    # the envelope
```

`--probe` under the host's own `python3` will report one thread and mislead you,
as it misled this investigation. Build the venv.

## The wrong-then-right rate for this investigation

Two of the measurements here were wrong before they were right, and neither was
caught by reading the code:

1. **The thread probe reported 1 and meant 15.** Run under the host's apt numpy
   rather than the venv the workflow builds. It pointed at "threading is not
   involved", which would have ended the investigation.
2. **The mitigation was pinned without the pinned arm being measured** (§2 rows
   2–3, added on the second pass). The table showed the environment varies; it
   never showed the pin removes the variation. It does — but that was a
   *conclusion presented as a measurement* until it was one.

Both are the same failure in different clothes: a correct instrument in an
unchecked state. Publishing the rate is what lets a reader calibrate the numbers
above, per `CLAUDE.md`.

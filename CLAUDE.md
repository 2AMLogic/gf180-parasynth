# Working in this repository

Read `docs/verification-rules.md` first — start red, carry injected-bug
controls, a cell count is not evidence of correctness. Then read
`docs/failure-modes.md`, which root-causes why this project keeps producing
confident wrong answers: **internal consistency is cheap to check and external
grounding is expensive, so work drifts toward the cheap check — and the cheap
check feels like rigour because it is rigorous in form.**

Four consequences that will bite you specifically:

- **An estimator calibrated on our own model is not validated.** It needs a
  signal whose answer is known independently of the thing being measured. A
  probe calibrated the other way reported 25 dB of separation that turned out
  to be window leakage.
- **A suite that only tests against our own decision records cannot tell you
  the model is right.** The voice has 42 such tests and, until today, zero
  external references.
- **Check that the thing you are testing is the thing that ships.** Every bench
  drove the register write port rather than the link, so the control path
  delivered 37 of 155 writes with every block still bit-exact.
- **Sweep a parameter before arguing about it.** Hours went into 8 modes versus
  12; yosys pads the bank to a power of two, so 9 through 16 cost identically,
  and the variable that mattered was `NUMS`.

**And a second root cause, from the measurement apparatus rather than the
evidence: preconditions assumed rather than asserted.** Every one of these was
a correct instrument in a wrong state — an unlicensed Diva inserting clicks for
hours, a Model D rendering exact silence, Surge renaming parameter 265 from
"Unison Voices" to "High Cut" by oscillator type, Mini V3 defaulting to a
sub-audio octave, all three plugins appearing to step at 94 Hz because that was
the host's block rate.

- **Assert your apparatus's preconditions at the point of use, and REFUSE
  rather than report when they fail.** `REFUSED` is a first-class outcome,
  distinct from pass and fail. A tool that answers when it cannot is worse than
  one that is absent, because its output looks exactly like data.
- **Run a gate against the current state before committing it.** Three
  unsatisfiable gates were written here in one day. An unsatisfiable gate is
  worse than no gate: it trains everyone to ignore gates, including the ones
  that work.
- **Publish your wrong-then-right rate** where the numbers are read. One
  session produced five measurements that were wrong before they were right,
  all caught by controls rather than inspection. That rate is how a reader
  calibrates any single figure.

This file is about how to work, not what to build.

## Why this block exists: it is a canary for the tools

**The instrument is the payload. Exercising the toolchain is the point.**

This is a 2AM Logic canary block, and what it is a canary *for* is
**`2AMLogic/klayout-tools`** ("tools for AI agents to work with IC layout") and
the open-source EDA flow underneath it. A deliberately awkward design -- a
time-shared datapath, a modal bank, sixteen drum voices, an SPI link -- finds
tool defects that a two-transistor test case never will.

**So when a tool fails, needs a workaround, or silently does the wrong thing,
FILING IT UPSTREAM IS A DELIVERABLE, not a distraction.** It is arguably the
more valuable half: the instrument helps one project, a fixed tool helps every
project that follows.

What we have already found and had NOT filed until someone asked:

- **`klt synthesize` emits netlists its own place-and-route cannot consume** --
  three separate patches were needed to route `ladder_dp`, each surfacing as an
  opaque error from a *different* tool (`STA-0171`, `DRT-0305`) with nothing
  pointing back at the netlist klt produced. Now klayout-tools#2085.
- **Without a power block, klt places no tapcells, no PDN and no fillers, and
  does not say so.** The flow completes and yields a plausible-looking layout
  with no power delivery. Now klayout-tools#2086.

Both sat in `pnr/klt/ladder_dp/run-klt.sh` as comments for weeks. The work of
discovering them was done; only the filing was missing.

**The rule.** If you work around a tool rather than a bug in our design, the
workaround is evidence and it goes upstream -- `2AMLogic/klayout-tools` for
layout and flow, `rjwalters/loom` for agent orchestration. Record the exact
version, the exact error, and the patch you applied. A workaround that lives
only in a shell script is a finding nobody else can use.

## Write Python, not bash

**Anything with logic goes in Python.** Bash is for a single command with no
branching, no arithmetic and no error handling. This is not style: the bash
version of `tools/run_all.py` printed **`FAIL(??)` for a job that exited 1**,
because `eval "cmd; exit 1"` exits the subshell before the wrapper can record
the status — *an unknown rendered in the place where a result belongs.*

The same session produced `exit=$?` after a pipe (capturing `tail`'s status,
not the command's) **three separate times**, each one reporting success for a
command that had failed.

Python's `subprocess.run` cannot do either. Everything else here is Python and
is tested; tooling should be too.

## Waiting is the expensive part, not the work

Simulation here is slow: a bit-exact voice run is 255,060 frames and takes
~20 minutes under iverilog; gate-level drum runs take an hour. You will be
waiting a lot. **How you wait dominates the cost of the session.**

The thing to understand: a check is not cheap just because the command is
cheap. Every time you wake to look at a job you re-process your whole context.
Two hours in that is several hundred thousand tokens, so `ls build/` costs the
same as a hard reasoning turn. Twenty polls is twenty full-context passes with
no work in them.

Five of six agents on this repository burned a large fraction of their budget
in polling loops after their work was already finished on disk. That is the
single biggest avoidable cost here.

### The measurement, so this is not an opinion

One session was audited. **Eighty-nine agent wake-ups produced nothing but
"still running" or "ending my turn"** — one agent alone did **twenty-four**.

Each wake reprocesses the agent's context, which is wasteful. What it *costs*
depends on caching and the provider, so measure usage separately rather than
quoting a token figure per wake — but eighty-nine empty turns is a large number
whatever the multiplier, and it is almost certainly the biggest avoidable cost
here. The cause is always the same: **serial jobs.** Fire one, wake, fire the
next, wake.

### Do this

**`make verify`.** One target, one turn, every fast check in parallel. There is
deliberately no documented way to run *some* of them — the choice was removed
because the prohibition did not work: two more empty wakes happened within five
minutes of the rule being written down.

```
make verify        every fast check
make verify-full   adds the hour-long runs
make controls      every injected defect that must turn something red
```

**For anything else, batch known-independent work** with `tools/run_all.py`.
It reports each job's own exit status, distinguishes a timeout as `NO-VERDICT`
from a failure, and kills the process group so a shelled-out job's children do
not survive. It has its own tests (`tools/test_run_all.py`) because a runner's
only failure mode that matters is a false green.

**Not everything, and this matters:** generating vectors before simulating
them, or reading a failure before choosing the next diagnostic, is legitimate
sequential work. The objective is fewer **empty** turns, not unconditional
parallelism. And one batched call cannot *guarantee* one turn — the platform
may background a long call regardless. It removes the self-inflicted serial
wakes, which are the ones we control.

**If you are waiting on something external**, block in one command and chain
the follow-up into the same call, so the result is already analysed when you
wake.

**Chain the follow-up work into the same command.** Do not return to the model
between running a thing and reading its result:

```bash
.venv/bin/python rtl-sketch/verify_voice.py --set full > /tmp/v.log 2>&1 \
  && grep -E "PASS|mismatch" /tmp/v.log
```

**End your turn.** You are re-invoked when a background job completes. Firing a
job and stopping costs nothing while it runs. Firing a job and polling costs a
full turn per check.

**Use `Monitor` for progress you actually need to see** — it streams stdout
lines as events without a turn each. Filter to the lines you would act on,
including failures, not just the success marker.

### Do not do this

- launch several background jobs and then loop checking all of them
- `sleep`, check, `sleep`, check
- re-run a long job to see whether it still passes when you have not changed
  anything it depends on
- report "waiting on the run" as a turn. If the work is done and only a report
  is missing, write the report
- **wake to say you are still waiting.** That turn costs a full context pass and
  tells the coordinator nothing it did not already know
- **start a second job after the first finishes**, if you knew you needed both

### Two more things that cost an hour each

**Use the quick set while iterating.** `verify_voice --set full` is 383,460
frames and about twenty minutes. Running it two or three times during
development is an hour of wall clock proving nothing the quick set does not.
`make verify` uses the quick set; `make verify-full` is for once, before the PR.

**One deliverable per agent.** Runs hit two hours because briefs contain "and",
several times over — one agent delivered a reference document, three decision
records and four experiments it measured and correctly did not ship. The test
is whether the pieces are *independently verifiable*, not whether they are
separately describable: `fcr` changes the cutoff mapping so everything measured
after it must be re-baselined, and sequencing that inside one agent was right.

### Heavy work runs on the build box, not the developer's laptop

**`make verify`, `make verify-full`, `make reference-integration`, RTL
simulations (iverilog/vvp), render or parameter sweeps, and whole-suite pytest
runs go on the repo's pinned AWS build box.** The laptop is for editing,
reading and single focused test files. On 2026-09-26 two agents ran `make
verify` and an 8-worker probe render on the developer's MacBook at load
average 70–100 for over an hour; the probe timed out and neither produced a
verdict. The same work on the box has 8 dedicated cores.

- **The box** is this repo's pinned instance (`REPO_REMOTE_INSTANCE_ID` in the
  gitignored `.env`; ssh alias `repo-remote-gf180-parasynth`). Start and stop
  it **only** through `.claude/skills/repo/scripts/repo-remote.sh up --yes
  --json aws` / `down --yes` — see `~/.config/repo/README.md` for the rules
  (≤ 8 vCPU, no raw `aws ec2 run-instances`, never `Fleet=loom` hosts).
  **Stop it when the queued work is done.**
- **Match CI, not your laptop:** Python 3.12 (`uv venv --python 3.12`, then
  `numpy scipy pytest`, exactly what the workflows install) and the pinned
  toolchain from `tools/setup_ci_oss_cad.py` (run with `GITHUB_PATH` set to a
  file; it prints the bin dir to prepend to `PATH`). Install `make` if absent.
  Run make with `PY=<that venv's python>`.
- **Ship code as a `git bundle`** of the branches and clone it on the box. A
  worktree's `.git` is a pointer file, and the provenance tooling needs real
  commits and dirty flags.
- **References:** copy `~/dev/refs/` (the Fischer TR-808 corpus and its
  manifest, `GF180_TR808_REFS`) to the same path on the box; the clap probe
  checks that manifest path and refuses without it.
- **Coordinators run the box; subagent briefs say so.** A subagent that needs
  a heavy run commits its branch and asks for it; it does not start the run
  locally "just this once".

The same waiting rules apply: launch the job with `nohup`, then block in one
command that reads its exit code and chains the follow-up analysis.

### If you are stopping because you are blocked

Say what you are blocked on and end the turn. Do not spin. The coordinator can
read your worktree directly, and has had to several times — every agent whose
work was harvested that way had already finished it.

## Your instrument is a deliverable, not scaffolding

**Commit the code that produced your numbers, to your branch, as you go.**
Not at the end, and not into the scratchpad.

This is not tidiness. An agent here spent a long investigation on the 808 hat
and cymbal high band, refuted its own hypothesis at the first step -- the best
outcome an experiment can have -- and committed nothing. Its branch had zero
commits and its six probe scripts sat in a session scratchpad. The findings
survived only because they were transcribed by hand into a docstring. The
scripts were recovered, but by luck: the files happened to still be on disk.

**The line between an intermediate and a deliverable.** The scratchpad is for
render dumps and throwaway one-liners. The thing that *measured* something goes
in `tools/` or `model/`, on your branch, with its validation cases. A number
without the code that produced it is a claim, not evidence -- the same standard
`tools/run_case.py` already enforces on every result through provenance.

The most valuable file in that rescue was `hh_probe4.py`, whose whole job was to
record that an earlier result of 7.2 was actually 30.6 because an emulator had
been used for a knob it was never validated for. **A record of a result that
looked good and was wrong is worth more than one that was right first time**,
and it is exactly the file that gets deleted.

A worktree is not durable storage either. Commit early; you can always rebase.

## Worktrees

Several agents work here at once. Use your own:

```bash
git worktree add /tmp/wt-<yourtask> -b <branch> main
```

Do not commit in the main checkout, and do not edit files another agent owns —
your brief says which are yours. Expect to merge.

<!-- BEGIN LOOM ORCHESTRATION -->
This repository uses [Loom](https://github.com/rjwalters/loom) for AI-powered development orchestration — see the Loom repository for the full guide (roles, labels, worktrees, configuration). When installed, Loom also writes a locally-substituted copy of that guide to `.loom/CLAUDE.md`.
<!-- END LOOM ORCHESTRATION -->

<!-- BEGIN LOOM ORCHESTRATION (AGENTS) -->
This repository uses [Loom](https://github.com/rjwalters/loom) for AI-powered development orchestration (dual-runtime: Claude Code reads `CLAUDE.md`; OpenAI Codex CLI and other AGENTS.md-aware runtimes read this file). See the Loom repository for the full guide (roles, labels, worktrees, configuration). When installed, Loom also writes a locally-substituted copy of the runtime-neutral guide to `.loom/AGENTS.md`.
<!-- END LOOM ORCHESTRATION (AGENTS) -->

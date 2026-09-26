# Trials: why we went back and forth, and how to stop

2026-09-26. A root-cause analysis of the 2026-09-24/26 sessions (PRs #209–#261),
and a proposal to make progress autonomous by turning goals into **trials**:
executable, pass/fail/no-verdict checks that any agent can run, pick up and
flip.

## 1. What actually happened

Two and a half days, roughly 20 merged PRs, and a coordinator relaying an
external reviewer's comments to agents. Most PRs needed **two to four rounds**.
The rounds were not about hard problems. They were about things that could
have been known before the PR was opened:

| PR | Round-trip cause | Could a check have caught it first? |
|---|---|---|
| #248 | cost model `explained: false` in its own reports; I²S verdict PASSed with 0 periods compared | yes — run the claim against the committed evidence; test empty/truncated input |
| #255 | a test file never run before push (KeyError); 4 failures found on the build box | yes — the PR's own tests |
| #228 | "four-pole" fixture was a two-pole | yes — a fixture checked against its analytic formula |
| #241 | NaN / ±inf certified as PASS | yes — non-finite inputs in the test set |
| #226, #231, #232, #253 | claims worded stronger than the evidence | partly — a claims checklist tied to the evidence file |
| #253, #261, #255 | CI refused: branch inputs differ from `origin/main` | yes — update from main before the final gate |
| #255 (box) | `make`, `pyyaml`, `pyserial`, `origin/main`, refs path missing | yes — one environment bootstrap, used everywhere |
| local runs | `make verify` + 8-worker render on the laptop at load 70–100, no verdict | yes — heavy work has one home (the build box) |

Separately, four measurements were used before they were qualified and later
had to be walked back: M1A attack (two-sided domain), clap burst timing (noisy
envelope), cowbell "unwanted" metric (direction), filter rolloff (estimator
version). Each cost a full round.

## 2. Root causes

**R1. "Done" is defined after the work, by a reviewer.** Agents are briefed
with prose; acceptance is decided when a reviewer reads the PR. Every
reviewer finding becomes a new round. The fix is not better reviewing — it is
writing acceptance down as an executable check *before* the work, so the
agent can see it fail, make it pass, and show the receipt.

**R2. Verification happens late, in whichever environment is nearest.** The
laptop, the build box and CI each had different Python packages, tools, refs
and branch bases. Main moved 180 commits in 2.5 days under several writers,
so any branch older than a few hours fails the stale-input guard — correctly —
at the last step. There is no single reproducible "run the gate" entry point.

**R3. A human relay is the scheduler.** Work advanced only when the reviewer's
text was pasted to the coordinator and forwarded to an agent. Plans lived as
sandbox files outside the repo and were sometimes pasted twice. Loom's own
workflow (Curator → Builder → Judge → Champion) was not used for this work, so
nothing moved without a person.

**R4. Measurements are consumed before they are qualified.** #115 (estimators
declare their domain and refuse outside it) has been open since 09-18. It was
re-derived by hand three times this week.

**R5. Lessons are written as essays, not mechanisms.** Six "Root cause" issues
from 09-18 (#71, #104, #117, #123, #135, #155, #163) are still open; the same
failure modes recurred. An RCA that does not become a gate or a check does not
change behaviour.

**R6. The backlog does not describe the work.** 65 open issues; ~40 carry no
Loom label, so Loom never picks them up; several are stale (#169 fixed by
#178, #206 by #209/#212, #208's "not delivered", #79/#85 superseded plans).
The real priorities lived in `plan0NN.md` files instead.

## 3. The fix: trials

A **trial** is one command that answers one product question with PASS, FAIL
or NO VERDICT, leaves a receipt (inputs' hashes, exit status, output), and
runs the same way on the build box and in CI. It is the executable form of a
milestone. It generalises what `docs/capability-dag.md` already does for
implementation nodes, and adds the product nodes that DAG does not have.

Rules (amended after review, plan085 §3):

1. **Every executable work issue names its trial(s) and expected outcome.**
   Legitimate outcomes: valid FAIL → PASS; NO VERDICT → valid FAIL (coverage);
   preserved PASS (refactor, setup, docs); a qualified negative experiment.
   A sound change names ONE target property and an explicit preservation set.
   An issue with no executable acceptance contract stays in triage.
2. **A meaningful baseline before implementation.** Prefer a committed
   reproducer before the fix; a separate preliminary PR is optional. "Red"
   counts only if the checker ran and detected the intended condition — an
   import error or missing asset is not a caught defect. For a new capability
   an honest initial NO VERDICT is fine; for a working capability wrapped as a
   trial, keep its PASS and show the wrapper rejects a relevant counterexample.
3. **Judge reviews contract, implementation and evidence** — does the trial
   ask the right question, is the apparatus independently exercised, is the
   evidence complete and bound to the candidate, and does the implementation
   meet the contract. A receipt alone can certify a faithfully reproduced
   wrong formula. Criterion/tolerance changes are versioned and never
   reported as sound improvements.
4. **Three product verdicts, separate from execution status.** PASS: complete
   qualified evidence meets the criterion. FAIL: complete qualified evidence
   violates it. NO VERDICT: evidence absent, corrupt, incomplete, unqualified,
   or no measurement established. A process exit code is not the verdict. A
   control counts only when its child reaches the intended meaningful failure
   (not an import error, timeout or empty comparison). Not-run / queued /
   operator-blocked are workflow states, not results. A composite PASSes only
   when every required child is valid and passing; a conclusive required-child
   FAIL is FAIL; otherwise NO VERDICT, with coverage shown.
5. **Close the investigation, not the goal.** A negative experiment closes
   "evaluate approach X", never the product requirement it served. A
   root-cause issue closes as covered only when a MERGED check exercises its
   failure mode — a planned trial is not closure evidence.
6. **Freeze experiment inputs; qualify integration separately.** At dispatch
   record base commit, candidate, configuration, references, tools, analyser
   and criteria; the result stays valid for those inputs after main moves.
   At merge, validate the integration head and re-run only trials whose
   dependency fingerprints changed. The current stale-input guard stays on;
   narrowing it is a separate, tested change (unrelated upstream change,
   changed reference/analyser/ROM, intentional DUT change, changed merge
   result, corrupted or foreign evidence).
7. **Thin dispatcher, no second source of truth.** `make trial T=<id>` wraps
   existing checkers via `tools/run_all.py`, `tools/manifest.py`,
   `tools/provenance.py` and `docs/dag.json`; `docs/trials.json` owns only the
   product question, scope, required children, criterion version, preservation
   set and resource class, and references DAG nodes rather than copying their
   commands. Results are generated from validated receipts; artifacts are
   retained, not just hashed. No service, database or new scheduler.

### Proposed trial set (product first)

| ID | Question | State today | Verifier (existing where possible) |
|---|---|---|---|
| T-RELEASE-BOUND | Do image, sources, host bytes, supported domain and evidence agree? | not yet run as a trial (#255 draft) | `fpga/release/release_manifest.py` + release tests |
| T-PLAY-DIGITAL | Do the documented playback commands produce correct, non-silent audio through UART→RTL→I²S? | partial (held note was silent until #255) | release held-note + `verify_rolling_playback.py --rtl` |
| T-DEADLINE | Does every supported configuration meet the frame deadline, with complete evidence? | PASS baseline; pulse2x excluded | `rtl-sketch/verify_deadline.py` |
| T-PULSE2X-IMAGE | Is a PULSE2X=1 image qualified (fit, timing, deadline, I²S)? | FAIL | #205 |
| T-CLAP-L2 | Is D12A's late energy repaired in the shipping model and RTL? | in progress (#261 → D) | run_case D12A + model→RTL→I²S |
| T-LIVE-MIDI | Does a MIDI controller drive the image with bounded latency and no stuck notes? | not started | new; sim first |
| T-PHYSICAL | Does the board's line output match the digital prediction (gain, latency, noise, repeatability)? | operator-only (rig) | new capture analysis; #208 |
| T-MEASURE-QUAL-* | A FAMILY of bounded tasks, one estimator each, starting with one qualified measurement and the known-unqualified clap timing; not a prerequisite for shipping L2 | NO VERDICT (#115) | known-answer suite per estimator |
| T-BOARD-INTEGRITY | Is the scorecard internally consistent and bound to its records? | PASS | `tools/scorecard.py --check` |
| T-BOARD-COVERAGE | How many cases have a valid measurement? (a count, not a pass) | 20 / 100 | scorecard |
| T-SOUND-* | Per case/property: does qualified evidence meet tolerance? (nine case passes is not a passing instrument) | 9 case passes | run_case per property |

Sound work (M1A harmonic, cowbell partial balance, BD attack, aliasing #61,
DC blocking #152/#165) becomes **one trial per property to flip**, each issue
naming the case/property and its preservation set.

### Operating model

- **Autonomous loop:** Loom Curator turns each open issue into
  `trial + expected transition + owned files + stop rule`; Builder claims
  `loom:issue` work, runs heavy steps on the build box (CLAUDE.md), opens a PR
  with the receipt; Judge checks the trial and receipt; Champion merges.
- **Operator:** only `loom:operator-decision` (product trade-offs such as the
  clap's shape/energy choice) and `loom:operator-only` (hardware: T-PHYSICAL,
  flashing, rig). The external reviewer's role becomes auditing trials and
  receipts, not steering each PR.
- **Coordinator budget:** at most two implementation owners at once, one
  heavy workload on the box at a time (CLAUDE.md).

## 4. Pilot, not migration

1. **Backlog first:** a read-only curation proposal (every open issue:
   disposition, exact existing Loom labels, trial/parent goal, evidence for any
   closure, prerequisites, expected transition, stop rule) reviewed by the
   operator before any bulk label or close.
2. **Three pilot trials only**, wrapping existing checkers: T-RELEASE-BOUND,
   T-DEADLINE (baseline), T-PLAY-DIGITAL. One shared bootstrap specification
   for the build box and CI, with cheap dependency/tool/reference checks
   before long jobs.
3. **Prove the dispatcher's failure behaviour:** missing assets, empty or
   truncated comparisons, a genuine DUT defect, timeout and altered evidence
   each get the correct verdict.
4. **Run one issue through the real Loom loop** (Curator → Builder → Judge →
   Champion) with no human message relay.
5. **Measure whether it helped** over the next few items: review rounds from
   missed declared checks, human relay interventions, reruns caused only by
   provenance, setup failures found after expensive work began, time from
   implementation to qualified result. Not PR count or trial count.

## 5. Running a trial (pilot, implemented)

From a fresh checkout, on the build box or in CI (the laptop is not a
supported environment; its Python and Icarus differ from the spec):

```
python3 tools/trial_env.py bootstrap --venv ~/work/trials-venv   # box; CI omits --venv
~/work/trials-venv/bin/python tools/trial.py run T-DEADLINE      # or: make trial T=T-DEADLINE PY=...
~/work/trials-venv/bin/python tools/trial.py check-receipt build/trials/T-DEADLINE/<run>/receipt.json
```

`bootstrap` installs `spec/trial-environment.json` (Python 3.12, pinned
packages, oss-cad-suite via `tools/setup_ci_oss_cad.py`) and is a no-op when it
is already satisfied. `tools/trial.py` refuses with NO VERDICT, before any child
starts, when the environment, a checker, or a required asset is absent.
`docs/trials.json` is the registry; `.github/workflows/trials.yml` runs the
same bootstrap and gates on T-DEADLINE. Receipts live under `build/trials/` and
are uploaded by CI as `trial-receipts`; `tools/trial.py compare A B` checks two
environments reached the same numbers from the same inputs.

### Pilot receipts, 2026-09-26 (a snapshot, not maintained state)

`docs/trials/pilot-2026-09-26.tgz` holds every receipt bundle the pilot
produced on the build box. At the time, `python3 tools/trial.py check-all
pilot` reported 12/12 valid. They are `trial-receipt/1` receipts, and the
current checker **rejects** them. A /1 receipt does not record each child's
interpreter, so a child's verdict cannot be re-derived from its evidence. That
was the defect: a re-sealed receipt with a FAIL child flipped to PASS was
reported valid (review of 4eb4e72). The table below is what the pilot printed.
It is kept as a record, not as receipts the current checker accepts. `main-c50abf9/` is this branch's own tree;
`cand255-5a8f87d/` is this branch merged locally with open PR #255's head
`b2ccc12` (never pushed), to show what the two #255-dependent trials return
once its manifest and held-note checker land. CI's receipts for the same
commit are the `trial-receipts` artifact of `.github/workflows/trials.yml`;
`tools/trial.py compare` reports AGREE between CI and the box for both
T-DEADLINE modes.

| trial | tree | verdict | what it shows |
|---|---|---|---|
| T-RELEASE-BOUND | main | NO VERDICT (preflight) | manifest and `release_manifest.py` absent until #255 |
| T-RELEASE-BOUND | +#255 | ~~PASS~~ NO VERDICT | manifest BOUND; Arty evidence binding BOUND. It was reported PASS with **no control declared**, a vacuous pass. The composite now refuses that. It stays NO VERDICT until a STALE counterexample control exists (after #255) |
| T-DEADLINE reanalyse | main | PASS | retained traces: slack 14, 3300/3300 I2S periods; late160 control caught |
| T-DEADLINE sim | main | PASS | this tree's RTL: slack 13 (one cycle less than the published image's 14), 3300/3300 periods; control caught |
| T-DEADLINE sim, candidate late160 | main | FAIL | 3496 missed frames, overrun: the deadline reason |
| T-DEADLINE sim, 45 s budget | main | NO VERDICT (timeout) | no PASS left behind |
| T-DEADLINE sim, SIGTERM at 60 s | main | NO VERDICT (cancelled) | children killed, none surviving |
| T-DEADLINE reanalyse, truncated trace | main | NO VERDICT | staging refused the altered trace before the checker ran |
| T-PLAY-DIGITAL | main | NO VERDICT (preflight) | `held_note_audible.py` absent until #255 |
| T-PLAY-DIGITAL | +#255 | PASS | three held notes audible (peaks 12760/7345/10376 LSB) and bit-exact; demo phrase 257,185 I2S periods, 508/508 writes, 0 mismatches; silent-image control caught |

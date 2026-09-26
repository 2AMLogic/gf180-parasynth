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

Rules:

1. **Every Loom work issue names the trial it flips** (or the new trial it
   adds) and its expected state change, e.g. `T-PLAY-DIGITAL: FAIL → PASS`.
   An issue without a trial is triage, not work.
2. **The trial is written and committed red first**, before the
   implementation, in its own small PR if needed. Acceptance changes are
   trial changes, reviewed as such — never re-negotiated in prose.
3. **A PR shows the trial transition with a receipt** from the build box or
   CI. Judge reviews the trial and the receipt, not the narrative.
4. **An unqualified measurement is NO VERDICT, never PASS** (#115, #134).
   Qualifying it is its own trial.
5. **A negative result closes the issue** with its evidence; it does not open
   a follow-up investigation automatically.
6. **One entry point**: `make trial T=<id>` runs a trial on the current tree
   after refusing if the branch is behind `origin/main` on any input it
   hashes; `tools/box_bootstrap.py` makes the build box match CI.

### Proposed trial set (product first)

| ID | Question | State today | Verifier (existing where possible) |
|---|---|---|---|
| T-RELEASE-BOUND | Do image, sources, host bytes, supported domain and evidence agree? | ~PASS (#255 draft) | `fpga/release/release_manifest.py` + release tests |
| T-PLAY-DIGITAL | Do the documented playback commands produce correct, non-silent audio through UART→RTL→I²S? | partial (held note was silent until #255) | release held-note + `verify_rolling_playback.py --rtl` |
| T-DEADLINE | Does every supported configuration meet the frame deadline, with complete evidence? | PASS baseline; pulse2x excluded | `rtl-sketch/verify_deadline.py` |
| T-PULSE2X-IMAGE | Is a PULSE2X=1 image qualified (fit, timing, deadline, I²S)? | FAIL | #205 |
| T-CLAP-L2 | Is D12A's late energy repaired in the shipping model and RTL? | in progress (#261 → D) | run_case D12A + model→RTL→I²S |
| T-LIVE-MIDI | Does a MIDI controller drive the image with bounded latency and no stuck notes? | not started | new; sim first |
| T-PHYSICAL | Does the board's line output match the digital prediction (gain, latency, noise, repeatability)? | operator-only (rig) | new capture analysis; #208 |
| T-MEASURE-QUAL | Does each scored estimator declare and enforce its validated domain? | FAIL (#115) | known-answer suites per estimator |
| T-BOARD | Scorecard coverage/pass counts, from qualified measurements only | 20 valid / 9 pass | `tools/scorecard.py --check` |

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

## 4. What this changes immediately

1. Add `make trial`, `tools/box_bootstrap.py` and the trial registry
   (`docs/trials.json`, extending `docs/dag.json`), with the release, deadline
   and playback trials wired first — they already have verifiers.
2. Curate the backlog: close stale/superseded issues with evidence; convert
   each "Root cause" essay into a gate (or close it as absorbed by a named
   trial); label every live issue with its trial and a Loom state.
3. Stop writing `plan0NN.md` as the source of truth; the trial registry and
   Loom issues are the plan.

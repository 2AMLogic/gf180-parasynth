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
from 09-18 (#71, #117, #123, #135, #155, #163) are still open; the same
failure modes recurred. (#104 has since closed the way this asks: as a tested
module, `model/measure_harness.py`, not as more text.) An RCA that does not become a gate or a check does not
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
| T-RELEASE-BOUND-R1 | The same question for the R1 image (#280), with R0 pinned as the rollback | PASS (#280) | `fpga/release/r1_release.py` + `test_r1_release.py` |
| T-PLAY-DIGITAL | Do the documented playback commands produce correct, non-silent audio through UART→RTL→I²S? | partial (held note was silent until #255) | release held-note + `verify_rolling_playback.py --rtl` |
| T-DEADLINE | Does every supported configuration meet the frame deadline, with complete evidence? | PASS baseline; pulse2x excluded | `rtl-sketch/verify_deadline.py` |
| T-PULSE2X-IMAGE | Is a PULSE2X=1 image qualified (fit, timing, deadline, I²S)? | FAIL | #205 |
| T-CLAP-L2 | Is D12A's late energy repaired in the shipping model and RTL? | in progress (#261 → D) | run_case D12A + model→RTL→I²S |
| T-LIVE-MIDI | Does a MIDI controller drive the image with bounded latency and no stuck notes? | not started | new; sim first |
| T-PHYSICAL | Does the board's line output match the digital prediction (gain, latency, noise, repeatability)? | registered; NO VERDICT (operator-blocked) until the rig is recorded | `tools/r0_capture.py` against `tools/r0_reference.py`; procedure `docs/capture-r0.md`; #208 |
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
same bootstrap and gates on T-DEADLINE and, since #278, on T-RELEASE-BOUND in its
own `trial-release-bound` job. Receipts live under `build/trials/` and
are uploaded by CI as `trial-receipts`; `tools/trial.py compare A B` checks two
environments reached the same numbers from the same inputs.

### Receipt membership is bound to the recorded registry (#283)

A receipt is only as strong as its child list. Before #283, `check-receipt`
walked whatever children the receipt listed, so deleting a failing required
child (with its directory) and re-sealing produced a VALID PASS, and deleting
the only required child did too (`all([])` is true). Now:

- `composite()` returns NO VERDICT when the required population is empty
  ("no required child answered the product question").
- `check-receipt` finds the registry whose sha256 the receipt recorded
  (`identities.registry`) -- the file at the recorded path, the working-tree
  `docs/trials.json`, then any commit in git history -- and compares the
  receipt's required and control children, with role, checker and interpreter
  spec, against that mode. A difference is a `population:` problem.
- If that exact registry cannot be retrieved, the result is `UNVERIFIABLE:` and
  the receipt is never VALID. This is deliberately a different message from a
  `population:` rejection: an unavailable historical registry is not evidence of
  forgery. A receipt checked after the working-tree registry changed is still
  VALID when the recorded registry is in history.

Limit: the registry hash is the receipt's own claim. A forger who also supplies a
registry with a matching hash is not caught here; git history is the authority
only when the repository is available. Controls: `tools/test_trial.py`
`test_forgery_a_*` / `test_forgery_b_*`.

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

### R1 release receipts, 2026-09-26 (#278; a snapshot, not maintained state)

`docs/trials/r1-2026-09-26.tgz` holds `trial-receipt/2` receipts from the build
box, produced by `tools/r1_release_trials.py` (its `summary.json` and logs are
in the bundle). After extracting it, `python3 tools/trial.py check-all r1`
reports **12/12 valid**. Editing one artifact of the extracted copy (the silent
control's peak set to 4000 and its verdict to PASS) is REJECTED twice: once as
an altered artifact, and once because the evidence re-derives to PASS, not caught.

Integrated revisions:

- `landed-abfb95b/` is a clean clone of main at `abfb95b`. That commit contains
  #274 (`935d153`, the trials pilot) and #255 (`019ec53`, the release manifest),
  plus #261, and nothing from #278. This is the landed #274+#255 combination
  the issue asks for. The earlier `cand255-5a8f87d/` merge above was local and
  never pushed, so it is development evidence only.
- `release-460abf9/` is a clean clone of #278's branch at `460abf9`, which is
  `abfb95b` plus the stale controls. Its `fixtures/` receipts come from a
  throwaway clone of the same commit with evidence removed. Each receipt records
  `dirty: true` and the damage. The real manifest and tree were only read.

| trial | tree | verdict | what it shows |
|---|---|---|---|
| T-RELEASE-BOUND | main `abfb95b` | NO VERDICT | both children BOUND, but no control declared, so it cannot PASS (the state #278 fixes) |
| T-RELEASE-BOUND | `460abf9` | **PASS** | manifest BOUND; binding BOUND; `stale-manifest` caught; `stale-binding` caught |
| T-RELEASE-BOUND, candidate stale-manifest | `460abf9` | FAIL | manifest bound to the pre-fix CLI bytes of `run --note 45 --fixture none` (26 packets, not 36) is STALE at exactly `commands.held-default.cmds_sha256`/`.packets` |
| T-RELEASE-BOUND, candidate stale-binding | `460abf9` | FAIL | isolated tree holding the image's pre-#252 `voice_dp.v` (`a1575257`) is STALE against `drift-clean`, naming exactly that file |
| T-RELEASE-BOUND, manifest deleted | `460abf9` + damage | NO VERDICT (preflight) | missing required asset; nothing ran |
| T-RELEASE-BOUND, manifest truncated | `460abf9` + damage | NO VERDICT | `release_manifest: REFUSED -- ... unreadable`; the stale-manifest control also REFUSED (not caught) |
| T-RELEASE-BOUND, bound record deleted | `460abf9` + damage | NO VERDICT | binding checker REFUSED; the stale-binding control also REFUSED (not caught) |
| T-DEADLINE reanalyse | main `abfb95b` | PASS | retained traces: slack 14, 3300/3300 I2S periods; late160 caught |
| T-DEADLINE sim | main `abfb95b` | PASS | this tree's RTL: slack 13, 3300/3300 periods, 0 missed; late160 caught |
| T-DEADLINE sim, candidate late160 | main `abfb95b` | FAIL | 3496 missed frames, overrun: the deadline reason |
| T-PLAY-DIGITAL sim | main `abfb95b` | **PASS** | held notes bit-exact and audible, peaks 12760/7345/10376 LSB (default/m5a-saw/m5a-pulse); demo phrase 257,185 I2S periods, 508/508 writes, 0 wire mismatches, 0 overrun; silent-image control caught (peak 0) |
| T-PLAY-DIGITAL sim, candidate held-legacy-silent | main `abfb95b` | FAIL | pre-fix image replays bit-exact but is silent: peak 0 below the 1024 LSB floor |

T-PLAY-DIGITAL on landed main reproduces the unpushed pilot merge's numbers
exactly (the same three peaks, 257,185 periods and 508 writes). The phrase took
2386 s of simulation.

**Unreadable manifest, start red.** On landed main, `release_manifest.py
--manifest <truncated copy>` raised `JSONDecodeError` and exited 1, which is
STALE's exit code. The trial still read it as NO VERDICT because no verdict line
was printed, but anything reading the exit status alone would have reported a
stale release. #278 makes it `REFUSED` (exit 2).
(`fpga/release/test_stale_controls.py::test_unreadable_manifest_is_refused_not_stale`)

**Wrong-then-right in this work: 0 measurements.** One defect was found by
reading code before the first run, not by a control. The binding control's
first parser would have counted the checker's indented "re-run the bench"
commands as uncovered sources. A STALE naming them would then have been
REFUSED as "a different reason", which is a false negative, not a false catch.

**Blocking, not yet required.** CI's `trial-release-bound` job fails unless the
trial PASSes. Making it a *required* status check is not blocked by permissions:
the sweep's token has admin on this repository. It is blocked by two things.
First, `main` has no branch protection or ruleset at all, so requiring anything
is a new repository policy. Second, a required job must exist on `main` before
it is required, or older PRs cannot merge. Tracked as #287.

### R1 candidate receipts, 2026-09-26 (#279; a snapshot, not maintained state)

`fpga/reports/r1-candidate/receipts.tgz` holds the `trial-receipt/2` receipts of
the R1 qualification run (build box, commit `ccf7ed4`, RTL frozen at `6864435`);
`summary.json` beside it is harvested from them by `tools/r1_harvest.py`, which
re-checks every receipt. After #300's review the T-PLAY-DIGITAL `r1` and
T-LIVE-MIDI `rtl` receipts were regenerated at `14e12bc` under the new
completeness gate (required I2S periods derived from the stimulus, a
truncation control on every replay) by **re-analysing** the same RTL runs
(`--reuse-rtl-if-identical`: full identity match); the held notes, which are
short, re-simulated. Every replay compared exactly its required count: demo
257,427, bar808-full 266,937, held 3,820, run-m5a 8,109, live MIDI 109,053 /
46,653 / 166,513; every truncation control caught. Naming: the historical release `baseline 2025.1, r1`
is **R0**; the "R1 release receipts" section above is about R0.

| trial | verdict | what it shows |
|---|---|---|
| T-PLAY-DIGITAL `r1` | **PASS** | `--image tree` bytes, target frozen apart from the sender: held notes 12760/7345/10376 LSB, `run --fixture m5a` 12760; demo 511/511 writes, 0 mismatches over 257,427 periods; bar808-full 397/397, 0 over 266,937; silent control and wrong-kit control (release sender vs R1 target, caught at the init bytes) caught |
| T-DEADLINE `stress` | **PASS** | full rev-14 kit under three gliding, modulated oscillators: 0 missed, slack 13; `late:15` caught (32 missed) |
| T-DEADLINE `sim` | **PASS** | arty-uart: slack 13, 3300/3300 periods; `late160` caught |
| T-LIVE-MIDI `sim` / `rtl` | **PASS** / **PASS** | 193-write known state; RTL 397/588/502 writes, 0 mismatches; 3/3 controls |
| T-RELEASE-BOUND | **PASS** | R0 preserved on this branch; both stale controls caught |

`sim` mode of T-PLAY-DIGITAL (R0 bytes through the current RTL) was not re-run:
it is a compatibility test and says nothing about either image's playback.

### R1 image receipts, 2026-09-27 (#280; a snapshot, not maintained state)

On the build box, after the one R1 build (bitstream `544499e2...`, routed
checkpoint `0f81026e...`, Vivado 2025.1 SW Build 6140274):

| trial | verdict | what it shows |
|---|---|---|
| T-RELEASE-BOUND-R1 | **PASS** | `r1-2025.1.json` BOUND: image, publication, shipped reports (re-parsed), DSP evidence (re-derived), per-port external I/O and routed.dcp agree; compiled inputs are the candidate's at `6864435`; `--image tree` bytes are the ones the candidate's RTL evidence replayed; R0 pinned |
| T-RELEASE-BOUND-R1, `stale-r1-image` | caught | the R1 manifest naming R0's bitstream/checkpoint/publication is STALE at exactly those three fields |
| T-RELEASE-BOUND-R1, `stale-r1-host` | caught | the R1 manifest naming R0's `run --fixture demo` bytes (508 packets, no preamble, revision-11 kit) is STALE at exactly `host.commands.demo.cmds_sha256`/`.packets` |
| T-RELEASE-BOUND | **PASS** | R0 untouched: its manifest still BOUND, both of its stale controls caught |

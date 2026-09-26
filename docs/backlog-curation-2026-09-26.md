# Backlog curation proposal — 2026-09-26 (read-only)

**Status: PROPOSAL. Nothing in this document has been applied.** No label,
close, comment or issue creation was performed while preparing it. It is the
"proposed diff first" that plan085 §7 asks for; the operator reviews it and
then applies (or edits) the mutations.

- Scope: every open issue on `2AMLogic/gf180-parasynth` at 2026-09-26
  ~11:30Z — **64 issues** (the "~66" in the brief included PRs #255/#261,
  which are pull requests, not issues).
- Verified against: `origin/main` @ `b9c5223`, PR states via `gh`, issue
  bodies and all comments. "Fixed" below always names a merged change on
  `origin/main` (every SHA cited was checked with
  `git merge-base --is-ancestor <sha> origin/main`). A plan, a trial name or
  an open PR is never cited as closure evidence.
- Framework: `docs/trials.md` from PR #266 (draft), amended by plan085
  (three verdicts PASS / FAIL / NO VERDICT; expected-evidence transitions
  rather than mandatory FAIL→PASS; close the investigation, keep the goal).
- Labels: only labels that exist today (`gh label list`). Every
  `loom:operator-only` carries exactly one sub-kind, per
  `.loom/roles/curator.md` § "Applying `loom:operator-only`".

## 1. Summary

### Counts by proposed disposition

| Disposition | Count | Issues |
|---|--:|---|
| close — fixed (merged change) | 7 | #68, #77, #95, #99, #111, #169, #206 |
| close — superseded | 7 | #79, #85, #100, #122, #123, #135, #152 |
| close — absorbed by an existing mechanism | 2 | #117, #163 |
| epic (tracking only) | 3 | #115 (new label), #207 (repurposed), #45 (unchanged) |
| operator-only + sub-kind | 1 | #208 (`loom:operator-blocked`) |
| promote to `loom:issue` (short queue) | 2 | #205, #225 |
| in flight / automation-owned — no change | 9 | #23, #48, #213, #224, #239, #242, #245, #254, #263 |
| park — remove `loom:issue`, keep `loom:curated` | 2 | #33, #61 |
| keep in triage/backlog | 31 | #71, #94, #102, #104, #107, #109, #114, #116, #124, #125, #127, #129, #131, #134, #136, #137, #138, #140, #143, #155, #158, #162, #165, #215, #220, #243, #247, #250, #257, #259, #264 |
| **total** | **64** | |

Plus **five proposed new issues** (§3). None has been created.

### The short executable queue (priority order)

Only these items should be dispatchable. Everything else stays in
triage/backlog or is already in flight.

| # | Item | Trial | Expected evidence transition | Owner / constraint |
|---|---|---|---|---|
| 1 | **PR #255** release manifest (no issue; PR is the unit) | T-RELEASE-BOUND | NO VERDICT (draft, `make verify` not yet run on the build box) → PASS for the declared Arty baseline domain, with exclusions (PULSE2X=1, #247 domain) kept visible | release owner; build box; one heavy job |
| 2 | **New issue N1**: D12A L2 final strike into the production model → RTL → I²S (plan081 D) | T-CLAP-L2 | acoustic ratio/decay: valid FAIL (baseline 0/8) → PASS (L2 8/8 on fresh offsets, PR #261); timing property stays **NO VERDICT — unqualified** and visible | sound owner; waits on operator go (Q2) and #261 merging |
| 3 | **New issue N2**: trial pilot — `make trial` + bootstrap for T-RELEASE-BOUND, T-DEADLINE, T-PLAY-DIGITAL | T-DEADLINE, T-PLAY-DIGITAL (+ T-RELEASE-BOUND wrapper) | preserved PASS (deadline, #248), NO VERDICT → PASS (playback through UART→RTL→I²S), plus each negative control listed in plan085 §5 reaching its intended verdict | one integration owner, after #255 settles |
| 4 | **New issue N3**: live MIDI, simulator half | T-LIVE-MIDI (sim) | not run → PASS/FAIL in simulation; physical half stays operator-only | after N2 |
| 5 | **#208** first reproducible board recording | T-PHYSICAL | not run (operator-blocked) → valid capture reanalysed in CI | **operator-only** (rig, board) |
| 6 | **#205** PULSE2X=1 Arty image | T-PULSE2X-IMAGE | NO VERDICT (no qualified image exists) → per-component PASS/FAIL (fit, timing, deadline, I²S) | build box; after #255; one heavy job |
| 7 | **#225** transient detector default at MIDI 36 | T-MEASURE-QUAL (child) | valid FAIL (clean MIDI 36 saw flags every period) → PASS with injected-click control still caught | any builder; `model/reference_integrity.py` |
| 8 | **New issue N4**: T-MEASURE-QUAL pilot — burst-timing estimator declares its domain and refuses on noise-excited envelopes | T-MEASURE-QUAL (child of #115) | clap timing currently consumed as if valid → **NO VERDICT (REFUSED)** on D12A timing; M1A `m1a-envelope-score-v3` preserved PASS as the qualified example | measurement owner; not the L2 sound owner |

Not in the queue, deliberately: #33 (LibreLane half slot), #61 (saw aliasing
re-measure) — both approved but not on the product critical path; see "park".

### Needs operator answer (short form; full list in §4)

1. Has the Arty A7-100T arrived, and is the PCM5102 + capture interface (MOTU?) rig available? `fpga/ARTY.md:532` on main still says the board has not arrived.
2. Go / no-go for plan081 D (L2 into production), given timing stays unqualified.
3. Park #33 and #61 (remove `loom:issue`) so Loom does not dispatch them ahead of the queue?
4. Should speculative sound/estimator backlog be parked with `loom:operator-only` + `loom:operator-objective`, since the Curator's fallback query picks up unlabeled issues?
5. PR #229 (#224) is held by Champion (critical file `.github/workflows/rungs.yml`, `loom:operator`): approve or send back?

## 2. The full table

Column key. **Labels**: current → proposed (`+` add, `−` remove).
**Evidence**: merged PRs / SHAs / paths on `origin/main` @ `b9c5223`.
**Transition**: the evidence change the issue is expected to produce, and its
stop rule. "Keep" rows state why they are not dispatchable yet.

### 2.1 Close — fixed

| Issue | Current labels | Proposed | Trial / goal | Evidence (merged) | Transition / stop rule |
|---|---|---|---|---|---|
| #68 Version rendering and analysis separately | `loom:building` `loom:curated` `tier:goal-advancing` | **close (completed)**; `−loom:building` | T-MEASURE-QUAL infra | PR #260 `1d5faa5`: `tools/manifest.py`, `tools/provenance.py`, `tools/test_manifest.py`; ACs 1–5 met per its table. AC6's CI-upload half and "one real case" were filed as #259 by that PR | done; remainder lives in #259, not here |
| #77 Reference-rig qualification must be an automated preflight | none | **close (completed)** | T-MEASURE-QUAL (reference side) | #87 `74ce6a0` (mapping derived from measurement; deliberately-wrong setups rejected); #121 `93c0c41` (frozen profile: bundle version + binary SHA-256, `tools/refprofile.py:345-371`); #234 `c3a8797` (pins checked after every clip); #190 `105a466` (reference controls run in CI) | done; per-capability qualification and driver causality continue in #136 / #137 |
| #95 All three toms fail the same pitch-drop check | none | **close (completed)** | T-BOARD sound (toms) | #110 `0bdf44f` measured ×1.063/×1.140/×1.236; #154 `80b3756` corrected the law; #173 `ff2498d` reconciled pins; `spec/NUMERIC-CONTRACT.md` 15.7.1 now "HARDWARE-MEASURED". `docs/scorecard/BOARD.md`: D03A/D05A/D07A now fail on **body spectrum**, not pitch drop | done; the remaining tom failure is a different property (goes to epic #207) |
| #99 Two definitions of "closer to the machine" — which judges? | none | **close (completed)** | T-BOARD (acceptance rule) | DR 0015 via #157 `6347d45`; implemented by #159 `0548ad5` (`evaluate()` returns properties; `compare()` ACCEPT/REJECT/INCOMPARABLE) | done; the issue's own last comment says "do not re-open the decision" |
| #111 Nobody has measured the machine's own spread | none | **close (completed — premise refuted)** | T-BOARD tolerances | #126 `4ded211`: no take axis exists; session-to-session spread measured (f0 2.74 %, band 0.159 dB, T20 1.30 %, attack 0.079 ms) | completed negative experiment. Unit-to-unit spread is unmeasurable with a single-machine corpus: recorded as a known limitation and operator question Q6, not a live task. f0 tolerance shape continues in #127 |
| #169 #164's repair reached filt_corner but not filt_rolloff | none | **close (completed)** | T-BOARD filters (F1) | #178 `6495128` ("Fixes #167 and #169"): `tools/run_case.py` `filt_rolloff` now uses `am.dc_plateau_db(...)` and passes `ref_db` to `corner_from_curve` | done; F1 records regenerated later by #232/#235 |
| #206 Evidence-gated publish path for dsp_feedback_review_complete | none | **close (completed)** | T-RELEASE-BOUND (child) | #209 `981ee88` (flag derived in `fpga/publish_arty.py:242`); #212 `9430d53` (binding by `routed.dcp` digest); `fpga/reports/arty/integrated-baseline-2025.1/publication.json:120` = `true`; tests in `fpga/test_publish_arty.py` | done |

### 2.2 Close — superseded

| Issue | Current labels | Proposed | Superseded by | Evidence | Note |
|---|---|---|---|---|---|
| #79 The execution DAG: what can run in parallel | none | **close (not planned — superseded)** | #85, then `docs/dag.json` + `tools/compile_dag.py` (README DAG) | #57, #145 `5c8f16f`; every workstream it listed has since merged (#74, #30, #130, #87) | a 2026-09-18 plan snapshot; the trial registry (N2) replaces it as the operational source |
| #85 The plan, as five steps | none | **close (not planned — superseded)** | `docs/dag.json`, `docs/milestones` (#78), trials catalogue (#266) | steps 1–3 landed (#74, #87/#121, #93) | plan snapshot; plan085 §0 says snapshots must not compete with the registry |
| #100 Fitting policy: CMA-ES on a float surrogate | none | **close (not planned — superseded)** | DR 0015 (#157 `6347d45`) — #99's closing comment: "Supersedes #100" | DR 0015 text; #159 `0548ad5` implements "a surrogate proposes, the exact rule accepts" | holdout prerequisite stays in #116 |
| #122 32 Mono cases rest on a reference that has never made a sound | none | **close (not planned — superseded)** | Mono cases are now scored against qualified Mini V3 frozen references: M5A (#180), M5B #187 `ce5b004`, M1A #194 `f269761` / #203 `fd0d339` / #214 `9b44785` | `docs/scorecard/mono-*` | Model D cross-check stays in #124. Wording drift to fix separately: `tools/refprofile.py` `RIG_VERDICTS["miniv3"]` still says Mono cases cannot use Mini V3 envelopes (#129 is the vehicle) |
| #123 Root cause: configuration recorded as tool property | none | **close (not planned — superseded)** | #136 (verdict keyed on (rig, host, capability)) — **#136's body must absorb #123's environment-tuple rule before this closes** | the defect is still live: `tools/refprofile.py:247-252` still records Model D as "renders exact silence headlessly" with no host | not "fixed": moved to the concrete issue that would fix it |
| #135 Root cause: measured effect shipped with an unverified mechanism | none | **close (not planned — superseded)** | #155 row 3 ("the cause is Z → test Z") is the same rule | no mechanical check exists for this class | kept visible inside #155; see §2.9 |
| #152 The drum block has no DC blocking anywhere | `next` | **close (completed — diagnosis)**; `−next` | #165 (the bounded prototype that carries the unresolved goal) | #160 `50288cc` localised it (two mechanisms, five voices + two marginal) | plan085 Rule 5: close the investigation, keep the goal in #165 |

### 2.3 Close — absorbed by an existing mechanism

Plan085 §3 Rule 5: a root-cause issue closes as covered only when an actual
merged check exercises its failure mode.

| Issue | Current labels | Proposed | Mechanism that exercises the failure mode | Caveat |
|---|---|---|---|---|
| #117 Root cause: reasoning substituted for execution | none | **close (completed — absorbed)** | `tools/check_doc_claims.py` (#230 `8182f22`) re-derives each marked prose claim from a test / grep / commit and reports OK / STALE / REFUSED; wired into `make verify` (`Makefile:56`) and `make claims`. That is the essay's rule 1 ("a claim about code must cite an execution") | covers **marked** claims only. Rules 2–4 are working hints, not checkable |
| #163 Root cause: symmetry of code is not symmetry of treatment | none | **close (completed — absorbed)** | invariance tests that catch the class without knowing the mechanism: `model/test_discrimination.py::test_a_measurement_does_not_depend_on_where_the_record_begins` (#166 `2e41887`), and the #103 invariances in `model/test_audio_measure.py` (#132 `ba14af2`); all three named instances (#101, #160 F2, #161) are closed | protects the discrimination/measurement path; a new paired comparison elsewhere needs its own invariance test |

### 2.4 Epics

| Issue | Current labels | Proposed | Children | Stop rule |
|---|---|---|---|---|
| #45 Five mechanical fixes | `loom:epic` | no change | #224 open (PR #229, held); #222/#223 closed | Champion auto-closes when #224 closes |
| #115 Estimators should declare their validated domain and refuse outside it | none | `+loom:epic` `+tier:goal-supporting`; retitle prefix "T-MEASURE-QUAL:" | N4 (pilot), #225, #134, #158, #104, #109, #127, #136, #137 | per plan085 §6: a family, not one task. Closes only when every **scored** estimator declares its domain; never on the pilot alone |
| #207 Lane F: continue sound qualification | none | `+loom:epic`; retitle "T-BOARD sound quality: one trial per property" | N1 (clap), #61, #107, #162, #165, #220, #243, #257, #102; M1A harmonic/attack line (#218, #219, #226 merged, no candidate) | tracking only; a child closes on a qualified improvement or a completed negative experiment. A "20 valid / 9 pass" board is coverage, not a pass (plan085 §6) |

### 2.5 Operator-only

| Issue | Current labels | Proposed | Trial | Evidence / prerequisites | Transition / stop rule |
|---|---|---|---|---|---|
| #208 First reproducible recording from the board | none | `+loom:operator-only` `+loom:operator-blocked` `+tier:goal-advancing` `+next`; retitle to drop "(BLOCKED: … not yet delivered)" **only if** Q1 says the board has arrived | T-PHYSICAL | staged: image `a66c9349…`, `fpga/uart_host.py run`, wiring in `fpga/ARTY.md`; `fpga/ARTY.md:532` on main: "the board has not arrived". Prereq: #255 merged (the capture must name the released image) | not run → a retained raw capture whose reanalysis CI can repeat (gain, latency, noise, repeatability). CI cannot manufacture the capture. If the board has arrived, swap the sub-kind to `loom:operator-mechanical` (the protocol is written; wiring/recording is a physical action) |

### 2.6 Promote to `loom:issue` (queue items that already have an issue)

The Curator does not add `loom:issue`; these are for the operator (or
Champion) to apply after the body is amended with the trial contract.

| Issue | Current labels | Proposed | Trial | Evidence / prerequisites / owner | Transition / stop rule |
|---|---|---|---|---|---|
| #205 Lane E: PULSE2X=1 Arty build | none | `+loom:curated` `+loom:issue` `+tier:goal-advancing` `+next`; body amended with per-component criteria | T-PULSE2X-IMAGE | RTL side merged (#192); PR #189 (draft) measures 2x pulse; PR #248 `d089c67` excludes PULSE2X=1 from T-DEADLINE; #255 lists it as an exclusion. **Prereq: #255 merged; the build box free (one heavy job).** Files: `fpga/build_arty.py`, `fpga/publish_arty.py`, new report dir | NO VERDICT (no qualified image) → per component (fit, timing, deadline, I²S) PASS or valid FAIL. An absent image is not a FAIL. Stop after one image is qualified or refused with reasons; do not change the PULSE2X=0 baseline |
| #225 transient_report: 5 ms blocks flag clicks on MIDI 36 | none | `+loom:curated` `+loom:issue` `+tier:goal-supporting` `+next` | T-MEASURE-QUAL (child of #115) | `model/reference_integrity.py:54` still `block_ms=5.0` (last touched `17eb862`). Files: that module + its tests | valid FAIL (clean MIDI 36 saw reports transients on every period) → PASS, and the injected click on the same saw still caught; higher-pitch cases preserved. Stop when both tests land; no retuning of `k` |

### 2.7 In flight or automation-owned — no change

| Issue | Current labels | State | Note |
|---|---|---|---|
| #23 R10 target: paraphony AND a complete 808 | `loom:blocked` `tier:goal-advancing` | tracking; blocked on silicon (#33) | root product goal; leave |
| #48 Shark-tooth BLAMP | `loom:issue` `loom:curated` `tier:maintenance` | PR #244 (`loom:changes-requested`, `loom:ci-failure`) | **PR #244 adds `spec/decision-records/0017-…` but 0017 and 0018 and 0019 already exist on main — must renumber to 0020** (#250) |
| #213 Champion: Merge-Risk Hold Digest | `loom:blocked` | automation-owned digest, "not a work item" | leave |
| #224 Sensitivity-sweep gate (#45 item 5) | `loom:issue` `loom:curated` `tier:goal-supporting` | PR #229 `loom:pr` + `loom:operator` (critical-file hold) | operator Q5 |
| #239 Re-derive discrimination.md §8.4/§8.6 | `loom:issue` `loom:curated` `tier:maintenance` | PR #262 `loom:pr` | leave |
| #242 sound_report locked properties drifted; nightly gate broken | `loom:building` `loom:curated` `tier:goal-supporting` | building | leave |
| #245 Two build-tool bugs are not injections | `loom:issue` `loom:building` `loom:curated` `tier:goal-supporting` | building | leave |
| #254 verify_ctl refusal paths | `loom:issue` `loom:building` `loom:curated` `tier:goal-supporting` | PR #268 | leave |
| #263 Duplicate comment block in reference_rigs.py | `loom:building` `loom:curated` `tier:maintenance` | PR #267 | leave |

### 2.8 Park — approved but not on the queue

Proposed only if the operator agrees (Q3). Removing `loom:issue` keeps the
curation; re-promotion is one label.

| Issue | Current labels | Proposed | Trial / goal | Why parked | Stop rule for un-parking |
|---|---|---|---|---|---|
| #33 Joined chip half-slot route (LibreLane) | `loom:issue` `loom:curated` `tier:goal-advancing` | `−loom:issue` | silicon (#23, DAG node S2) | heavy new toolchain (LibreLane 3 + Nix, PR #176 draft); off the instrument's current critical path | re-promote when the operator schedules silicon work |
| #61 Oscillator aliasing re-measure (saw, OSC2X=1) | `loom:issue` `loom:curated` `tier:goal-supporting` | `−loom:issue` | T-BOARD sound (#207) | a useful measurement, not a queued trial | re-promote as an #207 child once N1 and the pilot land |

### 2.9 Keep in triage / backlog

"`+loom:triage`" is proposed only for concrete, bounded items that can be
curated into a trial contract. Speculative items keep **no** Loom label (see
Q4). Nothing here is dispatchable.

| Issue | Current labels | Proposed labels | Trial / parent | State against main (evidence) | Prerequisites / file owner | Expected transition / stop rule |
|---|---|---|---|---|---|---|
| #71 Root cause: briefs are workstreams | none | none | process (plan085 §8) | **convert, not absorbed.** The "full verification repeatedly" half is mitigated by the `make verify` / `verify-full` split (#65 `0faba3e`); the "one deliverable per brief" half has no check. Proposed gate: plan085 Rule 1 (every work issue names one trial + expected transition), enforced at curation | N2 lands the rule | close when the pilot's §8 metrics show a completed item carrying its trial contract |
| #94 Every scorecard case says fixed-model | none | none | T-BOARD coverage | partly done: `docs/scorecard/results/M5A.json` is `integrated-rtl`; the drum, filter and ensemble anchors are still `fixed-model` | after N2 (reuses the playback path) | NO VERDICT (engine) → valid measurements on `integrated-rtl` for one drum, one filter, one ensemble case |
| #102 Cymbal Hh3 third-order vs 2-pole | none | none | #207 | speculative structural change; its prerequisites (#101, #99) are closed, but no holdout exists (#116) | #116 | completed enumeration (2- vs 3-pole) on a holdout |
| #104 Deliverables must be reusable instruments | none | `+loom:triage`; narrow to "promote `validate_known_answer` / `floor_for_these_signals` / `windowed_alike` / `descent_test` into a shared module" | #115 | the helpers still live only in `tools/measure_conga_body_spread.py`; the "commit your instrument" rule is in CLAUDE.md (text, not a check) | measurement owner | preserved PASS (conga tool tests) with the helpers importable elsewhere |
| #107 One envelope drives both partials | none | none | #207 (cowbell) | structural; #141/#241 `0fce497` changed the cowbell metric direction but not the wiring | #116 holdout | completed experiment: cost of independent envelopes vs single-instant match |
| #109 Scorecard collapses time-varying quantities | none | `+loom:triage` | #115 | `band_pair_db` charges τ twice; single-window metrics | measurement owner | estimator declares interval; valid trajectory metric for D10A/D13A |
| #114 Constants carry provenance as data | none | none | #115 (adjacent) | speculative infra | — | — |
| #116 Rubric versioning, sealed holdout, no omnibus scalar | none | none | T-BOARD | partly delivered: #260 records analyser version and bound-change rationale; #159 has no omnibus judge. The sealed holdout does not exist | operator Q7 (holdout policy) | holdout runnable for ≥1 voice |
| #124 pedalboard-backed reference rig | none | none | #115 reference side | not built (`pedalboard` appears only in docs/strings) | dependency decision | — |
| #125 Mine prior plugin-corpus work in ~/dev | none | none | — | depends on a local `~/dev` path; low value | — | candidate for close (not planned) at the next review |
| #127 f0 tolerance cannot distinguish bass-drum settings | none | `+loom:triage` | #115 / T-BOARD tolerances | still true; its blockers (#99, #101) are closed | measurement owner | tolerance re-expressed relative to control travel, with its rationale recorded by `accept()` |
| #129 Prose verdicts inside a hashed input | none | `+loom:triage` | #115 reference side | still true: `refprofile/README.md:62` says `profile.json` and `tools/run_case.py` "still carry the unscoped wording" | touches the stale-input guard → coordinate with N2 | wording correctable without invalidating clip hashes; guard still refuses a clip-hash edit |
| #131 Host scheduling policy lives outside the contract | none | `+loom:triage` | T-PLAY-DIGITAL / T-LIVE-MIDI | contract §4 says at most one write per frame (`spec/NUMERIC-CONTRACT.md:372`) but not the host anchor rule; the policy lives in `fpga/spi_host.py` / `LiveMusicHost` | N3 | informative contract text + a conformance test a second host must pass |
| #134 NaN defeats threshold guards | none | `+loom:triage`; narrow to "estimator-wide NaN invariance test" | #115 | partly done: #133 `ceb2bba` (refprofile), `ad8f4d4` (#241 review: non-finite score is no verdict). No test injects NaN into every `audio_measure` estimator | measurement owner | valid FAIL on the new invariance test for any permeable estimator → PASS |
| #136 Qualification is one boolean per reference | none | `+loom:triage`; absorb #123's environment tuple | #115 reference side | `RIG_VERDICTS` (`tools/refprofile.py:241`) is still one boolean per rig | #129 (same file) | per-(rig, host, capability) verdict; a Model D refusal names its host |
| #137 Nothing checks a driver's commands took effect | none | `+loom:triage` | #115 reference side | no differential (octave / cutoff) check in `tools/refprofile.py` | after #136 | a deliberately unresponsive rig stub is REFUSED |
| #138 Discriminator: what is it not measuring? | none | none | #207 | speculative | #143 first | — |
| #140 DAG node GREEN regardless of its evidence | none | `+loom:triage`; narrow to prerequisite propagation | N2 (composite-verdict rule) | half fixed by #145 `5c8f16f`; its own PR says "the prerequisite-propagation half … stays open"; `tools/compile_dag.py` has no dependency propagation | fold into N2 if the registry composes DAG nodes | GREEN above RED/BLOCKED → BLOCKED, with a start-red control |
| #143 Knob-equivalents rescale per comparison | none | none | #207 | speculative | — | — |
| #155 Root cause: claims have types | none | none; keep as the single remaining claim-type essay (absorbs #135) | process | **convert, not absorbed.** Rows with a mechanism: "main says X" → `tools/check_stale_base.py` (#72 `039fa0e`) and the stale-tree refusal (#98 `f4141de`, `tools/test_run_case.py:736`); "this guard catches Y" → `make controls` + permanent injections (#251 `bf13fdd`). Rows 3–4 ("the cause is Z", "fixed → grep for the same shape") have no check | a Judge checklist item under plan085 Rule 3 is the proposed gate | close when rows 3–4 have a named check or are explicitly accepted as review-only |
| #158 Validate the judge | `next` | `−next` `+loom:triage`; narrow to fixtures + perturbation ladders | #115 | point 6 (acceptance-policy unit tests) delivered by #159 `0548ad5` | measurement owner | per-estimator report (bias, false alarms, abstention) for ≥1 estimator |
| #162 Bass drum attack 18–23 dB short | none | none | #207 (BD) | open; #21 closed with the mechanism confirmed but blocked by #220 | #220 | — |
| #165 Prototype a DC blocker | `next` | `−next` | #207 (CY, RS) | PR #175 (draft): "recovered experiment, acceptance not met" | #175 disposition | completed negative experiment or qualified improvement with controls BD/HT/CH preserved |
| #215 Conflict-only rebase breaks commit-hash provenance | none | `+loom:triage` | N2 / plan085 Rule 6 | still possible: `tools/provenance.py:60` records `rev-parse HEAD`; no reachability refusal on read | after N2 | an unreachable `source_commit` gives REFUSED, not `CalledProcessError` |
| #220 modal_fixed floored biquad DC pedestal | none | none | #207 (BD) | open; blocks #21's BP candidate | — | measured acceptable pedestal floor |
| #243 Pekonen-style coloration | none | none | #207 | speculative; the issue itself says "not urgent" | — | "measured, not taking it" is a complete outcome |
| #247 Model/RTL mismatch: glide between increments ≥ 2^23 | none | `+loom:triage` | T-RELEASE-BOUND (declared exclusion) | reproducible (`rtl-sketch/verify_deadline.py --scenario extreme-saw`); #255 excludes this domain | release owner; may need an operator ruling (fix vs clamp the register range, Q8) | valid FAIL → PASS, or the domain removed by contract with a refusal |
| #250 Decision-record numbers collide | none | `+loom:triage` | repo hygiene | live now: PR #244 adds 0017, while 0017–0019 exist on main; no check in `tools/` or `Makefile` | — | a duplicate-number check that fails a fixture with two 0017 files |
| #257 Per-frame resonance-keyed cutoff correction | `tier:goal-supporting` | none | #207 (filter) | datapath and contract-revision change; #237 declined it on purpose | operator objective | — |
| #259 Manifest artifacts into CI | `loom:triage` | no change | N2 / plan085 §4 (retention) | follow-up from #260; none of the workflows uploads `runs/` or `jobs/` | fold into N2 if the pilot needs artifact persistence | one real case retained and uploaded with a documented retention policy |
| #264 moog-acceptance.yml times out at 20 min | `loom:triage` | no change | CI health | 6 of the 12 most recent runs cancelled (08:39–10:17Z); runners moved to Blacksmith by #186 at 11:18Z, **after** every observed timeout. `timeout-minutes: 20` unchanged | re-measure on Blacksmith | close if ≥10 consecutive runs on Blacksmith finish under the cap; otherwise split or raise the cap |

## 3. Proposed new issues (not created)

**N1. T-CLAP-L2: ship D12A's explicit final strike (L2) through model → RTL → I²S (plan081 D)**
- Parent: #207. Prerequisites: PR #261 merged; operator go (Q2).
- Acceptance, reported separately and never collapsed into one green label (plan085 §6):
  (a) acoustic: burst/tail ratio and decay on DEV and fresh offsets, D12A official record re-scored, baseline 0/8 → L2 8/8 expected;
  (b) implementation equivalence: production `drums_fx` matches the experiment's `EnvFx` at L2, and matches bit-exactly at "no final level";
  (c) RTL bit-exact against the model, decoded I²S;
  (d) T-DEADLINE preserved;
  (e) burst timing reported **NO VERDICT (unqualified)**, never PASS;
  (f) image readiness stated separately (no image rebuild claimed).
- Preservation set: the other 15 drum cases' current verdicts. Stop rule: if (b) or (c) cannot be met at the 0.5 dB tie band, stop and report. Do not retune.

**N2. Trial pilot: `make trial T=` + box bootstrap for T-RELEASE-BOUND, T-DEADLINE, T-PLAY-DIGITAL**
- Scope and acceptance are plan085 §5, verbatim. Existing checkers only: `fpga/release/release_manifest.py` (#255), `rtl-sketch/verify_deadline.py` (#248), `fpga/verify_rolling_playback.py` (#210 `aa13017`) and the held-note path from #255. Registry fields that reference `docs/dag.json` rather than copy it.
- Expected transitions: T-DEADLINE preserved PASS; T-PLAY-DIGITAL NO VERDICT → PASS; each control (asset removed → NO VERDICT; truncated I²S → not PASS; DUT counterexample → FAIL; cancellation → no reusable PASS).
- Timebox: if it needs new general infrastructure, stop and shrink the wrapper. One integration owner, after #255 settles. Then run one narrow issue (candidate: #225) through Curator → Builder → Judge → Champion with no human relay.

**N3. T-LIVE-MIDI (simulator half): MIDI in → `LiveMusicHost` → UART bridge → RTL, bounded latency, no stuck notes**
- Reuse `fpga/README.md`'s `LiveMusicHost` (#177 `d9f23e3`) and `fpga/uart_device_sim.py`.
- Acceptance: a recorded MIDI stream (note on/off, overlapping notes, a note-off lost at the end) produces the expected register schedule and non-silent I²S; note-on to first sample latency is bounded and reported; zero stuck notes; an injected dropped note-off is caught.
- The physical half stays under #208 / T-PHYSICAL. Absorbs #131's contract text if convenient.

**N4. T-MEASURE-QUAL pilot: burst-timing estimator declares its validated domain and refuses on noise-excited envelopes**
- Parent: #115. Evidence it is needed: PR #261 B found the official detector fails every pre-frozen budget on independent noise-excited fixtures, but is exact on a noise-free carrier.
- Acceptance: the estimator carries its domain as data; on D12A it returns REFUSED with the reason, and the board shows NO VERDICT for timing (not PASS 8/8); its noise-free known-answer test is preserved; M1A `m1a-envelope-score-v3` (#214) is recorded as the worked example of a qualified estimator.
- Owner: a measurement owner, not the L2 sound owner, to avoid file collisions with N1.

**N5 (optional). T-RELEASE-BOUND tracking issue for PR #255**
- Only if the operator wants the release to have an issue for Loom to track; otherwise the PR is the unit.
- Acceptance: `release_manifest.py` BOUND on the build box with `make verify` exit recorded in the PR; exclusions listed; **explicitly states that the published image predates drift (contract 6.11 / DR 0019, `fpga/ARTY.md:19-27`)**, so the manifest must bind the image's recorded sources, not the live tree.

## 4. Needs operator answer

1. **Board and rig (#208, T-PHYSICAL, N3 physical half).** Has the Arty A7-100T arrived? Is the PCM5102 breakout wired, and is a capture interface (the MOTU, or another) available with AGC and effects off? `fpga/ARTY.md:532` on main still says "the board has not arrived". The answer picks #208's sub-kind: `operator-blocked` (not arrived) or `operator-mechanical` (arrived).
2. **L2 go (N1).** PR #261 says plan081 D "waits for the coordinator's go". Ship L2 with timing unqualified and visible, or wait for N4?
3. **Park #33 and #61** by removing `loom:issue`, so Loom does not dispatch heavy or off-queue work ahead of the eight queue items (plan085 §5: at most two implementation owners and one heavy workload)?
4. **Parking the speculative backlog.** The Curator's Priority-2 fallback query curates unlabeled issues, and Champion may then promote them. Apply `loom:operator-only` + `loom:operator-objective` to the speculative sound/estimator items (#102, #107, #114, #124, #125, #138, #143, #162, #220, #243, #257) until a product objective selects them, or leave them unlabeled?
5. **PR #229 (#224)** carries `loom:operator` for a critical-file change (`.github/workflows/rungs.yml`) and has conflicted since 2026-09-25T21:15Z. Approve the workflow change, or send it back?
6. **Second-unit 808 data (#111 residual).** Every reachable recording descends from one machine. Is there any plan to acquire a second unit or its recordings (`808_loops_from_mars.zip` gives repeats, not a second unit)? If not, record unit-to-unit spread as a permanent limitation of T-BOARD.
7. **Holdout policy (#116).** Seal the `/variation` cases now, before any fitted coefficient ships?
8. **#247 domain.** Should register-legal increments ≥ 2^23 be fixed (model/RTL glide agreement) or removed from the contract's supported domain (clamp, with a refusal)? #255 currently excludes the domain, which is honest but leaves the bug open.
9. **Build-box budget.** #255, #205 and any N1 RTL runs each need the EC2 build box. Confirm the order (#255 → N1 → #205), and that only one heavy job runs at a time.

## Method and limits

- Read every open issue body and comment thread (64 issues), `docs/trials.md`
  at PR #266's head, plan085, `.loom/roles/curator.md` and the label list.
  Checked claims against `origin/main` @ `b9c5223` with `git show`,
  `git grep` and `git merge-base --is-ancestor`, and PR state with `gh`.
  No tests, sims or builds were run; no verdict here comes from execution.
- Places where a claim from the brief did **not** hold as stated:
  #206 was fixed by #209 **and** #212 (#209 alone left the flag false on the
  integrated baseline); #140 is only **half** fixed by #145; and #208's "not
  delivered" is **not stale on main**: `fpga/ARTY.md` still says so. Whether
  it is stale in reality is Q1.
- Counts in PR #266 ("65 open issues; ~40 carry no Loom label") were not relied on:
  the live count is 64, and 49 carry no `loom:` label.

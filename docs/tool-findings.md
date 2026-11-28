# Tool findings: what was filed upstream, and what was looked at and not filed

This is the investigation recorded on **2026-09-18**, against the versions
named below. Statements about upstream `main` refer to those commits. At
landing on 2026-09-19, upstream issues
[#2085](https://github.com/2AMLogic/klayout-tools/issues/2085),
[#2088](https://github.com/2AMLogic/klayout-tools/issues/2088), and
[#2089](https://github.com/2AMLogic/klayout-tools/issues/2089) were closed;
[#2086](https://github.com/2AMLogic/klayout-tools/issues/2086) and
[#2090](https://github.com/2AMLogic/klayout-tools/issues/2090) remained open.
Closing an upstream issue does not revalidate our older flow artifacts.

A comb of this repository for **defects in the tools we use** that had been
discovered here and recorded only as workarounds in shell scripts, run logs and
doc footnotes.

The point is stated in `README.md` §"Why this block exists": a fixed tool helps
every project that follows; the instrument helps one. A workaround that lives
only in a shell script is a finding nobody else can use.

**Two lists follow, and the second matters as much as the first.** "Looked at
and not filed, with the reason" is what stops the next comb re-examining the
same ground.

Scope of this pass: filing target was `2AMLogic/klayout-tools`. Findings whose
upstream is a different project are recorded below with the correct home named,
not filed in the wrong repository.

**Later passes, other upstreams.** This document is the 2026-09-18
klayout-tools comb and is not extended in place. Findings against the other
upstream named in `CLAUDE.md`, `2AMLogic/loom`, live in their own dated
write-ups, or directly in the upstream report when the finding was filed
straight upstream with no local write-up:

| pass | finding | filed |
|---|---|---|
| [`loom-orphan-reap-2026-09-27.md`](loom-orphan-reap-2026-09-27.md) | orphan reaping is uid-blind: a dockerd-started (root-owned) orphan survives every Loom SIGKILL with EPERM, is logged as "uninterruptible I/O", and binds no dispatch decision | [rjwalters/loom#9160](https://github.com/rjwalters/loom/issues/9160) |
| #850 re-curation pass, 2026-11-16 | `check-verified-corrections-preserved.sh` matches only `## ` headings: an h3 `### Verified corrections` section passes silently at exit 0 even when deleted wholesale, and an h2 section mid-body swallows following h3 subsections so unrelated `###` edits FAIL as lost corrections | [rjwalters/loom#13938](https://github.com/rjwalters/loom/issues/13938) |
| #877 upstream filing, 2026-11-16 | the #9548 write guard refuses `loom-daemon forge create-issue` on every foreign public repo (`NOT_IN_INSTALLATION`, "Loom writes need WRITE") although issue creation needs only `issues:write`: the same App token files there via `gh` with `push:false` — measured fresh on 0.19.1302, the check that fires is installation membership, not a push check, and it is not the #13306 in-installation staleness family (the repo is permanently outside the installation) | [rjwalters/loom#13978](https://github.com/rjwalters/loom/issues/13978) |
| #1110 upstream filing, 2026-11-23 | Judge's docs-only fast path is unreachable on `gh` 2.102.0: `docs_only_fast_path_worklog_check()` (`defaults/.claude/commands/loom/judge.md`, added 2026-11-19 by `154e58de7`; `1200410` on `2AMLogic/loom` `origin/main`, fetched 2026-11-23) reads the PR's base repository with `gh pr view --json baseRepository`, a field this `gh` rejects (`Unknown JSON field`), so the fail-closed read answers `NOT ELIGIBLE: base repository read failed` for every docs-only PR: the read is broken on this client, not the PR, and the string names the read. The upstream test stays green because its `gh` stub answers `pr view` whatever field is asked; `champion-pr-merge.md` has no base-repository read to mirror. Workaround, still forge-sourced: `gh pr view <n> --json url,isCrossRepository` | **not filed** — operator step: every read of `rjwalters/loom` from the dispatch host was shed on 2026-11-23, so the duplicate search could not run and nothing was filed blind. The ready-to-file text is in a comment on #1110. No second `gh` build was at hand, so whether a newer `gh` exposes the field is untested |
| #1653 upstream filing, 2026-11-28 | consumer installs keep the pre-transfer owner slug `rjwalters/loom` in five install-time files (`.loom/README.md`, the dated guides `.loom/CLAUDE.md` and `.loom/AGENTS.md`, `.loom/config/skill-routes.json`, `.loom/config.json`) although the templates no longer carry it at `04bbcca88`: `resync-installed.sh` has no `.loom/README.md` surface and never re-renders a present dated guide, the two config paths are install-time-only by design, and nothing looks, since `tests/install/test-no-old-owner-slug.sh` scans only Loom's own tree and `doctor` has no stale-slug line for a consumer; `reflection.upstream_repo` is read by nothing. Read from source at `04bbcca88` and from the installed script; not executed. Consumer side: #1653, twelve hand substitutions | [2AMLogic/loom#15718](https://github.com/2AMLogic/loom/issues/15718) |
| #1614 upstream filing, 2026-11-28 | `config-resolver.sh` (loom-daemon 0.20.37, commit `6342be7`) hands each config tier to `jq` as ONE `--argjson` argument, and Linux caps a single argument at 32 pages (131,072 bytes) whatever `ARG_MAX` is: with a `defaults.json` of 208,716 bytes (138,054 compact; `autonomous.sessionReconcile.privateClones` alone is 107,674) line 135 prints `/usr/bin/jq: Argument list too long`, `worktree.sh` continues, and `create-issue.sh` resolves an empty config and exits 126, unfiled -- a status outside its documented 0/1/2/3/75. Two more entry points, same defect (#1828): `create-pr.sh` exits 126 with no PR -- PR #1746's body, same step on the same loom-daemon 0.20.37 and 208,716-byte `defaults.json`, cleared by the workaround below, and again on `loom-crew-1` while recording this (exit 126, no forge call; cleared the same way; `defaults.json` 208,282 bytes) -- and `.loom/scripts/lib/worktree-root.sh` prints the same line and then returns the correct root -- reproduced 2026-11-28 while judging #1746, and again on `loom-crew-1` while recording this (exit 0, `defaults.json` 208,282 bytes). Raising `ulimit -s` does not help (measured). Workaround, no installed file changed: `LOOM_CONFIG_DEFAULTS_FILE` pointed at a copy without `privateClones` (30,369 compact), after which both filings succeeded | **not filed** -- operator step: `create-issue.sh --repo rjwalters/loom` is refused by the #9548 write guard (the #877 row above, rjwalters/loom#13978), so nothing was filed by another path. The ready-to-file text is in a comment on #1614, with a second observation from the same filing (the duplicate backstop blocking a sibling of one defect family on its shared boilerplate) |
| #1705 finding, 2026-11-28 | `worktree.sh` `_wt_lease_claim` (line 1559 on `origin/main` `73ff5717`; loom-daemon 0.20.37, commit `6342be7`) leases a self-claimed issue with `lease ensure --watch-pid "${LOOM_AGENT_SESSION_PID:-${CLAUDE_PID:-$PPID}}"`. On that build the guarded opencode harness exports `LOOM_NATIVE_WORKER_PID` but not `LOOM_AGENT_SESSION_PID` (`harness.rs` line 91 at v0.20.37 sets only the first; the same in this issue's own tool environment on `loom-crew-2`), so an untargeted claim falls through to `$PPID`, the tool-call shell the harness reaps when the call returns. The renewer stops at its next `pid_is_live` wake (`lease-renewal.md` "Watched PID died") and `sweep-lease-fence.sh check` then exits 3 (EXPIRED) before push: on the #1600 claim, per #1705, the last renewal was 13 min after the call and the fence aborted 23 min later, so finished work could not be pushed from that session. Daemon-dispatched claims are not affected -- `LOOM_SWEEP_LEASE_RENEW_DISPATCHED` already runs a renewer on the session pid and `lease ensure` starts none. Workaround on that build, no installed file changed: `LOOM_AGENT_SESSION_PID="$LOOM_NATIVE_WORKER_PID" ./.loom/scripts/worktree.sh N`, which supplies the launch pid that #15465 later exports under the first name (not exercised here: this issue's own claim was daemon-dispatched) | [2AMLogic/loom#15455](https://github.com/2AMLogic/loom/issues/15455) -- already filed upstream by the fleet identity on 2026-11-26, before #1705 was raised, and fixed the same day by [2AMLogic/loom#15465](https://github.com/2AMLogic/loom/pull/15465) (`79d9fcb2c39a`, merged 2026-11-26T15:39Z), which makes `Harness::command` export the launch pid as `LOOM_AGENT_SESSION_PID` beside `LOOM_NATIVE_WORKER_PID`. Read at the tags on 2026-11-28: `harness.rs` has no such export at v0.20.37, v0.20.40 or v0.20.41, and has it from **v0.20.42** through v0.20.51 -- v0.20.41 shipped 3 min after the merge without it, so the tag, not the merge date, is what to check. This host still runs 0.20.37: the open step is a worker-spec pin bump to v0.20.42 or later, not a filing. Not covered by #15455 (its own Out of Scope): the `$PPID` fallback order in `worktree.sh`, and launchers that do not go through `Harness::command`. #1705's premise that the fleet identity cannot write to `2AMLogic/loom` (per #1337) does not hold -- #1337 is about `rjwalters/loom`, and #15455 is that identity's own issue there. Duplicate search over `2AMLogic/loom` (`watch-pid`, `PPID`, `LOOM_AGENT_SESSION_PID`, `opencode lease`): #15455 is the only match for this defect |
| #2029 upstream filing, 2026-11-28 | Guide's WORK_LOG presence check is spelling-exact: `work_log_has_issue()` in `guide.md` (`grep -qE "^- \*\*Issue #${1}\*\* "`; installed copy lines 1181 (issues), 1166 (PRs) and 1792 (the documented write format) on loom-daemon 0.20.37, commit `6342be7`; the same three lines unchanged at `2AMLogic/loom` main `3767e654`, 1343/1328/2177) matches `- **Issue #N** (closed):` and cannot match `- **Issue #N (closed)**:`, so the tick behind PR #1941 (head `91cf3598`) re-appended 14 closed issues that `origin/main` already carried in the second spelling: #1221, #1263, #1289, #1297, #1301, #1302, #1306, #1315, #1319, #1323, #1325, #1329, #1331, #1338. The other 4 of its 18 records in `### 2026-11-25` were genuinely new (#1317, #1326, #1222, #1227). Measured: the check run verbatim against main's `WORK_LOG.md` says absent for 14 of 14 numbers that are present, and present for a first-spelling control (#1294); the section diff from main `60bb3756` to `91cf3598` is 18 lines added, 0 removed. The second spelling was itself written by two Guide `docs:` commits on 2026-11-25 (`0858bf5d` 0 to 3 bullets, `6a52d828` 3 to 14); why they wrote it is not measured. The first report's claim that the 18 were filed under `### 2026-11-26` was not reproduced at `91cf3598`: all 18 are under `### 2026-11-25`, so the defect is the duplicate, not the section. Detector with the real sections as fixtures: `tools/check_worklog_18.py`. Not covered by 2AMLogic/loom#6087, which fixed the opposite (false-positive) direction and left this anchor; 2AMLogic/loom#5539 introduced the check. The filing itself hit the #1614 row above on the same host (exit 126, nothing filed; verified by title search before retrying) and succeeded with that row's workaround | [2AMLogic/loom#15820](https://github.com/2AMLogic/loom/issues/15820) |

---

## 1. Filed

### klayout-tools#2088 — `klt synthesize` has no `gf180mcu_fd_sc_mcu7t5v0` entry in any per-library table

**New.** klt `0.5.0+g604c4fb8a1ce`, PDK `gf180mcuD` @ ciel `54435919…`,
`gf180mcu_fd_sc_mcu7t5v0`, `tt_025C_5v00`. Source checked on klayout-tools
`main` @ `c28dc868`.

#1649 added the 7-track library to `klt place-and-route`. `klt synthesize`
never got the same treatment: `grep -c mcu7t5v0 synthesize.py` = **0**, the same
grep on `place_and_route.py` = **30**. `_ABC_CONSTR_INPUTS` (`:341`) and
`_TIE_CELLS` (`:487`) carry the 9-track library only, keyed on the exact name.

Consequence, all from one missing dict key, with our own run's committed
artefacts as evidence:

- the generated `synth_ladder_dp.run.ys` has **no `-constr`**, **no
  `setundef -zero`**, **no `hilomap`**, and no ABC log;
- the response is `status: "ok"`, `warnings.total: 0`, **`timing: null`** — for
  a request that supplied `constraints.clock_period_ns = 81.38`;
- all 34 mapped cell types are `_1` drive, which is unconstrained `abc -liberty`;
- bare constant assigns survive → `[ERROR DRT-0305]` downstream (#2085 item 2);
- **#2006's `setundef -zero` fix is inert on this library**, because it is
  scoped to the same `_TIE_CELLS` condition — so #1973's Failure 2 is still
  reachable on current `main` for any 7t design.

The silent half is the dangerous half: a hollow `timing: null` inside an
`"ok"` envelope is indistinguishable from "this design has no timing to
report". Same family as #1988, in a verb #1988 does not cover.

Filed with the three table entries sourced against the installed PDK
(`__tieh` pin `Z` `function : "1"`, `__tiel` pin `ZN` `function : "0"`,
`__buf_4` pin `I` `capacitance : 0.009315` under `capacitive_load_unit(1, pf)`),
and with the cheapest fix regardless of the tables: **warn when `cell_library`
matches no table.**

**Retired 2026-11-13/14, measured at klt `0.9.2.dev986+gaadc1d351523` and
again at `0.9.2.dev1029+g76f47afa48f1` (#765).** The three patches in
`pnr/klt/ladder_dp/run-klt.sh` are gone and `pnr/klt/ladder_dp/run_klt.py` runs
the flow with none. Re-run on the same PDK the original used (`gf180mcuD` @
ciel `54435919…`), so klt is the only variable that moved -- and the two klt
builds emit a **byte-identical** netlist
(`sha256:ea7cc228fd4013e7640f87078a1f0f4907b94565f53a21f153c0b4c5f25201c8`),
so the counts below are not one build's accident. (Those committed records
were the `dev1029` bounded run until #808 replaced them with the finished klt
`0.9.0` route; the `dev1029` versions are
`git show 64212ad0:pnr/klt/ladder_dp/<file>`.)

The generated `synth_ladder_dp.run.ys` now carries all three things
this finding said were missing for the 7t library -- `abc -constr
ladder_dp_abc.constr -D 81380`, `setundef -zero`, and `hilomap -hicell
gf180mcu_fd_sc_mcu7t5v0__tieh Z -locell gf180mcu_fd_sc_mcu7t5v0__tiel ZN` --
and in the netlist each patch's target is now **zero**:

| patch's target, counted in `ladder_dp_synth.v` | klt 0.5.0 | klt 0.9.2 |
|---|---|---|
| `signed` port/wire declarations (patch 1, `STA-0171`) | present | **0** |
| bare `assign x = 1'h0;` constants (patch 2, `DRT-0305`) | 3 | **0** |
| all-x `assign` with no load (patch 3, same GROUND net) | 2 | **0** |
| klt's own `__tiel`/`__tieh` instances | 0 | **59** |

**These counts are re-measured on every run, not quoted from here.**
`run_klt.audit_retired_patches` runs each retired patch's *own* matcher over
the netlist klt emitted, where the patch used to run, and the run FAILS in a
second if any of the three is non-zero -- otherwise a regression would surface
where it originally did, hours into OpenSTA or TritonRoute as an opaque error
with nothing pointing back at the netlist. Its control is the netlist the
patches were written for, which must turn all three red
(`test_the_audit_turns_red_on_the_netlist_the_patches_were_written_for`). What
it cannot see is stated with it: patch 2 only ever matched **1-bit** constants,
so a wider constant assign is the same defect class in a shape that patch never
handled -- counted and reported next to the gated counts, never gated on.

**`STA-0171` is cleared live, not only by counting.** The `dev1029` run
reached `cts` with `setup_violation_count: 0` and `hold_violation_count: 0`
at both `place` and `cts`; #808's committed `0.9.0` route goes further --
`route` completes with 0 setup and 0 hold violations at all three corners
(`par_response.json` `stages[]` and `corners[]`). OpenSTA read the
unpatched netlist and built a timing graph from it, end to end. That is the
error patch 1 existed to avoid, and it does not occur.

The silent half is fixed too: `timing` is no longer `null` inside an `"ok"`
envelope -- the same request now reports `critical_path_ps: 30288.33` against
its `delay_target_ps: 81380` -- and `provenance` carries `klt_git_commit` and
`klt_reinstall_spec` alongside `klt_version`, which is #2090.

Not the same netlist: `instance_count` went 5328 -> 5703 and `area_um2`
113292.0768 -> 117230.2656, which is what applying `-constr`, `setundef` and
`hilomap` to this library costs. klt `0.9.0` (#808) lands on exactly those
numbers -- `instance_count: 5703`, `area_um2: 117230.2656`, the same as
`dev1029` -- though the netlist sha differs (`a3f5d1ad…` vs `ea7cc228…`; the
text carries each run's own uuid paths). The committed `synth_response.json`
is now the `0.9.0` run; the `dev1029` one is
`git show 64212ad0:pnr/klt/ladder_dp/synth_response.json` and the 0.5.0 one
is `git show b9930a4a:pnr/klt/ladder_dp/synth_response.json`.

**Finished route, 2026-11-18 (#808): the record now establishes `DRT-0305`
measured gone on a completed route.** The bounded-at-`cts` record above is
superseded. Job `rs-parasynth808-od2-s00` (on-demand `m6i.4xlarge`, attended
operator session after #856 -- this repo has no reachable pinned build box
from a dispatch worker) ran `run_klt.py` unbounded, `target_stage route`,
exit 0: synth 63 s, place-and-route **4261 s** (the ~5 h estimate was
pessimistic), no `BOUNDED RUN` line, the retired-patch audit again 0/0/0
with klt's 59 own tie cells, and **no `DRT-0305` and no `STA-0171`** anywhere
in stderr (empty) or any collected stage log -- the only textual matches are
the audit's own label lines. `par_response.json` (42,537 bytes) parses and
reports `stage_reached: route`, `status: ok`, `flow__errors__count: 0`,
antenna violations 0, setup and hold violations 0 at all three corners,
`timing__fmax` 29.45 MHz, worst setup slack 16.30 ns at `ss_125C_4v50`.

**The route is at klt `0.9.0` (`klt_git_commit 5dbbad05…`, klayout 0.30.13,
OpenROAD `26Q4-1310-g98c6045c72`), not the `0.9.2.dev1029` the retirement
was measured at** -- the job image carries 0.9.0, and the issue named the
newer build, so every statement in this section is a statement about `0.9.0`
and is labelled as such; it is not generalised to later builds. One
observation the version drift buys: `provenance.pdk.compatibility` in the
committed `par_response.json` records klt 0.9.0 *expecting* open_pdks
`f6eeac7d…` -- the shuttle/LibreLane lineage's pin -- and marking the
deliberately chosen `54435919…` pin `status: "mismatch"` while running
anyway: the two-pin split of #808's correction 6, visible inside the tool
itself. The mismatch is recorded in the response, not refused, and the pin
that ran is the `ladder_dp` lineage's.

**The residual violations, classified and then answered (#808, #1006).**
`route_drc_violation_count` is **24**, and all four readings agree: the <!-- claim: grep='^  "route_drc_violation_count": 24,$' in=pnr/klt/ladder_dp/par_response.json -->
report's own records (24), the metrics keys (`route__drc_errors` 24,
`route_pass:0__route__drc_errors` 24, each occurring once, `json.loads`
agreeing), and the response-side count. Every violation is `Metal Spacing`
between a signal net and `VSS`, every one on `Metal2`, none involves `VDD`,
and they are a small family of near-identical patches (0.2-0.28 ×
0.138-0.268 µm). The per-iteration metrics show 24 from iteration 4 onward
-- with brief transient bumps (32 at iteration 17, 27 at 25, 25 at
41/49/57) -- and 24 at the final iteration 60: the router did not clear
them; both artefact-only readings (the series and the byte-identical
`ladder_dp_route_pass_0_drc.rpt` / `ladder_dp_route_drc.rpt` pair) are now
committed in-repo as `route_drc_extract.json`, bound by sha256 and checked
against `route_drc_classification.json` by
`check_route_drc_extract.py` (#1006: a number whose inputs are a gitignored
`.klt/` tree and a bucket that expires is a claim, not evidence).
`classify_route_drc.py` (committed with its validation cases; output
recorded in `route_drc_classification.json`, which carries the sha256 of
its inputs) classifies each site **unclassified, reason "mixed power (VSS)
and signal srcs"** -- neither bucket is forced.

**Signoff probe attempt, 2026-11-21.** The dry run listed the routed GDS
and Verilog in the output specification for `rs-parasynth1085-84ece5e0`.
The live upload exited 1 with `The config profile (batch-runner-submit) could not be found`
before a job launched. DRC deck coverage and confirmation of the 24 sites,
LVS body and power connectivity, and ERC results for VDD and VSS all await
the first run on the collected layout. [Sub-block probe #1085](https://github.com/2AMLogic/gf180-parasynth/issues/1085)

**Design-versus-router, answered from the routed DEF (#1006).** The DEF was
deliberately not collected by the original job, so #1006 re-ran the route
at the *same* klt -- same pinned heavy AMI (`ami-08688417b66e0c74d`, klt
0.9.0, Yosys 0.68+, OpenROAD `26Q4-1310`, ciel 3.2.0, 16 vCPU, PDK pin
`54435919…`, source netlist sha `a3f5d1ad…`; the tool probe, `nproc` and
the PDK pin are committed in `route_def_manifest.json` with the re-run's
sha256 bindings, but the AMI id and the re-run's netlist sha stay prose,
not bound in-repo #1011) -- as job
`rs-parasynth808-def1006c-s00`, and it **reproduced the original route**:
the re-run's `ladder_dp_route_drc.rpt` is byte-identical to the committed
record's binding (`be35b9d9…`), its DEF is the original's exact byte size,
and the route-pass pair is byte-identical again. So the re-run's DEF is the
DEF of these 24 violations, and `analyze_def_sites.py` (committed, its
parser pinned by a synthetic mini-DEF; output recorded per site in
`def_site_analysis.json`, bound by sha256 to the DEF, the report and the
par response) reads it:

- the VSS `Metal2` bounding **every one of the 24 sites** is **pdngen's own
  M1->M4 connect geometry**: the `add_pdn_connect -layers {Metal1 Metal4}
  -max_columns {5} -ongrid {Metal2 Metal3 Metal4}` via-column lands
  (STRIPE-tagged via pads -- the tag is on the record's `pad`/`via_pad`
  entries since #1011, per-site minimum via-pad gap 0-48 DBU at the
  bbox at **17 of 24 sites**, 1168 DBU = 0.58 µm at **the other 7**;
  measured two ways -- from the committed record, whose
  `power_shapes_in_window` is sorted ascending before its 8-entry
  truncation so each per-site minimum is exact, and untruncated from the
  DEF -- and both give 17/7; the record carries <!-- claim: test=pnr/klt/ladder_dp/test_def_site_split.py::test_split_is_17_close_and_7_at_1168 -->
  `power_shapes_in_window_total` beside the truncation since #1011) fused
  with the DRCFILL-tagged landing strips
  pdngen draws over the same columns (gap **0** at all 24 -- the strip edge
  exactly bounds each bbox);
- **both shapes pre-date detailed routing.** A cts-bounded re-run (job
  `rs-parasynth808-def1006d-s00`) collected the pre-route
  `ladder_dp.cts.def`, which already carries the same strips and lands
  coordinate-identical, and the collected per-stage Tcl files show **no
  metal-fill command anywhere in the flow**: `pdngen` is the only command
  that writes special-net `Metal2`. The DRCFILL tag is pdngen's own labelling
  of its landing metal, recorded as found;
- the router placed the signal side against this obstruction and could not
  close the spacing across 60 iterations (per-site minimum via-pad gap
  0-48 DBU at 17 of the 24 sites). The router did not create the VSS metal
  it violates against. The governing `Metal2` spacing rule value is *not*
  bound here -- no tech LEF was reachable on the hosts this record was
  written on, so no rule number is asserted; what is measured is the gap
  itself, per site, in `def_site_analysis.json`.

Two limitations of the instrument, recorded rather than discovered later:
`signal_overlaps_power` is a **lower bound** -- regular `NETS` carry no
width in DEF, so signal wires are modelled as zero-width centrelines and
real metal edges can overlap power where a centreline does not (nothing
above rests on `overlap == false`; stated in the record's own `what` since
#1011, so the limitation travels with the evidence); and the parser counts the DEF
path-point extension form `( x y ext )` as **unparsed** -- the committed
record's `def_parse` shows 452 unparsed tokens with samples in that shape,
and every one of its `unparsed_samples` entries names a signal net while
`specialnets_parsed` is 2 (the VSS side the conclusion reads). The
token-level decomposition of those 452 and the section split behind it are
measured on the DEF itself, which is not in-tree; #1011 carries what was
measured there and the regeneration the parser fix needs.

So design-versus-router resolves to **the PDN's**, and the separate
floorplan issue #808's criteria anticipate is **filed on this evidence**
(#1009): it names the 24 sites and the `add_pdn_connect` line that shapes
them, and the decision it reached is recorded below -- the lands are
**accepted as known-PDN residual**, the connect-geometry sweep deferred.
This record keeps the classification `unclassified` (the classifier
classifies by nets and stays neither-nor); the conclusion above is a
reading of `def_site_analysis.json`, recorded there per site.

The lead this resolves is the 2026-11-14 container sighting (#5726's shape:
a route whose only record was an orphaned container, read as 9 residual
Metal Spacing violations at fmax 28.04 MHz), resolved by the attributable
route, not inherited: its *shape* (Metal Spacing, signal-vs-`VSS`, all
`Metal2`, surviving every remaining iteration) is confirmed; its *count*
(9) is superseded by 24, and its `fmax` 28.04 MHz and via/wirelength
figures came from the unattributable container, so nothing else from that
sighting carries forward as evidence.

**Decision, 2026-11-18 (#1009): the 24 `Metal2` spacing sites are accepted
as known-PDN residual -- a documented trade, no geometry change.** The
connect-geometry sweep (`max_columns`, then the `-split_cuts` width) is
**deferred, not rejected**: it is filed as a follow-on only when a named
sound or player requirement needs a DRC-clean `ladder_dp` route. That
trigger is deliberate, and the reason the trade goes this way: **no sound
or player requirement currently needs these sites fixed.** Nothing in the
current milestone (#282, checked 2026-11-18: no mention of the
`ladder_dp` route, DRC or PDN in its body) or the FPGA delivery path rests
on this route being DRC-clean, and ASIC shrinking is deferred (#33). At
52.3% utilization (`par_response.json` `utilization_pct` 52.3365) <!-- claim: grep='^  "utilization_pct": 52.3365,?$' in=pnr/klt/ladder_dp/par_response.json --> with
**0 antenna, 0 setup and 0 hold violations** (`antenna_violation_count`, <!-- claim: grep='^  "antenna_violation_count": 0,?$' in=pnr/klt/ladder_dp/par_response.json -->
`setup_violation_count`, <!-- claim: grep='^  "setup_violation_count": 0,?$' in=pnr/klt/ladder_dp/par_response.json -->
`hold_violation_count`, <!-- claim: grep='^  "hold_violation_count": 0,?$' in=pnr/klt/ladder_dp/par_response.json -->
all 0, setup and hold at
all three corners), there is nothing to spend on the trade and nothing it
would buy.

What is accepted, per `pnr/klt/ladder_dp/def_site_analysis.json`
(`verdict_counts` `{"fill_wire_spaced": 24}`, bound by sha256 to the <!-- claim: grep='^    "fill_wire_spaced": 24$' in=pnr/klt/ladder_dp/def_site_analysis.json -->
routed DEF, the DRC report and `par_response.json`): 24 sites, all
`Metal Spacing`, all `Metal2`, each between a signal net and `VSS`; the
`VSS` shape at every site is the PDN's own M1->M4 connect geometry, with
the per-site minimum via-pad gap 0-48 DBU at **17 of 24** sites and 1168
DBU = 0.58 µm at **the other 7**, and the DRCFILL-tagged landing strip
bounding every site at gap **0** (the PDN attribution and its evidence
files are the #1006 record above). **No `Metal2` rule value is quoted
here, and none was before**: no tech LEF was reachable on the hosts these
records were written on, so what is measured is the gap itself, per site;
reading `LAYER Metal2 SPACING` from the tech LEF at the pinned PDK and
restating any margin against it is part of the deferred sweep's work, not
part of this acceptance. Nothing is filed upstream -- this is this
repository's floorplan, not a klt finding. The committed
`route_drc_classification.json` stays `unclassified` and the extract
records (`route_drc_extract.json`, `check_route_drc_extract.py`) are
unchanged by this acceptance: it re-derives nothing.

### klayout-tools#5577 — `klt synthesize`'s netlist path carries a per-run uuid, so no committed `klt place-and-route` request can name it

**New, 2026-11-13.** klt `0.9.2.dev986+gaadc1d351523`
(`klt_git_commit` `aadc1d351523`), PDK `gf180mcuD` @ ciel `54435919…`,
`gf180mcu_fd_sc_mcu7t5v0`, `tt_025C_5v00`.

Found by #765 while retiring the #2088 workaround: with the patches gone, the
re-run died in **one second**, before OpenROAD, with

```
{"schema_version": 1, "error": {"command": "place-and-route",
 "message": "netlist not found: .klt/synthesize/ladder_dp_synth.v"}}
```

`klt synthesize` now writes into `.klt/synthesize/<run_id>/`, and `run_id`
defaults to `f"run-{uuid.uuid4().hex}"` (`synthesize.py:1236`), so the path is
unpredictable per invocation. Pinning `run_id` in the request does not fix it:
`_create_run_directory` (`synthesize.py:1234`) claims the directory with
`os.mkdir` and refuses an existing one — "each invocation needs a new run_id
(prior evidence is retained)" — so a pinned path works **exactly once**. There
is therefore no value of `netlist` a repository can commit and run twice.

Same family as #2085 ("klt synthesize emits netlists its own place-and-route
cannot consume"), for the netlist's *path* rather than its contents: #2085's
three content defects are genuinely fixed, and this is what the next caller
hits instead.

Workaround in our tree, and it is a workaround: `pnr/klt/ladder_dp/run_klt.py`
reads `netlist_path` out of the synthesize response and substitutes it into a
derived place-and-route request. Filed with three fixes ranked by cost, the
cheapest being a `synthesize_report` request field so place-and-route can name
the *report* rather than the netlist.

### klayout-tools#5726 — the OpenROAD container outlives the caller, so a killed klt leaves an orphaned route writing unattributable artefacts

**New, 2026-11-14 (#765).** klt `0.9.2.dev986+gaadc1d351523` and
`0.9.2.dev1029+g76f47afa48f1`; `openroad` =
`openroad/orfs@sha256:bb7f3169…` (`26Q3-1510-g6cb3f2b704`) through the wrapper
klayout-tools' own `scripts/install-openroad-docker.sh` generates.

`openroad` on the provisioned hosts is not a binary: it is a containerised
wrapper, and `_openroad_engine.py` drives it with `subprocess.run` (lines 226,
272). Kill the **klt process** -- a caller's timeout, or a sweep reaping its
agent -- and the container client dies while the container itself does not.
OpenROAD finishes the route and writes `*.def`, `*.odb`, `*_route_drc.rpt` and
`*_route_metrics.json` into the caller's repository with no response, no stage
`commit.json`, and no live parent.

Twice in this directory, and both times into git-tracked space:

| | response | route artefacts | gap |
|---|---|---|---|
| 2026-09-18 | `par_response.json`, 0 bytes, 01:37 | DEF/ODB/DRC/metrics 15:57 | 14 h 20 m |
| 2026-11-13 | `par_response.json`, 0 bytes, 16:15 | DEF/ODB/DRC/metrics 23:19 | 5 h 03 m |

The 2026-11-13 chain is fully reconstructable: the route stage's
`openroad-logs/<id>/stdout.log` stops mid-`[INFO DRT-0195] Start 27th stubborn
tiles iteration` at 19:06, when its klt was reaped, and has **no
`invocation.json`**; the DEF (10.6 MB), the as-built Verilog, the route ODB,
the DRC report and the metrics all carry mtime 23:19 and are owned by `root`,
the container's user. On the same host, two containers of that image were still
running **13 days** after the sweep that started them ended.

Why it is klt's and not merely a container fact: it is klayout-tools' own
wrapper and recommended install path; a correct caller doing
`start_new_session` + `os.killpg` still cannot reach a container; and it
undermines klt's own preconditions -- `--resume` reuses "each leading stage
whose `commit.json` matches and whose artifacts are on disk", which after a
killed run may be racing a live writer with no way to detect it. Filed with
four fixes ranked by cost, the cheapest being three lines in the wrapper: a
`--cidfile` plus a `trap` that stops that id on `EXIT INT TERM`.

Workaround in our tree, and it is a workaround: `run_klt.py` runs klt in its
own process group, kills the group on timeout, then stops the OpenROAD
containers that are demonstrably its own -- ownership being an absolute path
inside the run's directory in the container's argv, deliberately **not** a
bind mount, because the wrapper mounts `$PWD` and a mount test on a shared
host would match a container started from a parent directory and stop another
agent's run.

### klayout-tools#2089 — `detailed_route` runs twice into the same `-output_drc` path, so the DRC count comes back doubled

**New.** Same environment. Root cause in klt source: one `detailed_route_call`
string (`place_and_route.py:3683`) emitted twice — once after `global_route`
(`:3720`) and once per antenna-repair iteration after `repair_antennas`
(`:3743`) — with the **same** `-output_drc` and `-output_maze` paths. Visible
verbatim in the committed `.klt/place-and-route/pnr_ladder_dp_route.tcl`.

Measured on our committed artefacts:

| artefact | value |
|---|---|
| `ladder_dp_route_drc.rpt` — `violation type:` records | **46** |
| same file — distinct `(type, srcs, bbox)` | **23**, every one appearing exactly twice, always as an adjacent pair |
| `_count_route_drc_violations` (counts header lines) | returns **46** |
| `ladder_dp_route_metrics.json` | one object, **97 duplicate keys**; `route__drc_errors` = **23** (line 118) and **46** (line 216) |
| what `json.loads` / `JSON.parse` sees | **46** |

`route_drc_violation_count` is a `_TOP_LEVEL_METRIC_KEYS` field (#938) and is
the only routing-DRC number klt produces. A clean design is unaffected (0
doubles to 0), so the error appears exactly when the number is being consulted.
With `max_antenna_repair_iterations = N` the multiplier is N+1.

Independently sighted under plain ORFS on the same platform: our `synth_top`
run's "2 detailed-route DRC violations" are **one** site listed twice
(`docs/pnr-synth-top.md` §3).

**Third data point, 2026-11-18 (#808), and it is at klt `0.9.0` -- older than
the `dev986` line whose unattributable container reading showed the
doubling.** On the finished route's artefacts the doubling is **not seen**,
in both independent places: `ladder_dp_route_drc.rpt` has **24** `violation
type:` records and **24** distinct `(type, srcs, bbox[, layer])` sites (0
adjacent duplicate pairs, multiplier 1.0), and `ladder_dp_route_metrics.json`
carries `route__drc_errors` **24** and `route_pass:0__route__drc_errors`
**24**, each occurring exactly **once** -- no duplicate keys, so what
`json.loads` sees and what the file says agree. The mechanism is
structurally absent in this build's outputs: klt 0.9.0 emits a **distinct
report path per `detailed_route` call** -- `ladder_dp_route_pass_0_drc.rpt`
(first call) and `ladder_dp_route_drc.rpt` (last) are both present and
**byte-identical, 24 records each** (antenna repair found 0 violations, so
the second pass changed nothing) -- where the defect re-used one path; the
two calls themselves are visible in the route invocation's
`openroad-logs/<id>/stdout.log` as two `DRT-0024` layer-completion
sequences. The route `.tcl` was not among the job's collected outputs, so
the emitted `-output_drc` paths could not be read directly and
`max_antenna_repair_iterations` not counted from it; the per-pass report
pair is the observed evidence. Version order does not explain the contrast:
`0.5.0` doubled (attributable), `0.9.0` does not (attributable), and the
`dev986` 9/18 sighting came from the unattributable orphaned container
(#5726, resolved above). One build's measurement, recorded as found -- do
not read it as "fixed at 0.9.0" for later builds.

### klayout-tools#2090 — `provenance.klt_version` reports the package version, not the build version

**Fixed upstream; confirmed 2026-11-14 (#765).** The committed
`synth_response.json` now carries `"klt_version":
"0.9.2.dev1029+g76f47afa48f1"` plus `klt_git_commit` and
`klt_reinstall_spec`, so the identity is in the record without our wrapper
having to recover it. What follows is the original finding, kept because the
harm it did is the reason the record is checked at all.

`klt version` printed `0.5.0+g604c4fb8a1ce`; the then-committed
`synth_response.json` said `"klt_version": "0.5.0"`.
`build_identity.build_version()` (#1202) exists precisely for this and is wired
only into the CLI banner (`cli/__init__.py:22`, `cli/parser.py:111`);
`_provenance.py:356` → `_klt_version()` returns `klayout_tools.__version__`,
which `build_identity.py`'s own docstring calls *"correct for packaging and
useless for identity"*.

Demonstrated harm, ours: **#2085 was filed with two items that were already
fixed**, because `"0.5.0"` is true on both sides of #2006 (`652d1536`, landed
the day after the build we ran). We only recovered `604c4fb8` because our
wrapper happens to run `klt version` into `run.log`. Sibling of the open #2035.

### klayout-tools#2085 — comment: correction and narrowing

Our own earlier filing, corrected. Items 1 (`signed`/`STA-0171`) and 3
(dangling all-`x`) are already fixed by #2006, which landed after our build.
Item 2 (`_TIE_CELLS` 9t-only) is **still live at `c28dc868`** and additionally
makes item 3's fix inert for 7t — carried forward as #2088.

Also added: the `signed`/`STA-0171` failure **reproduces outside klt**, under
plain ORFS on the same RTL and platform (workaround:
`pnr/orfs/gf180_7t/synth_unsigned.tcl`). It is a yosys-`write_verilog` /
OpenSTA-reader interaction, not a klt-specific defect — which supports #2006
doing the rewrite inside klt.

### klayout-tools#2086 — comment: the positive control, and a detector

Added the counts from klt's own DEF for the run **with** a power block — 9,789
fillers, 244 `filltie`, 240 `endcap`, 2 SPECIALNETS — which are already in the
DEF klt writes and reach no response field. That is issue #2086's suggestion 3,
costed.

Also offered `pnr/orfs/check-pdn.py` as a spec for the refusal: ~70 lines of
pure DEF text parsing, exits 1 on no `FOLLOWPIN` rails, no straps above Metal1,
no PDN vias, no tap/endcap, or no fillers.

Stated honestly in the comment: our committed artefact is the **positive**
case. The negative case is a recorded observation, not a kept artefact.

---

## 2. Looked at and not filed, with the reason

### Real defects, but the upstream is not klayout-tools

Filing these into `klayout-tools` would be wrong-repo noise. Recorded here with
the correct home so the next pass does not re-derive them.

| Finding | Evidence | Upstream |
|---|---|---|
| **ORFS finish `6_report.json` writes `design__instance__area` twice with two different meanings** (filed 2026-11-15 from issue #835). `report_cell_usage` emits the bare name for its fill-inclusive census total (92,248 fill cells included: 3382067.81 µm²), then ORFS's `report_metrics` → `report_design_area_metrics` → `odb::blockMetrics` emits the same bare name for the filler-excluded active area (2033108.63 µm²); the metrics writer serialises its entry list verbatim (`MetricsEntry::assembleJSON`) and ships its KeepFirst/KeepLast/Remove dedup machinery dormant (`makeDefaultPolicies()` returns empty), so `json.load`'s last-wins silently keeps the second. A first-wins and a last-wins reader disagree about cell area by 66 % with neither erroring, and every archived ORFS run here carries it — both `synth_top` runs, plus `detailedroute__route__drc_errors = [1, 2]` and `detailedroute__route__wirelength` duplicated in `5_2_route.json` (the klayout-tools#2089 family, covered by the same guard for free). Emitting source confirmed **by reading the producer** at OpenROAD `26Q3-1260-g06a5a02279` (`dbSta.cc` `reportCellUsage:780`, `odb/src/db/util.cpp` `blockMetrics:487`, still on `master` @ `806decf02`); the original issue's "core area emitted under the instance-area key" framing was refuted before filing — this row's own second framing was refuted in turn by OpenROAD#12307's correction of record: the equality with `finish__design__core__area` is a *real identity* (fillers tile the core, so each run's `CORE_AREA` w×h equals its log cell-usage total at full precision — 1839.60 × 1838.48 = 3382067.808), not the 6-sf coincidence first written here. `pnr/orfs/summarize.py` now records duplicates and refuses (`duplicate-metric`) rather than publish a number attributable only to emission order. | `pnr/orfs/evidence/synth_top/joined-d1e5068/6_report.json` + `6_report.log`; `pnr/orfs/test_summarize.py` | The-OpenROAD-Project/OpenROAD [#12303](https://github.com/The-OpenROAD-Project/OpenROAD/issues/12303) |
| **OpenROAD's metrics JSON serialises every float metric at 6 significant figures.** Explicit `std::setprecision(6)` in the templated `Logger::metric(std::string_view, T)` overload (`src/utl/include/utl/Logger.h:206` at `06a5a02279`; `:208` on `master` @ `77370d8802`, 2026-11-15) — not `assembleJSON`, which only formats the stored string. `report_cell_usage`'s total prints 3382067.81 µm² in the log, serialises as `3.38207e+06` (+2.19); the nine per-class areas sum to the log Total exactly (residual 0.00) yet fall **4.94 µm²** short of the stated whole after serialisation — a reconciliation gap that exists only in the JSON. Integer metrics unaffected. One-line fix proposed upstream (`max_digits10`); PR offered only if maintainers accept the golden-file churn. **Correction of record:** the `core__area` == fill-inclusive `instance__area` equality is a *real identity*, not a 6-sf coincidence as OpenROAD#12303's body says — fillers tile the core, and each run's `CORE_AREA` w×h equals its log cell-usage total at full precision (1839.60 × 1838.48 = 3382067.808 vs 3382067.81; 1293.60 × 1293.60 = 1673400.960 vs 1673400.96). | `pnr/orfs/evidence/synth_top/joined-d1e5068/6_report.log` lines 32-42 vs `6_report.json`; same shape in `placeholder-2a88c35` | [The-OpenROAD-Project/OpenROAD#12307](https://github.com/The-OpenROAD-Project/OpenROAD/issues/12307); [correction of record posted on #12303](https://github.com/The-OpenROAD-Project/OpenROAD/issues/12303#issuecomment-6960689429) (rjwalters, 2026-11-17) |
| **ORFS gf180 `TRACK_OPTION=7t` is broken out of the box.** `platforms/gf180/cells_adders.v` / `cells_latch.v` hard-code `gf180mcu_fd_sc_mcu9t5v0__addf_1`/`addh_1`/`latq_1`; with the 7t liberty yosys aborts with 414 `is used but has no driver` in `check -assert`. Workaround: `pnr/orfs/gf180_7t/`. | `docs/pnr-first-run.md` §1.1 item 1 | OpenROAD-flow-scripts |
| **IR-drop aborts at every corner but WC.** `platforms/gf180/setRC.tcl` sets via resistances only when `CORNER=WC`; at TC `analyze_power_grid` dies with `[ERROR PSM-0021] Resistance map constains invalid values.` (typo upstream's), *after* the final DEF/ODB/SPEF/netlist are written but before `report_metrics`. Exact text in `pnr/orfs/work/ladder_dp.log:778`. | `docs/pnr-first-run.md` §1.1 item 4 | OpenROAD / ORFS gf180 platform |
| **`DESIGN_CONFIG` is included before the platform config**, so platform variables set with plain `=` (e.g. `DONT_USE_CELLS = *_1`) can only be overridden on the make command line. | `pnr/orfs/README.md` | OpenROAD-flow-scripts |
| **`ecppll -i 25 -o 12.288` answers 12.5 MHz and exits 0** — 17,253 ppm, 29.7 cents sharp — without saying it has given up. Pure silent wrong answer. Workaround: `fpga/scripts/pll_search.py` (92 lines), result 725/59 MHz = +11 ppm, hand-instantiated in `fpga/rtl/ulx3s_top.v`. | `docs/fpga-clock.md:58`, `fpga/scripts/pll_search.py:8` | prjtrellis |
| **nextpnr prints "Max frequency for clock" twice and the first is the post-placement estimate**, unlabelled — and it is not a conservative bound (30.30 vs 30.49 on one design; 29.82 vs 30.88 on another). Workaround: `fpga/scripts/pnr_report.py` (`render`, formerly report.sh:54) labels both. | `fpga/reports/logs/ecp5_pnr.log:118` vs `:609` | nextpnr |
| **`nextpnr-ice40 --version` reports an empty version string** (`"nextpnr-ice40" -- Next Generation Place and Route (Version )`), so the iCE40 half of the flow is unversioned in our provenance record. | `fpga/reports/provenance.txt:7-9` | nextpnr packaging |
| **`gf180mcu_fd_io` operating-conditions table says 4.5–5.5 V DVDD while its characterization-corners page lists 3.3 V corners.** Unresolved. | `docs/DESIGN.md:331-336` | gf180mcu PDK |

### Judged not a defect

- **ORFS `LEC_CHECK=1` dies with `child killed: illegal instruction`** in CTS.
  That is a Kepler binary under amd64 Rosetta emulation on Apple Silicon — an
  **environment problem**, per the brief's own exclusion. `LEC_CHECK=0`.
- **`SYNTH_MEMORY_MAX_BITS` default 4096 rejects a 257×16 = 4,112-bit ROM.** A
  documented, tunable limit doing what it says. Raised to 65536.
- **GNU make keeps trailing whitespace before an inline `#`**, so
  `CORNER = TC   # …` silently yields `$(TC   _LIB_FILES)` = empty. Documented
  GNU make semantics, not a tool defect. There *is* an ORFS-side ask — refuse
  when `$(CORNER)_LIB_FILES` resolves empty rather than calling `read_liberty`
  with no file — but we did not keep the artefact of what that run produced, so
  it is not filable to the bar.
- **ORFS's gf180 default corner is `BC = ff_n40C_5v50`, the optimistic one.** A
  platform default, not a defect; we set `CORNER=TC` and said so.
- **`DONT_USE_CELLS = *_1`: klt allows `*_1`, ORFS excludes it**, a 22–42 %
  area difference on the same RTL (`docs/verification-rules.md`). klt's
  divergence is **deliberate, sourced and measured** — `synthesize.py:368-395`
  documents it at length under #807 (+31 % area, +12 % delay on an 8×8
  multiplier) and explains why a congestion policy does not belong at synthesis
  time. Our own +29 % to +40 % across every block on gf180 7t corroborates
  their number rather than contradicting it. Both responses carry
  `script_path`/`run_script_path`, so the policy actually applied is always
  recoverable from the artefact.
- **yosys pads inferred memories to a power of two** (MODES 9–16 cost
  identically), and **keys parameterised modules as `$paramod$<hash>\<name>`**.
  Both are correct documented behaviour. The second one produced a wrong number
  here — `drum_kit` reported at 325,388 µm² when that is the submodule
  `modal_dp` — but that was **our** extractor taking `modules.values()[0]`;
  fixed and documented in `fpga/scripts/x1_report.py`.
- **yosys optimised an all-X datapath down to 1,917 cells and reported a
  plausible area.** Correct behaviour on a design that was don't-care. **Our**
  RTL bug; it is the origin of `docs/verification-rules.md` rule 3. Reproduced
  and now permanently injected (`pnr/report_synth_area.py --inject
  TANH_INDEX_OOR`, issue #245), and the reproduction sharpens the finding: on
  the fixture the collapse is **invisible from the netlist**. It contains no
  `x`, it simulates `x`-free at the gate level against the same bench, and its
  cell count moves 0.1 % (1,677 against 1,679) — yosys resolved the don't-care
  to concrete values, as it is entitled to. The **behavioural** simulation of
  the sources is the only thing that sees it (`y` is `x` on 506 of 512 cycles).
  So "check the netlist for X before quoting its area" is not a weaker version
  of the right check; it is not a check at all.
- **`iverilog -Ptb.dut.IDX_BITS=5` silently does nothing.** `-P` assigns
  parameters of a **root module instance** only, so a hierarchical path through
  an instance is accepted on the command line, applied to nothing, and reported
  with no warning: `iverilog -g2012 -o x.vvp -Ptb.dut.IDX_BITS=5 tb.v dut.v` &&
  `vvp x.vvp` printed `IDX_BITS=4`. Icarus Verilog 13.0 (stable, v13_0).
  Documented semantics, arguably, and **our** misuse — recorded because the
  *shape* is the one `docs/verification-rules.md` rule 5 condition 2 exists for:
  an injection that never activates while every log looks clean, and the run
  reports the clean design's numbers under the mutant's name. It cost about
  twenty minutes here and only surfaced because the fixture was expected to go
  X and did not. The workaround is not to use `-P`: `pnr/report_synth_area.py`
  writes parameter overrides into a generated wrapper that **both** yosys and
  iverilog read, and refuses the run unless two independent receipts — the
  `$paramod\<mod>\<P>=…` module yosys derived, and the value the running
  simulation prints from inside the elaborated hierarchy — agree with what was
  asked for.
- **`FREQUENCY PORT "clk" 12.288 MHz` on the ULX3S's 25 MHz oscillator pin,
  with no PLL, and nextpnr reported PASS.** Documented LPF semantics — a
  `FREQUENCY PORT` is an assertion the checker verifies, not a clock the board
  produces. **Our** constraint-file bug. Recorded because the *shape* (a green
  sign-off on a hollow premise; the only tell was `EHXPLLL: 0/2`) is the class
  this ledger exists for.
- **`fpga/Makefile`'s nextpnr recipes are prefixed with `-` and piped through
  `| tail -40`**, so make sees `tail`'s status either way. **Our** bug, and
  exactly the anti-pattern `CLAUDE.md` §"Write Python, not bash" records. Not a
  tool defect; worth fixing here.
- **GitHub does not fail a PR for an unparseable workflow — it silently does
  not register it.** Real and dangerous, but not an EDA tool and not
  klayout-tools.

### Questions the brief asked, answered

**"ORFS's `gf180` platform has no KLayout sign-off DRC deck (`make drc` answers
'DRC not supported'). Is that a klayout-tools gap, an ORFS gap, or expected?"**

**An ORFS gap, and klayout-tools already fills it.** `klt drc` ships a curated
**gf180mcu** deck — `src/klayout_tools/drc.py` carries gf180mcu rules
throughout (`DF.1a`/`DF.3a` `_LV`/`_MV` voltage pairs, `MIMTM.1`, `mim.space.1`,
the `Dualgate` 55/0 marker layer), and `:1711` names `sky130`/`gf180mcu` as the
curated engine's deck identifiers. So there is nothing to file against
klayout-tools; the remedy is to run `klt drc` on the P&R output.

Not established, and deliberately not tested here (this pass does not re-run
P&R): whether that deck behaves sensibly on a full standard-cell digital GDS
as opposed to the analog blocks it was built for. That is the next experiment,
not a finding.

**"No behavioural Verilog cell models, so no gate-level simulation" — also
repeated as a v0.3 non-claim.** True of the **ORFS platform**, false of the
**PDK we already have installed**. `gf180mcuD` at ciel `54435919…` ships
`libs.ref/gf180mcu_fd_sc_mcu7t5v0/verilog/{gf180mcu_fd_sc_mcu7t5v0.v,primitives.v}`
— 964 KB, with `USE_POWER_PINS`/`FUNCTIONAL` guards and `specify` timing blocks.
klt's own `functional_verification.py` is PDK-agnostic (the caller supplies the
cell models). So gate-level simulation of a routed netlist is **unblocked
today** and needs no tool fix — nobody wired it up. `docs/v0.3-scope.md` and
`docs/pnr-synth-top.md` §8 should be narrowed from "impossible" to "not done".

### Candidates left on the table, with what is missing

- **A `klt place-and-route` report does not say what stage was *asked* for.**
  Measured 2026-11-14 at `0.9.2.dev1029+g76f47afa48f1` (#765): a request with
  `"target_stage": "cts"` produces a response carrying `"stage_reached":
  "cts"` and **no `target_stage` key at all**, and `stage_reached` is set from
  the requested stage rather than observed
  (`place_and_route.py:2302`, `"stage_reached": target_stage`). So a committed
  report cannot be told apart from one whose caller asked for `route` — the
  distinction between "bounded on purpose" and "stopped short" is not in the
  artefact, only in the caller's own log. It bites `--check REPORT` hardest:
  verifying this field against its request compares a value with itself.
  **Why not filed yet:** klt only reaches the response on success, so this is
  a report-contract gap rather than a wrong answer, and we have not checked
  whether `coverage`/`applied` is the intended place for it. **Next step:
  read `--check`'s field list and propose the echo there.** Our own driver
  does not depend on the fix — `run.log` records the override in words — which
  is exactly why this could sit unnoticed.
- **`[WARNING DRT-0349] LEF58_ENCLOSURE with no CUTCLASS is not supported.
  Skipping for layer Via1`** — and the same for Via2, Via3, Via4, in every
  routing log we have (`pnr/orfs/work/ladder_dp.log:127`,
  `synth_core.log:138`, `ladder_dp_util70.log:1020`). TritonRoute silently
  skips a whole rule class on **every** via layer of this platform, and the
  resulting count is the only DRC number this project quotes. The klt-side ask
  — surface which rule classes the router skipped alongside
  `route_drc_violation_count` — is a coverage feature in the shape of #1988,
  but we have not established what klt could feasibly capture from the router,
  so it is not filed. **Next step: establish whether the skipped enclosure
  rules are checkable by `klt drc`'s gf180mcu deck instead.**
- **`pnr/klt/ladder_dp/par_response.json` is 0 bytes.** **Half-settled by #765,
  and the half that is settled is not a defect.** The capture this entry asked
  for was made — `pnr/klt/ladder_dp/run_klt.py` records every step's exit
  status and stderr — and it shows the mechanism for an 0-byte response on a
  **failed** run: `--format json` writes the error envelope to **stderr** and
  nothing to stdout, so `… --format json > par_response.json` leaves a 0-byte
  file behind on any error. Reproduced in one second, on both verbs, with a
  request naming a file that does not exist:

  ```
  $ python3 tools/klt_error_stream_probe.py --pdk-root <pdk>
  place-and-route  exit=1  stdout_bytes=0  stderr_bytes=132  envelope_stream=stderr
  synthesize       exit=1  stdout_bytes=0  stderr_bytes=129  envelope_stream=stderr
  ```

  `tools/klt_error_stream_probe.py` is that measurement, committed, with its
  own tests — the point of this entry's history is that the number was once
  only available by accident.

  **Not filed, because it is deliberate and documented in the source**:
  `cli/output.py:139` — "``format == "json"`` writes the documented error
  envelope to stderr" — which keeps stdout clean for the success report. The
  cost is borne by the caller: a `>` redirect, which is how klt's own
  request/response pairs are driven, turns every failure into a 0-byte
  artefact. Our driver now captures both streams, so the envelope survives.

  **Settled in full, 2026-11-14 (#765), and the remaining half is not a defect
  either -- it is klayout-tools#5726.** The original claim was that the 0-byte
  file "followed a run that *had* written a routed DEF, ODB, DRC report and
  metrics", so a successful route had produced no report. Both halves of that
  are now measured:

  1. **A successful run does write a report.** The `dev1029` run's
     `par_response.json` is **25,304 bytes** and parses -- the first non-empty
     one this directory has had. So a 0-byte response cannot have come from a
     success, and the inference "the route succeeded, therefore klt dropped the
     report" was wrong.
  2. **The artefacts did not come from the run that wrote the 0-byte file.**
     `openroad` is a containerised wrapper; when klt is killed the container
     keeps routing and writes the DEF, ODB, DRC report and metrics **hours
     later, with no live parent**. The 14 h 20 m gap that made this entry
     unfilable (response 01:37, artefacts 15:57) is the signature of that
     leak, not of a dropped report -- and the committed 0.5.0 `run.log`
     agrees: it stops after `signed qualifiers left: 0` and never prints a
     `place-and-route:` timing line, because `run-klt.sh` was killed before
     reaching it.

  Reproduced on 2026-11-13 with the same shape and a 5 h 03 m gap, from a run
  of our own driver, which is what identified the mechanism. **Filed as
  klayout-tools#5726**; the evidence and the four ranked fixes are in that
  section above. `run_klt.py` now reaps its own containers on timeout, so this
  directory cannot grow another unattributable artefact set from a driver
  timeout -- only from the whole agent being killed, which is what the
  upstream fix covers.

### klt 0.8.0 -> 0.9.2 signoff pin bump, 2026-11-20 (#1084): the two named upstream cases

Checked while bumping the CI `klt` pin 0.8.0 -> 0.9.2 (issue #1084), with
`uvx --with pyyaml --from klayout-tools==0.9.2 klt signoff --attest {9,10}`
from the repository root; both envelopes re-attested green and the item-8
attempt refused as its evidence gap predicts.

- **klayout-tools#5829** (attest envelope `provenance.input` shape changed
  under an unchanged `schema_version`): **does not reproduce here.** The
  0.8.0-era committed envelopes and the fresh 0.9.2 envelopes carry the
  identical `{"path": {"path": ..., "scope": "repo"}, "content_hash": ...}`
  shape for `provenance.input` (compared with `git show
  HEAD:manifests/evidence/item-{9,10}.json` against the regenerated files).
  The shape change named in #5829 is an item-1 envelope change; this repo
  attests items 8, 9 and 10 only.
- **klayout-tools#6653** (`--attest` under uvx: inventory checks that run
  `python3 -I`): **does not reproduce here.** Neither committed inventory
  invokes `python3 -I` or the `klayout` wrapper; item 10's audit runs bare
  `python3 tools/check_workflows.py` and item 9's cold start bare
  `python3 tools/item9_cold_start.py`, both resolved by PATH.
- **Adjacent observation, recorded because it changed our workflow, not
  filed:** 0.9.2's `signoff_attest._command_env()` removes the tool venv's
  `bin` from the check-command `PATH` (deliberate: a bare `python3` resolves
  to the runner's interpreter, not the tool's), so the 0.8.0-era idiom
  `uvx --with pyyaml ...` no longer provisions the interpreter that runs the
  item-10 audit command. On a clean setup-python 3.12 without pyyaml the
  audit exits 2 ("pyyaml not installed; cannot check workflows") and the
  producer fails; observed during #1084 curation on 2026-11-20 and confirmed
  by installing PyYAML into that interpreter (producer then exits 0).
  `signoff.yml` now installs pyyaml next to uv for that interpreter. This is
  documented, intended 0.9.x behaviour, not a defect: nothing to file.

---

## 3. What this pass changes about how findings get recorded

Three of the five items above needed information that was only available by
accident:

- **#2090 exists because `provenance.klt_version` could not identify the
  build.** Our correction to #2085 was only possible because a wrapper script
  happened to run `klt version` into `run.log`.
- **The `par_response.json` candidate is unfilable because stderr was not
  captured.**
- **The GNU-make candidate is unfilable because the bad run was fixed rather
  than kept.**

So: when a tool is worked around, keep the failing artefact, capture stderr,
and record the tool's own self-reported build string next to it. A workaround
without those is a finding that cannot be filed.

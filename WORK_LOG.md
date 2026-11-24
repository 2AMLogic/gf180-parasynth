# Work log

Recorded forge events; merge or closure alone does not establish sound qualification or hardware delivery. Initial coverage: the twenty most recently merged PRs, excluding Guide maintenance, and recently closed issues since 2026-10-01. Earlier events are outside this initial log.

## Archives

- [2026-09](work_log/WORK_LOG_2026-09.md)

### 2026-11-24

- **PR #1151**: fix: refuse malformed provenance and read quoted klt pins in signoff regrade
- **Issue #1100** (closed): Fix malformed-provenance refusals and quoted-pin comparisons in signoff
- **PR #1134**: refactor: remove five unreferenced defs, moving their explanations to live code
- **Issue #800** (closed): Remove 6 unreferenced top-level defs in model/ and tools/: each is a dead duplicate of logic inlined at its call site
- **PR #1133**: ci: re-home full pulse2x phrase run into rtl-full, delete pulse_audio workflow
- **Issue #760** (closed): Retire or re-home pulse_audio.yml: its only push trigger is a branch that merged in September

### 2026-11-23

- **PR #1141**: fix: baseline refusal names pytest's real exit status
- **Issue #1128** (closed): holdout probe's baseline refusal names "pytest exited 1" when pytest exited 2 with a parsed FAILED line
- **PR #1114**: fix: ignore .loom/account-ranking.* daemon state file
- **Issue #1112** (closed): check-main-clean.sh exits 3 on every sweep: the daemon state file .loom/account-ranking.codex is not in the loom-managed .gitignore block
- **PR #1101**: test: run the pinned-klt envelope step locally, not only in CI
- **Issue #1095** (closed): the pinned-klt envelope gate has no committed control and no local half: it is runnable only by pushing to CI
- **PR #1139**: chore: regenerate loom-managed gitignore block
- **Issue #1122** (closed): loom-managed .gitignore block is two patterns stale vs installed loom-daemon 0.20.20 (/node_modules/, /pnpm-lock.yaml)
- **PR #1138**: fix: refuse two mutation records when the unmutated suite is red
- **Issue #1130** (closed): Two unwired mutation records read any non-zero pytest as killed with no unmutated run: mutate_pnr_report, probe_injection_discrimination
- **PR #1140**: docs: link DAG tag criterion to generated evidence table
- **Issue #1137** (closed): Link the capability DAG’s stamping criteria to the generated tag evidence
- **PR #1135**: docs: refresh Foundation/Minimoog status cells from dag-results
- **Issue #1121** (closed): docs/capability-dag.md F2 status still says the modal bank is on the unmerged `drums` branch
- **PR #1127**: fix: holdout probe refuses on a red unmutated baseline before any control runs
- **Issue #1124** (closed): holdout_injected_controls has no unmutated-tree baseline: a pre-existing red in test_holdout.py reads as 'every control fired'
- **PR #1111**: docs: record the unfiled judge fast-path gh field finding
- **Issue #820** (closed): Post-merge live verification of #818: the nightly's records land on main, and the [dag] loop guard holds
- **PR #1119**: fix: holdout probe maps pytest's exit status to its own outcome space
- **Issue #1118** (closed): holdout_injected_controls: pytest's own exit status is read as the probe's outcome — a deleted test_holdout.py reports 'every control fired' (exit 0)
- **PR #1107**: Remove stale UART repair probe, record transcript provenance
- **Issue #762** (closed): Remove fpga/probe_uart_findings.py: it reports 2 false FAILs on the repaired host and nothing runs it
- **PR #1104**: test: require the drum model in TR-808 acceptance
- **Issue #1094** (closed): Remove the obsolete optional-model skips from TR-808 acceptance
- **PR #1103**: fix: refuse non-16-bit WAVs in the two unguarded reference-corpus readers
- **Issue #817** (closed): Consolidate the seven WAV readers: four conventions, and two answer 96 dB wrong on a 24-bit file instead of refusing
- **PR #1115**: fix: holdout probe exits 2 for REFUSED instead of folding it into exit 1
- **Issue #1113** (closed): make controls records holdout_injected_controls' REFUSED as FAIL, and its Makefile comment names an exit 2 the probe never emits
- **PR #1108**: probes: dispositions for the four zero-reference orphan probes (wire, pin, delete)
- **Issue #1106** (closed): Unreferenced probe scripts under tools/ may drift the way fpga/probe_uart_findings.py did
- **PR #1093**: Prepare DRC, LVS and supply ERC probes for the routed ladder
- **PR #1098**: chore: remove legacy unsaturating 2x decimator prototypes and unrun Verilog scratch benches
- **Issue #1096** (closed): Remove 2 legacy unsaturating 2x decimator prototypes and 5 unrun Verilog scratch benches from rtl-sketch/
- **PR #1090**: signoff: fail regrade on an evidence envelope whose klt_version is not the pin
- **Issue #1089** (closed): regrade cannot tell a re-attested envelope from a stale one: no gate binds evidence/item-{9,10}.json provenance.klt_version to the pinned klt

### 2026-11-20

- **PR #1087**: signoff: bump the klt pin 0.8.0 -> 0.9.2, re-attest items 9 and 10, record the item 8 refusal
- **Issue #1084** (closed): T1 item 8: bump the signoff klt pin 0.8.0 -> 0.9.2, re-attest items 9 and 10, and attest the committed characterization report

### 2026-11-19

- **Issue #1075** (closed): Remove the Arty bring-up retry loop that hides a refused held note
- **PR #1076**: fix: stop bringup_arty's play_note retrying a refused hold
- **PR #1074**: State the foreign-host URL residual of the slow-record gate and pin it
- **Issue #1073** (closed): check_nightly_slow_records: the artifact-record URL prefix is `\S*`, so a foreign host or bare path in a complete record line still attributes the run
- **Issue #1066** (closed): check_nightly_slow_records: --run-id is optional but load-bearing, and control mode exits 0 over a printed FAIL
- **PR #1067**: fix: require --run-id and return the worse of verdict/control exits in the nightly slow-record gate
- **PR #1072**: fix: preserve quarter-slot geometry and voice parameters in ORFS
- **Issue #1070** (closed): Nightly slow-record gate accepts an Actions dependency SHA as the repository head
- **PR #1071**: fix: nightly gate attributes head sha to the checkout step's own output, not any line naming it
- **Issue #1033** (closed): Two pre-existing claim markers split their paragraph in rendered Markdown (docs/tr808-reference.md)
- **PR #1065**: docs: trail six mid-paragraph claim markers; refuse the splitting placement
- **Issue #1058** (closed): AC (a)'s sha-equality discriminator in #820 cannot pass on a quiet night under record_unchanged() (#967) — rederive the nightly slow-record check
- **PR #1059**: add check_nightly_slow_records: per-node corroboration gate for #820 AC (a)
- **Issue #1056** (closed): Say why eight of the ten files hashed into the frozen Mini V3 records are deliberately NOT guarded
- **PR #1060**: docs: correct Mini V3 re-bind guidance and narrow the reader-pin rationale
- **Issue #706** (closed): Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)
- **Issue #922** (closed): Tracked-JSON truncate-then-write remains in holdout.py (ledger + seals) and measure_m1a_volume_mapping.py
- **PR #948**: tools: write holdout ledger/seals and M1A volume-mapping records atomically
- **PR #750**: fix: keep the Mini V3 ring readers frozen with a named guard (re-scope per #1048)
- **Issue #1053** (closed): Isolate the Mini V3 freeze-defeat tests so each mutation starts from a passing copy
- **PR #1055**: test: isolate freeze-defeat mutants in function-scoped copies with a passing control
- **Issue #1050** (closed): [voice-only] Baseline Mini V4 as a new reference suite: render script, case list, board case table
- **PR #1052**: feat: baseline Mini V4 as a separate reference suite (manifest, render script, V3 freeze, board suite column)
- **Issue #1018** (closed): Confirm WORK_PLAN.md's as-of sentence survives Guide regeneration, and reconcile it with the inbound upstream in-region stamp
- **PR #1045**: fix: refuse a claim marker whose closer is not on its opener's line
- **Issue #1044** (closed): check_doc_claims.py silently drops a claim marker that spans two lines, so an unverified claim reports green

### 2026-11-18

- **PR #1042**: fix: refuse a second claim marker on one line instead of dropping it
- **Issue #1041** (closed): check_doc_claims.py silently drops a second claim marker on the same line, so an unverified claim reports green
- **Issue #1040** (closed): analyze_def_sites: read the kept DEF's extension-point census on the #1010 host (live-vs-latent, one command)
- **PR #1039**: fix: a '+' in the DEF walk ends the layer-name position
- **Issue #1037** (closed): analyze_def_sites: a skipped '+' does not end the LAYER-name position either (sibling of #1034)
- **PR #1031**: fix: consume skip-keyword name arguments so they never become phantom vias
- **Issue #1030** (closed): analyze_def_sites: a skip-keyword's NAME argument after a path's points becomes a phantom via (same class as #1013/#1021, via branch not width branch)
- **PR #1029**: docs: bind #1006 route-record figures to claim markers
- **Issue #1023** (closed): docs/tool-findings.md carries zero claim markers, so its route-record figures have nothing re-deriving them
- **PR #1025**: fix: apply the DEF path-point extension to the wire rect and count it in the record
- **Issue #1022** (closed): analyze_def_sites: the DEF path-point extension is recorded but never applied to the emitted rect, nor surfaced in the record
- **PR #1035**: fix: end the via-name position at a skipped '+' in DEF path walk
- **Issue #1034** (closed): analyze_def_sites: '+ NONDEFAULTRULE <name>' after a path's points makes the keyword a phantom via (the spelling #1030 left out of scope)
- **PR #1027**: docs: scope 'bound in-repo' claim to what route_def_manifest.json carries
- **Issue #1026** (closed): tool-findings.md:189-193 — "this chain is bound in-repo" covers two items the manifest does not
- **PR #1024**: fix: consume STYLE/MASK/WEIGHT numeric arguments with their keyword, never as the path width
- **Issue #1021** (closed): analyze_def_sites: STYLE/MASK numeric arguments are still consumed as the path width
- **PR #1019**: bind the DEF re-run evidence chain in-repo: commit route_def_manifest.json, regenerate def_site_analysis.json with totals, shape tags, overlap lower bound
- **Issue #1011** (closed): Commit route_def_manifest.json and regenerate def_site_analysis.json's record fields against the kept DEF
- **PR #1016**: docs: accept ladder_dp's 24 Metal2 PDN spacing sites as known-PDN residual
- **Issue #1009** (closed): Floorplan/PDN: the M1->M4 via-column lands leave 24 Metal2 spacing violations in ladder_dp's finished route
- **PR #1017**: parse the DEF path-point extension form ( x y ext ): record ext_start/ext_end, never consume it as width
- **Issue #1013** (closed): analyze_def_sites: parse or refuse DEF path-point extension form ( x y ext )
- **PR #1015**: docs: add static as-of instruction to WORK_PLAN.md header
- **Issue #1014** (closed): WORK_PLAN.md carries no as-of date, so a reader cannot tell when a declined regeneration left it stale
- **PR #1010**: bind the ladder_dp route-DRC claims in-repo and answer design-versus-router from the routed DEF
- **Issue #1006** (closed): ladder_dp route record: design-vs-router is undetermined for want of a DEF, and the classifier's inputs are not in the tree
- **PR #1005**: test: injected defect for the start-skip legitimacy half of the two-instant CB control
- **Issue #1004** (closed): test_verify_109_corpus_gate: the start-skip legitimacy assertion ships without the input that defeats it (rule 8)
- **PR #1001**: tools: commit the probes prose-literal census its counts were quoted from
- **PR #1002**: pnr: commit the finished ladder_dp route record (klt 0.9.0) and classify its 24 residual DRC violations
- **PR #999**: fix: NOT_QUOTED sentinel for unquoted CONCLUSION figures in the corpus-gate test
- **Issue #998** (closed): The #988/#990 prose-literal class scanner is in no tree, so its 139/159/156 counts (and "no third site") cannot be re-derived
- **Issue #997** (closed): test_verify_109_corpus_gate's CB extent parser can report a drift that does not exist (two-instant CB returns start=None)
- **Issue #808** (closed): Route ladder_dp to completion with the unpatched netlist on the build box: DRT-0305's cause is measured gone, the error is not re-observed
- **PR #1000**: tools: remove one-shot ring-ratio snapshot harness
- **PR #996**: fix: render_writes refuses out-of-record streams instead of asserting
- **PR #993**: probes: derive two summaries' quoted figures from the run's own measurements
- **PR #991**: probes: derive verify_109_claims CONCLUSION extents from the run's measurements
- **PR #987**: perf suite: drive the shipped mixer -- a synth_top backend for the full-performance suite (#338)
- **Issue #995** (closed): performance_suite: render_writes answers AssertionError, not REFUSED, for a stream past the record
- **Issue #990** (closed): Class sweep (#988): two more same-shape sites - hard-coded prose figures beside the same run's live measurements
- **Issue #988** (closed): verify_109_claims:244's CONCLUSION understates RS floor-clearing by ~13 ms (53.9 measured vs ~41 prose), and the figure is registered with no gate
- **Issue #975** (closed): Remove tools/ring_ratio_compare.py: one-shot #548 before/after harness, nothing references it
- **Issue #912** (closed): Five more JSON-refusal handlers miss UnicodeDecodeError: the #909 gap generalises past load_json_refused
- **Issue #338** (closed): Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement
- **PR #818**: ci: commit the nightly's slow DAG records to main, not just the artifact
- **Issue #787** (closed): Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run"
- **PR #986**: docs: record verify_109_claims half-2 live run against the real Fischer corpus
- **PR #985**: docs: mark §5c mpd.* bucket names as stale pending #933 regeneration
- **Issue #983** (closed): CI produced zero check-runs on PR #982: pull_request workflows stopped triggering (needs operator)
- **Issue #829** (closed): Run `make reference-integration` on the box: verify_109_claims' real-reference half has never been exercised against the real Fischer corpus
- **Issue #856** (closed): Provision the pinned build box for this checkout: no .env, no remote.env, and the dispatch worker's AWS identity has no EC2 access
- **Issue #869** (closed): Forward upstream to rjwalters/loom: post-verdict issue-body hash refuses when the body ends in a newline (#13600's fix covers one trailing newline, jq gives two)

### 2026-11-17

- **PR #980**: docs: record the posted OpenROAD#12303 correction of record in the #12307 row
- **Issue #888** (closed): Post the #12307 correction of record as a comment on OpenROAD#12303
- **Issue #826** (closed): compile_dag --only: a named node that cannot run is skipped SILENTLY (exit 0, no line)
- **PR #974**: dag: restate verdict-less evidence_file as REFUSED in docs, and forbid the silent declaration
- **PR #972**: fix: compile_dag --only refuses a named node it cannot run, instead of silence
- **PR #977**: test: pin run_evidence()'s known-key REFUSED and its non-recording path through main
- **Issue #976** (closed): compile_dag: run_evidence()'s EVIDENCE_PRECEDENCE refusal ships without the input that defeats it
- **Issue #971** (closed): compile_dag: docs/capability-dag.md and test_compile_dag_deps.py still state the pre-#791 'EXISTS ONLY returns pass' behaviour as current
- **PR #968**: test: refuse the archive fixture during a squash merge
- **PR #973**: fix: derive compile_dag's four evidence-source readers from one EVIDENCE_PRECEDENCE
- **PR #969**: fix: keep unchanged dag records byte-identical so record PRs can land (#967)
- **PR #970**: fix: share the claim-marker skip grammar between checker and #789 probe
- **PR #810**: dag: report an evidence file that declares no verdict as REFUSED, not GREEN
- **PR #944**: test: catch serialise-then-write and write-mode open spellings in the inline record-write guard
- **PR #965**: fix: refuse the 789-marker suite's archive fixture mid-merge
- **PR #953**: fix: --margins all-caught no-cliff input states the measured fact, not 'not every pair is CAUGHT'
- **PR #957**: fix: 789 marker probe derives its already-satisfied guard from the staged injection
- **PR #960**: fix: anchor the drum_kit grep= claim so the 789 probe's injection cannot collide with it
- **Issue #966** (closed): 789-marker fixture guard: `git merge --squash` reproduces the exact mid-merge split it refuses, and is not refused
- **Issue #827** (closed): compile_dag: unjudged() keys on evidence_file but run_evidence dispatches suite/verifier first, so a node with both would be REFUSED despite a passing suite
- **Issue #967** (closed): Any PR that rewrites a docs/dag-results.json record cannot land while main is busy: dag.yml re-conflicts it every merge
- **Issue #962** (closed): 789 probe: injected_state mirrors the checker's FENCE rule but not its inline-backtick rule
- **Issue #791** (closed): compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone
- **Issue #923** (closed): The source-shape guard in test_atomic_record_writers.py stays green on open(...,'w') + write(json.dumps(...))
- **Issue #964** (closed): 789 probe suite: a merged-tree measurement is silently void unless HEAD is the merge commit (git archive HEAD fixture)
- **Issue #950** (closed): print_margins' else prose says 'not every pair is CAUGHT' on a guard that does not measure it
- **Issue #936** (closed): 789 marker probe: already_satisfied() holds a hand-written copy of each claim's in=/grep=, and the two can diverge silently
- **Issue #959** (closed): main is red: the 789 marker probe's already-satisfied injection also defeats #904's grep= uniqueness refusal (tools job, since 72be3665)
- **PR #951**: probes: split the rate-fold verdict else on the dB red count it mis-states
- **Issue #946** (closed): rate_fold_db_control's verdict prose states a stronger fact than `must == []` supports
- **PR #945**: test: guard the ms.* grouping label against a silent %.0f collision
- **Issue #939** (closed): measure: the ms.* grouping label is %.0f, so two MS_WINDOWS_S entries that round to the same integer ms would silently merge buckets
- **PR #876**: ci: collect the 16 uncovered fpga/ test files in a fpga-uncovered job
- **Issue #853** (closed): 16 of 40 fpga/ test files are run by no CI job (23 more only via make verify-fast) -- the fpga half of the #404/#851 shape
- **Issue #958** (closed): 789 marker probe: the #920 already-satisfied refusal for drum_kit is shadowed by check_doc_claims' one-line uniqueness refusal — tools job red on main since 72be3665
- **Issue #956** (closed): compile_dag.py's status legend lists BLOCKED and TODO twice (merge artefact from #810)
- **Issue #937** (closed): rate_tol_sweep.py --unit <anything but db> labels its columns caught_db/cost_db, and two smaller label drifts from #895
- **PR #942**: fix: name rate_tol_sweep's per-unit columns by the swept unit
- **Issue #932** (closed): check_attested_hashes: a pathed check entry with no content_hash is a silent green, and is counted as compared
- **PR #938**: fix: REFUSE pathed check entries with no committed hash; count only compared files
- **Issue #929** (closed): measure: extra_group() merges the 10.0 ms and 5.0 ms MPD strides into one bucket (6 strides -> 5 buckets)
- **PR #934**: fix: bucket MPD attribution columns by stride, not by stride decimals
- **Issue #920** (closed): 789 marker probe: a control still reports FAIL when the tree already satisfies the value it injects
- **PR #927**: fix: 789 marker probe refuses an injection state the tree already satisfies
- **Issue #815** (closed): Re-base the 114 Refused definitions on refusal.Refused and delete the three mitigations it makes dead (follow-on to #784)
- **PR #915**: fix: re-base every Refused on refusal.Refused, delete the three dead mitigations, flip the guard to enforcing
- **Issue #904** (closed): check_doc_claims.py cannot detect a grep= pattern that matches several lines, so no marker's uniqueness is machine-checked
- **PR #916**: check_doc_claims: refuse a grep= marker matching more than one line
- **Issue #928** (closed): docs: discrimination.md:962 points at line 232 of discrimination-run-16.txt, which #778 regenerated
- **PR #931**: docs: re-anchor jit.stability figure to revision 24937ca4
- **Issue #940** (closed): The #906 roadmap gate: tags assumed in rungs.yml but asserted in nightly.yml, and nothing guards the shipped step order
- **PR #943**: ci: assert the shipped compile_dag check-before-run order; state rungs' tag precondition
- **Issue #935** (closed): the dB family's rate tolerance ships 8.0 dB of licence at the floor_db clamp with no injected-bug control on any dB column
- **Issue #921** (closed): Makefile and docs/claim-markers.md understate the 789 marker probe's runtime margin by ~6x
- **PR #926**: docs: restore measured ~0.6 s / ~6000x figures for the 789 probe runtime note
- **PR #941**: [rate-tol] measured: no dB must row is constructible for the integer-fold control at any licence (#935)
- **Issue #906** (closed): rungs.yml's roadmap gate checks the README that the line above it just rewrote
- **PR #924**: fix: the roadmap gate judges the README as committed, not the regen's own output
- **Issue #882** (closed): Tracked-JSON experiment-record writers still truncate-then-write (class sweep remainder from #881)
- **PR #918**: fix: tracked-JSON experiment records replace atomically; record readers refuse with a path
- **Issue #782** (closed): bug: the relative rate tolerance is dimensionally meaningless on the 147 dB columns
- **PR #895**: split RATE_TOL_REL into per-unit scalars; sweep the dB term to 0.1 and empty the rate quarantine
- **Issue #884** (closed): regrade: an attested-file edit fails as a bare 'exit 3' with the drift report swallowed, and nothing couples the edit to regenerating the evidence
- **PR #925**: signoff: surface klt's drift report on failure; add attested-hash coupling check to verify-fast
- **Issue #930** (closed): signoff.yml swallows the attest-check diagnostic: failing attestation prints nothing under bash -e
- **Issue #778** (closed): measure: re-derive the jit.stability +0.008 and mpd.* attributions with the exact-period fold (REFUSED in #767 -- no corpus on that host)
- **PR #919**: docs: re-derive the 5b/5c attributions with the post-#776 fold; record corpus octave incidence
- **Issue #885** (closed): pytest_collection_inventory.py:78 comment says pnr/ is 'make verify only' — false once the pnr CI job lands (#851/#865)
- **PR #914**: docs: correct GUARDED_DIRS comment — CI collects pnr/ as a third job
- **Issue #879** (closed): docs: makefile_recipe comment-filter docstrings claim a silent exit 0 for a mutation that is loud mid-recipe
- **PR #907**: docs: scope makefile_recipe comment-filter docstrings to the line position
- **Issue #905** (closed): check_doc_claims_789_marker_controls.py reports FAIL, not REFUSED, when an uncommitted edit makes a #789 claim stale
- **PR #913**: fix: 789 marker probe REFUSES a dirty tree instead of reporting FAILing controls
- **Issue #874** (closed): docs/tool-findings.md will carry contradictory claims about the core-area identity once #849 merges
- **PR #911**: docs: drop stale attribution of the 6-sf coincidence framing to PR #849's row
- **Issue #909** (closed): load_json_refused catches JSONDecodeError only: a damaged record that is also invalid UTF-8 escapes as a traceback
- **PR #910**: fix: refuse a manifest record truncated mid-UTF-8 rather than a bare UnicodeDecodeError

### 2026-11-16

- **Issue #864** (closed): classify_route_drc.py crashes where it documents exit 3 REFUSED: 3 measured inputs return rc=1 with a traceback
- **PR #908**: fix: classify_route_drc refuses (exit 3) where it documented REFUSED
- **Issue #881** (closed): Class sweep for #842: tracked JSON still written by truncate-then-write outside compile_dag
- **PR #883**: fix: the tracked bounds ledger persists through an atomic replace (#881)
- **Issue #900** (closed): Rule-8 statement for gate (c)'s narrowed header predicate: a column header with no format spec escapes the vintage pin
- **PR #902**: docs: state gate (c)'s spec-less-header bound in the rule-8 paragraph, record it with a control
- **Issue #845** (closed): estimator_defects.py section 6: nine unguarded `.value` arithmetic sites, including two that reach a check() verdict, owned by neither #792 nor #843
- **PR #898**: probes: guard estimator_defects section 6's nine .value reads behind REFUSED
- **Issue #873** (closed): spec/NUMERIC-CONTRACT.md: PR #846's repaired sites cite figures and pointers that no marker guards
- **PR #901**: docs: guard NUMERIC-CONTRACT's cited figures and pointers with 8 grep claim markers
- **Issue #863** (closed): compile_dag: stale_against() answers a content question with a commit-range walk, so a merge-shaped conflict round renders fast nodes STALE (false positive; --full-history is NOT the fix)
- **PR #903**: fix: measure DAG staleness by content, not by a commit-range walk
- **Issue #784** (closed): Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error
- **PR #841**: fix: refusals crossing a class boundary are verdicts again (refusal.Refused base + baseline-mode guard)
- **Issue #811** (closed): Wire or document tools/probes/check_doc_claims_789_marker_controls.py: a control no target runs (the #756 shape, again)
- **PR #897**: controls: wire check_doc_claims_789_marker_controls.py as a standing control
- **Issue #877** (closed): File upstream (rjwalters/loom): loom-daemon's write guard gates issue creation on push/WRITE, refusing every public upstream where issues:write suffices
- **PR #896**: docs: index rjwalters/loom#13978 (forge create-issue refused on every foreign public repo) in the Loom pass table
- **Issue #858** (closed): compile_dag classify(): the empty-covers REFUSED guard masks a FAILING record as REFUSED when the node also carries an in-history tag
- **PR #894**: fix: answer a failing evidence record RED before the empty-covers refusal
- **Issue #870** (closed): probe_orphan_reap argv test drops true matches where a worktree path ends a longer token (--opt=<path>, <path>:<mount>)
- **PR #893**: fix: restore worktree attribution for paths that end a longer argv token
- **Issue #867** (closed): #781 residue: fold-interpolator verdict summary says 'Five' where its own runs field has four singleton groups, plus two merge-jammed lines
- **PR #892**: fix: fold-interpolator verdict counts its four singleton widths; unjam two merge-residue lines
- **Issue #852** (closed): test_probe_labels gate (c) polices filter_corner_recalibration's module docstring, and goes red if it says "pre-#150"
- **PR #891**: fix: scope the probe-label vintage gate to column headers, name the file in violations
- **Issue #890** (closed): Guide's WORK_PLAN Ready list carries forward omissions: #874 was eligible before the run and is absent on both sides of #889
- **Issue #835** (closed): File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings
- **PR #849**: summarize.py refuses duplicated required metrics (OpenROAD#12303)
- **Issue #851** (closed): pnr/ test files run under make verify but under no CI job (6 of 8) -- the #404 shape, one directory over
- **PR #865**: ci: collect pnr/ as a directory in CI; assert the docker precondition in container_progress
- **Issue #868** (closed): refusal_exit_census: the sites_outside_set pin is keyed on line numbers, so an unrelated edit above cymbal_vca_drive.py:1525 reds the gate
- **PR #878**: fix: key census sites_outside_set on (file, function, status) counts, not lines
- **Issue #866** (closed): fold-interpolator-sweep.txt's n_over column no longer reproduces: #775 narrowed RATE_TOL_REL and the artefact was not regenerated
- **PR #880**: regenerate fold-interpolator n_over against RATE_TOL_REL 0.02 and record the dependency in limits
- **Issue #842** (closed): compile_dag.py --run: the per-node records write is not atomic, in the change made for kill-safety
- **PR #862**: fix: dag --run persists records through an atomic replace
- **Issue #836** (closed): sensitivity controls: nothing cross-checks `make controls` against sens.INJECTIONS (the helper already exists)
- **PR #855**: test: cross-check make controls against sens.INJECTIONS, paired with the --expect token
- **Issue #834** (closed): characterize_synth_top: the per-corner Fmax tolerance omits min-period rounding, and its boundary test's pass arm is a no-op
- **PR #872**: tools: per-corner Fmax budget absorbs the min-period rounding term
- **Issue #850** (closed): File upstream (rjwalters/loom): check-verified-corrections-preserved.sh matches only '## ', so an h3 Verified-corrections section passes silently at exit 0
- **PR #875**: docs: index rjwalters/loom#13938 (h3 blind spot in check-verified-corrections-preserved.sh) in the Loom pass table
- **Issue #843** (closed): estimator_defects.py: five unguarded `.value` arithmetic sites remain after #805, outside #792's section 6
- **PR #871**: fix: rule estimator_defects.py's five remaining unguarded .value reads per site
- **Issue #797** (closed): spec/NUMERIC-CONTRACT.md item 29 says the chip still carries drum_section_placeholder and that nothing verifies the joined chip
- **PR #846**: docs: numeric contract states the shipped drum section and exact-sum master mix
- **Issue #848** (closed): File upstream: ORFS metrics JSON serialises every float metric at 6 significant figures (hard-coded setprecision(6))
- **PR #861**: docs: ledger the OpenROAD 6-sf metrics serialisation defect (OpenROAD#12307)
- **Issue #774** (closed): bug: dominant_period picks a different octave at 44.1 kHz and 48 kHz on some clips
- **PR #781**: fix: resolve dominant_period's octave pick with a swept margin
- **Issue #804** (closed): Collapse the three exit statuses that mean REFUSED: run_all.py reports the cymbal low-tail refusal as FAIL(1)
- **PR #825**: runner: report every declared refusal status as NO-VERDICT, not FAIL
- **Issue #789** (closed): docs/milestones.md milestone 3 and the README's T1 sentence contradict the tree
- **PR #798**: docs: repair milestone 3 and the README T1 sentence, and put both under check_doc_claims
- **Issue #819** (closed): run_klt.py container-ownership test matches a path prefix without a separator boundary
- **PR #860**: fix: match prefix tests at a path-component boundary, not a string boundary
- **PR #857**: pnr: add classify_route_drc.py, the #2089-aware DRC classifier #808's route record needs
- **Issue #792** (closed): Corner probes: date the pre-#150 columns, fix `corner_shipped`'s stale docstring, guard the refusal print
- **PR #840**: probe: date the corner probes' pre-#150 columns, fix corner_shipped's docstring, guard the refusal print
- **Issue #805** (closed): The BASELINE of a perturbation comparison is read unguarded while the comparison itself is guarded (2 probe sites)
- **PR #839**: fix: assert the baseline precondition of every perturbation comparison before reading it
- **Issue #793** (closed): bug: tools/sensitivity.py raises ZeroDivisionError on an objective that reaches zero
- **PR #822**: fix: sensitivity gate refuses, never divides, across or below a zero objective
- **Issue #823** (closed): compile_dag.py --run writes docs/dag-results.json only after the whole loop, so one crashed verifier loses every node's records
- **PR #833**: DAG --run: guard the evidence loop and persist records per node
- **Issue #828** (closed): Dedupe BS.1770 K-weighted loudness: three copies of kweight/loudness in tools/
- **PR #837**: [tools] one BS.1770 K-weighting implementation: the listening packs import perceptual_gate

### 2026-11-15

- **Issue #824** (closed): stale_against() reads an unresolvable sha as 'nothing changed', so a record whose commit left the history renders GREEN instead of STALE
- **PR #844**: fix: refuse DAG evidence whose staleness cannot be measured, never render it current
- **Issue #803** (closed): A probe's identical flag has #785's absent-vs-null hole, load-bearing: balance_input_invariance reports identical on a dropped key
- **PR #831**: fix: compare balance_input_invariance images with fpga/record_compare
- **Issue #775** (closed): bug: RATE_TOL_REL is 8x larger than everything jit.cyclecorr measures
- **PR #783**: fix: narrow RATE_TOL_REL to 0.02, and record why no margin test can exist
- **PR #832**: tools: characterize synth_top from the committed route evidence
- **Issue #790** (closed): T1 item 8: build one characterization report from the committed synth_top route evidence and record the klt signoff result
- **PR #796**: fix: normalise repeat_period per overlapped window and resolve its multiple
- **Issue #780** (closed): bug: repeat_period returns four times the true repeat period on a signal that repeats exactly
- **Issue #768** (closed): measure: how many discrimination rows fall under the new jit.dphase_ar1 floor (REFUSED in #763 -- no corpus on that host)
- **PR #786**: docs: roadmap as of 2026-11-13
- **PR #799**: retire DAG nodes M5 and D4, and forbid the issue-only shape returning
- **Issue #788** (closed): DAG nodes M5 and D4 read TODO against closed issues: give each evidence or retire it
- **PR #807**: fix: assert degenerate_refused from the trace instead of by elimination
- **PR #809**: fix(pnr): retire the three klt netlist patches, and make the record attributable to an invocation
- **Issue #765** (closed): Retire the three netlist patches in pnr/klt/ladder_dp/run-klt.sh: the klt bugs they work around were fixed upstream in September
- **PR #813**: test: ground-truth corner_dc_referenced against filtered_noise, with a -6 dB control
- **Issue #794** (closed): bug: make verify is red on main -- estimator_ground_truth reports corner_dc_referenced undeclared
- **Issue #540** (closed): CI: verify the #709 cancel/duplicate-run fixes live and re-measure main job-minutes (steps 3-4 deferred)
- **PR #821**: verify_109_claims counts its verdict, refuses when the reference half is required
- **Issue #814** (closed): verify_109_claims' real-reference half is binding on no host and FAILs on a partial corpus: the cymbal-369 row-7 defect, unsearched
- **Issue #801** (closed): Remove the duplicated period grid in measure_promoted_bands.py: the unguarded copy reports 0.034 % when 9 of 12 known cases refuse
- **PR #806**: fix: refuse the period worst-case figure when any of its twelve known cases refuses

### 2026-11-14

- **Issue #772** (closed): Remove the stale pre-#109 scorecard estimator copies and the unrun apparatus command from tools/measure_partial_balance.py
- **PR #773**: feat: count dphase_ar1 floor refusals per side, and plumb the floor-off arm
- **PR #779**: refactor: drop the retired fixed-window estimators from the balance diagnostic
- **Issue #785** (closed): Consolidate 4 copies of the release staleness comparison: two generations already disagree on a record that ships with nulls
- **PR #802**: fix: one release staleness comparison, with the absent-vs-null hole closed

### 2026-11-13

- **Issue #763** (closed): bug: test_extra_features_are_rate_independent fails on main -- jit.dphase_ar1.seg0 is rate-dependent
- **PR #769**: fix: refuse jit.dphase_ar1 below a physical floor instead of a 1e-20 epsilon
- **Issue #759** (closed): Consolidate the duplicated injected-defect ladder and k-ROM builder in model/ into one copy
- **PR #764**: refactor: keep one copy of the injected-defect ladder and the k-ROM builder
- **Issue #755** (closed): Wire or remove tools/bd_pitch_predeclaration_mutants.py: unrun mutation control now REFUSES
- **PR #757**: run the BD pitch mutation controls: retarget the bool mutant, wire make controls
- **Issue #756** (closed): tools/probes/check_doc_claims_scope_control.py: a self-described control that no target runs
- **PR #758**: docs: record the claim-checker scope control as a one-time #435 record
- **Issue #767** (closed): bug: jit.cycledshape and mpd5.00ms.rowcorr are rate-dependent on the extended stimulus panel
- **PR #776**: fix: fold at the exact period so jit.cycledshape and mpd*.rowcorr stop reading the sample rate

### 2026-11-12

- **PR #754**: Bump Repo Skills 0.25.10 -> 0.26.1

### 2026-11-10

- **Issue #572** (closed): Auditor guard telemetry: sql delete no where false positive on Python del

### 2026-11-07

- **PR #752**: Bump Repo Skills 0.25.1 -> 0.25.10

### 2026-11-06

- **Issue #749** (closed): Dedupe BS.1770 K-weighted loudness: 3 hand-copied implementations, already drifted
- **Issue #570** (closed): Auditor guard telemetry: stash scope create redirect false positives
- **Issue #571** (closed): Auditor guard telemetry: worktree write confinement blocks sweep checkpoint
- **Issue #732** (closed): Consolidate duplicated compare_files/main scaffolding across three candidate comparators
- **Issue #547** (closed): reference_rigs._k_rom_for caches on id(g_rom): a freed candidate ROM can hand its k table to the next
- **PR #751**: fix(model): key substituted-ROM k cache on content, not id()

### 2026-11-01

- **PR #746**: Bump Repo Skills 0.24.7 -> 0.25.1
- **Issue #747** (closed): tools: test_rho_is_invariant_to_record_gain fails on main (rho value is None)
- **PR #748**: tools: compare refused rho depths by None-ness in the gain-invariance test

### 2026-10-30

- **Issue #745** (closed): Remove audition/physical.py: unreferenced float modal sketch

### 2026-10-29

- **Issue #742** (closed): CI regrade fails on main: committed item-10 attestation hashes a README.md the DAG bot regenerates
- **PR #743**: signoff: stop attesting README.md, which dag.yml rewrites

### 2026-10-27

- **PR #740**: ci: attest T1 item 9 (testbenches shipped) with klt signoff
- **PR #739**: ci: attest T1 item 10 with klt signoff and re-grade the committed verdict
- **Issue #738** (closed): T1 item 9 (testbenches shipped): inventory of the RTL-vs-model benches and klt signoff --attest 9
- **Issue #737** (closed): T1 item 10 (repo hygiene): klt signoff block manifest, committed verdict with CI re-grade, and --attest 10
- **Issue #744** (closed): main: item-10 attestation drifted after README regeneration (regrade red on every PR)

### 2026-10-24

- **Issue #560** (closed): [gate] a between-recording bar for #379 needs data the MARS pack cannot supply (rule v2 refused: no p satisfies seeds and stays-green together)
- **PR #535**: Single corpus-path resolver: configured_refs() reaches every reader (#522)
- **Issue #522** (closed): Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader

### 2026-10-21

- **PR #733**: Bump Repo Skills 0.24.3 -> 0.24.7

### 2026-10-20

- **PR #652**: [sound] #592 phase-aware tom pitch-drop measurement vs Fischer: REFUSED (not identifiable), no repair
- **Issue #592** (closed): [sound] toms: diode-drop magnitude and time constant against the Fischer takes (LT onset 74 vs 35 cents)
- **PR #709**: ci: stop cancelling started main runs and double-running PR commits
- **PR #727**: docs(gate-379): source-qualification report for a between-recording bar
- **Issue #726** (closed): Qualify up to three independent 808 reference-data sources for #560

### 2026-10-17

- **PR #722**: fix: run_case refuses a bool or numeric string as a measurement
- **Issue #720** (closed): Follow-on from PR #719: run_case._finite still accepts bool and numeric strings
- **PR #719**: fix: share one finite-number guard so a bool EDT10 is REFUSED
- **Issue #718** (closed): Consolidate divergent _finite() guards; cymbal_tone_render's accepts bool

### 2026-10-16

- **PR #634**: performance_suite: first model-backend run of the 7 phrases (#338)
- **Issue #673** (closed): zero_crossing_frequency moves 0.78% under polarity reversal on an asymmetric waveform (upward crossings only)
- **PR #711**: Bump Repo Skills 0.24.1 -> 0.24.3
- **PR #714**: tools: add two-SHA driver for prepare-corpus before/after probes
- **PR #690**: fix: route conga and partial-balance probes through run_case.prepare and the preparation contract
- **Issue #636** (closed): Two measurement tools carry private prepare() copies that still have the pre-#132 lead clamp (#101)

### 2026-10-15

- **PR #708**: fix: make zero_crossing_frequency polarity-symmetric and regenerate res-tuning artifacts
- **PR #704**: fix: route free-ring pitch readers through ring_estimator
- **PR #701**: ci: gate fpga publish-binding and Arty SD-top tests in verify-fast
- **PR #699**: ci: run the named R1/R2 image selection tests in verify-fast
- **PR #698**: feat: detect the two noise controls on a swept mean-residual statistic
- **PR #697**: fix: glide_cents refuses where the zero-glide known answer exceeds the floor
- **PR #696**: fix: _ring_ratio refuses non-sustained and ambiguous rings via a shared estimator
- **PR #695**: gate: match the candidate's rate path to the target's for impulse
- **PR #694**: fix: REFUSE the simulated MIDI session on an overloaded host instead of FAIL
- **PR #688**: fix(residual_dc): re-derive --verdict row labels from detail under the frozen limits
- **PR #685**: docs: record zero_crossing_frequency polarity sensitivity and pin its bound
- **Issue #684** (closed): Make zero_crossing_frequency polarity-symmetric and regenerate the res-tuning artifacts
- **Issue #703** (closed): Follow-on from PR #696: audit other ring-pitch readers still using bare am.dominant_frequency
- **Issue #692** (closed): CI-reachability gate for the remaining unreached fpga/test_*.py (split from #482)
- **Issue #654** (closed): residual_dc --verdict trusts each record's label instead of re-deriving it from detail under the frozen limits
- **Issue #602** (closed): pitch_trajectory.glide_cents answers tens of cents wrong just above its refusal threshold (and with f0 far from F_REF)
- **Issue #584** (closed): midi_session --port sim clean path reports ERR_DUE under host load (wall-clock flake, pre-existing)
- **Issue #548** (closed): ladder_headroom._ring_ratio reports a pitch for a below-onset dead-band limit cycle, and falls back silently on estimator disagreement
- **Issue #539** (closed): [probe] permitted-differences noise controls are caught in ~60% of independent seed groups; their green on VALIDATE_BASE is one draw
- **Issue #482** (closed): CI does not run fpga/test_bringup_arty.py or several other fpga/test_*.py files
- **PR #691**: fix: audit broad except handlers; dirty on unanswered git, exit red on descent errors
- **PR #689**: docs: exempt calibration-only p dial from sensitivity registry, with trigger
- **PR #687**: fix: read response_row and sound_report corners against the DC plateau
- **PR #686**: fix: refuse image_identity when published sets differ from declared populations
- **PR #683**: fix: make drum read_wav and dominant_period padding-invariant
- **PR #681**: ci: check_workflows asserts repo paths named by workflows and Makefiles exist
- **PR #680**: release: bind the R2 candidate to R2_FROZEN_AT instead of the working tree
- **Issue #682** (closed): gate-379: register or explicitly exempt the p calibration dial from the sensitivity registry
- **Issue #658** (closed): Release gates: bind R2's candidate to R2_FROZEN_AT and give the post-R2 tree its own image/verification directory (blocks #657)
- **Issue #629** (closed): check_workflows.py: assert paths named in workflows and Makefiles exist (standing guard for the #622 class)
- **Issue #617** (closed): Audit drum-facing whole-clip-mean / zero-phase conditioning for trailing-silence sensitivity (drum_verify, discrimination_features)
- **Issue #615** (closed): Audit the 57 remaining broad except handlers in tools/ and model/ (follow-up to #610)
- **Issue #582** (closed): release_manifest.image_identity: self-described membership sets can be emptied (vacuous loop, same shape as #283)
- **Issue #546** (closed): Qualify corner convention in reference_compare.response_row and sound_report._corner (no ref_db)
- **PR #504**: Reachable attack-context re-bind path for Builders (#502)
- **PR #677**: Bump Repo Skills 0.19.5 -> 0.24.1
- **PR #675**: audio_distance_floor: thread sample rate into mel_dac (#533)
- **PR #674**: spi_host: report BD attack-window width change in check() (#506)
- **Issue #502** (closed): A Builder cannot re-bind the attack-context evidence: workflow_dispatch is 403 for the agent token
- **Issue #538** (closed): Correct remaining permitted-differences detection counts after #537
- **Issue #506** (closed): spi_host: feasible() can lengthen the BD 4 ms attack window under dense hits; check() does not report it

### 2026-10-14

- **PR #671**: uart_host: blame host latency, not the device clock, at the rolling anchor unwrap (#459)
- **PR #672**: docs: DAC control pins are breakout-specific and a precondition of the audio path
- **PR #670**: verify_live_midi: refuse unmeasurable frame unwrap (#479)
- **PR #669**: external_claim: REFUSE on malformed second_route instead of crashing (#475)
- **PR #668**: Remove model/fixed_sweep.py (flat metric, unreferenced, superseded)
- **PR #667**: deadline: bind the -l2 capture to the schedule-relevant RTL (#452)
- **Issue #459** (closed): Rolling playback blames the device's clock for host latency: 'frame counter did not advance' while it ran at 48 kHz
- **Issue #460** (closed): ARTY.md's 'leave MU/FM unconnected' is Adafruit-specific and silently mutes #408's purple PCM5102
- **Issue #479** (closed): verify_live_midi._unwrap carries the same unchecked 0x8000 gap assumption #474 fixed
- **Issue #475** (closed): tools/external_claim.py crashes instead of REFUSING on a malformed second_route
- **Issue #486** (closed): check_doc_claims exits 1 on main: docs/scorecard/cymbal-369/vca-drive/vca-drive.json was committed from a dirty tree
- **Issue #663** (closed): Remove model/fixed_sweep.py: flat metric, unreferenced, superseded by ladder-rung1-audit
- **Issue #463** (closed): CI: decide whether PR branches should run each suite once (push + pull_request double run)
- **Issue #452** (closed): Deadline evidence: the -l2 capture is bound to its stimulus but not to the RTL that ran it

### 2026-10-13

- **PR #666**: Correct rungs.yml comments on non-strict compile_dag --check
- **PR #665**: fpga/ARTY.md: document --box local for on-box DSP extraction
- **PR #664**: uart_host: --hold-frames over the wrap horizon is REFUSED, not a traceback
- **PR #650**: feat(#400): production-tail diagnostic for the CY 1-2.5 kHz question (REFUSED/NO VERDICT)
- **PR #561**: gate-379: between-recording bar rule v2 (REFUSED, isolated), current-main ranking, controls
- **PR #633**: Replace unsatisfiable nightly reference-freshness check (#622)
- **PR #594**: fpga: refuse 'routed' from an incomplete nextpnr log (#565)
- **PR #624**: Nightly controls: replace blind I2S_SWAP with discriminating serializer bench
- **PR #649**: [593] tom distortion: qualification instrument, controls and preregistration
- **Issue #438** (closed): Correct rungs.yml comment about non-strict compile_dag --check behavior
- **Issue #367** (closed): fpga/ARTY.md: document --box local for the on-box DSP extraction invocation
- **Issue #458** (closed): uart_host --hold-frames raises a raw ValueError instead of REFUSED, and does so intermittently
- **Issue #379** (closed): 808 kit: automated perceptual gate calibrated against the 808's own variability — prove it, rank all 16 sounds, fix worst first
- **Issue #400** (closed): Cymbal tail: qualify and localize the remaining production-path energy/decay discrepancy
- **Issue #622** (closed): Nightly reference-freshness gate is unsatisfiable: tests a docs path that has never existed
- **Issue #565** (closed): Refuse to report 'routed' from an incomplete nextpnr log (fpga/scripts/report.sh + fpga/Makefile)
- **Issue #619** (closed): Nightly controls requires I2S_SWAP detection despite declared blind timing

### 2026-10-12

Closure events below record forge state, not a verified fix or a new sound result.
- **PR #655**: Cymbal knob-law render: kit_at delegates to the shipped constructor (#413)
- **Issue #618** (closed): Output-stage floor quantisation: does `>> 15` leave a standing sub-LSB bias and explain H_CH2's bound overshoot?
- **PR #653**: probe: measure output-stage floor quantisation against an exact round-half-even oracle

### 2026-10-11

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #403** (closed): PR #195: regenerate report.json — stale source_commit has blocked CI across 5 fallback-review passes and 11+ rebases
- **Issue #354** (closed): verify_voice --set full fails on R1's published voice_dp.v (osc2x+filter2x): an audition reference sequence and extremes
- **PR #646**: #354: gate the all-max image and pin R1's ladder-width control to its signature; current-tree closure receipts
- **Issue #361** (closed): descent_test's resample-ratio sweep never tests ratio 1.0 exactly (drops one sample at 'x1.0000')
- **PR #645**: fix(harness): descent_test identity candidate is exactly ratio 1.0 (#361)

### 2026-10-10

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #347** (closed): M5 pulse preset upper-partial deficit (4.9–5.3 dB vs Mini V3): cutoff/voicing question
- **Issue #339** (closed): Late-event policy P3 vs RTL: a late event with an earlier sent due than a draining tail is dropped (decide policy or next-image RTL)
- **PR #639**: [#324] R1 capture inputs: R1-bound references and commands; T-PHYSICAL capture-r1 refuses R0 material
- **Issue #163** (closed): Root cause: symmetry of code is not symmetry of treatment — three comparisons where both sides called the same function and were handled differently
- **PR #638**: Preparation contract at the drum pair site: refuse unlike-prepared sides (#163, first slice)

### 2026-10-09

Closure events below record forge state, not a verified fix or a new sound result.
- **Issue #631** (closed): ci: replace retired "(D5)" Blacksmith citations in workflow comments (needs workflow scope; 2am#3914)
- **PR #632**: ci: cite the Blacksmith decision by section, not the retired "(D5)"
- **Issue #609** (closed): Test and inject-control tools/r1_harvest.py: the R1 receipt_valid verdict has no test
- **PR #628**: test: cover r1_harvest receipt_valid verdict with injected controls
- **Issue #620** (closed): Vacuous-pass guard audit: standing check for verdicts that pass on an empty population
- **PR #627**: tools: vacuous_guard_audit, a standing check for verdicts that pass on an empty population
- **Issue #623** (closed): Nightly sound-report red: CP (clap) decay tau, T20 and attack outside tolerance, two of them locks
- **Issue #610** (closed): Follow-on from PR #604: preserve measurement errors and corrupt RTL anchors
- **PR #613**: Preserve corrupt anchors and measurement error types (#610)
- **PR #614**: [#152] Residual drum DC: offset-vs-skirt apparatus + dev-condition run (partial; confirm batch pending, #152 stays open)
- **Issue #608** (closed): File the Icarus Verilog generate-case-over-string-parameter bug upstream (the -g2005 workaround is unfiled)
- **PR #612**: [#608] Icarus generate-case string-param upstream report + guard
- **Issue #611** (closed): Follow-on from PR #607: qualify lead-drive refusal controls and preserve experiment limits
- **PR #616**: [#611] Lead-drive refusal controls (baseline mismatch, unchanged audio), NaN fix, README correction
- **PR #606**: Full-performance suite with seeded-defect controls (#338)
- **PR #607**: [#337] Lead ladder drive: saw 0.50 confirmed on untouched note (still failing), pulse no candidate
- **Issue #600** (closed): Measurement catches that turn bugs into 'estimator refused' (sound_report.m_noise_share, moog_probe.scan, 11 more)
- **PR #604**: Measurement catches no longer turn bugs into 'estimator refused' (#600)
- **PR #601**: [#557] BD pitch envelope: pre-tuning freeze (conditions, metric, min-improvement rule, preservation limits)
- **Issue #568** (closed): synth_count.sh prints blank cell counts and exits 0 when yosys fails
- **PR #596**: [#568] synth_count: refuse instead of printing blank cell counts
- **Issue #564** (closed): Collect the 57 in-file tests that pytest never runs (excitation_energy, dc_blocker, discrimination_*)
- **PR #590**: Collect the 57 in-file tests pytest never ran, with a collection-inventory guard (#564)
- **PR #597**: [#556] MA/RS stage-attribution instrument (measurement only, no repair)
- **PR #589**: #558 toms/congas vs the gate: negative under the frozen rule; post-hoc, a BP numerator cuts conga pitch_shape (waiting on #350); impulse moves with the gate's rate path (#588); no sound change
- **PR #587**: [#557] BD pitch-trajectory estimator, qualification and baseline script (instrument only)
- **PR #586**: Envelope response: prior-note attack shortening is not residual envelope level (#337)

### 2026-10-08

Closure events below record forge state, not a verified fix or a new sound result.
- **PR #583**: M1A phase-aware drive question; drive 0.25 confirmed at matched phase but not promoted; partial #337.
- **PR #581**: Bind trial-receipt membership to the recorded registry and refuse empty required membership.
- **PR #580**: Record revision 9 registers and the 4-bit wave code in the numeric contract.
- **PR #579**: Refuse real MIDI sessions when an internal injection set is non-empty.
- **Issue #283** (closed): Trial receipt accepted deleted or empty required-child membership; PR #581.
- **Issue #321** (closed): Revision 9 registers and wave-code width were missing from the numeric contract; PR #580.
- **Issue #288** (closed): Real MIDI sessions did not refuse an active internal injection set; PR #579.
- **Issue #426** (closed): Deadline-evidence stimulus binding; closure records forge state, not a fresh timing verdict.
- **Issue #573** (closed): Duplicate checkpoint-write guard telemetry; canonical finding #571 remains open.
- **Issue #575** (closed): Duplicate Python-deletion guard telemetry; canonical finding #572 remains open.
- **Issue #576** (closed): Duplicate literal-body-path guard telemetry; canonical finding #574 remains open.

### 2026-10-07

- **PR #554**: Shared-bus DC coupling model and contract (#551); partial #510, RTL and I2S qualification remain open.
- **PR #550**: Model-side glide boundary check; partial #247, RTL reproduction remains pending.
- **PR #549**: Model-side resonance-keyed cutoff correction; partial #257, RTL PR #555 remains open.
- **PR #545**: Reconcile filter-rolloff claims after the corner repair.
- **PR #544**: MARS calibration instrument; calibration REFUSED, existing diagnostic ranking retained; partial #379.
- **PR #543**: BD pedestal instrument and mechanism investigation; partial #220.
- **PR #542**: Pekonen-style coloration measured and declined.
- **PR #541**: Refuse unreachable attack-context source commits.
- **Issue #551** (closed): Model and numeric-contract increment of cymbal DC coupling.
- **Issue #333** (closed): Sound batch 1, unwanted-artifact repair tracking.
- **Issue #314** (closed): Stale D02A trial-release evidence documentation.
- **Issue #243** (closed): Pekonen-style oscillator coloration candidate; PR #542 records the decision to decline it.
- **Issue #215** (closed): Unreachable attack-context source-commit refusal, PR #541.
- **Issue #169** (closed): Filter-rolloff claim reconciliation, PR #545.

### 2026-10-06

- **Issue #207** (closed): Continued sound-qualification lane tracking.
- **Issue #206** (closed): Evidence-gated DSP feedback publication path.

### 2026-10-05

- **PR #537**: Restore noise slope control and record margins and fixture comparison.
- **PR #536**: Positive grammar for corpus serial provenance.
- **Issue #528** (closed): Noise slope control margin investigation.
- **Issue #527** (closed): Serial grammar accepting an unknown serial.
- **Issue #382** (closed): Cymbal Hh2 and LEVEL-corner investigation tracking.

### 2026-10-03

- **PR #532**: Reference-audio S3 route and BD take-to-take repeatability.
- **Issue #111** (closed): Machine-spread measurement, PR #532.

### 2026-10-02

- **PR #531**: Remove no-op npm scripts.
- **PR #529**: Synthetic estimator ground-truth suite and controls; partial #158.
- **PR #526**: Permitted-differences regression suite; partial #158.
- **PR #524**: Real-recording perturbation ladder.
- **PR #523**: Reference corpus lineage and qualification.
- **PR #511**: DC-blocker probe and preservation-apparatus corrections.
- **Issue #525** (closed): No-op npm scripts.
- **Issue #520** (closed): Corpus lineage documentation and qualification.
- **Issue #519** (closed): Permitted-differences regression suite.
- **Issue #518** (closed): Real-recording perturbation ladder.
- **Issue #517** (closed): Synthetic estimator ground-truth suite.
- **Issue #165** (closed): DC-blocker prototype; the joint CY/RS candidate did not trigger shipping.
- **Issue #45** (closed): Mechanical process-fix tracking.

### 2026-10-01

- **PR #509**: [dag] propagate RED/BLOCKED dependency status in classify (#140)
- **PR #508**: Read the promoted metrics' floor from the harness that measures floors (#138)
- **PR #507**: Promote cqt.0-200Hz and jit.period_ms to refusing estimators; floor unmeasurable (#138)
- **PR #505**: fpga/spi_host.py: fix hits() anchoring claim, pin 15.7.1 sequence-step movement (#496)
- **PR #503**: Wire causality battery into _Plugin.qualify for Surge and Mini V3 (#137)
- **PR #499**: Add mechanism=<status> claim modifier: effect and mechanism are separate claims
- **PR #501**: NaN fails closed at every audio-entry boundary (#134)
- **PR #500**: Qualification is a verdict per (rig, host, capability), not one boolean per rig
- **PR #495**: spec: the model's write burst is not a wire schedule — 15.7.1 gains the anchor policy
- **PR #516**: Edge-leak cause and normalization decision.
- **PR #514**: Low-band onset metric and edge-straddle precondition; partial #138.
- **PR #513**: Claim-type checks and close-time search policy.
- **Issue #515** (closed): Structural edge-leak refusal.
- **Issue #496** (closed): Unanchored SPI coefficient sequences.
- **Issue #455** (closed): Missing repo remote skill installation.
- **Issue #155** (closed): Claim-type checks and close-time search policy, PR #513.
- **Issue #143** (closed): Knob-equivalent comparison-scale tracking.
- **Issue #140** (closed): Evidence-file existence used as a DAG verdict.
- **Issue #137** (closed): Reference-driver command-effect verification.
- **Issue #136** (closed): Reference qualification by capability.
- **Issue #135** (closed): Unverified mechanism used as a decision premise.
- **Issue #134** (closed): NaN defeating comparison guards.
- **Issue #131** (closed): Physically deliverable write-sequence contract.

### 2026-09-30

- **PR #494**: docs(CLAUDE): point at the shared build-box runbook; list the .env keys
- **Issue #129** (closed): Prose verdicts live inside a hashed input, so a comment cannot be corrected without invalidating measurements
- **PR #493**: Split refprofile/profile.json's prose out of the hashed evidence
- **Issue #224** (closed): Sensitivity-sweep gate before optimisation work: define and demonstrate it (#45 item 5)
- **PR #229**: feat: gate optimisation work on a committed sensitivity sweep
- **Issue #404** (closed): The cymbal-369 gates and their must-fail controls are not run by any CI job
- **PR #427**: CI collects tools/ as a directory, and runs the cymbal gates as their own steps
- **PR #355**: R2: pulse2x (operator override), skip2xwin, #354 ladder repair, #315 -- built, bound, --image r2
- **Issue #127** (closed): The f0 tolerance cannot distinguish any two settings of the bass drum
- **PR #492**: f0 tolerance: score the bass drum against its own travel, not against 10 % of a unit-to-unit spread
- **Issue #242** (closed): sound_report.py: two locked properties (LADDER corner ratio drift, LT attack) have drifted past tolerance, unnoticed by a broken nightly gate
- **PR #269**: sound_report: two locks re-locked to the commits that moved them, and the nightly gate that should have caught them
- **Issue #484** (closed): Remove tools/compile_dag.py:mtime_commit: dead since introduction, superseded by stale_against
- **PR #490**: tools: drop compile_dag's mtime_commit, dead since the file was written
- **Issue #481** (closed): refprofile.load_clip refuses silence at 1e-9 but nothing asserts a frozen clip's LEVEL
- **PR #491**: refprofile: gate a frozen clip on its LEVEL, not on silence (#481)
- **Issue #125** (closed): Mine the prior plugin-corpus work in ~/dev before rebuilding what it already solved
- **PR #487**: pedalboard is installable on Linux; a stub bundle must REFUSE, not traceback (#124)
- **PR #489**: ci: move off Blacksmith to GitHub-hosted runners (operator decision 2026-09-30, D5)
- **Issue #432** (closed): Cymbal: the three envelope generators' peak collector voltages (Q16/Q17/Q18), read off SN p.13
- **PR #488**: Cymbal: the three envelope generators' peak collector voltages (Q16/Q17/Q18), read off SN p.13
- **Issue #483** (closed): Remove tools/trials_pilot_demo.py: superseded one-shot pilot demo, zero references
- **PR #485**: chore: remove superseded zero-reference trials pilot demo script
- **PR #480**: A pedalboard-hosted Model D rig, with the dawdreamer rig's qualification discipline and #137's causality checks
- **Issue #449** (closed): Button drum demo: Arty BTN0-3 trigger four 808 voices, no laptop
- **PR #454**: Button drum demo: Arty BTN0-3 trigger four 808 voices, no laptop (#449)
- **PR #433**: cymbal #369 step 11: the swing VCAs read off SN p.13 — there are not three drives, and the one term there cannot close the balance
- **PR #195**: Measure selected-model attack in frozen note-history contexts
- **PR #395**: ci: fork-gate every Blacksmith runner label — fork PRs to GitHub-hosted
- **PR #467**: Bring-up that asserts its preconditions, and the gate lesson from getting it wrong
- **PR #468**: Update Repo Skills to v0.16.1

### 2026-09-29

- **Issue #472** (closed): verify_sd_dac.py's silence case has no injected control
- **PR #477**: fpga: give verify_sd_dac's silence case an injected-bug control
- **Issue #474** (closed): verify_rolling_playback._unwrap assumes consecutive writes are < 0x8000 frames apart without checking
- **PR #478**: assert the frame-log unwrap's precondition instead of assuming it
- **Issue #471** (closed): effective_pins only reads single-line PACKAGE_PIN, would miss -dict form
- **PR #476**: fpga: refuse an unparseable PACKAGE_PIN line in effective_pins
- **Issue #123** (closed): Root cause: every external-tool finding we made was a property of a configuration, recorded as a property of the tool
- **PR #470**: Require environment tuple and second route for external-tool claims
- **Issue #466** (closed): play_song tests: frame unwrap breaks on long rests; note-offs checked only against the code under test
- **PR #469**: test(play_song): long-rest fixture, safe unwrap, independent note-off check
- **Issue #465** (closed): test_constant_port_evidence_matches_vivados_own_warning tests its own regex, not the build gate's
- **PR #473**: fpga: share the constant-port-evidence regex between gate and test
- **PR #409**: play_song.py: play a tune over the USB-UART link
- **Issue #406** (closed): Sigma-delta audio out on a Pmod: hear the Arty build without the PCM5102 breakout
- **PR #408**: Sigma-delta no-DAC demo output on Pmod JD (#406)
- **Issue #115** (closed): Estimators should declare their validated domain and refuse outside it
- **PR #461**: Estimators declare their validated domain and refuse outside it (#115)
- **Issue #457** (closed): CI: skip suites a PR's changes cannot affect (file filters, minimum) and stop double-running push + pull_request
- **PR #464**: CI: skip suites a change cannot affect, fail-safe, and still report (#457)
- **Issue #116** (closed): Seal the scorecard's holdout cases before they're read (F1D/F2D/F3D/F5D) — items 1-2 of this issue already landed via #141
- **PR #462**: [#116] seal the scorecard's holdout cases before they are read (F1D sealed; F2D/F3D/F5D re-justified)
- **PR #456**: Install rjwalters/repo skills (repo-remote.sh et al.)

### 2026-09-28

- **Issue #117** (closed): Root cause: reasoning substituted for execution, when execution was available and cheaper
- **Issue #443** (closed): Deadline evidence: the -l2 capture binds to stimulus bytes, not to the schedule-relevant projection, so it goes stale on value-only changes
- **PR #451**: Deadline evidence: bind the -l2 capture to the schedule-relevant projection of its stimulus
- **Issue #431** (closed): cymbal_band_balance: the balance's preconditions gate on a file existing and never read it, so any file lifts the refusal
- **PR #448**: cymbal_band_balance: filter-figure and vca-drive preconditions now read their content
- **Issue #436** (closed): No bench record hashes the XDC, so check_arty_evidence_binding --scope publication can only REFUSE
- **PR #450**: constraint-binding evidence for the Arty XDC, so publication scope can answer
- **Issue #435** (closed): check_doc_claims scans only docs/*.md, so a claim marker anywhere else is a check that never runs
- **PR #447**: check_doc_claims: widen the default document set to an explicit include list, and refuse on markers outside it
- **PR #446**: ci: move short and scheduled jobs off Blacksmith to GitHub-hosted
- **Issue #429** (closed): cymbal #369 step 10 follow-up: the tone-render record is dirty-tree, and three named-property figures disagree with the tool that computes them
- **PR #445**: fix: regenerate the dirty-tree tone-render record and stop the serialiser coercing flags
- **PR #444**: Deadline evidence: re-capture the -l2 stress-saw traces against this tree's stimulus
- **Issue #441** (closed): Dead code: reference_voice's shark-tooth TARGET report is unreachable after _floor_fmt's returns
- **PR #442**: fix: re-attach the shark-tooth TARGET section to report_osc
- **Issue #425** (closed): cymbal tone term: the resolved bound excludes component tolerance, and says so — decide whether to carry one
- **PR #440**: feat: publish tone-stage component sensitivity and its cited tolerance term
- **Issue #422** (closed): CI: 'python' job segfaults (exit 139) in model/drums_fx.py during model/test_drum_fit.py — non-deterministic, same commit passes on the other trigger
- **PR #439**: #422: the CI segfault measured rather than re-run — 0/120, a pinned arm the first pass assumed, and the gate that cannot fail (#438)
- **Issue #421** (closed): check_arty_evidence_binding's live gate has no constraint-drift coverage: drift() and historical() exclude build.XDC
- **PR #437**: [#421] check_arty_evidence_binding: a publication scope that sees the XDC
- **Issue #411** (closed): Cymbal candidate 4: the mid band's LEVEL, not another filter — candidate 3 overcorrects Hh1 by ~4 dB
- **PR #434**: cymbal (#369) step 11: Hh1's own gain, restored to its circuit value — REFUSED
- **Issue #420** (closed): cymbal balance: re-derive the tone term from #390's nodal solution, which resolved a precondition #396 could only bound
- **PR #430**: fix: re-derive the cymbal tone term from #390's nodal solution
- **PR #428**: cymbal #369 step 10: the TONE knob on a rendered candidate — the law is not the blocker, the balance is
- **PR #424**: Cymbal (#369) step 9: the TONE knob is VR4's wiper — a law from the circuit, and the corpus gate it claimed but could not run
- **PR #423**: cymbal (#369) step 8: §10's asymmetric clipping eliminated, and step 7's 9.4 dB gap corrected to 1.3
- **Issue #396** (closed): Cymbal candidate 3: apply the tone stage's measured tilt only, levels unchanged
- **PR #414**: cymbal (#396): the inter-band balance REFUSED, with the blocking factor measured — it is the VCA drives, not Figure 9's window
- **Issue #401** (closed): pytest tools/ is red on main: the R0 evidence-binding gate's historical list is stale after 383f10b changed arty-a7-100.xdc
- **PR #419**: [#401] R0's binding gate: name the publication-vs-verification scope gap
- **Issue #416** (closed): test_r0_published_image_is_bound_to_its_historical_source_set is red on main
- **Issue #390** (closed): Cymbal tone stage: read W14b Figure 9's three fifth-order families, the last unmodelled block of reference §10
- **PR #417**: feat: resolve the cymbal tone-stage inter-band balance from SN p.13
- **PR #418**: cymbal (#369) step 7: the 808's 1-2.5 kHz outlasts its low band at all 25 settings, ours dies first, and no documented filter can do it
- **PR #410**: cymbal (#369) step 6: the 1-2.5 kHz decay qualified — and §6's decay reading refuted; the defect is a level, not a decay
- **Issue #388** (closed): D10A rimshot: our high bridged-T mode is 18.7 dB too weak relative to the low mode (broad shelf where the machine has a resonance)
- **PR #412**: fix: correct the rimshot's two bridged-T modes' relative drive (contract revision 15)
- **Issue #415** (closed): PR #195: attack-context evidence bound to an unpushed commit; analysis source has since drifted
- **Issue #393** (closed): gitignore: add .claude/skills/repo/logs/ so guard-hook logs don't block fleet resync (2am#1184)
- **PR #407**: gitignore: add .claude/skills/repo/logs/ so guard-hook logs don't block fleet resync
- **Issue #389** (closed): balance_trajectory_db reports a balance on pure white noise instead of refusing (D10A/D13A floor gate compares a biased pick against an unbiased floor)
- **PR #405**: fix: REFUSE a partial balance when the found line is not a resolved mode (#389)

### 2026-09-27

- **PR #402**: cymbal (#369) step 5: the tone stage's tilt applied — strike solved (worst 15.4 → 5.7 dB), and the 1–2.5 kHz tail is not a filter
- **Issue #385** (closed): The kit's inline MEASURED literals have no name, so they cannot carry provenance
- **PR #399**: Name kit_808's five inline MEASURED literals so PROVENANCE can see them
- **Issue #386** (closed): TOM_DROP_RATIO's comment calls a fitted value measured
- **PR #398**: fix: correct TOM_DROP_RATIO comments to say fitted, not measured
- **PR #397**: cymbal (#369) step 4: the tone stage is the missing tilt (W14b Fig. 9 digitised)
- **Issue #239** (closed): Rungs 2-4: a candidate-ladder comparison harness, and DR 0001's reversal condition
- **PR #262**: docs: re-derive discrimination.md 8.4/8.6 ours rows against the shipped filter
- **PR #384**: #379 perceptual gate: proof, 16-sound ranking, and why the same-unit bar cannot pass anything
- **PR #391**: #369 cymbal step 3: Hh2, Hh3 and the level stage read off W14b Figs 4/10 (§18 closed); candidate 2 measured and negative
- **Issue #298** (closed): Live MIDI: expose the full 16-sound kit (congas, rimshot, maracas) and test exclusive-pair switching
- **PR #392**: Live MIDI: all 16 808 sounds, exclusive-pair switching with tail cut (#298)
- **Issue #380** (closed): D10A rimshot Partial balance is no-verdict for our own model: mid-band (900-1100 Hz) content swamps the high partial's floor margin
- **PR #394**: fix: D10A's lower RS guard was reading the strike, not the floor
- **Issue #114** (closed): Constants should carry provenance as data, so "what does this failure rest on" is a query
- **PR #387**: Constants carry their provenance as data, so "what does this rest on" is a query
- **PR #364**: R2: ladder (x*gain)>>11 in 26 bits -- the first wrong operation behind #354
- **Issue #376** (closed): Cymbal crosstalk control cannot fail: use the 808's Q6 skirt and a paired negative
- **PR #383**: cymbal: the crosstalk control can now fail (Q6 skirt + paired negative, #376)
- **Issue #109** (closed): The scorecard collapses time-varying quantities into single windows, and one metric charges decay twice
- **PR #381**: D10A/D13A partial balance: a floor-gated trajectory, not one averaged window (#109)
- **PR #374**: #369 cymbal step 2: §10 candidate overcorrects at CY5025 (negative); ablation localizes the differentiator and Hh2's corner
- **Issue #368** (closed): Fix stale, self-contradicting header comment in rtl-sketch/modal_dp_rom.v
- **PR #378**: docs: correct modal_dp_rom.v's stale header to describe the ROM design
- **PR #351**: #334 tom/conga body spectrum: shared cause localized; numerator fix blocked by the modal deadband (#350)
- **PR #375**: docs: follow-up wording on #366/#370 (gain sign flip; M5B foldback cells)
- **PR #371**: #369 cymbal step 1: qualified band measurement; shipped cymbal's defect localized (Hh1 missing, top octave short)
- **PR #366**: #347 pulse-cutoff sweep: negative (brightness only by trading aliasing)
- **PR #370**: R2 pulse bounded repair (rectangle level into the ladder): NOT ACCEPTED
- **PR #344**: skip2xwin re-checked on current sources, with its negative control (#333)
- **Issue #313** (closed): RTL evidence reuse: record the simulator version in the run identity, and per-child reuse in the receipt
- **PR #346**: Reuse identity: the simulator version in the RTL run identity; per-child reuse in receipts (#313)
- **Issue #323** (closed): Named R1 host selection: an explicit frozen R1 profile bound to #317's release, without changing defaults
- **PR #365**: Named, frozen R1 selection --image r1; image-aware R1 host binding (#323)
- **PR #343**: M5A/M5B artifacts: reproduce on R1, localize by stage, confirm pulse2x (#333)
- **Issue #316** (closed): dsp_dpreg_extract.py cannot run on the build box itself (ssh/scp to self)
- **PR #363**: dsp_dpreg_extract: --box local runs on the Vivado host without ssh to self (#316)
- **PR #362**: AGENTS.md/CLAUDE.md: the current milestone's sound goal outranks the canary goal
- **PR #357**: #354: the verify_voice full-set failure is outside R1's qualified domain (carried state from the all-max image)
- **Issue #315** (closed): Arty XDC: the UART-RX synchroniser constraints match no objects (dead in R0 and R1)
- **PR #358**: XDC: UART-RX sync constraints bind g_uart.u_uart; build asserts binding and effect; publisher checks reports (#315)
- **PR #348**: Shuttle half slot: the route's last mile, its instrument, and a tool finding withdrawn
- **PR #359**: r1_release: pin the ext-I/O instrument by version (git), not the working tree
- **Issue #322** (closed): Live MIDI on macOS: a tested CoreMIDI input path for a physical controller (Launchkey 37)
- **PR #330**: Live MIDI on macOS: CoreMIDI input adapter, tests, smoke procedure, host latency (#322)
- **Issue #104** (closed): Agent deliverables must be reusable instruments, not scratchpad probes
- **PR #360**: Extract shared measurement-validation harness (model/measure_harness.py)
- **PR #356**: Release: R1 binds its RTL from git at the freeze, not the working tree (unblocks a second image)
- **Issue #340** (closed): r0_capture analyse: an I/O error at the final record write exits 1 (FAIL code) instead of 3 (ERROR)
- **PR #352**: r0_capture analyse: an I/O error writing the record exits 3 (ERROR), not 1 (FAIL)
- **Issue #319** (closed): r1_release: re-derive the ext-I/O record from its raw paths; three small R1 doc/scorecard accuracy fixes (from #317 review)
- **PR #332**: r1_release: re-derive the ext-I/O record from raw paths; evidence-derived scorecard flags; R1 doc fixes (#319)
- **Issue #342** (closed): Five LibreLane/OpenROAD findings from the half-slot route are unfiled, and the agents that found them have no upstream access
- **PR #349**: File the half-slot tool findings upstream and record each outcome (#342)
- **Issue #102** (closed): The cymbal's Hh3 is third-order in the reference and a single 2-pole in the model
- **PR #345**: [cy] The cymbal's Hh3 stays a two-pole: a third pole built, priced, measured and rejected on a holdout (#102)
- **Issue #329** (closed): uart_device_sim: a late event waits a full counter revolution and blocks the event FIFO
- **PR #341**: Late events: the real UART RTL vs a pre-stated policy; repair the device model's revolution stall (#329)
- **Issue #94** (closed): Every scorecard case says fixed-model: nothing has been measured on the instrument
- **PR #299**: E0: R0 capture procedure, R0 reference audio, capture analysis, T-PHYSICAL
- **Issue #250** (closed): Decision-record numbering can silently collide across concurrent PRs (0017 allocated twice; 0017 collides again on PR #244)
- **PR #328**: [dr] give every decision record its own number, and check it
- **Issue #301** (closed): NUMERIC-CONTRACT: revision 9 has no section-18 entry, though the contract cites "revision 9's pins"
- **PR #326**: spec: write the missing section-18 Rev 9 entry, and empty KNOWN_MISSING
- **PR #327**: docs: diagnose the uid-blind orphan reap and add a probe for unsignalable worktree processes
- **Issue #307** (closed): the CI-wiring guards pass on a step gated by `if: false` or `continue-on-error: true`
- **PR #320**: Guard CI-wiring tests against a step that would never actually block
- **Issue #280** (closed): R1 player preview: build, bind and publish the Arty image
- **PR #317**: R1 player preview: build, bind and publish the Arty A7-100T image (#280)
- **Issue #292** (closed): Anchor one ensemble case on integrated-rtl (#94 remaining scope: Ensemble)
- **PR #309**: E1A on the integrated RTL: the first Ensemble row measured on the chip
- **Issue #318** (closed): Rebase can orphan a scorecard result's source_commit (seen on E1A, PR #309)
- **PR #312**: Shuttle half slot: the LibreLane run, and the two questions it has not answered yet
- **Issue #291** (closed): Anchor one filter case on integrated-rtl (#94 remaining scope: Filters)
- **PR #311**: feat: anchor F1A on integrated-rtl (#291)

### 2026-09-26

- **Issue #290** (closed): Anchor one drum case on integrated-rtl (#94 remaining scope: Drums)
- **PR #308**: D02A on the integrated RTL: the first Drums row measured on the chip
- **Issue #279** (closed): R1 player preview: freeze and qualify the complete digital candidate (OSC2X=1 FILTER2X=1 PULSE2X=0 + L2)
- **PR #300**: R1 player preview: freeze and qualify the rev-14 digital candidate (#279)
- **Issue #271** (closed): reference_rigs.py oscillator-1 comment table disagrees with docs/surge-waveform-mapping.txt, and cites a sweep file that never existed
- **PR #304**: reference_rigs: the oscillator-1 Shape/Width table is now a checked transcription of the sweep
- **Issue #61** (closed): Our oscillators alias 19-32 dB more than both references, and it worsens with pitch
- **PR #305**: Issue #61: the saw's aliasing after the 2x decimator — closed against Mini V3, narrowed against Surge
- **Issue #264** (closed): moog-acceptance.yml intermittently times out at the 20-minute job cap under concurrent CI load
- **PR #303**: ci: refute the #264 contention theory with per-step timing, keep both triggers
- **Issue #293** (closed): NUMERIC-CONTRACT: revision 12 is assigned to both drift (DR 0019) and polyBLAMP (DR 0017) on main
- **PR #302**: test(spec): guard NUMERIC-CONTRACT revision numbers against collision
- **Issue #95** (closed): All three toms fail the same pitch-drop check — and the spec's magnitude is inferred, not measured
- **PR #295**: Toms' pitch drop: the reference documents still said x1.7, and nothing was checking (#95)
- **Issue #296** (closed): trial-release-bound is red on main since #244: the release manifest's verification record no longer binds
- **PR #273**: Clap L2 in production: ENV_FRATE final strike (contract rev 14), model = RTL = I2S, D12A rescored (timing UNQUALIFIED)
- **Issue #276** (closed): reference_integrity.transient_report: hp_hz is a dead parameter
- **PR #294**: reference_integrity: remove the dead hp_hz parameter from transient_report
- **Issue #297** (closed): release_manifest.check() REFUSED on main: uart-clean verification.json does not hash to publication.json's recorded value
- **Issue #48** (closed): Oscillators: the shark-tooth needs BLAMP as well as BLEP, and noise needs a spectrum
- **PR #244**: The shark-tooth's corner: polyBLAMP as well as PolyBLEP, model and RTL (DR 0017)
- **Issue #100** (closed): Fitting policy: CMA-ES on a float surrogate, never gradient descent on the shipping model
- **Issue #99** (closed): Two definitions of "closer to the machine" disagree — which one is the judge?
- **Issue #85** (closed): The plan, as five steps — and what is actually running against each
- **Issue #79** (closed): The execution DAG: what can run in parallel right now, and what is genuinely blocked
- **Issue #77** (closed): Reference-rig qualification must be an automated preflight, not a one-time investigation
- **Issue #71** (closed): Agents run for two hours because they are briefed with workstreams, not deliverables
- **Issue #278** (closed): R1 release trials: stale-manifest control for T-RELEASE-BOUND, make it blocking, T-PLAY-DIGITAL on the landed integration
- **PR #289**: R1 release trials: stale-manifest/binding controls for T-RELEASE-BOUND, gate it, T-PLAY-DIGITAL on landed main (#278)
- **Issue #281** (closed): Live MIDI host session (simulator half): T-LIVE-MIDI
- **PR #286**: Live MIDI host session, simulator half: T-LIVE-MIDI (#281)
- **PR #261**: D12A plan081 B/C: burst timing unqualified; explicit final strike (L2) confirmed on fresh offsets -- model improvement, promotion incomplete
- **PR #255**: Release: Arty A7-100T baseline 2025.1 r1 manifest (OSC2X=1 FILTER2X=1 PULSE2X=0), with enforced supported domain
- **PR #284**: CLAUDE.md: headless sessions must not end the turn on background work
- **PR #274**: Trials pilot: make trial T=<id> for T-RELEASE-BOUND, T-DEADLINE, T-PLAY-DIGITAL
- **Issue #225** (closed): reference_integrity.transient_report: 5 ms default blocks falsely flag clicks on low held notes (MIDI 36)
- **PR #277**: transient_report: scale blocks to the note period when f0 is known (#225)
- **Issue #68** (closed): Version rendering and analysis separately — we cannot currently answer "did the sound change, or only the measurement?"
- **Issue #259** (closed): Wire render/analyse/accept manifest artifacts into CI (upload-artifact + retention) and one real scorecard case
- **PR #275**: Version rendering and analysis separately: render / analyse / accept on a real case, with the retained audio
- **Issue #245** (closed): Two shipped build-tool bugs are still not injections: an X netlist with an area, and a die area recovered from its own utilization input
- **PR #272**: Two shipped build-tool bugs become injections: an X netlist gets no area, a die from its own utilisation target gets no ratio
- **Issue #254** (closed): verify_ctl: the blindness matrix's two refusal paths can report a run that was never measured
- **PR #268**: verify_ctl: stop the blindness matrix's refusal paths from reporting an unmeasured run
- **PR #266**: docs/trials.md: root-cause analysis of the back-and-forth, and trials as the unit of work
- **Issue #263** (closed): Remove duplicate oscillator-1 comment block in reference_rigs.py
- **PR #267**: docs: remove superseded duplicate of Surge oscillator-1 comment
- **PR #186**: .github/workflows: Migrate workflows to Blacksmith runners
- **Issue #265** (closed): acceptance CI workflow is creeping toward, and now past, its 20-minute timeout
- **PR #260**: feat: add render/analyse/accept manifest scheme with retroactive reanalysis
- **PR #253**: D12A clap burst/tail: reproduction and diagnosis (late envelope; stopped at burst-timing qualification)
- **PR #256**: CLAUDE.md/AGENTS.md: run heavy work on the build box, not the laptop
- **Issue #237** (closed): The cutoff knob means a different thing at each resonance: 105 cents of travel
- **PR #258**: Ladder cutoff offset: retain the res=1.05 operating point, prove a single constant can't close the resonance-dependent travel, add the missing injected control
- **Issue #56** (closed): Oscillator drift: three stable oscillators do not sound like three analog ones
- **PR #252**: Oscillator drift: the references bound it, so the amount is a choice (contract 6.11, DR 0019)
- **Issue #52** (closed): Testing policy: every bug becomes a permanent injection, and every suite reports what it is blind to
- **PR #251**: test: every shipped measurement bug becomes a permanent injection, and a second suite reports its blindness
- **Issue #47** (closed): Gain structure is part of the sound model, not a volume control
- **PR #238**: Gain structure as tested nodes: combined drums+bass headroom, H(0)=1/(1+k), and a two-score bass-compensation pair
- **PR #248**: T1: the three-saw production deadline — baseline fits (observed min slack 6, stress 14); PULSE2X=1 excluded; component bench launches at the chip's cycle
- **Issue #46** (closed): The filter ladder: four rungs, compared in software before anything ships
- **PR #249**: measure: rungs 2-4 compared, and the candidate that wins on sound and cannot be afforded
- **Issue #53** (closed): Movement: nothing tests what the filter does while the cutoff is moving
- **PR #246**: Movement, part two: the resonance moving, and filter modulation at audio rate
- **Issue #141** (closed): The cowbell's 11.34 is a metric direction error: we have 34 dB LESS of the component, not more
- **PR #241**: Cowbell #141: the difference tone is a defect ceiling — a rubric change, not a sound change
- **PR #235**: F1: selectable calibration surge-type2-clean-v1; official F1A–F1C on the selected filter path (3 passes)
- **PR #240**: measure: what is left in the shipped ladder, and the tuning offset nothing was watching
- **Issue #233** (closed): refprofile.render records pins_held_after_render: true without checking; Surge XT renames params 259/260/264/265 after the first render
- **PR #234**: refprofile: check pins after every clip render; derive pins_held_after_render (#233)
- **Issue #49** (closed): Playing behaviour: choose our rules deliberately, then test them
- **PR #236**: docs: decide the external feedback loop's tap, gain, delay and saturation

### 2026-09-25

- **PR #231**: measure: F1 matched-level Surge references + one compensated input-scaling experiment
- **PR #232**: measure: F1A-F1C board records at the current estimator (measurement-version update)
- **Issue #222** (closed): Repo-wide ground-truth gate: extend the 808 suite's claim/backing-test meta-test beyond one file (#45 item 1)
- **PR #227**: test: extend the claim/ground-truth gate to test_moog_acceptance.py
- **Issue #223** (closed): Doc-claim-to-evidence checker: flag prose claims whose backing test is missing, passing-when-cited-as-failing, or stale (#45 item 4)
- **PR #230**: Doc-claim-to-evidence checker, and the failure-modes status list it was commissioned to correct
- **PR #228**: measure: current F1A-F1C cutoff baseline on the selected filter path
- **PR #226**: M1A drive experiment: no candidate chosen -- drive fixes odd-partial phase sensitivity but worsens even partials
- **PR #219**: M1A phase-cycle capture: Mini V3 at all relative phases -- INCONCLUSIVE under the pre-registered rule
- **Issue #21** (closed): R8: shaped excitation pulse — the ~90% discrimination term
- **PR #221**: docs: BD excitation-shape probe for #21 -- mechanism confirmed, blocked by #220, kit unchanged
- **PR #218**: M1A harmonic-shape diagnosis: phase-sensitive mix; no sound change supported yet
- **PR #217**: M1A attack: record the stopping point and two qualifications
- **Issue #22** (closed): R9: complete the 808 — the eight voices we don't have
- **PR #214**: M1A: known-answer qualification of the attack measurement (m1a-envelope-score-v3); still no verdict
- **Issue #1** (closed): Product target: USB-C bus-powered class-compliant sound module (keychain)
- **PR #216**: docs: the keychain dongle, itemised from manufacturers' drawings (#1 board fit)
- **PR #212**: arty: bind integrated-baseline DSP evidence to routed.dcp by digest; extractor records the digest


# Work log

Recorded forge events; merge or closure alone does not establish sound qualification or hardware delivery. Initial coverage: the twenty most recently merged PRs, excluding Guide maintenance, and recently closed issues since 2026-10-01. Earlier events are outside this initial log.

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

### 2026-09-24

- **PR #210**: Musical-length playback: rolling UART delivery, qualified through the RTL
- **PR #211**: M1A: select the −4 dB gain patch; two-sided attack qualification; exploratory attack sweep
- **PR #209**: publish: derive dsp_feedback_review_complete from DCP-bound evidence
- **PR #197**: measure: publish M1A gain improvement and diagnose oscillator cancellation
- **PR #203**: Qualify M1A attack measurement: envelope becomes a valid metric; M1A valid fail (21/100)
- **PR #199**: Build and publish Arty A7-100T baseline with SPI-to-I2S verification
- **PR #202**: USB-UART control bridge: device-side event scheduling, one-command playback
- **PR #201**: Qualify external I/O timing: I2S forwarded clock, SPI MISO return, documented LED exceptions
- **PR #200**: Dismiss the 13 DPREG-4 DSP feedback warnings with mapped-simulation evidence
- **PR #204**: Integrated qualified Arty baseline: DSP disposition + external I/O timing + UART bridge in one image

### 2026-09-23

- **PR #191**: Add named selected Mono presets and checked FPGA build

### 2026-09-22

- **PR #194**: Measure first M1A bass comparison with qualified release and explicit missing evidence
- **PR #192**: Advance pulse 2x through the production RTL and Mono I2S scorer

### 2026-09-21

- **PR #193**: Measure the prior-note effect behind Mono attack discrepancies
- **PR #180**: Land measured Mono baseline, shared engine and portable references
- **PR #182**: Measure reconstructed 2x versus 4x ladder paths
- **PR #183**: Combine verified M5A candidate and release fix
- **PR #187**: Add first complete Mini V3 M5B scorecard case
- **PR #188**: Score M5B with selected M5A engine
- **PR #190**: Restore exact frozen Surge cache and run reference controls in CI
- **PR #185**: Preserve voice envelope release precision
- **PR #184**: Skip redundant render when scoring supplied I2S audio

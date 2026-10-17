# Work log

Recorded forge events; merge or closure alone does not establish sound qualification or hardware delivery. Initial coverage: the twenty most recently merged PRs, excluding Guide maintenance, and recently closed issues since 2026-10-01. Earlier events are outside this initial log.

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

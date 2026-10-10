# Work log

Recorded forge events; merge or closure alone does not establish sound qualification or hardware delivery. Initial coverage: the twenty most recently merged PRs, excluding Guide maintenance, and recently closed issues since 2026-10-01. Earlier events are outside this initial log.

### 2026-10-10

Closure events below record forge state, not a verified fix or a new sound result.

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

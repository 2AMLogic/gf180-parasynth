# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery.

<!-- guide:plan-body:start -->
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs under a hold (`loom:blocked` or `loom:operator`) — only a human merge decision is missing.

_None._

## Very Important

`loom:very-important` (one counts per workspace); every stage takes these first.

- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)

## Important

`loom:important`; taken before unlabelled work.

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin

## Ready

Human-approved issues ready for implementation (`loom:issue`).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#325**: Consolidated physical setup session sheet (R0 first, then R1)
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override)
- **#778**: measure: re-derive the jit.stability +0.008 and mpd.* attributions with the exact-period fold (REFUSED in #767 -- no corpus on that host)
- **#782**: bug: the relative rate tolerance is dimensionally meaningless on the 147 dB columns
- **#787**: Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run"
- **#811**: Wire or document tools/probes/check_doc_claims_789_marker_controls.py: a control no target runs (the #756 shape, again)
- **#845**: estimator_defects.py section 6: nine unguarded `.value` arithmetic sites, including two that reach a check() verdict, owned by neither #792 nor #843
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates
- **#852**: test_probe_labels gate (c) polices filter_corner_recalibration's module docstring, and goes red if it says "pre-#150"
- **#858**: compile_dag classify(): the empty-covers REFUSED guard masks a FAILING record as REFUSED when the node also carries an in-history tag
- **#863**: compile_dag: stale_against() answers a content question with a commit-range walk, so a merge-shaped conflict round renders fast nodes STALE (false positive; --full-history is NOT the fix)
- **#864**: classify_route_drc.py crashes where it documents exit 3 REFUSED: 3 measured inputs return rc=1 with a traceback
- **#867**: #781 residue: fold-interpolator verdict summary says 'Five' where its own runs field has four singleton groups, plus two merge-jammed lines
- **#870**: probe_orphan_reap argv test drops true matches where a worktree path ends a longer token (--opt=<path>, <path>:<mount>)
- **#873**: spec/NUMERIC-CONTRACT.md: PR #846's repaired sites cite figures and pointers that no marker guards
- **#877**: File upstream (rjwalters/loom): loom-daemon's write guard gates issue creation on push/WRITE, refusing every public upstream where issues:write suffices
- **#879**: docs: makefile_recipe comment-filter docstrings claim a silent exit 0 for a mutation that is loud mid-recipe
- **#882**: Tracked-JSON experiment-record writers still truncate-then-write (class sweep remainder from #881)
- **#884**: regrade: an attested-file edit fails as a bare 'exit 3' with the drift report swallowed, and nothing couples the edit to regenerating the evidence
- **#885**: pytest_collection_inventory.py:78 comment says pnr/ is 'make verify only' — false once the pnr CI job lands (#851/#865)
- **#888**: Post the #12307 correction of record as a comment on OpenROAD#12303

## In Progress

Issues currently being built (`loom:building`).

- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)
- **#784**: Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error
- **#791**: compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone
- **#835**: File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings
- **#853**: 16 of 40 fpga/ test files are run by no CI job (23 more only via make verify-fast) -- the fpga half of the #404/#851 shape
- **#881**: Class sweep for #842: tracked JSON still written by truncate-then-write outside compile_dag

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

- **#849**: summarize.py refuses duplicated required metrics (OpenROAD#12303)

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

_None._

## Proposed

Issues carrying `loom:curated`.

- **#33**: The joined chip does not fit in one quarter slot — two slots, or cut something *(curated)*
- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay *(curated)*
- **#138**: We already have a discriminator at balanced accuracy 1.000 — the open question is what it is NOT measuring *(curated)*
- **#152**: Residual drum DC coupling: qualify non-cymbal defects before proposing a repair *(curated)*
- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy *(curated)*
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(curated)*
- **#205**: Lane E: PULSE2X=1 Arty build with its own fit/timing/warning qualification *(curated)*
- **#208**: Record the first qualified R0 physical I2S capture on the Arty *(curated)*
- **#220**: modal_fixed's floored biquad settles into a DC pedestal that grows as resonator state shrinks — blocks #21's BD excitation-shape fix *(curated)*
- **#247**: Model/RTL mismatch: glide between increments >= 2^23 (register-legal, above Nyquist) diverges after ~7 frames *(curated)*
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098) *(curated)*
- **#285**: CLAUDE.md headless rule: say how to wait on jobs longer than one foreground call (10 min cap) *(curated)*
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms) *(curated)*
- **#310**: Sweep hygiene: a killed sweep leaves its nohup'd heavy job running, and nothing reaps or surfaces it *(curated)*
- **#324**: R1 capture inputs: R1-bound references and commands for the physical capture procedure *(curated)*
- **#325**: Consolidated physical setup session sheet (R0 first, then R1) *(curated)*
- **#334**: Sound: shared tom/conga body-spectrum failures — test a common cause before tuning six presets *(curated)*
- **#335**: Sound: cowbell partial balance (2.82x tolerance) — local voicing repair *(curated)*
- **#336**: Sound: rimshot spectrum, maracas envelope/band balance, and hat/cymbal qualification — cover every advertised drum *(curated)*
- **#337**: Sound: mono character — oscillator mixtures, drift, drive/resonance, envelope response; M1A as a phase-aware question *(curated)*
- **#338**: Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement *(curated)*
- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails *(curated)*
- **#353**: Harvest the finished half-slot route: one command, four files, one DAG node *(curated)*
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override) *(curated)*
- **#413**: Cymbal knob-law render: kit_at is not the instrument, and its blocker was tracked by a merged PR *(curated)*
- **#453**: Run tools/deadline_binding_probe.py on the build box to ground-truth the #443 drum-latency argument *(curated)*
- **#498**: np.trapezoid requires numpy>=2.0 and nothing declares that floor: 5 tests fail on a 1.26 host *(curated)*
- **#510**: Ship the cymbal DC coupling: register contract, RTL, and the I2S carry-through #165 did not trigger *(curated)*
- **#512**: Turn the edge-straddle guard on for lowband_level_db, and re-read HT/LC 7.5 with it *(curated)*
- **#521**: Detector coverage matrix + bounded first validation experiment (#158) *(curated)*
- **#530**: Permitted-differences suite: add the polarity x estimated-frequency rows #158 names *(curated)*
- **#533**: audio_distance_floor.distances() computes mel_dac at 48 kHz on 44.1 kHz Fischer files *(curated)*
- **#552**: Cymbal DC coupling 2/3: RTL shared-bus stage, drum_regs decoder, host init, bit-exact bench with mutants (#510) *(curated)*
- **#556**: [sound] MA and RS: noise-voice centroid and flatness trajectories fail the gate (35.5x, 20.6x; 9.0x and 7.9x farther than real MARS takes) *(curated)*
- **#557**: [sound] BD: missing pitch drop (pitch_shape 22.2x; about 230 cents) *(curated)*
- **#558**: [sound] toms and congas: pitch trajectory and strike impulse fail the gate (MC 19.4x, LC 16.4x, LT 14.8x, MT 10.5x, HC 8.7x, HT 8.0x) *(curated)*
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate *(curated)*
- **#562**: gate-379 follow-up: regenerate between-recording diagnostics with Q6; register the p dial *(curated)*
- **#588**: [gate] perceptual_gate impulse reads the 44.1->48 kHz rate path: BD and the six toms/congas fail it 3.8-5.0x with no sound defect *(curated)*
- **#593**: [sound] toms: H2..H4 output distortion (LT modulation 14.8x), lead from #558 *(curated)*
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning *(curated)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(curated)*
- **#637**: prepare() takes a DC estimate on our side and never on the reference side (5 ms pre-onset rule) *(curated)*
- **#648**: M5 manifest readers truncate offset_samples and bare-subscript event fields (same class as PR #647 finding) *(curated)*
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL) *(curated)*
- **#676**: Re-run audio_distance_floor probe on build box and regenerate audio-distance-metrics results after sr fix (#533) *(curated)*
- **#678**: Release gates: give the post-R2 tree its own image key and verification directory (blocks #657, split from #658) *(curated)*
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin *(curated)*
- **#700**: Run before/after corpus measurement for partial-balance and conga-spread probes after #690 *(curated)*
- **#705**: Run bd_pitch_baseline on the Fischer corpus after #697 and read f0_line_hz against the new glide f0 range *(curated)*
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig) *(curated)*
- **#778**: measure: re-derive the jit.stability +0.008 and mpd.* attributions with the exact-period fold (REFUSED in #767 -- no corpus on that host) *(curated)*
- **#782**: bug: the relative rate tolerance is dimensionally meaningless on the 147 dB columns *(curated)*
- **#784**: Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error *(curated)*
- **#787**: Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run" *(curated)*
- **#791**: compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone *(curated)*
- **#808**: Route ladder_dp to completion with the unpatched netlist on the build box: DRT-0305's cause is measured gone, the error is not re-observed *(curated)*
- **#811**: Wire or document tools/probes/check_doc_claims_789_marker_controls.py: a control no target runs (the #756 shape, again) *(curated)*
- **#829**: Run `make reference-integration` on the box: verify_109_claims' real-reference half has never been exercised against the real Fischer corpus *(curated)*
- **#835**: File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings *(curated)*
- **#845**: estimator_defects.py section 6: nine unguarded `.value` arithmetic sites, including two that reach a check() verdict, owned by neither #792 nor #843 *(curated)*
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates *(curated)*
- **#852**: test_probe_labels gate (c) polices filter_corner_recalibration's module docstring, and goes red if it says "pre-#150" *(curated)*
- **#853**: 16 of 40 fpga/ test files are run by no CI job (23 more only via make verify-fast) -- the fpga half of the #404/#851 shape *(curated)*
- **#856**: Provision the pinned build box for this checkout: no .env, no remote.env, and the dispatch worker's AWS identity has no EC2 access *(curated)*
- **#858**: compile_dag classify(): the empty-covers REFUSED guard masks a FAILING record as REFUSED when the node also carries an in-history tag *(curated)*
- **#859**: repo-remote.sh status reports instances:[] when DescribeInstances is unauthorized — file upstream (rjwalters/repo) and record the finding *(curated)*
- **#863**: compile_dag: stale_against() answers a content question with a commit-range walk, so a merge-shaped conflict round renders fast nodes STALE (false positive; --full-history is NOT the fix) *(curated)*
- **#864**: classify_route_drc.py crashes where it documents exit 3 REFUSED: 3 measured inputs return rc=1 with a traceback *(curated)*
- **#867**: #781 residue: fold-interpolator verdict summary says 'Five' where its own runs field has four singleton groups, plus two merge-jammed lines *(curated)*
- **#870**: probe_orphan_reap argv test drops true matches where a worktree path ends a longer token (--opt=<path>, <path>:<mount>) *(curated)*
- **#873**: spec/NUMERIC-CONTRACT.md: PR #846's repaired sites cite figures and pointers that no marker guards *(curated)*
- **#877**: File upstream (rjwalters/loom): loom-daemon's write guard gates issue creation on push/WRITE, refusing every public upstream where issues:write suffices *(curated)*
- **#879**: docs: makefile_recipe comment-filter docstrings claim a silent exit 0 for a mutation that is loud mid-recipe *(curated)*
- **#881**: Class sweep for #842: tracked JSON still written by truncate-then-write outside compile_dag *(curated)*
- **#882**: Tracked-JSON experiment-record writers still truncate-then-write (class sweep remainder from #881) *(curated)*
- **#884**: regrade: an attested-file edit fails as a bare 'exit 3' with the drift report swallowed, and nothing couples the edit to regenerating the evidence *(curated)*
- **#885**: pytest_collection_inventory.py:78 comment says pnr/ is 'make verify only' — false once the pnr CI job lands (#851/#865) *(curated)*

## Proposed (Architect / Hermit)

- **#599**: Single git-state reader: collapse ~20 private git() provenance helpers onto tools/provenance.py with one failure encoding *(architect)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(architect)*
- **#569**: Consolidate 12+ duplicated file-SHA-256 helpers into tools/provenance.py *(hermit)*
- **#621**: Remove tools/regen_m1a_units.py: completed one-shot migration and its sole-caller cache path *(hermit)*
- **#753**: Consolidate 16 duplicate file-sha256 helpers in tools/ into provenance.py *(hermit)*
- **#760**: Retire or re-home pulse_audio.yml: its only push trigger is a branch that merged in September *(hermit)*
- **#762**: Remove fpga/probe_uart_findings.py: it reports 2 false FAILs on the repaired host and nothing runs it *(hermit)*
- **#800**: Remove 6 unreferenced top-level defs in model/ and tools/: each is a dead duplicate of logic inlined at its call site *(hermit)*
- **#812**: verify-fast re-runs 40 test files that sibling jobs in the same workflow already collect wholesale, on the stale premise that no CI job does *(hermit)*
- **#817**: Consolidate the seven WAV readers: four conventions, and two answer 96 dB wrong on a 24-bit file instead of refusing *(hermit)*

## Epics

- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098)

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 0 |
| Very important | 1 |
| Important | 6 |
| Ready (`loom:issue`) | 23 |
| In Progress (`loom:building`) | 11 |
| PRs awaiting review | 1 |
| Approved PRs awaiting merge | 0 |
| Curated | 78 |
| Architect / Hermit proposals | 10 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

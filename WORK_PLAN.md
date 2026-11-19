# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery. As-of date: the generated region below is as current as its own last change — `git log --no-patch -1 --format='%h %cs' -L '/^<!-- guide:plan-body:start -->$/,/^<!-- guide:plan-body:end -->$/:WORK_PLAN.md' origin/main` — which neither a declined Guide regeneration nor a docs-only commit elsewhere in this file advances.

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
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(held)*
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin *(held)*

## Ready

Human-approved issues (`loom:issue`) ready for implementation — excluding held (`loom:blocked`/`loom:operator`/`loom:frozen`), `loom:building`, and issues closed by an open `loom:pr` PR (listed under Approved).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#325**: Consolidated physical setup session sheet (R0 first, then R1)
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override)

## In Progress

Issues currently being built (`loom:building`).

- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates
- **#922**: Tracked-JSON truncate-then-write remains in holdout.py (ledger + seals) and measure_m1a_volume_mapping.py
- **#947**: Atomic-write remainder: 23 tracked JSONs still written truncate-then-write (audit of #922)
- **#949**: check_doc_claims: a grep= marker whose only match is its own marker line grades OK, guarding nothing
- **#1022**: analyze_def_sites: the DEF path-point extension is recorded but never applied to the emitted rect, nor surfaced in the record
- **#1023**: docs/tool-findings.md carries zero claim markers, so its route-record figures have nothing re-deriving them
- **#1030**: analyze_def_sites: a skip-keyword's NAME argument after a path's points becomes a phantom via (same class as #1013/#1021, via branch not width branch)
- **#1034**: analyze_def_sites: '+ NONDEFAULTRULE <name>' after a path's points makes the keyword a phantom via (the spelling #1030 left out of scope)

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

- **#1025**: fix: apply the DEF path-point extension to the wire rect and count it in the record
- **#1029**: docs: bind #1006 route-record figures to claim markers
- **#1031**: fix: consume skip-keyword name arguments so they never become phantom vias
- **#1035**: fix: end the via-name position at a skipped '+' in DEF path walk

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

_None._

## Proposed

Issues carrying `loom:curated`.

- **#33**: The joined chip does not fit in one quarter slot — two slots, or cut something *(curated)* *(held)*
- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay *(curated)*
- **#138**: We already have a discriminator at balanced accuracy 1.000 — the open question is what it is NOT measuring *(curated)* *(held)*
- **#152**: Residual drum DC coupling: qualify non-cymbal defects before proposing a repair *(curated)* *(held)*
- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy *(curated)*
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(curated)* *(held)*
- **#205**: Lane E: PULSE2X=1 Arty build with its own fit/timing/warning qualification *(curated)* *(held)*
- **#208**: Record the first qualified R0 physical I2S capture on the Arty *(curated)*
- **#220**: modal_fixed's floored biquad settles into a DC pedestal that grows as resonator state shrinks — blocks #21's BD excitation-shape fix *(curated)* *(held)*
- **#247**: Model/RTL mismatch: glide between increments >= 2^23 (register-legal, above Nyquist) diverges after ~7 frames *(curated)* *(held)*
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098) *(curated)*
- **#285**: CLAUDE.md headless rule: say how to wait on jobs longer than one foreground call (10 min cap) *(curated)* *(held)*
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms) *(curated)*
- **#310**: Sweep hygiene: a killed sweep leaves its nohup'd heavy job running, and nothing reaps or surfaces it *(curated)* *(held)*
- **#324**: R1 capture inputs: R1-bound references and commands for the physical capture procedure *(curated)* *(held)*
- **#325**: Consolidated physical setup session sheet (R0 first, then R1) *(curated)*
- **#334**: Sound: shared tom/conga body-spectrum failures — test a common cause before tuning six presets *(curated)* *(held)*
- **#335**: Sound: cowbell partial balance (2.82x tolerance) — local voicing repair *(curated)* *(held)*
- **#336**: Sound: rimshot spectrum, maracas envelope/band balance, and hat/cymbal qualification — cover every advertised drum *(curated)* *(held)*
- **#337**: Sound: mono character — oscillator mixtures, drift, drive/resonance, envelope response; M1A as a phase-aware question *(curated)* *(held)*
- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails *(curated)*
- **#353**: Harvest the finished half-slot route: one command, four files, one DAG node *(curated)* *(held)*
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override) *(curated)*
- **#413**: Cymbal knob-law render: kit_at is not the instrument, and its blocker was tracked by a merged PR *(curated)* *(held)*
- **#453**: Run tools/deadline_binding_probe.py on the build box to ground-truth the #443 drum-latency argument *(curated)* *(held)*
- **#498**: np.trapezoid requires numpy>=2.0 and nothing declares that floor: 5 tests fail on a 1.26 host *(curated)* *(held)*
- **#510**: Ship the cymbal DC coupling: register contract, RTL, and the I2S carry-through #165 did not trigger *(curated)* *(held)*
- **#512**: Turn the edge-straddle guard on for lowband_level_db, and re-read HT/LC 7.5 with it *(curated)* *(held)*
- **#521**: Detector coverage matrix + bounded first validation experiment (#158) *(curated)*
- **#530**: Permitted-differences suite: add the polarity x estimated-frequency rows #158 names *(curated)* *(held)*
- **#533**: audio_distance_floor.distances() computes mel_dac at 48 kHz on 44.1 kHz Fischer files *(curated)* *(held)*
- **#552**: Cymbal DC coupling 2/3: RTL shared-bus stage, drum_regs decoder, host init, bit-exact bench with mutants (#510) *(curated)* *(held)*
- **#556**: [sound] MA and RS: noise-voice centroid and flatness trajectories fail the gate (35.5x, 20.6x; 9.0x and 7.9x farther than real MARS takes) *(curated)* *(held)*
- **#557**: [sound] BD: missing pitch drop (pitch_shape 22.2x; about 230 cents) *(curated)* *(held)*
- **#558**: [sound] toms and congas: pitch trajectory and strike impulse fail the gate (MC 19.4x, LC 16.4x, LT 14.8x, MT 10.5x, HC 8.7x, HT 8.0x) *(curated)* *(held)*
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate *(curated)*
- **#562**: gate-379 follow-up: regenerate between-recording diagnostics with Q6; register the p dial *(curated)* *(held)*
- **#588**: [gate] perceptual_gate impulse reads the 44.1->48 kHz rate path: BD and the six toms/congas fail it 3.8-5.0x with no sound defect *(curated)* *(held)*
- **#593**: [sound] toms: H2..H4 output distortion (LT modulation 14.8x), lead from #558 *(curated)* *(held)*
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning *(curated)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(curated)* *(held)*
- **#637**: prepare() takes a DC estimate on our side and never on the reference side (5 ms pre-onset rule) *(curated)* *(held)*
- **#648**: M5 manifest readers truncate offset_samples and bare-subscript event fields (same class as PR #647 finding) *(curated)* *(held)*
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL) *(curated)*
- **#676**: Re-run audio_distance_floor probe on build box and regenerate audio-distance-metrics results after sr fix (#533) *(curated)* *(held)*
- **#678**: Release gates: give the post-R2 tree its own image key and verification directory (blocks #657, split from #658) *(curated)* *(held)*
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin *(curated)* *(held)*
- **#700**: Run before/after corpus measurement for partial-balance and conga-spread probes after #690 *(curated)* *(held)*
- **#705**: Run bd_pitch_baseline on the Fischer corpus after #697 and read f0_line_hz against the new glide f0 range *(curated)* *(held)*
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig) *(curated)*
- **#820**: Post-merge live verification of #818: the nightly's records land on main, and the [dag] loop guard holds *(curated)* *(held)*
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates *(curated)*
- **#859**: repo-remote.sh status reports instances:[] when DescribeInstances is unauthorized — file upstream (rjwalters/repo) and record the finding *(curated)* *(held)*
- **#899**: probe_orphan_reap: _argv_names bounds the right side of the path match but not the left *(curated)* *(held)*
- **#922**: Tracked-JSON truncate-then-write remains in holdout.py (ledger + seals) and measure_m1a_volume_mapping.py *(curated)*
- **#933**: regenerate discrimination artefacts under the six-bucket MPD grouping (follow-up to #929) *(curated)* *(held)*
- **#947**: Atomic-write remainder: 23 tracked JSONs still written truncate-then-write (audit of #922) *(curated)*
- **#949**: check_doc_claims: a grep= marker whose only match is its own marker line grades OK, guarding nothing *(curated)*
- **#1018**: Confirm WORK_PLAN.md's as-of sentence survives Guide regeneration, and reconcile it with the inbound upstream in-region stamp *(curated)* *(held)*
- **#1022**: analyze_def_sites: the DEF path-point extension is recorded but never applied to the emitted rect, nor surfaced in the record *(curated)*
- **#1023**: docs/tool-findings.md carries zero claim markers, so its route-record figures have nothing re-deriving them *(curated)*
- **#1030**: analyze_def_sites: a skip-keyword's NAME argument after a path's points becomes a phantom via (same class as #1013/#1021, via branch not width branch) *(curated)*
- **#1033**: Two pre-existing claim markers split their paragraph in rendered Markdown (docs/tr808-reference.md) *(curated)* *(held)*
- **#1034**: analyze_def_sites: '+ NONDEFAULTRULE <name>' after a path's points makes the keyword a phantom via (the spelling #1030 left out of scope) *(curated)*

## Proposed (Architect / Hermit)

- **#599**: Single git-state reader: collapse ~20 private git() provenance helpers onto tools/provenance.py with one failure encoding *(architect)* *(held)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(architect)* *(held)*
- **#569**: Consolidate 12+ duplicated file-SHA-256 helpers into tools/provenance.py *(hermit)* *(held)*
- **#621**: Remove tools/regen_m1a_units.py: completed one-shot migration and its sole-caller cache path *(hermit)* *(held)*
- **#753**: Consolidate 16 duplicate file-sha256 helpers in tools/ into provenance.py *(hermit)* *(held)*
- **#760**: Retire or re-home pulse_audio.yml: its only push trigger is a branch that merged in September *(hermit)* *(held)*
- **#762**: Remove fpga/probe_uart_findings.py: it reports 2 false FAILs on the repaired host and nothing runs it *(hermit)* *(held)*
- **#800**: Remove 6 unreferenced top-level defs in model/ and tools/: each is a dead duplicate of logic inlined at its call site *(hermit)* *(held)*
- **#812**: verify-fast re-runs 40 test files that sibling jobs in the same workflow already collect wholesale, on the stale premise that no CI job does *(hermit)* *(held)*
- **#817**: Consolidate the seven WAV readers: four conventions, and two answer 96 dB wrong on a 24-bit file instead of refusing *(hermit)* *(held)*

## Epics

- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098)

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 0 |
| Very important | 1 |
| Important | 6 |
| Ready (`loom:issue`, net of holds/building/merge-pending) | 4 |
| In Progress (`loom:building`) | 14 |
| PRs awaiting review | 4 |
| Approved PRs awaiting merge | 0 |
| Curated | 64 |
| Architect / Hermit proposals | 10 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery. As-of date: the generated region below is as current as its own last change — `git log --no-patch -1 --format='%h %cs' -L '/^<!-- guide:plan-body:start -->$/,/^<!-- guide:plan-body:end -->$/:WORK_PLAN.md' origin/main` — which neither a declined Guide regeneration nor a docs-only commit elsewhere in this file advances.

<!-- guide:plan-body:start -->
_Generated: 2026-11-24T18:46:31Z (snapshot; compare against live labels)_
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs under a hold (`loom:blocked` or `loom:operator`).

- **#1064**: ci: route the nine apt dependency steps through a retry-wrapped helper

## Very Important

`loom:very-important` (one counts per workspace); every stage takes these first.

- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)

## Important

`loom:important`; taken before unlabelled work.

- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(held)*
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin *(held)*
- **#1049**: [voice-only] Measure synthesized+placed area of a voice-only top against one quarter slot *(held)*

## Ready

Human-approved issues (`loom:issue`) ready for implementation — excluding held (`loom:blocked`/`loom:operator`/`loom:frozen`), `loom:building`, and issues closed by an open `loom:pr` PR (listed under Approved).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#325**: Consolidated physical setup session sheet (R0 first, then R1)
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override)
- **#1207**: Four log10-over-ratio sites in tools/cymbal_*.py were not individually named in #1197's class sweep

## In Progress

Issues currently being built (`loom:building`).

- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates
- **#947**: Atomic-write remainder: 16 confirmed tracked JSON records across 14 modules
- **#949**: check_doc_claims: a grep= marker whose only match is its own marker line grades OK, guarding nothing
- **#1126**: Require a matching clean run before make controls credits an RTL mismatch
- **#1183**: Triage the remaining cross-module refusal escape candidates from the refusal_sibling_scan census
- **#1192**: check_workflows roadmap order gates key on step name, so the SET of compile_dag.py --run invocations is unasserted
- **#1199**: Pair clean baselines with the bare injected controls outside make controls (nightly.yml job, verify-full row) and normalise relative --outdir in verify_ladder/verify_modal
- **#1206**: check_workflows: the #906 pair order gate lets a duplicate guarded step name through (dict-overwrite, no ambiguity refusal)
- **#1208**: Decide whether tools/delta_report.py belongs in each comparator's SOURCES (provenance now computed by unhashed code)
- **#1211**: Use the ensemble scorer’s existing file hash checks for cached negative controls
- **#1212**: rungs.yml rtl-full: the Verilator VOICE_FILTER2X_OFF control has no clean --m5a-smoke twin in that job
- **#1214**: refusal_sibling_scan: ambiguous-callee reports family-less callees; --json leaks absolute paths
- **#1215**: Make MIDI and UART packages optional in the keyboard audition instructions
- **#1217**: rungs.yml pulse2x job: the VOICE_PULSE2X_OFF control's clean twin is an adjacent run_all row, which cannot gate it
- **#1219**: Consolidate duplicated UART RTL capture/replay in verify_live_midi and verify_rolling_playback (fixes --image r2 running with PULSE2X=0)
- **#1222**: nightly.yml: the verify_ctl and verify_synth_top mutant steps have no clean run anywhere in the workflow
- **#1232**: Remove conflicting key assignments from the audition player’s keyboard guide
- **#1235**: No mechanical guard that workflow injected-defect controls are gated; the run_controls guards read the Makefile recipe only

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

- **#1196**: ci: assert the compile_dag.py invocation set, not just the named order-gate steps
- **#1201**: controls: real-verifier mixed-baseline evidence, end-to-end make controls runs, drums outdir fix
- **#1209**: fix: refuse duplicate step names in the roadmap pair gate instead of last-wins
- **#1216**: Bind ensemble control-run reuse to the sidecar digests the report carries
- **#1218**: tools: hash tools/delta_report.py in each comparator's SOURCES
- **#1220**: ci: pair rtl-full's Verilator M5A mutant with a clean smoke baseline
- **#1223**: ci: gate the pulse2x VOICE_PULSE2X_OFF control on its clean twin via run_controls
- **#1226**: docs: make MIDI and UART packages optional in the keyboard audition install
- **#1227**: fix(nightly): gate the ctl and synth_top mutant steps on a clean run
- **#1228**: fix: share the UART RTL capture/replay pipeline; replay live-midi r2 bytes at PULSE2X=1
- **#1233**: fix: correct audition player help keys for glide, hats and filter envelope
- **#1240**: test: guard workflow RTL mutant gating and baseline config match

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#1064**: ci: route the nine apt dependency steps through a retry-wrapped helper *(held)* — linked issue #1062
- **#1213**: fix: gate bare injected controls on clean baselines; normalise relative --outdir in ladder/modal benches — linked issue #1199

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
- **#847**: Wire check_m1a_drive_report.py into make verify; record why check_m1a_selection.py and three others are not unwired gates *(curated)*
- **#859**: repo-remote.sh status reports instances:[] when DescribeInstances is unauthorized — file upstream (rjwalters/repo) and record the finding *(curated)* *(held)*
- **#899**: probe_orphan_reap: _argv_names bounds the right side of the path match but not the left *(curated)* *(held)*
- **#933**: regenerate discrimination artefacts under the six-bucket MPD grouping (follow-up to #929) *(curated)* *(held)*
- **#947**: Atomic-write remainder: 16 confirmed tracked JSON records across 14 modules *(curated)*
- **#949**: check_doc_claims: a grep= marker whose only match is its own marker line grades OK, guarding nothing *(curated)*
- **#1049**: [voice-only] Measure synthesized+placed area of a voice-only top against one quarter slot *(curated)* *(held)*
- **#1062**: CI flake: pulse2x-rtl reds on an apt mirror timeout in its iverilog install step (9 unprotected apt call sites) *(curated)*
- **#1085**: Probe klt drc, klt lvs (power_connectivity) and klt erc on the committed ladder_dp route, and file each klt gap upstream (tool probe; no T1 credit) *(curated)* *(held)*
- **#1110**: Judge docs-only fast path is unreachable on this host: its base-repo read uses a gh --json field this gh 2.102.0 rejects *(curated)* *(held)*
- **#1126**: Require a matching clean run before make controls credits an RTL mismatch *(curated)*
- **#1148**: rtl-full step 8 is red on main: M5A filter2x SPI-to-I2S diverges from the model in 815424 of 1305549 frames (62%) *(curated)* *(held)*
- **#1183**: Triage the remaining cross-module refusal escape candidates from the refusal_sibling_scan census *(curated)*
- **#1192**: check_workflows roadmap order gates key on step name, so the SET of compile_dag.py --run invocations is unasserted *(curated)*
- **#1199**: Pair clean baselines with the bare injected controls outside make controls (nightly.yml job, verify-full row) and normalise relative --outdir in verify_ladder/verify_modal *(curated)*
- **#1206**: check_workflows: the #906 pair order gate lets a duplicate guarded step name through (dict-overwrite, no ambiguity refusal) *(curated)*
- **#1207**: Four log10-over-ratio sites in tools/cymbal_*.py were not individually named in #1197's class sweep *(curated)*
- **#1208**: Decide whether tools/delta_report.py belongs in each comparator's SOURCES (provenance now computed by unhashed code) *(curated)*
- **#1212**: rungs.yml rtl-full: the Verilator VOICE_FILTER2X_OFF control has no clean --m5a-smoke twin in that job *(curated)*
- **#1214**: refusal_sibling_scan: ambiguous-callee reports family-less callees; --json leaks absolute paths *(curated)*
- **#1217**: rungs.yml pulse2x job: the VOICE_PULSE2X_OFF control's clean twin is an adjacent run_all row, which cannot gate it *(curated)*
- **#1221**: rungs.yml rtl job: the default-config verify_synth_top mutants (VOICE_MASTER_PRESHIFT, I2S_SHIFT) have no config-matched clean twin *(curated)*
- **#1222**: nightly.yml: the verify_ctl and verify_synth_top mutant steps have no clean run anywhere in the workflow *(curated)*
- **#1235**: No mechanical guard that workflow injected-defect controls are gated; the run_controls guards read the Makefile recipe only *(curated)*

## Proposed (Architect / Hermit)

- **#599**: Single git-state reader: collapse ~20 private git() provenance helpers onto tools/provenance.py with one failure encoding *(architect)* *(held)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(architect)* *(held)*
- **#569**: Consolidate 12+ duplicated file-SHA-256 helpers into tools/provenance.py *(hermit)* *(held)*
- **#621**: Remove tools/regen_m1a_units.py: completed one-shot migration and its sole-caller cache path *(hermit)* *(held)*
- **#753**: Consolidate 16 duplicate file-sha256 helpers in tools/ into provenance.py *(hermit)* *(held)*
- **#812**: verify-fast re-runs 40 test files that sibling jobs in the same workflow already collect wholesale, on the stale premise that no CI job does *(hermit)* *(held)*
- **#1172**: Wire in or remove check_nightly_slow_records.py: a 1,176-line gate nothing runs *(hermit)* *(held)*
- **#1173**: Remove tools/audit_tracked_json_writers.py: one-shot #922 report nothing runs *(hermit)* *(held)*
- **#1205**: Remove disk cache replay from the 808 conditioning comparison *(hermit)* *(held)*

## Epics

- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098)

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 1 |
| Very important | 1 |
| Important | 4 |
| Ready (`loom:issue`, net of holds/building/merge-pending) | 5 |
| In Progress (`loom:building`) | 23 |
| PRs awaiting review | 12 |
| Approved PRs awaiting merge | 2 |
| Curated | 73 |
| Architect / Hermit proposals | 9 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

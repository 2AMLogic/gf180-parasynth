# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery.

<!-- guide:plan-body:start -->
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs stuck under a `loom:operator` merge-risk hold — implementation work is done, only a human merge decision is missing.

- **#504**: Reachable attack-context re-bind path for Builders (#502)
- **#561**: gate-379: between-recording bar rule v2 (REFUSED, isolated), current-main ranking, controls

## Operator Priority

Issues the operator starred (`loom:operator-priority`); land these first.

_None._

## Ready

Human-approved issues ready for implementation (`loom:issue`).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#247**: Model/RTL mismatch: glide between increments >= 2^23 (register-legal, above Nyquist) diverges after ~7 frames
- **#257**: A per-frame, resonance-keyed cutoff correction: closing most of the 105 cents issue #237 measured and declined to fix in-place
- **#283**: trials: check-receipt accepts a receipt with a required child deleted (including zero required children -> PASS)
- **#288**: midi_session: refuse real sessions when an internal fault-injection set is non-empty
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#321**: NUMERIC-CONTRACT: sections 5.1 and 5.2 never recorded revision 9's registers, and still specify wave[k] as 3 bits where 6.4 defines nine codes
- **#337**: Sound: mono character — oscillator mixtures, drift, drive/resonance, envelope response; M1A as a phase-aware question
- **#338**: Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY
- **#379**: 808 kit: automated perceptual gate calibrated against the 808's own variability — prove it, rank all 16 sounds, fix worst first
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#522**: Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader
- **#556**: [sound] MA and RS: noise-voice centroid and flatness trajectories fail the gate (35.5x, 20.6x; 9.0x and 7.9x farther than real MARS takes)
- **#557**: [sound] BD: missing pitch drop (pitch_shape 22.2x; about 230 cents)
- **#558**: [sound] toms and congas: pitch trajectory and strike impulse fail the gate (MC 19.4x, LC 16.4x, LT 14.8x, MT 10.5x, HC 8.7x, HT 8.0x)
- **#564**: Collect the 57 in-file tests that pytest never runs (excitation_energy, dc_blocker, discrimination_*)
- **#565**: Refuse to report 'routed' from an incomplete nextpnr log (fpga/scripts/report.sh + fpga/Makefile)
- **#568**: synth_count.sh prints blank cell counts and exits 0 when yosys fails

## In Progress

Issues currently being built (`loom:building`).

_None._

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

_None._

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#504**: Reachable attack-context re-bind path for Builders (#502)
- **#561**: gate-379: between-recording bar rule v2 (REFUSED, isolated), current-main ranking, controls

## Proposed

Issues carrying `loom:curated`.

- **#33**: The joined chip does not fit in one quarter slot — two slots, or cut something *(curated)*
- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay *(curated)*
- **#124**: Build a pedalboard-backed reference rig, with the same qualification discipline as the dawdreamer one *(curated)*
- **#138**: We already have a discriminator at balanced accuracy 1.000 — the open question is what it is NOT measuring *(curated)*
- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy *(curated)*
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(curated)*
- **#205**: Lane E: PULSE2X=1 Arty build with its own fit/timing/warning qualification *(curated)*
- **#208**: Record the first qualified R0 physical I2S capture on the Arty *(curated)*
- **#220**: modal_fixed's floored biquad settles into a DC pedestal that grows as resonator state shrinks — blocks #21's BD excitation-shape fix *(curated)*
- **#247**: Model/RTL mismatch: glide between increments >= 2^23 (register-legal, above Nyquist) diverges after ~7 frames *(curated)*
- **#257**: A per-frame, resonance-keyed cutoff correction: closing most of the 105 cents issue #237 measured and declined to fix in-place *(curated)*
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098) *(curated)*
- **#283**: trials: check-receipt accepts a receipt with a required child deleted (including zero required children -> PASS) *(curated)*
- **#285**: CLAUDE.md headless rule: say how to wait on jobs longer than one foreground call (10 min cap) *(curated)*
- **#288**: midi_session: refuse real sessions when an internal fault-injection set is non-empty *(curated)*
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms) *(curated)*
- **#310**: Sweep hygiene: a killed sweep leaves its nohup'd heavy job running, and nothing reaps or surfaces it *(curated)*
- **#321**: NUMERIC-CONTRACT: sections 5.1 and 5.2 never recorded revision 9's registers, and still specify wave[k] as 3 bits where 6.4 defines nine codes *(curated)*
- **#334**: Sound: shared tom/conga body-spectrum failures — test a common cause before tuning six presets *(curated)*
- **#335**: Sound: cowbell partial balance (2.82x tolerance) — local voicing repair *(curated)*
- **#336**: Sound: rimshot spectrum, maracas envelope/band balance, and hat/cymbal qualification — cover every advertised drum *(curated)*
- **#337**: Sound: mono character — oscillator mixtures, drift, drive/resonance, envelope response; M1A as a phase-aware question *(curated)*
- **#338**: Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement *(curated)*
- **#353**: Harvest the finished half-slot route: one command, four files, one DAG node *(curated)*
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY *(curated)*
- **#379**: 808 kit: automated perceptual gate calibrated against the 808's own variability — prove it, rank all 16 sounds, fix worst first *(curated)*
- **#502**: A Builder cannot re-bind the attack-context evidence: workflow_dispatch is 403 for the agent token *(curated)*
- **#510**: Ship the cymbal DC coupling: register contract, RTL, and the I2S carry-through #165 did not trigger *(curated)*
- **#521**: Detector coverage matrix + bounded first validation experiment (#158) *(curated)*
- **#522**: Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader *(curated)*
- **#556**: [sound] MA and RS: noise-voice centroid and flatness trajectories fail the gate (35.5x, 20.6x; 9.0x and 7.9x farther than real MARS takes) *(curated)*
- **#557**: [sound] BD: missing pitch drop (pitch_shape 22.2x; about 230 cents) *(curated)*
- **#558**: [sound] toms and congas: pitch trajectory and strike impulse fail the gate (MC 19.4x, LC 16.4x, LT 14.8x, MT 10.5x, HC 8.7x, HT 8.0x) *(curated)*
- **#564**: Collect the 57 in-file tests that pytest never runs (excitation_energy, dc_blocker, discrimination_*) *(curated)*

## Proposed (Architect / Hermit)

- **#569**: Consolidate 12+ duplicated file-SHA-256 helpers into tools/provenance.py *(hermit)*

## Epics

- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098)

## Backlog Balance

| Tier | Count |
|------|-------|
| Operator merge-risk holds | 2 |
| Operator priority | 0 |
| Ready (`loom:issue`) | 19 |
| In Progress (`loom:building`) | 0 |
| PRs awaiting review | 0 |
| Approved PRs awaiting merge | 2 |
| Curated | 34 |
| Architect / Hermit proposals | 1 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

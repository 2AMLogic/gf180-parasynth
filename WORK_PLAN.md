# Work plan

Forge lifecycle snapshot maintained by Guide. The milestone is #282: a great-sounding mono Moog-like synth and complete 808 kit. Lifecycle labels describe workflow, not acoustic acceptance. Preserve the recorded pulse2x operator override and qualify improvements on untouched conditions before claiming delivery.

<!-- guide:plan-body:start -->
## Operator Attention: Merge-Risk-Hold Pileup

Judge-approved PRs under a hold (`loom:blocked` or `loom:operator`) — only a human merge decision is missing.

- **#652**: [sound] #592 phase-aware tom pitch-drop measurement vs Fischer: REFUSED (not identifiable), no repair
- **#709**: ci: stop cancelling started main runs and double-running PR commits

## Very Important

`loom:very-important` (one counts per workspace); every stage takes these first.

- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)

## Important

`loom:important`; taken before unlabelled work.

- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#592**: [sound] toms: diode-drop magnitude and time constant against the Fischer takes (LT onset 74 vs 35 cents)

## Ready

Human-approved issues ready for implementation (`loom:issue`).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#257**: A per-frame, resonance-keyed cutoff correction: closing most of the 105 cents issue #237 measured and declined to fix in-place
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#325**: Consolidated physical setup session sheet (R0 first, then R1)
- **#338**: Sound: full-performance suite — bass, lead, drum-only and mixed phrases with control movement
- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override)
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#522**: Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)

## In Progress

Issues currently being built (`loom:building`).

- **#540**: CI: stop cancelling started main runs, drop duplicate push+PR runs, and batch nodes on the newest main (~1,450 job-min/day on main)

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

_None._

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#652**: [sound] #592 phase-aware tom pitch-drop measurement vs Fischer: REFUSED (not identifiable), no repair
- **#709**: ci: stop cancelling started main runs and double-running PR commits

## Proposed

Issues carrying `loom:curated`.

- **#33**: The joined chip does not fit in one quarter slot — two slots, or cut something *(curated)*
- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay *(curated)*
- **#124**: Build a pedalboard-backed reference rig, with the same qualification discipline as the dawdreamer one *(curated)*
- **#138**: We already have a discriminator at balanced accuracy 1.000 — the open question is what it is NOT measuring *(curated)*
- **#152**: Residual drum DC coupling: qualify non-cymbal defects before proposing a repair *(curated)*
- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy *(curated)*
- **#162**: The bass drum's entire attack is 18-23 dB short across 170-678 Hz *(curated)*
- **#205**: Lane E: PULSE2X=1 Arty build with its own fit/timing/warning qualification *(curated)*
- **#208**: Record the first qualified R0 physical I2S capture on the Arty *(curated)*
- **#220**: modal_fixed's floored biquad settles into a DC pedestal that grows as resonator state shrinks — blocks #21's BD excitation-shape fix *(curated)*
- **#247**: Model/RTL mismatch: glide between increments >= 2^23 (register-legal, above Nyquist) diverges after ~7 frames *(curated)*
- **#257**: A per-frame, resonance-keyed cutoff correction: closing most of the 105 cents issue #237 measured and declined to fix in-place *(curated)*
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
- **#369**: Cymbal: a very accurate TR-808 cymbal — three-band structure, Hh3 third-order, qualified band decay, confirmed across TONE/DECAY *(curated)*
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override) *(curated)*
- **#413**: Cymbal knob-law render: kit_at is not the instrument, and its blocker was tracked by a merged PR *(curated)*
- **#453**: Run tools/deadline_binding_probe.py on the build box to ground-truth the #443 drum-latency argument *(curated)*
- **#498**: np.trapezoid requires numpy>=2.0 and nothing declares that floor: 5 tests fail on a 1.26 host *(curated)*
- **#510**: Ship the cymbal DC coupling: register contract, RTL, and the I2S carry-through #165 did not trigger *(curated)*
- **#512**: Turn the edge-straddle guard on for lowband_level_db, and re-read HT/LC 7.5 with it *(curated)*
- **#521**: Detector coverage matrix + bounded first validation experiment (#158) *(curated)*
- **#522**: Four corpus-path resolvers disagree, so $GF180_TR808_REFS does not reach every reader *(curated)*
- **#530**: Permitted-differences suite: add the polarity x estimated-frequency rows #158 names *(curated)*
- **#533**: audio_distance_floor.distances() computes mel_dac at 48 kHz on 44.1 kHz Fischer files *(curated)*
- **#540**: CI: stop cancelling started main runs, drop duplicate push+PR runs, and batch nodes on the newest main (~1,450 job-min/day on main) *(curated)*
- **#552**: Cymbal DC coupling 2/3: RTL shared-bus stage, drum_regs decoder, host init, bit-exact bench with mutants (#510) *(curated)*
- **#556**: [sound] MA and RS: noise-voice centroid and flatness trajectories fail the gate (35.5x, 20.6x; 9.0x and 7.9x farther than real MARS takes) *(curated)*
- **#557**: [sound] BD: missing pitch drop (pitch_shape 22.2x; about 230 cents) *(curated)*
- **#558**: [sound] toms and congas: pitch trajectory and strike impulse fail the gate (MC 19.4x, LC 16.4x, LT 14.8x, MT 10.5x, HC 8.7x, HT 8.0x) *(curated)*
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate *(curated)*
- **#562**: gate-379 follow-up: regenerate between-recording diagnostics with Q6; register the p dial *(curated)*
- **#588**: [gate] perceptual_gate impulse reads the 44.1->48 kHz rate path: BD and the six toms/congas fail it 3.8-5.0x with no sound defect *(curated)*
- **#592**: [sound] toms: diode-drop magnitude and time constant against the Fischer takes (LT onset 74 vs 35 cents) *(curated)*
- **#593**: [sound] toms: H2..H4 output distortion (LT modulation 14.8x), lead from #558 *(curated)*
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning *(curated)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(curated)*
- **#637**: prepare() takes a DC estimate on our side and never on the reference side (5 ms pre-onset rule) *(curated)*
- **#648**: M5 manifest readers truncate offset_samples and bare-subscript event fields (same class as PR #647 finding) *(curated)*
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL) *(curated)*
- **#673**: zero_crossing_frequency moves 0.78% under polarity reversal on an asymmetric waveform (upward crossings only) *(curated)*
- **#676**: Re-run audio_distance_floor probe on build box and regenerate audio-distance-metrics results after sr fix (#533) *(curated)*
- **#678**: Release gates: give the post-R2 tree its own image key and verification directory (blocks #657, split from #658) *(curated)*
- **#693**: Build-box re-capture for PR #662 (drum rev 18): deadline monitor, UART-bridge record, latency-argument pin *(curated)*
- **#700**: Run before/after corpus measurement for partial-balance and conga-spread probes after #690 *(curated)*
- **#705**: Run bd_pitch_baseline on the Fischer corpus after #697 and read f0_line_hz against the new glide f0 range *(curated)*

## Proposed (Architect / Hermit)

- **#599**: Single git-state reader: collapse ~20 private git() provenance helpers onto tools/provenance.py with one failure encoding *(architect)*
- **#626**: Area sweep reports drop missing rows and can print stale results (mode_report.py, synth_area.py, run_all.sh) *(architect)*
- **#569**: Consolidate 12+ duplicated file-SHA-256 helpers into tools/provenance.py *(hermit)*
- **#621**: Remove tools/regen_m1a_units.py: completed one-shot migration and its sole-caller cache path *(hermit)*

## Epics

- **#158**: Validate the judge: perturbation ladders on real recordings, synthetic fixtures with exact answers, and unit tests for the acceptance policy
- **#282**: Epic: great-sounding mono Moog-like synth and complete 808 kit (plan098)

## Backlog Balance

| Bucket | Count |
|------|-------|
| Operator merge-risk holds | 2 |
| Very important | 1 |
| Important | 3 |
| Ready (`loom:issue`) | 14 |
| In Progress (`loom:building`) | 1 |
| PRs awaiting review | 0 |
| Approved PRs awaiting merge | 2 |
| Curated | 57 |
| Architect / Hermit proposals | 4 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

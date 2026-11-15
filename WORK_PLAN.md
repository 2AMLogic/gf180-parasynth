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
- **#804**: Collapse the three exit statuses that mean REFUSED: run_all.py reports the cymbal low-tail refusal as FAIL(1), a RESULT

## Ready

Human-approved issues ready for implementation (`loom:issue`).

- **#107**: One envelope drives both partials, so no voice can reproduce differential partial decay
- **#306**: uart_host: live held-note hold overshoots the requested 1920 frames by ~1235 frames (~26 ms)
- **#325**: Consolidated physical setup session sheet (R0 first, then R1)
- **#372**: M5 phrase stimulus lands later notes up to 0.56 ms early (with or without override)
- **#768**: measure: how many discrimination rows fall under the new jit.dphase_ar1 floor (REFUSED in #763 -- no corpus on that host)
- **#792**: Corner probes: date the pre-#150 columns, fix `corner_shipped`'s stale docstring, guard the refusal print
- **#797**: spec/NUMERIC-CONTRACT.md item 29 says the chip still carries drum_section_placeholder and that nothing verifies the joined chip
- **#805**: The BASELINE of a perturbation comparison is read unguarded while the comparison itself is guarded (2 probe sites)
- **#824**: stale_against() reads an unresolvable sha as 'nothing changed', so a record whose commit left the history renders GREEN instead of STALE
- **#835**: File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings

## In Progress

Issues currently being built (`loom:building`).

- **#350**: Qualify the drum modal-bank floor-arithmetic deadband across production tails
- **#521**: Detector coverage matrix + bounded first validation experiment (#158)
- **#559**: [sound] CH and CP: flatness (CH 9.3x) and decay (CP 15.9x) fail the gate
- **#598**: Production drum host: prevent repeated tom bends from feeding transient pitch back into nominal tuning
- **#656**: Cymbal DECAY late-T20 not qualified/monotone: E_CYL sits on the Q0.16 rate-register floor at DECAY 7.5 and 10 (needs wider rate range in RTL)
- **#706**: Route the hash-pinned Mini V3 ring readers through ring_estimator (needs rig)
- **#774**: bug: dominant_period picks a different octave at 44.1 kHz and 48 kHz on some clips
- **#775**: bug: RATE_TOL_REL is 8x larger than everything jit.cyclecorr measures
- **#780**: bug: repeat_period returns four times the true repeat period on a signal that repeats exactly
- **#784**: Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error
- **#787**: Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run"
- **#789**: docs/milestones.md milestone 3 and the README's T1 sentence contradict the tree
- **#790**: T1 item 8: build one characterization report from the committed synth_top route evidence and record the klt signoff result
- **#791**: compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone
- **#793**: bug: tools/sensitivity.py raises ZeroDivisionError on an objective that reaches zero
- **#803**: A probe's identical flag has #785's absent-vs-null hole, load-bearing: balance_input_invariance reports identical on a dropped key
- **#804**: Collapse the three exit statuses that mean REFUSED: run_all.py reports the cymbal low-tail refusal as FAIL(1), a RESULT
- **#823**: compile_dag.py --run writes docs/dag-results.json only after the whole loop, so one crashed verifier loses every node's records
- **#828**: Dedupe BS.1770 K-weighted loudness: three copies of kweight/loudness in tools/

## PRs Awaiting Review

PRs waiting on Judge (`loom:review-requested`).

- **#781**: fix: resolve dominant_period's octave pick with a swept margin
- **#810**: dag: report an evidence file that declares no verdict as REFUSED, not GREEN
- **#831**: fix: compare balance_input_invariance images with fpga/record_compare
- **#833**: DAG --run: guard the evidence loop and persist records per node
- **#837**: [tools] one BS.1770 K-weighting implementation: the listening packs import perceptual_gate

## Approved (Awaiting Merge)

PRs that passed review and are queued for Champion auto-merge (`loom:pr`).

- **#783**: fix: narrow RATE_TOL_REL to 0.02, and record why no margin test can exist
- **#796**: fix: normalise repeat_period per overlapped window and resolve its multiple
- **#798**: docs: repair milestone 3 and the README T1 sentence, and put both under check_doc_claims
- **#822**: fix: sensitivity gate refuses, never divides, across or below a zero objective
- **#832**: tools: characterize synth_top from the committed route evidence

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
- **#768**: measure: how many discrimination rows fall under the new jit.dphase_ar1 floor (REFUSED in #763 -- no corpus on that host) *(curated)*
- **#774**: bug: dominant_period picks a different octave at 44.1 kHz and 48 kHz on some clips *(curated)*
- **#775**: bug: RATE_TOL_REL is 8x larger than everything jit.cyclecorr measures *(curated)*
- **#780**: bug: repeat_period returns four times the true repeat period on a signal that repeats exactly *(curated)*
- **#784**: Give Refused a shared base class: 113 unrelated definitions make two refusals report as a crash and an execution error *(curated)*
- **#787**: Carry the nightly's slow DAG evidence to main so F1, M1, D1, I1 and I2 stop reading STALE or "never run" *(curated)*
- **#789**: docs/milestones.md milestone 3 and the README's T1 sentence contradict the tree *(curated)*
- **#790**: T1 item 8: build one characterization report from the committed synth_top route evidence and record the klt signoff result *(curated)*
- **#791**: compile_dag renders a node GREEN when its evidence file declares no verdict: M3, S1 and S3 are green on existence alone *(curated)*
- **#792**: Corner probes: date the pre-#150 columns, fix `corner_shipped`'s stale docstring, guard the refusal print *(curated)*
- **#793**: bug: tools/sensitivity.py raises ZeroDivisionError on an objective that reaches zero *(curated)*
- **#797**: spec/NUMERIC-CONTRACT.md item 29 says the chip still carries drum_section_placeholder and that nothing verifies the joined chip *(curated)*
- **#803**: A probe's identical flag has #785's absent-vs-null hole, load-bearing: balance_input_invariance reports identical on a dropped key *(curated)*
- **#805**: The BASELINE of a perturbation comparison is read unguarded while the comparison itself is guarded (2 probe sites) *(curated)*
- **#808**: Route ladder_dp to completion with the unpatched netlist on the build box: DRT-0305's cause is measured gone, the error is not re-observed *(curated)*
- **#823**: compile_dag.py --run writes docs/dag-results.json only after the whole loop, so one crashed verifier loses every node's records *(curated)*
- **#824**: stale_against() reads an unresolvable sha as 'nothing changed', so a record whose commit left the history renders GREEN instead of STALE *(curated)*
- **#829**: Run `make reference-integration` on the box: verify_109_claims' real-reference half has never been exercised against the real Fischer corpus *(curated)*
- **#835**: File upstream: ORFS 6_report.json emits finish__design__instance__area twice with two different meanings *(curated)*

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
| Important | 7 |
| Ready (`loom:issue`) | 10 |
| In Progress (`loom:building`) | 19 |
| PRs awaiting review | 5 |
| Approved PRs awaiting merge | 5 |
| Curated | 70 |
| Architect / Hermit proposals | 10 |
| Active epics | 2 |
<!-- guide:plan-body:end -->

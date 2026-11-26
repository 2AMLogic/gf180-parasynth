# Work Log Archive — 2026-09

Sections moved byte-for-byte from [WORK_LOG.md](../WORK_LOG.md) by `loom-daemon work-docs archive`.

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

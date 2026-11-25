# Work Log Archive — 2026-09

Sections moved byte-for-byte from [WORK_LOG.md](../WORK_LOG.md) by `loom-daemon work-docs archive`.

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

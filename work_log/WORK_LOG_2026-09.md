# Work Log Archive — 2026-09

Sections moved byte-for-byte from [WORK_LOG.md](../WORK_LOG.md) by `loom-daemon work-docs archive`.

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

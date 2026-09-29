# Demo image (#406): no-DAC output + plug-in PCM5102 on JA -- a build record, NOT a publication

`fpga/rtl/arty_a7_sd_top.v`, built by `fpga/build_arty_sd.py` on 2026-09-28
with Vivado 2025.1 (SW Build 6140274, the AMD Vivado ML 2025.1 AMI) in
242 s. State **BUILT_DEMO_TIMING_PASS**. `fpga/publish_arty.py` refuses this wrapper by
design (no `VERIFICATION_BY_WRAPPER` entry). Nothing here is a release image,
and no R0/R1 figure depends on it. See `fpga/ARTY.md`, "No-DAC demo output".

**JA is laid out for a PCM5102 breakout plugged straight in, NOT for the R0/R1
jumper wiring.** The placed pins below are read from the routed design's
`report_io` (`io.rpt`):

| Port | Pin | Pmod | Breakout pin |
|---|---|---|---|
| `dac_sck` (constant 0) | G13 | JA1 | SCK |
| `i2s_bclk` | B11 | JA2 | BCK |
| `i2s_sdata` | A11 | JA3 | DIN |
| `i2s_lrclk` | D12 | JA4 | LCK |
| — | GND / 3.3 V | JA5 / JA6 | GND / VIN |
| `sd_left` / `sd_right` | D4 / D3 | JD1 / JD2 | (RC filter) |

| Result | Value |
|---|---|
| WNS / WHS | +15.085 ns / +0.037 ns; 0 failing setup, hold or pulse endpoints |
| Output ports | unconstrained: none; user false path: `sd_left`, `sd_right`; forwarded clock: `i2s_bclk`; `dac_sck` is a constant (Synth 8-3917) and in no timing class |
| XDC queries | every object query bound (`constraint_matches.rpt`); the exceptions are exactly the XDC's (`exceptions.rpt`) |
| Critical warnings | 0 in `vivado.log` |
| DRC | 268 warnings (64 DPIP-1, 97 DPOP-1, 94 DPOP-2, 13 DPREG-4), the same census as R0; the DPREG-4 set is NOT re-dispositioned for this image |
| Slice LUTs / registers / DSPs | 14132 / 13574 / 100 |

Wrong, then right: the first direct-plug build carried a `set_false_path` on
the constant `dac_sck`. Vivado never applied it, and this gate refused the
build because `report_exceptions` did not list it. That bitstream
(`cf2efb9b…`) had the same pins and timing; it is superseded here and not
kept. The earlier no-DAC-only record (`0d22b944…`, JA in the jumper layout)
is in this directory's git history.

Digital proofs bound before Vivado ran: the core wrapper's
`reports/arty/rev14-clean/verification.json` and a `fpga/verify_sd_dac.py`
PASS (`report.json` → `verification`, `sd_verification`).

Bitstream SHA-256 `95a4f92ffa1135b08fa1591220fb384c9ac1229d53cdb298376540010bb10a22`.
Load it with `openFPGALoader -b arty_a7_100t fpga/reports/arty/sd-demo-2025.1/arty.bit`
(SRAM), or add `-f` (flash). The routed checkpoint stays on the build box.

The `.rpt` files here have their `| Host :` line removed, and every file has the box's home path
replaced by `<box>/`. `report.json`'s
`artifact_sha256` therefore names the ORIGINAL files:

| File | Original SHA-256 (in report.json) | Committed |
|---|---|---|
| `arty.bit` | `95a4f92ffa1135b0…` | `95a4f92ffa1135b0…` |
| `clocks.rpt` | `816bf22fd49d7d0d…` | `c3366e7436c53ac5…` |
| `constraint_matches.rpt` | `f54d170efa349074…` | `f54d170efa349074…` |
| `drc.rpt` | `c0b23ed9a310f3ed…` | `92742f226e14c83f…` |
| `exceptions.rpt` | `b4678b756ad3f351…` | `ec0f402b602223f8…` |
| `io.rpt` | `b86dea742c562a05…` | `dde7c702f807e9b7…` |
| `timing.rpt` | `7261ab2fcc331de0…` | `1ebf61c35830985d…` |
| `utilization.rpt` | `d0c147daa23dee67…` | `9638b5e8ae8bc5cf…` |

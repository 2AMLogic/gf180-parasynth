# No-DAC demo image (#406) -- a build record, NOT a publication

`fpga/rtl/arty_a7_sd_top.v`, built by `fpga/build_arty_sd.py` on 2026-09-27
with Vivado 2025.1 (SW Build 6140274, the AMD Vivado ML 2025.1 AMI) in
241 s. State **BUILT_DEMO_TIMING_PASS**. `fpga/publish_arty.py` refuses this wrapper by
design (no `VERIFICATION_BY_WRAPPER` entry); nothing here is a release image,
and no R0/R1 figure depends on it. See `fpga/ARTY.md`, "No-DAC demo output".

| Result | Value |
|---|---|
| WNS / WHS | +16.28 ns / +0.03 ns; 0 failing setup, hold or pulse endpoints |
| Output ports | unconstrained: none; user false path: `sd_left`, `sd_right`; forwarded clock: `i2s_bclk` |
| XDC queries | every object query bound (`constraint_matches.rpt`); exceptions exactly the XDC's (`exceptions.rpt`) |
| Critical warnings | 0 in `vivado.log` |
| DRC | 268 warnings (64 DPIP-1, 97 DPOP-1, 94 DPOP-2, 13 DPREG-4), the same census as R0; the DPREG-4 set is NOT re-dispositioned for this image |
| Slice LUTs / registers / DSPs | 14129 / 13574 / 100 |

Digital proofs bound before Vivado ran: the core wrapper's
`reports/arty/rev14-clean/verification.json` and a `fpga/verify_sd_dac.py`
PASS (`report.json` → `verification`, `sd_verification`).

Bitstream SHA-256 `0d22b944f58fe5a19d4dcef88acc9a4f67f897b52be186c1b94fae6d8268d0bd`.
Load it: `openFPGALoader -b arty_a7_100t fpga/reports/arty/sd-demo-2025.1/arty.bit`
(SRAM) or add `-f` (flash). The routed checkpoint stays on the build box.

The `.rpt` files here have their `| Host :` line removed, and `report.json` and
`build.tcl` have the box's home path replaced by `<box>/`; `report.json`'s
`artifact_sha256` therefore names the ORIGINAL files:

| File | Original SHA-256 (in report.json) | Committed |
|---|---|---|
| `arty.bit` | `0d22b944f58fe5a1…` | `0d22b944f58fe5a1…` |
| `clocks.rpt` | `a1b2124bb5b198b8…` | `5a34c6b114bad159…` |
| `constraint_matches.rpt` | `7105c979b5887587…` | `7105c979b5887587…` |
| `drc.rpt` | `14ca406edfbe694a…` | `bbe0e57141f24128…` |
| `exceptions.rpt` | `c42ec8b6d421ac20…` | `c6d1ad3761880ddf…` |
| `timing.rpt` | `e26748f763e248fe…` | `c7b966db24b18336…` |
| `utilization.rpt` | `07d820e2be2c19fb…` | `4c0ef276d85c5102…` |

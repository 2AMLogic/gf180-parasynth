# Deadline re-captures on the revision-14 tree (clap L2)

Kept out of `README.md` on purpose: that file is the #248 deadline report the
published R0 release (`baseline 2025.1, r1`; plan087 R0) binds by digest
(`fpga/release/baseline-2025.1.json`, `evidence.deadline.readme_sha256`). The
R0 image is revision-11 RTL and these
runs are not evidence about it, so they must not move its record.

`traces/prod-stress-saw-l2` and `traces/ctl-prod-stress-late15-l2` (records in
`runs/*-l2.json`, `runs/run_all-l2.json`: 2/2 exit 0, build box, commit
`0939a1f`) are the stress-saw clean run and its late:15 control on the
contract-revision-14 tree. The kit gained one write, so the stress-saw stimulus
is now 461 writes and 2158 required periods. The run passes with worst sample
slack 13; late:15 misses 32 frames and is caught. The pre-L2 captures in
`README.md` are unchanged and stay history. `tools/test_verify_deadline.py` now
refuses to judge a capture that was not driven by the current stimulus.

## R1 qualification (#279): the heavy workload and the late control on the frozen candidate

Build box, commit `ccf7ed4` (RTL byte-identical to the R1 freeze at `6864435`,
`fpga/release/r1-candidate.json`), as trial receipts (`T-DEADLINE --mode stress`
and `--mode sim`; bundled in `fpga/reports/r1-candidate/receipts.tgz`).

**Observed** (simulation of these workloads only):

| run | workload | frames | missed | worst sample / busy slack | drum slack | I2S compared | verdict |
|---|---|---:|---:|---|---:|---|---|
| stress-saw (SPI pins) | three audible 2x oscillators, glide + osc/filter modulation, noise, full rev-14 kit (461 writes), every stop struck, control writes, drum filter toggled | 2157 | 0 | **13 / 15** | 130 | 2158/2158, 0 differ | PASS |
| control `late:15` | the same, completion 15 cycles late | 2157 | 32 | 0 / 0 | 130 | 2158/2158, 41 differ | FAIL for the deadline reason: **caught** |
| arty-uart (UART pins) | three audible 2x saws, glide, wheel, waveform and control writes; drum page at reset | 3496 | 0 | **13 / 15** | 130 | 3300/3300, 0 differ | PASS |
| control `late:160` | the same, 160 cycles late | 3496 | 3496 | -- | 130 | 3300/3300, 989 differ | FAIL for the deadline reason: **caught** |

The player workloads' own worst strobe cycles through the UART wrapper are in
`fpga/release/R1.md` (demo, bar808-full, live MIDI), from the playback receipts.

**Analytical bound** (the cost model of `README.md` section 4, re-measured, not
a proof). On revision 14 the constant is **C = 88 in every frame** (2157 +
3496 frames, 0 overlap exceptions, `ywait` 5..11), one cycle more than the
revision-11 traces' 87 -- consistent with drift's unconditional `S_DA0` state
(#252), which moved the UART bench's worst strobe 175 -> 176. The drum section
still ends at cycle 125. With the per-term maxima of section 4 (R 57, O 60, W 6,
2A 6, I 3, K 0 for three 2x saws):

| configuration | bound | slack |
|---|---:|---:|
| R1 player domain (increments < 2^23, ROUTE = 0 so D = 1) | 221 | 33 |
| increments < 2^23 with ROUTE = 1 (D = 23; outside the player domain) | 243 | 11 |
| any register value (A up to 2 per oscillator, D = 23) | 249 | 5 |

The observed worst (strobe 241, slack 13) sits inside the 243 bound of the run's
own domain. Assumptions 1-5 of section 4 carry, with C = 88 measured here.

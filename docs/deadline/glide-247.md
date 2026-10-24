# #247 high-increment glide: model-side diagnosis

Split out of README.md because that file's sha256 is bound in the release manifest (evidence.deadline.readme_sha256).

## Model-side check only, RTL reproduction pending

Added 2026-10-07 on a host with no build box (no `.env`, no pinned toolchain,
RTL simulation not permitted on it). The historical `prod-extreme-saw.json`
(1809 wire mismatches, no deadline failure) is unchanged and is NOT
re-measured here.

Measured: `model/test_glide_boundary.py` (9 tests, pure Python) agrees with an
independent `divmod` restatement of contract 6.7 on all 169 pairs of
{0, 1, 0x7FFFFF, 0x800000, 0x800001, 0xC00000, 0xC80000, 0xD00000, 0xF00000,
0xF80000, 0xFF0000, 0xFFFFFE, 0xFFFFFF} at GLIDE in {0, 1, 2692, 2^20, 2^24-1},
with exact landing and no accumulator above 0xFFFFFF00. So the Python model's
slew is not the side that is wrong at these increments.

Not measured: anything about `voice_dp.v`, the I2S wire, or whether #247
still reproduces on current main. Static reading of `S_SL0..S_SL2`/`Pfull`/
`acc_up` found no width defect, which is a reading and not a result. The
start-red run, the first differing state and any repair remain to be done on
the build box (`verify_deadline.py --scenario extreme-saw` and
`extreme-saw-mod`, then a `verify_voice.py` state regression driven from the
pair table in the test file).

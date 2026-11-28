single-process reference: `xdist-n1-r2`, pytest's own 2975.4 s

| tests with a printed duration | summed call (s) | longest one test (s) | >= 60 s | >= 30 s | >= 10 s |
|---|---|---|---|---|---|
| 2591 | 2782 | 238 | 2 | 13 | 69 |

the longest: `tools/test_r0_capture.py::test_t_physical_on_a_synthetic_bundle_is_no_verdict_with_its_control_caught`

| workers | summed call / N (s) | floored by the longest test (s) | as minutes |
|---|---|---|---|
| 1 | 2782 | 2782 | 46.4 |
| 2 | 1391 | 1391 | 23.2 |
| 4 | 695 | 695 | 11.6 |
| 8 | 348 | 348 | 5.8 |

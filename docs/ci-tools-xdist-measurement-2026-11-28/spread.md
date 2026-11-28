| run | CPU | pytest's own (s) | CPU user+sys (s) | CPU / wall |
|---|---|---|---|---|
| -r1 | AMD EPYC 7763 64-Core Processor | 5101.1 | 6699 | 1.31 |
| -r2 | AMD EPYC 9V45 96-Core Processor | 2975.4 | 4110 | 1.38 |
| -r3 | AMD EPYC 9V74 80-Core Processor | 3876.4 | 5172 | 1.33 |

per-test ratio of call seconds, fast run -> slow run (tests of >= 1 s in the fast run):

- `-r2` -> `-r1`: 295 tests, p10 1.43, median 1.69, p90 1.94; summed call 2782 -> 4772 s (1.72)
- `-r3` -> `-r1`: 344 tests, p10 1.17, median 1.28, p90 1.48; summed call 3631 -> 4772 s (1.31)
- `-r2` -> `-r3`: 295 tests, p10 1.16, median 1.30, p90 1.44; summed call 2782 -> 3631 s (1.31)

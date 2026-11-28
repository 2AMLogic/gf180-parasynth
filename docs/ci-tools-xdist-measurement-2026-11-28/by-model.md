whole-job minutes, CPU model down and candidate across; a cell lists every run of that pair

| CPU | n1 | n2 | nauto | n8 | s0 | s1 |
|---|---|---|---|---|---|---|
| AMD EPYC 7763 64-Core Processor | 85.8 | 48.2, 48.8 | 45.3 | - | 44.9, 45.2 | 45.0 |
| AMD EPYC 9V74 80-Core Processor | 65.2 | - | 32.9 | 33.7 | - | 33.6 |
| AMD EPYC 9V45 96-Core Processor | 50.2 | - | 26.6 | 24.9 | - | - |
| Intel(R) Xeon(R) 6973P-C | - | 32.9 | - | - | - | - |
| Intel(R) Xeon(R) Platinum 8370C CPU @ 2.80GHz | - | - | - | 47.5 | - | 43.6 |
| INTEL(R) XEON(R) PLATINUM 8573C | - | - | - | - | 33.9 | - |

same-model speed-up against the single process (n1 wall / candidate wall):

- AMD EPYC 7763 64-Core Processor: n2 1.77x, nauto 1.89x, s0 1.90x, s1 1.91x
- AMD EPYC 9V45 96-Core Processor: nauto 1.89x, n8 2.01x
- AMD EPYC 9V74 80-Core Processor: nauto 1.98x, n8 1.93x, s1 1.94x

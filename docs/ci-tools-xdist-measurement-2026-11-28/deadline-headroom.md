call seconds per repeat; `timed out` is the test's own `subprocess.run(timeout=)` expiring

| test (single-process s) | deadline | n1 | n2 r1 / r2 / r3 | nauto r1 / r2 / r3 | n8 r1 / r2 / r3 | its shard, r1 / r2 / r3 |
|---|---|---|---|---|---|---|
| `test_audio_distance_floor_refusal.py::test_full_run_with_mt50_cut_into_the_strike_exits_refused_` (55 s) | 300 s | 109 / 55 / 72 | 146 / 86 / 147 | 131 / 238 / 159 | timed out / timed out / 263 | 110 / 72 / 107 |
| `test_audio_distance_floor_refusal.py::test_full_run_on_an_unmodified_corpus_exits_0` (54 s) | 300 s | 109 / 54 / 72 | 145 / 101 / 146 | 137 / 243 / 154 | timed out / timed out / 274 | 110 / 73 / 106 |
| `test_audio_distance_floor_refusal.py::test_full_run_with_bd5050_cut_into_the_strike_exits_refuse` (54 s) | 300 s | 108 / 54 / 71 | 143 / 87 / 135 | 138 / 249 / 163 | timed out / timed out / 233 | 110 / 71 / 107 |
| `test_m1a_refusal_boundaries.py::test_unqualified_reference_manifest_exits_refused_2[analys` (33 s) | 180 s | 50 / 33 / 40 | 51 / 33 / 51 | 63 / 112 / 84 | timed out / 132 / 102 | 51 / 40 / 46 |
| `test_m1a_refusal_boundaries.py::test_non_refused_exception_is_not_swallowed_by_cli_boundar` (33 s) | 180 s | 50 / 33 / 40 | 51 / 33 / 51 | 55 / 89 / 67 | timed out / timed out / 113 | 51 / 40 / 50 |

worst observed call time as a share of the deadline, by candidate:

- n1: 36%
- n2: 49%
- nauto: 83%
- n8: 100%
- shard: 37%

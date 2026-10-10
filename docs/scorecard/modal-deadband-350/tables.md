{
 "dev": {
  "renders": 15,
  "cells": 240,
  "by_status": {
   "MEASURED": 18,
   "NO-TAIL": 3,
   "NOT-DRIVEN": 174,
   "REFUSED": 45
  },
  "measured_with_bus": 18,
  "over_arith_floor": 12,
  "max_int16_residual_lsb": 36.0079,
  "max_state_residual_lsb": 23897.62,
  "tail_kinds": {
   "stuck": 18
  },
  "voices_tail_int16_peak_max": 37,
  "voices_over_floor": 11,
  "t20_measured": 13,
  "t20_refused": 2,
  "t20_max_abs_pct": 9.865,
  "t20_refusal_reasons": [
   "fixed: the record ends before the decay does: only 1211.8 ms",
   "fixed: the record ends before the decay does: only 1504.8 ms"
  ]
 },
 "confirm": {
  "renders": 33,
  "cells": 528,
  "by_status": {
   "MEASURED": 36,
   "NO-TAIL": 3,
   "NOT-DRIVEN": 390,
   "REFUSED": 99
  },
  "measured_with_bus": 36,
  "over_arith_floor": 12,
  "max_int16_residual_lsb": 36.0018,
  "max_state_residual_lsb": 23893.58,
  "tail_kinds": {
   "stuck": 35,
   "limit-cycle": 1
  },
  "voices_tail_int16_peak_max": 37,
  "voices_over_floor": 12,
  "t20_measured": 30,
  "t20_refused": 3,
  "t20_max_abs_pct": 3.905,
  "t20_refusal_reasons": [
   "fixed: the record ends before the decay does: only 305.2 ms ",
   "fixed: the record ends before the decay does: only 380.0 ms ",
   "fixed: the record ends before the decay does: only 553.9 ms "
  ]
 },
 "population": {
  "renders": 8,
  "cells": 128,
  "by_status": {
   "MEASURED": 3,
   "NO-TAIL": 5,
   "NOT-DRIVEN": 96,
   "REFUSED": 24
  },
  "measured_with_bus": 0,
  "over_arith_floor": 0,
  "max_int16_residual_lsb": null,
  "max_state_residual_lsb": null,
  "tail_kinds": {},
  "voices_tail_int16_peak_max": 1,
  "voices_over_floor": 0,
  "t20_measured": 7,
  "t20_refused": 1,
  "t20_max_abs_pct": 0.081,
  "t20_refusal_reasons": [
   "fixed: the record ends before the decay does: only 1680.4 ms"
  ]
 },
 "disposition": {
  "verdict": "REFUSED",
  "defect_above_arithmetic_floor_confirmed": true,
  "refusals": [
   "persistent DC / tail-floor limit: capture gate refuses DC (/tmp/tr808-ref/manifest.json absent: no manifest, no hashes, no provenance)",
   "T20 limit: no same-machine repeatability at the shipped settings of the voices that move (only BD, at DECAY position A, 1.30 %, docs/bd-repeatability-measurement.md)"
  ]
 },
 "state_budget_for_1_int16_lsb": {
  "BD:mode8": 671.1,
  "SD:mode9": 832.2,
  "SD:mode10": 250.7,
  "LT:mode11": 462.3,
  "MT:mode12": 358.7,
  "HT:mode13": 223.7,
  "LC:mode11": 237.6,
  "MC:mode12": 180.9,
  "HC:mode13": 107.5
 }
}

### DEV
| sound | renders | measured rows | rows > 1 int16 LSB | max int16 resid (LSB) | voices with tail > 1 LSB | T20 measured / refused | max abs T20 delta |
|---|---:|---:|---:|---:|---:|---|---:|
| BD | 3 | 3 | 3 | 36.0079 | 3 | 2 / 1 | 5.32 |
| HT | 3 | 3 | 1 | 7.6502 | 1 | 3 / 0 | 5.349 |
| LT | 3 | 3 | 3 | 15.7504 | 3 | 3 / 0 | 9.865 |
| MT | 3 | 3 | 2 | 9.0002 | 2 | 3 / 0 | 9.428 |
| SD | 3 | 6 | 3 | 2.7001 | 2 | 2 / 1 | 2.658 |

| sound | accent | mode | amp | tail | departs (ms) | float level there | state resid (LSB) | bus resid (LSB) | int16 resid (LSB / dBFS) | voice T20 delta |
|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|
| BD | 0.5 | 8 | 217 | stuck -23893 | 590.0 | 44649.5 | 23894.15 | 80.004 | 36.0027 / -59.18 | REFUSED |
| BD | 1.0 | 8 | 217 | stuck -23893 | 750.0 | 28978.4 | 23895.31 | 80.008 | 36.0044 / -59.18 | +5.32 % |
| BD | 2.0 | 8 | 217 | stuck -23893 | 850.0 | 28678.6 | 23897.62 | 80.015 | 36.0079 / -59.18 | +1.04 % |
| SD | 0.5 | 9 | 175 | stuck -1947 | 210.0 | 1178.4 | 1947.0 | 6.0 | 2.7001 / -81.68 | REFUSED |
| SD | 0.5 | 10 | 581 | stuck -515 | 80.0 | 258.8 | 515.0 | 5.0 | 2.2501 / -83.27 | REFUSED |
| SD | 1.0 | 9 | 175 | stuck -2 | 220.0 | 1605.0 | 2.0 | 1.0 | 0.45 / -97.24 | +2.66 % |
| SD | 1.0 | 10 | 581 | stuck -516 | 80.0 | 518.1 | 516.0 | 5.0 | 2.2501 / -83.27 | +2.66 % |
| SD | 2.0 | 9 | 175 | stuck -2 | 240.0 | 1760.7 | 2.0 | 1.0 | 0.45 / -97.24 | +0.08 % |
| SD | 2.0 | 10 | 581 | stuck -2 | 90.0 | 341.4 | 2.0 | 1.0 | 0.45 / -97.24 | +0.08 % |
| LT | 0.5 | 11 | 315 | stuck -7201 | 520.0 | 5509.9 | 7201.0 | 35.0 | 15.7504 / -66.36 | +9.87 % |
| LT | 1.0 | 11 | 315 | stuck -7158 | 580.0 | 5401.9 | 7158.0 | 35.0 | 15.7504 / -66.36 | +2.02 % |
| LT | 2.0 | 11 | 315 | stuck -7029 | 630.0 | 5347.3 | 7029.0 | 34.0 | 15.3004 / -66.61 | +0.58 % |
| MT | 0.5 | 12 | 406 | stuck -3201 | 360.0 | 2523.6 | 3201.0 | 20.0 | 9.0002 / -71.22 | +9.43 % |
| MT | 1.0 | 12 | 406 | stuck -2 | 390.0 | 2730.2 | 2.0 | 1.0 | 0.45 / -97.24 | +0.09 % |
| MT | 2.0 | 12 | 406 | stuck -3127 | 400.0 | 3967.6 | 3127.0 | 20.0 | 9.0002 / -71.22 | +0.84 % |
| HT | 0.5 | 13 | 651 | stuck -1704 | 290.0 | 1295.4 | 1704.0 | 17.0 | 7.6502 / -72.64 | +5.35 % |
| HT | 1.0 | 13 | 651 | stuck -1 | 310.0 | 1483.2 | 1.0 | 1.0 | 0.45 / -97.24 | +0.35 % |
| HT | 2.0 | 13 | 651 | stuck -1 | 330.0 | 1492.3 | 1.0 | 1.0 | 0.45 / -97.24 | -0.04 % |

### CONFIRM
| sound | renders | measured rows | rows > 1 int16 LSB | max int16 resid (LSB) | voices with tail > 1 LSB | T20 measured / refused | max abs T20 delta |
|---|---:|---:|---:|---:|---:|---|---:|
| BD | 3 | 3 | 1 | 36.0018 | 1 | 1 / 2 | 0.456 |
| HC | 6 | 6 | 1 | 3.6001 | 1 | 6 / 0 | 0.34 |
| HT | 3 | 3 | 1 | 7.6502 | 1 | 3 / 0 | 2.544 |
| LC | 6 | 6 | 3 | 7.2002 | 3 | 6 / 0 | 0.777 |
| LT | 3 | 3 | 1 | 15.7504 | 1 | 3 / 0 | 3.689 |
| MC | 6 | 6 | 2 | 4.5001 | 2 | 6 / 0 | 3.905 |
| MT | 3 | 3 | 1 | 9.0002 | 1 | 3 / 0 | 3.72 |
| SD | 3 | 6 | 2 | 2.7001 | 2 | 2 / 1 | 1.169 |

| sound | accent | mode | amp | tail | departs (ms) | float level there | state resid (LSB) | bus resid (LSB) | int16 resid (LSB / dBFS) | voice T20 delta |
|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|
| BD | 0.25 | 8 | 217 | stuck -23893 | 490.0 | 45045.8 | 23893.58 | 80.002 | 36.0018 / -59.18 | REFUSED |
| BD | 0.75 | 8 | 217 | stuck -3 | 670.0 | 38163.4 | 4.73 | 1.006 | 0.4526 / -97.19 | +0.46 % |
| BD | 1.5 | 8 | 217 | stuck 0 | 810.0 | 28500.5 | 3.72 | 0.012 | 0.0055 / -135.43 | REFUSED |
| SD | 0.25 | 9 | 175 | stuck -1948 | 180.0 | 1541.3 | 1948.0 | 6.0 | 2.7001 / -81.68 | REFUSED |
| SD | 0.25 | 10 | 581 | stuck -515 | 70.0 | 392.2 | 515.0 | 5.0 | 2.2501 / -83.27 | REFUSED |
| SD | 0.75 | 9 | 175 | stuck -2 | 200.0 | 2362.3 | 2.0 | 1.0 | 0.45 / -97.24 | +1.17 % |
| SD | 0.75 | 10 | 581 | stuck -2 | 80.0 | 388.4 | 2.0 | 1.0 | 0.45 / -97.24 | +1.17 % |
| SD | 1.5 | 9 | 175 | stuck -2 | 240.0 | 1320.2 | 2.0 | 1.0 | 0.45 / -97.24 | +0.11 % |
| SD | 1.5 | 10 | 581 | stuck -2 | 90.0 | 256.0 | 2.0 | 1.0 | 0.45 / -97.24 | +0.11 % |
| LT | 0.25 | 11 | 315 | stuck -3 | 420.0 | 8517.4 | 3.0 | 1.0 | 0.45 / -97.24 | +1.76 % |
| LT | 0.75 | 11 | 315 | stuck -7191 | 560.0 | 5290.9 | 7191.0 | 35.0 | 15.7504 / -66.36 | +3.69 % |
| LT | 1.5 | 11 | 315 | stuck -3 | 590.0 | 6679.2 | 3.0 | 1.0 | 0.45 / -97.24 | -0.03 % |
| MT | 0.25 | 12 | 406 | stuck -2 | 320.0 | 2422.8 | 2.0 | 1.0 | 0.45 / -97.24 | +0.76 % |
| MT | 0.75 | 12 | 406 | stuck -3196 | 380.0 | 2539.7 | 3196.0 | 20.0 | 9.0002 / -71.22 | +3.72 % |
| MT | 1.5 | 12 | 406 | stuck -2 | 420.0 | 2255.7 | 2.0 | 1.0 | 0.45 / -97.24 | +0.10 % |
| HT | 0.25 | 13 | 651 | stuck -1 | 250.0 | 1606.7 | 1.0 | 1.0 | 0.45 / -97.24 | +1.12 % |
| HT | 0.75 | 13 | 651 | stuck -1702 | 290.0 | 1869.5 | 1702.0 | 17.0 | 7.6502 / -72.64 | +2.54 % |
| HT | 1.5 | 13 | 651 | stuck -1 | 330.0 | 1341.0 | 1.0 | 1.0 | 0.45 / -97.24 | +0.03 % |
| LC | 0.25 | 11 | 613 | stuck 0 | 420.0 | 2092.6 | 0.0 | 0.0 | ~0 (below float resolution) | -0.05 % |
| LC | 0.5 | 11 | 613 | stuck 0 | 490.0 | 1687.8 | 0.0 | 0.0 | ~0 (below float resolution) | +0.39 % |
| LC | 0.75 | 11 | 613 | stuck 0 | 540.0 | 1341.4 | 0.0 | 0.0 | ~0 (below float resolution) | +0.46 % |
| LC | 1.0 | 11 | 613 | stuck -1705 | 570.0 | 1215.6 | 1705.0 | 16.0 | 7.2002 / -73.16 | +0.78 % |
| LC | 1.5 | 11 | 613 | stuck -1692 | 590.0 | 1293.3 | 1692.0 | 16.0 | 7.2002 / -73.16 | +0.34 % |
| LC | 2.0 | 11 | 613 | stuck -1677 | 580.0 | 1831.5 | 1677.0 | 16.0 | 7.2002 / -73.16 | +0.19 % |
| MC | 0.25 | 12 | 805 | stuck 0 | 240.0 | 735.5 | 0.0 | 0.0 | ~0 (below float resolution) | +0.43 % |
| MC | 0.5 | 12 | 805 | stuck -744 | 270.0 | 688.4 | 744.0 | 10.0 | 4.5001 / -77.24 | +3.90 % |
| MC | 0.75 | 12 | 805 | stuck 0 | 290.0 | 606.8 | 0.0 | 0.0 | ~0 (below float resolution) | -0.04 % |
| MC | 1.0 | 12 | 805 | stuck 0 | 280.0 | 1067.8 | 0.0 | 0.0 | ~0 (below float resolution) | +0.11 % |
| MC | 1.5 | 12 | 805 | stuck 0 | 300.0 | 853.8 | 0.0 | 0.0 | ~0 (below float resolution) | +0.13 % |
| MC | 2.0 | 12 | 805 | stuck -732 | 330.0 | 471.5 | 732.0 | 9.0 | 4.0501 / -78.16 | +0.10 % |
| HC | 0.25 | 13 | 1355 | stuck 0 | 220.0 | 433.5 | 0.0 | 0.0 | ~0 (below float resolution) | +0.34 % |
| HC | 0.5 | 13 | 1355 | stuck 0 | 260.0 | 270.5 | 0.0 | 0.0 | ~0 (below float resolution) | -0.08 % |
| HC | 0.75 | 13 | 1355 | stuck 0 | 280.0 | 226.6 | 0.0 | 0.0 | ~0 (below float resolution) | -0.16 % |
| HC | 1.0 | 13 | 1355 | stuck 0 | 280.0 | 302.2 | 0.0 | 0.0 | ~0 (below float resolution) | +0.23 % |
| HC | 1.5 | 13 | 1355 | cycle p=370 | 300.0 | 226.3 | 364.0 | 8.0 | 3.6001 / -79.18 | +0.09 % |
| HC | 2.0 | 13 | 1355 | stuck 0 | 290.0 | 368.7 | 0.0 | 0.0 | ~0 (below float resolution) | +0.08 % |

### POPULATION
| sound | renders | measured rows | rows > 1 int16 LSB | max int16 resid (LSB) | voices with tail > 1 LSB | T20 measured / refused | max abs T20 delta |
|---|---:|---:|---:|---:|---:|---|---:|
| CB | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.081 |
| CH | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.012 |
| CL | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.0 |
| CP | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.0 |
| CY | 1 | 0 | 0 | - | 0 | 0 / 1 | - |
| MA | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.0 |
| OH | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.05 |
| RS | 1 | 0 | 0 | - | 0 | 1 / 0 | 0.0 |

| sound | accent | mode | amp | tail | departs (ms) | float level there | state resid (LSB) | bus resid (LSB) | int16 resid (LSB / dBFS) | voice T20 delta |
|---|---:|---:|---:|---|---:|---:|---:|---:|---|---|
| RS | 1.0 | 14 | 0 | stuck -280 | 40.0 | 33.3 | 280.0 | REFUSED tap-only | REFUSED tap-only | +0.00 % |
| RS | 1.0 | 15 | 0 | cycle p=29 | 30.0 | 7.3 | 37.0 | REFUSED tap-only | REFUSED tap-only | +0.00 % |
| CL | 1.0 | 15 | 0 | cycle p=58 | 240.0 | 19.1 | 39.0 | REFUSED tap-only | REFUSED tap-only | +0.00 % |
